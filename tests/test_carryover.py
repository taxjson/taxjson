"""Tests for taxjson-carryover: multi-year loss carryforward/carryback
ledger — Canada balance + T1A carryback candidates, US ST/LT worksheet,
--claimed folding, pipeline parity, truncation caveat.
"""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_carryover import (
    _us_worksheet,
    build_canada_ledger,
    build_usa_ledger,
    load_claimed,
    main,
    render,
    yearly_nets,
)
from taxjson.lib.core import TaxTransaction
from taxjson.lib.pipeline import GainsRequest, run_gains


def tx(action="BUYSELL", date="2025-01-15", symbol="XEI.TO", qty=0.0,
       net=0.0, price=0.0, time="09:30:00"):
    return {"action": action, "date": date, "date_settle": date,
            "time": time, "symbol": symbol, "quantity": qty,
            "price": price, "net_amount": net, "currency": "CAD"}


# Buy 2021; +10,000 gain in 2022; -4,000 loss in 2023; +2,500 gain in 2024.
CA_HISTORY = [
    tx(date="2021-05-10", qty=300, net=30000.0, price=100.0),
    tx(date="2022-03-01", qty=-100, net=20000.0, price=200.0),   # +10,000
    tx(date="2023-06-01", qty=-100, net=6000.0, price=60.0),     # -4,000
    tx(date="2024-04-01", qty=-100, net=12500.0, price=125.0),   # +2,500
]


def ca_nets(claimed=None, history=CA_HISTORY):
    txs = [TaxTransaction(**d) for d in history]
    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
        results = run_gains(txs, (), (), GainsRequest(
            country="canada", taxable=True, phantom_hint=False))
    nets = yearly_nets(results, "settle")
    return build_canada_ledger(nets, claimed or {})


class TestCanadaLedger(unittest.TestCase):
    def test_gain_loss_gain_flow(self):
        ledger = ca_nets()
        by_year = {r["year"]: r for r in ledger["rows"]}
        self.assertAlmostEqual(by_year[2022]["net_gain"], 10000.0, places=2)
        self.assertAlmostEqual(by_year[2023]["net_gain"], -4000.0, places=2)
        # Loss year: full amount can go back to 2022 (capped at its gain).
        self.assertEqual(by_year[2023]["carryback_candidates"],
                         [{"year": 2022, "amount": 4000.0}])
        self.assertAlmostEqual(by_year[2023]["carryforward_balance"],
                               4000.0, places=2)
        # Gain year after: balance still open, capped availability shown.
        self.assertAlmostEqual(by_year[2024]["available_to_apply"],
                               2500.0, places=2)
        self.assertAlmostEqual(ledger["final_carryforward"], 4000.0,
                               places=2)

    def test_claimed_reduces_balance(self):
        ledger = ca_nets(claimed={2024: 2500.0})
        by_year = {r["year"]: r for r in ledger["rows"]}
        self.assertAlmostEqual(by_year[2024]["claimed_applied"], 2500.0,
                               places=2)
        self.assertAlmostEqual(ledger["final_carryforward"], 1500.0,
                               places=2)

    def test_carryback_capped_and_not_double_counted(self):
        nets = {
            2022: {"net": 3000.0, "st": 0, "lt": 0, "dispositions": 1},
            2023: {"net": -5000.0, "st": 0, "lt": 0, "dispositions": 1},
            2024: {"net": -2000.0, "st": 0, "lt": 0, "dispositions": 1},
        }
        ledger = build_canada_ledger(nets, {})
        by_year = {r["year"]: r for r in ledger["rows"]}
        # 2023's loss consumes all of 2022's capacity (capped at 3000)...
        self.assertEqual(by_year[2023]["carryback_candidates"],
                         [{"year": 2022, "amount": 3000.0}])
        # ...so 2024's loss finds nothing left to carry back to.
        self.assertEqual(by_year[2024]["carryback_candidates"], [])
        self.assertAlmostEqual(ledger["final_carryforward"], 7000.0,
                               places=2)

    def test_carryback_only_reaches_three_years(self):
        nets = {
            2019: {"net": 9000.0, "st": 0, "lt": 0, "dispositions": 1},
            2023: {"net": -1000.0, "st": 0, "lt": 0, "dispositions": 1},
        }
        ledger = build_canada_ledger(nets, {})
        by_year = {r["year"]: r for r in ledger["rows"]}
        self.assertEqual(by_year[2023]["carryback_candidates"], [])

    def test_unmatched_claims_reported(self):
        nets = {2023: {"net": -100.0, "st": 0, "lt": 0, "dispositions": 1}}
        ledger = build_canada_ledger(nets, {2023: 500.0})
        self.assertAlmostEqual(ledger["unmatched_claims"], 400.0, places=2)


class TestUsWorksheet(unittest.TestCase):
    def test_st_loss_lt_gain(self):
        # ST -5000 vs LT +1000 → 4000 loss, 3000 deducted → 1000 ST carry.
        st, lt = _us_worksheet(-5000.0, 1000.0, 3000.0)
        self.assertAlmostEqual(st, 1000.0)
        self.assertAlmostEqual(lt, 0.0)

    def test_lt_loss_st_gain(self):
        st, lt = _us_worksheet(500.0, -6000.0, 3000.0)
        self.assertAlmostEqual(st, 0.0)
        self.assertAlmostEqual(lt, 2500.0)

    def test_both_losses_deduction_absorbs_st_first(self):
        st, lt = _us_worksheet(-2000.0, -4000.0, 3000.0)
        self.assertAlmostEqual(st, 0.0)
        self.assertAlmostEqual(lt, 3000.0)


