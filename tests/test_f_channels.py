"""Invented resolved-f format/decision regressions, not material-fit evidence."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / 'wannier_mcp/workflow/tools'
sys.path.insert(0, str(TOOLS))
import basis_scope
import manifold_selection as selection
import orbital_evidence
from orbital_channels import (SPD_COLUMNS, SPDF_COLUMNS, basis_dimension,
                              procar_columns, resolved_shells)
from vasp_to_cache import read_procar
from valence_selection import build_selection
import test_basis_scope as fixture_module


def printed_procar(columns=SPDF_COLUMNS, spinor=False, repeat_header=False,
                   omit_total=False, second_columns=None):
    text = ['PROCAR lm decomposed', '# of k-points: 1 # of bands: 2 # of ions: 1',
            'k-point 1 : 0 0 0 weight = 1']
    for band in range(2):
        text.append(f'band {band+1} # energy {2*band-1} # occ. {0 if band else 1}')
        cols = columns if band == 0 or second_columns is None else second_columns
        for table in range(4 if spinor else 1):
            if table == 0 or repeat_header:
                text.append('ion ' + ' '.join(cols) + ' tot')
            values = np.arange(1, len(cols)+1, dtype=float) / 1000
            if table:
                values *= -table  # signed magnetization is not charge
            row = ' '.join(f'{v:.3f}' for v in np.r_[values, values.sum()])
            text.append('1 ' + row)
            if not omit_total:
                text.append('tot ' + row)
    return '\n'.join(text) + '\n'


class ResolvedFChannels(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def parse(self, **kwargs):
        path = self.root/'PROCAR'
        path.write_text(printed_procar(**kwargs))
        return read_procar(path, 1, 2, 1)

    def test_scalar_f_keeps_every_raw_component_and_printed_total(self):
        result = self.parse()
        self.assertEqual(result['columns'], list(SPDF_COLUMNS))
        self.assertEqual(result['raw'].shape, (1, 2, 1, 16))
        np.testing.assert_array_equal(result['raw'][0, 0, 0], np.arange(1, 17)/1000)
        np.testing.assert_allclose(result['raw'].sum(axis=(-1, -2)), result['printed_total'])
        self.assertEqual(result['spin_mode'], 'scalar')

    def test_spinor_f_with_signed_magnetization_and_optional_repeated_headers(self):
        for repeat in (False, True):
            with self.subTest(repeat=repeat):
                result = self.parse(spinor=True, repeat_header=repeat)
                self.assertEqual(result['spin_mode'], 'spinor')
                np.testing.assert_array_equal(result['raw'][0, 0, 0], np.arange(1, 17)/1000)

    def test_single_ion_f_without_redundant_total(self):
        result = self.parse(omit_total=True)
        self.assertEqual(result['inferred_single_ion_tot_rows'], 2)
        np.testing.assert_allclose(result['printed_total'], .136)

    def test_partial_unknown_coarse_duplicate_and_reordered_layouts_rejected(self):
        for cols in (SPDF_COLUMNS[:-1], SPD_COLUMNS+('f',),
                     SPDF_COLUMNS[:-1]+('f_unknown',), SPDF_COLUMNS[:-1]+('fz3',),
                     ('s', 'p', 'd', 'f'), tuple(reversed(SPDF_COLUMNS))):
            with self.subTest(columns=cols), self.assertRaises(ValueError):
                self.parse(columns=cols)

    def test_layout_change_between_states_and_truncation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'layout changed'):
            self.parse(second_columns=SPD_COLUMNS)
        text = printed_procar().replace('1 0.001 0.002', '1 0.001', 1)
        path = self.root/'PROCAR'; path.write_text(text)
        with self.assertRaisesRegex(ValueError, 'ion rows'):
            read_procar(path, 1, 2, 1)

    def test_existing_spd_layout_and_aliases_are_preserved_without_fake_f(self):
        result = self.parse(columns=SPD_COLUMNS)
        self.assertEqual(result['raw'].shape[-1], 9)
        self.assertNotIn('f', resolved_shells(result['columns']))
        for alias in ('dx2', 'dx2-y2'):
            cols = list(SPDF_COLUMNS); cols[8] = alias
            self.assertEqual(procar_columns(cols), list(SPDF_COLUMNS))

    def test_cache_groups_all_f_components_and_ions_with_permuted_columns(self):
        columns = list(reversed(SPDF_COLUMNS))
        raw = np.arange(1, 65, dtype=float).reshape(1, 1, 1, 4, 16)/1000
        metadata = {'columns': columns, 'species': ['Ce', 'Ce', 'O'], 'counts': [1, 2, 1],
                    'ef': .5, 'procar_sha256': 'a'*64, 'ef_source': 'invented'}
        cache = self.root/'charge.npz'
        np.savez(cache, metadata=json.dumps(metadata), raw=raw, energies=np.array([[[1.5]]]))
        _, e, groups, _ = orbital_evidence.read_cache(cache)
        ids = [columns.index(c) for c in SPDF_COLUMNS[-7:]]
        self.assertAlmostEqual(groups['Ce:f'][0, 0], raw[0, 0, 0, :3][:, ids].sum())
        np.testing.assert_allclose(sum(groups.values()), raw[0].sum(axis=(-1, -2)))
        np.testing.assert_array_equal(e, [[1.]])
        metadata['columns'] = list(SPD_COLUMNS)
        np.savez(cache, metadata=json.dumps(metadata), raw=raw, energies=np.array([[[1.5]]]))
        with self.assertRaisesRegex(ValueError, 'orbital axis'):
            orbital_evidence.read_cache(cache)

    def test_f_dimension_and_inspection_prior_are_not_automatic_selection(self):
        self.assertEqual(basis_dimension(['Ce:f'], ['Ce'], [2], 'scalar'), 14)
        self.assertEqual(basis_dimension(['Ce:f'], ['Ce'], [2], 'spinor'), 28)
        self.assertEqual(basis_dimension(['Ce:f', 'O:p'], ['Ce', 'O'], [2, 3], 'spinor'), 46)
        self.assertEqual(basis_scope.starting_basis({'Ce:s': None, 'Ce:f': None, 'O:p': None}), ['Ce:f', 'O:p'])
        self.assertEqual(basis_scope.starting_basis({'Ce:s': None}), [])

    def test_omitted_f_can_veto_protection_but_does_not_change_basis_or_outer(self):
        e = np.array([[-1., 0., 1.], [-.9, .1, 1.1]])
        groups = {'O:p': np.full(e.shape, .6), 'Ce:f': np.zeros(e.shape)}
        groups['Ce:f'][1, 1] = .7  # non-Gamma state is also checked
        basis = ['O:p']; frozen = [-1., .5]
        result = selection.frozen_state_counterexample_screen(e, groups, basis, [0, 1, 2],
            frozen, selection.POLICY['selection_parameters'])
        self.assertEqual(result['negative_state_count'], 1)
        self.assertEqual(result['negative_samples'][0]['k_band_1based'], [2, 2])
        self.assertEqual(basis, ['O:p'])
        self.assertEqual(frozen, [-1., .5])

    def test_complete_f_exclusion_uses_seven_or_fourteen_not_five(self):
        for spin, width in (('scalar', 7), ('spinor', 14)):
            e = np.array([np.r_[np.linspace(-10, -8, width), -2., 0., 2.]])
            occ = np.full(e.shape, 2. if spin == 'scalar' else 1.)
            groups = {'Ce:f': np.ones(e.shape), 'Ce:d': np.zeros(e.shape)}
            block = {'band_range_1based': [1, width], 'orbital_groups': ['Ce:f'],
                     'reason': 'Invented fully occupied non-target f prefix.'}
            result, gate = build_selection(e, occ, {'species': ['Ce'], 'counts': [1], 'spin_mode': spin},
                groups, ['Ce:d'], [block], .1, .001)
            self.assertEqual(result['exclude_bands_1based'], list(range(1, width+1)))
            self.assertTrue(gate['gate_pass'])

    def test_official_conversion_records_real_f_axis_and_alignment(self):
        (self.root/'POSCAR').write_text('Invented only\n1\n1 0 0\n0 1 0\n0 0 1\nCe\n1\nDirect\n0 0 0\n')
        (self.root/'EIGENVAL').write_text('1 1 1 1\nh\nh\nh\nh\n1 1 2\n0 0 0 1\n1 -1 1\n2 1 0\n')
        (self.root/'PROCAR').write_text(printed_procar(spinor=True))
        reference = self.root/'source.txt'; reference.write_text('Invented source only')
        (self.root/'ef.json').write_text(json.dumps({'kind': 'reviewed_scf_reference',
            'ef_eV': 0., 'source_path': str(reference),
            'source_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
            'spin_mode': 'spinor', 'reason': 'Invented parser regression only'}))
        p = subprocess.run([sys.executable, '-B', str(TOOLS/'vasp_to_cache.py'),
            '--poscar', str(self.root/'POSCAR'), '--eigenval', str(self.root/'EIGENVAL'),
            '--procar', str(self.root/'PROCAR'), '--ef-json', str(self.root/'ef.json'),
            '--out', str(self.root/'charge.npz')], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        meta, _, groups, _ = orbital_evidence.read_cache(self.root/'charge.npz')
        self.assertEqual(meta['columns'], list(SPDF_COLUMNS))
        self.assertLess(meta['max_total_rounding_difference'], 1e-15)
        self.assertAlmostEqual(groups['Ce:f'][0, 0], .091)


class FProposalContracts(unittest.TestCase):
    def setUp(self):
        # Reuse an invented task-scope builder without importing its test class
        # into discovery or duplicating its tests.
        self.fixture = fixture_module.TaskFirstSelection(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def configure(self, spin):
        f = self.fixture
        width = 14 if spin == 'spinor' else 7
        row = np.r_[-4., np.linspace(-2., 1.5, width), np.arange(3., 20.)]
        f.energies = np.stack([row, row+.05, row+.1])
        f.raw = np.zeros((1, 3, len(row), 1, 16))
        f.raw[0, :, 0, 0, 0] = .8
        f.raw[0, :, 1:width+1, 0, 9:] = .1  # whole-shell raw weight .7
        f.raw[0, :, width+1:, 0, 9:] = .001
        f.meta.update(species=['Ce'], columns=list(SPDF_COLUMNS), spin_mode=spin)
        f.save_cache()
        if spin == 'spinor':
            with np.load(f.cache) as z:
                payload = {k: np.array(z[k]) for k in z.files}
            payload['occupations'] = np.where(payload['occupations'] > 0, 1., 0.)
            np.savez(f.cache, **payload)
        f.inspection = selection.inspect(f.cache)
        r = copy.deepcopy(f.review)
        r['inspection_id'] = f.inspection['inspection_id']
        r['orbital_decisions'] = [f.row('Ce:'+shell,
            'include' if shell == 'f' else 'omit_first_trial',
            'task_component' if shell == 'f' else 'deep_only' if shell == 's' else 'no_task_role')
            for shell in ('s', 'p', 'd', 'f')]
        r['task_scope_review']['deep_blocks'][0]['orbital_groups'] = ['Ce:s']
        r['task_scope_review']['task_anchor_band_ranges_1based'] = [[2, width+1]]
        r['target_band_ranges_1based'] = [[2, width+1]]
        r['reference_band_ranges_1based'] = [[2, width+5]]
        r['frozen_band_ranges_1based'] = [[2, width+1]]
        r['window_override'] = {'dis_eV': [-2.5, 8.], 'frozen_eV': [-1.95, 1.65],
            'reason': 'Invented independently reviewed candidate for format testing only.'}
        return f, r

    def test_f_proposal_record_validation_and_forged_basis_rejection(self):
        for spin, expected in (('scalar', 7), ('spinor', 14)):
            with self.subTest(spin=spin):
                f, review = self.configure(spin)
                proposal, analysis, record, validation = f.propose(review)
                self.assertEqual(proposal['basis'], ['Ce:f'])
                self.assertEqual(proposal['num_wann'], expected)
                self.assertTrue(validation['record_consistent'])
                self.assertEqual(validation['recomputed_checks']['num_wann'], expected)
                record['trial_basis'] = ['Ce:d']
                with self.assertRaises(ValueError):
                    orbital_evidence.check_record(record, analysis, f.cache)

    def test_missing_f_columns_are_reported_as_unavailable_not_zero(self):
        f, _ = self.configure('scalar')
        f.meta['columns'] = list(SPD_COLUMNS)
        f.raw = f.raw[..., :9]
        f.save_cache()
        inspected = selection.inspect(f.cache)
        self.assertEqual(inspected['unavailable_chemical_shell_evidence'], ['Ce:f'])
        self.assertNotIn('Ce:f', {row['group'] for row in inspected['orbital_evidence']})

    def test_f_mcp_packet_and_selection_figure_use_same_generic_tools(self):
        from unittest.mock import patch
        from wannier_mcp import server
        f, review = self.configure('scalar')
        with patch.dict('os.environ', {'WAN_WORKSPACE_ROOT': str(f.root), 'WAN_ALLOW_SUBMIT': '0'}):
            result = server.prepare_evidence(str(f.cache), str(f.root/'packet'))
        self.assertTrue(result['ok'], result)
        draft = json.loads((f.root/'packet/review.json').read_text())
        self.assertEqual({d['group'] for d in draft['orbital_decisions']}, {'Ce:s', 'Ce:p', 'Ce:d', 'Ce:f'})
        self.assertTrue(all(d['action'] is None for d in draft['orbital_decisions']))
        proposal, _, _, _ = f.propose(review)
        destination = f.root/'rendered'
        destination.mkdir()
        selection.plot(f.cache, None, proposal, destination)
        self.assertTrue((destination/'selection.png').is_file())

    def test_sealed_f_interface_preserves_projection_and_solver_controls(self):
        import decision_stages
        import conduction_review
        import prepare_wannier_interface as interface
        from plot_dft_bands import verified_path
        f, review = self.configure('spinor')
        case = (f.root/'case').resolve()
        for name in ('01_scf', '02_band', '03_wan_trial'):
            (case/name).mkdir(parents=True)
        band, scf = case/'02_band', case/'01_scf'
        # All files and convergence markers are invented. Source validators,
        # stage binding and identity checks are not mocked or disabled.
        pos = 'Invented only\n1\n1 0 0\n0 1 0\n0 0 1\nCe\n1\nDirect\n0 0 0\n'
        nb = f.energies.shape[1]
        eigen = ['1 1 1 1', 'h', 'h', 'h', 'h', f'1 3 {nb}']
        pro = ['PROCAR lm decomposed', f'# of k-points: 3 # of bands: {nb} # of ions: 1']
        for k, energies in enumerate(f.energies):
            eigen.append(f'{k/4} 0 0 0')
            pro.append(f'k-point {k+1} : {k/4} 0 0 weight = 0')
            for b, energy in enumerate(energies):
                occ = 1 if energy < 0 else 0
                eigen.append(f'{b+1} {energy:.8f} {occ}')
                pro.append(f'band {b+1} # energy {energy:.8f} # occ. {occ}')
                pro.append('ion '+' '.join(SPDF_COLUMNS)+' tot')
                for table in range(4):
                    values = f.raw[0, k, b, 0] if table == 0 else np.zeros(16)
                    row = ' '.join(str(v) for v in np.r_[values, values.sum()])
                    pro.extend(['1 '+row, 'tot '+row])
        (band/'EIGENVAL').write_text('\n'.join(eigen)+'\n')
        (band/'PROCAR').write_text('\n'.join(pro)+'\n')
        (band/'KPOINTS').write_text('Invented path\n3\nLine-mode\nReciprocal\n0 0 0 ! A\n.5 0 0 ! B\n')
        for directory, icharg in ((scf, 2), (band, 11)):
            (directory/'POSCAR').write_text(pos)
            (directory/'POTCAR').write_text('Invented nonlicensed identity stand-in only\n')
            (directory/'INCAR').write_text(f'ICHARG = {icharg}\nENCUT = 400\nNSW = 0\nISPIN = 1\nLSORBIT = T\n')
            (directory/'OUTCAR').write_text(f'NBANDS = {nb}\nICHARG = {icharg}\naborting loop because EDIFF is reached\nGeneral timing and accounting\n')
        (scf/'CHGCAR').write_text('Invented density stand-in only\n')
        f.meta.update(source_files={key: {'sha256': hashlib.sha256((band/name).read_bytes()).hexdigest()}
            for key, name in [('poscar', 'POSCAR'), ('procar', 'PROCAR'), ('eigenval', 'EIGENVAL'), ('kpoints', 'KPOINTS')]})
        f.meta['procar_sha256'] = f.meta['source_files']['procar']['sha256']
        f.save_cache()
        with np.load(f.cache) as z: payload = {key: np.array(z[key]) for key in z.files}
        payload['occupations'] = np.where(f.energies[None] < 0, 1., 0.)
        geometry = json.loads(str(payload['path_geometry']))
        geometry['source_kpoints_sha256'] = f.meta['source_files']['kpoints']['sha256']
        payload['path_geometry'] = json.dumps(geometry)
        np.savez(band/'charge.npz', **payload)
        meta, _, _, cache_hash = orbital_evidence.read_cache(band/'charge.npz')
        path, _, _, _ = verified_path(band/'charge.npz', None, cache_hash, meta, 3)
        (band/'fit_path.json').write_text(json.dumps(path))
        (case/'MATERIAL-LAYOUT.json').write_text(json.dumps({'format': 'wannier-material-layout-v1',
            'case_dir': str(case), 'mode': 'fresh_dft'}))
        review['inspection_id'] = selection.inspect(band/'charge.npz')['inspection_id']
        review['trial_intent'] = {'mode': 'exploratory', 'reason': 'Invented format smoke, not a scientific model.'}
        review_path = f.root/'f-review.json'; review_path.write_text(json.dumps(review))
        context = f.root/'context.json'
        context.write_text(json.dumps({'experiment_id': 'invented-f-interface',
            'task': review['task_scope_review']['objective'], 'evaluation_mode': 'development_replay',
            'actor': {'kind': 'human', 'id': 'invented-contract-test'},
            'guidance': [{'origin': 'task', 'description': 'Invented generic f interface test only.'}]}))
        initial = decision_stages.snapshot(case, review_path, context)
        initial_dir = Path(initial['directory'])
        proposal = json.loads((initial_dir/'selection.json').read_text())
        questions = conduction_review.questions(band/'charge.npz', proposal)
        answer = copy.deepcopy(questions['answer_template'])
        answer['outer']['role'] = 'diffuse_tail' if questions['near_outer_sample'] else 'none_in_probe'
        answer['outer']['reason'] = 'Invented tail with explicit exploratory scope.'
        for item in answer['frozen_candidates']:
            item['decision'] = 'exploratory_defer'
            item['reason'] = 'Keep the independently declared invented candidate.'
        review['conduction_review'] = answer; review_path.write_text(json.dumps(review))
        selected = decision_stages.snapshot(case, review_path, context, parent=initial_dir,
            trial_id='trial_001', reason='Explicit invented f syntax test.')
        records = Path(selected['directory'])
        manifest = interface.prepare_band(band, scf, band/'charge.npz', records/'analysis.json',
            records/'decision.json', case/'03_wan_trial/trial_001/interface', mp_grid=[3,3,3])
        self.assertEqual(manifest['expected']['num_wann'], 14)
        self.assertEqual(manifest['trial_basis'], ['Ce:f'])
        self.assertEqual(manifest['win_scalars']['num_iter'], '0')
        self.assertEqual(manifest['win_scalars']['dis_num_iter'], '10000')
        self.assertEqual(float(manifest['win_scalars']['dis_conv_tol']), 1e-8)
        for key in ('write_hr', 'write_rmn', 'write_tb', 'bands_plot'):
            self.assertEqual(manifest['win_scalars'][key], 'true')
        generated = (case/'03_wan_trial/trial_001/interface/wannier90.win.in').read_text()
        self.assertIn('begin projections\nCe:f\nend projections', generated)
        self.assertIn('orbital_channels.py', decision_stages.verify(records)['code_sha256'])


if __name__ == '__main__':
    unittest.main()
