"""GitHub issues #45, #46, #47, #48, #50, #51, #52: init writing through
an inputs/ link to outside the project, an empty fetched holdings
snapshot, a snapshot's broker id next to a local alias, account names
differing only by case, the election listing of a longer-named sibling
account, and command chaining (a `-C DIR` named like a command; an
account or folder named like a command). Synthetic data only."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from taxjson.lib.dispatch import python_module_argv  # -P only on 3.11+

REPO = Path(__file__).resolve().parents[1]
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


def _split(argv):
    from taxjson.bin import taxjson_run as R
    p, sub = R._build_parser()
    return R._split_command_segments(p, argv, set(sub.choices))


class TestChainGlobalOptionValue(unittest.TestCase):
    """#51: the value of `-C DIR` is never the first command."""

    def test_dir_named_like_a_command(self):
        self.assertEqual(_split(["-C", "run", "run", "sum"]),
                         [["-C", "run", "run"], ["-C", "run", "sum"]])
        self.assertEqual(_split(["--dir", "sum", "run", "sum"]),
                         [["--dir", "sum", "run"], ["--dir", "sum", "sum"]])
        self.assertEqual(_split(["--dir=run", "run", "sum"]),
                         [["--dir=run", "run"], ["--dir=run", "sum"]])
        self.assertEqual(_split(["-Crun", "run", "sum"]),
                         [["-Crun", "run"], ["-Crun", "sum"]])

    def test_dir_alone_is_the_help_page(self):
        from taxjson.bin import taxjson_run as R
        p, sub = R._build_parser()
        self.assertTrue(R._no_command(["-C", "run"], set(sub.choices)))
        self.assertFalse(R._no_command(["-C", "run", "sum"],
                                       set(sub.choices)))

    def test_cli_chain_in_a_project_named_run(self):
        top = _tmp(self)
        _project(top / "run", trades=(
            "BUYSELL 2024-01-10 09:30:00 QZZQ.TO 10 CAD 10 100 0\n"
            "BUYSELL 2024-05-10 09:30:00 QZZQ.TO -10 CAD 12 120 0\n"))
        r = _cli("-C", "run", "run", "--no-input", "sum", "--json", cwd=top)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout[r.stdout.rindex("\n{") + 1:])
        self.assertAlmostEqual(doc["totals"]["realized"], 20.0, places=2)


