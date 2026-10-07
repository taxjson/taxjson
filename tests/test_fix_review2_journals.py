"""Second pre-release review of the journals (v0.24.0):

- 1: the missing-history walks read a day's buys first ONLY on a day that
  holds a journal — the journal legs' own dates (a broker pair id, RBC's
  J~ reference, a .tt JOURNAL line) and, for a pair a journal (or a
  legacy ticker.map JOURNAL line) shows, a day with opposite trades of
  the same quantity on its two DIFFERENT listings. A TOBASE line alone
  is no journal: a sale with no purchase on one listing and a buy of the
  other stays missing history, and a sale-then-rebuy of one listing is
  never a journal;
- 2: the legs of a journal inside one account (a broker pair id, RBC's
  J~ reference, a .tt JOURNAL line) pair inside that account before any
  transfer of another account cancels a leg of the same symbol;
- 7: a .tt JOURNAL line bigger than the broker's journal between the
  same listings one business day away (or on its trades' day) is said;
- 12: a hub's partners of two corporate forms are not pooled; a class,
  warrant or venue suffix is not a listing root; RBC's J~ reference is
  read on RBC's rows only in the walks.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: 55500001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both

_USA_CAD = {"source_currencies": ["CAD"]}
_QT_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,"
           "Currency,Activity Type,Account #,Account Type\n")


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _run(test, root, *args, ok=True):
    r = cli(root, "run", "--no-input", *args)
    if ok:
        test.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _sum(test, root):
    r = cli(root, "sum", "--json")
    test.assertEqual(r.returncode, 0, _out(r)[-2000:])
    return json.loads(r.stdout)


def _short_reported(test, root, sym, said=None):
    """Whether the run, `sum` (its sales with no purchase, in a total or
    not) and find-missing-history each report the sale of `sym` as one
    with no purchase."""
    r = _run(test, root)
    if said is not None:
        said.append(_out(r))
    s = _sum(test, root)
    m = cli(root, "find-missing-history")
    return ("no purchase in your files" in _out(r),
            any(sym in json.dumps(x)
                for k in ("no_purchase_uncovered", "no_purchase_in_totals")
                for x in s.get(k) or []),
            sym in m.stdout)


# ------------------------------------------------------------------ 1

class TestTobaseAloneIsNoJournal(unittest.TestCase):

    def _check(self, country, tmap, sell, buy):
        files = {"ticker.map": tmap,
                 "inputs/margin/m.tt": (
                     f"BUYSELL 2025-03-03 09:00:00 {sell} -100 "
                     f"{'USD' if '.U.' in sell or sell.endswith('.US') else 'CAD'}"
                     f" 7.00 700.00 0.00\n"
                     f"BUYSELL 2025-03-03 10:00:00 {buy} 100 CAD 10.00 "
                     f"1000.00 0.00\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files, usa=_USA_CAD)[country]
            said = []
            got = _short_reported(self, root, buy, said)
            self.assertEqual(got, (True, True, True))
            # The line to add if the units were journaled.
            self.assertIn(f"`JOURNAL 2025-03-03 {buy} {sell} 100` (margin)",
                          said[0])

    @rule("CA-XLIST-04")
    def test_canada_currency_lines_tobase_hides_no_missing_purchase(self):
        self._check("canada", "TOBASE QZF.U.TO QZF.TO\n", "QZF.U.TO",
                    "QZF.TO")

    @rule("US-XLIST-03")
    def test_usa_currency_lines_tobase_hides_no_missing_purchase(self):
        self._check("usa", "TOBASE QZF.U.TO QZF.TO\n", "QZF.U.TO",
                    "QZF.TO")

    @rule("CA-XLIST-04")
    def test_canada_interlisted_tobase_hides_no_missing_purchase(self):
        self._check("canada", "TOBASE QZX.US QZX.TO\n", "QZX.US", "QZX.TO")

    @rule("US-XLIST-03")
    def test_usa_interlisted_tobase_hides_no_missing_purchase(self):
        self._check("usa", "TOBASE QZX.US QZX.TO\n", "QZX.US", "QZX.TO")


class TestJournalDaysOnly(unittest.TestCase):
    """A run-joined RBC gambit (J~ legs, no map line) is no short on its
    own days; a later sale-then-rebuy of one listing with no purchase
    before it is missing history."""

    def _files(self, later):
        from test_fix_dated_events import _rbc_gambit
        rbc, tmap = _rbc_gambit(True)
        files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap}
        if later:
            files["inputs/margin/m.tt"] = (
                "BUYSELL 2025-06-02 09:00:00 QZD.TO -500 CAD 14.00 "
                "7000.00 0.00\n"
                "BUYSELL 2025-06-02 10:00:00 QZD.TO 500 CAD 14.10 "
                "7050.00 0.00\n")
        return files

    def _check(self, country):
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for later in (False, True):
                root = projects_both(Path(td) / str(later),
                                     files=self._files(later),
                                     usa=_USA_CAD)[country]
                got[later] = _short_reported(self, root, "QZD")
            self.assertEqual(got[False], (False, False, False))
            self.assertEqual(got[True], (True, True, True))

    @rule("CA-XLIST-04")
    def test_canada_later_sale_and_rebuy_is_missing_history(self):
        self._check("canada")

    @rule("US-XLIST-03")
    def test_usa_later_sale_and_rebuy_is_missing_history(self):
        self._check("usa")


if __name__ == "__main__":
    unittest.main()
