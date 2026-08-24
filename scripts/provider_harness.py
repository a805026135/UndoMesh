"""Run a safe real-provider Tool Calling smoke test and emit JSON evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from undomesh.app import UndoMeshApp
from undomesh.core.config import UndoMeshConfig
from undomesh.runtime.events import EventType


async def run(args: argparse.Namespace) -> dict:
    fixture = PROJECT_ROOT / "benchmarks" / "fixtures" / "provider_smoke.txt"
    marker = fixture.read_text(encoding="utf-8").strip()
    with TemporaryDirectory() as directory:
        temporary = Path(directory)
        config = UndoMeshConfig(
            provider=args.provider,
            model=args.model,
            api_base_url=args.base_url,
            project_dir=str(PROJECT_ROOT),
            memory_enabled=False,
            event_db=str(temporary / "events.db"),
            skill_eval_db=str(temporary / "skills.db"),
        )
        app = UndoMeshApp(config)
        try:
            response = await app.run_query(
                "Use read_file on benchmarks/fixtures/provider_smoke.txt and return only its content."
            )
            assert app.query_loop is not None
            events = app.query_loop.events
            tool_events = [event for event in events if event.event_type == EventType.TOOL_COMPLETED]
            return {
                "schema_version": 1,
                "provider": args.provider,
                "model": args.model,
                "base_url": config.effective_api_base_url,
                "success": marker in response and bool(tool_events),
                "iterations": app.query_loop.iteration_count,
                "completed_tools": [event.payload.get("tool_name") for event in tool_events],
                "marker_returned": marker in response,
                "api_key_persisted": False,
            }
        finally:
            await app.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
