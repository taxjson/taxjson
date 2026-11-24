"""A futures `.tt` line's TOTAL against its own price (backlog TJ-5162).

A buy's total written as the cash paid out (negative) was booked as a
credit by the futures settlement and as a cost by the engines' native
view: one trade, two gains. The reader now takes either sign as the
cost at a price above 0, reads a negative price's total only where the
line proves it (its size, the total = qty x price x size +/- fee, sign
included), and refuses the rest, naming the file and line: a settled
line an older `taxjson-convert-tt` wrote from such a buy too.
`taxjson-convert-tt` writes a total as the book holds it (the row's id
stays its source's), and either sign reads back as the one trade.

Every contract is made up (F:QZM26.US); the amounts are invented.
"""
import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.futures import settle_futures
from taxjson.lib.tt_format import (json_to_tt_lines, parse_tt_line,
                                   tx_to_tt_line)
from taxjson.lib.tx_load import coerce_transaction_row
import _paths
from _style import CapturedWidth
from _tmpfiles import private_dir

_WIDTH = CapturedWidth()
FUT = "F:QZM26.US"


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _parse(line, where="hand.tt:7"):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        tx = parse_tt_line(line, account_name="margin", source=where)
    return tx, err.getvalue()


def _buy(rest):
    return f"BUYSELL 2025-01-06 10:00:00 {FUT} {rest}"


def _sell(rest):
    return f"BUYSELL 2025-06-02 10:00:00 {FUT} {rest}"


def _settled(lines, method="average"):
    """The settled P/L rows of a book of .tt lines."""
    rows = [coerce_transaction_row(_parse(ln)[0], i, "t")
            for i, ln in enumerate(lines)]
    out, _ = settle_futures(rows, method)
    return [t.net_amount for t in out]


class TestBuyTotalEitherSign(unittest.TestCase):
    """Price above 0: the buy's total is its cost, whichever sign."""

    def test_both_signs_are_read_and_are_one_cost(self):
        neg, err_n = _parse(_buy("2 USD 300 -60000 0 x100"))
        pos, err_p = _parse(_buy("2 USD 300 60000 0 x100"))
        self.assertEqual((neg["net_amount"], pos["net_amount"]),
                         (-60000.0, 60000.0))
        self.assertEqual((err_n, err_p), ("", ""))
        close = _sell("-2 USD 100 20000 0 x100")
        for method in ("average", "fifo"):
            self.assertEqual(
                _settled([_buy("2 USD 300 -60000 0 x100"), close], method),
                _settled([_buy("2 USD 300 60000 0 x100"), close], method))

    def test_a_cover_written_as_cash_out_is_the_same_cover(self):
        opened = _buy("-2 USD 300 60000 0 x100")
        for method in ("average", "fifo"):
            self.assertEqual(
                _settled([opened, _sell("2 USD 100 -20000 0 x100")], method),
                [0.0, 40000.0])
            self.assertEqual(
                _settled([opened, _sell("2 USD 100 20000 0 x100")], method),
                [0.0, 40000.0])

    def test_no_size_on_the_line_reads_the_same(self):
        self.assertEqual(_settled([_buy("2 USD 300 -60000 0"),
                                   _sell("-2 USD 100 20000 0")]),
                         [0.0, -40000.0])

    def test_a_share_line_is_untouched(self):
        tx, _ = _parse("BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 USD 5 -50 0")
        self.assertEqual(tx["net_amount"], -50.0)


