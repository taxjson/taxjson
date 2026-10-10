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
local_timezone = "America/Toronto"
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
_CRYPTO_CFG = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
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
            self.assertIn("across crypto accounts (kr1, kr2)", r.stdout)
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


def _qt_row(d, s, act, q, p, sym="XEI.TO", acct="55500001", comm=0):
    g = abs(q) * p
    net = -g - comm if act == "Buy" else g - comm
    return (f"{d} 09:30:00 AM,{s} 12:00:00 AM,{act},{sym},DESC,{q},"
            f"{p:.2f},{g:.2f},{comm},{net:.2f},CAD,{acct},Trades,"
            f"Individual\n")


# A wash sale in 2024 AND one in 2025.
_TWO_YEAR_WASH = _QT_HEADER + "".join([
    _qt_row("2024-03-01", "2024-03-04", "Buy", 100, 10),
    _qt_row("2024-06-03", "2024-06-04", "Sell", -100, 8),
    _qt_row("2024-06-10", "2024-06-11", "Buy", 100, 8),
    _qt_row("2025-03-03", "2025-03-04", "Sell", -100, 6),
    _qt_row("2025-03-10", "2025-03-11", "Buy", 100, 6),
    _qt_row("2025-09-02", "2025-09-03", "Sell", -100, 7)])


