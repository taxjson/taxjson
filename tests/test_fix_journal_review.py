"""Pre-release review of the dated-event journals (v0.24.0): a JOURNAL
moves units between two listings INSIDE one account, so

- H1: its legs (a .tt JOURNAL line's, a broker's journal pair) never
  pair with another account's transfer — not as a move of your own,
  not as the end of an arrival from outside the books, not as an
  in-kind move to or from a plan;
- H7: an option contract is never journaled or renamed into a stock;
- M2: a .tt JOURNAL joins two listings only when something shows they
  are one security (one root, names that agree, a TOBASE line);
- M5: a listing joined to two others through a hub joins them only when
  their own names agree with each other;
- M6: migrating a ticker.map JOURNAL line to TOBASE changes nothing in
  the missing-history walks;
- the lows: trailing corporate forms only, one record per currency
  journal, RBC's J~ reference on RBC's rows only, identical .tt lines,
  a partial duplicate of the broker's journal, future dates and absurd
  quantities, a malformed cross_listings.state.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: U5550001, 55500001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both

_QT_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,"
           "Currency,Activity Type,Account #,Account Type\n")
_TWO = '[accounts.a]\ntype = "taxable"\n\n[accounts.b]\ntype = "taxable"\n'
_USA_CAD = {"source_currencies": ["CAD"]}


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _total(test, root):
    r = cli(root, "sum", "--json")
    test.assertEqual(r.returncode, 0, _out(r)[-2000:])
    return json.loads(r.stdout)["totals"]["total"]


def _run(test, root, *args, ok=True):
    r = cli(root, "run", "--no-input", *args)
    if ok:
        test.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


# ------------------------------------------------------------------ H1

def _arrival_files(with_journal):
    """Account a receives 100 QZG.TO from outside the books (Questrade
    states a book value of 5000) and sells them; account b bought 100 at
    2000 and journals them to QZG.U.TO the same day."""
    qa = _QT_HDR + (
        "2025-03-05,2025-03-05,TF6,QZG.TO,QZG CORP TRANSFER BOOK VALUE "
        "5000.00,100,0.00,0.00,0.00,0.00,CAD,Transfers,55500001,Margin\n"
        "2025-04-01,2025-04-02,Sell,QZG.TO,QZG CORP,-100,80.00,8000.00,"
        "0.00,8000.00,CAD,Trades,55500001,Margin\n")       # pii-ok
    tt = "BUYSELL 2025-01-03 10:00:00 QZG.TO 100 CAD 20.00 2000.00 0.00\n"
    if with_journal:
        tt += "JOURNAL 2025-03-05 QZG.TO QZG.U.TO 100\n"
    return {"inputs/a/qt.csv": qa, "inputs/b/b.tt": tt}


class TestJournalLegsStayInTheirAccount(unittest.TestCase):

    def _check_arrival(self, country):
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for wj in (False, True):
                root = projects_both(Path(td) / str(wj), accounts=_TWO,
                                     files=_arrival_files(wj),
                                     usa=_USA_CAD)[country]
                r = _run(self, root, "--strict")
                got[wj] = _total(self, root)
                if wj:
                    # a's arrival keeps its book value: b's journal is
                    # b's own move.
                    self.assertIn("booked at the", _out(r))
            self.assertAlmostEqual(got[True], got[False], delta=0.011)

    @rule("CA-XLIST-04")
    def test_canada_tt_journal_never_cancels_another_accounts_arrival(self):
        self._check_arrival("canada")

    @rule("US-XLIST-03")
    def test_usa_tt_journal_never_cancels_another_accounts_arrival(self):
        self._check_arrival("usa")

    def _inkind_files(self, with_journal):
        from test_feat_in_kind import _ib
        files = {"inputs/margin/ib.csv": _ib([("2025-01-10", 100, 10.0)],
                                             [("2025-03-14", -100, 1500)]),
                 "inputs/rrsp/r.tt": (
                     "BUYSELL 2025-02-03 10:00:00 QZK.U.TO 100 USD 7.00 "
                     "700.00 0.00\n")}
        if with_journal:
            files["inputs/rrsp/r.tt"] += \
                "JOURNAL 2025-03-17 QZK.U.TO QZK.TO 100\n"
        return files

    def _check_inkind(self, country):
        acc = ('[accounts.margin]\ntype = "taxable"\n\n'
               '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n')
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for wj in (False, True):
                root = projects_both(Path(td) / str(wj), accounts=acc,
                                     files=self._inkind_files(wj),
                                     usa=_USA_CAD)[country]
                r = _run(self, root)
                self.assertNotIn("in-kind move", _out(r))
                got[wj] = _total(self, root)
            self.assertAlmostEqual(got[True], got[False], delta=0.011)
            self.assertAlmostEqual(got[True], 0.0, delta=0.011)

    @rule("CA-XLIST-04")
    def test_canada_plan_journal_is_not_an_in_kind_contribution(self):
        self._check_inkind("canada")

    @rule("US-XLIST-03")
    def test_usa_plan_journal_is_not_an_in_kind_move(self):
        self._check_inkind("usa")


class TestOwnJournalLegs(unittest.TestCase):
    """lib/transfer_in.own_journal_legs: the pure pairing."""

    def _row(self, sym, q, day="2025-03-05", pair="", desc=""):
        return {"action": "TRANSFER", "symbol": sym, "quantity": q,
                "date": day, "journal_pair": pair, "description": desc}

    def test_pairs_settle_in_their_account_only(self):
        from taxjson.lib import transfer_in as TI
        rows = [("b", "tt-journal", self._row("QZG.TO", -100, pair="p1")),
                ("b", "tt-journal", self._row("QZG.U.TO", 100, pair="p1")),
                ("a", "questrade", self._row("QZG.TO", 100))]
        self.assertEqual(TI.own_journal_legs(rows), ({0, 1}, set()))
        found = TI.arrivals(rows)
        self.assertEqual([(x.account, x.symbol, x.quantity) for x in found],
                         [("a", "QZG.TO", 100.0)])
        self.assertEqual(TI.movable_rows(rows), [rows[2]])

    def test_one_legged_line_settles_the_broker_leg_beside_it(self):
        from taxjson.lib import transfer_in as TI
        rows = [("m", "ib", self._row("QZD.TO", -50, day="2025-05-06")),
                ("m", "tt-journal", self._row("QZD.U.TO", 50, day="2025-05-07",
                                                pair="p")),
                ("o", "ib", self._row("QZD.TO", 50, day="2025-05-06"))]
        settled, orphans = TI.own_journal_legs(rows)
        self.assertEqual((settled, orphans), ({0, 1}, set()))
        # o's in-leg is an arrival: m's broker leg is m's journal's.
        self.assertEqual([x.account for x in TI.arrivals(rows)], ["o"])

    def test_an_orphan_leg_cancels_its_own_account_only(self):
        from taxjson.lib import transfer_in as TI
        rows = [("m", "questrade", self._row("QZD.TO", -50, pair="q1")),
                ("o", "questrade", self._row("QZD.TO", 50))]
        self.assertEqual(TI.own_journal_legs(rows), (set(), {0}))
        self.assertEqual([x.account for x in TI.arrivals(rows)], ["o"])
        self.assertEqual(TI.movable_rows(rows), [rows[1]])

    def test_rbc_reference_is_read_on_rbc_rows_only(self):
        from taxjson.lib.missing_history import journal_leg_key
        tfr = {"action": "TRANSFER", "date": "2025-05-06",
               "description": "TFR - QZD UNIT TRANSFER TO U$  J~0TFR1Q"}
        self.assertEqual(journal_leg_key(tfr),
                         ("ref", "2025-05-06", "0TFR1Q"))
        self.assertEqual(journal_leg_key(tfr, broker="rbc_direct"),
                         ("ref", "2025-05-06", "0TFR1Q"))
        # Another broker's row, or text that is no TFR row: no reference.
        self.assertIsNone(journal_leg_key(tfr, broker="questrade"))
        self.assertIsNone(journal_leg_key(dict(
            tfr, description="QZD MEMO J~0TFR1Q")))


# ------------------------------------------------------------------ H7

_OPT = {"canada": "QZK250620C00010000.TO", "usa": "QZK250620C00010000.US"}
_STK = {"canada": "QZK.TO", "usa": "QZK.US"}


class TestNoOptionJournalOrRename(unittest.TestCase):

    def test_parse_refuses_contracts(self):
        from taxjson.bin.taxjson_convert_tt import (parse_journal_line,
                                                    parse_rename_line)
        for bad in ("JOURNAL 2025-03-05 QZK250620C00010000.TO QZK.TO 1",
                    "JOURNAL 2025-03-05 QZK.TO QZK250620C00010000.TO 1",
                    "JOURNAL 2025-03-05 QZK.US F:QZK.US 1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                parse_journal_line(bad, "x.tt:4")
            self.assertIn("x.tt:4", str(cm.exception))
            self.assertIn("never journaled", str(cm.exception))
        for bad, word in (
                ("RENAME 2025-04-01 QZK250620C00010000.US QZK.US",
                 "into a share listing"),
                ("RENAME 2025-04-01 QZK.US QZK250620C00010000.US",
                 "from a share listing"),
                ("RENAME 2025-04-01 QZO250620C00010000.US "
                 "QZN250620C00010000.US", "RENAME 2025-04-01 QZO.US QZN.US"),
                ("RENAME 2025-04-01 QZO250620C00010000.US "
                 "QZO250620C00012000.US", "another contract")):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                parse_rename_line(bad, "x.tt:2")
            self.assertIn(word, str(cm.exception))
        # Two share listings still parse.
        self.assertEqual(parse_rename_line("RENAME 2025-04-01 QZO.US "
                                           "QZN.US")["new"], "QZN.US")

    def _check(self, country, line, word):
        tt = (f"BUYSELL 2025-03-03 10:00:00 {_OPT[country]} 1 "
              f"{'CAD' if country == 'canada' else 'USD'} 1.00 100.00 0.00 "
              f"x100\n" + line.format(o=_OPT[country], s=_STK[country]))
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": tt})[country]
            r = _run(self, root, ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot be booked", _out(r))
            self.assertIn(word, _out(r))
            self.assertIn("inputs/margin/m.tt:2", _out(r))

    @rule("CA-XLIST-04")
    def test_canada_option_journal_stops_the_run(self):
        self._check("canada", "JOURNAL 2025-03-05 {o} {s} 1\n",
                    "never journaled")

    @rule("US-XLIST-03")
    def test_usa_option_journal_stops_the_run(self):
        self._check("usa", "JOURNAL 2025-03-05 {o} {s} 1\n",
                    "never journaled")

    @rule("CA-ACB-RENAME")
    def test_canada_option_rename_stops_the_run(self):
        self._check("canada", "RENAME 2025-04-01 {o} {s}\n",
                    "into a share listing")

    @rule("US-BASIS-RENAME")
    def test_usa_option_rename_stops_the_run(self):
        self._check("usa", "RENAME 2025-04-01 {o} {s}\n",
                    "into a share listing")


if __name__ == "__main__":
    unittest.main()
