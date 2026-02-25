"""YAML program spec parsing, validation, and agent construction.

Converts declarative YAML program specs into Agent objects that the Execution
runtime can manage. See specs/program-specs.md for the full design.
"""

from __future__ import annotations

import logging
from string import Template
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict

from orpheus.lib.agents.base import ACPConfig, Agent
from orpheus.lib.agents.claude import ClaudeAgent
from orpheus.lib.agents.codex import CodexAgent
from orpheus.lib.program import Program


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models -- mirrors the YAML schema from specs/program-specs.md
# ---------------------------------------------------------------------------


class McpServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    headers: dict[str, str] | None = None


class AgentNodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model: str | None = None
    harness: str | None = None
    harness_args: dict[str, str] | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    monitor_prompt: str | None = None
    mcp_servers: list[McpServerSpec] | None = None
    constructors: list[str] | None = None


class DefinitionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    args: dict[str, str] | None = None
    template: list[AgentNodeSpec]


class HarnessModelsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: list[str]
    default: str


class HarnessSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    default_args: list[str] = []
    args: dict[str, str] = {}
    env: dict[str, str] = {}
    models: HarnessModelsSpec | None = None
    setup: str | None = None


class ProgramSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: dict[str, str] | None = None
    harnesses: dict[str, HarnessSpec] | None = None
    definitions: dict[str, DefinitionSpec] | None = None
    root: AgentNodeSpec


# ---------------------------------------------------------------------------
# Built-in harness registry
# ---------------------------------------------------------------------------

