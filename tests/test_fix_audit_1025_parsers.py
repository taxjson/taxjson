"""Fixes for the 2026-10-25 workflow audit, group parsers: the FX-rate
window scan (lib/rates_window), the income summer
(bin/taxjson_sum_income) and the crypto broker readers (Coinbase,
Kraken). One test per finding, each named for it. Synthetic data only."""
import dataclasses
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path
from unittest import mock

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tool(module, *args):
    """Run `taxjson.bin.<module>` as the CLI does (a child Python)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv(f"taxjson.bin.{module}", [str(a) for a in args]),
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=300)


class TestRatesWindow(unittest.TestCase):
    def test_diff_engine_1_a_data_cell_holding_date_is_not_a_header(self):
        """diff-engine#1: A38 regression: a data cell containing "date"
        (CONSOLIDATED, LIQUIDATED, UPDATE) is taken for a header row, so
        the FX window starts too late and `tjs run` stops with "no
        exchange rate". The header test matched "date" anywhere in a
        short cell, so a data row read as a header, its column replaced
        the Date column and every later compact date was skipped; and a
        column-scan hit turned off the scan of a compact-only file. A
        header names "date" as a word (TradeDate, Date/Time, TRADE_DATE)
        on a line with no number or date, cells are read as CSV, and a
        file with no date of another shape is scanned whole again."""
        from taxjson.lib.rates_window import earliest_in_text, window_start
        head = b"Date,Action,Symbol,Description,Quantity,Price,Amount,Currency\n"
        sell = b"20241105,SELL,ZZQ.US,ZZQ CORP,-10,30,300,USD\n"
        old = b"20230103,BUY,ZZQ.US,ZZQ CORP,10,20,-200,USD\n"
        for second in (
                (b"20240310,BUY,ZZE.US,CONSOLIDATED ZZE HOLDINGS,10,90,"
                 b"-900,USD\n"),
                b"20240310,Liquidated,ZZE.US,ZZE CORP,10,90,-900,USD\n",
                b"20240310,BUY,ZZE.US,UPDATE,10,90,-900,USD\n"):
            data = head + sell + second + old
            self.assertEqual(earliest_in_text(data, 2027), date(2023, 1, 3),
                             second)
        # The project: the window reaches the oldest row, as on v0.34.0.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "generic_bank.csv"
            p.write_bytes(head + sell + b"20240310,BUY,ZZE.US,CONSOLIDATED"
                          b" ZZE HOLDINGS,10,90,-900,USD\n" + old)
            self.assertEqual(window_start(2024, [p], today=date(2026, 10, 25)),
                             date(2022, 12, 20))
        # A mixed file (an IB Flex export with a WhenGenerated line): the
        # TradeDate column is read past a description holding "date", and
        # past a quoted description holding the delimiter.
        flex = (b"Statement,WhenGenerated,2026-01-02\n"
                b'"Symbol","Description","TradeDate","Qty"\n'
                b'"ZZE","CONSOLIDATED ZZE INC","20210104","1"\n'
                b'"ZZQ","ZZQ CORP, CL A","20190104","1"\n')
        self.assertEqual(earliest_in_text(flex, 2027), date(2019, 1, 4))
        # Header words still recognised; data rows never are.
        for h in (b"TradeDate", b"Date/Time", b"Settle Date",
                  b"SettleDateTarget", b"TRADE_DATE", b"Datetime"):
            data = (b"Note,2026-01-02\nSymbol," + h + b"\nZZQ,20190104\n")
            self.assertEqual(earliest_in_text(data, 2027), date(2019, 1, 4),
                             h)
        # An 8-digit id outside a date column of a mixed file stays an id.
        self.assertEqual(earliest_in_text(
            b"Date,OrderID\n2024-01-02,20010101\n", 2027), date(2024, 1, 2))
        self.assertEqual(earliest_in_text(
            b"Date,Description,OrderID\n2024-01-02,UPDATED,20010101\n",
            2027), date(2024, 1, 2))


class TestSumIncome(unittest.TestCase):
    def test_diff_tooling_3_null_amounts_are_refused_not_booked_as_zero(self):
        """diff-tooling#3: taxjson-sum-income books an income row with
        null amounts as $0 at exit 0 (the S051-14 'carry no amount'
        refusal is lost). load_income_data copied the funnel's
        normalized row over the file's, and for an explicit null
        gross_amount / net_amount that value is TaxTransaction's 0.0
        default. A null amount keeps the row's None, so the row is
        refused (exit 2) as on v0.34.0; a row with one amount null and
        the other given still sums the given one."""
        from taxjson.bin import taxjson_sum_income as si
        with tempfile.TemporaryDirectory() as td:
            for action, typ in (("DIVIDEND", "dividend"), ("TAX", "tax"),
                                ("INTEREST", "interest")):
                p = Path(td) / f"{action.lower()}.json"
                rows = [
                    {"action": "DIVIDEND", "type": "dividend",
                     "symbol": "ZZQX.US", "date": "2024-03-14",
                     "currency": "USD", "gross_amount": 50.0,
                     "net_amount": 42.5},
                    {"action": action, "type": typ, "symbol": "ZZQY.US",
                     "date": "2024-06-14", "currency": "USD",
                     "gross_amount": None, "net_amount": None}]
                p.write_text(json.dumps({"transactions": rows}))
                loaded = si.load_income_data(p)
                self.assertIsNone(loaded[1]["gross_amount"])
                self.assertIsNone(loaded[1]["net_amount"])
                r = _tool("taxjson_sum_income", p)
                self.assertEqual(r.returncode, 2, (action, r.stdout,
                                                   r.stderr))
                self.assertIn("1 income row(s) carry no amount", r.stderr)
                self.assertIn(f"{action} ZZQY.US 2024-06-14", r.stderr)
                self.assertNotIn("TOTAL NET INCOME", r.stdout)
            # One amount null, the other given: the given one is summed.
            p = Path(td) / "partial.json"
            p.write_text(json.dumps({"transactions": [
                {"action": "DIVIDEND", "type": "dividend",
                 "symbol": "ZZQY.US", "date": "2024-06-14",
                 "currency": "USD", "gross_amount": None,
                 "net_amount": 42.5}]}))
            r = _tool("taxjson_sum_income", p)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("42.50", r.stdout)


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
    from taxjson.lib.core import TaxTransaction
    fields = {f.name for f in dataclasses.fields(TaxTransaction)}
    return deduplicate([TaxTransaction(**{**{k: v for k, v in r.items()
                                             if k in fields},
                                          "source": name})
                        for r in rows])


class TestCryptoRowIds(unittest.TestCase):
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
