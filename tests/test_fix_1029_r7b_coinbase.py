"""Fix round 7 (wave B), group "coinbase": the Coinbase readers and the
crypto send / price helpers (backlog TJ-0865, TJ-0870, TJ-0916,
TJ-0747, TJ-0652, TJ-0663, TJ-0667, TJ-0671, TJ-0744).

All rows, ids and amounts are synthetic.
"""
import contextlib
import copy
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.brokerages.coinbase import (CoinbaseBrokerage,
                                             coinbase_cash_events)
from taxjson.bin.taxjson_fx_cash import apply_jurisdiction
from tax_rules import rule


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")
_BOOK = "coins/coinbase"


def _events(td, name, body):
    p = Path(td) / name
    p.write_text(_HDR + body)
    evs = coinbase_cash_events(p)
    for e in evs:
        e.update(label="coins", book=_BOOK, broker="coinbase", file=name)
    return p, evs


def _lines(*texts):
    out = []
    for i, t in enumerate(texts, 1):
        e = CE.parse_line(t, f"cash.tt:{i}")
        e.update(label="coins", book=_BOOK, file="cash.tt", book_problem="")
        out.append(e)
    return out


def _ledger(events, lines, rate, native=()):
    return V2.build(copy.deepcopy(list(native)),
                    {"events": copy.deepcopy(events),
                     "lines": copy.deepcopy(lines), "problems": [],
                     "stablecoins_as_cash": True},
                    "CAD", 2025, rate, country="canada")


class TestConvertStablecoinLegs(unittest.TestCase):
    """TJ-0916: a Convert spending or receiving a stablecoin moves US-
    dollar cash (CA-CRYPTO-02); the v2 cash reader restores that leg
    from the row's Notes (the property parser books only the coin)."""

    _BODY = ("CB-1,2025-06-02 16:00:00 UTC,Convert,USDC,100,USD,1,100,100,"
             "0,Converted 100 USDC to 1 ZZE\n"
             "CB-2,2025-06-04 16:00:00 UTC,Convert,ZZE,1,USD,100,100,100,"
             "0,Converted 1 ZZE to 100 USDC\n"
             "CB-3,2025-06-06 16:00:00 UTC,Sell,USDC,100,CAD,6,600,600,0,"
             "Sold 100 USDC for 600 CAD\n")

    @rule("CA-CRYPTO-02")
    def test_convert_cash_legs_reach_the_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            p, evs = _events(td, "coinbase.csv", self._BODY)
            with contextlib.redirect_stderr(io.StringIO()):
                native = CoinbaseBrokerage().parse_file(p)
        for r in native:
            r.update(account="coins", source="coinbase.csv", _book=_BOOK)
        legs = sorted((e["date"], e["kind"], e["currency"], e["amount"])
                      for e in evs if e["kind"] == "FLOW")
        self.assertEqual(legs, [("2025-06-02", "FLOW", "USD", -100.0),
                                ("2025-06-04", "FLOW", "USD", 100.0)])
        v = _ledger(evs, _lines("CASHOPEN 2025-01-01 USD 100 100",
                                "CASHBAL 2025-12-31 USD 0"),
                    lambda c, d: 1.25 if d < "2025-06-04" else 1.5,
                    native)
        self.assertEqual(v["status"], "computed", v.get("problems"))
        self.assertAlmostEqual(v["net_gain"], 475.0, places=2)
        self.assertAlmostEqual(
            apply_jurisdiction(v["net_gain"], "canada")["reportable"],
            275.0, places=2)

    def test_asset_column_naming_either_leg(self):
        # The Asset column may name the coin or the stablecoin: the cash
        # leg is read from Notes either way.
        body = ("CB-1,2025-06-02 16:00:00 UTC,Convert,ZZE,1,USD,100,100,"
                "100,0,Converted 100 USDC to 1 ZZE\n")
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "a.csv", body)
        self.assertEqual([(e["kind"], e["amount"]) for e in evs],
                         [("FLOW", -100.0)])

    def test_tjs_cash_lists_the_leg_as_a_trade_not_a_fee(self):
        body = self._BODY.splitlines(True)[0]
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "a.csv", body)
        self.assertEqual(CE.tt_line(evs[0], "CAD"),
                         "#TRADE 2025-06-02 USD -100.00")

    def test_stablecoin_to_stablecoin_and_coin_to_coin_move_no_cash(self):
        body = ("CB-1,2025-06-02 16:00:00 UTC,Convert,USDC,100,USD,1,100,"
                "100,0,Converted 100 USDC to 100 USDT\n"
                "CB-2,2025-06-03 16:00:00 UTC,Convert,ZZE,1,USD,100,100,"
                "100,0,Converted 1 ZZE to 2 ZZF\n")
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "a.csv", body)
        self.assertEqual(evs, [])

    def test_property_mode_moves_no_cash(self):
        # A US project's stablecoin is property (US-CRYPTO-02).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.csv"
            p.write_text(_HDR + self._BODY.splitlines(True)[0])
            self.assertEqual(coinbase_cash_events(p, stable_cash=False), [])


