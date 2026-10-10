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
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fix_l_runcore_a import (_CONFIG, _QT_HEADER,  # noqa: E402
                                  REPO_ROOT, _project, _run_cli,
                                  _with_setting)
from tax_rules import rule
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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
    """A project tips can read without a pipeline run: the per-listing
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


class TestTips(unittest.TestCase):
    """S042-10, S042-12 / S048-00, S042-13, S049-10 (`taxjson scan`, now
    `tips`; the unused-rule check is `ticker-map --suggest`'s)."""

    def test_online_probe_honours_offline_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [("QZQ.US", 10)], raw_rows=[
                {"action": "DIVIDEND", "symbol": "QZQ.US",
                 "date": "2025-03-01", "net_amount": 5.0}])
            env, log = _fake_yf_env(tmp)
            env["TAXJSON_OFFLINE"] = "1"
            r = _run_cli_env(root, "tips", "--online", env=env)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("TAXJSON_OFFLINE is set", r.stderr)
            self.assertFalse(log.exists() and log.read_text().strip(),
                             "a ticker was sent to Yahoo while offline")

    def test_online_findings_have_a_stable_order(self):
        # Keyed by Yahoo's spelling (the probe asks for it, B14): the
        # same-root MAP-GAP these listings also make is `ticker-map
        # --suggest`'s now, so the order is the online findings'.
        names = {"GOOG": ["Alphabet Inc.", "Alphabet Inc."],
                 "GOOGL": ["Alphabet Inc.", "Alphabet Inc."],
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
                r = _run_cli_env(root, "tips", "--online", "--json",
                                 env=env, seed=seed)
                self.assertNotIn("Traceback", r.stderr)
                outs.add(r.stdout)
        self.assertEqual(len(outs), 1, "finding order depends on the "
                                       "hash seed")
        doc = json.loads(outs.pop())
        self.assertTrue(doc["findings"])

    def test_unreadable_holdings_report_is_not_a_clean_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [("XEI.TO", 10)])
            # A second, readable account: one unreadable report among
            # readable ones used to warn and then print a clean report.
            (root / "taxjson.toml").write_text(
                _CONFIG + '\n[accounts.tfsa]\ntype = "sheltered"\n')
            (root / "reports" / "tfsa_holdings.toml").write_text(
                '[[holding]]\nsymbol = "XIC.TO"\nquantity = 5\n')
            (root / "reports" / "margin_holdings.toml").write_text(
                '[[holding]]\nsymbol = "XEI.TO\n')
            r = _run_cli(root, "tips")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertNotIn("No tips", r.stdout)
            self.assertIn("margin_holdings.toml", r.stderr)

    def test_unused_rule_note_needs_every_source_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(tmp, [("XEI.TO", 10)],
                                 ticker_map="TOBASE AAQ.US AAQ.TO\n")
            src = root / "work" / "margin_questrade.json"
            src.write_text(json.dumps({"transactions": [
                {"symbol": "AAQ.US", "action": "BUYSELL"}]}))
            r = _run_cli(root, "ticker-map", "--suggest")
            self.assertNotIn("Unused rules", r.stdout)
            self.assertNotIn("TOBASE AAQ.US AAQ.TO", r.stdout)
            src.write_text('{"transactions": [{"symbol": "AA')
            r = _run_cli(root, "ticker-map", "--suggest")
            self.assertNotIn("Unused rules", r.stdout)
            self.assertIn("margin_questrade.json", r.stdout)
            self.assertIn("unused-rule check is skipped", r.stdout)
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
    "BUYSELL 2025-02-02 09:30:00 ABC261218C00050000.TO -1 CAD 1.6 "
    "160.004 0.0\n"
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
        "{'net_gain': -12708.35, 'overdrafts': {}, 'pools_year_end': {},"
        " 'per_currency': {'USD': {'acquired': 1.0, 'disposed': 1.0,"
        " 'gain': -12708.35}}, 'overdrafts_year': {'USD': {'count': 2,"
        " 'units': 300.0}}},"
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
            # cap 100.004 + option 160.004: REALIZED rounds once
            # (260.01); OPTION takes the cent so the row foots.
            self.assertEqual(
                {k: row[k] for k in ("stock", "option", "realized",
                                     "dividend", "pil", "fees", "total")},
                {"stock": 100.0, "option": 160.01, "realized": 260.01,
                 "dividend": 30.0, "pil": 10.0, "fees": 0.0,
                 "total": 300.01})
            self.assertEqual(doc["totals"]["total"], 300.01)
            self.assertEqual(str(doc["year"]), "2025")
            self.assertEqual(doc["tainted_routed"], 0)
            self.assertEqual(doc["tainted_included"], 0)
            txt = _run_cli(root, "sum").stdout
            self.assertIn("TOTAL = REALIZED + DIVIDEND + PIL", txt)
            # The .sum's GRAND TOTAL is the same figure.
            dot_sum = (root / "reports" / "margin_wash.sum").read_text()
            self.assertIn("300.01", dot_sum)

    def test_fx_note_reportable_is_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            r = _run_with_fx_note(root, "sum", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            fx = json.loads(r.stdout)["filing"]["fx_cash"]
            self.assertIsNotNone(fx, "Canada FOR THE RETURN lost its "
                                     "line 15300 FX note")
            # The default ledger is NOT RELIABLE (CA-FX-07): no
            # reportable figure, the raw ones under unreliable_raw.
            self.assertEqual((fx["line"], fx["reliable"]), ("15300", False))
            self.assertNotIn("reportable", fx)
            self.assertEqual((fx["unreliable_raw"]["net_gain"],
                              fx["unreliable_raw"]["reportable"]),
                             (-12708.35, -12508.35))
            t = " ".join(_run_with_fx_note(root, "sum").stdout.split())
            self.assertIn("FX on foreign cash: NOT RELIABLE for 2025 — 2 "
                          "in-year overdrafts (300.00 USD); conversions, "
                          "deposits/withdrawals and margin balances are not "
                          "read; do not file this figure", t)
            self.assertNotIn("-12,508.35", t)

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
            # A2-0659: the registered-account part is lost for good,
            # an affiliated person's is theirs to add to their own ACB.
            self.assertIn("is lost for good, and one caused by an "
                          "affiliated person's acquisition is permanent "
                          "for this return", " ".join(t.split()))
            self.assertIn("(200.00 of the DENIED total)", " ".join(t.split()))


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
            # The list in full: --details (Essentials first).
            r = _run_cli(root, "instalments", "--details")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Not modelled: self-employed CPP/EI",
                          _run_cli(root, "instalments").stdout)
            flat = " ".join(r.stdout.split())
            self.assertIn("Not modelled - CPP/EI payable on "
                          "self-employment", flat)
            self.assertIn("line 15300", flat)
            j = json.loads(_run_cli(root, "instalments", "--json").stdout)
            self.assertEqual(len(j["not_modelled"]), 2)
            # The assumptions in full: --details (Essentials first).
            e = _run_cli(root, "estimate", "--province", "ON", "--details")
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


