"""`tjs run`'s console scoped to the tax year (owner feedback from a
new-user run).

* Positions that go short with no purchase in the files are listed one
  by one only when they bear on the project's year — a row of the year
  draws on the missing purchase or touches the position at all, or
  (Canada) another taxable account of its ACB pool trades the symbol
  that year: the test find-missing-history sorts its sections by
  (lib/missing_history.classify_year_shorts). The rest are ONE `Info:`
  line naming `find-missing-history --write-missing-history
  --outside-year`, which records them; the .diag / .sum keep every
  line.
* An engine message is shown once per run whichever pass says it.
* Whole lines in order through a pipe; the checks before the first
  stage under a step; a failed stage's headline repeated in the run's
  last line; the Questrade codes note one code per line.

Every fixture is synthetic: invented tickers, fake account ids (each
line marked pii-ok), small amounts.

Formerly: test_fix_mhscope"""
import re
import shutil
import unittest
from _support.ib_missing_history import _make, _tj


def _sfx(country):
    return ".TO" if country == "canada" else ".US"


class TestShortsScopedToTheYear(unittest.TestCase):
    """The owner's case in both countries: the shorts of the year are
    listed, the others are one line; the captured text keeps them all;
    `--outside-year` records the others and changes no number of the
    year.

    Formerly: test_fix_mhscope.TestShortsScopedToTheYear"""

    def test_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                self._check(country)

    def _check(self, country):
        tmp, root = _make(country)
        self.addCleanup(shutil.rmtree, tmp, True)
        sx = _sfx(country)
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        text = r.stdout + r.stderr
        from taxjson.lib import out
        self.assertEqual(out.console_lint(r.stdout), [], r.stdout)
        shown = re.findall(r"(?m)^Warning: Short position: (\S+) \((\w+)\)",
                           text)
        # The year's shorts, once each; not the ones that went short in
        # 2024 and never trade again (margin and the registered rrsp).
        self.assertEqual(sorted(shown), sorted([
            (f"QABC{sx}", "margin"), (f"QXYZ{sx}", "margin"),
            (f"QRGT{sx}", "rrsp")]), text)
        # One summary line for the three others, naming the command.
        flat = " ".join(r.stdout.split())
        self.assertEqual(flat.count("went short before 2025"), 1, text)
        self.assertIn("Info: 3 positions went short before 2025 with "
                      "missing history; no effect on 2025's numbers", flat)
        # The one-line form names the command; its flags
        # (--write-missing-history --outside-year) are in the detail
        # (`run --details`; docs/output-style.md, Essentials first).
        self.assertIn("find-missing-history`", flat)
        # Under its own step, after every account's books.
        steps = re.findall(r"(?m)^==> (.*)$", r.stdout)
        i = steps.index("Checking for missing purchase history")
        self.assertIn("Writing summary reports/margin.sum", steps[:i])
        # The captured text keeps every short (.diag and .sum).
        diag = (root / "work" / "margin_gains.json.diag").read_text()
        for s in ("QPAS", "QOPN", "QABC", "QXYZ"):
            self.assertIn(f"warning: ATTENTION: short: {s}{sx} (margin)",
                          diag)
        self.assertIn(f"QRGL{sx} (rrsp)", (root / "reports" /
                                            "rrsp.sum").read_text())

        # find-missing-history agrees: the summarised ones are its NOT
        # relevant rows, a still-short one says so (each row's why is
        # behind --details: docs/output-style.md, Essentials first).
        r = _tj(root, "find-missing-history", "--details")
        nr = r.stdout.split("NOT relevant to 2025", 1)[1]
        for s in ("QPAS", "QOPN", "QRGL"):
            self.assertIn(f"{s}{sx}", nr)
        self.assertNotIn("QABC", nr)
        self.assertIn("still short at the start of 2025, with no 2025 "
                      "activity", " ".join(nr.split()))

        before = _tj(root, "sum", "--json").stdout
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        # (dated .tt lines in the accounts' inputs: OPENING ...
        # cost=unknown — lib/missing_history)
        got = []
        for acct in ("margin", "rrsp"):
            f = root / "inputs" / acct / "missing_history.tt"
            got += [(ln.split()[2], acct) for ln in
                    (f.read_text().splitlines() if f.exists() else [])
                    if ln.startswith("OPENING ")]
        self.assertEqual(sorted(got),
                         [(f"QOPN{sx}", "margin"), (f"QPAS{sx}", "margin"),
                          (f"QRGL{sx}", "rrsp")])
        # A second call adds nothing (never a line twice).
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to add",
                      " ".join(r.stderr.split()).lower())
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("went short before", r.stdout)
        self.assertEqual(len(re.findall(r"(?m)^Warning: Short position:",
                                        r.stdout)), 3, r.stdout)
        # The year's numbers do not move.
        self.assertEqual(before, _tj(root, "sum", "--json").stdout)

    def test_outside_year_keeps_your_lines(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(_tj(root, "run", "--no-input").returncode, 0)
        f = root / "inputs" / "margin" / "missing_history.tt"
        mine = "OPENING 2020-01-01 QHAND.TO 5 cost=unknown  # by hand\n"
        f.write_text(mine)
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = f.read_text()
        self.assertTrue(text.startswith(mine), text)
        def n(t):
            return sum(1 for ln in t.splitlines() if ln.startswith("OPENING "))
        self.assertEqual(n(text), 3)
        self.assertEqual(n((root / "inputs" / "rrsp" / "missing_history.tt")
                           .read_text()), 1)

    def test_outside_year_needs_write(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(_tj(root, "run", "--no-input").returncode, 0)
        r = _tj(root, "find-missing-history", "--outside-year")
        self.assertEqual(r.returncode, 2, r.stderr)
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year", "--all-history")
        self.assertEqual(r.returncode, 2, r.stderr)


class TestYearScopeLib(unittest.TestCase):
    """lib/missing_history: the row fields the run and the report share.

    Formerly: test_fix_mhscope.TestYearScopeLib"""

    def _rows(self, book, year=2025, country="canada", registered=None):
        from taxjson.lib.missing_history import classify_year_shorts
        return classify_year_shorts(
            book, year, country=country,
            registered=registered or {"margin": False, "qt": False},
            date_basis="trade")

    def test_activity_and_year_start(self):
        from tax_rules.dual import tx
        book = [
            # short in 2024, a dividend in 2025: touched in the year
            tx("BUYSELL", "2024-03-01", "QDV.TO", -10, 100),
            tx("DIVIDEND", "2025-03-01", "QDV.TO", 0, 5.0,
               gross_amount=5.0),
            # short in 2024, nothing since: still short, not listed
            tx("BUYSELL", "2024-03-01", "QNO.TO", -10, 100),
            # short in 2024, covered in 2024: flat at the year's start
            tx("BUYSELL", "2024-03-01", "QFL.TO", -10, 100),
            tx("BUYSELL", "2024-04-01", "QFL.TO", 10, 90),
        ]
        rows = self._rows(book)
        dv = rows[("QDV.TO", "margin")]
        self.assertFalse(dv.affects_year)
        self.assertTrue(dv.in_year_activity)
        self.assertTrue(dv.year_listed)
        no = rows[("QNO.TO", "margin")]
        self.assertFalse(no.year_listed)
        self.assertTrue(no.short_at_year_start)
        fl = rows[("QFL.TO", "margin")]
        self.assertFalse(fl.year_listed)
        self.assertFalse(fl.short_at_year_start)

    def test_pool_differs_by_country(self):
        # One account's short of 2024 with another taxable account
        # trading the symbol in 2025: Canada pools the two accounts'
        # shares (s.47) — that account's 2025 gain moves, so the pair
        # is listed; the US keeps basis per account — it is not.
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2024-03-01", "QPL", -10, 100),
                tx("BUYSELL", "2023-03-01", "QPL", 20, 150, account="qt"),
                tx("BUYSELL", "2025-03-01", "QPL", -20, 220, account="qt")]
        ca = self._rows(book, country="canada")[("QPL", "margin")]
        us = self._rows(book, country="usa")[("QPL", "margin")]
        self.assertEqual(ca.pooled_with, ("qt",))
        self.assertTrue(ca.year_listed)
        self.assertEqual(us.pooled_with, ())
        self.assertFalse(us.year_listed)
        # A registered account is not in the pool.
        reg = self._rows(book, country="canada",
                         registered={"margin": False, "qt": True})
        self.assertFalse(reg[("QPL", "margin")].year_listed)

    def test_pool_on_the_run_console(self):
        tmp, root = _make("canada", extra_accounts={
            "qt": ("taxable", "U5550003",  # pii-ok
                   [("QPAS", "2023-02-01", 40, 2, 81, "O"),
                    ("QPAS", "2025-02-03", -40, 3, -81, "C")])})
        self.addCleanup(shutil.rmtree, tmp, True)
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"(?m)^Warning: Short position: "
                                   r"QPAS\.TO \(margin\)")
        self.assertIn("Info: 2 positions went short before 2025",
                      r.stdout)
        # (each row's why: --details, docs/output-style.md)
        r = _tj(root, "find-missing-history", "--details")
        aff = r.stdout.split("AFFECTS 2025", 1)[1].split("\n\n", 1)[0]
        self.assertIn("QPAS.TO", aff)
        self.assertIn("one ACB pool", " ".join(aff.split()))


if __name__ == "__main__":
    unittest.main()
