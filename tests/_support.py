from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "paper-replication-archive"
SCRIPTS = SKILL_ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from evidence_runtime import source_fingerprint  # noqa: E402


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run_python(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def make_plan(
    path: Path,
    source: Path,
    *,
    mode: str = "scalar",
    route: str = "independent_reimplementation",
    comparison: dict[str, Any] | None = None,
    observation: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
    untrusted_materials: list[str] | None = None,
    code_trust: str = "reviewed",
    extra_outputs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    fingerprint = source_fingerprint(source)
    if comparison is None:
        comparison = {
            "type": "scalar",
            "reference": 1.25,
            "acceptance": {"rules": [{"metric": "absolute_error", "max": 0.0}], "combine": "all"},
            "evidence_strength": "numeric",
            "unit_conversion": {"scale": 1.0, "offset": 0.0},
        }
    if observation is None:
        observation = {"method": "json_path", "artifact": "observed.json", "selector": "value", "unit": "arb", "bound_to_run": True}
    outputs = [{"path": "observed.json", "kind": "machine_observation"}]
    outputs.extend(extra_outputs or [])
    plan = {
        "schema_version": 5,
        "scientific_contract": {"model": "offline deterministic fixture", "parameters": {"mode": mode}},
        "execution_location": "local",
        "plan_id": f"plan-{mode}",
        "target_id": f"target-{mode}",
        "claim": {
            "claim_id": f"claim-{mode}",
            "text": f"offline {mode} fixture",
            "paper_anchors": ["page 1"],
            "route": route,
        },
        "source": {
            "path": str(source.resolve()),
            "commit": fingerprint["commit"],
            "tree_hash": fingerprint["tree_hash"],
            "dirty_diff_sha256": fingerprint["dirty_diff_sha256"],
        },
        "inputs": [],
        "command": {
            "argv": [sys.executable, "{source}/safe_runner.py", "--mode", mode, "--output", "{output}/observed.json"],
            "cwd": ".",
            "environment": {},
        },
        "capabilities": {
            "network": "forbidden",
            "read_paths": ["{source}"],
            "write_paths": ["{output}"],
            "code_trust": code_trust,
            "external_sandbox": False,
            "untrusted_materials": untrusted_materials or [],
        },
        "budget": {
            "wall_time_seconds": 10,
            "disk_mb": 2,
            "output_count": len(outputs),
            "single_file_mb": 1,
            "memory_mb": {"value": 256, "enforcement": "advisory"},
            "gpu": {"required": False, "enforcement": "advisory"},
        },
        "outputs": outputs,
        "observation": observation,
        "comparison": comparison,
        "blockers": blockers or [],
    }
    write_json(path, plan)
    return plan
