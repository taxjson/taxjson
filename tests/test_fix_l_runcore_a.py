"""Regression pins for the run-core LOW findings, first half (2026-09
audit, runcore-a).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no FX fetch.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

# Buy 100 @ 10.00 (comm 9.95) -> ACB 1009.95; sell 100 @ 15.00 (comm
# 9.95) -> proceeds 1490.05; gain 480.10.
_MARGIN_CSV = _QT_HEADER + (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,ISHARES COMP,"
    "-100,15.00,1500.00,9.95,1490.05,CAD,55500001,Trades,Individual\n")


def _run_cli(root, *args, env=None):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    pre = ["-C", str(root)] if root is not None else []
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *pre, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _run_mod(mod, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    return subprocess.run([sys.executable, "-m", mod, *args],
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          env=e, stdin=subprocess.DEVNULL)


def _project(tmp, config=_CONFIG, csv=_MARGIN_CSV):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(csv)
    return root


def _with_setting(line):
    return _CONFIG.replace("source_currencies = []\n",
                           f"source_currencies = []\n{line}\n")


class TestSettingsCheckedByEveryReader(unittest.TestCase):
    """R1-152 / R1-261 (fx_cash_gains quoted), R1-183 (option timing
    keys unvalidated outside `run`)."""

    def test_quoted_fx_cash_gains_is_refused(self):
        for cmd in (("run", "--no-input"), ("sum",), ("fx-cash",)):
            with self.subTest(cmd=cmd), \
                    tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp)
                self.assertEqual(_run_cli(root, "run", "--no-input")
                                 .returncode, 0)
                (root / "taxjson.toml").write_text(_with_setting(
                    'fx_cash_gains = "false"'))
                (root / "reports" / "fx_cash.rpt").unlink(missing_ok=True)
                r = _run_cli(root, *cmd)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("fx_cash_gains must be true or false",
                              r.stderr)
                self.assertFalse((root / "reports" / "fx_cash.rpt")
                                 .exists())

    def test_option_timing_typos_are_refused_by_option_boundary(self):
        for line in ('option_premium_timing = "grants"',
                     'option_grant_timing_since = true',
                     'option_grant_timing_since = "2025"',
                     'option_grant_timing_since = 25'):
            with self.subTest(line=line), \
                    tempfile.TemporaryDirectory() as tmp:
                cfg = _CONFIG.replace("option_grant_timing_since = 2025\n",
                                      "")
                root = _project(tmp, cfg)
                self.assertEqual(_run_cli(root, "run", "--no-input")
                                 .returncode, 0)
                (root / "taxjson.toml").write_text(cfg.replace(
                    "source_currencies = []\n",
                    f"source_currencies = []\n{line}\n"))
                for cmd in (("option-boundary",), ("close-year",)):
                    r = _run_cli(root, *cmd)
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    self.assertIn("option_", r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                self.assertFalse(list((root / "filed").glob("*.json"))
                                 if (root / "filed").exists() else [])


class TestValidateConfigOptionKeys(unittest.TestCase):
    """S041-11: validate_config accepts the valid option_* values and
    refuses the invalid ones (the checks were never exercised); the
    [estimate] finiteness guard fires on ONE non-finite value."""

    def _cfg(self, **settings):
        s = {"year": 2025, "country": "canada", "base_currency": "CAD"}
        s.update(settings)
        return {"settings": s, "accounts": {"margin": {"type": "taxable"}}}

    def test_valid_values_pass(self):
        from taxjson.bin.taxjson_run import validate_config
        for kw in ({"option_premium_timing": "grant"},
                   {"option_premium_timing": "close"},
                   {"option_grant_timing_since": 2025},
                   {"option_buyback_loss_superficial": True},
                   {"option_buyback_loss_superficial": False},
                   {"fx_cash_gains": False}):
            with self.subTest(kw=kw):
                validate_config(self._cfg(**kw))

    def test_invalid_values_die(self):
        from taxjson.bin.taxjson_run import validate_config
        for kw in ({"option_premium_timing": "grnat"},
                   {"option_grant_timing_since": "2025"},
                   {"option_grant_timing_since": 25},
                   {"option_grant_timing_since": 20250},
                   {"option_grant_timing_since": True},
                   {"option_buyback_loss_superficial": "yes"},
                   {"fx_cash_gains": "no"}):
            with self.subTest(kw=kw), self.assertRaises(SystemExit):
                validate_config(self._cfg(**kw))

    def test_estimate_inputs_refuse_one_non_finite_value(self):
        from taxjson.bin import taxjson_run as R
        for oi, ol in ((120000.0, float("nan")), (float("inf"), 0.0),
                       (float("nan"), 0.0), (0.0, float("inf"))):
            with self.subTest(oi=oi, ol=ol), \
                    tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp)
                args = argparse.Namespace(other_income=oi, other_losses=ol)
                with self.assertRaises(SystemExit), \
                        redirect_stderr(io.StringIO()):
                    R._estimate_inputs(root, args)


class TestConfigFileShapes(unittest.TestCase):
    """S038-04 (UTF-8 BOM), S040-03 (taxjson.toml a directory)."""

    def test_config_with_bom_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "taxjson.toml").write_bytes(
                b"\xef\xbb\xbf" + _CONFIG.encode())
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_generic_mapping_with_bom_is_read(self):
        from taxjson.lib.brokerages import generic
        src = REPO_ROOT / "examples" / "generic_wealthsimple.toml"
        if not src.exists():
            self.skipTest("examples/ not shipped")
        with tempfile.TemporaryDirectory() as tmp:
            csv = Path(tmp) / "x.csv"
            csv.write_text("a,b\n")
            (Path(tmp) / "x.csv.toml").write_bytes(
                b"\xef\xbb\xbf" + src.read_bytes())
            try:
                generic._load_mapping(csv)
            except ValueError as e:
                self.assertNotIn("bad TOML", str(e))

    def test_config_directory_is_one_line_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            (root / "taxjson.toml").unlink()
            (root / "taxjson.toml").mkdir()
            for cmd in (("run",), ("sum",)):
                r = _run_cli(root, *cmd)
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("cannot read", r.stderr)


class TestDirectoryErrors(unittest.TestCase):
    """R1-263 (init onto a file), S038-17 (work/ or reports/ a file)."""

    def test_init_onto_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "afile"
            f.write_text("")
            for target in (str(f), str(f / "sub")):
                r = _run_cli(None, "init", target, "--country", "canada")
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("cannot create the project directory",
                              r.stderr)
            self.assertEqual(f.read_text(), "")

    def test_run_with_work_or_reports_a_file(self):
        for d in ("work", "reports"):
            with self.subTest(d=d), tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp)
                (root / d).write_text("")
                r = _run_cli(root, "run", "--no-input")
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("not a directory", r.stderr)
                # Refused before any stage: nothing written.
                other = root / ("reports" if d == "work" else "work")
                self.assertFalse(any(other.glob("*.json"))
                                 if other.is_dir() else False)


class TestNumericFlags(unittest.TestCase):
    """R1-333 / S037-14 / R1-242: tolerances and thresholds must be
    finite and non-negative."""

    def test_sanity_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            for bad in ("nan", "-1", "inf", "-inf"):
                r = _run_cli(root, "sanity", "margin", "--tolerance", bad)
                self.assertEqual(r.returncode, 2, (bad, r.stderr))
                self.assertIn("--tolerance", r.stderr)

    def test_reconcile_slips_tolerance(self):
        for bad in ("nan", "-1", "inf"):
            r = _run_mod("taxjson.bin.taxjson_reconcile_slips",
                         "--tolerance", bad, "x.csv", "y.json")
            self.assertEqual(r.returncode, 2, (bad, r.stderr))
            self.assertIn("must be a finite number", r.stderr)

    def test_t1135_thresholds(self):
        for flag in ("--threshold", "--detailed-threshold"):
            for bad in ("nan", "-100000", "0", "inf"):
                r = _run_mod("taxjson.bin.taxjson_t1135", flag, bad, "x")
                self.assertEqual(r.returncode, 2, (flag, bad, r.stderr))
                self.assertIn("must be a finite number", r.stderr)

    def test_watch_threshold(self):
        from taxjson.bin import taxjson_run as R
        from taxjson.bin import taxjson_watch as W
        self.assertEqual(R._watch_threshold(
            argparse.Namespace(threshold=0.0)), 0.0)
        self.assertEqual(R._watch_threshold(
            argparse.Namespace(threshold=None)), 100.0)
        # 0 = any move of a cent or more; float noise is no move.
        self.assertIsNotNone(W.diff_harvest(500.0, 550.0, 0.0))
        self.assertIsNone(W.diff_harvest(500.0, 500.0 + 1e-9, 0.0))
        r = _run_cli(Path("."), "watch", "--threshold", "-1")
        self.assertEqual(r.returncode, 2)

    def test_watch_unusable_state_warns(self):
        from taxjson.bin import taxjson_watch as W
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".watch_state.json"
            for text in ("{ trunc", "[]",
                         json.dumps({"schema_version": 999})):
                p.write_text(text)
                err = io.StringIO()
                with redirect_stderr(err):
                    self.assertIsNone(W.load_state(p))
                self.assertIn("NEW baseline", err.getvalue())
            p.unlink()
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertIsNone(W.load_state(p))
            self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
