"""Regression tests for the low-round engine-area audit fixes (fixl/engine).

Synthetic data only; account labels are fake.
"""
import contextlib
import io
import json
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from taxjson.bin import to_base_curr as T
from taxjson.lib import price_chain as PC
from tax_rules import rule


def _weekdays(a, b):
    out, d = [], a
    while d <= b:
        if date.fromisoformat(d).weekday() < 5:
            out.append(d)
        d = T._shift(d, 1)
    return out


class TestFxCashRateAge(unittest.TestCase):
    """R1-151: fx-cash priced an event with the newest rate on file
    however old it was; the converter refuses anything past 5 days."""

    def test_latest_rate_bounded(self):
        hist = {"USD": {"2024-01-02": 1.32}}
        self.assertEqual(PC.latest_rate(hist, "USD", "2025-11-03"),
                         (1.32, "2024-01-02"))            # unbounded
        self.assertEqual(PC.latest_rate(hist, "USD", "2025-11-03",
                                        max_age_days=7), (None, None))
        self.assertEqual(PC.latest_rate(hist, "USD", "2024-01-05",
                                        max_age_days=7),
                         (1.32, "2024-01-02"))

    @rule("CA-FX-02")
    def test_fx_cash_counts_a_stale_rate_unrated(self):
        self._stale("canada")

    @rule("US-FX-02")
    def test_fx_cash_counts_a_stale_rate_unrated_us(self):
        self._stale("usa")

    def _stale(self, country):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        rows = [
            {"action": "BUYSELL", "date": "2024-01-02", "symbol": "XYZ.US",
             "quantity": -10, "net_amount": 1000.0, "currency": "USD"},
            {"action": "BUYSELL", "date": "2025-11-03", "symbol": "XYZ.US",
             "quantity": 10, "net_amount": 1000.0, "currency": "USD"},
        ]
        base, cur = ("CAD", "USD") if country == "canada" else ("USD", "CAD")
        for r in rows:
            r["currency"] = cur
        doc = build_ledger(rows, base, {cur: {"2024-01-02": 1.32}},
                           2025, country=country)
        self.assertEqual(doc["unrated"], {cur: 1})
        # Within the 5-day lookback the rate is used.
        rows[1]["date"] = "2024-01-05"
        doc = build_ledger(rows, base, {cur: {"2024-01-02": 1.32}},
                           2024, country=country)
        self.assertEqual(doc["unrated"], {})


class TestPriceCacheValues(unittest.TestCase):
    """R1-156 / R1-244: a cached price that is missing, null, 0,
    negative, NaN, Inf or text was served as a quote (0.0 -> a -100%
    LOSS in harvest; text crashed). It is a cache miss now."""

    def _fetch(self, rec):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".price_cache.json"
            p.write_text(json.dumps({"AAA.US": rec}, allow_nan=True))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                q = PC.fetch_prices({"AAA.US": "AAA"}, cache_path=p,
                                    fetchers=[])
            return q, err.getvalue()

    def test_bad_prices_are_misses(self):
        today = date.today().isoformat()
        for bad in (None, 0, -5, "n/a", float("nan"), float("inf")):
            rec = {"asof": today, "price": bad}
            q, err = self._fetch(rec)
            self.assertNotIn("AAA.US", q, bad)
            self.assertIn("no usable price", err)
        q, _ = self._fetch({"asof": today})              # key missing
        self.assertNotIn("AAA.US", q)

    def test_good_price_served(self):
        q, _ = self._fetch({"asof": date.today().isoformat(),
                            "price": 18.5})
        self.assertEqual(q["AAA.US"].price, 18.5)


class TestCacheLoadersSurviveNonUtf8(unittest.TestCase):
    """S055-04: a non-UTF-8 byte in a cache raised UnicodeDecodeError
    instead of degrading to a refetch."""

    def test_all_three_loaders(self):
        from taxjson.bin import fill_crypto_prices as F
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "c.json"
            bad.write_bytes(b'{"USDCAD-2025-01-02": "\xe9"}')
            self.assertEqual(PC._load_cache(bad), {})
            with mock.patch.object(T, "CACHE_FILE", str(bad)):
                self.assertEqual(T.load_cache(), {})
            with mock.patch.object(F, "CACHE_FILE", str(bad)):
                self.assertEqual(F.load_cache(), {})