class TestChainNamesLikeCommands(unittest.TestCase):
    """#52: an account or folder named like a command is addressable;
    a bare command name starts a new command only where the previous
    command cannot take it, and `--` always separates."""

    def test_account_named_like_a_command_is_the_argument(self):
        root = _project(_tmp(self) / "p", accounts=("margin", "sum"))
        d = ["-C", str(root)]
        self.assertEqual(_split(d + ["events", "sum"]),
                         [d + ["events", "sum"]])
        self.assertEqual(_split(d + ["events", "2024", "sum"]),
                         [d + ["events", "2024", "sum"]])
        self.assertEqual(_split(d + ["sanity", "sum"]),
                         [d + ["sanity", "sum"]])
        self.assertEqual(_split(d + ["fetch", "margin", "sum"]),
                         [d + ["fetch", "margin", "sum"]])
        # `--` is the explicit separator.
        self.assertEqual(_split(d + ["events", "--", "sum"]),
                         [d + ["events"], d + ["sum"]])
        # A later command after the account still chains.
        self.assertEqual(_split(d + ["events", "sum", "run"]),
                         [d + ["events", "sum"], d + ["run"]])

    def test_command_name_that_is_no_account_chains(self):
        root = _project(_tmp(self) / "p")
        d = ["-C", str(root)]
        self.assertEqual(_split(d + ["events", "sum"]),
                         [d + ["events"], d + ["sum"]])
        self.assertEqual(_split(d + ["fetch", "run"]),
                         [d + ["fetch"], d + ["run"]])
        self.assertEqual(_split(d + ["sanity", "run", "--no-input"]),
                         [d + ["sanity"], d + ["run", "--no-input"]])
        self.assertEqual(_split(d + ["run", "--fast", "sum", "--json"]),
                         [d + ["run", "--fast"], d + ["sum", "--json"]])
        self.assertEqual(_split(d + ["run", "close-year", "check-filed"]),
                         [d + ["run"], d + ["close-year"],
                          d + ["check-filed"]])
        # A date positional never takes a command name.
        self.assertEqual(_split(d + ["list", "margin", "sum"]),
                         [d + ["list", "margin"], d + ["sum"]])

    def test_folder_or_symbol_named_like_a_command(self):
        self.assertEqual(_split(["init", "--country", "canada", "sum"]),
                         [["init", "--country", "canada", "sum"]])
        self.assertEqual(_split(["init", "sum", "--", "run"]),
                         [["init", "sum"], ["run"]])
        self.assertEqual(_split(["audit", "sum"]), [["audit", "sum"]])

    def test_cli_events_for_an_account_named_sum(self):
        root = _project(_tmp(self) / "p", accounts=("margin", "sum"))
        d = root / "inputs" / "sum"
        d.mkdir(parents=True)
        (d / "trades.tt").write_text(
            "BUYSELL 2024-01-10 09:30:00 QZZQ.TO 10 CAD 10 100 0\n")
        r = _cli("-C", str(root), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _cli("-C", str(root), "events", "sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("QZZQ.TO", r.stdout)
        self.assertNotIn("REALIZED-GAINS SUMMARY", r.stdout)
        # `--` chains: the events, then the gains summary.
        r = _cli("-C", str(root), "events", "--", "sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REALIZED-GAINS SUMMARY", r.stdout)

    def test_cli_init_into_a_folder_named_sum(self):
        top = _tmp(self)
        r = _cli("init", "--country", "canada", "--year", "2024", "sum",
                 cwd=top)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((top / "sum" / "2024" / "taxjson.toml").is_file())
        self.assertFalse((top / "2024").exists())


class TestInitThroughLinkOutside(unittest.TestCase):
    """#45: init checks the write boundary before writing anything."""

    def test_inputs_link_outside_is_refused(self):
        top = _tmp(self)
        proj, outside = top / "proj", top / "outside"
        proj.mkdir()
        outside.mkdir()
        (proj / "inputs").symlink_to(outside, target_is_directory=True)
        r = _cli("init", "--country", "canada", "--year", "2024",
                 str(proj))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("inputs/", r.stderr)
        self.assertIn("outside", " ".join(r.stderr.split()))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(sorted(p.name for p in proj.iterdir()), ["inputs"])

    def test_single_and_year_folder_links_are_refused(self):
        top = _tmp(self)
        outside = top / "outside"
        outside.mkdir()
        proj = top / "single"
        proj.mkdir()
        (proj / "inputs").symlink_to(outside, target_is_directory=True)
        r = _cli("init", "--single", "--country", "canada", "--year",
                 "2024", str(proj))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse((proj / "taxjson.toml").exists())
        proj = top / "years"
        proj.mkdir()
        (proj / "2024").symlink_to(outside, target_is_directory=True)
        r = _cli("init", "--country", "canada", "--year", "2024",
                 str(proj))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_link_inside_the_project_is_kept(self):
        proj = _tmp(self) / "proj"
        (proj / "data" / "inputs").mkdir(parents=True)
        (proj / "inputs").symlink_to("data/inputs", target_is_directory=True)
        r = _cli("init", "--country", "canada", "--year", "2024",
                 str(proj))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any((proj / "data" / "inputs").iterdir()))


_EMPTY_FETCHED = (
    '# Live Questrade holdings snapshot — taxjson fetch/verify.\n'
    '[meta]\naccount = "margin"\nbroker_account = "99900001"\n'  # pii-ok
    'source = "questrade-api"\ngenerated_at = "2024-12-31T20:00:00Z"\n'
    'holdings_count = 0\n')


class TestEmptySnapshot(unittest.TestCase):
    """#46: a snapshot of an account that holds nothing."""

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
        from taxjson.lib.holdings_dir import empty_snapshot
        from taxjson.lib.positions_reports import (PositionsReportError,
                                                   read_positions)
        from taxjson.lib.tomlcompat import tomllib
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
    alias its [meta] account carries."""

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


class TestCaseOnlyAccountNames(unittest.TestCase):
    """#48: account names that differ only by case are refused."""

    def test_config_problem(self):
        from taxjson.lib.config_check import account_type_problems
        cfg = {"settings": {"country": "canada"},
               "accounts": {n: {"type": "taxable"}
                            for n in ("Margin", "margin")}}
        problems = account_type_problems(cfg)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("Margin", problems[0])
        self.assertIn("margin", problems[0])
        self.assertIn("case", problems[0])
        cfg["accounts"] = {"margin": {"type": "taxable"},
                           "margin_us": {"type": "taxable"}}
        self.assertEqual(account_type_problems(cfg), [])

    def test_run_refuses(self):
        root = _project(_tmp(self) / "p", accounts=("Margin", "margin"))
        r = _cli("-C", str(root), "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("differ only", " ".join(r.stderr.split()))


if __name__ == "__main__":
    unittest.main()
