"""`tjs clean` removes what `tjs run` generated and nothing else (lib/clean,
cli/books `cmd_clean`).

- The demo project: a clean leaves exactly what `tjs init --demo` made (every
  generated file gone, every file of the project's own as it was), and the
  run after it writes the fresh run's work/, reports/ and exports/ again,
  byte for byte (the clock and the files' times masked); a second clean has
  nothing to remove.
  Files of the user's in work/ and reports/ (the 2026-11-22 review's set)
  stay, named in the default output and counted nowhere.
- A planted project of year folders (no run needed): every protected kind
  survives (the settings, maps, inputs with their .tt, manifest.json,
  sends.json and .bak files, holdings/, slips, filed/, snapshot/,
  inputs_redact/, checklist.json, an older taxjson's export/), and so does
  what work/ keeps (a lock, the slip-key salt, watch's baseline, the price
  caches, the generated crypto-sends .tt, an older layout's manifest, a
  saved compare snapshot) and any file taxjson did not write; every
  generated kind goes (the books, the records, the checklist's verdicts,
  the drift verdict, compare/prev, the reports, the retired watchlist
  files, the export the year wrote, an account's files since removed from
  taxjson.toml, through the records naming it).
- A place of the user's files (a [settings] folder, an account's holdings
  file, a default folder through a link) that is, is inside or holds work/
  or reports/ refuses the project in one line, nothing removed in any year.
- `--dry-run` removes nothing; `--json` carries the registry's number.
- The folder holding the years cleans every year; a year folder its own
  (another year's export left alone; one whose record names no year only
  from the folder holding the years, which also names its own stray
  export/, work/ and reports/).
- Links: a work/ linked out of the project (or inside it) is refused with
  nothing removed; a link inside work/ is removed as a link; an exports
  folder outside the project or reached through a link is left alone, said.
- A held run lock refuses in one line, nothing removed.
- A clean that stops part-way (a folder it cannot remove, an interrupt)
  has removed the run's record of what it read first: the books' commands
  refuse what is left; the failure names the path from the project.
- A filed year (a real close-year): its export goes, from its folder and
  from the folder holding the years.
- After a clean the commands that need the books refuse as before any run
  (slip-audit too, and its --template), and the checklist shows no verdict
  from before.

All data synthetic."""
import fcntl
import json
import os
import re
import shutil
import stat
import unittest
from pathlib import Path
from unittest import mock

import _paths
from _golden import _masked     # (imports tests/_hermetic first: a synthetic HOME, offline)
from _support.cli_command_runs import _tjs
from _tmpfiles import private_dir

from taxjson.lib import clean as CL
from taxjson.lib import json_contract as J

REPO = _paths.REPO
_MTIME = re.compile(r'"mtime_ns": \d+')

_SETTINGS = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
             'country = "canada"\nbase_currency = "CAD"\n'
             'source_currencies = ["USD"]\noption_grant_timing_since = {year}\n'
             '{extra}[accounts.margin]\ntype = "taxable"\n')
_TT = ("BUYSELL 2024-01-08 10:00:00 ZZQA.TO 100 CAD 10 -1000 0\n"
       "BUYSELL 2024-03-04 10:00:00 ZZQA.TO -40 CAD 12 480 0\n")
# Files of the user's in work/ and reports/ (the 2026-11-22 reviews' sets):
# none is a name taxjson writes, though some begin as an account's files do
# (margin, crypto), a blend's or a radar's, or end as a stage's stderr.
_NOT_OURS = ("work/notes.txt", "work/My Sheet.xlsx", "work/draft.tt",
             "work/mine/keep.txt", "reports/2023_final.sum",
             "reports/accountant.rpt", "reports/broker_holdings.toml",
             "work/margin_notes.txt", "work/margin_2023_final.sum",
             "work/.margin_private", "work/crypto_sends_draft.tt",
             "work/.blend_mine", "work/notes.diag", "work/mine.traces",
             "reports/wash_radar_mine.json", "reports/notes.diag")


def _tree(root: Path) -> dict:
    """Every entry under `root`: a file's bytes, a link's target, a folder."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for n in dirs + files:
            p = Path(dirpath) / n
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir",)
            else:
                out[rel] = ("file", p.read_bytes())
    return out


def _tree_but_lock(root: Path) -> dict:
    """`_tree` of the demo without 2024's run lock (it stays with a work/
    that keeps a file)."""
    out = _tree(root)
    out.pop("2024/work/.run.lock", None)
    return out


def _generated(root: Path) -> dict:
    """The demo's year folder's work/ and reports/ and the root exports/,
    each file's text with the clock, digests and file times masked."""
    out = {}
    for sub in ("2024/work", "2024/reports", "exports"):
        for p in sorted((root / sub).rglob("*")):
            if p.is_file() and not p.is_symlink():
                text = p.read_bytes().decode("utf-8", "replace")
                out[p.relative_to(root).as_posix()] = _MTIME.sub(
                    '"mtime_ns": 0', _masked(text, str(root)))
    return out


def _w(p: Path, text: str = "synthetic\n") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _years(base: Path, years=(2024,), newest_export=None) -> Path:
    """A project of year folders, planted (never run): shared inputs/,
    exports/ written by the `newest_export` year's run (default the last
    year), and in each year work/ and reports/ as a run leaves them."""
    root = base / "proj"
    _w(root / "inputs/margin/trades.tt", _TT)
    _w(root / "tobase.map", "# synthetic\n")
    extra = 'inputs_dir = "../inputs"\nexports_dir = "../exports"\n'
    for y in years:
        d = root / str(y)
        _w(d / "taxjson.toml", _SETTINGS.format(year=y, extra=extra))
        _w(d / "work/margin_gains.json", '{"transactions": []}\n')
        _w(d / "work/.deps/margin_gains.json.json", "{}\n")
        _w(d / "reports/margin.sum", "SUMMARY\n")
    y = newest_export or years[-1]
    names = ["margin_holdings.toml", "README.txt"]
    for n in names:
        _w(root / "exports" / n)
    _w(root / "exports/.taxjson-exports.json", json.dumps(
        {"schema_version": 1, "year": y, "files": sorted(names)}))
    return root