class TestYfMapLoader(unittest.TestCase):
    """S076-24: lower-case keys were never matched. S077-07: a line
    with no target was dropped silently."""

    def test_case_and_short_line(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "yf_ticker.map").write_text(
                "oldco.to newco.to\nABC.TO\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                m = PC.load_yf_map([td])
        self.assertEqual(m, {"OLDCO.TO": ("newco.to", 1.0)})
        self.assertIn("'ABC.TO'", err.getvalue())
        self.assertIn("line ignored", err.getvalue())


class TestTvExchangeMapShortLine(unittest.TestCase):
    """S077-07 (sibling): tv_exchange.map dropped a line with no
    exchange silently."""

    def test_warns(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "tv_exchange.map").write_text("NVDA.US\n")
            gains = tmp / "gains.json"
            gains.write_text(json.dumps({"inventory": [
                {"symbol": "NVDA.US", "qty": 1, "total_cost": 100,
                 "currency": "USD"}]}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_export",
                 "--tradingview", str(gains)],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("line ignored", r.stderr)


class TestBocDegradedAnswer(unittest.TestCase):
    """S055-03: an HTTP 200 with no (or truncated) observations was
    recorded as coverage for good, so the dates kept a Yahoo close or a
    stale forward-filled 'boc' rate even after the Bank recovered."""

    TODAY = "2026-09-29"

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = mock.patch.object(T, "CACHE_FILE", str(Path(td.name) / "fx.json"))
        p.start()
        self.addCleanup(p.stop)
        self.empty = True
        self.calls = []

    def boc(self, cur, a, b):
        self.calls.append((a, b))
        if self.empty:
            return {}
        return {d: "1.3500" for d in _weekdays(a, b) if d < self.TODAY}

    def yahoo(self, ticker, a, b):
        return {d: 1.2 for d in _weekdays(a, b) if d < self.TODAY}

    def build(self, today=None):
        return T.build_rates("USD", "CAD", "2024-01-01", "2024-12-31",
                             today=today or self.TODAY, fetch_boc_fn=self.boc,
                             fetch_yahoo_fn=self.yahoo)

    def test_cold_empty_answer_is_rechecked(self):
        rows, _errors, notes = self.build()
        src = {d: s for d, _v, s in rows}
        self.assertEqual(src["2024-06-03"], "yahoo")      # fallback now
        self.assertTrue(any("no observations" in n for n in notes))
        # The Bank recovers: the next run asks again and its rates win.
        self.empty = False
        self.calls.clear()
        rows, _errors, _notes = self.build(today="2026-09-30")
        self.assertTrue(self.calls)
        src = {d: (v, s) for d, v, s in rows}
        self.assertEqual(src["2024-06-03"], ("1.3500", "boc"))

    def test_recheck_window_expires(self):
        self.build()
        self.calls.clear()
        later = T._shift(self.TODAY, T.SUSPECT_RECHECK_DAYS + 1)
        self.build(today=later)
        self.assertEqual(self.calls, [])      # accepted as the truth

    def test_healthy_answer_leaves_no_marker(self):
        self.empty = False
        _rows, _e, notes = self.build()
        self.assertFalse(any("no observations" in n for n in notes))
        cache = json.loads(Path(T.CACHE_FILE).read_text())
        self.assertNotIn("suspect", cache["_boc"]["USDCAD"])


class TestDiagnosticsNotRepeated(unittest.TestCase):
    """R1-181 (third part): the gains and blend stages both persist the
    engine's stderr, so each engine line appeared twice in _wash.sum."""

    def test_same_block_once(self):
        from taxjson.bin.taxjson_run import collect_diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            text = ("warning: option-replacement ... XYZ.US [x]\n"
                    "  continuation\n"
                    "NOTE: 1 pair(s) go short\n")
            (cache / "acct_blend.diag").write_text(text)
            (cache / "acct_gains.json.diag").write_text(
                text + "warning: only in gains\n")
            kept = collect_diagnostics(cache, "acct").splitlines()
        self.assertEqual(kept.count("warning: option-replacement ... "
                                    "XYZ.US [x]"), 1)
        self.assertEqual(kept.count("  continuation"), 1)
        self.assertEqual(kept.count("NOTE: 1 pair(s) go short"), 1)
        self.assertIn("warning: only in gains", kept)


class TestGrantWriteLoss(unittest.TestCase):
    """S069-01: under grant timing a write whose fee exceeds its premium
    fed the superficial-loss solver unconditionally, and the denial
    reached wash_sales/summary but never the grant record (invariant
    broken). S069-00: the same path ignored the tainted gate."""

    S = "ABC261218C00090000.TO"

    def _run(self, timing, flag, phantom=False):
        from taxjson.lib.core import TaxTransaction, get_tax_rules
        S = self.S
        tx = [TaxTransaction(action="BUYSELL", date="2026-03-02",
                             time="10:00:00", symbol=S, quantity=-50,
                             currency="CAD", price=0.01, commission=60.0,
                             net_amount=-10.0, account="margin"),
              TaxTransaction(action="BUYSELL", date="2026-03-20",
                             time="10:00:00", symbol=S, quantity=50,
                             currency="CAD", price=0.0, net_amount=0.0,
                             account="margin")]
        if phantom:
            tx.insert(0, TaxTransaction(
                action="OPENING_BALANCE", date="2026-01-02", time="00:00:00",
                symbol=S, quantity=-1, currency="CAD", account="margin"))
        sh = [TaxTransaction(action="BUYSELL", date="2026-03-04",
                             time="10:00:00", symbol=S, quantity=50,
                             currency="CAD", price=0.01, net_amount=51.0,
                             account="rrsp")]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = get_tax_rules("canada").compute_gains(
                tx, sheltered_transactions=sh, option_premium_timing=timing,
                option_grant_since=2025,
                option_buyback_loss_superficial=flag)
        self.assertNotIn("invariant broken", err.getvalue())
        return r

    @rule("CA-SL-11")
    def test_flag_off_both_timings_allow(self):
        for timing in ("grant", "close"):
            r = self._run(timing, False)
            self.assertAlmostEqual(r["summary"]["total_gain"], -10.0, 2)
            self.assertEqual(r["wash_sales"], [])

    @rule("CA-SL-12")
    def test_flag_on_both_timings_deny_and_record_agrees(self):
        for timing in ("grant", "close"):
            r = self._run(timing, True)
            recs = r["transactions"]
            self.assertEqual(len(recs), 1, timing)
            self.assertAlmostEqual(recs[0]["disallowed_amount"], 10.0, 2)
            self.assertAlmostEqual(r["summary"]["total_gain"], 0.0, 2)
            self.assertAlmostEqual(r["summary"]["total_disallowed"], 10.0, 2)

    def test_tainted_write_loss_never_feeds_solver(self):
        r = self._run("grant", True, phantom=True)
        self.assertEqual(r["wash_sales"], [])
        self.assertFalse(r["summary"].get("total_disallowed"))


def _one(book, country, year=2025, **req):
    """One country's run_gains on `book` (stderr captured)."""
    import copy
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)), [], [],
                        req=GainsRequest(country=country, taxable=True,
                                         year=year, **req))
    res["_stderr"] = err.getvalue()
    return res


