"""Check the scalar parsing optimization against the prior NumPy predicates."""
from pathlib import Path
import sys
import unittest
import warnings

import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / 'wannier_mcp/workflow/tools'
sys.path.insert(0, str(TOOLS))
import prepare_wannier_interface as interface


def previous_numbers(line, count):
    values = [float(v.replace('D', 'E').replace('d', 'e')) for v in line.split()]
    if len(values) != count or not np.isfinite(values).all():
        raise ValueError('Malformed/nonfinite numerical interface row.')
    return values


def previous_mmn(line):
    values = [float(v.replace('D', 'E').replace('d', 'e')) for v in line.split()]
    if len(values) not in (2, 3) or not np.isfinite(values).all():
        raise ValueError('Malformed/nonfinite MMN matrix row.')
    if len(values) == 3 and not np.isclose(values[2], np.hypot(*values[:2]),
                                             atol=1e-9, rtol=1e-10):
        raise ValueError('MMN magnitude column disagrees with complex overlap.')
    return values[:2]


def outcome(function, *args):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        try:
            return ('ok', function(*args))
        except ValueError as exc:
            return ('error', str(exc))


class NumericRows(unittest.TestCase):
    def test_custom_error_traps_and_default_warnings_keep_prior_behavior(self):
        lines = ['1e-310 0 0', '-1.7976931348623157e308 0 1.7976931348623157e308',
                 '1.7976931348623157e308 1.7976931348623157e308 1',
                 '.3 -.4 .5', '3 4 5']
        def capture(fn, line):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                try:
                    result = ('ok', fn(line))
                except (ValueError, FloatingPointError) as exc:
                    result = (type(exc).__name__, str(exc))
                return result, [(v.category.__name__, str(v.message)) for v in caught]
        for policy in ['ignore', 'warn', 'raise']:
            with np.errstate(all=policy):
                for line in lines:
                    self.assertEqual(capture(previous_mmn, line),
                                     capture(interface.finite_mmn_numbers, line), (policy, line))
        with np.errstate(divide='warn', over='warn', under='ignore', invalid='warn'):
            for line in lines:
                self.assertEqual(capture(previous_mmn, line),
                                 capture(interface.finite_mmn_numbers, line))

    def test_required_columns_fortran_exponents_and_nonfinite_rows(self):
        lines = ['', '1', '1 2', '1 2 3', '-0.0 +0.0', '1D-10 -2d+1',
                 'nan 1', '1 inf', '1 -inf', '1E400 2', 'invalid 2']
        for line in lines:
            for count in (0, 1, 2, 3, 4):
                self.assertEqual(outcome(previous_numbers, line, count),
                                 outcome(interface.finite_numbers, line, count))
            self.assertEqual(outcome(previous_mmn, line),
                             outcome(interface.finite_mmn_numbers, line))

    def test_optional_magnitude_matches_prior_threshold_at_adjacent_floats(self):
        for a, b in [(0., 0.), (.3, -.4), (1e-300, -2e-300),
                     (1e-10, -2e-10), (1e10, -2e10), (1e308, 1e308)]:
            magnitude = float(np.hypot(a, b))
            tolerance = 1e-9 + 1e-10 * magnitude
            candidates = [magnitude, -magnitude]
            for edge in [magnitude - tolerance, magnitude + tolerance]:
                candidates.extend([np.nextafter(edge, -np.inf), edge,
                                   np.nextafter(edge, np.inf)])
            for c in candidates:
                line = f'{a:.17e} {b:.17e} {c:.17e}'
                self.assertEqual(outcome(previous_mmn, line),
                                 outcome(interface.finite_mmn_numbers, line), line)

    def test_finite_complex_pair_with_overflowing_magnitude_stays_rejected(self):
        # Finite input components may have an infinite hypot. A naive
        # inf > inf comparison would incorrectly accept the diagnostic.
        line = '1.7976931348623157e308 1.7976931348623157e308 1.0'
        self.assertEqual(outcome(previous_mmn, line),
                         outcome(interface.finite_mmn_numbers, line))
        self.assertEqual(outcome(interface.finite_mmn_numbers, line)[0], 'error')

    def test_random_complex_rows_keep_exact_outputs_and_rejection_messages(self):
        generator = np.random.default_rng(9141)
        for _ in range(500):
            a, b = generator.normal(size=2) * 10. ** generator.uniform(-250, 250)
            m = float(np.hypot(a, b))
            for c in [m, m + (1e-9 + 1e-10 * m) * generator.uniform(-2, 2)]:
                line = f'{a:.17e} {b:.17e} {c:.17e}'
                self.assertEqual(outcome(previous_mmn, line),
                                 outcome(interface.finite_mmn_numbers, line))
