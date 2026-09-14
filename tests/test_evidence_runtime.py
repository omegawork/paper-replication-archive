from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _support import FIXTURES, make_plan, run_python, write_json

from evidence_runtime import execute_plan, preview_plan, verify_bundle, write_approval


class EvidenceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pra-evidence-test-")
        self.root = Path(self.temporary.name)
        self.source = FIXTURES / "safe_source"

    def tearDown(self):
        self.temporary.cleanup()

    def approve_and_execute(self, plan_path: Path, bundle_name: str = "bundle"):
        approval = self.root / f"{bundle_name}-approval.json"
        write_approval(plan_path, approval, "unit-test", "2026-08-29T12:00:00-07:00")
        return execute_plan(plan_path, approval, self.root / bundle_name)

    def test_deterministic_scalar_bundle_passes_and_reverifies(self):
        plan = self.root / "scalar-plan.json"
        make_plan(plan, self.source, mode="scalar")
        result = self.approve_and_execute(plan)
        self.assertEqual(result["verification_status"], "verified")
        self.assertEqual(result["claim_status"], "REPRODUCED_WITHIN_ACCEPTANCE")
        self.assertEqual(verify_bundle(self.root / "bundle")["manifest_sha256"], result["manifest_sha256"])

    def test_curve_scale_offset_is_valid_negative_result(self):
        plan = self.root / "curve-plan.json"
        make_plan(
            plan,
            self.source,
            mode="curve",
            comparison={
                "type": "curve",
                "reference": {"x": [0.0, 1.0, 2.0], "y": [1.0, 2.0, 3.0]},
                "acceptance": {"interpolation": "none", "y_acceptance": {"absolute_tolerance": 0.01}, "scale_consistency_tolerance": 0.01},
                "evidence_strength": "numeric",
                "unit_conversion": {"scale": 1.0, "offset": 0.0},
            },
        )
        result = self.approve_and_execute(plan, "curve-bundle")
        self.assertEqual(result["verification_status"], "verified")
        self.assertEqual(result["claim_status"], "NOT_REPRODUCED")
        self.assertEqual(result["comparison_verdict"], "scale_offset_but_trend_consistent")

    def test_multi_seed_bundle_records_raw_runs(self):
        plan = self.root / "seed-plan.json"
        make_plan(
            plan,
            self.source,
            mode="stochastic",
            comparison={
                "type": "stochastic",
                "reference": {"mean": 1.0, "std": 0.1},
                "acceptance": {"min_runs": 3, "mean_absolute_tolerance": 1e-12, "std_absolute_tolerance": 1e-12},
                "evidence_strength": "numeric",
                "unit_conversion": {"scale": 1.0, "offset": 0.0},
            },
        )
        result = self.approve_and_execute(plan, "seed-bundle")
        self.assertEqual(result["claim_status"], "REPRODUCED_WITHIN_ACCEPTANCE")
        observed = json.loads((self.root / "seed-bundle" / "observation.json").read_text(encoding="utf-8"))
        self.assertEqual(len(observed["value"]), 3)

    def test_blocked_license_plan_never_executes(self):
        fixture = json.loads((FIXTURES / "04_blocked_missing_license.json").read_text(encoding="utf-8"))
        plan = self.root / "blocked-plan.json"
        make_plan(plan, self.source, blockers=fixture["blockers"])
        preview = preview_plan(plan)
        self.assertEqual(preview["status"], "blocked")
        with self.assertRaisesRegex(ValueError, "blocked plan cannot be approved"):
            write_approval(plan, self.root / "approval.json", "unit-test")

    def test_prompt_injection_and_dangerous_readme_refused(self):
        plan = self.root / "danger-plan.json"
        make_plan(plan, FIXTURES / "malicious_source", untrusted_materials=["README.md"])
        preview = preview_plan(plan)
        self.assertEqual(preview["status"], "blocked")
        self.assertTrue(preview["untrusted_material_findings"])

    def test_stale_approval_invalid_after_plan_edit(self):
        plan = self.root / "stale-plan.json"
        value = make_plan(plan, self.source)
        approval = self.root / "stale-approval.json"
        write_approval(plan, approval, "unit-test")
        value["budget"]["wall_time_seconds"] = 11
        write_json(plan, value)
        with self.assertRaisesRegex(ValueError, "stale"):
            execute_plan(plan, approval, self.root / "stale-bundle")

    def test_source_copy_change_invalidates_positive_evidence(self):
        plan = self.root / "mutate-plan.json"
        make_plan(plan, self.source, mode="mutate")
        approval = self.root / "mutate-approval.json"
        write_approval(plan, approval, "unit-test")
        with self.assertRaisesRegex(ValueError, "self-verification"):
            execute_plan(plan, approval, self.root / "mutate-bundle")
        self.assertEqual(verify_bundle(self.root / "mutate-bundle")["claim_status"], "INCONCLUSIVE")

    def test_undeclared_output_invalidates_positive_evidence(self):
        plan = self.root / "extra-plan.json"
        make_plan(plan, self.source, mode="extra")
        approval = self.root / "extra-approval.json"
        write_approval(plan, approval, "unit-test")
        with self.assertRaisesRegex(ValueError, "self-verification"):
            execute_plan(plan, approval, self.root / "extra-bundle")
        self.assertEqual(verify_bundle(self.root / "extra-bundle")["verification_status"], "invalid")

    def test_non_run_bound_literal_cannot_be_positive(self):
        plan = self.root / "literal-plan.json"
        make_plan(plan, self.source, observation={"method": "literal", "artifact": None, "selector": 1.25, "unit": "arb", "bound_to_run": False})
        approval = self.root / "literal-approval.json"
        write_approval(plan, approval, "unit-test")
        with self.assertRaisesRegex(ValueError, "self-verification"):
            execute_plan(plan, approval, self.root / "literal-bundle")
        self.assertEqual(verify_bundle(self.root / "literal-bundle")["claim_status"], "INCONCLUSIVE")

    def test_bundle_tamper_is_detected(self):
        plan = self.root / "tamper-plan.json"
        make_plan(plan, self.source)
        self.approve_and_execute(plan, "tamper-bundle")
        output = self.root / "tamper-bundle" / "output" / "observed.json"
        output.write_text('{"value": 9}\n', encoding="utf-8")
        result = verify_bundle(self.root / "tamper-bundle")
        self.assertEqual(result["verification_status"], "invalid")

    def test_screenshot_and_visual_routes_never_positive(self):
        plan = self.root / "screenshot-plan.json"
        make_plan(
            plan,
            self.source,
            comparison={
                "type": "scalar",
                "reference": 1.25,
                "acceptance": {"absolute_tolerance": 0.0},
                "evidence_strength": "screenshot_coarse_anchor",
                "unit_conversion": {"scale": 1.0, "offset": 0.0},
            },
        )
        result = self.approve_and_execute(plan, "screenshot-bundle")
        self.assertEqual(result["claim_status"], "MANUAL_REVIEW_REQUIRED")
        visual = self.root / "visual-plan.json"
        make_plan(visual, self.source, route="visual_reconstruction")
        visual_result = self.approve_and_execute(visual, "visual-bundle")
        self.assertEqual(visual_result["claim_status"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(visual_result["route"], "visual_reconstruction")

    def test_official_replay_and_independent_reimplementation_remain_distinct(self):
        official_plan = self.root / "official-plan.json"
        make_plan(official_plan, self.source, route="official_artifact_replay")
        official = self.approve_and_execute(official_plan, "official-bundle")
        independent_plan = self.root / "independent-plan.json"
        make_plan(independent_plan, self.source, route="independent_reimplementation")
        independent = self.approve_and_execute(independent_plan, "independent-bundle")
        self.assertEqual(official["route"], "official_artifact_replay")
        self.assertEqual(independent["route"], "independent_reimplementation")
        self.assertNotEqual(official["route"], independent["route"])

    def test_final_claims_are_built_only_from_reverified_index(self):
        plan = self.root / "claims-plan.json"
        make_plan(plan, self.source)
        result = self.approve_and_execute(plan, "claims-bundle")
        case = self.root / "claims-case"
        init = run_python(
            "runtime_control.py", "init-case", str(case), "--case-id", "claims-case",
            "--task-mode", "reproduce_specified_targets", "--paper-title", "Paper", "--source", "offline",
            "--spawn-capability-confirmed",
        )
        self.assertEqual(init.returncode, 0, init.stderr)
        record = run_python(
            "runtime_control.py", "record-target", str(case), "--target-id", result["target_id"],
            "--route", result["route"], "--run-state", "completed", "--claim-status", result["claim_status"],
            "--comparison-verdict", result["comparison_verdict"], "--evidence-bundle", str(self.root / "claims-bundle"),
            "--next-action", "build claims",
        )
        self.assertEqual(record.returncode, 0, record.stderr)
        built = run_python("runtime_control.py", "build-final-claims", str(case))
        self.assertEqual(built.returncode, 0, built.stderr)
        claims = json.loads((case / "reports" / "final_response_allowed_claims.json").read_text(encoding="utf-8"))
        self.assertEqual(claims["claims"][0]["claim_status"], "REPRODUCED_WITHIN_ACCEPTANCE")
        (self.root / "claims-bundle" / "output" / "observed.json").write_text('{"value": 9}\n', encoding="utf-8")
        rebuilt = run_python("runtime_control.py", "build-final-claims", str(case))
        self.assertNotEqual(rebuilt.returncode, 0)


if __name__ == "__main__":
    unittest.main()
