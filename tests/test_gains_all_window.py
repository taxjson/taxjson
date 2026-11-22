"""A windowed view over more than the tax year (`stats 2y`, `winners
all`, `ccd-sum 2023`, --all-history): the seven views that read the
tax year's gains files (stats, win-rates, winners, losers, ccd-sum,
leaps, leaps-sum) read the full-history file built on demand from the
same consolidated base-currency book (work/<account>_gains_all.json),
said in an Info line, instead of warning that the rows outside the
year are missing. Synthetic books only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _qa_project import console, project, tj
from _support.qt_trades import QT_HEAD
from taxjson.lib.records import record_path, sha256_file

ROWS = (QT_HEAD
        + "2023-02-01,2023-02-03,Buy,ZZQ,ZZQ CORP,100,40.00,-4000.00,0,"
          "-4000.00,USD,Trades,55500001,Margin\n"           # pii-ok
        + "2023-06-01,2023-06-05,Sell,ZZQ,ZZQ CORP,-50,30.00,1500.00,0,"
          "1500.00,USD,Trades,55500001,Margin\n"            # pii-ok
        + "2024-01-02,2024-01-04,Buy,ZZQ,ZZQ CORP,100,50.00,-5000.00,0,"
          "-5000.00,USD,Trades,55500001,Margin\n"           # pii-ok
        + "2024-03-01,2024-03-05,Sell,ZZQ,ZZQ CORP,-150,60.00,9000.00,0,"
          "9000.00,USD,Trades,55500001,Margin\n")           # pii-ok

NOTE = "read from the full-history books"
OLD_WARNING = "cover tax year"
VIEWS = ("stats", "win-rates", "winners", "losers", "ccd-sum", "leaps",
         "leaps-sum")


def _json(root, *args):
    r = tj(root, *args, "--json")
    return json.loads(r.stdout), r


class TestTwoYearProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "two",
                           {"margin/questrade.csv": ROWS})
        tj(cls.root, "run", "--no-input")
        cls.all_file = cls.root / "work" / "margin_gains_all.json"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_default_window_is_the_tax_year_file(self):
        d, r = _json(self.root, "stats")
        self.assertEqual(d["classes"]["long-shares"]["trades"], 1)
        self.assertNotIn(NOTE, r.stderr)
        self.assertNotIn(OLD_WARNING, r.stderr)

    def test_stats_over_all_history_counts_both_years(self):
        d, r = _json(self.root, "stats", "all")
        self.assertEqual(d["classes"]["long-shares"]["trades"], 2)
        self.assertEqual(d["classes"]["long-shares"]["losses"], 1)
        self.assertIn(NOTE, r.stderr)
        self.assertNotIn(OLD_WARNING, r.stderr)
        self.assertTrue(self.all_file.is_file())
        d2, _r = _json(self.root, "stats", "--all-history")
        self.assertEqual(d2["classes"]["long-shares"]["trades"], 2)
        d3, _r = _json(self.root, "stats", "2y")
        self.assertEqual(d3["scope"], "last 2y")

    def test_an_earlier_year_window(self):
        d, r = _json(self.root, "stats", "2023")
        self.assertEqual(d["scope"], "tax year 2023")
        self.assertEqual(d["classes"]["long-shares"]["trades"], 1)
        self.assertLess(d["classes"]["long-shares"]["net_pl"], 0)
        w, _r = _json(self.root, "losers", "2023")
        self.assertEqual([x["ticker"] for x in w["rows"]], ["ZZQ.US"])
        self.assertLess(w["total_gain"], 0)
        self.assertEqual(w["basis"],
                         "denials among taxable accounts, full history")

    def test_in_year_rows_equal_the_filing_file(self):
        """The full-history file comes from the same consolidated book
        with the same flags: the tax year's rows match."""
        year, _r = _json(self.root, "winners")
        both, _r = _json(self.root, "winners", "all")
        prior, _r = _json(self.root, "winners", "2023")
        self.assertAlmostEqual(both["total_gain"],
                               year["total_gain"] + prior["total_gain"], 2)
        s_year, _r = _json(self.root, "stats")
        s_all, _r = _json(self.root, "stats", "all")
        self.assertAlmostEqual(
            s_all["classes"]["long-shares"]["largest_win"],
            s_year["classes"]["long-shares"]["largest_win"], 2)

    def test_every_view_takes_the_wide_window(self):
        for view in VIEWS:
            r = tj(self.root, view, "all")
            self.assertIn(NOTE, r.stderr, (view, console(r)))
            self.assertNotIn(OLD_WARNING, r.stderr, view)
        w, _r = _json(self.root, "win-rates", "all")
        self.assertEqual(w["total"]["trades"], 2)

    def test_built_every_time_a_view_reads_it(self):
        # No record's word serves a view (the owner's rule, TJ-4307): with
        # nothing changed, the next view builds the file again. Its record is
        # still written, for the sweep (`_vouched`).
        tj(self.root, "stats", "all")
        old = self.all_file.stat().st_mtime_ns - 10 ** 9
        os.utime(self.all_file, ns=(old, old))
        tj(self.root, "winners", "all")
        self.assertGreater(self.all_file.stat().st_mtime_ns, old)
        rec = json.loads(record_path(self.all_file).read_text(encoding="utf-8"))
        self.assertEqual(rec["out"], sha256_file(self.all_file))

    def test_built_once_per_command(self):
        # A command that reads the file again (the grant expiries, a pool's
        # covers, a blend's members) takes the one it built: its own build.
        # The next command builds it again (`run_state.begin_command`).
        from taxjson.cli import common
        from taxjson.pipeline import run_state
        settings = common.load_settings(self.root, missing_ok=True)
        work = self.root / "work"
        self.addCleanup(run_state.begin_command)
        with mock.patch.object(common, "run_to_file", wraps=common.run_to_file) as built:
            run_state.begin_command()
            for _ in range(2):
                self.assertEqual(common._gains_all_file(self.root, work, "margin", settings),
                                 self.all_file)
            self.assertEqual(built.call_count, 1)
            run_state.begin_command()
            common._gains_all_file(self.root, work, "margin", settings)
            self.assertEqual(built.call_count, 2)

    def test_the_tax_year_files_are_not_the_full_history_one(self):
        from taxjson.lib.report_model import resolve_gains_files
        files = resolve_gains_files(self.root / "work")
        self.assertEqual(list(files), ["margin"])
        self.assertFalse(files["margin"].name.endswith("_gains_all.json"))


class TestBasisLabel(unittest.TestCase):
    def test_full_history_label(self):
        from taxjson.lib.report_model import gains_basis_label
        # Backlog TJ-0452: the engine's own denials are in it, among
        # the taxable accounts (TJ-0677); "pre-wash" read as none at all.
        self.assertEqual(gains_basis_label({"m": Path("m_gains_all.json")}),
                         "denials among taxable accounts, full history")
        self.assertEqual(gains_basis_label([Path("m_gains_wash.json")]),
                         "wash-adjusted")
        self.assertEqual(gains_basis_label([Path("m_gains.json")]),
                         "pre-wash")


if __name__ == "__main__":
    unittest.main()
