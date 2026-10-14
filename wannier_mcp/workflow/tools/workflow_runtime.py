"""Bounded presentation and durable execution of the unchanged scientific CLI."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

TOOLS = Path(__file__).resolve().parent
ALLOWED = {p.name for p in TOOLS.glob('*.py')} - {'workflow_runtime.py'}
INLINE_CHARS = 8000
SCALAR_KEYS = {'status', 'format', 'scope', 'mode', 'num_wann', 'num_iter', 'role',
              'stage_id', 'proposal_id', 'inspection_id', 'manifest_id', 'result_id',
              'ready_for_wannier', 'ready_for_fit', 'usable_outputs', 'preflight_ok',
              'execution_complete', 'provisional', 'provisional_fit_ready', 'submitted',
              'job_id', 'directory', 'output', 'out_dir', 'decision', 'analysis',
              'cache', 'path', 'selection_png', 'selection_pdf', 'index', 'fit_pass',
              'accepted', 'scientific_trial_index', 'selection_proposal_id',
              'current_outer_eV', 'current_frozen_upper_eV', 'min_nouter',
              'min_disentanglement_headroom', 'selected_continuation_concern',
              'parent_result', 'next_action', 'record_consistent', 'gate_pass',
              'sha256', 'history', 'history_sha256', 'config', 'config_sha256',
              'status_file', 'phase', 'scientific_choice_automatically_assigned',
              'interface_files_consistent', 'attempts', 'request_id', 'duplicate',
              'record', 'report', 'requests', 'total_cost', 'currency',
              'cache_sha256', 'review_sha256', 'analysis_id', 'packet',
              'native_wrapper_returncode'}
SMALL_KEYS = {'basis', 'windows', 'windows_relative_eV', 'choices', 'disentanglement',
              'frozen_upper_review', 'frozen_edge_candidate', 'frozen_candidates',
              'near_outer_sample', 'low_conduction_target_scope', 'answer_template',
              'adopted_frozen_negative_screen', 'mesh_gate', 'exclusion_gate',
              'dft_space_assessment', 'expected', 'dimensions', 'wannier_policy',
              'convergence_controls', 'matched_error_metrics', 'counts',
              'precision_regions', 'output_checks', 'changes', 'problems', 'warnings',
              'artifacts', 'outputs', 'checks', 'timing', 'current', 'result',
              'blockers', 'required_fields', 'evidence_requests', 'computed_fields',
              'questions', 'orbital_actions', 'controller', 'solver', 'gate',
              'usage', 'cost_components', 'incomplete_fields', 'numerical_checks',
              'metrics', 'runtime', 'assessment', 'decision_windows', 'display_ranges_eV',
              'trial_basis', 'target_band_ranges_1based', 'reference_band_ranges_1based',
              'frozen_band_ranges_1based', 'band_selection', 'manifold_checks',
              'upper_reference_audit', 'adopted_frozen_upper_review',
              'adopted_frozen_first_unprotected_target', 'frozen', 'outer',
              'validation_context', 'sources', 'error', 'pricing'}
SMALL_KEYS.update({'all_computed_near_outer', 'outside_reference_near_outer'})
SMALL_KEYS.update({'task_scope_review', 'additional_basis_evidence', 'task_scope_checks',
                   'chemical_starting_basis', 'optional_chemical_hints'})
SMALL_KEYS.update({'scope_audit', 'lower_protection', 'scalar_membership', 'upper_origin',
                   'required_review', 'negative_rule', 'automatically_changed_variables'})
SCALAR_KEYS.update({'scientific_approval', 'interface_mesh_gate_pending'})
LARGE_KEYS = {'per_k', 'per_k_counts', 'actual_kpoints', 'kpoints', 'kcoords',
              'energies_absolute_eV', 'band_table', 'script', 'orbital_evidence',
              'retained_indices', 'retained_band_indices_1based', 'retained_bands_1based'}
CRITICAL_KEYS = {'basis', 'windows', 'windows_relative_eV', 'disentanglement',
                 'mesh_gate', 'exclusion_gate', 'adopted_frozen_negative_screen',
                 'numerical_checks', 'band_selection', 'manifold_checks',
                 'target_band_ranges_1based', 'reference_band_ranges_1based',
                 'frozen_band_ranges_1based', 'adopted_frozen_upper_review',
                 'adopted_frozen_first_unprotected_target', 'trial_basis',
                 'upper_reference_audit', 'all_computed_near_outer', 'outside_reference_near_outer',
                 'frozen_upper_review'}
CRITICAL_KEYS.update({'task_scope_review', 'additional_basis_evidence'})
CRITICAL_KEYS.update({'scope_audit', 'lower_protection', 'scalar_membership', 'upper_origin', 'required_review'})
DESCRIPTIVE_KEYS = {'description', 'review_reason', 'scope', 'decision_role', 'reason',
                    'note', 'interpretation', 'details', 'scope_note', 'warnings',
                    'risk_if_omitted', 'risk_if_included', 'objective', 'requested_scope_evidence',
                    'representation_reason', 'origin', 'next_action', 'identity_scope', 'message'}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refuse a symlink output record.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temporary.open('x') as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write('\n')
    os.replace(temporary, path)


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def runtime_directory(workspace, *parts):
    root = Path(workspace).resolve()
    directory = root / '.wannier_runtime'
    for part in ('', *parts):
        directory = directory / part
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            raise ValueError('Runtime directories must stay within the workspace without symlink redirection.')
        directory.mkdir(exist_ok=True)
    return directory


def prose(field, value):
    return field in DESCRIPTIVE_KEYS or (field == 'role' and isinstance(value, str) and ' ' in value)


def compact(value, depth=0, field=None):
    """Retain counts and witnesses; never silently replace a scientific decision."""
    if depth > 7:
        return {'details': 'read the full record'}
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in LARGE_KEYS:
                result[key + '_stored_count'] = len(item) if hasattr(item, '__len__') else None
            else:
                result[key] = compact(item, depth + 1, key)
        return result
    if isinstance(value, list):
        if len(value) <= 6 or (len(value) <= 32 and all(isinstance(x, str) for x in value)
                             and sum(len(x) for x in value) <= 1200):
            return [compact(x, depth + 1, field) for x in value]
        return {'count': len(value), 'first_items': [compact(x, depth + 1, field) for x in value[:3]],
                'remaining': 'read the full record'}
    if isinstance(value, str) and prose(field, value) and len(value) > 700:
        return value[:700] + ' [remaining text in full record]'
    return value


def summarize(parsed):
    if not isinstance(parsed, dict):
        return {}
    result = {k: compact(v, field=k) for k, v in parsed.items() if k in SCALAR_KEYS and not isinstance(v, (dict, list))}
    for key in SMALL_KEYS:
        if key in parsed:
            result[key] = compact(parsed[key], field=key)
    upper = parsed.get('frozen_upper_review')
    if isinstance(upper, dict) and isinstance(upper.get('candidates'), list):
        candidates = upper['candidates']
        vetoed = [row for row in candidates if row.get('negative_veto')]
        first = vetoed[0] if vetoed else None
        screen = first.get('added_state_negative_screen', first.get('negative_screen', {})) if first else {}
        samples = screen.get('negative_samples') or []
        result['frozen_upper_review'] = {
            'criterion_id': upper.get('criterion_id'), 'status': upper.get('status'),
            'target_states_above_current_upper': upper.get('target_states_above_current_upper'),
            'candidate_count': len(candidates), 'negative_veto_count': len(vetoed),
            'first_vetoed_candidate_windows': first.get('windows') if first else None,
            'first_negative_state_count': screen.get('negative_state_count') if first else None,
            'first_counterexample': samples[0] if samples else None,
            'scope': 'Summary of preexisting options; negative-only veto, never an expansion recommendation. All candidates in full log.'}
    return result


def tighten(value, text_limit=120, field=None):
    """Reduce descriptions and collections, preserving pairs and scalar checks.

    Window endpoints, k/band coordinates and band-range pairs are atomic
    values: never shorten them as if they were lists of independent samples.
    """
    if isinstance(value, dict):
        return {k: tighten(v, text_limit, k) for k, v in value.items()}
    if isinstance(value, list):
        atomic = all(isinstance(x, (int, float, bool)) or x is None for x in value)
        pairs = all(isinstance(x, list) and len(x) == 2 and
                    all(isinstance(n, (int, float)) for n in x) for x in value)
        names = all(isinstance(x, str) for x in value)
        if (atomic and len(value) <= 6) or (pairs and len(value) <= 32) or (names and len(value) <= 32):
            return value
        return {'count': len(value), 'first_items': [tighten(x, text_limit, field) for x in value[:1]],
                'remaining': 'read the full record'}
    if isinstance(value, str) and prose(field, value) and len(value) > text_limit:
        return value[:text_limit] + ' [full text in log]'
    return value


def omit_prose(value):
    if isinstance(value, dict):
        return {k: omit_prose(v) for k, v in value.items()
                if not (isinstance(v, str) and prose(k, v))}
    if isinstance(value, list):
        return [omit_prose(v) for v in value]
    return value


def envelope(record, log_path, parsed=None, brief_keys=()):
    result = summarize(parsed)
    if isinstance(parsed, dict):
        for key in brief_keys:
            if key in parsed:
                result[key] = compact(parsed[key])
    ok = record['exit_code'] == 0
    error = record.get('stderr', '')[-1200:]
    status = result.get('status', 'completed' if ok else 'command_failed')
    output = {'ok': ok, 'exit_code': record['exit_code'], 'status': status,
              'log': str(Path(log_path).resolve()), 'log_sha256': sha(log_path),
              'result': result, 'inline_character_budget': INLINE_CHARS,
              'scientific_acceptance': 'not_assigned_by_runtime'}
    if not ok:
        output['error'] = {'kind': 'timeout' if record.get('timeout_seconds') else 'command_rejected_or_failed',
                           'message': error or record.get('stdout', '')[-1200:],
                           'automatic_retry': False,
                           'next_action': 'inspect the error; fix the draft or record a technical restart'}
    if isinstance(parsed, dict):
        output['result_keys'] = list(parsed)[:45]
    elif ok:
        output['stdout_excerpt'] = record.get('stdout', '')[-1200:]
    # Do not replace all useful results with a generic truncation flag.
    if len(json.dumps(output, ensure_ascii=False)) > INLINE_CHARS:
        result = tighten(result)
        output['result'] = result
        output['descriptions_condensed'] = True
        output.pop('result_keys', None)
    while len(json.dumps(output, ensure_ascii=False)) > INLINE_CHARS:
        removable = [k for k in result if k not in SCALAR_KEYS]
        if not removable:
            output.pop('result_keys', None)
            if len(json.dumps(output, ensure_ascii=False)) > INLINE_CHARS:
                raise ValueError('Runtime envelope exceeded its character budget.')
            break
        optional = [k for k in removable if k not in CRITICAL_KEYS]
        if not optional:
            result = tighten(result, text_limit=20)
            output['result'] = result
            if len(json.dumps(output, ensure_ascii=False)) > INLINE_CHARS:
                result = omit_prose(result)
                output['result'] = result
                output['descriptive_fields_in_log'] = True
            if len(json.dumps(output, ensure_ascii=False)) > INLINE_CHARS:
                # Exceptionally large scientific records stay addressable; an
                # incomplete summary must not masquerade as a complete one.
                output['status'] = 'details_required'
                output['details_in_log'] = list(result)
                output['result'] = {k: result[k] for k in ('status', 'num_wann', 'num_iter', 'basis',
                    'windows', 'windows_relative_eV', 'ready_for_wannier', 'ready_for_fit') if k in result}
                output['next_action'] = 'read the required exact JSON fields from the full log before deciding'
                if len(json.dumps(output, ensure_ascii=False)) > INLINE_CHARS:
                    output['details_in_log'] = ['all scientific fields']
                    output['result'] = {'status': status}
            break
        key = max(optional, key=lambda k: len(json.dumps(result[k], ensure_ascii=False)))
        result.pop(key)
        output.setdefault('details_in_log', []).append(key)
    return output


def execute(tool, arguments, workspace, *, python=None, timeout=600, brief_keys=()):
    import sys
    if tool not in ALLOWED or Path(tool).name != tool:
        raise ValueError('Unknown bundled CLI tool.')
    workspace = Path(workspace).resolve()
    directory = runtime_directory(workspace, 'logs')
    log = directory / (tool[:-3] + '-' + uuid.uuid4().hex + '.json')
    argv = [python or sys.executable, '-B', str(TOOLS / tool), *map(str, arguments)]
    cache = runtime_directory(workspace, 'cache')
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', MPLBACKEND='Agg',
                       XDG_CACHE_HOME=str(cache), MPLCONFIGDIR=str(cache / 'matplotlib'))
    (cache / 'matplotlib').mkdir(parents=True, exist_ok=True)
    started = now()
    clock = time.monotonic()
    try:
        process = subprocess.run(argv, cwd=workspace, env=environment, text=True,
                                 capture_output=True, timeout=timeout, check=False)
        record = dict(exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
    except subprocess.TimeoutExpired as error:
        decode = lambda x: x.decode(errors='replace') if isinstance(x, bytes) else (x or '')
        record = dict(exit_code=None, stdout=decode(error.stdout), stderr=decode(error.stderr), timeout_seconds=timeout)
    except OSError as error:
        record = dict(exit_code=None, stdout='', stderr=str(error))
    record.update(argv=argv, started_utc=started, finished_utc=now(),
                  tool_wall_seconds=time.monotonic() - clock, tool_sha256=sha(TOOLS / tool))
    try:
        parsed = json.loads(record['stdout'])
    except (json.JSONDecodeError, TypeError):
        parsed = None
    atomic_json(log, record)
    return envelope(record, log, parsed, brief_keys), parsed


def parse_job_id(value):
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:;[A-Za-z0-9_.-]+)?', value.strip()):
        raise ValueError('Unrecognized sbatch identity; do not resubmit an uncertain submission.')
    return value.strip().split(';')[0]
