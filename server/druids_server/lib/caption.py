"""Caption summarizer for the graph view.

Watches agent events and produces short captions. Tool calls produce immediate
captions from the tool name. Response text accumulates and gets summarized by
Haiku on a debounce timer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import anthropic


logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SUMMARIZE_PROMPT = (
    "You are labeling a node in a live visualization of coding agents. "
    "Summarize what this agent is doing in under 8 words. "
    "Only output the summary, nothing else.\n\n"
    "Agent text:\n{text}"
)
FLUSH_DELAY_SECONDS = 2.0
FLUSH_CHAR_THRESHOLD = 200
MAX_BUFFER_CHARS = 2000


class CaptionSummarizer:
    """Produces short captions for agent nodes in the graph view.

    One instance per Execution. Tool calls emit captions immediately.
    Response text accumulates in a per-agent buffer and gets summarized
    by Haiku after a debounce delay or when a threshold is reached.
    """

    def __init__(self, emit_fn: Callable[[str, dict[str, Any] | None], None]) -> None:
        self._emit = emit_fn
        self._buffers: dict[str, str] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Lazy-init the Anthropic client using the server's configured API key."""
        if self._client is None:
            from druids_server.config import settings

            self._client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
            )
        return self._client

    def tool_caption(self, agent_name: str, tool_name: str, args: dict[str, Any]) -> None:
        """Emit a caption from a tool call. No LLM needed."""
        # Skip MCP-prefixed tool calls -- the native `druids:` version follows
        # with the same args and avoids duplicate captions.
        if tool_name.startswith("mcp__"):
            return

        # Strip "druids:" prefix for cleaner display
        short_name = tool_name.removeprefix("druids:")

        # Flush any pending response text first
        if self._buffers.get(agent_name):
            self._cancel_timer(agent_name)
            asyncio.get_event_loop().create_task(self._flush(agent_name))

        # Special handling for inter-agent message tool
        if short_name == "message":
            receiver = args.get("receiver", "")
            caption = f"→ {receiver}" if receiver else "message"
            self._emit("caption", {"agent": agent_name, "text": caption})
            return

        # Pick the most informative arg value for the label
        key_arg = _pick_key_arg(short_name, args)
        if key_arg:
            caption = f"{short_name}: {key_arg[:50]}"
        else:
            caption = short_name

        self._emit("caption", {"agent": agent_name, "text": caption})

    def accumulate(self, agent_name: str, text: str) -> None:
        """Append response text to the agent's buffer and schedule a flush."""
        buf = self._buffers.get(agent_name, "")
        buf += text
        logger.debug("caption accumulate '%s': +%d chars, total=%d", agent_name, len(text), len(buf))
        # Cap buffer size to avoid unbounded growth
        if len(buf) > MAX_BUFFER_CHARS:
            buf = buf[-MAX_BUFFER_CHARS:]
        self._buffers[agent_name] = buf

        if len(buf) >= FLUSH_CHAR_THRESHOLD:
            self._cancel_timer(agent_name)
            asyncio.get_event_loop().create_task(self._flush(agent_name))
        else:
            self._schedule_flush(agent_name)

    async def _flush(self, agent_name: str) -> None:
        """Summarize the buffer with Haiku and emit the caption."""
        self._cancel_timer(agent_name)
        buf = self._buffers.pop(agent_name, "")
        if not buf.strip():
            return

        try:
            client = self._get_client()
            resp = await client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=25,
                messages=[{"role": "user", "content": SUMMARIZE_PROMPT.format(text=buf.strip())}],
            )
            caption = resp.content[0].text.strip() if resp.content else ""
            if caption:
                self._emit("caption", {"agent": agent_name, "text": caption})
        except Exception:
            logger.warning("Caption summarize failed for '%s'", agent_name, exc_info=True)

    def _schedule_flush(self, agent_name: str) -> None:
        """Schedule a debounced flush."""
        self._cancel_timer(agent_name)
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            FLUSH_DELAY_SECONDS,
            lambda: loop.create_task(self._flush(agent_name)),
        )
        self._timers[agent_name] = handle

    def _cancel_timer(self, agent_name: str) -> None:
        """Cancel a pending flush timer."""
        handle = self._timers.pop(agent_name, None)
        if handle:
            handle.cancel()


def _pick_key_arg(tool_name: str, args: dict[str, Any]) -> str:
    """Pick the most informative argument value for a tool caption."""
    # Common tool patterns
    lower = tool_name.lower()
    if "bash" in lower or "shell" in lower:
        return str(args.get("command", args.get("cmd", "")))
    if "read" in lower:
        return str(args.get("file_path", args.get("path", "")))
    if "edit" in lower or "write" in lower:
        return str(args.get("file_path", args.get("path", "")))
    if "glob" in lower:
        return str(args.get("pattern", ""))
    if "grep" in lower or "search" in lower:
        return str(args.get("pattern", args.get("query", "")))
    # Fallback: first string arg value
    for v in args.values():
        if isinstance(v, str) and v:
            return v
    return ""
