"""Gemini program for orpheus exec.

Creates a Google Gemini agent that implements a specification.

Like codex, gemini-cli doesn't reliably pick up MCP servers from ACP
session/new. Instead we write ~/.gemini/settings.json before starting
the agent, configuring the orpheus-mcp server via env vars injected
by Execution.run_program.
"""

from orpheus.config import settings
from orpheus.lib import ACPConfig, Agent

# Shell wrapper that runs before gemini --experimental-acp:
# 1. Install gemini-cli if missing
# 2. Write ~/.gemini/settings.json with MCP server from ORPHEUS_MCP_URL env
# 3. Start gemini in ACP + yolo mode
GEMINI_WRAPPER = r"""
export PATH="/opt/node/bin:$PATH"
which gemini >/dev/null 2>&1 || npm install -g @google/gemini-cli >/dev/null 2>&1
ln -sf /opt/node/bin/gemini /usr/local/bin/gemini 2>/dev/null || true
mkdir -p ~/.gemini
if [ -n "$ORPHEUS_MCP_URL" ]; then
  if [ -n "$ORPHEUS_MCP_TOKEN" ]; then
    HEADERS='"headers": {"Authorization": "Bearer '"$ORPHEUS_MCP_TOKEN"'"}, '
  else
    HEADERS=""
  fi
  MODEL_BLOCK=""
  if [ -n "$GEMINI_MODEL" ]; then
    MODEL_BLOCK='"model": {"name": "'"$GEMINI_MODEL"'"},'
  fi
  cat > ~/.gemini/settings.json << SETTINGS_EOF
{
  ${MODEL_BLOCK}
  "mcpServers": {
    "orpheus-mcp": {
      "url": "$ORPHEUS_MCP_URL",
      "type": "http",
      ${HEADERS}"trust": true
    }
  }
}
SETTINGS_EOF
fi
# Diagnostic: test MCP connectivity from this container
if [ -n "$ORPHEUS_MCP_URL" ]; then
  MCP_SDK_PATH=$(node -e "try{console.log(require.resolve('@modelcontextprotocol/sdk/client/streamableHttp.js',{paths:['/opt/node/lib/node_modules/@google/gemini-cli']}))}catch(e){}" 2>/dev/null)
  if [ -n "$MCP_SDK_PATH" ]; then
    SDK_DIR=$(dirname $(dirname $(dirname "$MCP_SDK_PATH")))
    NODE_PATH="$SDK_DIR" node -e "
const {StreamableHTTPClientTransport}=require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const {Client}=require('@modelcontextprotocol/sdk/client/index.js');
(async()=>{
  try{
    const t=new StreamableHTTPClientTransport(new URL(process.env.ORPHEUS_MCP_URL),
      {requestInit:{headers:{Authorization:'Bearer '+process.env.ORPHEUS_MCP_TOKEN}}});
    const c=new Client({name:'diag',version:'1.0.0'});
    await c.connect(t);
    const tools=await c.listTools();
    console.error('MCP_DIAG_OK: '+tools.tools.map(t=>t.name).join(','));
    await c.close();
  }catch(e){console.error('MCP_DIAG_FAIL: '+e.message+'\n'+e.stack);}
})();
" 2>>/tmp/gemini-debug.log
  else
    echo "MCP_DIAG: SDK not found" >> /tmp/gemini-debug.log
  fi
fi
# Log settings.json for debugging
cat ~/.gemini/settings.json >> /tmp/gemini-debug.log 2>/dev/null
exec gemini --experimental-acp --yolo 2>>/tmp/gemini-debug.log
"""


def create_task_program(
    spec: str,
    snapshot_id: str | None = None,
    repo_name: str | None = None,
    working_dir: str | None = None,
    container_name: str | None = None,
    model: str | None = None,
) -> Agent:
    """Create a Gemini agent that implements the given spec."""

    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    prompt = f"""You are a software engineering agent. Implement the following specification:

---
{spec}
---

Work through the task step by step."""

    if not working_dir:
        working_dir = f"/home/agent/{repo_name}" if repo_name and snapshot_id else "/home/agent"

    return Agent(
        name="gemini",
        config=ACPConfig(
            command="/bin/sh",
            command_args=["-c", GEMINI_WRAPPER],
            env={
                "GEMINI_API_KEY": settings.gemini_api_key.get_secret_value(),
                "GEMINI_MODEL": model or "gemini-3-pro-preview",
            },
            working_directory=working_dir,
            container_name=container_name,
            auth_method="gemini-api-key",
        ),
        instance_type="sandbox" if snapshot_id else None,
        snapshot=snapshot_id,
        init_prompt=prompt,
        idle_nudge_prompt=(
            "You have stopped working. You MUST call the `finish` MCP tool with your "
            "execution_id and a summary of what you accomplished. Do not output anything "
            "else - just call the finish tool now."
        ),
        idle_nudge_timeout=120,
    )
