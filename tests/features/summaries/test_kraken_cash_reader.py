"""Batch D (the FX-on-cash ledger v2, the Kraken cash reader, futures):
2026-10 audit findings 71, 72, 78, 86, 91, 102, 103, the reader's events
from Kraken's own parse since PLAN step 16 commit 11 (its own pins:
test_kraken_cash_events_from_its_parse). All data synthetic.

Formerly: test_fix_batch_d_cash"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import timezones as TZ


def _kraken(text):
    """The Kraken parse's cash events of `text` as a ledger export, on Toronto
    time."""
    from _support.parser_cash import parsed_cash_events
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(TZ, "local_tz_name",
                         return_value="America/Toronto"):
        p = Path(tmp) / "kraken_demo.csv"
        p.write_text(text)
        return parsed_cash_events("kraken", p)


def _bals(evs):
    return {(e["date"], e["currency"]): e["balance"] for e in evs
            if e["kind"] == "CASHBAL"}


_HEAD = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"


class TestKrakenCashOrder(unittest.TestCase):
    """#71: a day's balance rows listed newest-first took the first row
    of the day as its end (CASHBAL 100, not 120).

    Formerly: test_fix_batch_d_cash.TestKrakenCashOrder"""

    def test_same_day_rows_newest_first(self):
        data = ("L2,R2,2025-02-03 16:00:00,deposit,,currency,ZUSD,20,0,"
                "120\n"
                "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,100,0,"
                "100\n")
        bals = _bals(_kraken(_HEAD + data))
        self.assertEqual(bals[("2024-12-31", "USD")], 0.0)
        self.assertEqual(bals[("2025-02-03", "USD")], 120.0)
        # Oldest-first: the same figures.
        rev = "".join(reversed(data.splitlines(True)))
        self.assertEqual(_bals(_kraken(_HEAD + rev)), bals)


class TestKrakenCashHeader(unittest.TestCase):
    """#72: an uppercased header (which the parser reads) gave no cash
    events; a missing column returned [] instead of refusing.

    Formerly: test_fix_batch_d_cash.TestKrakenCashHeader"""

    DATA = ("L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,100,0,100\n"
            "L2,R2,2025-03-03 15:00:00,withdrawal,,currency,ZUSD,-40,1,"
            "59\n")

    def test_uppercase_header_reads_the_same(self):
        lower = _kraken(_HEAD + self.DATA)
        upper = _kraken(_HEAD.upper() + self.DATA)
        self.assertTrue(lower)
        self.assertEqual(upper, lower)

    def test_missing_column_is_refused_naming_the_file(self):
        head = "txid,refid,time,type,subtype,aclass,asset,amount,balance\n"
        data = "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,100,100\n"
        with self.assertRaises(ValueError) as cm:
            _kraken(head + data)
        msg = str(cm.exception)
        self.assertIn("kraken_demo.csv", msg)
        self.assertIn("required column(s) missing: fee", msg)

    def test_trades_export_has_no_cash_events(self):
        head = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,"
                "vol\n")
        data = ("T1,O1,XBT/USD,2025-02-03 15:00:00,buy,market,100,100,0,"
                "1\n")
        self.assertEqual(_kraken(head + data), [])


class TestKrakenCashFeeCurrency(unittest.TestCase):
    """#78: a fee in another `feecurrency` was charged to the row's
    asset (FLOW USD -2, opening 1002 instead of EUR -2, opening 1000).

    Formerly: test_fix_batch_d_cash.TestKrakenCashFeeCurrency"""

    HEAD = ("txid,refid,time,type,subtype,aclass,asset,amount,fee,"
            "feecurrency,balance\n")

    def test_fiat_fee_in_another_currency(self):
        evs = _kraken(self.HEAD + "L1,R1,2025-02-03 15:00:00,withdrawal,,"
                      "currency,ZUSD,-100,2,ZEUR,900\n")
        flows = [(e["currency"], e["amount"]) for e in evs
                 if e["kind"] == "FLOW"]
        self.assertEqual(flows, [("EUR", -2.0)])
        self.assertEqual(_bals(evs)[("2024-12-31", "USD")], 1000.0)

    def test_coin_fee_is_not_a_cash_flow(self):
        evs = _kraken(self.HEAD + "L1,R1,2025-02-03 15:00:00,withdrawal,,"
                      "currency,ZUSD,-100,0.0001,XXBT,900\n")
        self.assertEqual([e for e in evs if e["kind"] == "FLOW"], [])
        self.assertEqual(_bals(evs)[("2024-12-31", "USD")], 1000.0)

    def test_fee_in_the_asset_itself(self):
        evs = _kraken(self.HEAD + "L1,R1,2025-02-03 15:00:00,withdrawal,,"
                      "currency,ZUSD,-100,2,ZUSD,898\n")
        flows = [(e["currency"], e["amount"]) for e in evs
                 if e["kind"] == "FLOW"]
        self.assertEqual(flows, [("USD", -2.0)])
        self.assertEqual(_bals(evs)[("2024-12-31", "USD")], 1000.0)


if __name__ == "__main__":
    unittest.main()
