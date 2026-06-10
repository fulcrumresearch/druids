import { StringEnum, Type } from "@mariozechner/pi-ai";
import type { ExtensionAPI, ExtensionContext, ToolDefinition } from "@mariozechner/pi-coding-agent";

const serverUrl = process.env.RAMURE_SERVER_URL;
const executionId = process.env.RAMURE_EXECUTION_ID;
const agentId = process.env.RAMURE_AGENT_ID;
const appendedSystemPrompt = process.env.RAMURE_SYSTEM_PROMPT || "";
// Cap on pi_tool_result text per event. A full ``cargo build``
// can be tens of KB; truncate to keep per-agent jsonl files
// bounded. Callers can raise or lower via the env var; set it to
// 0 to forward without truncation.
const toolResultMaxBytes = Math.max(
  0,
  parseInt(process.env.RAMURE_TOOL_RESULT_MAX_BYTES || "16384", 10),
);
// Cap on visible message text per event. This logs assistant/user text
// emitted by pi, but deliberately does not include hidden thinking blocks.
// Set to 0 to forward without truncation.
const messageMaxBytes = Math.max(
  0,
  parseInt(process.env.RAMURE_MESSAGE_MAX_BYTES || "16384", 10),
);
type RemoteTool = {
  name: string;
  description?: string;
  parameters?: Record<string, unknown>;
};

type LogEntry = {
  seq: number;
  ts: number;
  type: string;
  origin: string;
  data: any;
};

