"""Backlog fix round 6 of 2026-10-29, group `drafts`: the purchase drafts
of the standalone taxjson-missing-history (outside a project).

TJ-0711  `taxjson-missing-history BOOK --country usa --write-purchases
         FILE` outside a project dated an IB lot on its settlement date:
         with no taxjson.toml it fell back to the settle basis before
         --country was read, and TJ-0419's lot dating converts IB's
         trade date under settle. A US lot keeps IB's trade date, the
         start of its holding period (US tax_date trade): the adopted
         draft turned a long-term gain short-term. --country now sets
         the date basis there, as a project's country does; Canada's
         drafts keep the settlement date.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
a made-up ticker (SYN), fake account ids marked pii-ok, amounts under
1,000.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import settings_for
from test_basis_drafts import _lot, _parsed, _sell
from test_fix_ibparse import HEAD, TRADES_H

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_ENV = {"PYTHONPATH": str(SRC), "TAXJSON_OFFLINE": "1",
        "TAXJSON_WIDTH": "0"}

# A sale of 10 SYN on 2025-06-20 closing one lot IB opened 2024-06-18
# (a Tuesday: T+1 settles it 2024-06-20), 367 days before the sale.
_BODY = (_sell("SYN", "2025-06-20, 10:00:00", -10, 20, 100)
         + _lot("SYN", "2024-06-18", 10, 10, 100))


def _flat(text: str) -> str:
    return " ".join(text.split())


def _tool(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_missing_history",
                           [str(a) for a in args]),
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=dict(os.environ, **_ENV), timeout=120)


def _tjs(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *map(str, args)]),
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=dict(os.environ, **_ENV), timeout=300)


def _book(folder: Path, body: str = _BODY) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    book = folder / "margin_base.json"
    book.write_text(json.dumps({"transactions": _parsed(body)}))
    return book


def _buysell(text: str):
    return [ln.split() for ln in text.splitlines()
            if ln.startswith("BUYSELL")]


# ---- TJ-0711: a standalone US draft keeps IB's trade date ----------------
class TestTJ0711StandaloneUsDraftDate(unittest.TestCase):
    """TJ-0711: outside a project, `--country usa --write-purchases`
    wrote the lot on 2024-06-20 (its settlement date) where the same
    command in a US project writes IB's 2024-06-18."""

    def _standalone(self, td, country, *more):
        book = _book(Path(td) / "loose")
        dest = Path(td) / f"{country}.txt"
        r = _tool(book, "--country", country, *more,
                  "--write-purchases", dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r, dest.read_text()

    @rule("US-BASIS-08")
    def test_usa_keeps_ibs_trade_date_as_a_us_project_does(self):
        with tempfile.TemporaryDirectory() as td:
            r, text = self._standalone(td, "usa", "--year", "2025")
            line, = _buysell(text)
            self.assertEqual(line[1], "2024-06-18")
            self.assertNotIn("the lot's settlement date", text)
            self.assertNotIn("tax_date settle", text)
            self.assertIn("opened 2024-06-18", text)
            err = _flat(r.stderr)
            self.assertIn("no taxjson.toml beside the input — a row's "
                          "tax year is taken from its TRADE date, the "
                          "default tax_date of --country usa", err)
            self.assertNotIn("SETTLEMENT date", err)
            # The control: the same book in a US project (its country's
            # tax_date, trade) writes the same line.
            root = Path(td) / "us"
            root.mkdir()
            (root / "taxjson.toml").write_text(
                settings_for("usa", year=2025)
                + '[accounts.margin]\ntype = "taxable"\n')
            book = _book(root / "work")
            control = root / "control.txt"
            c = _tool(book, "--write-purchases", control)
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            self.assertEqual(_buysell(control.read_text()), [line])

    @rule("CA-ACB-15")
    def test_canada_keeps_the_settlement_date(self):
        # TJ-0419's dating, unchanged outside a project.
        with tempfile.TemporaryDirectory() as td:
            r, text = self._standalone(td, "canada", "--year", "2025")
            line, = _buysell(text)
            self.assertEqual(line[1], "2024-06-20")
            self.assertIn("dated 2024-06-20, the lot's settlement date",
                          _flat(text))
            self.assertIn("(its settlement date: tax_date settle)", text)
            self.assertIn("a row's tax year is taken from its SETTLEMENT "
                          "date, the default tax_date of --country "
                          "canada", _flat(r.stderr))

    @rule("US-BASIS-08")
    @rule("CA-ACB-15")
    def test_the_year_scope_follows_the_countrys_basis(self):
        # A sale traded 2024-12-31 settles 2025-01-02: a 2024 sale in
        # the US (trade date), a 2025 one in Canada (settlement date).
        body = (_sell("SYN", "2024-12-31, 10:00:00", -10, 20, 100)
                + _lot("SYN", "2024-03-11", 10, 10, 100))
        with tempfile.TemporaryDirectory() as td:
            book = _book(Path(td) / "loose", body)
            for country, year, drafted in (("usa", 2024, True),
                                           ("usa", 2025, False),
                                           ("canada", 2024, False),
                                           ("canada", 2025, True)):
                dest = Path(td) / f"{country}{year}.txt"
                r = _tool(book, "--country", country, "--year", year,
                          "--write-purchases", dest)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(dest.exists(), drafted,
                                 (country, year, r.stdout))
                if not drafted:
                    self.assertIn(f"No purchase to draft with a sale in "
                                  f"{year}", _flat(r.stdout))

    def test_no_country_is_still_refused_and_the_report_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            book = _book(Path(td) / "loose")
            r = _tool(book, "--year", "2025", "--write-purchases",
                      Path(td) / "x.txt")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("--write-purchases needs the project's country",
                          _flat(r.stderr))
            # The report (no --write-purchases) reads no --country: the
            # settle fallback, said as before.
            r = _tool(book, "--country", "usa", "--year", "2025")
            self.assertIn("a row's tax year is taken from its SETTLEMENT "
                          "date (run it on a project's work/ files",
                          _flat(r.stderr))

    @rule("US-BASIS-08")
    def test_the_kept_draft_is_long_term(self):
        # The audit's impact: the adopted standalone draft in a US
        # project with the 2025-06-20 sale, 367 days after IB's lot.
        with tempfile.TemporaryDirectory() as td:
            _, text = self._standalone(td, "usa", "--year", "2025")
            root = Path(td) / "applied"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                settings_for("usa", year=2025)
                + '[accounts.margin]\ntype = "taxable"\n')
            (root / "ticker.map").write_text("")
            (root / "inputs" / "margin" / "ib.csv").write_text(
                (HEAD + TRADES_H + _BODY).replace("U5550001",   # pii-ok
                                                  "U1234567"))  # pii-ok
            (root / "inputs" / "margin" / "purchases.tt").write_text(text)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])
            gains = json.loads(
                (root / "work" / "margin_gains_wash.json").read_text())
            sale, = gains["transactions"]
            self.assertEqual(sale["acquired_date"], "2024-06-18")
            self.assertEqual(sale["days_held"], 367)
            self.assertEqual(sale["term"], "LONG_TERM")


if __name__ == "__main__":
    unittest.main()
