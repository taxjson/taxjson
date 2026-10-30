"""Regression pins for the 2026-10-30 fix round 7, wave F, group "misc"
(backlog TJ-1291). Every broker response is mocked; nothing touches the
network. Synthetic numbers, account ids and tickers (SYN) only; each test's
files live in a folder of their own.
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
from taxjson.lib.brokerages.questrade import questrade_cash_events

from test_fix_1029_r7_fetch import NUMBER, SESSION, _act, _qt_request
from test_fix_1029_r7e_fetch import _DEP, _DIV, _FX, _parser_refuses

_LFJ = {**_DEP, "action": "LFJ", "type": "Other"}
_TF6 = {**_DEP, "action": "TF6", "type": "Transfers"}

# Cash rows parse_file leaves out unread and the FX-on-cash ledger reads.
LOST = (
    ("deposit, null net", {**_DEP, "netAmount": None}),
    ("deposit, blank net", {**_DEP, "netAmount": " "}),
    ("deposit, net text", {**_DEP, "netAmount": "abc"}),
    ("deposit, null currency", {**_DEP, "currency": None}),
    ("deposit, quantity text", {**_DEP, "quantity": "abc"}),
    ("deposit, trade date text", {**_DEP, "tradeDate": "pending"}),
    ("fx, null net", {**_FX, "netAmount": None}),
    ("fx, net text", {**_FX, "netAmount": "abc"}),
    ("fx, null currency", {**_FX, "currency": None}),
    ("fx, trade date text", {**_FX, "tradeDate": "pending"}),
    ("fx by its type alone", {**_FX, "action": "", "currency": None}),
    ("lending income, null net", {**_LFJ, "netAmount": None}),
    ("cash journal, null net", {**_TF6, "netAmount": None}),
)
# Rows with a cell nothing reads, or no cash in them: TJ-1231 stands.
KEPT = (
    ("dividend, settlement text", {**_DIV, "settlementDate": "pending"}),
    ("dividend, price text", {**_DIV, "price": "abc"}),
    ("deposit, settlement text", {**_DEP, "settlementDate": "pending"}),
    ("deposit, price text", {**_DEP, "price": "abc"}),
    ("fx, settlement text", {**_FX, "settlementDate": "pending"}),
    ("fx of zero, null currency", {**_FX, "netAmount": 0, "currency": None}),
)


class TestACashRowTheLedgerCannotUseBlocksTheTrim(unittest.TestCase):
    """TJ-1291: parse_file skips deposits and FX rows unread, so asking it
    alone cleared rows whose cash the ledger v2 reader then lost, and the
    good manual copy was trimmed."""

    def _fetch(self, acts):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "inputs" / "margin"
            folder.mkdir(parents=True)
            keep = [_act("2025-06-02", "2025-06-03", 10, 10), _DEP]
            manual = api.qt_to_csv(keep, NUMBER).replace(",API\n", ",Margin\n")
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
            events = questrade_cash_events(folder / "manual.csv")
            return res, events, err.getvalue()

    def test_the_gate_names_the_row(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        for name, row in LOST:
            with self.subTest(name):
                self.assertEqual(len(api.qt_unusable_activities([good, row])),
                                 1)
        # The rows the parser alone cleared, TJ-1291's own.
        for row in ({**_DEP, "netAmount": None}, {**_FX, "netAmount": None},
                    {**_FX, "currency": None}):
            self.assertFalse(_parser_refuses([good, row]))

    def test_the_manual_deposit_is_kept_for_the_ledger(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        for name, row in LOST:
            with self.subTest(name):
                res, events, err = self._fetch([good, row])
                self.assertEqual(
                    [t["rows"] for t in res["margin"]["trimmed"]], [0])
                self.assertIn("not trimmed", err)
                self.assertEqual([round(e["amount"]) for e in events], [500])

    def test_a_row_with_nothing_to_lose_does_not_block(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        for name, row in KEPT:
            with self.subTest(name):
                self.assertEqual(api.qt_unusable_activities([good, row]), [])

    def test_a_whole_download_still_trims(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        res, events, err = self._fetch([good, _DEP])
        self.assertEqual([t["rows"] for t in res["margin"]["trimmed"]], [2])
        self.assertEqual(events, [])
        self.assertNotIn("not trimmed", err)

    def test_asking_shows_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertTrue(api.qt_unusable_activities(
                [{**_DEP, "netAmount": "abc"}]))
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_a_row_that_is_no_cash_row_is_the_parsers_alone(self):
        # A purchase's cash is not the ledger's: the reader has no say.
        for bad in ({"price": "abc"}, {"netAmount": None}, {"currency": None}):
            row = {**_act("2025-06-02", "2025-06-03", 10, 10), **bad}
            self.assertFalse(api._qt_cash_reader_loses(row), bad)


if __name__ == "__main__":
    unittest.main()
