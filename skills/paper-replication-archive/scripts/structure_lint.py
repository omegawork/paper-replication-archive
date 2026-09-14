#!/usr/bin/env python3
"""Check the required v5 case layout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REQUIRED_DIRS = (
    "work", "reference/raw", "reference/processed", "reference/author_generated",
    "code", "results", "logs", "stage_gates", "reports", "to_obsidian", "02_reproduction",
)
REQUIRED_FILES = (
    "work/case.json", "work/runtime_state.json", "work/agent_registry.json",
    "work/user_decisions.json", "logs/runtime_events.jsonl", "logs/agent_trace.md",
    "reports/live_progress.md", "reports/completion_evidence_table.md",
)


def validate(case_dir: Path) -> list[str]:
    errors = [f"missing directory: {relative}" for relative in REQUIRED_DIRS if not (case_dir / relative).is_dir()]
    errors.extend(f"missing runtime file: {relative}" for relative in REQUIRED_FILES if not (case_dir / relative).is_file())
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint required v5 case structure")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    errors = validate(args.case_dir)
    if errors:
        print("FAIL: structure guardrail failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: required v5 case structure exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
