"""Tests for YAML program spec parsing, validation, and agent construction."""

from unittest.mock import MagicMock, patch

import pytest
from orpheus.lib.agents.base import Agent
from orpheus.lib.agents.claude import ClaudeAgent
from orpheus.lib.agents.codex import CodexAgent
from orpheus.lib.spec import (
    BUILTIN_HARNESSES,
    RESERVED_ARG_NAMES,
    ProgramSpec,
    build_root_agent,
    parse_program_spec,
    resolve_harness,
)


# ---------------------------------------------------------------------------
# TestParseSpec
# ---------------------------------------------------------------------------


class TestParseSpec:
    def test_minimal_spec(self):
        """Simplest valid spec: just a root with a name."""
        yaml_str = """
root:
  name: agent
"""
        spec = parse_program_spec(yaml_str)
        assert spec.root.name == "agent"
        assert spec.params is None
        assert spec.definitions is None

    def test_full_spec(self):
        """Spec with params, definitions, and root."""
        yaml_str = """
params:
  model: claude-opus-4-6

definitions:
  worker:
    args:
      task: string
    template:
      - name: worker-$task
        model: $model
        user_prompt: Do $task

root:
  name: lead
  model: $model
  user_prompt: Coordinate work.
  constructors:
    - worker
"""
        spec = parse_program_spec(yaml_str)
        assert spec.params == {"model": "claude-opus-4-6"}
        assert "worker" in spec.definitions
        assert spec.definitions["worker"].args == {"task": "string"}
        assert len(spec.definitions["worker"].template) == 1
        assert spec.root.constructors == ["worker"]

    def test_invalid_yaml_rejected(self):
        """Non-YAML content raises an error."""
        with pytest.raises(Exception):
            parse_program_spec("not: [valid: yaml: {{")

    def test_non_mapping_rejected(self):
        """YAML that parses to a list instead of a dict is rejected."""
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            parse_program_spec("- item1\n- item2")

    def test_unknown_fields_rejected(self):
        """extra='forbid' rejects unknown top-level fields."""
        yaml_str = """
root:
  name: agent
extra_field: bad
"""
        with pytest.raises(Exception):
            parse_program_spec(yaml_str)

    def test_unknown_agent_fields_rejected(self):
        """extra='forbid' rejects unknown fields on agent nodes."""
        yaml_str = """
root:
  name: agent
  unknown_field: bad
"""
        with pytest.raises(Exception):
            parse_program_spec(yaml_str)

    def test_missing_root_rejected(self):
        """Spec without root field is rejected."""
        yaml_str = """
params:
  model: claude-opus-4-6
"""
        with pytest.raises(Exception):
            parse_program_spec(yaml_str)

    def test_param_overrides_merged(self):
        """param_overrides are merged into spec.params, overriding defaults."""
        yaml_str = """
params:
  model: claude-opus-4-6
  extra: keep

root:
  name: agent
"""
        spec = parse_program_spec(yaml_str, param_overrides={"model": "gemini-3-pro-preview"})
        assert spec.params["model"] == "gemini-3-pro-preview"
        assert spec.params["extra"] == "keep"

    def test_param_overrides_create_params_when_none(self):
        """param_overrides work even when spec has no params block."""
        yaml_str = """
root:
  name: agent
"""
        spec = parse_program_spec(yaml_str, param_overrides={"model": "claude-opus-4-6"})
        assert spec.params == {"model": "claude-opus-4-6"}

    def test_constructor_references_validated(self):
        """Constructor names must reference existing definitions."""
        yaml_str = """
root:
  name: agent
  constructors:
    - nonexistent
"""
        with pytest.raises(ValueError, match="references undefined definition"):
            parse_program_spec(yaml_str)

    def test_reserved_arg_names_rejected(self):
        """Reserved names cannot be used as definition arg names."""
        for reserved_name in RESERVED_ARG_NAMES:
            yaml_str = f"""
definitions:
  bad:
    args:
      {reserved_name}: string
    template:
      - name: agent

root:
  name: lead
  constructors:
    - bad
"""
            with pytest.raises(ValueError, match="reserved arg name"):
                parse_program_spec(yaml_str)


# ---------------------------------------------------------------------------
# TestResolveHarness
# ---------------------------------------------------------------------------


