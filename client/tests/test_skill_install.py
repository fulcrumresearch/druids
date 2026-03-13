"""Tests for the skill install command."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from druids.commands.skill import SKILLS
from druids.main import app
from typer.testing import CliRunner


runner = CliRunner()


def _expected_content(filename: str) -> str:
    return importlib.resources.files("druids.skills").joinpath(filename).read_text(encoding="utf-8")


def test_install_creates_all_skill_files(tmp_path: Path):
    """Install should create SKILL.md for every bundled skill."""
    result = runner.invoke(app, ["skill", "install", "--target-dir", str(tmp_path)])

    assert result.exit_code == 0
    for skill_name, filename in SKILLS.items():
        skill_path = tmp_path / ".claude" / "skills" / skill_name / "SKILL.md"
        assert skill_path.exists(), f"{skill_name} not installed"
        assert skill_path.read_text(encoding="utf-8") == _expected_content(filename)


def test_install_twice_succeeds(tmp_path: Path):
    """Running install twice should succeed (overwrite)."""
    result1 = runner.invoke(app, ["skill", "install", "--target-dir", str(tmp_path)])
    assert result1.exit_code == 0

    result2 = runner.invoke(app, ["skill", "install", "--target-dir", str(tmp_path)])
    assert result2.exit_code == 0

    for skill_name in SKILLS:
        skill_path = tmp_path / ".claude" / "skills" / skill_name / "SKILL.md"
        assert skill_path.exists()


def test_install_default_target_dir(monkeypatch, tmp_path: Path):
    """Install with no flags should use current working directory."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["skill", "install"])

    assert result.exit_code == 0
    for skill_name in SKILLS:
        skill_path = tmp_path / ".claude" / "skills" / skill_name / "SKILL.md"
        assert skill_path.exists()


def test_install_global(monkeypatch, tmp_path: Path):
    """Install with --global should write to ~/.claude."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    result = runner.invoke(app, ["skill", "install", "--global"])

    assert result.exit_code == 0
    for skill_name, filename in SKILLS.items():
        skill_path = tmp_path / ".claude" / "skills" / skill_name / "SKILL.md"
        assert skill_path.exists()
        assert skill_path.read_text(encoding="utf-8") == _expected_content(filename)


def test_install_global_short_flag(monkeypatch, tmp_path: Path):
    """Install with -g should behave the same as --global."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    result = runner.invoke(app, ["skill", "install", "-g"])

    assert result.exit_code == 0
    for skill_name in SKILLS:
        skill_path = tmp_path / ".claude" / "skills" / skill_name / "SKILL.md"
        assert skill_path.exists()


def test_target_dir_overrides_global(monkeypatch, tmp_path: Path):
    """--target-dir should take precedence over --global."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    target = tmp_path / "custom"
    target.mkdir()

    result = runner.invoke(app, ["skill", "install", "--global", "--target-dir", str(target)])

    assert result.exit_code == 0
    for skill_name in SKILLS:
        assert (target / ".claude" / "skills" / skill_name / "SKILL.md").exists()
        assert not (fake_home / ".claude" / "skills" / skill_name / "SKILL.md").exists()


def test_claude_md_append_when_exists(tmp_path: Path):
    """When CLAUDE.md exists without a Druids section, install should offer to append."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My Project\n\nSome instructions.\n")

    # Simulate answering "yes" to the confirm prompt
    result = runner.invoke(app, ["skill", "install", "--target-dir", str(tmp_path)], input="y\n")

    assert result.exit_code == 0
    content = claude_md.read_text(encoding="utf-8")
    assert "## Druids" in content
    assert "# My Project" in content


def test_claude_md_no_append_when_declined(tmp_path: Path):
    """When user declines, CLAUDE.md should not be modified."""
    claude_md = tmp_path / "CLAUDE.md"
    original = "# My Project\n\nSome instructions.\n"
    claude_md.write_text(original)

    result = runner.invoke(app, ["skill", "install", "--target-dir", str(tmp_path)], input="n\n")

    assert result.exit_code == 0
    assert claude_md.read_text(encoding="utf-8") == original
    # Snippet should be printed to stdout
    assert "## Druids" in result.output


def test_claude_md_skip_when_section_exists(tmp_path: Path):
    """When CLAUDE.md already has a Druids section, do not offer to append."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My Project\n\n## Druids\n\nAlready configured.\n")

    result = runner.invoke(app, ["skill", "install", "--target-dir", str(tmp_path)])

    assert result.exit_code == 0
    # Should not prompt or print the snippet
    assert "Append" not in result.output


def test_claude_md_snippet_printed_when_no_file(tmp_path: Path):
    """When no CLAUDE.md exists, print the snippet to stdout."""
    result = runner.invoke(app, ["skill", "install", "--target-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "## Druids" in result.output
    assert "Recommended CLAUDE.md section" in result.output
