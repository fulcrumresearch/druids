"""Setup session -- lightweight agent session for the setup wizard.

A SetupSession wraps one AgentConnection on the user's devbox VM. It does not
use the Program/Execution machinery (no DB rows, no task tracking, no spawning).
It is a direct 1:1 mapping: one devbox VM, one agent connection, one session.

Event history is kept in memory only. On server restart, all sessions are gone
and the API clears setup_slug on the devbox so users can start fresh.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Literal

import druids_server.config as config
from druids_server.lib.connection import AgentConnection
from druids_server.lib.machine import Machine
from druids_server.utils.forwarding_tokens import mint_token
from druids_server.utils.slugs import generate_task_slug


SETUP_SYSTEM_PROMPT = dedent(
    """
    You are a setup wizard running directly on the user's development VM. Your job is to configure this repository so that Druids agents can build, test, and interact with it end to end.

    Be interactive. Before doing anything significant, tell the user what you are about to do and why. Surface blockers early. If you need credentials, service accounts, or environment variable values you cannot derive from the codebase, ask for them explicitly before proceeding.

    Before starting, check whether `.druids/SETUP.md` already exists. If it does, read it. If setup was partially completed in a previous session, continue from where it left off rather than starting over.

    When you finish and the environment is fully ready, tell the user clearly: "Setup is complete. You can now click Save snapshot." Do not say this until the environment is actually ready.
    """
).strip()

SETUP_USER_PROMPT = dedent(
    """
    Set up a development environment on this VM so that an AI coding agent can build, test, and interact with the project `{repo_full_name}` end to end.

    The repo `{repo_full_name}` is cloned at `/home/agent/repo`. The VM runs Debian with Python 3.11, Node.js LTS, and GitHub CLI pre-installed. Commands run as root by default, so prefix with `sudo -u agent bash -c '...'` when running as the project user. The agent user has passwordless sudo.

    At each step, explain what you are about to do, what you found, and what you need from me. By default, figure things out from the codebase. But give me a chance to intervene -- I may know about services that need to run, dependencies that are tricky to install, environment variables that are not obvious from the code, or testing requirements like headless browsers.

    Before proceeding, ask me: "I'm going to explore the repo to figure out dependencies, services, and environment config. Do you have any setup instructions I should follow, or anything I should know before I start? (e.g. specific services that need to run, tricky dependencies, env vars I'll need values for)"

    ## 1. Install dependencies

    Read the project's setup files to figure out what it needs:

    ```
    cat /home/agent/repo/package.json
    cat /home/agent/repo/pyproject.toml
    cat /home/agent/repo/Makefile
    ```

    Tell me what you found and what you are about to install. I may flag things like: pinned system libraries, packages that need build tools (gcc, libffi-dev), or dependencies that require special installation steps.

    Then install the dependencies using whatever package manager the project uses.

    ## 2. Configure the environment

    Figure out what the project needs beyond dependencies:

    - Read the code for config: look for `.env` files, `BaseSettings` classes, `os.environ.get` calls, `config.json`, `docker-compose.yml`, etc.
    - Identify services: databases, caches, message queues, background workers.
    - Identify ports: what the project binds to and whether any need external exposure.

    Present your findings to me. Be specific about what you need:
    - For each env var: what it controls, whether you can set a reasonable default, or whether you need a value from me.
    - For each service: what it is, what the project uses it for, and whether you should install it.
    - For ports: what binds where, and whether agents will need to expose any of them externally.

    Ask me for secret values (API keys, tokens) -- do not guess or skip these. If the project needs a database, cache, or other service, install and configure it during setup. It will be captured in the snapshot. Use `.env` files or write to `/home/agent/.bashrc` for environment variables.

    ## 3. Verify the environment

    Run the project's test suite to confirm the basics work. If something fails, diagnose and fix it.

    But tests are just a baseline. Before moving on, ask me: "Tests pass. Is there anything else I should verify? For example: does the server need to start and respond to requests? Are there integration tests that need a running database? Do tests need a headless browser or other runtime?"

    If the project has a server, start it and hit its health endpoint. If it has a CLI, run a command. If it has a database, confirm it connects. The goal is a VM where an agent can build, run, and interact with the system end to end, not just pass unit tests.

    ## 4. Write .druids/SETUP.md

    Synthesize what you learned during setup into `/home/agent/repo/.druids/SETUP.md`. Agent VMs are forked from this snapshot, so dependencies are already installed and the environment is already configured. This file tells agents what is already set up for them and how to use it.

    Do not duplicate information already in the repo's README, CLAUDE.md, or other docs. Focus on the runtime environment that was configured during setup:

    - What services are running (databases, servers, workers) and on what ports. How to restart each one.
    - What environment variables were set, where they live, and what they control (names and purpose, not secret values).
    - What ports are in use. If any need to be exposed externally, what env vars or config must change to match the new URL.
    - How to verify the system works: not just "run pytest" but the full loop. If the project has a server, how to start it and confirm it responds. If it has an API, how to call it.
    - Known issues or quirks specific to the environment (e.g. "Postgres must be running before the server starts").

    The purpose is so agents can immediately interact with the system end to end without rediscovering how the project works. Keep it concise.

    After writing the file, ask me if I want to commit `.druids/SETUP.md` to the repo so that Druids agents always have it, even if the snapshot is rebuilt.

    ## 5. Done

    Tell me when everything is ready so I can save a snapshot. Future executions will start from this state.
    """
).strip()

MODIFY_USER_PROMPT = dedent(
    """
    You are modifying an existing development environment for `{repo_full_name}`.
    This VM was created from a saved snapshot, so dependencies, services, and
    configuration from the initial setup are already in place.

    The repo is at `/home/agent/repo`.

    Start by reading `.druids/SETUP.md` to understand what is already configured.
    Then ask me: "What would you like to change?"

    When I tell you what to change, make the modifications. After each change,
    verify that the affected part of the environment still works (e.g. if I ask
    you to add a dependency, install it and confirm the test suite still passes).
    Update `.druids/SETUP.md` if the change affects it.

    When all changes are complete, tell me: "Modifications complete. You can now
    click Save snapshot."
    """
).strip()


logger = logging.getLogger(__name__)

# In-memory registry: repo_full_name -> SetupSession
# Lives here (not in the route module) so proxy.py can import it without
# creating a circular dep through the routes layer.
setup_registry: dict[str, SetupSession] = {}

# Per-repo locks that serialize concurrent /setup/wizard/start calls. Without
# this, a page reload arriving while a 30s launch is in progress sees
# setup_slug=None in the DB and starts a second launch on the same VM.
_launch_locks: dict[str, asyncio.Lock] = {}


def get_launch_lock(repo_full_name: str) -> asyncio.Lock:
    """Get or create a per-repo lock for serializing setup launches."""
    if repo_full_name not in _launch_locks:
        _launch_locks[repo_full_name] = asyncio.Lock()
    return _launch_locks[repo_full_name]


def is_active_session(slug: str) -> bool:
    """Check if a slug corresponds to a live setup wizard session."""
    for session in setup_registry.values():
        if session.slug == slug:
            return True
    return False


# ---------------------------------------------------------------------------
# SetupSession dataclass
# ---------------------------------------------------------------------------


@dataclass
class SetupSession:
    """Owns the wizard connection for one devbox."""

    slug: str
    user_id: str
    machine: Machine
    conn: AgentConnection
    mode: Literal["setup", "modify"] = "setup"
    status: Literal["running", "done", "error"] = "running"
    events: list[dict] = field(default_factory=list)  # sequential event history
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

    # Internal text buffer: accumulate agent text chunks until flushed
    _text_buffer: str = field(default="", repr=False)
    _flush_task: asyncio.Task | None = field(default=None, repr=False)

    # Track pending tool calls: toolCallId -> title / input / kind
    _tool_titles: dict[str, str] = field(default_factory=dict, repr=False)
    _tool_inputs: dict[str, str] = field(default_factory=dict, repr=False)
    _tool_kinds: dict[str, str] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def emit_event(session: SetupSession, event_type: str, data: dict) -> None:
    """Append a new event to session history and notify all subscribers."""
    event_id = len(session.events)
    event = {"id": event_id, "event": event_type, "data": data}
    session.events.append(event)
    for queue in list(session._subscribers):
        queue.put_nowait(event)


def _broadcast(session: SetupSession, event_type: str, data: dict) -> None:
    """Push to live subscribers only. Does not persist to session.events.

    Used for transient updates (e.g. partial tool output) that live subscribers
    should see in real time but that should not appear in replay history.
    """
    event = {"event": event_type, "data": data, "ephemeral": True}
    for queue in list(session._subscribers):
        queue.put_nowait(event)


def subscribe(session: SetupSession) -> asyncio.Queue:
    """Register a new SSE subscriber and return its queue."""
    queue: asyncio.Queue = asyncio.Queue()
    session._subscribers.append(queue)
    return queue


def unsubscribe(session: SetupSession, queue: asyncio.Queue) -> None:
    """Remove an SSE subscriber queue."""
    try:
        session._subscribers.remove(queue)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Session launch
# ---------------------------------------------------------------------------


async def launch_setup_session(
    user_id: str,
    repo_full_name: str,
    machine: Machine,
    mode: Literal["setup", "modify"] = "setup",
) -> SetupSession:
    """Launch a setup wizard session on a provisioned machine.

    Deploys the bridge, creates an AgentConnection, wires event handlers,
    and sends the initial setup prompt. The session's event handlers are
    wired before the prompt is sent so that the first response is captured.

    Args:
        user_id: The owner of this session (for ownership checks).
        repo_full_name: "owner/repo" -- used in the agent prompt.
        machine: A Machine with a running sandbox (already provisioned).
        mode: "setup" for fresh setup, "modify" for modifying existing snapshot.

    Returns:
        A running SetupSession. The agent is already active.
    """
    slug = generate_task_slug()
    working_dir = "/home/agent/repo"
    logger.info("launch_setup_session slug=%s user=%s repo=%s", slug, user_id, repo_full_name)

    # Mint a forwarding token so the agent can reach the Anthropic proxy.
    forwarding_token = mint_token(user_id, slug, "wizard")

    # Install volatile packages (CLI wheel). Idempotent.
    await machine.init()

    # Deploy bridge and start the ACP process.
    settings = config.get_settings()
    bridge_id, bridge_token = await machine.ensure_bridge(
        _SetupACPConfig(forwarding_token=forwarding_token, base_url=str(settings.base_url)),
        working_directory=working_dir,
    )

    # Establish AgentConnection.
    conn = AgentConnection(bridge_id=bridge_id, bridge_token=bridge_token)
    await conn.start()

    await conn.new_session(cwd=working_dir, system_prompt=SETUP_SYSTEM_PROMPT)
    await conn.set_model("claude-opus-4-6")

    # Build the session object before wiring handlers (handlers close over it).
    session = SetupSession(slug=slug, user_id=user_id, machine=machine, conn=conn, mode=mode)
    _wire_handlers(session)

    # Send the initial prompt.
    if mode == "modify":
        prompt_text = MODIFY_USER_PROMPT.format(repo_full_name=repo_full_name)
    else:
        prompt_text = SETUP_USER_PROMPT.format(repo_full_name=repo_full_name)
    await conn.prompt_nowait(prompt_text)

    logger.info("launch_setup_session slug=%s launched", slug)
    return session


@dataclass
class _SetupACPConfig:
    """Minimal config object that satisfies Machine.ensure_bridge's interface."""

    forwarding_token: str
    base_url: str

    def to_bridge_start(self, working_directory: str) -> dict:
        """Produce the payload for the bridge /start endpoint."""
        return {
            "command": "claude-code-acp",
            "args": ["--dangerously-skip-permissions"],
            "env": {
                "ANTHROPIC_API_KEY": self.forwarding_token,
                "ANTHROPIC_BASE_URL": f"{self.base_url}/api/proxy/anthropic",
            },
            "working_directory": working_directory,
        }


