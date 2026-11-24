"""A .tt purchase whose units a move of your own took out of the account is
spent by that move: it covers no later arrival, which is booked at the
broker's book value (backlog TJ-5003, TJ-1354). The posting-lag safeguard
stays: units arriving within SLOW_MOVE_DAYS after the move out answer it
(a transfer row can post that late), and no .tt purchase is spent.

Synthetic data only; account labels and tickers are made up.
"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib import transfer_in as TI
from _support.qt_close import _QH, _project, _row

_N2 = "99900002"


def _tf(day, qty, bv=None, num="99900001"):
    desc = f"SYN CORP TRANSFER BOOK VALUE {bv}.00" if bv else "SYN CORP TRANSFER"
    return _row(day, "TF6", "SYN.TO", qty, 0, 0, kind="Transfers", desc=desc,
                num=num)


def _sell(day, qty, net, num="99900001"):
    return _row(day, "Sell", "SYN.TO", -qty, net / qty, net, num=num)


def _tt(day, qty=10, total=100):
    return f"BUYSELL {day} 09:30:00 SYN.TO {qty} CAD {total // qty} {total} 0\n"


def _outcome(accounts, files):
    """(the arrivals booked, the year's gain, the holdings as (account,
    units, cost), each arrival's status in `tjs transfers --json`)."""
    with tempfile.TemporaryDirectory() as d:
        root = _project(Path(d), accounts)
        for rel, text in files.items():
            (root / "inputs" / rel).write_text(text)
        tj(root, "run", "--no-input")
        tj(root, "run", "--no-input")
        booked = []
        for p in (root / "work").glob("*_transfer_costs.json"):
            booked += [(t["date"][:10], t["quantity"], t["net_amount"])
                       for t in json.loads(p.read_text())["transactions"]
                       if t.get("quantity", 0) > 0]
        gain = json.loads(tj(root, "form-export", "--json").stdout)["gain_unrounded"]
        held = json.loads(tj(root, "list", "--json").stdout)["rows"]
        moves = json.loads(tj(root, "transfers", "--json").stdout)["transfers"]
        return (sorted(booked), gain,
                [(h.get("account"), h["qty"], h.get("cost")) for h in held],
                [(m["account"], m["date"], m["arrival"]) for m in moves
                 if m["quantity"] > 0])


def _arrival(day, qty=10.0):
    return TI.Arrival(
        account="m", symbol="SYN.TO", key="SYN.TO", date=day, time="09:30:00",
        quantity=qty, currency="CAD", book_value=500.0, broker="questrade",
        source="", description="", row={"time": "09:30:00"})


def _tx(day, q):
    return {"action": "BUYSELL", "date": day, "time": "09:30:00",
            "symbol": "SYN.TO", "quantity": q}


class TestInAProject(unittest.TestCase):
    """The entry's repro: a's .tt purchase of 10 for 100 (January 5), its 10
    shares moved to b (February 10, both legs), 10 more arriving in a with a
    book value of 500 (May 5), 10 sold for 700 (June 2)."""

    FILES = {"aa/t.tt": _tt("2025-01-05"),
             "bb/q.csv": _QH + _tf("2025-02-10", 10, num=_N2)}

    @rule(R.CA_ACB_TRANSFER_BV, R.CA_ACB_OWN_MOVE)
    def test_the_purchase_that_moved_out_covers_no_later_arrival(self):
        booked, gain, held, moves = _outcome(
            {"aa": "taxable", "bb": "taxable"},
            dict(self.FILES, **{"aa/q.csv": _QH + _tf("2025-02-10", -10)
                                + _tf("2025-05-05", 10, 500)
                                + _sell("2025-06-02", 10, 700)}))
        # 20 shares at 600 before the sale: 700 less 300.
        self.assertEqual(booked, [("2025-05-05", 10.0, 500.0)])
        self.assertAlmostEqual(gain, 400.0, places=2)
        self.assertEqual([h[:2] for h in held], [("bb", 10.0)])
        self.assertAlmostEqual(held[0][2], 300.0, places=2)
        self.assertEqual(moves, [("bb", "2025-02-10", "own move"),
                                 ("aa", "2025-05-05", "book value")])

    def test_control_an_arrival_inside_the_posting_lag_reads_as_before(self):
        # The arrival posts 15 days after the move out: it may be the row of
        # the units that left, and the .tt purchase the one that waits for it.
        booked, gain, _held, moves = _outcome(
            {"aa": "taxable", "bb": "taxable"},
            dict(self.FILES, **{"aa/q.csv": _QH + _tf("2025-02-10", -10)
                                + _tf("2025-02-25", 10, 500)
                                + _sell("2025-06-02", 10, 700)}))
        self.assertEqual(booked, [])
        self.assertAlmostEqual(gain, 600.0, places=2)
        self.assertEqual(moves[-1], ("aa", "2025-02-25", ".tt covers"))

    @rule(R.CA_ACB_TRANSFER_BV, R.CA_ACB_OWN_MOVE)
    def test_out_and_back_within_the_lag_then_an_outside_arrival(self):
        # Review M1: the shares went to b and came back 19 days later; the May
        # arrival is new shares. 20 at 600 before the sale: 400, 10 left at 300.
        booked, gain, held, moves = _outcome(
            {"aa": "taxable", "bb": "taxable"},
            {"aa/t.tt": _tt("2025-01-05"),
             "aa/q.csv": _QH + _tf("2025-02-10", -10) + _tf("2025-03-01", 10)
             + _tf("2025-05-05", 10, 500) + _sell("2025-06-02", 10, 700),
             "bb/q.csv": _QH + _tf("2025-02-10", 10, num=_N2)
             + _tf("2025-03-01", -10, num=_N2)})
        self.assertEqual(booked, [("2025-05-05", 10.0, 500.0)])
        self.assertAlmostEqual(gain, 400.0, places=2)
        self.assertEqual([h[1] for h in held], [10.0])
        self.assertAlmostEqual(held[0][2], 300.0, places=2)
        self.assertEqual(moves[-1], ("aa", "2025-05-05", "book value"))

    def test_control_no_move_out_the_purchase_covers(self):
        booked, gain, held, _moves = _outcome(
            {"aa": "taxable"},
            {"aa/t.tt": _tt("2025-01-05"),
             "aa/q.csv": _QH + _tf("2025-05-05", 10, 500)
             + _sell("2025-06-02", 10, 700)})
        self.assertEqual(booked, [])
        self.assertAlmostEqual(gain, 600.0, places=2)
        self.assertEqual(held, [])


