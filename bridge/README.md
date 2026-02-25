# Court Bridge

HTTP/SSE bridge for ACP agent subprocesses, running on Morph VMs.

This code is embedded into Morph snapshots by the server's `morph.py` module.
It is NOT deployed as a standalone service.

See [BRIDGE.md](../BRIDGE.md) for the full architecture: connection model, SSE reconnection, VM provisioning, and MCP tool flow.

## How it works

1. Server creates a Morph snapshot that includes this bridge code
2. When an agent VM boots, the server deploys the current bridge.py and starts it
3. Bridge spawns `claude-code-acp` (or other agent) as a subprocess
4. Bridge exposes HTTP endpoints: POST /input for stdin, GET /output SSE stream for stdout
5. Messages flow: Server ↔ HTTP/SSE ↔ Bridge ↔ ACP subprocess
