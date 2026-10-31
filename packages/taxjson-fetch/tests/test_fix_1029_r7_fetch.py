"""Regression pins for the 2026-10-29 fix round 7, group "fetch"
(backlog TJ-0857, TJ-0867, TJ-0868, TJ-0869, TJ-0880, TJ-0885,
TJ-0735). Every broker response is mocked; nothing touches the
network. Synthetic numbers, account ids and tickers (SYN) only; each
test's files live in a folder of their own.
"""
# First: puts the plugin, the core and its test helpers on sys.path.
import _support  # noqa: F401
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from taxjson_fetch import api
from taxjson_fetch import command as fc
from taxjson.lib import markets
from taxjson.lib.positions_reports import read_positions

NUMBER = "99900001"   # pii-ok (synthetic)
SESSION = {"api_server": "https://api01.iq.questrade.com",
           "access_token": "x"}

# A synthetic IB statement (section,Header/Data CSV).
HEAD = ("Statement,Header,Field Name,Field Value\n"
        "Statement,Data,BrokerName,Interactive Brokers\n"
        "Statement,Data,Title,Activity Statement\n")
TRADES_H = ("Trades,Header,DataDiscriminator,Asset Category,Currency,"
            "Account,Symbol,Date/Time,Quantity,T. Price,C. Price,"
            "Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n")


def _trade(when, qty, price, proceeds, acct="U1234567", code="O"):
    return (f"Trades,Data,Order,Stocks,CAD,{acct},SYN,\"{when}\",{qty},"
            f"{price},0,{proceeds},0,0,0,0,{code}\n")


def _period(text):
    return f"Statement,Data,Period,\"{text}\"\n"


def _flex_request(root, *, year=2025, **args):
    said = []
    a = dict(flex_token="SYNTHFLEX", dry_run=False, days=None, year=None,
             from_date=None, positions=False, trim_overlap=False)
    a.update(args)
    req = SimpleNamespace(
        args=SimpleNamespace(**a), root=root, work=root / "work",
        config={"settings": {"year": year, "country": "canada"},
                "accounts": {"margin": {"type": "taxable",
                                        "brokerage": "ibkr_flex",
                                        "query_id": "123"}}},
        accounts=["margin"], say=said.append, json=True)
    return req, said


def _run_flex(root, download, **kw):
    """`tjs fetch` of one Flex account answering `download`.
    Returns (result or the SystemExit, said)."""
    req, said = _flex_request(root, **kw)
    with patch.object(api, "flex_fetch",
                      lambda *a, **k: download.encode()), \
            contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            return fc.run(req), said
        except SystemExit as e:
            return e, said


