"""Backlog fix round 7 (wave D) of 2026-10-29, group `projects`.

- TJ-1081 a holdings_dir reached through a link inside the year that
  leads out of it is a shared folder, not the year's own: new-year and
  align keep it (the TJ-0798 rewrite stays for a folder really inside);
- TJ-1082 migrate --to-years re-points a link that stays (the shared
  inputs/, the top) and names something in a folder that moves;
- TJ-1083 / TJ-1114 new-year makes and names the folder holdings_dir
  gives the new year, and claims no holdings/ it did not make;
- TJ-1115 the checklist's form-export step reads readiness
  `conservation` issues;
- TJ-1116 the parser docstrings point at the settlement rule's T+5 era;
- TJ-1127 / TJ-1128 scripts/land.sh waits for .yaml workflows and names
  the checks still pending at its timeout.

land.sh runs in throwaway folders with a stub `gh`: nothing touches the
network. Synthetic data only (made-up tickers and accounts, amounts under
a thousand).
"""
import json
import os
import re
import stat
import unittest
from pathlib import Path

import test_fix_release_hardening as _hardening
from _qa_project import project as qa_project, tj
from taxjson.lib import project_layout as PL
from test_fix_1029_r7_migrate import CFG, SNAP, TT, _Tmp, project, tjs
from test_fix_1029_r7b_tooling import _fixture

REPO = Path(__file__).resolve().parent.parent
TRADE = "BUYSELL {y}-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
HOLDING = ('[meta]\naccount = "margin"\nas_of = "{y}-12-31"\n[[holding]]\n'
           'symbol = "SYN.TO"\nquantity = 10\ncurrency = "CAD"\n')


def settings(year_dir):
    return PL.parse_toml((Path(year_dir) / "taxjson.toml")
                         .read_text())["settings"]


class _Years(_Tmp):
    """A 2025 year folder beside the shared inputs/."""

    def year(self, holdings_dir=None, y=2025):
        d = self.top / str(y)
        d.mkdir()
        extra = 'inputs_dir = "../inputs"\n'
        if holdings_dir:
            extra += f'holdings_dir = "{holdings_dir}"\n'
        (d / "taxjson.toml").write_text(
            CFG.format(year=y) + extra
            + '[accounts.margin]\ntype = "taxable"\n')
        (d / "ticker.map").write_text("")
        ins = self.top / "inputs" / "margin"
        if not ins.is_dir():
            ins.mkdir(parents=True)
            (ins / "trades.tt").write_text(TRADE.format(y=y))
        return d


class HoldingsThroughALink(_Years):
    """TJ-1081: top/snapshots_all is shared; 2025/snapshots is a link to
    it. As written the setting is inside 2025; it resolves outside."""

    def _linked(self):
        d = self.year("../2025/snapshots")
        (self.top / "snapshots_all").mkdir()
        (self.top / "snapshots_all" / "margin_holdings.toml").write_text(
            HOLDING.format(y=2025))
        (d / "snapshots").symlink_to("../snapshots_all")
        return d

    def test_own_folder_value(self):
        d = self._linked()
        self.assertEqual(PL.own_folder_value(d, "../2025/snapshots"),
                         "../2025/snapshots")
        self.assertEqual(PL.own_folder_value(d, "snapshots"), "snapshots")

    def test_really_inside_is_still_the_years_own(self):
        # TJ-0798's case, with and without the folder on disk, and a
        # full path through a link to the project.
        d = self.year()
        self.assertEqual(PL.own_folder_value(d, "../2025/snapshots"),
                         "snapshots")
        (d / "snapshots").mkdir()
        self.assertEqual(PL.own_folder_value(d, "../2025/snapshots"),
                         "snapshots")
        self.assertEqual(PL.own_folder_value(d, str(d / "snapshots")),
                         "snapshots")
        (self.top / "alias").symlink_to("2025")
        self.assertEqual(
            PL.own_folder_value(d, str(self.top / "alias" / "snapshots")),
            "snapshots")
        # A link to another folder inside the year: the name as written.
        (d / "real").mkdir()
        (d / "named").symlink_to("real")
        self.assertEqual(PL.own_folder_value(d, "../2025/named"), "named")
        # Beside the years: as it is.
        self.assertEqual(PL.own_folder_value(d, "../shared"), "../shared")

    def test_new_year_keeps_reading_the_shared_folder(self):
        self._linked()
        self.ok(tjs(self.top, "new-year", "2026"))
        new = self.top / "2026"
        st = settings(new)
        self.assertEqual(st.get("holdings_dir"), "../2025/snapshots")
        self.assertEqual(PL.setting_problems(new, st), [])
        self.assertEqual(PL.holdings_folder(new),
                         self.top / "snapshots_all")

    def test_align_does_not_rewrite_it(self):
        self._linked()
        new = self.year("../snapshots_all", y=2026)
        self.ok(tjs(new, "align", "--from", "2025", "--write", "--all",
                    "--json"))
        self.assertEqual(PL.holdings_folder(new), self.top / "snapshots_all")


