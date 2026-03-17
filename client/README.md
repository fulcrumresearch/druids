# Druids Client

CLI and type definitions for Druids. The package is also installed on agent VMs so agents can call `druids tool ...` to invoke program-registered handlers.

Run `druids --help` for available commands.

## Agent identity

On VMs, agent identity comes from per-process env vars set by the bridge: `DRUIDS_ACCESS_TOKEN`, `DRUIDS_AGENT_NAME`, `DRUIDS_EXECUTION_SLUG`. This lets multiple agents on the same machine have distinct identities. Machine-level config (`base_url`) loads from `~/.druids/config.json`.

## Building the wheel

The server installs the client wheel on agent VMs at boot. After changing client code, rebuild:

```
cd client && uv build
```

This creates a `.whl` in `client/dist/`. The server reads it from there.
