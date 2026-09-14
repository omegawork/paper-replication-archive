#!/usr/bin/env python3
"""Validate the canonical JSON Target Matrix and its generated Markdown."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from evidence_runtime import preview_plan
from runtime_model import load_case, load_json, render_target_matrix, target_matrix_path
from runtime_schema import SchemaValidationError, validate_document


def validate(case_dir: Path) -> list[str]:
    errors: list[str] = []
    path = target_matrix_path(case_dir)
    if not path.is_file():
        return ["work/target_matrix.json is missing"]
    try:
        matrix = load_json(path)
        validate_document(matrix, "target_matrix")
        case = load_case(case_dir)
    except (ValueError, SchemaValidationError) as exc:
        return [str(exc)]
    if matrix["case_id"] != case["case_id"]:
        errors.append("target matrix case_id mismatch")
    ids = [target["target_id"] for target in matrix["targets"]]
    if len(ids) != len(set(ids)):
        errors.append("target ids must be unique")
    for target in matrix["targets"]:
        if target["route"] == "not_applicable_schematic":
            if target["comparison_verdict"] != "not_applicable_schematic":
                errors.append(f"{target['target_id']}: schematic verdict mismatch")
            continue
        if not target.get("reference_anchor_mode") or not target.get("paper_anchor_plan"):
            errors.append(f"{target['target_id']}: reference anchor metadata is incomplete")
        try:
            preview = preview_plan(case_dir / target["evidence_plan"])
            if preview["status"] != "preview_ready":
                errors.append(f"{target['target_id']}: execution preview blocked: {preview['blockers']}")
        except (ValueError, OSError) as exc:
            errors.append(f"{target['target_id']}: {exc}")
    markdown = case_dir / "02_reproduction" / "target_matrix.md"
    if markdown.is_file() and markdown.read_text(encoding="utf-8-sig") != render_target_matrix(matrix):
        errors.append("generated target_matrix.md differs from target_matrix.json")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint the canonical Target Matrix")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()
    errors = validate(args.case_dir)
    if errors:
        print("FAIL: Target Matrix guardrail failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: canonical Target Matrix passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
