"""Fix round 7 (wave F) of 2026-10-30, the closecash group: regressions of
wave E's repairs. A broker sale of units the coverage replay cannot see, a
transit whose arrival settled in the year, a later rebuy at another
broker and an INKIND line check-tt cannot size yet (synthetic data only).

Formerly: test_fix_1029_r7f_closecash"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from _support.qt_close import _QH, _project, _row


def _tf(day, qty, bv=None, num="99900001"):
    desc = f"SYN CORP TRANSFER BOOK VALUE {bv}.00" if bv else "SYN CORP TRANSFER"
    return _row(day, "TF6", "SYN.TO", qty, 0, 0, kind="Transfers", desc=desc,
                num=num)


def _sell(day, qty, net, num="99900001"):
    return _row(day, "Sell", "SYN.TO", -qty, net / qty, net, num=num)


def _buy(day, qty, cost, num="99900001"):
    return _row(day, "Buy", "SYN.TO", qty, cost / qty, -cost, num=num)


class TestTJ1283UnitsTheReplayCannotSee(unittest.TestCase):
    """TJ-1283 (a regression of TJ-1179's fix): a broker sale of units the
    account held by other means (an OPENING, a move of your own, a broker
    row's split) spent a .tt purchase waiting for a later arrival.

    Formerly: test_fix_1029_r7f_closecash.TestTJ1283UnitsTheReplayCannotSee"""

    def _run(self, accounts, files):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), accounts)
            for rel, text in files.items():
                (root / "inputs" / rel).write_text(text)
            tj(root, "run", "--no-input")
            r = tj(root, "run", "--no-input")
            self.assertNotIn("go short", r.stdout + r.stderr)
            booked = []
            for p in (root / "work").glob("*_transfer_costs.json"):
                booked += [t["date"][:10] for t in
                           json.loads(p.read_text())["transactions"]]
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            held = json.loads(tj(root, "list", "--json").stdout)["rows"]
            return (sorted(booked), fe["gain_unrounded"],
                    [(h["symbol"], h["qty"]) for h in held])

    def test_an_opening_balance(self):
        got = self._run({"margin": "taxable"}, {
            "margin/t.tt": "OPENING 2024-12-31 SYN.TO 20 CAD 200\n"
                           "BUYSELL 2025-01-05 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "margin/q.csv": _QH + _sell("2025-03-03", 20, 300)
            + _tf("2025-05-05", 10, 100) + _sell("2025-06-02", 10, 200)})
        self.assertEqual(got[0], [])
        self.assertAlmostEqual(got[1], 200.0, places=2)
        self.assertEqual(got[2], [])

    def test_a_sale_beyond_the_opening_still_spends_the_tt_units(self):
        # TJ-1179's own case beside an opening: 5 opening units, 10 .tt
        # units, 15 sold: the arrival has nothing left to cover it.
        got = self._run({"margin": "taxable"}, {
            "margin/t.tt": "OPENING 2024-12-31 SYN.TO 5 CAD 50\n"
                           "BUYSELL 2025-01-05 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "margin/q.csv": _QH + _sell("2025-03-03", 15, 300)
            + _tf("2025-05-05", 10, 300) + _sell("2025-06-02", 10, 400)})
        self.assertEqual(got[0], ["2025-05-05"])
        self.assertAlmostEqual(got[1], 250.0, places=2)
        self.assertEqual(got[2], [])

    def test_a_move_of_your_own(self):
        got = self._run({"aa": "taxable", "bb": "taxable"}, {
            "aa/q.csv": _QH + _buy("2025-01-02", 10, 100)
            + _tf("2025-02-03", -10),
            "bb/t.tt": "BUYSELL 2025-01-05 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "bb/q.csv": _QH + _tf("2025-02-03", 10, num="99900002")
            + _sell("2025-03-03", 10, 150, num="99900002")
            + _tf("2025-05-05", 10, 500, num="99900002")
            + _sell("2025-06-02", 10, 200, num="99900002")})
        self.assertEqual(got[0], [])
        self.assertAlmostEqual(got[1], 150.0, places=2)
        self.assertEqual(got[2], [])

    def test_a_split_by_a_broker_row(self):
        split = _row("2025-02-03", "DIS", "SYN.TO", 10, 0, 0, kind="Other",
                     desc="SYN CORP 2 FOR 1 STOCK SPLIT ON 10 SHS")
        got = self._run({"margin": "taxable"}, {
            "margin/t.tt": "BUYSELL 2025-03-10 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "margin/q.csv": _QH + _buy("2025-01-02", 10, 200) + split
            + _sell("2025-04-01", 20, 300) + _tf("2025-06-02", 10, 100)
            + _sell("2025-07-02", 10, 200)})
        self.assertEqual(got[0], [])
        self.assertAlmostEqual(got[1], 200.0, places=2)
        self.assertEqual(got[2], [])

    def _replay(self, tt, broker, own_in=None, qty=10.0):
        from taxjson.lib import transfer_in as TI
        a = TI.Arrival(
            account="m", symbol="SYN.TO", key="SYN.TO", date="2025-06-01",
            time="09:30:00", quantity=qty, currency="CAD", book_value=100.0,
            broker="questrade", source="", description="",
            row={"time": "09:30:00"})
        TI.mark_covered([a], {"m": tt}, broker_rows={"m": broker},
                        own_in={"m": own_in or []})
        return a

    @staticmethod
    def _tx(day, q, sym="SYN.TO", action="BUYSELL", new=""):
        return {"action": action, "date": day, "time": "09:30:00",
                "symbol": sym, "quantity": q, "symbol_new": new}

    def test_a_split_that_renames_and_a_reverse_split(self):
        tx = self._tx
        wait = [tx("2025-03-10", 10)]
        # The broker's rows rename OLD.TO: its units are SYN.TO's after it.
        a = self._replay(wait, [tx("2025-01-02", 10, "OLD.TO"),
                                tx("2025-02-03", 1.0, "OLD.TO", "SPLIT",
                                   "SYN.TO"),
                                tx("2025-04-01", -10)])
        self.assertTrue(a.covered)
        # A reverse split adds no units; the sale beyond them is the .tt's.
        a = self._replay(wait, [tx("2025-01-02", 10),
                                tx("2025-02-03", 0.5, "SYN.TO", "SPLIT"),
                                tx("2025-04-01", -15)])
        self.assertFalse(a.covered)

    def test_an_own_move_of_another_symbol_covers_no_sale(self):
        tx = self._tx
        wait = [tx("2025-01-05", 10)]
        sale = [tx("2025-03-03", -10)]
        a = self._replay(wait, sale, own_in=[("2025-02-03", "SYN.TO", 10.0)])
        self.assertTrue(a.covered)
        a = self._replay(wait, sale, own_in=[("2025-02-03", "ZZZ.TO", 10.0)])
        self.assertFalse(a.covered)

    def test_usa_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), {"margin": "taxable"})
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"')
                .replace('America/Toronto', 'America/New_York'))
            (root / "inputs/margin/t.tt").write_text(
                "OPENING 2024-12-31 SYN.US 20 USD 200 2024-01-02\n"
                "BUYSELL 2025-01-05 09:30:00 SYN.US 10 USD 10 100 0\n")
            rows = (_sell("2025-03-03", 20, 300) + _tf("2025-05-05", 10, 100)
                    + _sell("2025-06-02", 10, 200))
            (root / "inputs/margin/q.csv").write_text(
                _QH + rows.replace("SYN.TO", "SYN.US")
                .replace(",CAD,", ",USD,"))
            tj(root, "run", "--no-input")
            tj(root, "run", "--no-input")
            self.assertFalse(
                (root / "work/margin_transfer_costs.json").exists())


if __name__ == "__main__":
    unittest.main()
