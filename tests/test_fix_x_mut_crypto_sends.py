"""Mutation pins for lib/crypto_sends.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round: send/arrival pairing (CA-CRYPTO-06 /
US-CRYPTO-05), the decisions manifest, rates and fair values
(CA-CRYPTO-07), the US-dollar/stablecoin pool and its FX gain
(CA-CRYPTO-08: the Kraken and Coinbase flow readers had no unit test at
all), network fees, the duplicate-line check and the prompt. Synthetic
data only; no network (the price source is stubbed).
"""
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from taxjson.lib import crypto_sends as cs
from tax_rules import rule


def _s(exchange="coinbase", account="c", date="2025-08-24",
       time="09:41:52", symbol="SOL", quantity=-1.0, **kw):
    r = {"exchange": exchange, "account": account, "date": date,
         "time": time, "symbol": symbol, "quantity": quantity,
         "price": 0.0, "currency": "", "fee": 0.0, "kind": "Send",
         "ref": ""}
    r.update(kw)
    return r


def _daily(table, until="2025-12-31"):
    """A sparse {cur: {date: (rate, src)}} forward-filled to a row per
    day, as the real rates file is (its 7-day carry and the stage's
    5-day lookback, A2-0414): a sparse fixture row is the day's rate
    until the next one."""
    from datetime import date, timedelta
    out = {}
    for cur, days in table.items():
        keys = sorted(days)
        filled = {}
        for i, k in enumerate(keys):
            d = date.fromisoformat(k)
            end = (date.fromisoformat(keys[i + 1]) if i + 1 < len(keys)
                   else date.fromisoformat(until) + timedelta(days=1))
            while d < end:
                filled[d.isoformat()] = days[k]
                d += timedelta(days=1)
        out[cur] = filled
    return out


def _rates(table=None, base="CAD"):
    return cs.Rates(_daily(table if table is not None else
                           {"USD": {"2025-01-01": (1.35, "boc"),
                                    "2025-03-01": (1.45, "boc")}}), base)


class TestFormatting(unittest.TestCase):
    def test_mask_ref_strips_the_xfer_suffix_first(self):
        # m1230: the suffix is cut from the END.
        self.assertEqual(cs.mask_ref("x-xfer"), "x***")
        self.assertEqual(cs.mask_ref("-xfer"), "")
        self.assertEqual(cs.mask_ref("LG1GGG-xfer"), "LG***")

    def test_fmt_price_edge_values(self):
        # m1012 (0 and non-finite print "0"), m1046 (no decimals for a
        # 7-digit price), m1435 (negative prices format by magnitude).
        self.assertEqual(cs.fmt_price(0.0), "0")
        self.assertEqual(cs.fmt_price(float("inf")), "0")
        self.assertEqual(cs.fmt_price(float("nan")), "0")
        self.assertEqual(cs.fmt_price(1234567.89), "1234568")
        self.assertEqual(cs.fmt_price(-123.4567891), "-123.457")

    def test_send_id_of_an_unknown_exchange(self):
        # m1333: the first two letters of the exchange, "xx" if none.
        self.assertEqual(cs.send_id("binance", "2025-01-02", "03:04:05",
                                    "btc", -0.5),
                         "bi-20250102T030405-BTC-0.5")
        self.assertTrue(cs.send_id("", "2025-01-02", "03:04:05", "BTC",
                                   1).startswith("xx-"))

    def test_row_datetime_uses_its_time(self):
        # m1232.
        self.assertEqual(cs._dt({"date": "2025-01-02", "time": "10:11:12"}),
                         datetime(2025, 1, 2, 10, 11, 12))
        self.assertEqual(cs._dt({"date": "2025-01-02", "time": ""}),
                         datetime(2025, 1, 2))


class TestLoadTransferRows(unittest.TestCase):
    def _load(self, txs, meta=None, name="a_kraken_transfers.json",
              raw=None):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / name
            p.write_text(raw if raw is not None else json.dumps({
                "metadata": meta if meta is not None else
                {"kind": "transfer_sidecar", "brokerage": "kraken"},
                "transactions": txs}))
            return cs.load_transfer_rows(Path(tmp), ["a"])

    @rule("CA-CRYPTO-06")
    def test_rows_and_fees(self):
        rows = self._load([
            # fee in the coin itself, from the description
            {"date": "2025-01-02", "time": "01:02:03", "symbol": "btc",
             "quantity": -0.5, "price": -40000,
             "description": "withdrawal (fee 0.0002 BTC)", "id": "AB12"},
            # fee in another currency: not part of the coins sent (m1407)
            {"date": "2025-01-03", "symbol": "ETH", "quantity": -1,
             "description": "withdrawal (fee 0.5 USD)"},
            # legacy `fee` field, signed (m1410)
            {"date": "2025-01-04", "symbol": "SOL", "quantity": 2,
             "fee": -0.01},
            # no quantity (m1405) / no date (m1234): skipped
            {"date": "2025-01-05", "symbol": "SOL"},
            {"symbol": "SOL", "quantity": 1}])
        self.assertEqual(len(rows), 3)
        btc, eth, sol = rows
        self.assertEqual((btc["exchange"], btc["symbol"], btc["fee"],
                          btc["price"], btc["kind"], btc["ref"],
                          btc["time"]),
                         ("kraken", "BTC", 0.0002, 40000.0, "withdrawal",
                          "AB***", "01:02:03"))
        self.assertEqual(eth["fee"], 0.0)
        self.assertEqual((sol["fee"], sol["time"], sol["kind"]),
                         (0.01, "00:00:00", "transfer"))

    def test_brokerage_defaults_to_the_file_name(self):
        # m1233.
        rows = self._load([{"date": "2025-01-02", "symbol": "BTC",
                            "quantity": -1}],
                          meta={"kind": "transfer_sidecar"},
                          name="a_coinbase_transfers.json")
        self.assertEqual(rows[0]["exchange"], "coinbase")

    def test_other_sidecar_kinds_are_ignored(self):
        # m1127.
        self.assertEqual(self._load([{"date": "2025-01-02", "symbol": "X",
                                      "quantity": 1}],
                                    meta={"kind": "something_else"}), [])

    def test_unreadable_sidecar_is_a_value_error(self):
        # m1124.
        with self.assertRaises(ValueError) as cm:
            self._load(None, raw="{broken")
        self.assertIn("could not read a_kraken_transfers.json",
                      str(cm.exception))