class TestViewsSayWhatTheySkip(unittest.TestCase):
    """R1-223, R1-224, R1-264, R1-274, R1-284, R1-287, R1-288,
    S039-07, S039-17, S039-20/21/22, S040-06."""

    def test_unreadable_base_book_keeps_its_withholding_fallback(self):
        from taxjson.bin.taxjson_run import _actual_withholding
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "x_base.json").write_text(json.dumps({
                "transactions": [{"action": "TAX", "date": "2025-03-01",
                                  "net_amount": 150.0}]}))
            for state in ("missing", "truncated"):
                if state == "truncated":
                    (cache / "y_base.json").write_text("{ trunc")
                err = io.StringIO()
                with redirect_stderr(err):
                    got = _actual_withholding(
                        cache, ["x", "y"], 2025,
                        foreign_by_account={"x": 1000.0, "y": 2000.0})
                self.assertAlmostEqual(got, 450.0, places=2, msg=state)
                self.assertIn("y_base.json", err.getvalue())

    def test_estimate_trace_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "estimate", "--verbose", "--province", "ON")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("@ 14.5%)", r.stdout)
            self.assertNotIn("@ 14%)", r.stdout)

    def test_carryover_is_quiet_about_a_skipped_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + '[accounts.crypto]\ntype = '
                            '"taxable"\ncrypto = true\n')
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "carryover")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("no base file", r.stderr)

    def test_gains_names_the_crypto_account_it_leaves_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + '[accounts.kr]\ntype = '
                            '"taxable"\ncrypto = true\n')
            (root / "inputs" / "kr").mkdir()
            (root / "inputs" / "kr" / "kr_trades.csv").write_text(_KR2)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "gains")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("crypto account 'kr' is not shown", r.stderr)

    def test_wash_sales_explain_is_year_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=_TWO_YEAR_WASH)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "wash-sales", "--explain")
            self.assertEqual(r.returncode, 0, r.stderr)
            # One block per denial, headed "SYMBOL — DATE — ACCOUNT"
            # (the report layout, docs/output-style.md).
            heads = [ln for ln in r.stdout.splitlines()
                     if ln.startswith("XEI.TO — ")]
            self.assertEqual(len(heads), 1, heads)
            self.assertIn("2025-03-03", heads[0])

    def test_missing_currency_is_not_labelled_cad(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        line = _tx_display_line({"action": "BUYSELL", "date": "2026-03-02",
                                 "symbol": "AAA", "quantity": 100,
                                 "price": 10, "net_amount": -1002.0,
                                 "fee": 2.0})
        self.assertNotIn(" CAD ", line)
        self.assertIn(" ? ", line)

    def test_merged_audit_json_keeps_failure_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + '[accounts.kr]\ntype = '
                            '"taxable"\ncrypto = true\n')
            (root / "inputs" / "kr").mkdir()
            (root / "inputs" / "kr" / "kr_trades.csv").write_text(
                _KR_HDR + "TXB1,OB1,BTC/CAD,2025-01-16 10:00:00.1234,buy,"
                "limit,90000,90000,0,1.0,,,\nTXB2,OB2,BTC/CAD,2025-02-16 "
                "10:00:00.1234,sell,limit,95000,95000,0,1.0,,,\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            # Drop margin's disposition from its saved books.
            for f in (root / "work").glob("margin_gains*.json"):
                d = json.loads(f.read_text())
                d["transactions"] = [t for t in d["transactions"]
                                     if "gain" not in t]
                f.write_text(json.dumps(d))
            r = _run_cli(root, "audit", "--json")
            self.assertNotEqual(r.returncode, 0)
            doc = json.loads(r.stdout)
            self.assertTrue(doc["failed"])
            self.assertTrue(any("MISSING" in f for f in
                                doc.get("reconciliation_failures", [])),
                            doc.get("reconciliation_failures"))

    def test_all_accounts_views_read_a_sorted_only_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + '[accounts.kr]\ntype = '
                            '"taxable"\ncrypto = true\n')
            work = root / "work"
            work.mkdir()
            row = {"action": "BUYSELL", "date": "2025-02-01",
                   "time": "10:00:00", "symbol": "BTC", "quantity": 1.0,
                   "price": 100.0, "net_amount": -100.0,
                   "currency": "CAD", "account": "kr"}
            (work / "kr_sorted.json").write_text(json.dumps(
                {"transactions": [row]}))
            (work / "margin_raw.json").write_text(json.dumps(
                {"transactions": [dict(row, symbol="XEI.TO",
                                       account="margin")]}))
            r = _run_cli(root, "trades", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("BTC", r.stdout)

    def test_non_utf8_work_file_is_a_one_line_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            for f in ("margin_gains_wash.json", "margin_gains.json",
                      "margin_raw.json", "margin_base.json"):
                p = root / "work" / f
                if p.exists():
                    p.write_bytes(b'{"transactions": [], "x": "caf\xe9"}')
            for cmd in ("trades", "winners", "gains", "transfers", "list"):
                r = _run_cli(root, cmd)
                self.assertNotIn("Traceback", r.stderr, cmd)

    def test_transfers_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + '[accounts.rrsp]\ntype = '
                            '"sheltered"\ntransfers = true\n')
            work = root / "work"
            work.mkdir()
            (work / "margin_kraken_transfers.json").write_text(json.dumps({
                "metadata": {"kind": "transfer_sidecar",
                             "account": "margin"},
                "transactions": [{"action": "TRANSFER", "date":
                                  "2025-03-01", "symbol": "TAO",
                                  "quantity": -0.1, "fee": 0.002,
                                  "description": "withdrawal",
                                  "currency": "USD"}]}))
            (work / "rrsp_base.json").write_text("{ trunc")
            r = _run_cli(root, "transfers", "margn")
            self.assertEqual(r.returncode, 1)
            self.assertIn("margn", r.stderr)
            r = _run_cli(root, "transfers", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = json.loads(r.stdout)["transfers"]
            self.assertEqual(rows[0]["fee"], 0.002)
            self.assertIn("rrsp_base.json", r.stderr)
            r = _run_cli(root, "transfers")
            self.assertIn("FEE", r.stdout)
            self.assertIn("0.002", r.stdout)

    def test_leaps_sum_counts_phantom_basis_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            work = root / "work"
            work.mkdir()
            sym = "GHI260618C00050000.TO"
            (work / "margin_raw.json").write_text(json.dumps({
                "transactions": [
                    {"action": "BUYSELL", "date": "2025-01-10",
                     "time": "10:00:00", "symbol": sym, "quantity": 1,
                     "price": 5.0, "net_amount": -500.0,
                     "currency": "CAD"},
                    {"action": "BUYSELL", "date": "2025-06-10",
                     "time": "10:00:00", "symbol": sym, "quantity": -2,
                     "price": 8.0, "net_amount": 1600.0,
                     "currency": "CAD"}]}))
            (work / "margin_gains.json").write_text(json.dumps({
                "transactions": [],
                "manual_reporting_required": [
                    {"symbol": sym, "date": "2025-06-10",
                     "date_settle": "2025-06-11", "qty": 2,
                     "proceeds": 1600.0, "cost": 500.0,
                     "gain": 1100.0}]}))
            r = _run_cli(root, "leaps-sum")
            self.assertIn("MANUAL reporting", r.stderr)


_TT_A = ("BUYSELL 2025-02-03 09:30:00 ABC.TO 50 CAD 10.0 -500.0 0.0\n")
_TT_B = ("BUYSELL 2025-02-04 09:30:00 DEF.TO 70 CAD 10.0 -700.0 0.0\n")


class TestFastCache(unittest.TestCase):
    """S037-22 (raw holdings after a .tt is deleted), S039-03 (code key
    by content), S037-15 (.tt stem ending in a reserved suffix)."""

    def _tt_project(self, tmp, files):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(_CONFIG)
        d = root / "inputs" / "margin"
        d.mkdir(parents=True, exist_ok=True)
        for n, body in files.items():
            (d / n).write_text(body)
        return root

    def test_deleted_tt_leaves_the_raw_holdings_under_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tt_project(tmp, {"a.tt": _TT_A, "b.tt": _TT_B})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            (root / "inputs" / "margin" / "b.tt").unlink()
            r = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            h = (root / "reports" / "margin_holdings.toml").read_text()
            self.assertIn("ABC.TO", h)
            self.assertNotIn("DEF.TO", h)

    def test_code_fingerprint_forces_a_full_rebuild(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            stamp = root / "work" / R._CODE_STAMP
            self.assertEqual(stamp.read_text().strip(),
                             R._package_fingerprint())
            r = _run_cli(root, "run", "--fast", "--no-input")
            self.assertNotIn("code changed", r.stdout)
            # A different stamp = different code: --fast rebuilds.
            stamp.write_text("0" * 64 + "\n")
            r = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("code changed", r.stdout)
            self.assertIn("==> Reading 1 file", r.stdout)

    def test_tt_stem_with_reserved_suffix_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "inputs" / "margin" / "msft_gains.tt").write_text(
                _TT_A)
            r = _run_cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("msft_gains.tt", r.stderr)
            self.assertIn("msft-gains.tt", r.stderr)


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def _row(action, date, symbol, **kw):
    d = {"action": action, "date": date, "time": "10:00:00",
         "symbol": symbol, "currency": "CAD"}
    d.update(kw)
    return d


_MIXED_CFG = _CONFIG + ('[accounts.rrsp]\ntype = "sheltered"\n'
                        '[accounts.kr]\ntype = "taxable"\n'
                        'crypto = true\n')


class TestReportLabelsAndTotals(unittest.TestCase):
    """S039-19 / S041-07 (PIL footer), S041-09 / S041-10 / S041-12
    (sheltered scope), S037-11 (footing), S040-14 (winners signs),
    S024-01 / S040-12 (grant WRITE is not a close), S028-04 (ccd.rpt
    orientation), S023-11 (staking is not a dividend)."""

    def _views_project(self, tmp):
        root = _project(tmp, _MIXED_CFG)
        work = root / "work"
        _write(work / "margin_raw.json", {"transactions": [
            _row("DIVIDEND", "2025-05-01", "AAA.TO", gross_amount=30.0,
                 net_amount=30.0),
            _row("DIVIDEND_IN_LIEU", "2025-05-15", "AAA.TO",
                 gross_amount=10.0, net_amount=10.0),
            _row("ADJUST", "2025-06-01", "AAA.TO", net_amount=-5.0,
                 type="roc"),
            _row("BUYSELL", "2025-07-01", "AAA.TO", quantity=-10,
                 price=10.0, net_amount=100.0)]})
        _write(work / "rrsp_raw.json", {"transactions": [
            _row("DIVIDEND_IN_LIEU", "2025-05-15", "BBB.TO",
                 gross_amount=900.0, net_amount=900.0),
            _row("ADJUST", "2025-06-01", "BBB.TO", net_amount=-500.0,
                 type="roc"),
            _row("BUYSELL", "2025-07-01", "BBB.TO", quantity=-10,
                 price=500.0, net_amount=5000.0)]})
        _write(work / "kr_filled.json", {"transactions": [
            _row("DIVIDEND", "2025-05-03", "ETH", gross_amount=7.0,
                 net_amount=7.0)]})
        return root

    def test_tx_view_footers_keep_pil_and_staking_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._views_project(tmp)
            ev = _run_cli(root, "events")
            self.assertEqual(ev.returncode, 0, ev.stderr)
            self.assertRegex(ev.stdout, r"TOTAL DIVIDEND:\s+30\.00 CAD")
            self.assertRegex(ev.stdout,
                             r"TOTAL DIVIDEND IN LIEU:\s+910\.00 CAD")
            self.assertRegex(ev.stdout, r"TOTAL STAKING.*7\.00 CAD")
            dil = _run_cli(root, "dil")
            self.assertNotRegex(dil.stdout, r"TOTAL DIVIDEND:")
            self.assertIn("TOTAL DIVIDEND IN LIEU", dil.stdout)
            j = json.loads(_run_cli(root, "events", "--json").stdout)
            self.assertEqual(j["totals"]["dividend"], {"CAD": 30.0})
            self.assertEqual(j["totals"]["dividend_in_lieu"],
                             {"CAD": 910.0})
            self.assertEqual(j["totals"]["staking"], {"CAD": 7.0})

    def test_divs_sum_labels_staking(self):
        # main's S031-04 design: kept in the total, named as staking.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._views_project(tmp)
            r = _run_cli(root, "divs-sum")
            self.assertIn("crypto staking rewards", r.stdout)
            j = json.loads(_run_cli(root, "divs-sum", "--json").stdout)
            self.assertEqual(j["crypto_staking"], {"CAD": 7.0})

    def test_sheltered_scope_is_split_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._views_project(tmp)
            dil = _run_cli(root, "dil-sum")
            self.assertIn("ORDINARY INCOME: 10.00 CAD", dil.stdout)
            self.assertIn("SHELTERED (rrsp", dil.stdout)
            j = json.loads(_run_cli(root, "dil-sum", "--json").stdout)
            self.assertEqual(j["totals_ordinary"], {"CAD": 10.0})
            self.assertEqual(j["totals_sheltered"], {"CAD": 900.0})
            roc = _run_cli(root, "roc-sum")
            self.assertIn("TAXABLE (compare with T3 box 42): 5.00 CAD",
                          roc.stdout)
            j = json.loads(_run_cli(root, "roc-sum", "--json").stdout)
            self.assertEqual(j["totals_taxable"], {"CAD": 5.0})
            ts = _run_cli(root, "trades-sum")
            self.assertIn("sold in taxable accounts only: 100.00 CAD",
                          ts.stdout)
            j = json.loads(_run_cli(root, "trades-sum", "--json").stdout)
            self.assertEqual(j["sold_taxable"], {"CAD": 100.0})

    def test_totals_foot_to_the_printed_rows(self):
        from taxjson.bin.taxjson_run import _foot, _foot_by_currency
        rows = [0.005, 0.005, 0.005]       # each prints 0.01 (or 0.00)
        self.assertEqual(_foot(rows), round(sum(round(v, 2)
                                                 for v in rows), 2))
        self.assertEqual(_foot([1.004, 2.004]), 3.0)
        self.assertEqual(_foot_by_currency([("CAD", 1.004),
                                            ("CAD", 2.004),
                                            ("USD", 0.336)]),
                         {"CAD": 3.0, "USD": 0.34})
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _write(root / "work" / "margin_raw.json", {"transactions": [
                _row("DIVIDEND", "2025-05-01", f"T{i}.TO",
                     gross_amount=1.004, net_amount=1.004)
                for i in range(3)]})
            r = _run_cli(root, "divs-sum")
            self.assertIn("TOTAL DIVIDEND: 3.00 CAD", r.stdout)

    def _options_project(self, tmp):
        root = _project(tmp, csv=_QT_HEADER)
        (root / "inputs" / "margin" / "opts.tt").write_text(
            "BUYSELL 2025-01-10 09:30:00 MIXCO.TO 100 CAD 10.0 -1000.0 0.0\n"
            "BUYSELL 2025-02-02 09:30:00 ABC261218C00050000.TO -5 CAD 4.0 "
            "1995.0 5.0\n"
            "BUYSELL 2025-03-05 09:30:00 ABC261218C00050000.TO 5 CAD 2.0 "
            "-1005.0 5.0\n"
            "BUYSELL 2025-04-01 09:30:00 MIXCO.TO -100 CAD 12.0 1200.0 0.0\n"
            "BUYSELL 2025-05-01 09:30:00 MIXCO.TO -100 CAD 50.0 5000.0 0.0\n"
            "BUYSELL 2025-06-01 09:30:00 MIXCO.TO 100 CAD 40.0 -4000.0 0.0\n")
        (root / "inputs" / "margin" / "questrade_2025.csv").unlink()
        r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        return root

    def test_option_views_under_grant_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._options_project(tmp)
            w = json.loads(_run_cli(root, "winners", "--json").stdout)
            rows = {r["ticker"]: r for r in w["rows"]}
            # S040-14: real-world orientation (form-export's).
            self.assertAlmostEqual(rows["MIXCO.TO"]["proceeds"], 6200.0)
            self.assertAlmostEqual(rows["MIXCO.TO"]["cost"], 5000.0)
            self.assertAlmostEqual(rows["ABC.TO"]["proceeds"], 1995.0)
            self.assertAlmostEqual(rows["ABC.TO"]["cost"], 1005.0)
            # S040-12: the WRITE record is not a close.
            self.assertEqual(rows["ABC.TO"]["closes"], 1)
            c = json.loads(_run_cli(root, "ccd-sum", "--json").stdout)
            self.assertEqual(c["rows"][0]["contracts"], 1)
            self.assertEqual(c["rows"][0]["qty"], 5)
            self.assertAlmostEqual(c["rows"][0]["proceeds"], 1995.0)
            # S028-04: ccd.rpt shows premium and buy-back positive.
            rpt = (root / "reports" / "ccd.rpt").read_text()
            self.assertIn("PREMIUM", rpt)
            self.assertNotIn("-1,995.00", rpt)
            self.assertNotIn("-1,005.00   ", rpt.split("GAIN")[0])
            line = next(ln for ln in rpt.splitlines()
                        if ln.startswith("2025-03-05"))
            self.assertIn(" 1,005.00 ", line)

    def test_sum_gains_does_not_count_write_records(self):
        from taxjson.bin.taxjson_sum_gains import summarize_gains
        write = {"symbol": "ABC261218C00050000.TO", "date": "2025-02-02",
                 "qty": 5, "cost": -1995.0, "proceeds": 0.0,
                 "gain": 1995.0, "days_held": 0, "currency": "CAD",
                 "direction": "SHORT", "grant": True}
        close = dict(write, date="2025-03-05", cost=0.0, proceeds=-1005.0,
                     gain=-1005.0, days_held=31, grant=False)
        res = summarize_gains({"transactions": [write, close],
                               "summary": {"year": 2025}})
        st = res["ticker_stats"]["ABC.TO"]["CAD"]
        self.assertEqual(st["trade_count"], 1)
        self.assertAlmostEqual(st["opt"], 990.0)

    def test_staking_labels_in_sum_and_dot_sum(self):
        from taxjson.bin.taxjson_sum_gains import (format_report,
                                                    summarize_gains)
        res = summarize_gains({"transactions": [
            {"symbol": "ETH", "action": "DIVIDEND", "dividend": 7.0,
             "date": "2025-05-03", "currency": "CAD"}],
            "summary": {"year": 2025}})
        self.assertIn("TOTAL STAKING REWARDS",
                      format_report(res, no_color=True, staking=True))
        self.assertIn("TOTAL DIVIDENDS / STAKING",
                      format_report(res, no_color=True))
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _MIXED_CFG)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            _write(root / "work" / "kr_gains.json", {
                "transactions": [{"symbol": "ETH", "action": "DIVIDEND",
                                  "dividend": 7.0, "date": "2025-05-03",
                                  "currency": "CAD"}],
                "summary": {"year": 2025}})
            r = _run_cli(root, "sum")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("is STAKING rewards", r.stdout)
            j = json.loads(_run_cli(root, "sum", "--json").stdout)
            kr = [a for a in j["accounts"] if a["account"] == "kr"]
            self.assertTrue(kr and kr[0].get("dividend_is_staking"))


