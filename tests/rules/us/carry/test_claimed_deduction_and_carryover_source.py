"""The 2026-10-29 fix round 2, US year-end group: the experimental US
engine's estimate, carryover and close-year lock.

- TJ-0509 the US estimate caps the tax at the tax on all of the taxable
  income at the ordinary rates (Pub 505 Worksheet 2-7 lines 39-40): a
  long-term gain stacked between the 0% capital-gain band's end and the
  12% ordinary band's end paid 15%;
- TJ-0472 `estimate --verbose` lists every band either run reaches, so
  the BASE column adds up to its ORDINARY total when an investment loss
  moves the WITH run down a bracket;
- TJ-0464 close-year's US lock counts the year's [carryover] claimed
  entry as the deduction used (it carried the whole loss when the
  estimate had no other income), says when neither it nor [estimate]
  other_income was set; the next year's ledger shows a locked year's
  offset as its lock's, and handoff flags a claimed entry that differs;
- TJ-0554 a US close-year record keeps each lot held at Dec 31 (its
  acquisition date and basis), and handoff compares them: an opening with
  the same units and total basis but the lots' costs swapped passed,
  and the next year's FIFO sale took another basis;
- TJ-0555 handoff against a record that is not the year before the
  project's (a 2024 lock in a 2026 project) is a problem of the record
  itself (exit 1, in the JSON), not a clean verdict with a note;
- TJ-0466 carryover and close-year say how much §1256 P/L they carry
  (round 3: split 40% short-term / 60% long-term, as Form 6781 does);
- TJ-0470 with no close-year lock before the year and no carryover
  entered, the US estimate says its carryovers in are 0 and why, and
  points to `tjs carryover`;
- TJ-0473 no T1A carry-back or `amt` in US output, and no IRA in the
  Canadian checklist;
- TJ-0468 docs/filing.md has a US variant of its Canada-only steps
  instead of "the same steps apply with the form names swapped".

Synthetic data only (made-up tickers and accounts; amounts under a
thousand where a figure is written with a thousands separator).

Formerly: test_fix_1029_r2_usyear
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import carryforward as CF
from taxjson.lib import tax_estimate as TE
from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))


def _env(**extra):
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _cli(root, *args):
    """`tjs -C ROOT ARGS` as a child of THIS tree's code."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *map(str, args)])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(), timeout=900)


def _ok(r):
    if r.returncode != 0:
        raise AssertionError(f"rc {r.returncode}\n{r.stdout[-3000:]}\n"
                             f"{r.stderr[-3000:]}")
    return r


def _said(r):
    return " ".join((r.stdout + r.stderr).split())


def _tmp(test):
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return Path(td.name)


def _us_toml(year, tail="", inputs_dir=None):
    return ('[settings]\ncountry = "usa"\n'
            f'year = {year}\nbase_currency = "USD"\n'
            + (f'inputs_dir = "{inputs_dir}"\n' if inputs_dir else "")
            + '\n[accounts.margin]\ntype = "taxable"\n' + tail)


def _us_project(root, year, book, tail=""):
    """A one-folder US project of the account `margin` (a .tt book)."""
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "trades.tt").write_text(book)
    (root / "taxjson.toml").write_text(_us_toml(year, tail))
    return root


# ------------------------------------------------------------- TJ-0509
def _usa(**kw):
    args = dict(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, year=2025)
    args.update(kw)
    return TE.estimate_usa(**args)


# ------------------------------------------------------------- TJ-0464
# 2024: a short-term loss of 900; 2025: a short-term gain of 50.
_BOOK = ("BUYSELL 2024-02-01 10:00:00 ZZL.US 10 USD 100.00 -1000.00 0\n"
         "BUYSELL 2024-09-03 10:00:00 ZZL.US -10 USD 10.00 100.00 0\n"
         "BUYSELL 2025-02-03 10:00:00 ZZM.US 1 USD 100.00 -100.00 0\n"
         "BUYSELL 2025-06-02 10:00:00 ZZM.US -1 USD 150.00 150.00 0\n")


