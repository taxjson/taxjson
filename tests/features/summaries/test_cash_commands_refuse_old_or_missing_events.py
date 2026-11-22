"""PLAN step 16 commit 6: the commands that read the cash events (lib/cash_events
`collect`) read the last run's parse (taxjson-brokerage --cash-out:
work/<account>_<broker>.cash), never the exports, and so never data older than
the inputs. That covers `tjs cash`, `tjs fx-cash --ledger v2`, `tjs t1135`,
`tjs sum` (its ledger v2 line) and the checklist's fx-cash step; close-year
refuses the books. The owner's rule (2026-11-17): "tjs run should always be
full - i don't want to take any chance of stale data". No flag skips the check.
`tjs t1135` and `tjs sum` refuse as a whole, as the filing commands do (owner
decision 2026-11-18, TJ-4347).

Each command is pinned twice: with the sidecar gone (the books as new as the
inputs), it refuses and names `tjs run`; with an input changed after the run,
it refuses as the books are refused (cli/common `_books_older_than_inputs`),
until `tjs run`. The review of commit 6 (TJ-4356): a run scoped by `--account`,
or a full run that failed after its parse, rewrote the sidecar and left the
last full run's record, so with the inputs put back as the record names them
the commands read that run's events. Every `tjs run` sets the record aside
before it writes into work/, and only a full run that finishes writes one.

Synthetic data only.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

import _paths
from _support.fx_ledger import _TOML, _rates
from _support import parser_cash

REPO_ROOT = _paths.REPO

# An RBC export of a USD account held in the United States (its cash counts for
# T1135): a deposit, a purchase and its sale.
_RBC = ("Date,Activity,Symbol,Symbol Description,Quantity,Price,Settlement Date,Account,"
        "Value,Currency,Description\n"
        "2025-01-06,Deposits & Contributions,,,,,2025-01-06,99900099,1000,USD,CONTRIBUTION\n"
        "2025-02-03,Buy,ZZQ,ZZQ CORP,10,50,2025-02-04,99900099,-500,USD,ZZQ CORP\n"
        "2025-11-03,Sell,ZZQ,ZZQ CORP,-10,55,2025-11-04,99900099,550,USD,ZZQ CORP\n")
_LATER = "2025-06-02,Deposits & Contributions,,,,,2025-06-02,99900099,200,USD,CONTRIBUTION\n"
# A second export whose purchase has no exchange rate (offline, a currency
# the rates file lacks): the full run stops after the parse wrote its events.
_NO_RATE = (_RBC.splitlines(True)[0]
            + "2025-02-03,Buy,ZZR,ZZR CORP,5,10,2025-02-04,99900099,-50,CHF,ZZR CORP\n"
            + _LATER)
# A sheltered account's export: its parse writes no cash events.
_RBC_TFSA = _RBC.replace("99900099", "88800088")
_NO_RECORD = ("no full `tjs run` recorded what the books in work/ were built from "
              "(work/.inputs_fingerprint.json)")


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run",
                                             ["-C", str(root), *args]),
                          cwd=REPO_ROOT, capture_output=True, text=True, env=e,
                          stdin=subprocess.DEVNULL, timeout=600)


class TestCashCommandsReadTheRunsEvents(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "proj"
        (cls.root / "inputs" / "margin").mkdir(parents=True)
        (cls.root / "work").mkdir()
        (cls.root / "inputs" / "tfsa").mkdir(parents=True)
        (cls.root / "taxjson.toml").write_text(
            _TOML.format(extra='fx_cash_ledger = "v2"') + 'custodian_country = "USA"\n'
            '\n[accounts.tfsa]\ntype = "sheltered"\n')
        (cls.root / "inputs" / "margin" / "rbc.csv").write_text(_RBC)
        (cls.root / "inputs" / "tfsa" / "rbc.csv").write_text(_RBC_TFSA)
        _rates(cls.root / "work" / "to_base.csv", cls.addClassCleanup)
        cls._run()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _run(cls):
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr + r.stdout

    def _fx_step(self, sid="fx-cash"):
        doc = json.loads(_cli(self.root, "checklist", "--run", "--json").stdout)
        return [s for s in doc["steps"] if s["id"] == sid][0]

    def _refused(self, said):
        """Each command that reads the cash events refuses, saying `said`
        and naming `tjs run`."""
        r = _cli(self.root, "cash", "all")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(said, r.stderr)
        self.assertIn("run `tjs run` first", r.stderr.lower())
        r = _cli(self.root, "fx-cash", "--ledger", "v2")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(said, r.stderr)
        self.assertIn("run `tjs run` first", r.stderr.lower())
        # t1135 and sum refuse as a whole, before any figure (owner decision
        # 2026-11-18, TJ-4347: before, t1135 tested the securities with its
        # cash rows refused, and sum printed its rows, line 15300 omitted).
        for argv in (("t1135", "--json"), ("t1135",), ("sum", "--details"), ("sum",)):
            r = _cli(self.root, *argv)
            self.assertEqual(r.returncode, 1, (argv, r.stdout))
            self.assertEqual(r.stdout, "", argv)
            self.assertIn(said, r.stderr, argv)
            self.assertIn("`tjs run`", r.stderr, argv)
            self.assertNotIn("The figures below", r.stderr, argv)
        # The refusal comes before the books' banner ("The figures below
        # may..."), which would head no figures (review of commit 6).
        for argv in (("cash", "all"), ("fx-cash", "--ledger", "v2")):
            self.assertNotIn("The figures below", _cli(self.root, *argv).stderr, argv)
        # The checklist's step says the refusal's reason, not its advice.
        step = self._fx_step()
        self.assertEqual(step["status"], "blocked", step)
        self.assertIn(said, step["detail"])

    def _read(self):
        """Each command reads the run's cash events."""
        r = _cli(self.root, "cash", "all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CASHMOVE 2025-01-06 USD +1,000.00", r.stdout)
        r = _cli(self.root, "fx-cash", "--ledger", "v2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["ledger"], "v2")
        doc = json.loads(_cli(self.root, "t1135", "--json").stdout)
        self.assertNotEqual(doc["cash_ledger_status"], "refused", doc["cash_not_counted"])
        r = _cli(self.root, "sum", "--details")
        self.assertNotIn("estimate omitted", r.stderr)
        self.assertIn("ledger v2", " ".join(r.stdout.split()))
        self.assertNotEqual(self._fx_step()["status"], "blocked")
        return r

    def test_after_a_run_each_command_reads_its_events(self):
        self._read()
        self.assertTrue((self.root / "work" / "margin_rbc_direct.cash").is_file())
        # A sheltered account's parse writes none: the ledger reads the
        # taxable accounts alone (review of commit 6).
        self.assertTrue((self.root / "work" / "tfsa_rbc_direct.json").is_file())
        self.assertFalse((self.root / "work" / "tfsa_rbc_direct.cash").exists())

    def test_with_no_sidecar_each_command_refuses_naming_tjs_run(self):
        side = self.root / "work" / "margin_rbc_direct.cash"
        side.unlink()
        self.addCleanup(self._run)
        self._refused("no cash events recorded for the rbc_direct exports of taxable "
                      "account 'margin' (work/margin_rbc_direct.cash)")
        self._run()
        self._read()

    def test_a_stale_sidecar_with_fresh_books_refuses_t1135_and_sum(self):
        # The books as new as the inputs, the cash events not the run's (a
        # sidecar naming another export): t1135 and sum refuse before any
        # figure, as `tjs cash` does (owner decision 2026-11-18, TJ-4347:
        # the whole-command refusal keeps the sidecar checks).
        side = self.root / "work" / "margin_rbc_direct.cash"
        text = side.read_text()
        self.addCleanup(side.write_text, text)
        doc = json.loads(text)
        doc["files"][0]["name"] = "other.csv"
        side.write_text(json.dumps(doc))
        for argv in (("t1135", "--json"), ("sum",), ("cash", "all")):
            r = _cli(self.root, *argv)
            self.assertEqual(r.returncode, 1, (argv, r.stdout))
            self.assertEqual(r.stdout, "", argv)
            self.assertIn("work/margin_rbc_direct.cash names other exports than the last "
                          "run's", " ".join(r.stderr.split()), argv)
            self.assertIn("`tjs run`", r.stderr, argv)
        side.write_text(text)
        self._read()

    def test_an_input_changed_after_the_run_is_refused_until_tjs_run(self):
        csv = self.root / "inputs" / "margin" / "rbc.csv"
        csv.write_text(_RBC + _LATER)
        self.addCleanup(self._run)
        self.addCleanup(csv.write_text, _RBC)
        self._refused("the books in work/ are older than the inputs (changed: "
                      "inputs/margin/rbc.csv)")
        r = _cli(self.root, "close-year")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("inputs changed since the last full run (changed: "
                      "inputs/margin/rbc.csv)", r.stderr)
        self._run()
        self._read()
        # The events are the inputs' as they are now.
        self.assertIn("CASHMOVE 2025-06-02 USD   +200.00",
                      _cli(self.root, "cash", "all").stdout)

    def _no_record(self):
        """No record of the last full run; the books and the cash events are
        refused by every command that reads them, close-year too; the
        checklist's run-clean step says the last run did not finish one."""
        self.assertFalse((self.root / "work" / ".inputs_fingerprint.json").exists())
        step = self._fx_step("run-clean")
        self.assertEqual(step["status"], "attention", step)
        self.assertIn("the last `tjs run` did not finish a full run", step["detail"])
        self._refused(_NO_RECORD)
        r = _cli(self.root, "close-year")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("the last `tjs run` did not finish a full run", r.stderr)

    def test_a_scoped_run_leaves_the_project_refused_until_a_full_run(self):
        # Review of commit 6, P1 (repro A): a deposit added, `tjs run --account
        # margin`, the export put back: `tjs cash` listed the scoped run's
        # deposit at exit 0, as the record named the export as it is.
        csv = self.root / "inputs" / "margin" / "rbc.csv"
        self.addCleanup(self._run)
        self.addCleanup(csv.write_text, _RBC)
        csv.write_text(_RBC + _LATER)
        r = _cli(self.root, "run", "--account", "margin", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        csv.write_text(_RBC)
        self.assertIn("2025-06-02", (self.root / "work" / "margin_rbc_direct.cash").read_text())
        self._no_record()
        self._run()
        self._read()
        self.assertNotIn("2025-06-02", _cli(self.root, "cash", "all").stdout)

    def test_a_failed_full_run_leaves_the_project_refused_until_a_full_run(self):
        # Review of commit 6, P1 (repro B): a full run that stops after its
        # parse (no exchange rate) rewrote the books and the cash events and
        # left the record; the export taken out again, the commands read
        # them. The books' own refusal (form-export, check-filed: cli/common
        # `_refuse_books_older_than_inputs`) had the same hole.
        extra = self.root / "inputs" / "margin" / "extra.csv"
        self.addCleanup(self._run)
        self.addCleanup(extra.unlink, missing_ok=True)
        extra.write_text(_NO_RATE)
        r = _cli(self.root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("no exchange rate", r.stderr)
        extra.unlink()
        side = json.loads((self.root / "work" / "margin_rbc_direct.cash").read_text())
        self.assertIn("extra.csv", [f["name"] for f in side["files"]])   # the failed run's
        self._no_record()
        r = _cli(self.root, "form-export")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(_NO_RECORD, " ".join(r.stderr.split()))
        self._run()
        self._read()


class TestAUsLockClosedFromTheseBooksIsNoChange(unittest.TestCase):
    """Review of commit 6: in a US project the filed year's lock is an input,
    so after close-year `tjs cash` and `fx-cash --ledger v2` refused ("added:
    filed/ (locked years)") until another run. They read the lock as
    close-year does: one of the project's year closed from these books is no
    change (cli/common `_refuse_cash_older_than_inputs`, `read_upto`)."""

    def test_cash_reads_after_close_year(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "usa"\nyear = 2024\nbase_currency = "USD"\n'
                'source_currencies = []\n\n[accounts.margin]\ntype = "taxable"\n')
            (root / "ticker.map").write_text("")
            (root / "inputs" / "margin" / "t.tt").write_text(
                "BUYSELL 2024-11-01 09:30:00 WSHQ.US 100 USD 50.00 5000.00 0\n"
                "BUYSELL 2024-12-20 09:30:00 WSHQ.US -100 USD 51.00 5100.00 0\n")
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "close-year", "--yes")
            self.assertEqual(r.returncode, 0, r.stderr[-1000:])
            self.assertTrue((root / "filed" / "2024.json").is_file())
            # (The books' banner still names the lock: the run-clean step
            # reads it with no read_upto.)
            for argv in (("cash", "all"), ("fx-cash", "--ledger", "v2")):
                r = _cli(root, *argv)
                self.assertEqual(r.returncode, 0, (argv, r.stderr))
                self.assertNotIn("older than the inputs", r.stderr, argv)


class TestTheSidecarsAreCheckedAgainstTheRun(unittest.TestCase):
    """lib/cash_events `_sidecar_files` and `collect` read the parse's cash
    events only as the last run left them: its sources list, each broker
    group's sidecar of the kind the parse writes, naming the files the list
    names, read at the project's stablecoin basis; in the account folder's
    order across broker groups. Each guard is pinned (review of commit 6:
    none was)."""

    def setUp(self):
        from taxjson.lib import cash_events as CE
        from taxjson.lib.country import stablecoin_model
        self.CE = CE
        self.stable = stablecoin_model("canada") == "cash"
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name) / "proj"
        self.work = self.root / "work"
        folder = self.root / "inputs" / "m"
        folder.mkdir(parents=True)
        self.work.mkdir()
        (self.root / "taxjson.toml").write_text(
            '[settings]\ncountry = "canada"\nyear = 2025\n\n[accounts.m]\ntype = "taxable"\n')
        # The folder's order (a.csv, b.csv) is not the broker groups' (ib's
        # b.csv first in the list).
        for broker, name in (("ib", "b.csv"), ("rbc_direct", "a.csv")):
            (folder / name).write_bytes((_paths.FIXTURES / broker / "sample.csv").read_bytes())
            doc = parser_cash.cash_sidecar(broker, "m", [folder / name], "canada")
            CE.sidecar_path(self.work / f"m_{broker}.json").write_text(json.dumps(doc))
        self.lst = self.work / "m_sources.list"
        self.lst.write_text("ib/b.csv\nrbc_direct/a.csv\n")

    def _refused(self, said, stable=None):
        with self.assertRaises(ValueError) as cm:
            self.CE._sidecar_files(self.work, "m", self.stable if stable is None else stable)
        self.assertIn(said, str(cm.exception))
        self.assertIn("tjs run", str(cm.exception))

    def test_the_run_as_it_left_them_is_read(self):
        got = self.CE._sidecar_files(self.work, "m", self.stable)
        self.assertEqual([(b, n) for b, n, _e in got], [("ib", "b.csv"), ("rbc_direct", "a.csv")])

    def test_no_sources_list(self):
        self.lst.unlink()
        self._refused("no cash events recorded for the broker exports of taxable account "
                      "'m' (work/m_sources.list)")

    def test_a_sidecar_of_another_kind(self):
        side = self.work / "m_ib.cash"
        doc = json.loads(side.read_text())
        doc["metadata"]["kind"] = "transfers"
        side.write_text(json.dumps(doc))
        self._refused("work/m_ib.cash is no cash-events file")

    def test_a_sidecar_naming_other_exports_than_the_list(self):
        self.lst.write_text("ib/c.csv\nrbc_direct/a.csv\n")
        self._refused("work/m_ib.cash names other exports than the last run's "
                      "work/m_sources.list")

    def test_a_sidecar_of_the_other_stablecoin_basis(self):
        self._refused("was read with stablecoins as", stable=not self.stable)

    def test_the_events_follow_the_folders_order_across_broker_groups(self):
        from taxjson.pipeline.config import load_config
        got = self.CE.collect(self.root, load_config(self.root), "canada", {}, self.work)
        files = [e["file"] for e in got["events"]]
        self.assertTrue(files, got)
        self.assertEqual(sorted(set(files)), ["a.csv", "b.csv"])
        self.assertEqual(files, sorted(files))      # a.csv's (RBC) before b.csv's (IB)

    def test_a_parse_row_takes_its_cash_events_with_it(self):
        # pipeline/stage_table `companions`: what goes with a parse's book
        # when its instance is gone (the sweep) includes its sidecar.
        from taxjson.pipeline.stage_table import companions
        out = self.work / "m_ib.json"
        self.assertIn(self.work / "m_ib.cash", companions("parse", out))
        self.assertNotIn(self.work / "m_ib.cash", companions("base", out))


class TestTheParseRecordsTheFixturesEvents(unittest.TestCase):
    """`taxjson-brokerage --cash-out` writes, for each export it parses, the
    events the conformance kit holds the fixture's reader to (commit 5's
    expected_cash.json); without --country (the stablecoin basis) it is
    refused before the parse."""

    def _parse(self, broker, *extra, folder=None):
        sample = _paths.FIXTURES / (folder or broker) / "sample.csv"
        return subprocess.run(python_module_argv(
            "taxjson.bin.taxjson_brokerage", ["--brokerage", broker, str(sample), *extra]),
            cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                     TAXJSON_LOCAL_TZ="America/Toronto"), timeout=600)

    def test_each_files_events_are_its_golden(self):
        for broker in ("ib", "rbc_direct", "questrade"):
            with self.subTest(broker=broker), tempfile.TemporaryDirectory() as td:
                side = Path(td) / f"a_{broker}.cash"
                r = self._parse(broker, "--country", "canada", "--cash-out", str(side))
                self.assertEqual(r.returncode, 0, r.stderr)
                doc = json.loads(side.read_text(encoding="utf-8"))
                self.assertEqual(doc["metadata"]["kind"], "cash_events")
                self.assertEqual(doc["metadata"]["input_files"], ["sample.csv"])
                (entry,) = doc["files"]
                self.assertEqual((entry["name"], entry["problem"]), ("sample.csv", ""))
                golden = _paths.FIXTURES / broker / "expected_cash.json"
                self.assertEqual(entry["events"],
                                 json.loads(golden.read_text(encoding="utf-8")))

    def test_a_readers_note_is_said_once_and_its_own_line_reaches_the_diag(self):
        # TJ-4348: the readers' stderr was swallowed. Kraken's reader repeated
        # the parse's stablecoin note (said once); since step 16 commit 11 no
        # reader is left, and the parse that builds the events says it once.
        # A line the sidecar's builder says reaches the parse's stderr, which
        # the run keeps in the .json.diag.
        with tempfile.TemporaryDirectory() as td:
            r = self._parse("kraken", "--country", "canada", "--cash-out",
                            str(Path(td) / "a.cash"), folder="kraken_ledgers")
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
            self.assertEqual(sum("USDC is treated as a US-dollar stablecoin" in ln
                                 for ln in lines), 1, r.stderr)
            self.assertEqual(len(lines), len(set(lines)), r.stderr)
        import argparse
        import contextlib
        import io
        import sys
        from unittest import mock
        from taxjson.bin import taxjson_brokerage as TB
        from taxjson.lib import cash_events as CE

        def reader(broker, account, paths, country, built=None, problems=None):
            print("note: a line only the reader says", file=sys.stderr)
            return {"files": [], "metadata": {"kind": CE.SIDECAR_KIND}}
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(CE, "cash_sidecar", reader), contextlib.redirect_stderr(err):
            TB._write_cash_out(argparse.Namespace(cash_out=str(Path(td) / "a.cash"),
                                                  account_name="a", country="canada"),
                               "kraken", [])
        self.assertIn("a line only the reader says", err.getvalue())

    def test_needs_the_country(self):
        with tempfile.TemporaryDirectory() as td:
            side = Path(td) / "a_ib.cash"
            r = self._parse("ib", "--cash-out", str(side))
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("--cash-out needs --country", r.stderr)
            self.assertFalse(side.exists())


if __name__ == "__main__":
    unittest.main()
