"""Kraken parser and cash-reader fixes of 2026-10-29, round 7 (group
kraken).

TJ-0861 (the v2 cash reader treated each half of a conversion two
exports split as a one-currency spread, its paid CAD lost), TJ-0888 (a
later ledger export's balance before its first row was dated to the
previous year end over another export's earlier rows), TJ-0750 (the
documented `earn` / `autoallocate` subtype was reported as unbooked
property), TJ-0739 (one ledger entry in two exports with its numbers
printed to different precision was refused as DIFFERENT content) and
TJ-0738 (an older export's copy of a cross-export swap leg, kept first,
dropped the newer copy's amountusd), TJ-0749 (a negative trade fee, a
maker rebate, was booked as a charge), TJ-0746 (fills of one second
kept the export's row order, not the fraction of a second the
timestamps state).

Every fixture is SYNTHETIC: fake ledger ids, invented amounts. Each
test's files live in a folder of their own.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from test_fix_1029_r2_cryptoparse import _brokerage

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.brokerages.kraken import (KrakenBrokerage,
                                           kraken_cash_events)

_KL_H2 = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance,amountusd,feeusd,feecurrency\n")


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    global _OLD_TZ
    _OLD_TZ = os.environ.get("TAXJSON_LOCAL_TZ")
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def tearDownModule():
    if _OLD_TZ is None:
        os.environ.pop("TAXJSON_LOCAL_TZ", None)
    else:
        os.environ["TAXJSON_LOCAL_TZ"] = _OLD_TZ


def _cash(bodies, tt_lines, year=2025):
    """The v2 ledger over Kraken ledger exports `bodies` (one file each,
    alone in a folder of their own, every file's events collected and
    deduplicated as cash_events.collect does), with `tt_lines`: (events,
    the built ledger)."""
    with tempfile.TemporaryDirectory() as td:
        events = []
        for n, body in enumerate(bodies):
            f = Path(td) / f"kraken_{n}.csv"
            f.write_text(_KL_H2 + body)
        for n in range(len(bodies)):
            f = Path(td) / f"kraken_{n}.csv"
            for e in kraken_cash_events(f):
                e.update(label="crypto", book="crypto/kraken",
                         broker="kraken", file=f.name)
                events.append(e)
    lines = []
    for ln in tt_lines:
        e = CE.parse_line(ln, "cash.tt:1")
        e.update(label="crypto", book="crypto/kraken", file="cash.tt",
                 book_problem="")
        lines.append(e)
    events = CE.dedup_across_files(events)
    doc = V2.build([], {"events": events, "lines": lines, "problems": [],
                        "stablecoins_as_cash": True},
                   "CAD", year, lambda c, d: 1.25, country="canada")
    return events, doc


# TJ-0861: CAD 150 spent just before UTC midnight for USDC 100 received
# two seconds later (one refid), later sold for CAD 400.
_SPEND = "L1,R1,2025-06-01 23:59:59,spend,,currency,CAD,spot,-150,0,,100,0,CAD\n"
_RECV = "L2,R1,2025-06-02 00:00:01,receive,,currency,USDC,spot,100,0,,100,0,USDC\n"
_SELL = ("L3,R2,2025-06-03 16:00:00,spend,,currency,USDC,spot,-100,0,,100,0,USDC\n"
         "L4,R2,2025-06-03 16:00:01,receive,,currency,CAD,spot,400,0,,100,0,CAD\n")
_OPEN = ["CASHOPEN 2025-01-01 USD 0 0", "CASHBAL 2025-12-31 USD 0"]


class TestKrakenCashConversionAcrossExports(unittest.TestCase):
    """TJ-0861: each export's half of a split conversion was a spread
    FLOW; the USD then took a synthetic spot cost (125) instead of the
    CAD 150 paid. The legs are joined across the account's exports."""

    def _convs(self, events):
        return sorted((e["date"], e["from_ccy"], e["from_amt"], e["to_ccy"],
                       e["to_amt"]) for e in events if e["kind"] == "FXCONV")

    @rule("CA-FX-07")
    def test_split_conversion_is_joined(self):
        events, doc = _cash([_SPEND, _RECV, _SELL], _OPEN)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 250.0)
        self.assertEqual(self._convs(events), [
            ("2025-06-01", "CAD", 150.0, "USD", 100.0),
            ("2025-06-03", "USD", 100.0, "CAD", 400.0)])
        self.assertFalse([e for e in events if e["kind"] == "FLOW"])

    def test_same_as_one_export(self):
        joined, doc_j = _cash([_SPEND + _RECV, _SELL], _OPEN)
        split, doc_s = _cash([_SPEND, _RECV, _SELL], _OPEN)
        self.assertEqual(self._convs(joined), self._convs(split))
        self.assertEqual(doc_j["net_gain"], doc_s["net_gain"])

    def test_overlapping_export_is_kept_once(self):
        # A partial export repeating the receipt beside the full one.
        events, doc = _cash([_SPEND + _RECV, _RECV, _SELL], _OPEN)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 250.0)
        self.assertEqual(len(self._convs(events)), 2)

    def test_a_coin_leg_elsewhere_is_no_spread(self):
        # CAD spent for SOL, the SOL receipt in the other export: the
        # position book's fill, never a CAD "spread" FLOW.
        spend = ("L5,R3,2025-06-01 23:59:59,spend,,currency,CAD,spot,-150,"
                 "0,,100,0,CAD\n")
        recv = ("L6,R3,2025-06-02 00:00:01,receive,,currency,SOL,spot,1,"
                "0,,100,0,SOL\n")
        events, _doc = _cash([spend, recv], [])
        self.assertFalse([e for e in events
                          if e["kind"] in ("FLOW", "FXCONV")], events)


