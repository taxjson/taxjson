"""FX capital gains on cash (s.39(1.1) / §988) — opt-in report."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_fx_cash import (apply_jurisdiction,
                                         build_ledger)

REPO_ROOT = Path(__file__).resolve().parent.parent

_RATES = {("USD", "2026-01-10"): 1.30, ("USD", "2026-02-10"): 1.40,
          ("USD", "2026-03-10"): 1.35, ("USD", "2026-06-10"): 1.25}


def _rate_of(cur, d):
    return _RATES.get((cur, d))


def _tx(action, date, cur, net, qty=0.0, **kw):
    return dict(action=action, date=date, date_settle=date,
                time="10:00:00", symbol=kw.pop("symbol", "AAA.US"),
                quantity=qty, currency=cur, net_amount=net,
                account=kw.pop("account", "margin"), **kw)


class TestLedger(unittest.TestCase):
    def test_sell_acquires_buy_disposes_with_acb(self):
        # Sell raises US$10,000 @1.30; later buy spends US$10,000
        # @1.40 -> gain = 10,000 x 0.10 = 1,000 CAD.
        txs = [_tx("BUYSELL", "2026-01-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2026-02-10", "USD", 10000.0, qty=50)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        self.assertAlmostEqual(doc["net_gain"], 1000.0)
        self.assertAlmostEqual(
            doc["per_currency"]["USD"]["disposed"], 10000.0)

    def test_pooled_average_rate(self):
        # 10k @1.30 + 10k @1.40 -> avg 1.35; spend 10k @1.35 -> 0 gain.
        txs = [_tx("BUYSELL", "2026-01-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2026-02-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2026-03-10", "USD", 10000.0, qty=50)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        self.assertAlmostEqual(doc["net_gain"], 0.0)
        self.assertAlmostEqual(doc["pools"]["USD"]["units"], 10000.0)

    def test_income_and_tax_move_cash(self):
        # Dividend acquires; withholding TAX (positive) disposes.
        txs = [_tx("DIVIDEND", "2026-01-10", "USD", 1000.0),
               _tx("TAX", "2026-02-10", "USD", 150.0)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        self.assertAlmostEqual(doc["net_gain"], 150 * 0.10, places=2)

    def test_loss_direction(self):
        txs = [_tx("BUYSELL", "2026-02-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2026-06-10", "USD", 10000.0, qty=50)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        self.assertAlmostEqual(doc["net_gain"], -1500.0)

    def test_overdraft_counted_and_zero_gain(self):
        txs = [_tx("BUYSELL", "2026-02-10", "USD", 5000.0, qty=50)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        self.assertEqual(doc["overdrafts"].get("USD"), 1)
        self.assertAlmostEqual(doc["net_gain"], 0.0)

    def test_base_currency_rows_ignored(self):
        txs = [_tx("BUYSELL", "2026-01-10", "CAD", 5000.0, qty=100,
                   symbol="BBB.TO")]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        self.assertEqual(doc["per_currency"], {})

    def test_prior_year_builds_pool_but_not_the_report(self):
        rates = dict(_RATES)
        rates[("USD", "2025-06-10")] = 1.20
        txs = [_tx("BUYSELL", "2025-06-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2026-02-10", "USD", 10000.0, qty=50)]
        doc = build_ledger(txs, "CAD", {}, 2026,
                           rate_of=lambda c, d: rates.get((c, d)))
        self.assertAlmostEqual(doc["net_gain"], 2000.0)   # 1.20->1.40
        self.assertAlmostEqual(
            doc["per_currency"]["USD"]["acquired"], 0.0)  # 2025 buy
        self.assertEqual(len(doc["events"]), 1)

    def test_missing_rate_skips_loudly(self):
        txs = [_tx("BUYSELL", "2026-04-01", "USD", 1000.0, qty=-10)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        self.assertEqual(doc["unrated"].get("USD"), 1)


class TestJurisdiction(unittest.TestCase):
    def test_ca_200_de_minimis_both_directions(self):
        self.assertEqual(apply_jurisdiction(150.0, "canada")
                         ["reportable"], 0.0)
        self.assertEqual(apply_jurisdiction(-150.0, "canada")
                         ["reportable"], 0.0)
        self.assertAlmostEqual(apply_jurisdiction(1200.0, "canada")
                               ["reportable"], 1000.0)
        self.assertAlmostEqual(apply_jurisdiction(-1200.0, "canada")
                               ["reportable"], -1000.0)

    def test_us_ordinary_full_amount(self):
        v = apply_jurisdiction(150.0, "usa")
        self.assertAlmostEqual(v["reportable"], 150.0)
        self.assertIn("ORDINARY", v["note"])


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestFxCashCli(unittest.TestCase):
    def _project(self, tmp, toggle=False):
        root = Path(tmp)
        work = root / "work"
        work.mkdir(parents=True)
        extra = "fx_cash_gains = true\n" if toggle else ""
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            + extra +
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')
        (work / "margin_raw.json").write_text(json.dumps(
            {"transactions": [
                _tx("BUYSELL", "2026-01-10", "USD", 10000.0, qty=-100),
                _tx("BUYSELL", "2026-02-10", "USD", 10000.0, qty=50),
            ]}))
        # Sheltered book with USD activity: must NOT appear (s.39
        # doesn't reach registered accounts).
        (work / "rrsp_raw.json").write_text(json.dumps(
            {"transactions": [
                _tx("BUYSELL", "2026-02-10", "USD", 99999.0, qty=500,
                    account="rrsp")]}))
        (work / "to_base.csv").write_text(
            "2026-01-10 12:00:00 USD CAD 1.30\n"
            "2026-02-10 12:00:00 USD CAD 1.40\n")
        return root

    def test_report_and_de_minimis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _cli(root, "fx-cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1,000.00", r.stdout)          # net
        self.assertIn("800.00", r.stdout)            # after the $200
        self.assertIn("REPORTABLE", r.stdout)
        self.assertIn("s.39(1.1)", r.stdout)
        self.assertNotIn("99,999", r.stdout)                # sheltered

    def test_json_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _cli(root, "fx-cash", "--json")
        doc = json.loads(r.stdout)
        self.assertAlmostEqual(doc["net_gain"], 1000.0)
        self.assertAlmostEqual(doc["reportable"], 800.0)
        self.assertEqual(doc["currency"], "CAD")

    def test_events_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _cli(root, "fx-cash", "--events")
        self.assertIn("2026-02-10 margin USD 10000", r.stdout)

    def test_toggle_gates_the_run_hook(self):
        # The command itself always works; the end-of-run report only
        # appears with fx_cash_gains = true. (Exercised via the hook
        # function directly — a full run needs a populated pipeline.)
        from taxjson.bin.taxjson_run import _fx_cash_after_run
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, toggle=False)
            (root / "reports").mkdir()
            out = io.StringIO()
            with redirect_stdout(out):
                _fx_cash_after_run(root, root / "work",
                                   root / "reports")
            self.assertEqual(out.getvalue(), "")
            self.assertFalse((root / "reports" / "fx_cash.rpt")
                             .exists(), "off by default")
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, toggle=True)
            (root / "reports").mkdir()
            out = io.StringIO()
            with redirect_stdout(out):
                _fx_cash_after_run(root, root / "work",
                                   root / "reports")
            self.assertIn("reportable 800.00", out.getvalue())
            rpt = (root / "reports" / "fx_cash.rpt").read_text()
        self.assertIn("REPORTABLE", rpt)
        self.assertIn("800.00", rpt)


if __name__ == "__main__":
    unittest.main()
