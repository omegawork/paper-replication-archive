#!/usr/bin/env python3
"""Durable v5 execution worker. Reopening a run never launches it twice."""
from __future__ import annotations

from pathlib import Path
import os
import signal
import subprocess
import sys
import time
import uuid

from runtime_model import atomic_write_json, load_json, now_iso, sha256_file

# Keep handles for intentionally detached children when used as an imported API.
# Subsequent observations reap finished children without waiting on live work.
_detached_workers = []


def _reap_workers():
    _detached_workers[:] = [worker for worker in _detached_workers if worker.poll() is None]


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # access denied is not proof of exit
        try:
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def status(bundle):
    _reap_workers()
    record_path = bundle / 'run_record.json'
    if not record_path.exists():
        return {'run_state': 'interrupted_before_record', 'bundle': str(bundle),
                'next_action': 'Inspect preserved residue; do not overwrite or launch into this directory.'}
    record = load_json(record_path)
    launch_path = bundle / 'launch.json'
    if not record.get('worker_pid') and launch_path.exists():
        record['worker_pid'] = load_json(launch_path).get('worker_pid')
    result = {key: record.get(key) for key in ('run_id', 'run_state', 'started_at', 'finished_at', 'worker_pid', 'child_pid')}
    result['worker_alive'] = process_alive(record.get('worker_pid'))
    result['child_alive'] = process_alive(record.get('child_pid'))
    result['launch_in_progress'] = (bundle / '.launch.lock').exists()
    handle = bundle / 'output' / 'run_handle.json'
    if handle.exists():
        result['external_handle'] = load_json(handle)
    result['next_action'] = 'Read the existing run; do not start a replacement while its completion is unknown.'
    return result


