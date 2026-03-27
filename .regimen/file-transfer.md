# Inter-agent file transfer

Covers the `send_file` and `download_file` builtin MCP tools in `server/druids_server/lib/execution.py` and `server/druids_server/lib/tools.py`. Tests that agents can transfer files between Docker sandbox containers with topology enforcement, including happy path, edge cases, and error handling.

## Setup

Create a PostgreSQL database for the demo server.

```bash
sudo -u postgres psql -c "CREATE DATABASE druids_regimen"
```

Expected: `CREATE DATABASE`.

## Start the server

Start the Druids server with Docker sandbox backend on port 8002.

```bash timeout=15
cd server && DRUIDS_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/druids_regimen" DRUIDS_SANDBOX_TYPE=docker DRUIDS_PORT=8002 uv run alembic upgrade head 2>&1 | tail -2
```

Expected: output contains `Running upgrade`.

```bash
cd server && DRUIDS_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/druids_regimen" DRUIDS_SANDBOX_TYPE=docker DRUIDS_PORT=8002 DRUIDS_BASE_URL=http://localhost:8002 uv run druids-server &
```

Wait for the server to be ready.

```bash
curl -sf --retry 5 --retry-connrefused --retry-delay 1 http://localhost:8002/amcp/ -X POST -H "Content-Type: application/json" -H "Accept: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['jsonrpc'])"
```

Expected: `2.0` (MCP endpoint responding).

## Tool schemas are registered

Verify the two new tools appear in the builtin tool list with correct schemas.

```bash timeout=10
cd server && uv run python3 -c "
from druids_server.lib.tools import BUILTIN_TOOLS, BUILTIN_TOOL_SCHEMAS
assert 'send_file' in BUILTIN_TOOLS
assert 'download_file' in BUILTIN_TOOLS
sf = next(s for s in BUILTIN_TOOL_SCHEMAS if s['name'] == 'send_file')
df = next(s for s in BUILTIN_TOOL_SCHEMAS if s['name'] == 'download_file')
assert sf['inputSchema']['required'] == ['receiver', 'path']
assert df['inputSchema']['required'] == ['sender', 'path']
assert 'dest_path' in sf['inputSchema']['properties']
assert 'dest_path' in df['inputSchema']['properties']
print('send_file and download_file schemas OK')
"
```

Expected: `send_file and download_file schemas OK`.

## E2E file transfer between Docker containers

Run the full end-to-end test: spin up two real Docker containers, write files, transfer them, verify contents, test topology enforcement and error paths.

```bash timeout=60
cd server && uv run python3 -c "
import asyncio
from unittest.mock import MagicMock, patch
from uuid import uuid4
from druids_server.lib.sandbox.docker import DockerSandbox
from druids_server.lib.execution import Execution
from druids_server.lib.machine import Machine

async def demo():
    results = []

    sandbox_a = await DockerSandbox.create('druids-base')
    sandbox_b = await DockerSandbox.create('druids-base')

    machine_a = Machine(sandbox=sandbox_a)
    machine_b = Machine(sandbox=sandbox_b)
    agent_a = MagicMock(); agent_a.name = 'alice'; agent_a.machine = machine_a
    agent_b = MagicMock(); agent_b.name = 'bob'; agent_b.machine = machine_b

    with patch('druids_server.lib.execution.CaptionSummarizer'):
        ex = Execution(id=uuid4(), slug='test', user_id='u1')
    ex.agents = {'alice': agent_a, 'bob': agent_b}
    ex.edges = [{'from': 'alice', 'to': 'bob'}, {'from': 'bob', 'to': 'alice'}]

    # Test 1: send_file
    await sandbox_a.write_file('/tmp/hello.txt', 'Hello from Alice!')
    r = await ex._handle_send_file('alice', {'receiver': 'bob', 'path': '/tmp/hello.txt', 'dest_path': '/tmp/got.txt'})
    content = await sandbox_b.read_file('/tmp/got.txt')
    ok = 'Transferred' in r and content == b'Hello from Alice!'
    results.append(('send_file', ok, r))

    # Test 2: download_file
    await sandbox_a.write_file('/tmp/data.json', '{\"x\":1}')
    r = await ex._handle_download_file('bob', {'sender': 'alice', 'path': '/tmp/data.json'})
    content = await sandbox_b.read_file('/tmp/data.json')
    ok = 'Transferred' in r and content == b'{\"x\":1}'
    results.append(('download_file', ok, r))

    # Test 3: default dest_path
    await sandbox_a.write_file('/home/agent/f.txt', 'same path')
    r = await ex._handle_send_file('alice', {'receiver': 'bob', 'path': '/home/agent/f.txt'})
    content = await sandbox_b.read_file('/home/agent/f.txt')
    ok = 'Transferred' in r and content == b'same path'
    results.append(('default_dest', ok, r))

    # Test 4: topology blocks disconnected
    ex.edges = []
    r = await ex._handle_send_file('alice', {'receiver': 'bob', 'path': '/tmp/hello.txt'})
    ok = 'not reachable' in r
    results.append(('topo_blocked', ok, r))

    # Test 5: one-way edge
    ex.edges = [{'from': 'alice', 'to': 'bob'}]
    r_ok = await ex._handle_send_file('alice', {'receiver': 'bob', 'path': '/tmp/hello.txt'})
    r_fail = await ex._handle_send_file('bob', {'receiver': 'alice', 'path': '/tmp/got.txt'})
    ok = 'Transferred' in r_ok and 'not reachable' in r_fail
    results.append(('one_way', ok, f'fwd={r_ok} rev={r_fail}'))

    # Test 6: file not found
    r = await ex._handle_send_file('alice', {'receiver': 'bob', 'path': '/no/such/file'})
    ok = 'could not read' in r
    results.append(('not_found', ok, r))

    # Test 7: binary data
    binary = bytes(range(256))
    await sandbox_a.write_file('/tmp/bin', binary)
    r = await ex._handle_send_file('alice', {'receiver': 'bob', 'path': '/tmp/bin'})
    got = await sandbox_b.read_file('/tmp/bin')
    ok = got == binary and '256 bytes' in r
    results.append(('binary', ok, r))

    await sandbox_a.stop()
    await sandbox_b.stop()

    all_ok = True
    for name, ok, detail in results:
        status = 'PASS' if ok else 'FAIL'
        if not ok: all_ok = False
        print(f'{status}: {name} — {detail}')
    print()
    print('ALL PASSED' if all_ok else 'SOME FAILED')

asyncio.run(demo())
"
```

Expected: every line starts with `PASS:` and the last line is `ALL PASSED`.

## Unit tests pass

```bash timeout=30
cd server && uv run pytest tests/lib/test_file_transfer.py tests/lib/test_tool_schemas.py -v 2>&1 | tail -25
```

Expected: `17 passed` from `test_file_transfer.py` and all tests in `test_tool_schemas.py` pass, 0 failures.

## Cleanup

```bash
pkill -f "druids-server" 2>/dev/null; sudo -u postgres psql -c "DROP DATABASE IF EXISTS druids_regimen" 2>/dev/null; echo "done"
```

Expected: `done`.
