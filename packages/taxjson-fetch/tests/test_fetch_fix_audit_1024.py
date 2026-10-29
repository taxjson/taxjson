"""Regression pins for the 2026-10-24 external audit, group "fetch"
(A21, B01, B02, B27, B28): the Questrade and IBKR Flex fetch paths of
the taxjson-fetch plugin. Every broker response is mocked; nothing
touches the network. Synthetic account numbers (99900001, 99900002)
and tickers (ZZQ) only; each test's files live in a folder of their own.
"""
# First: puts the plugin, the core and its test helpers on sys.path.
import _support  # noqa: F401
import csv
import io
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from taxjson_fetch import api
from taxjson_fetch import command as fc

SESSION = {"api_server": "https://api01.iq.questrade.com",
           "access_token": "synthetic-access"}
_WAIT = 30          # seconds: a safety net only; every step is an event


def _activity(day, **over):
    """One synthetic Questrade API activity (a Buy of ZZQ on `day`)."""
    act = dict(tradeDate=day, settlementDate=day, action="Buy",
               symbol="ZZQ", description="synthetic", quantity=1,
               price=10, grossAmount=-10, commission=0, netAmount=-10,
               currency="USD", type="Trades")
    act.update(over)
    return act


def _rows(path):
    return list(csv.reader(io.StringIO(path.read_text())))


class TestA21ChunkDedupMultiplicity(unittest.TestCase):
    """A21: Questrade chunk dedup drops a second identical fill first
    seen in an overlapping chunk."""

    def test_a21_second_copy_first_seen_in_the_next_chunk_is_kept(self):
        act = {"tradeDate": "2026-01-31T00:00:00-05:00", "action": "Buy",
               "quantity": 1}
        # Chunk 1 (Jan 1..28) holds one copy of the boundary row, chunk 2
        # holds two: one is the repeat, the other a fill chunk 1 never saw.
        with patch.object(api, "_qt_get", side_effect=[
                {"activities": [act]},
                {"activities": [act, dict(act)]}]):
            acts = api.qt_activities(SESSION, "99900001", date(2026, 1, 1),
                                     date(2026, 2, 10), lambda u: b"")
        self.assertEqual(len(acts), 2)

    def test_a21_a_plain_repeat_is_still_dropped(self):
        act = {"tradeDate": "2026-01-31T00:00:00-05:00", "action": "Buy",
               "quantity": 1}
        other = dict(act, action="Sell")
        with patch.object(api, "_qt_get", side_effect=[
                {"activities": [act, act]},
                {"activities": [other, act, act]}]):
            acts = api.qt_activities(SESSION, "99900001", date(2026, 1, 1),
                                     date(2026, 2, 10), lambda u: b"")
        self.assertEqual([a["action"] for a in acts],
                         ["Buy", "Buy", "Sell"])


class TestB01MissingActivities(unittest.TestCase):
    """B01: Questrade treats a response without activities as a
    successful empty window."""

    def _fetch(self, doc):
        with patch.object(api, "_qt_get", return_value=doc):
            return api.qt_activities(SESSION, "99900001", date(2026, 1, 1),
                                     date(2026, 1, 1), lambda u: b"")

    def test_b01_an_object_without_activities_is_refused(self):
        with self.assertRaises(RuntimeError) as cm:
            self._fetch({"message": "upstream failed"})
        msg = str(cm.exception)
        self.assertIn("no 'activities' list", msg)
        self.assertIn("upstream failed", msg)
        self.assertIn("nothing trimmed", msg)
        self.assertNotIn("99900001", msg)              # the path is masked
        self.assertNotIn("synthetic-access", msg)

    def test_b01_a_null_activities_is_refused(self):
        with self.assertRaises(RuntimeError) as cm:
            self._fetch({"activities": None})
        self.assertIn("not a list", str(cm.exception))

    def test_b01_an_explicit_empty_list_is_an_empty_window(self):
        self.assertEqual(self._fetch({"activities": []}), [])


