"""Fixes for the 2026-10-25 workflow audit, group parsers: the FX-rate
window scan (lib/rates_window), the income summer
(bin/taxjson_sum_income) and the crypto broker readers (Coinbase,
Kraken). One test per finding, each named for it. Synthetic data only.

Formerly: test_fix_audit_1025_parsers"""
import tempfile
import unittest
from datetime import date
from pathlib import Path


class TestRatesWindow(unittest.TestCase):
    """Formerly: test_fix_audit_1025_parsers.TestRatesWindow"""
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


if __name__ == "__main__":
    unittest.main()
