"""DFT input routing contracts only; fixture contents do not establish valid DFT."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'wannier_mcp/workflow/tools'
sys.path.insert(0, str(TOOLS))
import material_layout as layout
import prepare_wannier_interface as interface
import shared_nscf as shared
import slurm_runner as runner
from workflow_runtime import atomic_json


class DFTSourceRouting(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.site = self.root / 'site.json'
        atomic_json(self.site, {'scheduler': 'slurm'})
        self.postw90 = self.root / 'postw90'
        self.postw90.touch()

    def case(self, mode):
        case = self.root / mode
        sources = {}
        for stage in ('01_scf', '02_band'):
            source = case / stage if mode == 'fresh_dft' else self.root / (mode + '_' + stage)
            source.mkdir(parents=True)
            (source / 'INCAR').write_text(stage + ' input\n')
            sources[stage] = source
        if mode == 'fresh_dft':
            layout.register_fresh(case)
        else:
            layout.init(case, sources['01_scf'], sources['02_band'])
        return case

    def controller(self, case, **options):
        runner.configure(case, self.site, 'trial', postw90=self.postw90, **options)
        instance = runner.Runner(case, execute_jobs=False)
        selected = instance.trial / 'records/selection'
        selected.mkdir(parents=True)
        atomic_json(selected / 'STAGE.json', {'stage_id': 'routing-fixture'})
        return instance

    def advance(self, instance, command):
        with patch.object(instance, 'verify', return_value={'role': 'real_first_try', 'stage_id': 'routing-fixture'}):
            with patch.object(instance, 'command', side_effect=command) as called:
                with patch.object(runner, 'execute') as execution:
                    output = instance.advance()
        execution.assert_not_called()
        called.assert_called_once()
        return output

    def check_interface_args(self, instance, expected_scf, expected_band, key, tool, args):
        self.assertEqual((key, tool), ('prepare_interface', 'prepare_wannier_interface.py'))
        self.assertEqual(args[:13], ['prepare', '--band-dir', str(expected_band), '--scf-dir', str(expected_scf),
            '--cache', str(instance.case / '02_band/charge.npz'), '--analysis', str(instance.trial / 'records/selection/analysis.json'),
            '--decision', str(instance.trial / 'records/selection/decision.json'), '--out-dir', str(instance.trial / 'interface')])
        self.assertEqual((Path(args[2]) / 'INCAR').read_text(), '02_band input\n')
        self.assertEqual((Path(args[4]) / 'INCAR').read_text(), '01_scf input\n')

    def test_fresh_and_reused_layouts_supply_readable_original_inputs(self):
        for mode in ('fresh_dft', 'reused_dft'):
            with self.subTest(mode=mode):
                case = self.case(mode)
                instance = self.controller(case, mp_grid=[2, 3, 4])
                suffix = 'source' if mode == 'reused_dft' else ''
                def command(key, tool, args):
                    self.check_interface_args(instance, case / '01_scf' / suffix, case / '02_band' / suffix, key, tool, args)
                    self.assertEqual(args[13:], ['--mp-grid', '2', '3', '4'])
                    return {}
                output = self.advance(instance, command)
                self.assertEqual(output['status'], 'execution_authorization_required')
                self.assertEqual(output['stage'], 'interface')

    def shared_controller(self, mp_grid=None):
        case = self.case('reused_dft')
        source = self.root / 'shared_wavecar'
        source.mkdir()
        bound = {}
        for stage, field in (('01_scf', 'scf_directory'), ('02_band', 'band_directory')):
            original = self.root / 'sealed_originals' / stage
            original.mkdir(parents=True)
            (original / 'INCAR').write_text((case / stage / 'source/INCAR').read_text())
            bound[field] = str(original)
        atomic_json(source / 'SOURCE_SEAL.json', {'format': shared.FORMAT, 'directory': str(source),
            **bound, 'grid': [7, 8, 9], 'source_id': 'invalid-fixture-seal'})
        return self.controller(case, shared_source=source, mp_grid=mp_grid), source, bound

    def test_shared_source_uses_seal_bound_paths_without_overriding_grid(self):
        instance, source, bound = self.shared_controller()
        def command(key, tool, args):
            self.check_interface_args(instance, bound['scf_directory'], bound['band_directory'], key, tool, args)
            self.assertEqual(args[13:], ['--shared-source', str(source)])
            return {}
        self.assertEqual(self.advance(instance, command)['status'], 'execution_authorization_required')

    def core_prepare(self, key, tool, args):
        values = dict(zip(args[1::2], args[2::2]))
        grid = None
        if '--mp-grid' in args:
            index = args.index('--mp-grid')
            grid = list(map(int, args[index + 1:index + 4]))
        return interface.prepare_band(values['--band-dir'], values['--scf-dir'], values['--cache'],
            values['--analysis'], values['--decision'], values['--out-dir'],
            mp_grid=grid, shared_source=values['--shared-source'])

    def test_reading_seal_for_routing_does_not_bypass_core_verification(self):
        instance, source, bound = self.shared_controller()
        with patch.object(shared, 'verify', wraps=shared.verify) as verify:
            output = self.advance(instance, self.core_prepare)
        verify.assert_called_once_with(str(source), Path(bound['scf_directory']), Path(bound['band_directory']))
        self.assertEqual(output['status'], 'operation_requires_review')
        self.assertIn('Shared source seal identity mismatch', output['next_action'])
        self.assertFalse((instance.trial / 'interface').exists())

    def test_shared_grid_conflict_is_rejected_before_freezing_runtime_state(self):
        with self.assertRaisesRegex(ValueError, 'omit mp_grid'):
            self.shared_controller(mp_grid=[2, 3, 4])
        case = self.root / 'reused_dft'
        self.assertFalse((case / '.wannier_runtime').exists())
        self.assertFalse((case / '03_wan_trial/trial_001/interface').exists())


if __name__ == '__main__':
    unittest.main()
