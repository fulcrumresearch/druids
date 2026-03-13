# Bridge

ACP bridge for agent subprocesses, running on Morph VMs or Docker containers.

This code is embedded into sandbox environments by the server's `machine.py` module. It is NOT deployed as a standalone service.

## How it works

1. Server creates a sandbox (Morph snapshot or Docker container) that includes the bridge code
2. When an agent VM boots, the server deploys the current `bridge.py` and starts it
3. Bridge spawns `claude-code-acp` (or other agent) as a subprocess
4. Bridge connects back to the server via a reverse relay: it pushes stdout events and pulls stdin messages from the server's relay endpoints
5. Messages flow: Server <-> Reverse Relay <-> Bridge <-> ACP subprocess

The bridge does not expose HTTP endpoints to the server. Instead, it initiates connections to the server's bridge relay endpoints (`/api/bridge/{bridge_id}/push` and `/api/bridge/{bridge_id}/pull`), which avoids the need for the server to reach the VM directly.