class TestFlexAdjacentYearKept(unittest.TestCase):
    """TJ-0869: a full calendar-year refresh deleted the next January's
    repurchase that denies a December loss (and the history before the
    year, which its cost reads) with no word."""

    BASE = (_trade("2025-06-02, 10:00:00", 10, 20, -200)
            + _trade("2025-12-22, 10:00:00", -10, 10, 100, code="C"))
    REBUY = _trade("2026-01-05, 10:00:00", 10, 10, -100)
    OLD = (HEAD + _period("January 1, 2025 - January 31, 2026") + TRADES_H
           + BASE + REBUY)
    NEW = (HEAD + _period("January 1, 2025 - December 31, 2025") + TRADES_H
           + BASE)

    def test_the_january_repurchase_is_a_lost_date(self):
        self.assertEqual(fc._flex_lost_dates(self.OLD, self.NEW, 2025),
                         ["2026-01-05"])

    def test_the_fetch_refuses_and_keeps_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "inputs" / "margin" / "ib_flex.csv"
            out.parent.mkdir(parents=True)
            out.write_text(self.OLD)
            res, _said = _run_flex(root, self.NEW)
            self.assertIsInstance(res, SystemExit)
            self.assertIn("2026-01-05", str(res.code))
            self.assertEqual(out.read_text(), self.OLD)
            self.assertTrue((out.parent / "ib_flex.csv.new").is_file())

    def test_earlier_history_is_kept_too(self):
        old = (HEAD + _period("June 1, 2024 - December 31, 2025")
               + TRADES_H + _trade("2024-06-03, 10:00:00", 10, 20, -200)
               + self.BASE)
        self.assertEqual(fc._flex_lost_dates(old, self.NEW, 2025),
                         ["2024-06-03"])

    def test_later_dates_are_not_the_years(self):
        # Past the 30 days after the year (and its settlements): nothing
        # of 2025 reads it, so a full-year refresh replaces it.
        old = (HEAD + _period("January 1, 2025 - March 31, 2026")
               + TRADES_H + self.BASE
               + _trade("2026-03-02, 10:00:00", 10, 10, -100))
        self.assertEqual(fc._flex_lost_dates(old, self.NEW, 2025), [])

    def test_a_full_year_refresh_of_the_same_period_still_replaces(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "inputs" / "margin" / "ib_flex.csv"
            out.parent.mkdir(parents=True)
            out.write_text(self.NEW)
            changed = self.NEW + _trade("2025-07-02, 10:00:00", 1, 20, -20)
            res, _said = _run_flex(root, changed)
            self.assertNotIsInstance(res, SystemExit)
            self.assertEqual(out.read_text(), changed)


class TestFlexRefreshHoldsItsOwnDates(unittest.TestCase):
    """TJ-1032: only what the download does not hold counts as lost: a
    refresh of the same full-year statement was refused for a ClosedLot
    row's open date or a late withholding adjustment dated before the
    year. TJ-0869's refusal of real lost activity stays."""

    TR_H = ("Trades,Header,DataDiscriminator,Asset Category,Currency,Account,"
            "Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,"
            "Basis,Realized P/L,MTM P/L,Code\n")
    SALE = _trade("2025-06-02, 10:00:00", -10, 30, 300, code="C")
    LOT = ('Trades,Data,ClosedLot,Stocks,CAD,U1234567,SYN,"2024-03-04",'
           "10,20,,,,200,100,,\n")
    WHT = ("Withholding Tax,Header,Currency,Date,Description,Amount,Code\n"
           "Withholding Tax,Data,USD,2024-12-16,SYN(US0000000001) Cash "
           "Dividend USD 0.10 per Share - US Tax,-1.50,\n")
    DIV = ("Dividends,Header,Currency,Date,Description,Amount\n"
           "Dividends,Data,USD,2024-12-16,SYN(US0000000001) Cash "
           "Dividend USD 0.10 per Share,10\n")
    Y25 = HEAD + _period("January 1, 2025 - December 31, 2025") + TR_H + SALE

    def test_the_same_statement_refreshes(self):
        for name, extra in (("closed lot", self.LOT),
                            ("withholding", self.WHT)):
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                text = self.Y25 + extra
                out = root / "inputs" / "margin" / "ib_flex.csv"
                out.parent.mkdir(parents=True)
                out.write_text(text)
                res, _said = _run_flex(root, text)
                self.assertNotIsInstance(res, SystemExit)
                self.assertEqual(fc._flex_lost_dates(text, text, 2025), [])

    def test_a_closed_lot_of_a_lost_purchase_does_not_hide_it(self):
        # The old file holds the 2024 purchase; the 2025 download only
        # names its date as a ClosedLot: the purchase is deleted.
        old = (HEAD + _period("January 1, 2024 - December 31, 2025")
               + self.TR_H + _trade("2024-03-04, 10:00:00", 10, 20, -200)
               + self.SALE)
        self.assertEqual(fc._flex_lost_dates(old, self.Y25 + self.LOT, 2025),
                         ["2024-03-04"])

    def test_a_late_adjustment_does_not_hide_the_lost_dividend(self):
        # The download keeps the adjustment of that date, not the dividend.
        old = (HEAD + _period("January 1, 2024 - December 31, 2025")
               + self.TR_H + self.SALE + self.DIV + self.WHT)
        self.assertEqual(fc._flex_lost_dates(old, self.Y25 + self.WHT, 2025),
                         ["2024-12-16"])

    def test_a_closed_lot_does_not_stretch_a_download_with_no_period(self):
        old = (HEAD + self.TR_H + _trade("2024-06-03, 10:00:00", 10, 20, -200)
               + self.SALE)
        new = HEAD + self.TR_H + self.SALE + self.LOT
        self.assertEqual(fc._flex_span(new), ("2025-06-02", "2025-06-02"))
        self.assertEqual(fc._flex_lost_dates(old, new, 2025), ["2024-06-03"])


class TestFlexOneAccount(unittest.TestCase):
    """TJ-0867: a combined statement (one Statement section, rows of two
    accounts) passed the one-account guard."""

    OTHER = "U7654321"   # pii-ok (synthetic)
    TWO = (HEAD + _period("January 1, 2025 - December 31, 2025") + TRADES_H
           + _trade("2025-06-02, 10:00:00", 10, 10, -100, acct="U1234567")
           + _trade("2025-06-02, 10:00:00", 20, 10, -200, acct=OTHER))

    def test_account_ids(self):
        self.assertEqual(api.flex_account_ids(self.TWO),
                         ["U1234567", self.OTHER])
        one = (HEAD + "Account Information,Header,Field Name,Field Value\n"
               "Account Information,Data,Account,U1234567 (Custom)\n"
               + TRADES_H
               + _trade("2025-06-02, 10:00:00", 10, 10, -100)
               + "Trades,Total,,Stocks,CAD,Total,,,,,,-100,0,0,0,0,\n")
        self.assertEqual(api.flex_account_ids(one), ["U1234567"])
        listed = (HEAD + "Account Information,Header,Field Name,"
                  "Field Value\nAccount Information,Data,Accounts Included,"
                  f"\"U1234567, {self.OTHER}\"\n")
        self.assertEqual(api.flex_account_ids(listed),
                         ["U1234567", self.OTHER])

    def test_a_two_account_download_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            res, _said = _run_flex(root, self.TWO)
            self.assertIsInstance(res, SystemExit)
            msg = str(res.code)
            self.assertIn("2 broker accounts", msg)
            self.assertIn("U1***", msg)
            self.assertIn("U7***", msg)
            self.assertNotIn("U1234567", msg)
            self.assertFalse(
                (root / "inputs" / "margin" / "ib_flex.csv").exists())

    def test_one_account_is_written(self):
        one = (HEAD + _period("January 1, 2025 - December 31, 2025")
               + TRADES_H + _trade("2025-06-02, 10:00:00", 10, 10, -100))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            res, _said = _run_flex(root, one)
            self.assertNotIsInstance(res, SystemExit)
            self.assertEqual(
                (root / "inputs" / "margin" / "ib_flex.csv").read_text(),
                one)


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


class TestTrimNeedsUsableRows(unittest.TestCase):
    """TJ-0868: --trim-overlap removed a manual export's valid rows while
    the fetched replacement had a blank required amount."""

    def _fetch(self, acts):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "inputs" / "margin"
            folder.mkdir(parents=True)
            good = _act("2025-06-02", "2025-06-03", 10, 10)
            manual = api.qt_to_csv([good], NUMBER).replace(",API\n",
                                                           ",Margin\n")
            (folder / "manual.csv").write_text(manual)
            req, said = _qt_request(root)
            err = io.StringIO()
            with patch.object(fc, "_qt_open_session",
                              return_value=(dict(SESSION), "x")), \
                    patch.object(api, "qt_activities",
                                 return_value=acts), \
                    patch.object(api, "qt_window",
                                 return_value=(date(2025, 6, 1),
                                               date(2025, 6, 30))), \
                    contextlib.redirect_stderr(err):
                res = fc.run(req)
            return (res, (folder / "manual.csv").read_text(), manual,
                    said, err.getvalue())

    def test_a_blank_amount_keeps_the_manual_rows(self):
        bad = _act("2025-06-02", "2025-06-03", 10, 10, netAmount=None)
        res, after, before, _said, err = self._fetch([bad])
        self.assertEqual(after, before)
        trimmed = res["margin"]["trimmed"]
        self.assertEqual([t["rows"] for t in trimmed], [0])
        self.assertIn("Net Amount", trimmed[0]["refused"])
        self.assertIn("not trimmed", err)
        self.assertIn("2025-06-02", err)

    def test_usable_rows_still_trim(self):
        good = _act("2025-06-02", "2025-06-03", 10, 10)
        res, after, _before, _said, _err = self._fetch([good])
        self.assertEqual([t["rows"] for t in res["margin"]["trimmed"]], [1])
        self.assertEqual(len(after.splitlines()), 1)

    def test_unusable_rows_named(self):
        acts = [_act("2025-06-02", "2025-06-03", 10, 10),
                _act("2025-06-04", "2025-06-05", 1, 10, grossAmount=""),
                _act("2025-06-04", "2025-06-05", 1, 10, tradeDate=None,
                     transactionDate="")]
        bad = api.qt_unusable_activities(acts)
        self.assertEqual(len(bad), 2)
        self.assertIn("Gross Amount", bad[0])
        self.assertIn("2025-06-04", bad[0])
        self.assertIn("Transaction Date", bad[1])


class TestNumericSpellingOverlap(unittest.TestCase):
    """TJ-0735: a boundary activity both API windows return counted
    twice when one window spelled 10 as 10.0."""

    def _download(self, second):
        first = _act("2025-01-06", "2025-01-07", 10, 1)
        boundary = _act("2025-01-28", "2025-01-29", 10, 10)
        sale = _act("2025-02-04", "2025-02-05", 20, 20, action="Sell",
                    grossAmount=400, netAmount=400)
        chunks = iter([[first, boundary], [second(boundary), sale]])

        def http(_url):
            return json.dumps({"activities": next(chunks)}).encode()
        # Windows of 28 days: Jan 1-28, then Jan 29-Feb 15.
        return api.qt_activities(dict(SESSION), NUMBER, date(2025, 1, 1),
                                 date(2025, 2, 15), http)

    def test_a_float_spelling_is_the_same_activity(self):
        same = self._download(lambda b: dict(b))
        floats = self._download(lambda b: dict(
            b, quantity=10.0, price=10.0, grossAmount=-100.0,
            netAmount=-100.0, commission=0.0))
        self.assertEqual(len(same), 3)
        self.assertEqual(len(floats), 3)

    def test_a_real_second_fill_is_kept(self):
        # Two copies in the later window, one in the earlier: the second
        # copy is a fill the first window did not see.
        first = _act("2025-01-28", "2025-01-29", 10, 10)
        chunks = iter([[first], [dict(first, quantity=10.0),
                                 dict(first, quantity=10.0)]])

        def http(_url):
            return json.dumps({"activities": next(chunks)}).encode()
        acts = api.qt_activities(dict(SESSION), NUMBER, date(2025, 1, 1),
                                 date(2025, 2, 15), http)
        self.assertEqual(len(acts), 2)

    def test_the_csv_merge_matches_numeric_spellings(self):
        b = _act("2025-01-28", "2025-01-29", 10, 10)
        old = api.qt_to_csv([b], NUMBER)
        new = api.qt_to_csv([dict(b, quantity=10.0, price=10.0,
                                  grossAmount=-100.0, netAmount=-100.0,
                                  commission=0.0)], NUMBER)
        self.assertNotEqual(old, new)
        merged, added = fc._merge_csv_text(old, new)
        self.assertEqual(added, 0)
        self.assertEqual(len(merged.splitlines()), 2)
        self.assertEqual(fc._qt_restatement_suspects(old, new), [])
        # A different amount is still a different row.
        other = api.qt_to_csv([dict(b, netAmount=-101.0)], NUMBER)
        merged, added = fc._merge_csv_text(old, other)
        self.assertEqual(added, 1)


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


OPT = {"symbol": "SYN20Nov26C5.00", "symbolId": 12345, "openQuantity": 1,
       "averageEntryPrice": 2}
OCC = "SYN261120C00005000"


class TestLiveMultRule(unittest.TestCase):
    """TJ-0857: a ticker.map MULT line is the contract size of a live
    option the books hold, as everywhere else."""

    def test_the_mult_line_wins_over_a_booked_contract(self):
        text, rows, _said, calls = _live(
            [OPT], [{"symbol": OCC + ".TO"}],
            ticker_map=f"MULT {OCC}.TO 10\n")
        self.assertIn("multiplier = 10.0", text)
        self.assertEqual([r.cost for r in rows], [20.0])
        self.assertEqual(calls, [])

    def test_a_root_line_wins_over_the_books_size(self):
        text, rows, _said, _calls = _live(
            [OPT], [{"symbol": OCC + ".TO", "multiplier": 100}],
            ticker_map="MULT SYN 10\n")
        self.assertEqual([r.cost for r in rows], [20.0])

    def test_an_unbooked_contract_needs_no_lookup(self):
        text, rows, said, calls = _live(
            [OPT], [], ticker_map=f"MULT {OCC}.US 10\n")
        self.assertEqual([r.cost for r in rows], [20.0])
        self.assertEqual(calls, [])
        self.assertFalse([s for s in said if "ASSUMED" in s])

    def test_no_line_keeps_the_books(self):
        text, rows, _said, _calls = _live(
            [OPT], [{"symbol": OCC + ".TO", "multiplier": 10}])
        self.assertEqual([r.cost for r in rows], [20.0])


class TestLiveVenueOfAClosedListing(unittest.TestCase):
    """TJ-0880: a fully closed Canadian contract in the books made the
    open US one of the same series .TO, costed in CAD."""

    def _book(self):
        def t(sym, qty, day):
            return {"action": "BUYSELL", "symbol": sym, "quantity": qty,
                    "date": day, "multiplier": 100,
                    "currency": "CAD" if sym.endswith(".TO") else "USD"}
        return [t(OCC + ".TO", 1, "2026-06-01"),
                t(OCC + ".TO", -1, "2026-06-02"),
                t(OCC + ".US", 1, "2026-06-03")]

    def test_questrade_says_usd(self):
        text, rows, _said, calls = _live([OPT], self._book(),
                                         lookup={12345: "USD"})
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(OCC + ".US", "USD")])
        self.assertEqual(calls, [("listing", [12345])])

    def test_questrade_says_cad(self):
        _text, rows, _said, _calls = _live([OPT], self._book(),
                                           lookup={12345: "CAD"})
        self.assertEqual([(r.symbol, r.currency) for r in rows],
                         [(OCC + ".TO", "CAD")])

    def test_no_answer_is_said_and_no_currency_guessed(self):
        text, rows, said, _calls = _live([OPT], self._book(),
                                         lookup=RuntimeError("down"))
        self.assertEqual([r.currency or "" for r in rows], [""])
        self.assertNotIn("currency =", text)
        self.assertIn("AMBIGUOUS", text)
        note = [s for s in said if "both" in s]
        self.assertTrue(note, said)
        self.assertIn("down", note[0])

    def test_one_listing_in_the_books_asks_nothing(self):
        _text, rows, _said, calls = _live([OPT], self._book()[2:])
        self.assertEqual([r.symbol for r in rows], [OCC + ".US"])
        self.assertEqual(calls, [])

    def test_the_lookup_reads_the_currency(self):
        asked = []

        def http(url):
            asked.append(url)
            return json.dumps({"symbols": [
                {"symbolId": 12345, "symbol": "SYN20Nov26C5.00",
                 "currency": "USD"},
                {"symbolId": 777, "currency": "CAD"},
                {"symbolId": 12346, "currency": "EUR"}]}).encode()
        with patch.object(api, "_qt_get",
                          lambda s, t, p, h: json.loads(h(p))):
            got = api.qt_option_currencies(dict(SESSION), [12345, 12346],
                                           http)
        self.assertEqual(got, {12345: "USD"})
        self.assertIn("ids=12345%2C12346", asked[0])


