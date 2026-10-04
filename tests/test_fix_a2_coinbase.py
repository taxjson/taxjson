"""Re-audit-2 Coinbase parser fixes (crypto-02 list). Synthetic data only.

A2-0022 / A2-0080 (Coinbase half) / A2-0250: money identity — a row whose
  Subtotal (or Total) contradicts Quantity x Price, whose Total contradicts
  Subtotal +/- fee, or whose Convert quantity contradicts its Notes is
  refused, as every equity parser refuses one (S023-19).
A2-0565 / A2-0997: a sale whose fee exceeds its Subtotal books NEGATIVE
  proceeds whatever sign the Total cell carries.
A2-1023: an explicit $0.00 Buy is refused like a blank one.
A2-0237 / A2-0564 / A2-0567 / A2-1024: a fiat Withdrawal is a recognized
  non-event, like a fiat Deposit.
A2-0566: Deposit / Subscription in a COIN is UNBOOKED, not a non-event.
A2-0249: a row cut inside an optional column (Fees, Notes) is refused.
A2-0584: the swallowed-row error names the line the bad quote opened on.
A2-0998: Convert legs of an export without an ID column share an id stem.
A2-1003 (Coinbase half): a USD-valued Convert / Advanced Trade spending a
  stablecoin away from its peg is warned about.
"""
import os
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n")
_HDR_NOID = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")


def _parse(body, header=_HDR, stablecoins_as_cash=True):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "cb_2025.csv"
        f.write_text("Transactions\n" + header + body)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parser = CoinbaseBrokerage()
            parser.stablecoins_as_cash = stablecoins_as_cash
            tx = parser.parse_file(f)
        return tx, err.getvalue(), parser


_BUY = ("a1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,$200.00,$400.00,"
        "$402.00,$2.00,Bought 2 SOL\n")


class TestConvertIdentity(unittest.TestCase):
    """A2-0022."""

    _OK = ("c1,2025-06-02 12:00:00 UTC,Convert,BTC,-0.05,CAD,$43000.00,"
           "$2150.00,$2160.00,$10.00,Converted 0.05 BTC to 1.2 ETH\n")

    def test_consistent_convert_books(self):
        tx, _, _ = _parse(self._OK)
        self.assertEqual(sorted(t["symbol"] for t in tx), ["BTC", "ETH"])

    def test_ten_x_subtotal_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse("c1,2025-06-02 12:00:00 UTC,Convert,BTC,-0.05,CAD,"
                   "$43000.00,$21500.00,$21510.00,$10.00,"
                   "Converted 0.05 BTC to 1.2 ETH\n")
        self.assertIn("Quantity", str(cm.exception))

    def test_quantity_disagreeing_with_notes_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse("c1,2025-06-02 12:00:00 UTC,Convert,BTC,-5,CAD,"
                   "$430.00,$2150.00,$2160.00,$10.00,"
                   "Converted 0.05 BTC to 1.2 ETH\n")
        self.assertIn("Notes", str(cm.exception))

    def test_asset_naming_the_received_leg_checks_that_leg(self):
        tx, _, _ = _parse("c1,2025-06-02 12:00:00 UTC,Convert,ETH,1.2,CAD,"
                          "$1791.67,$2150.00,$2160.00,$10.00,"
                          "Converted 0.05 BTC to 1.2 ETH\n")
        self.assertEqual(len(tx), 2)