class TestNegativePrice(unittest.TestCase):
    """Price below 0: the total carries the sign of qty x price x size
    +/- fee, and is read only where the line proves it."""

    def test_a_buy_receives_and_a_sale_pays(self):
        buy, err = _parse(_buy("1 USD -5 -5000 0 x1000"))
        self.assertEqual((buy["net_amount"], err), (-5000.0, ""))
        sale, err = _parse(_sell("-1 USD -5 -5000 0 x1000"))
        self.assertEqual((sale["net_amount"], err), (-5000.0, ""))
        # Bought for a credit of 5,000, sold at +10: 15,000 made.
        self.assertEqual(_settled([_buy("1 USD -5 -5000 0 x1000"),
                                   _sell("-1 USD 10 10000 0 x1000")]),
                         [0.0, 15000.0])
        # Bought at +10, sold by paying 5,000: 15,000 lost.
        self.assertEqual(_settled([_buy("1 USD 10 10000 0 x1000"),
                                   _sell("-1 USD -5 -5000 0 x1000")]),
                         [0.0, -15000.0])

    def test_the_commission_is_inside_the_total(self):
        buy, _ = _parse(_buy("1 USD -5 -4997.5 2.5 x1000"))
        self.assertEqual(buy["net_amount"], -4997.5)
        sale, _ = _parse(_sell("-1 USD -5 -5002.5 2.5 x1000"))
        self.assertEqual(sale["net_amount"], -5002.5)

    def test_a_sale_pays_its_commission_too(self):
        # 1 x -0.5 x 1000 - 20: the fee is 4% of the notional, so a
        # total checked against notional + fee (-480) would be refused.
        sale, err = _parse(_sell("-1 USD -0.5 -520 20 x1000"))
        self.assertEqual((sale["net_amount"], err), (-520.0, ""))
        self.assertIn("-520.00", self._refused(_sell("-1 USD -0.5 -480 20 x1000")))
        buy, _ = _parse(_buy("1 USD -0.5 -480 20 x1000"))
        self.assertEqual(buy["net_amount"], -480.0)
        self.assertIn("-480.00", self._refused(_buy("1 USD -0.5 -520 20 x1000")))

    def test_the_sign_is_read_inside_the_tolerance_floor(self):
        # Expected -0.02: +0.02 is 0.04 away, under the 0.05 floor, and
        # still the opposite trade.
        text = self._refused(_buy("1 USD -0.00002 0.02 0 x1000"))
        self.assertIn("-0.02", text)
        self.assertEqual(
            _parse(_buy("1 USD -0.00002 -0.02 0 x1000"))[0]["net_amount"], -0.02)
        self._refused(_sell("-1 USD -0.00002 0.02 0 x1000"))
        self._refused(_buy("1 USD -0.00002 0 0 x1000"))

    def test_the_price_is_quoted_as_written(self):
        text = self._refused(_buy("1 USD -0.00002 0.02 0 x1000"))
        self.assertIn("negative price -0.00002:", text)
        self.assertNotIn("e-05", text)
        text = self._refused(_buy("1 USD -5.1234567 5123.4567 0 x1000"))
        self.assertIn("negative price -5.1234567:", text)

    def test_a_commission_larger_than_the_credit_is_a_cost(self):
        buy, err = _parse(_buy("1 USD -0.001 1.5 2.5 x1000"))
        self.assertEqual((buy["net_amount"], err), (1.5, ""))
        self.assertEqual(_settled([_buy("1 USD -0.001 1.5 2.5 x1000"),
                                   _sell("-1 USD 10 10000 0 x1000")]),
                         [0.0, 9998.5])

    def _refused(self, line):
        with self.assertRaises(ValueError) as cm:
            _parse(line)
        text = str(cm.exception)
        self.assertTrue(text.startswith("hand.tt:7: futures "), text)
        self.assertIn(" ".join(line.split()), text)
        return text

    def test_a_total_of_the_other_sign_is_refused(self):
        text = self._refused(_buy("1 USD -5 5000 0 x1000"))
        self.assertIn("-5000.00", text)
        self.assertIn("receives money", text)
        text = self._refused(_sell("-1 USD -5 5000 0 x1000"))
        self.assertIn("-5000.00", text)
        self.assertIn("pays money", text)

    def test_a_total_of_another_size_is_refused(self):
        self.assertIn("-5000.00", self._refused(_buy("1 USD -5 -500 0 x1000")))
        self.assertIn("-5000.00",
                      self._refused(_sell("-1 USD -5 -500 0 x1000")))

    def test_no_size_on_the_line_is_refused(self):
        for line in (_buy("1 USD -5 -5000 0"), _sell("-1 USD -5 -5000 0")):
            self.assertIn("x1000", self._refused(line))

    def test_no_price_and_a_negative_buy_total_is_refused(self):
        text = self._refused(_buy("1 USD 0 -5000 0"))
        self.assertIn("no price", text)
        # A positive total, or a sale, with no price reads as before.
        self.assertEqual(_parse(_buy("1 USD 0 5000 0"))[0]["net_amount"],
                         5000.0)
        self.assertEqual(_parse(_sell("-1 USD 0 6000 0"))[0]["net_amount"],
                         6000.0)

    def test_a_settled_line_and_a_cancellation_are_not_checked(self):
        tx, _ = _parse(_sell("-1 USD -5 250 0 type=futures_settlement"))
        self.assertEqual(tx["net_amount"], 250.0)
        tx, _ = _parse(_buy("-1 USD -5 -5000 0 x1000 type=trade_cancel"))
        self.assertEqual(tx["quantity"], -1.0)


