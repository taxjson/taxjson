"""The stage table (PLAN step 12; src/taxjson/pipeline/stage_table.py).

The executor runs every row, its steps bound by the module whose code they are
(`execute`, `bound`), and is the package's one `needs_rebuild` ask. Here: the
resolver per kind of reference (a File present or absent, an Out of a row or
a stamp for each instance it reads, a Tree's glob order and its files' keys, a
folder refused), the executor (order, `when`, prepare, the ask, grounds,
every_run, a build vouched for at once, a row run after the one whose finding
its `when` reads, the outputs kept by output, a declared folder one line), the
table's names, and that every ask is the executor's and every row bound once.
That the golden project books reach every row is
tests/test_freshness_edges.py's (TestFreshnessEdges), on its traced runs.
Synthetic files only.
"""
import ast
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _paths
from taxjson.cli import common
from taxjson.pipeline import run, run_state, stages
from taxjson.pipeline import stage_table as ST
from taxjson.pipeline.freshness import (
    FolderInputError, _inputs_now, commit_records, record_path,
)

SRC = _paths.TESTS.parent / "src" / "taxjson"
CANADA = SimpleNamespace(country="canada")


class _Project(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve() / "p"
        self.work = self.root / "work"
        self.work.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel: str, text: str = "x\n") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def keys(self, paths):
        """What a record holds for `paths` ({name from the project: key})."""
        return _inputs_now(self.work / "out.json", tuple(paths))


