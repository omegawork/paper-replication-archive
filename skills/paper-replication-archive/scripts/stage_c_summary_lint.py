#!/usr/bin/env python3
"""Validate the structured Stage C Summary and decision-panel coverage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from runtime_model import load_json, load_state, render_stage_c_summary


def validate(case_dir: Path) -> list[str]:
    state = load_state(case_dir)
    path = case_dir / "reports" / "stage_c_summary.json"
    markdown = case_dir / "reports" / "stage_c_summary.md"
    if not path.is_file():
        return ["reports/stage_c_summary.json is missing"]
    summary = load_json(path)
    required = {"schema_version", "round", "actual_work", "critic_failures", "targets", "most_important_conclusion", "acceptance_files"}
    errors = [f"summary fields missing: {sorted(required - set(summary))}"] if not required <= set(summary) else []
    if set(summary.get("targets", {})) != set(state["targets"]):
        errors.append("summary target set differs from runtime state")
    unresolved = {target_id for target_id, target in state["targets"].items() if target.get("claim_status") != "REPRODUCED_WITHIN_ACCEPTANCE"}
    if state["case"]["schema_version"] == 4 and not unresolved <= set(summary.get("decision_panels", {})):
        errors.append("every unresolved target requires a decision panel")
    if not markdown.is_file() or markdown.stat().st_size == 0:
        errors.append("reports/stage_c_summary.md is missing or empty")
    elif state["case"]["schema_version"] == 5:
        expected = render_stage_c_summary(summary, language=state["case"].get("language", "zh-CN"), state=state)
        if markdown.read_bytes() != expected.encode("utf-8"):
            errors.append("summary Markdown is stale; regenerate it from canonical content and current events")
        for target_id, details in summary.get("targets", {}).items():
            current = state["targets"].get(target_id, {})
            for field in ("run_state", "claim_status", "comparison_verdict", "presentation_status", "target_kind"):
                if field in details and field in current and details[field] != current[field]:
                    errors.append(f"summary {target_id}.{field} conflicts with event state; omit duplicated status fields or correct canonical content")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint v5 Stage C Summary")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    try:
        errors = validate(args.case_dir)
    except (ValueError, OSError) as exc:
        errors = [str(exc)]
    if errors:
        print("FAIL: Stage C Summary guardrail failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: Stage C Summary guardrail passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
