"""Fix round 7 (wave B), group "coinbase": the Coinbase readers and the
crypto send / price helpers (backlog TJ-0865, TJ-0870, TJ-0916,
TJ-0747, TJ-0652, TJ-0663, TJ-0667, TJ-0671, TJ-0744).

All rows, ids and amounts are synthetic.

Formerly: test_fix_1029_r7b_coinbase
"""
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _send(day, qty, sym="USDC", acct="a"):
    return {"date": day, "time": "12:00:00", "symbol": sym,
            "quantity": qty, "fee": 0, "exchange": "coinbase",
            "account": acct, "currency": "USD", "price": 1}


class TestParkedReturnIsTheSameCoin(unittest.TestCase):
    """TJ-0652: coins a `self` send parked come back only as the same
    coin; another stablecoin arriving is new money, and each arrival
    taken as a return is listed.

    Formerly: test_fix_1029_r7b_coinbase.TestParkedReturnIsTheSameCoin"""

    def _pool(self, arrival_sym):
        from datetime import datetime
        from taxjson.lib import crypto_sends as CS
        from taxjson.lib import base_rates as BR
        flows = [{"dt": datetime(2025, 1, 10, 12), "units": 100.0,
                  "cad": -100.0, "what": "buy", "key": None}]
        out = _send("2025-01-20", -100.0)
        arrive = _send("2025-02-10", 100.0, sym=arrival_sym, acct="b")
        gift = _send("2025-03-01", -100.0, sym=arrival_sym, acct="b")
        rates = BR.Rates({"USD": {d: (r, "boc") for d, r in
                                  {"2025-01-20": 1.0, "2025-02-10": 2.0,
                                   "2025-03-01": 2.0}.items()}}, "CAD")
        return CS.usd_pool(flows, [out, arrive, gift], [],
                           {id(out): "self1", id(gift): "gift1"}, rates,
                           kept={id(out)})

    @rule(R.CA_CRYPTO_08)
    def test_another_stablecoin_is_new_money(self):
        r = self._pool("USDT")
        g = r["results"]["gift1"]
        self.assertEqual((g["acb"], g["gain"]), (150.0, 50.0))
        self.assertEqual(r["units_now"], 100.0)
        self.assertEqual(r["returns"], [])

    @rule(R.CA_CRYPTO_08)
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
    in two yearly ledgers keeps its exact CAD cost in the pool.

    Formerly: test_fix_1029_r7b_coinbase.TestKrakenLegsAcrossExports"""

    def _gift(self, bodies):
        from taxjson.lib import crypto_sends as CS
        from taxjson.lib import base_rates as BR
        rates = BR.Rates({"USD": {"2025-12-31": (1.25, "boc"),
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

    @rule(R.CA_CRYPTO_08)
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


if __name__ == "__main__":
    unittest.main()
