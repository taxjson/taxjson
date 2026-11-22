"""Fix round 2026-10-29 (round 4), group views: derived views and
reports.

TJ-0279  <account>.sum asked for a `.tt OPENING cost=unknown` line
         (MANUAL REPORTING REQUIRED) for a sale of units moved in from
         another of your accounts: their cost is in the blended figures
         (<account>_wash.sum, `tjs sum`). They are a block of their
         own now, and its holdings table says "unknown", not 0.00.
TJ-0598  reports/<account>_holdings.toml: units moved in from another
         of your accounts take the cost the run has after the passes
         across accounts (`cost_source = "blended"`), where round 3
         left it unknown.
TJ-0629  `tjs stats` / `win-rates` counted a registered account's
         withdrawal in kind (a TRANSFER out, `transfers = true`, which
         the books book as a sale at its value) as a closed trade, with
         a win or a loss. It is no trade: its P/L is counted apart.
TJ-0634  The US option-replacement warning's covered_shares carried
         float noise (9.999999999999998) into the gains JSON: the loss's
         open part is a money ratio's, and quantities are saved
         unrounded. Share counts are now at the engine's share
         precision (six decimals).

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r4_views
"""
import contextlib
import io
import os
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _flat(text: str) -> str:
    return " ".join(text.split())


def _own_view(country, rows):
    """run_gains on one account's own book (qb): rows of (id, action,
    date, quantity, net amount[, description])."""
    from taxjson.model.transaction import TaxTransaction as T
    from taxjson.lib.pipeline import GainsRequest, run_gains
    sfx = "TO" if country == "canada" else "US"
    cur = "CAD" if country == "canada" else "USD"
    txs = []
    for i, a, d, q, n, *desc in rows:
        txs.append(T(id=i, action=a, date=d, date_settle=d, time="10:00:00",
                     symbol=f"ZZQ.{sfx}", quantity=q, net_amount=n,
                     price=abs(n / q) if q and n else 0.0, currency=cur,
                     account="qb",
                     **({"type": "lot_move", "description": desc[0]}
                        if desc else {})))
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(txs, [], req=GainsRequest(
            country=country, year=2024, taxable=True, phantom_hint=False))


_MOVE_IN = ("mv", "TRANSFER", "2024-03-01", 100, 0.0,
            "own-account move #1: qa -> qb (test)")


class TestTheEnginesNameTheMove(unittest.TestCase):
    """TJ-0279: a sale of units whose unknown cost an own move alone
    brought carries `own_move_from`; one whose pool (Canada) or lot
    (US) also holds an opening at an unknown cost does not.

    Formerly: test_fix_1029_r4_views.TestTheEnginesNameTheMove"""

    def _froms(self, country, rows):
        doc = _own_view(country, rows)
        return [(m["qty"], m.get("own_move_from"))
                for m in doc.get("manual_reporting_required") or []]

    def _a_move_alone(self, country):
        self.assertEqual(
            self._froms(country, [_MOVE_IN, ("s", "BUYSELL", "2024-06-03",
                                             -40, 120.0)]),
            [(40.0, ["qa"])])

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada_a_move_alone(self):
        self._a_move_alone("canada")

    @rule(R.US_BASIS_05)
    def test_usa_a_move_alone(self):
        self._a_move_alone("usa")

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada_a_move_and_an_opening(self):
        # One pool: the sale's unknown cost is not the move's alone.
        self.assertEqual(self._froms("canada", [
            ("ob", "OPENING_BALANCE", "2024-01-02", 10, 0.0), _MOVE_IN,
            ("s", "BUYSELL", "2024-06-03", -40, 120.0)]), [(40.0, None)])

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada_the_pool_drains_between(self):
        # The moved units all sold (the pool flat), then an opening at
        # an unknown cost: its sale is no own move's.
        self.assertEqual(self._froms("canada", [
            _MOVE_IN, ("s1", "BUYSELL", "2024-06-03", -100, 300.0),
            ("ob", "OPENING_BALANCE", "2024-07-02", 10, 0.0),
            ("s2", "BUYSELL", "2024-08-01", -10, 30.0)]),
            [(100.0, ["qa"]), (10.0, None)])

    @rule(R.US_BASIS_05)
    def test_usa_each_lot_its_own(self):
        # FIFO: the opening's lot first, then the moved lot.
        self.assertEqual(self._froms("usa", [
            ("ob", "OPENING_BALANCE", "2024-01-02", 10, 0.0), _MOVE_IN,
            ("s", "BUYSELL", "2024-06-03", -40, 120.0)]),
            [(10.0, None), (30.0, ["qa"])])


class TestTheManualSectionSplits(unittest.TestCase):
    """TJ-0279: a .sum's unknown-cost rows: the ones an own move brought
    in their own block, the rest MANUAL REPORTING REQUIRED as before.

    Formerly: test_fix_1029_r4_views.TestTheManualSectionSplits"""

    def test_both_blocks(self):
        from taxjson.bin.taxjson_form_export import manual_section
        rows = [{"symbol": "ZZQ.TO", "date": "2024-06-03", "qty": 40.0,
                 "proceeds": 120.0, "account": "qb", "currency": "CAD",
                 "own_move_from": ["qa"]},
                {"symbol": "ZZK.TO", "date": "2024-06-04", "qty": 5.0,
                 "proceeds": 50.0, "account": "qb", "currency": "CAD"}]
        text = _flat("\n".join(manual_section(rows, "", "canada")))
        self.assertIn("MANUAL REPORTING REQUIRED — 1 sale(s) with no "
                      "purchase in your files (unknown cost, .tt OPENING "
                      "cost=unknown), proceeds 50.00 CAD", text)
        self.assertIn("ZZK.TO 2024-06-04 5 50.00 qb", text)
        self.assertIn("MOVED IN FROM YOUR OWN ACCOUNT — 1 sale(s) of units "
                      "moved in from qa, proceeds 120.00 CAD", text)
        # Without own-move rows: the block as before.
        plain = manual_section(rows[1:], "CAD")
        self.assertTrue(plain[0].startswith("MANUAL REPORTING REQUIRED"))
        self.assertNotIn("MOVED IN", "\n".join(plain))


if __name__ == "__main__":
    unittest.main()
