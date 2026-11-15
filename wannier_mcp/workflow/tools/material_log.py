#!/usr/bin/env python3
"""Render a fixed-format per-material log from existing workflow records."""
import argparse
import fcntl
import sys
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap

HEADER = 'WANNIERCOMPASS MATERIAL LOG v1'


def read(path):
    if not path.is_file():
        return {}
    with path.open() as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('Expected JSON object: '+str(path))
    return value


def text(value):
    if value is None:
        return 'unknown'
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return format(value,'.6g')
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return ' '.join(str(value).split())


def short(value, limit=160):
    value=text(value)
    return value if len(value)<=limit else value[:limit-3].rsplit(' ',1)[0]+'...'


WIDTH = 104
KEY_WIDTH = 23


def final_spreads(path):
    if not path.is_file():
        return []
    raw = path.read_text(errors='replace')
    content = raw.rsplit('Final State', 1)[-1] if 'Final State' in raw else ''
    return [float(m[1]) for m in re.finditer(
        r'WF centre and spread[^\n]*\)\s*([-+\d.Ee]+)', content)]


def render(case):
    case = Path(case).resolve()
    layout = read(case/'MATERIAL-LAYOUT.json')
    workspace = Path(layout.get('case_dir', str(case)))
    trials = sorted(p for p in (case/'03_wan_trial').glob('trial_*')
                    if p.is_dir() and re.fullmatch(r'trial_\d{3,}', p.name))
    lines = [HEADER, '=' * WIDTH]
    levels = {'WARNING': 0, 'ERROR': 0}

    def section(title):
        lines.extend(['', ('-- ' + title + ' ').ljust(WIDTH, '-')])

    def group(title):
        lines.extend(['', '  [' + title + ']'])

    def field(key, value):
        prefix = '  ' + key.ljust(KEY_WIDTH) + ' : '
        if isinstance(value, str) and value.startswith('/'):
            lines.append(prefix + value)
            return
        lines.extend(textwrap.wrap(text(value), width=WIDTH, initial_indent=prefix,
                     subsequent_indent=' ' * len(prefix), break_long_words=True,
                     break_on_hyphens=False) or [prefix + 'unknown'])

    def event(level, code, detail):
        if level in levels:
            levels[level] += 1
        prefix = '[' + level + '] ' + code + ' | '
        lines.extend(textwrap.wrap(text(detail), width=WIDTH, initial_indent=prefix,
                     subsequent_indent=' ' * min(len(prefix), 12), break_long_words=True,
                     break_on_hyphens=False))

    def location(trial, suffix):
        return Path('03_wan_trial')/trial.name/suffix

    field('UPDATED_UTC', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))
    field('MATERIAL_ID', layout.get('material_id', case.name))
    poscars = [case/'01_scf/source/POSCAR', case/'01_scf/POSCAR']
    material = layout.get('material_name') or next((p.read_text().splitlines()[0][:120]
        for p in poscars if p.is_file() and p.stat().st_size), None)
    field('MATERIAL', material)
    summary_at = len(lines)
    lines.append('')
    section('LOCATIONS')
    field('WORKSPACE', str(workspace))
    field('SCF_DIR', layout.get('scf_source', str(workspace/'01_scf')))
    field('BAND_DIR', layout.get('band_source', str(workspace/'02_band')))
    if layout.get('regression_source'):
        field('MATRIX_SOURCE', layout['regression_source'])
    field('RELATIVE_PATHS', 'Resolved from WORKSPACE')
    section('TRIAL OVERVIEW')
    lines.append(f'  {"TRIAL":<12} {"ROLE":<22} {"WANNIER STATUS":<25} {"MAX SPREAD (A^2)":>16}  FIT PASS')
    lines.append('  ' + '-' * (WIDTH-2))
    for trial in trials:
        stage = read(trial/'records/selection/STAGE.json')
        run = read(trial/'wannier/wannier_run.json')
        fit = read(trial/'comparison/fit_review.json')
        spreads = final_spreads(trial/'wannier/wannier90.wout')
        role = text(stage.get('role', 'unselected'))
        status = text(run.get('status', 'not executed'))
        restarts = sorted((trial/'technical').glob('restart_*/RESTART.json'))
        if restarts:
            latest = read(restarts[-1])
            failed_parent = 'failed' in status or 'timed_out' in status
            active_restart = latest.get('status') in ('running', 'completed_converged', 'execution_failed')
            if failed_parent and active_restart:
                status = latest['status'] + ' [restart]'
        maximum = text(max(spreads) if spreads else None)
        if restarts and failed_parent and active_restart and latest.get('spread_max_a2') is not None:
            maximum = text(latest['spread_max_a2'])
        # Complete role/status values remain in the per-trial fields below.
        lines.append(f'  {trial.name:<12} {role[:22]:<22} {status[:25]:<25} {maximum:>16}  {text(fit.get("fit_pass"))}')
    if not trials:
        event('INFO', 'NO_TRIALS', 'No trial recorded yet')

    for label, folder in [('SCF', '01_scf'), ('BAND', '02_band')]:
        root = case/folder
        if (root/'source').is_dir():
            root = root/'source'
        record = read(root/'run.json')
        if record:
            section(label + ' EXECUTION')
            status = record.get('status', 'recorded')
            bad = record.get('returncode') not in (None, 0) or record.get('usable_outputs') is False
            event('ERROR' if bad else 'INFO', label+'_STATUS', status)
            field('JOB_ID', record.get('slurm_job_id'))
            field('RETURN_CODE', record.get('returncode'))
            field('FINISHED_UTC', record.get('finished_utc'))
            field('RUN_RECORD', str(root/'run.json'))
            for problem in record.get('problems', []):
                event('ERROR', label+'_PROBLEM', problem)
    if read(case/'03_wan_trial/initial/STAGE.json'):
        event('INFO', 'INITIAL_RECORDED', 'not executed')

    previous = None
    for number, trial in enumerate(trials, 1):
        section(f'TRIAL {number:02d} / {len(trials):02d} : {trial.name}')
        event('INFO', 'TRIAL', trial.name)
        stage = read(trial/'records/selection/STAGE.json')
        decision = read(trial/'records/selection/decision.json')
        review = read(trial/'records/selection/review.json')
        choices = stage.get('choices') or {}
        basis = choices.get('basis', decision.get('trial_basis'))
        windows = decision.get('windows') or {}
        dis = choices.get('dis_eV', windows.get('dis_eV'))
        frozen = choices.get('frozen_eV', windows.get('frozen_eV'))
        field('ROLE', stage.get('role', 'unselected'))
        field('DIRECTORY', str(location(trial, '')))
        group('INPUT SETTINGS')
        field('BASIS', ', '.join(basis) if isinstance(basis, list) else basis)
        field('DIS_EV_REL_EF', dis)
        field('FROZEN_EV_REL_EF', frozen)
        prepared = read(trial/'wannier/wannier_manifest.json') or read(trial/'interface/trial_manifest.json')
        controls = prepared.get('controls', prepared.get('win_scalars', {}))
        guide = controls.get('guiding_centres', prepared.get('source_win_scalars', {}).get('guiding_centres', 'false')) if prepared else None
        current = dict(basis=basis, dis_eV=dis, frozen_eV=frozen,
                       guiding_centres=guide, num_iter=controls.get('num_iter'))
        if previous is not None:
            changed = [k for k, v in current.items() if v != previous[k]]
            field('CHANGES', ', '.join(changed) if changed else 'none')
            for key in changed:
                field('  '+key.upper(), text(previous[key]) + ' -> ' + text(current[key]))
        previous = current
        reason = stage.get('reason')
        if reason:
            field('COMMENT', short(reason, 120))
        for label, key in [('TARGET_REASON', 'target_reason'), ('WINDOW_REASON', 'frozen_reason')]:
            if review.get(key):
                field(label, short(review[key], 120))
        field('DECISION_RECORD', str(location(trial, 'records/selection/STAGE.json')))

        for folder, filename in [('interface', 'trial_manifest.json'), ('wannier', 'wannier_manifest.json')]:
            manifest = read(trial/folder/filename)
            record = read(trial/folder/('interface_run.json' if folder=='interface' else 'wannier_run.json'))
            if not manifest and not record:
                continue
            group(folder.upper())
            field('DIRECTORY', str(location(trial, folder)))
            if manifest:
                event('INFO', folder.upper()+'_PREPARED', manifest.get('status', 'prepared'))
                controls = manifest.get('controls', manifest.get('win_scalars', {}))
                field('NUM_ITER', controls.get('num_iter'))
                guide = controls.get('guiding_centres', manifest.get('source_win_scalars', {}).get('guiding_centres', 'false'))
                field('GUIDING_CENTRES', guide)
                for warning in manifest.get('warnings', []):
                    code = 'GUIDING_CENTRES_ENABLED' if 'guiding_centres=true' in str(warning) else 'INPUT_WARNING'
                    event('WARNING', code, warning)
            if record:
                status = record.get('status', 'unknown')
                event('ERROR' if record.get('returncode') not in (None, 0) or 'failed' in status or 'timed_out' in status else 'INFO',
                      folder.upper()+'_STATUS', status)
                field('JOB_ID', record.get('slurm_job_id', record.get('job_id')))
                field('STARTED_UTC', record.get('started_utc'))
                field('FINISHED_UTC', record.get('finished_utc'))
                field('RETURN_CODE', record.get('returncode'))
                field('WALL_SECONDS', record.get('wall_seconds'))
                if folder=='wannier':
                    dis_record = record.get('disentanglement') or {}
                    field('DIS_CONVERGENCE', dis_record.get('state'))
                    field('DIS_ITERATIONS', dis_record.get('iterations'))
                    field('WOUT', str(location(trial, 'wannier/wannier90.wout')))
                problems = record.get('problems', [])
                if record.get('problem'):
                    problems = [*problems, record['problem']]
                for problem in problems:
                    event('ERROR', folder.upper()+'_PROBLEM', problem)

        fit = read(trial/'comparison/fit_review.json')
        diagnostic = read(trial/'comparison/diagnostics.json')
        validation = read(trial/'comparison/validation_report.json')
        spreads = final_spreads(trial/'wannier/wannier90.wout')
        if fit or spreads or validation.get('error'):
            group('RESULTS')
        if validation.get('error'):
            event('ERROR', 'COMPARISON_FAILED', validation['error'])
        if fit:
            event('INFO', 'FIT_REVIEW', fit.get('status', 'recorded'))
            field('FIT_PASS', fit.get('fit_pass'))
            for name in ('frozen', 'outer', 'target'):
                metrics = diagnostic.get(name, fit.get(name, (fit.get('metrics') or {}).get(name)))
                if isinstance(metrics, dict):
                    values = metrics.get('matched_error_metrics', metrics)
                    entries = [label+'='+format(1000*values[key], '.6g')
                        for label, key in [('MAE', 'mae_eV'), ('RMSE', 'rmse_eV'),
                                          ('P95', 'p95_abs_eV'), ('MAX', 'max_abs_eV')]
                        if values.get(key) is not None]
                    if entries:
                        field(name.upper()+'_ERROR_MEV', '  '.join(entries))
                    counts = metrics.get('counts') or {}
                    if counts:
                        field(name.upper()+'_STATES', '  '.join(k+'='+text(counts[key]) for k, key in
                            [('target','dft_target_states'), ('matched','matched_states'),
                             ('unmatched','unmatched_dft_states')] if key in counts))
            field('FIT_RECORD', str(location(trial, 'comparison/fit_review.json')))
            field('FIT_FIGURE', str(location(trial, 'comparison/fit_review.png')))
        if spreads:
            field('WF_COUNT', len(spreads))
            raw = (trial/'wannier/wannier90.wout').read_text(errors='replace')
            omega = []
            for name in ('I', 'D', 'OD', 'TOT'):
                matches = re.findall(r'\bO_'+name+r'\s*=\s*([-+\d.Ee]+)', raw)
                if name=='I' and not matches:
                    matches = re.findall(r'Final Omega_I\s+([-+\d.Ee]+)', raw)
                omega.append(float(matches[-1]) if matches else None)
            if any(value is not None for value in omega):
                lines.append('    OMEGA (A^2)             I                D               OD            TOTAL')
                lines.append('                  ' + ' '.join(f'{value:>16.6f}' if value is not None else f'{"unknown":>16}' for value in omega))

            lines.extend(['', '    SPREAD (A^2)          MEAN             MAX',
                          f'                        {sum(spreads)/len(spreads):>10.6f}      {max(spreads):>10.6f}'])
        for restart in sorted((trial/'technical').glob('restart_*/RESTART.json')):
            info = read(restart)
            event('INFO', 'TECHNICAL_RESTART', restart.parent.name)
            field('COMMENT', short(info.get('reason'), 120))
            if info.get('status'):
                field('RESTART_STATUS', info['status'])
                field('EXECUTION_DIR', str(restart.parent.relative_to(case)))
                for key in ('MPI_RANKS', 'NUM_ITER', 'DIS_CONVERGENCE', 'DIS_ITERATIONS', 'SPREAD_MAX_A2', 'ORIGINAL_FROZEN_RMSE_MEV'):
                    if key.lower() in info:
                        field(key, info[key.lower()])
            field('RESTART_RECORD', str(restart.relative_to(case)))
        results = sorted((trial/'records/results').glob('result_*.json'))
        if results:
            assessment = read(results[-1]).get('assessment') or {}
            if assessment.get('next_action'):
                field('NEXT_ACTION', short(assessment['next_action'], 120))
    lines[summary_at] = f'  TRIALS: {len(trials)}    WARNINGS: {levels["WARNING"]}    ERRORS: {levels["ERROR"]}'
    lines.extend(['', '=' * WIDTH, 'END OF MATERIAL LOG', ''])
    return '\n'.join(lines)


def _write(case, output=None):
    case=Path(case).resolve();path=Path(output) if output else case/'WANNIER.log'
    if path.is_symlink() or (path.exists() and not path.read_text().startswith(HEADER+'\n')):
        raise ValueError('Refusing to replace an unmanaged log: '+str(path))
    body=render(case)
    fd,name=tempfile.mkstemp(prefix='.wannier-log-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:stream.write(body)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
    return str(path)


def write(case, output=None):
    case=Path(case).resolve();path=Path(output) if output else case/'WANNIER.log'
    lock=path.with_name('.'+path.name+'.lock')
    fd=os.open(lock,os.O_CREAT|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        return _write(case,output)


def refresh(directory):
    """Refresh the human view after a recorded operation; no science is changed."""
    directory=Path(directory).resolve()
    for case in (directory,*directory.parents):
        if (case/'MATERIAL-LAYOUT.json').is_file():
            try:
                return write(case)
            except (OSError,ValueError,TypeError) as error:
                print('[ERROR] MATERIAL_LOG_WRITE_FAILED | '+str(error),file=sys.stderr)
                return None
    return None


if __name__=='__main__':

    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case-dir',required=True);p.add_argument('--output')
    a=p.parse_args();print(write(a.case_dir,a.output))
