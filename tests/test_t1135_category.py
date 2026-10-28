"""The T1135 category of each property row (tax-logic CA-RPT-18): taken
from the instrument, the interlisted master's units and funds left for
the user, a ticker.map `T1135 SYMBOL COUNTRY CATEGORY` line setting it.

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), amounts under 1,000 a unit.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.bin.taxjson_t1135 import (main, property_category,
                                       render_brief, render_report)
from taxjson.lib import tobase_map as TB
from taxjson.lib.ticker_map import side_rules_from_text
from test_fix_a2_pins2_t1135 import _r, _report
from test_tobase_map import _master, _project

FU = "BBG000000U01"
UNITS = {FU: {"name": "QZ UNITS LP", "kind": "unit",
              "share_class_figi": FU, "domicile": "BM",
              "ca": ["QZU.UN.TO"], "us": ["QZU.US"],
              "us_exchange": ["NYSE"]}}
CALL = "QZA250620C00050000.US"


def _buys(*symbols):
    return [_r("BUYSELL", "2025-02-03", s, 10, -500.0) for s in symbols]


class TestTakenFromTheInstrument(unittest.TestCase):

    @rule("CA-RPT-18")
    def test_share_option_future_crypto_units(self):
        self.assertEqual(property_category("QZA.US", {}, {}), (2, "listing"))
        self.assertEqual(property_category(CALL, {}, {}), (6, "derivative"))
        self.assertEqual(property_category("F:QZZ5.US", {}, {}),
                         (6, "derivative"))
        self.assertEqual(property_category("QZC", {}, {}), (6, "crypto"))
        self.assertEqual(property_category("QZU.US", {}, {"QZU.US": "unit"}),
                         (None, "unit"))
        # A ticker.map line wins over every default.
        self.assertEqual(property_category("QZU.US", {"QZU.US": 6},
                                           {"QZU.US": "unit"}),
                         (6, "ticker.map"))

    @rule("CA-RPT-18")
    def test_every_row_carries_its_category(self):
        rep = _report(_buys("QZA.US", "QZC") + [
            _r("BUYSELL", "2025-02-03", CALL, 1, -300.0)])
        got = {p["symbol"]: (p["category"], p["category_from"])
               for p in rep["properties"]}
        self.assertEqual(got, {"QZA.US": (2, "listing"),
                               "QZC": (6, "crypto"),
                               CALL: (6, "derivative")})
        self.assertEqual(rep["category_review_symbols"], [])
        text = render_brief(rep)
        self.assertIn("CAT: the form's category", text)
        self.assertRegex(text, r"QZA\.US +USA +2 ")


class TestTickerMapLine(unittest.TestCase):

    @rule("CA-RPT-18")
    def test_an_optional_category_after_the_country(self):
        side = side_rules_from_text("T1135 QZE.US USA 4\nT1135 QZW.US CA\n")
        self.assertEqual(side.problems, [])
        self.assertEqual(side.t1135, {"QZE.US": "USA", "QZW.US": "CA"})
        self.assertEqual(side.t1135_category, {"QZE.US": 4})

    @rule("CA-RPT-18")
    def test_a_category_the_form_has_not_stops_t1135(self):
        for text, words in (
                ("T1135 QZE.US USA 7\n", "categories 1 to 6"),
                ("T1135 QZE.US USA four\n", "categories 1 to 6"),
                ("T1135 QZE.US CA 2\n", "goes with a foreign country"),
                ("T1135 QZE.US USA 4 2\n", "[CATEGORY]"),
                ("T1135 QZE.US USA 4\nT1135 QZE.US USA 2\n",
                 "given twice")):
            with self.subTest(text=text):
                side = side_rules_from_text(text)
                self.assertEqual(len(side.t1135_problems), 1)
                self.assertIn(words, side.t1135_problems[0])

    @rule("CA-RPT-18")
    def test_the_category_follows_a_rename_and_a_booked_listing(self):
        rows = _buys("QZE.US") + [
            _r("SPLIT", "2025-05-01", "QZE.US", 1.0, 0.0,
               symbol_new="QZF.US")]
        rep = _report(rows, categories={"QZE.US": 4})
        self.assertEqual({p["symbol"]: p["category"]
                          for p in rep["properties"]}, {"QZF.US": 4})
        # A line naming a listing that a TOBASE line books as another.
        rep = _report(_buys("QZE.US"), categories={"QZX.US": 4},
                      key_renames={"QZX.US": "QZE.US"})
        self.assertEqual(rep["properties"][0]["category"], 4)


class TestMasterUnits(unittest.TestCase):

    def _run(self, ticker=""):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker=ticker, tobase=(
                f"TOBASE QZU.UN.TO QZU.US  # master:{FU}:"
                f"{TB.line_hash('TOBASE QZU.UN.TO QZU.US')} country=BMU\n"))
            base = root / "margin_base.json"
            base.write_text(json.dumps({"transactions": _buys("QZU.US")}))
            out = io.StringIO()
            with mock.patch.object(TB, "load_master",
                                   return_value=_master(UNITS)), \
                    contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = main([str(base), "--year", "2025", "--map",
                           str(root / "ticker.map"), "--year-wash-only",
                           "--json"])
            self.assertEqual(rc, 0)
            return json.loads(out.getvalue())

    @rule("CA-RPT-18")
    def test_units_are_named_for_the_user_to_set(self):
        rep = self._run()
        row = rep["properties"][0]
        self.assertEqual((row["symbol"], row["country"], row["category"],
                          row["category_from"]),
                         ("QZU.US", "BMU", None, "unit"))
        self.assertEqual(rep["category_review_symbols"], ["QZU.US"])
        self.assertIn("! QZU.US: units, not shares: set the T1135 category",
                      render_brief(rep))
        self.assertIn("QZU.US: units in the interlisted master",
                      " ".join(render_report(rep).split()))

    @rule("CA-RPT-18")
    def test_a_ticker_map_line_sets_their_category(self):
        rep = self._run(ticker="T1135 QZU.US BMU 6\n")
        row = rep["properties"][0]
        self.assertEqual((row["country"], row["category"],
                          row["category_from"]), ("BMU", 6, "ticker.map"))
        self.assertEqual(rep["category_review_symbols"], [])


if __name__ == "__main__":
    unittest.main()
