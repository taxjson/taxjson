"""Re-audit 2 fixes for the RBC Direct and Questrade parsers (fix lists
parsers-rbc and parsers-questrade). Every fixture is synthetic (fake
account ids 55500001 / 55500002, made-up option codes)."""  # pii-ok
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

from test_fix_rbc import (ABC_REC, ABC_REM, ABC_SELL, HDR, parse_files,
                          parse_one, row)
from test_fix_rbcqt import q, qdiv, qt_parse
from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both

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


# RBC re-describes one contract between yearly exports: ".QRL" with the
# 2024 OPEN CONTRACT buy, ".QRL.B" with the 2025 CLOSE CONTRACT sale.
OPT_BUY = row("December 9, 2024", "Buy", "8ZZZZZ1", "", "4", "2.80",
              "-1130.70", "CAD",
              "CALL .QRL   06/18/27    38 QRL TELECOM INC DA "
              "OPEN CONTRACT", settle="December 10, 2024")
OPT_SELL = row("December 8, 2025", "Sell", "8ZZZZZ1", "", "-4", "5.10",
               "2029.30", "CAD",
               "CALL .QRL.B   06/18/27    38 QRL TELECOM INC CA "
               "CLOSE CONTRACT", settle="December 9, 2025")
TT_OPT = "BUYSELL 2024-12-10 09:30:00 QRL270618C00038000.TO 4 CAD 2.80 1130.70 10.70\n"
TT_OPT_B = TT_OPT.replace("QRL270618", "QRL.B270618")


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
        txs, _err, _ = parse_files({"a.csv": OPT_BUY, "b.csv": OPT_SELL
                                    + stock})
        oc = {(t["symbol"], t["quantity"]): t.get("open_close") for t in txs}
        self.assertEqual(oc[("QRL270618C00038000.TO", 4.0)], "O")
        self.assertEqual(oc[("QRL270618C00038000.TO", -4.0)], "C")
        self.assertIsNone(oc[("XYZ.TO", 10.0)])

    def test_expiry_row_is_closing(self):
        # A2-0266: RBC's expiry of a long (Reorganization, signed -3).
        exp = row("June 22, 2026", "Reorganization", "8ZZZZZ2", "", "-4",
                  "", "0", "CAD",
                  "EXP - CALL .QRM.B 06/19/26 38 QRM TELECOM INC "
                  "OPTION EXPIRATION - EXPIRED")
        txs, _err, _ = parse_one(exp)
        self.assertEqual([t.get("open_close") for t in txs], ["C"])

    def test_close_sale_under_other_root_names_the_held_contract(self):
        books = [
            _tx(date="2024-12-10", symbol="QRL270618C00038000.TO",
                quantity=4, net_amount=1130.70),
            _tx(date="2025-12-08", symbol="QRL.B270618C00038000.TO",
                quantity=-4, net_amount=2029.30, open_close="C"),
        ]
        msgs = unbacked_option_close_messages(books)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith("warning: ATTENTION: "))
        self.assertIn("GLOBAL QRL270618C00038000.TO QRL.B270618C00038000.TO",
                      msgs[0])

    def test_buy_to_close_opening_a_long_is_named(self):
        # A2-0267: the written call is in the .tt as QRM; RBC's buy-back
        # says CLOSE CONTRACT under QRM.B.
        books = [
            _tx(date="2024-12-13", symbol="QRM270618C00038000.TO",
                quantity=-4, net_amount=1109.30),
            _tx(date="2025-06-09", symbol="QRM.B270618C00038000.TO",
                quantity=4, net_amount=410.70, open_close="C"),
        ]
        f = unbacked_option_closes(books)
        self.assertEqual([(x["side"], x["partners"]) for x in f],
                         [("purchase", [("QRM270618C00038000.TO", -4.0)])])
        self.assertIn("GLOBAL QRM270618C00038000.TO QRM.B270618C00038000.TO",
                      unbacked_option_close_messages(books)[0])

    def test_adjusted_root_digit_partner(self):
        # A2-0095: the .tt holds TQZ, the XCH-renamed close says TQZ1.
        books = [
            _tx(date="2024-05-15", symbol="TQZ260918C00062000.TO",
                quantity=7, net_amount=1312.40),
            _tx(date="2025-03-17", symbol="TQZ1260918C00062000.TO",
                quantity=-7, net_amount=6203.10, open_close="C"),
        ]
        self.assertIn("GLOBAL TQZ260918C00062000.TO TQZ1260918C00062000.TO",
                      unbacked_option_close_messages(books)[0])

    def test_backed_close_and_opening_write_are_silent(self):
        books = [
            _tx(date="2024-12-10", symbol="QRL.B270618C00038000.TO",
                quantity=4, net_amount=1130.70),
            _tx(date="2025-12-08", symbol="QRL.B270618C00038000.TO",
                quantity=-4, net_amount=2029.30, open_close="C"),
            _tx(date="2025-12-08", symbol="ZZZ270618C00010000.TO",
                quantity=-1, net_amount=100.0, open_close="O"),
        ]
        self.assertEqual(unbacked_option_close_messages(books), [])

    def test_same_day_write_and_buy_back_in_either_order_is_backed(self):
        # RBC prints no time: a day's write (O) and its buy-back (C) can
        # sort buy first.
        sym = "WIDG240816C00072000.US"
        books = [
            _tx(date="2024-08-02", symbol=sym, quantity=6, net_amount=741.0,
                open_close="C"),
            _tx(date="2024-08-02", symbol=sym, quantity=-6,
                net_amount=1029.0, open_close="O"),
        ]
        self.assertEqual(unbacked_option_closes(books), [])

    def test_unrelated_root_is_not_a_partner(self):
        books = [
            _tx(date="2024-12-10", symbol="ABC270618C00038000.TO",
                quantity=4, net_amount=1130.70),
            _tx(date="2025-12-08", symbol="DEF270618C00038000.TO",
                quantity=-4, net_amount=2029.30, open_close="C"),
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
                           {"rbc_2024.csv": HDR + OPT_BUY})
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
                     {"rbc_2025.csv": HDR + OPT_SELL, "margin_start.tt": tt},
                     f'prior_year_record = "{self.rec}"\n')
        r = _cli_run(p, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return p, r.stdout + r.stderr

    def test_wrong_root_in_tt_is_loud_and_fails_handoff(self):
        p, out = self._p25("wrong", TT_OPT)
        self.assertIn("ATTENTION: QRL.B270618C00038000.TO", out)
        self.assertIn("GLOBAL QRL270618C00038000.TO QRL.B270618C00038000.TO",
                      out)
        h = _cli_run(p, "handoff")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        self.assertIn("marked CLOSING", h.stdout)

    def test_this_years_root_in_tt_passes_handoff(self):
        p, out = self._p25("right", TT_OPT_B)
        self.assertNotIn("ATTENTION: QRL", out)
        h = _cli_run(p, "handoff")
        self.assertEqual(h.returncode, 0, h.stdout + h.stderr)
        self.assertIn("re-described", h.stdout)

    def test_expiry_under_other_root_points_at_the_map_line(self):
        # A2-0266: the EXP row of QRM.B for a long the .tt holds as QRM:
        # the run named a missing expiry row (wrong) for both legs.
        exp = row("June 22, 2026", "Reorganization", "8ZZZZZ2", "", "-4",
                  "", "0", "CAD",
                  "EXP - CALL .QRM.B   06/19/26    38 QRM TELECOM "
                  "INC OPTION EXPIRATION - EXPIRED")
        tt = ("BUYSELL 2025-11-24 10:00:00 QRM260619C00038000.TO 4 CAD 2.80 "
              "1130.70 10.70\n")
        p = _project(self.base / "exp", 2026,
                     {"rbc.csv": HDR + exp, "start.tt": tt})
        r = _cli_run(p, "run", "--no-input")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("GLOBAL QRM260619C00038000.TO QRM.B260619C00038000.TO",
                      out)
        self.assertNotIn("missing its expiry", out)
        self.assertNotIn("add the missing purchase", out)


ATT = "warning: ATTENTION:"

# A ticker change RBC applied without a reorganization row: buys under
# the old symbol, the sale under the new one (synthetic values).
ZEPH = "ZEPHYR LENDING CORPORATION COMMON STOCK"
RENAME_ROWS = [
    row("October 17, 2022", "Sell", "PQRB", ZEPH, "-1200", "21",
        "25199.95", "USD", "ZEPHYR LENDING CORPORATION UNSOLICITED CA"),
    row("July 11, 2022", "Buy", "PQRA", ZEPH, "450", "17", "-7659.95", "USD",
        "ZEPHYR LENDING CORPORATION UNSOLICITED DA"),
    row("May 9, 2022", "Buy", "PQRA", ZEPH, "750", "16", "-12009.95", "USD",
        "ZEPHYR LENDING CORPORATION UNSOLICITED DA"),
]


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
        _txs, err, _ = parse_one("".join(RENAME_ROWS))
        (ln,) = [x for x in _attention(err) if "PQRA" in x]
        self.assertIn("GLOBAL PQRA.US PQRB.US", ln)

    def test_rbc_ticker_change_with_a_buy_first(self):
        # A2-0270: the new symbol opens with a small buy, then sells more.
        body = (row("October 17, 2022", "Sell", "PQRB", ZEPH, "-1210", "21",
                    "25409.95", "USD", "ZEPHYR LENDING UNSOLICITED CA")
                + row("October 14, 2022", "Buy", "PQRB", ZEPH, "10", "21",
                      "-210.05", "USD", "ZEPHYR LENDING UNSOLICITED DA")
                + "".join(RENAME_ROWS[1:]))
        _txs, err, _ = parse_one(body)
        self.assertTrue(any("GLOBAL PQRA.US PQRB.US" in ln
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


def _stk(td, qty, sym="XTD.TO", cur="CAD"):
    return q(td=td, action="DIS", sym=sym,
             desc="XTD SPLIT CORP STK DIV ON 1000 SHS", qty=str(qty),
             price="0", gross="0", comm="0", net="0", cur=cur,
             act="Dividends")


def _rei(td, qty, net, sym="QZF.TO"):
    return q(td=td, action="REI", sym=sym, desc="QZF FUND REINV@C$8.34",
             qty=str(qty), price="0", gross="0", comm="0", net=str(net),
             cur="CAD", act="Dividend reinvestment")


def _qbuy(sym, qty, price, td="2025-01-06"):
    g = qty * price
    return q(td=td, sym=sym, desc=f"{sym} WE ACTED AS AGENT", qty=str(qty),
             price=str(price), gross=f"{-g:.2f}", comm="0", net=f"{-g:.2f}",
             cur="CAD")


def _held(txs):
    pos = {}
    for t in txs:
        if t["action"] in ("BUYSELL", "ASSIGN"):
            pos[t["symbol"]] = round(pos.get(t["symbol"], 0) + t["quantity"], 6)
    return {k: v for k, v in pos.items() if v}


class TestQtReversalsAcrossExports(unittest.TestCase):
    """A2-0026, A2-0280, A2-0281, A2-0616, A2-1055, A2-1060, A2-1063:
    Questrade CIL / REI / stock-dividend reversals pair across all of an
    account's exports, overlap copies included."""

    def test_overlapping_exports_stock_dividend_reversed_and_reposted(self):
        h1 = _qbuy("XTD.TO", 1000, 5) + _stk("2025-03-05", 150)
        fy = (_qbuy("XTD.TO", 1000, 5) + _stk("2025-03-05", 150)
              + _stk("2025-03-20", -150) + _stk("2025-03-20", 100))
        txs, err, _ = qt_parse(h1, fy)
        # Both files' buys come through (taxjson-sort dedups the copy);
        # neither copy of the reversed +150 does.
        self.assertEqual(sorted(t["quantity"] for t in txs
                                if t.get("type") == "stock_dividend"),
                         [100.0], err)

    def test_overlapping_exports_drip_reversed_in_the_newer(self):
        old = _qbuy("QZF.TO", 100, 8) + _rei("2025-06-30", 3, -25.02)
        new = (_qbuy("QZF.TO", 100, 8) + _rei("2025-06-30", 3, -25.02)
               + _rei("2025-07-10", -3, 25.02))
        txs, err, _ = qt_parse(old, new)
        self.assertFalse([t for t in txs if t["quantity"] == 3.0], err)

    def test_original_in_last_years_export(self):
        y25 = _qbuy("QZF.TO", 100, 8) + _rei("2025-12-30", 3, -25.02)
        y26 = _rei("2026-01-05", -3, 25.02)
        txs, err, _ = qt_parse(y25, y26)
        self.assertEqual(_held(txs), {"QZF.TO": 100.0}, err)

    def test_reversal_with_no_original_is_still_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaises(BrokerageParseError):
            qt_parse(_qbuy("QZF.TO", 100, 8), _rei("2026-01-05", -3, 25.02))

    def test_reversed_stock_dividend_has_no_note_and_keeps_the_later_one(self):
        body = (_qbuy("XTD.TO", 1000, 5) + _stk("2025-03-05", 2)
                + _stk("2025-03-10", -2) + _stk("2025-06-05", 2))
        txs, err, _ = qt_parse(body)
        sd = [t for t in txs if t.get("type") == "stock_dividend"]
        self.assertEqual([t["date"] for t in sd], ["2025-06-05"], err)
        self.assertNotIn("on 2025-03-05 booked", err)
        self.assertIn("on 2025-06-05 booked", err)



class TestRbcReinvestReversalsAcrossExports(unittest.TestCase):
    """A2-1044 / A2-1048: an RBC REI CANCEL pairs with its REI in any of
    the account's exports, whatever the file order with an overlap."""
    REI = row("December 30, 2025", "Dividends", "SRU.UN", "SMARTCENTRES",
              "2", "", "-64.48", "CAD", "REI - SMARTCENTRES REINV@C$32.24")
    CXL = row("January 5, 2026", "Dividends", "SRU.UN", "SMARTCENTRES",
              "-2", "", "64.48", "CAD",
              "REI - SMARTCENTRES REINV@C$32.24 CANCEL")
    BUY = row("March 3, 2025", "Buy", "SRU.UN", "SMARTCENTRES", "110",
              "25.00", "-2759.95", "CAD", "SMARTCENTRES UNSOLICITED")

    def test_original_in_the_other_export(self):
        txs, err, _ = parse_files({"rbc_2025.csv": self.REI + self.BUY,
                                   "rbc_2026.csv": self.CXL})
        self.assertEqual(_held(txs), {"SRU.UN.TO": 110.0}, err)

    def test_overlap_in_either_order(self):
        early = self.REI + self.BUY
        full = self.CXL + self.REI + self.BUY
        for files in ({"a_early.csv": early, "b_full.csv": full},
                      {"a_full.csv": full, "b_early.csv": early}):
            txs, err, _ = parse_files(files)
            self.assertEqual(_held(txs), {"SRU.UN.TO": 110.0},
                             (sorted(files), err))

    def test_fully_overlapped_file_is_not_a_zero_row_failure(self):
        # A2-0269: every row of the older download is in the newer one
        # (which also cancels the REI): --strict refused correct books.
        buy = row("March 3, 2025", "Buy", "SRQ.UN", "SRQ REIT", "100", "30",
                  "-3009.95", "CAD", "SRQ REIT UNSOLICITED DA")
        rei = row("April 18, 2025", "Dividends", "SRQ.UN", "SRQ REIT", "2",
                  "", "-64.48", "CAD", "REI - SRQ REIT REINV@C$32.24")
        cxl = row("April 22, 2025", "Dividends", "SRQ.UN", "SRQ REIT", "-2",
                  "", "64.48", "CAD", "REI - SRQ REIT REINV@C$32.24 CANCEL")
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", 2025,
                         {"rbc_older.csv": HDR + buy + rei,
                          "rbc_newer.csv": HDR + buy + rei + cxl})
            r = _cli_run(p, "run", "--no-input", "--strict")
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, out)
            self.assertNotIn("parsed to 0 transactions", out)
            lst = _cli_run(p, "list")
        self.assertRegex(lst.stdout, r"SRQ\.UN\.TO\s+100\s")

    def test_no_original_anywhere_is_refused(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        with self.assertRaises(RbcFormatError):
            parse_files({"rbc_2026.csv": self.CXL})



class TestRbcBookCostAdjustments(unittest.TestCase):
    """A2-0094, A2-0273, A2-1047."""

    def _one(self, activity, desc):
        return parse_one(row("December 31, 2025", activity, "XYZ.UN",
                             "XYZ TRUST", "", "", "0", "CAD", desc,
                             settle="March 20, 2026"))

    def test_return_of_capital_activity_lowers_the_acb(self):
        for desc in ("XYZ TRUST 2025 ADJUSTMENT TO BOOK COST $50.00",
                     "XYZ TRUST ROC ADJUSTMENT TO BOOK COST $50.00"):
            txs, err, _ = self._one("Return of Capital", desc)
            self.assertEqual([(t["action"], t["net_amount"]) for t in txs],
                             [("ADJUST", -50.0)], (desc, err))

    def test_activity_and_description_disagreeing_is_refused(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        with self.assertRaises(RbcFormatError):
            self._one("Return of Capital", "XYZ TRUST 2025 NOTIONAL "
                      "DISTRIBUTION ADJUSTMENT TO BOOK COST $50.00")

    def test_year_end_roc_says_the_income_already_holds_it(self):
        txs, err, _ = self._one("Distribution", "RTC - XYZ TRUST RETURN OF "
                                "CAPITAL ADJUSTMENT TO BOOK COST $30.00")
        self.assertEqual([t["net_amount"] for t in txs], [-30.0])
        self.assertTrue(any("counted twice" in ln for ln in _attention(err)),
                        err)

    def test_zero_adjustment_books_nothing(self):
        for desc in ("XYZ TRUST NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK "
                     "COST $0.00",
                     "XYZ TRUST RETURN OF CAPITAL ADJUSTMENT TO BOOK COST "
                     "$0.00"):
            txs, err, _ = self._one("Distribution", desc)
            self.assertEqual(txs, [], desc)
            self.assertNotIn("raises its ACB", err)


    def test_questrade_zero_net_book_cost_row_is_unbooked(self):
        # A2-1062: the Questrade twin was an 'informational' non-event.
        txs, err, _ = qt_parse(qdiv("XYZ.UN.TO", "XYZ TRUST RETURN OF CAPITAL "
                                    "ADJUSTMENT TO BOOK COST $1.16", "0",
                                    cur="CAD"))
        self.assertEqual(txs, [])
        self.assertIn("warning: UNBOOKED:", err)



def _parse_with(cls, text, name="x.csv"):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return cls().parse_file(p), err.getvalue()


class TestBuyRowsWithASaleSign(unittest.TestCase):
    """A2-0097, A2-0287, A2-1025, A2-1057: a Buy row whose quantity (and
    cash) say SALE is refused, as the generic importer refuses it."""

    def test_rbc(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        body = (row("January 12, 2024", "Buy", "XEI", "XEI ETF", "200", "24",
                    "-4809.95", "CAD", "XEI ETF UNSOLICITED")
                + row("February 12, 2024", "Buy", "XEI", "XEI ETF", "-100",
                      "25", "-2509.95", "CAD", "XEI ETF UNSOLICITED"))
        with self.assertRaises(RbcFormatError) as cm:
            parse_one(body)
        self.assertIn("NEGATIVE Quantity", str(cm.exception))

    def test_questrade(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaises(BrokerageParseError):
            qt_parse(q(qty="-40", price="170", gross="-6800", comm="-9.95",
                       net="-6809.95"))

    def test_webull(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.webull import WebullBrokerage
        wh = ('"Currency","Date","Action Code","Symbol","Security '
              'Description","Type Code","Quantity","Price","Proceeds"\n')
        wb = 'USD,15-01-2025,BUY,NVDA,NVIDIA CORP,STK,10,120.00,"(1201.00)"\n'
        bad = 'USD,20-03-2025,BUY,NVDA,NVIDIA CORP,STK,-4,150.00,"599.20"\n'
        with self.assertRaises(BrokerageParseError):
            _parse_with(WebullBrokerage, wh + wb + bad)
        txs, _err = _parse_with(WebullBrokerage, wh + wb)
        self.assertEqual([t["quantity"] for t in txs], [10.0])

    def test_coinbase(self):
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        h = ("Timestamp,Transaction Type,Asset,Quantity Transacted,Price "
             "Currency,Price at Transaction,Fees and/or Spread,Total "
             "(inclusive of fees and/or spread)\n")
        b = "2025-01-15 10:00:00 UTC,Buy,ETH,1.5,USD,3000,10,4510\n"
        bad = "2025-04-10 09:00:00 UTC,Buy,ETH,-0.5,USD,3400,5,-1695\n"
        with self.assertRaises(Exception) as cm:
            _parse_with(CoinbaseBrokerage, h + b + bad)
        self.assertIn("sale signature", str(cm.exception))
        sell = "2025-04-10 09:00:00 UTC,Sell,ETH,-0.5,USD,3400,5,-1695\n"
        txs, _err = _parse_with(CoinbaseBrokerage, h + b + sell)
        self.assertIn(-0.5, [t["quantity"] for t in txs])



class TestReinvestmentMoneyIdentity(unittest.TestCase):
    """A2-0268: a DRIP row whose cash is 10x units x price is refused when
    the price is in the row's currency, and kept when the REINV@ marker
    is in another currency (real cross-currency rows)."""

    def test_rbc(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        bad = row("April 18, 2025", "Dividends", "SRU.UN", "SMARTCENTRES",
                  "10", "", "-3224.00", "CAD",
                  "REI - SMARTCENTRES REINV@C$32.24")
        with self.assertRaises(RbcFormatError):
            parse_one(bad)
        ok = bad.replace("-3224.00", "-322.40")
        txs, _err, _ = parse_one(ok)
        self.assertEqual([t["net_amount"] for t in txs], [322.4])
        cross = bad.replace("REINV@C$", "REINV@U$")
        txs, _err, _ = parse_one(cross)
        self.assertEqual([t["net_amount"] for t in txs], [3224.0])

    def test_questrade(self):
        from taxjson.lib.brokerages.base import BrokerageParseError

        def rei(net, marker="C$"):
            return q(td="2025-06-30", action="REI", sym="SRU.UN.TO",
                     desc=f"SMARTCENTRES REINV@{marker}32.24", qty="10",
                     price="0", gross="0", comm="0", net=net, cur="CAD",
                     act="Dividend reinvestment")
        with self.assertRaises(BrokerageParseError):
            qt_parse(rei("-3224.00"))
        txs, _err, _ = qt_parse(rei("-322.40"))
        self.assertEqual([t["net_amount"] for t in txs], [322.4])
        txs, _err, _ = qt_parse(rei("-3224.00", "U$"))
        self.assertEqual([t["net_amount"] for t in txs], [3224.0])



class TestRbcCoveragePerAccount(unittest.TestCase):
    """A2-0272 / A2-0275 (per Account) and A2-1049 (trading days, no
    inverted range)."""

    def _msgs(self, files, year=2025, today=None):
        from datetime import date
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, (as_of, acct) in enumerate(files):
                body = (row("March 3, 2025", "Buy", "QZF", "QZ FUND", "100",
                            "10", "-1009.95", "CAD", "QZ FUND UNSOLICITED",
                            acct=acct)
                        if acct else "")
                p = Path(d) / f"rbc{i}.csv"
                p.write_text(f'"Activity Export as of {as_of}"\n\n' + HDR
                             + body)
                paths.append(p)
            with contextlib.redirect_stderr(io.StringIO()):
                ctx = RbcBrokerage.prepare_files(paths)
            return RbcBrokerage.coverage_messages(
                ctx, year, today=today or date(2026, 7, 10))

    def test_another_accounts_later_export_does_not_certify_this_one(self):
        m = self._msgs([("Dec 15, 2025", ACCT),
                        ("Jan 5, 2026", "55500002")])  # pii-ok
        att = [x for x in m if x.startswith(ATT)]
        self.assertEqual(len(att), 1, m)
        self.assertIn("rbc0.csv", att[0])
        self.assertIn("2025-12-16 to 2025-12-31", att[0])

    def test_dec31_export_has_no_inverted_range(self):
        m = self._msgs([("Dec 31, 2025", ACCT)])
        self.assertFalse(any(x.startswith(ATT) for x in m), m)
        self.assertFalse(any("2026-01-01 to 2025-12-31" in x for x in m))

    def test_weekend_only_gap_is_not_missing_trades(self):
        # 2023-12-29 is a Friday: only Sat/Sun are left in the year.
        m = self._msgs([("Dec 29, 2023", ACCT)], year=2023)
        self.assertFalse(any(x.startswith(ATT) for x in m), m)



class TestConcatenatedExportOrder(unittest.TestCase):
    """A2-0100: two newest-first Questrade exports concatenated (header
    repeated) keep each segment's real same-day order."""

    def test_questrade_segments(self):
        from test_fix_rbcqt import QH
        d = "XEI ETF WE ACTED AS AGENT"
        q1 = (q(td="2025-03-03", sym="XEI.TO", desc=d, qty="50", price="20",
                gross="-1000", comm="0", net="-1000", cur="CAD")
              + q(td="2025-03-03", action="Sell", sym="XEI.TO", desc=d,
                  qty="-100", price="20", gross="2000", comm="0", net="2000",
                  cur="CAD")
              + q(td="2025-01-06", sym="XEI.TO", desc=d, qty="100",
                  price="25", gross="-2500", comm="0", net="-2500", cur="CAD"))
        q2 = (q(td="2025-06-02", action="Sell", sym="XEI.TO", desc=d,
                qty="-50", price="22", gross="1100", comm="0", net="1100",
                cur="CAD")
              + q(td="2025-05-01", sym="XEI.TO", desc=d, qty="10",
                  price="21", gross="-210", comm="0", net="-210", cur="CAD"))
        for body in (q1 + QH + q2, q2 + QH + q1):
            txs, err, _ = qt_parse(body)
            mar3 = [t["quantity"] for t in txs if t["date"] == "2025-03-03"]
            self.assertEqual(mar3, [-100.0, 50.0], err)


    def test_generic_segments(self):
        # A2-1084: the generic importer's twin; the repeated header was
        # also reported as an UNBOOKED trade.
        from taxjson.lib.brokerages.generic import GenericBrokerage
        toml = ('[broker]\nname="acme"\n[columns]\ndate="Date"\n'
                'action="Type"\nsymbol="Ticker"\nquantity="Shares"\n'
                'price="Price"\namount="Amount"\nfee="Commission"\n'
                'currency="Currency"\n[actions]\n"BUY"="buy"\n'
                '"SELL"="sell"\n')
        gh = "Date,Type,Ticker,Shares,Price,Amount,Commission,Currency\n"
        seg2 = ("2025-03-20,BUY,XYZ,4,151,605,1,USD\n"
                "2025-03-20,SELL,XYZ,-4,150,599,1,USD\n"
                "2025-03-10,BUY,XYZ,1,100,101,1,USD\n")
        seg1 = ("2025-01-20,BUY,XYZ,2,100,201,1,USD\n"
                "2025-01-15,BUY,XYZ,10,120,1201,1,USD\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "acme.csv"
            p.write_text(gh + seg1 + gh + seg2)
            Path(str(p) + ".toml").write_text(toml)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                txs = GenericBrokerage().parse_file(p)
        mar20 = [t["quantity"] for t in txs if t["date"] == "2025-03-20"]
        self.assertEqual(mar20, [-4.0, 4.0], err.getvalue())
        self.assertNotIn("UNBOOKED", err.getvalue())


    def test_rbc_runs_without_a_second_header(self):
        # A2-1046: RBC refuses a repeated header; a plain concatenation
        # is read as newest-first runs.
        r2 = (row("March 20, 2025", "Buy", "RY", "ROYAL BANK", "4", "151",
                  "-613.95", "CAD", "ROYAL BANK UNSOLICITED")
              + row("March 20, 2025", "Sell", "RY", "ROYAL BANK", "-4", "150",
                    "590.05", "CAD", "ROYAL BANK UNSOLICITED")
              + row("March 10, 2025", "Buy", "RY", "ROYAL BANK", "1", "100",
                    "-109.95", "CAD", "ROYAL BANK UNSOLICITED"))
        r1 = (row("January 20, 2025", "Buy", "RY", "ROYAL BANK", "2", "100",
                  "-209.95", "CAD", "ROYAL BANK UNSOLICITED")
              + row("January 15, 2025", "Buy", "RY", "ROYAL BANK", "10",
                    "120", "-1209.95", "CAD", "ROYAL BANK UNSOLICITED"))
        txs, err, _ = parse_one(r1 + r2)
        mar20 = sorted((t["time"], t["quantity"]) for t in txs
                       if t["date"] == "2025-03-20")
        self.assertEqual([q_ for _t, q_ in mar20], [-4.0, 4.0], err)



class TestRbcSmallRowChecks(unittest.TestCase):
    """A2-1045, A2-1050, A2-1051."""

    def test_swallowed_row_error_names_the_stray_quote_line(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        # RBC writes a quote inside a Description unescaped: one that
        # ends right after it keeps the field open across the line
        # breaks (the reader's line_num is where the record ENDS).
        bad = row("May 1, 2024", "Sell", "ABC", "ABC CORP", "-100", "10",
                  "990.05", "CAD", 'ABC CORP PRINCIPAL "')
        rest = "".join(f"May {d} 2024,Buy,DEF,DEF CORP,50,20,May {d} 2024,"
                       f"{ACCT},-1000.00,CAD,DEF CORP\n" for d in range(2, 8))
        with self.assertRaises(RbcFormatError) as cm:
            parse_one(bad + rest)
        self.assertIn("rbc.csv:2:", str(cm.exception))

    def test_blank_symbol_tax_is_refused(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        tax = row("June 2, 2025", "Taxes", "", "", "", "", "-1.25", "USD",
                  "NRT - NON-RES TAX")
        with self.assertRaises(RbcFormatError):
            parse_one(tax)

    def test_files_without_account_column_do_not_contradict_dedup(self):
        from test_fix_rbc import HDR_NOACCT
        r = row("March 3, 2025", "Buy", "XYZ", "XYZ CORP", "10", "5.00",
                "-59.95", "CAD", "XYZ CORP UNSOLICITED", acct=None)
        _txs, err, _ = parse_files({"a.csv": r, "b.csv": r},
                                   header=HDR_NOACCT)
        self.assertNotIn("NOTHING was de-duplicated", err)
        self.assertIn("de-duplication decides", err)


    def test_merger_split_across_two_exports_is_corp_actions_not_unbooked(self):
        # A2-0271 (parser half; corp-actions pairs the legs, A2-0214).
        buy = row("March 3, 2025", "Buy", "QZH", "QZH CORPORATION", "100",
                  "50", "-5009.95", "USD", "QZH CORPORATION UNSOLICITED")
        rem = row("December 31, 2025", "Reorganization", "Q015283",
                  "QZH CORPORATION", "-100", "", "0", "USD",
                  "MGR - QZH CORPORATION MERGER TO QZC CORPORATION 1 NEW = "
                  "1 OLD")
        rcv = row("January 2, 2026", "Reorganization", "QZC",
                  "QZC CORPORATION", "100", "", "0", "USD",
                  "MGR - QZC CORPORATION SHRS RECEIVED THRU MERGER")
        txs, err, _ = parse_files({"rbc_2025.csv": buy + rem,
                                   "rbc_2026.csv": rcv})
        self.assertNotIn("UNBOOKED", err)
        self.assertEqual([t["symbol"] for t in txs], ["QZH.US"])
        self.assertIn("taxjson-corp-actions", err)


    def test_rbc_usd_class_of_a_tsx_etf_is_said(self):
        # A2-1043: not renamed (RBC's spelling is unverified), but said.
        txs, err, _ = parse_one(row(
            "March 3, 2025", "Buy", "ZSP", "BMO S&P 500 INDEX ETF US DOLLAR "
            "UNITS", "10", "50", "-509.95", "USD", "BMO S&P 500 INDEX ETF "
            "US DOLLAR UNITS UNSOLICITED"))
        self.assertEqual([t["symbol"] for t in txs], ["ZSP.US"])
        self.assertTrue(any("GLOBAL ZSP.US ZSP.U.TO" in ln
                            for ln in _attention(err)), err)



class TestQuestradeDescriptionNumbers(unittest.TestCase):
    """A2-0278, A2-1058 (decimal commas refused) and A2-1061 (a BRW
    journal's cost stays with its own security)."""

    def _brw(self, sym, qty, desc, cur="CAD"):
        return q(action="BRW", sym=sym, desc=desc, qty=qty, price="0",
                 gross="0", comm="0", net="0", cur=cur, act="Other")

    def test_cnv_rate_with_a_decimal_comma_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        body = (self._brw("DLR.TO", "-300", "GLOBAL X US DLR JOURNAL "
                          "POSITION TO USD")
                + self._brw("DLR.U.TO", "300", "GLOBAL X US DLR JOURNAL "
                            "POSITION FROM CAD BOOK VALUE: $2468.13 CNV@ "
                            "1,3579", cur="USD"))
        with self.assertRaises(BrokerageParseError):
            qt_parse(body)

    def test_cil_fraction_with_a_decimal_comma_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        for frac in ("1,5", "0,5"):
            cil = q(td="2025-07-30", action="CIL", sym="TDB.TO",
                    desc=f"TDB SPLIT CORP CASH IN LIEU OF {frac} SHARES",
                    qty="0", price="0", gross="0", comm="0", net="6.25",
                    cur="CAD", act="Other")
            with self.assertRaises(BrokerageParseError, msg=frac):
                qt_parse(cil)

    def test_journal_cost_pairs_by_security(self):
        body = (self._brw("XYZ.TO", "-100", "XYZ CORP JOURNAL POSITION TO "
                          "USD")
                + self._brw("DLR.TO", "-100", "DLR JOURNAL POSITION TO USD")
                + self._brw("DLR.U.TO", "100", "DLR JOURNAL POSITION FROM "
                            "CAD BOOK VALUE: $1000.00 CNV@ 1.40", cur="USD")
                + self._brw("XYZ", "100", "XYZ CORP JOURNAL POSITION FROM "
                            "CAD BOOK VALUE: $5000.00 CNV@ 1.40", cur="USD"))
        txs, err, _ = qt_parse(body)
        cost = {t["symbol"]: t.get("net_amount") for t in txs
                if t["quantity"] < 0}
        self.assertAlmostEqual(cost["DLR.TO"], 1400.0, msg=err)
        self.assertAlmostEqual(cost["XYZ.TO"], 7000.0, msg=err)


    def test_cad_settled_us_trade_commission_is_cad(self):
        # A2-0615: Buy 10 @100 USD at EXCHANGE RATE 1.38, Net -1393.73 CAD.
        buy = q(sym="ZZQ", desc="ZZQ INC WE ACTED AS AGENT EXCHANGE RATE "
                "1.38", qty="10", price="100", gross="-1000", comm="-9.95",
                net="-1393.73", cur="CAD")
        sell = q(td="2025-03-03", action="Sell", sym="ZZQ",
                 desc="ZZQ INC WE ACTED AS AGENT EXCHANGE RATE 1.40",
                 qty="-10", price="120", gross="1200", comm="-9.95",
                 net="1666.07", cur="CAD")
        txs, err, _ = qt_parse(buy + sell)
        got = {t["quantity"]: (t["commission"], t["net_amount"],
                               t["gross_amount"]) for t in txs}
        self.assertEqual(got[10.0][0], 13.73, err)
        self.assertEqual(got[-10.0][0], 13.93, err)
        for comm, net, gross in got.values():
            self.assertAlmostEqual(abs(gross) + comm if net > abs(gross)
                                   else abs(gross) - comm, net, places=2)



class TestPaymentsInLieuFromCanadianDealers(unittest.TestCase):
    """A2-0098: Questrade 'SUBST PAY ... IN LIEU OF DIVIDEND' and RBC
    'CASH / PAYMENT IN LIEU OF DIVIDEND' rows are payments in lieu paid
    by a Canadian dealer, not dividends."""

    def _books(self):
        from taxjson.lib.core import TaxTransaction
        qt_txs, _e, _ = qt_parse(
            qdiv("XLV", "HEALTH CARE SELECT SUBST PAY ON 100 SHS IN LIEU OF "
                 "DIVIDEND", "26.33")
            + qdiv("RY.TO", "ROYAL BANK SUBST PAY ON 100 SHS IN LIEU OF "
                   "DIVIDEND", "50.00", cur="CAD"))
        rbc_txs, _e, _ = parse_one(row(
            "June 2, 2025", "Dividends", "MSFT", "MICROSOFT CORP", "", "",
            "83.00", "USD", "MICROSOFT CORP CASH IN LIEU OF DIVIDEND"))
        return qt_txs, rbc_txs

    def test_parsers_emit_a_payment_in_lieu(self):
        qt_txs, rbc_txs = self._books()
        for t in qt_txs + rbc_txs:
            self.assertEqual((t["action"], t["type"], t["dealer_country"]),
                             ("DIVIDEND_IN_LIEU", "dividend_in_lieu", "CA"))

    @rule("CA-INC-03")
    @rule_absent("CA-INC-03", country="usa")
    @rule("US-INC-01")
    def test_canada_deems_only_the_canadian_issuer_a_dividend(self):
        from taxjson.lib.core import TaxTransaction
        qt_txs, rbc_txs = self._books()
        book = [TaxTransaction(**{k: v for k, v in t.items()
                                  if k in TaxTransaction.__dataclass_fields__})
                for t in qt_txs + rbc_txs]
        r = gains_both(book, year=2025)
        inc = {c: {t["symbol"]: t for t in r[c]["transactions"]
                   if t.get("action") == "DIVIDEND_IN_LIEU"}
               for c in ("canada", "usa")}
        self.assertEqual(inc["canada"]["RY.TO"].get("deemed_dividend"),
                         "ITA s.260")
        self.assertNotIn("deemed_dividend", inc["canada"]["XLV.US"])
        self.assertNotIn("deemed_dividend", inc["canada"]["MSFT.US"])
        for sym in ("RY.TO", "XLV.US", "MSFT.US"):
            self.assertNotIn("deemed_dividend", inc["usa"][sym])



class TestQuestradeCashRows(unittest.TestCase):
    """A2-0277 / A2-0614: deposits, withdrawals, interest and lending."""

    def _cash(self, action, atype, desc, net, cur="CAD"):
        return q(action=action, sym="", desc=desc, qty="0", price="0",
                 gross="0", comm="0", net=net, cur=cur, act=atype)

    def test_deposits_and_withdrawals_are_recognised_non_events(self):
        body = "".join(self._cash(a, t, d, n) for a, t, d, n in (
            ("EWD", "Withdrawals", "ELECTRONIC FUND TRANSFER", "-18000"),
            ("WDR", "Withdrawals", "WITHDRAWAL", "-500"),
            ("CON", "Deposits", "CONTRIBUTION", "1000"),
            ("DEP", "Deposits", "DEPOSIT", "1000"),
            ("EFT", "Deposits", "ELECTRONIC FUNDS TRANSFER", "1000")))
        txs, err, _ = qt_parse(body)
        self.assertEqual(txs, [])
        self.assertNotIn("unclassified", err)

    def test_interest_is_booked_with_its_sign(self):
        body = (self._cash("INT", "Interest", "INTEREST ON CREDIT BALANCE",
                           "12.34")
                + self._cash("INT", "Interest", "INTEREST CHARGED ON DEBIT "
                             "BALANCE", "-45.67", cur="USD"))
        txs, err, _ = qt_parse(body)
        self.assertEqual(sorted((t["action"], t["net_amount"]) for t in txs),
                         [("INTEREST", -45.67), ("INTEREST", 12.34)], err)

    def test_stock_lending_income_is_unbooked_out_loud(self):
        txs, err, _ = qt_parse(self._cash("LFJ", "Other",
                                          "STOCK LENDING INCOME", "3.21",
                                          cur="USD"))
        self.assertEqual(txs, [])
        self.assertIn("warning: UNBOOKED:", err)



class TestClassShareAssignmentStockLeg(unittest.TestCase):
    """A2-1059: the stock leg of an assignment on a class share (QRL.B
    under root QRL, BRK.B under BRKB) is the stock, not 100x contracts."""

    def test_questrade(self):
        for sym, desc, cur, want in (
                ("QRL.B.TO", "QRL TELECOM INC CL B ASSIGNMENT OF "
                 "OPTION CALL QRL 05/16/25 50", "CAD", "QRL.B.TO"),
                ("BRK.B", "BERKSHIRE HATHAWAY INC CL B ASSIGNMENT OF OPTION "
                 "CALL BRKB 05/16/25 50", "USD", "BRK.B.US")):
            txs, err, _ = qt_parse(q(action="Sell", sym=sym, desc=desc,
                                     qty="-100", price="50", gross="5000",
                                     comm="0", net="5000", cur=cur))
            self.assertEqual([(t["symbol"], t["quantity"]) for t in txs],
                             [(want, -100.0)], err)

    def test_rbc(self):
        txs, err, _ = parse_one(row(
            "May 16, 2025", "Sell", "QRL.B", "QRL TELECOM CL B", "-100", "50",
            "4990.05", "CAD", "QRL TELECOM CL B ASSIGNMENT OF OPTION CALL "
            "QRL 05/16/25 50"))
        self.assertEqual([(t["symbol"], t["quantity"]) for t in txs],
                         [("QRL.B.TO", -100.0)], err)



class TestCoordinatorHandOffs(unittest.TestCase):
    """A2-1041 (Questrade / RBC strike consumers) and A2-1042 (the
    Questrade half), left by the IB area."""

    def test_decimal_comma_strike_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaises(BrokerageParseError):
            qt_parse(q(sym="AAPL.OPT", desc="CALL AAPL 10/24/25 2,50",
                       qty="1", price="1.5", gross="-150", comm="0",
                       net="-150"))
        with self.assertRaises(BrokerageParseError):
            parse_one(row("March 3, 2025", "Buy", "8ZZZZZ3", "", "1", "1.5",
                          "-150", "CAD", "CALL .QZX 10/24/25 1,0000 QZX CORP "
                          "OPEN CONTRACT"))
        txs, _err, _ = parse_one(row(
            "March 3, 2025", "Buy", "8ZZZZZ3", "", "1", "1.5", "-150", "CAD",
            "CALL .QZX 10/24/25 1,050 QZX CORP OPEN CONTRACT"))
        self.assertEqual([t["symbol"] for t in txs],
                         ["QZX251024C01050000.TO"])

    def test_questrade_truncated_row_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        full = q()
        # Cut into the money columns: refused.
        cut = ",".join(full.rstrip("\n").split(",")[:9]) + "\n"
        with self.assertRaises(BrokerageParseError):
            qt_parse(cut)
        # Only the trailing account columns missing: booked, said.
        cut = ",".join(full.rstrip("\n").split(",")[:11]) + "\n"
        txs, err, _ = qt_parse(cut)
        self.assertEqual(len(txs), 1)
        self.assertTrue(any("Account Type" in ln for ln in _attention(err)),
                        err)


if __name__ == "__main__":
    unittest.main()
