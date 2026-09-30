"""Regression pins for the run-core medium findings (2026-09 audit).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no FX fetch.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

# Buy 100 @ 10.00 (comm 9.95) -> ACB 1009.95; sell 100 @ 15.00 (comm
# 9.95) -> proceeds 1490.05; gain 480.10.
_MARGIN_CSV = _QT_HEADER + (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,ISHARES COMP,"
    "-100,15.00,1500.00,9.95,1490.05,CAD,55500001,Trades,Individual\n")


def _run_cli(root, *args, env=None):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(tmp, config=_CONFIG, csv=_MARGIN_CSV):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(csv)
    return root


def _set_config(root, text):
    (root / "taxjson.toml").write_text(text)


class TestSettingsValidatedEverywhere(unittest.TestCase):
    """R1-153 (base_currency) and R1-256 (year): checked by every config
    reader, not left to fail later as a misleading stage error."""

    def test_base_currency_is_trimmed_and_upper_cased(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG.replace('"CAD"', '" cad "'))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertNotIn("non-CAD", r.stderr)
            doc = json.loads(_run_cli(root, "sum", "--json").stdout)
            self.assertEqual(doc.get("currency", "CAD"), "CAD")

    def test_base_currency_not_a_code_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            _set_config(root, _CONFIG.replace('"CAD"', '"C A D"'))
            r = _run_cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("base_currency", r.stderr)
            r = _run_cli(root, "sum")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("base_currency", r.stderr)

    def test_implausible_year_is_refused_by_run_and_sum(self):
        for bad in ("2204", "1850", "0", "true"):
            with self.subTest(year=bad), \
                    tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, _CONFIG.replace("year = 2025",
                                                     f"year = {bad}"))
                r = _run_cli(root, "run", "--no-input")
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("year", r.stderr)
                r = _run_cli(root, "sum")
                self.assertNotEqual(r.returncode, 0)

    def test_year_with_no_activity_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG.replace("year = 2025",
                                                 "year = 2015"))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no transaction", r.stderr)
            self.assertIn("2015", r.stderr)


class TestConfigTablesChecked(unittest.TestCase):
    """R1-216 / R1-257 / S038-13: a misspelled top-level table or key
    in [estimate] / [instalments] is reported by the commands that read
    them, not only (or never) by `run`."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = _project(self._td.name)
        r = _run_cli(self.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr

    def tearDown(self):
        self._td.cleanup()

    def test_misspelled_estimate_table_warns_in_estimate_and_run(self):
        _set_config(self.root, _CONFIG + "\n[estimates]\nother_income = "
                    "200000\n")
        r = _run_cli(self.root, "estimate", "--province", "ON", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[estimates]", r.stderr)
        self.assertIn("did you mean", r.stderr)
        r = _run_cli(self.root, "run", "--no-input")
        self.assertIn("[estimates]", r.stderr)

    def test_misspelled_key_warns_in_estimate(self):
        _set_config(self.root, _CONFIG + "\n[estimate]\nother_incme = "
                    "120000\n")
        r = _run_cli(self.root, "estimate", "--province", "ON")
        self.assertIn("other_incme", r.stderr)
        self.assertIn("other_income", r.stderr)

    def test_misspelled_instalments_key_warns_in_instalments(self):
        _set_config(self.root, _CONFIG + (
            "\n[instalments]\nbasis = \"prior_year\"\n"
            "prior_year_net_tax = 8000\nsecond_prior_net_tax = 8000\n"
            "witheld = 2000\n"))
        r = _run_cli(self.root, "instalments")
        self.assertIn("witheld", r.stderr)
        self.assertIn("withheld", r.stderr)


class TestInstalmentMoneyInputs(unittest.TestCase):
    """R1-217: [instalments] money inputs are sign-, finiteness- and
    type-checked like paid[].amount already was; [estimate] rejects a
    TOML boolean."""

    def _instalments(self, extra):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + "\n[instalments]\nbasis = "
                            "\"prior_year\"\nprior_year_net_tax = 8000\n"
                            "second_prior_net_tax = 8000\n" + extra)
            return _run_cli(root, "instalments")

    def test_negative_withheld_is_refused(self):
        r = self._instalments("withheld = -5000\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("withheld", r.stderr)

    def test_nan_and_bool_withheld_are_refused(self):
        for v in ("nan", "true", "inf"):
            with self.subTest(v=v):
                r = self._instalments(f"withheld = {v}\n")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("withheld", r.stderr)

    def test_negative_prior_year_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + "\n[instalments]\nbasis = "
                            "\"prior_year\"\nprior_year_net_tax = -60000\n")
            r = _run_cli(root, "instalments")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("prior_year_net_tax", r.stderr)

    def test_rate_must_be_a_fraction_below_one(self):
        for v in ("-0.08", "1.0"):
            with self.subTest(rate=v):
                r = self._instalments(f"prescribed_rate = {v}\n")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("prescribed_rate", r.stderr)
        r = self._instalments(
            'prescribed_rates = [{ from = "2025-01-01", rate = -0.07 }]\n')
        self.assertNotEqual(r.returncode, 0)

    def test_estimate_boolean_other_income_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + "\n[estimate]\nother_income = "
                            "true\n")
            _run_cli(root, "run", "--no-input")
            r = _run_cli(root, "estimate", "--province", "ON")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("other_income", r.stderr)


class TestNetTaxOwingGuidance(unittest.TestCase):
    """R1-215: the guidance for the prior years' net tax owing named
    line 48500 minus withholding — 48500 already subtracts withholding
    AND the instalments paid, so following it reads 'no instalments
    required' for a typical instalment payer."""

    def test_no_guidance_names_line_48500(self):
        from taxjson.bin import taxjson_run as R
        from taxjson.bin import taxjson_instalments as I
        texts = [R._TEMPLATE_INSTALMENTS,
                 (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
                 Path(I.__file__).read_text(encoding="utf-8")]
        for t in texts:
            self.assertNotIn("48500 minus", t)
        self.assertIn("43700", R._TEMPLATE_INSTALMENTS)


class TestInitForceKeepsEveryBackup(unittest.TestCase):
    """R1-255: a second `init --force` overwrote taxjson.toml.bak with
    the first template, losing the user's config."""

    def test_second_force_keeps_the_first_backup(self):
        from taxjson.bin.taxjson_run import cmd_init

        def _init(path, country):
            with redirect_stdout(io.StringIO()) as out:
                cmd_init(argparse.Namespace(path=str(path), dir=".",
                                            force=True, country=country,
                                            year=None))
            return out.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            _init(root, "ca")
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text() + "\n# MARKER-55500001\n")
            _init(root, "us")
            out = _init(root, "ca")
            hits = [p.name for p in root.iterdir()
                    if p.name.startswith("taxjson.toml.bak")
                    and "MARKER-55500001" in p.read_text()]
            self.assertEqual(len(hits), 1, sorted(p.name
                                                  for p in root.iterdir()))
            self.assertIn("taxjson.toml.bak", out)


if __name__ == "__main__":
    unittest.main()
