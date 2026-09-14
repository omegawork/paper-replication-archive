#!/usr/bin/env python3
"""CLI for the fail-closed v5 paper-replication runtime."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from runtime_model import (
    REVIEW_ROLES,
    SCHEMA_VERSION,
    STAGE_ORDER,
    append_event,
    atomic_write_json,
    atomic_write_text,
    audit_case,
    canonical_json_bytes,
    case_path,
    ensure_case_dirs,
    load_case,
    load_json,
    load_state,
    now_iso,
    render_case,
    render_chat_card,
    sha256_bytes,
    sha256_file,
    target_matrix_path,
)
from runtime_schema import SchemaValidationError, enum_values, validate_document


TASK_MODES = set(enum_values("case", "task_mode"))
ROUTES = set(enum_values("target_matrix", "targets", "[]", "route"))
RUN_STATES = set(enum_values("target_matrix", "targets", "[]", "run_state"))
CLAIM_STATUSES = set(enum_values("target_matrix", "targets", "[]", "claim_status"))
COMPARISON_VERDICTS = set(enum_values("target_matrix", "targets", "[]", "comparison_verdict"))
STAGE_RUN_STATES = {"not_started", "in_progress", "completed", "failed", "blocked", "recorded_unresolved"}
PRODUCER_ROLES = {"StageA": "Deep Reader", "StageB": "Strategist", "StageC": "Executor", "StageCSummary": "Supervisor"}
ALLOWED_STAGE_ROLES = {
    "Stage0": {"Supervisor"},
    "StageA": {"Deep Reader", "Critic", "Supervisor"},
    "StageB": {"Strategist", "Critic", "Supervisor"},
    "StageC": {"Executor", "Critic", "Supervisor"},
    "StageCSummary": {"Supervisor", "Critic"},
    "StageD": {"Critic", "Supervisor"},
}
TARGET_DECISIONS = {
    "retry",
    "accept_current_status",
    "accept_blocked",
    "mark_not_attempted",
    "exclude_from_archive",
    "freeze_current_result",
    "continue_improve_evidence",
    "skip_next_round",
}
OVERALL_DECISIONS = {"continue_next_round", "stage_end_archive", "conservative_archive", "stop"}


def _persist(case_dir: Path, event_type: str, payload: dict[str, Any], now: str | None) -> str:
    append_event(case_dir, event_type, payload, timestamp=now)
    state = render_case(case_dir, artifacts=False)
    audit_case(case_dir)
    return render_chat_card(state, action=event_type)


def _relative_existing_file(case_dir: Path, value: str) -> str:
    path = Path(value)
    candidate = path if path.is_absolute() else case_dir / path
    candidate = candidate.resolve()
    root = case_dir.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"file must be inside the case directory: {value}")
    if not candidate.is_file():
        raise ValueError(f"required file does not exist: {value}")
    return candidate.relative_to(root).as_posix()


def _approved_target_plans(case_dir: Path) -> tuple[bool, list[str]]:
    matrix_file = target_matrix_path(case_dir)
    if not matrix_file.exists():
        return False, ["work/target_matrix.json is missing"]
    matrix = load_json(matrix_file)
    try:
        validate_document(matrix, "target_matrix")
    except SchemaValidationError as exc:
        return False, [f"target matrix schema failure: {exc}"]
    errors: list[str] = []
    for target in matrix["targets"]:
        if target["route"] == "not_applicable_schematic":
            continue
        plan_path = case_dir / target["evidence_plan"]
        approval_value = target.get("approval")
        if not approval_value:
            errors.append(f"{target['target_id']}: approval path is missing")
            continue
        approval_path = case_dir / approval_value
        if not plan_path.is_file() or not approval_path.is_file():
            errors.append(f"{target['target_id']}: plan or approval file is missing")
            continue
        try:
            plan = load_json(plan_path)
            approval = load_json(approval_path)
            validate_document(plan, "evidence_plan")
            from evidence_runtime import _load_approval
            _load_approval(approval_path, plan_path, plan)
        except (ValueError, SchemaValidationError) as exc:
            errors.append(f"{target['target_id']}: {exc}")
            continue
        if approval["plan_id"] != plan["plan_id"] or approval["plan_sha256"] != sha256_file(plan_path):
            errors.append(f"{target['target_id']}: approval does not match current plan bytes")
        if target["run_state"] not in {"approved", "running", "completed", "failed", "blocked"}:
            errors.append(f"{target['target_id']}: target matrix is not marked approved")
    return not errors, errors


def _stage_passed(state: dict[str, Any], stage_id: str) -> bool:
    stage = state["stages"][stage_id]
    return stage["run_state"] == "completed" and stage["review"]["decision"] == "pass"


def _required_decisions_present(state: dict[str, Any]) -> tuple[bool, list[str]]:
    rounds = state["decisions"]["rounds"]
    if not rounds:
        return False, ["overall"]
    latest_key = str(max(int(key) for key in rounds))
    latest = rounds[latest_key]
    missing = [
        target_id
        for target_id, target in state["targets"].items()
        if target.get("claim_status") != "REPRODUCED_WITHIN_ACCEPTANCE"
        and target_id not in latest["per_target"]
    ]
    if latest.get("overall") is None:
        missing.append("overall")
    return not missing, missing


def _target_review_errors(case_dir, state, target_id):
    """Recheck the selected target's own immutable independent review."""
    from evidence_runtime import verify_bundle
    from exhausted_terminal_record import record_path, verify_record
    target = state['targets'][target_id]
    if not target.get('evidence_bundle') and record_path(case_dir, target_id).exists():
        try:
            verify_record(case_dir, target_id)
            return []
        except (ValueError, OSError) as exc:
            return [f'target {target_id} terminal review is invalid: {exc}']
    review = target.get('independent_review') or {}
    agent = state['agents'].get(review.get('agent_id'), {})
    if (review.get('decision') != 'pass' or agent.get('source') != 'platform_spawn_result'
            or agent.get('role') != 'Critic' or agent.get('stage') != 'StageC'
            or agent.get('target_id') != target_id or agent.get('run_state') != 'finished'):
        return [f'target {target_id} requires its own finished independent Critic review']
    try:
        report = _relative_existing_file(case_dir, review.get('report', ''))
        if sha256_file(case_dir / report) != review.get('report_sha256'):
            raise ValueError('review report bytes changed')
        if review.get('claim_status') != target.get('claim_status'):
            raise ValueError('review belongs to an earlier scientific status')
        if target.get('evidence_bundle'):
            current = verify_bundle(Path(target['evidence_bundle']))
            if current['verification_status'] == 'invalid' or current['manifest_sha256'] != review.get('evidence_manifest_sha256'):
                raise ValueError('review does not bind current numerical evidence')
    except (ValueError, OSError) as exc:
        return [f'target {target_id} review is stale: {exc}']
    return []


