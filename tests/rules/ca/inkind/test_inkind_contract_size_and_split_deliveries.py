"""Audit 2026-10-24, group "inkind": B20, B43, B05, B16, A13, B36, B37,
B14 — in-kind moves, the corporate views, the holdings folder, the
holdings TOML reader and the [[distributions]] ADJUST. Synthetic data
only.

Formerly: test_fix_audit_1024_inkind"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from taxjson.lib import in_kind as ik

from tax_rules import rule
from taxjson.rules import ids as R

OPT = "ZZQ270115C00010000.US"


def _tx(d, q, sym="ZZQ.US", action="BUYSELL", **kw):
    return dict(date=d, date_settle=d, time="10:00:00", account="margin",
                symbol=sym, action=action, quantity=q, net_amount=0,
                currency="USD", **kw)


class TestInKind(unittest.TestCase):
    """Formerly: test_fix_audit_1024_inkind.TestInKind"""

    @rule(R.CA_INKIND_06)
    def test_b20_option_valued_at_the_rows_contract_size(self):
        """B20: In-kind valuations ignore an adjusted option contract
        multiplier — one contract of an adjusted series (multiplier 50)
        quoted at 2 is worth 100, not 200, and its booked price is per
        that size."""
        leg = ik.Leg("margin", "generic",
                     _tx("2026-01-02", -1, sym=OPT, action="TRANSFER",
                         multiplier=50), OPT)
        m = ik.Move(ik.CONTRIBUTION, leg, None, "rrsp", 1, "2026-01-02")
        ik.value([m], lambda m: SimpleNamespace(price=2, currency="USD",
                                                day="2026-01-02"))
        self.assertEqual(m.fmv, 100.0)
        row = ik.booked_rows([m], "canada")["margin"][0]
        self.assertEqual((row["price"], row["net_amount"],
                          row["multiplier"]), (2.0, 100.0, 50))
        # No declared size: the standard 100 still applies.
        leg = ik.Leg("margin", "generic",
                     _tx("2026-01-02", -1, sym=OPT, action="TRANSFER"), OPT)
        m = ik.Move(ik.CONTRIBUTION, leg, None, "rrsp", 1, "2026-01-02")
        ik.value([m], lambda m: SimpleNamespace(price=2, currency="USD",
                                                day="2026-01-02"))
        self.assertEqual(m.fmv, 200.0)
        row = ik.booked_rows([m], "canada")["margin"][0]
        self.assertEqual(row["price"], 2.0)
        self.assertNotIn("multiplier", row)
        # The plan's leg's declared size counts when only it has one.
        plan = ik.Leg("rrsp", "generic",
                      _tx("2026-01-02", 1, sym=OPT, action="TRANSFER",
                          multiplier=10), OPT, "rrsp")
        m = ik.Move(ik.CONTRIBUTION, leg, plan, "rrsp", 1, "2026-01-02")
        ik.value([m], lambda m: SimpleNamespace(price=2, currency="USD",
                                                day="2026-01-02"))
        self.assertEqual(m.fmv, 20.0)
        row = ik.booked_rows([m], "canada")["margin"][0]
        self.assertEqual((row["price"], row["multiplier"]), (2.0, 10.0))

    @rule(R.CA_INKIND_01)
    def test_b43_parts_count_every_candidate_leg(self):
        """B43: In-kind split-delivery matching declares uniqueness after
        discarding candidates — a ninth leg that makes a second set of
        parts makes the match ambiguous (nothing booked), instead of the
        first set being booked as the only one."""
        d = ik.Leg("margin", "generic",
                   _tx("2026-01-02", -10, action="TRANSFER"), "ZZQ.US")

        def cands(qs):
            return [(i, ik.Leg("rrsp", "generic",
                               _tx("2026-01-02" if i < 8 else "2026-01-03",
                                   q, action="TRANSFER"), "ZZQ.US", "rrsp"))
                    for i, q in enumerate(qs)]
        self.assertEqual(ik._parts(d, cands([4, 6, 7, 7, 7, 7, 7, 7, 4]),
                                   10), [])
        # Without the ninth leg the one set is found, whatever its place.
        self.assertEqual(ik._parts(d, cands([7, 7, 7, 7, 7, 7, 4, 6]), 10),
                         [6, 7])
        # A ninth leg that makes no second set changes nothing.
        self.assertEqual(ik._parts(d, cands([4, 6, 7, 7, 7, 7, 7, 7, 9]),
                                   10), [0, 1])
        # Past the search budget nothing is proven unique: [].
        with patch.object(ik, "_PARTS_STEPS", 4):
            self.assertEqual(ik._parts(d, cands([4, 6, 7, 7, 7, 7, 7, 7]),
                                       10), [])


if __name__ == "__main__":
    unittest.main()