class TestKrakenBalanceAcrossExports(unittest.TestCase):
    """TJ-0888: a June export's balance before its first row was dated
    to Dec 31 of the year before, over a January deposit in the other
    export: a false opening of 100 USD, the ledger not computed."""

    _DEP = "L1,F1,2025-01-02 16:00:00,deposit,,currency,ZUSD,spot,100,0,100,100,0,ZUSD\n"
    _WD = "L2,F2,2025-06-02 16:00:00,withdrawal,,currency,ZUSD,spot,-20,0,80,20,0,ZUSD\n"
    _TT = ["CASHMOVE 2025-01-02 USD 100 spot",
           "CASHMOVE 2025-06-02 USD -20 spot"]

    def _bals(self, events):
        return sorted((e["date"], e["currency"], e["balance"])
                      for e in events if e["kind"] == "CASHBAL")

    @rule("CA-FX-07")
    def test_sliced_history_computes(self):
        events, doc = _cash([self._DEP, self._WD], self._TT)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertNotIn(("2024-12-31", "USD", 100.0), self._bals(events))

    def test_same_as_one_export(self):
        _e, joined = _cash([self._DEP + self._WD], self._TT)
        _e, sliced = _cash([self._DEP, self._WD], self._TT)
        self.assertEqual((joined["status"], joined["net_gain"]),
                         (sliced["status"], sliced["net_gain"]))

    def test_yearly_exports_keep_their_opening(self):
        # A 2024 export and a 2025 one: the 2025 export's balance before
        # its first row IS the Dec 31 balance (nothing in between).
        dep = ("L1,F1,2024-03-02 16:00:00,deposit,,currency,ZUSD,spot,100,"
               "0,100,100,0,ZUSD\n")
        events, _doc = _cash([dep, self._WD], [])
        self.assertIn(("2024-12-31", "USD", 100.0), self._bals(events))


def _parse(files, name="kr_ledgers.csv"):
    """KrakenBrokerage over `files` ({name: text}, alone in a folder of
    their own): (transactions of `name`, stderr, the parser)."""
    parser = KrakenBrokerage()
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        for n, text in files.items():
            (Path(td) / n).write_text(text)
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(Path(td) / name)
    return txs, err.getvalue(), parser


class TestKrakenAutoallocate(unittest.TestCase):
    """TJ-0750: an `earn` / `autoallocate` pair (Kraken's documented
    spelling of an automatic Earn allocation) was UNBOOKED property; it
    is the same owned-wallet move as `autoallocation`."""

    _H = "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"

    def _pair(self, subtype):
        return self._H + (
            f"L1,REF1,2026-07-02 15:00:00,earn,{subtype},currency,SOL,"
            f"spot / main,-10,0,0\n"
            f"L2,REF1,2026-07-02 15:00:00,earn,{subtype},currency,SOL,"
            f"earn / bonded,10,0,10\n")

    def test_autoallocate_is_a_wallet_move(self):
        for subtype in ("autoallocation", "autoallocate"):
            with self.subTest(subtype=subtype):
                txs, err, parser = _parse({"kr_ledgers.csv":
                                           self._pair(subtype)})
                self.assertEqual(txs, [])
                self.assertNotIn("UNBOOKED", err)
                moves = sum(v for k, v in parser._skip_counts.items()
                            if "Earn wallet move" in k)
                self.assertEqual(moves, 2)

    def test_crypto_sends_skips_it(self):
        from taxjson.lib.crypto_sends import kraken_pool_flows
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers.csv"
            p.write_text(self._pair("autoallocate").replace("SOL", "USDC"))
            self.assertEqual(kraken_pool_flows(p), [])


def _trades(r):
    return sorted((t["symbol"], t["quantity"], t["net_amount"], t.get("id"))
                  for t in json.loads(r.stdout)["transactions"])


