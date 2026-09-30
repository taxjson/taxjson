"""Regression pins for the pipeline-area audit fixes (2026-09).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no FX fetch.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
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


def _project(tmp, config=_CONFIG):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(
        _MARGIN_CSV)
    return root


def _realized(root):
    """The margin account's realized gain, from `sum --json`."""
    r = _run_cli(root, "sum", "margin", "--json")
    assert r.returncode == 0, r.stderr + r.stdout
    doc = json.loads(r.stdout)
    return doc


class TestTtStemCollision(unittest.TestCase):
    """R1-116: a .tt file whose stem equals a broker group name wrote
    its converted JSON over that broker's parse (both were
    work/<acct>_<name>.json) and every CSV trade vanished, rc 0."""

    def test_tt_named_after_broker_keeps_the_broker_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "inputs" / "margin" / "questrade.tt").write_text(
                "DIVIDEND 2025-03-01 00:00:00 XEI.TO 100 CAD 0.25 25.00\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            work = root / "work"
            parsed = json.loads((work / "margin_questrade.json").read_text())
            rows = parsed.get("transactions", parsed)
            self.assertEqual(
                sorted(t["action"] for t in rows), ["BUYSELL", "BUYSELL"])
            self.assertTrue((work / "margin_tt_questrade.json").exists())
            base = json.loads((work / "margin_base.json").read_text())
            brows = base.get("transactions", base)
            self.assertEqual(
                sorted(t["action"] for t in brows),
                ["BUYSELL", "BUYSELL", "DIVIDEND"])
            summ = (root / "reports" / "margin.sum").read_text()
            self.assertIn("480.10", summ)

    def test_check_dates_reads_the_namespaced_tt_output(self):
        from taxjson.lib.check_dates import sources
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "m_sources.list").write_text(
                "questrade/q.csv\ntt/questrade.tt\n")
            got = {(k, str(p.name)) for k, _l, p in sources(cache, "m")}
        self.assertIn(("tt", "m_tt_questrade.json"), got)
        self.assertIn(("broker", "m_questrade.json"), got)


