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
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

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


# ------------------------------------------------------------- TJ-0472
class TestTraceListsEveryBand(unittest.TestCase):
    """TJ-0472: the trace iterated the WITH run's slices only, so a
    band only the base reaches was dropped and the BASE rows did not
    add up to = ORDINARY.

    Formerly: test_fix_1029_r2_usyear.TestTraceListsEveryBand"""

    def test_rows(self):
        from taxjson.cli.estimate import _trace_bracket_rows
        TE.apply_vintage(2025)
        rows = _trace_bracket_rows(TE.US_ORD_BRACKETS, 49000.0, 46000.0)
        self.assertEqual(rows, [
            ("0-11,925 @ 10.00%", 1192.5, 1192.5),
            ("11,925-48,475 @ 12.00%", 4386.0, 4089.0),
            ("48,475-49,000 @ 22.00%", 115.5, 0.0)])
        base = sum(b for _l, b, _w in rows)
        self.assertAlmostEqual(base, TE._bracket_tax(
            49000.0, TE.US_ORD_BRACKETS), places=6)
        # The other way round (the WITH run higher) is as before.
        up = _trace_bracket_rows(TE.US_ORD_BRACKETS, 46000.0, 49000.0)
        self.assertEqual(up[-1], ("48,475-49,000 @ 22.00%", 0.0, 115.5))

    def test_verbose_trace(self):
        root = _us_project(_tmp(self) / "p", 2025, (
            "BUYSELL 2025-02-03 10:00:00 ZZL.US 10 USD 100.00 -1000.00 0\n"
            "BUYSELL 2025-03-03 10:00:00 ZZL.US -10 USD 10.00 100.00 0\n"))
        _ok(_cli(root, "run", "--no-input"))
        r = _ok(_cli(root, "estimate", "--other-income", "64750",
                     "--verbose"))
        self.assertIn("48,475-49,000 @ 22.00% 115.50 0.00", _said(r))
        self.assertIn("11,925-48,475 @ 12.00% 4,386.00 4,341.00",
                      _said(r))


if __name__ == "__main__":
    unittest.main()