class TestTheResolver(_Project):

    def test_a_file_is_declared_present_or_absent(self):
        i = ST.Inst("x", ST.StageCtx(work=self.work, account="m"))
        p = ST.resolve((ST.File("{work}/{a}_absent.json"),), i)
        self.assertEqual(p, [self.work / "m_absent.json"])
        self.assertEqual(self.keys(p), {"work/m_absent.json": None})
        # Only a file: nothing until there is one, then its content's key.
        only = (ST.File("{root}/x.txt", keep=ST.FILE),)
        self.assertEqual(ST.resolve(only, i), [])
        self.write("x.txt", "one\n")
        self.assertEqual(self.keys(ST.resolve(only, i)),
                         {"x.txt": hashlib.sha256(b"one\n").hexdigest()})

    def test_a_fact_of_the_run_is_declared_when_there_is_one(self):
        ctx = ST.StageCtx(work=self.work, account="m")
        refs = (ST.File("@ticker_map"),)
        self.assertEqual(ST.resolve(refs, ST.Inst("x", ctx)), [])
        ctx.ticker_map = self.root / "ticker.map"
        self.assertEqual(ST.resolve(refs, ST.Inst("x", ctx)), [self.root / "ticker.map"])

    def test_an_out_is_each_instance_it_reads(self):
        a, b = self.write("inputs/m/a.csv"), self.write("inputs/m/b.csv")
        t1, t2 = self.write("inputs/m/start.tt"), self.write("inputs/m/more.tt")
        ctx = ST.StageCtx(work=self.work, settings=CANADA, account="m",
                     grouped={"questrade": [a], "ib": [b]}, tt_files=[t1, t2])
        acct = ST.Inst("base", ctx)
        # A BROKER row: each group's; a TT row: each .tt file's.
        self.assertEqual(sorted(ST.resolve((ST.Out("parse"),), acct)),
                         [self.work / "m_ib.json", self.work / "m_questrade.json"])
        self.assertEqual(ST.resolve((ST.Out("tt"),), acct),
                         [self.work / "m_tt_start.json", self.work / "m_tt_more.json"])
        # A ref's `when` is asked of each instance it expands to.
        self.assertEqual(ST.resolve((ST.Out("parse", when=lambda j: j.broker == "ib"),), acct),
                         [self.work / "m_ib.json"])
        # A PROJECT row and a stamp.
        self.assertEqual(ST.resolve((ST.Out("rates"), ST.Out("sources")), acct),
                         [self.work / "to_base.csv", self.work / "m_sources.list"])
        # A broker instance reads its own group's stamp.
        self.assertEqual(ST.resolve((ST.Out("suffix"),), ST.Inst("parse", ctx, broker="questrade")),
                         [self.work / "m_questrade_listing_suffix.state"])

    def test_an_out_of_a_blend_is_each_member_s(self):
        ctx = ST.StageCtx(work=self.work, tag="blend", members=["rrsp", "tfsa"],
                     crypto_members=frozenset({"tfsa"}))
        # The equity books' merge for one member, the crypto chain's for the other.
        self.assertEqual(ST.resolve(ST.BOOK, ST.Inst("sheltered", ctx)),
                         [self.work / "rrsp_base.json", self.work / "tfsa_base.json"])
        self.assertEqual(ST.resolve((ST.Out("base", when=ST._equity),), ST.Inst("sheltered", ctx)),
                         [self.work / "rrsp_base.json"])
        # A member's own instance reads its own book, and the blend's output.
        split = ST.Inst("split", ctx, member="rrsp")
        self.assertEqual(ST.resolve(ST.TABLE["split"].inputs, split),
                         [self.work / ".blend_gains_wash.json", self.work / ".blend_base.json",
                          self.work / "rrsp_base.json"])

    def test_a_merge_reads_its_members_books_in_their_order(self):
        # What the merges build from (ST.member_books): the books their
        # rows' BOOK declares, in the members' order. A blend is of one
        # kind: the same list as BOOK resolves (blend_base's inputs); the
        # sheltered merge of an equity and a crypto account keeps the run's
        # order, where `resolve` lists the equity book first.
        for row in ("sheltered", "blend_base", "gains_all_blend"):
            self.assertEqual(ST.TABLE[row].inputs[:len(ST.BOOK)], ST.BOOK, row)
        self.assertEqual([ST.TABLE[r].inputs for r in ("sheltered", "blend_base")],
                         [ST.BOOK, ST.BOOK])

        def books(*names):
            return [self.work / f"{n}_base.json" for n in names]
        for members, crypto in ((["qt", "margin"], ()), (["crypto2", "crypto"], ("crypto", "crypto2"))):
            ctx = ST.StageCtx(work=self.work, tag="t", members=members,
                              crypto_members=frozenset(crypto))
            for row in ("blend_base", "gains_all_blend"):
                with self.subTest(members=members, row=row):
                    i = ST.Inst(row, ctx)
                    self.assertEqual(ST.member_books(i), books(*members))
                    self.assertEqual(ST.resolve(ST.BOOK, i), books(*members))
        mixed = ST.StageCtx(work=self.work, members=["tfsa", "rrsp"],
                            crypto_members=frozenset({"tfsa"}))
        i = ST.Inst("sheltered", mixed)
        self.assertEqual(ST.member_books(i), books("tfsa", "rrsp"))
        self.assertEqual(ST.resolve(ST.TABLE["sheltered"].inputs, i), books("rrsp", "tfsa"))

    def test_a_tree_glob_is_sorted_and_each_file_keyed(self):
        for name in ("z.tt", "a.tt", "m.tt"):         # made in this order
            self.write(f"inputs/m/{name}", f"{name}\n")
        self.write("inputs/m/a.csv")
        i = ST.Inst("x", ST.StageCtx(work=self.work, account="m"))
        tree = (ST.Tree("glob:{root}/inputs/{a}/*.tt"),)
        got = ST.resolve(tree, i)
        self.assertEqual([p.name for p in got], ["a.tt", "m.tt", "z.tt"])
        before = self.keys(got)
        self.assertEqual(before["inputs/m/m.tt"], hashlib.sha256(b"m.tt\n").hexdigest())
        self.write("inputs/m/m.tt", "edited\n")
        after = self.keys(ST.resolve(tree, i))
        self.assertEqual(sorted(n for n in before if before[n] != after[n]), ["inputs/m/m.tt"])

    def test_a_selection_is_the_run_s(self):
        a = self.write("inputs/m/a.csv")
        t = self.write("inputs/m/s.tt")
        ctx = ST.StageCtx(work=self.work, account="m", grouped={"ib": [a]}, tt_files=[t])
        self.assertEqual(ST.resolve((ST.Tree("csvs"),), ST.Inst("parse", ctx, broker="ib")), [a])
        self.assertEqual(ST.resolve((ST.Tree("tt_files"),), ST.Inst("corp", ctx, broker="ib")), [t])

    def test_a_folder_is_refused(self):
        (self.root / "inputs").mkdir()
        i = ST.Inst("x", ST.StageCtx(work=self.work))
        declared = ST.resolve((ST.File("{root}/inputs"),), i)
        with self.assertRaises(FolderInputError):
            self.keys(declared)
        # A path a stage leaves out when it is a folder (an elections manifest).
        self.assertEqual(ST.resolve((ST.File("{root}/inputs", keep=ST.NOT_DIR),), i), [])


