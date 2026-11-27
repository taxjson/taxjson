"""RBC Direct writes no time, so the parser numbers a day's rows from the
bottom of the export (taking it as newest first). An option's own OPEN /
CLOSE CONTRACT words override that guess where they contradict it: per
broker account and series, an OPEN row goes just before a CLOSE row of
the other direction that the position before it cannot back, as many as
the close needs, and nothing else moves (backlog TJ-5348;
lib/brokerages/rbc_direct.py `_order_by_open_close`). A same-day write
read after its buy-back made `tjs option-yield` keep a phantom written
contract open "past expiry", and the account's .sum carry a false
ATTENTION.

The order is proven only where the account's data holds the series' life
from the day moved to its end: its rows (a row two exports hold counted
once, as the run books it) net to nothing, and the exports cover that day
through the expiry row, or through a week past the expiry, with no
trading session between two exports that neither holds (an export's span:
its "Date Range:" line, the end capped by its "Activity Export as of"
date; else its first row to that date; else its rows); and replayed from
nothing held in the new order, every CLOSE finds what it closes. There the
day reordered is the export's note. What was held before the data is
unknown, so anywhere else the day is put in the same order (on a day with
no clock the day's own OPEN rows back a CLOSE first: the owner's ruling
on backlog TJ-5349, which fixes TJ-5363) and the parse says an ATTENTION:
the order rests on the day's own rows, and it names the export to add.

Synthetic data only: the demo export's layout, accounts 99900099 and
99990000, the made-up underlying XYZ.
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.cli_command_runs import _gains_file, _tjs
from _support.rbc_rows import HDR, HDR_NOACCT, row
from _support.rbc_rows import parse_files as _parse_files

COLUMNS = ('Date,Activity,Symbol,Description,Quantity,Price,'
           'Settlement Date,Currency,Value,Amount\n')


def _preamble(span=None, as_of=None):
    """The demo layout's preamble: `span` its "Date Range:" line
    ("January 1, 2025 - June 15, 2025"), `as_of` an "Activity Export as
    of" line above it ("Jan 5, 2026"); neither: the rows alone say what
    the export holds."""
    return ((f'"Activity Export as of {as_of} at 8:59:00 am ET"\n'
             if as_of else '')
            + 'Account Activity Detail Report\n"Account Number: 99900099"\n'
            + (f'"Date Range: {span}"\n' if span else '') + '\n' + COLUMNS)


PREAMBLE = _preamble()
YEAR_2025 = "January 1, 2025 - December 31, 2025"
CALL = "XYZ250620C00050000.US"
PUT = "XYZ250620P00045000.US"
_C = "8XYZAA1,CALL XYZ 06/20/25 50 XYZ CORP"
_P = "8XYZAB1,PUT XYZ 06/20/25 45 XYZ CORP"
NOTE = "rows of %s on 2025-06-06 put in the broker's open/close order"
WORDS = ("(RBC writes no time): the OPEN CONTRACT sale of 3 before the "
         "CLOSE CONTRACT purchase of 3")

# The series' rows as the export lists them (newest first).
LATER = ("12/31/2025,Interest,,INTEREST ON CREDIT BALANCE,0,0,12/31/2025,"
         "USD,1.25,1.25\n")
BUY_0612 = (f"06/12/2025,Buy,{_C} DA CLOSE CONTRACT,3,1.10,06/13/2025,USD,"
            f"-330.00,-342.45\n")
WRITE_0610 = (f"06/10/2025,Sell,{_C} CA OPEN CONTRACT,-3,1.60,06/11/2025,USD,"
              f"480.00,467.55\n")
WRITE_0606 = (f"06/06/2025,Sell,{_C} CA OPEN CONTRACT,-3,1.40,06/09/2025,USD,"
              f"420.00,407.55\n")
BUY_0606 = (f"06/06/2025,Buy,{_C} DA CLOSE CONTRACT,3,0.90,06/09/2025,USD,"
            f"-270.00,-282.45\n")
WRITE_0602 = (f"06/02/2025,Sell,{_C} CA OPEN CONTRACT,-3,1.20,06/03/2025,USD,"
              f"360.00,347.55\n")
FILL_0606 = (f"06/06/2025,Sell,{_C} CA OPEN CONTRACT,-1,1.40,06/09/2025,USD,"
             f"140.00,127.55\n")
EXPIRY_0623 = (f"06/23/2025,Reorganization,{_C.split(',')[0]},EXP - "
               f"{_C.split(',')[1]} OPTION EXPIRATION - EXPIRED,3,,06/23/2025,"
               f"USD,0,0\n")
LONG_0606 = (f"06/06/2025,Buy,{_P} DA OPEN CONTRACT,2,0.80,06/09/2025,USD,"
             f"-160.00,-172.45\n")
SOLD_0606 = (f"06/06/2025,Sell,{_P} CA CLOSE CONTRACT,-2,1.20,06/09/2025,USD,"
             f"240.00,227.55\n")

# The backlog's export: on 2025-06-06 the OPEN write is listed ABOVE the
# CLOSE buy-back; the account held nothing in the series before.
REPRO = BUY_0612 + WRITE_0610 + WRITE_0606 + BUY_0606
EXPORT = LATER + REPRO
# The same rows listed in their real order (the true reading).
TRUE_EXPORT = LATER + BUY_0612 + WRITE_0610 + BUY_0606 + WRITE_0606
MIRROR = LATER + LONG_0606 + SOLD_0606
MIRROR_TRUE = LATER + SOLD_0606 + LONG_0606
# Two overlapping downloads with no Account column: the 06-10 write is in
# both (one row, as the run books it); the 06-06 day in the first only.
OVERLAP = {"rbc_a.csv": WRITE_0610 + WRITE_0606 + BUY_0606,
           "rbc_b.csv": LATER + BUY_0612 + WRITE_0610}
OVERLAP_TRUE = {"rbc_a.csv": WRITE_0610 + BUY_0606 + WRITE_0606,
                "rbc_b.csv": LATER + BUY_0612 + WRITE_0610}


def parse_files(files, header=HDR, country="canada"):
    """The account parsed for a Canadian book: a day no export proves is
    put in open/close order by the country's reading
    (characterize/no_clock_order), not by the parser."""
    return _parse_files(files, header=header, country=country)


def _parse(body, header=PREAMBLE):
    """(transactions, stderr) of one export in the demo layout."""
    txs, err, _ = parse_files({"rbc_2025.csv": body}, header=header)
    return txs, err


def _times(txs, symbol, day="2025-06-06", acct=None):
    """The day's rows of `symbol`, by their clock: [(time, qty, code)]."""
    return sorted((t["time"], t["quantity"], t.get("open_close"))
                  for t in txs if t["symbol"] == symbol and t["date"] == day
                  and (acct is None or t.get("broker_account") == acct))


