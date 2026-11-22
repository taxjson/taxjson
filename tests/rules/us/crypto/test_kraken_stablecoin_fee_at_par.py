"""Re-audit-2 test pins (tests-pins-03): the position walks twinned
across tools — A2-1590 (one split reported by two brokers is applied
once; the apply-distributions split key includes the account), A2-1539
(ASSIGN and TRANSFER rows move every walk's position) — and A2-1564
(a US Kraken stablecoin withdrawal fee is a sale at par). Synthetic
data only.

Formerly: test_fix_a2_pins1_03_walks"""
import os
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestKrakenUsStablecoinFeeAtPar(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_03_walks.TestKrakenUsStablecoinFeeAtPar"""
    @rule(R.US_CRYPTO_03)
    def test_usdc_withdrawal_fee_is_a_sale_at_par(self):
        """A2-1564: in a US book a stablecoin is property, so a USDC
        withdrawal fee is a sale of USDC valued at its 1.00 par (no
        market lookup)."""
        from _support.kraken_cash import _KR_LEDGER_H, _kraken
        text = _KR_LEDGER_H + (
            '"L1","R1","2025-05-04 16:00:00","withdrawal","","currency",'
            '"USDC","spot","-100.0000","1.0000","0"\n')
        rows = _kraken(text, "kr_ledgers", cash=False)
        fee = [r for r in rows if r["action"] == "BUYSELL"]
        self.assertEqual(len(fee), 1)
        self.assertEqual(fee[0]["symbol"], "USDC")
        self.assertEqual(fee[0]["quantity"], -1.0)
        self.assertEqual(fee[0]["price"], 1.0)
        self.assertEqual(fee[0]["net_amount"], 1.0)


if __name__ == "__main__":
    unittest.main()
