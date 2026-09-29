"""Regression tests for the Kraken / Coinbase / generic-CSV / .tt
parser hardening (fail loudly rather than book a wrong number).

All fixtures are synthetic. Items:
  H3  generic importer row-level sanity checks
  H4  .tt converter: unknown actions, dates/times, inline comments
  M3  Kraken trades: fee currency from the ledger (coin-charged fees)
  M4  Coinbase: USDC is USD cash (consistent with Kraken)
  M9  Kraken legacy ledger types, asset suffixes, feecurrency, columns
  LOW multi-asset dust sweeps, UTC -> local dates, CA$ amounts
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


# ------------------------------------------------------------------ H3
_G_CSV = ("Date,Type,Ticker,Shares,Price,Gross,Commission,Net,Currency\n"
          "2025-03-05,BUY,ABC,100,10.00,1000.00,9.99,-1009.99,CAD\n"
          "2025-03-10,SELL,ABC,100,12.00,1200.00,9.99,1190.01,CAD\n")
_G_TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
           'quantity="Shares"\nprice="Price"\namount="Net"\n'
           'fee="Commission"\ncurrency="Currency"\n'
           '[actions]\n"BUY"="buy"\n"SELL"="sell"\n')


def _generic(csv_text, toml_text):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_t.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(toml_text)
        return _run(GenericBrokerage().parse_file, c)


class TestGenericSanity(unittest.TestCase):
    def test_correct_mapping_parses(self):
        txs, err = _generic(_G_CSV, _G_TOML)
        self.assertEqual([round(t["net_amount"], 2) for t in txs],
                         [1009.99, 1190.01])
        self.assertNotIn("warning", err)

    def test_fee_and_amount_swapped_refused(self):
        bad = _G_TOML.replace('amount="Net"', 'amount="Commission"') \
                     .replace('fee="Commission"', 'fee="Net"')
        with self.assertRaises(ValueError) as cm:
            _generic(_G_CSV, bad)
        self.assertIn("mapping", str(cm.exception))

    def test_two_fields_on_one_header_refused(self):
        bad = _G_TOML.replace('fee="Commission"', 'fee="Net"')
        with self.assertRaises(ValueError) as cm:
            _generic(_G_CSV, bad)
        self.assertIn("SAME CSV header", str(cm.exception))
        self.assertIn("amount, fee", str(cm.exception))

    def test_gross_as_amount_warns_and_mismatch_raises(self):
        bad = _G_TOML.replace('amount="Net"', 'amount="Gross"')
        # Within 1% (fee is 1% of 1000): a loud warning per row.
        txs, err = _generic(_G_CSV, bad)
        self.assertIn("GROSS column mapped as amount", err)
        # A row where the fee is >1% of the gross: refused outright.
        csv = _G_CSV + "2025-04-03,BUY,XYZ,10,5.00,50.00,1.00,-51.00,USD\n"
        with self.assertRaises(ValueError) as cm:
            _generic(csv, bad)
        self.assertIn("differs from qty x price + fee", str(cm.exception))
        self.assertIn("line 4", str(cm.exception))

    def test_large_fee_needs_explicit_opt_in(self):
        csv = ("Date,Type,Ticker,Shares,Price,Gross,Commission,Net,Currency\n"
               "2025-03-05,BUY,ABC,1,10.00,10.00,4.95,-14.95,CAD\n")
        with self.assertRaises(ValueError) as cm:
            _generic(csv, _G_TOML)
        self.assertIn("allow_large_fees", str(cm.exception))
        txs, _ = _generic(csv, _G_TOML + "[options]\nallow_large_fees = true\n")
        self.assertAlmostEqual(txs[0]["net_amount"], 14.95)

    def test_option_multiplier(self):
        csv = ("Date,Type,Ticker,Shares,Price,Gross,Commission,Net,Currency\n"
               "2025-03-05,BUY,AAPL250321C00200000,2,1.50,300.00,1.30,"
               "-301.30,USD\n")
        txs, _ = _generic(csv, _G_TOML)
        self.assertAlmostEqual(txs[0]["net_amount"], 301.30)
        self.assertAlmostEqual(txs[0]["gross_amount"], 300.0)

    def test_currency_must_be_explicit(self):
        bad = _G_TOML.replace('currency="Currency"\n', '')
        with self.assertRaises(ValueError) as cm:
            _generic(_G_CSV, bad)
        self.assertIn("no currency", str(cm.exception))
        # A default is enough...
        txs, _ = _generic(_G_CSV, bad + '[defaults]\ncurrency = "CAD"\n')
        self.assertEqual(txs[0]["currency"], "CAD")
        # ...but a mapped column with an EMPTY cell and no default is not.
        csv = _G_CSV.replace("-1009.99,CAD", "-1009.99,")
        with self.assertRaises(ValueError) as cm:
            _generic(csv, _G_TOML)
        self.assertIn("refusing to assume USD", str(cm.exception))

    def test_ca_dollar_prefix(self):
        csv = _G_CSV.replace("-1009.99", "CA$-1009.99")
        txs, _ = _generic(csv, _G_TOML)
        self.assertAlmostEqual(txs[0]["net_amount"], 1009.99)

    def test_shipped_example_mapping_still_loads(self):
        toml = (REPO_ROOT / "examples" / "generic_wealthsimple.toml").read_text()
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
               "2025-01-15,BUY,XEI,100,10.00,-1005.00,CAD\n")
        txs, _ = _generic(csv, toml)
        self.assertEqual(txs[0]["symbol"], "XEI.TO")


# ------------------------------------------------------------------ H4
class TestTtStrict(unittest.TestCase):
    def _tt(self, text):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manual.tt"
            p.write_text(text)
            return _run(tt_to_json, p, "m")

    def test_unknown_actions_raise_with_suggestion(self):
        for bad, guess in (("BUYSEL", "BUYSELL"), ("buysell", "BUYSELL"),
                           ("Dividend", "DIVIDEND"), ("SELL", "BUYSELL")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    self._tt("# header\n"
                             f"{bad} 2025-01-02 09:30:00 A.US 1 USD 1 1 0\n")
                msg = str(cm.exception)
                self.assertIn("manual.tt:2", msg)
                self.assertIn(f"Did you mean {guess}?", msg)
                self.assertIn("BUYSELL, TRANSFER", msg)

    def test_cli_exits_nonzero_on_unknown_action(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.tt"
            p.write_text("SELL 2025-01-02 09:30:00 A.US 1 USD 1 1 0\n")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_convert_tt",
                 str(p)], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown .tt action 'SELL'", r.stderr)

    def test_bad_date_and_time_raise(self):
        for line, what in (
                ("BUYSELL 2025-02-30 09:30:00 A.US 1 USD 1 1 0", "date"),
                ("BUYSELL 02/01/2025 09:30:00 A.US 1 USD 1 1 0", "date"),
                ("BUYSELL 2025-02-01 9:30 A.US 1 USD 1 1 0", "time"),
                ("BUYSELL 2025-02-01 25:00:00 A.US 1 USD 1 1 0", "time"),
                # Missing time used to shift every field left.
                ("BUYSELL 2025-02-01 A.US 1 USD 1 1 0", "time")):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as cm:
                    self._tt(line + "\n")
                self.assertIn(what, str(cm.exception))

    def test_inline_comment_stripped(self):
        out, _ = self._tt("BUYSELL 2025-01-02 09:30:00 A.US 10 USD 1 10 0"
                          "  # bought on a whim\n")
        self.assertEqual(len(out["transactions"]), 1)
        self.assertEqual(out["transactions"][0]["net_amount"], 10.0)

    def test_declared_only_as_real_token(self):
        from taxjson.lib.pipeline import MANUAL_TRANSFER_DECLARATION
        out, _ = self._tt(
            "TRANSFER 2025-01-02 09:30:00 A.US -5 USD 1 5  # not DECLARED\n"
            "TRANSFER 2025-01-03 09:30:00 A.US -5 USD 1 5 DECLARED\n")
        d = [t.get("description") for t in out["transactions"]]
        self.assertNotEqual(d[0], MANUAL_TRANSFER_DECLARATION)
        self.assertEqual(d[1], MANUAL_TRANSFER_DECLARATION)

    def test_stray_trailing_token_raises(self):
        with self.assertRaises(ValueError) as cm:
            self._tt("BUYSELL 2025-01-02 09:30:00 A.US 1 USD 1 1 0 oops\n")
        self.assertIn("unexpected trailing token", str(cm.exception))

    def test_total_warning_names_file_and_line(self):
        _, err = self._tt("# c\n\nBUYSELL 2025-01-02 09:30:00 A.US 10 USD"
                          " 5 80 0\n")
        self.assertIn("warning: manual.tt:3:", err)


# ------------------------------------------------------------------ M3
_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")


def _kraken_dir(files):
    from taxjson.lib.brokerages.kraken import KrakenBrokerage
    td = tempfile.TemporaryDirectory()
    for name, text in files.items():
        (Path(td.name) / name).write_text(text)
    return td, KrakenBrokerage


class TestKrakenCoinFees(unittest.TestCase):
    TRADES = (_KT_H +
              # buy 10 SOL for 2000 CAD; fee (5 CAD-equiv) taken in SOL
              "T1,O1,SOL/CAD,2025-06-02 16:00:00.1,buy,limit,200,2000,5,10,,,\n"
              # sell 4 SOL for 800 CAD; fee taken in SOL
              "T2,O2,SOL/CAD,2025-06-03 16:00:00.1,sell,limit,200,800,2,4,,,\n"
              # buy 1 ETH for 3000 CAD; fee in CAD (quote)
              "T3,O3,ETH/CAD,2025-06-04 16:00:00.1,buy,limit,3000,3000,7.5,1,,,\n")
    LEDGER = (_KL_H +
              "L1,T1,2025-06-02 16:00:00,trade,tradespot,currency,SOL,spot,10,0.025,9.975\n"
              "L2,T1,2025-06-02 16:00:00,trade,tradespot,currency,ZCAD,spot,-2000,0,0\n"
              "L3,T2,2025-06-03 16:00:00,trade,tradespot,currency,SOL,spot,-4,0.01,5.965\n"
              "L4,T2,2025-06-03 16:00:00,trade,tradespot,currency,ZCAD,spot,800,0,800\n"
              "L5,T3,2025-06-04 16:00:00,trade,tradespot,currency,XETH,spot,1,0,1\n"
              "L6,T3,2025-06-04 16:00:00,trade,tradespot,currency,ZCAD,spot,-3000,7.5,0\n")

    def test_coin_fee_comes_out_of_the_coins(self):
        td, K = _kraken_dir({"kr_trades.csv": self.TRADES,
                             "kr_ledgers.csv": self.LEDGER})
        with td:
            txs, err = _run(K().parse_file,
                            Path(td.name) / "kr_trades.csv")
        by = {t["id"]: t for t in txs}
        # Buy: 10 − 0.025 SOL arrived, for exactly 2000 CAD, no CAD fee.
        self.assertAlmostEqual(by["T1"]["quantity"], 9.975)
        self.assertAlmostEqual(by["T1"]["net_amount"], 2000.0)
        self.assertEqual(by["T1"]["fee"], 0.0)
        # Sell: 4 + 0.01 SOL left, proceeds exactly 800 CAD.
        self.assertAlmostEqual(by["T2"]["quantity"], -4.01)
        self.assertAlmostEqual(by["T2"]["net_amount"], 800.0)
        # Quote-currency fee unchanged: 1 ETH for 3000 + 7.5 CAD.
        self.assertAlmostEqual(by["T3"]["quantity"], 1.0)
        self.assertAlmostEqual(by["T3"]["net_amount"], 3007.5)
        self.assertAlmostEqual(by["T3"]["fee"], 7.5)
        self.assertNotIn("can't be verified", err)
        self.assertIn("2 fill(s) had the fee taken in the traded coin", err)

    def test_units_reconcile_with_ledger(self):
        td, K = _kraken_dir({"kr_trades.csv": self.TRADES,
                             "kr_ledgers.csv": self.LEDGER})
        with td:
            txs, _ = _run(K().parse_file, Path(td.name) / "kr_trades.csv")
        sol = sum(t["quantity"] for t in txs if t["symbol"] == "SOL")
        # ledger: (10 − 0.025) + (−4 − 0.01)
        self.assertAlmostEqual(sol, 5.965)

    def test_no_ledger_warns_fee_currency_unverified(self):
        td, K = _kraken_dir({"kr_trades.csv": self.TRADES})
        with td:
            txs, err = _run(K().parse_file, Path(td.name) / "kr_trades.csv")
        self.assertIn("fee currency of 3 fill(s) can't be verified", err)
        # Legacy behaviour otherwise: full vol, quote-unit fee.
        self.assertAlmostEqual(txs[0]["quantity"], 10.0)

    def test_mismatched_ledger_raises(self):
        bad = self.LEDGER.replace("SOL,spot,10,", "SOL,spot,11,")
        td, K = _kraken_dir({"kr_trades.csv": self.TRADES,
                             "kr_ledgers.csv": bad})
        with td, self.assertRaises(ValueError) as cm:
            _run(K().parse_file, Path(td.name) / "kr_trades.csv")
        self.assertIn("!= trades vol", str(cm.exception))

    def test_ledger_leg_of_wrong_asset_raises(self):
        bad = self.LEDGER.replace("L1,T1,2025-06-02 16:00:00,trade,tradespot,"
                                  "currency,SOL", "L1,T1,2025-06-02 16:00:00,"
                                  "trade,tradespot,currency,ADA")
        td, K = _kraken_dir({"kr_trades.csv": self.TRADES,
                             "kr_ledgers.csv": bad})
        with td, self.assertRaises(ValueError) as cm:
            _run(K().parse_file, Path(td.name) / "kr_trades.csv")
        self.assertIn("do not match the pair", str(cm.exception))

    def test_trades_required_columns(self):
        csv = ("txid,ordertxid,pair,time,type,ordertype,price,cost,vol\n"
               "T1,O1,SOL/CAD,2025-06-01 12:00:00.1,buy,limit,200,2000,10\n")
        td, K = _kraken_dir({"kr_trades.csv": csv})
        with td, self.assertRaises(ValueError) as cm:
            _run(K().parse_file, Path(td.name) / "kr_trades.csv")
        self.assertIn("required column(s) missing: fee", str(cm.exception))

    def test_garbage_number_raises(self):
        csv = _KT_H + "T1,O1,SOL/CAD,2025-06-02 16:00:00.1,buy,limit,200,2k,5,10,,,\n"
        td, K = _kraken_dir({"kr_trades.csv": csv})
        with td, self.assertRaises(ValueError) as cm:
            _run(K().parse_file, Path(td.name) / "kr_trades.csv")
        self.assertIn("unparseable cost", str(cm.exception))


# ------------------------------------------------------------------ M9
class TestKrakenLegacyLedger(unittest.TestCase):
    def test_staking_dividend_and_suffixes(self):
        csv = (_KL_H +
               "L1,R1,2023-03-01 12:00:00,staking,,currency,DOT.S,spot,0.5,0,100.5\n"
               "L2,R2,2023-03-02 12:00:00,transfer,spottostaking,currency,DOT.S,spot,100,0,100\n"
               "L3,R3,2023-03-03 12:00:00,earn,reward,currency,ETH2.S,spot,0.01,0,1.01\n"
               "L4,R4,2023-03-04 12:00:00,dividend,,currency,USD.HOLD,spot,1.25,0,1.25\n"
               "L5,R5,2023-03-05 12:00:00,staking,,currency,XXBT.M,spot,0.001,0,1\n")
        td, K = _kraken_dir({"kr_ledgers.csv": csv})
        with td:
            txs, err = _run(K().parse_file, Path(td.name) / "kr_ledgers.csv")
        divs = {t["symbol"]: t for t in txs if t["action"] == "DIVIDEND"}
        self.assertEqual(sorted(divs), ["BTC", "DOT", "ETH", "USD"])
        self.assertAlmostEqual(divs["DOT"]["quantity"], 0.5)
        # Fiat reward: cash income in its own currency, no position.
        self.assertEqual(divs["USD"]["net_amount"], 1.25)
        self.assertEqual(divs["USD"]["currency"], "USD")
        buys = sorted(t["symbol"] for t in txs if t["action"] == "BUYSELL")
        self.assertEqual(buys, ["BTC", "DOT", "ETH"])
        self.assertNotIn("unhandled type", err)

    def test_feecurrency_other_than_asset(self):
        csv = ("txid,refid,time,type,subtype,aclass,subclass,asset,wallet,"
               "amount,fee,balance,amountusd,feeusd,balanceusd,feecurrency\n"
               "L1,R1,2026-03-01 12:00:00,earn,reward,currency,crypto,SOL,"
               "spot / main,0.2,0.05,10.2,30.00,7.50,1500,USD\n")
        td, K = _kraken_dir({"kr_ledgers.csv": csv})
        with td:
            txs, _ = _run(K().parse_file, Path(td.name) / "kr_ledgers.csv")
        div = next(t for t in txs if t["action"] == "DIVIDEND")
        buy = next(t for t in txs if t["action"] == "BUYSELL")
        # The USD fee does not reduce the COINS credited...
        self.assertAlmostEqual(buy["quantity"], 0.2)
        self.assertAlmostEqual(buy["net_amount"], 30.0)
        # ...it reduces the income.
        self.assertAlmostEqual(div["net_amount"], 22.5)

    def test_same_ccy_fee_unchanged(self):
        csv = ("txid,refid,time,type,subtype,aclass,subclass,asset,wallet,"
               "amount,fee,balance,amountusd,feeusd,balanceusd,feecurrency\n"
               "L1,R1,2026-03-01 12:00:00,earn,reward,currency,crypto,SOL,"
               "spot / main,0.2,0.05,10.2,30.00,7.50,1500,SOL\n")
        td, K = _kraken_dir({"kr_ledgers.csv": csv})
        with td:
            txs, _ = _run(K().parse_file, Path(td.name) / "kr_ledgers.csv")
        buy = next(t for t in txs if t["action"] == "BUYSELL")
        self.assertAlmostEqual(buy["quantity"], 0.15)
        self.assertAlmostEqual(buy["net_amount"], 22.5)

    def test_ledger_required_columns(self):
        csv = ("txid,refid,time,type,subtype,aclass,asset,amount,balance\n"
               "L1,R1,2023-03-01 12:00:00,staking,,currency,DOT.S,0.5,100.5\n")
        td, K = _kraken_dir({"kr_ledgers.csv": csv})
        with td, self.assertRaises(ValueError) as cm:
            _run(K().parse_file, Path(td.name) / "kr_ledgers.csv")
        self.assertIn("required column(s) missing: fee", str(cm.exception))

    def test_withdrawal_coin_fee_is_disposed(self):
        csv = (_KL_H +
               "L1,F1,2026-05-04 16:00:00,withdrawal,,currency,TAO,spot,-0.1,0.002,14\n")
        td, K = _kraken_dir({"kr_ledgers.csv": csv})
        with td:
            txs, _ = _run(K().parse_file, Path(td.name) / "kr_ledgers.csv")
        xfer = next(t for t in txs if t["action"] == "TRANSFER")
        fee = next(t for t in txs if t["action"] == "BUYSELL")
        self.assertAlmostEqual(xfer["quantity"], -0.1)
        self.assertAlmostEqual(fee["quantity"], -0.002)
        self.assertEqual(fee["symbol"], "TAO")


# ------------------------------------------------------------------ LOW
class TestKrakenDustSweep(unittest.TestCase):
    def test_every_swept_asset_is_booked(self):
        csv = ("txid,refid,time,type,subtype,aclass,subclass,asset,wallet,"
               "amount,fee,balance,amountusd,feeusd,balanceusd,feecurrency\n"
               "L1,D1,2026-09-19 12:00:00,spend,dustsweeping,currency,fiat,CAD,spot,-0.0043,0,0,-0.003,0,0,\n"
               "L2,D1,2026-09-19 12:00:00,spend,dustsweeping,currency,crypto,ADA,spot,-4.0,0,0,-0.9,0,0,\n"
               "L3,D1,2026-09-19 12:00:00,spend,dustsweeping,currency,crypto,AVAX,spot,-0.01,0,0,-0.1,0,0,\n"
               "L4,D1,2026-09-19 12:00:00,spend,dustsweeping,currency,crypto,BNB,spot,-0.001,0,0,-0.6,0,0,\n"
               "L5,D1,2026-09-19 12:00:00,receive,dustsweeping,currency,fiat,USD,spot,1.6,0,1.6,1.6,0,1.6,\n")
        td, K = _kraken_dir({"kr_ledgers.csv": csv})
        with td:
            txs, err = _run(K().parse_file, Path(td.name) / "kr_ledgers.csv")
        sells = {t["symbol"]: t for t in txs if t["action"] == "BUYSELL"}
        self.assertEqual(sorted(sells), ["ADA", "AVAX", "BNB"])
        self.assertAlmostEqual(sells["ADA"]["quantity"], -4.0)
        # Proceeds split by amountusd share (0.9 / 1.603 of 1.60).
        self.assertAlmostEqual(sells["ADA"]["net_amount"],
                               1.6 * 0.9 / 1.603, places=6)
        total = sum(t["net_amount"] for t in sells.values())
        self.assertAlmostEqual(total, 1.6 * 1.6 / 1.603, places=6)
        self.assertEqual(len({t["id"] for t in sells.values()}), 3)
        self.assertIn("split by amountusd", err)


class TestUtcToLocal(unittest.TestCase):
    def test_new_year_utc_row_lands_in_previous_local_year(self):
        csv = _KT_H + ("T1,O1,SOL/CAD,2026-01-01 03:00:00.1,buy,limit,"
                       "200,2000,5,10,,,\n")
        td, K = _kraken_dir({"kr_trades.csv": csv})
        with td:
            txs, _ = _run(K().parse_file, Path(td.name) / "kr_trades.csv")
        self.assertEqual((txs[0]["date"], txs[0]["time"]),
                         ("2025-12-31", "22:00:00"))

    def test_summer_offset_and_env_override(self):
        from taxjson.lib.brokerages._crypto_common import utc_to_local
        self.assertEqual(utc_to_local(datetime(2025, 7, 1, 3, 0)),
                         datetime(2025, 6, 30, 23, 0))
        with mock.patch.dict(os.environ,
                             {"TAXJSON_LOCAL_TZ": "America/Vancouver"}):
            self.assertEqual(utc_to_local(datetime(2025, 7, 1, 3, 0)),
                             datetime(2025, 6, 30, 20, 0))

    def test_eastern_fallback_matches_tz_database(self):
        from taxjson.lib.brokerages import _crypto_common as cc
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo("America/Toronto")
        except Exception:                           # pragma: no cover
            self.skipTest("no tz database")
        d = datetime(2005, 1, 1, 0, 30)
        while d.year < 2028:
            want = cc.utc_to_local(d, "America/Toronto")
            got = d + timedelta(hours=cc._eastern_offset_hours(d))
            self.assertEqual(got, want, d)
            d += timedelta(hours=37)


class TestStrictMoney(unittest.TestCase):
    def test_forms(self):
        from taxjson.lib.brokerages._crypto_common import strict_money
        for raw, want in (("CA$4.00", 4.0), ("-CA$4.00", -4.0),
                          ("CA$-4.00", -4.0), ("$1,234.50", 1234.5),
                          ("US$3", 3.0), ("(12.00)", -12.0), ("", 0.0),
                          ("-$0.25", -0.25), ("1e-8", 1e-8)):
            self.assertAlmostEqual(strict_money(raw), want, msg=raw)
        for raw in ("CA$", "4,00", "1.234,56", "abc", "$1.2.3", "nan"):
            with self.assertRaises(ValueError, msg=raw):
                strict_money(raw)


# ------------------------------------------------------------------ M4
_CB_H = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
         "Price Currency,Price at Transaction,Subtotal,"
         "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n")


def _coinbase(rows):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cb.csv"
        p.write_text(_CB_H + rows)
        par = CoinbaseBrokerage()
        txs, err = _run(par.parse_file, p)
        return txs, err, par


class TestCoinbaseUsdcAsCash(unittest.TestCase):
    ROWS = (
        'a1,2025-06-01 15:00:00 UTC,Buy,USDC,1000,CAD,$1.37,$1370.00,'
        '$1377.00,$7.00,Bought 1000 USDC for 1377 CAD using CAD Wallet\n'
        'a2,2025-06-02 15:00:00 UTC,Advanced Trade Buy,ETH,0.4,CAD,'
        '"$3,400.00","$1,360.00","$1,362.00",$2.00,'
        'Bought 0.4 ETH for 994.2 USDC on ETH-USDC at 2480 USDC/ETH\n'
        'a3,2025-06-03 15:00:00 UTC,Advanced Trade Sell,ETH,-0.1,CAD,'
        '"$3,500.00",-$350.00,-$349.50,$0.50,'
        'Sold 0.1 ETH for 255.2 USDC on ETH-USDC at 2555 USDC/ETH\n')

    def test_usdc_never_enters_the_book(self):
        txs, err, par = _coinbase(self.ROWS)
        self.assertEqual({t["symbol"] for t in txs}, {"ETH"})
        self.assertIn("stablecoin conversion Buy USDC", err)
        eth = sorted(txs, key=lambda t: t["date"])
        self.assertAlmostEqual(eth[0]["net_amount"], 1362.0)
        self.assertAlmostEqual(eth[1]["net_amount"], 349.5)
        # Row accounting stays exact (lint).
        self.assertEqual(par._rows_seen,
                         par._rows_consumed + sum(par._skip_counts.values()))

    def test_usdc_staking_reward_is_income_only(self):
        rows = ('s1,2025-06-01 15:00:00 UTC,Staking Income,USDC,0.5,CAD,'
                '$1.37,$0.685,$0.685,$0.00,\n')
        txs, _, _ = _coinbase(rows)
        self.assertEqual([t["action"] for t in txs], ["DIVIDEND"])

    def test_convert_into_usdc_is_a_cash_sale(self):
        rows = ('c1,2025-06-01 15:00:00 UTC,Convert,ETH,-0.1,CAD,$3400,'
                '$340.00,$342.00,$2.00,Converted 0.1 ETH to 245.1 USDC\n')
        txs, _, _ = _coinbase(rows)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["symbol"], "ETH")
        self.assertAlmostEqual(txs[0]["quantity"], -0.1)
        self.assertAlmostEqual(txs[0]["net_amount"], 338.0)

    def test_ca_dollar_amounts_parse_and_garbage_raises(self):
        rows = ('b1,2025-06-02 15:00:00 UTC,Buy,SOL,2,CAD,CA$200.00,'
                'CA$400.00,CA$404.00,CA$4.00,\n')
        txs, _, _ = _coinbase(rows)
        self.assertAlmostEqual(txs[0]["net_amount"], 404.0)
        self.assertAlmostEqual(txs[0]["price"], 200.0)
        with self.assertRaises(ValueError) as cm:
            _coinbase(rows.replace("CA$404.00", "CA$ four hundred"))
        self.assertIn("unparseable total", str(cm.exception))

    def test_utc_timestamp_becomes_local_date(self):
        rows = ('s1,2026-01-01 02:30:00 UTC,Staking Income,SOL,0.01,CAD,'
                '$200,$2.00,$2.00,$0.00,\n')
        txs, _, _ = _coinbase(rows)
        self.assertEqual({t["date"] for t in txs}, {"2025-12-31"})


if __name__ == "__main__":
    unittest.main()
