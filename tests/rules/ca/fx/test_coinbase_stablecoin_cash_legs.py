"""Fix round 7 (wave B), group "coinbase": the Coinbase readers and the
crypto send / price helpers (backlog TJ-0865, TJ-0870, TJ-0916,
TJ-0747, TJ-0652, TJ-0663, TJ-0667, TJ-0671, TJ-0744).

All rows, ids and amounts are synthetic.

Formerly: test_fix_1029_r7b_coinbase
"""
import contextlib
import copy
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
# The Coinbase parse builds its cash events (PLAN step 16 commit 8).
from _support.parser_cash import parsed_cash_events
from taxjson.bin.taxjson_fx_cash import apply_jurisdiction
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")
_BOOK = "coins/coinbase"


def _events(td, name, body):
    p = Path(td) / name
    p.write_text(_HDR + body)
    evs = parsed_cash_events("coinbase", p)
    for e in evs:
        e.update(label="coins", book=_BOOK, broker="coinbase", file=name)
    return p, evs


def _lines(*texts):
    out = []
    for i, t in enumerate(texts, 1):
        e = CL.parse_line(t, f"cash.tt:{i}")
        e.update(label="coins", book=_BOOK, file="cash.tt", book_problem="")
        out.append(e)
    return out


def _ledger(events, lines, rate, native=()):
    return V2.build(copy.deepcopy(list(native)),
                    {"events": copy.deepcopy(events),
                     "lines": copy.deepcopy(lines), "problems": [],
                     "stablecoins_as_cash": True},
                    "CAD", 2025, rate, country="canada")


class TestConvertStablecoinLegs(unittest.TestCase):
    """TJ-0916: a Convert spending or receiving a stablecoin moves US-
    dollar cash (CA-CRYPTO-02); the v2 cash reader restores that leg
    from the row's Notes (the property parser books only the coin).

    Formerly: test_fix_1029_r7b_coinbase.TestConvertStablecoinLegs"""

    _BODY = ("CB-1,2025-06-02 16:00:00 UTC,Convert,USDC,100,USD,1,100,100,"
             "0,Converted 100 USDC to 1 ZZE\n"
             "CB-2,2025-06-04 16:00:00 UTC,Convert,ZZE,1,USD,100,100,100,"
             "0,Converted 1 ZZE to 100 USDC\n"
             "CB-3,2025-06-06 16:00:00 UTC,Sell,USDC,100,CAD,6,600,600,0,"
             "Sold 100 USDC for 600 CAD\n")

    @rule(R.CA_CRYPTO_02)
    def test_convert_cash_legs_reach_the_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            p, evs = _events(td, "coinbase.csv", self._BODY)
            with contextlib.redirect_stderr(io.StringIO()):
                native = CoinbaseBrokerage().parse_file(p)
        for r in native:
            r.update(account="coins", source="coinbase.csv", _book=_BOOK)
        legs = sorted((e["date"], e["kind"], e["currency"], e["amount"])
                      for e in evs if e["kind"] == "FLOW")
        self.assertEqual(legs, [("2025-06-02", "FLOW", "USD", -100.0),
                                ("2025-06-04", "FLOW", "USD", 100.0)])
        v = _ledger(evs, _lines("CASHOPEN 2025-01-01 USD 100 100",
                                "CASHBAL 2025-12-31 USD 0"),
                    lambda c, d: 1.25 if d < "2025-06-04" else 1.5,
                    native)
        self.assertEqual(v["status"], "computed", v.get("problems"))
        self.assertAlmostEqual(v["net_gain"], 475.0, places=2)
        self.assertAlmostEqual(
            apply_jurisdiction(v["net_gain"], "canada")["reportable"],
            275.0, places=2)

    def test_asset_column_naming_either_leg(self):
        # The Asset column may name the coin or the stablecoin: the cash
        # leg is read from Notes either way.
        body = ("CB-1,2025-06-02 16:00:00 UTC,Convert,ZZE,1,USD,100,100,"
                "100,0,Converted 100 USDC to 1 ZZE\n")
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "a.csv", body)
        self.assertEqual([(e["kind"], e["amount"]) for e in evs],
                         [("FLOW", -100.0)])

    def test_tjs_cash_lists_the_leg_as_a_trade_not_a_fee(self):
        body = self._BODY.splitlines(True)[0]
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "a.csv", body)
        self.assertEqual(CE.tt_line(evs[0], "CAD"),
                         "#TRADE 2025-06-02 USD -100.00")

    def test_stablecoin_to_stablecoin_and_coin_to_coin_move_no_cash(self):
        body = ("CB-1,2025-06-02 16:00:00 UTC,Convert,USDC,100,USD,1,100,"
                "100,0,Converted 100 USDC to 100 USDT\n"
                "CB-2,2025-06-03 16:00:00 UTC,Convert,ZZE,1,USD,100,100,"
                "100,0,Converted 1 ZZE to 2 ZZF\n")
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "a.csv", body)
        self.assertEqual(evs, [])

    def test_a_stablecoin_converted_to_or_from_a_currency_has_no_trade_leg(self):
        # Review of PLAN step 16 commit 8 (TJ-4384, c25 and c29): the trade
        # leg is a stablecoin's against a coin; a Convert between a
        # stablecoin and a currency (either way) gave a FLOW under each.
        body = ("CB-1,2025-06-02 16:00:00 UTC,Convert,USDC,100,CAD,1.36,136,136,0,"
                "Converted 100 USDC to 136 CAD\n"
                "CB-2,2025-06-03 16:00:00 UTC,Convert,CAD,136,CAD,1,136,136,0,"
                "Converted 136 CAD to 100 USDC\n")
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "a.csv", body)
        self.assertEqual(evs, [])

    def test_property_mode_moves_no_cash(self):
        # A US project's stablecoin is property (US-CRYPTO-02).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.csv"
            p.write_text(_HDR + self._BODY.splitlines(True)[0])
            self.assertEqual(parsed_cash_events("coinbase", p, "usa"), [])


class TestSubscriptionFromCash(unittest.TestCase):
    """TJ-0870: a Coinbase One subscription paid from US-dollar cash
    spends that cash in the ledger.

    Formerly: test_fix_1029_r7b_coinbase.TestSubscriptionFromCash"""

    @rule(R.CA_FX_07)
    def test_usd_subscription_spends_usd(self):
        body = ("CB-SUB,2025-06-02 16:00:00 UTC,Subscription,USD,-30,USD,1,"
                "-30,-30,0,Coinbase One subscription\n")
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "coinbase.csv", body)
        self.assertEqual([(e["kind"], e["currency"], e["amount"])
                          for e in evs], [("FLOW", "USD", -30.0)])
        v = _ledger(evs, _lines("CASHOPEN 2025-01-01 USD 30 30",
                                "CASHBAL 2025-12-31 USD 0"),
                    lambda c, d: 1.5)
        self.assertEqual(v["status"], "computed", v.get("problems"))

    def test_stablecoin_subscription_in_cash_mode(self):
        body = ("CB-SUB,2025-06-02 16:00:00 UTC,Subscription,USDC,30,USD,1,"
                "30,30,0,Coinbase One subscription\n")
        with tempfile.TemporaryDirectory() as td:
            p, evs = _events(td, "coinbase.csv", body)
            self.assertEqual([(e["kind"], e["currency"], e["amount"])
                              for e in evs], [("FLOW", "USD", -30.0)])
            self.assertEqual(parsed_cash_events("coinbase", p, "usa"), [])


if __name__ == "__main__":
    unittest.main()
