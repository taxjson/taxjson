"""Fix round (futures area): futures booked on their settlement P/L, T1135
futures cost and phantoms, and a transient Bank of Canada 404 that must
not switch a currency to Yahoo for good.

Findings: R1-0 / R1-52 / R1-204 (futures notional FX), S008-04 (T1135
futures notional), R1-321 (T1135 ignores phantoms.json), R1-145 (sticky
Valet 404). Every fetcher is stubbed — no network."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from taxjson.bin import to_base_curr as T

TODAY = "2026-09-28"                                   # a Monday


def _weekdays(a, b):
    from datetime import date
    out, d = [], a
    while d <= b:
        if date.fromisoformat(d).weekday() < 5:
            out.append(d)
        d = T._shift(d, 1)
    return out


# ------------------------------------------------------------ R1-145

class _FxCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        p = mock.patch.object(T, "CACHE_FILE",
                              str(Path(self.td.name) / "fx.json"))
        p.start()
        self.addCleanup(p.stop)
        self.boc_calls = []
        self.boc_fail = None

    def boc(self, cur, a, b):
        self.boc_calls.append((cur, a, b))
        if self.boc_fail:
            raise self.boc_fail
        return {d: "1.3500" for d in _weekdays(a, b) if d < self.today}

    def yahoo(self, ticker, a, b):
        return {d: 1.2 for d in _weekdays(a, b) if d < self.today}

    today = TODAY

    def build(self, start="2025-01-01", end="2025-01-10", offline=False):
        return T.build_rates("USD", "CAD", start, end, today=self.today,
                             offline=offline, fetch_boc_fn=self.boc,
                             fetch_yahoo_fn=self.yahoo)


def _http_error(code, body, ctype="text/html"):
    return urllib.error.HTTPError(
        "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json",
        code, "Not Found", {"Content-Type": ctype}, io.BytesIO(body))


class TestValet404IsNotSticky(_FxCase):
    def test_html_404_is_a_failed_fetch_not_series_not_found(self):
        err = _http_error(404, b"<html><body>Maintenance</body></html>")
        with mock.patch.object(T.urllib.request, "urlopen",
                               side_effect=err):
            with self.assertRaises(Exception) as cm:
                T.fetch_boc("USD", "2025-01-01", "2025-01-10")
        self.assertNotIsInstance(cm.exception, T.SeriesNotFound)

    def test_valet_json_not_found_is_definitive(self):
        body = json.dumps({"message": "Series FXUSDCAD not found.",
                           "docs": "https://www.bankofcanada.ca/valet"
                           }).encode()
        err = _http_error(404, body, "application/json")
        with mock.patch.object(T.urllib.request, "urlopen",
                               side_effect=err):
            with self.assertRaises(T.SeriesNotFound):
                T.fetch_boc("USD", "2025-01-01", "2025-01-10")

    def test_transient_failure_never_switches_to_yahoo(self):
        # Run 1 healthy, run 2 one transient failure, run 3 healthy.
        rows, _, _ = self.build()
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))
        self.boc_fail = OSError("HTTP Error 404: Not Found (proxy)")
        rows, errors, _ = self.build(end="2025-01-20")
        self.assertTrue(errors)
        self.assertTrue(all(s == "boc" for _d, _v, s in rows))
        self.boc_fail = None
        rows, errors, _ = self.build(end="2025-01-20")
        self.assertEqual(errors, [])
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))
        blk = json.loads(Path(T.CACHE_FILE).read_text())["_boc"]["USDCAD"]
        self.assertNotIn("not_published", blk)

    def test_definitive_not_found_is_rechecked_after_a_while(self):
        self.boc_fail = T.SeriesNotFound("FXUSDCAD")
        self.build()
        blk = json.loads(Path(T.CACHE_FILE).read_text())["_boc"]["USDCAD"]
        self.assertTrue(blk.get("not_published"))
        self.assertEqual(blk.get("not_published_checked"), TODAY)
        # Same day: honoured, not re-asked.
        self.boc_calls.clear()
        self.build()
        self.assertEqual(self.boc_calls, [])
        # A week later the series is back: re-probed, marker cleared,
        # the Bank's rates are used again.
        self.boc_fail = None
        self.today = T._shift(TODAY, T.NOT_PUBLISHED_RECHECK_DAYS)
        rows, _, notes = self.build()
        self.assertTrue(self.boc_calls)
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))
        blk = json.loads(Path(T.CACHE_FILE).read_text())["_boc"]["USDCAD"]
        self.assertNotIn("not_published", blk)

    def test_legacy_undated_marker_is_reprobed(self):
        # A cache written by the old code: a transient 404 marked the
        # series unpublished forever, with 319 good observations kept.
        self.build()                               # healthy → obs cached
        cache = json.loads(Path(T.CACHE_FILE).read_text())
        cache["_boc"]["USDCAD"]["not_published"] = True
        Path(T.CACHE_FILE).write_text(json.dumps(cache))
        self.boc_calls.clear()
        rows, _, _ = self.build(end="2025-01-20")
        self.assertTrue(self.boc_calls)
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))

    def test_cached_bank_rates_keep_their_source_under_a_marker(self):
        self.build()                               # obs for 2025-01-01..10
        cache = json.loads(Path(T.CACHE_FILE).read_text())
        cache["_boc"]["USDCAD"]["not_published"] = True
        cache["_boc"]["USDCAD"]["not_published_checked"] = TODAY
        Path(T.CACHE_FILE).write_text(json.dumps(cache))
        rows, _, notes = self.build(end="2025-01-20", offline=True)
        src = {d: s for d, _v, s in rows}
        self.assertEqual(src["2025-01-06"], "boc")
        self.assertTrue(any("FXUSDCAD" in n and "not found" in n
                            for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
