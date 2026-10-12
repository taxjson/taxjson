"""Test files never share a folder with another test's (or run's) files.

A Kraken trades export indexes every Kraken ledger in its folder and a
Webull export reads its other years (brokerages/detect.
same_broker_siblings). Tests used to write their CSVs straight into the
shared temp root, so a `kr_ledgers_*.csv` left there by an interrupted
run (or written meanwhile by another run on the machine) became a
sibling of every Kraken file the suite parsed, and five Kraken tests
failed with "Kraken ledger exports ... both carry ledger txid L1***
with DIFFERENT content". Every temp file now sits alone in its own
folder (tests/_tmpfiles.private_tmpfile).
"""
import ast
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _tmpfiles import private_dir, private_tmpfile

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent

# A temp-file call that lands in the shared temp root unless it names a
# folder; gettempdir() IS the shared root.
_NEEDS_DIR = {"NamedTemporaryFile", "mkstemp", "mktemp"}
_NEVER = {"gettempdir"}

# Two conflicting Kraken ledgers, as two interrupted runs left them.
_LEDGER_HEAD = "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
_STRAYS = {
    "kr_ledgers_strayA.csv":
        _LEDGER_HEAD + "L1,REFA,2024-03-01 10:00:00,deposit,,currency,XXBT,spot,0.5,0,0.5\n",
    "kr_ledgers_strayB.csv":
        _LEDGER_HEAD + "L1,REFB,2024-04-01 10:00:00,deposit,,currency,XETH,spot,0.7,0,0.7\n",
}
_TRADES = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
           "T1,O1,BTC/USD,2025-01-15 10:00:00.1234,buy,limit,600,60,1,0.1,,,\n")

# The tests that failed with the strays beside them.
_AFFECTED = (
    "test_parser_activity_coverage.TestKrakenActivities.test_btc_dai_pair_treated_as_fiat_quote",
    "test_parser_activity_coverage.TestKrakenActivities.test_trades_buy",
    "test_parser_activity_coverage.TestKrakenActivities.test_trades_csv_crypto_to_crypto_two_legs",
    "test_parser_activity_coverage.TestKrakenActivities.test_trades_sell",
    "test_parser_coverage_audit.TestKrakenFiatConversions.test_trades_csv_fiat_base_fills_are_non_events",
)


def _test_sources():
    yield from sorted(TESTS.rglob("*.py"))
    yield from sorted(REPO.glob("packages/*/tests/**/*.py"))


def _shared_root_calls(path):
    """(line, call) for each temp-file call in `path` that writes into
    the shared temp root."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name in _NEVER:
            out.append((node.lineno, name))
        elif name in _NEEDS_DIR and not any(k.arg == "dir" for k in node.keywords):
            out.append((node.lineno, name))
    return out


class TestNoSharedTempRoot(unittest.TestCase):
    def test_no_test_writes_into_the_shared_temp_root(self):
        bad = []
        for p in _test_sources():
            if p.name in ("_tmpfiles.py", "test_temp_isolation.py"):
                continue
            bad += [f"{p.relative_to(REPO)}:{ln}: {call}()"
                    for ln, call in _shared_root_calls(p)]
        self.assertEqual(
            bad, [],
            "these write into the shared temp root, where a parser's "
            "sibling discovery reads other tests' and runs' files; use "
            "_tmpfiles.private_tmpfile / tempfile.TemporaryDirectory():\n  "
            + "\n  ".join(bad))

    def test_private_tmpfile_is_alone_in_its_own_folder(self):
        with private_tmpfile("w", suffix=".csv", prefix="kr_ledgers_",
                             delete=False) as f:
            f.write(_LEDGER_HEAD)
        folder = Path(f.name).parent
        self.assertNotEqual(folder.resolve(),
                            Path(tempfile.gettempdir()).resolve())
        self.assertEqual(os.listdir(folder), [Path(f.name).name])
        with private_tmpfile("w", suffix=".csv") as g:
            self.assertNotEqual(Path(g.name).parent, folder)


class TestStrayLedgersInTheTempRoot(unittest.TestCase):
    """Two conflicting kr_ledgers_*.csv in TMPDIR, as interrupted runs
    leave them: the Kraken tests still pass."""

    def setUp(self):
        self.tmp = Path(private_dir())
        for name, text in _STRAYS.items():
            (self.tmp / name).write_text(text)

    def test_the_strays_break_a_trades_file_written_beside_them(self):
        # Control: the hazard is real. A trades export in the same
        # folder indexes both ledgers and refuses the conflict.
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        trades = self.tmp / "tmp_trades.csv"
        trades.write_text(_TRADES)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "both carry ledger txid"):
                KrakenBrokerage().parse_file(trades)

    def test_affected_tests_pass_with_strays_in_tmpdir(self):
        env = dict(os.environ, TMPDIR=str(self.tmp), TAXJSON_WIDTH="0")
        r = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", *_AFFECTED],
            cwd=TESTS, env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertIn(f"Ran {len(_AFFECTED)} test", r.stderr)
        # Nothing the tests wrote is left beside the strays.
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()
                                if p.suffix == ".csv"), sorted(_STRAYS))


if __name__ == "__main__":
    unittest.main()