class TestTheExecutor(_Project):
    """A made-up BROKER row run by `execute`: its instances in the row's
    order, `when`, prepare before the ask, the build every time (`tjs run`
    always rebuilds everything, and so do the views) and, when `reuse`d (a
    row of fetched data: the rates; this one is marked so), only when stale
    or on a ground, every_run always, the ask over the row's inputs. A
    derived row asked to reuse is refused."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.grounds = False
        st = ST.Stage("t_row", ST.BROKER, "{work}/{a}_{b}.out",
                      (ST.File("{work}/{b}.in"), ST.Out("sources")),
                      when=lambda i: i.broker != "skip", fetched=True,
                      grounds=lambda i: self.grounds,
                      order=lambda b: b == "q")
        patcher = mock.patch.dict(ST.TABLE, {"t_row": st})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ctx = ST.StageCtx(work=self.work, account="m",
                          grouped={"q": [], "skip": [], "x": []})
        for b in ("q", "x"):
            self.write(f"work/{b}.in", f"{b}\n")

    def bind(self):
        def prepare(i):
            self.calls.append(("prepare", i.broker))
            return f"prep-{i.broker}"

        def build(i, r):
            self.calls.append(("build", i.broker, r.prep))
            ST.output(i).write_text("built\n", encoding="utf-8")

        def every_run(i, r):
            self.calls.append(("every_run", i.broker, sorted(p.name for p in r.inputs)))
        return ST.bound({"t_row": ST.Steps(build, prepare, every_run)})

    def test_an_unbound_row_is_refused(self):
        with self.assertRaises(LookupError):
            ST.execute(ST.TABLE, self.ctx, "t_row")
        with self.assertRaises(KeyError):
            ST.bound({"no_such_row": ST.Steps(lambda i, r: None)})

    def test_a_derived_row_is_never_reused(self):
        # No derived figure comes from a record's word (TJ-4307): only a row
        # of fetched data may skip on its record.
        derived = dataclasses.replace(ST.TABLE["t_row"], fetched=False)
        with mock.patch.dict(ST.TABLE, {"t_row": derived}):
            table = ST.bound({"t_row": ST.Steps(lambda i, r: self.calls.append("build"))})
            with self.assertRaisesRegex(LookupError, "t_row row is derived"):
                ST.execute(table, self.ctx, "t_row", reuse=True)
            self.assertEqual(self.calls, [])
            ST.execute(table, self.ctx, "t_row", at_once=True)
            ST.execute(table, self.ctx, "t_row", at_once=True)
            self.assertEqual(self.calls, ["build"] * 4)    # x and q, twice

    def test_a_build_is_vouched_for_at_once_when_asked(self):
        # A view command completes no run: its builds are recorded as each
        # ends (`record_now`); a run's wait for commit_records.
        table = self.bind()
        ST.execute(table, self.ctx, "t_row")
        self.assertFalse(record_path(self.work / "m_x.out").exists())
        commit_records()
        self.write("work/x.in", "edited\n")
        ST.execute(table, self.ctx, "t_row", reuse=True, at_once=True)
        rec = json.loads(record_path(self.work / "m_x.out").read_text(encoding="utf-8"))
        self.assertEqual(rec["inputs"]["work/x.in"], hashlib.sha256(b"edited\n").hexdigest())
        self.calls.clear()
        ST.execute(table, self.ctx, "t_row", reuse=True, at_once=True)
        self.assertNotIn("build", [c[0] for c in self.calls])

    def test_a_declared_folder_stops_the_command_in_one_line(self):
        (self.work / "x.in").unlink()
        (self.work / "x.in").mkdir()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            ST.execute(self.bind(), self.ctx, "t_row")
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(err.getvalue().splitlines(), [
            "tjs: error: work/x.in is a folder where a file was expected — rename or "
            "remove it, then run again"])
        self.assertNotIn("build", [c[0] for c in self.calls])

    def test_the_outputs_run_are_kept_by_output(self):
        # Two rows that share an output (base and crypto_base) are one entry:
        # what a sweep of the outputs no row built compares.
        shared = "{work}/{a}_shared.out"
        rows = {"t_equity": ST.Stage("t_equity", ST.ACCOUNT, shared, (), when=ST._equity),
                "t_crypto": ST.Stage("t_crypto", ST.ACCOUNT, shared, (), when=ST._crypto)}
        with mock.patch.dict(ST.TABLE, rows), \
                mock.patch.object(run_state, "current", run_state.RunState()):
            table = ST.bound({n: ST.Steps(lambda i, r: ST.output(i).write_text("x\n"))
                              for n in rows})
            out = self.work / "m_shared.out"
            ST.execute(table, self.ctx, "t_equity", "t_crypto")
            self.assertEqual(run_state.current.stage_outputs,
                             {out: ("t_equity", "m", None, None, None)})
            crypto = ST.StageCtx(work=self.work, account="m", crypto=True)
            ST.execute(table, crypto, "t_equity", "t_crypto")
            self.assertEqual(run_state.current.stage_outputs,
                             {out: ("t_crypto", "m", None, None, None)})

    def _listed(self, instances: dict) -> None:
        self.write("work/.deps/run.json", json.dumps({"format": 2, "instances": instances}))

    def vouch(self, p: Path, out: object = True, fmt: int = 2) -> Path:
        """`p`'s record, as a run writes it: the hash of its bytes (of what
        it points to), another `out`, or none."""
        rp = record_path(p)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rec = {"format": fmt, "code": "c", "inputs": {}}
        if out is True:
            rec["out"] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        elif out is not None:
            rec["out"] = out
        rp.write_text(json.dumps(rec), encoding="utf-8")
        return p

    def built(self, rel: str, text: str = "x\n") -> Path:
        """A file a run built: its record vouches for it."""
        return self.vouch(self.write(rel, text))

    def test_the_last_run_is_swept_of_what_vanished_and_nothing_else(self):
        # work/.deps/run.json (TJ-4265): what the last run's instances built
        # and this run's did not goes, with the files beside it and its
        # record; what a current instance built stays, and so does a
        # spared row's (asked later) unless its account ran nothing (a
        # removed account's radar); an account taxjson.toml no longer names
        # leaves none of its own files (its stamps, its notes, a view's
        # full-history gains: TJ-4264).
        gone = self.built("work/m_webull.out")
        beside = self.write("work/m_webull.out.diag")
        rec = record_path(gone)
        kept = self.built("work/m_questrade.out")
        late = self.built("reports/fees.rpt")
        radar = [self.built("reports/wash_radar_old.rpt"),
                 self.write("reports/wash_radar_old.json")]
        old = [self.write(f"work/old{s}") for s in (
            "_sources.list", "_inputs.fingerprint", "_detect.diag")]
        old += [self.built("work/old_gains_all.json"), record_path(self.work / "old_gains_all.json")]
        mine = [self.write(rel) for rel in ("taxjson.toml", "inputs/m/x.csv",
                                            "work/m_sources.list")]
        t_row = {"row": "t_row", "account": "m", "broker": "webull"}
        self._listed({
            "work/m_webull.out": t_row,
            "work/m_questrade.out": {**t_row, "broker": "questrade"},
            "reports/fees.rpt": {"row": "fees"},
            "reports/wash_radar_old.rpt": {"row": "radar", "account": "old"},
            "taxjson.toml": t_row,
            "work/../inputs/m/x.csv": t_row,
        })
        with mock.patch.object(run_state, "current", run_state.RunState()):
            run_state.current.stage_outputs[kept] = ("t_row", "m", "questrade", None, None)
            self.assertEqual(ST.swept_accounts(self.work, ["m", "other"]), ["m"])
            swept = ST.sweep_vanished(self.work, spare=ST.LATE_ROWS, configured=["m"])
            self.assertEqual(sorted(swept.gone), [
                "reports/wash_radar_old.json", "reports/wash_radar_old.rpt",
                "work/m_webull.out", "work/m_webull.out.diag", "work/old_detect.diag",
                "work/old_gains_all.json", "work/old_inputs.fingerprint",
                "work/old_sources.list"])
            self.assertEqual(swept.kept, [])
            self.assertEqual([p.exists() for p in (gone, beside, rec, *radar, *old)],
                             [False] * 10)
            self.assertEqual([p.exists() for p in (kept, late, *mine)], [True] * 5)
            self.assertEqual(ST.commit_run(self.work, full=True), [])
            self.assertFalse(late.exists())
            listed = json.loads((self.work / ".deps/run.json").read_text(encoding="utf-8"))
            self.assertEqual(listed, {"format": 2, "instances": {"work/m_questrade.out": {
                "row": "t_row", "account": "m", "broker": "questrade", "tt": None,
                "tag": None}}})

    def test_the_sweep_takes_the_table_s_names_never_the_list_s(self):
        # TJ-4269, TJ-4273: a hand-edited or hostile run.json names nothing
        # the sweep removes but the table's own outputs, and none through a
        # link, even with a record planted that vouches for it: a folder
        # link inside work/ or reports/, files of yours there, a name the
        # row's instance would not write, a broker that is no export group,
        # an elections file, a folder in an output's place; a link in an
        # output's place goes, not what it points to.
        outside = self.root.parent / "outside"
        outside.mkdir()
        secrets = [outside / n for n in ("secret.txt", "target.txt", "wash_radar_old.rpt")]
        for p in secrets:
            p.write_text("keep\n", encoding="utf-8")
        inputs = [self.write(r) for r in ("inputs/margin/manifest.json", "inputs/crypto/sends.json")]
        os.symlink(self.root / "inputs", self.work / "in")
        os.symlink(outside, self.work / "sub")
        (self.root / "reports").mkdir()
        os.symlink(outside, self.root / "reports" / "link")
        self.vouch(self.work / "m_kraken.out", "0" * 64)
        os.symlink(outside / "target.txt", self.work / "m_kraken.out")
        self.vouch(self.work / "m_kraken.out")
        yours = [self.write(r) for r in ("reports/mynotes.txt", "reports/adir/x.txt",
                                         "work/saved_by_me.json")]
        # The table's names, but not the ones the list gives, or not one part,
        # or no export group's, or the elections', each vouched for.
        table_s = [self.built(r) for r in (
            "reports/m.sum", "work/.deps/keep_gains.json", "work/m_notes.out",
            "work/margin_manifest.json", "work/margin_generic-x_manifest.json",
            "work/m_tt_x_pending_elections.json")]
        (self.work / "m_rbc_direct.out").mkdir()
        ghost = {"row": "gains", "account": "ghost", "beside": ["work/sub/target.txt",
                                                                "inputs/margin/manifest.json"]}
        self._listed({
            "work/in/margin/manifest.json": ghost, "work/in/crypto/sends.json": ghost,
            "work/sub/secret.txt": ghost, "reports/link/secret.txt": ghost,
            "reports/mynotes.txt": {"row": "sum", "account": "mynotes"},
            "reports/adir/x.txt": ghost, "work/saved_by_me.json": ghost,
            "work/m_kraken.out": {"row": "t_row", "account": "m", "broker": "kraken"},
            "work/m_rbc_direct.out": {"row": "t_row", "account": "m", "broker": "rbc_direct"},
            "work/in_x.out": {"row": "t_row", "account": "in", "broker": "x/../../outside"},
            "work/ghost_gains.json": {"row": "gains", "account": "../outside/x"},
            "reports/notes.sum": {"row": "sum", "account": "m"},
            "work/.deps/keep_gains.json": {"row": "gains", "account": ".deps/keep"},
            "work/m_notes.out": {"row": "t_row", "account": "m", "broker": "notes"},
            "work/margin_manifest.json": {"row": "parse", "account": "margin",
                                          "broker": "manifest"},
            "work/margin_generic-x_manifest.json": {"row": "parse", "account": "margin",
                                                    "broker": "generic-x_manifest"},
            "work/m_tt_x_pending_elections.json": {"row": "tt", "account": "m",
                                                   "tt": "x_pending_elections.tt"},
        })
        with mock.patch.object(run_state, "current", run_state.RunState()):
            self.assertEqual(ST.sweep_vanished(self.work, configured=[]),
                             ST.Swept(["work/m_kraken.out"], []))
            self.assertFalse(os.path.lexists(self.work / "m_kraken.out"))
            self.assertEqual([p.exists() for p in (*secrets, *inputs, *yours, *table_s)],
                             [True] * 14)
            self.assertTrue((self.work / "m_rbc_direct.out").is_dir())
            # reports/ itself a link out of the project: nothing through it.
            shutil.rmtree(self.root / "reports")
            os.symlink(outside, self.root / "reports")
            self.vouch(self.root / "reports/wash_radar_old.rpt")
            self._listed({"reports/wash_radar_old.rpt": {"row": "radar", "account": "old"}})
            self.assertEqual(ST.sweep_vanished(self.work, configured=[]).gone, [])
            self.assertTrue(secrets[2].exists())

    def test_only_what_its_record_vouches_for_goes(self):
        # TJ-4274: an output goes only when a record taxjson wrote (format 2)
        # holds the hash of its bytes now. One edited since, or with no
        # record, no hash in it or an older format's, is kept and said; a
        # file beside it goes only with it, one with a record of its own only
        # when that record vouches for it; a removed account's own files only
        # when an output of its went; a link in an output's place goes alone
        # when what it points to is the output as built.
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "copy.out").write_text("as built\n", encoding="utf-8")
        (outside / "other.out").write_text("not as built\n", encoding="utf-8")
        edited = self.built("work/a_webull.out")
        edited.write_text("edited\n", encoding="utf-8")
        ours = self.built("work/a_kraken.out")
        diag = self.write("work/a_kraken.out.diag")
        self.built("work/a_questrade.out")
        diag_rec = self.vouch(self.write("work/a_questrade.out.diag"), "0" * 64)
        no_rec = self.write("work/b_webull.out")
        no_hash = self.vouch(self.write("work/b_kraken.out"), None)
        older = self.vouch(self.write("work/b_questrade.out"), True, fmt=1)
        b_files = [self.write(r) for r in ("work/b_sources.list", "work/b_detect.diag")]
        c_files = [self.write(r) for r in ("work/c_sources.list", "work/c_detect.diag")]
        for name, target in (("c_webull.out", "copy.out"), ("c_kraken.out", "other.out")):
            self.vouch(self.write(f"work/{name}", "as built\n"))
            (self.work / name).unlink()
            os.symlink(outside / target, self.work / name)
        # (a link to a pipe is never read: it would wait for a writer)
        os.mkfifo(outside / "pipe")
        self.vouch(self.write("work/c_rbc_direct.out"), "0" * 64)
        (self.work / "c_rbc_direct.out").unlink()
        os.symlink(outside / "pipe", self.work / "c_rbc_direct.out")
        absent = self.vouch(self.write("work/c_questrade.out"))
        absent.unlink()
        self._listed({f"work/{a}_{b}.out": {"row": "t_row", "account": a, "broker": b}
                      for a in "abc" for b in ("webull", "kraken", "questrade", "rbc_direct")})
        with mock.patch.object(run_state, "current", run_state.RunState()):
            swept = ST.sweep_vanished(self.work, configured=[])
        self.assertEqual(sorted(swept.gone), [
            "work/a_kraken.out", "work/a_kraken.out.diag", "work/a_questrade.out",
            "work/c_detect.diag", "work/c_sources.list", "work/c_webull.out"])
        self.assertEqual(sorted(swept.kept), [
            ("work/a_webull.out", ST.EDITED), ("work/b_kraken.out", ST.NOT_OURS),
            ("work/b_questrade.out", ST.NOT_OURS), ("work/b_webull.out", ST.NOT_OURS),
            ("work/c_kraken.out", ST.EDITED), ("work/c_rbc_direct.out", ST.EDITED)])
        self.assertEqual([p.exists() for p in (ours, diag)], [False] * 2)
        self.assertEqual([p.exists() for p in (edited, diag_rec, no_rec, no_hash, older,
                                               *b_files)], [True] * 7)
        self.assertEqual([p.is_file() for p in (*c_files,)], [False] * 2)
        self.assertTrue(os.path.islink(self.work / "c_kraken.out"))
        self.assertEqual(sorted(p.name for p in outside.iterdir()),
                         ["copy.out", "other.out", "pipe"])
        self.assertFalse(record_path(absent).exists())

    def test_a_row_whose_when_reads_another_s_finding_runs_after_it(self):
        # The native books: built unless the raw row's every_run found mixed
        # currencies (`raw_mixed`), so they name it in `after`.
        found = {"mixed": False}

        def every_run(i, _r):
            i.ctx.raw_mixed = found["mixed"]
        rows = {"t_raw": ST.Stage("t_raw", ST.ACCOUNT, "{work}/{a}_t_raw.out", ()),
                "t_native": ST.Stage("t_native", ST.ACCOUNT, "{work}/{a}_t_native.out", (),
                                     when=lambda i: not i.ctx.raw_mixed, after=("t_raw",))}
        with mock.patch.dict(ST.TABLE, rows):
            build = ST.Steps(lambda i, r: ST.output(i).write_text("x\n"))
            table = ST.bound({"t_raw": build._replace(every_run=every_run), "t_native": build})
            with self.assertRaises(LookupError) as cm:
                ST.execute(table, ST.StageCtx(work=self.work, account="m"), "t_native")
            self.assertIn("t_raw", str(cm.exception))
            ran = ST.execute(table, ST.StageCtx(work=self.work, account="m"), "t_raw", "t_native")
            self.assertEqual([i.row for i in ran], ["t_raw", "t_native"])
            found["mixed"] = True
            ran = ST.execute(table, ST.StageCtx(work=self.work, account="m"), "t_raw", "t_native")
            self.assertEqual([i.row for i in ran], ["t_raw"])

    def test_the_instances_run_in_order_and_build_every_run_or_when_stale(self):
        table = self.bind()
        self.assertIsNone(ST.TABLE["t_row"].build, "the table itself is unchanged")
        ran = ST.execute(table, self.ctx, "t_row")
        self.assertEqual([i.broker for i in ran], ["x", "q"])     # q last; skip not run
        self.assertEqual(self.calls, [
            ("prepare", "x"), ("build", "x", "prep-x"),
            ("every_run", "x", ["m_sources.list", "x.in"]),
            ("prepare", "q"), ("build", "q", "prep-q"),
            ("every_run", "q", ["m_sources.list", "q.in"])])
        commit_records()
        # The record holds the inputs the row declares.
        import json
        rec = json.loads(record_path(self.work / "m_x.out").read_text(encoding="utf-8"))
        self.assertEqual(sorted(rec["inputs"]), ["work/m_sources.list", "work/x.in"])
        # A run: nothing changed, every instance built again.
        self.calls.clear()
        ST.execute(table, self.ctx, "t_row")
        self.assertEqual([c[1] for c in self.calls if c[0] == "build"], ["x", "q"])
        commit_records()
        # Reused and fresh: prepared and given its every_run, not built.
        self.calls.clear()
        ST.execute(table, self.ctx, "t_row", reuse=True)
        self.assertNotIn("build", [c[0] for c in self.calls])
        self.assertEqual([c[0] for c in self.calls], ["prepare", "every_run"] * 2)
        # An input changed: that instance only; a run or a ground: each.
        self.write("work/q.in", "edited\n")
        self.calls.clear()
        ST.execute(table, self.ctx, "t_row", reuse=True)
        self.assertEqual([c[1] for c in self.calls if c[0] == "build"], ["q"])
        commit_records()
        for reuse, grounds in ((False, False), (True, True)):
            self.grounds = grounds
            self.calls.clear()
            ST.execute(table, self.ctx, "t_row", reuse=reuse)
            self.assertEqual([c[1] for c in self.calls if c[0] == "build"], ["x", "q"])
            commit_records()


class TestTheTable(unittest.TestCase):

    def test_the_names_are_unique(self):
        names = [st.name for st in ST.ROWS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sorted(set(names) & set(ST.STAMPS)), [], "a row and a stamp share a name")
        for st in ST.ROWS:
            self.assertIn(st.per, ST.PER, st.name)
        for per, _path in ST.STAMPS.values():
            self.assertIn(per, ST.PER)

    def test_every_reference_names_a_row_a_stamp_or_a_selection(self):
        for st in ST.ROWS:
            for r in st.inputs:
                with self.subTest(row=st.name, ref=r):
                    self.assertIn(r.keep, (ST.ANY, ST.FILE, ST.NONEMPTY, ST.NOT_DIR))
                    if isinstance(r, ST.Out):
                        self.assertIn(r.row, set(ST.TABLE) | set(ST.STAMPS))
                    elif isinstance(r, ST.Tree):
                        self.assertTrue(r.select.startswith("glob:") or r.select in ST.TREES)

    def test_the_file_names_are_the_stages(self):
        # The table spells the names the stages write (the golden books'
        # goldens hold them in the runs too).
        self.assertTrue(ST.TRANSFER_COSTS.endswith(stages.TRANSFER_COSTS_SUFFIX))
        self.assertTrue(ST.OWN_MOVES.endswith(stages.OWN_MOVES_SUFFIX))
        self.assertTrue(ST.STAMPS["missing_history"][1].endswith(stages.MISSING_HISTORY_STATE))

    def test_every_ask_is_the_executor_s(self):
        # One `needs_rebuild(` call in the package (past freshness itself):
        # the executor's, over the inputs its row declares.
        asks = []
        for p in sorted(SRC.rglob("*.py")):
            mod = p.relative_to(SRC).as_posix()
            if mod == "pipeline/freshness.py":
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            own = set()
            if mod == "pipeline/stage_table.py":
                fn = next(d for d in tree.body
                          if isinstance(d, ast.FunctionDef) and d.name == "execute")
                own = {id(n) for n in ast.walk(fn)}
            for n in ast.walk(tree):
                f = n.func if isinstance(n, ast.Call) else None
                if ((isinstance(f, ast.Name) and f.id == "needs_rebuild")
                        or (isinstance(f, ast.Attribute) and f.attr == "needs_rebuild")):
                    asks.append("execute" if id(n) in own else f"{mod}:{n.lineno}")
        self.assertEqual(asks, ["execute"], "a `needs_rebuild` ask outside the executor: "
                                            "give its stage a row (pipeline/stage_table.ROWS) "
                                            "and run it with `execute`")

    def test_only_a_fetched_row_reuses_its_record(self):
        # The owner's rule (2026-11-17, "i don't want to take any chance of
        # stale data"): a run and a view build every derived output every
        # time. The one call that may skip a build on its record (`reuse`)
        # names rows of data fetched from outside the project (`fetched`:
        # the rates), and `execute` refuses any other (TJ-4307).
        self.assertEqual([st.name for st in ST.ROWS if st.fetched], ["rates"],
                         "a row marked fetched: only data from outside the project "
                         "(the rates) may be reused on its record")
        reuses = []
        for p in sorted(SRC.rglob("*.py")):
            mod = p.relative_to(SRC).as_posix()
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if not isinstance(n, ast.Call):
                    continue
                kw = {k.arg: k.value for k in n.keywords}
                f = n.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                if name == "needs_rebuild" and "force" in kw:
                    self.assertEqual(ast.unparse(kw["force"]), "not reuse",
                                     f"{mod}:{n.lineno}: the ask's force is the executor's")
                if name != "execute" or "reuse" not in kw:
                    continue
                rows = [a.value for a in n.args[2:] if isinstance(a, ast.Constant)]
                self.assertTrue(isinstance(kw["reuse"], ast.Constant) and kw["reuse"].value is True
                                and len(rows) == len(n.args) - 2,
                                f"{mod}:{n.lineno}: `reuse=True` and the rows named, "
                                f"so this test can read them")
                reuses.append((mod, rows))
        self.assertEqual(reuses, [("pipeline/stages.py", ["rates"])],
                         "a call that reuses on a record: name only a fetched row")

    def test_every_row_is_bound_once(self):
        # Each row's steps are bound by the module whose code they are: the
        # run's stages, run.py's sheltered merge, the views' on-demand builds.
        tables = {"pipeline/stages": stages._STAGES, "pipeline/run": run._RUN_STAGES,
                  "cli/common": common._ON_DEMAND}
        bound = [(name, mod) for mod, table in tables.items()
                 for name, st in table.items() if st.build is not None]
        names = [name for name, _mod in bound]
        self.assertEqual(sorted(n for n in set(names) if names.count(n) > 1), [],
                         "a row bound in two modules")
        self.assertEqual(sorted(set(ST.TABLE) - set(names)), [], "a row no module binds")

    def test_the_rows_that_share_an_output(self):
        # Kept by output, not by row (run_state.stage_outputs), and never
        # both for one account: base for an equity account, crypto_base for
        # a crypto one; gains_all for an account alone, gains_all_split for
        # one the run blended (_gains_all_file); split for a blended
        # account's wash file, wash for a crypto account's own pass;
        # holdings for an equity account, crypto_holdings for a crypto one.
        by_out = {}
        for st in ST.ROWS:
            if isinstance(st.out, str):
                by_out.setdefault(st.out, []).append(st.name)
        self.assertEqual(sorted(sorted(rows) for rows in by_out.values() if len(rows) > 1),
                         [["base", "crypto_base"], ["crypto_holdings", "holdings"],
                          ["gains_all", "gains_all_split"], ["split", "wash"]])
        for crypto in (False, True):
            i = ST.Inst("base", ST.StageCtx(work=Path("work"), account="m", crypto=crypto))
            self.assertEqual([ST.TABLE[r].when(i) for r in ("base", "crypto_base")],
                             [not crypto, crypto])

    def test_the_native_books_run_after_raw(self):
        # Their `when` reads what the raw row's every_run found (`raw_mixed`):
        # the native books and the holdings handoff made from them.
        for st in ST.ROWS:
            with self.subTest(row=st.name):
                self.assertEqual(st.after, ("raw",) if st.when is ST._native else ())
        self.assertEqual(sorted(st.name for st in ST.ROWS if st.after),
                         ["holdings", "raw_base", "raw_base_gains", "raw_gains"])


if __name__ == "__main__":
    unittest.main()