def start(plan_path, approval_path, bundle, *, detach=False):
    _reap_workers()
    from evidence_runtime import load_plan, preview_plan, _load_approval, verify_bundle
    plan_path, approval_path, bundle = plan_path.resolve(), approval_path.resolve(), bundle.resolve()
    plan = load_plan(plan_path)
    if plan['schema_version'] != 5:
        raise ValueError('v4 plans are read-only; verification is supported, execution requires a new v5 case')
    if bundle.exists() and any(bundle.iterdir()):
        saved = bundle / 'plan.json'
        if saved.is_file() and sha256_file(saved) != sha256_file(plan_path):
            raise ValueError('existing run binds different plan bytes; preserve it and use a new run directory')
        if (bundle / 'evidence_index.json').is_file():
            return recover(bundle)
        return status(bundle)
    preview = preview_plan(plan_path)
    if preview['status'] != 'preview_ready':
        raise ValueError('execution blocked by preview: ' + '; '.join(preview['blockers']))
    receipt = _load_approval(approval_path, plan_path, plan, live=True)
    source = Path(plan['source']['path']).resolve()
    if bundle == source or source in bundle.parents or bundle in source.parents:
        raise ValueError('Evidence Bundle must be outside the source tree')
    bundle.mkdir(parents=True, exist_ok=True)
    launch_lock = bundle / '.launch.lock'
    try:
        descriptor = os.open(launch_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return status(bundle)
    run_id = str(uuid.uuid4())
    os.write(descriptor, str(os.getpid()).encode())
    reserved = False
    try:
        atomic_write_json(bundle / 'run_record.json', {
            'schema_version': 5, 'run_id': run_id, 'run_state': 'preparing',
            'plan_sha256': sha256_file(plan_path), 'started_at': now_iso(),
            'worker_pid': None, 'child_pid': None, 'command_template': plan['command']})
        import shutil
        try:
            shutil.copy2(plan_path, bundle / 'plan.json')
            shutil.copy2(approval_path, bundle / 'approval.json')
        except OSError as exc:
            record = load_json(bundle / 'run_record.json')
            record.update(run_state='failed', preparation_failed=True, finished_at=now_iso(),
                          execution_error=str(exc), budget_reserved=False)
            atomic_write_json(bundle / 'run_record.json', record)
            return dict(record, claim_status='INCONCLUSIVE', next_action='Preserve this failure; repair the cause and use a new run directory under the same scope.')
        if receipt.get('origin') == 'inherited_task_scope':
            from scope_authorization import reserve_run
            reserve_run(receipt, plan, bundle, run_id)
            reserved = True
        record = load_json(bundle / 'run_record.json')
        record['budget_reserved'] = reserved
        atomic_write_json(bundle / 'run_record.json', record)
        try:
            worker = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), str(bundle)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError as exc:
            # No computation started; finalize locally without retrying the launch.
            os.close(descriptor)
            descriptor = None
            launch_lock.unlink()
            run_worker(bundle, preparation_error=f'worker launch failed: {exc}')
            return verify_bundle(bundle)
        # Popen succeeded. The worker now owns execution and its reservation.
        # A redundant launch receipt failing to write must never release that
        # reservation or rewrite the live worker's record as a failed launch.
        launch_error = None
        try:
            atomic_write_json(bundle / 'launch.json', {'worker_pid': worker.pid, 'run_id': run_id})
        except OSError as exc:
            launch_error = str(exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        launch_lock.unlink(missing_ok=True)
    if detach:
        _detached_workers.append(worker)
        return {'run_id': run_id, 'worker_pid': worker.pid, 'run_state': 'preparing', 'bundle': str(bundle),
                'launch_metadata_error': launch_error}
    worker.wait()
    if not (bundle / 'evidence_index.json').is_file():
        raise ValueError('worker interrupted; inspect the durable run record and preserve its outputs')
    result = verify_bundle(bundle)
    if result['verification_status'] == 'invalid':
        raise ValueError('created Evidence Bundle failed self-verification: ' + '; '.join(result['integrity_errors']))
    return result


def run_worker(bundle, *, preparation_error=None):
    import evidence_runtime as rt
    from comparison_engine import compare
    plan = rt.load_plan(bundle / 'plan.json')
    receipt = load_json(bundle / 'approval.json')
    run = load_json(bundle / 'run_record.json')
    source = Path(plan['source']['path'])
    snapshot = bundle / 'source_snapshot'
    output = bundle / 'output'
    output.mkdir(exist_ok=True)
    (bundle / 'raw').mkdir(exist_ok=True)
    run.update(worker_pid=os.getpid(), run_state='preparing', returncode=None, timed_out=False,
               execution_error=None, resolved_argv=[], shell=False, environment={},
               limit_enforcement={}, inventory_errors=[])
    atomic_write_json(bundle / 'run_record.json', run)
    started_clock = time.monotonic()
    def inventory(path, *, source_tree=False):
        try:
            return rt._inventory(path, exclude_git=source_tree)
        except (ValueError, OSError) as exc:
            run['inventory_errors'].append(f'{path.name}: {type(exc).__name__}: {exc}')
            run['execution_error'] = 'An inventory could not be collected; see inventory_errors'
            return []
    def inputs():
        result = []
        for item in plan['inputs']:
            try:
                digest = rt.sha256_file(rt._safe_relative(source, item['path'], must_exist=True))
            except (ValueError, OSError) as exc:
                digest = None
                run['inventory_errors'].append(f"input {item['path']}: {exc}")
                run['execution_error'] = 'An input could not be collected; see inventory_errors'
            result.append({'path': item['path'], 'sha256': digest})
        return result
    before = {'original_source': [], 'source_copy': [], 'inputs': []}
    atomic_write_json(bundle / 'inventory_before.json', before)
    child = None
    try:
        if preparation_error:
            raise ValueError(preparation_error)
        rt._load_approval(bundle / 'approval.json', bundle / 'plan.json', plan, live=True)
        preview = rt.preview_plan(bundle / 'plan.json')
        run['limit_enforcement'] = preview['enforcement']
        if preview['status'] != 'preview_ready':
            raise ValueError('worker preflight blocked: ' + '; '.join(preview['blockers']))
        before['original_source'] = inventory(source, source_tree=True)
        before['inputs'] = inputs()
        rt._copy_source(source, snapshot)
        before['source_copy'] = inventory(snapshot, source_tree=True)
        atomic_write_json(bundle / 'inventory_before.json', before)
        if run['inventory_errors'] or rt._inventory_hash(before['source_copy']) != plan['source']['tree_hash']:
            raise ValueError('source snapshot changed after review; do not execute unreviewed bytes')
        for item in plan['inputs']:
            if rt.sha256_file(rt._safe_relative(snapshot, item['path'], must_exist=True)) != item['sha256']:
                raise ValueError('copied input changed after review')
        environment, recorded = rt._execution_environment(plan)
        cwd = rt._safe_relative(snapshot, plan['command']['cwd'], must_exist=True)
        if not cwd.is_dir():
            raise ValueError('command cwd is not a directory inside the source copy')
        argv = rt._resolve_argv(plan['command']['argv'], snapshot, output)
        run.update(resolved_argv=argv, environment=recorded)
        with (bundle / 'raw/stdout.log').open('wb') as stdout, (bundle / 'raw/stderr.log').open('wb') as stderr:
            run['launch_requested'] = True
            atomic_write_json(bundle / 'run_record.json', run)
            child = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, shell=False, start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            run.update(child_pid=child.pid, run_state='running')
            try:
                atomic_write_json(bundle / 'run_record.json', run)
            except OSError as exc:
                # The process is running. Keep ownership and wait for it even
                # when its redundant progress snapshot cannot be written.
                run['progress_record_error'] = str(exc)
            try:
                run['returncode'] = child.wait(timeout=float(plan['budget']['wall_time_seconds']))
            except subprocess.TimeoutExpired:
                run['timed_out'] = True
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.killpg(child.pid, signal.SIGKILL)
                run['returncode'] = child.wait()
    except Exception as exc:
        run['execution_error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if child is not None and child.poll() is None:
            # Do not seal or release accounting for an unobserved live child.
            # If termination cannot be established, raising leaves the run and
            # its reservation recoverable, instead of advertising completion.
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(child.pid, signal.SIGKILL)
            run['returncode'] = child.wait(timeout=10)
        for name in ('stdout.log', 'stderr.log'):
            (bundle / 'raw' / name).touch(exist_ok=True)
        after = {'original_source': inventory(source, source_tree=True),
                 'source_copy': inventory(snapshot, source_tree=True) if snapshot.is_dir() else [],
                 'inputs': inputs()}
        atomic_write_json(bundle / 'inventory_after.json', after)
        items = inventory(output)
        declared = {item['path'] for item in plan['outputs']}
        actual = {item['path'] for item in items}
        run.update(finished_at=now_iso(), output_inventory=items,
                   undeclared_outputs=sorted(actual - declared), missing_outputs=sorted(declared - actual),
                   resource_violations=[], elapsed_seconds=time.monotonic() - started_clock)
        # Account for persisted logs and source snapshot as well as outputs.
        # Small metadata files are included too; disk enforcement is post-run.
        size = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()) / (1024 * 1024)
        run['disk_megabytes'] = size
        if size > plan['budget']['disk_mb']:
            run['resource_violations'].append('disk_mb')
        if len(items) > plan['budget']['output_count']:
            run['resource_violations'].append('output_count')
        if any(item['size'] > plan['budget']['single_file_mb'] * 1024 * 1024 for item in items):
            run['resource_violations'].append('single_file_mb')
        observation, comparison = rt.run_observation(plan, bundle, run)
        run['run_state'] = 'failed' if rt.execution_failed(run) or observation.get('unavailable') else 'completed'
        if receipt.get('origin') == 'inherited_task_scope':
            from scope_authorization import finish_run
            try:
                finish_run(receipt, run['run_id'], run['elapsed_seconds'], size,
                           scientific_started=run.get('child_pid') is not None, outcome=run['run_state'])
            except (ValueError, OSError) as exc:
                run['accounting_error'] = str(exc)
        atomic_write_json(bundle / 'run_record.json', run)
        atomic_write_json(bundle / 'observation.json', observation)
        atomic_write_json(bundle / 'comparison.json', comparison)
        # Parent releases this short-lived lock before the worker can be sealed.
        for _ in range(100):
            if not (bundle / '.launch.lock').exists():
                break
            time.sleep(0.01)
        rt.seal_bundle(bundle)


def recover(bundle, *, completion_evidence=None):
    """Seal an interrupted run negatively, never rerun or infer successful exit."""
    import evidence_runtime as rt
    bundle = bundle.resolve()
    if (bundle / 'evidence_index.json').is_file():
        result = rt.verify_bundle(bundle)
        if result['verification_status'] == 'invalid':
            return result
        run = load_json(bundle / 'run_record.json')
        receipt = load_json(bundle / 'approval.json')
        if receipt.get('origin') == 'inherited_task_scope':
            from scope_authorization import finish_run, load_ledger
            entry = load_ledger(Path(receipt['scope_path']))['runs'].get(run['run_id'])
            if not entry or entry['plan_sha256'] != result['plan_sha256'] or Path(entry['bundle']).resolve() != bundle:
                raise ValueError('sealed run does not match its accounting entry')
            if entry['status'] == 'reserved':
                measured_disk = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()) / (1024 * 1024)
                finish_run(receipt, run['run_id'], run.get('elapsed_seconds', entry['wall_time_seconds']),
                           max(run.get('disk_megabytes', entry['disk_mb']), measured_disk),
                           scientific_started=run.get('child_pid') is not None or run.get('recovery', False), outcome=run['run_state'])
                result['accounting_recovered'] = True
        return result
    observed = status(bundle)
    lock = bundle / '.launch.lock'
    if lock.exists():
        owner = int(lock.read_text().strip())
        if process_alive(owner):
            raise ValueError('launch owner is still alive; observe the existing run')
        lock.unlink()
    if observed.get('worker_alive') or observed.get('child_alive'):
        raise ValueError('existing process is still alive; recovery cannot start a replacement')
    run = load_json(bundle / 'run_record.json')
    if observed.get('external_handle') or (run.get('launch_requested') and not run.get('child_pid')):
        if completion_evidence is None:
            raise ValueError('completion is uncertain; inspect the existing external/launch handle and supply terminal evidence')
        evidence = load_json(completion_evidence)
        if (evidence.get('run_id') != run['run_id'] or evidence.get('terminal') is not True
                or not evidence.get('source_ref') or not evidence.get('observation')):
            raise ValueError('terminal evidence must bind this run and an actual process/scheduler observation')
        atomic_write_json(bundle / 'recovery_observation.json', evidence)
    plan = rt.load_plan(bundle / 'plan.json')
    receipt = rt._load_approval(bundle / 'approval.json', bundle / 'plan.json', plan)
    output = bundle / 'output'
    output.mkdir(exist_ok=True)
    (bundle / 'raw').mkdir(exist_ok=True)
    for name in ('stdout.log', 'stderr.log'):
        (bundle / 'raw' / name).touch(exist_ok=True)
    before_path = bundle / 'inventory_before.json'
    if not before_path.exists():
        atomic_write_json(before_path, {'original_source': [], 'source_copy': [], 'inputs': []})
    # Recovery does not recreate lost source or input evidence.
    if not (bundle / 'inventory_after.json').exists():
        atomic_write_json(bundle / 'inventory_after.json', {'original_source': [], 'source_copy': [], 'inputs': []})
    run.update(run_state='failed', finished_at=now_iso(), returncode=None, timed_out=False,
               execution_error='Interrupted worker; successful exit was not established. No computation was restarted.',
               recovery=True, output_inventory=rt._inventory(output), resource_violations=[],
               undeclared_outputs=[], missing_outputs=[])
    observation, comparison = rt.run_observation(plan, bundle, run)
    if receipt.get('origin') == 'inherited_task_scope':
        from scope_authorization import finish_run, load_ledger
        entry = load_ledger(Path(receipt['scope_path']))['runs'][run['run_id']]
        measured_disk = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()) / (1024 * 1024)
        finish_run(receipt, run['run_id'], entry['wall_time_seconds'], max(entry['disk_mb'], measured_disk),
                   scientific_started=entry.get('scientific', True), outcome='failed')
    atomic_write_json(bundle / 'run_record.json', run)
    atomic_write_json(bundle / 'observation.json', observation)
    atomic_write_json(bundle / 'comparison.json', comparison)
    return rt.seal_bundle(bundle)


if __name__ == '__main__':
    run_worker(Path(sys.argv[1]).resolve())
