"""Leftovers of the v0.24.0 third pre-release review:

- 1: a `DISTINCT` line written with the bare US ticker (`DISTINCT QZX
  QZX.TO`) answers the `.US` pair (the books spell a bare US ticker
  QZX.US) and says how it was read; a GLOBAL / TOBASE line so written is
  not re-read (it would move pools) but warned, naming the line to write.

Every fixture is SYNTHETIC: invented QZ*/ZZX tickers and names, fake
account ids (pii-ok: 55500001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from _qa_project import console, tj
from test_fix_qa_f3_xlist_loss import f3_project, zzx_loss


def _flat(text):
    return " ".join(text.split())


# ------------------------------------------------------------------ 1

class TestDistinctBareUsTicker(unittest.TestCase):

    def test_parse(self):
        from taxjson.bin.taxjson_ticker_map import (_parse_map_text,
                                                    canonical_distinct)
        tm, problems, _notes = _parse_map_text(
            "DISTINCT ZZX ZZX.TO\nDISTINCT QZB.B.TO QZB.B\n"
            "DISTINCT QZC QZD\nDISTINCT ZZY.US ZZY\n")
        self.assertEqual(problems, [])
        self.assertIn(frozenset(("ZZX.US", "ZZX.TO")), tm.distinct)
        self.assertIn(frozenset(("QZB.B.TO", "QZB.B.US")), tm.distinct)
        # Two bare symbols are two coins; a bare symbol whose US
        # spelling is the other side is left as written.
        self.assertIn(frozenset(("QZC", "QZD")), tm.distinct)
        self.assertIn(frozenset(("ZZY.US", "ZZY")), tm.distinct)
        # An option contract is no bare ticker.
        self.assertEqual(canonical_distinct("ZZX250117C00010000",
                                            "ZZX.TO"),
                         ("ZZX250117C00010000", "ZZX.TO"))

    def _distinct(self, country, kind):
        with tempfile.TemporaryDirectory() as td:
            root = f3_project(td, "p", country=country,
                              ticker_map="DISTINCT ZZX ZZX.TO\n")
            r = tj(root, "run", "--no-input", "--strict")
            text = _flat(console(r))
            self.assertNotIn(f"possible {kind} across listings", text)
            self.assertIn("ticker.map:1: `DISTINCT ZZX ZZX.TO` is read as "
                          "`DISTINCT ZZX.US ZZX.TO`", text)
            loss, = zzx_loss(root)
            self.assertLess(loss["gain"], -100.0)     # still allowed
            self.assertEqual(tj(root, "scan", check=False).stdout.count(
                "XLIST-LOSS"), 0)
            doc = json.loads(tj(root, "ticker-map", "--suggest",
                                "--json").stdout)
            lines = [s["line"] for s in doc.get("suggestions")
                     or doc.get("offer") or []]
            self.assertFalse([x for x in lines if "ZZX" in x], doc)

    @rule("CA-XLIST-05")
    def test_canada_distinct_with_the_bare_us_ticker(self):
        self._distinct("canada", "superficial loss")

    @rule("US-XLIST-04")
    def test_usa_distinct_with_the_bare_us_ticker(self):
        self._distinct("usa", "wash sale")

    def _tobase(self, country, kind):
        with tempfile.TemporaryDirectory() as td:
            root = f3_project(td, "p", country=country,
                              ticker_map="TOBASE ZZX ZZX.TO\n")
            r = tj(root, "run", "--no-input")
            text = _flat(console(r))
            self.assertIn("Warning: ticker.map:1: `TOBASE ZZX ZZX.TO` "
                          "names ZZX without a market suffix", text)
            self.assertIn("write `TOBASE ZZX.US ZZX.TO`", text)
            # Not re-read: the loss stays allowed, the radar still asks.
            self.assertIn(f"possible {kind} across listings", text)
            loss, = zzx_loss(root)
            self.assertLess(loss["gain"], -100.0)     # still allowed

    @rule("CA-XLIST-05")
    def test_canada_tobase_with_the_bare_us_ticker_is_warned(self):
        self._tobase("canada", "superficial loss")

    @rule("US-XLIST-04")
    def test_usa_tobase_with_the_bare_us_ticker_is_warned(self):
        self._tobase("usa", "wash sale")

    def test_coin_lines_are_quiet(self):
        from taxjson.bin.taxjson_ticker_map import listing_spelling_notes
        self.assertEqual(listing_spelling_notes(
            "GLOBAL QZCOIN QZC\nDISTINCT QZC QZD\nTOBASE ZZX.US ZZX.TO\n"),
            ([], []))


if __name__ == "__main__":
    unittest.main()
