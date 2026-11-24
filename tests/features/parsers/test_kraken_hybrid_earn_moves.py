"""Kraken Hybrid Earn rows (backlog TJ-4951, TJ-4952, TJ-4954): a
`hybridearnwithdrawal` takes an asset out of the spot balance into the
product and a `hybridearndeposit` brings it back, with no row of another
wallet for what is parked. Both directions are a move within Kraken:

- the cash events (the FX-on-cash ledger v2's) have a CASHMOVE for
  neither (a PARKED event, no move), and the ledger balances add what is
  parked as of the balance row used, so cash folded into the book (a fiat
  currency; in a Canadian project a USD stablecoin) neither leaves it nor
  comes back twice; a `.tt` CASHMOVE line written for such a row when it
  was read as a cash move is refused by name (TJ-4951);
- a coin's round trip leaves its position and cost as they were: the
  withdrawal is custody evidence kept out of the books, the deposit a
  non-event (TJ-4952, pinned as it already behaved);
- what a row brings back beyond what the account's rows had parked is
  not booked as income or as coins acquired, and said: an ATTENTION line
  naming the coin and the excess, and for cash an undeclared CASHMOVE of
  the excess, which the ledger asks about (TJ-4954). The rows of one
  second are walked in the order their balances give, every row of the
  wallet chained from the balance before that second or to the one after
  it, where that chain is the order the export lists them in or both
  balances anchor it; otherwise the file's order is kept (the export's
  lines, from the last up where it lists newest first), and where the
  order matters a second ATTENTION line says so.

All data synthetic.
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

H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance,"
     "amountusd,feeusd,feecurrency\n")
OVER = "Hybrid Earn brought back"
ORDER = "share one second"
# What an excess of cash leaves, by case (the ATTENTION line's end): a
# currency the FX-cash ledger follows (the parse is not told [settings]
# fx_cash_inflow_cost, so it says both readings), the project's own.
FOREIGN = ("{0} is cash in the books, so no coin is missing; `tjs fx-cash --ledger v2` "
           "takes the excess as cash that came in: at the day's rate when [settings] "
           "fx_cash_inflow_cost = \"spot\", otherwise undeclared, with no figure until "
           "a .tt `CASHMOVE` line says what it was.")
OWN = ("{0} is the project's own currency: no coin is missing, and `tjs fx-cash` "
       "computes no gain or loss on it.")


def _parse(path, country="canada", finder=True):
    """(the parser after its parse of `path`, its rows, its notes), the
    export's siblings found as taxjson-brokerage finds them."""
    from taxjson.lib.brokerages.detect import same_broker_siblings
    from taxjson.parsers.registry import parser_class
    from taxjson.parsers.settings import ParseSettings
    parser = parser_class("kraken")()
    parser.configure(ParseSettings.for_country(country))
    if finder:
        parser.sibling_finder = same_broker_siblings
    err = io.StringIO()
    with patch.object(TZ, "local_tz_name", return_value="America/Toronto"), \
            contextlib.redirect_stderr(err):
        txs = parser.parse_file(Path(path))
    assert parser.cash_problem in (None, ""), parser.cash_problem
    return parser, txs, err.getvalue()


def _parked(parser):
    return [(e["date"], e["currency"], e["amount"], e["line_where"])
            for e in parser.cash_events if e["kind"] == "PARKED"]


def _moves(parser):
    return [(e["date"], e["currency"], e["amount"], e["desc"])
            for e in parser.cash_events if e["kind"] == "CASHMOVE"]


def _bals(parser):
    return {(e["date"], e["currency"]): e["balance"]
            for e in parser.cash_events if e["kind"] == "CASHBAL"}


class _Dir(unittest.TestCase):
    def _dir(self, files):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        for name, rows in files.items():
            (d / name).write_text(H + rows, encoding="utf-8")
        return d


