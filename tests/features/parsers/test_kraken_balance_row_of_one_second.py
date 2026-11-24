"""Which row of one second a Kraken ledger balance is read from (backlog
TJ-4951, review round 2 of the Hybrid Earn work): the cash events' CASHBAL
is the `balance` after the LATEST row. Rows of one second were taken in
the file's lines; an export listed newest first then ended a second at
its first row's balance. The file's order is now the order the export
lists its rows in read oldest first: its lines, from the last up where
it lists newest first. And the order walked is the balance column's
when it settles one: every row of the wallet in that second chained from
the wallet's balance before it (the balance after the second before; one
of that second's balances where its own order is not known) or to a
balance the next second begins at, or, with no balance on either side,
among themselves, every such chain ending at one balance, and that chain
either the file's order or anchored on both sides (the rows then account
for the whole difference between the two balances).
Otherwise the file's order stays, as it was (rows that come back to the
balance they began at chain two ways; a second only half in the export
chains from no balance beside it), and the balance's `where` says so
where another order would give another balance. It stays, and is said,
where the rows chain in an order that is not the file's and one balance
only anchors them: a second that lacks a row chains so too, to a balance
that reconciles and is wrong (review round 4), and a chain of the file's
own that the balance before contradicts may follow a row missing before
the second (review round 3). The gap must stay loud.

None of these ledgers has a Hybrid Earn row. All data synthetic.
"""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import timezones as TZ

H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance,"
     "amountusd,feeusd,feecurrency\n")


def _row(txid, time, typ, amount, balance, fee=0):
    return (f"{txid},R{txid},{time},{typ},,currency,ZUSD,spot / main,{amount},{fee},"
            f"{balance},{abs(amount)},{fee},USD\n")


NOTED = "whose balances do not say which came last: "
# What the note says was read, by case: the export's lines from the first
# down or from the last up; no direction (the wallet's rows are in no one
# order of time, or share one time); the fractions of a second.
UP = NOTED + ("the file's order was taken, its lines from the first down (the export lists "
              "this wallet's rows oldest first). Where that is not their true order, "
              "listing the second's rows in it settles it; where it is, a row is missing "
              "from the export before or among them)")
DOWN = NOTED + ("the file's order was taken, its lines from the last up (the export lists "
                "this wallet's rows newest first). Where")
NO_WAY = NOTED + ("the export does not say which way it lists this wallet's rows (they are "
                  "in no one order of time, or share one time), so the order their balances "
                  "chain in was taken where the lines, read down or up, chain, and the "
                  "lines' order where they do not. Sorting the export's rows by time, "
                  "oldest first or newest first throughout, settles it; where the second's "
                  "rows still do not chain then, a row is missing from the export before or "
                  "among them)")
FRACTIONS = NOTED + ("the order of the fractions of a second they state was taken, which is "
                     "not the order their balances chain in. Where")


def _events(path):
    """The export's CASHBAL events, parsed alone in its folder."""
    from taxjson.parsers.registry import parser_class
    from taxjson.parsers.settings import ParseSettings
    parser = parser_class("kraken")()
    parser.configure(ParseSettings.for_country("canada"))
    with patch.object(TZ, "local_tz_name", return_value="America/Toronto"), \
            contextlib.redirect_stderr(io.StringIO()):
        parser.parse_file(Path(path))
    assert parser.cash_problem in (None, ""), parser.cash_problem
    return [e for e in parser.cash_events if e["kind"] == "CASHBAL"]


def _bals(path):
    """The export's CASHBAL figures by date."""
    return {e["date"]: e["balance"] for e in _events(path)}


