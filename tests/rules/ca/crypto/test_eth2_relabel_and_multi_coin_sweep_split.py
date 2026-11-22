"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import os
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import tax_logic as TL


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _text(country, rid, **settings):
    """The rendered text of one rule under `settings`."""
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


# ------------------------------------------------------------ crypto
class TestCoinbaseEth2IsEth(unittest.TestCase):
    """A2-0479 / A2-0815: Coinbase's ETH -> ETH2 convert is a relabel;
    tax-logic says so for each country.

    Formerly: test_fix_a2_spec.TestCoinbaseEth2IsEth"""

    def _check(self, country, rid):
        import tempfile
        from taxjson.lib import markets
        from _support.crypto_rows import _bs, _cb_row, _parse_cb
        self.assertIn("Coinbase's ETH2", _text(country, rid))
        self.assertIn("GLOBAL ETH2 ETH", _text(country, rid))
        # The fold is the project's ticker.map line (owner 2026-10-04).
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tm = Path(td.name) / "ticker.map"
        tm.write_text("GLOBAL ETH2 ETH\n")
        markets.use_ticker_map(tm)
        self.addCleanup(markets.use_ticker_map, None)
        txs, err = _parse_cb(
            _cb_row("b1", "2025-01-02 10:00:00 UTC", "Buy", "ETH", "1",
                    "CAD", "2000", "2000", "2000", "0")
            + _cb_row("c1", "2025-06-02 10:00:00 UTC", "Convert", "ETH",
                      "-1", "CAD", "4000", "4000", "4000", "0",
                      "Converted 1 ETH to 1 ETH2"))
        self.assertEqual([(t["symbol"], t["quantity"]) for t in _bs(txs)],
                         [("ETH", 1.0)])
        self.assertIn("same property ETH", err)

    @rule(R.CA_CRYPTO_10)
    def test_canada(self):
        self._check("canada", "CA-CRYPTO-10")

    @rule(R.US_CRYPTO_06)
    def test_usa(self):
        self._check("usa", "US-CRYPTO-06")


class TestKrakenDustSweepSplit(unittest.TestCase):
    """A2-1469: a multi-coin dust sweep's receipt is split by amountusd,
    equally without it.

    Formerly: test_fix_a2_spec.TestKrakenDustSweepSplit"""

    def _sales(self, rows, header, cash):
        from _support.kraken_ledger import _ledger, _parse
        txs, err = _parse({"kr_ledgers.csv": _ledger(rows, header)},
                          "kr_ledgers.csv", cash=cash)
        return ({t["symbol"]: round(t["net_amount"], 6) for t in txs
                 if t["action"] == "BUYSELL"}, err)

    def _check(self, cash):
        from _support.kraken_ledger import _KL_H, _KL_H2
        legs = ["L1,RS1,2025-03-01 12:00:00,spend,,currency,ADA,spot,-1,0,0",
                "L2,RS1,2025-03-01 12:00:00,spend,,currency,AVAX,spot,-0.01,"
                "0,0",
                "L3,RS1,2025-03-01 12:00:00,spend,,currency,BNB,spot,-0.001,"
                "0,0",
                "L4,RS1,2025-03-01 12:00:00,receive,,currency,ZUSD,spot,1.80,"
                "0,1.80"]
        eq, err = self._sales(legs, _KL_H, cash)
        self.assertEqual(eq, {"ADA": 0.6, "AVAX": 0.6, "BNB": 0.6})
        self.assertIn("equal shares", err)
        usd = [legs[0] + ",0.90,,", legs[1] + ",0.60,,", legs[2] + ",0.30,,",
               legs[3] + ",1.80,,"]
        by, err = self._sales(usd, _KL_H2, cash)
        self.assertEqual(by, {"ADA": 0.9, "AVAX": 0.6, "BNB": 0.3})
        self.assertIn("amountusd", err)

    @rule(R.CA_CRYPTO_11)
    def test_canada(self):
        self._check(cash=True)

    @rule(R.US_CRYPTO_07)
    def test_usa(self):
        self._check(cash=False)


if __name__ == "__main__":
    unittest.main()
