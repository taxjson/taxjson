"""`taxjson quick-start`: the workflow step by step (lib/quick_start).

- Coverage: every command a step names exists, every user-facing command
  is named by a step or listed in quick_start.EXCLUDED with its reason, and
  every `tjs ...` command line of the guide parses with the real parser.
- Outside a project: every step, numbered, with its commands; house style.
- Inside a project (synthetic projects only): a fresh `init` (next: the
  broker files), the style project's run with a sale that has no purchase
  (attention on find-missing-history, the next step), a clean project
  (mostly done, next: the checklist), a legacy file, the --json schema.
- Read-only: no file of the project changes, and no subprocess or socket
  is opened while the guide is evaluated.
"""
import contextlib
import hashlib
import io
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _style import PIPE_WIDTH, assert_styled, env, project  # noqa: E402

from taxjson.lib import quick_start as QS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


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
    def test_every_command_the_guide_names_exists(self):
        grouped = _grouped()
        named = QS.named_commands()
        missing = sorted(set(named) - grouped)
        self.assertEqual(missing, [], f"named by the guide but not a "
                         f"command: {[(m, named[m]) for m in missing]}")

    def test_every_user_facing_command_is_in_a_step(self):
        """A new command must be added to a step in lib/quick_start.steps
        or to quick_start.EXCLUDED with the reason it is left out."""
        grouped = _grouped()
        named = set(QS.named_commands())
        uncovered = sorted(grouped - named - set(QS.EXCLUDED))
        self.assertEqual(uncovered, [], "in no quick-start step and not in "
                         "quick_start.EXCLUDED")

    def test_exclusions_are_real_commands_with_a_reason(self):
        grouped = _grouped()
        self.assertLessEqual(len(QS.EXCLUDED), 5, "keep the list small")
        for name, why in QS.EXCLUDED.items():
            self.assertIn(name, grouped)
            self.assertTrue(why.strip(), name)
        self.assertEqual(sorted(set(QS.EXCLUDED) & set(QS.named_commands())),
                         [], "excluded, yet a step names it")

    def test_every_command_line_parses(self):
        """Each `tjs CMD ...` line is a real command with real flags
        (placeholders such as ACCOUNT or FILE are positionals)."""
        from taxjson.bin.taxjson_run import _build_parser
        p, sub = _build_parser()
        for st in QS.steps(2025):
            for c in st.cmds:
                argv = shlex.split(c.command)
                if argv[0] != "tjs" or argv[1].startswith("-") \
                        or argv[1] not in sub.choices:
                    continue
                with self.subTest(step=st.id, command=c.command), \
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
        cmds = [c.command for st in QS.steps(2025) for c in st.cmds]
        self.assertTrue(any(c.startswith(QS.INSTALL) and "--channel" in c
                            for c in cmds))
        self.assertTrue(any("--without-fetch" in c for c in cmds))

    def test_one_country_commands_are_marked(self):
        from taxjson.lib.country import COMMAND_COUNTRY
        for st in QS.steps(2025):
            for c in st.cmds:
                argv = c.command.split()
                if argv[0] == "tjs" and argv[1] in COMMAND_COUNTRY:
                    self.assertEqual(c.country, COMMAND_COUNTRY[argv[1]],
                                     c.command)

    def test_quick_start_is_in_the_set_up_group(self):
        from taxjson.bin.taxjson_run import _COMMAND_GROUPS
        self.assertIn("quick-start", dict(_COMMAND_GROUPS)["Set up"])


