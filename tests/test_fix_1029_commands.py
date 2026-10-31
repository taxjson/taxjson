"""Backlog fix round 2026-10-29, group `commands`: the command findings
of the 2026-10-28 worktree audit and older command items.

TJ-0349  align --write --all: an account only one year has is listed,
         never its keys brought over (its required `type` was commented
         out); the configuration as written must pass the config check.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account names (margin, rrsp), amounts under
1,000.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _snapshot(root):
    """{relative path: sha256} of every file under `root`."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes())
            .hexdigest() for p in Path(root).rglob("*") if p.is_file()}


class TestAlignLeavesALoneAccountAlone(unittest.TestCase):
    """TJ-0349 (TJ-0393): 2024 has [accounts.margin] and
    [accounts.rrsp], 2025 only margin. `align --from 2025 --write --all`
    in 2024 set accounts.rrsp.type to 2025's "(not set)": it commented
    the `type` out, and the next run stopped ("[accounts.rrsp] has no
    `type`"). An account only one year has is now listed, never its
    keys brought over; and a taxjson.toml the config check would refuse
    is never written."""

    def _years(self, d, acc24=("margin", "rrsp"), acc25=("margin",),
               extra25=""):
        top = Path(d)
        for year, names in ((2024, acc24), (2025, acc25)):
            y = top / str(year)
            y.mkdir()
            accts = "".join(
                f'[accounts.{n}]\ntype = '
                f'"{"sheltered" if n == "rrsp" else "taxable"}"\n'
                + (extra25 if year == 2025 and n == "margin" else "")
                for n in names)
            (y / "taxjson.toml").write_text(
                f'[settings]\nyear = {year}\ncountry = "canada"\n'
                f'base_currency = "CAD"\nsource_currencies = []\n'
                f'inputs_dir = "../inputs"\n{accts}')
            (y / "ticker.map").write_text("")
        for n in ("margin", "rrsp"):
            (top / "inputs" / n).mkdir(parents=True)
            (top / "inputs" / n / "m.tt").write_text(
                "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 "
                "100.00 0\n")
        return top

    def test_an_account_only_here_keeps_its_type(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d)
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--all", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["accounts_only_here"], ["rrsp"])
            self.assertEqual(doc["accounts_only_there"], [])
            self.assertFalse([k for k in doc["keys"]
                              if k["key"].startswith("accounts.rrsp")])
            self.assertEqual(doc["keys_set"], [])
            self.assertFalse(doc["written"])
            text = (top / "2024" / "taxjson.toml").read_text()
            self.assertIn('[accounts.rrsp]\ntype = "sheltered"', text)
            run = _tjs(top / "2024", "run", "--no-input")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_an_account_only_there_is_listed_not_added(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d, acc24=("margin",),
                              acc25=("margin", "rrsp"))
            before = _snapshot(top)
            r = _tjs(top / "2024", "align", "--from", "2025")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[accounts] only in 2025: rrsp", r.stdout)
            self.assertIn("align never adds or removes an account",
                          r.stdout)
            self.assertNotIn("accounts.rrsp.type", r.stdout)
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--all")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Nothing brought over.", r.stdout)
            self.assertIn("[accounts] only in 2025: rrsp", r.stdout)
            self.assertEqual(_snapshot(top), before)

    def test_years_counts_an_account_only_one_year_has(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d)
            r = _tjs(top, "years", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            y24 = next(y for y in doc["years"] if y["year"] == 2024)
            self.assertEqual(y24["differs_from_newest"]["accounts"], 1)
            self.assertEqual(y24["differs_from_newest"]["keys"], 0)

    def test_a_config_the_check_refuses_is_never_written(self):
        with tempfile.TemporaryDirectory() as d:
            # 2025's margin says crypto = "yes" (quoted: the check
            # refuses it); bringing it over would break 2024 too.
            top = self._years(d, acc24=("margin",), acc25=("margin",),
                              extra25='crypto = "yes"\n')
            before = _snapshot(top)
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--all")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("would not pass the config check", r.stderr)
            self.assertIn("crypto must be true or false", r.stderr)
            self.assertIn("Nothing was written", r.stderr)
            self.assertEqual(_snapshot(top), before)


def _elect_project(d, sheltered=False):
    """A Canadian project: margin (taxable), and rrsp (sheltered)."""
    root = Path(d) / "p"
    accts = '[accounts.margin]\ntype = "taxable"\n' + (
        '[accounts.rrsp]\ntype = "sheltered"\n' if sheltered else "")
    (root / "inputs" / "margin").mkdir(parents=True)
    if sheltered:
        (root / "inputs" / "rrsp").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2024\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n' + accts)
    (root / "ticker.map").write_text("")
    return root


class TestElectJsonViewsAgree(unittest.TestCase):
    """TJ-0400: `elect rrsp --json` listed only the saved elections:
    an event the run booked by the sheltered default (shown by `elect
    --json` and `elect rrsp`) was missing, `{"accounts": {}}`.
    TJ-0406: a manifest that does not read was an `error` object with
    exit 0; the text listing stopped. Every view now exits 2 (an input
    that cannot be read, docs/commands.md)."""

    def _defaulted(self, root):
        from taxjson.lib.corp_actions import SHELTERED_DEFAULT
        (root / "work").mkdir()
        (root / "work" / "rrsp_ib_corp.json").write_text(json.dumps(
            {"transactions": [{
                "action": "BUYSELL", "date": "2024-06-03",
                "symbol": "ZZSP.TO", "quantity": 5,
                "corp_event_id": "zz-spinoff-1",
                "corp_election": SHELTERED_DEFAULT}]}))

    def test_one_account_lists_the_defaulted_event(self):
        with tempfile.TemporaryDirectory() as d:
            root = _elect_project(d, sheltered=True)
            self._defaulted(root)
            every = _tjs(root, "elect", "--json")
            one = _tjs(root, "elect", "rrsp", "--json")
            text = _tjs(root, "elect", "rrsp")
            for r in (every, one, text):
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("zz-spinoff-1", text.stdout)
            got = json.loads(one.stdout)
            self.assertEqual(got["accounts"]["rrsp"],
                             json.loads(every.stdout)["accounts"]["rrsp"])
            ev = got["accounts"]["rrsp"]["zz-spinoff-1"]
            self.assertFalse(ev["saved"])
            self.assertEqual(ev["summary"], "spinoff ZZSP.TO on 2024-06-03")

    def test_an_unreadable_manifest_exits_2_in_every_view(self):
        with tempfile.TemporaryDirectory() as d:
            root = _elect_project(d)
            (root / "inputs" / "margin" / "manifest.json").write_text("{")
            for args in (("elect", "margin", "--json"), ("elect", "--json")):
                r = _tjs(root, *args)
                self.assertEqual(r.returncode, 2, args)
                doc = json.loads(r.stdout)
                self.assertIn("not valid JSON",
                              doc["accounts"]["margin"]["error"])
            r = _tjs(root, "elect", "margin")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("not valid JSON", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_no_manifest_is_still_an_empty_listing(self):
        with tempfile.TemporaryDirectory() as d:
            root = _elect_project(d)
            r = _tjs(root, "elect", "margin", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout), {"accounts": {}})


class TestAnEmptyGainsObjectIsDamaged(unittest.TestCase):
    """TJ-0412: a gains file replaced by `{}` read as an empty book:
    shares and list showed no positions, wash-sales no denials, and
    sanity compared clean against an empty holdings list, all at exit
    0. A gains file with none of its parts is now refused (an empty
    list stays a valid empty book)."""

    def test_reader_refuses_only_a_gains_file_without_its_parts(self):
        from taxjson.lib.json_input import InputFileError, read_work_doc
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            for name in ("margin_gains.json", "margin_gains_wash.json",
                         "margin_raw_gains.json"):
                (w / name).write_text("{}")
                with self.assertRaisesRegex(InputFileError,
                                            "a gains file with none of"):
                    read_work_doc(w / name)
            ok = {"margin_gains.json": '{"transactions": []}',
                  "margin_gains_wash.json": '{"inventory": []}',
                  "margin_raw_gains.json": "[]",
                  "margin_base.json": "{}"}
            for name, text in ok.items():
                (w / name).write_text(text)
                self.assertIsInstance(read_work_doc(w / name), dict)

    def test_the_views_refuse_it(self):
        with tempfile.TemporaryDirectory() as d:
            root = _elect_project(d)
            (root / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 "
                "100.00 0\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name in ("margin_gains.json", "margin_gains_wash.json"):
                if (root / "work" / name).is_file():
                    (root / "work" / name).write_text("{}")
            (root / "holdings").mkdir()
            (root / "holdings" / "empty.toml").write_text("holding = []\n")
            for args in (("shares", "--json"), ("list", "--json"),
                         ("wash-sales", "--json"),
                         ("sanity", "margin=holdings/empty.toml",
                          "--json")):
                r = _tjs(root, *args)
                self.assertNotEqual(r.returncode, 0, args)
                self.assertIn("a gains file with none of", r.stderr, args)
                self.assertNotIn("Traceback", r.stderr, args)


class TestRemoveNamesTheLinesFile(unittest.TestCase):
    """TJ-0165 (TJ-0401): an OPENING cost=unknown line in a hand-written
    inputs/margin/hand.tt for an option the broker never coded CLOSING
    (a written option) — find-missing-history said "REMOVE from
    missing_history.tt"; only --details named hand.tt:1. Each row now
    names its line, the heading its file, and the checklist says it."""

    def test_the_hand_written_file_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            root = _elect_project(d)
            (root / "inputs" / "margin" / "hand.tt").write_text(
                'OPENING 2023-12-29 ZZL250117C00050000.TO 2 cost=unknown '
                'reason="bought before the data"\n'
                "BUYSELL 2024-03-01 10:00:00 ZZL250117C00050000.TO -2 CAD "
                "3.00 598.70 1.30\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((r.stdout + r.stderr).count("never coded"), 1)
            f = _tjs(root, "find-missing-history")
            self.assertEqual(f.returncode, 0, f.stdout + f.stderr)
            self.assertIn("REMOVE from inputs/margin/hand.tt - ", f.stdout)
            self.assertIn("ZZL250117C00050000.TO margin  "
                          "inputs/margin/hand.tt:1", f.stdout)
            self.assertNotIn("missing_history.tt", f.stdout)
            c = _tjs(root, "checklist", "--run", "--only",
                     "missing-history")
            self.assertIn("remove: ZZL250117C00050000.TO (margin, "
                          "inputs/margin/hand.tt:1)", c.stdout)

    def test_entries_in_missing_history_tt_keep_its_name(self):
        from taxjson.bin.taxjson_missing_history import (_entries_file,
                                                         _entry_row)
        from taxjson.lib.missing_history import StaleMissingHistoryEntry as E
        mh = "missing_history.tt"
        a = E("ZZA.TO", "margin", "derivative",
              file_name="inputs/margin/missing_history.tt:3")
        b = E("ZZB.TO", "tfsa", "derivative",
              file_name="inputs/tfsa/missing_history.tt:1")
        c = E("ZZC.TO", "margin", "derivative",
              file_name="inputs/margin/hand.tt:2")
        bare = E("ZZD.TO", "margin", "derivative")
        self.assertEqual(_entries_file([a, b], mh), mh)
        self.assertEqual(_entries_file([c], mh), "inputs/margin/hand.tt")
        self.assertEqual(_entries_file([a, c], mh),
                         "the .tt file each line names")
        self.assertEqual(_entries_file([bare], mh), mh)
        self.assertEqual(_entry_row(bare, mh), "ZZD.TO margin")
        self.assertEqual(_entry_row(a, mh), "ZZA.TO margin  "
                         "inputs/margin/missing_history.tt:3")


class TestRangeCheckedFlagsAreUsageErrors(unittest.TestCase):
    """TJ-0241: `winners --top 0` exited 1, the code of a finding; a
    usage error is 2 (docs/commands.md, TJ-0157), said before any file
    is read. The same for `edge-cases --margin -1` and a `crypto-sends
    --set` without `=`."""

    def test_top_and_margin(self):
        with tempfile.TemporaryDirectory() as d:
            root = _elect_project(d)        # no work/: never read
            for args, said in ((("winners", "--top", "0"), "--top must be"),
                               (("losers", "--top", "-3"), "--top must be"),
                               (("edge-cases", "--margin", "-1"),
                                "--margin must be")):
                r = _tjs(root, *args)
                self.assertEqual(r.returncode, 2, (args, r.stderr))
                self.assertIn(said, r.stderr)
                self.assertNotIn("run `tjs run` first", r.stderr)


class TestRankedViewNamesTheAccount(unittest.TestCase):
    """TJ-0260: `tjs losers margin` in a project of two taxable
    accounts said "LOSERS — tax year 2024 (CAD, worst 10)" and "LOSERS
    TOTAL: … (1 shown)": only the TOTAL REALIZED GAIN line named the
    account. The title and the side's total now name it."""

    def _project(self, d):
        root = Path(d) / "p"
        (root / "work").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.cash]\ntype = "taxable"\n')

        def d_(sym, gain):
            return {"symbol": sym, "date": "2024-03-01", "qty": 10,
                    "proceeds": 500.0 + gain, "cost": 500.0, "gain": gain,
                    "currency": "CAD", "days_held": 30}
        for acct, rows in (("margin", [d_("ZZL.TO", -300.0),
                                       d_("ZZW.TO", 200.0)]),
                           ("cash", [d_("ZZC.TO", -100.0)])):
            (root / "work" / f"{acct}_gains.json").write_text(json.dumps(
                {"summary": {"year": "2024"}, "transactions": rows,
                 "inventory": []}))
        return root

    def test_title_and_side_total_name_the_account(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._project(d)
            r = _tjs(root, "losers", "margin")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith(
                "LOSERS — tax year 2024 (CAD, account margin, worst 10)"),
                r.stdout)
            self.assertRegex(r.stdout,
                             r"LOSERS TOTAL:\s+-300\.00 CAD \(1 shown, "
                             r"margin\)")
            self.assertNotIn("ZZC.TO", r.stdout)
            r = _tjs(root, "losers")
            self.assertTrue(r.stdout.startswith(
                "LOSERS — tax year 2024 (CAD, worst 10)"), r.stdout)
            self.assertRegex(r.stdout, r"LOSERS TOTAL:\s+-400\.00 CAD "
                                       r"\(all 2 shown\)")
            r = _tjs(root, "winners", "cash")
            self.assertIn("No realized winners in tax year 2024 (account "
                          "cash, basis:", r.stdout)


class TestInProcessCommandNamesItself(unittest.TestCase):
    """TJ-0354: `_die` read the `_CURRENT_CMD` global, which only main's
    dispatch loop sets: a `cmd_*` function called in-process after
    another command (tests, a GUI) named the earlier one ("tjs
    reconcile-slips: error" for a t1135 refusal)."""

    def _err_of(self, fn):
        import argparse
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin import taxjson_run as R
        from taxjson.lib import out
        err = io.StringIO()
        with mock.patch.object(R, "_CURRENT_CMD", "reconcile-slips"), \
                out.unwrapped(), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit):
            fn(R, argparse.Namespace)
        return R, err.getvalue()

    def test_a_direct_call_names_its_own_command(self):
        R, err = self._err_of(lambda R, NS: R.cmd_winners(NS(
            dir=".", period=None, account=None, top=0, json=False)))
        self.assertIn(f"{R._PROG} winners: error: --top must be >= 1",
                      err)
        self.assertNotIn("reconcile-slips", err)

    def test_a_helper_outside_any_command_keeps_the_set_name(self):
        R, err = self._err_of(lambda R, NS: R._die_input("no such file"))
        self.assertIn(f"{R._PROG} reconcile-slips: error: no such file",
                      err)


class TestSharedTobaseHeaderWraps(unittest.TestCase):
    """TJ-0233: `migrate`'s "tobase.map: one file every year reads, in
    <top> ..." line was a bare print: with a long folder path it ran
    past the width; the plan's other lines wrap."""

    def test_every_line_fits_the_width(self):
        with tempfile.TemporaryDirectory() as d:
            top = Path(d) / "a_long_folder_name_for_the_tax_years" / "taxes"
            for year in (2024, 2025):
                y = top / str(year)
                y.mkdir(parents=True)
                (y / "taxjson.toml").write_text(
                    f'[settings]\nyear = {year}\ncountry = "canada"\n'
                    f'base_currency = "CAD"\nsource_currencies = []\n'
                    f'inputs_dir = "../inputs"\n'
                    f'[accounts.margin]\ntype = "taxable"\n')
                (y / "ticker.map").write_text("")
                (y / "tobase.map").write_text("TOBASE ZZQ.US ZZQ.TO\n")
            (top / "inputs" / "margin").mkdir(parents=True)
            r = _tjs(top, "migrate", "--dry-run",
                     env_extra={"TAXJSON_WIDTH": "100"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("tobase.map: one file every year reads, in",
                          r.stdout)
            self.assertGreater(len(f"tobase.map: one file every year "
                                   f"reads, in {top} ([settings] "
                                   f'tobase_map = "../tobase.map")'), 100)
            for ln in r.stdout.splitlines():
                # A path on a line of its own cannot wrap: the temp
                # folder's own length (the gate's parallel runner nests
                # it) is not the message's.
                if " " not in ln.strip():
                    continue
                self.assertLessEqual(len(ln), 100, ln)


class TestShortCoverActLineFits(unittest.TestCase):
    """TJ-0229: `tjs sum`'s `! ` line for sales booked as shorts a
    later purchase closed went past 100 columns with an option symbol:
    the head text was long and out.act_list kept at least one name. The
    head is shorter, and act_list names none when not one fits."""

    HEAD = "1 sale with no purchase booked as shorts covered later"
    CMD = "tjs find-missing-history"

    def test_act_list_drops_a_name_that_does_not_fit(self):
        from taxjson.lib import out
        line = out.act_list(self.HEAD, ["ZZC241220C00040000.US"], self.CMD)
        self.assertLessEqual(len(line), 100, line)
        self.assertEqual(line, f"! {self.HEAD} — {self.CMD}")
        line = out.act_list(self.HEAD, ["ZZN.TO"], self.CMD)
        self.assertEqual(line, f"! {self.HEAD} (ZZN.TO) — {self.CMD}")
        line = out.act_list(self.HEAD, ["ZZN.TO", "ZZC241220C00040000.US",
                                        "ZZM.TO"], self.CMD)
        self.assertEqual(line, f"! {self.HEAD} (ZZN.TO +2 more) — "
                               f"{self.CMD}")
        # A head that does not fit even alone keeps its first name.
        long_head = "x" * 100
        self.assertIn("(ZZN.TO)", out.act_list(long_head, ["ZZN.TO"]))

    def test_sum_says_it_within_100_columns(self):
        with tempfile.TemporaryDirectory() as d:
            root = _elect_project(d)
            (root / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2024-02-01 10:00:00 ZZN.TO -10 CAD 50 500\n"
                "BUYSELL 2024-04-01 10:00:00 ZZN.TO 10 CAD 40 -400\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            s = _tjs(root, "sum", env_extra={"TAXJSON_WIDTH": "120"})
            acts = [ln for ln in s.stdout.splitlines()
                    if ln.startswith("! ")]
            self.assertIn(f"! {self.HEAD} (ZZN.TO) — {self.CMD}", acts)
            for ln in acts:
                self.assertLessEqual(len(ln), 100, ln)


if __name__ == "__main__":
    unittest.main()
