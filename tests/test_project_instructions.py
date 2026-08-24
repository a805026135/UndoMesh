from __future__ import annotations

from pathlib import Path

from undomesh.context.instructions import ProjectInstructionLoader
from undomesh.core.context import ConversationContext


def test_instruction_chain_is_broad_to_specific_and_override_wins(tmp_path: Path) -> None:
    user = tmp_path / "user"
    project = tmp_path / "project"
    nested = project / "src" / "service"
    user.mkdir()
    nested.mkdir(parents=True)
    (user / "AGENTS.md").write_text("user rule", encoding="utf-8")
    (project / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (project / "src" / "AGENTS.md").write_text("ignored normal", encoding="utf-8")
    (project / "src" / "AGENTS.override.md").write_text("src override", encoding="utf-8")
    (nested / "TEAM.md").write_text("service rule", encoding="utf-8")

    bundle = ProjectInstructionLoader(
        project,
        working_directory=nested,
        user_config_dir=user,
        fallback_names=("TEAM.md",),
    ).load()

    assert [source.scope for source in bundle.sources] == [
        "user", "project", "directory", "directory"
    ]
    assert bundle.content.index("user rule") < bundle.content.index("root rule")
    assert bundle.content.index("root rule") < bundle.content.index("src override")
    assert bundle.content.index("src override") < bundle.content.index("service rule")
    assert "ignored normal" not in bundle.content


def test_instruction_budget_stops_before_partial_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    (project / "AGENTS.md").write_text("root", encoding="utf-8")
    (nested / "AGENTS.md").write_text("x" * 100, encoding="utf-8")

    bundle = ProjectInstructionLoader(
        project,
        working_directory=nested,
        max_bytes=150,
    ).load()

    assert bundle.truncated
    assert len(bundle.sources) == 1
    assert "root" in bundle.content
    assert "x" * 100 not in bundle.content


def test_context_injects_project_instructions(config, tmp_project: Path) -> None:
    (tmp_project / "AGENTS.md").write_text("Run the focused tests.", encoding="utf-8")

    context = ConversationContext(config)

    assert "Run the focused tests." in context.system_prompt
    assert context.instruction_bundle.sources[0].scope == "project"