class TestMatchTransfers(unittest.TestCase):
    """CA-CRYPTO-06 / US-CRYPTO-05: an arrival on another exchange
    within 10 minutes before to 3 days after, 90% to 100% of the coins."""

    def _arrive(self, **kw):
        base = dict(exchange="kraken", quantity=1.0)
        base.update(kw)
        return _s(**base)

    def _pairs(self, rows):
        un, pairs = cs.match_transfers(rows)
        return un, [(o["quantity"], i["quantity"], i["exchange"],
                     i["time"], i["date"]) for o, i in pairs]

    @rule("CA-CRYPTO-06")
    @rule("US-CRYPTO-05")
    def test_window_edges(self):
        # m1242/m1243 (edges inclusive), m1040/m1041 (10 min, 3 days).
        send = _s(quantity=-1.0, date="2025-08-24", time="12:00:00")
        for when, ok in ((("2025-08-24", "11:50:00"), True),
                         (("2025-08-24", "11:49:00"), False),
                         (("2025-08-27", "12:00:00"), True),
                         (("2025-08-27", "12:01:00"), False)):
            un, pairs = self._pairs([dict(send), self._arrive(
                date=when[0], time=when[1])])
            self.assertEqual(bool(pairs), ok, when)
            self.assertEqual(len(un), 0 if ok else 1, when)

    @rule("US-CRYPTO-05")
    @rule("CA-CRYPTO-06")
    def test_quantity_band(self):
        # m1244/m1412/m1340 (an equal arrival matches, also a tiny one),
        # m1439 (more than was sent never matches), m1140 (exactly 90%
        # matches).
        for sent, got, ok in ((1.0, 1.0, True), (0.0001, 0.0001, True),
                              (1.0, 1.5, False), (1.0, 0.9, True),
                              (1.0, 0.89, False)):
            _un, pairs = self._pairs([_s(quantity=-sent),
                                      self._arrive(quantity=got)])
            self.assertEqual(bool(pairs), ok, (sent, got))

    @rule("CA-CRYPTO-06")
    @rule("US-CRYPTO-05")
    def test_only_sends_are_sends(self):
        # m1338: an arrival with no send is neither unmatched nor paired.
        un, pairs = self._pairs([self._arrive(quantity=0.5)])
        self.assertEqual((un, pairs), ([], []))

    @rule("CA-CRYPTO-06")
    @rule("US-CRYPTO-05")
    def test_each_arrival_pairs_once_and_only_its_coin(self):
        # m1131 / m1132. The pairing is an assignment (re-audit
        # A2-0078): of two equal sends, the one closer in time to the
        # single arrival takes it.
        rows = [_s(quantity=-1.0, time="07:00:00"),
                _s(quantity=-1.0, time="07:05:00"),
                self._arrive(time="07:10:00"),
                self._arrive(symbol="ETH", time="07:11:00")]
        un, pairs = self._pairs(rows)
        self.assertEqual(len(pairs), 1)
        self.assertEqual([u["time"] for u in un], ["07:00:00"])

    @rule("CA-CRYPTO-06")
    @rule("US-CRYPTO-05")
    def test_same_exchange_other_account_is_a_move(self):
        # m1241 (and m1134: the same exchange AND account is not).
        rows = [_s(quantity=-1.0, exchange="kraken", account="a"),
                _s(quantity=1.0, exchange="kraken", account="b",
                   time="09:48:00")]
        self.assertEqual(len(self._pairs(rows)[1]), 1)
        rows[1]["account"] = "a"
        un, pairs = self._pairs(rows)
        self.assertEqual((len(un), pairs), (1, []))

    @rule("CA-CRYPTO-06")
    @rule("US-CRYPTO-05")
    def test_closest_in_time_then_least_loss_then_first(self):
        # m1246 (|delta|), m1247 (smaller loss), m1249 (first on a tie).
        send = _s(quantity=-1.0, time="12:00:00")
        _u, p = self._pairs([dict(send),
                             self._arrive(time="11:55:00"),
                             self._arrive(time="12:01:00",
                                          exchange="binance")])
        self.assertEqual(p[0][2], "binance")
        _u, p = self._pairs([dict(send),
                             self._arrive(time="12:01:00", quantity=0.95),
                             self._arrive(time="12:01:00", quantity=0.99,
                                          exchange="binance")])
        self.assertEqual(p[0][1], 0.99)
        _u, p = self._pairs([dict(send),
                             self._arrive(time="12:01:00"),
                             self._arrive(time="12:01:00",
                                          exchange="binance")])
        self.assertEqual(p[0][2], "kraken")


