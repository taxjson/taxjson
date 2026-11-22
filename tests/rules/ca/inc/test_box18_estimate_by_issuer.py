"""Re-audit-2 tests-pins (pins3, group g6b): fixes in taxjson_run.py's
reports, guards, wording gates and settings that held but that no test
failed on when reverted. Each TestCase names the A2 finding and the code
it pins; every test was checked to fail with that code reverted.

All data is synthetic (invented tickers, round amounts).

Formerly: test_fix_a2_pins3_g6b
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _doc(path, txs, **extra):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"transactions": txs}
    d.update(extra)
    path.write_text(json.dumps(d))
    return path


# ----------------------------------------------- A2-0934, A2-1586, A2-1587
def _t(action, date, sym, qty=0.0, net=0.0, cur="CAD", **kw):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": sym, "quantity": qty,
         "price": abs(net / qty) if qty else 0.0, "net_amount": net,
         "currency": cur}
    d.update(kw)
    return d


def _cgd(root, *entries):
    """[[capital_gains_dividends]] entries (symbol, account) for 2025,
    amount "all", appended to taxjson.toml."""
    with (root / "taxjson.toml").open("a") as f:
        for sym, acct in entries:
            f.write(f'\n[[capital_gains_dividends]]\nsymbol = "{sym}"\n'
                    f'year = 2025\namount = "all"\n'
                    + (f'account = "{acct}"\n' if acct else ""))


# ------------------------------------------------------------- A2-1593
class TestBox18EstimateByIssuer(unittest.TestCase):
    """_box18_into_estimate moves a mapped T5 box 18 amount out of the
    Canadian dividends for a Canadian issuer and out of the foreign
    dividends otherwise (income_dating.is_canadian_issuer) — both
    branches pinned (CA-INC-06).

    Formerly: test_fix_a2_pins3_g6b.TestBox18EstimateByIssuer"""

    @rule(R.CA_INC_06)
    def test_each_issuer_leaves_its_own_bucket(self):
        from taxjson.cli import summaries as cli_summaries
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            rows = [_t("DIVIDEND", "2025-03-10", "ZZS.TO", net=100.0,
                       gross_amount=100.0, id="d1"),
                    _t("DIVIDEND", "2025-03-11", "ZZF.US", net=30.0,
                       gross_amount=30.0, id="d2")]
            _doc(root / "work" / "margin_raw.json", rows)
            _doc(root / "work" / "margin_base.json", rows)
            _cgd(root, ("ZZS.TO", ""), ("ZZF.US", ""))
            est = {"div_ca": 500.0, "div_foreign": 300.0}
            fb = {"margin": 300.0}
            cli_summaries._box18_into_estimate(root, est, ["margin"], 2025, fb)
        self.assertAlmostEqual(est["div_ca"], 400.0)
        self.assertAlmostEqual(est["div_foreign"], 270.0)
        self.assertAlmostEqual(fb["margin"], 270.0)
        self.assertAlmostEqual(est["cg_div"], 130.0)


if __name__ == "__main__":
    unittest.main()
