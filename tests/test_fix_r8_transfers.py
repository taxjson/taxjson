"""Fix round 8, the transfers group: the units a broker sale can read before
it spends a .tt purchase (TJ-1326, TJ-1327, TJ-1340), and what they
leave alone: TJ-1179's and TJ-1283's own shapes stay in
test_fix_1029_r7e_close and test_fix_1029_r7f_closecash (synthetic data
only)."""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_fix_1029_r7e_close import _QH, _project, _row

from taxjson.lib import transfer_in as TI

_N2 = "99900002"
_N3 = "99900003"
_OPENING = "OPENING 2024-12-31 SYN.TO 10 CAD 100\n"


def _tf(day, qty, bv=None, num="99900001"):
    desc = f"SYN CORP TRANSFER BOOK VALUE {bv}.00" if bv else "SYN CORP TRANSFER"
    return _row(day, "TF6", "SYN.TO", qty, 0, 0, kind="Transfers", desc=desc,
                num=num)


def _sell(day, qty, net, num="99900001"):
    return _row(day, "Sell", "SYN.TO", -qty, net / qty, net, num=num)


def _buy(day, qty, cost, num="99900001"):
    return _row(day, "Buy", "SYN.TO", qty, cost / qty, -cost, num=num)


def _split(day, shares, num="99900001"):
    return _row(day, "DIS", "SYN.TO", shares, 0, 0, kind="Other",
                desc=f"SYN CORP 2 FOR 1 STOCK SPLIT ON {shares} SHS", num=num)


def _tt(day, qty=10, total=100, sym="SYN.TO"):
    return f"BUYSELL {day} 09:30:00 {sym} {qty} CAD {total // qty} {total} 0\n"


class _Case(unittest.TestCase):
    def outcome(self, accounts, files):
        """The arrivals booked (dates), the year's gain, the holdings
        (account, units, cost) and whether the run says a position is
        short, and the run's text."""
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), accounts)
            for rel, text in files.items():
                (root / "inputs" / rel).write_text(text)
            tj(root, "run", "--no-input")
            r = tj(root, "run", "--no-input")
            booked = []
            for p in (root / "work").glob("*_transfer_costs.json"):
                booked += [t["date"][:10] for t in
                           json.loads(p.read_text())["transactions"]
                           if t.get("quantity", 0) > 0]
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            held = json.loads(tj(root, "list", "--json").stdout)["rows"]
            text = " ".join((r.stdout + r.stderr).split())
            self.text = text
            return (sorted(booked), fe["gain_unrounded"],
                    [(h.get("account"), h["qty"], h.get("cost")) for h in held],
                    "go short" in text)

    def assertOutcome(self, got, booked, gain, held):
        self.assertEqual(got[0], booked)
        self.assertAlmostEqual(got[1], gain, places=2)
        self.assertEqual([h[:2] for h in got[2]], [h[:2] for h in held])
        for g, h in zip(got[2], held):
            if len(h) > 2:
                self.assertAlmostEqual(g[2], h[2], places=2)
        self.assertFalse(got[3], "the run says a position goes short")


