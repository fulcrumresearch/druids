"""Tests for system prompts on program agents."""

from unittest.mock import MagicMock, patch

from programs.program_utils import (
    AGENT_SYSTEM_PROMPT,
    GIT_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    ROOT_AGENT_SYSTEM_PROMPT,
    VERIFICATION_PROMPT,
    make_executor,
)


EXECUTOR_SYSTEM_PROMPT = AGENT_SYSTEM_PROMPT + "\n\n" + GIT_SYSTEM_PROMPT + "\n\n" + VERIFICATION_PROMPT
ROOT_SYSTEM_PROMPT = (
    AGENT_SYSTEM_PROMPT + "\n\n" + GIT_SYSTEM_PROMPT + "\n\n" + ROOT_AGENT_SYSTEM_PROMPT + "\n\n" + VERIFICATION_PROMPT
)


class TestSystemPromptConstants:
    def test_agent_system_prompt_mentions_orpheus(self):
        assert "Orpheus" in AGENT_SYSTEM_PROMPT

    def test_agent_system_prompt_mentions_autonomous(self):
        assert "autonomously" in AGENT_SYSTEM_PROMPT

    def test_agent_system_prompt_mentions_sandbox(self):
        assert "sandbox" in AGENT_SYSTEM_PROMPT

    def test_agent_system_prompt_contains_identity_template_vars(self):
        assert "$execution_slug" in AGENT_SYSTEM_PROMPT
        assert "$agent_name" in AGENT_SYSTEM_PROMPT
        assert "$working_directory" in AGENT_SYSTEM_PROMPT

    def test_git_system_prompt_contains_branch_template_var(self):
        assert "$branch_name" in GIT_SYSTEM_PROMPT

    def test_git_system_prompt_mentions_create_pr_skill(self):
        assert "/create-pr" in GIT_SYSTEM_PROMPT

    def test_root_system_prompt_mentions_create_pr_skill(self):
        assert "/create-pr" in ROOT_AGENT_SYSTEM_PROMPT

    def test_root_system_prompt_mentions_submit(self):
        assert "submit" in ROOT_AGENT_SYSTEM_PROMPT


class TestMakeExecutor:
    def test_claude_executor_has_git_system_prompt(self):
        agent = make_executor("claude", "exec-1", "/home/agent/repo")

        assert agent.system_prompt == EXECUTOR_SYSTEM_PROMPT

    @patch("orpheus.lib.agents.codex.settings")
    def test_codex_executor_has_git_system_prompt(self, mock_settings):
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = mock_key

        agent = make_executor("codex", "exec-1", "/home/agent/repo")

        assert agent.system_prompt == EXECUTOR_SYSTEM_PROMPT

    def test_executor_system_prompt_does_not_contain_root(self):
        agent = make_executor("claude", "exec-1", "/home/agent/repo")

        assert ROOT_AGENT_SYSTEM_PROMPT not in agent.system_prompt


class TestClaudeProgram:
    def test_root_agent_has_full_system_prompt(self):
        from programs.claude import create_task_program

        agent = create_task_program("test spec", "snap-123", "myrepo")

        assert agent.system_prompt == ROOT_SYSTEM_PROMPT


class TestCodexProgram:
    @patch("orpheus.lib.agents.codex.settings")
    def test_root_agent_has_full_system_prompt(self, mock_settings):
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = mock_key

        from programs.codex import create_task_program

        agent = create_task_program("test spec", "snap-123", "myrepo")

        assert agent.system_prompt == ROOT_SYSTEM_PROMPT


REVIEW_FULL_SYSTEM_PROMPT = AGENT_SYSTEM_PROMPT + "\n\n" + REVIEW_SYSTEM_PROMPT


REVIEW_KWARGS = {
    "pr_number": 42,
    "pr_title": "Add widgets",
    "pr_body": "Adds widget support.",
    "repo_full_name": "owner/repo",
}


class TestReviewAgentProgram:
    def test_returns_single_demo_agent(self):
        from programs.verify import create_review_agent

        agent = create_review_agent("snap-123", "myrepo", **REVIEW_KWARGS)

        assert agent.name == "demo"
        assert agent.instance_source == "devbox"
        assert agent.working_directory == "/home/agent/myrepo"

    def test_demo_agent_has_review_system_prompt(self):
        from programs.verify import create_review_agent

        agent = create_review_agent("snap-123", "myrepo", **REVIEW_KWARGS)

        assert agent.system_prompt == REVIEW_FULL_SYSTEM_PROMPT

    def test_demo_agent_does_not_have_git_prompt(self):
        from programs.verify import create_review_agent

        agent = create_review_agent("snap-123", "myrepo", **REVIEW_KWARGS)

        assert GIT_SYSTEM_PROMPT not in agent.system_prompt
        assert ROOT_AGENT_SYSTEM_PROMPT not in agent.system_prompt

    def test_demo_agent_user_prompt_contains_pr_info(self):
        from programs.verify import create_review_agent

        agent = create_review_agent("snap-123", "myrepo", **REVIEW_KWARGS)

        assert "PR #42" in agent.user_prompt
        assert "Add widgets" in agent.user_prompt
        assert "gh pr diff 42" in agent.user_prompt

    def test_demo_agent_includes_original_spec(self):
        from programs.verify import create_review_agent

        agent = create_review_agent("snap-123", "myrepo", **REVIEW_KWARGS, original_spec="Build X.")

        assert "Build X." in agent.user_prompt
        assert "Original task spec" in agent.user_prompt

    def test_monitor_prompt_references_pr(self):
        from programs.verify import create_review_agent

        agent = create_review_agent("snap-123", "myrepo", **REVIEW_KWARGS)

        assert agent.monitor_prompt is not None
        assert "PR #42" in agent.monitor_prompt
        assert "owner/repo" in agent.monitor_prompt
        assert "Adds widget support." in agent.monitor_prompt
