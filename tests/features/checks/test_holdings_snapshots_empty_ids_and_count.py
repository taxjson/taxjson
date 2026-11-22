"""GitHub issues #45, #46, #47, #48, #50, #51, #52: init writing through
an inputs/ link to outside the project, an empty fetched holdings
snapshot, a snapshot's broker id next to a local alias, account names
differing only by case, the election listing of a longer-named sibling
account, and command chaining (a `-C DIR` named like a command; an
account or folder named like a command). Synthetic data only.

Formerly: test_fix_issues_cli"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO
SRC = str(REPO / "src")

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TAXJSON_OFFLINE"] = "1"
    return env


def _cli(*args, cwd=None):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", args),
        cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env=_env())


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


def _config(accounts=("margin",)):
    return ('[settings]\ncountry = "canada"\nyear = 2024\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            + "".join(f'[accounts.{a}]\ntype = "taxable"\n'
                      for a in accounts))


def _project(root: Path, accounts=("margin",), trades=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_config(accounts))
    (root / "ticker.map").write_text("")
    if trades is not None:
        d = root / "inputs" / accounts[0]
        d.mkdir(parents=True, exist_ok=True)
        (d / "trades.tt").write_text(trades)
    return root


_EMPTY_FETCHED = (
    '# Live Questrade holdings snapshot — tjs fetch/verify.\n'
    '[meta]\naccount = "margin"\nbroker_account = "99900001"\n'  # pii-ok
    'source = "questrade-api"\ngenerated_at = "2024-12-31T20:00:00Z"\n'
    'holdings_count = 0\n')


class TestEmptySnapshot(unittest.TestCase):
    """#46: a snapshot of an account that holds nothing.

    Formerly: test_fix_issues_cli.TestEmptySnapshot"""

    def test_sanity_accepts_an_empty_snapshot(self):
        root = _project(_tmp(self) / "p", trades=(
            "BUYSELL 2024-01-10 09:30:00 QZZQ.TO 10 CAD 10 100 0\n"
            "BUYSELL 2024-05-10 09:30:00 QZZQ.TO -10 CAD 12 120 0\n"))
        self.assertEqual(_cli("-C", str(root), "run", "--no-input")
                         .returncode, 0)
        (root / "holdings").mkdir()
        (root / "holdings" / "margin_live_holdings.toml").write_text(
            _EMPTY_FETCHED)
        r = _cli("-C", str(root), "sanity", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("no [[holding]]", r.stderr)

    def test_readers_accept_it_and_still_refuse_a_typo(self):
        from taxjson.lib.positions_reports import empty_snapshot
        from taxjson.lib.positions_reports import (PositionsReportError,
                                                   read_positions)
        import tomllib
        d = _tmp(self)
        good = d / "empty.toml"
        good.write_text(_EMPTY_FETCHED)
        self.assertTrue(empty_snapshot(tomllib.loads(_EMPTY_FETCHED)))
        rep = read_positions(good)
        self.assertEqual(len(rep.rows), 0)
        for text in ('[meta]\naccount = "x"\n[[holdings]]\nsymbol = "Q"\n',
                     '[meta]\naccount = "x"\nholdings_count = 2\n',
                     'title = "not a snapshot"\n'):
            self.assertFalse(empty_snapshot(tomllib.loads(text)), text)
            bad = d / "bad.toml"
            bad.write_text(text)
            with self.assertRaises(PositionsReportError):
                read_positions(bad)


class TestSnapshotBrokerId(unittest.TestCase):
    """#47: the snapshot's broker_account claims it, whatever local
    alias its [meta] account carries.

    Formerly: test_fix_issues_cli.TestSnapshotBrokerId"""

    def test_broker_account_wins_over_the_alias(self):
        from taxjson.lib import holdings_dir as HD
        d = _tmp(self)
        (d / "download.toml").write_text(
            '[meta]\naccount = "old_name"\n'
            'broker_account = "99900001"\n'  # pii-ok
            '[[holding]]\nsymbol = "QZZQ.TO"\nquantity = 2\n')
        found, notes = HD.discover(
            d, {"new_name": {"account": "99900001"}})  # pii-ok
        self.assertEqual(list(found), ["new_name"], notes)
        self.assertEqual(notes, [])

    def test_meta_account_id_still_claims(self):
        from taxjson.lib import holdings_dir as HD
        d = _tmp(self)
        (d / "download.toml").write_text(
            '[meta]\naccount = "U5550001"\n'  # pii-ok
            '[[holding]]\nsymbol = "QZZQ.TO"\nquantity = 2\n')
        found, _ = HD.discover(d, {"ib": {"account": "U5550001"}})  # pii-ok
        self.assertEqual(list(found), ["ib"])



class TestRunSummaryCountsDiscoveredSnapshots(unittest.TestCase):
    """The run's closing "N accounts with positions not checked against
    the broker's holdings" line counts a holdings/ snapshot that sanity
    compares (found by broker id or file name), not only `holdings =
    [...]` in taxjson.toml.

    Formerly: test_fix_issues_cli.TestRunSummaryCountsDiscoveredSnapshots"""

    _BUY = "BUYSELL 2024-01-10 09:30:00 QZZQ.TO 10 CAD 10 100 0\n"

    def _run(self, snapshots):
        root = _project(_tmp(self) / "p", accounts=("margin", "tfsa"),
                        trades=self._BUY)
        cfg = root / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            '[accounts.margin]\ntype = "taxable"\n',
            '[accounts.margin]\ntype = "taxable"\n'
            'account = "99900001"\n'))  # pii-ok
        (root / "inputs" / "tfsa").mkdir(parents=True)
        (root / "inputs" / "tfsa" / "t.tt").write_text(
            self._BUY.replace("QZZQ", "QZZR"))
        if snapshots:
            (root / "holdings").mkdir()
            (root / "holdings" / "download.toml").write_text(
                '[meta]\nbroker_account = "99900001"\n'  # pii-ok
                '[[holding]]\nsymbol = "QZZQ.TO"\nquantity = 10\n')
        r = _cli("-C", str(root), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads((root / "reports" / "run_summary.json")
                         .read_text())
        return ([u["account"] for u in doc["unchecked_accounts"]],
                " ".join((r.stdout + r.stderr).split()))

    def test_discovered_snapshot_is_checked(self):
        unchecked, out = self._run(snapshots=True)
        self.assertEqual(unchecked, ["tfsa"])
        self.assertIn("1 account with open positions and no holdings file "
                      "to check them against: tfsa (1)", out)

    def test_no_snapshot_is_listed(self):
        unchecked, out = self._run(snapshots=False)
        self.assertEqual(unchecked, ["margin", "tfsa"])
        self.assertIn("2 accounts with open positions and no holdings "
                      "file to check them against: margin (1), tfsa (1)",
                      out)


if __name__ == "__main__":
    unittest.main()
