from __future__ import annotations

from pathlib import Path


def extension_source() -> str:
    path = Path(__file__).with_name("extension.ts")
    if not path.exists():
        raise FileNotFoundError(f"Bundled extension not found: {path}")
    return path.read_text()
