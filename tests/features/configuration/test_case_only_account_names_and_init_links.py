"""GitHub issues #45, #46, #47, #48, #50, #51, #52: init writing through
an inputs/ link to outside the project, an empty fetched holdings
snapshot, a snapshot's broker id next to a local alias, account names
differing only by case, the election listing of a longer-named sibling
account, and command chaining (a `-C DIR` named like a command; an
account or folder named like a command). Synthetic data only.

Formerly: test_fix_issues_cli"""
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


class TestInitThroughLinkOutside(unittest.TestCase):
    """#45: init checks the write boundary before writing anything.

    Formerly: test_fix_issues_cli.TestInitThroughLinkOutside"""

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


class TestCaseOnlyAccountNames(unittest.TestCase):
    """#48: account names that differ only by case are refused.

    Formerly: test_fix_issues_cli.TestCaseOnlyAccountNames"""

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
