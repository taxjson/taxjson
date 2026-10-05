"""House output style (docs/output-style.md) for the position, period and
roll-up views, stats and crypto-sends: their diagnostics are headline +
indented detail with the `taxjson <cmd>:` prefix, their tables fit the
width (dropping the least important columns first), and the re-importable
`events PERIOD ACCOUNT` taxtext keeps its bytes. Synthetic style projects
(tests/_style.py) only."""
import unittest

from _style import assert_styled, project

# The commands converted here (their stdout is in
# test_style_smoke.TestConvertedCommands.CASES).
_CMDS = [("list",), ("shares", "--options"), ("events",), ("trades",),
         ("divs",), ("dil",), ("fees",), ("gains",), ("leaps",), ("roc",),
         ("transfers",), ("ccd-sum",), ("dil-sum",), ("divs-sum",),
         ("fees-sum",), ("leaps-sum",), ("roc-sum",), ("trades-sum",),
         ("winners", "all"), ("stats",), ("stats", "--all-history"),
         ("crypto-sends",)]


class TestDiagnostics(unittest.TestCase):
    def test_stderr_is_styled(self):
        for country in ("canada", "usa"):
            p = project(country)
            for args in _CMDS:
                with self.subTest(country=country, args=args):
                    r = p.run(*args)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    assert_styled(self, r.stderr)
                    for ln in r.stderr.splitlines():
                        if ln and not ln.startswith(" "):
                            self.assertRegex(
                                ln, r"^(taxjson [\w-]+: )?(note|warning|"
                                    r"error): ", ln)

    def test_note_is_a_headline_with_detail(self):
        r = project("canada").run("gains")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stderr.splitlines()
        i = next(i for i, ln in enumerate(lines)
                 if ln.startswith("taxjson gains: note: crypto account"))
        self.assertTrue(lines[i + 1].startswith("  Its gains are in "))

    def test_errors_name_the_command(self):
        p = project("usa")
        r = p.run("list", "--date", "2024-13-01")
        self.assertEqual(r.returncode, 1)
        self.assertIn("taxjson list: error: --date 2024-13-01 is not a "
                      "real calendar date", r.stderr)
        r = p.run("fees-sum", "nosuch")
        self.assertEqual(r.returncode, 1)
        lines = r.stderr.strip().splitlines()
        self.assertTrue(lines[0].startswith(
            "taxjson fees-sum: error: no files for account 'nosuch' in "
            "work/ — run"), lines)
        self.assertTrue(all(ln.startswith("  ") for ln in lines[1:]), lines)
        self.assertIn("run `taxjson run` first, or check the name",
                      " ".join(r.stderr.split()))
        r = p.run("winners", "--top", "0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("taxjson winners: error: --top must be >= 1", r.stderr)


class TestFitted(unittest.TestCase):
    def test_transfers_drops_columns_on_a_narrow_terminal(self):
        r = project("canada").run("transfers", TAXJSON_WIDTH=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_styled(self, r.stdout, width=60)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("DATE"))
        self.assertEqual(head.split(), ["DATE", "ACCOUNT", "SYMBOL", "QTY",
                                        "VALUE", "CUR", "IN_BOOKS"])

    def test_stats_drops_the_largest_columns_first(self):
        r = project("canada").run("stats", TAXJSON_WIDTH=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("CLASS"))
        self.assertNotIn("LARGEST_WIN", head)
        self.assertIn("PROFIT_FACTOR", head)
        self.assertIn("How the trades are counted", r.stdout)
        # Unwrapped (captured), every column stays.
        r = project("canada").run("stats", TAXJSON_WIDTH=0)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("CLASS"))
        self.assertIn("LARGEST_WIN", head)

    def test_fees_sum_sections(self):
        r = project("canada").run("fees-sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith(
            "TRADING FEES BY ACCOUNT / BROKERAGE — tax year 2024"))
        self.assertIn("Non-option vs option fees", lines)
        self.assertIn("Definitions", lines)
        self.assertTrue(lines[-1].startswith("- $/UNIT")
                        or lines[-2].startswith("- $/UNIT"))

    def test_crypto_sends_keeps_the_tt_line_whole(self):
        r = project("usa").run("crypto-sends", TAXJSON_WIDTH=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        tt = [ln for ln in r.stdout.splitlines() if ".tt if payment:" in ln]
        self.assertEqual(len(tt), 1)
        self.assertTrue(tt[0].rstrip().endswith(" 0"), tt[0])
        self.assertTrue(r.stdout.splitlines()[-1].startswith(
            "Decide: taxjson crypto-sends crypto --set ID=self|payment"))


class TestTaxtextUnchanged(unittest.TestCase):
    def test_events_for_one_account_is_pure_taxtext(self):
        a = project("canada").run("events", "all", "margin")
        b = project("canada").run("events", "all", "margin",
                                  TAXJSON_WIDTH=40)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        first = a.stdout.splitlines()[0]
        self.assertRegex(first, r"^[A-Z_]+ +\d{4}-\d{2}-\d{2} ")


if __name__ == "__main__":
    unittest.main()
