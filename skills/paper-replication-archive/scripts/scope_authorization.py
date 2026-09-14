#!/usr/bin/env python3
"""Task-scope grants and exact run receipts. Records are not identity attestation.

Only an actual user instruction may establish a grant. A reviewed code repair can
inherit that grant; it cannot broaden the scientific contract or resource scope.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import json
import os
import uuid

from runtime_model import (atomic_write_json, canonical_json_bytes, load_json,
                           now_iso, sha256_bytes, sha256_file)
from runtime_schema import validate_document


def contract(plan):
    """Fields whose change needs a scope decision, unlike source-code hashes."""
    return {key: plan[key] for key in (
        "target_id", "claim", "scientific_contract", "execution_location",
        "command", "observation", "comparison",
    )} | {
        "source_path": str(Path(plan["source"]["path"]).resolve()),
        "capabilities": plan["capabilities"],
        "target_kind": plan.get("target_kind", "paper_result"),
        "operation_kind": plan.get("operation_kind", "scientific"),
    }


def _number(value, name, *, integer=False):
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")


def validate_scope(scope):
    validate_document(scope, 'task_scope')
    if scope.get("schema_version") != 5 or scope.get("kind") != "task_scope":
        raise ValueError("unsupported task scope")
    if scope.get("policy") not in {"task_scoped", "preview_only", "per_run"}:
        raise ValueError("scope requires an explicit execution policy")
    instruction = scope.get("instruction", {})
    if not instruction.get("text", "").strip() or not instruction.get("source_ref", "").strip():
        raise ValueError("scope requires the actual user instruction and its source reference")
    if not scope.get("targets") or not scope.get("scope_id"):
        raise ValueError("scope requires targets and a stable id")
    for key in ("wall_time_seconds", "disk_mb", "max_runs"):
        _number(scope.get("total_budget", {}).get(key), key, integer=key == "max_runs")
    return scope


def create_scope(plans, output, instruction, source_ref, *, wall_time_seconds, disk_mb, max_runs, policy="task_scoped"):
    from evidence_runtime import load_plan, preview_plan
    if output.exists() or ledger_path(output).exists():
        raise ValueError("scope already exists; preserve it and create a new scope for a real user change")
    targets = {}
    for path in plans:
        plan = load_plan(path)
        if plan["schema_version"] != 5:
            raise ValueError("v4 evidence plans are read-only; create a new v5 plan")
        preview = preview_plan(path)
        if preview["status"] != "preview_ready":
            raise ValueError("resolve preview blockers before recording scope: " + "; ".join(preview["blockers"]))
        if plan["target_id"] in targets:
            raise ValueError("duplicate target in task scope")
        targets[plan["target_id"]] = {"contract": contract(plan), "per_run_budget": plan["budget"],
                                      "initial_plan_sha256": sha256_file(path)}
    scope = validate_scope({"schema_version": 5, "kind": "task_scope", "scope_id": str(uuid.uuid4()),
        "recorded_at": now_iso(), "policy": policy, "instruction": {"text": instruction, "source_ref": source_ref},
        "targets": targets, "total_budget": {"wall_time_seconds": wall_time_seconds, "disk_mb": disk_mb, "max_runs": max_runs}})
    atomic_write_json(output, scope)
    atomic_write_json(ledger_path(output), {"schema_version": 5, "scope_sha256": sha256_file(output), "runs": {}})
    return scope


def scope_differences(scope, plan):
    validate_scope(scope)
    if scope["policy"] != "task_scoped":
        return [f"execution policy is {scope['policy']}; inherited execution is unavailable"]
    permitted = scope["targets"].get(plan["target_id"])
    if not permitted:
        return [f"target {plan['target_id']} is outside the task scope"]
    current = contract(plan)
    if plan.get("implementation_repair_reason", "").strip():
        old = permitted["contract"]["command"]
        new = plan["command"]
        if (new["argv"][0] == old["argv"][0] and new["cwd"] == old["cwd"]
                and new["environment"] == old["environment"]):
            # The reason is not authorization: the original scientific contract
            # remains binding, and validate_review requires explicit independent verification.
            current["command"] = old
    presentation = plan.get("operation_kind") == "presentation"
    if presentation:
        from evidence_runtime import verify_bundle
        parent = plan.get("presentation_of", {})
        try:
            bundle = Path(parent["bundle"])
            verified = verify_bundle(bundle)
            if (verified["verification_status"] == "invalid" or verified["target_id"] != plan["target_id"]
                    or verified["manifest_sha256"] != parent["manifest_sha256"]):
                return ["presentation must bind the unchanged current numerical Evidence Bundle"]
            parent_plan = load_json(bundle / "plan.json")
            if parent_plan.get("operation_kind", "scientific") != "scientific":
                return ["presentation must derive directly from numerical evidence"]
            hashes = {item["sha256"] for item in load_json(bundle / "run_record.json")["output_inventory"]}
            if not hashes.intersection(item["sha256"] for item in plan["inputs"]):
                return ["presentation must reuse a hashed numerical output as an input"]
            if plan["comparison"]["type"] not in {"manual", "visual"}:
                return ["presentation repairs cannot establish a new scientific comparison"]
            # Same interpreter, source root, environment, location and declared
            # capabilities. New rendering code/argv receives independent review.
            old_command = permitted["contract"]["command"]
            if (plan["command"]["argv"][0] != old_command["argv"][0]
                    or plan["command"]["environment"] != old_command["environment"]
                    or plan["command"]["cwd"] != old_command["cwd"]):
                return ["presentation changes the execution environment outside its grant"]
        except (ValueError, OSError, KeyError) as exc:
            return [f"presentation evidence is unavailable: {exc}"]
    changes = [f"task boundary changed: {key}" for key, value in current.items()
               if not (presentation and key in {"command", "operation_kind", "observation", "comparison"})
               if canonical_json_bytes(value) != canonical_json_bytes(permitted["contract"].get(key))]
    old_budget = permitted["per_run_budget"]
    for key, value in plan["budget"].items():
        old = old_budget.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if old is None or value > old:
                changes.append(f"per-run budget increased: {key}")
        elif value != old:
            if key == "memory_mb" and isinstance(value, dict) and isinstance(old, dict):
                reduced = (isinstance(value.get("value"), (int, float)) and not isinstance(value.get("value"), bool)
                           and isinstance(old.get("value"), (int, float))
                           and 0 < value["value"] <= old["value"]
                           and {k: v for k, v in value.items() if k != "value"} == {k: v for k, v in old.items() if k != "value"})
                if reduced:
                    continue
            changes.append(f"resource capability changed: {key}")
    return changes


def validate_review(review, plan, plan_hash):
    validate_document(review, 'execution_review')
    if (review.get("decision") != "pass" or review.get("source") != "platform_spawn_result"
            or not review.get("reviewer_agent_id") or not review.get("producer_agent_id")
            or review["reviewer_agent_id"] == review["producer_agent_id"]
            or review.get("plan_sha256") != plan_hash
            or review.get("source_tree_hash") != plan["source"]["tree_hash"]):
        raise ValueError("run binding requires an independent native review of the current plan and source")
    if not review.get("findings", "").strip():
        raise ValueError("review must explain what was checked")
    if plan.get("implementation_repair_reason") and review.get("implementation_repair_verified") is not True:
        raise ValueError("independent review must explicitly verify the implementation repair preserves the scientific scope")


def bind_plan(plan_path, scope_path, review_path, output):
    from evidence_runtime import load_plan, preview_plan
    plan = load_plan(plan_path)
    if plan["schema_version"] != 5:
        raise ValueError("legacy plans cannot inherit a v5 task grant")
    preview = preview_plan(plan_path)
    if preview["status"] != "preview_ready":
        raise ValueError("repair preview blockers before binding: " + "; ".join(preview["blockers"]))
    scope = load_json(scope_path)
    differences = scope_differences(scope, plan)
    if differences:
        raise ValueError("scope decision required: " + "; ".join(differences))
    review = load_json(review_path)
    validate_review(review, plan, sha256_file(plan_path))
    # Validate existing accounting before generating another receipt.
    load_ledger(scope_path)
    receipt = {"schema_version": 5, "kind": "run_authorization", "plan_id": plan["plan_id"],
        "plan_sha256": sha256_file(plan_path), "source_tree_hash": plan["source"]["tree_hash"],
        "input_sha256": sha256_bytes(canonical_json_bytes(plan["inputs"])), "allow_execute": True,
        "origin": "inherited_task_scope", "scope_path": str(scope_path.resolve()),
        "scope_sha256": sha256_file(scope_path), "scope": scope,
        "review": review, "review_sha256": sha256_file(review_path), "bound_at": now_iso()}
    atomic_write_json(output, receipt)
    return receipt


def validate_receipt(receipt, plan, plan_hash, *, live=False):
    validate_document(receipt, 'run_authorization')
    if receipt.get("kind") != "run_authorization" or receipt.get("schema_version") != 5:
        raise ValueError("unsupported run authorization receipt")
    if receipt.get("allow_execute") is not True or receipt.get("origin") != "inherited_task_scope":
        raise ValueError("receipt cannot create or impersonate user authorization")
    if (receipt.get("plan_sha256") != plan_hash or receipt.get("plan_id") != plan["plan_id"]
            or receipt.get("source_tree_hash") != plan["source"]["tree_hash"]
            or receipt.get("input_sha256") != sha256_bytes(canonical_json_bytes(plan["inputs"]))):
        raise ValueError("stale run receipt; revalidate the repair and bind it to the existing scope")
    differences = scope_differences(receipt["scope"], plan)
    if differences:
        raise ValueError("scope decision required: " + "; ".join(differences))
    validate_review(receipt["review"], plan, plan_hash)
    if live:
        scope_path = Path(receipt["scope_path"])
        if sha256_file(scope_path) != receipt["scope_sha256"] or load_json(scope_path) != receipt["scope"]:
            raise ValueError("task scope changed; inspect the current user instruction")
        load_ledger(scope_path)
    return receipt


def ledger_path(scope_path):
    return scope_path.with_name(scope_path.stem + '.usage.json')


def load_ledger(scope_path):
    ledger = load_json(ledger_path(scope_path))
    if ledger.get("scope_sha256") != sha256_file(scope_path) or not isinstance(ledger.get("runs"), dict):
        raise ValueError("scope accounting is missing or does not match the grant; do not reset it")
    return ledger


@contextmanager
def ledger_lock(scope_path):
    path = scope_path.with_name(scope_path.stem + '.usage.lock')
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("scope accounting is locked; inspect the owning process before recovery") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink()


def reserve_run(receipt, plan, bundle, run_id):
    path = Path(receipt["scope_path"])
    with ledger_lock(path):
        ledger = load_ledger(path)
        runs = ledger["runs"]
        if any(r["status"] == "reserved" for r in runs.values()):
            raise ValueError("an existing run owns this task scope; inspect/resume it before starting another")
        total = receipt["scope"]["total_budget"]
        if len(runs) >= total["max_runs"]:
            raise ValueError("cumulative run budget exhausted")
        if any(r.get("outcome") == "failed" and r["plan_sha256"] == receipt["plan_sha256"] for r in runs.values()):
            raise ValueError("same failed plan has no new repair evidence; diagnose and record the repair before retrying")
        for key in ("wall_time_seconds", "disk_mb"):
            if sum(r[key] for r in runs.values()) + plan["budget"][key] > total[key]:
                raise ValueError(f"cumulative {key} budget would be exceeded")
        scientific = plan.get("operation_kind", "scientific") == "scientific"
        attempts = sum(r.get("scientific", True) and r["target_id"] == plan["target_id"] for r in runs.values())
        if scientific and attempts >= 4:
            raise ValueError("four scientific attempts exhausted for target")
        runs[run_id] = {"status": "reserved", "target_id": plan["target_id"], "scientific": scientific,
            "bundle": str(bundle), "wall_time_seconds": plan["budget"]["wall_time_seconds"],
            "disk_mb": plan["budget"]["disk_mb"], "plan_sha256": receipt["plan_sha256"]}
        atomic_write_json(ledger_path(path), ledger)


def finish_run(receipt, run_id, elapsed, disk_mb, *, scientific_started=True, outcome=None):
    path = Path(receipt["scope_path"])
    with ledger_lock(path):
        ledger = load_ledger(path)
        run = ledger["runs"][run_id]
        run.update(status="finished", wall_time_seconds=max(0, elapsed), disk_mb=max(0, disk_mb))
        if outcome is not None:
            run['outcome'] = outcome
        if not scientific_started:
            run['scientific'] = False
        atomic_write_json(ledger_path(path), ledger)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("record-scope")
    create.add_argument("--plan", action="append", type=Path, required=True)
    create.add_argument("--instruction-file", type=Path, required=True)
    create.add_argument("--source-ref", required=True)
    create.add_argument("--policy", choices=["task_scoped", "preview_only", "per_run"], default="task_scoped")
    create.add_argument("--total-wall-time-seconds", type=float, required=True)
    create.add_argument("--total-disk-mb", type=float, required=True)
    create.add_argument("--max-runs", type=int, required=True)
    create.add_argument("--output", type=Path, required=True)
    bind = sub.add_parser("bind")
    for name in ("plan", "scope", "review", "output"):
        bind.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "record-scope":
            result = create_scope(args.plan, args.output, args.instruction_file.read_text(encoding="utf-8-sig"), args.source_ref,
                wall_time_seconds=args.total_wall_time_seconds, disk_mb=args.total_disk_mb, max_runs=args.max_runs, policy=args.policy)
        else:
            result = bind_plan(args.plan, args.scope, args.review, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
