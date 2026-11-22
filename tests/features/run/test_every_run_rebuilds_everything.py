"""`tjs run` always rebuilds everything (the owner's decision of 2026-11-17,
PLAN step 12: no switch that can give a wrong result if misused).

After a full run, a run with nothing changed builds every stage again (each
step said, the blend's files written again) and gives the same figures; one
account's export changed moves that account's figures only. `--fast` is
gone: `tjs run --fast` is argparse's unknown option, exit 2, before anything
is read or written, alone or first in a chain.

Formerly tests/test_fast_run_incremental (PLAN step 3's proof that a no-change
`--fast` skipped every stage)."""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj, toml

_YEAR = 2025
_A = ("BUYSELL 2025-01-06 10:00:00 ABC.TO 100 CAD 10 -1000 0\n"
      "BUYSELL 2025-03-03 10:00:00 ABC.TO -40 CAD 12 480 0\n")
_B = ("BUYSELL 2025-02-03 10:00:00 DEF.TO 50 CAD 20 -1000 0\n"
      "BUYSELL 2025-04-01 10:00:00 DEF.TO -50 CAD 18 900 0\n")
# A later sale in the first account: its figures move, the other's do not.
_A_MORE = "BUYSELL 2025-05-05 10:00:00 ABC.TO -60 CAD 11 660 0\n"

# The per-account stages' steps, said by every run.
_STAGE_LINES = ("==> Reading", "==> Sorting", "==> Calculating capital gains")


def _mtimes(root: Path) -> dict:
    """The blend's gains file, its timeline and each account's split."""
    work = root / "work"
    files = {"blend": work / ".blend_gains_wash.json",
             "timeline": work / ".blend_gains_wash.timeline.json",
             "margin": work / "margin_gains_wash.json",
             "other": work / "other_gains_wash.json"}
    for p in files.values():
        assert p.exists(), p
    return {k: p.stat().st_mtime_ns for k, p in files.items()}


def _project(tmp: str) -> Path:
    root = Path(tmp) / "proj"
    for acct, text in (("margin", _A), ("other", _B)):
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "trades.tt").write_text(text)
    (root / "taxjson.toml").write_text(toml(
        year=_YEAR, extra_accounts='[accounts.other]\ntype = "taxable"\n'))
    return root


def _figures(root: Path) -> str:
    out = {}
    for acct in ("margin", "other"):
        doc = json.loads(tj(root, "sum", acct, "--json").stdout)
        out[acct] = doc
    return json.dumps(out, sort_keys=True)


def _tree(root: Path) -> dict:
    """Every file of the project, by path: its bytes."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


class TestEveryRunRebuildsEverything(unittest.TestCase):

    def test_a_run_with_nothing_changed_builds_every_stage_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = tj(root, "run", "--no-input")
            for line in _STAGE_LINES:
                self.assertIn(line, r.stdout, line)
            full = _figures(root)
            before = _mtimes(root)
            records = sorted(p.name for p in (root / "work" / ".deps").glob("*.json"))
            self.assertTrue(records, "a complete run records what it built")

            # Nothing changed: every stage runs again, the same figures.
            r = tj(root, "run", "--no-input")
            for line in _STAGE_LINES:
                self.assertIn(line, r.stdout, line)
            self.assertEqual(r.stdout.count("==> Calculating capital gains"), 2, r.stdout)
            after = _mtimes(root)
            for k in ("blend", "timeline", "margin", "other"):
                self.assertGreater(after[k], before[k], k)
            for acct in ("margin", "other"):
                twin = json.loads((root / "work" / f"{acct}_report.json").read_text())
                self.assertEqual(twin.get("basis"), "wash-adjusted", acct)
            self.assertEqual(_figures(root), full)

            # One account's export changes: its figures move, the other's do not.
            (root / "inputs" / "margin" / "trades.tt").write_text(_A + _A_MORE)
            tj(root, "run", "--no-input")
            moved = _figures(root)
            self.assertNotEqual(moved, full)
            self.assertEqual(json.loads(moved)["other"], json.loads(full)["other"])

    def test_fast_is_an_unknown_option_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            tj(root, "run", "--no-input")
            (root / "inputs" / "margin" / "trades.tt").write_text(_A + _A_MORE)
            before = _tree(root)
            for argv in (("run", "--fast"), ("run", "--fast", "--no-input"),
                         ("run", "--fast", "sum")):
                with self.subTest(argv=argv):
                    r = tj(root, *argv, check=False)
                    self.assertEqual(r.returncode, 2)
                    self.assertEqual(r.stdout, "")
                    self.assertIn("Error: unrecognized arguments: --fast",
                                  str(r.stderr))
                    self.assertEqual(_tree(root), before, "nothing read into work/ or written")
            r = tj(root, "help", "run")
            self.assertNotIn("--fast", r.stdout)


if __name__ == "__main__":
    unittest.main()