class TestBuySellIdentity(unittest.TestCase):
    """A2-0080 (Coinbase half), A2-0565 / A2-0997, A2-1023."""

    def test_real_shaped_rows_book(self):
        tx, _, _ = _parse(
            _BUY + "a2,2025-06-03 12:00:00 UTC,Advanced Trade Sell,SOL,"
                   "-1,CAD,$210.00,-$210.00,-$209.79,$0.21,"
                   "Sold 1 SOL for 150 USDC on SOL-USDC at 150 USDC/SOL\n")
        sell = [t for t in tx if t["quantity"] < 0][0]
        self.assertAlmostEqual(sell["net_amount"], 209.79)

    def test_sell_total_above_subtotal_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_BUY + "a2,2025-06-03 12:00:00 UTC,Sell,SOL,-1,CAD,"
                          "$7500.00,$7500.00,$7550.00,$50.00,Sold\n")
        self.assertIn("Total", str(cm.exception))

    def test_buy_total_below_subtotal_refused(self):
        with self.assertRaises(ValueError):
            _parse("a1,2025-06-01 12:00:00 UTC,Buy,SOL,30,CAD,$200.00,"
                   "$6000.00,$5950.00,$50.00,Bought\n")

    def test_ten_x_total_refused(self):
        with self.assertRaises(ValueError):
            _parse("a1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,$200.00,"
                   "$4000.00,$4002.00,$2.00,Bought 2 SOL\n")

    def test_fee_above_subtotal_negative_total_books_negative(self):
        for total in ("-$0.49", "$0.49"):
            tx, _, _ = _parse(
                _BUY + f"a2,2025-06-03 12:00:00 UTC,Sell,SOL,-0.0025,CAD,"
                       f"$200.00,$0.50,{total},$0.99,Sold\n")
            sell = [t for t in tx if t["quantity"] < 0][0]
            self.assertAlmostEqual(sell["net_amount"], -0.49, msg=total)

    def test_blank_total_agrees_with_given_total(self):
        tx, _, _ = _parse(_BUY + "a2,2025-06-03 12:00:00 UTC,Sell,SOL,"
                                 "-0.0025,CAD,$200.00,$0.50,,$0.99,Sold\n")
        sell = [t for t in tx if t["quantity"] < 0][0]
        self.assertAlmostEqual(sell["net_amount"], -0.49)

    def test_explicit_zero_buy_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse("a1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,$0.00,$0.00,"
                   "$0.00,$0.00,Bought 2 SOL\n")
        self.assertIn("$0", str(cm.exception))

    def test_dust_sell_with_price_books(self):
        tx, _, _ = _parse(_BUY + "a2,2025-06-03 12:00:00 UTC,Sell,SOL,"
                                 "-0.00001,CAD,$200.00,$0.00,$0.00,$0.00,"
                                 "Sold\n")
        self.assertEqual(len(tx), 2)


class TestStakingIdentity(unittest.TestCase):
    """A2-0250."""

    def test_ten_x_subtotal_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse("s1,2025-06-01 12:00:00 UTC,Staking Income,ETH,0.01,"
                   "CAD,$3000.00,$300.00,$420.00,$120.00,\n")
        self.assertIn("Subtotal", str(cm.exception))

    def test_real_shaped_reward_books(self):
        tx, _, _ = _parse("s1,2025-06-01 12:00:00 UTC,Staking Income,ETH,"
                          "0.01,CAD,$3000.00,$30.00,$42.00,$12.00,\n")
        div = [t for t in tx if t["action"] == "DIVIDEND"][0]
        self.assertAlmostEqual(div["net_amount"], 30.0)


class TestFiatNonEvents(unittest.TestCase):
    """A2-0237 / A2-0564 / A2-0567 / A2-1024, A2-0566."""

    def test_fiat_withdrawal_is_a_nonevent(self):
        tx, err, parser = _parse(
            _BUY + "w1,2025-06-04 12:00:00 UTC,Withdrawal,CAD,-500,CAD,"
                   "$1.00,-$500.00,-$500.00,$0.00,\n")
        self.assertEqual(len(tx), 1)
        self.assertNotIn("UNBOOKED", err)
        self.assertEqual(parser.lint_findings, [])

    def test_coin_deposit_is_unbooked(self):
        _, err, parser = _parse(
            _BUY + "d1,2025-06-04 12:00:00 UTC,Deposit,BTC,0.5,CAD,"
                   "$80000.00,$40000.00,$40000.00,$0.00,\n")
        self.assertIn("UNBOOKED", err)
        self.assertTrue(parser.lint_findings)

    def test_subscription_paid_in_coin_is_unbooked(self):
        _, err, _ = _parse(
            _BUY + "d1,2025-06-04 12:00:00 UTC,Subscription,ETH,-0.004,"
                   "CAD,$3000.00,-$12.00,-$12.00,$0.00,\n")
        self.assertIn("UNBOOKED", err)

    def test_fiat_subscription_and_coin_staking_transfer_stay_nonevents(self):
        _, err, parser = _parse(
            _BUY + "d1,2025-06-04 12:00:00 UTC,Subscription,CAD,-12.99,"
                   "CAD,$1.00,-$12.99,-$12.99,$0.00,\n"
                   "d2,2025-06-05 12:00:00 UTC,Retail Staking Transfer,SOL,"
                   "-1,CAD,$200.00,-$200.00,-$200.00,$0.00,\n")
        self.assertNotIn("UNBOOKED", err)
        self.assertEqual(parser.lint_findings, [])