class TestTheBalanceRowOfOneSecond(unittest.TestCase):
    def _path(self, rows):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "kraken_ledgers.csv"
        path.write_text(H + "".join(rows), encoding="utf-8")
        return path

    def _bals_of(self, rows):
        return _bals(self._path(rows))

    def _noted(self, rows):
        """The dates whose balance says the file's order was taken."""
        return [e["date"] for e in _events(self._path(rows)) if NOTED in e["where"]]

    def _usd(self, rows, said=None):
        """The export's US-dollar balances by date; with `said`, those
        whose `where` holds it (and that carry `unsure`)."""
        evs = [e for e in _events(self._path(rows)) if e["currency"] == "USD"]
        if said is None:
            return {e["date"]: e["balance"] for e in evs}
        self.assertEqual([e["date"] for e in evs if e.get("unsure")],
                         [e["date"] for e in evs if NOTED in e["where"]])
        return [e["date"] for e in evs if said in e["where"]]

    # 1000 in; then in one second 100 out (900) and 200 out (700).
    DEPOSIT = _row("L1", "2026-01-05 15:00:00", "deposit", 1000, 1000)
    FIRST = _row("L2", "2026-02-10 15:00:00", "withdrawal", -100, 900)
    LAST = _row("L3", "2026-02-10 15:00:00", "withdrawal", -200, 700)

    def test_an_export_listed_newest_first_ends_the_second_at_its_last_row(self):
        # Newest first, the second's last row is listed before its first:
        # the file's order took 900 for the balance at the end. Chained
        # from the 1000 before the second, the last row is the 700 one.
        for name, rows in (("oldest first", (self.DEPOSIT, self.FIRST, self.LAST)),
                           ("newest first", (self.LAST, self.FIRST, self.DEPOSIT))):
            with self.subTest(name):
                self.assertEqual(self._bals_of(rows),
                                 {"2025-12-31": 0.0, "2026-02-10": 700.0})

    def test_a_fee_in_the_asset_is_part_of_the_chain(self):
        # 100 out with a fee of 2: 1000 -> 898, then 200 out -> 698.
        rows = (_row("L3", "2026-02-10 15:00:00", "withdrawal", -200, 698),
                _row("L2", "2026-02-10 15:00:00", "withdrawal", -100, 898, fee=2),
                self.DEPOSIT)
        self.assertEqual(self._bals_of(rows)["2026-02-10"], 698.0)

    # One second that comes back to the balance it began at: 50 out
    # (150 -> 100), then 50 in (100 -> 150).
    OUT = _row("L3", "2026-03-10 15:00:00", "withdrawal", -50, 100)
    IN = _row("L4", "2026-03-10 15:00:00", "deposit", 50, 150)

    def test_a_blank_balance_before_the_second_leaves_the_files_order(self):
        # 100 in, then 50 in whose `balance` is blank: the wallet holds
        # 150 and the last balance the export states is 100, stale. A
        # chain begun at that 100 reads the second as in first, out last,
        # ending at 100; the file lists it out first, in last: 150, which
        # is right. No known balance before the second, no chain: the
        # file's order.
        rows = (_row("L1", "2026-01-05 15:00:00", "deposit", 100, 100),
                _row("L2", "2026-02-10 15:00:00", "deposit", 50, ""),
                self.OUT, self.IN)
        self.assertEqual(self._bals_of(rows)["2026-03-10"], 150.0)

    def test_a_second_only_half_in_the_export_leaves_the_files_order(self):
        # The export holds 100, then two rows of a second whose first
        # row (50 in, 100 -> 150) it lacks: out (150 -> 100) and in
        # (100 -> 150), then a later row that begins at 150. Chained from
        # the 100 the two would read in first, out last, ending at 100;
        # the row after them begins at 150, so that chain is not the
        # second's, and the file's order (ending at 150) stays.
        rows = (_row("L1", "2025-11-05 15:00:00", "deposit", 100, 100),
                self.OUT.replace("2026-03-10", "2025-12-30"),
                self.IN.replace("2026-03-10", "2025-12-30"),
                _row("L5", "2026-01-15 15:00:00", "deposit", 20, 170))
        self.assertEqual(self._bals_of(rows),
                         {"2024-12-31": 0.0, "2025-12-31": 150.0, "2026-01-15": 170.0})

    def test_the_wallets_first_second_is_anchored_on_the_rows_after_it(self):
        # Newest first again, the wallet's FIRST second two rows: 1000 in
        # (0 -> 1000), 100 out (-> 900); then 50 out (900 -> 850) the
        # next year. No balance before the first second, but the row
        # after it begins at 900: the second ended there, so it began at
        # 0. The file's order opened at 1000 and closed 2025 at 1000.
        rows = (_row("L3", "2026-01-15 15:00:00", "withdrawal", -50, 850),
                _row("L2", "2025-12-30 15:00:00", "withdrawal", -100, 900),
                _row("L1", "2025-12-30 15:00:00", "deposit", 1000, 1000))
        self.assertEqual(self._bals_of(rows),
                         {"2024-12-31": 0.0, "2025-12-31": 900.0, "2026-01-15": 850.0})
        # And a first second that returns to the balance it began at
        # (out 150 -> 100, in 100 -> 150), then a second of two rows
        # (20 out -> 130, 30 out -> 100): the first ends where one of the
        # next second's rows begins, 150, and that second chains from it.
        rows = (_row("L6", "2026-04-10 15:00:00", "withdrawal", -30, 100),
                _row("L5", "2026-04-10 15:00:00", "withdrawal", -20, 130),
                self.IN, self.OUT)
        self.assertEqual(self._bals_of(rows), {"2025-12-31": 150.0, "2026-04-10": 100.0})

    def test_a_wallets_only_second_is_chained_among_its_own_rows(self):
        # The wallet's only second, listed newest first: 1000 in
        # (0 -> 1000), 100 out (-> 900), 200 out (-> 700). No balance
        # before or after, but the three rows chain one way only, so the
        # second began at 0 and ended at 700 (the file's order: 900 and
        # 1000). Nothing to say.
        rows = (self.LAST, self.FIRST, self.DEPOSIT)
        self.assertEqual(self._bals_of(rows), {"2025-12-31": 0.0, "2026-02-10": 700.0})
        self.assertEqual(self._noted(rows), [])

    def test_a_stale_balance_before_the_second_does_not_reorder_a_chain_of_the_file(self):
        # 2000 held; a deposit of 500 that the export lacks; then in one
        # second 500 out (2500 -> 2000) and 500 in (2000 -> 2500), listed
        # in that order. The file's order is a chain of its own, ending
        # at 2500. Chained from the stale 2000 the second would read in
        # first, out last, and end at 2000: the missing 500 hidden and
        # the ledger reconciled (review round 3). The file's order
        # stays, so the statement balance is 2500 and the gap is loud;
        # the balance says which order was taken.
        rows = (_row("L1", "2026-01-05 15:00:00", "deposit", 2000, 2000),
                _row("L3", "2026-03-01 15:00:00", "withdrawal", -500, 2000),
                _row("L4", "2026-03-01 15:00:00", "deposit", 500, 2500))
        self.assertEqual(self._bals_of(rows)["2026-03-01"], 2500.0)
        self.assertEqual(self._noted(rows), ["2026-03-01"])
        # With a row after the second that begins at 2000, the file's
        # order (ending at 2500) is contradicted on that side: the chain
        # from the balance before, which ends at 2000, is the order.
        rows += (_row("L5", "2026-04-01 15:00:00", "deposit", 1, 2001),)
        self.assertEqual(self._bals_of(rows)["2026-04-01"], 2001.0)
        self.assertEqual(self._noted(rows), [])

    # 800 in; then in ONE second 200 in (-> 1000), 200 out (-> 800), 300
    # out (-> 500), 300 in (-> 800). The wallet ends at 800.
    A_DEPOSIT = _row("L1", "2026-01-20 15:00:00", "deposit", 800, 800)
    A_SECOND = (_row("L2", "2026-03-13 15:00:00", "deposit", 200, 1000),
                _row("L3", "2026-03-13 15:00:00", "withdrawal", -200, 800),
                _row("L4", "2026-03-13 15:00:00", "withdrawal", -300, 500),
                _row("L5", "2026-03-13 15:00:00", "deposit", 300, 800))

    def test_a_row_missing_inside_a_second_is_not_chained_into_another_order(self):
        # The export lacks the second's 200 out. The three rows left
        # chain from the 800 before them in another order (300 out, 300
        # in, 200 in) to 1000: a balance 200 over the wallet's, which
        # the ledger's moves (the missing one not among them) reconcile
        # to. That chain is not the order the export lists the rows in
        # and nothing after the second anchors it: the file's order is
        # read, ending at the 800 of the row that came last, and the
        # balance says so (review round 4). In both listings: an export
        # listed newest first is read from its last line up.
        lacking = (self.A_DEPOSIT,) + self.A_SECOND[:1] + self.A_SECOND[2:]
        for name, rows in (("oldest first", lacking), ("newest first", lacking[::-1])):
            with self.subTest(name):
                self.assertEqual(self._bals_of(rows)["2026-03-13"], 800.0)
                self.assertEqual(self._noted(rows), ["2026-03-13"])
        # The whole second, in either listing: its own order chains, and
        # nothing is said.
        whole = (self.A_DEPOSIT,) + self.A_SECOND
        for name, rows in (("oldest first", whole), ("newest first", whole[::-1])):
            with self.subTest(whole=name):
                self.assertEqual(self._bals_of(rows)["2026-03-13"], 800.0)
                self.assertEqual(self._noted(rows), [])

    def test_a_chain_against_the_listing_is_taken_when_both_sides_anchor_it(self):
        # The second's rows listed in another order than they happened
        # in (200 out, 100 out for 1000 -> 900 -> 700), a row after them
        # beginning at 700: the rows account for the whole difference
        # between the 1000 before and the 700 after, so no row is
        # missing among them and the balance column's order is taken,
        # with nothing to say. Without the row after, the listing and
        # the chain disagree and one balance only anchors the chain:
        # the file's order (ending at 900), said.
        rows = (self.DEPOSIT, self.LAST, self.FIRST)
        self.assertEqual(self._bals_of(rows)["2026-02-10"], 900.0)
        self.assertEqual(self._noted(rows), ["2026-02-10"])
        rows += (_row("L4", "2026-04-01 15:00:00", "deposit", 50, 750),)
        self.assertEqual(self._bals_of(rows), {"2025-12-31": 0.0, "2026-04-01": 750.0})
        self.assertEqual(self._noted(rows), [])

    def test_a_stale_balance_before_the_second_in_an_export_listed_newest_first(self):
        # The stale-balance case above with the export listed newest
        # first: the second's rows happened in the order of its lines
        # from the last up, 500 out (2500 -> 2000) then 500 in (-> 2500),
        # a chain of its own that the stale 2000 before it contradicts.
        # Read from the first line down, the two rows chain from that
        # 2000 and hid the missing deposit (review round 4, P2-1).
        rows = (_row("L4", "2026-03-01 15:00:00", "deposit", 500, 2500),
                _row("L3", "2026-03-01 15:00:00", "withdrawal", -500, 2000),
                _row("L1", "2026-01-05 15:00:00", "deposit", 2000, 2000))
        self.assertEqual(self._bals_of(rows)["2026-03-01"], 2500.0)
        self.assertEqual(self._noted(rows), ["2026-03-01"])
        # And the whole export listed newest first, the same second
        # after a 500 deposit it holds: nothing to say.
        rows = rows[:2] + (_row("L2", "2026-02-01 15:00:00", "deposit", 500, 2500),) + rows[2:]
        self.assertEqual(self._bals_of(rows)["2026-03-01"], 2500.0)
        self.assertEqual(self._noted(rows), [])

    def test_rows_of_one_second_that_state_fractions_are_in_their_order(self):
        # Backlog TJ-5028: a newest-first export lists a withdrawal at
        # 12:00:00.200 (-5, balance 105) before a deposit at 12:00:00.100
        # (+10, balance 110). The fraction was dropped and the pair read
        # in the file's order: opening and closing both 110. The rows
        # are walked by the instant they state: opening 100, closing 105.
        rows = (_row("L2", "2026-02-10 12:00:00.200", "withdrawal", -5, 105),
                _row("L1", "2026-02-10 12:00:00.100", "deposit", 10, 110))
        self.assertEqual(self._bals_of(rows), {"2025-12-31": 100.0, "2026-02-10": 105.0})
        self.assertEqual(self._noted(rows), [])
        # A pair that comes back to the balance it began at chains both
        # ways, and the fractions still say which row came last: 10 out
        # (100 -> 90) at .100, 10 in (90 -> 100) at .200, in either
        # listing.
        pair = (_row("L1", "2026-02-10 12:00:00.100", "withdrawal", -10, 90),
                _row("L2", "2026-02-10 12:00:00.200", "deposit", 10, 100))
        for rows in (pair, pair[::-1]):
            with self.subTest(first=rows[0][:2]):
                self.assertEqual(self._bals_of(rows),
                                 {"2025-12-31": 100.0, "2026-02-10": 100.0})
                self.assertEqual(self._noted(rows), [])
        # The same pair of the audit's with no fraction stated, the
        # export's only rows: nothing says which way the export lists
        # its rows. The two chain one way, the lines read from the last
        # up: taken (round 4 read the lines down: 110 and 110), and
        # said, since no other row confirms that the export lists
        # newest first.
        rows = (_row("L2", "2026-02-10 12:00:00", "withdrawal", -5, 105),
                _row("L1", "2026-02-10 12:00:00", "deposit", 10, 110))
        self.assertEqual(self._usd(rows), {"2025-12-31": 100.0, "2026-02-10": 105.0})
        self.assertEqual(self._usd(rows, NO_WAY), ["2025-12-31", "2026-02-10"])

    def test_fractions_against_the_balances_do_not_win_in_silence(self):
        # 100 held; in one second a deposit (100 -> 110) and a withdrawal
        # (110 -> 105), by their balances in that order; the fractions
        # they state put the withdrawal first. As the wallet's last
        # second the fractions' order is read (110) and the balance says
        # that the balances chain another way (review round 5: the
        # fractions won with nothing said).
        first = _row("L0", "2026-01-05 12:00:00.0000", "deposit", 100, 100)
        wd = _row("L1", "2026-03-13 12:00:00.1000", "withdrawal", -5, 105)
        dep = _row("L2", "2026-03-13 12:00:00.2000", "deposit", 10, 110)
        rows = (first, wd, dep)
        self.assertEqual(self._usd(rows)["2026-03-13"], 110.0)
        self.assertEqual(self._usd(rows, FRACTIONS), ["2026-03-13"])
        # With a row after the second that begins at 105, both balances
        # hold the chain: it is walked, and the later balance is right.
        rows += (_row("L9", "2026-04-01 12:00:00.0000", "deposit", 1, 106),)
        self.assertEqual(self._usd(rows), {"2025-12-31": 0.0, "2026-04-01": 106.0})
        # A row that states no fraction beside one that does: the
        # fractions order nothing (a bare 12:00:00 is not 12:00:00.000),
        # and the export's own order, which chains, is read: 105.
        rows = (first, _row("L1", "2026-03-13 12:00:00.5000", "deposit", 10, 110),
                _row("L2", "2026-03-13 12:00:00", "withdrawal", -5, 105))
        self.assertEqual(self._usd(rows)["2026-03-13"], 105.0)
        self.assertEqual(self._usd(rows, NOTED), [])

    # 500 in 2025; 300 in (-> 800); then in ONE second 200 in (-> 1000)
    # and 300 out (-> 700). Each export below lists its rows oldest
    # first inside; its first and last rows say otherwise.
    Y25 = (_row("L1", "2025-06-01 15:00:00", "deposit", 500, 500),)
    Y26 = (_row("L2", "2026-01-20 15:00:00", "deposit", 300, 800),
           _row("L3", "2026-03-13 15:00:00", "deposit", 200, 1000),
           _row("L4", "2026-03-13 15:00:00", "withdrawal", -300, 700))
    CAD = ("L9,RL9,2025-02-01 15:00:00,deposit,,currency,ZCAD,spot / main,50,0,50,36,0,CAD\n",)

    def test_an_export_sorted_by_asset_lists_each_wallet_its_own_way(self):
        # The US-dollar rows oldest first, then a Canadian-dollar row of
        # the year before: the export's first row is later than its last,
        # which read the whole export as listed newest first and walked
        # every second from the last line up: 1000 where the wallet holds
        # 700, a gap with a remedy that did not apply (review round 5).
        # The way an export lists a wallet's rows is read from every
        # pair of that wallet's rows: oldest first. 700, nothing said.
        rows = self.Y25 + self.Y26 + self.CAD
        self.assertEqual(self._usd(rows), {"2024-12-31": 0.0, "2025-12-31": 500.0,
                                           "2026-03-13": 700.0})
        self.assertEqual(self._usd(rows, NOTED), [])
        # And listed newest first inside, the Canadian row first.
        rows = self.CAD + (self.Y25 + self.Y26)[::-1]
        self.assertEqual(self._usd(rows)["2026-03-13"], 700.0)
        self.assertEqual(self._usd(rows, NOTED), [])
        # A wallet whose only rows share one second says nothing of its
        # own: it takes the way the export's other wallets are listed
        # (two Canadian rows, oldest first, after it).
        cad2 = self.CAD + (self.CAD[0].replace("L9", "L10").replace("2025-02-01", "2025-03-01")
                           .replace(",50,0,50,", ",50,0,100,"),)
        rows = self.Y26[1:] + cad2
        # (The US-dollar wallet's first row, in 2026, begins at 800: its
        # balance is known from the end of 2025, not at the end of 2024,
        # where the Canadian rows open the account: backlog TJ-5032.)
        self.assertEqual(self._usd(rows), {"2025-12-31": 800.0, "2026-03-13": 700.0})
        self.assertEqual(self._usd(rows, NOTED), [])

    def test_an_export_in_no_one_order_of_time_gives_no_direction(self):
        # Two exports pasted, the later year first; and an export with
        # one earlier row appended. The wallet's rows go both ways in
        # time, so the export does not say which way it lists them. The
        # second's rows chain as the lines read down and not as they
        # read up: that order is taken (700, as before round 4), and the
        # balance says that no direction was known and what settles it.
        late = (_row("L8", "2026-01-02 15:00:00", "deposit", 500, 500),)
        for name, rows in (("pasted", self.Y26 + self.Y25), ("a late row", self.Y26 + late)):
            with self.subTest(name):
                self.assertEqual(self._usd(rows)["2026-03-13"], 700.0)
                self.assertEqual(self._usd(rows, NO_WAY), ["2026-03-13"])
        # A second that the balance before it and the balance after it
        # both hold needs no direction: nothing to say.
        rows = self.Y26 + (_row("L5", "2026-05-01 15:00:00", "deposit", 100, 800),) + self.Y25
        self.assertEqual(self._usd(rows)["2026-05-01"], 800.0)
        self.assertEqual(self._usd(rows, NOTED), [])
        # An export listed newest first with one newer row appended: the
        # same, the second held on both sides.
        rows = (self.Y25 + self.Y26)[::-1] + (
            _row("L8", "2026-04-01 15:00:00", "deposit", 100, 800),)
        self.assertEqual(self._usd(rows)["2026-04-01"], 800.0)
        self.assertEqual(self._usd(rows, NOTED), [])
        # No direction and rows that chain neither down nor up (the
        # second lacks its 200 in): the lines' order, said.
        rows = self.Y26[:1] + self.Y26[2:] + (
            _row("L6", "2026-03-13 15:00:00", "deposit", 50, 750),) + self.Y25
        self.assertEqual(self._usd(rows)["2026-03-13"], 750.0)
        self.assertEqual(self._usd(rows, NO_WAY), ["2026-03-13"])

    def test_an_export_whose_rows_share_one_second_gives_no_direction(self):
        # The export's only rows, 200 in (800 -> 1000) and 300 out
        # (-> 700), listed either way: they chain one way, which is
        # taken (800 before them, 700 after), and both balances say
        # that the export states no direction.
        for rows in (self.Y26[1:], self.Y26[1:][::-1]):
            with self.subTest(first=rows[0][:2]):
                self.assertEqual(self._usd(rows), {"2025-12-31": 800.0, "2026-03-13": 700.0})
                self.assertEqual(self._usd(rows, NO_WAY), ["2025-12-31", "2026-03-13"])

    def test_a_deposit_and_its_conversion_every_month_are_read_as_listed(self):
        # A recurring buy from the wallet's first row on: each month one
        # second holds a deposit of 100 (0 -> 100) and the spend of all
        # of it (100 -> 0). Every second comes back to the balance it
        # began at, so it chains both ways, and the balances the next
        # second's rows begin at (0 and 100) fit both: each balance was
        # said "not sure", on an ordinary export in time order (review
        # round 6). The export says which way it lists the rows, the
        # listed order chains, and it ends where the next second's
        # listed first row begins: taken, nothing to say. Both listings.
        rows = ()
        for n, day in enumerate(("2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05")):
            rows += (_row(f"D{n}", f"{day} 15:00:00", "deposit", 100, 100),
                     _row(f"S{n}", f"{day} 15:00:00", "withdrawal", -100, 0))
        for name, listed in (("oldest first", rows), ("newest first", rows[::-1])):
            with self.subTest(name):
                self.assertEqual(self._usd(listed), {"2025-12-31": 0.0, "2026-04-05": 0.0})
                self.assertEqual(self._usd(listed, NOTED), [])
        # Listed the other way inside each second (the spend before its
        # deposit, oldest first): each second chains that way too, to
        # where the next begins, and nothing in the export says
        # otherwise: 100 before the first row and after the last.
        other = tuple(r for a, b in zip(rows[::2], rows[1::2]) for r in (b, a))
        self.assertEqual(self._usd(other), {"2025-12-31": 100.0, "2026-04-05": 100.0})
        # A wallet whose only two rows share one second and come back to
        # the balance they began at, in an export whose other rows say it
        # lists oldest first: the listed order (150 out, then in).
        cad = ("L8,RL8,2025-02-01 15:00:00,deposit,,currency,ZCAD,spot / main,50,0,50,36,0,CAD\n",
               "L9,RL9,2025-03-01 15:00:00,deposit,,currency,ZCAD,spot / main,50,0,100,36,0,CAD\n")
        pair = cad + (self.OUT, self.IN)
        self.assertEqual(self._usd(pair)["2026-03-10"], 150.0)
        self.assertEqual(self._usd(pair, NOTED), [])

    def test_a_wallets_only_second_that_chains_two_ways_keeps_the_files_order(self):
        # No balance before the export's only second and none after it,
        # and its rows come back to the balance they began at: its order
        # is the file's (the opening balance is the one before the row
        # listed first, the closing one after the row listed last), as
        # before, and both balances say so.
        for rows, opening, closing in (((self.OUT, self.IN), 150.0, 150.0),
                                       ((self.IN, self.OUT), 100.0, 100.0)):
            with self.subTest(first=rows[0][:2]):
                self.assertEqual(self._bals_of(rows),
                                 {"2025-12-31": opening, "2026-03-10": closing})
                self.assertEqual(self._usd(rows, NO_WAY), ["2025-12-31", "2026-03-10"])


