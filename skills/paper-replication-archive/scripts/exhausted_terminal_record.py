#!/usr/bin/env python3
"""Create and verify a negative-only StageC exhausted-terminal record.

This record is an integrity-bound engineering closure artifact. It is not an
Evidence Bundle and can never authorize a positive scientific conclusion.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from runtime_model import (
    atomic_write_json,
    canonical_json_bytes,
    event_log_path,
    load_json,
    load_state,
    read_events,
    sha256_bytes,
    sha256_file,
)
from runtime_schema import SchemaValidationError, validate_document


TARGET_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.()-]*")
ALLOWED_TERMINAL_PAIRS = {("failed", "INCONCLUSIVE"), ("blocked", "BLOCKED")}
ALLOWED_TERMINAL_VERDICTS = {
    "not_evaluated",
    "blocked_unreadable_anchor",
    "blocked_missing_candidate",
}
NON_SCHEMATIC_ROUTES = {
    "official_artifact_replay",
    "independent_reimplementation",
    "figure_digitization",
    "visual_reconstruction",
}
PROTECTED_EVIDENCE_PATHS = (
    "logs/runtime_events.jsonl",
    "work/runtime_state.json",
    "work/agent_registry.json",
    "work/user_decisions.json",
    "logs/agent_trace.md",
    "reports/live_progress.md",
    "reports/completion_evidence_table.md",
)


def record_path(case_dir: Path, target_id: str) -> Path:
    """Return the one standard record path for a safe target id."""
    if TARGET_PATTERN.fullmatch(target_id) is None:
        raise ValueError(f"target id is not safe for a record filename: {target_id!r}")
    return case_dir.resolve() / "work" / "exhausted_terminal_records" / f"{target_id}.json"


def _case_relative_file(case_dir: Path, value: str | Path, label: str) -> tuple[Path, str]:
    raw = Path(value)
    if raw.is_absolute():
        raise ValueError(f"{label} must be a case-relative path")
    case_root = case_dir.resolve()
    candidate = (case_root / raw).resolve()
    try:
        relative = candidate.relative_to(case_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} escapes the case directory: {value}") from exc
    if not candidate.is_file():
        raise ValueError(f"{label} is not a file: {relative}")
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {relative}")
    return candidate, relative


def _case_relative_path(case_dir: Path, value: str | Path, label: str) -> tuple[Path, str]:
    raw = Path(value)
    if raw.is_absolute():
        raise ValueError(f"{label} must be a case-relative path")
    case_root = case_dir.resolve()
    candidate = (case_root / raw).resolve()
    try:
        relative = candidate.relative_to(case_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} escapes the case directory: {value}") from exc
    if not candidate.exists():
        raise ValueError(f"{label} does not exist: {relative}")
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {relative}")
    return candidate, relative


def _file_binding(case_dir: Path, value: str | Path, label: str) -> dict[str, Any]:
    path, relative = _case_relative_file(case_dir, value, label)
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"{label} must be nonempty: {relative}")
    return {"path": relative, "size": size, "sha256": sha256_file(path)}


def _event_prefix(case_dir: Path, events: list[dict[str, Any]], count: int) -> dict[str, Any]:
    if count < 1 or count > len(events):
        raise ValueError(f"event prefix count {count} is outside the current log length {len(events)}")
    raw = event_log_path(case_dir).read_bytes()
    lines = raw.splitlines(keepends=True)
    if len(lines) != len(events) or any(not line.endswith(b"\n") for line in lines):
        raise ValueError("runtime event log bytes do not form complete newline-terminated events")
    last = events[count - 1]
    return {
        "event_count": count,
        "last_event_id": last["event_id"],
        "last_event_hash": last["hash"],
        "sha256": sha256_bytes(b"".join(lines[:count])),
    }


def _finished_events(
    events: list[dict[str, Any]],
    state: dict[str, Any],
    target_id: str,
    role: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for event in events:
        if event["event_type"] != "agent_finished":
            continue
        payload = event["payload"]
        agent_id = payload.get("agent_id")
        agent = state["agents"].get(agent_id)
        if not agent or agent_id in seen:
            continue
        if (
            agent.get("source") == "platform_spawn_result"
            and agent.get("stage") == "StageC"
            and agent.get("role") == role
            and agent.get("target_id") == target_id
            and agent.get("run_state") == "finished"
            and payload.get("stage") == "StageC"
            and payload.get("target_id") == target_id
        ):
            seen.add(agent_id)
            result.append((event, agent))
    return result


def _registered_role_ids(
    events: list[dict[str, Any]],
    target_id: str,
    role: str,
) -> list[str]:
    result: list[str] = []
    for event in events:
        if event["event_type"] != "agent_registered":
            continue
        agent = event["payload"].get("agent", {})
        if (
            agent.get("source") == "platform_spawn_result"
            and agent.get("stage") == "StageC"
            and agent.get("role") == role
            and agent.get("target_id") == target_id
        ):
            result.append(agent["agent_id"])
    return result


def _handoff_contains(case_dir: Path, handoff_out: Any, relative: str) -> bool:
    if not isinstance(handoff_out, list):
        return False
    expected = (case_dir.resolve() / Path(relative)).resolve()
    for item in handoff_out:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item)
        candidate = path.resolve() if path.is_absolute() else (case_dir.resolve() / path).resolve()
        if candidate == expected:
            return True
    return False


def _engineering_inventory(case_dir: Path, values: list[str | Path], target_id: str) -> list[dict[str, Any]]:
    if not values:
        raise ValueError("at least one engineering-evidence file or directory is required")
    case_root = case_dir.resolve()
    protected = [(case_root / item).resolve() for item in PROTECTED_EVIDENCE_PATHS]
    protected.append(record_path(case_dir, target_id).resolve())
    records: list[dict[str, Any]] = []
    seen_roots: set[str] = set()
    for value in values:
        root, relative = _case_relative_path(case_dir, value, "engineering evidence")
        if relative in seen_roots:
            raise ValueError(f"duplicate engineering evidence root: {relative}")
        seen_roots.add(relative)
        if any(root == item or root in item.parents for item in protected):
            raise ValueError(f"engineering evidence root contains mutable runtime material: {relative}")
        if root.is_file():
            files = [root]
            kind = "file"
        elif root.is_dir():
            files = sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(case_root).as_posix())
            kind = "directory"
        else:
            raise ValueError(f"engineering evidence must be a file or directory: {relative}")
        if not files:
            raise ValueError(f"engineering evidence directory is empty: {relative}")
        inventory: list[dict[str, Any]] = []
        for file_path in files:
            if file_path.is_symlink():
                raise ValueError(f"engineering evidence must not contain symlinks: {file_path}")
            try:
                file_relative = file_path.resolve().relative_to(case_root).as_posix()
            except ValueError as exc:
                raise ValueError(f"engineering evidence escapes the case directory: {file_path}") from exc
            inventory.append(
                {"path": file_relative, "size": file_path.stat().st_size, "sha256": sha256_file(file_path)}
            )
        records.append(
            {
                "root": relative,
                "kind": kind,
                "tree_sha256": sha256_bytes(canonical_json_bytes(inventory)),
                "files": inventory,
            }
        )
    return sorted(records, key=lambda item: item["root"])


def _build_record(
    case_dir: Path,
    target_id: str,
    critic_report: str | Path,
    supervisor_report: str | Path,
    engineering_evidence: list[str | Path],
    event_prefix_count: int,
) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    events = read_events(case_dir)
    state = load_state(case_dir, audit_derived=False)
    target = state["targets"].get(target_id)
    if not target:
        raise ValueError(f"runtime target not found: {target_id}")
    route = target.get("route")
    if route not in NON_SCHEMATIC_ROUTES:
        raise ValueError("exhausted-terminal records are allowed only for non-schematic routes")
    terminal_pair = (target.get("run_state"), target.get("claim_status"))
    if terminal_pair not in ALLOWED_TERMINAL_PAIRS:
        raise ValueError(f"target is not an allowed negative terminal pair: {terminal_pair!r}")
    if target.get("comparison_verdict") not in ALLOWED_TERMINAL_VERDICTS:
        raise ValueError("target comparison verdict is not allowed for exhausted negative closure")
    if target.get("evidence_bundle") or target.get("verified_evidence"):
        raise ValueError("target already contains Evidence Bundle or verified-evidence state")
    if target.get("evidence_rows") != []:
        raise ValueError("target accepted evidence_rows must be exactly empty")
    stage_c = state["stages"]["StageC"]
    legacy = state["case"]["schema_version"] == 4
    if legacy and (stage_c.get("attempt_count"), stage_c.get("repair_count")) != (4, 3):
        raise ValueError("StageC must be exhausted at attempt_count=4 and repair_count=3")

    executor_events = _finished_events(events, state, target_id, "Executor")
    critic_events = _finished_events(events, state, target_id, "Critic")
    if len(executor_events) < (4 if legacy else 1):
        raise ValueError("target requires finished StageC Executors: four for v4, at least one for v5")
    if len(critic_events) < (4 if legacy else 1):
        raise ValueError("target requires finished StageC Critics: four for v4, at least one for v5")
    latest_critic_event, latest_critic_agent = critic_events[-1]
    registered_critic_ids = _registered_role_ids(events, target_id, "Critic")
    if not registered_critic_ids or registered_critic_ids[-1] != latest_critic_agent["agent_id"]:
        raise ValueError("latest target Critic has not finished and cannot bind a terminal report")

    critic_binding = _file_binding(case_dir, critic_report, "latest Critic report")
    critic_document = load_json(case_dir / critic_binding["path"])
    try:
        validate_document(critic_document, "critic_report")
    except SchemaValidationError as exc:
        raise ValueError(f"latest Critic report schema failure: {exc}") from exc
    if (
        critic_document.get("stage") != "StageC"
        or critic_document.get("target_id") != target_id
        or critic_document.get("reviewer_agent_id") != latest_critic_agent["agent_id"]
        or critic_document.get("decision") not in {"fail", "blocked"}
        or not critic_document.get("required_repairs")
    ):
        raise ValueError("latest Critic report is not a matching StageC fail/blocked report")
    critic_binding["decision"] = critic_document["decision"]

    supervisor_events = [
        item
        for item in _finished_events(events, state, target_id, "Supervisor")
        if item[0]["sequence"] > latest_critic_event["sequence"]
    ]
    if not supervisor_events:
        raise ValueError("a finished target Supervisor after the latest Critic is required")
    latest_supervisor_event, latest_supervisor_agent = supervisor_events[-1]
    supervisor_outcome = latest_supervisor_agent.get("outcome")
    if not isinstance(supervisor_outcome, str) or not supervisor_outcome.startswith("BLOCKED:"):
        raise ValueError("post-Critic target Supervisor outcome must begin with 'BLOCKED:'")
    terminal_reason = supervisor_outcome.split(":", 1)[1].strip()
    if not terminal_reason:
        raise ValueError("post-Critic target Supervisor BLOCKED outcome requires a terminal reason")
    supervisor_binding = _file_binding(case_dir, supervisor_report, "target Supervisor report")
    if not _handoff_contains(case_dir, latest_supervisor_agent.get("handoff_out"), supervisor_binding["path"]):
        raise ValueError("latest post-Critic target Supervisor handoff does not name the report")

    record = {
        "schema_version": state["case"]["schema_version"],
        "record_version": 1,
        "record_type": "exhausted_terminal_failure",
        "verification_status": "verified_terminal_failure",
        "target_id": target_id,
        "route": route,
        "terminal_state": {
            "run_state": target["run_state"],
            "claim_status": target["claim_status"],
            "comparison_verdict": target.get("comparison_verdict"),
            "accepted_evidence_rows": [],
            "evidence_bundle_present": False,
            "verified_evidence_present": False,
        },
        "stage_c": {"attempt_count": stage_c["attempt_count"], "repair_count": stage_c["repair_count"]},
        "executor_agent_ids": sorted(agent["agent_id"] for _, agent in executor_events),
        "critic_agent_ids": sorted(agent["agent_id"] for _, agent in critic_events),
        "latest_critic": {
            "agent_id": latest_critic_agent["agent_id"],
            "finished_event_sequence": latest_critic_event["sequence"],
            "finished_event_hash": latest_critic_event["hash"],
            "report": critic_binding,
        },
        "supervisor_advice": {
            "agent_id": latest_supervisor_agent["agent_id"],
            "finished_event_sequence": latest_supervisor_event["sequence"],
            "finished_event_hash": latest_supervisor_event["hash"],
            "outcome": supervisor_outcome,
            "terminal_reason": terminal_reason,
            "report": supervisor_binding,
        },
        "event_prefix": _event_prefix(case_dir, events, event_prefix_count),
        "engineering_evidence": _engineering_inventory(case_dir, engineering_evidence, target_id),
        "scientific_positive_allowed": False,
        "next_stage": "StageCSummary",
    }
    try:
        validate_document(record, "exhausted_terminal_record")
    except SchemaValidationError as exc:
        raise ValueError(f"exhausted-terminal record schema failure: {exc}") from exc
    return record


def create_record(
    case_dir: Path,
    target_id: str,
    critic_report: str | Path,
    supervisor_report: str | Path,
    engineering_evidence: list[str | Path],
) -> dict[str, Any]:
    """Create the deterministic standard record, then re-open and verify it."""
    from runtime_model import require_writable_case
    require_writable_case(case_dir)
    events = read_events(case_dir.resolve())
    record = _build_record(
        case_dir,
        target_id,
        critic_report,
        supervisor_report,
        engineering_evidence,
        len(events),
    )
    path = record_path(case_dir, target_id)
    atomic_write_json(path, record)
    return verify_record(case_dir, target_id)


def verify_record(case_dir: Path, target_id: str) -> dict[str, Any]:
    """Recompute every binding while allowing only valid later log appends."""
    path = record_path(case_dir, target_id)
    stored = load_json(path)
    try:
        validate_document(stored, "exhausted_terminal_record")
    except SchemaValidationError as exc:
        raise ValueError(f"exhausted-terminal record schema failure: {exc}") from exc
    if stored.get("target_id") != target_id:
        raise ValueError("exhausted-terminal record target does not match its standard path")
    expected = _build_record(
        case_dir,
        target_id,
        stored["latest_critic"]["report"]["path"],
        stored["supervisor_advice"]["report"]["path"],
        [item["root"] for item in stored["engineering_evidence"]],
        stored["event_prefix"]["event_count"],
    )
    if canonical_json_bytes(stored) != canonical_json_bytes(expected):
        raise ValueError("exhausted-terminal record differs from live runtime/evidence recomputation")
    return {
        "verification_status": "verified_terminal_failure",
        "record_path": path.relative_to(case_dir.resolve()).as_posix(),
        "record_sha256": sha256_file(path),
        "target_id": target_id,
        "claim_status": stored["terminal_state"]["claim_status"],
        "scientific_positive_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify a negative-only exhausted StageC record")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("case_dir", type=Path)
    create.add_argument("--target-id", required=True)
    create.add_argument("--critic-report", required=True)
    create.add_argument("--supervisor-report", required=True)
    create.add_argument("--engineering-evidence", action="append", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("case_dir", type=Path)
    verify.add_argument("--target-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_record(
                args.case_dir,
                args.target_id,
                args.critic_report,
                args.supervisor_report,
                args.engineering_evidence,
            )
        else:
            result = verify_record(args.case_dir, args.target_id)
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