def _us(book, year=2025):
    return {"usa": _one(book, "usa", year)}


def _btx(*a, **k):
    from tax_rules.dual import tx
    return tx(*a, **k)


class TestUsOptionReplacementWarning(unittest.TestCase):
    """R1-181, S069-17, S070-00, S070-01, S070-03, S070-04, S070-22: the
    US call-as-replacement warning (warn-only)."""

    CALL = "XYZ250620C00010000.US"

    def _warns(self, book, year=2025):
        r = _us(book, year)["usa"]
        return r.get("option_replacement_warnings") or [], r

    @rule("US-WASH-12")
    def test_one_contract_sized_and_used_once_across_fills(self):
        for fills in (1, 10):
            book = [_btx("BUYSELL", "2025-01-10", "XYZ.US", 1000, -20000)]
            for i in range(fills):
                book.append(_btx("BUYSELL", "2025-04-15", "XYZ.US",
                                 -1000 / fills, 10000 / fills,
                                 time=f"10:00:{i:02d}"))
            book.append(_btx("BUYSELL", "2025-04-22", self.CALL, 1, -100))
            w, r = self._warns(book)
            self.assertEqual(len(w), 1, fills)
            self.assertEqual(w[0]["covered_shares"], 100.0)
            self.assertAlmostEqual(w[0]["at_risk_amount"], -1000.0, 2)
            self.assertIn("up to 100 of the", r["_stderr"])

    @rule("US-WASH-12")
    def test_one_contract_two_losses(self):
        book = [_btx("BUYSELL", "2025-01-10", "LLL.US", 200, -6200),
                _btx("BUYSELL", "2025-05-01", "LLL.US", -100, 2550),
                _btx("BUYSELL", "2025-05-02", "LLL250919C00030000.US", 1,
                     -100),
                _btx("BUYSELL", "2025-05-08", "LLL.US", -100, 2550)]
        w, _ = self._warns(book)
        self.assertEqual([x["loss_date"] for x in w], ["2025-05-01"])
        self.assertAlmostEqual(w[0]["at_risk_amount"], -550.0, 2)

    @rule("US-WASH-12")
    def test_buy_to_close_is_not_an_acquisition(self):
        C = "XYZ250620C00030000.US"
        head = [_btx("BUYSELL", "2025-05-01", C, -1, 100),
                _btx("BUYSELL", "2025-01-10", "XYZ.US", 100, -2000),
                _btx("BUYSELL", "2025-06-02", "XYZ.US", -100, 1000)]
        w, _ = self._warns(head + [_btx("BUYSELL", "2025-06-12", C, 1, -50)])
        self.assertEqual(w, [])
        # Buying 2 against a -1 short: 1 closes, 1 opens.
        w, _ = self._warns(head + [_btx("BUYSELL", "2025-06-12", C, 2, -100)])
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["option_qty"], 1.0)

    @rule("US-WASH-01", "US-WASH-12")
    def test_window_edges(self):
        def book(day):
            return [_btx("BUYSELL", "2025-01-10", "XYZ.US", 100, -2000),
                    _btx("BUYSELL", "2025-04-15", "XYZ.US", -100, 1000),
                    _btx("BUYSELL", day, self.CALL, 1, -100)]
        for day, n in (("2025-03-16", 1), ("2025-03-15", 0),
                       ("2025-05-15", 1), ("2025-05-16", 0)):
            w, _ = self._warns(book(day))
            self.assertEqual(len(w), n, day)

    @rule("US-WASH-12")
    def test_prior_year_warning_not_printed_in_year_run(self):
        book = [_btx("BUYSELL", "2024-01-10", "XYZ.US", 100, -2000),
                _btx("BUYSELL", "2024-06-03", "XYZ.US", -100, 1000),
                _btx("BUYSELL", "2024-06-10", "XYZ241220C00010000.US", 1,
                     -100),
                _btx("BUYSELL", "2025-02-03", "ABC.US", 10, -100)]
        w, r = self._warns(book, year=2025)
        self.assertEqual(w, [])
        self.assertNotIn("option-replacement", r["_stderr"])
        w, r = self._warns(book, year=2024)
        self.assertEqual(len(w), 1)
        self.assertIn("option-replacement", r["_stderr"])

    def test_us_count_is_year_scoped(self):
        book = [_btx("BUYSELL", "2024-01-10", "AAA.US", 30, -300),
                _btx("BUYSELL", "2024-03-10", "AAA.US", -10, 110),
                _btx("BUYSELL", "2024-04-10", "AAA.US", -10, 110),
                _btx("BUYSELL", "2025-03-10", "AAA.US", -10, 120)]
        r = _us(book)["usa"]
        self.assertEqual(r["summary"]["count"], len(r["transactions"]))
        self.assertEqual(r["summary"]["count"], 1)