# The Account-column layout (two broker accounts, overlapping downloads).
A1, A2 = "99900099", "99990000"
OPT = "CALL XYZ 06/20/25 50 XYZ CORP"


def _row(date, activity, qty, price, value, desc, acct, symbol="8XYZAA1"):
    return row(date, activity, symbol, "", qty, price, value, "USD", desc,
               acct=acct)


def _write(date, q, px="1.40", acct=A1):
    return _row(date, "Sell", f"-{q}", px, f"{float(px) * 100 * q - 12.45:.2f}",
                f"{OPT} CA OPEN CONTRACT", acct)


def _buy_back(date, q, px="0.90", acct=A1):
    return _row(date, "Buy", f"{q}", px, f"-{float(px) * 100 * q + 12.45:.2f}",
                f"{OPT} DA CLOSE CONTRACT", acct)


def _interest(date, acct=A1):
    return _row(date, "Interest", "0", "0", "1.25",
                "INTEREST ON CREDIT BALANCE", acct, symbol="")


def _assigned(date, q, acct=A1):
    """The option leg of an assignment and the shares delivered."""
    return (_row(date, "Other", f"{q}", "", "0",
                 f"ASN - {OPT} ASSIGNMENT OF OPTION", acct)
            + _row(date, "Sell", f"-{q * 100}", "50", f"{q * 5000 - 9.95:.2f}",
                   "XYZ CORP ASSIGNMENT OF OPTION AS OF 06/20/25", acct,
                   symbol="XYZ"))


D2, D6, D10, D12 = "June 2, 2025", "June 6, 2025", "June 10, 2025", \
    "June 12, 2025"
