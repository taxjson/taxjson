"""Fixes of 2026-10-29, round 2, group fxcrypto: the FX-on-cash ledger
v2 (lib/fx_cash_v2, lib/cash_events, `taxjson cash`) and crypto-sends'
USD-cash / stablecoin pool (lib/crypto_sends). One test class per
backlog item, each named for it. Synthetic data only."""
import os
from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import fx_cash_v2 as FX

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,Total (inclusive "
             "of fees and/or spread),Fees and/or Spread,Notes\n")
BOOK = "a/coinbase"


def _cash(cur, units, cost, stable_cash, book=BOOK, year=2025):
    """An opening of `units` at `cost` and a year-end statement balance
    of the same units: the account's cash does not move in the year."""
    return {"stablecoins_as_cash": stable_cash,
            "lines": [{"kind": "CASHOPEN", "origin": "tt", "book": book,
                       "date": f"{year}-01-01", "currency": cur,
                       "units": units, "cost": cost,
                       "where": "cash.tt:1"}],
            "events": [{"kind": "CASHBAL", "book": book,
                        "date": f"{year}-12-31", "currency": cur,
                        "balance": units, "where": "cash.tt:2"}]}


class TestTJ0493UsStablecoinRewardMovesNoForeignCash(unittest.TestCase):
    """TJ-0493: a US project's Coinbase reward of 100 USDC valued at 80
    EUR (its income and its acquisition rows) was read as 80 EUR coming
    in and going out: an 11.11 USD gain on a EUR pool that never moved.
    A US stablecoin is property (US-FX-03): a reward in one moves no
    cash."""

    def _rows(self, stable_cash):
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        old = os.environ.get("TAXJSON_LOCAL_TZ")
        os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
        try:
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "cb_2025.csv"
                p.write_text("Transactions\n" + CB_HEADER
                             + "r1,2025-06-01 12:00:00 UTC,Staking Income,"
                               "USDC,100,EUR,0.8,80,80,0,Reward\n")
                parser = CoinbaseBrokerage()
                parser.stablecoins_as_cash = stable_cash
                rows = parser.parse_file(p)
        finally:
            if old is None:
                os.environ.pop("TAXJSON_LOCAL_TZ", None)
            else:
                os.environ["TAXJSON_LOCAL_TZ"] = old
        for r in rows:
            r["_book"] = BOOK
        return rows

    @rule("US-FX-03")
    def test_us_reward_priced_in_eur_leaves_the_eur_pool_alone(self):
        rows = self._rows(stable_cash=False)
        self.assertTrue(rows)
        doc = FX.build(rows, _cash("EUR", 100.0, 100.0, False), "USD",
                       2025, lambda cur, d: 1.25, country="usa")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 0.0)
        self.assertEqual(doc["events"], [])
        self.assertEqual(doc["pools_year_end"],
                         {"EUR": {"units": 100.0, "acb": 100.0}})

    @rule("US-FX-03")
    def test_us_policy_follows_the_country_when_the_cash_says_none(self):
        rows = self._rows(stable_cash=False)
        cash = _cash("EUR", 100.0, 100.0, False)
        del cash["stablecoins_as_cash"]
        doc = FX.build(rows, cash, "USD", 2025, lambda cur, d: 1.25,
                       country="usa")
        self.assertEqual((doc["status"], doc["net_gain"]),
                         ("computed", 0.0))

    def test_flows_helper_keeps_canadas_stablecoin_cash(self):
        from taxjson.bin import taxjson_fx_cash as F
        row = {"action": "DIVIDEND", "symbol": "USDC", "currency": "EUR",
               "net_amount": 80.0, "gross_amount": 80.0,
               "description": "Staking Reward"}
        self.assertEqual(F._cash_legs(row), [("EUR", 80.0)])
        self.assertEqual(F._cash_legs(row, stable_cash=False), [])


