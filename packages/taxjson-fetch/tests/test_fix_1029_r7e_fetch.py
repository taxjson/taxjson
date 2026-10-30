"""Regression pins for the 2026-10-30 fix round 7, wave E, group
"fetchforms" (backlog TJ-1231, TJ-1235). Every broker response is mocked;
nothing touches the network. Synthetic numbers, account ids and tickers
(SYN) only; each test's files live in a folder of their own.
"""
# First: puts the plugin, the core and its test helpers on sys.path.
import _support  # noqa: F401
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from taxjson_fetch import api
from taxjson_fetch import command as fc
from taxjson.lib.brokerages.questrade import QuestradeBrokerage

from test_fix_1029_r7_fetch import (NUMBER, SESSION, _act, _live,
                                    _qt_request)

_D = "2025-06-20T00:00:00.000000-04:00"
_Y1 = "0001-01-01T00:00:00.000000-05:00"
_BASE = dict(tradeDate=_D, transactionDate=_D, settlementDate=_D,
             description="SYNTHETIC", quantity=0, price=0, grossAmount=0,
             commission=0, netAmount=5, currency="CAD", symbol="")
_DIV = {**_BASE, "action": "DIV", "type": "Dividends", "symbol": "SYN.TO"}
_DEP = {**_BASE, "action": "CON", "type": "Deposits", "netAmount": 500}
_FX = {**_BASE, "action": "FXT", "type": "FX conversion", "netAmount": -100}


def _parser_refuses(acts):
    with tempfile.TemporaryDirectory() as td, \
            contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        p = Path(td) / "questrade_2025.csv"
        p.write_text(api.qt_to_csv(acts, NUMBER))
        try:
            QuestradeBrokerage().parse_file(p)
        except Exception:
            return True
    return False


