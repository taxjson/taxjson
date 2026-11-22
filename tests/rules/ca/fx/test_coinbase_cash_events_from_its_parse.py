"""Coinbase's cash events from its own parse (PLAN step 16 commit 8,
backlog TJ-4380 to TJ-4384): a cash cell the parse itself never reads that
cannot be read is the file's problem, the parse standing; the cash of a row
of a currency or a cash coin. Commit 8 added these classes to
test_coinbase_stablecoin_cash_legs, one of the files the split module
test_fix_1029_r7b_coinbase became, though they were never that module's;
commit 12 moved them to a module of their own (TJ-4389).

All rows, ids and amounts are synthetic.
"""
import os
import tempfile
import unittest
from pathlib import Path


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestACashCellTheParseNeverReads(unittest.TestCase):
    """PLAN step 16 commit 8 (TJ-4381): Coinbase's cash events come from its
    parse. A cash cell the parse itself never reads (a fiat Deposit's
    quantity or time: a non-event; a stablecoin Buy's Total: a conversion
    the parse counts) that cannot be read leaves the file no events and its
    problem, worded byte for byte as the cash reader worded it (`tjs cash`
    and `tjs fx-cash --ledger v2` show it: "its cash events could not be
    read (...)"); never a refused parse, never an empty list read as no
    cash. The expected texts are the reader's own, recorded at 9e6c7444."""

    _H = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
          "Price Currency,Price at Transaction,Subtotal,"
          "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n")
    _QTY = ("ValueError: unparseable quantity transacted 'abc' (Coinbase row "
            "'2025-02-03 16:00:00 UTC' 'Deposit') — refusing to book it as 0. "
            "Fix the cell or report the new export format.")
    CASES = (
        ("SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,,,,0,Bought 100 USDC\n",
         "ValueError: cb.csv:2: Coinbase row '2025-02-03 16:00:00 UTC' 'Buy' "
         "'USDC': the Total cell is blank and neither Subtotal nor quantity x "
         "price is there to derive it from — refusing to book $0 cost. Fill "
         "in the Total from the Coinbase statement."),
        ("SYN-1,2025-02-03 16:00:00 UTC,Deposit,CAD,abc,CAD,1,,,0,x\n", _QTY),
        ("SYN-1,03/02/2025,Deposit,CAD,100,CAD,1,,,0,x\n",
         "ValueError: Coinbase deposit row has an unparseable timestamp: "
         "'03/02/2025' (asset='CAD'). Add the format to parse_date if this is "
         "a new Coinbase export variant."),
        ("SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,1.5,150,zz,1.5,"
         "Bought 100 USDC\n",
         "ValueError: unparseable total 'zz' (Coinbase row "
         "'2025-02-03 16:00:00 UTC' 'Buy') — refusing to book it as 0. Fix "
         "the cell or report the new export format."),
        # The first such cell is the file's problem, the events before it
        # dropped with it, as the reader stopped there.
        ("SYN-0,2025-02-02 16:00:00 UTC,Deposit,CAD,100,CAD,1,100,100,0,x\n"
         "SYN-1,2025-02-03 16:00:00 UTC,Deposit,CAD,abc,CAD,1,,,0,x\n", _QTY),
    )

    def test_the_problem_is_the_readers_wording_and_the_parse_stands(self):
        from _support.parser_cash import parsed
        for rows, problem in self.CASES:
            with self.subTest(rows=rows[:40]), tempfile.TemporaryDirectory() as td:
                p = Path(td) / "cb.csv"
                p.write_text(self._H + rows)
                parser = parsed("coinbase", p)
                self.assertEqual((parser.cash_events, parser.cash_problem), ([], problem))

    def test_the_sidecar_holds_the_files_problem_beside_the_others_events(self):
        import json
        import subprocess
        import _paths
        from taxjson.lib.dispatch import python_module_argv
        with tempfile.TemporaryDirectory() as td:
            a, b, side = Path(td) / "a.csv", Path(td) / "b.csv", Path(td) / "m.cash"
            a.write_text(self._H + self.CASES[1][0])
            b.write_text(self._H + "SYN-2,2025-02-04 16:00:00 UTC,Deposit,CAD,100,"
                                   "CAD,1,100,100,0,x\n")
            r = subprocess.run(python_module_argv(
                "taxjson.bin.taxjson_brokerage",
                ["--brokerage", "coinbase", str(a), str(b), "--country", "canada",
                 "--account", "m", "--cash-out", str(side)]),
                cwd=_paths.REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0"), timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(side.read_text(encoding="utf-8"))
        got = {f["name"]: ([(e["kind"], e["amount"]) for e in f["events"]], f["problem"])
               for f in doc["files"]}
        self.assertEqual(got, {"a.csv": ([], self._QTY),
                               "b.csv": ([("CASHMOVE", 100.0)], "")})
        self.assertTrue(doc["metadata"]["stablecoins_as_cash"])

    def test_reading_the_cash_rows_again_moves_no_rows_instant(self):
        # The finish reads each cash row's time again: the instant the
        # parse records for its last row (taxjson-brokerage orders rows of
        # one second across exports by it) stays that row's own.
        import datetime
        from _support.parser_cash import parsed
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.csv"
            p.write_text(self._H
                         + "SYN-1,2025-02-01 10:00:00 UTC,Deposit,CAD,100,CAD,1,100,100,0,x\n"
                         + "SYN-2,2025-02-03 16:00:00 UTC,Buy,ZZE,1,USD,100,100,100,0,"
                           "Bought 1 ZZE\n")
            parser = parsed("coinbase", p)
        self.assertEqual(parser.instant_of_line, {3: datetime.datetime(2025, 2, 3, 16, 0)})
        self.assertEqual([e["kind"] for e in parser.cash_events], ["CASHMOVE"])

    def test_an_events_place_is_its_record_as_the_reader_counted(self):
        # TJ-4383: the record's number among the file's records, as the cash
        # reader counted them (csv records, not lines): after a preamble cell
        # spanning two lines, the Deposit on line 4 is record 3.
        from _support.parser_cash import parsed_cash_events
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.csv"
            p.write_text('"Transactions\nreport"\n' + self._H
                         + "SYN-1,2025-02-03 16:00:00 UTC,Deposit,CAD,100,CAD,1,100,100,0,x\n")
            evs = parsed_cash_events("coinbase", p)
        self.assertEqual([e["line_where"] for e in evs], ["cb.csv:3"])


class TestACurrencyRowsCash(unittest.TestCase):
    """PLAN step 16 commit 8 (TJ-4380): the cash of a row of a currency or
    a cash coin, as the parse builds it (the hand mutants a fiat row booked
    in US dollars, a stablecoin quote kept as a currency, a row of no
    quantity kept, each survived the tests before these)."""

    _H = TestACashCellTheParseNeverReads._H

    def _cash(self, rows):
        from _support.parser_cash import parsed_cash_events
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.csv"
            p.write_text(self._H + rows)
            evs = parsed_cash_events("coinbase", p)
        return [(e["kind"], e.get("currency") or (e["from_ccy"], e["to_ccy"]),
                 e.get("amount")) for e in evs]

    def test_a_fiat_row_moves_its_own_currency(self):
        self.assertEqual(self._cash(
            "SYN-1,2025-02-03 16:00:00 UTC,Deposit,CAD,100,CAD,1,100,100,0,x\n"
            "SYN-2,2025-02-04 16:00:00 UTC,Withdrawal,EUR,-50,EUR,1,50,50,0,x\n"),
            [("CASHMOVE", "CAD", 100.0), ("CASHMOVE", "EUR", -50.0)])

    def test_a_stablecoin_quote_is_us_dollars(self):
        # USDC bought for USDT: both US-dollar cash (CA-CRYPTO-02), so no
        # conversion, the fee alone a spend.
        self.assertEqual(self._cash(
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,USDT,1,100,101,1,"
            "Bought 100 USDC\n"), [("FLOW", "USD", -1.0)])

    def test_a_row_of_no_quantity_is_no_event(self):
        self.assertEqual(self._cash(
            "SYN-1,2025-02-03 16:00:00 UTC,Deposit,CAD,0,CAD,1,0,0,0,x\n"
            "SYN-2,2025-02-04 16:00:00 UTC,Deposit,CAD,100,CAD,1,100,100,0,x\n"),
            [("CASHMOVE", "CAD", 100.0)])

    def test_a_received_stablecoin_is_us_dollars_in(self):
        # Review of PLAN step 16 commit 8 (TJ-4384, c12): a Receive is an
        # inflow as a Deposit is; booked as an outflow, it survived.
        self.assertEqual(self._cash(
            "SYN-1,2025-02-03 16:00:00 UTC,Receive,USDC,40,USD,1,40,40,0,x\n"),
            [("CASHMOVE", "USD", 40.0)])

    def test_a_small_quantity_is_still_an_event(self):
        # TJ-4384, c11: only a row of no quantity (below 1e-9) is no event;
        # with the floor at half a cent, a 0.004 USD Deposit was dropped.
        self.assertEqual(self._cash(
            "SYN-1,2025-02-03 16:00:00 UTC,Deposit,USD,0.004,USD,1,,,0,x\n"),
            [("CASHMOVE", "USD", 0.004)])


if __name__ == "__main__":
    unittest.main()
