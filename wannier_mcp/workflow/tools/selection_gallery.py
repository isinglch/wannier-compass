#!/usr/bin/env python3
"""Review-only gallery for sealed material stages or explicit selection-only cases.

The gallery links existing figures; it never infers scientific acceptance, changes
a selection, or reads outside the explicitly supplied experiment root.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from orbital_evidence import digest

MARKER = '<!-- managed: selection_gallery v1 -->\n'
STAGES = ('initial', 'real_first_try')


def read(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def child(path, root):
    """No gallery input may reach outside the named experiment root."""
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Gallery input is a symlink or outside the experiment root: ' + str(path))
    return path


def required_file(path, root):
    child(path, root)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Missing or empty gallery artifact: ' + str(path))
    return path


def stage_artifacts(stage, root):
    files = ('selection.json', 'review.json', 'selection.png', 'selection.pdf')
    return {name: required_file(stage / name, root) for name in files}


def reviewed_selection(stage, root, sealed):
    files = stage_artifacts(stage, root)
    record = read(files['selection.json'])
    review = read(files['review.json'])
    if record.get('format') != 'wannier-selection-proposal-v1':
        raise ValueError('Gallery requires a formal selection proposal: ' + str(stage))
    if digest({k: v for k, v in record.items() if k != 'proposal_id'}) != record.get('proposal_id'):
        raise ValueError('Selection identity mismatch: ' + str(stage))
    if digest(review) != record.get('review_sha256'):
        raise ValueError('Selection and review disagree: ' + str(stage))
    figure_binding = 'unsealed_without_figure_manifest'
    figure_manifest = stage / 'selection_plot_manifest.json'
    render_manifest = stage / 'render_manifest.json'
    if figure_manifest.exists():
        manifest = read(required_file(figure_manifest, root))
        if (manifest.get('format') != 'wannier-selection-figure-v1' or
                manifest.get('proposal_id') != record['proposal_id'] or
                manifest.get('selection_sha256') != sha(files['selection.json']) or
                any(manifest.get('figures', {}).get(name) != sha(files[name])
                    for name in ('selection.png', 'selection.pdf'))):
            raise ValueError('Selection figure manifest mismatch: ' + str(stage))
        figure_binding = 'proposal_figure_hashes_verified'
    elif render_manifest.exists():
        manifest = read(required_file(render_manifest, root))
        if (manifest.get('format') != 'wannier-selection-render-v1' or
                manifest.get('source_selection', {}).get('sha256') != sha(files['selection.json']) or
                any(manifest.get('outputs', {}).get(name, {}).get('sha256') != sha(files[name])
                    for name in ('selection.png', 'selection.pdf'))):
            raise ValueError('Rendered figure manifest mismatch: ' + str(stage))
        figure_binding = 'rendered_figure_hashes_verified'
    if sealed:
        manifest_path = required_file(stage / 'STAGE.json', root)
        manifest = read(manifest_path)
        if digest({k: v for k, v in manifest.items() if k != 'stage_id'}) != manifest.get('stage_id'):
            raise ValueError('Stage identity mismatch: ' + str(stage))
        for name in files:
            if manifest.get('files', {}).get(name) != sha(files[name]):
                raise ValueError('Stage figure/selection hash mismatch: ' + str(stage / name))
        role = manifest['role']
        reason = manifest['reason']
        figure_binding = 'sealed_stage_figure_hashes_verified'
    else:
        role = stage.name
        reason = review.get('frozen_reason', '')
    return dict(path=stage, role=role, reason=reason,
                status=record['status'], basis=record['basis'], num_wann=record['num_wann'],
                windows=record['windows_relative_eV'],
                excluded=record['band_selection']['exclude_bands_1based'],
                negative_count=record['adopted_frozen_negative_screen']['negative_state_count'],
                hashes={name: sha(path) for name, path in files.items()},
                sealed=sealed, figure_binding=figure_binding)


def managed_cases(root, experiment_id):
    entries, warnings = [], []
    for case in sorted(root.iterdir()):
        if not case.is_dir() or case.name == 'selection_gallery':
            continue
        if case.is_symlink():
            warnings.append('Skipped symlink case: ' + case.name)
            continue
        marker = case / 'MATERIAL-LAYOUT.json'
        if not marker.is_file() or marker.is_symlink():
            continue
        base = case / '03_wan_trial'
        initial = base / 'initial'
        if not (initial / 'STAGE.json').is_file():
            continue
        try:
            context = read(required_file(initial / 'context.json', root))
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
            warnings.append(case.name + '/initial: ' + str(error))
            continue
        if experiment_id is not None and context.get('experiment_id') != experiment_id:
            continue
        stages = [initial] + sorted(base.glob('trial_*/records/selection'))
        for stage in stages:
            try:
                child(stage, root)
                stage_context = read(required_file(stage / 'context.json', root))
                if stage_context.get('experiment_id') != context.get('experiment_id'):
                    raise ValueError('Stage experiment differs from its initial baseline.')
                item = reviewed_selection(stage, root, sealed=True)
                item['case'] = case.name
                for name in ('dft_bands', 'projected_bands'):
                    figure = case / '02_band' / 'plots' / (name + '.png')
                    if figure.is_file() and not figure.is_symlink():
                        item[name] = required_file(figure, root)
                entries.append(item)
            except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
                warnings.append(case.name + '/' + stage.relative_to(base).as_posix() + ': ' + str(error))
    return entries, warnings


def selection_only_cases(root):
    entries, warnings = [], []
    for case in sorted(root.iterdir()):
        if not case.is_dir() or case.name == 'selection_gallery':
            continue
        if case.is_symlink():
            warnings.append('Skipped symlink case: ' + case.name)
            continue
        for stage in sorted(case.iterdir()):
            if not stage.is_dir() or not (stage.name in STAGES or re.fullmatch(r'retry_[0-9]{3,}', stage.name)):
                continue
            try:
                child(stage, root)
                item = reviewed_selection(stage, root, sealed=False)
                item['case'] = case.name
                if item['figure_binding'] == 'unsealed_without_figure_manifest':
                    warnings.append(case.name + '/' + stage.name +
                                    ': Figure manifest absent; this legacy image is not hash-bound to its selection.')
                for name in ('dft_bands', 'projected_bands'):
                    figure = case / 'review_packet' / 'plots' / (name + '.png')
                    if figure.is_file() and not figure.is_symlink():
                        item[name] = required_file(figure, root)
                entries.append(item)
            except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
                warnings.append(case.name + '/' + stage.name + ': ' + str(error))
    return entries, warnings


def link(path, output):
    relative = os.path.relpath(path, output.parent).replace(os.sep, '/')
    return quote(relative, safe='/.-_')


def page(entries, warnings, output, mode, experiment_id):
    cards = []
    for item in entries:
        path = item['path']
        title = html.escape(item['case'] + ' · ' + item['role'])
        details = dict(basis=item['basis'], num_wann=item['num_wann'],
                       exclude_bands_1based=item['excluded'],
                       dis_eV=item['windows']['dis_eV'],
                       frozen_eV=item['windows']['frozen_eV'],
                       status=item['status'], frozen_counterexamples=item['negative_count'],
                       sealed_stage=item['sealed'], figure_binding=item['figure_binding'])
        image = link(path / 'selection.png', output)
        links = ' · '.join('<a href="' + link(path / name, output) + '">' + name + '</a>'
                         for name in ('selection.png', 'selection.pdf', 'selection.json', 'review.json'))
        extras = ' · '.join('<a href="' + link(item[name], output) + '">' + name + '</a>'
                          for name in ('dft_bands', 'projected_bands') if name in item)
        cards.append('<article><h2>' + title + '</h2><p>' + html.escape(item['reason']) + '</p>'
                     '<pre>' + html.escape(json.dumps(details, ensure_ascii=False, indent=2)) + '</pre>'
                     '<a href="' + image + '"><img src="' + image + '" alt="Selection-only figure"></a>'
                     '<p>' + links + (' · ' + extras if extras else '') + '</p></article>')
    problems = ''.join('<li>' + html.escape(warning) + '</li>' for warning in warnings)
    note = ('Only explicitly sealed material stages are shown.' if mode == 'managed' else
            'Selection-only records are identity-checked but not sealed execution stages.')
    return (MARKER + '<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<title>Wannier selection gallery</title><style>'
            'body{font:16px system-ui;background:#f4f7fa;color:#172b39;margin:2rem auto;max-width:1250px}'
            'article{background:white;padding:1.25rem;margin:1rem 0;border-radius:12px;box-shadow:0 2px 10px #0001}'
            'img{width:100%;height:auto}a{color:#075985}pre{white-space:pre-wrap}li{margin:.4rem 0}'
            '</style><h1>Wannier selection gallery</h1><p>' + html.escape(note) +
            ' Figures and metrics support selection review; they do not certify interface mesh, disentanglement, or fit.</p>' +
            ('<p>Experiment: ' + html.escape(experiment_id) + '</p>' if experiment_id else '') +
            ('<h2>Record issues requiring review</h2><ul>' + problems + '</ul>' if warnings else '') +
            ''.join(cards) + '</html>')


def atomic_text(path, value):
    fd, name = tempfile.mkstemp(prefix='.selection-gallery-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def render(root, mode='managed', experiment_id=None):
    root = Path(root).resolve()
    if not root.is_dir() or mode not in ('managed', 'selection-only'):
        raise ValueError('Provide an existing experiment root and a known gallery mode.')
    entries, warnings = (managed_cases(root, experiment_id) if mode == 'managed' else selection_only_cases(root))
    if not entries and not warnings:
        raise ValueError('No selection stages found directly under the supplied experiment root.')
    output_dir = root / 'selection_gallery'
    if output_dir.is_symlink():
        raise ValueError('Gallery output directory must not be a symlink.')
    output_dir.mkdir(exist_ok=True)
    output = output_dir / 'index.html'
    manifest_path = output_dir / 'gallery_manifest.json'
    if (output.is_symlink() or (output.exists() and not output.read_text().startswith(MARKER)) or
            manifest_path.is_symlink() or (manifest_path.exists() and read(manifest_path).get('format') != 'wannier-selection-gallery-v1')):
        raise ValueError('Refuse replacing an unmanaged selection gallery.')
    content = page(entries, warnings, output, mode, experiment_id)
    manifest = dict(format='wannier-selection-gallery-v1', created_utc=datetime.now(timezone.utc).isoformat(),
                    mode=mode, experiment_id=experiment_id, root=str(root),
                    entries=[dict(case=item['case'], role=item['role'], status=item['status'],
                                  stage=str(item['path']), hashes=item['hashes'],
                                  figure_binding=item['figure_binding']) for item in entries],
                    warnings=warnings, html_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    scope='Selection visualization only; no historical answer, interface or fit approval.')
    atomic_text(output, content)
    atomic_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return dict(index=str(output), manifest=str(manifest_path), entries=len(entries), warnings=warnings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Explicit experiment root; only its immediate case directories are scanned.')
    parser.add_argument('--mode', choices=('managed', 'selection-only'), default='managed')
    parser.add_argument('--experiment-id', help='For managed mode, include only cases with this initial context experiment_id.')
    args = parser.parse_args()
    try:
        result = render(args.root, args.mode, args.experiment_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
