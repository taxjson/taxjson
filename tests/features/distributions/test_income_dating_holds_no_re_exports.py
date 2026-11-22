"""lib/income_dating.py after its re-exports went (TJ-4223, after v1.0.0).

The income-dating and payment-in-lieu rules are
taxjson.characterize.income_dating's (PLAN step 14); lib/income_dating.py
holds only what reads a project's [settings] (`IncomeRules.from_settings`,
`rules_for`), which characterize may not import. The names it imported back
so old imports kept working are gone: each name it imports, its own code
reads.
"""
import ast
import unittest

import _paths

MODULE = _paths.SRC / "taxjson" / "lib" / "income_dating.py"


class TestWhatTheModuleHolds(unittest.TestCase):
    def test_it_re_exports_nothing(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = {a.asname or a.name.partition(".")[0] for n in tree.body
                    if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names} - {"annotations"}
        read = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        self.assertEqual(sorted(imported - read), [])
        self.assertNotIn("__all__", {t.id for n in tree.body if isinstance(n, ast.Assign)
                                     for t in n.targets if isinstance(t, ast.Name)})

    def test_what_reads_the_settings_stays(self):
        from taxjson.characterize import income_dating as rules
        from taxjson.lib.income_dating import IncomeRules, rules_for
        self.assertTrue(issubclass(IncomeRules, rules.IncomeRules))
        self.assertEqual(rules_for("canada").country, "canada")
        self.assertEqual(IncomeRules.from_settings({"country": "usa"}).country, "usa")


if __name__ == "__main__":
    unittest.main()