class TestResolveHarness:
    def test_claude_model_prefix(self):
        """claude- prefix resolves to claude-code harness."""
        name, spec, model = resolve_harness("claude-sonnet-4-6", None)
        assert name == "claude-code"
        assert model == "claude-sonnet-4-6"

    def test_codex_model_prefix(self):
        """gpt- prefix resolves to codex harness."""
        name, spec, model = resolve_harness("gpt-5.2-codex", None)
        assert name == "codex"
        assert model == "gpt-5.2-codex"

    def test_o1_model_prefix(self):
        """o1- prefix resolves to codex harness."""
        name, spec, model = resolve_harness("o1-preview", None)
        assert name == "codex"
        assert model == "o1-preview"

    def test_gemini_model_prefix(self):
        """gemini- prefix resolves to gemini harness."""
        name, spec, model = resolve_harness("gemini-3-pro-preview", None)
        assert name == "gemini"
        assert model == "gemini-3-pro-preview"

    def test_unknown_model_prefix_errors(self):
        """Unknown model prefix raises ValueError."""
        with pytest.raises(ValueError, match="No harness found"):
            resolve_harness("llama-3-70b", None)

    def test_explicit_harness_overrides_prefix(self):
        """Explicit harness is used even if model matches another harness."""
        name, spec, model = resolve_harness("claude-opus-4-6", "codex")
        assert name == "codex"
        assert model == "claude-opus-4-6"

    def test_explicit_harness_with_default_model(self):
        """When harness specified without model, use harness default."""
        name, spec, model = resolve_harness(None, "codex")
        assert name == "codex"
        assert model == "gpt-5.2-codex"

    def test_unknown_harness_errors(self):
        """Unknown harness name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown harness"):
            resolve_harness(None, "nonexistent")

    def test_defaults_to_claude_code(self):
        """No model or harness defaults to claude-code with default model."""
        name, spec, model = resolve_harness(None, None)
        assert name == "claude-code"
        assert model == "claude-opus-4-6"

    def test_custom_harness_lookup(self):
        """Custom harnesses are checked alongside built-in ones."""
        from orpheus.lib.spec import HarnessModelsSpec, HarnessSpec

        custom = {
            "aider": HarnessSpec(
                command="aider-acp",
                models=HarnessModelsSpec(prefix=["aider-"], default="aider-v1"),
            )
        }
        name, spec, model = resolve_harness(None, "aider", custom)
        assert name == "aider"
        assert model == "aider-v1"


# ---------------------------------------------------------------------------
# TestBuildRootAgent
# ---------------------------------------------------------------------------


class TestBuildRootAgent:
    def test_simple_claude_agent(self):
        """Simple spec with claude model produces ClaudeAgent."""
        yaml_str = """
root:
  name: agent
  model: claude-opus-4-6
  user_prompt: Do the task.
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert isinstance(agent, ClaudeAgent)
        assert agent.name == "agent"
        assert agent.model == "claude-opus-4-6"
        assert agent.user_prompt == "Do the task."

    @patch("orpheus.lib.agents.codex.settings")
    def test_codex_model_produces_codex_agent(self, mock_settings):
        """Codex model prefix produces CodexAgent."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = mock_key

        yaml_str = """
root:
  name: agent
  model: gpt-5.2-codex
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert isinstance(agent, CodexAgent)

    def test_default_model_produces_claude_agent(self):
        """No model specified defaults to claude-code."""
        yaml_str = """
root:
  name: agent
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert isinstance(agent, ClaudeAgent)
        assert agent.model == "claude-opus-4-6"

    def test_root_agent_devbox_instance_source(self):
        """Root agent gets instance_source='devbox'."""
        yaml_str = """
root:
  name: agent
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert agent.instance_source == "devbox"

    def test_working_directory_includes_repo_name(self):
        """Working directory is /home/agent/{repo_name}."""
        yaml_str = """
root:
  name: agent
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert agent.working_directory == "/home/agent/myrepo"

    def test_params_substituted_in_prompts(self):
        """Params are substituted in user_prompt and system_prompt."""
        yaml_str = """
params:
  task: review the code

root:
  name: agent
  user_prompt: "Please $task"
  system_prompt: "You will $task"
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert agent.user_prompt == "Please review the code"
        assert agent.system_prompt == "You will review the code"

    def test_runtime_vars_left_intact_by_safe_substitute(self):
        """$spec and $execution_slug are left as-is since they are runtime vars."""
        yaml_str = """
root:
  name: agent
  user_prompt: "Task: $spec slug: $execution_slug"
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert "$spec" in agent.user_prompt
        assert "$execution_slug" in agent.user_prompt

    def test_params_substituted_in_model(self):
        """Params are substituted in the model field."""
        yaml_str = """
params:
  planner_model: claude-sonnet-4-6

root:
  name: agent
  model: $planner_model
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert isinstance(agent, ClaudeAgent)
        assert agent.model == "claude-sonnet-4-6"

    def test_harness_args_allowed_tools(self):
        """harness_args.allowed_tools appends --allowedTools to command_args."""
        yaml_str = """
root:
  name: agent
  model: claude-opus-4-6
  harness_args:
    allowed_tools: "Bash Read Grep"
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert "--allowedTools" in agent.config.command_args
        idx = agent.config.command_args.index("--allowedTools")
        assert agent.config.command_args[idx + 1] == "Bash Read Grep"

    def test_mcp_servers_wired(self):
        """mcp_servers are wired into agent.config.mcp_servers."""
        yaml_str = """