class TestTokenFileLink(unittest.TestCase):
    """TJ-0885: a token file that is a symlink to a shared token was
    replaced by a regular file of its own, and the shared chain died."""

    def _refresh(self, active):
        calls = []

        def refresh(token, _http):
            calls.append(token)
            if token != active[0]:
                raise RuntimeError("synthetic invalidated refresh token")
            active[0] = active[0] + "N"
            return {"api_server": "https://api01.iq.questrade.com",
                    "access_token": "SYNTHACCESS",
                    "refresh_token": active[0]}
        return refresh, calls

    def test_the_link_target_is_read_locked_and_written(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            target = d / "shared_token"
            target.write_text("SYNTHOLD\n")
            link = d / "tool_token"
            link.symlink_to(target.name)
            active = ["SYNTHOLD"]
            refresh, calls = self._refresh(active)
            with patch.object(api, "qt_refresh", side_effect=refresh):
                _s1, _t = fc._qt_open_session(link, None)
                s2, _t = fc._qt_open_session(target, None)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text().strip(), active[0])
            self.assertEqual(calls, ["SYNTHOLD", "SYNTHOLDN"])
            self.assertEqual(s2["refresh_token"], "SYNTHOLDNN")
            self.assertEqual(oct(target.stat().st_mode & 0o777), "0o600")
            self.assertFalse((d / "tool_token.lock").exists())

    def test_a_dangling_link_is_refused_before_the_token_is_spent(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            link = d / "tool_token"
            link.symlink_to("missing_token")
            with patch.object(api, "qt_refresh") as refresh, \
                    self.assertRaises(SystemExit) as cm:
                fc._qt_open_session(link, None, explicit="SYNTHNEW")
            refresh.assert_not_called()
            self.assertIn("missing_token", str(cm.exception.code))
            self.assertFalse((d / "missing_token").exists())

    def test_a_regular_file_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            tok = Path(td) / "tok"
            tok.write_text("SYNTHOLD\n")
            refresh, _calls = self._refresh(["SYNTHOLD"])
            with patch.object(api, "qt_refresh", side_effect=refresh):
                fc._qt_open_session(tok, None)
            self.assertFalse(tok.is_symlink())
            self.assertEqual(tok.read_text(), "SYNTHOLDN\n")
            self.assertTrue(os.path.exists(str(tok) + ".lock"))


if __name__ == "__main__":
    unittest.main()
