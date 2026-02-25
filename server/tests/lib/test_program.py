"""Tests for Program."""

import pytest
from orpheus.lib.agents.base import Agent
from orpheus.lib.program import Program


class TestProgram:
    def test_program_has_name(self):
        """Program requires name."""
        program = Program(name="test")
        assert program.name == "test"

    def test_program_has_constructors(self):
        """Program has constructors dict."""
        program = Program(name="test")
        assert program.constructors == {}

    def test_program_with_constructors(self):
        """Program can have constructors."""

        def make_worker(task: str) -> Program:
            return Agent(name=f"worker-{task}")

        program = Program(
            name="orchestrator",
            constructors={"worker": make_worker},
        )

        assert "worker" in program.constructors
        worker = program.constructors["worker"](task="foo")
        assert worker.name == "worker-foo"

    @pytest.mark.asyncio
    async def test_exec_returns_empty_list(self):
        """Base Program.exec() returns empty list."""
        program = Program(name="test")
        result = await program.exec()
        assert result == []


class TestAgentAsProgram:
    def test_agent_inherits_constructors(self):
        """Agent can have constructors like Program."""

        def make_helper() -> Agent:
            return Agent(name="helper")

        agent = Agent(
            name="orch",
            constructors={"helper": make_helper},
        )

        assert "helper" in agent.constructors
