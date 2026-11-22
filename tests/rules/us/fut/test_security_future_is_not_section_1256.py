"""Regression tests for fix round 7 (wave B), group coreus: the engines'
income and US paths.

TJ-0647 a US stock dividend's entitled sale before a split was counted
        in pre-split shares: the dividend ratio came out wrong.
TJ-0648 an entitled sale under the ticker a rename replaced was not
        found: the held shares took the whole dividend.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7b_coreus
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _gains(book, sheltered=(), year=2025, country="usa", **req):
    """pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


# ---------------------------------------------------------------- TJ-0730
class TestSecurityFutureIsNotSection1256(unittest.TestCase):
    """A single-stock future (root ZQF) opened 2026-08-03 at 100 and
    closed 2026-08-10 at 101 (x100): with `SECFUT ZQF` in the ticker.map
    it is no §1256 contract: a short-term gain of 100 on Form 8949, out
    of the Form 6781 list and inside the wash-sale rule. Without the
    line every future stays §1256.

    Formerly: test_fix_1029_r7b_coreus.TestSecurityFutureIsNotSection1256"""

    def _with_map(self, text):
        import tempfile
        from pathlib import Path
        from taxjson.lib import markets
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tm = Path(td.name) / "ticker.map"
        tm.write_text(text)
        markets.use_ticker_map(tm)
        self.addCleanup(markets.use_ticker_map, None)

    def _form(self, legs):
        from taxjson.lib.tax_forms import build_8949
        from taxjson.lib.futures import settle_futures
        book = [tx("BUYSELL", d, "F:ZQFZ6.US", q, p * 100, price=p,
                   multiplier=100)
                for d, (q, p) in zip(("2026-08-03", "2026-08-10"), legs)]
        book = settle_futures(book, "fifo")[0]
        res = _gains(book, year=2026)
        ents = [t for t in res["transactions"] if t.get("qty")]
        return ents, build_8949(ents, 2026)

    @rule(R.US_FUT_02)
    def test_declared_security_future_goes_on_form_8949(self):
        from taxjson.engine.us.terms import outside_1091
        from taxjson.lib.futures import section_1256_kind
        self._with_map("SECFUT F:ZQF\n")
        self.assertEqual(section_1256_kind("F:ZQFZ6.US"), "")
        self.assertFalse(outside_1091("F:ZQFZ6.US"))
        self.assertEqual(section_1256_kind("F:ZQGZ6.US"), "future")
        ents, f = self._form([(1, 100), (-1, 101)])
        self.assertEqual(ents[0]["term"], "SHORT_TERM")
        self.assertEqual(f["section_1256"], [])
        row = f["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (100.0, 0.0, 100.0))
        _ents, f = self._form([(1, 100), (-1, 99)])
        row = f["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (0.0, 100.0, -100.0))
        _ents, f = self._form([(-1, 100), (1, 99)])
        row = f["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (100.0, 0.0, 100.0))

    @rule(R.US_FUT_02)
    def test_without_the_line_every_future_is_section_1256(self):
        from taxjson.engine.us.terms import outside_1091
        self._with_map("")
        self.assertTrue(outside_1091("F:ZQFZ6.US"))
        _ents, f = self._form([(1, 100), (-1, 101)])
        self.assertEqual(f["part_I"], [])
        self.assertEqual([r["kind"] for r in f["section_1256"]],
                         ["future"])

    def test_ticker_map_line(self):
        from taxjson.lib.ticker_map import parse_side_line
        self.assertEqual(parse_side_line("SECFUT", "SECFUT /zqf"),
                         ("secfut", "ZQF", True))
        self.assertEqual(parse_side_line("SECFUT", "SECFUT ZQF NO"),
                         ("secfut", "ZQF", False))
        with self.assertRaises(ValueError):
            parse_side_line("SECFUT", "SECFUT ZQF MAYBE")


if __name__ == "__main__":
    unittest.main()