class TestScanSanityFetch(unittest.TestCase):
    """R1-243, S042-04 (scan), R1-113 / R1-334 / R1-351 (sanity),
    R1-353 (Questrade auth hint)."""

    def test_account_plan_is_a_whole_token(self):
        from taxjson.bin.taxjson_run import _account_plan
        tx = {"type": "taxable"}
        self.assertEqual(_account_plan("admiral", tx), "taxable")
        self.assertEqual(_account_plan("spiral", tx), "taxable")
        self.assertEqual(_account_plan("rrsp2", {"type": "sheltered"}),
                         "rrsp")
        self.assertEqual(_account_plan("my-tfsa", {"type": "sheltered"}),
                         "tfsa")
        self.assertEqual(_account_plan("x", {"type": "taxable",
                                             "plan": "bogus"}), "taxable")
        self.assertEqual(_account_plan("x", {"type": "sheltered",
                                             "plan": " TFSA "}), "tfsa")

    def test_unknown_plan_value_warns(self):
        from taxjson.bin.taxjson_run import validate_config
        w = validate_config({"settings": {"year": 2025,
                                          "country": "canada"},
                             "accounts": {"m": {"type": "taxable",
                                                "plan": "non-registered"}}})
        self.assertTrue(any("plan" in x and "non-registered" in x
                            for x in w), w)

    def test_option_listing_counts_for_map_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            (root / "reports" / "margin_holdings.toml").write_text(
                '[[holding]]\nsymbol = "AAQ.TO"\nquantity = 100\n'
                '[[holding]]\nsymbol = "AAQ261218C00040000.US"\n'
                'quantity = 2\n')
            r = _run_cli(root, "ticker-map", "--suggest")
            self.assertIn("To verify", r.stdout)
            self.assertIn("TOBASE AAQ.US AAQ.TO   or   DISTINCT AAQ.US "
                          "AAQ.TO", r.stdout)

    def _sanity_project(self, tmp):
        root = _project(tmp, _CONFIG + '[accounts.kr]\ntype = '
                        '"taxable"\ncrypto = true\n')
        (root / "inputs" / "kr").mkdir()
        (root / "inputs" / "kr" / "kr_trades.csv").write_text(_KR2)
        self.assertEqual(_run_cli(root, "run", "--no-input").returncode, 0)
        return root

    def test_sanity_pairs_venue_suffixed_crypto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._sanity_project(tmp)
            f = Path(tmp) / "U5550001_holdings.toml"  # pii-ok
            f.write_text('[meta]\naccount = "U5550001"\n'  # pii-ok
                         '[[holding]]\nsymbol = "BTC.KR"\n'
                         'quantity = 1.0\nasset_type = "crypto"\n')
            r = _run_cli(root, "sanity", f"kr={f}")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("MISSING", r.stdout)
            # R1-351: the id in the file name and label is masked.
            self.assertNotIn("U5550001", r.stdout)  # pii-ok
            self.assertIn("U5***_holdings.toml", r.stdout)


