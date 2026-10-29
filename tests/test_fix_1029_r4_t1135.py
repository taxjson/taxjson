"""Fixes of 2026-10-29, round 4, group t1135: the cash an account
holds outside Canada in the T1135 test (tax-logic CA-RPT-20), on the
walk's own date basis (backlog TJ-0627), and the interest it earns as
its income (CA-RPT-14, TJ-0628). One test class per backlog item, each
named for it.

Every fixture is SYNTHETIC: invented tickers (ZZ*), invented account
numbers (999...), invented account names, small amounts.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date as _date
from datetime import timedelta as _td
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.bin import taxjson_t1135 as T
from taxjson.lib import fx_cash_v2 as FX
from taxjson.lib import t1135_cash as TC
from taxjson.lib.core import TaxTransaction
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent

BOOK = "abroad/tt"


def _tt(kind, date, cur, n=1, **kw):
    """A .tt cash line of the account `abroad` as lib/cash_events reads
    it."""
    return dict({"kind": kind, "origin": "tt", "book": BOOK,
                 "label": "abroad", "date": date, "settle": date,
                 "currency": cur, "where": f"cash.tt:{n}"}, **kw)


def _bal(d, cur, balance, n=9):
    return {"kind": "CASHBAL", "book": BOOK, "label": "abroad", "date": d,
            "currency": cur, "balance": balance, "where": f"cash.tt:{n}"}


def _native(td, sd, q, net, sym="ZZQX.US"):
    """A native row of the account: `q` shares bought (q > 0, `net`
    negative) or sold for `net` US dollars, traded `td`, settled
    `sd`."""
    return {"action": "BUYSELL", "type": "", "date": td, "date_settle": sd,
            "time": "09:30:00", "symbol": sym, "quantity": q,
            "currency": "USD", "net_amount": net, "price": abs(net / q),
            "description": "trade", "account": "abroad", "_book": BOOK}


def _base(td, sd, q, net, sym="ZZQX.US"):
    """The same row in the base book (CAD at 1.35)."""
    return TaxTransaction(action="BUYSELL", date=td, date_settle=sd,
                          time="09:30:00", symbol=sym, quantity=q,
                          net_amount=net, currency="CAD", account="abroad",
                          id=f"b-{td}-{sym}-{q}")


def _rate(_c, _d):
    return 1.35


def _ledger(native, cash, basis):
    doc = FX.build(native, cash, "CAD", 2025, _rate, country="canada",
                   holdings=True, holdings_basis=basis)
    return doc


def _report(base_rows, cash_doc, basis, gains=False):
    """build_report over the base rows (one file) with `cash_doc`, on
    the date basis `basis`; with `gains` the account's gains file as
    `taxjson run` writes it (the INCOME column), else none."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "abroad_base.json"
        p.write_text(json.dumps({"transactions": [r.to_dict()
                                                  for r in base_rows]}))
        gpaths = []
        with contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(T, "_DETAILS", True):
            if gains:
                g = Path(td) / "abroad_gains.json"
                g.write_text(json.dumps(run_gains(
                    list(base_rows), [], [], GainsRequest(
                        country="canada", taxable=True, year=2025,
                        tax_date=basis, phantom_hint=False))))
                gpaths.append(g)
            return T.build_report([p], gpaths, 2025, {}, "CAD",
                                  today="2026-10-29", cash=cash_doc,
                                  tax_date=basis)


def _usd_cash(units, cost, *more_lines, bal=None):
    lines = [_tt("CASHOPEN", "2025-01-01", "USD", units=units, cost=cost)]
    lines += list(more_lines)
    return {"stablecoins_as_cash": True, "lines": lines,
            "events": [_bal("2025-12-31", "USD", bal)] if bal is not None
            else []}


