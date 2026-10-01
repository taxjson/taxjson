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


_KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
           "margin,misc,ledgers\n")
_KR1_SHORT = _KR_HDR + (
    "TXA1,OA1,BTC/CAD,2025-01-15 10:00:00.1234,buy,limit,50000,50000,0,"
    "1.0,,,\nTXA2,OA2,BTC/CAD,2025-06-02 14:30:00.5678,sell,limit,60000,"
    "60000,0,1.0,,,\nTXA3,OA3,ETH/CAD,2025-07-02 14:30:00.5678,sell,"
    "limit,3000,3000,0,1.0,,,\n")
_KR2 = _KR_HDR + ("TXB1,OB1,BTC/CAD,2025-01-16 10:00:00.1234,buy,limit,"
                  "90000,90000,0,1.0,,,\n")
_CRYPTO_CFG = ('[settings]\nyear = 2025\ncountry = "canada"\n'
               'base_currency = "CAD"\nsource_currencies = []\n')


def _crypto_project(root, accounts, tfsa=True):
    """accounts: {name: kraken trades csv}; plus a sheltered tfsa."""
    root = Path(root)
    cfg = _CRYPTO_CFG
    for n, body in accounts.items():
        cfg += f'[accounts.{n}]\ntype = "taxable"\ncrypto = true\n'
        (root / "inputs" / n).mkdir(parents=True, exist_ok=True)
        (root / "inputs" / n / "kr_trades.csv").write_text(body)
    if tfsa:
        cfg += '[accounts.tfsa]\ntype = "sheltered"\n'
        (root / "inputs" / "tfsa").mkdir(parents=True, exist_ok=True)
        (root / "inputs" / "tfsa" / "questrade_2025.csv").write_text(
            _MARGIN_CSV)
    (root / "taxjson.toml").write_text(cfg)
    return root


def _count(path, text):
    return path.read_text().count(text) if path.exists() else 0


class TestDiagnosticsBanner(unittest.TestCase):
    """R1-259 (baseline .sum one run stale), S038-08 / S038-20 (orphaned
    post-pass sidecars), R1-330 (unindented continuation dropped),
    S038-05 (later-year notes), S037-18 (absolute path in crypto .sum)."""

    def test_baseline_sum_is_fresh_and_fixed_notes_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _crypto_project(tmp, {"kr1": _KR1_SHORT})
            rep = root / "reports"
            for _ in range(2):
                r = _run_cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr)
                # Once, not once per run (R1-259).
                self.assertEqual(_count(rep / "kr1.sum", "go short"), 1)
            # S037-18: no absolute path in the report.
            self.assertNotIn(str(Path(tmp).resolve()),
                             (rep / "kr1.sum").read_text())
            self.assertIn("OK: kr1_base.json", (rep / "kr1.sum").read_text())
            # Fix the short and drop the sheltered account: kr1 gets no
            # wash pass any more; its old wash diag must go (S038-20 A).
            (root / "inputs" / "kr1" / "kr_trades.csv").write_text(
                "".join(ln for ln in _KR1_SHORT.splitlines(True)
                        if "ETH" not in ln))
            _crypto_project(root, {"kr1": (root / "inputs" / "kr1" /
                                           "kr_trades.csv").read_text()},
                            tfsa=False)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_count(rep / "kr1.sum", "go short"), 0)
            self.assertFalse((root / "work" /
                              "kr1_gains_wash.json.diag").exists())

    def test_account_moving_into_and_out_of_the_crypto_blend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _crypto_project(tmp, {"kr1": _KR1_SHORT})
            rep, work = root / "reports", root / "work"
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            self.assertGreater(_count(rep / "kr1_wash.sum", "go short"), 0)
            fixed = "".join(ln for ln in _KR1_SHORT.splitlines(True)
                            if "ETH" not in ln)
            # S038-20 B: kr2 joins, kr1 moves to the blended pass.
            _crypto_project(root, {"kr1": fixed, "kr2": _KR2})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("crypto pass", r.stdout)
            self.assertEqual(_count(rep / "kr1_wash.sum", "go short"), 0)
            # S038-08: short again inside the blend, then kr2 leaves.
            _crypto_project(root, {"kr1": _KR1_SHORT, "kr2": _KR2})
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            self.assertTrue((work / "kr1_cryptoblend.diag").exists())
            import shutil
            shutil.rmtree(root / "inputs" / "kr2")
            _crypto_project(root, {"kr1": fixed})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((work / "kr1_cryptoblend.diag").exists())
            self.assertEqual(_count(rep / "kr1_wash.sum", "go short"), 0)
            self.assertEqual(_count(rep / "kr1.sum", "go short"), 0)

    def test_attestation_note_is_kept_whole(self):
        from taxjson.bin.taxjson_run import collect_diagnostics
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        from test_transfer_handling import _tx
        shel = [_tx('TRANSFER', '2025-06-15', 'Q.TO', +100, net=8000.0,
                    account='RRSP'),
                _tx('TRANSFER', '2025-06-15', 'Q.TO', -100, net=8000.0,
                    account='RRSP')]
        main = [_tx('BUYSELL', '2025-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        err = io.StringIO()
        with redirect_stderr(err):
            _drop_self_cancelling_transfers(shel, main_transactions=main)
        note = err.getvalue()
        self.assertIn("declared legs let the cluster net", note)
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "work"
            cache.mkdir()
            (cache / "rrsp_blend.diag").write_text(note)
            got = collect_diagnostics(cache, "rrsp")
        self.assertIn("declared legs let the cluster net", got)
        self.assertIn("record THAT leg as a BUYSELL", got)

    def test_notes_about_later_years_are_set_apart(self):
        from taxjson.bin.taxjson_run import collect_diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(_CONFIG)
            cache = root / "work"
            cache.mkdir()
            (cache / "margin_gains.json.diag").write_text(
                "NOTE: FFN.TO: re-denominated a trade executed 2026-07-02\n"
                "NOTE: ABC.TO: something on 2025-12-20\n"
                "NOTE: XYZ.TO: rebuy 2026-01-15 inside the window\n"
                "warning: no date here\n"
                "error: bad row 2026-08-01\n")
            got = collect_diagnostics(cache, "margin").splitlines()
        head = got.index(next(ln for ln in got
                              if ln.startswith("-- notes about events "
                                               "after 2025")))
        self.assertEqual(got[head + 1:],
                         ["NOTE: FFN.TO: re-denominated a trade executed "
                          "2026-07-02"])
        self.assertIn("NOTE: XYZ.TO: rebuy 2026-01-15 inside the window",
                      got[:head])
        self.assertIn("error: bad row 2026-08-01", got[:head])

    def test_parse_count_echo_matches_spaces_and_transfer_form(self):
        from taxjson.bin.taxjson_run import _PARSE_COUNT_RE
        for ln in ("  kr_trades.csv: 2 tax objects",
                   "  kr_trades 2025 export.csv: 2 tax objects",
                   "  kr_ledgers.csv: 0 tax objects (1 TRANSFER row(s) "
                   "kept aside)"):
            self.assertTrue(_PARSE_COUNT_RE.match(ln), ln)


if __name__ == "__main__":
    unittest.main()
