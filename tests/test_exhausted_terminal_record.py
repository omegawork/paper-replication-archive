from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _support import FIXTURES, make_plan, run_python, write_json

from evidence_runtime import execute_plan, write_approval
from exhausted_terminal_record import create_record, record_path, verify_record
from runtime_model import append_event, render_case
from stage_preflight_check import run_checks


NOW = "2000-01-01T00:00:00+00:00"
TARGET_ID = "Fig9"


class ExhaustedTerminalRecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pra-exhausted-terminal-")
        self.root = Path(self.temporary.name)
        self.tick = 0

    def tearDown(self):
        self.temporary.cleanup()

    def _time(self) -> str:
        self.tick += 1
        return f"2000-01-01T00:00:{self.tick:02d}+00:00"

    def _init_case(self, name: str = "case") -> Path:
        case = self.root / name
        result = run_python(
            "runtime_control.py",
            "init-case",
            str(case),
            "--case-id",
            name,
            "--task-mode",
            "reproduce_specified_targets",
            "--paper-title",
            "Fixture paper",
            "--source",
            "offline",
            "--spawn-capability-confirmed",
            "--now",
            NOW,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return case

    def _finish_agent(
        self,
        case: Path,
        agent_id: str,
        role: str,
        target_id: str,
        handoff_out: list[str] | None = None,
        outcome: str | None = None,
        finish: bool = True,
    ) -> None:
        registered_at = self._time()
        append_event(
            case,
            "agent_registered",
            {
                "agent": {
                    "agent_id": agent_id,
                    "run_id": f"run-{self.tick:04d}",
                    "source": "platform_spawn_result",
                    "stage": "StageC",
                    "role": role,
                    "target_id": target_id,
                    "task": f"{role} {target_id}",
                    "handoff_in": [],
                    "handoff_out": [],
                    "replacement_for": None,
                    "registered_at": registered_at,
                    "last_heartbeat_at": registered_at,
                    "finished_at": None,
                    "run_state": "running",
                    "outcome": "pending",
                    "materialization": None,
                },
                "stage": "StageC",
                "target_id": target_id,
                "next_action": "finish fixture agent",
            },
            timestamp=registered_at,
        )
        if not finish:
            return
        append_event(
            case,
            "agent_finished",
            {
                "agent_id": agent_id,
                "stage": "StageC",
                "target_id": target_id,
                "outcome": outcome or ("critique_fail" if role == "Critic" else "produced_artifact"),
                "handoff_out": handoff_out or [],
                "materialization": "direct_agent_write",
                "next_action": "continue fixture",
            },
            timestamp=self._time(),
        )

    def _exhausted_case(self, *, create: bool = True, name: str = "case", count: int = 4) -> tuple[Path, str, str, str]:
        case = self._init_case(name)
        for index in range(1, count + 1):
            self._finish_agent(case, f"executor-{index}", "Executor", TARGET_ID)
        for index in range(1, count + 1):
            self._finish_agent(case, f"critic-{index}", "Critic", TARGET_ID)

        critic_relative = "work/critic_reports/Fig9-repair4.json"
        write_json(
            case / critic_relative,
            {
                "schema_version": 4,
                "stage": "StageC",
                "target_id": TARGET_ID,
                "reviewer_agent_id": f"critic-{count}",
                "decision": "fail",
                "summary": "Fourth attempt remains inconclusive.",
                "findings": [{"severity": "blocking", "detail": "No accepted candidate."}],
                "required_repairs": ["Record the exhausted negative terminal state."],
                "evidence_reviewed": ["work/engineering/Fig9/result.txt"],
            },
        )
        supervisor_relative = "work/handoffs/Fig9-terminal-supervisor.md"
        supervisor_path = case / supervisor_relative
        supervisor_path.parent.mkdir(parents=True, exist_ok=True)
        supervisor_path.write_text("# Terminal supervisor advice\n\nNo lawful fifth repair.\n", encoding="utf-8")
        self._finish_agent(
            case,
            "supervisor-terminal",
            "Supervisor",
            TARGET_ID,
            [supervisor_relative],
            "BLOCKED: no lawful fifth repair",
        )

        engineering_relative = "work/engineering/Fig9"
        engineering_path = case / engineering_relative
        engineering_path.mkdir(parents=True, exist_ok=True)
        (engineering_path / "result.txt").write_text("solver stopped without an accepted candidate\n", encoding="utf-8")
        append_event(
            case,
            "stage_updated",
            {
                "stage": "StageC",
                "run_state": "failed",
                "attempt_count": count,
                "repair_count": count - 1,
                "supervisor_advice_agent_id": "supervisor-terminal",
                "next_action": "record exhausted terminal failure",
            },
            timestamp=self._time(),
        )
        append_event(
            case,
            "target_recorded",
            {
                "target_id": TARGET_ID,
                "stage": "StageC",
                "target": {
                    "target_id": TARGET_ID,
                    "stage_id": "StageC",
                    "route": "independent_reimplementation",
                    "run_state": "failed",
                    "claim_status": "INCONCLUSIVE",
                    "comparison_verdict": "not_evaluated",
                    "legacy_status": None,
                    "evidence_rows": [],
                },
                "next_action": "verify terminal record",
            },
            timestamp=self._time(),
        )
        render_case(case)
        if create:
            result = create_record(
                case,
                TARGET_ID,
                critic_relative,
                supervisor_relative,
                [engineering_relative],
            )
            self.assertEqual(result["verification_status"], "verified_terminal_failure")
            self.assertFalse(result["scientific_positive_allowed"])
        return case, critic_relative, supervisor_relative, engineering_relative

    def test_early_terminal_failure_uses_one_real_role_pair_instead_of_four_fabricated_reviews(self):
        # Synthetic role IDs exercise the protocol; native validation is a separate evaluation.
        case, _, _, _ = self._exhausted_case(count=1)
        self.assertEqual(verify_record(case, TARGET_ID)["verification_status"], "verified_terminal_failure")
        checks, _ = run_checks(case, "StageC", TARGET_ID, False)
        self.assertTrue(all(item["passed"] for item in checks), checks)

    def test_valid_target_and_aggregate_preflight_pass_after_report_event_append(self):
        case, _, _, _ = self._exhausted_case()
        checks, _ = run_checks(case, "StageC", TARGET_ID, False)
        self.assertTrue(all(item["passed"] for item in checks), checks)
        written = run_python(
            "stage_preflight_check.py",
            str(case),
            "--stage",
            "StageC",
            "--target-id",
            TARGET_ID,
            "--write-report",
            "--now",
            self._time(),
        )
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertEqual(verify_record(case, TARGET_ID)["verification_status"], "verified_terminal_failure")
        aggregate, _ = run_checks(case, "StageC", None, True)
        self.assertTrue(all(item["passed"] for item in aggregate), aggregate)

    def test_missing_record_fails_target_preflight(self):
        case, _, _, _ = self._exhausted_case(create=False)
        checks, _ = run_checks(case, "StageC", TARGET_ID, False)
        self.assertFalse(all(item["passed"] for item in checks))
        self.assertTrue(any(item["name"] == "exhausted terminal record verify" for item in checks))

    def test_engineering_evidence_and_event_prefix_tamper_fail(self):
        case, _, _, engineering = self._exhausted_case()
        (case / engineering / "result.txt").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            verify_record(case, TARGET_ID)

        other, _, _, _ = self._exhausted_case(name="prefix-tamper")
        log = other / "logs" / "runtime_events.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[0])
        event["payload"]["next_action"] = "tampered prefix"
        lines[0] = json.dumps(event, ensure_ascii=False, sort_keys=True)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_record(other, TARGET_ID)

    def test_unrelated_later_event_is_allowed(self):
        case, _, _, _ = self._exhausted_case()
        append_event(
            case,
            "heartbeat_recorded",
            {"agent_id": "executor-1", "stage": "StageC", "target_id": TARGET_ID},
            timestamp=self._time(),
        )
        self.assertEqual(verify_record(case, TARGET_ID)["verification_status"], "verified_terminal_failure")

    def test_positive_state_or_bundle_after_record_fails(self):
        positive, _, _, _ = self._exhausted_case()
        append_event(
            positive,
            "target_recorded",
            {
                "target_id": TARGET_ID,
                "stage": "StageC",
                "target": {
                    "run_state": "completed",
                    "claim_status": "REPRODUCED_WITHIN_ACCEPTANCE",
                    "comparison_verdict": "within_acceptance",
                    "evidence_rows": [],
                },
                "next_action": "invalid positive upgrade",
            },
            timestamp=self._time(),
        )
        with self.assertRaisesRegex(ValueError, "negative terminal pair"):
            verify_record(positive, TARGET_ID)

        bundle_case, _, _, _ = self._exhausted_case(name="bundle-present")
        append_event(
            bundle_case,
            "target_recorded",
            {
                "target_id": TARGET_ID,
                "stage": "StageC",
                "target": {"evidence_bundle": "results/Fig9/evidence_bundle"},
                "next_action": "invalid mixed closure",
            },
            timestamp=self._time(),
        )
        with self.assertRaisesRegex(ValueError, "Evidence Bundle"):
            verify_record(bundle_case, TARGET_ID)

    def test_newer_target_critic_invalidates_record(self):
        case, _, _, _ = self._exhausted_case()
        self._finish_agent(case, "critic-5", "Critic", TARGET_ID)
        with self.assertRaisesRegex(ValueError, "latest Critic report"):
            verify_record(case, TARGET_ID)

        running, _, _, _ = self._exhausted_case(name="newer-running-critic")
        self._finish_agent(running, "critic-5-running", "Critic", TARGET_ID, finish=False)
        with self.assertRaisesRegex(ValueError, "latest target Critic has not finished"):
            verify_record(running, TARGET_ID)

    def test_ordinary_bundle_and_schematic_paths_are_unchanged(self):
        plan = self.root / "ordinary-plan.json"
        make_plan(plan, FIXTURES / "safe_source", mode="scalar")
        approval = self.root / "ordinary-approval.json"
        bundle = self.root / "ordinary-bundle"
        write_approval(plan, approval, "unit-test", NOW)
        verification = execute_plan(plan, approval, bundle)
        ordinary = self._init_case("ordinary")
        self._finish_agent(ordinary, "ordinary-executor", "Executor", verification["target_id"])
        append_event(
            ordinary,
            "target_recorded",
            {
                "target_id": verification["target_id"],
                "stage": "StageC",
                "target": {
                    "target_id": verification["target_id"],
                    "stage_id": "StageC",
                    "route": verification["route"],
                    "run_state": "completed",
                    "claim_status": verification["claim_status"],
                    "comparison_verdict": verification["comparison_verdict"],
                    "legacy_status": None,
                    "evidence_rows": [],
                    "evidence_bundle": str(bundle),
                    "verified_evidence": verification,
                },
                "next_action": "ordinary preflight",
            },
            timestamp=self._time(),
        )
        render_case(ordinary)
        ordinary_checks, _ = run_checks(ordinary, "StageC", verification["target_id"], False)
        self.assertTrue(all(item["passed"] for item in ordinary_checks), ordinary_checks)

        schematic = self._init_case("schematic")
        self._finish_agent(schematic, "schematic-executor", "Executor", "Diagram1")
        append_event(
            schematic,
            "target_recorded",
            {
                "target_id": "Diagram1",
                "stage": "StageC",
                "target": {
                    "target_id": "Diagram1",
                    "stage_id": "StageC",
                    "route": "not_applicable_schematic",
                    "run_state": "completed",
                    "claim_status": "MANUAL_REVIEW_REQUIRED",
                    "comparison_verdict": "not_applicable_schematic",
                    "legacy_status": None,
                    "evidence_rows": [],
                },
                "next_action": "schematic preflight",
            },
            timestamp=self._time(),
        )
        render_case(schematic)
        schematic_checks, _ = run_checks(schematic, "StageC", "Diagram1", False)
        self.assertTrue(all(item["passed"] for item in schematic_checks), schematic_checks)

    def test_standard_record_path_and_schema_negative_flags(self):
        case, _, _, _ = self._exhausted_case()
        path = record_path(case, TARGET_ID)
        self.assertEqual(path, case / "work" / "exhausted_terminal_records" / "Fig9.json")
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(stored["scientific_positive_allowed"])
        self.assertFalse(stored["terminal_state"]["evidence_bundle_present"])
        self.assertEqual(stored["terminal_state"]["accepted_evidence_rows"], [])
        self.assertEqual(stored["supervisor_advice"]["terminal_reason"], "no lawful fifth repair")
        self.assertEqual(stored["next_stage"], "StageCSummary")


if __name__ == "__main__":
    unittest.main()