class TestDistinctIdsAcrossFiles(unittest.TestCase):
    """TJ-0865: two exports holding distinct Coinbase IDs are two sets of
    executions, not one export's overlap; the same IDs in two files
    still count once."""

    _BUY = (",2025-06-02 16:00:00 UTC,Buy,USDC,100,CAD,1,100,100,0,"
            "Bought 100 USDC for 100 CAD\n")
    _SELL = (",2025-06-04 16:00:00 UTC,Sell,USDC,100,CAD,3,300,300,0,"
             "Sold 100 USDC for 300 CAD\n")

    def _net(self, files):
        evs = []
        with tempfile.TemporaryDirectory() as td:
            for name, body in files.items():
                evs += _events(td, name, body)[1]
        kept = CE.dedup_across_files(evs)
        v = _ledger(kept, _lines("CASHOPEN 2025-01-01 USD 0 0",
                                 "CASHBAL 2025-12-31 USD 0"),
                    lambda c, d: 2.0)
        self.assertEqual(v["status"], "computed", v.get("problems"))
        return kept, v["net_gain"]

    @rule("CA-FX-07")
    def test_distinct_ids_in_two_files_are_two_round_trips(self):
        joined = {"all.csv": ("CB-A1" + self._BUY + "CB-B1" + self._SELL
                              + "CB-A2" + self._BUY + "CB-B2"
                              + self._SELL)}
        split = {"a.csv": "CB-A1" + self._BUY + "CB-B1" + self._SELL,
                 "b.csv": "CB-A2" + self._BUY + "CB-B2" + self._SELL}
        _k, g_joined = self._net(joined)
        kept, g_split = self._net(split)
        self.assertEqual(len(kept), 4)
        self.assertAlmostEqual(g_joined, 400.0, places=2)
        self.assertAlmostEqual(g_split, 400.0, places=2)

    def test_same_ids_in_two_files_count_once(self):
        same = {"a.csv": "CB-A1" + self._BUY + "CB-B1" + self._SELL,
                "b.csv": "CB-A1" + self._BUY + "CB-B1" + self._SELL}
        kept, g = self._net(same)
        self.assertEqual(len(kept), 2)
        self.assertAlmostEqual(g, 200.0, places=2)

    def test_an_id_less_copy_beside_an_id_export_counts_once(self):
        # An older export without IDs overlapping a newer one with them:
        # one event, as before.
        with tempfile.TemporaryDirectory() as td:
            evs = _events(td, "a.csv",
                          "CB-A1" + self._BUY + "CB-B1" + self._SELL)[1]
            p = Path(td) / "old.csv"
            p.write_text(
                "Timestamp,Transaction Type,Asset,Quantity Transacted,"
                "Price Currency,Price at Transaction,Subtotal,"
                "Total (inclusive of Fees and/or Spread),"
                "Fees and/or Spread,Notes\n"
                + self._BUY.lstrip(",") + self._SELL.lstrip(","))
            old = coinbase_cash_events(p)
            for e in old:
                e.update(label="coins", book=_BOOK, broker="coinbase",
                         file="old.csv")
        kept = CE.dedup_across_files(evs + old)
        self.assertEqual(len(kept), 2)

    def test_two_id_less_identical_rows_in_one_file_are_two(self):
        body = (self._BUY.lstrip(",") * 2)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "old.csv"
            p.write_text(
                "Timestamp,Transaction Type,Asset,Quantity Transacted,"
                "Price Currency,Price at Transaction,Subtotal,"
                "Total (inclusive of Fees and/or Spread),"
                "Fees and/or Spread,Notes\n" + body)
            evs = coinbase_cash_events(p)
        for e in evs:
            e.update(label="coins", book=_BOOK, file="old.csv")
        self.assertEqual(len(CE.dedup_across_files(evs)), 2)


