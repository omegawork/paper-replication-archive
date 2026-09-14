#!/usr/bin/env python3
"""Describe resource risk for a preview; never authorize execution."""

from __future__ import annotations

import argparse
import json


def classify(minutes: float, memory_fraction: float, *, gpu: bool = False, network: bool = False) -> dict[str, object]:
    risks: list[str] = []
    if memory_fraction > 0.80:
        risks.append("estimated memory exceeds 80 percent of available RAM")
    elif memory_fraction > 0.50:
        risks.append("estimated memory exceeds 50 percent of available RAM")
    if minutes > 60:
        risks.append("estimated wall time exceeds one hour")
    elif minutes >= 30:
        risks.append("estimated wall time is at least 30 minutes")
    if gpu:
        risks.append("GPU use requires explicit budget approval and remains advisory in the local runtime")
    if network:
        risks.append("network use requires explicit approval and is not hard-isolated by the local runtime")
    return {
        "execution_policy": "preview_only",
        "risk_level": "high" if memory_fraction > 0.80 or minutes > 60 or gpu else "moderate" if risks else "low",
        "risks": risks,
        "enforcement": {"wall_time": "hard_timeout", "memory": "advisory", "gpu": "advisory", "network": "advisory"},
        "approval_required": True,
        "note": "This estimate is a planning signal, not execution permission or scientific evidence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Describe v4 Target resource risk")
    parser.add_argument("--minutes", type=float, required=True)
    parser.add_argument("--memory-fraction", type=float, required=True)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--network", action="store_true")
    args = parser.parse_args()
    if args.minutes < 0 or not 0 <= args.memory_fraction <= 1:
        parser.error("minutes must be nonnegative and memory-fraction must be between 0 and 1")
    print(json.dumps(classify(args.minutes, args.memory_fraction, gpu=args.gpu, network=args.network), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
