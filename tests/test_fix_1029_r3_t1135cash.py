"""Fixes of 2026-10-29, round 3, group t1135cash: the FX-on-cash
ledger v2's conversion commission in a foreign currency (backlog
TJ-0621), an opening snapshot read as a purchase (TJ-0622), and the
cash an account holds outside Canada in the T1135 test (TJ-0454: a
crypto account's US dollars and stablecoins; TJ-0609: the cash of an
account whose custodian is abroad). One test class per backlog item,
each named for it.

Every fixture is SYNTHETIC: invented tickers (ZZ*), invented ledger ids,
invented account names, small amounts.
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
from types import SimpleNamespace
from unittest import mock

from tax_rules import rule
from taxjson.bin import taxjson_t1135 as T
from taxjson.lib import cash_events as CE
from taxjson.lib import checklist as CL
from taxjson.lib import fx_cash_v2 as FX
from taxjson.lib import t1135_cash as TC
from taxjson.lib.core import TaxTransaction
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent

KR_HEAD = ('"txid","refid","time","type","subtype","aclass","asset",'
           '"wallet","amount","fee","balance"\n')


def _kr(*rows):
    """A Kraken ledger export of `rows` (txid, refid, time, type, asset,
    amount, fee, balance)."""
    return KR_HEAD + "".join(
        f'"{t}","{r}","{tm}","{ty}","","currency","{a}","spot / main",'
        f'"{amt}","{fee}","{bal}"\n'
        for t, r, tm, ty, a, amt, fee, bal in rows)


def _kraken_events(text, book="crypto/kraken", label="crypto"):
    """The cash events kraken_cash_events reads from a ledger export, in
    the account `label`'s book, crypto dated in Toronto."""
    from taxjson.lib.brokerages.kraken import kraken_cash_events
    old = os.environ.get("TAXJSON_LOCAL_TZ")
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers.csv"
            p.write_text(text)
            evs = kraken_cash_events(p)
    finally:
        if old is None:
            os.environ.pop("TAXJSON_LOCAL_TZ", None)
        else:
            os.environ["TAXJSON_LOCAL_TZ"] = old
    for e in evs:
        e["book"] = book
        e["label"] = label
    return evs


def _tt(kind, date, cur, *, book="crypto/kraken", label="crypto", n=1,
        **kw):
    """A .tt cash line as lib/cash_events reads it."""
    return dict({"kind": kind, "origin": "tt", "book": book, "label": label,
                 "date": date, "settle": date, "currency": cur,
                 "where": f"cash.tt:{n}"}, **kw)


