"""Mutation pins for lib/crypto_sends.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round: send/arrival pairing (CA-CRYPTO-06 /
US-CRYPTO-05), the decisions manifest, rates and fair values
(CA-CRYPTO-07), the US-dollar/stablecoin pool and its FX gain
(CA-CRYPTO-08: the Kraken and Coinbase flow readers had no unit test at
all), network fees, the duplicate-line check and the prompt. Synthetic
data only; no network (the price source is stubbed).

Formerly: test_fix_x_mut_crypto_sends
"""
import unittest

from taxjson.lib import crypto_sends as cs
from taxjson.lib import base_rates as BR
from tax_rules import rule
from taxjson.rules import ids as R
from _support.crypto_send_flows import _s, _rates, _flow


class TestMatchTransfers(unittest.TestCase):
    """CA-CRYPTO-06 / US-CRYPTO-05: an arrival on another exchange
    within 10 minutes before to 3 days after, 90% to 100% of the coins.

    Formerly: test_fix_x_mut_crypto_sends.TestMatchTransfers"""

    def _arrive(self, **kw):
        base = dict(exchange="kraken", quantity=1.0)
        base.update(kw)
        return _s(**base)

    def _pairs(self, rows):
        un, pairs = cs.match_transfers(rows)
        return un, [(o["quantity"], i["quantity"], i["exchange"],
                     i["time"], i["date"]) for o, i in pairs]

    @rule(R.CA_CRYPTO_06)
    @rule(R.US_CRYPTO_05)
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

    @rule(R.US_CRYPTO_05)
    @rule(R.CA_CRYPTO_06)
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

    @rule(R.CA_CRYPTO_06)
    @rule(R.US_CRYPTO_05)
    def test_only_sends_are_sends(self):
        # m1338: an arrival with no send is neither unmatched nor paired.
        un, pairs = self._pairs([self._arrive(quantity=0.5)])
        self.assertEqual((un, pairs), ([], []))

    @rule(R.CA_CRYPTO_06)
    @rule(R.US_CRYPTO_05)
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

    @rule(R.CA_CRYPTO_06)
    @rule(R.US_CRYPTO_05)
    def test_same_exchange_other_account_is_a_move(self):
        # m1241 (and m1134: the same exchange AND account is not).
        rows = [_s(quantity=-1.0, exchange="kraken", account="a"),
                _s(quantity=1.0, exchange="kraken", account="b",
                   time="09:48:00")]
        self.assertEqual(len(self._pairs(rows)[1]), 1)
        rows[1]["account"] = "a"
        un, pairs = self._pairs(rows)
        self.assertEqual((len(un), pairs), (1, []))

    @rule(R.CA_CRYPTO_06)
    @rule(R.US_CRYPTO_05)
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


class TestFairValue(unittest.TestCase):
    """Formerly: test_fix_x_mut_crypto_sends.TestFairValue"""
    @rule(R.CA_CRYPTO_07)
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

    @rule(R.CA_CRYPTO_08)
    def test_stablecoin_is_dollar_cash_in_canada(self):
        # m1346: the rate is named when the base is not USD.
        fv = cs.fair_value(_s(symbol="USDC", quantity=-10.0,
                              date="2025-03-01"), _rates(), None)
        self.assertEqual((fv["price"], fv["value"]), (1.45, 14.5))
        self.assertIn("US-dollar cash in the books) x Bank of Canada",
                      fv["source"])

    @rule(R.CA_CRYPTO_07)
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


class TestUsdPool(unittest.TestCase):
    """CA-CRYPTO-08: average cost of the US-dollar/stablecoin pool
    against the send-date value.

    Formerly: test_fix_x_mut_crypto_sends.TestUsdPool"""

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

    @rule(R.CA_CRYPTO_08)
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

    @rule(R.CA_CRYPTO_08)
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

    @rule(R.CA_CRYPTO_08)
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

    @rule(R.CA_CRYPTO_08)
    def test_unrated_acquisition_is_counted_and_skipped(self):
        # m1425 / m1380.
        res, _ = self._pool([_flow("2024-06-01", 10.0)], [])
        self.assertEqual((res["unrated"], res["units_now"]), (1, 0.0))

    @rule(R.CA_CRYPTO_08)
    def test_us_dollar_send_has_no_fx_line(self):
        # m1290: only a STABLECOIN send gets the gain line.
        send = self._send("2025-03-01", 10.0, symbol="USD")
        res, _ = self._pool([_flow("2025-01-15", 100.0, -140.0)], [send])
        self.assertEqual(res["results"], {})
        self.assertEqual(res["units_now"], 90.0)

    @rule(R.CA_CRYPTO_08)
    def test_pool_drained_exactly_restarts_its_average(self):
        # m1374/m1375: after a full drain, a new lot's cost is its own.
        send = self._send("2025-02-01", 100.0)
        res, _ = self._pool([_flow("2025-01-15", 100.0, -137.0),
                             _flow("2025-02-02", 10.0, -15.0)], [send])
        self.assertEqual((res["units_now"], res["avg_cost"]), (10.0, 1.5))

    @rule(R.CA_CRYPTO_08)
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
    either side, still held on day 30.

    Formerly: test_fix_x_mut_crypto_sends.TestPoolSuperficialFlag"""

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

    @rule(R.CA_CRYPTO_08)
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

    @rule(R.CA_CRYPTO_08)
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


class TestNetworkFees(unittest.TestCase):
    """Formerly: test_fix_x_mut_crypto_sends.TestNetworkFees"""
    def _pairs(self, **o):
        out = _s(quantity=-1.0, **o)
        inn = _s(exchange="kraken", quantity=0.99, time="07:20:00",
                 symbol=out["symbol"])
        return [(out, inn)]

    @rule(R.CA_CRYPTO_06)
    def test_fee_rows(self):
        # m1313: "sent" is the positive quantity sent.
        fees = cs.network_fees(self._pairs(price=100.0, currency="CAD"),
                               "c", _rates(), None, "canada")
        self.assertEqual(len(fees), 1)
        f = fees[0]
        self.assertEqual((f["quantity"], f["sent"], f["arrived"]),
                         (0.01, 1.0, 0.99))
        self.assertTrue(f["id"].endswith("-SOL-1-fee"))

    @rule(R.CA_CRYPTO_06)
    @rule(R.US_CRYPTO_05)
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

    @rule(R.US_CRYPTO_05)
    def test_us_stablecoin_fee_is_property_at_par(self):
        # m1311: a US project prices the USDC fee at its 1.00 USD par.
        rates = BR.Rates({}, "USD")
        fees = cs.network_fees(self._pairs(symbol="USDC"), "c", rates,
                               None, "usa")
        self.assertEqual(len(fees), 1)
        self.assertIn("at its 1.00 USD par", fees[0]["fair_value"]["source"])


if __name__ == "__main__":
    unittest.main()
