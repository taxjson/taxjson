"""Audit batch A (2026-10): the crypto and cash books.

74: a Coinbase Buy / Sell of a stablecoin priced in CAD with a blank
Total read the Total as 0 in the FX-on-cash ledger v2 reader
(`coinbase_cash_events`) and the crypto-sends pool
(`coinbase_pool_flows`): a zero-cost acquisition. Both now derive it as
the parser does (Subtotal +/- fee, else quantity x price +/- fee) and
refuse, naming the row, when nothing is there to derive it from.

79: `crypto_sends.build_report` added up each export's stablecoin pool
flows, so two overlapping Kraken ledgers (or Coinbase exports) counted
the shared entries twice. Each flow now carries its identity (the
Kraken refid, the Coinbase ID or row) and overlapping files are read
once; genuine same-content rows in one file still both count.

Finding 75 (Questrade cash conversions in a newest-first export) is
pinned in test_fix_v0242_misc.TestQuestradeCashEvents.

Every fixture is SYNTHETIC: invented ids, amounts and dates.
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from test_fix_x_mut_crypto_sends import (CB_HEADER, SIDE, USDC_ID, _UTC,
                                         _kr, _project)

from taxjson.lib import crypto_sends as cs
from taxjson.lib.brokerages.coinbase import coinbase_cash_events


def _write(tmp, name, text):
    p = Path(tmp) / name
    p.write_text(text)
    return p


# ------------------------------------------------- 74: blank Coinbase Total

class TestCoinbaseBlankTotal(_UTC):
    def _cash(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            evs = coinbase_cash_events(_write(tmp, "cb.csv",
                                              CB_HEADER + rows))
        return [(e["from_ccy"], e["from_amt"], e["to_ccy"], e["to_amt"])
                for e in evs if e["kind"] == "FXCONV"]

    def _pool(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            flows = cs.coinbase_pool_flows(_write(tmp, "cb.csv",
                                                  CB_HEADER + rows))
        return [(f["units"], round(f["cad"], 2)) for f in flows]

    @rule("CA-FX-07")
    def test_cash_events_derive_a_blank_total(self):
        self.assertEqual(self._cash(
            # Subtotal + fee on a buy
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,1.5,150,,1.5,"
            "Bought 100 USDC\n"
            # no Subtotal either: quantity x price + fee
            "SYN-2,2025-02-04 16:00:00 UTC,Buy,USDC,10,CAD,1.4,,,0.5,"
            "Bought 10 USDC\n"
            # Subtotal - fee on a sale
            "SYN-3,2025-02-05 16:00:00 UTC,Sell,USDC,-100,CAD,1.5,150,,1.5,"
            "Sold 100 USDC\n"), [
            ("CAD", 151.5, "USD", 100.0),
            ("CAD", 14.5, "USD", 10.0),
            ("USD", 100.0, "CAD", 148.5)])

    @rule("CA-FX-07")
    def test_same_currency_fee_is_a_cash_spend(self):
        """2026-10 audit #73: USDC bought or sold for USD is no
        conversion, but its fee is US dollars spent."""
        with tempfile.TemporaryDirectory() as tmp:
            evs = coinbase_cash_events(_write(
                tmp, "cb.csv", CB_HEADER
                + "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,USD,1,100,"
                  "105,5,Bought 100 USDC\n"
                + "SYN-2,2025-02-04 16:00:00 UTC,Sell,USDC,-50,USD,1,50,"
                  "48,2,Sold 50 USDC\n"
                + "SYN-3,2025-02-05 16:00:00 UTC,Buy,USDC,10,USD,1,10,"
                  "10,0,Bought 10 USDC\n"))
        self.assertEqual([e["kind"] for e in evs], ["FLOW", "FLOW"])
        self.assertEqual([(e["currency"], e["amount"]) for e in evs],
                         [("USD", -5.0), ("USD", -2.0)])

    def test_cash_events_refuse_an_underivable_total(self):
        with self.assertRaises(ValueError) as cm:
            self._cash("SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,,,,"
                       "0,Bought 100 USDC\n")
        msg = str(cm.exception)
        self.assertIn("cb.csv:2", msg)
        self.assertIn("Total cell is blank", msg)
        self.assertIn("Fill in the Total", msg)

    def test_a_given_total_is_kept(self):
        self.assertEqual(self._cash(
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,1.5,150,151.5,"
            "1.5,Bought 100 USDC\n"), [("CAD", 151.5, "USD", 100.0)])

    @rule("CA-CRYPTO-08")
    def test_pool_derives_a_blank_total(self):
        self.assertEqual(self._pool(
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,1.5,150,,1.5,"
            "Bought 100 USDC\n"
            "SYN-2,2025-02-04 16:00:00 UTC,Buy,USDC,10,CAD,1.4,,,0.5,"
            "Bought 10 USDC\n"), [(100.0, -151.5), (10.0, -14.5)])

    def test_pool_refuses_an_underivable_total(self):
        with self.assertRaises(ValueError) as cm:
            self._pool("SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,,,,"
                       "0,Bought 100 USDC\n")
        self.assertIn("cb.csv:2", str(cm.exception))

    def test_pool_usd_buy_needs_no_total(self):
        # A USD-priced buy has no CAD leg: its cost is the day's rate, so
        # a blank Total there is not refused.
        self.assertEqual(self._pool(
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,USD,,,,0,"
            "Bought 100 USDC\n"), [(100.0, 0.0)])


# ------------------------------------- 79: overlapping exports, read once

def _f(units, key):
    return {"units": units, "key": key}


class TestMergePoolFlows(unittest.TestCase):
    def _units(self, *files):
        return [f["units"] for f in cs.merge_pool_flows(list(files))]

    def test_a_repeated_entry_counts_once(self):
        a = [_f(100, ("kraken", "R1"))]
        b = [_f(100, ("kraken", "R1")), _f(50, ("kraken", "R2"))]
        self.assertEqual(self._units(a, b), [100, 50])

    def test_genuine_duplicates_in_one_file_both_count(self):
        k = ("coinbase", "row", ("same", "cells"))
        self.assertEqual(self._units([_f(5, k), _f(5, k)]), [5, 5])
        # ...and an overlapping copy of that file adds nothing,
        self.assertEqual(self._units([_f(5, k), _f(5, k)],
                                     [_f(5, k), _f(5, k)]), [5, 5])
        # while a later file with a third copy adds the third only.
        self.assertEqual(self._units([_f(5, k)], [_f(5, k), _f(5, k)]),
                         [5, 5])

    def test_a_flow_with_no_key_always_counts(self):
        self.assertEqual(self._units([_f(1, None)], [_f(1, None)]), [1, 1])


class TestKrakenRepeatedTxid(_UTC):
    def test_a_pasted_twice_entry_is_read_once(self):
        rows = [("R1", "2025-01-10 12:00:00", "spend", "", "ZCAD", -140, 0),
                ("R1", "2025-01-10 12:00:00", "receive", "", "USDC", 100, 0)]
        text = _kr(rows)
        text += text.split("\n", 1)[1]            # the rows again
        with tempfile.TemporaryDirectory() as tmp:
            flows = cs.kraken_pool_flows(_write(tmp, "ledgers.csv", text))
        self.assertEqual([(f["units"], f["cad"], f["key"]) for f in flows],
                         [(100.0, -140.0, ("kraken", "R1"))])

    def test_one_txid_with_different_content_is_refused(self):
        text = _kr([("R1", "2025-01-10 12:00:00", "receive", "", "USDC",
                     100, 0)])
        text += _kr([("R1", "2025-01-10 12:00:00", "receive", "", "USDC",
                      90, 0)]).split("\n", 1)[1]
        with tempfile.TemporaryDirectory() as tmp, \
                self.assertRaises(ValueError) as cm:
            cs.kraken_pool_flows(_write(tmp, "ledgers.csv", text))
        self.assertIn("DIFFERENT content", str(cm.exception))


_R1 = [("R1", "2025-01-10 12:00:00", "spend", "", "ZCAD", -140, 0),
       ("R1", "2025-01-10 12:00:00", "receive", "", "USDC", 100, 0)]
_R2 = [("R2", "2025-01-20 12:00:00", "spend", "", "ZCAD", -150, 0),
       ("R2", "2025-01-20 12:00:00", "receive", "", "USDC", 100, 0)]


class TestBuildReportOverlap(_UTC):
    """USDC 100 for CAD 140 (R1), then 100 for CAD 150 (R2); 100 sent
    on 02-01 at 1.40: ACB 100 x 290/200 = 145, 100 left. Counting R1
    twice gave 300 units at CAD 430: ACB 143.33, 200 left."""

    def _report(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root, cfg = _project(tmp, sidecar=SIDE)
            bf = [(broker, _write(tmp, name, text))
                  for broker, name, text in files]
            doc = cs.build_report(root, cfg, broker_files={"a": bf})
        sends = {s["id"]: s for s in doc["accounts"]["a"]["sends"]}
        return doc["pool"]["units_now"], sends[USDC_ID]["fx"]["acb"]

    @rule("CA-CRYPTO-08")
    def test_overlapping_kraken_ledgers_are_read_once(self):
        self.assertEqual(self._report([
            ("kraken", "ledgers_jan.csv", _kr(_R1)),
            ("kraken", "ledgers_all.csv", _kr(_R1 + _R2))]),
            (100.0, 145.0))

    @rule("CA-CRYPTO-08")
    def test_overlapping_coinbase_exports_are_read_once(self):
        r1 = ("c1,2025-01-10 12:00:00 UTC,Buy,USDC,100,CAD,1.4,140,140,0,"
              "Bought 100 USDC\n")
        r2 = ("c2,2025-01-20 12:00:00 UTC,Buy,USDC,100,CAD,1.5,150,150,0,"
              "Bought 100 USDC\n")
        self.assertEqual(self._report([
            ("coinbase", "cb_jan.csv", CB_HEADER + r1),
            ("coinbase", "cb_all.csv", CB_HEADER + r1 + r2)]),
            (100.0, 145.0))

    @rule("CA-CRYPTO-08")
    def test_distinct_entries_of_the_same_amount_both_count(self):
        # Two real 100-USDC buys for CAD 140 (distinct refids): 200 units
        # at CAD 280, ACB 140; a second file repeating them adds nothing.
        r1b = [(r[0].replace("R1", "R3"),) + r[1:] for r in _R1]
        self.assertEqual(self._report([
            ("kraken", "ledgers_a.csv", _kr(_R1 + r1b)),
            ("kraken", "ledgers_b.csv", _kr(_R1 + r1b))]),
            (100.0, 140.0))


if __name__ == "__main__":
    unittest.main()
