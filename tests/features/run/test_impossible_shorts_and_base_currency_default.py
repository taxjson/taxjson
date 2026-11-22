"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.tt_fx_project import _cli, _tt_project
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------ A2-0395 / A2-0137
class TestImpossibleShortsAreLoud(unittest.TestCase):
    """A registered account or a spot-crypto book cannot be short: a sale
    with nothing held is missing history. The run says so on the console
    (ATTENTION) and `run --strict` refuses.

    Formerly: test_fix_a2_pipecmd.TestImpossibleShortsAreLoud"""

    def _run_both(self, root, home, needle):
        r = _cli(root, home, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = r.stdout + r.stderr
        self.assertIn("ATTENTION: short:", out)
        self.assertIn(needle, out)
        r = _cli(root, home, "run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertIn("cannot be short", r.stderr)

    def test_registered_account_short(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 50 -5000 0",
                    "BUYSELL 2026-06-10 10:00:00 XYZ.TO -100 CAD 40 4000 0"]),
                "tfsa": ("sheltered", [
                    "BUYSELL 2026-03-02 10:00:00 XYZ.TO -100 CAD 45 4500 0",
                    "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"]),
            })
            self._run_both(root, home, "XYZ.TO (tfsa)")

    def test_spot_crypto_short(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "kr": ("taxable", [
                    "BUYSELL 2026-03-02 10:00:00 BTC -1 CAD 100000 100000 0"]),
            }, extra_settings="")
            cfg = (root / "taxjson.toml").read_text().replace(
                '[accounts.kr]\ntype = "taxable"\n',
                '[accounts.kr]\ntype = "taxable"\ncrypto = true\n')
            (root / "taxjson.toml").write_text(cfg)
            self._run_both(root, home, "BTC (kr)")

    def test_taxable_short_stays_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-03-02 10:00:00 XYZ.TO -100 CAD 45 4500 0",
                "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"])
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("ATTENTION: short:", r.stdout + r.stderr)


# ---------------------------------------------------------------- A2-0712
class TestRunDefaultsBaseCurrency(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestRunDefaultsBaseCurrency"""

    def test_unset_base_is_the_countrys(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0",
                "BUYSELL 2026-03-05 10:00:00 XYZ.TO -10 CAD 12 120 0"])
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                'base_currency = "CAD"\n', ""))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertIn("20.00", (root / "reports" / "m.sum").read_text())


# ------------------------------------------------------ A2-1230 / A2-1231
class TestSmallRunReaders(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestSmallRunReaders"""

    def test_overlap_note_counts_transfers(self):
        import contextlib, io
        from taxjson.pipeline.stages import _warn_cross_taxable_overlap
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.json", Path(td) / "b.json"
            a.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "symbol": "XYZ.TO"}]}))
            b.write_text(json.dumps({"transactions": [
                {"action": "TRANSFER", "symbol": "XYZ.TO"}]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _warn_cross_taxable_overlap([("a", a), ("b", b)],
                                            {"country": "canada"})
            self.assertIn("XYZ.TO", err.getvalue())

    def test_sanity_refuses_boolean_quantity(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 30 CAD 10 -300 0"])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            h = Path(td) / "h.toml"
            h.write_text('[[holding]]\nsymbol = "XYZ.TO"\nquantity = true\n')
            r = _cli(root, home, "sanity", f"m={h}")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is not a number", r.stderr)


if __name__ == "__main__":
    unittest.main()
