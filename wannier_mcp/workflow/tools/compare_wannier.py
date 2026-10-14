#!/usr/bin/env python3
"""Compare a standard Wannier trial with DFT at the exact band-path points.

Run inside an allocated Slurm compute node. Bundled helpers preserve the executed Hamiltonian and share the SCF energy zero. A fresh validation directory is mandatory.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

# Apply to the driver, plotter subprocess and native MPI ranks, before NumPy loads.
for _thread_variable in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_thread_variable] = '1'

import numpy as np
from scipy.optimize import linear_sum_assignment


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def relative_energies(dft_absolute, wannier_absolute, ef):
    """Subtract the very same supplied SCF EF; never fit a shift."""
    a, b = np.asarray(dft_absolute, dtype=float), np.asarray(wannier_absolute, dtype=float)
    if a.ndim != 2 or b.ndim != 2 or len(a) != len(b) or not len(a):
        raise ValueError('Expected matching nonempty k axes and two-dimensional energy arrays.')
    if not np.isfinite(ef) or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('Energy arrays and SCF EF must be finite.')
    return a - ef, b - ef


def review_title(material, label, context):
    """Describe the computation state without turning a null fit_pass into failure."""
    if context.get('provisional'):
        state = 'PROVISIONAL: disentanglement not converged | energy diagnostic; NOT ACCEPTED'
    else:
        state = 'energy review; TOOL STATUS: NOT EVALUATED'
    if context.get('not_converged_by_2000'):
        state += ' | slow disentanglement: not converged by 2000'
    return f'{material} / {label} | {state}'


def match_region(dft_relative, wannier_relative, interval):
    """Prioritize all DFT target states, matching against all W90 states per k.

    The minimum-cost rectangular assignment has no error cutoff. Missing/extra
    counts describe assignment cardinality, not a physical branch assessment.
    """
    dft, wan = np.asarray(dft_relative), np.asarray(wannier_relative)
    if dft.ndim != 2 or wan.ndim != 2 or len(dft) != len(wan):
        raise ValueError('Energy arrays must share their k axis.')
    if not np.isfinite(dft).all() or not np.isfinite(wan).all():
        raise ValueError('Nonfinite eigenvalue.')
    lo, hi = interval
    if not np.isfinite([lo, hi]).all() or lo >= hi:
        raise ValueError('Diagnostic interval must be finite and increasing.')
    rows, errors = [], []
    totals = dict(dft_target_states=0, matched_states=0, unmatched_dft_states=0,
                  wannier_states_in_interval=0, unmatched_wannier_states_in_interval=0,
                  matched_wannier_states_outside_interval=0)
    for k, (de, we) in enumerate(zip(dft, wan), 1):
        target = np.flatnonzero((de >= lo) & (de <= hi))
        inside = np.flatnonzero((we >= lo) & (we <= hi))
        ri, wi = linear_sum_assignment(np.abs(de[target, None] - we[None, :]))
        di = target[ri]
        missing = np.setdiff1d(target, di)
        extra = np.setdiff1d(inside, wi)
        error = we[wi] - de[di]
        errors.extend(error.tolist())
        totals['dft_target_states'] += len(target)
        totals['matched_states'] += len(di)
        totals['unmatched_dft_states'] += len(missing)
        totals['wannier_states_in_interval'] += len(inside)
        totals['unmatched_wannier_states_in_interval'] += len(extra)
        totals['matched_wannier_states_outside_interval'] += int(((we[wi] < lo) | (we[wi] > hi)).sum())
        rows.append(dict(k_index_1based=k, pairs=[dict(dft_band_1based=int(i+1),
            wannier_band_1based=int(j+1), dft_eV=float(de[i]), wannier_eV=float(we[j]),
            error_eV=float(we[j]-de[i])) for i,j in zip(di,wi)],
            unmatched_dft=[dict(band_1based=int(i+1), energy_eV=float(de[i])) for i in missing],
            unmatched_wannier_in_interval=[dict(band_1based=int(i+1), energy_eV=float(we[i])) for i in extra]))
    err = np.asarray(errors)
    metrics = dict(mae_eV=float(np.abs(err).mean()), rmse_eV=float(np.sqrt(np.mean(err**2))),
                   p95_abs_eV=float(np.percentile(np.abs(err),95)), max_abs_eV=float(np.abs(err).max())) if err.size else None
    return dict(interval_relative_to_scf_ef_eV=[float(lo),float(hi)], counts=totals,
                matched_error_metrics=metrics, per_k=rows,
                assignment='independent per-k minimum total absolute-energy one-to-one rectangular matching; DFT interval states against every W90 eigenvalue',
                weighting='equal sampled states, including repeated path endpoints; no BZ or path-length weighting',
                limitations=['No error threshold: a far-away eigenvalue can still be assigned.',
                    'Unmatched means cardinality only, not verified missing or spurious physical branches.',
                    'No band connectivity, wavefunction, orbital, symmetry or off-path validation.',
                    'Interval analyses are independent; they do not establish a common global band mapping.'],
                fit_pass=None, energy_shift_fitted=False)


def source_completion(wannier, helper, seed, workflow_report=None):
    from wannier_workflow import read_wout
    win = wannier/f'{seed}.win'
    values, nw, _ = helper.win_info(win.read_text())
    if workflow_report and workflow_report.get('technical_output_recovery'):
        if not workflow_report.get('ready_for_fit') or workflow_report.get('no_op'):
            raise ValueError('Technical output recovery did not produce an authenticated private output set.')
        dis=workflow_report['source_disentanglement']
        source=workflow_report['source_context']
        provisional=dis['state']=='iteration_limit_reached'
        source_wout=source['source_files'][f'{seed}.wout']
        return values,dict(normal_completion=True,disentanglement_converged=dis['state']=='converged',
            disentanglement_required=int(values['num_bands'])>nw,
            not_converged_by_2000=dis.get('not_converged_by_2000'),review_flags=dis.get('review_flags',[]),
            convergence_controls=dict(dis_conv_tol=float(values.get('dis_conv_tol','1e-10')),
                dis_conv_window=int(values.get('dis_conv_window','3')),
                source='sealed source Wannier input and technical recovery record'),
            provisional=provisional,num_iter=0,dis_num_iter_limit=dis['dis_num_iter'],
            observed_dis_iterations=dis.get('last_iteration'),
            wout=dict(path=source_wout['path'],sha256=source_wout['sha256']),
            recovery_wout=workflow_report['recovery_wout'],win=dict(path=str(win),sha256=sha(win)),
            technical_output_recovery=True,scientific_proposal_changed=False,
            disentanglement_context='Inherited from sealed source WOUT; recovery WOUT is required to contain no DIS history.',
            fit_pass=None,status='provisional_unconverged_diagnostic_not_accepted' if provisional else 'diagnostic_not_accepted')
    wout = wannier/f'{seed}.wout'
    text = wout.read_text()
    if text.count('All done: wannier90 exiting') != 1 or not text.rstrip().endswith('All done: wannier90 exiting'):
        raise ValueError('Require one normally completed W90 run; a setup-only, running or appended output is insufficient.')
    limits = [int(x) for x in re.findall(r'Total number of iterations\s*:\s*(\d+)', text)]
    required = int(values['num_bands']) > nw
    budget = int(values['dis_num_iter'])
    if (not limits or limits[0] != 0 or (required and limits != [0,budget]) or
            (not required and limits not in ([0],[0,budget]))):
        raise ValueError('WOUT iteration limits do not authenticate the num_iter=0/dis_num_iter input.')
    numbers = re.findall(r'Number of Wannier Functions\s*:\s*(\d+)', text)
    if len(numbers) != 1 or int(numbers[0]) != nw:
        raise ValueError('WOUT num_wann differs from the current input.')
    yes = 'Disentanglement convergence criteria satisfied' in text
    no = 'Disentanglement convergence criteria not satisfied' in text
    if yes and no:
        raise ValueError('Conflicting convergence markers; source is ambiguous.')
    if required and not yes and not no:
        raise ValueError('No explicit disentanglement convergence result in the completed WOUT.')
    if not required and (yes or no):
        raise ValueError('Unexpected disentanglement result for a model without disentanglement.')
    observed = re.findall(r'^\s*(\d+)\s+[-+\d.Ee]+\s+[-+\d.Ee]+\s+[-+\d.Ee]+\s+[-+\d.Ee]+\s+<-- DIS\s*$',text,re.M)
    provisional = required and not yes
    convergence = read_wout(wout,budget,required)
    return values, dict(normal_completion=True, disentanglement_converged=yes,
        disentanglement_required=required,
        not_converged_by_2000=convergence['not_converged_by_2000'],
        review_flags=convergence['review_flags'],
        convergence_controls=dict(dis_conv_tol=float(values.get('dis_conv_tol','1e-10')),
            dis_conv_window=int(values.get('dis_conv_window','3')),
            source='explicit input or Wannier90 3.x defaults for legacy inputs'),
        provisional=provisional, num_iter=0, dis_num_iter_limit=budget,
        observed_dis_iterations=int(observed[-1]) if observed else None,
        wout=dict(path=str(wout),sha256=sha(wout)), win=dict(path=str(win),sha256=sha(win)),
        fit_pass=None, status='provisional_unconverged_diagnostic_not_accepted' if provisional else 'diagnostic_not_accepted')


def allocated_node(ntasks):
    job = os.environ.get('SLURM_JOB_ID')
    nodes = os.environ.get('SLURM_JOB_NODELIST')
    if not job or not nodes:
        raise ValueError('Run only inside a Slurm compute-node allocation; no login-node science execution.')
    available = int(os.environ.get('SLURM_NTASKS','0'))
    if ntasks < 1 or available < ntasks:
        raise ValueError(f'Requested {ntasks} tasks, but SLURM_NTASKS={available}.')
    hosts = subprocess.run(['scontrol','show','hostnames',nodes],check=True,capture_output=True,text=True).stdout.split()
    host = socket.gethostname().split('.')[0]
    if host not in {h.split('.')[0] for h in hosts} or re.match(r'login',host,re.I):
        raise ValueError('Current host is not a compute node in this Slurm allocation.')
    return dict(job_id=job,host=host,allocated_nodes=hosts,allocated_ntasks=available,used_ntasks=ntasks)


def add_context_to_new_report(path, context):
    """Annotate only outputs created in this invocation's new directory."""
    value = json.loads(path.read_text())
    value['validation_context'] = context
    value['provisional'] = context['provisional']
    value['fit_pass'] = None
    if context['provisional']:
        value['status'] = 'provisional_' + value.get('status','diagnostic')
    # File was newly generated by the unchanged helper/plotter in our private output directory.
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def comparison_trial(material, trial_id, trial_dir=None):
    """Resolve either the canonical scientific trial or one recorded technical restart."""
    import material_layout as layout
    canonical = layout.trial_path(material, trial_id)
    if trial_dir is None:
        return canonical, False
    requested = Path(trial_dir).resolve()
    technical = (canonical/'technical').resolve()
    try:
        relative = requested.relative_to(technical)
    except ValueError as error:
        raise ValueError('A comparison --trial-dir must be inside the canonical trial technical/ directory.') from error
    if len(relative.parts) != 1 or not re.fullmatch(r'restart_[0-9]{3,}',relative.name):
        raise ValueError('A technical comparison directory must be one direct restart_NNN child of technical/.')
    if not requested.is_dir():
        raise ValueError('Technical restart directory does not exist: '+str(requested))
    return requested, True


