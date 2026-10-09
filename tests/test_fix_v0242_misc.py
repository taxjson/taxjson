"""Pre-release review of v0.24.2: the FX-on-cash ledger v2's inputs (M8)
and a handful of lows.

M8: v2 computed a figure for a Questrade, Webull or generic account
without reading its conversions, deposits or withdrawals — a Questrade
account that converted CAD to USD, bought and sold a US share and
converted back read as a USD margin loan (net -500 where the year made
+500). Questrade's FXT / deposit / withdrawal / cash-transfer / LFJ rows
are now read; an account of a broker with no reader that moves foreign
cash is refused until `CASHBOOK <book> complete` says its .tt cash lines
are all of them.

Lows: `taxjson elect` lists an event the sheltered default booked; a US
project's checklist has no T5 / T3 step; a malformed v2 cash line
says it is read only by ledger v2.

Every fixture is SYNTHETIC: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date as _date, timedelta as _td
from pathlib import Path

from tax_rules import rule

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2

REPO_ROOT = Path(__file__).resolve().parent.parent

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qrow(d, action, sym, desc, qty, price, net, cur, atype,
          acct="55500001"):                                # pii-ok
    return (f"{d} 09:30:00 AM,{d} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price},{net},0.00,{net},{cur},{acct},{atype},"
            f"Individual margin\n")


# CAD 13,000 -> USD 10,000 (01-15); buy 100 XYZ for US$10,000 (02-12,
# rate 1.35): +500; sell for US$10,000 (03-12, 1.40); USD 10,000 -> CAD
# 14,000 (04-15): 14,000 - 14,000 = 0. Net +500. Read without the
# conversions it was a US$10,000 loan at 1.35 repaid at 1.40: -500.
_QT_CSV = _QH + "".join((
    _qrow("2025-01-15", "FXT", "", "CONVERSION - CAD/USD", 0, 0,
          -13000, "CAD", "FX conversion"),
    _qrow("2025-01-15", "FXT", "", "CONVERSION - CAD/USD", 0, 0,
          10000, "USD", "FX conversion"),
    _qrow("2025-02-12", "Buy", "XYZ", "XYZ CORP", 100, 100, -10000,
          "USD", "Trades"),
    _qrow("2025-03-12", "Sell", "XYZ", "XYZ CORP", -100, 100, 10000,
          "USD", "Trades"),
    _qrow("2025-04-15", "FXT", "", "CONVERSION - USD/CAD", 0, 0,
          -10000, "USD", "FX conversion"),
    _qrow("2025-04-15", "FXT", "", "CONVERSION - USD/CAD", 0, 0,
          14000, "CAD", "FX conversion"),
))

_TOML = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = ["USD"]
option_grant_timing_since = 2025
fx_cash_ledger = "v2"

[accounts.{acct}]
type = "taxable"
"""

_CASH_TT = "CASHOPEN 2025-01-01 USD 0 0\nCASHBAL 2025-12-31 USD 0\n"