# ---------------------------------------------------------------- outside
class TestOutsideAProject(unittest.TestCase):
    def test_every_step_numbered_with_its_commands(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("quick-start", cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        out = r.stdout
        assert_styled(self, out, PIPE_WIDTH)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("QUICK START — "))
        n = len(QS.steps(2025))
        for i in range(1, n + 1):
            self.assertRegex(out, rf"(?m)^ *{i}\. \S")
        for cmd in ("tjs init --country", "tjs run", "tjs scan",
                    "tjs find-missing-history --write-missing-history "
                    "--outside-year", "tjs reconcile-slips", "tjs redact",
                    "tjs close-year", "tjs handoff", "--without-fetch"):
            self.assertIn(cmd, out)
        # No marks outside a project; the last line says how to get them.
        self.assertNotIn("[x]", out)
        self.assertIn("quick-start", lines[-1])
        # One-country commands say whose they are.
        self.assertIn("(Canada)", out)
        self.assertIn("(USA)", out)

    def test_json_outside(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("-C", d, "quick-start", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertFalse(doc["in_project"])
        self.assertIsNone(doc["counts"])
        self.assertIsNone(doc["next"])
        self.assertTrue(all(s["status"] is None for s in doc["steps"]))

    def test_missing_dir_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("-C", str(Path(d) / "nope"), "quick-start")
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
            # init's closing lines point here.
            r = _cli("-C", str(root), "quick-start", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            st = {s["id"]: s for s in doc["steps"]}
            self.assertEqual(st["init"]["status"], "done")
            self.assertEqual(st["configure"]["status"], "done")
            self.assertEqual(doc["next"]["id"], "inputs")
            self.assertTrue(st["inputs"]["next"])
            self.assertEqual(st["run"]["status"], "todo")
            self.assertEqual(st["missing-history"]["detail"],
                             "after `tjs run`")
            r = _cli("-C", str(root), "quick-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_styled(self, r.stdout, PIPE_WIDTH)
        self.assertRegex(r.stdout, r"(?m)^  \[>\]  4\. Put each account's "
                                   r"broker exports")
        self.assertEqual(r.stdout.splitlines()[-1],
                         "Next (step 4): download the exports into "
                         "inputs/<account>/ (or `tjs fetch`)")

    def test_no_zone_with_a_crypto_account_needs_attention(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._init(d, "UTC")
            r = _cli("-C", str(root), "quick-start", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        st = {s["id"]: s for s in doc["steps"]}
        self.assertEqual(st["configure"]["status"], "attention")
        self.assertIn("local_timezone", st["configure"]["detail"])
        self.assertEqual(doc["next"]["id"], "configure")

    def test_init_points_to_quick_start(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli("init", "--country", "usa", str(Path(d) / "p"))
        self.assertIn("quick-start", r.stdout)

    def test_a_legacy_file_is_the_configure_step(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._init(d, "America/Toronto")
            (root / "yf_ticker.map").write_text("# old\n")
            r = _cli("-C", str(root), "quick-start", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        st = {s["id"]: s for s in doc["steps"]}
        self.assertEqual(st["configure"]["status"], "attention")
        self.assertIn("taxjson migrate", st["configure"]["detail"])


class TestAfterARun(unittest.TestCase):
    """The style projects: a run with a sale that has no purchase."""

    def test_missing_history_needs_attention_and_is_next(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = project(country)
                r = p.run("quick-start", "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                doc = json.loads(r.stdout)
                st = {s["id"]: s for s in doc["steps"]}
                self.assertEqual(st["run"]["status"], "done")
                self.assertEqual(st["missing-history"]["status"], "attention")
                self.assertIn("no purchase", st["missing-history"]["detail"])
                self.assertEqual(doc["next"]["id"], "missing-history")
                self.assertEqual(doc["next"]["do"],
                                 "`tjs find-missing-history`")

    def test_text_layout(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = project(country).run("quick-start")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_styled(self, r.stdout, PIPE_WIDTH)
                # The guide runs nothing, so the checks it reads print
                # no notes of their own (a built-in market list entry).
                self.assertEqual(r.stderr, "")
                out = r.stdout
                self.assertRegex(out, r"(?m)^  \[>\]  7\. Fill in missing "
                                      r"purchase history$")
                self.assertEqual(out.splitlines()[-1],
                                 "Next (step 7): `tjs find-missing-history`")
                # A done step is one state line; --all shows its commands.
                self.assertNotIn("tjs run --no-input", out)
                full = project(country).run("quick-start", "--all").stdout
                self.assertIn("tjs run --no-input", full)
                self.assertEqual(full.count("Why: "),
                                 len(json.loads(project(country).run(
                                     "quick-start", "--json").stdout)
                                     ["steps"]))

    def test_country_commands(self):
        ca = json.loads(project("canada").run("quick-start",
                                              "--json").stdout)
        us = json.loads(project("usa").run("quick-start", "--json").stdout)

        def cmds(doc):
            return {c["command"] for s in doc["steps"]
                    for c in s["commands"]}
        self.assertIn("tjs t1135", cmds(ca))
        self.assertNotIn("tjs t1135", cmds(us))
        self.assertIn("tjs form-export --form txf --out gains.txf", cmds(us))
        self.assertNotIn("tjs form-export --form txf --out gains.txf",
                         cmds(ca))

    def test_json_schema(self):
        doc = json.loads(project("canada").run("quick-start",
                                               "--json").stdout)
        self.assertEqual(set(doc), {"schema_version", "in_project", "year",
                                    "country", "counts", "next", "steps"})
        self.assertEqual(doc["schema_version"], 1)
        self.assertTrue(doc["in_project"])
        self.assertEqual((doc["year"], doc["country"]), (2024, "canada"))
        self.assertEqual(set(doc["counts"]), set(QS.STATUSES))
        self.assertEqual(sum(doc["counts"].values()), len(doc["steps"]))
        self.assertEqual(set(doc["next"]), {"step", "id", "title", "do"})
        ids = [s["id"] for s in doc["steps"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([s["step"] for s in doc["steps"]],
                         list(range(1, len(ids) + 1)))
        for s in doc["steps"]:
            self.assertEqual(set(s), {"step", "id", "section", "title",
                                      "how", "why", "commands", "status",
                                      "detail", "next"})
            self.assertIn(s["status"], QS.STATUSES)
            for c in s["commands"]:
                self.assertEqual(set(c), {"command", "note", "country"})
        self.assertEqual([s["id"] for s in doc["steps"] if s["next"]],
                         [doc["next"]["id"]])

    def test_read_only(self):
        """Nothing in the project changes, and no subprocess or socket
        is opened while the guide is evaluated."""
        p = project("canada")
        before = _snapshot(p.root)
        r = p.run("quick-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        p.run("quick-start", "--json")
        self.assertEqual(_snapshot(p.root), before)

        calls = []

        def refuse(name):
            def f(*a, **k):
                calls.append(name)
                raise AssertionError(f"quick-start called {name}")
            return f
        with mock.patch.object(subprocess, "run", refuse("subprocess.run")), \
                mock.patch.object(subprocess, "Popen",
                                  refuse("subprocess.Popen")), \
                mock.patch.object(socket.socket, "connect",
                                  refuse("socket.connect")), \
                mock.patch.object(os, "system", refuse("os.system")):
            g = QS.evaluate(p.root)
            QS.render(g)
            QS.to_json(g)
        self.assertEqual(calls, [])
        self.assertTrue(all(not s.detail.startswith("could not check")
                            for s in g.states.values()))
        self.assertEqual(_snapshot(p.root), before)


class TestCleanProject(unittest.TestCase):
    """A small clean project: one taxable account, a buy and its sale."""

    def test_mostly_done_and_next_is_the_checklist(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "clean"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                'option_grant_timing_since = 2024\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "book.tt").write_text(
                "BUYSELL 2024-01-10 10:00:00 ZZA.TO 100 CAD 10 1000\n"
                "BUYSELL 2024-06-03 10:00:00 ZZA.TO -100 CAD 12 1200\n")
            r = _cli("-C", str(root), "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = _cli("-C", str(root), "quick-start", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            text = _cli("-C", str(root), "quick-start").stdout
        st = {s["id"]: s["status"] for s in doc["steps"]}
        for sid in ("install", "init", "configure", "inputs", "run",
                    "elections", "missing-history", "transfers",
                    "ticker-map", "journals", "renames", "sanity",
                    "wash-sales", "option-timing"):
            self.assertEqual(st[sid], "done", sid)
        self.assertEqual(st["crypto-sends"], "n/a")
        self.assertEqual(doc["counts"]["attention"], 0)
        self.assertGreaterEqual(doc["counts"]["done"], 13)
        self.assertEqual(doc["next"]["id"], "checklist")
        assert_styled(self, text, PIPE_WIDTH)
        n = [x.id for x in QS.steps(2024)].index("checklist") + 1
        self.assertEqual(text.splitlines()[-1],
                         f"Next (step {n}): `tjs checklist`")

    def test_a_checklist_mark_counts(self):
        """A review step the user marked done in checklist.json is done."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\n'
                'type = "taxable"\n')
            (root / "checklist.json").write_text(json.dumps(
                {"year": 2024, "overrides": {"check-dates": {
                    "status": "done", "note": "looked", "date":
                    "2025-03-01"}}}))
            g = QS.evaluate(root)
        self.assertEqual(g.states["check-dates"].status, "done")
        self.assertIn("checklist.json", g.states["check-dates"].detail)


if __name__ == "__main__":
    unittest.main()
