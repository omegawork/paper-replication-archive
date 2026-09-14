#!/usr/bin/env python3
"""Deterministic offline fixture command used by Evidence Runtime tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["scalar", "table", "curve", "stochastic", "mutate", "extra"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "scalar":
        value = {"value": 1.25}
    elif args.mode == "table":
        value = {"value": {"columns": ["x", "y"], "rows": [{"x": 0.0, "y": 1.0}, {"x": 1.0, "y": 2.0}]}}
    elif args.mode == "curve":
        value = {"value": {"x": [0.0, 1.0, 2.0], "y": [2.0, 4.0, 6.0]}}
    elif args.mode == "stochastic":
        value = {"value": [{"seed": 1, "value": 0.9}, {"seed": 2, "value": 1.0}, {"seed": 3, "value": 1.1}]}
    elif args.mode == "mutate":
        Path(__file__).with_name("unexpected_source_write.txt").write_text("mutation", encoding="utf-8")
        value = {"value": 1.25}
    else:
        args.output.with_name("undeclared.json").write_text("{}\n", encoding="utf-8")
        value = {"value": 1.25}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
