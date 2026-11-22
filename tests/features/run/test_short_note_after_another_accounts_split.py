"""Regression tests for fix round 7 (wave E), group shorts: a marked short
beside an own move's shares, a written option's premium in the holdings views
under close timing, the run's no-total note and holdings report after a split
one account reports, and edge-cases' HELD DAY 30 for a short holder.

Synthetic data only; account labels and tickers are made up.

Formerly: test_fix_1029_r7e_shorts
"""
import tempfile
import unittest
from _qa_project import console, flat, project, tj
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_MARGIN = ("BUYSELL 2024-01-10 10:00:00 ZZS.TO 100 CAD 10.00 1000.00\n"
           "SPLIT 2024-03-01 09:30:00 ZZS.TO ZZS.TO 2\n")
_QT_ROWS = ("BUYSELL 2024-02-10 10:00:00 ZZS.TO 100 CAD 8.00 800.00\n"
            "BUYSELL 2024-04-10 10:00:00 ZZS.TO -150 CAD 12.00 1800.00\n"
            "BUYSELL 2024-06-10 10:00:00 ZZS.TO 80 CAD 11.00 880.00\n")
_QT_ACCT = '[accounts.qt]\ntype = "taxable"\n'


class TestRunNoteAfterAnotherAccountsSplit(unittest.TestCase):
    """TJ-1258: the run's short note reads the split another taxable
    account reports, as the engine and find-missing-history do.

    Formerly: test_fix_1029_r7e_shorts.TestRunNoteAfterAnotherAccountsSplit"""

    def _note(self, margin=_MARGIN, qt=_QT_ROWS, extra=_QT_ACCT, more=None):
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/f.tt": margin, "qt/f.tt": qt,
                                     **(more or {})}, extra_accounts=extra)
            return flat(console(tj(root, "run", "--no-input")))

    def test_no_note_when_the_split_covers_the_sale(self):
        text = self._note()
        self.assertNotIn("go short in qt's data", text)
        self.assertNotIn("in no total", text)

    def test_a_real_short_past_the_split_keeps_its_note(self):
        qt = ("BUYSELL 2024-02-10 10:00:00 ZZS.TO 10 CAD 8.00 80.00\n"
              "BUYSELL 2024-04-10 10:00:00 ZZS.TO -50 CAD 12.00 600.00\n"
              "BUYSELL 2024-06-10 10:00:00 ZZS.TO 80 CAD 11.00 880.00\n")
        self.assertIn("go short in qt's data", self._note(qt=qt))

    def test_a_split_of_a_registered_account_is_not_spread(self):
        """TJ-1022: the books apply it to that book only."""
        tfsa = _QT_ACCT + '[accounts.tfsa]\ntype = "sheltered"\n'
        text = self._note(
            margin="BUYSELL 2024-01-10 10:00:00 ZZS.TO 100 CAD 10.00 1000.00\n",
            extra=tfsa,
            more={"tfsa/f.tt": _MARGIN})
        self.assertIn("go short in qt's data", text)


if __name__ == '__main__':
    unittest.main()
