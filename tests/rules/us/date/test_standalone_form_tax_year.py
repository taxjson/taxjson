"""Backlog fix round 7 (wave B) of 2026-10-29, group `forms`: form
export, carryover and the cash view.

TJ-0895  a commission-only disposal of unknown-cost units: its manual
         row keeps net proceeds -2, never "proceeds 2".
TJ-0896  US carryover: a loss still carried after the last year with a
         sale takes each later year's 3,000 up to the project year.
TJ-0898  a closed year's T5 box 18 capital-gains dividends stay in the
         carry-back room the next year's carryover offers.
TJ-0901  the standalone Form 8949 takes its year from the gains files
         (or the trade date), not a December trade's January settlement.
TJ-0904  taxjson-carryover --filed-lock YEAR=PATH refuses a lock of
         another year than YEAR.
TJ-0905  the standalone form export reads a gains file named twice once.
TJ-0889  `tjs cash`: a copied line keeps its book (at=alpha), so the
         copy reads into the book the original did.
TJ-0693  `tjs cash`: a stock-lending receipt is a #CREDIT line and an
         other credit, never a fee paid.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).

Formerly: test_fix_1029_r7b_forms
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from _support.filing_forms_project import _env, _project, _run


def _module(mod, *args, cwd=None):
    """A standalone tool (`taxjson-form-export` ...) in a child process."""
    return subprocess.run(python_module_argv(mod) + [str(a) for a in args],
                          capture_output=True, text=True, env=_env(),
                          cwd=cwd, stdin=subprocess.DEVNULL, timeout=600)


# A synthetic Interactive Brokers activity statement: 10 SYN bought
# 2024-01-03, sold 2024-12-31 (settling 2025-01-02).
IB_DEC31 = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n"
    "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
    "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
    "Realized P/L,MTM P/L,Code\n"
    'Trades,Data,Order,Stocks,USD,SYN,"2024-01-03, 10:00:00",10,10,10,'
    "-100,0,100,0,0,O\n"
    'Trades,Data,Order,Stocks,USD,SYN,"2024-12-31, 10:00:00",-10,12,12,'
    "120,0,-100,20,0,C\n")


class TestStandaloneFormYear(unittest.TestCase):
    """TJ-0901: a US 2024 gains file whose only sale was traded
    2024-12-31 and settled 2025-01-02. `taxjson-form-export FILE
    --country usa --form 8949 --json` without --year labelled it 2025
    (the settlement's year).

    Formerly: test_fix_1029_r7b_forms.TestStandaloneFormYear"""

    def test_unit_infer_year_on_the_files_basis(self):
        from taxjson.lib.tax_forms import _infer_year
        rows = [{"date": "2024-12-31", "date_settle": "2025-01-02"}]
        self.assertEqual(_infer_year(rows, "date"), 2024)
        self.assertEqual(_infer_year(rows, "date_settle"), 2025)
        self.assertEqual(_infer_year(rows), 2025)

    def test_standalone_8949_year(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p", country="usa", year=2024)
            (root / "inputs" / "margin" / "ib.csv").write_text(IB_DEC31)
            _run(self, root)
            f = root / "work" / "margin_gains_wash.json"
            if not f.exists():
                f = root / "work" / "margin_gains.json"
            sale = [t for t in json.loads(f.read_text())["transactions"]
                    if t.get("gain") is not None]
            self.assertEqual(sale[0]["date"], "2024-12-31")
            self.assertTrue(sale[0]["date_settle"].startswith("2025"))
            r = _module("taxjson.bin.taxjson_form_export", f, "--country",
                        "usa", "--form", "8949", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["year"], 2024)
            r = _module("taxjson.bin.taxjson_form_export", f, "--country",
                        "usa", "--form", "8949")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("tax year 2024", r.stdout)
            # A file that records no year: the rows' trade dates.
            doc = json.loads(f.read_text())
            doc["summary"].pop("year", None)
            g = Path(d) / "noyear" / "gains.json"
            g.parent.mkdir()
            g.write_text(json.dumps(doc))
            r = _module("taxjson.bin.taxjson_form_export", g, "--country",
                        "usa", "--form", "8949", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["year"], 2024)


if __name__ == "__main__":
    unittest.main()
