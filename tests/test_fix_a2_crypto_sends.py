"""Re-audit 2 (crypto-sends): pairing, decisions, prices and the
stablecoin pool of lib/crypto_sends.py and the `taxjson run` /
`taxjson crypto-sends` wiring.

Synthetic exports only; a hand-written work/to_base.csv and a
fill-crypto cache under a temporary HOME with TAXJSON_OFFLINE set, so
nothing here touches the network.
"""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from taxjson.lib import crypto_sends as cs
from tax_rules import rule

from test_fix_sends import _cad_usd_rates_file, _cli, _rates_file

CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")
KR_HEADER = "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"


def rule_text(rid):
    from taxjson.lib.tax_logic import rule_sections, rule_country
    c = rule_country(rid)
    return next(r.text for _t, rs in rule_sections(c, {}) for r in rs
                if r.id == rid)


def _s(exchange="coinbase", account="c", date="2025-07-10",
       time="06:00:00", symbol="BTC", quantity=-1.0, **kw):
    r = {"exchange": exchange, "account": account, "date": date,
         "time": time, "symbol": symbol, "quantity": quantity,
         "price": 0.0, "currency": "", "fee": 0.0, "kind": "Send",
         "ref": ""}
    r.update(kw)
    return r


def _arr(time, quantity, exchange="kraken", **kw):
    return _s(exchange=exchange, time=time, quantity=quantity,
              kind="deposit", **kw)


def _proj(td, accounts, *, country="canada"):
    """A project with `accounts` {name: {file name: text}}; every
    account is a taxable crypto account (in this order)."""
    root = Path(td) / "proj"
    base = "USD" if country == "usa" else "CAD"
    src = "CAD" if country == "usa" else "USD"
    toml = (f'[settings]\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n')
    for name, files in accounts.items():
        toml += f'[accounts.{name}]\ntype = "taxable"\ncrypto = true\n'
        d = root / "inputs" / name
        d.mkdir(parents=True)
        for fn, text in files.items():
            (d / fn).write_text(text)
    (root / "taxjson.toml").write_text(toml)
    (root / "work").mkdir()
    if country == "usa":
        _cad_usd_rates_file(root / "work" / "to_base.csv")
    else:
        _rates_file(root / "work" / "to_base.csv")
    home = Path(td) / "home"
    home.mkdir()
    (home / ".crypto_price_cache.json").write_text(json.dumps(
        {"SOL-2025-06-01": 150.0}))
    return root, home


def _sales(root, acct, symbol):
    base = json.loads((root / "work" / f"{acct}_base.json").read_text())
    return sorted((round(float(t["quantity"]), 8),
                   round(float(t["net_amount"]), 2))
                  for t in base["transactions"]
                  if t["action"] == "BUYSELL" and t["symbol"] == symbol
                  and float(t["quantity"]) < 0)


# Coinbase (UTC; Toronto is UTC-4 in July): 2 BTC bought, 1 BTC sent
# 2025-07-10 10:00 UTC = 06:00 local.
CB_SEND = (CB_HEADER
           + "c1,2025-07-01 12:00:00 UTC,Buy,BTC,2,CAD,100000,200000,"
             "200000,0,Bought 2 BTC\n"
           + "c2,2025-07-10 10:00:00 UTC,Send,BTC,1,CAD,110000,110000,"
             "110000,0,Sent 1 BTC\n")
SEND_ID = "cb-20250710T060000-BTC-1"
KR_EMPTY = (KR_HEADER
            + "LA1,RA1,2025-06-01 12:00:00,earn,reward,currency,SOL,spot,"
              "1,0,1\n")


def _kr_with_deposit(qty, when="2025-07-12 10:00:00"):
    return KR_EMPTY + (f"LD1,RD1,{when},deposit,,currency,XXBT,spot,{qty},"
                       f"0,{qty}\n")


