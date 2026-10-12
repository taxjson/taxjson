"""Issue #91: a superficial-loss deferral parked in an empty TARGET pool
is recovered when a later rename/merger brings shares into it.

The loss on AAA is denied because BBB (bought 30 days before, renamed
AAA five days after the sale) is the replacement. At the denial AAA's
pool is empty, so the denied amount waits in the pool; before the fix
the rename added BBB's shares and cost but left the waiting amount
parked, so the loss was denied and never recovered (gains overstated
by the denial). Synthetic data only.
"""
import unittest

from tax_rules import rule
from tax_rules.dual import gains_both, tx

import test_engine_invariants as INV


def _book(*sales):
    """BBB bought, AAA bought and sold at a 2,000 loss, BBB renamed AAA
    on Jan 25, then the given AAA sales (date, qty, proceeds)."""
    rows = [
        tx("BUYSELL", "2025-01-06", "BBB.US", 100, 5000,
           settle="2025-01-07"),
        tx("BUYSELL", "2025-01-10", "AAA.US", 100, 10000,
           settle="2025-01-13"),
        tx("BUYSELL", "2025-01-20", "AAA.US", -100, 8000,
           settle="2025-01-21"),
        tx("SPLIT", "2025-01-25", "BBB.US", 1.0, 0, symbol_new="AAA.US",
           time="00:00:01"),
    ]
    for d, q, p in sales:
        rows.append(tx("BUYSELL", d, "AAA.US", -q, p, settle=d))
    return rows


def _sales(res):
    return [(g["date"], round(g["qty"], 4), round(g["gain"], 2))
            for g in res["transactions"] if "gain" in g and g.get("qty")]


class TestMergerIntoFlatPoolRecoversDeferral(unittest.TestCase):

    @rule("CA-SL-19")
    @rule("CA-ACB-RENAME")
    @rule("US-WASH-09")
    @rule("US-BASIS-RENAME")
    def test_partial_and_final_sale_recover_the_denied_loss(self):
        r = gains_both(_book(("2025-03-03", 40, 4000),
                             ("2025-04-01", 60, 6000)), year=2025)
        for c in ("canada", "usa"):
            s = r[c]["summary"]
            self.assertAlmostEqual(s["total_disallowed"], 2000.0, 2, c)
            # 18,000 proceeds - 15,000 cost: every denied dollar back.
            self.assertAlmostEqual(s["total_gain"], 3000.0, 2, c)
            self.assertEqual(_sales(r[c]), [
                ("2025-01-20", 100.0, 0.0),
                # 40 of 100 shares at 7,000 (5,000 + the 2,000 denied)
                ("2025-03-03", 40.0, 1200.0),
                ("2025-04-01", 60.0, 1800.0)], c)
            self.assertFalse([i for i in r[c].get("inventory") or []
                              if abs(i["qty"]) > 1e-9], c)

    @rule("CA-SL-19")
    @rule("US-WASH-09")
    def test_partial_sale_leaves_the_rest_of_the_deferral_once(self):
        # Cost and the deferred tally move together: the remaining 60
        # shares carry 4,200 of ACB, of which 1,200 is deferred loss —
        # not 3,000 (deferral lost) nor a tally counted twice.
        r = gains_both(_book(("2025-03-03", 40, 4000)), year=2025)
        for c in ("canada", "usa"):
            inv = [i for i in r[c]["inventory"] if i["symbol"] == "AAA.US"]
            self.assertEqual(len(inv), 1, c)
            self.assertAlmostEqual(inv[0]["qty"], 60.0, 4, c)
            self.assertAlmostEqual(inv[0]["total_cost"], 4200.0, 2, c)
            self.assertAlmostEqual(inv[0]["deferred_wash"], 1200.0, 2, c)
            self.assertAlmostEqual(r[c]["summary"]["total_gain"], 1200.0,
                                   2, c)


class TestConservationFuzzSeeds(unittest.TestCase):
    """The conservation fuzzer's failing seeds from issue #91: each one
    a deferral parked in a flat pool that a rename-merge later filled."""

    SEEDS = (8630, 11607, 17440, 17840, 27279)

    @rule("CA-SL-19")
    @rule("CA-ACB-RENAME")
    def test_canada_seeds_conserve(self):
        chk = INV.TestConservationFuzz("test_canada_fuzz")
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                chk._check_book(seed, "canada")

    @rule("US-WASH-09")
    @rule("US-BASIS-RENAME")
    def test_usa_seeds_conserve(self):
        chk = INV.TestConservationFuzz("test_usa_fuzz")
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                chk._check_book(seed, "usa")


if __name__ == "__main__":
    unittest.main()
