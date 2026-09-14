#!/usr/bin/env python3
"""Audit the versioned event chain and all derived runtime JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from runtime_model import audit_case


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a versioned event-sourced case")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    try:
        result = audit_case(args.case_dir)
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK: versioned event chain and derived state passed")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
