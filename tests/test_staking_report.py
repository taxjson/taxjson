"""Synthetic reward disclosures: provenance, netting, unknowns and totals."""
import copy
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from taxjson.lib import out
from taxjson.lib.staking_report import _display_column, build_report, render


def reward(event="sample", *, amount=80, quantity=4, price=20,
           asset="DEMO", currency="CAD", metadata=None, acquisition=80):
    div = {"action": "DIVIDEND", "date": "2025-03-04", "time": "10:15:00",
           "symbol": asset, "quantity": quantity, "price": price,
           "gross_amount": amount, "net_amount": amount, "currency": currency,
           "id": event + "-div", "description": "Staking Reward"}
    if metadata is not None:
        div["staking_reward"] = copy.deepcopy(metadata)
    rows = [("crypto_demo", div)]
    if acquisition is not None:
        buy = {**div, "action": "BUYSELL", "id": event + "-buy",
               "net_amount": acquisition, "gross_amount": acquisition, "fee": 0}
        buy.pop("staking_reward", None)
        rows.append(("crypto_demo", buy))
    return rows


def metadata(**overrides):
    result = {"schema_version": 1, "broker": "kraken", "kind": "staking",
              "source_type": "earn/reward", "source_file": "sample.csv",
              "source_line": 7, "source_timestamp": "2025-03-04T15:15:00Z",
              "source_id": "sample", "asset": "DEMO", "gross_quantity": 5,
              "fee_quantity": 1, "fee_currency": "DEMO", "net_quantity": 4,
              "valuation_currency": "USD", "gross_value": None,
              "fee_value": None, "net_value": None, "price": None,
              "commission_known": True, "commission_method": "withheld_from_reward"}
    result.update(overrides)
    return result


def report(rows, **kwargs):
    return build_report(rows, currency="CAD", scope="tax year 2025",
                        account_groups={"crypto_demo": "taxable", "crypto_plan": "sheltered"}, **kwargs)


