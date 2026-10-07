"""IB one-contract-id ticker changes (pre-release review of v0.24.0):
the change is booked only when the contract id's own rows date it.

- H4: the change is booked at NEW's earliest row of ANY section (a
  corporate action, a return of capital, a dividend), not its first
  trade: the split / return of capital before that trade is kept (both
  countries).
- H6: a corporate action that names OLD and NEW together (a split that
  changes the symbol) is IB's own rename: no contract-id event on top.
- M1: dates per (contract id, symbol): another company that used the
  ticker (its own contract id) never dates the change; a symbol listed
  under two contract ids in one statement, or NEW's rows starting on or
  before OLD's last row of any section, is not booked (ATTENTION with
  the .tt line).
- LOW: a .tt RENAME of OLD to another symbol stops the booking; IB's
  `.OLD` placeholder is never a rename target.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: U5550001).
"""
import json
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import cli, projects_both
from test_fix_ibparse import (CA_H, DIV_H, FII_H, HEAD, TRADES_H, _ca,
                              _trade)

_ONE_ID = ('Financial Instrument Information,Data,Stocks,"QZNB, QZOA",'
           'QZNB CORP,990000779,US9990007791,,NYSE,1,,,COMMON,,\n')
_NEW_ONLY = ('Financial Instrument Information,Data,Stocks,"QZNB",'
             'QZNB CORP,990000779,US9990007791,,NYSE,1,,,COMMON,,\n')
_OTHER_CO = ('Financial Instrument Information,Data,Stocks,"QZNB",'
             'QZNB OLDCO,990000111,US9990001111,,NYSE,1,,,COMMON,,\n')
_SPLIT2 = 'QZNB(US9990007791) Split 2 for 1 (QZNB, QZNB CORP, US9990007791)'
_ROC = ('Dividends,Data,USD,2025-06-02,QZNB(US9990007791) Cash Dividend '
        'USD 1.00 per Share (Return of Capital),100\n')


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _booked(out):
    """The contract id's 'booked as a ticker change' Warning (the console
    starts the line's second part with a capital)."""
    return ("Booked as a ticker change" in out
            or "— booked as a ticker change" in out)


def _run(files, country, *extra):
    """(project, CompletedProcess of `run --no-input --strict`)."""
    td = tempfile.TemporaryDirectory()
    root = projects_both(td.name, files=files)[country]
    r = cli(root, "run", "--no-input", "--strict", *extra)
    return td, root, r


def _gain(root):
    r = cli(root, "sum", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)["accounts"][0]["realized"]


def _events(root):
    return [(e["old"], e["new"], e["date"], e["source"]) for e in
            json.loads(cli(root, "renames", "--json").stdout)["renames"]]


def _h4(new_first, old_sym="QZOA", fii=_ONE_ID, sale=600):
    """QZOA bought 100 @10; `new_first` (a NEW row before NEW's first
    trade); QZNB 100 sold on 2025-08-01."""
    return (HEAD + TRADES_H
            + _trade(old_sym, '2025-01-10, 10:00:00', 100, 10, -1000)
            + _trade('QZNB', '2025-08-01, 10:00:00', -100, sale / 100,
                     sale, code='C')
            + new_first + FII_H + fii)


_H4_SPLIT = CA_H + _ca(_SPLIT2, 100, when='2025-06-02, 20:25:00')
_H4_ROC = DIV_H + _ROC
_H4_DIV = DIV_H + ('Dividends,Data,USD,2025-06-02,QZNB(US9990007791) Cash '
                   'Dividend USD 0.25 per Share (Ordinary Dividend),25\n')


class TestNewEventBeforeNewFirstTrade(unittest.TestCase):
    """H4: the change was booked on NEW's first TRADE, after NEW's split
    (or return of capital): the split was lost (US -400 for +100). It is
    booked at 00:00 on NEW's earliest row of ANY section now."""

    def _check(self, country, new_first, sale):
        files = {"inputs/margin/ib.csv": _h4(new_first, sale=sale)}
        td, root, r = _run(files, country)
        with td:
            out = _out(r)
            self.assertEqual(r.returncode, 0, out[-3000:])
            self.assertTrue(_booked(out), out[-2000:])
            self.assertEqual(_events(root), [("QZOA.US", "QZNB.US",
                                              "2025-06-02", "ib-conid")])
            got = _gain(root)
        # The same gain as a book where the shares always were QZNB.
        ref_files = {"inputs/margin/ib.csv": _h4(new_first, "QZNB",
                                                 _NEW_ONLY, sale)}
        td, ref, r = _run(ref_files, country)
        with td:
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertAlmostEqual(got, _gain(ref), places=2)
        return got

    @rule("CA-ACB-RENAME")
    def test_canada_split_before_first_trade(self):
        self.assertGreater(self._check("canada", _H4_SPLIT, 600), 0)

    @rule("US-BASIS-RENAME")
    def test_usa_split_before_first_trade(self):
        self.assertAlmostEqual(self._check("usa", _H4_SPLIT, 600), 100.0,
                               places=2)

    @rule("CA-ACB-RENAME")
    def test_canada_return_of_capital_before_first_trade(self):
        self._check("canada", _H4_ROC, 1200)

    @rule("US-BASIS-RENAME")
    def test_usa_return_of_capital_before_first_trade(self):
        self.assertAlmostEqual(self._check("usa", _H4_ROC, 1200), 300.0,
                               places=2)

    @rule("CA-ACB-RENAME")
    def test_canada_dividend_before_first_trade(self):
        self._check("canada", _H4_DIV, 1200)

    @rule("US-BASIS-RENAME")
    def test_usa_dividend_before_first_trade(self):
        self.assertAlmostEqual(self._check("usa", _H4_DIV, 1200), 200.0,
                               places=2)