def _rates(path):
    d, lines = _date(2025, 1, 1), []
    while d <= _date.today():
        r = ("1.3000" if d < _date(2025, 2, 10) else
             "1.3500" if d < _date(2025, 3, 10) else "1.4000")
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD {r} boc")
        d += _td(days=1)
    path.write_text("\n".join(lines) + "\n")


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
             PYTHONPATH=str(REPO_ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(td, acct, files):
    root = Path(td) / "p"
    (root / "inputs" / acct).mkdir(parents=True)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(_TOML.format(acct=acct))
    for name, text in files.items():
        (root / "inputs" / acct / name).write_text(text)
    _rates(root / "work" / "to_base.csv")
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout + r.stderr
    return root


# ------------------------------------------------------- Questrade (M8)

class TestQuestradeCashEvents(unittest.TestCase):

    def test_reader(self):
        from taxjson.lib.brokerages.questrade import questrade_cash_events
        text = _QT_CSV + "".join((
            _qrow("2025-05-01", "DEP", "", "DEPOSIT", 0, 0, 250, "USD",
                  "Deposits"),
            _qrow("2025-05-02", "WDR", "", "WITHDRAWAL", 0, 0, -40, "USD",
                  "Withdrawals"),
            _qrow("2025-05-03", "TF6", "", "TRANSFER TO 55500002", 0, 0,
                  -60, "USD", "Transfers"),                 # pii-ok
            _qrow("2025-05-04", "LFJ", "", "STOCK LENDING INCOME", 0, 0,
                  1.25, "USD", "Other"),
            _qrow("2025-05-05", "FXT", "", "CONVERSION - USD/CAD", 0, 0,
                  -5, "USD", "FX conversion"),
        ))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "q.csv"
            p.write_text(text)
            evs = questrade_cash_events(p)
        convs = [(e["date"], e["from_ccy"], e["from_amt"], e["to_ccy"],
                  e["to_amt"]) for e in evs if e["kind"] == "FXCONV"]
        self.assertEqual(convs, [
            ("2025-01-15", "CAD", 13000.0, "USD", 10000.0),
            ("2025-04-15", "USD", 10000.0, "CAD", 14000.0)])
        moves = [(e["date"], e["currency"], e["amount"], e["desc"])
                 for e in evs if e["kind"] == "CASHMOVE"]
        self.assertEqual(moves, [
            ("2025-05-01", "USD", 250.0, "DEP"),
            ("2025-05-02", "USD", -40.0, "WDR"),
            ("2025-05-03", "USD", -60.0, "TF6"),
            ("2025-05-05", "USD", -5.0,
             "FXT conversion leg with no other leg")])
        flows = [(e["currency"], e["amount"]) for e in evs
                 if e["kind"] == "FLOW"]
        self.assertEqual(flows, [("USD", 1.25)])
        self.assertTrue(all(e["account"] for e in evs))
        self.assertNotIn("55500001", json.dumps(evs))     # pii-ok

    @rule("CA-FX-07")
    def test_v2_reads_the_conversions(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "qt", {"q.csv": _QT_CSV,
                                       "cash.tt": _CASH_TT})
            j = json.loads(_cli(root, "fx-cash", "--json").stdout)
        self.assertEqual(j["status"], "computed", j["problems"])
        self.assertAlmostEqual(j["net_gain"], 500.0, places=2)
        self.assertEqual(j["borrowed"], [])
        self.assertEqual(j["unread_books"], [])


# ----------------------------------------------- brokers with no reader

_WB_ACCT = "55500002"                                       # pii-ok
_WB = "Webull Securities (Canada) Ltd.\nSynthetic Demo Statement\n" + (
    "Account Number: " + _WB_ACCT + "\n") + """\
Date Range: January 1 2025 - December 31 2025

"Currency","Date","Action Code","Symbol","Security Description","Type Code","Quantity","Price","Proceeds"
USD,12-02-2025,BUY,@XYZ,XYZ CORP,EQ,100,100.00,"(10,000.00)"
USD,12-03-2025,SELL,@XYZ,XYZ CORP,EQ,100,100.00,"10,000.00"
"""


class TestUnreadBroker(unittest.TestCase):

    @rule("CA-FX-07")
    def test_webull_refused_until_declared_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "wb", {"wb.csv": _WB, "cash.tt": _CASH_TT})
            j = json.loads(_cli(root, "fx-cash", "--json").stdout)
            self.assertEqual(j["status"], "not_computed")
            p, = j["problems"]
            self.assertEqual((p["kind"], p["book"]), ("unread", "wb/webull"))
            self.assertIn("`CASHBOOK webull complete`", p["text"])
            self.assertEqual(j["unread_books"], [
                {"book": "wb/webull", "broker": "webull",
                 "complete": False, "moves": True}])
            (root / "inputs" / "wb" / "cash.tt").write_text(
                _CASH_TT + "FXCONV 2025-01-15 CAD 13000 USD 10000\n"
                "FXCONV 2025-04-15 USD 10000 CAD 14000\n"
                "CASHBOOK webull complete\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            j = json.loads(_cli(root, "fx-cash", "--json").stdout)
            text = " ".join(_cli(root, "fx-cash").stdout.split())
        self.assertEqual(j["status"], "computed", j["problems"])
        self.assertAlmostEqual(j["net_gain"], 500.0, places=2)
        self.assertTrue(j["unread_books"][0]["complete"])
        self.assertIn("NOT READ FROM THE EXPORT", text)
        self.assertIn("wb/webull webull declared complete (CASHBOOK)", text)

    def _books(self):
        b = CE.Books()
        b.add("m", "generic", "")
        return b

    def _native(self, cur):
        return [{"action": "BUYSELL", "date": "2025-02-10",
                 "date_settle": "2025-02-10", "time": "10:00:00",
                 "symbol": "XYZ", "quantity": 10, "net_amount": 100.0,
                 "currency": cur, "account": "m", "_book": "m/generic"}]

    def _lines(self, cur, extra=""):
        out = []
        for ln in (f"CASHOPEN 2025-01-01 {cur} 100 130",
                   f"CASHBAL 2025-12-31 {cur} 0") + ((extra,) if extra
                                                     else ()):
            ev = CE.parse_line(ln, "inputs/m/c.tt:1")
            ev.update(label="m", file="c.tt", book="m/generic",
                      book_problem="")
            out.append(ev)
        return out

    @rule("CA-FX-07")
    def test_generic_refused_canada(self):
        cash = {"events": [], "lines": self._lines("USD"),
                "books": self._books(), "problems": []}
        doc = V2.build(self._native("USD"), cash, "CAD", 2025,
                       lambda c, d: 1.35, country="canada")
        self.assertEqual([p["kind"] for p in doc["problems"]], ["unread"])

    @rule("US-FX-03")
    def test_generic_refused_usa(self):
        b = self._books()
        cash = {"events": [], "lines": self._lines("CAD"), "books": b,
                "problems": []}
        doc = V2.build(self._native("CAD"), cash, "USD", 2025,
                       lambda c, d: 0.74, country="usa")
        self.assertEqual([p["kind"] for p in doc["problems"]], ["unread"])
        b.complete.add("m/generic")
        doc = V2.build(self._native("CAD"), cash, "USD", 2025,
                       lambda c, d: 0.74, country="usa")
        self.assertEqual(doc["status"], "computed", doc["problems"])

    def test_cashbook_line(self):
        ev = CE.parse_line("CASHBOOK webull complete", "c.tt:1")
        self.assertEqual((ev["book_name"], ev["complete"]),
                         ("webull", True))
        self.assertFalse(CE.parse_line("CASHBOOK webull")["complete"])
        with self.assertRaises(CE.CashLineError):
            CE.parse_line("CASHBOOK webull done", "c.tt:1")


class TestCombinedIbStatement(unittest.TestCase):

    @rule("CA-FX-07")
    def test_one_book_said(self):
        from test_fix_fx_cash_v2 import IB_STATEMENT
        from taxjson.lib.brokerages.ib_extractor import ib_cash_events
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.csv"
            p.write_text(IB_STATEMENT)
            evs = ib_cash_events(p)
            one = Path(td) / "one.csv"
            one.write_text(IB_STATEMENT.replace("U55X02", "U55X01"))
            evs_one = ib_cash_events(one)
        note, = [e for e in evs if e["kind"] == "NOTE"]
        self.assertIn("one IB statement of 2 accounts (#", note["text"])
        self.assertIn("is one cash book — its Cash Report is their "
                      "combined balance", note["text"])
        self.assertNotIn("U55X0", note["text"])
        self.assertEqual([e for e in evs_one if e["kind"] == "NOTE"], [])
        # The ledger shows it as a note.
        b = CE.Books()
        b.add("m", "ib", "")
        for e in evs:
            e.update(label="m", broker="ib", file="ib.csv", book="m/ib")
        doc = V2.build([], {"events": evs, "lines": [], "books": b,
                            "problems": []}, "CAD", 2025,
                       lambda c, d: 1.35, country="canada")
        self.assertTrue(any("one IB statement of 2 accounts" in n
                            for n in doc["notes"]))


# ------------------------------------------------------------- the lows

class TestMalformedCashLine(unittest.TestCase):

    def test_says_it_is_a_v2_line(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.tt"
            p.write_text("CASHBAL 2025-12-31 USD\n")
            with self.assertRaises(ValueError) as cm:
                tt_to_json(p, "margin")
        msg = str(cm.exception)
        self.assertIn("malformed CASHBAL line", msg)
        self.assertIn("a line of the FX-on-cash ledger v2 only", msg)
        self.assertIn("the default ledger ignores it", msg)


class TestChecklistSlipStep(unittest.TestCase):

    @rule("CA-SLIP-01")
    def test_absent_in_a_us_project(self):
        """A US project's checklist has no T5 / T3 step: its t5-t3 item
        is the 1099-DIV / 1099-INT comparison, with no slip-audit."""
        from taxjson.lib.checklist import items

        def t5(country):
            return next(i for i in items(2025, country) if i.id == "t5-t3")
        self.assertIn("T5 / T3", t5("canada").title)
        self.assertIn("T5 / T3", t5(None).title)
        self.assertNotIn("T5", t5("usa").title)
        self.assertNotIn("tjs slip-audit",
                         [c.command for c in t5("usa").cmds])
        for c in ("canada", "usa"):
            with self.subTest(country=c), \
                    tempfile.TemporaryDirectory() as td:
                from tax_rules.dual import cli, projects_both
                root = projects_both(td)[c]
                out = cli(root, "checklist", "--quick").stdout
                self.assertEqual("T5 / T3" in out, c == "canada", out)


class TestElectListsShelteredDefault(unittest.TestCase):

    def test_listed_not_no_elections(self):
        from test_feat_sheltered_elections import (
            TestCanadaRegisteredAccount as _Fx, _flat)
        from tax_rules.dual import cli
        fx = _Fx()
        with tempfile.TemporaryDirectory() as td:
            root = fx._project(td)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _flat(r)[-2000:])
            eid = fx._event_id(root, fx.PLAN)
            text = _flat(cli(root, "elect"))
            js = json.loads(cli(root, "elect", "--json").stdout)
        self.assertIn(f"{eid}: sheltered default ($0 cost for the "
                      f"distributed shares)", text)
        self.assertIn("spin-off SPNCO", text)
        self.assertNotIn(f"No elections recorded: {fx.PLAN}", text)
        rec = js["accounts"][fx.PLAN][eid]
        self.assertEqual((rec["election"], rec["saved"]),
                         ("sheltered_default", False))


if __name__ == "__main__":
    unittest.main()