class TestCanadaCallRuleWindowEdges(unittest.TestCase):
    """S070-03 (Canada now enforces the call rule): a call bought on day
    -30 or +30 counts, day 31 does not; a call sold ON day +30 is not
    held at the end of day 30."""

    CALL = "XYZ250620C00010000.US"

    def _gain(self, extra):
        book = [_btx("BUYSELL", "2025-01-10", "XYZ.US", 100, -2000),
                _btx("BUYSELL", "2025-04-15", "XYZ.US", -100, 1000)] + extra
        return _one(book, "canada")["summary"]

    @rule("CA-SL-01", "CA-SL-02", "CA-SL-05")
    def test_edges(self):
        buy = lambda d: _btx("BUYSELL", d, self.CALL, 1, -100)  # noqa: E731
        self.assertAlmostEqual(self._gain([buy("2025-05-15")])
                               ["total_disallowed"], 1000.0, 2)
        self.assertFalse(self._gain([buy("2025-05-16")])
                         .get("total_disallowed"))
        self.assertAlmostEqual(self._gain([buy("2025-03-16")])
                               ["total_disallowed"], 1000.0, 2)
        self.assertFalse(self._gain([buy("2025-03-15")])
                         .get("total_disallowed"))
        sold = self._gain([buy("2025-04-20"),
                           _btx("BUYSELL", "2025-05-15", self.CALL, -1, 50)])
        self.assertFalse(sold.get("total_disallowed"))


class TestIdenticalRowsKeepTheirOwnDenial(unittest.TestCase):
    """R1-169 (US) / S069-21 (Canada): two byte-identical rows shared one
    content-hash id, so only one took its denial / basis bump."""

    def _tx(self, date, qty, net):
        from taxjson.lib.core import TaxTransaction
        return TaxTransaction(action="BUYSELL", date=date, time="10:00:00",
                              date_settle=date, symbol="XYZ.US",
                              quantity=qty, price=abs(net / qty),
                              net_amount=net, currency="USD",
                              account="margin")

    @rule("CA-SL-08")
    def test_canada_two_identical_loss_sells(self):
        from taxjson.lib.core import CanadaTaxRules
        book = [self._tx("2024-03-01", 10, 120.0),
                self._tx("2024-03-06", -3, 30.0),
                self._tx("2024-03-06", -3, 30.0),
                self._tx("2024-03-19", 20, 240.0)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = CanadaTaxRules().compute_gains(book)
        self.assertAlmostEqual(r["summary"]["total_disallowed"], 12.0, 2)
        inv = {i["symbol"]: i for i in r["inventory"]}
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 300.0, 2)
        self.assertIn("repeat an earlier row exactly", err.getvalue())

    @rule("US-WASH-09")
    def test_us_two_identical_replacement_buys(self):
        from taxjson.lib.core import USATaxRules
        book = [self._tx("2024-03-05", 50, 1000.0),
                self._tx("2024-04-09", -50, 500.0),
                self._tx("2024-05-01", 10, 80.0),
                self._tx("2024-05-01", 10, 80.0),
                self._tx("2024-09-01", -20, 200.0)]
        with contextlib.redirect_stderr(io.StringIO()):
            r = USATaxRules().compute_gains(book)
        sells = [g for g in r["transactions"] if g.get("date") == "2024-09-01"]
        self.assertEqual(sorted(round(g["cost"], 2) for g in sells),
                         [180.0, 180.0])
        self.assertAlmostEqual(r["summary"]["total_gain"], -460.0, 2)


