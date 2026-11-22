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
import json
import shutil
import unittest
from _support.ib_missing_history import _CUR, _ib, _make, _tj


def _sfx(country):
    return ".TO" if country == "canada" else ".US"


class TestListMarksMissingHistory(unittest.TestCase):
    """`tjs list` tells a purchase missing from the files (a sale
    with nothing to close) from a real short (the broker marks it, IB
    code O), with the pairs find-missing-history reports.

    Formerly: test_fix_mhscope.TestListMarksMissingHistory"""

    def test_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                self._check(country)

    def _check(self, country):
        sx = _sfx(country)
        tmp, root = _make(country)
        self.addCleanup(shutil.rmtree, tmp, True)
        csv = root / "inputs" / "margin" / "ib_margin.csv"
        csv.write_text(csv.read_text() + _ib(
            "U5550001", [("QSHO", "2025-03-10", -10, 8, 0, "O")],  # pii-ok
            _CUR[country]).split("Code\n", 1)[1])
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        suspects = {f"QOPN{sx}", f"QPAS{sx}", f"QXYZ{sx}", f"QRGL{sx}",
                    f"QRGT{sx}"}
        for extra in ((), ("--date", "2025-12-31")):
            doc = json.loads(_tj(root, "list", "--json", *extra).stdout)
            got = {x["symbol"]: x["missing_history_suspect"]
                   for x in doc["rows"]}
            self.assertEqual({s for s, v in got.items() if v}, suspects,
                             (extra, got))
            self.assertFalse(got[f"QSHO{sx}"])
            # the existing keys are unchanged
            self.assertIn("deferred_wash", doc["rows"][0])
        r = _tj(root, "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(ln for ln in r.stdout.splitlines()
                    if f"QPAS{sx}" in ln)
        self.assertTrue(line.rstrip().endswith("missing history?"), line)
        line = next(ln for ln in r.stdout.splitlines()
                    if f"QSHO{sx}" in ln)
        self.assertNotIn("missing history", line)
        r = _tj(root, "list", "--negative")
        out = r.stdout
        a = out.index("Short positions (1):")
        b = out.index("Missing history (a sale with no purchase in your "
                      "files) (5):")
        self.assertLess(a, b)
        self.assertIn(f"QSHO{sx}", out[a:b])
        self.assertNotIn(f"QSHO{sx}", out[b:])
        for sym in suspects:
            self.assertIn(sym, out[b:])
        # The fixes in full: --details (Essentials first); the default
        # view names the command.
        self.assertIn("tjs find-missing-history", out)
        out = _tj(root, "list", "--negative", "--details").stdout
        self.assertIn("`tjs find-missing-history --write-missing-history "
                      "--all-history`", " ".join(out.split()))
        # find-missing-history reports the same pairs, the real short
        # apart.
        fmh = _tj(root, "find-missing-history", "--year", "2025").stdout
        trunc = fmh.split("TRUNCATED HISTORY", 1)[1]
        for sym in suspects:
            self.assertIn(sym, trunc)
        self.assertNotIn(f"QSHO{sx}", trunc)
        self.assertIn(f"QSHO{sx}", fmh.split("TRUNCATED HISTORY", 1)[0])


if __name__ == "__main__":
    unittest.main()