class TestDecisions(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sends.json"
            p.write_text(text)
            return cs.load_decisions(p)

    def test_missing_and_schema_default(self):
        # m1143 / m1051.
        self.assertEqual(cs.load_decisions(Path("/nonexistent/sends.json")),
                         {"schema_version": 1, "sends": {}})
        self.assertEqual(self._load('{"sends": {}}')["schema_version"], 1)

    def test_malformed_manifests_are_value_errors(self):
        # m1015 (bad JSON), m1016 (not an object / sends not a table),
        # m1050 (a record that is not a table, an unknown decision).
        for text, msg in (("{x", "is not valid JSON"),
                          ("[]", "expected"),
                          ('{"sends": []}', "expected"),
                          ('{"sends": {"k": "gift"}}', "has decision"),
                          ('{"sends": {"k": {"decision": "maybe"}}}',
                           "has decision 'maybe'")):
            with self.assertRaises(ValueError, msg=text) as cm:
                self._load(text)
            self.assertIn(msg, str(cm.exception), text)

    def test_record_decision_note_and_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sub" / "sends.json"
            cs.record_decision(p, "k", "gift", note="for mum", price=2.5,
                               summary="Kraken withdrawal")
            # Same decision, no --price: the hand price stays.
            cs.record_decision(p, "k", "gift", note="")
            doc = json.loads(p.read_text())
            self.assertEqual(doc["sends"]["k"],
                             {"decision": "gift", "price": 2.5,
                              "summary": "Kraken withdrawal"})
            # Another decision without --price drops it (re-audit
            # A2-0572: a typo'd price was stuck for good).
            cs.record_decision(p, "k", "payment")
            doc = json.loads(p.read_text())
            self.assertEqual(doc["sends"]["k"],
                             {"decision": "payment",
                              "summary": "Kraken withdrawal"})
            with self.assertRaises(ValueError):
                cs.record_decision(p, "k", "donation")


class TestRates(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "to_base.csv"
            p.write_text(text)
            return cs.load_rates(p)

    @rule("CA-CRYPTO-07")
    def test_rate_file_lines(self):
        # m1055/m1146/m1250/m1252 (a 5-column line without source is a
        # rate; a short line is not), m1056-m1059 (bad lines skipped),
        # m1149/m1251/m1344 (a positive finite rate below 1 is kept).
        t = self._load("\n".join([
            "2025-01-02 12:00:00 USD CAD 1.35 BoC",
            "2025-01-03 12:00:00 usd CAD 1.36",
            "2025-01-04 12:00:00 CAD USD 0.73 yahoo",
            "2025-01-05 USD",
            "Jan-06 12:00:00 USD CAD 1.37",
            "2025-01-07 12:00:00 USD CAD abc"]))
        self.assertEqual(t, {"USD": {"2025-01-02": (1.35, "boc"),
                                     "2025-01-03": (1.36, "")},
                             "CAD": {"2025-01-04": (0.73, "yahoo")}})
        # A rate that parses but is not a positive finite number is
        # refused, as convert-currency refuses it (re-audit A2-0999).
        for bad in ("-1.0", "0", "inf", "nan"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                self._load(f"2025-01-08 12:00:00 USD CAD {bad}")
            self.assertIn("not a positive finite number", str(cm.exception))

    def test_lookup_labels_and_text(self):
        # m1152 (an unknown source keeps its own name), m1064 (the
        # "latest before" wording).
        r = cs.Rates({"USD": {"2025-01-02": (1.35, "yahoo"),
                              "2025-01-06": (1.4, "boc")}}, "cad")
        self.assertEqual(r.get("CAD", "2025-01-03"), (1.0, "2025-01-03", ""))
        self.assertEqual(r.get("usd", "2025-01-03"),
                         (1.35, "2025-01-02", "yahoo"))
        self.assertIsNone(r.get("USD", "2025-01-01"))
        self.assertEqual(cs._rate_text("USD", "CAD",
                                       r.get("USD", "2025-01-06"),
                                       "2025-01-06"),
                         "Bank of Canada USD/CAD 1.4 (2025-01-06)")
        self.assertEqual(cs._rate_text("USD", "CAD",
                                       r.get("USD", "2025-01-07"),
                                       "2025-01-07"),
                         "Bank of Canada USD/CAD 1.4 (2025-01-06, latest "
                         "before 2025-01-07)")


class TestYahooPrice(unittest.TestCase):
    def _lookup(self, cache, fetch=None, offline=True):
        from taxjson.bin import fill_crypto_prices as F
        with tempfile.TemporaryDirectory() as tmp:
            cf = Path(tmp) / "cache.json"
            cf.write_text(json.dumps(cache))
            env = {"TAXJSON_OFFLINE": "1" if offline else "0"}
            with mock.patch.object(F, "CACHE_FILE", str(cf)), \
                    mock.patch.dict(os.environ, env), \
                    mock.patch.object(F, "get_crypto_price",
                                      fetch or (lambda s, d: 0.0)):
                look = cs.yahoo_usd_price(Path(tmp))
                got = look("ZZQ", "2025-01-02")
                saved = json.loads(cf.read_text())
            return got, saved

    def test_cache_hits_and_misses(self):
        # m1066 (a missing key is a miss), m1154 (a cached 0 is a miss),
        # m1255 (a cached 0.5 is a price).
        self.assertEqual(self._lookup({})[0], (None, "ZZQ"))
        self.assertEqual(self._lookup({"ZZQ-2025-01-02": 0.0})[0],
                         (None, "ZZQ"))
        self.assertEqual(self._lookup({"ZZQ-2025-01-02": 0.5})[0],
                         (0.5, "ZZQ"))

    def test_fetch_caches_a_good_price(self):
        # m1067/m1155/m1256: a fetched 0.5 is kept and cached; a failed
        # fetch (0.0) is not.
        got, saved = self._lookup({}, fetch=lambda s, d: 0.5,
                                  offline=False)
        self.assertEqual(got, (0.5, "ZZQ"))
        self.assertEqual(saved, {"ZZQ-2025-01-02": 0.5})
        got, saved = self._lookup({}, fetch=lambda s, d: 0.0,
                                  offline=False)
        self.assertEqual((got, saved), ((None, "ZZQ"), {}))


class TestFairValue(unittest.TestCase):
    @rule("CA-CRYPTO-07")
    def test_send_row_prices(self):
        # m1068 (value is positive), m1161/m1162 (value = qty x price in
        # cents), m1258 (a 0.5 spot price is a price), m1349 (a foreign
        # spot price is converted).
        fv = cs.fair_value(_s(quantity=-3.0, price=0.5, currency="CAD"),
                           _rates(), None)
        self.assertEqual((fv["price"], fv["value"]), (0.5, 1.5))
        self.assertIn("spot price on the send row (CAD)", fv["source"])
        fv = cs.fair_value(_s(quantity=-0.123, date="2025-03-01",
                              price=100.0, currency="USD"),
                           _rates(), None)
        self.assertEqual((fv["price"], fv["value"]), (145.0, 17.84))
        self.assertIn("Coinbase spot price 100 USD x Bank of Canada "
                      "USD/CAD 1.45 (2025-03-01)", fv["source"])
        self.assertIsNone(cs.fair_value(_s(price=1.0, currency="EUR"),
                                        _rates(), None))

    @rule("CA-CRYPTO-08")
    def test_stablecoin_is_dollar_cash_in_canada(self):
        # m1346: the rate is named when the base is not USD.
        fv = cs.fair_value(_s(symbol="USDC", quantity=-10.0,
                              date="2025-03-01"), _rates(), None)
        self.assertEqual((fv["price"], fv["value"]), (1.45, 14.5))
        self.assertIn("US-dollar cash in the books) x Bank of Canada",
                      fv["source"])

    @rule("CA-CRYPTO-07")
    def test_manual_and_yahoo(self):
        fv = cs.fair_value(_s(quantity=-2.0), _rates(), None, manual=3.25)
        self.assertEqual((fv["value"], fv["source"]),
                         (6.5, "entered by hand (--price)"))
        fv = cs.fair_value(_s(quantity=-2.0, date="2025-03-01"), _rates(),
                           lambda s, d: (10.0, "SOL"))
        self.assertEqual(fv["price"], 14.5)
        self.assertIsNone(cs.fair_value(_s(), _rates(), None))
        self.assertIsNone(cs.fair_value(_s(), _rates(),
                                        lambda s, d: (None, "SOL")))

    def test_tt_line_rounds_the_total_once(self):
        # m1265: 0.3749 -> 0.37 (a 3-decimal pre-round would give 0.38).
        line = cs.tt_line(_s(quantity=-1.0), {"price": 0.3749}, "CAD")
        self.assertTrue(line.endswith(" 0.3749 0.37 0"), line)


# ------------------------------------------------- USD / stablecoin pool
KR_HEADER = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,"
             "fee,balance\n")


def _kr(rows):
    return KR_HEADER + "".join(
        f"L{i},{ref},{t},{typ},{sub},currency,{asset},spot,{amt},{fee},0\n"
        for i, (ref, t, typ, sub, asset, amt, fee) in enumerate(rows))


class _UTC(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "UTC"})
        p.start()
        self.addCleanup(p.stop)


class TestKrakenPoolFlows(_UTC):
    def _flows(self, rows, text=None):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ledgers.csv"
            p.write_text(text if text is not None else _kr(rows))
            return [(f["dt"].strftime("%m-%d %H:%M"), f["units"], f["cad"])
                    for f in cs.kraken_pool_flows(p)]

    @rule("CA-CRYPTO-08")
    def test_flows(self):
        flows = self._flows([
            # CAD -> USDC: exact CAD cost (one refid, two legs)
            ("R1", "2025-01-15 15:00:00", "spend", "", "ZCAD", -1370, 0),
            ("R1", "2025-01-15 15:00:00", "receive", "", "USDC", 1000, 0),
            # custody of a coin: the sidecar's (m1267, m1268)
            ("R2", "2025-01-16 10:00:00", "withdrawal", "", "XXBT", -1, 0),
            ("R3", "2025-01-16 11:00:00", "transfer", "transferpeertopeer",
             "USDC", -50, 0),
            # a fiat USD deposit IS a pool flow (m1268)
            ("R4", "2025-01-17 10:00:00", "deposit", "", "ZUSD", 500, 0),
            # own-wallet shuffles (m1166, m1270, m1354, m1356, m1357)
            ("R5", "2025-01-18 10:00:00", "earn", "allocation", "USDC",
             -100, 0),
            ("R6", "2025-01-18 11:00:00", "hybridearnallocation", "",
             "USDC", -20, 0),
            ("R7", "2025-01-18 12:00:00", "transfer", "spottostaking",
             "USDC", -30, 0),
            # a transfer that is not a wallet move is a flow (m1353,
            # m1414, m1415, m1357)
            ("R8", "2025-01-18 13:00:00", "transfer", "airdrop", "USDC",
             3, 0),
            # an earn REWARD is a flow (m1166)
            ("R9", "2025-02-01 12:00:00", "earn", "reward", "USDC", 2, 0),
            # USDC -> BTC with the fee in USDC (m1169, m1360)
            ("RA", "2025-02-02 12:00:00", "trade", "", "USDC", -100, 0.5),
            ("RA", "2025-02-02 12:00:00", "trade", "", "XXBT", 0.001, 0),
            # CAD -> USDC with the fee in CAD (m1170, m1364)
            ("RB", "2025-02-03 12:00:00", "trade", "", "ZCAD", -200, 1),
            ("RB", "2025-02-03 12:00:00", "trade", "", "USDC", 140, 0),
            # a coin-only trade is no flow
            ("RC", "2025-02-04 12:00:00", "trade", "", "XETH", -1, 0),
            ("RC", "2025-02-04 12:00:00", "trade", "", "XXBT", 0.05, 0),
        ])
        self.assertEqual(flows, [
            ("01-15 15:00", 1000.0, -1370.0),
            ("01-17 10:00", 500.0, 0.0),
            ("01-18 13:00", 3.0, 0.0),
            ("02-01 12:00", 2.0, 0.0),
            ("02-02 12:00", -100.5, 0.0),     # m1273: spends are kept
            ("02-03 12:00", 140.0, -201.0)])

    def test_not_a_ledger_export(self):
        self.assertEqual(self._flows(None, text="txid,ordertxid,pair\n"),
                         [])


CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")


class TestCoinbasePoolFlows(_UTC):
    def _flows(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cb.csv"
            p.write_text(text)
            return [(f["dt"].strftime("%m-%d %H:%M"), f["units"], f["cad"])
                    for f in cs.coinbase_pool_flows(p)]

    @rule("CA-CRYPTO-08")
    def test_flows(self):
        text = ("Transactions\n"
                "User,someone,uid\n"           # preamble (m1071, m1176)
                + CB_HEADER +
                "c1,2025-05-01 12:00:00 UTC,Buy,USDC,20,CAD,1.38,27.60,"
                "27.60,0,Bought 20 USDC\n"
                "c2,2025-05-02T12:00:00Z,Buy,USDC,10,USD,1,10,10,0,"
                "Bought 10 USDC\n"             # ISO stamp (m1181)
                "c3,2025-05-03 12:00:00 UTC,Sell,USDC,-5,USD,1,5,5,0,"
                "Sold 5 USDC\n"                # signed quantity (m1182)
                "c4,2025-05-04 12:00:00 UTC,Staking Income,USDC,0.5,USD,1,"
                "0.5,0.5,0,\n"
                "c5,2025-05-05 12:00:00 UTC,Send,USDC,7,USD,1,7,7,0,"
                "Sent 7 USDC\n"                # sidecar's (m1074)
                "c6,not-a-date,Buy,USDC,1,USD,1,1,1,0,x\n"
                "c7,2025-05-06 12:00:00 UTC,Convert,USDC,100,USD,1,100,100,"
                "0,Converted 100 USDC to 0.001 BTC\n"
                "c8,2025-05-07 12:00:00 UTC,Convert,USDC,10,USD,1,10,10,0,"
                "Converted 10 USDC to 10 USDT\n"
                "c9,2025-05-08 12:00:00 UTC,Convert,BTC,0.002,USD,50000,"
                "100,100,0,Converted 0.002 BTC to 99 USDC\n"
                "ca,2025-05-09 12:00:00 UTC,Advanced Trade Buy,BTC,0.001,"
                "USD,50000,50,50,0,Bought 0.001 BTC for 50.25 USDC on "
                "BTC-USDC at 50000 USDC/BTC\n"
                "cb,2025-05-10 12:00:00 UTC,Advanced Trade Sell,ETH,0.1,"
                "USD,3000,300,300,0,Sold 0.1 ETH for 299 USDC on ETH-USDC "
                "at 3000 USDC/ETH\n"
                "cc,2025-05-11 12:00:00 UTC,Advanced Trade Buy,ETH,0.1,"
                "USD,3000,300,300,0,Bought 0.1 ETH for 300 USD on ETH-USD "
                "at 3000 USD/ETH\n"
                "cd,2025-05-12 12:00:00 UTC,Buy,USDC,4,CAD,1.4,5.6,,0,"
                "Bought 4 USDC\n"              # no total (m1420)
                ",,\n")
        self.assertEqual(self._flows(text), [
            ("05-01 12:00", 20.0, -27.6),     # m1417, m1460, m1461
            ("05-02 12:00", 10.0, 0.0),       # m1462
            ("05-03 12:00", -5.0, 0.0),
            ("05-04 12:00", 0.5, 0.0),
            ("05-06 12:00", -100.0, 0.0),
            ("05-08 12:00", 99.0, 0.0),
            ("05-09 12:00", -50.25, 0.0),
            ("05-10 12:00", 299.0, 0.0),
            ("05-12 12:00", 4.0, -0.0)])

    def test_column_order_and_missing_columns(self):
        # m1277 (a column at index 0 is read), m1178 (a column the
        # export lacks reads as empty: no Total column).
        text = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
                "Price Currency,Notes\n"
                "2025-05-01 12:00:00 UTC,Buy,USDC,20,CAD,Bought 20 USDC\n")
        self.assertEqual(self._flows(text), [("05-01 12:00", 20.0, -0.0)])

    def test_convert_with_unreadable_notes_is_no_flow(self):
        # m1186: a Convert row whose notes do not parse adds nothing.
        text = (CB_HEADER +
                "c1,2025-05-01 12:00:00 UTC,Convert,BTC,0.002,USD,50000,"
                "100,100,0,something else\n")
        self.assertEqual(self._flows(text), [])

    def test_no_header_no_flows(self):
        # m1023.
        self.assertEqual(self._flows("just,some,text\n1,2,3\n"), [])


def _flow(day, units, cad=0.0):
    return {"dt": datetime.strptime(day + " 12:00:00", "%Y-%m-%d %H:%M:%S"),
            "units": units, "cad": cad, "what": "test"}


class TestUsdPool(unittest.TestCase):
    """CA-CRYPTO-08: average cost of the US-dollar/stablecoin pool
    against the send-date value."""

    def _pool(self, flows, transfers, pairs=(), table=None):
        rates = _rates(table if table is not None else
                       {"USD": {"2025-01-01": (1.40, "boc"),
                                "2025-03-01": (1.50, "boc")}})
        ids = {id(t): cs.send_id(t["exchange"], t["date"], t["time"],
                                 t["symbol"], t["quantity"])
               for t in transfers if t["quantity"] < 0
               and not any(o is t for o, _i in pairs)}
        res = cs.usd_pool(flows, transfers, list(pairs), ids, rates)
        return res, ids

    def _send(self, day, q, symbol="USDC", **kw):
        return _s(exchange="kraken", date=day, time="13:00:00",
                  symbol=symbol, quantity=-q, **kw)

    @rule("CA-CRYPTO-08")
    def test_gain_against_average_cost(self):
        # 1000 at 1370 CAD + 10 at the rate (1.40) = 1384 / 1010;
        # 100 sent on 2025-03-01 at 1.50: value 150, ACB 137.03.
        send = self._send("2025-03-01", 100.0)
        res, ids = self._pool([_flow("2025-01-15", 1000.0, -1370.0),
                               _flow("2025-02-01", 10.0)], [send])
        r = res["results"][ids[id(send)]]
        self.assertEqual((r["value"], r["acb"], r["gain"], r["overdrawn"],
                          r["superficial"]), (150.0, 137.03, 12.97, False,
                                              False))
        self.assertEqual((res["overdrafts"], res["unrated"]), (0, 0))
        self.assertEqual(res["units_now"], 910.0)
        # m1294 / m1295: avg cost = cost / units, 6 decimals.
        self.assertEqual(res["avg_cost"], round(1384 * 910 / 1010 / 910, 6))
        self.assertEqual(res["avg_cost"], 1.370297)

    @rule("CA-CRYPTO-08")
    def test_overdraft_has_zero_gain_on_the_short_part(self):
        # m1087 (no ACB from an empty pool), m1088/m1189 (0.5 units are
        # covered), m1194 (counted), m1386 (short x rate).
        send = self._send("2025-03-01", 10.0)
        res, ids = self._pool([_flow("2025-01-15", 0.5, -0.7)], [send])
        r = res["results"][ids[id(send)]]
        self.assertEqual((r["acb"], r["value"], r["gain"], r["overdrawn"]),
                         (14.95, 15.0, 0.05, True))
        self.assertEqual(res["overdrafts"], 1)
        self.assertIsNone(res["avg_cost"])
        send = self._send("2025-03-01", 10.0)
        res, ids = self._pool([], [send])
        r = res["results"][ids[id(send)]]
        self.assertEqual((r["acb"], r["gain"]), (15.0, 0.0))

    @rule("CA-CRYPTO-08")
    def test_other_coins_and_arrivals(self):
        # m1085 (a BTC send is not pool cash), m1187/m1371/m1372 (an
        # unpaired arrival of 0.5 USDC is an acquisition at the rate),
        # m1188 (a paired arrival is not), m1288/m1289/m1383 (a paired
        # send loses only its network fee), m1382 (a spend flow).
        btc = self._send("2025-02-01", 1.0, symbol="BTC")
        arr = _s(exchange="kraken", date="2025-02-02", symbol="USDC",
                 quantity=0.5)
        out = self._send("2025-02-03", 100.0)
        inn = _s(exchange="coinbase", date="2025-02-03", time="13:05:00",
                 symbol="USDC", quantity=99.0)
        res, _ids = self._pool([_flow("2025-01-15", 1000.0, -1400.0),
                                _flow("2025-01-20", -200.0)],
                               [btc, arr, out, inn], pairs=[(out, inn)])
        # 1000 - 200 + 0.5 - 1 (fee) = 799.5
        self.assertEqual(res["units_now"], 799.5)
        self.assertEqual(res["results"], {})

    @rule("CA-CRYPTO-08")
    def test_unrated_acquisition_is_counted_and_skipped(self):
        # m1425 / m1380.
        res, _ = self._pool([_flow("2024-06-01", 10.0)], [])
        self.assertEqual((res["unrated"], res["units_now"]), (1, 0.0))

    @rule("CA-CRYPTO-08")
    def test_us_dollar_send_has_no_fx_line(self):
        # m1290: only a STABLECOIN send gets the gain line.
        send = self._send("2025-03-01", 10.0, symbol="USD")
        res, _ = self._pool([_flow("2025-01-15", 100.0, -140.0)], [send])
        self.assertEqual(res["results"], {})
        self.assertEqual(res["units_now"], 90.0)

    @rule("CA-CRYPTO-08")
    def test_pool_drained_exactly_restarts_its_average(self):
        # m1374/m1375: after a full drain, a new lot's cost is its own.
        send = self._send("2025-02-01", 100.0)
        res, _ = self._pool([_flow("2025-01-15", 100.0, -137.0),
                             _flow("2025-02-02", 10.0, -15.0)], [send])
        self.assertEqual((res["units_now"], res["avg_cost"]), (10.0, 1.5))

    @rule("CA-CRYPTO-08")
    def test_cent_rounding(self):
        # m1449/m1450/m1452: value, ACB and gain in cents; m1201 units.
        send = self._send("2025-03-01", 33.333)
        res, ids = self._pool([_flow("2025-01-15", 100.0, -137.0001)],
                              [send])
        r = res["results"][ids[id(send)]]
        for k in ("value", "acb", "gain"):
            self.assertEqual(round(r[k], 2), r[k], k)
        self.assertEqual(r["value"], 50.0)
        self.assertEqual(res["units_now"], 66.667)
        res, _ = self._pool([_flow("2025-01-15", 1.23456789, -2.0)], [])
        self.assertEqual(res["units_now"], 1.234568)
        # m1295: the average cost has 6 decimals.
        res, _ = self._pool([_flow("2025-01-15", 3.0, -1.0)], [])
        self.assertEqual(res["avg_cost"], 0.333333)


class TestPoolSuperficialFlag(unittest.TestCase):
    """s.54 on the pool: a loss, the property bought within 30 days
    either side, still held on day 30."""

    def _run(self, flows, sends):
        rates = _rates({"USD": {"2025-01-01": (1.50, "boc"),
                                "2025-03-01": (1.40, "boc"),
                                "2025-05-01": (1.50, "boc")}})
        ids = {id(t): cs.send_id(t["exchange"], t["date"], t["time"],
                                 t["symbol"], t["quantity"])
               for t in sends}
        res = cs.usd_pool(flows, sends, [], ids, rates)
        return [res["results"][ids[id(t)]] for t in sends]

    def _send(self, day, q):
        return _s(exchange="kraken", date=day, time="13:00:00",
                  symbol="USDC", quantity=-q)

    @rule("CA-CRYPTO-08")
    def test_window_and_still_held(self):
        # Loss: bought at 1.50, sent at 1.40.
        base = [_flow("2025-01-15", 100.0, -150.0)]
        # reacquired 30 days after (inclusive) and held -> superficial
        r, = self._run(base + [_flow("2025-03-31", 5.0)],
                       [self._send("2025-03-01", 10.0)])
        self.assertTrue(r["superficial"])
        # m1009: day 31 is outside.
        r, = self._run(base + [_flow("2025-04-01", 5.0)],
                       [self._send("2025-03-01", 10.0)])
        self.assertFalse(r["superficial"])
        # m1095: no acquisition in the window -> not superficial even
        # though units are still held.
        r, = self._run(base, [self._send("2025-03-01", 10.0)])
        self.assertFalse(r["superficial"])
        # m1199: everything gone ON day 30 -> not held.
        r, = self._run(base + [_flow("2025-03-02", 5.0),
                               _flow("2025-03-31", -95.0)],
                       [self._send("2025-03-01", 10.0)])
        self.assertFalse(r["superficial"])

    @rule("CA-CRYPTO-08")
    def test_only_losses_and_each_send_by_its_own_date(self):
        # m1092/m1198: a zero or small positive gain is never
        # superficial; m1427: the second send's window is its own.
        r1, = self._run([_flow("2025-03-01", 100.0, -140.0),
                         _flow("2025-03-05", 5.0)],
                        [self._send("2025-03-02", 10.0)])
        self.assertEqual(r1["gain"], 0.0)
        self.assertFalse(r1["superficial"])
        g, = self._run([_flow("2025-03-01", 100.0, -139.95),
                        _flow("2025-03-05", 5.0)],
                       [self._send("2025-03-02", 10.0)])
        self.assertEqual(g["gain"], 0.01)
        self.assertFalse(g["superficial"])
        early, late = self._run([_flow("2025-01-15", 100.0, -150.0),
                                 _flow("2025-01-20", 5.0)],
                                [self._send("2025-01-21", 1.0),
                                 self._send("2025-03-01", 10.0)])
        self.assertFalse(late["superficial"])


# ------------------------------------------------------------- report
def _project(tmp, *, country="canada", base="CAD", sidecar=None,
             decisions=None, rates=None):
    root = Path(tmp)
    (root / "work").mkdir(parents=True, exist_ok=True)
    for a in ("a", "b"):
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    (root / "work" / "a_kraken_transfers.json").write_text(json.dumps({
        "metadata": {"kind": "transfer_sidecar", "brokerage": "kraken"},
        "transactions": sidecar or []}))
    (root / "work" / "to_base.csv").write_text(
        rates or ("2025-01-01 12:00:00 USD CAD 1.40 boc\n"
                  "2025-01-31 12:00:00 USD CAD 1.40 boc\n"))
    if decisions is not None:
        (root / "inputs" / "a" / "sends.json").write_text(
            json.dumps({"sends": decisions}))
    settings = {"country": country}
    if base:
        settings["base_currency"] = base
    return root, {"settings": settings,
                  "accounts": {"a": {"crypto": True}, "b": {"crypto": True},
                               "c": {}}}


SIDE = [{"date": "2025-02-01", "time": "10:00:00", "symbol": "USDC",
         "quantity": -100, "description": "withdrawal"},
        {"date": "2025-02-02", "time": "10:00:00", "symbol": "SOL",
         "quantity": -2, "price": 150, "currency": "CAD",
         "description": "withdrawal"}]
USDC_ID = "kr-20250201T100000-USDC-100"
SOL_ID = "kr-20250202T100000-SOL-2"


class TestBuildReport(_UTC):
    @rule("CA-CRYPTO-08")
    def test_canada_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, cfg = _project(tmp, base=None, sidecar=SIDE, decisions={
                SOL_ID: {"decision": "gift"},
                "kr-20200101T000000-ETH-1": {"decision": "self"}})
            cb = Path(tmp) / "cb.csv"
            cb.write_text(CB_HEADER +
                          "c1,2025-01-15 12:00:00 UTC,Buy,USDC,150,CAD,1.38,"
                          "207,207,0,Bought 150 USDC\n")
            doc = cs.build_report(root, cfg, broker_files={
                "a": [("coinbase", cb)]})
        # m1297: the base defaults to the country's currency.
        self.assertEqual(doc["base_currency"], "CAD")
        a = doc["accounts"]["a"]
        sends = {s["id"]: s for s in a["sends"]}
        # m1392: Canada prices a stablecoin as US-dollar cash.
        self.assertIn("US-dollar cash", sends[USDC_ID]["fair_value"]
                      ["source"])
        self.assertTrue(sends[USDC_ID]["stable"])
        # m1390: the Coinbase ledger feeds the pool: 150 - 100 = 50.
        self.assertEqual(doc["pool"]["units_now"], 50.0)
        # m1308: the summary leaves the per-send results out.
        self.assertNotIn("results", doc["pool"])
        self.assertEqual(sends[USDC_ID]["fx"]["acb"], 138.0)
        # m1394: one undecided (the USDC send); m1429: the orphan.
        self.assertEqual(a["undecided"], 1)
        self.assertEqual(a["orphans"], ["kr-20200101T000000-ETH-1"])
        self.assertEqual(sends[SOL_ID]["tt"],
                         "BUYSELL 2025-02-02 10:00:00 SOL -2 CAD 150 "
                         "300.00 0")
        # m1101: only crypto accounts, and `want` filters.
        self.assertEqual(sorted(doc["accounts"]), ["a", "b"])
        with tempfile.TemporaryDirectory() as tmp:
            root, cfg = _project(tmp, sidecar=SIDE)
            doc = cs.build_report(root, cfg, want="b")
        self.assertEqual(list(doc["accounts"]), ["b"])


class TestNetworkFees(unittest.TestCase):
    def _pairs(self, **o):
        out = _s(quantity=-1.0, **o)
        inn = _s(exchange="kraken", quantity=0.99, time="07:20:00",
                 symbol=out["symbol"])
        return [(out, inn)]

    @rule("CA-CRYPTO-06")
    def test_fee_rows(self):
        # m1313: "sent" is the positive quantity sent.
        fees = cs.network_fees(self._pairs(price=100.0, currency="CAD"),
                               "c", _rates(), None, "canada")
        self.assertEqual(len(fees), 1)
        f = fees[0]
        self.assertEqual((f["quantity"], f["sent"], f["arrived"]),
                         (0.01, 1.0, 0.99))
        self.assertTrue(f["id"].endswith("-SOL-1-fee"))

    @rule("CA-CRYPTO-06")
    @rule("US-CRYPTO-05")
    def test_skips(self):
        # m1103/m1104: another account's pair, and a send whose fee the
        # ledger states, are not this account's hidden fees; m1108: a
        # full arrival has no fee.
        self.assertEqual(cs.network_fees(self._pairs(), "other", _rates(),
                                         None, "canada"), [])
        self.assertEqual(cs.network_fees(self._pairs(fee=0.01), "c",
                                         _rates(), None, "canada"), [])
        out, inn = _s(quantity=-1.0), _s(exchange="kraken", quantity=1.0)
        self.assertEqual(cs.network_fees([(out, inn)], "c", _rates(),
                                         None, "canada"), [])

    @rule("US-CRYPTO-05")
    def test_us_stablecoin_fee_is_property_at_par(self):
        # m1311: a US project prices the USDC fee at its 1.00 USD par.
        rates = cs.Rates({}, "USD")
        fees = cs.network_fees(self._pairs(symbol="USDC"), "c", rates,
                               None, "usa")
        self.assertEqual(len(fees), 1)
        self.assertIn("at its 1.00 USD par", fees[0]["fair_value"]["source"])


class TestDuplicateLines(unittest.TestCase):
    ENTRY = {"id": "kr-20260316T142241-KSM-0.25", "date": "2026-03-16",
             "time": "14:22:41", "symbol": "KSM", "quantity": 0.25}

    def test_only_hand_written_tt_sales_of_the_same_coin_and_qty(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "inputs"
            a, b = inputs / "a", inputs / "b"
            a.mkdir(parents=True)
            b.mkdir()
            line = "BUYSELL 2026-03-16 14:22:41 KSM -0.25 CAD 212.40 53.10 0"
            (a / "mine.tt").write_text(
                "# notes\n"
                "BUYSELL 2026-03-16\n"                       # m1326/m1327
                "DIVIDEND 2026-03-16 14:22:41 KSM -0.25 CAD 1 1 0\n"
                "BUYSELL 2026-03-16 14:22:41 KSM x CAD 1 1 0\n"  # m1328
                "BUYSELL 2026-03-16 14:22:41 ETH -0.25 CAD 1 1 0\n"  # m1329
                + line + "\n")
            (b / "other.tt").write_text(
                "BUYSELL 2026-03-16 09:00:00 ksm -0.25 CAD 1 1 0\n")
            (a / "crypto_sends.tt").write_text(line + "\n")   # m1218
            (a / "notes.txt").write_text(line + "\n")
            (a / "bad.tt").write_bytes(b"\xff\xfe BUYSELL")     # m1220
            hits = cs.duplicate_lines(a, [dict(self.ENTRY)])
        self.assertEqual(
            [(h["file"], h["line"], h["same_time"]) for h in hits],
            [("inputs/a/mine.tt", 6, True),
             ("inputs/b/other.tt", 1, False)])


class TestPrompt(unittest.TestCase):
    def _sends(self):
        return [{"id": "k1", "summary": "Kraken withdrawal 1 USDC",
                 "decision": None, "symbol": "USDC", "stable": True,
                 "fair_value": {"price": 1.45, "currency": "CAD",
                                "value": 1.45, "source": "src"},
                 "fx": {"gain": -1.234, "superficial": True}}]

    @rule("CA-CRYPTO-07")
    def test_answers_and_messages(self):
        # m1037 (the given `say` is used), m1433 (the FX gain line),
        # m1225 (an unknown answer asks again).
        said = []
        with tempfile.TemporaryDirectory() as tmp:
            man = Path(tmp) / "sends.json"
            answers = iter(["zz", "s"])
            n = cs.prompt_undecided(self._sends(), man,
                                    ask=lambda q: next(answers),
                                    say=said.append)
            self.assertEqual(n, 1)
            self.assertEqual(cs.load_decisions(man)["sends"]["k1"]
                             ["decision"], "self")
        text = "\n".join(said)
        self.assertIn("FX gain -1.23 (likely superficial)", text)
        self.assertIn("answer s, g, p or k", text)

    def test_eof_and_nothing_to_do(self):
        # m1223 (EOF stops), m1116 (no undecided send).
        with tempfile.TemporaryDirectory() as tmp:
            man = Path(tmp) / "sends.json"

            def eof(q):
                raise EOFError
            self.assertEqual(cs.prompt_undecided(self._sends(), man,
                                                 ask=eof, say=lambda m: 0),
                             0)
            self.assertEqual(cs.prompt_undecided([], man), 0)
            self.assertFalse(man.exists())

    @rule("US-SEND-02")
    def test_us_prompt_refuses_gift(self):
        # m1226/m1331/m1332/m1227.
        said = []
        with tempfile.TemporaryDirectory() as tmp:
            man = Path(tmp) / "sends.json"
            answers = iter(["g", "p"])
            cs.prompt_undecided(self._sends(), man,
                                ask=lambda q: next(answers),
                                say=said.append, allow_gift=False)
            self.assertEqual(cs.load_decisions(man)["sends"]["k1"]
                             ["decision"], "payment")
        self.assertIn("a gift is not a sale for the donor", "\n".join(said))


if __name__ == "__main__":
    unittest.main()
