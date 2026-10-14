#!/usr/bin/env python3
"""Prepare and execute disentanglement-only Wannier90 trials under Slurm."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

import dft_workflow as dft
import orbital_evidence as evidence
import prepare_wannier_interface as interface

SEED = 'wannier90'
INPUTS = tuple(SEED+'.'+suffix for suffix in ('win','amn','mmn','eig'))
MANIFEST = 'wannier_manifest.json'
RECORD = 'wannier_run.json'
REQUIRED_PRODUCTS = (SEED+'.chk',SEED+'_hr.dat',SEED+'_r.dat',SEED+'_tb.dat',SEED+'_band.dat',SEED+'_band.kpt')
OUTPUTS = ('stdout.log','stderr.log',SEED+'.wout',SEED+'.werr',*REQUIRED_PRODUCTS)
RECOVERY_FORMAT = 'wannier-output-recovery-v1'
RECOVERY_INPUTS = (SEED+'.win',SEED+'.chk',SEED+'.eig')
RECOVERY_GENERATED_OUTPUTS = ('stdout.log','stderr.log',SEED+'.wout',SEED+'.werr',*REQUIRED_PRODUCTS[1:])
OUTPUT_CONTROLS = {*interface.NATIVE_OUTPUT_FLAGS,'bands_num_points'}
WINDOW_CONTROLS = ('dis_win_min','dis_win_max','dis_froz_min','dis_froz_max')
SOLVER_CONTROLS = {'dis_num_iter','dis_conv_tol','dis_conv_window'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def control_settings(text):
    scalars, blocks = interface.parse_win(text)
    if scalars.get('num_iter') != '0':
        raise ValueError('Wannier execution requires explicit num_iter = 0; localization is forbidden.')
    if not re.fullmatch(r'[1-9][0-9]*', scalars.get('dis_num_iter', '')):
        raise ValueError('An explicit positive integer dis_num_iter is required.')
    if 'restart' in scalars or interface.boolean(scalars.get('postproc_setup', 'false')):
        raise ValueError('This entry point starts a fresh disentanglement run, not restart or preprocessing.')
    return scalars, blocks


def recovery_control_settings(text):
    """Validate the deliberately narrow Wannier90 3.x restart=plot input."""
    scalars, blocks = interface.parse_win(text)
    if scalars.get('num_iter') != '0':
        raise ValueError('Output recovery preserves explicit num_iter = 0; localization is forbidden.')
    if not re.fullmatch(r'[1-9][0-9]*', scalars.get('dis_num_iter', '')):
        raise ValueError('Output recovery requires the sealed positive dis_num_iter value.')
    if scalars.get('restart', '').strip().lower() != 'plot':
        raise ValueError('Technical output recovery requires exactly restart = plot.')
    if interface.boolean(scalars.get('postproc_setup', 'false')):
        raise ValueError('Technical output recovery is not preprocessing.')
    if any(not interface.boolean(scalars.get(key,'false')) for key in interface.NATIVE_OUTPUT_FLAGS):
        raise ValueError('Output recovery requires write_hr/write_rmn/write_tb/bands_plot=true.')
    return scalars, blocks


def set_scalar(text, key, value):
    pattern = re.compile(r'^\s*'+re.escape(key)+r'\s*[=:]', re.I)
    lines = text.splitlines(keepends=True)
    found = [i for i,line in enumerate(lines) if pattern.match(line)]
    if len(found) > 1:
        raise ValueError('Duplicate Wannier control: '+key)
    if found:
        lines[found[0]] = f'{key} = {value}\n'
        return ''.join(lines)
    return text.rstrip('\n')+f'\n{key} = {value}\n'


def interface_execution_evidence(source, checked, allow_failed_window=False):
    """Cross-check the official execution record against current VASP outputs."""
    record_path = source/'interface_run.json'
    if not record_path.is_file():
        raise ValueError('Official interface_run.json execution evidence is required; matrix-only/external interfaces are not supported.')
    record_bytes = interface.require_file(record_path).read_bytes()
    record = json.loads(record_bytes)
    manifest_bytes = interface.require_file(source/'trial_manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest.get('manifest_id') != checked['manifest_id'] or
            evidence.digest({k:v for k,v in manifest.items() if k != 'manifest_id'}) != checked['manifest_id']):
        raise ValueError('Interface manifest changed after validation.')
    validation = record.get('validation',{})
    allowed_status = {'interface_files_consistent','window_gate_failed'} if allow_failed_window else {'interface_files_consistent'}
    ready = checked.get('ready_for_wannier')
    if (type(record.get('returncode')) is not int or record['returncode'] != 0 or
            checked.get('status') not in allowed_status or type(ready) is not bool or
            (not allow_failed_window and not ready) or
            record.get('manifest_id') != checked['manifest_id'] or
            record.get('status') != checked['status'] or
            record.get('interface_files_consistent') is not True or record.get('ready_for_wannier') is not ready or
            validation.get('manifest_id') != checked['manifest_id'] or
            validation.get('status') != checked['status'] or validation.get('ready_for_wannier') is not ready or
            record.get('parallelism',{}).get('policy') != 'interface-ncore1-kpar1-v1'):
        raise ValueError('Official interface execution record does not establish successful completion for this trial.')
    required = {'OUTCAR',*INPUTS}
    if manifest.get('mode') in ('band_to_interface', 'band_to_shared_wavecar_interface'):
        required.add('EIGENVAL')
    if manifest.get('mode') == 'band_to_shared_wavecar_interface':
        required.add('OSZICAR')
    hashes = record.get('output_hashes',{})
    if not required <= set(hashes):
        raise ValueError('Official interface execution record lacks required output hashes.')
    for name,digest in hashes.items():
        if Path(name).name != name or dft.sha(interface.require_file(source/name)) != digest:
            raise ValueError('Official interface output hash differs from current file: '+name)
    outcar_bytes = interface.require_file(source/'OUTCAR').read_bytes()
    outcar_digest = hashlib.sha256(outcar_bytes).hexdigest()
    if outcar_digest != hashes['OUTCAR']:
        raise ValueError('Official interface output hash differs from current file: OUTCAR')
    outcar = outcar_bytes.decode(errors='replace')
    if ('General timing and accounting' not in outcar or
            (manifest.get('mode') != 'band_to_shared_wavecar_interface' and
             not re.search(r'aborting loop because EDIFF is reached',outcar,re.I))):
        raise ValueError('Current interface OUTCAR lacks normal termination/electronic EDIFF convergence.')
    for tag,expected in [('NSW',0),('ICHARG',11),('NBANDS',manifest['expected']['nbands'])]:
        values = re.findall(r'\b'+tag+r'\s*=\s*([+\-]?\d+)',outcar)
        if not values or int(values[-1]) != expected:
            raise ValueError('Current interface OUTCAR setting differs: '+tag)
    return dict(record_sha256=hashlib.sha256(record_bytes).hexdigest(),outcar_sha256=outcar_digest,
                interface_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                output_hashes=dict(hashes),
                returncode=0,manifest_id=checked['manifest_id'],
                evidence='Official execution record, current output hashes and independent OUTCAR completion/settings checks.')


def copy_receipted_matrix(source, destination, name, expected_sha256):
    """Copy against the validated execution receipt, never a later snapshot."""
    if name not in INPUTS or name.endswith('.win'):
        raise ValueError('Only an unchanged interface matrix can use this copy gate.')
    original = interface.require_file(source/name)
    if dft.sha(original) != expected_sha256:
        raise ValueError('Interface matrix differs from execution receipt before copying: '+name)
    shutil.copyfile(original, destination/name)
    if dft.sha(destination/name) != expected_sha256:
        raise ValueError('Interface matrix changed while copying: '+name)


def review_window_reuse(source, manifest, cache, analysis_path, decision_path):
    """Review only new windows against unchanged band evidence and actual mesh."""
    from vasp_to_cache import read_eigenval
    if manifest.get('mode') not in ('band_to_interface', 'band_to_shared_wavecar_interface'):
        raise ValueError('Window-only reuse requires a reviewed projected-band interface source.')
    paths = {key:Path(value).resolve() for key,value in
             [('cache',cache),('analysis',analysis_path),('decision',decision_path)]}
    sources = {key:dict(path=str(path),sha256=dft.sha(interface.require_file(path))) for key,path in paths.items()}
    if sources['cache']['sha256'] != manifest['source_hashes'].get('cache'):
        raise ValueError('Window-only reuse requires the exact original projected-band cache.')
    analysis = dft.read_json(paths['analysis']); decision = dft.read_json(paths['decision'])
    checked = evidence.check_record(decision,analysis,paths['cache'])
    if decision['status'] != 'proposed_trial' or checked['check_scope'] != 'band_path':
        raise ValueError('Window-only reuse requires a reviewed band_path proposed_trial.')
    if decision['trial_basis'] != manifest['trial_basis']:
        raise ValueError('Window-only reuse cannot change basis or projection order; new projections need matching new AMN.')
    from valence_selection import retained_indices, sampled_gap_checks
    old_selection=manifest.get('band_selection')
    new_selection=decision.get('band_selection')
    old_ids=(old_selection or {}).get('exclude_bands_1based',[])
    new_ids=(new_selection or {}).get('exclude_bands_1based',[])
    if old_ids != new_ids:
        raise ValueError('Window-only reuse cannot change exclude_bands; prepare matching new interface matrices.')
    meta, _, _, _ = evidence.read_cache(paths['cache'])
    if (meta['ef'] != manifest['ef_eV'] or meta['spin_mode'] != manifest['expected']['spin_mode'] or
            checked['recomputed_checks']['num_wann'] != manifest['expected']['num_wann']):
        raise ValueError('Window-only reuse changed the Fermi reference, spin mode or basis dimension.')
    eigenval = interface.require_file(source/'EIGENVAL')
    current = read_eigenval(eigenval)
    actual_selection = manifest.get('band_selection')
    retained=retained_indices(actual_selection,current['energies'].shape[1])
    exclusion_gate=sampled_gap_checks(current['energies']-manifest['ef_eV'],current['occupations'],
        actual_selection,manifest['expected']['spin_mode'])
    if not exclusion_gate['gate_pass']:
        raise ValueError('Occupied-block exclusion fails on the actual source interface mesh.')
    windows = decision['windows']
    gate = evidence.window_checks(current['energies'][:,retained]-manifest['ef_eV'],manifest['expected']['num_wann'],windows,'wannier_mesh')
    gate['scope'] = ('Actual source interface EIGENVAL mesh sampled-state counts and top-band margin for the new windows; '
                     'no off-grid completeness, interpolation accuracy or fit certification.')
    if not gate['gate_pass']:
        raise ValueError('New windows fail the actual interface mesh gate: '+json.dumps(gate['violating_kpoints_1based']))
    scalars = {key:f'{value+manifest["ef_eV"]:.12g}' for key,value in
               zip(WINDOW_CONTROLS,windows['dis_eV']+windows['frozen_eV'])}
    return dict(format='window-only-matrix-reuse-v1',matrix_reuse=True,new_interface_execution=False,
        source_interface_manifest_id=manifest['manifest_id'],source_windows_relative_eV=manifest['windows_relative_eV'],
        reviewed_sources=sources,analysis=analysis,decision=decision,proposal_validation=checked,
        windows_relative_eV=windows,approved_window_scalars=scalars,ef_eV=manifest['ef_eV'],
        actual_eigenval=dict(path=str(eigenval.resolve()),sha256=dft.sha(eigenval)),
        actual_kpoints_sha256=evidence.digest(current['kcoords'].tolist()),mesh_gate=gate,
        projection_order_preserved=True,scope='Only the four reviewed window scalars change; source projections and all interface matrices are preserved.')


def prepare(interface_dir, out_dir, dis_num_iter=None, band_dir=None, cache=None, analysis=None, decision=None):
    started = utc_now(); timer = time.monotonic()
    source = Path(interface_dir).resolve(); destination = Path(out_dir).resolve()
    if destination.exists():
        raise ValueError('Use a new Wannier directory; existing trials are never overwritten.')
    dis_num_iter = interface.validate_dis_budget(interface.DIS_DEFAULT if dis_num_iter is None else dis_num_iter)
    supplied = [value is not None for value in (cache,analysis,decision)]
    if any(supplied) and not all(supplied):
        raise ValueError('Window-only reuse requires --cache, --analysis and --decision together.')
    reuse_requested = all(supplied)
    checked = interface.validate(source, outputs=True)
    if not checked.get('interface_files_consistent') or (not reuse_requested and not checked.get('ready_for_wannier')):
        raise ValueError('Interface matrices and actual mesh window gate must pass before Wannier preparation.')
    execution_evidence = interface_execution_evidence(source,checked,allow_failed_window=reuse_requested)
    interface_manifest_bytes = interface.require_file(source/'trial_manifest.json').read_bytes()
    if hashlib.sha256(interface_manifest_bytes).hexdigest() != execution_evidence['interface_manifest_sha256']:
        raise ValueError('Interface manifest changed after execution evidence was read.')
    interface_manifest = json.loads(interface_manifest_bytes)
    reuse = review_window_reuse(source,interface_manifest,cache,analysis,decision) if reuse_requested else None
    from decision_stages import require_executable_decision, require_execution_binding
    stage_binding = (require_executable_decision(decision, cache) if reuse_requested else
                     require_execution_binding(interface_manifest.get('decision_stage')))
    band_dir = band_dir or interface_manifest.get('source_directories',{}).get('band')
    if band_dir is None:
        raise ValueError('A verified projected-band source is required for native plots; provide --band-dir for a legacy interface.')
    expected_hashes = dict(interface_manifest.get('source_hashes',{}))
    expected_hashes.setdefault('band/POSCAR',interface_manifest['input_hashes']['POSCAR'])
    native_plot = interface.native_band_plot(band_dir,expected_hashes)
    original_bytes = interface.require_file(source/(SEED+'.win')).read_bytes()
    if hashlib.sha256(original_bytes).hexdigest() != execution_evidence['output_hashes'][SEED+'.win']:
        raise ValueError('Interface win changed after execution validation.')
    original = original_bytes.decode()
    original_scalars, original_blocks = control_settings(original)
    text = original; changes = []
    approved_scalars = dict(original_scalars)
    if reuse is not None:
        approved_scalars.update(reuse['approved_window_scalars'])
        for key,value in reuse['approved_window_scalars'].items():
            if original_scalars[key] != value:
                changes.append(dict(parameter=key,before=original_scalars[key],after=value,
                                    reason='New reviewed decision and recomputed actual interface mesh window gate; matrices reused unchanged.'))
                text = set_scalar(text,key,value)
    solver = dict(dis_num_iter=str(dis_num_iter), dis_conv_tol=str(interface.DIS_TOL),
                  dis_conv_window=str(interface.DIS_WINDOW))
    for key,value in solver.items():
        if original_scalars.get(key) != value:
            changes.append(dict(parameter=key, before=original_scalars.get(key), after=value,
                                reason='Current explicit disentanglement budget/tolerance policy; source interface preserved.'))
            text = set_scalar(text, key, value)
    for key,value in native_plot['scalars'].items():
        if original_scalars.get(key) != value:
            changes.append(dict(parameter=key,before=original_scalars.get(key),after=value,
                                reason='Required native output/plot control; no basis, window or numerical solver change.'))
            text = set_scalar(text,key,value)
    if 'kpoint_path' in original_blocks:
        if original_blocks['kpoint_path'] != native_plot['kpoint_path']:
            raise ValueError('Existing native plot path differs from the verified DFT band source.')
    else:
        text = text.rstrip('\n')+'\n\nbegin kpoint_path\n'+'\n'.join(native_plot['kpoint_path'])+'\nend kpoint_path\n'
        changes.append(dict(parameter='kpoint_path',before=None,after=native_plot['kpoint_path'],
                            reason='Native band plot follows the verified existing DFT path.'))
    scalars, blocks = control_settings(text)
    protected = lambda settings: {k:v for k,v in settings.items() if k not in {*SOLVER_CONTROLS,*OUTPUT_CONTROLS}}
    if (protected(scalars) != protected(approved_scalars) or
            {k:v for k,v in blocks.items() if k!='kpoint_path'} != {k:v for k,v in original_blocks.items() if k!='kpoint_path'}):
        raise ValueError('Preparation changed settings outside the approved windows, budget and native output/plot parameters.')
    source_hashes = {name:execution_evidence['output_hashes'][name] for name in INPUTS}
    source_hashes['trial_manifest.json'] = execution_evidence['interface_manifest_sha256']
    source_hashes['interface_run.json'] = execution_evidence['record_sha256']
    source_hashes['OUTCAR'] = execution_evidence['outcar_sha256']
    if reuse is not None:
        source_hashes['EIGENVAL'] = reuse['actual_eigenval']['sha256']
    destination.mkdir(parents=True)
    input_hashes = dict(source_hashes)
    input_hashes = {name:input_hashes[name] for name in INPUTS}
    input_hashes[SEED+'.win'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    for name in INPUTS:
        if name.endswith('.win'):
            (destination/name).write_text(text, encoding='utf-8')
        else:
            copy_receipted_matrix(source, destination, name, source_hashes[name])
    manifest = dict(decision_stage=stage_binding, format='wannier-execution-v1', seed=SEED, interface_dir=str(source),
        interface_manifest_id=checked['manifest_id'], interface_validation=checked,
        interface_execution_evidence=execution_evidence,
        source_hashes=source_hashes, source_win_scalars=original_scalars, source_win_blocks=original_blocks,
        input_hashes=input_hashes, changes=changes,
        controls=dict(num_iter=0, dis_num_iter=int(scalars['dis_num_iter']),
                      dis_conv_tol=float(scalars['dis_conv_tol']), dis_conv_window=int(scalars['dis_conv_window']),
                      **{key:True for key in interface.NATIVE_OUTPUT_FLAGS},bands_num_points=int(scalars['bands_num_points'])),
        native_plot=native_plot,
        dimensions=dict(num_wann=int(scalars['num_wann']), num_bands=int(scalars['num_bands'])),
        prepared_started_utc=started, prepared_finished_utc=utc_now(),
        preparation_wall_seconds=time.monotonic()-timer,
        scope='Prepared inputs only; no disentanglement convergence or interpolation acceptance claim.')
    if reuse is not None:
        manifest['window_reuse'] = reuse
    manifest['manifest_id'] = evidence.digest(manifest)
    dft.write_json(destination/MANIFEST, manifest)
    return dict(status='prepared_not_executed', directory=str(destination),
                manifest_id=manifest['manifest_id'], controls=manifest['controls'], changes=changes)


def validate_inputs(directory):
    directory = Path(directory).resolve()
    manifest = dft.read_json(interface.require_file(directory/MANIFEST))
    if (manifest.get('format') != 'wannier-execution-v1' or manifest.get('seed') != SEED or
            evidence.digest({k:v for k,v in manifest.items() if k!='manifest_id'}) != manifest['manifest_id']):
        raise ValueError('Wannier manifest identity/format mismatch.')
    scalars, blocks = control_settings(interface.require_file(directory/(SEED+'.win')).read_text())
    if any(not interface.boolean(scalars.get(key,'false')) for key in interface.NATIVE_OUTPUT_FLAGS):
        raise ValueError('Wannier execution requires write_hr/write_rmn/write_tb/bands_plot=true.')
    if set(manifest['input_hashes']) != set(INPUTS):
        raise ValueError('Wannier input inventory differs from the execution contract.')
    for name, digest in manifest['input_hashes'].items():
        if dft.sha(interface.require_file(directory/name)) != digest:
            raise ValueError('Prepared Wannier input changed: '+name)
    receipt_hashes = manifest.get('interface_execution_evidence',{}).get('output_hashes')
    if receipt_hashes is not None and any(
            manifest['input_hashes'][name] != receipt_hashes.get(name) or
            manifest['source_hashes'].get(name) != receipt_hashes.get(name)
            for name in INPUTS[1:]):
        raise ValueError('Prepared matrices differ from the original execution receipt.')
    if int(scalars['dis_num_iter']) != manifest['controls']['dis_num_iter']:
        raise ValueError('Disentanglement budget differs from the manifest.')
    # Legacy sealed runs remain inspectable. New preparations record both fields.
    for key,convert in [('dis_conv_tol',float),('dis_conv_window',int)]:
        if key in manifest['controls'] and convert(scalars.get(key,'nan')) != manifest['controls'][key]:
            raise ValueError('Disentanglement convergence control differs from the manifest: '+key)
    approved_scalars = dict(manifest['source_win_scalars'])
    if 'window_reuse' in manifest:
        reuse = manifest['window_reuse']; source = Path(manifest['interface_dir'])
        for name in ('trial_manifest.json','interface_run.json','OUTCAR','EIGENVAL'):
            if dft.sha(interface.require_file(source/name)) != manifest['source_hashes'].get(name):
                raise ValueError('Window reuse source changed: '+name)
        sources = reuse['reviewed_sources']
        current = review_window_reuse(source,dft.read_json(source/'trial_manifest.json'),
            sources['cache']['path'],sources['analysis']['path'],sources['decision']['path'])
        if current != reuse:
            raise ValueError('Window reuse review, source identity or actual mesh gate changed.')
        approved_scalars.update(reuse['approved_window_scalars'])
        if any(manifest['input_hashes'][name] != manifest['source_hashes'][name] for name in INPUTS[1:]):
            raise ValueError('Window-only reuse must preserve the original interface matrix hashes.')
        allowed = {*SOLVER_CONTROLS,*OUTPUT_CONTROLS,*WINDOW_CONTROLS,'kpoint_path'}
        if any(change['parameter'] not in allowed for change in manifest['changes']):
            raise ValueError('Unexpected parameter change in window-only reuse.')
    allowed_solver = SOLVER_CONTROLS if 'dis_conv_tol' in manifest['controls'] else {'dis_num_iter'}
    protected = lambda settings: {k:v for k,v in settings.items() if k not in {*allowed_solver,*OUTPUT_CONTROLS}}
    if (protected(scalars) != protected(approved_scalars) or
            {k:v for k,v in blocks.items() if k!='kpoint_path'} !=
            {k:v for k,v in manifest['source_win_blocks'].items() if k!='kpoint_path'}):
        raise ValueError('Reviewed basis, window, geometry or algorithm settings changed.')
    if (blocks.get('kpoint_path') != manifest['native_plot']['kpoint_path'] or
            int(scalars.get('bands_num_points','0')) != manifest['controls']['bands_num_points'] or
            manifest['controls']['bands_num_points'] < 2):
        raise ValueError('Verified native plot path or point budget changed.')
    return directory, manifest


def resources(profile, ntasks=1, allocation=False):
    site = dft.site_profile(profile)
    if site['scheduler'] != 'slurm':
        raise ValueError('Wannier execution requires a Slurm site; login-node/local execution is disabled.')
    if type(ntasks) is not int or ntasks < 1 or ntasks > site['ntasks']:
        raise ValueError('Requested Wannier ntasks must lie between 1 and site.ntasks.')
    nodes = min(site['nodes'], ntasks)
    launcher = site['launcher']
    if ntasks > 1 and not launcher:
        raise ValueError('Multiple Wannier ranks require a configured MPI launcher.')
    if launcher and not any('{ntasks}' in arg for arg in launcher):
        raise ValueError('Wannier launcher must use {ntasks} explicitly.')
    executable = Path(site.get('wannier90',''))
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('The site.wannier90 executable is unavailable.')
    command = [arg.replace('{ntasks}',str(ntasks)) for arg in launcher] + [str(executable), SEED]
    # Validate and use the same environment setup for the actual scientific command.
    command = dft.environment_command(site, command)
    record = dict(site_ntasks=site['ntasks'], effective_ntasks=ntasks, nodes=nodes,
                  command=command, executable=str(executable))
    if allocation:
        if not os.environ.get('SLURM_JOB_ID'):
            raise ValueError('Wannier run requires an active Slurm allocation.')
        try:
            available = int(os.environ['SLURM_NTASKS'])
        except (KeyError, ValueError) as exc:
            raise ValueError('SLURM_NTASKS is required to verify the allocation.') from exc
        if available < ntasks:
            raise ValueError('Insufficient Slurm tasks for the requested Wannier ranks.')
        record['allocated_ntasks'] = available
    return site, record


def ensure_fresh(directory):
    artifacts = [RECORD,*OUTPUTS]
    if any((directory/name).exists() or (directory/name).is_symlink() for name in artifacts):
        raise ValueError('Wannier execution artifacts already exist; prepare a new run directory.')


def read_wout(path, budget, disentanglement_required=True):
    text = interface.require_file(path).read_text(errors='replace')
    iterations = [int(match[1]) for match in re.finditer(r'^\s*(\d+)\s+[^\n]*<--\s*DIS\s*$', text, re.M)]
    consistent = iterations == list(range(1,len(iterations)+1)) and len(iterations) <= budget
    satisfied = bool(re.search(r'Disentanglement convergence criteria satisfied', text, re.I))
    unsatisfied = bool(re.search(r'Disentanglement convergence criteria not satisfied', text, re.I))
    limit = bool(re.search(r'Maximum number of disentanglement iterations reached', text, re.I))
    normal = bool(re.search(r'All done:\s*wannier90 exiting', text, re.I))
    if not consistent or (satisfied and (unsatisfied or limit)):
        state = 'inconsistent_output'
    elif satisfied and iterations:
        state = 'converged'
    elif limit and unsatisfied and len(iterations) == budget:
        state = 'iteration_limit_reached'
    elif not disentanglement_required and not iterations and not (satisfied or unsatisfied or limit):
        state = 'not_required'
    else:
        state = 'unconfirmed'
    threshold = interface.DIS_POLICY['slow_after_iterations']
    slow = (len(iterations)>threshold or (len(iterations)==threshold and state!='converged')) if consistent and state!='inconsistent_output' else None
    if state=='not_required': slow = False
    if state not in ('converged','not_required') and len(iterations)<threshold: slow = None
    return dict(state=state, iterations=len(iterations), last_iteration=iterations[-1] if iterations else None,
                slow_after_iterations=threshold, not_converged_by_2000=slow,
                convergence_at_iteration=len(iterations) if state=='converged' else None,
                review_flags=['slow_disentanglement'] if slow else [],
                dis_num_iter=budget, converged=state=='converged', iteration_limit_reached=state=='iteration_limit_reached',
                normal_completion_marker=normal, iteration_sequence_consistent=consistent,
                evidence=dict(convergence_satisfied=satisfied, convergence_not_satisfied=unsatisfied,
                              maximum_iterations_warning=limit),
                parser_contract='Wannier90 3.x DIS iteration rows and explicit convergence messages; exit status alone is insufficient.')


def stable_file_evidence(path):
    """Hash a source file twice and bind its identity, rejecting an active change."""
    path = interface.require_file(path)
    before = path.stat(); first = dft.sha(path); after = path.stat(); second = dft.sha(path)
    identity = lambda stat: (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)
    if identity(before) != identity(after) or first != second:
        raise ValueError('Source file changed while output recovery inspected it: '+str(path))
    return dict(path=str(path.resolve()),sha256=first,size=after.st_size,device=after.st_dev,
                inode=after.st_ino,mtime_ns=after.st_mtime_ns)


def source_run_evidence(source, manifest, source_job_id=None):
    """Read any partial ordinary record without pretending that it is complete."""
    record_path = source/RECORD
    record = None
    if record_path.is_file():
        record = dft.read_json(record_path)
        if record.get('manifest_id') != manifest['manifest_id']:
            raise ValueError('Source run record does not identify the source manifest.')
        if record.get('input_hashes') != manifest['input_hashes']:
            raise ValueError('Source run record input hashes differ from the source manifest.')
        for name,digest in record.get('output_hashes',{}).items():
            if Path(name).name != name or dft.sha(interface.require_file(source/name)) != digest:
                raise ValueError('Source run record output hash differs from the current file: '+name)
    recorded = None if record is None else record.get('slurm_job_id')
    if source_job_id is not None and recorded is not None and str(source_job_id) != str(recorded):
        raise ValueError('Explicit source job ID differs from the source execution record.')
    job_id = str(source_job_id if source_job_id is not None else recorded or '')
    if not re.fullmatch(r'[0-9]+(?:_[0-9]+)?',job_id):
        raise ValueError('A numeric Slurm source job ID is required for afterany output recovery.')
    return dict(job_id=job_id,record_path=str(record_path.resolve()) if record is not None else None,
                record_sha256=dft.sha(record_path) if record is not None else None,
                record_status=record.get('status') if record is not None else None,
                record_returncode=record.get('returncode') if record is not None else None)


def source_recovery_context(source, source_job_id=None):
    """Authenticate sealed science inputs, real DIS evidence and a postwann chk."""
    source, manifest = validate_inputs(source)
    evidence_files = {name:stable_file_evidence(source/name) for name in
                      (MANIFEST,SEED+'.win',SEED+'.wout',SEED+'.chk',SEED+'.eig')}
    text = interface.require_file(source/(SEED+'.wout')).read_text(errors='replace')
    dis = read_wout(source/(SEED+'.wout'),manifest['controls']['dis_num_iter'],
                    manifest['dimensions']['num_bands'] > manifest['dimensions']['num_wann'])
    if dis['state'] not in ('converged','iteration_limit_reached','not_required'):
        raise ValueError('Source WOUT does not establish a completed DIS state for output recovery.')
    final = list(re.finditer(r'\bFinal\s+State\b',text,re.I))
    if not final or not re.search(r'Writing\s+checkpoint\s+file\s+'+re.escape(SEED)+
                                  r'\.chk.*?\bdone\b',text[final[-1].end():],re.I|re.S):
        raise ValueError('Source WOUT does not establish a completed postwann checkpoint.')
    if evidence_files[SEED+'.chk']['size'] <= 0:
        raise ValueError('Source postwann checkpoint is empty.')
    return dict(source_path=str(source),source_manifest_id=manifest['manifest_id'],
                source_manifest_sha256=evidence_files[MANIFEST]['sha256'],source_files=evidence_files,
                source_disentanglement=dis,source_normal_completion_marker=dis['normal_completion_marker'],
                postwann_checkpoint_evidence='Final State followed by a completed checkpoint write in the sealed source WOUT.',
                source_job=source_run_evidence(source,manifest,source_job_id))


def materialize_readonly_input(source, destination):
    """Prefer a hard link for a large immutable recovery input, then verify it."""
    source = interface.require_file(source); source_before = stable_file_evidence(source)
    try:
        os.link(source,destination)
        method = 'hardlink'
    except OSError:
        shutil.copyfile(source,destination)
        method = 'copy_fallback'
    destination_after = stable_file_evidence(destination)
    if destination_after['sha256'] != source_before['sha256']:
        raise ValueError('Recovery input differs after materialization: '+source.name)
    if method == 'hardlink' and (destination_after['device'],destination_after['inode']) != (source_before['device'],source_before['inode']):
        raise ValueError('Hard-link identity check failed: '+source.name)
    return dict(method=method,source=source_before,destination=destination_after,
                read_only_contract='Workflow never writes this linked/copied input; Wannier90 3.x restart=plot reads it.')


def prepare_output_recovery(source_dir, out_dir, source_job_id=None):
    """Prepare a private restart=plot directory without changing scientific controls."""
    started=utc_now(); timer=time.monotonic()
    source=Path(source_dir).resolve(); destination=Path(out_dir).resolve()
    if destination.exists():
        raise ValueError('Use a new technical output-recovery directory; existing paths are never overwritten.')
    context=source_recovery_context(source,source_job_id)
    source_text=interface.require_file(source/(SEED+'.win')).read_text()
    source_scalars,source_blocks=control_settings(source_text)
    recovery_text=set_scalar(source_text,'restart','plot')
    recovery_scalars,recovery_blocks=recovery_control_settings(recovery_text)
    without_restart=dict(recovery_scalars); without_restart.pop('restart')
    if without_restart != source_scalars or recovery_blocks != source_blocks:
        raise ValueError('Output recovery changed a scientific, solver, basis, window or geometry control.')
    destination.mkdir(parents=True)
    (destination/(SEED+'.win')).write_text(recovery_text)
    materialization={name:materialize_readonly_input(source/name,destination/name)
                     for name in (SEED+'.chk',SEED+'.eig')}
    manifest=dict(format=RECOVERY_FORMAT,seed=SEED,technical_output_recovery=True,
        scientific_proposal_changed=False,restart='plot',source=context,
        input_hashes={name:dft.sha(destination/name) for name in RECOVERY_INPUTS},
        input_materialization=materialization,
        controls=dict(num_iter=0,dis_num_iter=int(recovery_scalars['dis_num_iter']),
            dis_conv_tol=float(recovery_scalars.get('dis_conv_tol',source_scalars.get('dis_conv_tol','1e-10'))),
            dis_conv_window=int(recovery_scalars.get('dis_conv_window',source_scalars.get('dis_conv_window','3'))),
            restart='plot',**{key:True for key in interface.NATIVE_OUTPUT_FLAGS},
            bands_num_points=int(recovery_scalars['bands_num_points'])),
        dimensions=dict(num_wann=int(recovery_scalars['num_wann']),num_bands=int(recovery_scalars['num_bands'])),
        prepared_started_utc=started,prepared_finished_utc=utc_now(),preparation_wall_seconds=time.monotonic()-timer,
        minimal_inputs_basis=('Wannier90 3.x restart=plot reads .win and postwann .chk directly; '
            '.eig is additionally required by write_hr/write_tb/bands_plot. AMN/MMN are intentionally absent.'),
        scope='Technical output recovery only; source disentanglement state is inherited and scientific acceptance is unchanged.')
    manifest['manifest_id']=evidence.digest(manifest)
    dft.write_json(destination/MANIFEST,manifest)
    return dict(status='technical_output_recovery_prepared',directory=str(destination),
                manifest_id=manifest['manifest_id'],technical_output_recovery=True,
                scientific_proposal_changed=False,source_job=context['source_job'],
                source_disentanglement=context['source_disentanglement'],materialization=materialization)


def read_recovery_manifest(directory):
    directory=Path(directory).resolve()
    manifest=dft.read_json(interface.require_file(directory/MANIFEST))
    if (manifest.get('format') != RECOVERY_FORMAT or manifest.get('seed') != SEED or
            manifest.get('technical_output_recovery') is not True or
            manifest.get('scientific_proposal_changed') is not False or manifest.get('restart') != 'plot' or
            evidence.digest({k:v for k,v in manifest.items() if k!='manifest_id'}) != manifest.get('manifest_id')):
        raise ValueError('Technical output-recovery manifest identity/format mismatch.')
    return directory,manifest


def validate_output_recovery_inputs(directory):
    directory,manifest=read_recovery_manifest(directory)
    if set(manifest.get('input_hashes',{})) != set(RECOVERY_INPUTS):
        raise ValueError('Output recovery input inventory differs from the minimal Wannier90 3.x contract.')
    recovery_text=interface.require_file(directory/(SEED+'.win')).read_text()
    recovery_scalars,recovery_blocks=recovery_control_settings(recovery_text)
    source=Path(manifest['source']['source_path']).resolve()
    source_directory,source_manifest=validate_inputs(source)
    source_text=interface.require_file(source/(SEED+'.win')).read_text()
    source_scalars,source_blocks=control_settings(source_text)
    without_restart=dict(recovery_scalars); without_restart.pop('restart')
    if without_restart != source_scalars or recovery_blocks != source_blocks:
        raise ValueError('Output recovery changed a scientific, solver, basis, window or geometry control.')
    for name,digest in manifest['input_hashes'].items():
        if dft.sha(interface.require_file(directory/name)) != digest:
            raise ValueError('Prepared output-recovery input changed: '+name)
    if (source_manifest['manifest_id'] != manifest['source']['source_manifest_id'] or
            dft.sha(source_directory/MANIFEST) != manifest['source']['source_manifest_sha256']):
        raise ValueError('Source Wannier manifest changed after recovery preparation.')
    current=source_recovery_context(source,manifest['source']['source_job']['job_id'])
    for name,sealed in manifest['source']['source_files'].items():
        observed=current['source_files'][name]
        for field in ('sha256','size','device','inode','mtime_ns'):
            if observed[field] != sealed[field]:
                raise ValueError('Sealed output-recovery source changed: '+name)
    if current['source_disentanglement'] != manifest['source']['source_disentanglement']:
        raise ValueError('Source DIS evidence changed after recovery preparation.')
    for name,row in manifest.get('input_materialization',{}).items():
        actual=(directory/name).stat()
        if dft.sha(directory/name) != row['destination']['sha256']:
            raise ValueError('Materialized recovery input hash changed: '+name)
        if row['method']=='hardlink' and (actual.st_dev,actual.st_ino) != (row['source']['device'],row['source']['inode']):
            raise ValueError('Materialized hard-link identity changed: '+name)
        if row['method'] not in ('hardlink','copy_fallback'):
            raise ValueError('Unknown recovery input materialization method: '+name)
    return directory,manifest


def ordinary_completion(source):
    try:
        report=inspect_regular(source)
    except (ValueError,OSError,KeyError,TypeError):
        return None
    if not report.get('execution_complete'):
        return None
    record=dft.read_json(interface.require_file(Path(source).resolve()/RECORD))
    return dict(report,output_hashes=record['output_hashes'],manifest_id=record['manifest_id'],
                source_run_record_sha256=dft.sha(Path(source).resolve()/RECORD))


def inspect_regular(directory, returncode=None):
    directory, manifest = validate_inputs(directory)
    if returncode is None:
        run_record = dft.read_json(interface.require_file(directory/RECORD))
        if run_record.get('manifest_id') != manifest['manifest_id']:
            raise ValueError('Execution record does not identify this manifest.')
        returncode = run_record['returncode']
        current_outputs = {name for name in OUTPUTS if (directory/name).is_file() and (directory/name).stat().st_size}
        if current_outputs != set(run_record.get('output_hashes',{})):
            raise ValueError('Recorded Wannier output inventory changed after execution.')
        for name, digest in run_record.get('output_hashes',{}).items():
            if Path(name).name != name or dft.sha(interface.require_file(directory/name)) != digest:
                raise ValueError('Recorded Wannier output changed: '+name)
    problems = []
    if returncode != 0:
        problems.append('Wannier executable returned nonzero status.')
    try:
        dis = read_wout(directory/(SEED+'.wout'), manifest['controls']['dis_num_iter'],
                        manifest['dimensions']['num_bands'] > manifest['dimensions']['num_wann'])
    except (ValueError, OSError) as error:
        dis = dict(state='unconfirmed',converged=False,iteration_limit_reached=False,iterations=None,
                   normal_completion_marker=False)
        problems.append(str(error))
    if not dis['normal_completion_marker']:
        problems.append('Wout lacks normal completion evidence.')
    if dis['state'] in ('unconfirmed','inconsistent_output'):
        problems.append('Disentanglement state is not confirmed by the actual iteration log.')
    artifacts = {}
    for name in REQUIRED_PRODUCTS:
        artifacts[name] = (directory/name).is_file() and (directory/name).stat().st_size > 0
        if not artifacts[name]:
            problems.append('Missing nonempty output: '+name)
    if (directory/(SEED+'.werr')).is_file() and (directory/(SEED+'.werr')).stat().st_size:
        problems.append('Wannier error file is nonempty.')
    complete = not problems
    provisional = complete and dis['state']=='iteration_limit_reached'
    status = ('completed_iteration_limit' if provisional else 'completed_converged' if complete and dis['converged']
              else 'completed_no_disentanglement_required' if complete else 'execution_failed')
    return dict(status=status, execution_complete=complete, disentanglement=dis, artifacts=artifacts,
                convergence_controls={key:manifest['controls'].get(key) for key in ('dis_conv_tol','dis_conv_window')},
                ready_for_fit=complete, provisional_fit_ready=provisional,
                fit_status='provisional_unconverged' if provisional else 'not_evaluated', fit_pass=None, accepted=False,
                num_iter=0, wannier_localization_executed=False, problems=problems,
                scope='Execution and disentanglement status only; interpolation accuracy and scientific acceptance require separate review.')


def ensure_recovery_fresh(directory):
    artifacts=[RECORD,*RECOVERY_GENERATED_OUTPUTS]
    if any((directory/name).exists() or (directory/name).is_symlink() for name in artifacts):
        raise ValueError('Output-recovery execution artifacts already exist; use a new technical directory.')


def source_job_active(job_id):
    """Fail closed if Slurm still lists the source job in any active state."""
    process=subprocess.run(['squeue','--noheader','--jobs',str(job_id),'--format=%T'],
                           capture_output=True,text=True,check=False)
    if process.returncode:
        raise ValueError('Cannot verify that the source Slurm job has ended: '+process.stderr.strip())
    return bool(process.stdout.strip())


def inspect_output_recovery(directory, returncode=None):
    directory,manifest=read_recovery_manifest(directory)
    if returncode is None:
        record=dft.read_json(interface.require_file(directory/RECORD))
        if (record.get('manifest_id') != manifest['manifest_id'] or
                record.get('technical_output_recovery') is not True or
                record.get('scientific_proposal_changed') is not False):
            raise ValueError('Output-recovery record does not identify this technical manifest.')
        if record.get('status')=='no_op_source_already_complete':
            source_report=ordinary_completion(manifest['source']['source_path'])
            if source_report is None or source_report.get('output_hashes',{}) != record.get('source_certification',{}).get('output_hashes',{}):
                raise ValueError('No-op recovery source is no longer an ordinarily authenticated complete run.')
            return dict(record,source_execution=source_report)
    directory,manifest=validate_output_recovery_inputs(directory)
    if returncode is None:
        returncode=record.get('returncode')
        current_outputs={name for name in OUTPUTS if (directory/name).is_file() and (directory/name).stat().st_size}
        if current_outputs != set(record.get('output_hashes',{})):
            raise ValueError('Recorded output-recovery inventory changed after execution.')
        for name,digest in record.get('output_hashes',{}).items():
            if Path(name).name != name or dft.sha(interface.require_file(directory/name)) != digest:
                raise ValueError('Recorded output-recovery file changed: '+name)
    problems=[]
    if returncode != 0:
        problems.append('Wannier restart=plot executable returned nonzero status.')
    try:
        text=interface.require_file(directory/(SEED+'.wout')).read_text(errors='replace')
    except (ValueError,OSError) as error:
        text=''; problems.append(str(error))
    normal=(text.count('All done: wannier90 exiting')==1 and
            text.rstrip().endswith('All done: wannier90 exiting'))
    restart_plot=bool(re.search(r'Restarting\s+Wannier90\s+from\s+plotting\s+routines',text,re.I))
    if not normal:
        problems.append('Recovery WOUT lacks one terminal All done marker.')
    if not restart_plot:
        problems.append('Recovery WOUT lacks restart-from-plotting evidence.')
    if re.search(r'<--\s*DIS\s*$',text,re.M|re.I) or re.search(r'Disentanglement convergence criteria',text,re.I):
        problems.append('Recovery WOUT unexpectedly contains a new DIS history.')
    artifacts={}
    for name in REQUIRED_PRODUCTS:
        artifacts[name]=(directory/name).is_file() and (directory/name).stat().st_size>0
        if not artifacts[name]:
            problems.append('Missing nonempty recovered output: '+name)
    if (directory/(SEED+'.werr')).is_file() and (directory/(SEED+'.werr')).stat().st_size:
        problems.append('Wannier recovery error file is nonempty.')
    complete=not problems
    dis=manifest['source']['source_disentanglement']
    provisional=complete and dis['state']=='iteration_limit_reached'
    status=('completed_output_recovery_iteration_limit' if provisional else
            'completed_output_recovery_converged_source' if complete and dis['state']=='converged' else
            'completed_output_recovery_no_disentanglement_source' if complete and dis['state']=='not_required' else
            'output_recovery_failed')
    return dict(status=status,execution_complete=complete,ready_for_fit=complete,
        provisional_fit_ready=provisional,fit_status='provisional_unconverged' if provisional else 'not_evaluated',
        fit_pass=None,accepted=False,technical_output_recovery=True,scientific_proposal_changed=False,
        restart='plot',new_disentanglement_executed=False,wannier_localization_executed=False,num_iter=0,
        disentanglement=dis,source_disentanglement=dis,source_context=manifest['source'],
        recovery_wout=dict(path=str((directory/(SEED+'.wout')).resolve()),
                           sha256=dft.sha(directory/(SEED+'.wout')) if text else None,
                           normal_completion_marker=normal,restart_plot_marker=restart_plot),
        artifacts=artifacts,problems=problems,
        scope='Recovered technical files only; DIS status comes exclusively from the sealed source WOUT and acceptance remains false.')


def inspect(directory, returncode=None):
    directory=Path(directory).resolve()
    manifest=dft.read_json(interface.require_file(directory/MANIFEST))
    if manifest.get('format')==RECOVERY_FORMAT:
        return inspect_output_recovery(directory,returncode)
    return inspect_regular(directory,returncode)


def run_output_recovery(directory, profile, ntasks=1):
    directory,manifest=read_recovery_manifest(directory)
    source_job=manifest['source']['source_job']['job_id']
    ensure_recovery_fresh(directory)
    if source_job_active(source_job):
        raise ValueError('Source Slurm job is still active; output recovery refuses concurrent source access.')
    source_report=ordinary_completion(manifest['source']['source_path'])
    if source_report is not None:
        report=dict(status='no_op_source_already_complete',execution_complete=True,ready_for_fit=False,
            source_ready_for_fit=True,no_op=True,technical_output_recovery=True,scientific_proposal_changed=False,
            accepted=False,fit_pass=None,manifest_id=manifest['manifest_id'],source_job=manifest['source']['source_job'],
            source_context=manifest['source'],
            source_certification=dict(path=manifest['source']['source_path'],manifest_id=manifest['source']['source_manifest_id'],
                                      output_hashes=source_report.get('output_hashes',{})),
            scope='No recovery executed because the ordinary source workflow became fully authenticated before dependency start.')
        dft.write_json(directory/RECORD,report)
        return report
    directory,manifest=validate_output_recovery_inputs(directory)
    site,parallelism=resources(profile,ntasks,allocation=True)
    lock=directory/'.running'
    with lock.open('x') as stream:
        stream.write(str(os.getpid()))
    started=utc_now();timer=time.monotonic();code=None;execution_error=None
    try:
        try:
            with (directory/'stdout.log').open('x') as stdout,(directory/'stderr.log').open('x') as stderr:
                code=subprocess.run(parallelism['command'],cwd=directory,stdout=stdout,stderr=stderr,check=False).returncode
        except OSError as error:
            execution_error=str(error)
        elapsed=time.monotonic()-timer
        try:
            report=inspect_output_recovery(directory,returncode=code if code is not None else -1)
        except (ValueError,OSError,KeyError,TypeError) as error:
            report=dict(status='output_recovery_failed',execution_complete=False,ready_for_fit=False,
                provisional_fit_ready=False,accepted=False,fit_pass=None,problems=[str(error)],
                technical_output_recovery=True,scientific_proposal_changed=False,
                source_disentanglement=manifest['source']['source_disentanglement'])
        report.update(returncode=code,manifest_id=manifest['manifest_id'],site_sha256=dft.sha(profile),
            slurm_job_id=os.environ['SLURM_JOB_ID'],source_job=manifest['source']['source_job'],parallelism=parallelism,
            started_utc=started,finished_utc=utc_now(),wall_seconds=elapsed,input_hashes=manifest['input_hashes'])
        if execution_error:
            report['execution_error']=execution_error
        report['output_hashes']={name:dft.sha(directory/name) for name in OUTPUTS
            if (directory/name).is_file() and (directory/name).stat().st_size}
        report['required_product_hashes']={name:report['output_hashes'][name] for name in REQUIRED_PRODUCTS
            if name in report['output_hashes']}
        dft.write_json(directory/RECORD,report)
        return report
    finally:
        lock.unlink()


def submit_output_recovery(directory, profile, ntasks=1, execute=False):
    directory,manifest=read_recovery_manifest(directory)
    if ordinary_completion(manifest['source']['source_path']) is None:
        directory,manifest=validate_output_recovery_inputs(directory)
    source_job=manifest['source']['source_job']['job_id']
    if not re.fullmatch(r'[0-9]+(?:_[0-9]+)?',source_job):
        raise ValueError('Invalid Slurm source job ID for afterany dependency.')
    site,parallelism=resources(profile,ntasks)
    ensure_recovery_fresh(directory)
    lines=['#!/bin/bash',f'#SBATCH --nodes={parallelism["nodes"]}',f'#SBATCH --ntasks={ntasks}',
        '#SBATCH --job-name=wannier-output-recovery',f'#SBATCH --dependency=afterany:{source_job}',
        f'#SBATCH --output={shlex.quote(str(directory/"slurm-%j.out"))}',
        f'#SBATCH --error={shlex.quote(str(directory/"slurm-%j.err"))}']
    for key in ('partition','account','time','exclude'):
        if site.get(key): lines.append(f'#SBATCH --{key}={site[key]}')
    lines += ['set -euo pipefail','ulimit -s unlimited','export PYTHONDONTWRITEBYTECODE=1',
        shlex.join([site['python'],str(Path(__file__).resolve()),'run-output-recovery','--directory',str(directory),
                    '--site',str(Path(profile).resolve()),'--ntasks',str(ntasks)])]
    script='\n'.join(lines)+'\n'
    if not execute:
        return dict(submitted=False,script=script,parallelism=parallelism,dependency='afterany:'+source_job,
                    technical_output_recovery=True,scientific_proposal_changed=False)
    folder=directory/'submissions';folder.mkdir(exist_ok=True)
    path=folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.slurm');path.write_text(script)
    process=subprocess.run(['sbatch','--parsable',str(path)],capture_output=True,text=True,check=False)
    record=dict(returncode=process.returncode,stdout=process.stdout.strip(),stderr=process.stderr.strip(),
        script_sha256=dft.sha(path),site_sha256=dft.sha(profile),manifest_id=manifest['manifest_id'],
        source_job_id=source_job,dependency='afterany:'+source_job,technical_output_recovery=True,
        scientific_proposal_changed=False)
    dft.write_json(path.with_suffix('.json'),record)
    if process.returncode:
        raise ValueError('Slurm output-recovery submission failed; inspect the submission record.')
    return dict(submitted=True,job_id=process.stdout.strip(),script=str(path),dependency='afterany:'+source_job,
                technical_output_recovery=True,scientific_proposal_changed=False,accepted=False)


def run(directory, profile, ntasks=1):
    directory, manifest = validate_inputs(directory)
    require_current_solver_policy(manifest)
    from decision_stages import require_execution_binding
    require_execution_binding(manifest.get('decision_stage'))
    site, parallelism = resources(profile, ntasks, allocation=True)
    ensure_fresh(directory)
    lock = directory/'.running'
    with lock.open('x') as stream:
        stream.write(str(os.getpid()))
    started = utc_now(); timer = time.monotonic(); code = None; execution_error = None
    try:
        try:
            with (directory/'stdout.log').open('x') as stdout, (directory/'stderr.log').open('x') as stderr:
                code = subprocess.run(parallelism['command'],cwd=directory,stdout=stdout,stderr=stderr,check=False).returncode
        except OSError as error:
            execution_error = str(error)
        elapsed = time.monotonic()-timer
        try:
            report = inspect(directory, returncode=code if code is not None else -1)
        except (ValueError, OSError) as error:
            report = dict(status='execution_failed',execution_complete=False,ready_for_fit=False,
                          provisional_fit_ready=False,accepted=False,fit_pass=None,
                          num_iter=0,wannier_localization_executed=False,problems=[str(error)])
        report.update(returncode=code, manifest_id=manifest['manifest_id'], site_sha256=dft.sha(profile),
                      slurm_job_id=os.environ['SLURM_JOB_ID'], parallelism=parallelism,
                      started_utc=started, finished_utc=utc_now(), wall_seconds=elapsed,
                      input_hashes=manifest['input_hashes'])
        if execution_error:
            report['execution_error'] = execution_error
        # Slurm's stdout can still grow after this process prints its result;
        # bind only scientific outputs and our already closed process logs.
        report['output_hashes'] = {name:dft.sha(directory/name) for name in OUTPUTS
            if (directory/name).is_file() and (directory/name).stat().st_size}
        dft.write_json(directory/RECORD,report)
        return report
    finally:
        lock.unlink()


def submit(directory, profile, ntasks=1, execute=False):
    directory, manifest = validate_inputs(directory)
    require_current_solver_policy(manifest)
    from decision_stages import require_execution_binding
    require_execution_binding(manifest.get('decision_stage'))
    site, parallelism = resources(profile, ntasks)
    ensure_fresh(directory)
    lines = ['#!/bin/bash',f'#SBATCH --nodes={parallelism["nodes"]}',f'#SBATCH --ntasks={ntasks}',
             '#SBATCH --job-name=wannier-disentangle',
             f'#SBATCH --output={shlex.quote(str(directory/"slurm-%j.out"))}',
             f'#SBATCH --error={shlex.quote(str(directory/"slurm-%j.err"))}']
    for key in ('partition','account','time','exclude'):
        if site.get(key): lines.append(f'#SBATCH --{key}={site[key]}')
    lines += ['set -euo pipefail','ulimit -s unlimited','export PYTHONDONTWRITEBYTECODE=1',
              shlex.join([site['python'],str(Path(__file__).resolve()),'run','--directory',str(directory),
                          '--site',str(Path(profile).resolve()),'--ntasks',str(ntasks)])]
    script = '\n'.join(lines)+'\n'
    if not execute:
        return dict(submitted=False,script=script,parallelism=parallelism)
    folder = directory/'submissions'; folder.mkdir(exist_ok=True)
    path = folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.slurm'); path.write_text(script)
    process = subprocess.run(['sbatch','--parsable',str(path)],capture_output=True,text=True,check=False)
    record = dict(returncode=process.returncode,stdout=process.stdout.strip(),stderr=process.stderr.strip(),
                  script_sha256=dft.sha(path),site_sha256=dft.sha(profile),manifest_id=manifest['manifest_id'])
    dft.write_json(path.with_suffix('.json'),record)
    if process.returncode:
        raise ValueError('Slurm submission failed; inspect the submission record.')
    return dict(submitted=True,job_id=process.stdout.strip(),script=str(path),accepted=False)


def require_current_solver_policy(manifest):
    controls = manifest['controls']
    interface.validate_dis_budget(controls['dis_num_iter'])
    if controls.get('dis_conv_tol') != interface.DIS_TOL or controls.get('dis_conv_window') != interface.DIS_WINDOW:
        raise ValueError('Prepare a new run under the current explicit convergence policy; sealed old results remain inspectable.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--interface-dir',required=True); p.add_argument('--out-dir',required=True)
    p.add_argument('--dis-num-iter',type=int,help=f'Iteration budget, default/maximum {interface.DIS_MAX}; convergence tolerance explicitly {interface.DIS_TOL}.')
    p.add_argument('--band-dir',help='Explicit verified DFT band source for legacy interfaces without source_directories.band.')
    for name in ('cache','analysis','decision'):
        p.add_argument('--'+name,help='Provide all three to review new windows on the same band-interface matrices.')
    p=commands.add_parser('prepare-output-recovery')
    p.add_argument('--source-dir',required=True);p.add_argument('--out-dir',required=True)
    p.add_argument('--source-job-id',help='Numeric Slurm job ID; must match an existing partial source record when present.')
    for name in ('run','submit','inspect'):
        p = commands.add_parser(name); p.add_argument('--directory',required=True)
        if name != 'inspect':
            p.add_argument('--site',required=True); p.add_argument('--ntasks',type=int,default=1)
        if name == 'submit': p.add_argument('--execute',action='store_true')
    for name in ('run-output-recovery','submit-output-recovery'):
        p=commands.add_parser(name);p.add_argument('--directory',required=True)
        p.add_argument('--site',required=True);p.add_argument('--ntasks',type=int,default=1)
        if name=='submit-output-recovery':p.add_argument('--execute',action='store_true')
    args = parser.parse_args()
    try:
        if args.command=='prepare': result = prepare(args.interface_dir,args.out_dir,args.dis_num_iter,args.band_dir,args.cache,args.analysis,args.decision)
        elif args.command=='prepare-output-recovery': result=prepare_output_recovery(args.source_dir,args.out_dir,args.source_job_id)
        elif args.command=='run': result = run(args.directory,args.site,args.ntasks)
        elif args.command=='submit': result = submit(args.directory,args.site,args.ntasks,args.execute)
        elif args.command=='run-output-recovery': result=run_output_recovery(args.directory,args.site,args.ntasks)
        elif args.command=='submit-output-recovery': result=submit_output_recovery(args.directory,args.site,args.ntasks,args.execute)
        else: result = inspect(args.directory)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))
        return 2 if args.command in ('run','run-output-recovery','inspect') and not result['execution_complete'] else 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2,str(error)+'\n')


if __name__=='__main__':
    raise SystemExit(main())
