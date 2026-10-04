"""Low-round parsers2 fixes: Coinbase.

R1-115 (truncated rows refused), R1-309 (a real Send carries a negative
quantity), S055-06 (a Send/Receive with no quantity is refused),
S056-09 (the Convert refusal carries its own workaround), S056-12 (a
coin-moving row of an unknown type is UNBOOKED), S056-16 (staking value:
Subtotal wins; no Subtotal -> qty x price), S056-18 (a dust convert's
excess fee reaches the loss). Synthetic data only.
"""
import os
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from tax_rules import rule


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

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


@rule("CA-INC-04")
@rule("US-INC-02")
class TestStakingValue(unittest.TestCase):
    """S056-16."""

    def test_subtotal_wins_over_qty_times_price(self):
        tx, _, _ = _parse(
            "a7,2025-07-18 18:44:13 UTC,Staking Income,ZZC,0.00189506,CAD,"
            "$200.00,$0.38,$0.55,$0.17,\n")
        div = [t for t in tx if t["action"] == "DIVIDEND"][0]
        self.assertAlmostEqual(div["net_amount"], 0.38)

    def test_no_subtotal_column_uses_qty_times_price_not_total(self):
        hdr = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
               "Price Currency,Price at Transaction,"
               "Total (inclusive of fees and/or spread),Fees and/or Spread,"
               "Notes\n")
        tx, _, _ = _parse(
            "a8,2025-07-18 18:44:13 UTC,Staking Income,ZZC,0.2,CAD,"
            "$200.00,$60.00,$20.00,\n", header=hdr)
        div = [t for t in tx if t["action"] == "DIVIDEND"][0]
        buy = [t for t in tx if t["action"] == "BUYSELL"][0]
        self.assertAlmostEqual(div["net_amount"], 40.00)    # not 60.00
        self.assertAlmostEqual(buy["net_amount"], 40.00)


class TestSendNetworkFeeListed(unittest.TestCase):
    """R1-26: a Coinbase Send carries its network fee inside the sent
    quantity; the arrival on Kraken is short by it. crypto-sends lists it
    (and, since the owner decision, books it: test_fix_d_crypto_drip)."""

    def _report(self, country):
        import json
        from taxjson.lib.crypto_sends import build_report
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "work").mkdir()

            def side(broker, rows):
                (root / "work" / f"c_{broker}_transfers.json").write_text(
                    json.dumps({"metadata": {"kind": "transfer_sidecar",
                                             "brokerage": broker},
                                "transactions": rows}))
            side("coinbase", [
                {"action": "TRANSFER", "date": "2025-06-02",
                 "time": "08:00:00", "symbol": "SOL", "quantity": -1.0001,
                 "price": 150.0, "currency": "CAD", "fee": 0.0,
                 "description": "Send", "id": "cb1"},
                {"action": "TRANSFER", "date": "2025-06-02",
                 "time": "08:00:00", "symbol": "USDC", "quantity": -100.5,
                 "price": 1.0, "currency": "USD", "fee": 0.0,
                 "description": "Send", "id": "cb2"}])
            side("kraken", [
                {"action": "TRANSFER", "date": "2025-06-02",
                 "time": "08:20:00", "symbol": "SOL", "quantity": 1.0,
                 "currency": "USD", "fee": 0.0, "description": "deposit",
                 "id": "kr1"},
                {"action": "TRANSFER", "date": "2025-06-02",
                 "time": "08:20:00", "symbol": "USDC", "quantity": 100.0,
                 "currency": "USD", "fee": 0.0, "description": "deposit",
                 "id": "kr2"}])
            cfg = {"settings": {"country": country,
                                "base_currency": "CAD" if country == "canada"
                                else "USD"},
                   "accounts": {"c": {"crypto": True}}}
            return build_report(root, cfg, None, with_pool=False)

    def test_short_arrival_is_listed(self):
        rep = self._report("canada")
        short = rep["accounts"]["c"]["network_fees"]
        # The USDC send is US-dollar cash in a Canada book: not a coin.
        self.assertEqual([s["symbol"] for s in short], ["SOL"])
        self.assertAlmostEqual(short[0]["quantity"], 0.0001)

    def test_us_project_lists_the_stablecoin_too(self):
        rep = self._report("usa")
        short = rep["accounts"]["c"]["network_fees"]
        self.assertEqual(sorted(s["symbol"] for s in short),
                         ["SOL", "USDC"])


if __name__ == "__main__":
    unittest.main()
