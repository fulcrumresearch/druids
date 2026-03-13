# Inline runtime: in-process program execution

Covers the in-process program execution path where programs run inside the server process, tool handlers are registered on `Agent._handlers` via `@agent.on()`, and tool calls dispatch directly from `Execution.call_tool()` to those handlers without HTTP relay. Relates to `server/druids_server/lib/execution.py`, `server/druids_server/lib/agents/base.py`, `server/druids_server/lib/program_dispatch.py`, and `server/druids_server/api/routes/executions.py`.

## Setup

Run the inline runtime unit tests. These exercise tool dispatch, topology enforcement, schema extraction, and protocol conformance without needing a running server.

```bash timeout=30
cd /home/ubuntu/code/druids/server && DRUIDS_SANDBOX_TYPE=docker .venv/bin/python -m pytest tests/lib/test_execution_relay.py tests/lib/test_tool_schemas.py tests/lib/test_protocol_conformance.py -v
```

Expected: all tests pass, zero failures. There should be tests covering:
- `TestDispatchTool` (6 tests): handler dispatch, async handlers, missing handler/agent errors, caller injection
- `TestMessageTool` (2 tests): topology enforcement for messaging
- `TestListAgentsTool` (1 test): only lists topology-connected agents
- `TestTopology` (2 tests): bidirectional and forward-only connections
- `TestClientEvents` (3 tests): client event dispatch, unregistered events, event name tracking
- `TestWriteCliConfig` (1 test): machine config writing
- `TestExtractToolSchema` (7 tests): schema extraction from function signatures
- `TestExtractAgentToolSchemas` (1 test): batch schema extraction from Agent handlers
- `TestBuiltinToolSchemas` (3 tests): expose, message, list_agents schemas
- `TestListToolSchemas` (2 tests): combined built-in + program-defined schemas
- `TestConnectAgentDruidsMCP` (2 tests): MCP server inclusion in agent connections
- `TestPreambleRemoved` (2 tests): system prompt not modified
- Protocol conformance (2 tests): Agent satisfies AgentProtocol, Execution satisfies ProgramContext

## No references to deleted runtime_relay

The `runtime_relay.py` module was removed. No file in the server should import or reference it.

```bash timeout=10
cd /home/ubuntu/code/druids/server && grep -r "runtime_relay" --include="*.py" . ; echo "exit: $?"
```

Expected: no matches found. Exit code 0 from grep means references exist (fail). The echo should show `exit: 1` indicating grep found nothing.

## Agent.on() registers handlers

Verify that `Agent.on()` stores handlers in `_handlers` rather than being a no-op.

```bash timeout=10
cd /home/ubuntu/code/druids/server && DRUIDS_SANDBOX_TYPE=docker .venv/bin/python -c "
from druids_server.lib.agents.base import Agent
a = Agent(name='test')

@a.on('submit')
def on_submit(summary: str = ''):
    return f'submitted: {summary}'

assert 'submit' in a._handlers, 'handler not registered'
assert a._handlers['submit']('hello') == 'submitted: hello', 'handler returned wrong value'
print('Agent.on() registers and invokes handlers correctly')
"
```

Expected: prints "Agent.on() registers and invokes handlers correctly" with no errors.

## Schema extraction from handlers

Verify that `extract_tool_schema` produces correct MCP-compatible schemas from function signatures.

```bash timeout=10
cd /home/ubuntu/code/druids/server && DRUIDS_SANDBOX_TYPE=docker .venv/bin/python -c "
from druids_server.lib.program_dispatch import extract_tool_schema

def handler(name: str, count: int = 0):
    '''Do something useful.'''

schema = extract_tool_schema('my_tool', handler)
assert schema['name'] == 'my_tool'
assert schema['description'] == 'Do something useful.'
assert schema['inputSchema']['properties']['name']['type'] == 'string'
assert schema['inputSchema']['properties']['count']['type'] == 'integer'
assert 'name' in schema['inputSchema']['required']
assert 'count' not in schema['inputSchema']['required']
print('Schema extraction produces correct MCP-compatible output')
"
```

Expected: prints "Schema extraction produces correct MCP-compatible output" with no errors.

## Inline tool dispatch

Verify that `Execution.call_tool()` dispatches directly to in-memory handlers.

```bash timeout=10
cd /home/ubuntu/code/druids/server && DRUIDS_SANDBOX_TYPE=docker .venv/bin/python -c "
import asyncio
from uuid import uuid4
from druids_server.lib.agents.base import Agent
from druids_server.lib.execution import Execution

async def test():
    ex = Execution(id=uuid4(), slug='test', user_id='u1')
    agent = Agent(name='builder')
    agent._ready = asyncio.get_event_loop().create_future()
    agent._ready.set_result(None)
    ex.agents['builder'] = agent

    @agent.on('greet')
    def on_greet(name: str = 'world'):
        return f'hello {name}'

    result = await ex.call_tool('builder', 'greet', {'name': 'druids'})
    assert result == 'hello druids', f'unexpected result: {result}'
    print('Inline tool dispatch works correctly')

asyncio.run(test())
"
```

Expected: prints "Inline tool dispatch works correctly" with no errors.

## Topology enforcement

Verify that `connect()` and `is_connected()` control agent messaging permissions.

```bash timeout=10
cd /home/ubuntu/code/druids/server && DRUIDS_SANDBOX_TYPE=docker .venv/bin/python -c "
from uuid import uuid4
from druids_server.lib.agents.base import Agent
from druids_server.lib.execution import Execution

ex = Execution(id=uuid4(), slug='test', user_id='u1')
a = Agent(name='alice')
b = Agent(name='bob')
c = Agent(name='carol')

ex.connect(a, b)
ex.connect(a, c, direction='forward')

assert ex.is_connected('alice', 'bob')
assert ex.is_connected('bob', 'alice')
assert ex.is_connected('alice', 'carol')
assert not ex.is_connected('carol', 'alice')
print('Topology enforcement works correctly')
"
```

Expected: prints "Topology enforcement works correctly" with no errors.
