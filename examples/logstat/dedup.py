"""Deduplication. Bottleneck: O(n^2) nested-loop scan."""

from __future__ import annotations


def deduplicate(entries: list[dict]) -> list[dict]:
    """Remove duplicate request entries (same request_id)."""
    unique = []
    for entry in entries:
        is_dup = False
        for existing in unique:
            if entry.get("request_id") == existing.get("request_id"):
                is_dup = True
                break
        if not is_dup:
            unique.append(entry)
    return unique
