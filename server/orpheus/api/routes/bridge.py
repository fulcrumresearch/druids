"""Reverse bridge relay endpoints.

These endpoints are called by bridge processes running on agent machines.
They are authenticated with a per-bridge bearer token minted by the server.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from orpheus.lib.connection import bridge_relay_hub


router = APIRouter(prefix="/bridge", tags=["bridge"])


class PushRequest(BaseModel):
    messages: list[str] = Field(default_factory=list)


class PullRequest(BaseModel):
    max_items: int = 128
    timeout_seconds: float = 20.0


class PullResponse(BaseModel):
    messages: list[str]


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    return authorization[7:]


@router.post("/{bridge_id}/push")
async def push_output(
    bridge_id: str,
    request: PushRequest,
    authorization: str | None = Header(default=None),
):
    token = _extract_bearer(authorization)
    if not bridge_relay_hub.is_valid_token(bridge_id, token):
        raise HTTPException(401, "Invalid bridge credentials")

    await bridge_relay_hub.mark_connected(bridge_id)
    await bridge_relay_hub.touch(bridge_id)
    await bridge_relay_hub.push_output(bridge_id, request.messages)
    return {"status": "ok", "count": len(request.messages)}


@router.post("/{bridge_id}/pull", response_model=PullResponse)
async def pull_input(
    bridge_id: str,
    request: PullRequest,
    authorization: str | None = Header(default=None),
):
    token = _extract_bearer(authorization)
    if not bridge_relay_hub.is_valid_token(bridge_id, token):
        raise HTTPException(401, "Invalid bridge credentials")

    await bridge_relay_hub.mark_connected(bridge_id)
    await bridge_relay_hub.touch(bridge_id)
    max_items = max(1, min(request.max_items, 1024))
    timeout_seconds = max(0.0, min(request.timeout_seconds, 55.0))
    messages = await bridge_relay_hub.pull_input(bridge_id, max_items=max_items, timeout_seconds=timeout_seconds)
    return PullResponse(messages=messages)
