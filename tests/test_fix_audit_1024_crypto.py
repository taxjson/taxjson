"""Audit 2026-10-24, group "crypto": lib/crypto_sends and the Kraken and
Coinbase cash readers of the FX-on-cash ledger v2.

A35: two `crypto-sends --set`s at once each loaded sends.json, changed
their own send and saved; the later save dropped the other's decision.
The load, change and save are now one locked step (safe_write.file_lock
on a hidden lock file beside the manifest).

A36: the stablecoin pool de-duplicated the flows of EVERY crypto
account together; a Coinbase export with no ID column keys a row by its
cells, so identical purchases in two independent accounts collapsed to
one. Each account's exports are merged by themselves, then added up.

A45: `usd_pool` took a flow's CAD leg as the pool's cost whatever the
base currency; it now goes through the rates (1.0 for a CAD base).

A46: the pairing's weighted cost let a nearer arrival that lost coins
beat an exact one a day later. The assignment is now lexicographic
(most pairs, then fewest coins lost, then closest in time), as tax-logic
CA-CRYPTO-06 states.

C06 / C07: a stablecoin sold or bought for US dollars off par (200 USDC
for 197 USD) is the same folded currency, so no FXCONV — but the pool
moved by the spread. Kraken booked nothing; Coinbase booked only a
spread PAID. The net movement is now a FLOW either way.

C08: a Kraken cash trade with two receipt legs under one refid was
skipped. Legs are added up per currency; a group with more than two
currencies is refused naming the refid.

C09: the Coinbase cash reader accepted only the exact labels Buy / Sell,
so an "Advanced Trade Buy" of USDC priced in CAD was no FXCONV. It uses
the parser's own type regexes.

Every fixture is SYNTHETIC: invented ids, amounts and dates.
"""
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest import mock

from _tmpfiles import private_dir
from tax_rules import rule

from taxjson.lib import crypto_sends as cs
from taxjson.lib.brokerages.coinbase import coinbase_cash_events
from taxjson.lib.brokerages.kraken import kraken_cash_events

KR_HEADER = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"
CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")
# The older retail layout (no ID column): a row's identity is its cells.
CB_OLD_HEADER = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
                 "Spot Price Currency,Subtotal,"
                 "Total (inclusive of fees and/or spread)\n")


def _write(folder, name, text):
    p = Path(folder) / name
    p.write_text(text)
    return p


class _Toronto(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "America/Toronto"})
        p.start()
        self.addCleanup(p.stop)


# ------------------------------------------------------------------ A35