# ============================================== TJ-0621 a fee in the
# currency sold or bought
class TestTJ0621ConversionFeeInAForeignCurrency(unittest.TestCase):
    """TJ-0621: Kraken took a 10 USD commission on 990 USD sold for
    CAD; ledger v2 walked the 10 USD as a spend of its own at the day's
    rate (a gain of 0.50) and the 990 as the conversion (49.50): 50.00
    in all. The 1,000 USD that left were disposed of for the Canadian
    dollars received, the commission included (CA-FX-07): 36.50."""

    @rule("CA-FX-07")
    def test_a_fee_in_the_currency_sold_is_part_of_the_disposition(self):
        evs = _kraken_events(_kr(
            ("LZ01", "FZ01", "2024-01-02 15:00:00", "deposit", "ZUSD",
             "1000.0000", "0.0000", "1000.0000"),
            ("LZ02", "TZ01", "2024-01-03 15:00:00", "trade", "ZUSD",
             "-990.0000", "10.0000", "0.0000"),
            ("LZ03", "TZ01", "2024-01-03 15:00:00", "trade", "ZCAD",
             "1336.5000", "0.0000", "1336.5000")))
        lines = [_tt("CASHOPEN", "2024-01-01", "USD", units=0.0, cost=0.0),
                 _tt("CASHMOVE", "2024-01-02", "USD", n=2, amount=1000.0,
                     decl={"how": "cost", "value": 1300.0})]
        CE.apply_declarations(evs, lines)
        doc = FX.build([], {"events": evs, "lines": lines,
                            "stablecoins_as_cash": True}, "CAD", 2024,
                       lambda c, d: 1.35, country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 36.5)
        self.assertEqual([(e["units"], e["gain"]) for e in doc["events"]],
                         [(1000.0, 36.5)])
        self.assertEqual(doc["per_currency"]["USD"]["disposed"], 1000.0)

    @rule("CA-FX-07")
    def test_a_fee_in_the_currency_bought_comes_off_its_units(self):
        # 1,350 CAD bought 1,000 USD less a 2 USD commission: 998 USD
        # at a cost of 1,350, sold for 7.28 CAD more: a gain of 7.28.
        evs = _kraken_events(_kr(
            ("LZ01", "FZ01", "2024-01-02 15:00:00", "deposit", "ZCAD",
             "1350.0000", "0.0000", "1350.0000"),
            ("LZ02", "TZ01", "2024-01-03 15:00:00", "trade", "ZCAD",
             "-1350.0000", "0.0000", "0.0000"),
            ("LZ03", "TZ01", "2024-01-03 15:00:00", "trade", "ZUSD",
             "1000.0000", "2.0000", "998.0000"),
            ("LZ04", "TZ02", "2024-03-05 15:00:00", "trade", "ZUSD",
             "-998.0000", "0.0000", "0.0000"),
            ("LZ05", "TZ02", "2024-03-05 15:00:00", "trade", "ZCAD",
             "1357.2800", "0.0000", "1357.2800")))
        doc = FX.build([], {"events": evs, "lines": [],
                            "stablecoins_as_cash": True}, "CAD", 2024,
                       lambda c, d: 1.35, country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 7.28)
        self.assertEqual(doc["per_currency"]["USD"]["acquired"], 998.0)
        self.assertTrue(all(r["ok"] for r in doc["reconciliation"]),
                        doc["reconciliation"])

    @rule("CA-FX-07")
    def test_a_fee_charged_on_no_conversion_is_still_a_spend(self):
        # A withdrawal's fee in US dollars is a spend of its own.
        evs = _kraken_events(_kr(
            ("LZ01", "FZ01", "2024-01-02 15:00:00", "deposit", "ZUSD",
             "100.0000", "0.0000", "100.0000"),
            ("LZ02", "FZ02", "2024-02-02 15:00:00", "withdrawal", "ZUSD",
             "-90.0000", "10.0000", "0.0000")))
        lines = [_tt("CASHOPEN", "2024-01-01", "USD", units=0.0, cost=0.0),
                 _tt("CASHMOVE", "2024-01-02", "USD", n=2, amount=100.0,
                     decl={"how": "cost", "value": 130.0}),
                 _tt("CASHMOVE", "2024-02-02", "USD", n=3, amount=-90.0,
                     decl={"how": "kept", "value": None})]
        CE.apply_declarations(evs, lines)
        doc = FX.build([], {"events": evs, "lines": lines,
                            "stablecoins_as_cash": True}, "CAD", 2024,
                       lambda c, d: 1.35, country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        # The 10 USD fee: spent at 1.35 (13.50) from a cost of 13.00.
        self.assertEqual(doc["net_gain"], 0.5)


# ============================================== TJ-0622 an opening
class TestTJ0622OpeningSnapshotMovesNoCash(unittest.TestCase):
    """TJ-0622: an opening balance (a .tt OPENING line, `tjs
    opening`) is a BUYSELL of type `opening` that records a position
    already held on the snapshot day; the FX-on-cash ledgers read it as
    a purchase for cash: 500 US dollars left the account that day."""

    ROW = {"action": "BUYSELL", "type": "opening", "date": "2025-06-02",
           "date_settle": "2025-06-02", "time": "00:00:00",
           "symbol": "ZZQ.US", "quantity": 5, "currency": "USD",
           "net_amount": -500.0, "price": 100.0,
           "description": "opening balance", "account": "margin",
           "_book": "margin/ib"}

    @rule("CA-FX-07")
    def test_the_cash_legs_of_an_opening_are_none(self):
        from taxjson.bin import taxjson_fx_cash as F
        from taxjson.lib.core import OPENING_TYPE
        self.assertEqual(self.ROW["type"], OPENING_TYPE)
        self.assertEqual(F._cash_legs(dict(self.ROW)), [])
        # A purchase of the same shares does move the cash.
        self.assertEqual(F._cash_legs(dict(self.ROW, type="")),
                         [("USD", -500.0)])

    @rule("CA-FX-07")
    def test_ledger_v2_reconciles_an_account_with_an_opening(self):
        book = "margin/ib"
        cash = {"stablecoins_as_cash": True,
                "lines": [_tt("CASHOPEN", "2025-01-01", "USD", book=book,
                              label="margin", units=800.0, cost=1000.0)],
                "events": [{"kind": "CASHBAL", "book": book,
                            "label": "margin", "date": "2025-12-31",
                            "currency": "USD", "balance": 800.0,
                            "where": "cash.tt:2"}]}
        doc = FX.build([dict(self.ROW)], cash, "CAD", 2025,
                       lambda c, d: 1.35, country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual((doc["net_gain"], doc["events"]), (0.0, []))
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 800.0, "acb": 1000.0}})


