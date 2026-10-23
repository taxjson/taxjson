"""Input-file order never changes a figure (the 2026-10 critique's
permutation property; audit #104/#106 were order-dependent). Each
project is run, every input file is renamed so the order the run reads
them in reverses, the books are rebuilt, and `sum --json`'s filing
figures and per-account totals must be equal. Synthetic books only."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _qa_project import ENV, tj

INPUT_SUFFIXES = (".csv", ".tt")


def _figures(root):
    d = json.loads(tj(root, "sum", "--json").stdout)
    return {"filing": d.get("filing"), "accounts": d.get("accounts"),
            "totals": d.get("totals")}


def _reverse_order(inputs: Path) -> None:
    """Rename each account folder's input files so their name order
    (the order a run reads them in) is reversed."""
    for folder in sorted(p for p in inputs.iterdir() if p.is_dir()):
        files = sorted(f for f in folder.iterdir()
                       if f.is_file() and f.suffix.lower() in INPUT_SUFFIXES)
        n = len(files)
        for i, f in enumerate(files):
            f.rename(f.with_name(f"{n - i:02d}_{f.name}"))


def _rebuild(root):
    for d in ("work", "reports"):
        shutil.rmtree(root / d, ignore_errors=True)
    tj(root, "run", "--no-input")


class TestFileOrder(unittest.TestCase):
    def _demo(self, tmp):
        top = Path(tmp) / "demo"
        r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                            "init", "--demo", str(top)], capture_output=True,
                           text=True, env=ENV, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        return top, top / "2024"

    def _check(self, root, inputs):
        _rebuild(root)
        before = _figures(root)
        _reverse_order(inputs)
        _rebuild(root)
        self.assertEqual(_figures(root), before)

    def test_demo_projects_brokers(self):
        with tempfile.TemporaryDirectory() as tmp:
            top, root = self._demo(tmp)
            self._check(root, top / "inputs")

    def test_overlapping_export_read_once_whatever_the_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            top, root = self._demo(tmp)
            qt = top / "inputs" / "questrade" / "questrade_demo.csv"
            shutil.copy(qt, qt.with_name("questrade_demo_copy.csv"))
            self._check(root, top / "inputs")

    def test_statements_that_disagree(self):
        """Two statements of one IB account with another merger value
        (audit #104 through the real extractor): the same result in
        either order."""
        import test_fix_corp as C
        t = C.TestEventInTwoBrokerAccounts()

        def file_(v):
            ca = (C._ib_ca(f'{t.D} (DEF, DEF CORP, CA0000000002)', 500,
                           1000 * v, when='2026-06-11, 20:25:00')
                  + C._ib_ca(f'{t.D} (ABC, ABC CORP, CA0000000001)', -1000,
                             -1000 * v, when='2026-06-11, 20:25:00'))
            return (C._IB_HEAD.format(acct='U5550001')         # pii-ok
                    + C._IB_TRADES
                    + C._ib_buy('ABC', 1000, 10, when='2026-03-03, 10:00:00')
                    + C._IB_CA + ca)
        with tempfile.TemporaryDirectory() as tmp:
            root = C._project(tmp, {'ib_a.csv': file_(12),
                                    'ib_b.csv': file_(13)})
            tj(root, "run", "--no-input", check=False)
            for p in C._pending(root):
                tj(root, "elect", "margin", "--set",
                   f"{p['event_id']}=taxable_disposition")
            self._check(root, root / "inputs")


if __name__ == "__main__":
    unittest.main()
