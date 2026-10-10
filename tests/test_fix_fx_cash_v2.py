"""FX on foreign cash: the default ledger is NOT RELIABLE (never a filing
figure); the opt-in ledger v2 reads conversions, deposits/withdrawals and
statement balances, models foreign-currency margin debt and refuses
instead of guessing (tax-logic CA-FX-07 / US-FX-03).

The hand cases are the investigation's synthetic books (rates on the
10th of each month: 1.30, 1.35, 1.40, 1.38, 1.42); every expected figure
is worked out by hand in the comment beside it.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.bin.taxjson_fx_cash import (apply_jurisdiction, build_ledger,
                                         unreliable_status)

REPO_ROOT = Path(__file__).resolve().parent.parent

R = {"2025-01-10": 1.30, "2025-02-10": 1.35, "2025-03-10": 1.40,
     "2025-04-10": 1.38, "2025-05-10": 1.42}


def rate_of(cur, d):
    return R.get(d)


IB = "margin/ib"


def bs(d, q, net, sym="XYZ.US", book=IB, cur="USD", settle=None):
    return {"action": "BUYSELL", "date": d, "date_settle": settle or d,
            "time": "10:00:00", "symbol": sym, "quantity": q,
            "net_amount": net, "currency": cur, "account": "margin",
            "_book": book}


def div(d, gross, net, book=IB):
    return {"action": "DIVIDEND", "date": d, "date_settle": d,
            "symbol": "XYZ.US", "gross_amount": gross, "net_amount": net,
            "currency": "USD", "account": "margin", "_book": book}


def tax(d, amt, book=IB):
    return {"action": "TAX", "date": d, "date_settle": d,
            "symbol": "XYZ.US", "net_amount": amt, "currency": "USD",
            "account": "margin", "_book": book}


def tt(line, label="margin", book=IB):
    ev = CE.parse_line(line, "inputs/margin/cash.tt:1")
    ev.update(label=label, file="cash.tt", book=book, book_problem="")
    return ev


def broker(ev, label="margin", book=IB):
    ev.update(label=label, book=book, broker="ib", file="x.csv")
    return ev


def cash(events=(), lines=(), problems=()):
    return {"events": list(events), "lines": list(lines),
            "problems": list(problems), "stablecoins_as_cash": True}


OPEN0 = "CASHOPEN 2025-01-01 USD 0 0"
CLOSE0 = "CASHBAL 2025-12-31 USD 0"


def ledger(native, c, **kw):
    kw.setdefault("country", "canada")
    return V2.build(native, c, "CAD", 2025, rate_of, **kw)


class TestHandCases(unittest.TestCase):
    """The investigation's synthetic books under v2."""

    @rule("CA-FX-07")
    def test_deposit_buy_sell_dividend_convert_out(self):
        # CAD 13,000 -> USD 10,000 (01-10, actual CAD: 1.30); buy US$
        # 10,000 of XYZ (02-10 @1.35): +500.00; sell for 11,000 (03-10
        # @1.40): pool 11,000 / 15,400; dividend 100 gross (04-10 @1.38):
        # 11,100 / 15,538; withholding 15 @1.38: 20.70 - 15 x 15,538/
        # 11,100 = -0.297...; convert the 11,085 left to CAD 15,740.70  # pii-ok
        # (05-10): 15,740.70 - 15,517.0027 = +223.697...  # pii-ok
        # Net = 500 + 20.70 + 15,740.70 - 15,538 = 723.40.  # pii-ok
        native = [bs("2025-02-10", 100, 10000.0),
                  bs("2025-03-10", -100, 11000.0),
                  div("2025-04-10", 100.0, 85.0), tax("2025-04-10", 15.0)]
        c = cash([broker(CE.conv("2025-01-10", "CAD", 13000.0, "USD",
                                 10000.0)),
                  broker(CE.conv("2025-05-10", "USD", 11085.0, "CAD",
                                 15740.70))],
                 [tt(OPEN0), tt(CLOSE0)])
        doc = ledger(native, c)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 723.40, places=2)
        self.assertEqual(apply_jurisdiction(doc["net_gain"],
                                            "canada")["reportable"], 523.40)
        self.assertEqual(doc["close"]["books"], {})
        self.assertTrue(doc["reliable"])
        self.assertEqual(doc["label"], "v2 (opt-in, under audit)")

    @rule("CA-FX-07")
    def test_dlr_u_exit(self):
        # The same book left through DLR.U (Norbert's gambit): buying
        # DLR.U.TO with the 11,085 USD on 05-10 SPENDS it at the day's
        # rate, 11,085 x 1.42 = 15,740.70 — the same +723.40 net.  # pii-ok
        native = [bs("2025-02-10", 100, 10000.0),
                  bs("2025-03-10", -100, 11000.0),
                  div("2025-04-10", 100.0, 85.0), tax("2025-04-10", 15.0),
                  bs("2025-05-10", 1108.5, 11085.0, sym="DLR.U.TO")]
        c = cash([broker(CE.conv("2025-01-10", "CAD", 13000.0, "USD",
                                 10000.0))], [tt(OPEN0), tt(CLOSE0)])
        doc = ledger(native, c)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 723.40, places=2)

    @rule("CA-FX-07")
    def test_margin_loan_realised_on_repayment(self):
        # Buy US$10,000 with no USD (02-10 @1.35): a US$10,000 debt worth
        # 13,500; the sale (03-10 @1.40) repays it with dollars costing
        # 14,000: 13,500 - 14,000 = -500.00. The default ledger books
        # this as zero (the overdraft moves at the day's rate).
        native = [bs("2025-02-10", 100, 10000.0),
                  bs("2025-03-10", -100, 10000.0)]
        doc = ledger(native, cash([], [tt(OPEN0), tt(CLOSE0)]))
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], -500.0, places=2)
        self.assertEqual(len(doc["borrowed"]), 1)
        self.assertEqual(doc["borrowed"][0]["units"], 10000.0)
        self.assertEqual(doc["repaid"][0]["gain"], -500.0)
        v1 = build_ledger(native, "CAD", {}, 2025, rate_of=rate_of,
                          country="canada")
        self.assertAlmostEqual(v1["net_gain"], 0.0)
        self.assertEqual(v1["overdrafts_year"]["USD"],
                         {"count": 1, "units": 10000.0})

    @rule("CA-FX-07")
    def test_unseen_deposit_needs_a_declared_cost(self):
        # A US$10,000 deposit from outside the books (01-10), spent
        # 02-10 @1.35. Undeclared: NOT COMPUTED. cost=13,100: 13,500 -
        # 13,100 = +400. spot: 13,500 - 13,000 = +500.
        native = [bs("2025-02-10", 100, 10000.0)]
        dep = lambda: broker(CE.move("2025-01-10", "USD", 10000.0,
                                     where="s.csv", desc="Electronic"))
        close = "CASHBAL 2025-12-31 USD 0"
        doc = ledger(native, cash([dep()], [tt(OPEN0), tt(close)]))
        self.assertEqual(doc["status"], "not_computed")
        self.assertEqual([p["kind"] for p in doc["problems"]],
                         ["undeclared"])
        self.assertIn("CASHMOVE 2025-01-10 USD 10000.00 cost=",
                      doc["problems"][0]["text"])
        self.assertFalse(doc["reliable"])
        doc = ledger(native, cash([dep()], [
            tt(OPEN0), tt(close),
            tt("CASHMOVE 2025-01-10 USD 10000 cost=13100")]))
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 400.0, places=2)
        doc = ledger(native, cash([dep()], [
            tt(OPEN0), tt(close), tt("CASHMOVE 2025-01-10 USD 10000 spot")]))
        self.assertAlmostEqual(doc["net_gain"], 500.0, places=2)
        # [settings] fx_cash_inflow_cost = "spot": the explicit opt-in.
        doc = ledger(native, cash([dep()], [tt(OPEN0), tt(close)]),
                     inflow_spot=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 500.0, places=2)
        self.assertIn("fx_cash_inflow_cost", doc["declared"][0]["how"])

    def test_withdrawal_kept_leaves_at_cost(self):
        # Opening US$1,000 costing 1,300; US$400 withdrawn to your own
        # USD account (kept): it leaves at its cost (520), no gain; the
        # 600 left are spent 03-10 @1.40: 840 - 780 = +60.
        native = [bs("2025-03-10", 6, 600.0)]
        wd = broker(CE.move("2025-02-10", "USD", -400.0, where="s.csv"))
        doc = ledger(native, cash([wd], [
            tt("CASHOPEN 2025-01-01 USD 1000 1300"), tt(CLOSE0),
            tt("CASHMOVE 2025-02-10 USD -400 kept")]))
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 60.0, places=2)
        self.assertEqual(doc["declared"][0]["cost_left"], 520.0)
        # proceeds=: converted on the way (CAD 560 for US$400): 560 -
        # 520 = +40, plus the +60.
        wd = broker(CE.move("2025-02-10", "USD", -400.0, where="s.csv"))
        doc = ledger(native, cash([wd], [
            tt("CASHOPEN 2025-01-01 USD 1000 1300"), tt(CLOSE0),
            tt("CASHMOVE 2025-02-10 USD -400 proceeds=560")]))
        self.assertAlmostEqual(doc["net_gain"], 100.0, places=2)


