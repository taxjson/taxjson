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


if __name__ == "__main__":
    unittest.main()