class TestHybridEarnCashStaysInTheBook(_Dir):
    """TJ-4951: neither direction is a CASHMOVE; the balances add what is
    parked."""

    # 2000 USDC held; 1000 parked over the year end, back in January.
    ROWS = (
        "L1,R1,2025-11-03 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
        "L2,R2,2025-12-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-1000,0,1000,-1000,0,USDC\n"
        "L3,R3,2026-01-12 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
        "1000,0,2000,1000,0,USDC\n")

    def test_a_stablecoin_round_trip_is_no_cash_move_in_canada(self):
        d = self._dir({"kraken_ledgers.csv": self.ROWS})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual(_moves(parser),
                         [("2025-11-03", "USD", 2000.0, "Kraken deposit USDC")])
        # Each row a PARKED event: listed, and no move of the ledger.
        self.assertEqual(_parked(parser),
                         [("2025-12-10", "USD", -1000.0, "kraken_ledgers.csv:3"),
                          ("2026-01-12", "USD", 1000.0, "kraken_ledgers.csv:4")])
        # The balance's `where` says how much of it is parked.
        self.assertEqual(
            {e["date"]: e["where"] for e in parser.cash_events if e["kind"] == "CASHBAL"},
            {"2024-12-31": "kraken_ledgers.csv: ledger balance",
             "2025-12-31": "kraken_ledgers.csv: ledger balance + 1,000.00 parked in "
                           "Hybrid Earn",
             "2026-01-12": "kraken_ledgers.csv: ledger balance"})
        # The year-end balance is the spot balance and what is parked: the
        # ledger still holds the 1000, as the account does.
        self.assertEqual(_bals(parser), {("2024-12-31", "USD"): 0.0,
                                         ("2025-12-31", "USD"): 2000.0,
                                         ("2026-01-12", "USD"): 2000.0})
        self.assertNotIn(OVER, err)

    def test_a_fiat_round_trip_is_no_cash_move_either(self):
        rows = self.ROWS.replace("USDC,spot", "ZUSD,spot").replace(",0,USDC\n", ",0,USD\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        for country in ("canada", "usa"):
            with self.subTest(country):
                parser, txs, err = _parse(d / "kraken_ledgers.csv", country)
                self.assertEqual(_moves(parser),
                                 [("2025-11-03", "USD", 2000.0, "Kraken deposit USD")])
                self.assertEqual(_bals(parser)[("2025-12-31", "USD")], 2000.0)
                self.assertEqual(txs, [])
                self.assertNotIn(OVER, err)

    def test_a_fee_in_cash_is_a_flow_in_both_directions(self):
        rows = (
            "L1,R1,2025-11-03 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
            "L2,R2,2025-12-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-1000,0.5,999.5,-1000,0.5,USDC\n"
            "L3,R3,2026-01-12 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "1000,0.25,1999.25,1000,0.25,USDC\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        parser, _txs, _err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual([(e["date"], e["currency"], e["amount"], e["desc"])
                          for e in parser.cash_events if e["kind"] == "FLOW"],
                         [("2025-12-10", "USD", -0.5, "Kraken hybridearnwithdrawal fee"),
                          ("2026-01-12", "USD", -0.25, "Kraken hybridearndeposit fee")])
        self.assertEqual(_bals(parser)[("2025-12-31", "USD")], 1999.5)
        self.assertEqual(_bals(parser)[("2026-01-12", "USD")], 1999.25)

    def test_what_another_export_parked_is_in_the_opening_balance(self):
        # The withdrawal in the 2025 export, the deposit in the 2026 one:
        # read beside each other nothing is over, and the balance at the
        # end of 2025 holds what was parked (the balances are the
        # account's, from its first row in any export: backlog TJ-5032).
        first, second = self.ROWS.split("L3,", 1)
        d = self._dir({"kraken_ledgers_2025.csv": first,
                       "kraken_ledgers_2026.csv": "L3," + second})
        parser, _txs, err = _parse(d / "kraken_ledgers_2026.csv")
        self.assertEqual(_moves(parser), [])
        self.assertEqual(_bals(parser), {("2024-12-31", "USD"): 0.0,
                                         ("2025-12-31", "USD"): 2000.0,
                                         ("2026-01-12", "USD"): 2000.0})
        self.assertNotIn(OVER, err)
        # Without the older export the rows do not say where the 1000
        # came from: an undeclared CASHMOVE and the ATTENTION line.
        parser, _txs, err = _parse(d / "kraken_ledgers_2026.csv", finder=False)
        self.assertEqual(_moves(parser), [(
            "2026-01-12", "USD", 1000.0,
            "Kraken hybridearndeposit USDC, over what was parked")])
        self.assertEqual(_bals(parser)[("2025-12-31", "USD")], 1000.0)
        self.assertIn(f"{OVER} 1000 USDC more than", err)

    def test_what_another_export_parks_later_is_in_the_accounts_balance(self):
        # Export a ends at 15:00 with 2010 in the spot balance; export b
        # parks 1000 at 18:00 that day. The balances are the account's
        # (backlog TJ-5032): each export gives the wallet's latest
        # balance in either (1011) with what was parked as of that row
        # (1000), and none at the end of export a (a once closed there
        # at its own 2010).
        d = self._dir({
            "kraken_ledgers_a.csv":
                "L1,R1,2026-01-03 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,1,0,USDC\n"
                "L2,R2,2026-02-10 15:00:00,deposit,,currency,USDC,spot / main,10,0,2010,1,0,USDC\n",
            "kraken_ledgers_b.csv":
                "L3,R3,2026-02-10 18:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
                "-1000,0,1010,-1,0,USDC\n"
                "L4,R4,2026-05-10 18:00:00,deposit,,currency,USDC,spot / main,1,0,1011,1,0,USDC\n"})
        for name in ("kraken_ledgers_a.csv", "kraken_ledgers_b.csv"):
            with self.subTest(name):
                parser, _txs, _err = _parse(d / name)
                self.assertEqual(_bals(parser), {("2025-12-31", "USD"): 0.0,
                                                 ("2026-05-10", "USD"): 2011.0})

    def test_a_row_with_no_balance_adds_nothing_to_the_balance_before_it(self):
        # The parking row's `balance` is blank: the wallet's last balance
        # is the one before it (2000, nothing parked yet), and the 1000
        # parked after it is not added to that.
        rows = (
            "L1,R1,2026-01-03 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,1,0,USDC\n"
            "L2,R2,2026-02-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-1000,0,,-1,0,USDC\n"
            "L3,R3,2026-03-10 15:00:00,deposit,,currency,ZUSD,spot / main,5,0,5,5,0,USD\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        parser, _txs, _err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual(_bals(parser)[("2026-03-10", "USD")], 2005.0)
        # A later row of the wallet with a balance: the parked 1000 is in.
        rows += ("L4,R4,2026-04-10 15:00:00,deposit,,currency,USDC,spot / main,"
                 "1,0,1001,1,0,USDC\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        parser, _txs, _err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual(_bals(parser)[("2026-04-10", "USD")], 2006.0)

    def test_a_later_row_of_another_wallet_adds_nothing_parked_to_this_one(self):
        # The parking row's `balance` is blank, so the spot wallet's last
        # balance (2000) is from before it; a later row of the coin's
        # Earn wallet (USDC.F) is no reason to add the 1000 to that 2000:
        # what is parked is added per wallet, as of that wallet's balance
        # row (review round 2: 3005, the parked 1000 counted twice).
        rows = (
            "L1,R1,2026-01-03 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,1,0,USDC\n"
            "L2,R2,2026-02-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-1000,0,,-1,0,USDC\n"
            "L3,R3,2026-03-10 15:00:00,deposit,,currency,USDC.F,earn / flexible,5,0,5,5,0,"
            "USDC\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        parser, _txs, _err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual(_bals(parser)[("2026-03-10", "USD")], 2005.0)
        self.assertEqual([e["where"] for e in parser.cash_events
                          if e["kind"] == "CASHBAL" and e["date"] == "2026-03-10"],
                         ["kraken_ledgers.csv: ledger balance"])

    def test_a_us_projects_stablecoin_is_no_cash_event(self):
        # Property there (US-CRYPTO-02): the cash events hold no USDC row.
        d = self._dir({"kraken_ledgers.csv": self.ROWS})
        parser, _txs, _err = _parse(d / "kraken_ledgers.csv", "usa")
        self.assertEqual(parser.cash_events, [])


class TestHybridEarnExcessIsSaid(_Dir):
    """TJ-4954: more back than was parked is booked nowhere, and said."""

    ROWS = (
        "L1,R1,2026-01-05 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
        "L2,R2,2026-05-11 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-1000,0,1000,-1000,0,USDC\n"
        "L3,R3,2026-06-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
        "1004,0,2004,1004,0,USDC\n")

    def test_a_stablecoins_excess_is_an_undeclared_move_and_an_attention_line(self):
        d = self._dir({"kraken_ledgers.csv": self.ROWS})
        parser, txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual(_moves(parser), [
            ("2026-01-05", "USD", 2000.0, "Kraken deposit USDC"),
            ("2026-06-10", "USD", 4.0,
             "Kraken hybridearndeposit USDC, over what was parked")])
        (ev,) = [e for e in parser.cash_events if e.get("desc", "").endswith("parked")]
        self.assertEqual((ev["decl"], ev["line_where"]), (None, "kraken_ledgers.csv:4"))
        self.assertEqual(_bals(parser)[("2026-06-10", "USD")], 2004.0)
        # No income and no purchase: the rows are the custody evidence of
        # the funding deposit and of the withdrawal, kept out of the books.
        self.assertEqual([(t["action"], t["quantity"]) for t in txs],
                         [("TRANSFER", 2000.0), ("TRANSFER", -1000.0)])
        (line,) = [ln for ln in err.splitlines() if OVER in ln]
        self.assertIn("warning: ATTENTION: Kraken ledger kraken_ledgers.csv: Hybrid Earn "
                      "brought back 4 USDC more than this account's ledger rows had parked "
                      "(1 row(s), 2026-06-10)", line)
        self.assertIn("the excess is not booked as income or as coins acquired", line)
        self.assertIn("Check Kraken's Earn history", line)
        self.assertTrue(line.endswith(FOREIGN.format("USDC")), line)

    def test_a_coins_excess_is_said_and_never_booked(self):
        rows = (
            "L1,R1,2026-03-02 15:00:00,hybridearnwithdrawal,,currency,SOL,spot / main,"
            "-2,0,3,-300,0,SOL\n"
            "L2,R2,2026-04-01 15:00:00,hybridearndeposit,,currency,SOL,spot / main,"
            "1.5,0,4.5,225,0,SOL\n"
            "L3,R3,2026-04-02 15:00:00,hybridearndeposit,,currency,SOL,spot / main,"
            "0.75,0,5.25,110,0,SOL\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        for country in ("canada", "usa"):
            with self.subTest(country):
                parser, txs, err = _parse(d / "kraken_ledgers.csv", country)
                self.assertEqual([(t["action"], t["symbol"], t["quantity"]) for t in txs],
                                 [("TRANSFER", "SOL", -2.0)])
                self.assertEqual(_moves(parser), [])
                (line,) = [ln for ln in err.splitlines() if OVER in ln]
                self.assertIn("brought back 0.25 SOL more than", line)
                self.assertIn("(1 row(s), 2026-04-02)", line)
                self.assertIn("the books hold 0.25 SOL fewer than Kraken shows", line)

    def test_a_us_projects_stablecoin_excess_is_a_coins(self):
        d = self._dir({"kraken_ledgers.csv": self.ROWS})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv", "usa")
        self.assertEqual(parser.cash_events, [])
        (line,) = [ln for ln in err.splitlines() if OVER in ln]
        self.assertIn("the books hold 4 USDC fewer than Kraken shows", line)

    def test_still_parked_at_the_end_is_no_excess(self):
        # 1000 out, 400 back: 600 is still parked, nothing is over.
        rows = self.ROWS.replace("1004,0,2004,1004", "400,0,1400,400")
        d = self._dir({"kraken_ledgers.csv": rows})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual(len(_moves(parser)), 1)
        self.assertEqual(_bals(parser)[("2026-06-10", "USD")], 2000.0)
        self.assertNotIn(OVER, err)

    def test_an_excess_before_a_later_parking_is_still_an_excess(self):
        # 1000 out, 1004 back, 500 out: the totals (1500 out, 1004 back)
        # hide the 4 that came back before the second parking.
        rows = self.ROWS + (
            "L4,R4,2026-07-01 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-500,0,1504,-500,0,USDC\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual(_moves(parser)[1:], [(
            "2026-06-10", "USD", 4.0, "Kraken hybridearndeposit USDC, over what was parked")])
        self.assertEqual(_bals(parser)[("2026-07-01", "USD")], 2004.0)
        self.assertIn(f"{OVER} 4 USDC more than", err)

    def test_a_return_and_a_new_parking_in_one_second_by_their_balances(self):
        # 1000 parked; then in one second 1004 back (balance 2004) and 500
        # parked again (balance 1504), then 10 in (1514): the balances
        # before and after the second say the return came first, so 4 is
        # over, whichever row the export lists first. Read parked first,
        # the 500 hid it and the balance was 4 short.
        head = (
            "L1,R1,2026-01-05 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
            "L2,R2,2026-02-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-1000,0,1000,-1000,0,USDC\n")
        back = ("L3,R3,2026-03-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
                "1004,0,2004,1004,0,USDC\n")
        park = ("L4,R4,2026-03-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
                "-500,0,1504,-500,0,USDC\n")
        after = ("L5,R5,2026-04-02 15:00:00,deposit,,currency,USDC,spot / main,10,0,1514,10,0,"
                 "USDC\n")
        for order, line in ((back + park, 4), (park + back, 5)):
            with self.subTest(first=order[:2]):
                d = self._dir({"kraken_ledgers.csv": head + order + after})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual(_moves(parser)[1:-1], [(
                    "2026-03-10", "USD", 4.0,
                    "Kraken hybridearndeposit USDC, over what was parked")])
                (ev,) = [e for e in parser.cash_events if e.get("desc", "").endswith("parked")]
                self.assertEqual(ev["line_where"], f"kraken_ledgers.csv:{line}")
                # 1514 in the spot balance and 500 parked.
                self.assertEqual(_bals(parser)[("2026-04-02", "USD")], 2014.0)
                self.assertIn(f"{OVER} 4 USDC more than", err)
                self.assertNotIn(ORDER, err)
        # The wallet's last second, with only the balance before it: the
        # chain from that balance is taken where it is the order the
        # export lists the rows in, in an export listed oldest first or
        # newest first (its lines read from the last up).
        whole = head + back + park
        for name, text in (("oldest first", whole),
                           ("newest first", "".join(reversed(whole.splitlines(True))))):
            with self.subTest(listed=name):
                d = self._dir({"kraken_ledgers.csv": text})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual([m[2] for m in _moves(parser) if "parked" in m[3]], [4.0])
                self.assertEqual(_bals(parser)[("2026-03-10", "USD")], 2004.0)
                self.assertNotIn(ORDER, err)
        # Listed the other way round there, the balances and the listing
        # disagree and one balance only anchors the chain: three rows of
        # which one is missing chain the same way (review round 4). The
        # file's order is read (parked first: 1500 parked, 1004 back,
        # nothing over; 2004 in the spot balance and 496 parked), and
        # said: the parse's line, and the balance's `where`.
        for name, text in (("oldest first", head + park + back),
                           ("newest first", back + park + "".join(
                               reversed(head.splitlines(True))))):
            with self.subTest(against_the_listing=name):
                d = self._dir({"kraken_ledgers.csv": text})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual([m[2] for m in _moves(parser) if "parked" in m[3]], [])
                self.assertEqual(_bals(parser)[("2026-03-10", "USD")], 2500.0)
                (where,) = [e["where"] for e in parser.cash_events
                            if e["kind"] == "CASHBAL" and e["date"] == "2026-03-10"]
                self.assertIn("whose balances do not say which came last", where)
                (said,) = [ln for ln in err.splitlines() if ORDER in ln]
                self.assertIn("read in the file's order, which finds 0 USDC brought back "
                              "beyond what was parked and leaves 496 parked. Another order "
                              "gives 0 to 4 brought back", said)

    def test_a_parking_and_its_return_in_one_second_by_the_balance_before(self):
        # Out and back in one second end at the balance they began at, so
        # the two rows alone give two orders; the wallet's balance before
        # that second gives the one, with the row after it (which begins
        # where the second ended; without a row after, the listing that
        # chains the other way could be the true one after a missing
        # row, and the file's order is kept and said). Beginning at
        # 2000: parked, then back (nothing over). Beginning at 1000 with
        # nothing parked in the rows: back first, 1000 over, then parked.
        back = ("L3,R3,2026-03-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
                "1000,0,2000,1000,0,USDC\n")
        park = ("L4,R4,2026-03-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
                "-1000,0,1000,-1000,0,USDC\n")
        for began, over, end in ((2000, [], 2000.0),
                                 (1000, [("2026-03-10", "USD", 1000.0,
                                          "Kraken hybridearndeposit USDC, over what was "
                                          "parked")], 2000.0)):
            head = (f"L1,R1,2026-01-05 15:00:00,deposit,,currency,USDC,spot / main,"
                    f"{began},0,{began},{began},0,USDC\n")
            after = (f"L9,R9,2026-04-02 15:00:00,withdrawal,,currency,USDC,spot / main,"
                     f"-1,0,{began - 1},1,0,USDC\n")
            for order in (back + park, park + back):
                with self.subTest(began=began, first=order[:2]):
                    d = self._dir({"kraken_ledgers.csv": head + order + after})
                    parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                    self.assertEqual([m for m in _moves(parser) if "parked" in m[3]], over)
                    # (began at 2000: back at 2000, nothing parked; began
                    # at 1000: 1000 in the spot balance and 1000 parked;
                    # less the 1 withdrawn after)
                    self.assertEqual(_bals(parser)[("2026-04-02", "USD")], end - 1)
                    self.assertNotIn(ORDER, err)
                    self.assertEqual(OVER in err, bool(over))

    def test_one_second_the_balances_put_in_no_order_is_said(self):
        # The export's first rows, back and out in one second: no balance
        # before them says which came first (each row's balance fits
        # after the other's). Read in the file's order, and said, with
        # what the other order gives.
        back = ("L3,R3,2026-03-10 15:00:00,hybridearndeposit,,currency,{0},spot / main,"
                "1000,0,{1},1000,0,{0}\n")
        park = ("L4,R4,2026-03-10 15:00:00,hybridearnwithdrawal,,currency,{0},spot / main,"
                "-1000,0,{2},-1000,0,{0}\n")
        said = ("warning: ATTENTION: Kraken ledger kraken_ledgers.csv: 2 rows of {0} share "
                "one second on 2026-03-10, 2 of them Hybrid Earn rows, and their balances do "
                "not say which came first — read in {3}, which finds {1} {0} "
                "brought back beyond what was parked and leaves {2} parked. Another order "
                "gives 0 to 1000 brought back beyond what was parked, 0 to 1000 left parked: "
                "where {4} is not the true one, the difference is booked "
                "nowhere, or booked as an excess that is none. Check Kraken's Earn history "
                "for that day: listing the second's rows in the export in the order they "
                "happened in (each beginning at the balance the one before it ended at, the "
                "export's rows in one order of time) settles it, unless a row is missing "
                "from the export.")
        # (The export's only rows share one second: it does not say
        # which way it lists them, and the line says that too.)
        lines = ("the order of the export's lines, the export not saying which way it "
                 "lists them", "that order")
        files = ("the file's order", "the file's order")
        for first, over in ((back, 1000), (park, 0)):
            rows = "".join(r.format("USDC", 2000, 1000)
                           for r in (first, park if first is back else back))
            with self.subTest(first="back" if first is back else "park"):
                d = self._dir({"kraken_ledgers.csv": rows})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                # The file's order: back first, 1000 came back that the
                # rows had not parked (an undeclared move, and the line
                # of an excess too); parked first, nothing is over.
                self.assertEqual(
                    [m[2] for m in _moves(parser) if "parked" in m[3]],
                    [1000.0] if over else [])
                self.assertEqual(OVER in err, bool(over))
                (line,) = [ln for ln in err.splitlines() if ORDER in ln]
                self.assertEqual(line, said.format("USDC", over, 1000 if over else 0,
                                                   *lines))
        # Blank balances after a known one give no order either.
        rows = ("L1,R1,2026-01-05 15:00:00,deposit,,currency,SOL,spot / main,2000,0,2000,"
                "2000,0,SOL\n" + park.format("SOL", "", "") + back.format("SOL", "", ""))
        d = self._dir({"kraken_ledgers.csv": rows})
        _parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertNotIn(OVER, err)
        (line,) = [ln for ln in err.splitlines() if ORDER in ln]
        self.assertEqual(line, said.format("SOL", 0, 0, *files))

    # 2000 held, 1000 of it parked; then in ONE second 1004 come back
    # (balance 2004), 504 are spent on SOL (1500) and 500 are parked
    # again (1000).
    BETWEEN_HEAD = (
        "L1,R1,2026-01-05 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
        "L2,R2,2026-02-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-1000,0,{0},-1000,0,USDC\n")
    BETWEEN = (
        "L3,R3,2026-03-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
        "1004,0,2004,1004,0,USDC\n",
        "L4,RT1,2026-03-10 15:00:00,spend,,currency,USDC,spot / main,-504,0,1500,-504,0,USDC\n",
        "L5,R5,2026-03-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-500,0,1000,-500,0,USDC\n")
    BETWEEN_TAIL = (
        "L6,RT1,2026-03-10 15:00:00,receive,,currency,SOL,spot / main,4,0,4,504,0,SOL\n")

    AFTER = ("L7,R7,2026-04-02 15:00:00,deposit,,currency,USDC,spot / main,10,0,1010,10,0,"
             "USDC\n")

    def test_a_row_of_another_type_between_a_return_and_a_new_parking(self):
        # The two Hybrid Earn rows alone chain the wrong way round (the
        # parking ends at 1000, where the return begins): 1500 parked,
        # 1004 back, nothing over, and a balance 4 short that the ledger
        # reconciled to (review round 2, P1). Every row of the wallet in
        # that second, chained from the 1000 before it to the 1000 the
        # next row begins at, gives the one order: back (4 over), spent,
        # parked. Whatever order the export lists them in.
        from itertools import permutations
        for order in permutations(self.BETWEEN):
            with self.subTest(order=[r[:2] for r in order]):
                d = self._dir({"kraken_ledgers.csv": self.BETWEEN_HEAD.format(1000)
                               + "".join(order) + self.BETWEEN_TAIL + self.AFTER})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual(_moves(parser)[1:], [
                    ("2026-03-10", "USD", 4.0,
                     "Kraken hybridearndeposit USDC, over what was parked"),
                    ("2026-04-02", "USD", 10.0, "Kraken deposit USDC")])
                # 1010 in the spot balance and 500 parked.
                self.assertEqual(_bals(parser)[("2026-04-02", "USD")], 1510.0)
                self.assertEqual(
                    [e["where"] for e in parser.cash_events
                     if e["kind"] == "CASHBAL" and e["date"] == "2026-04-02"],
                    ["kraken_ledgers.csv: ledger balance + 500.00 parked in Hybrid Earn"])
                self.assertIn(f"{OVER} 4 USDC more than", err)
                self.assertNotIn(ORDER, err)

    def test_that_second_as_the_wallets_last_is_said_where_the_file_chains_another_way(self):
        # No row after the second: one balance only, the 1000 before
        # it, anchors the chain (back, spent, parked). It is taken where
        # the export lists the rows in that order. Any other listing
        # disagrees with it, and a second that lacks a row chains
        # another way from the balance before it just as well (review
        # round 4), as a listing that is a chain of its own (spent,
        # parked, back; parked, back, spent) would be the true one after
        # a row missing before the second (round 3). The file's order
        # is kept there, and said: the balance's `where`, and the
        # parse's line where the order changes what the Hybrid Earn
        # rows give.
        # (the listing; over; the balance with what is parked; the
        # parse's line)
        back, spend, park = self.BETWEEN
        for order, over, bal, line in (
                ((back, spend, park), 4, 1500.0, None),
                ((back, park, spend), 4, 2000.0, True),
                ((spend, back, park), 4, 1500.0, False),
                ((park, spend, back), 0, 2500.0, True),
                ((spend, park, back), 0, 2500.0, True),
                ((park, back, spend), 0, 1996.0, True)):
            with self.subTest(order=[r[:2] for r in order]):
                d = self._dir({"kraken_ledgers.csv": self.BETWEEN_HEAD.format(1000)
                               + "".join(order) + self.BETWEEN_TAIL})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual([m[2] for m in _moves(parser) if "parked" in m[3]],
                                 [4.0] if over else [])
                self.assertEqual(_bals(parser)[("2026-03-10", "USD")], bal)
                (where,) = [e["where"] for e in parser.cash_events
                            if e["kind"] == "CASHBAL" and e["date"] == "2026-03-10"]
                self.assertEqual(ORDER in err, bool(line))
                self.assertEqual("whose balances do not say which came last" in where,
                                 line is not None)
        # The export listed newest first, the second in the order it
        # happened in from the last line up: taken, nothing to say.
        text = self.BETWEEN_HEAD.format(1000) + "".join(self.BETWEEN) + self.BETWEEN_TAIL
        d = self._dir({"kraken_ledgers.csv": "".join(reversed(text.splitlines(True)))})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual([m[2] for m in _moves(parser) if "parked" in m[3]], [4.0])
        self.assertEqual(_bals(parser)[("2026-03-10", "USD")], 1500.0)
        self.assertNotIn(ORDER, err)

    def test_a_row_missing_before_a_parking_and_its_return_in_both_listings(self):
        # 800 held; a deposit of 100 that the export lacks; then in one
        # second 100 parked (900 -> 800) and 100 back (800 -> 900). From
        # the stale 800 the two rows chain the other way round: back
        # first, an excess of 100 and 100 left parked, the missing
        # deposit read as a Hybrid Earn excess. The order the export
        # lists them in is a chain of its own, so it stays: nothing
        # over, nothing parked, a balance of 900 against moves of 800
        # (the ledger's gap), and the parse and the balance say which
        # order was read. In an export listed oldest first (review round
        # 3) and in one listed newest first, read from its last line up
        # (round 4, P2-1).
        rows = (
            "L1,R1,2026-01-20 15:00:00,deposit,,currency,USDC,spot / main,800,0,800,800,0,USDC\n",
            "L3,R3,2026-03-12 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-100,0,800,-100,0,USDC\n",
            "L4,R4,2026-03-12 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "100,0,900,100,0,USDC\n")
        for name, listed in (("oldest first", rows), ("newest first", rows[::-1])):
            with self.subTest(name):
                d = self._dir({"kraken_ledgers.csv": "".join(listed)})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual(_moves(parser), [("2026-01-20", "USD", 800.0,
                                                   "Kraken deposit USDC")])
                self.assertEqual(_bals(parser)[("2026-03-12", "USD")], 900.0)
                (where,) = [e["where"] for e in parser.cash_events
                            if e["kind"] == "CASHBAL" and e["date"] == "2026-03-12"]
                self.assertIn("whose balances do not say which came last: the file's order "
                              "was taken", where)
                self.assertNotIn(OVER, err)
                (line,) = [ln for ln in err.splitlines() if ORDER in ln]
                self.assertIn("read in the file's order, which finds 0 USDC brought back "
                              "beyond what was parked and leaves 0 parked. Another order "
                              "gives 0 to 100 brought back beyond what was parked, 0 to 100 "
                              "left parked", line)

    def test_an_export_sorted_by_asset_is_not_walked_backwards(self):
        # 1000 held; in one second 100 out (-> 900), 100 back from Hybrid
        # Earn that no row parked (-> 1000), 300 parked (-> 700); later
        # rows; the rows oldest first. With a Canadian-dollar row of the
        # year before listed LAST (an export sorted by asset), the
        # export's first row is later than its last: read as listed
        # newest first, the second was walked from its last line up, the
        # 300 parked first and the 100 "back" out of them: no excess,
        # 100 fewer parked, and a balance 100 short that reconciled with
        # nothing said by the ledger (review round 5). A wallet's
        # direction is read from its own rows: 100 over, 300 parked, in
        # either layout.
        usdc = (
            "T0,R0,2026-03-10 15:00:00,deposit,,currency,USDC,spot / main,1000,0,1000,1000,0,USDC\n"
            "T1,R1,2026-03-11 15:00:00,withdrawal,,currency,USDC,spot / main,-100,0,900,-100,0,USDC\n"
            "T2,R2,2026-03-11 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "100,0,1000,100,0,USDC\n"
            "T3,R3,2026-03-11 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-300,0,700,-300,0,USDC\n"
            "T4,R4,2026-03-12 15:00:00,withdrawal,,currency,USDC,spot / main,-100,0,600,-100,0,USDC\n"
            "T5,R5,2026-03-12 15:00:00,withdrawal,,currency,USDC,spot / main,-300,0,300,-300,0,USDC\n"
            "T6,R6,2026-03-13 15:00:00,deposit,,currency,USDC,spot / main,300,0,600,300,0,USDC\n")
        cad = "C1,RC1,2025-02-01 15:00:00,deposit,,currency,ZCAD,spot / main,50,0,50,36,0,CAD\n"
        for name, text in (("the other asset first", cad + usdc),
                           ("the other asset last", usdc + cad)):
            with self.subTest(name):
                d = self._dir({"kraken_ledgers.csv": text})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual([m[2] for m in _moves(parser) if "parked" in m[3]], [100.0])
                # 600 in the spot balance and 300 parked.
                self.assertEqual(_bals(parser)[("2026-03-13", "USD")], 900.0)
                self.assertIn(f"{OVER} 100 USDC more than", err)
                self.assertNotIn(ORDER, err)
                self.assertEqual([e for e in parser.cash_events if e.get("unsure")], [])

    def test_a_parking_and_its_return_in_exports_pasted_out_of_order(self):
        # 500 in 2025; in 2026 300 more (800), then in the wallet's last
        # second 100 parked (-> 700) and 100 back (-> 800); the 2026
        # rows pasted BEFORE the 2025 row. Walked from the last line up,
        # the return came first: 100 over what was parked and 100 left
        # parked, neither true (review round 5). The wallet's rows go
        # both ways in time, so no direction is known: the two rows
        # chain from the balance before them as the lines read down,
        # which is taken (nothing over, nothing parked) and said, by the
        # parse and by the balance.
        text = (
            "L2,R2,2026-01-20 15:00:00,deposit,,currency,USDC,spot / main,300,0,800,300,0,USDC\n"
            "L3,R3,2026-03-13 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-100,0,700,-100,0,USDC\n"
            "L4,R4,2026-03-13 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "100,0,800,100,0,USDC\n"
            "L1,R1,2025-06-01 15:00:00,deposit,,currency,USDC,spot / main,500,0,500,500,0,USDC\n")
        d = self._dir({"kraken_ledgers.csv": text})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual([m for m in _moves(parser) if "parked" in m[3]], [])
        self.assertEqual(_bals(parser)[("2026-03-13", "USD")], 800.0)
        self.assertNotIn(OVER, err)
        (line,) = [ln for ln in err.splitlines() if ORDER in ln]
        self.assertIn("read in the order their balances chain in, the export not saying "
                      "which way it lists them, which finds 0 USDC brought back beyond what "
                      "was parked and leaves 0 parked. Another order gives 0 to 100 brought "
                      "back beyond what was parked, 0 to 100 left parked: where that order "
                      "is not the true one", line)
        (bal,) = [e for e in parser.cash_events
                  if e["kind"] == "CASHBAL" and e["date"] == "2026-03-13"]
        self.assertTrue(bal.get("unsure"))
        self.assertIn("the export does not say which way it lists this wallet's rows", bal["where"])

    def test_a_balance_after_a_second_of_unknown_order_says_what_is_parked_is_not_sure(self):
        # 800 held (the export lacks a deposit of 100); one second: 100
        # parked (900 -> 800), 100 back (800 -> 900), which chain from
        # no balance beside them; then 50 in (-> 950), and two rows of
        # one later second. The second is read in the file's order and
        # said. What is parked after it depends on that order, and the
        # closing balance is read from a later row: it says so too, so
        # that the ledger can warn when it reconciles (review round 5:
        # only a balance read from the second itself was marked).
        text = (
            "L1,R1,2026-01-20 15:00:00,deposit,,currency,USDC,spot / main,800,0,800,800,0,USDC\n"
            "L3,R3,2026-03-12 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-100,0,800,-100,0,USDC\n"
            "L4,R4,2026-03-12 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "100,0,900,100,0,USDC\n"
            "L5,R5,2026-03-20 15:00:00,deposit,,currency,USDC,spot / main,50,0,950,50,0,USDC\n")
        d = self._dir({"kraken_ledgers.csv": text})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertIn(ORDER, err)
        (bal,) = [e for e in parser.cash_events
                  if e["kind"] == "CASHBAL" and e["date"] == "2026-03-20"]
        self.assertEqual(bal["balance"], 950.0)
        self.assertTrue(bal.get("unsure"))
        self.assertEqual(
            bal["where"],
            "kraken_ledgers.csv: ledger balance (what Hybrid Earn holds of it is counted "
            "from Hybrid Earn rows that share one second on 2026-03-12 in kraken_ledgers.csv "
            "and whose order is not known: the ATTENTION line of `tjs run` for that second "
            "says what settles it)")
        # Without the Hybrid Earn second: nothing to say.
        d = self._dir({"kraken_ledgers.csv": "".join(
            text.splitlines(True)[:1] + [text.splitlines(True)[3].replace("950", "850")])})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual([e for e in parser.cash_events if e.get("unsure")], [])

    def test_the_same_second_with_no_known_balance_before_it_is_the_files_order(self):
        # The parking row before that second has a blank balance: the
        # balance the second began at is not known, so no chain is
        # anchored. The file's order is kept, and said, because another
        # order the balances allow gives another result (the three rows
        # come back to the balance they began at, so each may be first:
        # spent, parked, back would end at 2004 with 496 parked).
        back, spend, park = self.BETWEEN
        for order, over, held, bal in (((back, spend, park), 4, 500, 1500.0),
                                       ((park, back, spend), 0, 496, 1996.0)):
            with self.subTest(order=[r[:2] for r in order]):
                d = self._dir({"kraken_ledgers.csv": self.BETWEEN_HEAD.format("")
                               + "".join(order) + self.BETWEEN_TAIL})
                parser, _txs, err = _parse(d / "kraken_ledgers.csv")
                self.assertEqual([m[2] for m in _moves(parser) if "parked" in m[3]],
                                 [float(over)] if over else [])
                self.assertEqual(_bals(parser)[("2026-03-10", "USD")], bal)
                (line,) = [ln for ln in err.splitlines() if ORDER in ln]
                self.assertIn(
                    f"3 rows of USDC share one second on 2026-03-10, 2 of them Hybrid Earn "
                    f"rows, and their balances do not say which came first — read in the "
                    f"file's order, which finds {over} USDC brought back beyond what was "
                    f"parked and leaves {held} parked. Another order gives 0 to 4 brought "
                    f"back beyond what was parked, 496 to 500 left parked, and the balance "
                    f"after them with what is parked 1500 to 2500 ({bal:.10g} in the file's "
                    f"order): where", line)

    def test_two_wallets_of_one_coin_in_one_second_are_said(self):
        # 100 USDC leave the spot wallet for Hybrid Earn and 100 come
        # back to the Earn wallet (USDC.F) in the same second: a balance
        # is a wallet's, so none says which wallet's row came first.
        # Parked first, nothing is over; back first, 100 is. Read a
        # wallet at a time (the spot wallet first), and said.
        rows = (
            "L1,R1,2026-01-05 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
            "L2,R2,2026-03-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-100,0,1900,-100,0,USDC\n"
            "L3,R3,2026-03-10 15:00:00,hybridearndeposit,,currency,USDC.F,earn / flexible,"
            "100,0,100,100,0,USDC\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertEqual([m for m in _moves(parser) if "parked" in m[3]], [])
        self.assertEqual(_bals(parser)[("2026-03-10", "USD")], 2000.0)
        (line,) = [ln for ln in err.splitlines() if ORDER in ln]
        self.assertIn("2 rows of USDC share one second on 2026-03-10, 2 of them Hybrid Earn "
                      "rows, and their balances do not say which came first — read in the "
                      "file's order, which finds 0 USDC brought back beyond what was parked "
                      "and leaves 0 parked. Another order gives 0 to 100 brought back beyond "
                      "what was parked, 0 to 100 left parked: where", line)

    def test_one_second_whose_order_changes_nothing_is_not_said(self):
        # 2000 parked before; 500 back and 300 parked in one second with no
        # balances: either order leaves 1800 parked and nothing over.
        rows = (
            "L1,R1,2026-02-10 15:00:00,hybridearnwithdrawal,,currency,SOL,spot / main,"
            "-2000,0,,-1,0,SOL\n"
            "L2,R2,2026-03-10 15:00:00,hybridearndeposit,,currency,SOL,spot / main,"
            "500,0,,1,0,SOL\n"
            "L3,R3,2026-03-10 15:00:00,hybridearnwithdrawal,,currency,SOL,spot / main,"
            "-300,0,,-1,0,SOL\n")
        d = self._dir({"kraken_ledgers.csv": rows})
        _parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertNotIn(ORDER, err)
        self.assertNotIn(OVER, err)

    def test_the_cash_wording_by_currency(self):
        # The project's own currency (ZCAD in Canada, ZUSD in a US one):
        # no FX gain to compute. A foreign one (ZUSD in Canada, and there
        # a USD stablecoin): the ledger's cash move, by the setting.
        def rows(asset, cur):
            return (
                f"L1,R1,2026-01-05 15:00:00,deposit,,currency,{asset},spot / main,"
                f"5000,0,5000,1,0,{cur}\n"
                f"L2,R2,2026-02-05 15:00:00,hybridearnwithdrawal,,currency,{asset},"
                f"spot / main,-1000,0,4000,-1,0,{cur}\n"
                f"L3,R3,2026-03-05 15:00:00,hybridearndeposit,,currency,{asset},"
                f"spot / main,1010,0,5010,1,0,{cur}\n")
        for asset, cur, country, end in (("ZCAD", "CAD", "canada", OWN),
                                         ("ZUSD", "USD", "usa", OWN),
                                         ("ZUSD", "USD", "canada", FOREIGN),
                                         ("ZCAD", "CAD", "usa", FOREIGN),
                                         ("USDC", "USDC", "canada", FOREIGN)):
            with self.subTest(asset=asset, country=country):
                d = self._dir({"kraken_ledgers.csv": rows(asset, cur)})
                parser, txs, err = _parse(d / "kraken_ledgers.csv", country)
                (line,) = [ln for ln in err.splitlines() if OVER in ln]
                self.assertIn(f"{OVER} 10 {cur} more than", line)
                self.assertTrue(line.endswith(end.format(cur)), line)
                self.assertNotIn("neither income nor an inflow", line)
                # The same cash event in each: the excess, undeclared.
                self.assertEqual(_moves(parser)[1:], [(
                    "2026-03-05", "USD" if cur == "USDC" else cur, 10.0,
                    f"Kraken hybridearndeposit {cur}, over what was parked")])

    def test_a_cash_fee_is_not_said_unbooked_and_a_coin_fee_is(self):
        # A fee in cash on the way back is the cash events' FLOW, as on the
        # way out: the parse no longer says it is not in the books. A fee
        # in a coin on the way back still is said (nothing books it).
        cash = (
            "L1,R1,2026-01-05 15:00:00,deposit,,currency,USDC,spot / main,2000,0,2000,1,0,USDC\n"
            "L2,R2,2026-02-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-1000,0.5,999.5,-1,0,USDC\n"
            "L3,R3,2026-03-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "1000,0.25,1999.25,1,0,USDC\n")
        d = self._dir({"kraken_ledgers.csv": cash})
        parser, _txs, err = _parse(d / "kraken_ledgers.csv")
        self.assertNotIn("UNBOOKED", err)
        self.assertEqual(len([e for e in parser.cash_events if e["kind"] == "FLOW"]), 2)
        # A US project's USDC is a coin: its fee on the way back is said.
        _parser, _txs, err = _parse(d / "kraken_ledgers.csv", "usa")
        self.assertIn("warning: UNBOOKED: Kraken ledger kraken_ledgers.csv: 1 row(s)", err)
        self.assertIn("hybridearndeposit/ fee x1", err)

    def test_a_row_two_exports_hold_is_said_by_the_first(self):
        d = self._dir({"kraken_ledgers_a.csv": self.ROWS,
                       "kraken_ledgers_b.csv": self.ROWS})
        _p, _txs, err_a = _parse(d / "kraken_ledgers_a.csv")
        parser_b, _txs, err_b = _parse(d / "kraken_ledgers_b.csv")
        self.assertIn(OVER, err_a)
        self.assertNotIn(OVER, err_b)
        # (Each export gives the cash event; the ledger keeps one.)
        self.assertEqual(len(_moves(parser_b)), 2)


class TestHybridEarnRoundTripInTheBooks(unittest.TestCase):
    """TJ-4952 and TJ-4951 through `tjs run`: a coin's and a stablecoin's
    round trip leave the position, its cost and the cash ledger as they
    were."""

    LEDGER = H + (
        "L01,R01,2026-01-05 15:00:00,deposit,,currency,ZCAD,spot / main,5000,0,5000,3600,0,CAD\n"
        "L02,RT1,2026-01-12 15:00:00,spend,,currency,ZCAD,spot / main,-2800,0,2200,-2000,0,CAD\n"
        "L03,RT1,2026-01-12 15:00:00,receive,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
        "L04,RT2,2026-02-02 15:00:00,spend,,currency,ZCAD,spot / main,-1000,0,1200,-720,0,CAD\n"
        "L05,RT2,2026-02-02 15:00:00,receive,,currency,SOL,spot / main,5,0,5,720,0,SOL\n"
        "L06,RH1,2026-03-02 15:00:00,hybridearnwithdrawal,,currency,SOL,spot / main,"
        "-2,0,3,-300,0,SOL\n"
        "L07,RH2,2026-04-01 15:00:00,hybridearndeposit,,currency,SOL,spot / main,"
        "2,0,5,300,0,SOL\n"
        "L08,RH3,2026-05-11 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-1000,0,1000,-1000,0,USDC\n"
        "L09,RH4,2026-06-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
        "1000,0,2000,1000,0,USDC\n"
        "L10,RT3,2026-07-01 15:00:00,spend,,currency,SOL,spot / main,-1,0,4,-200,0,SOL\n"
        "L11,RT3,2026-07-01 15:00:00,receive,,currency,ZCAD,spot / main,280,0,1480,200,0,CAD\n")

    def _cli(self, root, *args):
        from taxjson.lib.dispatch import python_module_argv
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run", ["-C", str(root), *args]),
            cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900,
            env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0", NO_COLOR="1",
                     TAXJSON_LOCAL_TZ="America/Toronto"))

    def _project(self, files, tt="", settings="", usa=False, year=2026):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "proj"
        (root / "inputs" / "crypto").mkdir(parents=True)
        home = ('country = "usa"\nbase_currency = "USD"\nsource_currencies = []\n' if usa else
                'country = "canada"\nprovince = "ON"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n')
        (root / "taxjson.toml").write_text(
            f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
            + home + settings
            + '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n', encoding="utf-8")
        for name, text in files.items():
            (root / "inputs" / "crypto" / name).write_text(text, encoding="utf-8")
        if tt:
            (root / "inputs" / "crypto" / "cash.tt").write_text(tt, encoding="utf-8")
        os.chmod(root, 0o700)
        return root

    def test_position_cost_and_cash_are_as_before_the_round_trip(self):
        root = self._project({"kr_ledgers.csv": self.LEDGER})
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(OVER, r.stdout + r.stderr)
        gains = json.loads((root / "work" / "crypto_gains.json").read_text(encoding="utf-8"))
        # 5 SOL bought for 1000 CAD, 1 sold: 4 left at 800, the two Hybrid
        # Earn rows between them moving neither.
        self.assertEqual([(i["symbol"], i["qty"], i["total_cost"])
                          for i in gains["inventory"]], [("SOL", 4.0, 800.0)])
        r = self._cli(root, "fx-cash", "--ledger", "v2", "--cash-events")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout
        self.assertNotRegex(out, r"CASHMOVE +\S+ USD")
        self.assertRegex(out, r"2026-05-11  crypto/kraken  PARKED    -1,000\.00 USD Kraken "
                              r"hybridearnwithdrawal USDC: into Hybrid Earn")
        self.assertRegex(out, r"2026-06-10  crypto/kraken  PARKED    \+1,000\.00 USD Kraken "
                              r"hybridearndeposit USDC: back from Hybrid Earn")
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"2026-07-01  crypto/kraken  USD\s+2,000\.00\s+2,000\.00\s+0\.00")

    # 2000 USDC bought, 1000 parked, 1004 back.
    EXCESS = "".join(LEDGER.splitlines(keepends=True)[:4]) + (
        "L08,RH3,2026-05-11 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-1000,0,1000,-1000,0,USDC\n"
        "L09,RH4,2026-06-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
        "1004,0,2004,1004,0,USDC\n")

    def test_tt_lines_written_for_the_rows_are_refused_by_name(self):
        # The lines the old reading asked for (the way out `kept`, the way
        # back `cost=`): left in, they reconciled on another figure. Each
        # is named with its file and line and the ledger gives no figure;
        # a line that fits no Hybrid Earn row stays a move of its own.
        tt = ("CASHMOVE 2026-05-11 USD -1000 kept\n"
              "CASHMOVE 2026-06-10 USD 1000 cost=1500.00\n"
              "CASHMOVE 2026-06-20 USD 25 cost=35.00\n")
        root = self._project({"kr_ledgers.csv": self.LEDGER}, tt=tt)
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn("NOT COMPUTED", out)
        self.assertIn(
            "- inputs/crypto/cash.tt:1: `CASHMOVE 2026-05-11 USD -1000 kept` declares a cash "
            "move that is a Kraken Hybrid Earn row (Kraken hybridearnwithdrawal USDC, "
            "kr_ledgers.csv:9) — a Hybrid Earn row is a move within Kraken: the cash stays "
            "in crypto/kraken, so the line moves cash that never moved: remove the line", out)
        self.assertIn(
            "- inputs/crypto/cash.tt:2: `CASHMOVE 2026-06-10 USD 1000 cost=1500.00` declares "
            "a cash move that is a Kraken Hybrid Earn row (Kraken hybridearndeposit USDC, "
            "kr_ledgers.csv:10)", out)
        self.assertNotIn("cash.tt:3", out)
        self.assertRegex(out, r"2026-06-20  crypto/kraken  USD\s+25\.00  cost=35\.00")
        # An unpaired one, the cash still parked: named, and no gap line
        # pointing the other way (the refused line is not walked).
        ledger = "".join(self.LEDGER.splitlines(keepends=True)[:9])
        root = self._project({"kr_ledgers.csv": ledger},
                             tt="CASHMOVE 2026-05-11 USD -1000 kept\n")
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertIn("inputs/crypto/cash.tt:1: `CASHMOVE 2026-05-11 USD -1000 kept`", out)
        self.assertNotIn("the ledger does not see", out)
        # Without the line: computed, the parked 1000 in the balance.
        root = self._project({"kr_ledgers.csv": ledger})
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"2026-05-11  crypto/kraken  USD\s+2,000\.00\s+2,000\.00\s+0\.00")

    # 2000 USDC bought in 2025, 1000 parked over the year end.
    OVER_YEARS = H + (
        "L01,R01,2025-11-03 15:00:00,deposit,,currency,ZCAD,spot / main,5000,0,5000,3600,0,CAD\n"
        "L02,RT1,2025-11-10 15:00:00,spend,,currency,ZCAD,spot / main,-2800,0,2200,-2000,0,CAD\n"
        "L03,RT1,2025-11-10 15:00:00,receive,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
        "L04,RH1,2025-12-10 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-1000,0,1000,-1000,0,USDC\n"
        "L05,RH2,2026-01-12 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
        "1000,0,2000,1000,0,USDC\n")

    def test_a_line_of_an_earlier_year_does_not_stop_this_one(self):
        # The line was written in 2025 for the parking of that year. It
        # is 2025's to refuse: 2026 is computed, and a Warning says what
        # the line is and that removing it changes 2025 (review round 2:
        # it stopped 2026 too). 2026 opens from a CASHOPEN line here, not
        # from 2025's close, and the Warning says so.
        opening = "CASHOPEN 2026-01-01 USD 2000 2800\n"
        root = self._project({"kr_ledgers.csv": self.OVER_YEARS},
                             tt="CASHMOVE 2025-12-10 USD -1000 kept\n" + opening)
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"2026-01-12  crypto/kraken  USD\s+2,000\.00\s+2,000\.00\s+0\.00")
        self.assertIn(
            "warning: inputs/crypto/cash.tt:1: `CASHMOVE 2025-12-10 USD -1000 kept` declares "
            "a cash move that is a Kraken Hybrid Earn row (Kraken hybridearnwithdrawal USDC, "
            "kr_ledgers.csv:5) — a Hybrid Earn row is a move within Kraken: the cash stays "
            "in crypto/kraken, so the line moves cash that never moved. The line is of 2025, "
            "which is not walked here: it does not stop 2026 and changes nothing in it (2026 "
            "does not open from a recorded close). 2025's own ledger refuses the line; "
            "removing it changes 2025: compute 2025 again and, if 2025 is filed, see `tjs "
            "check-filed`", out)
        # The year the line is of refuses it.
        root = self._project({"kr_ledgers.csv": self.OVER_YEARS}, year=2025,
                             tt="CASHMOVE 2025-12-10 USD -1000 kept\n")
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn("NOT COMPUTED", out)
        self.assertIn("so the line moves cash that never moved: remove the line", out)
        # A line of 2026 for the way back: 2026's to refuse.
        root = self._project({"kr_ledgers.csv": self.OVER_YEARS},
                             tt="CASHMOVE 2026-01-12 USD 1000 own\n" + opening)
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertIn("inputs/crypto/cash.tt:1: `CASHMOVE 2026-01-12 USD 1000 own` declares "
                      "a cash move that is a Kraken Hybrid Earn row", out)

    # 2000 USDC bought; on one day 300 parked in Hybrid Earn (line 5) and
    # 300 really withdrawn (line 6): two rows alike in day and amount.
    ALIKE = "".join(LEDGER.splitlines(keepends=True)[:4]) + (
        "L08,RH3,2026-05-11 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
        "-300,0,1700,-300,0,USDC\n"
        "L09,RW9,2026-05-11 16:00:00,withdrawal,,currency,USDC,spot / main,"
        "-300,0,1400,-300,0,USDC\n")
    KEPT = "CASHMOVE 2026-05-11 USD -300 kept"
    SOLD = "CASHMOVE 2026-05-11 USD -300 proceeds=400.00"
    BOTH_ROWS = ("both a cash move (Kraken withdrawal USDC, kr_ledgers.csv:6) and a Kraken "
                 "Hybrid Earn row (Kraken hybridearnwithdrawal USDC, kr_ledgers.csv:5) of one "
                 "day and amount, and nothing says which row {0} was written for. A Hybrid "
                 "Earn row is a move within Kraken and takes no line: keep one line for each "
                 "cash move, with its row after it (`row=kr_ledgers.csv:6`), and remove any "
                 "other")

    def _alike(self, *lines):
        root = self._project({"kr_ledgers.csv": self.ALIKE},
                             tt="".join(ln + "\n" for ln in lines))
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root, self._cli(root, "fx-cash", "--ledger", "v2", "--cash-events").stdout

    def test_a_line_that_fits_a_hybrid_earn_row_and_a_real_move_declares_neither(self):
        # The line written for the Hybrid Earn row when it was read as a
        # cash move (`kept`) fits the real withdrawal of that day too.
        # Taken for it, the withdrawal was never asked about and the
        # year computed on another's declaration (review round 3, P1).
        # Nothing says which row a line was written for: it declares
        # neither, the ledger names it with both rows, and the
        # withdrawal is still asked about. However many lines alike
        # there are, in either order: one, two, three.
        _root, out = self._alike(self.KEPT)
        self.assertIn("NOT COMPUTED — 2 problem(s)", out)
        self.assertIn(f"- inputs/crypto/cash.tt:1: `{self.KEPT}` — the line fits "
                      + self.BOTH_ROWS.format("it") + "\n", out)
        self.assertIn("-300.00 USD went out on 2026-05-11 (Kraken withdrawal USDC, "
                      "kr_ledgers.csv:6) and nothing says what it was", out)
        self.assertRegex(out, r"CASHMOVE  -300\.00 USD undeclared Kraken withdrawal USDC")
        for lines in ((self.KEPT, self.SOLD), (self.SOLD, self.KEPT),
                      (self.KEPT, self.SOLD, self.KEPT)):
            with self.subTest(lines=lines):
                _root, out = self._alike(*lines)
                self.assertIn("NOT COMPUTED — 2 problem(s)", out)
                named = " and ".join(f"inputs/crypto/cash.tt:{n}: `{ln}`"
                                     for n, ln in enumerate(lines, 1))
                self.assertIn(f"- {named} — each fits " + self.BOTH_ROWS.format("each")
                              + "\n", out)
                self.assertNotIn("remove the line", out)
                self.assertRegex(out, r"CASHMOVE  -300\.00 USD undeclared Kraken withdrawal")
        # No line: the withdrawal alone is asked about, as on main.
        _root, out = self._alike()
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertIn("kr_ledgers.csv:6) and nothing says what it was", out)

    def test_a_line_naming_its_row_declares_that_row(self):
        # The way out the message gives: the line for the withdrawal
        # names its row. It declares that row alone; the other line then
        # fits the Hybrid Earn row only and is named for removal; with it
        # gone the year is computed: 300 out for 400.00 against 420.00.
        sold = self.SOLD + " row=kr_ledgers.csv:6"
        _root, out = self._alike(self.KEPT, sold)
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertIn(f"- inputs/crypto/cash.tt:1: `{self.KEPT}` declares a cash move that "
                      f"is a Kraken Hybrid Earn row (Kraken hybridearnwithdrawal USDC, "
                      f"kr_ledgers.csv:5)", out)
        self.assertIn("so the line moves cash that never moved: remove the line", out)
        for lines in ((sold,), (self.KEPT + " row=kr_ledgers.csv:6",)):
            with self.subTest(lines=lines):
                _root, out = self._alike(*lines)
                self.assertNotIn("NOT COMPUTED", out)
                self.assertRegex(out, r"NET\s+-20\.00" if "proceeds" in lines[0]
                                 else r"NET\s+0\.00")
                self.assertRegex(out, r"Kraken withdrawal USDC\s+inputs/crypto/cash\.tt:1")
        # A row that is the Hybrid Earn row: the line is that row's.
        _root, out = self._alike(self.KEPT + " row=kr_ledgers.csv:5")
        self.assertIn("declares a cash move that is a Kraken Hybrid Earn row", out)
        self.assertIn("kr_ledgers.csv:6) and nothing says what it was", out)
        # A row that is no move the line fits: said, and not walked as a
        # move of its own.
        line = self.KEPT + " row=kr_ledgers.csv:4"
        _root, out = self._alike(line)
        self.assertIn(
            f"- inputs/crypto/cash.tt:1: `{line}` names a row (row=kr_ledgers.csv:4) that "
            f"is no undeclared cash move of crypto on that day, in that currency and of "
            f"that amount — a row is named as the ledger's messages name it (FILE:LINE, in "
            f"double quotes where the file's name holds a space): correct the row, or "
            f"remove row=", out)
        self.assertIn("NOT COMPUTED — 2 problem(s)", out)

    ASKED = ("-300.00 USD went out on 2026-05-11 (Kraken withdrawal USDC, {0}) and nothing "
             "says what it was — add `CASHMOVE 2026-05-11 USD -300.00 kept {1}` (still "
             "yours elsewhere; or proceeds=<CAD> / spot / own) to a .tt of crypto (with its "
             "row=: a Kraken Hybrid Earn row of that day and amount takes no line, and a "
             "line without row= fits both)")

    def test_the_line_asked_for_names_its_row_where_one_without_would_fit_two(self):
        # The withdrawal has a Hybrid Earn row alike: the line the ledger
        # asks for, and the `?` line of `tjs cash` that is copied into a
        # .tt file, carry row=. Without it the line copied was refused
        # on the next run as fitting both rows (review round 4).
        root, out = self._alike()
        self.assertIn(self.ASKED.format("kr_ledgers.csv:6", "row=kr_ledgers.csv:6"), out)
        r = self._cli(root, "cash")
        self.assertRegex(r.stdout,
                         r"CASHMOVE\s+2026-05-11 USD\s+-300\.00 \? row=kr_ledgers\.csv:6 ")
        # Taken as asked, in one step.
        _root, out = self._alike("CASHMOVE 2026-05-11 USD -300.00 kept row=kr_ledgers.csv:6")
        self.assertNotIn("NOT COMPUTED", out)
        # A move with no row alike is asked about as before, without row=.
        root = self._project({"kr_ledgers.csv": "".join(
            self.ALIKE.splitlines(keepends=True)[:4] + self.ALIKE.splitlines(keepends=True)[5:])})
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn("add `CASHMOVE 2026-05-11 USD -300.00 kept` (still yours elsewhere; or "
                      "proceeds=<CAD> / spot / own) to a .tt of crypto\n", out)
        self.assertNotIn("row=", out)
        self.assertNotIn("row=", self._cli(root, "cash").stdout)

    def test_a_return_with_nothing_parked_is_one_row_and_its_line_is_taken_as_asked(self):
        # 1000 come back that no row parked: the whole row is the excess.
        # It was a PARKED event and a CASHMOVE of the same 1000, and the
        # line the ledger asked for fit both "rows", the same row twice:
        # refused (review round 4). One row, one event: the move.
        ledger = "".join(self.LEDGER.splitlines(keepends=True)[:4]) + (
            "L09,RH4,2026-06-10 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "1000,0,3000,1000,0,USDC\n")
        root = self._project({"kr_ledgers.csv": ledger})
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2", "--cash-events").stdout
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertIn("+1,000.00 USD came in on 2026-06-10 (Kraken hybridearndeposit USDC, "
                      "over what was parked, kr_ledgers.csv:5) and nothing says what it was "
                      "— add `CASHMOVE 2026-06-10 USD 1000.00 cost=<what it cost in CAD>` "
                      "(or own / spot) to a .tt of crypto\n", out)
        self.assertNotIn("PARKED", out)
        self.assertEqual(out.count("CASHMOVE  +1,000.00 USD undeclared"), 1)
        root = self._project({"kr_ledgers.csv": ledger},
                             tt="CASHMOVE 2026-06-10 USD 1000.00 cost=1350.00\n")
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2", "--cash-events").stdout
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"2026-06-10  crypto/kraken  USD\s+3,000\.00\s+3,000\.00\s+0\.00")
        self.assertRegex(out, r"over what was parked\s+inputs/crypto/cash\.tt:1")
        r = self._cli(root, "cash")
        self.assertRegex(r.stdout, r"CASHMOVE\s+2026-06-10 USD\s+\+1,000\.00 cost=1,350\.00")
        self.assertNotIn("arning", r.stdout + r.stderr)

    def test_a_row_that_brings_back_more_than_was_parked_says_it_is_one_row(self):
        # 1000 parked, 1004 back: the row is listed as PARKED and its 4
        # as a CASHMOVE; the PARKED row says which part is the move.
        root = self._project({"kr_ledgers.csv": self.EXCESS})
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2", "--cash-events").stdout
        self.assertIn("PARKED    +1,004.00 USD Kraken hybridearndeposit USDC: back from "
                      "Hybrid Earn, the account's cash throughout (no move, but for the 4.00 "
                      "beyond what was parked: the CASHMOVE of the same row "
                      "(kr_ledgers.csv:6))", out)
        self.assertIn("PARKED    -1,000.00 USD Kraken hybridearnwithdrawal USDC: into Hybrid "
                      "Earn, the account's cash throughout (no move)", out)

    def test_a_row_in_an_export_whose_name_holds_a_space(self):
        # A browser's second download: `kraken_ledgers (1).csv`. A row
        # of it is named in double quotes, in every message and in the
        # line; unquoted, the line's words split at the space and the
        # line was refused with no mention of row= (review round 4).
        name = "kraken_ledgers (1).csv"
        root = self._project({name: self.ALIKE}, tt=self.SOLD + "\n")
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn('with its row after it (`row="kraken_ledgers (1).csv:6"`), and remove '
                      'any other', out)
        self.assertIn(self.ASKED.format(f"{name}:6", 'row="kraken_ledgers (1).csv:6"'), out)
        r = self._cli(root, "cash")
        self.assertRegex(r.stdout, r'-300\.00 \? row="kraken_ledgers \(1\)\.csv:6" ')
        root = self._project({name: self.ALIKE},
                             tt=self.SOLD + ' row="kraken_ledgers (1).csv:6"  # sold\n')
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"NET\s+-20\.00")
        # A row that is none, quoted in the message too.
        root = self._project({name: self.ALIKE},
                             tt=self.SOLD + ' row="kraken_ledgers (1).csv:4"\n')
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn('names a row (row="kraken_ledgers (1).csv:4") that is no undeclared '
                      'cash move', out)
        # Unquoted: the run stops on the line, naming row= and the form.
        root = self._project({name: self.ALIKE},
                             tt=self.SOLD + " row=kraken_ledgers (1).csv:6\n")
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("cash.tt:1: malformed CASHMOVE line — row='kraken_ledgers' is not a "
                      "broker's row: row= takes the row as the ledger's messages name it, "
                      "<file:line> (row=kr_ledgers.csv:6), in double quotes when the file's "
                      "name holds a space, a `#` or a quote "
                      '(row="kraken_ledgers (1).csv:6")', r.stdout + r.stderr)

    def test_a_row_named_after_the_export_grew_is_told_its_row_may_have_moved(self):
        # The export replaced by a longer one: a row added above moves
        # the withdrawal from line 6 to line 7, and line 6 is now the
        # Hybrid Earn row. The line is refused by name, never taken for
        # another row; the text allowed only that it was written for the
        # Hybrid Earn row ("remove the line"). It says the row may have
        # moved and names the move alike (review round 4).
        rows = self.ALIKE.splitlines(keepends=True)
        longer = "".join(rows[:4]) + (
            "L07,RX,2026-03-01 15:00:00,withdrawal,,currency,ZCAD,spot / main,-10,0,2190,-7,0,"
            "CAD\n") + "".join(rows[4:])
        line = self.SOLD + " row=kr_ledgers.csv:6"
        root = self._project({"kr_ledgers.csv": longer}, tt=line + "\n")
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn("NOT COMPUTED — 2 problem(s)", out)
        fix = ("remove the line; or, where it was written for a cash move and the export has "
               "changed since (rows added above it move its line number), correct its row= "
               "(the cash move of that day and amount that nothing declares: "
               "`row=kr_ledgers.csv:7`)")
        self.assertIn(f"- inputs/crypto/cash.tt:1: `{line}` declares a cash move that is a "
                      f"Kraken Hybrid Earn row (Kraken hybridearnwithdrawal USDC, "
                      f"kr_ledgers.csv:6) — a Hybrid Earn row is a move within Kraken: the "
                      f"cash stays in crypto/kraken, so the line moves cash that never moved: "
                      f"{fix}\n", out)
        r = self._cli(root, "cash")
        self.assertIn(f"refuses it — {fix}", r.stdout + r.stderr)

    def test_a_line_naming_the_hybrid_row_and_one_naming_none_are_said_in_one_run(self):
        # `kept row=<the Hybrid Earn row>` and a second line without
        # row=: the second was taken for the withdrawal while the first
        # stood, and refused as fitting both rows once the first was
        # removed as told (review round 4: two steps). Both are said
        # at once.
        _root, out = self._alike(self.KEPT + " row=kr_ledgers.csv:5", self.SOLD)
        self.assertIn("NOT COMPUTED — 3 problem(s)", out)
        self.assertIn("inputs/crypto/cash.tt:1: `CASHMOVE 2026-05-11 USD -300 kept "
                      "row=kr_ledgers.csv:5` declares a cash move that is a Kraken Hybrid "
                      "Earn row", out)
        self.assertIn(f"- inputs/crypto/cash.tt:2: `{self.SOLD}` — the line fits "
                      + self.BOTH_ROWS.format("it") + "\n", out)
        self.assertRegex(out, r"CASHMOVE  -300\.00 USD undeclared Kraken withdrawal USDC")

    def test_a_row_in_either_of_two_overlapping_exports_names_the_move(self):
        # Two exports hold the withdrawal: all the rows in one (line 6),
        # the last two in the other (line 3). The cash events keep one
        # copy, and only that copy's row was a row a line could name:
        # adding the second export refused a line that was right (review
        # round 4). Either row names the move.
        short = H + "".join(self.ALIKE.splitlines(keepends=True)[4:])
        files = {"kr_ledgers_a.csv": self.ALIKE, "kr_ledgers_b.csv": short}
        for row in ("kr_ledgers_a.csv:6", "kr_ledgers_b.csv:3"):
            with self.subTest(row=row):
                root = self._project(files, tt=f"{self.SOLD} row={row}\n")
                self.assertEqual(self._cli(root, "run").returncode, 0)
                out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
                self.assertNotIn("NOT COMPUTED", out)
                self.assertRegex(out, r"NET\s+-20\.00")
        # The Hybrid Earn row by either of its rows is still that row.
        for row in ("kr_ledgers_a.csv:5", "kr_ledgers_b.csv:2"):
            with self.subTest(hybrid=row):
                root = self._project(files, tt=f"{self.SOLD} row={row}\n")
                self.assertEqual(self._cli(root, "run").returncode, 0)
                out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
                self.assertIn("declares a cash move that is a Kraken Hybrid Earn row", out)

    def test_an_earlier_years_line_that_fits_two_rows_is_a_warning_of_this_year(self):
        # The two rows alike are of 2025 and so is the line: 2025's to
        # refuse. Computing 2026 said nothing of it, where a line written
        # for a Hybrid Earn row gets a Warning (review round 4).
        ledger = self.ALIKE.replace("2026-", "2025-") + (
            "L20,R20,2026-02-02 15:00:00,deposit,,currency,USDC,spot / main,10,0,1410,10,0,"
            "USDC\n")
        root = self._project({"kr_ledgers.csv": ledger}, tt=(
            "CASHMOVE 2025-05-11 USD -300 kept\nCASHOPEN 2026-01-01 USD 1700 2380\n"
            "CASHMOVE 2026-02-02 USD 10 spot\n"))
        self.assertEqual(self._cli(root, "run").returncode, 0)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertNotIn("NOT COMPUTED", out)
        self.assertIn(
            "warning: inputs/crypto/cash.tt:1: `CASHMOVE 2025-05-11 USD -300 kept` — the "
            "line fits " + self.BOTH_ROWS.format("it")
            + ". The line is of 2025, which is not walked here: it does not stop 2026 and "
            "changes nothing in it (2026 does not open from a recorded close). 2025's own "
            "ledger refuses the line; correcting it changes 2025: compute 2025 again and, if "
            "2025 is filed, see `tjs check-filed`", out)

    def test_tjs_cash_leaves_out_lines_that_fit_two_rows_too(self):
        root, _out = self._alike(self.KEPT, self.SOLD)
        r = self._cli(root, "cash")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("each fits " + self.BOTH_ROWS.format("each") + " (not listed or "
                      "counted here; `tjs fx-cash --ledger v2` gives no figure until then)",
                      r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"CASHMOVE\s+2026-05-11 USD\s+-300\.00 \?")
        self.assertNotIn("kept", r.stdout)
        self.assertNotIn("proceeds", r.stdout)
        self.assertRegex(r.stdout, r"TOTAL WITHDRAWALS: 300\.00 USD")

    def test_the_earlier_years_warning_names_the_close_only_when_the_year_opens_from_it(self):
        # (The CASHOPEN case is in the test above, through the ledger.)
        from taxjson.lib import fx_cash_v2 as V2
        def said(year, carry):
            park = {"kind": "PARKED", "date": "2024-12-10", "settle": "2024-12-10",
                    "currency": "USD", "amount": -1000.0, "label": "crypto",
                    "book": "crypto/kraken", "desc": "Kraken hybridearnwithdrawal USDC",
                    "line_where": "kr_ledgers.csv:5"}
            line = {"kind": "CASHMOVE", "date": "2024-12-10", "settle": "2024-12-10",
                    "currency": "USD", "amount": -1000.0, "label": "crypto",
                    "book": "crypto/kraken", "decl": {"how": "kept", "value": None},
                    "line": "CASHMOVE 2024-12-10 USD -1000 kept", "origin": "tt",
                    "where": "inputs/crypto/cash.tt:1"}
            problems = []
            (text,) = V2._declarations([park], [line], "CAD", year, problems, carry)
            self.assertEqual(problems, [])
            return text.split("never moved. ", 1)[1]
        carry = {"year": 2025, "currency": "CAD", "books": {"crypto/kraken": {}}}
        self.assertEqual(
            said(2025, dict(carry, year=2024)),
            "The line is of 2024, which is not walked here, so it does not stop 2025; the "
            "pool 2025 opens with comes from the close of 2024, which was computed with the "
            "line. Removing the line changes 2024 and what its close carries forward: "
            "compute and close 2024 again and, if 2024 is filed, see `tjs check-filed`")
        self.assertIn("compute and close 2024 and each year after it again",
                      said(2026, carry))
        for carry in (None, {"problem": "unreadable"}, {"year": 2025, "books": {}}):
            self.assertEqual(
                said(2026, carry),
                "The line is of 2024, which is not walked here: it does not stop 2026 and "
                "changes nothing in it (2026 does not open from a recorded close). 2024's "
                "own ledger refuses the line; removing it changes 2024: compute 2024 again "
                "and, if 2024 is filed, see `tjs check-filed`")

    def test_tjs_cash_leaves_out_the_line_the_ledger_refuses(self):
        # `tjs cash` listed and counted the refused line as a withdrawal
        # of its own. It leaves it out, as the ledger does, and says so;
        # a line that fits no Hybrid Earn row is listed as before.
        tt = ("CASHMOVE 2026-05-11 USD -1000 kept\n"
              "CASHMOVE 2026-06-20 USD 25 cost=35.00\n")
        root = self._project({"kr_ledgers.csv": self.LEDGER}, tt=tt)
        self.assertEqual(self._cli(root, "run").returncode, 0)
        r = self._cli(root, "cash")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn(
            "tjs cash: warning: inputs/crypto/cash.tt:1: `CASHMOVE 2026-05-11 USD -1000 kept` "
            "declares a cash move that is a Kraken Hybrid Earn row (Kraken "
            "hybridearnwithdrawal USDC, kr_ledgers.csv:9) — a Hybrid Earn row is a move within Kraken: the cash stays "
            "in crypto/kraken, so the line moves cash that never moved: not listed or "
            "counted here, and `tjs fx-cash --ledger v2` refuses it — remove the line", out)
        self.assertNotIn("-1,000.00 kept", r.stdout)
        self.assertNotIn("TOTAL WITHDRAWALS", r.stdout)
        self.assertRegex(r.stdout, r"CASHMOVE\s+2026-06-20 USD\s+\+25\.00 cost=35\.00")
        doc = json.loads(self._cli(root, "cash", "--json").stdout)
        self.assertEqual([row["tt"] for row in doc["rows"] if row.get("origin") == "tt"],
                         ["CASHMOVE 2026-06-20 USD +25.00 cost=35.00"])
        self.assertEqual(doc["totals"]["withdrawals"], {})
        self.assertEqual(len(doc["problems"]), 1)
        self.assertIn("cash.tt:1", doc["problems"][0])

    def test_a_second_of_unknown_order_reaches_the_ledger_the_run_and_the_sum(self):
        # 1000 held at the year's start; the wallet's first second, in an
        # export listed oldest first: 300 parked (1000 -> 700), 100 back
        # (900 -> 1000), 100 out (1000 -> 900), which chain only in the
        # reverse of that order; then 100 out (-> 600) and 50 out (-> 550).
        # The second is read in the file's order (200 left parked, nothing
        # over), and the closing balance, read from a later single row,
        # reconciles: 550 and 200 parked against a ledger of 750. The
        # ledger says in a Warning that what is parked is not sure, with
        # the second's day and export (review rounds 5 and 6: no test
        # took the mark through the ledger); the end-of-run note and the
        # `tjs sum` line say that there are warnings, which were in the
        # report alone.
        rows = (
            "L3,R3,2026-03-11 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-300,0,700,-300,0,USDC\n"
            "L2,R2,2026-03-11 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "100,0,1000,100,0,USDC\n"
            "L1,R1,2026-03-11 15:00:00,withdrawal,,currency,USDC,spot / main,-100,0,900,-100,0,USDC\n"
            "L4,R4,2026-03-12 15:00:00,withdrawal,,currency,USDC,spot / main,-100,0,600,-100,0,USDC\n"
            "L5,R5,2026-03-14 15:00:00,withdrawal,,currency,USDC,spot / main,-50,0,550,-50,0,USDC\n")
        tt = ("CASHOPEN 2026-01-01 USD 1000.00 1350.00 at=kraken\n"
              "CASHMOVE 2026-03-11 USD -100 spot\nCASHMOVE 2026-03-12 USD -100 spot\n"
              "CASHMOVE 2026-03-14 USD -50 spot\n")
        root = self._project({"kr_ledgers.csv": H + rows}, tt=tt,
                             settings='fx_cash_ledger = "v2"\nfx_cash_gains = true\n')
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        (line,) = [ln for ln in out.splitlines() if ORDER in ln]
        self.assertIn("settles it, unless a row is missing from the export.", line)
        self.assertIn("review it before using it; 2 warnings in reports/fx_cash.rpt", out)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertNotIn("NOT COMPUTED", out)
        self.assertIn(
            "warning: crypto/kraken USD: the statement balance at the end of 2026-03-14 "
            "(750.00) reconciles, and may not be the balance at the end of that day "
            "(kr_ledgers.csv: ledger balance + 200.00 parked in Hybrid Earn (what Hybrid "
            "Earn holds of it is counted from Hybrid Earn rows that share one second on "
            "2026-03-11 in kr_ledgers.csv and whose order is not known: the ATTENTION line "
            "of `tjs run` for that second says what settles it))", out)
        doc = json.loads(self._cli(root, "fx-cash", "--ledger", "v2", "--json").stdout)
        self.assertEqual(len(doc["warnings"]), 2)
        # (`unsure` on every reconciliation row, true or false.)
        self.assertEqual([row["unsure"] for row in doc["reconciliation"]], [True, True])
        r = self._cli(root, "sum")
        self.assertIn("FX on foreign cash (line 15300), not in the rows: reportable 0.00; "
                      "2 warnings in reports/fx_cash.rpt", r.stdout + r.stderr)
        # The second listed in the order it happened in: nothing to say,
        # and the note and the line are as they were.
        lines = rows.splitlines(True)
        root = self._project({"kr_ledgers.csv": H + "".join(lines[:3][::-1] + lines[3:])},
                             tt=tt + "CASHMOVE 2026-03-11 USD 100 spot\n",
                             settings='fx_cash_ledger = "v2"\nfx_cash_gains = true\n')
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(ORDER, r.stdout + r.stderr)
        self.assertIn("review it before using it -> reports/fx_cash.rpt", r.stdout + r.stderr)
        doc = json.loads(self._cli(root, "fx-cash", "--ledger", "v2", "--json").stdout)
        self.assertEqual(doc["warnings"], [])
        self.assertEqual([row["unsure"] for row in doc["reconciliation"]], [False, False])
        r = self._cli(root, "sum")
        self.assertNotIn("warning", (r.stdout + r.stderr).split("FX on foreign cash")[1][:120])

    def test_an_excess_is_asked_about_or_taken_at_spot_by_the_setting(self):
        # The default: undeclared, no figure, the line to add.
        root = self._project({"kr_ledgers.csv": self.EXCESS})
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (line,) = [ln for ln in (r.stdout + r.stderr).splitlines() if OVER in ln]
        self.assertTrue(line.endswith(FOREIGN.format("USDC")), line)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertIn("+4.00 USD came in on 2026-06-10 (Kraken hybridearndeposit USDC, over "
                      "what was parked, kr_ledgers.csv:6) and nothing says what it was — add "
                      "`CASHMOVE 2026-06-10 USD 4.00 cost=<what it cost in CAD>`", out)
        # fx_cash_inflow_cost = "spot": in at the day's rate, a figure, and
        # the line the parse printed says that reading too.
        root = self._project({"kr_ledgers.csv": self.EXCESS},
                             settings='fx_cash_inflow_cost = "spot"\n')
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (line,) = [ln for ln in (r.stdout + r.stderr).splitlines() if OVER in ln]
        self.assertTrue(line.endswith(FOREIGN.format("USDC")), line)
        out = self._cli(root, "fx-cash", "--ledger", "v2").stdout
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"2026-06-10  crypto/kraken  USD\s+4\.00  spot "
                              r"\(fx_cash_inflow_cost\)")
        self.assertRegex(out, r"2026-06-10  crypto/kraken  USD\s+2,004\.00\s+2,004\.00\s+0\.00")

    def test_overlapping_exports_give_the_ledger_one_excess(self):
        # Both exports hold the rows: one ATTENTION line (the first
        # export's), one undeclared move in the ledger, one PARKED row each.
        root = self._project({"kr_ledgers_a.csv": self.EXCESS,
                              "kr_ledgers_b.csv": self.EXCESS})
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        said = [ln for ln in (r.stdout + r.stderr).splitlines() if OVER in ln]
        self.assertEqual(len(said), 1, said)
        self.assertIn("Kraken ledger kr_ledgers_a.csv:", said[0])
        out = self._cli(root, "fx-cash", "--ledger", "v2", "--cash-events").stdout
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertEqual(out.count("PARKED    +1,004.00 USD"), 1)
        self.assertEqual(out.count("PARKED    -1,000.00 USD"), 1)
        self.assertEqual(out.count("CASHMOVE  +4.00 USD undeclared"), 1)

    def test_a_us_projects_excess_through_the_run(self):
        # US dollars are the project's own currency, USDC a coin there:
        # the run says each in its own words and books nothing for either.
        ledger = H + (
            "L1,R1,2026-01-03 15:00:00,deposit,,currency,ZUSD,spot / main,3000,0,3000,3000,0,USD\n"
            "L2,R2,2026-02-10 15:00:00,hybridearnwithdrawal,,currency,ZUSD,spot / main,"
            "-1000,0,2000,-1000,0,USD\n"
            "L3,R3,2026-03-12 15:00:00,hybridearndeposit,,currency,ZUSD,spot / main,"
            "1010,0,3010,1010,0,USD\n"
            "L4,R4,2026-03-13 15:00:00,hybridearnwithdrawal,,currency,USDC,spot / main,"
            "-10,0,0,-10,0,USDC\n"
            "L5,R5,2026-03-14 15:00:00,hybridearndeposit,,currency,USDC,spot / main,"
            "12,0,12,12,0,USDC\n")
        root = self._project({"kr_ledgers.csv": ledger}, usa=True)
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        said = [ln for ln in (r.stdout + r.stderr).splitlines() if OVER in ln]
        self.assertEqual(len(said), 2, said)
        self.assertIn(f"{OVER} 10 USD more than", said[0])
        self.assertTrue(said[0].endswith(OWN.format("USD")), said[0])
        self.assertIn(f"{OVER} 2 USDC more than", said[1])
        self.assertTrue(said[1].endswith("the books hold 2 USDC fewer than Kraken shows "
                                         "until the excess is entered via a .tt file."))
        gains = json.loads((root / "work" / "crypto_gains.json").read_text(encoding="utf-8"))
        self.assertEqual(gains["inventory"], [])