class TestSubscriptionFromCash(unittest.TestCase):
    """TJ-0870: a Coinbase One subscription paid from US-dollar cash
    spends that cash in the ledger."""

    @rule("CA-FX-07")
    def test_usd_subscription_spends_usd(self):
        body = ("CB-SUB,2025-06-02 16:00:00 UTC,Subscription,USD,-30,USD,1,"
                "-30,-30,0,Coinbase One subscription\n")
        with tempfile.TemporaryDirectory() as td:
            _p, evs = _events(td, "coinbase.csv", body)
        self.assertEqual([(e["kind"], e["currency"], e["amount"])
                          for e in evs], [("FLOW", "USD", -30.0)])
        v = _ledger(evs, _lines("CASHOPEN 2025-01-01 USD 30 30",
                                "CASHBAL 2025-12-31 USD 0"),
                    lambda c, d: 1.5)
        self.assertEqual(v["status"], "computed", v.get("problems"))

    def test_stablecoin_subscription_in_cash_mode(self):
        body = ("CB-SUB,2025-06-02 16:00:00 UTC,Subscription,USDC,30,USD,1,"
                "30,30,0,Coinbase One subscription\n")
        with tempfile.TemporaryDirectory() as td:
            p, evs = _events(td, "coinbase.csv", body)
            self.assertEqual([(e["kind"], e["currency"], e["amount"])
                              for e in evs], [("FLOW", "USD", -30.0)])
            self.assertEqual(coinbase_cash_events(p, stable_cash=False), [])


class TestPriceCandleMatchesTheDay(unittest.TestCase):
    """TJ-0744: fill-crypto takes the close of the asked asset, in USD,
    on the asked UTC day, never the first positive close of whatever
    the provider answered."""

    @staticmethod
    def _stamp(day):
        import calendar
        import time
        return calendar.timegm(time.strptime(day, "%Y-%m-%d"))

    def _price(self, meta, days, closes, with_stamps=True):
        import json
        from unittest import mock
        from taxjson.bin import fill_crypto_prices as F
        res = {"meta": meta, "indicators": {"quote": [{"close": closes}]}}
        if with_stamps:
            res["timestamp"] = [self._stamp(d) for d in days]
        body = json.dumps({"chart": {"result": [res], "error": None}})

        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch.object(F.urllib.request, "urlopen",
                                  lambda req, timeout=15: io.BytesIO(body.encode())), \
                contextlib.redirect_stderr(err):
            return F.get_crypto_price("SYN", "2025-03-03"), err.getvalue()

    _USD = {"symbol": "SYN-USD", "currency": "USD"}

    def test_the_asked_days_close_not_the_first(self):
        p, err = self._price(self._USD, ["2025-03-02", "2025-03-03"],
                             [10, 20])
        self.assertEqual(p, 20.0)
        self.assertEqual(err, "")

    def test_wrong_day_only_is_unpriced(self):
        p, err = self._price(self._USD, ["2025-03-02"], [10])
        self.assertEqual(p, 0.0)
        self.assertIn("no usable close for SYN-USD on 2025-03-03", err)

    def test_other_currency_or_symbol_is_unpriced(self):
        p, err = self._price({"symbol": "SYN-EUR", "currency": "EUR"},
                             ["2025-03-03"], [20])
        self.assertEqual(p, 0.0)
        self.assertIn("SYN-EUR", err)
        p, err = self._price({"symbol": "SYN-USD", "currency": "EUR"},
                             ["2025-03-03"], [20])
        self.assertEqual(p, 0.0)
        self.assertIn("in EUR, not USD", err)

    def test_boolean_close_is_no_price(self):
        p, _err = self._price(self._USD, ["2025-03-03"], [True])
        self.assertEqual(p, 0.0)

    def test_a_single_candle_without_timestamps_still_reads(self):
        p, _err = self._price({}, [], [None, 20], with_stamps=False)
        self.assertEqual(p, 20.0)
        p, _err = self._price({}, [], [10, 20], with_stamps=False)
        self.assertEqual(p, 0.0)

    def test_a_refused_answer_is_not_cached(self):
        import json
        from unittest import mock
        from test_fix_x_mut_fill_crypto import run
        from taxjson.bin import fill_crypto_prices as F
        row = {"action": "BUYSELL", "date": "2025-03-03", "symbol": "SYN",
               "quantity": 2.0, "price": 0.0, "net_amount": 0.0,
               "currency": "USD"}
        body = json.dumps({"chart": {"result": [{
            "meta": self._USD, "timestamp": [self._stamp("2025-03-02")],
            "indicators": {"quote": [{"close": [10]}]}}]}}).encode()
        real = F.get_crypto_price
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch.object(F.urllib.request, "urlopen",
                                  lambda req, timeout=15: io.BytesIO(body)):
            txs, cache, _err, _calls, _rc = run(td, [row], real)
        self.assertNotIn("SYN-2025-03-03", cache)
        self.assertEqual(txs[0]["net_amount"], 0.0)

