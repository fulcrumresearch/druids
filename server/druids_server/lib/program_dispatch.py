"""Tool schema extraction for program handlers.

Pure functions for extracting MCP-compatible tool schemas from Python
function signatures. Used by Execution for tool listing.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


_TYPE_TO_JSON: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}


def _annotation_to_json_type(annotation: Any) -> str:
    """Convert a Python type annotation to a JSON Schema type string."""
    if annotation is inspect.Parameter.empty:
        return "string"
    name = annotation.__name__ if isinstance(annotation, type) else str(annotation)
    return _TYPE_TO_JSON.get(name, "string")


def extract_tool_schema(tool_name: str, handler: Callable) -> dict:
    """Extract an MCP-compatible tool schema from a handler function."""
    sig = inspect.signature(handler)
    description = inspect.getdoc(handler) or ""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "caller"):
            continue
        json_type = _annotation_to_json_type(param.annotation)
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "name": tool_name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def extract_agent_tool_schemas(handlers: dict[str, Callable]) -> list[dict]:
    """Extract schemas for all tool handlers in a name -> handler mapping."""
    return [extract_tool_schema(name, handler) for name, handler in handlers.items()]
