"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as RID


# ------------------------------------------------------------- TJ-0221

class TestFxCashV1RewardPricedInBase(unittest.TestCase):
    """TJ-0221: ledger v1 read a USDC reward priced in CAD as CAD
    income: the 5 US dollars never came in, and a later spend of them
    was an overdraft. It now books them as v2 does (TJ-0038).

    Formerly: test_fix_1029_messages.TestFxCashV1RewardPricedInBase"""

    REWARD = {"action": "DIVIDEND", "description": "Staking Reward",
              "symbol": "USDC", "quantity": 5.0, "currency": "CAD",
              "price": 1.37, "gross_amount": 6.85, "net_amount": 6.85,
              "date": "2025-07-02", "date_settle": "2025-07-02",
              "account": "crypto", "source": "cb.csv"}

    @staticmethod
    def _ledger(rows, country="canada"):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        return build_ledger(rows, "CAD", {}, 2025, country=country,
                            rate_of=lambda cur, on: 1.40)

    @rule(RID.CA_FX_07)
    def test_the_rewards_dollars_come_in_at_the_incomes_value(self):
        doc = self._ledger([self.REWARD])
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 5.0, "acb": 6.85}})
        self.assertEqual(doc["per_currency"]["USD"]["acquired"], 5.0)
        # Priced in USD: at the day's rate, as before.
        doc = self._ledger([dict(self.REWARD, currency="USD", price=1.0,
                                 gross_amount=5.0, net_amount=5.0)])
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 5.0, "acb": 7.0}})

    def test_a_us_project_keeps_the_stablecoin_as_property(self):
        doc = self._ledger([self.REWARD], country="usa")
        self.assertEqual(doc["pools_year_end"], {})


if __name__ == "__main__":
    unittest.main()
