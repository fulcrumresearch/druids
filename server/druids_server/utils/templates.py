"""Template substitution helpers."""

from __future__ import annotations

from string import Template
from typing import Any


def resolve_secret_refs(config: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    """Resolve $VAR_NAME references in a config dict from a secrets dict.

    Walks the dict and substitutes $VAR_NAME patterns in string values using
    string.Template.safe_substitute. This lets programs define config
    declaratively with secret references like {"url": "$SLACK_MCP_URL",
    "headers": {"Authorization": "Bearer $SLACK_BOT_TOKEN"}}.
    """

    def _resolve(value: Any) -> Any:
        if isinstance(value, str):
            return Template(value).safe_substitute(secrets)
        if isinstance(value, dict):
            return {k: _resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_resolve(v) for v in value]
        return value

    return {name: _resolve(cfg) for name, cfg in config.items()}
