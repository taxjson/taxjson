"""The backlog fix round of 2026-10-29, group checklist: `tjs checklist`
is the user's starting point (AGENTS.md), so its Next line must name
what fixes the finding it just reported, and the checks it leans on
must not read missing evidence as clean.

TJ-0356  (the audit's TJ-0402) the Next line of an attention step
         named the item's first command over the result's own: a
         tobase.map line the run refuses said "Next: `tjs format`",
         which lays out taxjson.toml and fixes nothing. Now Next names
         the result's own action or command (fix the line, then `tjs
         run`), as its `! ` line does.
TJ-0032  (the audit's TJ-0403) after a run refused on a .tt line, the
         run-clean step said only "inputs changed since the last full
         run" and sent the user back to `tjs run`, which refused again.
         Now it names the line the run refuses and points to check-tt.
TJ-0395  `tjs check-dates` with an account's work/<account>_sources.list
         gone checked one account fewer and said every date was fine
         (exit 0). Now the account is an ERROR (`missing`, exit 1).

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth

from taxjson.lib import checklist as CL
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str, width: str = "0"
         ) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH=width)
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _config(root: Path, accounts: str = '[accounts.margin]\n'
            'type = "taxable"\n') -> None:
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'local_timezone = "America/Toronto"\n' + accounts,
        encoding="utf-8")


class _RunOnce(unittest.TestCase):
    """One synthetic Canadian project with two taxable accounts (a
    purchase each, in a .tt file), run once; each test changes a copy
    of it."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.base = Path(cls._td.name) / "base"
        for acct, day in (("margin", "06"), ("margin2", "07")):
            # (one purchase each, on two days: the same file in both
            # accounts is a file booked twice, backlog TJ-0478)
            (cls.base / "inputs" / acct).mkdir(parents=True)
            (cls.base / "inputs" / acct / "trades.tt").write_text(
                f"BUYSELL 2025-01-{day} 10:00:00 ZZQ.TO 1 CAD 100 100 0\n",
                encoding="utf-8")
        _config(cls.base, '[accounts.margin]\ntype = "taxable"\n'
                          '[accounts.margin2]\ntype = "taxable"\n')
        (cls.base / "ticker.map").write_text("", encoding="utf-8")
        r = _cli(cls.base, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy(self, name: str) -> Path:
        dst = Path(self._td.name) / name
        shutil.copytree(self.base, dst)
        return dst

    def step(self, root: Path, sid: str, run: bool = True) -> tuple:
        """(the step's JSON, the document's `next`) of `checklist --only
        SID` (`run`: checked now, else the stored verdict)."""
        r = _cli(root, "checklist", *(["--run"] if run else []),
                 "--only", sid, "--json")
        doc = json.loads(r.stdout)
        return [s for s in doc["steps"] if s["id"] == sid][0], doc["next"]


class TestNextNamesTheFindingsFix(_RunOnce):
    """TJ-0356 (TJ-0402): the Next line names what fixes the finding
    shown, not the item's first command."""

    def test_tj0356_a_tobase_map_line_is_fixed_then_rebuilt(self):
        root = self.copy("tobase")
        (root / "tobase.map").write_text("GLOBAL ZZQ.US ZZQ.TO\n",
                                         encoding="utf-8")
        step, nxt = self.step(root, "format")
        self.assertEqual(step["effective"], "attention", step)
        self.assertEqual(nxt["id"], "format")
        self.assertEqual(nxt["do"], "fix or delete the line tobase.map:1, "
                                    "then `tjs run`")
        self.assertNotIn("tjs format`", nxt["do"])
        # The stored verdict (no --run) says the same.
        _step, again = self.step(root, "format", run=False)
        self.assertEqual(again["do"], nxt["do"])
        # And the text view's last line.
        r = _cli(root, "checklist", "--only", "format", width="120")
        self.assertIn("Next (step 19, format): fix or delete the line "
                      "tobase.map:1, then `tjs run`", r.stdout)

    def test_tj0356_the_results_own_command_wins_over_the_items(self):
        its = {it.id: it for it in CL.items(2025, "canada")}
        # An attention with a command of its own and no action.
        r = CL.Result("transfers", "attention", "2 transfer-in(s)",
                      cmd="tjs transfers")
        self.assertEqual(CL._do(its["transfers"], r), "`tjs transfers`")
        r = CL.Result("format", "attention", "x", cmd="tjs format-map")
        self.assertEqual(CL._do(its["format"], r), "`tjs format-map`")
        # An instruction is said as written.
        r = CL.Result("configure", "attention", "no [accounts.NAME]",
                      cmd="edit taxjson.toml")
        self.assertEqual(CL._do(its["configure"], r), "edit taxjson.toml")
        # An item with no standing advice: the command its detail names,
        # as the `! ` line names it.
        r = CL.Result("run-clean", "attention",
                      "pending elections (`tjs elect --pending`)")
        self.assertEqual(CL._do(its["run-clean"], r),
                         "`tjs elect --pending`")
        # The item's advice stays for an item that has one...
        r = CL.Result("inputs-frozen", "attention", "latest activity "
                      "2025-11-03 — not through 2026-01-31")
        self.assertIn("download the exports", CL._do(its["inputs-frozen"],
                                                     r))
        # ...and for a step that is merely to do.
        r = CL.Result("run-clean", "todo", "x `tjs elect --pending`")
        self.assertEqual(CL._do(its["run-clean"], r), "`tjs run`")

    def test_tj0356_outside_a_repository_the_lock_step_says_git_init(self):
        # (the item's advice, "commit filed/<year>.json", cannot be
        # followed outside a repository)
        from unittest import mock
        root = self.copy("nogit")
        ctx = CL.Ctx(root=root, cfg={"settings": {"year": 2025}},
                     year=2025, today=CL.date(2026, 5, 1),
                     run_sub=lambda *a, **k: (0, "", ""))
        with mock.patch.object(CL, "_lock_state", lambda *_a: None), \
                mock.patch.object(CL, "_is_git_repo", lambda _r: False):
            r = CL.d_lock_committed(ctx)
        self.assertEqual(r.status, "attention", r.detail)
        its = {it.id: it for it in CL.items(2025, "canada")}
        self.assertEqual(CL._do(its["lock-committed"], r),
                         "`git init`, then commit filed/2025.json")


class TestRunCleanNamesTheTtRefusal(_RunOnce):
    """TJ-0032 (TJ-0403): after a run refused on a .tt line, run-clean
    names that line and points to check-tt, not back at `tjs run`."""

    def test_tj0032_run_clean_names_the_line_the_run_refuses(self):
        root = self.copy("badtt")
        (root / "inputs" / "margin" / "bad.tt").write_text(
            "BUYSELL 2025-13-05 10:00:00 ZZB.TO 10 CAD 1 10 0\n",
            encoding="utf-8")
        run = _cli(root, "run", "--no-input")
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        step, nxt = self.step(root, "run-clean")
        self.assertEqual(step["effective"], "attention", step)
        self.assertTrue(step["detail"].startswith(
            "1 .tt problem(s) `tjs run` refuses — "
            "inputs/margin/bad.tt:1: "), step["detail"])
        self.assertIn("inputs changed since the last full run (added: "
                      "inputs/margin/bad.tt)", step["detail"])
        self.assertEqual(nxt["id"], "run-clean")
        self.assertEqual(nxt["do"], "fix each line `tjs check-tt` names, "
                                    "then `tjs run`")
        r = _cli(root, "checklist", "--only", "run-clean", width="120")
        self.assertIn("! 8 run-clean: 1 .tt problem(s) `tjs run` refuses "
                      "— inputs/margin/bad.tt:1: ", r.stdout)
        self.assertIn("— tjs check-tt", r.stdout)

    def test_tj0032_a_clean_tt_change_still_says_run(self):
        root = self.copy("goodtt")
        (root / "inputs" / "margin" / "more.tt").write_text(
            "BUYSELL 2025-02-03 10:00:00 ZZB.TO 10 CAD 1 10 0\n",
            encoding="utf-8")
        step, nxt = self.step(root, "run-clean")
        self.assertEqual(step["effective"], "attention", step)
        self.assertTrue(step["detail"].startswith(
            "inputs changed since the last full run"), step["detail"])
        self.assertEqual(nxt["do"], "`tjs run`")

    def test_tj0032_the_tt_lines_step_says_fix_then_run(self):
        root = self.copy("ttlines")
        (root / "inputs" / "margin" / "bad.tt").write_text(
            "BUYSELL 2025-13-05 10:00:00 ZZB.TO 10 CAD 1 10 0\n",
            encoding="utf-8")
        step, _nxt = self.step(root, "tt-lines")
        self.assertEqual(step["effective"], "attention", step)
        its = {it.id: it for it in CL.items(2025, "canada")}
        r = CL.Result("tt-lines", "attention", step["detail"],
                      cmd="tjs check-tt", do=CL.TT_REFUSED_DO)
        self.assertEqual(CL._do(its["tt-lines"], r),
                         "fix each line `tjs check-tt` names, then "
                         "`tjs run`")


class TestItemsAsTheyApplyHere(_RunOnce):
    """TJ-0289: `tjs staking --details` only where a crypto account is
    configured. TJ-0264: a filed year's inputs-frozen step names no
    download and no `tjs fetch`."""

    def _commands(self, root: Path, sid: str) -> tuple:
        """(the text view of --only SID, the step's JSON commands)."""
        text = _cli(root, "checklist", "--only", sid, width="120").stdout
        doc = json.loads(_cli(root, "checklist", "--only", sid,
                              "--json").stdout)
        step = [s for s in doc["steps"] if s["id"] == sid][0]
        return text, [c["command"] for c in step["commands"]], step

    def test_tj0289_no_staking_without_a_crypto_account(self):
        text, cmds, _s = self._commands(self.base, "explore")
        self.assertNotIn("tjs staking", text)
        self.assertFalse([c for c in cmds if c.startswith("tjs staking")])
        self.assertIn("tjs tax-logic", cmds)

    def test_tj0289_staking_with_a_crypto_account(self):
        root = self.copy("crypto")
        with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
            fh.write('[accounts.coins]\ntype = "taxable"\ncrypto = true\n')
        text, cmds, _s = self._commands(root, "explore")
        self.assertIn("tjs staking --details", text)
        self.assertIn("tjs staking --details", cmds)

    def test_tj0289_outside_a_project_the_guide_names_it(self):
        self.assertIn("tjs staking --details", CL.render_guide(2025))

    def test_tj0264_a_filed_year_names_no_download(self):
        text, cmds, step = self._commands(self.base, "inputs-frozen")
        self.assertIn("tjs fetch", cmds)            # an open year
        self.assertIn("Download all the history", step["how"])
        root = self.copy("filed")
        (root / "filed").mkdir()
        (root / "filed" / "2025.json").write_text(json.dumps(
            {"year": 2025, "closed_at": "2026-04-20T10:00:00"}),
            encoding="utf-8")
        text, cmds, step = self._commands(root, "inputs-frozen")
        self.assertEqual(cmds, ["tjs check-filed"])
        self.assertNotIn("tjs fetch", text)
        self.assertNotIn("Download all the history", text)
        self.assertIn("Filed: the books are the exports as they were at "
                      "close-year", text)
        self.assertEqual(step["how"], CL.FROZEN_INPUTS_HOW)


class TestCheckDatesLostSourcesList(_RunOnce):
    """TJ-0395: an account whose books are built but whose sources list
    is gone is evidence not checked, never dropped from the count."""

    def test_tj0395_a_missing_sources_list_is_an_error(self):
        good = _cli(self.base, "check-dates", "--json")
        self.assertEqual(good.returncode, 0, good.stderr[-2000:])
        before = json.loads(good.stdout)
        self.assertEqual(before["errors"], 0)
        root = self.copy("nolist")
        (root / "work" / "margin2_sources.list").unlink()
        r = _cli(root, "check-dates", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["errors"], 1)
        issue = doc["issues"][0]
        self.assertEqual((issue["code"], issue["account"]),
                         ("missing", "margin2"))
        self.assertIn("work/margin2_sources.list is missing",
                      issue["message"])
        self.assertIn("margin2: sources list", doc["sources"])
        text = _cli(root, "check-dates")
        self.assertEqual(text.returncode, 1)
        self.assertNotIn("Every date lands where its market allows",
                         text.stdout)

    def test_tj0395_an_unreadable_sources_list_is_an_error(self):
        root = self.copy("badlist")
        (root / "work" / "margin2_sources.list").write_bytes(b"\xff\xfe\xff")
        r = _cli(root, "check-dates", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertIn("cannot be read", doc["issues"][0]["message"])

    def test_tj0395_an_account_with_no_books_is_not_flagged_here(self):
        from taxjson.lib.check_dates import _lost_sources_list
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(_lost_sources_list(Path(td), "margin"), "")


class _SanityOnce(_RunOnce):
    """margin buys 10 ZZQ.TO in January and 5 more in July (books end
    2025-07-07); margin2 holds 1 ZZR.TO. Run once; each test adds
    holdings files to a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.base = Path(cls._td.name) / "base"
        rows = {"margin": "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 10 CAD 10 "
                          "100 0\nBUYSELL 2025-07-07 10:00:00 ZZQ.TO 5 CAD "
                          "10 50 0\n",
                "margin2": "BUYSELL 2025-01-06 10:00:00 ZZR.TO 1 CAD 10 10 "
                           "0\n"}
        for acct, text in rows.items():
            (cls.base / "inputs" / acct).mkdir(parents=True)
            (cls.base / "inputs" / acct / "trades.tt").write_text(
                text, encoding="utf-8")
        _config(cls.base, '[accounts.margin]\ntype = "taxable"\n'
                          '[accounts.margin2]\ntype = "taxable"\n'
                          'account = "99900003"\n')
        (cls.base / "ticker.map").write_text("", encoding="utf-8")
        r = _cli(cls.base, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]

    @staticmethod
    def snap(path: Path, qty: float, sym: str = "ZZQ.TO", **meta) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        head = ("[meta]\n" + "".join(f'{k} = "{v}"\n'
                                     for k, v in meta.items())
                if meta else "")
        path.write_text(head + f'[[holding]]\nsymbol = "{sym}"\n'
                        f'quantity = {qty}\ncurrency = "CAD"\n',
                        encoding="utf-8")
        return path


class TestSanityDatedBesideUndated(_SanityOnce):
    """TJ-0257 (TJ-0409): a dated snapshot beside an undated one was
    summed and compared with the latest books, certified clean."""

    def test_tj0257_unnamed_files_of_two_days_are_refused(self):
        root = self.copy("mixed")
        self.snap(root / "snaps" / "dated.toml", 10, as_of="2025-06-30")
        self.snap(root / "snaps" / "undated.toml", 5)
        r = _cli(root, "sanity",
                 "margin=snaps/dated.toml+snaps/undated.toml", "--json")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "", "no clean verdict")
        err = " ".join(r.stderr.split())
        self.assertIn("dated.toml (2025-06-30), undated.toml (undated) "
                      "are dated differently and none names its broker "
                      "account", err)
        self.assertIn("An undated file is read as of the books' last day "
                      "(2025-07-07)", err)

    def test_tj0257_a_dated_file_of_the_books_last_day_is_no_mix(self):
        # The books did not move after it: both files are of one day.
        root = self.copy("sameday")
        self.snap(root / "snaps" / "dated.toml", 10, as_of="2025-12-31")
        self.snap(root / "snaps" / "undated.toml", 5)
        r = _cli(root, "sanity",
                 "margin=snaps/dated.toml+snaps/undated.toml", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["clean"])

    def test_tj0257_named_broker_accounts_are_summed_with_a_note(self):
        # Two broker accounts of one taxjson account: summed, as before,
        # and the note says the dated one's positions moved since.
        root = self.copy("named")
        self.snap(root / "snaps" / "a.toml", 10, as_of="2025-06-30",
                  broker_account="99900001")
        self.snap(root / "snaps" / "b.toml", 5, broker_account="99900002")
        r = _cli(root, "sanity", "margin=snaps/a.toml+snaps/b.toml")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("ZZQ.TO changed after 2025-06-30", err)
        self.assertIn("b.toml undated", err)


class TestSanityRefusalAdvice(_SanityOnce):
    """TJ-0256: only unclaimed snapshots is no path to fix. TJ-0255:
    the A13 note's point before its first clause break."""

    def test_tj0256_only_unclaimed_snapshots_says_claim_them(self):
        root = self.copy("unclaimed")
        self.snap(root / "holdings" / "zzz_positions.toml", 15)
        r = _cli(root, "sanity")
        # Exit 2: nothing compared is no difference (TJ-0640).
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("no account claims it", err)
        self.assertIn("none of the snapshots in holdings/ could be compared "
                      "(see the notes above) — claim each for its account",
                      err)
        self.assertNotIn("fix the paths", err)

    def test_tj0256_a_missing_path_still_says_fix_the_paths(self):
        root = self.copy("missingpath")
        with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
            fh.write('holdings = ["nowhere.toml"]\n')     # margin2's
        r = _cli(root, "sanity")
        # Exit 2: a named input missing (TJ-0640).
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("fix the paths.", " ".join(r.stderr.split()))

    def test_tj0255_the_a13_note_says_not_compared_first(self):
        root = self.copy("a13")
        with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
            fh.write('holdings = ["m2.toml"]\n')           # margin2's
        self.snap(root / "m2.toml", 1, sym="ZZR.TO")
        self.snap(root / "holdings" / "margin_holdings.toml", 1,
                  sym="ZZR.TO", broker_account="99900003")
        # The default view's one line, cut to the width: the point stays.
        r = _cli(root, "sanity", width="80")
        self.assertIn("Info: holdings/margin_holdings.toml is not compared",
                      r.stderr)
        r = _cli(root, "sanity", width="120")
        self.assertIn("holdings/margin_holdings.toml is not compared: its "
                      "broker account 99*** is margin2's",
                      " ".join(r.stderr.split()))


if __name__ == "__main__":
    unittest.main()
