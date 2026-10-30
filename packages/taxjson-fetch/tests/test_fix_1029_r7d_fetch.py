"""Regression pins for the 2026-10-29 fix round 7, wave D, group
"fetchexport" (backlog TJ-1073, TJ-1074, TJ-1108). Every broker response
is mocked; nothing touches the network. Synthetic numbers, account ids
and tickers (SYN) only; each test's files live in a folder of their own.
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


class TestTrimNeedsEveryRequiredCell(unittest.TestCase):
    """TJ-1073: --trim-overlap removed the manual row while the fetched
    one held a blank Currency or an unreadable date, which the strict
    parser refuses."""

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
            return (res, (folder / "manual.csv").read_text(), manual,
                    err.getvalue())

    def _refused(self, bad, word):
        res, after, before, err = self._fetch([bad])
        self.assertEqual(after, before)
        trimmed = res["margin"]["trimmed"]
        self.assertEqual([t["rows"] for t in trimmed], [0])
        self.assertIn(word, trimmed[0]["refused"])
        self.assertIn("not trimmed", err)

    def test_a_null_currency(self):
        self._refused(_act("2025-06-02", "2025-06-03", 10, 10,
                           currency=None), "Currency")

    def test_a_blank_currency(self):
        self._refused(_act("2025-06-02", "2025-06-03", 10, 10,
                           currency="  "), "Currency")

    def test_an_unreadable_trade_date(self):
        self._refused(_act("2025-06-02", "2025-06-03", 10, 10,
                           tradeDate="2025-06-02X"), "Transaction Date")

    def test_an_unreadable_settlement_date(self):
        self._refused(_act("2025-06-02", "2025-06-03", 10, 10,
                           settlementDate="soon"), "Settlement Date")

    def test_a_quantity_that_is_no_number(self):
        self._refused(_act("2025-06-02", "2025-06-03", 10, 10,
                           quantity="many"), "Quantity")

    def test_every_refused_row_is_one_the_parser_refuses(self):
        for kw in ({"currency": None}, {"tradeDate": "2025-06-02X"},
                   {"settlementDate": "soon"}, {"quantity": "many"},
                   {"price": "abc"}):
            a = dict(_act("2025-06-02", "2025-06-03", 10, 10), **kw)
            self.assertTrue(api.qt_unusable_activities([a]), kw)
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "questrade_2025.csv"
                p.write_text(api.qt_to_csv([a], NUMBER))
                with self.assertRaises(Exception, msg=kw):
                    QuestradeBrokerage().parse_file(p)

    def test_what_the_parser_reads_still_trims(self):
        good = [_act("2025-06-02", "2025-06-03", 10, 10),
                _act("2025-06-02", "2025-06-03", 10, 10,
                     tradeDate="2025-06-02"),
                _act("2025-06-02", "2025-06-03", 10, 10,
                     tradeDate="2025-06-02T10:30:00.000000-04:00",
                     settlementDate="2025-06-03T00:00:00.000000-04:00"),
                dict(_act("2025-06-02", "2025-06-03", 0, 0), quantity=None,
                     price=None)]
        self.assertEqual(api.qt_unusable_activities(good), [])
        res, after, _before, _err = self._fetch(good[:1])
        self.assertEqual([t["rows"] for t in res["margin"]["trimmed"]], [1])
        self.assertEqual(len(after.splitlines()), 1)


LIVE = {"symbol": "SYN18Dec26C6.00", "symbolId": 12345, "openQuantity": 1,
        "averageEntryPrice": 2}
LIVE_OCC = "SYN261218C00006000"
TO_CLOSED = "SYN261120C00005000.TO"
US_OTHER = "SYN261120C00007000.US"


def _closed(sym):
    cur = "CAD" if sym.endswith(".TO") else "USD"
    return [{"action": "BUYSELL", "symbol": sym, "quantity": 1,
             "date": "2026-06-01", "multiplier": 100, "currency": cur},
            {"action": "BUYSELL", "symbol": sym, "quantity": -1,
             "date": "2026-06-02", "multiplier": 100, "currency": cur}]


class TestUnbookedOptionListing(unittest.TestCase):
    """TJ-1074, TJ-1108: a closed Montreal option in the books made a
    live US contract of its root, not in the books yet, .TO and CAD."""

    def test_questrade_says_usd(self):
        text, rows, _said, calls = _live(
            [LIVE], _closed(TO_CLOSED), sizes={12345: 100.0},
            lookup={12345: "USD"})
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(LIVE_OCC + ".US", "USD")])
        self.assertIn(("listing", [12345]), calls)
        self.assertNotIn("AMBIGUOUS", text)

    def test_questrade_says_cad(self):
        _text, rows, _said, _calls = _live(
            [LIVE], _closed(TO_CLOSED), sizes={12345: 100.0},
            lookup={12345: "CAD"})
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(LIVE_OCC + ".TO", "CAD")])

    def test_no_answer_is_said_and_no_currency_guessed(self):
        text, rows, said, _calls = _live(
            [LIVE], _closed(TO_CLOSED), sizes={12345: 100.0},
            lookup=RuntimeError("down"))
        self.assertEqual([r.currency or "" for r in rows], [""])
        self.assertNotIn("currency =", text)
        self.assertIn("AMBIGUOUS", text)
        # (The books' listing of the root stays the spelling: no phantom
        # symbol mismatch, the 2026-09 audit's cash-secured put.)
        self.assertEqual([r.symbol for r in rows], [LIVE_OCC + ".TO"])
        note = [s for s in said if "not in the books yet" in s
                and "Montreal" in s]
        self.assertTrue(note, said)
        self.assertIn("down", note[0])

    def test_a_silent_lookup_is_said_too(self):
        text, rows, said, _calls = _live(
            [LIVE], _closed(TO_CLOSED), sizes={12345: 100.0}, lookup={})
        self.assertIn("AMBIGUOUS", text)
        self.assertTrue([s for s in said if "did not say" in s], said)

    def test_a_root_on_both_listings_in_the_books(self):
        book = _closed(TO_CLOSED) + _closed(US_OTHER)
        _text, rows, _said, calls = _live(
            [LIVE], book, sizes={12345: 100.0}, lookup={12345: "USD"})
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(LIVE_OCC + ".US", "USD")])
        self.assertIn(("listing", [12345]), calls)

    def test_a_cdr_in_the_payload_does_not_decide_when_questrade_says(self):
        eq = {"symbol": "SYN.TO", "symbolId": 777, "openQuantity": 5,
              "averageEntryPrice": 9}
        _text, rows, _said, calls = _live(
            [eq, LIVE], [], sizes={12345: 100.0}, lookup={12345: "USD"})
        got = {r.symbol: r.currency for r in rows}
        self.assertEqual(got[LIVE_OCC + ".US"], "USD")
        self.assertEqual(got["SYN.TO"], "CAD")
        self.assertIn(("listing", [12345]), calls)

    def test_a_booked_contract_asks_nothing_about_its_listing(self):
        # (Held in the books. A closed copy says nothing of the listing
        # open now: TJ-1235, test_fix_1029_r7e_fetch.)
        book = _closed(LIVE_OCC + ".TO")[:1]
        _text, rows, _said, calls = _live([LIVE], book)
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(LIVE_OCC + ".TO", "CAD")])
        self.assertEqual(calls, [])

    def test_a_root_the_books_hold_only_us_asks_nothing(self):
        _text, rows, _said, calls = _live(
            [LIVE], _closed(US_OTHER), sizes={12345: 100.0})
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(LIVE_OCC + ".US", "USD")])
        self.assertNotIn("listing", [c[0] for c in calls])

    def test_the_other_root_is_not_taught(self):
        other = dict(LIVE, symbol="ZZQ18Dec26C6.00")
        _text, rows, _said, calls = _live(
            [other], _closed(TO_CLOSED), sizes={12345: 100.0})
        self.assertEqual([r.currency for r in rows], ["USD"])
        self.assertNotIn("listing", [c[0] for c in calls])


if __name__ == "__main__":
    unittest.main()
