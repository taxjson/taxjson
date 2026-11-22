"""Shared test helpers: the Questrade fetch's rows, request and live run.

Shared from: test_fix_1029_r7_fetch, test_fix_1029_r7e_fetch

A synthetic account and session (`NUMBER`, `SESSION`), an activity
(`_act`) and the cash rows built on `_BASE` (`_DIV`, `_DEP`, `_FX`), a
`tjs fetch` request of one Questrade account (`_qt_request`), the live
holdings snapshot of one account (`_live`), whether the strict parser
refuses a download (`_parser_refuses`), and a live option (`LIVE`, its
contract `OCC`). Every broker response is mocked; nothing touches the
network.

Imported after `_bootstrap`, which each plugin test module imports
first (it puts the plugin and the core on sys.path).
"""
import contextlib
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from taxjson_fetch import api
from taxjson_fetch import command as fc
from taxjson.lib import markets
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.positions_reports import read_positions

NUMBER = "99900001"   # pii-ok (synthetic)
SESSION = {"api_server": "https://api01.iq.questrade.com",
           "access_token": "x"}


def _act(day, settle, qty, price, action="Buy", **kw):
    gross = qty * price
    a = dict(tradeDate=f"{day}T00:00:00-05:00",
             transactionDate=f"{settle}T00:00:00-05:00", action=action,
             symbol="SYN.TO", description="SYNTHETIC CORP", quantity=qty,
             price=price, grossAmount=-gross, commission=0,
             netAmount=-gross, currency="CAD", type="Trades")
    a.update(kw)
    return a


def _qt_request(root, **args):
    said = []
    a = dict(refresh_token="", dry_run=False, days=None, year=None,
             from_date="2025-06-01", positions=False, trim_overlap=True)
    a.update(args)
    req = SimpleNamespace(
        args=SimpleNamespace(**a), root=root, work=root / "work",
        config={"settings": {"year": 2025, "country": "canada"},
                "accounts": {"margin": {"type": "taxable",
                                        "brokerage": "questrade",
                                        "account": NUMBER}}},
        accounts=["margin"], say=said.append, json=True)
    return req, said


def _live(positions, book, *, sizes=None, lookup=None, ticker_map=""):
    """The live-holdings fetch of one account. Returns (snapshot text,
    rows, said, symbol-lookup calls)."""
    calls = []

    def sizes_of(_session, ids, _http):
        calls.append(("size", sorted(ids)))
        return dict(sizes or {})

    def items_of(_session, ids, _http):
        calls.append(("listing", sorted(ids)))
        if isinstance(lookup, Exception):
            raise lookup
        return dict(lookup or {})
    said = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work = root / "work"
        work.mkdir()
        (work / "margin_base.json").write_text(
            json.dumps({"transactions": book}))
        if ticker_map:
            (root / "ticker.map").write_text(ticker_map)
            markets.use_ticker_map(root / "ticker.map")
        cfg = {"settings": {"year": 2026},
               "accounts": {"margin": {"brokerage": "questrade",
                                       "account": NUMBER}}}
        try:
            with patch.object(fc, "_qt_open_session",
                              return_value=(dict(SESSION), "x")), \
                    patch.object(api, "qt_positions",
                                 return_value=positions), \
                    patch.object(api, "qt_contract_sizes",
                                 side_effect=sizes_of), \
                    patch.object(api, "qt_option_currencies",
                                 side_effect=items_of), \
                    contextlib.redirect_stderr(io.StringIO()):
                fc._qt_live_holdings(root, work, cfg, ["margin"], None,
                                     said.append)
        finally:
            markets.use_ticker_map(None)
        snap = root / "holdings" / "margin_live_holdings.toml"
        text = snap.read_text()
        rows = read_positions(snap).rows
    return text, rows, said, calls


_D = "2025-06-20T00:00:00.000000-04:00"
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


LIVE = {"symbol": "SYN18Dec26C6.00", "symbolId": 12345, "openQuantity": 1,
        "averageEntryPrice": 2}
OCC = "SYN261218C00006000"
