# ramure


ramure is an opinionated and lightweight Python library for building reliable agent software. It makes it easy to define programs where agents communicate across environments to accomplish a task.


Agent software are complex distributed systems. The goal of ramure is to make it easier to build and robustify these systems, in 2 notable ways:

- **Infrastructure primitives**: for agent communication, provisioning, and the software environments in which they run
- **Fault-tolerant and modular design**: ramure's abstractions encourage modularity and fault-tolerance in the design of agent software, using ideas from distributed systems programming like Erlang. See [multi-agent systems as distributed software](https://fulcrum.inc/2026/04/30/multi-agent-as-distributed.html) for the motivation behind ramure's design.

Here are some examples of tasks ramure makes easy with agents:

- optimization
- custom software generation pipelines with user input
- data pipelines
- worker pools, monitors, and supervisors


Note: ramure is the successor to our earlier agent runtime druids. The old code is preserved at [fulcrumresearch/druids-archive](https://github.com/fulcrumresearch/druids-archive).

## Install

```bash
pip install ramure
```

Python 3.11+ is required.

`ramure` depends on `pi` and `tmux` for the machines on which agents run.

## Quick start

Here is an annotated single worker program:

```python
import asyncio
from ramure import agent, agent_process, done, fail, wait

# registers the ramure runtime which will manage the agents and their machines, as well as controlling the lifecycle/cleanup
@agent_process
async def run_task(spec: str) -> str:
    # initialize an agent (either locally or on a remote sandbox)
    worker = await agent(f"worker")

    # register tools the agent can call in-harness
    @worker.on("finish")
    async def on_finish(summary: str) -> str:
        """Call with your result when the task is done."""
        done(summary)
        return "Recorded."

    @worker.on("give_up")
    async def on_give_up(reason: str) -> str:
        """Call if you cannot complete the task."""
        fail(f"gave_up: {reason}")
        return "Recorded."

    await worker.send(
        f"Task:\n\n{spec}\n\n"
        "When done, call finish(summary). If impossible, "
        "call give_up(reason)."
    )
    # wait for the done/fail lifecycle triggered by the agent events
    return await wait()


if __name__ == "__main__":
    print(asyncio.run(run_task("Write 10 diverse haikus about git rebase.")))
```

Run it with:

```bash
uv run your_program.py
```

Now you can connect to the agent via `ramure connect worker` to see what it's doing.

## Core concepts

### Agent processes

The central object of ramure is the `agent_process` (AP), defined by decorating an async function with `@agent_process`. Inside the function, you define agents and machines as well as how they should communicate.

When a root AP gets called, ramure initializes a runtime that is responsible for the lifecycle of the agents and machines it owns. Nested APs inherit the active runtime. To control the lifecycle of an AP, you define events that agents can call back into deterministic Python through `@agent.on(...)`.

Structuring how information moves in your program makes it easier to reliably use agent labor, especially in more complex cases. You can also configure which `image` an AP runs from — your local machine by default, or any other backend (see [Images and machines](#images-and-machines)).

### Composition

APs compose. An AP can call another AP the way you'd call any async function:

```python
@agent_process
async def main():
    code = await write_code("fibonacci function")
    review = await review_code(code)
    return code
```

Or fan out concurrently with `asyncio.gather`:

```python
@agent_process
async def main():
    results = await asyncio.gather(
        research("Rust"),
        research("Python"),
    )
    return results
```

### Observation, bubbling, and retry

An AP can also `spawn()` and obtain a handle to the running AP whose events become observable in real time.

```python
@agent_process
async def main():
    handle = spawn(flaky_task, "write a haiku")

    async for event in handle.events:
        if event.type == "failed":
            handle = spawn(flaky_task, "write a haiku")
        elif event.type == "done":
            return event.data
```

This handle holds on to the child AP's event stream, so you can observe it as it runs, retry if it fails, and pass events along to higher-level supervisors using `bubble()`.

Processes can emit custom events with `emit(type, data)`. If a supervisor wants child events to appear on its own event stream, use `bubble()`:

```python
@agent_process
async def worker_pool(specs: list[str]) -> None:
    for i, spec in enumerate(specs):
        tid = f"t{i:04d}"
        bubble(spawn(run_task, tid, spec), source=tid)
    await wait()
```

Now a parent observing `spawn(worker_pool, specs).events` can see child events too, tagged with `source=tid`.

### Images and machines

Where does an agent actually run? In ramure that's the **machine** — a running environment. An **image** is the template that spawns one.

```
Image.spawn() -> Machine
```

A `Machine` is anything that satisfies four async methods:

```python
class Machine(ABC):
    async def exec(self, command, *, user="agent", timeout=None) -> ExecResult: ...
    async def write_file(self, path, content) -> None: ...
    async def read_file(self, path) -> bytes: ...
    async def stop(self) -> None: ...
```

That's the whole contract. ramure uses `exec` to launch the `pi` harness in a tmux session on the machine, `write_file` to drop the agent extension there, and `stop` for cleanup. Two optional methods, `fork()` and `snapshot()`, let backends expose cheap state duplication if they have it (MorphCloud does; LocalMachine doesn't).

Bundled backends:

- `LocalImage(workdir=, env=)` — your host. Default. Each spawned `LocalMachine` is just a working directory.
- `MorphImage(...)` — [MorphCloud](https://morphcloud.com) VMs. Snapshot/fork friendly. `pip install ramure[morph]`.

### Adding your own backend

A new backend is a pair: an `Image` that knows how to bring up a machine, and a `Machine` that wraps the running thing. For example, a Docker container backend would look roughly like:

```python
from ramure.machines.base import Image, Machine
from ramure.types import ExecResult

class DockerMachine(Machine):
    def __init__(self, container_id: str):
        self.container_id = container_id

    async def exec(self, command, *, user="agent", timeout=None):
        # docker exec ... and wrap in ExecResult
        ...

    async def write_file(self, path, content): ...
    async def read_file(self, path): ...
    async def stop(self): ...  # docker rm -f

class DockerImage(Image):
    def __init__(self, image: str):
        self.image = image

    async def spawn(self) -> DockerMachine:
        # docker run -d ... and return DockerMachine(container_id)
        ...
```

Pass an instance anywhere ramure takes one: `@agent_process(image=DockerImage("my/image"))`, `await agent("name", image=...)`, or `await machine(image=...)`. The runtime takes care of registering the agent over WebSocket from inside the machine, and tears everything down via `Machine.stop()` when the AP exits.

Look at [`ramure/machines/local.py`](./ramure/machines/local.py) (~110 lines) for the simplest reference implementation, and [`ramure/machines/morph.py`](./ramure/machines/morph.py) for one with SSH, snapshots, and forking.

### Endpoints and afforded interfaces

APs can also encode specific ways in which they are interacted with, by exposing an API that can be called in code, or via another agent. To do this, use the `@expose` decorator:

```python
@agent_process
async def worker_pool() -> None:
    specs: dict[str, str] = {}

    @expose
    async def add_task(spec: str) -> str:
        tid = f"t{len(specs):04d}"
        specs[tid] = spec
        emit("task_added", {"task_id": tid, "spec": spec})
        bubble(spawn(run_task, tid, spec), source=tid)
        return tid

    @expose
    async def tasks() -> dict[str, str]:
        return dict(specs)

    emit("ready", None)
    await wait()
```

You can then consume the exposed worker pool in various ways:

```python
@agent_process
async def main():
    pool = spawn(worker_pool)

    async for event in pool.events:
        if event.type == "ready":
            break

    # call directly
    await pool.call("add_task", spec="Write a haiku about git rebase.")

    # or attach an agent to call the exposed functions on the pool,
    # which get exposed as tools
    monitor = await agent(
        "monitor",
        system_prompt="You run a pool of workers.",
    )
    await pool.attach(monitor, prefix="pool_")
```

This lets you give a component narrow affordances instead of ambient access to everything. Endpoints run inside the child process's scope, so calls to `emit()`, `done()`, and `fail()` inside an endpoint affect the child, not the caller. Child-owned agents are also visible through `handle.agents` once the child has created them.

## API

### Decorator

- `@agent_process(image=, timeout=, log_dir=, host=, port=, base_url=)` — wrap an async function as a process

### Ambient functions

- `await agent(name, system_prompt=, image=, machine=)` — create an agent
- `await machine(image=)` — spawn a standalone machine
- `connect(a, b, direction=)` — allow agents to message/send files
- `done(result)` — signal process success
- `fail(reason)` — signal process failure
- `await wait()` — block until `done()` or `fail()`
- `emit(type, data)` — emit a process event
- `spawn(fn, *args, **kwargs)` — run a process in the background, returns `ProcessHandle`
- `bubble(handle, source=)` — forward a child process's events onto the current process stream
- `@expose` — register an async function as an endpoint callable via `handle.call()` or attachable via `handle.attach()`
- `current_runtime()` — access the active runtime (rarely needed)

### Agent methods

- `agent.on(tool_name)` — decorator to register an async tool handler
- `agent.send(message)` — send a message to the agent
- `agent.exec(command)` — run a shell command on the agent's machine
- `agent.events` — async-iterable log of raw agent events

### ProcessHandle

- `handle.events` — async-iterable stream of process events
- `handle.agents` — dict of the child's agents created so far
- `await handle.call(name, **kwargs)` — call an endpoint
- `await handle.attach(agent, only=, prefix=)` — register endpoints as tools on an agent
- `handle.cancel()` — cancel the process

## CLI

Running a root `@agent_process` opens a Unix socket at
`~/.ramure/runtimes/{execution_id}.sock` and writes a per-run log tree
under `~/.ramure/logs/{execution_id}/`. The `ramure` CLI uses these:

```text
ramure ls                                       # live runs
ramure status [--id <prefix>]                   # agents, machines, connections, affordances
ramure send <agent> <msg> [--id <prefix>]
ramure call <endpoint> [k=v ...] [--id <prefix>] [--json]   # invoke an @expose'd endpoint
ramure connect <agent> [--id <prefix>]          # tmux attach
ramure ssh <agent> [--id <prefix>]              # shell on the agent's machine
```

`--id` takes an execution-id prefix. Omit it when there's only one live run.
All commands require the run to be live (socket present). Finished-run logs
remain under `~/.ramure/logs/{execution_id}/`.

### `ramure call` and external affordances

Whatever the root `@agent_process` `@expose`s is reachable from outside
the program via `ramure call`. Arguments are parsed as JSON first, with
string fallback, so typed values work without flags:

```text
ramure call add_task spec="write tests"
ramure call add_task count=3 enabled=true tags='["a","b"]'
ramure call list_tasks --json
```

Nested AP `@expose`s remain internal — reach them via `handle.call` /
`handle.attach` from owning code. Only the root program's surface is
externally addressable.


