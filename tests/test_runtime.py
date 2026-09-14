from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _support import run_python, write_json

from runtime_control import _dependency_errors, _required_decisions_present
from runtime_model import append_event, audit_case, load_state, render_case


NOW = "2000-01-01T00:00:00+00:00"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pra-runtime-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def init_case(self, name="case", mode="reproduce_specified_targets") -> Path:
        case = self.root / name
        result = run_python(
            "runtime_control.py", "init-case", str(case), "--case-id", name,
            "--task-mode", mode, "--paper-title", "Fixture paper", "--source", "offline",
            "--spawn-capability-confirmed", "--now", NOW,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return case

    def test_original_three_modes_initialize(self):
        for index, mode in enumerate(("deep_reading_only", "reproduce_all_data_figures", "reproduce_specified_targets")):
            case = self.init_case(f"mode-{index}", mode)
            self.assertEqual(load_state(case)["case"]["task_mode"], mode)

    def test_init_fails_closed_without_real_spawn_capability(self):
        case = self.root / "closed"
        result = run_python(
            "runtime_control.py", "init-case", str(case), "--case-id", "closed",
            "--task-mode", "deep_reading_only", "--paper-title", "Paper", "--source", "offline",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("spawn capability", result.stderr)
        self.assertFalse(case.exists())

    def test_duplicate_agent_id_is_rejected(self):
        case = self.init_case()
        first = run_python(
            "runtime_control.py", "register-agent", str(case), "--stage", "Stage0",
            "--role", "Supervisor", "--agent-id", "platform-1", "--task", "precheck",
        )
        second = run_python(
            "runtime_control.py", "register-agent", str(case), "--stage", "Stage0",
            "--role", "Supervisor", "--agent-id", "platform-1", "--task", "duplicate",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("duplicate agent id", second.stderr)

    def test_skipped_stage_is_rejected(self):
        case = self.init_case()
        result = run_python(
            "runtime_control.py", "set-stage", str(case), "--stage", "StageB",
            "--run-state", "in_progress", "--attempt-count", "1", "--repair-count", "0",
            "--next-action", "invalid skip",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dependency gate closed", result.stderr)

    def test_stage_c_executors_are_strictly_serial(self):
        case = self.init_case()
        append_event(case, "stage_updated", {"stage": "StageC", "run_state": "in_progress", "attempt_count": 1, "repair_count": 0, "supervisor_advice_agent_id": None, "next_action": "target T1"}, timestamp="2026-08-29T12:00:01-07:00")
        render_case(case)
        first = run_python("runtime_control.py", "register-agent", str(case), "--stage", "StageC", "--role", "Executor", "--agent-id", "executor-1", "--task", "T1", "--target-id", "T1", "--now", "2026-08-29T12:00:02-07:00")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = run_python("runtime_control.py", "register-agent", str(case), "--stage", "StageC", "--role", "Executor", "--agent-id", "executor-2", "--task", "T2", "--target-id", "T2")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("must be serial", second.stderr)

    def test_attempt_four_requires_finished_supervisor_advice(self):
        case = self.init_case()
        denied = run_python(
            "runtime_control.py", "set-stage", str(case), "--stage", "Stage0",
            "--run-state", "in_progress", "--attempt-count", "4", "--repair-count", "3",
            "--next-action", "attempt four",
        )
        self.assertNotEqual(denied.returncode, 0)
        register = run_python(
            "runtime_control.py", "register-agent", str(case), "--stage", "Stage0",
            "--role", "Supervisor", "--agent-id", "supervisor-advice", "--task", "give advice",
        )
        self.assertEqual(register.returncode, 0, register.stderr)
        active_denied = run_python(
            "runtime_control.py", "set-stage", str(case), "--stage", "Stage0",
            "--run-state", "in_progress", "--attempt-count", "4", "--repair-count", "3",
            "--supervisor-advice-agent-id", "supervisor-advice", "--next-action", "attempt four",
        )
        self.assertNotEqual(active_denied.returncode, 0)
        finish = run_python(
            "runtime_control.py", "finish-agent", str(case), "--agent-id", "supervisor-advice",
            "--outcome", "produced_artifact", "--next-action", "apply advice",
        )
        self.assertEqual(finish.returncode, 0, finish.stderr)
        allowed = run_python(
            "runtime_control.py", "set-stage", str(case), "--stage", "Stage0",
            "--run-state", "in_progress", "--attempt-count", "4", "--repair-count", "3",
            "--supervisor-advice-agent-id", "supervisor-advice", "--next-action", "attempt four",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_event_log_rebuild_and_tamper_detection(self):
        case = self.init_case()
        audit = audit_case(case)
        self.assertEqual(audit["event_count"], 1)
        state_path = case / "work" / "runtime_state.json"
        state_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            audit_case(case)
        render_case(case)
        self.assertTrue(audit_case(case)["ok"])
        log = case / "logs" / "runtime_events.jsonl"
        event = json.loads(log.read_text(encoding="utf-8").strip())
        event["sequence"] = 2
        log.write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sequence mismatch"):
            audit_case(case)

    def test_truncated_and_wrong_previous_hash_are_detected(self):
        case = self.init_case()
        append_event(case, "heartbeat_recorded", {"agent_id": "none", "stage": "Stage0", "target_id": None}, timestamp="2026-08-29T12:00:01-07:00")
        render_case(case)
        log = case / "logs" / "runtime_events.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        second = json.loads(lines[1])
        second["previous_hash"] = "0" * 64
        lines[1] = json.dumps(second, sort_keys=True)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "previous_hash"):
            audit_case(case)
        log.write_text(lines[0], encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "truncated"):
            audit_case(case)

    def test_target_and_overall_decisions_open_stage_d_dependency(self):
        case = self.init_case()
        append_event(
            case,
            "target_recorded",
            {"target_id": "Fig2", "stage": "StageC", "target": {"target_id": "Fig2", "stage_id": "StageC", "route": "independent_reimplementation", "run_state": "blocked", "claim_status": "BLOCKED", "comparison_verdict": "blocked_missing_candidate", "legacy_status": None, "evidence_rows": []}, "next_action": "summarize"},
            timestamp="2026-08-29T12:00:01-07:00",
        )
        append_event(case, "agent_registered", {"agent": {"agent_id": "critic-summary", "run_id": "run-0001", "source": "platform_spawn_result", "stage": "StageCSummary", "role": "Critic", "target_id": None, "task": "review summary", "handoff_in": [], "handoff_out": [], "replacement_for": None, "registered_at": "2026-08-29T12:00:02-07:00", "last_heartbeat_at": "2026-08-29T12:00:02-07:00", "finished_at": None, "run_state": "running", "outcome": "pending", "materialization": None}, "stage": "StageCSummary", "target_id": None, "next_action": "review"}, timestamp="2026-08-29T12:00:02-07:00")
        append_event(case, "agent_finished", {"agent_id": "critic-summary", "stage": "StageCSummary", "target_id": None, "outcome": "critique_pass", "handoff_out": [], "materialization": "direct_agent_write", "next_action": "record review"}, timestamp="2026-08-29T12:00:03-07:00")
        append_event(case, "stage_updated", {"stage": "StageCSummary", "run_state": "completed", "attempt_count": 1, "repair_count": 0, "supervisor_advice_agent_id": None, "next_action": "decisions"}, timestamp="2026-08-29T12:00:04-07:00")
        append_event(case, "review_recorded", {"stage": "StageCSummary", "agent_id": "critic-summary", "decision": "pass", "report": "work/critic_reports/summary.json", "reason": "", "next_action": "decisions"}, timestamp="2026-08-29T12:00:05-07:00")
        render_case(case)
        self.assertFalse(_required_decisions_present(load_state(case))[0])
        target = run_python(
            "runtime_control.py", "record-decision", str(case), "--scope", "target", "--round", "1",
            "--target-id", "Fig2", "--decision", "accept_blocked", "--next-action", "overall decision",
            "--now", "2026-08-29T12:00:06-07:00",
        )
        self.assertEqual(target.returncode, 0, target.stderr)
        overall = run_python(
            "runtime_control.py", "record-decision", str(case), "--scope", "overall", "--round", "1",
            "--decision", "conservative_archive", "--next-action", "StageD gate",
            "--now", "2026-08-29T12:00:07-07:00",
        )
        self.assertEqual(overall.returncode, 0, overall.stderr)
        state = load_state(case)
        self.assertTrue(_required_decisions_present(state)[0])
        self.assertEqual(_dependency_errors(case, state, "StageD"), [])

    def test_stage_c_target_and_aggregate_preflights_are_both_required(self):
        case = self.init_case()
        append_event(case, "target_recorded", {"target_id": "T1", "stage": "StageC", "target": {"target_id": "T1", "stage_id": "StageC", "route": "independent_reimplementation", "run_state": "completed", "claim_status": "INCONCLUSIVE", "comparison_verdict": "not_evaluated", "legacy_status": None, "evidence_rows": []}, "next_action": "preflight"}, timestamp="2026-08-29T12:00:01-07:00")
        render_case(case)
        errors = _dependency_errors(case, load_state(case), "StageCSummary")
        self.assertTrue(any("aggregate" in item for item in errors))
        self.assertTrue(any("target T1 preflight" in item for item in errors))
        append_event(case, "preflight_recorded", {"stage": "StageC", "target_id": "T1", "result": "pass", "report": "stage_gates/t1.json", "attempt_count": 1, "producer_agent_id": "executor", "next_action": "aggregate"}, timestamp="2026-08-29T12:00:02-07:00")
        append_event(case, "preflight_recorded", {"stage": "StageC", "target_id": None, "result": "pass", "report": "stage_gates/aggregate.json", "attempt_count": 1, "producer_agent_id": None, "next_action": "summary"}, timestamp="2026-08-29T12:00:03-07:00")
        render_case(case)
        remaining = _dependency_errors(case, load_state(case), "StageCSummary")
        self.assertFalse(any('preflight' in item for item in remaining), remaining)
        self.assertTrue(any('independent Critic review' in item for item in remaining), remaining)


if __name__ == "__main__":
    unittest.main()
