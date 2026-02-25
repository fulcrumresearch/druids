"""Program discovery."""

from __future__ import annotations

import importlib

from orpheus.paths import PROGRAMS_DIR


def discover_programs() -> list[tuple[str, callable]]:
    """Discover all programs in programs/ with create_task_program function."""
    results = []

    for py_file in PROGRAMS_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        module_name = f"programs.{py_file.stem}"
        module = importlib.import_module(module_name)

        if hasattr(module, "create_task_program"):
            results.append((py_file.stem, module.create_task_program))

    return results