class TestB01PositionsFollowUp(unittest.TestCase):
    """B01 follow-up: the positions endpoint had the same leniency — a
    response without a `positions` list became an empty live-holdings
    snapshot at exit 0."""

    def _positions(self, doc):
        with patch.object(api, "_qt_get", return_value=doc):
            return api.qt_positions(SESSION, "99900001", lambda u: b"")

    def test_b01_positions_without_a_list_are_refused(self):
        for doc, what in (({"message": "upstream failed"},
                           "no 'positions' list"),
                          ({"positions": None}, "not a list"),
                          ({"positions": {"symbol": "ZZQ"}}, "not a list")):
            with self.subTest(doc=doc):
                with self.assertRaises(RuntimeError) as cm:
                    self._positions(doc)
                msg = str(cm.exception)
                self.assertIn(what, msg)
                self.assertIn("/v1/accounts/99***/positions", msg)
                self.assertIn("no live-holdings snapshot was written", msg)
                self.assertNotIn("99900001", msg)
                self.assertNotIn("synthetic-access", msg)
        with self.assertRaises(RuntimeError) as cm:
            self._positions({"code": 1017, "message": "upstream failed"})
        self.assertIn("code: 1017; message: upstream failed",
                      str(cm.exception))

    def test_b01_an_explicit_empty_list_is_an_empty_account(self):
        self.assertEqual(self._positions({"positions": []}), [])
        self.assertEqual(self._positions(
            {"positions": [{"symbol": "ZZQ.TO", "openQuantity": 1}]}),
            [{"symbol": "ZZQ.TO", "openQuantity": 1}])


_FLEX_ONE = ('Statement,Header,Field Name,Field Value\n'
             'Statement,Data,BrokerName,Interactive Brokers\n'
             'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
             'Statement,Data,WhenGenerated,"January 2, 2026"\n'
             'Trades,Header,DataDiscriminator,Asset Category\n'
             'Trades,Data,Order,Stocks\n')


class TestB02FlexStatementCount(unittest.TestCase):
    """B02: Flex multi-account guard counts metadata rows as separate
    account statements."""

    def test_b02_metadata_rows_are_one_statement(self):
        self.assertEqual(api.flex_statement_count(_FLEX_ONE), 1)
        # The demo IB statement users model their exports on.
        demo = _support.REPO_ROOT / "examples" / "ib_demo.csv"
        self.assertEqual(api.flex_statement_count(
            demo.read_text(encoding="utf-8", errors="replace")), 1)

    def test_b02_two_statements_still_count_two(self):
        self.assertEqual(api.flex_statement_count(_FLEX_ONE + _FLEX_ONE), 2)
        # Back to back, each with its own Statement header.
        head = _FLEX_ONE.split("Trades,Header")[0]
        self.assertEqual(api.flex_statement_count(head + head), 2)
        self.assertEqual(api.flex_statement_count(
            '"Statement","Data","BrokerName","IB"\n'), 1)


class TestB27ConcurrentFetches(unittest.TestCase):
    """B27: Concurrent Questrade fetches overwrite each other's
    downloaded activities."""

    def test_b27_two_fetches_into_one_csv_keep_both_downloads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "inputs" / "margin").mkdir(parents=True)
            args = SimpleNamespace(days=None, year=None, from_date=None,
                                   dry_run=False, trim_overlap=False)
            cfg = {"settings": {"year": 2026},
                   "accounts": {"margin": {"brokerage": "questrade",
                                           "account": "99900001"}}}
            waiting = threading.Event()        # a fetch asked for the lock
            said = []

            def say(msg):
                said.append(msg)
                if "waiting for another `taxjson fetch`" in msg:
                    waiting.set()
            req = SimpleNamespace(args=args, root=root, work=root / "work",
                                  config=cfg, accounts=["margin"], say=say,
                                  json=True)
            orig = fc._merge_csv_text
            first_merge = threading.Event()

            def merge(existing, new):
                result = orig(existing, new)
                if not first_merge.is_set():
                    # The first merger holds its result until the other
                    # fetch has asked for the lock. Unlocked (the bug),
                    # nobody asks: the other fetch reads the same old
                    # file meanwhile and the two publish one row each.
                    first_merge.set()
                    waiting.wait(3)
                return result
            local = threading.local()

            def worker(day):
                local.day = day
                return fc._run(req)
            with patch.object(fc, "_qt_open_session", return_value=(
                    {"api_server": "synthetic", "access_token": "synthetic"},
                    "synthetic")), \
                    patch.object(api, "qt_window", return_value=(
                        date(2026, 1, 1), date(2026, 1, 3))), \
                    patch.object(api, "qt_activities",
                                 side_effect=lambda *a: [_activity(local.day)]), \
                    patch.object(fc, "_merge_csv_text", side_effect=merge):
                with ThreadPoolExecutor(2) as ex:
                    futures = [ex.submit(worker, d)
                               for d in ("2026-01-01", "2026-01-02")]
                    for f in futures:
                        f.result(timeout=_WAIT)
            rows = _rows(root / "inputs" / "margin" / "questrade_2026.csv")
            self.assertEqual(sorted(r[0][:10] for r in rows[1:]),
                             ["2026-01-01", "2026-01-02"])
            self.assertTrue(waiting.is_set(),
                            "the second fetch waited for the first")


