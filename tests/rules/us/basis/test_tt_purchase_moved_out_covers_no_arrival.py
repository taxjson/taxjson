"""US: a .tt purchase whose units a move of your own took to another account
is spent by that move, as in Canada (lib/transfer_in is one replay for both
countries): it covers no later transfer-in, which is booked at the broker's
stated book value (backlog TJ-5003, TJ-1354; the review's M5).

Synthetic data only; account labels and tickers are made up.
"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R
from _support.qt_close import _QH, _project, _row

_N2 = "99900002"


def _tf(day, qty, bv=None, num="99900001"):
    desc = f"SYN CORP TRANSFER BOOK VALUE {bv}.00" if bv else "SYN CORP TRANSFER"
    return _row(day, "TF6", "SYN.US", qty, 0, 0, kind="Transfers", desc=desc,
                num=num)


def _usd(text):
    return text.replace(",CAD,", ",USD,")


def _outcome(files):
    with tempfile.TemporaryDirectory() as d:
        root = _project(Path(d), {"aa": "taxable", "bb": "taxable"})
        toml = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(
            toml.replace('country = "canada"', 'country = "usa"')
            .replace('base_currency = "CAD"', 'base_currency = "USD"')
            .replace('America/Toronto', 'America/New_York'))
        for rel, text in files.items():
            (root / "inputs" / rel).write_text(text)
        tj(root, "run", "--no-input")
        tj(root, "run", "--no-input")
        gain = json.loads(tj(root, "form-export", "--json").stdout)["gain_unrounded"]
        held = json.loads(tj(root, "list", "--json").stdout)["rows"]
        moves = json.loads(tj(root, "transfers", "--json").stdout)["transfers"]
        return (gain, [(h.get("account"), h["qty"], h.get("cost")) for h in held],
                [(m["account"], m["date"], m["arrival"]) for m in moves
                 if m["quantity"] > 0])


class TestUsProject(unittest.TestCase):
    TT = "BUYSELL 2025-01-05 09:30:00 SYN.US 10 USD 10 100 0\n"
    SELL = _row("2025-06-02", "Sell", "SYN.US", -10, 70, 700)

    @rule(R.US_BASIS_TRANSFER_BV)
    def test_the_purchase_that_moved_out_covers_no_later_arrival(self):
        # a's lot of 10 at 100 moves to b; the May arrival is a lot of 10 at
        # 500 in a, sold for 700.
        gain, held, moves = _outcome({
            "aa/t.tt": self.TT,
            "aa/q.csv": _usd(_QH + _tf("2025-02-10", -10) + _tf("2025-05-05", 10, 500)
                             + self.SELL),
            "bb/q.csv": _usd(_QH + _tf("2025-02-10", 10, num=_N2))})
        self.assertAlmostEqual(gain, 200.0, places=2)
        self.assertEqual([h[:2] for h in held], [("bb", 10.0)])
        self.assertAlmostEqual(held[0][2], 100.0, places=2)
        self.assertEqual(moves, [("bb", "2025-02-10", "own move"),
                                 ("aa", "2025-05-05", "book value")])

    def test_control_no_move_out_the_purchase_covers(self):
        gain, held, moves = _outcome({
            "aa/t.tt": self.TT,
            "aa/q.csv": _usd(_QH + _tf("2025-05-05", 10, 500) + self.SELL),
            "bb/q.csv": _usd(_QH)})
        self.assertAlmostEqual(gain, 600.0, places=2)
        self.assertEqual(held, [])
        self.assertEqual(moves, [("aa", "2025-05-05", ".tt covers")])


if __name__ == "__main__":
    unittest.main()
