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


if __name__ == "__main__":
    unittest.main()
