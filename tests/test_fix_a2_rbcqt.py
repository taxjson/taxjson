"""Re-audit 2 fixes for the RBC Direct and Questrade parsers (fix lists
parsers-rbc and parsers-questrade). Every fixture is synthetic (fake
account ids 55500001 / 55500002, made-up option codes)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_close_check import (unbacked_option_closes,
                                            unbacked_option_close_messages)

from test_fix_rbc import (ABC_REC, ABC_REM, ABC_SELL, HDR, ORCX_ROWS, OWL,
                          parse_files, parse_one, row)
from test_fix_rbcqt import q, qdiv, qt_parse

REPO = Path(__file__).resolve().parent.parent
ACCT = "55500001"  # pii-ok (synthetic)


def _cli_run(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600, env=env)


def _project(root, year, files, extra_settings="", ticker_map=""):
    root = Path(root)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n'
        f'option_grant_timing_since = 2024\n{extra_settings}'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text(ticker_map)
    for name, text in files.items():
        (root / "inputs" / "margin" / name).write_text(text)
    return root


# RBC re-describes one contract between yearly exports: ".RCI" with the
# 2024 OPEN CONTRACT buy, ".RCI.B" with the 2025 CLOSE CONTRACT sale.
RCI_BUY = row("December 23, 2024", "Buy", "8ZZZZZ1", "", "3", "3.55",
              "-1075.70", "CAD",
              "CALL .RCI   01/15/27    46 ROGERS COMMUNICATIONS INC DA "
              "OPEN CONTRACT", settle="December 24, 2024")
RCI_SELL = row("December 29, 2025", "Sell", "8ZZZZZ1", "", "-3", "5.00",
               "1489.30", "CAD",
               "CALL .RCI.B   01/15/27    46 ROGERS COMMUNICATIONS INC CA "
               "CLOSE CONTRACT", settle="December 30, 2025")
TT_RCI = "BUYSELL 2024-12-24 09:30:00 RCI270115C00046000.TO 3 CAD 3.55 1075.70 10.70\n"
TT_RCIB = TT_RCI.replace("RCI270115", "RCI.B270115")


def _tx(**kw):
    base = dict(action="BUYSELL", time="09:30:00", currency="CAD",
                price=1.0, account="margin")
    base.update(kw)
    base.setdefault("date_settle", base["date"])
    return TaxTransaction(**base)


class TestRbcOpenCloseMarker(unittest.TestCase):
    """A2-0006 / A2-0267 / A2-0266 / A2-0095: RBC's OPEN/CLOSE CONTRACT
    marker is carried as the neutral open_close code, and a closing row
    the books cannot back is named on the console with the ticker.map
    line when the contract is held under another root."""

    def test_marker_is_carried_on_option_rows_only(self):
        stock = row("March 3, 2025", "Buy", "XYZ", "XYZ CORP", "10", "5.00",
                    "-59.95", "CAD", "XYZ CORP OPEN CONTRACT")
        txs, _err, _ = parse_files({"a.csv": RCI_BUY, "b.csv": RCI_SELL
                                    + stock})
        oc = {(t["symbol"], t["quantity"]): t.get("open_close") for t in txs}
        self.assertEqual(oc[("RCI270115C00046000.TO", 3.0)], "O")
        self.assertEqual(oc[("RCI270115C00046000.TO", -3.0)], "C")
        self.assertIsNone(oc[("XYZ.TO", 10.0)])

    def test_expiry_row_is_closing(self):
        # A2-0266: RBC's expiry of a long (Reorganization, signed -3).
        exp = row("January 19, 2026", "Reorganization", "8ZZZZZ2", "", "-3",
                  "", "0", "CAD",
                  "EXP - CALL .RCX.B 01/16/26 46 ROGERS COMMUNICATIONS INC "
                  "OPTION EXPIRATION - EXPIRED")
        txs, _err, _ = parse_one(exp)
        self.assertEqual([t.get("open_close") for t in txs], ["C"])

    def test_close_sale_under_other_root_names_the_held_contract(self):
        books = [
            _tx(date="2024-12-24", symbol="RCI270115C00046000.TO",
                quantity=3, net_amount=1075.70),
            _tx(date="2025-12-29", symbol="RCI.B270115C00046000.TO",
                quantity=-3, net_amount=1489.30, open_close="C"),
        ]
        msgs = unbacked_option_close_messages(books)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith("warning: ATTENTION: "))
        self.assertIn("GLOBAL RCI270115C00046000.TO RCI.B270115C00046000.TO",
                      msgs[0])

    def test_buy_to_close_opening_a_long_is_named(self):
        # A2-0267: the written call is in the .tt as RCX; RBC's buy-back
        # says CLOSE CONTRACT under RCX.B.
        books = [
            _tx(date="2024-12-20", symbol="RCX270115C00046000.TO",
                quantity=-3, net_amount=1054.70),
            _tx(date="2025-06-02", symbol="RCX.B270115C00046000.TO",
                quantity=3, net_amount=310.70, open_close="C"),
        ]
        f = unbacked_option_closes(books)
        self.assertEqual([(x["side"], x["partners"]) for x in f],
                         [("purchase", [("RCX270115C00046000.TO", -3.0)])])
        self.assertIn("GLOBAL RCX270115C00046000.TO RCX.B270115C00046000.TO",
                      unbacked_option_close_messages(books)[0])

    def test_adjusted_root_digit_partner(self):
        # A2-0095: the .tt holds TRX, the XCH-renamed close says TRX1.
        books = [
            _tx(date="2024-05-01", symbol="TRX260116C00055000.TO",
                quantity=5, net_amount=1038.20),
            _tx(date="2025-03-03", symbol="TRX1260116C00055000.TO",
                quantity=-5, net_amount=8986.80, open_close="C"),
        ]
        self.assertIn("GLOBAL TRX260116C00055000.TO TRX1260116C00055000.TO",
                      unbacked_option_close_messages(books)[0])

    def test_backed_close_and_opening_write_are_silent(self):
        books = [
            _tx(date="2024-12-24", symbol="RCI.B270115C00046000.TO",
                quantity=3, net_amount=1075.70),
            _tx(date="2025-12-29", symbol="RCI.B270115C00046000.TO",
                quantity=-3, net_amount=1489.30, open_close="C"),
            _tx(date="2025-12-29", symbol="ZZZ270115C00010000.TO",
                quantity=-1, net_amount=100.0, open_close="O"),
        ]
        self.assertEqual(unbacked_option_close_messages(books), [])

    def test_unrelated_root_is_not_a_partner(self):
        books = [
            _tx(date="2024-12-24", symbol="ABC270115C00046000.TO",
                quantity=3, net_amount=1075.70),
            _tx(date="2025-12-29", symbol="XYZ270115C00046000.TO",
                quantity=-3, net_amount=1489.30, open_close="C"),
        ]
        (m,) = unbacked_option_close_messages(books)
        self.assertNotIn("GLOBAL ABC", m)
        self.assertIn("find-missing-history", m)


class TestRbcCloseContractEndToEnd(unittest.TestCase):
    """A2-0006 end to end: the run console names the CLOSE CONTRACT row,
    and `taxjson handoff` accepts the re-described root and refuses the
    closed year's root."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.p24 = _project(cls.base / "p2024", 2024,
                           {"rbc_2024.csv": HDR + RCI_BUY})
        r = _cli_run(cls.p24, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        r = _cli_run(cls.p24, "close-year", "--force")
        assert r.returncode == 0, r.stdout + r.stderr
        cls.rec = cls.p24 / "filed" / "2024.json"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _p25(self, name, tt):
        p = _project(self.base / name, 2025,
                     {"rbc_2025.csv": HDR + RCI_SELL, "margin_start.tt": tt},
                     f'prior_year_record = "{self.rec}"\n')
        r = _cli_run(p, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return p, r.stdout + r.stderr

    def test_wrong_root_in_tt_is_loud_and_fails_handoff(self):
        p, out = self._p25("wrong", TT_RCI)
        self.assertIn("ATTENTION: RCI.B270115C00046000.TO", out)
        self.assertIn("GLOBAL RCI270115C00046000.TO RCI.B270115C00046000.TO",
                      out)
        h = _cli_run(p, "handoff")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        self.assertIn("marked CLOSING", h.stdout)

    def test_this_years_root_in_tt_passes_handoff(self):
        p, out = self._p25("right", TT_RCIB)
        self.assertNotIn("ATTENTION: RCI", out)
        h = _cli_run(p, "handoff")
        self.assertEqual(h.returncode, 0, h.stdout + h.stderr)
        self.assertIn("re-described", h.stdout)

    def test_expiry_under_other_root_points_at_the_map_line(self):
        # A2-0266: the EXP row of RCX.B for a long the .tt holds as RCX:
        # the run named a missing expiry row (wrong) for both legs.
        exp = row("January 19, 2026", "Reorganization", "8ZZZZZ2", "", "-3",
                  "", "0", "CAD",
                  "EXP - CALL .RCX.B   01/16/26    46 ROGERX COMMUNICATIONS "
                  "INC OPTION EXPIRATION - EXPIRED")
        tt = ("BUYSELL 2025-06-23 10:00:00 RCX260116C00046000.TO 3 CAD 3.55 "
              "1075.70 10.70\n")
        p = _project(self.base / "exp", 2026,
                     {"rbc.csv": HDR + exp, "start.tt": tt})
        r = _cli_run(p, "run", "--no-input")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("GLOBAL RCX260116C00046000.TO RCX.B260116C00046000.TO",
                      out)
        self.assertNotIn("missing its expiry", out)
        self.assertNotIn("add the missing purchase", out)


ATT = "warning: ATTENTION:"


def _attention(err):
    return [ln for ln in err.splitlines() if ln.startswith(ATT)]


class TestMoneyWarningsReachTheConsole(unittest.TestCase):
    """A2-0005, A2-0007, A2-0027, A2-0096, A2-0612, A2-0613, A2-0276,
    A2-0279, A2-0282, A2-0283: parser warnings about money the books
    get wrong (a guessed listing or code, income left out, shares with
    no cost) carry the ATTENTION prefix `taxjson run` prints on the
    console; plain warnings reached only the .sum."""

    def test_rbc_temporary_code_assumption(self):
        _txs, err, _ = parse_files({"rbc_2026.csv": ABC_SELL + ABC_REC
                                    + ABC_REM})
        self.assertTrue(any("A012345" in ln for ln in _attention(err)), err)

    def test_rbc_ticker_change_puts_the_map_line_first(self):
        _txs, err, _ = parse_one("".join(ORCX_ROWS))
        (ln,) = [x for x in _attention(err) if "ORCX" in x]
        self.assertIn("GLOBAL ORCX.US OBDX.US", ln)

    def test_rbc_ticker_change_with_a_buy_first(self):
        # A2-0270: the new symbol opens with a small buy, then sells more.
        body = (row("August 23, 2023", "Sell", "OBDX", OWL, "-1578", "15",
                    "23650.05", "USD", "BLUE OWLX UNSOLICITED CA")
                + row("August 22, 2023", "Buy", "OBDX", OWL, "10", "15",
                      "-150.05", "USD", "BLUE OWLX UNSOLICITED DA")
                + "".join(ORCX_ROWS[1:]))
        _txs, err, _ = parse_one(body)
        self.assertTrue(any("GLOBAL ORCX.US OBDX.US" in ln
                            for ln in _attention(err)), err)

    def test_rbc_notional_distribution(self):
        nd = row("December 31, 2025", "Distribution", "VDX", "VANGUARD X",
                 "", "", "0", "CAD", "VANGUARD X 2025 NOTIONAL DISTRIBUTION "
                 "ADJUSTMENT TO BOOK COST $2000.00")
        _txs, err, _ = parse_one(nd)
        self.assertTrue(any("notional distribution" in ln
                            for ln in _attention(err)), err)

    def test_qt_internal_code_roc(self):
        _txs, err, _ = qt_parse(
            qdiv("A020626", "OTHERCO INC RETURN OF CAPITAL ON 100 SHS REC "
                 "01/15/26 PAY 02/01/26", "700.00", cur="CAD"))
        self.assertTrue(any("A020626" in ln for ln in _attention(err)), err)

    def test_qt_net_of_tax_dividend_and_no_book_value(self):
        div = qdiv("AAPL", "APPLE INC CASH DIV ON 100 SHS NON-RES TAX "
                   "WITHHELD", "21.25")
        tfi = q(action="TF6", sym="QZT.TO", desc="QZT CORP TRANSFER IN",
                qty="100", price="0", gross="0", comm="0", net="0",
                cur="CAD", act="Transfers")
        _txs, err, _ = qt_parse(div + tfi, taxable=True)
        att = _attention(err)
        self.assertTrue(any("NON-RES TAX WITHHELD" in ln for ln in att), err)
        self.assertTrue(any("TRANSFER BOOK VALUE" in ln for ln in att), err)

    def test_qt_ticker_change_buy_first_and_console(self):
        # A2-0099 / A2-0279.
        d = "QQ HOLDINGS CORP WE ACTED AS AGENT"
        body = (q(sym="QQOL", desc=d, qty="500", price="20", gross="-10000",
                  comm="0", net="-10000")
                + q(td="2025-05-01", sym="QQNW", desc=d, qty="100",
                    price="25", gross="-2500", comm="0", net="-2500")
                + q(td="2025-06-01", action="Sell", sym="QQNW", desc=d,
                    qty="-600", price="30", gross="18000", comm="0",
                    net="18000"))
        _txs, err, _ = qt_parse(body)
        self.assertTrue(any("GLOBAL QQOL.US QQNW.US" in ln
                            for ln in _attention(err)), err)

    def test_run_echoes_the_continuation_line(self):
        # A2-0613: an indented continuation under an echoed line.
        from taxjson.bin.taxjson_run import echo_parse_stats
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x.json"
            Path(d, "x.json.diag").write_text(
                "warning: ATTENTION: a.csv: head\n    GLOBAL A.US B.US\n"
                "  a.csv: 3 tax objects\nwarning: plain\n    hidden\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                echo_parse_stats(out)
        got = buf.getvalue()
        self.assertIn("GLOBAL A.US B.US", got)
        self.assertIn("\n  a.csv: 3 tax objects", got)
        self.assertNotIn("hidden", got)

    def test_canada_stock_dividend_is_on_the_console(self):
        # A2-0265: the Canadian $0-cost stock dividend (CA-STKDIV-01).
        buy = row("March 3, 2025", "Buy", "TDQ", "TDQ SPLIT CORP", "100",
                  "10", "-1000", "CAD", "TDQ SPLIT CORP UNSOLICITED")
        sd = row("June 2, 2025", "Reorganization", "TDQ", "TDQ SPLIT CORP",
                 "5", "", "0", "CAD", "DIS - TDQ SPLIT CORP STK DIV ON 100 "
                 "SHS")
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", 2025, {"rbc.csv": HDR + sd + buy})
            r = _cli_run(p, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ATTENTION: TDQ.TO: stock dividend of 5", r.stdout)


class TestOneExportManyAccounts(unittest.TestCase):
    """A2-0025: one Questrade / RBC export holding rows of two broker
    accounts, one a TFSA, was booked to one taxable account silently."""

    def _qt(self, acct, atype, td, action, qty, price):
        g = qty * price
        return q(td=td, action=action, sym="QZEQ.TO",
                 desc="QZEQ ETF WE ACTED AS AGENT", qty=str(qty),
                 price=str(price), gross=f"{-g:.2f}", comm="0",
                 net=f"{-g:.2f}", cur="CAD").replace(
                     f",{ACCT},Trades,Individual margin",
                     f",{acct},Trades,{atype}")

    def test_registered_rows_in_a_taxable_account_are_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        body = (self._qt(ACCT, "Individual margin", "2025-01-15", "Buy",
                         10, 30)
                + self._qt("55500002", "Individual TFSA",  # pii-ok
                           "2025-02-18", "Buy", 100, 30))
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(body, taxable=True)
        self.assertIn("registered plan", str(cm.exception))
        self.assertNotIn("55500002", str(cm.exception))   # masked

    def test_two_taxable_accounts_are_said_out_loud(self):
        body = (self._qt(ACCT, "Individual margin", "2025-01-15", "Buy",
                         10, 30)
                + self._qt("55500002", "Joint margin",  # pii-ok
                           "2025-02-18", "Buy", 100, 30))
        txs, err, _ = qt_parse(body, taxable=True)
        self.assertEqual(len(txs), 2)
        self.assertTrue(any("2 Questrade accounts" in ln
                            for ln in _attention(err)), err)

    def test_rbc_file_with_two_accounts(self):
        a = row("March 3, 2025", "Buy", "XYZ", "XYZ CORP", "10", "5.00",
                "-59.95", "CAD", "XYZ CORP UNSOLICITED")
        b = row("March 4, 2025", "Buy", "XYZ", "XYZ CORP", "10", "5.00",
                "-59.95", "CAD", "XYZ CORP UNSOLICITED",
                acct="55500002")  # pii-ok
        _txs, err, _ = parse_one(a + b)
        self.assertTrue(any("2 RBC accounts" in ln for ln in _attention(err)),
                        err)
        # One account per file, several files: the normal layout.
        _txs, err, _ = parse_files({"a.csv": a, "b.csv": b})
        self.assertFalse(any("RBC accounts" in ln for ln in _attention(err)))


if __name__ == "__main__":
    unittest.main()