def _zzz_short_csv():
    return _QT_HEADER + "".join([
        _qt_row("2025-02-03", "2025-02-04", "Buy", 50, 10, sym="ZZZ.TO"),
        _qt_row("2025-05-05", "2025-05-06", "Sell", -150, 12,
                sym="ZZZ.TO")])


class TestMoreViews(unittest.TestCase):
    """R1-339, S031-05, S038-11, S040-02, S041-00, S039-18,
    OWNER-FEES-SIGN."""

    def test_missing_history_lists_phantom_covered_pairs_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=_zzz_short_csv())
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                "OPENING 2025-02-02 ZZZ.TO 100 cost=unknown\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "find-missing-history")
            self.assertIn("COVERED by missing_history.tt", r.stdout)
            self.assertNotIn("AFFECTS 2025", r.stdout)
            self.assertNotIn("--suggest-missing-history", r.stdout)
            from taxjson.lib import checklist
            self.assertIn("COVERED", checklist.d_missing_history.__code__
                          .co_consts.__repr__())

    def test_fees_json_without_conversion_has_no_mixed_total(self):
        from taxjson.bin.taxjson_fees import aggregate, render_json
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "m_ib.json"
            f.write_text(json.dumps({
                "metadata": {"source_brokerage": "ib"},
                "transactions": [
                    {"id": "1", "action": "BUYSELL", "date": "2025-01-02",
                     "symbol": "A.US", "quantity": 1, "fee": 10.0,
                     "currency": "USD", "net_amount": -100},
                    {"id": "2", "action": "BUYSELL", "date": "2025-01-03",
                     "symbol": "B.TO", "quantity": 1, "fee": 5.0,
                     "currency": "CAD", "net_amount": -100}]}))
            b, g, info = aggregate([f], year="2025", since=None,
                                   to_curr=None, history={},
                                   default_rate=1.35, by_account=False)
            doc = json.loads(render_json(b, g, info, to_curr=None,
                                         by_account=False, year="2025"))
            self.assertIsNone(doc["total"]["total"])
            self.assertEqual(set(doc["total"]["by_currency"]),
                             {"CAD", "USD"})

    def test_fees_report_skips_deleted_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv = _QT_HEADER + "".join([
                _qt_row("2025-01-15", "2025-01-16", "Buy", 100, 10),
                _qt_row("2025-06-20", "2025-06-23", "Sell", -100, 15),
                _qt_row("2025-02-15", "2025-02-16", "Buy", 10, 1,
                        sym="JUNK.TO", comm=50),
                _qt_row("2025-03-15", "2025-03-16", "Sell", -10, 1,
                        sym="JUNK.TO", comm=50)])
            root = _project(tmp, csv=csv)
            (root / "ticker.map").write_text("DELETE JUNK.TO\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            self.assertNotIn("100.00", (root / "reports" / "fees.rpt")
                             .read_text())
            r = _run_cli(root, "fees-sum", "--json")
            j = json.loads(r.stdout)
            self.assertLess(j["total"]["total"] or 0.0, 50.0)

    def test_renamed_leaps_keeps_its_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=_QT_HEADER)
            (root / "inputs" / "margin" / "questrade_2025.csv").unlink()
            (root / "inputs" / "margin" / "x.tt").write_text(
                "BUYSELL 2024-02-01 10:00:00 XYZ260116C00050000.TO 1 CAD "
                "5.00 509.95 9.95\n"
                "BUYSELL 2024-02-01 10:00:00 ABC260116C00050000.TO 1 CAD "
                "5.00 509.95 9.95\n"
                "SPLIT 2024-08-01 10:00:00 XYZ260116C00050000.TO "
                "XYZ1260116C00050000.TO 1\n"
                "BUYSELL 2025-03-03 10:00:00 XYZ1260116C00050000.TO -1 CAD "
                "8.00 790.05 9.95\n"
                "BUYSELL 2025-03-03 10:00:00 ABC260116C00050000.TO -1 CAD "
                "8.00 790.05 9.95\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            j = json.loads(_run_cli(root, "leaps-sum", "--json").stdout)
            self.assertEqual({x["contract"] for x in j["rows"]},
                             {"ABC260116C00050000.TO",
                              "XYZ1260116C00050000.TO"})
            self.assertAlmostEqual(j["total_gain"], 560.20, places=2)

    def test_instalments_name_assumed_zero_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG.replace(
                "option_grant_timing_since = 2025\n",
                'option_grant_timing_since = 2025\nprovince = "ON"\n')
                + '[instalments]\nbasis = "current_year"\n')
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "instalments")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("other income", r.stdout.lower())
            self.assertIn("! Assumed 0: other income", r.stdout)
            # The sentences in full: --details (Essentials first).
            self.assertIn("assumed 0", _run_cli(root, "instalments",
                                                "--details").stdout)
            j = json.loads(_run_cli(root, "instalments", "--json").stdout)
            self.assertEqual(len(j["assumed_zero"]), 2)

    def test_tax_year_trade_views_use_the_settlement_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv = _QT_HEADER + "".join([
                _qt_row("2025-03-02", "2025-03-03", "Buy", 100, 10,
                        sym="GAIN.TO"),
                _qt_row("2025-12-31", "2026-01-02", "Sell", -100, 20,
                        sym="GAIN.TO")])
            root = _project(tmp, _CONFIG.replace("year = 2025",
                                                 "year = 2026"), csv=csv)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            j = json.loads(_run_cli(root, "trades-sum", "--json").stdout)
            self.assertAlmostEqual(j["totals"]["CAD"]["sold"], 2000.0)
            r = _run_cli(root, "trades")
            self.assertIn("GAIN.TO", r.stdout)
            r = _run_cli(root, "trades", "2025")
            self.assertNotIn("-100", r.stdout)

    def test_questrade_fee_rows_are_positive_charges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=_MARGIN_CSV
                            + "2025-07-02 09:30:00 AM,2025-07-02 12:00:00 "
                            "AM,FCH,,PLUS PLAN FEE,0,0.00,0.00,0.00,-9.96,"
                            "CAD,55500001,Fees and rebates,Individual\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            j = json.loads(_run_cli(root, "fees", "--json").stdout)
            fee = [x for x in j["rows"] if x["action"] == "FEE"]
            self.assertEqual([x["fee"] for x in fee], [9.96])