def _dependency_errors(case_dir: Path, state: dict[str, Any], stage_id: str) -> list[str]:
    mode = state["case"]["task_mode"]
    errors: list[str] = []
    if stage_id == "Stage0":
        return errors
    if stage_id == "StageA" and not _stage_passed(state, "Stage0"):
        errors.append("Stage0 Supervisor pass is required")
    elif stage_id == "StageB":
        if mode == "deep_reading_only":
            errors.append("deep_reading_only has no StageB")
        if not _stage_passed(state, "StageA"):
            errors.append("StageA Critic pass is required")
        preflight = state["stages"]["StageA"]["preflight"]["aggregate"]
        if not preflight or preflight["result"] != "pass":
            errors.append("StageA aggregate preflight pass is required")
    elif stage_id == "StageC":
        if state["case"].get("execution_policy") == "preview_only":
            errors.append("case is preview_only; execution was not requested")
        if not _stage_passed(state, "StageB"):
            errors.append("StageB Critic pass is required")
        preflight = state["stages"]["StageB"]["preflight"]["aggregate"]
        if not preflight or preflight["result"] != "pass":
            errors.append("StageB aggregate preflight pass is required")
        approved, approval_errors = _approved_target_plans(case_dir)
        if not approved:
            errors.extend(approval_errors)
    elif stage_id == "StageCSummary":
        stage_c = state["stages"]["StageC"]
        aggregate = stage_c["preflight"]["aggregate"]
        if not aggregate or aggregate["result"] != "pass":
            errors.append("StageC aggregate evidence preflight pass is required")
        for target_id, target in state["targets"].items():
            if target.get("run_state") not in {"completed", "failed", "blocked"}:
                errors.append(f"target {target_id} is not terminal")
            target_preflight = stage_c["preflight"]["targets"].get(target_id)
            if not target_preflight or target_preflight["result"] != "pass":
                errors.append(f"target {target_id} preflight has not passed")
            if state['case']['schema_version'] == 5:
                errors.extend(_target_review_errors(case_dir, state, target_id))
    elif stage_id == "StageD":
        if mode == "deep_reading_only":
            if not _stage_passed(state, "StageA"):
                errors.append("StageA Critic pass is required")
        else:
            if not _stage_passed(state, "StageCSummary"):
                errors.append("Stage C Summary Critic pass is required")
            present, missing = (True, []) if state["case"]["schema_version"] == 5 else _required_decisions_present(state)
            if not present:
                errors.append("missing user decisions: " + ", ".join(missing))
    return errors


def command_init_case(args: argparse.Namespace) -> str:
    if not args.spawn_capability_confirmed:
        raise ValueError("native subagent spawn capability must be confirmed before case initialization")
    if args.task_mode not in TASK_MODES:
        raise ValueError(f"unsupported task mode: {args.task_mode}")
    if args.case_dir.exists() and any(args.case_dir.iterdir()):
        raise ValueError(f"case directory already exists and is not empty: {args.case_dir}")
    stamp = now_iso(args.now)
    ensure_case_dirs(args.case_dir)
    metadata: dict[str, Any] = {}
    if args.metadata_json:
        parsed = json.loads(args.metadata_json)
        if not isinstance(parsed, dict):
            raise ValueError("--metadata-json must be a JSON object")
        metadata = parsed
    case = {
        "schema_version": SCHEMA_VERSION,
        "case_id": args.case_id,
        "task_mode": args.task_mode,
        "paper": {"title": args.paper_title, "source": args.source},
        "created_at": stamp,
        "execution_policy": getattr(args, "execution_policy", "task_scoped"),
        "language": getattr(args, "language", "zh-CN"),
        "case_metadata": metadata,
    }
    validate_document(case, "case")
    atomic_write_json(case_path(args.case_dir), case)
    payload = {
        "case_sha256": sha256_bytes(canonical_json_bytes(case)),
        "spawn_capability_confirmed": True,
        "native_progress_status": args.native_progress_status,
        "next_action": "Spawn Stage0 Supervisor",
    }
    return _persist(args.case_dir, "case_initialized", payload, args.now)


