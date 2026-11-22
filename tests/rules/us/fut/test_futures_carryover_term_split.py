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


# ------------------------------------------------------------- TJ-0466
_IB_HEAD = ("Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
            "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,MTM P/L,Code\n")
# An SPX call (a broad-based index option: §1256) closed at a 402.00
# loss, and a stock sold at a 100.00 gain, in 2025.
_IB_1256 = (
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n" + _IB_HEAD
    + 'Trades,Data,Order,Equity and Index Options,USD,SPX 21MAR25 5000 C,'
      '"2025-02-03, 10:00:00",1,5.00,5.00,-500.00,-1.00,501.00,0,0,O\n'
    + 'Trades,Data,Order,Equity and Index Options,USD,SPX 21MAR25 5000 C,'
      '"2025-03-03, 10:00:00",-1,1.00,1.00,100.00,-1.00,-501.00,-402.00,0,'
      'C\n'
    + 'Trades,Data,Order,Stocks,USD,ZZM,"2025-02-03, 10:00:00",10,100.00,'
      '100.00,-1000.00,0,1000.00,0,0,O\n'
    + 'Trades,Data,Order,Stocks,USD,ZZM,"2025-06-02, 10:00:00",-10.0,110.00,'
      '110.00,1100.00,0,-1000.00,100.00,0,C\n')


class TestSection1256CarriedShortTerm(unittest.TestCase):
    """TJ-0466: carryover and the close-year lock carried §1256 P/L as
    all short-term without a word (the estimate names it). Round 3 (the
    owner's decision) splits it as Form 6781 does, 40% short-term and
    60% long-term: test_section_1256_sixty_forty has the rest.

    Formerly: test_fix_1029_r2_usyear.TestSection1256CarriedShortTerm"""

    def _project(self):
        root = _tmp(self) / "p"
        (root / "inputs" / "a").mkdir(parents=True)
        (root / "inputs" / "a" / "a.csv").write_text(_IB_1256)
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "usa"\nyear = 2025\n'
            'base_currency = "USD"\n\n[accounts.a]\ntype = "taxable"\n'
            '\n[carryover]\nclaimed = { 2025 = 0 }\n')
        _ok(_cli(root, "run", "--no-input"))
        return root

    @rule(R.US_FUT_03)
    def test_carryover_and_close_year_say_so(self):
        root = self._project()
        led = json.loads(_ok(_cli(root, "carryover", "--json")).stdout)
        (row,) = led["rows"]
        # 100.00 + 40% of -402.00 short-term, 60% long-term.
        self.assertEqual((row["net_st"], row["st_carryover"]),
                         (-60.8, 60.8))
        self.assertEqual((row["net_lt"], row["lt_carryover"]),
                         (-241.2, 241.2))
        self.assertEqual(row["section_1256"], -402.0)
        full = _said(_ok(_cli(root, "carryover", "--details")))
        self.assertIn("as Form 6781 splits it, whatever the holding "
                      "period", full)
        close = _ok(_cli(root, "close-year", "--yes", "--details"))
        self.assertIn("the carryover takes the 2025 §1256 net, -402.00, "
                      "as Form 6781 splits it", _said(close))
        lock = json.loads((root / "filed" / "2025.json").read_text())
        cl = lock["carryforwards"]["capital_loss"]
        self.assertEqual((cl["st_closing"], cl["lt_closing"],
                          cl["section_1256_gain"]), (60.8, 241.2, -402.0))

    @rule(R.US_FUT_02)
    def test_no_note_without_a_carryover(self):
        root = _us_project(_tmp(self) / "p", 2025, (
            "BUYSELL 2025-02-03 10:00:00 ZZM.US 1 USD 100.00 -100.00 0\n"
            "BUYSELL 2025-06-02 10:00:00 ZZM.US -1 USD 150.00 150.00 0\n"))
        _ok(_cli(root, "run", "--no-input"))
        self.assertNotIn("§1256", _said(_ok(_cli(root, "carryover",
                                                  "--details"))))


if __name__ == "__main__":
    unittest.main()
