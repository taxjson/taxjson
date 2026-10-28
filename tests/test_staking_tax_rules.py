"""Pin custodial import assumptions separately from country tax authority.

Synthetic rewards exercise received quantity, income, acquisition value and
fee disposal together; text checks keep the legal limitation visible.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tax_rules import rule

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.tax_logic import catalog

LEDGER_HEADER = (
    "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
    "balance,amountusd,feeusd,feecurrency\n"
)


def kraken_reward(*, fee, fee_usd, fee_asset, balance, cash=True):
    ledger = LEDGER_HEADER + (
        "LDEMO,RDEMO,2026-01-01 03:00:00,earn,reward,currency,ZZC,"
        f"earn,0.2,{fee},{balance},40,{fee_usd},{fee_asset}\n"
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td, "kraken_ledgers.csv")
        path.write_text(ledger, encoding="utf-8")
        parser = KrakenBrokerage()
        parser.stablecoins_as_cash = cash
        with patch.dict(os.environ, TAXJSON_LOCAL_TZ="America/Toronto"):
            return parser.parse_file(path)


def reward_legs(rows):
    income = [r for r in rows if r["action"] == "DIVIDEND"]
    buys = [r for r in rows if r["action"] == "BUYSELL"
            and r["quantity"] > 0]
    sales = [r for r in rows if r["action"] == "BUYSELL"
             and r["quantity"] < 0]
    return income, buys, sales


class TestCanadianCustodialStaking(unittest.TestCase):
    @rule("CA-INC-04", "CA-INC-STAKING", "CA-CRYPTO-03")
    def test_retained_commission_is_net_acquisition_without_second_sale(self):
        rows = kraken_reward(fee="0.05", fee_usd="10", fee_asset="ZZC",
                             balance="0.15")
        income, buys, sales = reward_legs(rows)
        self.assertEqual(len(income), 1)
        self.assertEqual(len(buys), 1)
        self.assertEqual(sales, [])
        for row in income + buys:
            self.assertAlmostEqual(row["quantity"], 0.15)
            self.assertAlmostEqual(row["net_amount"], 30)
            self.assertEqual(row["date"], "2025-12-31")
        self.assertEqual(buys[0]["fee"], 0)

    @rule("CA-INC-STAKING", "CA-CRYPTO-03")
    def test_different_coin_fee_keeps_full_reward_cost_and_disposes_fee(self):
        rows = kraken_reward(fee="0.02", fee_usd="4", fee_asset="ZZF",
                             balance="0.2")
        income, buys, sales = reward_legs(rows)
        self.assertAlmostEqual(income[0]["net_amount"], 36)
        self.assertAlmostEqual(buys[0]["quantity"], 0.2)
        self.assertAlmostEqual(buys[0]["net_amount"], 40)
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["symbol"], "ZZF")
        self.assertAlmostEqual(sales[0]["quantity"], -0.02)
        self.assertAlmostEqual(sales[0]["net_amount"], 4)

    @rule("CA-INC-STAKING")
    def test_fiat_fee_changes_income_without_disposing_reward_units(self):
        rows = kraken_reward(fee="2", fee_usd="2", fee_asset="ZUSD",
                             balance="0.2")
        income, buys, sales = reward_legs(rows)
        self.assertAlmostEqual(income[0]["net_amount"], 38)
        self.assertAlmostEqual(buys[0]["quantity"], 0.2)
        self.assertAlmostEqual(buys[0]["net_amount"], 40)
        self.assertEqual(sales, [])

    @rule("CA-INC-04", "CA-INC-STAKING")
    def test_coinbase_cad_income_and_cost_use_received_subtotal(self):
        csv = (
            "Transactions\n"
            "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
            "Price Currency,Price at Transaction,Subtotal,"
            "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
            "aaaaaaaaaaaaaaaaaaaaaaaa,2026-01-01 03:00:00 UTC,"
            "Staking Income,ZZC,0.2,CAD,200,40,60,20,\n"
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "coinbase.csv")
            path.write_text(csv, encoding="utf-8")
            with patch.dict(os.environ, TAXJSON_LOCAL_TZ="America/Toronto"):
                rows = CoinbaseBrokerage().parse_file(path)
        income, buys, sales = reward_legs(rows)
        self.assertEqual(sales, [])
        for row in income + buys:
            self.assertEqual(row["currency"], "CAD")
            self.assertEqual(row["date"], "2025-12-31")
            self.assertAlmostEqual(row["quantity"], 0.2)
            self.assertAlmostEqual(row["net_amount"], 40)

    @rule("CA-INC-STAKING")
    def test_rule_identifies_assumption_and_unmodeled_gross_alternative(self):
        text = catalog("canada")["CA-INC-STAKING"].text
        self.assertIn("Custodial staking assumption", text)
        self.assertIn("not a CRA ruling", text)
        self.assertIn("Gross income plus a separate expense", text)
        self.assertIn("different AMT effects", text)
        self.assertIn("Do not deduct a netted commission again", text)


class TestUSCustodialStaking(unittest.TestCase):
    @rule("US-INC-STAKING", "US-CRYPTO-03")
    def test_retained_commission_matches_shared_import_convention(self):
        income, buys, sales = reward_legs(kraken_reward(
            fee="0.05", fee_usd="10", fee_asset="ZZC", balance="0.15",
            cash=False))
        self.assertEqual(sales, [])
        self.assertAlmostEqual(income[0]["net_amount"], 30)
        self.assertAlmostEqual(buys[0]["quantity"], 0.15)
        self.assertAlmostEqual(buys[0]["net_amount"], 30)

    @rule("US-INC-STAKING", "US-CRYPTO-03")
    def test_separate_coin_fee_does_not_reduce_nonstablecoin_basis(self):
        income, buys, sales = reward_legs(kraken_reward(
            fee="0.02", fee_usd="4", fee_asset="ZZF", balance="0.2",
            cash=False))
        self.assertAlmostEqual(income[0]["net_amount"], 36)
        self.assertAlmostEqual(buys[0]["quantity"], 0.2)
        self.assertAlmostEqual(buys[0]["net_amount"], 40)
        self.assertEqual(len(sales), 1)
        self.assertAlmostEqual(sales[0]["quantity"], -0.02)

    @rule("US-INC-02", "US-INC-STAKING")
    def test_gross_income_dominion_and_fee_limitation_stay_us_specific(self):
        rules = catalog("usa")
        income = rules["US-INC-02"].text
        assumption = rules["US-INC-STAKING"].text
        self.assertIn("gross ordinary income", income)
        self.assertIn("dominion and control", income)
        self.assertIn("IRS Rev. Rul. 2023-14", income)
        self.assertIn("does not determine US gross-income entitlement",
                      assumption)
        self.assertIn("does not decide transaction fees", assumption)
        self.assertNotIn("CRA", income + assumption)
        self.assertNotIn("AMT", income + assumption)


if __name__ == "__main__":
    unittest.main()
