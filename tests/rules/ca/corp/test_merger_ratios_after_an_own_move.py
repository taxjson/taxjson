"""Fix round 2026-10-29, group transfers: moves of your own between two
taxable accounts, and the views of them.

TJ-0269 (the worktree audit's TJ-0380): a US project paired its
          transfer rows twice. The arrival stage (lib/transfer_in.
          arrivals: the same quantity, then the closest date) decided
          which transfer-ins came from outside the books; the lot carry
          (_custody_pairing: the first in leg of the same quantity in
          file order) decided which account received the sender's lots.
          With two candidates they chose different partners: qa's lots
          went to qb, whose own arrival was booked at its book value as
          well (held twice), and qc, the arrival's real partner, sold
          shares it never held (a short). One pairing now: the run's
          own-move plan (own_move_plan, the arrival stage's call) is
          what the lot carry reads.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_transfers
"""
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


class TestMergerAfterOwnMove(unittest.TestCase):
    """TJ-0280 / TJ-0364: a merger whose accounts received different
    ratios weighs each account's holdings just before it — after a move
    of your own between them. The Canadian engine dropped the move's
    legs (one ACB pool, s.47) before it weighed, so the weights were
    the holdings without the move: 100 / 100 rather than 50 / 150.

    Formerly: test_fix_1029_transfers.TestMergerAfterOwnMove"""

    def _book(self):
        from tax_rules.dual import tx
        legs = [tx("TRANSFER", "2025-02-01", "ZZOLD.US", q, 0, account=a,
                   currency="CAD", type="lot_move", time="00:00:00",
                   description="own-account move #1: a -> b (not a "
                               "sale: the ACB stays one pool)",
                   id=f"leg-{a}")
                for a, q in (("a", -50), ("b", 50))]
        return ([tx("BUYSELL", "2025-01-02", "ZZOLD.US", 100, 500,
                    account=a, currency="CAD", id=f"buy-{a}")
                 for a in ("a", "b")] + legs
                + [tx("SPLIT", "2025-03-03", "ZZOLD.US", r, 0, account=a,
                      currency="CAD", symbol_new="ZZNEW.US",
                      id=f"split-{a}")
                   for a, r in (("a", 1), ("b", 1.02))]
                + [tx("BUYSELL", "2025-04-01", "ZZNEW.US", -203, 1200,
                      account="b", currency="CAD", id="sale")])

    @rule(R.CA_CORP_13, R.CA_ACB_OWN_MOVE)
    def test_the_weights_see_the_move(self):
        import contextlib
        import io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = run_gains(self._book(), req=GainsRequest(
                country="canada", year=2025, taxable=True,
                phantom_hint=False))
        # 50 x 1 + 150 x 1.02 = 203 shares, the 1000 of cost: the sale
        # of all 203 gains 200, and nothing is left (no phantom short).
        self.assertEqual(
            (round(sum(t.get("gain", 0) for t in r["transactions"]), 6),
             [i for i in r["inventory"] if abs(i["qty"]) > 1e-9]),
            (200.0, []))
        self.assertIn("holdings-weighted ratio 1.015", err.getvalue())


if __name__ == "__main__":
    unittest.main()
