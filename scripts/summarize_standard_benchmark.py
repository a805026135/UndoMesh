"""Create comparable evidence from JSONL benchmark outcomes without running external datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from miniclaude.evaluation.standard import wilson_interval


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="JSONL records with success, tokens, cost and duration_seconds")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.results.read_text(encoding="utf-8").splitlines() if line.strip()]
    successes = sum(bool(row["success"]) for row in rows)
    low, high = wilson_interval(successes, len(rows))
    report = {
        "attempts": len(rows), "successes": successes,
        "success_rate": successes / len(rows) if rows else 0,
        "wilson_95_interval": [low, high],
        "token_usage": sum(int(row.get("tokens", 0)) for row in rows),
        "cost": sum(float(row.get("cost", 0)) for row in rows),
        "wall_time_seconds": sum(float(row.get("duration_seconds", 0)) for row in rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