class NewYearNamesTheFolder(_Years):
    """TJ-1083 / TJ-1114: what new-year says and makes follows the
    holdings_dir the new year reads."""

    def _new(self, *args):
        r = self.ok(tjs(self.top, "new-year", "2026", *args))
        return r.stdout + r.stderr, self.top / "2026"

    def test_own_folder_is_made_and_named(self):
        for value in ("snapshots", "../2025/snapshots"):
            with self.subTest(value=value):
                d = self.year(value)
                (d / "snapshots").mkdir()
                out, new = self._new()
                self.assertEqual(settings(new)["holdings_dir"], "snapshots")
                self.assertTrue((new / "snapshots").is_dir())
                self.assertFalse((new / "holdings").exists())
                self.assertIn("ticker.map, snapshots/", out)
                self.assertNotIn("holdings/", out)
                self.assertIn("2026/snapshots/", out)
                self.assertEqual(stat.S_IMODE(
                    (new / "snapshots").stat().st_mode), 0o700)
                self.ok(tjs(new, "run", "--no-input"))
                # A snapshot saved where it says is the one read.
                (new / "snapshots" / "margin_holdings.toml").write_text(
                    HOLDING.format(y=2026))
                r = tjs(new, "sanity")
                self.assertNotIn("UNCHECKED", r.stdout + r.stderr)
                import shutil
                shutil.rmtree(new)
                shutil.rmtree(d)

    def test_details_names_it_too(self):
        d = self.year("snapshots")
        (d / "snapshots").mkdir()
        out, _new = self._new("--details")
        self.assertNotIn("2026/holdings/", out)
        self.assertNotIn("2025/holdings/", out)
        self.assertIn("2026/snapshots/", out)
        self.assertIn("2025/snapshots/", out)

    def test_nested_own_folder(self):
        d = self.year("data/snaps")
        (d / "data" / "snaps").mkdir(parents=True)
        out, new = self._new()
        self.assertTrue((new / "data" / "snaps").is_dir())
        self.assertEqual(stat.S_IMODE((new / "data").stat().st_mode), 0o700)
        self.assertIn("2026/data/snaps/", out)

    def test_year_folder_already_there(self):
        # The file-by-file path: a nested folder whose parent exists, and
        # a folder there already (not claimed as made).
        self.year("data/snaps")
        new = self.top / "2026"
        (new / "data").mkdir(parents=True)
        (new / "data" / "mine.txt").write_text("kept\n")
        out, _new = self._new()
        self.assertTrue((new / "data" / "snaps").is_dir())
        self.assertEqual((new / "data" / "mine.txt").read_text(), "kept\n")
        self.assertIn("data/snaps/", out)

    def test_folder_there_is_not_claimed(self):
        self.year("snapshots")
        new = self.top / "2026"
        (new / "snapshots").mkdir(parents=True)
        out, _new = self._new()
        self.assertIn("taxjson.toml, ticker.map", out)
        self.assertNotIn("ticker.map, snapshots/", out)
        self.assertIn("2026/snapshots/", out)

    def test_a_file_in_the_way_is_refused(self):
        self.year("data/snaps")
        new = self.top / "2026"
        new.mkdir()
        (new / "data").write_text("a file\n")
        r = tjs(self.top, "new-year", "2026")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Nothing was created", r.stderr)
        self.assertFalse((new / "taxjson.toml").exists())

    def test_shared_folder_is_named_and_not_claimed(self):
        self.year("../snapshots")
        out, new = self._new()
        self.assertEqual(sorted(p.name for p in new.iterdir()),
                         ["taxjson.toml", "ticker.map"])
        self.assertNotIn("holdings/", out)
        self.assertIn("snapshots/ (shared)", out)
        self.assertNotIn("2026/snapshots", out)

    def test_default_is_unchanged(self):
        self.year()
        out, new = self._new()
        self.assertTrue((new / "holdings").is_dir())
        self.assertIn("ticker.map, holdings/", out)
        self.assertIn("2026/holdings/", out)


