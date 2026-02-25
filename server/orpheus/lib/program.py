"""Program - base class for executable units."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Program:
    """A program that can execute."""

    name: str
    constructors: dict[str, Callable[..., "Program"]] = field(default_factory=dict)

    @property
    def is_agent(self) -> bool:
        """Whether this program is an Agent."""
        return False

    async def exec(self, *args) -> list["Program"]:
        """Execute this program. Returns new programs to add to execution."""
        return []
