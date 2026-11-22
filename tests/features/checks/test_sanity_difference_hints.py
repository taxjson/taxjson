"""Backlog fix round 2 of 2026-10-29, group `review`: the review and
filing-output commands (audit --year, form-export, the wash review, the
stale-book warning, check-tt, the checklist's run-clean, sanity's
pointer, the inputs/ permission warning).

TJ-0550  audit --year refuses a lock closed under the other country
         before applying its date basis or option timing.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*, SYN*), account names (margin, tfsa), amounts
under 1,000, broker account numbers like 99900001.

Formerly: test_fix_1029_r2_review
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, *, country="canada", year=2025, accounts=None,
             files=None, settings=""):
    """A project at `root`: taxjson.toml, an empty ticker.map and one
    inputs/<account>/ per account; `files` maps "account/name" to its
    text."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    accounts = accounts or {"margin": "taxable"}
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items()))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        acct, _, fn = name.partition("/")
        (root / "inputs" / acct / fn).write_text(text)
    return root


class TestSanityPointsAtTheFixThatWorks(unittest.TestCase):
    """TJ-0423: `sanity` ended every difference with fewer shares in the
    books on "usually means missing history — tjs find-missing-history",
    which then said "No missing-cost-basis issues found" for a position
    bought before the data (MISSING_IN_TAXJSON, or a partial history
    with a positive book position) and for one typed under another
    spelling. Each kind of difference now names the fix that works for
    it: an opening balance, the books' symbol, find-missing-history for
    a position the books hold short.

    Formerly: test_fix_1029_r2_review.TestSanityPointsAtTheFixThatWorks"""

    TT = ("BUYSELL 2025-01-06 10:00:00 ZZA.TO 30 CAD 10 -300 0\n"
          "BUYSELL 2025-01-06 10:00:00 ZZB.TO 75 CAD 10 -750 0\n"
          "BUYSELL 2025-02-03 10:00:00 ZZS.TO -5 CAD 10 50 0\n")

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p",
                         files={"margin/t.tt": cls.TT})
        r = _tjs(cls.p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _sanity(self, holdings):
        h = self.p / "h.toml"
        h.write_text("".join(
            f'[[holding]]\nsymbol = "{s}"\nquantity = {q}\n'
            for s, q in holdings))
        r = _tjs(self.p, "sanity", f"margin={h}")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        return [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]

    def test_a_position_bought_before_the_data_names_an_opening(self):
        acts = self._sanity([("ZZA.TO", 30), ("ZZB.TO", 75),
                             ("ZZQ.TO", 10), ("ZZS.TO", 0)])
        self.assertEqual(len(acts), 2, acts)
        self.assertIn("ZZQ.TO", acts[1])
        self.assertTrue(acts[1].endswith("— tjs opening margin FILE"),
                        acts)
        self.assertNotIn("find-missing-history", acts[1])
        # ZZS.TO: sold with no purchase, short in the books.
        self.assertIn("ZZS.TO", acts[0])
        self.assertTrue(acts[0].endswith("— tjs find-missing-history"),
                        acts)
        self.assertTrue(all(len(a) <= 100 for a in acts), acts)

    def test_a_partial_history_names_an_opening_too(self):
        acts = self._sanity([("ZZA.TO", 40), ("ZZB.TO", 75)])
        self.assertEqual(len(acts), 2, acts)
        self.assertIn("ZZA.TO", acts[1])
        self.assertTrue(acts[1].endswith("— tjs opening margin FILE"),
                        acts)

    def test_the_same_quantity_under_another_symbol_is_a_spelling(self):
        acts = self._sanity([("ZZA.TO", 30), ("ZZB", 75)])
        self.assertEqual(len(acts), 2, acts)
        spell = [a for a in acts if "symbol spelling" in a]
        self.assertEqual(len(spell), 1, acts)
        self.assertIn("ZZB / ZZB.TO", spell[0])
        self.assertTrue(spell[0].endswith("— tjs list"), acts)
        self.assertFalse(any("opening" in a for a in acts), acts)


if __name__ == "__main__":
    unittest.main()
