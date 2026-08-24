"""REPL (Read-Eval-Print Loop) interactive interface."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from undomesh.core.context import ConversationContext
from undomesh.core.loop import QueryLoop
from undomesh.ui.renderer import Renderer
from undomesh.runtime.checkpoints import CheckpointManager, RewindMode
from undomesh.runtime.events import EventType

logger = logging.getLogger(__name__)


class REPL:
    """Interactive REPL for UndoMesh.

    Commands:
        /help    - Show help
        /clear   - Clear conversation
        /stats   - Show session statistics
        /save    - Save session to file
        /quit    - Exit
    """

    def __init__(
        self,
        query_loop: QueryLoop,
        context: ConversationContext,
        checkpoint_manager: CheckpointManager | None = None,
    ) -> None:
        self.query_loop = query_loop
        self.context = context
        self.renderer = Renderer()
        self.checkpoint_manager = checkpoint_manager
        self._running = False

    async def run(self) -> None:
        """Start the REPL loop."""
        self.renderer.print_banner()
        self.renderer.print_welcome()

        # Set up tool execution callbacks for UI feedback
        self.query_loop.set_callbacks(
            on_tool_start=self.renderer.print_tool_start,
            on_tool_end=self.renderer.print_tool_end,
            on_thinking=self.renderer.print_thinking,
        )
        if self.query_loop.tool_executor.permission_manager:
            self.query_loop.tool_executor.permission_manager.set_confirm_callback(
                self.renderer.confirm_tool
            )

        self._running = True

        while self._running:
            try:
                user_input = self.renderer.prompt_input().strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                self._handle_command(user_input)
                continue

            # Display user input
            self.renderer.print_user_input(user_input)

            # Run the query loop
            try:
                response = await self.query_loop.run(user_input)
            except KeyboardInterrupt:
                self.renderer.print_info("\n[Interrupted]")
                continue
            except Exception as e:
                logger.error("Query failed: %s", e, exc_info=True)
                self.renderer.print_error(str(e))
                continue

        # Cleanup
        self._cleanup()

    def _handle_command(self, command: str) -> None:
        """Handle REPL commands."""
        cmd = command.lower().strip()

        if cmd in ("/help", "/h", "/?"):
            self._show_help()

        elif cmd in ("/clear", "/cls"):
            self.context.clear()
            self.query_loop.event_store.append(
                session_id=self.context.session_id,
                event_type=EventType.SESSION_STARTED,
                payload={"model": self.query_loop.config.model},
            )
            self.renderer.print_info("Conversation cleared.")

        elif cmd in ("/stats", "/s"):
            self.renderer.print_stats(
                self.query_loop.token_usage,
                self.context.message_count,
            )

        elif cmd in ("/save",):
            path = self.context.save_session()
            self.renderer.print_info(f"Session saved to: {path}")

        elif cmd in ("/checkpoints", "/cp"):
            self._show_checkpoints()

        elif cmd.startswith("/rewind "):
            self._rewind(cmd)

        elif cmd in ("/restores",):
            self._show_restores()

        elif cmd in ("/instructions",):
            self.renderer.console.print_json(data=self.context.instruction_bundle.describe())

        elif cmd in ("/hooks",):
            hook_bus = self.query_loop.tool_executor.hook_bus
            self.renderer.console.print_json(data=hook_bus.describe() if hook_bus else [])

        elif cmd.startswith("/abort-restore "):
            self._abort_restore(cmd.split(" ", 1)[1].strip())

        elif cmd in ("/quit", "/exit", "/q"):
            self._running = False

        elif cmd.startswith("/model "):
            model = cmd.split(" ", 1)[1].strip()
            self.query_loop.llm_client.config.model = model
            self.renderer.print_info(f"Model changed to: {model}")

        else:
            self.renderer.print_error(f"Unknown command: {command}")
            self._show_help()

    def _show_help(self) -> None:
        """Display help information."""
        help_text = """