class MigrateLinksThatStay(_Tmp):
    """TJ-1082: a link in a folder that stays, naming something that
    moves, names it at its new place. What TJ-0699 fixed (links that move
    themselves) is MigrateRelocation in test_fix_1029_r7_migrate."""

    def _proj(self):
        p = self.top / "proj"
        project(p)
        (p / "inputs" / "margin" / "trades.tt").write_text(
            TRADE.format(y=2025))
        (p / "holdings").mkdir()
        (p / "holdings" / "snap.toml").write_text(SNAP)
        return p

    def test_relative_link_in_the_shared_inputs(self):
        p = self._proj()
        link = p / "inputs" / "margin" / "pos_link.toml"
        link.symlink_to("../../holdings/snap.toml")
        self.ok(tjs(p, "run", "--no-input"))
        r = self.ok(tjs(p, "migrate", "--to-years"))
        self.assertIn("pos_link.toml", r.stdout)
        self.assertEqual(os.readlink(link), "../../2025/holdings/snap.toml")
        self.assertEqual(link.resolve(),
                         p / "2025" / "holdings" / "snap.toml")
        self.ok(tjs(p / "2025", "run", "--no-input"))

    def test_absolute_link_and_one_at_the_top(self):
        p = self._proj()
        a = p / "inputs" / "margin" / "abs_link.toml"
        a.symlink_to(p / "holdings" / "snap.toml")
        top = p / "latest"
        top.symlink_to("holdings")
        self.ok(tjs(p, "migrate", "--to-years"))
        self.assertEqual(os.readlink(a),
                         str(p / "2025" / "holdings" / "snap.toml"))
        self.assertEqual(os.readlink(top), "2025/holdings")
        self.ok(tjs(p / "2025", "run", "--no-input"))

    def test_other_links_are_left_alone(self):
        p = self._proj()
        (p / "inputs" / "margin" / "pos.toml").write_text(SNAP)
        same = p / "inputs" / "margin" / "same.toml"
        same.symlink_to("pos.toml")
        stray = p / "notes"
        stray.mkdir()
        gone = stray / "gone"
        gone.symlink_to("../nowhere")
        self.ok(tjs(p, "migrate", "--to-years"))
        self.assertEqual(os.readlink(same), "pos.toml")
        self.assertEqual(os.readlink(gone), "../nowhere")

    def test_dry_run_says_it_and_changes_nothing(self):
        p = self._proj()
        link = p / "inputs" / "margin" / "pos_link.toml"
        link.symlink_to("../../holdings/snap.toml")
        r = self.ok(tjs(p, "migrate", "--to-years", "--dry-run"))
        self.assertIn("pos_link.toml", r.stdout)
        self.assertEqual(os.readlink(link), "../../holdings/snap.toml")
        self.assertFalse((p / "2025").exists())

    def test_read_only_folder_refused_before_anything_moves(self):
        p = self._proj()
        d = p / "inputs" / "margin"
        (d / "pos_link.toml").symlink_to("../../holdings/snap.toml")
        d.chmod(0o500)
        self.addCleanup(d.chmod, 0o700)
        if os.access(d, os.W_OK):
            self.skipTest("this user writes through a read-only mode")
        r = tjs(p, "migrate", "--to-years")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("pos_link.toml", r.stderr)
        self.assertIn("Nothing was moved", r.stderr)
        self.assertFalse((p / "2025").exists())
        self.assertTrue((p / "holdings" / "snap.toml").is_file())

    def test_frozen_copy_of_the_link_names_the_moved_file(self):
        # The filed year's own copy of the account folder keeps a link
        # that works from where the copy is, and so does the shared one.
        p = self._proj()
        link = p / "inputs" / "margin" / "pos_link.toml"
        link.symlink_to("../../holdings/snap.toml")
        abs_link = p / "inputs" / "margin" / "abs_link.toml"
        abs_link.symlink_to(p / "holdings" / "snap.toml")
        (p / "inputs" / "margin" / "trades.tt").write_text(TT)
        self.ok(tjs(p, "run", "--no-input"))
        self.ok(tjs(p, "close-year", "--yes"))
        r = self.ok(tjs(p, "migrate", "--to-years"))
        self.assertIn("filed with its inputs frozen", r.stdout)
        y = p / "2025"
        snap = y / "holdings" / "snap.toml"
        copy = y / "inputs" / "margin"
        for f in (link, abs_link, copy / "pos_link.toml",
                  copy / "abs_link.toml"):
            self.assertTrue(f.is_symlink(), f)
            self.assertEqual(f.resolve(), snap, f)


