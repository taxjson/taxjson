"""`taxjson checklist`: every step from install to filing, checked
(lib/checklist.items). `taxjson quick-start` was merged into it.

- Coverage: every command an item names exists, every user-facing
  command is named by an item or listed in checklist.EXCLUDED with its
  reason, every `tjs ...` command line parses with the real parser, no
  two items share an id and no check is defined twice; every id the
  quick-start guide had maps into the checklist's id space.
- Outside a project: every step, numbered, with its commands; house
  style; exit 0; a mark needs a project.
- Inside a project (synthetic projects only): a fresh `init` (next: the
  broker files), the style projects' run with a sale that has no
  purchase (missing-history needs attention), a clean project (mostly
  done), checklist.json marks on any item, the --json schema, exit codes.
- Nothing is written to the project but checklist.json (by a mark).
"""
import contextlib
import hashlib
import io
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _style import PIPE_WIDTH, assert_styled, env, project  # noqa: E402

from taxjson.lib import checklist as cl  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Every step id `taxjson quick-start` had, and the checklist item it is
# now (one item where a quick-start step and a check were the same
# thing). The guide's own "walk the filing checklist" step is the
# command itself: its lines are the list's closing lines.
QUICK_START_IDS = {
    "install": "install", "init": "init", "configure": "configure",
    "inputs": "inputs-frozen", "run": "run-clean",
    "elections": "elections", "missing-history": "missing-history",
    "transfers": "transfers", "ticker-map": "ticker-map",
    "journals": "journals", "renames": "renames",
    "crypto-sends": "crypto-sends", "tt-lines": "tt-lines",
    "format": "format", "scan": "tips", "sanity": "sanity",
    "edge-cases": "edge-cases", "option-timing": "option-boundary",
    "wash-sales": "wash-reviewed", "check-dates": "check-dates",
    "sum": "sum", "explore": "explore", "slips": "t5008",
    "slip-audit": "t5-t3", "filing": "form-export",
    "estimate": "estimate", "trading": "trading", "redact": "redact",
    "close-year": "filed-lock", "next-year": "next-year",
    "handoff": "handoff",
}


def _cli(*args, cwd=None, **extra):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *args],
        cwd=cwd, capture_output=True, text=True, env=env(**extra),
        stdin=subprocess.DEVNULL, timeout=900)


def _grouped():
    from taxjson.bin.taxjson_run import _COMMAND_GROUPS
    return {n for _t, names in _COMMAND_GROUPS for n in names}


def _snapshot(root: Path):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (
                st.st_size, st.st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest())
    return out


