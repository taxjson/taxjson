"""Backlog fix round 7 (wave B) of 2026-10-29, group `tooling`: the
privacy and release tools — `tjs redact` (escaped ids in taxjson.toml,
folder paths in a settings file's comments, tab- and semicolon-delimited
exports), scripts/land.sh (the checks of the pushed commit, a status
request that fails) and the installer (channels.json read strictly, the
release an install stays on, a linked git worktree).

land.sh and install.sh run in throwaway folders: git remotes are local
bare repositories, `gh` and `python` are stubs on PATH, so nothing
touches the network or the real repository. Every value is synthetic.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_fix_release_channels as _channels
import test_fix_release_hardening as _hardening
from taxjson.bin import taxjson_redact as R
from test_fix_1029_r2_redact import tjs, toml, year_folder


def _fixture(case, cls, name):
    """An instance of another module's fixture class, set up for `case`
    (its own tests are not run again here)."""
    t = cls(name)
    t.setUp()
    case.addCleanup(t.tearDown)
    return t


class TestLandChecks(unittest.TestCase):
    """scripts/land.sh's merge decision (backlog TJ-0701, TJ-0702)."""

    def land_fixture(self):
        return _fixture(self, _hardening.TestLand,
                        "test_timeout_leaves_it_open")

    def test_a_failed_status_request_never_merges(self):
        # TJ-0701: the Actions jobs pass, the commit-status request fails.
        t = self.land_fixture()
        t._stub("gh", _hardening._LAND_GH.replace(
            'if path.endswith("/status"):\n        sys.exit(0)',
            'if path.endswith("/status"):\n'
            '        sys.stderr.write("HTTP 403: synthetic\\n"); sys.exit(1)'))
        t.set_state()
        t.commit()
        start = t.origin_main()
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(t.origin_main(), start, "nothing merged")
        self.assertFalse(t.calls("pr", "close"), "left open, not failed")
        self.assertFalse([c for c in t.calls("pr", "merge")
                          if "--auto" not in c])

    def test_a_failed_runs_request_never_merges(self):
        # The runs request fails; a commit status that passed is all
        # that is left, and it is not every check.
        t = self.land_fixture()
        t._stub("gh", _hardening._LAND_GH.replace(
            'if "/actions/runs?" in path:',
            'if "/actions/runs?" in path:\n        sys.exit(1)').replace(
            'if path.endswith("/status"):\n        sys.exit(0)',
            'if path.endswith("/status"):\n'
            '        print("external\\tpass"); sys.exit(0)'))
        t.set_state()
        t.commit()
        start = t.origin_main()
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(t.origin_main(), start)

    def test_expected_workflows_are_the_pushed_commits(self):
        # TJ-0702: two pull-request workflows committed; the second one's
        # run has not appeared. Deleting it only in the working tree must
        # not make land merge on the first one's run alone.
        t = self.land_fixture()
        t.workflow("CI")
        t.workflow("Second")
        t.git(t.dev, "add", "-A")
        t.set_state()
        t.commit("with workflows")
        (t.dev / ".github" / "workflows" / "second.yml").unlink()
        start = t.origin_main()
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(t.origin_main(), start)
        self.assertFalse((t.dev / ".github" / "workflows"
                          / "second.yml").exists(),
                         "the uncommitted deletion is left as it was")

    def test_an_uncommitted_rename_is_not_waited_for(self):
        # The phantom case: the commit's CI workflow ran and passed; a
        # rename only in the working tree must not be waited for.
        t = self.land_fixture()
        t.workflow("CI")
        t.git(t.dev, "add", "-A")
        t.set_state()
        t.commit("with a workflow")
        ci = t.dev / ".github" / "workflows" / "ci.yml"
        ci.write_text(ci.read_text().replace("name: CI", "name: Future CI"))
        r = t.land("--timeout", "0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(t.git(t.dev, "log", "-1", "--format=%s"),
                         "with a workflow")
        self.assertIn("name: Future CI", ci.read_text(),
                      "the uncommitted edit is kept")


