"""Log line parsing. Bottleneck: recompiles regex on every call."""

from __future__ import annotations

import json
import re


def parse_line(line: str) -> dict | None:
    """Parse a single log line. Returns None if the line is malformed."""
    line = line.strip()
    if not line:
        return None
    if not re.match(r"^\s*\{.*\}\s*$", line):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    endpoint = data.get("endpoint", "")
    endpoint = re.sub(r"/\d+", "/:id", endpoint)
    data["endpoint"] = endpoint
    return data