class TestThroughTheLedger(unittest.TestCase):
    """The same through `tjs run` and `tjs fx-cash --ledger v2`."""

    HEAD = H + (
        "L01,R01,2026-01-05 15:00:00,deposit,,currency,ZCAD,spot / main,5000,0,5000,3600,0,CAD\n"
        "L02,RT1,2026-01-12 15:00:00,spend,,currency,ZCAD,spot / main,-2800,0,2200,-2000,0,CAD\n"
        "L03,RT1,2026-01-12 15:00:00,receive,,currency,ZUSD,spot / main,2000,0,2000,2000,0,USD\n")
    OUT = "L05,R05,2026-03-01 15:00:00,withdrawal,,currency,ZUSD,spot / main,-500,0,{0},500,0,USD\n"
    IN = "L06,R06,2026-03-01 15:00:00,deposit,,currency,ZUSD,spot / main,500,0,{0},500,0,USD\n"
    TT = "CASHMOVE 2026-03-01 USD -500 kept\nCASHMOVE 2026-03-01 USD 500 cost=680.00\n"

    def _fx(self, ledger, tt=TT):
        from taxjson.lib.dispatch import python_module_argv
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "proj"
        (root / "inputs" / "crypto").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\n'
            'country = "canada"\nprovince = "ON"\nbase_currency = "CAD"\n'
            'source_currencies = ["USD"]\n'
            '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n', encoding="utf-8")
        (root / "inputs" / "crypto" / "kr_ledgers.csv").write_text(ledger, encoding="utf-8")
        (root / "inputs" / "crypto" / "cash.tt").write_text(tt, encoding="utf-8")
        os.chmod(root, 0o700)
        out = ""
        for args in (("run",), ("fx-cash", "--ledger", "v2")):
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run", ["-C", str(root), *args]),
                cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=900, env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                                      NO_COLOR="1", TAXJSON_LOCAL_TZ="America/Toronto"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = r.stdout
        return out

    def test_a_row_missing_before_a_second_the_file_chains_stays_a_gap(self):
        # The stale-balance case above: the ledger walks 2000, the
        # statement says 2500, and the year is not computed.
        out = self._fx(self.HEAD + self.OUT.format(2000) + self.IN.format(2500))
        self.assertIn("NOT COMPUTED", out)
        self.assertIn(
            "crypto/kraken USD: the statement balance at the end of 2026-03-01 is 2,500.00, "
            "the ledger's 2,000.00 (gap +500.00, over the 1.00 tolerance)", out)
        self.assertIn("where it is, a row is missing from the export before or among them", out)
        self.assertIn(NOTED, out)

    def test_a_row_missing_inside_a_second_stays_a_gap_in_both_listings(self):
        # 2000 held; in one second 200 in (-> 2200), 200 out (-> 2000),
        # 300 out (-> 1700), 300 in (-> 2000), of which the export lacks
        # the 200 out. The moves it holds add up to 2200; the wallet
        # holds 2000. The rows left chain from the 2000 before them to
        # 2200 in another order, which reconciled (review round 4, P1):
        # the file's order is read, the statement balance is the 2000 of
        # the row that came last, and the year is not computed, the gap
        # line saying what was read.
        second = ("L05,R05,2026-03-01 15:00:00,deposit,,currency,ZUSD,spot / main,200,0,2200,200,0,USD\n"
                  "L07,R07,2026-03-01 15:00:00,withdrawal,,currency,ZUSD,spot / main,-300,0,1700,300,0,USD\n"
                  "L08,R08,2026-03-01 15:00:00,deposit,,currency,ZUSD,spot / main,300,0,2000,300,0,USD\n")
        tt = ("CASHMOVE 2026-03-01 USD 200 spot\nCASHMOVE 2026-03-01 USD -300 kept\n"
              "CASHMOVE 2026-03-01 USD 300 spot\n")
        rows = (self.HEAD + second).splitlines(True)
        for name, text in (("oldest first", "".join(rows)),
                           ("newest first", rows[0] + "".join(reversed(rows[1:])))):
            with self.subTest(name):
                out = self._fx(text, tt)
                self.assertIn("NOT COMPUTED", out)
                self.assertIn(
                    "crypto/kraken USD: the statement balance at the end of 2026-03-01 is "
                    "2,000.00, the ledger's 2,200.00 (gap -200.00, over the 1.00 tolerance)",
                    out)
                self.assertIn(NOTED, out)
                self.assertIn("a row is missing from the export before or among them", out)

    def test_a_balance_read_in_file_order_that_reconciles_is_a_warning(self):
        # 2000 held; in one second 100 in (-> 2100), 200 out (-> 1900),
        # 100 in (-> 2000), listed as 200 out, 100 in (-> 2100), 100 in
        # (-> 2000): not the order they chain in, with only the balance
        # before them. The file's order is read; its last row's balance
        # is the 2000 the ledger walks to, so the year is computed. No
        # gap line quotes what was read: a Warning says it, since a
        # balance that agrees by another order of those rows is the
        # check a missing row slips through (review round 4).
        a = "L05,R05,2026-03-01 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,2100,100,0,USD\n"
        b = ("L06,R06,2026-03-01 15:00:00,withdrawal,,currency,ZUSD,spot / main,-200,0,1900,"
             "200,0,USD\n")
        c = "L07,R07,2026-03-01 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,2000,100,0,USD\n"
        tt = ("CASHMOVE 2026-03-01 USD 100 spot\nCASHMOVE 2026-03-01 USD -200 kept\n"
              "CASHMOVE 2026-03-01 USD 100 spot\n")
        out = self._fx(self.HEAD + b + a + c, tt)
        self.assertNotIn("NOT COMPUTED", out)
        self.assertIn(
            "warning: crypto/kraken USD: the statement balance at the end of 2026-03-01 "
            "(2,000.00) reconciles, and may not be the balance at the end of that day "
            "(kr_ledgers.csv: ledger balance (read from rows that share one second and whose "
            "balances do not say which came last: the file's order was taken", out)
        # Listed in the order they happened in: nothing to say.
        out = self._fx(self.HEAD + a + b + c, tt)
        self.assertNotIn("NOT COMPUTED", out)
        self.assertNotIn("arning", out)

    def test_exports_whose_first_and_last_rows_misstate_their_order(self):
        # 2000 held; 100 in (-> 2100); in one second 200 in (-> 2300)
        # and 300 out (-> 2000); the rows oldest first.
        rows = (
            "L04,R04,2026-02-01 15:00:00,deposit,,currency,ZUSD,spot / main,100,0,2100,100,0,USD\n"
            "L05,R05,2026-03-01 15:00:00,deposit,,currency,ZUSD,spot / main,200,0,2300,200,0,USD\n"
            "L06,R06,2026-03-01 15:00:00,withdrawal,,currency,ZUSD,spot / main,-300,0,2000,300,0,"
            "USD\n")
        tt = ("CASHMOVE 2026-02-01 USD 100 spot\nCASHMOVE 2026-03-01 USD 200 spot\n"
              "CASHMOVE 2026-03-01 USD -300 kept\n")
        # Sorted by asset: the US-dollar rows, then the Canadian-dollar
        # ones, whose last is earlier than the export's first row. Read
        # as listed newest first, the second ended at 2300 and the year
        # was NOT COMPUTED with a remedy that did not apply (review
        # round 5). The US-dollar rows say oldest first: computed, and
        # nothing to say.
        head = self.HEAD[len(H):].splitlines(True)
        cad = "L00,R00,2026-01-02 15:00:00,deposit,,currency,ZCAD,spot / main,50,0,50,36,0,CAD\n"
        out = self._fx(H + head[2] + rows + head[0] + head[1] + cad, tt)
        self.assertNotIn("NOT COMPUTED", out)
        self.assertNotIn("arning", out)
        self.assertRegex(out, r"2026-03-01  crypto/kraken  USD\s+2,000\.00\s+2,000\.00\s+0\.00")
        # Two exports pasted, the later rows first: the US-dollar rows
        # go both ways in time, so the export states no direction. The
        # second's rows chain from the 2100 before them as the lines
        # read down, which is taken: computed, and a Warning says that
        # no direction was known and what settles it.
        out = self._fx(H + rows + "".join(head), tt)
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"2026-03-01  crypto/kraken  USD\s+2,000\.00\s+2,000\.00\s+0\.00")
        self.assertIn(
            "warning: crypto/kraken USD: the statement balance at the end of 2026-03-01 "
            "(2,000.00) reconciles, and may not be the balance at the end of that day "
            "(kr_ledgers.csv: ledger balance (read from rows that share one second and "
            + NO_WAY + ")", out)

    def test_listing_the_seconds_rows_oldest_first_settles_a_second_read_in_file_order(self):
        # The wallet's last second comes back to the 2000 it began at:
        # 500 out (-> 1500), then 500 in (-> 2000). Listed newest first
        # it is a chain of its own the other way (2000 -> 2500 -> 2000
        # read as in, then out, ending at 1500), which only a row
        # missing before it would explain, and nothing after it says
        # otherwise: the file's order is taken, the statement balance is
        # 1500 against the ledger's 2000, and the gap line says what was
        # read and how to settle it. Listed oldest first, it reconciles.
        newest = self._fx(self.HEAD + self.IN.format(2000) + self.OUT.format(1500))
        self.assertIn("NOT COMPUTED", newest)
        self.assertIn(
            "the statement balance at the end of 2026-03-01 is 1,500.00, the ledger's "
            "2,000.00", newest)
        self.assertIn(
            "(kr_ledgers.csv: ledger balance (read from rows that share one second and "
            + UP + ")", newest)
        oldest = self._fx(self.HEAD + self.OUT.format(1500) + self.IN.format(2000))
        self.assertNotIn("NOT COMPUTED", oldest)
        self.assertRegex(oldest, r"2026-03-01  crypto/kraken  USD\s+2,000\.00\s+2,000\.00\s+0\.00")
        self.assertNotIn(NOTED, oldest)


if __name__ == "__main__":
    unittest.main()
