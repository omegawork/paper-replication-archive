#!/usr/bin/env python3
"""Ensure reports/live_progress.md is a deterministic event-log rendering."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from runtime_model import load_case, load_state, read_events, render_progress


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint the generated progress panel")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    try:
        load_case(args.case_dir)
        expected = render_progress(load_state(args.case_dir), read_events(args.case_dir))
        path = args.case_dir / "reports" / "live_progress.md"
        if not path.is_file() or path.read_text(encoding="utf-8-sig") != expected:
            raise ValueError("live_progress.md differs from the event-log rendering")
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK: generated progress panel passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
