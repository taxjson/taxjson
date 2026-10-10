"""Essentials first (docs/output-style.md) for the trading and loss
commands: the default view keeps the budget (legend before the table,
one-line `! ` actions, at most 6 other lines at width 100) and
`--details` prints what the default view leaves out."""
import unittest

import _style
from _style import assert_concise, project

_W = {"TAXJSON_WIDTH": "100"}


def _sfx(country):
    return ".TO" if country == "canada" else ".US"


def _flat(text):
    return " ".join((text or "").split())


class _Base(unittest.TestCase):
    def run_both(self, *args, details=False, ok=(0,)):
        out = {}
        for c in ("canada", "usa"):
            a = [x.format(sfx=_sfx(c)) for x in args]
            if details:
                a.append("--details")
            r = project(c).run(*a, **_W)
            self.assertIn(r.returncode, ok, (c, a, r.stderr[-2000:]))
            out[c] = r
        return out


class TestWashSales(_Base):
    def test_default_is_concise(self):
        for c, r in self.run_both("wash-sales").items():
            with self.subTest(country=c):
                assert_concise(self, r.stdout, r.stderr,
                               legend="DENIED: the loss")
                self.assertNotIn("WHAT DENIED MEANS", r.stdout)
                self.assertIn("tjs wash-sales --details", r.stdout)

    def test_details_keeps_the_notes(self):
        r = self.run_both("wash-sales", details=True)
        self.assertIn("WHAT DENIED MEANS", r["canada"].stdout)
        self.assertIn("affiliated person", r["canada"].stdout)
        self.assertIn("IRA, is lost for good", _flat(r["usa"].stdout))


class TestWashRadar(_Base):
    def test_default_is_concise(self):
        for args in (("wash-radar",),
                     ("wash-radar", "--date", "2024-11-25", "--all")):
            for c, r in self.run_both(*args).items():
                with self.subTest(country=c, args=args):
                    assert_concise(self, r.stdout, r.stderr,
                                   legend="STATUS: what a loss sale")
                    self.assertNotIn("DEFINITIONS", r.stdout)

    def test_actions_name_the_status(self):
        r = project("canada").run("wash-radar", "--date", "2024-11-25",
                                  **_W)
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertTrue(any(a.startswith("! VIOLATION: sell all of "
                                         "SAMPA.TO") for a in acts), acts)
        self.assertTrue(any(a.startswith("! BLOCKED: do not buy AAPL.US")
                            for a in acts), acts)

    def test_details_is_the_full_report(self):
        r = self.run_both("wash-radar", "--date", "2024-11-25",
                          details=True)
        for c in r:
            self.assertIn("DEFINITIONS", r[c].stdout)
            self.assertIn("these verdicts cover this project's accounts "
                          "only", _flat(r[c].stdout))
        self.assertIn("Sell 100.0000 shares (taxable 100) by 2024-11-28",
                      _flat(r["canada"].stdout))


if __name__ == "__main__":
    unittest.main()
