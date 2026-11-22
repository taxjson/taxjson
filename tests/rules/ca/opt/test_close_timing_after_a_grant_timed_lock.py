"""The 2026-10-29 fix round, group "edgecases": `tjs edge-cases`
and `tjs option-boundary` on synthetic books.

- TJ-0396: a contract on explicit close timing (option_premium_timing =
  "close") whose write year's lock records grant timing, or reports the
  premium, is a double count, flagged ATTENTION like a transition
  contract's (CA-OPT-07).
- TJ-0225: edge-cases names a lock reached through [settings]
  prior_year_record by its path, as option-boundary does, not as
  filed/<year>.json.

Made-up symbols (ZZQ, Q) and amounts only.

Formerly: test_fix_1029_edgecases
"""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import project_layout as PL
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.option_boundary import straddling
import _paths

SRC = _paths.REPO / "src"
OPT = "Q260116C00050000.TO"          # expires 2026-01-16
TODAY = date(2026, 9, 30)
GRANT25 = {2025: {"option_premium_timing": "grant",
                  "option_grant_since": 2025}}


def T(action="BUYSELL", **kw):
    base = {"action": action, "currency": "CAD", "account": "margin",
            "price": 1.0, "time": "10:00:00"}
    base.update(kw)
    base.setdefault("date_settle", base["date"])
    return TaxTransaction(**base)


def write(d="2025-12-15", q=-1, net=100.0, sym=OPT):
    return T(date=d, symbol=sym, quantity=q, net_amount=net)


def buy(d="2026-01-12", q=1, net=-50.0, sym=OPT, price=0.5):
    return T(date=d, symbol=sym, quantity=q, net_amount=net, price=price)


def close_rows(book, **kw):
    """The 2026 project's rows on explicit close timing (its default
    since is its own year)."""
    return straddling(book, 2026, "close", 2026, today=TODAY, **kw)


