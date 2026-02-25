"""Message - communication between programs."""

from dataclasses import dataclass


@dataclass
class Message:
    sender: str  # program name
    receiver: str  # program name
    content: str