class TestTrimFlagsOnlyWhatTheParserRefuses(unittest.TestCase):
    """TJ-1231: a dividend, deposit or FX row with a cell its branch never
    reads blocked --trim-overlap, and the warning said to delete it."""

    READ_FINE = (
        ("dividend, settlement text", {**_DIV, "settlementDate": "pending"}),
        ("dividend, settlement year 1", {**_DIV, "settlementDate": _Y1}),
        ("dividend, price text", {**_DIV, "price": "abc"}),
        ("deposit, settlement text", {**_DEP, "settlementDate": "pending"}),
        ("deposit, settlement year 1", {**_DEP, "settlementDate": _Y1}),
        ("deposit, price text", {**_DEP, "price": "abc"}),
        # (A cash row's net, currency or trade date: the FX-on-cash ledger
        # reads them, so they do block: test_fix_1029_r7f_misc, TJ-1291.)
        ("fx, settlement text", {**_FX, "settlementDate": "pending"}),
    )
    REFUSED = (
        ("buy, settlement text", _act("2025-06-02", "2025-06-03", 10, 10,
                                      settlementDate="soon")),
        ("buy, null currency", _act("2025-06-02", "2025-06-03", 10, 10,
                                    currency=None)),
        ("buy, null net", _act("2025-06-02", "2025-06-03", 10, 10,
                               netAmount=None)),
        ("dividend, null net", {**_DIV, "netAmount": None}),
        ("dividend, null currency", {**_DIV, "currency": None}),
        ("dividend, trade date text", {**_DIV, "tradeDate": "pending"}),
    )

    def _fetch(self, acts):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "inputs" / "margin"
            folder.mkdir(parents=True)
            good = _act("2025-06-02", "2025-06-03", 10, 10)
            manual = api.qt_to_csv([good], NUMBER).replace(",API\n",
                                                           ",Margin\n")
            (folder / "manual.csv").write_text(manual)
            req, _said = _qt_request(root)
            err = io.StringIO()
            with patch.object(fc, "_qt_open_session",
                              return_value=(dict(SESSION), "x")), \
                    patch.object(api, "qt_activities", return_value=acts), \
                    patch.object(api, "qt_window",
                                 return_value=(date(2025, 6, 1),
                                               date(2025, 6, 30))), \
                    contextlib.redirect_stderr(err):
                res = fc.run(req)
            return res, (folder / "manual.csv").read_text(), err.getvalue()

    def test_a_row_the_parser_reads_does_not_block_the_trim(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        for name, row in self.READ_FINE:
            with self.subTest(name):
                self.assertFalse(_parser_refuses([good, row]))
                self.assertEqual(api.qt_unusable_activities([good, row]), [])
                res, after, err = self._fetch([good, row])
                self.assertEqual(
                    [t["rows"] for t in res["margin"]["trimmed"]], [1])
                self.assertEqual(len(after.splitlines()), 1)
                self.assertNotIn("not trimmed", err)

    def test_a_row_the_parser_refuses_still_blocks_it(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        for name, row in self.REFUSED:
            with self.subTest(name):
                self.assertTrue(_parser_refuses([good, row]))
                self.assertEqual(len(api.qt_unusable_activities([good, row])),
                                 1)
                res, _after, err = self._fetch([good, row])
                self.assertEqual(
                    [t["rows"] for t in res["margin"]["trimmed"]], [0])
                self.assertIn("not trimmed", err)

    def test_only_the_refused_row_is_named(self):
        ok = {**_DIV, "settlementDate": "pending"}
        bad = _act("2025-06-04", "2025-06-05", 1, 10, netAmount=None)
        got = api.qt_unusable_activities([ok, bad])
        self.assertEqual(len(got), 1)
        self.assertIn("Net Amount", got[0])
        self.assertNotIn("Dividends", got[0])

    def test_asking_the_parser_shows_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            api.qt_unusable_activities(
                [{**_DIV, "settlementDate": "pending"},
                 _act("2025-06-04", "2025-06-05", 1, 10, netAmount=None)])
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_a_clean_download_never_asks_the_parser(self):
        with patch.object(api, "_qt_parser_refuses",
                          side_effect=AssertionError("asked")):
            self.assertEqual(api.qt_unusable_activities(
                [_act("2025-06-02", "2025-06-03", 10, 10), _DIV, _DEP, _FX]),
                [])

    def test_an_item_that_is_no_activity_is_named(self):
        self.assertEqual(len(api.qt_unusable_activities([5])), 1)


LIVE = {"symbol": "SYN18Dec26C6.00", "symbolId": 12345, "openQuantity": 1,
        "averageEntryPrice": 2}
OCC = "SYN261218C00006000"


def _rows(sym, *qtys, action="BUYSELL"):
    cur = "CAD" if sym.endswith(".TO") else "USD"
    return [{"action": action, "symbol": sym, "quantity": q,
             "date": f"2026-06-{i + 1:02d}", "multiplier": 100,
             "currency": cur} for i, q in enumerate(qtys)]


class TestAClosedBookedContractSaysNothingOfTheListing(unittest.TestCase):
    """TJ-1235: the books hold the series only closed, on one listing, and
    the live one is the other: its symbol and currency came from the
    closed row, with no lookup."""

    def _got(self, rows):
        return [(r.symbol, r.currency) for r in rows]

    def test_closed_montreal_live_us(self):
        text, rows, said, calls = _live(
            [LIVE], _rows(OCC + ".TO", 1, -1), sizes={12345: 100.0},
            lookup={12345: "USD"})
        self.assertEqual(self._got(rows), [(OCC + ".US", "USD")])
        self.assertIn(("listing", [12345]), calls)
        self.assertNotIn("AMBIGUOUS", text)
        self.assertIn("multiplier = 100.0", text)
        self.assertEqual([s for s in said if "note" in s], [])

    def test_closed_us_live_montreal(self):
        _text, rows, _said, _calls = _live(
            [LIVE], _rows(OCC + ".US", 1, -1), sizes={12345: 100.0},
            lookup={12345: "CAD"})
        self.assertEqual(self._got(rows), [(OCC + ".TO", "CAD")])

    def test_closed_and_the_same_listing_live(self):
        text, rows, _said, _calls = _live(
            [LIVE], _rows(OCC + ".TO", 1, -1), sizes={12345: 10.0},
            lookup={12345: "CAD"})
        self.assertEqual(self._got(rows), [(OCC + ".TO", "CAD")])
        # The books' own contract: their size.
        self.assertIn("multiplier = 100.0", text)

    def test_a_flipped_contract_takes_the_size_questrade_states(self):
        text, _rows_, _said, _calls = _live(
            [LIVE], _rows(OCC + ".TO", 1, -1), sizes={12345: 10.0},
            lookup={12345: "USD"})
        self.assertIn("multiplier = 10.0", text)

    def test_no_answer_is_said_and_no_currency_guessed(self):
        for lookup in ({}, RuntimeError("down")):
            with self.subTest(lookup=lookup):
                text, rows, said, _calls = _live(
                    [LIVE], _rows(OCC + ".TO", 1, -1), sizes={12345: 100.0},
                    lookup=lookup)
                self.assertEqual([r.symbol for r in rows], [OCC + ".TO"])
                self.assertEqual([r.currency or "" for r in rows], [""])
                self.assertNotIn("currency =", text)
                self.assertIn("AMBIGUOUS: the books hold this contract only "
                              "closed", text)
                note = [s for s in said if "only closed" in s]
                self.assertEqual(len(note), 1, said)

    def test_a_contract_the_books_hold_open_asks_nothing(self):
        for book in (_rows(OCC + ".TO", 1), _rows(OCC + ".TO", 2, -1),
                     _rows(OCC + ".TO", -1),
                     _rows(OCC + ".TO", 1) + _rows(OCC + ".TO", 1, -1)):
            with self.subTest(book=book):
                _text, rows, _said, calls = _live([LIVE], book)
                self.assertEqual(self._got(rows), [(OCC + ".TO", "CAD")])
                self.assertEqual(calls, [])

    def test_a_row_the_net_cannot_read_is_no_closed_book(self):
        book = _rows(OCC + ".TO", 1, -1) + _rows(OCC + ".TO", 2,
                                                 action="SPLIT")
        _text, rows, _said, calls = _live([LIVE], book)
        self.assertEqual(self._got(rows), [(OCC + ".TO", "CAD")])
        self.assertEqual(calls, [])
        book = _rows(OCC + ".TO", 1, -1) + _rows(OCC + ".TO", "many")
        _text, rows, _said, calls = _live([LIVE], book)
        self.assertEqual(calls, [])

    def test_a_row_of_income_alone_is_no_closed_book(self):
        # (A symbol with no trade: nothing to call closed.)
        book = _rows(OCC + ".TO", 0, action="DIVIDEND")
        _text, rows, _said, calls = _live([LIVE], book)
        self.assertEqual(self._got(rows), [(OCC + ".TO", "CAD")])
        self.assertEqual(calls, [])

    def test_shares_are_no_contract(self):
        eq = {"symbol": "SYN.TO", "symbolId": 777, "openQuantity": 5,
              "averageEntryPrice": 9}
        _text, rows, _said, calls = _live([eq], _rows("SYN.TO", 1, -1))
        self.assertEqual(self._got(rows), [("SYN.TO", "CAD")])
        self.assertEqual(calls, [])

    def test_both_listings_booked_keeps_its_own_note(self):
        book = _rows(OCC + ".TO", 1, -1) + _rows(OCC + ".US", 1)
        text, _rows_, said, _calls = _live([LIVE], book, lookup={})
        self.assertIn("both Montreal-listed", text)
        self.assertNotIn("only closed", text)
        self.assertTrue([s for s in said if "both Montreal" in s])


if __name__ == "__main__":
    unittest.main()