class TestClaimedDeduction(unittest.TestCase):
    """TJ-0464: close-year's US lock took the carryover from the
    estimate, which counted the deduction as used only as far as its
    own taxable income absorbed it — none with no other income — and
    never read [carryover] claimed.

    Formerly: test_fix_1029_r2_usyear.TestClaimedDeduction"""

    @rule(R.US_EST_CARRY_TI)
    def test_estimate_counts_the_claimed_deduction(self):
        r = _usa(st=-900.0, deduction_claimed=400.0)
        self.assertEqual(r["ordinary_offset"], 900.0)
        self.assertEqual(r["offset_used_for_carryover"], 400.0)
        self.assertEqual(r["offset_used_from"], "claimed")
        self.assertEqual(r["st_carryover_after"], 500.0)
        self.assertEqual(r["losses_unused"], 500.0)
        # More than the loss allows is capped at it; none given: the
        # taxable income decides (0 here: no other income).
        self.assertEqual(_usa(st=-900.0, deduction_claimed=990.0)
                         ["st_carryover_after"], 0.0)
        plain = _usa(st=-900.0)
        self.assertEqual(plain["st_carryover_after"], 900.0)
        self.assertEqual(plain["offset_used_from"], "taxable_income")
        # The tax itself is the estimate's: unchanged by the entry.
        self.assertEqual(r["estimated_tax"], plain["estimated_tax"])

    def _closed(self, tail):
        top = _tmp(self) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(_BOOK)
        (top / "2024").mkdir()
        (top / "2024" / "taxjson.toml").write_text(
            _us_toml(2024, tail, "../inputs"))
        _ok(_cli(top / "2024", "run", "--no-input"))
        close = _ok(_cli(top / "2024", "close-year", "--yes"))
        lock = json.loads((top / "2024" / "filed" / "2024.json")
                          .read_text())
        return top, close, lock["carryforwards"]["capital_loss"]

    @rule(R.US_CARRY_01, R.US_CARRY_04, R.US_CARRY_03)
    def test_lock_ledger_and_handoff(self):
        top, close, cl = self._closed(
            "\n[carryover]\nclaimed = { 2024 = 400.00 }\n")
        self.assertEqual((cl["st_closing"], cl["lt_closing"]),
                         (500.0, 0.0))
        self.assertEqual(cl["deduction_used"], 400.0)
        self.assertEqual(cl["deduction_from"], "[carryover] claimed")
        self.assertIn("Capital loss carryover into 2025: short-term "
                      "500.00, long-term 0.00", _said(close))
        self.assertIn("Deducted in 2024: 400.00 ([carryover] claimed)",
                      _said(close))
        _ok(_cli(top / "2024", "new-year", "2025"))
        y5 = top / "2025"
        _ok(_cli(y5, "run", "--no-input"))
        rows = json.loads(_ok(_cli(y5, "carryover", "--json")).stdout)[
            "rows"]
        r24, r25 = rows
        self.assertEqual((r24["ordinary_income_offset"],
                          r24["st_carryover"]), (400.0, 500.0))
        self.assertNotIn("claimed_offset", r24)
        self.assertEqual((r25["ordinary_income_offset"],
                          r25["st_carryover"]), (450.0, 0.0))
        est = json.loads(_ok(_cli(y5, "estimate", "--json")).stdout)[
            "estimate"]
        self.assertEqual(est["carryover_short_term"], 500.0)
        h = _ok(_cli(y5, "handoff", "--json"))
        self.assertEqual(json.loads(h.stdout)["carry"], [])
        # A claimed entry for 2024 that differs from what the record
        # counted is named, and the ledger says so.
        t = (y5 / "taxjson.toml").read_text()
        (y5 / "taxjson.toml").write_text(
            t.replace("claimed = { 2024 = 400.0 }",
                      "claimed = { 2024 = 300.0 }"))
        h = _cli(y5, "handoff", "--json")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        (item,) = json.loads(h.stdout)["carry"]
        self.assertEqual((item["what"], item["here"], item["record"]),
                         ("[carryover] claimed 2024", 300.0, 400.0))
        led = _ok(_cli(y5, "carryover"))
        self.assertIn("2024: claimed 300.00, its lock deducted 400.00",
                      _said(led))

    @rule(R.US_CARRY_01)
    def test_no_claim_no_income_is_said(self):
        _top, close, cl = self._closed("")
        self.assertEqual((cl["st_closing"], cl["deduction_used"],
                          cl["deduction_available"]), (900.0, 0.0, 900.0))
        self.assertEqual(cl["deduction_from"],
                         "the estimate's taxable income")
        self.assertIn("no [estimate] other_income and no [carryover] "
                      "claimed entry for 2024: 0.00 of the 900.00 "
                      "deduction against ordinary income is counted as "
                      "used", _said(close))
        # With other income the estimate's taxable income absorbs it:
        # no note.
        _top, close, cl = self._closed(
            "\n[estimate]\nother_income = 80000\n")
        self.assertEqual((cl["st_closing"], cl["deduction_used"]),
                         (0.0, 900.0))
        self.assertNotIn("counted as used", _said(close))

    @rule(R.US_CARRY_03, R.US_CARRY_04)
    def test_older_lock_without_the_deduction(self):
        """A lock an older release wrote (no deduction_used) carried the
        whole loss: the ledger's row shows the offset its balance
        implies (0), and handoff names the claimed entry."""
        top, _close, _cl = self._closed("")
        p = top / "2024" / "filed" / "2024.json"
        doc = json.loads(p.read_text())
        for k in ("deduction_used", "deduction_available",
                  "deduction_from", "other_income",
                  "other_income_entered"):
            doc["carryforwards"]["capital_loss"].pop(k)
        p.write_text(json.dumps(doc, indent=2))
        self.assertEqual(CF.us_deduction_used(doc), (0.0, 900.0))
        _ok(_cli(top / "2024", "new-year", "2025"))
        y5 = top / "2025"
        t = (y5 / "taxjson.toml").read_text()
        (y5 / "taxjson.toml").write_text(
            t + "\n[carryover]\nclaimed = { 2024 = 400.00 }\n")
        _ok(_cli(y5, "run", "--no-input"))
        r24 = json.loads(_ok(_cli(y5, "carryover", "--json")).stdout)[
            "rows"][0]
        self.assertEqual((r24["ordinary_income_offset"],
                          r24["claimed_offset"], r24["st_carryover"]),
                         (0.0, 400.0, 900.0))
        h = _cli(y5, "handoff")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        self.assertIn("[carryover] claimed records 400.00 deducted "
                      "against ordinary income on the 2024 return",
                      _said(h))