def command_register_agent(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    if args.agent_id in state["agents"]:
        raise ValueError(f"duplicate agent id: {args.agent_id}")
    if args.role not in ALLOWED_STAGE_ROLES[args.stage]:
        raise ValueError(f"role {args.role} is not allowed in {args.stage}")
    errors = _dependency_errors(args.case_dir, state, args.stage)
    if args.stage != state["runtime"]["current_stage"] and errors:
        raise ValueError("stage dependency gate closed: " + "; ".join(errors))
    if args.role == "Executor":
        active = [
            agent["agent_id"]
            for agent in state["agents"].values()
            if agent["role"] == "Executor" and agent["run_state"] == "running"
        ]
        if active:
            raise ValueError("StageC target Executors must be serial; active: " + ", ".join(active))
        if not args.target_id:
            raise ValueError("Executor registration requires --target-id")
    if args.replacement_for:
        prior = state["agents"].get(args.replacement_for)
        if not prior or prior["run_state"] != "finished":
            raise ValueError("replacement agent requires a finished --replacement-for agent")
    stamp = now_iso(args.now)
    agent = {
        "agent_id": args.agent_id,
        "run_id": f"run-{len(state['agents']) + 1:04d}",
        "source": "platform_spawn_result",
        "stage": args.stage,
        "role": args.role,
        "target_id": args.target_id,
        "task": args.task,
        "handoff_in": args.handoff_in or [],
        "handoff_out": [],
        "replacement_for": args.replacement_for,
        "registered_at": stamp,
        "last_heartbeat_at": stamp,
        "finished_at": None,
        "run_state": "running",
        "outcome": "pending",
        "materialization": None,
    }
    return _persist(
        args.case_dir,
        "agent_registered",
        {"agent": agent, "stage": args.stage, "target_id": args.target_id, "next_action": args.next_action},
        args.now,
    )


def command_finish_agent(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    agent = state["agents"].get(args.agent_id)
    if not agent:
        raise ValueError(f"agent not found: {args.agent_id}")
    if agent["run_state"] != "running":
        raise ValueError(f"agent is not active: {args.agent_id}")
    payload = {
        "agent_id": args.agent_id,
        "stage": agent["stage"],
        "target_id": agent.get("target_id"),
        "outcome": args.outcome,
        "handoff_out": args.handoff_out or [],
        "materialization": args.materialization,
        "next_action": args.next_action,
    }
    return _persist(args.case_dir, "agent_finished", payload, args.now)


def command_set_stage(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    stage = state["stages"][args.stage]
    run_state = args.run_state or args.status
    if run_state not in STAGE_RUN_STATES:
        raise ValueError(f"unsupported stage run state: {run_state}")
    attempt = stage["attempt_count"] if args.attempt_count is None else args.attempt_count
    repair = stage["repair_count"] if args.repair_count is None else args.repair_count
    target_id = getattr(args, "target_id", None)
    if args.stage == "StageC" and target_id:
        previous = state["targets"].get(target_id, {}).get("scientific_attempt_count", 0)
        if attempt < previous:
            raise ValueError("target scientific attempt count cannot decrease")
    if attempt > 4 or repair > 3:
        raise ValueError("at most four scientific attempts and three scientific repairs are allowed")
    if not target_id and (attempt < stage["attempt_count"] or repair < stage["repair_count"]):
        raise ValueError("attempt and repair counts cannot decrease")
    if attempt < 0 or repair < 0 or repair > max(0, attempt - 1):
        raise ValueError("invalid attempt/repair count relationship")
    advice_id = args.supervisor_advice_agent_id or stage.get("supervisor_advice_agent_id")
    if attempt >= 4:
        advice = state["agents"].get(advice_id) if advice_id else None
        if not advice or advice["role"] != "Supervisor" or advice["run_state"] != "finished":
            raise ValueError("attempt 4 requires a finished registered Supervisor advice run")
    if run_state == "in_progress":
        errors = _dependency_errors(args.case_dir, state, args.stage)
        if errors:
            raise ValueError("stage dependency gate closed: " + "; ".join(errors))
    payload = {
        "stage": args.stage,
        "target_id": target_id,
        "run_state": run_state,
        "attempt_count": attempt,
        "repair_count": repair,
        "supervisor_advice_agent_id": advice_id,
        "next_action": args.next_action,
    }
    return _persist(args.case_dir, "stage_updated", payload, args.now)


def command_record_review(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    expected_role = REVIEW_ROLES[args.stage]
    if args.role != expected_role:
        raise ValueError(f"{args.stage} requires review role {expected_role}")
    agent = state["agents"].get(args.agent_id)
    if not agent or agent["role"] != args.role or agent["stage"] != args.stage:
        raise ValueError("reviewer must be a registered agent with the matching role and stage")
    if agent["run_state"] != "finished":
        raise ValueError("reviewer agent must be finished before its decision is recorded")
    report = _relative_existing_file(args.case_dir, args.report)
    if not report.endswith(".json"):
        raise ValueError("v4 Critic/Supervisor reports must use versioned JSON")
    report_data = load_json(args.case_dir / report)
    validate_document(report_data, "critic_report")
    if report_data["stage"] != args.stage or report_data["reviewer_agent_id"] != args.agent_id:
        raise ValueError("Critic report stage or reviewer does not match the review command")
    if report_data["decision"] != args.decision:
        raise ValueError("Critic report decision does not match the review command")
    payload = {
        "stage": args.stage,
        "agent_id": args.agent_id,
        "decision": args.decision,
        "report": report,
        "reason": args.reason,
        "next_action": args.next_action,
    }
    if args.stage == 'StageC' and agent.get('target_id'):
        target_id = agent['target_id']
        if report_data.get('target_id') != target_id:
            raise ValueError('target Critic report must identify its registered target')
        target = state['targets'].get(target_id, {})
        manifest = None
        if target.get('evidence_bundle'):
            from evidence_runtime import verify_bundle
            verified = verify_bundle(Path(target['evidence_bundle']))
            manifest = verified['manifest_sha256']
            if verified['verification_status'] == 'invalid' or report_data.get('evidence_manifest_sha256') != manifest:
                raise ValueError('target Critic report must bind the current numerical evidence_manifest_sha256')
        payload.update(target_id=target_id, target_review={
            'agent_id': args.agent_id, 'decision': args.decision, 'report': report,
            'report_sha256': sha256_file(args.case_dir / report), 'evidence_manifest_sha256': manifest,
            'claim_status': target.get('claim_status')})
    return _persist(args.case_dir, "review_recorded", payload, args.now)


def command_heartbeat(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    agent = state["agents"].get(args.agent_id)
    if not agent or agent["run_state"] != "running":
        raise ValueError("heartbeat requires an active registered agent")
    payload = {"agent_id": args.agent_id, "stage": agent["stage"], "target_id": agent.get("target_id")}
    return _persist(args.case_dir, "heartbeat_recorded", payload, args.now)


def _legacy_target_mapping(status: str) -> tuple[str, str]:
    lowered = status.lower()
    if "blocked" in lowered or "missing" in lowered:
        return "blocked", "BLOCKED"
    if lowered in {"not_started", "not_attempted_this_round"}:
        return "not_started", "NOT_ATTEMPTED"
    if lowered == "reproduced_within_acceptance":
        return "completed", "REPRODUCED_WITHIN_ACCEPTANCE"
    return "completed", "INCONCLUSIVE"


def command_record_target(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    if getattr(args, 'presentation_status', None) == 'passed':
        raise ValueError('presentation passed requires record-presentation with an independent report bound to the figure evidence')
    current = state["targets"].get(args.target_id, {})
    run_state = args.run_state
    claim_status = args.claim_status
    if args.status:
        mapped_run, mapped_claim = _legacy_target_mapping(args.status)
        run_state = run_state or mapped_run
        claim_status = claim_status or mapped_claim
    run_state = run_state or current.get("run_state", "not_started")
    claim_status = claim_status or current.get("claim_status", "NOT_ATTEMPTED")
    route = args.route or current.get("route")
    verdict = args.comparison_verdict or current.get("comparison_verdict", "not_evaluated")
    if run_state not in RUN_STATES:
        raise ValueError(f"unsupported target run_state: {run_state}")
    if claim_status not in CLAIM_STATUSES:
        raise ValueError(f"unsupported claim_status: {claim_status}")
    if route not in ROUTES:
        raise ValueError("record-target requires a valid --route")
    if verdict not in COMPARISON_VERDICTS:
        raise ValueError(f"unsupported comparison_verdict: {verdict}")
    if route == "visual_reconstruction" and claim_status == "REPRODUCED_WITHIN_ACCEPTANCE":
        raise ValueError("visual_reconstruction cannot support scientific reproduction")
    if route == "not_applicable_schematic" and verdict != "not_applicable_schematic":
        raise ValueError("schematic route requires not_applicable_schematic verdict")
    if getattr(args, "presentation_status", None) and not args.evidence_bundle:
        args.evidence_bundle = current.get("evidence_bundle")
    if claim_status == "REPRODUCED_WITHIN_ACCEPTANCE" and not args.evidence_bundle:
        raise ValueError("positive claim status requires a verified Evidence Bundle")
    target = {
        "target_id": args.target_id,
        "stage_id": args.stage_id or current.get("stage_id", "StageC"),
        "route": route,
        "run_state": run_state,
        "claim_status": claim_status,
        "comparison_verdict": verdict,
        "legacy_status": args.status if args.status else current.get("legacy_status"),
        "evidence_rows": args.evidence_row or current.get("evidence_rows", []),
    }
    target["presentation_status"] = getattr(args, "presentation_status", None) or current.get("presentation_status", "not_checked")
    target["target_kind"] = getattr(args, "target_kind", None) or current.get("target_kind", "paper_result")
    if args.evidence_bundle:
        from evidence_runtime import verify_bundle

        verification = verify_bundle(Path(args.evidence_bundle))
        if verification["target_id"] != args.target_id:
            raise ValueError("Evidence Bundle target_id does not match record-target")
        bundle_plan = load_json(Path(args.evidence_bundle) / "plan.json")
        if bundle_plan.get('operation_kind') == 'presentation':
            raise ValueError('presentation derivatives use record-presentation; preserve the numerical evidence selection')
        if verification['manifest_sha256'] != (current.get('verified_evidence') or {}).get('manifest_sha256'):
            target.update(presentation_status='not_checked', presentation_bundle=None,
                          presentation_review=None, independent_review=None)
        if route != verification["route"]:
            raise ValueError("requested route differs from the Evidence Bundle")
        bundle_kind = bundle_plan.get("target_kind", "paper_result")
        if getattr(args, "target_kind", None) and args.target_kind != bundle_kind:
            raise ValueError("requested target kind differs from the Evidence Bundle")
        target["target_kind"] = bundle_kind
        bundle_run = load_json(Path(args.evidence_bundle) / "run_record.json")
        actual_run_state = bundle_run.get("run_state", "completed" if bundle_run.get("returncode") == 0 else "failed")
        if run_state != actual_run_state:
            raise ValueError("requested run state differs from the Evidence Bundle")
        if verification["verification_status"] not in {"verified", "manual_review_required", "blocked", "execution_failed"}:
            raise ValueError("Evidence Bundle verification failed")
        if claim_status != verification["claim_status"]:
            raise ValueError("requested claim_status differs from recomputed bundle status")
        return _persist(
            args.case_dir,
            "evidence_verified",
            {
                "target_id": args.target_id,
                "target": target,
                "bundle": str(Path(args.evidence_bundle).resolve()),
                "verification": verification,
                "stage": "StageC",
                "next_action": args.next_action,
            },
            args.now,
        )
    # A later run/status cannot silently retain an older positive bundle as its
    # current evidence. Earlier evidence remains intact in its files and events.
    if any(target[key] != current.get(key) for key in ("run_state", "claim_status", "comparison_verdict")):
        target["evidence_bundle"] = None
        target["verified_evidence"] = None
        target["evidence_rows"] = []
        target.update(presentation_status='not_checked', presentation_bundle=None,
                      presentation_review=None, independent_review=None)
    return _persist(
        args.case_dir,
        "target_recorded",
        {"target_id": args.target_id, "target": target, "stage": "StageC", "next_action": args.next_action},
        args.now,
    )


def command_record_decision(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    if args.round < 1:
        raise ValueError("decision round must be >= 1")
    matrix_file = target_matrix_path(args.case_dir)
    if matrix_file.is_file():
        matrix = load_json(matrix_file)
        validate_document(matrix, "target_matrix")
        if args.round != matrix["round"]:
            raise ValueError(f"decision round must match target_matrix.json round {matrix['round']}")
    if state["runtime"]["current_stage"] not in {"StageC", "StageCSummary"}:
        raise ValueError("user decisions may be recorded only during Stage C Summary")
    round_data = state["decisions"]["rounds"].get(str(args.round), {"per_target": {}, "overall": None})
    if args.scope == "target":
        if not args.target_id or args.target_id not in state["targets"]:
            raise ValueError("target decision requires an existing --target-id")
        if args.decision not in TARGET_DECISIONS:
            raise ValueError(f"invalid target decision: {args.decision}")
        if args.target_id in round_data["per_target"]:
            raise ValueError("target decision already recorded for this round")
    else:
        if args.target_id:
            raise ValueError("overall decision must not include --target-id")
        if args.decision not in OVERALL_DECISIONS:
            raise ValueError(f"invalid overall decision: {args.decision}")
        required = [
            target_id
            for target_id, target in state["targets"].items()
            if target.get("claim_status") != "REPRODUCED_WITHIN_ACCEPTANCE"
            and target_id not in round_data["per_target"]
        ]
        if required and state["case"]["schema_version"] == 4:
            raise ValueError("record per-target decisions before the overall decision: " + ", ".join(required))
        if round_data["overall"] is not None:
            raise ValueError("overall decision already recorded for this round")
    payload = {
        "scope": args.scope,
        "round": args.round,
        "target_id": args.target_id,
        "decision": args.decision,
        "reason": args.reason,
        "stage": "StageCSummary",
        "next_action": args.next_action,
    }
    return _persist(args.case_dir, "decision_recorded", payload, args.now)


def command_record_preflight(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    report = _relative_existing_file(args.case_dir, args.report)
    if args.target_id and args.stage != "StageC":
        raise ValueError("target-scoped preflight is defined only for StageC")
    payload = {
        "stage": args.stage,
        "target_id": args.target_id,
        "result": args.result,
        "report": report,
        "attempt_count": state["stages"][args.stage]["attempt_count"],
        "producer_agent_id": args.producer_agent_id,
        "next_action": args.next_action,
    }
    return _persist(args.case_dir, "preflight_recorded", payload, args.now)


def command_render_case(args: argparse.Namespace) -> str:
    state = render_case(args.case_dir)
    return render_chat_card(state, action="render-case")


def command_audit_case(args: argparse.Namespace) -> str:
    result = audit_case(args.case_dir)
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _tree_inventory(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise ValueError(f"source case directory does not exist: {root}")
    items: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        items.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return items


def _legacy_state(source: Path) -> tuple[str, dict[str, Any]]:
    json_candidates = [source / "work" / "runtime_state.json", source / "work" / "state_machine.json"]
    for candidate in json_candidates:
        if candidate.is_file():
            data = load_json(candidate)
            return f"json-schema-{data.get('schema_version', 'unversioned')}", data
    yaml_path = source / "work" / "state_machine.yaml"
    if not yaml_path.is_file():
        return "unversioned-no-state", {}
    text = yaml_path.read_text(encoding="utf-8-sig")
    try:
        import yaml  # type: ignore  # Legacy migration only; never used by v4 cases.

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            data = {}
    except (ImportError, Exception):
        data = {}
        for key in ("case_id", "task_mode", "reproduction_status", "archive_status"):
            match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*[\"']?([^\n\"']+)", text)
            if match:
                data[key] = match.group(1).strip()
        data["_raw_state_text"] = text
    version = data.get("schema_version", "unversioned")
    return f"yaml-schema-{version}", data


def _migration_preview(source: Path, destination: Path) -> dict[str, Any]:
    inventory = _tree_inventory(source)
    legacy_format, legacy = _legacy_state(source)
    preview = {
        "schema_version": SCHEMA_VERSION,
        "source": str(source.resolve()),
        "destination": str(destination.resolve()),
        "source_format": legacy_format,
        "file_count": len(inventory),
        "total_bytes": sum(item["size"] for item in inventory),
        "source_tree_sha256": sha256_bytes(canonical_json_bytes(inventory)),
        "status_policy": "legacy status preserved; all unverified success maps to INCONCLUSIVE",
        "uncertain_fields": [],
    }
    if not legacy:
        preview["uncertain_fields"].extend(["case_id", "task_mode", "paper_title", "targets"])
    preview["preview_sha256"] = sha256_bytes(canonical_json_bytes(preview))
    return preview


def _extract_legacy_targets(legacy: dict[str, Any]) -> dict[str, str]:
    raw_targets = legacy.get("targets", {})
    targets: dict[str, str] = {}
    if isinstance(raw_targets, dict):
        for target_id, value in raw_targets.items():
            if isinstance(value, dict):
                status = (
                    value.get("reproduction_status")
                    or value.get("claim_status")
                    or value.get("current_status")
                    or value.get("status")
                    or legacy.get("reproduction_status")
                )
            else:
                status = value
            targets[str(target_id)] = str(status or "unknown")
    if not targets:
        status = legacy.get("reproduction_status") or legacy.get("archive_status")
        if status:
            targets["legacy_case_status"] = str(status)
    return targets


def command_migrate_case(args: argparse.Namespace) -> str:
    source = args.source.resolve()
    destination = args.destination.resolve()
    preview = _migration_preview(source, destination)
    if not args.execute:
        return json.dumps(preview, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.confirm_preview_hash != preview["preview_sha256"]:
        raise ValueError("--confirm-preview-hash must match the current migration preview")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"migration destination must be absent or empty: {destination}")
    before = _tree_inventory(source)
    ensure_case_dirs(destination)
    legacy_copy = destination / "legacy_source"
    shutil.copytree(source, legacy_copy, copy_function=shutil.copy2)
    copied = _tree_inventory(legacy_copy)
    if canonical_json_bytes(before) != canonical_json_bytes(copied):
        raise ValueError("byte-for-byte legacy copy verification failed")
    legacy_format, legacy = _legacy_state(source)
    task = legacy.get("task", {}) if isinstance(legacy.get("task"), dict) else {}
    case_id = str(task.get("case_id") or legacy.get("case_id") or source.name)
    mode = str(task.get("task_mode") or legacy.get("task_mode") or "reproduce_specified_targets")
    if mode not in TASK_MODES:
        mode = "reproduce_specified_targets"
    title = str(task.get("paper_title") or legacy.get("paper_title") or source.name)
    stamp = now_iso(args.now)
    case = {
        "schema_version": SCHEMA_VERSION,
        "case_id": f"{case_id}-v5-migrated",
        "task_mode": mode,
        "paper": {"title": title, "source": f"legacy:{source}"},
        "created_at": stamp,
        "execution_policy": "preview_only",
        "case_metadata": {
            "migration": {
                "source_format": legacy_format,
                "source_tree_sha256": preview["source_tree_sha256"],
                "source_path": str(source),
            }
        },
    }
    validate_document(case, "case")
    atomic_write_json(case_path(destination), case)
    append_event(
        destination,
        "case_migrated",
        {
            "case_sha256": sha256_bytes(canonical_json_bytes(case)),
            "source_tree_sha256": preview["source_tree_sha256"],
            "next_action": "Review conservative v5 migration; no legacy success is accepted as v5 evidence",
        },
        timestamp=args.now,
    )
    for target_id, legacy_status in _extract_legacy_targets(legacy).items():
        run_state, mapped = _legacy_target_mapping(legacy_status)
        if mapped == "REPRODUCED_WITHIN_ACCEPTANCE":
            mapped = "INCONCLUSIVE"
        append_event(
            destination,
            "target_recorded",
            {
                "target_id": target_id,
                "stage": "StageC",
                "target": {
                    "target_id": target_id,
                    "stage_id": "StageC",
                    "route": "visual_reconstruction" if legacy_status == "not_scientific_reproduction" else "independent_reimplementation",
                    "run_state": run_state,
                    "claim_status": mapped,
                    "comparison_verdict": "manual_review_required" if legacy_status == "not_scientific_reproduction" else "not_evaluated",
                    "legacy_status": legacy_status,
                    "evidence_rows": [],
                },
                "next_action": "Manual review of migrated legacy target",
            },
            timestamp=args.now,
        )
    atomic_write_json(destination / "work" / "legacy_source_manifest.json", {"files": before})
    report = {
        **preview,
        "executed_at": stamp,
        "legacy_copy": "legacy_source",
        "positive_status_policy_applied": True,
        "source_unchanged": _tree_inventory(source) == before,
    }
    atomic_write_json(destination / "reports" / "migration_report.json", report)
    atomic_write_text(
        destination / "reports" / "migration_report.md",
        "# v5 Migration Report\n\n"
        f"- Source format: `{legacy_format}`\n"
        f"- Source files: `{len(before)}`\n"
        f"- Source tree SHA-256: `{preview['source_tree_sha256']}`\n"
        "- Original source: unchanged and never edited.\n"
        "- Legacy statuses: preserved in `legacy_status`; no prior success was upgraded to v5 reproduction.\n",
    )
    render_case(destination)
    audit_case(destination)
    if _tree_inventory(source) != before:
        raise ValueError("legacy source changed during migration")
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def command_build_final_claims(args: argparse.Namespace) -> str:
    state = load_state(args.case_dir)
    claims: list[dict[str, Any]] = []
    from evidence_runtime import verify_bundle

    for target_id, target in sorted(state["targets"].items()):
        bundle_value = target.get("evidence_bundle")
        if not bundle_value:
            claims.append(
                {
                    "target_id": target_id,
                    "claim_status": "INCONCLUSIVE" if target.get("claim_status") == "REPRODUCED_WITHIN_ACCEPTANCE" else target.get("claim_status", "NOT_ATTEMPTED"),
                    "target_kind": target.get("target_kind", "paper_result"),
                    "counts_as_paper_reproduction": False,
                    "allowed_wording": "No verified positive scientific claim is allowed.",
                    "evidence_bundle": None,
                }
            )
            continue
        verification = verify_bundle(Path(bundle_value))
        if verification["verification_status"] == "invalid":
            raise ValueError(
                f"cannot build final claims from invalid Evidence Bundle for {target_id}: "
                + "; ".join(verification.get("integrity_errors", []))
            )
        plan = load_json(Path(bundle_value) / "plan.json")
        kind = plan.get("target_kind", target.get("target_kind", "paper_result"))
        status = verification["claim_status"]
        if status != target.get("claim_status") or verification["route"] != target.get("route"):
            raise ValueError(f"current target state and its selected evidence disagree: {target_id}")
        if status == "REPRODUCED_WITHIN_ACCEPTANCE":
            wording = "Reproduced within the pre-approved acceptance contract."
        elif status == "NOT_REPRODUCED":
            wording = "The executed result did not satisfy the pre-approved acceptance contract."
        elif status == "BLOCKED":
            wording = "Execution was blocked; no scientific reproduction claim is allowed."
        elif status == "MANUAL_REVIEW_REQUIRED":
            wording = "Only a feature-level or manual-review statement is allowed; no numeric reproduction claim is allowed."
        else:
            wording = "Evidence is inconclusive; no positive scientific reproduction claim is allowed."
        if kind == "internal_check":
            wording = "Internal consistency check: " + wording + " This is not a paper-result reproduction."
        claims.append(
            {
                "target_kind": kind,
                "counts_as_paper_reproduction": kind == "paper_result" and status == "REPRODUCED_WITHIN_ACCEPTANCE",
                "presentation_status": target.get("presentation_status", "not_checked"),
                "target_id": target_id,
                "claim_id": verification["claim_id"],
                "claim_status": status,
                "comparison_verdict": verification["comparison_verdict"],
                "route": verification["route"],
                "allowed_wording": wording,
                "evidence_bundle": str(Path(bundle_value).resolve()),
                "manifest_sha256": verification["manifest_sha256"],
            }
        )
    result = {
        "schema_version": state["case"]["schema_version"],
        "paper_targets_accepted": sum(item.get("counts_as_paper_reproduction", False) for item in claims),
        "internal_checks": sum(item.get("target_kind") == "internal_check" for item in claims),
        "case_id": state["case"]["case_id"],
        "generated_from_event_hash": state["runtime"]["last_event_hash"],
        "claims": claims,
    }
    atomic_write_json(args.case_dir / "reports" / "final_response_allowed_claims.json", result)
    lines = [
        "# Final Response Allowed Claims",
        "",
        "> Deterministically generated from currently verified Evidence Bundles.",
        "",
        "| Target | Claim status | Route | Comparison | Allowed wording | Manifest SHA-256 |",
        "|---|---|---|---|---|---|",
    ]
    for item in claims:
        values = [
            item.get("target_id"), item.get("claim_status"), item.get("route", "-"),
            item.get("comparison_verdict", "-"), item["allowed_wording"], item.get("manifest_sha256", "-"),
        ]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    if state["case"]["schema_version"] == 5:
        from report_language import structured_report
        language = state["case"].get("language", "zh-CN")
        text = structured_report("Evidence-supported claims" if language == "en" else "证据支持的结论", result, language)
    else:
        text = "\n".join(lines) + "\n"
    atomic_write_text(args.case_dir / "reports" / "final_response_allowed_claims.md", text)
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"



def command_define_targets(args):
    state = load_state(args.case_dir)
    matrix = load_json(args.file)
    validate_document(matrix, "target_matrix")
    if matrix["schema_version"] != 5 or matrix["case_id"] != state["case"]["case_id"]:
        raise ValueError("target definitions must belong to this v5 case")
    ids = [t["target_id"] for t in matrix["targets"]]
    if len(set(ids)) != len(ids):
        raise ValueError("target ids must be unique")
    if set(state.get("targets", {})) - set(ids):
        raise ValueError("cannot silently remove a recorded target")
    for target in matrix["targets"]:
        if target["target_id"] not in state["targets"] and target["claim_status"] != "NOT_ATTEMPTED":
            raise ValueError("new target definitions cannot establish scientific conclusions")
    return _persist(args.case_dir, "targets_defined", {"matrix": matrix, "next_action": "Validate target plans"}, args.now)


def command_engineering_repair(args):
    state = load_state(args.case_dir)
    item = state["targets"][args.target_id] if args.target_id else state["stages"][args.stage]
    evidence = _relative_existing_file(args.case_dir, args.evidence)
    previous = item.get("last_engineering_repair", {})
    if not args.change.strip() or (previous.get("diagnosis") == args.diagnosis and previous.get("change") == args.change
                                 and previous.get("evidence_sha256") == sha256_file(args.case_dir / evidence)):
        raise ValueError("no new repair or evidence; do not repeat the same failed operation")
    return _persist(args.case_dir, "engineering_repair_recorded", {
        "stage": args.stage, "target_id": args.target_id, "category": args.category,
        "diagnosis": args.diagnosis, "change": args.change, "evidence": evidence,
        "evidence_sha256": sha256_file(args.case_dir / evidence), "next_action": args.next_action}, args.now)


def command_export_case(args):
    source, destination = args.case_dir.resolve(), args.destination.resolve()
    audit_case(source)
    if destination == source or source in destination.parents or destination in source.parents or destination.exists():
        raise ValueError("export requires a new, separate destination")
    shutil.copytree(source, destination)
    return json.dumps({"source_unchanged": True, "destination": str(destination), "mode": "byte-preserving-export"}) + "\n"


def command_record_presentation(args):
    from evidence_runtime import verify_bundle
    state = load_state(args.case_dir)
    target = dict(state["targets"][args.target_id])
    if not target.get("evidence_bundle"):
        raise ValueError("presentation requires selected numerical evidence")
    parent = verify_bundle(Path(target["evidence_bundle"]))
    derivative = verify_bundle(args.bundle)
    plan = load_json(args.bundle / "plan.json")
    original_figure = args.bundle.resolve() == Path(target['evidence_bundle']).resolve()
    if (derivative["target_id"] != args.target_id or parent['verification_status'] == 'invalid'
            or derivative["verification_status"] not in {"verified", "manual_review_required"}
            or (not original_figure and (plan.get("operation_kind") != "presentation"
                or plan["presentation_of"]["manifest_sha256"] != parent["manifest_sha256"]))):
        raise ValueError("presentation must verify and bind the current unchanged numerical evidence")
    reviewer = state["agents"].get(args.reviewer_id, {})
    if (reviewer.get("source") != "platform_spawn_result" or reviewer.get("role") != "Critic"
            or reviewer.get("run_state") != "finished" or reviewer.get("target_id") != args.target_id
            or reviewer.get("stage") not in {"StageC", "StageD"}):
        raise ValueError("presentation quality requires a finished native Critic")
    report = _relative_existing_file(args.case_dir, args.report)
    review = load_json(args.case_dir / report)
    validate_document(review, "critic_report")
    producer = load_json(args.bundle / "approval.json").get("review", {}).get("producer_agent_id")
    if (review.get("reviewer_agent_id") != args.reviewer_id or review.get("target_id") != args.target_id
            or review.get("stage") != reviewer.get("stage") or args.reviewer_id == producer
            or review.get("presentation_manifest_sha256") != derivative["manifest_sha256"]
            or (args.status == "passed" and review.get("decision") != "pass")
            or (args.status == "needs_repair" and review.get("decision") not in {"fail", "blocked"})):
        raise ValueError("presentation review must be independent and bind this target, status and exact derivative manifest")
    expected_report = (args.case_dir / report).resolve()
    if not any((Path(path) if Path(path).is_absolute() else args.case_dir / path).resolve() == expected_report
               for path in reviewer.get("handoff_out", [])):
        raise ValueError("presentation review report must be in the finished Critic's handoff")
    target.update(presentation_status=args.status, presentation_bundle=str(args.bundle.resolve()),
                  presentation_review={"agent_id": args.reviewer_id, "report": report,
                                       "sha256": sha256_file(args.case_dir / report)})
    return _persist(args.case_dir, "target_recorded", {"target_id": args.target_id, "target": target,
        "stage": state['runtime']['current_stage'], "next_action": "Continue with the numerical result and reviewed presentation"}, args.now)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="paper-replication-archive v5 runtime controller")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-case")
    init.add_argument("case_dir", type=Path)
    init.add_argument("--case-id", required=True)
    init.add_argument("--task-mode", required=True, choices=sorted(TASK_MODES))
    init.add_argument("--paper-title", required=True)
    init.add_argument("--source", required=True)
    init.add_argument("--native-progress-status", choices=["pending", "available", "unavailable"], default="pending")
    init.add_argument("--spawn-capability-confirmed", action="store_true")
    init.add_argument("--language", choices=["zh-CN", "en"], default="zh-CN")
    init.add_argument("--execution-policy", choices=["task_scoped", "preview_only", "per_run"], default="task_scoped")
    init.add_argument("--metadata-json")
    init.add_argument("--now")
    init.set_defaults(handler=command_init_case)

    register = sub.add_parser("register-agent")
    register.add_argument("case_dir", type=Path)
    register.add_argument("--stage", required=True, choices=STAGE_ORDER)
    register.add_argument("--role", required=True, choices=["Supervisor", "Deep Reader", "Strategist", "Executor", "Critic"])
    register.add_argument("--agent-id", required=True)
    register.add_argument("--task", required=True)
    register.add_argument("--target-id")
    register.add_argument("--handoff-in", action="append")
    register.add_argument("--replacement-for")
    register.add_argument("--next-action", default="Wait for registered agent output")
    register.add_argument("--now")
    register.set_defaults(handler=command_register_agent)

    finish = sub.add_parser("finish-agent")
    finish.add_argument("case_dir", type=Path)
    finish.add_argument("--agent-id", required=True)
    finish.add_argument("--outcome", required=True)
    finish.add_argument("--handoff-out", action="append")
    finish.add_argument("--materialization", choices=["direct_agent_write", "materialized_verbatim_by_orchestrator"])
    finish.add_argument("--next-action", required=True)
    finish.add_argument("--now")
    finish.set_defaults(handler=command_finish_agent)

    stage = sub.add_parser("set-stage")
    stage.add_argument("case_dir", type=Path)
    stage.add_argument("--stage", required=True, choices=STAGE_ORDER)
    stage.add_argument("--run-state", choices=sorted(STAGE_RUN_STATES))
    stage.add_argument("--status", choices=sorted(STAGE_RUN_STATES), help="compatibility alias for --run-state")
    stage.add_argument("--target-id")
    stage.add_argument("--attempt-count", type=int)
    stage.add_argument("--repair-count", type=int)
    stage.add_argument("--supervisor-advice-agent-id")
    stage.add_argument("--next-action", required=True)
    stage.add_argument("--now")
    stage.set_defaults(handler=command_set_stage)

    review = sub.add_parser("record-review")
    review.add_argument("case_dir", type=Path)
    review.add_argument("--stage", required=True, choices=STAGE_ORDER)
    review.add_argument("--role", required=True, choices=["Supervisor", "Critic"])
    review.add_argument("--agent-id", required=True)
    review.add_argument("--decision", required=True, choices=["pass", "fail", "blocked"])
    review.add_argument("--report", required=True)
    review.add_argument("--reason", default="")
    review.add_argument("--next-action", required=True)
    review.add_argument("--now")
    review.set_defaults(handler=command_record_review)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("case_dir", type=Path)
    heartbeat.add_argument("--agent-id", required=True)
    heartbeat.add_argument("--now")
    heartbeat.set_defaults(handler=command_heartbeat)

    target = sub.add_parser("record-target")
    target.add_argument("case_dir", type=Path)
    target.add_argument("--target-id", required=True)
    target.add_argument("--status", help="legacy status compatibility input")
    target.add_argument("--run-state", choices=sorted(RUN_STATES))
    target.add_argument("--claim-status", choices=sorted(CLAIM_STATUSES))
    target.add_argument("--route", choices=sorted(ROUTES))
    target.add_argument("--presentation-status", choices=["not_checked", "passed", "needs_repair", "not_applicable"])
    target.add_argument("--target-kind", choices=["paper_result", "internal_check", "schematic"])
    target.add_argument("--stage-id")
    target.add_argument("--comparison-verdict", choices=sorted(COMPARISON_VERDICTS))
    target.add_argument("--evidence-row", action="append")
    target.add_argument("--evidence-bundle")
    target.add_argument("--next-action", required=True)
    target.add_argument("--now")
    target.set_defaults(handler=command_record_target)

    decision = sub.add_parser("record-decision")
    decision.add_argument("case_dir", type=Path)
    decision.add_argument("--scope", required=True, choices=["target", "overall"])
    decision.add_argument("--round", required=True, type=int)
    decision.add_argument("--target-id")
    decision.add_argument("--decision", required=True)
    decision.add_argument("--reason", default="")
    decision.add_argument("--next-action", required=True)
    decision.add_argument("--now")
    decision.set_defaults(handler=command_record_decision)

    preflight = sub.add_parser("record-preflight")
    preflight.add_argument("case_dir", type=Path)
    preflight.add_argument("--stage", required=True, choices=["StageA", "StageB", "StageC", "StageCSummary"])
    preflight.add_argument("--target-id")
    preflight.add_argument("--result", required=True, choices=["pass", "fail"])
    preflight.add_argument("--report", required=True)
    preflight.add_argument("--producer-agent-id")
    preflight.add_argument("--next-action", required=True)
    preflight.add_argument("--now")
    preflight.set_defaults(handler=command_record_preflight)

    render = sub.add_parser("render-case")
    render.add_argument("case_dir", type=Path)
    render.set_defaults(handler=command_render_case)

    audit = sub.add_parser("audit-case")
    audit.add_argument("case_dir", type=Path)
    audit.set_defaults(handler=command_audit_case)

    migrate = sub.add_parser("migrate-case")
    migrate.add_argument("source", type=Path)
    migrate.add_argument("--destination", required=True, type=Path)
    migrate.add_argument("--execute", action="store_true")
    migrate.add_argument("--confirm-preview-hash")
    migrate.add_argument("--now")
    migrate.set_defaults(handler=command_migrate_case)

    final_claims = sub.add_parser("build-final-claims")
    final_claims.add_argument("case_dir", type=Path)
    final_claims.set_defaults(handler=command_build_final_claims)
    presentation = sub.add_parser("record-presentation")
    presentation.add_argument("case_dir", type=Path)
    presentation.add_argument("--target-id", required=True)
    presentation.add_argument("--bundle", required=True, type=Path)
    presentation.add_argument("--reviewer-id", required=True)
    presentation.add_argument("--report", required=True)
    presentation.add_argument("--status", required=True, choices=["passed", "needs_repair"])
    presentation.add_argument("--now")
    presentation.set_defaults(handler=command_record_presentation)
    define = sub.add_parser("define-targets")
    define.add_argument("case_dir", type=Path)
    define.add_argument("--file", required=True, type=Path)
    define.add_argument("--now")
    define.set_defaults(handler=command_define_targets)
    repair = sub.add_parser("record-engineering-repair")
    repair.add_argument("case_dir", type=Path)
    repair.add_argument("--stage", required=True, choices=STAGE_ORDER)
    repair.add_argument("--target-id")
    repair.add_argument("--category", required=True, choices=["environment", "transport", "format", "presentation"])
    repair.add_argument("--diagnosis", required=True)
    repair.add_argument("--change", required=True)
    repair.add_argument("--evidence", required=True)
    repair.add_argument("--next-action", required=True)
    repair.add_argument("--now")
    repair.set_defaults(handler=command_engineering_repair)
    status = sub.add_parser("status")
    status.add_argument("case_dir", type=Path)
    status.set_defaults(handler=lambda a: render_chat_card(load_state(a.case_dir), action="status"))
    export = sub.add_parser("export-case")
    export.add_argument("case_dir", type=Path)
    export.add_argument("--destination", required=True, type=Path)
    export.set_defaults(handler=command_export_case)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if hasattr(args, "case_dir") and args.command not in {"init-case", "audit-case", "status", "export-case"}:
            from runtime_model import require_writable_case
            require_writable_case(args.case_dir)
        output = args.handler(args)
    except (ValueError, OSError, json.JSONDecodeError, SchemaValidationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