class TestTJ0494InKindMovesNoCash(unittest.TestCase):
    """TJ-0494: the rows `taxjson run` books for securities moved in kind
    (into or out of a plan at fair value, an arrival at book value) are
    BUYSELL rows with a structured type and no cash leg; ledger v2 read
    them as trades: a contribution made up 500 USD of cash, a withdrawal
    or an arrival a 400 USD debt, and an account whose cash never moved
    was refused."""

    @rule("CA-FX-07")
    def test_security_only_rows_leave_the_cash_alone(self):
        from taxjson.lib.core import (IN_KIND_CONTRIBUTION_TYPE,
                                      IN_KIND_DISPOSITION_TYPE,
                                      IN_KIND_WITHDRAWAL_TYPE,
                                      TRANSFER_BOOK_VALUE_TYPE)
        for typ, qty in ((IN_KIND_CONTRIBUTION_TYPE, -100),
                         (IN_KIND_DISPOSITION_TYPE, -100),
                         (IN_KIND_WITHDRAWAL_TYPE, 100),
                         (TRANSFER_BOOK_VALUE_TYPE, 100)):
            row = {"action": "BUYSELL", "type": typ, "date": "2025-06-02",
                   "date_settle": "2025-06-02", "symbol": "ZZQ.US",
                   "quantity": qty, "currency": "USD", "net_amount": 500.0,
                   "gross_amount": 500.0,
                   "description": "IN-KIND synthetic shares moved",
                   "account": "a", "_book": "a/ib"}
            doc = FX.build([row], _cash("USD", 100.0, 125.0, True,
                                        book="a/ib"),
                           "CAD", 2025, lambda cur, d: 1.25,
                           country="canada")
            self.assertEqual(doc["status"], "computed",
                             (typ, doc["problems"]))
            self.assertEqual(doc["net_gain"], 0.0, typ)
            self.assertEqual(doc["borrowed"], [], typ)
            self.assertEqual(doc["pools_year_end"],
                             {"USD": {"units": 100.0, "acb": 125.0}}, typ)

    @rule("US-FX-03")
    def test_us_in_kind_withdrawal_in_euros_moves_no_euros(self):
        from taxjson.lib.core import IN_KIND_WITHDRAWAL_TYPE
        row = {"action": "BUYSELL", "type": IN_KIND_WITHDRAWAL_TYPE,
               "date": "2025-06-02", "date_settle": "2025-06-02",
               "symbol": "ZZQ.DE", "quantity": 100, "currency": "EUR",
               "net_amount": 500.0, "gross_amount": 500.0,
               "description": "IN-KIND synthetic shares moved",
               "account": "a", "_book": "a/ib"}
        doc = FX.build([row], _cash("EUR", 100.0, 110.0, False,
                                    book="a/ib"),
                       "USD", 2025, lambda cur, d: 1.1, country="usa")
        self.assertEqual((doc["status"], doc["net_gain"], doc["borrowed"]),
                         ("computed", 0.0, []), doc["problems"])

    def test_every_type_the_in_kind_rows_take_is_one(self):
        from taxjson.bin import taxjson_fx_cash as F
        from taxjson.lib import in_kind as IK
        for kind in (IK.CONTRIBUTION, IK.WITHDRAWAL):
            for plan in ("rrsp", "resp"):
                for country in ("canada", "usa"):
                    m = type("M", (), {"kind": kind, "plan": plan})()
                    t = IK.row_type(m, country)
                    self.assertIn(t, F._SECURITY_ONLY_TYPES, (kind, plan))

    def test_a_trade_for_cash_still_moves_it(self):
        from taxjson.bin import taxjson_fx_cash as F
        row = {"action": "BUYSELL", "type": "", "quantity": 10,
               "currency": "USD", "net_amount": 500.0,
               "description": "BUY ZZQ"}
        self.assertEqual(F._cash_legs(row), [("USD", -500.0)])


IB_FOREX = """Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers (Synthetic)
Statement,Data,Title,Activity Statement
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,U99900011
Account Information,Data,Base Currency,CAD
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm in CAD,MTM in CAD,Code
Trades,Data,Order,Forex,CAD,USD.CAD,"2024-01-03, 10:00:00","{qty}",1.35,1.35,"{proceeds}",-2,0,
Deposits & Withdrawals,Header,Currency,Account,Settle Date,Description,Amount
Deposits & Withdrawals,Data,CAD,U99900011,2024-01-02,Electronic Fund Transfer,13502
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Starting Cash,CAD,0,0,0,
Cash Report,Data,Ending Cash,CAD,0.00,0.00,0,
Cash Report,Data,Starting Cash,USD,0,0,0,
Cash Report,Data,Ending Cash,USD,{usd_end},{usd_end},0,
"""


