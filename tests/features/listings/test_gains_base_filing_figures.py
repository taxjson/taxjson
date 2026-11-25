"""`tjs gains-base`: the window's dispositions in the base currency, the
filing figures (each account's wash-adjusted base book), each row the
filed form's cells for that one sale (Canada: Schedule 3's, the cells
`tjs gains-cra` shows, with COST(ACB) and GAIN before any denial, DENIED
and ALLOWED; US: Form 8949's (d), (e), (g), (h)), the totals per account
(taxable and sheltered apart) and THE RETURN, `tjs sum`'s RETURN row:
its PROCEEDS, OUTLAYS, DENIED and ALLOWED sum's, its COST(ACB) sum's
COST(ACB) + DENIED; the engine's unrounded total said where the rounding
moves it (src/taxjson/lib/gains_base.py, src/taxjson/cli/gains_filing.py
`cmd_gains_base`).

The mixed USD/CAD project, one whose three sales each round down a
part of a cent, one with a futures loss and a write for a net debit,
the demo, a US project with a wash sale and a future, and recorded
golden books.
"""
import json
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path

import _golden
from _support.filing_mixed_currency_project import Project, cents
from _tmpfiles import private_dir

from taxjson.lib import json_contract as J
from taxjson.lib.dispatch import python_module_argv

# The sum --json `filing` keys gains-base carries under the same names.
SUM_KEYS = ("totals", "engine_gain_unrounded", "engine_denied_unrounded", "date_basis")


def _same_as_sum(test, base, sm):
    """`base` (gains-base --json) against `sm` (sum --json): the filing
    block is sum's, THE RETURN row is sum's RETURN row in gains-base's
    columns, every row foots, no cell is negative, and the taxable rows
    are the return's within a cent a security."""
    f = base["totals"]["filing"]
    test.assertIsNotNone(f)
    for k in SUM_KEYS:
        test.assertEqual(f[k], sm["filing"][k], k)
    lines = "parts_8949" if "parts_8949" in sm["filing"] else "lines"
    test.assertEqual(f[lines], sm["filing"][lines])
    test.assertTrue(f["rows_match"])
    ret, t = base["totals"]["return"], sm["filing"]["totals"]
    if base["form"] == "8949":
        want = {k: cents(t[k]) for k in ("proceeds", "cost", "adjustment", "gain")}
        for r in base["rows"]:
            if r["form"] == "8949":
                test.assertEqual(cents(r["proceeds"]) - cents(r["cost"]) + cents(r["adjustment"]),
                                 cents(r["gain"]), r["symbol"])
    else:
        want = {"proceeds": cents(t["proceeds"]), "outlays": cents(t["outlays"]),
                "denied": cents(t["denied"]), "allowed": cents(t["gain"]),
                "cost": cents(t["acb"]) + cents(t["denied"]),
                "gain": cents(t["gain"]) - cents(t["denied"])}
        for r in base["rows"]:
            with test.subTest(row=(r["date"], r["symbol"])):
                test.assertEqual(cents(r["proceeds"]) - cents(r["cost"]) - cents(r["outlays"]),
                                 cents(r["gain"]))
                test.assertEqual(cents(r["gain"]) + cents(r["denied"]), cents(r["allowed"]))
                test.assertGreaterEqual(min(r["proceeds"], r["cost"], r["outlays"]), 0)
    test.assertEqual({k: cents(ret[k]) for k in want}, want)
    taxable = [r for r in base["rows"] if r["taxable"]]
    for k in want:
        gap = cents(base["totals"]["taxable"][k]) - want[k]
        test.assertEqual(gap, cents(base["totals"]["rows_less_return"].get(k, 0)), k)
        test.assertLessEqual(abs(gap), Decimal("0.01") * len({r["symbol"] for r in taxable}), k)


class TestTheMixedProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.run()
        cls.doc = cls.p.json("gains-base")
        cls.sum = cls.p.json("sum")

    def test_the_document_s_number(self):
        self.assertEqual(self.doc["schema_version"], J.SCHEMAS["gains-base"])
        self.assertEqual((self.doc["currency"], self.doc["scope"]), ("CAD", "tax year 2025"))
        self.assertEqual((self.doc["form"], self.doc["columns"]),
                         ("schedule3", ["proceeds", "cost", "outlays", "gain", "denied",
                                        "allowed"]))

    def test_the_figure_for_the_return_is_sum_s(self):
        _same_as_sum(self, self.doc, self.sum)
        self.assertEqual(self.doc["totals"]["filing"]["command"], "tjs sum")
        self.assertEqual(cents(self.doc["totals"]["return"]["allowed"]),
                         cents(self.sum["filing"]["totals"]["gain"]))

    def test_each_sale_s_cents_are_gains_cra_s(self):
        cra = {r["id"]: r for r in self.p.json("gains-cra")["rows"]}
        rows = [r for r in self.doc["rows"] if r["taxable"]]
        self.assertEqual({r["id"] for r in rows}, set(cra))
        for r in rows:
            c = cra[r["id"]]
            with self.subTest(symbol=r["symbol"], date=r["date"]):
                self.assertEqual([cents(r[k]) for k in ("proceeds", "outlays", "allowed")],
                                 [cents(c[k]) for k in ("proceeds", "outlays", "gain")])
                self.assertEqual(cents(r["cost"]), cents(c["acb"]) + cents(c["denied"]))

    def test_the_sheltered_rows_are_shown_and_kept_out(self):
        tfsa = [r for r in self.doc["rows"] if r["account"] == "tfsa"]
        self.assertEqual([(r["symbol"], r["taxable"]) for r in tfsa], [("QZAA.TO", False)])
        t = self.doc["totals"]
        self.assertEqual((t["sheltered"]["dispositions"], cents(t["sheltered"]["allowed"])),
                         (1, Decimal("20.00")))
        self.assertEqual(t["taxable"]["dispositions"], 7)
        self.assertEqual([(a["account"], a["type"]) for a in t["accounts"]],
                         [("margin", "taxable"), ("tfsa", "sheltered")])

    def test_gain_before_denial_denied_and_allowed(self):
        r = [x for x in self.doc["rows"] if x["symbol"] == "QZLS.TO"][0]
        self.assertEqual((r["proceeds"], r["cost"], r["outlays"]), (400.0, 500.0, 0.0))
        self.assertEqual((r["gain"], r["denied"], r["allowed"]), (-100.0, 100.0, 0.0))
        for x in self.doc["rows"]:
            self.assertEqual(cents(x["gain"]) + cents(x["denied"]), cents(x["allowed"]), x["symbol"])

    def test_a_short_sale_s_legs_the_real_world_way(self):
        r = [x for x in self.doc["rows"] if x["symbol"] == "QZSH.US"][0]
        self.assertEqual(r["direction"], "SHORT")
        self.assertGreater(r["proceeds"], r["cost"])
        self.assertGreater(r["cost"], 0)

    def test_the_text(self):
        r = self.p.tjs("gains-base", TAXJSON_WIDTH="120")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(
            "REALIZED GAINS — CAD, tax year 2025, the filing figures (wash-adjusted)\n"))
        self.assertIn("SHELTERED (not on the return)", r.stdout)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[1], "PROCEEDS − COST(ACB) − OUTLAYS = GAIN (a loss negative), "
                                   "before any superficial-loss denial; GAIN + DENIED = ALLOWED.")
        self.assertIn("THE RETURN's COST(ACB) = tjs sum's COST(ACB) + DENIED.", lines[2])
        ret = next(ln for ln in lines if ln.startswith("THE RETURN (tjs sum)"))
        t = self.sum["filing"]["totals"]
        self.assertTrue(ret.endswith(f"{t['gain']:,.2f}"), ret)
        self.assertIn(f"  {t['proceeds']:,.2f}  ", ret)
        self.assertEqual([ln for ln in lines if len(ln) > 120], [])

    def test_a_narrower_view_has_no_figure_for_the_return(self):
        for args in (("--options",), ("2024",), ("tfsa",)):
            with self.subTest(args=args):
                doc = self.p.json("gains-base", *args)
                self.assertIsNone(doc["totals"]["filing"])
                self.assertIsNone(doc["totals"]["return"])
        self.assertIn("this view shows fewer", self.p.tjs("gains-base", "--options").stdout)
        self.assertIn("tfsa is sheltered", self.p.tjs("gains-base", "tfsa").stdout)
        one = self.p.json("gains-base", "margin")["totals"]["filing"]
        self.assertEqual(one["command"], "tjs sum margin")
        self.assertEqual(one["totals"], self.p.json("sum", "margin")["filing"]["totals"])


