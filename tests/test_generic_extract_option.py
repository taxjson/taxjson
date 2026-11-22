"""Recommendation 9's second tier-1 gap: `[extract] option`, an option
written as text (`CALL ZZQ 03/21/25 5.00`) read as its contract. The
four questions: which cell is which (the symbol cell, then the
description; the named groups root, expiry, right, strike), what money
it may carry (an option trade's, the OCC symbol's size checks), what
the no-op is (no match, or a target that books no symbol: the cells as
before), and the shared shape (`format_occ_symbol`, the broker parsers')."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from _tmpfiles import isolate_layouts
from taxjson.lib.brokerages.generic import GenericBrokerage

isolate_layouts()

PATTERN = (r"^(?P<right>CALL|PUT) (?P<root>[A-Z.]+) "
           r"(?P<expiry>\d\d/\d\d/\d\d) (?P<strike>[\d.,]+)$")
COLUMNS = """\
[columns]
date = "Date"
action = "Action"
symbol = "Symbol"
description = "Description"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"
"""
ACTIONS = """
[actions]
"BUY" = "buy"
"SELL" = "sell"
"DIV" = "dividend"
"""
HEADER = "Date,Action,Symbol,Description,Quantity,Price,Amount,Currency\n"


def _toml(pattern=PATTERN, columns=COLUMNS, formats=""):
    return (columns + formats + ACTIONS
            + "\n[extract]\noption = '" + pattern + "'\n")


class TestExtractOption(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def parse(self, rows, toml=None, header=HEADER):
        csv = self.dir / "activity.csv"
        csv.write_text(header + rows)
        csv.with_name(csv.name + ".toml").write_text(toml or _toml())
        with contextlib.redirect_stderr(io.StringIO()):
            return [(t["action"], t["symbol"], t["quantity"], t["net_amount"])
                    for t in GenericBrokerage().parse_file(csv)]

    def refused(self, rows, toml=None, header=HEADER):
        with self.assertRaises(ValueError) as cm:
            self.parse(rows, toml, header)
        return str(cm.exception)

    def test_the_description_names_the_contract(self):
        got = self.parse(
            "2025-01-06,BUY,ZZQ,CALL ZZQ 03/21/25 5.00,1,0.50,-50.00,USD\n"
            "2025-02-10,SELL,ZZQ,CALL ZZQ 03/21/25 5.00,1,0.80,80.00,USD\n"
            "2025-02-11,BUY,ZZQ,ZZQ COMMON,10,4.00,-40.00,USD\n")
        self.assertEqual(got, [
            ("BUYSELL", "ZZQ250321C00005000.US", 1.0, 50.0),
            ("BUYSELL", "ZZQ250321C00005000.US", -1.0, 80.0),
            # No match: the symbol cell, as before.
            ("BUYSELL", "ZZQ.US", 10.0, 40.0)])

    def test_the_symbol_cell_holds_the_text(self):
        # No description column: the pattern reads the symbol cell, the
        # text refused there before as "an option DESCRIPTION".
        header = "Date,Action,Symbol,Quantity,Price,Amount,Currency\n"
        columns = COLUMNS.replace('description = "Description"\n', "")
        rows = "2025-01-06,BUY,PUT ZZQ 03/21/25 1250.5,1,2.00,-200.00,CAD\n"
        self.assertIn("an [extract] option pattern",
                      self.refused(rows, columns + ACTIONS, header))
        self.assertEqual(self.parse(rows, _toml(columns=columns), header),
                         [("BUYSELL", "ZZQ250321P01250500.TO", 1.0, 200.0)])

    def test_the_expiry_format(self):
        pattern = (r"^(?P<root>[A-Z]+) (?P<expiry>\d\d[A-Z]{3}\d\d) "
                   r"(?P<strike>[\d.]+) (?P<right>[CP])$")
        toml = _toml(pattern, formats='\n[formats]\noption_expiry = "%d%b%y"\n')
        self.assertEqual(
            self.parse("2025-01-06,BUY,ZZQ,ZZQ 21MAR25 5 C,2,0.25,-50.00,USD\n", toml),
            [("BUYSELL", "ZZQ250321C00005000.US", 2.0, 50.0)])
        self.assertIn("does not match [formats].option_expiry = '%d%b%y'",
                      self.refused("2025-01-06,BUY,ZZQ,ZZQ 31FEB25 5 C,2,0.25,-50.00,USD\n",
                                   toml))

    def test_what_is_refused(self):
        # A right the pattern lets through but that is no right.
        loose = PATTERN.replace("CALL|PUT", "[A-Z]+")
        self.assertIn("option right 'CALLS' is not C, P, CALL or PUT",
                      self.refused("2025-01-06,BUY,ZZQ,CALLS ZZQ 03/21/25 5,1,0.5,-50.00,USD\n",
                                   _toml(loose)))
        # A strike the OCC field cannot hold, and a symbol cell naming
        # another contract.
        self.assertIn("strike", self.refused(
            "2025-01-06,BUY,ZZQ,CALL ZZQ 03/21/25 0,1,0.5,-50.00,USD\n"))
        self.assertIn("name two contracts", self.refused(
            "2025-01-06,BUY,ZZQ250321C00006000,CALL ZZQ 03/21/25 5.00,1,0.5,-50.00,USD\n"))
        # The same contract in both cells is one.
        self.assertEqual(self.parse(
            "2025-01-06,BUY,ZZQ250321C00005000,CALL ZZQ 03/21/25 5.00,1,0.5,-50.00,USD\n"),
            [("BUYSELL", "ZZQ250321C00005000.US", 1.0, 50.0)])

    def test_the_pattern_is_checked_at_load(self):
        msg = self.refused("2025-01-06,BUY,ZZQ,x,1,1,-1.00,USD\n",
                           _toml(PATTERN.replace("?P<strike>", "")))
        self.assertIn("has no named group (?P<strike>...)", msg)
        header = "Date,Action,Quantity,Price,Amount,Currency\n"
        columns = "".join(ln + "\n" for ln in COLUMNS.splitlines()
                          if not ln.startswith(("symbol", "description")))
        self.assertIn("neither [columns].symbol nor [columns].description",
                      self.refused("2025-01-06,BUY,1,1,-1.00,USD\n",
                                   _toml(columns=columns)
                                   + '\n[defaults]\nsymbol = "ZZQ"\n', header))

    def test_a_row_that_books_no_contract(self):
        # A dividend books its symbol cell: a description that reads as
        # a contract does not make the dividend an option's.
        got = self.parse("2025-03-03,DIV,ZZQ,CALL ZZQ 03/21/25 5.00,,,1.50,USD\n")
        self.assertEqual([(a, s) for a, s, _q, _n in got], [("DIVIDEND", "ZZQ.US")])

    def test_an_expiry_is_a_zero_close_of_the_contract(self):
        got = self.parse(
            "2025-01-06,BUY,ZZQ,CALL ZZQ 03/21/25 5.00,1,0.50,-50.00,USD\n"
            "2025-03-21,SELL,ZZQ,CALL ZZQ 03/21/25 5.00,1,0,0,USD\n")
        self.assertEqual(got[1], ("BUYSELL", "ZZQ250321C00005000.US", -1.0, 0.0))

    def test_a_stock_leg_naming_its_option(self):
        # The review's case (TJ-2641): a pattern with no anchors matches
        # an assignment's stock leg too. The money tells: 100 x 5.00 is
        # the shares', not 100 contracts at 5.00.
        loose = PATTERN.strip("^$")
        leg = "2025-03-21,BUY,ZZQ,ASSIGNED PUT ZZQ 03/21/25 5.00,100,5.00,-500.00,USD\n"
        self.assertEqual(self.parse(leg, _toml(loose)),
                         [("BUYSELL", "ZZQ.US", 100.0, 500.0)])
        self.assertEqual(self.parse(
            "2025-01-06,BUY,ZZQ,BOUGHT CALL ZZQ 03/21/25 5.00,2,0.50,-100.00,USD\n",
            _toml(loose)), [("BUYSELL", "ZZQ250321C00005000.US", 2.0, 100.0)])
        # No price to tell: refused, unless the match is the whole cell.
        no_price = leg.replace(",5.00,-500", ",,-500")
        self.assertIn("no price and amount (both non-zero)", self.refused(no_price, _toml(loose)))
        self.assertEqual(self.parse(no_price), [("BUYSELL", "ZZQ.US", 100.0, 500.0)])
        self.assertEqual(self.parse(
            "2025-01-06,BUY,ZZQ,CALL ZZQ 03/21/25 5.00,1,,-50.00,USD\n"),
            [("BUYSELL", "ZZQ250321C00005000.US", 1.0, 50.0)])

    def test_the_root(self):
        # A class share's '-' or '/' is a dot (TJ-2642); a root no OCC
        # symbol can begin with is refused, never booked as shares.
        wide = PATTERN.replace("[A-Z.]+", "[A-Z./-]+")
        self.assertEqual(self.parse(
            "2025-01-06,BUY,,CALL ZZQ-B 03/21/25 5.00,1,0.50,-50.00,USD\n", _toml(wide)),
            [("BUYSELL", "ZZQ.B250321C00005000.US", 1.0, 50.0)])
        self.assertIn("cannot begin an OCC symbol", self.refused(
            "2025-01-06,BUY,,CALL ZZQABCDEFGHI 03/21/25 5.00,1,0.50,-50.00,USD\n"))

    def test_a_refusal_names_the_row(self):
        # TJ-2643: a decimal-comma strike, named with its row and source.
        msg = self.refused('2025-01-06,BUY,ZZQ,"CALL ZZQ 03/21/25 2,50",1,0.5,-50.00,USD\n')
        self.assertIn("activity.csv line 2", msg)
        self.assertIn("[extract].option", msg)
        self.assertIn("decimal comma", msg)

    def test_an_explicit_listing_is_kept(self):
        # TJ-2644: the symbol cell's listing wins over the currency's.
        desc = ",CALL ZZQ 03/21/25 5.00,1,0.50,-50.00,CAD\n"
        for cell in ("ZZQ250321C00005000.US", "ZZQ.US"):
            self.assertEqual(self.parse("2025-01-06,BUY," + cell + desc),
                             [("BUYSELL", "ZZQ250321C00005000.US", 1.0, 50.0)])
        self.assertEqual(self.parse("2025-01-06,BUY,ZZQ" + desc),
                         [("BUYSELL", "ZZQ250321C00005000.TO", 1.0, 50.0)])

    def test_an_export_with_no_symbol_column(self):
        # TJ-2645: the description alone names the contract.
        header = "Date,Action,Description,Quantity,Price,Amount,Currency\n"
        columns = COLUMNS.replace('symbol = "Symbol"\n', "")
        self.assertEqual(self.parse(
            "2025-01-06,BUY,CALL ZZQ 03/21/25 5.00,1,0.50,-50.00,USD\n",
            _toml(columns=columns), header),
            [("BUYSELL", "ZZQ250321C00005000.US", 1.0, 50.0)])

    def test_the_expiry_year(self):
        # TJ-2647: an OCC symbol holds 2000 to 2099; a format naming no
        # year is refused at load.
        four = PATTERN.replace(r"\d\d/\d\d/\d\d", r"\d\d/\d\d/\d{4}")
        toml = _toml(four, formats='\n[formats]\noption_expiry = "%m/%d/%Y"\n')
        self.assertIn("holds the years 2000 to 2099", self.refused(
            "2025-01-06,BUY,ZZQ,CALL ZZQ 01/15/1999 5.00,1,0.50,-50.00,USD\n", toml))
        self.assertIn("must name the expiry's year", self.refused(
            "2025-01-06,BUY,ZZQ,x,1,1,-1.00,USD\n",
            _toml(formats='\n[formats]\noption_expiry = "%m/%d"\n')))

    def test_the_money_that_tells(self):
        # The second review (TJ-2651 to TJ-2653): only a price and an
        # amount both non-zero tell the contract from the shares; a mini's
        # 10 is a contract's size too.
        loose = _toml(PATTERN.strip("^$"))
        self.assertIn("no price and amount (both non-zero)", self.refused(
            "2025-03-21,BUY,ZZQ,ASSIGNED PUT ZZQ 03/21/25 5.00,100,0,0,USD\n", loose))
        self.assertIn("no price and amount (both non-zero)", self.refused(
            "2025-03-21,SELL,ZZQ,EXERCISED CALL ZZQ 03/21/25 5.00,1,5.00,0,USD\n", loose))
        self.assertEqual(self.parse(
            "2025-01-06,BUY,ZZQ,BOUGHT CALL ZZQ 03/21/25 5.00,1,0.50,-5.00,USD\n", loose),
            [("BUYSELL", "ZZQ250321C00005000.US", 1.0, 5.0)])

    def test_the_tickers_listing_and_relation(self):
        # TJ-2654, TJ-2655, TJ-2656: a known listing suffix alone is kept
        # (a class share's or a USD series' letter is no listing), an
        # adjusted root keeps the ticker's listing, another ticker is refused.
        desc = ",CALL ZZQ 03/21/25 5.00,1,0.50,-50.00,"
        self.assertEqual(self.parse("2025-01-06,BUY,ZZQ.B" + desc + "CAD\n"),
                         [("BUYSELL", "ZZQ250321C00005000.TO", 1.0, 50.0)])
        self.assertEqual(self.parse("2025-01-06,BUY,ZZQ.U" + desc + "USD\n"),
                         [("BUYSELL", "ZZQ250321C00005000.US", 1.0, 50.0)])
        self.assertEqual(self.parse(
            "2025-01-06,BUY,ZZQ.US,CALL ZZQ1 03/21/25 5.00,1,0.50,-50.00,CAD\n",
            _toml(PATTERN.replace("[A-Z.]+", "[A-Z0-9.]+"))),
            [("BUYSELL", "ZZQ1250321C00005000.US", 1.0, 50.0)])
        self.assertIn("name two securities",
                      self.refused("2025-01-06,BUY,QZRX" + desc + "USD\n"))

    def test_the_symbol_cells_contract(self):
        # TJ-2657, TJ-2658: a lower-case OCC cell is a contract, and a
        # futures option's prefix is no other contract.
        self.assertIn("name two contracts", self.refused(
            "2025-01-06,BUY,zzq250418c00005000,CALL ZZQ 03/21/25 5.00,1,0.50,-50.00,USD\n"))
        msg = self.refused(
            "2025-01-06,BUY,F:ZZQ250321C00005000,CALL ZZQ 03/21/25 5.00,1,0.50,,USD\n")
        self.assertNotIn("two contracts", msg)

    def test_a_row_the_pattern_alone_names(self):
        # TJ-2659: with the pattern the one symbol source, a row it does
        # not match is refused, not booked with no security.
        header = "Date,Action,Description,Quantity,Price,Amount,Currency\n"
        columns = COLUMNS.replace('symbol = "Symbol"\n', "")
        self.assertIn("names no security", self.refused(
            "2025-01-06,BUY,ZZQ COMMON SHARES,10,4.00,-40.00,USD\n",
            _toml(columns=columns), header))

    def test_the_symbol_builders_refusal_names_the_pattern(self):
        # TJ-2660: a strike no OCC symbol can spell.
        loose = _toml(PATTERN.replace(r"[\d.,]+", r"\S+"))
        self.assertIn("[extract].option", self.refused(
            "2025-01-06,BUY,ZZQ,CALL ZZQ 03/21/25 abc,1,0.50,-50.00,USD\n", loose))


if __name__ == "__main__":
    unittest.main()