DEC = "December 31, 2025"
WROTE_FIRST = [("09:30:00", -3.0, "O"), ("09:30:01", 3.0, "C")]
BOUGHT_FIRST = [("09:30:00", 3.0, "C"), ("09:30:01", -3.0, "O")]
RESTS = "The order rests on that day's own rows"


def _order_lines(err):
    """The parse's lines for a day put in order: (label, text)."""
    return [(ln.split(":", 1)[0].strip(), ln) for ln in err.splitlines()
            if "open/close order" in ln]


class _OrderSaid:
    def proven(self, err, n=1):
        """`n` days put in order, each the export's note (no ATTENTION)."""
        got = _order_lines(err)
        self.assertEqual([k for k, _ln in got], ["note"] * n, err)
        self.assertNotIn(RESTS, err)

    def unproven(self, err, n=1):
        """`n` days put in order on the day's own rows: an ATTENTION that
        names the export to add (backlog TJ-5363)."""
        got = _order_lines(err)
        self.assertEqual([k for k, _ln in got], ["warning"] * n, err)
        for _k, ln in got:
            self.assertIn("warning: ATTENTION: ", ln)
            self.assertIn(RESTS, ln)
            self.assertIn("Add the export covering those dates", ln)


class TestTheDayOrder(_OrderSaid, unittest.TestCase):
    """The parser's clock for a day's rows of one series."""

    def test_the_write_goes_before_its_buy_back(self):
        txs, err = _parse(EXPORT)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        # The other days keep their numbers.
        self.assertEqual(_times(txs, CALL, "2025-06-10"),
                         [("09:30:00", -3.0, "O")])
        self.assertEqual(_times(txs, CALL, "2025-06-12"),
                         [("09:30:00", 3.0, "C")])

    def test_the_note_names_the_rows_moved(self):
        _txs, err = _parse(EXPORT)
        self.assertIn(f"note: rbc_2025.csv: {NOTE % CALL} {WORDS}", err)
        # The 12-31 row proves the export reaches past the expiry: the
        # note names no stated date.
        self.assertNotIn("taken as holding", err)
        # It names what moved, not how the export listed them.
        self.assertNotIn("listed above", err)
        _txs, err = _parse(TRUE_EXPORT)
        self.assertNotIn("open/close order", err)

    def test_the_true_order_is_read_the_same(self):
        def by_clock(body):
            return sorted(_parse(body)[0],
                          key=lambda t: (t["date"], t["time"]))
        self.assertEqual(by_clock(EXPORT), by_clock(TRUE_EXPORT))

    def test_a_long_open_buy_goes_before_its_close_sale(self):
        txs, err = _parse(MIRROR)
        self.assertEqual(_times(txs, PUT), [("09:30:00", 2.0, "O"),
                                            ("09:30:01", -2.0, "C")])
        self.assertIn(f"{NOTE % PUT} (RBC writes no time): the OPEN CONTRACT "
                      f"purchase of 2 before the CLOSE CONTRACT sale of 2",
                      err)

    def test_a_close_the_held_position_backs_keeps_the_file_order(self):
        # The 06-06 buy-back closes the 06-02 write: the bottom row stays
        # first, and the new write after it is closed on 06-12.
        txs, err = _parse(LATER + BUY_0612 + WRITE_0606 + BUY_0606
                          + WRITE_0602)
        self.assertEqual(_times(txs, CALL), BOUGHT_FIRST)
        self.assertNotIn("open/close order", err)

    def test_only_the_rows_the_close_needs_move(self):
        # 1 written on 06-02; the buy-back of 3 on 06-06 needs 2 more:
        # the two fills of 1 (identical rows) go before it, the write of
        # 3 listed above them stays after it.
        write3 = WRITE_0606.replace("-3,1.40", "-3,1.50").replace(
            "420.00,407.55", "450.00,437.55")
        write1 = WRITE_0602.replace("-3,1.20", "-1,1.20").replace(
            "360.00,347.55", "120.00,107.55")
        txs, err = _parse(LATER + BUY_0612 + write3 + FILL_0606 + FILL_0606
                          + BUY_0606 + write1)
        self.assertEqual(_times(txs, CALL), [("09:30:00", -1.0, "O"),
                                             ("09:30:01", -1.0, "O"),
                                             ("09:30:02", 3.0, "C"),
                                             ("09:30:03", -3.0, "O")])
        self.assertIn(f"{NOTE % CALL} (RBC writes no time): the OPEN "
                      f"CONTRACT sales of 1 and 1 before the CLOSE CONTRACT "
                      f"purchase of 3", err)

    def test_a_day_of_one_common_time_gets_the_order(self):
        # Rows out of date order: every day keeps one common time, and the
        # file order (the readers' tie order) put the buy-back first.
        early = ("01/31/2025,Interest,,INTEREST ON CREDIT BALANCE,0,0,"
                 "01/31/2025,USD,0.50,0.50\n")
        txs, err = _parse(BUY_0612 + BUY_0606 + WRITE_0606 + WRITE_0610
                          + LATER + early)
        self.assertIn("same-day rows keep one common time", err)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.assertIn(f"{NOTE % CALL} {WORDS}", err)

    def test_both_directions_of_one_series_on_a_day(self):
        # A long bought and sold, and a write bought back, the same day,
        # every CLOSE read first: each close now finds what it closes.
        long_open = (f"06/06/2025,Buy,{_C} DA OPEN CONTRACT,2,0.80,"
                     f"06/09/2025,USD,-160.00,-172.45\n")
        long_close = (f"06/06/2025,Sell,{_C} CA CLOSE CONTRACT,-2,1.00,"
                      f"06/09/2025,USD,200.00,187.55\n")
        write = (f"06/06/2025,Sell,{_C} CA OPEN CONTRACT,-2,1.40,"
                 f"06/09/2025,USD,280.00,267.55\n")
        buy = (f"06/06/2025,Buy,{_C} DA CLOSE CONTRACT,2,0.90,"
               f"06/09/2025,USD,-180.00,-192.45\n")
        txs, err = _parse(LATER + long_open + long_close + write + buy)
        pos = 0.0
        for _time, q, code in _times(txs, CALL):
            if code == "C":
                self.assertGreaterEqual(-pos if q > 0 else pos, abs(q))
            pos += q
        self.assertEqual(pos, 0.0)
        self.assertIn(NOTE % CALL, err)

    def test_a_close_history_before_the_data_could_back_is_said(self):
        # The rows do not net to nothing (an expiry row of 3 follows): a
        # write before the data could be what the buy-back closed. The
        # day's own write backs it first, and the parse says so; the
        # expiry then finds nothing written and is the one flagged.
        txs, err = _parse(LATER + EXPIRY_0623 + WRITE_0606 + BUY_0606)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.unproven(err)

    def test_the_backlogs_four_rows_need_an_export_reaching_the_expiry(self):
        # The data ends 06-12, before the 06-20 expiry: a write before the
        # data could still be open after it. Put in order on the day's
        # own rows, with an ATTENTION naming the dates to cover.
        txs, err = _parse(REPRO)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.unproven(err)
        self.assertIn(f"do not hold {CALL} from that day through 2025-06-27 "
                      f"(a week past its 2025-06-20 expiry", err)
        # Taken on 2026-01-05, the export holds the expiry and the week
        # after it: the day is put in order.
        txs, err = _parse(REPRO, header=_preamble(as_of="Jan 5, 2026"))
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.proven(err)
        # No row reaches the expiry week: the note says the order rests
        # on the as-of date (a shorter range downloaded late holds less).
        self.assertIn(f"{NOTE % CALL} {WORDS} (the rows end 2025-06-12; "
                      f"taken as holding everything to its as-of date "
                      f"2026-01-05: if the export was asked for a shorter "
                      f"range, add the rest)", err)
        # Taken on 2025-06-25, it can lack the expiry's row: not proven.
        txs, err = _parse(REPRO, header=_preamble(as_of="Jun 25, 2025"))
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.unproven(err)

    def test_a_gap_between_the_exports_is_said(self):
        pair = WRITE_0606 + BUY_0606
        later = {
            # over the expiry: the second export starts in September
            "over the expiry": ("09/30/2025,Interest,,INTEREST ON CREDIT "
                                "BALANCE,0,0,09/30/2025,USD,0.75,0.75\n"),
            # between the day and the expiry: from 06-16 on
            "after the day": ("06/16/2025,Interest,,INTEREST ON CREDIT "
                              "BALANCE,0,0,06/16/2025,USD,0.75,0.75\n")}
        for name, first in later.items():
            with self.subTest(name):
                txs, err, _ = parse_files({"a.csv": pair,
                                           "b.csv": LATER + first},
                                          header=PREAMBLE)
                self.assertEqual(_times(txs, CALL), WROTE_FIRST)
                self.unproven(err)

    def test_an_as_of_date_does_not_stretch_a_date_range(self):
        # Asked for January 1 to June 15 and taken on 2026-01-05: nothing
        # after June 15 is in the file, so neither is the 06-20 expiry.
        # Alone, or with a September-to-December export: not proven.
        pair = WRITE_0606 + BUY_0606
        early = _preamble("January 1, 2025 - June 15, 2025",
                          "Jan 5, 2026") + pair
        late = (_preamble("September 1, 2025 - December 31, 2025") + LATER
                + "09/30/2025,Interest,,INTEREST ON CREDIT BALANCE,0,0,"
                  "09/30/2025,USD,0.75,0.75\n")
        for files in ({"a.csv": early}, {"a.csv": early, "b.csv": late}):
            with self.subTest(sorted(files)):
                txs, err, _ = parse_files(files, header="")
                self.assertEqual(_times(txs, CALL), WROTE_FIRST)
                self.unproven(err)
        # A Date Range through the year is the export's span though its
        # last row is the 06-06 one: proven, the export's note.
        txs, err = _parse(pair, header=_preamble(YEAR_2025))
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.proven(err)
        # ... on that stated end, the note says (a range asked for ahead
        # of the download holds less).
        self.assertIn(f"{NOTE % CALL} {WORDS} (the rows end 2025-06-06; "
                      f"taken as holding everything to its Date Range end "
                      f"2025-12-31: if the export was taken before then, "
                      f"add the rest)", err)
        # The same range taken on 2025-06-25 ends there: not proven.
        txs, err = _parse(pair, header=_preamble(YEAR_2025, "Jun 25, 2025"))
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.unproven(err)

    def test_consecutive_exports_join(self):
        # A January 2026 call written and bought back on 2025-12-10 in the
        # 2025 export; the 2026 export holds its expiry week.
        jan = "8XYZBA1,CALL XYZ 01/16/26 50 XYZ CORP"
        y25 = (LATER
               + f"12/10/2025,Sell,{jan} CA OPEN CONTRACT,-3,1.40,12/11/2025,"
                 f"USD,420.00,407.55\n"
               + f"12/10/2025,Buy,{jan} DA CLOSE CONTRACT,3,0.90,12/11/2025,"
                 f"USD,-270.00,-282.45\n")
        jan30 = ("01/30/2026,Interest,,INTEREST ON CREDIT BALANCE,0,0,"
                 "01/30/2026,USD,1.25,1.25\n")
        jan02 = ("01/02/2026,Interest,,INTEREST ON CREDIT BALANCE,0,0,"
                 "01/02/2026,USD,0.25,0.25\n")
        cases = {
            # the Date Range lines: December 31, then January 1
            "ranges": ({"a.csv": _preamble(YEAR_2025) + y25,
                        "b.csv": _preamble("January 1, 2026 - December 31, "
                                           "2026") + jan30 + jan02}, True),
            # the rows alone: 12-31, then 01-02, the next session
            "rows": ({"a.csv": PREAMBLE + y25,
                      "b.csv": PREAMBLE + jan30 + jan02}, True),
            # the rows alone, the 2026 ones from 01-30: the sessions
            # between are a gap
            "a gap in the rows": ({"a.csv": PREAMBLE + y25,
                                   "b.csv": PREAMBLE + jan30}, False)}
        # (`moved`: the exports join, so the order is proven)
        for name, (files, moved) in cases.items():
            with self.subTest(name):
                txs, err, _ = parse_files(files, header="")
                got = _times(txs, "XYZ260116C00050000.US", "2025-12-10")
                self.assertEqual(got, WROTE_FIRST)
                (self.proven if moved else self.unproven)(err)

    def test_a_close_larger_than_the_days_opens_takes_them_first(self):
        # A buy-back of 3, a write of 2 that day and 1 four days later: no
        # order backs the whole close from nothing held. The day's write
        # of 2 backs it first (the books report the third contract by
        # hand), and the parse says what the order rests on.
        txs, err, _ = parse_files(
            {"a.csv": _interest(DEC) + _write(D10, 1, "1.60") + _write(D6, 2)
             + _buy_back(D6, 3)}, header=HDR)
        self.assertEqual(_times(txs, CALL), [("09:30:00", -2.0, "O"),
                                             ("09:30:01", 3.0, "C")])
        self.unproven(err)

    def test_two_broker_accounts_in_one_export(self):
        # A1 needs the order; A2's buy-back closes its own 06-02 write.
        body = (_interest(DEC) + _interest(DEC, A2) + _buy_back(D12, 3, acct=A2)
                + _write(D6, 3) + _write(D6, 3, acct=A2) + _buy_back(D6, 3)
                + _buy_back(D6, 3, acct=A2) + _write(D2, 3, acct=A2))
        txs, err, _ = parse_files({"a.csv": body}, header=HDR)
        a1, a2 = _times(txs, CALL, acct=A1), _times(txs, CALL, acct=A2)
        self.assertEqual([(q, c) for _t, q, c in a1], [(-3.0, "O"), (3.0, "C")])
        self.assertEqual([(q, c) for _t, q, c in a2], [(3.0, "C"), (-3.0, "O")])
        self.assertEqual(err.count("open/close order"), 1)

    def test_the_position_comes_from_every_export_of_the_account(self):
        # The day is in one export, the series' close and the row past its
        # expiry in the account's other (overlapping: the 06-10 write is
        # in both, and is one row).
        first = _write(D10, 3, "1.60") + _write(D6, 3) + _buy_back(D6, 3)
        second = _interest(DEC) + _buy_back(D12, 3, "1.10") \
            + _write(D10, 3, "1.60")
        txs, err, _ = parse_files({"a.csv": first, "b.csv": second},
                                  header=HDR)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.assertEqual(len(_times(txs, CALL, "2025-06-10")), 1)
        self.assertIn(f"note: a.csv: {NOTE % CALL}", err)
        # Alone, the first export holds neither the close nor the end:
        # the same order, not proven.
        txs, err, _ = parse_files({"a.csv": first}, header=HDR)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.unproven(err)

    def test_overlapping_exports_without_an_account_column(self):
        # The parser passes the 06-10 write on twice (the run's dedup
        # books it once): counted once, the series nets to nothing.
        txs, err, _ = parse_files(OVERLAP, header=PREAMBLE)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.assertEqual(len(_times(txs, CALL, "2025-06-10")), 2)
        self.assertIn(f"note: rbc_a.csv: {NOTE % CALL}", err)

    def test_a_day_in_two_exports_keeps_the_file_order(self):
        # No Account column: the day is in both downloads, each copy of
        # it read as its file lists it (a renumbered copy would no longer
        # be the other's duplicate).
        day = _write(D6, 3, acct=None) + _buy_back(D6, 3, acct=None)
        txs, err, _ = parse_files(
            {"a.csv": _write(D10, 3, "1.60", acct=None) + day,
             "b.csv": _interest(DEC, None) + _buy_back(D12, 3, "1.10", None)
             + _write(D10, 3, "1.60", acct=None) + day}, header=HDR_NOACCT)
        self.assertEqual(_times(txs, CALL), sorted(BOUGHT_FIRST * 2))
        self.assertNotIn("open/close order", err)

    def test_a_day_with_an_assignment_row_keeps_the_file_order(self):
        # 06-20: a write and its buy-back, and the 06-02 write assigned.
        d20 = "June 20, 2025"
        txs, err, _ = parse_files(
            {"a.csv": _interest(DEC) + _assigned(d20, 1) + _write(d20, 3)
             + _buy_back(d20, 3) + _write(D2, 1, "1.20")}, header=HDR)
        trades = [(q, c) for _t, q, c in _times(txs, CALL, "2025-06-20")
                  if abs(q) == 3]
        self.assertEqual(trades, [(3.0, "C"), (-3.0, "O")])
        self.assertNotIn("open/close order", err)

    def test_a_series_ended_by_an_assignment(self):
        txs, err, _ = parse_files(
            {"a.csv": _interest(DEC) + _assigned("June 20, 2025", 3)
             + _write(D10, 3, "1.60") + _write(D6, 3) + _buy_back(D6, 3)},
            header=HDR)
        self.assertEqual(_times(txs, CALL), WROTE_FIRST)
        self.assertIn(NOTE % CALL, err)