def _parse_sidecar(sidecar, *csvs):
    """taxjson-brokerage over `csvs` (Coinbase, Canada) writing the
    transfer sidecar `sidecar`."""
    import subprocess
    from taxjson.lib.dispatch import python_module_argv
    env = dict(os.environ, TAXJSON_WIDTH="0", TAXJSON_OFFLINE="1")
    r = subprocess.run(python_module_argv(
        "taxjson.bin.taxjson_brokerage",
        ["--brokerage", "coinbase", "--country", "canada", "--account",
         "coin", "--transfers-out", str(sidecar), *map(str, csvs)]),
        capture_output=True, text=True, env=env)
    if r.returncode:
        raise AssertionError(r.stderr)


class TestRemovedExportIsStale(unittest.TestCase):
    """TJ-0671: an export removed since the last parse makes the
    transfer evidence stale, as an edited one does."""

    def test_removed_export_is_named(self):
        from taxjson.lib.crypto_sends import stale_evidence
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            d = root / "inputs" / "coin"
            d.mkdir(parents=True)
            a, b = d / "history.csv", d / "sends.csv"
            a.write_text(_HDR + "BUY901,2025-01-02 17:00:00 UTC,Buy,ZZE,2,"
                         "CAD,10,20,20,0,Bought 2 ZZE\n")
            b.write_text(_HDR + "SEND901,2025-03-03 17:00:00 UTC,Send,ZZE,"
                         "1,CAD,20,20,20,0,Sent 1 ZZE\n")
            side = work / "coin_coinbase_transfers.json"
            _parse_sidecar(side, a, b)
            both = {"coin": [("coinbase", a), ("coinbase", b)]}
            self.assertEqual(stale_evidence(work, both), [])
            b.unlink()
            only_a = {"coin": [("coinbase", a)]}
            self.assertEqual(stale_evidence(work, only_a),
                             ["coin (sends.csv removed since the last "
                              "parse)"])
            _parse_sidecar(side, a)
            self.assertEqual(stale_evidence(work, only_a), [])
            # An export added with an old mtime (a copy keeping its
            # stamp) is new evidence too.
            b.write_text(_HDR)
            os.utime(b, (1, 1))
            self.assertEqual(stale_evidence(work, both),
                             ["coin (sends.csv added since the last "
                              "parse)"])

    def test_an_older_sidecar_without_the_list_is_read_as_before(self):
        import json
        from taxjson.lib.crypto_sends import stale_evidence
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            a = work / "a.csv"
            a.write_text(_HDR)
            os.utime(a, (1, 1))
            (work / "coin_coinbase_transfers.json").write_text(json.dumps(
                {"transactions": [],
                 "metadata": {"kind": "transfer_sidecar"}}))
            self.assertEqual(stale_evidence(work,
                                            {"coin": [("coinbase", a)]}), [])


def _send(day, qty, sym="USDC", acct="a"):
    return {"date": day, "time": "12:00:00", "symbol": sym,
            "quantity": qty, "fee": 0, "exchange": "coinbase",
            "account": acct, "currency": "USD", "price": 1}