class TestAnOldSettledLine(unittest.TestCase):
    """A settled futures line an older taxjson-convert-tt wrote from a
    buy in the cash sign: its buying leg records negative money at a
    price above 0, and the close after it carries the flipped gain."""

    OPEN = ("BUYSELL 2025-01-06 10:00:00 F:QZM26.US 2.00000000 CAD "
            "405.00000000 0.00000 0.00000 x100 type=futures_settlement "
            "settle=LO,-60000.0,0,USD,1.3500")
    CLOSE = ("BUYSELL 2025-06-02 10:00:00 F:QZM26.US -2.00000000 CAD "
             "135.00000000 108000.00000 0.00000 x100 "
             "type=futures_settlement settle=LC,20000.0,80000.0,USD,1.3500")

    def test_the_opening_is_refused_naming_the_line(self):
        with self.assertRaises(ValueError) as cm:
            _parse(self.OPEN)
        text = str(cm.exception)
        self.assertTrue(text.startswith("hand.tt:7: settle=LO,-60000.0,"),
                        text)
        self.assertIn("older taxjson-convert-tt", text)
        self.assertIn("tjs run", text)
        self.assertIn(self.OPEN, text)

    def test_a_cover_is_refused_too(self):
        with self.assertRaises(ValueError) as cm:
            _parse(self.OPEN.replace("settle=LO,-60000.0,0,",
                                     "settle=SC,-60000.0,500.0,"))
        self.assertIn("a SC leg buys", str(cm.exception))

    def test_what_is_still_read(self):
        # The cost as the settlement records it now.
        tx, _ = _parse(self.OPEN.replace("LO,-60000.0", "LO,60000.0"))
        self.assertEqual(tx["settle_native"], "LO 60000.0 0 USD 1.3500")
        # The credit of a buy at a negative price.
        tx, _ = _parse(
            "BUYSELL 2025-02-03 10:00:00 F:QZM26.US 1.00000000 CAD "
            "-6.75000000 0.00000 0.00000 x1000 type=futures_settlement "
            "settle=LO,-5000.0,0,USD,1.3500")
        self.assertEqual(tx["settle_native"], "LO -5000.0 0 USD 1.3500")
        # A selling leg (its money is not the test).
        self.assertEqual(_parse(self.CLOSE)[0]["net_amount"], 108000.0)

    def test_the_tool_refuses_the_file(self):
        d = Path(private_dir())
        tt = d / "old_settled.tt"
        tt.write_text(self.OPEN + "\n" + self.CLOSE + "\n")
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                   PYTHONPATH=str(_paths.SRC))
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_convert_tt")
            + [str(tt), str(d / "out.json"), "--account-name", "margin",
               "--equity"],
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("old_settled.tt:1: settle=LO,-60000.0,", r.stderr)
        self.assertFalse((d / "out.json").exists())


