#!/usr/bin/env python3
"""Optional Slurm reference adapter; stop at scientific decision boundaries.

Uses the original separate Slurm submissions and original scientific records.
No orbital, window, exclusion, acceptance or solver setting is inferred here.
"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

from workflow_runtime import atomic_json, execute, now, parse_job_id, sha, summarize, TOOLS, file_lock
import material_layout as layout


def read(path):
    return json.loads(Path(path).read_text())


def location(case, phase, trial_id='trial_001'):
    case = Path(case).resolve()
    if not case.is_dir() or phase not in ('evidence', 'trial'):
        raise ValueError('Require an existing material directory and evidence/trial phase.')
    if not re.fullmatch(r'trial_[0-9]{3,}', trial_id):
        raise ValueError('trial_id must be one canonical trial basename.')
    path = case / '.wannier_runtime' / 'runner' / (phase if phase == 'evidence' else trial_id)
    if any(p.is_symlink() for p in (path, *list(path.parents)[:3])):
        raise ValueError('Runtime directories must not be symlinks.')
    return case, path


def configure(case, site, phase, trial_id='trial_001', postw90=None, shared_source=None,
              mp_grid=None, interface_source=None, ntasks=1):
    case, path = location(case, phase, trial_id)
    if shared_source and mp_grid is not None:
        raise ValueError('A sealed shared source supplies the complete KPOINTS; omit mp_grid rather than overriding its grid.')
    if type(ntasks) is not int or ntasks < 1:
        raise ValueError('Wannier/comparison tasks must be positive.')
    if mp_grid and (len(mp_grid) != 3 or any(type(n) is not int or n <= 0 for n in mp_grid)):
        raise ValueError('mp_grid requires three positive integers; original interface validation still applies.')
    site = Path(site).resolve()
    if not site.is_file():
        raise ValueError('Missing site profile.')
    if read(site).get('scheduler') != 'slurm':
        raise ValueError('This optional reference runner supports Slurm only; portable scientific tools do not require it.')
    if phase == 'trial' and (not postw90 or not Path(postw90).is_file()):
        raise ValueError('A trial runner requires the explicit existing postw90 executable before any submission.')
    cfg = {'format': 'wannier-case-runner-v1', 'case': str(case), 'site': str(site),
           'site_sha256': sha(site), 'phase': phase, 'trial_id': trial_id,
           'postw90': str(Path(postw90).resolve()) if postw90 else None,
           'shared_source': str(Path(shared_source).resolve()) if shared_source else None,
           'mp_grid': mp_grid, 'interface_source': str(Path(interface_source).resolve()) if interface_source else None,
           'ntasks': ntasks, 'runner_sha256': sha(__file__),
           'tool_hashes': {p.name: sha(p) for p in sorted(TOOLS.glob('*.py'))},
           'scientific_selection': 'none; an original sealed selected stage is required'}
    target = path / 'config.json'
    if target.exists():
        if read(target) != cfg:
            raise ValueError('Runner config is immutable; use a new scientific trial or case, not an in-place settings change.')
    else:
        atomic_json(target, cfg)
        atomic_json(path / 'state.json', {'steps': {}, 'submissions': {}, 'terminal': None})
    if not (path / 'state.json').is_file():
        raise ValueError('Runner state is missing; reconcile the incomplete configuration before execution.')
    return {'status': 'runner_configured_not_submitted', 'config': str(target), 'config_sha256': sha(target)}


class Runner:
    def __init__(self, case, phase='trial', trial_id='trial_001', execute_jobs=False):
        self.case, self.path = location(case, phase, trial_id)
        self.cfg = read(self.path / 'config.json')
        if sha(self.cfg['site']) != self.cfg['site_sha256'] or sha(__file__) != self.cfg['runner_sha256']:
            raise ValueError('Configured site or runner changed; reconcile the recorded execution before changing versions.')
        if any(not (TOOLS / name).is_file() or sha(TOOLS / name) != digest
               for name, digest in self.cfg['tool_hashes'].items()):
            raise ValueError('Bundled tools changed after runner configuration.')
        self.state = read(self.path / 'state.json')
        self.execute_jobs = execute_jobs
        self.phase, self.trial_id = phase, trial_id
        self.trial = self.case / '03_wan_trial' / trial_id
        self.site = self.cfg['site']

    def save(self):
        atomic_json(self.path / 'state.json', self.state)

    def status(self, status, next_action, **extra):
        value = {'format': 'wannier-stage-status-v1', 'status': status, 'phase': self.phase,
                 'trial_id': self.trial_id if self.phase == 'trial' else None,
                 'next_action': next_action, 'updated_utc': now(),
                 'scientific_acceptance': 'not_assigned_by_runner', **extra}
        atomic_json(self.path / 'stage_status.json', value)
        return value

    def command(self, key, tool, arguments):
        existing = self.state['steps'].get(key)
        if existing:
            if existing['status'] == 'complete':
                return existing['parsed']
            if existing['status'] == 'started':
                # Recover a lost controller response only from its completed
                # durable CLI log. Never rerun an uncertain mutation.
                matches = []
                for log in (self.case / '.wannier_runtime/logs').glob(tool[:-3] + '-*.json'):
                    record = read(log)
                    if (record.get('started_utc', '') >= existing['started_utc'] and
                            record.get('argv', [])[3:] == list(map(str, arguments))):
                        matches.append((log, record))
                if len(matches) == 1 and matches[0][1]['exit_code'] == 0:
                    log, record = matches[0]
                    parsed = json.loads(record['stdout'])
                    existing.update(status='complete', parsed=parsed, log=str(log), recovered_from_log=True)
                    self.save()
                    return parsed
            raise ValueError('Previous operation did not commit completion: ' + key + '; inspect its log; do not blindly retry.')
        self.state['steps'][key] = {'status': 'started', 'started_utc': now(), 'tool': tool, 'arguments': arguments}
        self.save()
        result, parsed = execute(tool, arguments, self.case, timeout=600)
        self.state['steps'][key].update(status='complete' if result['ok'] else 'failed',
                                       log=result['log'], parsed=parsed, envelope=result)
        self.save()
        if not result['ok']:
            self.state['terminal'] = self.status('operation_failed', 'inspect the operation log; preserve this attempt', operation=key, details=result)
            self.save()
            raise ValueError('Official CLI operation rejected or failed: ' + key)
        return parsed

    def verify(self, tool, arguments):
        # Evidence authentication must run again on resume, even when a prior
        # preparation/submission result is cached.
        result, parsed = execute(tool, arguments, self.case)
        if not result['ok']:
            raise ValueError('Current evidence verification failed; inspect ' + result['log'])
        return parsed

    def submit(self, key, tool, arguments):
        old = self.state['submissions'].get(key)
        if old:
            if old.get('job_id'):
                return old['job_id']
            return None  # durable intent without known completion; never resubmit
        if not self.execute_jobs:
            return None
        self.state['submissions'][key] = {'status': 'submission_intent', 'started_utc': now()}
        self.save()  # must survive a lost response or controller death
        result, parsed = execute(tool, [*arguments, '--execute'], self.case)
        entry = self.state['submissions'][key]
        entry.update(log=result['log'], response=result)
        try:
            if not result['ok'] or not isinstance(parsed, dict) or not parsed.get('submitted'):
                raise ValueError('No authenticated successful submission response.')
            entry.update(status='submitted', job_id=parse_job_id(parsed.get('job_id')))
        except ValueError:
            entry['status'] = 'submission_completion_unknown'
        self.save()
        return entry.get('job_id')

    def wait_for(self, key, expected):
        submission = self.state['submissions'].get(key)
        if not submission:
            return self.status('execution_authorization_required', 'use advance/start with --execute after reviewing the configured site', stage=key)
        job = submission.get('job_id')
        if not job:
            return self.status('submission_completion_unknown', 'reconcile the original submission; adopt a verified job record; do not resubmit', stage=key,
                               submission_log=submission.get('log'))
        if all(p.is_file() for p in expected):
            return None
        try:
            process = subprocess.run(['squeue', '-h', '-j', job, '-o', '%i|%T'],
                                     capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return self.status('monitoring_unknown', 'continue monitoring this existing job; do not submit another', stage=key, job_id=job)
        if process.returncode or process.stdout.strip():
            return self.status('waiting_for_job' if not process.returncode else 'monitoring_unknown',
                               'controller waits without model calls', stage=key, job_id=job,
                               scheduler_state=process.stdout.strip()[:120])
        # Disappearing from squeue does not prove successful computation. Give
        # shared filesystem metadata a short grace period; then ask for review.
        observed = submission.setdefault('first_missing_queue_epoch', time.time())
        self.save()
        if time.time() - observed < 60:
            return self.status('waiting_for_job', 'await official completion record; queue absence is not success', stage=key, job_id=job)
        return self.status('job_outcome_unknown', 'inspect Slurm logs or scheduler accounting; missing official record is not a passed stage', stage=key, job_id=job)

    def evidence(self):
        cache = self.case / '02_band' / 'charge.npz'
        fit_path = self.case / '02_band' / 'fit_path.json'
        if not cache.is_file() or not fit_path.is_file():
            if not (self.case / 'case.json').is_file():
                return self.status('needs_dft_materials', 'prepare a fresh DFT case or collect verified reused bands with the original tools')
            expected = [self.case / '01_scf/run.json', self.case / '02_band/run.json']
            if not all(p.is_file() for p in expected):
                manifest = read(self.case / 'case.json')
                stages = ['band'] if manifest.get('reuse_scf') or expected[0].is_file() else ['scf', 'band']
                self.submit('dft', 'dft_workflow.py', ['submit', '--case', str(self.case), '--site', self.site, '--stages', *stages])
                pending = self.wait_for('dft', expected)
                if pending:
                    return pending
            report = self.verify('dft_workflow.py', ['inspect', '--case', str(self.case), '--stage', 'band'])
            if not report['usable_outputs']:
                return self.status('dft_failed', 'review original DFT run records; do not continue to selection')
            self.command('collect_band', 'dft_workflow.py', ['collect', '--case', str(self.case), '--stage', 'band'])
        identity = sha(cache)
        if self.state.get('evidence_cache_sha256', identity) != identity:
            raise ValueError('Projected-band cache changed after evidence preparation; create a new evidence run.')
        self.state['evidence_cache_sha256'] = identity
        self.save()
        packet = self.path / 'review_packet'
        inspection = self.path / 'inspection.json'
        self.command('review_packet', 'review_packet.py', ['--cache', str(cache), '--out-dir', str(packet)])
        self.command('inspect_manifold', 'manifold_selection.py', ['inspect', '--cache', str(cache), '--out', str(inspection)])
        return self.status('scientific_decision_required', 'review projected bands; explicitly choose basis/target/reference/windows; seal initial and real_first_try',
                           artifacts={'cache': str(cache), 'fit_path': str(fit_path), 'inspection': str(inspection), 'packet': str(packet)},
                           operation_log_directory=str(self.case / '.wannier_runtime/logs'))

    def execute_trial(self):
        selected = self.trial / 'records' / 'selection'
        if not (selected / 'STAGE.json').is_file():
            return self.status('scientific_decision_required', 'seal real_first_try/retry with original decision gates; initial is never executed')
        stage = self.verify('decision_stages.py', ['verify', '--directory', str(selected)])
        if stage['role'] not in ('real_first_try', 'retry'):
            return self.status('scientific_decision_required', 'select an executable first try or retry')
        if self.state.get('selected_stage_id', stage['stage_id']) != stage['stage_id']:
            raise ValueError('Selected stage changed after this trial runner began.')
        self.state['selected_stage_id'] = stage['stage_id']
        self.save()
        # Hash the decision identity on every resume, not merely its existence.
        if read(selected / 'STAGE.json')['stage_id'] != stage['stage_id']:
            raise ValueError('Selected stage changed after runner verification.')
        cache = self.case / '02_band/charge.npz'
        analysis, decision = selected / 'analysis.json', selected / 'decision.json'
        source = self.cfg['interface_source']
        interface = Path(source) if source else self.trial / 'interface'
        if not source:
            if self.cfg['shared_source']:
                # Route to the seal's bound inputs; scientific prepare still
                # verifies the seal, its source evidence and the band-cache hashes.
                seal = read(Path(self.cfg['shared_source']) / 'SOURCE_SEAL.json')
                scf_dir, band_dir = seal['scf_directory'], seal['band_directory']
            else:
                paths = layout.case_paths(layout.load_case(self.case))
                scf_dir, band_dir = paths['scf_dir'], paths['band_dir']
            args = ['prepare', '--band-dir', band_dir, '--scf-dir', scf_dir,
                    '--cache', str(cache), '--analysis', str(analysis), '--decision', str(decision), '--out-dir', str(interface)]
            if self.cfg['shared_source']:
                args += ['--shared-source', self.cfg['shared_source']]
            if self.cfg['mp_grid']:
                args += ['--mp-grid', *map(str, self.cfg['mp_grid'])]
            self.command('prepare_interface', 'prepare_wannier_interface.py', args)
            if not (interface / 'interface_run.json').is_file():
                self.submit('interface', 'dft_workflow.py', ['submit-interface', '--trial', str(interface), '--site', self.site])
                pending = self.wait_for('interface', [interface / 'interface_run.json'])
                if pending:
                    return pending
        if not source:
            checked = self.verify('prepare_wannier_interface.py', ['validate', '--trial-dir', str(interface), '--outputs'])
            if not checked.get('ready_for_wannier'):
                return self.status('scientific_decision_required', 'review original mesh/data-sufficiency gate; retain physically chosen windows', gate=summarize(checked))
        # Window-only reuse is authenticated by original Wannier prepare, which
        # allows a failed OLD window while checking these NEW selected windows.
        wan = self.trial / 'wannier'
        args = ['prepare', '--interface-dir', str(interface), '--out-dir', str(wan)]
        if source:
            args += ['--cache', str(cache), '--analysis', str(analysis), '--decision', str(decision)]
        self.command('prepare_wannier', 'wannier_workflow.py', args)
        if not (wan / 'wannier_run.json').is_file():
            self.submit('wannier', 'wannier_workflow.py', ['submit', '--directory', str(wan), '--site', self.site, '--ntasks', str(self.cfg['ntasks'])])
            pending = self.wait_for('wannier', [wan / 'wannier_run.json'])
            if pending:
                return pending
        inspected = self.verify('wannier_workflow.py', ['inspect', '--directory', str(wan)])
        if not inspected.get('ready_for_fit'):
            return self.status('wannier_execution_failed', 'review convergence and authenticated products; record failure separately', solver=summarize(inspected))
        if not self.cfg['postw90']:
            return self.status('configuration_required', 'provide an explicit postw90 executable before configuring this trial runner')
        comparison = self.trial / 'comparison'
        if not (comparison / 'validation_report.json').is_file():
            self.submit('comparison', 'compare_wannier.py', ['submit', '--case-dir', str(self.case), '--trial-id', self.trial_id,
                        '--site', self.site, '--postw90', self.cfg['postw90'], '--ntasks', str(self.cfg['ntasks'])])
            pending = self.wait_for('comparison', [comparison / 'validation_report.json'])
            if pending:
                return pending
        report = read(comparison / 'validation_report.json')
        diagnostics = comparison / 'diagnostics.json'
        if not diagnostics.is_file() or report.get('error'):
            return self.status('comparison_failed', 'record this failure and inspect the original comparison logs')
        products = {str(p): sha(p) for p in (comparison / 'validation_report.json', diagnostics)}
        if self.state.get('comparison_products', products) != products:
            raise ValueError('Comparison products changed since the previous completed review handoff.')
        self.state['comparison_products'] = products
        self.save()
        return self.status('fit_review_required', 'review fit, declared target coverage and unchanged scoring scope; assess acceptance or seal a justified retry',
                           solver=summarize(inspected),
                           artifacts={'diagnostics': str(diagnostics), 'diagnostics_sha256': sha(diagnostics),
                                      'fit_figure': str(comparison / 'fit_review.png'),
                                      'selection_figure': str(selected / 'selection.png')})

    def advance(self):
        lock = self.path / 'advance.lock'
        with lock.open('x') as f:
            f.write(json.dumps({'pid': os.getpid(), 'host': socket.gethostname(), 'started_utc': now()}))
        try:
            self.state = read(self.path / 'state.json')  # refresh after acquiring the lock
            if self.state.get('terminal'):
                return self.state['terminal']
            return self.evidence() if self.phase == 'evidence' else self.execute_trial()
        except (ValueError, KeyError, OSError) as error:
            return self.status('operation_requires_review', str(error), automatic_retry=False)
        finally:
            lock.unlink()


def advance(case, phase, trial_id, execute_jobs=False, wait_seconds=0):
    deadline = time.monotonic() + wait_seconds
    while True:
        result = Runner(case, phase, trial_id, execute_jobs).advance()
        if result['status'] not in ('waiting_for_job', 'monitoring_unknown') or time.monotonic() >= deadline:
            return result
        time.sleep(min(15, max(0.1, deadline - time.monotonic())))


def start(case, phase, trial_id, execute_jobs=False, wait_seconds=14400):
    Runner(case, phase, trial_id, execute_jobs)  # validate immutable config before spawning
    _, path = location(case, phase, trial_id)
    lock = path / 'start.lock'
    with lock.open('x') as stream:
        stream.write(json.dumps({'pid': os.getpid(), 'host': socket.gethostname()}))
    try:
        return start_locked(case, phase, trial_id, execute_jobs, wait_seconds)
    finally:
        lock.unlink()


def start_locked(case, phase, trial_id, execute_jobs=False, wait_seconds=14400):
    case, path = location(case, phase, trial_id)
    info = path / 'controller.json'
    if info.is_file():
        old = read(info)
        if old.get('host') == socket.gethostname():
            try:
                os.kill(old['pid'], 0)
                return {'status': 'controller_already_running', 'controller': old, 'status_file': str(path / 'stage_status.json')}
            except ProcessLookupError:
                pass
    command = [sys.executable, '-B', str(Path(__file__).resolve()), 'advance', '--case-dir', str(case),
               '--phase', phase, '--trial-id', trial_id, '--wait-seconds', str(wait_seconds)]
    if execute_jobs:
        command += ['--execute']
    log = path / ('controller-' + uuid.uuid4().hex + '.log')
    with log.open('x') as stream:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
    value = {'pid': process.pid, 'host': socket.gethostname(), 'command': command, 'log': str(log), 'started_utc': now()}
    atomic_json(info, value)
    return {'status': 'controller_started', 'controller': value, 'status_file': str(path / 'stage_status.json'),
            'next_action': 'wait for controller completion; it does not call a model or decide science'}


def adopt(case, phase, trial_id, stage, job_id, submission_record):
    _, path = location(case, phase, trial_id)
    with file_lock(path / 'advance.lock'):
        return adopt_locked(case, phase, trial_id, stage, job_id, submission_record)


def adopt_locked(case, phase, trial_id, stage, job_id, submission_record):
    _, path = location(case, phase, trial_id)
    record = read(submission_record)
    parsed = parse_job_id(record.get('stdout', ''))
    if parsed != parse_job_id(job_id) or record.get('returncode') != 0:
        raise ValueError('Adoption requires an actual successful original sbatch response record.')
    source = Path(submission_record).resolve()
    source.relative_to(Path(case).resolve())
    trial = Path(case).resolve() / '03_wan_trial' / trial_id
    expected = {'dft': Path(case).resolve() / 'submissions',
                'interface': trial / 'interface/submissions',
                'wannier': trial / 'wannier/submissions',
                'comparison': trial / 'records/comparison_submissions'}
    if stage not in expected or source.parent != expected[stage]:
        raise ValueError('Submission record must belong to this exact stage and trial.')
    script = source.with_suffix('.slurm')
    if not script.is_file() or sha(script) != record.get('script_sha256'):
        raise ValueError('Submission script missing or changed.')
    cfg = read(path / 'config.json')
    if stage != 'comparison' and record.get('site_sha256') != cfg['site_sha256']:
        raise ValueError('Submission profile differs from the configured site.')
    state = read(path / 'state.json')
    old = state['submissions'].get(stage)
    if not old or old.get('job_id') or old['status'] not in ('submission_intent', 'submission_completion_unknown'):
        raise ValueError('Only an uncertain existing submission intent can adopt a job.')
    old.update(status='submitted', job_id=parsed, adopted_record={'path': str(source), 'sha256': sha(source)})
    atomic_json(path / 'state.json', state)
    return {'status': 'existing_submission_reconciled', 'job_id': parsed, 'new_submission': False}


def recover_lock(case, phase, trial_id, name):
    if name not in ('start', 'advance'):
        raise ValueError('Only a named controller lock can be recovered.')
    _, path = location(case, phase, trial_id)
    lock = path / (name + '.lock')
    record = read(lock)
    if record['host'] != socket.gethostname():
        raise ValueError('Recover on the recorded controller host.')
    try:
        os.kill(record['pid'], 0)
    except ProcessLookupError:
        atomic_json(path / ('recovered-lock-' + uuid.uuid4().hex + '.json'), record)
        lock.unlink()
        return {'status': 'dead_controller_lock_recovered', 'new_submission': False}
    raise ValueError('Recorded controller process is still alive; do not remove its lock.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['configure', 'advance', 'start', 'status', 'adopt-job', 'recover-lock'])
    parser.add_argument('--case-dir', required=True)
    parser.add_argument('--phase', choices=['evidence', 'trial'], default='trial')
    parser.add_argument('--trial-id', default='trial_001')
    for name in ('site', 'postw90', 'shared-source', 'interface-source', 'stage', 'job-id', 'submission-record'):
        parser.add_argument('--' + name)
    parser.add_argument('--mp-grid', nargs=3, type=int)
    parser.add_argument('--ntasks', type=int, default=1)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--wait-seconds', type=float, default=0)
    args = parser.parse_args()
    try:
        if args.command == 'configure':
            result = configure(args.case_dir, args.site, args.phase, args.trial_id, args.postw90,
                               args.shared_source, args.mp_grid, args.interface_source, args.ntasks)
        elif args.command == 'status':
            _, path = location(args.case_dir, args.phase, args.trial_id)
            result = read(path / 'stage_status.json') if (path / 'stage_status.json').is_file() else {'status': 'configured_not_advanced'}
        elif args.command == 'adopt-job':
            result = adopt(args.case_dir, args.phase, args.trial_id, args.stage, args.job_id, args.submission_record)
        elif args.command == 'recover-lock':
            result = recover_lock(args.case_dir, args.phase, args.trial_id, args.stage)
        elif args.command == 'start':
            result = start(args.case_dir, args.phase, args.trial_id, args.execute,
                           args.wait_seconds if args.wait_seconds > 0 else 14400)
        else:
            if args.wait_seconds < 0:
                raise ValueError('wait-seconds must be nonnegative.')
            result = advance(args.case_dir, args.phase, args.trial_id, args.execute, args.wait_seconds)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, 'Runner error: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