class TestEachLineRoundedToTheCent(unittest.TestCase):
    """Three sales each a part of a cent over: Schedule 3 rounds each
    security's line down, so the return's figure is a cent under the
    rows' unrounded total, as `tjs sum --details` says."""

    TT = "".join(f"BUYSELL 2025-01-06 10:00:00 {s}.TO 1 CAD 10.000 10.000 0\n"
                 f"BUYSELL 2025-03-10 10:00:00 {s}.TO -1 CAD 11.004 11.004 0\n"
                 for s in ("QZRA", "QZRB", "QZRC"))

    def test_the_return_s_figure_and_the_unrounded_one(self):
        p = Project(files={"margin": self.TT}, planted=False)
        p.run()
        doc, sm = p.json("gains-base"), p.json("sum")
        _same_as_sum(self, doc, sm)
        f = doc["totals"]["filing"]
        self.assertEqual((f["totals"]["gain"], f["engine_gain_unrounded"]), (3.0, 3.01))
        self.assertEqual(doc["totals"]["taxable"]["allowed"], 3.0)
        self.assertEqual(doc["totals"]["rows_less_return"], {})
        self.assertEqual([r["gain_unrounded"] for r in doc["rows"]], [1.004] * 3)
        text = p.tjs("gains-base").stdout
        self.assertIn("the engine's unrounded total is 3.01 (-0.01 on the return's ALLOWED)",
                      text)
        self.assertIn("unrounded total gain is 3.01 (-0.01", p.tjs("sum", "--details").stdout)


class TestAFuturesLossAndANetDebitWrite(unittest.TestCase):
    """A future closed at a loss shows the T5008 shape (PROCEEDS 0.00, the
    loss as COST(ACB)); a put written under grant timing for less than its
    commission shows the premium gross and the commission as OUTLAYS:
    Schedule 3's cells, never a negative PROCEEDS."""

    TT = ("BUYSELL 2025-08-11 10:00:00 F:QZM26.US 1 USD 305 30500 0 x100\n"
          "BUYSELL 2025-09-03 10:00:00 F:QZM26.US -1 USD 290 29000 0 x100\n"
          "BUYSELL 2025-06-02 10:00:00 QZRX251219P00010000.US -1 USD 0.01 -0.30 1.30\n")

    def test_the_cells(self):
        p = Project(files={"margin": self.TT})
        p.run()
        doc, sm = p.json("gains-base"), p.json("sum")
        _same_as_sum(self, doc, sm)
        fut, put = (next(r for r in doc["rows"] if r["symbol"] == s)
                    for s in ("F:QZM26.US", "QZRX251219P00010000.US"))
        self.assertEqual((fut["proceeds"], fut["outlays"]), (0.0, 0.0))
        self.assertGreater(fut["cost"], 0)
        self.assertEqual(cents(fut["cost"]), -cents(fut["gain"]))
        self.assertGreater(put["proceeds"], 0)
        self.assertGreater(put["outlays"], put["proceeds"])
        self.assertEqual(put["cost"], 0.0)
        self.assertLess(put["allowed"], 0)


