from __future__ import annotations

from typing import Literal

from druids.helpers.schema import build_tool_definition


def test_build_tool_definition_uses_docstring_and_types() -> None:
    def handler(name: str, count: int = 1, enabled: bool = False):
        """Run the thing."""

    schema = build_tool_definition("run", handler)
    assert schema["name"] == "run"
    assert schema["description"] == "Run the thing."
    assert schema["parameters"]["properties"]["name"]["type"] == "string"
    assert schema["parameters"]["properties"]["count"]["type"] == "integer"
    assert schema["parameters"]["properties"]["enabled"]["type"] == "boolean"
    assert schema["parameters"]["required"] == ["name"]


def test_build_tool_definition_supports_literal() -> None:
    def handler(mode: Literal["fast", "slow"] = "fast"):
        pass

    schema = build_tool_definition("choose", handler)
    assert schema["parameters"]["properties"]["mode"]["enum"] == ["fast", "slow"]
