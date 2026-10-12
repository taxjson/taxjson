"""taxjson-sum-gains surfaces a non-converged wash-sale solver.

The engine warns on stderr at compute time and writes
`summary.wash_solver_converged` into the gains JSON, but a user reading
only the summary report had no signal the numbers might be inconsistent.
`summarize_gains` now carries the flag through and `format_report` prints
a prominent banner when it is explicitly False (missing flag / converged
=> no banner, so US-engine and older files never spuriously warn).
"""
import unittest

from taxjson.bin.taxjson_sum_gains import format_report, summarize_gains

_BASE = {'ticker_stats': {}, 'total_year': 'all'}
_BANNER = 'did NOT converge'


class TestWashConvergenceBanner(unittest.TestCase):
    def test_banner_when_not_converged(self):
        out = format_report(
            {**_BASE, 'wash_solver_converged': False, 'wash_solver_iterations': 1000},
            no_color=True,
        )
        self.assertIn(_BANNER, out)
        self.assertIn('1000 iterations', out)

    def test_no_banner_when_converged(self):
        out = format_report({**_BASE, 'wash_solver_converged': True}, no_color=True)
        self.assertNotIn(_BANNER, out)

    def test_no_banner_when_flag_absent(self):
        # US engine / older gains files don't emit the flag — must not warn.
        out = format_report(_BASE, no_color=True)
        self.assertNotIn(_BANNER, out)

    def test_summarize_gains_carries_flag(self):
        rd = summarize_gains({
            'transactions': [],
            'summary': {'wash_solver_converged': False, 'wash_solver_iterations': 7},
        })
        self.assertIs(rd['wash_solver_converged'], False)
        self.assertEqual(rd['wash_solver_iterations'], 7)

    def test_summarize_gains_defaults_converged_true(self):
        rd = summarize_gains({'transactions': [], 'summary': {}})
        self.assertIs(rd['wash_solver_converged'], True)


if __name__ == '__main__':
    unittest.main()