# A swap of 1 SOL for 2 ETH (refid R1) whose spend and receive two
# exports split; the spend repeated in an overlapping export.
_SW_SPEND = "K1,R1,2025-12-31 23:59:59,spend,,currency,SOL,spot,-1,0,0,100,0,SOL\n"
_SW_RECV = "K2,R1,2026-01-01 00:00:01,receive,,currency,ETH,spot,2,0,2,,0,ETH\n"


class TestKrakenRepeatedLedgerEntry(unittest.TestCase):
    """TJ-0739 / TJ-0738: one ledger entry in two overlapping exports."""

    _BOOKED = [("ETH", 2.0, 100.0, "R1-buy"), ("SOL", -1.0, 100.0, "R1-sell")]

    def test_numbers_spelled_differently_are_one_entry(self):
        # TJ-0739: -1 / 0 in one export, -1.00000000 / 0.00000000 in
        # the other: one entry, the swap booked once.
        b = _SW_SPEND.replace(",-1,0,0,100,", ",-1.00000000,0.00000000,0,100,")
        r, _side = _brokerage({"a_old.csv": _KL_H2 + _SW_SPEND,
                               "b_new.csv": _KL_H2 + b,
                               "c_receive.csv": _KL_H2 + _SW_RECV},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_trades(r), self._BOOKED)
        self.assertNotIn("DIFFERENT", r.stderr)

    def test_a_different_number_is_still_refused(self):
        b = _SW_SPEND.replace(",-1,0,0,100,", ",-1.5,0,0,100,")
        r, _side = _brokerage({"a_old.csv": _KL_H2 + _SW_SPEND,
                               "b_new.csv": _KL_H2 + b,
                               "c_receive.csv": _KL_H2 + _SW_RECV},
                              "kraken", "--strict")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DIFFERENT", " ".join(r.stderr.split()))

    def test_newer_valuation_kept_whatever_the_order(self):
        # TJ-0738: the older copy of the spend has no amountusd; the
        # newer one has 100. Either order books the swap at 100.
        old = _SW_SPEND.replace(",0,100,0,SOL", ",0,,0,SOL")
        for files in ({"a_old.csv": old, "b_new.csv": _SW_SPEND},
                      {"a_new.csv": _SW_SPEND, "b_old.csv": old}):
            with self.subTest(order=sorted(files)):
                files = {k: _KL_H2 + v for k, v in files.items()}
                files["c_receive.csv"] = _KL_H2 + _SW_RECV
                r, _side = _brokerage(files, "kraken", "--strict")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(_trades(r), self._BOOKED)

    def test_two_whole_copies_agree_on_the_valuation(self):
        # Each export holds the whole swap, the older without amountusd:
        # both book it at 100 (the run keeps one), never a restated row.
        old = _SW_SPEND.replace(",0,100,0,SOL", ",0,,0,SOL") + _SW_RECV
        new = _SW_SPEND + _SW_RECV.replace(",2,,0,ETH", ",2,100,0,ETH")
        r, _side = _brokerage({"a_old.csv": _KL_H2 + old,
                               "b_new.csv": _KL_H2 + new},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(set(_trades(r))), self._BOOKED)


_TR_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_LG_H = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"


def _rebate_files(fee, ledger_fee=None, coin_fee="0"):
    """A SYN/USD maker buy of 10 for 500 with trade fee `fee`, and its
    ledger (quote fee `ledger_fee`, default the same; base fee
    `coin_fee`); later sold for 600."""
    lf = fee if ledger_fee is None else ledger_fee
    tr = _TR_H + (
        f"T1,O1,SYN/USD,2025-07-02 15:00:00,buy,limit,50,500,{fee},10,0,,\n"
        "T2,O2,SYN/USD,2025-12-02 15:00:00,sell,limit,60,600,0,10,0,,\n")
    bal = 900 - 500 - float(lf)
    lg = _LG_H + (
        f"L1,T1,2025-07-02 15:00:00,trade,,currency,SYN,10,{coin_fee},10\n"
        f"L2,T1,2025-07-02 15:00:00,trade,,currency,USD,-500,{lf},{bal}\n"
        "L3,T2,2025-12-02 15:00:00,trade,,currency,SYN,-10,0,0\n"
        f"L4,T2,2025-12-02 15:00:00,trade,,currency,USD,600,0,{bal + 600}\n")
    return {"kr_trades.csv": tr, "kr_ledgers.csv": lg}