class TestTheBooksAndTheCountry(unittest.TestCase):
    def test_books_older_than_the_inputs_are_refused(self):
        p = Project(files={"margin": "BUYSELL 2025-01-06 10:00:00 QZNW.TO 10 CAD 10 100 0\n"
                                     "BUYSELL 2025-03-06 10:00:00 QZNW.TO -10 CAD 12 120 0\n"},
                    planted=False)
        p.run()
        self.assertEqual(p.tjs("gains-base").returncode, 0)
        tt = p.root / "inputs" / "margin" / "trades.tt"
        tt.write_text(tt.read_text() + "BUYSELL 2025-04-06 10:00:00 QZNW.TO 1 CAD 10 10 0\n")
        r = p.tjs("gains-base", "--json")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("tjs run", r.stderr)

    def test_a_us_project_leaves_the_section_1256_contracts_out(self):
        toml = ('[settings]\nlocal_timezone = "America/New_York"\nyear = 2025\n'
                'country = "usa"\nbase_currency = "USD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
        p = Project(files={"margin": "BUYSELL 2025-01-06 10:00:00 QZNW 10 USD 10 100 0\n"
                                     "BUYSELL 2025-03-06 10:00:00 QZNW -10 USD 12 120 0\n"
                                     "BUYSELL 2025-01-06 10:00:00 QZLS 10 USD 50 500 0\n"
                                     "BUYSELL 2025-05-05 10:00:00 QZLS -10 USD 40 400 0\n"
                                     "BUYSELL 2025-05-12 10:00:00 QZLS 10 USD 41 410 0\n"
                                     "BUYSELL 2025-08-11 10:00:00 F:QZM26 1 USD 300 30000 0 x100\n"
                                     "BUYSELL 2025-09-03 10:00:00 F:QZM26 -1 USD 310 31000 0 x100\n"},
                    toml=toml, planted=False)
        p.run()
        doc, sm = p.json("gains-base"), p.json("sum")
        _same_as_sum(self, doc, sm)
        self.assertEqual((doc["form"], doc["columns"]),
                         ("8949", ["proceeds", "cost", "adjustment", "gain"]))
        self.assertEqual({r["symbol"]: r["form"] for r in doc["rows"]},
                         {"QZNW": "8949", "QZLS": "8949", "F:QZM26": "6781"})
        wash = next(r for r in doc["rows"] if r["symbol"] == "QZLS")
        self.assertEqual([wash[k] for k in ("proceeds", "cost", "adjustment", "gain", "code")],
                         [400.0, 500.0, 100.0, 0.0, "W"])
        fut = next(r for r in doc["rows"] if r["symbol"] == "F:QZM26")
        self.assertEqual([fut[k] for k in ("proceeds", "cost", "adjustment", "gain")],
                         [None, None, None, 1000.0])
        self.assertEqual(doc["totals"]["return"]["gain"], 20.0)
        self.assertEqual(doc["totals"]["taxable"]["section_1256_gain"], 1000.0)
        text = p.tjs("gains-base").stdout
        self.assertIn("Form 8949's cells per sale: (d) PROCEEDS − (e) COST + (g) ADJUSTMENT = "
                      "(h) GAIN", text)
        self.assertIn("100.00 W", text)
        self.assertIn("Not on Form 8949: the section 1256 contracts, Form 6781 by hand "
                      "(1,000.00).", text)


class TestTheDemo(unittest.TestCase):
    def test_the_figure_for_the_return_is_sum_s(self):
        p = Project(files={}, planted=False)
        demo = Path(private_dir()) / "demo"
        r = subprocess.run(python_module_argv("taxjson.bin.taxjson_run") + ["init", "--demo", str(demo)],
                           capture_output=True, text=True, env=p.env, stdin=subprocess.DEVNULL,
                           timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        years = sorted(d for d in demo.iterdir() if d.is_dir() and d.name.isdigit())
        p.root = years[0]
        p.run()
        doc, sm = p.json("gains-base"), p.json("sum")
        _same_as_sum(self, doc, sm)
        self.assertTrue([r for r in doc["rows"] if not r["taxable"]])
        self.assertTrue([r for r in doc["rows"] if r["permanently_denied"]])


class TestAGoldenBook(unittest.TestCase):
    """style-canada's and style-usa's recorded views: gains-base's figure
    for the return is their sum's."""

    def _view(self, book, argv):
        name = _golden.view_name({"argv": argv})
        p = _golden.GOLDEN / book / "expected" / "_views" / f"{name}.stdout.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def test_the_figure_for_the_return(self):
        for book in ("style-canada", "style-usa"):
            with self.subTest(book=book):
                _same_as_sum(self, self._view(book, "gains-base --json"),
                             self._view(book, "sum --json"))


if __name__ == "__main__":
    unittest.main()
