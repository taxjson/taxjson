"""QA F2 (external QA suite, synthetic data): a .tt line whose total is
off qty x price + fee by more than 1% is booked as written, and now said:
the run's console (file:line, both figures, on every run), `run
--strict` and `taxjson checklist`'s run-clean step (lib/tt_totals)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import out

from _qa_project import console, gains, project, tj

# ------------------------------------------------------------------ F2

F2_BOOK = ("BUYSELL 2024-01-10 10:00:00 ZZA.TO 100 CAD 10 1100\n"
           "BUYSELL 2024-06-03 10:00:00 ZZA.TO -100 CAD 12 1200\n")


class TestF2TtTotals(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "f2", {"margin/book.tt": F2_BOOK})
        cls.r = tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_booked_as_written_and_said_on_the_console(self):
        sale, = [g for g in gains(self.root) if g.get("symbol") == "ZZA.TO"]
        self.assertAlmostEqual(sale["cost"], 1100.0)
        self.assertAlmostEqual(sale["gain"], 100.0)
        text = console(self.r)
        self.assertIn("Warning: inputs/margin/book.tt:1: a .tt line's "
                      "total 1100.00 is not qty x price + fee = 1000.00",
                      text)
        self.assertIn("BUYSELL 2024-01-10 10:00:00 ZZA.TO 100 CAD 10 1100\n",
                      text)
        self.assertIn("The total is what is booked", text)
        # (the sale's total agrees: no second warning)
        self.assertNotIn("book.tt:2", text)

    def test_style(self):
        self.assertEqual(out.lint(self.r.stderr), [])
        self.assertEqual(out.lint(self.r.stdout), [])

    def test_cached_run_still_warns(self):
        # Read from the .diag on every run, the .tt converted again or
        # not.
        r = tj(self.root, "run", "--no-input")
        self.assertIn("book.tt:1: a .tt line's total 1100.00", console(r))

    def test_strict_stops(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        r = tj(d, "run", "--no-input", "--strict", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--strict: margin: inputs/margin/book.tt has 1 "
                      "line(s) whose total is not qty x price +/- fee",
                      r.stderr)

    def test_checklist_run_clean_lists_it(self):
        from datetime import date

        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        ctx = cl.Ctx(root=self.root, cfg=cfg, year=2024,
                     today=date(2025, 3, 1),
                     run_sub=lambda argv, timeout=900: (0, "", ""))
        res = cl.d_run_clean(ctx)
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("1 .tt line(s) whose total is not qty x price +/- "
                      "fee, booked as written: inputs/margin/book.tt:1",
                      res.detail)


class TestF2Thresholds(unittest.TestCase):
    """The 1% threshold, its 0.05 floor, a 5x total, a line with no
    price and a sale whose commission exceeds its gross."""

    def _run(self, book):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, "p", {"margin/book.tt": book})
            r = tj(root, "run", "--no-input")
            return console(r)

    def test_five_times_total(self):
        text = self._run("BUYSELL 2024-01-10 10:00:00 ZZA.TO 100 CAD 10 5000\n"
                         "BUYSELL 2024-06-03 10:00:00 ZZA.TO -100 CAD 12 1200\n")
        self.assertIn("a .tt line's total 5000.00 is not qty x price + fee "
                      "= 1000.00", text)

    def test_tiny_total_rounding_and_no_price_are_quiet(self):
        text = self._run(
            # 3 x 0.333 = 0.999: a total of 1.03 is within the 0.05 floor
            "BUYSELL 2024-01-10 10:00:00 ZZT.TO 3 CAD 0.333 1.03\n"
            # no price: the total alone states the amount
            "BUYSELL 2024-01-11 10:00:00 ZZP.TO 10 CAD 0 250\n")
        self.assertNotIn("a .tt line's total", text)

    def test_within_one_percent_is_quiet(self):
        text = self._run("BUYSELL 2024-01-10 10:00:00 ZZA.TO 100 CAD 10 1009\n")
        self.assertNotIn("a .tt line's total", text)

    def test_sell_total_zero_with_commission_over_gross(self):
        text = self._run("BUYSELL 2024-01-10 10:00:00 ZZQ.TO 1 CAD 0.02 0.02\n"
                         "BUYSELL 2024-06-03 10:00:00 ZZQ.TO -1 CAD 0.01 0 9.95\n")
        self.assertIn("inputs/margin/book.tt:2: a .tt sale's total is 0 but "
                      "its commission exceeds its gross", text)


if __name__ == "__main__":
    unittest.main()
