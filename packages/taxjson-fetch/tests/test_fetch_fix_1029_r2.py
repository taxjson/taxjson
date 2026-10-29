"""Regression pins for the 2026-10-29 audit continuation, group
"brokerparse" (backlog TJ-0513 to TJ-0516): Questrade live holdings and
the fetch overlap guard. Every broker response is mocked; nothing
touches the network. Synthetic account numbers (99900001) and tickers
(SYN) only; each test's files live in a folder of their own.
"""
# First: puts the plugin, the core and its test helpers on sys.path.
import _support  # noqa: F401
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson_fetch import api
from taxjson_fetch import command as fc
from taxjson.lib.positions_reports import read_positions

NUMBER = "99900001"   # pii-ok (synthetic)
_OLD = ('[meta]\naccount = "margin"\n[[holding]]\nsymbol = "SYN.TO"\n'
        'quantity = 10\n')


def _live(positions, book=None):
    """Run the live-holdings fetch for one account with `positions` as
    Questrade's payload and `book` as the account's parsed books; return
    (snapshot text, rows read back, SystemExit or None)."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work = root / "work"
        work.mkdir()
        if book is not None:
            (work / "margin_base.json").write_text(
                json.dumps({"transactions": book}))
        hold = root / "holdings"
        hold.mkdir()
        snap = hold / "margin_live_holdings.toml"
        snap.write_text(_OLD)
        cfg = {"settings": {"year": 2026},
               "accounts": {"margin": {"brokerage": "questrade",
                                       "account": NUMBER}}}
        stop = None
        with patch.object(fc, "_qt_open_session",
                          return_value=({"access_token": "x"}, "x")), \
                patch.object(api, "qt_positions", return_value=positions), \
                contextlib.redirect_stderr(io.StringIO()):
            try:
                fc._qt_live_holdings(root, work, cfg, ["margin"], None,
                                     lambda _m: None)
            except SystemExit as e:
                stop = e
        text = snap.read_text()
        rows = read_positions(snap).rows
    return text, rows, stop


class TestLivePositionsAreValidated(unittest.TestCase):
    """TJ-0513: a position item without a readable openQuantity (or
    symbol) is refused before the snapshot is replaced."""

    def test_missing_quantity_keeps_the_previous_snapshot(self):
        text, rows, stop = _live([{"symbol": "SYN.TO",
                                   "averageEntryPrice": 10}])
        self.assertIsNotNone(stop)
        self.assertIn("openQuantity", str(stop.code))
        self.assertEqual(text, _OLD)
        self.assertEqual([(r.symbol, r.quantity) for r in rows],
                         [("SYN.TO", 10.0)])

    def test_unreadable_items_are_refused(self):
        for item in ({"symbol": "SYN.TO", "openQuantity": "nan"},
                     {"symbol": "SYN.TO", "openQuantity": True},
                     {"symbol": "SYN.TO", "openQuantity": None},
                     {"openQuantity": 5},
                     {"symbol": "SYN.TO", "openQuantity": 5,
                      "averageEntryPrice": "x"},
                     "SYN.TO"):
            with self.assertRaises(RuntimeError, msg=item):
                api.positions_to_holdings_toml([item], "margin", NUMBER,
                                               "2026-01-02 10:00:00")

    def test_a_closed_position_stays_legitimate(self):
        text = api.positions_to_holdings_toml(
            [{"symbol": "SYN.TO", "openQuantity": 0},
             {"symbol": "QZA.TO", "openQuantity": 5,
              "averageEntryPrice": 2}],
            "margin", NUMBER, "2026-01-02 10:00:00")
        self.assertIn("holdings_count = 1", text)
        self.assertNotIn("SYN.TO", text)

    def test_the_api_response_is_checked_too(self):
        def http(_url):
            return json.dumps({"positions": [{"symbol": "SYN.TO"}]}).encode()
        with self.assertRaises(RuntimeError):
            api.qt_positions({"api_server": "https://api01.iq.questrade.com",
                              "access_token": "x"}, NUMBER, http)


class TestLiveOptionContracts(unittest.TestCase):
    """TJ-0514 / TJ-0515: a live option keeps the contract size the
    books state, and a class-share root converts like any other."""

    def test_book_multiplier_reaches_the_snapshot(self):
        text, rows, stop = _live(
            [{"symbol": "SYN20Jan26C88.00", "openQuantity": 1,
              "averageEntryPrice": 2}],
            book=[{"symbol": "SYN260120C00088000.US", "multiplier": 10}])
        self.assertIsNone(stop)
        self.assertIn("multiplier = 10", text)
        self.assertEqual([(r.symbol, r.cost) for r in rows],
                         [("SYN260120C00088000.US", 20.0)])

    def test_books_that_disagree_leave_the_cost_unknown(self):
        text, rows, _stop = _live(
            [{"symbol": "SYN20Jan26C88.00", "openQuantity": 1,
              "averageEntryPrice": 2}],
            book=[{"symbol": "SYN260120C00088000.US", "multiplier": 10},
                  {"symbol": "SYN260120C00088000.US", "multiplier": 100}])
        self.assertIn("contract_multiplier_unknown = true", text)
        self.assertEqual([r.cost for r in rows], [None])

    def test_standard_contract_unchanged(self):
        text, rows, _stop = _live(
            [{"symbol": "SYN20Jan26C88.00", "openQuantity": 1,
              "averageEntryPrice": 2}],
            book=[{"symbol": "SYN260120C00088000.US"}])
        self.assertNotIn("multiplier", text)
        self.assertEqual([r.cost for r in rows], [200.0])

    def test_class_share_root(self):
        self.assertEqual(
            api.qt_position_symbol("SYN.B20Jan26C88.00",
                                   book_symbols={"SYN.B260120C00088000.US"}),
            "SYN.B260120C00088000.US")
        self.assertEqual(api.qt_position_symbol("SYN.B20Jan26C88.00"),
                         "SYN.B260120C00088000.US")
        self.assertEqual(
            api.qt_position_symbol("SYN.B20Jan26P88.50",
                                   to_roots={"SYN.B"}),
            "SYN.B260120P00088500.TO")
        _text, rows, _stop = _live(
            [{"symbol": "SYN.B20Jan26C88.00", "openQuantity": 1,
              "averageEntryPrice": 2}],
            book=[{"symbol": "SYN.B260120C00088000.US"}])
        self.assertEqual([(r.symbol, r.asset_type, r.cost) for r in rows],
                         [("SYN.B260120C00088000.US", "option", 200.0)])

    def test_impossible_contract_is_left_as_sent(self):
        # A February 30 expiry or a strike finer than 0.001 is no OCC
        # symbol: kept as sent, so the mismatch stays visible.
        self.assertEqual(api.qt_position_symbol("SYN30Feb26C88.00"),
                         "SYN30Feb26C88.00")
        self.assertEqual(api.qt_position_symbol("SYN20Jan26C88.0005"),
                         "SYN20Jan26C88.0005")


_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
        "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
        "Account #,Activity Type,Account Type\n")


def _row(day, atype):
    return (f"{day} 12:00:00 AM,{day} 12:00:00 AM,Buy,SYN.TO,SYN CORP,10,"
            f"10.00,-100.00,-1.00,-101.00,CAD,{NUMBER},Trades,{atype}\n")


class TestOverlapByContentNotName(unittest.TestCase):
    """TJ-0516: a manual export named like a fetch file is an overlap;
    the fetch's own rows (Account Type API) in another year's file are
    not."""

    def _overlap(self, name, body):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / name).write_text(_HDR + body)
            out = d / "questrade_2025.csv"
            out.write_text(_HDR)
            hits = fc._qt_window_overlap(d, out, "2025-01-01", "2025-12-31",
                                         NUMBER)
            return [(p.name, n) for p, n in hits]

    def test_manual_rows_count_whatever_the_name(self):
        for name in ("manual.csv", "questrade_2024.csv"):
            self.assertEqual(self._overlap(name, _row("2025-03-03",
                                                      "Margin")),
                             [(name, 1)])

    def test_the_fetchs_own_rows_do_not(self):
        self.assertEqual(self._overlap("questrade_2024.csv",
                                       _row("2025-01-02", "API")), [])
        self.assertEqual(self._overlap(
            "questrade_2024.csv",
            _row("2025-01-02", "API") + _row("2025-03-03", "Margin")),
            [("questrade_2024.csv", 1)])

    def test_trim_keeps_the_fetchs_own_rows(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "questrade_2024.csv"
            p.write_text(_HDR + _row("2025-01-02", "API")
                         + _row("2025-03-03", "Margin"))
            self.assertEqual(fc._qt_trim_file(p, "2025-01-01", "2025-12-31",
                                              NUMBER), 1)
            text = p.read_text()
        self.assertIn("2025-01-02", text)
        self.assertNotIn("2025-03-03", text)


if __name__ == "__main__":
    unittest.main()