# ------------------------------------------------------------- TJ-0470
class TestEstimateSaysWhereNoCarryoverCameFrom(unittest.TestCase):
    """TJ-0470: with no lock and nothing entered the US estimate used a
    0 carryover in silence, while `tjs carryover` on the same books
    showed one carried in.

    Formerly: test_fix_1029_r2_usyear.TestEstimateSaysWhereNoCarryoverCameFrom"""

    def test_no_lock_no_entry(self):
        # 2024: a short-term loss of 3,400 (400 left after the 3,000
        # deduction); 2025: a gain of 50.
        root = _us_project(_tmp(self) / "p", 2025, (
            "BUYSELL 2024-02-01 10:00:00 ZZL.US 10 USD 400.00 -4000.00 0\n"
            "BUYSELL 2024-09-03 10:00:00 ZZL.US -10 USD 60.00 600.00 0\n"
            + _BOOK.split("\n", 2)[2]))
        _ok(_cli(root, "run", "--no-input"))
        led = json.loads(_ok(_cli(root, "carryover", "--json")).stdout)
        self.assertEqual(led["rows"][0]["st_carryover"], 400.0)
        out = _said(_ok(_cli(root, "estimate")))
        self.assertIn("Capital loss carryovers in: 0.00 — no close-year "
                      "lock before 2025 and none entered: see `tjs "
                      "carryover`", out)
        est = json.loads(_ok(_cli(root, "estimate", "--json")).stdout)[
            "estimate"]
        self.assertIsNone(est["carry_sources"]["other_losses"])
        # Entered: its source, as before.
        out = _said(_ok(_cli(root, "estimate", "--other-losses", "40")))
        self.assertIn("Capital loss carryovers in: short-term 40.00, "
                      "long-term 0.00 — from --other-losses", out)
        self.assertNotIn("none entered", out)


if __name__ == "__main__":
    unittest.main()