@rule(R.CA_OPT_07)
class TestExplicitCloseTimingDoubleCount(unittest.TestCase):
    """TJ-0396: 2025 is locked under grant timing (the 100.00 premium
    reported there); the 2026 project on close timing taxes it again.

    Formerly: test_fix_1029_edgecases.TestExplicitCloseTimingDoubleCount"""

    def test_buyback_on_a_grant_lock_is_attention(self):
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing=GRANT25,
                          filed_labels={2025: "previous.json"})
        self.assertTrue(r["attention"], r)
        self.assertTrue(r["action"].startswith(
            "ATTENTION: 2025 is locked (previous.json) and its lock "
            "records grant timing, so the 2025 return reported the 100.00 "
            "premium; this project is on close timing "
            "(option_premium_timing = \"close\") and taxes it again in "
            "2026."), r["action"])
        self.assertIn('option_premium_timing = "grant" with '
                      'option_grant_timing_since = 2025', r["action"])
        # Not the old advice: T1-ADJ 2025 to ADD a premium that 2025
        # already reported.
        self.assertNotIn("2025 was filed: T1-ADJ 2025", r["action"])
        self.assertNotIn("the Act puts", r["action"])

    def test_expiry_and_open_rows_too(self):
        expired = T(date="2026-01-16", symbol=OPT, quantity=1,
                    net_amount=0.0, price=0.0)
        (r,) = close_rows([write(), expired], filed_years={2025},
                          filed_timing=GRANT25)
        self.assertEqual(r["close_kind"], "expiry")
        self.assertTrue(r["attention"], r)
        self.assertIn("taxes it again in 2026", r["action"])
        later = "Q270115C00050000.TO"
        (r,) = close_rows([write(sym=later)], filed_years={2025},
                          filed_timing=GRANT25)
        self.assertEqual(r["close_kind"], "open")
        self.assertTrue(r["attention"], r)
        self.assertIn("taxes it again when it closes", r["action"])

    def test_the_locks_reported_premium_is_evidence(self):
        # A lock that predates the timing record, but whose
        # dispositions report the write's premium as a 2025 gain.
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"note": "old lock"}},
                          filed_premiums={2025: {(OPT, "2025-12-15"):
                                                 100.0}})
        self.assertTrue(r["attention"], r)
        self.assertIn("its lock reports the 100.00 premium as a 2025 "
                      "gain", r["action"])

    def test_known_dispositions_without_the_premium_are_not_a_double(self):
        # The lock records grant timing, but its return's dispositions
        # leave the premium out: nothing was reported twice.
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing=GRANT25,
                          filed_premiums={2025: {}})
        self.assertFalse(r["attention"], r)

    def test_a_close_timing_lock_keeps_the_amendment(self):
        # 2025 filed on close timing: the premium is in no 2025 return,
        # and the Act puts it there (as before the fix).
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"option_premium_timing":
                                               "close"}})
        self.assertFalse(r["attention"], r)
        self.assertTrue(r["action"].endswith("; 2025 was filed: "
                                             "T1-ADJ 2025"), r["action"])

    def test_a_close_timing_lock_wins_over_a_same_day_gain(self):
        # The lock records close timing; a gain on the write date (a
        # same-day close of another lot) is not this premium.
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"option_premium_timing":
                                               "close"}},
                          filed_premiums={2025: {(OPT, "2025-12-15"):
                                                 40.0}})
        self.assertFalse(r["attention"], r)

    def test_no_lock_is_unchanged(self):
        (r,) = close_rows([write(), buy()])
        self.assertFalse(r["attention"])
        self.assertIn("enable grant timing with option_grant_timing_since "
                      "= 2025 to correct", r["action"])

    def test_the_locks_own_since_is_the_hint(self):
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"option_premium_timing":
                                               "grant",
                                               "option_grant_since": 2023}})
        self.assertIn("option_grant_timing_since = 2023", r["action"])

    def test_assignment_is_the_amendment(self):
        book = [write(),
                T(action="ASSIGN", date="2026-01-16", symbol=OPT,
                  quantity=1, net_amount=0.0, price=0.0),
                T(action="ASSIGN", date="2026-01-16",
                  date_settle="2026-01-19", symbol="Q.TO", quantity=-100,
                  net_amount=5000.0, price=50.0)]
        (r,) = close_rows(book, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual(r["close_kind"], "assignment")
        self.assertTrue(r["action"].startswith(
            "T1-ADJ 2025: remove the 100.00 premium reported for 2025 "
            "(s.49(4))"), r["action"])

    def test_transition_wording_is_unchanged(self):
        # Grant mode, written before since: the transition's own text.
        (r,) = straddling([write(), buy()], 2026, "grant", 2026,
                          filed_years={2025}, filed_timing=GRANT25,
                          today=TODAY)
        self.assertTrue(r["attention"])
        self.assertIn("this project keeps the contract on close timing "
                      "(option_grant_timing_since = 2026)", r["action"])


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


@rule(R.CA_OPT_07)
class TestPriorRecordLockCLI(unittest.TestCase):
    """The audit's reproduction (TJ-0408 for TJ-0225, TJ-0396): a 2025
    project on grant timing writes a call for 100 and is closed; its
    lock is kept as previous.json, which the 2026 project (the same
    folder, year bumped) names as its prior_year_record; the call is
    bought back for 50 in 2026.

    Formerly: test_fix_1029_edgecases.TestPriorRecordLockCLI"""

    TT = ("BUYSELL 2025-12-15 10:00:00 ZZQ260116C00010000.TO -1 CAD 1 "
          "100 0\n"
          "BUYSELL 2026-01-12 10:00:00 ZZQ260116C00010000.TO 1 CAD 0.5 "
          "50 0\n")

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory(prefix="tj1029-")
        p = cls.root = Path(cls._td.name) / "p"
        (p / "inputs" / "margin").mkdir(parents=True)
        (p / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (p / "inputs" / "margin" / "m.tt").write_text(cls.TT)
        for args in (("run", "--no-input"), ("close-year", "--yes")):
            r = _cli(p, *args)
            assert r.returncode == 0, r.stdout + r.stderr
        (p / "filed" / "2025.json").rename(p / "previous.json")
        cls._set(year=2026, prior_year_record="previous.json")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @classmethod
    def _set(cls, **keys):
        toml = cls.root / "taxjson.toml"
        text = toml.read_text()
        for k, v in keys.items():
            text = PL.set_key_text(text, ("settings", k), v)
        toml.write_text(text)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr

    def _both(self):
        r = _cli(self.root, "edge-cases", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        (edge,) = json.loads(r.stdout)["year_boundary"]["written_options"]
        r = _cli(self.root, "option-boundary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        ob = json.loads(r.stdout)
        (row,) = ob["rows"]
        return edge, row, ob

    def test_a_default_project_and_then_explicit_close_timing(self):
        # TJ-0225: grant timing by default (since = 2026): the 2025
        # contract is a transition contract taxed twice; edge-cases
        # named a filed/2025.json that does not exist.
        self._set(option_premium_timing="grant")
        edge, row, ob = self._both()
        self.assertTrue(edge["attention"])
        self.assertIn("2025 is locked (previous.json)", edge["action"])
        self.assertNotIn("filed/2025.json", edge["action"])
        self.assertEqual(edge["action"], row["action"])
        # TJ-0396: option_premium_timing = "close" said attention 0 and
        # "2025 was filed: T1-ADJ 2025" though 2025 reported the 100.
        self._set(option_premium_timing="close")
        edge, row, ob = self._both()
        self.assertEqual(ob["attention"], 1, ob)
        self.assertTrue(row["attention"])
        self.assertTrue(edge["attention"])
        self.assertEqual(edge["action"], row["action"])
        self.assertIn("2025 is locked (previous.json)", row["action"])
        self.assertIn('option_premium_timing = "close"', row["action"])
        self.assertNotIn("2025 was filed: T1-ADJ 2025", row["action"])
        # The default text says the row needs attention under it; the
        # details give the action.
        r = _cli(self.root, "edge-cases")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("needs attention: tjs option-boundary --details",
                      r.stdout)
        r = _cli(self.root, "edge-cases", "--details")
        self.assertIn("=> ATTENTION: 2025 is locked (previous.json)",
                      " ".join(r.stdout.split()))


if __name__ == "__main__":
    unittest.main()
