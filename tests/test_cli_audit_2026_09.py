"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene."""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent

from taxjson.bin import taxjson_run as R  # noqa: E402


def _tj(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


def _call(fn, **kw):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            fn(argparse.Namespace(**kw))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


_TOML = ('[settings]\nyear = {year}\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         '[accounts.margin]\ntype = "taxable"\n'
         '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')

_GAINS = {"summary": {"year": "2024", "total_gain": 100.0},
          "transactions": [{"action": "BUYSELL", "symbol": "AAA.TO",
                            "date": "2024-03-01",
                            "date_settle": "2024-03-04",
                            "quantity": -10, "qty": 10, "gain": 100.0,
                            "proceeds": 1100.0, "cost": 1000.0,
                            "id": "abc", "account": "margin"}]}


def _project(td, year=2024, gains_year="2024"):
    root = Path(td)
    (root / "taxjson.toml").write_text(_TOML.format(year=year))
    (root / "work").mkdir()
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "crypto").mkdir(parents=True)
    g = json.loads(json.dumps(_GAINS))
    g["summary"]["year"] = gains_year
    (root / "work" / "margin_gains.json").write_text(json.dumps(g))
    return root


class TestWrongYearLock(unittest.TestCase):          # B2
    def test_close_year_refuses_books_of_another_year(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, year=2025, gains_year="2024")
            code, _o, _e = _call(R.cmd_close_year, dir=str(root),
                                 year=None, force=False)
            self.assertIn("rebuild with `taxjson run` first", str(code))
            self.assertIn("margin: 2024", str(code))
            self.assertFalse((root / "filed").exists())

    def test_sum_warns_loudly_on_year_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, year=2025, gains_year="2024")
            code, out, err = _call(R.cmd_summary, dir=str(root),
                                   account=None, json=False)
            self.assertIn("WARNING: [settings].year is 2025", err)
            self.assertIn("margin (2024)", err)

    def test_matching_year_is_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            self.assertEqual(R._artifact_year_mismatch(
                {"margin": root / "work" / "margin_gains.json"}, 2024), {})


class TestUntypedAccountFatal(unittest.TestCase):    # B3
    def test_missing_type_dies_listing_the_choices(self):
        with self.assertRaises(SystemExit) as cm:
            R.validate_config({"settings": {"year": 2025,
                                            "country": "canada"},
                               "accounts": {"margin": {}}})
        self.assertIn("taxable | sheltered", str(cm.exception))


class TestInputsFolders(unittest.TestCase):          # B6 + subfolders
    def _cfg(self):
        return {"settings": {"year": 2025, "country": "canada"},
                "accounts": {"margin": {"type": "taxable"}}}

    def test_slips_folder_is_not_an_orphan_account(self):
        with tempfile.TemporaryDirectory() as td:
            inputs = Path(td) / "inputs"
            (inputs / "slips").mkdir(parents=True)
            (inputs / "slips" / "t5008.csv").write_text("a,b\n")
            self.assertEqual(R.validate_config(self._cfg(), inputs), [])

    def test_csv_in_account_subfolder_warns(self):
        with tempfile.TemporaryDirectory() as td:
            inputs = Path(td) / "inputs"
            (inputs / "margin" / "2025").mkdir(parents=True)
            (inputs / "margin" / "2025" / "q.csv").write_text("a,b\n")
            w = R.validate_config(self._cfg(), inputs)
        self.assertTrue(any("inputs/margin/2025/" in m and "NOT read" in m
                            for m in w), w)


class TestSkippedAccounts(unittest.TestCase):        # B7
    def test_record_and_read_back(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "crypto").mkdir(parents=True)
            (root / "inputs" / "rrsp").mkdir(parents=True)
            R._record_skipped_accounts(root / "work", ["crypto", "rrsp"])
            self.assertEqual(R._accounts_skipped_for_no_inputs(root),
                             {"crypto", "rrsp"})
            # Files added since the run: the hint IS needed again.
            (root / "inputs" / "rrsp" / "x.csv").write_text("a\n")
            self.assertEqual(R._accounts_skipped_for_no_inputs(root),
                             {"crypto"})
            # A single-account run only updates its own entry.
            R._record_skipped_accounts(root / "work", [], only="crypto")
            doc = json.loads((root / "work" / "skipped_accounts.json")
                             .read_text())
            self.assertEqual(doc["accounts"], ["rrsp"])

    def test_form_export_argv_quiet_for_skipped_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            R._record_skipped_accounts(root / "work", ["crypto"])
            err = io.StringIO()
            with redirect_stderr(err):
                argv = R._taxable_gains_argv(root, root / "work")
            self.assertEqual(len(argv), 2)
            self.assertNotIn("crypto", err.getvalue())

    def test_unskipped_missing_account_still_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            err = io.StringIO()
            with redirect_stderr(err):
                R._taxable_gains_argv(root, root / "work")
            self.assertIn("'crypto'", err.getvalue())


class TestRunEmptyProject(unittest.TestCase):        # B21
    def test_strict_empty_project_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_TOML.format(year=2024))
            (root / "inputs" / "margin").mkdir(parents=True)
            r = _tj(root, "run", "--strict")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("no account had any input", r.stderr)
            self.assertNotIn("Done.", r.stdout)
            r = _tj(root, "run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no account had any input", r.stderr)
            doc = json.loads((root / "work" / "skipped_accounts.json")
                             .read_text())
            self.assertEqual(doc["accounts"], ["crypto", "margin"])

    def test_nonexistent_dir_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(Path(td) / "nope", "sum")
        # exit 2: a usage error, not a finding (re-audit A2-0164)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such directory", r.stderr)

    def test_typo_warning_precedes_missing_year(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                _TOML.format(year=2024).replace("year = 2024",
                                                "yeer = 2024"))
            r = _tj(root, "run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did you mean 'year'", r.stderr)
        self.assertLess(r.stderr.index("did you mean"),
                        r.stderr.index("missing [settings] year"))