class TestQuantityKeysNotRounded(unittest.TestCase):
    """R1-317 (part 1) / S074-22: trigger_qty and opening_qty are coin
    counts, but the gains JSON rounded them to 4 dp (6.76e-06 -> 0.0)."""

    def test_keys(self):
        from taxjson.lib.numeric import round_floats
        out = round_floats({"wash_trigger": {"trigger_qty": 6.76e-06,
                                             "trigger_price": 0.00001234},
                            "phantom_application_log": [
                                {"opening_qty": 12.345678}],
                            "option_qty": 0.333333333})
        self.assertEqual(out["wash_trigger"]["trigger_qty"], 6.76e-06)
        self.assertEqual(out["wash_trigger"]["trigger_price"], 0.0)
        self.assertEqual(out["phantom_application_log"][0]["opening_qty"],
                         12.345678)
        self.assertEqual(out["option_qty"], 0.333333333)

    def test_suggestions_keep_precision(self):
        from taxjson.lib.phantom_holdings import (PhantomCandidate,
                                                  format_suggestions)
        import dataclasses
        fields = {f.name for f in dataclasses.fields(PhantomCandidate)}
        kw = {k: None for k in fields}
        kw.update(symbol="BTC", account="crypto", registered=False,
                  first_negative_date="2025-01-02", peak_short=-3e-05,
                  end_position=-3e-05, disposition_count=1)
        doc = json.loads(format_suggestions([PhantomCandidate(**kw)]))
        self.assertEqual(doc[0]["_peak_short"], -3e-05)
        self.assertEqual(doc[0]["_end_position"], -3e-05)


class TestMergerReceiptPostedLater(unittest.TestCase):
    """S075-23: a merger receipt posted a day after the removal was not
    linked (same-date only); the RBC reorganization pairing allows ±7."""

    def _rows(self, recv_date):
        from taxjson.lib.core import TaxTransaction
        mk = lambda d, s, q, desc: TaxTransaction(  # noqa: E731
            action="BUYSELL", date=d, time="10:00:00", symbol=s,
            quantity=q, net_amount=0.0, currency="USD", account="margin",
            description=desc)
        return [mk("2025-07-01", "H015283.US", -100,
                   "MGR - HESS CORPORATION MERGER TO CHEVRON CORPORATION "
                   "1.025 NEW = 1 OLD"),
                mk(recv_date, "CVX.US", 102,
                   "MGR - CHEVRON CORPORATION SHRS RECEIVED THRU MERGER")]

    def test_next_day_receipt_links(self):
        from taxjson.lib.phantom_holdings import detect_corp_action_links
        links = detect_corp_action_links(self._rows("2025-07-02"))
        self.assertEqual([(l.old_symbol, l.new_symbol) for l in links],
                         [("H015283.US", "CVX.US")])
        self.assertEqual(detect_corp_action_links(self._rows("2025-07-20")),
                         [])


class TestUsRenameMergeKeepsAcquisitionTime(unittest.TestCase):
    """S070-14: after a rename, same-date lots kept insertion order (the
    target's lots first) instead of acquisition time."""

    def _run(self, t_old, t_new):
        from taxjson.lib.core import TaxTransaction, USATaxRules

        def mk(d, s, q, net, t="10:00:00"):
            return TaxTransaction(action="BUYSELL", date=d, time=t,
                                  date_settle=d, symbol=s, quantity=q,
                                  price=abs(net / q), net_amount=net,
                                  currency="USD", account="ZZ1")
        txs = [mk("2025-01-06", "OLD.US", 100, 4000.0, t_old),
               mk("2025-01-06", "NEW.US", 100, 5000.0, t_new),
               TaxTransaction(action="SPLIT", date="2025-02-03",
                              time="00:00:00", symbol="OLD.US",
                              symbol_new="NEW.US", quantity=1.0,
                              currency="USD", date_settle="2025-02-03",
                              account="ZZ1"),
               mk("2025-03-03", "NEW.US", -100, 4500.0)]
        with contextlib.redirect_stderr(io.StringIO()):
            return USATaxRules().compute_gains(txs)

    @rule("US-BASIS-01")
    def test_fifo_by_time(self):
        r = self._run("09:00:00", "15:00:00")
        self.assertAlmostEqual(r["summary"]["total_gain"], 500.0, 2)
        r = self._run("15:00:00", "09:00:00")
        self.assertAlmostEqual(r["summary"]["total_gain"], -500.0, 2)


