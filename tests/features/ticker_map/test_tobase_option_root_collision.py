"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest

import _paths


class TestToBaseOptionCollision(unittest.TestCase):
    """R1-16: TOBASE's root rename must not pool a US-listed option into a
    Montreal contract with the same code.

    Formerly: test_fix_misc.TestToBaseOptionCollision"""

    BOOK = (
        "BUYSELL 2025-02-03 10:00:00 RY261218C00105000.TO 1.00000000 CAD 3.00000000 300.00000 0.00000\n"
        "BUYSELL 2025-02-04 10:00:00 RY261218C00105000.US 1.00000000 CAD 1.00000000 100.00000 0.00000\n"
        "BUYSELL 2025-03-03 10:00:00 RY261218C00105000.TO -1.00000000 CAD 2.50000000 250.00000 0.00000\n"
        "BUYSELL 2025-02-05 10:00:00 QGX261218C00016000.US 1.00000000 CAD 1.20000000 120.00000 0.00000\n")

    def test_us_option_kept_separate_and_named(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = _paths.REPO
        env = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(repo / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "book.tt").write_text(self.BOOK)
            (root / "ticker.map").write_text("TOBASE RY.US RY.TO\nTOBASE QGX.US QG.TO\n")
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')

            def cli(*args):
                return subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root), *args],
                    capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            r = cli("run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            # merge2's warnings reach the .sum DIAGNOSTICS section.
            run_err = (root / "reports" / "margin.sum").read_text()
            base = json.loads((root / "work" / "margin_base.json").read_text())
            gains = json.loads((root / "work" / "margin_gains.json").read_text())
        syms = {t["symbol"] for t in base["transactions"]}
        self.assertIn("RY261218C00105000.US", syms)             # not pooled into .TO
        self.assertIn("QG261218C00016000.TO", syms)              # no collision: renamed as before
        (d,) = [t for t in gains["transactions"] if "gain" in t]
        self.assertEqual((d["symbol"], round(d["gain"], 2)), ("RY261218C00105000.TO", -50.0))
        self.assertIn("RY261218C00105000.US", run_err)
        self.assertIn("kept separate", run_err)


if __name__ == "__main__":
    unittest.main()
