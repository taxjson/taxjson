"""GitHub issue #12: an order IB cancels in two parts (two `Ca` rows, in
one statement or across two) is removed in full. The first cancellation
reduces the order; the second, cancelling exactly what is left, used to
be matched against the order's ORIGINAL size (exact) and the reduced one
(partial), so it matched neither and stayed booked as a phantom sale.
Synthetic data only (invented tickers and account ids, pii-ok)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE, pair_cancellations
from tax_rules.dual import gains_both, tx

from _style import CapturedWidth

SRC = Path(__file__).resolve().parents[1] / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _row(qty, price, net, *, date="2025-02-03", ca=False):
    t = {"action": "BUYSELL", "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": "SYNTH.US", "currency": "USD",
         "account": "demo", "quantity": qty, "price": price,
         "net_amount": net}
    if ca:
        t["type"] = TRADE_CANCEL_TYPE
    return t


class TestSuccessivePartialCancellations(unittest.TestCase):

    def test_two_parts_cancel_the_whole_order(self):
        for parts in ((-4, -6), (-6, -4), (-3, -3, -4)):
            with self.subTest(parts=parts):
                order = _row(10, 10, 100)
                cas = [_row(q, 10, -q * 10, ca=True) for q in parts]
                partials = []
                kept, pairs, unmatched = pair_cancellations(
                    [order] + cas, partials=partials)
                self.assertEqual((kept, unmatched), ([], []))
                self.assertEqual(len(partials), len(parts) - 1)
                # The last one pairs with the order itself (gone in full).
                self.assertIs(pairs[0][0], order)
                self.assertIs(pairs[0][1], cas[-1])

    def test_rest_of_an_order_and_a_separate_fill(self):
        # A same-size fill of another time is not the residual's match.
        order = _row(10, 10, 100)
        other = dict(_row(6, 10, 60), time="11:00:00")
        cas = [_row(-4, 10, 40, ca=True), _row(-6, 10, 60, ca=True)]
        kept, pairs, unmatched = pair_cancellations([order, other] + cas)
        self.assertEqual((kept, unmatched), ([other], []))
        self.assertIs(pairs[0][0], order)


class TestExecutionThenWholeOrder(unittest.TestCase):
    """A Ca of one execution, then a Ca of the whole order (order 440:
    cancel 40, then cancel 440): the order is removed in full — the
    second matches neither the residual (400) nor a part of it, and used
    to stay booked as a phantom -440 sale."""

    def test_unit(self):
        order = _row(440, 10, 4400)
        ca40 = _row(-40, 10, 400, ca=True)
        ca440 = _row(-440, 10, 4400, ca=True)
        partials, overlaps = [], []
        kept, pairs, unmatched = pair_cancellations(
            [order, ca40, ca440], partials=partials, overlaps=overlaps)
        self.assertEqual((kept, unmatched), ([], []))
        self.assertEqual(len(partials), 1)
        self.assertEqual(len(pairs), 1)
        self.assertIs(pairs[0][0], order)
        self.assertIs(pairs[0][1], ca440)
        self.assertEqual(len(overlaps), 1)
        self.assertIs(overlaps[0][0], order)
        self.assertEqual(overlaps[0][2]["quantity"], 400)

    def test_two_reduced_orders_stay_unmatched(self):
        # Two reduced originals of the size: no guess, still flagged.
        def at(t, hhmm):
            return dict(t, time=hhmm)
        rows = [at(_row(440, 10, 4400), "10:00:00"),
                at(_row(440, 10, 4400), "11:00:00"),
                at(_row(-40, 10, 400, ca=True), "10:00:00"),
                at(_row(-40, 10, 400, ca=True), "11:00:00"),
                at(_row(-440, 10, 4400, ca=True), "12:00:00")]
        _kept, _pairs, unmatched = pair_cancellations(rows)
        self.assertEqual(len(unmatched), 1)

    def test_merge2_both_countries(self):
        earlier = [_row(100, 5, 500, date="2025-01-02"),
                   _row(440, 10, 4400)]
        later = [_row(-40, 10, 400, ca=True), _row(-440, 10, 4400, ca=True)]
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = _merge2({"earlier.json": earlier,
                             "later.json": later}, country)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("none of this account's inputs", r.stderr)
                self.assertIn("the whole SYNTH.US order of 440", r.stderr)
                rows = json.loads(r.stdout)["transactions"]
                self.assertEqual(
                    [(t["date"], t["quantity"], t["net_amount"])
                     for t in rows], [("2025-01-02", 100.0, 500.0)])
        book = [TaxTransaction(**{k: v for k, v in t.items()
                                  if k in ("action", "date", "date_settle",
                                           "time", "symbol", "currency",
                                           "account", "quantity", "price",
                                           "net_amount")})
                for t in rows]
        res = gains_both(book + [tx("BUYSELL", "2025-03-03", "SYNTH.US",
                                    -100, 700, account="demo")], year=2025)
        for c in ("canada", "usa"):
            # 100 sold for 700 against a basis of 500: no phantom -440.
            self.assertAlmostEqual(res[c]["summary"]["total_gain"], 200.0,
                                   places=6, msg=c)


def _merge2(files, country):
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, rows in files.items():
            p = Path(td) / name
            p.write_text(json.dumps({"transactions": rows}))
            paths.append(str(p))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_merge2", "--sort",
             "--dedup", "--validate", "--country", country, "--year",
             "2025", *paths],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                     TAXJSON_WIDTH="0"))
        return r


class TestAcrossStatements(unittest.TestCase):

    def test_merge2_two_partial_cancels_in_a_later_file(self):
        earlier = [_row(10, 5, 50, date="2025-01-02"), _row(10, 10, 100)]
        later = [_row(-4, 10, 40, ca=True), _row(-6, 10, 60, ca=True)]
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = _merge2({"earlier.json": earlier,
                             "later.json": later}, country)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("none of this account's inputs", r.stderr)
                self.assertIn("dropped the rest (6) of the SYNTH.US trade "
                              "of 10", r.stderr)
                self.assertIn("1 transactions validated", r.stderr)
                rows = json.loads(r.stdout)["transactions"]
                self.assertEqual(
                    [(t["date"], t["quantity"], t["net_amount"])
                     for t in rows], [("2025-01-02", 10.0, 50.0)])
        # Both engines: no gain, and the holding keeps its basis of 50
        # (a sale of the 10 for 70 gains 20).
        book = [TaxTransaction(**{k: v for k, v in t.items()
                                  if k in ("action", "date", "date_settle",
                                           "time", "symbol", "currency",
                                           "account", "quantity", "price",
                                           "net_amount")})
                for t in rows]
        res = gains_both(book, year=2025)
        for c in ("canada", "usa"):
            self.assertAlmostEqual(res[c]["summary"]["total_gain"], 0.0,
                                   places=6, msg=c)
        res = gains_both(book + [tx("BUYSELL", "2025-03-03", "SYNTH.US",
                                    -10, 70, account="demo")], year=2025)
        for c in ("canada", "usa"):
            self.assertAlmostEqual(res[c]["summary"]["total_gain"], 20.0,
                                   places=6, msg=c)


class TestIbParser(unittest.TestCase):

    def setUp(self):
        from test_fix_a2_ib import _booked, _stmt
        from test_fix_ibparse import TRADES_H, _trade
        self._booked, self._stmt = _booked, _stmt
        self.TRADES_H, self._trade = TRADES_H, _trade

    def _cas(self):
        w = '2025-02-03, 10:00:00'
        return (self._trade('QZK', w, -4, 10, 40, code='Ca'),
                self._trade('QZK', w, -6, 10, 60, code='Ca'))

    def test_one_statement_two_partial_cancels(self):
        w = '2025-02-03, 10:00:00'
        rows, err = self._booked({'ib_2025.csv': self._stmt(
            'January 1, 2025', 'December 31, 2025', self.TRADES_H,
            self._trade('QZK', '2025-01-02, 10:00:00', 10, 5, -50),
            self._trade('QZK', w, 10, 10, -100), *self._cas())})
        got = [(t['date'], t['quantity']) for t in rows
               if t['symbol'] == 'QZK.US']
        self.assertEqual(got, [('2025-01-02', 10.0)], err)
        self.assertIn("the rest (6) of the QZK.US trade", err)

    def test_one_statement_execution_then_whole_order(self):
        w = '2025-02-03, 10:00:00'
        rows, err = self._booked({'ib_2025.csv': self._stmt(
            'January 1, 2025', 'December 31, 2025', self.TRADES_H,
            self._trade('QZK', '2025-01-02, 10:00:00', 10, 5, -50),
            self._trade('QZK', w, 440, 10, -4400),
            self._trade('QZK', w, -40, 10, 400, code='Ca'),
            self._trade('QZK', w, -440, 10, 4400, code='Ca'))})
        got = [(t['date'], t['quantity']) for t in rows
               if t['symbol'] == 'QZK.US']
        self.assertEqual(got, [('2025-01-02', 10.0)], err)
        self.assertIn("the whole QZK.US order of 440", err)

    def test_two_statements_then_merge2(self):
        from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
        rows, err = self._booked({
            'ib_2025.csv': self._stmt(
                'January 1, 2025', 'December 31, 2025', self.TRADES_H,
                self._trade('QZK', '2025-01-02, 10:00:00', 10, 5, -50),
                self._trade('QZK', '2025-02-03, 10:00:00', 10, 10, -100)),
            'ib_2026.csv': self._stmt(
                'January 1, 2026', 'March 31, 2026', self.TRADES_H,
                *self._cas())})
        fields = TaxTransaction.__dataclass_fields__
        book = [TaxTransaction(**{k: v for k, v in t.items()
                                  if k in fields}) for t in rows]
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            kept = cancel_trade_pairs(book)
        got = [(t.date, t.quantity) for t in kept if t.symbol == 'QZK.US']
        self.assertEqual(got, [('2025-01-02', 10.0)], err + out.getvalue())
        self.assertNotIn("none of this account's inputs", out.getvalue())


if __name__ == '__main__':
    unittest.main()
