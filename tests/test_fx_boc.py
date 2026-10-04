"""FX audit (2026-09): Bank of Canada as the primary FX source, a history
window that no longer ends 5.5 years back, default-rate fallbacks as
validation ERRORs, TAXJSON_OFFLINE parsing, and the offline FX stage.

Every fetcher is stubbed — the suite never touches the network."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from taxjson.bin import to_base_curr as T
from taxjson.bin.taxjson_convert_currency import (
    load_exchange_rates, load_rate_sources,
)
from taxjson.lib.offline import offline_enabled
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-09-28"                                   # a Monday


def _days(a, b):
    out, d = [], a
    while d <= b:
        out.append(d)
        d = T._shift(d, 1)
    return out


def _weekdays(a, b):
    from datetime import date
    return [d for d in _days(a, b)
            if date.fromisoformat(d).weekday() < 5]


class _CacheCase(unittest.TestCase):
    """A private cache file per test and recording fetch stubs."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        p = mock.patch.object(T, "CACHE_FILE",
                              str(Path(self.td.name) / "fx.json"))
        p.start()
        self.addCleanup(p.stop)
        self.boc_calls, self.yahoo_calls = [], []
        self.boc_rate = "1.3500"
        self.yahoo_rate = 1.2000
        self.boc_fail = None
        self.boc_skip = set()                       # dates BoC lacks

    def boc(self, cur, a, b):
        self.boc_calls.append((cur, a, b))
        if self.boc_fail:
            raise self.boc_fail
        return {d: self.boc_rate for d in _weekdays(a, b)
                if d not in self.boc_skip and d < TODAY}

    def yahoo(self, ticker, a, b):
        self.yahoo_calls.append((ticker, a, b))
        return {d: self.yahoo_rate for d in _weekdays(max(a, "2003-12-01"), b)
                if d < TODAY}

    def build(self, frm="USD", to="CAD", start="2016-12-20",
              end=TODAY, offline=False):
        return T.build_rates(frm, to, start, end, today=TODAY,
                             offline=offline, fetch_boc_fn=self.boc,
                             fetch_yahoo_fn=self.yahoo)