class TestInstaller(unittest.TestCase):
    """install.sh's release choice (backlog TJ-0703, TJ-0704, TJ-0810)."""

    def inst_fixture(self):
        return _fixture(self, _channels.TestInstallerChannels,
                        "test_stable_by_default_then_follows_it")

    def dry(self, t, *args, **env):
        return t.install(*args, ok=False, TAXJSON_DRY_RUN="1", **env)

    def local_tag(self, t, annotated):
        """The install moved to a local commit off main with a newer tag."""
        t.install()
        t.git(t.inst, "checkout", "-q", "--detach", "origin/main")
        (t.inst / "local.txt").write_text("synthetic local change\n")
        t.git(t.inst, "add", "local.txt")
        t.git(t.inst, "commit", "-q", "-m", "local variation")
        if annotated:
            t.git(t.inst, "tag", "-a", "v99.0.0", "-m", "local tag")
        else:
            t.git(t.inst, "tag", "v99.0.0")

    def test_a_newer_local_tag_that_is_no_release_is_not_kept(self):
        for annotated in (False, True):
            with self.subTest(annotated=annotated):
                t = self.inst_fixture()
                self.local_tag(t, annotated)
                r = self.dry(t)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("channel stable → release v0.2.0", r.stdout)
                self.assertNotIn("release v99.0.0", r.stdout)
                self.assertIn("on v99.0.0, which is not a release", r.stdout)
                r = t.install()
                self.assertEqual(t.on(), "v0.2.0")

    def test_a_newer_release_is_still_kept(self):
        t = self.inst_fixture()
        t.install("--channel", "latest")
        r = self.dry(t, "--channel", "stable")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("on v0.3.0, newer than stable (v0.2.0) — staying",
                      r.stdout)
        self.assertIn("channel stable → release v0.3.0", r.stdout)

    def push_channels(self, t, text):
        (t.dev / "channels.json").write_text(text)
        t.git(t.dev, "commit", "-q", "-am", "synthetic channels")
        t.git(t.dev, "push", "-q", "origin", "main")

    def test_malformed_channels_json_is_refused(self):
        for text in ('{ this is not valid JSON; "stable":"v0.1.0"\n',
                     '{"stable": "v0.1.0", "beta": "v0.3.0",}\n',
                     '{"stable": "v0.1.0"} {"stable": "v0.3.0"}\n',
                     '["stable", "v0.1.0"]\n',
                     '{"stable": "v0.1.0", "latest": "v0.3.0"}\n'):
            with self.subTest(text=text):
                t = self.inst_fixture()
                self.push_channels(t, text)
                r = self.dry(t)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("channels.json on main is not a JSON object",
                              r.stderr)
                self.assertNotIn("→ release", r.stdout)

    def test_a_value_that_is_no_release_is_refused(self):
        t = self.inst_fixture()
        t.git(t.dev, "tag", "-a", "v0.4.0-rc1", "-m", "synthetic candidate")
        t.git(t.dev, "push", "-q", "origin", "v0.4.0-rc1")
        t.set_origin_channels("v0.4.0-rc1")
        r = self.dry(t)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("v0.4.0-rc1  (dry run", r.stdout)
        self.assertIn("fix channels.json on main", r.stderr)

    def test_valid_channels_json_in_any_layout(self):
        for text, want in (
                ('{"beta":"v0.3.0","stable":"v0.1.0"}', "v0.1.0"),
                ('\n{\n\t"stable" :\r\n "v0.1.0"\n}\n', "v0.1.0"),
                ('{"stable": "v0.3.0", "stable": "v0.1.0"}', "v0.1.0"),
                ('{"beta": "v0.1.0"}', "v0.3.0"),      # none: the newest
                ('{}', "v0.3.0")):
            with self.subTest(text=text):
                t = self.inst_fixture()
                self.push_channels(t, text)
                r = self.dry(t)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"channel stable → release {want}", r.stdout)

    def test_a_linked_worktree_is_an_existing_install(self):
        # TJ-0810: TAXJSON_DIR is a linked worktree (its .git a file) on
        # v0.3.0; stable names v0.2.0.
        t = self.inst_fixture()
        t.install()
        wt = t.d / "wt"
        t.git(t.inst, "worktree", "add", "-q", "--detach", str(wt), "v0.3.0")
        self.assertTrue((wt / ".git").is_file())
        r = self.dry(t, TAXJSON_DIR=str(wt))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("does not exist", r.stdout)
        self.assertIn("on v0.3.0, newer than stable (v0.2.0) — staying",
                      r.stdout)
        r = t.install(TAXJSON_DIR=str(wt))
        self.assertIn("channel stable → release v0.3.0", r.stdout)
        got = subprocess.run(["git", "-C", str(wt), "describe", "--tags",
                              "--exact-match"], capture_output=True,
                             text=True, env=_channels.GIT_ENV)
        self.assertEqual(got.stdout.strip(), "v0.3.0")