class TestCleanTheDemo(unittest.TestCase):
    """The demo project, run: clean, then run again."""

    def test_clean_then_run_rebuilds_the_fresh_run(self):
        root = Path(private_dir()) / "demo"
        r = _tjs(None, "init", "--demo", str(root))
        self.assertEqual(r.returncode, 0, r.stderr)
        made = _tree(root)
        year = root / "2024"
        r = _tjs(year, "run")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        fresh = _generated(root)
        self.assertTrue(any(k.startswith("2024/work/") for k in fresh))
        self.assertTrue(any(k.startswith("2024/reports/") for k in fresh))
        self.assertTrue(any(k.startswith("exports/") for k in fresh))
        # A second run leaves compare/prev and more: all of it goes too.
        r = _tjs(year, "run")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertTrue((year / "work/compare/prev").is_dir())
        # The user's own files in work/ and reports/: never taxjson's.
        mine = [_w(year / n) for n in _NOT_OURS]
        made.update({p.relative_to(root).as_posix(): ("file", p.read_bytes())
                     for p in mine})
        for p in mine:
            for parent in p.relative_to(root).parents:
                if parent.as_posix() not in (".", "2024"):
                    made.setdefault(parent.as_posix(), ("dir",))

        before = _tree(root)
        r = _tjs(year, "clean", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Would remove", r.stdout)
        self.assertIn("nothing was removed", r.stdout)
        for folder in ("work/:", "reports/:", "../exports/:"):
            self.assertIn(folder, r.stdout)
        self.assertEqual(_tree(root), before, "a dry run removes nothing")
        # What a run and the views wrote is all recognised: only the
        # user's files are left as not taxjson's.
        r = _tjs(year, "clean", "--dry-run", "--json")
        self.assertEqual(sorted(json.loads(r.stdout)["not_written"]),
                         sorted(n.replace("/keep.txt", "/") for n in _NOT_OURS))

        r = _tjs(year, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Removed", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(_tree_but_lock(root), made,
                         "the project as `tjs init --demo` made it, and the "
                         "user's files: every generated file gone, every "
                         "other one as it was (work/ stays, with its lock)")
        # The files of the user's are named without --details (the first
        # few, then how many more), as not taxjson's: never counted as
        # ones the run wrote.
        n = len(_NOT_OURS)
        self.assertIn(f"left {n} item(s) taxjson did not write", r.stderr)
        self.assertIn(f"+{n - 8} more (`tjs clean --details` lists them)", r.stderr)

        r = _tjs(year, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to remove", r.stdout)
        self.assertEqual(_tree_but_lock(root), made)
        for p in mine + [year / "work/.run.lock"]:
            p.unlink()
        for d in ("work/mine", "work", "reports"):
            (year / d).rmdir()

        # The books' commands refuse as before any run.
        r = _tjs(year, "sum")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("run` first", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

        r = _tjs(year, "run")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        again = _generated(root)
        self.assertEqual(sorted(again), sorted(fresh))
        for k in fresh:
            self.assertEqual(again[k], fresh[k], k)


class TestWhatStaysAndWhatGoes(unittest.TestCase):

    def test_every_protected_kind_survives_and_every_generated_kind_goes(self):
        root = _years(Path(private_dir()))
        y = root / "2024"
        own = [
            y / "ticker.map", root / "tobase.map", y / "checklist.json",
            root / "inputs/margin/manifest.json", root / "inputs/margin/sends.json",
            root / "inputs/margin/trades.tt.bak", root / "inputs/margin/crypto_sends.tt",
            y / "inputs/slips/slips.toml", y / "holdings/margin.toml",
            y / "filed/2024.json", y / "filed/2024/reports/margin.sum",
            root / "snapshot/2024.20261101-101000/2024/taxjson.toml",
            y / "inputs_redact/margin/trades.tt", y / "export/old.csv",
            root / ".gitignore",
        ]
        kept = [
            y / "work/.fetch.lock", y / "work/.slip_key_salt",
            y / "work/.watch_state.json", y / "work/.close_cache.json",
            y / "work/.price_cache.json", y / "work/.history_cache.json",
            y / "work/crypto_sends/margin/crypto_sends.tt",
            y / "work/margin_manifest.json", y / "work/compare/base/snapshot.json",
            y / "reports/notes.txt", y / "reports/exports/mine.csv",
            root / "exports/mine.txt",
        ]
        gone = [
            y / "work/margin_base.json", y / "work/margin_gains.json.diag",
            y / "work/.inputs_fingerprint.json", y / "work/checklist_results.json",
            y / "work/filed_check.state", y / "work/margin_pending_elections.json",
            y / "work/compare/prev/snapshot.json", y / "work/compare/.partial-run",
            y / "work/.deps/reports/margin.sum.json",
            y / "reports/margin_holdings.toml", y / "reports/wash_radar_margin.json",
            y / "reports/wash_radar_margin.rpt", y / "reports/run_summary.json",
            y / "reports/fees.rpt", y / "reports/margin.sum.0123abcd.part",
            y / "reports/exports/margin_SA.csv",
            # An account taxjson.toml no longer names: its records name it.
            y / "work/zold_gains.json", y / "work/.deps/zold_gains.json.json",
            y / "work/zold_sources.list", y / "work/.zold_gains.timeline.json",
            y / "reports/zold.sum", y / "reports/zold_wash.sum",
            y / "work/.deps/reports/zold_holdings.toml.json",
            y / "reports/zold_holdings.toml",
        ]
        for p in own + kept + gone:
            _w(p)
        # The export the 2024 run wrote, recorded (_years).
        gone += [root / "exports/margin_holdings.toml", root / "exports/README.txt",
                 root / "exports/.taxjson-exports.json"]
        (y / "work/.run.lock").touch()
        survive = {p: p.read_bytes() for p in own + kept}
        r = _tjs(y, "clean", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        for p, data in survive.items():
            self.assertEqual(p.read_bytes(), data, p)
        for p in gone:
            self.assertFalse(os.path.lexists(p), p)
        # What work/ keeps is said, with why (--details).
        for name in (".slip_key_salt", ".watch_state.json", "compare/base/",
                     "reports/notes.txt", "margin_manifest.json"):
            self.assertIn(name, r.stderr)
        self.assertIn("2024 is filed", r.stderr)
        self.assertIn("export/", r.stderr)
        self.assertIn("taxjson did not write", r.stderr)
        # The run lock stays beside what work/ keeps.
        self.assertTrue((y / "work/.run.lock").exists())

    def test_what_taxjson_did_not_write_stays_and_is_named(self):
        """Recognised by full name, never by how a name begins: a file
        that starts as an account's, a blend's or a radar's does, or ends
        as a stage's stderr, stays (the 2026-11-22 reviews' sets); so does
        a folder a record names, and an account's book no record and no
        taxjson.toml table ties to taxjson (a removed account's)."""
        root = _years(Path(private_dir()))
        y = root / "2024"
        mine = [_w(y / n) for n in _NOT_OURS]
        # A record naming a folder: honoured only for a file.
        mine.append(_w(y / "work/sub/file.txt"))
        _w(y / "work/.deps/sub.json", "{}\n")
        mine.append(_w(y / "work/zgone_base.json"))
        theirs = sorted(n.replace("/keep.txt", "/") for n in _NOT_OURS)
        theirs = sorted(theirs + ["work/sub/", "work/zgone_base.json"])
        r = _tjs(y, "clean", "--dry-run", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual(sorted(doc["not_written"]), theirs)
        r = _tjs(y, "clean", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in mine:
            self.assertTrue(p.is_file(), p)
        self.assertFalse((y / "work/margin_gains.json").exists())
        self.assertFalse((y / "reports/margin.sum").exists())
        for n in theirs:
            self.assertIn(n, r.stderr)
        # The book and the records, the report, the export: the files of
        # the user's are no part of the count.
        self.assertEqual({f["folder"]: f["files"] for f in doc["folders"]},
                         {"work/": 3, "reports/": 1, "../exports/": 3})
        self.assertEqual(doc["files"], 7)

    def test_the_kept_names_are_their_owners(self):
        from taxjson.lib import checklist, compare, crypto_sends, records, slip_audit
        from taxjson.lib.first_run import SUMMARY_FILE
        from taxjson.pipeline import run, stage_table, stages
        self.assertEqual(CL.SLIP_KEY_SALT, slip_audit.KEY_SALT_FILE)
        from taxjson.cli import technicals
        self.assertEqual(CL.HISTORY_CACHE, technicals.HISTORY_CACHE)
        self.assertTrue(CL.kept_why(CL.HISTORY_CACHE, False))
        self.assertEqual(CL.CRYPTO_SENDS, crypto_sends.GENERATED_DIR)
        self.assertEqual(CL.COMPARE, compare.SNAP_ROOT)
        self.assertEqual(CL.COMPARE_RUN, (compare.PREV, compare.PARTIAL_MARK))
        self.assertEqual(CL.CHECKLIST_RESULTS, checklist.RESULTS_FILE)
        self.assertEqual(CL.DEPS, records.DEPS_DIR)
        self.assertEqual(CL.RETIRED_EXPORTS, stages._RETIRED_EXPORTS)
        self.assertIn(CL.LEGACY_MANIFEST, stage_table._NEVER_SWEPT)
        self.assertIn(SUMMARY_FILE, CL.REPORT_FILES)
        self.assertEqual(run._EXPORTS_MANIFEST, ".taxjson-exports.json")
        from taxjson.lib import (cross_listings, dated_events, fingerprint_record,
                                 in_kind, loss_overrides, similar, xlist_state)
        from taxjson.lib.brokerages import generic
        from taxjson.bin import taxjson_redact
        from taxjson.lib import project_layout as PL
        self.assertEqual(CL.FINGERPRINT, fingerprint_record.FINGERPRINT_FILE)
        self.assertEqual(CL.FILED_CHECK, PL.FILED_CHECK_STATE)
        for name in (fingerprint_record.SET_ASIDE_FILE, stages.RATES_START_STAMP,
                     stages.MISSING_HISTORY_STATE, in_kind.IN_KIND_FILE,
                     stages._SKIPPED_ACCOUNTS_FILE, PL.EFFECTIVE_MAP,
                     dated_events.STATE, similar.STATE, xlist_state.STATE,
                     cross_listings.STATE, loss_overrides.STATE):
            self.assertIn(name, CL.WORK_FILES)
        self.assertTrue(set(stage_table._LEGACY) <= CL.LEGACY_WORK)
        self.assertIn(generic.LAYOUTS_DIR, CL._USER_PLACES)
        self.assertIn(taxjson_redact.TREE_DEFAULT, CL._USER_PLACES)
        self.assertIn(checklist.STATE_FILE, CL._USER_PLACES)

    def test_the_names_are_the_writers(self):
        """Every name the stage table gives a file (each row's output and
        stamp, what its build writes beside it, an account's own files),
        rendered for an account, an export group, a .tt file and a blend,
        is one lib/clean lists, and each one it lists beyond them is
        another writer's, found in that writer's code. The export groups
        are the parsers', the blends the stage table's."""
        from taxjson.parsers.registry import SPECS
        from taxjson.pipeline import stage_table as ST
        root = Path(private_dir()) / "p"
        work = root / "work"
        ctx = ST.StageCtx(work=work, account="zacct", tag="ztag", root_dir=root)
        marks = (("zacct", "{a}"), ("zgrp", "{b}"), ("ztt", "{t}"), ("ztag", "{tag}"))

        def spec(q: Path):
            s = q.name
            for v, k in marks:
                s = s.replace(v, k)
            return q.parent.name, s
        seen = set()
        for row, st in ST.TABLE.items():
            out = ST._path(st.out, ST.Inst(row, ctx, broker="zgrp", tt=Path("ztt.tt")))
            seen |= {spec(q) for q in [out, *ST.companions(row, out)]}
        for name, (_per, path) in ST.STAMPS.items():
            out = ST._path(path, ST.Inst(name, ctx, broker="zgrp"))
            if out is not None:
                seen.add(spec(out))
        seen |= {spec(q) for q in ST.account_files(work, "zacct", ["zgrp"])
                 if q.parent == work}
        work_seen = {s for d, s in seen if d == "work"}
        reports_seen = {s for d, s in seen if d == "reports"}
        self.assertEqual(work_seen | reports_seen, {s for _d, s in seen})
        self.assertGreater(len(work_seen), 80)
        per = set(CL.ACCOUNT_WORK) | set(CL.GROUP_WORK) | set(CL.TT_WORK) | set(CL.BLEND_WORK)
        self.assertEqual(work_seen - per - CL.WORK_FILES, set())
        self.assertEqual(reports_seen - set(CL.ACCOUNT_REPORTS) - CL.REPORT_FILES, set())
        # Listed beyond the table: other writers' names, each in its code.
        src = REPO / "src" / "taxjson"
        others = {"{a}_corp_spinoff_value.diag": ("pipeline/inputs.py", '_corp_spinoff_value.diag"'),
                  "{a}_partial_carryover.diag": ("pipeline/inputs.py", '_partial_carryover.diag"'),
                  "{a}_expired_options.diag": ("pipeline/inputs.py", '_expired_options.diag"'),
                  "{a}_crypto_sends.diag": ("pipeline/stages.py", '_crypto_sends.diag"'),
                  "{a}_pending_elections.json": ("pipeline/stages.py", '_pending_elections.json"'),
                  "{a}_{b}.overrides": ("pipeline/inputs.py", 'out.stem + ".overrides"'),
                  ".{tag}_all_base.json": ("cli/common.py", '_all_base.json"')}
        self.assertEqual(per - work_seen, set(others))
        for name, (where, frag) in others.items():
            self.assertIn(frag, (src / where).read_text(encoding="utf-8"), name)
        self.assertEqual(CL.REPORT_FILES - reports_seen, {"run_summary.json", "fx_cash.rpt"})
        self.assertIn('reports_dir / "fx_cash.rpt"',
                      (src / "pipeline" / "run.py").read_text(encoding="utf-8"))
        # The books (`holds_books`) are the rows' outputs per account.
        rows = {s for d, s in (spec(ST._path(st.out, ST.Inst(r, ctx, broker="zgrp",
                                                             tt=Path("ztt.tt"))))
                               for r, st in ST.TABLE.items())
                if d == "work" and s.startswith("{a}_") and s.endswith(".json")}
        self.assertEqual(set(CL.ACCOUNT_BOOKS) | set(CL.GROUP_BOOKS) | set(CL.TT_BOOKS), rows)
        # The groups a parse runs per, the blends.
        self.assertEqual(CL.PARSE_GROUPS, {s.id for s in SPECS})
        self.assertEqual(CL.BLEND_TAGS, {s[len("{a}_"):-len(".diag")] for s in work_seen
                                         if s.startswith("{a}_") and s.endswith("blend.diag")})
        # Each name, for a real account and group, is recognised.
        k = CL.Known(frozenset({"zacct"}), tts=frozenset({"ztt"}))
        for d, s in sorted(seen):
            name = s.format(a="zacct", b="ib", t="ztt", tag="blend")
            with self.subTest(name=name):
                if d == "work":
                    self.assertTrue(CL.work_ours(name, False, k), name)
                else:
                    self.assertTrue(CL.report_ours(name, k), name)
        src = REPO / "src" / "taxjson"
        plan = (src / "cli" / "plan.py").read_text(encoding="utf-8")
        self.assertIn(f'"{CL.WATCH_STATE}"', plan)
        self.assertIn(f'"{CL.PRICE_CACHE}"', plan)
        self.assertIn(f'"{CL.CLOSE_CACHE}"',
                      (src / "pipeline" / "stages.py").read_text(encoding="utf-8"))

    def test_no_tt_file_is_ever_removed(self):
        """Whatever names it (a record, an export's record, a name like an
        account's file), a .tt stays, and so does a folder holding one."""
        root = _years(Path(private_dir()))
        y = root / "2024"
        tts = [_w(y / n, _TT) for n in (
            "work/margin_gains.tt", "work/margin_x.tt", "reports/margin.tt",
            "reports/margin.sum.tt", "work/.deps/keep.tt",
            "work/compare/prev/reports/a.tt")]
        _w(y / "work/.deps/margin_x.tt.json", "{}\n")
        _w(y / "work/.deps/reports/margin.tt.json", "{}\n")
        _w(y / "work/compare/prev/snapshot.json")
        rec = root / "exports/.taxjson-exports.json"
        doc = json.loads(rec.read_text(encoding="utf-8"))
        doc["files"].append("trades.tt")
        rec.write_text(json.dumps(doc), encoding="utf-8")
        tts.append(_w(root / "exports/trades.tt", _TT))
        for n in ("x.tt", "X.TT", "a.Tt"):
            self.assertTrue(CL.never_removed(n), n)
        k = CL.Known(frozenset({"margin"}), work=frozenset({"margin_x.tt"}),
                     reports=frozenset({"margin.tt"}))
        self.assertFalse(CL.work_ours("margin_x.tt", False, k))
        self.assertFalse(CL.report_ours("margin.tt", k))
        r = _tjs(y, "clean", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in tts:
            self.assertTrue(p.is_file(), p)
        for d in ("work/.deps/", "work/compare/prev/"):
            self.assertIn(f"{d}: it holds a .tt file", r.stderr)
        self.assertFalse((y / "work/margin_gains.json").exists())
        self.assertFalse((y / "reports/margin.sum").exists())
        self.assertFalse((root / "exports/margin_holdings.toml").exists())
        self.assertIn("../exports/trades.tt", r.stderr)

    def test_json_and_dry_run(self):
        root = _years(Path(private_dir()))
        before = _tree(root)
        r = _tjs(root / "2024", "clean", "--dry-run", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema_version"], J.SCHEMAS["clean"])
        self.assertTrue(doc["dry_run"])
        self.assertEqual({f["folder"] for f in doc["folders"]},
                         {"work/", "reports/", "../exports/"})
        self.assertEqual(doc["files"], sum(f["files"] for f in doc["folders"]))
        self.assertEqual(_tree(root), before)
        r = _tjs(root / "2024", "clean", "--json")
        doc = json.loads(r.stdout)
        self.assertFalse(doc["dry_run"])
        self.assertFalse((root / "2024/work").exists())
        r = _tjs(root / "2024", "clean", "--json")
        self.assertEqual(json.loads(r.stdout)["folders"], [])


class TestYearFolders(unittest.TestCase):

    def test_a_year_folder_cleans_its_own_the_root_every_year(self):
        root = _years(Path(private_dir()), years=(2024, 2025))
        r = _tjs(root / "2024", "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((root / "2024/work").exists())
        self.assertFalse((root / "2024/reports").exists())
        self.assertTrue((root / "2025/work/margin_gains.json").is_file())
        self.assertTrue((root / "2025/reports/margin.sum").is_file())
        # The newest year's export is not 2024's.
        self.assertTrue((root / "exports/.taxjson-exports.json").is_file())
        r = _tjs(root, "clean", "--dry-run")
        self.assertIn("2025/work/:", r.stdout)
        self.assertIn("exports/:", r.stdout)
        r = _tjs(root, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in ("2025/work", "2025/reports", "exports"):
            self.assertFalse((root / p).exists(), p)
        self.assertTrue((root / "inputs/margin/trades.tt").is_file())
        self.assertTrue((root / "2025/taxjson.toml").is_file())

    def test_outside_a_project_the_usual_refusal(self):
        d = Path(private_dir())
        r = _tjs(d, "clean")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not a taxjson project", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestLinks(unittest.TestCase):

    def test_a_work_folder_linked_elsewhere_is_refused(self):
        base = Path(private_dir())
        outside = base / "outside"
        _w(outside / "a_gains.json")
        for target in (outside, Path("inner")):
            with self.subTest(target=str(target)):
                root = _years(base / target.name, years=(2024, 2025))
                shutil.rmtree(root / "2024/work")
                if not target.is_absolute():
                    _w(root / "2024/inner/a_gains.json")
                (root / "2024/work").symlink_to(target)
                before = _tree(root)
                for where in (root / "2024", root):
                    r = _tjs(where, "clean")
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn("symlink", r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertEqual(_tree(root), before, "nothing removed")
                self.assertTrue((outside / "a_gains.json").is_file())

    def test_a_link_inside_work_is_removed_as_a_link(self):
        base = Path(private_dir())
        target = _w(base / "keep.json", "keep\n")
        _w(base / "dir/keep.json", "keep\n")
        root = _years(base)
        (root / "2024/work/margin_gains_wash.json").symlink_to(target)
        (root / "2024/work/margin_raw.json").symlink_to(base / "dir")
        r = _tjs(root / "2024", "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((root / "2024/work").exists())
        self.assertEqual(target.read_text(), "keep\n")
        self.assertEqual((base / "dir/keep.json").read_text(), "keep\n")

    def test_an_exports_folder_out_of_reach_is_left_alone(self):
        base = Path(private_dir())
        # A single-folder project naming a folder beside it: outside the
        # project's write boundary.
        root = base / "single"
        _w(root / "inputs/margin/trades.tt", _TT)
        _w(root / "taxjson.toml", _SETTINGS.format(
            year=2024, extra='exports_dir = "../shared"\n'))
        _w(root / "work/margin_gains.json")
        _w(base / "shared/margin_holdings.toml")
        _w(base / "shared/.taxjson-exports.json", json.dumps(
            {"schema_version": 1, "year": 2024, "files": ["margin_holdings.toml"]}))
        # A project of year folders whose exports folder is a link.
        years = _years(base / "y")
        (years / "elsewhere").mkdir()
        for n in os.listdir(years / "exports"):
            os.replace(years / "exports" / n, years / "elsewhere" / n)
        (years / "exports").rmdir()
        (years / "exports").symlink_to("elsewhere")
        for proj, shared, why in ((root, base / "shared", "outside the project"),
                                  (years / "2024", years / "elsewhere", "symlink")):
            with self.subTest(project=proj.name):
                before = _tree(shared)
                r = _tjs(proj, "clean")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("not cleaned", r.stderr)
                self.assertIn(why, r.stderr)
                self.assertFalse((proj / "work").exists())
                self.assertEqual(_tree(shared), before)


class TestThePlacesOfTheUsersFiles(unittest.TestCase):
    """A place the user's files live inside work/ or reports/ (or holding
    one): the project is refused, nothing removed in any year."""

    def test_a_setting_naming_work_or_reports_refuses(self):
        cases = (('holdings_dir = "reports"', "is", "reports/",
                  "reports/margin_live_holdings.toml"),
                 ('holdings_dir = "work"', "is", "work/", "work/margin.toml"),
                 ('holdings_dir = "work/h"', "is inside", "work/", "work/h/margin.toml"),
                 ('inputs_dir = "work/in"', "is inside", "work/",
                  "work/in/margin/trades.tt"))
        for line, how, folder, theirs in cases:
            with self.subTest(setting=line):
                root = _years(Path(private_dir()), years=(2024, 2025))
                cfg = root / "2025/taxjson.toml"
                text = cfg.read_text(encoding="utf-8")
                if 'inputs_dir = "../inputs"\n' in text and line.startswith("inputs_dir"):
                    text = text.replace('inputs_dir = "../inputs"\n', "")
                cfg.write_text(text.replace("[accounts.margin]",
                                            line + "\n[accounts.margin]"), encoding="utf-8")
                _w(root / "2025" / theirs)
                before = _tree(root)
                for where, prefix in ((root / "2025", ""), (root, "2025/")):
                    r = _tjs(where, "clean")
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
                    self.assertEqual(len(lines), 1, r.stderr)
                    self.assertIn(f"[settings] {line}", lines[0])
                    self.assertIn(f" {how} {prefix}{folder},", lines[0])
                    self.assertIn("nothing was removed", lines[0])
                    # From the folder holding the years: 2024 untouched too.
                    self.assertEqual(_tree(root), before)

    def test_an_accounts_holdings_file_or_a_linked_folder_refuses(self):
        root = _years(Path(private_dir()))
        y = root / "2024"
        cfg = y / "taxjson.toml"
        text = cfg.read_text(encoding="utf-8")
        cfg.write_text(text + 'holdings = ["reports/broker.toml"]\n', encoding="utf-8")
        _w(y / "reports/broker.toml")
        r = _tjs(y, "clean")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("[accounts.margin] holdings", r.stderr)
        self.assertTrue((y / "reports/broker.toml").is_file())
        cfg.write_text(text, encoding="utf-8")
        _w(y / "work/layouts/mine.toml")
        (y / "layouts").symlink_to("work/layouts")
        r = _tjs(y, "clean")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("layouts (a link to work/layouts) is inside work/", r.stderr)
        self.assertTrue((y / "work/layouts/mine.toml").is_file())
        self.assertTrue((y / "work/margin_gains.json").is_file())


    def test_a_holdings_setting_of_one_path_refuses(self):
        """`holdings = "work/x.toml"` (one path, as `tjs sanity` reads it:
        project/accounts `holdings_paths`) is a holdings file like a list's."""
        from taxjson.lib.holdings_dir import listed_files
        from taxjson.project.accounts import accounts_of
        for where in ("work/margin_live.toml", "reports/margin_live.toml"):
            with self.subTest(holdings=where):
                root = _years(Path(private_dir()))
                y = root / "2024"
                cfg = y / "taxjson.toml"
                cfg.write_text(cfg.read_text(encoding="utf-8")
                               + f'holdings = "{where}"\n', encoding="utf-8")
                _w(y / where)
                doc = {"accounts": {"margin": {"type": "taxable", "holdings": where}}}
                self.assertEqual(accounts_of(doc)["margin"].holdings_paths, [where])
                self.assertEqual(len(listed_files(y, doc["accounts"])), 1)
                before = _tree(root)
                for at in (y, root):
                    r = _tjs(at, "clean")
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
                    self.assertEqual(len(lines), 1, r.stderr)
                    self.assertIn(f'[accounts.margin] holdings = "{where}" in ', lines[0])
                    self.assertIn("nothing was removed", lines[0])
                    self.assertEqual(_tree(root), before)

    def test_another_years_place_in_a_years_folder(self):
        """2025's holdings file in 2024/reports/: from the folder holding
        the years, every year's places are checked against every year's
        folders; from 2024's folder, 2025's settings are not read."""
        root = _years(Path(private_dir()), years=(2024, 2025))
        cfg = root / "2025/taxjson.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8")
                       + 'holdings = ["../2024/reports/margin_holdings.toml"]\n',
                       encoding="utf-8")
        _w(root / "2024/reports/margin_holdings.toml")
        before = _tree(root)
        r = _tjs(root, "clean")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertIn('[accounts.margin] holdings = ["../2024/reports/margin_holdings.toml"]'
                      ' in 2025/taxjson.toml is inside 2024/reports/,', lines[0])
        self.assertEqual(_tree(root), before)
        r = _tjs(root / "2025", "clean", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _tjs(root / "2024", "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((root / "2024/reports").exists())


class TestTheOrder(unittest.TestCase):

    def test_the_phases_go_over_every_year_and_the_locks_last(self):
        """From the folder holding the years: every year's .sum files, then
        every year's record of what the run read, then the rest, then the
        books the views' banner looks for, then the records; each run lock
        after all of it."""
        root = _years(Path(private_dir()), years=(2024, 2025))
        for y in ("2024", "2025"):
            for n in (CL.FINGERPRINT, "margin_raw.json", "margin_base.json",
                      "margin_gains_wash.json"):
                _w(root / y / "work" / n)
            _w(root / y / "reports/margin_wash.sum")
            (root / y / "work" / ".run.lock").touch()
        roots = CL.projects(root)
        done = []

        def note(kind):
            return lambda p: done.append((kind, Path(p).relative_to(root).as_posix()))
        with CL.held(roots, dry_run=False) as made:
            pl = CL.plan(root, roots, made)
            with mock.patch.object(CL, "_remove", note("rm")), \
                    mock.patch.object(CL, "_rmdir", note("rmdir")), \
                    mock.patch.object(CL, "_unlock", note("unlock")):
                CL.remove(pl)

        def at(pred):
            return [i for i, (k, p) in enumerate(done) if pred(k, p)]
        sums = at(lambda k, p: k == "rm" and p.endswith(".sum"))
        first = at(lambda k, p: k == "rm" and p.endswith(CL.FINGERPRINT))
        books = at(lambda k, p: k == "rm" and p.endswith(("_base.json", "_gains.json",
                                                          "_gains_wash.json")))
        deps = at(lambda k, p: k == "rm" and p.endswith("/.deps"))
        rest = at(lambda k, p: k == "rm")
        rest = sorted(set(rest) - set(sums) - set(first) - set(books) - set(deps))
        locks = at(lambda k, p: k == "unlock")
        self.assertEqual(len(sums), 4)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(books), 6)
        self.assertEqual(len(deps), 2)
        self.assertTrue(rest)
        self.assertLess(max(sums), min(first))
        self.assertLess(max(first), min(rest))
        self.assertLess(max(rest), min(books))
        self.assertLess(max(books), min(deps))
        self.assertEqual([done[i][1] for i in locks],
                         ["2024/work/.run.lock", "2025/work/.run.lock"])
        self.assertLess(max(at(lambda k, p: k == "rm")), min(locks))
        self.assertTrue(all(k in ("unlock", "rmdir") for k, _p in done[min(locks):]))


class TestTheFolderHoldingTheYears(unittest.TestCase):

    def test_a_record_naming_no_year_and_the_stray_folders(self):
        root = _years(Path(private_dir()), years=(2024, 2025))
        rec = root / "exports/.taxjson-exports.json"
        doc = json.loads(rec.read_text(encoding="utf-8"))
        del doc["year"]
        rec.write_text(json.dumps(doc), encoding="utf-8")
        for n in ("export/old.csv", "work/x.json", "reports/x.sum"):
            _w(root / n)
        r = _tjs(root / "2025", "clean", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("None", r.stdout + r.stderr)
        self.assertIn("names no year", r.stderr)
        self.assertTrue(rec.is_file())
        r = _tjs(root, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("None", r.stdout + r.stderr)
        self.assertIn("its record names no year", r.stdout)
        self.assertFalse((root / "exports").exists())
        for n in ("export/", "work/", "reports/"):
            self.assertIn(n, r.stderr.split("no command writes them now")[-1])
        for n in ("export/old.csv", "work/x.json", "reports/x.sum"):
            self.assertTrue((root / n).is_file(), n)


# The views a stop part-way is checked with (beside `tjs sum`, a filing
# command): each refuses, or warns that the books are not the clean result.
_VIEWS = ("list", "gains", "carryover", "sanity", "shares", "divs", "wash-radar")
_NOT_CLEAN = "these books are not the clean result"


class TestAStopPartWay(unittest.TestCase):
    """The demo, run once (its books copied aside): a clean that cannot
    remove a folder, or is interrupted, leaves books the filing commands
    refuse and the views warn of (or, once the books are gone, refuse);
    then a real close-year, and the filed year's export goes."""

    def _views(self, y: Path, *, warn: bool) -> None:
        for cmd in _VIEWS:
            r = _tjs(y, cmd)
            if warn:
                self.assertIn(_NOT_CLEAN, r.stderr, cmd)
                self.assertIn("reports/ has no .sum", r.stderr, cmd)
            else:
                self.assertNotEqual(r.returncode, 0, cmd + r.stdout[-500:])
                self.assertRegex(r.stderr, "[Rr]un `tjs run` first", cmd)
            self.assertNotIn("Traceback", r.stderr, cmd)

    def test_a_stop_leaves_books_every_command_refuses(self):
        root = Path(private_dir()) / "demo"
        self.assertEqual(_tjs(None, "init", "--demo", str(root)).returncode, 0)
        y = root / "2024"
        r = _tjs(y, "run")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        aside = Path(private_dir()) / "aside"
        for n in ("2024/work", "2024/reports", "exports"):
            shutil.copytree(root / n, aside / n, symlinks=True)

        def restore():
            for n in ("2024/work", "2024/reports", "exports"):
                if (root / n).exists():
                    for d, _ds, _fs in os.walk(root / n):
                        os.chmod(d, 0o700)
                    shutil.rmtree(root / n)
                shutil.copytree(aside / n, root / n, symlinks=True)
            r = _tjs(y, "sum")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])

        # A folder it cannot remove: among the run's other entries (the
        # books the views' banner reads still there: they warn), and the
        # records, removed last (no book left: they refuse).
        for folder, warn in (("work/compare/prev", True),
                             ("work/.deps/reports", False)):
            with self.subTest(cannot_remove=folder):
                restore()
                _w(y / folder / "x.json")
                os.chmod(y / folder, stat.S_IRUSR | stat.S_IXUSR)
                try:
                    r = _tjs(y, "clean")
                finally:
                    os.chmod(y / folder, 0o700)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                # The folder, or an entry in it: which one the removal
                # stops at differs between Python releases (3.12.3 names the
                # entry, 3.12.16 and 3.13 the folder; CI runs the later ones).
                self.assertIn(folder, r.stderr)
                self.assertIn("could not be removed", r.stderr)
                self.assertIn("the views warn", r.stderr)
                self.assertFalse((y / "work" / CL.FINGERPRINT).exists())
                self.assertEqual(list((y / "reports").glob("*.sum")), [])
                self.assertTrue((y / "work/.run.lock").exists())
                self.assertEqual((y / "work/margin_gains.json").is_file(), warn)
                r = _tjs(y, "sum")      # (no books: 2; books no run recorded: 1)
                self.assertIn(r.returncode, (1, 2), r.stdout + r.stderr)
                self.assertIn("Run `tjs run` first", r.stderr)
                self._views(y, warn=warn)

        with self.subTest(interrupted=True):
            restore()
            calls = []
            real = CL._remove
            roots = CL.projects(y)
            with CL.held(roots, dry_run=False) as made:
                pl = CL.plan(y, roots, made)
                # A few entries into the rest, after the .sum files and the
                # run's record of what it read.
                at = sum(len(f.sums) + len(f.first) for f in pl.folders) + 3

                def stop(p):
                    calls.append(p)
                    if len(calls) == at:
                        raise KeyboardInterrupt
                    real(p)
                with mock.patch.object(CL, "_remove", stop):
                    with self.assertRaises(KeyboardInterrupt):
                        CL.remove(pl)
            self.assertTrue((y / "work/margin_gains.json").is_file())
            self.assertFalse((y / "work" / CL.FINGERPRINT).exists())
            r = _tjs(y, "sum")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("no full `tjs run` recorded", r.stderr)
            self._views(y, warn=True)

        # A real close-year (it comments out inputs_dir): the filed year's
        # export is still the project's, from its folder and from the root.
        restore()
        r = _tjs(y, "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertIn("# inputs_dir", (y / "taxjson.toml").read_text(encoding="utf-8"))
        self.assertTrue((root / "exports/.taxjson-exports.json").is_file())
        r = _tjs(y, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("not cleaned", r.stderr)
        self.assertFalse((root / "exports").exists())
        self.assertTrue((y / "filed/2024.json").is_file())
        r = _tjs(y, "run")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertTrue((root / "exports/.taxjson-exports.json").is_file())
        r = _tjs(root, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("not cleaned", r.stderr)
        self.assertFalse((root / "exports").exists())
        self.assertFalse((y / "work").exists())


class TestTheRunLock(unittest.TestCase):

    def test_a_held_run_lock_refuses_in_one_line(self):
        root = _years(Path(private_dir()), years=(2024, 2025))
        lock = root / "2025/work/.run.lock"
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            before = _tree(root)
            for where in (root / "2025", root):
                r = _tjs(where, "clean")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1, r.stderr)
                self.assertIn("in progress", lines[0])
                self.assertIn("nothing was removed", lines[0])
                self.assertEqual(_tree(root), before)
        finally:
            os.close(fd)
        r = _tjs(root, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((root / "2025/work").exists())


class TestAfterACleanTheBooksAreGone(unittest.TestCase):
    """A clean that keeps something in work/ (the slip-key salt): the
    commands that need the books answer as before any run."""

    def test_book_commands_refuse_and_no_old_verdict_shows(self):
        root = _years(Path(private_dir()))
        y = root / "2024"
        shutil.rmtree(y / "work")
        never = _tjs(y, "checklist", "--json")
        self.assertIn(never.returncode, (0, 1), never.stderr)
        refusals = {cmd: _tjs(y, *cmd.split()) for cmd in
                    ("sum", "ticker-map --suggest", "div-estimate", "slip-audit",
                     "slip-audit --template")}
        _w(y / "work/margin_gains.json", '{"transactions": []}\n')
        _w(y / "work/checklist_results.json", json.dumps({"synthetic": True}))
        _w(y / "work/.slip_key_salt", "synthetic\n")
        # A file of the user's, shaped like a book, left by the clean: no
        # book either.
        _w(y / "work/my_notes.json", '{"transactions": []}\n')
        r = _tjs(y, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((y / "work/.slip_key_salt").is_file())
        self.assertTrue((y / "work/my_notes.json").is_file())
        after = _tjs(y, "checklist", "--json")
        self.assertEqual(after.returncode, never.returncode, after.stderr)
        self.assertEqual(json.loads(after.stdout), json.loads(never.stdout))
        for cmd, want in refusals.items():
            with self.subTest(cmd=cmd):
                r = _tjs(y, *cmd.split())
                self.assertNotEqual(want.returncode, 0, cmd)
                # work/ is there (the salt): "no books in work/", where
                # before any run there was no work/ at all.
                said = (want.stderr.replace("no work/ in the project", "no books in work/")
                        .replace("no work/ —", "no books in work/ —"))
                self.assertEqual((r.returncode, r.stderr, r.stdout),
                                 (want.returncode, said, want.stdout))
        self.assertFalse(CL.holds_books(y / "work"))
        self.assertEqual(CL.no_books(y / "work"), "no books in work/")
        # A file of the user's is no book either.
        _w(y / "work/notes.txt")
        self.assertFalse(CL.holds_books(y / "work"))


if __name__ == "__main__":
    unittest.main()
