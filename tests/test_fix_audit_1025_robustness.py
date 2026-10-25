"""Workflow audit 2026-10-25, group robustness: a folder taxjson writes
into that is a symlink leaving the project, refused by every command
(robustness#3), and the gains traces growing with a symbol's rows, not
their square (robustness#5). Synthetic projects and data only."""
import argparse
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (a synthetic HOME, offline: tests/_hermetic)
from taxjson.bin import taxjson_run as run
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _cli(root, *args):
    """`taxjson -C ROOT ...` as a child of THIS tree's code."""
    env = dict(os.environ, TAXJSON_WIDTH="0", TAXJSON_OFFLINE="1",
               NO_COLOR="1")
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get(
        "PYTHONPATH", "")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=str(REPO), stdin=subprocess.DEVNULL, capture_output=True,
        text=True, env=env)


def _project(test) -> Path:
    """A fresh `tjs init --single` Canada project in a folder of its
    own (the folder beside it is outside the project)."""
    tmp = Path(tempfile.mkdtemp(prefix="taxjson-test-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    root = tmp / "proj"
    r = _cli(root, "init", "--single", "--country", "canada",
             "--year", "2024")
    test.assertEqual(r.returncode, 0, r.stderr)
    return root


def _link_out(root: Path, name: str) -> Path:
    """Replace the project's folder `name` with a link to an empty
    folder outside the project; the outside folder."""
    outside = root.parent / ("outside_" + name.replace("/", "_"))
    outside.mkdir()
    link = root / name
    if link.is_dir() and not link.is_symlink():
        shutil.rmtree(link)
    link.symlink_to(outside, target_is_directory=True)
    return outside


class TestOutsideLinkEveryCommand(unittest.TestCase):

    def test_robustness_3_every_command_refuses_a_written_folder_linked_out(self):
        """robustness#3 `tjs watch` writes work/.watch_state.json through
        a work/ symlink that leaves the project; about 35 other commands
        also run at exit 0, though the docs say every command stops with
        exit 2: watch, the listings, audit ... now stop with the same
        exit-2 refusal before reading or writing anything, for work/,
        reports/ and an account's inputs/ folder; a link that stays
        inside the project still runs."""
        root = _project(self)
        acct = sorted(d.name for d in (root / "inputs").iterdir()
                      if d.is_dir() and d.name != "slips")[0]
        for name, cmds in (("work", ("watch", "list", "audit",
                                     "wash-radar", "find-missing-history",
                                     "format-map")),
                           ("reports", ("watch",)),
                           (f"inputs/{acct}", ("watch",))):
            saved = root.parent / "saved"
            if (root / name).is_dir():
                (root / name).rename(saved)
            outside = _link_out(root, name)
            for cmd in cmds:
                with self.subTest(folder=name, cmd=cmd):
                    r = _cli(root, cmd)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn("symlinks to outside the project",
                                  r.stderr)
                    self.assertIn(f"{name}/ ->", r.stderr)
                    self.assertEqual(list(outside.iterdir()), [])
            (root / name).unlink()
            if saved.exists():
                saved.rename(root / name)
        # A link inside the project is the project's own folder: kept.
        shutil.rmtree(root / "work", ignore_errors=True)
        (root / "scratch").mkdir()
        (root / "work").symlink_to("scratch", target_is_directory=True)
        shutil.copy(REPO / "examples" / "questrade_demo.csv",
                    root / "inputs" / "margin" / "questrade_demo.csv")
        r = _cli(root, "run")
        self.assertNotIn("symlinks to outside", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _cli(root, "watch")
        self.assertNotIn("symlinks to outside", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((root / "scratch" / ".watch_state.json").exists())

    def test_robustness_3_the_check_covers_every_project_command(self):
        """robustness#3 (the one project-level check): every command of
        the parser but the documented exceptions (help, tax-logic, the
        release tools, init's own check, redact) is refused in a
        project whose work/ leads outside it."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            root.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2024\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (Path(td) / "away").mkdir()
            (root / "work").symlink_to(Path(td) / "away",
                                       target_is_directory=True)
            _p, sub = run._build_parser("taxjson")
            missed = []
            for cmd in sorted(sub.choices):
                args = argparse.Namespace(cmd=cmd, dir=str(root))
                err = io.StringIO()
                try:
                    with contextlib.redirect_stderr(err):
                        run._refuse_outside_links(args)
                except SystemExit as e:
                    self.assertEqual(e.code, 2, cmd)
                    self.assertIn("symlinks to outside the project",
                                  err.getvalue())
                    continue
                missed.append(cmd)
            self.assertEqual(sorted(missed),
                             sorted(run._LINK_CHECK_FREE_CMDS
                                    & set(sub.choices)))


def _one_symbol_year(n):
    """n buys of ZZQ.TO, then n sales of 10 at a higher price (gains
    only): one pool whose history every sale's trace reaches back to."""
    from taxjson.lib.core import TaxTransaction
    rows = []
    for i in range(n):
        d = f"2025-0{1 + i % 5}-{1 + i % 28:02d}"
        px = 10.0 + i / 1000
        rows.append(TaxTransaction(action="BUYSELL", date=d, symbol="ZZQ.TO",
                                   quantity=10.0, price=px,
                                   net_amount=10 * px, currency="CAD",
                                   account="margin"))
    for i in range(n):
        d = f"2025-{7 + i % 5:02d}-{1 + i % 28:02d}"
        px = 20.0 + i / 1000
        rows.append(TaxTransaction(action="BUYSELL", date=d, symbol="ZZQ.TO",
                                   quantity=-10.0, price=px,
                                   net_amount=10 * px, currency="CAD",
                                   account="margin"))
    return rows


def _traces(n, folder):
    """The gains of `_one_symbol_year(n)` with traces, and the size of
    the traces file taxjson-gains writes for them."""
    from taxjson.bin.taxjson_gains import write_traces_file
    from taxjson.lib.core import CanadaTaxRules
    res = CanadaTaxRules().compute_gains(_one_symbol_year(n), trace=True,
                                         detect_wash_sales=False)
    path = Path(folder) / f"n{n}.traces"
    write_traces_file(res, path, country="canada", input_path=None,
                      year="2025", tax_date_basis="settle")
    return res, path


class TestTracesLinear(unittest.TestCase):

    def test_robustness_5_traces_grow_linearly_with_a_symbols_rows(self):
        """robustness#5 The gains engine's audit trace copies a symbol's
        whole pool history into every sale: time, memory and
        work/*.traces grew quadratically. Each sale's trace now shares
        its pool's running trace (it still reads as the whole history,
        for `tjs audit` and taxjson-explain), and the traces file shows
        each pool line once: a block shows the lines since the block
        above of the same pool, headed by where the earlier ones are."""
        from taxjson.lib.trace_format import (SharedTrace,
                                              render_report_trace)
        with tempfile.TemporaryDirectory() as td:
            res1, p1 = _traces(150, td)
            res2, p2 = _traces(300, td)
            s1, s2 = p1.stat().st_size, p2.stat().st_size
            # Twice the rows, about twice the file (a copy per sale made
            # it four times).
            self.assertLess(s2, 2.5 * s1, (s1, s2))
            text = p2.read_text()
            self.assertEqual(text.count("Cost_Added:"), 300)
            self.assertEqual(text.count("Cost_Rmvd:"), 299)
            self.assertEqual(text.count("ACB CALCULATION TRACE"), 300)
            self.assertEqual(text.count("(continued from the sale of "), 299)
            sales = [g for g in res2["transactions"] if g.get("trace")]
            self.assertEqual(len(sales), 300)
            # The record's trace is still the whole pool history: the
            # heading, 300 buys, the 299 earlier sales and its own line.
            last = sales[-1]["trace"]
            self.assertIsInstance(last, SharedTrace)
            self.assertIs(last.base, sales[0]["trace"].base)
            full = list(last)
            self.assertEqual(len(full), 1 + 300 + 299 + 1)
            self.assertTrue(full[0].startswith("# --- ACB CALCULATION TRACE"))
            self.assertEqual(last, full)
            self.assertEqual(last[-1], full[-1])
            self.assertIn("Proceeds:", full[-1])
            report = "\n".join(render_report_trace(sales[-1], 120))
            # (the buys of 2025-01-01: rows 0, 140 and 280)
            self.assertEqual(report.count("2025-01-01"), 3)
            self.assertIn("Pool history (ACB trace)", report)


if __name__ == "__main__":
    unittest.main()
