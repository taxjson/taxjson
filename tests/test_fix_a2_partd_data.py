"""Re-audit-2 partition fixes, lists partition-07 / partition-08 (area
partD), data half: IB Asia-Pacific trade dates, the generic importer's
crypto advice and futures prefixes, Coinbase stablecoin quotes in
property mode, Kraken ledger de-pegs, the country-ownership tables'
completeness check and the US explain trace's per-account FIFO.

Every fixture is SYNTHETIC: fake account ids (55500001 / U5550001),  # pii-ok
invented tickers (QZ*, XYZ, ZZS).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


def _env(home=None, **extra):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", NO_COLOR="1")
    if home:
        env["HOME"] = str(home)
    env.update(extra)
    return env


def _tool(module, *args, home=None):
    return subprocess.run([PY, "-m", module, *map(str, args)],
                          capture_output=True, text=True, env=_env(home),
                          stdin=subprocess.DEVNULL, cwd=REPO)


# ------------------------------------------------------- IB clock
class TestIbAsiaClock(unittest.TestCase):
    """A2-1302: every Asia-Pacific fill takes its exchange's date."""

    @rule("CA-DATE-SESSION")
    def test_jpy_hkd_cnh_evening_fills_move_to_the_local_day(self):
        from taxjson.lib.brokerages.ib_extractor import _ib_market_trade_date
        for cur in ("JPY", "HKD", "CNH", "SGD", "AUD", "NZD"):
            d, _t, stamp = _ib_market_trade_date(
                "2026-03-03", "21:45:00", "Stocks", cur, "")
            self.assertEqual(d, "2026-03-04", cur)
            self.assertTrue(stamp, cur)


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
    (every futures prefix settles on the trade date)."""

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

    @rule("CA-DATE-09")
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


# --------------------------------------------------------------- Coinbase
_CB_H = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
         "Price Currency,Price at Transaction,Subtotal,Total (inclusive of "
         "fees and/or spread),Fees and/or Spread,Notes,ID\n")


def _cb(rows, cash):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cb.csv"
        p.write_text(_CB_H + rows, encoding="utf-8")
        par = CoinbaseBrokerage()
        par.stablecoins_as_cash = cash
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return par.parse_file(p), err.getvalue()


class TestCoinbaseStablecoinQuote(unittest.TestCase):
    """A2-0730: in property mode (a US project) a fill priced in a
    stablecoin with no 'on BASE-QUOTE' pair is not folded to USD."""

    @rule("US-CRYPTO-02")
    @rule_absent("US-CRYPTO-02", country="canada")
    @rule("CA-CRYPTO-02")
    def test_property_mode_refuses_rather_than_drop_the_leg(self):
        row = ("2025-03-03T15:00:00Z,Advanced Trade Buy,ETH,0.5,PYUSD,2000,"
               "1000,1000,0,Bought 0.5 ETH for 1000 PYUSD,cbid-1\n")
        with self.assertRaises(ValueError) as cm:
            _cb(row, cash=False)
        self.assertIn("PYUSD", str(cm.exception))
        # Canada's cash model: PYUSD is US-dollar cash, one ETH buy.
        txs, _ = _cb(row, cash=True)
        self.assertEqual([(t["symbol"], t["quantity"], t["currency"])
                          for t in txs], [("ETH", 0.5, "USD")])

    def test_property_mode_with_pair_books_both_legs(self):
        row = ("2025-03-03T15:00:00Z,Advanced Trade Buy,ETH,0.5,PYUSD,2000,"
               "1000,1000,0,Bought 0.5 ETH for 1000 PYUSD on ETH-PYUSD,"
               "cbid-2\n")
        txs, _ = _cb(row, cash=False)
        self.assertEqual(sorted((t["symbol"], t["quantity"]) for t in txs),
                         [("ETH", 0.5), ("PYUSD", -1000.0)])


# ----------------------------------------------------------------- Kraken
_KR_H = ('"txid","refid","time","type","subtype","aclass","asset","wallet",'
         '"amount","fee","balance"\n')


class TestKrakenLedgerDepeg(unittest.TestCase):
    """A2-1284: a ledger instant trade USDC -> USD off the peg warns."""

    @rule("CA-CRYPTO-02")
    def test_ledger_instant_trade_off_peg_warns(self):
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        rows = ('"LA1","RX1","2025-03-03 15:00:00","spend","","currency",'
                '"USDC","spot / main",-1000,0,0\n'
                '"LA2","RX1","2025-03-03 15:00:00","receive","","currency",'
                '"ZUSD","spot / main",900,0,900\n')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kraken_ledgers.csv"
            p.write_text(_KR_H + rows, encoding="utf-8")
            par = KrakenBrokerage()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                txs = par.parse_file(p)
        self.assertEqual(txs, [])
        self.assertIn("USDC traded at 0.9000 USD", err.getvalue())


# ---------------------------------------------------- ownership tables
class TestOwnershipTables(unittest.TestCase):
    """A2-0719: check_tax_rules verifies the country-ownership tables, and
    the Canada-only --foreign-roc dividend / --slip-gains are in them."""

    def _check(self):
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import check_tax_rules
        finally:
            sys.path.pop(0)
        return check_tax_rules

    def test_tables_complete_today(self):
        self.assertEqual(self._check().ownership_problems(), [])

    def test_gaps_are_caught(self):
        from taxjson.lib import country as C
        chk = self._check()
        saved = (dict(C.FLAG_COUNTRY), dict(C.FLAG_VALUE_COUNTRY),
                 dict(C.COMMAND_COUNTRY))
        try:
            del C.FLAG_COUNTRY["--slip-gains"]           # help: no marker
            C.FLAG_COUNTRY["--no-such-flag"] = C.CANADA  # no CLI, no why
            del C.FLAG_VALUE_COUNTRY[("--foreign-roc", "dividend")]
            C.COMMAND_COUNTRY["no-such-command"] = C.USA
            probs = "\n".join(chk.ownership_problems())
        finally:
            C.FLAG_COUNTRY.clear()
            C.FLAG_COUNTRY.update(saved[0])
            C.FLAG_VALUE_COUNTRY.clear()
            C.FLAG_VALUE_COUNTRY.update(saved[1])
            C.COMMAND_COUNTRY.clear()
            C.COMMAND_COUNTRY.update(saved[2])
        self.assertIn("--no-such-flag is not an option", probs)
        self.assertIn("--no-such-flag is missing from country._FLAG_ATTRS",
                      probs)
        self.assertIn("FLAG_COUNTRY['--no-such-flag'] has no reason", probs)
        self.assertIn("--foreign-roc: its help says", probs)
        self.assertIn("no-such-command", probs)

    def test_value_level_refusal(self):
        from taxjson.lib.country import flag_country_problems
        self.assertTrue(flag_country_problems(
            "usa", {"--foreign-roc": "dividend"}))
        self.assertEqual(flag_country_problems(
            "usa", {"--foreign-roc": "acb"}), [])
        self.assertEqual(flag_country_problems(
            "canada", {"--foreign-roc": "dividend"}), [])
        self.assertTrue(flag_country_problems(
            "usa", {"--slip-gains": ["2025=1.00"]}))


# ----------------------------------------------------- wash-sales --explain
class TestExplainPerAccount(unittest.TestCase):
    """A2-0155 / A2-0318: the US trace of the merged taxable books keeps
    FIFO per account, like the table it explains."""

    @rule("US-BASIS-01")
    def test_wash_sales_explain_matches_the_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            for a, rows in (("eq1", "BUYSELL 2024-06-03 10:00:00 XYZ.US 50 USD "
                                    "20 1000 0\n"
                                    "BUYSELL 2025-03-03 10:00:00 XYZ.US -50 USD "
                                    "15 750 0\n"),
                            ("eq2", "BUYSELL 2024-02-01 10:00:00 XYZ.US 50 USD "
                                    "30 1500 0\n"
                                    "BUYSELL 2025-03-10 10:00:00 XYZ.US 10 USD "
                                    "15 150 0\n")):
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / f"{a}.tt").write_text(rows)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\n[accounts.eq1]\ntype = "taxable"\n'
                '[accounts.eq2]\ntype = "taxable"\n')
            base = [PY, "-m", "taxjson.bin.taxjson_run", "-C", str(root)]
            r = subprocess.run(base + ["run", "--no-input"],
                               capture_output=True, text=True, env=_env(td),
                               stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            x = subprocess.run(base + ["wash-sales", "--explain"],
                               capture_output=True, text=True, env=_env(td),
                               stdin=subprocess.DEVNULL)
        self.assertIn("Against Lot: 2024-06-03", x.stdout, x.stderr)
        self.assertNotIn("Against Lot: 2024-02-01", x.stdout)


if __name__ == "__main__":
    unittest.main()