class TestDefaultsAndTotalsPinned(unittest.TestCase):
    """S038-06: a Canada project without tax_date runs on SETTLEMENT
    dates at every consumer; S040-22: the printed totals of winners,
    dil-sum, roc-sum, trades-sum, list and wash-sales over 2+ rows."""

    def test_canada_tax_date_defaults_to_settle_everywhere(self):
        from taxjson.bin import taxjson_filed, taxjson_run as R
        from taxjson.lib.country import default_tax_date
        self.assertEqual(default_tax_date("canada"), "settle")
        self.assertEqual(taxjson_filed._tax_date({}, "canada"), "settle")
        self.assertEqual(R._tax_date({"country": "canada"}), "settle")
        self.assertEqual(R._tax_date_basis({"country": "canada"}),
                         "settle")
        # End to end: traded 2024-12-31, settled 2025-01-02 -> 2025.
        with tempfile.TemporaryDirectory() as tmp:
            csv = _QT_HEADER + "".join([
                _qt_row("2024-06-03", "2024-06-04", "Buy", 100, 10),
                _qt_row("2024-12-31", "2025-01-02", "Sell", -100, 15)])
            root = _project(tmp, csv=csv)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            j = json.loads(_run_cli(root, "sum", "--json").stdout)
            self.assertAlmostEqual(j["filing"]["totals"]["gain"], 500.0)
            a = json.loads(_run_cli(root, "audit", "--json").stdout)
            self.assertEqual(len(a["events"]), 1)
            r = _run_mod("taxjson.bin.taxjson_explain", "--country",
                         "canada", "--year", "2025",
                         str(root / "work" / "margin_base.json"))
            self.assertIn("XEI.TO", r.stdout)
            r = _run_mod("taxjson.bin.taxjson_audit", "--country",
                         "canada", "--year", "2025", "--json", "--base",
                         str(root / "work" / "margin_base.json"))
            self.assertEqual(len(json.loads(r.stdout)["events"]), 1)

    def test_report_totals_over_several_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv = _QT_HEADER + "".join([
                _qt_row("2025-01-06", "2025-01-07", "Buy", 100, 10,
                        sym="AAA.TO"),
                _qt_row("2025-02-03", "2025-02-04", "Sell", -100, 15,
                        sym="AAA.TO"),
                _qt_row("2025-01-06", "2025-01-07", "Buy", 100, 20,
                        sym="BBB.TO"),
                _qt_row("2025-02-03", "2025-02-04", "Sell", -100, 21,
                        sym="BBB.TO"),
                # two wash sales: CCC and DDD rebought and held
                _qt_row("2025-01-06", "2025-01-07", "Buy", 100, 10,
                        sym="CCC.TO"),
                _qt_row("2025-03-03", "2025-03-04", "Sell", -100, 8,
                        sym="CCC.TO"),
                _qt_row("2025-03-10", "2025-03-11", "Buy", 100, 8,
                        sym="CCC.TO"),
                _qt_row("2025-01-06", "2025-01-07", "Buy", 50, 30,
                        sym="DDD.TO"),
                _qt_row("2025-04-01", "2025-04-02", "Sell", -50, 27,
                        sym="DDD.TO"),
                _qt_row("2025-04-08", "2025-04-09", "Buy", 50, 27,
                        sym="DDD.TO")])
            root = _project(tmp, csv=csv)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            w = _run_cli(root, "winners").stdout
            self.assertIn("TOTAL REALIZED GAIN: 600.00 CAD", w)
            ws = _run_cli(root, "wash-sales").stdout
            self.assertIn("2 superficial loss(es); 350.00 CAD of losses denied",
                          ws)
            ts = _run_cli(root, "trades-sum").stdout
            self.assertIn("sold 5,750.00", ts)
            lst = _run_cli(root, "list", "--json")
            j = json.loads(lst.stdout)
            self.assertAlmostEqual(sum(float(r.get("cost") or 0)
                                       for r in j["rows"]), 2500.0)
            lt = _run_cli(root, "list").stdout
            self.assertIn("350.00", lt)           # deferred total