class RewardModelTests(unittest.TestCase):
    def test_same_asset_commission_values_gross_fee_and_net_once(self):
        rows = reward(metadata=metadata())
        original = copy.deepcopy(rows)
        result = report(rows)
        self.assertEqual(rows, original)
        self.assertEqual(len(result["rewards"]), 1)
        item = result["rewards"][0]
        self.assertEqual((item["gross_amount"], item["commission_amount"], item["income_amount"], item["acquisition_cost"]),
                         (100, 20, 80, 80))
        self.assertEqual((item["gross_quantity"], item["fee_quantity"], item["net_quantity"]), (5, 1, 4))
        self.assertEqual(item["acquisition_pairing"], "paired transaction id")
        self.assertEqual(result["totals"]["income_amount"]["total"], 80)
        json.dumps(result, allow_nan=False)

    def test_coinbase_uses_recorded_fx_and_preserves_source_fact_currency(self):
        meta = metadata(broker="coinbase", source_type="Staking Income", gross_quantity=None,
                        fee_quantity=None, fee_currency="USD", price=10,
                        gross_value=50, fee_value=10, net_value=40,
                        valuation={"price": 10, "currency": "USD", "date": "2025-03-04",
                                   "source": "broker", "fx_rate": 2, "target_currency": "CAD"})
        item = report(reward(metadata=meta))["rewards"][0]
        self.assertEqual(item["fx_rate"], 2)
        self.assertEqual(item["fx_provenance"], "recorded book conversion rate")
        self.assertEqual(item["commission_amount"], 20)
        self.assertEqual(item["source_evidence"]["fee_value"], 10)
        self.assertIsNone(item["gross_quantity"])
        self.assertIsNone(item["fee_quantity"])
        self.assertEqual(item["fee_currency"], "USD")

    def test_inferred_fx_is_labelled_and_zero_is_not_unknown(self):
        meta = metadata(gross_value=40, fee_value=0, net_value=40, price=10,
                        gross_quantity=4, fee_quantity=0, fee_currency="USD")
        item = report(reward(metadata=meta))["rewards"][0]
        self.assertEqual(item["fx_rate"], 2)
        self.assertIn("inferred", item["fx_provenance"])
        self.assertEqual(item["commission_amount"], 0)
        self.assertEqual(item["gross_amount"], 80)

    def test_income_ratio_fallback_requires_known_nonzero_source_net(self):
        meta = metadata(gross_quantity=None, fee_quantity=None, price=None,
                        gross_value=50, fee_value=10, net_value=40)
        item = report(reward(metadata=meta, price=0))["rewards"][0]
        self.assertEqual(item["fx_rate"], 2)
        self.assertIn("source net value", item["fx_provenance"])
        meta["net_value"] = None
        item = report(reward(metadata=meta, price=0, acquisition=None))["rewards"][0]
        self.assertIsNone(item["fx_rate"])
        self.assertIsNone(item["commission_amount"])

    def test_cross_asset_fee_keeps_acquisition_cost_separate(self):
        meta = metadata(gross_quantity=4, net_quantity=4, fee_quantity=2,
                        fee_currency="FEECOIN", commission_method="paid_in_other_asset",
                        valuation_currency="CAD", gross_value=80, fee_value=12, net_value=68,
                        price=20)
        result = report(reward(metadata=meta, amount=68, acquisition=80))
        item = result["rewards"][0]
        self.assertEqual((item["gross_amount"], item["commission_amount"], item["income_amount"], item["acquisition_cost"]),
                         (80, 12, 68, 80))
        self.assertEqual(item["fee_currency"], "FEECOIN")
        self.assertEqual(result["groups"][0]["fee_quantities"]["FEECOIN"]["total"], 2)
        self.assertNotIn("gross_quantity", result["totals"])

    def test_book_bridge_does_not_call_source_rounding_a_commission(self):
        meta = metadata(broker="coinbase", gross_quantity=None, fee_quantity=None,
                        fee_currency="CAD", valuation_currency="CAD", price=20,
                        gross_value=100.01, fee_value=20, net_value=80)
        item = report(reward(metadata=meta))["rewards"][0]
        self.assertEqual(item["gross_amount"], 100)
        self.assertEqual(item["source_gross_base"], 100.01)
        self.assertAlmostEqual(item["income_reconciliation_difference"], .01)
        self.assertEqual(item["commission_amount"], 20)

    def test_quote_currency_does_not_convert_source_money_in_another_currency(self):
        meta = metadata(broker="coinbase", gross_quantity=None, fee_quantity=None,
                        fee_currency="CAD", valuation_currency="CAD", price=None,
                        gross_value=100, fee_value=20, net_value=80,
                        valuation={"price": 10, "currency": "USD", "date": "2025-03-04",
                                   "source": "Yahoo daily close", "fx_rate": 2, "target_currency": "CAD"})
        item = report(reward(metadata=meta))["rewards"][0]
        self.assertEqual(item["fx_rate"], 2)
        self.assertEqual(item["source_value_fx_rate"], 1)
        self.assertEqual(item["commission_amount"], 20)
        self.assertEqual(item["gross_amount"], 100)
        meta.update(valuation_currency="EUR", fee_currency="EUR")
        item = report(reward(metadata=meta))["rewards"][0]
        self.assertIsNone(item["source_value_fx_rate"])
        self.assertIsNone(item["commission_amount"])

    def test_quote_currency_does_not_relabel_source_income_ratio(self):
        meta = metadata(gross_quantity=None, fee_quantity=None, valuation_currency="EUR",
                        price=5, gross_value=50, fee_value=10, net_value=40,
                        valuation={"currency": "USD", "source": "unknown"})
        item = report(reward(metadata=meta, price=0, acquisition=None))["rewards"][0]
        self.assertIsNone(item["native_price"])
        self.assertIsNone(item["fx_rate"])
        self.assertIsNone(item["commission_amount"])

    def test_unconvertible_nonzero_fee_not_fabricated_from_net_book_price(self):
        meta = metadata(valuation_currency="EUR", gross_value=10, fee_value=2,
                        fee_currency="EUR", gross_quantity=4, net_quantity=4,
                        commission_method="paid_in_other_asset",
                        valuation={"price": 10, "currency": "USD", "fx_rate": 2, "target_currency": "CAD"})
        item = report(reward(metadata=meta))["rewards"][0]
        self.assertIsNone(item["commission_amount"])

    def test_legacy_is_unclassified_with_unknown_commission(self):
        result = report(reward())
        item = result["rewards"][0]
        self.assertEqual(item["kind"], "unclassified")
        self.assertEqual(item["broker"], "unknown")
        self.assertIsNone(item["commission_amount"])
        self.assertIsNone(item["gross_amount"])
        self.assertEqual(item["income_amount"], 80)
        self.assertEqual(result["coverage"]["unclassified"], 1)
        self.assertEqual(result["totals"]["commission_amount"],
                         {"known_total": 0, "known_count": 0, "unknown_count": 1, "total": None})

    def test_promotional_income_stays_other_reward(self):
        result = report(reward(metadata=metadata(kind="other_reward", source_type="Learning Reward")))
        self.assertEqual(result["rewards"][0]["kind"], "other_reward")
        self.assertEqual(result["groups"][0]["kind_counts"], {"other_reward": 1})

    def test_explicit_unknown_fee_does_not_become_zero(self):
        meta = metadata(commission_known=False, gross_quantity=None, fee_quantity=None,
                        commission_method="unknown")
        item = report(reward(metadata=meta))["rewards"][0]
        self.assertIsNone(item["commission_amount"])
        self.assertIsNone(item["gross_amount"])

    def test_no_acquisition_is_unknown_not_zero_cost(self):
        item = report(reward(metadata=metadata(), acquisition=None))["rewards"][0]
        self.assertIsNone(item["acquisition_cost"])
        self.assertEqual(item["acquisition_pairing"], "no paired acquisition in selected books")

    def test_ambiguous_legacy_pairing_does_not_choose_purchase(self):
        rows = reward()
        for _, tx in rows:
            tx.pop("id")
        rows.append((rows[1][0], dict(rows[1][1])))
        item = report(rows)["rewards"][0]
        self.assertIsNone(item["acquisition_cost"])
        self.assertEqual(item["acquisition_pairing"], "ambiguous reward purchases")

    def test_regular_purchase_is_not_used_as_legacy_reward_basis(self):
        rows = reward()
        rows[0][1].pop("id")
        rows[1][1].pop("id")
        rows[1][1]["description"] = "Purchase"
        self.assertIsNone(report(rows)["rewards"][0]["acquisition_cost"])

    def test_one_purchase_cannot_support_two_rewards(self):
        rows = reward()
        rows.append((rows[0][0], dict(rows[0][1])))
        result = report(rows)
        self.assertEqual(result["coverage"]["paired_acquisitions"], 1)
        self.assertEqual(result["totals"]["income_amount"]["total"], 160)

    def test_same_id_in_different_account_does_not_pair(self):
        rows = reward()
        rows[1] = ("crypto_plan", rows[1][1])
        self.assertIsNone(report(rows)["rewards"][0]["acquisition_cost"])

    def test_scope_groups_do_not_mix_taxable_and_sheltered(self):
        rows = reward(metadata=metadata())
        rows += [("crypto_plan", tx) for _, tx in reward("other", metadata=metadata())]
        result = report(rows)
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual({group["account_group"] for group in result["groups"]}, {"taxable", "sheltered"})
        self.assertEqual(result["totals"]["income_amount"]["total"], 160)
        self.assertEqual(result["totals_by_account_group"]["taxable"]["income_amount"]["total"], 80)
        self.assertEqual(result["totals_by_account_group"]["sheltered"]["income_amount"]["total"], 80)
        self.assertEqual(result["totals_by_account_group"]["unknown"]["reward_count"], 0)

    def test_source_assets_sharing_a_book_symbol_do_not_mix_quantities(self):
        rows = reward(metadata=metadata(asset="COIN_A"), asset="BOOK_CASH")
        rows += reward("second", metadata=metadata(asset="COIN_B"), asset="BOOK_CASH")
        result = report(rows)
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual({group["asset"] for group in result["groups"]}, {"COIN_A", "COIN_B"})

    def test_partial_known_totals_expose_coverage(self):
        rows = reward(metadata=metadata()) + reward("legacy")
        result = report(rows)
        stat = result["totals"]["commission_amount"]
        self.assertEqual(stat, {"known_total": 20, "known_count": 1, "unknown_count": 1, "total": None})
        self.assertEqual(result["totals"]["income_amount"]["total"], 160)

    def test_missing_price_is_not_reported_as_complete_valuation(self):
        result = report(reward(metadata=metadata(), amount=0, price=0, acquisition=0))
        self.assertEqual(result["rewards"][0]["income_amount"], 0)
        self.assertIsNone(result["rewards"][0]["commission_amount"])
        self.assertEqual(result["coverage"]["missing_valuation"], 1)

    def test_acquisition_valuation_mismatch_disclosed_without_fix(self):
        meta = metadata(gross_quantity=4, net_quantity=4, fee_quantity=1,
                        fee_currency="USD", commission_method="paid_in_other_asset",
                        gross_value=4, fee_value=1, net_value=3,
                        valuation={"price": .75, "currency": "USD", "date": "2025-03-04",
                                   "source": "stablecoin net income / reward quantity", "fx_rate": 2,
                                   "target_currency": "CAD", "fx_source": "history prior date", "fx_date": "2025-03-03"})
        result = report(reward(metadata=meta, amount=6, price=1.5, acquisition=6))
        item = result["rewards"][0]
        self.assertEqual(item["acquisition_cost"], 6)
        self.assertEqual(item["expected_acquisition_cost"], 8)
        self.assertEqual(result["coverage"]["acquisition_discrepancies"], 1)
        self.assertEqual(item["fx_date"], "2025-03-03")
        self.assertEqual(item["fx_source"], "history prior date")

    def test_wrong_currency_is_not_summed_under_base_label(self):
        result = report(reward(metadata=metadata(), currency="USD"))
        self.assertIsNone(result["rewards"][0]["income_amount"])
        self.assertIsNone(result["rewards"][0]["acquisition_cost"])

    def test_country_rule_and_source_book_dates_are_separate(self):
        with self.assertRaises(ValueError):
            report(reward(), country="not-a-country")
        meta = metadata(source_timestamp="2025-03-05T02:15:00Z")
        result = report(reward(metadata=meta), country="US", local_timezone="America/Vancouver")
        item = result["rewards"][0]
        self.assertEqual(result["rule"], "US-INC-STAKING")
        self.assertEqual(item["date"], "2025-03-04")
        self.assertEqual(item["source"]["timestamp"], "2025-03-05T02:15:00Z")
        self.assertEqual(item["local_timezone"], "America/Vancouver")


