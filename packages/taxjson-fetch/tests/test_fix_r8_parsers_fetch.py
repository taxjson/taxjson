"""Fix round 8, group parsers: `tjs fetch --trim-overlap` and the live
holdings snapshot (backlog TJ-1292, TJ-1324, TJ-1144). Every broker response
is mocked; nothing touches the network. Synthetic numbers, account ids and
tickers (SYN) only; each test's files live in a folder of their own.
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

from test_fix_1029_r7_fetch import NUMBER, SESSION, _act, _live, _qt_request
from test_fix_1029_r7e_fetch import _DEP, LIVE, OCC

_D1 = "2025-06-02T00:00:00.000000-04:00"
_D2 = "2025-06-05T00:00:00.000000-04:00"
_CIL = {"tradeDate": _D1, "transactionDate": _D1, "settlementDate": _D1,
        "action": "CIL", "type": "Other", "symbol": "SYN.TO",
        "description": "SYN CO CASH IN LIEU OF 0.5 SHARES", "quantity": 0,
        "price": 0, "grossAmount": 0, "commission": 0, "netAmount": 5,
        "currency": "CAD"}
_REV = {**_CIL, "tradeDate": _D2, "transactionDate": _D2,
        "settlementDate": _D2, "netAmount": -5}


def _fetch(acts):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        folder = root / "inputs" / "margin"
        folder.mkdir(parents=True)
        manual = api.qt_to_csv([_act("2025-06-02", "2025-06-03", 10, 10)],
                               NUMBER).replace(",API\n", ",Margin\n")
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
        return res, " ".join(err.getvalue().split())


class TestAReversalReadBesideItsOriginal(unittest.TestCase):
    """TJ-1292: a flagged reversal was asked about alone, and refused for
    having no original, though the download holds it."""

    READ = (("settlement text", {"settlementDate": "pending"}),
            ("price text", {"price": "abc"}),
            ("gross null", {"grossAmount": None}))

    def _parses(self, acts):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "q.csv"
            f.write_text(api.qt_to_csv(acts, NUMBER))
            with contextlib.redirect_stderr(io.StringIO()):
                QuestradeBrokerage().parse_file(f)

    def test_the_pair_does_not_block_the_trim(self):
        for name, cells in self.READ:
            with self.subTest(name):
                acts = [_CIL, {**_REV, **cells}]
                self._parses(acts)         # the parser reads the file
                self.assertEqual(api.qt_unusable_activities(acts), [])

    def test_the_pair_beside_a_trade_trims(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        res, err = _fetch([good, _CIL, {**_REV, "settlementDate": "pending"}])
        self.assertEqual([t["rows"] for t in res["margin"]["trimmed"]], [1])
        self.assertNotIn("not trimmed", err)

    def test_a_damaged_original_beside_its_reversal_is_read_too(self):
        acts = [{**_CIL, "settlementDate": "pending"},
                {**_REV, "settlementDate": "pending"}]
        self._parses(acts)
        self.assertEqual(api.qt_unusable_activities(acts), [])

    # The neighbours, unchanged.
    def test_a_reversal_with_no_original_still_blocks(self):
        for acts in ([{**_REV, "settlementDate": "pending"}],
                     [{**_CIL, "symbol": "ZZZ.TO"},
                      {**_REV, "settlementDate": "pending"}]):
            self.assertEqual(len(api.qt_unusable_activities(acts)), 1, acts)

    def test_a_reversal_of_another_amount_still_blocks(self):
        acts = [{**_CIL, "netAmount": 7},
                {**_REV, "settlementDate": "pending"}]
        self.assertEqual(len(api.qt_unusable_activities(acts)), 1)

    def test_a_missing_net_still_blocks(self):
        acts = [_CIL, {**_REV, "netAmount": None}]
        self.assertEqual(len(api.qt_unusable_activities(acts)), 1)

    def test_a_refused_trade_beside_the_same_symbol_still_blocks(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        bad = _act("2025-06-04", "2025-06-05", 1, 10, netAmount=None)
        self.assertEqual(len(api.qt_unusable_activities([good, bad])), 1)

    def test_another_rows_refusal_does_not_clear_or_blame_the_pair(self):
        bad = _act("2025-06-04", "2025-06-05", 1, 10, netAmount=None)
        got = api.qt_unusable_activities(
            [bad, _CIL, {**_REV, "settlementDate": "pending"}])
        self.assertEqual(len(got), 1)
        self.assertIn("Net Amount", got[0])


class TestTheWarningNamesTheReader(unittest.TestCase):
    """TJ-1324: a row only the FX-on-cash ledger cannot use was said to be
    refused by `tjs run`, which reads that file."""

    GOOD = _act("2025-06-02", "2025-06-03", 10, 10)
    LEDGER = {**_DEP, "netAmount": None}
    PARSER = _act("2025-06-04", "2025-06-05", 1, 10, netAmount=None)

    def test_only_the_ledger(self):
        _res, err = _fetch([self.GOOD, self.LEDGER])
        self.assertIn("not trimmed", err)
        self.assertIn("FX-on-cash ledger", err)
        self.assertNotIn("`tjs run` refuses", err)

    def test_the_parser(self):
        _res, err = _fetch([self.GOOD, self.PARSER])
        self.assertIn("`tjs run` refuses", err)
        self.assertNotIn("FX-on-cash ledger", err)

    def test_both(self):
        _res, err = _fetch([self.GOOD, self.LEDGER, self.PARSER])
        self.assertIn("`tjs run` refuses", err)
        self.assertIn("FX-on-cash ledger", err)

    def test_the_lines_say_which(self):
        ledger = []
        got = api.qt_unusable_activities([self.LEDGER, self.PARSER],
                                         ledger_only=ledger)
        self.assertEqual(len(got), 2)
        self.assertEqual(len(ledger), 1)
        self.assertIn("Deposits", ledger[0])


class TestALiveOptionOnARootOnlyAPositionTeaches(unittest.TestCase):
    """TJ-1144: with no lookup answer, an option on a root with a .TO
    position (a CDR, perhaps) is not guessed .TO and CAD."""

    EQ = {"symbol": "SYN.TO", "symbolId": 777, "openQuantity": 100,
          "averageEntryPrice": 10}

    def test_no_answer_is_said_and_no_currency_guessed(self):
        for lookup in ({}, RuntimeError("down")):
            with self.subTest(lookup=lookup):
                text, rows, said, _calls = _live(
                    [self.EQ, LIVE], [], sizes={12345: 100.0}, lookup=lookup)
                got = {r.symbol: r.currency or "" for r in rows}
                self.assertEqual(got[OCC + ".TO"], "")
                self.assertIn("AMBIGUOUS: this contract is not in the books",
                              text)
                self.assertEqual(
                    len([s for s in said if "not in the books yet" in s
                         and "Montreal" in s]), 1, said)

    # The neighbours, unchanged.
    def test_an_answer_decides(self):
        for listed, suffix in (("CAD", ".TO"), ("USD", ".US")):
            with self.subTest(listed=listed):
                text, rows, _said, _calls = _live(
                    [self.EQ, LIVE], [], sizes={12345: 100.0},
                    lookup={12345: listed})
                got = {r.symbol: r.currency for r in rows}
                self.assertEqual(got[OCC + suffix], listed)
                self.assertNotIn("AMBIGUOUS", text)

    def test_the_equity_keeps_its_currency(self):
        _text, rows, _said, _calls = _live(
            [self.EQ, LIVE], [], sizes={12345: 100.0}, lookup={})
        got = {r.symbol: r.currency for r in rows}
        self.assertEqual(got["SYN.TO"], "CAD")

    def test_a_root_with_no_to_listing_stays_us(self):
        _text, rows, said, calls = _live([LIVE], [], sizes={12345: 100.0})
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(OCC + ".US", "USD")])
        self.assertNotIn(("listing", [12345]), calls)

    def test_a_contract_in_the_books_asks_nothing(self):
        book = [{"action": "BUYSELL", "symbol": OCC + ".TO", "quantity": 1,
                 "date": "2026-06-01", "multiplier": 100, "currency": "CAD"}]
        text, rows, _said, calls = _live([self.EQ, LIVE], book)
        got = {r.symbol: r.currency for r in rows}
        self.assertEqual(got[OCC + ".TO"], "CAD")
        self.assertEqual(calls, [])
        self.assertNotIn("AMBIGUOUS", text)


if __name__ == "__main__":
    unittest.main()
