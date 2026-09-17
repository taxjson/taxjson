"""Tests for taxjson-lint-crosslistings."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _buy(symbol, qty, desc=None):
    d = dict(action="BUYSELL", date="2026-01-05", time="09:30:00",
             symbol=symbol, quantity=qty, net_amount=abs(qty) * 10.0,
             price=10.0, currency="CAD", account="x")
    if desc:
        d["description"] = desc
    return d


def _run(taxable, sheltered, mapfile=None, strict=False):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.json"
        s = Path(tmp) / "s.json"
        t.write_text(json.dumps({"transactions": taxable}))
        s.write_text(json.dumps({"transactions": sheltered}))
        cmd = [sys.executable, "-m", "taxjson.bin.taxjson_lint_crosslistings",
               "--taxable", str(t), "--sheltered", str(s)]
        if mapfile:
            m = Path(tmp) / "ticker.map"
            m.write_text(mapfile)
            cmd += ["--map", str(m)]
        if strict:
            cmd.append("--strict")
        return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


class TestLintCrosslistings(unittest.TestCase):
    def test_cdr_is_ok(self):
        r = _run([_buy("AVGO.US", 10)],
                 [_buy("AVGO.TO", 5, "BROADCOM INC CDR CIBC DEPOSITARY RECEIPTS")])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[OK] AVGO", r.stdout)

    def test_tobase_present_but_not_consolidated_warns(self):
        # Both listings still appear AND there is a TOBASE entry → WARN.
        r = _run([], [_buy("SII.US", 54), _buy("SII.TO", 0)],
                 mapfile="TOBASE SII.US SII.TO\n")
        self.assertIn("[WARN] SII", r.stdout)

    def test_unmapped_same_ticker_is_review(self):
        # Sheltered-only so the marker is a plain [REVIEW] (no taxable ‼).
        r = _run([], [_buy("CMG.TO", 3), _buy("CMG.US", 2)])
        self.assertIn("[REVIEW] CMG", r.stdout)

    def test_taxable_exposure_flagged_and_strict_exits_nonzero(self):
        # A REVIEW root with TAXABLE exposure is actionable (‼) and --strict
        # makes it a failure.
        r = _run([_buy("FOO.TO", 100)], [_buy("FOO.US", 50)], strict=True)
        self.assertIn("FOO", r.stdout)
        self.assertIn("‼", r.stdout)          # the ‼ marker
        self.assertNotEqual(r.returncode, 0)

    def test_sheltered_only_review_not_strict_failure(self):
        # No taxable exposure → not actionable → --strict still exits 0.
        r = _run([], [_buy("BAR.TO", 100), _buy("BAR.US", 50)], strict=True)
        self.assertIn("[REVIEW] BAR", r.stdout)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_crosslisting_is_clean(self):
        r = _run([_buy("AAPL.US", 10)], [_buy("ENB.TO", 100)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No cross-listed roots", r.stdout)

    def test_options_ignored(self):
        # An option on .US plus equity on .TO must NOT count as a cross-listing.
        r = _run([_buy("XYZ250321C00100000.US", 1)], [_buy("XYZ.TO", 100)])
        self.assertIn("No cross-listed roots", r.stdout)


if __name__ == "__main__":
    unittest.main()
