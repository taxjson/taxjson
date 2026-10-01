"""Regression pins for the run-core LOW findings, second half (2026-09
audit, runcore-b).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no network (a
stub yfinance stands in where a command would call Yahoo).
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fix_l_runcore_a import (_CONFIG, _MARGIN_CSV, _QT_HEADER,  # noqa: E402
                                  REPO_ROOT, _project, _run_cli)


def _run_cli_env(root, *args, env=None, seed=None):
    e = dict(env or {})
    if seed is not None:
        e["PYTHONHASHSEED"] = str(seed)
    return _run_cli(root, *args, env=e)


# A stand-in for yfinance: records every symbol asked for (one per line
# in $FAKE_YF_LOG) and answers .info / .history from $FAKE_YF_NAMES.
_FAKE_YF = '''
import json, os
_names = json.loads(os.environ.get("FAKE_YF_NAMES") or "{}")
def _log(sym):
    p = os.environ.get("FAKE_YF_LOG")
    if p:
        with open(p, "a") as f:
            f.write(sym + "\\n")
class _Hist:
    empty = True
class Ticker:
    def __init__(self, sym):
        _log(sym)
        self._sym = sym
        self.history_metadata = {}
    @property
    def info(self):
        ln, sn = _names.get(self._sym, ["", ""])
        return {"longName": ln, "shortName": sn}
    def history(self, *a, **k):
        return _Hist()
'''


def _fake_yf_env(tmp, names=None):
    d = Path(tmp) / "fakeyf"
    (d / "yfinance").mkdir(parents=True, exist_ok=True)
    (d / "yfinance" / "__init__.py").write_text(_FAKE_YF)
    log = Path(tmp) / "yf.log"
    pp = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": f"{d}{os.pathsep}{pp}" if pp else str(d),
            "FAKE_YF_LOG": str(log),
            "FAKE_YF_NAMES": json.dumps(names or {})}, log


def _scan_project(tmp, holdings, raw_rows=(), ticker_map=None):
    """A project scan can read without a pipeline run: the per-listing
    holdings report and the raw book are all it needs."""
    root = Path(tmp) / "p"
    (root / "reports").mkdir(parents=True)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(_CONFIG)
    body = "".join(f'[[holding]]\nsymbol = "{s}"\nquantity = {q}\n\n'
                   for s, q in holdings)
    (root / "reports" / "margin_holdings.toml").write_text(body)
    (root / "work" / "margin_raw.json").write_text(
        json.dumps({"transactions": list(raw_rows)}))
    if ticker_map is not None:
        (root / "ticker.map").write_text(ticker_map)
    return root


class TestScan(unittest.TestCase):
    """S042-10, S042-12 / S048-00, S042-13, S049-10."""

    def test_online_probe_honours_offline_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [("QZQ.US", 10)], raw_rows=[
                {"action": "DIVIDEND", "symbol": "QZQ.US",
                 "date": "2025-03-01", "net_amount": 5.0}])
            env, log = _fake_yf_env(tmp)
            env["TAXJSON_OFFLINE"] = "1"
            r = _run_cli_env(root, "scan", "--online", env=env)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("TAXJSON_OFFLINE is set", r.stderr)
            self.assertFalse(log.exists() and log.read_text().strip(),
                             "a ticker was sent to Yahoo while offline")

    def test_online_findings_have_a_stable_order(self):
        names = {"GOOG.US": ["Alphabet Inc.", "Alphabet Inc."],
                 "GOOGL.US": ["Alphabet Inc.", "Alphabet Inc."],
                 "GOOG.TO": ["Alphabet Inc.",
                             "ALPHABET CDR (CAD HEDGED)"],
                 "GOOGL.NE": ["Alphabet Inc.", "Alphabet Inc."]}
        outs = set()
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [("GOOG.US", 1), ("GOOGL.US", 1),
                                       ("GOOG.TO", 1), ("GOOGL.NE", 1)])
            env, _log = _fake_yf_env(tmp, names)
            env["TAXJSON_OFFLINE"] = "0"
            for seed in range(6):
                r = _run_cli_env(root, "scan", "--online", "--json",
                                 env=env, seed=seed)
                self.assertNotIn("Traceback", r.stderr)
                outs.add(r.stdout)
        self.assertEqual(len(outs), 1, "finding order depends on the "
                                       "hash seed")
        doc = json.loads(outs.pop())
        self.assertTrue(doc["findings"])

    def test_unreadable_holdings_report_is_not_a_clean_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [("XEI.TO", 10)])
            # A second, readable account: one unreadable report among
            # readable ones used to warn and then print a clean scan.
            (root / "taxjson.toml").write_text(
                _CONFIG + '\n[accounts.tfsa]\ntype = "sheltered"\n')
            (root / "reports" / "tfsa_holdings.toml").write_text(
                '[[holding]]\nsymbol = "XIC.TO"\nquantity = 5\n')
            (root / "reports" / "margin_holdings.toml").write_text(
                '[[holding]]\nsymbol = "XEI.TO\n')
            r = _run_cli(root, "scan")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertNotIn("clean scan", r.stdout)
            self.assertIn("margin_holdings.toml", r.stderr)

    def test_unused_rule_note_needs_every_source_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [("XEI.TO", 10)],
                                 ticker_map="TOBASE AAQ.US AAQ.TO\n")
            src = root / "work" / "margin_questrade.json"
            src.write_text(json.dumps({"transactions": [
                {"symbol": "AAQ.US", "action": "BUYSELL"}]}))
            r = _run_cli(root, "scan")
            self.assertNotIn("match no parsed symbol", r.stdout)
            src.write_text('{"transactions": [{"symbol": "AA')
            r = _run_cli(root, "scan")
            self.assertNotIn("match no parsed symbol", r.stdout)
            self.assertIn("margin_questrade.json", r.stderr)
            self.assertNotIn("Traceback", r.stderr)


class TestAuditSources(unittest.TestCase):
    """S047-12 (_mapped.json is derived), S047-13 (sibling prefix)."""

    def test_source_files(self):
        from taxjson.bin.taxjson_run import _audit_source_files
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            for n in ("margin_questrade.json", "margin_mapped.json",
                      "margin_base.json", "margin_us_ib.json",
                      "margin_us_base.json", "crypto_wallet.json",
                      "crypto_mapped.json", "margin_manifest.json"):
                (w / n).write_text("{}")
            got = [p.name for p in _audit_source_files(
                w, "margin", ["margin", "margin_us", "crypto"])]
            self.assertEqual(got, ["margin_questrade.json"])
            got = [p.name for p in _audit_source_files(
                w, "margin_us", ["margin", "margin_us", "crypto"])]
            self.assertEqual(got, ["margin_us_ib.json"])
            got = [p.name for p in _audit_source_files(w, "crypto")]
            self.assertEqual(got, ["crypto_wallet.json"])


_RICH_TT = (
    "BUYSELL 2025-01-10 09:30:00 XEI.TO 100 CAD 10.0 -1000.0 0.0\n"
    "BUYSELL 2025-02-02 09:30:00 ABC261218C00050000.TO -1 CAD 1.5 "
    "150.004 0.0\n"
    "BUYSELL 2025-04-01 09:30:00 XEI.TO -100 CAD 11.0 1100.004 0.0\n"
    "DIVIDEND 2025-04-15 09:30:00 XEI.TO 0 CAD 0 30.00\n"
    "DIVIDEND_IN_LIEU 2025-05-01 09:30:00 XEI.TO 0 CAD 0 10.00\n")


def _tt_project(tmp, tt=_RICH_TT, config=_CONFIG, run=True):
    root = _project(tmp, config=config, csv=_QT_HEADER)
    (root / "inputs" / "margin" / "questrade_2025.csv").unlink()
    (root / "inputs" / "margin" / "rows.tt").write_text(tt)
    if run:
        r = _run_cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr
    return root


def _run_with_fx_note(root, *args):
    """The CLI with the FX-on-cash ledger stubbed (net -12,708.35,
    reportable -12,508.35 after the $200 exemption)."""
    code = (
        "import sys\n"
        "import taxjson.bin.taxjson_run as R\n"
        "R._fx_cash_doc = lambda root, cache: ("
        "{'net_gain': -12708.35, 'overdrafts': {}, 'pools_year_end': {}},"
        " {'reportable': -12508.35}, 'CAD', 2025, 'canada')\n"
        f"sys.argv = ['taxjson', '-C', {str(root)!r}] + {list(args)!r}\n"
        "R.main()\n")
    e = dict(os.environ, TAXJSON_OFFLINE="1")
    return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                          capture_output=True, text=True, env=e,
                          stdin=subprocess.DEVNULL)


class TestSumTable(unittest.TestCase):
    """S042-21 (rows foot), S042-22 (TOTAL includes PIL), G1-11 (every
    column pinned), S043-02 / S048-04 (denied footer), S043-04 / G1-3
    (FX note pinned)."""

    def test_every_column_of_an_account_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            r = _run_cli(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            row = doc["accounts"][0]
            # cap 100.004 + option 150.004: REALIZED rounds once
            # (250.01); OPTION takes the cent so the row foots.
            self.assertEqual(
                {k: row[k] for k in ("stock", "option", "realized",
                                     "dividend", "pil", "fees", "total")},
                {"stock": 100.0, "option": 150.01, "realized": 250.01,
                 "dividend": 30.0, "pil": 10.0, "fees": 0.0,
                 "total": 290.01})
            self.assertEqual(doc["totals"]["total"], 290.01)
            self.assertEqual(str(doc["year"]), "2025")
            self.assertEqual(doc["tainted_routed"], 0)
            self.assertEqual(doc["tainted_included"], 0)
            txt = _run_cli(root, "sum").stdout
            self.assertIn("TOTAL = REALIZED + DIVIDEND + PIL", txt)
            # The .sum's GRAND TOTAL is the same figure.
            dot_sum = (root / "reports" / "margin_wash.sum").read_text()
            self.assertIn("290.01", dot_sum)

    def test_fx_note_reportable_is_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            r = _run_with_fx_note(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            fx = json.loads(r.stdout)["filing"]["fx_cash"]
            self.assertIsNotNone(fx, "Canada FOR THE RETURN lost its "
                                     "line 15300 FX note")
            self.assertEqual((fx["line"], fx["net_gain"], fx["reportable"]),
                             ("15300", -12708.35, -12508.35))
            t = _run_with_fx_note(root, "sum").stdout
            self.assertIn("net -12,708.35, reportable -12,508.35 after "
                          "the $200 exemption", t)

    def test_denied_footer_names_the_permanent_part(self):
        cfg = _CONFIG + '\n[accounts.tfsa]\ntype = "sheltered"\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, tt=(
                "BUYSELL 2025-03-03 09:30:00 XEI.TO 100 CAD 10.0 -1000.0 0\n"
                "BUYSELL 2025-03-10 09:30:00 XEI.TO -100 CAD 8.0 800.0 0\n"),
                config=cfg, run=False)
            (root / "inputs" / "tfsa").mkdir()
            (root / "inputs" / "tfsa" / "rows.tt").write_text(
                "BUYSELL 2025-03-12 09:30:00 XEI.TO 100 CAD 8.0 -800.0 0\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            j = json.loads(_run_cli(root, "sum", "--json").stdout)
            self.assertEqual(j["filing"]["totals"]["denied"], 200.0)
            self.assertEqual(j["filing"]["totals"]["permanently_denied"],
                             200.0)
            t = _run_cli(root, "sum").stdout
            self.assertIn("lost for good — no ACB addition (200.00 of the "
                          "DENIED total)", " ".join(t.split()))


class TestEstimatePins(unittest.TestCase):
    """S042-20: PIL is income in the estimate, the AMT top-up is part
    of net tax owing, the assumed 15% withholding is ADDED per account;
    S043-20: withholding summed in a stable order."""

    def test_pil_raises_the_estimate(self):
        res = {}
        for with_pil in (True, False):
            tt = _RICH_TT if with_pil else "".join(
                ln + "\n" for ln in _RICH_TT.splitlines()
                if "IN_LIEU" not in ln)
            with tempfile.TemporaryDirectory() as tmp:
                root = _tt_project(tmp, tt=tt)
                r = _run_cli(root, "estimate", "--json", "--province",
                             "ON", "--other-income", "50000")
                self.assertEqual(r.returncode, 0, r.stderr)
                res[with_pil] = json.loads(r.stdout)["estimate"]
        self.assertAlmostEqual(res[True]["investment_income"]
                               - res[False]["investment_income"], 10.0)
        self.assertGreater(res[True]["estimated_tax"],
                           res[False]["estimated_tax"])

    def test_net_tax_owing_adds_the_amt_topup(self):
        from taxjson.bin.taxjson_run import _net_tax_owing
        r = {"tax_with": {"total": 1000.0}, "amt": {"topup": 250.0}}
        self.assertEqual(_net_tax_owing(r, 300.0), 950.0)
        self.assertEqual(_net_tax_owing(r, 5000.0), 0.0)

    def test_withholding_per_account_and_order(self):
        from taxjson.bin.taxjson_run import _actual_withholding
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            (w / "ib_base.json").write_text(json.dumps({"transactions": [
                {"action": "TAX", "date": "2025-03-01", "net_amount": 40.0},
                {"action": "TAX", "date": "2024-03-01", "net_amount": 99.0}]}))
            (w / "qt_base.json").write_text(json.dumps({"transactions": []}))
            # ib's own TAX rows (40) plus 15% of qt's foreign dividends.
            got = _actual_withholding(w, {"ib", "qt"}, 2025,
                                      {"ib": 500.0, "qt": 1000.0})
            self.assertAlmostEqual(got, 190.0)
            for i, v in enumerate((0.1, 0.2, 0.3, 0.4)):
                (w / f"a{i}_base.json").write_text(json.dumps(
                    {"transactions": [{"action": "TAX",
                                       "date": "2025-01-01",
                                       "net_amount": v}]}))
            accts = {f"a{i}" for i in range(4)}
            self.assertEqual(_actual_withholding(w, accts, 2025), 1.0)


class TestInstalmentInputs(unittest.TestCase):
    """S043-05 (TOML numbers only), S043-15 / S048-12 (what the schedule
    does not model is printed)."""

    def test_string_amounts_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            for block in ('[instalments]\nwithheld = "5_000"\n',
                          '[instalments]\npaid = [{ date = "2025-03-15", '
                          'amount = "5_000" }]\n'):
                (root / "taxjson.toml").write_text(
                    _CONFIG.replace('country = "canada"',
                                    'country = "canada"\nprovince = "ON"')
                    + "\n" + block)
                r = _run_cli(root, "instalments")
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("5_000", r.stderr)

    def test_not_modelled_is_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            (root / "taxjson.toml").write_text(
                _CONFIG.replace('country = "canada"',
                                'country = "canada"\nprovince = "ON"')
                + '\n[instalments]\nbasis = "current_year"\n')
            r = _run_cli(root, "instalments")
            self.assertEqual(r.returncode, 0, r.stderr)
            flat = " ".join(r.stdout.split())
            self.assertIn("NOT MODELLED: CPP/EI payable on self-employment",
                          flat)
            self.assertIn("line 15300", flat)
            j = json.loads(_run_cli(root, "instalments", "--json").stdout)
            self.assertEqual(len(j["not_modelled"]), 2)
            e = _run_cli(root, "estimate", "--province", "ON")
            self.assertIn("line 15300", " ".join(e.stdout.split()))



class TestCorruptWorkFiles(unittest.TestCase):
    """S042-18: a truncated, non-UTF-8 or wrong-shape work/ artifact is
    a one-line error (or a named skip), never a traceback."""

    _CMDS = {
        "margin_gains_wash.json": [
            ("sum",), ("winners",), ("shares",), ("wash-sales",), ("list",),
            ("close-year",), ("audit", "--summary"), ("t1135",),
            ("wash-radar",), ("buy-check", "XEI")],
        "margin_base.json": [
            ("option-boundary",), ("transfers",), ("roc-sum",),
            ("find-missing-history",), ("estimate", "--province", "ON")],
        "margin_raw.json": [("divs-sum",), ("fx-cash",), ("trades",),
                            ("leaps-sum",)],
        "margin_report.json": [("sum",)],
    }

    def test_no_tracebacks(self):
        bad = {"trunc": lambda b: b[:len(b) // 2],
               "nonutf8": lambda b: b"\xff\xfe" + b,
               "list": lambda b: b"[1, 2, 3]",
               "scalars": lambda b: b'{"transactions": 5, "summary": 3}'}
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "base").mkdir()
            base = _project(Path(tmp) / "base")
            self.assertEqual(_run_cli(base, "run", "--no-input")
                             .returncode, 0)
            for fname, cmds in self._CMDS.items():
                for vname, mangle in bad.items():
                    root = Path(tmp) / f"{fname}-{vname}"
                    shutil.copytree(base, root)
                    f = root / "work" / fname
                    f.write_bytes(mangle(f.read_bytes()))
                    for cmd in cmds:
                        with self.subTest(file=fname, how=vname, cmd=cmd):
                            r = _run_cli(root, *cmd)
                            self.assertNotIn("Traceback", r.stderr)
                    shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
