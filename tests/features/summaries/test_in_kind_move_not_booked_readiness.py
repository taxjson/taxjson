"""Backlog group `runcon`: the run's console output, run-state messages
and closing list.

TJ-0034  a misspelled local_timezone stopped the run with "has no
         local_timezone" and never named the typo (the checklist said
         the same); a first project's configure step offered `tjs
         migrate` and `tjs align --from <year-1>`, both failing.
TJ-0095  an in-kind contribution that could not be valued was NOT
         booked, yet `tjs sum` had no `!` line and the checklist marked
         the transfers step done.
TJ-0109  one "inputs changed (changed: ...; added: ...)" problem was
         split at "; " into two run-state problems.
TJ-0130  crypto only: `tjs gains` said "run `tjs run` first" after a
         successful run, and exited 1.
TJ-0133  in the years layout the run named the year folder's inputs/
         instead of ../inputs/ in its skip and nothing-computed lines.
TJ-0154  a full run after `run --account` opened with a stale-wash
         warning per account telling the user to run `tjs run`.
TJ-0155  empty project: `tjs sum` said "Run `tjs run` first" right
         after that run said nothing was computed.
TJ-0162  crypto-only use of the scaffold warned about
         option_grant_timing_since.
TJ-0163  the run's console showed "taxjson-apply-distributions: applied
         2 adjustment(s) to margin_base.json.stage."
TJ-0165  (the part this group owns) an option the broker never coded
         CLOSING is warned about once per run.
TJ-0177  "Info: holdings changed since the last run (N):" was a
         multi-line message in the run's default view.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_backlog_runcon
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str, width: str = "0"
         ) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH=width)
    env.pop("TAXJSON_LOCAL_TZ", None)
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


def _project(root: Path, accounts: str, **settings) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        settings_for("canada", **dict({"year": 2024,
                                       "source_currencies": []},
                                      **settings))
        + accounts, encoding="utf-8")
    os.chmod(root, 0o700)
    return root


def _in_kind_project(root: Path) -> Path:
    """margin buys 100 ZZB and moves 50 to the RRSP on 2024-05-01; the
    rows state no value and ticker.map's QUOTE ratio makes the close no
    value either: the contribution is NOT booked."""
    _project(root, '[accounts.margin]\ntype = "taxable"\n'
                   '[accounts.rrsp]\ntype = "sheltered"\n')
    (root / "ticker.map").write_text("QUOTE ZZB.TO ZZBX.TO 2\n")
    for a in ("margin", "rrsp"):
        (root / "inputs" / a).mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2024.csv").write_text(
        _QH + "2024-01-10,2024-01-11,Buy,ZZB,ZZB CORP,100,10.00,-1000.00,"
        "-4.95,-1004.95,CAD,Trades,99900001,Margin\n"
        "2024-05-01,2024-05-01,TF6,ZZB,ZZB CORP TFER TO 99900002,-50,0.00,"
        "0.00,0.00,0.00,CAD,Transfers,99900001,Margin\n")
    (root / "inputs" / "rrsp" / "questrade_2024.csv").write_text(
        _QH + "2024-05-01,2024-05-01,TF6,ZZB,ZZB CORP TRANSFER BOOK VALUE "
        "502.48,50,0.00,0.00,0.00,0.00,CAD,Transfers,99900002,RRSP\n")
    return root


class TestInKindNotBooked(unittest.TestCase):
    """TJ-0095.

    Formerly: test_fix_backlog_runcon.TestInKindNotBooked"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _in_kind_project(Path(cls._tmp.name) / "p")
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "NOT booked" in r.stdout + r.stderr, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_sum_has_an_act_line(self):
        r = _cli(self.root, "sum", width="120")
        self.assertEqual(r.returncode, 0, r.stderr)
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertTrue(any("in-kind move" in a and "NOT booked" in a
                            and "ZZB.TO" in a for a in acts), r.stdout)
        self.assertTrue(all(len(a) <= 100 for a in acts), acts)

    def test_sum_details_names_the_inkind_line(self):
        r = _cli(self.root, "sum", "--details", width="120")
        text = " ".join((r.stdout + r.stderr).split())
        self.assertIn("INKIND 2024-05-01 ZZB.TO -50 CAD <price per share>",
                      text)

    def test_sum_json_readiness(self):
        r = _cli(self.root, "sum", "--json")
        doc = json.loads(r.stdout)
        kinds = [i["kind"] for i in doc["readiness"]["issues"]]
        self.assertIn("in-kind-not-booked", kinds)
        self.assertFalse(doc["readiness"]["complete"])

    def test_checklist_transfers_needs_attention(self):
        r = _cli(self.root, "checklist", "--run", "--only", "transfers",
                 "--json")
        doc = json.loads(r.stdout)
        step = next(s for s in doc["steps"] if s["id"] == "transfers")
        self.assertEqual(step["effective"], "attention", step)
        self.assertIn("in-kind", step["detail"])

    def test_form_export_json_readiness(self):
        """TJ-0251 (TJ-0375): form-export --json carries the same
        readiness record as sum --json."""
        r = _cli(self.root, "form-export", "--json")
        doc = json.loads(r.stdout)
        kinds = [i["kind"] for i in doc["readiness"]["issues"]]
        self.assertIn("in-kind-not-booked", kinds)
        self.assertFalse(doc["readiness"]["complete"])
        s = json.loads(_cli(self.root, "sum", "--json").stdout)
        self.assertEqual(sorted(kinds), sorted(
            i["kind"] for i in s["readiness"]["issues"]))

    def test_form_export_text_has_an_act_line(self):
        """TJ-0251: and a `! ` line in its text view."""
        r = _cli(self.root, "form-export", width="120")
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertTrue(any("in-kind move" in a and "NOT booked" in a
                            and "tjs transfers" in a for a in acts),
                        r.stdout)
        r = _cli(self.root, "form-export", "--details", width="120")
        self.assertIn("NOT in these rows", r.stdout)

    def test_readiness_build(self):
        from taxjson.lib import readiness as RD
        self.assertTrue(RD.build()["complete"])
        rec = RD.build(in_kind_unbooked=2)
        self.assertFalse(rec["complete"])
        self.assertEqual(rec["issues"][0]["count"], 2)


if __name__ == "__main__":
    unittest.main()
