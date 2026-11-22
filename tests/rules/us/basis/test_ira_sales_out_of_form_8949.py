"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, settings_for
from taxjson.rules import ids as R

from taxjson.lib import tax_logic as TL


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _text(country, rid, **settings):
    """The rendered text of one rule under `settings`."""
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


def _project(td, country, files, *, accounts=("margin",), year=2025,
             **settings):
    root = Path(td) / country
    root.mkdir(parents=True, exist_ok=True)
    if not isinstance(accounts, dict):
        accounts = {a: "taxable" for a in accounts}
    acc = "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items())
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, **settings) + acc)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


class TestUsShelteredOutOfTheTotals(unittest.TestCase):
    """A2-0822: US tax-logic states that sheltered (IRA) accounts are
    kept out of Form 8949 and the totals.

    Formerly: test_fix_a2_spec.TestUsShelteredOutOfTheTotals"""

    @rule(R.US_BASIS_07)
    def test_ira_sale_is_not_on_form_8949(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "inputs/margin/m.tt":
                    "BUYSELL 2024-02-01 10:00:00 XYZ.US 10 USD 100 1000 0\n"
                    "BUYSELL 2025-06-02 10:00:00 XYZ.US -10 USD 200 2000 0\n",
                "inputs/ira/i.tt":
                    "BUYSELL 2024-02-01 10:00:00 QQZ.US 10 USD 100 1000 0\n"
                    "BUYSELL 2025-06-02 10:00:00 QQZ.US -10 USD 300 3000 0\n"},
                accounts={"margin": "taxable", "ira": "sheltered"})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            f = cli(root, "form-export", "--form", "8949")
            self.assertEqual(f.returncode, 0, f.stderr[-2000:])
        self.assertIn("XYZ.US", f.stdout)
        self.assertNotIn("QQZ.US", f.stdout)
        self.assertIn("sheltered", _text("usa", "US-BASIS-07"))


if __name__ == "__main__":
    unittest.main()
