"""Fix round 7 (wave B) of 2026-10-29, the handoff group: the year
handoff's straddler and double checks, the deleted prior-year lock, the
all-history selection recompute and the journals report (synthetic
data only).

Formerly: test_fix_1029_r7b_handoff"""
import csv
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj


def _project(root, year, accounts=("margin",), prior=None):
    root.mkdir(parents=True, exist_ok=True)
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    if prior:
        head += f'prior_year_record = "{prior}"\n'
    (root / "taxjson.toml").write_text(head + "".join(
        f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


def _rbc_row(day, action, q, cur, desc, price="", value=0):
    return [day, action, "SYN", "SYNTHETIC FUND", q, price, day, "99900001",
            value, cur, desc]


class TestJournalsSameDay(unittest.TestCase):
    """TJ-0850: two same-day, same-size RBC journals with distinct J~
    references are two journals in `tjs journals`.

    Formerly: test_fix_1029_r7b_handoff.TestJournalsSameDay"""

    def _run(self, second_day):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, ("a",))
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]'))
            rows = [["Date", "Activity", "Symbol", "Symbol Description",
                     "Quantity", "Price", "Settlement Date", "Account",
                     "Value", "Currency", "Description"],
                    _rbc_row("March 11, 2025", "Buy", 40, "USD",
                             "SYNTHETIC FUND UNSOLICITED DA", 10, -400)]
            for n, day in enumerate(("May 12, 2025", second_day)):
                ref = f"J~0TFR{n + 1}"
                rows += [_rbc_row(day, "Transfers", -10, "USD",
                                  f"TFR - SYNTHETIC FUND TRANSFER TO C$  "
                                  f"{ref}"),
                         _rbc_row(day, "Transfers", 10, "CAD",
                                  f"TFR - SYNTHETIC FUND TRANSFER FROM U$  "
                                  f"{ref}")]
            buf = io.StringIO()
            csv.writer(buf).writerows(rows)
            (p / "inputs/a/rbc.csv").write_text(
                '"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
                + buf.getvalue())
            (p / "work").mkdir()
            d, rates = date(2025, 1, 1), []
            while d <= date(2026, 1, 31):
                rates.append(f"{d} 12:00:00 USD CAD 1.3 boc")
                d += timedelta(days=1)
            (p / "work/to_base.csv").write_text("\n".join(rates) + "\n")
            tj(p, "run", "--no-input")
            return json.loads(tj(p, "journals", "--json").stdout)

    def test_same_day_journals_are_two(self):
        rep = self._run("May 12, 2025")
        self.assertEqual(len(rep["journals"]), 2, rep)
        self.assertEqual(rep["counts"]["joined"], 2, rep)
        self.assertEqual(sum(j["quantity"] for j in rep["journals"]), 20)

    def test_different_days_control(self):
        rep = self._run("May 13, 2025")
        self.assertEqual(len(rep["journals"]), 2, rep)
        self.assertEqual(rep["counts"]["joined"], 2, rep)


if __name__ == "__main__":
    unittest.main()
