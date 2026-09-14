"""Synthetic incidents. IDs below are test doubles, not real-agent validation."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from _support import FIXTURES, make_plan, run_python, write_json
import evidence_runtime as rt
import evidence_execution as execution
from runtime_model import (append_event, audit_case, load_json, load_state, read_events, render_case,
                           render_chat_card, render_completion_evidence, render_progress,
                           render_target_matrix, sha256_file)
from runtime_control import _dependency_errors, _target_review_errors
from runtime_schema import _validate, validate_document, SchemaValidationError
from scope_authorization import create_scope, bind_plan, load_ledger, reserve_run, scope_differences


class V5RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pra-v5-regression-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        shutil.copytree(FIXTURES / 'safe_source', self.source)
        self.plan_path = self.root / 'plan.json'
        self.plan = make_plan(self.plan_path, self.source)
        self.scope_path = self.root / 'scope.json'
        self.receipt_path = self.root / 'receipt.json'

    def tearDown(self):
        self.temp.cleanup()

    def grant(self, *, policy='task_scoped', max_runs=12):
        return create_scope([self.plan_path], self.scope_path,
            'Reproduce this synthetic target locally, including bounded implementation and presentation repairs.',
            'synthetic-user-message:1', wall_time_seconds=120, disk_mb=50, max_runs=max_runs, policy=policy)

    def bind(self, plan_path=None, output=None):
        plan_path = plan_path or self.plan_path
        plan = load_json(plan_path)
        review_path = self.root / 'execution-review.json'
        write_json(review_path, {'source': 'platform_spawn_result', 'producer_agent_id': 'test-producer',
            'reviewer_agent_id': 'test-critic', 'decision': 'pass', 'plan_sha256': sha256_file(plan_path),
            'source_tree_hash': plan['source']['tree_hash'], 'findings': 'Synthetic fixture review; test double only.',
            'implementation_repair_verified': True})
        return bind_plan(plan_path, self.scope_path, review_path, output or self.receipt_path)

    def refresh(self, plan=None, path=None):
        plan = plan or self.plan
        plan['source'].update(rt.source_fingerprint(self.source))
        write_json(path or self.plan_path, plan)
        return plan

    def execute(self, name='bundle'):
        return rt.execute_plan(self.plan_path, self.receipt_path, self.root / name)

    def init(self, *, language='en', policy='task_scoped'):
        case = self.root / 'case'
        result = run_python('runtime_control.py', 'init-case', case, '--case-id', 'test-case',
            '--task-mode', 'reproduce_specified_targets', '--paper-title', 'Synthetic paper', '--source', 'offline',
            '--language', language, '--execution-policy', policy, '--spawn-capability-confirmed')
        self.assertEqual(result.returncode, 0, result.stderr)
        return case

    def prepare_crash(self):
        self.grant()
        receipt = self.bind()
        bundle = self.root / 'crashed'
        bundle.mkdir()
        shutil.copy2(self.plan_path, bundle / 'plan.json')
        shutil.copy2(self.receipt_path, bundle / 'approval.json')
        reserve_run(receipt, self.plan, bundle, 'synthetic-crashed-run')
        write_json(bundle / 'run_record.json', {'schema_version': 5, 'run_id': 'synthetic-crashed-run',
            'run_state': 'preparing', 'worker_pid': None, 'child_pid': None,
            'command_template': self.plan['command'], 'plan_sha256': sha256_file(self.plan_path),
            'started_at': '2026-01-01T00:00:00+00:00'})
        return bundle

    def record_result(self, case, result, bundle):
        command = run_python('runtime_control.py', 'record-target', case, '--target-id', result['target_id'],
            '--route', result['route'], '--run-state', 'completed', '--claim-status', result['claim_status'],
            '--comparison-verdict', result['comparison_verdict'], '--evidence-bundle', bundle, '--next-action', 'report')
        self.assertEqual(command.returncode, 0, command.stderr)

    def test_json_boolean_and_sibling_constraints(self):
        approval = self.root / 'approval.json'
        value = rt.write_approval(self.plan_path, approval, 'synthetic explicit per-run user')
        value['allow_execute'] = 1
        with self.assertRaises(SchemaValidationError):
            validate_document(value, 'approval')
        self.assertTrue(_validate(2, {'oneOf': [{'const': 2}], 'maximum': 1}, '$'))
        self.assertTrue(_validate([True], {'const': [1]}, '$'))

    def test_code_repair_reuses_grant_but_requires_new_exact_review(self):
        self.grant()
        self.bind()
        original_scope = self.scope_path.read_bytes()
        runner = self.source / 'safe_runner.py'
        runner.write_text(runner.read_text() + '\n# implementation repair, scientific contract unchanged\n', encoding='utf-8')
        self.refresh()
        self.plan['plan_id'] += '-repair'
        write_json(self.plan_path, self.plan)
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.execute()
        receipt = self.bind()
        self.assertEqual(receipt['origin'], 'inherited_task_scope')
        self.assertNotIn('approved_by', receipt)
        self.assertEqual(self.scope_path.read_bytes(), original_scope)
        self.assertEqual(self.execute()['verification_status'], 'verified')

    def test_scope_changes_block_but_lower_memory_does_not(self):
        scope = self.grant()
        lower = copy.deepcopy(self.plan)
        lower['budget']['memory_mb']['value'] = 128
        self.assertEqual(scope_differences(scope, lower), [])
        for field, changed in [('scientific_contract', {'model': 'different physics'}),
                               ('execution_location', 'unapproved-host')]:
            plan = copy.deepcopy(self.plan)
            plan[field] = changed
            self.assertTrue(scope_differences(scope, plan))
        larger = copy.deepcopy(self.plan)
        larger['budget']['wall_time_seconds'] = 11
        self.assertTrue(scope_differences(scope, larger))

    def test_command_entry_repair_inherits_scope_after_explicit_independent_review(self):
        self.grant()
        (self.source / 'safe_runner.py').rename(self.source / 'corrected_runner.py')
        self.plan['command']['argv'] = [value.replace('safe_runner.py', 'corrected_runner.py') for value in self.plan['command']['argv']]
        self.plan['implementation_repair_reason'] = 'Correct script entry name without changing scientific parameters.'
        self.refresh()
        self.bind()
        self.assertEqual(self.execute()['verification_status'], 'verified')
        self.plan['scientific_contract'] = {'model': 'different science'}
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'scope decision'):
            self.bind()

    def test_preview_policy_cannot_bind_or_enter_execution(self):
        self.grant(policy='preview_only')
        with self.assertRaisesRegex(ValueError, 'preview_only'):
            self.bind()
        case = self.init(policy='preview_only')
        self.assertTrue(any('preview_only' in error for error in _dependency_errors(case, load_state(case), 'StageC')))
        self.assertFalse((self.root / 'bundle').exists())

    def test_cumulative_budget_is_not_reset_by_rebinding(self):
        self.grant(max_runs=1)
        self.bind()
        self.execute()
        self.bind()
        with self.assertRaisesRegex(ValueError, 'cumulative run budget'):
            self.execute('second')
        self.assertEqual(len(load_ledger(self.scope_path)['runs']), 1)

    def test_same_bundle_is_idempotent(self):
        self.grant(); self.bind()
        first = self.execute()
        run_id = load_json(self.root / 'bundle/run_record.json')['run_id']
        with patch.object(execution.subprocess, 'Popen', side_effect=AssertionError('duplicate execution')):
            second = self.execute()
        self.assertEqual(first['manifest_sha256'], second['manifest_sha256'])
        self.assertEqual(load_json(self.root / 'bundle/run_record.json')['run_id'], run_id)
        self.assertEqual(len(load_ledger(self.scope_path)['runs']), 1)

    def test_source_changed_after_parent_preview_never_executes(self):
        bundle = self.prepare_crash()
        marker = self.root / 'unreviewed-marker'
        (self.source / 'safe_runner.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
        execution.run_worker(bundle)
        self.assertFalse(marker.exists())
        self.assertEqual(rt.verify_bundle(bundle)['claim_status'], 'INCONCLUSIVE')
        entry = load_ledger(self.scope_path)['runs']['synthetic-crashed-run']
        self.assertEqual(entry['status'], 'finished')
        self.assertFalse(entry['scientific'])

    def test_worker_launch_failure_is_finalized_and_releases_budget(self):
        self.grant(); self.bind()
        original = subprocess.Popen
        def fail_worker(argv, *args, **kwargs):
            if any(str(value).endswith('evidence_execution.py') for value in argv):
                raise OSError('synthetic unavailable worker')
            return original(argv, *args, **kwargs)
        with patch.object(subprocess, 'Popen', side_effect=fail_worker):
            result = self.execute()
        self.assertEqual(result['verification_status'], 'execution_failed')
        self.assertEqual(next(iter(load_ledger(self.scope_path)['runs'].values()))['status'], 'finished')
        self.assertFalse((self.root / 'bundle/.launch.lock').exists())

    def test_preparation_copy_failure_does_not_reserve_budget(self):
        self.grant(); self.bind()
        original = shutil.copy2
        def fail_approval(source, destination, *args, **kwargs):
            if Path(destination).name == 'approval.json':
                raise OSError('synthetic transport write failure')
            return original(source, destination, *args, **kwargs)
        with patch.object(shutil, 'copy2', side_effect=fail_approval):
            result = self.execute()
        self.assertEqual(result['run_state'], 'failed')
        self.assertTrue(result['preparation_failed'])
        self.assertEqual(load_ledger(self.scope_path)['runs'], {})
        self.assertEqual(self.execute('repaired-directory')['verification_status'], 'verified')

    def test_launch_metadata_failure_keeps_live_worker_and_scope_reserved(self):
        runner = self.source / 'safe_runner.py'
        runner.write_text(runner.read_text() + '\nimport time\ntime.sleep(2)\n')
        self.refresh(); self.grant(); self.bind()
        original = execution.atomic_write_json
        def fail_launch(path, value):
            if Path(path).name == 'launch.json':
                raise OSError('synthetic launch metadata failure after Popen')
            return original(path, value)
        bundle = self.root / 'bundle'
        with patch.object(execution, 'atomic_write_json', side_effect=fail_launch):
            result = execution.start(self.plan_path, self.receipt_path, bundle, detach=True)
        try:
            self.assertIn('synthetic', result['launch_metadata_error'])
            self.assertEqual(next(iter(load_ledger(self.scope_path)['runs'].values()))['status'], 'reserved')
            with self.assertRaisesRegex(ValueError, 'existing run owns'):
                reserve_run(load_json(self.receipt_path), self.plan, self.root / 'duplicate', 'duplicate')
        finally:
            for _ in range(150):
                if (bundle / 'evidence_index.json').exists():
                    break
                time.sleep(.1)
        self.assertEqual(rt.verify_bundle(bundle)['verification_status'], 'verified')
        self.assertEqual(len(load_ledger(self.scope_path)['runs']), 1)

    def test_worker_progress_write_failure_waits_for_child_before_settling(self):
        runner = self.source / 'safe_runner.py'
        runner.write_text(runner.read_text() + '\nimport time\ntime.sleep(1)\n')
        self.refresh()
        bundle = self.prepare_crash()
        original = execution.atomic_write_json
        def fail_progress(path, value):
            if Path(path).name == 'run_record.json' and value.get('run_state') == 'running':
                raise OSError('synthetic running-state write failure')
            return original(path, value)
        with patch.object(execution, 'atomic_write_json', side_effect=fail_progress):
            execution.run_worker(bundle)
        record = load_json(bundle / 'run_record.json')
        self.assertFalse(execution.process_alive(record['child_pid']))
        self.assertGreaterEqual(record['elapsed_seconds'], 1)
        self.assertEqual(rt.verify_bundle(bundle)['verification_status'], 'verified')

    def test_sealed_run_recovers_accounting_without_rewriting_evidence(self):
        bundle = self.prepare_crash()
        with patch('scope_authorization.finish_run', side_effect=ValueError('temporary ledger lock')):
            execution.run_worker(bundle)
        before = {p.relative_to(bundle).as_posix(): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
        self.assertEqual(load_ledger(self.scope_path)['runs']['synthetic-crashed-run']['status'], 'reserved')
        result = execution.recover(bundle)
        self.assertTrue(result['accounting_recovered'])
        self.assertEqual(load_ledger(self.scope_path)['runs']['synthetic-crashed-run']['status'], 'finished')
        self.assertEqual(before, {p.relative_to(bundle).as_posix(): p.read_bytes() for p in bundle.rglob('*') if p.is_file()})

    def test_worker_resolves_path_alias_before_recording_artifact_paths(self):
        bundle = self.prepare_crash()
        traversed = bundle.parent / 'traversed'
        traversed.mkdir()
        alias = traversed / '..' / bundle.name
        execution.run_worker(alias)
        result = rt.verify_bundle(bundle)
        self.assertEqual(result['verification_status'], 'verified', result)

    def test_inventory_failure_still_seals_negative_evidence(self):
        bundle = self.prepare_crash()
        original = rt._inventory
        calls = 0
        def fail_tail(path, **kwargs):
            nonlocal calls
            if Path(path).resolve() == self.source.resolve():
                calls += 1
                if calls >= 3:
                    raise OSError('synthetic failed source inventory')
            return original(path, **kwargs)
        with patch.object(rt, '_inventory', side_effect=fail_tail):
            execution.run_worker(bundle)
        self.assertGreaterEqual(calls, 3, 'the failure must actually be injected on every platform')
        self.assertTrue((bundle / 'evidence_index.json').exists())
        self.assertEqual(rt.verify_bundle(bundle)['claim_status'], 'INCONCLUSIVE')
        self.assertEqual(load_ledger(self.scope_path)['runs']['synthetic-crashed-run']['status'], 'finished')

    def test_missing_output_is_verifiable_failure(self):
        (self.source / 'safe_runner.py').write_text('raise RuntimeError("synthetic missing output")\n')
        self.refresh(); self.grant(); self.bind()
        result = self.execute()
        self.assertEqual(result['verification_status'], 'execution_failed')
        self.assertEqual(result['claim_status'], 'INCONCLUSIVE')
        self.assertFalse(load_json(self.root / 'bundle/observation.json')['bound_to_run'])
        self.assertEqual(rt.verify_bundle(self.root / 'bundle')['verification_status'], 'execution_failed')
        with self.assertRaisesRegex(ValueError, 'same failed plan'):
            self.execute('unchanged-failure')

    def test_raw_logs_are_charged_to_disk_budget(self):
        path = self.source / 'safe_runner.py'
        path.write_text(path.read_text() + '\nprint("x" * (3 * 1024 * 1024))\n')
        self.refresh(); self.grant(); self.bind()
        result = self.execute()
        self.assertEqual(result['claim_status'], 'INCONCLUSIVE')
        self.assertIn('disk_mb', load_json(self.root / 'bundle/run_record.json')['resource_violations'])
        self.assertGreater(next(iter(load_ledger(self.scope_path)['runs'].values()))['disk_mb'], 3)

    def test_dead_worker_recovery_never_restarts_computation(self):
        bundle = self.prepare_crash()
        with patch.object(execution, 'process_alive', return_value=False), patch.object(subprocess, 'Popen', side_effect=AssertionError('must not restart')):
            result = execution.recover(bundle)
        self.assertEqual(result['verification_status'], 'execution_failed')
        self.assertEqual(result['claim_status'], 'INCONCLUSIVE')
        entry = load_ledger(self.scope_path)['runs']['synthetic-crashed-run']
        self.assertEqual(entry['status'], 'finished')
        self.assertEqual(entry['wall_time_seconds'], self.plan['budget']['wall_time_seconds'])

    def test_recovery_refuses_live_or_unknown_external_completion(self):
        bundle = self.prepare_crash()
        with patch.object(execution, 'process_alive', return_value=True):
            with self.assertRaisesRegex(ValueError, 'alive'):
                execution.recover(bundle)
        (bundle / 'output').mkdir()
        write_json(bundle / 'output/run_handle.json', {'scheduler_id': 'synthetic-job'})
        with self.assertRaisesRegex(ValueError, 'completion is uncertain'):
            execution.recover(bundle)

    def test_latest_negative_status_clears_selected_old_success(self):
        self.grant(); self.bind(); result = self.execute()
        case = self.init()
        self.record_result(case, result, self.root / 'bundle')
        failed = run_python('runtime_control.py', 'record-target', case, '--target-id', result['target_id'],
            '--run-state', 'failed', '--claim-status', 'INCONCLUSIVE', '--comparison-verdict', 'not_evaluated', '--next-action', 'review failure')
        self.assertEqual(failed.returncode, 0, failed.stderr)
        built = run_python('runtime_control.py', 'build-final-claims', case)
        self.assertEqual(built.returncode, 0, built.stderr)
        claims = load_json(case / 'reports/final_response_allowed_claims.json')
        self.assertEqual(claims['paper_targets_accepted'], 0)
        self.assertEqual(claims['claims'][0]['claim_status'], 'INCONCLUSIVE')
        self.assertTrue((self.root / 'bundle/evidence_index.json').exists())
        self.assertTrue(audit_case(case)['ok'])

    def test_internal_check_is_not_counted_as_paper_reproduction(self):
        self.plan['target_kind'] = 'internal_check'
        write_json(self.plan_path, self.plan)
        self.grant(); self.bind(); result = self.execute()
        case = self.init()
        self.record_result(case, result, self.root / 'bundle')
        built = run_python('runtime_control.py', 'build-final-claims', case)
        self.assertEqual(built.returncode, 0, built.stderr)
        claims = load_json(case / 'reports/final_response_allowed_claims.json')
        self.assertEqual(claims['paper_targets_accepted'], 0)
        self.assertEqual(claims['internal_checks'], 1)

    def test_presentation_repair_inherits_scope_without_scientific_attempt(self):
        self.grant(); self.bind(); numeric = self.execute()
        parent = self.root / 'bundle'
        shutil.copy2(parent / 'output/observed.json', self.source / 'numeric.json')
        (self.source / 'plot.py').write_text('import pathlib, sys, json\nvalue=json.loads(pathlib.Path(__file__).with_name("numeric.json").read_text())["value"]\npathlib.Path(sys.argv[1]).write_text("<svg xmlns=\\"http://www.w3.org/2000/svg\\"><text>value="+str(value)+"</text></svg>")\n')
        derivative = copy.deepcopy(self.plan)
        derivative.update(plan_id='plot-repair', operation_kind='presentation',
                          presentation_of={'bundle': str(parent), 'manifest_sha256': numeric['manifest_sha256']})
        derivative['inputs'] = [{'path': 'numeric.json', 'sha256': sha256_file(self.source / 'numeric.json')}]
        derivative['command']['argv'] = [self.plan['command']['argv'][0], '{source}/plot.py', '{output}/figure.svg']
        derivative['outputs'] = [{'path': 'figure.svg', 'kind': 'presentation'}]
        derivative['observation'] = {'method': 'file_sha256', 'artifact': 'figure.svg', 'selector': None, 'unit': None, 'bound_to_run': True}
        derivative['comparison'] = {'type': 'manual', 'reference': None, 'acceptance': {}, 'evidence_strength': 'visual_only', 'unit_conversion': {'scale': 1.0, 'offset': 0.0}}
        plan_path = self.root / 'presentation-plan.json'
        self.refresh(derivative, plan_path)
        receipt_path = self.root / 'presentation-receipt.json'
        self.bind(plan_path, receipt_path)
        result = rt.execute_plan(plan_path, receipt_path, self.root / 'presentation')
        self.assertEqual(result['claim_status'], 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(sum(entry['scientific'] for entry in load_ledger(self.scope_path)['runs'].values()), 1)
        self.assertEqual(rt.verify_bundle(parent)['manifest_sha256'], numeric['manifest_sha256'])
        case = self.init()
        self.record_result(case, numeric, parent)
        bypass = run_python('runtime_control.py', 'record-target', case, '--target-id', result['target_id'],
                            '--presentation-status', 'passed', '--next-action', 'Attempt unreviewed quality upgrade')
        self.assertNotEqual(bypass.returncode, 0)
        append_event(case, 'stage_updated', {'stage': 'StageC', 'run_state': 'in_progress',
            'attempt_count': 1, 'repair_count': 0, 'supervisor_advice_agent_id': None, 'next_action': 'Review plot fixture'})
        render_case(case, artifacts=False)
        report_path = case / 'work/plot-review.json'
        review = {'schema_version': 5, 'stage': 'StageC', 'target_id': result['target_id'],
                  'reviewer_agent_id': 'test-plot-critic', 'decision': 'pass', 'summary': 'Synthetic plot review.',
                  'findings': [], 'required_repairs': [], 'evidence_reviewed': [str(self.root / 'presentation')],
                  'presentation_manifest_sha256': result['manifest_sha256']}
        write_json(report_path, review)
        command = run_python('runtime_control.py', 'register-agent', case, '--stage', 'StageC', '--role', 'Critic',
            '--agent-id', 'test-plot-critic', '--target-id', result['target_id'], '--task', 'Synthetic presentation test double')
        self.assertEqual(command.returncode, 0, command.stderr)
        command = run_python('runtime_control.py', 'finish-agent', case, '--agent-id', 'test-plot-critic',
            '--outcome', 'critique_pass', '--handoff-out', 'work/plot-review.json', '--materialization', 'direct_agent_write',
            '--next-action', 'Record reviewed presentation')
        self.assertEqual(command.returncode, 0, command.stderr)
        args = ('runtime_control.py', 'record-presentation', case, '--target-id', result['target_id'],
                '--bundle', self.root / 'presentation', '--reviewer-id', 'test-plot-critic',
                '--report', 'work/plot-review.json', '--status', 'passed')
        command = run_python(*args)
        self.assertEqual(command.returncode, 0, command.stderr)
        target = load_state(case)['targets'][result['target_id']]
        self.assertEqual(target['presentation_status'], 'passed')
        self.assertEqual(load_state(case)['runtime']['current_stage'], 'StageC')
        self.assertEqual(read_events(case)[-1]['payload']['stage'], 'StageC')
        self.assertEqual(target['claim_status'], numeric['claim_status'])
        self.assertEqual(target['verified_evidence']['manifest_sha256'], numeric['manifest_sha256'])
        review['presentation_manifest_sha256'] = '0' * 64
        write_json(report_path, review)
        self.assertNotEqual(run_python(*args).returncode, 0)
        # Reviewing the original numerical bundle's figure needs no extra execution.
        review['presentation_manifest_sha256'] = numeric['manifest_sha256']
        write_json(report_path, review)
        direct = list(args)
        direct[direct.index('--bundle') + 1] = parent
        self.assertEqual(run_python(*direct).returncode, 0)
        self.refresh()
        self.bind()
        fresh = self.execute('fresh-numerical')
        self.record_result(case, fresh, self.root / 'fresh-numerical')
        self.assertEqual(load_state(case)['targets'][result['target_id']]['presentation_status'], 'not_checked')

    def test_attempt_cap_and_engineering_counter_are_separate(self):
        case = self.init()
        before = audit_case(case)['event_count']
        denied = run_python('runtime_control.py', 'set-stage', case, '--stage', 'Stage0', '--run-state', 'in_progress',
            '--attempt-count', '5', '--repair-count', '4', '--next-action', 'invalid fifth attempt')
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(audit_case(case)['event_count'], before)
        (case / 'work/fix.txt').write_text('corrected isolated package metadata')
        result = run_python('runtime_control.py', 'record-engineering-repair', case, '--stage', 'Stage0',
            '--category', 'environment', '--diagnosis', 'bad package identifier', '--change', 'correct package identifier',
            '--evidence', 'work/fix.txt', '--next-action', 'retry within scope')
        self.assertEqual(result.returncode, 0, result.stderr)
        stage = load_state(case)['stages']['Stage0']
        self.assertEqual(stage['attempt_count'], 0)
        self.assertEqual(stage['engineering_repair_count'], 1)

    def test_independent_target_review_cannot_cover_another_target_or_changed_report(self):
        self.grant(); self.bind(); result = self.execute()
        case = self.init()
        self.record_result(case, result, self.root / 'bundle')
        append_event(case, 'stage_updated', {'stage': 'StageC', 'run_state': 'in_progress',
            'attempt_count': 1, 'repair_count': 0, 'supervisor_advice_agent_id': None, 'next_action': 'Review fixture'})
        render_case(case, artifacts=False)
        report = {'schema_version': 5, 'stage': 'StageC', 'target_id': result['target_id'],
                  'reviewer_agent_id': 'test-target-critic', 'decision': 'pass', 'summary': 'Synthetic review.',
                  'findings': [], 'required_repairs': [], 'evidence_reviewed': ['bundle'],
                  'evidence_manifest_sha256': result['manifest_sha256']}
        write_json(case / 'work/target-review.json', report)
        commands = [
            ('register-agent', '--stage', 'StageC', '--role', 'Critic', '--agent-id', 'test-target-critic',
             '--target-id', result['target_id'], '--task', 'Synthetic target review test double'),
            ('finish-agent', '--agent-id', 'test-target-critic', '--outcome', 'critique_pass',
             '--handoff-out', 'work/target-review.json', '--materialization', 'direct_agent_write', '--next-action', 'Record'),
            ('record-review', '--stage', 'StageC', '--role', 'Critic', '--agent-id', 'test-target-critic',
             '--decision', 'pass', '--report', 'work/target-review.json', '--next-action', 'Continue')]
        for command in commands:
            actual = run_python('runtime_control.py', command[0], case, *command[1:])
            self.assertEqual(actual.returncode, 0, actual.stderr)
        state = load_state(case)
        self.assertEqual(_target_review_errors(case, state, result['target_id']), [])
        state['targets']['another-target'] = {'run_state': 'completed', 'claim_status': 'INCONCLUSIVE',
                                             'independent_review': state['targets'][result['target_id']]['independent_review']}
        self.assertTrue(_target_review_errors(case, state, 'another-target'))
        report['summary'] = 'Changed after recorded review'
        write_json(case / 'work/target-review.json', report)
        self.assertTrue(_target_review_errors(case, state, result['target_id']))

    def test_views_separate_target_preflight_and_presentation_from_stage_state(self):
        case = self.init()
        state = load_state(case)
        state['runtime'].update(current_stage='StageC', active_target='Fig1')
        state['stages']['StageC']['preflight']['targets']['Fig1'] = {'result': 'PASS'}
        state['targets']['Fig1'] = {'target_id': 'Fig1', 'claim': 'Ground energy',
            'paper_anchors': ['Eq.1'], 'route': 'numerical', 'run_state': 'completed',
            'claim_status': 'REPRODUCED_WITHIN_ACCEPTANCE', 'comparison_verdict': 'within_acceptance',
            'evidence_plan': 'plan.json', 'presentation_status': 'needs_repair',
            'scientific_attempt_count': 1, 'engineering_repair_count': 2,
            'independent_review': {'decision': 'pass'}}
        card = render_chat_card(state)
        self.assertIn('Aggregate preflight', card)
        self.assertIn('Target preflight', card)
        self.assertIn('| 1/2 | PASS | pass | needs_repair |', card)
        for rendered in (card, render_progress(state, []), render_completion_evidence(state),
                         render_target_matrix({'schema_version': 5, 'targets': list(state['targets'].values())})):
            self.assertIn('Presentation', rendered)
            self.assertIn('needs_repair', rendered)
        legacy = copy.deepcopy(state)
        legacy['schema_version'] = 4
        self.assertNotIn('Presentation', render_progress(legacy, []))
        self.assertNotIn('Target preflight', render_chat_card(legacy))

    def test_negative_summary_needs_no_user_decision_form(self):
        case = self.init()
        state = load_state(case)
        state['stages']['StageCSummary']['run_state'] = 'completed'
        state['stages']['StageCSummary']['review']['decision'] = 'pass'
        state['targets']['T'] = {'run_state': 'failed', 'claim_status': 'INCONCLUSIVE'}
        self.assertEqual(state['decisions']['rounds'], {})
        self.assertEqual(_dependency_errors(case, state, 'StageD'), [])

    def test_concurrent_append_cannot_corrupt_sequence(self):
        case = self.init()
        def add(index):
            for _ in range(100):
                try:
                    append_event(case, 'heartbeat_recorded', {'agent_id': 'synthetic-' + str(index)})
                    return
                except ValueError as exc:
                    if 'writer is busy' not in str(exc):
                        raise
                    time.sleep(0.005)
            self.fail('writer remained busy')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(add, range(12)))
        render_case(case)
        self.assertEqual(audit_case(case)['event_count'], 13)


if __name__ == '__main__':
    unittest.main()
