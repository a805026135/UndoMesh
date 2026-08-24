from miniclaude.core.config import MiniClaudeConfig
from miniclaude.tools.registry import ToolRegistry


def test_default_registry_has_no_mutation_bypass(config) -> None:
    registry = ToolRegistry(config)
    registry.register_all()
    names = set(registry.get_names())
    assert {"apply_patch_set", "rollback_change_set", "transaction_storage_status"} <= names
    assert {"write_file", "edit_file", "run_command"}.isdisjoint(names)


def test_legacy_mutation_tools_require_explicit_opt_in(tmp_project) -> None:
    config = MiniClaudeConfig(
        project_dir=str(tmp_project),
        transactional_changes_only=False,
        allow_shell=True,
    )
    registry = ToolRegistry(config)
    registry.register_all()
    assert {"write_file", "edit_file", "run_command"} <= set(registry.get_names())