class TestKrakenMakerRebate(unittest.TestCase):
    """TJ-0749: a negative fee (Kraken's maker rebate; the ledger's
    balance is the prior one + amount − fee) was abs()'d into a charge:
    the buy cost 500.10 instead of 499.90."""

    def _buy(self, files):
        txs, err, _p = _parse(files, "kr_trades.csv")
        buy = [t for t in txs if t["quantity"] > 0]
        self.assertEqual(len(buy), 1, err)
        return buy[0], err

    def test_rebate_lowers_the_cost(self):
        buy, _err = self._buy(_rebate_files("-0.1"))
        self.assertAlmostEqual(buy["net_amount"], 499.9, places=8)
        self.assertAlmostEqual(buy["fee"], -0.1, places=8)

    def test_a_charge_still_adds(self):
        buy, _err = self._buy(_rebate_files("0.1"))
        self.assertAlmostEqual(buy["net_amount"], 500.1, places=8)

    def test_rebate_on_a_sale_adds_to_the_proceeds(self):
        files = _rebate_files("0")
        files["kr_trades.csv"] = files["kr_trades.csv"].replace(
            "600,0,10,0", "600,-0.12,10,0")
        files["kr_ledgers.csv"] = files["kr_ledgers.csv"].replace(
            "USD,600,0,", "USD,600,-0.12,")
        txs, err, _p = _parse(files, "kr_trades.csv")
        sell = [t for t in txs if t["quantity"] < 0]
        self.assertEqual(len(sell), 1, err)
        self.assertAlmostEqual(sell[0]["net_amount"], 600.12, places=8)

    def test_sign_disagreement_is_refused(self):
        # The trades export says a rebate, the ledger a charge.
        with self.assertRaises(ValueError):
            _parse(_rebate_files("-0.1", ledger_fee="0.1"), "kr_trades.csv")

    def test_crypto_sends_pool_reads_the_rebate_as_a_credit(self):
        from taxjson.lib.crypto_sends import kraken_pool_flows
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers.csv"
            p.write_text(_LG_H + (
                "L1,T1,2025-07-02 15:00:00,trade,,currency,SYN,10,0,10\n"
                "L2,T1,2025-07-02 15:00:00,trade,,currency,USDC,-500,-0.1,"
                "400.1\n"))
            flows = kraken_pool_flows(p)
        self.assertEqual(len(flows), 1)
        self.assertAlmostEqual(flows[0]["units"], -499.9, places=8)

    def test_a_rebate_in_the_coin_is_refused(self):
        with self.assertRaisesRegex(ValueError, "rebate paid in the traded"):
            _parse(_rebate_files("0", coin_fee="-0.002"), "kr_trades.csv")


class TestKrakenSubsecondOrder(unittest.TestCase):
    """TJ-0746: a sale at 15:00:00.1000 and a rebuy at 15:00:00.9000 of
    one second were replayed in the export's row order: a descending
    export put the rebuy into the average cost before the sale."""

    _ROWS = [("T1", "2025-01-06 15:00:00.1000", "buy", 5, 50),
             ("T2", "2025-03-03 15:00:00.1000", "sell", 10, 100),
             ("T3", "2025-03-03 15:00:00.9000", "buy", 10.01, 100.1)]

    def _files(self, rows):
        tr, lg = _TR_H, _LG_H
        for tid, stamp, typ, price, cost in rows:
            tr += (f"{tid},O{tid},SOL/CAD,{stamp},{typ},market,{price},"
                   f"{cost},0,10,0,,\n")
            sign = 1 if typ == "buy" else -1
            lg += (f"{tid}B,{tid},{stamp},trade,,currency,SOL,{10 * sign},"
                   f"0,10\n{tid}Q,{tid},{stamp},trade,,currency,CAD,"
                   f"{-cost * sign},0,100\n")
        return {"kr_trades.csv": tr, "kr_ledgers.csv": lg}

    @rule("CA-ACB-01")
    def test_export_order_does_not_change_the_gain(self):
        import dataclasses
        from taxjson.lib.core import TaxTransaction as T
        from taxjson.lib.pipeline import GainsRequest, run_gains
        fields = {f.name for f in dataclasses.fields(T)}
        for rows in (self._ROWS, list(reversed(self._ROWS))):
            with self.subTest(first=rows[0][0]):
                txs, err, _p = _parse(self._files(rows), "kr_trades.csv")
                same = [t["id"] for t in txs if t["date"] == "2025-03-03"]
                self.assertEqual(same, ["T2", "T3"], err)
                res = run_gains(
                    [T(**{k: v for k, v in x.items() if k in fields})
                     for x in txs],
                    req=GainsRequest(country="canada", year=2025,
                                     taxable=True, phantom_hint=False))
                self.assertAlmostEqual(res["summary"]["total_gain"], 50.0,
                                       places=6)

    def test_one_instant_keeps_the_export_order(self):
        rows = [("T2", "2025-03-03 15:00:00", "sell", 10, 100),
                ("T3", "2025-03-03 15:00:00", "buy", 10.01, 100.1)]
        for order in (rows, list(reversed(rows))):
            txs, err, _p = _parse(self._files(order), "kr_trades.csv")
            self.assertEqual([t["id"] for t in txs],
                             [r[0] for r in order], err)


if __name__ == "__main__":
    unittest.main()
