"""Essentials first (docs/output-style.md) for the summaries: estimate,
amt, instalments, carryover, t1135, fx-cash, form-export and
reconcile-slips. The default view keeps its budget (non-table lines,
`! ` lines within 100 columns, the legend above its table) on the
synthetic style projects at TAXJSON_WIDTH=100, and --details still
prints what the default view leaves out."""
import shutil
import tempfile
import unittest
from pathlib import Path

import _style
from _style import assert_concise, project


def _flat(text: str) -> str:
    return " ".join(text.split())


def _run(country, *args, **env):
    r = project(country).run(*args, TAXJSON_WIDTH="100", **env)
    return r


class TestEstimate(unittest.TestCase):
    ARGS = {"canada": ("estimate", "--province", "ON"),
            "usa": ("estimate",)}

    def test_default_is_the_estimate_block(self):
        for c, args in self.ARGS.items():
            with self.subTest(country=c):
                r = _run(c, *args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(r.stdout.startswith("TAX ESTIMATE — "),
                                r.stdout[:200])
                self.assertIn("ESTIMATED TAX ON INVESTMENT INCOME: ",
                              r.stdout)
                self.assertIn("tjs sum", r.stdout)
                # The summary's warnings are its `! ` lines, copied over.
                assert_concise(self, r.stdout, r.stderr, budget=6,
                               legend="ESTIMATE ONLY, not filing numbers")
                self.assertIn("! 2 sales with no purchase", r.stdout)
                self.assertNotIn("Assumes:", r.stdout)

    def test_details_keeps_the_summary_and_the_assumptions(self):
        for c, args in self.ARGS.items():
            with self.subTest(country=c):
                r = _run(c, *args, "--details")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("REALIZED-GAINS SUMMARY", r.stdout)
                self.assertLess(r.stdout.index("REALIZED-GAINS SUMMARY"),
                                r.stdout.index("TAX ESTIMATE"))
                self.assertIn("Assumes:", r.stdout)
        r = _run("canada", *self.ARGS["canada"], "--details")
        flat = _flat(r.stdout)
        self.assertIn("AMT recomputes with capital gains at 100%", flat)
        self.assertIn("trust distributions included", flat)


class TestAmt(unittest.TestCase):
    def test_default_and_details(self):
        r = _run("canada", "amt", "--province", "ON")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_concise(self, r.stdout, r.stderr, budget=6,
                       legend="ESTIMATE ONLY, not filing numbers")
        self.assertIn("= Federal minimum tax", r.stdout)
        self.assertNotIn("Law: ITA", r.stdout)
        d = _run("canada", "amt", "--province", "ON", "--details")
        self.assertIn("Law: ITA s.127.5-127.55", d.stdout)
        self.assertIn("not modelled (the estimate has no input",
                      _flat(d.stdout))


class TestInstalments(unittest.TestCase):
    def test_default_and_details(self):
        src = project("canada").root
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(src, root)
            cfg = root / "taxjson.toml"
            text = cfg.read_text().replace(
                '# province                        = "ON"',
                'province = "ON"  #')
            self.assertIn('province = "ON"', text)
            cfg.write_text(text + (
                '\n[estimate]\nother_income = 200000\n'
                '\n[instalments]\nbasis = "prior_year"\n'
                'prior_year_net_tax = 90000\n'
                'second_prior_net_tax = 80000\n'
                'paid = [{ date = "2024-03-15", amount = 900 }]\n'))
            p = _style.Project(root, "canada")
            r = p.run("instalments", TAXJSON_WIDTH="100")
            d = p.run("instalments", "--details", TAXJSON_WIDTH="100")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_concise(self, r.stdout, budget=6)
        self.assertIn("Net instalment interest", r.stdout)
        self.assertIn("! Behind by ", r.stdout)
        self.assertIn("Not modelled", d.stdout)
        self.assertIn("ITA 161(4.01)", _flat(d.stdout))


class TestCarryover(unittest.TestCase):
    def test_default_and_details(self):
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                r = _run(c, "carryover")
                self.assertEqual(r.returncode, 0, r.stderr)
                # (usa stderr: the books' stage notes, the run's rules)
                assert_concise(self, r.stdout,
                               *([r.stderr] if c == "canada" else []),
                               budget=6, legend="100% amounts")
                self.assertIn("! History starts in 2024", r.stdout)
                self.assertNotIn("NOTES", r.stdout)
                d = _run(c, "carryover", "--details")
                self.assertIn("NOTES", d.stdout)
                self.assertIn("this history starts in 2024", d.stdout)
        d = _run("canada", "carryover", "--details")
        self.assertIn("Apply the 50% inclusion rate", _flat(d.stdout))


class TestT1135(unittest.TestCase):
    def test_default_and_details(self):
        r = _run("canada", "t1135")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_concise(self, r.stdout, r.stderr, budget=6,
                       legend="Amounts are cost (ACB)")
        self.assertIn("Maximum total cost in 2024: ", r.stdout)
        self.assertIn("=> below the 100,000.00 CAD threshold", r.stdout)
        self.assertIn("! BTC, ETH: crypto: check where held", r.stdout)
        d = _run("canada", "t1135", "--details")
        flat = _flat(d.stdout)
        self.assertIn("Registered accounts (RRSP/TFSA/...) are excluded",
                      flat)
        self.assertIn("month-end FAIR MARKET VALUE", flat)


class TestFxCash(unittest.TestCase):
    def test_default_and_details(self):
        r = _run("canada", "fx-cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(
            "FX on foreign cash: NOT RELIABLE for 2024 — "), r.stdout)
        assert_concise(self, r.stdout, r.stderr, budget=6,
                       legend="GAIN(LOSS): the FX move")
        d = _run("canada", "fx-cash", "--details")
        self.assertIn("CAVEAT: currency conversions", d.stdout)
        self.assertIn("disposals exceeded the ledgered balance",
                      _flat(d.stdout))
        u = _run("usa", "fx-cash")
        assert_concise(self, u.stdout, u.stderr, budget=6)