class TestClockTimeNormalisedAtLoad(unittest.TestCase):
    """S071-13: an unpadded loss time ('9:30:00') sorted after its own
    superficial-loss bump ('09:30:01'), moving $30 between years; a
    seconds-less time ('09:30') crashed the engine."""

    def _book(self, loss_time):
        rows = [
            {"action": "BUYSELL", "date": "2024-12-02", "time": "10:00:00",
             "symbol": "XYZ.TO", "quantity": 100, "price": 10.0,
             "net_amount": 1000.0, "currency": "CAD", "account": "m"},
            {"action": "BUYSELL", "date": "2024-12-20", "time": loss_time,
             "symbol": "XYZ.TO", "quantity": -80, "price": 8.0,
             "net_amount": 640.0, "currency": "CAD", "account": "m"},
            {"action": "BUYSELL", "date": "2025-01-10", "time": "10:00:00",
             "symbol": "XYZ.TO", "quantity": 20, "price": 10.0,
             "net_amount": 200.0, "currency": "CAD", "account": "m"},
            {"action": "BUYSELL", "date": "2025-03-03", "time": "10:00:00",
             "symbol": "XYZ.TO", "quantity": -20, "price": 10.0,
             "net_amount": 200.0, "currency": "CAD", "account": "m"},
        ]
        from taxjson.lib.core import coerce_transaction_row
        return [coerce_transaction_row(r, i, "t") for i, r in
                enumerate(rows)]

    def _gains(self, loss_time):
        from taxjson.lib.core import CanadaTaxRules
        with contextlib.redirect_stderr(io.StringIO()):
            r = CanadaTaxRules().compute_gains(self._book(loss_time))
        by_year = {}
        for g in r["transactions"]:
            y = g["date"][:4]
            by_year[y] = round(by_year.get(y, 0.0) + g["gain"], 2)
        return by_year, r["summary"]["total_disallowed"]

    @rule("CA-SL-09")
    def test_spellings_agree(self):
        ref = self._gains("09:30:00")
        self.assertEqual(self._gains("9:30:00"), ref)
        self.assertEqual(self._gains("09:30"), ref)

    def test_bad_time_refused(self):
        from taxjson.lib.core import coerce_transaction_row
        with self.assertRaisesRegex(ValueError, "clock time"):
            coerce_transaction_row(
                {"action": "BUYSELL", "date": "2025-01-02",
                 "time": "25:00:00", "symbol": "X"}, 0, "t")
        self.assertEqual(coerce_transaction_row(
            {"action": "BUYSELL", "date": "2025-01-02", "time": "9:05",
             "symbol": "X"}, 0, "t").time, "09:05:00")