class TestTheReplay(unittest.TestCase):
    """transfer_in.mark_covered: the units a move out takes."""

    def _covered(self, arrival_day, own_out, tt=(("2025-01-05", 10),), **kw):
        a = _arrival(arrival_day)
        TI.mark_covered([a], {"m": [_tx(d, q) for d, q in tt]},
                        own_out={"m": own_out}, **kw)
        return a.covered, a.covered_units

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_a_move_out_spends_the_purchase(self):
        self.assertEqual(self._covered(
            "2025-05-05", [("2025-02-10", "SYN.TO", 10.0)]), (False, 0.0))

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_part_moved_out_part_covers(self):
        self.assertEqual(self._covered(
            "2025-05-05", [("2025-02-10", "SYN.TO", 4.0)]), (False, 6.0))

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_the_day_after_the_lag_spends(self):
        day = "2025-03-28"          # February 10 + SLOW_MOVE_DAYS + 1
        self.assertEqual(TI.SLOW_MOVE_DAYS, 45)
        self.assertEqual(self._covered(
            day, [("2025-02-10", "SYN.TO", 10.0)]), (False, 0.0))

    def test_control_the_last_day_of_the_lag_does_not(self):
        self.assertEqual(self._covered(
            "2025-03-27", [("2025-02-10", "SYN.TO", 10.0)])[0], True)

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_a_move_of_your_own_back_in_does_not_answer_the_move_out(self):
        # The units that came back are counted as held by other means: the
        # .tt purchase that left is spent all the same (review M1).
        self.assertEqual(self._covered(
            "2025-05-05", [("2025-02-10", "SYN.TO", 10.0)],
            own_in={"m": [("2025-02-20", "SYN.TO", 10.0)]}), (False, 0.0))

    def test_control_an_earlier_arrival_inside_the_lag_answers_the_move_out(self):
        # An arrival from outside 15 days after the move out took the .tt
        # purchase (the late row of the units that left); a second purchase
        # still covers the arrival being read.
        first = _arrival("2025-02-25")
        a = _arrival("2025-05-05")
        TI.mark_covered([first, a], {"m": [_tx("2025-01-05", 10), _tx("2025-03-03", 10)]},
                        own_out={"m": [("2025-02-10", "SYN.TO", 10.0)]})
        self.assertEqual((first.covered, a.covered), (True, True))

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_a_move_in_after_the_lag_does_not(self):
        self.assertEqual(self._covered(
            "2025-06-05", [("2025-02-10", "SYN.TO", 10.0)],
            own_in={"m": [("2025-04-20", "SYN.TO", 10.0)]}), (False, 0.0))

    def test_control_a_purchase_after_the_move_out_is_not_spent(self):
        self.assertEqual(self._covered(
            "2025-05-05", [("2025-02-10", "SYN.TO", 10.0)],
            tt=(("2025-03-03", 10),))[0], True)

    def test_control_units_moved_in_first_leave_first(self):
        # (TJ-1327's shape: the move out takes what moved in.)
        self.assertEqual(self._covered(
            "2025-05-05", [("2025-02-10", "SYN.TO", 10.0)],
            own_in={"m": [("2025-01-20", "SYN.TO", 10.0)]})[0], True)

    def test_control_a_move_on_the_arrival_day_spends_nothing(self):
        self.assertEqual(self._covered(
            "2025-05-05", [("2025-05-05", "SYN.TO", 10.0)])[0], True)


if __name__ == "__main__":
    unittest.main()