class TestTJ0456ConversionCommission(unittest.TestCase):
    """TJ-0456: IB charged 2.00 CAD on the conversion of 13,500 CAD to
    10,000 USD; ledger v2 dropped the commission (a flow in the base
    currency) and costed the USD at 13,500, so spending it at 1.3582
    gained 82.00 instead of 80.00. A conversion is at the amount
    actually paid (CA-FX-07), its commission included."""

    def _events(self, qty="10,000", proceeds="-13,500", usd_end="0.00"):
        from taxjson.lib.brokerages.ib_extractor import ib_cash_events
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.csv"
            p.write_text(IB_FOREX.format(qty=qty, proceeds=proceeds,
                                         usd_end=usd_end))
            evs = ib_cash_events(p)
        for e in evs:
            e["book"] = "margin/ib"
            e["label"] = "margin"
        return evs

    def _spend(self, units):
        return {"action": "BUYSELL", "type": "", "date": "2024-03-01",
                "date_settle": "2024-03-05", "symbol": "ZZB.US",
                "quantity": 100, "currency": "USD",
                "net_amount": float(units), "gross_amount": float(units),
                "description": "BUY ZZB", "account": "margin",
                "_book": "margin/ib"}

    @rule("CA-FX-07")
    def test_buying_usd_costs_the_commission_too(self):
        evs = self._events()
        fees = [e for e in evs if e["kind"] == "FLOW"]
        self.assertEqual(len(fees), 1)
        rates = {"2024-01-03": 1.35, "2024-03-05": 1.3582}
        doc = FX.build([self._spend(10000)],
                       {"events": evs, "lines": [],
                        "stablecoins_as_cash": True},
                       "CAD", 2024, lambda c, d: rates.get(d),
                       country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 80.0)
        self.assertEqual([e["gain"] for e in doc["events"]], [80.0])

    @rule("CA-FX-07")
    def test_selling_usd_fetches_the_amount_less_the_commission(self):
        # 10,000 USD held at a cost of 13,400 CAD, sold for 13,500 CAD
        # less the 2.00 CAD commission: a gain of 98.00.
        evs = self._events(qty="-10,000", proceeds="13,500")
        conv = [e for e in evs if e["kind"] == "FXCONV"]
        self.assertEqual((conv[0]["from_ccy"], conv[0]["to_ccy"]),
                         ("USD", "CAD"))
        evs = [e for e in evs if e["kind"] != "CASHMOVE"]
        cash = {"events": evs, "stablecoins_as_cash": True,
                "lines": [{"kind": "CASHOPEN", "origin": "tt",
                           "book": "margin/ib", "date": "2024-01-01",
                           "currency": "USD", "units": 10000.0,
                           "cost": 13400.0, "where": "cash.tt:1"}]}
        # The statement opens with the 10,000 USD the CASHOPEN gives.
        for e in evs:
            if e["kind"] == "CASHBAL" and e["currency"] == "USD" \
                    and e["date"] == "2023-12-31":
                e["balance"] = 10000.0
        doc = FX.build([], cash, "CAD", 2024, lambda c, d: 1.35,
                       country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 98.0)

    @rule("CA-FX-07")
    def test_a_kraken_conversion_fee_in_cad_adds_to_the_cost(self):
        from taxjson.lib.brokerages.kraken import kraken_cash_events
        old = os.environ.get("TAXJSON_LOCAL_TZ")
        os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
        try:
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "kr_ledgers.csv"
                p.write_text(
                    '"txid","refid","time","type","subtype","aclass",'
                    '"asset","wallet","amount","fee","balance"\n'
                    '"LZ01","FZ01","2024-01-02 15:00:00","deposit","",'
                    '"currency","ZCAD","spot / main","1352.0000","0.0000",'
                    '"1352.0000"\n'
                    '"LZ02","TZ01","2024-01-03 15:00:00","trade","",'
                    '"currency","ZCAD","spot / main","-1350.0000",'
                    '"2.0000","0.0000"\n'
                    '"LZ03","TZ01","2024-01-03 15:00:00","trade","",'
                    '"currency","ZUSD","spot / main","1000.0000",'
                    '"0.0000","1000.0000"\n'
                    '"LZ04","TZ02","2024-03-05 15:00:00","trade","",'
                    '"currency","ZUSD","spot / main","-1000.0000",'
                    '"0.0000","0.0000"\n'
                    '"LZ05","TZ02","2024-03-05 15:00:00","trade","",'
                    '"currency","ZCAD","spot / main","1360.0000",'
                    '"0.0000","1360.0000"\n')
                evs = kraken_cash_events(p)
        finally:
            if old is None:
                os.environ.pop("TAXJSON_LOCAL_TZ", None)
            else:
                os.environ["TAXJSON_LOCAL_TZ"] = old
        for e in evs:
            e["book"] = "crypto/kraken"
            e["label"] = "crypto"
        doc = FX.build([], {"events": evs, "lines": [],
                            "stablecoins_as_cash": True},
                       "CAD", 2024, lambda c, d: 1.35, country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        # 1,000 USD cost 1,352 CAD (1,350 + the 2 fee), sold for 1,360.
        self.assertEqual(doc["net_gain"], 8.0)


def _tz(fn):
    """Run `fn` with crypto rows dated in Toronto (local_timezone)."""
    def wrapped(*a, **k):
        old = os.environ.get("TAXJSON_LOCAL_TZ")
        os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
        try:
            return fn(*a, **k)
        finally:
            if old is None:
                os.environ.pop("TAXJSON_LOCAL_TZ", None)
            else:
                os.environ["TAXJSON_LOCAL_TZ"] = old
    return wrapped


def _usdc_send(day, qty, exchange="coinbase", acct="a"):
    return {"date": day, "time": "12:00:00", "symbol": "USDC",
            "quantity": -qty, "fee": 0, "exchange": exchange,
            "account": acct, "currency": "USD", "price": 1}


def _rates(table):
    from taxjson.lib import crypto_sends as CS
    return CS.Rates({"USD": {d: (r, "boc") for d, r in table.items()}},
                    "CAD")


class TestTJ0499CoinbaseUsdIsInThePool(unittest.TestCase):
    """TJ-0499: the crypto-sends pool read only a Coinbase export's
    stablecoins: 100 US dollars deposited were left out, and 100 USDC
    bought with them came in as new units at the day's rate (2 CAD), so
    the pool cost 350 for 200 units and a payment of 100 USDC gained 25
    instead of 75. US dollars and stablecoins are one pool
    (CA-CRYPTO-08)."""

    @_tz
    def _flows(self, rows):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.csv"
            p.write_text("Transactions\n" + CB_HEADER + rows)
            return CS.coinbase_pool_flows(p)

    @rule("CA-CRYPTO-08")
    def test_usd_for_usdc_keeps_the_pooled_cost(self):
        from taxjson.lib import crypto_sends as CS
        flows = self._flows(
            "c1,2025-01-01 17:00:00 UTC,Buy,USDC,100,CAD,1.5,150,150,0,"
            "Bought 100 USDC\n"
            "c2,2025-01-10 17:00:00 UTC,Deposit,USD,100,USD,1,100,100,0,"
            "Deposited 100\n"
            "c3,2025-01-20 17:00:00 UTC,Buy,USDC,100,USD,1,100,100,0,"
            "Bought 100 USDC\n")
        s = _usdc_send("2025-01-21", 100)
        r = CS.usd_pool(flows, [s], [], {id(s): "pay"},
                        _rates({"2025-01-01": 1.5, "2025-01-10": 1.0,
                                "2025-01-20": 2.0, "2025-01-21": 2.0}))
        self.assertEqual(r["results"]["pay"]["acb"], 125.0)
        self.assertEqual(r["results"]["pay"]["gain"], 75.0)
        self.assertEqual((r["units_now"], r["overdrafts"]), (100.0, 0))

    def test_the_pool_holds_what_the_account_holds(self):
        # 7000 USD deposited, a coin bought for 6136.60 USD, a 2.50
        # USDC reward: 865.90 US dollars, as fx-cash ledger v2 counts
        # the account (a coin bought with USD pays those dollars).
        flows = self._flows(
            "c1,2024-02-28 15:00:00 UTC,Deposit,USD,7000.00,USD,1.00,"
            "7000.00,7000.00,0.00,Deposit from bank\n"
            "c2,2024-03-01 15:00:00 UTC,Buy,ZZC,0.10000000,USD,61000.00,"
            "6100.00,6136.60,36.60,Bought 0.1 ZZC for 6136.60 USD\n"
            "c3,2024-10-01 13:00:00 UTC,Rewards Income,USDC,2.50000000,"
            "USD,1.00,2.50,2.50,0.00,Received 2.50 USDC from USDC "
            "rewards\n"
            "c4,2024-10-02 13:00:00 UTC,Advanced Trade Sell,ZZC,0.01,USD,"
            "60000,600,597,3,Sold 0.01 ZZC for 597 USD on ZZC-USD at "
            "60000 USD/ZZC\n"
            "c5,2024-10-03 13:00:00 UTC,Advanced Trade Buy,ZZE,1,USD,"
            "100,100,100,0,Bought 1 ZZE for 0.002 ZZC on ZZE-ZZC at "
            "0.002 ZZC/ZZE\n"
            "c6,2024-10-04 13:00:00 UTC,Withdrawal,USD,500,USD,1,500,"
            "500,0,Withdrew 500\n"
            "c7,2024-10-05 13:00:00 UTC,Staking Income,ZZE,0.1,USD,10,"
            "1,1,0,Received 0.1 ZZE\n")
        self.assertAlmostEqual(sum(f["units"] for f in flows),
                               7000 - 6136.60 + 2.50 + 597 - 500, places=6)
        self.assertEqual([round(f["units"], 2) for f in flows],
                         [7000.0, -6136.6, 2.5, 597.0, -500.0])

    def test_a_usdc_sold_for_usd_moves_only_its_spread(self):
        flows = self._flows(
            "c1,2025-01-20 17:00:00 UTC,Sell,USDC,100,USD,1,100,99,1,"
            "Sold 100 USDC\n")
        self.assertEqual([f["units"] for f in flows], [-1.0])


KR_LEDGER_HEADER = ("txid,refid,time,type,subtype,aclass,asset,amount,fee,"
                    "balance,feecurrency\n")


class TestTJ0500KrakenRewardFeeInCad(unittest.TestCase):
    """TJ-0500: a Kraken reward of 100 USDC whose 1.00 fee was charged in
    CAD entered the crypto-sends pool at a cost of 1 CAD (the fee taken
    as the whole consideration), so paying the 100 USDC out at the same
    rate gained 124 CAD. The reward's units cost their value at the
    day's rate; the fee is a commission. A conversion's CAD fee still
    adds to its cost."""

    @_tz
    def _flows(self, rows):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers.csv"
            p.write_text(KR_LEDGER_HEADER + rows)
            return CS.kraken_pool_flows(p)

    @rule("CA-CRYPTO-08")
    def test_a_reward_costs_its_value_not_its_fee(self):
        from taxjson.lib import crypto_sends as CS
        flows = self._flows("LZ01,RZ01,2025-06-01 17:00:00,staking,,"
                            "currency,USDC,100,1,100,CAD\n")
        self.assertEqual([(f["units"], f["cad"]) for f in flows],
                         [(100.0, 0.0)])
        s = _usdc_send("2025-06-02", 100, exchange="kraken")
        r = CS.usd_pool(flows, [s], [], {id(s): "pay"},
                        _rates({"2025-06-01": 1.25, "2025-06-02": 1.25}))
        self.assertEqual((r["results"]["pay"]["acb"],
                          r["results"]["pay"]["gain"]), (125.0, 0.0))

    def test_a_conversion_fee_in_cad_is_its_cost(self):
        flows = self._flows(
            "LZ02,TZ02,2025-06-03 17:00:00,trade,,currency,ZCAD,-135,"
            "0.5,0,CAD\n"
            "LZ03,TZ02,2025-06-03 17:00:00,trade,,currency,USDC,100,0,"
            "100,USDC\n")
        self.assertEqual([(f["units"], f["cad"]) for f in flows],
                         [(100.0, -135.5)])


class TestTJ0501SelfSendStaysInThePool(unittest.TestCase):
    """TJ-0501: 100 USDC bought for 100 CAD, 50 sent to your own wallet
    (saved `self`), 100 more bought for 200 CAD, 50 paid out at USD/CAD
    2: the pool walk ignored the decision and disposed of the self send,
    so the payment's ACB was 83.33 (gain 16.67). The 50 are still yours:
    the pool is 200 units costing 300, the ACB 75 and the gain 25."""

    @_tz
    def _report(self, root, sends, decisions, rates):
        import json
        from taxjson.lib import crypto_sends as CS
        (root / "work").mkdir(parents=True)
        (root / "inputs" / "a").mkdir(parents=True)
        p = root / "inputs" / "a" / "cb.csv"
        p.write_text("Transactions\n" + CB_HEADER
                     + "a1,2025-06-01 17:00:00 UTC,Buy,USDC,100,CAD,1,100,"
                       "100,0,Bought 100\n"
                     + "a2,2025-06-03 17:00:00 UTC,Buy,USDC,100,CAD,2,200,"
                       "200,0,Bought 100\n")
        trans = []
        for day, q in sends:
            t = _usdc_send(day, abs(q)) if q < 0 else dict(
                _usdc_send(day, 0), quantity=q)
            t.update(action="TRANSFER", description="withdrawal")
            trans.append(t)
        (root / "work" / "a_coinbase_transfers.json").write_text(
            json.dumps({"metadata": {"kind": "transfer_sidecar",
                                     "brokerage": "coinbase"},
                        "transactions": trans}))
        man = {CS.send_id("coinbase", day, "12:00:00", "USDC", abs(q)):
               {"decision": d} for (day, q), d in zip(sends, decisions)
               if d}
        (root / "inputs" / "a" / "sends.json").write_text(
            json.dumps({"schema_version": 1, "sends": man}))
        (root / "work" / "to_base.csv").write_text("".join(
            f"{d} 00:00:00 USD CAD {r} boc\n" for d, r in rates.items()))
        cfg = {"settings": {"country": "canada", "base_currency": "CAD",
                            "year": 2025},
               "accounts": {"a": {"type": "taxable", "crypto": True}}}
        return CS.build_report(root, cfg, {"a": [("coinbase", p)]})

    @rule("CA-CRYPTO-08")
    def test_a_self_send_keeps_its_units_and_cost(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._report(Path(td),
                             [("2025-06-02", -50), ("2025-06-04", -50)],
                             ["self", "payment"],
                             {"2025-06-01": 1, "2025-06-02": 1,
                              "2025-06-03": 2, "2025-06-04": 2})
        sends = r["accounts"]["a"]["sends"]
        self.assertEqual([s["decision"] for s in sends],
                         ["self", "payment"])
        pay = sends[1]["fx"]
        self.assertEqual((pay["acb"], pay["gain"]), (75.0, 25.0))
        # The self send's figure is what it would be as a gift or
        # payment; the 150 units left are in the pool (50 elsewhere).
        self.assertEqual(sends[0]["fx"]["gain"], 0.0)
        self.assertEqual(r["pool"]["units_now"], 150.0)
        self.assertEqual(r["pool"]["overdrafts"], 0)

    @rule("CA-CRYPTO-08")
    def test_coins_coming_back_from_your_wallet_are_not_bought_again(self):
        from taxjson.lib import crypto_sends as CS
        flows = [{"dt": datetime(2025, 6, 1, 12),
                  "units": 100.0, "cad": -100.0, "what": "buy",
                  "key": None}]
        out = _usdc_send("2025-06-02", 50)
        back = dict(_usdc_send("2025-07-20", 0), quantity=50.0)
        rates = _rates({"2025-06-02": 1.0, "2025-07-20": 3.0})
        r = CS.usd_pool(flows, [out, back], [], {id(out): "own"}, rates,
                        kept={id(out)})
        # Still 100 units costing 100: the 50 came back, not bought.
        self.assertEqual((r["units_now"], r["avg_cost"]), (100.0, 1.0))
        # Undecided (not kept), the send leaves and the arrival is new.
        r = CS.usd_pool(flows, [out, back], [], {id(out): "own"}, rates)
        self.assertEqual((r["units_now"], r["avg_cost"]), (100.0, 2.0))


class TestTJ0502RatesToAnotherCurrency(unittest.TestCase):
    """TJ-0502: load_rates kept only a row's FROM currency and date, so a
    CAD project's send was priced with a rates file to EUR (100 units at
    1 USD: 90 "CAD", labelled a Bank of Canada USD/CAD rate). A rate to
    another currency than the base is refused, naming the line."""

    def _file(self, td, text):
        p = Path(td) / "to_base.csv"
        p.write_text(text)
        return p

    def test_a_file_to_another_currency_is_refused(self):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            p = self._file(td, "2025-06-02 00:00:00 USD EUR 0.9 boc\n")
            with self.assertRaisesRegex(ValueError, r"line 1.*to EUR.*CAD"):
                CS.load_rates(p, "CAD")
            # Read without a base, the table names its target and the
            # pricer refuses it.
            with self.assertRaisesRegex(ValueError, r"to EUR.*base "
                                        r"currency is CAD"):
                CS.Rates(CS.load_rates(p), "CAD")

    def test_a_mixed_file_is_refused(self):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            p = self._file(td, "2025-06-02 00:00:00 USD CAD 1.37 boc\n"
                               "2025-06-02 00:00:00 EUR USD 1.1 boc\n")
            with self.assertRaisesRegex(ValueError, r"line 2.*to USD"):
                CS.load_rates(p)

    def test_the_base_currency_file_still_prices(self):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            p = self._file(td, "2025-06-02 00:00:00 USD CAD 1.37 boc\n")
            r = CS.Rates(CS.load_rates(p, "CAD"), "CAD")
            self.assertEqual(r.get("USD", "2025-06-02")[0], 1.37)
            # An empty file (no source currencies) has no target.
            p.write_text("")
            CS.Rates(CS.load_rates(p, "CAD"), "CAD")
            CS.Rates(CS.load_rates(p), "CAD")

    def test_a_send_is_not_priced_with_a_foreign_rate(self):
        import json
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "a_coinbase_transfers.json").write_text(
                json.dumps({"metadata": {"kind": "transfer_sidecar",
                                         "brokerage": "coinbase"},
                            "transactions": [dict(
                                _usdc_send("2025-06-02", 100),
                                symbol="ZZC", action="TRANSFER")]}))
            (root / "work" / "to_base.csv").write_text(
                "2025-06-02 00:00:00 USD EUR 0.9 boc\n")
            cfg = {"settings": {"country": "canada",
                                "base_currency": "CAD", "year": 2025},
                   "accounts": {"a": {"type": "taxable", "crypto": True}}}
            with self.assertRaisesRegex(ValueError, "to EUR"):
                CS.build_report(root, cfg, None)


class TestTJ0457StablecoinGiftGainCountedOnce(unittest.TestCase):
    """TJ-0457: crypto-sends said a stablecoin gift's currency gain was in
    no taxjson figure ("add it by hand ... (not in fx-cash)") even under
    ledger v2, which reads the send as US dollars going out and, once it
    is declared (`CASHMOVE ... proceeds=`), counts the gain: following
    both counted it twice. Under v2 the listing gives each send's
    declaration and says fx-cash counts it; under v1 (which does not
    read it) the by-hand advice stays."""

    def _listing(self, ledger, details=False):
        import contextlib
        import io
        from taxjson.bin import taxjson_run as TR
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r = TestTJ0501SelfSendStaysInThePool._report(
                self, root, [("2025-06-02", -50), ("2025-06-04", -40)],
                ["self", "gift"],
                {"2025-06-01": 1, "2025-06-02": 1.37, "2025-06-03": 2,
                 "2025-06-04": 2})
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
                'year = 2025\nlocal_timezone = "America/Toronto"\n'
                + (f'fx_cash_ledger = "{ledger}"\n' if ledger else "")
                + '\n[accounts.a]\ntype = "taxable"\ncrypto = true\n')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                TR._print_crypto_sends(root, r, details)
        return " ".join(buf.getvalue().split())

    @rule("CA-CRYPTO-08")
    @rule("CA-FX-07")
    def test_under_v2_the_gain_is_declared_not_added(self):
        out = self._listing("v2")
        # The gift: 40 units at 2.00 = 80.00, its ACB 40 x 1.50 = 60.00.
        self.assertIn("= +20.00 CAD", out)
        self.assertIn("fx-cash v2: CASHMOVE 2025-06-04 USD -40.00 "
                      "proceeds=80.00", out)
        self.assertIn("fx-cash v2: CASHMOVE 2025-06-02 USD -50.00 kept",
                      out)
        self.assertNotIn("by hand", out)
        self.assertIn("fx-cash v2 counts it once each", out)
        out = self._listing("v2", details=True)
        self.assertIn("Do NOT add this total too", out)
        self.assertNotIn("add it by hand", out)

    def test_under_v1_it_is_added_by_hand(self):
        for ledger in (None, "v1"):
            out = self._listing(ledger)
            self.assertIn("add it by hand to the year's net FX gain", out)
            self.assertNotIn("CASHMOVE", out)


