"""`tjs plot-tax`: the tax each month's investment income generated,
as a chart against the year's estimate (CA-PLAN-08 / US-PLAN-09).
Planning numbers only. Synthetic books and projects only."""
import json
import tempfile
import unittest

from tax_rules import rule

from taxjson.lib import tax_plot as TP
from _qa_project import project as qa_project, tj
from _style import assert_legend_first, assert_styled
from _style import project as style_project

_W = {"TAXJSON_WIDTH": "100"}


def _points(taxes, incomes=None):
    out, ct, ci = [], 0.0, 0.0
    for i, t in enumerate(taxes):
        ct += t
        ci += (incomes[i] if incomes else t * 4)
        out.append({"month": f"2024-{i + 1:02d}", "cum_tax": ct,
                    "cum_income": ci})
    return out


class TestBuild(unittest.TestCase):
    def test_months_are_the_differences_of_the_runs(self):
        taxes = [100, -40, 220, 0, 600, 300, -50, 0, 250, 0, 500, 400]
        doc = TP.build(_points(taxes), year=2024, base="CAD",
                       year_estimate=2280.0, year_income=9120.0,
                       label="canada/ON", accounts=["margin"])
        self.assertEqual([r["tax"] for r in doc["rows"]], taxes)
        self.assertEqual(doc["rows"][1]["cum_tax"], 60.0)
        self.assertEqual(doc["rows"][1]["income"], -160.0)
        self.assertEqual(doc["months_total"], 2280.0)
        self.assertEqual(doc["year_adjustment"], 0.0)
        # The year's own run differs by what is dated to the year.
        doc = TP.build(_points(taxes), year=2024, base="CAD",
                       year_estimate=2300.0, year_income=9120.0,
                       label="canada/ON", accounts=["margin"])
        self.assertEqual(doc["year_adjustment"], 20.0)

    def test_helpers(self):
        self.assertEqual(TP.month_end("2024-02"), "2024-02-29")
        self.assertEqual(TP.months_of_year(2025)[11], "2025-12")
        self.assertEqual(TP._nice_step(2280.0, 8), 500.0)
        self.assertEqual(TP._nice_step(70.0, 8), 10.0)
        self.assertEqual(TP._nice_step(0.0, 8), 1.0)
        self.assertEqual((TP._level(50, 100), TP._level(-50, 100),
                          TP._level(49, 100)), (1, -1, 0))
        doc = {"transactions": [{"date": "2024-01-05"},
                                {"date": "2024-03-31"},
                                {"date": "2024-04-01"}], "summary": {}}
        sl = TP.slice_doc(doc, lambda t: t["date"], "2024-03-31")
        self.assertEqual(len(sl["transactions"]), 2)
        self.assertEqual(sl["summary"], {})


class TestChart(unittest.TestCase):
    def test_bars_level_and_negative_month(self):
        taxes = [100, -40, 220, 0, 600, 300, -50, 0, 250, 0, 500, 400]
        rows = TP.build(_points(taxes), year=2024, base="CAD",
                        year_estimate=2280.0, year_income=0.0,
                        label="x", accounts=[])["rows"]
        lines = TP.chart(rows, 2280.0, 120, "CAD")
        self.assertTrue(lines[0].lstrip().startswith("CAD |  # = the "
                                                     "month's tax   "))
        axis = next(ln for ln in lines if ln.lstrip().startswith("0 +"))
        i = lines.index(axis)
        # The estimate's twelfth (190) is the dashed level, labelled.
        lv = next(ln for ln in lines[1:] if "estimate / 12" in ln)
        self.assertTrue(lv.lstrip().startswith("200 |"), lv)
        # May (600) reaches the 600 row; February (-40) and July (-50)
        # hang below the axis, nothing else does.
        top = next(ln for ln in lines if ln.lstrip().startswith("600 |"))
        self.assertIn("#", top)
        below = lines[i + 1:-1]
        self.assertEqual(len(below), 1)
        self.assertEqual(below[0].count("#" * 4), 1)      # July only
        self.assertEqual(lines[-1].split(), list(TP.MONTH_NAMES))
        # Every line fits a narrow terminal too.
        self.assertTrue(all(len(ln) <= 80 for ln in TP.chart(rows, 2280.0,
                                                               80)))

    def test_flat_year(self):
        rows = TP.build(_points([0] * 12), year=2024, base="CAD",
                        year_estimate=0.0, year_income=0.0, label="x",
                        accounts=[])["rows"]
        lines = TP.chart(rows, 0.0, 120)
        self.assertEqual(sum(ln.count("#") for ln in lines[1:]), 0)
        # A twelfth below half a step sits on the axis.
        axis = next(ln for ln in lines if ln.lstrip().startswith("0 +"))
        self.assertTrue(axis.endswith("estimate / 12"), axis)

    def test_render(self):
        doc = TP.build(_points([100, -40] + [0] * 10), year=2024,
                       base="CAD", year_estimate=60.0, year_income=240.0,
                       label="canada/ON", accounts=["margin"],
                       with_amt=75.0)
        text = TP.render(doc, 100).text()
        self.assertTrue(text.startswith("TAX BY MONTH — planning numbers, "
                                        "tax year 2024 (canada/ON, CAD)"))
        self.assertIn("MONTH", text)
        self.assertRegex(text, r"2024-02\s+-160\.00\s+-40\.00\s+60\.00")
        self.assertIn("Estimated tax for the year:  60.00 CAD on 240.00",
                      text)
        self.assertRegex(text, r"With the minimum tax:\s+75\.00 CAD")
        self.assertNotIn("dated to the year", text)


