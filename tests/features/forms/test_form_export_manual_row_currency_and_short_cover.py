"""Backlog fix round 2 of 2026-10-29, group `review`: the review and
filing-output commands (audit --year, form-export, the wash review, the
stale-book warning, check-tt, the checklist's run-clean, sanity's
pointer, the inputs/ permission warning).

TJ-0550  audit --year refuses a lock closed under the other country
         before applying its date basis or option timing.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*, SYN*), account names (margin, tfsa), amounts
under 1,000, broker account numbers like 99900001.

Formerly: test_fix_1029_r2_review
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.engine_short_cover import _short_cover_gains


def _form_main(argv):
    """form-export's main in-process: (rc, stdout, stderr)."""
    import contextlib
    import io
    from taxjson.bin import taxjson_form_export as FE
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = FE.main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _manual_long(currency, proceeds=100.0):
    return {"date": "2025-06-02", "date_settle": "2025-06-02",
            "symbol": "SYN.US", "qty": 10, "proceeds": proceeds,
            "currency": currency, "direction": "LONG", "account": "margin"}


class TestFormExportManualRowCurrency(unittest.TestCase):
    """TJ-0496: a gains file with no computed disposition and one
    unknown-cost (manual reporting) row in USD exported a CAD Schedule
    3 at exit 0, its manual proceeds 100 labelled CAD. The manual rows
    are held to the return's currency as the computed ones are.

    Formerly: test_fix_1029_r2_review.TestFormExportManualRowCurrency"""

    def _doc(self, d, rows, txs=()):
        p = Path(d) / "g.json"
        p.write_text(json.dumps({"summary": {"year": 2025},
                                 "transactions": list(txs),
                                 "manual_reporting_required": rows}))
        return p

    def test_a_usd_manual_row_is_refused_in_a_cad_return(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d, [_manual_long("USD")])
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            self.assertEqual(rc, 2, out)
            self.assertIn("another currency than CAD", err)
            self.assertIn("1 USD", err)
            self.assertEqual(out, "")

    def test_a_usd_manual_row_is_refused_in_form_8949_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d, [dict(_manual_long("CAD"), term="SHORT_TERM")])
            rc, out, err = _form_main([str(p), "--form", "8949",
                                       "--country", "usa", "--year",
                                       "2025"])
            self.assertEqual(rc, 2, out)
            self.assertIn("another currency than USD", err)

    def test_a_manual_row_in_the_returns_currency_is_listed(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d, [_manual_long("CAD")])
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["manual_proceeds"], 100.0)

    def test_a_manual_row_with_no_currency_is_said(self):
        with tempfile.TemporaryDirectory() as d:
            row = _manual_long("CAD")
            del row["currency"]
            p = self._doc(d, [row])
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            self.assertEqual(rc, 0, err)
            self.assertIn("carry no currency", err)


class TestFormExportManualShortCover(unittest.TestCase):
    """TJ-0497: an unknown-cost short cover (the short sale before the
    data, its proceeds unknown; covered for 500) was listed for manual
    reporting as proceeds 500 — the cover cost — in the warning, the
    MANUAL REPORTING table, the CSV and `manual_proceeds`. Its cover
    cost is now named as such, and its proceeds as unknown.

    Formerly: test_fix_1029_r2_review.TestFormExportManualShortCover"""

    def _export(self, d, country, *extra):
        g = _short_cover_gains(country)
        self.assertEqual(
            [m["direction"] for m in g["manual_reporting_required"]],
            ["SHORT"])
        p = Path(d) / "g.json"
        p.write_text(json.dumps(g))
        form = "schedule3" if country == "canada" else "8949"
        return _form_main([str(p), "--form", form, "--country", country,
                           "--year", "2025", *extra])

    def test_json_separates_the_cover_cost_from_proceeds(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                rc, out, err = self._export(d, country, "--json")
                self.assertEqual(rc, 0, err)
                j = json.loads(out)
                self.assertEqual(j["manual_proceeds"], 0.0)
                self.assertEqual(j["manual_cover_cost"], 500.0)
                m = j["manual_reporting_required"][0]
                self.assertEqual(m["cover_cost"], 500.0)
                self.assertIsNone(m["short_sale_proceeds"])
                self.assertNotIn("proceeds 500.00", err)
                self.assertIn("cover cost 500.00", err)

    def test_the_text_table_says_unknown_proceeds(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                rc, out, err = self._export(d, country)
                self.assertEqual(rc, 0, err)
                sec = out[out.index("MANUAL REPORTING REQUIRED"):]
                self.assertIn("COVER COST", sec)
                row = next(ln for ln in sec.splitlines()
                           if ln.startswith("SYN.US"))
                self.assertIn("unknown", row)
                self.assertIn("500.00", row)
                self.assertIn("short", sec.splitlines()[0])

    def test_the_csv_writes_the_cover_cost_as_cost(self):
        import csv
        for country, pcol, ccol in (("canada", 7, 8), ("usa", 4, 5)):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                c = Path(d) / "out.csv"
                rc, out, err = self._export(d, country, "--csv", str(c))
                self.assertEqual(rc, 0, err)
                rows = [r for r in csv.reader(c.open())
                        if r and r[0] == "MANUAL"]
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0][pcol], "")
                self.assertEqual(float(rows[0][ccol]), 500.0)
                self.assertIn("short", ",".join(rows[0]))

    def test_a_long_row_keeps_its_proceeds(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.json"
            p.write_text(json.dumps({
                "summary": {"year": 2025}, "transactions": [],
                "manual_reporting_required": [_manual_long("CAD")]}))
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            j = json.loads(out)
            self.assertEqual(j["manual_proceeds"], 100.0)
            self.assertEqual(j["manual_cover_cost"], 0.0)
            self.assertNotIn("cover_cost",
                             j["manual_reporting_required"][0])
            self.assertIn("proceeds 100.00", err)


if __name__ == "__main__":
    unittest.main()
