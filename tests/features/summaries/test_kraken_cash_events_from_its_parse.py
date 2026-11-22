"""Kraken's cash events from its own parse (PLAN step 16 commit 11,
backlog TJ-4460 to TJ-4462): classify notes every ledger row and the
finish builds the events, the account's other ledger exports read beside
it through the run's sibling finder. Commit 11 put its class in
test_kraken_cash_reader, one of the files the split module
test_fix_batch_d_cash became, though it was never that module's; commit
11b moved it here with the review's pins (TJ-4462). All data synthetic.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import timezones as TZ


def _bals(evs):
    return {(e["date"], e["currency"]): e["balance"] for e in evs
            if e["kind"] == "CASHBAL"}


class TestKrakenCashFromItsParse(unittest.TestCase):
    """PLAN step 16 commit 11: Kraken's cash events come from its own parse
    (classify notes every ledger row, the finish builds them), the account's
    other ledger exports read beside it through the run's sibling finder;
    lib/cash_readers/kraken.py, the last reader, went. A cell the events
    cannot read is the file's cash problem in the reader's words (recorded
    at 474f61b0), never a refused parse. The stablecoin note's two moves
    (backlog TJ-4461) are pinned as they now behave."""

    H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance,"
         "amountusd,feeusd,feecurrency\n")
    NOTE = "USDC is treated as a US-dollar stablecoin"

    def _dir(self, files):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        for name, rows in files.items():
            (d / name).write_text(self.H + rows)
        return d

    def _parsed(self, path, finder=True, country="canada"):
        from _support.parser_cash import parsed
        with patch.object(TZ, "local_tz_name", return_value="America/Toronto"):
            if finder:
                return parsed("kraken", path, country)
            from taxjson.parsers.registry import parser_class
            from taxjson.parsers.settings import ParseSettings
            parser = parser_class("kraken")()
            parser.configure(ParseSettings.for_country(country))
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_file(path)
            return parser

    def _tool(self, *paths, cash_out):
        from taxjson.lib.dispatch import python_module_argv
        return subprocess.run(python_module_argv(
            "taxjson.bin.taxjson_brokerage",
            ["--brokerage", "kraken", *map(str, paths), "--country", "canada",
             "--account", "crypto", "--cash-out", str(cash_out)]),
            cwd=Path(paths[0]).parent, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=600,
            env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                     TAXJSON_LOCAL_TZ="America/Toronto"))

    def test_each_refusal_is_the_files_problem_in_the_readers_words(self):
        from _support.parser_cash import parsed_cash_events
        # The texts lib/cash_readers/kraken.py raised (474f61b0): the file's
        # events none and its cash problem, the parse passing.
        three = self._dir({"kraken_ledgers.csv":
            "L1,RT1,2025-02-03 15:00:00,trade,tradespot,currency,ZUSD,spot / main,-100,0,0,-100,0,USD\n"
            "L2,RT1,2025-02-03 15:00:00,trade,tradespot,currency,ZEUR,spot / main,50,0,50,54,0,EUR\n"
            "L3,RT1,2025-02-03 15:00:00,trade,tradespot,currency,ZCAD,spot / main,60,0,60,44,0,CAD\n"})
        badbal = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,1x0,100,0,USD\n"})
        for d, said in (
                (three, "ValueError: Kraken ledger kraken_ledgers.csv: refid RT*** (2025-02-03) "
                        "trades 3 currencies at once (+60.00 CAD, +50.00 EUR, -100.00 USD) — the "
                        "FX-on-cash ledger books one currency for another. Enter it as FXCONV .tt "
                        "lines and remove its rows from the export."),
                (badbal, "ValueError: unparseable balance '1x0' (Kraken kraken_ledgers.csv:2) — "
                         "refusing to book it as 0. Fix the cell or report the new export format.")):
            with self.subTest(said[:40]):
                parser = self._parsed(d / "kraken_ledgers.csv")
                self.assertEqual((parser.cash_events, parser.cash_problem), ([], said))
                with self.assertRaises(ValueError) as cm,                         patch.object(TZ, "local_tz_name", return_value="America/Toronto"):
                    parsed_cash_events("kraken", d / "kraken_ledgers.csv")
                self.assertEqual(str(cm.exception), said)
        # The tool writes it as the file's problem and exits 0.
        r = self._tool(three / "kraken_ledgers.csv", cash_out=three / "a.cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        (entry,) = json.loads((three / "a.cash").read_text(encoding="utf-8"))["files"]
        self.assertEqual((entry["events"], entry["problem"][:40]),
                         ([], "ValueError: Kraken ledger kraken_ledgers"))

    def test_a_sibling_ledger_is_read_through_the_runs_finder(self):
        # A conversion two exports split at midnight UTC (backlog TJ-0861):
        # its USD leg is in the other export, which the parse reads beside
        # it only through `sibling_finder` (the run's detection, as
        # taxjson-brokerage sets it); a bare parse sees no sibling.
        d = self._dir({
            "kraken_ledgers_2024.csv":
                "L1,R0,2024-12-01 15:00:00,deposit,,currency,ZCAD,spot / main,1400,0,1400,1000,0,CAD\n"
                "L2,RT9,2024-12-31 23:59:59,trade,tradespot,currency,ZCAD,spot / main,-1400,0,0,-1000,0,CAD\n",
            "kraken_ledgers_2025.csv":
                "L3,RT9,2025-01-01 00:00:00,trade,tradespot,currency,ZUSD,spot / main,1000,0,1000,1000,0,USD\n"})
        def kinds(evs):
            return [(e["kind"], e.get("desc", "")) for e in evs if e["kind"] != "CASHBAL"]
        self.assertEqual(kinds(self._parsed(d / "kraken_ledgers_2024.csv").cash_events),
                         [("CASHMOVE", "Kraken deposit CAD"), ("FXCONV", "Kraken CAD->USD")])
        self.assertEqual(kinds(self._parsed(d / "kraken_ledgers_2024.csv", finder=False).cash_events),
                         [("CASHMOVE", "Kraken deposit CAD"), ("FLOW", "Kraken CAD spread")])

    def test_the_events_read_no_bonded_staking_fold(self):
        # The parse folds `<COIN><dd>.S` into a coin its export holds; the
        # cash events read the codes as the reader read them, after the
        # parse (`_KNOWN_ASSETS` None): ZUSD28.S is no US dollar there.
        d = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,100,0,USD\n"
            "L2,R2,2025-03-03 15:00:00,staking,,currency,ZUSD28.S,spot / main,1,0,1,1,0,USD\n"})
        self.assertEqual(_bals(self._parsed(d / "kraken_ledgers.csv").cash_events),
                         {("2024-12-31", "USD"): 0.0, ("2025-02-03", "USD"): 100.0})

    @staticmethod
    def _rows(evs):
        return [(e["kind"], e["date"], e.get("currency") or f"{e['from_ccy']}->{e['to_ccy']}",
                 e.get("amount", e.get("balance", e.get("from_amt"))), e["where"]) for e in evs]

    def test_a_conversion_is_the_same_event_from_each_export_holding_a_leg(self):
        # One refid's CAD paid in two exports, its USD received a day
        # later: the FXCONV is dated by the receipt, from the first leg in
        # (file, line) order, whichever export is parsed (each export
        # holding a leg gives it; cash_events.dedup_across_files keeps one).
        d = self._dir({
            "kraken_ledgers_2024.csv":
                "L1,R0,2024-12-01 15:00:00,deposit,,currency,ZCAD,spot / main,2000,0,2000,1460,0,CAD\n"
                "L2,RT9,2024-12-30 15:00:00,trade,tradespot,currency,ZCAD,spot / main,-700,0,1300,-511,0,CAD\n",
            "kraken_ledgers_2025.csv":
                "L3,RT9,2025-01-02 15:00:00,trade,tradespot,currency,ZCAD,spot / main,-700,0,600,-511,0,CAD\n"
                "L4,RT9,2025-01-03 15:00:00,trade,tradespot,currency,ZUSD,spot / main,1000,0,1000,1000,0,USD\n"})
        conv = ("FXCONV", "2025-01-03", "CAD->USD", 1400.0, "kraken_ledgers_2024.csv:3")
        for name in ("kraken_ledgers_2024.csv", "kraken_ledgers_2025.csv"):
            with self.subTest(name):
                self.assertEqual([r for r in self._rows(self._parsed(d / name).cash_events)
                                  if r[0] == "FXCONV"], [conv])

    def test_the_property_basis_reads_no_stablecoin_as_cash(self):
        # A Canadian book's USDC is US-dollar cash; a US book's is property.
        d = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,100,0,USD\n"
            "L2,R2,2025-02-04 15:00:00,deposit,,currency,USDC,spot / main,50,0,50,50,0,USDC\n"})
        moves = {c: [(r[1], r[3]) for r in self._rows(self._parsed(d / "kraken_ledgers.csv",
                                                                     country=c).cash_events)
                     if r[0] == "CASHMOVE"]
                 for c in ("canada", "usa")}
        self.assertEqual(moves, {"canada": [("2025-02-03", 100.0), ("2025-02-04", 50.0)],
                                 "usa": [("2025-02-03", 100.0)]})

    def test_a_repeat_a_fraction_of_a_second_and_a_reward(self):
        # A row listed twice is read once; a time with fractions of a second
        # is read to the second; a reward paid in the currency is no move
        # (the position book's income), only the balance's.
        d = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,100,0,USD\n"
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,100,0,USD\n"
            "L2,R2,2025-02-04 15:00:00.5,deposit,,currency,ZUSD,spot / main,5,0,105,5,0,USD\n"
            "L3,R3,2025-02-05 15:00:00,staking,,currency,ZUSD,spot / main,1,0,106,1,0,USD\n"})
        self.assertEqual([r[:4] for r in self._rows(self._parsed(d / "kraken_ledgers.csv").cash_events)],
                         [("CASHMOVE", "2025-02-03", "USD", 100.0),
                          ("CASHMOVE", "2025-02-04", "USD", 5.0),
                          ("CASHBAL", "2024-12-31", "USD", 0.0),
                          ("CASHBAL", "2025-02-05", "USD", 106.0)])

    def test_a_sibling_the_events_cannot_read_is_left_to_its_own_parse(self):
        # Another ledger export with a time it cannot read, or a column
        # missing: its own parse refuses it; this export's events are as if
        # it were alone, with no problem.
        alone = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,100,0,USD\n"})
        d = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,100,0,USD\n",
            "kraken_ledgers_badtime.csv":
            "L7,R7,soon,deposit,,currency,ZUSD,spot / main,5,0,5,5,0,USD\n"})
        (d / "kraken_ledgers_nofee.csv").write_text(
            "txid,refid,time,type,subtype,aclass,asset,wallet,amount,balance\n"
            "L8,R8,2025-01-03 15:00:00,deposit,,currency,ZUSD,spot / main,9,9\n")
        want = self._parsed(alone / "kraken_ledgers.csv")
        got = self._parsed(d / "kraken_ledgers.csv")
        self.assertEqual((got.cash_events, got.cash_problem), (want.cash_events, ""))

    def test_a_stablecoin_only_a_sibling_holds_is_noted_by_its_own_parse(self):
        # Backlog TJ-4461 (1), as it now behaves: an export parsed alone says
        # nothing of a stablecoin only another ledger export holds (the
        # reader said it after the summary); with that export in the same
        # run (as `tjs run` parses an account's exports), its parse says it
        # once, before the summary lines, as before.
        d = self._dir({
            "kr_ledgers_2024.csv":
                "L1,R1,2024-03-10 15:00:00,deposit,,currency,ZUSD,spot / main,900,0,900,900,0,USD\n",
            "kr_ledgers_2025.csv":
                "L2,R2,2025-03-10 15:00:00,deposit,,currency,USDC,spot / main,500,0,500,500,0,USDC\n"})
        r = self._tool(d / "kr_ledgers_2024.csv", cash_out=d / "one.cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(self.NOTE, r.stderr)
        r = self._tool(d / "kr_ledgers_2024.csv", d / "kr_ledgers_2025.csv", cash_out=d / "two.cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stderr.splitlines()
        said = [i for i, ln in enumerate(lines) if self.NOTE in ln]
        summary = [i for i, ln in enumerate(lines) if ln.startswith("  kr_ledgers_2024.csv: ")]
        self.assertEqual(len(said), 1, r.stderr)
        self.assertLess(said[0], summary[0], r.stderr)

    def test_an_own_rows_stablecoin_fee_currency_is_noted_by_the_finish(self):
        # Backlog TJ-4461 (2), as it now behaves: a trade row's feecurrency
        # only the cash events read (USDC here) is noted when the finish
        # builds them, before the file's summary line (the reader said it
        # after).
        d = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-01 15:00:00,deposit,,currency,ZUSD,spot / main,500,0,500,500,0,USD\n"
            "L2,RT2,2025-02-03 15:00:00,trade,tradespot,currency,ZUSD,spot / main,-100,0.5,399.5,-100,0.5,USDC\n"
            "L3,RT2,2025-02-03 15:00:00,trade,tradespot,currency,ZEUR,spot / main,92,0,92,100,0,EUR\n"})
        r = self._tool(d / "kraken_ledgers.csv", cash_out=d / "a.cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stderr.splitlines()
        said = [i for i, ln in enumerate(lines) if self.NOTE in ln]
        summary = [i for i, ln in enumerate(lines) if ln.startswith("  kraken_ledgers.csv: ")]
        self.assertEqual(len(said), 1, r.stderr)
        self.assertLess(said[0], summary[0], r.stderr)
        (entry,) = json.loads((d / "a.cash").read_text(encoding="utf-8"))["files"]
        self.assertIn(("FLOW", "USD", -0.5),
                      [(e["kind"], e.get("currency"), e.get("amount")) for e in entry["events"]])


class TestKrakenCashAcrossExports(unittest.TestCase):
    """The review of PLAN step 16 commit 11 (TJ-4462): each rule of the
    moved code no test held, as the parse answers it now (each shown to
    fail under the review's mutant of it: k03 to k42)."""

    H = TestKrakenCashFromItsParse.H
    _dir = TestKrakenCashFromItsParse._dir
    _parsed = TestKrakenCashFromItsParse._parsed

    @staticmethod
    def _events(evs):
        return [(e["kind"], e["date"],
                 e.get("currency") or f"{e['from_ccy']}->{e['to_ccy']}",
                 e.get("amount", e.get("balance", e.get("from_amt"))),
                 e.get("to_amt"), e["where"], e.get("desc"))
                for e in evs if e["kind"] != "CASHBAL"]

    def test_a_leg_two_sibling_exports_hold_joins_once(self):
        # k04, k27, k03: the USD leg is in two overlapping sibling exports;
        # it joins the CAD leg once (1000 USD, not 2000), its USD fee a
        # FLOW charged on the conversion, named by the first sibling in
        # name order.
        leg = "LB-1,RT1,2024-06-10 15:00:00,trade,,currency,ZUSD,spot / main,1000.00,2.00,998.00,0,0,USD\n"
        d = self._dir({
            "kraken_ledgers_a.csv":
                "LA-1,RT1,2024-06-10 15:00:00,trade,,currency,ZCAD,spot / main,-1300.00,0,700.00,0,0,CAD\n",
            "kraken_ledgers_b.csv": leg, "kraken_ledgers_c.csv": leg})
        evs = self._parsed(d / "kraken_ledgers_a.csv").cash_events
        self.assertEqual(self._events(evs), [
            ("FLOW", "2024-06-10", "USD", -2.0, None, "kraken_ledgers_b.csv:2",
             "Kraken conversion fee"),
            ("FXCONV", "2024-06-10", "CAD->USD", 1300.0, 1000.0, "kraken_ledgers_a.csv:2",
             "Kraken CAD->USD")])
        self.assertEqual([e.get("_fee_of") for e in evs if e["kind"] == "FLOW"],
                         ["kraken_ledgers_a.csv:2"])

    def test_a_siblings_stablecoin_leg_is_a_coin_at_the_property_basis(self):
        # k28: in a Canadian book a sibling's USDC leg is US-dollar cash, so
        # CAD for it is a conversion; in a US book it is property, the
        # position book's fill, so no conversion.
        d = self._dir({
            "kraken_ledgers_a.csv":
                "LA-1,RT2,2025-04-01 15:00:00,trade,,currency,ZCAD,spot / main,-136.00,0,564.00,0,0,CAD\n",
            "kraken_ledgers_b.csv":
                "LB-1,RT2,2025-04-01 15:00:00,trade,,currency,USDC,spot / main,100.00,0,100.00,0,0,USDC\n"})
        got = {c: self._events(self._parsed(d / "kraken_ledgers_a.csv", country=c).cash_events)
               for c in ("canada", "usa")}
        self.assertEqual(got, {
            "canada": [("FXCONV", "2025-04-01", "CAD->USD", 136.0, 100.0,
                        "kraken_ledgers_a.csv:2", "Kraken CAD->USDC")],
            "usa": []})

    def test_moves_and_the_years_an_export_spans(self):
        # k35, k38, k42: an export of 2024 and 2025 opens at the end of 2023
        # and closes each year (the last at its last day); a deposit of 0 is
        # no move; a USDC deposit is US dollars named as USDC.
        d = self._dir({"kraken_ledgers.csv":
            "L1,R1,2024-06-10 15:00:00,deposit,,currency,ZCAD,spot / main,2000,0,2000,0,0,CAD\n"
            "L2,R2,2025-03-01 15:00:00,deposit,,currency,ZUSD,spot / main,0,0,0,0,0,USD\n"
            "L3,R3,2025-03-02 15:00:00,deposit,,currency,USDC,spot / main,50,0,50,0,0,USDC\n"})
        evs = self._parsed(d / "kraken_ledgers.csv").cash_events
        self.assertEqual([(k, day, cur, amt, desc) for k, day, cur, amt, _t, _w, desc
                          in self._events(evs)],
                         [("CASHMOVE", "2024-06-10", "CAD", 2000.0, "Kraken deposit CAD"),
                          ("CASHMOVE", "2025-03-02", "USD", 50.0, "Kraken deposit USDC")])
        self.assertEqual(_bals(evs), {
            ("2023-12-31", "CAD"): 0.0, ("2023-12-31", "USD"): 0.0,
            ("2024-12-31", "CAD"): 2000.0, ("2024-12-31", "USD"): 0.0,
            ("2025-03-02", "CAD"): 2000.0, ("2025-03-02", "USD"): 50.0})

    def test_a_wallet_another_export_moves_first_has_no_opening(self):
        # k07, k25: the June export's USDC (US dollars here) was moved by the
        # January export, so the June export says no US-dollar balance at
        # the year's start, for any of its wallets (the January one gives
        # it); its own closing balance sums its wallets.
        d = self._dir({
            "kraken_ledgers_a.csv":
                "LA-1,R1,2025-01-05 15:00:00,deposit,,currency,USDC,spot / main,30,0,30,0,0,USDC\n",
            "kraken_ledgers_b.csv":
                "LB-1,R2,2025-06-01 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,0,0,USD\n"
                "LB-2,R3,2025-06-02 15:00:00,deposit,,currency,USDC,spot / main,50,0,80,0,0,USDC\n"})
        self.assertEqual(_bals(self._parsed(d / "kraken_ledgers_b.csv").cash_events),
                         {("2025-06-02", "USD"): 180.0})

    def test_a_wallet_is_its_asset_and_its_suffix(self):
        # k07b: another export moving USD.HOLD says nothing of the spot ZUSD
        # wallet's balance at the year's start.
        d = self._dir({
            "kraken_ledgers_a.csv":
                "LA-1,R1,2025-01-05 15:00:00,deposit,,currency,USD.HOLD,spot / main,30,0,30,0,0,USD\n",
            "kraken_ledgers_b.csv":
                "LB-1,R2,2025-06-01 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,100,0,0,USD\n"})
        self.assertEqual(_bals(self._parsed(d / "kraken_ledgers_b.csv").cash_events),
                         {("2024-12-31", "USD"): 0.0, ("2025-06-01", "USD"): 100.0})

    def test_a_trade_row_with_no_refid_is_a_trade_of_its_own(self):
        # k11: two trade rows without a refid are two trades (each a
        # one-currency spread), never one conversion.
        d = self._dir({"kraken_ledgers.csv":
            "L1,,2025-02-03 15:00:00,trade,,currency,ZUSD,spot / main,-100,0,0,0,0,USD\n"
            "L2,,2025-02-03 15:00:00,trade,,currency,ZEUR,spot / main,92,0,92,0,0,EUR\n"})
        self.assertEqual([(k, cur, amt, desc) for k, _d, cur, amt, _t, _w, desc in
                          self._events(self._parsed(d / "kraken_ledgers.csv").cash_events)],
                         [("FLOW", "USD", -100.0, "Kraken USD spread"),
                          ("FLOW", "EUR", 92.0, "Kraken EUR spread")])

    def test_only_a_siblings_trade_legs_join_a_trade(self):
        # k12c: a sibling's deposit that shares the trade's refid is no leg.
        d = self._dir({
            "kraken_ledgers_a.csv":
                "LA-1,RT1,2025-01-01 15:00:00,trade,,currency,ZCAD,spot / main,-1400,0,0,0,0,CAD\n",
            "kraken_ledgers_b.csv":
                "LB-1,RT1,2025-01-01 15:00:00,trade,,currency,ZUSD,spot / main,1000,0,1000,0,0,USD\n"
                "LB-2,RT1,2025-01-02 15:00:00,deposit,,currency,ZUSD,spot / main,50,0,1050,0,0,USD\n"})
        self.assertEqual([e[:5] for e in self._events(
            self._parsed(d / "kraken_ledgers_a.csv").cash_events)],
            [("FXCONV", "2025-01-01", "CAD->USD", 1400.0, 1000.0)])

    def test_a_conversion_is_dated_by_its_last_receipt(self):
        # k13: a currency received in two legs on two days: the conversion
        # is on the later.
        d = self._dir({"kraken_ledgers.csv":
            "L1,RT1,2025-01-01 15:00:00,trade,,currency,ZCAD,spot / main,-1400,0,0,0,0,CAD\n"
            "L2,RT1,2025-01-02 15:00:00,trade,,currency,ZUSD,spot / main,600,0,600,0,0,USD\n"
            "L3,RT1,2025-01-03 15:00:00,trade,,currency,ZUSD,spot / main,400,0,1000,0,0,USD\n"})
        self.assertEqual([e[:5] for e in self._events(
            self._parsed(d / "kraken_ledgers.csv").cash_events)],
            [("FXCONV", "2025-01-03", "CAD->USD", 1400.0, 1000.0)])

    def test_a_balance_is_rounded_to_its_units(self):
        # k23: two wallets of US dollars, 0.1 and 0.2: a balance of 0.3,
        # never 0.30000000000000004.
        d = self._dir({"kraken_ledgers.csv":
            "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,spot / main,0.1,0,0.1,0,0,USD\n"
            "L2,R2,2025-02-03 15:00:00,deposit,,currency,USDC,spot / main,0.2,0,0.2,0,0,USDC\n"})
        bals = _bals(self._parsed(d / "kraken_ledgers.csv").cash_events)
        self.assertEqual(repr(bals[("2025-02-03", "USD")]), "0.3")


if __name__ == "__main__":
    unittest.main()
