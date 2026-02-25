"""Collaborative program with terminal-only agents.

All agents (orchestrator + sub-agents) are restricted to bash + orpheus MCP tools.
No file editing tools (Read, Write, Edit, Glob, Grep for claude; apply_patch for codex).

For claude agents: --allowedTools explicitly lists Bash + the needed MCP tools.
For codex agents: config.toml [features] disables apply_patch, web_search, view_image.
"""

import base64
import json

from orpheus.config import settings
from orpheus.lib import ACPConfig, Agent
from programs.claude import CLAUDE_WRAPPER


def create_task_program(
    spec: str,
    snapshot_id: str | None = None,
    repo_name: str | None = None,
    working_dir: str | None = None,
    container_name: str | None = None,
    model: str | None = None,
) -> Agent:
    """Create a collaborative program with terminal-only agents."""
    if not working_dir:
        working_dir = f"/home/agent/{repo_name}" if repo_name and snapshot_id else "/home/agent"

    claude_model = model or "claude-opus-4-6"
    codex_model = "gpt-5.2-codex"

    def make_claude(name: str) -> Agent:
        env = {
            "ANTHROPIC_API_KEY": settings.anthropic_api_key.get_secret_value(),
            "ANTHROPIC_MODEL": claude_model,
            "CLAUDE_SETTINGS_JSON": json.dumps({"model": claude_model}),
        }
        return Agent(
            name=name,
            config=ACPConfig(
                command="/bin/sh",
                command_args=["-c", CLAUDE_WRAPPER],
                env=env,
                working_directory=working_dir,
                container_name=container_name,
            ),
            instance_type="branch",
            init_prompt=f"""You are '{name}', a coding agent collaborating with a partner agent named 'codex-1'.

You are both working on the same task on separate machines. You each have a full copy of the codebase. You can talk to each other using send_message.

You only have access to a bash terminal. Use it for everything: reading files (cat, head, tail), writing files (cat with heredoc, tee), searching (grep, find), editing (sed, awk), compiling, and testing.

Collaborate naturally. Share what you're planning before you start coding. If you get stuck, ask your partner. If you find something tricky, give them a heads up. Share code snippets and diffs in your messages. Review each other's work. You're a team.

When you're done, send a message to 'orchestrator' summarizing ALL of your changes. Include the COMPLETE content of every file you created or modified so the orchestrator can apply them. Use code blocks with the full file path as the label.

MCP tools: send_message, get_programs""",
        )

    def make_codex(name: str) -> Agent:
        # codex config: set model, disable all non-shell tools
        # [features] disables apply_patch, web_search, view_image
        # [tools] experimental_supported_tools = [] disables read_file, list_dir, grep_files
        config_content = (
            f'model = "{codex_model}"\n'
            'model_reasoning_effort = "high"\n'
            "\n"
            "[features]\n"
            "apply_patch_freeform = false\n"
            "view_image_tool = false\n"
            "web_search_request = false\n"
            "\n"
            "[tools]\n"
            "experimental_supported_tools = []\n"
        )
        b64 = base64.b64encode(config_content.encode()).decode()
        wrapper_cmd = f"mkdir -p /root/.codex && echo {b64} | base64 -d > /root/.codex/config.toml && exec codex-acp"
        return Agent(
            name=name,
            config=ACPConfig(
                command="/bin/sh",
                command_args=["-c", wrapper_cmd],
                env={"OPENAI_API_KEY": settings.openai_api_key.get_secret_value()},
                working_directory=working_dir,
                container_name=container_name,
                auth_method="openai-api-key",
            ),
            instance_type="branch",
            init_prompt=f"""You are '{name}', a coding agent collaborating with a partner agent named 'claude-1'.

You are both working on the same task on separate machines. You each have a full copy of the codebase. You can talk to each other using send_message.

You only have access to a shell terminal. Use it for everything: reading files (cat, head, tail), writing files (cat with heredoc, tee), searching (grep, find), editing (sed, awk), compiling, and testing.

Collaborate naturally. Share what you're planning before you start coding. If you get stuck, ask your partner. If you find something tricky, give them a heads up. Share code snippets and diffs in your messages. Review each other's work. You're a team.

When you're done, send a message to 'orchestrator' summarizing ALL of your changes. Include the COMPLETE content of every file you created or modified so the orchestrator can apply them. Use code blocks with the full file path as the label.

MCP tools: send_message, get_programs""",
        )

    prompt = f"""You are an orchestrator coordinating two collaborating agents. Your task:

---
{spec}
---

You only have bash and orchestration tools. Use bash for all file operations on your machine.

Strategy:
1. Spawn both agents:
   - spawn(constructor_name="claude", kwargs={{"name": "claude-1"}})
   - spawn(constructor_name="codex", kwargs={{"name": "codex-1"}})

2. Send each agent the task via send_message. Tell them to start by discussing their approach with each other before diving into code.

3. Let them work. They will message each other to collaborate. Monitor their progress via get_programs.

4. When both agents report back via send_message, review their solutions based on the code and diffs they share in their messages.

5. Pick the better solution (or combine the best parts of both).

6. CRITICAL: Apply the chosen solution to YOUR machine. The agents worked on separate copies. You must write all the files yourself using bash (cat with heredoc, tee, sed, etc.) based on the code they shared in their messages.

7. Verify the solution works by running any relevant commands on your machine.

All communication happens through send_message. Agents share code and diffs as text in messages. You cannot access their files directly - you must recreate them locally using bash.

MCP tools: spawn, send_message, get_programs, stop_agent, finish"""

    env = {
        "ANTHROPIC_API_KEY": settings.anthropic_api_key.get_secret_value(),
        "ANTHROPIC_MODEL": claude_model,
        "CLAUDE_SETTINGS_JSON": json.dumps({"model": claude_model}),
    }

    return Agent(
        name="orchestrator",
        config=ACPConfig(
            command="/bin/sh",
            command_args=["-c", CLAUDE_WRAPPER],
            env=env,
            working_directory=working_dir,
            container_name=container_name,
        ),
        instance_type="sandbox" if snapshot_id else None,
        snapshot=snapshot_id,
        constructors={"claude": make_claude, "codex": make_codex},
        init_prompt=prompt,
    )