class TestFormExport(unittest.TestCase):
    LEGEND = {"canada": "GAIN(LOSS) = PROCEEDS − ACB − OUTLAYS",
              "usa": "Each sale: (a) description"}

    def test_default_and_details(self):
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                r = _run(c, "form-export")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr, budget=6,
                               legend=self.LEGEND[c])
                self.assertNotIn("NOTES", r.stdout)
                d = _run(c, "form-export", "--details")
                self.assertIn("NOTES", d.stdout)
        r = _run("canada", "form-export")
        self.assertIn("Line 13200 (gain/loss):", r.stdout)
        flat = _flat(_run("canada", "form-export", "--details").stdout)
        self.assertIn("superficial loss 338.18 PERMANENTLY denied", flat)
        self.assertIn("T3 box 21 goes on line 17600", flat)
        u = _run("usa", "form-export")
        self.assertIn("gain:          6,097.14", u.stdout)
        self.assertIn("Code W rows are wash sales",
                      _flat(_run("usa", "form-export", "--details").stdout))


class TestReconcileSlips(unittest.TestCase):
    SLIP = {"canada": "inputs/slips/t5008.csv",
            "usa": "inputs/slips/1099b.csv"}

    def test_default_and_details(self):
        for c, slip in self.SLIP.items():
            with self.subTest(country=c):
                r = _run(c, "reconcile-slips", slip)
                assert_concise(self, r.stdout, r.stderr, budget=6,
                               legend="MISMATCH fails the check")
                self.assertNotIn("NOTES", r.stdout)
                d = _run(c, "reconcile-slips", slip, "--details")
                self.assertEqual(d.returncode, r.returncode)
                self.assertIn("NOTES", d.stdout)
                self.assertIn("Exchanges issue no", _flat(d.stderr))
        r = _run("canada", "reconcile-slips", self.SLIP["canada"])
        self.assertIn("! Explain or fix 3 difference(s)", r.stdout)
        d = _run("canada", "reconcile-slips", self.SLIP["canada"],
                 "--details")
        self.assertIn("often legitimate", _flat(d.stdout))


if __name__ == "__main__":
    unittest.main()