# The synthetic ids of TJ-0835, as taxjson.toml spells them with escapes.
ESCAPED = 'account = "\\u00551234567"\nquery_id = "20\\u00325"\n'  # pii-ok: synthetic
LITERAL = 'account = "U1234567"\nquery_id = "2025"\n'


class TestRedactEscapedIds(unittest.TestCase):
    """TJ-0835: an id in taxjson.toml written with TOML escapes is
    replaced in the copy, and --check of a copy finds one left."""

    def redact(self, extra):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = year_folder(Path(td.name), extra=extra)
        r = tjs(p, "redact", "--no-denylist")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return p, p / "inputs_redact" / "taxjson.toml"

    def test_escaped_ids_are_replaced_like_literal_ones(self):
        _p, lit = self.redact(LITERAL)
        _p, esc = self.redact(ESCAPED)
        want = toml(lit)["accounts"]["margin"]
        self.assertEqual(want["account"], "U9990001")
        self.assertEqual(want["query_id"], "9999")
        self.assertEqual(toml(esc)["accounts"]["margin"], want)
        self.assertNotIn("1234567", esc.read_text())
        self.assertEqual(toml(esc)["settings"]["year"], 2025)

    def test_check_finds_an_escaped_id_left_in_a_copy(self):
        p, cfg = self.redact(LITERAL)
        r = tjs(p / "inputs_redact", "redact", "--check", "--no-denylist")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg.write_text(cfg.read_text().replace('"U9990001"',
                                               '"\\u00551234567"'))
        self.assertEqual(toml(cfg)["accounts"]["margin"]["account"],
                         "U1234567")
        r = tjs(p / "inputs_redact", "redact", "--check", "--no-denylist")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("account id", r.stdout + r.stderr)

    def test_plain_strings_read_the_same(self):
        text = ('[settings]\nyear = 2025  # "\\u0055" in a comment\n'
                '"query\\u005fid" = "20\\u00325"\n'
                "lit = '\\u0055 stays'\n"
                'q = "a \\" quote \\u0022 and \\\\u0055 kept"\n'
                'tab = "\\u0009"\n'
                'ml = """\n\\u00551234567\\\n  more""""\n'
                "ml2 = '''x'''''\n")
        new = R.plain_toml_strings(text)
        self.assertEqual(toml_text(new), toml_text(text))
        self.assertIn('"query_id" = "2025"', new)
        self.assertIn("U1234567", new)
        self.assertIn("'\\u0055 stays'", new)            # a literal string
        self.assertIn('# "\\u0055" in a comment', new)    # a comment
        self.assertIn("\\u0022", new)                     # a quote stays one
        self.assertIn("\\\\u0055 kept", new)              # an escaped backslash
        self.assertIn('"\\u0009"', new)                   # not printable
        # Text that does not read is left as it was.
        bad = 'a = "\\u0055\n'
        self.assertEqual(R.plain_toml_strings(bad), bad)