# ============================================== TJ-0454 / TJ-0609 the
# cash an account holds outside Canada
def _sale(d, q, net, sym="ZZCOIN", cur="USD", book="crypto/kraken",
          acct="crypto"):
    """A native row: `q` units of a coin sold (q < 0) or bought for
    `net` of `cur` (a purchase's net negative)."""
    return {"action": "BUYSELL", "type": "", "date": d, "date_settle": d,
            "time": "15:00:00", "symbol": sym, "quantity": q,
            "currency": cur, "net_amount": net, "price": abs(net / q),
            "description": "trade", "account": acct, "_book": book}


def _bal(d, cur, balance, book="crypto/kraken", label="crypto", n=9):
    return {"kind": "CASHBAL", "book": book, "label": label, "date": d,
            "currency": cur, "balance": balance, "where": f"cash.tt:{n}"}


class TestTJ0454LedgerHoldings(unittest.TestCase):
    """The FX-on-cash ledger v2 gives each book's cash day by day
    (`holdings`): a foreign currency's units and their cost at the
    pool's average cost, the base currency's balance where the books
    give it (tax-logic CA-RPT-20)."""

    @rule("CA-RPT-20")
    def test_a_sale_for_us_dollars_is_cash_held_at_its_cost(self):
        cash = {"stablecoins_as_cash": True, "events": [
                    _bal("2024-12-31", "USD", 620.0)],
                "lines": [_tt("CASHOPEN", "2024-01-01", "USD", units=0.0,
                              cost=0.0)]}
        doc = FX.build([_sale("2024-03-01", -2, 620.0)], cash, "CAD", 2024,
                       lambda c, d: 1.35, country="canada", holdings=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        (s,) = doc["holdings"]["foreign"]
        self.assertEqual((s["book"], s["currency"]), ("crypto/kraken", "USD"))
        self.assertEqual(s["points"][-1], ["2024-03-01", 620.0, 837.0])
        self.assertEqual(doc["holdings"]["base"], [])
        # Without `holdings` the doc is as it was.
        self.assertNotIn("holdings", FX.build(
            [_sale("2024-03-01", -2, 620.0)], cash, "CAD", 2024,
            lambda c, d: 1.35, country="canada"))

    @rule("CA-RPT-20")
    def test_an_opening_balance_is_held_from_the_start_of_the_year(self):
        cash = {"stablecoins_as_cash": True, "events": [
                    _bal("2024-12-31", "USD", 80.0)],
                "lines": [_tt("CASHOPEN", "2024-01-01", "USD", units=80.0,
                              cost=100.0)]}
        doc = FX.build([], cash, "CAD", 2024, lambda c, d: 1.35,
                       country="canada", holdings=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["holdings"]["foreign"], [
            {"book": "crypto/kraken", "currency": "USD",
             "points": [["2023-12-31", 80.0, 100.0]]}])

    @rule("CA-RPT-20")
    def test_the_base_currency_is_given_where_the_books_reconcile(self):
        b = "abroad/tt"
        ops = [{"sd": "2024-02-01", "td": "2024-02-01", "book": b,
                "amount": 500.0},
               {"sd": "2024-05-01", "td": "2024-05-01", "book": b,
                "amount": -100.0}]
        opening = {b: {"units": 200.0}}
        ok = FX._base_holdings(ops, [_bal("2024-12-31", "CAD", 600.0,
                                          book=b)], opening, set(), 2024)
        self.assertEqual(ok, [{"book": b, "ok": True, "why": "", "points": [
            ["2023-12-31", 200.0], ["2024-02-01", 700.0],
            ["2024-05-01", 600.0]]}])
        # No opening line: a statement balance before the year is one.
        st = FX._base_holdings(ops, [_bal("2023-12-29", "CAD", 200.0, book=b),
                                     _bal("2024-12-31", "CAD", 600.0,
                                          book=b)], {}, set(), 2024)
        self.assertTrue(st[0]["ok"], st)
        self.assertEqual(st[0]["points"][0], ["2023-12-31", 200.0])

    @rule("CA-RPT-20")
    def test_the_base_currency_is_not_given_without_the_balances(self):
        b = "abroad/tt"
        ops = [{"sd": "2024-02-01", "td": "2024-02-01", "book": b,
                "amount": 500.0}]
        none = FX._base_holdings(ops, [_bal("2024-12-31", "CAD", 500.0,
                                            book=b)], {}, set(), 2024)
        self.assertFalse(none[0]["ok"])
        self.assertIn("no balance at the start of 2024", none[0]["why"])
        late = FX._base_holdings(ops, [], {b: {"units": 0.0}}, set(), 2024)
        self.assertIn("no statement balance on or after its last 2024 "
                      "flow (2024-02-01)", late[0]["why"])
        off = FX._base_holdings(ops, [_bal("2024-12-31", "CAD", 400.0,
                                           book=b)],
                                {b: {"units": 0.0}}, set(), 2024)
        self.assertIn("the statement balance at the end of 2024-12-31 is "
                      "400.00, the books' 500.00", off[0]["why"])
        lost = FX._base_holdings(ops, [], {b: {"units": 0.0}}, {"abroad"},
                                 2024)
        self.assertFalse(lost[0]["ok"])


class TestTJ0454CashDocument(unittest.TestCase):
    """lib/t1135_cash: which accounts' cash is read, and the rows the
    ledger's holdings give."""

    @rule("CA-RPT-20")
    def test_the_accounts_whose_cash_is_held_outside_canada(self):
        self.assertEqual(TC.counted_accounts({
            "crypto": {"type": "taxable", "crypto": True},
            "cbase": {"type": "taxable", "crypto": True,
                      "custodian_country": "CAN"},
            "kr": {"type": "taxable", "crypto": True,
                   "custodian_country": "USA"},
            "abroad": {"type": "taxable", "custodian_country": "GBR"},
            "margin": {"type": "taxable"},
            "tfsa": {"type": "tfsa", "crypto": True}}),
            {"abroad": "GBR", "crypto": "CRYPTO", "kr": "USA"})

    @rule("CA-RPT-20")
    def test_the_rows_of_a_computed_ledger(self):
        led = {"status": "computed", "problems": [], "holdings": {
            "foreign": [
                {"book": "crypto/kraken", "currency": "USD",
                 "points": [["2024-03-01", 620.0, 837.0]]},
                {"book": "crypto/coinbase", "currency": "USD",
                 "points": [["2024-02-01", 10.0, 13.0],
                            ["2024-04-01", 0.0, 0.0]]},
                {"book": "margin/ib", "currency": "USD",
                 "points": [["2024-03-01", 99.0, 130.0]]}],
            "base": [{"book": "crypto/kraken", "ok": True, "why": "",
                      "points": [["2023-12-31", 50.0],
                                 ["2024-05-01", -5.0]]}]}}
        doc = TC.cash_doc(led, {"crypto": "CRYPTO"}, 2024, "CAD",
                          stablecoins=["usdc"])
        self.assertIsNone(TC.check(doc))
        self.assertEqual(doc["stablecoins"], ["USDC"])
        self.assertEqual(doc["not_counted"], [])
        rows = {s["currency"]: s for s in doc["series"]}
        self.assertEqual(rows["USD"]["points"], [
            ["2024-02-01", 10.0, 13.0], ["2024-03-01", 630.0, 850.0],
            ["2024-04-01", 620.0, 837.0]])
        # A negative balance is a debt: no cost amount.
        self.assertEqual(rows["CAD"]["points"], [
            ["2023-12-31", 50.0, 50.0], ["2024-05-01", 0.0, 0.0]])
        self.assertFalse(any(s["unknown"] for s in doc["series"]))

    @rule("CA-RPT-20")
    def test_a_refusing_ledger_leaves_the_cost_unknown(self):
        led = {"status": "not_computed", "problems": [
            {"kind": "balance", "book": "crypto/kraken", "currency": "USD",
             "date": "2024-12-31", "amount": 3.0, "text": "a gap"},
            {"kind": "balance", "book": "crypto/kraken", "currency": "EUR",
             "date": "2024-12-31", "amount": 3.0, "text": "a euro gap"}],
            "holdings": {"foreign": [
                {"book": "crypto/kraken", "currency": "USD",
                 "points": [["2024-03-01", 620.0, 837.0]]}],
                "base": [{"book": "crypto/kraken", "ok": False,
                          "why": "no balance", "points": []}]}}
        doc = TC.cash_doc(led, {"crypto": "CRYPTO"}, 2024, "CAD")
        rows = {s["currency"]: s for s in doc["series"]}
        self.assertEqual((rows["USD"]["unknown"], rows["USD"]["why"]),
                         (True, "a gap"))
        self.assertEqual((rows["EUR"]["unknown"], rows["EUR"]["points"]),
                         (True, []))
        self.assertEqual(doc["not_counted"], [
            {"account": "crypto", "currency": "CAD", "unknown": False,
             "why": "crypto/kraken: no balance"}])
        gone = TC.cash_doc(None, {"crypto": "CRYPTO"}, 2024, "CAD",
                           refusal="fx-cash v2: no rates")
        self.assertEqual((gone["ledger_status"], gone["series"]),
                         ("refused", []))
        self.assertEqual(gone["not_counted"], [
            {"account": "crypto", "currency": "", "unknown": True,
             "why": "fx-cash v2: no rates"}])
        self.assertEqual(TC.cash_doc(led, {}, 2024, "CAD")["series"], [])


def _row(a, d, s, q, n=0.0, acct="crypto"):
    """A base-book row (a buy's net negative, a sale's positive)."""
    return TaxTransaction(action=a, date=d, date_settle=d, time="10:00:00",
                          symbol=s, quantity=q, net_amount=n, currency="CAD",
                          account=acct, id=f"{a}-{d}-{s}-{q}-{acct}")


def _report(rows, cash, year=2025, **kw):
    """build_report over the rows (one base file per account) with the
    cash document `cash`; no gains files (income and gain n/a)."""
    with tempfile.TemporaryDirectory() as td:
        by_acct = {}
        for r in rows:
            d = r.to_dict()
            by_acct.setdefault(d["account"], []).append(d)
        bases = []
        for acct, rs in by_acct.items():
            p = Path(td) / f"{acct}_base.json"
            p.write_text(json.dumps({"transactions": rs}))
            bases.append(p)
        with contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(T, "_DETAILS", True):
            return T.build_report(bases, [], year, {}, "CAD",
                                  today="2026-10-29", cash=cash, **kw)


def _cash(*series, not_counted=()):
    return {"version": TC.VERSION, "year": 2025, "currency": "CAD",
            "ledger_status": "computed", "accounts": {"crypto": "CRYPTO"},
            "series": [dict({"account": "crypto", "currency": "USD",
                             "country": "CRYPTO", "unknown": False,
                             "why": ""}, **s) for s in series],
            "not_counted": list(not_counted), "stablecoins": ["USDC"]}


def _acts(text):
    return [ln for ln in text.splitlines() if ln.startswith("! ")]


class TestTJ0454CashInTheTest(unittest.TestCase):
    """TJ-0454: coins sold on an exchange for USDC (US-dollar cash in a
    Canadian project, CA-CRYPTO-02) left the T1135 test: only the coins'
    cost was walked, so a year that held 880 of cash after the sale
    read as 500 (the coins before it). The cash is a row of its own,
    category 1, at its cost day by day (CA-RPT-20)."""

    @rule("CA-RPT-20")
    def test_a_sale_for_cash_moves_the_cost_into_the_cash_row(self):
        rows = [_row("BUYSELL", "2024-03-01", "ZZCOIN", 2, -500.0),
                _row("BUYSELL", "2025-03-01", "ZZCOIN", -2, 880.0)]
        rep = _report(rows, _cash({"points": [["2024-12-31", 0.0, 0.0],
                                              ["2025-03-01", 650.0, 880.0]]}))
        p = {r["symbol"]: r for r in rep["properties"]}
        self.assertEqual(rep["max_total_cost"], 880.0)   # not 500 + 880
        self.assertEqual(rep["max_total_date"], "2025-03-01")
        c = p["USD cash"]
        self.assertEqual((c["cash"], c["cash_account"], c["category"],
                          c["category_from"], c["country"], c["max_cost"],
                          c["year_end_cost"]),
                         (True, "crypto", 1, "cash", "CRYPTO", 880.0, 880.0))
        self.assertEqual(p["ZZCOIN"]["max_cost"], 500.0)
        self.assertIs(rep["total_settled"], True)
        self.assertEqual(rep["cash_accounts"], {"crypto": "CRYPTO"})
        brief = " ".join(T.render_brief(rep).split())
        self.assertIn("USD cash (crypto) CRYPTO 1 880.00 880.00", brief)
        self.assertIn("CAT: the form's category (1 cash; 2 shares;", brief)
        self.assertIn("! ZZCOIN, USD cash (crypto): crypto, check where "
                      "held — tjs t1135 --details", brief)
        self.assertEqual([r["symbol"] for r in rep["properties"]],
                         ["ZZCOIN", "USD cash"])          # cash last
        full = " ".join(T.render_report(rep).split())
        self.assertIn("USD cash (crypto): a crypto platform's cash — check "
                      "where held; its account's custodian_country in "
                      "taxjson.toml sets the country", full)
        self.assertIn("Read for: crypto (CRYPTO).", full)

    @rule("CA-RPT-20")
    def test_cash_that_buys_a_coin_is_not_counted_twice(self):
        rows = [_row("BUYSELL", "2025-02-01", "ZZCOIN", 2, -700.0)]
        rep = _report(rows, _cash({"points": [["2024-12-31", 750.0, 1000.0],
                                              ["2025-02-01", 225.0, 300.0]]}))
        self.assertEqual(rep["max_total_cost"], 1000.0)  # not 1000 + 700
        p = {r["symbol"]: r for r in rep["properties"]}
        self.assertEqual((p["USD cash"]["max_cost"],
                          p["USD cash"]["year_end_cost"]), (1000.0, 300.0))

    @rule("CA-RPT-20")
    def test_the_cash_can_take_the_total_over_the_threshold(self):
        rows = [_row("BUYSELL", "2025-02-01", "ZZCOIN", 2, -700.0)]
        rep = _report(rows, _cash({"points": [["2025-03-01", 90.0, 120.0]]}),
                      threshold=750.0)
        self.assertEqual(rep["max_total_cost"], 820.0)
        self.assertIs(rep["filing_required"], True)

    @rule("CA-RPT-20")
    def test_a_cost_the_ledger_cannot_give_leaves_the_total_open(self):
        rows = [_row("BUYSELL", "2025-02-01", "ZZCOIN", 2, -700.0)]
        rep = _report(rows, _cash({"points": [], "unknown": True,
                                   "why": "a gap"}))
        p = {r["symbol"]: r for r in rep["properties"]}
        self.assertIs(p["USD cash"]["unknown_acb"], True)
        self.assertIs(rep["total_settled"], False)
        self.assertEqual(rep["cash_unknown_cost"], ["USD cash (crypto)"])
        text = T.render_brief(rep)
        brief = " ".join(text.split())
        self.assertIn("=> NOT SETTLED: below the 100,000.00 CAD threshold, "
                      "but some cash's cost is unknown — tjs fx-cash "
                      "--ledger v2", brief)
        self.assertNotIn("unknown ACB", brief)
        self.assertNotIn("no T1135 required", brief)
        self.assertNotIn("cost unknown", brief)        # the verdict says it
        full = " ".join(T.render_report(rep).split())
        self.assertIn("USD cash (crypto): its cost is unknown — the "
                      "FX-on-cash ledger v2 refuses (a gap)", full)
        self.assertIn("=> NOT SETTLED", full)
        # Over the threshold all the same: a filing, the cash named.
        rep = _report(rows, _cash({"points": [], "unknown": True,
                                   "why": "a gap"}), threshold=500.0)
        self.assertIs(rep["filing_required"], True)
        text = T.render_brief(rep)
        self.assertIn("! USD cash (crypto): cost unknown (fx-cash v2 "
                      "refuses) — tjs fx-cash --ledger v2", text)
        self.assertTrue(all(len(a) <= 100 for a in _acts(text)), text)

    @rule("CA-RPT-20")
    def test_cash_the_books_give_no_balance_of_is_named(self):
        rows = [_row("BUYSELL", "2025-02-01", "ZZCOIN", 2, -700.0)]
        rep = _report(rows, _cash(not_counted=[{
            "account": "crypto", "currency": "CAD", "unknown": False,
            "why": "crypto/kraken: no balance at the start of 2025"}]))
        self.assertIs(rep["total_settled"], True)
        text = T.render_brief(rep)
        self.assertIn("! crypto: CAD cash not counted: add it by hand — tjs "
                      "t1135 --details", text)
        self.assertTrue(all(len(a) <= 100 for a in _acts(text)), text)
        full = " ".join(T.render_report(rep).split())
        self.assertIn("crypto: its CAD cash is not counted — crypto/kraken: "
                      "no balance at the start of 2025. Add its cost by "
                      "hand.", full)
        # The ledger could not run at all: not settled.
        rep = _report(rows, _cash(not_counted=[{
            "account": "crypto", "currency": "", "unknown": True,
            "why": "fx-cash v2: no rates"}]))
        self.assertIs(rep["total_settled"], False)
        self.assertIn("! crypto: cash not counted (fx-cash v2 could not run) "
                      "— tjs fx-cash --ledger v2", T.render_brief(rep))

    @rule("CA-RPT-20")
    def test_a_stablecoin_reward_is_the_cash_rows_income(self):
        cust = T.Custody({}, {}, [])
        k = cust.cash("USD", "crypto", "CRYPTO")
        cust.cash_coins[("crypto", "USDC")] = k
        self.assertEqual(cust.key_of("USDC", "crypto"), "CASH:USD:crypto")
        self.assertEqual(cust.key_of("USDC", "margin"), "USDC")
        self.assertEqual(cust.symbol(k), "USD cash")

    @rule("CA-RPT-20")
    def test_a_cash_document_of_another_version_is_refused(self):
        from taxjson.lib.cli_diag import InputContentError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cash.json"
            p.write_text(json.dumps(dict(_cash(), version=99)))
            with self.assertRaises(InputContentError):
                T.load_cash(p)
        self.assertIsNone(T.load_cash(None))


class TestTJ0454Checklist(unittest.TestCase):
    """The checklist's t1135 step is not done while the cash's cost is
    unknown, nor while an account's cash is not counted."""

    def _ctx(self, rep):
        return SimpleNamespace(settings={"country": "canada"}, year=2025,
                               today=_date(2026, 3, 1),
                               sub=lambda *a: (0, json.dumps(rep), ""))

    @rule("CA-RPT-20")
    def test_the_step_waits_for_the_cash(self):
        base = {"filing_required": False, "year_complete": True}
        r = CL.d_t1135(self._ctx(dict(
            base, total_settled=False,
            cash_unknown_cost=["USD cash (crypto)"])))
        self.assertEqual((r.status, r.cmd),
                         ("attention", "tjs fx-cash --ledger v2"))
        self.assertIn("USD cash (crypto) is unknown", r.detail)
        r = CL.d_t1135(self._ctx(dict(
            base, total_settled=True,
            cash_not_counted=[{"account": "abroad"}])))
        self.assertEqual(r.status, "manual")
        self.assertIn("the cash of abroad is not counted", r.detail)
        self.assertEqual(CL.d_t1135(self._ctx(dict(
            base, total_settled=True))).status, "done")
        # Over the threshold is a filing whatever the cash.
        self.assertEqual(CL.d_t1135(self._ctx(dict(
            base, filing_required=True, total_settled=False))).status,
            "manual")


# One account held with a custodian in the United States (TJ-0609):
# Canadian dollars there (an opening and the year-end statement
# balance) and US dollars that bought shares.
_TOML = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = ["USD"]
option_grant_timing_since = 2025

[accounts.abroad]
type = "taxable"
custodian_country = "USA"
"""

_ROWS_TT = """\
CASHOPEN 2025-01-01 CAD 600 600
CASHOPEN 2025-01-01 USD 0 0
CASHMOVE 2025-01-15 USD 1000 cost=1300
BUYSELL 2025-02-12 09:30:00 ZZQX.US 5 USD 100.0 -500.0 0
CASHBAL 2025-12-31 USD 500
CASHBAL 2025-12-31 CAD 600
"""


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


class TestTJ0609CashAtACustodianAbroad(unittest.TestCase):
    """TJ-0609: an account held with a custodian outside Canada holds
    its cash there too, foreign property in any currency (category 1),
    yet `tjs t1135` counted only its securities."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "proj"
        (cls.root / "inputs" / "abroad").mkdir(parents=True)
        (cls.root / "taxjson.toml").write_text(_TOML)
        (cls.root / "inputs" / "abroad" / "rows.tt").write_text(_ROWS_TT)
        (cls.root / "work").mkdir()
        _rates(cls.root / "work" / "to_base.csv")
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr + r.stdout

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule("CA-RPT-20")
    def test_the_accounts_cash_is_counted_at_its_cost(self):
        r = _cli(self.root, "t1135", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads(r.stdout)
        p = {(x["symbol"], x.get("cash_account")): x
             for x in j["properties"]}
        cad = p[("CAD cash", "abroad")]
        usd = p[("USD cash", "abroad")]
        self.assertEqual((cad["country"], cad["category"], cad["max_cost"],
                          cad["year_end_cost"]), ("USA", 1, 600.0, 600.0))
        # 1000 US dollars at a cost of 1300; 500 of them bought shares.
        self.assertEqual((usd["country"], usd["max_cost"],
                          usd["year_end_cost"]), ("USA", 1300.0, 650.0))
        self.assertEqual(p[("ZZQX.US", None)]["max_cost"], 675.0)
        # Jan 15: 600 + 1300; the purchase: 600 + 650 + 675, never
        # 600 + 1300 + 675.
        self.assertEqual(j["max_total_cost"], 1925.0)
        self.assertIs(j["total_settled"], True)
        self.assertEqual(j["cash_accounts"], {"abroad": "USA"})
        flat = " ".join(_cli(self.root, "t1135").stdout.split())
        self.assertIn("CAD cash (abroad) USA 1 600.00 600.00", flat)

    @rule("CA-RPT-20")
    def test_without_the_custodian_abroad_no_cash_is_read(self):
        (self.root / "taxjson.toml").write_text(
            _TOML.replace('custodian_country = "USA"\n', ""))
        self.addCleanup((self.root / "taxjson.toml").write_text, _TOML)
        j = json.loads(_cli(self.root, "t1135", "--json").stdout)
        self.assertFalse(any(x.get("cash") for x in j["properties"]))
        self.assertEqual(j["cash_accounts"], {})


if __name__ == "__main__":
    unittest.main()