class TestPairingIsAnAssignment(unittest.TestCase):
    """CA-CRYPTO-06: the pairing pairs as many sends as it can, then
    loses the fewest coins, then the closest in time (A2-0078,
    A2-0239), and tries splits (A2-1006)."""

    @rule("CA-CRYPTO-06")
    def test_send_does_not_take_another_sends_arrival(self):
        # A2-0078: 1.0 at 10:00 and 0.92 at 10:01; deposits 0.919 at
        # 10:30 and 0.999 at 10:31. Greedy gave 1.0 <- 0.919 (a 0.081
        # BTC "fee") and left the 0.92 send unmatched.
        rows = [_s(time="10:00:00", quantity=-1.0),
                _s(time="10:01:00", quantity=-0.92),
                _arr("10:30:00", 0.919), _arr("10:31:00", 0.999)]
        un, pairs = cs.match_transfers(rows)
        self.assertEqual(un, [])
        self.assertEqual(sorted((-o["quantity"], i["quantity"])
                                for o, i in pairs),
                         [(0.92, 0.919), (1.0, 0.999)])
        fees = cs.network_fees(pairs, "c", cs.Rates({}, "CAD"), None,
                               "canada")
        self.assertAlmostEqual(sum(f["quantity"] for f in fees), 0.002)

    @rule("CA-CRYPTO-06")
    def test_a_full_matching_is_found(self):
        # A2-0239: 1.00 at 10:00, 0.95 at 11:00; arrivals 0.95 at 11:05
        # and 0.99 the next day.
        rows = [_s(time="10:00:00", quantity=-1.0),
                _s(time="11:00:00", quantity=-0.95),
                _arr("11:05:00", 0.95),
                _arr("10:00:00", 0.99, date="2025-07-11")]
        un, pairs = cs.match_transfers(rows)
        self.assertEqual(un, [])
        self.assertEqual(sorted((-o["quantity"], i["quantity"])
                                for o, i in pairs),
                         [(0.95, 0.95), (1.0, 0.99)])

    @rule("CA-CRYPTO-06")
    def test_split_arrival_and_split_send(self):
        # A2-1006: one send landing as two deposits ...
        rows = [_s(quantity=-1.0), _arr("06:20:00", 0.5),
                _arr("06:25:00", 0.4995)]
        un, pairs = cs.match_transfers(rows)
        self.assertEqual(un, [])
        self.assertAlmostEqual(pairs[0][1]["quantity"], 0.9995)
        self.assertEqual(len(cs.arrival_rows(pairs[0][1])), 2)
        # ... and two sends landing as one deposit (each its share).
        rows = [_s(time="06:00:00", quantity=-0.6),
                _s(time="06:01:00", quantity=-0.4),
                _arr("06:30:00", 0.999)]
        un, pairs = cs.match_transfers(rows)
        self.assertEqual(un, [])
        self.assertEqual(len(pairs), 2)
        self.assertAlmostEqual(sum(i["quantity"] for _o, i in pairs), 0.999)

    @rule("CA-CRYPTO-06")
    def test_hybrid_earn_is_never_paired(self):
        # A2-0573: the Hybrid Earn row took the real withdrawal's arrival
        # and booked a fake 0.05 ETH network fee.
        rows = [_s(exchange="kraken", time="06:00:00", symbol="ETH",
                   quantity=-1.0, kind="hybridearnwithdrawal"),
                _s(exchange="kraken", time="06:05:00", symbol="ETH",
                   quantity=-1.0, kind="withdrawal", fee=0.001),
                _arr("06:20:00", 0.95, exchange="coinbase", symbol="ETH",
                     date="2025-07-11"),
                _arr("06:20:00", 1.0, exchange="coinbase", symbol="ETH")]
        un, pairs = cs.match_transfers(rows)
        self.assertEqual([u["kind"] for u in un], ["hybridearnwithdrawal"])
        self.assertEqual([(o["kind"], i["quantity"]) for o, i in pairs],
                         [("withdrawal", 1.0)])
        self.assertEqual(cs.network_fees(pairs, "c", cs.Rates({}, "CAD"),
                                         None, "canada"), [])

    @rule("CA-CRYPTO-06")
    def test_unpaired_send_stays_unmatched(self):
        rows = [_s(quantity=-1.0), _arr("07:00:00", 0.99)]
        cs.assign_send_ids(rows)
        un, pairs = cs.match_transfers(rows, {("c", rows[0]["sid"])})
        self.assertEqual((len(un), pairs), (1, []))