# ---------------------------------------------------------------- coverage
class TestCoverage(unittest.TestCase):
    def test_every_command_an_item_names_exists(self):
        grouped = _grouped()
        named = cl.named_commands()
        missing = sorted(set(named) - grouped)
        self.assertEqual(missing, [], f"named by the checklist but not a "
                         f"command: {[(m, named[m]) for m in missing]}")

    def test_every_user_facing_command_is_named(self):
        """A new command must be named by an item in lib/checklist._spec
        or listed in checklist.EXCLUDED with the reason it is left out."""
        grouped = _grouped()
        named = set(cl.named_commands())
        uncovered = sorted(grouped - named - set(cl.EXCLUDED))
        self.assertEqual(uncovered, [], "named by no checklist item and "
                         "not in checklist.EXCLUDED")

    def test_exclusions_are_real_commands_with_a_reason(self):
        grouped = _grouped()
        self.assertLessEqual(len(cl.EXCLUDED), 5, "keep the list small")
        for name, why in cl.EXCLUDED.items():
            self.assertIn(name, grouped)
            self.assertTrue(why.strip(), name)
        self.assertEqual(sorted(set(cl.EXCLUDED) & set(cl.named_commands())),
                         [], "excluded, yet an item names it")

    def test_ids_unique_and_every_check_once(self):
        ids = cl.item_ids()
        self.assertEqual(len(ids), len(set(ids)), "two items share an id")
        steps = [s[0] for s in cl.STEPS]
        self.assertEqual(len(steps), len(set(steps)), "a check twice")
        self.assertEqual(set(steps), set(cl.DETECTORS))
        self.assertEqual(set(cl.STEP_RULES) & set(cl.DETECTORS), set())
        self.assertEqual(set(ids), set(cl.DETECTORS) | set(cl.STEP_RULES))
        for country in (None, "canada", "usa"):
            got = [it.id for it in cl.items(2025, country)]
            self.assertEqual(got, ids, country)
        for it in cl.items(2025):
            self.assertIn(it.section, cl.SECTIONS, it.id)
            self.assertTrue(it.title and it.why, it.id)
        # The sections in order: each one's items together.
        secs = [it.section for it in cl.items(2025)]
        self.assertEqual(sorted(secs, key=cl.SECTIONS.index), secs)

    def test_every_quick_start_step_is_one_item(self):
        ids = set(cl.item_ids())
        for old, new in QUICK_START_IDS.items():
            self.assertIn(new, ids, old)
        merged = list(QUICK_START_IDS.values())
        self.assertEqual(len(merged), len(set(merged)))

    def test_every_command_line_parses(self):
        """Each `tjs CMD ...` line is a real command with real flags
        (placeholders such as ACCOUNT or FILE are positionals)."""
        from taxjson.bin.taxjson_run import _build_parser
        p, sub = _build_parser()
        for it in cl.items(2025):
            for c in it.cmds:
                argv = shlex.split(c.command)
                if argv[0] != "tjs" or argv[1].startswith("-") \
                        or argv[1] not in sub.choices:
                    continue
                with self.subTest(item=it.id, command=c.command), \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    try:
                        p.parse_args(argv[1:])
                    except SystemExit:
                        self.fail(f"{c.command!r} does not parse: "
                                  f"{err.getvalue()}")

    def test_installer_flags_exist(self):
        text = (REPO / "install.sh").read_text(encoding="utf-8")
        for flag in ("--channel", "--without-fetch"):
            self.assertIn(flag, text)
        cmds = [c.command for it in cl.items(2025) for c in it.cmds]
        self.assertTrue(any(c.startswith(cl.INSTALL) and "--channel" in c
                            for c in cmds))
        self.assertTrue(any("--without-fetch" in c for c in cmds))

    def test_one_country_commands_are_marked(self):
        from taxjson.lib.country import COMMAND_COUNTRY
        for it in cl.items(2025):
            for c in it.cmds:
                argv = c.command.split()
                if argv[0] == "tjs" and argv[1] in COMMAND_COUNTRY:
                    self.assertEqual(c.country, COMMAND_COUNTRY[argv[1]],
                                     c.command)

    def test_quick_start_is_gone(self):
        from taxjson.bin.taxjson_run import _COMMAND_GROUPS, _build_parser
        self.assertIn("checklist", dict(_COMMAND_GROUPS)["Set up"])
        self.assertNotIn("quick-start", _grouped())
        _p, sub = _build_parser()
        self.assertNotIn("quick-start", sub.choices)
        with self.assertRaises(ImportError):
            import taxjson.lib.quick_start  # noqa: F401

    def test_next_is_the_first_open_item_then_one_to_confirm(self):
        rs = [cl.Result("install", "done"), cl.Result("fees", "manual"),
              cl.Result("inputs-frozen", "todo", "year still open",
                        actionable=False),
              cl.Result("t5008", "todo"), cl.Result("noa", "manual")]
        self.assertEqual(cl.next_result(rs).id, "t5008")
        rs[3].status = "done"
        nxt = cl.next_result(rs)
        self.assertEqual(nxt.id, "fees")
        doc = cl.to_json(rs, 2025, "canada")
        self.assertEqual(doc["next"]["do"],
                         "confirm it, then `tjs checklist --done fees`")

    def test_review_never_keeps_the_list_open(self):
        rs = [cl.Result("tips", "review"), cl.Result("init", "done"),
              cl.Result("t1135", "n/a"), cl.Result("fees", "manual")]
        self.assertFalse(cl.to_json(rs, 2025, "canada")["all_passed"])
        cl.apply_override(rs[3], {"status": "done"})
        doc = cl.to_json(rs, 2025, "canada")
        self.assertTrue(doc["all_passed"])
        self.assertIsNone(doc["next"])