_TWO_ACCT_CFG = _CONFIG + '\n[accounts.tfsa]\ntype = "sheltered"\n'


def _held_project(tmp, holdings_line=""):
    """margin holds 100 XEI.TO, tfsa holds 10 XIC.TO."""
    cfg = _TWO_ACCT_CFG.replace('[accounts.margin]\ntype = "taxable"\n',
                                '[accounts.margin]\ntype = "taxable"\n'
                                + holdings_line)
    root = _tt_project(tmp, tt="BUYSELL 2025-01-10 09:30:00 XEI.TO 100 "
                       "CAD 10.0 -1000.0 0.0\n", config=cfg, run=False)
    (root / "inputs" / "tfsa").mkdir()
    (root / "inputs" / "tfsa" / "rows.tt").write_text(
        "BUYSELL 2025-01-10 09:30:00 XIC.TO 10 CAD 10.0 -100.0 0.0\n")
    r = _run_cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr
    (root / "hold").mkdir(exist_ok=True)
    return root


class TestSanity(unittest.TestCase):
    """S044-03 / S044-16 (ids masked), S044-18 (blank rows refused),
    S044-19 (unchecked accounts), S049-08 (TOML error named)."""

    def test_missing_file_note_masks_the_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _held_project(
                tmp, 'holdings = ["hold/55500001_holdings.toml"]\n')  # pii-ok
            r = _run_cli(root, "sanity")
            self.assertIn("55***_holdings.toml", r.stderr)
            self.assertNotIn("55500001", r.stderr + r.stdout)  # pii-ok

    def test_meta_account_is_masked_in_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _held_project(tmp, 'holdings = ["hold/exact.toml"]\n')
            (root / "hold" / "exact.toml").write_text(
                '[meta]\naccount = "U5550001"\n\n'  # pii-ok
                '[[holding]]\nsymbol = "XEI.TO"\nquantity = 100\n')
            r = _run_cli(root, "sanity", "--json")
            self.assertNotIn("U5550001", r.stdout)  # pii-ok
            j = json.loads(r.stdout)
            self.assertEqual(j["files"][0]["file_account"], "U5***")

    def test_blank_rows_are_refused(self):
        rows = ('[[holding]]\nsymbol = ""\ncusip = "000000AA0"\n'
                'quantity = 50\n',
                '[[holding]]\nsymbol = "XYZ.US"\nquantity = ""\n',
                '[[holding]]\nsymbol = "XYZ.US"\nqty = 200\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = _held_project(tmp, 'holdings = ["hold/h.toml"]\n')
            for extra in rows:
                with self.subTest(extra=extra):
                    (root / "hold" / "h.toml").write_text(
                        '[[holding]]\nsymbol = "XEI.TO"\nquantity = 100\n\n'
                        + extra)
                    r = _run_cli(root, "sanity")
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    self.assertIn("h.toml", r.stderr)
                    self.assertNotIn("Traceback", r.stderr)

    def test_unchecked_account_is_not_done(self):
        from datetime import date
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            root = _held_project(tmp, 'holdings = ["hold/h.toml"]\n')
            (root / "hold" / "h.toml").write_text(
                '[[holding]]\nsymbol = "XEI.TO"\nquantity = 100\n')
            r = _run_cli(root, "sanity")
            self.assertIn("UNCHECKED: account(s) tfsa (1 position(s))",
                          r.stdout)
            j = json.loads(_run_cli(root, "sanity", "--json").stdout)
            self.assertFalse(j["complete"])
            self.assertEqual(j["uncovered_accounts"], ["tfsa"])
            from taxjson.bin.taxjson_run import load_config
            ctx = cl.Ctx(root, load_config(root), 2025, date.today(),
                         cl.default_run_sub(root))
            res = cl.d_sanity(ctx)
            self.assertEqual(res.status, "attention")
            self.assertIn("tfsa", res.detail)

    def test_unreadable_config_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _held_project(tmp, 'holdings = ["hold/h.toml"]\n')
            cfg = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                cfg.replace("year = 2025", "year = 2025 x"))
            r = _run_cli(root, "sanity")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not valid TOML", r.stderr)
            self.assertNotIn("declares `holdings", r.stderr)


