# Design question: state coordination in multi-agent orchestration

## What druids is

Druids is a Python framework for orchestrating multiple AI coding agents. A druids program is an async Python script. When you run it, an in-process HTTP server starts, machines are spawned (Docker containers or local processes), and LLM coding agents are launched — each in its own tmux pane with filesystem access, shell, and dev tools. The agents communicate with the orchestrator through tool calls (HTTP POST) and receive messages via SSE.

```python
import asyncio
from druids import Context, LocalImage

async def main():
    async with Context(image=LocalImage()) as ctx:
        builder = await ctx.agent("builder", prompt="Implement the feature.")
        reviewer = await ctx.agent("reviewer", prompt="Review the builder's work.")
        ctx.connect(builder, reviewer)

        @builder.on("submit")
        async def submit(summary: str = ""):
            await reviewer.send(f"Review this:\n{summary}")
            return "Submitted for review."

        @reviewer.on("approve")
        async def approve(summary: str = ""):
            await ctx.done(summary)
            return "Done."

        @reviewer.on("reject")
        async def reject(feedback: str = ""):
            await builder.send(f"Fix this:\n{feedback}")
            return "Feedback sent."

        result = await ctx.wait()
        print(result)

asyncio.run(main())
```

- `await ctx.agent(name, prompt=...)` spawns a machine, launches an LLM agent, waits until it's ready, delivers the prompt. Returns an `Agent` handle.
- `@agent.on("tool_name")` registers a tool handler. The function signature becomes the tool schema. When the agent calls the tool, the handler runs.
- `await agent.send(message)` delivers text to the agent's LLM context.
- `ctx.connect(a, b)` enables direct messaging between two agents.
- `await ctx.done(result)` / `await ctx.fail(reason)` signal completion.

Agents are autonomous LLMs. They decide what tools to call and when. The program influences them through prompts and available tools but cannot force a specific sequence. Each agent is heavyweight — a full LLM process on its own machine, seconds to spawn. Typical executions have 2–20 agents.

Tool handlers run concurrently as async tasks. Agents can be created dynamically inside handlers. The topology (who can talk to whom, what tools exist) changes at runtime.

## The problem

Programs naturally accumulate shared mutable state that multiple agents' tool handlers touch concurrently. The program author writes what looks like a straightforward script, but the concurrent tool calls are invisible — they look like sequential function calls from each agent's perspective, yet the handlers share state through closures with no synchronization.

The kinds of state that get shared:

- **Counters and accumulators.** Multiple workers report results. A handler appends to a list and checks if all workers are done. Two handlers see the threshold met simultaneously.
- **Task queues and dependency graphs.** Agents pull tasks from a shared queue. Completion of one task unblocks others. Multiple agents popping and pushing concurrently.
- **Shared codebases.** Multiple agents on separate machines modifying the same repository. Who pushes when, how merge conflicts resolve, how to sequence dependent changes.
- **Knowledge bases.** A research swarm where agents contribute findings and read each other's findings. Concurrent reads during writes.
- **Budgets.** A global token or cost limit that all agents decrement. Classic concurrent counter problem.

## Two ideas we have

### 1. Agent-owned state with serialized handlers

Each agent owns a state object. Handlers for that agent are serialized — one at a time, like an Erlang process. Multiple agents can call the same tool concurrently, but the invocations queue and execute sequentially. No locks needed.

Cross-agent state follows the same pattern: pick one agent to own it, everyone else calls that agent's tools.

### 2. `ask()` — typed request-response

`ask()` sends a prompt to an agent and awaits until the agent calls one of a set of expected tools. Returns a result with which tool was called and what parameters the agent passed. The agent may call other tools along the way (bash, read, write) — those proceed normally. `ask()` only resolves when one of the listed tools is called, or times out.

This turns inter-agent coordination from callback wiring into sequential control flow. You send work, await a typed result, continue.

The two compose: agent-owned state makes per-agent coordination safe, `ask()` makes inter-agent coordination sequential and typed.

## The question

Beyond these two primitives — serialized agent-owned state and `ask()` — are there additional program-level primitives or design choices we should provide to help with state coordination in these multi-agent executions? Or is the right move to give users agents-as-processes with these two building blocks and let them structure things however they want? Or should we even have those primitives?

Respond with your thoughts on the ideal design, and then a more detailed literature review of how people think about these ideas and resources that might be good to think about.
