"""MCP routes for agent communication.

These endpoints are exposed via MCP so agents can call them as tools.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orpheus.api.deps import CallerContext, CallerHeaders, CurrentUser, UserExecutions
from orpheus.db.models.execution import update_execution
from orpheus.db.session import get_session
from orpheus.lib.devbox import InstanceNotFound, resolve_instance
from orpheus.lib.execution import Execution, ExposedService
from orpheus.lib.machine import BRIDGE_PORT


router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_execution(slug: str, executions: dict[str, Execution]) -> Execution:
    """Get execution by slug or raise 404."""
    execution = executions.get(slug)
    if execution is None:
        raise HTTPException(404, f"Execution {slug} not found")
    return execution


def _resolve_execution(body_slug: str | None, caller: CallerContext, executions: dict[str, Execution]) -> Execution:
    """Resolve execution from header (preferred) or body slug."""
    slug = caller.execution_slug or body_slug
    if not slug:
        raise HTTPException(400, "execution_slug is required")
    return _get_execution(slug, executions)


def _resolve_sender(body_sender: str | None, caller: CallerContext) -> str:
    """Resolve sender from header (preferred) or body."""
    sender = caller.agent_name or body_sender
    if not sender:
        raise HTTPException(400, "sender is required")
    return sender


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    execution_slug: str | None = None
    sender: str | None = None
    receiver: str  # name of receiver program
    message: str


class RemoteExecRequest(BaseModel):
    repo: str | None = None
    execution_slug: str | None = None
    agent_name: str | None = None
    command: str


class SpawnRequest(BaseModel):
    execution_slug: str | None = None
    sender: str | None = None
    constructor_name: str
    kwargs: dict = {}


class StopAgentRequest(BaseModel):
    execution_slug: str | None = None
    agent_name: str


class GetSSHRequest(BaseModel):
    execution_slug: str | None = None
    agent_name: str


class ExposePortRequest(BaseModel):
    execution_slug: str | None = None
    agent_name: str
    port: int
    service_name: str


class GetProgramsRequest(BaseModel):
    execution_slug: str | None = None


class SubmitExecutionRequest(BaseModel):
    execution_slug: str | None = None
    pr_url: str | None = None
    summary: str | None = None




# ---------------------------------------------------------------------------
# MCP-tagged endpoints (agent-callable)
# ---------------------------------------------------------------------------


@router.post("/messages/send", tags=["mcp"], operation_id="send_message")
async def send_message(
    request: SendMessageRequest, user: CurrentUser, executions: UserExecutions, caller: CallerHeaders
):
    """Send message between programs. Use sender="driver" for external callers."""
    ex = _resolve_execution(request.execution_slug, caller, executions)
    sender = _resolve_sender(request.sender, caller)

    if sender != "driver" and sender not in ex.programs:
        raise HTTPException(404, f"Sender {sender} not found")

    if request.receiver not in ex.programs:
        raise HTTPException(404, f"Receiver {request.receiver} not found")

    await ex.send(sender, request.receiver, request.message)
    return {"status": "sent", "recipient": request.receiver}


@router.post("/spawn", tags=["mcp"], operation_id="spawn")
async def spawn(request: SpawnRequest, user: CurrentUser, executions: UserExecutions, caller: CallerHeaders):
    """Spawn a new program using a constructor from the sender's program."""
    ex = _resolve_execution(request.execution_slug, caller, executions)
    sender = _resolve_sender(request.sender, caller)

    # Validate spawner exists
    if sender not in ex.programs:
        raise HTTPException(404, f"Unknown spawner: {sender}")

    spawner = ex.programs[sender]
    if request.constructor_name not in spawner.constructors:
        raise HTTPException(404, f"Constructor {request.constructor_name} not found")

    # Spawn the new program(s) -- constructors may return multiple agents
    new_programs = await ex.spawn(sender, request.constructor_name, **request.kwargs)

    return {
        "status": "spawned",
        "programs": [{"name": p.name, "constructors": list(p.constructors.keys())} for p in new_programs],
    }


# ---------------------------------------------------------------------------
# Management endpoints (agent-callable)
# ---------------------------------------------------------------------------


@router.post("/programs", tags=["mcp"], operation_id="get_programs")
async def get_programs(
    request: GetProgramsRequest, user: CurrentUser, executions: UserExecutions, caller: CallerHeaders
):
    """Get all programs in the execution."""
    ex = _resolve_execution(request.execution_slug, caller, executions)

    programs = []
    for name, program in ex.programs.items():
        info = {
            "name": name,
            "constructors": list(program.constructors.keys()),
        }
        if program.is_agent and program.machine:
            info["instance_id"] = program.machine.instance_id
            info["bridge_id"] = program.machine.bridge_id
        programs.append(info)

    return {"programs": programs}


