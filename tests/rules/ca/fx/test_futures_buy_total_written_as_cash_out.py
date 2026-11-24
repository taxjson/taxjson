"""A futures buy whose `.tt` TOTAL is written as the cash paid out
(negative) is the trade its positive spelling is, in every figure
(backlog TJ-5162).

The settlement rewrite (lib/futures) read a buy's money signed while the
engine's native view took its magnitude: bought at 300 and sold at 100,
two contracts of 100, the native gains showed the 40,000 USD loss and
the base-currency figures a 108,000 CAD gain (twice the loss, the other
sign), with no warning. Made-up contract and amounts.
"""
import json
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.lib.futures import settle_futures
from taxjson.model.transaction import TaxTransaction as T
from taxjson.rules import ids as R
from _support.cli_command_runs import _tjs
from _tmpfiles import private_dir

FUT = "F:QZM26.US"
SELL = f"BUYSELL 2025-06-02 10:00:00 {FUT} -2 USD 100 20000 0 x100\n"
CASH_OUT = f"BUYSELL 2025-01-06 10:00:00 {FUT} 2 USD 300 -60000 0 x100\n"
COST = f"BUYSELL 2025-01-06 10:00:00 {FUT} 2 USD 300 60000 0 x100\n"
# tests/_hermetic's synthetic USD -> CAD rate, every day.
RATE = 1.35


def _project(name, lines):
    root = Path(private_dir()) / name
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
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
    r = CanadaTaxRules().compute_gains(rows, detect_wash_sales=False)
    return r["summary"]["total_gain"]


@rule(R.CA_FX_04)
class TestOneTradeOneFigure(unittest.TestCase):
    """`tjs gains` (native) and `tjs sum` (the base currency) of the two
    spellings: the loss, 40,000 USD and 54,000 CAD at its closing
    date's rate."""

    @classmethod
    def setUpClass(cls):
        cls.cash_out = _project("cash-out", CASH_OUT + SELL)
        cls.cost = _project("cost", COST + SELL)

    def test_the_native_gain_is_the_loss(self):
        for root in (self.cash_out, self.cost):
            doc = _json(root, "gains")
            self.assertEqual(doc["totals"], {"USD": -40000.0})
            self.assertEqual([(g["proceeds"], g["cost"], g["gain"])
                              for g in doc["rows"]],
                             [(20000.0, 60000.0, -40000.0)])

    def test_the_base_currency_figure_is_the_loss(self):
        for root in (self.cash_out, self.cost):
            doc = _json(root, "sum")
            self.assertEqual(doc["currency"], "CAD")
            self.assertEqual([a["realized"] for a in doc["accounts"]],
                             [-40000.0 * RATE])
            self.assertEqual([(ln["gain"], ln["dispositions"])
                              for ln in doc["filing"]["lines"]
                              if ln["dispositions"]],
                             [(-40000.0 * RATE, 1)])

    def test_the_reports_of_the_two_spellings_are_the_same(self):
        for name in ("margin.sum", "margin_wash.sum", "fees.rpt"):
            self.assertEqual(
                (self.cash_out / "reports" / name).read_text(),
                (self.cost / "reports" / name).read_text(), name)

    def test_the_settled_books_record_one_cost(self):
        def facts(root):
            doc = json.loads((root / "work" / "margin_base.json").read_text())
            return [(t["quantity"], t["net_amount"], t["settle_native"])
                    for t in doc["transactions"]]
        self.assertEqual(facts(self.cash_out), facts(self.cost))
        self.assertEqual(facts(self.cost), [
            (2.0, 0.0, "LO 60000.0 0 USD 1.3500"),
            (-2.0, -40000.0 * RATE, "LC 20000.0 -40000.0 USD 1.3500")])


@rule(R.CA_FX_04)
class TestACoverInTheCashSign(unittest.TestCase):
    """A short covered by a buy written as cash out: `tjs audit` ties
    out and names the fill's notional as a magnitude, as for the cover
    written as a cost."""

    OPEN = f"BUYSELL 2025-01-06 10:00:00 {FUT} -2 USD 3 600 0 x100\n"

    def _audit(self, name, total):
        root = _project(name, self.OPEN + (
            f"BUYSELL 2025-06-02 10:00:00 {FUT} 2 USD 1 {total} 0 x100\n"))
        r = _tjs(root, "audit")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r.stdout, _json(root, "sum")["accounts"][0]["realized"]

    def test_the_notional_is_a_magnitude(self):
        cash, cash_gain = self._audit("cover-cash-out", "-200")
        cost, cost_gain = self._audit("cover-cost", "200")
        self.assertEqual((cash_gain, cost_gain), (400.0 * RATE, 400.0 * RATE))
        for text in (cash, cost):
            self.assertIn("the notional 200.00 USD never changes hands", text)
            self.assertNotIn("the notional -", text)


@rule(R.CA_FX_04)
class TestSettlementReadsABuyAsTheEngineDoes(unittest.TestCase):
    """The settled P/L and the engine's native gain, fill by fill."""

    def _both(self, rows):
        settled, _ = settle_futures(list(rows), "average")
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

    def test_a_buy_at_a_negative_price_keeps_its_credit(self):
        # Paid 5,000 to take the contract, sold at +10: 15,000 made.
        self.assertEqual(self._both([
            _row("2025-01-06", 1, -5.0, -5000.0, multiplier=1000.0),
            _row("2025-06-02", -1, 10.0, 10000.0, multiplier=1000.0)]),
            (15000.0, 15000.0))

    def test_a_sale_at_a_negative_price_pays(self):
        self.assertEqual(self._both([
            _row("2025-01-06", 1, 10.0, 10000.0, multiplier=1000.0),
            _row("2025-06-02", -1, -5.0, -5000.0, multiplier=1000.0)]),
            (-15000.0, -15000.0))

    def test_only_a_future_receives_on_a_buy(self):
        from taxjson.engine.replacement import receives_on_buy
        from taxjson.lib.futures import buy_receives
        fut = _row("2025-01-06", 1, -5.0, -5000.0)
        share = T(action="BUYSELL", date="2025-01-06",
                  date_settle="2025-01-06", time="10:00:00",
                  symbol="ZZQ.US", quantity=10, currency="USD", price=-5.0,
                  net_amount=-50.0, account="margin")
        option = T(action="BUYSELL", date="2025-01-06",
                   date_settle="2025-01-06", time="10:00:00",
                   symbol="F:QZ260116C00050000.US", quantity=1, currency="USD",
                   price=-2.0, net_amount=-2000.0, account="margin")
        for test in (buy_receives, receives_on_buy):
            self.assertTrue(test(fut))
            self.assertFalse(test(share))
            self.assertFalse(test(option))
        # The share's cost is its magnitude: sold for 60, a gain of 10.
        sale = T(action="BUYSELL", date="2025-06-02",
                 date_settle="2025-06-02", time="10:00:00", symbol="ZZQ.US",
                 quantity=-10, currency="USD", price=6.0, net_amount=60.0,
                 account="margin")
        self.assertEqual(_gain([share, sale]), 10.0)

    def test_a_partial_close_takes_the_average_of_both_spellings(self):
        # 2 at 300 (cash sign) and 2 at 200 (cost): average 250 x 100.
        self.assertEqual(self._both([
            _row("2025-01-06", 2, 300.0, -60000.0),
            _row("2025-02-03", 2, 200.0, 40000.0),
            _row("2025-06-02", -1, 100.0, 10000.0)]),
            (-15000.0, -15000.0))


if __name__ == "__main__":
    unittest.main()
