"""IB one-contract-id ticker changes (second pre-release review):

- #3: a corporate action naming both symbols that the parse leaves
  UNBOOKED (a CUSIP/ISIN change row) made the contract-id path stand
  down ("booked from that row") while nothing booked the change. Only a
  row the parse books as a symbol change (a renaming split's SPLIT row,
  a merger the corporate-action stage owns) stands it down now; else the
  contract id's own verdict applies (here: ATTENTION with the .tt line).
- #4: the verdict and the hint date were per IB account: one account
  booked the change, another refused it, and following the refusing
  account's hint moved the first account's change past its sale. The
  change is decided from every IB account of the project now (the hint
  is NEW's earliest row anywhere), and a declared date after an
  account's first NEW trade is refused naming that account.
- #10: only rows that move a position or its cost (trades, transfers,
  corporate actions, returns of capital) date OLD's last row; OLD's
  dividend or withholding after the change no longer blocks it.
- #11: a declared RENAME the other way round (NEW -> OLD) no longer
  silences the contract id's evidence: ATTENTION naming its direction.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: U5550001, U5550002).
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
_WHT_H = 'Withholding Tax,Header,Currency,Date,Description,Amount\n'
_TWO = ('[accounts.ma]\ntype = "taxable"\n'
        '[accounts.mb]\ntype = "taxable"\n')
_U2 = 'U5550002'    # pii-ok


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _booked(out):
    return ("Booked as a ticker change" in out
            or "— booked as a ticker change" in out)


def _run(files, country, *extra, accounts=None, strict=True):
    td = tempfile.TemporaryDirectory()
    kw = {"accounts": accounts} if accounts else {}
    root = projects_both(td.name, files=files, **kw)[country]
    r = cli(root, "run", "--no-input", *(["--strict"] if strict else []),
            *extra)
    return td, root, r


def _gains(root):
    r = cli(root, "sum", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads(r.stdout)
    return {a["account"]: a["realized"] for a in doc["accounts"]}, doc


def _events(root):
    return [(e["old"], e["new"], e["date"], e["source"]) for e in
            json.loads(cli(root, "renames", "--json").stdout)["renames"]]


def _cusip_change():
    """QZOA bought 100 @10; IB's CUSIP/ISIN change rows (+100 QZNB /
    -100 QZOA, unhandled by the parser) on 2025-04-01; QZNB 100 sold @12
    on 2025-05-12; one contract id listed as "QZNB, QZOA"."""
    return (HEAD + TRADES_H
            + _trade('QZOA', '2025-01-10, 10:00:00', 100, 10, -1000)
            + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                     code='C')
            + CA_H
            + _ca('QZOA(US9990007791) CUSIP/ISIN Change to (US9990007792) '
                  '(QZNB, QZNB CORP, US9990007792)', 100,
                  when='2025-04-01, 20:25:00')
            + _ca('QZOA(US9990007791) CUSIP/ISIN Change to (US9990007792) '
                  '(QZOA, QZNB CORP, US9990007791)', -100,
                  when='2025-04-01, 20:25:00')
            + FII_H + _ONE_ID)


class TestUnbookedCorporateActionNamingBoth(unittest.TestCase):
    """#3: the CA rows are unhandled; the contract id must not stand
    down as if they booked the change."""

    def _check(self, country):
        td, root, r = _run({"inputs/margin/ib.csv": _cusip_change()},
                           country, strict=False)
        with td:
            out = _out(r)
            self.assertNotIn("booked from it", out)
            self.assertIn("names QZNB and QZOA together, but the parse "
                          "books no symbol change from it", out)
            self.assertIn("`RENAME 2025-04-01 QZOA.US QZNB.US`", out)
            self.assertEqual(_events(root), [])
        # Following the hint books the change: the sale is in the sum.
        td, root, r = _run({"inputs/margin/ib.csv": _cusip_change(),
                            "inputs/margin/r.tt":
                                "RENAME 2025-04-01 QZOA.US QZNB.US\n"},
                           country, strict=False)
        with td:
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertEqual([e[3] for e in _events(root)], ["tt"])
            return _gains(root)[0]["margin"]

    @rule("CA-ACB-RENAME")
    def test_canada_cusip_change_row_is_no_booking(self):
        self.assertGreater(self._check("canada"), 0)

    @rule("US-BASIS-RENAME")
    def test_usa_cusip_change_row_is_no_booking(self):
        self.assertAlmostEqual(self._check("usa"), 200.0, places=2)

    @rule("CA-ACB-RENAME")
    def test_strict_run_stops(self):
        td, root, r = _run({"inputs/margin/ib.csv": _cusip_change()},
                           "canada")
        with td:
            self.assertNotEqual(r.returncode, 0)


def _ma():
    return (HEAD + TRADES_H
            + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
            + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                     code='C')
            + FII_H + _ONE_ID)


def _mb(late_old=''):
    """Account mb: QZOA 50 bought; QZNB 10 sold on 2025-05-14; QZOA's
    dividend on 2025-05-20 (after mb's first QZNB row); `late_old`."""
    return (HEAD + TRADES_H
            + _trade('QZOA', '2025-02-05, 10:00:00', 50, 10, -500, acct=_U2)
            + _trade('QZNB', '2025-05-14, 10:00:00', -10, 12, 120,
                     code='C', acct=_U2)
            + late_old
            + DIV_H + ('Dividends,Data,USD,2025-05-20,QZOA(US9990007791) '
                       'Cash Dividend USD 0.25 per Share (Ordinary '
                       'Dividend),10\n')
            + FII_H + _ONE_ID)


class TestOneDecisionAcrossAccounts(unittest.TestCase):
    """#4: one contract id, two IB accounts: one decision, one date."""

    def _clean(self, country):
        td, root, r = _run({"inputs/ma/ib.csv": _ma(),
                            "inputs/mb/ib.csv": _mb()}, country,
                           accounts=_TWO)
        with td:
            out = _out(r)
            self.assertEqual(r.returncode, 0, out[-3000:])
            self.assertNotIn("does not book from the contract id", out)
            self.assertEqual(_events(root), [("QZOA.US", "QZNB.US",
                                              "2025-05-12", "ib-conid")])
            for acct in ("ma", "mb"):
                rows = json.loads((root / "work" / f"{acct}_ib.json")
                                  .read_text())
                rows = rows.get("transactions", rows) \
                    if isinstance(rows, dict) else rows
                self.assertEqual(
                    [(t["date"], t.get("event_source")) for t in rows
                     if t.get("action") == "SPLIT"],
                    [("2025-05-12", "ib-conid")], acct)
            return _gains(root)[0]

    @rule("CA-ACB-RENAME")
    def test_canada_both_accounts_book_one_date(self):
        g = self._clean("canada")
        self.assertGreater(g["ma"], 0)
        self.assertGreater(g["mb"], 0)

    @rule("US-BASIS-RENAME")
    def test_usa_both_accounts_book_one_date(self):
        g = self._clean("usa")
        self.assertAlmostEqual(g["ma"], 200.0, places=2)
        self.assertAlmostEqual(g["mb"], 20.0, places=2)

    def _late_declared(self, country, strict):
        td, root, r = _run({"inputs/ma/ib.csv": _ma(),
                            "inputs/mb/ib.csv": _mb(),
                            "inputs/mb/r.tt":
                                "RENAME 2025-05-14 QZOA.US QZNB.US\n"},
                           country, accounts=_TWO, strict=strict)
        with td:
            out = _out(r)
            self.assertNotEqual(r.returncode, 0, out[-3000:])
            self.assertIn("is declared on 2025-05-14, but the IB "
                          "statements of account 'ma' already trade or "
                          "transfer QZNB on 2025-05-12", out)
            self.assertIn("`RENAME 2025-05-12 QZOA.US QZNB.US`", out)

    @rule("CA-ACB-RENAME")
    def test_canada_declared_date_after_an_accounts_trade_refused(self):
        self._late_declared("canada", True)

    @rule("US-BASIS-RENAME")
    def test_usa_declared_date_after_an_accounts_trade_refused(self):
        # Non-strict too: the US books sold nothing in ma, at exit 0.
        self._late_declared("usa", False)

    @rule("CA-ACB-RENAME")
    def test_declared_earliest_date_books(self):
        td, root, r = _run({"inputs/ma/ib.csv": _ma(),
                            "inputs/mb/ib.csv": _mb(),
                            "inputs/mb/r.tt":
                                "RENAME 2025-05-12 QZOA.US QZNB.US\n"},
                           "canada", accounts=_TWO)
        with td:
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertEqual([e[3] for e in _events(root)], ["tt"])

    def _refused(self, country):
        # mb's QZOA return of capital (it moves the cost) after the first
        # QZNB row (ma's, 2025-05-12): refused in BOTH accounts, the hint
        # the earliest NEW row in any account.
        late = (DIV_H + 'Dividends,Data,USD,2025-05-20,QZOA(US9990007791) '
                'Cash Dividend USD 0.10 per Share (Return of Capital),4\n')
        td, root, r = _run({"inputs/ma/ib.csv": _ma(),
                            "inputs/mb/ib.csv": _mb(late)}, country,
                           accounts=_TWO, strict=False)
        with td:
            out = _out(r)
            self.assertFalse(_booked(out), out[-3000:])
            self.assertEqual(out.count("`RENAME 2025-05-12 QZOA.US "
                                       "QZNB.US`"), 2)
            self.assertIn("(a trade or transfer in account 'ma')", out)
            self.assertIn("(2025-05-20, a return of capital row in "
                          "account 'mb')", out)
            self.assertEqual(_events(root), [])

    @rule("CA-ACB-RENAME")
    def test_canada_refused_in_every_account(self):
        self._refused("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_refused_in_every_account(self):
        self._refused("usa")


def _late_income(rows):
    """QZOA 100 bought; QZNB 100 sold on 2025-05-12; `rows` of QZOA
    dated 2025-05-20."""
    return (HEAD + TRADES_H
            + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
            + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                     code='C')
            + rows + FII_H + _ONE_ID)


_LATE_DIV = (DIV_H + 'Dividends,Data,USD,2025-05-20,QZOA(US9990007791) '
             'Cash Dividend USD 0.25 per Share (Ordinary Dividend),25\n'
             + _WHT_H + 'Withholding Tax,Data,USD,2025-05-20,'
             'QZOA(US9990007791) Cash Dividend USD 0.25 per Share - US '
             'Tax,-3.75\n')
_LATE_WHT = (_WHT_H + 'Withholding Tax,Data,USD,2025-05-20,'
             'QZOA(US9990007791) Cash Dividend USD 0.25 per Share - US '
             'Tax,1.50\n')
_LATE_ROC = (DIV_H + 'Dividends,Data,USD,2025-05-20,QZOA(US9990007791) '
             'Cash Dividend USD 1.00 per Share (Return of Capital),100\n')


class TestIncomeRowsNeverBlock(unittest.TestCase):
    """#10: OLD's dividend / withholding after NEW's first trade moves no
    position: the change is booked, the income still lands."""

    def _check(self, country, rows):
        td, root, r = _run({"inputs/margin/ib.csv": _late_income(rows)},
                           country)
        with td:
            out = _out(r)
            self.assertEqual(r.returncode, 0, out[-3000:])
            self.assertTrue(_booked(out), out[-2000:])
            self.assertEqual(_events(root), [("QZOA.US", "QZNB.US",
                                              "2025-05-12", "ib-conid")])
            g, doc = _gains(root)
            rows = json.loads((root / "work" / "margin_ib.json")
                              .read_text())
            rows = rows.get("transactions", rows) \
                if isinstance(rows, dict) else rows
            return g["margin"], doc["totals"]["dividend"], rows

    @rule("CA-ACB-RENAME")
    def test_canada_late_dividend_and_withholding(self):
        gain, div, _root = self._check("canada", _LATE_DIV)
        self.assertGreater(gain, 0)
        self.assertGreater(div, 0)

    @rule("US-BASIS-RENAME")
    def test_usa_late_dividend_and_withholding(self):
        gain, div, _root = self._check("usa", _LATE_DIV)
        self.assertAlmostEqual(gain, 200.0, places=2)
        self.assertAlmostEqual(div, 25.0, places=2)

    @rule("US-BASIS-RENAME")
    def test_usa_late_withholding_adjustment(self):
        gain, _div, rows = self._check("usa", _LATE_WHT)
        self.assertAlmostEqual(gain, 200.0, places=2)
        # The withholding adjustment is still booked (under QZOA).
        self.assertTrue(any(abs(float(t.get("net_amount") or 0)) == 1.5
                            for t in rows), rows)

    @rule("CA-ACB-RENAME")
    def test_canada_late_withholding_adjustment(self):
        self.assertGreater(self._check("canada", _LATE_WHT)[0], 0)

    @rule("CA-ACB-RENAME")
    def test_late_return_of_capital_still_blocks(self):
        # A return of capital moves the cost: OLD's ROC after NEW's
        # first trade leaves the date to the user.
        td, root, r = _run({"inputs/margin/ib.csv":
                            _late_income(_LATE_ROC)}, "canada",
                           strict=False)
        with td:
            out = _out(r)
            self.assertFalse(_booked(out), out[-2000:])
            self.assertIn("(2025-05-20, a return of capital row)", out)
            self.assertEqual(_events(root), [])


def _plain():
    return (HEAD + TRADES_H
            + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
            + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                     code='C')
            + FII_H + _ONE_ID)


