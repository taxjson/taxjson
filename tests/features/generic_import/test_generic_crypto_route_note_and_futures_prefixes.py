"""Re-audit-2 partition fixes, lists partition-07 / partition-08 (area
partD), data half: IB Asia-Pacific trade dates, the generic importer's
crypto advice and futures prefixes, Coinbase stablecoin quotes in
property mode, Kraken ledger de-pegs, the country-ownership tables'
completeness check and the US explain trace's per-account FIFO.

Every fixture is SYNTHETIC: fake account ids (55500001 / U5550001),  # pii-ok
invented tickers (QZ*, XYZ, ZZS).

Formerly: test_fix_a2_partd_data
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO = _paths.REPO


# --------------------------------------------------------------- generic
_GEN_MAP = """
[columns]
date = "Date"
action = "Action"
symbol = "Symbol"
quantity = "Qty"
price = "Price"
amount = "Amount"
fee = "Fee"

[actions]
"BUY" = "buy"
"SELL" = "sell"

[defaults]
currency = "USD"
"""


def _generic(rows, opts=""):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "generic_x.csv.toml").write_text(_GEN_MAP + opts)
        p = Path(td) / "generic_x.csv"
        p.write_text("Date,Action,Symbol,Qty,Price,Amount,Fee\n" + rows)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = GenericBrokerage().parse_file(p)
    return txs, err.getvalue()


class TestGenericImporter(unittest.TestCase):
    """A2-0152 / A2-0425 (no crypto route through generic), A2-0431
    (every futures prefix settles on the trade date).

    Formerly: test_fix_a2_partd_data.TestGenericImporter"""

    def test_settle_on_trade_date_says_it_is_not_a_crypto_route(self):
        txs, err = _generic("2025-03-03,BUY,BTC,0.5,90000,45000,0\n",
                            '\n[options]\nsettle_on_trade_date = true\n')
        self.assertEqual(txs[0]["symbol"], "BTC.US")
        self.assertIn("not a crypto route", err)
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("For a crypto-only export set", readme)
        ex = (REPO / "examples/generic_wealthsimple.toml").read_text(
            encoding="utf-8")
        self.assertNotIn("crypto-only export: no settlement cycle", ex)

    @rule(R.CA_DATE_09)
    def test_every_futures_prefix_settles_on_the_trade_date(self):
        rows = ("2025-12-01,BUY,{s},1,6000,0,0\n"
                "2025-12-31,SELL,{s},-1,6010,0,0\n")
        for sym in ("F:ESZ5", "/ESZ5", "\\ESZ5"):
            from taxjson.lib.brokerages.generic import GenericBrokerage
            settle = GenericBrokerage._settle_for(
                {}, lambda _r, _c: "", "row 2", "2025-12-31", "%Y-%m-%d",
                sym, "USD", True, False)
            self.assertEqual(settle, "2025-12-31", sym)
        del rows


if __name__ == "__main__":
    unittest.main()
