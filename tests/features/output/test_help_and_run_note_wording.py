"""Re-audit-2 test pins, group G2 (parsers and message fixes).

Each test pins a fix that held on the audited commit but that no test
failed on when reverted (A2-0176, A2-0521, A2-0538, A2-0897, A2-1569,
A2-1582, A2-1612, A2-0894, A2-1597). Every fixture is SYNTHETIC: fake
account ids, invented tickers and amounts.

Formerly: test_fix_a2_pins3_g2
"""
import contextlib
import io
import tempfile
import unittest

from taxjson.project.settings import Settings
from taxjson.pipeline import run_state


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"



# ------------------------------------------------- A2-1597 message fixes
#
# Each fix below changed only a user-facing message, and reverting it
# left the suite green. Hunk ids are the audit's (commit_hunk).

import json
import os
import subprocess
import sys


def _tool_help(module, *args):
    """`python -m taxjson.bin.<module> <args> --help`, unwrapped."""
    env = {**os.environ, 'COLUMNS': '1000'}
    r = subprocess.run([sys.executable, '-m', f'taxjson.bin.{module}', *args,
                        '--help'], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL)
    return ' '.join(r.stdout.split())


class TestHelpAndNoteWording(unittest.TestCase):
    """A2-1597 hunks 16def71_589 (fees-sum help), c4126f0_651
    (missing-history --include-options help), 3977c2f_1207 (to_base_curr
    help: the Bank of Canada legacy noon rate), bfa3d37_1169 (gains
    --option-buyback-wash: either premium timing), 213fa9a_288
    (generate-parser PRIVACY notice), f48933e_913 (checklist t5-t3: the
    TAXABLE line).

    Formerly: test_fix_a2_pins3_g2.TestHelpAndNoteWording"""

    def test_fees_sum_help(self):
        self.assertIn('(default: tax year; trade dates); the totals '
                      '`tjs run` writes to reports/fees.rpt, by account '
                      'by default', _tool_help('taxjson_run', 'fees-sum'))

    def test_missing_history_include_options_help(self):
        self.assertIn('also include OCC option symbols and futures (a '
                      'negative position is normal sell-to-open, so skipped '
                      'by default — except a sale the broker codes CLOSING, '
                      'IB code C, which is always reported)',
                      _tool_help('taxjson_missing_history'))

    def test_to_base_curr_help(self):
        self.assertIn('(from 2017-01-03; its legacy noon rate before '
                      '2017-03-01, back to 2007-05-01); Yahoo Finance fills '
                      'earlier dates', _tool_help('to_base_curr'))

    def test_gains_buyback_wash_help(self):
        self.assertIn('Canada, either premium timing: treat the loss on '
                      'buying back a written option',
                      _tool_help('taxjson_gains'))

    def test_generate_parser_privacy_notice(self):
        self.assertIn('PRIVACY: the first --sample-lines lines of the file '
                      'are sent to the provider. Run `tjs redact` on a '
                      'real export first', _tool_help('taxjson_generate_parser'))

    def test_checklist_t5_t3_names_the_taxable_line(self):
        from taxjson.lib.checklist import DETECTORS
        self.assertIn('the TAXABLE line of `tjs divs-sum` / `roc-sum`',
                      DETECTORS['t5-t3'](None).detail)


class TestRunNoteWording(unittest.TestCase):
    """A2-1597 hunks f0c339f_814 (scan: unused ticker.map rules checked
    rename chains), d7a26de_462 (US estimate assumptions: the §988
    result is not included), 4dffc4b_1026 (init --force names the
    backup it actually wrote).

    Formerly: test_fix_a2_pins3_g2.TestRunNoteWording"""

    def test_unused_rule_names_rename_chains(self):
        # `scan`'s unused-rule note is `ticker-map --suggest`'s list now.
        from _support.qt_margin_views import _run_cli
        from _support.tt_holdings_projects import _scan_project
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [('XEI.TO', 10)],
                                 ticker_map='TOBASE AAQ.US AAQ.TO\n')
            (root / 'work' / 'margin_questrade.json').write_text(json.dumps(
                {'transactions': [{'symbol': 'XEI.TO',
                                   'action': 'BUYSELL'}]}))
            # (each rule's reason: --details, docs/output-style.md)
            r = _run_cli(root, 'ticker-map', '--suggest', '--details')
        # Each rule is listed as written, its reason under it.
        self.assertIn('Unused rules, delete? (1)', r.stdout)
        self.assertIn('\nTOBASE AAQ.US AAQ.TO\n', r.stdout)
        self.assertIn('(stock rows, option roots and rename chains '
                      'checked)', ' '.join(r.stdout.split()))

    def test_us_estimate_assumptions_exclude_section_988(self):
        from unittest import mock

        from taxjson.cli.estimate import _print_tax_estimate
        out = io.StringIO()
        # The assumptions are printed with --details (Essentials first).
        with contextlib.redirect_stdout(out), \
                mock.patch.object(run_state, "current_details", True):
            _print_tax_estimate(
                {'settings': Settings.from_table({'country': 'usa', 'year': 2025})},
                {'realized': 1000.0, 'st': 1000.0, 'lt': 0.0, 'div_ca': 0.0,
                 'div_foreign': 0.0, 'pil': 0.0}, 'USD',
                other_income=50000.0, other_losses=0.0, province=None)
        self.assertIn('interest income and the §988 result on foreign '
                      'currency (`tjs fx-cash`) not included',
                      ' '.join(out.getvalue().split()))

    def test_init_force_names_the_numbered_backup(self):
        with tempfile.TemporaryDirectory() as td:
            base = [sys.executable, '-m', 'taxjson.bin.taxjson_run', 'init',
                    '--single', td, '--year', '2025']
            run = dict(capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
            subprocess.run(base + ['--country', 'ca'], check=True, **run)
            subprocess.run(base + ['--country', 'us', '--force'],
                           check=True, **run)
            r = subprocess.run(base + ['--country', 'ca', '--force'], **run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('wrote taxjson.toml.bak1', r.stdout)
        # One line in the default view (docs/output-style.md, Essentials
        # first).
        self.assertIn('re-add (see taxjson.toml.bak1)',
                      " ".join(r.stdout.split()).lower())


if __name__ == '__main__':
    unittest.main()
