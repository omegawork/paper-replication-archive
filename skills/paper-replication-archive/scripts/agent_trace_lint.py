#!/usr/bin/env python3
"""Cross-check real subagent roles against event-replayed registry and trace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from runtime_model import audit_case, load_state, render_agent_trace


def validate(case_dir: Path, required_roles: list[str]) -> list[str]:
    audit_case(case_dir)
    state = load_state(case_dir)
    errors = [
        f"agent source is not platform_spawn_result: {agent_id}"
        for agent_id, agent in state["agents"].items()
        if agent.get("source") != "platform_spawn_result"
    ]
    roles = {agent["role"] for agent in state["agents"].values()}
    errors.extend(f"required subagent role is missing: {role}" for role in required_roles if role not in roles)
    trace_path = case_dir / "logs" / "agent_trace.md"
    if not trace_path.is_file() or trace_path.read_text(encoding="utf-8-sig") != render_agent_trace(state):
        errors.append("agent_trace.md differs from event-replayed agent registry")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint native subagent trace")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--required-role", action="append", default=[])
    args = parser.parse_args()
    try:
        errors = validate(args.case_dir, args.required_role)
    except (ValueError, OSError) as exc:
        errors = [str(exc)]
    if errors:
        print("FAIL: agent trace guardrail failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: agent trace is backed by real registered ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
