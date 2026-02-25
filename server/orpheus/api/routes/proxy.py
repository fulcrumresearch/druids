"""Anthropic API proxy.

Agents authenticate with a forwarding token (JWT) instead of the raw API key.
The proxy validates the token, checks the execution registry, swaps in the
real key, and streams the response back. Token usage is extracted from each
response and accumulated on the ExecutionRecord.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from orpheus.api.deps import get_executions_registry
from orpheus.config import settings
from orpheus.lib.forwarding_tokens import validate_token
from orpheus.db.models.execution import increment_usage
from orpheus.db.session import get_session


logger = logging.getLogger(__name__)

router = APIRouter()

UPSTREAM_BASE = "https://api.anthropic.com"

# Only paths under these prefixes are forwarded. Claude Code uses
# v1/messages (create) and v1/messages/count_tokens. Everything else
# (models, completions, batches, files, admin) is blocked.
_ALLOWED_PATH_PREFIXES = ("v1/messages",)

# Module-level client: pools TCP connections across requests.
_client = httpx.AsyncClient(timeout=None)

# Headers that must not be forwarded between hops.
_STRIP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


def _extract_usage(body: bytes) -> dict | None:
    """Extract token usage from a completed Anthropic response.

    For streaming (SSE): scans for the last `message_delta` event which
    contains cumulative totals.
    For non-streaming: parses the JSON body directly.
    """
    text = body.decode("utf-8", errors="replace")
    usage = None

    # Streaming: look for message_delta events (last one has final totals)
    if "event: message_delta" in text:
        for line in text.split("\n"):
            if not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[6:])
            except (json.JSONDecodeError, ValueError):
                continue
            if data.get("type") == "message_delta" and "usage" in data:
                usage = data["usage"]
        return usage

    # Non-streaming: top-level JSON with usage field
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "usage" in data:
            return data["usage"]
    except (json.JSONDecodeError, ValueError):
        pass

    return None


async def _record_usage(execution_slug: str, user_id: str, usage: dict) -> None:
    """Write usage to DB and enforce token budget.

    After incrementing, checks cumulative output tokens against the configured
    cap. If exceeded, stops the execution so subsequent requests are rejected.
    """
    try:
        async with get_session() as db:
            total_output = await increment_usage(
                db,
                slug=execution_slug,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
                cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            )
    except Exception:
        logger.warning("Failed to record usage for %s", execution_slug, exc_info=True)
        return

    cap = settings.max_output_tokens_per_execution
    if total_output >= cap:
        logger.warning(
            "Token budget exceeded for execution=%s total_output=%d cap=%d -- stopping execution",
            execution_slug,
            total_output,
            cap,
        )
        registry = get_executions_registry()
        user_execs = registry.get(user_id)
        if user_execs and execution_slug in user_execs:
            execution = user_execs.pop(execution_slug)
            await execution.stop("token_budget_exceeded")


@router.api_route(
    "/proxy/anthropic/{path:path}",
    methods=["POST"],
    tags=["proxy"],
    include_in_schema=False,
)
async def proxy_anthropic(path: str, request: Request):
    # Only allow specific upstream endpoints.
    if not path.startswith(_ALLOWED_PATH_PREFIXES):
        raise HTTPException(status_code=403, detail="Path not allowed")

    # Extract token from x-api-key or Authorization header.
    token = request.headers.get("x-api-key") or ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    token = token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing forwarding token")

    # Validate JWT signature.
    try:
        claims = validate_token(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    # Check the token is tied to a live execution.
    registry = get_executions_registry()
    user_execs = registry.get(claims["sub"])
    if not user_execs or claims["execution_slug"] not in user_execs:
        raise HTTPException(status_code=403, detail="Execution not active")
    execution = user_execs[claims["execution_slug"]]
    if claims["agent_name"] not in execution.programs:
        raise HTTPException(status_code=403, detail="Agent not active")

    logger.info(
        "proxy_anthropic request user_id=%s execution=%s agent=%s method=%s path=%s",
        claims["sub"],
        claims["execution_slug"],
        claims["agent_name"],
        request.method,
        path,
    )

    # Build upstream request: strip hop-by-hop and auth headers, inject real key.
    upstream_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _STRIP_HEADERS and k.lower() not in {"authorization", "x-api-key"}
    }
    upstream_headers["x-api-key"] = settings.anthropic_api_key.get_secret_value()

    upstream_url = httpx.URL(f"{UPSTREAM_BASE}/{path}")
    if request.url.query:
        upstream_url = upstream_url.copy_with(query=request.url.query.encode())

    body = await request.body()

    try:
        upstream_request = _client.build_request(
            request.method,
            upstream_url,
            headers=upstream_headers,
            content=body,
        )
        upstream_response = await _client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        logger.warning("proxy_anthropic upstream_error execution=%s error=%r", claims["execution_slug"], exc)
        raise HTTPException(status_code=502, detail="Upstream request failed") from exc

    async def stream():
        buf = bytearray()
        try:
            async for chunk in upstream_response.aiter_bytes():
                buf.extend(chunk)
                yield chunk
        finally:
            await upstream_response.aclose()
            logger.info(
                "proxy_anthropic response user_id=%s execution=%s agent=%s status=%s",
                claims["sub"],
                claims["execution_slug"],
                claims["agent_name"],
                upstream_response.status_code,
            )
            if upstream_response.status_code == 200:
                usage = _extract_usage(bytes(buf))
                if usage:
                    asyncio.create_task(_record_usage(claims["execution_slug"], claims["sub"], usage))

    response_headers = {k: v for k, v in upstream_response.headers.items() if k.lower() not in _STRIP_HEADERS}
    # httpx decompresses via aiter_bytes(), so drop content-encoding.
    response_headers.pop("content-encoding", None)

    return StreamingResponse(
        stream(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