class TestRefusals(unittest.TestCase):
    """v2 never guesses: each case is NOT COMPUTED with its reason."""

    def test_missing_opening_pool(self):
        doc = ledger([bs("2025-02-10", 100, 10000.0)], cash([], [tt(CLOSE0)]))
        self.assertEqual(doc["status"], "not_computed")
        self.assertIn("opening", [p["kind"] for p in doc["problems"]])
        self.assertIn("CASHOPEN 2025-01-01 USD", doc["problems"][0]["text"])

    def test_statement_opening_without_cost(self):
        # The statement holds US$5,000 at the start of the year: their
        # cost cannot be guessed.
        st = broker(CE.balance("2024-12-31", "USD", 5000.0, where="s"))
        doc = ledger([bs("2025-03-10", -1, 100.0)], cash([st],
                                                         [tt(CLOSE0)]))
        self.assertEqual(doc["status"], "not_computed")
        p = [x for x in doc["problems"] if x["kind"] == "opening"][0]
        self.assertIn("5,000.00", p["text"])
        self.assertIn("CASHOPEN 2025-01-01 USD 5000.00", p["text"])
        # A statement opening of zero needs no cost.
        st = broker(CE.balance("2024-12-31", "USD", 0.0, where="s"))
        end = broker(CE.balance("2025-12-31", "USD", 100.0, where="s"))
        doc = ledger([bs("2025-03-10", -1, 100.0)], cash([st, end]))
        self.assertEqual(doc["status"], "computed", doc["problems"])

    def test_reconciliation_gap(self):
        # The statement ends the year at US$250; the ledger knows of
        # none: a conversion it does not see.
        doc = ledger([bs("2025-02-10", -1, 100.0)], cash([], [
            tt(OPEN0), tt("CASHBAL 2025-12-31 USD 350")]))
        self.assertEqual(doc["status"], "not_computed")
        rec = [p for p in doc["problems"] if p["kind"] == "reconcile"]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["amount"], 250.0)
        self.assertFalse(doc["reconciliation"][0]["ok"])

    def test_no_balance_to_reconcile(self):
        doc = ledger([bs("2025-02-10", -1, 100.0)], cash([], [tt(OPEN0)]))
        self.assertEqual(doc["status"], "not_computed")
        self.assertIn("no statement balance",
                      doc["problems"][0]["text"])

    def test_overdraft_in_an_account_that_does_not_reconcile(self):
        # A spend with no USD and a year-end the statement contradicts:
        # the overdraft cannot be told from missing cash.
        doc = ledger([bs("2025-02-10", 1, 100.0)], cash([], [
            tt(OPEN0), tt("CASHBAL 2025-12-31 USD 0")]))
        kinds = [p["kind"] for p in doc["problems"]]
        self.assertIn("reconcile", kinds)
        self.assertIn("overdraft", kinds)
        od = [p for p in doc["problems"] if p["kind"] == "overdraft"][0]
        self.assertEqual((od["date"], od["book"], od["amount"]),
                         ("2025-02-10", IB, -100.0))

    def test_own_transfer_must_have_left_first(self):
        rbc = "margin/rbc"
        out_ = broker(CE.move("2025-03-10", "USD", -500.0, where="a.csv"))
        in_ = broker(CE.move("2025-02-10", "USD", 500.0, where="b.csv"),
                     book=rbc)
        doc = ledger([], cash([out_, in_], [
            tt("CASHOPEN 2025-01-01 USD 500 650"),
            tt("CASHOPEN 2025-01-01 USD 0 0", book=rbc),
            tt("CASHMOVE 2025-03-10 USD -500 own"),
            tt("CASHMOVE 2025-02-10 USD 500 own", book=rbc),
            tt(CLOSE0), tt("CASHBAL 2025-12-31 USD 500", book=rbc)]))
        self.assertIn("own", [p["kind"] for p in doc["problems"]])

    def test_unrated(self):
        native = [bs("2025-06-30", -1, 100.0)]         # no rate that day
        doc = ledger(native, cash([], [tt(OPEN0),
                                       tt("CASHBAL 2025-12-31 USD 100")]))
        self.assertEqual(doc["status"], "not_computed")
        self.assertIn("rate", [p["kind"] for p in doc["problems"]])

    def test_unattributed_tt_rows(self):
        row = bs("2025-02-10", -1, 100.0, book=None)
        doc = ledger([row], cash([], [tt(OPEN0), tt(CLOSE0)]))
        self.assertIn("book", [p["kind"] for p in doc["problems"]])


