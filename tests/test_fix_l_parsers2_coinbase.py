"""Low-round parsers2 fixes: Coinbase.

R1-115 (truncated rows refused), R1-309 (a real Send carries a negative
quantity), S055-06 (a Send/Receive with no quantity is refused),
S056-09 (the Convert refusal carries its own workaround), S056-12 (a
coin-moving row of an unknown type is UNBOOKED), S056-16 (staking value:
Subtotal wins; no Subtotal -> qty x price), S056-18 (a dust convert's
excess fee reaches the loss). Synthetic data only.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage

_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n")


def _parse(body, header=_HDR):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "cb_2025.csv"
        f.write_text("Transactions\n" + header + body)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parser = CoinbaseBrokerage()
            tx = parser.parse_file(f)
        return tx, err.getvalue(), parser


_BUY = ("a1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,$200.00,$400.00,"
        "$402.00,$2.00,Bought 2 SOL\n")


class TestTruncatedRows(unittest.TestCase):
    """R1-115."""

    def test_two_cell_tail_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_BUY + "a9,2025-07-01 00:00:00 UTC\n")
        self.assertIn("truncated", str(cm.exception))

    def test_row_cut_before_the_total_refused(self):
        with self.assertRaises(ValueError):
            _parse(_BUY + "a9,2025-07-01 00:00:00 UTC,Sell,SOL,-1,CAD\n")

    def test_blank_line_is_ignored(self):
        tx, _, _ = _parse(_BUY + ",,,,,,,,,,\n")
        self.assertEqual(len(tx), 1)


class TestSends(unittest.TestCase):

    def test_real_send_with_negative_quantity_is_a_transfer(self):
        # R1-309: Coinbase exports a Send's quantity NEGATIVE.
        tx, err, _ = _parse(
            _BUY + "a2,2025-06-02 12:00:00 UTC,Send,SOL,-1.0001,CAD,"
                   "$210.00,$210.02,$210.02,$0.00,Sent 1.0001 SOL\n")
        tr = [t for t in tx if t["action"] == "TRANSFER"]
        self.assertEqual(len(tr), 1)
        self.assertAlmostEqual(tr[0]["quantity"], -1.0001)
        self.assertNotIn("unclassified", err)

    def test_send_with_blank_quantity_refused(self):
        # S055-06: it fell to the "unclassified type" skip.
        with self.assertRaises(ValueError) as cm:
            _parse(_BUY + "a2,2025-06-02 12:00:00 UTC,Receive,ETH,,CAD,"
                          "$2000.00,,,$0.00,\n")
        self.assertIn("no Quantity Transacted", str(cm.exception))


class TestUnknownTypes(unittest.TestCase):
    """S056-12."""

    def test_coin_moving_unknown_type_is_unbooked(self):
        tx, err, parser = _parse(
            _BUY + "a3,2025-06-03 12:00:00 UTC,Airdrop,ETH,0.1,CAD,"
                   "$3400.00,$340.00,$340.00,$0.00,\n")
        self.assertEqual(len(tx), 1)
        self.assertIn("warning: UNBOOKED: Coinbase", err)
        self.assertIn("Airdrop x1", err)
        self.assertTrue(parser.lint_findings)

    def test_known_non_event_is_not_unbooked(self):
        _, err, _ = _parse(
            _BUY + "a3,2025-06-03 12:00:00 UTC,Deposit,CAD,500,CAD,"
                   "$1.00,$500.00,$500.00,$0.00,\n")
        self.assertNotIn("UNBOOKED", err)


class TestConvert(unittest.TestCase):

    def test_unrecognised_convert_carries_its_own_workaround(self):
        # S056-09: it pointed at a KNOWN_ISSUES entry that is gone.
        with self.assertRaises(ValueError) as cm:
            _parse("a4,2025-06-03 12:00:00 UTC,Convert,BTC,0.01,CAD,"
                   "$90000.00,$900.00,$910.00,$10.00,Swapped BTC for ETH\n")
        msg = str(cm.exception)
        self.assertNotIn("KNOWN_ISSUES", msg)
        self.assertNotIn("USD-denominated", msg)
        self.assertIn(".tt file", msg)

    def test_dust_convert_to_stablecoin_books_the_excess_fee(self):
        # S056-18: Subtotal 0.50, fee 0.99 -> proceeds -0.49, not 0.
        tx, _, _ = _parse(
            "a5,2025-06-01 12:00:00 UTC,Buy,ETH,0.001,CAD,$3000.00,$3.00,"
            "$3.00,$0.00,\n"
            "a6,2025-06-02 12:00:00 UTC,Convert,ETH,0.001,CAD,$500.00,"
            "$0.50,$1.49,$0.99,Converted 0.001 ETH to 0.36 USDC\n")
        sell = [t for t in tx if t["quantity"] < 0][0]
        self.assertAlmostEqual(sell["net_amount"], -0.49)
        self.assertAlmostEqual(sell["fee"], 0.99)


class TestStakingValue(unittest.TestCase):
    """S056-16."""

    def test_subtotal_wins_over_qty_times_price(self):
        tx, _, _ = _parse(
            "a7,2025-07-18 18:45:04 UTC,Staking Income,ZZC,0.00189506,CAD,"
            "$200.00,$0.38,$0.55,$0.17,\n")
        div = [t for t in tx if t["action"] == "DIVIDEND"][0]
        self.assertAlmostEqual(div["net_amount"], 0.38)

    def test_no_subtotal_column_uses_qty_times_price_not_total(self):
        hdr = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
               "Price Currency,Price at Transaction,"
               "Total (inclusive of fees and/or spread),Fees and/or Spread,"
               "Notes\n")
        tx, _, _ = _parse(
            "a8,2025-07-18 18:45:04 UTC,Staking Income,ZZC,0.2,CAD,"
            "$200.00,$60.00,$20.00,\n", header=hdr)
        div = [t for t in tx if t["action"] == "DIVIDEND"][0]
        buy = [t for t in tx if t["action"] == "BUYSELL"][0]
        self.assertAlmostEqual(div["net_amount"], 40.00)    # not 60.00
        self.assertAlmostEqual(buy["net_amount"], 40.00)


if __name__ == "__main__":
    unittest.main()