# ============================================== TJ-0627 the cash on the
# walk's own date basis
class TestTJ0627CashOnTheWalksDates(unittest.TestCase):
    """TJ-0627: with tax_date = "trade" the walk dated the shares on
    their trade date while the FX-on-cash ledger v2 gave the cash on its
    settlement date: a purchase's cash and its shares both counted
    between the two days (1,300 of cash and 675 of shares, 1,975, where
    the account held 1,325), and a sale's proceeds were in no row until
    they settled."""

    # 1,000 US dollars at a cost of 1,300; 500 buy 5 shares (traded Feb
    # 12, settled Feb 13); 3 shares sold for 300 (Jun 10, settled Jun 11).
    NATIVE = [_native("2025-02-12", "2025-02-13", 5, -500.0),
              _native("2025-06-10", "2025-06-11", -3, 300.0)]
    BASE = [_base("2025-02-12", "2025-02-13", 5, -675.0),
            _base("2025-06-10", "2025-06-11", -3, 405.0)]

    def _cash(self):
        return _usd_cash(1000.0, 1300.0, bal=800.0)

    @rule("CA-RPT-20")
    def test_the_ledgers_holdings_on_each_basis(self):
        settle = _ledger(self.NATIVE, self._cash(), "settle")
        self.assertEqual(settle["status"], "computed", settle["problems"])
        self.assertEqual(settle["holdings"]["basis"], "settle")
        self.assertEqual(settle["holdings"]["foreign"][0]["points"], [
            ["2024-12-31", 1000.0, 1300.0], ["2025-02-13", 500.0, 650.0],
            ["2025-06-11", 800.0, 1055.0]])
        trade = _ledger(self.NATIVE, self._cash(), "trade")
        self.assertEqual(trade["status"], "computed", trade["problems"])
        self.assertEqual(trade["holdings"]["basis"], "trade")
        # The purchase's 500 leave on its trade date at the average cost
        # (1.30); the sale's 300 come in on its trade date at their own
        # cost (1.35 a unit, the cost they settle at).
        self.assertEqual(trade["holdings"]["foreign"][0]["points"], [
            ["2024-12-31", 1000.0, 1300.0], ["2025-02-12", 500.0, 650.0],
            ["2025-06-10", 800.0, 1055.0]])
        # The ledger's own figures do not move.
        for k in ("net_gain", "events", "reconciliation", "pools_year_end",
                  "close"):
            self.assertEqual(trade[k], settle[k], k)
        with self.assertRaises(ValueError):
            _ledger(self.NATIVE, self._cash(), "record")

    @rule("CA-RPT-20")
    def test_the_base_currency_on_trade_dates(self):
        b = "abroad/tt"
        ops = [{"td": "2025-02-12", "sd": "2025-02-13", "book": b,
                "amount": -500.0},
               {"td": "2025-06-10", "sd": "2025-06-11", "book": b,
                "amount": 300.0},
               # Traded Dec 30 2024, settled in January: on trade dates
               # it has left by the year's start.
               {"td": "2024-12-30", "sd": "2025-01-02", "book": b,
                "amount": -100.0}]
        checks = [_bal("2025-12-31", "CAD", 700.0)]
        opening = {b: {"units": 1000.0}}
        (s,) = FX._base_holdings(ops, checks, opening, set(), 2025)
        self.assertTrue(s["ok"], s)
        self.assertEqual(s["points"], [
            ["2024-12-31", 1000.0], ["2025-01-02", 900.0],
            ["2025-02-13", 400.0], ["2025-06-11", 700.0]])
        (t,) = FX._base_holdings(ops, checks, opening, set(), 2025,
                                 basis="trade")
        self.assertEqual((t["ok"], t["why"]), (s["ok"], s["why"]))
        self.assertEqual(t["points"], [
            ["2024-12-31", 900.0], ["2025-02-12", 400.0],
            ["2025-06-10", 700.0]])

    @rule("CA-RPT-20")
    def test_a_purchase_is_never_counted_twice(self):
        for basis, day in (("settle", "2025-02-13"),
                           ("trade", "2025-02-12")):
            with self.subTest(basis=basis):
                doc = TC.cash_doc(_ledger(self.NATIVE, self._cash(), basis),
                                  {"abroad": "USA"}, 2025, "CAD",
                                  basis=basis)
                self.assertEqual(doc["basis"], basis)
                rep = _report(self.BASE, doc, basis)
                # 650 of cash and 675 of shares, never 1,300 + 675.
                self.assertEqual((rep["max_total_cost"],
                                  rep["max_total_date"]), (1325.0, day))
                p = {r["symbol"]: r for r in rep["properties"]}
                self.assertEqual((p["USD cash"]["max_cost"],
                                  p["USD cash"]["year_end_cost"]),
                                 (1300.0, 1055.0))

    @rule("CA-RPT-20")
    def test_a_sales_proceeds_are_counted_from_its_trade_date(self):
        # 5 shares bought in 2024 (675), sold Jun 10 for 1,500 US dollars
        # (2,025), settled Jun 11; the dollars leave the account on
        # `out`.
        native = [_native("2025-06-10", "2025-06-11", -5, 1500.0)]
        base = [_base("2024-03-01", "2024-03-04", 5, -675.0),
                _base("2025-06-10", "2025-06-11", -5, 2025.0)]

        def run(basis, out):
            cash = _usd_cash(0.0, 0.0, _tt(
                "CASHMOVE", out, "USD", n=2, amount=-1500.0,
                decl={"how": "kept", "value": None}), bal=0.0)
            led = _ledger(native, cash, basis)
            self.assertEqual(led["status"], "computed", led["problems"])
            doc = TC.cash_doc(led, {"abroad": "USA"}, 2025, "CAD",
                              basis=basis)
            rep = _report(base, doc, basis)
            return rep["max_total_cost"], rep["max_total_date"]
        # The dollars held a day after the sale settled: both bases see
        # them, each on its own day.
        self.assertEqual(run("settle", "2025-06-12"), (2025.0, "2025-06-11"))
        self.assertEqual(run("trade", "2025-06-12"), (2025.0, "2025-06-10"))
        # Gone the day the sale settles: on trade dates they were held
        # from the trade date (on settlement dates they came and went in
        # one day: docs/limits.md).
        self.assertEqual(run("trade", "2025-06-11"), (2025.0, "2025-06-10"))

    @rule("CA-RPT-20")
    def test_a_lag_across_the_year_start_and_end(self):
        # Bought Dec 30 2024 (settled Jan 2): on trade dates its cash had
        # left by the year's start. 200 US dollars converted from 270
        # Canadian dollars on Dec 31 2025 settle in 2026: held at the
        # year's end on trade dates, at the cost paid.
        native = [_native("2024-12-30", "2025-01-02", 1, -100.0)]
        cash = _usd_cash(1000.0, 1300.0, bal=1100.0)
        cash["events"].append({
            "kind": "FXCONV", "origin": "broker", "book": BOOK,
            "label": "abroad", "date": "2025-12-31", "settle": "2026-01-02",
            "from_ccy": "CAD", "from_amt": 270.0, "to_ccy": "USD",
            "to_amt": 200.0, "value": None, "where": "x.csv:7"})
        trade = _ledger(native, cash, "trade")
        self.assertEqual(trade["status"], "computed", trade["problems"])
        self.assertEqual(trade["holdings"]["foreign"][0]["points"], [
            ["2024-12-31", 900.0, 1170.0], ["2025-12-31", 1100.0, 1440.0]])
        settle = _ledger(native, cash, "settle")
        self.assertEqual(settle["holdings"]["foreign"][0]["points"], [
            ["2024-12-31", 1000.0, 1300.0], ["2025-01-02", 900.0, 1170.0]])

    @rule("CA-RPT-20")
    def test_a_debt_is_repaid_first_on_trade_dates(self):
        # 100 US dollars owed at the start (borrowed at 1.30); a sale of
        # 300 traded Mar 3, settled Mar 4: 200 held from Mar 3, at the
        # proceeds' own cost.
        native = [_native("2025-03-03", "2025-03-04", -3, 300.0)]
        cash = _usd_cash(-100.0, 130.0, bal=200.0)
        trade = _ledger(native, cash, "trade")
        self.assertEqual(trade["status"], "computed", trade["problems"])
        self.assertEqual(trade["holdings"]["foreign"][0]["points"],
                         [["2025-03-03", 200.0, 270.0]])
        settle = _ledger(native, cash, "settle")
        self.assertEqual(settle["holdings"]["foreign"][0]["points"],
                         [["2025-03-04", 200.0, 270.0]])

    @rule("CA-RPT-20")
    def test_a_cash_document_on_another_basis_is_refused(self):
        led = _ledger(self.NATIVE, self._cash(), "trade")
        with self.assertRaises(ValueError):
            TC.cash_doc(led, {"abroad": "USA"}, 2025, "CAD", basis="settle")
        doc = TC.cash_doc(led, {"abroad": "USA"}, 2025, "CAD",
                          basis="trade")
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "abroad_base.json"
            b.write_text(json.dumps({
                "metadata": {"target_currency": "CAD"},
                "transactions": [r.to_dict() for r in self.BASE]}))
            c = Path(td) / "cash.json"
            c.write_text(json.dumps(doc))
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                rc = T.main(["--year", "2025", "--option-premium-timing",
                             "close", "--cash", str(c), str(b)])
            self.assertEqual(rc, 2)
            self.assertIn("the T1135 cash document is dated on the trade "
                          "basis, --tax-date is settle",
                          " ".join(err.getvalue().split()))
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(T.main([
                    "--year", "2025", "--option-premium-timing", "close",
                    "--tax-date", "trade", "--cash", str(c), str(b)]), 0)


