"""Built-in tool definitions available to every agent."""

from __future__ import annotations


BUILTIN_TOOLS = ["expose", "message", "list_agents"]

BUILTIN_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "expose",
        "description": "Expose a local port as a public HTTPS URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_name": {"type": "string", "description": "Name for the exposed service."},
                "port": {"type": "integer", "description": "Local port to expose."},
            },
            "required": ["service_name", "port"],
        },
    },
    {
        "name": "message",
        "description": "Send a message to another agent in this execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "receiver": {"type": "string", "description": "Name of the agent to message."},
                "message": {"type": "string", "description": "Message text."},
            },
            "required": ["receiver", "message"],
        },
    },
    {
        "name": "list_agents",
        "description": "List all agents in this execution.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]
