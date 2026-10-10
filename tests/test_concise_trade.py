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


def _no_box_rules(text):
    # The audit's ══ / ── rules are rules (lib/out.classify counts only
    # ASCII rules as such).
    return "\n".join(ln for ln in (text or "").split("\n")
                     if not (ln.strip() and set(ln.strip()) <= set("═─")))


class TestAudit(_Base):
    def test_summary_is_concise(self):
        for c, r in self.run_both("audit", "--summary").items():
            with self.subTest(country=c):
                assert_concise(self, _no_box_rules(r.stdout), r.stderr)
                self.assertIn("pipeline tie-out", r.stdout)
                self.assertNotIn("Totals are unrounded", r.stdout)
                self.assertIn("tjs audit --details", r.stdout)

    def test_details_keep_the_rounding_note(self):
        for args in (("audit", "--summary"),
                     ("audit", "QZQ.US", "--no-color")):
            r = self.run_both(*args, details=True)
            self.assertIn("Schedule 3 rows (form-export",
                          _flat(r["canada"].stdout))
            self.assertIn("Form 8949 rows (form-export",
                          _flat(r["usa"].stdout))


class TestHarvest(_Base):
    def _runs(self, *args):
        import shutil
        from test_style_group_c import _copy, _price_every_holding
        out = {}
        for c in ("canada", "usa"):
            p = _copy(c)
            self.addCleanup(shutil.rmtree, p.root.parent, True)
            _price_every_holding(p)
            out[c] = (p.run("harvest", "--no-ibkr", *args, **_W),
                      p.run("harvest", "--no-ibkr", *args, "--details",
                            **_W))
        return out

    def test_default_is_concise_details_keep_the_notes(self):
        for args in (("--options",), ("--crypto",)):
            for c, (r, d) in self._runs(*args).items():
                with self.subTest(country=c, args=args):
                    self.assertEqual(r.returncode, 0, r.stderr)
                    assert_concise(self, r.stdout, r.stderr,
                                   legend="PRICE: ^ IBKR")
                    self.assertIn("HARVESTABLE LOSSES", r.stdout)
                    self.assertNotIn("COLUMNS", r.stdout)
                    self.assertIn("COLUMNS", d.stdout)
                    self.assertIn("these verdicts cover this project's "
                                  "accounts only", _flat(d.stdout))
                    self.assertIn("Info: pricing ", d.stderr)
                    if c == "usa":
                        self.assertIn("! RISK rows (", r.stdout)


if __name__ == "__main__":
    unittest.main()
