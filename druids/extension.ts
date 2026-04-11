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

type SseEvent = {
  event: string;
  data: any;
};

function requireEnv(name: string, value: string | undefined): string {
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

const baseUrl = requireEnv("DRUIDS_SERVER_URL", serverUrl);
const currentExecutionId = requireEnv("DRUIDS_EXECUTION_ID", executionId);
const currentAgentId = requireEnv("DRUIDS_AGENT_ID", agentId);

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

async function postJson(path: string, body: unknown): Promise<any> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  let data: any = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    throw new Error(data?.error || `HTTP ${response.status}`);
  }

  return data;
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
      const response = await postJson(`/agents/${currentAgentId}/tool_call`, {
        tool: tool.name,
        params,
      });
      return {
        content: [{ type: "text", text: formatToolResult(response.result) }],
        details: { remoteResult: response.result, toolCallId },
      };
    },
  };

  pi.registerTool(definition);
  registeredTools.add(tool.name);
}

async function deliverMessage(pi: ExtensionAPI, ctx: ExtensionContext, text: string) {
  if (!text) return;
  if (ctx.isIdle()) {
    pi.sendUserMessage(text);
  } else {
    pi.sendUserMessage(text, { deliverAs: "steer" });
  }
}

async function registerWithServer(pi: ExtensionAPI, registeredTools: Set<string>) {
  const response = await postJson("/agents/register", {
    agent_id: currentAgentId,
    execution_id: currentExecutionId,
  });

  for (const tool of response.tools || []) {
    registerRemoteTool(pi, tool, registeredTools);
  }
}

async function* parseSseStream(response: Response): AsyncGenerator<SseEvent> {
  if (!response.body) {
    throw new Error("SSE stream missing response body");
  }

  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  let buffer = "";

  while (true) {
    const chunk = await reader.read();
    if (chunk.done) return;
    buffer += decoder.decode(chunk.value, { stream: true });

    while (true) {
      const boundary = buffer.indexOf("\n\n");
      if (boundary === -1) break;

      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      let event = "message";
      const dataLines: string[] = [];

      for (const line of rawEvent.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }

      const payload = dataLines.join("\n");
      yield { event, data: payload ? JSON.parse(payload) : {} };
    }
  }
}

async function runEventLoop(pi: ExtensionAPI, ctx: ExtensionContext, registeredTools: Set<string>) {
  while (true) {
    try {
      await registerWithServer(pi, registeredTools);

      const response = await fetch(`${baseUrl}/agents/${currentAgentId}/events`, {
        headers: { Accept: "text/event-stream" },
      });
      if (!response.ok) {
        throw new Error(`SSE connection failed with HTTP ${response.status}`);
      }

      for await (const event of parseSseStream(response)) {
        if (event.event === "message") {
          await deliverMessage(pi, ctx, String(event.data?.text || ""));
        } else if (event.event === "new_tool") {
          registerRemoteTool(pi, event.data, registeredTools);
        } else if (event.event === "shutdown") {
          ctx.shutdown();
          return;
        }
      }
    } catch (error) {
      if (ctx.hasUI) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.ui.notify(`druids extension disconnected: ${message}`, "warning");
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

export default function druidsExtension(pi: ExtensionAPI) {
  const registeredTools = new Set<string>();
  let started = false;

  pi.on("session_start", async (_event, ctx) => {
    if (started) return;
    started = true;
    void runEventLoop(pi, ctx, registeredTools);
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