class TestTruncation(unittest.TestCase):
    """A2-0249."""

    def test_cut_inside_total_with_fees_and_notes_missing_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_BUY + "a2,2025-06-03 12:00:00 UTC,Sell,SOL,-0.25,CAD,"
                          "$3900.00,$975.00,$967")
        self.assertIn("truncated", str(cm.exception))


class TestSwallowedRowLine(unittest.TestCase):
    """A2-0584."""

    def test_error_names_the_line_the_quote_opened_on(self):
        body = ('a1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,$200.00,$400.00,'
                '$402.00,$2.00,"Bought 2 SOL ""\n' + _BUY + _BUY)
        with self.assertRaises(ValueError) as cm:
            _parse(body)
        # "Transactions" is line 1, the header line 2, the bad row line 3.
        self.assertIn("line 3", str(cm.exception))


class TestConvertNoIdStem(unittest.TestCase):
    """A2-0998."""

    def test_legs_share_an_id_stem_without_an_id_column(self):
        row = ("2025-06-02 12:00:00 UTC,Convert,BTC,-1,USD,$60000.00,,,"
               "$0.00,Converted 1 BTC to 20 ETH\n")
        tx, _, _ = _parse(row, header=_HDR_NOID, stablecoins_as_cash=False)
        ids = sorted(t.get("id", "") for t in tx)
        self.assertEqual(len(ids), 2)
        self.assertTrue(ids[0].endswith("-buy") and ids[1].endswith("-sell"),
                        ids)
        self.assertEqual(ids[0][:-4], ids[1][:-5])

    def test_two_identical_converts_get_distinct_stems(self):
        row = ("2025-06-02 12:00:00 UTC,Convert,BTC,-1,USD,$60000.00,,,"
               "$0.00,Converted 1 BTC to 20 ETH\n")
        tx, _, _ = _parse(row + row, header=_HDR_NOID,
                          stablecoins_as_cash=False)
        self.assertEqual(len({t["id"] for t in tx}), 4)

    def test_fill_crypto_pairs_the_no_id_legs(self):
        import json
        from taxjson.bin.fill_crypto_prices import _swap_pairs
        from taxjson.lib.core import load_transactions
        row = ("2025-06-02 12:00:00 UTC,Convert,BTC,-1,USD,$60000.00,,,"
               "$0.00,Converted 1 BTC to 20 ETH\n")
        tx, _, _ = _parse(row, header=_HDR_NOID, stablecoins_as_cash=False)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "cb.json"
            f.write_text(json.dumps(tx))
            loaded = load_transactions(f)
        self.assertEqual(len(_swap_pairs(loaded)), 1)


class TestDepegConvert(unittest.TestCase):
    """A2-1003 (Coinbase half)."""

    def test_usd_convert_spending_usdc_off_peg_warns(self):
        _, err, _ = _parse("c1,2025-06-02 12:00:00 UTC,Convert,USDC,-1000,"
                           "USD,$0.88,$880.00,$880.00,$0.00,"
                           "Converted 1000 USDC to 0.4 ETH\n")
        self.assertIn("USDC traded at 0.8800", err)

    def test_usd_stable_to_stable_convert_off_peg_warns(self):
        _, err, _ = _parse("c1,2025-06-02 12:00:00 UTC,Convert,USDT,-1000,"
                           "USD,$1.00,$1000.00,$1000.00,$0.00,"
                           "Converted 1000 USDT to 880 USDC\n")
        self.assertIn("traded at", err)

    def test_usd_advanced_trade_on_usdc_off_peg_warns(self):
        _, err, _ = _parse("a2,2025-06-03 12:00:00 UTC,Advanced Trade Buy,"
                           "ETH,0.5,USD,$1760.00,$880.00,$880.00,$0.00,"
                           "Bought 0.5 ETH for 1000 USDC on ETH-USDC at "
                           "2000 USDC/ETH\n")
        self.assertIn("USDC traded at 0.8800", err)

    def test_on_peg_convert_is_quiet(self):
        _, err, _ = _parse("c1,2025-06-02 12:00:00 UTC,Convert,USDC,-1000,"
                           "USD,$1.00,$1000.00,$1000.00,$0.00,"
                           "Converted 1000 USDC to 0.4 ETH\n")
        self.assertNotIn("traded at", err)


if __name__ == "__main__":
    unittest.main()