class TestRuleTextMatchesCode(unittest.TestCase):
    """A2-0594 / A2-1007: tax-logic states what the pairing and the
    Kraken coin-fee booking do."""

    def _window(self):
        send = _s(quantity=-1.0, exchange="coinbase", account="a",
                  time="12:00:00")
        out = {}
        for label, kw in (("9 min before", dict(time="11:51:00")),
                          ("11 min before", dict(time="11:49:00")),
                          ("same exchange, other account",
                           dict(exchange="coinbase", account="b",
                                time="12:05:00"))):
            arr = _s(**{**dict(exchange="kraken", account="a",
                               quantity=1.0), **kw})
            out[label] = bool(cs.match_transfers([dict(send), arr])[1])
        return out

    @rule("CA-CRYPTO-06")
    def test_canada_pairing_window(self):
        self.assertEqual(self._window(), {
            "9 min before": True, "11 min before": False,
            "same exchange, other account": True})
        t = rule_text("CA-CRYPTO-06")
        self.assertIn("10 minutes before", t)
        self.assertIn("same exchange in another crypto account", t)

    @rule("US-CRYPTO-05")
    def test_us_pairing_window(self):
        self.assertEqual(self._window()["9 min before"], True)
        self.assertIn("10 minutes before", rule_text("US-CRYPTO-05"))

    def _fee_rows(self, cash):
        import io
        from contextlib import redirect_stderr
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        text = KR_HEADER + (
            "L1,R1,2025-06-02 12:00:00,deposit,,currency,SOL,spot,1,0.01,1\n"
            "L2,R2,2025-06-03 12:00:00,transfer,transferpeertopeer,currency,"
            "SOL,spot,-0.5,0.01,0.5\n"
            "L3,R3,2025-06-04 12:00:00,hybridearnwithdrawal,,currency,SOL,"
            "spot,-0.2,0.01,0.3\n")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers.csv"
            p.write_text(text)
            k = KrakenBrokerage()
            k.stablecoins_as_cash = cash
            with redirect_stderr(io.StringIO()):
                txs = k.parse_file(p)
        return [t for t in txs if t["action"] == "BUYSELL"
                and t["symbol"] == "SOL" and t["quantity"] < 0]

    @rule("CA-CRYPTO-03")
    def test_canada_coin_fee_on_any_move_is_a_sale(self):
        self.assertEqual(len(self._fee_rows(True)), 3)
        self.assertIn("Hybrid Earn withdrawal", rule_text("CA-CRYPTO-03"))

    @rule("US-CRYPTO-03")
    def test_us_coin_fee_on_any_move_is_a_sale(self):
        self.assertEqual(len(self._fee_rows(False)), 3)
        self.assertIn("a deposit", rule_text("US-CRYPTO-03"))


class TestSendIds(unittest.TestCase):
    def test_same_second_sends_get_their_own_ids(self):
        # A2-0586.
        rows = [_s(exchange="kraken", quantity=-1.0, symbol="SOL"),
                _s(exchange="kraken", quantity=-1.0, symbol="SOL")]
        cs.assign_send_ids(rows)
        self.assertEqual([r["sid"] for r in rows],
                         ["kr-20250710T060000-SOL-1",
                          "kr-20250710T060000-SOL-1-2"])


