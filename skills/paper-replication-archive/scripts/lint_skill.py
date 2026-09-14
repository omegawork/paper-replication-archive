#!/usr/bin/env python3
"""Repository-independent lint for the paper-replication-archive v5 skill."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


LOCAL_MODULES = {"runtime_model", "runtime_schema", "runtime_control", "comparison_engine", "evidence_runtime", "exhausted_terminal_record", "scope_authorization", "evidence_execution", "report_language"}
REQUIRED_SCRIPTS = {
    "runtime_control.py", "runtime_model.py", "runtime_schema.py", "evidence_runtime.py",
    "comparison_engine.py", "exhausted_terminal_record.py", "stage_preflight_check.py", "stage_gate_check.py", "lint_skill.py",
}
REQUIRED_SCHEMAS = {
    "case.schema.json", "runtime_event.schema.json", "runtime_state.schema.json",
    "target_matrix.schema.json", "evidence_plan.schema.json", "approval.schema.json",
    "evidence_index.schema.json", "paper_manifest.schema.json", "figure_manifest.schema.json",
    "digitization_record.schema.json", "deep_reading_pack.schema.json", "critic_report.schema.json",
    "archive.schema.json", "stage0_precheck.schema.json", "exhausted_terminal_record.schema.json",
}


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("SKILL.md must begin with YAML frontmatter")
    block = text[4:].split("\n---\n", 1)[0]
    result: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            raise ValueError(f"invalid SKILL.md frontmatter line: {line}")
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def validate(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.is_file():
        return ["SKILL.md is missing"]
    text = skill_path.read_text(encoding="utf-8-sig")
    try:
        frontmatter = _frontmatter(text)
        if not {"name", "description"} <= set(frontmatter):
            errors.append("SKILL.md frontmatter must contain name and description")
        if frontmatter.get("name") != "paper-replication-archive":
            errors.append("SKILL.md name is incorrect")
    except ValueError as exc:
        errors.append(str(exc))
    for link in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        if "://" not in link and not (skill_dir / link).exists():
            errors.append(f"broken SKILL.md relative link: {link}")
    scripts = skill_dir / "scripts"
    missing_scripts = REQUIRED_SCRIPTS - {path.name for path in scripts.glob("*.py")}
    errors.extend(f"missing required script: {name}" for name in sorted(missing_scripts))
    schema_dir = skill_dir / "schemas"
    missing_schemas = REQUIRED_SCHEMAS - {path.name for path in schema_dir.glob("*.json")}
    errors.extend(f"missing required schema: {name}" for name in sorted(missing_schemas))
    if list(schema_dir.glob("*.yaml")) or list(schema_dir.glob("*.yml")):
        errors.append("Schemas must be JSON; legacy YAML schemas must not remain")
    for path in schema_dir.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict) or value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                errors.append(f"invalid schema declaration: {path.name}")
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON schema {path.name}: {exc}")
    for path in scripts.glob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            errors.append(f"syntax error in {path.name}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".", 1)[0]]
            else:
                continue
            for name in names:
                if name == "yaml":
                    if path.name != "runtime_control.py" or "Legacy migration only" not in source:
                        errors.append(f"PyYAML is allowed only in the legacy migration path: {path.name}")
                elif name not in sys.stdlib_module_names and name not in LOCAL_MODULES:
                    errors.append(f"non-standard dependency imported by {path.name}: {name}")
    prohibited = ["schema-v3", "schema version 3", "work/state_machine.yaml", "work/agent_registry.yaml", "work/target_matrix.md"]
    for path in [skill_path, *sorted((skill_dir / "references").glob("*.md"))]:
        body = path.read_text(encoding="utf-8-sig").lower()
        for marker in prohibited:
            if marker.lower() in body:
                errors.append(f"legacy runtime marker remains in {path.relative_to(skill_dir)}: {marker}")
    version_path = skill_dir / "VERSION"
    if not version_path.is_file():
        errors.append("VERSION is required")
    elif not re.fullmatch(r"5\.\d+\.\d+", version_path.read_text(encoding="utf-8-sig").strip()):
        errors.append("VERSION must contain a semantic v5 release such as 5.0.0")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint paper-replication-archive v5 skill")
    parser.add_argument("skill_dir", nargs="?", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    errors = validate(args.skill_dir.resolve())
    if errors:
        print("FAIL: skill lint failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: paper-replication-archive v5 skill lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