class TestRedactCommentPaths(unittest.TestCase):
    """TJ-0714: a folder path of this computer in a settings file's
    comment is replaced in the copy, and --check names one left."""

    def test_function(self):
        text = ('[settings]\n# was: inputs_dir = "/srv/casey/taxes/inputs"\n'
                'year = 2025\n')
        out, rep = R.redact_project_text(text, [], {})
        self.assertEqual(out, '[settings]\n# was: inputs_dir = '
                              '"<redacted path>"\nyear = 2025\n')
        self.assertEqual(rep.paths, [2])
        self.assertTrue(rep.found_anything())

    def test_shapes(self):
        for comment, want in (
                ("# see ~casey/taxes/2024/ for last year",
                 "# see <redacted path> for last year"),
                ("#C:\\Users\\casey\\taxes", "#<redacted path>"),
                ("# '/home/Casey Example/taxes' (old)",
                 "# '<redacted path>' (old)"),
                ("# holdings = [\"/srv/casey/snap.toml\", \"x.toml\"]",
                 "# holdings = [\"<redacted path>\", \"x.toml\"]"),
                ("# \\\\server\\share\\taxes", "# <redacted path>")):
            with self.subTest(comment=comment):
                self.assertEqual(R._redact_comment_paths(comment),
                                 (want, 1))
        for comment in ("# https://example.com/docs/x/", "# /ES futures",
                        "# 50/50 split", "# and/or", "# docs/commands.md",
                        "# ../inputs", "# <redacted path>",
                        # (a home folder naming no one: the templates')
                        "# diff ~/taxes/2023/taxjson.toml"):
            with self.subTest(comment=comment):
                self.assertEqual(R._redact_comment_paths(comment),
                                 (comment, 0))

    def test_a_year_folders_copy(self):
        with tempfile.TemporaryDirectory() as td:
            p = year_folder(Path(td))
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "[settings]\n",
                '[settings]\n# was: inputs_dir = "/srv/casey/taxes/inputs"\n'))
            (p / "ticker.map").write_text("# from /srv/casey/maps/old.map\n")
            r = tjs(p, "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            dst = p / "inputs_redact"
            for name in ("taxjson.toml", "ticker.map"):
                self.assertNotIn("/srv/casey", (dst / name).read_text())
            self.assertIn("folder path", r.stdout + r.stderr)
            self.assertEqual(toml(dst / "taxjson.toml")["settings"]["year"],
                             2025)
            r = tjs(dst, "redact", "--check", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # A copy that still holds one (an older taxjson's).
            (dst / "ticker.map").write_text("# from /srv/casey/maps/old.map\n")
            r = tjs(dst, "redact", "--check", "--no-denylist")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("ticker.map", r.stdout + r.stderr)


class TestRedactOtherDelimiters(unittest.TestCase):
    """TJ-0569 (the rest): a tab-, semicolon- or pipe-delimited table's
    AccountAlias is replaced, not only listed for review."""

    def test_the_alias_is_replaced(self):
        for d in ("\t", ";", "|"):
            with self.subTest(delimiter=d):
                text = (f"Date{d}AccountAlias{d}Symbol\r\n"
                        f"2025-01-03{d}Sample Person{d}SYN.TO\r\n"
                        f'2025-01-04{d}"Other Person"{d}SYN.TO\r\n')
                out, rep = R.redact_text(text)
                self.assertEqual(out, (
                    f"Date{d}AccountAlias{d}Symbol\r\n"
                    f"2025-01-03{d}REDACTED{d}SYN.TO\r\n"
                    f'2025-01-04{d}"REDACTED"{d}SYN.TO\r\n'))
                self.assertEqual(rep.identity_rows, 2)
                self.assertEqual(rep.review, [])

    def test_a_name_beside_the_account_and_a_decimal_comma(self):
        text = ("Account;Name;Amount\n"
                "U1234567;Sample Person;1,50\n")   # pii-ok: synthetic
        out, rep = R.redact_text(text)
        self.assertNotIn("Sample Person", out)
        self.assertNotIn("U1234567", out)
        self.assertIn(";1,50\n", out)

    def test_the_table_ends_at_another_width(self):
        text = ("Date\tAccountAlias\tSymbol\n"
                "2025-01-03\tSample Person\tSYN.TO\n"
                "Total\t2\n"
                "2025-01-05\tKeep This\tSYN.TO\n")
        out, _rep = R.redact_text(text)
        self.assertIn("2025-01-03\tREDACTED\tSYN.TO\n", out)
        self.assertIn("Total\t2\n", out)
        self.assertIn("2025-01-05\tKeep This\tSYN.TO\n", out)

    def test_a_comma_csv_is_read_as_before(self):
        text = ('Date,Description,Amount\n'
                '2025-01-03,"Note; part; two",5\n')
        self.assertEqual(R.redact_text(text)[0], text)
        # A tab table with no identity column is left alone.
        text = "Date\tSymbol\tAmount\n2025-01-03\tSYN.TO\t5\n"
        self.assertEqual(R.redact_text(text)[0], text)

    def test_a_redacted_copy_checks_clean(self):
        text = "Date\tAccountAlias\tSymbol\n2025-01-03\tSample Person\tSYN.TO\n"
        out, _rep = R.redact_text(text)
        again, rep = R.redact_text(out, stand_ins=True)
        self.assertEqual(again, out)
        self.assertFalse(rep.found_anything())


def toml_text(text):
    from taxjson.lib.tomlcompat import tomllib
    return tomllib.loads(text)


if __name__ == "__main__":
    unittest.main()
