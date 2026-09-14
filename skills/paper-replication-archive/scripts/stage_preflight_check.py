#!/usr/bin/env python3
"""Deterministic versioned preflight checks before each independent Critic."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from evidence_runtime import preview_plan, verify_bundle
from exhausted_terminal_record import verify_record as verify_exhausted_terminal_record
from runtime_model import (
    append_event,
    atomic_write_json,
    atomic_write_text,
    load_json,
    load_state,
    now_iso,
    render_case,
    target_matrix_path,
)
from runtime_schema import SchemaValidationError, validate_document


READING_PACK_FILES = (
    "deep_reading_note.md",
    "paper_structure_map.md",
    "formula_explanation.md",
    "figure_overview.md",
    "deep_reading_coverage_table.md",
    "figure_semantic_map.json",
    "formula_list.json",
    "claim_registry.json",
    "parameter_registry.json",
)


def _result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _producer(state: dict[str, Any], stage: str, target_id: str | None) -> str | None:
    role = {"StageA": "Deep Reader", "StageB": "Strategist", "StageC": "Executor", "StageCSummary": "Supervisor"}.get(stage)
    if not role:
        return None
    candidates = [
        agent
        for agent in state["agents"].values()
        if agent["stage"] == stage
        and agent["role"] == role
        and (target_id is None or agent.get("target_id") == target_id)
        and agent["run_state"] == "finished"
    ]
    return candidates[-1]["agent_id"] if candidates else None


def _stage_a(case_dir: Path) -> list[dict[str, Any]]:
    root = case_dir / "01_deep_reading"
    canonical = case_dir / "work" / "deep_reading_pack.json"
    results = [_result("deep_reading_pack.json", canonical.is_file(), str(canonical))]
    if canonical.is_file():
        try:
            validate_document(load_json(canonical), "deep_reading_pack")
            results.append(_result("deep reading schema", True, "versioned schema passed"))
        except (ValueError, SchemaValidationError) as exc:
            results.append(_result("deep reading schema", False, str(exc)))
    for name in READING_PACK_FILES:
        path = root / name
        results.append(_result(name, path.is_file() and path.stat().st_size > 0, str(path)))
    for name in ("figure_semantic_map.json", "formula_list.json", "claim_registry.json", "parameter_registry.json"):
        path = root / name
        if path.is_file():
            try:
                value = load_json(path)
                valid = isinstance(value, (dict, list))
                detail = f"JSON root={type(value).__name__}"
            except ValueError as exc:
                valid, detail = False, str(exc)
            results.append(_result(f"parse {name}", valid, detail))
    return results


def _stage_b(case_dir: Path) -> list[dict[str, Any]]:
    matrix_path = target_matrix_path(case_dir)
    if not matrix_path.is_file():
        return [_result("target_matrix.json", False, "work/target_matrix.json is missing")]
    try:
        matrix = load_json(matrix_path)
        validate_document(matrix, "target_matrix")
    except (ValueError, SchemaValidationError) as exc:
        return [_result("target_matrix.json", False, str(exc))]
    results = [_result("target_matrix.json", True, f"targets={len(matrix['targets'])}")]
    ids = [target["target_id"] for target in matrix["targets"]]
    results.append(_result("unique target ids", len(ids) == len(set(ids)), ", ".join(ids)))
    for target in matrix["targets"]:
        if target["route"] == "not_applicable_schematic":
            results.append(_result(f"{target['target_id']} schematic", True, "no execution plan required"))
            continue
        plan_path = case_dir / target["evidence_plan"]
        try:
            preview = preview_plan(plan_path)
            passed = preview["status"] == "preview_ready"
            detail = json.dumps({"status": preview["status"], "blockers": preview["blockers"], "plan_sha256": preview["plan_sha256"]}, ensure_ascii=False)
        except (ValueError, OSError) as exc:
            passed, detail = False, str(exc)
        results.append(_result(f"{target['target_id']} execution preview", passed, detail))
    return results


def _stage_c_target(case_dir: Path, state: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    target = state["targets"].get(target_id)
    if not target:
        return [_result("runtime target", False, f"target not found: {target_id}")]
    if target.get("route") == "not_applicable_schematic":
        return [_result("schematic target", target.get("comparison_verdict") == "not_applicable_schematic", "no scientific execution")]
    bundle_value = target.get("evidence_bundle")
    if not bundle_value:
        try:
            verification = verify_exhausted_terminal_record(case_dir, target_id)
            passed = verification["verification_status"] == "verified_terminal_failure"
            detail = json.dumps(verification, ensure_ascii=False, sort_keys=True)
        except (ValueError, OSError) as exc:
            passed, detail = False, str(exc)
        return [_result("exhausted terminal record verify", passed, detail)]
    try:
        verification = verify_bundle(Path(bundle_value))
        passed = verification["verification_status"] in {"verified", "manual_review_required", "blocked", "execution_failed"}
        detail = json.dumps(verification, ensure_ascii=False, sort_keys=True)
    except (ValueError, OSError) as exc:
        passed, detail = False, str(exc)
    return [_result("Evidence Bundle verify", passed, detail)]


def _stage_c_aggregate(state: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    target_preflights = state["stages"]["StageC"]["preflight"]["targets"]
    for target_id, target in state["targets"].items():
        record = target_preflights.get(target_id)
        results.append(_result(f"{target_id} target preflight", bool(record and record["result"] == "pass"), str(record)))
        results.append(_result(f"{target_id} terminal run", target.get("run_state") in {"completed", "failed", "blocked"}, target.get("run_state", "missing")))
        if target.get("claim_status") == "REPRODUCED_WITHIN_ACCEPTANCE":
            verified = target.get("verified_evidence") or {}
            results.append(_result(f"{target_id} positive evidence", verified.get("verification_status") == "verified", str(verified)))
    return results or [_result("targets", False, "StageC has no targets")]


def _stage_c_summary(case_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    summary_path = case_dir / "reports" / "stage_c_summary.json"
    markdown_path = case_dir / "reports" / "stage_c_summary.md"
    if not summary_path.is_file():
        return [_result("stage_c_summary.json", False, "missing")]
    try:
        summary = load_json(summary_path)
    except ValueError as exc:
        return [_result("stage_c_summary.json", False, str(exc))]
    required = {"schema_version", "round", "actual_work", "critic_failures", "targets", "most_important_conclusion", "acceptance_files"}
    results = [
        _result("summary required fields", required <= set(summary), f"missing={sorted(required - set(summary))}"),
        _result("summary target coverage", set(summary.get("targets", {})) == set(state["targets"]), "target ids must match runtime state"),
        _result("summary markdown", markdown_path.is_file() and markdown_path.stat().st_size > 0, str(markdown_path)),
    ]
    unresolved = {target_id for target_id, target in state["targets"].items() if target.get("claim_status") != "REPRODUCED_WITHIN_ACCEPTANCE"}
    if state["case"]["schema_version"] == 4:
        results.append(_result("decision panel coverage", unresolved <= set(summary.get("decision_panels", {})), f"unresolved={sorted(unresolved)}"))
    return results


def run_checks(case_dir: Path, stage: str, target_id: str | None, aggregate: bool) -> tuple[list[dict[str, Any]], str | None]:
    state = load_state(case_dir)
    producer = _producer(state, stage, target_id)
    results: list[dict[str, Any]] = []
    if not (stage == "StageC" and aggregate):
        results.append(_result("producer run binding", producer is not None, f"producer_agent_id={producer}"))
    if stage == "StageA":
        results.extend(_stage_a(case_dir))
    elif stage == "StageB":
        results.extend(_stage_b(case_dir))
    elif stage == "StageC" and target_id:
        results.extend(_stage_c_target(case_dir, state, target_id))
    elif stage == "StageC" and aggregate:
        results.extend(_stage_c_aggregate(state))
    elif stage == "StageCSummary":
        results.extend(_stage_c_summary(case_dir, state))
    else:
        raise ValueError("StageC preflight requires either --target-id or --aggregate")
    return results, producer


def report_paths(case_dir: Path, stage: str, target_id: str | None, aggregate: bool) -> tuple[Path, Path]:
    suffix = f"_{re.sub(r'[^A-Za-z0-9_.()-]+', '_', target_id)}" if target_id else "_aggregate" if aggregate and stage == "StageC" else ""
    stem = f"{stage[0].lower() + stage[1:]}{suffix}_preflight_report"
    return case_dir / "stage_gates" / f"{stem}.json", case_dir / "stage_gates" / f"{stem}.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run v5 deterministic checks before Critic spawn")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--stage", required=True, choices=["StageA", "StageB", "StageC", "StageCSummary"])
    parser.add_argument("--target-id")
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()
    try:
        if args.write_report:
            from runtime_model import require_writable_case
            require_writable_case(args.case_dir)
            render_case(args.case_dir)
        state = load_state(args.case_dir)
        results, producer = run_checks(args.case_dir, args.stage, args.target_id, args.aggregate)
        passed = all(item["passed"] for item in results)
        stamp = now_iso(args.now)
        json_path, markdown_path = report_paths(args.case_dir, args.stage, args.target_id, args.aggregate)
        report = {
            "schema_version": state["case"]["schema_version"],
            "stage": args.stage,
            "target_id": args.target_id,
            "aggregate": bool(args.aggregate),
            "result": "pass" if passed else "fail",
            "attempt_count": state["stages"][args.stage]["attempt_count"],
            "producer_agent_id": producer,
            "checked_at": stamp,
            "checks": results,
        }
        if args.write_report:
            atomic_write_json(json_path, report)
            lines = [f"# {args.stage} Deterministic Preflight", "", f"Result: `{'pass' if passed else 'fail'}`", "", "| Check | Result | Detail |", "|---|---|---|"]
            for item in results:
                safe_name = item["name"].replace("|", "\\|")
                safe_detail = item["detail"].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {safe_name} | {'pass' if item['passed'] else 'fail'} | {safe_detail} |")
            lines.extend(["", "This is an engineering guardrail, not a scientific acceptance decision.", ""])
            atomic_write_text(markdown_path, "\n".join(lines))
            append_event(
                args.case_dir,
                "preflight_recorded",
                {
                    "stage": args.stage,
                    "target_id": args.target_id,
                    "result": report["result"],
                    "report": json_path.relative_to(args.case_dir).as_posix(),
                    "attempt_count": report["attempt_count"],
                    "producer_agent_id": producer,
                    "next_action": "Spawn independent Critic" if passed else "Return full preflight report to producer",
                },
                timestamp=args.now,
            )
            render_case(args.case_dir)
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if not passed:
        print(f"FAIL: {args.stage} deterministic preflight failed", file=sys.stderr)
        for item in results:
            if not item["passed"]:
                print(f"- {item['name']}: {item['detail']}", file=sys.stderr)
        return 1
    print(f"OK: {args.stage} deterministic preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
