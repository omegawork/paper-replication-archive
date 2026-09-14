# V4 RENDERING GUARDRAIL ONLY - DOES NOT VALIDATE SCIENTIFIC CORRECTNESS
"""Lint Obsidian-oriented markdown for portability and math issues."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ABSOLUTE_PATH_PATTERNS = [r"\b[A-Za-z]:[\\/]", r"\\Users\\", r"/Users/"]
FORBIDDEN_EMPTY_MARKERS = ["TODO", "TBD", "placeholder", "略", "待补"]
IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
TABLE_ROW_PATTERN = re.compile(r"^\s*\|.*\|\s*$")
FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
LATIN_WORD_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*\b")


def markdown_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if ".git" not in path.parts)


def chinese_ratio(text: str) -> float:
    cjk = len(CJK_PATTERN.findall(text))
    latin = len(LATIN_WORD_PATTERN.findall(text))
    if cjk + latin == 0:
        return 1.0
    return cjk / (cjk + latin)


def validate_markdown_file(path: Path, root: Path, require_chinese: bool = False) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(root)

    # Language quality is reviewed semantically, never gated by character quotas.

    for pattern in ABSOLUTE_PATH_PATTERNS:
        if re.search(pattern, text):
            errors.append(f"{rel}: contains absolute local path pattern {pattern}")
    for marker in FORBIDDEN_EMPTY_MARKERS:
        if marker.lower() in text.lower():
            errors.append(f"{rel}: contains empty marker token {marker}")
    if text.count("$$") % 2 != 0:
        errors.append(f"{rel}: unpaired $$ math delimiter")

    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence and "$" in line:
            errors.append(f"{rel}:{number}: math delimiter inside code fence will not render in Obsidian")
        if TABLE_ROW_PATTERN.match(line) and "$" in line:
            errors.append(f"{rel}:{number}: math formula inside markdown table; move formula outside table")

    for match in IMAGE_PATTERN.finditer(text):
        target = match.group(1).split("#", 1)[0].strip()
        if re.match(r"^[A-Za-z]+://", target) or re.match(r"^[A-Za-z]:[\\/]", target):
            errors.append(f"{rel}: image link must be relative: {target}")
            continue
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            errors.append(f"{rel}: image link escapes markdown root: {target}")
            continue
        if not resolved.exists():
            errors.append(f"{rel}: image target does not exist: {target}")
    return errors


def validate(root: Path, allow_any_root: bool = False, require_chinese: bool = False) -> list[str]:
    errors: list[str] = []
    if not allow_any_root and root.name != "to_obsidian":
        root = root / "to_obsidian"
    if not root.exists():
        return [f"markdown root not found: {root}"]
    if not allow_any_root:
        if (root / ".git").exists():
            errors.append("to_obsidian must not contain .git")
        start_files = list(root.glob("00_START_HERE*.md"))
        moc_files = list(root.glob("*MOC*.md"))
        if not start_files:
            errors.append("missing 00_START_HERE markdown file")
        if not moc_files:
            errors.append("missing MOC markdown file")
    for path in markdown_files(root):
        errors.extend(validate_markdown_file(path, root, require_chinese=require_chinese))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint Obsidian markdown portability and math rendering")
    parser.add_argument("path", type=Path)
    parser.add_argument("--allow-any-root", action="store_true", help="lint the provided directory directly")
    parser.add_argument("--require-chinese", action="store_true", help="legacy compatibility flag; language is reviewed semantically")
    args = parser.parse_args()

    errors = validate(args.path, allow_any_root=args.allow_any_root, require_chinese=args.require_chinese)
    if errors:
        print("FAIL: Obsidian markdown guardrail failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: Obsidian markdown guardrail passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
