"""GitHub issues #5, #6 and #8: input contracts of the JSON loaders.

#5  An unpadded date ("2025-2-01") was accepted, but the engines order
    rows by comparing date STRINGS: the row sorted after "2025-10-01",
    which changed FIFO/ACB gains and turned a long sale into a short
    cover. Every date field is now written YYYY-MM-DD at coercion; a
    value that is not a real calendar date in that shape is refused.
#6  A book object without a "transactions" key ("Transactions") was read
    as an empty book: empty gains at exit 0.
#8  A NaN gain in a gains file gave a successful form export with NaN in
    its JSON.

All data is synthetic.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import (CanadaTaxRules, USATaxRules,
                              coerce_transaction_row, load_transactions)
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _row(date, qty, net, sym="SYNTH.US", cur="USD", **kw):
    return dict(action="BUYSELL", date=date, date_settle=date, symbol=sym,
                currency=cur, quantity=qty, net_amount=net, account="T1",
                **kw)


def _coerce(rows):
    return [coerce_transaction_row(r, i, "test") for i, r in enumerate(rows)]


def _gains(engine, txs):
    with contextlib.redirect_stderr(io.StringIO()):
        return engine().compute_gains(txs, detect_wash_sales=False)


def _disp(result):
    return [(t["date"], t.get("direction"), round(t["cost"], 6),
             round(t["gain"], 6)) for t in result["transactions"]]


def _cli(module, *args, stdin=None):
    return subprocess.run([sys.executable, "-m", module, *args],
                          capture_output=True, text=True, input=stdin,
                          stdin=None if stdin is not None
                          else subprocess.DEVNULL)


class TestIssue5DatesCanonical(unittest.TestCase):

    def _three(self, first):
        return [_row(first, 1, -100), _row("2025-10-01", 1, -200),
                _row("2025-11-01", -1, 120)]

    def test_three_row_repro_both_engines(self):
        for engine in (USATaxRules, CanadaTaxRules):
            with self.subTest(engine=engine.__name__):
                loose = _gains(engine, _coerce(self._three("2025-2-01")))
                tight = _gains(engine, _coerce(self._three("2025-02-01")))
                self.assertEqual(_disp(loose), _disp(tight))
        us = _gains(USATaxRules, _coerce(self._three("2025-2-01")))
        # FIFO: the February share (cost 100) is the one sold.
        self.assertEqual(_disp(us), [("2025-11-01", "LONG", 100.0, 20.0)])

    def test_two_row_long_sale_is_not_a_short_cover(self):
        # "2025-10-01" < "2025-2-01" as strings: the sale sorted first and
        # opened a short that the February buy then covered.
        for engine in (USATaxRules, CanadaTaxRules):
            with self.subTest(engine=engine.__name__):
                r = _gains(engine, _coerce([_row("2025-2-01", 1, -100),
                                            _row("2025-10-01", -1, 130)]))
                self.assertEqual(_disp(r),
                                 [("2025-10-01", "LONG", 100.0, 30.0)])

    def test_every_date_field_is_written_canonically(self):
        row = _row("2025-2-1", 1, -100, lot_date="2024-3-9",
                   record_date="2025-1-31", ex_date="2025-1-30",
                   time="9:30")
        row["date_settle"] = "2025-2-4"
        t = coerce_transaction_row(row, 0, "test")
        self.assertEqual(
            (t.date, t.date_settle, t.lot_date, t.record_date, t.ex_date,
             t.time),
            ("2025-02-01", "2025-02-04", "2024-03-09", "2025-01-31",
             "2025-01-30", "09:30:00"))

    def test_invalid_dates_are_refused(self):
        bad = ("2025-02-30", "2025-13-01", "2025/02/01", "25-2-1",
               "2025-02-01T10:00", "2025-2", "Feb 1 2025", "2025-0-10")
        for fld in ("date", "date_settle", "lot_date", "record_date",
                    "ex_date"):
            for v in bad:
                with self.subTest(field=fld, value=v):
                    row = _row("2025-02-01", 1, -100)
                    row[fld] = v
                    with self.assertRaises(ValueError) as cm:
                        coerce_transaction_row(row, 0, "test")
                    self.assertIn(fld, str(cm.exception))
                    self.assertIn("YYYY-MM-DD", str(cm.exception))

    def test_file_and_stdin_loaders_normalise(self):
        from taxjson.lib.pipeline import load_stdin_transactions
        doc = {"transactions": [_row("2025-2-01", 1, -100)]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "book.json"
            p.write_text(json.dumps(doc))
            self.assertEqual(load_transactions(p)[0].date, "2025-02-01")
        txs = load_stdin_transactions(io.StringIO(json.dumps(doc)))
        self.assertEqual(txs[0].date_settle, "2025-02-01")

    def test_gains_cli_orders_an_unpadded_book_like_a_padded_one(self):
        outs = []
        with tempfile.TemporaryDirectory() as tmp:
            for first in ("2025-2-01", "2025-02-01"):
                p = Path(tmp) / f"book{len(outs)}.json"
                p.write_text(json.dumps(
                    {"transactions": self._three(first)}))
                r = _cli("taxjson.bin.taxjson_gains", str(p),
                         "--country", "usa", "--no-wash")
                self.assertEqual(r.returncode, 0, r.stderr)
                outs.append(json.loads(r.stdout)["summary"])
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
