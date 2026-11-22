"""Second-audit DEFERRED findings, the rest (A2-0590, A2-1014, A2-1091,
A2-1052/A2-1054, A2-1056). Every fixture is synthetic: invented tickers, fake
account ids and made-up amounts.

Formerly: test_fix_def_rest"""
import tempfile
import unittest
from pathlib import Path

from _support.parser_blank_settle_and_renames import QT_RENAME, WB_RENAME, _brokerage
from _support.rbc_rows import row as rbc_row


# ------------------------------------------ A2-1056: hints the map answers
import os

from _support.parser_qt_rbc_rows import QH
from _support.rbc_rows import HDR as RBC_HDR, RQA_ROWS


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestTickerChangeHintMootOnceMapped(unittest.TestCase):
    """A2-1056: the Questrade, RBC and Webull ticker-change hints (and
    RBC's untraded-income listing hint) kept printing after the
    suggested ticker.map line was added: the parsers never saw the map.
    taxjson-brokerage --ticker-map (passed by run) drops a hint whose
    pair the map already joins; --lint keeps it.

    Formerly: test_fix_def_rest.TestTickerChangeHintMootOnceMapped"""

    CASES = (
        ('questrade', {'questrade_2025.csv': QH + QT_RENAME},
         'GLOBAL QQOL.US QQNW.US', 'looks renamed'),
        ('rbc', {'rbc.csv': RBC_HDR + ''.join(RQA_ROWS)},
         'GLOBAL RQA.US RQB.US', 'looks renamed'),
        ('webull', {'wb.csv': WB_RENAME},
         'RENAME QQOL.US QQNW.US 2025-12-06', 'ticker change Webull'),
    )

    def test_hint_without_a_map(self):
        for brk, files, _line, hint in self.CASES:
            rc, err = _brokerage(brk, files)
            self.assertEqual(rc, 0, err)
            self.assertIn(hint, err, brk)

    def test_hint_dropped_once_the_map_joins_the_pair(self):
        for brk, files, line, hint in self.CASES:
            rc, err = _brokerage(brk, files, line + '\n')
            self.assertEqual(rc, 0, err)
            self.assertNotIn(hint, err, brk)

    def test_dated_rename_and_chain_also_join(self):
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'RENAME QQOL.US QQNW.US 2025-09-01\n')
        self.assertNotIn('looks renamed', err)
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'GLOBAL QQOL.US QQMID.US\n'
                             'GLOBAL QQNW.US QQMID.US\n')
        self.assertNotIn('looks renamed', err)

    def test_unrelated_map_line_keeps_the_hint(self):
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'GLOBAL QQOL.US QQOTHER.US\n')
        self.assertIn('looks renamed', err)

    def test_lint_keeps_the_hint(self):
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'GLOBAL QQOL.US QQNW.US\n', '--lint')
        self.assertIn('looks renamed', err)

    def test_rbc_untraded_income_hint_moot_once_mapped(self):
        div = rbc_row("June 2, 2025", "Dividend", "QZT", "QZT CORP", "", "",
                      "12.00", "USD", "QZT CORP RETURN OF CAPITAL")
        rc, err = _brokerage('rbc', {'rbc.csv': RBC_HDR + div})
        self.assertIn('no trade rows for QZT', err)
        rc, err = _brokerage('rbc', {'rbc.csv': RBC_HDR + div},
                             'TOBASE QZT.US QZT.TO\n')
        self.assertNotIn('no trade rows for QZT', err)

    def test_questrade_internal_code_hint_moot_once_mapped(self):
        from _support.parser_qt_rbc_rows import qdiv
        roc = qdiv('X000008', 'OTHERCO INC RETURN OF CAPITAL ON 100 SHS '
                   'REC 01/15/26 PAY 02/01/26', '700.00', cur='CAD')
        rc, err = _brokerage('questrade', {'q.csv': QH + roc})
        self.assertIn('keeps internal symbol code', err)
        rc, err = _brokerage('questrade', {'q.csv': QH + roc},
                             'GLOBAL X000008.TO QZO.TO\n')
        self.assertNotIn('keeps internal symbol code', err)

    def test_rbc_usd_units_hint_names_the_tsx_spelling(self):
        for sym in ('QZS', 'QZS.U', 'QZS.U.TO'):
            buy = rbc_row("March 3, 2025", "Buy", sym,
                          "QZ S&P FUND US DOLLAR UNITS", "10", "10", "-100",
                          "USD", "QZ S&P FUND US DOLLAR UNITS UNSOLICITED")
            rc, err = _brokerage('rbc', {'rbc.csv': RBC_HDR + buy})
            self.assertIn('QZS.U.TO', err, sym)
            self.assertNotIn('.U.TO.U.TO', err, sym)
            self.assertNotIn('.U.U.TO', err, sym)

    def test_run_passes_the_map(self):
        from _support.cli_margin_project import _project, _cli_run
        with tempfile.TemporaryDirectory() as d:
            body = QT_RENAME.replace(',USD,', ',CAD,')
            p = _project(Path(d) / 'p', 2025,
                         {'questrade_2025.csv': QH + body},
                         ticker_map='GLOBAL QQOL.TO QQNW.TO\n')
            r = _cli_run(p, 'run', '--no-input')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('looks renamed', r.stdout + r.stderr)
            sums = ''.join(f.read_text() for f in
                           (p / 'reports').rglob('*.sum'))
            self.assertTrue(sums)
            self.assertNotIn('looks renamed', sums)


if __name__ == "__main__":
    unittest.main()
