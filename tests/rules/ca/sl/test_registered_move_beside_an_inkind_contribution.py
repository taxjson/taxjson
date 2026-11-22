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
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestRegisteredMoveInACluster(unittest.TestCase):
    """TJ-0362: the netter of moves between your registered accounts
    judged a symbol's transfer cluster whole: a move rrsp -> rrsp2 with
    another transfer of the symbol within 35 days (a contribution in
    kind into the TFSA) did not net to zero, so the move's legs stayed
    custody rows (under transfers_as_acquisitions = true, a disposal
    and an acquisition). Each move in such a cluster now nets on its
    own.

    Formerly: test_fix_1029_transfers.TestRegisteredMoveInACluster"""

    def _rows(self):
        from tax_rules.dual import tx
        return [tx("TRANSFER", "2026-03-02", "QZT.TO", -100, 0,
                   account="rrsp", currency="CAD", id="out"),
                tx("TRANSFER", "2026-03-04", "QZT.TO", 100, 0,
                   account="rrsp2", currency="CAD", id="in"),
                tx("TRANSFER", "2026-03-20", "QZT.TO", 50, 2500,
                   account="tfsa", currency="CAD", id="contribution")]

    def test_the_move_nets_and_the_contribution_stays(self):
        from taxjson.lib.pipeline import _net_cross_account_transfers
        netted: list = []
        segs: list = []
        out = _net_cross_account_transfers(self._rows(), netted_out=netted,
                                           near_trade_guard=False,
                                           netted_segments=segs)
        self.assertEqual([t.id for t in out], ["contribution"])
        self.assertEqual(sorted(t.id for t in netted), ["in", "out"])
        self.assertEqual([[t.id for t in s] for s in segs], [["out", "in"]])

    def test_a_guard_still_refuses_the_move(self):
        # A taxable trade of the symbol near the move (the strict
        # policy's guard) still leaves it un-netted.
        from tax_rules.dual import tx
        from taxjson.lib.pipeline import _net_cross_account_transfers
        main = [tx("BUYSELL", "2026-03-10", "QZT.TO", -10, 400,
                   account="margin", currency="CAD")]
        out = _net_cross_account_transfers(self._rows(),
                                           main_transactions=main)
        self.assertEqual(len(out), 3)

    def test_two_moves_of_one_size_pair_by_the_closest_dates(self):
        from tax_rules.dual import tx
        from taxjson.lib.pipeline import _net_cross_account_transfers
        rows = [tx("TRANSFER", d, "QZT.TO", q, 0, account=a,
                   currency="CAD", id=i)
                for d, q, a, i in (
                    ("2026-03-02", -100, "rrsp", "o1"),
                    ("2026-03-03", 100, "rrsp2", "i1"),
                    ("2026-03-20", -100, "rrsp2", "o2"),
                    ("2026-03-21", 100, "tfsa", "i2"),
                    ("2026-03-25", 30, "tfsa", "c"))]
        netted: list = []
        out = _net_cross_account_transfers(rows, netted_out=netted,
                                           near_trade_guard=False)
        self.assertEqual([t.id for t in out], ["c"])
        self.assertEqual(len(netted), 4)


if __name__ == "__main__":
    unittest.main()
