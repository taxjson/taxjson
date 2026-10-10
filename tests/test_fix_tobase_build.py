"""scripts/build_interlisted.py: the interlisted master's corrections
(retracted listings, reused ended tickers, FINRA's temporary OTC symbols)
and the TSX funds' US-dollar lines.

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented names.
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import tobase_map as TB
from test_tobase_map import TestBuild, _eq, _xlsx


class TestReusedTicker(unittest.TestCase):

    def _cache(self, td, us_rows):
        B, cache = TestBuild._cache(self, td)
        figi = json.loads((cache / B.FIGI_CACHE).read_text())
        figi[B.Figi.key(B.ticker_job("QZE", "US"))] = us_rows
        (cache / B.FIGI_CACHE).write_text(json.dumps(figi))
        return B, cache

    def test_a_reused_ended_ticker_refuses_the_build_unless_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            # QZE on the US side names another company's ADR today.
            B, cache = self._cache(td, [_eq("QZE", "US", "BBG000000X09",
                                            typ="ADR", name="QZ OTHER")])
            hist = Path(td) / "h.toml"
            hist.write_text('[[ended]]\nname = "QZ ENDED"\nshare_class_figi'
                            ' = "BBG000000E01"\nca = ["QZE.TO"]\n'
                            'us = ["QZE.US"]\nuntil = "2025-06-30"\n')
            out = Path(td) / "m.toml"
            args = ["--cache", str(cache), "--out", str(out), "--history",
                    str(hist), "--date", "2026-01-02"]
            self.assertEqual(B.main(args), 3)
            self.assertFalse(out.exists())
            hist.write_text(hist.read_text() + 'reused_by = "QZ OTHER"\n')
            self.assertEqual(B.main(args), 0)
            from taxjson.lib.tomlcompat import tomllib
            e = tomllib.loads(out.read_text())["security"]["BBG000000E01"]
            self.assertEqual(e["history"][0]["reused_by"], "QZ OTHER")

    def test_the_same_class_today_is_no_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = self._cache(td, [_eq("QZE", "US", "BBG000000E01")])
            hist = [{"name": "QZ ENDED", "share_class_figi": "BBG000000E01",
                     "ca": ["QZE.TO"], "us": ["QZE.US"]}]
            doc, rep = B.build(cache, None, "2026-01-02",
                               B.Figi(cache / B.FIGI_CACHE), hist)
            self.assertEqual(rep["reused_tickers"], [])



class TestRetracted(unittest.TestCase):

    def test_build_never_re_adds_a_retracted_listing(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = TestBuild._cache(self, td)
            fig = B.Figi(cache / B.FIGI_CACHE)
            prev, _ = B.build(cache, None, "2026-01-02", fig, [])
            self.assertIn("QZAAF.US", prev["security"]["BBG000000A01"]
                          ["us_otc"])
            corr = [{"share_class_figi": "BBG000000A01", "_correction": True,
                     "retracted": [{"listing": "QZAAF.US",
                                    "reason": "a test"}]}]
            doc, _ = B.build(cache, prev, "2026-02-01", fig, corr)
            a = doc["security"]["BBG000000A01"]
            self.assertNotIn("QZAAF.US", a.get("us_otc", []))
            self.assertNotIn("QZAAF.US", [h["listing"] for h in
                                          a.get("history", [])])
            self.assertEqual(a["retracted"][0]["listing"], "QZAAF.US")
            # A later build without the correction keeps it out.
            doc2, _ = B.build(cache, doc, "2026-03-01", fig, [])
            self.assertNotIn("QZAAF.US", doc2["security"]["BBG000000A01"]
                             .get("us_otc", []))
            m = TB.Master({"generated": "x"}, doc2["security"], {})
            self.assertNotIn("TOBASE QZAAF.US QZA.TO",
                             [g.rule for g in TB.master_lines(m)])

    def test_temporary_otc_symbol_is_left_out(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = TestBuild._cache(self, td)
            K = B.Figi.key
            figi = json.loads((cache / B.FIGI_CACHE).read_text())
            figi[K(B.class_job("BBG000000V01", "US"))] = [
                _eq("QZVVD", "US", "BBG000000V01")]
            figi[K(B.ticker_job("QZVVF", "US"))] = []
            (cache / B.FIGI_CACHE).write_text(json.dumps(figi))
            prev = {"security": {"BBG000000V01": {
                "name": "QZ", "kind": "share",
                "share_class_figi": "BBG000000V01", "ca": ["QZV.TO"],
                "us": [], "us_exchange": [], "us_otc": ["QZVVD.US"],
                "first_seen": "2025-01-01"}}, "distinct": {}}
            doc, rep = B.build(cache, prev, "2026-01-02",
                               B.Figi(cache / B.FIGI_CACHE), [])
            v = doc["security"]["BBG000000V01"]
            self.assertNotIn("us_otc", v)
            self.assertNotIn("until", v)
            self.assertEqual(v["retracted"][0]["listing"], "QZVVD.US")
            self.assertEqual(rep["otc_temporary_symbols"][0]["listing"],
                             "QZVVD.US")
            # The permanent symbol, when OpenFIGI gives it the same class.
            figi[K(B.ticker_job("QZVVF", "US"))] = [
                _eq("QZVVF", "US", "BBG000000V01")]
            (cache / B.FIGI_CACHE).write_text(json.dumps(figi))
            doc, _ = B.build(cache, None, "2026-01-02",
                             B.Figi(cache / B.FIGI_CACHE), [])
            self.assertEqual(doc["security"]["BBG000000V01"]["us_otc"],
                             ["QZVVF.US"])




class TestEtfCurrencyLines(unittest.TestCase):

    def test_usd_line_of_the_same_class_only(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = TestBuild._cache(self, td)
            hdr = ["Co_ID", "Root Ticker", "Name", "SP_Type"]
            _xlsx(cache / f"{B.TMX_XLSX_PREFIX}2026-01-01.xlsx", {
                "TSX Issuers": [hdr, ["1", "QZA", "QZ Alpha", ""],
                                ["7", "QZF", "QZ Fund", "Exchange Traded "
                                 "Funds"],
                                ["8", "QZH", "QZ Hedged", "Exchange Traded "
                                 "Funds"]],
                "TSXV Issuers": [hdr]})
            K = B.Figi.key
            figi = json.loads((cache / B.FIGI_CACHE).read_text())
            figi.update({
                K(B.ticker_job("QZF", "CN")): [_eq("QZF", "CN",
                                                   "BBG000000F01", "ETP")],
                K(B.ticker_job("QZF/U", "CN")): [_eq("QZF/U", "CN",
                                                     "BBG000000F01", "ETP")],
                K(B.ticker_job("QZH", "CN")): [_eq("QZH", "CN",
                                                   "BBG000000H01", "ETP")],
                K(B.ticker_job("QZH/U", "CN")): [_eq("QZH/U", "CN",
                                                     "BBG000000H02", "ETP")]})
            (cache / B.FIGI_CACHE).write_text(json.dumps(figi))
            doc, rep = B.build(cache, None, "2026-01-02",
                               B.Figi(cache / B.FIGI_CACHE), [])
            f = doc["security"]["BBG000000F01"]
            self.assertEqual((f["kind"], f["ca"], f["us"]),
                             ("fund", ["QZF.TO", "QZF.U.TO"], []))
            self.assertNotIn("BBG000000H01", doc["security"])
            self.assertEqual(rep["etf_usd_lines_other_class"], ["QZH.U"])


if __name__ == "__main__":
    unittest.main()
