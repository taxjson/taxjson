"""Regression tests for fix round 7 (wave E), group shorts: a marked short
beside an own move's shares, a written option's premium in the holdings views
under close timing, the run's no-total note and holdings report after a split
one account reports, and edge-cases' HELD DAY 30 for a short holder.

Synthetic data only; account labels and tickers are made up.

Formerly: test_fix_1029_r7e_shorts
"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R
from _qa_project import gains, project, tj
from taxjson.bin.taxjson_split_gains import _split_inventory
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res


def _row(account, date, qty, net, open_close='', sym='SYQ.TO', settle=None):
    return TaxTransaction(action='BUYSELL', date=date,
                          date_settle=settle or date,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency='CAD', account=account,
                          open_close=open_close)


_OPT = "SYQ261218P00010000.TO"
_CLOSE_TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
               'country = "canada"\nbase_currency = "CAD"\n'
               'source_currencies = ["USD"]\n'
               'option_premium_timing = "close"\n'
               '[accounts.a]\ntype = "taxable"\n'
               '[accounts.b]\ntype = "taxable"\n')
_WRITE_A = f"BUYSELL 2025-01-02 10:00:00 {_OPT} -1 CAD 1 100\n"
_WRITE_B = f"BUYSELL 2025-01-03 10:00:00 {_OPT} -2 CAD 3 600\n"


def _close_project(td, name, a=_WRITE_A, b=_WRITE_B, toml=_CLOSE_TOML):
    root = project(td, name, {"a/t.tt": a, "b/t.tt": b})
    (root / "taxjson.toml").write_text(toml)
    tj(root, "run", "--no-input")
    return root


def _option_rows(root, account):
    doc = json.loads((root / "work" / f"{account}_gains_wash.json").read_text())
    return {i["symbol"]: (round(i["qty"], 6), round(i["total_cost"], 2))
            for i in doc["inventory"]}


class TestCloseTimingViewsKeepEachAccountsPremium(unittest.TestCase):
    """TJ-1251: an account's open written contracts show the premium a
    close takes (CA-OPT-06), in list and harvest, as TJ-1105's close does.

    Formerly: test_fix_1029_r7e_shorts.TestCloseTimingViewsKeepEachAccountsPremium"""

    @rule(R.CA_OPT_06)
    def test_each_account_shows_its_own_premium(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p")
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-1.0, -100.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})
            text = tj(root, "list").stdout
            self.assertRegex(text, r"a\s+\S+\s+-1\s+-100\.00")
            self.assertRegex(text, r"b\s+\S+\s+-2\s+-600\.00")

    @rule(R.CA_OPT_06)
    def test_a_close_realizes_what_the_view_showed_and_b_stays(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(
                td, "p", a=_WRITE_A
                + f"BUYSELL 2025-06-02 10:00:00 {_OPT} 1 CAD 0.5 50\n")
            self.assertEqual(_option_rows(root, "a"), {})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})
            self.assertEqual(sum(round(g["gain"], 2)
                                 for g in gains(root, "a")), 50.0)

    @rule(R.CA_OPT_06)
    def test_a_partial_close_keeps_the_accounts_average(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(
                td, "p",
                a=f"BUYSELL 2025-01-02 10:00:00 {_OPT} -2 CAD 1.5 300\n"
                  f"BUYSELL 2025-06-02 10:00:00 {_OPT} 1 CAD 0.5 50\n")
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-1.0, -150.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})

    @rule(R.CA_OPT_06)
    def test_the_harvest_figure_is_what_a_buy_back_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p")
            (root / "work" / ".price_cache.json").write_text(json.dumps(
                {_OPT: {"price": 0.5, "source": "yfinance", "currency": "CAD",
                        "asof": date.today().isoformat(),
                        "quote_symbol": _OPT}}))
            rows = json.loads(tj(root, "harvest", "--options", "--json",
                                 check=False).stdout)["rows"]
            got = {r["account"]: round(r["unrealized"], 2) for r in rows}
            self.assertEqual(got, {"a": 50.0, "b": 500.0})

    @rule(R.CA_OPT_06)
    def test_grant_timing_is_as_before(self):
        toml = _CLOSE_TOML.replace('"close"', '"grant"')
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p", toml=toml)
            a_rows, b_rows = _option_rows(root, "a"), _option_rows(root, "b")
            self.assertEqual(a_rows[_OPT][0], -1.0)
            self.assertEqual(round(a_rows[_OPT][1] + b_rows[_OPT][1], 2), -700.0)

    @rule(R.CA_OPT_06)
    def test_units_that_are_not_the_accounts_lots_go_by_share(self):
        rows = [{"symbol": _OPT, "qty": -3.0, "total_cost": -700.0,
                 "account_premiums": {
                     "a": {"units": 2.0, "premium": 100.0},
                     "b": {"units": 1.0, "premium": 600.0}}}]
        book = [{"action": "BUYSELL", "symbol": _OPT, "quantity": -1.0,
                 "date": "2025-01-02", "date_settle": "2025-01-02",
                 "account": "a"}]
        (row,) = _split_inventory(rows, "a", book, {}, "9999-12-31")
        self.assertEqual(row["qty"], -1.0)
        self.assertEqual(row["total_cost"], round(-700.0 / 3, 6))
        self.assertNotIn("account_premiums", row)

    @rule(R.CA_OPT_06)
    def test_a_parked_deferral_goes_by_units_on_top(self):
        rows = [{"symbol": _OPT, "qty": -3.0, "total_cost": -730.0,
                 "account_premiums": {
                     "a": {"units": 1.0, "premium": 100.0},
                     "b": {"units": 2.0, "premium": 600.0}}}]
        book = [{"action": "BUYSELL", "symbol": _OPT, "quantity": -1.0,
                 "date": "2025-01-02", "date_settle": "2025-01-02",
                 "account": "a"}]
        (row,) = _split_inventory(rows, "a", book, {}, "9999-12-31")
        self.assertEqual(row["total_cost"], -110.0)

    @rule(R.CA_OPT_06)
    def test_a_table_of_the_wrong_shape_goes_by_share(self):
        book = [{"action": "BUYSELL", "symbol": _OPT, "quantity": -1.0,
                 "date": "2025-01-02", "date_settle": "2025-01-02",
                 "account": "a"}]
        for bad in ("x", [], {"a": 1}, {"a": {"units": "?", "premium": 1}},
                    {"a": {"units": 1.0}}, None):
            rows = [{"symbol": _OPT, "qty": -3.0, "total_cost": -600.0,
                     "account_premiums": bad}]
            (row,) = _split_inventory(rows, "a", book, {}, "9999-12-31")
            self.assertEqual(row["total_cost"], -200.0, bad)

    @rule(R.CA_OPT_06)
    def test_a_split_scales_both_accounts_lots(self):
        split = f"SPLIT 2025-03-03 09:30:00 {_OPT} {_OPT} 2\n"
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p", a=_WRITE_A + split, b=_WRITE_B)
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-2.0, -100.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-4.0, -600.0)})

    @rule(R.CA_OPT_06)
    def test_a_registered_accounts_write_is_apart(self):
        toml = _CLOSE_TOML + '[accounts.tfsa]\ntype = "sheltered"\n'
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {
                "a/t.tt": _WRITE_A, "b/t.tt": _WRITE_B,
                "tfsa/t.tt": f"BUYSELL 2025-01-03 10:00:00 {_OPT} -1 CAD 9 900\n"})
            (root / "taxjson.toml").write_text(toml)
            tj(root, "run", "--no-input")
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-1.0, -100.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})

    @rule(R.CA_OPT_06)
    def test_one_account_has_no_premium_table(self):
        res = _run([_row("a", "2025-01-02", -1, 100, sym=_OPT),
                    _row("a", "2025-01-03", -2, 600, sym=_OPT)])
        self.assertNotIn("account_premiums", res["inventory"][0])

    @rule(R.CA_OPT_06)
    def test_two_accounts_have_it_and_it_sums_to_the_pool(self):
        res = _run([_row("a", "2025-01-02", -1, 100, sym=_OPT),
                    _row("b", "2025-01-03", -2, 600, sym=_OPT)])
        row = res["inventory"][0]
        self.assertEqual(row["account_premiums"],
                         {"a": {"units": 1.0, "premium": 100.0},
                          "b": {"units": 2.0, "premium": 600.0}})
        self.assertEqual(row["total_cost"], -700.0)


if __name__ == '__main__':
    unittest.main()
