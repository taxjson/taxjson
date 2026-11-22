"""`tjs sanity` and a USD stablecoin row of a crypto snapshot (backlog
TJ-4950). A Canadian project's books hold a USD stablecoin as US-dollar
cash (tax-logic CA-CRYPTO-02), never as a position, so a snapshot row
`symbol = "USDC.KR"`, `asset_type = "crypto"` could never tie: it read
MISSING_IN_TAXJSON, kept the checklist's sanity step open, and the hint
said to book an opening balance of it. The row is now left out as a cash
row is, with a note naming the coin; the classification is the parsers'
(the shipped `usd_stablecoins` list as ticker.map STABLE lines change it,
the code read as the Kraken parser reads an asset code). Only when the
books carry no position of the coin: a project whose books do (a `.tt`
BUYSELL line books one) is compared as before; a reward paid in the coin
is an income row of the books, not a position.
A US project's stablecoin is a coin (US-CRYPTO-02) and is compared.

All data synthetic.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

NOTE = "not compared as a coin: USDC (12.5) in kraken.toml"


def _project(td, country, base, holdings, inventory, ticker_map=""):
    from _support.filing_sanity import _gains
    root = Path(td) / "proj"
    root.mkdir()
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\nbase_currency = "{base}"\n'
        'source_currencies = []\nlocal_timezone = "America/Toronto"\n'
        '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n', encoding="utf-8")
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map, encoding="utf-8")
    _gains(root, "crypto", inventory)
    (root / "kraken.toml").write_text(
        'schema_version = "1.0"\n[meta]\naccount = "crypto"\n\n' + "".join(
            f'[[holding]]\nsymbol = "{s}"\nquantity = {q}\nasset_type = "{t}"\n\n'
            for s, q, t in holdings), encoding="utf-8")
    return root


def _sanity(root, *args):
    from taxjson.lib.dispatch import python_module_argv
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), "sanity", "crypto=kraken.toml", *args]),
        cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600,
        env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0", NO_COLOR="1"))


class TestSanityStablecoinRows(unittest.TestCase):
    HOLDINGS = (("SOL.KR", 4.0, "crypto"), ("USDC.KR", 12.5, "crypto"))

    def _root(self, country="canada", base="CAD", holdings=None, inventory=None,
              ticker_map=""):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return _project(td.name, country, base, holdings or self.HOLDINGS,
                        inventory if inventory is not None else {"SOL": 4.0}, ticker_map)

    @rule(R.CA_CRYPTO_02)
    def test_canada_leaves_the_row_out_as_cash_and_says_so(self):
        root = self._root()
        r = _sanity(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: tickers and quantities agree", r.stdout)
        self.assertNotIn("MISSING_IN_TAXJSON", r.stdout)
        self.assertNotIn("tjs opening", r.stdout + r.stderr)
        (note,) = [ln for ln in r.stderr.splitlines() if "not compared as a coin" in ln]
        self.assertIn(NOTE, note)
        self.assertIn("US-dollar cash in this project's books (tax-logic CA-CRYPTO-02)", note)
        self.assertIn("`STABLE <coin> NO`", note)
        # --json: the note is in `notes`, and leaves nothing unchecked.
        doc = json.loads(_sanity(root, "--json").stdout)
        self.assertEqual((doc["clean"], doc["complete"], doc["discrepancies"]),
                         (True, True, []))
        (jnote,) = doc["notes"]
        self.assertIn(NOTE, jnote)
        # The cost section holds no row of it either (--json and --details).
        self.assertEqual([c["symbol"] for c in doc["cost"]["rows"]], ["SOL"])
        self.assertEqual(doc["cost"]["not_compared"], 1)
        r = _sanity(root, "--details")
        self.assertIn("Not compared: SOL (the report states no cost)", r.stdout)
        self.assertNotIn("USDC", r.stdout)

    @rule(R.CA_CRYPTO_02)
    def test_canada_a_bare_code_and_another_coins_difference(self):
        # The bare code is the coin too; a real coin's difference is
        # still one, with its hint.
        root = self._root(holdings=(("SOL", 5.0, "crypto"), ("USDT", 3.0, "crypto")))
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("QTY_MISMATCH", r.stdout)
        self.assertNotIn("USDT ", r.stdout)
        self.assertIn("not compared as a coin: USDT (3) in kraken.toml", r.stderr)

    @rule(R.CA_CRYPTO_02)
    def test_canada_books_that_hold_the_coin_as_a_position_are_compared(self):
        # A `.tt` BUYSELL of USDC puts it in the books as a position: the
        # row ties as before, and a difference is one, in both directions.
        root = self._root(inventory={"SOL": 4.0, "USDC": 12.5})
        r = _sanity(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: tickers and quantities agree", r.stdout)
        self.assertNotIn("not compared as a coin", r.stderr)
        root = self._root(inventory={"SOL": 4.0, "USDC": 100.0})
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"USDC\s+QTY_MISMATCH\s+100\.0\s+12\.5")
        self.assertNotIn("not compared as a coin", r.stderr)
        # The file holds none of it: missing from the holdings, as before.
        root = self._root(inventory={"SOL": 4.0, "USDC": 100.0},
                          holdings=(("SOL.KR", 4.0, "crypto"),))
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"USDC\s+MISSING_IN_HOLDINGS")
        # A coin a STABLE line calls a stablecoin while the books hold it:
        # its real difference is not hidden.
        root = self._root(inventory={"SOL": 9.0}, ticker_map="STABLE SOL USD\n")
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"SOL\s+QTY_MISMATCH\s+9\s+4\s")

    @rule(R.CA_CRYPTO_02)
    def test_canada_krakens_wallet_suffixes_and_case_name_the_coin(self):
        # `USDC.F` and `USDC.HOLD` are Kraken's wallets of USDC (the
        # parser's own reading of an asset code); a look-alike is a coin.
        root = self._root(holdings=(("SOL.KR", 4.0, "crypto"), ("USDC.F", 2.0, "crypto"),
                                    ("USDC.HOLD", 5.0, "crypto"), ("usdc.kr", 1.0, "Crypto"),
                                    ("USDCX.KR", 3.0, "crypto")))
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not compared as a coin: USDC (8) in kraken.toml", r.stderr)
        self.assertRegex(r.stdout, r"USDCX\s+MISSING_IN_TAXJSON")
        self.assertIn("1 discrepancy", r.stdout)

    @rule(R.CA_CRYPTO_02)
    def test_canada_a_row_that_is_not_crypto_is_compared(self):
        # A security whose ticker spells like a stablecoin is no coin.
        root = self._root(holdings=(("SOL.KR", 4.0, "crypto"), ("USDC", 12.5, "equity")))
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MISSING_IN_TAXJSON", r.stdout)
        self.assertNotIn("not compared as a coin", r.stderr)

    @rule(R.CA_CRYPTO_02)
    def test_canada_the_ticker_maps_stable_lines_decide(self):
        # `STABLE USDC NO`: a coin again, compared. `STABLE QZS USD`: a
        # stablecoin the shipped list does not name, left out.
        root = self._root(ticker_map="STABLE USDC NO\n")
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"USDC\s+MISSING_IN_TAXJSON")
        self.assertNotIn("not compared as a coin", r.stderr)
        root = self._root(ticker_map="STABLE QZS USD\n",
                          holdings=(("SOL.KR", 4.0, "crypto"), ("QZS.KR", 7.0, "crypto")))
        r = _sanity(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not compared as a coin: QZS (7) in kraken.toml", r.stderr)

    @rule(R.US_CRYPTO_02)
    def test_a_us_project_compares_it_as_a_coin(self):
        root = self._root(country="usa", base="USD")
        r = _sanity(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"USDC\s+MISSING_IN_TAXJSON")
        self.assertNotIn("not compared as a coin", r.stderr)
        # Held in the books, it ties.
        root = self._root(country="usa", base="USD", inventory={"SOL": 4.0, "USDC": 12.5})
        r = _sanity(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("not compared as a coin", r.stderr)


H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance,"
     "amountusd,feeusd,feecurrency\n")
# 2800 CAD for 2000 USDC, 1000 CAD for 4 SOL, a reward of 1.5 USDC.
LEDGER = H + (
    "L01,R01,2026-01-05 15:00:00,deposit,,currency,ZCAD,spot / main,5000,0,5000,3600,0,CAD\n"
    "L02,RT1,2026-01-12 15:00:00,spend,,currency,ZCAD,spot / main,-2800,0,2200,-2000,0,CAD\n"
    "L03,RT1,2026-01-12 15:00:00,receive,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
    "L04,RT2,2026-02-02 15:00:00,spend,,currency,ZCAD,spot / main,-1000,0,1200,-720,0,CAD\n"
    "L05,RT2,2026-02-02 15:00:00,receive,,currency,SOL,spot / main,4,0,4,720,0,SOL\n"
    "L06,RW1,2026-03-02 15:00:00,earn,reward,currency,USDC,spot / main,1.5,0,2001.5,1.5,0,USDC\n")


class TestSanityStablecoinRowsThroughARun(unittest.TestCase):
    """The books `tjs run` builds of a Kraken ledger: a reward paid in
    USDC is an income row naming the coin, and no position of it."""

    def _cli(self, root, *args):
        from taxjson.lib.dispatch import python_module_argv
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run", ["-C", str(root), *args]),
            cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900,
            env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0", NO_COLOR="1",
                     TAXJSON_LOCAL_TZ="America/Toronto"))

    def _project(self, tt=""):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "proj"
        (root / "inputs" / "crypto").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\n'
            'country = "canada"\nprovince = "ON"\nbase_currency = "CAD"\n'
            'source_currencies = ["USD"]\n'
            '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n', encoding="utf-8")
        (root / "inputs" / "crypto" / "kr_ledgers.csv").write_text(LEDGER, encoding="utf-8")
        if tt:
            (root / "inputs" / "crypto" / "buy.tt").write_text(tt, encoding="utf-8")
        (root / "kraken.toml").write_text(
            'schema_version = "1.0"\n[meta]\naccount = "crypto"\n\n'
            '[[holding]]\nsymbol = "SOL.KR"\nquantity = 4.0\nasset_type = "crypto"\n\n'
            '[[holding]]\nsymbol = "USDC.KR"\nquantity = 2001.5\nasset_type = "crypto"\n',
            encoding="utf-8")
        os.chmod(root, 0o700)
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root

    @rule(R.CA_CRYPTO_02)
    def test_a_reward_in_the_coin_is_no_position_of_it(self):
        root = self._project()
        # The books do carry a row naming USDC: the reward's income.
        rows = json.loads((root / "work" / "crypto_base.json").read_text(
            encoding="utf-8"))["transactions"]
        self.assertEqual([r["action"] for r in rows if r.get("symbol") == "USDC"],
                         ["DIVIDEND"])
        r = self._cli(root, "sanity", "crypto=kraken.toml")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: tickers and quantities agree", r.stdout)
        self.assertNotIn("MISSING_IN_TAXJSON", r.stdout)
        self.assertNotIn("tjs opening", r.stdout + r.stderr)
        self.assertIn("not compared as a coin: USDC (2001.5) in kraken.toml", r.stderr)

    @rule(R.CA_CRYPTO_02)
    def test_a_tt_purchase_of_the_coin_is_a_position_and_is_compared(self):
        root = self._project(tt="BUYSELL 2026-01-10 10:00:00 USDC 12.5 USD 1.00 12.50\n")
        r = self._cli(root, "sanity", "crypto=kraken.toml")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"USDC\s+QTY_MISMATCH\s+12\.5\s+2001\.5")
        self.assertNotIn("not compared as a coin", r.stderr)


if __name__ == "__main__":
    unittest.main()