def _qt_csv(rows):
    """A MANUAL Questrade export of (trade day, account number) rows; ""
    is a blank Account # cell. Its Account Type is a plan's (Margin),
    not the fetch's `API`: rows the fetch wrote are never an overlap
    (backlog TJ-0516)."""
    parts = [api.qt_to_csv([_activity(day)], number) for day, number in rows]
    head = parts[0].splitlines(keepends=True)[0]
    return head + "".join(p.splitlines(keepends=True)[1]
                          .replace(",API\n", ",Margin\n") for p in parts)


class TestB28TrimScopedToAccount(unittest.TestCase):
    """B28: Questrade overlap trimming deletes rows belonging to a
    different broker account."""

    def test_b28_another_accounts_rows_are_neither_counted_nor_trimmed(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = d / "manual.csv"
            manual.write_text(_qt_csv([("2026-01-02", "99900002")]))
            out = d / "questrade_2026.csv"
            out.write_text(api.qt_to_csv([], "99900001"))
            self.assertEqual(fc._qt_window_overlap(
                d, out, "2026-01-01", "2026-01-03", "99900001"), [])
            self.assertEqual(fc._qt_trim_file(
                manual, "2026-01-01", "2026-01-03", "99900001"), 0)
            self.assertIn("99900002", manual.read_text())
            self.assertFalse((d / "manual.csv.bak").exists())

    def test_b28_own_and_unattributed_rows_inside_the_window_still_go(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = d / "manual.csv"
            body = _qt_csv([("2025-12-15", "99900001"),      # before the window
                            ("2026-01-02", "99900001"),      # the account's
                            ("2026-01-02", "99900002"),      # another account's
                            ("2026-01-02", "")])             # unattributed
            manual.write_text(body)
            out = d / "questrade_2026.csv"
            out.write_text(api.qt_to_csv([], "99900001"))
            self.assertEqual(fc._qt_window_overlap(
                d, out, "2026-01-01", "2026-01-03", "99900001"),
                [(manual, 2)])
            self.assertEqual(fc._qt_trim_file(
                manual, "2026-01-01", "2026-01-03", "99900001"), 2)
            kept = _rows(manual)
            self.assertEqual([(r[0][:10], r[11]) for r in kept[1:]],
                             [("2025-12-15", "99900001"),
                              ("2026-01-02", "99900002")])
            self.assertEqual((d / "manual.csv.bak").read_text(), body)

    def test_b28_a_file_without_an_account_column_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = d / "manual.csv"
            body = ("Transaction Date,Symbol,Quantity\n"
                    "2026-01-02,ZZQ,1\n")
            manual.write_text(body)
            with self.assertRaises(ValueError) as cm:
                fc._qt_trim_file(manual, "2026-01-01", "2026-01-03",
                                 "99900001")
            msg = str(cm.exception)
            self.assertIn("manual.csv", msg)
            self.assertIn("Account #", msg)
            self.assertNotIn("99900001", msg)          # masked
            self.assertEqual(manual.read_text(), body)
            self.assertFalse((d / "manual.csv.bak").exists())


if __name__ == "__main__":
    unittest.main()