class TestPerAccount(unittest.TestCase):
    @rule("CA-FX-07")
    def test_debt_is_per_broker_account(self):
        # IB holds US$10,000 (cost 13,000); RBC spends US$1,000 it does
        # not hold (02-10 @1.35): RBC BORROWS (1,350) — the IB dollars
        # are not its. The 03-10 sale in RBC (@1.40) repays: 1,350 -
        # 1,400 = -50. IB's pool is untouched.
        rbc = "margin/rbc:3f2a"
        native = [bs("2025-02-10", 10, 1000.0, book=rbc),
                  bs("2025-03-10", -10, 1000.0, book=rbc)]
        doc = ledger(native, cash([], [
            tt("CASHOPEN 2025-01-01 USD 10000 13000"),
            tt("CASHOPEN 2025-01-01 USD 0 0", book=rbc),
            tt("CASHBAL 2025-12-31 USD 10000"),
            tt("CASHBAL 2025-12-31 USD 0", book=rbc)]))
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], -50.0, places=2)
        self.assertEqual(doc["close"]["books"],
                         {IB: {"USD": {"units": 10000.0, "cost": 13000.0}}})
        # A balance the statement disagrees with is caught per account.
        doc = ledger(native, cash([], [
            tt("CASHOPEN 2025-01-01 USD 10000 13000"),
            tt("CASHOPEN 2025-01-01 USD 0 0", book=rbc),
            tt("CASHBAL 2025-12-31 USD 9000"),
            tt("CASHBAL 2025-12-31 USD 1000", book=rbc)]))
        bad = sorted(p["book"] for p in doc["problems"]
                     if p["kind"] == "reconcile")
        self.assertEqual(bad, [IB, rbc])

    def test_own_transfer_carries_cost(self):
        # IB sends its US$1,000 (cost 1,300) to RBC as an own move; RBC
        # spends it 03-10 @1.40: 1,400 - 1,300 = +100.
        rbc = "margin/rbc"
        out_ = broker(CE.move("2025-02-10", "USD", -1000.0, where="a.csv"))
        in_ = broker(CE.move("2025-02-10", "USD", 1000.0, where="b.csv"),
                     book=rbc)
        doc = ledger([bs("2025-03-10", 10, 1000.0, book=rbc)],
                     cash([out_, in_], [
                         tt("CASHOPEN 2025-01-01 USD 1000 1300"),
                         tt("CASHOPEN 2025-01-01 USD 0 0", book=rbc),
                         tt("CASHMOVE 2025-02-10 USD -1000 own"),
                         tt("CASHMOVE 2025-02-10 USD 1000 own", book=rbc),
                         tt(CLOSE0), tt("CASHBAL 2025-12-31 USD 0",
                                        book=rbc)]))
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 100.0, places=2)

    def test_unsettled_trade_counts_in_the_statement_balance(self):
        # A sale on Dec 31 settling Jan 2: the statement's trade-date
        # balance includes it, the settled ledger does not yet — the
        # check adds it back.
        native = [bs("2025-12-31", -1, 100.0, settle="2026-01-02")]
        doc = ledger(native, cash([], [tt(OPEN0),
                                       tt("CASHBAL 2025-12-31 USD 100")]))
        self.assertEqual(doc["reconciliation"][0]["gap"], 0.0)

    def test_carry_from_the_prior_close(self):
        # The prior year's close carries the pool: US$1,000 costing
        # 1,300, spent 02-10 @1.35: +50.
        carry = {"year": 2024, "books": {IB: {"USD": {"units": 1000.0,
                                                      "cost": 1300.0}}}}
        doc = ledger([bs("2025-02-10", 10, 1000.0)],
                     cash([], [tt(CLOSE0)]), carry=carry)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 50.0, places=2)
        self.assertEqual(doc["carried_in"], 1)