class TestTJ0458UnitsAreLabelledAsUnits(unittest.TestCase):
    """TJ-0458: fx-cash's ACQUIRED / DISPOSED columns hold units of the
    currency (10,000 US dollars) but were printed under "All amounts
    CAD", so a user read 10,000.00 as CAD (the CAD cost was 13,502). The
    columns say UNITS and the gains say CAD; --json keeps its fields."""

    def _v2_doc(self):
        t = TestTJ0456ConversionCommission()
        evs = t._events()
        rates = {"2024-01-03": 1.35, "2024-03-05": 1.3582}
        return FX.build([t._spend(10000)],
                        {"events": evs, "lines": [],
                         "stablecoins_as_cash": True},
                        "CAD", 2024, lambda c, d: rates.get(d),
                        country="canada")

    def test_ledger_v2(self):
        from taxjson.bin.taxjson_fx_cash import apply_jurisdiction
        doc = self._v2_doc()
        text = FX.render(doc, apply_jurisdiction(doc["net_gain"], "canada"),
                         "canada", width_=0)
        head = next(ln for ln in text.splitlines() if ln.startswith("CUR"))
        self.assertEqual(head.split(), ["CUR", "UNITS", "IN", "UNITS",
                                        "OUT", "GAIN(LOSS)", "CAD",
                                        "ON", "DEBT", "CAD"])
        self.assertNotIn("All amounts", text)
        self.assertIn("UNITS IN / OUT: units of each currency; the gains "
                      "in CAD.", text)
        # --json is unchanged: the same fields, the units as before.
        self.assertEqual(doc["per_currency"]["USD"]["acquired"], 10000.0)

    def test_ledger_v1(self):
        from taxjson.bin import taxjson_fx_cash as V1
        from test_fix_a2_fx_cash import _rate_of, _tx
        doc = V1.build_ledger(
            [_tx("DIVIDEND", "2026-01-10", "USD", 1000.0,
                 gross_amount=1000.0),
             _tx("BUYSELL", "2026-03-10", "USD", -300.0, qty=3.0)],
            "CAD", {}, 2026, rate_of=_rate_of)
        verdict = V1.apply_jurisdiction(doc["net_gain"], "canada")
        for render in (V1.render_brief, V1.render_report):
            text = render(doc, "CAD", 2026, "canada", verdict, width_=0)
            head = next(ln for ln in text.splitlines()
                        if ln.startswith("CUR"))
            self.assertEqual(head.split(), ["CUR", "UNITS", "IN", "UNITS",
                                            "OUT", "GAIN(LOSS)", "CAD"])
            self.assertNotIn("All amounts", text)


