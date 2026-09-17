"""Tests for taxjson-fees (trading-fee report by brokerage).

Covers: brokerage grouping, the BUYSELL/ASSIGN-only + rebate fee rule, dedup
by id (and the missing-id case), the --year/settlement filter, --to currency
conversion (incl. silent-fallback surfacing), and the full set of comparison
statistics (mean, median, %notional, per-share vs per-contract) via --json.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(dirp, name, brokerage, txs):
    p = Path(dirp) / name
    p.write_text(json.dumps({
        "metadata": {"source_brokerage": brokerage},
        "transactions": txs,
    }))
    return p


def _run(*args):
    cmd = [sys.executable, "-m", "taxjson.bin.taxjson_fees", *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


def _run_json(*args):
    r = _run(*args, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _tx(**kw):
    base = {"action": "BUYSELL", "date": "2026-03-01", "symbol": "AAA.US",
            "currency": "USD", "quantity": 10, "commission": 0.0, "fee": 0.0,
            "gross_amount": 1000.0}
    base.update(kw)
    return base


class TestFees(unittest.TestCase):
    def test_per_brokerage_totals_and_actions_filter(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "margin_ib.json", "ib", [
                _tx(id="1", commission=5.0),
                _tx(id="2", fee=3.0, symbol="AAA260101C00010000.US"),
                _tx(id="3", action="DIVIDEND", fee=99.0),   # not a trade
                _tx(id="4"),                                # zero fee
            ])
            _write(d, "lira_questrade.json", "questrade", [_tx(id="5", commission=2.0)])
            doc = _run_json("--cache", d)
        self.assertAlmostEqual(doc["brokerages"]["ib"]["total"], 8.0)
        self.assertAlmostEqual(doc["brokerages"]["questrade"]["total"], 2.0)
        self.assertAlmostEqual(doc["total"]["total"], 10.0)
        self.assertEqual(doc["brokerages"]["ib"]["trades"], 2)  # dividend+zero excluded

    def test_stat_math(self):
        # Known inputs: a stock trade, an option trade, and a rebate.
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a_ib.json", "ib", [
                _tx(id="s", commission=10.0, quantity=100, gross_amount=1000.0),
                _tx(id="o", fee=6.0, quantity=2, gross_amount=2000.0,
                    symbol="AAA260101C00010000.US"),
                _tx(id="r", commission=-1.0, quantity=10, gross_amount=500.0),
            ])
            b = _run_json("--cache", d)["brokerages"]["ib"]
        self.assertAlmostEqual(b["total"], 15.0)         # 10 + 6 - 1
        self.assertAlmostEqual(b["mean"], 5.0)           # 15 / 3
        self.assertAlmostEqual(b["median"], 6.0)         # median(-1, 6, 10)
        self.assertAlmostEqual(b["stock_fee"], 9.0)      # 10 + (-1)
        self.assertAlmostEqual(b["option_fee"], 6.0)
        # per-share = stock fee / stock shares = 9 / 110
        self.assertAlmostEqual(b["per_share"], 9.0 / 110.0)
        # per-contract = option fee / contracts = 6 / 2
        self.assertAlmostEqual(b["per_contract"], 3.0)
        # %notional = all fees (all rows have notional>0) / total notional
        self.assertAlmostEqual(b["pct_notional"], 15.0 / 3500.0 * 100)

    def test_zero_notional_excluded_from_pct(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a_ib.json", "ib", [
                _tx(id="s", commission=10.0, gross_amount=1000.0),
                _tx(id="x", action="ASSIGN", commission=5.0, gross_amount=0.0,
                    net_amount=0.0),
            ])
            b = _run_json("--cache", d)["brokerages"]["ib"]
        self.assertAlmostEqual(b["total"], 15.0)
        # the zero-notional ASSIGN fee is excluded from both numerator & denom
        self.assertAlmostEqual(b["pct_notional"], 10.0 / 1000.0 * 100)

    def test_rebate_included(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a_ib.json", "ib", [
                _tx(id="a", commission=5.0),
                _tx(id="b", commission=-2.0),
            ])
            b = _run_json("--cache", d)["brokerages"]["ib"]
        self.assertAlmostEqual(b["total"], 3.0)
        self.assertEqual(b["trades"], 2)

    def test_dedup_by_id(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a_ib.json", "ib", [_tx(id="dup", commission=4.0)])
            _write(d, "b_ib.json", "ib", [_tx(id="dup", commission=4.0)])
            doc = _run_json("--cache", d)
        self.assertAlmostEqual(doc["brokerages"]["ib"]["total"], 4.0)
        self.assertEqual(doc["meta"]["dups_collapsed"], 1)

    def test_missing_id_not_deduped_and_reported(self):
        # Rows without ids can't be deduped; they must all count and be flagged.
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a_ib.json", "ib", [{k: v for k, v in _tx(commission=4.0).items()}])
            _write(d, "b_ib.json", "ib", [{k: v for k, v in _tx(commission=4.0).items()}])
            doc = _run_json("--cache", d)
        self.assertAlmostEqual(doc["brokerages"]["ib"]["total"], 8.0)
        self.assertEqual(doc["meta"]["rows_without_id"], 2)

    def test_year_filter_is_trade_date_basis(self):
        # TRADE-date basis, matching the per-trade `taxjson fees`
        # view (2026-09 audit unified the two — they used to disagree
        # at year boundaries). Fees are incurred at trade.
        with tempfile.TemporaryDirectory() as d:
            _write(d, "m_ib.json", "ib", [
                _tx(id="a", date="2026-02-02", commission=7.0),
                _tx(id="b", date="2025-02-02", commission=50.0),
                _tx(id="c", date="2025-12-31", date_settle="2026-01-02",
                    commission=9.0),   # trades in 2025 → excluded
            ])
            b = _run_json("--cache", d, "--year", "2026")["brokerages"]["ib"]
        self.assertAlmostEqual(b["total"], 7.0)

    def test_by_account_grouping(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "margin_ib.json", "ib",
                   [{**_tx(id="a", commission=5.0), "account": "margin"}])
            _write(d, "rrsp_ib.json", "ib",
                   [{**_tx(id="b", commission=3.0), "account": "rrsp"}])
            doc = _run_json("--cache", d, "--by-account")
        self.assertIn("ib/margin", doc["brokerages"])
        self.assertIn("ib/rrsp", doc["brokerages"])
        self.assertAlmostEqual(doc["brokerages"]["ib/margin"]["total"], 5.0)

    def test_to_currency_conversion_and_by_currency(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "m_ib.json", "ib", [
                _tx(id="a", currency="USD", commission=10.0),
                _tx(id="b", currency="CAD", commission=4.0),
            ])
            rates = Path(d) / "rates.csv"
            rates.write_text("2026-03-01 12:00:00 USD CAD 1.40000\n")
            doc = _run_json("--cache", d, "--to", "CAD", "--rates", str(rates))
        b = doc["brokerages"]["ib"]
        self.assertAlmostEqual(b["total"], 10 * 1.40 + 4)   # 18.0
        self.assertIn("USD", b["by_currency"])
        self.assertAlmostEqual(b["by_currency"]["USD"]["total"], 10.0)  # native

    def test_native_multicurrency_text(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "m_ib.json", "ib", [
                _tx(id="a", currency="USD", commission=10.0),
                _tx(id="b", currency="CAD", commission=4.0),
            ])
            r = _run("--cache", d)
        self.assertEqual(r.returncode, 0, r.stderr)
        # One row per (brokerage, currency); currency is its own CUR column.
        self.assertRegex(r.stdout, r"ib\s+USD")
        self.assertRegex(r.stdout, r"ib\s+CAD")

    def test_fx_fallback_is_surfaced(self):
        # Rate file lacks the trade date → default-rate fallback must be loud.
        with tempfile.TemporaryDirectory() as d:
            _write(d, "m_ib.json", "ib", [_tx(id="a", currency="USD",
                                              commission=10.0)])
            rates = Path(d) / "rates.csv"
            rates.write_text("2020-01-01 12:00:00 USD CAD 1.30000\n")
            r = _run("--cache", d, "--to", "CAD", "--rates", str(rates))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("default-rate", (r.stdout + r.stderr))

    def test_zero_fee_broker_listed(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "m_ib.json", "ib", [_tx(id="a", commission=5.0)])
            _write(d, "w_webull.json", "webull", [_tx(id="b")])  # zero fee
            doc = _run_json("--cache", d)
        self.assertNotIn("webull", doc["brokerages"])
        self.assertIn("webull", doc["meta"]["zero_fee_brokers"])

    def test_to_requires_rates(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "m_ib.json", "ib", [_tx(id="a", commission=1.0)])
            r = _run("--cache", d, "--to", "CAD")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--to requires --rates", r.stderr)

    def test_file_without_brokerage_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "merged.json"
            p.write_text(json.dumps({"metadata": {},
                                     "transactions": [_tx(id="a", fee=5.0)]}))
            r = _run(str(p))
        # No-data is SUCCESS (exit-code convention) — returning 1 here
        # crashed `taxjson run` for commission-free projects
        # (FUZZ-2026-07 #H).
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No trading fees", r.stdout)


if __name__ == "__main__":
    unittest.main()
