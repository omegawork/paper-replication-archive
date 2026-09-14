from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import REPO_ROOT, SKILL_ROOT, SCRIPTS, run_python

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import install_skill


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class MigrationInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pra-migration-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_unversioned_legacy_migration_is_copy_only_and_conservative(self):
        source = self.root / "legacy-unversioned"
        (source / "work").mkdir(parents=True)
        (source / "work" / "state_machine.yaml").write_text(
            "case_id: legacy\ntask_mode: reproduce_specified_targets\n"
            "reproduction_status: not_scientific_reproduction\n"
            "targets:\n  Fig2:\n    current_status: not_scientific_reproduction\n",
            encoding="utf-8",
        )
        (source / "result.txt").write_bytes(b"legacy bytes\x00")
        before = tree_hash(source)
        destination = self.root / "migrated"
        preview_run = run_python("runtime_control.py", "migrate-case", str(source), "--destination", str(destination))
        self.assertEqual(preview_run.returncode, 0, preview_run.stderr)
        preview = json.loads(preview_run.stdout)
        execute = run_python(
            "runtime_control.py", "migrate-case", str(source), "--destination", str(destination),
            "--execute", "--confirm-preview-hash", preview["preview_sha256"], "--now", "2026-08-29T12:00:00-07:00",
        )
        self.assertEqual(execute.returncode, 0, execute.stderr)
        self.assertEqual(tree_hash(source), before)
        self.assertEqual((destination / "legacy_source" / "result.txt").read_bytes(), b"legacy bytes\x00")
        state = json.loads((destination / "work" / "runtime_state.json").read_text(encoding="utf-8"))
        statuses = [target.get("legacy_status") for target in state["targets"].values()]
        self.assertIn("not_scientific_reproduction", statuses)
        self.assertNotIn("REPRODUCED_WITHIN_ACCEPTANCE", [target["claim_status"] for target in state["targets"].values()])

    def test_schema3_success_migrates_to_inconclusive(self):
        source = self.root / "legacy-v3"
        (source / "work").mkdir(parents=True)
        (source / "work" / "runtime_state.json").write_text(
            json.dumps({"schema_version": 3, "task": {"case_id": "old", "task_mode": "reproduce_specified_targets", "paper_title": "Old"}, "targets": {"T": {"status": "reproduced_within_acceptance"}}}),
            encoding="utf-8",
        )
        destination = self.root / "migrated-v3"
        preview = json.loads(run_python("runtime_control.py", "migrate-case", str(source), "--destination", str(destination)).stdout)
        result = run_python("runtime_control.py", "migrate-case", str(source), "--destination", str(destination), "--execute", "--confirm-preview-hash", preview["preview_sha256"])
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((destination / "work" / "runtime_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["targets"]["T"]["claim_status"], "INCONCLUSIVE")

    def test_managed_installer_update_check_refusal_and_uninstall(self):
        target = self.root / "installed"
        updated = install_skill.update(SKILL_ROOT, target, REPO_ROOT)
        self.assertTrue(updated["ok"])
        self.assertTrue(install_skill.check(SKILL_ROOT, target)["ok"])
        (target / "SKILL.md").write_text("unknown local edit", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown changes"):
            install_skill.update(SKILL_ROOT, target, REPO_ROOT)
        shutil.copy2(SKILL_ROOT / "SKILL.md", target / "SKILL.md")
        uninstalled = install_skill.uninstall(target)
        self.assertTrue(uninstalled["ok"])
        self.assertFalse(target.exists())

    def test_installer_cli_check_matches_managed_copy(self):
        target = self.root / "cli-install"
        install_skill.update(SKILL_ROOT, target, REPO_ROOT)
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "install_skill.py"), "--action", "check", "--source", str(REPO_ROOT), "--target", str(target)],
            text=True, capture_output=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_cli_uninstall_does_not_require_source_checkout(self):
        target = self.root / "cli-uninstall"
        install_skill.update(SKILL_ROOT, target, REPO_ROOT)
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "install_skill.py"), "--action", "uninstall", "--target", str(target)],
            text=True, capture_output=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
