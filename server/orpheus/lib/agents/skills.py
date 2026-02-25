"""Skill file discovery and upload to agent VMs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from orpheus.paths import AGENT_SKILLS_DIR


if TYPE_CHECKING:
    from orpheus.lib.machine import Machine

logger = logging.getLogger(__name__)


async def upload_skills(machine: Machine, remote_skills_dir: str) -> None:
    """Upload agent skill files from server/agent-skills/ to a VM instance.

    Reads each SKILL.md from server/agent-skills/<name>/ and writes it to
    <remote_skills_dir>/<name>/SKILL.md on the VM.
    """
    if not AGENT_SKILLS_DIR.is_dir():
        logger.warning(f"Agent skills directory not found at {AGENT_SKILLS_DIR}, skipping skill upload")
        return

    for skill_path in sorted(AGENT_SKILLS_DIR.iterdir()):
        if not skill_path.is_dir():
            continue
        skill_file = skill_path / "SKILL.md"
        if not skill_file.exists():
            continue

        skill_name = skill_path.name
        remote_dir = f"{remote_skills_dir}/{skill_name}"

        await machine.exec(f"mkdir -p {remote_dir}", user="agent")
        try:
            await machine.upload(str(skill_file), f"{remote_dir}/SKILL.md")
            logger.info(f"Uploaded skill '{skill_name}' to {remote_dir}/SKILL.md")
        except Exception:
            logger.exception(f"Failed to upload skill '{skill_name}'")
