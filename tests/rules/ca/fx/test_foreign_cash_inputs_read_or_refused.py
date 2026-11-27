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

Lows: `tjs elections` lists an event the sheltered default booked; a US
project's checklist has no T5 / T3 step; a malformed v2 cash line
says it is read only by ledger v2.

Every fixture is SYNTHETIC: invented tickers, amounts and account ids.

Formerly: test_fix_v0242_misc
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date as _date, timedelta as _td
from pathlib import Path

from _planted_rates import plant
from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import cash_events as CE
from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as V2
import _paths

REPO_ROOT = _paths.REPO

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


def _rates(path, cleanup):
    """The rates file, and the same rates in the suite's HOME (put back
    by `cleanup`): a run that rebuilds the file keeps them (TJ-0635)."""
    d, lines = _date(2025, 1, 1), []
    while d <= _date.today():
        r = ("1.3000" if d < _date(2025, 2, 10) else
             "1.3500" if d < _date(2025, 3, 10) else "1.4000")
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD {r} boc")
        d += _td(days=1)
    plant(path, "\n".join(lines) + "\n", cleanup=cleanup)


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
             PYTHONPATH=str(REPO_ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(td, acct, files, cleanup):
    root = Path(td) / "p"
    (root / "inputs" / acct).mkdir(parents=True)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(_TOML.format(acct=acct))
    for name, text in files.items():
        (root / "inputs" / acct / name).write_text(text)
    _rates(root / "work" / "to_base.csv", cleanup)
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout + r.stderr
    return root


# ------------------------------------------------------- Questrade (M8)

class TestQuestradeCashEvents(unittest.TestCase):

    def test_reader(self):
        # The parse's cash events (`cash_events`, PLAN step 16 commit 9; once
        # lib/cash_readers/questrade.py's).
        from _support.parser_cash import parsed_cash_events
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
            evs = parsed_cash_events("questrade", p)
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

    # Real order: CAD 200 -> USD 100, then USD 100 -> CAD 150, both on
    # 02-03, with a same-day USD withdrawal between them; deposits on
    # the days around anchor the date order.
    _REAL = (
        ("2025-02-02", "DEP", "DEPOSIT", 1, "CAD", "Deposits"),
        ("2025-02-03", "FXT", "CONVERSION - CAD/USD", -200, "CAD",
         "FX conversion"),
        ("2025-02-03", "FXT", "CONVERSION - CAD/USD", 100, "USD",
         "FX conversion"),
        ("2025-02-03", "WDR", "WITHDRAWAL", -5, "USD", "Withdrawals"),
        ("2025-02-03", "FXT", "CONVERSION - USD/CAD", -100, "USD",
         "FX conversion"),
        ("2025-02-03", "FXT", "CONVERSION - USD/CAD", 150, "CAD",
         "FX conversion"),
        ("2025-02-04", "DEP", "DEPOSIT", 1, "CAD", "Deposits"))

    def _events(self, rows):
        from _support.parser_cash import parsed_cash_events
        text = _QH + "".join(_qrow(d, a, "", desc, 0, 0, n, c, t)
                             for d, a, desc, n, c, t in rows)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "q.csv"
            p.write_text(text)
            return parsed_cash_events("questrade", p)

    @staticmethod
    def _seq(evs):
        return [(e["kind"], e["date"], e.get("from_ccy"), e.get("from_amt"),
                 e.get("to_ccy"), e.get("to_amt"), e.get("amount"))
                for e in evs]

    def test_newest_first_export_in_real_order(self):
        # Audit finding 75: Questrade's web export lists rows newest
        # first. Read in file order, the two same-day conversions were
        # replayed backward (USD -> CAD before CAD -> USD).
        want = [
            ("CASHMOVE", "2025-02-02", None, None, None, None, 1.0),
            ("FXCONV", "2025-02-03", "CAD", 200.0, "USD", 100.0, None),
            ("CASHMOVE", "2025-02-03", None, None, None, None, -5.0),
            ("FXCONV", "2025-02-03", "USD", 100.0, "CAD", 150.0, None),
            ("CASHMOVE", "2025-02-04", None, None, None, None, 1.0)]
        self.assertEqual(self._seq(self._events(self._REAL)), want)
        self.assertEqual(self._seq(self._events(self._REAL[::-1])), want)

    @rule(R.CA_FX_07)
    def test_newest_first_export_gain(self):
        # USD 100 held at CAD 100; buy USD 100 for CAD 200 (pool 200 at
        # CAD 300), sell USD 100 for CAD 150 (ACB 150): gain 0, USD 100
        # left at CAD 150. Replayed backward it was gain 50, cost 200.
        from taxjson.lib import fx_cash_v2 as F
        evs = [e for e in self._events(self._REAL[::-1])
               if e["kind"] == "FXCONV"]
        lines = []
        for text in ("CASHOPEN 2025-01-01 USD 100 100",
                     "CASHBAL 2025-12-31 USD 100"):
            e = CL.parse_line(text)
            e.update(book="margin/tt", label="margin", where="cash.tt:1")
            lines.append(e)
        for e in evs:
            e.update(book="margin/tt", label="margin")
        doc = F.build([], {"lines": lines, "events": evs, "problems": []},
                      "CAD", 2025, lambda c, d: 1.5, country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 0.0, places=2)
        self.assertAlmostEqual(
            doc["close"]["books"]["margin/tt"]["USD"]["cost"], 150.0,
            places=2)

    @rule(R.CA_FX_07)
    def test_v2_reads_the_conversions(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "qt", {"q.csv": _QT_CSV,
                                       "cash.tt": _CASH_TT}, self.addCleanup)
            j = json.loads(_cli(root, "fx-cash", "--json").stdout)
        self.assertEqual(j["status"], "computed", j["problems"])
        self.assertAlmostEqual(j["net_gain"], 500.0, places=2)
        self.assertEqual(j["borrowed"], [])
        self.assertEqual(j["unread_books"], [])


class TestQuestradeCashEventsFromItsParse(unittest.TestCase):
    """PLAN step 16 commit 9: Questrade's cash events are its parse's (classify
    notes each cash row, the finish builds them), no longer a second reading of
    the export (lib/cash_readers/questrade.py). A cash cell the parse itself
    never reads (a conversion's or a deposit's amount or date) is the file's
    `cash_problem`, worded byte for byte as the reader worded its error, never
    a refused parse."""

    _H = ("Transaction Date,Settlement Date,Action,Symbol,Quantity,Net Amount,"
          "Currency,Account #,Activity Type,Description,Price,Gross Amount,"
          "Commission,Account Type\n")
    _NUM = (" is not a number this parser accepts (decimal commas, space-grouped "
            "digits and text are refused rather than guessed) — a ")
    _FIX = " cash row the FX-on-cash ledger reads; fix the cell in the export"

    @classmethod
    def _row(cls, d, action, sym, qty, net, cur, atype, acct="99900099", settle=None):
        return (f"{d},{d if settle is None else settle},{action},{sym},{qty},{net},"
                f"{cur},{acct},{atype},synthetic,0,0,0,Margin\n")

    def _parsed(self, rows):
        from _support.parser_cash import parsed
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cash.csv"
            p.write_text(self._H + "".join(rows))
            return parsed("questrade", p)

    def test_a_cash_cell_the_parse_never_reads_is_the_files_problem(self):
        r, n, f = self._row, self._NUM, self._FIX
        cases = (
            # The review's repro: a conversion leg's Net Amount.
            ([r("2025-03-03", "FXT", "", 0, -1300, "CAD", "FX conversion"),
              r("2025-03-03", "FXT", "", 0, "abc", "USD", "FX conversion")],
             "BrokerageParseError: cash.csv line 3: Net Amount 'abc'" + n + "FXT" + f),
            ([r("2025-03-05", "LFJ", "ZZQ", 0, "n/a", "USD", "Other")],
             "BrokerageParseError: cash.csv line 2: Net Amount 'n/a'" + n + "LFJ" + f),
            ([r("2025-03-06", "TF6", "", 0, "abc", "USD", "Transfers")],
             "BrokerageParseError: cash.csv line 2: Net Amount 'abc'" + n + "TF6" + f),
            ([r("2025-03-07", "WDR", "", 0, "N/A", "USD", "Withdrawals")],
             "BrokerageParseError: cash.csv line 2: Net Amount 'N/A'" + n + "WDR" + f),
            ([r("pending", "FXT", "", 0, -100, "USD", "FX conversion", settle="")],
             "BrokerageParseError: cash.csv line 2: Transaction Date 'pending' is not a date"),
            # Newest first: the first error in the order the rows happened.
            ([r("2025-03-09", "DEP", "", 0, "late", "CAD", "Deposits"),
              r("2025-03-08", "FXT", "", 0, "early", "USD", "FX conversion")],
             "BrokerageParseError: cash.csv line 3: Net Amount 'early'" + n + "FXT" + f),
        )
        for rows, problem in cases:
            with self.subTest(problem[:60]):
                parser = self._parsed(rows)
                self.assertEqual((parser.cash_events, parser.cash_problem), ([], problem))

    def test_the_legs_pair_in_their_account_and_the_cells_read_as_the_reader_read_them(self):
        from taxjson.lib.privacy import hash_broker_account
        r = self._row
        parser = self._parsed([
            r("2025-04-01", "FXT", "", 0, -130, "CAD", "FX conversion", acct="99900009"),
            r("2025-04-01", "FXT", "", 0, -50, "USD", "FX conversion"),
            r("2025-04-01", "FXT", "", 0, 100, "USD", "FX conversion", acct="99900009"),
            r("2025-04-01", "FXT", "", 0, 70, "CAD", "FX conversion"),
            # A code and a currency in lower case; no settlement date.
            r("2025-04-02", "dep", "", 0, 25, "usd", "Deposits", acct="99900009", settle=""),
            r("2025-04-03", "DEP", "ZZQ", 0, 25, "USD", "Deposits"),      # a symbol: no move
            r("2025-04-03", "WDR", "", 0, -0.004, "USD", "Withdrawals"),  # under a cent
            r("2025-04-04", "TF6", "ZZQ", 5, -10, "USD", "Transfers"),    # units: no move
            r("2025-04-05", "LFJ", "", 0, 1.5, "USD", "Other", settle="2025-04-07"),
            # Two legs of one currency are no conversion: each a lone leg.
            r("2025-04-06", "FXT", "", 0, -20, "USD", "FX conversion"),
            r("2025-04-06", "FXT", "", 0, 20, "USD", "FX conversion"),
            # A cash-move code under another Activity Type is a move still.
            r("2025-04-07", "CON", "", 0, 40, "CAD", "Other"),
        ])
        self.assertEqual(parser.cash_problem, "")
        a9, a99 = hash_broker_account("99900009"), hash_broker_account("99900099")
        got = [(e["kind"], e["account"], e["date"], e["settle"], e.get("from_ccy"),
                e.get("from_amt"), e.get("to_ccy"), e.get("to_amt"), e.get("currency"),
                e.get("amount"), e["desc"], e["line_where"]) for e in parser.cash_events]
        self.assertEqual(got, [
            ("FXCONV", a9, "2025-04-01", "2025-04-01", "CAD", 130.0, "USD", 100.0, None,
             None, "FXT", "cash.csv:2"),
            ("FXCONV", a99, "2025-04-01", "2025-04-01", "USD", 50.0, "CAD", 70.0, None,
             None, "FXT", "cash.csv:3"),
            ("CASHMOVE", a9, "2025-04-02", "2025-04-02", None, None, None, None, "USD",
             25.0, "DEP", "cash.csv:6"),
            ("FLOW", a99, "2025-04-05", "2025-04-07", None, None, None, None, "USD", 1.5,
             "LFJ stock-lending income", "cash.csv:10"),
            ("CASHMOVE", a99, "2025-04-06", "2025-04-06", None, None, None, None, "USD",
             -20.0, "FXT conversion leg with no other leg", "cash.csv:11"),
            ("CASHMOVE", a99, "2025-04-06", "2025-04-06", None, None, None, None, "USD",
             20.0, "FXT conversion leg with no other leg", "cash.csv:12"),
            ("CASHMOVE", a99, "2025-04-07", "2025-04-07", None, None, None, None, "CAD",
             40.0, "CON", "cash.csv:13")])

    def test_each_kind_test_and_each_pairing_key_reads_as_the_reader_read_it(self):
        # The review of commit 9 (TJ-4444): a row for each test of a cash
        # row's kind, and for each part of a conversion's pairing key.
        from taxjson.lib.privacy import hash_broker_account
        r, fx = self._row, "FX conversion"
        parser = self._parsed([
            r("2025-01-02", "", "", 0, 500, "CAD", "Deposits"),     # a type, no code
            r("2025-01-03", "", "", 0, 250, "CAD", "Transfers"),    # a journal by its type
            r("2025-01-04", "TF6", "", 0, 125, "CAD", "Other"),     # a journal by its code
            r("2025-01-05", "DEP", "", 5, 77, "CAD", "Deposits"),   # units: no move
            # Both legs out, in two currencies: no conversion.
            r("2025-01-06", "FXT", "", 0, -952, "CAD", fx),
            r("2025-01-06", "FXT", "", 0, -700, "USD", fx),
            # One day, two settlement dates: no conversion.
            r("2025-01-07", "FXT", "", 0, -136, "CAD", fx),
            r("2025-01-07", "FXT", "", 0, 100, "USD", fx, settle="2025-01-08"),
            # A conversion settles on its Settlement Date.
            r("2025-01-09", "FXT", "", 0, -272, "CAD", fx, settle="2025-01-10"),
            r("2025-01-09", "FXT", "", 0, 200, "USD", fx, settle="2025-01-10"),
            r("2025-01-11", "DEP", "", 0, 60, "CAD", "Deposits", acct=""),
            # A blank Settlement Date is the trade day's: the legs pair.
            r("2025-01-12", "FXT", "", 0, -408, "CAD", fx, settle=""),
            r("2025-01-12", "FXT", "", 0, 300, "USD", fx),
        ])
        self.assertEqual(parser.cash_problem, "")
        a = hash_broker_account("99900099")
        lone = "FXT conversion leg with no other leg"
        got = [(e["kind"], e["account"], e["date"], e["settle"], e.get("from_ccy"),
                e.get("from_amt"), e.get("to_ccy"), e.get("to_amt"), e.get("currency"),
                e.get("amount"), e["desc"], e["line_where"]) for e in parser.cash_events]
        self.assertEqual(got, [
            ("CASHMOVE", a, "2025-01-02", "2025-01-02", None, None, None, None, "CAD",
             500.0, "Deposits", "cash.csv:2"),
            ("CASHMOVE", a, "2025-01-03", "2025-01-03", None, None, None, None, "CAD",
             250.0, "Transfers", "cash.csv:3"),
            ("CASHMOVE", a, "2025-01-04", "2025-01-04", None, None, None, None, "CAD",
             125.0, "TF6", "cash.csv:4"),
            ("CASHMOVE", a, "2025-01-06", "2025-01-06", None, None, None, None, "CAD",
             -952.0, lone, "cash.csv:6"),
            ("CASHMOVE", a, "2025-01-06", "2025-01-06", None, None, None, None, "USD",
             -700.0, lone, "cash.csv:7"),
            ("CASHMOVE", a, "2025-01-07", "2025-01-07", None, None, None, None, "CAD",
             -136.0, lone, "cash.csv:8"),
            ("CASHMOVE", a, "2025-01-07", "2025-01-08", None, None, None, None, "USD",
             100.0, lone, "cash.csv:9"),
            ("FXCONV", a, "2025-01-09", "2025-01-10", "CAD", 272.0, "USD", 200.0, None,
             None, "FXT", "cash.csv:10"),
            ("CASHMOVE", "", "2025-01-11", "2025-01-11", None, None, None, None, "CAD",
             60.0, "DEP", "cash.csv:12"),
            ("FXCONV", a, "2025-01-12", "2025-01-12", "CAD", 408.0, "USD", 300.0, None,
             None, "FXT", "cash.csv:13")])

    def test_the_sidecar_holds_the_files_problem_and_the_parse_goes_on(self):
        import subprocess
        from taxjson.lib.dispatch import python_module_argv
        r = self._row
        with tempfile.TemporaryDirectory() as td:
            a, b, side = Path(td) / "a.csv", Path(td) / "b.csv", Path(td) / "m.cash"
            a.write_text(self._H + r("2025-03-03", "FXT", "", 0, "abc", "USD",
                                     "FX conversion"))
            b.write_text(self._H + r("2025-03-04", "DEP", "", 0, 500, "CAD", "Deposits"))
            res = subprocess.run(python_module_argv(
                "taxjson.bin.taxjson_brokerage",
                ["--brokerage", "questrade", str(a), str(b), "--country", "canada",
                 "--account", "m", "--cash-out", str(side)]),
                cwd=_paths.REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0"), timeout=600)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("Net Amount", res.stderr)
            doc = json.loads(side.read_text(encoding="utf-8"))
        got = {f["name"]: ([(e["kind"], e["amount"]) for e in f["events"]], f["problem"])
               for f in doc["files"]}
        self.assertEqual(got, {
            "a.csv": ([], "BrokerageParseError: a.csv line 2: Net Amount 'abc'" + self._NUM
                      + "FXT" + self._FIX),
            "b.csv": ([("CASHMOVE", 500.0)], "")})


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

    @rule(R.CA_FX_07)
    def test_webull_refused_until_declared_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "wb", {"wb.csv": _WB, "cash.tt": _CASH_TT},
                            self.addCleanup)
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
            ev = CL.parse_line(ln, "inputs/m/c.tt:1")
            ev.update(label="m", file="c.tt", book="m/generic",
                      book_problem="")
            out.append(ev)
        return out

    @rule(R.CA_FX_07)
    def test_generic_refused_canada(self):
        cash = {"events": [], "lines": self._lines("USD"),
                "books": self._books(), "problems": []}
        doc = V2.build(self._native("USD"), cash, "CAD", 2025,
                       lambda c, d: 1.35, country="canada")
        self.assertEqual([p["kind"] for p in doc["problems"]], ["unread"])

    @rule(R.US_FX_03)
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
        ev = CL.parse_line("CASHBOOK webull complete", "c.tt:1")
        self.assertEqual((ev["book_name"], ev["complete"]),
                         ("webull", True))
        self.assertFalse(CL.parse_line("CASHBOOK webull")["complete"])
        with self.assertRaises(CL.CashLineError):
            CL.parse_line("CASHBOOK webull done", "c.tt:1")


class TestCombinedIbStatement(unittest.TestCase):

    @rule(R.CA_FX_07)
    def test_one_book_said(self):
        from _support.fx_ledger import IB_STATEMENT
        from _support.parser_cash import parsed_cash_events
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.csv"
            p.write_text(IB_STATEMENT)
            evs = parsed_cash_events("ib", p)
            one = Path(td) / "one.csv"
            one.write_text(IB_STATEMENT.replace("U55X02", "U55X01"))
            evs_one = parsed_cash_events("ib", one)
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
        from taxjson.lib.tt_format import tt_to_json
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

    @rule(R.CA_SLIP_01)
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
                out = cli(root, "checklist", "--run", "--quick", "--all").stdout
                self.assertEqual("T5 / T3" in out, c == "canada", out)


class TestElectListsShelteredDefault(unittest.TestCase):

    def test_listed_not_no_elections(self):
        from taxjson.lib.country import CANADA
        from _support.ib_sheltered_elections import _CA_PLAN, _event_id, _flat, _sheltered_project
        from tax_rules.dual import cli
        with tempfile.TemporaryDirectory() as td:
            root = _sheltered_project(td, CANADA, _CA_PLAN)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _flat(r)[-2000:])
            eid = _event_id(root, _CA_PLAN)
            text = _flat(cli(root, "elections"))
            js = json.loads(cli(root, "elections", "--json").stdout)
        self.assertIn(f"{eid}: sheltered default ($0 cost for the "
                      f"distributed shares)", text)
        self.assertIn("spin-off SPNCO", text)
        self.assertNotIn(f"No elections recorded: {_CA_PLAN}", text)
        rec = js["accounts"][_CA_PLAN][eid]
        self.assertEqual((rec["election"], rec["saved"]),
                         ("sheltered_default", False))


if __name__ == "__main__":
    unittest.main()
