"""A Questrade fetch file that holds one trade twice, the second row
equal in every cell but a blank Symbol (backlog TJ-4955).

`tjs fetch` before the fix kept a re-fetched activity whose symbol came
back blank as a second row. The parser reads the blank row's option
contract from its description and books both: 4 contracts sold become 8,
the premium and the cash twice. The parser drops nothing (the file alone
cannot tell a copy from a second fill): it says ATTENTION, naming the
pairs' lines (the first three, the rest counted) and the fix, and `tjs
run` shows it at its end. The text holds for every pair: "where both
rows of a pair are booked" (a pair with no cash and no units books
neither; a share trade's blank copy is refused further on).

A blank Symbol alone is normal (a fee, an FX conversion, a deposit): no
ATTENTION without a named row equal in every other cell. All data
synthetic (the made-up ticker SYN).
"""
import tempfile
import unittest

from taxjson.lib.brokerages.questrade import blank_symbol_twins
from _support.cli_margin_project import _cli_run, _project
from _support.qt_parse import QH, qrow, qt_parse

_F = "questrade_2025.csv"
_OPT = "SYN16Jan26C130.00"
_DESC = "CALL SYN 01/16/26 130 SYN CORP WE ACTED AS AGENT"
_TWIN = "differ only by a blank Symbol: probably one trade fetched twice"


def _sale(sym=_OPT, **kw):
    base = dict(td='2025-03-03', action='Sell', sym=sym, desc=_DESC, qty='-4',
                price='2.25', gross='900.00', comm='-4.00', net='896.00',
                cur='CAD', atype='API')
    base.update(kw)
    return qrow(**base)


def _buy(**kw):
    base = dict(td='2025-02-03', action='Buy', sym='SYN', desc='SYN CORP',
                qty='10', price='10.00', gross='-100.00', comm='0.00',
                net='-100.00', cur='CAD', atype='API')
    base.update(kw)
    return qrow(**base)


def _fee(**kw):
    base = dict(td='2025-03-04', action='FCH', sym='', desc='ADMIN FEE',
                qty='0', price='0', gross='0', comm='0', net='-5.00',
                cur='CAD', act='Fees and rebates', atype='API')
    base.update(kw)
    return qrow(**base)


def _fx(net, cur):
    return qrow(td='2025-03-05', action='FXT', sym='', desc='FX CONVERSION',
                qty='0', price='0', gross='0', comm='0', net=net, cur=cur,
                act='FX conversion', atype='API')


def _run(root):
    """`tjs run`: (what it printed, its closing list)."""
    run = _cli_run(root, "run", "--no-input")
    if run.returncode:
        raise AssertionError(run.stdout[-1500:] + run.stderr[-1500:])
    return (run.stdout + run.stderr,
            run.stdout[run.stdout.index("==> Before you trust"):])


def _parse(body):
    return qt_parse({_F: body})


def _pairs(body):
    """`blank_symbol_twins` of the rows as the parser reads them."""
    import csv
    import io
    rows = list(enumerate(csv.DictReader(io.StringIO(QH + body)), 2))
    return blank_symbol_twins(rows)


class TestTheTwinIsSaid(unittest.TestCase):

    def test_the_named_row_first(self):
        txs, err = _parse(_buy() + _sale() + _fee() + _sale(sym=''))
        self.assertIn(
            f"warning: ATTENTION: [end of run] questrade_2025.csv: lines 3 "
            f"and 5 {_TWIN} (2025-03-03 Sell {_OPT}; the blank one is line "
            f"5). Where both rows of a pair are booked, the position, the "
            f"cash and the gain or loss count the trade twice. Run `tjs "
            f"fetch` over those dates again: it drops the blank row when "
            f"Questrade's download holds the trade once. If the pair "
            f"stays, check the broker's statement: delete the blank row "
            f"when it shows one trade, keep both when it shows two. Then "
            f"`tjs run`.", err)
        self.assertEqual(err.count(_TWIN), 1)

    def test_the_blank_row_first(self):
        _txs, err = _parse(_sale(sym='') + _buy(td='2025-03-03') + _sale())
        self.assertIn(f"lines 4 and 2 {_TWIN} (2025-03-03 Sell {_OPT}; the "
                      f"blank one is line 2).", err)

    def test_nothing_is_dropped(self):
        """Both rows stay booked: the parser never drops a row silently."""
        txs, _err = _parse(_buy() + _sale() + _sale(sym=''))
        sold = [t for t in txs if t.get('action') == 'BUYSELL'
                and t.get('quantity', 0) < 0]
        self.assertEqual(len(sold), 2, txs)
        self.assertEqual(sum(t['quantity'] for t in sold), -8)

    def test_a_number_spelled_another_way_is_the_same_number(self):
        self.assertEqual(_pairs(_sale() + _sale(sym='', qty='-4.0',
                                                net='896')), [(2, 3)])

    def test_every_pair_is_named(self):
        other = dict(td='2025-04-01', price='3.00', gross='1200.00',
                     net='1196.00')
        _txs, err = _parse(_sale() + _sale(sym='') + _sale(**other)
                           + _sale(sym='', **other))
        self.assertIn("questrade_2025.csv: 2 pairs of rows differ only by a "
                      "blank Symbol, each probably one trade fetched twice: "
                      f"lines 2 and 3 (2025-03-03 Sell {_OPT}; the blank one "
                      f"is line 3); lines 4 and 5 (2025-04-01 Sell {_OPT}; "
                      "the blank one is line 5). Where both rows of a pair "
                      "are booked", err)

    def test_the_pairs_named_are_bounded(self):
        body = ""
        for day in range(1, 6):
            kw = dict(td=f"2025-04-0{day}")
            body += _sale(**kw) + _sale(sym='', **kw)
        _txs, err = _parse(body)
        self.assertIn("5 pairs of rows differ only by a blank Symbol, each "
                      "probably one trade fetched twice: lines 2 and 3 ", err)
        self.assertIn(f"lines 6 and 7 (2025-04-03 Sell {_OPT}; the blank one "
                      "is line 7); and 2 more (named once these are "
                      "repaired). Where both rows", err)
        self.assertNotIn("lines 8 and 9", err)


