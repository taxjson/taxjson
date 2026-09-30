"""Medium-round planning fixes: the report/view commands.

Synthetic projects only. Each test names the audit finding it pins.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TOML = ('[settings]\nyear = 2026\ncountry = "canada"\n'
         'base_currency = "CAD"\n'
         '[accounts.margin]\ntype = "taxable"\n'
         '[accounts.rrsp]\ntype = "sheltered"\n')


def _runsub(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _json(root, *args):
    r = _runsub(root, *args, "--json")
    assert r.returncode == 0, (r.stdout, r.stderr)
    return json.loads(r.stdout)


def _project(tmp, toml=_TOML):
    root = Path(tmp)
    (root / "work").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(toml)
    return root


def _write(root, name, txs, summary=None, **extra):
    doc = {"transactions": txs}
    if summary is not None:
        doc["summary"] = summary
    doc.update(extra)
    (root / "work" / name).write_text(json.dumps(doc))


def _trade(date, sym, qty, price, net, fee=0.0, cur="CAD", **kw):
    t = {"action": "BUYSELL", "date": date, "time": "10:00:00",
         "symbol": sym, "quantity": qty, "price": price,
         "net_amount": net, "fee": fee, "currency": cur}
    t.update(kw)
    return t


class TestFeesAndTradesSigns(unittest.TestCase):
    """R1-54 / R1-269 (FEE rows positive = charged) and S039-11
    (trades view signed net/fee, signed totals)."""

    def _proj(self, tmp):
        root = _project(tmp)
        _write(root, "margin_raw.json", [
            _trade("2026-03-02", "AAA.TO", 100, 10.0, 1002.0, fee=2.0),
            _trade("2026-03-03", "AAA.TO", -100, 12.0, 1197.0, fee=3.0),
            # IB rebate on a trade: fee -0.50
            _trade("2026-03-04", "BBB.TO", -10, 1.0, 10.5, fee=-0.5),
            # Penny option close: commission exceeds the gross.
            _trade("2026-03-05", "BBB260116C00010000.TO", -1, 0.01,
                   -0.25, fee=1.25),
            {"action": "FEE", "date": "2026-03-06", "time": "09:30:00",
             "symbol": "", "currency": "CAD", "net_amount": 10.0},
            {"action": "FEE", "date": "2026-03-07", "time": "09:30:00",
             "symbol": "", "currency": "CAD", "net_amount": -1.0},
        ])
        return root

    def test_fee_rows_positive_is_charged(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            doc = _json(root, "fees")
        fees = {(r["action"], r["date"]): r["fee"] for r in doc["rows"]}
        self.assertEqual(fees[("FEE", "2026-03-06")], 10.0)
        self.assertEqual(fees[("FEE", "2026-03-07")], -1.0)
        # 2 + 3 - 0.5 + 1.25 trade fees, + 10 charged - 1 refunded
        self.assertAlmostEqual(doc["totals"]["CAD"], 14.75, places=2)

    def test_trades_view_is_signed_taxtext(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            r = _runsub(root, "trades", "2026", "margin")
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = [ln.split() for ln in r.stdout.splitlines()
                     if ln.startswith("BUYSELL")]
            rebate = next(ln for ln in lines if ln[3] == "BBB.TO")
            self.assertEqual(rebate[-1], "-0.50")
            penny = next(ln for ln in lines if ln[3].startswith("BBB26"))
            self.assertEqual(penny[-2], "-0.25")
            # Signed sell total: 1197 + 10.5 - 0.25
            self.assertIn("TOTAL SELL:     1,207.25 CAD", r.stdout)
            doc = _json(root, "trades", "2026", "margin")
            self.assertAlmostEqual(doc["totals"]["sell"]["CAD"], 1207.25)

    def test_trades_sum_sold_is_signed(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            doc = _json(root, "trades-sum")
        self.assertAlmostEqual(doc["totals"]["CAD"]["sold"], 1207.25)
        self.assertAlmostEqual(doc["totals"]["CAD"]["bought"], 1002.0)


if __name__ == "__main__":
    unittest.main()
