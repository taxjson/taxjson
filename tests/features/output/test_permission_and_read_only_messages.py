"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


# ------------------------------------------------------------- TJ-0418

KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
          "margin,misc,ledgers\n")
# ZZC: bought 1.0, sold 0.9999999 (a sale of "all" leaving 1e-07 units:
# rounding residue). ZZD: 2.5 units still held.
KR = (KR_HDR
      + "TXA1,OA1,ZZC/CAD,2025-01-15 10:00:00.1234,buy,limit,40,"
        "40,0,1.0,,,\n"
      + "TXA2,OA2,ZZD/CAD,2025-02-15 10:00:00.1234,buy,limit,10,"
        "25,0,2.5,,,\n"
      + "TXA3,OA3,ZZC/CAD,2025-03-15 10:00:00.1234,sell,limit,50,"
        "49.999995,0,0.9999999,,,\n")


def _kraken_project(td):
    root = Path(td)
    (root / "inputs" / "kr1").mkdir(parents=True)
    (root / "inputs" / "kr1" / "kr_trades.csv").write_text(KR)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.kr1]\ntype = "taxable"\ncrypto = true\n')
    return root


# ------------------------------------------------------------- TJ-0414

def _tjs(*args, cwd=REPO_ROOT):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(args)),
        cwd=cwd, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


# ------------------------------------------------- TJ-0234/0235/0236

_GEN_MAP = """\
[columns]
date = "Date"
action = "Transaction type"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"

[formats]
date = "%m/%d/%Y"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"""
_GEN_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
            "01/15/2025,BUY,ZZD,10,{price},100.00,CAD\n")
_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")


def _brokerage(files, broker, name):
    """taxjson-brokerage on `name`, `files` alone in a folder of their
    own: the CompletedProcess."""
    with tempfile.TemporaryDirectory() as td:
        for n, text in files.items():
            (Path(td) / n).write_text(text)
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                   TAXJSON_LOCAL_TZ="America/Toronto")
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", broker, "--account", "x",
                                str(Path(td) / name)]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)


def _error_line(r):
    return next(x for x in r.stderr.splitlines() if "error:" in x)


