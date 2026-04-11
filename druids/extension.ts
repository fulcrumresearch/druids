/*
 * Druids pi extension.
 *
 * This file is intentionally self-contained so the Python runtime can deploy it
 * onto a machine with a single write. It mirrors the protocol described in
 * spec.md:
 *   - open an SSE stream
 *   - register with POST /agents/register
 *   - register tools dynamically with pi.registerTool()
 *   - forward tool calls to POST /agents/{agent_id}/tool_call
 *   - deliver pushed messages with pi.sendUserMessage()
 *   - append DRUIDS_SYSTEM_PROMPT during before_agent_start
 *
 * The exact runtime API surface of pi may vary by version, so this extension is
 * written defensively and prefers feature detection.
 */

const serverUrl = process.env.DRUIDS_SERVER_URL;
const executionId = process.env.DRUIDS_EXECUTION_ID;
const agentId = process.env.DRUIDS_AGENT_ID;
const systemPrompt = process.env.DRUIDS_SYSTEM_PROMPT || "";

function assertEnv(name: string, value: string | undefined): string {
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

const baseUrl = assertEnv("DRUIDS_SERVER_URL", serverUrl);
const currentExecutionId = assertEnv("DRUIDS_EXECUTION_ID", executionId);
const currentAgentId = assertEnv("DRUIDS_AGENT_ID", agentId);

async function postJson(path: string, body: unknown): Promise<any> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.error || `HTTP ${response.status}`);
  }
  return data;
}

function registerTool(pi: any, tool: any) {
  if (!pi?.registerTool) return;
  pi.registerTool({
    name: tool.name,
    description: tool.description,
    parameters: tool.parameters,
    async execute(params: Record<string, unknown>) {
      const result = await postJson(`/agents/${currentAgentId}/tool_call`, {
        tool: tool.name,
        params,
      });
      return result.result;
    },
  });
}

async function startSse(pi: any) {
  const response = await fetch(`${baseUrl}/agents/${currentAgentId}/events`, {
    headers: { Accept: "text/event-stream" },
  });
  if (!response.body) throw new Error("SSE stream missing body");

  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  let buffer = "";
  let currentEvent = "message";
  let currentData = "{}";

  while (true) {
    const chunk = await reader.read();
    if (chunk.done) return;
    buffer += decoder.decode(chunk.value, { stream: true });

    while (buffer.includes("\n\n")) {
      const index = buffer.indexOf("\n\n");
      const rawEvent = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);

      currentEvent = "message";
      currentData = "{}";

      for (const line of rawEvent.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("event:")) currentEvent = line.slice(6).trim();
        if (line.startsWith("data:")) currentData = line.slice(5).trim();
      }

      const data = JSON.parse(currentData || "{}");
      if (currentEvent === "message") {
        await pi?.sendUserMessage?.(data.text || "");
      } else if (currentEvent === "new_tool") {
        registerTool(pi, data);
      } else if (currentEvent === "shutdown") {
        await pi?.shutdown?.();
        return;
      }
    }
  }
}

export default function druidsExtension(pi: any) {
  pi?.on?.("session_start", async () => {
    const sseTask = startSse(pi);
    const registration = await postJson("/agents/register", {
      agent_id: currentAgentId,
      execution_id: currentExecutionId,
    });
    for (const tool of registration.tools || []) registerTool(pi, tool);
    await sseTask;
  });

  pi?.on?.("before_agent_start", async (event: any) => {
    if (!systemPrompt) return event;
    const existing = event?.systemPrompt || "";
    return {
      ...event,
      systemPrompt: existing ? `${existing}\n\n${systemPrompt}` : systemPrompt,
    };
  });
}