function requireEnv(name: string, value: string | undefined): string {
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

const baseUrl = requireEnv("RAMURE_SERVER_URL", serverUrl);
const currentExecutionId = requireEnv("RAMURE_EXECUTION_ID", executionId);
const currentAgentId = requireEnv("RAMURE_AGENT_ID", agentId);

// Derive ws:// URL from http:// URL
const wsUrl = baseUrl.replace(/^http/, "ws");

function humanizeLabel(name: string): string {
  return name
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ") || "Tool";
}

function formatToolResult(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function schemaToTypeBox(schema: any): any {
  if (!schema || typeof schema !== "object") {
    return Type.String();
  }

  const options: Record<string, unknown> = {};
  if (typeof schema.description === "string") options.description = schema.description;
  if (schema.default !== undefined) options.default = schema.default;

  if (Array.isArray(schema.enum) && schema.enum.every((value: unknown) => typeof value === "string")) {
    return StringEnum(schema.enum as readonly string[], options);
  }

  switch (schema.type) {
    case "object": {
      const properties = schema.properties && typeof schema.properties === "object" ? schema.properties : {};
      const required = new Set(Array.isArray(schema.required) ? schema.required : []);
      const entries: Record<string, any> = {};

      for (const [key, value] of Object.entries(properties)) {
        const propertySchema = schemaToTypeBox(value);
        entries[key] = required.has(key) ? propertySchema : Type.Optional(propertySchema);
      }

      if (
        schema.additionalProperties &&
        typeof schema.additionalProperties === "object" &&
        Object.keys(properties).length === 0
      ) {
        return Type.Record(Type.String(), schemaToTypeBox(schema.additionalProperties), options);
      }

      if (schema.additionalProperties === false) {
        options.additionalProperties = false;
      }

      return Type.Object(entries, options);
    }
    case "array":
      return Type.Array(schemaToTypeBox(schema.items || { type: "string" }), options);
    case "integer":
      return Type.Integer(options);
    case "number":
      return Type.Number(options);
    case "boolean":
      return Type.Boolean(options);
    case "string":
    default:
      return Type.String(options);
  }
}

// -- WebSocket state --
//
// Replication model (see spec-replication.md):
// - `entries` is the local replica of the server's log for this agent.
// - `lastSeq` is the seq of the last entry we've observed.
// - The server is the sole writer. On connect we sync from `lastSeq`; on
//   every incoming entry we append and react. Outgoing tool calls wait
//   for a matching `tool_result` LogEntry; we do NOT reject on
//   disconnect because results may still arrive after reconnect+sync.

let ws: WebSocket | null = null;
let shuttingDown = false;
const entries: LogEntry[] = [];
let lastSeq = 0;
let callIdCounter = 0;

// Pending tool calls waiting for results
const pendingCalls = new Map<string, {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}>();

function nextCallId(): string {
  return `tc-${++callIdCounter}`;
}

function sendWs(msg: object): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

function sendEvent(eventType: string, data: object): void {
  sendWs({ type: "event", event_type: eventType, data });
}

function sendSync(): void {
  sendWs({ type: "sync", after: lastSeq });
}

// -- Tool registration --

function registerRemoteTool(pi: ExtensionAPI, tool: RemoteTool, registeredTools: Set<string>) {
  if (!tool?.name || registeredTools.has(tool.name)) {
    return;
  }

  const definition: ToolDefinition<any, { remoteResult: unknown }> = {
    name: tool.name,
    label: humanizeLabel(tool.name),
    description: tool.description || "",
    parameters: schemaToTypeBox(tool.parameters || { type: "object", properties: {}, required: [] }),
    async execute(toolCallId, params, _signal, _onUpdate, _ctx) {
      const callId = nextCallId();
      const result = await new Promise<unknown>((resolve, reject) => {
        pendingCalls.set(callId, { resolve, reject });
        sendEvent("tool_call", { call_id: callId, tool: tool.name, params });
      });
      return {
        content: [{ type: "text", text: formatToolResult(result) }],
        details: { remoteResult: result, toolCallId },
      };
    },
  };

  pi.registerTool(definition);
  registeredTools.add(tool.name);
}

// -- Log entry handling --

// Replication contract (see spec-replication.md):
// - Server assigns ``seq`` strictly monotonically.
// - Client applies entries in strict seq order (``lastSeq + 1`` only).
// - Gaps trigger a resync. Duplicates are dropped.
// This makes the client self-heal from any out-of-order or lost delivery
// on the server side.

function handleLogEntry(
  entry: LogEntry,
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  registeredTools: Set<string>,
): boolean {
  if (entry.seq <= lastSeq) {
    // Already applied. Server shouldn't produce these, but ignore defensively.
    return false;
  }

  if (entry.seq > lastSeq + 1) {
    // Gap detected: some entry in (lastSeq, entry.seq) is missing. Request
    // a resync and drop this entry; it will be re-sent in order by the
    // server's ``log.after(lastSeq)`` reply.
    sendSync();
    return false;
  }

  entries.push(entry);
  lastSeq = entry.seq;

  switch (entry.type) {
    case "registered": {
      const tools = entry.data?.tools || [];
      for (const tool of tools) {
        registerRemoteTool(pi, tool, registeredTools);
      }
      break;
    }

    case "tool_result": {
      const callId = entry.data?.call_id;
      const pending = pendingCalls.get(callId);
      if (pending) {
        pendingCalls.delete(callId);
        if (entry.data?.error) {
          pending.reject(new Error(entry.data.error));
        } else {
          pending.resolve(entry.data?.result);
        }
      }
      break;
    }

    case "tool_registered": {
      registerRemoteTool(pi, entry.data, registeredTools);
      break;
    }

    case "message": {
      const text = String(entry.data?.text || "");
      if (text) {
        if (ctx.isIdle()) {
          pi.sendUserMessage(text);
        } else {
          pi.sendUserMessage(text, { deliverAs: "steer" });
        }
      }
      break;
    }

    case "shutdown": {
      shuttingDown = true;
      // Reject any pending calls: we won't be reconnecting.
      for (const [callId, pending] of pendingCalls) {
        pending.reject(new Error("ramure: agent shutting down"));
        pendingCalls.delete(callId);
      }
      ctx.shutdown();
      return true; // signal to stop
    }

    case "error": {
      if (ctx.hasUI) {
        ctx.ui.notify(`ramure error: ${entry.data?.error || "unknown"}`, "error");
      }
      break;
    }
  }

  return false;
}

// -- WebSocket connection --

function connectWs(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  registeredTools: Set<string>,
): void {
  const url = `${wsUrl}/agents/${currentAgentId}/ws`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    // Sync first, then register
    sendSync();
    sendEvent("register", { execution_id: currentExecutionId });
  };

  ws.onmessage = (event: MessageEvent) => {
    try {
      const entry: LogEntry = JSON.parse(String(event.data));
      const shouldStop = handleLogEntry(entry, pi, ctx, registeredTools);
      if (shouldStop) {
        ws?.close();
        ws = null;
      }
    } catch (error) {
      // Ignore parse errors
    }
  };

  ws.onclose = () => {
    ws = null;
    if (shuttingDown) {
      return;
    }
    // Do NOT reject pendingCalls. In-flight results may already be in
    // the server log; we'll see them after reconnect + sync.
    setTimeout(() => {
      if (!shuttingDown) connectWs(pi, ctx, registeredTools);
    }, 1000);
  };

  ws.onerror = () => {
    // onclose will fire after this
    if (ctx.hasUI) {
      ctx.ui.notify("ramure: WebSocket error, reconnecting...", "warning");
    }
  };
}

// -- Pi activity event forwarding --

function setupActivityForwarding(pi: ExtensionAPI): void {
  pi.on("turn_start", async (event) => {
    sendEvent("turn_start", { turn_index: event.turnIndex });
  });

  pi.on("turn_end", async (event) => {
    sendEvent("turn_end", { turn_index: event.turnIndex });
  });

  pi.on("message_start", async (event) => {
    sendEvent("message_start", { role: event.message?.role });
  });

  pi.on("message_end", async (event) => {
    sendEvent("message_end", {
      role: event.message?.role,
      ...extractMessageLogFields(event.message),
    });
    const usage = extractUsageEventFields(event.message);
    if (usage) {
      sendEvent("usage", usage);
    }
  });

  pi.on("tool_execution_start", async (event) => {
    sendEvent("pi_tool_start", {
      tool_call_id: event.toolCallId,
      tool: event.toolName,
      args: event.args,
    });
  });

  pi.on("tool_execution_end", async (event) => {
    sendEvent("pi_tool_end", {
      tool_call_id: event.toolCallId,
      tool: event.toolName,
      is_error: event.isError || false,
    });
    sendEvent("pi_tool_result", {
      tool_call_id: event.toolCallId,
      tool: event.toolName,
      is_error: event.isError || false,
      ...extractToolResultFields(event.result),
    });
  });
}

function extractUsageEventFields(message: any): Record<string, unknown> | null {
  if (!message || message.role !== "assistant" || !message.usage) {
    return null;
  }

  const usage = message.usage || {};
  const cost = usage.cost || {};
  return {
    api: message.api,
    provider: message.provider,
    model: message.model,
    response_model: message.responseModel,
    stop_reason: message.stopReason,
    input_tokens: numberOrNull(usage.input),
    output_tokens: numberOrNull(usage.output),
    cache_read_tokens: numberOrNull(usage.cacheRead),
    cache_write_tokens: numberOrNull(usage.cacheWrite),
    total_tokens: numberOrNull(usage.totalTokens),
    cost: {
      input: numberOrNull(cost.input),
      output: numberOrNull(cost.output),
      cache_read: numberOrNull(cost.cacheRead),
      cache_write: numberOrNull(cost.cacheWrite),
      total: numberOrNull(cost.total),
    },
  };
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function extractMessageLogFields(message: any): Record<string, unknown> {
  if (!message) {
    return { text: "", truncated: false };
  }

  const textParts: string[] = [];
  const toolCalls: Array<Record<string, unknown>> = [];
  let thinkingBlocks = 0;
  let thinkingChars = 0;
  let imageBlocks = 0;
  let otherBlocks = 0;

  const content = message.content;
  if (typeof content === "string") {
    textParts.push(content);
  } else if (Array.isArray(content)) {
    for (const item of content) {
      if (!item || typeof item !== "object") {
        otherBlocks += 1;
        continue;
      }
      const it = item as Record<string, unknown>;
      if (it.type === "text" && typeof it.text === "string") {
        textParts.push(it.text);
      } else if (it.type === "toolCall") {
        toolCalls.push({
          id: typeof it.id === "string" ? it.id : undefined,
          name: typeof it.name === "string" ? it.name : undefined,
          arguments: it.arguments,
        });
      } else if (it.type === "thinking") {
        thinkingBlocks += 1;
        const thinking = typeof it.thinking === "string" ? it.thinking : "";
        thinkingChars += thinking.length;
      } else if (it.type === "image") {
        imageBlocks += 1;
      } else {
        otherBlocks += 1;
      }
    }
  }

  const out: Record<string, unknown> = {
    ...applyMessageTruncation(textParts.join("")),
    content_blocks: Array.isArray(content) ? content.length : (content ? 1 : 0),
  };
  if (toolCalls.length > 0) out.tool_calls = toolCalls;
  if (thinkingBlocks > 0) {
    out.thinking_blocks_redacted = thinkingBlocks;
    out.thinking_chars_redacted = thinkingChars;
  }
  if (imageBlocks > 0) out.image_blocks = imageBlocks;
  if (otherBlocks > 0) out.other_blocks = otherBlocks;
  return out;
}

function applyMessageTruncation(text: string): Record<string, unknown> {
  if (messageMaxBytes === 0 || text.length <= messageMaxBytes) {
    return { text, truncated: false };
  }
  return {
    text: text.slice(0, messageMaxBytes) + "... [truncated]",
    truncated: true,
    original_length: text.length,
  };
}

// Pull the human-readable parts out of pi's ToolExecutionEndEvent
// ``result`` field for forwarding. Pi's own shape is rich:
//   { content: [{ type: "text", text: "..." }, ...], isError: bool, details?: {...} }
// We flatten it to a single ``text`` string (concatenating every
// TextContent entry), truncate at ``toolResultMaxBytes``, and
// mark truncation explicitly so consumers can tell. Non-text
// content (images) is summarized but not forwarded -- log
// consumers almost never want raw image bytes, and they'd blow
// past the byte cap.
function extractToolResultFields(result: unknown): Record<string, unknown> {
  if (result === null || result === undefined) {
    return { text: "", truncated: false };
  }
  if (typeof result !== "object") {
    const s = String(result);
    return applyTruncation(s);
  }
  const r = result as Record<string, unknown>;
  const content = Array.isArray(r.content) ? r.content : [];
  const parts: string[] = [];
  let nonTextCount = 0;
  for (const item of content) {
    if (item && typeof item === "object") {
      const it = item as Record<string, unknown>;
      if (it.type === "text" && typeof it.text === "string") {
        parts.push(it.text);
      } else if (it.type) {
        nonTextCount += 1;
      }
    }
  }
  const text = parts.join("");
  const out = applyTruncation(text);
  if (nonTextCount > 0) {
    out.non_text_content = nonTextCount;
  }
  return out;
}

function applyTruncation(text: string): Record<string, unknown> {
  if (toolResultMaxBytes === 0 || text.length <= toolResultMaxBytes) {
    return { text, truncated: false };
  }
  // Byte-ish cap by code units; good enough, and avoids measuring
  // UTF-8 length on every tool call.
  return {
    text: text.slice(0, toolResultMaxBytes) + "... [truncated]",
    truncated: true,
    original_length: text.length,
  };
}

// -- Extension entry point --

export default function ramureExtension(pi: ExtensionAPI) {
  const registeredTools = new Set<string>();
  let started = false;

  setupActivityForwarding(pi);

  pi.on("session_start", async (_event, ctx) => {
    if (started) return;
    started = true;
    connectWs(pi, ctx, registeredTools);
  });

  pi.on("before_agent_start", async (event) => {
    if (!appendedSystemPrompt) {
      return undefined;
    }

    return {
      systemPrompt: event.systemPrompt
        ? `${event.systemPrompt}\n\n${appendedSystemPrompt}`
        : appendedSystemPrompt,
    };
  });
}