class TestSplitGainsPerAccount(unittest.TestCase):
    """S050-11, S050-15, S050-19, S050-21, S050-23: taxjson-split-gains."""

    def _combined(self):
        return {
            "summary": {"year": "2025", "tax_date_basis": "settle",
                        "count": 3, "total_gain": 0.0},
            "transactions": [
                {"tx_id": "a1", "account": "a", "symbol": "AAA.US",
                 "date": "2025-03-01", "gain": -100.0, "qty": 10},
                {"tx_id": "b1", "account": "b", "symbol": "BBB.US",
                 "date": "2025-03-01", "gain": 50.0, "qty": 10},
                {"tx_id": "b2", "account": "b", "symbol": "BBB.US",
                 "date": "2025-04-01", "gain": 50.0, "qty": 10}],
            "inventory": [
                {"symbol": "XYZ.TO", "qty": 200.0, "total_cost": 3000.0,
                 "position_start_date": "2021-03-01",
                 "last_acq_date": "2025-08-01"},
                {"symbol": "ETH", "qty": 1.0000004, "total_cost": 4000.0016,
                 "position_start_date": "2024-01-02",
                 "last_acq_date": "2025-01-02"}],
            "option_replacement_warnings": [
                {"loss_id": "a1", "rule": "call_vs_share_loss"},
                {"loss_id": "zz", "rule": "call_vs_share_loss"}],
            "phantom_application_log": [
                {"account": "a", "symbol": "Q.US", "inserted": False},
                {"account": "b", "symbol": "R.US", "inserted": False}],
        }

    def _base(self, *rows):
        return [dict(action="BUYSELL", date=d, date_settle=d, time="10:00:00",
                     symbol=s, quantity=q, account="x") for d, s, q in rows]

    def test_slice(self):
        from taxjson.bin.taxjson_split_gains import split_for_account
        base_b = self._base(("2025-08-01", "XYZ.TO", 100),
                            ("2025-01-02", "ETH", 4e-07))
        out = split_for_account(self._combined(), "b", base_b)
        inv = {r["symbol"]: r for r in out["inventory"]}
        self.assertEqual(inv["XYZ.TO"]["position_start_date"], "2025-08-01")
        self.assertEqual(inv["XYZ.TO"]["last_acq_date"], "2025-08-01")
        self.assertEqual(inv["ETH"]["qty"], 4e-07)
        self.assertEqual(out["summary"]["count"], 2)
        self.assertNotIn("option_replacement_warnings", out)
        self.assertEqual([e["symbol"] for e in
                          out["phantom_application_log"]], ["R.US"])
        base_a = self._base(("2021-03-01", "XYZ.TO", 100))
        out = split_for_account(self._combined(), "a", base_a)
        self.assertEqual(out["inventory"][0]["position_start_date"],
                         "2021-03-01")
        self.assertEqual(out["summary"]["count"], 1)
        self.assertEqual([w["loss_id"] for w in
                          out["option_replacement_warnings"]], ["a1"])

    def test_position_start_after_a_round_trip(self):
        from taxjson.bin.taxjson_split_gains import _position_starts
        rows = self._base(("2021-03-01", "XYZ.TO", 100),
                          ("2023-03-01", "XYZ.TO", -100),
                          ("2024-05-01", "XYZ.TO", 50))
        self.assertEqual(_position_starts(rows, "settle"),
                         {"XYZ.TO": "2024-05-01"})

    def test_bad_or_missing_base_is_fatal(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            comb = td / "c.json"
            comb.write_text(json.dumps(self._combined()))
            bad = td / "bad.json"
            bad.write_text("{not json")
            for base in (bad, td / "missing.json"):
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_split_gains",
                     str(comb), "--account", "a", "--base", str(base)],
                    capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("could not read --base", r.stderr)


def _ca(book, **kw):
    from taxjson.lib.core import CanadaTaxRules
    with contextlib.redirect_stderr(io.StringIO()):
        return CanadaTaxRules().compute_gains(book, trace=True, **kw)


def _cx(date, sym, qty, net, price=None, account="margin", **kw):
    from taxjson.lib.core import TaxTransaction
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action="BUYSELL", date=date, time="10:00:00",
                          date_settle=date, symbol=sym, quantity=qty,
                          price=price, net_amount=net, currency="CAD",
                          account=account, **kw)


class TestTraceColumns(unittest.TestCase):
    """S069-08 (per-share columns divide by contracts x 100), S070-24 /
    S071-00 (the trace's derived fee is the signed residual, with no
    25%-of-net guard), S078-06 (full vs partial uses a relative test),
    S069-24 (a buy-to-close is not an eligible candidate)."""

    OPT = "XYZ260320C00050000.TO"

    def test_option_per_share_columns(self):
        r = _ca([_cx("2026-01-05", self.OPT, 2, 1001.30, price=5.0,
                     commission=1.30),
                 _cx("2026-02-02", self.OPT, -1, 398.70, price=4.0,
                     commission=1.30)])
        trace = "\n".join(r["transactions"][0]["trace"])
        self.assertIn("ACB/Sh:  5.0065", trace)
        self.assertIn("Gain/Sh: -1.0195", trace)

    def test_trace_fee(self):
        from taxjson.lib.core import _effective_fee_for_trace as fee
        O = "SOUN240419C00007000.US"
        self.assertAlmostEqual(fee(_cx("2024-04-08", O, 4, 43.95,
                                       price=0.08)), 11.95, 4)
        self.assertAlmostEqual(fee(_cx("2024-04-08", O, -2, 6.01,
                                       price=0.04)), 1.99, 4)
        self.assertAlmostEqual(fee(_cx("2024-04-08", O, -10, -12.45,
                                       price=0.01)), 22.45, 4)
        # Sub-cent rounding on a zero-commission sale is not a fee.
        self.assertEqual(fee(_cx("2025-06-16", "CQQQ.US", -350, 15141.53,
                                 price=43.2615)), 0.0)
        self.assertAlmostEqual(fee(_cx("2025-06-16", "ABC.US", 500, 5204.95,
                                       price=10.41)), -0.05, 4)
        # A units mismatch (per-contract quote, per-share net) still
        # shows 0, not a fictitious fee.
        self.assertEqual(fee(_cx("2025-06-16", O, 1, 520.0, price=520.0)),
                         0.0)

    def test_partial_label_for_crypto_sized_units(self):
        from taxjson.lib.trace_format import _render_wash_window
        ww = {"window_start": "2025-01-01", "window_end": "2025-02-28",
              "loss_date": "2025-01-31",
              "loss_qty": 0.0015, "disallowed_qty": 0.0006,
              "bal_at_end": 0.0006, "loss_direction": "LONG",
              "transactions": []}
        text = "\n".join(_render_wash_window({"wash_window": ww}))
        self.assertIn("partial disallowance — 0.0006 of 0.0015", text)
        ww["disallowed_qty"] = 0.0015
        text = "\n".join(_render_wash_window({"wash_window": ww}))
        self.assertIn("full disallowance", text)

    def test_buy_to_close_role(self):
        call = "ABC250620C00030000.TO"
        r = _ca([_cx("2025-01-06", call, 1, 500.0),
                 _cx("2025-03-03", call, -1, 200.0),
                 _cx("2025-03-05", call, 1, 210.0)],
                sheltered_transactions=[
                    _cx("2025-02-01", call, -1, 100.0, account="tfsa"),
                    _cx("2025-03-10", call, 1, 90.0, account="tfsa")])
        recs = [g for g in r["transactions"] if g.get("wash_window")]
        self.assertTrue(recs)
        roles = {(t["date"], t["account"]): t["role"]
                 for t in recs[0]["wash_window"]["transactions"]}
        self.assertEqual(roles[("2025-03-10", "tfsa")], "cover")


