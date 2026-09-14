# GUARDRAIL ONLY - DOES NOT VALIDATE SCIENTIFIC CORRECTNESS
"""Structural lint for Stage A Reading Pack.

The check enforces required files and formula-derivation fields. It does not
judge whether the reading or derivations are scientifically correct.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


READING_FILES = [
    "deep_reading_note.md",
    "paper_structure_map.md",
    "formula_explanation.md",
    "figure_overview.md",
    "deep_reading_coverage_table.md",
    "figure_semantic_map.json",
    "formula_list.json",
    "claim_registry.json",
    "parameter_registry.json",
]

FORMULA_FIELDS = {
    "source": ["source", "来源"],
    "derivation path": ["derivation path", "推导路径"],
    "assumptions": ["assumptions", "假设"],
    "conditions": ["conditions", "适用条件", "条件"],
    "symbols": ["symbols", "符号"],
    "figure links": ["figure links", "图表关联", "关联图表"],
    "code mapping preview": ["code mapping preview", "代码映射预告"],
    "evidence anchor": ["evidence anchor", "证据锚点"],
    "uncertainty": ["uncertainty", "不确定性"],
}


def reading_dir(case_dir: Path) -> Path:
    direct = case_dir / "01_deep_reading"
    if direct.exists():
        return direct
    obsidian = case_dir / "to_obsidian" / "01_deep_reading"
    if obsidian.exists():
        return obsidian
    return direct


def normalized_headings(text: str) -> list[str]:
    return [line.strip().lower().lstrip("#").strip() for line in text.splitlines() if line.strip().startswith("#")]


def has_heading_alias(headings: list[str], aliases: list[str]) -> bool:
    for heading in headings:
        for alias in aliases:
            if alias.lower() in heading:
                return True
    return False


def table_has_coverage_rows(text: str) -> bool:
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    return len(rows) >= 3 and ("coverage" in text.lower() or "覆盖" in text)


def formula_blocks(text: str) -> list[str]:
    matches = list(re.finditer(r"(?im)^##+\s+(?:Formula\b.*|公式\b.*|Eq\.?\s*\(?\d+\)?.*)$", text))
    if not matches:
        return []
    blocks: list[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        blocks.append(text[start:end])
    return blocks


def block_has_field(block: str, aliases: list[str]) -> bool:
    return any(re.search(rf"(?im)^\s*[-*]?\s*{re.escape(alias)}\s*[:：]", block) for alias in aliases)


def validate(case_dir: Path) -> list[str]:
    errors: list[str] = []
    base = reading_dir(case_dir)
    for name in READING_FILES:
        path = base / name
        if not path.exists():
            errors.append(f"missing Reading Pack file: 01_deep_reading/{name}")
        elif path.stat().st_size == 0:
            errors.append(f"empty Reading Pack file: 01_deep_reading/{name}")
    if errors:
        return errors

    note = (base / "deep_reading_note.md").read_text(encoding="utf-8")
    headings = normalized_headings(note)
    required_heading_aliases = {
        "research problem": ["research problem", "研究问题", "问题与动机"],
        "method and model": ["method and model", "模型与方法", "方法与模型"],
        "main results": ["main results", "主要结果", "结果链条"],
        "limitations": ["limitations", "局限", "不确定性"],
        "evidence anchors": ["evidence anchors", "证据锚点", "来源锚点"],
    }
    for required, aliases in required_heading_aliases.items():
        if not has_heading_alias(headings, aliases):
            errors.append(f"deep_reading_note.md missing section heading: {required}")

    coverage = (base / "deep_reading_coverage_table.md").read_text(encoding="utf-8")
    if not table_has_coverage_rows(coverage):
        errors.append("deep_reading_coverage_table.md must contain a markdown coverage table")

    formula_text = (base / "formula_explanation.md").read_text(encoding="utf-8")
    blocks = formula_blocks(formula_text)
    if not blocks:
        errors.append("formula_explanation.md must contain one or more Formula/公式/Eq. blocks")
    for idx, block in enumerate(blocks, start=1):
        for field, aliases in FORMULA_FIELDS.items():
            if not block_has_field(block, aliases):
                errors.append(f"formula block {idx} missing field: {field}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint Stage A Reading Pack structure")
    parser.add_argument("case_dir", type=Path)
    args = parser.parse_args()

    errors = validate(args.case_dir)
    if errors:
        print("FAIL: Reading Pack guardrail failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: Reading Pack has required structural fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