class TestBackwardsDeclaration(unittest.TestCase):
    """#11: a .tt RENAME NEW -> OLD no longer silences the evidence."""

    def _check(self, country):
        td, root, r = _run({"inputs/margin/ib.csv": _plain(),
                            "inputs/margin/r.tt":
                                "RENAME 2025-05-12 QZNB.US QZOA.US\n"},
                           country)
        with td:
            out = _out(r)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("declares the ticker change the other way round "
                          "(QZNB.US -> QZOA.US), but the contract id's "
                          "rows run QZOA.US -> QZNB.US", out)
            self.assertIn("`RENAME 2025-05-12 QZOA.US QZNB.US`", out)
            self.assertNotIn("ib-conid", [e[3] for e in _events(root)])

    @rule("CA-ACB-RENAME")
    def test_canada_backwards_line_is_attention(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_backwards_line_is_attention(self):
        self._check("usa")

    @rule("CA-ACB-RENAME")
    def test_forward_line_stays_quiet(self):
        td, root, r = _run({"inputs/margin/ib.csv": _plain(),
                            "inputs/margin/r.tt":
                                "RENAME 2025-05-12 QZOA.US QZNB.US\n"},
                           "canada")
        with td:
            out = _out(r)
            self.assertEqual(r.returncode, 0, out[-3000:])
            self.assertNotIn("contract id 990000779", out)
            self.assertEqual([e[3] for e in _events(root)], ["tt"])


if __name__ == "__main__":
    unittest.main()
