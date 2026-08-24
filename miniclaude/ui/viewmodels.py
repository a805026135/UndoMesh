"""Pure presentation helpers shared by desktop tests and Tk widgets."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def checkpoint_row(checkpoint: Any, *, width: int = 42) -> str:
    prompt = str(checkpoint.prompt).replace("\n", " ")[:width]
    return f"  {str(checkpoint.code_health):<10}  {prompt}"


def checkpoint_detail(checkpoint: Any) -> str:
    return (
        f"Health        {checkpoint.code_health}\n"
        f"Sequence      {checkpoint.sequence}\n"
        f"Transactions  {len(checkpoint.transaction_ids)}\n"
        f"Checkpoint    {checkpoint.checkpoint_id}\n\n"
        f"Conflicts     {', '.join(checkpoint.conflict_paths) or 'none'}\n"
        f"Unavailable   {', '.join(checkpoint.unavailable) or 'none'}"
    )


def restore_history(receipts: Sequence[Any], *, limit: int = 4) -> str:
    lines = ["RESTORE RECEIPTS"]
    for receipt in receipts[:limit]:
        lines.append(
            f"{receipt.restore_id[:8]}  {receipt.state.value:<11}  "
            f"{len(receipt.touched_paths)} paths"
        )
    if len(lines) == 1:
        lines.append("No compensating restores yet")
    return "\n".join(lines)