class TestConcurrentDecisions(unittest.TestCase):
    def test_a35_concurrent_decisions_both_kept(self):
        """A35: concurrent crypto-send decisions silently lose one saved
        decision. Two record_decision calls interleaved between their
        load and save now both survive: the second waits for the first's
        lock."""
        first_in = threading.Event()
        second_in = threading.Event()
        original = cs.load_decisions
        calls = []

        def load(p):
            doc = original(p)
            calls.append(threading.get_ident())
            if len(calls) == 1:
                first_in.set()
                # Unlocked, the second call gets here while we wait and
                # the two saves race; locked, it cannot, and this times
                # out (the lock is held).
                second_in.wait(timeout=0.5)
            else:
                second_in.set()
            return doc

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            with mock.patch.object(cs, "load_decisions", side_effect=load):
                with ThreadPoolExecutor(2) as pool:
                    list(pool.map(lambda sid: cs.record_decision(p, sid, "self"),
                                  ["send-a", "send-b"]))
            self.assertEqual(sorted(cs.load_decisions(p)["sends"]),
                             ["send-a", "send-b"])
            self.assertEqual(len(calls), 2)
            # The lock is a hidden file beside the manifest: no inputs
            # scan reads or counts it.
            lock = cs.manifest_lock_path(p)
            self.assertEqual(lock.name, ".sends.json.lock")
            self.assertTrue(lock.is_file())
            from taxjson.lib.checklist import _skipped_input_name
            self.assertTrue(_skipped_input_name(lock.name))
            # clear_decision takes the same lock and still answers.
            self.assertTrue(cs.clear_decision(p, "send-a"))
            self.assertFalse(cs.clear_decision(p, "send-a"))
            self.assertEqual(sorted(cs.load_decisions(p)["sends"]),
                             ["send-b"])

    def test_a35_clear_on_a_missing_folder_creates_nothing(self):
        """A35: `--unset` on an account with no manifest reports "none
        saved" and leaves no folder or lock behind."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "no-such" / "sends.json"
            self.assertFalse(cs.clear_decision(p, "send-a"))
            self.assertFalse(p.parent.exists())


# ------------------------------------------------------------------ A36

class TestPoolFlowsPerAccount(_Toronto):
    ROW = "2026-01-01 12:00:00,Buy,USDC,10,CAD,14,14\n"

    def test_a36_two_accounts_identical_rows_both_count(self):
        """A36: stablecoin pool deduplicates independent accounts with
        identical Coinbase rows. Two accounts' exports with the same
        no-ID row are two purchases (20 units); two files of ONE account
        with that row are still an overlapping export, read once (10
        units: audit 2026-10 #79)."""
        a = _write(private_dir(), "cb_a.csv", CB_OLD_HEADER + self.ROW)
        b = _write(private_dir(), "cb_b.csv", CB_OLD_HEADER + self.ROW)
        two = cs.account_pool_flows({"a": [("coinbase", a)],
                                     "b": [("coinbase", b)]}, ["a", "b"])
        self.assertEqual(sum(f["units"] for f in two), 20)
        self.assertEqual([round(f["cad"], 2) for f in two], [-14, -14])
        one = cs.account_pool_flows({"a": [("coinbase", a),
                                           ("coinbase", b)]}, ["a"])
        self.assertEqual(sum(f["units"] for f in one), 10)
        # An account with no export, or of another broker, adds nothing.
        self.assertEqual(cs.account_pool_flows(
            {"a": [("coinbase", a)], "c": [("questrade", b)]},
            ["a", "c", "d"]), one)


# ------------------------------------------------------------------ A45

class TestPoolCostInBase(unittest.TestCase):
    def test_a45_cad_leg_goes_through_the_rate_to_base(self):
        """A45: stablecoin pool treats a CAD purchase cost as the
        configured non-CAD base currency. CAD 14 for 10 units with CAD
        at 0.6 of the base is 0.84 a unit, not 1.40; a CAD base is
        unchanged (rate 1.0); no CAD rate leaves the flow unrated."""
        flow = {"dt": datetime(2026, 1, 1, 12), "units": 10, "cad": -14}
        rates = {"USD": {"2026-01-01": (0.9, "synthetic")},
                 "CAD": {"2026-01-01": (0.6, "synthetic")}}
        r = cs.usd_pool([flow], [], [], {}, cs.Rates(rates, "EUR"))
        self.assertEqual(r["avg_cost"], 0.84)
        self.assertEqual(r["unrated"], 0)
        r = cs.usd_pool([flow], [], [], {}, cs.Rates(rates, "CAD"))
        self.assertEqual(r["avg_cost"], 1.4)
        r = cs.usd_pool([flow], [], [], {},
                        cs.Rates({"USD": rates["USD"]}, "EUR"))
        self.assertEqual((r["unrated"], r["units_now"]), (1, 0.0))


# ------------------------------------------------------------------ A46

def _xfer(q, d, acct="a", ex="coinbase", time="12:00:00"):
    return dict(symbol="USDC", quantity=q, date=d, time=time, account=acct,
                exchange=ex, kind="send" if q < 0 else "receive", fee=0,
                ref="", price=0, currency="USD")


class TestPairingIsLexicographic(unittest.TestCase):
    @rule("CA-CRYPTO-06")
    def test_a46_exact_later_arrival_beats_lossy_near_one(self):
        """A46: transfer matcher prefers a lossy nearby arrival over an
        exact later arrival. 100 sent; 99.99 arrives at once and an
        exact 100 the next day: the exact one is the pair (fewest coins
        lost comes before closest in time)."""
        o = _xfer(-100, "2026-01-01")
        near = _xfer(99.99, "2026-01-01", "b", "kraken")
        far = _xfer(100, "2026-01-02", "b", "kraken")
        unmatched, pairs = cs.match_transfers([o, near, far])
        self.assertEqual(unmatched, [])
        self.assertEqual([(o["quantity"], i["quantity"]) for o, i in pairs],
                         [(-100, 100)])
        # Equal loss: the closer one in time.
        late = _xfer(100, "2026-01-03", "b", "kraken")
        _un, pairs = cs.match_transfers([o, late, far])
        self.assertIs(pairs[0][1], far)
        # One more pair beats any loss: two sends, the exact arrival
        # goes to the send the lossy one cannot serve.
        o2 = _xfer(-100, "2026-01-01", "a", "coinbase", "12:05:00")
        unmatched, pairs = cs.match_transfers([o, o2, near, far])
        self.assertEqual(unmatched, [])
        self.assertEqual(sorted(i["quantity"] for _o, i in pairs),
                         [99.99, 100])

    def test_a46_assign_is_lexicographic(self):
        """A46: `_assign` compares tuple costs component by component:
        a tiny second component is never outweighed by a huge third."""
        self.assertEqual(cs._assign([[(0, 1e-9, 0), (0, 0, 1e9)]]), [1])
        self.assertEqual(cs._assign([[(0, 0, 5), (0, 0, 3)]]), [1])
        self.assertEqual(cs._assign([[None, (1, 0, 0)]]), [1])
        # Two rows: the total of the first component first.
        self.assertEqual(cs._assign([[(0, 0, 0), (0, 9, 0), None],
                                     [(0, 0, 0), None, (1, 0, 0)]]),
                         [1, 0])


# ------------------------------------------------------- C06 / C08 Kraken

def _kraken(rows):
    p = _write(private_dir(), "kr_ledgers_syn.csv", KR_HEADER + rows)
    return kraken_cash_events(p, True)


def _moves(evs):
    return [(e["kind"], e["date"], e.get("currency"), round(e["amount"], 6),
             e.get("desc")) for e in evs if e["kind"] == "FLOW"]


def _convs(evs):
    return [(e["date"], e["from_ccy"], e["from_amt"], e["to_ccy"],
             e["to_amt"], e["desc"]) for e in evs if e["kind"] == "FXCONV"]


class TestKrakenCashConversions(_Toronto):
    @rule("CA-FX-07")
    def test_c06_stablecoin_sold_off_par_moves_the_pool(self):
        """C06: Kraken stablecoin-to-dollar conversions omit their cash
        spread. 200 USDC sold for 197 USD is the same folded currency
        (no FXCONV), and the 3 US dollars fewer are a FLOW, so the
        ledger reaches the export's own balance."""
        evs = _kraken(
            "L1,R1,2026-01-02 16:00:00,trade,,currency,USDC,-200,0,0\n"
            "L2,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,197,0,197\n")
        self.assertEqual(_convs(evs), [])
        self.assertEqual(_moves(evs), [("FLOW", "2026-01-02", "USD", -3.0,
                                        "Kraken USDC/USD spread")])
        bals = {(e["date"], e["currency"]): e["balance"]
                for e in evs if e["kind"] == "CASHBAL"}
        self.assertEqual(bals[("2026-01-02", "USD")], 197.0)
        # Above par: the spread comes in. At par: nothing but the fee.
        evs = _kraken(
            "L1,R1,2026-01-02 16:00:00,trade,,currency,USDC,-200,0,0\n"
            "L2,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,200.5,0.5,200\n")
        self.assertEqual(sorted(_moves(evs)), [
            ("FLOW", "2026-01-02", "USD", -0.5, "Kraken conversion fee"),
            ("FLOW", "2026-01-02", "USD", 0.5, "Kraken USDC/USD spread")])
        evs = _kraken(
            "L1,R1,2026-01-02 16:00:00,trade,,currency,USDC,-200,0,0\n"
            "L2,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,200,0.5,199.5\n")
        self.assertEqual(_moves(evs), [
            ("FLOW", "2026-01-02", "USD", -0.5, "Kraken conversion fee")])

    @rule("CA-FX-07")
    def test_c06_ledger_reconciles_a_spread(self):
        """C06: with the spread booked, ledger v2 reconciles the Kraken
        book to its balance instead of NOT COMPUTED with a -3 gap."""
        from taxjson.lib import fx_cash_v2 as fx
        evs = _kraken(
            "L1,R1,2026-01-02 16:00:00,trade,,currency,USDC,-200,0,0\n"
            "L2,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,197,0,197\n")
        for e in evs:
            e["book"] = "margin/kraken"
        carry = {"year": 2025,
                 "books": {"margin/kraken": {"USD": {"units": 200,
                                                     "cost": 280}}}}
        doc = fx.build([], {"events": evs, "lines": []}, "CAD", 2026,
                       lambda c, d: 1.4, country="canada", carry=carry)
        self.assertEqual(doc["status"], "computed", doc.get("problems"))
        self.assertTrue(all(r["ok"] for r in doc["reconciliation"]))
        self.assertEqual(doc["pools_year_end"]["USD"]["units"], 197)

    @rule("CA-FX-07")
    def test_c08_two_receipt_legs_are_one_conversion(self):
        """C08: Kraken cash conversions with multiple receipt legs are
        silently skipped. USD -100 with CAD +60 and +80 under one refid
        is USD 100 -> CAD 140; three currencies under one refid are
        refused naming the entry."""
        evs = _kraken(
            "L1,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,-100,0,0\n"
            "L2,R1,2026-01-02 16:00:00,trade,,currency,ZCAD,60,0,60\n"
            "L3,R1,2026-01-02 16:00:01,trade,,currency,ZCAD,80,0,140\n")
        self.assertEqual(_convs(evs), [("2026-01-02", "USD", 100.0, "CAD",
                                        140.0, "Kraken USD->CAD")])
        self.assertEqual(_moves(evs), [])
        # Two debit legs of one currency, one receipt: the same.
        evs = _kraken(
            "L1,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,-40,0,60\n"
            "L2,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,-60,0,0\n"
            "L3,R1,2026-01-02 16:00:01,trade,,currency,ZCAD,140,0,140\n")
        self.assertEqual(_convs(evs), [("2026-01-02", "USD", 100.0, "CAD",
                                        140.0, "Kraken USD->CAD")])
        with self.assertRaises(ValueError) as cm:
            _kraken(
                "L1,R1,2026-01-02 16:00:00,trade,,currency,ZUSD,-100,0,0\n"
                "L2,R1,2026-01-02 16:00:00,trade,,currency,ZCAD,60,0,60\n"
                "L3,R1,2026-01-02 16:00:01,trade,,currency,ZEUR,50,0,50\n")
        msg = str(cm.exception)
        self.assertIn("kr_ledgers_syn.csv", msg)
        self.assertIn("refid R1***", msg)
        self.assertIn("3 currencies", msg)
        self.assertIn("FXCONV", msg)
        self.assertNotIn("R1,", msg)


# ----------------------------------------------------- C07 / C09 Coinbase

def _coinbase(rows):
    p = _write(private_dir(), "cb_syn.csv", CB_HEADER + rows)
    return coinbase_cash_events(p, True)


class TestCoinbaseCashConversions(_Toronto):
    @rule("CA-FX-07")
    def test_c07_stablecoin_bought_below_par_is_a_flow_in(self):
        """C07: Coinbase stablecoin purchases below par omit the cash
        increase. 200 USDC bought for 197 USD is 3 US dollars more in
        the folded pool: a FLOW in; a spread paid stays a FLOW out."""
        evs = _coinbase("SYN-1,2026-01-02 16:00:00 UTC,Buy,USDC,200,USD,"
                        "0.985,197,197,0,Bought 200 USDC\n")
        self.assertEqual(_convs(evs), [])
        self.assertEqual(_moves(evs), [("FLOW", "2026-01-02", "USD", 3.0,
                                        "Coinbase buy USDC spread")])
        evs = _coinbase("SYN-2,2026-01-02 16:00:00 UTC,Sell,USDC,200,USD,"
                        "1,200,197,3,Sold 200 USDC\n")
        self.assertEqual(_moves(evs), [("FLOW", "2026-01-02", "USD", -3.0,
                                        "Coinbase sell USDC fee")])

    @rule("CA-FX-07")
    def test_c09_advanced_trade_of_a_stablecoin_is_a_conversion(self):
        """C09: Coinbase Advanced Trade stablecoin conversions never
        enter the cash ledger. An "Advanced Trade Buy" of 100 USDC
        priced in CAD is the FXCONV CAD 140 -> USD 100 the same row
        labelled Buy gives; a Sell the other way; a reward is not a
        trade."""
        adv = ("SYN-1,2026-01-02 16:00:00 UTC,Advanced Trade Buy,USDC,100,"
               "CAD,1.4,140,140,0,Bought 100 USDC on USDC-CAD\n")
        self.assertEqual(_convs(_coinbase(adv)),
                         [("2026-01-02", "CAD", 140.0, "USD", 100.0,
                           "Coinbase buy USDC")])
        self.assertEqual(_convs(_coinbase(adv)),
                         _convs(_coinbase(adv.replace("Advanced Trade Buy",
                                                      "Buy"))))
        self.assertEqual(_convs(_coinbase(
            "SYN-2,2026-01-02 16:00:00 UTC,Advanced Trade Sell,USDC,100,"
            "CAD,1.4,140,139,1,Sold 100 USDC on USDC-CAD\n")),
            [("2026-01-02", "USD", 100.0, "CAD", 139.0, "Coinbase sell USDC")])
        evs = _coinbase("SYN-3,2026-01-02 16:00:00 UTC,Rewards Income,USDC,"
                        "5,CAD,1.4,7,7,0,Rewards\n")
        self.assertEqual([e["kind"] for e in evs], [])


if __name__ == "__main__":
    unittest.main()