class TestTJ0460StablecoinsInTheUsdBalance(unittest.TestCase):
    """TJ-0460: a Coinbase account holding 863.40 US dollars and a 2.50
    USDC reward, its CASHBAL giving the 863.40 the statement shows: v2
    (stablecoins counted as US-dollar cash) found a 2.50 gap and blamed
    "a conversion, deposit or withdrawal the ledger does not see". The
    message now says the line must count the USD stablecoins too."""

    @_tz
    def _rows(self):
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.csv"
            p.write_text("Transactions\n" + CB_HEADER
                         + "r1,2024-10-01 13:00:00 UTC,Rewards Income,USDC,"
                           "2.50,USD,1.00,2.50,2.50,0.00,Received 2.50 "
                           "USDC\n")
            parser = CoinbaseBrokerage()
            parser.stablecoins_as_cash = True
            rows = parser.parse_file(p)
        for r in rows:
            r["_book"] = "crypto/coinbase"
        return rows

    def _doc(self, balance, book="crypto/coinbase", rows=None):
        cash = _cash("USD", 863.40, 1170.0, True, book=book, year=2024)
        cash["events"][0]["balance"] = balance
        cash["events"][0]["origin"] = "tt"
        return FX.build(self._rows() if rows is None else rows, cash,
                        "CAD", 2024, lambda c, d: 1.36, country="canada")

    @rule("CA-CRYPTO-02")
    def test_the_gap_message_names_the_stablecoins(self):
        doc = self._doc(863.40)
        self.assertEqual(doc["status"], "not_computed")
        text = " ".join(p["text"] for p in doc["problems"])
        self.assertIn("gap -2.50", text)
        self.assertIn("CASHBAL USD is its US dollars and its USDC", text)
        # Given with the USDC, it computes.
        self.assertEqual(self._doc(865.90)["status"], "computed")

    def test_no_stablecoin_note_outside_a_crypto_book(self):
        doc = self._doc(860.0, book="margin/ib", rows=[])
        text = " ".join(p["text"] for p in doc["problems"])
        self.assertIn("does not see", text)
        self.assertNotIn("stablecoins", text)


