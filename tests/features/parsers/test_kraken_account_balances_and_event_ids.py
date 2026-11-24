"""Kraken's ledger balances are the account's, and its cash events carry
the ledger's ids (the outside audit of 2026-11-23: backlog TJ-5027,
TJ-5032, TJ-5033).

- TJ-5027: a wallet is its asset code AND the wallet the export names
  (its `wallet` column). Read by the code alone, 100 USDC of which 40
  were allocated to `earn / flexible` closed at 40: the last wallet's
  balance replaced the other's.
- TJ-5032: every export gives the ACCOUNT's balance (each wallet's
  latest balance at or before the date, the wallets of a currency added
  up, over every ledger export read). The spot wallet and the Earn
  wallet exported apart were a CASHBAL of 100 and one of 200, each
  checked against the whole account; the same rows in one file gave 300.
  A wallet the exports read do not cover gives no balance from the day
  cash moved to it, and a NOTE. Where a wallet's next row begins at
  another balance than its last row ended at, rows are missing between
  them: a date between the two reads the balance the next row begins at.
  Before the year end just before a wallet's first row its balance is
  not known, unless that row begins at nothing.
- TJ-5033: a cash move carries its row's txid, and the events of a trade
  an id of the trade (its refid and the leg). Two deposits of 100 on one
  day in two exports were read as one event repeated.

The same rows give the same balances and the same cash however the
exports are split. All data synthetic.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import cash_events as CE
from taxjson.lib import timezones as TZ

from tax_rules import rule
from taxjson.rules import ids as R

H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance,"
     "amountusd,feeusd,feecurrency\n")
# An export of the older layout: no `wallet` column.
H_OLD = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"


def _row(txid, refid, time, typ, sub, asset, wallet, amount, balance, fee=0):
    return (f"{txid},{refid},{time},{typ},{sub},currency,{asset},{wallet},{amount},{fee},"
            f"{balance},{abs(amount)},{fee},USD\n")


def _old(txid, refid, time, typ, sub, asset, amount, balance):
    return f"{txid},{refid},{time},{typ},{sub},currency,{asset},{amount},0,{balance}\n"


class _Exports(unittest.TestCase):
    def _events(self, files):
        """Every export's cash events as `collect` holds them before its
        dedup (one account folder, one book); a file whose name ends in
        `_old.csv` is written with the older header."""
        from _support.parser_cash import parsed_cash_events
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        for name, rows in files.items():
            (Path(td.name) / name).write_text(
                (H_OLD if name.endswith("_old.csv") else H) + rows, encoding="utf-8")
        out = []
        with patch.object(TZ, "local_tz_name", return_value="America/Toronto"):
            for name in sorted(files):
                for e in parsed_cash_events("kraken", Path(td.name) / name):
                    out.append(dict(e, label="coins", broker="kraken", file=name,
                                    book="coins/kraken"))
        return out

    def _kept(self, files):
        return CE.dedup_across_files(self._events(files))

    @staticmethod
    def _bals(events):
        return sorted((e["date"], e["currency"], e["balance"]) for e in events
                      if e["kind"] == "CASHBAL")

    @staticmethod
    def _moves(events):
        return sorted((e["date"], e["currency"], e["amount"]) for e in events
                      if e["kind"] == "CASHMOVE")


class TestAWalletIsItsCodeAndItsName(_Exports):
    """TJ-5027."""
    ROWS = (_row("LA1", "RA1", "2026-03-02 15:00:00", "deposit", "", "USDC", "spot / main",
                 100, 100)
            + _row("LA2", "RA2", "2026-03-05 15:00:00", "earn", "allocation", "USDC",
                   "spot / main", -40, 60)
            + _row("LA3", "RA2", "2026-03-05 15:00:00", "earn", "allocation", "USDC",
                   "earn / flexible", 40, 40))

    @rule(R.CA_FX_07)
    def test_two_wallets_of_one_asset_code_keep_a_balance_each(self):
        evs = self._events({"kraken_ledgers.csv": self.ROWS})
        self.assertEqual(self._bals(evs), [("2025-12-31", "USD", 0.0),
                                           ("2026-03-05", "USD", 100.0)])
        # Two wallets, a row each in that second: no second whose order
        # the balances leave open.
        self.assertFalse([e for e in evs if e.get("unsure")])
        self.assertEqual(self._moves(evs), [("2026-03-02", "USD", 100.0)])

    def test_the_wallets_name_is_read_whatever_its_case_and_spaces(self):
        from taxjson.lib.brokerages.kraken import _kr_wallet
        self.assertEqual(_kr_wallet("USDC", " Spot /  Main "), _kr_wallet("USDC", "spot / main"))
        self.assertNotEqual(_kr_wallet("USDC", "spot / main"),
                            _kr_wallet("USDC", "earn / flexible"))
        self.assertNotEqual(_kr_wallet("USDC.F", "earn / flexible"),
                            _kr_wallet("USDC", "earn / flexible"))
        # (no `wallet` column: the code and its suffix, as before)
        self.assertEqual(_kr_wallet("USDC.F"), "USDC.F")
        self.assertEqual(_kr_wallet("ZUSD", ""), "USD")

    @rule(R.CA_FX_07)
    def test_rows_without_a_wallet_column_join_the_named_spot_wallet(self):
        # An older export (no `wallet` column) holds the deposit; a newer
        # one names the wallets. The older rows are the spot wallet's:
        # counted beside it, the account held 100 + 60 + 40.
        old = _old("LA1", "RA1", "2026-03-02 15:00:00", "deposit", "", "USDC", 100, 100)
        new = self.ROWS.split("\n", 1)[1]
        kept = self._kept({"kraken_ledgers_old.csv": old, "kraken_ledgers_new.csv": new})
        self.assertEqual(self._bals(kept), [("2025-12-31", "USD", 0.0),
                                            ("2026-03-05", "USD", 100.0)])
        self.assertFalse([e for e in kept if e.get("unsure")])

    @rule(R.CA_FX_07)
    def test_rows_without_a_wallet_column_join_the_wallet_of_their_kind_only(self):
        # (review P2-3) The older export's bare USDC is the spot wallet:
        # beside a newer export that names Earn wallets alone it is a
        # wallet of its own, added up (100 + 5 + 7), and nothing is
        # unsure. It once joined the only named wallet, an Earn one.
        old = _old("LA1", "RA1", "2026-03-02 15:00:00", "deposit", "", "USDC", 100, 100)
        new = (_row("LA5", "RA5", "2026-03-06 15:00:00", "deposit", "", "USDC",
                    "earn / flexible", 5, 5)
               + _row("LA6", "RA6", "2026-03-07 15:00:00", "deposit", "", "USDC",
                      "earn / bonded", 7, 7))
        kept = self._kept({"kraken_ledgers_old.csv": old, "kraken_ledgers_new.csv": new})
        self.assertIn(("2026-03-07", "USD", 112.0), self._bals(kept))
        self.assertFalse([e for e in kept if e.get("unsure")])
        # The older export's USDC.F is the flexible Earn wallet the newer
        # one names (60 spot + 41 Earn after a reward of 1).
        old = (old + _old("LA2", "RA2", "2026-03-05 15:00:00", "earn", "allocation", "USDC",
                          -40, 60)
               + _old("LA3", "RA2", "2026-03-05 15:00:00", "earn", "allocation", "USDC.F",
                      40, 40))
        new = _row("LA9", "RA9", "2026-04-01 15:00:00", "earn", "reward", "USDC",
                   "earn / flexible", 1, 41)
        kept = self._kept({"kraken_ledgers_old.csv": old, "kraken_ledgers_new.csv": new})
        self.assertIn(("2026-04-01", "USD", 101.0), self._bals(kept))
        self.assertFalse([e for e in kept if e.get("unsure")])

    def test_rows_that_may_be_one_of_two_named_wallets_of_their_kind_are_said(self):
        # No `wallet` column on the older rows, and the newer export
        # names two spot wallets of the asset: nothing says which the
        # older rows are of. Counted on their own, and the balance says so.
        old = _old("LA1", "RA1", "2026-03-02 15:00:00", "deposit", "", "USDC", 100, 100)
        new = (_row("LA5", "RA5", "2026-03-06 15:00:00", "deposit", "", "USDC",
                    "spot / main", 5, 5)
               + _row("LA6", "RA6", "2026-03-07 15:00:00", "deposit", "", "USDC",
                      "spot / margin", 7, 7))
        kept = self._kept({"kraken_ledgers_old.csv": old, "kraken_ledgers_new.csv": new})
        last = [e for e in kept if e["kind"] == "CASHBAL" and e["date"] == "2026-03-07"]
        self.assertEqual([e["balance"] for e in last], [112.0])
        self.assertTrue(last[0].get("unsure"))
        for part in ("the USDC rows of kraken_ledgers_old.csv name no wallet",
                     "spot / main, spot / margin", "twice where they are one of those"):
            self.assertIn(part, last[0]["where"])


class TestTheBalanceIsTheAccounts(_Exports):
    """TJ-5032."""
    SPOT = _row("LB1", "RB1", "2026-03-02 15:00:00", "deposit", "", "USDC", "spot / main",
                100, 100)
    EARN = _row("LB2", "RB2", "2026-03-02 16:00:00", "deposit", "", "USDC.F",
                "earn / flexible", 200, 200)

    @rule(R.CA_FX_07)
    def test_wallets_exported_apart_give_the_account_balance(self):
        split = {"kraken_ledgers_spot.csv": self.SPOT, "kraken_ledgers_earn.csv": self.EARN}
        each = self._events(split)
        for name in split:
            with self.subTest(name):
                self.assertEqual(self._bals([e for e in each if e["file"] == name]),
                                 [("2025-12-31", "USD", 0.0), ("2026-03-02", "USD", 300.0)])
        one = self._kept({"kraken_ledgers.csv": self.SPOT + self.EARN})
        kept = CE.dedup_across_files(each)
        self.assertEqual(self._bals(kept), [("2025-12-31", "USD", 0.0),
                                            ("2026-03-02", "USD", 300.0)])
        self.assertEqual(self._bals(kept), self._bals(one))
        self.assertEqual(self._moves(kept), self._moves(one))

    @rule(R.CA_FX_07)
    def test_exports_split_by_date_give_the_same_balances_as_one_file(self):
        # January to March in one export, April on in the other, a year
        # end between two rows of the second: each export once closed at
        # its own last row, a balance the other's rows had moved since.
        rows = [_row("LD1", "RD1", "2025-11-03 15:00:00", "deposit", "", "ZUSD", "spot / main",
                     500, 500),
                _row("LD2", "RD2", "2026-02-03 15:00:00", "withdrawal", "", "ZUSD",
                     "spot / main", -100, 400),
                _row("LD3", "RD3", "2026-04-07 15:00:00", "deposit", "", "USDC",
                     "earn / flexible", 50, 50),
                _row("LD4", "RD4", "2026-05-05 15:00:00", "withdrawal", "", "ZUSD",
                     "spot / main", -25, 375)]
        one = self._kept({"kraken_ledgers.csv": "".join(rows)})
        want = [("2024-12-31", "USD", 0.0), ("2025-12-31", "USD", 500.0),
                ("2026-05-05", "USD", 425.0)]
        self.assertEqual(self._bals(one), want)
        for cut in (1, 2, 3):
            with self.subTest(cut=cut):
                kept = self._kept({"kraken_ledgers_a.csv": "".join(rows[:cut]),
                                   "kraken_ledgers_b.csv": "".join(rows[cut:])})
                self.assertEqual(self._bals(kept), want)
                self.assertEqual(self._moves(kept), self._moves(one))

    @rule(R.CA_FX_07)
    def test_rows_missing_between_two_exports_are_a_gap_on_the_earlier_side(self):
        # (review P1) The 2023 export ends at 300; the 2025 export's
        # first row begins at 500: rows are missing between them. The
        # year ends between the two read the 500 the later export opens
        # at (the 300 was stale by what the missing rows moved), from
        # the later export's row; the gap then shows in 2024's ledger.
        files = {"kraken_ledgers_2023.csv": _row(
                     "LG1", "RG1", "2023-03-02 15:00:00", "deposit", "", "ZUSD", "spot / main",
                     300, 300),
                 "kraken_ledgers_2025.csv": _row(
                     "LG3", "RG3", "2025-03-02 15:00:00", "deposit", "", "ZUSD", "spot / main",
                     100, 600)}
        kept = self._kept(files)
        self.assertEqual(self._bals(kept), [
            ("2022-12-31", "USD", 0.0), ("2023-12-31", "USD", 500.0),
            ("2024-12-31", "USD", 500.0), ("2025-03-02", "USD", 600.0)])
        where = {e["date"]: e["where"] for e in kept if e["kind"] == "CASHBAL"}
        self.assertEqual(where["2022-12-31"], "kraken_ledgers_2023.csv: ledger balance")
        self.assertEqual(where["2024-12-31"], "kraken_ledgers_2025.csv: ledger balance")
        # The balance says what it is, for the ledger's gap line: no
        # statement's balance at the end of that day, the later export's
        # opening (review round 2, P3).
        said = {e["date"]: e.get("opens") for e in kept if e["kind"] == "CASHBAL"}
        self.assertEqual(said["2023-12-31"], said["2024-12-31"])
        self.assertEqual(said["2024-12-31"], (
            "kraken_ledgers_2025.csv:2 opens the wallet USD, spot / main at 500.00 USD "
            "(2025-03-02), and the wallet's row before it, kraken_ledgers_2023.csv:2 "
            "(2023-03-02), ends at 300.00"))
        self.assertIsNone(said["2022-12-31"])
        from taxjson.lib import fx_cash_v2 as V2
        check = next(e for e in kept if e["kind"] == "CASHBAL" and e["date"] == "2023-12-31")
        text = V2._gap_text("coins/kraken", "USD", check, 300.0, 200.0, check["where"])
        self.assertTrue(text.startswith("coins/kraken USD: kraken_ledgers_2025.csv:2 opens the "
                                        "wallet USD, spot / main at 500.00 USD"), text)
        for part in ("rows between the two are missing from the exports read",
                     "is 500.00 where the ledger has 300.00 at the end of 2023-12-31 "
                     "(gap +200.00, over the 1.00 tolerance)"):
            self.assertIn(part, text)
        self.assertNotIn("the statement balance", text)
        # Rows that chain: the balance after the latest row, as ever.
        files["kraken_ledgers_2025.csv"] = files["kraken_ledgers_2025.csv"].replace(
            ",100,0,600,", ",100,0,400,")
        kept = self._kept(files)
        self.assertEqual(self._bals(kept)[1:3], [
            ("2023-12-31", "USD", 300.0), ("2024-12-31", "USD", 300.0)])
        self.assertFalse([e for e in kept if e.get("opens")])

    @rule(R.CA_FX_07)
    def test_a_gap_is_seen_past_a_row_that_states_no_balance(self):
        # (review round 2, P1) The later export's first row, a deposit of
        # 50, states no balance; its next, a deposit of 100, ends at 650:
        # 650 less 100 less 50 is 500 where the earlier export ended at
        # 300. A gap, read at the 500 the wallet held before the blank
        # row. The blank row once switched the comparison off.
        first = _row("LG1", "RG1", "2023-03-02 15:00:00", "deposit", "", "ZUSD", "spot / main",
                     300, 300)
        blank = _row("LG2", "RG2", "2025-03-01 15:00:00", "deposit", "", "ZUSD", "spot / main",
                     50, "")
        kept = self._kept({"kraken_ledgers_2023.csv": first,
                           "kraken_ledgers_2025.csv": blank + _row(
                               "LG3", "RG3", "2025-03-02 15:00:00", "deposit", "", "ZUSD",
                               "spot / main", 100, 650)})
        self.assertEqual(self._bals(kept), [
            ("2022-12-31", "USD", 0.0), ("2023-12-31", "USD", 500.0),
            ("2024-12-31", "USD", 500.0), ("2025-03-02", "USD", 650.0)])
        self.assertTrue(all(e.get("opens") for e in kept if e["kind"] == "CASHBAL"
                            and e["date"] in ("2023-12-31", "2024-12-31")))
        # The same rows in one ledger that chains (300, +50, +100 = 450):
        # no gap, the balance after the latest row that states one.
        kept = self._kept({"kraken_ledgers.csv": first + blank + _row(
            "LG3", "RG3", "2025-03-02 15:00:00", "deposit", "", "ZUSD", "spot / main",
            100, 450)})
        self.assertEqual(self._bals(kept), [
            ("2022-12-31", "USD", 0.0), ("2023-12-31", "USD", 300.0),
            ("2024-12-31", "USD", 300.0), ("2025-03-02", "USD", 450.0)])
        self.assertFalse([e for e in kept if e.get("opens")])

    @rule(R.CA_FX_07)
    def test_a_wallet_is_not_known_before_the_year_end_before_its_first_row(self):
        # CAD from 2023; US dollars from a 2025 row that begins at 500.
        # Nothing says the account held 500 US dollars at the end of
        # 2022 or 2023: no USD balance then, and a NOTE of the row.
        evs = self._events({
            "kraken_ledgers_2023.csv": _row("LC1", "RC1", "2023-03-01 15:00:00", "deposit", "",
                                            "ZCAD", "spot / main", 70, 70),
            "kraken_ledgers_2025.csv": _row("LD9", "RD9", "2025-03-01 15:00:00", "deposit", "",
                                            "ZUSD", "spot / main", 100, 600)})
        kept = CE.dedup_across_files(evs)
        self.assertEqual([b for b in self._bals(kept) if b[1] == "USD"],
                         [("2024-12-31", "USD", 500.0), ("2025-03-01", "USD", 600.0)])
        self.assertEqual([b[0] for b in self._bals(kept) if b[1] == "CAD"],
                         ["2022-12-31", "2023-12-31", "2024-12-31", "2025-03-01"])
        notes = [e["text"] for e in kept if e["kind"] == "NOTE"]
        self.assertEqual(len(notes), 1)
        for part in ("kraken_ledgers_2025.csv:2: the wallet USD, spot / main holds 500.00 USD "
                     "before its first balance (2025-03-01)",
                     "no ledger balance of USD is given before that date"):
            self.assertIn(part, notes[0])

    @rule(R.CA_FX_07)
    def test_a_transfer_to_futures_leaves_the_book_and_withholds_nothing(self):
        # (review P2-2) `spottofutures` is a CASHMOVE out of the book:
        # no wallet of the ledger holds its other side.
        rows = (_row("LD1", "RD1", "2025-03-01 15:00:00", "deposit", "", "ZUSD", "spot / main",
                     1000, 1000)
                + _row("LF1", "RF1", "2025-04-01 15:00:00", "transfer", "spottofutures", "ZUSD",
                       "spot / main", -100, 900)
                + _row("LD2", "RD2", "2026-02-01 15:00:00", "deposit", "", "ZUSD", "spot / main",
                       5, 905))
        evs = self._events({"kraken_ledgers.csv": rows})
        self.assertEqual(self._bals(evs), [("2024-12-31", "USD", 0.0),
                                           ("2025-12-31", "USD", 900.0),
                                           ("2026-02-01", "USD", 905.0)])
        self.assertFalse([e for e in evs if e["kind"] == "NOTE"])
        self.assertIn(("2025-04-01", "USD", -100.0), self._moves(evs))

    @rule(R.CA_FX_07)
    def test_a_legacy_staking_transfer_is_a_move_within_kraken(self):
        # (TJ-5227) The staking wallet is the account's: a transfer
        # between it and the spot wallet is no cash move, as an Earn
        # allocation is none; the two wallets add up. Each side was a
        # CASHMOVE the ledger asked a declaration of. A lone side is a
        # wallet no export covers: no balance from that day, and a NOTE.
        dep = _row("LD1", "RD1", "2025-03-01 15:00:00", "deposit", "", "ZUSD", "spot / main",
                   1000, 1000)
        out = _row("LS1", "RS1", "2025-04-01 15:00:00", "transfer", "spottostaking", "ZUSD",
                   "spot / main", -100, 900)
        into = _row("LS2", "RS2", "2025-04-01 15:00:05", "transfer", "stakingfromspot", "USD.M",
                    "spot / main", 100, 100)
        evs = self._events({"kraken_ledgers.csv": dep + out + into})
        self.assertEqual(self._moves(evs), [("2025-03-01", "USD", 1000.0)])
        self.assertEqual(self._bals(evs), [("2024-12-31", "USD", 0.0),
                                           ("2025-04-01", "USD", 1000.0)])
        self.assertFalse([e for e in evs if e["kind"] == "NOTE"])
        evs = self._events({"kraken_ledgers.csv": dep + out})
        self.assertEqual(self._moves(evs), [("2025-03-01", "USD", 1000.0)])
        self.assertEqual(self._bals(evs), [("2024-12-31", "USD", 0.0)])
        self.assertEqual(len([e for e in evs if e["kind"] == "NOTE"]), 1)

    def test_a_wallet_no_export_covers_gives_no_balance_and_a_note(self):
        # 40 USDC allocated out of the spot wallet, and no export read
        # holds the Earn wallet's row: the spot wallet's 60 is not the
        # account's USD. No CASHBAL of USD from that day on (the opening
        # before it stands), and a NOTE naming the row.
        rows = (TestAWalletIsItsCodeAndItsName.ROWS.rsplit("LA3", 1)[0]
                + _row("LA9", "RA9", "2026-03-06 15:00:00", "deposit", "", "ZCAD",
                       "spot / main", 10, 10))
        evs = self._events({"kraken_ledgers.csv": rows})
        self.assertEqual(self._bals(evs), [("2025-12-31", "CAD", 0.0),
                                           ("2025-12-31", "USD", 0.0),
                                           ("2026-03-06", "CAD", 10.0)])
        notes = [e for e in evs if e["kind"] == "NOTE"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["currency"], "USD")
        for part in ("kraken_ledgers.csv:3: 40.00 USDC moved out of the wallet USDC, "
                     "spot / main on 2026-03-05", "no Kraken ledger export read holds that "
                     "wallet's row", "no ledger balance of USD is given from then on",
                     "Add the ledger export that holds the other wallet's rows"):
            self.assertIn(part, notes[0]["text"])
        # With the Earn wallet's export beside it: the account's balance,
        # and nothing to say.
        both = self._kept({"kraken_ledgers.csv": rows, "kraken_ledgers_earn.csv": _row(
            "LA3", "RA2", "2026-03-05 15:00:00", "earn", "allocation", "USDC",
            "earn / flexible", 40, 40)})
        self.assertFalse([e for e in both if e["kind"] == "NOTE"])
        self.assertIn(("2026-03-06", "USD", 100.0), self._bals(both))


class TestCashEventsCarryTheLedgersIds(_Exports):
    """TJ-5033."""
    D1 = _row("LC1", "RC1", "2026-03-02 12:00:00", "deposit", "", "ZUSD", "spot / main",
              100, 100)
    D2 = _row("LC2", "RC2", "2026-03-02 13:00:00", "deposit", "", "ZUSD", "spot / main",
              100, 200)

    @rule(R.CA_FX_07)
    def test_two_deposits_alike_in_two_exports_are_two(self):
        each = self._events({"kraken_ledgers_a.csv": self.D1, "kraken_ledgers_b.csv": self.D2})
        self.assertEqual(sorted(e["_broker_id"] for e in each if e["kind"] == "CASHMOVE"),
                         ["LC1", "LC2"])
        kept = CE.dedup_across_files(each)
        one = self._kept({"kraken_ledgers.csv": self.D1 + self.D2})
        self.assertEqual(self._moves(kept), [("2026-03-02", "USD", 100.0)] * 2)
        self.assertEqual(self._moves(kept), self._moves(one))
        self.assertEqual(self._bals(kept), self._bals(one))
        self.assertEqual(self._bals(kept), [("2025-12-31", "USD", 0.0),
                                            ("2026-03-02", "USD", 200.0)])

    def test_one_txid_in_overlapping_exports_is_kept_once(self):
        kept = self._kept({"kraken_ledgers_a.csv": self.D1,
                           "kraken_ledgers_b.csv": self.D1 + self.D2})
        self.assertEqual(self._moves(kept), [("2026-03-02", "USD", 100.0)] * 2)
        # The copy kept names the other export's row too (a .tt line's
        # row= may name either).
        first = next(e for e in kept if e.get("_broker_id") == "LC1")
        self.assertEqual(sorted(CE._rows_of(first)),
                         ["kraken_ledgers_a.csv:2", "kraken_ledgers_b.csv:2"])

    def test_a_fee_and_a_trades_events_carry_ids_of_their_own(self):
        rows = (_row("LE1", "RE1", "2026-03-02 12:00:00", "withdrawal", "", "ZUSD",
                     "spot / main", -100, 895, fee=5)
                + _row("LE2", "TE1", "2026-03-03 12:00:00", "trade", "", "ZUSD",
                       "spot / main", -200, 694, fee=1)
                + _row("LE3", "TE1", "2026-03-03 12:00:00", "trade", "", "ZCAD",
                       "spot / main", 280, 280))
        split = {"kraken_ledgers_a.csv": rows.split("LE3", 1)[0],
                 "kraken_ledgers_b.csv": "LE3" + rows.split("LE3", 1)[1]}
        one = self._events({"kraken_ledgers.csv": rows})
        ids = sorted((e["kind"], e["_broker_id"]) for e in one if e.get("_broker_id"))
        self.assertEqual(ids, [("CASHMOVE", "LE1"), ("FLOW", "LE1"), ("FLOW", "TE1:fee:LE2"),
                               ("FXCONV", "TE1:conv")])
        # The conversion's legs in two exports: each gives the trade's
        # events under the same ids, kept once.
        each = self._events(split)
        self.assertEqual(sorted(e["file"] for e in each if e["kind"] == "FXCONV"),
                         sorted(split))
        kept = CE.dedup_across_files(each)
        self.assertEqual(sorted((e["kind"], e["_broker_id"]) for e in kept
                                if e.get("_broker_id")), ids)

    def test_a_hybrid_earn_excess_is_the_same_from_overlapping_exports(self):
        # (review P2-4, TJ-5224) One second holds a parking of 1000 and a
        # return of 1500, their balances blank. Export a lists them in
        # that order, export b (the two rows alone) the other way. Each
        # export walks the copy of the export of the earliest name, so
        # both derive 500 beyond what was parked: one event, no conflict
        # (b's own order once derived 1500 for the same row).
        dep = _row("LD1", "RD1", "2026-03-01 15:00:00", "deposit", "", "ZUSD", "spot / main",
                   2000, 2000)
        park = _row("LH1", "RH1", "2026-03-05 15:00:00", "hybridearnwithdrawal", "", "ZUSD",
                    "spot / main", -1000, "")
        back = _row("LH2", "RH2", "2026-03-05 15:00:00", "hybridearndeposit", "", "ZUSD",
                    "spot / main", 1500, "")
        each = self._events({"kraken_ledgers_a.csv": dep + park + back,
                             "kraken_ledgers_b.csv": back + park})
        over = [(e["file"], e["amount"], e["line_where"]) for e in each
                if e["kind"] == "CASHMOVE" and "over what was parked" in e["desc"]]
        self.assertEqual(over, [("kraken_ledgers_a.csv", 500.0, "kraken_ledgers_a.csv:4"),
                                ("kraken_ledgers_b.csv", 500.0, "kraken_ledgers_b.csv:2")])
        kept = CE.dedup_across_files(each)
        self.assertEqual([e["amount"] for e in kept if e["kind"] == "CASHMOVE"
                          and "over what was parked" in e["desc"]], [500.0])

    def test_a_second_two_exports_share_in_parts_is_read_as_the_one_that_holds_it(self):
        # (review round 2, P2) Export a holds the return of 1500; export
        # b holds the parking of 1000 and the same return. The second's
        # rows are the union of both, each once, read as export b lists
        # them (it holds both): 500 beyond what was parked, as b alone
        # gives. Read each from the export of the earliest name, the
        # return (a's) came before the parking (b's): 1500.
        dep = _row("LD1", "RD1", "2026-03-01 15:00:00", "deposit", "", "ZUSD", "spot / main",
                   2000, 2000)
        park = _row("LH1", "RH1", "2026-03-05 15:00:00", "hybridearnwithdrawal", "", "ZUSD",
                    "spot / main", -1000, "")
        back = _row("LH2", "RH2", "2026-03-05 15:00:00", "hybridearndeposit", "", "ZUSD",
                    "spot / main", 1500, "")

        def over(files):
            return [e["amount"] for e in CE.dedup_across_files(self._events(files))
                    if e["kind"] == "CASHMOVE" and "over what was parked" in e["desc"]]
        self.assertEqual(over({"kraken_ledgers_a.csv": dep + back,
                               "kraken_ledgers_b.csv": park + back}), [500.0])
        self.assertEqual(over({"kraken_ledgers_b.csv": dep + park + back}), [500.0])

    @rule(R.CA_FX_07)
    def test_one_txid_with_two_amounts_in_two_exports_is_refused(self):
        # (TJ-5034's check reads Kraken's ids as it reads Coinbase's.)
        with self.assertRaises(CE.CashIdConflict) as cm:
            self._kept({"kraken_ledgers_a.csv": self.D1,
                        "kraken_ledgers_b.csv": self.D1.replace(",100,0,100,", ",120,0,120,")})
        msg = str(cm.exception)
        for part in ("coins/kraken", "the broker's id LC***", "kraken_ledgers_a.csv:2",
                     "kraken_ledgers_b.csv:2",
                     "the copies differ in the amount: 100.00 in kraken_ledgers_a.csv:2",
                     "against 120.00 in kraken_ledgers_b.csv:2",
                     "keep only the export with the right copy"):
            self.assertIn(part, msg)
        self.assertNotIn("LC1", msg)        # the id is masked


if __name__ == "__main__":
    unittest.main()
