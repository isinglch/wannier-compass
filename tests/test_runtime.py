"""Execution contracts, not scientific quality claims; all test values are invented."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'wannier_mcp/workflow/tools'
sys.path.insert(0, str(TOOLS))
import slurm_runner as runner
import remote_command as remote
import review_workflow as review
import usage_accounting as accounting
import workflow_runtime as runtime
from wannier_mcp import server


class TemporaryCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)


class RuntimeContracts(TemporaryCase):
    def test_large_mesh_preserves_checks_and_complete_log(self):
        payload = {'status': 'consistent', 'ready_for_wannier': False,
                   'num_wann': 10, 'actual_kpoints': [[0.1, 0.2, 0.3]] * 15000,
                   'mesh_gate': {'gate_pass': False, 'min_nouter': 9,
                                 'per_k': [{'nouter': 9}] * 15000},
                   'adopted_frozen_negative_screen': {'negative_state_count': 1,
                                                     'negative_samples': [{'k_band_1based': [4, 8]}]}}
        stdout = json.dumps(payload)
        process = subprocess.CompletedProcess([], 0, stdout, '')
        with patch.object(runtime.subprocess, 'run', return_value=process):
            result, parsed = runtime.execute('prepare_wannier_interface.py', ['validate'], self.root)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), runtime.INLINE_CHARS)
        self.assertEqual(json.loads(Path(result['log']).read_text())['stdout'], stdout)
        self.assertEqual(parsed, payload)
        self.assertEqual(result['result']['mesh_gate']['min_nouter'], 9)
        self.assertEqual(result['result']['adopted_frozen_negative_screen']['negative_state_count'], 1)
        self.assertFalse(result['result']['ready_for_wannier'])
        self.assertEqual(result['log_sha256'], runtime.sha(result['log']))

    def test_failure_stays_nonzero_and_no_retry(self):
        with patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, '', 'missing required witness')) as run:
            result, _ = runtime.execute('manifold_selection.py', ['propose'], self.root)
        self.assertFalse(result['ok'])
        self.assertEqual(result['exit_code'], 2)
        self.assertFalse(result['error']['automatic_retry'])
        self.assertEqual(run.call_count, 1)

    def test_budget_pressure_preserves_window_endpoints_and_witness_coordinates(self):
        payload = {'status': 'needs_window_review', 'basis': ['X:s', 'X:p'],
                   'cache': str(self.root / ('long/' * 170) / 'charge.npz'),
                   'windows_relative_eV': {'dis_eV': [-10, 16], 'frozen_eV': [-9, 2], 'review_reason': 'r' * 2000},
                   'numerical_checks': {'gate_pass': True, 'disentanglement_headroom': {'min': 0, 'median': 2, 'max': 3}, 'scope': 's' * 2000},
                   'adopted_frozen_first_unprotected_target': {'k_band_1based': [100, 6], 'energy_eV': 2.1},
                   'upper_reference_audit': {'all_computed_near_outer': {'selected_raw_max': 0.6}, 'scope': 't' * 2000},
                   'frozen_upper_review': {'candidates': [{'negative_veto': True, 'windows': {'frozen_eV': [-9, 3]},
                       'negative_screen': {'negative_state_count': 2, 'negative_samples': [{'k_band_1based': [145, 4]}]}}]},
                   'frozen_candidates': [{'reason': 'text' * 2000}] * 6}
        log = self.root / 'record.json'
        runtime.atomic_json(log, payload)
        result = runtime.envelope({'exit_code': 0, 'stderr': ''}, log, payload)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), runtime.INLINE_CHARS)
        short = result['result']
        self.assertEqual(short['windows_relative_eV']['dis_eV'], [-10, 16])
        self.assertEqual(short['windows_relative_eV']['frozen_eV'], [-9, 2])
        self.assertEqual(short['adopted_frozen_first_unprotected_target']['k_band_1based'], [100, 6])
        self.assertEqual(short['numerical_checks']['disentanglement_headroom']['min'], 0)
        self.assertEqual(short['frozen_upper_review']['first_counterexample']['k_band_1based'], [145, 4])
        self.assertEqual(short['cache'], payload['cache'])

    def test_exact_log_field_can_be_read_without_full_context(self):
        target = self.root / 'log.json'
        runtime.atomic_json(target, {'stdout': json.dumps({'checks': {'nouter': [8, 9, 10]}})})
        with patch.dict(os.environ, {'WAN_WORKSPACE_ROOT': str(self.root)}):
            output = server.read_record_summary(str(target), '/stdout/checks/nouter/1')
            self.assertEqual(output['result']['result'], 9)
            with self.assertRaisesRegex(ValueError, 'JSON pointer'):
                server.read_record_summary(str(target), '/stdout/checks/nouter/-1')

    def test_runtime_symlink_cannot_redirect_outputs(self):
        with tempfile.TemporaryDirectory() as other:
            (self.root / '.wannier_runtime').symlink_to(other)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                runtime.execute('manifold_selection.py', [], self.root)

    def test_transport_business_error_is_not_retried(self):
        process = subprocess.CompletedProcess([], 2, '', 'expected witness [4, 8]')
        with patch.object(remote.subprocess, 'run', return_value=process) as run:
            output = remote.remote('cluster', ['python', 'tool.py', 'two words'], self.root, read_only=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(output['exit_code'], 2)
        self.assertIn("'two words'", run.call_args.args[0][-1])

    def test_transport_read_retry_and_uncertain_mutation(self):
        failure = subprocess.CompletedProcess([], 255, '', 'Connection closed')
        success = subprocess.CompletedProcess([], 0, '{}', '')
        with patch.object(remote.subprocess, 'run', side_effect=[failure, success]) as run, patch.object(remote.time, 'sleep'):
            output = remote.remote('cluster', ['squeue'], self.root, read_only=True)
        self.assertTrue(output['ok'])
        self.assertEqual(run.call_count, 2)
        with patch.object(remote.subprocess, 'run', return_value=failure) as run:
            output = remote.remote('cluster', ['sbatch', 'job.slurm'], self.root)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(output['status'], 'completion_unknown')

    def test_review_incremental_hash_and_seal_protection(self):
        draft = self.root / 'review.json'
        runtime.atomic_json(draft, {'orbital_decisions': [{'group': 'X:p', 'action': 'include'}], 'a': {'b': 1}})
        original = runtime.sha(draft)
        result = review.patch_review(draft, {'a': {'c': 2}}, original)
        self.assertEqual(json.loads(draft.read_text())['a'], {'b': 1, 'c': 2})
        self.assertEqual(json.loads(draft.read_text())['orbital_decisions'][0]['action'], 'include')
        self.assertTrue(Path(result['history']).is_file())
        with self.assertRaisesRegex(ValueError, 'Draft changed'):
            review.patch_review(draft, {'a': 3}, original)
        runtime.atomic_json(self.root / 'STAGE.json', {})
        with self.assertRaisesRegex(ValueError, 'sealed'):
            review.patch_review(draft, {}, runtime.sha(draft))

    def test_concurrent_patches_cannot_both_accept_the_same_hash(self):
        draft = self.root / 'review.json'
        runtime.atomic_json(draft, {'a': 1})
        digest = runtime.sha(draft)
        entered, release = Event(), Event()
        original = review.atomic_json
        def pause_history(path, value):
            if '.draft_history' in str(path):
                entered.set()
                self.assertTrue(release.wait(3))
            original(path, value)
        with patch.object(review, 'atomic_json', side_effect=pause_history), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(review.patch_review, draft, {'b': 2}, digest)
            self.assertTrue(entered.wait(3))
            try:
                second = pool.submit(review.patch_review, draft, {'c': 3}, digest)
                with self.assertRaises(FileExistsError):
                    second.result(timeout=3)
            finally:
                release.set()
            first.result(timeout=3)
        self.assertEqual(json.loads(draft.read_text()), {'a': 1, 'b': 2})

    def test_skeleton_assigns_no_physical_choice(self):
        inspection = self.root / 'inspection.json'
        runtime.atomic_json(inspection, {'inspection_id': 'invented', 'orbital_evidence': [{'group': 'X:p'}]})
        review.template(inspection, self.root / 'review.json')
        data = json.loads((self.root / 'review.json').read_text())
        self.assertIsNone(data['orbital_decisions'][0]['action'])
        self.assertIsNone(data['target_band_ranges_1based'])
        self.assertNotIn('window_override', data)


class RunnerContracts(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.site = self.root / 'site.json'
        runtime.atomic_json(self.site, {'scheduler': 'slurm'})
        self.executable = self.root / 'postw90'
        self.executable.touch()
        runner.configure(self.root, self.site, 'trial', postw90=self.executable)

    def controller(self, execute=True):
        return runner.Runner(self.root, execute_jobs=execute)

    def test_selection_boundary_never_submits(self):
        with patch.object(runner, 'execute') as run:
            output = self.controller().advance()
        self.assertEqual(output['status'], 'scientific_decision_required')
        run.assert_not_called()

    def test_submission_once_and_unknown_response_stops(self):
        with patch.object(runner, 'execute', return_value=({'ok': True, 'log': 'fake'}, {'submitted': True, 'job_id': '123;cluster'})) as run:
            self.assertEqual(self.controller().submit('wannier', 'wannier_workflow.py', ['submit']), '123')
            self.assertEqual(self.controller().submit('wannier', 'wannier_workflow.py', ['submit']), '123')
        self.assertEqual(run.call_count, 1)
        with patch.object(runner, 'execute', return_value=({'ok': False, 'log': 'fake'}, None)) as run:
            self.controller().submit('interface', 'dft_workflow.py', ['submit-interface'])
            self.controller().submit('interface', 'dft_workflow.py', ['submit-interface'])
        self.assertEqual(run.call_count, 1)
        output = self.controller().wait_for('interface', [self.root / 'missing'])
        self.assertEqual(output['status'], 'submission_completion_unknown')

    def test_two_stale_instances_reload_state_under_lock(self):
        first, second = self.controller(), self.controller()
        def stage(instance):
            instance.submit('wannier', 'wannier_workflow.py', ['submit'])
            return instance.status('waiting_for_job', 'test')
        with patch.object(runner.Runner, 'execute_trial', stage), patch.object(runner, 'execute',
            return_value=({'ok': True, 'log': 'fake'}, {'submitted': True, 'job_id': '123'})) as run:
            first.advance()
            second.advance()
        self.assertEqual(run.call_count, 1)

    def test_window_reuse_defers_old_window_gate_to_original_prepare(self):
        source = self.root / 'prior_interface'
        source.mkdir()
        runner.configure(self.root, self.site, 'trial', trial_id='trial_002', postw90=self.executable, interface_source=source)
        instance = runner.Runner(self.root, trial_id='trial_002', execute_jobs=False)
        selected = instance.trial / 'records/selection'
        selected.mkdir(parents=True)
        runtime.atomic_json(selected / 'STAGE.json', {'stage_id': 'invented'})
        def verify(tool, args):
            if tool == 'prepare_wannier_interface.py':
                raise AssertionError('Do not apply the old window gate before new-window reuse preparation.')
            return {'role': 'retry', 'stage_id': 'invented'}
        with patch.object(instance, 'verify', side_effect=verify), patch.object(instance, 'command', return_value={}) as command:
            output = instance.advance()
        self.assertEqual(output['status'], 'execution_authorization_required')
        self.assertEqual(command.call_args.args[0], 'prepare_wannier')
        self.assertIn('--decision', command.call_args.args[2])

    def test_queue_absence_is_not_success(self):
        instance = self.controller()
        instance.state['submissions']['wannier'] = {'job_id': '123', 'first_missing_queue_epoch': 0}
        instance.save()
        with patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            output = instance.wait_for('wannier', [self.root / 'missing'])
        self.assertEqual(output['status'], 'job_outcome_unknown')

    def test_lost_completed_response_resumes_from_log_without_rerun(self):
        instance = self.controller()
        instance.state['steps']['prepare'] = {'status': 'started', 'started_utc': '2000', 'tool': 'wannier_workflow.py'}
        instance.save()
        directory = runtime.runtime_directory(self.root, 'logs')
        runtime.atomic_json(directory / 'wannier_workflow-test.json', {
            'started_utc': '2026', 'exit_code': 0,
            'argv': ['python', '-B', 'wannier_workflow.py', 'prepare'], 'stdout': '{"status":"prepared"}'})
        with patch.object(runner, 'execute') as run:
            output = self.controller().command('prepare', 'wannier_workflow.py', ['prepare'])
        self.assertEqual(output['status'], 'prepared')
        run.assert_not_called()

    def test_config_is_immutable_and_complete_before_submission(self):
        with self.assertRaisesRegex(ValueError, 'immutable'):
            runner.configure(self.root, self.site, 'trial', postw90=self.executable, ntasks=2)
        with self.assertRaisesRegex(ValueError, 'postw90'):
            runner.configure(self.root, self.site, 'trial', trial_id='trial_002')

    def test_adopt_rejects_wrong_stage_record(self):
        bad = self.root / 'wrong.json'
        runtime.atomic_json(bad, {'stdout': '123', 'returncode': 0})
        with self.assertRaisesRegex(ValueError, 'exact stage'):
            runner.adopt(self.root, 'trial', 'trial_001', 'wannier', '123', bad)


class AccountingContracts(TemporaryCase):
    def test_missing_stays_null_and_request_is_deduplicated(self):
        source = self.root / 'native.json'
        runtime.atomic_json(source, {'id': 'invented-request', 'model': 'invented-model',
                                     'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
        first = accounting.record(self.root, source, 'deepseek', 'assessment')
        second = accounting.record(self.root, source, 'deepseek', 'assessment')
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertIsNone(first['usage']['input_uncached'])
        report = accounting.report(self.root)
        self.assertEqual(report['requests'], 1)
        self.assertIsNone(report['total_cost'])

    def test_caller_alias_cannot_count_one_native_request_twice(self):
        source = self.root / 'native.json'
        runtime.atomic_json(source, {'id': 'native-response', 'model': 'invented', 'usage': {'completion_tokens': 20}})
        accounting.record(self.root, source, 'deepseek', 'assessment', 'header-one')
        output = accounting.record(self.root, source, 'deepseek', 'assessment', 'header-two')
        self.assertTrue(output['duplicate'])
        self.assertEqual(accounting.report(self.root)['requests'], 1)

    def test_exclusive_native_counts_and_matching_explicit_rates(self):
        source = self.root / 'native.json'
        runtime.atomic_json(source, {'id': 'invented-request', 'model': 'invented-model',
            'usage': {'prompt_tokens': 100, 'prompt_cache_hit_tokens': 80,
                      'prompt_cache_miss_tokens': 20, 'completion_tokens': 10}})
        accounting.record(self.root, source, 'deepseek', 'real_first_try')
        pricing = self.root / 'pricing.json'
        runtime.atomic_json(pricing, {'provider': 'deepseek', 'model': 'invented-model', 'currency': 'invented',
            'source': 'test-only invented rates', 'effective_date': '2000-01-01',
            'billable_fields': ['input_uncached', 'cache_read', 'output'],
            'rates_per_million': {'input_uncached': 2, 'cache_read': 1, 'output': 3}})
        report = accounting.report(self.root, pricing)
        self.assertAlmostEqual(report['total_cost'], 0.00015)
        data = json.loads(pricing.read_text()); data['model'] = 'other'
        runtime.atomic_json(pricing, data)
        with self.assertRaisesRegex(ValueError, 'matching native'):
            accounting.report(self.root, pricing)

    def test_mcp_accounting_and_patch_stay_in_workspace(self):
        with patch.dict(os.environ, {'WAN_WORKSPACE_ROOT': str(self.root)}):
            with self.assertRaisesRegex(ValueError, 'within WAN_WORKSPACE_ROOT'):
                server.account_native_usage('../outside.json', 'deepseek', 'assessment')


if __name__ == '__main__':
    unittest.main()
