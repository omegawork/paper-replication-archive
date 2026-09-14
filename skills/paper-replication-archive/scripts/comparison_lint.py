#!/usr/bin/env python3
"""Reverify one or more Evidence Bundles; never trust stored verdict text."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from evidence_runtime import verify_bundle


def bundle_paths(path: Path) -> list[Path]:
    if (path / "bundle_manifest.json").is_file():
        return [path]
    return sorted(item.parent for item in path.rglob("bundle_manifest.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Reverify v4 Evidence Bundle comparisons")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    bundles = bundle_paths(args.path)
    if not bundles:
        print("FAIL: no Evidence Bundles found", file=sys.stderr)
        return 1
    errors = []
    for bundle in bundles:
        try:
            result = verify_bundle(bundle)
            if result["verification_status"] == "invalid":
                errors.append(f"{bundle}: {result['integrity_errors']}")
        except (ValueError, OSError) as exc:
            errors.append(f"{bundle}: {exc}")
    if errors:
        print("FAIL: comparison/evidence verification failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: all Evidence Bundles were rehashed and recomputed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