class TestParkedReturnIsTheSameCoin(unittest.TestCase):
    """TJ-0652: coins a `self` send parked come back only as the same
    coin; another stablecoin arriving is new money, and each arrival
    taken as a return is listed."""

    def _pool(self, arrival_sym):
        from datetime import datetime
        from taxjson.lib import crypto_sends as CS
        flows = [{"dt": datetime(2025, 1, 10, 12), "units": 100.0,
                  "cad": -100.0, "what": "buy", "key": None}]
        out = _send("2025-01-20", -100.0)
        arrive = _send("2025-02-10", 100.0, sym=arrival_sym, acct="b")
        gift = _send("2025-03-01", -100.0, sym=arrival_sym, acct="b")
        rates = CS.Rates({"USD": {d: (r, "boc") for d, r in
                                  {"2025-01-20": 1.0, "2025-02-10": 2.0,
                                   "2025-03-01": 2.0}.items()}}, "CAD")
        return CS.usd_pool(flows, [out, arrive, gift], [],
                           {id(out): "self1", id(gift): "gift1"}, rates,
                           kept={id(out)})

    @rule("CA-CRYPTO-08")
    def test_another_stablecoin_is_new_money(self):
        r = self._pool("USDT")
        g = r["results"]["gift1"]
        self.assertEqual((g["acb"], g["gain"]), (150.0, 50.0))
        self.assertEqual(r["units_now"], 100.0)
        self.assertEqual(r["returns"], [])

    @rule("CA-CRYPTO-08")
    def test_the_same_coin_comes_back_and_is_listed(self):
        r = self._pool("USDC")
        g = r["results"]["gift1"]
        self.assertEqual((g["acb"], g["gain"]), (100.0, 100.0))
        self.assertEqual(r["returns"],
                         [{"date": "2025-02-10", "account": "b",
                           "exchange": "coinbase", "symbol": "USDC",
                           "units": 100.0}])


_KL = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
       "balance,amountusd,feeusd,feecurrency\n")
_SPEND = ("L1,R1,2025-12-31 23:59:59,spend,,currency,CAD,spot,-150,0,0,"
          "100,0,CAD\n")
_RECV = ("L2,R1,2026-01-01 00:00:01,receive,,currency,USDC,spot,100,0,"
         "100,100,0,USDC\n")


class TestKrakenLegsAcrossExports(unittest.TestCase):
    """TJ-0667: a Kraken conversion whose CAD spend and USDC receipt sit
    in two yearly ledgers keeps its exact CAD cost in the pool."""

    def _gift(self, bodies):
        from taxjson.lib import crypto_sends as CS
        rates = CS.Rates({"USD": {"2025-12-31": (1.25, "boc"),
                                  "2026-02-02": (2.0, "boc")}}, "CAD")
        send = dict(account="k", exchange="kraken", date="2026-02-02",
                    time="10:00:00", symbol="USDC", quantity=-100.0,
                    fee=0.0, price=0.0, currency="USD", kind="withdrawal")
        with tempfile.TemporaryDirectory() as td:
            files = []
            for n, body in enumerate(bodies):
                f = Path(td) / f"kr_{n}.csv"
                f.write_text(_KL + body)
                files.append(("kraken", f))
            flows = CS.account_pool_flows({"k": files}, ["k"])
        res = CS.usd_pool(flows, [send], [], {id(send): "gift"}, rates)
        return flows, res["results"]["gift"]

    @rule("CA-CRYPTO-08")
    def test_split_exports_keep_the_cad_cost(self):
        for bodies in ([_SPEND + _RECV], [_SPEND, _RECV], [_RECV, _SPEND],
                       # an overlapping copy of either adds nothing
                       [_SPEND + _RECV, _RECV], [_SPEND, _SPEND + _RECV]):
            flows, g = self._gift(bodies)
            self.assertEqual([(f["units"], f["cad"]) for f in flows],
                             [(100.0, -150.0)], bodies)
            self.assertEqual((g["acb"], g["gain"]), (150.0, 50.0), bodies)

    def test_rows_without_txid_follow_the_file_holding_most(self):
        from taxjson.lib import crypto_sends as CS
        row = (",,2025-03-01 12:00:00,earn,reward,currency,USDC,spot,5,0,"
               "5,5,0,USDC\n")
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.csv", Path(td) / "b.csv"
            a.write_text(_KL + row + row)
            b.write_text(_KL + row)
            flows = CS.kraken_account_flows([a, b])
        self.assertEqual([f["units"] for f in flows], [5.0, 5.0])


