"""Claude program for orpheus exec.

Creates a Claude agent that implements a specification.
"""

from orpheus.lib.agents.base import Agent
from orpheus.lib.agents.claude import ClaudeAgent

from .program_utils import AGENT_SYSTEM_PROMPT, GIT_SYSTEM_PROMPT, ROOT_AGENT_SYSTEM_PROMPT, VERIFICATION_PROMPT


def create_task_program(spec: str, repo_name: str) -> Agent:
    """Create a task program that implements the given spec."""

    prompt = f"""You are a software engineering agent. Implement the following specification:

---
{spec}
---

Read `.orpheus/SETUP.md` for project-specific build and test instructions. \
Then work through the task step by step."""

    return ClaudeAgent(
        name="claude",
        working_directory=f"/home/agent/{repo_name}",
        user_prompt=prompt,
        system_prompt=AGENT_SYSTEM_PROMPT
        + "\n\n"
        + GIT_SYSTEM_PROMPT
        + "\n\n"
        + ROOT_AGENT_SYSTEM_PROMPT
        + "\n\n"
        + VERIFICATION_PROMPT,
    )
