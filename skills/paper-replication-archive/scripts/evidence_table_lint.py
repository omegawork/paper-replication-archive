#!/usr/bin/env python3
"""Ensure the visible Evidence Table is fully generated from runtime JSON."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from runtime_model import load_state, render_completion_evidence


def validate(case_dir: Path) -> list[str]:
    path = case_dir / "reports" / "completion_evidence_table.md"
    if not path.is_file():
        return [f"missing generated table: {path}"]
    expected = render_completion_evidence(load_state(case_dir))
    return [] if path.read_text(encoding="utf-8-sig") == expected else ["Completion Evidence Table differs from event-replayed state"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint generated v4 Completion Evidence Table")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    try:
        errors = validate(args.case_dir)
    except (ValueError, OSError) as exc:
        errors = [str(exc)]
    if errors:
        print("FAIL: evidence table guardrail failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: generated Completion Evidence Table passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
