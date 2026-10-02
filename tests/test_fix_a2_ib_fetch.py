"""Re-audit-2 fixes for the `taxjson fetch` helpers (list parsers-ib-02).

- A2-0084 / A2-0598: a fetched Questrade file (qt_to_csv) and the
  re-fetch union-merge (_merge_csv_text) keep the API's row order
  within a day — a same-day sale listed before its rebuy is booked
  from the shares held before it (CA-DATE-14 / US-DATE-13).
- A2-0256 / A2-1040: the overlap check and --trim-overlap read a UTF-16
  or UTF-8-BOM Questrade sibling the way the parser does.
- A2-0083: the Flex replace guard takes the download's span from its
  Statement Period / activity dates, never from digits inside a number.
- A2-0599: Questrade activity windows ask from local (America/Toronto)
  midnight, EDT in summer.

All data synthetic (fake account ids, invented tickers).
"""
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _act(day, action, qty, price, sym="XEI.TO"):
    gross = -qty * price
    return {"tradeDate": f"{day}T00:00:00.000000-04:00",
            "transactionDate": f"{day}T00:00:00.000000-04:00",
            "settlementDate": f"{day}T00:00:00.000000-04:00",
            "action": action, "symbol": sym, "symbolId": 1,
            "description": "ISHARES SP TSX COMP HIGH DIV",
            "currency": "CAD", "quantity": qty, "price": price,
            "grossAmount": gross, "commission": 0.0,
            "netAmount": gross, "type": "Trades"}


# API order: buy in January; on Jun 10 a sale of the 100 held, then a
# rebuy of 100 (Questrade stamps every row at midnight).
_API = [_act("2025-01-10", "Buy", 100, 10.0),
        _act("2025-06-10", "Sell", -100, 15.0),
        _act("2025-06-10", "Buy", 100, 15.10)]


def _actions(text):
    rows = list(csv.reader(io.StringIO(text)))
    return [(r[0][:10], r[2]) for r in rows[1:] if r]


class TestFetchKeepsApiOrder(unittest.TestCase):
    """A2-0084 / A2-0598."""

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_qt_to_csv_keeps_same_day_api_order(self):
        from taxjson.bin.taxjson_fetch import qt_to_csv
        self.assertEqual(_actions(qt_to_csv(_API, "1")),
                         [("2025-01-10", "Buy"), ("2025-06-10", "Sell"),
                          ("2025-06-10", "Buy")])

    def test_qt_to_csv_still_chronological_across_days(self):
        from taxjson.bin.taxjson_fetch import qt_to_csv
        text = qt_to_csv([_API[1], _API[2], _API[0]], "1")
        self.assertEqual(_actions(text),
                         [("2025-01-10", "Buy"), ("2025-06-10", "Sell"),
                          ("2025-06-10", "Buy")])
        # byte-stable: the same download renders the same file
        self.assertEqual(text, qt_to_csv([_API[1], _API[2], _API[0]], "1"))

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_merge_keeps_api_order_of_new_rows(self):
        from taxjson.bin.taxjson_fetch import qt_to_csv
        from taxjson.bin.taxjson_run import _merge_csv_text
        existing = qt_to_csv(_API[:1], "1")
        merged, added = _merge_csv_text(existing, qt_to_csv(_API, "1"))
        self.assertEqual(added, 2)
        self.assertEqual(_actions(merged),
                         [("2025-01-10", "Buy"), ("2025-06-10", "Sell"),
                          ("2025-06-10", "Buy")])

    def test_merge_reorders_a_file_written_buy_first(self):
        # A file written by the old fetch (Buy sorted before Sell) takes
        # the API's order for the rows the new download covers.
        from taxjson.bin.taxjson_fetch import qt_to_csv
        from taxjson.bin.taxjson_run import _merge_csv_text
        new = qt_to_csv(_API, "1")
        rows = new.splitlines(keepends=True)
        old = rows[0] + rows[1] + rows[3] + rows[2]      # Buy, Buy, Sell
        merged, added = _merge_csv_text(old, new)
        self.assertEqual(added, 0)
        self.assertEqual(merged, new)

    def test_merge_keeps_rows_outside_the_window_chronological(self):
        from taxjson.bin.taxjson_fetch import qt_to_csv
        from taxjson.bin.taxjson_run import _merge_csv_text
        dec = _act("2025-12-01", "Sell", -50, 16.0)
        existing = qt_to_csv([_API[0], dec], "1")
        merged, added = _merge_csv_text(existing, qt_to_csv(_API[1:], "1"))
        self.assertEqual(added, 2)
        self.assertEqual([d for d, _ in _actions(merged)],
                         ["2025-01-10", "2025-06-10", "2025-06-10",
                          "2025-12-01"])

    @rule("CA-DATE-14")
    def test_fetched_file_books_sale_before_rebuy(self):
        # End to end (Canada): the sale is made from the January shares
        # (ACB 1,000 -> gain 500), and the rebuy is the new pool (1,510).
        from taxjson.bin.taxjson_fetch import qt_to_csv
        csv_text = qt_to_csv(_API, "55500001")  # pii-ok
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "questrade_2025.csv").write_text(
                csv_text)
            env = dict(os.environ, TAXJSON_OFFLINE="1")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--no-input"], cwd=REPO_ROOT,
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            doc = json.loads((root / "work" / "margin_gains.json")
                             .read_text())
        gains = [t for t in doc["transactions"] if t.get("symbol") == "XEI.TO"]
        self.assertEqual(len(gains), 1, gains)
        self.assertAlmostEqual(gains[0]["gain"], 500.0, places=2)


if __name__ == "__main__":
    unittest.main()
