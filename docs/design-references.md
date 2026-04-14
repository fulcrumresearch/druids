# Design references

Theoretical models and prior systems relevant to druids' design. These inform
decisions about state ownership, concurrency, communication topology, and
failure handling.

## Actor model (Hewitt, 1973)

An actor has private state, receives messages one at a time, can send messages
to other actors, and can create new actors. No shared state — all coordination
through message passing.

Erlang proved this works at scale. Our agents are already actors: they have
names, receive messages, respond through tools. What's missing from the current
implementation is state isolation and per-actor message serialization.

**Relevance:** Per-agent state with per-agent handler serialization. Handlers
for one agent don't run concurrently. State lives on the agent, not in module
globals.

**Key work:** Hewitt, Bishop, Steiger. *A Universal Modular ACTOR Formalism for
Artificial Intelligence.* IJCAI 1973. Armstrong. *Making Reliable Distributed
Systems in the Presence of Software Errors.* PhD thesis, 2003 (Erlang/OTP).

## Communicating Sequential Processes (Hoare, 1978)

Processes communicate through named channels. The emphasis is on the
communication pattern, not the individual processes. Go's goroutines and
channels are the mainstream implementation.

Relevant because druids programs are really about the wiring between agents —
`connect()`, tool handlers, message routing. The agents themselves are opaque
(pi does whatever pi does). The program author designs the communication
topology.

**Relevance:** The framework should make topology the primary design surface.
Tools and connections define the program's structure.

**Key work:** Hoare. *Communicating Sequential Processes.* CACM 1978.

## π-calculus (Milner, 1992)

Extension of CSP where channel names can be passed in messages. A process can
send another process the ability to communicate with a third party. This models
dynamic, reconfigurable communication topologies.

Relevant because druids topology is dynamic — a handler can create new agents
and new connections at runtime. A finder agent spawns a worker and connects it
to a reviewer. The communication graph changes shape during execution. The
π-calculus is the formal model for exactly this kind of mobile process network.

**Relevance:** Dynamic agent creation and dynamic `connect()` are not
afterthoughts — they're core to the model. The framework should make
topology changes cheap and natural.

**Key work:** Milner, Parrow, Walker. *A Calculus of Mobile Processes.* Information
and Computation, 1992.

## Tuple spaces (Gelernter, 1985, Linda)

Shared associative memory. Processes write tuples, read or take tuples by
pattern matching. Decoupled in time and space — the writer doesn't need to know
the reader, and the tuple persists until consumed.

Relevant as an alternative to direct messaging. Instead of agent A sending a
message to agent B, agent A writes a result to a shared space and agent B reads
it when ready. The informal pattern of closing over module-level lists and dicts
in handlers is a poor man's tuple space without concurrency safety.

**Relevance:** If shared program state is needed, a structured shared-state
primitive with proper concurrency semantics (like a tuple space) is better
than ad-hoc mutable globals. But the actor model — routing all mutation
through an owning agent — may be the better default.

**Key work:** Gelernter. *Generative Communication in Linda.* TOPLAS 1985.

## Ownership types (Clarke, 1998; Rust)

Each value has one owner. Others can borrow references but cannot mutate
concurrently. Rust's borrow checker enforces this at compile time, eliminating
data races.

Python cannot enforce ownership at the language level. But the concept informs
API design: if agent state is owned by the agent, the framework communicates
that through the API shape (state lives on `agent.state`, handlers receive
`caller`) even if it can't prevent a determined programmer from reaching across
agents.

**Relevance:** Convention-level ownership. The API makes the right thing easy
and the wrong thing (cross-agent state mutation) an obvious code smell.

**Key work:** Clarke, Potter, Noble. *Ownership Types for Flexible Alias
Protection.* OOPSLA 1998. The Rust Reference, "Ownership" chapter.

## Supervision trees (Erlang/OTP)

Not just about spawning processes — about what happens when they fail. A
supervisor watches its children and applies a restart policy: one-for-one
(restart just the failed child), one-for-all (restart everything),
rest-for-one (restart the failed child and everything started after it).

Druids doesn't handle agent failure yet. The spec defers it. But the
abstraction should be ready: agent creation and lifecycle management should be
structured so failure handling can be added without rewriting the core.

**Relevance:** The Context is a supervisor. Agents are its children. When we
add failure handling, OTP's model — explicit restart policies, isolated
failure domains, supervision hierarchies — is the one to follow.

**Key work:** Armstrong. *Making Reliable Distributed Systems in the Presence
of Software Errors.* 2003. Erlang/OTP Design Principles, "Supervisor Behaviour."

## Event sourcing / CQRS

All state changes are appended to an immutable log. Current state is derived
by replaying events. Command-Query Responsibility Segregation separates the
write path (commands that produce events) from the read path (projections
built from events).

The JSONL orchestrator log is a primitive form of this. Tool calls, messages,
and state transitions are already logged. If tool call results were treated as
the authoritative state record (rather than mutable in-memory dicts), you get
replayability, auditability, and natural serialization of concurrent writes.

**Relevance:** The log is already there. Leaning into event sourcing for
program state would solve the concurrent-mutation problem by construction:
state is derived from a serial event stream, not mutated in place.

**Key work:** Young. *CQRS Documents.* 2010. Kleppmann. *Designing Data-Intensive
Applications,* ch. 11, "Stream Processing."

## Summary: what matters most now

1. **Per-agent state with per-agent serialization** (actors). Handlers for
   one agent run one at a time. State lives on the agent.

2. **Topology as the primary design surface** (CSP/π-calculus). The program
   defines the wiring. `connect()` and tool handlers are the program's
   structure.

3. **Supervision readiness** (OTP). Agent creation and lifecycle should be
   structured so failure handling can be added later without redesign.
