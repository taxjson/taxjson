"""The US books of a futures buy whose `.tt` TOTAL is written as the
cash paid out (negative): the trade its positive spelling is (backlog
TJ-5162). The settlement rewrite read the buy's money signed, the US
engine's own walk its magnitude: a 40,000 USD loss was an 80,000 USD
gain in `tjs sum`. Made-up contract and amounts.
"""
import json
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.lib.futures import settle_futures
from taxjson.model.transaction import TaxTransaction as T
from taxjson.rules import ids as R
from _support.cli_command_runs import _tjs
from _tmpfiles import private_dir

FUT = "F:QZM26.US"
SELL = f"BUYSELL 2025-06-02 10:00:00 {FUT} -2 USD 100 20000 0 x100\n"
CASH_OUT = f"BUYSELL 2025-01-06 10:00:00 {FUT} 2 USD 300 -60000 0 x100\n"
COST = f"BUYSELL 2025-01-06 10:00:00 {FUT} 2 USD 300 60000 0 x100\n"


def _project(name, lines):
    root = Path(private_dir()) / name
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "usa"\n'
        'base_currency = "USD"\nsource_currencies = []\n'
        'local_timezone = "America/Toronto"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin" / "hand.tt").write_text(lines)
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return root


def _json(root, *args):
    r = _tjs(root, *args, "--json")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return json.loads(r.stdout)


def _row(date, qty, price, net, **kw):
    return T(action="BUYSELL", date=date, date_settle=date, time="10:00:00",
             symbol=FUT, quantity=qty, currency="USD", price=price,
             net_amount=net, account="margin", **kw)


def _gain(rows):
    r = USATaxRules().compute_gains(rows, detect_wash_sales=False)
    return r["summary"]["total_gain"]


@rule(R.US_FUT_01)
class TestOneTradeOneFigureUs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.cash_out = _project("cash-out", CASH_OUT + SELL)
        cls.cost = _project("cost", COST + SELL)

    def test_gains_and_sum_show_the_loss(self):
        for root in (self.cash_out, self.cost):
            self.assertEqual(_json(root, "gains")["totals"],
                             {"USD": -40000.0})
            doc = _json(root, "sum")
            self.assertEqual(doc["currency"], "USD")
            self.assertEqual([a["realized"] for a in doc["accounts"]],
                             [-40000.0])

    def test_the_reports_of_the_two_spellings_are_the_same(self):
        for name in ("margin.sum", "fees.rpt"):
            self.assertEqual(
                (self.cash_out / "reports" / name).read_text(),
                (self.cost / "reports" / name).read_text(), name)


@rule(R.US_FUT_01)
class TestSettlementReadsABuyAsTheUsEngineDoes(unittest.TestCase):

    def _both(self, rows):
        settled, _ = settle_futures(list(rows), "fifo")
        return _gain(list(rows)), _gain(settled)

    def test_a_long_bought_in_the_cash_sign(self):
        for net in (-60000.0, 60000.0):
            self.assertEqual(self._both([
                _row("2025-01-06", 2, 300.0, net),
                _row("2025-06-02", -2, 100.0, 20000.0)]),
                (-40000.0, -40000.0))

    def test_a_short_covered_in_the_cash_sign(self):
        for net in (-20000.0, 20000.0):
            self.assertEqual(self._both([
                _row("2025-01-06", -2, 300.0, 60000.0),
                _row("2025-06-02", 2, 100.0, net)]),
                (40000.0, 40000.0))

    def test_negative_prices_keep_their_sign(self):
        self.assertEqual(self._both([
            _row("2025-01-06", 1, -5.0, -5000.0, multiplier=1000.0),
            _row("2025-06-02", -1, 10.0, 10000.0, multiplier=1000.0)]),
            (15000.0, 15000.0))
        self.assertEqual(self._both([
            _row("2025-01-06", 1, 10.0, 10000.0, multiplier=1000.0),
            _row("2025-06-02", -1, -5.0, -5000.0, multiplier=1000.0)]),
            (-15000.0, -15000.0))

    def test_fifo_takes_the_first_lot_whichever_its_spelling(self):
        # First in: 2 at 300 written as cash out; one of them closed.
        self.assertEqual(self._both([
            _row("2025-01-06", 2, 300.0, -60000.0),
            _row("2025-02-03", 2, 200.0, 40000.0),
            _row("2025-06-02", -1, 100.0, 10000.0)]),
            (-20000.0, -20000.0))


if __name__ == "__main__":
    unittest.main()