# ---------------------------------------------------------------------------
# ACP event handlers
# ---------------------------------------------------------------------------


def _to_str(val) -> str:
    """Coerce an ACP raw input/output value to a plain string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


def _format_tool(title: str, kind: str, raw_input) -> tuple[str, str]:
    """Return (display_name, detail_line) for a tool call."""
    title = title.replace("`", "")

    if kind in ("execute", "search"):
        lines = [line.strip() for line in title.strip().splitlines() if line.strip()]
        first = lines[0] if lines else title
        if first.startswith("# "):
            first = first[2:]
        if len(first) > 100:
            first = first[:97] + "..."
        detail = title if (len(lines) > 1 or first != title) else ""
        return first, detail

    if kind in ("read", "edit", "delete", "move"):
        path = ""
        if isinstance(raw_input, dict):
            path = raw_input.get("file_path", "") or raw_input.get("notebook_path", "")
            if not isinstance(path, str):
                path = ""
        return title, path

    if kind == "other" and title.startswith("mcp__"):
        parts = title.split("__")
        short = parts[-1] if len(parts) >= 3 else title
        return short, ""

    return title, ""


def _remove_session(session: SetupSession) -> None:
    """Remove a session from the registry if it matches the current entry."""
    for repo, registered in list(setup_registry.items()):
        if registered is session:
            setup_registry.pop(repo, None)
            logger.info("Removed dead session slug=%s for %s", session.slug, repo)
            break


def _wire_handlers(session: SetupSession) -> None:
    """Register session/update notification handlers on the connection."""

    async def on_disconnect(params: dict) -> None:
        session.status = "error"
        _remove_session(session)

    session.conn.on("disconnect", on_disconnect)

    async def on_session_update(params: dict) -> None:
        update = params.get("update", {})
        session_update = update.get("sessionUpdate")

        if session_update == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                text = content.get("text", "")
                if text:
                    _handle_text_chunk(session, text)

        elif session_update == "tool_call":
            flush_text_buffer(session)
            tool_call_id = update["toolCallId"]
            kind = update.get("kind", "other")
            name, detail = _format_tool(update["title"], kind, update.get("rawInput"))
            session._tool_titles[tool_call_id] = name
            session._tool_inputs[tool_call_id] = detail
            session._tool_kinds[tool_call_id] = kind
            _broadcast(
                session,
                "tool",
                {"id": tool_call_id, "name": name, "kind": kind, "input": detail, "output": "", "status": "active"},
            )

        elif session_update == "tool_call_update":
            tool_call_id = update["toolCallId"]
            status = update.get("status")
            kind = session._tool_kinds.get(tool_call_id, "other")

            updated_title = update.get("title")
            if updated_title:
                name, detail = _format_tool(updated_title, kind, update.get("rawInput"))
                session._tool_titles[tool_call_id] = name
                session._tool_inputs[tool_call_id] = detail

            if status == "in_progress":
                name = session._tool_titles.get(tool_call_id, "unknown")
                detail = session._tool_inputs.get(tool_call_id, "")
                raw_output = _to_str(update.get("rawOutput"))
                _broadcast(
                    session,
                    "tool",
                    {
                        "id": tool_call_id,
                        "name": name,
                        "kind": kind,
                        "input": detail,
                        "output": raw_output,
                        "status": "active",
                    },
                )
            elif status == "completed":
                name = session._tool_titles.pop(tool_call_id)
                detail = session._tool_inputs.pop(tool_call_id, "")
                session._tool_kinds.pop(tool_call_id, None)
                if updated_title:
                    new_name, new_detail = _format_tool(updated_title, kind, update.get("rawInput"))
                    name = new_name
                    if new_detail:
                        detail = new_detail
                raw_output = _to_str(update.get("rawOutput"))
                emit_event(
                    session,
                    "tool",
                    {
                        "id": tool_call_id,
                        "name": name,
                        "kind": kind,
                        "input": detail,
                        "output": raw_output,
                        "status": "done",
                    },
                )
            elif status == "failed":
                name = session._tool_titles.pop(tool_call_id, "unknown")
                detail = session._tool_inputs.pop(tool_call_id, "")
                session._tool_kinds.pop(tool_call_id, None)
                if updated_title:
                    new_name, new_detail = _format_tool(updated_title, kind, update.get("rawInput"))
                    name = new_name
                    if new_detail:
                        detail = new_detail
                raw_output = _to_str(update.get("rawOutput"))
                emit_event(
                    session,
                    "tool",
                    {
                        "id": tool_call_id,
                        "name": name,
                        "kind": kind,
                        "input": detail,
                        "output": raw_output,
                        "status": "error",
                    },
                )

    session.conn.on("session/update", on_session_update)


def _handle_text_chunk(session: SetupSession, chunk: str) -> None:
    """Accumulate text chunk and broadcast progress to live subscribers.

    Text is accumulated in _text_buffer and broadcast as ephemeral
    ``message_stream`` events so the UI can show the text as it arrives.
    The buffer is persisted as a single ``message`` event when the agent
    starts a tool call (see ``flush_text_buffer``) or after a 2-second
    idle timeout.
    """
    session._text_buffer += chunk
    _broadcast(session, "message_stream", {"role": "assistant", "text": session._text_buffer})

    if session._flush_task and not session._flush_task.done():
        session._flush_task.cancel()
    session._flush_task = asyncio.create_task(_delayed_flush(session, 2.0))


async def _delayed_flush(session: SetupSession, delay: float) -> None:
    """Wait *delay* seconds, then flush the text buffer."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    flush_text_buffer(session)


def flush_text_buffer(session: SetupSession) -> None:
    """Persist accumulated text as a single message event and clear buffer."""
    if session._flush_task and not session._flush_task.done():
        session._flush_task.cancel()
    session._flush_task = None
    text = session._text_buffer.strip()
    session._text_buffer = ""
    if text:
        emit_event(session, "message", {"role": "assistant", "text": text})
