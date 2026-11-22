"""Audit batch A (2026-10): the crypto and cash books.

74: a Coinbase Buy / Sell of a stablecoin priced in CAD with a blank
Total read the Total as 0 in the FX-on-cash ledger v2 reader
(`coinbase_cash_events`, now the parse's) and the crypto-sends pool
(`coinbase_pool_flows`): a zero-cost acquisition. Both now derive it as
the parser does (Subtotal +/- fee, else quantity x price +/- fee) and
refuse, naming the row, when nothing is there to derive it from.

79: `crypto_sends.build_report` added up each export's stablecoin pool
flows, so two overlapping Kraken ledgers (or Coinbase exports) counted
the shared entries twice. Each flow now carries its identity (the
Kraken refid, the Coinbase ID or row) and overlapping files are read
once; genuine same-content rows in one file still both count.

Finding 75 (Questrade cash conversions in a newest-first export) is
pinned in
rules.ca.fx.test_foreign_cash_inputs_read_or_refused.TestQuestradeCashEvents.

Every fixture is SYNTHETIC: invented ids, amounts and dates.

Formerly: test_fix_audit_batch_a
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _support.crypto_send_flows import CB_HEADER, _UTC

from taxjson.lib import crypto_sends as cs
# The Coinbase parse builds its cash events (PLAN step 16 commit 8).
from _support.parser_cash import parsed_cash_events


def _write(tmp, name, text):
    p = Path(tmp) / name
    p.write_text(text)
    return p


# ------------------------------------------------- 74: blank Coinbase Total

class TestCoinbaseBlankTotal(_UTC):
    """Formerly: test_fix_audit_batch_a.TestCoinbaseBlankTotal"""
    def _cash(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            evs = parsed_cash_events("coinbase", _write(tmp, "cb.csv",
                                                          CB_HEADER + rows))
        return [(e["from_ccy"], e["from_amt"], e["to_ccy"], e["to_amt"])
                for e in evs if e["kind"] == "FXCONV"]

    def _pool(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            flows = cs.coinbase_pool_flows(_write(tmp, "cb.csv",
                                                  CB_HEADER + rows))
        return [(f["units"], round(f["cad"], 2)) for f in flows]

    @rule(R.CA_FX_07)
    def test_cash_events_derive_a_blank_total(self):
        self.assertEqual(self._cash(
            # Subtotal + fee on a buy
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,1.5,150,,1.5,"
            "Bought 100 USDC\n"
            # no Subtotal either: quantity x price + fee
            "SYN-2,2025-02-04 16:00:00 UTC,Buy,USDC,10,CAD,1.4,,,0.5,"
            "Bought 10 USDC\n"
            # Subtotal - fee on a sale
            "SYN-3,2025-02-05 16:00:00 UTC,Sell,USDC,-100,CAD,1.5,150,,1.5,"
            "Sold 100 USDC\n"), [
            ("CAD", 151.5, "USD", 100.0),
            ("CAD", 14.5, "USD", 10.0),
            ("USD", 100.0, "CAD", 148.5)])

    @rule(R.CA_FX_07)
    def test_same_currency_fee_is_a_cash_spend(self):
        """2026-10 audit #73: USDC bought or sold for USD is no
        conversion, but its fee is US dollars spent."""
        with tempfile.TemporaryDirectory() as tmp:
            evs = parsed_cash_events("coinbase", _write(
                tmp, "cb.csv", CB_HEADER
                + "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,USD,1,100,"
                  "105,5,Bought 100 USDC\n"
                + "SYN-2,2025-02-04 16:00:00 UTC,Sell,USDC,-50,USD,1,50,"
                  "48,2,Sold 50 USDC\n"
                + "SYN-3,2025-02-05 16:00:00 UTC,Buy,USDC,10,USD,1,10,"
                  "10,0,Bought 10 USDC\n"))
        self.assertEqual([e["kind"] for e in evs], ["FLOW", "FLOW"])
        self.assertEqual([(e["currency"], e["amount"]) for e in evs],
                         [("USD", -5.0), ("USD", -2.0)])

    def test_cash_events_refuse_an_underivable_total(self):
        with self.assertRaises(ValueError) as cm:
            self._cash("SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,,,,"
                       "0,Bought 100 USDC\n")
        msg = str(cm.exception)
        self.assertIn("cb.csv:2", msg)
        self.assertIn("Total cell is blank", msg)
        self.assertIn("Fill in the Total", msg)

    def test_a_given_total_is_kept(self):
        self.assertEqual(self._cash(
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,1.5,150,151.5,"
            "1.5,Bought 100 USDC\n"), [("CAD", 151.5, "USD", 100.0)])

    @rule(R.CA_CRYPTO_08)
    def test_pool_derives_a_blank_total(self):
        self.assertEqual(self._pool(
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,1.5,150,,1.5,"
            "Bought 100 USDC\n"
            "SYN-2,2025-02-04 16:00:00 UTC,Buy,USDC,10,CAD,1.4,,,0.5,"
            "Bought 10 USDC\n"), [(100.0, -151.5), (10.0, -14.5)])

    def test_pool_refuses_an_underivable_total(self):
        with self.assertRaises(ValueError) as cm:
            self._pool("SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,CAD,,,,"
                       "0,Bought 100 USDC\n")
        self.assertIn("cb.csv:2", str(cm.exception))

    def test_pool_usd_buy_needs_no_total(self):
        # A USDC bought for US dollars is one currency for itself
        # (backlog TJ-0499): with no Total to derive the spread from it
        # is taken at par — no flow, and not refused.
        self.assertEqual(self._pool(
            "SYN-1,2025-02-03 16:00:00 UTC,Buy,USDC,100,USD,,,,0,"
            "Bought 100 USDC\n"), [])


if __name__ == "__main__":
    unittest.main()