def _gains_project(tmp, inventory=(), transactions=(), config=_CONFIG,
                   base_rows=()):
    """A project whose work/ holds a hand-written gains file (and base
    book): the views read nothing else."""
    root = Path(tmp) / "g"
    (root / "work").mkdir(parents=True)
    (root / "taxjson.toml").write_text(config)
    (root / "work" / "margin_gains.json").write_text(json.dumps({
        "summary": {"year": 2025}, "transactions": list(transactions),
        "inventory": list(inventory)}))
    (root / "work" / "margin_base.json").write_text(json.dumps(
        {"transactions": list(base_rows)}))
    return root


_US_CFG = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "usa"
base_currency = "USD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""


class TestViews(unittest.TestCase):
    """S044-04 / S044-05 (shares), S045-03 / S049-14 (list), S048-10
    (short legs), S045-09 (missing books), S045-13 (permanent total),
    S048-22 (account token), S048-24 (slip line), S046-21 (units)."""

    def test_shares_leaves_futures_out_and_says_as_of(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _gains_project(tmp, inventory=[
                {"symbol": "F:MBTM6.US", "qty": 1, "total_cost": 38128.27},
                {"symbol": "XYZ.TO", "qty": 10, "total_cost": 100.0}],
                base_rows=[{"date": "2026-02-02", "action": "BUYSELL",
                            "symbol": "XYZ.TO"}])
            r = _run_cli(root, "shares")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("F:MBTM6", r.stdout)
            self.assertIn("XYZ.TO", r.stdout)
            self.assertIn("as of the latest data in the books (2026-02-02)",
                          r.stdout)
            self.assertNotIn("tax year 2025", r.stdout)
            j = json.loads(_run_cli(root, "shares", "--json").stdout)
            self.assertEqual(j["as_of"], "2026-02-02")
            self.assertEqual([x["symbol"] for x in j["rows"]], ["XYZ.TO"])

    def test_list_option_cost_is_per_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _gains_project(tmp, inventory=[
                {"symbol": "ABC270115C00050000.TO", "qty": 1,
                 "total_cost": 500.65},
                {"symbol": "XYZ.TO", "qty": 100, "total_cost": 500.65}])
            j = json.loads(_run_cli(root, "list", "--json").stdout)
            cps = {x["symbol"]: x["cost_per_share"] for x in j["rows"]}
            self.assertEqual(cps["ABC270115C00050000.TO"], 5.0065)
            self.assertEqual(cps["XYZ.TO"], 5.0065)

    def test_us_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _gains_project(tmp, config=_US_CFG, inventory=[
                {"symbol": "XYZ.US", "qty": 10, "total_cost": 1200.0,
                 "deferred_wash": 200.0}])
            r = _run_cli(root, "list")
            self.assertIn("§1091", r.stdout)
            self.assertNotIn("superficial", r.stdout)

    def test_short_legs_read_the_real_world_way(self):
        from taxjson.bin.taxjson_run import (_gain_display_line,
                                             _real_world_legs)
        t = {"date": "2025-07-01", "symbol": "ABC270618C00050000.TO",
             "qty": 1, "currency": "CAD", "direction": "SHORT",
             "cost": -515.0, "proceeds": -100.0, "gain": 415.0}
        self.assertEqual(_real_world_legs(t), (515.0, 100.0))
        line = _gain_display_line(t)
        self.assertIn("515.00 100.00 415.00", line)
        self.assertNotIn("-", line.split("CAD", 1)[1])
        self.assertEqual(_real_world_legs({"cost": 100.0,
                                           "proceeds": 150.0}),
                         (150.0, 100.0))

    def test_missing_books_are_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _held_project(tmp)
            for f in (root / "work").glob("tfsa_*"):
                f.unlink()
            for cmd in (("wash-sales",), ("divs-sum",), ("winners",)):
                with self.subTest(cmd=cmd):
                    r = _run_cli(root, *cmd)
                    self.assertIn("no ", r.stderr)
                    self.assertIn("tfsa", r.stderr)

    def test_permanent_total_is_pinned(self):
        cfg = _CONFIG + '\n[accounts.tfsa]\ntype = "sheltered"\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, tt=(
                "BUYSELL 2025-03-03 09:30:00 XEI.TO 100 CAD 10.0 -1000.0 0\n"
                "BUYSELL 2025-03-10 09:30:00 XEI.TO -100 CAD 8.0 800.0 0\n"
                "BUYSELL 2025-03-12 09:30:00 XEI.TO 70 CAD 8.0 -560.0 0\n"),
                config=cfg, run=False)
            (root / "inputs" / "tfsa").mkdir()
            (root / "inputs" / "tfsa" / "rows.tt").write_text(
                "BUYSELL 2025-03-12 09:30:00 XEI.TO 30 CAD 8.0 -240.0 0\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            j = json.loads(_run_cli(root, "wash-sales", "--json").stdout)
            self.assertEqual(j["totals"]["denied"], 200.0)
            self.assertEqual(j["totals"]["permanently_denied"], 60.0)
            t = _run_cli(root, "wash-sales").stdout
            self.assertIn("200.00 CAD of losses denied (60.00 "
                          "permanently denied)", t)

    def test_account_token_is_not_a_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            for cmd in ("winners", "ccd-sum"):
                r = _run_cli(root, cmd, "margin")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("may exceed", r.stderr)
            r = _run_cli(root, "winners", "all")
            self.assertIn("may exceed", r.stderr)

    def test_slip_line_leaves_staking_out(self):
        cfg = _CONFIG + '\n[accounts.kr]\ntype = "taxable"\ncrypto = true\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "d"
            (root / "work").mkdir(parents=True)
            (root / "taxjson.toml").write_text(cfg)
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [{"action": "DIVIDEND", "date":
                                   "2025-05-01", "symbol": "AAA.TO",
                                   "currency": "CAD",
                                   "gross_amount": 30.0,
                                   "net_amount": 30.0}]}))
            (root / "work" / "kr_filled.json").write_text(json.dumps(
                {"transactions": [{"action": "DIVIDEND", "date":
                                   "2025-05-03", "symbol": "ETH",
                                   "currency": "CAD",
                                   "gross_amount": 7.0,
                                   "net_amount": 7.0}]}))
            r = _run_cli(root, "divs-sum")
            self.assertIn("TAXABLE (compare with T5/T3 slips; crypto "
                          "staking excluded): 30.00 CAD", r.stdout)
            j = json.loads(_run_cli(root, "divs-sum", "--json").stdout)
            self.assertEqual(j["totals_slips"], {"CAD": 30.0})
            self.assertEqual(j["totals"], {"CAD": 37.0})

    def test_fx_cash_units_to_the_cent(self):
        code = (
            "import sys\n"
            "import taxjson.bin.taxjson_run as R\n"
            "import taxjson.bin.taxjson_fx_cash as FX\n"
            "FX.render_report = lambda *a, **k: 'REPORT'\n"
            "R._fx_cash_doc = lambda root, cache: ({'events': [{"
            "'date': '2025-03-03', 'account': 'margin', 'currency': 'USD',"
            " 'units': 61234.57, 'rate': 1.3579, 'gain': 456.7,"
            " 'symbol': 'DLR.U.TO'}]}, {}, 'CAD', 2025, 'canada')\n"
            "sys.argv = ['taxjson', 'fx-cash', '--events']\n"
            "R.main()\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        # (a table row now: compare its cells)
        self.assertIn("USD 61,234.57 1.3579 +456.70 DLR.U.TO",
                      " ".join(r.stdout.split()), r.stderr)


class TestYearsAndLocks(unittest.TestCase):
    """S044-06 / S044-07 (no year), S044-08 (unreadable lock), S045-23
    (year not ended), S045-24 (empty year), S046-02 (timing stamp),
    S047-14 (--year range), S047-20 (init year vs since range)."""

    def test_option_boundary_needs_a_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            (root / "taxjson.toml").write_text(
                _CONFIG.replace("year = 2025\n", "")
                .replace("option_grant_timing_since = 2025\n", ""))
            r = _run_cli(root, "option-boundary")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("[settings] year is required", r.stderr)
            self.assertNotIn("None", r.stderr)
            self.assertNotIn("tax year 0", r.stdout)

    def test_unreadable_lock_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            (root / "filed").mkdir()
            (root / "filed" / "2024.json").write_text('{"year": 2024, "op')
            r = _run_cli(root, "option-boundary")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("cannot read filed/2024.json", r.stderr)

    def test_close_year_refuses_an_open_year(self):
        from datetime import date
        y = date.today().year
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, tt=(
                f"BUYSELL {y}-01-10 09:30:00 XEI.TO 100 CAD 10.0 -1000.0 0\n"
                f"BUYSELL {y}-01-20 09:30:00 XEI.TO -100 CAD 11.0 1100.0 0\n"),
                config=_CONFIG.replace("2025", str(y)))
            r = _run_cli(root, "close-year")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("has not ended", r.stderr)
            self.assertFalse((root / "filed" / f"{y}.json").exists())

    def test_close_year_refuses_an_empty_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, config=_CONFIG.replace(
                "year = 2025", "year = 2015").replace(
                "option_grant_timing_since = 2025",
                "option_grant_timing_since = 2015"))
            r = _run_cli(root, "close-year")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("no disposition and no income in 2015", r.stderr)
            self.assertFalse((root / "filed" / "2015.json").exists())

    def test_close_year_refuses_a_timing_edited_after_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            (root / "taxjson.toml").write_text(_with_setting(
                'option_premium_timing = "close"'))
            # Without --force the run-state guard already stops it
            # (taxjson.toml changed since the run); --force waives that
            # one, never this.
            r = _run_cli(root, "close-year", "--force")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("another option timing", r.stderr)
            self.assertFalse((root / "filed" / "2025.json").exists())
            (root / "taxjson.toml").write_text(_CONFIG)
            r = _run_cli(root, "close-year", "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            lock = json.loads((root / "filed" / "2025.json").read_text())
            self.assertEqual(lock["option_timing"]["option_premium_timing"],
                             "grant")

    def test_year_flags_are_plausible_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            for cmd in (("audit", "--summary", "--year", "2204"),
                        ("audit", "--year", "0"),
                        ("find-missing-history", "--year", "-5"),
                        ("close-year", "--year", "0")):
                with self.subTest(cmd=cmd):
                    r = _run_cli(root, *cmd)
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertIn("plausible tax year", r.stderr)
            for mod in ("taxjson_fees", "taxjson_missing_history",
                        "taxjson_reconcile_slips", "taxjson_sum_income"):
                with self.subTest(mod=mod):
                    r = subprocess.run(
                        [sys.executable, "-m", f"taxjson.bin.{mod}",
                         "--year", "2204", "x.json"],
                        cwd=REPO_ROOT, capture_output=True, text=True,
                        stdin=subprocess.DEVNULL)
                    self.assertEqual(r.returncode, 2)
                    self.assertIn("plausible tax year", r.stderr)

    def test_old_init_year_passes_validation(self):
        from taxjson.bin.taxjson_run import validate_config
        from taxjson.lib.config_check import bool_setting_problems
        cfg = {"settings": {"year": 1989, "country": "canada",
                            "base_currency": "CAD",
                            "option_grant_timing_since": 1989},
               "accounts": {"m": {"type": "taxable"}}}
        validate_config(cfg)                    # no SystemExit
        self.assertFalse([p for p in bool_setting_problems(cfg)
                          if "option_grant_timing_since" in p])


def _days_ago(n):
    from datetime import date, timedelta
    return (date.today() - timedelta(days=n)).isoformat()


class TestWashAdvice(unittest.TestCase):
    """S046-10 (US crypto), S047-02 / S047-03 / S047-05 / S047-08 (last
    loss line), S047-06 (bare coin), S048-19 / S049-15 (still-held)."""

    def _ca_project(self, tmp, margin_tt, crypto_tt=None):
        from datetime import date
        y = date.today().year
        cfg = _CONFIG.replace("2025", str(y))
        if crypto_tt is not None:
            cfg += '\n[accounts.kr]\ntype = "taxable"\ncrypto = true\n'
        root = _tt_project(tmp, tt=margin_tt, config=cfg, run=False)
        if crypto_tt is not None:
            (root / "inputs" / "kr").mkdir()
            (root / "inputs" / "kr" / "rows.tt").write_text(crypto_tt)
        r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        return root

    @rule("US-WASH-13")
    def test_us_crypto_is_outside_1091_everywhere(self):
        cfg = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "usa"\n'
               'base_currency = "USD"\nsource_currencies = []\n\n'
               '[accounts.kr1]\ntype = "taxable"\ncrypto = true\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(cfg)
            (root / "inputs" / "kr1").mkdir(parents=True)
            (root / "inputs" / "kr1" / "rows.tt").write_text(
                "BUYSELL 2025-05-01 09:30:00 BTC 1 USD 50000 -50000 0\n"
                "BUYSELL 2025-06-02 09:30:00 BTC -1 USD 40000 40000 0\n"
                "BUYSELL 2025-06-10 09:30:00 BTC 1 USD 41000 -41000 0\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            for cmd in (("wash-sales", "--explain", "kr1"),
                        ("wash-sales", "--explain"),
                        ("wash-radar", "kr1", "--date", "2025-06-20"),
                        ("wash-radar", "--date", "2025-06-20")):
                with self.subTest(cmd=cmd):
                    r = _run_cli(root, *cmd)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertNotIn("disallowed +", r.stdout)
                    self.assertNotIn("VIOLATION:", r.stdout)
                    self.assertIn("§1091", r.stdout)

    def test_last_loss_line_wording_and_day_30(self):
        from taxjson.bin.taxjson_run import _last_loss_line
        ll = {"symbol": "XYZ.TO", "date": _days_ago(30), "gain": -500.0,
              "date_kind": "settled"}
        line = _last_loss_line(ll)
        self.assertIn(f"XYZ.TO settled {_days_ago(30)} (30 days ago", line)
        self.assertIn("INSIDE the 30-day window", line)
        ll["date"] = _days_ago(31)
        self.assertIn("outside the 30-day window", _last_loss_line(ll))
        ll["unknown_cost"] = True
        self.assertIn("unknown cost (no purchase in your files)",
                      _last_loss_line(ll))

    def test_last_loss_reads_routed_rows_and_warns(self):
        from taxjson.bin.taxjson_run import _last_loss_by_class
        with tempfile.TemporaryDirectory() as tmp:
            g = Path(tmp) / "margin_gains_wash.json"
            g.write_text(json.dumps({
                "summary": {"year": 2026},
                "transactions": [{"symbol": "NOP.TO", "qty": -5,
                                  "date": "2026-09-01",
                                  "date_settle": "2026-09-02",
                                  "gain": -500.0}],
                "manual_reporting_required": [
                    {"symbol": "KLM.TO", "qty": -11, "date": "2026-09-21",
                     "date_settle": "2026-09-22", "raw_gain": -450.0}]}))
            bad = Path(tmp) / "other_gains.json"
            bad.write_text('{"transactions": [')
            from io import StringIO
            from contextlib import redirect_stderr
            err = StringIO()
            with redirect_stderr(err):
                ll = _last_loss_by_class(
                    {"margin": g, "other": bad}, lambda s: s, set(),
                    usa=False)
            self.assertEqual(ll["KLM.TO"]["date"], "2026-09-22")
            self.assertTrue(ll["KLM.TO"]["unknown_cost"])
            self.assertEqual(ll["KLM.TO"]["date_kind"], "settled")
            self.assertIn("NOP.TO", ll)
            self.assertIn("other_gains.json", err.getvalue())

    def test_bare_coin_query_and_still_held_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ca_project(
                tmp,
                margin_tt=(
                    f"BUYSELL {_days_ago(40)} 09:30:00 ETH.TO 100 CAD 10.0 "
                    f"-1000.0 0\n"
                    f"BUYSELL {_days_ago(6)} 09:30:00 ETH.TO -100 CAD 8.0 "
                    f"800.0 0\n"
                    f"BUYSELL {_days_ago(9)} 09:30:00 XYZ.TO 10 CAD 10.0 "
                    f"-100.0 0\n"),
                crypto_tt=(f"BUYSELL {_days_ago(60)} 09:30:00 ETH 1 CAD "
                           f"3000 -3000 0\n"))
            coin = _run_cli(root, "buy-check", "ETH")
            self.assertNotIn("ETH.TO: COOLING", coin.stdout)
            self.assertIn("ETH: SAFE", coin.stdout)
            self.assertIn("ETH.TO is a separate listing", coin.stdout)
            eq = _run_cli(root, "buy-check", "ETH.TO")
            self.assertEqual(eq.returncode, 1, eq.stdout)
            self.assertIn("if you still hold the shares 30 days after "
                          "that sale", " ".join(eq.stdout.split()))
            self.assertIn("settled", eq.stdout)
            ex = _run_cli(root, "buy-check", "XYZ.TO")
            flat = " ".join(ex.stdout.split())
            self.assertIn("a full exit is not", flat)
            self.assertNotIn("before ~31 days", flat)


class TestAuditAndMissingHistory(unittest.TestCase):
    """S047-17 (merged total rounded once), S048-17 (broker ticker
    filter), S048-18 (locked-year timing), S047-19 (phantoms hint),
    S049-01 (renamed symbol traced to the broker's)."""

    def test_merged_audit_total_is_rounded_once(self):
        cfg = _CONFIG + '\n[accounts.kr]\ntype = "taxable"\ncrypto = true\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, tt=(
                "BUYSELL 2025-01-10 09:30:00 XEI.TO 100 CAD 10.0 -1000.0 0\n"
                "BUYSELL 2025-04-01 09:30:00 XEI.TO -100 CAD 11.0 1100.004 0\n"),
                config=cfg, run=False)
            (root / "inputs" / "kr").mkdir()
            (root / "inputs" / "kr" / "rows.tt").write_text(
                "BUYSELL 2025-01-10 09:30:00 ETH 1 CAD 1000 -1000 0\n"
                "BUYSELL 2025-04-01 09:30:00 ETH -1 CAD 1050.004 1050.004 0\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "audit", "--json", "--no-trace")
            j = json.loads(r.stdout)
            self.assertEqual(len(j["events"]), 2, r.stderr)
            self.assertEqual(j["total_gain"], 150.01)

    def test_filter_takes_the_broker_ticker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, tt=(
                "BUYSELL 2025-01-10 09:30:00 XEIOLD.TO 100 CAD 10.0 -1000.0 0\n"
                "BUYSELL 2025-04-01 09:30:00 XEIOLD.TO -100 CAD 11.0 1100.0 0\n"),
                run=False)
            (root / "ticker.map").write_text("TOBASE XEIOLD.TO XEI.TO\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "audit", "--summary", "XEIOLD.TO")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("XEI.TO", r.stdout)

    def test_locked_year_uses_its_recorded_timing(self):
        cfg = _CONFIG.replace("year = 2025", "year = 2026").replace(
            "option_grant_timing_since = 2025\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, tt=(
                "BUYSELL 2025-12-10 09:30:00 ABC261218C00050000.TO -1 CAD "
                "4.0 400.0 0\n"
                "BUYSELL 2026-01-10 09:30:00 ABC261218C00050000.TO 1 CAD "
                "1.0 -100.0 0\n"), config=cfg)
            (root / "filed").mkdir()
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "option_timing": {
                    "option_premium_timing": "grant",
                    "option_grant_since": 2025}}))
            r = _run_cli(root, "audit", "--year", "2025", "--json",
                         "--no-trace")
            self.assertIn("2025 is locked", r.stderr)
            j = json.loads(r.stdout)
            self.assertEqual(j["total_gain"], 400.0)

    def _short_project(self, tmp, tmap=None):
        root = _tt_project(tmp, tt=(
            "BUYSELL 2025-03-03 09:30:00 XEIOLD.TO -10 CAD 10.0 100.0 0\n"),
            run=False)
        if tmap:
            (root / "ticker.map").write_text(tmap)
        r = _run_cli(root, "run", "--no-input")
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return root

    def test_write_missing_history_takes_no_file(self):
        # (a FILE was the JSON form before v0.27.0: refused, said)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._short_project(tmp)
            e = dict(os.environ, TAXJSON_OFFLINE="1")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "find-missing-history", "--write-missing-history",
                 "missing_history.json"], cwd=root, capture_output=True,
                text=True, env=e, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertFalse((root / "missing_history.json").exists())
            self.assertIn("takes no FILE", " ".join(r.stderr.split()))

    def test_renamed_short_names_the_broker_ticker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._short_project(tmp, "TOBASE XEIOLD.TO XEI.TO\n")
            r = _run_cli(root, "find-missing-history")
            self.assertIn("XEI.TO", r.stdout)
            self.assertIn("XEI.TO <- XEIOLD.TO", r.stdout)