class TestPrices(unittest.TestCase):
    def _load(self, rec):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sends.json"
            p.write_text(json.dumps({"sends": {"k": rec}}))
            return cs.load_decisions(p)

    @rule("CA-CRYPTO-07")
    def test_hand_edited_price_is_checked(self):
        # A2-0241 / A2-0587.
        for bad in ("NaN", "Infinity", "-5", "0", '"12abc"', "1e-300",
                    "true"):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "sends.json"
                p.write_text('{"sends": {"k": {"decision": "gift", '
                             f'"price": {bad}}}}}}}')
                with self.assertRaises(ValueError, msg=bad) as cm:
                    cs.load_decisions(p)
                self.assertIn("'k' has price", str(cm.exception))
        self.assertEqual(self._load({"decision": "gift", "price": 2.5})
                         ["sends"]["k"]["price"], 2.5)

    def test_record_refuses_a_bad_price_and_writes_strict_json(self):
        # A2-0571 / A2-0588 / A2-1009.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sends.json"
            for bad in (float("inf"), float("nan"), 1e-300, -1.0):
                with self.assertRaises(ValueError):
                    cs.record_decision(p, "k", "gift", price=bad)
            cs.record_decision(p, "k", "gift", price=0.5)
            json.loads(p.read_text(),
                       parse_constant=lambda c: self.fail(c))

    def test_cli_price_must_be_finite(self):
        # --price inf / 1e400 were accepted (argparse float).
        with tempfile.TemporaryDirectory() as td:
            for bad in ("inf", "1e400", "nan", "-1", "0"):
                r = _cli(Path(td), Path(td), "crypto-sends", "a", "--set",
                         "x=gift", "--price", bad)
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn("--price", r.stderr, bad)

    def test_redeciding_drops_a_hand_price(self):
        # A2-0572: gift --price 1 (typo), then self, then payment.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sends.json"
            cs.record_decision(p, "k", "gift", price=1.0)
            cs.record_decision(p, "k", "self")
            cs.record_decision(p, "k", "payment")
            self.assertNotIn("price",
                             json.loads(p.read_text())["sends"]["k"])

    @rule("CA-CRYPTO-06")
    def test_fee_entry_takes_a_hand_price(self):
        # A2-0240: a `-fee` id takes `fee` + a price.
        o, i = _s(quantity=-1.0, symbol="ZZQ"), _arr("06:30:00", 0.9,
                                                    symbol="ZZQ")
        cs.assign_send_ids([o])
        fid = o["sid"] + "-fee"
        fees = cs.network_fees([(o, i)], "c", cs.Rates({}, "CAD"), None,
                               "canada")
        self.assertIsNone(fees[0]["tt"])
        fees = cs.network_fees([(o, i)], "c", cs.Rates({}, "CAD"), None,
                               "canada", {fid: {"decision": "fee",
                                                "price": 2.0}})
        self.assertEqual(fees[0]["tt"],
                         "BUYSELL 2025-07-10 06:00:00 ZZQ -0.1 CAD 2 "
                         "0.20 0")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sends.json"
            cs.record_decision(p, fid, "fee", price=2.0)
            with self.assertRaises(ValueError):
                cs.record_decision(p, "cb-x", "fee", price=2.0)


class TestUnpricedDoesNotBlock(unittest.TestCase):
    @rule("CA-CRYPTO-07")
    def test_priced_entries_are_written(self):
        # A2-0240: an unpriceable network fee held back the owner's
        # priced payment and left crypto_sends.tt unchanged.
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "a").mkdir(parents=True)
            tt = root / "inputs" / "a" / cs.TT_NAME
            pay = {"id": "cb-20250710T060000-ZZQ-0.5", "decision": "payment",
                   "stable": False, "exchange": "coinbase", "kind": "Send",
                   "date": "2025-07-10", "time": "06:00:00", "symbol": "ZZQ",
                   "quantity": 0.5, "fee_booked": 0, "note": "",
                   "fair_value": {"price": 2.0, "currency": "CAD",
                                  "source": "entered by hand (--price)"},
                   "tt": "BUYSELL 2025-07-10 06:00:00 ZZQ -0.5 CAD 2 1.00 0"}
            fee = {"id": "cb-20250710T070000-ZZQ-1-fee", "network_fee": True,
                   "tt": None, "fair_value": None}
            report = {"base_currency": "CAD", "country": "canada",
                      "accounts": {"a": {"sends": [pay],
                                         "network_fees": [fee],
                                         "tt_file": str(tt)}}}
            with self.assertRaises(cs.UnpricedSends) as cm:
                R._crypto_sends_tt(root, "a", report)
            self.assertIn("ZZQ-1-fee", str(cm.exception))
            self.assertIn("--set cb-20250710T070000-ZZQ-1-fee=fee",
                          str(cm.exception))
            self.assertIn(pay["tt"], tt.read_text())


