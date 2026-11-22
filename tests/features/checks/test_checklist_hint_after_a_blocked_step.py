"""2026-10 workflow audit: the fixes the audit's confirmed findings asked
for (CHANGELOG "Fixed"): interest paid shown apart, a negative futures
price in a generic CSV, the audit over a pending election, the
checklist's hints, init's timezone, the single-folder year hints,
sanity with no holdings file, the headless election message, no empty
manifest.json, compare's exit codes (tests/test_compare.py).

Formerly: test_workflow_audit_fixes"""
import tempfile
import unittest
from pathlib import Path

from _qa_project import project, tj
from _support.qt_trades import QT_ROWS


class TestChecklistHints(unittest.TestCase):
    """Formerly: test_workflow_audit_fixes.TestChecklistHints"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.top = Path(cls.tmp.name) / "demo"
        r = tj(cls.top, "init", "--demo", str(cls.top))
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_next_follows_the_blocked_step_then_names_the_shared_folder(self):
        year = self.top / "2024"
        r = tj(year, "checklist", "--run", "--only", "inputs-frozen", check=False)
        text = " ".join(r.stdout.split())
        self.assertIn("Next (step 4, inputs-frozen): no books yet — "
                      "run `tjs run`", text)
        self.assertNotIn("download the exports", text)
        tj(year, "run", "--no-input")
        r = tj(year, "checklist", "--run", "--only", "inputs-frozen", check=False)
        text = " ".join(r.stdout.split())
        self.assertIn("download the exports into ../inputs/<account>/", text)

    def test_configure_names_the_file_not_migrate(self):
        root = project(self.tmp.name, "crypto",
                       {"margin/questrade_2024.csv": QT_ROWS},
                       extra_accounts='[accounts.cb]\ntype = "taxable"\n'
                                      'crypto = true\n')
        toml = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(
            "\n".join(ln for ln in toml.splitlines()
                      if not ln.startswith("local_timezone")) + "\n")
        r = tj(root, "checklist", "--run", check=False)
        line, = [ln for ln in r.stdout.splitlines()
                 if ln.startswith("! 3 configure:")]
        self.assertTrue(line.endswith("— edit taxjson.toml"), line)
        self.assertNotIn("tjs migrate", line)


if __name__ == "__main__":
    unittest.main()
