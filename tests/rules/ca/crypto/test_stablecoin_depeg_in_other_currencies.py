"""Second-audit DEFERRED findings, the rest (A2-0590, A2-1014, A2-1091,
A2-1052/A2-1054, A2-1056). Every fixture is synthetic: invented tickers, fake
account ids and made-up amounts.

Formerly: test_fix_def_rest"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from taxjson.rules import ids as R

from _support.parser_blank_settle_and_renames import _brokerage


# ------------------------------------------ A2-1056: hints the map answers
import os


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# ----------------------------- A2-0590: de-peg check on non-USD-valued fills
from _support.cb_parse import _parse as cb_parse
from _support.kraken_ledger import _parse as kr_parse, _ledger as kr_ledger, _KT_H

RATES = ("2025-06-02 12:00:00 USD CAD 1.3500 boc\n"
         "2025-06-02 12:00:00 EUR CAD 1.5000 boc\n")


class _WithRates:
    """Install a rates table (the run's work/to_base.csv) the way
    taxjson-brokerage --rates does."""

    def setUp(self):
        from taxjson.lib.brokerages import _crypto_common as cc
        self._td = tempfile.TemporaryDirectory()
        rp = Path(self._td.name) / "to_base.csv"
        rp.write_text(RATES)
        cc.set_depeg_rates(rp, "CAD")
        self.addCleanup(cc.set_depeg_rates, None, None)
        self.addCleanup(self._td.cleanup)


def _cb(typ, qty, price, ccy="CAD"):
    sub = abs(qty) * price
    return (f"c1,2025-06-02 12:00:00 UTC,{typ},USDC,{qty},{ccy},"
            f"${price:.2f},${sub:.2f},${sub:.2f},$0.00,"
            f"{typ} {abs(qty)} USDC\n")


@rule(R.CA_CRYPTO_02)
class TestDepegNonUsdFills(_WithRates, unittest.TestCase):
    """A2-0590: only USD-valued stablecoin fills were checked against the
    peg. A fill valued in CAD or EUR is now converted through the day's
    rate (the run's rates file, the conversion stage's own) first.

    Formerly: test_fix_def_rest.TestDepegNonUsdFills"""

    def test_coinbase_buy_and_sell_usdc_priced_in_cad(self):
        for typ, qty in (("Buy", 100), ("Sell", -100)):
            _tx, err, _ = cb_parse(_cb(typ, qty, 1.20))
            # 1.20 CAD / 1.35 = 0.8889 USD
            self.assertIn("USDC traded at 0.8889 USD", err, typ)
        _tx, err, _ = cb_parse(_cb("Buy", 100, 1.35))
        self.assertNotIn("traded at", err)

    def test_kraken_stablecoin_fiat_pairs(self):
        def trade(pair, price, vol):
            return _KT_H + (f"T1,O1,{pair},2025-06-02 16:00:00,buy,limit,"
                            f"{price},{price * vol:.2f},0,{vol},,,\n")
        _t, err = kr_parse({"kr_trades.csv": trade("USDC/CAD", 1.20, 100)},
                           "kr_trades.csv")
        self.assertIn("USDC traded at 0.8889 USD", err)
        # 0.70 EUR x 1.50 / 1.35 = 0.7778 USD
        _t, err = kr_parse({"kr_trades.csv": trade("USDT/EUR", 0.70, 100)},
                           "kr_trades.csv")
        self.assertIn("USDT traded at 0.7778 USD", err)
        _t, err = kr_parse({"kr_trades.csv": trade("USDT/EUR", 0.90, 100)},
                           "kr_trades.csv")
        self.assertNotIn("traded at", err)
        # CAD/USDC: the stablecoin is the quote, 1 / price CAD each:
        # 1 / 0.80 = 1.25 CAD = 0.9259 USD; 1 / 0.74 = 1.0010 USD.
        _t, err = kr_parse({"kr_trades.csv": trade("CAD/USDC", 0.80, 100)},
                           "kr_trades.csv")
        self.assertIn("USDC traded at 0.9259 USD", err)
        _t, err = kr_parse({"kr_trades.csv": trade("CAD/USDC", 0.74, 100)},
                           "kr_trades.csv")
        self.assertNotIn("traded at", err)

    def test_kraken_ledger_instant_conversion_to_cad(self):
        led = kr_ledger([
            "L1,R1,2025-06-02 12:00:00,spend,,currency,USDC,spot,-100,0,0",
            "L2,R1,2025-06-02 12:00:00,receive,,currency,ZCAD,spot,120,0,"
            "120"])
        _t, err = kr_parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIn("USDC traded at 0.8889 USD", err)

    def test_no_rate_for_the_currency_is_said_not_guessed(self):
        from taxjson.lib.brokerages import _crypto_common as cc
        cc.set_depeg_rates(None, None)
        _tx, err, _ = cb_parse(_cb("Buy", 100, 1.20, ccy="GBP"))
        self.assertNotIn("traded at", err)
        self.assertIn("not checked for a de-peg", err)


class TestDepegIsCanadasCashModelOnly(_WithRates, unittest.TestCase):
    """Formerly: test_fix_def_rest.TestDepegIsCanadasCashModelOnly"""
    @rule(R.CA_CRYPTO_02)
    @rule_absent(R.CA_CRYPTO_02, country="usa")
    @rule(R.US_CRYPTO_02)
    def test_us_books_the_cad_priced_usdc_purchase_instead(self):
        _tx, err, _ = cb_parse(_cb("Buy", 100, 1.20))
        self.assertIn("traded at", err)                       # Canada
        tx, err, _ = cb_parse(_cb("Buy", 100, 1.20),
                              stablecoins_as_cash=False)      # USA
        self.assertNotIn("traded at", err)
        self.assertEqual([(t["symbol"], t["quantity"]) for t in tx
                          if t["action"] == "BUYSELL"], [("USDC", 100.0)])


@rule(R.CA_CRYPTO_02)
class TestDepegRatesThroughTheCli(unittest.TestCase):
    """Formerly: test_fix_def_rest.TestDepegRatesThroughTheCli"""
    def test_brokerage_rates_flag(self):
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "to_base.csv"
            rp.write_text(RATES)
            from _support.cb_parse import _HDR
            rc, err = _brokerage('coinbase',
                                 {'cb.csv': "Transactions\n" + _HDR
                                  + _cb("Buy", 100, 1.20)},
                                 None, '--country', 'canada',
                                 '--rates', str(rp))
        self.assertEqual(rc, 0, err)
        self.assertIn("USDC traded at 0.8889 USD", err)

    def test_run_passes_its_rates(self):
        from _support.crypto_project import CB_HEADER, _env, _project, _run_cli
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)          # USD/CAD 1.40 seeded
            row = ("c1,2026-03-02 12:00:00 UTC,Buy,USDC,100,CAD,$1.20,"
                   "$120.00,$120.00,$0.00,Bought 100 USDC\n")
            (root / "inputs" / "crypto" / "cb_2026.csv").write_text(
                "Transactions\n" + CB_HEADER + row)
            r = _run_cli(root, "run", "--no-input",
                         env=_env(Path(td), TAXJSON_OFFLINE="1"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 1.20 / 1.40 = 0.8571 USD, on the console (ATTENTION).
        self.assertIn("USDC traded at 0.8571 USD", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