class TestBankOfCanadaPrimary(_CacheCase):
    @rule("CA-FX-01")
    def test_boc_from_2017_yahoo_only_before(self):
        rows, errors, _ = self.build()
        self.assertEqual(errors, [])
        src = {d: s for d, _v, s in rows}
        self.assertEqual(src["2016-12-30"], "yahoo")
        self.assertEqual(src["2017-01-02"], "yahoo")
        self.assertEqual(src["2017-01-03"], "boc")
        self.assertEqual(src["2026-09-25"], "boc")
        # Yahoo asked ONLY for the pre-2017 range.
        self.assertEqual(self.yahoo_calls,
                         [("USDCAD=X", "2016-12-20", "2017-01-02")])
        self.assertEqual(self.boc_calls[0][:2], ("USD", "2017-01-03"))

    def test_weekend_takes_prior_business_day(self):
        self.boc_rate = None
        rates = {"2025-01-03": "1.4442", "2025-01-06": "1.4390"}
        self.boc = lambda c, a, b: {d: v for d, v in rates.items()
                                    if a <= d <= b}
        rows, _, _ = self.build(start="2025-01-03", end="2025-01-06")
        got = {d: v for d, v, _s in rows}
        self.assertEqual(got["2025-01-04"], "1.4442")    # Saturday
        self.assertEqual(got["2025-01-05"], "1.4442")    # Sunday
        self.assertEqual(got["2025-01-06"], "1.4390")

    def test_output_format_and_source_column(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(T, "fetch_boc", self.boc), \
                mock.patch.object(T, "fetch_yahoo", self.yahoo), \
                mock.patch.object(T, "fetch_boc_noon",
                                  lambda *_a: {}), \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": ""}), \
                redirect_stdout(out), redirect_stderr(err):
            rc = T.main(["USD", "CAD", "--start", "2016-12-28",
                         "--end", "2017-01-06"])
        self.assertEqual(rc, 0)
        lines = out.getvalue().splitlines()
        self.assertIn("2017-01-03 12:00:00 USD CAD 1.3500 boc", lines)
        self.assertIn("2016-12-28 12:00:00 USD CAD 1.2 yahoo", lines)
        self.assertIn("FX USD→CAD: Bank of Canada Valet for 4 dates, "
                      "Yahoo fallback for 6", err.getvalue())
        # The consumer contract is unchanged: five-column loaders read it.
        rf = Path(self.td.name) / "to_base.csv"
        rf.write_text(out.getvalue())
        hist = load_exchange_rates(rf, "CAD")
        self.assertEqual(str(hist["USD"]["2017-01-03"]), "1.3500")
        self.assertEqual(load_rate_sources(rf, "CAD")["USD"]["2016-12-28"],
                         "yahoo")

    def test_unpublished_currency_uses_yahoo(self):
        rows, _, notes = self.build(frm="ILS", start="2025-01-01",
                                    end="2025-01-10")
        self.assertEqual(self.boc_calls, [])
        self.assertTrue(rows and all(s == "yahoo" for _d, _v, s in rows))

    def test_series_not_found_falls_back_and_is_remembered(self):
        self.boc_fail = T.SeriesNotFound("FXUSDCAD")
        rows, _, notes = self.build(start="2025-01-01", end="2025-01-10")
        self.assertTrue(rows and all(s == "yahoo" for _d, _v, s in rows))
        self.assertTrue(any("publishes no USD/CAD" in n for n in notes))
        self.boc_calls.clear()
        self.build(start="2025-01-01", end="2025-01-10")
        self.assertEqual(self.boc_calls, [])             # not re-asked

    def test_failed_boc_fetch_is_not_papered_over_with_yahoo(self):
        self.boc_fail = OSError("network down")
        rows, errors, _ = self.build(start="2016-12-28", end="2017-01-10")
        self.assertTrue(any("Bank of Canada" in e for e in errors))
        self.assertTrue(all(d < "2017-01-03" for d, _v, _s in rows))

    def test_stopped_series_gap_uses_yahoo(self):
        # A published series with a long hole (RUB after 2022-03).
        self.boc_skip = set(_days("2025-02-01", "2025-03-31"))
        rows, _, _ = self.build(start="2025-01-01", end="2025-04-30")
        src = {d: s for d, _v, s in rows}
        self.assertEqual(src["2025-01-15"], "boc")
        self.assertEqual(src["2025-02-05"], "boc")       # within 7 days
        self.assertEqual(src["2025-03-01"], "yahoo")
        self.assertEqual(src["2025-04-15"], "boc")

    @rule("US-FX-02")
    def test_non_cad_target_is_yahoo_only(self):
        rows, _, _ = self.build(frm="CAD", to="USD", start="2025-01-01",
                                end="2025-01-10")
        self.assertEqual(self.boc_calls, [])
        self.assertTrue(rows and all(s == "yahoo" for _d, _v, s in rows))


class TestWindowAndCache(_CacheCase):
    def test_default_start_reaches_old_history(self):
        # The window used to be today-2000 days: a 2015 trade silently
        # took the 1.35 default.
        self.assertEqual(T.DEFAULT_START, "2000-01-01")
        rows, _, _ = self.build(start=T.DEFAULT_START)
        dates = {d for d, _v, _s in rows}
        self.assertIn("2012-06-15", dates)
        self.assertIn("2015-03-02", dates)

    def test_cached_ranges_are_not_refetched(self):
        self.build(start="2010-01-01")
        self.boc_calls.clear()
        self.yahoo_calls.clear()
        rows, _, _ = self.build(start="2010-01-01")
        self.assertEqual(self.yahoo_calls, [])
        # Only the trailing week is re-asked from the Bank.
        self.assertEqual(len(self.boc_calls), 1)
        self.assertGreaterEqual(self.boc_calls[0][1], "2026-09-20")

    def test_yahoo_range_without_data_is_not_refetched(self):
        self.build(start="2000-01-01")        # stub has nothing pre-2003-12
        self.yahoo_calls.clear()
        self.build(start="2000-01-01")
        self.assertEqual(self.yahoo_calls, [])

    def test_today_is_never_covered(self):
        self.build(start="2026-09-01")
        cache = json.loads(Path(T.CACHE_FILE).read_text())
        self.assertEqual(cache["_coverage"]["boc:USDCAD"][-1][1],
                         "2026-09-27")

    def test_legacy_cache_keys_still_serve(self):
        legacy = {f"USDCAD-{d}": 1.31 for d in _days("2016-06-01",
                                                    "2016-12-31")}
        Path(T.CACHE_FILE).write_text(json.dumps(legacy))
        rows, _, _ = self.build(start="2016-06-01", end="2017-01-05")
        self.assertEqual(self.yahoo_calls,
                         [("USDCAD=X", "2017-01-01", "2017-01-02")])
        got = {d: (v, s) for d, v, s in rows}
        self.assertEqual(got["2016-07-04"], ("1.31", "yahoo"))
        self.assertEqual(got["2017-01-04"][1], "boc")


class TestOffline(_CacheCase):
    def test_offline_fetches_nothing_and_does_not_fail(self):
        # A cache warm through yesterday used to fail offline every new
        # day ("needs USDCAD=X <today>..<tomorrow>").
        self.build(start="2024-01-01", end="2026-09-27")
        self.boc_calls.clear()
        self.yahoo_calls.clear()
        rows, errors, notes = self.build(start="2024-01-01", offline=True)
        self.assertEqual((self.boc_calls, self.yahoo_calls, errors),
                         ([], [], []))
        self.assertTrue(any("TAXJSON_OFFLINE" in n for n in notes))
        self.assertEqual(rows[-1][0], TODAY)            # today's row too

    def test_offline_empty_cache_exits_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(T, "fetch_boc", self.boc), \
                mock.patch.object(T, "fetch_yahoo", self.yahoo), \
                mock.patch.object(T, "fetch_boc_noon",
                                  lambda *_a: {}), \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}), \
                redirect_stdout(out), redirect_stderr(err):
            rc = T.main(["USD", "CAD"])
        self.assertEqual(rc, 0)
        self.assertEqual((self.boc_calls, self.yahoo_calls), ([], []))

    def test_stale_cache_is_not_smeared_forward(self):
        self.build(start="2026-08-01", end="2026-08-31")
        rows, _, _ = self.build(start="2026-08-01", offline=True)
        self.assertEqual(max(d for d, _v, _s in rows), "2026-08-31")


