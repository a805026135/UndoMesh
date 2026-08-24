from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from undomesh.__main__ import app
from undomesh.execution.recovery import RestoreState
from undomesh.ui.viewmodels import checkpoint_detail, checkpoint_row, restore_history


def test_cli_help_exposes_checkpoint_and_desktop_surfaces() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--gui" in result.stdout
    assert "--checkpoints" in result.stdout
    assert "--rewind" in result.stdout
    assert "--instructions" in result.stdout
    assert "--hooks" in result.stdout
    assert "--app-server" in result.stdout
    assert "--rewind-mode" in result.stdout


def test_recovery_viewmodels_render_health_conflicts_and_receipts() -> None:
    checkpoint = SimpleNamespace(
        prompt="change authentication\ncarefully",
        code_health="conflicted",
        sequence=42,
        transaction_ids=("tx-1", "tx-2"),
        checkpoint_id="checkpoint-id",
        conflict_paths=("auth.py",),
        unavailable=(),
    )
    receipt = SimpleNamespace(
        restore_id="12345678-aaaa-bbbb-cccc-123456789abc",
        state=RestoreState.CONFLICTED,
        touched_paths=("auth.py", "config.py"),
    )

    assert "conflicted" in checkpoint_row(checkpoint)
    detail = checkpoint_detail(checkpoint)
    assert "Transactions  2" in detail
    assert "auth.py" in detail
    history = restore_history((receipt,))
    assert "12345678" in history
    assert "conflicted" in history
    assert "2 paths" in history


def test_empty_restore_history_is_explicit() -> None:
    assert restore_history(()) == "RESTORE RECEIPTS\nNo compensating restores yet"