class TestBothCountries(unittest.TestCase):
    @rule("CA-FX-07")
    @rule("US-FX-03")
    @rule_absent("CA-FX-07", country="usa")
    def test_same_ledger_two_verdicts(self):
        native = [bs("2025-02-10", 100, 10000.0),
                  bs("2025-03-10", -100, 10000.0)]
        c = lambda: cash([], [tt(OPEN0), tt(CLOSE0)])
        ca = ledger(native, c(), country="canada")
        us = ledger(native, c(), country="usa")
        self.assertEqual(ca["net_gain"], us["net_gain"])
        # Canada: the $200 de minimis (s.39(1.1)); the US: ordinary
        # income in full (§988), no exemption.
        self.assertEqual(apply_jurisdiction(ca["net_gain"],
                                            "canada")["reportable"], -300.0)
        self.assertEqual(apply_jurisdiction(us["net_gain"],
                                            "usa")["reportable"], -500.0)

    @rule("US-FX-03")
    def test_us_base_usd_cad_is_foreign(self):
        # A US project: CAD is the foreign currency. US$ 1,000 -> CAD
        # 1,350 (actual USD cost 1,000) on 01-10; the CAD is spent on a
        # Canadian share 02-10 at a 0.75 USD/CAD rate: 1,012.50 - 1,000  # pii-ok
        # = +12.50 ordinary income.
        rates = {"2025-02-10": 0.75}
        native = [bs("2025-02-10", 10, 1350.0, sym="XYZ.TO", cur="CAD")]
        c = cash([broker(CE.conv("2025-01-10", "USD", 1000.0, "CAD",
                                 1350.0))],
                 [tt("CASHOPEN 2025-01-01 CAD 0 0"),
                  tt("CASHBAL 2025-12-31 CAD 0")])
        doc = V2.build(native, c, "USD", 2025, lambda cu, d: rates.get(d),
                       country="usa")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertAlmostEqual(doc["net_gain"], 12.50, places=2)


