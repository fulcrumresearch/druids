"""Built-in tool definitions available to every agent."""

from __future__ import annotations


BUILTIN_TOOLS = ["expose", "message", "list_agents", "send_file", "download_file"]

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
    {
        "name": "send_file",
        "description": (
            "Send a file from your filesystem to another connected agent. "
            "The file is read from your sandbox and written to the receiver's sandbox."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "receiver": {"type": "string", "description": "Name of the agent to send the file to."},
                "path": {"type": "string", "description": "Path to the file on your filesystem."},
                "dest_path": {
                    "type": "string",
                    "description": "Destination path on the receiver's filesystem. Defaults to the same path.",
                },
            },
            "required": ["receiver", "path"],
        },
    },
    {
        "name": "download_file",
        "description": (
            "Download a file from another connected agent's filesystem. "
            "The file is read from the sender's sandbox and written to your sandbox."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "Name of the agent to download from."},
                "path": {"type": "string", "description": "Path to the file on the sender's filesystem."},
                "dest_path": {
                    "type": "string",
                    "description": "Where to save the file on your filesystem. Defaults to the same path.",
                },
            },
            "required": ["sender", "path"],
        },
    },
]
