"""Fixes of 2026-10-30, round 7, wave E, group cash: T1135 cash in
transit, export coverage, Kraken swap fees, Coinbase's blank Total and
repeated hour across exports, and the crypto-sends migration on read-only
inputs. One test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented books and accounts, made-up tickers
and ids, small amounts. Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7e_cash
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj
from _support.filing_coverage_project import _GENERIC_MAP, _cfg, _checklist, _ib, _project
from _support.ib_statement_rows import _trade

from taxjson.lib import checklist as cl
from taxjson.lib import export_coverage as EC
from taxjson.project.settings import Settings
from _support.ca_t1135_cash import _t1135_project, _t1135, _generic


# ============================================== TJ-1228 a one-end-counted
# own move inside the year
class TestTJ1228TransitIsNotAMissingBalance(unittest.TestCase):
    """TJ-1228: the checklist and the brief read a span in transit as a
    balance the books do not give, and asked for a cost by hand.

    Formerly: test_fix_1029_r7e_cash.TestTJ1228TransitIsNotAMissingBalance"""

    @staticmethod
    def _rep(root):
        _t1135_project(root, {"us": "USA", "dom": None}, {
            "us": {"cash.tt": "CASHOPEN 2025-01-01 USD 100 125\n"
                              "CASHMOVE 2025-06-01 USD -100 own\n"
                              "CASHBAL 2025-12-31 USD 0\n"},
            "dom": {"cash.tt": "CASHOPEN 2025-01-01 USD 0 0\n"
                               "CASHMOVE 2025-06-05 USD 100 own\n"
                               "CASHBAL 2025-12-31 USD 100\n"}})
        return _t1135(root)

    def test_the_step_stays_done_and_the_brief_says_transit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            rep = self._rep(root)
            self.assertTrue(rep["total_settled"])
            n, = rep["cash_not_counted"]
            self.assertTrue(n["transit"])
            brief = tj(root, "t1135").stdout
            ctx = type("Ctx", (), {
                "settings": Settings.from_table({"country": "canada"}), "year": 2025,
                "today": date(2026, 10, 30),
                "sub": lambda self, *a: (0, json.dumps(rep), "")})()
            res = cl.d_t1135(ctx)
        self.assertEqual(res.status, "done", res)
        self.assertNotIn("add it by hand", brief)
        self.assertIn("in transit during the year", brief)

    def test_a_missing_base_balance_still_asks(self):
        rep = {"total_settled": True, "cash_not_counted": [
            {"account": "us", "currency": "CAD", "unknown": False,
             "why": "no balance"}], "year_complete": True}
        ctx = type("Ctx", (), {
            "settings": Settings.from_table({"country": "canada"}), "year": 2025,
            "today": date(2026, 10, 30),
            "sub": lambda self, *a: (0, json.dumps(rep), "")})()
        res = cl.d_t1135(ctx)
        self.assertEqual(res.status, "manual")
        self.assertIn("books give no balance", res.detail)


# ============================================== TJ-1158 a sale of another
class TestTJ1158AnotherAccountsOwnSale(unittest.TestCase):
    """TJ-1158: the sale of the other account's own units closed the
    position the short account still held.

    Formerly: test_fix_1029_r7e_cash.TestTJ1158AnotherAccountsOwnSale"""

    def _gaps(self, alpha, beta, tt_text=""):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, {"margin": "taxable"},
                            extra="combined_broker_accounts = true\n")
            for broker, acct, rows in (("alpha", "99900001", alpha),
                                       ("beta", "99900002", beta)):
                f = root / "inputs/margin" / f"generic_{broker}.csv"
                f.write_text(_generic(rows))
                f.with_name(f.name + ".toml").write_text(
                    _GENERIC_MAP.format(broker=broker, account=acct))
            if tt_text:
                (root / "inputs/margin/trades.tt").write_text(tt_text)
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [(g.broker, g.positions) for g in gaps if not g.info]

    BUY = ("2025-03-03", "BUY", "SYN.TO", 10)
    TAIL = ("2025-12-15", "BUY", "ZZZ.TO", 5)

    def test_the_short_broker_keeps_its_gap(self):
        got = self._gaps([self.BUY], [
            self.BUY, ("2025-05-01", "SELL", "SYN.TO", -10), self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 10.0)])])

    def test_a_sale_beyond_its_own_units_closes_the_rest(self):
        got = self._gaps([self.BUY], [
            ("2025-03-03", "BUY", "SYN.TO", 4),
            ("2025-05-01", "SELL", "SYN.TO", -10), self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 4.0)])])

    def test_a_broker_that_never_held_it_closes_it(self):
        # Control (a move to another broker booked as its sale).
        got = self._gaps([self.BUY], [
            ("2025-05-01", "SELL", "SYN.TO", -10), self.TAIL])
        self.assertEqual(got, [])

    def test_a_buy_and_sale_inside_the_gap_leave_the_units(self):
        got = self._gaps([self.BUY], [
            ("2025-06-01", "BUY", "SYN.TO", 7),
            ("2025-07-01", "SELL", "SYN.TO", -7), self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 10.0)])])

    def test_a_tt_sale_still_closes_it(self):
        got = self._gaps([self.BUY], [self.BUY, self.TAIL],
                         "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 15 150 0\n")
        # beta's 10 are open past the end of its own rows; alpha's closed.
        self.assertEqual(got, [])

    def test_two_ib_accounts_of_one_combined_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, {"a": "taxable"},
                            extra="combined_broker_accounts = true\n")
            (root / "inputs/a/a.csv").write_text(
                _ib("U1234567", "March 3, 2025", "SYN"))
            (root / "inputs/a/b.csv").write_text(
                _ib("U9990002", "December 31, 2025", "SYN")
                + _trade("SYN", "2025-05-01, 10:00:00", -10, 10, 100,
                         acct="U9990002", cur="CAD"))
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            step = _checklist(root)
        self.assertEqual([(g.broker, g.end, g.positions) for g in gaps],
                         [("ib", "2025-03-03", [("SYN.TO", 10.0)])])
        self.assertEqual(step.status, "attention", step)

    def test_a_split_scales_the_other_brokers_lot(self):
        rows = [{"action": "BUYSELL", "date": "2025-03-03", "symbol": "SYN",
                 "quantity": 10},
                {"action": "SPLIT", "date": "2025-04-01", "symbol": "SYN",
                 "quantity": 2},
                {"action": "BUYSELL", "date": "2025-05-01", "symbol": "SYN",
                 "quantity": -20}]
        spent = {}
        EC._spend_own_units(rows, spent, True)
        self.assertEqual(spent[id(rows[0])], 10.0)
        self.assertEqual(spent[id(rows[2])], -20.0)
        self.assertEqual(EC.closed_later([("SYN", 10.0)], [rows[2]],
                                         spent=spent),
                         ([("SYN", 10.0)], [], []))

    def test_a_written_option_is_opened_and_closed_too(self):
        rows = [{"action": "BUYSELL", "date": "2025-03-03",
                 "symbol": "SYN260116C00010000", "quantity": -1},
                {"action": "BUYSELL", "date": "2025-05-01",
                 "symbol": "SYN260116C00010000", "quantity": 1}]
        spent = {}
        EC._spend_own_units(rows, spent, False)
        self.assertEqual(spent, {id(rows[0]): -1.0, id(rows[1]): 1.0})


if __name__ == "__main__":
    unittest.main()
