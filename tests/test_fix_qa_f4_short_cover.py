"""QA F4 (external QA suite, synthetic data): a short sale closed within
the year: the run's mid-run note follows the engine's booking
(lib/first_run.engine_booking), and find-missing-history counts only the
sale, not its cover."""
import tempfile
import unittest

from taxjson.lib import out

from _qa_project import project, tj

# ------------------------------------------------------------------ F4

F4_BOOK = ("BUYSELL 2024-02-01 10:00:00 ZZN.TO -100 CAD 50 5000\n"
           "BUYSELL 2024-04-01 10:00:00 ZZN.TO 100 CAD 40 4000\n")


class TestF4ShortClosedInYear(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _check(self, country):
        root = project(self.tmp.name, country, {"margin/book.tt": F4_BOOK},
                       country=country)
        r = tj(root, "run", "--no-input")
        text = r.stdout
        self.assertIn("1 position(s) go short in margin's data (ZZN.TO): "
                      "booked as short sales closed by a later purchase",
                      text)
        self.assertNotIn("in no total", text)
        self.assertIn("Their gain is in the totals", text)
        self.assertEqual(out.lint(r.stdout), [])
        fm = tj(root, "find-missing-history", check=False).stdout
        row = [ln for ln in fm.splitlines() if ln.startswith("ZZN.TO")]
        self.assertTrue(row, fm)
        cells = row[0].split()
        # InYrSales 1 (the cover is no sale), InYrProceeds the sale's
        # (in the project's currency: CAD 5,000 in Canada)
        self.assertEqual(cells[5], "1", row[0])
        if country == "canada":
            self.assertEqual(cells[6], "5,000.00", row[0])

    def test_canada(self):
        self._check("canada")

    def test_usa(self):
        self._check("usa")

    def test_open_short_keeps_in_no_total(self):
        root = project(self.tmp.name, "open", {"margin/book.tt": F4_BOOK.split(
            "\n")[0] + "\n"})
        r = tj(root, "run", "--no-input")
        self.assertIn("their gain is in no total",
                      " ".join(r.stdout.split()))


if __name__ == "__main__":
    unittest.main()
