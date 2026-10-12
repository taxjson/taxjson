"""`taxjson sum`, essentials first (docs/output-style.md): the legends
before the tables, at most six non-table lines, one `! ` line per
warning, and --details printing every note the default leaves out."""
import unittest

import _style
from _style import assert_concise, project


def _flat(t: str) -> str:
    return " ".join(t.split())


def _return_row(text: str) -> str:
    return next(ln for ln in text.splitlines() if ln.startswith("RETURN "))


class TestSumConcise(unittest.TestCase):

    def _sum(self, country, *extra):
        r = project(country).run("sum", *extra, TAXJSON_WIDTH=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_canada_default_view(self):
        r = self._sum("canada")
        assert_concise(self, r.stdout, r.stderr, budget=6,
                       legend=["REALIZED = NON-OPT",
                               "GAIN = PROCEEDS − COST(ACB) − OUTLAYS"])
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertEqual(len(acts), 2, r.stdout)
        self.assertIn("with no purchase NOT in these totals", acts[0])
        self.assertIn("tjs find-missing-history", acts[0])
        self.assertIn("FX on foreign cash (line 15300)", acts[1])
        self.assertIn("More: tjs sum --details", r.stdout)
        # The warnings are the `! ` lines now, nothing on stderr.
        self.assertEqual(r.stderr.strip(), "")
        # The filed figures stay: the RETURN row, and the denied total
        # (its column dropped at width 100) in the legend.
        self.assertIn("9,242.85", _return_row(r.stdout))
        self.assertIn("superficial losses denied: 1,645.13", r.stdout)

    def test_usa_default_view(self):
        r = self._sum("usa")
        assert_concise(self, r.stdout, r.stderr, budget=6,
                       legend=["REALIZED = NON-OPT", "(h) = (d) − (e) + (g)"])
        self.assertIn("More: tjs sum --details", r.stdout)

    def test_details_restores_the_notes(self):
        for country, phrases, warn in (
                ("canada", ["Rows are rounded to the cent",
                            "Capital gains on T3 (box 21) and T5/T5013 "
                            "(box 18) slips",
                            "registered-account acquisition is lost for "
                            "good",
                            "Per-security rows: `taxjson form-export`",
                            "is STAKING rewards",
                            "FX on foreign cash: NOT RELIABLE for 2024",
                            "Totals include sheltered account(s)"],
                 "No .tt OPENING cost=unknown line"),
                ("usa", ["Column (g) is the code-W wash-sale loss",
                         "Per-sale rows: `taxjson form-export`"],
                 "No .tt OPENING cost=unknown line")):
            with self.subTest(country=country):
                d = self._sum(country, "--details")
                for p in phrases:
                    self.assertIn(p, _flat(d.stdout))
                self.assertIn(warn, _flat(d.stderr))
                # The same figures either way.
                self.assertEqual(_return_row(d.stdout),
                                 _return_row(self._sum(country).stdout))

    def test_captured_keeps_the_messages(self):
        # Captured for a program (width 0) the warning is the full
        # stderr message it always was.
        r = project("canada").run("sum", TAXJSON_WIDTH=0)
        self.assertIn("No .tt OPENING cost=unknown line", r.stderr)


if __name__ == "__main__":
    unittest.main()