class TestOpeningReplacesTheRowsBeforeIt(_Case):
    """TJ-1326, TJ-1340: rows dated on or before a symbol's snapshot are left
    out of the books (CA-OPEN-03), so they are not units in the replay: the
    snapshot's count once, and the arrival is new units."""

    @rule("CA-OPEN-03")
    def test_a_tt_purchase_the_snapshot_replaced(self):
        # 10 at 100 sold for 150; 10 at 500 sold for 700.
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _tt("2024-06-03") + _OPENING,
            "margin/q.csv": _QH + _sell("2025-03-03", 10, 150)
            + _tf("2025-05-05", 10, 500) + _sell("2025-06-02", 10, 700)}),
            ["2025-05-05"], 250.0, [])

    @rule("CA-OPEN-03")
    def test_a_broker_purchase_the_snapshot_replaced(self):
        # 20 at 200 sold for 300; 10 at 500 sold for 700.
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _OPENING + _tt("2025-01-05"),
            "margin/q.csv": _QH + _buy("2024-06-03", 10, 100)
            + _sell("2025-03-03", 20, 300) + _tf("2025-05-05", 10, 500)
            + _sell("2025-06-02", 10, 700)}),
            ["2025-05-05"], 300.0, [])

    @rule("CA-OPEN-03")
    def test_sold_together_with_the_arrival(self):
        # TJ-1340: 20 units at 600 sold for 700.
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _tt("2024-06-03") + _OPENING,
            "margin/q.csv": _QH + _tf("2025-05-05", 10, 500)
            + _sell("2025-06-02", 20, 700)}),
            ["2025-05-05"], 100.0, [])

    @rule("CA-OPEN-03")
    def test_a_purchase_on_the_snapshot_day(self):
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _tt("2024-12-31") + _OPENING,
            "margin/q.csv": _QH + _sell("2025-03-03", 10, 150)
            + _tf("2025-05-05", 10, 500) + _sell("2025-06-02", 10, 700)}),
            ["2025-05-05"], 250.0, [])

    @rule("CA-OPEN-03")
    def test_a_purchase_under_the_old_name(self):
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _tt("2024-01-02", sym="OLD.TO")
            + "RENAME 2024-06-03 OLD.TO SYN.TO\n" + _OPENING,
            "margin/q.csv": _QH + _sell("2025-03-03", 10, 150)
            + _tf("2025-05-05", 10, 500) + _sell("2025-06-02", 10, 700)}),
            ["2025-05-05"], 250.0, [])

    def test_control_a_purchase_after_the_snapshot_still_covers(self):
        # The sale sold the snapshot's units; the purchase waits. 20 at 200.
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _OPENING + _tt("2025-01-05"),
            "margin/q.csv": _QH + _sell("2025-03-03", 10, 150)
            + _tf("2025-05-05", 10, 500) + _sell("2025-06-02", 10, 700)}),
            [], 650.0, [])

    def test_control_another_symbols_snapshot_changes_nothing(self):
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _tt("2024-06-03")
            + "OPENING 2024-12-31 ZZZ.TO 10 CAD 100\n",
            "margin/q.csv": _QH + _tf("2025-05-05", 10, 500)
            + _sell("2025-06-02", 10, 700)}),
            [], 600.0, [("margin", 10.0, 100.0)])

    def test_usa_cuts_the_same_rows(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), {"margin": "taxable"})
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"')
                .replace('America/Toronto', 'America/New_York'))
            (root / "inputs/margin/t.tt").write_text(
                "BUYSELL 2024-06-03 09:30:00 SYN.US 10 USD 10 100 0\n"
                "OPENING 2024-12-31 SYN.US 10 USD 100 2024-06-03\n")
            rows = (_sell("2025-03-03", 10, 150) + _tf("2025-05-05", 10, 500)
                    + _sell("2025-06-02", 10, 700))
            (root / "inputs/margin/q.csv").write_text(
                _QH + rows.replace("SYN.TO", "SYN.US")
                .replace(",CAD,", ",USD,"))
            tj(root, "run", "--no-input")
            tj(root, "run", "--no-input")
            costs = json.loads((root / "work/margin_transfer_costs.json")
                               .read_text())
            self.assertEqual([t["date"][:10] for t in costs["transactions"]],
                             ["2025-05-05"])


class TestUnitsThatLeftAreNotSold(_Case):
    """TJ-1327: units that left by a move of your own or a contribution are
    not what a later broker sale sold, so it spends the .tt purchase."""

    def test_a_move_of_your_own_in_and_out_again(self):
        self.assertOutcome(self.outcome(
            {"aa": "taxable", "bb": "taxable", "cc": "taxable"}, {
                "aa/q.csv": _QH + _buy("2025-01-02", 10, 100)
                + _tf("2025-02-03", -10),
                "bb/t.tt": _tt("2025-01-05"),
                "bb/q.csv": _QH + _tf("2025-02-03", 10, num=_N2)
                + _tf("2025-02-10", -10, num=_N2)
                + _sell("2025-03-03", 10, 150, _N2)
                + _tf("2025-05-05", 10, 500, _N2)
                + _sell("2025-06-02", 10, 700, _N2),
                "cc/q.csv": _QH + _tf("2025-02-10", 10, num=_N3)}),
            ["2025-05-05"], 450.0, [("cc", 10.0, 300.0)])

    def test_an_opening_contributed_in_kind(self):
        self.assertOutcome(self.outcome(
            {"margin": "taxable", "plan": "sheltered"}, {
                "margin/t.tt": _OPENING
                + "INKIND 2025-01-10 SYN.TO -10 CAD 12 120 plan=tfsa\n"
                + _tt("2025-02-10"),
                "margin/q.csv": _QH + _tf("2025-01-10", -10)
                + _sell("2025-03-03", 10, 150) + _tf("2025-05-05", 10, 500)
                + _sell("2025-06-02", 10, 700),
                "plan/q.csv": _QH + _tf("2025-01-10", 10, num=_N2)}),
            ["2025-05-05"], 270.0, [("plan", 10.0)])

    def test_control_part_of_a_move_left_the_rest_was_sold(self):
        # 4 of the 10 moved on, the 6 left were sold: the purchase still waits.
        out4 = _row("2025-02-10", "TF6", "SYN.TO", -4, 0, 0, kind="Transfers",
                    desc="SYN CORP TRANSFER", num=_N2)
        in4 = _row("2025-02-10", "TF6", "SYN.TO", 4, 0, 0, kind="Transfers",
                   desc="SYN CORP TRANSFER", num=_N3)
        self.assertOutcome(self.outcome(
            {"aa": "taxable", "bb": "taxable", "cc": "taxable"}, {
                "aa/q.csv": _QH + _buy("2025-01-02", 10, 100)
                + _tf("2025-02-03", -10),
                "bb/t.tt": _tt("2025-01-05"),
                "bb/q.csv": _QH + _tf("2025-02-03", 10, num=_N2) + out4
                + _sell("2025-03-03", 6, 90, _N2)
                + _tf("2025-05-05", 10, 500, _N2)
                + _sell("2025-06-02", 10, 150, _N2),
                "cc/q.csv": _QH + in4}),
            [], 80.0, [("cc", 4.0, 40.0)])

    def test_control_part_of_an_opening_contributed_the_rest_was_sold(self):
        self.assertOutcome(self.outcome(
            {"margin": "taxable", "plan": "sheltered"}, {
                "margin/t.tt": "OPENING 2024-12-31 SYN.TO 20 CAD 200\n"
                "INKIND 2025-01-10 SYN.TO -10 CAD 12 120 plan=tfsa\n"
                + _tt("2025-02-10"),
                "margin/q.csv": _QH + _tf("2025-01-10", -10)
                + _sell("2025-03-03", 10, 150) + _tf("2025-05-05", 10, 500)
                + _sell("2025-06-02", 10, 700),
                "plan/q.csv": _QH + _tf("2025-01-10", 10, num=_N2)}),
            [], 670.0, [("plan", 10.0)])

    def test_control_a_move_on_the_arrival_day_spends_nothing(self):
        def tx(day, q):
            return {"action": "BUYSELL", "date": day, "time": "09:30:00",
                    "symbol": "SYN.TO", "quantity": q}
        a = TI.Arrival(
            account="m", symbol="SYN.TO", key="SYN.TO", date="2025-04-01",
            time="09:30:00", quantity=10.0, currency="CAD", book_value=100.0,
            broker="questrade", source="", description="",
            row={"time": "09:30:00"})
        TI.mark_covered([a], {"m": [tx("2025-01-02", 10)]},
                        own_out={"m": [("2025-04-01", "SYN.TO", 10.0)]})
        self.assertTrue(a.covered)


