"""phantoms.json and missing_history.json: no longer read (v0.27.0).

The project file that listed sales whose purchase is not in the broker
files (bought before the data starts) — phantoms.json, renamed
missing_history.json in 2026-10 — is replaced by dated .tt lines
(`OPENING <date> <SYMBOL> <qty> cost=unknown`). Either name stops every
command naming `taxjson migrate`, which converts it; a project holding
both is refused by migrate too. --gen-phantoms FILE is refused like
--write-missing-history FILE; --suggest-phantoms and --phantoms still
work; the old module name (taxjson.lib.phantom_holdings) is a shim.

Synthetic data only: fake account numbers (55500001 # pii-ok), all-CAD
Questrade books, no FX fetch.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env():
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    return e


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *map(str, args)], cwd=REPO_ROOT, capture_output=True, text=True,
        env=_env(), stdin=subprocess.DEVNULL)


def _run_mod(mod, *args):
    return subprocess.run([sys.executable, "-m", mod, *map(str, args)],
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          env=_env(), stdin=subprocess.DEVNULL)


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


def _qt(date, settle, action, sym, qty, price, net):
    gross = -net if action == "Buy" else net
    return (f"{date} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},"
            f"{sym} CORP,{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,"
            f"55500001,Trades,Individual\n")      # pii-ok


# ZZZ.TO: 100 sold with no purchase in the data (bought before it), 100
# bought back. XEI.TO: a clean round trip.
_CSV = _QT_HEADER + (
    _qt("2025-01-10", "2025-01-13", "Sell", "ZZZ.TO", -100, 30.0, 3000.0)
    + _qt("2025-03-03", "2025-03-04", "Buy", "ZZZ.TO", 100, 20.0, -2000.0)
    + _qt("2025-02-03", "2025-02-04", "Buy", "XEI.TO", 10, 10.0, -100.0)
    + _qt("2025-06-02", "2025-06-03", "Sell", "XEI.TO", -10, 12.0, 120.0))

_ENTRY = [{"symbol": "ZZZ.TO", "account": "margin"}]


def _project(tmp, files=()):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_CONFIG)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(_CSV)
    for name in files:
        (root / name).write_text(json.dumps(_ENTRY))
    return root


def _gains(root):
    return json.loads((root / "work" / "margin_gains.json").read_text())


class TestOldFileNamesRefused(unittest.TestCase):
    """phantoms.json (or missing_history.json): every command stops,
    naming `taxjson migrate`, which converts it; both names: migrate
    refuses too (which one is current cannot be guessed)."""

    @rule("CA-ACB-11")
    def test_phantoms_json_refused_then_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            (root / "phantoms.json").write_text(json.dumps(_ENTRY))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 2, r.stderr[-2000:])
            self.assertIn("phantoms.json is no longer read",
                          " ".join(r.stderr.split()))
            r = _run_cli(root, "migrate")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((root / "phantoms.json").exists())
            self.assertTrue((root / "phantoms.json.migrated").exists())
            tt = (root / "inputs" / "margin" /
                  "missing_history.tt").read_text()
            self.assertIn("OPENING 2025-01-09 ZZZ.TO 100 cost=unknown", tt)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            man = _gains(root).get("manual_reporting_required") or []
            self.assertEqual([m["symbol"] for m in man], ["ZZZ.TO"])

    def test_both_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, ["missing_history.json", "phantoms.json"])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 2, r.stderr[-2000:])
            self.assertIn("missing_history.json", r.stderr)
            self.assertIn("phantoms.json", r.stderr)
            self.assertIn("taxjson migrate", r.stderr)
            r = _run_cli(root, "migrate")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("both missing_history.json and phantoms.json",
                          " ".join(r.stderr.split()))
            # Neither file was touched.
            self.assertTrue((root / "phantoms.json").exists())
            self.assertTrue((root / "missing_history.json").exists())

    def test_library_raises(self):
        from taxjson.lib.missing_history import (MissingHistoryFileConflict,
                                                  legacy_missing_history_file)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "missing_history.json").write_text("[]")
            (root / "phantoms.json").write_text("[]")
            with self.assertRaises(MissingHistoryFileConflict):
                legacy_missing_history_file(root)


class TestWriteMissingHistory(unittest.TestCase):
    """find-missing-history --write-missing-history: .tt lines; a FILE
    (and the old --gen-phantoms FILE) is refused."""

    def test_default_is_the_accounts_tt_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            r = _run_cli(root, "find-missing-history",
                         "--write-missing-history")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((root / "missing_history.json").exists())
            out = root / "inputs" / "margin" / "missing_history.tt"
            self.assertEqual([ln.split()[2] for ln in
                              out.read_text().splitlines()
                              if ln.startswith("OPENING ")], ["ZZZ.TO"])
            self.assertIn("`taxjson run` reads them", r.stderr)
            self.assertIn("no purchase in your", r.stderr)
            self.assertNotIn("phantom", r.stderr.lower())

    def test_gen_phantoms_file_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            out = root / "cand.json"
            r = _run_cli(root, "find-missing-history", "--gen-phantoms", out)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("takes no FILE", " ".join(r.stderr.split()))
            self.assertFalse(out.exists())
            h = _run_cli(root, "find-missing-history", "--help")
            self.assertIn("--write-missing-history", h.stdout)
            self.assertNotIn("--gen-phantoms", h.stdout)


def _base_file(tmp):
    from taxjson.lib.core import TaxTransaction
    base = Path(tmp) / "margin_base.json"
    t = TaxTransaction(action="BUYSELL", date="2025-01-10", time="09:30:00",
                       symbol="ZZZ.TO", quantity=-100, price=30.0,
                       net_amount=3000.0, account="margin", currency="CAD",
                       date_settle="2025-01-13")
    base.write_text(json.dumps({"transactions": [t.to_dict()]}))
    return base


class TestDeprecatedStandaloneFlags(unittest.TestCase):

    def test_suggest_phantoms_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _base_file(tmp)
            new = Path(tmp) / "a.json"
            old = Path(tmp) / "b.json"
            r1 = _run_mod("taxjson.bin.taxjson_gains", "--country", "canada",
                          "--suggest-missing-history", new, base)
            r2 = _run_mod("taxjson.bin.taxjson_gains", "--country", "canada",
                          "--suggest-phantoms", old, base)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertNotIn("is now", r1.stderr)
            self.assertIn("--suggest-phantoms is now "
                          "--suggest-missing-history", r2.stderr)
            self.assertEqual(json.loads(new.read_text()),
                             json.loads(old.read_text()))
            self.assertEqual([e["symbol"] for e in
                              json.loads(new.read_text())], ["ZZZ.TO"])
            h = _run_mod("taxjson.bin.taxjson_gains", "--help")
            self.assertIn("--suggest-missing-history", h.stdout)
            self.assertNotIn("--suggest-phantoms", h.stdout)

    def test_missing_history_tool_phantoms_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _base_file(tmp)
            proj = Path(tmp) / "p"
            (proj / "inputs" / "margin").mkdir(parents=True)
            (proj / "taxjson.toml").write_text(_CONFIG)
            (proj / "inputs" / "margin" / "missing_history.tt").write_text(
                "OPENING 2025-01-09 ZZZ.TO 100 cost=unknown\n")
            r1 = _run_mod("taxjson.bin.taxjson_missing_history",
                          "--year", "2025", "--missing-history", proj, base)
            r2 = _run_mod("taxjson.bin.taxjson_missing_history",
                          "--year", "2025", "--phantoms", proj, base)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("COVERED by missing_history.tt", r1.stdout)
            self.assertEqual(r1.stdout, r2.stdout)


class TestOldModuleName(unittest.TestCase):

    def test_shim_is_the_same_module(self):
        import taxjson.lib.missing_history as mh
        import taxjson.lib.phantom_holdings as ph
        from taxjson.lib.phantom_holdings import (PhantomCandidate,
                                                  detect_phantoms,
                                                  load_phantoms,
                                                  report_phantom_log,
                                                  stale_phantom_entries,
                                                  StalePhantomEntry)
        self.assertIs(ph, mh)
        self.assertIs(detect_phantoms, mh.detect_missing_history)
        self.assertIs(load_phantoms, mh.load_missing_history)
        self.assertIs(PhantomCandidate, mh.MissingHistoryCandidate)
        self.assertIs(StalePhantomEntry, mh.StaleMissingHistoryEntry)
        self.assertIs(stale_phantom_entries,
                      mh.stale_missing_history_entries)
        self.assertIs(report_phantom_log, mh.report_missing_history_log)

    def test_old_keyword_of_synthesize_openings(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.missing_history import synthesize_openings
        tx = TaxTransaction(action="BUYSELL", date="2025-01-10",
                            time="09:30:00", symbol="ZZZ.TO",
                            quantity=-100, price=30.0, net_amount=3000.0,
                            account="margin", currency="CAD",
                            date_settle="2025-01-13")
        a, la = synthesize_openings([tx], {("ZZZ.TO", "margin")})
        b, lb = synthesize_openings([tx], phantoms={("ZZZ.TO", "margin")})
        self.assertEqual(la, lb)
        self.assertEqual(len(a), len(b))
        self.assertEqual(lb[0]["opening_qty"], 100.0)


class TestSplitGainsReadsTheOldLogKey(unittest.TestCase):

    def test_old_and_new_key(self):
        from taxjson.bin.taxjson_split_gains import _mh_log
        e = [{"account": "a", "symbol": "Q.US"}]
        self.assertEqual(_mh_log({"phantom_application_log": e}), e)
        self.assertEqual(_mh_log({"missing_history_log": e}), e)
        self.assertEqual(_mh_log({}), [])


if __name__ == "__main__":
    unittest.main()