root:
  name: agent
  mcp_servers:
    - name: github
      url: https://mcp.github.com
      headers:
        Authorization: Bearer token123
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert "github" in agent.config.mcp_servers
        assert agent.config.mcp_servers["github"]["url"] == "https://mcp.github.com"
        assert agent.config.mcp_servers["github"]["headers"]["Authorization"] == "Bearer token123"

    def test_monitor_prompt_set(self):
        """monitor_prompt is passed through to the agent."""
        yaml_str = """
root:
  name: agent
  monitor_prompt: Watch the agent carefully.
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert agent.monitor_prompt == "Watch the agent carefully."

    def test_gemini_model_produces_base_agent(self):
        """Gemini model produces a base Agent (not ClaudeAgent or CodexAgent)."""
        yaml_str = """
root:
  name: agent
  model: gemini-3-pro-preview
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert type(agent) is Agent
        assert agent.model == "gemini-3-pro-preview"
        assert agent.config.command == "gemini"


# ---------------------------------------------------------------------------
# TestConstructors
# ---------------------------------------------------------------------------


class TestConstructors:
    def test_constructor_produces_agents(self):
        """Definition with args produces a working constructor closure."""
        yaml_str = """
definitions:
  worker:
    args:
      file: string
    template:
      - name: reviewer-$file
        user_prompt: Review $file

root:
  name: lead
  constructors:
    - worker
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert "worker" in agent.constructors

        result = agent.constructors["worker"](file="main.py")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].name == "reviewer-main.py"
        assert result[0].user_prompt == "Review main.py"

    def test_constructor_missing_arg_rejected(self):
        """Missing required arg raises ValueError."""
        yaml_str = """
definitions:
  worker:
    args:
      file: string
    template:
      - name: w-$file

root:
  name: lead
  constructors:
    - worker
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")

        with pytest.raises(ValueError, match="missing required arg"):
            agent.constructors["worker"]()

    def test_constructor_extra_arg_rejected(self):
        """Extra kwargs raise ValueError."""
        yaml_str = """
definitions:
  worker:
    args:
      file: string
    template:
      - name: w-$file

root:
  name: lead
  constructors:
    - worker
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")

        with pytest.raises(ValueError, match="unexpected arg"):
            agent.constructors["worker"](file="main.py", extra="bad")

    def test_multi_agent_template(self):
        """Constructor template with multiple agents returns a list."""
        yaml_str = """
definitions:
  team:
    args:
      feature: string
    template:
      - name: coder-$feature
        user_prompt: Implement $feature
      - name: tester-$feature
        user_prompt: Test $feature

root:
  name: lead
  constructors:
    - team
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")

        result = agent.constructors["team"](feature="auth")
        assert len(result) == 2
        assert result[0].name == "coder-auth"
        assert result[0].user_prompt == "Implement auth"
        assert result[1].name == "tester-auth"
        assert result[1].user_prompt == "Test auth"

    def test_template_agents_fork_instance_source(self):
        """Constructor-spawned agents have instance_source='fork'."""
        yaml_str = """
definitions:
  worker:
    args:
      task: string
    template:
      - name: w-$task

root:
  name: lead
  constructors:
    - worker
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")

        result = agent.constructors["worker"](task="fix")
        assert result[0].instance_source == "fork"

    def test_constructor_no_args(self):
        """Definition with no args declared works with no kwargs."""
        yaml_str = """
definitions:
  helper:
    template:
      - name: helper
        user_prompt: Help out.

root:
  name: lead
  constructors:
    - helper
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")

        result = agent.constructors["helper"]()
        assert len(result) == 1
        assert result[0].name == "helper"

    def test_params_available_in_constructor_templates(self):
        """Top-level params are substituted in constructor template fields."""
        yaml_str = """
params:
  worker_model: claude-sonnet-4-6

definitions:
  worker:
    args:
      task: string
    template:
      - name: w-$task
        model: $worker_model

root:
  name: lead
  constructors:
    - worker
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")

        result = agent.constructors["worker"](task="fix")
        assert isinstance(result[0], ClaudeAgent)
        assert result[0].model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# TestParamSubstitution
# ---------------------------------------------------------------------------


class TestParamSubstitution:
    def test_param_overrides_win(self):
        """param_overrides override spec defaults."""
        yaml_str = """
params:
  model: claude-opus-4-6

root:
  name: agent
  model: $model
"""
        spec = parse_program_spec(yaml_str, param_overrides={"model": "claude-sonnet-4-6"})
        agent = build_root_agent(spec, "myrepo")
        assert agent.model == "claude-sonnet-4-6"

    def test_params_in_name(self):
        """Params are substituted in agent name."""
        yaml_str = """
params:
  role: reviewer

root:
  name: $role-agent
  user_prompt: You are a $role.
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert agent.name == "reviewer-agent"
        assert agent.user_prompt == "You are a reviewer."

    def test_params_in_mcp_server_url(self):
        """Params are substituted in MCP server URLs."""
        yaml_str = """
params:
  mcp_host: https://mcp.example.com

root:
  name: agent
  mcp_servers:
    - name: custom
      url: $mcp_host/api
"""
        spec = parse_program_spec(yaml_str)
        agent = build_root_agent(spec, "myrepo")
        assert agent.config.mcp_servers["custom"]["url"] == "https://mcp.example.com/api"