# A Questrade account (synthetic) whose custodian is in the United
# States: 1,000 US dollars at a cost of 1,300, 5 shares bought on Feb 12
# (settled Feb 13), 3 sold on Jun 10 (settled Jun 11).
_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type\n")
_QT_ROWS = (
    "2025-02-12,2025-02-13,Buy,ZZQX,ZZQX INC,5,100.00,-500.00,0.00,"
    "-500.00,USD,Trades,99900090,Margin\n"
    "2025-06-10,2025-06-11,Sell,ZZQX,ZZQX INC,-3,100.00,300.00,0.00,"
    "300.00,USD,Trades,99900090,Margin\n")


def _toml(trade):
    return ("[settings]\nlocal_timezone = \"America/Toronto\"\n"
            "year = 2025\ncountry = \"canada\"\nbase_currency = \"CAD\"\n"
            "source_currencies = [\"USD\"]\n"
            "option_grant_timing_since = 2025\n"
            + ("tax_date = \"trade\"\n" if trade else "")
            + "\n[accounts.abroad]\ntype = \"taxable\"\n"
              "custodian_country = \"USA\"\n")


def _rates(path):
    d, lines = _date(2025, 1, 1), []
    while d <= _date.today():
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += _td(days=1)
    path.write_text("\n".join(lines) + "\n")


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(td, name, trade, rows=_QT_ROWS, cash_tt=None):
    root = Path(td) / name
    (root / "inputs" / "abroad").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_toml(trade))
    (root / "inputs" / "abroad" / "qt.csv").write_text(_QT_HEAD + rows)
    (root / "inputs" / "abroad" / "cash.tt").write_text(
        cash_tt or "CASHOPEN 2025-01-01 USD 1000 1300\n"
                   "CASHBAL 2025-12-31 USD 800\n")
    (root / "work").mkdir()
    _rates(root / "work" / "to_base.csv")
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr + r.stdout
    return root


