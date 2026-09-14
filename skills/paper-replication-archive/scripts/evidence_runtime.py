#!/usr/bin/env python3
"""Execute v5 plans and independently verify versioned Evidence Bundles."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from comparison_engine import compare
from runtime_model import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    load_json,
    now_iso,
    sha256_bytes,
    sha256_file,
)
from runtime_schema import SchemaValidationError, validate_document


INTERNAL_EXCLUSIONS = {"bundle_manifest.json", "evidence_index.json", "verification.json", "report.md"}
DANGEROUS_EXECUTABLES = {"rm", "rmdir", "del", "erase", "format", "shutdown", "reboot", "diskpart"}
SUSPICIOUS_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"system\s+(message|prompt)", re.I),
    re.compile(r"developer\s+(message|instructions)", re.I),
    re.compile(r"rm\s+-rf\s+[/~]", re.I),
    re.compile(r"(?:del|erase|format)\s+[/\\]", re.I),
    re.compile(r"git\s+(?:reset\s+--hard|clean\s+-[a-z]*f)", re.I),
    re.compile(r"(?:curl|wget).{0,80}\|.{0,20}(?:sh|bash|powershell)", re.I),
)


def _safe_relative(root: Path, value: str, *, must_exist: bool = False) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"path must be relative: {value}")
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"path escapes its declared root: {value}")
    if must_exist and not candidate.exists():
        raise ValueError(f"declared path does not exist: {value}")
    return candidate


def _inventory(root: Path, *, exclude_git: bool = False) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise ValueError(f"inventory root is not a directory: {root}")
    result: list[dict[str, Any]] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix().encode("utf-8")):
        relative = path.relative_to(root)
        if exclude_git and (relative.parts[0] == ".git" or "__pycache__" in relative.parts or ".pytest_cache" in relative.parts):
            continue
        if root.resolve() not in path.resolve().parents:
            raise ValueError(f"inventory path escapes its root: {relative}")
        result.append({"path": relative.as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)})
    return result


def _inventory_hash(items: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(items))


def _git_root_hint(source: Path) -> Path | None:
    for candidate in (source, *source.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_output(source: Path, *args: str) -> bytes | None:
    root_hint = _git_root_hint(source)
    command = ["git"]
    if root_hint is not None:
        command.extend(["-c", f"safe.directory={root_hint.as_posix()}"])
    command.extend(["-C", str(source), *args])
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        return None
    return result.stdout if result.returncode == 0 else None


def source_fingerprint(source: Path) -> dict[str, Any]:
    source = source.resolve()
    inventory = _inventory(source, exclude_git=True)
    inventory_sha256 = _inventory_hash(inventory)
    commit_raw = _git_output(source, "rev-parse", "HEAD")
    if commit_raw is None:
        return {
            "fingerprint_version": "posix-utf8-v1",
            "commit": None,
            "tree_hash": inventory_sha256,
            "dirty_diff_sha256": sha256_bytes(b""),
            "inventory_sha256": inventory_sha256,
        }
    diff = _git_output(source, "diff", "--binary", "HEAD", "--", ".") or b""
    untracked_raw = _git_output(source, "ls-files", "--others", "--exclude-standard", "-z", "--", ".") or b""
    untracked_entries: list[dict[str, Any]] = []
    for value in untracked_raw.split(b"\0"):
        if not value:
            continue
        relative = value.decode("utf-8", errors="surrogateescape")
        path = _safe_relative(source, relative, must_exist=True)
        if path.is_file():
            untracked_entries.append({"path": relative.replace("\\", "/"), "sha256": sha256_file(path)})
    dirty_payload = diff + canonical_json_bytes(sorted(untracked_entries, key=lambda item: item["path"]))
    return {
        "fingerprint_version": "posix-utf8-v1",
        "commit": commit_raw.decode().strip(),
        "tree_hash": inventory_sha256,
        "dirty_diff_sha256": sha256_bytes(dirty_payload),
        "inventory_sha256": inventory_sha256,
    }


def load_plan(path: Path) -> dict[str, Any]:
    plan = load_json(path)
    try:
        validate_document(plan, "evidence_plan")
    except SchemaValidationError as exc:
        raise ValueError(f"evidence plan schema failure: {exc}") from exc
    return plan


def _scan_untrusted_materials(plan: dict[str, Any], source: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative in plan["capabilities"].get("untrusted_materials", []):
        path = _safe_relative(source, relative, must_exist=True)
        if not path.is_file():
            findings.append({"path": relative, "reason": "not_a_file"})
            continue
        text = path.read_bytes()[: 1024 * 1024].decode("utf-8", errors="replace")
        for pattern in SUSPICIOUS_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append({"path": relative, "reason": "untrusted_control_or_dangerous_text", "match": match.group(0)[:120]})
    return findings


def _command_risk(plan: dict[str, Any]) -> list[str]:
    argv = plan["command"]["argv"]
    executable = Path(argv[0]).name.lower().removesuffix(".exe").removesuffix(".cmd").removesuffix(".bat")
    risks: list[str] = []
    if executable in DANGEROUS_EXECUTABLES:
        risks.append(f"dangerous executable is forbidden: {executable}")
    joined = " ".join(argv).lower()
    if "git reset --hard" in joined or re.search(r"git\s+clean\s+-[a-z]*f", joined):
        risks.append("destructive git command is forbidden")
    return risks


def preview_plan(plan_path: Path) -> dict[str, Any]:
    plan_path = plan_path.resolve()
    plan = load_plan(plan_path)
    declared_source_path = Path(plan["source"]["path"])
    if not declared_source_path.is_absolute():
        raise ValueError("evidence plan source.path must be absolute")
    source = declared_source_path.resolve()
    if not source.is_dir():
        raise ValueError(f"declared source directory does not exist: {source}")
    observed_fingerprint = source_fingerprint(source)
    declared_fingerprint = {
        "commit": plan["source"]["commit"],
        "tree_hash": plan["source"]["tree_hash"],
        "dirty_diff_sha256": plan["source"]["dirty_diff_sha256"],
    }
    fingerprint_matches = all(observed_fingerprint[key] == value for key, value in declared_fingerprint.items())
    material_findings = _scan_untrusted_materials(plan, source)
    blockers = list(plan.get("blockers", []))
    blockers.extend(_command_risk(plan))
    if set(plan["capabilities"]["write_paths"]) != {"{output}"}:
        blockers.append("v5 local runtime permits only the declared {output} write root")
    if plan["capabilities"]["code_trust"] in {"unknown", "suspected_malicious"} and not plan["capabilities"]["external_sandbox"]:
        blockers.append("unknown or suspected-malicious code requires a real external sandbox")
    if material_findings and not plan["capabilities"]["external_sandbox"]:
        blockers.append("untrusted material contains control-like or dangerous text; external sandbox required")
    if not fingerprint_matches:
        blockers.append("declared source commit/tree/diff does not match current source")
    for declared in plan["inputs"]:
        path = _safe_relative(source, declared["path"], must_exist=True)
        if not path.is_file() or sha256_file(path) != declared["sha256"]:
            blockers.append(f"input hash mismatch: {declared['path']}")
    output_paths = [item["path"] for item in plan["outputs"]]
    if len(output_paths) != len(set(output_paths)):
        blockers.append("declared output paths must be unique")
    for relative in output_paths:
        try:
            _safe_relative(plan_path.parent, relative)
        except ValueError:
            blockers.append(f"output path is not a safe relative path: {relative}")
    plan_hash = sha256_file(plan_path)
    enforcement = {
        "wall_time": "hard_timeout",
        "memory": "advisory",
        "gpu": "advisory",
        "network": "advisory_without_external_sandbox",
        "disk_and_output_size": "post_run_enforced",
    }
    return {
        "schema_version": plan["schema_version"],
        "plan_id": plan["plan_id"],
        "target_id": plan["target_id"],
        "plan_sha256": plan_hash,
        "status": "blocked" if blockers else "preview_ready",
        "claim": plan["claim"],
        "exact_argv": plan["command"]["argv"],
        "cwd": plan["command"]["cwd"],
        "environment_keys": sorted(plan["command"]["environment"]),
        "capabilities": plan["capabilities"],
        "budget": plan["budget"],
        "outputs": plan["outputs"],
        "enforcement": enforcement,
        "declared_source": declared_fingerprint,
        "observed_source": observed_fingerprint,
        "untrusted_material_findings": material_findings,
        "blockers": blockers,
        "approval_template": {
            "schema_version": plan["schema_version"],
            "plan_id": plan["plan_id"],
            "plan_sha256": plan_hash,
            "approved_at": "REPLACE_WITH_TIMESTAMP",
            "approved_by": "REPLACE_WITH_USER_IDENTITY_LABEL",
            "allow_execute": True,
            "acknowledged_capabilities": {
                "network": plan["capabilities"]["network"],
                "write_paths": plan["capabilities"]["write_paths"],
                "budget": plan["budget"],
                "enforcement": enforcement,
            },
        },
        "integrity_note": "SHA-256 confirms bytes only; it does not authenticate the approver or prove scientific truth.",
    }


def write_approval(plan_path: Path, output: Path, approved_by: str, timestamp: str | None = None) -> dict[str, Any]:
    if load_plan(plan_path)["schema_version"] != 5:
        raise ValueError("v4 approvals are read-only")
    preview = preview_plan(plan_path)
    if preview["status"] != "preview_ready":
        raise ValueError("blocked plan cannot be approved: " + "; ".join(preview["blockers"]))
    approval = preview["approval_template"]
    approval["approved_at"] = now_iso(timestamp)
    approval["approved_by"] = approved_by
    validate_document(approval, "approval")
    atomic_write_json(output, approval)
    return approval


def _load_approval(path: Path, plan_path: Path, plan: dict[str, Any], *, live: bool = False) -> dict[str, Any]:
    approval = load_json(path)
    if approval.get("kind") == "run_authorization":
        from scope_authorization import validate_receipt
        return validate_receipt(approval, plan, sha256_file(plan_path), live=live)
    try:
        validate_document(approval, "approval")
    except SchemaValidationError as exc:
        raise ValueError(f"approval schema failure: {exc}") from exc
    current_hash = sha256_file(plan_path)
    if approval["plan_id"] != plan["plan_id"] or approval["plan_sha256"] != current_hash:
        raise ValueError("approval is stale: it does not match current plan bytes")
    if approval.get("allow_execute") is not True:
        raise ValueError("allow_execute must be the JSON boolean true")
    acknowledged = approval["acknowledged_capabilities"]
    if acknowledged.get("network") != plan["capabilities"]["network"]:
        raise ValueError("approval did not acknowledge the current network capability")
    if plan["schema_version"] == 5:
        if acknowledged.get("write_paths") != plan["capabilities"]["write_paths"] or acknowledged.get("budget") != plan["budget"]:
            raise ValueError("approval resource/write scope does not match the plan")
    return approval


def _copy_source(source: Path, destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in {".git", "__pycache__", ".pytest_cache"}}

    shutil.copytree(source, destination, copy_function=shutil.copy2, ignore=ignore)


def _resolve_argv(argv: list[str], source_copy: Path, output: Path) -> list[str]:
    return [value.replace("{source}", str(source_copy)).replace("{output}", str(output)) for value in argv]


def _execution_environment(plan: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    baseline_names = ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP") if os.name == "nt" else ("PATH", "TMPDIR", "LANG")
    environment = {name: os.environ[name] for name in baseline_names if name in os.environ}
    recorded: dict[str, str] = {name: "inherited-runtime-required" for name in environment}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    recorded["PYTHONDONTWRITEBYTECODE"] = "1"
    for name, value in plan["command"]["environment"].items():
        if isinstance(value, str):
            environment[name] = value
            recorded[name] = value
        elif isinstance(value, dict) and set(value) == {"secret_env"}:
            source_name = value["secret_env"]
            if source_name not in os.environ:
                raise ValueError(f"required secret environment variable is unavailable: {source_name}")
            environment[name] = os.environ[source_name]
            recorded[name] = "<redacted-secret-ref>"
        else:
            raise ValueError(f"environment entry {name} must be a string or secret_env reference")
    return environment, recorded


def _lookup_json(value: Any, selector: str) -> Any:
    current = value
    if selector in {"", "$", None}:
        return current
    for segment in str(selector).removeprefix("$.").split("."):
        if isinstance(current, list):
            current = current[int(segment)]
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            raise ValueError(f"json_path selector not found: {selector}")
    return current


def extract_observation(plan: dict[str, Any], bundle: Path) -> dict[str, Any]:
    specification = plan["observation"]
    method = specification["method"]
    artifact_value = specification.get("artifact")
    artifact_hash: str | None = None
    artifact_rel: str | None = None
    if artifact_value:
        artifact = _safe_relative(bundle / "output", artifact_value, must_exist=True)
        if not artifact.is_file():
            raise ValueError("observation artifact must be a file")
        artifact_hash = sha256_file(artifact)
        artifact_rel = f"output/{artifact.relative_to(bundle / 'output').as_posix()}"
    if method == "json_path":
        if not artifact_value:
            raise ValueError("json_path extraction requires an artifact")
        value = _lookup_json(load_json(artifact), specification.get("selector"))
    elif method == "csv_table":
        if not artifact_value:
            raise ValueError("csv_table extraction requires an artifact")
        selector = specification.get("selector") or {}
        with artifact.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            columns = reader.fieldnames or []
        selected = selector.get("columns", columns) if isinstance(selector, dict) else columns
        value_rows: list[dict[str, Any]] = []
        for row in rows:
            converted: dict[str, Any] = {}
            for column in selected:
                raw = row[column]
                try:
                    converted[column] = float(raw)
                except ValueError:
                    converted[column] = raw
            value_rows.append(converted)
        value = {"columns": selected, "rows": value_rows}
    elif method == "regex_stdout":
        selector = specification.get("selector")
        if not isinstance(selector, dict) or "pattern" not in selector:
            raise ValueError("regex_stdout selector requires pattern/group/cast")
        stdout_path = bundle / "raw" / "stdout.log"
        text = stdout_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(selector["pattern"], text, re.MULTILINE)
        if not match:
            raise ValueError("regex_stdout pattern did not match")
        raw = match.group(int(selector.get("group", 1)))
        cast = selector.get("cast", "string")
        value = float(raw) if cast == "float" else int(raw) if cast == "int" else raw
        artifact_rel = "raw/stdout.log"
        artifact_hash = sha256_file(stdout_path)
    elif method == "file_sha256":
        if not artifact_value:
            raise ValueError("file_sha256 extraction requires an artifact")
        value = artifact_hash
    elif method == "literal":
        value = specification.get("selector")
    elif method == "manual":
        value = None
    else:  # pragma: no cover - schema prevents this
        raise ValueError(f"unsupported observation method: {method}")
    bound = bool(specification.get("bound_to_run")) and artifact_hash is not None
    return {
        "schema_version": plan["schema_version"],
        "method": method,
        "value": value,
        "unit": specification.get("unit"),
        "artifact": artifact_rel,
        "artifact_sha256": artifact_hash,
        "bound_to_run": bound,
    }


def _write_manifest(bundle: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        relative = path.relative_to(bundle).as_posix()
        if relative in INTERNAL_EXCLUSIONS:
            continue
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    version = load_json(bundle / "plan.json")["schema_version"]
    manifest = {"schema_version": version, "bundle_version": 2 if version == 5 else 1, "files": files}
    atomic_write_json(bundle / "bundle_manifest.json", manifest)
    return manifest


def _bundle_extra_files(bundle: Path, manifest: dict[str, Any]) -> list[str]:
    listed = {item["path"] for item in manifest["files"]}
    actual = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path.relative_to(bundle).as_posix() not in INTERNAL_EXCLUSIONS
    }
    return sorted(actual - listed)


def _verification_from_raw(bundle: Path) -> dict[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_json(manifest_path)
    version = manifest.get("schema_version")
    if (version, manifest.get("bundle_version")) not in {(4, 1), (5, 2)}:
        raise ValueError("unsupported Evidence Bundle manifest version")
    integrity_errors: list[str] = []
    listed = [item["path"] for item in manifest.get("files", [])]
    required = {"plan.json", "approval.json", "run_record.json", "inventory_before.json", "inventory_after.json",
                "observation.json", "comparison.json"}
    if not required.issubset(listed) or len(set(listed)) != len(listed):
        integrity_errors.append("manifest must bind each core evidence file exactly once")
    for item in manifest.get("files", []):
        path = _safe_relative(bundle, item["path"], must_exist=True)
        if not path.is_file() or path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            integrity_errors.append(f"manifest mismatch: {item['path']}")
    extra = _bundle_extra_files(bundle, manifest)
    if extra:
        integrity_errors.append("undeclared bundle files: " + ", ".join(extra))
    plan_path = bundle / "plan.json"
    approval_path = bundle / "approval.json"
    plan = load_plan(plan_path)
    approval = _load_approval(approval_path, plan_path, plan)
    run = load_json(bundle / "run_record.json")
    before = load_json(bundle / "inventory_before.json")
    after = load_json(bundle / "inventory_after.json")
    if run.get("plan_sha256") != sha256_file(plan_path):
        integrity_errors.append("run record is not bound to plan bytes")
    if run.get("command_template") != plan["command"]:
        integrity_errors.append("run record command template differs from plan")
    incomplete_failure = version == 5 and bool(run.get("execution_error") or run.get("inventory_errors"))
    if not incomplete_failure and before.get("original_source") != after.get("original_source"):
        integrity_errors.append("original source changed during execution")
    if not incomplete_failure and before.get("source_copy") != after.get("source_copy"):
        integrity_errors.append("source copy changed during execution")
    if not incomplete_failure and before.get("inputs") != after.get("inputs"):
        integrity_errors.append("declared inputs changed during execution")
    if run.get("undeclared_outputs"):
        integrity_errors.append("execution produced undeclared outputs")
    if version == 4 and run.get("missing_outputs"):
        integrity_errors.append("execution missed declared outputs")
    if version == 4 and run.get("resource_violations"):
        integrity_errors.append("execution exceeded post-run resource limits")
    if version == 4 and (run.get("returncode") != 0 or run.get("timed_out")):
        integrity_errors.append("command did not complete successfully")
    if version == 5 and before.get("source_copy") and _inventory_hash(before["source_copy"]) != plan["source"]["tree_hash"]:
        integrity_errors.append("executed source snapshot does not match the plan fingerprint")
    if version == 5:
        recomputed_observation, recomputed_comparison = run_observation(plan, bundle, run)
    else:
        recomputed_observation = extract_observation(plan, bundle)
        recomputed_comparison = compare(recomputed_observation["value"], plan["comparison"])
    stored_observation = load_json(bundle / "observation.json")
    if canonical_json_bytes(recomputed_observation) != canonical_json_bytes(stored_observation):
        integrity_errors.append("stored observation differs from machine re-extraction")
    if not recomputed_observation["bound_to_run"] and not recomputed_observation.get("unavailable") and plan.get("operation_kind") != "presentation":
        integrity_errors.append("observation is not bound to a hashed run artifact")
    stored_comparison = load_json(bundle / "comparison.json")
    if canonical_json_bytes(recomputed_comparison) != canonical_json_bytes(stored_comparison):
        integrity_errors.append("stored comparison differs from deterministic recomputation")
    route = plan["claim"]["route"]
    manual = (
        plan.get("operation_kind") == "presentation"
        or route in {"visual_reconstruction", "not_applicable_schematic"}
        or recomputed_comparison.get("manual_review_required", False)
        or not recomputed_comparison.get("scientific_positive_allowed", False)
    )
    blockers = list(plan.get("blockers", []))
    if blockers:
        verification_status = "blocked"
        claim_status = "BLOCKED"
    elif integrity_errors:
        verification_status = "invalid"
        claim_status = "INCONCLUSIVE"
    elif version == 5 and (execution_failed(run) or recomputed_observation.get("unavailable")):
        verification_status = "execution_failed"
        claim_status = "INCONCLUSIVE"
    elif manual:
        verification_status = "manual_review_required"
        claim_status = "MANUAL_REVIEW_REQUIRED"
    elif recomputed_comparison.get("passed") is True:
        verification_status = "verified"
        claim_status = "REPRODUCED_WITHIN_ACCEPTANCE"
    else:
        verification_status = "verified"
        claim_status = "NOT_REPRODUCED"
    return {
        "schema_version": version,
        "bundle_version": manifest["bundle_version"],
        "target_id": plan["target_id"],
        "claim_id": plan["claim"]["claim_id"],
        "route": route,
        "plan_sha256": sha256_file(plan_path),
        "run_record_sha256": sha256_file(bundle / "run_record.json"),
        "observation_sha256": sha256_file(bundle / "observation.json"),
        "comparison_sha256": sha256_file(bundle / "comparison.json"),
        "manifest_sha256": sha256_file(manifest_path),
        "verification_status": verification_status,
        "claim_status": claim_status,
        "comparison_verdict": recomputed_comparison["comparison_verdict"],
        "integrity_errors": integrity_errors,
        "blockers": blockers,
        "approved_by_label": approval.get("approved_by", "inherited_task_scope"),
        "execution_errors": [str(run.get("execution_error"))] if run.get("execution_error") else [],
        "hash_scope_note": "Hashes establish byte integrity only, not identity or scientific truth.",
    }


def verify_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve()
    if not bundle.is_dir():
        raise ValueError(f"Evidence Bundle does not exist: {bundle}")
    result = _verification_from_raw(bundle)
    index_path = bundle / "evidence_index.json"
    if not index_path.is_file():
        raise ValueError("Evidence Bundle is missing evidence_index.json")
    stored = load_json(index_path)
    try:
        validate_document(stored, "evidence_index")
    except SchemaValidationError as exc:
        raise ValueError(f"Evidence Index schema failure: {exc}") from exc
    expected_index = {key: result[key] for key in (
        "schema_version", "bundle_version", "target_id", "claim_id", "route", "plan_sha256",
        "run_record_sha256", "observation_sha256", "comparison_sha256", "manifest_sha256",
        "verification_status", "claim_status", "comparison_verdict",
    )}
    if canonical_json_bytes(stored) != canonical_json_bytes(expected_index):
        result["integrity_errors"].append("Evidence Index differs from recomputed verification")
        result["verification_status"] = "invalid"
        result["claim_status"] = "INCONCLUSIVE"
    return result


def execute_plan(plan_path: Path, approval_path: Path, bundle: Path, *, detach: bool = False) -> dict[str, Any]:
    from evidence_execution import start
    return start(plan_path, approval_path, bundle, detach=detach)



def execution_failed(run):
    return bool(run.get("returncode") != 0 or run.get("timed_out") or run.get("execution_error")
                or run.get("missing_outputs") or run.get("resource_violations"))


def run_observation(plan, bundle, run):
    if not execution_failed(run):
        try:
            observation = extract_observation(plan, bundle)
            return observation, compare(observation["value"], plan["comparison"])
        except (ValueError, OSError, KeyError, TypeError, IndexError, OverflowError):
            pass
    return ({"schema_version": 5, "method": plan["observation"]["method"], "value": None,
             "unit": plan["observation"].get("unit"), "artifact": None, "artifact_sha256": None,
             "bound_to_run": False, "unavailable": True},
            {"passed": None, "scientific_positive_allowed": False, "comparison_verdict": "not_evaluated"})


def seal_bundle(bundle):
    _write_manifest(bundle)
    result = _verification_from_raw(bundle)
    index = {key: result[key] for key in ("schema_version", "bundle_version", "target_id", "claim_id", "route",
        "plan_sha256", "run_record_sha256", "observation_sha256", "comparison_sha256", "manifest_sha256",
        "verification_status", "claim_status", "comparison_verdict")}
    validate_document(index, "evidence_index")
    atomic_write_json(bundle / "evidence_index.json", index)
    atomic_write_text(bundle / "report.md", "# Evidence result\n\n"
        f"Target: {result['target_id']}\n\nVerification: {result['verification_status']}\n\n"
        f"Scientific claim: {result['claim_status']}\n\nComparison: {result['comparison_verdict']}\n\n"
        "Inspect raw outputs and comparison details. Archive integrity does not establish scientific acceptance.\n")
    return verify_bundle(bundle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="paper-replication-archive v5 evidence runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview")
    preview.add_argument("--plan", required=True, type=Path)
    preview.add_argument("--output", type=Path)

    approve = sub.add_parser("approve")
    approve.add_argument("--plan", required=True, type=Path)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--output", required=True, type=Path)
    approve.add_argument("--now")

    execute = sub.add_parser("execute")
    execute.add_argument("--plan", required=True, type=Path)
    execute.add_argument("--approval", required=True, type=Path)
    execute.add_argument("--output", required=True, type=Path)
    execute.add_argument("--detach", action="store_true")

    verify = sub.add_parser("verify")
    verify.add_argument("--bundle", required=True, type=Path)
    verify.add_argument("--write-verification", action="store_true")

    fingerprint = sub.add_parser("fingerprint-source")
    fingerprint.add_argument("--source", required=True, type=Path)
    status = sub.add_parser("status")
    status.add_argument("--bundle", required=True, type=Path)
    recover = sub.add_parser("recover")
    recover.add_argument("--bundle", required=True, type=Path)
    recover.add_argument("--completion-evidence", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "preview":
            result = preview_plan(args.plan)
            if args.output:
                atomic_write_json(args.output, result)
        elif args.command == "approve":
            result = write_approval(args.plan, args.output, args.approved_by, args.now)
        elif args.command == "execute":
            result = execute_plan(args.plan, args.approval, args.output, detach=args.detach)
        elif args.command == "recover":
            from evidence_execution import recover
            result = recover(args.bundle, completion_evidence=args.completion_evidence)
        elif args.command == "status":
            from evidence_execution import status
            result = status(args.bundle)
        elif args.command == "verify":
            result = verify_bundle(args.bundle)
            if args.write_verification and result["schema_version"] == 4:
                raise ValueError("v4 bundles are read-only; omit --write-verification")
            if args.write_verification:
                atomic_write_json(args.bundle / "verification.json", result)
        else:
            result = source_fingerprint(args.source)
    except (ValueError, OSError, subprocess.SubprocessError, SchemaValidationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