class TestInit(unittest.TestCase):                   # B8 + polish
    def test_next_steps_put_dash_c_first_and_quote(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "my taxes"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "init",
                 str(target), "--country", "ca", "--year", "2025"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"taxjson -C '{target.resolve()}' run", r.stdout)

    def test_far_future_year_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "init",
                 td, "--country", "ca", "--year", "2099"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a plausible tax year", r.stderr)

    def test_force_backs_up_and_lists_orphans(self):
        with tempfile.TemporaryDirectory() as td:
            base = [sys.executable, "-m", "taxjson.bin.taxjson_run",
                    "init", td, "--year", "2025"]
            subprocess.run(base + ["--country", "ca"], cwd=REPO_ROOT,
                           capture_output=True, check=True)
            r = subprocess.run(base + ["--country", "us", "--force"],
                               cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            bak = (Path(td) / "taxjson.toml.bak").read_text()
            self.assertIn('country           = "canada"', bak)
            self.assertIn("tfsa", r.stdout)
            self.assertIn("no [accounts.*] section", r.stdout)


class TestElect(unittest.TestCase):                  # B12 + polish
    def _root(self, td):
        root = _project(td)
        return root

    def test_pending_json_when_nothing_pending(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "elect", "--pending", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout),
                         {"schema_version": 1, "accounts": {}})

    def test_account_listing_json(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "elect", "margin", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"accounts": {}})

    def test_hint_without_set_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "elect", "margin", "--hint", "a=1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--hint only applies with --set", r.stderr)


class TestAudit(unittest.TestCase):                  # B15
    def test_unique_prefix_len(self):
        from taxjson.bin.taxjson_audit import unique_prefix_len
        self.assertEqual(unique_prefix_len(["abcdefghij1", "zz"]), 10)
        self.assertEqual(unique_prefix_len(
            ["abcdefghijKL1", "abcdefghijKL2"]), 13)

    def test_unknown_account_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            code, _o, _e = _call(R.cmd_audit, dir=str(root), account="nope",
                                 year=None, all_years=False)
        self.assertIn("not a taxable account", str(code))


class TestFindMissingHistoryCountry(unittest.TestCase):   # B16
    def test_write_missing_history_passes_country(self):
        seen = []

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake(cmd, **kw):
            seen.append(cmd)
            Path(cmd[cmd.index("--suggest-missing-history") + 1]
                 ).write_text("[]")
            return _P()

        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "taxjson.toml").write_text(
                _TOML.format(year=2024).replace('"canada"', '"us"')
                .replace('"CAD"', '"USD"'))
            (root / "work" / "margin_base.json").write_text("{}")
            with mock.patch("taxjson.lib.dispatch.run_cmd", fake):
                _call(R.cmd_find_missing_history, dir=str(root),
                      account=None, year=None, include_options=False,
                      write_missing_history=str(Path(td) / "mh.json"),
                      all_history=False)
        self.assertTrue(seen)
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index("--country") + 1], "usa")
        self.assertEqual(cmd[cmd.index("--year") + 1], "2024")


class TestFormExportArgs(unittest.TestCase):         # B19
    def test_out_without_txf_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            code, out, _e = _call(R.cmd_form_export, dir=str(root),
                                  form=None, out="x.txf", box=None,
                                  csv=None, json=False)
        self.assertIn("--out only applies to --form txf", str(code))
        self.assertEqual(out, "")


class TestEstimateProvinceFirst(unittest.TestCase):  # B20
    def test_missing_province_fails_before_any_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            code, out, _e = _call(R.cmd_summary, dir=str(root),
                                  account=None, json=False, estimate=True,
                                  other_income=None, other_losses=None,
                                  province=None, verbose=False)
        self.assertIn("needs a province", str(code))
        self.assertEqual(out, "")

    def test_unsupported_province_fails_before_any_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            code, out, _e = _call(R.cmd_summary, dir=str(root),
                                  account=None, json=False, estimate=True,
                                  other_income=None, other_losses=None,
                                  province="QC", verbose=False)
        self.assertIn("unsupported province 'QC'", str(code))
        self.assertEqual(out, "")

    def test_estimate_error_names_the_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_TOML.format(year=2024))
            r = _tj(root, "estimate")
        self.assertIn("taxjson estimate: no gains files", r.stderr)

    def test_province_without_estimate_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            _c, _o, err = _call(R.cmd_summary, dir=str(root), account=None,
                                json=False, province="ON")
        self.assertIn("--province is ignored", err)


class TestNoFalseCleanBeforeRun(unittest.TestCase):  # B22
    def _root(self, td):
        root = Path(td)
        (root / "taxjson.toml").write_text(_TOML.format(year=2024))
        (root / "work").mkdir()
        return root

    def test_scan_without_holdings_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "scan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("run `taxjson run`", r.stderr)
        self.assertNotIn("clean scan", r.stdout)

    def test_leaps_without_books_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            for sub in ("leaps", "leaps-sum"):
                r = _tj(root, sub)
                self.assertEqual(r.returncode, 1, sub)
                self.assertIn(f"taxjson {sub}: no gains files", r.stderr)


if __name__ == "__main__":
    unittest.main()