[bold]Available Commands:[/bold]
  [cyan]/help[/cyan]     Show this help message
  [cyan]/clear[/cyan]    Clear conversation history
  [cyan]/stats[/cyan]    Show session statistics
  [cyan]/save[/cyan]     Save current session
  [cyan]/checkpoints[/cyan] List prompt checkpoints and code health
  [cyan]/rewind ID MODE[/cyan] Rewind mode: code, conversation, both, summary
  [cyan]/restores[/cyan] Show compensating restore transactions
  [cyan]/instructions[/cyan] Show loaded project instruction sources
  [cyan]/hooks[/cyan] Show registered lifecycle hooks
  [cyan]/abort-restore ID[/cyan] Release a conflicted restore lease
  [cyan]/quit[/cyan]     Exit UndoMesh

[bold]Tips:[/bold]
  • Ask me to read, write, or edit files
  • Use glob patterns to find files (e.g., "find all .py files")
  • Ask me to run commands (tests, git, etc.)
  • I'll remember context throughout our conversation
"""
        self.renderer.console.print(help_text)

    def _show_checkpoints(self) -> None:
        if self.checkpoint_manager is None:
            self.renderer.print_error("Checkpoint manager is unavailable")
            return
        checkpoints = self.checkpoint_manager.list_checkpoints()
        if not checkpoints:
            self.renderer.print_info("No prompt checkpoints yet.")
            return
        for index, item in enumerate(checkpoints, start=1):
            prompt = item.prompt.replace("\n", " ")[:70]
            self.renderer.console.print(
                f"[cyan]{index:>2}[/cyan]  [bold]{item.code_health:<11}[/bold]  "
                f"tx={len(item.transaction_ids):<2}  {prompt}"
            )

    def _rewind(self, command: str) -> None:
        if self.checkpoint_manager is None:
            self.renderer.print_error("Checkpoint manager is unavailable")
            return
        parts = command.split()
        if len(parts) < 2:
            self.renderer.print_error("Usage: /rewind <index|checkpoint-id|latest> [code|conversation|both|summary]")
            return
        checkpoint_id = parts[1]
        try:
            mode = RewindMode(parts[2] if len(parts) > 2 else "both")
            if mode in {RewindMode.CODE, RewindMode.BOTH} and not self.renderer.confirm_tool(
                "rewind_checkpoint", {"checkpoint_id": checkpoint_id, "mode": mode.value}
            ):
                self.renderer.print_info("Rewind cancelled.")
                return
            receipt = self.checkpoint_manager.rewind(checkpoint_id, mode)
            self.renderer.print_info(
                f"Rewind {receipt.mode.value} completed; active session={receipt.active_session_id}"
                + (f", restore={receipt.restore_id}" if receipt.restore_id else "")
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.renderer.print_error(f"Rewind failed: {exc}")

    def _show_restores(self) -> None:
        if self.checkpoint_manager is None:
            self.renderer.print_error("Checkpoint manager is unavailable")
            return
        receipts = self.checkpoint_manager.change_engine.restore_coordinator.list_recent()
        if not receipts:
            self.renderer.print_info("No restore transactions yet.")
            return
        for item in receipts:
            self.renderer.console.print(
                f"[cyan]{item.restore_id[:8]}[/cyan]  {item.state.value:<12}  "
                f"paths={len(item.touched_paths)}  sources={len(item.source_transaction_ids)}"
            )

    def _abort_restore(self, restore_id: str) -> None:
        if self.checkpoint_manager is None:
            self.renderer.print_error("Checkpoint manager is unavailable")
            return
        if not self.renderer.confirm_tool("abort_restore_transaction", {"restore_id": restore_id}):
            self.renderer.print_info("Restore abort cancelled.")
            return
        try:
            receipt = self.checkpoint_manager.change_engine.restore_coordinator.abort(restore_id)
            self.renderer.print_info(f"Restore {receipt.restore_id} is now {receipt.state.value}.")
        except (OSError, RuntimeError, ValueError) as exc:
            self.renderer.print_error(f"Restore abort failed: {exc}")

    def _cleanup(self) -> None:
        """Clean up on exit."""
        # Auto-save session
        try:
            self.context.save_session()
        except Exception:
            pass
        self.renderer.print_goodbye()