# ---------------------------------------------------------------- outside
class TestOutsideAProject(unittest.TestCase):
    def test_every_step_numbered_with_its_commands(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("checklist", cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        out = r.stdout
        assert_styled(self, out, PIPE_WIDTH)
        lines = out.splitlines()
        self.assertEqual(lines[0], "CHECKLIST — from install to filing, "
                                   "step by step")
        for i in range(1, len(cl.item_ids()) + 1):
            self.assertRegex(out, rf"(?m)^ *{i}\. \S")
        for cmd in ("tjs init --country", "tjs run", "tjs tips",
                    "tjs find-missing-history --write-missing-history "
                    "--outside-year", "tjs reconcile-slips", "tjs redact",
                    "tjs close-year", "tjs handoff", "--without-fetch",
                    "tjs audit", "tjs fx-cash", "git status"):
            self.assertIn(cmd, out)
        # No marks outside a project; the last line says how to get them.
        self.assertNotIn("[x]", out)
        self.assertIn("checklist", lines[-1])
        # One-country commands say whose they are.
        self.assertIn("(Canada)", out)
        self.assertIn("(USA)", out)

    def test_json_outside(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("-C", d, "checklist", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema_version"], 2)
        self.assertFalse(doc["in_project"])
        for k in ("counts", "next", "all_passed", "year", "country"):
            self.assertIsNone(doc[k], k)
        self.assertEqual([s["id"] for s in doc["steps"]], cl.item_ids())
        self.assertTrue(all(s["status"] is None for s in doc["steps"]))

    def test_a_mark_needs_a_project(self):
        with tempfile.TemporaryDirectory() as d:
            for args in (("--done", "fees"), ("--walk",), ("--reset",)):
                with self.subTest(args=args):
                    r = _cli("-C", d, "checklist", *args)
                    self.assertEqual(r.returncode, 2)
                    self.assertIn("needs a project", r.stderr)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_missing_dir_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("-C", str(Path(d) / "nope"), "checklist")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such directory", r.stderr)


# ---------------------------------------------------------------- inside
class TestFreshProject(unittest.TestCase):
    def _init(self, d, tz):
        r = _cli("init", "--country", "canada", "--year", "2025",
                 str(Path(d) / "p"), TZ=tz)
        self.assertEqual(r.returncode, 0, r.stderr)
        return Path(d) / "p"

    def test_next_is_the_broker_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._init(d, "America/Toronto")
            r = _cli("-C", str(root), "checklist", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)     # not ready
            doc = json.loads(r.stdout)
            st = {s["id"]: s for s in doc["steps"]}
            for sid in ("install", "init", "configure"):
                self.assertEqual(st[sid]["effective"], "done", sid)
            self.assertEqual(doc["next"]["id"], "inputs-frozen")
            self.assertEqual(doc["next"]["step"], 4)
            self.assertTrue(st["inputs-frozen"]["next"])
            self.assertIn("no broker exports yet",
                          st["inputs-frozen"]["detail"])
            self.assertEqual(st["run-clean"]["effective"], "todo")
            self.assertEqual(st["run-clean"]["detail"], "no reports/ yet")
            r = _cli("-C", str(root), "checklist")
        self.assertEqual(r.returncode, 1, r.stderr)
        assert_styled(self, r.stdout, PIPE_WIDTH)
        self.assertRegex(r.stdout, r"(?m)^  \[>\]  4\. inputs-frozen +Each "
                                   r"account's broker exports")
        self.assertEqual(r.stdout.splitlines()[-1],
                         "Next (step 4, inputs-frozen): download the exports "
                         "into inputs/<account>/ (or `tjs fetch`)")

    def test_no_zone_with_a_crypto_account_stops_on_the_config(self):
        """A config every command refuses stops the checklist the same
        way (exit 1), naming what to set."""
        with tempfile.TemporaryDirectory() as d:
            root = self._init(d, "UTC")
            r = _cli("-C", str(root), "checklist", "--json", "--quick")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("local_timezone", r.stderr)

    def test_a_config_without_accounts_is_the_configure_step(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n')
            r = _cli("-C", str(root), "checklist", "--json", "--quick")
        self.assertEqual(r.returncode, 1, r.stderr)
        doc = json.loads(r.stdout)
        st = {s["id"]: s for s in doc["steps"]}
        self.assertEqual(st["configure"]["effective"], "attention")
        self.assertIn("no [accounts.NAME] section", st["configure"]["detail"])
        self.assertEqual(doc["next"]["id"], "configure")
        self.assertEqual(doc["next"]["do"], "edit taxjson.toml")

    def test_init_points_to_the_checklist(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("init", "--country", "usa", str(Path(d) / "p"))
        self.assertIn("checklist", r.stdout)
        self.assertNotIn("quick-start", r.stdout)


class TestAfterARun(unittest.TestCase):
    """The style projects: a run with a sale that has no purchase."""

    _cache = {}

    @classmethod
    def out(cls, country, *args):
        key = (country,) + args
        if key not in cls._cache:
            cls._cache[key] = project(country).run("checklist", *args)
        return cls._cache[key]

    def test_missing_history_needs_attention(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = self.out(country, "--json")
                self.assertEqual(r.returncode, 1, r.stderr)
                doc = json.loads(r.stdout)
                st = {s["id"]: s for s in doc["steps"]}
                self.assertEqual(st["run-clean"]["effective"], "done")
                self.assertEqual(st["missing-history"]["effective"],
                                 "attention")
                self.assertIn("missing basis",
                              st["missing-history"]["detail"])
                # The next item is the first open one, in order.
                first = next(s for s in doc["steps"]
                             if s["effective"] in ("attention", "todo",
                                                   "blocked"))
                self.assertEqual(doc["next"]["id"], first["id"])
                self.assertEqual([s["id"] for s in doc["steps"]
                                  if s["next"]], [first["id"]])

    def test_text_layout(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = self.out(country)
                self.assertEqual(r.returncode, 1, r.stderr)
                assert_styled(self, r.stdout, PIPE_WIDTH)
                out = r.stdout
                self.assertRegex(out, r"(?m)^  \[!\] 10\. missing-history +"
                                      r"No position with missing cost basis")
                self.assertRegex(out.splitlines()[-1],
                                 r"^Next \(step \d+, [a-z0-9-]+\): ")
                for sec in cl.SECTIONS:
                    self.assertIn(f". {sec.upper()}", out)
                # A done item is its state line; --all shows its commands.
                self.assertNotIn("tjs run --no-input", out)
                full = self.out(country, "--all")
                assert_styled(self, full.stdout, PIPE_WIDTH)
                self.assertIn("tjs run --no-input", full.stdout)
                doc = json.loads(self.out(country, "--json").stdout)
                self.assertEqual(full.stdout.count("Why: "),
                                 sum(1 for s in doc["steps"] if s["why"]))

    def test_country_commands(self):
        ca = json.loads(self.out("canada", "--json").stdout)
        us = json.loads(self.out("usa", "--json").stdout)

        def cmds(doc):
            return {c["command"] for s in doc["steps"]
                    for c in s["commands"]}
        self.assertIn("tjs t1135", cmds(ca))
        self.assertNotIn("tjs t1135", cmds(us))
        self.assertIn("tjs form-export --form txf --out gains.txf", cmds(us))
        self.assertNotIn("tjs form-export --form txf --out gains.txf",
                         cmds(ca))
        self.assertNotIn("tjs slip-audit", cmds(us))
        us_text = self.out("usa", "--all").stdout
        for word in ("T5008", "Schedule 3", "line 40500", "s.49"):
            self.assertNotIn(word, us_text)

    def test_json_schema(self):
        doc = json.loads(self.out("canada", "--json").stdout)
        self.assertEqual(set(doc), {"schema_version", "in_project", "year",
                                    "country", "all_passed", "counts",
                                    "next", "steps"})
        self.assertEqual(doc["schema_version"], cl.SCHEMA_VERSION)
        self.assertTrue(doc["in_project"])
        self.assertFalse(doc["all_passed"])
        self.assertEqual((doc["year"], doc["country"]), (2024, "canada"))
        self.assertEqual(set(doc["counts"]), set(cl.STATUSES))
        self.assertEqual(sum(doc["counts"].values()), len(doc["steps"]))
        self.assertEqual(set(doc["next"]), {"step", "id", "title", "do"})
        ids = [s["id"] for s in doc["steps"]]
        self.assertEqual(ids, cl.item_ids())
        self.assertEqual([s["step"] for s in doc["steps"]],
                         list(range(1, len(ids) + 1)))
        for s in doc["steps"]:
            self.assertEqual(set(s), {
                "step", "id", "section", "title", "check", "command",
                "commands", "how", "why", "status", "effective", "detail",
                "override", "note", "finding", "next"})
            self.assertIn(s["effective"], cl.STATUSES)
            self.assertEqual(s["check"], s["id"] in cl.DETECTORS)
            for c in s["commands"]:
                self.assertEqual(set(c), {"command", "note", "country"})

    def test_writes_nothing(self):
        """The checks run commands, but nothing in the project changes."""
        p = project("canada")
        before = _snapshot(p.root)
        self.out("canada")
        self.out("canada", "--json")
        self.assertEqual(_snapshot(p.root), before)


class TestFormerIds(unittest.TestCase):
    """`scan` became `tips` (the command was renamed): a checklist.json
    saved under the old id still marks the item, and the CLI accepts
    the old id."""

    def test_saved_scan_mark_applies_to_tips(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            shutil.copytree(project("canada").root, root)
            year = cl.load_state(root).get("year")
            (root / cl.STATE_FILE).write_text(json.dumps({
                "overrides": {"scan": {"status": "skipped", "note": "n",
                                       "date": "2026-01-02"}},
                **({"year": year} if year else {})}))
            st = cl.load_state(root)
            self.assertEqual(st["overrides"]["tips"]["status"], "skipped")
            self.assertNotIn("scan", st["overrides"])
            r = _cli("-C", str(root), "checklist", "--only", "scan",
                     "--json")
            doc = {s["id"]: s for s in json.loads(r.stdout)["steps"]}
            self.assertEqual(list(doc), ["tips"])
            self.assertEqual(doc["tips"]["effective"], "skipped")
            r = _cli("-C", str(root), "checklist", "--undo", "scan")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("tips", json.loads(
                (root / cl.STATE_FILE).read_text())["overrides"])

    def test_tips_is_a_review_item_that_never_keeps_the_list_open(self):
        item = {it.id: it for it in cl.items(2025, "canada")}["tips"]
        self.assertEqual(item.cmds[0].command, "tjs tips")
        self.assertNotIn("scan", cl.item_ids())
        self.assertEqual(cl.STEP_RULES["tips"](None, None).status,
                         "review")
        self.assertIn("review", cl.PASSED)


class TestMarks(unittest.TestCase):
    """checklist.json marks on any item: the only file a mark writes."""

    def test_marks_on_steps_and_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            shutil.copytree(project("canada").root, root)
            before = _snapshot(root)
            # `scan` is the tips item's former id: still accepted.
            r = _cli("-C", str(root), "checklist", "--done", "scan",
                     "--skip", "transfers", "--done", "fees",
                     "--note", "read it")
            self.assertEqual(r.returncode, 0, r.stderr)
            after = _snapshot(root)
            self.assertEqual(sorted(set(after) - set(before)),
                             [cl.STATE_FILE])
            self.assertEqual({k: v for k, v in after.items()
                              if k != cl.STATE_FILE}, before)
            st = json.loads((root / cl.STATE_FILE).read_text())
            self.assertEqual(st["overrides"]["tips"]["status"], "done")
            self.assertNotIn("scan", st["overrides"])
            self.assertEqual(st["overrides"]["transfers"]["status"],
                             "skipped")
            r = _cli("-C", str(root), "checklist", "--quick", "--json")
            doc = {s["id"]: s for s in json.loads(r.stdout)["steps"]}
            self.assertEqual(doc["tips"]["effective"], "done")
            # A skip accepts the finding; it stays beside the mark.
            self.assertEqual(doc["transfers"]["effective"], "skipped")
            self.assertIn("transfer-in", doc["transfers"]["finding"])
            self.assertEqual(doc["fees"]["effective"], "done")
            self.assertEqual(doc["fees"]["note"], "read it")
            # A DONE mark never hides a finding.
            r = _cli("-C", str(root), "checklist", "--done", "transfers")
            r = _cli("-C", str(root), "checklist", "--quick", "--json")
            doc = {s["id"]: s for s in json.loads(r.stdout)["steps"]}
            self.assertEqual(doc["transfers"]["effective"], "attention")


class TestCleanProject(unittest.TestCase):
    """A small clean project: one taxable account, a buy and its sale, a
    purchase after January, committed to git."""

    def test_mostly_done(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "clean"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\nprovince = "ON"\n'
                'option_grant_timing_since = 2024\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "book.tt").write_text(
                "BUYSELL 2024-01-10 10:00:00 ZZA.TO 100 CAD 10 1000\n"
                "BUYSELL 2024-06-03 10:00:00 ZZA.TO -100 CAD 12 1200\n"
                "BUYSELL 2025-02-03 10:00:00 ZZB.TO 10 CAD 10 100\n")
            r = _cli("-C", str(root), "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            # This machine's own hooks and signing never apply.
            git = ["git", "-C", str(root), "-c", "user.name=Sam",
                   "-c", "user.email=sam@example.com",
                   "-c", "core.hooksPath=/dev/null",
                   "-c", "commit.gpgsign=false"]
            for a in (["init", "-q"], ["add", "-A"],
                      ["commit", "-qm", "inputs"]):
                subprocess.run(git + a, check=True, capture_output=True)
            r = _cli("-C", str(root), "checklist", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            doc = json.loads(r.stdout)
            text = _cli("-C", str(root), "checklist").stdout
        st = {s["id"]: s["effective"] for s in doc["steps"]}
        for sid in ("install", "init", "configure", "inputs-frozen",
                    "export-coverage", "run-clean", "elections",
                    "missing-history", "transfers", "ticker-map",
                    "journals", "renames", "inputs-committed",
                    "option-boundary", "wash-reviewed", "check-dates",
                    "audit", "form-export"):
            self.assertEqual(st[sid], "done", sid)
        for sid in ("crypto-sends", "crypto-inputs", "sheltered-inputs",
                    "filing-positions"):
            self.assertEqual(st[sid], "n/a", sid)
        self.assertEqual(doc["counts"]["attention"], 0, st)
        # Nothing to fix: the next item is the slips, still to come.
        self.assertEqual(doc["next"]["id"], "t5008")
        self.assertEqual(doc["next"]["do"],
                         "`tjs reconcile-slips inputs/slips/*.csv`")
        self.assertIn("no slip file", {s["id"]: s for s in doc["steps"]}
                      ["t5008"]["detail"])
        assert_styled(self, text, PIPE_WIDTH)
        self.assertEqual(text.splitlines()[-1],
                         f"Next (step {doc['next']['step']}, "
                         f"{doc['next']['id']}): {doc['next']['do']}")


if __name__ == "__main__":
    unittest.main()
