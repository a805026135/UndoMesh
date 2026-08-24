"""CLI entry point for UndoMesh: python -m undomesh."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

app = typer.Typer(help="UndoMesh - AI Coding Agent")


@app.command()
def main(
    query: str | None = typer.Argument(None, help="Direct query (skip REPL)"),
    model: str = typer.Option("", "--model", "-m", help="Model to use"),
    provider: str = typer.Option("", "--provider", help="Provider preset (openai/deepseek/anthropic/gemini/dashscope/kimi/custom)"),
    base_url: str = typer.Option("", "--base-url", help="Override OpenAI-compatible Base URL"),
    test_connection: bool = typer.Option(False, "--test-connection", help="Test provider/key/model and exit"),
    project_dir: str = typer.Option("", "--dir", "-d", help="Project directory"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable memory system"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
    gui: bool = typer.Option(False, "--gui", "-g", help="Launch desktop GUI mode"),
    checkpoints: bool = typer.Option(False, "--checkpoints", help="List prompt checkpoints and exit"),
    rewind: str = typer.Option("", "--rewind", help="Checkpoint ID/index/latest to rewind"),
    rewind_mode: str = typer.Option("both", "--rewind-mode", help="code/conversation/both/summary"),
    yes: bool = typer.Option(False, "--yes", help="Approve a requested code rewind non-interactively"),
    instructions: bool = typer.Option(False, "--instructions", help="Show loaded project instruction sources and exit"),
    hooks: bool = typer.Option(False, "--hooks", help="Show registered lifecycle hooks and exit"),
    app_server: bool = typer.Option(False, "--app-server", help="Run newline-delimited JSON-RPC/ACP service on stdio"),
) -> None:
    """UndoMesh - A replayable coding agent with durable recovery."""
    # Load .env from project root
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # Setup logging
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Apply CLI overrides
    import os
    if model:
        os.environ["UNDOMESH_MODEL"] = model
    if provider:
        os.environ["UNDOMESH_PROVIDER"] = provider
    if base_url:
        os.environ["UNDOMESH_API_BASE_URL"] = base_url
    if project_dir:
        os.environ["UNDOMESH_PROJECT_DIR"] = project_dir
    if no_memory:
        os.environ["UNDOMESH_MEMORY_ENABLED"] = "false"

    # GUI mode
    if gui:
        from undomesh.ui.desktop import launch_desktop
        launch_desktop()
        return

    asyncio.run(_async_main(query, test_connection, checkpoints, rewind, rewind_mode, yes, instructions, hooks, app_server))


async def _async_main(
    query: str | None,
    test_connection: bool = False,
    checkpoints: bool = False,
    rewind: str = "",
    rewind_mode: str = "both",
    yes: bool = False,
    instructions: bool = False,
    hooks: bool = False,
    app_server: bool = False,
) -> None:
    """Async entry point."""
    from undomesh.app import UndoMeshApp

    agent_app = UndoMeshApp()
    app_closed = False

    try:
        if app_server:
            from undomesh.server.rpc import JSONRPCDispatcher, serve_stdio
            from undomesh.server.service import AppService
            service = AppService(agent_app, agent_app.config.job_db_path)
            await service.start()
            try:
                await serve_stdio(JSONRPCDispatcher(service))
            finally:
                await service.close()
                app_closed = True
            return
        await agent_app.initialize()

        if instructions:
            assert agent_app.context is not None
            import json
            print(json.dumps(agent_app.context.instruction_bundle.describe(), ensure_ascii=False, indent=2))
            return

        if hooks:
            assert agent_app.hook_bus is not None
            import json
            print(json.dumps(agent_app.hook_bus.describe(), ensure_ascii=False, indent=2))
            return

        if test_connection:
            assert agent_app.llm_client is not None
            report = await agent_app.llm_client.diagnose_connection()
            label = "Connected" if report.success else "Connection failed"
            print(f"{label}: {report.provider} / {report.model}: {report.status.value}: {report.message}")
            return

        if checkpoints:
            assert agent_app.checkpoint_manager is not None
            values = [
                agent_app.checkpoint_manager.describe(item)
                for item in agent_app.checkpoint_manager.list_checkpoints()
            ]
            import json
            print(json.dumps(values, ensure_ascii=False, indent=2))
            return

        if rewind:
            from dataclasses import asdict
            import json
            from undomesh.runtime.checkpoints import RewindMode
            mode = RewindMode(rewind_mode)
            if mode in {RewindMode.CODE, RewindMode.BOTH} and not yes:
                raise RuntimeError("Code rewind requires --yes after reviewing --checkpoints")
            assert agent_app.checkpoint_manager is not None
            receipt = agent_app.checkpoint_manager.rewind(rewind, mode)
            payload = asdict(receipt)
            payload["mode"] = receipt.mode.value
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        if query:
            # Direct query mode
            response = await agent_app.run_query(query)
            print(response)
        else:
            # REPL mode
            await agent_app.run_repl()
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
    except Exception as e:
        logging.getLogger(__name__).error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        if not app_closed:
            await agent_app.shutdown()


if __name__ == "__main__":
    app()
