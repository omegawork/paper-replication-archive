"""Synthetic projection, legacy, language and installation regressions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from _support import run_python, write_json
from evidence_runtime import _inventory, source_fingerprint
from obsidian_markdown_lint import validate as lint_markdown
from runtime_model import (audit_case, canonical_json_bytes, event_digest, load_json,
    load_state, read_events, render_case, replay_events, sha256_bytes, sha256_file)
from runtime_schema import SchemaValidationError, validate_document

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/paper-replication-archive'


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pra-portable-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        case = self.root / 'case'
        result = run_python('runtime_control.py', 'init-case', case, '--case-id', 'fixture',
            '--task-mode', 'reproduce_specified_targets', '--paper-title', 'Synthetic', '--source', 'offline',
            '--language', 'en', '--spawn-capability-confirmed')
        self.assertEqual(result.returncode, 0, result.stderr)
        return case

    def inventory(self, root, *, metadata=False):
        return {p.relative_to(root).as_posix(): (sha256_file(p), p.stat().st_mtime_ns) if metadata else sha256_file(p)
                for p in root.rglob('*') if p.is_file()}

    def test_v4_audit_status_export_are_read_only_and_mutations_rejected(self):
        case = self.init()
        # Build a synthetic legacy fixture, not a migration of real user history.
        metadata = load_json(case / 'work/case.json')
        metadata.pop('language')
        metadata.update(schema_version=4, execution_policy='preview_only')
        event = read_events(case)[0]
        event['schema_version'] = 4
        event['payload']['case_sha256'] = sha256_bytes(canonical_json_bytes(metadata))
        event['hash'] = event_digest({k: v for k, v in event.items() if k != 'hash'})
        write_json(case / 'work/case.json', metadata)
        (case / 'logs/runtime_events.jsonl').write_bytes(canonical_json_bytes(event) + b'\n')
        state = replay_events(metadata, [event])
        write_json(case / 'work/runtime_state.json', state)
        write_json(case / 'work/agent_registry.json', {'schema_version': 4, 'agents': []})
        write_json(case / 'work/user_decisions.json', state['decisions'])
        before = self.inventory(case, metadata=True)
        self.assertTrue(audit_case(case)['read_only_legacy'])
        for command in ('audit-case', 'status'):
            result = run_python('runtime_control.py', command, case)
            self.assertEqual(result.returncode, 0, result.stderr)
        exported = self.root / 'exported'
        result = run_python('runtime_control.py', 'export-case', case, '--destination', exported)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.inventory(case), self.inventory(exported))
        for command in ('render-case', 'build-final-claims'):
            result = run_python('runtime_control.py', command, case)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('read-only', result.stderr)
        self.assertEqual(before, self.inventory(case, metadata=True))

    def test_matrix_projection_matches_events_and_audit_never_repairs_it(self):
        case = self.init()
        matrix = load_json(SKILL / 'templates/target_matrix_template.json')
        matrix['case_id'] = 'fixture'
        matrix_file = self.root / 'targets.json'
        write_json(matrix_file, matrix)
        result = run_python('runtime_control.py', 'define-targets', case, '--file', matrix_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_python('runtime_control.py', 'record-target', case, '--target-id', 'Fig3b',
            '--run-state', 'failed', '--claim-status', 'INCONCLUSIVE', '--comparison-verdict', 'not_evaluated',
            '--next-action', 'Review retained evidence')
        self.assertEqual(result.returncode, 0, result.stderr)
        path = case / 'work/target_matrix.json'
        self.assertEqual(load_json(path)['targets'][0]['run_state'], 'failed')
        tampered = load_json(path)
        tampered['targets'] = []
        write_json(path, tampered)
        old_bytes = path.read_bytes()
        with self.assertRaises(ValueError):
            audit_case(case)
        self.assertEqual(path.read_bytes(), old_bytes)
        render_case(case, artifacts=False)
        self.assertTrue(audit_case(case)['ok'])

    def test_status_updates_do_not_overwrite_narrative_and_explicit_render_does(self):
        case = self.init()
        pack = load_json(SKILL / 'templates/deep_reading_pack_template.json')
        pack.update(language='en', note_sections=[{'heading': 'Result', 'content': 'A bounded synthetic observation.'}])
        write_json(case / 'work/deep_reading_pack.json', pack)
        render_case(case)
        note = case / '01_deep_reading/deep_reading_note.md'
        self.assertIn('Full-paper reading notes', note.read_text(encoding='utf-8'))
        note.write_text('A temporary presentation repair.\n', encoding='utf-8')
        before = (note.read_bytes(), note.stat().st_mtime_ns)
        result = run_python('runtime_control.py', 'set-stage', case, '--stage', 'Stage0',
            '--run-state', 'in_progress', '--attempt-count', '1', '--repair-count', '0', '--next-action', 'Inspect')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((note.read_bytes(), note.stat().st_mtime_ns), before)
        pack['note_sections'][0]['content'] = 'Canonical corrected result.'
        write_json(case / 'work/deep_reading_pack.json', pack)
        render_case(case)
        self.assertIn('Canonical corrected result.', note.read_text(encoding='utf-8'))
        stamp = note.stat().st_mtime_ns
        render_case(case)
        self.assertEqual(note.stat().st_mtime_ns, stamp)

    def test_english_markdown_has_no_cjk_quota_even_with_legacy_flag(self):
        (self.root / 'result.md').write_text('# Result\n\nAn exact synthetic result with an explicit limitation.\n', encoding='utf-8')
        self.assertEqual(lint_markdown(self.root, allow_any_root=True, require_chinese=True), [])

    def test_digitization_requires_independent_review_and_a_real_hash_shape(self):
        record = load_json(SKILL / 'templates/digitization_record_template.json')
        digest = hashlib.sha256(b'fixture').hexdigest()
        record.update(source_image_sha256=digest, operator='test-operator', review_status='agent_checked',
                      exported_points=[{'x': 0, 'y': 1}], estimated_uncertainty={'x': .01, 'y': .01},
                      reviewer={'source': 'platform_spawn_result', 'agent_id': 'test-reviewer', 'report_sha256': digest})
        validate_document(record, 'digitization_record')
        record['reviewer']['agent_id'] = record['operator']
        with self.assertRaises(SchemaValidationError):
            validate_document(record, 'digitization_record')
        record['reviewer'].update(agent_id='test-reviewer', report_sha256='not-a-hash')
        with self.assertRaises(SchemaValidationError):
            validate_document(record, 'digitization_record')

    def test_source_fingerprint_is_posix_utf8_sorted_and_location_independent(self):
        entries = {'z.dat': b'z\n', 'A.dat': b'A\n', 'nested/a.dat': b'a\n', '数据.dat': b'utf8\n'}
        expected_inventory = [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                              for name, data in sorted(entries.items(), key=lambda item: item[0].encode('utf-8'))]
        trees = []
        for name in ('first', 'another'):
            root = self.root / name
            for relative, data in reversed(list(entries.items())):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            trees.append(source_fingerprint(root)['tree_hash'])
            self.assertEqual(_inventory(root), expected_inventory)
        self.assertEqual(trees, [sha256_bytes(canonical_json_bytes(expected_inventory))] * 2)

    def test_release_zip_is_repeatable_and_installs_into_a_clean_directory(self):
        results = []
        for name in ('first-release', 'second-release'):
            output = self.root / name
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/package_release.py'), '--output', str(output)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            archive = next(output.glob('*.zip'))
            results.append(archive.read_bytes())
            self.assertEqual((output / 'SHA256SUMS.txt').read_text().split()[0], hashlib.sha256(results[-1]).hexdigest())
        self.assertEqual(*results)
        extracted = self.root / 'extracted'
        with zipfile.ZipFile(archive) as handle:
            self.assertFalse(any('.git/' in name or '__pycache__/' in name for name in handle.namelist()))
            handle.extractall(extracted)
        source = next(extracted.iterdir())
        target = self.root / 'installed/paper-replication-archive'
        for action in ('update', 'check'):
            result = subprocess.run([sys.executable, str(source / 'scripts/install_skill.py'), '--action', action,
                                     '--source', str(source), '--target', str(target)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / 'VERSION').read_text().strip(), '5.0.0')


if __name__ == '__main__':
    unittest.main()
