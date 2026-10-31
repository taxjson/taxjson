"""Backlog fix round 2026-10-27, group `build`: the project build and
infrastructure commands (migrate, find-missing-history, opening, align,
fetch --list, promote's documentation) — the tjs command audit's
findings.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account 99900001.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from tax_rules import rule

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


def _toml(year, country="canada", extra=""):
    cur = "CAD" if country == "canada" else "USD"
    return (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n{extra}'
            f'[accounts.margin]\ntype = "taxable"\n')


def _snapshot(root):
    """{relative path: sha256} of every file under `root`."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes())
            .hexdigest() for p in Path(root).rglob("*") if p.is_file()}


class TestMigratePlansEveryPhaseFirst(unittest.TestCase):
    """TJ-0300: `tjs migrate` in a year folder made the year folders'
    tobase.map copies one shared file (every year's taxjson.toml edited,
    the copies moved to .bak) before it read the selected project's old
    files — then stopped at a malformed one saying "Nothing was
    changed." Every phase is now planned before the first writes."""

    def _years(self, d):
        top = Path(d)
        for year in (2024, 2025):
            y = top / str(year)
            y.mkdir()
            (y / "taxjson.toml").write_text(
                _toml(year, extra='inputs_dir = "../inputs"\n'))
            (y / "ticker.map").write_text("")
            (y / "tobase.map").write_text("TOBASE ZZQ.US ZZQ.TO\n")
        (top / "inputs" / "margin").mkdir(parents=True)
        return top

    def test_a_malformed_old_file_stops_it_before_any_write(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d)
            (top / "2025" / "amt_carryover.txt").write_text("not a number\n")
            before = _snapshot(top)
            r = _tjs(top / "2025", "migrate")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("amt_carryover.txt:1", r.stderr)
            self.assertIn("Nothing was changed.", r.stderr)
            # True to its word: no file touched, none added.
            self.assertEqual(_snapshot(top), before)
            self.assertNotIn("removed", r.stdout)

    def test_a_missing_history_it_cannot_size_stops_it_too(self):
        # missing_history.json sized from books not built yet: refused in
        # the plan, before the shared tobase.map is written.
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d)
            (top / "2025" / "missing_history.json").write_text(json.dumps(
                [{"symbol": "ZZQ.TO", "account": "margin", "quantity": 5}]))
            before = _snapshot(top)
            r = _tjs(top / "2025", "migrate")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("Nothing was changed.", r.stderr)
            self.assertEqual(_snapshot(top), before)

    def test_both_phases_written_keep_each_others_settings(self):
        # The old file's plan is made again after the shared tobase.map
        # set its key: the migrated taxjson.toml keeps both.
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d)
            (top / "2025" / "amt_carryover.txt").write_text("2023 100.50\n")
            r = _tjs(top / "2025", "migrate")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            text = (top / "2025" / "taxjson.toml").read_text()
            self.assertIn('tobase_map = "../tobase.map"', text)
            self.assertIn("amt_carryover = { 2023 = 100.5 }", text)
            self.assertTrue((top / "tobase.map").is_file())
            self.assertTrue((top / "2025" / "amt_carryover.txt.migrated")
                            .is_file())

    def test_a_later_refusal_names_what_was_done(self):
        from taxjson.bin import taxjson_run as R
        self.assertEqual(R._migrate_done_note([]), [])
        note = R._migrate_done_note(["the shared tobase.map (above)"])
        self.assertEqual(len(note), 1)
        self.assertIn("the shared tobase.map (above)", note[0])
        self.assertNotIn("Nothing was changed", note[0])


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type\n")