class TestDefaultNotReliable(unittest.TestCase):
    @rule("CA-FX-07")
    @rule("US-FX-03")
    def test_v1_status_is_never_reliable(self):
        native = [bs("2025-02-10", 100, 10000.0)]
        led = build_ledger(native, "CAD", {}, 2025, rate_of=rate_of,
                           country="canada")
        st = unreliable_status(led, 2025,
                               apply_jurisdiction(led["net_gain"], "canada"))
        self.assertFalse(st["reliable"])
        self.assertEqual(
            st["headline"],
            "FX on foreign cash: NOT RELIABLE for 2025 — 1 in-year "
            "overdraft (10,000.00 USD); conversions, deposits/withdrawals "
            "and margin balances are not read; do not file this figure")
        self.assertIn("reportable", st["unreliable_raw"])
        self.assertNotIn("reportable", st)


class TestTtLines(unittest.TestCase):
    def test_forms(self):
        e = CE.parse_line("FXCONV 2025-03-04 CAD 1350 USD 1000 at=ib")
        self.assertEqual((e["from_ccy"], e["from_amt"], e["to_ccy"],
                          e["to_amt"], e["at"]),
                         ("CAD", 1350.0, "USD", 1000.0, "ib"))
        e = CE.parse_line("CASHMOVE 2025-03-04 USD -400 kept  # to my bank")
        self.assertEqual(e["decl"], {"how": "kept", "value": None})
        e = CE.parse_line("CASHMOVE 2025-03-04 USD 400 cost=540.50")
        self.assertEqual(e["decl"], {"how": "cost", "value": 540.5})
        e = CE.parse_line("CASHOPEN 2025-01-01 USD -250 330")
        self.assertEqual((e["units"], e["cost"]), (-250.0, 330.0))
        e = CE.parse_line("CASHBAL 2025-12-31 USD 1,234.50 at=rbc:3f2a")
        self.assertEqual((e["balance"], e["at"]), (1234.5, "rbc:3f2a"))
        self.assertIsNone(CE.parse_line("BUYSELL 2025-01-01 09:30:00 X"))

    def test_malformed_lines_name_the_form(self):
        for bad in ("CASHMOVE 2025-03-04 USD 400",            # no how
                    "CASHMOVE 2025-03-04 USD 400 kept",       # kept is out
                    "CASHMOVE 2025-03-04 USD -400 cost=10",   # cost is in
                    "CASHMOVE USD 2025-03-04 400 spot",       # date first
                    "FXCONV 2025-03-04 USD 100 USD 100",
                    "CASHOPEN 2025-01-01 USD 0 5",
                    "CASHBAL 2025-13-01 USD 5"):
            with self.subTest(bad=bad):
                with self.assertRaises(CE.CashLineError) as cm:
                    CE.parse_line(bad, "x.tt:3")
                self.assertIn("x.tt:3", str(cm.exception))

    def test_tt_to_json_skips_cash_lines(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rows.tt"
            p.write_text("CASHOPEN 2025-01-01 USD 0 0\n"
                         "BUYSELL 2025-02-10 09:30:00 XYZ.US 10 USD 10.0 "
                         "-100.0 0\n"
                         "CASHBAL 2025-12-31 USD 0\n")
            doc = tt_to_json(p, "margin")
            self.assertEqual([t["action"] for t in doc["transactions"]],
                             ["BUYSELL"])
            p.write_text("CASHMOVE 2025-02-10 USD 400\n")
            with self.assertRaises(ValueError):
                tt_to_json(p, "margin")


IB_STATEMENT = """\
Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm in CAD,MTM in CAD,Code
Trades,Data,Order,Forex,CAD,USD.CAD,"2025-01-10, 10:00:00","1,000",1.3,1.3,"-1,300",-2,0,
Trades,Data,Order,Forex,CAD,USD.CAD,"2025-05-10, 10:00:00",-500,1.42,1.42,710,-2,0,
Trades,Data,Order,Forex,USD,GBP.USD,"2025-04-10, 10:00:00",100,1.25,1.25,-125,-1,0,
Trades,SubTotal,,Forex,CAD,USD.CAD,,500,,,-590,-4,0,
Deposits & Withdrawals,Header,Currency,Account,Settle Date,Description,Amount
Deposits & Withdrawals,Data,USD,U55X01,2025-02-03,Electronic Fund Transfer,250
Deposits & Withdrawals,Data,USD,U55X01,2025-03-03,Internal Transfer Out To Account U55X02,-40
Deposits & Withdrawals,Data,USD,U55X02,2025-03-03,Internal Transfer In From Account U55X01,40
Deposits & Withdrawals,Data,USD,U55X01,2025-06-02,Adjustment: Deposit Advance (First 100.00 of 300.00),100
Deposits & Withdrawals,Data,USD,U55X01,2025-06-05,Cancellation (First 100.00 of 300.00),-100
Deposits & Withdrawals,Data,Total,,,,250
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Starting Cash,USD,0,0,0,
Cash Report,Data,Ending Cash,USD,625,625,0,
Cash Report,Data,Starting Cash,Base Currency Summary,0,0,0,
"""


class TestBrokerCashEvents(unittest.TestCase):
    def test_ib_forex_deposits_and_cash_report(self):
        from taxjson.lib.brokerages.ib_extractor import ib_cash_events
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ib_2025.csv"
            p.write_text(IB_STATEMENT)
            evs = ib_cash_events(p)
        convs = [e for e in evs if e["kind"] == "FXCONV"]
        self.assertEqual([(e["date"], e["from_ccy"], e["from_amt"],
                           e["to_ccy"], e["to_amt"]) for e in convs],
                         [("2025-01-10", "CAD", 1300.0, "USD", 1000.0),
                          ("2025-05-10", "USD", 500.0, "CAD", 710.0),
                          ("2025-04-10", "USD", 125.0, "GBP", 100.0)])
        fees = [(e["currency"], e["amount"]) for e in evs
                if e["kind"] == "FLOW"]
        self.assertEqual(fees, [("CAD", -2.0), ("CAD", -2.0),
                                ("CAD", -1.0)])
        moves = CE.pair_internal([e for e in evs
                                  if e["kind"] == "CASHMOVE"])
        # The internal pair and the advance/cancellation cancel; the
        # EFT stays, to be declared.
        self.assertEqual([(e["date"], e["amount"]) for e in moves],
                         [("2025-02-03", 250.0)])
        bals = sorted((e["date"], e["currency"], e["balance"]) for e in evs
                      if e["kind"] == "CASHBAL")
        self.assertEqual(bals, [("2024-12-31", "USD", 0.0),
                                ("2025-12-31", "USD", 625.0)])

    def test_rbc_cash_rows(self):
        from taxjson.lib.brokerages.rbc_direct import rbc_cash_events
        text = ('"Account Activity Detail Report"\n\n'
                "Date,Activity,Symbol,Symbol Description,Quantity,Price,"
                "Settlement Date,Account,Value,Currency,Description\n"
                "\"January 10, 2025\",Deposits & Contributions,,,0,0,"
                "\"January 10, 2025\",55500001,500.00,USD,"
                "DEP - TRANSFER FUNDS FROM BANK\n"
                "\"February 10, 2025\",Withdrawals & De-registrations,,,0,0,"
                "\"February 11, 2025\",55500001,-200.00,USD,"
                "WIR - TRANSFER FUNDS TO BANK\n"
                "\"March 10, 2025\",Buy,XYZ,XYZ CORP,10,10,"
                "\"March 11, 2025\",55500001,-100.00,USD,BOUGHT XYZ\n")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rbc.csv"
            p.write_text(text)
            evs = rbc_cash_events(p)
        self.assertEqual([(e["date"], e["settle"], e["currency"],
                           e["amount"]) for e in evs],
                         [("2025-01-10", "2025-01-10", "USD", 500.0),
                          ("2025-02-10", "2025-02-11", "USD", -200.0)])
        self.assertTrue(all(e["account"] for e in evs))
        self.assertNotIn("55500001", json.dumps(evs))   # pii-ok

    def test_kraken_fiat_conversion_funding_and_balances(self):
        from taxjson.lib.brokerages.kraken import kraken_cash_events
        text = ('"txid","refid","time","type","subtype","aclass","asset",'
                '"amount","fee","balance"\n'
                '"L1","R1","2025-01-10 15:00:00","deposit","","currency",'
                '"ZCAD","1300.00","0","1300.00"\n'
                '"L2","R2","2025-01-11 15:00:00","trade","","currency",'
                '"ZCAD","-1300.00","0","0.00"\n'
                '"L3","R2","2025-01-11 15:00:00","trade","","currency",'
                '"ZUSD","1000.00","2.00","998.00"\n'
                '"L4","R3","2025-03-01 15:00:00","withdrawal","",'
                '"currency","ZUSD","-500.00","1.00","497.00"\n')
        os.environ.setdefault("TAXJSON_LOCAL_TZ", "America/Toronto")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kr_ledgers_2025.csv"
            p.write_text(text)
            evs = kraken_cash_events(p, True)
        kinds = [(e["kind"], e.get("currency") or e.get("to_ccy"))
                 for e in evs]
        self.assertIn(("FXCONV", "USD"), kinds)
        conv = [e for e in evs if e["kind"] == "FXCONV"][0]
        self.assertEqual((conv["from_ccy"], conv["from_amt"],
                          conv["to_amt"]), ("CAD", 1300.0, 1000.0))
        moves = [(e["currency"], e["amount"]) for e in evs
                 if e["kind"] == "CASHMOVE"]
        self.assertEqual(moves, [("CAD", 1300.0), ("USD", -500.0)])
        fees = [(e["currency"], e["amount"]) for e in evs
                if e["kind"] == "FLOW"]
        self.assertEqual(sorted(fees), [("USD", -2.0), ("USD", -1.0)])
        bals = {(e["date"], e["currency"]): e["balance"] for e in evs
                if e["kind"] == "CASHBAL"}
        self.assertEqual(bals[("2024-12-31", "USD")], 0.0)
        self.assertEqual(bals[("2025-03-01", "USD")], 497.0)


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------ CLI
from datetime import date as _date, timedelta as _td  # noqa: E402

_TOML = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = ["USD"]
option_grant_timing_since = 2025
{extra}
[accounts.margin]
type = "taxable"
"""

# Deposit US$10,000 (declared cost 13,000); buy 100 XYZ 02-12 @1.35:
# 13,500 - 13,000 = +500; sell 03-12 @1.40; year-end US$10,000 held.
_ROWS_TT = """\
CASHOPEN 2025-01-01 USD 0 0
CASHMOVE 2025-01-15 USD 10000 cost=13000
BUYSELL 2025-02-12 09:30:00 XYZ.US 100 USD 100.0 -10000.0 0
BUYSELL 2025-03-12 09:30:00 XYZ.US -100 USD 100.0 10000.0 0
CASHBAL 2025-12-31 USD 10000
"""


def _rates(path):
    d, lines = _date(2025, 1, 1), []
    while d <= _date.today():
        r = ("1.3000" if d < _date(2025, 2, 10) else
             "1.3500" if d < _date(2025, 3, 10) else "1.4000")
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD {r} boc")
        d += _td(days=1)
    path.write_text("\n".join(lines) + "\n")


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


class TestCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "proj"
        (cls.root / "inputs" / "margin").mkdir(parents=True)
        (cls.root / "taxjson.toml").write_text(_TOML.format(extra=""))
        (cls.root / "inputs" / "margin" / "rows.tt").write_text(_ROWS_TT)
        (cls.root / "work").mkdir()
        _rates(cls.root / "work" / "to_base.csv")
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr + r.stdout

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _setting(self, extra):
        (self.root / "taxjson.toml").write_text(_TOML.format(extra=extra))
        self.addCleanup((self.root / "taxjson.toml").write_text,
                        _TOML.format(extra=""))

    @rule("CA-FX-07")
    def test_default_says_not_reliable_first(self):
        r = _cli(self.root, "fx-cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(
            "FX on foreign cash: NOT RELIABLE for 2025 — "), r.stdout)
        self.assertNotIn("REPORTABLE", r.stdout)
        j = json.loads(_cli(self.root, "fx-cash", "--json").stdout)
        self.assertIs(j["reliable"], False)
        self.assertNotIn("reportable", j)
        self.assertIn("reportable", j["unreliable_raw"])

    @rule("CA-FX-07")
    def test_sum_for_the_return(self):
        r = _cli(self.root, "sum", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        flat = " ".join(r.stdout.split())
        self.assertIn("FX on foreign cash: NOT RELIABLE for 2025 — 1 "
                      "in-year overdraft (10,000.00 USD); conversions, "
                      "deposits/withdrawals and margin balances are not "
                      "read; do not file this figure", flat)
        self.assertNotIn("after the $200 exemption", flat)
        fx = json.loads(_cli(self.root, "sum", "--json").stdout)[
            "filing"]["fx_cash"]
        self.assertIs(fx["reliable"], False)
        self.assertEqual(fx["line"], "15300")
        self.assertNotIn("reportable", fx)
        self.assertTrue(fx["reasons"])
        self.assertIn("net_gain", fx["unreliable_raw"])

    def test_sum_totals_do_not_move(self):
        a = json.loads(_cli(self.root, "sum", "--json").stdout)
        self._setting('fx_cash_ledger = "v2"')
        b = json.loads(_cli(self.root, "sum", "--json").stdout)
        self.assertEqual(a["totals"], b["totals"])
        self.assertEqual(a["filing"]["totals"], b["filing"]["totals"])

    @rule("CA-FX-07")
    def test_v2_flag_and_setting(self):
        r = _cli(self.root, "fx-cash", "--ledger", "v2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads(r.stdout)
        self.assertEqual(j["status"], "computed", j["problems"])
        self.assertAlmostEqual(j["net_gain"], 500.0, places=2)
        self.assertAlmostEqual(j["reportable"], 300.0, places=2)
        t = _cli(self.root, "fx-cash", "--ledger", "v2").stdout
        self.assertTrue(t.startswith("FX on foreign cash, ledger v2 "
                                     "(opt-in, under audit): computed"), t)
        self._setting('fx_cash_ledger = "v2"')
        flat = " ".join(_cli(self.root, "sum", "--details").stdout.split())
        self.assertIn("FX on foreign cash, ledger v2 (opt-in, under "
                      "audit), s.39(1.1): net 500.00, reportable 300.00 "
                      "after the $200 exemption — not in the rows above",
                      flat)
        fx = json.loads(_cli(self.root, "sum", "--json").stdout)[
            "filing"]["fx_cash"]
        self.assertEqual((fx["ledger"], fx["status"], fx["reportable"]),
                         ("v2", "computed", 300.0))

    def test_checklist_step(self):
        r = _cli(self.root, "checklist", "--json")
        doc = json.loads(r.stdout)
        step = [s for s in doc["steps"] if s["id"] == "fx-cash"][0]
        self.assertEqual(step["status"], "attention")
        self.assertIn("NOT RELIABLE", step["detail"])
        # v2 computed: attention until THAT figure is marked reviewed.
        self._setting('fx_cash_ledger = "v2"')
        doc = json.loads(_cli(self.root, "checklist", "--json").stdout)
        step = [s for s in doc["steps"] if s["id"] == "fx-cash"][0]
        self.assertEqual(step["status"], "attention")
        self.assertIn("mark it reviewed", step["detail"])
        r = _cli(self.root, "checklist", "--done", "fx-cash")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.addCleanup(lambda: (self.root / "checklist.json").unlink()
                        if (self.root / "checklist.json").exists() else 0)
        doc = json.loads(_cli(self.root, "checklist", "--json").stdout)
        step = [s for s in doc["steps"] if s["id"] == "fx-cash"][0]
        self.assertEqual(step.get("effective", step["status"]), "done",
                         step)

    def test_v2_refusal_is_said(self):
        tt = self.root / "inputs" / "margin" / "rows.tt"
        tt.write_text(_ROWS_TT.replace("CASHBAL 2025-12-31 USD 10000\n",
                                       "CASHBAL 2025-12-31 USD 9000\n"))
        self.addCleanup(tt.write_text, _ROWS_TT)
        r = _cli(self.root, "fx-cash", "--ledger", "v2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(
            "FX on foreign cash: NOT COMPUTED for 2025"), r.stdout)
        self.assertIn("no reportable figure", r.stdout)
        self.assertIn("gap -1,000.00", " ".join(r.stdout.split()))


class TestDedup(unittest.TestCase):
    def test_overlapping_exports_count_once(self):
        a = CE.conv("2025-01-10", "CAD", 1300.0, "USD", 1000.0)
        b = CE.conv("2025-01-10", "CAD", 1300.0, "USD", 1000.0)
        c = CE.conv("2025-01-10", "CAD", 1300.0, "USD", 1000.0)
        for e, f in ((a, "full.csv"), (b, "full.csv"), (c, "ytd.csv")):
            e.update(book=IB, file=f)
        # Two in one file are two conversions; the other file's copy is
        # the same event.
        self.assertEqual(len(CE.dedup_across_files([a, b, c])), 2)


class TestBookAttribution(unittest.TestCase):
    def test_tt_rows_need_a_cashbook_in_a_shared_folder(self):
        b = CE.Books()
        b.add("margin", "ib")
        b.add("margin", "webull", "abcd0123ef")
        b.file_broker[("margin", "ib_2025.csv")] = "ib"
        # Never guessed from the name: wb_*.tt beside wb_*.csv is not
        # enough.
        self.assertIsNone(b.for_tt("margin", "wb_manual.tt"))
        b.tt_book[("margin", "wb_manual.tt")] = b.for_at("margin",
                                                         "webull")[0]
        self.assertEqual(b.for_tt("margin", "wb_manual.tt"),
                         "margin/webull")
        one = CE.Books()
        one.add("solo", "ib")
        self.assertEqual(one.for_tt("solo", "x.tt"), "solo/ib")
        self.assertEqual(CE.Books().for_tt("hand", "x.tt"), "hand/tt")
        self.assertEqual(CE.parse_line("CASHBOOK webull")["book_name"],
                         "webull")
        with self.assertRaises(CE.CashLineError):
            CE.parse_line("CASHBOOK")

    def test_statement_correction_dated_before_its_period(self):
        # A withholding refund the 2026 statement carries, dated in
        # 2025: its cash moved in 2026 — not in the 2025 ledger.
        row = tax("2025-11-03", -50.0)
        row["source"] = "ib_2026.csv"
        per = {"kind": "PERIOD", "start": "2026-01-01", "end": "2026-09-30",
               "label": "margin", "file": "ib_2026.csv", "book": IB}
        doc = ledger([row], cash([per], [tt(OPEN0), tt(CLOSE0)]))
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["per_currency"], {})
        self.assertIn("before its period", doc["notes"][0])


class TestCloseYearCarry(unittest.TestCase):
    @rule("CA-FX-07")
    def test_close_year_records_the_v2_close_for_next_year(self):
        import shutil
        from taxjson.bin.taxjson_run import _fx_cash_carry
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                _TOML.format(extra='fx_cash_ledger = "v2"'))
            (root / "inputs" / "margin" / "rows.tt").write_text(_ROWS_TT)
            (root / "work").mkdir()
            _rates(root / "work" / "to_base.csv")
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            rec = json.loads((root / "filed" / "2025.json").read_text())
            # US$10,000 held at Dec 31, bought back at 1.40: cost 14,000.
            self.assertEqual(rec["fx_cash_v2"], {
                "year": 2025, "books": {"margin/tt": {
                    "USD": {"units": 10000.0, "cost": 14000.0}}}})
            nxt = Path(tmp) / "next"
            shutil.copytree(root / "inputs", nxt / "inputs")
            carry = _fx_cash_carry(
                nxt, {"prior_year_record": str(root / "filed" /
                                                 "2025.json")}, 2026)
            self.assertEqual(carry, rec["fx_cash_v2"])
            self.assertIsNone(_fx_cash_carry(nxt, {}, 2026))
