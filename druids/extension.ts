import { StringEnum, Type } from "@mariozechner/pi-ai";
import type { ExtensionAPI, ExtensionContext, ToolDefinition } from "@mariozechner/pi-coding-agent";

const serverUrl = process.env.DRUIDS_SERVER_URL;
const executionId = process.env.DRUIDS_EXECUTION_ID;
const agentId = process.env.DRUIDS_AGENT_ID;
const appendedSystemPrompt = process.env.DRUIDS_SYSTEM_PROMPT || "";

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

const baseUrl = requireEnv("DRUIDS_SERVER_URL", serverUrl);
const currentExecutionId = requireEnv("DRUIDS_EXECUTION_ID", executionId);
const currentAgentId = requireEnv("DRUIDS_AGENT_ID", agentId);

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

let ws: WebSocket | null = null;
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

function handleLogEntry(
  entry: LogEntry,
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  registeredTools: Set<string>,
): boolean {
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
      ctx.shutdown();
      return true; // signal to stop
    }

    case "error": {
      if (ctx.hasUI) {
        ctx.ui.notify(`druids error: ${entry.data?.error || "unknown"}`, "error");
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
    // Reject any pending tool calls
    for (const [callId, pending] of pendingCalls) {
      pending.reject(new Error("WebSocket disconnected"));
      pendingCalls.delete(callId);
    }
    // Reconnect after delay
    setTimeout(() => {
      connectWs(pi, ctx, registeredTools);
    }, 1000);
  };

  ws.onerror = () => {
    // onclose will fire after this
    if (ctx.hasUI) {
      ctx.ui.notify("druids: WebSocket error, reconnecting...", "warning");
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
    sendEvent("message_end", { role: event.message?.role });
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
  });
}

// -- Extension entry point --

export default function druidsExtension(pi: ExtensionAPI) {
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