class TestConvertTtWritesTheTotalAsTheBookHoldsIt(unittest.TestCase):
    """A JSON book's futures buy in the cash sign (`net_amount` -200)
    is written as it is (the row keeps its source's id) and reads back
    as the same trade."""

    BOOK = [
        {"action": "BUYSELL", "date": "2025-01-06",
         "date_settle": "2025-01-06", "time": "10:00:00",
         "symbol": "/QZM26.US", "quantity": 2.0, "price": 100.0,
         "net_amount": -200.0, "commission": 1.0, "currency": "CAD",
         "account": "margin"},
        {"action": "BUYSELL", "date": "2025-06-02",
         "date_settle": "2025-06-02", "time": "10:00:00",
         "symbol": FUT, "quantity": -1.0, "price": 300.0,
         "net_amount": 300.0, "commission": 1.0, "currency": "CAD",
         "account": "margin"},
    ]

    def _book(self):
        d = Path(private_dir())
        p = d / "book.json"
        p.write_text(json.dumps({"transactions": self.BOOK}))
        return p

    def test_the_buy_line_carries_the_books_total(self):
        with contextlib.redirect_stderr(io.StringIO()):
            lines = list(json_to_tt_lines(self._book()))
        self.assertEqual(lines[0].split()[3:8],
                         [FUT, "2.00000000", "CAD", "100.00000000",
                          "-200.00000"])

    def test_the_round_trip_settles_to_the_books_own_figures(self):
        with contextlib.redirect_stderr(io.StringIO()):
            lines = list(json_to_tt_lines(self._book()))
        direct, _ = settle_futures(
            [coerce_transaction_row(dict(r), i, "book")
             for i, r in enumerate(self.BOOK)], "average")
        self.assertEqual([t.net_amount for t in direct], [0.0, 200.0])
        self.assertEqual(_settled(lines), [0.0, 200.0])

    def test_a_tt_line_keeps_its_id_through_the_writer(self):
        for total in ("-60000", "60000"):
            tx, _ = _parse(_buy(f"2 USD 300 {total} 0 x100"))
            back, _ = _parse(tx_to_tt_line(dict(tx)))
            self.assertEqual(back["net_amount"], tx["net_amount"])
            self.assertEqual(back["id"], tx["id"])

    def test_a_credit_and_a_settled_part_are_written_signed(self):
        row = dict(self.BOOK[0], symbol=FUT)
        neg = tx_to_tt_line(dict(row, price=-5.0, net_amount=-10000.0,
                                 multiplier=1000.0, commission=0.0))
        self.assertEqual(neg.split()[7], "-10000.00000")
        self.assertEqual(_parse(neg)[0]["net_amount"], -10000.0)
        settled = tx_to_tt_line(dict(row, type="futures_settlement"))
        self.assertEqual(settled.split()[7], "-200.00000")

    def test_the_tool_round_trip(self):
        book = self._book()
        out = book.with_name("out.tt")
        back = book.with_name("back.json")
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                   PYTHONPATH=str(_paths.SRC))
        tool = python_module_argv("taxjson.bin.taxjson_convert_tt")
        r = subprocess.run(
            tool + [str(book), str(out), "--date-basis", "settle"],
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_text().splitlines()[0].split()[7],
                         "-200.00000")
        r = subprocess.run(
            tool + [str(out), str(back), "--account-name", "margin",
                    "--equity"],
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [coerce_transaction_row(t, i, "back") for i, t in enumerate(
            json.loads(back.read_text())["transactions"])
            if t["action"] == "BUYSELL"]
        settled, _ = settle_futures(rows, "average")
        self.assertEqual([t.net_amount for t in settled], [0.0, 200.0])


class TestTheToolRefusesTheLine(unittest.TestCase):

    def test_convert_tt_names_the_file_and_line(self):
        d = Path(private_dir())
        tt = d / "hand.tt"
        tt.write_text(_buy("2 USD 300 60000 0 x100") + "\n"
                      + _sell("-2 USD -1 200 0 x100") + "\n")
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                   PYTHONPATH=str(_paths.SRC))
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_convert_tt")
            + [str(tt), str(d / "out.json"), "--account-name", "margin",
               "--equity"],
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)
        # 1: the exit of every .tt line the reader refuses.
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("taxjson-convert-tt: error: hand.tt:2: futures sell "
                      "at the negative price -1", r.stderr)
        self.assertIn("-200.00", r.stderr)
        self.assertFalse((d / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