def run(args):
    started = time.monotonic()
    node = allocated_node(args.ntasks)
    import material_layout as layout
    import wannier_workflow as workflow
    material = layout.load_case(args.case_dir)
    case, technical_retry = comparison_trial(material,args.trial_id,getattr(args,'trial_dir',None))
    wan = case/'wannier'
    source_report = workflow.inspect(wan)
    if not source_report.get('execution_complete') or not source_report.get('ready_for_fit',source_report.get('execution_complete')):
        raise ValueError('Require completed Wannier execution with authenticated mandatory outputs.')
    args.seed = 'wannier90'
    args.validation_name = 'comparison'
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',args.validation_name):
        raise ValueError('validation-name must be one simple new directory basename.')
    out = case/args.validation_name
    if out.exists():
        raise ValueError('Validation output already exists; choose a new --validation-name.')
    pkg = Path(__file__).resolve().parents[1]
    helper_path, site_path = Path(__file__).with_name('geninterp_path.py'), Path(args.site).resolve()
    helper = module_from(helper_path,'current_geninterp_helper')
    values, context = source_completion(wan,helper,args.seed,source_report)
    site = json.loads(site_path.read_text())
    python = str(Path(site['python']).resolve())
    post = Path(args.postw90).resolve()
    if not post.is_file() or not os.access(post,os.X_OK):
        raise ValueError('An explicit executable --postw90 path is required.')
    metrics = Path(args.run_metrics).resolve() if args.run_metrics else None
    if metrics is not None and (not metrics.is_file() or not args.ledger or not args.experiment_id):
        raise ValueError('--run-metrics requires an existing script, --ledger and --experiment-id.')
    plotter = pkg/'tools/plot_fit_review.py'
    if not plotter.is_file():
        raise ValueError('Package has no plot_fit_review.py.')
    cache,path = material/'02_band/charge.npz',material/'02_band/fit_path.json'
    with np.load(cache,allow_pickle=False) as z:
        meta = json.loads(str(z['metadata']))
        dft_absolute = np.asarray(z['energies'],dtype=float)
    if dft_absolute.ndim != 3 or dft_absolute.shape[0] != 1:
        raise ValueError('Require one charge-channel DFT cache.')
    prov = meta.get('ef_provenance',{})
    scf_stage = material/'01_scf'
    scf_outcar = (scf_stage/'source' if (scf_stage/'source').is_dir() else scf_stage)/'OUTCAR'
    if prov.get('kind') != 'explicit_static_scf_outcar' or sha(scf_outcar) != prov.get('sha256'):
        raise ValueError('DFT cache must authenticate the supplied material static-SCF EF source.')
    ef = float(meta['ef'])
    raw = re.findall(r'E-fermi\s*:\s*([-+\d.Ee]+)',scf_outcar.read_text(errors='replace'))
    if not raw or abs(float(raw[-1])-ef)>1e-8:
        raise ValueError('Cache EF differs from the authenticated SCF OUTCAR.')
    frozen = [float(values[key].replace('d','e').replace('D','E'))-ef for key in ('dis_froz_min','dis_froz_max')]
    outer = [float(values[key].replace('d','e').replace('D','E'))-ef for key in ('dis_win_min','dis_win_max')]
    environment = Path(site['environment_script']).resolve()
    if not environment.is_file() or not isinstance(site.get('environment_args',[]),list):
        raise ValueError('Site environment script/argument list is invalid.')
    launch = site.get('launcher')
    if not isinstance(launch,list) or not launch or not all(isinstance(s,str) for s in launch):
        raise ValueError('Site MPI launcher must be a nonempty argv list.')
    launch = [s.format(ntasks=args.ntasks) for s in launch]
    post_command = launch + [str(post),args.seed]
    context.update(allocation=node,diagnostic_only=True,accepted=False,
        ef_eV=ef,ef_source=meta['ef_source'],energy_shift_fitted=False,
        note='Normal completion and disentanglement convergence do not accept the fit; all errors below are energy-only diagnostics.')
    report = dict(status='failed_before_native_run',validation_context=context,provisional=context['provisional'],
        fit_pass=None,accepted=False,inputs=dict(package=str(pkg),helper=str(helper_path),helper_sha256=sha(helper_path),
        site=str(site_path),site_sha256=sha(site_path),postw90=str(post),postw90_sha256=sha(post),
        dft_cache=str(cache),dft_cache_sha256=sha(cache),scf_ef_source=str(scf_outcar),scf_ef_sha256=prov['sha256']),
        driver_sha256=sha(__file__))
    helper.prepare(wan,cache,path,out,post,args.seed)
    add_context_to_new_report(out/'geninterp_manifest.json',context)
    try:
        attempt = f'{case.name}:{args.validation_name}'
        wrapped = [python,str(metrics),'run','--ledger',str(Path(args.ledger).resolve()),
            '--experiment-id',args.experiment_id,'--stage','wannier_validate_geninterp','--attempt-id',attempt,
            '--attempt-origin','model','--num-iter-limit','0','--dis-num-iter-limit',str(context['dis_num_iter_limit']),
            '--cwd',str(out),'--capture','--'] + post_command if metrics else post_command
        shell = '\n'.join(['set -eo pipefail',
            'source ' + shlex.join([str(environment)] + [str(s) for s in site.get('environment_args',[])]),
            'export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1',
            'ulimit -s unlimited','exec ' + shlex.join(wrapped)]) + '\n'
        (out/'run_native.sh').write_text(shell)
        report['native_argv'] = post_command
        with (out/'driver_native.stdout.log').open('x') as stdout,(out/'driver_native.stderr.log').open('x') as stderr:
            result = subprocess.run(['bash',str(out/'run_native.sh')],cwd=out,stdout=stdout,stderr=stderr)
        report['native_wrapper_returncode'] = result.returncode
        if result.returncode:
            raise ValueError(f'Native/accounting wrapper failed with exit {result.returncode}; see driver logs and ledger.')
        helper.convert(out)
        add_context_to_new_report(out/'geninterp_conversion.json',context)
        # Display annotations are extracted from the executed .win; no .win is reconstructed.
        display = dict(windows=dict(dis_eV=outer,frozen_eV=frozen),validation_context=context,
                       provisional=context['provisional'],fit_pass=None,status=context['status'])
        save_new(out/'executed_windows_for_plot.json',display)
        label = args.trial_id + (f' / technical/{case.name}' if technical_retry else '')
        title = review_title(material.name,label,context)
        plot = [python,str(plotter),'--cache',str(cache),'--wannier-dat',str(out/f'{args.seed}_band.dat'),
            '--wannier-kpt',str(out/f'{args.seed}_band.kpt'),'--wannier-energy-reference','absolute','--path',str(path),
            '--decision',str(out/'executed_windows_for_plot.json'),
            '--title',title,'--out-prefix',str(out/'fit_review')]
        with (out/'driver_plot.stdout.log').open('x') as stdout,(out/'driver_plot.stderr.log').open('x') as stderr:
            subprocess.run(plot,cwd=out,stdout=stdout,stderr=stderr,check=True)
        add_context_to_new_report(out/'fit_review.json',context)
        sys.path.insert(0,str(pkg/'tools'))
        plot_module = module_from(plotter,'current_fit_plotter')
        _,_,wannier_absolute = plot_module.read_wannier(out/f'{args.seed}_band.dat',out/f'{args.seed}_band.kpt')
        dft,wi = relative_energies(dft_absolute[0],wannier_absolute,ef)
        diagnostics = dict(status=context['status'],provisional=context['provisional'],validation_context=context,
            fit_pass=None,accepted=False,ef_eV=ef,ef_source=meta['ef_source'],energy_shift_fitted=False,
            frozen=match_region(dft,wi,frozen),outer=match_region(dft,wi,outer),
            region_role='Actual executed frozen/outer windows; no fixed EF interval. Outer contains disentanglement background and is not a promise to fit every DFT state.',
            sources=dict(cache_sha256=sha(cache),wannier_dat_sha256=sha(out/f'{args.seed}_band.dat')),
            method='scipy.optimize.linear_sum_assignment on absolute energy differences at each identical DFT k point',
            disclaimer='Energy-only independent per-k diagnostic, not branch/wavefunction matching or scientific acceptance; no new pass threshold.')
        save_new(out/'diagnostics.json',diagnostics)
        report.update(status=context['status'],outputs=dict(figure_png=str(out/'fit_review.png'),
            figure_pdf=str(out/'fit_review.pdf'),diagnostics=str(out/'diagnostics.json')))
    except Exception as exc:
        report.update(status='provisional_failed_validation' if context['provisional'] else 'failed_validation',
                      error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        report['driver_wall_seconds'] = time.monotonic()-started
        report['timing_scope'] = ('driver total includes preparation/native interpolation/conversion/plotting; '
            + ('native interpolation separately recorded by run_metrics' if metrics else 'no external accounting wrapper supplied')
            + '; not a new disentanglement attempt')
        save_new(out/'validation_report.json',report)
    display = 'technical_retry_private' if technical_retry else layout.publish(material,args.trial_id)['status']
    return dict(status=report['status'],directory=str(out),fit_pass=None,provisional=context['provisional'],display=display)


def submit(args):
    import dft_workflow as dft
    import material_layout as layout
    import wannier_workflow as workflow
    case = layout.load_case(args.case_dir)
    trial, technical_retry = comparison_trial(case,args.trial_id,getattr(args,'trial_dir',None))
    report = workflow.inspect(trial/'wannier')
    if not report['execution_complete'] or not report.get('ready_for_fit',report['execution_complete']):
        raise ValueError('Wannier execution must complete before comparison submission.')
    if (trial/'comparison').exists():
        raise ValueError('Comparison already exists; do not submit a duplicate.')
    site_path = Path(args.site).resolve()
    site = dft.site_profile(site_path)
    if site['scheduler'] != 'slurm' or args.ntasks < 1:
        raise ValueError('Comparison submission requires Slurm and positive ntasks.')
    command = [site['python'],str(Path(__file__).resolve()),'run','--case-dir',str(case),
        '--trial-id',args.trial_id,'--site',str(site_path),'--postw90',str(Path(args.postw90).resolve()),
        '--ntasks',str(args.ntasks)]
    if technical_retry:
        command += ['--trial-dir',str(trial)]
    if args.run_metrics:
        if not args.ledger or not args.experiment_id:
            raise ValueError('Accounting requires --ledger and --experiment-id.')
        command += ['--run-metrics',str(Path(args.run_metrics).resolve()),'--ledger',str(Path(args.ledger).resolve()),
            '--experiment-id',args.experiment_id]
    records = trial/'records'
    lines = ['#!/bin/bash',f'#SBATCH --nodes={site["nodes"]}',f'#SBATCH --ntasks={args.ntasks}',
        '#SBATCH --job-name=wannier-comparison',
        '#SBATCH --output='+shlex.quote(str(records/'comparison-%j.out')),
        '#SBATCH --error='+shlex.quote(str(records/'comparison-%j.err'))]
    for key in ('partition','account','time','exclude'):
        if site.get(key): lines.append(f'#SBATCH --{key}={site[key]}')
    script = '\n'.join(lines+['set -euo pipefail','ulimit -s unlimited',
        'export PYTHONDONTWRITEBYTECODE=1',shlex.join(command)])+'\n'
    if not args.execute:
        return dict(submitted=False,script=script)
    destination = records/'comparison_submissions'
    # A technical retry directory may contain only ``wannier/``.  Submission
    # records are part of the comparison operation, so create the complete
    # private records tree instead of requiring callers to pre-seed it.
    destination.mkdir(parents=True,exist_ok=True)
    path = destination/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.slurm')
    path.write_text(script)
    process = subprocess.run(['sbatch','--parsable',str(path)],capture_output=True,text=True)
    result = dict(returncode=process.returncode,stdout=process.stdout.strip(),stderr=process.stderr.strip(),
        script=str(path),script_sha256=sha(path),submitted=process.returncode==0,accepted=False)
    save_new(path.with_suffix('.json'),result)
    if process.returncode:
        raise ValueError('Comparison submission failed; inspect its submission record.')
    return dict(result,job_id=process.stdout.strip().split(';')[0])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('run','submit'),nargs='?',default='run')
    for flag in ('case-dir','site','postw90'):
        p.add_argument('--'+flag,required=True)
    p.add_argument('--trial-id',default='trial_001')
    p.add_argument('--trial-dir',help='Optional technical/restart_NNN directory inside the canonical trial.')
    p.add_argument('--run-metrics',help='Optional external accounting/run_metrics.py wrapper.')
    p.add_argument('--ledger')
    p.add_argument('--experiment-id')
    p.add_argument('--ntasks',type=int,default=1)
    p.add_argument('--execute',action='store_true',help='Only for submit: omission returns the job script.')
    args = p.parse_args()
    try:
        if args.command=='run' and args.execute:
            raise ValueError('--execute belongs only to submit.')
        print(json.dumps(submit(args) if args.command=='submit' else run(args),allow_nan=False))
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as exc:
        p.exit(2,str(exc)+'\n')


if __name__ == '__main__':
    main()