@router.post("/agents/stop", tags=["mcp"], operation_id="stop_agent")
async def stop_agent(request: StopAgentRequest, user: CurrentUser, executions: UserExecutions, caller: CallerHeaders):
    """Stop an agent by name."""
    ex = _resolve_execution(request.execution_slug, caller, executions)

    program = ex.programs.get(request.agent_name)
    if not program or not program.is_agent:
        raise HTTPException(404, f"Agent {request.agent_name} not found")

    await ex._disconnect_agent(request.agent_name)
    del ex.programs[request.agent_name]
    return {"status": "stopped", "agent_name": request.agent_name}


@router.post("/agents/ssh", tags=["mcp"], operation_id="get_agent_ssh")
async def get_agent_ssh(request: GetSSHRequest, user: CurrentUser, executions: UserExecutions, caller: CallerHeaders):
    """Get SSH credentials for an agent's VM."""
    ex = _resolve_execution(request.execution_slug, caller, executions)

    program = ex.programs.get(request.agent_name)
    if not program or not program.is_agent:
        raise HTTPException(404, f"Agent {request.agent_name} not found")

    if not program.machine:
        raise HTTPException(404, f"Agent {request.agent_name} has no machine")

    try:
        ssh_key = await program.machine.ssh_key()
    except Exception as e:
        logger.exception("Failed to retrieve SSH key for %s", request.agent_name)
        raise HTTPException(502, "Failed to retrieve SSH key") from e

    return {
        "host": "ssh.cloud.morph.so",
        "username": program.machine.instance_id,
        "private_key": ssh_key.private_key,
        "password": ssh_key.password,
    }


@router.post("/agents/expose-port", tags=["mcp"], operation_id="expose_port")
async def expose_port(request: ExposePortRequest, user: CurrentUser, executions: UserExecutions, caller: CallerHeaders):
    """Expose a port on an agent's VM as a public HTTPS URL."""
    ex = _resolve_execution(request.execution_slug, caller, executions)

    program = ex.programs.get(request.agent_name)
    if not program or not program.is_agent:
        raise HTTPException(404, f"Agent {request.agent_name} not found")

    if not (1 <= request.port <= 65535):
        raise HTTPException(400, f"Port must be between 1 and 65535, got {request.port}")

    if request.port == BRIDGE_PORT:
        raise HTTPException(400, f"Port {request.port} is reserved for the agent bridge")

    if not program.machine:
        raise HTTPException(404, f"Agent {request.agent_name} has no machine")

    try:
        url = await program.machine.expose_http_service(request.service_name, request.port)
    except httpx.HTTPStatusError as exc:
        status = 409 if exc.response.status_code == 409 else 502
        raise HTTPException(status, f"MorphCloud API error: {exc.response.text}") from exc

    ex.exposed_services.append(
        ExposedService(
            agent_name=request.agent_name,
            service_name=request.service_name,
            port=request.port,
            url=url,
        )
    )

    return {
        "url": url,
        "port": request.port,
        "service_name": request.service_name,
        "agent_name": request.agent_name,
    }


def _extract_pr_number(pr_url: str) -> int | None:
    """Extract the PR number from a GitHub PR URL like https://github.com/{owner}/{repo}/pull/{number}."""
    match = re.search(r"/pull/(\d+)", pr_url)
    return int(match.group(1)) if match else None


@router.post("/executions/submit", tags=["mcp"], operation_id="submit")
async def submit_execution(
    request: SubmitExecutionRequest, user: CurrentUser, executions: UserExecutions, caller: CallerHeaders
):
    """Mark an execution as submitted. Called by the agent after creating a PR."""
    ex = _resolve_execution(request.execution_slug, caller, executions)
    await ex.submit(pr_url=request.pr_url, summary=request.summary)

    # Persist to DB
    slug = ex.slug
    pr_number = _extract_pr_number(request.pr_url) if request.pr_url else None
    async with get_session() as db:
        await update_execution(
            db, ex.id, status="completed", pr_number=pr_number, pr_url=request.pr_url, summary=request.summary
        )

    logger.info(f"Execution {slug} submitted (pr_url={request.pr_url})")
    return {"status": "submitted", "execution_slug": slug}


# ---------------------------------------------------------------------------
# Driver-only tools
# ---------------------------------------------------------------------------


@router.post("/remote-exec", tags=["mcp-driver"], operation_id="remote_exec")
async def remote_exec(request: RemoteExecRequest, user: CurrentUser, executions: UserExecutions):
    """Run a command on a devbox or agent VM. Returns stdout, stderr, and exit code."""
    try:
        inst = await resolve_instance(
            user, executions, repo=request.repo, execution_slug=request.execution_slug, agent_name=request.agent_name
        )
    except InstanceNotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    try:
        result = await inst.aexec(request.command)
    except Exception:
        logger.exception("Failed to execute command on instance %s", inst.id)
        raise HTTPException(502, "Instance unavailable. It may have been stopped.")

    return {
        "stdout": result.stdout if hasattr(result, "stdout") else str(result),
        "stderr": result.stderr if hasattr(result, "stderr") else "",
        "exit_code": result.exit_code if hasattr(result, "exit_code") else 0,
    }