BOOK_CA = """\
BUYSELL 2024-01-10 10:00:00 ZZA.TO 100 CAD 10.00 1000
BUYSELL 2024-03-15 10:00:00 ZZA.TO -100 CAD 60.00 6000
BUYSELL 2024-04-02 10:00:00 ZZB.TO 100 CAD 50.00 5000
BUYSELL 2024-06-10 10:00:00 ZZB.TO -100 CAD 30.00 3000
"""
BOOK_US = BOOK_CA.replace(".TO", ".US").replace("CAD", "USD")


class TestPlotTaxCli(unittest.TestCase):
    @rule("CA-PLAN-08")
    def test_canada_months_add_up_to_the_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = qa_project(tmp, "p", {"margin/m.tt": BOOK_CA})
            tj(root, "run", "--no-input")
            flags = ["--province", "ON", "--other-income", "90000"]
            doc = json.loads(tj(root, "plot-tax", *flags, "--json").stdout)
            est = json.loads(tj(root, "estimate", *flags,
                                "--json").stdout)["estimate"]
            self.assertEqual(doc["year_estimate"], est["estimated_tax"])
            self.assertEqual(doc["year_income"], est["investment_income"])
            self.assertEqual(doc["months_total"], doc["year_estimate"])
            self.assertEqual(doc["year_adjustment"], 0.0)
            self.assertEqual(doc["label"], "canada/ON")
            self.assertEqual(doc["accounts"], ["margin"])
            rows = {r["month"]: r for r in doc["rows"]}
            self.assertEqual(len(rows), 12)
            # The March gain pays; the June loss gives some back; the
            # other months carry nothing.
            self.assertGreater(rows["2024-03"]["tax"], 0)
            self.assertEqual(rows["2024-03"]["income"], 5000.0)
            self.assertLess(rows["2024-06"]["tax"], 0)
            self.assertEqual(rows["2024-06"]["income"], -2000.0)
            self.assertEqual(rows["2024-02"]["tax"], 0.0)
            self.assertEqual(rows["2024-12"]["cum_tax"],
                             doc["year_estimate"])
            self.assertAlmostEqual(sum(r["tax"] for r in doc["rows"]),
                                   doc["year_estimate"], 1)
            text = tj(root, "plot-tax", *flags).stdout
            self.assertIn("TAX BY MONTH — planning numbers, tax year 2024",
                          text)
            self.assertIn("estimate / 12", text)
            self.assertIn("on top of 90,000.00 other income", text)
            # No province: refused before anything prints.
            bad = tj(root, "plot-tax", check=False)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("needs a province", bad.stderr)

    @rule("US-PLAN-09")
    def test_usa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = qa_project(tmp, "p", {"margin/m.tt": BOOK_US},
                              country="usa")
            tj(root, "run", "--no-input")
            flags = ["--other-income", "90000"]
            doc = json.loads(tj(root, "plot-tax", *flags, "--json").stdout)
            est = json.loads(tj(root, "estimate", *flags,
                                "--json").stdout)["estimate"]
            self.assertEqual(doc["year_estimate"], est["estimated_tax"])
            self.assertEqual(doc["label"], "usa")
            rows = {r["month"]: r for r in doc["rows"]}
            self.assertGreater(rows["2024-03"]["tax"], 0)
            self.assertLess(rows["2024-06"]["tax"], 0)
            self.assertEqual(rows["2024-12"]["cum_tax"],
                             doc["year_estimate"])


class TestPlotTaxStyle(unittest.TestCase):
    def test_style_both_countries(self):
        for c, flags in (("canada", ("--province", "ON")), ("usa", ())):
            with self.subTest(country=c):
                r = style_project(c).run("plot-tax", *flags, **_W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_legend_first(self, r.stdout,
                                    "Planning numbers, not filing")
                assert_styled(self, r.stdout)


if __name__ == "__main__":
    unittest.main()
