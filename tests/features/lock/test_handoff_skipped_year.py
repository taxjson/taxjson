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


# ------------------------------------------------------------- TJ-0554
def _lots_book(first, second, sale_year=None):
    """Two lots of ZZQ.US bought in 2024 at `first` then `second`; a
    sale of one unit at 30.00 in `sale_year`."""
    book = (f"BUYSELL 2024-03-01 10:00:00 ZZQ.US 1 USD {first:.2f} "
            f"-{first:.2f} 0\n"
            f"BUYSELL 2024-06-03 10:00:00 ZZQ.US 1 USD {second:.2f} "
            f"-{second:.2f} 0\n")
    if sale_year:
        book += (f"BUYSELL {sale_year}-06-02 10:00:00 ZZQ.US -1 USD 30.00 "
                 f"30.00 0\n")
    return book


# ------------------------------------------------------------- TJ-0555
class TestHandoffSkippedYear(unittest.TestCase):
    """TJ-0555: a 2026 project checked against the 2024 record exited 0
    with problems 0; only a stderr note named the years.

    Formerly: test_fix_1029_r2_usyear.TestHandoffSkippedYear"""

    def test_a_record_two_years_back_is_a_problem(self):
        top = _tmp(self)
        p24 = _us_project(top / "y24", 2024, _lots_book(10.0, 20.0))
        _ok(_cli(p24, "run", "--no-input"))
        _ok(_cli(p24, "close-year", "--yes", "--force"))
        rec = p24 / "filed" / "2024.json"
        p26 = _us_project(top / "y26", 2026, _lots_book(10.0, 20.0, 2026))
        _ok(_cli(p26, "run", "--no-input"))
        h = _cli(p26, "handoff", "--prior", rec, "--json")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        rep = json.loads(h.stdout)
        self.assertEqual(rep["positions"], [])
        (item,) = rep["partial"]
        self.assertEqual((item["record_year"], item["project_year"]),
                         (2024, 2026))
        self.assertIn("2025", item["why"])
        self.assertEqual(rep["problems"], 1)
        text = _cli(p26, "handoff", "--prior", rec)
        self.assertEqual(text.returncode, 1)
        self.assertIn("HAND-OFF CHECK — 2024 (closed) into 2026 (this "
                      "project)", _said(text))
        self.assertIn("- the record is for 2024, this project's year is "
                      "2026", _said(text))
        self.assertIn("1 problem(s).", _said(text))
        # The checklist's handoff step names it (not "a partial-year
        # snapshot").
        from datetime import date
        from taxjson.lib import checklist as cl
        ctx = cl.Ctx(root=p26, cfg={"settings": {
            "year": 2026, "prior_year_record": str(rec)}}, year=2026,
            today=date(2027, 3, 1),
            run_sub=lambda argv, timeout=900: (1, h.stdout, ""))
        r = cl.d_handoff(ctx)
        self.assertEqual(r.status, "attention")
        self.assertEqual(r.detail, "the record is for 2024, not 2025 — "
                                   "`tjs handoff`")


if __name__ == "__main__":
    unittest.main()
