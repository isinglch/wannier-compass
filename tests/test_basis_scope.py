"""Invented task-role regressions; no historical material choices or fit data."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import jsonschema
import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / 'wannier_mcp/workflow/tools'
sys.path.insert(0, str(TOOLS))
import basis_scope
import decision_stages
import conduction_review
import manifold_selection as selection
import orbital_evidence
import review_workflow
import workflow_runtime


class TaskFirstSelection(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cache = self.root / 'invented.npz'
        # Ordered rows at each k, but background and task envelopes overlap:
        # first-row global maximum -1.1 > second-row global minimum -2.0.
        e = np.array([[-3., -2., -1., .3, 1.5, 3., 4., 5., 6., 8.],
                      [-1.1, -1., -.8, .4, 1.6, 3.1, 4.1, 5.1, 6.1, 8.1],
                      [-2., -1.5, -.5, .5, 1.7, 3.2, 4.2, 5.2, 6.2, 8.2]])
        raw = np.zeros((1, 3, 10, 1, 9))
        raw[0, :, 0, 0, 0] = .8
        raw[0, :, 1:4, 0, 0] = .02  # residual s mixing never mandates s
        raw[0, :, 1:4, 0, 1] = .7
        raw[0, :, 4:, 0, 1] = .001
        raw[0, :, 0, 0, 1] = .01
        meta = {'name': 'invented-task-contract', 'ef': 0.,
                'species': ['Al'], 'counts': [1],
                'columns': ['s', 'py', 'pz', 'px', 'dxy', 'dyz', 'dz2', 'dxz', 'x2-y2'],
                'sampling': 'band_path', 'spin_mode': 'scalar',
                'procar_sha256': 'a' * 64, 'ef_source': 'invented independent reference',
                'source_files': {'kpoints': {'sha256': 'b' * 64}}}
        self.energies = e
        self.raw = raw
        self.meta = meta
        self.save_cache()
        self.inspection = selection.inspect(self.cache)
        self.review = {
            'format': selection.FORMAT, 'inspection_id': self.inspection['inspection_id'],
            'basis_revision': 'invented-first-review',
            'task_scope_review': {
                'format': basis_scope.FORMAT, 'objective_kind': 'primary_task_manifold',
                'objective': 'Represent the independently identified dispersive task manifold.',
                'requested_scope_evidence': None, 'task_anchor_band_ranges_1based': [[2, 4]],
                'deep_blocks': [{'band_range_1based': [1, 1], 'orbital_groups': ['Al:s'],
                                 'role': 'retained_background',
                                 'reason': 'Concentrated s background outside the declared task; energy envelopes overlap.'}]},
            'additional_basis_evidence': {},
            'orbital_decisions': [self.row('Al:s', 'omit_first_trial', 'deep_only'),
                                  self.row('Al:p', 'include', 'task_component'),
                                  self.row('Al:d', 'omit_first_trial', 'no_task_role')],
            'target_band_ranges_1based': [[2, 4]], 'reference_band_ranges_1based': [[2, 7]],
            'frozen_band_ranges_1based': [[2, 4]], 'reference_upper_status': 'reviewed_decay',
            'target_reason': 'Task branches were identified before completing their target rows.',
            'reference_reason': 'Review the supplied continuation separately.',
            'frozen_reason': 'Leave the retained lower background in disentanglement.'}

    @staticmethod
    def row(group, action, role):
        return {'group': group, 'action': action, 'task_role': role,
                'reason': 'Invented explicit task-role review.',
                'risk_if_included': 'An unnecessary channel enlarges the model and can alter disentanglement.',
                'risk_if_omitted': 'A task representation component could be missed; inspect the declared task.'}

    def save_cache(self):
        geometry = {'kcoords_type': 'fractional', 'source_kpoints_sha256': 'b' * 64,
                    'reciprocal_lattice_2pi_per_angstrom': np.eye(3).tolist(),
                    'segments_1based': [[1, 3]],
                    'ticks': [{'index_1based': 1, 'label': 'A'}, {'index_1based': 3, 'label': 'B'}]}
        np.savez(self.cache, metadata=json.dumps(self.meta), energies=self.energies[None],
                 raw=self.raw, occupations=np.where(self.energies[None] < 0, 2., 0.),
                 kcoords=np.array([[[0., 0., 0.], [.25, 0., 0.], [.5, 0., 0.]]]),
                 path_geometry=json.dumps(geometry))

    def propose(self, review=None):
        return selection.propose(self.cache, self.inspection, review or self.review, 'invented-review.json')

    def test_nullable_task_scope_patch_remains_valid_for_proposal(self):
        draft = self.root / 'draft.json'
        workflow_runtime.atomic_json(draft, {**self.review, 'temporary_optional_note': 'remove'})
        before = self.propose()[0]
        review_workflow.patch_review(draft, {
            'task_scope_review': {'requested_scope_evidence': None},
            'temporary_optional_note': None,
        }, workflow_runtime.sha(draft))
        patched = json.loads(draft.read_text())
        self.assertIn('requested_scope_evidence', patched['task_scope_review'])
        self.assertIsNone(patched['task_scope_review']['requested_scope_evidence'])
        self.assertNotIn('temporary_optional_note', patched)
        after = self.propose(patched)[0]
        self.assertEqual(before['basis'], after['basis'])
        self.assertEqual(before['windows_relative_eV'], after['windows_relative_eV'])

    def test_inspection_equal_weight_channels_have_stable_cache_order(self):
        # A zero-weight tie must not change inspection identity with the runtime.
        self.raw[:] = 0.
        self.save_cache()
        inspected = selection.inspect(self.cache)
        _, _, groups, _ = orbital_evidence.read_cache(self.cache)
        expected = list(groups)[:3]
        self.assertTrue(all(row['strongest_mean_channels'] == expected
                            for row in inspected['band_table']))
        self.assertEqual(inspected['inspection_id'], selection.inspect(self.cache)['inspection_id'])

    def include_s(self, exploratory=False):
        review = copy.deepcopy(self.review)
        review['orbital_decisions'][0] = self.row('Al:s', 'include',
            'exploratory_task_component' if exploratory else 'task_component')
        review['additional_basis_evidence'] = {'Al:s': {
            'witness_k_band_1based': [1, 2],
            'representation_reason': 'Inspect lower-valence task mixing; compare a trial retaining s.',
            'independent_of_deep_block': True}}
        if exploratory:
            review['trial_intent'] = {'mode': 'exploratory', 'reason': 'Test the unresolved s representation concern.'}
            review['window_override'] = {'dis_eV': [-2.5, 3.5], 'frozen_eV': [-1.05, .55],
                                         'reason': 'Invented feasible candidate for the planned comparison.'}
        return review

    def test_main_prior_has_no_group13_s_and_optional_d_is_separate(self):
        for element in ('B', 'Al', 'Ga', 'In', 'Tl', 'Nh'):
            self.assertEqual(selection.chemical_hint(element), ('p',))
        for element in ('Ca', 'Sr', 'Ba'):
            self.assertEqual(selection.chemical_hint(element), ('s',))
            self.assertEqual(basis_scope.OPTIONAL_HINT_SHELLS[element], ('d',))
        self.assertEqual(self.inspection['chemical_starting_basis'], ['Al:p'])
        self.assertEqual({r['group'] for r in self.inspection['orbital_evidence']}, {'Al:s', 'Al:p', 'Al:d'})

    def test_optional_empty_d_hint_has_no_mandatory_omission_and_can_be_reviewed_for_inclusion(self):
        for element in ('Ca', 'Sr', 'Ba'):
            self.meta['species'] = [element]
            self.save_cache()
            inspection = selection.inspect(self.cache)
            self.assertEqual(inspection['chemical_starting_basis'], [element + ':s'])
            self.assertEqual(inspection['optional_chemical_hints'], [element + ':d'])
            _, energies, groups, _ = orbital_evidence.read_cache(self.cache)
            scope = copy.deepcopy(self.review['task_scope_review'])
            scope['deep_blocks'] = []
            rows = [self.row(element + ':s', 'include', 'task_component'),
                    self.row(element + ':p', 'omit_first_trial', 'secondary_hybridization'),
                    self.row(element + ':d', 'omit_first_trial', 'no_task_role')]
            basis_scope.validate(scope, {}, rows, energies, groups)
            # The optional d hint did not require an omission witness. An
            # independently witnessed exploratory d component is also allowed.
            rows[2] = self.row(element + ':d', 'include', 'exploratory_task_component')
            groups[element + ':d'][0, 1] = .03
            additional = {element + ':d': {'witness_k_band_1based': [1, 2],
                'representation_reason': 'Test a nominally empty d component on an independently identified task branch.',
                'independent_of_deep_block': True}}
            check = basis_scope.validate(scope, additional, rows, energies, groups,
                {'mode': 'exploratory', 'reason': 'Invented planned comparison.'})
            self.assertEqual(check['additional_channel_witnesses'][element + ':d']['raw_weight'], .03)

    def test_additional_d_is_allowed_in_an_actual_new_proposal(self):
        self.raw[0, 0, 1, 0, 4] = .03
        self.save_cache()
        self.inspection = selection.inspect(self.cache)
        review = copy.deepcopy(self.review)
        review['inspection_id'] = self.inspection['inspection_id']
        review['orbital_decisions'][2] = self.row('Al:d', 'include', 'task_component')
        review['additional_basis_evidence'] = {'Al:d': {'witness_k_band_1based': [1, 2],
            'representation_reason': 'Invented d component relevant to representing the task branch.',
            'independent_of_deep_block': True}}
        proposal, _, record, validation = self.propose(review)
        self.assertEqual(proposal['basis'], ['Al:d', 'Al:p'])
        self.assertEqual(proposal['num_wann'], 8)
        self.assertTrue(validation['record_consistent'])
        self.assertEqual(record['status'], 'proposed_trial')

    def test_deep_only_omission_with_no_global_gap_keeps_background_and_independent_edges(self):
        self.assertLess(selection.boundary_evidence(self.energies, 1)['gap_above_global_eV'], 0)
        proposal, _, record, validation = self.propose()
        self.assertEqual(proposal['basis'], ['Al:p'])
        self.assertEqual(proposal['num_wann'], 3)
        self.assertEqual(proposal['band_selection']['exclude_bands_1based'], [])
        self.assertEqual(proposal['retained_lower_background']['original_bands_1based'], [1])
        self.assertLess(proposal['windows_relative_eV']['dis_eV'][0],
                        proposal['windows_relative_eV']['frozen_eV'][0])
        self.assertEqual(record['task_scope_review'], self.review['task_scope_review'])
        self.assertEqual(record['scope']['description'], self.review['task_scope_review']['objective'])
        self.assertTrue(validation['record_consistent'])
        self.assertEqual(record['status'], 'proposed_trial')

    def test_bonding_completeness_prose_and_target_expansion_cannot_supply_extra_evidence(self):
        review = self.include_s()
        review['orbital_decisions'][0]['reason'] = 'Include all bonding valence shells for completeness.'
        review['additional_basis_evidence'] = {}
        with self.assertRaisesRegex(ValueError, 'additional_basis_evidence'):
            self.propose(review)
        review = self.include_s()
        review['target_band_ranges_1based'] = [[1, 4]]
        review['task_scope_review']['deep_blocks'][0]['role'] = 'task_target'
        review['task_scope_review']['task_anchor_band_ranges_1based'] = [[1, 4]]
        review['additional_basis_evidence']['Al:s']['witness_k_band_1based'] = [1, 1]
        with self.assertRaisesRegex(ValueError, 'independent of their deep block'):
            self.propose(review)

    def test_lower_valence_s_witness_is_valid_even_when_not_strongest(self):
        review = self.include_s()
        proposal, _, record, _ = self.propose(review)
        self.assertEqual(proposal['basis'], ['Al:p', 'Al:s'])
        witness = proposal['task_scope_checks']['additional_channel_witnesses']['Al:s']
        self.assertEqual(witness['k_band_1based'], [1, 2])
        self.assertEqual(witness['raw_weight'], .02)
        self.assertLess(witness['raw_weight'], .7)
        self.assertEqual(record['additional_basis_evidence'], review['additional_basis_evidence'])

    def test_witness_has_no_positive_strength_threshold_and_zero_is_not_a_state(self):
        review = self.include_s()
        _, _, groups, _ = orbital_evidence.read_cache(self.cache)
        groups['Al:s'][0, 1] = 1e-15
        checked = basis_scope.validate(review['task_scope_review'], review['additional_basis_evidence'],
                                       review['orbital_decisions'], self.energies, groups)
        self.assertEqual(checked['additional_channel_witnesses']['Al:s']['raw_weight'], 1e-15)
        groups['Al:s'][0, 1] = 0.
        with self.assertRaisesRegex(ValueError, 'zero raw projection'):
            basis_scope.validate(review['task_scope_review'], review['additional_basis_evidence'],
                                 review['orbital_decisions'], self.energies, groups)

    def test_unresolved_inclusion_can_make_a_feasible_exploratory_trial(self):
        review = self.include_s(exploratory=True)
        proposal, _, record, _ = self.propose(review)
        self.assertEqual(proposal['status'], 'proposed_trial')
        self.assertEqual(record['trial_intent']['mode'], 'exploratory')
        del review['trial_intent']
        with self.assertRaisesRegex(ValueError, 'requires trial_intent'):
            self.propose(review)

    def test_explicitly_requested_extended_task_can_include_deep_s(self):
        review = self.include_s()
        scope = review['task_scope_review']
        scope['objective_kind'] = 'explicitly_requested_extended_manifold'
        scope['objective'] = 'The task explicitly includes the concentrated deep s block.'
        scope['requested_scope_evidence'] = 'Invented explicit task request: include the deep s branch in the represented manifold.'
        scope['task_anchor_band_ranges_1based'] = [[1, 4]]
        scope['deep_blocks'][0]['role'] = 'task_target'
        review['target_band_ranges_1based'] = [[1, 4]]
        review['reference_band_ranges_1based'] = [[1, 7]]
        review['frozen_band_ranges_1based'] = [[1, 4]]
        review['additional_basis_evidence']['Al:s'].update(
            witness_k_band_1based=[1, 1], independent_of_deep_block=False)
        proposal, _, _, _ = self.propose(review)
        self.assertEqual(proposal['basis'], ['Al:p', 'Al:s'])
        scope['requested_scope_evidence'] = None
        with self.assertRaisesRegex(ValueError, 'requested_scope_evidence'):
            self.propose(review)

    def test_deep_only_inclusion_and_background_target_contradictions_are_rejected(self):
        review = copy.deepcopy(self.review)
        review['orbital_decisions'][0]['action'] = 'include'
        with self.assertRaisesRegex(ValueError, 'deep_only/no_task_role'):
            self.propose(review)
        review = copy.deepcopy(self.review)
        review['target_band_ranges_1based'] = [[1, 4]]
        review['reference_band_ranges_1based'] = [[1, 7]]
        with self.assertRaisesRegex(ValueError, 'cannot silently become target'):
            self.propose(review)

    def test_both_inclusion_and_omission_risk_are_required(self):
        for row_index, field in ((1, 'risk_if_included'), (0, 'risk_if_omitted')):
            review = copy.deepcopy(self.review)
            del review['orbital_decisions'][row_index][field]
            with self.assertRaisesRegex(ValueError, field):
                self.propose(review)

    def test_target_cannot_drop_declared_task_anchor_to_pass_window_checks(self):
        review = copy.deepcopy(self.review)
        review['target_band_ranges_1based'] = [[3, 4]]
        review['frozen_band_ranges_1based'] = [[3, 4]]
        with self.assertRaisesRegex(ValueError, 'retain every task-anchor'):
            self.propose(review)

    def test_target_cannot_drop_explicit_deep_task_even_outside_anchor(self):
        review = self.include_s(exploratory=True)
        scope = review['task_scope_review']
        scope['objective_kind'] = 'explicitly_requested_extended_manifold'
        scope['requested_scope_evidence'] = 'Invented request includes the deep s block.'
        scope['deep_blocks'][0]['role'] = 'task_target'
        # The unchanged anchor and target are rows 2-4, but requested row 1
        # must not disappear merely because the smaller window is feasible.
        with self.assertRaisesRegex(ValueError, 'deep task-target row'):
            self.propose(review)
        review['target_band_ranges_1based'] = [[1, 4]]
        review['reference_band_ranges_1based'] = [[1, 7]]
        review['window_override']['dis_eV'][0] = -3.5
        proposal, _, _, _ = self.propose(review)
        self.assertEqual(proposal['task_scope_review'], scope)

    def test_objective_is_checked_before_target_ranges_and_v2_requires_explicit_migration(self):
        review = copy.deepcopy(self.review)
        review['task_scope_review']['objective'] = None
        review['target_band_ranges_1based'] = 'invalid'
        with self.assertRaisesRegex(ValueError, 'objective'):
            self.propose(review)
        review = copy.deepcopy(self.review)
        review['format'] = 'wannier-manifold-review-v2'
        before = copy.deepcopy(review)
        with self.assertRaisesRegex(ValueError, 'explicitly migrate'):
            self.propose(review)
        self.assertEqual(review, before)

    def test_frozen_counterexample_is_only_a_veto_and_keeps_basis_scope(self):
        review = copy.deepcopy(self.review)
        review['window_override'] = {'dis_eV': [-3.5, 3.5], 'frozen_eV': [-3.05, .55],
                                    'reason': 'Deliberately expose an invented unwanted frozen background state.'}
        proposal, _, record, _ = self.propose(review)
        self.assertGreater(proposal['adopted_frozen_negative_screen']['negative_state_count'], 0)
        self.assertEqual(proposal['status'], 'needs_window_review')
        self.assertEqual(record['trial_basis'], ['Al:p'])
        self.assertEqual(record['additional_basis_evidence'], {})
        self.assertEqual(record['task_scope_review'], self.review['task_scope_review'])

    def test_check_record_revalidates_witnesses_and_schema_and_stage_carry_scope(self):
        _, analysis, record, _ = self.propose(self.include_s())
        altered = copy.deepcopy(record)
        altered['additional_basis_evidence']['Al:s']['witness_k_band_1based'] = [1, 1]
        with self.assertRaisesRegex(ValueError, 'independent task anchor'):
            orbital_evidence.check_record(altered, analysis, self.cache)
        altered = copy.deepcopy(record)
        del altered['orbital_decisions'][0]['risk_if_included']
        with self.assertRaises(jsonschema.ValidationError):
            orbital_evidence.check_record(altered, analysis, self.cache)
        choices = decision_stages.physics(record)
        self.assertEqual(choices['task_scope_review'], record['task_scope_review'])
        self.assertEqual(choices['additional_basis_evidence'], record['additional_basis_evidence'])
        changed = copy.deepcopy(record)
        changed['task_scope_review']['objective'] += ' A new objective.'
        self.assertNotEqual(decision_stages.physics(changed), choices)

    def test_old_archive_schema_and_physics_remain_readable_not_current_proposals(self):
        _, analysis, record, _ = self.propose()
        old = copy.deepcopy(record)
        del old['task_scope_review'], old['additional_basis_evidence']
        for row in old['orbital_decisions']:
            for field in ('task_role', 'risk_if_included', 'risk_if_omitted'):
                del row[field]
        schema = json.loads((TOOLS.parent / 'policies/decision-v0.2.schema.json').read_text())
        jsonschema.Draft202012Validator(schema).validate(old)
        self.assertNotIn('task_scope_review', decision_stages.physics(old))
        with self.assertRaisesRegex(ValueError, 'explicitly migrate'):
            orbital_evidence.check_record(old, analysis, self.cache)

    def test_template_exposes_unassigned_task_roles_and_summary_carries_witness(self):
        inspection = self.root / 'inspection.json'
        inspection.write_text(json.dumps(self.inspection))
        review_workflow.template(inspection, self.root / 'review.json')
        draft = json.loads((self.root / 'review.json').read_text())
        self.assertIsNone(draft['task_scope_review']['objective'])
        self.assertIsNone(draft['task_scope_review']['deep_blocks'])
        self.assertTrue(all(row['task_role'] is None for row in draft['orbital_decisions']))
        self.assertEqual(draft['additional_basis_evidence'], {})
        proposal, _, _, _ = self.propose(self.include_s())
        short = workflow_runtime.summarize(proposal)
        self.assertEqual(short['task_scope_review'], proposal['task_scope_review'])
        self.assertEqual(short['additional_basis_evidence']['Al:s']['witness_k_band_1based'], [1, 2])

    def test_official_cli_proposal_and_check_record_carry_scope_and_render_invented_data(self):
        inspection = self.root / 'inspection.json'
        review = self.root / 'review.json'
        inspection.write_text(json.dumps(self.inspection))
        review.write_text(json.dumps(self.include_s()))
        out = self.root / 'proposal'
        command = [sys.executable, '-B', str(TOOLS / 'manifold_selection.py'), 'propose',
                   '--cache', str(self.cache), '--inspection', str(inspection),
                   '--review', str(review), '--out-dir', str(out), '--no-gallery']
        process = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['task_scope_review']['objective'], self.review['task_scope_review']['objective'])
        self.assertEqual(result['additional_basis_evidence']['Al:s']['witness_k_band_1based'], [1, 2])
        self.assertTrue((out / 'selection.png').is_file())
        self.assertTrue((out / 'selection.pdf').is_file())
        process = subprocess.run([sys.executable, '-B', str(TOOLS / 'orbital_evidence.py'),
            'check-record', '--cache', str(self.cache), '--record', str(out / 'decision.json'),
            '--analysis', str(out / 'analysis.json')], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)['record_consistent'])

    def test_unresolved_initial_hypothesis_stays_nonexecuting_then_explicit_real_selection_keeps_physics(self):
        case = (self.root / 'invented_case').resolve()
        for name in ('01_scf', '02_band', '03_wan_trial'):
            (case / name).mkdir(parents=True, exist_ok=True)
        (case / '02_band/charge.npz').write_bytes(self.cache.read_bytes())
        from plot_dft_bands import verified_path
        path, _, _, _ = verified_path(self.cache, None, self.inspection['cache_sha256'], self.meta, 3)
        (case / '02_band/fit_path.json').write_text(json.dumps(path))
        (case / 'MATERIAL-LAYOUT.json').write_text(json.dumps({
            'format': 'wannier-material-layout-v1', 'case_dir': str(case), 'mode': 'fresh_dft'}))
        review = self.include_s(exploratory=True)
        review_path = self.root / 'hypothesis_review.json'
        context_path = self.root / 'invented_context.json'
        review_path.write_text(json.dumps(review))
        context_path.write_text(json.dumps({'experiment_id': 'invented-task-contract',
            'task': review['task_scope_review']['objective'], 'evaluation_mode': 'development_replay',
            'actor': {'kind': 'human', 'id': 'invented-test-fixture'},
            'guidance': [{'origin': 'task', 'description': 'All arrays and choices are invented for a generic execution-contract test.'}]}))
        initial = decision_stages.snapshot(case, review_path, context_path)
        initial_dir = Path(initial['directory'])
        validation = json.loads((initial_dir / 'validation.json').read_text())
        self.assertFalse(initial['execution_selected'])
        self.assertFalse(validation['interface_preparation_allowed'])
        self.assertTrue(validation['requires_explicit_stage_selection'])
        self.assertIn('Non-executable', validation['initial_intent_role'])
        self.assertIsNone(decision_stages.verify(initial_dir)['scientific_trial_index'])
        with self.assertRaisesRegex(ValueError, 'Initial is a non-executable baseline'):
            decision_stages.require_executable_decision(initial_dir / 'decision.json')
        proposal = json.loads((initial_dir / 'selection.json').read_text())
        questions = conduction_review.questions(case / '02_band/charge.npz', proposal)
        answer = copy.deepcopy(questions['answer_template'])
        answer['outer']['role'] = 'diffuse_tail' if questions['near_outer_sample'] else 'none_in_probe'
        answer['outer']['reason'] = 'Invented supplied near-outer continuation; keep explicit exploratory uncertainty.'
        for row in answer['frozen_candidates']:
            row['decision'] = 'exploratory_defer'
            row['reason'] = 'Keep the independently declared exploratory frozen candidate for this invented comparison.'
        review['conduction_review'] = answer
        review_path.write_text(json.dumps(review))
        selected = decision_stages.snapshot(case, review_path, context_path, parent=initial_dir,
            trial_id='trial_001', reason='Explicitly select the unexecuted exploratory hypothesis.', baseline_control=True)
        selected_dir = Path(selected['directory'])
        self.assertTrue(selected['execution_selected'])
        self.assertEqual(selected['changes'], {})
        self.assertEqual(decision_stages.verify(selected_dir)['scientific_trial_index'], 1)
        binding = decision_stages.require_executable_decision(selected_dir / 'decision.json')
        self.assertEqual(binding['role'], 'real_first_try')
        initial_decision = json.loads((initial_dir / 'decision.json').read_text())
        selected_decision = json.loads((selected_dir / 'decision.json').read_text())
        self.assertEqual(decision_stages.physics(initial_decision), decision_stages.physics(selected_decision))
        self.assertEqual(selected_decision['trial_intent']['mode'], 'exploratory')
        self.assertEqual(selected_decision['orbital_decisions'][0]['task_role'], 'exploratory_task_component')


if __name__ == '__main__':
    unittest.main()