class TestWriteMissingHistoryDateBasis(unittest.TestCase):
    """TJ-0316: `find-missing-history --write-missing-history` ran
    taxjson-gains without the project's --tax-date, so its year filter
    used the country default: the sale the report lists under AFFECTS
    was filtered out as another year's, and nothing was written. Both
    countries, the non-default basis, a sale traded on Dec 31 that
    settles in January; the default basis is the control."""

    def _project(self, d, country, basis, trade, settle):
        root = Path(d)
        cur = "CAD" if country == "canada" else "USD"
        extra = f'tax_date = "{basis}"\n' if basis else ""
        (root / "taxjson.toml").write_text(_toml(2025, country, extra))
        ins = root / "inputs" / "margin"
        ins.mkdir(parents=True)
        (ins / "questrade.csv").write_text(
            _QT_HEAD + f"{trade},{settle},Sell,ZZQ,ZZQ CORP,-1,100,100,0,"
            f"100,{cur},Trades,99900001,Margin\n")
        r = _tjs(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root

    def _written(self, root):
        r = _tjs(root, "find-missing-history", "--write-missing-history")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        f = root / "inputs" / "margin" / "missing_history.tt"
        lines = ([ln for ln in f.read_text().splitlines()
                  if ln.startswith("OPENING")] if f.is_file() else [])
        return lines, r

    def _report_affects(self, root):
        r = _tjs(root, "find-missing-history")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return "AFFECTS 2025" in r.stdout

    @rule("CA-DATE-02")
    def test_canada_trade_basis(self):
        # Traded 2025-12-31, settled 2026-01-02: 2025's on the trade date.
        with tempfile.TemporaryDirectory() as d:
            root = self._project(d, "canada", "trade",
                                 "2025-12-31", "2026-01-02")
            self.assertTrue(self._report_affects(root))
            lines, _r = self._written(root)
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(lines[0].startswith(
                "OPENING 2025-12-30 ZZQ.TO 1 cost=unknown"), lines[0])

    @rule("CA-DATE-01")
    def test_canada_default_settle_basis_control(self):
        # The same sale on the settlement date is 2026's: not written.
        with tempfile.TemporaryDirectory() as d:
            root = self._project(d, "canada", None,
                                 "2025-12-31", "2026-01-02")
            self.assertFalse(self._report_affects(root))
            lines, r = self._written(root)
            self.assertEqual(lines, [])
            self.assertIn("outside tax year 2025", r.stderr)

    @rule("US-DATE-02")
    def test_usa_settle_basis(self):
        # Traded 2024-12-31, settled 2025-01-02: 2025's on settlement.
        with tempfile.TemporaryDirectory() as d:
            root = self._project(d, "usa", "settle",
                                 "2024-12-31", "2025-01-02")
            self.assertTrue(self._report_affects(root))
            lines, _r = self._written(root)
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(lines[0].startswith(
                "OPENING 2024-12-30 ZZQ.US 1 cost=unknown"), lines[0])

    @rule("US-DATE-01")
    def test_usa_default_trade_basis_control(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._project(d, "usa", None,
                                 "2024-12-31", "2025-01-02")
            self.assertFalse(self._report_affects(root))
            lines, r = self._written(root)
            self.assertEqual(lines, [])
            self.assertIn("outside tax year 2025", r.stderr)


class TestOpeningDate(unittest.TestCase):
    """TJ-0317: `tjs opening margin FILE --date 2025-1-1` wrote
    opening_2025-1-1.tt, which the next run refused ("snapshot date
    '2025-1-1' is not a valid YYYY-MM-DD date"). The date is checked
    with the .tt reader's rule, and every line is read back as the run
    reads it, before anything is written."""

    def _project(self, d):
        root = Path(d)
        (root / "taxjson.toml").write_text(_toml(2025))
        (root / "inputs" / "margin").mkdir(parents=True)
        pos = root / "positions.toml"
        pos.write_text('[meta]\nas_of = "2025-01-01"\n[[holding]]\n'
                       'symbol = "ZZQ.TO"\nquantity = 1\ncurrency = "CAD"\n'
                       'total_cost = 100\n')
        return root, pos

    def _openings(self, root):
        return sorted(p.name for p in (root / "inputs" / "margin").iterdir()
                      if p.name.startswith("opening_"))

    def test_an_unpadded_date_is_refused_naming_the_padded_one(self):
        with tempfile.TemporaryDirectory() as d:
            root, pos = self._project(d)
            r = _tjs(root, "opening", "margin", str(pos), "--date",
                     "2025-1-1")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            flat = " ".join(r.stderr.split())
            self.assertIn("--date '2025-1-1' is not a YYYY-MM-DD date",
                          flat)
            self.assertIn("Write it 2025-01-01 (--date).", flat)
            self.assertEqual(self._openings(root), [])

    def test_a_day_that_does_not_exist_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root, pos = self._project(d)
            for bad in ("2025-02-29", "2025-13-01", "2025-2-30"):
                with self.subTest(date=bad):
                    r = _tjs(root, "opening", "margin", str(pos), "--date",
                             bad)
                    self.assertEqual(r.returncode, 1, r.stderr)
                    self.assertIn("is not a YYYY-MM-DD date", r.stderr)
                    self.assertIn("Give the statement's day as YYYY-MM-DD",
                                  " ".join(r.stderr.split()))
            self.assertEqual(self._openings(root), [])

    def test_a_leap_day_is_written_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            root, pos = self._project(d)
            r = _tjs(root, "opening", "margin", str(pos), "--date",
                     "2024-02-29")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self._openings(root),
                             ["opening_2024-02-29.tt"])
            r = _tjs(root, "check-tt", "margin")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_padded_day(self):
        from taxjson.bin.taxjson_run import _opening_padded_day
        self.assertEqual(_opening_padded_day("2025-1-1"), "2025-01-01")
        self.assertEqual(_opening_padded_day("2024-2-29"), "2024-02-29")
        self.assertIsNone(_opening_padded_day("2025-2-29"))
        self.assertIsNone(_opening_padded_day("1/1/2025"))

    def test_a_line_that_would_not_read_back_is_never_written(self):
        import argparse
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        from unittest import mock
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as d:
            root, pos = self._project(d)
            args = argparse.Namespace(dir=str(root), account="margin",
                                      file=str(pos), date=None,
                                      dry_run=False, force=False)
            bad = ["OPENING 2025-01-01 ZZQ.TO 1 CAD 100.00 2025-13-01"]
            with mock.patch.object(R, "_opening_lines",
                                   return_value=(bad, [], [])), \
                    redirect_stderr(StringIO()), \
                    redirect_stdout(StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                R.cmd_opening(args)
            text = " ".join(str(cm.exception.code).split())
            self.assertIn("an OPENING line would not read back", text)
            self.assertIn("opening_2025-01-01.tt:7", text)
            self.assertEqual(self._openings(root), [])


class TestAlignWriteJson(unittest.TestCase):
    """TJ-0318: `tjs align --from 2025 --write --all --json` changed
    ticker.map and printed prose: json.loads failed. It now prints one
    JSON result after the writes — what was added, set and skipped, the
    files and their backups, and the warnings."""

    def _years(self, d, map24="", map25="", toml25_extra="",
               accounts25=("margin", "tfsa")):
        top = Path(d)
        for year, tmap in ((2024, map24), (2025, map25)):
            y = top / str(year)
            y.mkdir()
            names = ("margin", "tfsa") if year == 2024 else accounts25
            accts = "".join(
                f'[accounts.{n}]\ntype = "{"tfsa" if n == "tfsa" else "taxable"}"\n'
                for n in names)
            extra = 'inputs_dir = "../inputs"\n'
            body = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
                    f'base_currency = "CAD"\nsource_currencies = []\n'
                    f'{extra}{toml25_extra if year == 2025 else ""}{accts}')
            (y / "taxjson.toml").write_text(body)
            (y / "ticker.map").write_text(tmap)
        for n in ("margin", "tfsa"):
            (top / "inputs" / n).mkdir(parents=True)
        return top

    def test_write_all_json_is_one_json_result(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(
                d, map24="GLOBAL ZZX.US ZZY.US\n",
                map25="GLOBAL ZZOLD.US ZZQ.US\nGLOBAL ZZX.US ZZZ.US\n",
                accounts25=("tfsa", "margin"))
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--all", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual((doc["here"], doc["there"]), ("2024", "2025"))
            self.assertTrue(doc["written"])
            self.assertEqual(doc["map_lines_added"],
                             ["GLOBAL ZZOLD.US ZZQ.US"])
            self.assertEqual([s["line"] for s in doc["map_lines_skipped"]],
                             ["GLOBAL ZZX.US ZZZ.US"])
            self.assertTrue(doc["map_lines_skipped"][0]["problems"])
            self.assertEqual([f["file"] for f in doc["files"]],
                             ["ticker.map"])
            self.assertEqual(doc["files"][0]["backup"], "ticker.map.bak")
            self.assertIn("accounts (order)",
                          [k["key"] for k in doc["keys_not_changed"]])
            # The accounts' order is a note, not a warning (backlog
            # TJ-0922): it changes no figure.
            self.assertEqual(len(doc["warnings"]), 1, doc["warnings"])
            # The file was written as said; the warnings are on stderr.
            self.assertIn("GLOBAL ZZOLD.US ZZQ.US",
                          (top / "2024" / "ticker.map").read_text())
            self.assertNotIn("ZZZ.US",
                             (top / "2024" / "ticker.map").read_text())
            self.assertIn("not brought over", r.stderr)
            self.assertNotIn("Run `tjs run`", r.stdout)

    def test_a_key_set_is_listed(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d, toml25_extra="local_timezone = "
                              "\"America/Toronto\"\n")
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--all", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["keys_set"], [
                {"key": "settings.local_timezone",
                 "path": ["settings", "local_timezone"],
                 "value": "America/Toronto"}])
            self.assertEqual([f["file"] for f in doc["files"]],
                             ["taxjson.toml"])
            self.assertIn('local_timezone = "America/Toronto"',
                          (top / "2024" / "taxjson.toml").read_text())

    def test_nothing_to_bring_over(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d)
            before = _snapshot(top)
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--all", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertFalse(doc["written"])
            self.assertEqual(doc["files"], [])
            self.assertEqual(_snapshot(top), before)

    def test_write_json_without_all_is_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d, map25="GLOBAL ZZOLD.US ZZQ.US\n")
            before = _snapshot(top)
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--json")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("--write --json needs --all", r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertEqual(_snapshot(top), before)

    def test_text_output_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._years(d, map25="GLOBAL ZZOLD.US ZZQ.US\n")
            r = _tjs(top / "2024", "align", "--from", "2025", "--write",
                     "--all")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ticker.map: 1 line(s) added (the previous file "
                          "kept as ticker.map.bak)", r.stdout)
            self.assertIn("Run `tjs run` to rebuild the books with "
                          "them.", r.stdout)