class TestTheTextHoldsWhateverTheParserDoesWithThePair(unittest.TestCase):
    """It never says both rows are booked: only what follows if they
    are."""

    def test_a_pair_with_no_cash_and_no_units_books_neither(self):
        info = dict(td='2025-03-04', action='', sym='SYN',
                    desc='SYN CORP NOTICE', qty='0', price='0', gross='0',
                    comm='0', net='0', act='Other')
        txs, err = _parse(_buy() + qrow(cur='CAD', atype='API', **info)
                          + qrow(cur='CAD', atype='API',
                                 **dict(info, sym='')))
        self.assertEqual(len(txs), 1, txs)            # the buy alone
        self.assertIn(f"lines 3 and 4 {_TWIN}", err)
        self.assertIn("Where both rows of a pair are booked", err)
        self.assertNotIn("Both rows of a pair are booked", err)

    def test_a_share_trades_blank_copy_is_refused_after_the_warning(self):
        """A share trade with no symbol stops the run (as before); the
        warning above the refusal says what the row probably is."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 2025, {_F: QH + _buy() + _buy(sym='')})
            run = _cli_run(root, "run", "--no-input")
        self.assertEqual(run.returncode, 1, run.stdout[-800:])
        out = run.stdout + run.stderr
        self.assertIn("questrade_2025.csv line 3: BUYSELL (no symbol) "
                      "2025-02-03: missing required field 'symbol'", out)
        self.assertIn(f"{_F}: lines 2 and 3 {_TWIN} (2025-02-03 Buy SYN; "
                      f"the blank one is line 3). Where both rows of a pair "
                      f"are booked", out)
        self.assertLess(out.index(_TWIN), out.index("missing required field"))
        self.assertIn("delete the blank row when it shows one trade", out)


class TestNoTwinNoWord(unittest.TestCase):

    def _quiet(self, body):
        self.assertEqual(_pairs(body), [])
        _txs, err = _parse(body)
        self.assertNotIn("blank Symbol", err)

    def test_a_clean_file(self):
        self._quiet(_buy() + _sale() + _fee())

    def test_lone_blank_symbol_rows(self):
        """A fee, an FX conversion's two legs: blank symbols normally."""
        self._quiet(_buy() + _fee() + _fx('-100.00', 'CAD')
                    + _fx('70.00', 'USD'))

    def test_two_equal_blank_symbol_rows(self):
        self._quiet(_buy() + _fee() + _fee())

    def test_two_equal_named_rows_are_two_fills(self):
        self._quiet(_buy() + _sale() + _sale())

    def test_two_named_symbols_stay_two_rows(self):
        self.assertEqual(_pairs(_sale() + _sale(sym="SYN16Jan26C135.00")), [])

    def test_a_blank_row_that_differs_elsewhere(self):
        self.assertEqual(_pairs(_sale() + _sale(sym='', comm='-5.00')), [])
        self.assertEqual(_pairs(_sale() + _sale(sym='', td='2025-03-04')), [])
        self.assertEqual(_pairs(_sale() + _sale(sym='', acct='55500002')), [])


class TestMixedCounts(unittest.TestCase):

    def test_two_named_one_blank_is_one_pair(self):
        self.assertEqual(_pairs(_sale() + _sale() + _sale(sym='')), [(2, 4)])

    def test_one_named_two_blank_is_one_pair(self):
        """The second blank row is no copy of the one named row."""
        self.assertEqual(_pairs(_sale(sym='') + _sale() + _sale(sym='')),
                         [(3, 2)])

    def test_two_of_each_is_two_pairs(self):
        self.assertEqual(_pairs(_sale() + _sale(sym='') + _sale()
                                + _sale(sym='')), [(2, 3), (4, 5)])

    def test_the_header_repeated_is_no_row(self):
        self.assertEqual(_pairs(_sale() + QH + _sale(sym='')), [(2, 4)])


class TestShownAtTheEndOfTheRun(unittest.TestCase):
    """`tjs run` says it as the file is read and again in its closing
    list; the .sum keeps it."""

    def test_tjs_run_lists_it_at_its_end(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 2025, {_F: QH + _buy() + _sale()
                                       + _sale(sym='')})
            out, closing = _run(root)
            said = f"{_F}: lines 3 and 4 {_TWIN} (2025-03-03 Sell {_OPT}"
            self.assertIn("Warning: [margin] " + said, closing)
            self.assertEqual(out.count(said), 2, out)
            self.assertNotIn("[end of run]", out)
            self.assertIn("warning: ATTENTION: " + said,
                          (root / "reports" / "margin.sum").read_text())

    def test_a_clean_file_says_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 2025, {_F: QH + _buy() + _sale() + _fee()})
            out, _closing = _run(root)
            self.assertNotIn("blank Symbol", out)

    def test_a_lone_blank_symbol_row_says_nothing(self):
        """The option sale with its Symbol blank and no named copy: read
        from its description, as before."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 2025, {_F: QH + _buy() + _sale(sym='')
                                       + _fee()})
            out, _closing = _run(root)
            self.assertNotIn("blank Symbol", out)


if __name__ == "__main__":
    unittest.main()