class TestParserRefusalsNameTheFileOnce(unittest.TestCase):
    """TJ-0234 / TJ-0236: a refusal that names its file after the
    parser's label ("generic importer: ws.csv line 2: …", "Kraken
    trades kr.csv txid=…: …") was prefixed with the file again.

    Formerly: test_fix_1029_messages.TestParserRefusalsNameTheFileOnce"""

    def test_generic_refusal(self):
        r = _brokerage({"ws.csv": _GEN_CSV.format(price="broken"),
                        "ws.csv.toml": _GEN_MAP}, "generic", "ws.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = _error_line(r)
        self.assertIn("error: generic importer: ws.csv line 2: ", line)
        self.assertEqual(line.count("ws.csv"), 1, line)

    def test_kraken_unparseable_cost(self):
        rows = ("T1,O1,XBT/USD,2024-02-10 14:25:18,buy,limit,40000,"
                "4x00,8,0.1,,,\n")
        r = _brokerage({"kr.csv": _KT_H + rows}, "kraken", "kr.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = _error_line(r)
        self.assertIn("error: Kraken trades kr.csv txid=", line)
        self.assertEqual(line.count("kr.csv"), 1, line)

    def test_a_message_naming_no_file_is_still_prefixed(self):
        from taxjson.bin.taxjson_brokerage import _names_its_file
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ws.csv"
            f.write_text("x")
            self.assertFalse(_names_its_file(
                "generic importer: unparseable price value in the "
                "ws.csv.bak copy", f))
            self.assertFalse(_names_its_file(
                "generic importer: ws.csvx line 2: x", f))
            self.assertTrue(_names_its_file("ws.csv: x", f))


# ------------------------------------------------------- TJ-0218, TJ-0219

_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _locked(path, mode):
    """chmod `path` to `mode`; the cleanup puts 0o755 / 0o644 back."""
    path.chmod(mode)

    def _unlock():
        if path.exists():
            path.chmod(0o755 if path.is_dir() else 0o644)
    return _unlock


@unittest.skipIf(_ROOT, "root ignores permissions")
class TestPermissionMessagesNameTheFolderAndFix(unittest.TestCase):
    """TJ-0218: a write refused in a read-only folder gave the reason
    alone ("cannot write checklist.json: Permission denied") or, where
    no writer worded it, "permission denied: /abs/path". The file is
    named from where the command was run, with the `chmod` to run.

    Formerly: test_fix_1029_messages.TestPermissionMessagesNameTheFolderAndFix"""

    def test_checklist_done_in_a_read_only_project(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            _kraken_project(proj)
            self.addCleanup(_locked(proj, 0o555))
            r = _tjs("-C", "proj", "checklist", "--done", "1", cwd=td)
            self.assertFalse((proj / "checklist.json").exists())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        text = " ".join(r.stderr.split())
        self.assertIn("cannot write proj/checklist.json: Permission denied "
                      "— nothing was written", text)
        self.assertIn("The folder proj/ is not writable: `chmod u+w proj`.",
                      text)

    def test_an_unworded_write_names_the_folder_not_the_temp_file(self):
        from taxjson.lib.cli_diag import describe_input_error
        with tempfile.TemporaryDirectory() as td:
            ro = Path(td) / "rodir"
            ro.mkdir()
            self.addCleanup(_locked(ro, 0o555))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            e = PermissionError(13, "Permission denied",
                                str(ro / "x.json.ab12CD34.part"))
            self.assertEqual(
                describe_input_error(e),
                "cannot write rodir/x.json: Permission denied — The folder "
                "rodir/ is not writable: `chmod u+w rodir`")

    def test_an_unreadable_file_names_chmod_u_r(self):
        from taxjson.lib.cli_diag import describe_input_error
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "sub" / "zz.csv"
            f.parent.mkdir()
            f.write_text("a\n")
            self.addCleanup(_locked(f, 0o200))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            e = PermissionError(13, "Permission denied", str(f))
            self.assertEqual(
                describe_input_error(e),
                "cannot read sub/zz.csv: Permission denied — `chmod u+r "
                "sub/zz.csv` lets taxjson read it")


@unittest.skipIf(_ROOT, "root ignores permissions")
class TestReadOnlyRefusalNamesThePath(unittest.TestCase):
    """TJ-0219: a read-only ticker.map refused by format-map (and
    ticker-map --suggest --write) said `chmod u+w ticker.map`, the bare
    file name, wrong when run with -C from elsewhere.

    Formerly: test_fix_1029_messages.TestReadOnlyRefusalNamesThePath"""

    def test_format_map_from_elsewhere(self):
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "demo"
            r = _tjs("init", "--demo", str(demo))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            tm = demo / "2024" / "ticker.map"
            before = tm.read_text() + "\nZZQ   ZZQ.TO\n"
            tm.write_text(before)
            self.addCleanup(_locked(tm, 0o444))
            r = _tjs("-C", "demo/2024", "format-map", "--write", cwd=td)
            self.assertEqual(tm.read_text(), before)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        text = " ".join(r.stderr.split())
        self.assertIn("cannot write demo/2024/ticker.map: the file is "
                      "read-only", text)
        self.assertIn("`chmod u+w demo/2024/ticker.map` lets taxjson "
                      "rewrite it.", text)
        self.assertNotIn("`chmod u+w ticker.map`", text)

    def test_the_refusal_reason_names_the_path_from_here(self):
        from taxjson.lib.safe_write import ReadOnlyFileError, \
            refuse_read_only
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "y" / "slips.toml"
            f.parent.mkdir()
            f.write_text("")
            self.addCleanup(_locked(f, 0o444))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            with self.assertRaises(ReadOnlyFileError) as cm:
                refuse_read_only(f)
        self.assertIn("`chmod u+w y/slips.toml` lets taxjson rewrite it",
                      cm.exception.strerror)


if __name__ == "__main__":
    unittest.main()