class TestFetchListBesideALegacyFile(unittest.TestCase):
    """TJ-0217 (TJ-0326): `fetch --list` reads no project, yet a project
    with an old per-purpose file (yf_ticker.map) refused it, asking for
    `tjs migrate`; the other project guards already let it run.
    `fetch` itself, which reads the project, is still refused."""

    def _legacy(self, d):
        root = Path(d)
        (root / "taxjson.toml").write_text(_toml(2025))
        (root / "yf_ticker.map").write_text("")
        return root

    def test_list_runs(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._legacy(d)
            r = _tjs(root, "fetch", "--list", "--json")
            self.assertNotIn("yf_ticker.map", r.stderr)
            self.assertNotIn("tjs migrate", r.stderr)
            if r.returncode == 2:
                # No fetcher plugin installed here: the one install hint.
                self.assertIn("no fetcher is installed", r.stderr)
            else:
                self.assertEqual(r.returncode, 0, r.stderr)
                json.loads(r.stdout)

    def test_check_tt_of_a_named_file_runs(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._legacy(d)
            tt = root / "a.tt"
            tt.write_text("BUYSELL 2025-03-03 10:00:00 ZZQ.TO 10 CAD 10.00 "
                          "100.00 0\n")
            r = _tjs(root, "check-tt", str(tt))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("tjs migrate", r.stderr)

    def test_fetch_itself_is_still_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._legacy(d)
            r = _tjs(root, "fetch", "--dry-run")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            if "no fetcher is installed" in r.stderr:
                # CI's suite runs without taxjson-fetch: nothing to refuse.
                self.skipTest("no fetcher plugin installed here")
            self.assertIn("this project still has yf_ticker.map", r.stderr)


class TestPromoteDocsNameTheChannelsTag(unittest.TestCase):
    """TJ-0337: docs/commands.md said promote makes "no tag", and
    cmd_promote "never tags", while scripts/promote.sh's fast lane tags
    and pushes `channels-<timestamp>` (the publisher's trigger)."""

    def test_the_docs_say_what_promote_tags(self):
        from taxjson.bin import taxjson_run as R
        doc = (REPO / "docs" / "commands.md").read_text(encoding="utf-8")
        sect = doc.split("#### tjs promote", 1)[1].split("\n#### ", 1)[0]
        flat = " ".join(sect.split()).lower()
        self.assertNotIn("no tag,", flat)
        self.assertIn("no new release tag", flat)
        self.assertIn("channels-", flat)
        flat = " ".join((R.cmd_promote.__doc__ or "").split()).lower()
        self.assertNotIn("never tags", flat)
        self.assertIn("no new release tag", flat)
        self.assertIn("channels-", flat)
        sh = (REPO / "scripts" / "promote.sh").read_text(encoding="utf-8")
        self.assertIn("channels-", sh)


if __name__ == "__main__":
    unittest.main()
