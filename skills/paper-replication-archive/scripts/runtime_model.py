#!/usr/bin/env python3
"""Event-sourced runtime model for paper-replication-archive v5.

SHA-256 values prove byte integrity only. They do not establish authorship,
scientific correctness, provenance truth, or code safety.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from runtime_schema import SchemaValidationError, validate_document


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SCHEMA_VERSION = 5
STAGE_ORDER = ("Stage0", "StageA", "StageB", "StageC", "StageCSummary", "StageD")
REVIEW_ROLES = {
    "Stage0": "Supervisor",
    "StageA": "Critic",
    "StageB": "Critic",
    "StageC": "Critic",
    "StageCSummary": "Critic",
    "StageD": "Critic",
}
GENESIS_HASH = None


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone offset: {value}")
    return parsed


def now_iso(value: str | None = None) -> str:
    parsed = parse_timestamp(value) if value else datetime.now().astimezone()
    return parsed.isoformat(timespec="microseconds")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    if path.is_file() and path.read_bytes() == text.encode("utf-8"):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc


def ensure_case_dirs(case_dir: Path) -> None:
    for relative in (
        "work",
        "reference/raw",
        "reference/processed",
        "reference/author_generated",
        "code",
        "results",
        "logs",
        "stage_gates",
        "reports",
        "to_obsidian",
        "02_reproduction",
    ):
        (case_dir / relative).mkdir(parents=True, exist_ok=True)


def case_path(case_dir: Path) -> Path:
    return case_dir / "work" / "case.json"


def event_log_path(case_dir: Path) -> Path:
    return case_dir / "logs" / "runtime_events.jsonl"


def state_path(case_dir: Path) -> Path:
    return case_dir / "work" / "runtime_state.json"


def registry_path(case_dir: Path) -> Path:
    return case_dir / "work" / "agent_registry.json"


def decisions_path(case_dir: Path) -> Path:
    return case_dir / "work" / "user_decisions.json"


def target_matrix_path(case_dir: Path) -> Path:
    return case_dir / "work" / "target_matrix.json"


def event_digest(event_without_hash: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(event_without_hash))


def read_events(case_dir: Path, *, validate: bool = True) -> list[dict[str, Any]]:
    path = event_log_path(case_dir)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError(f"runtime event log appears truncated (missing final newline): {path}")
    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            raise ValueError(f"blank runtime event at {path}:{line_number}")
        try:
            event = json.loads(raw_line.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid runtime event at {path}:{line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"runtime event must be an object at {path}:{line_number}")
        events.append(event)
    if validate:
        validate_event_chain(events)
    return events


def validate_event_chain(events: Iterable[dict[str, Any]]) -> None:
    previous_hash: str | None = GENESIS_HASH
    previous_time: datetime | None = None
    for expected_sequence, event in enumerate(events, start=1):
        try:
            validate_document(event, "runtime_event")
        except SchemaValidationError as exc:
            raise ValueError(f"event {expected_sequence} schema failure: {exc}") from exc
        if event["sequence"] != expected_sequence:
            raise ValueError(
                f"event sequence mismatch: expected {expected_sequence}, got {event['sequence']}"
            )
        expected_id = f"evt-{expected_sequence:06d}"
        if event["event_id"] != expected_id:
            raise ValueError(f"event id mismatch: expected {expected_id}, got {event['event_id']}")
        if event["previous_hash"] != previous_hash:
            raise ValueError(f"event {expected_sequence} has an invalid previous_hash")
        unsigned = {key: value for key, value in event.items() if key != "hash"}
        if event["hash"] != event_digest(unsigned):
            raise ValueError(f"event {expected_sequence} hash mismatch")
        current_time = parse_timestamp(event["timestamp"])
        if previous_time and current_time < previous_time:
            raise ValueError(f"event {expected_sequence} timestamp precedes the prior event")
        previous_hash = event["hash"]
        previous_time = current_time


def _append_event_unlocked(
    case_dir: Path,
    event_type: str,
    payload: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    require_writable_case(case_dir)
    events = read_events(case_dir)
    stamp = now_iso(timestamp)
    if events and parse_timestamp(stamp) < parse_timestamp(events[-1]["timestamp"]):
        raise ValueError("runtime event timestamp is earlier than the previous event")
    sequence = len(events) + 1
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "sequence": sequence,
        "event_id": f"evt-{sequence:06d}",
        "timestamp": stamp,
        "event_type": event_type,
        "previous_hash": events[-1]["hash"] if events else GENESIS_HASH,
        "payload": payload,
    }
    event = {**unsigned, "hash": event_digest(unsigned)}
    try:
        validate_document(event, "runtime_event")
    except SchemaValidationError as exc:
        raise ValueError(f"refusing invalid runtime event: {exc}") from exc
    path = event_log_path(case_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json_bytes(event) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def new_stage(stage_id: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "run_state": "not_started",
        "attempt_count": 0,
        "repair_count": 0,
        "active_agent_id": None,
        "supervisor_advice_agent_id": None,
        "preflight": {"aggregate": None, "targets": {}},
        "review": {
            "required_role": REVIEW_ROLES[stage_id],
            "agent_id": None,
            "decision": "pending",
            "report": None,
            "reason": None,
        },
        "started_at": None,
        "completed_at": None,
    }


def new_state(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": case["schema_version"],
        "case": deepcopy(case),
        "stages": {stage: new_stage(stage) for stage in STAGE_ORDER},
        "targets": {},
        "agents": {},
        "decisions": {"schema_version": case["schema_version"], "rounds": {}},
        "runtime": {
            "current_stage": "Stage0",
            "active_target": None,
            "last_event_id": None,
            "last_event_hash": None,
            "last_progress_at": case["created_at"],
            "next_action": "Spawn Stage0 Supervisor",
            "spawn_capability_confirmed": False,
            "native_progress_status": "pending",
        },
    }


def _stage(state: dict[str, Any], stage_id: str) -> dict[str, Any]:
    if stage_id not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage_id}")
    return state["stages"][stage_id]


def replay_events(case: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    state = new_state(case)
    for event in events:
        payload = event["payload"]
        kind = event["event_type"]
        if kind == "case_initialized":
            state["runtime"]["spawn_capability_confirmed"] = bool(payload["spawn_capability_confirmed"])
            state["runtime"]["native_progress_status"] = payload.get("native_progress_status", "pending")
            state["runtime"]["next_action"] = payload.get("next_action", "Spawn Stage0 Supervisor")
        elif kind == "case_migrated":
            state["runtime"]["next_action"] = payload.get("next_action", "Review migrated case")
            state["runtime"]["spawn_capability_confirmed"] = False
        elif kind == "agent_registered":
            agent = deepcopy(payload["agent"])
            state["agents"][agent["agent_id"]] = agent
            stage = _stage(state, agent["stage"])
            stage["active_agent_id"] = agent["agent_id"]
            if agent.get("target_id"):
                state["runtime"]["active_target"] = agent["target_id"]
            state["runtime"]["current_stage"] = agent["stage"]
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "agent_finished":
            agent_id = payload["agent_id"]
            if agent_id not in state["agents"]:
                raise ValueError(f"event references unknown agent: {agent_id}")
            state["agents"][agent_id].update(
                {
                    "run_state": "finished",
                    "outcome": payload["outcome"],
                    "handoff_out": payload.get("handoff_out", []),
                    "finished_at": event["timestamp"],
                    "materialization": payload.get("materialization"),
                }
            )
            stage = _stage(state, state["agents"][agent_id]["stage"])
            if stage["active_agent_id"] == agent_id:
                stage["active_agent_id"] = None
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "stage_updated":
            stage = _stage(state, payload["stage"])
            stage.update(
                {
                    "run_state": payload["run_state"],
                    "attempt_count": payload["attempt_count"],
                    "repair_count": payload["repair_count"],
                    "supervisor_advice_agent_id": payload.get("supervisor_advice_agent_id"),
                }
            )
            if case["schema_version"] == 5 and payload.get("target_id"):
                target = state["targets"].setdefault(payload["target_id"], {"target_id": payload["target_id"]})
                target["scientific_attempt_count"] = payload["attempt_count"]
            if stage["run_state"] == "in_progress" and stage["started_at"] is None:
                stage["started_at"] = event["timestamp"]
            if stage["run_state"] in {"completed", "failed", "blocked", "recorded_unresolved"}:
                stage["completed_at"] = event["timestamp"]
            state["runtime"]["current_stage"] = payload["stage"]
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "review_recorded":
            stage = _stage(state, payload["stage"])
            reviewer = state["agents"].get(payload["agent_id"])
            if not reviewer:
                raise ValueError(f"review event references unknown agent: {payload['agent_id']}")
            if reviewer.get("stage") != payload["stage"] or reviewer.get("role") != REVIEW_ROLES[payload["stage"]]:
                raise ValueError("review event agent role/stage does not match the reviewed stage")
            if reviewer.get("run_state") != "finished":
                raise ValueError("review event references a reviewer that has not finished")
            stage["review"] = {
                "required_role": REVIEW_ROLES[payload["stage"]],
                "agent_id": payload["agent_id"],
                "decision": payload["decision"],
                "report": payload["report"],
                "reason": payload.get("reason", ""),
            }
            if case['schema_version'] == 5 and payload.get('target_review'):
                state['targets'][payload['target_id']]['independent_review'] = deepcopy(payload['target_review'])
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "heartbeat_recorded":
            agent_id = payload["agent_id"]
            if agent_id in state["agents"]:
                state["agents"][agent_id]["last_heartbeat_at"] = event["timestamp"]
        elif kind == "targets_defined":
            state["target_matrix"] = deepcopy(payload["matrix"])
            for item in payload["matrix"]["targets"]:
                current = state["targets"].setdefault(item["target_id"], {})
                previous = {key: current[key] for key in DYNAMIC_TARGET_FIELDS if key in current}
                current.update(deepcopy(item))
                current.update(previous)
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "engineering_repair_recorded":
            item = state["targets"][payload["target_id"]] if payload.get("target_id") else _stage(state, payload["stage"])
            item["engineering_repair_count"] = item.get("engineering_repair_count", 0) + 1
            item["last_engineering_repair"] = deepcopy(payload)
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "target_recorded":
            target_id = payload["target_id"]
            current = state["targets"].setdefault(target_id, {"target_id": target_id})
            current.update(deepcopy(payload["target"]))
            state["runtime"]["active_target"] = target_id
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "decision_recorded":
            round_key = str(payload["round"])
            round_data = state["decisions"]["rounds"].setdefault(
                round_key, {"per_target": {}, "overall": None}
            )
            record = {
                "decision": payload["decision"],
                "reason": payload.get("reason", ""),
                "recorded_at": event["timestamp"],
                "event_id": event["event_id"],
            }
            if payload["scope"] == "target":
                round_data["per_target"][payload["target_id"]] = record
            else:
                round_data["overall"] = record
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "preflight_recorded":
            stage = _stage(state, payload["stage"])
            record = {
                "result": payload["result"],
                "report": payload["report"],
                "attempt_count": payload["attempt_count"],
                "producer_agent_id": payload.get("producer_agent_id"),
                "recorded_at": event["timestamp"],
            }
            if payload.get("target_id"):
                stage["preflight"]["targets"][payload["target_id"]] = record
            else:
                stage["preflight"]["aggregate"] = record
            state["runtime"]["next_action"] = payload["next_action"]
        elif kind == "evidence_verified":
            target_id = payload["target_id"]
            current = state["targets"].setdefault(target_id, {"target_id": target_id})
            current.update(deepcopy(payload.get("target", {})))
            current["verified_evidence"] = deepcopy(payload["verification"])
            current["claim_status"] = payload["verification"]["claim_status"]
            current["comparison_verdict"] = payload["verification"]["comparison_verdict"]
            current["evidence_bundle"] = payload["bundle"]
            state["runtime"]["next_action"] = payload["next_action"]
        else:  # pragma: no cover
            raise ValueError(f"unsupported event type during replay: {kind}")
        state["runtime"]["last_event_id"] = event["event_id"]
        state["runtime"]["last_event_hash"] = event["hash"]
        state["runtime"]["last_progress_at"] = event["timestamp"]
    try:
        validate_document(state, "runtime_state")
    except SchemaValidationError as exc:
        raise ValueError(f"replayed state violates runtime schema: {exc}") from exc
    return state


def load_case(case_dir: Path) -> dict[str, Any]:
    value = load_json(case_path(case_dir))
    try:
        validate_document(value, "case")
    except SchemaValidationError as exc:
        raise ValueError(f"case.json schema failure: {exc}") from exc
    return value


def load_state(case_dir: Path, *, audit_derived: bool = True) -> dict[str, Any]:
    case = load_case(case_dir)
    events = read_events(case_dir)
    state = replay_events(case, events)
    if audit_derived and state_path(case_dir).exists():
        stored = load_json(state_path(case_dir))
        if canonical_json_bytes(stored) != canonical_json_bytes(state):
            raise ValueError("runtime_state.json does not match event-log replay; run audit-case")
    return state


def escape_table(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_agent_trace(state: dict[str, Any]) -> str:
    lines = [
        "# Agent Trace",
        "",
        "| stage | role | agent_id | run_state | target | task | outcome | replacement_for |",
        "|---|---|---|---|---|---|---|---|",
    ]
    agents = sorted(state["agents"].values(), key=lambda item: item["registered_at"])
    if not agents:
        lines.append("| Stage0 | Orchestrator | orchestrator | initialized | - | Initialize case | initialized | - |")
    for agent in agents:
        lines.append(
            "| " + " | ".join(
                escape_table(agent.get(key))
                for key in ("stage", "role", "agent_id", "run_state", "target_id", "task", "outcome", "replacement_for")
            ) + " |"
        )
    return "\n".join(lines) + "\n"


def render_target_matrix(matrix: dict[str, Any]) -> str:
    v5 = matrix.get('schema_version') == 5
    lines = [
        "# Target Matrix",
        "",
        "> Generated from `work/target_matrix.json`. Do not edit this Markdown file.",
        "",
        "| Target | Claim | Paper anchors | Route | Run state | Claim status | Comparison | Evidence plan |" + (" Presentation |" if v5 else ""),
        "|---|---|---|---|---|---|---|---|" + ("---|" if v5 else ""),
    ]
    for target in matrix["targets"]:
        lines.append(
            "| " + " | ".join(
                (
                    escape_table(target["target_id"]),
                    escape_table(target["claim"]),
                    escape_table(target["paper_anchors"]),
                    escape_table(target["route"]),
                    escape_table(target["run_state"]),
                    escape_table(target["claim_status"]),
                    escape_table(target["comparison_verdict"]),
                    escape_table(target["evidence_plan"]),
                ) + ((escape_table(target.get('presentation_status', 'not_checked')), ) if v5 else ())
            ) + " |"
        )
    return "\n".join(lines) + "\n"


def render_progress(state: dict[str, Any], events: list[dict[str, Any]]) -> str:
    v5 = state.get('schema_version') == 5
    current_stage = state["runtime"]["current_stage"]
    active_target = state["runtime"].get("active_target")
    lines = [
        "# Live Progress / 复现进度面板",
        "",
        "> Generated from the verified event log. Do not edit this file.",
        "",
        "## Current Node / 当前节点",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Stage | {escape_table(current_stage)} |",
        f"| Target | {escape_table(active_target or 'none')} |",
        f"| Last event | {escape_table(state['runtime']['last_event_id'])} |",
        f"| Next action | {escape_table(state['runtime']['next_action'])} |",
        "",
        "## Stage Overview / 阶段总览",
        "",
        "| Stage | Run state | Attempt | Repair | Aggregate preflight | Review | Reviewer |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for stage_id in STAGE_ORDER:
        stage = state["stages"][stage_id]
        preflight = stage["preflight"]["aggregate"]
        lines.append(
            f"| {stage_id} | {escape_table(stage['run_state'])} | {stage['attempt_count']} | "
            f"{stage['repair_count']} | {escape_table(preflight['result'] if preflight else 'pending')} | "
            f"{escape_table(stage['review']['decision'])} | {escape_table(stage['review']['agent_id'])} |"
        )
    lines.extend([
        "", "## Target States / 目标状态", "",
        "| Target | Route | Run state | Claim status | Comparison | Verified bundle |" + (" Presentation |" if v5 else ""),
        "|---|---|---|---|---|---|" + ("---|" if v5 else ""),
    ])
    if not state["targets"]:
        lines.append("| - | - | not_started | NOT_ATTEMPTED | not_evaluated | - |" + (" not_checked |" if v5 else ""))
    for target_id, target in sorted(state["targets"].items()):
        verified = target.get("verified_evidence") or {}
        lines.append(
            f"| {escape_table(target_id)} | {escape_table(target.get('route'))} | "
            f"{escape_table(target.get('run_state', 'not_started'))} | "
            f"{escape_table(target.get('claim_status', 'NOT_ATTEMPTED'))} | "
            f"{escape_table(target.get('comparison_verdict', 'not_evaluated'))} | "
            f"{escape_table(verified.get('manifest_sha256'))} |"
            + (f" {escape_table(target.get('presentation_status', 'not_checked'))} |" if v5 else "")
        )
    lines.extend([
        "", "## Recent Timeline / 最近时间线", "",
        "| Sequence | Time | Event | Stage | Target |", "|---:|---|---|---|---|",
    ])
    for event in events[-30:]:
        payload = event["payload"]
        lines.append(
            f"| {event['sequence']} | {escape_table(event['timestamp'])} | {escape_table(event['event_type'])} | "
            f"{escape_table(payload.get('stage'))} | {escape_table(payload.get('target_id'))} |"
        )
    return "\n".join(lines) + "\n"


def render_chat_card(state: dict[str, Any], action: str | None = None) -> str:
    stage_id = state["runtime"]["current_stage"]
    stage = state["stages"][stage_id]
    target_id = state["runtime"].get("active_target")
    target = state["targets"].get(target_id, {}) if target_id else {}
    if state.get('schema_version') == 5:
        lines = [
            '**PRA Progress / 复现进度**', '',
            '| Stage | Run state | Aggregate preflight | Stage review | Next action |',
            '|---|---|---|---|---|',
            f"| {escape_table(stage_id)} | {escape_table(stage['run_state'])} | "
            f"{escape_table((stage['preflight']['aggregate'] or {}).get('result', 'pending'))} | "
            f"{escape_table(stage['review']['decision'])} | {escape_table(state['runtime']['next_action'])} |",
        ]
        if target:
            # Target checks belong to StageC even when a later stage retains the active target.
            preflight = state['stages']['StageC']['preflight']['targets'].get(target_id) or {}
            lines.extend([
                '', '| Target | Scientific/Engineering | Target preflight (StageC) | Target review | Presentation | Run state | Claim status |',
                '|---|---|---|---|---|---|---|',
                f"| {escape_table(target_id)} | {target.get('scientific_attempt_count', 0)}/{target.get('engineering_repair_count', 0)} | "
                f"{escape_table(preflight.get('result', 'pending'))} | "
                f"{escape_table((target.get('independent_review') or {}).get('decision', 'pending'))} | "
                f"{escape_table(target.get('presentation_status', 'not_checked'))} | "
                f"{escape_table(target.get('run_state', 'not_started'))} | "
                f"{escape_table(target.get('claim_status', 'NOT_ATTEMPTED'))} |",
            ])
        if action:
            lines.extend(['', f'action: `{action}`'])
        return '\n'.join(lines) + '\n'
    lines = [
        "**PRA Progress / 复现进度**",
        "",
        "| Stage | Target | Attempt/Repair | Preflight | Review | Run state | Claim status | Next action |",
        "|---|---|---|---|---|---|---|---|",
        f"| {escape_table(stage_id)} | {escape_table(target_id or 'none')} | "
        f"{stage['attempt_count']}/{stage['repair_count']} | "
        f"{escape_table((stage['preflight']['aggregate'] or {}).get('result', 'pending'))} | "
        f"{escape_table(stage['review']['decision'])} | "
        f"{escape_table(target.get('run_state', stage['run_state']))} | "
        f"{escape_table(target.get('claim_status', 'NOT_ATTEMPTED'))} | "
        f"{escape_table(state['runtime']['next_action'])} |",
    ]
    if action:
        lines.extend(["", f"action: `{action}`"])
    return "\n".join(lines) + "\n"


def render_completion_evidence(state: dict[str, Any]) -> str:
    v5 = state.get('schema_version') == 5
    lines = [
        "# Completion Evidence Table",
        "",
        "> Generated from runtime state and verified Evidence Index records.",
        "",
        "| Target | Claim status | Comparison | Route | Bundle | Manifest SHA-256 |" + (" Presentation |" if v5 else ""),
        "|---|---|---|---|---|---|" + ("---|" if v5 else ""),
    ]
    if not state["targets"]:
        lines.append("| - | NOT_ATTEMPTED | not_evaluated | - | - | - |" + (" not_checked |" if v5 else ""))
    for target_id, target in sorted(state["targets"].items()):
        verification = target.get("verified_evidence") or {}
        lines.append(
            f"| {escape_table(target_id)} | {escape_table(target.get('claim_status', 'NOT_ATTEMPTED'))} | "
            f"{escape_table(target.get('comparison_verdict', 'not_evaluated'))} | "
            f"{escape_table(target.get('route'))} | {escape_table(target.get('evidence_bundle'))} | "
            f"{escape_table(verification.get('manifest_sha256'))} |"
            + (f" {escape_table(target.get('presentation_status', 'not_checked'))} |" if v5 else "")
        )
    return "\n".join(lines) + "\n"


def _list_text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "-")


def render_deep_reading_pack(case_dir: Path, pack: dict[str, Any]) -> None:
    """Render the Chinese Reading Pack and registries from one JSON source."""
    try:
        validate_document(pack, "deep_reading_pack")
    except SchemaValidationError as exc:
        raise ValueError(f"deep_reading_pack.json schema failure: {exc}") from exc
    root = case_dir / "01_deep_reading"
    english = pack.get("language") == "en"
    def tr(zh, en):
        return en if english else zh
    note = [tr('# 全文精读笔记','# Full-paper reading notes'), "", "> Generated from `work/deep_reading_pack.json`.", ""]
    for section in pack["note_sections"]:
        note.extend([f"## {section.get('heading', '未命名章节')}", "", str(section.get("content", "")), ""])
    atomic_write_text(root / "deep_reading_note.md", "\n".join(note))

    structure = [
        tr('# 论文结构图','# Paper structure'),
        "",
        "| Section | Pages | Purpose | Evidence anchors |",
        "|---|---|---|---|",
    ]
    for item in pack["structure"]:
        structure.append(
            f"| {escape_table(item.get('section'))} | {escape_table(item.get('pages'))} | "
            f"{escape_table(item.get('purpose'))} | {escape_table(item.get('evidence_anchors'))} |"
        )
    atomic_write_text(root / "paper_structure_map.md", "\n".join(structure) + "\n")

    formulas = [tr('# 公式来源与推导','# Formula provenance and derivation'), "", "> Formula bodies stay outside Markdown tables.", ""]
    for item in pack["formulas"]:
        formula_id = item.get("formula_id", "Formula-UNKNOWN")
        formulas.extend([f"## {tr('公式', 'Formula')} {formula_id}", "", str(item.get("explanation", item.get("explanation_zh", ""))), ""])
        expression = item.get("expression")
        if expression:
            formulas.extend(["$$", str(expression), "$$", ""])
        fields = (
            ("来源", "source"), ("推导路径", "derivation_path"), ("假设", "assumptions"),
            ("适用条件", "conditions"), ("符号", "symbols"), ("图表关联", "figure_links"),
            ("代码映射预告", "code_mapping_preview"), ("证据锚点", "evidence_anchor"),
            ("不确定性", "uncertainty"),
        )
        formulas.extend(f"- {key.replace('_', ' ') if english else label}: {_list_text(item.get(key))}" for label, key in fields)
        formulas.append("")
    atomic_write_text(root / "formula_explanation.md", "\n".join(formulas))

    figures = [tr('# 图表总览','# Figure overview'), "", "| Figure/Panel | Quantity and axes | Method | Reproduction class | Evidence anchor |", "|---|---|---|---|---|"]
    for item in pack["figures"]:
        figures.append(
            f"| {escape_table(item.get('panel_id') or item.get('figure_id'))} | "
            f"{escape_table(item.get('quantity_axes'))} | {escape_table(item.get('method'))} | "
            f"{escape_table(item.get('reproduction_class'))} | {escape_table(item.get('evidence_anchor'))} |"
        )
    atomic_write_text(root / "figure_overview.md", "\n".join(figures) + "\n")

    coverage = [tr('# 全文覆盖表','# Full-paper coverage'), "", "| Stable ID | Type | Paper anchor | Covered | Notes |", "|---|---|---|---|---|"]
    for item in pack["coverage"]:
        coverage.append(
            f"| {escape_table(item.get('stable_id'))} | {escape_table(item.get('type'))} | "
            f"{escape_table(item.get('paper_anchor'))} | {escape_table(item.get('covered'))} | "
            f"{escape_table(item.get('notes'))} |"
        )
    atomic_write_text(root / "deep_reading_coverage_table.md", "\n".join(coverage) + "\n")
    atomic_write_json(root / "figure_semantic_map.json", {"schema_version": pack["schema_version"], "figures": pack["figures"]})
    atomic_write_json(root / "formula_list.json", {"schema_version": pack["schema_version"], "formulas": pack["formulas"]})
    atomic_write_json(root / "claim_registry.json", {"schema_version": pack["schema_version"], "claims": pack["claims"]})
    atomic_write_json(root / "parameter_registry.json", {"schema_version": pack["schema_version"], "parameters": pack["parameters"]})


def render_critic_report(report: dict[str, Any]) -> str:
    try:
        validate_document(report, "critic_report")
    except SchemaValidationError as exc:
        raise ValueError(f"Critic report schema failure: {exc}") from exc
    lines = [
        f"# {report['stage']} Critic Report",
        "",
        f"- Target: `{report['target_id'] or 'overall'}`",
        f"- Reviewer agent: `{report['reviewer_agent_id']}`",
        f"- Decision: `{report['decision']}`",
        "",
        "## Summary",
        "",
        report["summary"],
        "",
        "## Findings",
        "",
    ]
    for item in report["findings"]:
        lines.append(f"- {_list_text(item)}")
    lines.extend(["", "## Required Repairs", ""])
    lines.extend(f"- {item}" for item in report["required_repairs"] or ["None"])
    lines.extend(["", "## Evidence Reviewed", ""])
    lines.extend(f"- `{item}`" for item in report["evidence_reviewed"])
    return "\n".join(lines) + "\n"


def _render_legacy_summary(summary: dict[str, Any]) -> str:
    def scalar(value: Any) -> str:
        if value is None or value == "":
            return "-"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).replace("\n", " ")

    def append_bullets(lines: list[str], value: Any, *, indent: int = 0) -> None:
        prefix = "  " * indent
        if isinstance(value, dict):
            if not value:
                lines.append(f"{prefix}- 无")
            for key, nested in value.items():
                if isinstance(nested, (dict, list)):
                    lines.append(f"{prefix}- {key}:")
                    append_bullets(lines, nested, indent=indent + 1)
                else:
                    lines.append(f"{prefix}- {key}: {scalar(nested)}")
            return
        if isinstance(value, list):
            if not value:
                lines.append(f"{prefix}- 无")
            for item in value:
                if isinstance(item, (dict, list)):
                    append_bullets(lines, item, indent=indent)
                else:
                    lines.append(f"{prefix}- {scalar(item)}")
            return
        lines.append(f"{prefix}- {scalar(value)}")

    lines = [
        "# Stage C Summary / 系统汇报",
        "",
        f"- Schema version: `{summary.get('schema_version')}`",
        f"- Round: `{summary.get('round')}`",
        "",
        "## 本轮实际完成内容",
        "",
    ]
    lines.extend(f"- {item}" for item in summary.get("actual_work", []))
    lines.extend(["", "## Critic Fail 原因汇总", ""])
    failures = summary.get("critic_failures", [])
    if not failures:
        lines.append("- 无")
    for failure in failures:
        if not isinstance(failure, dict):
            lines.append(f"- {scalar(failure)}")
            continue
        heading = f"{failure.get('stage', 'Unknown stage')} attempt {failure.get('attempt', '-')}"
        lines.extend([f"### {heading}", ""])
        for key in ("reviewer_agent_id", "report", "decision"):
            if key in failure:
                lines.append(f"- {key}: {scalar(failure[key])}")
        lines.append("- reasons:")
        append_bullets(lines, failure.get("reasons", []), indent=1)
        for key in ("repair", "closure"):
            if key in failure:
                lines.append(f"- {key}: {scalar(failure[key])}")
        extra = {key: value for key, value in failure.items() if key not in {"stage", "attempt", "reviewer_agent_id", "report", "decision", "reasons", "repair", "closure"}}
        if extra:
            lines.append("- other:")
            append_bullets(lines, extra, indent=1)
        lines.append("")

    lines.extend(["", "## Target 原文对比结论", ""])
    for target_id, value in summary.get("targets", {}).items():
        lines.extend([f"### {target_id}", ""])
        if not isinstance(value, dict):
            lines.extend([f"- State: {scalar(value)}", ""])
            continue
        for label, key in (
            ("Claim", "claim_id"),
            ("Route", "route"),
            ("Run state", "run_state"),
            ("Claim status", "claim_status"),
            ("Comparison verdict", "comparison_verdict"),
        ):
            lines.append(f"- {label}: `{scalar(value.get(key))}`")

        if value.get("paper_anchors"):
            lines.extend(["", "#### 论文锚点", ""])
            append_bullets(lines, value["paper_anchors"])

        comparison_contract = value.get("comparison_contract", {})
        if comparison_contract:
            lines.extend(["", "#### 比较合同", ""])
            append_bullets(lines, comparison_contract)

        points = value.get("recalculated_points", [])
        if points:
            unit_conversion = comparison_contract.get("unit_conversion", {}) if isinstance(comparison_contract, dict) else {}
            scale = unit_conversion.get("scale") if isinstance(unit_conversion, dict) else None
            offset = unit_conversion.get("offset") if isinstance(unit_conversion, dict) else None
            converted_label = "converted"
            if isinstance(scale, (int, float)) and not isinstance(scale, bool) and offset in (0, 0.0, None):
                converted_label = f"×{scale:g}"
            lines.extend([
                "",
                f"#### 逐点数值对比（{len(points)} 点）",
                "",
                f"| J | raw Eq. (3) | {converted_label} | reference | abs error | tolerance | pass |",
                "|---:|---:|---:|---:|---:|---:|:---:|",
            ])
            for point in points:
                passed = point.get("passed")
                pass_text = "是" if passed is True else "否" if passed is False else scalar(passed)
                lines.append(
                    f"| {escape_table(point.get('J'))} | {escape_table(point.get('raw_eq3'))} | "
                    f"{escape_table(point.get('converted'))} | {escape_table(point.get('reference'))} | "
                    f"{escape_table(point.get('absolute_error'))} | {escape_table(point.get('tolerance'))} | "
                    f"{escape_table(pass_text)} |"
                )

        for heading, key in (
            ("数值汇总", "numeric_summary"),
            ("执行记录", "execution"),
            ("完整性记录", "integrity"),
            ("工程预检事件", "engineering_incident"),
        ):
            if value.get(key):
                lines.extend(["", f"#### {heading}", ""])
                append_bullets(lines, value[key])

        warning = value.get("mandatory_reporting_warning")
        if isinstance(warning, dict):
            lines.extend([
                "",
                "> [!WARNING] comparison.json.actual_raw 字段误标",
                f"> {scalar(warning.get('defect'))}",
                ">",
                "> **真实 raw 路径**",
            ])
            raw_sources = warning.get("correct_raw_sources", [])
            if raw_sources:
                lines.extend(f"> - `{scalar(source)}`" for source in raw_sources)
            else:
                lines.append("> - 未提供")
            for label, key in (
                ("正确 converted 路径", "correct_converted_source"),
                ("正确 reference 路径", "correct_reference_source"),
                ("对 verdict 的影响", "effect_on_verdict"),
                ("保留规则", "preservation_rule"),
                ("严重度", "severity"),
            ):
                if key in warning:
                    lines.append(f"> - **{label}**: {scalar(warning[key])}")
        elif warning:
            lines.extend(["", "> [!WARNING] mandatory_reporting_warning", f"> {scalar(warning)}"])

        if value.get("allowed_scientific_conclusion_zh"):
            lines.extend(["", "#### 允许的科学结论", "", scalar(value["allowed_scientific_conclusion_zh"])])
        if value.get("not_established"):
            lines.extend(["", "#### 未建立 / not_established", ""])
            append_bullets(lines, value["not_established"])

        handled = {
            "claim_id", "route", "run_state", "claim_status", "comparison_verdict", "paper_anchors",
            "comparison_contract", "recalculated_points", "numeric_summary", "execution", "integrity",
            "mandatory_reporting_warning", "engineering_incident", "allowed_scientific_conclusion_zh",
            "not_established",
        }
        extra = {key: nested for key, nested in value.items() if key not in handled}
        if extra:
            lines.extend(["", "#### 其他结构化事实", ""])
            append_bullets(lines, extra)
        lines.append("")

    if summary.get("omissions"):
        lines.extend(["", "## 明确未做 / omissions", ""])
        append_bullets(lines, summary["omissions"])
    lines.extend(["", "## 最重要结论", "", str(summary.get("most_important_conclusion", "")), "", "## 关键验收文件", ""])
    lines.extend(f"- `{item}`" for item in summary.get("acceptance_files", []))
    lines.extend(["", "## 状态驱动决策面板", ""])
    panels = summary.get("decision_panels", {})
    if not panels:
        lines.append("- 无")
    for target_id, panel in panels.items():
        lines.extend([f"### {target_id}", ""])
        append_bullets(lines, panel)
        lines.append("")
    if summary.get("overall_user_decision"):
        lines.extend(["", "## 总体用户决定", ""])
        append_bullets(lines, summary["overall_user_decision"])
    return "\n".join(lines) + "\n"


def _render_legacy_archive(archive: dict[str, Any]) -> str:
    try:
        validate_document(archive, "archive")
    except SchemaValidationError as exc:
        raise ValueError(f"archive.json schema failure: {exc}") from exc

    labels = {
        "paper_id": "Paper ID",
        "title": "标题",
        "authors": "作者",
        "journal": "期刊",
        "volume": "卷",
        "article_number": "文章号",
        "publication_year": "年份",
        "doi": "DOI",
        "source_url_or_doi": "来源",
        "local_file": "本地文件",
        "sha256": "SHA-256",
        "license_status": "许可证状态",
        "target_id": "Target",
        "claim_id": "Claim ID",
        "status": "状态",
        "claim_status": "Claim status",
        "route": "复现路线",
        "comparison_verdict": "比较结论",
        "manifest_sha256": "Manifest SHA-256",
        "allowed_wording": "允许表述（逐字）",
        "kind": "类型",
        "description": "说明",
        "items": "未建立事项",
    }

    def scalar(value: Any) -> str:
        if value is None or value == "":
            return "-"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).replace("\n", " ")

    def append_field(lines: list[str], key: str, value: Any, *, indent: int = 0) -> None:
        prefix = "  " * indent
        label = labels.get(key, key)
        if isinstance(value, dict):
            lines.append(f"{prefix}- {label}:")
            if not value:
                lines.append(f"{prefix}  - 无")
            for child_key, child_value in value.items():
                append_field(lines, child_key, child_value, indent=indent + 1)
            return
        if isinstance(value, list):
            lines.append(f"{prefix}- {label}:")
            if not value:
                lines.append(f"{prefix}  - 无")
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"{prefix}  - 条目:")
                    for child_key, child_value in item.items():
                        append_field(lines, child_key, child_value, indent=indent + 2)
                else:
                    lines.append(f"{prefix}  - {scalar(item)}")
            return
        lines.append(f"{prefix}- {label}: {scalar(value)}")

    lines = [
        f"# {archive['title']}",
        "",
        "## 论文信息",
        "",
    ]
    for key, value in archive["paper"].items():
        append_field(lines, key, value)
    lines.extend([
        "",
        "## 精读摘要",
        "",
        archive["reading_summary"],
        "",
        "## 经验证 Claims",
        "",
    ])
    if not archive["claims"]:
        lines.append("- 无")
    for index, claim in enumerate(archive["claims"], start=1):
        heading = claim.get("target_id") or claim.get("claim_id") or f"Claim {index}"
        lines.extend([f"### {heading}", ""])
        for key, value in claim.items():
            append_field(lines, key, value)

    lines.extend(["", "## 负结果", ""])
    if not archive["negative_results"]:
        lines.append("- 无")
    for index, item in enumerate(archive["negative_results"], start=1):
        heading = item.get("kind") or item.get("result_id") or f"负结果 {index}"
        lines.extend([f"### {heading}", ""])
        for key, value in item.items():
            append_field(lines, key, value)

    lines.extend(["", "## 限制、阻塞与强制警告", ""])
    if not archive["blockers"]:
        lines.append("- 无")
    for index, item in enumerate(archive["blockers"], start=1):
        heading = item.get("kind") or f"限制 {index}"
        lines.extend([f"### {heading}", ""])
        for key, value in item.items():
            append_field(lines, key, value)

    lines.extend(["", "## 完整性文件", ""])
    lines.extend(f"- `{item}`" for item in archive["integrity_files"])
    return "\n".join(lines) + "\n"


def render_case(case_dir: Path, *, state: dict[str, Any] | None = None, artifacts: bool = True) -> dict[str, Any]:
    require_writable_case(case_dir)
    case = load_case(case_dir)
    events = read_events(case_dir)
    derived = state or replay_events(case, events)
    atomic_write_json(state_path(case_dir), derived)
    registry = {
        "schema_version": SCHEMA_VERSION,
        "agents": sorted(derived["agents"].values(), key=lambda item: item["registered_at"]),
    }
    atomic_write_json(registry_path(case_dir), registry)
    atomic_write_json(decisions_path(case_dir), derived["decisions"])
    atomic_write_text(case_dir / "logs" / "agent_trace.md", render_agent_trace(derived))
    atomic_write_text(case_dir / "reports" / "live_progress.md", render_progress(derived, events))
    atomic_write_text(
        case_dir / "reports" / "completion_evidence_table.md",
        render_completion_evidence(derived),
    )
    matrix_path = target_matrix_path(case_dir)
    if derived.get("target_matrix"):
        matrix = materialize_matrix(derived)
        atomic_write_json(matrix_path, matrix)
    if matrix_path.exists():
        matrix = load_json(matrix_path)
        try:
            validate_document(matrix, "target_matrix")
        except SchemaValidationError as exc:
            raise ValueError(f"target_matrix.json schema failure: {exc}") from exc
        if matrix["case_id"] != case["case_id"]:
            raise ValueError("target_matrix.json case_id does not match case.json")
        atomic_write_text(case_dir / "02_reproduction" / "target_matrix.md", render_target_matrix(matrix))
    if not artifacts:
        return derived
    reading_pack_path = case_dir / "work" / "deep_reading_pack.json"
    if reading_pack_path.exists():
        render_deep_reading_pack(case_dir, load_json(reading_pack_path))
    critic_root = case_dir / "work" / "critic_reports"
    if critic_root.is_dir():
        for report_path in sorted(critic_root.glob("*.json")):
            report = load_json(report_path)
            atomic_write_text(case_dir / "reports" / "critics" / f"{report_path.stem}.md", render_critic_report(report))
    summary_path = case_dir / "reports" / "stage_c_summary.json"
    if summary_path.exists():
        atomic_write_text(case_dir / "reports" / "stage_c_summary.md", render_stage_c_summary(load_json(summary_path), language=case.get("language", "zh-CN"), state=derived))
    archive_path = case_dir / "work" / "archive.json"
    if archive_path.exists():
        atomic_write_text(case_dir / "to_obsidian" / "Paper_Reproduction_Archive.md", render_archive(load_json(archive_path), language=case.get("language", "zh-CN")))
    return derived


def audit_case(case_dir: Path, *, require_rendered_match: bool = True) -> dict[str, Any]:
    case = load_case(case_dir)
    events = read_events(case_dir)
    if not events:
        raise ValueError("case has no runtime events")
    first = events[0]
    if first["event_type"] not in {"case_initialized", "case_migrated"}:
        raise ValueError("first event must initialize or migrate the case")
    expected_case_hash = sha256_bytes(canonical_json_bytes(case))
    if first["payload"].get("case_sha256") != expected_case_hash:
        raise ValueError("case.json hash does not match the first event")
    derived = replay_events(case, events)
    if require_rendered_match:
        if not state_path(case_dir).exists():
            raise ValueError("derived runtime_state.json is missing")
        if canonical_json_bytes(load_json(state_path(case_dir))) != canonical_json_bytes(derived):
            raise ValueError("derived runtime_state.json differs from event-log replay")
        expected_registry = {
            "schema_version": case["schema_version"],
            "agents": sorted(derived["agents"].values(), key=lambda item: item["registered_at"]),
        }
        if not registry_path(case_dir).exists() or canonical_json_bytes(load_json(registry_path(case_dir))) != canonical_json_bytes(expected_registry):
            raise ValueError("agent_registry.json differs from event-log replay")
        if not decisions_path(case_dir).exists() or canonical_json_bytes(load_json(decisions_path(case_dir))) != canonical_json_bytes(derived["decisions"]):
            raise ValueError("user_decisions.json differs from event-log replay")
    matrix_path = target_matrix_path(case_dir)
    if matrix_path.exists():
        matrix = load_json(matrix_path)
        try:
            validate_document(matrix, "target_matrix")
        except SchemaValidationError as exc:
            raise ValueError(f"target_matrix.json schema failure: {exc}") from exc
        if matrix["case_id"] != case["case_id"]:
            raise ValueError("target matrix belongs to another case")
    discrepancies = []
    if matrix_path.exists():
        for item in matrix["targets"]:
            live = derived["targets"].get(item["target_id"], {})
            for key in DYNAMIC_TARGET_FIELDS:
                if key in item and key in live and item[key] != live[key]:
                    discrepancies.append(f"{item['target_id']}.{key}: matrix differs from replay")
    if case["schema_version"] == 5 and discrepancies:
        raise ValueError("; ".join(discrepancies))
    if case["schema_version"] == 5 and derived.get("target_matrix"):
        if not matrix_path.exists() or canonical_json_bytes(matrix) != canonical_json_bytes(materialize_matrix(derived)):
            raise ValueError("target_matrix.json differs from event-log projection; run render-case")
    return {
        "ok": True,
        "read_only_legacy": case["schema_version"] == 4,
        "view_discrepancies": discrepancies,
        "case_id": case["case_id"],
        "event_count": len(events),
        "last_event_hash": events[-1]["hash"],
        "derived_state_sha256": sha256_bytes(canonical_json_bytes(derived)),
    }


DYNAMIC_TARGET_FIELDS = ("run_state", "claim_status", "comparison_verdict", "presentation_status",
                         "scientific_attempt_count", "engineering_repair_count")

def require_writable_case(case_dir):
    if case_path(case_dir).exists() and load_case(case_dir)["schema_version"] != 5:
        raise ValueError("v4 cases are read-only: audit or export to a separate destination; do not rewrite history")

def append_event(case_dir, event_type, payload, *, timestamp=None):
    require_writable_case(case_dir)
    path = event_log_path(case_dir).with_suffix('.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('case event writer is busy; observe/retry the same operation, do not ask for authorization') from exc
    except PermissionError as exc:
        # Windows can report access denied while another writer's lock is being
        # deleted. Keep the event untouched and expose a bounded-retry condition;
        # persistent errors still require inspecting the actual filesystem access.
        raise ValueError(f'case event writer is busy or lock is inaccessible; retry briefly, then inspect filesystem access: {exc}') from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        return _append_event_unlocked(case_dir, event_type, payload, timestamp=timestamp)
    finally:
        os.close(descriptor)
        path.unlink()


def materialize_matrix(state):
    matrix = deepcopy(state["target_matrix"])
    for target in matrix["targets"]:
        current = state["targets"].get(target["target_id"], {})
        for key in DYNAMIC_TARGET_FIELDS:
            if key in current:
                target[key] = current[key]
    return matrix


def render_stage_c_summary(summary, *, language=None, state=None):
    if summary.get("schema_version") == 4:
        return _render_legacy_summary(summary)
    from report_language import structured_report
    language = language or summary.get("language", "zh-CN")
    return structured_report("Reproduction results" if language == "en" else "复现结果", summary, language, state=state)


def render_archive(archive, *, language=None):
    if archive.get("schema_version") == 4:
        return _render_legacy_archive(archive)
    from report_language import structured_report
    language = language or archive.get("language", "zh-CN")
    return structured_report(archive.get("title", "Paper archive" if language == "en" else "论文档案"), archive, language)
