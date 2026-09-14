#!/usr/bin/env python3
"""Check v5 stage transitions against event-replayed state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from runtime_control import _dependency_errors
from runtime_model import STAGE_ORDER, audit_case, load_json, load_state, sha256_file
from runtime_schema import SchemaValidationError, validate_document


ALLOWED_TRANSITIONS = {
    ("Stage0", "StageA"),
    ("StageA", "StageB"),
    ("StageA", "StageD"),
    ("StageB", "StageC"),
    ("StageC", "StageCSummary"),
    ("StageCSummary", "StageD"),
}


def _review_errors(case_dir: Path, state: dict[str, Any], stage_id: str) -> list[str]:
    stage = state["stages"][stage_id]
    review = stage["review"]
    errors: list[str] = []
    if stage["run_state"] != "completed":
        errors.append(f"{stage_id} must be completed")
    if review["decision"] != "pass" or not review["agent_id"]:
        errors.append(f"{stage_id} independent {review['required_role']} review must pass")
    report = review.get("report")
    if not report or not (case_dir / report).is_file():
        errors.append(f"{stage_id} review report is missing")
    agent = state["agents"].get(review.get("agent_id"))
    if not agent or agent.get("source") != "platform_spawn_result" or agent.get("role") != review["required_role"]:
        errors.append(f"{stage_id} reviewer is not backed by a real registered subagent")
    return errors


def validate(case_dir: Path, stage: str, next_stage: str) -> list[str]:
    if (stage, next_stage) not in ALLOWED_TRANSITIONS:
        return [f"unsupported or skipped stage transition: {stage} -> {next_stage}"]
    audit_case(case_dir)
    state = load_state(case_dir)
    if stage == 'StageC' and state['case']['schema_version'] == 5:
        # Each current target has its own review/negative-terminal proof below;
        # the last target's global decision cannot stand in for all targets.
        errors = [] if state['stages'][stage]['run_state'] in {'completed', 'failed', 'blocked', 'recorded_unresolved'} else ['StageC must be terminal']
    else:
        errors = _review_errors(case_dir, state, stage)
    if stage == "Stage0":
        precheck_path = case_dir / "work" / "stage0_precheck.json"
        if not precheck_path.is_file():
            errors.append("Stage0 requires work/stage0_precheck.json")
        else:
            try:
                precheck = load_json(precheck_path)
                validate_document(precheck, "stage0_precheck")
                manifest_path = (case_dir / precheck["paper_manifest"]).resolve()
                if case_dir.resolve() not in manifest_path.parents or not manifest_path.is_file():
                    raise ValueError("paper_manifest path is missing or outside the case")
                manifest = load_json(manifest_path)
                validate_document(manifest, "paper_manifest")
                paper_path = (case_dir / manifest["local_file"]).resolve()
                if case_dir.resolve() not in paper_path.parents or not paper_path.is_file():
                    raise ValueError("paper_manifest local_file is missing or outside the case")
                if sha256_file(paper_path) != manifest["sha256"]:
                    raise ValueError("paper file hash differs from paper_manifest")
                for item in precheck["source_hashes"]:
                    source_path = (case_dir / item["path"]).resolve()
                    if case_dir.resolve() not in source_path.parents or not source_path.is_file() or sha256_file(source_path) != item.get("sha256"):
                        raise ValueError(f"Stage0 source hash mismatch: {item.get('path')}")
            except (ValueError, SchemaValidationError) as exc:
                errors.append(f"Stage0 precheck schema failure: {exc}")
    errors.extend(_dependency_errors(case_dir, state, next_stage))
    if stage in {"StageA", "StageB", "StageC", "StageCSummary"}:
        aggregate = state["stages"][stage]["preflight"]["aggregate"]
        if not aggregate or aggregate["result"] != "pass":
            errors.append(f"{stage} aggregate deterministic preflight must pass")
    if stage == "StageC":
        target_preflights = state["stages"]["StageC"]["preflight"]["targets"]
        for target_id in state["targets"]:
            record = target_preflights.get(target_id)
            if not record or record["result"] != "pass":
                errors.append(f"StageC target preflight missing or failed: {target_id}")
    required_roles = {
        "Stage0": {"Supervisor"},
        "StageA": {"Deep Reader", "Critic"},
        "StageB": {"Strategist", "Critic"},
        "StageC": {"Executor", "Critic"},
        "StageCSummary": {"Supervisor", "Critic"},
    }.get(stage, set())
    present = {agent["role"] for agent in state["agents"].values() if agent["stage"] == stage}
    for role in sorted(required_roles - present):
        errors.append(f"required real subagent role missing for {stage}: {role}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v5 stage transition")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--stage", required=True, choices=STAGE_ORDER)
    parser.add_argument("--next", required=True, dest="next_stage", choices=STAGE_ORDER)
    args = parser.parse_args()
    try:
        errors = validate(args.case_dir, args.stage, args.next_stage)
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("FAIL: stage gate closed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"OK: gate allows {args.stage} -> {args.next_stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
