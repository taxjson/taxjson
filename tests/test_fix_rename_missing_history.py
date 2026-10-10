"""phantoms.json -> missing_history.json (owner-approved rename, 2026-10).

The project file listing sales whose purchase is not in the broker files
(bought before the data starts) is now missing_history.json. An existing
phantoms.json keeps working — read, with one rename NOTE per run — and a
project holding both names is refused. The old flags (--gen-phantoms,
--suggest-phantoms, --phantoms) still work and print a note; the old
module name (taxjson.lib.phantom_holdings) is a shim.

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
NOTE_ENV = "TAXJSON_MISSING_HISTORY_NOTED"
RENAME_NOTE = "mv phantoms.json missing_history.json"


def _env():
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.pop(NOTE_ENV, None)           # each subprocess is its own run
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


class TestLegacyFileName(unittest.TestCase):
    """Only phantoms.json: read exactly like missing_history.json, with
    one rename NOTE per run; the file is never renamed or edited."""

    @rule("CA-ACB-11")
    def test_run_reads_phantoms_json_with_one_note(self):
        with tempfile.TemporaryDirectory() as t_new, \
                tempfile.TemporaryDirectory() as t_old:
            new = _project(t_new, ["missing_history.json"])
            old = _project(t_old, ["phantoms.json"])
            before = (old / "phantoms.json").read_bytes()
            r_new = _run_cli(new, "run", "--no-input")
            r_old = _run_cli(old, "run", "--no-input")
            self.assertEqual(r_new.returncode, 0, r_new.stderr[-2000:])
            self.assertEqual(r_old.returncode, 0, r_old.stderr[-2000:])
            both = r_old.stdout + r_old.stderr
            self.assertEqual(both.count(RENAME_NOTE), 1, both[-3000:])
            self.assertNotIn(RENAME_NOTE, r_new.stdout + r_new.stderr)
            # The same books: the sale with no purchase is routed to
            # manual reporting either way.
            g_new, g_old = _gains(new), _gains(old)
            self.assertEqual(
                [m["symbol"] for m in g_old["manual_reporting_required"]],
                ["ZZZ.TO"])
            self.assertEqual(g_new["manual_reporting_required"],
                             g_old["manual_reporting_required"])
            self.assertEqual(g_new["summary"], g_old["summary"])
            # Never renamed or edited by taxjson.
            self.assertTrue((old / "phantoms.json").exists())
            self.assertFalse((old / "missing_history.json").exists())
            self.assertEqual((old / "phantoms.json").read_bytes(), before)
            # The gains file writes the log's new key.
            self.assertIn("missing_history_log", g_old)
            self.assertNotIn("phantom_application_log", g_old)

    def test_resolver_and_note_once(self):
        from taxjson.lib import missing_history as mh
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(mh.project_missing_history_file(root))
            self.assertEqual(mh.missing_history_path(root),
                             root / "missing_history.json")
            (root / "phantoms.json").write_text("[]")
            err = io.StringIO()
            with mock.patch.dict(os.environ), \
                    contextlib.redirect_stderr(err):
                os.environ.pop(NOTE_ENV, None)
                p1 = mh.project_missing_history_file(root)
                p2 = mh.project_missing_history_file(root)
            self.assertEqual(p1, root / "phantoms.json")
            self.assertEqual(p2, p1)
            self.assertEqual(err.getvalue().count(RENAME_NOTE), 1)
            (root / "phantoms.json").rename(root / "missing_history.json")
            with mock.patch.dict(os.environ), \
                    contextlib.redirect_stderr(io.StringIO()) as e2:
                os.environ.pop(NOTE_ENV, None)
                self.assertEqual(mh.project_missing_history_file(root),
                                 root / "missing_history.json")
            self.assertEqual(e2.getvalue(), "")

    def test_messages_name_the_file_the_user_has(self):
        from taxjson.lib.missing_history import (load_missing_history,
                                                  synthesize_openings)
        from taxjson.lib.core import TaxTransaction
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "phantoms.json"
            p.write_text(json.dumps([{"symbol": "NOPE.TO",
                                      "account": "margin"}]))
            pairs = load_missing_history(p)
        self.assertEqual(pairs, {("NOPE.TO", "margin")})
        tx = TaxTransaction(action="BUYSELL", date="2025-01-10",
                            time="09:30:00", symbol="ABC.TO",
                            quantity=-1, price=1.0, net_amount=1.0,
                            account="margin", currency="CAD",
                            date_settle="2025-01-13")
        _new, log = synthesize_openings([tx], pairs)
        self.assertIn("check the spelling in phantoms.json",
                      log[0]["note"])
        _new, log = synthesize_openings([tx], {("NOPE.TO", "margin")})
        self.assertIn("check the spelling in missing_history.json",
                      log[0]["note"])

    def test_renaming_the_file_is_not_an_input_change(self):
        from taxjson.lib import checklist
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, ["phantoms.json"])
            cfg = {"settings": {"year": 2025, "country": "canada"},
                   "accounts": {"margin": {"type": "taxable"}}}
            checklist.record_input_fingerprint(root, cfg)
            self.assertEqual(checklist.inputs_changed(root, cfg), "")
            (root / "phantoms.json").rename(root / "missing_history.json")
            self.assertEqual(checklist.inputs_changed(root, cfg), "")
            (root / "missing_history.json").write_text("[]")
            self.assertIn("missing_history.json",
                          checklist.inputs_changed(root, cfg))

    def test_old_applied_marker_still_invalidates(self):
        # A work/ dir an older taxjson built with phantoms.json carries
        # .phantoms_applied: removing the file still rebuilds the gains.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, ["missing_history.json"])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            marker = root / "work" / ".missing_history_applied"
            self.assertTrue(marker.exists())
            marker.rename(root / "work" / ".phantoms_applied")
            (root / "missing_history.json").unlink()
            r = _run_cli(root, "run", "--no-input", "--fast")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("missing_history.json removed", r.stdout)
            self.assertFalse((root / "work" / ".phantoms_applied").exists())
            self.assertFalse(_gains(root).get("manual_reporting_required"))


class TestBothFileNamesRefused(unittest.TestCase):
    """missing_history.json and phantoms.json together: which one is
    current cannot be guessed — every command refuses (exit 2)."""

    def test_run_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, ["missing_history.json", "phantoms.json"])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 2, r.stderr[-2000:])
            self.assertIn("both missing_history.json and phantoms.json",
                          r.stderr)
            self.assertIn("Keep one", r.stderr)
            self.assertFalse((root / "work" / "margin_gains.json").exists())
            # Neither file was touched.
            self.assertTrue((root / "phantoms.json").exists())
            self.assertTrue((root / "missing_history.json").exists())

    def test_find_missing_history_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            for n in ("missing_history.json", "phantoms.json"):
                (root / n).write_text(json.dumps(_ENTRY))
            r = _run_cli(root, "find-missing-history")
            self.assertEqual(r.returncode, 2, r.stderr[-2000:])
            self.assertIn("both missing_history.json and phantoms.json",
                          r.stderr)

    def test_library_raises(self):
        from taxjson.lib.missing_history import (MissingHistoryFileConflict,
                                                  project_missing_history_file)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "missing_history.json").write_text("[]")
            (root / "phantoms.json").write_text("[]")
            with self.assertRaises(MissingHistoryFileConflict):
                project_missing_history_file(root)


class TestWriteMissingHistory(unittest.TestCase):
    """find-missing-history --write-missing-history [FILE] (formerly
    --gen-phantoms FILE)."""

    def test_default_is_the_accounts_tt_lines(self):
        # (owner decision v0.27.0: dated OPENING ... cost=unknown lines
        # in inputs/<account>/missing_history.tt; FILE keeps the JSON)
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

    def test_refuses_to_write_beside_the_old_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, ["phantoms.json"])
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            r = _run_cli(root, "find-missing-history",
                         "--write-missing-history",
                         str(root / "missing_history.json"))
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(RENAME_NOTE, r.stderr)
            self.assertFalse((root / "missing_history.json").exists())

    def test_deprecated_gen_phantoms_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            out = root / "cand.json"
            r = _run_cli(root, "find-missing-history", "--gen-phantoms", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--gen-phantoms is now --write-missing-history",
                          r.stderr)
            self.assertEqual([e["symbol"] for e in
                              json.loads(out.read_text())], ["ZZZ.TO"])
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
            f = Path(tmp) / "missing_history.json"
            f.write_text(json.dumps(_ENTRY))
            r1 = _run_mod("taxjson.bin.taxjson_missing_history",
                          "--year", "2025", "--missing-history", f, base)
            r2 = _run_mod("taxjson.bin.taxjson_missing_history",
                          "--year", "2025", "--phantoms", f, base)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("COVERED by missing_history.json", r1.stdout)
            self.assertEqual(r1.stdout, r2.stdout)
            self.assertIn("--phantoms is now --missing-history", r2.stderr)


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