class TestPaymentIsASale(unittest.TestCase):
    """CA-CRYPTO-07 / US-SEND-01: paying with crypto disposes of the
    coins in both countries — a `payment` send is written to
    crypto_sends.tt as a sale; a `self` move is not."""

    def _doc(self, decision):
        return {"sends": [{"id": "cb-20250710T060000-ZZQ-0.5",
                           "decision": decision, "stable": False,
                           "tt": "BUYSELL 2025-07-10 06:00:00 ZZQ -0.5 "
                                 "USD 2 1.00 0"}],
                "network_fees": []}

    @rule("CA-CRYPTO-07")
    @rule("US-SEND-01")
    def test_a_payment_is_written_as_a_sale(self):
        doc = self._doc("payment")
        write, unpriced = cs.tt_entries(doc)
        self.assertEqual([e["id"] for e in write],
                         ["cb-20250710T060000-ZZQ-0.5"])
        self.assertEqual(unpriced, [])
        self.assertEqual(cs.tt_want_ids(doc), {"cb-20250710T060000-ZZQ-0.5"})
        self.assertEqual(len(cs.disposing_entries(doc)), 1)
        self.assertEqual(cs.tt_entries(self._doc("self")), ([], []))


class TestRatesAndPriceSource(unittest.TestCase):
    def test_bad_rate_is_refused_not_skipped(self):
        # A2-0999: a NaN rate priced the send at the prior day's rate.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "to_base.csv"
            p.write_text("2024-05-13 12:00:00 USD CAD 1.3672 boc\n"
                         "2024-05-14 12:00:00 USD CAD NaN boc\n")
            with self.assertRaises(ValueError) as cm:
                cs.load_rates(p)
            self.assertIn("line 2", str(cm.exception))
            # The first row of a date wins (convert-currency's rule).
            p.write_text("2024-05-13 12:00:00 USD CAD 1.30 boc\n"
                         "2024-05-13 15:00:00 USD CAD 1.40 yahoo\n")
            self.assertEqual(cs.load_rates(p)["USD"]["2024-05-13"],
                             (1.30, "boc"))

    def _lookup(self, root, home, sym, day, price):
        from taxjson.bin import fill_crypto_prices as F
        with mock.patch.dict(os.environ, {"HOME": str(home)}), \
                mock.patch.object(F, "CACHE_FILE",
                                  str(home / ".crypto_price_cache.json")), \
                mock.patch.object(F, "get_crypto_price",
                                  side_effect=lambda s, d:
                                  price if s == sym else None) as g, \
                mock.patch("taxjson.lib.offline.offline_enabled",
                           return_value=False):
            got = cs.yahoo_usd_price(root)(sym, day)
            return got, g

    def test_today_is_not_cached(self):
        # A2-0570: the open candle went into the shared cache.
        with tempfile.TemporaryDirectory() as tmp:
            root, home = Path(tmp) / "p", Path(tmp) / "h"
            root.mkdir(), home.mkdir()
            today = datetime.now(timezone.utc).date().isoformat()
            (got, _y), _g = self._lookup(root, home, "SOL", today, 100.0)
            self.assertEqual(got, 100.0)
            cache = home / ".crypto_price_cache.json"
            self.assertNotIn(f"SOL-{today}", json.loads(cache.read_text())
                             if cache.exists() else {})
            (got, _y), _g = self._lookup(root, home, "SOL", "2024-01-02",
                                         90.0)
            self.assertEqual(json.loads(cache.read_text())
                             ["SOL-2024-01-02"], 90.0)

    def test_work_map_applies_as_in_fill_crypto(self):
        # A2-0242: fill-crypto reads the project root, then work/.
        from taxjson.bin import fill_crypto_prices as F
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "crypto_ticker.map").write_text("FOO FOO123\n")
            (root / "home").mkdir()
            (root / "home" / ".crypto_price_cache.json").write_text(
                json.dumps({"FOO123-2024-06-03": 150.0,
                            "FOO-2024-06-03": 2.0}))
            with mock.patch.object(F, "CACHE_FILE", str(
                    root / "home" / ".crypto_price_cache.json")):
                self.assertEqual(cs.yahoo_usd_price(root)("FOO",
                                                          "2024-06-03"),
                                 (150.0, "FOO123"))