class TestOfflineFlag(unittest.TestCase):
    def test_values(self):
        for v in ("1", "true", "TRUE", "yes", "on", " On "):
            self.assertTrue(offline_enabled({"TAXJSON_OFFLINE": v}), v)
        for v in ("0", "false", "no", "off", ""):
            self.assertFalse(offline_enabled({"TAXJSON_OFFLINE": v}), v)
        self.assertFalse(offline_enabled({}))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(offline_enabled({"TAXJSON_OFFLINE": "maybe"}))
        self.assertIn("not recognised", err.getvalue())

    def test_zero_does_not_block_crypto_lookup(self):
        from taxjson.bin import fill_crypto_prices as fcp

        class _Resp:
            def read(self):
                return json.dumps({"chart": {"result": [{"indicators": {
                    "quote": [{"close": [42.0]}]}}]}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch("urllib.request.urlopen", return_value=_Resp()):
            price = fcp.get_crypto_price("BTC", "2025-01-02")
        self.assertEqual(price, 42.0)

    def test_zero_does_not_block_price_chain(self):
        from taxjson.lib import price_chain as pc
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch.object(pc, "_yfinance_fetcher",
                                  return_value={"AAA": 10.0}) as yf_:
            pc.fetch_prices({"AAA": "AAA"},
                            cache_path=Path(td) / "c.json",
                            use_ibkr=False)
        yf_.assert_called_once()


class TestNoPandasAtImport(unittest.TestCase):
    def test_core_install_path(self):
        # A core-only install has neither pandas nor yfinance: importing
        # and running the BoC path must not need them.
        code = ("import sys; sys.modules['pandas'] = None; "
                "sys.modules['yfinance'] = None; "
                "from taxjson.bin import to_base_curr as T; "
                "assert T.yf is None; "
                "rows, e, n = T.build_rates('USD', 'CAD', '2025-01-02', "
                "'2025-01-03', today='2025-01-10', "
                "fetch_boc_fn=lambda c, a, b: {'2025-01-02': '1.44'}); "
                "print(rows[0])")
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                               capture_output=True, text=True,
                               env={**os.environ, "HOME": home})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("('2025-01-02', '1.44', 'boc')", r.stdout)
        # Without yfinance the BoC path still works; a non-CAD target
        # refuses cleanly instead of a ModuleNotFoundError traceback.
        err = io.StringIO()
        with mock.patch.object(T, "yf", None), redirect_stderr(err):
            rc = T.main(["CAD", "USD"])
        self.assertEqual(rc, 1)
        self.assertIn("[fx] extra", err.getvalue())
        self.assertIn("pip install -e '.[fx]'", err.getvalue())


def _write(path, rows):
    path.write_text(json.dumps({"transactions": rows}), encoding="utf-8")


def _tx(date, **kw):
    t = dict(action="BUYSELL", date=date, date_settle=date, time="10:00:00",
             symbol="AAA.US", quantity=1.0, currency="USD", price=100.0,
             net_amount=100.0, account="A")
    t.update(kw)
    return t


def _py(module, *args):
    return subprocess.run([sys.executable, "-m", module, *args],
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          env=os.environ.copy())


class TestDefaultRateIsValidationError(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        d = Path(self.td.name)
        self.rates = d / "to_base.csv"
        self.rates.write_text(
            "2020-01-02 12:00:00 USD CAD 1.2988 boc\n"
            "2020-01-03 12:00:00 USD CAD 1.2990 boc\n"
            "2016-06-01 12:00:00 USD CAD 1.2900 yahoo\n")
        self.inp = d / "in.json"
        _write(self.inp, [_tx("2020-01-02"), _tx("2020-01-03", quantity=2),
                          _tx("2016-06-01", quantity=3),
                          _tx("2012-05-01", quantity=4)])      # no rate

    def test_merge2_counts_the_fallback_as_error(self):
        r = _py("taxjson.bin.taxjson_merge2", "--to", "CAD", "--rates",
                str(self.rates), "--validate", str(self.inp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("validation: 1 error(s)", r.stderr)
        self.assertIn("converted at the default rate", r.stderr)
        self.assertIn("note: FX: Bank of Canada Valet for 2 dates, "
                      "Yahoo fallback for 1", r.stderr)

    def test_merge2_strict_refuses(self):
        r = _py("taxjson.bin.taxjson_merge2", "--to", "CAD", "--rates",
                str(self.rates), "--validate-strict", str(self.inp))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_explicit_default_rate_accepts_it(self):
        r = _py("taxjson.bin.taxjson_merge2", "--to", "CAD", "--rates",
                str(self.rates), "--default-rate", "1.35", "--validate",
                str(self.inp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("validation: ", r.stderr)

    def test_standalone_convert_then_validate_fails(self):
        # The crypto path: convert-currency, then taxjson-validate.
        r = _py("taxjson.bin.taxjson_convert_currency", str(self.inp),
                "--to", "CAD", "--rates", str(self.rates))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("validation: 1 error(s)", r.stderr)
        out = Path(self.td.name) / "base.json"
        out.write_text(r.stdout)
        meta = json.loads(r.stdout)["metadata"]
        self.assertEqual(meta["fx_default_rate_rows"][0]["date"],
                         "2012-05-01")
        v = _py("taxjson.bin.taxjson_validate", str(out))
        self.assertEqual(v.returncode, 1)
        self.assertIn("default rate", v.stdout)


if __name__ == "__main__":
    unittest.main()