class TestTJ0461BaseCurrencyMoveNeedsNoDeclaration(unittest.TestCase):
    """TJ-0461: `tjs cash` showed an IB deposit of 13,502 CAD as
    `CASHMOVE 2024-01-02 CAD +13,502.00 ?`, asking for a declaration
    ledger v2 ignores (it skips moves in the base currency). Such a move
    is listed as a `#CASHMOVE` comment line; a foreign one keeps `?`."""

    def test_a_cad_deposit_asks_for_nothing(self):
        from taxjson.lib import cash_events as CE
        evs = TestTJ0456ConversionCommission()._events()
        dep = next(e for e in evs if e["kind"] == "CASHMOVE")
        self.assertEqual(dep["currency"], "CAD")
        self.assertEqual(CE.tt_line(dep, "CAD"),
                         "#CASHMOVE 2024-01-02 CAD +13,502.00")
        # Without the base (an older caller) it is as before.
        self.assertTrue(CE.tt_line(dep).endswith(f" {CE.UNDECLARED}"))
        usd = dict(dep, currency="USD")
        self.assertEqual(CE.tt_line(usd, "CAD"),
                         "CASHMOVE 2024-01-02 USD +13,502.00 ?")
        # A declared move shows its declaration whatever its currency.
        kept = dict(dep, decl={"how": "kept"})
        self.assertEqual(CE.tt_line(kept, "CAD"),
                         "CASHMOVE 2024-01-02 CAD +13,502.00 kept")
        # Ledger v2 computes with it undeclared.
        rates = {"2024-01-03": 1.35, "2024-03-05": 1.3582}
        doc = FX.build([TestTJ0456ConversionCommission()._spend(10000)],
                       {"events": evs, "lines": [],
                        "stablecoins_as_cash": True},
                       "CAD", 2024, lambda c, d: rates.get(d),
                       country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])