class FormExportConservation(unittest.TestCase):
    """TJ-1115: a cost or quantity the books lost track of leaves the
    step's totals tying yet short: readiness incomplete, step attention.
    What TJ-0824 fixed: TestReadinessUnconsumedPremium."""

    def _step(self, root):
        doc = json.loads(tj(root, "checklist", "--run", "--only",
                            "form-export", "--json", check=False).stdout)
        return doc["steps"][0]

    def test_conservation_issue_makes_it_attention(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = qa_project(td, "p", {
                "margin/trades.tt":
                    "BUYSELL 2024-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2024-02-02 10:00:00 SYN.TO -10 CAD 15 150 0\n"})
            tj(root, "run", "--no-input")
            self.assertEqual(self._step(root)["status"], "done")
            diag = root / "work" / "margin_gains.json.diag"
            with diag.open("a") as f:
                f.write("warning: conservation: SYN.TO pool is EMPTY but "
                        "carries 40.00 of stranded basis\n")
            rd = json.loads(tj(root, "form-export", "--json").stdout)[
                "readiness"]
            self.assertIn("conservation", [i["kind"] for i in rd["issues"]])
            step = self._step(root)
            self.assertEqual(step["status"], "attention", step)
            self.assertIn("SYN.TO pool is EMPTY", step["detail"])
            self.assertIn("tjs audit", step["detail"])


class SettlementDocstrings(unittest.TestCase):
    """TJ-1116: docstrings describing the fallback name its T+5 era."""

    def test_no_docstring_stops_at_t3(self):
        for f in ("base.py", "generic.py", "ib_extractor.py"):
            text = (REPO / "src" / "taxjson" / "lib" / "brokerages"
                    / f).read_text()
            with self.subTest(f=f):
                flat = " ".join(text.split())
                self.assertIsNone(
                    re.search(r"T\+3 before 2017-09-05(?!,? T\+5)", flat))
                self.assertIn("T+5 before 1995-06-07", flat)


class LandWorkflows(unittest.TestCase):
    """TJ-1127 / TJ-1128: scripts/land.sh with a stub gh."""

    def fixture(self):
        return _fixture(self, _hardening.TestLand,
                        "test_timeout_leaves_it_open")

    def _workflow(self, t, filename, name):
        wf = t.dev / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / filename).write_text(
            f"name: {name}\non:\n  pull_request:\n"
            "permissions:\n  contents: read\n")

    def test_a_yaml_workflow_is_waited_for(self):
        t = self.fixture()
        t.workflow("CI")
        self._workflow(t, "second.yaml", "Second")
        t.git(t.dev, "add", "-A")
        t.set_state()
        t.commit("with workflows")
        start = t.origin_main()
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(t.origin_main(), start, "nothing merged")
        self.assertIn("Second", r.stderr)

    def test_a_yaml_workflow_with_a_paths_filter_is_not(self):
        t = self.fixture()
        t.workflow("CI")
        wf = t.dev / ".github" / "workflows" / "few.yaml"
        wf.write_text("name: Few\non:\n  pull_request:\n    paths:\n"
                      "      - scripts/x.sh\n")
        t.git(t.dev, "add", "-A")
        t.set_state()
        t.commit("with workflows")
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_timeout_names_a_status_request_that_failed(self):
        t = self.fixture()
        t._stub("gh", _hardening._LAND_GH.replace(
            'if path.endswith("/status"):\n        sys.exit(0)',
            'if path.endswith("/status"):\n'
            '        sys.stderr.write("HTTP 403: synthetic\\n"); sys.exit(1)'))
        t.set_state()
        t.commit()
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("still pending: commit statuses (could not be read)",
                      r.stderr)

    def test_timeout_names_slow_checks(self):
        t = self.fixture()
        t.set_state(checks="pending")
        t.commit()
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("still pending: CI", r.stderr)

    def test_timeout_with_no_check_yet_says_so(self):
        t = self.fixture()
        t.set_state(checks="none")
        t.commit()
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("no check has appeared yet", r.stderr)


if __name__ == "__main__":
    unittest.main()
