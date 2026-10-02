"""`taxjson tax-logic`: the rule summary follows the project's settings."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.tax_logic import render, sections
from tax_rules import rule

SRC = Path(__file__).resolve().parents[1] / "src"


class TestTaxLogic(unittest.TestCase):
    def test_canada_defaults(self):
        text = render("canada", {})
        self.assertIn("TAX LOGIC — Canada", text)
        self.assertIn("SETTLES", text)
        self.assertIn("right to acquire", text)
        self.assertIn("a put never replaces the shares", text)

    @rule("CA-OPT-05", "CA-OPT-10")
    def test_settings_change_the_text(self):
        st = {"tax_date": "trade", "option_premium_timing": "close",
              "futures_settle": "next_day",
              "foreign_return_of_capital": "acb",
              "option_buyback_loss_superficial": True, "year": 2026}
        self.assertIn("tax year 2026", render("canada", st))
        text = " ".join(r for _t, rules in sections("canada", st)
                        for r in rules)
        self.assertIn("TRADED", text)
        self.assertIn('option_premium_timing = "close"', text)
        self.assertIn('futures_settle = "next_day"', text)
        self.assertIn('foreign_return_of_capital = "acb"', text)
        self.assertIn("premium minus the cost", text)
        self.assertIn("option_buyback_loss_superficial = true", text)

    def test_grant_since_named(self):
        text = render("canada", {"option_premium_timing": "grant",
                                 "option_grant_timing_since": 2025})
        self.assertIn("written from 2025", text)

    def test_grant_since_defaults_to_year(self):
        text = " ".join(r for _t, rules in sections(
            "canada", {"year": 2026}) for r in rules)
        self.assertIn("written from 2026", text)
        self.assertIn("defaults to the project year", text)

    def test_usa(self):
        titles = [t for t, _ in sections("usa", {})]
        self.assertIn("Wash sales (§1091)", titles)
        self.assertIn("United States", render("us", {}))

    def test_cli_uses_project_country_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "usa"\n')
            env = {"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"}
            r = subprocess.run([sys.executable, "-m",
                                "taxjson.bin.taxjson_run", "-C", td,
                                "tax-logic", "--json"],
                               capture_output=True, text=True, env=env,
                               stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["country"], "usa")
            r = subprocess.run([sys.executable, "-m",
                                "taxjson.bin.taxjson_run", "-C", td,
                                "tax-logic", "--country", "canada"],
                               capture_output=True, text=True, env=env,
                               stdin=subprocess.DEVNULL, timeout=120)
            self.assertIn("Canada", r.stdout)


if __name__ == "__main__":
    unittest.main()