class _UTC(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "UTC"})
        p.start()
        self.addCleanup(p.stop)


class TestStablecoinPool(_UTC):
    """CA-CRYPTO-08: the pool the stablecoin gift's FX gain uses."""

    def _flows(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cb.csv"
            p.write_text(CB_HEADER + body)
            return [round(f["units"], 6) for f in cs.coinbase_pool_flows(p)]

    @rule("CA-CRYPTO-08")
    def test_convert_moves_the_pool_whichever_leg_asset_names(self):
        # A2-0243 (spend leg named by the TO coin) and A2-0574 (receive
        # leg named by the stablecoin).
        for asset in ("USDC", "ETH"):
            self.assertEqual(self._flows(
                f"c1,2025-05-06 12:00:00 UTC,Convert,{asset},1,USD,1,1000,"
                f"1000,0,Converted 1000 USDC to 0.4 ETH\n"), [-1000.0], asset)
        for asset in ("USDC", "BTC"):
            self.assertEqual(self._flows(
                f"c1,2025-05-06 12:00:00 UTC,Convert,{asset},1,USD,1,600,"
                f"600,0,Converted 0.01 BTC to 600 USDC\n"), [600.0], asset)

    def test_notes_numbers_are_strict(self):
        # A2-1008: '0,125' is a decimal comma, never 125.
        with self.assertRaises(ValueError):
            self._flows("c1,2025-05-06 12:00:00 UTC,Convert,USDC,1,USD,1,1,"
                        "1,0,\"Converted 0,125 USDC to 0.00005 ETH\"\n")
        self.assertEqual(self._flows(
            "c1,2025-05-06 12:00:00 UTC,Convert,ETH,1,USD,1,1,1,0,"
            "\"Converted 1 ETH to 1,234.56 USDC\"\n"), [1234.56])

    @rule("CA-CRYPTO-08")
    def test_deposit_fees_leave_the_pool(self):
        # A2-0592: an unpaired deposit's fee; A2-1011: a paired one's.
        # A rate within the 5-day lookback of the deposit (A2-0414).
        rates = cs.Rates({"USD": {"2025-01-01": (1.40, "boc"),
                                  "2025-01-31": (1.40, "boc")}}, "CAD")
        dep = _s(exchange="kraken", date="2025-02-01", symbol="USDC",
                 quantity=500.0, fee=5.0)
        res = cs.usd_pool([], [dep], [], {}, rates)
        self.assertEqual(res["units_now"], 495.0)
        buy = {"dt": datetime(2025, 1, 2), "units": 100.0, "cad": -140.0}
        out = _s(date="2025-02-01", symbol="USDC", quantity=-50.0)
        inn = _s(exchange="kraken", date="2025-02-01", time="06:10:00",
                 symbol="USDC", quantity=50.0, fee=1.0)
        res = cs.usd_pool([buy], [out, inn], [(out, inn)], {}, rates)
        self.assertEqual(res["units_now"], 99.0)


class TestStrictNumberSites(_UTC):
    """A2-1021 (crypto-sends sites): each strict number site refuses a
    decimal comma instead of reading it 1000x too large."""

    def test_kraken_pool_amount_and_fee(self):
        for amount, fee in (('"1,5"', "0"), ("1", '"0,5"')):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "kr.csv"
                p.write_text(KR_HEADER + f"L1,R1,2025-01-15 15:00:00,earn,"
                             f"reward,currency,USDC,spot,{amount},{fee},1\n")
                with self.assertRaises(ValueError, msg=(amount, fee)):
                    cs.kraken_pool_flows(p)

    def test_coinbase_pool_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cb.csv"
            p.write_text(CB_HEADER + 'c1,2025-05-01 12:00:00 UTC,Buy,USDC,'
                         '"20,5",CAD,1.38,27.60,27.60,0,Bought USDC\n')
            with self.assertRaises(ValueError):
                cs.coinbase_pool_flows(p)


class TestDuplicateLines(unittest.TestCase):
    def _hits(self, lines, qty=1000.0, sym="SOL"):
        with tempfile.TemporaryDirectory() as tmp:
            acct = Path(tmp) / "inputs" / "a"
            acct.mkdir(parents=True)
            (acct / "hand.tt").write_text("\n".join(lines) + "\n")
            e = {"id": "kr-x", "date": "2026-05-04", "time": "22:00:00",
                 "symbol": sym, "quantity": qty}
            return cs.duplicate_lines(acct, [e])

    def test_variants_are_flagged(self):
        # A2-0575: the UTC date, the fee folded in, a thousands comma,
        # a sale split over two lines.
        for lines in (["BUYSELL 2026-05-05 02:00:00 SOL -1000 CAD 1 1 0"],
                      ["BUYSELL 2026-05-04 22:00:00 SOL -1000.01 CAD 1 1 0"],
                      ["BUYSELL 2026-05-04 22:00:00 SOL -1,000 CAD 1 1 0"],
                      ["BUYSELL 2026-05-04 22:00:00 SOL -600 CAD 1 1 0",
                       "BUYSELL 2026-05-04 22:00:00 SOL -400 CAD 1 1 0"]):
            self.assertTrue(self._hits(lines), lines)
        self.assertFalse(self._hits(
            ["BUYSELL 2026-05-07 22:00:00 SOL -1000 CAD 1 1 0"]))
        self.assertFalse(self._hits(
            ["BUYSELL 2026-05-04 22:00:00 SOL -500 CAD 1 1 0"]))

    def test_rounded_quantity_is_flagged(self):
        # A2-1005.
        for q in ("-0.12345679", "-0.1235"):
            self.assertTrue(self._hits(
                [f"BUYSELL 2026-05-04 22:00:00 TAO {q} CAD 1 1 0"],
                qty=0.123456789, sym="TAO"), q)


class TestDecisionVersusPairing(unittest.TestCase):
    """A2-0004: a saved gift that later pairs with an arrival is said
    loudly, stops --strict, and can be unpaired."""

    @rule("CA-CRYPTO-06")
    def test_overridden_gift_is_reported_and_can_be_unpaired(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"crypto": {"cb_2025.csv": CB_SEND,
                                               "kr_ledgers.csv": KR_EMPTY}})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{SEND_ID}=gift", "--price", "110000")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(_sales(root, "crypto", "BTC"),
                             [(-1.0, 110000.0)])
            # An unrelated 0.93 BTC deposit two days later.
            kr = root / "inputs" / "crypto" / "kr_ledgers.csv"
            kr.write_text(_kr_with_deposit(0.93))
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(f"saves {SEND_ID}", r.stderr)
            self.assertIn("--unpair", r.stderr)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("is NOT booked", r.stderr)
            from taxjson.lib import checklist as cl
            from taxjson.lib.tomlcompat import tomllib
            ctx = cl.Ctx(root=root, cfg=tomllib.loads(
                (root / "taxjson.toml").read_text()), year=2025,
                today=datetime.now().date(),
                run_sub=lambda argv, timeout=900: (0, "", ""))
            res = cl.DETECTORS["crypto-sends"](ctx)
            self.assertEqual(res.status, "attention")
            self.assertIn(SEND_ID, res.detail)
            # Unpairing needs the flag; then the gift books again.
            r = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{SEND_ID}=gift")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--unpair", r.stderr)
            r = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{SEND_ID}=gift", "--unpair")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_sales(root, "crypto", "BTC"),
                             [(-1.0, 110000.0)])

    def test_confirming_the_pairing_with_self_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"crypto": {
                "cb_2025.csv": CB_SEND,
                "kr_ledgers.csv": _kr_with_deposit(0.99)}})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{SEND_ID}=self")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])


