"""Non-cash distribution ingestion (`distributions.map`).

Reinvested (phantom) capital-gains distributions never appear in broker
CSVs but raise ACB; late-published ROC factors lower it. The map file →
ADJUST-row bridge is covered here: the balance walk (splits, renames),
idempotent regeneration, pipeline wiring, and the end effect on a
computed gain.
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestBalanceWalk(unittest.TestCase):
    def test_split_and_rename_aware(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [
            {"action": "BUYSELL", "date": "2025-01-05", "time": "09:30:00",
             "symbol": "OLD.TO", "quantity": 100.0},
            {"action": "SPLIT", "date": "2025-02-01", "time": "00:00:00",
             "symbol": "OLD.TO", "symbol_new": "NEW.TO", "quantity": 2.0},
            {"action": "BUYSELL", "date": "2025-03-01", "time": "09:30:00",
             "symbol": "NEW.TO", "quantity": -50.0},
        ]
        # Query by the CURRENT ticker: pre-rename buys must count.
        self.assertAlmostEqual(balance_on(txs, "NEW.TO", "2025-03-31"),
                               150.0)
        self.assertAlmostEqual(balance_on(txs, "NEW.TO", "2025-01-31"),
                               100.0)   # before the split/rename

    def test_regeneration_never_accumulates(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        doc = {"transactions": [
            {"action": "BUYSELL", "date": "2025-01-05", "time": "09:30:00",
             "symbol": "XAW.TO", "quantity": 100.0, "account": "m"},
        ]}
        rows = [("XAW.TO", "2025-12-29", 0.43)]
        doc, n1 = apply_distributions(doc, rows, "m")
        doc, n2 = apply_distributions(doc, rows, "m")
        adjusts = [t for t in doc["transactions"]
                   if t["action"] == "ADJUST"]
        self.assertEqual((n1, n2, len(adjusts)), (1, 1, 1))
        self.assertAlmostEqual(adjusts[0]["net_amount"], 43.0, places=4)


class TestEndToEnd(unittest.TestCase):
    def test_map_reduces_gain_at_sale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            # Buy 100 @ 10 in 2025; reinvested dist Dec 2025 (+0.50/sh);
            # sell all in 2026 @ 12.
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XAW.TO,"
                "D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n"
                "2026-03-20 10:15:00 AM,2026-03-23 12:00:00 AM,Sell,XAW.TO,"
                "D,-100,12.00,1200.00,0.00,1200.00,CAD,1,Trades,Individual\n")
            (root / "distributions.map").write_text(
                "# reinvested capital-gains distribution\n"
                "XAW.TO 2025-12-29 0.50\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            gains = json.loads(
                (root / "work" / "margin_gains.json").read_text())
        sale = next(t for t in gains["transactions"]
                    if t.get("qty") == 100.0 and "gain" in t)
        # ACB 1000 + 50 phantom dist = 1050 → gain 150, not 200.
        self.assertAlmostEqual(
            sale["gain"], 150.0, places=2,
            msg="the reinvested distribution did not raise the ACB")

    def test_unheld_symbol_row_is_noted_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
                "D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n")
            (root / "distributions.map").write_text(
                "ZZZ.TO 2025-12-29 0.50\n")
            r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no ZZZ.TO shares held", r.stderr + r.stdout)


class TestRecordDateBasis(unittest.TestCase):
    """2026-09 audit: the record-date balance walked TRADE dates, so a
    sale traded 06-19 settling 06-22 made the seller NOT a holder on a
    06-19 record date (wrong — unsettled, still holder of record), and
    a buy traded ON the record date was credited (wrong — it settles
    after). `--date-basis settle` (the CRA default; `taxjson run`
    passes the project's tax_date) keys the walk on date_settle."""

    _BOOK = [
        {"action": "BUYSELL", "date": "2026-06-01", "time": "09:30:00",
         "date_settle": "2026-06-02", "symbol": "XAW.TO",
         "quantity": 100.0, "account": "margin"},
        {"action": "BUYSELL", "date": "2026-06-19", "time": "10:15:00",
         "date_settle": "2026-06-22", "symbol": "XAW.TO",
         "quantity": -100.0, "account": "margin"},
    ]

    def test_unsettled_sale_still_holder_of_record(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions, balance_on)
        self.assertAlmostEqual(
            balance_on(self._BOOK, "XAW.TO", "2026-06-19", "settle"),
            100.0)
        self.assertAlmostEqual(
            balance_on(self._BOOK, "XAW.TO", "2026-06-19", "trade"), 0.0)
        # The ADJUST row lands under settle, is skipped under trade.
        doc, n = apply_distributions({"transactions": list(self._BOOK)},
                                     [("XAW.TO", "2026-06-19", 0.50)],
                                     "margin", "settle")
        self.assertEqual(n, 1)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"]
        self.assertAlmostEqual(adj[0]["net_amount"], 50.0, places=2)
        with redirect_stderr(io.StringIO()):
            _, n = apply_distributions({"transactions": list(self._BOOK)},
                                       [("XAW.TO", "2026-06-19", 0.50)],
                                       "margin", "trade")
        self.assertEqual(n, 0)

    def test_buy_traded_on_record_date_not_yet_credited(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        book = [{"action": "BUYSELL", "date": "2026-06-19",
                 "time": "09:30:00", "date_settle": "2026-06-22",
                 "symbol": "XAW.TO", "quantity": 100.0,
                 "account": "margin"}]
        self.assertAlmostEqual(
            balance_on(book, "XAW.TO", "2026-06-19", "settle"), 0.0)
        self.assertAlmostEqual(
            balance_on(book, "XAW.TO", "2026-06-19", "trade"), 100.0)
        # Rows without date_settle (SPLIT/OPENING_BALANCE) fall back to
        # the trade date under either basis.
        bare = [{"action": "OPENING_BALANCE", "date": "2026-06-19",
                 "symbol": "XAW.TO", "quantity": 7.0}]
        self.assertAlmostEqual(
            balance_on(bare, "XAW.TO", "2026-06-19", "settle"), 7.0)

    def test_pipeline_passes_project_tax_date(self):
        # Canada project (tax_date defaults to settle): the 06-19 record
        # date credits the seller whose sale settles 06-22 — ACB 1050,
        # gain 150, not 200.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                "2026-06-01 09:30:00 AM,2026-06-02 12:00:00 AM,Buy,XAW.TO,"
                "D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n"
                "2026-06-19 10:15:00 AM,2026-06-22 12:00:00 AM,Sell,XAW.TO,"
                "D,-100,12.00,1200.00,0.00,1200.00,CAD,1,Trades,Individual\n")
            (root / "distributions.map").write_text(
                "XAW.TO 2026-06-19 0.50\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("+50.00 ACB adjustment", r.stderr)
            gains = json.loads(
                (root / "work" / "margin_gains.json").read_text())
        sale = next(t for t in gains["transactions"]
                    if t.get("qty") == 100.0 and "gain" in t)
        self.assertAlmostEqual(sale["gain"], 150.0, places=2)


if __name__ == "__main__":
    unittest.main()
