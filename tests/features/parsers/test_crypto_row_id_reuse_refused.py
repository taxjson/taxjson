"""Fixes for the 2026-10-25 workflow audit, group parsers: the FX-rate
window scan (lib/rates_window), the income summer
(bin/taxjson_sum_income) and the crypto broker readers (Coinbase,
Kraken). One test per finding, each named for it. Synthetic data only.

Formerly: test_fix_audit_1025_parsers"""
import dataclasses
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import _paths

REPO_ROOT = _paths.REPO


_CB_H = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
         "Price Currency,Price at Transaction,Subtotal,"
         "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")
_CB_1 = "CB-Z-1,2024-01-18 16:24:11 UTC,Buy,ZZC,2,USD,40.00,80.00,81.00,1.00,\n"
_CB_2 = ("CB-Z-1,2024-01-19 16:24:11 UTC,Buy,ZZC,3,USD,50.00,150.00,151.00,"
         "1.00,\n")
_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KT_1 = "T1-ZZ,O1,ZZC/USD,2024-02-10 14:25:18,buy,limit,40,80,0.5,2,0,,\n"
_KT_2 = "T1-ZZ,O2,ZZC/USD,2024-03-11 10:00:00,buy,limit,60,180,0.5,3,0,,\n"


def _parse_one(parser, name, text):
    """Parse one export, alone in a folder of its own (the Kraken trades
    reader looks for a ledgers export beside it)."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text)
        with redirect_stderr(io.StringIO()):
            return parser().parse_file(p)


def _kept(rows, name):
    """The rows taxjson-sort --dedup keeps of one file's parse."""
    from taxjson.bin.taxjson_sort import deduplicate
    from taxjson.model.transaction import TaxTransaction
    fields = {f.name for f in dataclasses.fields(TaxTransaction)}
    return deduplicate([TaxTransaction(**{**{k: v for k, v in r.items()
                                             if k in fields},
                                          "source": name})
                        for r in rows])


class TestCryptoRowIds(unittest.TestCase):
    """Formerly: test_fix_audit_1025_parsers.TestCryptoRowIds"""
    def test_robustness_4_one_broker_id_on_two_different_rows_is_refused(self):
        """robustness#4: Two different rows sharing one broker id in a
        file (Coinbase ID, Kraken txid) keep only the first: the other
        trade is dropped from the books with no message. The parsers
        keep the broker's id as the row id, and the dedup reads one id
        twice in one file as the file repeating itself. A second row
        with the same id and DIFFERENT content is refused now, naming
        the file, both lines and the (masked) id; an identical repeat
        is still booked once, and an ordinary export still parses."""
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        with mock.patch.dict(os.environ,
                             {"TAXJSON_LOCAL_TZ": "America/Toronto"}):
            for parser, name, head, a, b, shown in (
                    (CoinbaseBrokerage, "cb.csv", _CB_H, _CB_1, _CB_2,
                     "ID CB***"),
                    (KrakenBrokerage, "kraken_trades.csv", _KT_H, _KT_1,
                     _KT_2, "txid T1***")):
                with self.assertRaises(ValueError) as cm:
                    _parse_one(parser, name, head + a + b)
                msg = str(cm.exception)
                self.assertIn(f"{name} lines 2 and 3", msg)
                self.assertIn(shown, msg)
                self.assertIn("DIFFERENT content", msg)
                self.assertIn("2024-0", msg)        # both rows described
                self.assertNotIn("CB-Z-1", msg)     # the id is masked
                self.assertNotIn("T1-ZZ", msg)
                # The same row twice (two downloads pasted together): one
                # trade, booked once.
                rows = _parse_one(parser, name, head + a + a)
                self.assertEqual(len(_kept(rows, name)), 1, name)
                # Two rows, two ids: two trades.
                rows = _parse_one(parser, name,
                                  head + a + b.replace("Z-1", "Z-2", 1)
                                  .replace("T1-ZZ", "T2-ZZ", 1))
                self.assertEqual(len(_kept(rows, name)), 2, name)
            # The shipped demo exports still parse.
            for parser, demo in ((CoinbaseBrokerage, "coinbase_demo.csv"),
                                 (KrakenBrokerage, "kraken_demo.csv")):
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / demo
                    shutil.copyfile(REPO_ROOT / "examples" / demo, p)
                    with redirect_stderr(io.StringIO()):
                        self.assertTrue(parser().parse_file(p), demo)


if __name__ == "__main__":
    unittest.main()