class TestFetchAndWatch(unittest.TestCase):
    """S046-12 (watch --state paths), S046-16 (trim refuses a swallowed
    record), S046-17 (Questrade number masked)."""

    def test_watch_state_paths_are_one_line_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp)
            (root / "adir").mkdir()
            (root / "afile").write_text("x")
            for state, want in (("adir", "is a directory"),
                                ("afile/x.json", "cannot create")):
                with self.subTest(state=state):
                    r = _run_cli(root, "watch", "--state", state)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertIn(want, r.stderr)
            self.assertEqual(list((root).glob("*.part")), [])


class TestPins(unittest.TestCase):
    """S050-09 (taxjson-sort orders a day by clock time), G1-13 (the
    blended-pass conservation check warns on a gap, only on a gap)."""

    def test_sort_orders_same_day_rows_by_time(self):
        from taxjson.bin.taxjson_sort import sort_transactions
        from taxjson.lib.core import coerce_transaction_row
        rows = [{"date": "2025-03-03", "time": t, "action": "BUYSELL",
                 "symbol": "ETH", "quantity": 1, "price": 1.0,
                 "currency": "CAD", "net_amount": -1.0,
                 "description": d}
                for t, d in (("14:00:00", "late"), ("10:00:00", "early"),
                             ("09:00:00", "next-day"))]
        rows[2]["date"] = "2025-03-04"
        txs = [coerce_transaction_row(r, i, "t") for i, r in enumerate(rows)]
        got = [t.description for t in sort_transactions(txs)]
        self.assertEqual(got, ["early", "late", "next-day"])

    def test_blend_conservation_check(self):
        from taxjson.bin.taxjson_run import _blend_conservation_gaps
        blended = {"inventory": [{"symbol": "AEM.TO", "qty": 100},
                                 {"symbol": "XEI.TO", "qty": 50},
                                 {"symbol": "AEM.TO", "qty": 60,
                                  "account": "margin"}]}
        split = [{"inventory": [{"symbol": "AEM.TO", "qty": 60,
                                 "blended_pool": True},
                                {"symbol": "XEI.TO", "qty": 50,
                                 "blended_pool": True}]},
                 {"inventory": [{"symbol": "AEM.TO", "qty": 40,
                                 "blended_pool": True}]}]
        self.assertEqual(_blend_conservation_gaps(blended, split), [])
        split[1]["inventory"][0]["qty"] = 30
        gaps = _blend_conservation_gaps(blended, split)
        self.assertEqual(len(gaps), 1)
        self.assertIn("blended AEM.TO holds 100 but the per-account split "
                      "accounts for only 90", gaps[0])


if __name__ == "__main__":
    unittest.main()