class TestMultiAccountFreshness(unittest.TestCase):
    """A2-0077 / A2-1026 / A2-1010: the sends stage pairs against the
    exports of THIS run."""

    @rule("CA-CRYPTO-06")
    def test_first_run_after_a_new_export_pairs_against_it(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"acoin": {"cb_2025.csv": CB_SEND},
                                    "bkr": {"kr_ledgers.csv": KR_EMPTY}})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("not yet classified", r.stderr)
            kr = root / "inputs" / "bkr" / "kr_ledgers.csv"
            kr.write_text(_kr_with_deposit(0.99))
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("not yet classified", r.stderr)
            # The 0.01 BTC network fee is booked in THIS run.
            self.assertEqual(_sales(root, "acoin", "BTC"), [(-0.01, 1100.0)])

    def test_standalone_command_refuses_on_stale_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"acoin": {"cb_2025.csv": CB_SEND},
                                    "bkr": {"kr_ledgers.csv": KR_EMPTY}})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            kr = root / "inputs" / "bkr" / "kr_ledgers.csv"
            kr.write_text(_kr_with_deposit(0.99))
            future = time.time() + 60
            os.utime(kr, (future, future))
            r = _cli(root, home, "crypto-sends", "acoin", "--set",
                     f"{SEND_ID}=gift")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not current", r.stderr)
            r = _cli(root, home, "crypto-sends")
            self.assertIn("not current", r.stderr)

    def test_removed_export_books_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            kr = (KR_EMPTY + "LW1,RW1,2025-06-02 12:00:00,withdrawal,,"
                  "currency,SOL,spot,-1,0,0\n")
            root, home = _proj(td, {"crypto": {"kr_ledgers.csv": kr,
                                               "cb_2025.csv": CB_SEND}})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            sid = "kr-20250602T080000-SOL-1"
            r = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{sid}=payment", "--price", "200")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(_sales(root, "crypto", "SOL"), [(-1.0, 200.0)])
            (root / "inputs" / "crypto" / "kr_ledgers.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_sales(root, "crypto", "SOL"), [])


# US: two Coinbase accounts; 1 BTC bought in `bkr`, sent to `acoin`,
# sold there.
US_BKR = (CB_HEADER
          + "u1,2025-05-01 12:00:00 UTC,Buy,BTC,1,USD,100000,100000,100000,"
            "0,Bought 1 BTC\n"
          + "u2,2025-07-01 12:00:00 UTC,Send,BTC,1,USD,105000,105000,"
            "105000,0,Sent 1 BTC\n")
US_ACOIN = (CB_HEADER
            + "v1,2025-07-01 13:00:00 UTC,Receive,BTC,1,USD,105000,105000,"
              "105000,0,Received 1 BTC\n"
            + "v2,2025-07-10 12:00:00 UTC,Sell,BTC,1,USD,110000,110000,"
              "110000,0,Sold 1 BTC\n")


class TestUsMoveBetweenAccounts(unittest.TestCase):
    @rule("US-CRYPTO-05")
    def test_strict_stops_and_a_plain_run_warns(self):
        # A2-0003: run --strict was rc 0 with a 0 gain.
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"bkr": {"cb_bkr.csv": US_BKR},
                                    "acoin": {"cb_acoin.csv": US_ACOIN}},
                               country="usa")
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot carry the moved coins' basis", r.stderr)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("ONE crypto account", r.stderr)


if __name__ == "__main__":
    unittest.main()