def _h6(ca_when):
    """IB's own renaming reverse split QZOA 1-for-10 into QZNB (the two
    legs at `ca_when`), the QZNB sale of 10 on 2025-03-03 10:00, one
    contract id listed as "QZNB, QZOA"."""
    return (HEAD + TRADES_H
            + _trade('QZOA', '2025-01-10, 10:00:00', 100, 10, -1000)
            + _trade('QZNB', '2025-03-03, 10:00:00', -10, 120, 1200,
                     code='C')
            + CA_H
            + _ca('QZOA(US9990007791) Split 1 for 10 (QZNB, QZNB CORP, '
                  'US9990007792)', 10, when=ca_when)
            + _ca('QZOA(US9990007791) Split 1 for 10 (QZOA.OLD, QZNB CORP, '
                  'US9990007791)', -100, when=ca_when)
            + FII_H + _ONE_ID)


class TestCorporateActionRenames(unittest.TestCase):
    """H6: the contract-id event was booked on top of IB's own renaming
    split: 90 phantom shares (silent when the split row came after the
    first QZNB trade)."""

    def _before(self, country):
        td, root, r = _run({"inputs/margin/ib.csv":
                            _h6('2025-03-02, 20:25:00')}, country)
        with td:
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertFalse(_booked(_out(r)))
            evs = _events(root)
            self.assertEqual([(e[0], e[1], e[3]) for e in evs],
                             [("QZOA.US", "QZNB.US", "broker")])
            h = (root / "reports" / "margin_holdings.toml").read_text()
            self.assertNotIn("QZNB.US", h)      # 10 in, 10 sold
            return _gain(root)

    @rule("CA-ACB-RENAME")
    def test_canada_split_renames_once(self):
        self.assertGreater(self._before("canada"), 0)

    @rule("US-BASIS-RENAME")
    def test_usa_split_renames_once(self):
        self.assertAlmostEqual(self._before("usa"), 200.0, places=2)

    def _after(self, country):
        # IB's split row AFTER the first QZNB trade: the corporate-action
        # path's own dating; no contract-id event papers over it.
        td, root, r = _run({"inputs/margin/ib.csv":
                            _h6('2025-03-04, 20:25:00')}, country)
        with td:
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("ib-conid", [e[3] for e in _events(root)])

    @rule("CA-ACB-RENAME")
    def test_canada_split_after_first_trade_not_papered_over(self):
        self._after("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_split_after_first_trade_not_papered_over(self):
        self._after("usa")

    @rule("CA-ACB-RENAME")
    def test_tt_split_row_books_it(self):
        body = (HEAD + TRADES_H
                + _trade('QZOA', '2025-01-10, 10:00:00', 100, 10, -1000)
                + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                         code='C')
                + FII_H + _ONE_ID)
        td, root, r = _run({
            "inputs/margin/ib.csv": body,
            "inputs/margin/x.tt":
                "SPLIT 2025-05-01 09:30:00 QZOA.US QZNB.US 1\n"}, "canada")
        with td:
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertEqual([e[3] for e in _events(root)], ["tt"])


def _m1(split_statements):
    """Another company traded QZNB (its own contract id) in 2024; ours
    was QZOA, bought 2024-03-10, and is QZNB when sold on 2025-05-12."""
    a = (HEAD + TRADES_H
         + _trade('QZNB', '2024-01-10, 10:00:00', 10, 5, -50)
         + _trade('QZNB', '2024-02-10, 10:00:00', -10, 6, 60, code='C')
         + _trade('QZOA', '2024-03-10, 10:00:00', 100, 10, -1000))
    b = (HEAD + TRADES_H
         + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200, code='C'))
    if not split_statements:
        return {"inputs/margin/ib.csv": a + b[len(HEAD + TRADES_H):]
                + FII_H + _ONE_ID + _OTHER_CO}
    return {"inputs/margin/ib_2024.csv": a + FII_H + _OTHER_CO + (
                'Financial Instrument Information,Data,Stocks,"QZOA",'
                'QZNB CORP,990000779,US9990007791,,NYSE,1,,,COMMON,,\n'),
            "inputs/margin/ib_2025.csv": b + FII_H + _ONE_ID}