class TestBrokerRowSplits(_Case):
    """A broker row's split scales the units a broker sale can read, down as
    well as up, and one split reported twice is one event (CA-CORP-12)."""

    @staticmethod
    def _tx(day, q, sym="SYN.TO", action="BUYSELL", new=""):
        return {"action": action, "date": day, "time": "09:30:00",
                "symbol": sym, "quantity": q, "symbol_new": new}

    def _covered(self, tt, broker, qty=10.0):
        a = TI.Arrival(
            account="m", symbol="SYN.TO", key="SYN.TO", date="2025-06-01",
            time="09:30:00", quantity=qty, currency="CAD", book_value=100.0,
            broker="questrade", source="", description="",
            row={"time": "09:30:00"})
        TI.mark_covered([a], {"m": tt}, broker_rows={"m": broker})
        return a

    def test_a_reverse_split_leaves_fewer_units_to_sell(self):
        tx = self._tx
        a = self._covered([tx("2025-03-10", 10)],
                          [tx("2025-01-02", 10),
                           tx("2025-02-03", 0.5, action="SPLIT"),
                           tx("2025-04-01", -15)])
        self.assertEqual(a.status, "book_value")

    def test_control_a_reverse_split_sale_of_the_brokers_units_only(self):
        tx = self._tx
        a = self._covered([tx("2025-03-10", 10)],
                          [tx("2025-01-02", 10),
                           tx("2025-02-03", 0.5, action="SPLIT"),
                           tx("2025-04-01", -5)])
        self.assertTrue(a.covered)

    def test_a_split_on_a_tt_line_and_a_broker_row_is_one_event(self):
        # 40 units after the one split, all sold: the arrival is new units.
        arrive = _row("2025-06-02", "TF6", "SYN.TO", 20, 0, 0, kind="Transfers",
                      desc="SYN CORP TRANSFER BOOK VALUE 200.00")
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _tt("2025-01-05")
            + "SPLIT 2025-02-03 09:30:00 SYN.TO SYN.TO 2\n",
            "margin/q.csv": _QH + _buy("2025-01-02", 10, 200)
            + _split("2025-02-03", 10) + _sell("2025-04-01", 40, 600) + arrive
            + _sell("2025-07-02", 20, 500)}),
            ["2025-06-02"], 600.0, [])

    def test_control_the_same_rows_with_the_broker_row_only(self):
        # The sale sold the broker's 20; the purchase after the split waits.
        # 40 units at 300: half sold for 300, half for 500.
        arrive = _row("2025-06-02", "TF6", "SYN.TO", 20, 0, 0, kind="Transfers",
                      desc="SYN CORP TRANSFER BOOK VALUE 200.00")
        self.assertOutcome(self.outcome({"margin": "taxable"}, {
            "margin/t.tt": _tt("2025-03-10", 20, 100),
            "margin/q.csv": _QH + _buy("2025-01-02", 10, 200)
            + _split("2025-02-03", 10) + _sell("2025-04-01", 20, 300) + arrive
            + _sell("2025-07-02", 20, 500)}),
            [], 500.0, [])


if __name__ == "__main__":
    unittest.main()
