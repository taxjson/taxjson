"""Regression pins for the 2026-10-29 fix round 3, group "parsers"
(backlog TJ-0614): a live Questrade option the books do not hold yet
takes the contract size Questrade's symbol lookup states, else the
standard size is said to be assumed; and, round 6 (TJ-0670), not the
shares an adjusted contract delivers. Every broker response is mocked;
nothing touches the network. Synthetic numbers and tickers (SYN) only;
each test's files live in a folder of their own.
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
SESSION = {"api_server": "https://api01.iq.questrade.com",
           "access_token": "x"}
MINI = {"symbol": "SYN20Jan26C88.00", "symbolId": 7001, "openQuantity": 1,
        "averageEntryPrice": 2}
OCC = "SYN260120C00088000.US"


def _snapshot(positions, **kw):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "margin_live_holdings.toml"
        p.write_text(api.positions_to_holdings_toml(
            positions, "margin", NUMBER, "2026-01-02 10:00:00", **kw))
        return p.read_text(), read_positions(p).rows


def _live(positions, sizes=None, book=None):
    """The live-holdings fetch of one account: Questrade's positions
    payload `positions`, its symbol lookup answering `sizes` (an
    exception: the lookup fails). Returns (snapshot text, rows, said,
    lookup calls)."""
    calls = []

    def lookup(_session, ids, _http):
        calls.append(sorted(ids))
        if isinstance(sizes, Exception):
            raise sizes
        return dict(sizes or {})
    said = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work = root / "work"
        work.mkdir()
        if book is not None:
            (work / "margin_base.json").write_text(
                json.dumps({"transactions": book}))
        cfg = {"settings": {"year": 2026},
               "accounts": {"margin": {"brokerage": "questrade",
                                       "account": NUMBER}}}
        with patch.object(fc, "_qt_open_session",
                          return_value=(dict(SESSION), "x")), \
                patch.object(api, "qt_positions", return_value=positions), \
                patch.object(api, "qt_contract_sizes", side_effect=lookup), \
                contextlib.redirect_stderr(io.StringIO()):
            fc._qt_live_holdings(root, work, cfg, ["margin"], None,
                                 said.append)
        snap = root / "holdings" / "margin_live_holdings.toml"
        text = snap.read_text()
        rows = read_positions(snap).rows
    return text, rows, said, calls


class TestSymbolLookup(unittest.TestCase):
    """qt_contract_sizes reads optionContractDeliverables."""

    def test_sizes_from_the_deliverables(self):
        asked = []

        def http(url):
            asked.append(url)
            return json.dumps({"symbols": [
                {"symbolId": 7001, "symbol": "SYN20Jan26C88.00",
                 "optionContractDeliverables": {
                     "underlyings": [{"multiplier": 10,
                                      "underlyingSymbol": "SYN",
                                      "underlyingSymbolId": 7000}],
                     "cashInLieu": 0}},
                {"symbolId": 7002, "symbol": "SYN20Jan26C90.00",
                 "optionContractDeliverables": {
                     "underlyings": [{"multiplier": 100,
                                      "underlyingSymbol": "SYN"},
                                     {"multiplier": 50,
                                      "underlyingSymbol": "QZB"}],
                     "cashInLieu": 0}},
                {"symbolId": 7003, "symbol": "SYN20Jan26C92.00"},
                {"symbolId": 9999, "optionContractDeliverables": {
                    "underlyings": [{"multiplier": 100}]}}]}).encode()
        got = api.qt_contract_sizes(SESSION, [7003, 7001, 7002, True, -4],
                                    http)
        self.assertEqual(got, {7001: 10.0, 7002: None})
        self.assertEqual(len(asked), 1)
        self.assertIn("/v1/symbols?ids=7001%2C7002%2C7003", asked[0])

    def test_a_malformed_response_is_refused(self):
        with self.assertRaises(RuntimeError) as cm:
            api.qt_contract_sizes(SESSION, [7001],
                                  lambda _u: b'{"code": 1017}')
        self.assertIn("no contract size was read", str(cm.exception))


class TestUnbookedLiveOption(unittest.TestCase):
    """TJ-0614: a live option the books do not hold yet."""

    def test_the_lookup_size_reaches_the_snapshot(self):
        text, rows = _snapshot([MINI], live_sizes={7001: 10.0})
        self.assertIn("multiplier = 10.0", text)
        self.assertEqual([(r.symbol, r.cost) for r in rows], [(OCC, 20.0)])

    def test_a_deliverable_that_is_not_shares_leaves_the_cost_unknown(self):
        text, rows = _snapshot([MINI], live_sizes={7001: None})
        self.assertIn("contract_multiplier_unknown = true", text)
        self.assertEqual([r.cost for r in rows], [None])

    def test_no_size_is_said_to_be_assumed(self):
        assumed = []
        text, rows = _snapshot([MINI], assumed=assumed)
        self.assertIn("# contract size ASSUMED: 100 shares", text)
        self.assertNotIn("multiplier", text.replace("# contract", ""))
        self.assertEqual([r.cost for r in rows], [200.0])
        self.assertEqual(assumed, [OCC])

    def test_the_books_size_still_wins(self):
        assumed = []
        text, _rows = _snapshot(
            [MINI], book_symbols={OCC}, book_multipliers={OCC: 10},
            live_sizes={7001: 100.0}, assumed=assumed)
        self.assertIn("multiplier = 10.0", text)
        self.assertEqual(assumed, [])
        # Held by the books with no size: their standard 100, nothing
        # assumed.
        text, _rows = _snapshot([MINI], book_symbols={OCC}, assumed=assumed)
        self.assertNotIn("ASSUMED", text)
        self.assertEqual(assumed, [])

    def test_the_fetch_asks_questrade_for_unbooked_options_only(self):
        held = dict(MINI, symbol="SYN20Jan26P80.00", symbolId=7005)
        text, rows, said, calls = _live(
            [MINI, held, {"symbol": "QZA.TO", "symbolId": 7000,
                          "openQuantity": 5, "averageEntryPrice": 3}],
            sizes={7001: 10.0},
            book=[{"symbol": "SYN260120P00080000.US"}])
        self.assertEqual(calls, [[7001]])
        self.assertIn("multiplier = 10.0", text)
        self.assertEqual(sorted((r.symbol, r.cost) for r in rows),
                         [("QZA.TO", 15.0), (OCC, 20.0),
                          ("SYN260120P00080000.US", 200.0)])
        self.assertFalse([m for m in said if "ASSUMED" in m], said)

    def test_an_adjusted_contracts_deliverable_is_not_its_size(self):
        # backlog TJ-0670: after a 1-for-10 reverse split the contract
        # (root SYN1) delivers 10 SYN shares, but its premium is still
        # paid on 100: Questrade's `multiplier` is the shares delivered.
        # A size other than 100 is taken only from a contract whose
        # root is its underlying's symbol (not adjusted); else unknown.
        def http(_url):
            dlv = {"underlyings": [{"multiplier": 10,
                                    "underlyingSymbol": "SYN",
                                    "underlyingSymbolId": 7000}],
                   "cashInLieu": 0}
            return json.dumps({"symbols": [
                {"symbolId": 7010, "symbol": "SYN115Jan27C10.00",
                 "optionRoot": "SYN1", "optionContractDeliverables": dlv},
                # No root stated or readable: not known either.
                {"symbolId": 7011, "optionContractDeliverables": dlv},
                # The root from the symbol when optionRoot is absent.
                {"symbolId": 7012, "symbol": "SYN115Jan27P10.00",
                 "optionContractDeliverables": dlv},
                # An adjusted contract delivering 100 shares: 100.
                {"symbolId": 7013, "symbol": "SYN115Jan27C12.00",
                 "optionRoot": "SYN1", "optionContractDeliverables": {
                     "underlyings": [{"multiplier": 100,
                                      "underlyingSymbol": "QZB"}],
                     "cashInLieu": 0}},
                # Root SYN on the listing SYN.TO: not adjusted.
                {"symbolId": 7014, "symbol": "SYN20Jan26C88.00",
                 "optionRoot": "SYN", "optionContractDeliverables": {
                     "underlyings": [{"multiplier": 10,
                                      "underlyingSymbol": "SYN.TO"}],
                     "cashInLieu": 0}}]}).encode()
        got = api.qt_contract_sizes(SESSION, [7010, 7011, 7012, 7013, 7014],
                                    http)
        self.assertEqual(got, {7010: None, 7011: None, 7012: None,
                               7013: 100.0, 7014: 10.0})
        adjusted = dict(MINI, symbol="SYN115Jan27C10.00", symbolId=7010)
        text, rows = _snapshot([adjusted], live_sizes=got)
        self.assertIn("contract_multiplier_unknown = true", text)
        self.assertEqual([(r.symbol, r.cost) for r in rows],
                         [("SYN1270115C00010000.US", None)])

    def test_a_failed_lookup_is_said(self):
        text, rows, said, _calls = _live(
            [MINI], sizes=RuntimeError("/v1/symbols: HTTP 500"))
        self.assertIn("ASSUMED", text)
        note = " ".join(" ".join(m.split()) for m in said if "ASSUMED" in m)
        self.assertIn(f"1 option(s) not in the books yet ({OCC})", note)
        self.assertIn("lookup failed (/v1/symbols: HTTP 500)", note)
        self.assertEqual([r.cost for r in rows], [200.0])


if __name__ == "__main__":
    unittest.main()