class TestTJ0627TradeDateProject(unittest.TestCase):
    """`tjs t1135` in a project whose tax_date is trade: the cash is
    given on trade dates too."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.trade = _project(cls._tmp.name, "trade", True)
        cls.settle = _project(cls._tmp.name, "settle", False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule("CA-RPT-20")
    def test_the_total_on_each_basis(self):
        for root, basis, day in ((self.trade, "trade", "2025-02-12"),
                                 (self.settle, "settle", "2025-02-13")):
            with self.subTest(basis=basis):
                r = _cli(root, "t1135", "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                j = json.loads(r.stdout)
                self.assertEqual(j["tax_date_basis"], basis)
                # 650 of cash and 675 of shares; never 1,300 + 675.
                self.assertEqual((j["max_total_cost"],
                                  j["max_total_date"]), (1325.0, day))
                p = {(x["symbol"], x.get("cash_account")): x
                     for x in j["properties"]}
                self.assertEqual(p[("USD cash", "abroad")]["max_cost"],
                                 1300.0)

    @rule("CA-RPT-20")
    def test_the_report_says_its_dates(self):
        flat = " ".join(_cli(self.trade, "t1135", "--details").stdout.split())
        self.assertIn("Amounts are COST (ACB-style, trade-dated: tax_date = "
                      "\"trade\")", flat)
        flat = " ".join(_cli(self.settle, "t1135",
                             "--details").stdout.split())
        self.assertIn("Amounts are COST (ACB-style, settlement-dated)", flat)


# ============================================== TJ-0628 the interest the
# cash earns
def _interest(d, cur, amt, rid, sym="CASH", acct="abroad"):
    """A native INTEREST row (`amt` in `cur`), id `rid`."""
    return {"action": "INTEREST", "type": "interest", "date": d,
            "date_settle": d, "time": "16:00:00", "symbol": sym,
            "quantity": 0.0, "currency": cur, "net_amount": amt,
            "description": "interest", "account": acct, "_book": BOOK,
            "id": rid}


def _base_interest(d, amt, rid, sym="CASH", acct="abroad"):
    """The same row in the base book: its amount in CAD, no currency of
    its own."""
    return TaxTransaction(action="INTEREST", date=d, date_settle=d,
                          time="16:00:00", symbol=sym, quantity=0.0,
                          net_amount=amt, currency="CAD", account=acct,
                          type="interest", id=rid)


class TestTJ0628InterestOnTheCash(unittest.TestCase):
    """TJ-0628: an account's cash held outside Canada is a CAT 1 row,
    yet the interest the account pays on that cash (an INTEREST row
    naming no symbol) was in no row's INCOME: the USD cash row of an
    account that earned 4 US dollars of interest read 0."""

    @rule("CA-RPT-14")
    def test_the_ledger_names_the_currency_each_interest_was_paid_in(self):
        native = [_interest("2025-06-30", "USD", 4.0, "i-usd"),
                  _interest("2025-07-31", "CAD", 2.0, "i-cad"),
                  _interest("2025-08-29", "USDC", 1.0, "i-usdc"),
                  _interest("2025-03-31", "USD", 9.0, "i-bond",
                            sym="ZZB.US"),
                  _interest("2024-12-31", "USD", 7.0, "i-2024")]
        with contextlib.redirect_stderr(io.StringIO()):     # its USDC note
            doc = _ledger(native, _usd_cash(10.0, 13.0, bal=24.0), "settle")
        self.assertEqual(doc["holdings"]["interest"], [
            {"id": "i-usd", "date": "2025-06-30", "account": "abroad",
             "currency": "USD"},
            {"id": "i-cad", "date": "2025-07-31", "account": "abroad",
             "currency": "CAD"},
            # A USD stablecoin is US-dollar cash here (CA-CRYPTO-02).
            {"id": "i-usdc", "date": "2025-08-29", "account": "abroad",
             "currency": "USD"}])
        cash = TC.cash_doc(doc, {"abroad": "USA"}, 2025, "CAD")
        self.assertEqual(cash["interest"], {"i-usd": "USD", "i-cad": "CAD",
                                            "i-usdc": "USD"})
        # An account whose cash is not counted keeps none.
        self.assertEqual(TC.cash_doc(doc, {"other": "USA"}, 2025,
                                     "CAD")["interest"], {})

    @rule("CA-RPT-14")
    def test_the_interest_is_its_cash_rows_income(self):
        rows = [_base("2025-02-12", "2025-02-12", 5, -675.0),
                _base_interest("2025-06-30", 5.4, "i-usd"),
                _base_interest("2025-07-31", 2.0, "i-cad"),
                # Interest charged on a debit balance: a debt's cost.
                _base_interest("2025-08-29", -1.35, "i-debit"),
                # Paid in euros: the account's euros are no row.
                _base_interest("2025-09-30", 1.5, "i-eur"),
                # Not the year's.
                _base_interest("2024-12-31", 9.0, "i-2024")]
        doc = {"version": TC.VERSION, "year": 2025, "currency": "CAD",
               "basis": "settle", "ledger_status": "computed",
               "accounts": {"abroad": "USA"},
               "series": [
                   {"account": "abroad", "currency": "USD", "country": "USA",
                    "points": [["2024-12-31", 1000.0, 1300.0],
                               ["2025-02-12", 500.0, 650.0]],
                    "unknown": False, "why": ""},
                   {"account": "abroad", "currency": "CAD", "country": "USA",
                    "points": [["2024-12-31", 600.0, 600.0]],
                    "unknown": False, "why": ""}],
               "not_counted": [], "stablecoins": [],
               "interest": {"i-usd": "USD", "i-cad": "CAD",
                            "i-debit": "USD", "i-eur": "EUR",
                            "i-2024": "USD"}}
        rep = _report(rows, doc, "settle", gains=True)
        p = {r["symbol"]: r for r in rep["properties"]}
        self.assertEqual((p["USD cash"]["income"], p["CAD cash"]["income"],
                          p["ZZQX.US"]["income"]), (5.4, 2.0, 0.0))
        self.assertNotIn("EUR cash", p)
        self.assertEqual(rep["by_country"]["USA"]["income"], 7.4)
        # Without the cash document the interest is no row's (an
        # account held in Canada).
        rep = _report(rows, None, "settle", gains=True)
        self.assertEqual([r["symbol"] for r in rep["properties"]],
                         ["ZZQX.US"])
        self.assertEqual(rep["properties"][0]["income"], 0.0)

    @rule("CA-RPT-14")
    def test_the_report_says_so(self):
        rep = _report([_base("2025-02-12", "2025-02-12", 5, -675.0)],
                      None, "settle")
        full = " ".join(T.render_report(rep).split())
        self.assertIn("Interest credited on an account's cash is the income "
                      "of its cash row where the cash is one (in the "
                      "currency it was paid in), else no property's; "
                      "interest charged is no row's.", full)


class TestTJ0628InterestInAProject(unittest.TestCase):
    """`tjs t1135`: a Questrade account (custodian abroad) credited 4 US
    dollars of interest (5.40 at 1.35): the USD cash row's income."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _project(
            cls._tmp.name, "interest", False,
            rows=_QT_ROWS + "2025-06-30,2025-06-30,INT,,INTEREST,0,0,0,0,"
                            "4.00,USD,Interest,99900090,Margin\n",
            cash_tt="CASHOPEN 2025-01-01 USD 1000 1300\n"
                    "CASHBAL 2025-12-31 USD 804\n")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule("CA-RPT-14")
    def test_the_usd_cash_rows_income(self):
        r = _cli(self.root, "t1135", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads(r.stdout)
        p = {(x["symbol"], x.get("cash_account")): x
             for x in j["properties"]}
        usd = p[("USD cash", "abroad")]
        self.assertEqual((usd["income"], usd["year_end_cost"]),
                         (5.4, 1060.4))
        self.assertEqual(p[("ZZQX.US", None)]["income"], 0.0)
        self.assertEqual(j["by_country"]["USA"]["income"], 5.4)


if __name__ == "__main__":
    unittest.main()