class RenderTests(unittest.TestCase):
    def test_mixed_scope_defaults_separate_income_and_label_combined_total(self):
        rows = reward(metadata=metadata())
        rows += [("crypto_plan", tx) for _, tx in reward("plan", amount=40, acquisition=40)]
        rows += [("unmapped", tx) for _, tx in reward("unknown", amount=7, acquisition=7)]
        result = report(rows)
        self.assertEqual(result["totals"]["income_amount"]["total"], 127)
        self.assertEqual(result["totals_by_account_group"]["unknown"]["income_amount"]["total"], 7)
        for summary in (False, True):
            with patch.dict(os.environ, {"TAXJSON_WIDTH": "120"}):
                text = "\n".join(render(result, summary=summary))
            self.assertIn("all scopes", text)
            self.assertIn("INCOME BY ACCOUNT SCOPE", text)
            self.assertRegex(text, r"taxable\s+1\s+80\.00\s+1 / 1")
            self.assertRegex(text, r"sheltered\s+1\s+40\.00\s+1 / 1")
            self.assertRegex(text, r"unknown\s+1\s+7\.00\s+1 / 1")
            self.assertIn("before rounding", text)
            self.assertEqual(out.lint(text), [])
            self.assertLessEqual(len(out.prose_lines(text)), 6)

    def test_unknown_scope_income_retains_incomplete_coverage(self):
        rows = reward(metadata=metadata())
        unknown = reward("unknown", acquisition=None)
        unknown[0][1].pop("gross_amount")
        unknown[0][1].pop("net_amount")
        rows += [("unmapped", tx) for _, tx in unknown]
        result = report(rows)
        stat = result["totals_by_account_group"]["unknown"]["income_amount"]
        self.assertEqual(stat["unknown_count"], 1)
        self.assertIsNone(stat["total"])
        text = "\n".join(render(result))
        self.assertRegex(text, r"unknown\s+1\s+\?\s+0 / 1")

    def test_default_budget_and_details_evidence(self):
        result = report(reward(metadata=metadata()))
        with patch.dict(os.environ, {"TAXJSON_WIDTH": "120"}):
            for summary in (False, True):
                text = "\n".join(render(result, summary=summary))
                self.assertEqual(out.lint(text), [])
                self.assertLessEqual(len(out.prose_lines(text)), 6)
                self.assertIn("80.00", text)
                # What the figure is, not an instruction about the return
                # (backlog TJ-0288).
                self.assertIn("NET INCOME is after the COMMISSION the "
                              "exchange kept", text)
                self.assertNotIn("deducted again", text)
                detail = "\n".join(render(result, summary=summary, details=True))
                self.assertEqual(out.lint(detail), [])
                for needle in ("sample.csv", "line 7", "2025-03-04T15:15:00Z",
                               "America/Toronto", "Commission units", "FX to base", "CA-INC-STAKING"):
                    self.assertIn(needle, detail)

    def test_tj0288_no_commission_line_without_a_commission(self):
        # A view with no commission (none withheld, or no reward at all)
        # says nothing about one.
        for rows in (reward(metadata=metadata(fee_quantity=0, gross_quantity=4)),
                     []):
            result = report(rows)
            for summary in (False, True):
                with patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
                    text = "\n".join(render(result, summary=summary))
                self.assertNotIn("COMMISSION the exchange kept", text)
                self.assertNotIn("deducted again", text)
        self.assertNotIn("deduct", " ".join(report([])["notes"]))

    def test_partial_subtotals_unknown_and_kind_are_visible(self):
        result = report(reward(metadata=metadata()) + reward("legacy"))
        with patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
            text = "\n".join(render(result, summary=True))
        self.assertIn("20.00*", text)
        self.assertIn("unclassified 1 of 2", text)
        self.assertIn("S1", text)
        self.assertIn("U1", text)

    def test_money_columns_foot_after_rounding(self):
        for values in ([.004, .004, .004], [.005, .005, .005], [-.005, -.005, .004, None], [.001] * 20):
            shown = _display_column(values)
            total = sum(Decimal(value.replace(",", "")) for value in shown if value != "?")
            raw = sum(Decimal(str(value)) for value in values if value is not None)
            self.assertEqual(total, raw.quantize(Decimal(".01"), rounding="ROUND_HALF_UP"))

    def test_rendered_total_uses_same_half_up_rounding_as_row(self):
        result = report(reward(amount=.015, acquisition=.015))
        for summary in (False, True):
            with patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
                lines = render(result, summary=summary)
            total = next(line for line in lines if line.startswith("TOTAL"))
            self.assertEqual(total.split()[-2:], ["0.02", "0.02"])
            self.assertNotIn("0.01", "\n".join(lines))

    def test_details_retain_saved_source_quantity_price_and_fx_precision(self):
        meta = metadata(gross_quantity=.3468135791, fee_quantity=.1, net_quantity=.2468135791,
                        gross_value=12.3456789123, fee_value=.123456789,
                        net_value=12.2222221233,
                        valuation={"price": 30.1234567891, "currency": "USD", "fx_rate": 1.2345678912,
                                   "target_currency": "CAD", "source": "broker", "date": "2025-03-04"})
        result = report(reward(metadata=meta, quantity=.2468135791))
        with patch.dict(os.environ, {"TAXJSON_WIDTH": "120"}):
            text = "\n".join(render(result, details=True))
        for value in ("0.3468135791", "0.2468135791", "12.3456789123", "0.123456789",
                      "12.2222221233", "30.1234567891", "1.2345678912"):
            self.assertIn(value, text)

    def test_small_nonzero_quantity_does_not_show_as_zero(self):
        result = report(reward(quantity=.00000004, amount=.01, price=250000, acquisition=.01))
        with patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
            self.assertIn("4e-08", "\n".join(render(result)))

    def test_empty_report_short_and_json_safe(self):
        result = report([])
        self.assertEqual(result["totals"]["reward_count"], 0)
        self.assertEqual(result["totals"]["income_amount"]["total"], 0)
        self.assertIn("No crypto rewards", "\n".join(render(result)))
        self.assertLessEqual(len(out.prose_lines("\n".join(render(result)))), 6)
        json.dumps(result, allow_nan=False)

    def test_output_escapes_broker_source_control_characters(self):
        result = report(reward(metadata=metadata(source_file="sample\x1b[31m.csv")))
        with patch.dict(os.environ, {"TAXJSON_WIDTH": "120"}):
            text = "\n".join(render(result, details=True))
        self.assertNotIn("\x1b", text)
        self.assertIn("\\x1b", text)

    def test_coinbase_cash_value_is_not_claimed_to_be_paid_cash_quantity(self):
        meta = metadata(broker="coinbase", fee_quantity=None, fee_currency="USD", fee_value=10,
                        valuation_currency="USD", price=10, gross_value=50, net_value=40)
        result = report(reward(metadata=meta))
        with patch.dict(os.environ, {"TAXJSON_WIDTH": "120"}):
            text = "\n".join(render(result, details=True))
        self.assertIn("Coin quantity not supplied; commission valued in USD", text)
        self.assertIn("withheld from reward", text)
        self.assertNotIn("withheld_from_reward", text)
        self.assertNotIn("market symbol ?", text)


if __name__ == "__main__":
    unittest.main()