class TestSendIdsFollowBrokerIds(unittest.TestCase):
    """TJ-0663: two sends alike to the second keep their send ids (and
    so their saved decisions) when the export lists them in the other
    order."""

    _ROWS = ["AAA-SEND-901,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,10,10,"
             "10,0,Sent 1 ZZE\n",
             "BBB-SEND-902,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,11,11,"
             "11,0,Sent 1 ZZE\n"]

    def _sids(self, rows):
        from taxjson.lib.crypto_sends import (assign_send_ids,
                                              load_transfer_rows)
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"
            work.mkdir()
            f = Path(td) / "coinbase.csv"
            f.write_text(_HDR + "".join(rows))
            _parse_sidecar(work / "coin_coinbase_transfers.json", f)
            out = load_transfer_rows(work, ["coin"])
        assign_send_ids(out)
        return {r["ref"]: (r["sid"], r["price"]) for r in out}

    def test_reversed_export_keeps_each_send_id(self):
        fwd = self._sids(self._ROWS)
        rev = self._sids(list(reversed(self._ROWS)))
        self.assertEqual(fwd, rev)
        self.assertEqual(sorted(sid for sid, _p in fwd.values()),
                         ["cb-20250303T120000-ZZE-1",
                          "cb-20250303T120000-ZZE-1-2"])

    def test_the_raw_id_is_not_kept(self):
        from taxjson.lib.crypto_sends import _id_key
        self.assertNotIn("SEND", _id_key("AAA-SEND-901-xfer"))
        self.assertEqual(_id_key(""), "")


class TestRepeatedHourKeepsOrder(unittest.TestCase):
    """TJ-0747: a sale at 05:50 UTC and a rebuy at 06:10 UTC on the day
    Toronto's clocks fall back (01:50 EDT, then 01:10 EST) stay in that
    order: the sale realizes its gain as on any other day."""

    def _gain(self, day):
        import dataclasses
        from taxjson.lib.core import TaxTransaction as T
        from taxjson.lib.pipeline import GainsRequest, run_gains
        body = ("C1,2025-01-06 15:00:00 UTC,Buy,ZZE,10,CAD,5,50,50,0,"
                "Bought 10 ZZE\n"
                f"C2,{day} 05:50:00 UTC,Sell,ZZE,10,CAD,10,100,100,0,"
                "Sold 10 ZZE\n"
                f"C3,{day} 06:10:00 UTC,Buy,ZZE,10,CAD,10.01,100.1,100.1,"
                "0,Bought 10 ZZE\n")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.csv"
            p.write_text(_HDR + body)
            with contextlib.redirect_stderr(io.StringIO()):
                rows = CoinbaseBrokerage().parse_file(p)
        fields = {f.name for f in dataclasses.fields(T)}
        txs = [T(**{k: v for k, v in r.items() if k in fields})
               for r in rows]
        with contextlib.redirect_stderr(io.StringIO()):
            res = run_gains(txs, req=GainsRequest(
                country="canada", year=2025, taxable=True,
                phantom_hint=False))
        times = {r["id"]: r["time"] for r in rows}
        return res["summary"]["total_gain"], times

    @rule("CA-DATE-12")
    def test_sale_before_rebuy_across_the_clock_change(self):
        g_normal, _t = self._gain("2025-11-01")
        g_fold, times = self._gain("2025-11-02")
        self.assertAlmostEqual(g_normal, 50.0, places=2)
        self.assertAlmostEqual(g_fold, 50.0, places=2)
        self.assertLess(times["C2"], times["C3"])

    def test_times_around_the_repeated_hour(self):
        from datetime import datetime
        from taxjson.lib.brokerages import _crypto_common as cc
        got = [cc.utc_to_local(datetime(2025, 11, 2, h, m),
                               "America/Toronto").strftime("%H:%M:%S")
               for h, m in ((4, 59), (5, 0), (5, 50), (6, 0), (6, 10),
                            (6, 59), (7, 0))]
        self.assertEqual(got, ["00:59:00", "01:00:00", "01:25:00",
                               "01:30:00", "01:35:00", "01:59:30",
                               "02:00:00"])
        self.assertEqual(got, sorted(got))
        # Any other hour is its wall-clock time.
        self.assertEqual(cc.utc_to_local(datetime(2025, 7, 1, 3, 0),
                                         "America/Toronto"),
                         datetime(2025, 6, 30, 23, 0))

    def test_the_no_tz_database_fallback_agrees(self):
        from datetime import datetime, timedelta
        from taxjson.lib.brokerages import _crypto_common as cc
        for y in (2006, 2025):
            for h in range(3, 9):
                for m in (0, 1, 29, 30, 59):
                    d = (datetime(y, 10, 29) if y < 2007
                         else datetime(y, 11, 2)) + timedelta(hours=h,
                                                              minutes=m)
                    self.assertEqual(cc._eastern_local(d),
                                     cc.utc_to_local(d, "America/Toronto"),
                                     d)


if __name__ == "__main__":
    unittest.main()
