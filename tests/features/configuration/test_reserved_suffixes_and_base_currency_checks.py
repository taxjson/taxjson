"""Mutation pins for re-audit-2 test-gap findings (tests-pins-04/05/06):
RBC and Webull parser dates and pairing, the MXN settlement cutover, the
phantom-relevance split de-duplication and the config checks.

Each test fails when the finding's surviving mutant is applied. All data
is synthetic: fake tickers and a fake broker account id.

Formerly: test_fix_a2_pins2_rbc"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths

REPO_ROOT = _paths.REPO


# ---------------------------------------------------------- config_check
_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""
_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n"
       "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES "
       "COMP,100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,"
       "Individual\n")  # pii-ok (synthetic)


def _project(tmp, config, accounts=("margin",)):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "questrade_2025.csv").write_text(_QT)
    return root


def _cli(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _settings(**kw):
    from taxjson.lib.config_check import settings_problems
    cfg = {"settings": dict(country="canada", **kw)}
    return settings_problems(cfg), cfg["settings"]


class TestConfigChecks(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestConfigChecks"""

    # S038-07 and siblings: every reserved suffix, written out here so a
    # suffix dropped from config_check fails this test.
    RESERVED = ("_raw", "_base", "_gains", "_wash", "_merged", "_sorted",
                "_filled", "_mapped", "_report", "_tt", "_manifest",
                "_sources")

    def test_a2_1599_every_reserved_suffix_is_refused(self):
        from taxjson.lib.config_check import account_name_problem
        for suf in self.RESERVED:
            with self.subTest(suffix=suf):
                msg = account_name_problem("m" + suf)
                self.assertIn(repr(suf), msg)
                self.assertIn(repr(suf), account_name_problem(
                    "M" + suf.upper()))

    def test_a2_0927_account_named_wash_is_refused_by_run(self):
        """S038-07: an account `m_wash` overwrote account m's wash report
        (reports/m_wash.sum) with rc 0."""
        cfg = _CONFIG + '\n[accounts.m_wash]\ntype = "taxable"\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg, accounts=("margin", "m_wash"))
            r = _cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("m_wash", r.stderr)
        self.assertIn("'_wash'", r.stderr)

    def test_a2_0926_base_currency_is_upper_cased(self):
        """S030-20: base_currency = "cad" converted every CAD fee at the
        default FX rate; the reader canonicalises the spelling."""
        for raw in ("cad", " Cad ", "CAD"):
            probs, s = _settings(base_currency=raw)
            self.assertEqual(probs, [], raw)
            self.assertEqual(s["base_currency"], "CAD", raw)

    def test_a2_0928_settings_must_be_a_table(self):
        """S038-12: `settings = "x"` is a one-line error, not an
        AttributeError traceback."""
        from taxjson.lib.config_check import settings_problems
        self.assertEqual(settings_problems({"settings": "x"}),
                         ["[settings] must be a table"])
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, 'settings = "x"\n\n[accounts.margin]\n'
                                 'type = "taxable"\n')
            r = _cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[settings] must be a table", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a2_1598_base_currency_must_be_three_letters(self):
        for bad in (5, "CADX", "C$", ""):
            with self.subTest(base=bad):
                probs, _ = _settings(base_currency=bad)
                self.assertTrue(any("3-letter" in p for p in probs), probs)
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG.replace('"CAD"', '5'))
            r = _cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("3-letter", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
