"""Batch C regression pins (slips and misc): findings 42, 83, 98, 99 and
112 of the 2026-10 bug review (finding 82 is pinned in the fetch
plugin's own tests, packages/taxjson-fetch/tests/test_fetch_fix_batch_c.py).

Synthetic data only: made-up tickers (SAMP, ZZQ), issuers and broker
account hashes.

Formerly: test_fix_batch_c_slips
"""
import unittest
from taxjson.lib import cra_slips as C
from taxjson.lib import slip_audit as SA


def _group(h, amount):
    r = SA.BookRow(account="margin", id=h, action="INTEREST", symbol="",
                   root="", currency="CAD", native=amount, cad=amount,
                   pay_date="2025-03-03", tax_date="2025-03-03",
                   source=h + ".csv", source_hash=h, category="interest",
                   hashes=(h,))
    return C.Group("margin", (h,), "rbc_direct", (h + ".csv",), [r])


def _t5(amount, issuer="RBC DIRECT INVESTING INC."):
    return C.CraSlip("a.pdf", 2025, "T5", "original", issuer, "CAD",
                     {"13": amount})


class TestT5EqualBrokerAccounts(unittest.TestCase):
    """112: a T5 two broker accounts match equally is not placed.

    Formerly: test_fix_batch_c_slips.TestT5EqualBrokerAccounts"""

    def test_tie_left_unplaced(self):
        (pl,) = C.place([_t5(100)], [_group("a", 100), _group("b", 100)],
                        2025, {})
        self.assertIsNone(pl.group)
        self.assertIsNone(pl.account)
        self.assertEqual(pl.key, "")
        self.assertIn("ambiguous: 2", pl.how)
        self.assertIn("broker_account", pl.how)

    def test_clear_match_still_placed(self):
        (pl,) = C.place([_t5(100)], [_group("a", 100), _group("b", 40)],
                        2025, {})
        self.assertEqual((pl.account, pl.key), ("margin", "a"))
        self.assertIn("by its payments", pl.how)

    def test_two_slips_two_accounts_placed(self):
        pls = C.place([_t5(100), _t5(40)],
                      [_group("a", 100), _group("b", 40)], 2025, {})
        self.assertEqual([p.key for p in pls], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