class TestAccountTypeCheckedEverywhere(unittest.TestCase):
    """R1-268: validate_config ran only in `taxjson run`. An account
    type edited after the run ("Taxable") matched neither partition, so
    estimate / instalments / sum / form-export / close-year / carryover
    dropped the account with rc 0 — close-year even locked the wrong
    total. Every command that reads the config must refuse it."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _project(cls._td.name)
        r = _run_cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr + r.stdout
        cfg = cls.root / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace('type = "taxable"',
                                               'type = "Taxable"'))

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _refused(self, *args):
        r = _run_cli(self.root, *args)
        self.assertNotEqual(r.returncode, 0, (args, r.stdout))
        self.assertIn("[accounts.margin] type must be", r.stderr, args)
        self.assertIn("'Taxable'", r.stderr, args)

    def test_filing_commands_refuse_an_invalid_type(self):
        for args in (("estimate",), ("instalments",), ("sum",),
                     ("sum", "margin"), ("form-export",),
                     ("carryover",), ("close-year",), ("checklist",),
                     ("t1135",)):
            with self.subTest(args=args):
                self._refused(*args)
        self.assertFalse((self.root / "filed").exists()
                         and any((self.root / "filed").iterdir()))

    def test_web_context_refuses_an_invalid_type(self):
        try:
            from taxjson.web.context import ProjectContext
        except ImportError:
            self.skipTest("web extras not installed")
        with self.assertRaises(ValueError) as cm:
            ProjectContext.load(self.root)
        self.assertIn("'Taxable'", str(cm.exception))

    def test_untyped_account_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            (root / "taxjson.toml").write_text(
                _CONFIG.replace('type = "taxable"\n', ""))
            r = _run_cli(root, "estimate")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("has no `type`", r.stderr)

    def test_valid_config_stays_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _run_cli(root, "tax-logic")
        self.assertNotIn("[accounts.", r.stderr)


class TestSpreadsheetInputsRefused(unittest.TestCase):
    """R1-64 / R1-248: an .xlsx export in inputs/<account>/ was never
    read (only .csv/.tt are), run exited 0 without naming it, and the
    checklist counted it as present activity."""

    def test_unconverted_xlsx_stops_the_run_naming_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "inputs" / "margin" / "Activity_2025.xlsx").write_bytes(
                b"PK\x03\x04 not really a workbook")
            r = _run_cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("inputs/margin/Activity_2025.xlsx", r.stderr)
        self.assertIn("taxjson-xlsx-to-csv", r.stderr)

    def test_xlsx_next_to_its_converted_csv_only_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "inputs" / "margin" / "questrade_2025.xlsx").write_bytes(
                b"PK\x03\x04")
            r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("questrade_2025.xlsx", r.stderr)
        self.assertIn("questrade_2025.csv", r.stderr)

    def test_xlsx_only_folder_without_account_is_named(self):
        from taxjson.bin.taxjson_run import validate_config
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "inputs"
            (inputs / "margin").mkdir(parents=True)
            (inputs / "tfsa").mkdir()
            (inputs / "tfsa" / "export.xlsx").write_bytes(b"PK")
            warns = validate_config(
                {"settings": {}, "accounts": {"margin": {"type": "taxable"}}},
                inputs)
        self.assertTrue(any("inputs/tfsa/" in w for w in warns), warns)

    def test_checklist_does_not_count_a_spreadsheet_as_activity(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "export.xlsx").write_bytes(b"PK")
            (d / "notes.txt").write_text("x")
            self.assertEqual(cl._data_files(d), [])
            (d / "q.csv").write_text("x")
            self.assertEqual([p.name for p in cl._data_files(d)], ["q.csv"])

    def test_checklist_run_clean_flags_an_unread_spreadsheet(self):
        from datetime import date as _date
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            (root / "inputs" / "margin" / "Activity_2025.xlsx").write_bytes(
                b"PK")
            cfg = {"accounts": {"margin": {"type": "taxable"}}}
            ctx = cl.Ctx(root=root, cfg=cfg, year=2025, today=_date.today(),
                         run_sub=None)
            res = cl.d_run_clean(ctx)
        self.assertEqual(res.status, "attention")
        self.assertIn("Activity_2025.xlsx", res.detail)


_CRYPTO_CONFIG = _CONFIG + """
[accounts.crypto]
type = "taxable"
crypto = true
"""


class TestZeroTransactionParse(unittest.TestCase):
    """R1-247: a non-empty export that parses to 0 transactions (a
    renamed header, a kr_-named file that is not a Kraken ledger) only
    warned in the .sum; run and run --strict exited 0 and the checklist
    called the run clean."""

    def _proj(self, tmp):
        root = _project(tmp, _CRYPTO_CONFIG)
        (root / "inputs" / "crypto").mkdir()
        (root / "inputs" / "crypto" / "kr_2025.csv").write_text(
            "time,asset,amount,note\n2025-01-01 00:00:00,BTC,0.1,x\n")
        return root

    def test_default_run_warns_loudly_on_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            r = _run_cli(root, "run", "--no-input")
            summ = (root / "reports" / "crypto.sum").read_text()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kr_2025.csv parsed to 0 transactions", r.stderr)
        self.assertIn("NONE of its rows are in the books", r.stderr)
        self.assertIn("kr_2025.csv parsed to 0 transactions", summ)

    def test_strict_run_fails_even_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            r = _run_cli(root, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("kr_2025.csv", r.stderr)
            self.assertIn("--strict", r.stderr)
            # The parse is cached now: the gate must still fire.
            r2 = _run_cli(root, "run", "--no-input", "--strict", "--fast")
            self.assertNotEqual(r2.returncode, 0, r2.stdout)
            self.assertIn("kr_2025.csv", r2.stderr)

    def test_checklist_run_clean_needs_attention(self):
        from datetime import date as _date
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            cfg = {"accounts": {"margin": {"type": "taxable"},
                                "crypto": {"type": "taxable",
                                           "crypto": True}}}
            ctx = cl.Ctx(root=root, cfg=cfg, year=2025, today=_date.today(),
                         run_sub=None)
            res = cl.d_run_clean(ctx)
        self.assertEqual(res.status, "attention")
        self.assertIn("0 transactions", res.detail)


if __name__ == "__main__":
    unittest.main()