class TestTheRowOfACashmoveLine(unittest.TestCase):
    """`row=` of a .tt CASHMOVE line (lib/cash_lines): the forms read, and
    the form every message prints (review round 4)."""

    def test_a_row_is_read_bare_or_in_double_quotes(self):
        from taxjson.lib.cash_lines import parse_line, row_token
        head = "CASHMOVE 2025-05-11 USD -300 kept "
        for text, row in (
                ("row=kr_ledgers.csv:6", "kr_ledgers.csv:6"),
                ('row="kraken_ledgers (1).csv:6"', "kraken_ledgers (1).csv:6"),
                ('row="kraken #2.csv:6" at=kraken # the second', "kraken #2.csv:6"),
                ('at=kraken row="a \\"b\\" c\\\\d.csv:12"', 'a "b" c\\d.csv:12'),
                ('# row="x.csv:1"', None), ("", None)):
            with self.subTest(text=text):
                ev = parse_line(head + text)
                self.assertEqual(ev["row"], row)
                self.assertEqual(ev["decl"], {"how": "kept", "value": None})
                if row is not None:
                    # What a message prints is read back as the same row.
                    self.assertEqual(parse_line(head + row_token(row))["row"], row)
        self.assertEqual(row_token("kr_ledgers.csv:6"), "row=kr_ledgers.csv:6")
        self.assertEqual(row_token("kraken_ledgers (1).csv:6"),
                         'row="kraken_ledgers (1).csv:6"')

    def test_a_malformed_row_is_refused_naming_row(self):
        from taxjson.lib.cash_lines import CashLineError, parse_line
        head = "CASHMOVE 2025-05-11 USD -300 kept "
        for text, said in (("row=kraken_ledgers (1).csv:6", "row='kraken_ledgers' is not"),
                           ("row=kr_ledgers.csv", "row='kr_ledgers.csv' is not"),
                           ("row=kr_ledgers.csv:0", "row='kr_ledgers.csv:0' is not"),
                           ('row=":6"', "row=':6' is not"),
                           ('row="a.csv:6" row=b.csv:7', "row= given twice")):
            with self.subTest(text=text):
                with self.assertRaises(CashLineError) as cm:
                    parse_line(head + text, "inputs/crypto/cash.tt:1")
                self.assertIn(said, str(cm.exception))
                self.assertIn("inputs/crypto/cash.tt:1: ", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
