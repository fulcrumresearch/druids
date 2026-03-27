"""Trace formatting helpers for execution activity endpoints."""

from __future__ import annotations


MAX_TOOL_TEXT = 2000
MAX_PROMPT_TEXT = 500
MAX_RESPONSE_TEXT = 500


def truncate_text(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit]


def truncate_payload(value: object, limit: int) -> object:
    if isinstance(value, str):
        return truncate_text(value, limit)
    if isinstance(value, list):
        return [truncate_payload(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: truncate_payload(item, limit) for key, item in value.items()}
    return value


def extract_tool_result_meta(result: object) -> tuple[str | None, int | None, object | None]:
    if isinstance(result, dict):
        output = result.get("aggregated_output")
        exit_code = result.get("exit_code")
        duration = result.get("duration_secs", result.get("duration"))
        return output, exit_code, duration
    if isinstance(result, str):
        return result, None, None
    return None, None, None


def merge_response_chunks(events: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for event in events:
        if (
            event.get("type") == "response_chunk"
            and merged
            and merged[-1].get("type") == "response_chunk"
            and merged[-1].get("agent") == event.get("agent")
        ):
            merged[-1]["text"] = f"{merged[-1].get('text', '')}{event.get('text', '')}"
            if event.get("ts"):
                merged[-1]["ts"] = event["ts"]
            continue
        merged.append(event)
    return merged


def normalize_event(event: dict, compact: bool) -> dict:
    event_type = event.get("type")

    if event_type == "tool_use":
        if compact:
            return {
                "type": event_type,
                "agent": event.get("agent"),
                "tool": event.get("tool"),
                "ts": event.get("ts"),
            }
        trimmed = dict(event)
        if "params" in trimmed:
            trimmed["params"] = truncate_payload(trimmed.get("params"), MAX_TOOL_TEXT)
        return trimmed

    if event_type == "tool_result":
        output, exit_code, duration = extract_tool_result_meta(event.get("result"))
        if compact:
            compact_event = {
                "type": event_type,
                "agent": event.get("agent"),
                "tool": event.get("tool"),
                "ts": event.get("ts"),
            }
            if exit_code is not None:
                compact_event["exit_code"] = exit_code
            if duration is not None:
                compact_event["duration_secs"] = duration
            return compact_event
        trimmed = dict(event)
        trimmed["result"] = truncate_text(output, MAX_TOOL_TEXT)
        if exit_code is not None:
            trimmed["exit_code"] = exit_code
        if duration is not None:
            trimmed["duration_secs"] = duration
        return trimmed

    if event_type == "response_chunk":
        trimmed = dict(event)
        trimmed["text"] = truncate_text(trimmed.get("text", ""), MAX_RESPONSE_TEXT)
        return trimmed

    if event_type == "prompt":
        trimmed = dict(event)
        trimmed["text"] = truncate_text(trimmed.get("text", ""), MAX_PROMPT_TEXT)
        return trimmed

    if event_type == "topology":
        return {
            "type": event_type,
            "agents": event.get("agents", []),
            "edges": event.get("edges", []),
            "ts": event.get("ts"),
        }

    if event_type == "client_event":
        return {"type": event_type, "event": event.get("event"), "data": event.get("data", {}), "ts": event.get("ts")}

    if event_type == "error":
        return {
            "type": event_type,
            "agent": event.get("agent"),
            "error": truncate_text(event.get("error", ""), MAX_TOOL_TEXT),
            "ts": event.get("ts"),
        }

    return event