class TestUsaLedger(unittest.TestCase):
    def _nets(self):
        return {
            2023: {"net": -4000.0, "st": -5000.0, "lt": 1000.0,
                   "dispositions": 2},
            2024: {"net": 1500.0, "st": 1500.0, "lt": 0.0,
                   "dispositions": 1},
        }

    def test_offset_and_carryover_chain(self):
        ledger = build_usa_ledger(self._nets(), {})
        by_year = {r["year"]: r for r in ledger["rows"]}
        self.assertAlmostEqual(by_year[2023]["ordinary_income_offset"],
                               3000.0, places=2)
        self.assertAlmostEqual(by_year[2023]["st_carryover"], 1000.0,
                               places=2)
        # 2024: ST 1500 - 1000 carry = +500, combined positive → all clear.
        self.assertAlmostEqual(by_year[2024]["st_carryover"], 0.0, places=2)
        self.assertAlmostEqual(ledger["final_carryforward"], 0.0, places=2)

    def test_claimed_zero_disables_assumed_offset(self):
        ledger = build_usa_ledger(self._nets(), {2023: 0.0})
        by_year = {r["year"]: r for r in ledger["rows"]}
        self.assertAlmostEqual(by_year[2023]["ordinary_income_offset"], 0.0)
        self.assertAlmostEqual(by_year[2023]["st_carryover"], 4000.0,
                               places=2)


class TestClaimedFile(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "claimed_losses.txt"
            p.write_text("# filed reality\n2023 3000\n2023 500\n"
                         "garbage line\n2024 -5\n")
            err = io.StringIO()
            with redirect_stderr(err):
                claimed = load_claimed(p)
        self.assertEqual(claimed, {2023: 3500.0})
        self.assertEqual(err.getvalue().count("line ignored"), 2)


class TestPipelineParity(unittest.TestCase):
    def test_yearly_nets_match_per_year_runs(self):
        # A wash-affected multi-year history: the ledger's per-year nets
        # must equal what a req.year pipeline run reports (the same
        # numbers the wash gains files carry).
        history = CA_HISTORY + [
            tx(date="2023-06-20", qty=50, net=3100.0, price=62.0),  # rebuy
        ]
        txs = [TaxTransaction(**d) for d in history]
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            full = run_gains(txs, (), (), GainsRequest(
                country="canada", taxable=True, phantom_hint=False))
        nets = yearly_nets(full, "settle")
        for year in sorted(nets):
            with redirect_stderr(io.StringIO()), \
                 redirect_stdout(io.StringIO()):
                yearly = run_gains(
                    [TaxTransaction(**d) for d in history], (), (),
                    GainsRequest(country="canada", taxable=True, year=year,
                                 phantom_hint=False))
            self.assertAlmostEqual(
                nets[year]["net"], yearly["summary"]["total_gain"],
                places=2, msg=f"year {year} diverges from pipeline")


class TestCliAndRender(unittest.TestCase):
    def test_end_to_end_text_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": CA_HISTORY}))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main([str(base), "--country", "canada"])
            self.assertEqual(rc, 0)
            text = out.getvalue()
            self.assertIn("CARRYOVER LEDGER", text)
            self.assertIn("carried back to 2022 via T1A", text)
            self.assertIn("inclusion rate", text)
            # History starts in 2021 and the first ledger year is 2022 —
            # no truncation warning expected... the buy year IS the first
            # tx year, and the first DISPOSITION year is later.
            out2 = io.StringIO()
            with redirect_stdout(out2), redirect_stderr(io.StringIO()):
                rc = main([str(base), "--country", "canada", "--json"])
            self.assertEqual(rc, 0)
            ledger = json.loads(out2.getvalue())
            self.assertAlmostEqual(ledger["final_carryforward"], 4000.0,
                                   places=2)
            self.assertEqual(ledger["first_transaction_year"], 2021)

    def test_truncation_caveat_when_first_year_has_dispositions(self):
        history = [
            tx(date="2022-01-05", qty=100, net=10000.0, price=100.0),
            tx(date="2022-03-01", qty=-100, net=12000.0, price=120.0),
        ]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": history}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                main([str(base), "--country", "canada"])
        self.assertIn("history starts in 2022", out.getvalue())

    def test_missing_file(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["/nonexistent.json"]), 2)


class TestRunWrapper(unittest.TestCase):
    def test_taxjson_carryover_subcommand(self):
        try:
            import tomllib  # noqa: F401
        except ImportError:
            try:
                import tomli  # noqa: F401
            except ImportError:
                self.skipTest("no TOML support on this interpreter")
        from taxjson.bin.taxjson_run import cmd_carryover

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n'
                '[accounts.margin]\ntype = "taxable"\n\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            work = root / "work"
            work.mkdir()
            (work / "margin_base.json").write_text(
                json.dumps({"transactions": CA_HISTORY}))
            # claimed_losses.txt auto-detected at the root.
            (root / "claimed_losses.txt").write_text("2024 2500\n")
            args = argparse.Namespace(dir=str(root), claimed=None, json=True)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    cmd_carryover(args)
            self.assertEqual(cm.exception.code, 0)
            ledger = json.loads(out.getvalue())
        self.assertAlmostEqual(ledger["final_carryforward"], 1500.0,
                               places=2)


if __name__ == "__main__":
    unittest.main()