class TestClassShareGrouping(unittest.TestCase):
    """S040-11: an QRL...TO option on QRL.B.TO shares groups under the
    class share in the per-underlying reports."""

    def test_aliases_and_sum_gains(self):
        from taxjson.lib.ticker_map import class_share_aliases, underlying_of
        from taxjson.bin.taxjson_sum_gains import summarize_gains
        al = class_share_aliases(["QRL.B.TO", "QRL260417C00044000.TO",
                                  "ABC.TO"])
        self.assertEqual(al, {"QRL.TO": "QRL.B.TO"})
        self.assertEqual(underlying_of("QRL260417C00044000.TO", al),
                         "QRL.B.TO")
        # Two classes on one root: ambiguous, no alias.
        self.assertEqual(class_share_aliases(["X.A.TO", "X.B.TO"]), {})
        res = summarize_gains({"transactions": [
            {"symbol": "QRL.B.TO", "date": "2025-04-15", "qty": 100,
             "cost": 5000.0, "proceeds": 5298.0, "gain": 298.0,
             "currency": "CAD"},
            {"symbol": "QRL260417C00044000.TO", "date": "2025-03-19",
             "qty": 1, "cost": -199.0, "proceeds": -51.0, "gain": 148.0,
             "currency": "CAD", "direction": "SHORT"}],
            "summary": {"year": 2025}})
        self.assertEqual(set(res["ticker_stats"]), {"QRL.B.TO"})


if __name__ == "__main__":
    unittest.main()
