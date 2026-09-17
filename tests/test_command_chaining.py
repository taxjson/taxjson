"""Chained subcommands: `taxjson run sum` executes both, in order."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


def _project(tmp):
    root = Path(tmp)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT_HEADER +
        "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,D,"
        "100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n"
        "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,D,"
        "-100,15.00,1500.00,0.00,1500.00,CAD,1,Trades,Individual\n")
    return root


class TestSegmentSplitting(unittest.TestCase):
    def _split(self, argv):
        import argparse
        from taxjson.bin.taxjson_run import _split_command_segments
        # A miniature parser mirroring the real shapes.
        p = argparse.ArgumentParser()
        p.add_argument("-C", "--dir", default=".")
        sub = p.add_subparsers(dest="cmd", required=True)
        pr = sub.add_parser("run")
        pr.add_argument("--fast", action="store_true")
        pr.add_argument("--no-input", action="store_true")
        pr.add_argument("--account")
        ps = sub.add_parser("sum")
        ps.add_argument("--json", action="store_true")
        psh = sub.add_parser("show")
        psh.add_argument("account")
        return _split_command_segments(p, argv, set(sub.choices))

    def test_two_plain_commands(self):
        self.assertEqual(self._split(["run", "sum"]),
                         [["run"], ["sum"]])

    def test_flags_stay_with_their_command(self):
        self.assertEqual(self._split(["run", "--fast", "sum", "--json"]),
                         [["run", "--fast"], ["sum", "--json"]])

    def test_option_value_collision_not_split(self):
        # `sum` is --account's VALUE, not a new command.
        self.assertEqual(self._split(["run", "--account", "sum"]),
                         [["run", "--account", "sum"]])

    def test_positional_collision_not_split(self):
        # An account literally named `sum`: `show` alone is incomplete,
        # so `sum` is consumed as the positional.
        self.assertEqual(self._split(["show", "sum"]), [["show", "sum"]])

    def test_global_prefix_applied_to_every_segment(self):
        self.assertEqual(self._split(["-C", "/proj", "run", "sum"]),
                         [["-C", "/proj", "run"],
                          ["-C", "/proj", "sum"]])

    def test_explicit_separator(self):
        self.assertEqual(self._split(["show", "sum", "--", "sum"]),
                         [["show", "sum"], ["sum"]])


class TestChainedExecution(unittest.TestCase):
    def test_run_then_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _cli(root, "run", "--no-input", "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        # The tail of stdout is sum's (pretty-printed) JSON document —
        # it starts at the last line-leading brace.
        doc = json.loads(r.stdout[r.stdout.rindex("\n{") + 1:])
        self.assertAlmostEqual(doc["totals"]["realized"], 500.0,
                               places=2)

    def test_failure_stops_the_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            # `show nope` fails (no reports yet); `run` must NOT execute.
            r = _cli(root, "show", "nope", "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((Path(tmp) / "work").exists(),
                         "the failed first command must stop the chain")




class TestReleaseAuditRegressions(unittest.TestCase):
    """Fixes from the pre-release adversarial pass."""

    def test_end_of_options_idiom_preserved(self):
        # `show -- margin` (argparse's own `--` use) must NOT split —
        # the chain boundary only fires when the segment is complete.
        from test_command_chaining import TestSegmentSplitting as T
        helper = T("test_two_plain_commands")
        self.assertEqual(helper._split(["show", "--", "margin"]),
                         [["show", "--", "margin"]])

    def test_chained_help_prints_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _cli(root, "run", "-h", "sum")
        # -h is terminal for the chain, but the help block must appear
        # exactly once (the trial parse used to leak a second copy).
        self.assertEqual(r.stdout.count("usage: taxjson run"), 1,
                         r.stdout)




class TestChainPreValidation(unittest.TestCase):
    """2026-08-21 audit: `taxjson events -- 30d` HALF-RAN — events
    executed, then '30d' died rc 2. The whole chain is validated
    before anything executes."""

    def test_bad_tail_segment_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _cli(root, "events", "--", "30d")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing was executed", r.stderr)
        self.assertFalse((Path(tmp) / "work").exists(),
                         "the first command must not have run")

    def test_ambiguous_boundary_gets_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _cli(root, "run", "--no-input", "sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        # `sum` could have been run's account value? No — run takes no
        # positional, so no note there. Use `events trades`: trades is
        # both a command and a plausible account arg.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _cli(root, "run", "--no-input")
            r = _cli(root, "events", "trades")
        self.assertIn("note:", r.stderr)
        self.assertIn("'trades'", r.stderr)


if __name__ == "__main__":
    unittest.main()