class TestDatesPerContractId(unittest.TestCase):
    """M1: symbol-keyed dates booked the change on the other company's
    first QZNB row (2024-01-10)."""

    def _apart(self, country):
        td, root, r = _run(_m1(True), country)
        with td:
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertEqual(_events(root), [("QZOA.US", "QZNB.US",
                                              "2025-05-12", "ib-conid")])
            return _gain(root)

    @rule("CA-ACB-RENAME")
    def test_canada_other_company_never_dates_the_change(self):
        self.assertGreater(self._apart("canada"), 0)

    @rule("US-BASIS-RENAME")
    def test_usa_other_company_never_dates_the_change(self):
        self.assertAlmostEqual(self._apart("usa"), 200.0, places=2)

    def _together(self, country):
        td, root, r = _run(_m1(False), country)
        with td:
            out = _out(r)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("under several contract ids", out)
            self.assertIn("`RENAME YYYY-MM-DD QZOA.US QZNB.US`", out)
            self.assertFalse(_booked(out), out[-2000:])
            self.assertEqual(_events(root), [])

    @rule("CA-ACB-RENAME")
    def test_canada_one_statement_two_ids_not_booked(self):
        self._together("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_one_statement_two_ids_not_booked(self):
        self._together("usa")

    @rule("CA-ACB-RENAME")
    def test_same_day_rows_not_booked(self):
        body = (HEAD + TRADES_H
                + _trade('QZOA', '2025-05-12, 09:40:00', 100, 10, -1000)
                + _trade('QZNB', '2025-05-12, 15:00:00', -100, 12, 1200,
                         code='C')
                + FII_H + _ONE_ID)
        td, root, r = _run({"inputs/margin/ib.csv": body}, "canada")
        with td:
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("on or before QZOA's last row", _out(r))
            self.assertEqual(_events(root), [])


class TestOldRowAfterNewFirstRow(unittest.TestCase):

    @rule("CA-ACB-RENAME")
    def test_old_dividend_after_new_first_row_not_booked(self):
        # OLD's dividend after NEW's first trade: the rows overlap, the
        # date is the user's to give.
        body = (HEAD + TRADES_H
                + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
                + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                         code='C')
                + DIV_H + ('Dividends,Data,USD,2025-05-20,QZOA(US9990007791) '
                           'Cash Dividend USD 0.25 per Share (Ordinary '
                           'Dividend),25\n')
                + FII_H + _ONE_ID)
        td, root, r = _run({"inputs/margin/ib.csv": body}, "canada")
        with td:
            out = _out(r)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("on or before QZOA's last row (2025-05-20, a "
                          "Dividends row)", out)
            self.assertIn("`RENAME 2025-05-12 QZOA.US QZNB.US`", out)
            self.assertFalse(_booked(out))
            self.assertEqual(_events(root), [])


class TestDeclarationsAndPlaceholders(unittest.TestCase):

    @rule("CA-ACB-RENAME")
    def test_tt_rename_of_old_elsewhere_decides(self):
        body = (HEAD + TRADES_H
                + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
                + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                         code='C')
                + FII_H + _ONE_ID)
        td, root, r = _run({
            "inputs/margin/ib.csv": body,
            "inputs/margin/renames.tt":
                "RENAME 2025-04-01 QZOA.US QZC.US\n"}, "canada")
        with td:
            out = _out(r)
            self.assertIn("renames QZOA.US to QZC.US", out)
            self.assertFalse(_booked(out), out[-2000:])
            self.assertEqual([(e[1], e[3]) for e in _events(root)],
                             [("QZC.US", "tt")])

    @rule("CA-ACB-RENAME")
    def test_old_placeholder_is_no_rename_target(self):
        body = (HEAD + TRADES_H
                + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
                + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                         code='C')
                + FII_H + (
                    'Financial Instrument Information,Data,Stocks,'
                    '"QZNB, QZOA, QZOA.OLD",QZNB CORP,990000779,'
                    'US9990007791,,NYSE,1,,,COMMON,,\n'))
        td, root, r = _run({"inputs/margin/ib.csv": body}, "canada")
        with td:
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertEqual(_events(root), [("QZOA.US", "QZNB.US",
                                              "2025-05-12", "ib-conid")])
            self.assertNotIn("QZOA.OLD.US", _out(r))


if __name__ == "__main__":
    unittest.main()
