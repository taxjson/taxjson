"""2026-10 workflow audit, the polish pass: the friction and cosmetic
findings (CHANGELOG "Fixed"). A command's own usage line on an unknown
flag, a wrapper's relayed child error, one frozen note, the demo's
province, a new year's config in the template layout, a relative FILE
found in the project, reconcile-slips with no slip yet, the checklist's
DONE note, the watch title, the hand-off record path, money in
wash-radar's advice, the stale-wash warning once,
option-boundary's attention reason, an init folder owner-only, a
symlinked shared inputs/ keyed as the setting names it, init's --force
help, migrate's note, option-yield's stderr."""
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from _qa_project import console, project, tj
from test_workflow_audit_fixes import QT_ROWS

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestUnits(unittest.TestCase):
    def test_child_error_strips_the_label_on_every_line(self):
        from taxjson.bin.taxjson_run import _child_error
        text = _child_error("Info: crypto account(s) excluded: crypto\n"
                            "Error: taxjson estimate: the canada estimate "
                            "needs a province\nPass --province ON|BC|AB.")
        self.assertNotIn("Error:", text)
        self.assertNotIn("taxjson estimate:", text)
        self.assertIn("the canada estimate needs a province", text)

    def test_one_frozen_note_over_adjacent_keys(self):
        from taxjson.bin.taxjson_run import (_FROZEN_NOTE, _FROZEN_NOTE_2,
                                             _one_frozen_note)
        adjacent = "\n".join(["[settings]", _FROZEN_NOTE,
                              '# inputs_dir = "../inputs"', _FROZEN_NOTE_2,
                              '# tobase_map = "../tobase.map"', "year = 2024"])
        out = _one_frozen_note(adjacent)
        self.assertEqual(out.count("## frozen by"), 1)
        self.assertIn('# tobase_map = "../tobase.map"', out)
        apart = "\n".join(["[settings]", _FROZEN_NOTE,
                           '# inputs_dir = "../inputs"', 'year = 2024',
                           _FROZEN_NOTE_2, '# tobase_map = "../tobase.map"'])
        self.assertEqual(_one_frozen_note(apart), apart)

    def test_watch_title_has_one_parenthesis(self):
        from taxjson.bin.taxjson_watch import render_report
        text = render_report([], "2026-10-20", since="2026-10-17", width_=0)
        self.assertIn("(previous baseline 2026-10-17, as of 2026-10-20)", text)
        self.assertNotIn(") (", text)
        self.assertIn("(as of 2026-10-20)",
                      render_report([], "2026-10-20", width_=0))

    def test_handoff_record_path_is_normalized(self):
        from taxjson.lib.handoff import render
        rep = {"year": 2024, "notes": [], "positions": [], "missed": [],
               "double": [], "partial": [], "problems": 0}
        lines = render(rep, "/p/2025/../2024/filed/2024.json")
        self.assertIn("Record: /p/2024/filed/2024.json", "\n".join(lines))
        self.assertNotIn("/2025/../", "\n".join(lines))

    def test_option_boundary_attention_reason(self):
        from taxjson.lib.checklist import _attention_reason
        row = {"symbol": "ZZQ250117C00030000.US",
               "action": "ATTENTION: import the expiry, assignment or "
                         "buy-back row for 1 unit(s) (the broker export is "
                         "missing it); until then the 713.00 premium's year "
                         "is unknown"}
        self.assertEqual(_attention_reason(row), "past expiry, no expiry row")
        self.assertEqual(_attention_reason({"action": "ATTENTION: 2024 is "
                                            "locked (filed/2024.json); x"}),
                         "a locked year to amend")

    def test_wash_radar_advice_money_has_two_decimals(self):
        src = (REPO_ROOT / "src/taxjson/bin/taxjson_wash_radar.py").read_text()
        self.assertNotRegex(src, r"\$\{[^}]*:\.4f\}")

    def test_stale_wash_warning_once_per_process(self):
        from taxjson.lib import report_model as RM
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "m_gains.json").write_text("[]")
            (cache / "m_gains_wash.json").write_text("[]")
            RM._STALE_SAID.clear()
            with mock.patch.object(RM, "stale_wash_inputs",
                                   return_value=["m_base.json"]):
                err = io.StringIO()
                with redirect_stderr(err):
                    RM.resolve_gains_files(cache, prefer_wash=True)
                    RM.resolve_gains_files(cache, prefer_wash=True)
            self.assertEqual(err.getvalue().count("was built before"), 1,
                             err.getvalue())

    def test_symlinked_shared_inputs_key_as_the_setting_names_it(self):
        from taxjson.lib.checklist import input_fingerprint
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp)
            (top / "realinputs" / "qt").mkdir(parents=True)
            (top / "realinputs" / "qt" / "trades.tt").write_text("# tt\n")
            os.symlink("realinputs", top / "inputs")
            root = top / "2024"
            root.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'inputs_dir = "../inputs"\n\n[accounts.qt]\n'
                'brokerage = "questrade"\nkind = "margin"\n')
            cfg = {"settings": {"year": 2024, "country": "canada",
                                "inputs_dir": "../inputs"},
                   "accounts": {"qt": {"brokerage": "questrade"}}}
            keys = set(input_fingerprint(root, cfg))
            self.assertIn("inputs/qt/trades.tt", keys)
            self.assertFalse(any(k.startswith("realinputs/") for k in keys),
                             keys)


class TestSingleProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "single",
                           {"margin/questrade_2024.csv": QT_ROWS})
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_unknown_flag_gets_the_commands_usage(self):
        r = tj(self.root, "option-yield", "--details", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("option-yield [-h]", r.stderr)
        self.assertIn("unrecognized arguments: --details", r.stderr)
        self.assertNotIn("[-C DIR]", r.stderr)

    def test_relative_file_is_found_in_the_project(self):
        (self.root / "pos.toml").write_text(
            '[[holding]]\nsymbol = "ZZQ.US"\nquantity = 0\n')
        r = tj(self.root, "sanity", "margin=pos.toml", check=False)
        self.assertNotIn("is not an existing .toml file", r.stderr,
                         console(r))
        r = tj(self.root, "sanity", "margin=nope.toml", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("then from the project's", r.stderr)

    def test_reconcile_slips_with_no_slip_yet(self):
        r = tj(self.root, "reconcile-slips", "inputs/slips/*.csv",
               check=False)
        self.assertEqual(r.returncode, 2, console(r))
        self.assertIn("Error:", r.stderr)
        self.assertIn("no slip CSV matches inputs/slips/*.csv", r.stderr)
        self.assertIn("--skip t5008", r.stderr)

    def test_done_mark_says_a_finding_outranks_it(self):
        r = tj(self.root, "checklist", "--done", "inputs-frozen")
        self.assertIn("A DONE mark never hides a finding", r.stdout)
        self.assertIn("`--skip inputs-frozen`", r.stdout)
        r = tj(self.root, "checklist", "--undo", "inputs-frozen")
        self.assertNotIn("never hides", r.stdout)

    def test_migrate_note_in_the_house_style(self):
        r = tj(self.root, "migrate", "--dry-run", check=False)
        self.assertRegex(r.stdout, r"(?m)^Info: .*nothing to migrate")
        self.assertNotIn("taxjson migrate: nothing", r.stdout)

    def test_redact_done_line(self):
        r = tj(self.root, "redact", check=False)
        self.assertNotIn("—,", r.stdout + r.stderr)

    def test_empty_inputs_name_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "empty" / "inputs" / "margin").mkdir(parents=True)
            root = project(tmp, "empty", {})
            r = tj(root, "run", "--no-input", check=False)
            self.assertIn("put each broker's export in inputs/<account>/",
                          r.stderr)

    def test_init_force_help_and_owner_only_folder(self):
        r = tj(self.root, "init", "--help")
        self.assertIn("taxjson.toml.bak", r.stdout)
        if os.name == "nt":
            return
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "taxes"
            d.mkdir()
            os.chmod(d, 0o775)
            tj(d, "init", "--single", "--country", "canada", "--year",
               "2024", str(d))
            self.assertEqual(stat.S_IMODE(d.stat().st_mode) & 0o077, 0)
            r = tj(d, "run", "--no-input", check=False)
            self.assertNotIn("can be read by other users", r.stderr)


class TestDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.top = Path(cls.tmp.name) / "demo"
        r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                            "init", "--demo", str(cls.top)],
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, console(r)
        cls.root = cls.top / "2024"
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_demo_has_a_province(self):
        from taxjson.lib import demo as DM
        self.assertIn(f'province                          = "{DM.PROVINCE}"',
                      (self.root / "taxjson.toml").read_text())
        r = tj(self.root, "estimate", check=False)
        self.assertEqual(r.returncode, 0, console(r))

    def test_new_year_config_is_in_the_template_layout(self):
        from taxjson.lib.config_template import format_config
        tj(self.root, "new-year", "2025")
        text = (self.top / "2025" / "taxjson.toml").read_text()
        self.assertFalse(format_config(text).changed)
        r = tj(self.top / "2025", "checklist", "--run", "--only", "format")
        self.assertNotIn("not in the template layout", r.stdout)

    def test_option_yield_has_no_blank_stderr_line(self):
        r = tj(self.root, "option-yield")
        self.assertFalse(r.stderr.startswith("\n"), repr(r.stderr[:80]))
        self.assertNotIn("\n\n", r.stderr)

    def test_single_account_run_line_names_the_fix(self):
        r = tj(self.root, "run", "--account", "margin", "--no-input")
        self.assertRegex(r.stderr, r"so `(taxjson|tjs) run` fully before filing")

    def test_wash_radar_money(self):
        r = tj(self.root, "wash-radar", "--date", "2024-11-25", "--details")
        self.assertNotRegex(r.stdout, r"\$\d+\.\d{4}\b")
        self.assertRegex(r.stdout, r"[Ll]oss of \$\d+\.\d\d on ")


if __name__ == "__main__":
    unittest.main()