BUILTIN_HARNESSES: dict[str, HarnessSpec] = {
    "claude-code": HarnessSpec(
        command="claude-code-acp",
        default_args=["--dangerously-skip-permissions"],
        args={"allowed_tools": "--allowedTools"},
        models=HarnessModelsSpec(prefix=["claude-"], default="claude-opus-4-6"),
    ),
    "codex": HarnessSpec(
        command="codex-acp",
        default_args=["-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"'],
        args={"allowed_tools": "config.toml"},
        models=HarnessModelsSpec(prefix=["gpt-", "codex-", "o1-", "o3-", "o4-"], default="gpt-5.2-codex"),
    ),
    "gemini": HarnessSpec(
        command="gemini",
        default_args=["--experimental-acp", "--yolo"],
        models=HarnessModelsSpec(prefix=["gemini-"], default="gemini-3-pro-preview"),
        setup="npm install -g @google/gemini-cli",
    ),
}


# Reserved names that cannot be used as constructor arg names.
RESERVED_ARG_NAMES = frozenset({"execution_slug", "agent_name", "working_directory", "branch_name", "spec"})


# ---------------------------------------------------------------------------
# Harness resolution
# ---------------------------------------------------------------------------


def resolve_harness(
    model: str | None,
    harness: str | None,
    custom_harnesses: dict[str, HarnessSpec] | None = None,
) -> tuple[str, HarnessSpec, str | None]:
    """Determine harness name, spec, and resolved model from agent node fields.

    Returns (harness_name, harness_spec, resolved_model).
    resolved_model is None only if the harness has no models config and no model was given.
    """
    all_harnesses = dict(BUILTIN_HARNESSES)
    if custom_harnesses:
        all_harnesses.update(custom_harnesses)

    if harness:
        if harness not in all_harnesses:
            raise ValueError(f"Unknown harness: {harness}")
        spec = all_harnesses[harness]
        resolved_model = model or (spec.models.default if spec.models else None)
        return harness, spec, resolved_model

    if model:
        for name, spec in all_harnesses.items():
            if spec.models:
                for prefix in spec.models.prefix:
                    if model.startswith(prefix):
                        return name, spec, model
        raise ValueError(f"No harness found for model prefix: {model}")

    # Neither model nor harness specified: default to claude-code
    default = BUILTIN_HARNESSES["claude-code"]
    return "claude-code", default, default.models.default


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


def parse_program_spec(yaml_str: str, param_overrides: dict[str, str] | None = None) -> ProgramSpec:
    """Parse a YAML string into a validated ProgramSpec.

    param_overrides are merged into spec.params (overrides win).
    """
    raw = yaml.safe_load(yaml_str)
    if not isinstance(raw, dict):
        raise ValueError("Program spec must be a YAML mapping")

    spec = ProgramSpec(**raw)

    # Merge param overrides
    if param_overrides:
        if spec.params is None:
            spec.params = {}
        spec.params.update(param_overrides)

    # Validate constructor references and reserved arg names
    _validate_spec(spec)

    return spec


def _validate_spec(spec: ProgramSpec) -> None:
    """Check cross-field constraints that Pydantic cannot express."""
    definitions = spec.definitions or {}

    # Validate constructor names in root reference existing definitions
    if spec.root.constructors:
        for cname in spec.root.constructors:
            if cname not in definitions:
                raise ValueError(f"Constructor '{cname}' references undefined definition")

    # Validate constructor names in definition templates too
    for def_name, defn in definitions.items():
        for tmpl_agent in defn.template:
            if tmpl_agent.constructors:
                for cname in tmpl_agent.constructors:
                    if cname not in definitions:
                        raise ValueError(
                            f"Constructor '{cname}' in definition '{def_name}' references undefined definition"
                        )

    # Reject reserved arg names in definitions
    for def_name, defn in definitions.items():
        if defn.args:
            reserved_used = set(defn.args.keys()) & RESERVED_ARG_NAMES
            if reserved_used:
                raise ValueError(
                    f"Definition '{def_name}' uses reserved arg name(s): {', '.join(sorted(reserved_used))}"
                )


# ---------------------------------------------------------------------------
# Agent construction from spec
# ---------------------------------------------------------------------------


def build_root_agent(spec: ProgramSpec, repo_name: str) -> Agent:
    """Build the root Agent (and its constructors) from a validated ProgramSpec."""
    custom_harnesses = spec.harnesses or {}
    params = dict(spec.params) if spec.params else {}
    definitions = spec.definitions or {}
    working_dir = f"/home/agent/{repo_name}"

    root = _build_agent(spec.root, params, custom_harnesses, working_dir, instance_source="devbox")

    # Build constructors for each definition referenced in root.constructors
    if spec.root.constructors:
        for cname in spec.root.constructors:
            defn = definitions[cname]
            root.constructors[cname] = _make_constructor(cname, defn, params, custom_harnesses, working_dir)

    return root


def _build_agent(
    node: AgentNodeSpec,
    params: dict[str, str],
    custom_harnesses: dict[str, HarnessSpec],
    working_dir: str,
    instance_source: str,
) -> Agent:
    """Create an Agent from an AgentNodeSpec, substituting params into string fields."""
    # Substitute params into model/harness before resolution so $planner_model etc. work
    pre_model = _sub(node.model, params) if node.model else None
    pre_harness = _sub(node.harness, params) if node.harness else None
    harness_name, harness_spec, resolved_model = resolve_harness(pre_model, pre_harness, custom_harnesses)

    # Substitute params into remaining string fields
    name = _sub(node.name, params)
    system_prompt = _sub(node.system_prompt, params) if node.system_prompt else None
    user_prompt = _sub(node.user_prompt, params) if node.user_prompt else None
    monitor_prompt = _sub(node.monitor_prompt, params) if node.monitor_prompt else None

    # Build command_args from harness defaults + harness_args
    command_args = list(harness_spec.default_args)
    if node.harness_args:
        for arg_name, arg_value in node.harness_args.items():
            flag = harness_spec.args.get(arg_name)
            if flag:
                command_args.extend([flag, _sub(arg_value, params)])

    # Build MCP servers dict for ACPConfig
    mcp_servers: dict[str, dict] = {}
    if node.mcp_servers:
        for srv in node.mcp_servers:
            entry: dict = {"url": _sub(srv.url, params)}
            if srv.headers:
                entry["headers"] = {k: _sub(v, params) for k, v in srv.headers.items()}
            mcp_servers[srv.name] = entry

    # Use typed agent subclass for known harnesses, base Agent for custom
    if harness_name == "claude-code":
        agent = ClaudeAgent(
            name=name,
            model=resolved_model or "claude-opus-4-6",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            monitor_prompt=monitor_prompt,
            working_directory=working_dir,
            instance_source=instance_source,
        )
        # ClaudeAgent.__post_init__ sets config; override command_args if we have extra flags
        if command_args != list(harness_spec.default_args):
            agent.config.command_args = command_args
        if mcp_servers:
            agent.config.mcp_servers = mcp_servers
    elif harness_name == "codex":
        agent = CodexAgent(
            name=name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            monitor_prompt=monitor_prompt,
            working_directory=working_dir,
            instance_source=instance_source,
        )
        if mcp_servers:
            agent.config.mcp_servers = mcp_servers
    else:
        config = ACPConfig(
            command=harness_spec.command,
            command_args=command_args,
            env=dict(harness_spec.env),
            mcp_servers=mcp_servers,
        )
        agent = Agent(
            name=name,
            model=resolved_model,
            config=config,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            monitor_prompt=monitor_prompt,
            working_directory=working_dir,
            instance_source=instance_source,
        )

    return agent


def _make_constructor(
    def_name: str,
    defn: DefinitionSpec,
    params: dict[str, str],
    custom_harnesses: dict[str, HarnessSpec],
    working_dir: str,
) -> Callable[..., list[Program]]:
    """Create a constructor closure for a definition.

    The returned callable validates kwargs against declared args, substitutes
    params + kwargs into all template fields, and returns a list of Agent objects.
    """
    declared_args = set(defn.args.keys()) if defn.args else set()

    def constructor(**kwargs) -> list[Program]:
        # Validate kwargs match declared args exactly
        provided = set(kwargs.keys())
        missing = declared_args - provided
        extra = provided - declared_args
        if missing:
            raise ValueError(f"Constructor '{def_name}' missing required arg(s): {', '.join(sorted(missing))}")
        if extra:
            raise ValueError(f"Constructor '{def_name}' received unexpected arg(s): {', '.join(sorted(extra))}")

        # Merge params + kwargs for substitution
        sub_vars = dict(params)
        sub_vars.update(kwargs)

        agents: list[Program] = []
        for tmpl in defn.template:
            agent = _build_agent(tmpl, sub_vars, custom_harnesses, working_dir, instance_source="fork")
            agents.append(agent)

        return agents

    return constructor


def _sub(value: str, variables: dict[str, str]) -> str:
    """Substitute $variables using safe_substitute (leaves unknown vars intact)."""
    return Template(value).safe_substitute(variables)