class TestPhantomFileDiagnostics(unittest.TestCase):
    """S076-05: a stale or mistyped phantoms.json entry was noted only in
    the gains JSON, and any phantom file switched the go-short hint off.
    Also: every stage handed the whole project file printed a 'no rows'
    warning for every other account's entry."""

    def _books(self):
        main = [_cx("2025-02-03", "ABC.TO", -10, 100.0),
                _cx("2025-03-03", "XYZ.TO", 10, 100.0),
                _cx("2025-04-03", "XYZ.TO", -10, 120.0),
                _cx("2025-05-03", "DEF.TO", -10, 100.0)]
        sh = [_cx("2025-02-03", "QQQ.TO", 10, 100.0, account="tfsa")]
        return main, sh

    def test_notes(self):
        from taxjson.lib.pipeline import prepare_books
        main, sh = self._books()
        with tempfile.TemporaryDirectory() as td:
            ph = Path(td) / "phantoms.json"
            ph.write_text(json.dumps([
                {"symbol": "ABC.TO", "account": "margin"},
                {"symbol": "XYZ.TO", "account": "margin"},
                {"symbol": "ABCD.TO", "account": "margin"},
                {"symbol": "ZZZ.TO", "account": "rrsp"}]))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                prepare_books(main, sh, [], taxable=True,
                              incomplete_history=ph)
        e = err.getvalue()
        self.assertEqual(e.count("ABCD.TO / margin, but no row"), 1)
        self.assertIn("XYZ.TO / margin, but its rows never go short", e)
        self.assertNotIn("ZZZ.TO", e)       # another account's entry
        self.assertNotIn("ABC.TO / margin", e)
        # The go-short hint still names the pair the file does not list.
        self.assertIn("go short in this data: DEF.TO/margin", e)
        self.assertIn("find-missing-history --gen-phantoms", e)


class TestJournalNoteDirection(unittest.TestCase):
    """S076-18: the unmapped cross-listing NOTE picked the rule direction
    from a '.US' suffix only, so DLR.TO -> DLR.U.TO suggested folding the
    CAD listing into the USD unit."""

    def _note(self, legs, base):
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        from taxjson.lib.core import TaxTransaction
        rows = [TaxTransaction(action="TRANSFER", date=d, time="10:00:00",
                               symbol=s, quantity=q, net_amount=1000.0,
                               currency="CAD", account="rrsp")
                for d, s, q in legs]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _drop_self_cancelling_transfers(rows, main_transactions=[],
                                            base_currency=base)
        return err.getvalue()

    def test_unit_listing(self):
        for legs in ([("2025-02-03", "DLR.TO", -1000),
                      ("2025-02-04", "DLR.U.TO", 1000)],
                     [("2025-02-03", "DLR.U.TO", -1000),
                      ("2025-02-04", "DLR.TO", 1000)]):
            self.assertIn("TOBASE DLR.U.TO DLR.TO", self._note(legs, "CAD"))
        legs = [("2025-02-03", "AEM.TO", -10), ("2025-02-04", "AEM.US", 10)]
        self.assertIn("TOBASE AEM.TO AEM.US", self._note(legs, "USD"))
        self.assertIn("TOBASE AEM.US AEM.TO", self._note(legs, "CAD"))


if __name__ == "__main__":
    unittest.main()
