"""Codex program for orpheus exec.

Creates an OpenAI Codex agent that implements a specification.
"""

from orpheus.lib.agents.base import Agent
from orpheus.lib.agents.codex import CodexAgent

from .program_utils import AGENT_SYSTEM_PROMPT, GIT_SYSTEM_PROMPT, ROOT_AGENT_SYSTEM_PROMPT, VERIFICATION_PROMPT


def create_task_program(spec: str, repo_name: str | None = None) -> Agent:
    """Create a Codex agent that implements the given spec."""

    prompt = f"""You are a software engineering agent. Implement the following specification:

---
{spec}
---

Read `.orpheus/SETUP.md` for project-specific build and test instructions. \
Then work through the task step by step."""

    working_dir = f"/home/agent/{repo_name}" if repo_name else "/home/agent"

    return CodexAgent(
        name="codex",
        working_directory=working_dir,
        user_prompt=prompt,
        system_prompt=AGENT_SYSTEM_PROMPT
        + "\n\n"
        + GIT_SYSTEM_PROMPT
        + "\n\n"
        + ROOT_AGENT_SYSTEM_PROMPT
        + "\n\n"
        + VERIFICATION_PROMPT,
    )
