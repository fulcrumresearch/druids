# Built-in agent tools

Every agent automatically has access to these tools via the `druids` CLI on
its VM. These are handled by the runtime, not by program-defined handlers.

| Tool | Arguments | Description |
|---|---|---|
| `message` | `receiver`, `message` | Send a message to another agent in the execution. |
| `list_agents` | (none) | List all agent names in the execution. |
| `expose` | `service_name`, `port` | Expose a local port as a public HTTPS URL. |

Agents call these via the CLI:

```
druids tool message receiver="other-agent" message="hello"
druids tool list_agents
druids tool expose service_name="web" port=3000
```

Program-defined tools (registered with [`@agent.on()`](agent.md#agenton)) are also available
through the same CLI interface.