def _project(tmp, name, files):
    root = Path(tmp) / name
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\ncountry = "canada"\nlocal_timezone = "America/Toronto"\n'
        'year = 2025\nbase_currency = "CAD"\nsource_currencies = ["USD"]\n'
        'option_grant_timing_since = 2024\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text("")
    if isinstance(files, str):
        files = {"rbc_2025.csv": files}
    for fname, body in files.items():
        (root / "inputs" / "margin" / fname).write_text(PREAMBLE + body)
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return root


class TestTheRun(unittest.TestCase):
    """`tjs run` and its readers on the backlog's export, the true order,
    the mirror case and overlapping downloads: one reading of the trades
    everywhere."""

    _tmp: tempfile.TemporaryDirectory[str]
    root: Path
    true: Path
    mirror: Path
    mirror_true: Path
    overlap: Path
    overlap_true: Path

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.root = _project(cls._tmp.name, "listed", EXPORT)
        cls.true = _project(cls._tmp.name, "true", TRUE_EXPORT)
        cls.mirror = _project(cls._tmp.name, "mirror", MIRROR)
        cls.mirror_true = _project(cls._tmp.name, "mirror_true", MIRROR_TRUE)
        cls.overlap = _project(cls._tmp.name, "overlap", OVERLAP)
        cls.overlap_true = _project(cls._tmp.name, "overlap_true",
                                    OVERLAP_TRUE)

    def _json(self, root, *args):
        r = _tjs(root, *args, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _yield(self, *args):
        r = _tjs(self.root, "option-yield", "XYZ", "all", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_option_yield_has_two_round_trips_and_nothing_open(self):
        doc = json.loads(self._yield("--json"))
        self.assertEqual(doc["open"], [])
        trips = [(c["opened"], c["closed"], c["contracts"], c["how"])
                 for c in doc["closed"]]
        self.assertEqual(trips, [("2025-06-06", "2025-06-06", 3.0,
                                  "bought back"),
                                 ("2025-06-10", "2025-06-12", 3.0,
                                  "bought back")])
        self.assertTrue(all(c["premium"] is not None for c in doc["closed"]))

    def test_option_yield_gives_no_past_expiry_hint(self):
        text = self._yield()
        self.assertNotIn("past expiry", text)
        self.assertNotIn("opened before the data", text)

    def test_no_attention_in_the_account_sum(self):
        for root in (self.root, self.mirror):
            self.assertNotIn("ATTENTION",
                             (root / "reports" / "margin.sum").read_text())
        # The overlap's one ATTENTION is the run's dedup of the 06-10 row
        # two downloads hold (booked once); none for the open/close code.
        text = (self.overlap / "reports" / "margin.sum").read_text()
        self.assertEqual(text.count("ATTENTION"), 1)
        self.assertIn("ATTENTION: dedup:", text)
        self.assertNotIn("open/close code", text)

    def test_the_gains_file_is_the_true_readings(self):
        for listed, true in ((self.root, self.true),
                             (self.mirror, self.mirror_true),
                             (self.overlap, self.overlap_true)):
            self.assertEqual(json.loads(_gains_file(listed).read_text()),
                             json.loads(_gains_file(true).read_text()))

    def test_the_figures_docs_upgrading_names(self):
        # On a reordered day the count of dispositions, the gain's rounding
        # cent, the ACB, T1135's maximum cost and the FX-on-cash overdraft
        # can move, each to what the rows in their true order give (the
        # old readings: 3 dispositions, max cost 381.31 and a 282.45 USD
        # overdraft for the backlog's export; 2 dispositions, a gain of
        # 74.38, ACB 232.81 and no T1135 row for the mirror).
        want = {"listed": (4, 337.77, 843.61, {}, {}),
                "mirror": (1, 74.39, 232.8, {"USA": 232.81},
                           {"USD": {"count": 1, "units": 172.45}})}
        for listed, true in ((self.root, self.true),
                             (self.mirror, self.mirror_true)):
            got = []
            for root in (listed, true):
                filing = self._json(root, "sum")["filing"]
                acct = filing["accounts"][0]
                t1135 = self._json(root, "t1135")["by_country"]
                got.append((acct["dispositions"], acct["gain"], acct["acb"],
                            {c: v["max_cost"] for c, v in t1135.items()},
                            filing["fx_cash"]["overdrafts_in_year"]))
            self.assertEqual(got[0], want[listed.name])
            self.assertEqual(got[0], got[1])


if __name__ == "__main__":
    unittest.main()
