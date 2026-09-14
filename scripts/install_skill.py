#!/usr/bin/env python3
"""Safely check, update, or uninstall the managed personal Skill copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


SKILL_NAME = "paper-replication-archive"
MANAGED_MANIFEST = ".pra-managed.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return result
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if relative.as_posix() == MANAGED_MANIFEST or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        result[relative.as_posix()] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    return result


def resolve_source(value: Path) -> tuple[Path, Path]:
    source = value.resolve()
    if (source / "SKILL.md").is_file():
        skill = source
    elif (source / "skills" / SKILL_NAME / "SKILL.md").is_file():
        skill = source / "skills" / SKILL_NAME
    else:
        raise ValueError(f"cannot find {SKILL_NAME}/SKILL.md under source: {source}")
    repo_root = skill
    for candidate in (skill, *skill.parents):
        if (candidate / "baselines" / "v3-skill-manifest.json").is_file():
            repo_root = candidate
            break
    return skill, repo_root


def default_target() -> Path:
    configured = os.environ.get("CODEX_HOME")
    root = Path(configured).resolve() if configured else Path.home() / ".codex"
    return root / "skills" / SKILL_NAME


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid manifest JSON {path}: {exc}") from exc


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temp_path = Path(temporary)
        if temp_path.exists():
            temp_path.unlink()


def _baseline(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / "baselines" / "v3-skill-manifest.json"
    value = load_json(path)
    files = value.get("files", [])
    if value.get("file_count") != 45 or not isinstance(files, list):
        raise ValueError("v3 baseline manifest is invalid")
    return {item["path"]: {"sha256": item["sha256"], "size": item["size"]} for item in files}


def _managed_state(target: Path, repo_root: Path) -> tuple[dict[str, dict[str, Any]], str]:
    current = inventory(target)
    managed_path = target / MANAGED_MANIFEST
    if managed_path.is_file():
        value = load_json(managed_path)
        recorded = value.get("files")
        if not isinstance(recorded, dict):
            raise ValueError("installed managed manifest has no files mapping")
        expected = recorded
        origin = "managed-" + str(value.get("source_version", "unknown"))
    else:
        expected = _baseline(repo_root)
        origin = "recognized-v3-baseline"
    missing = sorted(set(expected) - set(current))
    unknown = sorted(set(current) - set(expected))
    modified = sorted(path for path in set(expected) & set(current) if current[path]["sha256"] != expected[path]["sha256"])
    if missing or unknown or modified:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        if modified:
            details.append("modified=" + ",".join(modified))
        raise ValueError("refusing to overwrite an installation with unknown changes: " + "; ".join(details))
    return expected, origin


def check(source: Path, target: Path) -> dict[str, Any]:
    source_files = inventory(source)
    target_files = inventory(target)
    missing = sorted(set(source_files) - set(target_files))
    extra = sorted(set(target_files) - set(source_files))
    changed = sorted(path for path in set(source_files) & set(target_files) if source_files[path]["sha256"] != target_files[path]["sha256"])
    ok = not missing and not extra and not changed
    if not ok:
        raise ValueError(f"source/install mismatch: missing={missing}; extra={extra}; changed={changed}")
    manifest = target / MANAGED_MANIFEST
    if not manifest.is_file():
        raise ValueError("installed copy matches bytes but lacks the managed manifest")
    recorded = load_json(manifest)
    if recorded.get("files") != source_files:
        raise ValueError("installed managed manifest does not match current source inventory")
    return {"ok": True, "action": "check", "file_count": len(source_files), "target": str(target)}


def update(source: Path, target: Path, repo_root: Path) -> dict[str, Any]:
    source_files = inventory(source)
    if not source_files:
        raise ValueError("source skill contains no files")
    target.mkdir(parents=True, exist_ok=True)
    prior, origin = _managed_state(target, repo_root) if inventory(target) or (target / MANAGED_MANIFEST).exists() else ({}, "empty")
    changed: list[str] = []
    removed: list[str] = []
    for relative, metadata in source_files.items():
        destination = target / Path(relative)
        if not destination.is_file() or sha256_file(destination) != metadata["sha256"]:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / Path(relative), destination)
            changed.append(relative)
    for relative, metadata in prior.items():
        if relative in source_files:
            continue
        path = target / Path(relative)
        if path.is_file():
            if sha256_file(path) != metadata["sha256"]:
                raise ValueError(f"refusing to remove modified previously managed file: {relative}")
            path.unlink()
            removed.append(relative)
    for directory in sorted((item for item in target.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    managed = {
        "schema_version": 1,
        "skill": SKILL_NAME,
        "source_version": (source / "VERSION").read_text(encoding="utf-8-sig").strip(),
        "files": source_files,
    }
    atomic_json(target / MANAGED_MANIFEST, managed)
    result = check(source, target)
    result.update({"action": "update", "origin": origin, "copied_files": changed, "removed_files": removed})
    return result


def uninstall(target: Path) -> dict[str, Any]:
    managed_path = target / MANAGED_MANIFEST
    if not managed_path.is_file():
        raise ValueError("uninstall requires a managed manifest; refusing unknown installation")
    value = load_json(managed_path)
    files = value.get("files")
    if not isinstance(files, dict):
        raise ValueError("installed managed manifest is invalid")
    current = inventory(target)
    unknown = sorted(set(current) - set(files))
    modified = sorted(path for path in set(current) & set(files) if current[path]["sha256"] != files[path]["sha256"])
    missing = sorted(set(files) - set(current))
    if unknown or modified or missing:
        raise ValueError(f"refusing uninstall with unknown changes: unknown={unknown}; modified={modified}; missing={missing}")
    for relative in sorted(files, reverse=True):
        (target / Path(relative)).unlink()
    managed_path.unlink()
    for directory in sorted((item for item in target.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        target.rmdir()
    except OSError:
        pass
    return {"ok": True, "action": "uninstall", "removed_files": len(files), "target": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely manage the personal paper-replication-archive skill copy")
    parser.add_argument("--action", required=True, choices=["check", "update", "uninstall"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path, default=default_target())
    args = parser.parse_args()
    try:
        target = args.target.resolve()
        if args.action == "uninstall":
            result = uninstall(target)
        else:
            if args.source is None:
                raise ValueError("--source is required for check and update")
            source, repo_root = resolve_source(args.source)
        if args.action == "check":
            result = check(source, target)
        elif args.action == "update":
            result = update(source, target, repo_root)
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
