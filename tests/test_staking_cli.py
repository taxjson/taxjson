"""Reward evidence reaches the CLI through parsing, coercion and FX.

Synthetic exports only; the view must use the final books, never sum the
broker intermediates again or reprice a reward when it is displayed.
"""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.core import coerce_transaction_row
from taxjson.lib.dispatch import python_module_argv
from taxjson.bin.taxjson_convert_currency import convert_transaction


REPO = Path(__file__).resolve().parents[1]
CONFIG = '''[settings]
country = "canada"
year = 2025
base_currency = "CAD"
local_timezone = "America/Toronto"
[accounts.crypto]
type = "taxable"
crypto = true
[accounts.vault]
type = "sheltered"
crypto = true
[accounts.margin]
type = "taxable"
'''
CSV = '''Transactions
ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes
demo-a,2025-07-16 08:21:37 UTC,Staking Income,ZZC,0.2,USD,200,40,60,20,
demo-b,2026-01-01 01:00:00 UTC,Staking Income,ZZC,0.1,USD,100,10,10,0,
demo-c,2026-01-02 01:00:00 UTC,Staking Income,ZZC,0.1,USD,100,10,10,0,
'''


def cli(root, *args):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]), cwd=REPO, text=True,
        capture_output=True, stdin=subprocess.DEVNULL,
        env={**os.environ, "TAXJSON_OFFLINE": "1", "TAXJSON_WIDTH": "120"})


def project(root):
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(CONFIG, encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td, "coinbase_demo.csv")
        src.write_text(CSV, encoding="utf-8")
        with patch.dict(os.environ, TAXJSON_LOCAL_TZ="America/Toronto"):
            parsed = CoinbaseBrokerage().parse_file(src)
    history = {"USD": {"2025-07-16": Decimal("1.25"),
                       "2025-12-31": Decimal("1.40"),
                       "2026-01-01": Decimal("1.40")}}
    converted = [convert_transaction(
        coerce_transaction_row(t, i, "synthetic reward"), "CAD", history, None
    ).to_dict() for i, t in enumerate(parsed)]
    for name, rows in (("crypto_base.json", converted),
                       ("crypto_coinbase.json", parsed),
                       ("crypto_sorted.json", parsed),
                       ("vault_base.json", copy.deepcopy(converted[:2])),
                       ("margin_base.json", [{"action": "DIVIDEND",
                         "date": "2025-06-01", "symbol": "ZZZ.TO",
                         "currency": "CAD", "gross_amount": 100}])):
        (root / "work" / name).write_text(
            json.dumps({"transactions": rows}), encoding="utf-8")
    return converted


class TestStakingCommands(unittest.TestCase):
    def test_parser_fx_evidence_and_local_year_reach_both_views(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project(root)
            results = [cli(root, command, "2025", "crypto", "--json")
                       for command in ("staking", "staking-sum")]
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
            detail, summary = (json.loads(r.stdout) for r in results)
        self.assertEqual(detail, summary)
        self.assertEqual(len(detail["rewards"]), 2)
        self.assertAlmostEqual(detail["totals"]["income_amount"]["total"], 64)
        self.assertAlmostEqual(detail["totals"]["commission_amount"]["total"], 25)
        self.assertAlmostEqual(detail["totals"]["gross_amount"]["total"], 89)
        self.assertAlmostEqual(detail["totals"]["acquisition_cost"]["total"], 64)
        self.assertEqual(detail["coverage"]["unknown_commission"], 0)

    def test_filters_keep_matching_acquisition_and_do_not_sum_intermediates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project(root)
            result = cli(root, "staking", "crypto", "--broker", "COINBASE",
                         "--symbol", "zzc", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            empty = cli(root, "staking", "crypto", "--symbol", "OTHER", "--json")
            self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertEqual(len(report["rewards"]), 2)
        self.assertEqual(report["totals"]["acquisition_cost"]["total"], 64)
        self.assertEqual(json.loads(empty.stdout)["rewards"], [])

    def test_sheltered_and_taxable_rewards_are_separate_groups(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project(root)
            result = cli(root, "staking-sum", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
        self.assertEqual(len(report["rewards"]), 3)
        self.assertEqual(len(report["groups"]), 2)
        self.assertEqual({r["account"] for r in report["rewards"]}, {"crypto", "vault"})

    def test_older_book_keeps_income_but_not_invented_zero_commission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = project(root)
            for row in rows:
                row.pop("staking_reward", None)
            (root / "work" / "crypto_base.json").write_text(
                json.dumps({"transactions": rows}), encoding="utf-8")
            result = cli(root, "staking-sum", "crypto", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            text = cli(root, "staking", "crypto", "--details")
            self.assertEqual(text.returncode, 0, text.stderr)
            filtered = cli(root, "staking", "crypto", "--broker", "coinbase", "--json")
            self.assertNotEqual(filtered.returncode, 0)
            self.assertIn("exchange is unknown", filtered.stderr)
        self.assertEqual(report["totals"]["income_amount"]["total"], 64)
        self.assertIsNone(report["totals"]["commission_amount"]["total"])
        self.assertEqual(report["coverage"]["unknown_commission"], 2)
        self.assertIn("unknown", text.stdout.lower())

    def test_tt_reward_is_left_out_of_an_exchange_view_not_refused(self):
        # A .tt reward line names no exchange and no rerun gives it one:
        # `--broker coinbase` lists Coinbase's rewards and counts it.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = project(root)
            rows.append({"action": "DIVIDEND", "date": "2025-08-01",
                         "time": "12:00:00", "symbol": "ZZC",
                         "quantity": 0.01, "currency": "CAD", "price": 200.0,
                         "net_amount": 2.0, "gross_amount": 2.0,
                         "source": "hand_reward.tt", "id": "tt-reward-1"})
            (root / "work" / "crypto_base.json").write_text(
                json.dumps({"transactions": rows}), encoding="utf-8")
            result = cli(root, "staking", "2025", "crypto", "--broker",
                         "coinbase", "--json")
            unknown = cli(root, "staking", "2025", "crypto", "--broker",
                          "unknown", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["rewards"]), 2)
        self.assertIn("1 reward(s) in this window come from .tt lines",
                      result.stderr)
        self.assertEqual(unknown.returncode, 0, unknown.stderr)
        self.assertEqual([r["transaction_id"] for r in
                          json.loads(unknown.stdout)["rewards"]],
                         ["tt-reward-1"])

    def test_unconverted_final_book_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = project(root)
            rows[0]["currency"] = "USD"
            (root / "work" / "crypto_base.json").write_text(
                json.dumps({"transactions": rows}), encoding="utf-8")
            result = cli(root, "staking-sum", "crypto")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not in CAD", result.stderr)


if __name__ == "__main__":
    unittest.main()