class TestTJ0462FilledRateNamesItsPublicationDate(unittest.TestCase):
    """TJ-0462: the rates file fills a weekend or holiday with the Bank of
    Canada's last rate, and the evidence named the filled day ("history
    exact date; rate date 2024-03-31", "Bank of Canada USD/CAD 1.401
    (2024-12-01)"), a day the Bank published nothing. The file now
    records the publication date of a filled day, and the evidence
    names it."""

    RATES = {"2024-03-28": "1.3550", "2024-04-02": "1.3560"}

    def _file(self, td):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock
        from taxjson.bin import to_base_curr as T
        boc = (lambda _c, a, b: {d: v for d, v in self.RATES.items()
                                 if a <= d <= b})
        out = io.StringIO()
        with mock.patch.object(T, "CACHE_FILE", str(Path(td) / "fx.json")), \
                mock.patch.object(T, "fetch_boc", boc), \
                mock.patch.object(T, "fetch_yahoo", lambda *_a: {}), \
                mock.patch.object(T, "fetch_boc_noon", lambda *_a: {}), \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": ""}), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = T.main(["USD", "CAD", "--start", "2024-03-28",
                         "--end", "2024-04-02"])
        self.assertFalse(rc)
        p = Path(td) / "to_base.csv"
        p.write_text(out.getvalue())
        return p

    @rule("CA-FX-01")
    def test_the_file_names_the_publication_day(self):
        with tempfile.TemporaryDirectory() as td:
            lines = self._file(td).read_text().splitlines()
        self.assertEqual(lines, [
            "2024-03-28 12:00:00 USD CAD 1.3550 boc",
            "2024-03-29 12:00:00 USD CAD 1.3550 boc 2024-03-28",
            "2024-03-30 12:00:00 USD CAD 1.3550 boc 2024-03-28",
            "2024-03-31 12:00:00 USD CAD 1.3550 boc 2024-03-28",
            "2024-04-01 12:00:00 USD CAD 1.3550 boc 2024-03-28",
            "2024-04-02 12:00:00 USD CAD 1.3560 boc"])

    @rule("CA-FX-01")
    def test_the_conversion_evidence_names_it(self):
        from decimal import Decimal
        from taxjson.bin import taxjson_convert_currency as CC
        with tempfile.TemporaryDirectory() as td:
            hist = CC.load_exchange_rates(self._file(td), "CAD")
        ev = {}
        rate = CC.get_rate_for_date("USD", "2024-03-31", hist, None,
                                    rate_evidence=ev)
        self.assertEqual(rate, Decimal("1.3550"))
        self.assertEqual(ev, {"fx_date": "2024-03-28",
                              "fx_source": "history prior date"})
        ev = {}
        CC.get_rate_for_date("USD", "2024-03-28", hist, None,
                             rate_evidence=ev)
        self.assertEqual(ev, {"fx_date": "2024-03-28",
                              "fx_source": "history exact date"})

    def test_crypto_sends_names_it(self):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            r = CS.Rates(CS.load_rates(self._file(td), "CAD"), "CAD")
        got = r.get("USD", "2024-03-31")
        self.assertEqual(got, (1.355, "2024-03-28", "Bank of Canada"))
        self.assertEqual(CS._rate_text("USD", "CAD", got, "2024-03-31"),
                         "Bank of Canada USD/CAD 1.355 (2024-03-28, "
                         "latest before 2024-03-31)")
        self.assertEqual(r.get("USD", "2024-03-28")[1], "2024-03-28")

    def test_an_older_file_reads_as_before(self):
        from taxjson.bin import taxjson_convert_currency as CC
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "to_base.csv"
            p.write_text("2024-03-28 12:00:00 USD CAD 1.3550 boc\n"
                         "2024-03-31 12:00:00 USD CAD 1.3550 boc\n")
            hist = CC.load_exchange_rates(p, "CAD")
            ev = {}
            CC.get_rate_for_date("USD", "2024-03-31", hist, None,
                                 rate_evidence=ev)
            self.assertEqual(ev["fx_date"], "2024-03-31")
            self.assertEqual(CS.Rates(CS.load_rates(p), "CAD").get(
                "USD", "2024-03-31")[1], "2024-03-31")


if __name__ == "__main__":
    unittest.main()
