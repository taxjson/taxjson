"""Regression pins for the 2026-09 broker-parser coverage audit.

Every fixture here is synthetic (FAKE account ids like U1 / 99900001,
made-up ISINs). One class per gap:

  1. IB `Trades / Forex` rows       -> recognized non-events, no phantom asset
  2. Kraken stablecoin rewards      -> priced 1.0/unit, income = qty
  3. IB exercise code `Ex`          -> ASSIGN; premium rolls into the stock leg
  4. IB `Transaction Fees`          -> folded into the same-day trade, else FEE
  5. IB `Commission Adjustments`    -> negative (refund) FEE row
  6. IB tender / voluntary offer    -> no-op round trip vs. booked cash sale
  7. Kraken transfer/transferpeertopeer -> TRANSFER evidence
  8. Kraken fiat conversions        -> recognized non-events
  9. Questrade FCH / FXT            -> FEE row / non-event
  hygiene (a)-(h)                   -> row accounting, subtotals, trailers,
                                       coinbase non-events, 0-tx guard
"""
import contextlib
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.core import TaxTransaction, get_tax_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
NE = IbBrokerage.KNOWN_NONEVENT_PREFIX


def _parse(parser_cls, text, prefix="x_"):
    """(parser, transactions, stderr) for an in-memory CSV."""
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     prefix=prefix) as f:
        f.write(text)
        name = f.name
    parser = parser_cls()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(Path(name))
    finally:
        os.remove(name)
    return parser, txs, err.getvalue()


def _unaccounted(parser):
    return (parser._rows_seen - parser._rows_consumed
            - sum(parser._skip_counts.values()))


def _gains(txs, country):
    valid = set(inspect.signature(TaxTransaction).parameters)
    tt = [TaxTransaction(**{k: v for k, v in t.items() if k in valid})
          for t in txs]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = get_tax_rules(country).compute_gains(tt)
    return res["transactions"], err.getvalue()


IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n')
IB_TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
               'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
IB_CA_H = ('Corporate Actions,Header,Asset Category,Currency,Date/Time,'
           'Description,Quantity,Proceeds,Value,Realized P/L,Code\n')
IB_TXN_FEES_H = ('Transaction Fees,Header,Asset Category,Currency,Account,'
                 'Date/Time,Symbol,Description,Quantity,Trade Price,'
                 'Amount,Code\n')
IB_COMM_ADJ_H = ('Commission Adjustments,Header,Currency,Date,Description,'
                 'Amount,Code\n')

KR_LEDGER_H = ('"txid","refid","time","type","subtype","aclass","asset",'
               '"wallet","amount","fee","balance"\n')
KR_TRADES_H = ('txid,ordertxid,pair,time,type,ordertype,price,cost,fee,'
               'vol,margin,misc,ledgers\n')
QT_H = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
        'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
        'Account #,Activity Type,Account Type\n')
CB_H = ('Timestamp,Transaction Type,Asset,Quantity Transacted,'
        'Price Currency,Price at Transaction,Fees and/or Spread,'
        'Total (inclusive of fees and/or spread)\n')


# ---------------------------------------------------------------- 1. Forex
class TestIbForexRows(unittest.TestCase):
    CSV = IB_HEAD + IB_TRADES_H + (
        'Trades,Data,Order,Stocks,USD,U1,MSFT,"2026-02-05, 09:31:00",'
        '50,400.00,0,-20000,-1,0,0,0,O\n'
        'Trades,Data,Order,Forex,CAD,U1,USD.CAD,"2026-02-05, 10:00:00",'
        '1000,1.38,1.381,-1380,-2,0,0,0,AFx\n'
        'Trades,Data,Order,Forex,CAD,U1,USD.CAD,"2026-02-06, 10:00:00",'
        '-500,1.379,1.38,689.5,-2,0,0,1.2,\n'
        'Trades,Data,Order,Forex,CAD,U1,USD.CAD,"2026-02-07, 10:00:00",'
        '250,1.377,1.378,-344.25,-2,0,0,0,P\n'
        'Trades,Data,Total,Forex,,,,,,,,-1034.75,-6,,,1.2,\n'
    )

    def test_forex_rows_are_counted_non_events_not_phantom_assets(self):
        parser, txs, err = _parse(IbBrokerage, self.CSV)
        syms = {t["symbol"] for t in txs}
        self.assertEqual(syms, {"MSFT.US"},
                         "a Forex conversion must not become a BUYSELL "
                         "of a phantom USD/USD.CAD asset")
        forex = {k: v for k, v in parser._skip_counts.items()
                 if "Forex" in k}
        self.assertEqual(sum(forex.values()), 3)
        self.assertTrue(all(k.startswith(NE) for k in forex),
                        "Forex rows are RECOGNIZED non-events, not "
                        "unclassified skips")
        self.assertEqual(_unaccounted(parser), 0)
        # Printed under the calmer note, never under the "needs a new
        # branch" call to action.
        self.assertIn("recognized non-event", err)
        self.assertIn("Forex", err)
        self.assertNotIn("unclassified", err)


# ---------------------------------------------------- 2. stablecoin reward
class TestKrakenStablecoinReward(unittest.TestCase):
    def test_usdc_reward_is_income_worth_its_quantity(self):
        csv = KR_LEDGER_H + (
            '"L1","","2026-01-15 10:00:00","earn","reward","currency",'
            '"USDC","earn","12.5","0","112.5"\n'
        )
        _, txs, _ = _parse(KrakenBrokerage, csv, prefix="kr_ledgers_")
        divs = [t for t in txs if t["action"] == "DIVIDEND"]
        self.assertEqual(len(divs), 1)
        d = divs[0]
        self.assertEqual(d["symbol"], "USDC",
                         "folding to `USD` is what made fill-crypto "
                         "refuse to price it")
        self.assertAlmostEqual(d["quantity"], 12.5)
        self.assertAlmostEqual(d["price"], 1.0)
        self.assertAlmostEqual(d["net_amount"], 12.5)
        self.assertAlmostEqual(d["gross_amount"], 12.5)
        # No acquisition leg: USDC is later SPENT as a fiat quote, so a
        # USDC position would sit in the book forever as a phantom long.
        self.assertEqual([t for t in txs if t["action"] == "BUYSELL"], [])

    def test_crypto_reward_still_ships_unpriced_pair(self):
        csv = KR_LEDGER_H + (
            '"L1","","2026-01-15 10:00:00","earn","reward","currency",'
            '"ETH","earn","0.01","0","1"\n'
        )
        _, txs, _ = _parse(KrakenBrokerage, csv, prefix="kr_ledgers_")
        self.assertEqual({t["action"] for t in txs}, {"DIVIDEND", "BUYSELL"})
        self.assertTrue(all(t["net_amount"] == 0.0 for t in txs))

    def _run_fill(self, rows):
        from taxjson.bin import fill_crypto_prices as fcp
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "in.json"
            inp.write_text(json.dumps({"transactions": rows}))
            old_cache, fcp.CACHE_FILE = fcp.CACHE_FILE, str(Path(td) / "c.json")
            old_argv, sys.argv = sys.argv, ["taxjson-fill-crypto", str(inp)]
            old_off = os.environ.get("TAXJSON_OFFLINE")
            os.environ["TAXJSON_OFFLINE"] = "1"     # any lookup = failure
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    fcp.main()
            finally:
                fcp.CACHE_FILE = old_cache
                sys.argv = old_argv
                if old_off is None:
                    os.environ.pop("TAXJSON_OFFLINE", None)
                else:
                    os.environ["TAXJSON_OFFLINE"] = old_off
        return json.loads(out.getvalue())["transactions"], err.getvalue()

    def test_fill_crypto_prices_stablecoin_and_usd_dividend_at_one(self):
        base = {"date": "2026-01-15", "time": "10:00:00",
                "date_settle": "2026-01-15", "currency": "USD",
                "price": 0.0, "net_amount": 0.0, "gross_amount": 0.0,
                "account": "Kraken", "type": "dividend",
                "description": "Staking Reward"}
        rows = [
            {**base, "action": "DIVIDEND", "symbol": "USDC", "quantity": 12.5},
            {**base, "action": "DIVIDEND", "symbol": "DAI", "quantity": 3.0},
            # A reward someone already folded to its anchor by hand.
            {**base, "action": "DIVIDEND", "symbol": "USD", "quantity": 7.25},
        ]
        out, err = self._run_fill(rows)
        by = {t["symbol"]: t for t in out}
        self.assertAlmostEqual(by["USDC"]["net_amount"], 12.5)
        self.assertAlmostEqual(by["USDC"]["price"], 1.0)
        self.assertAlmostEqual(by["DAI"]["net_amount"], 3.0)
        self.assertAlmostEqual(by["USD"]["net_amount"], 7.25)
        self.assertNotIn("failed to fetch", err)


# ------------------------------------------------------------ 3. Ex code
class TestIbExerciseCode(unittest.TestCase):
    CALL = IB_HEAD + IB_TRADES_H + (
        # buy 1 call @ 2.00 (+1 comm) -> cost 201
        'Trades,Data,Order,Equity and Index Options,USD,U1,"ABC 16JAN26 50 C",'
        '"2026-01-05, 10:00:00",1,2.00,0,-200,-1,0,0,0,O\n'
        # exercise: option leg closed at T. Price 0
        'Trades,Data,Order,Equity and Index Options,USD,U1,"ABC 16JAN26 50 C",'
        '"2026-01-16, 16:20:00",-1,0,0,0,0,0,0,0,C;Ex\n'
        # stock leg: buy 100 @ strike 50
        'Trades,Data,Order,Stocks,USD,U1,ABC,"2026-01-16, 16:20:00",'
        '100,50,0,-5000,0,0,0,0,Ex;O\n'
        # later sale @ 60 (+1 comm)
        'Trades,Data,Order,Stocks,USD,U1,ABC,"2026-03-02, 10:00:00",'
        '-100,60,0,6000,-1,0,0,0,C\n'
    )
    PUT = IB_HEAD + IB_TRADES_H + (
        'Trades,Data,Order,Stocks,USD,U1,ABC,"2026-01-02, 10:00:00",'
        '100,60,0,-6000,-1,0,0,0,O\n'
        # buy 1 put @ 3.00 (+1 comm) -> cost 301
        'Trades,Data,Order,Equity and Index Options,USD,U1,"ABC 16JAN26 50 P",'
        '"2026-01-05, 10:00:00",1,3.00,0,-300,-1,0,0,0,O\n'
        'Trades,Data,Order,Equity and Index Options,USD,U1,"ABC 16JAN26 50 P",'
        '"2026-01-16, 16:20:00",-1,0,0,0,0,0,0,0,C;Ex\n'
        # stock leg: sell 100 @ strike 50
        'Trades,Data,Order,Stocks,USD,U1,ABC,"2026-01-16, 16:20:00",'
        '-100,50,0,5000,0,0,0,0,C;Ex\n'
    )

    def test_ex_option_leg_is_assign_stock_leg_stays_buysell(self):
        parser, txs, _ = _parse(IbBrokerage, self.CALL)
        by = {(t["symbol"], t["date"]): t for t in txs}
        self.assertEqual(by[("ABC260116C00050000.US", "2026-01-16")]["action"],
                         "ASSIGN", "`C;Ex` at T. Price 0 must classify "
                         "like `A` (was a plain BUYSELL at price 0)")
        self.assertEqual(by[("ABC.US", "2026-01-16")]["action"], "BUYSELL")
        self.assertAlmostEqual(by[("ABC.US", "2026-01-16")]["price"], 50.0)
        self.assertEqual(_unaccounted(parser), 0)

    def _one_stock_entry(self, txs, country):
        entries, err = _gains(txs, country)
        self.assertNotIn("unconsumed option-assignment", err,
                         f"{country}: premium staged but never rolled")
        stock = [e for e in entries if e["symbol"] == "ABC.US"]
        opt = [e for e in entries if e["symbol"] != "ABC.US"]
        self.assertEqual(opt, [], f"{country}: the exercised option "
                                  f"must not realize its own P&L")
        self.assertEqual(len(stock), 1)
        return stock[0]

    def test_call_exercise_premium_rolls_into_stock_cost_both_engines(self):
        _, txs, _ = _parse(IbBrokerage, self.CALL)
        for country in ("canada", "usa"):
            e = self._one_stock_entry(txs, country)
            # cost = 5000 strike + 201 premium; proceeds = 6000 - 1
            self.assertAlmostEqual(e["cost"], 5201.0, places=2, msg=country)
            self.assertAlmostEqual(e["proceeds"], 5999.0, places=2, msg=country)
            self.assertAlmostEqual(e["gain"], 798.0, places=2, msg=country)

    def test_put_exercise_premium_reduces_stock_proceeds_both_engines(self):
        _, txs, _ = _parse(IbBrokerage, self.PUT)
        for country in ("canada", "usa"):
            e = self._one_stock_entry(txs, country)
            # proceeds = 5000 strike - 301 premium; cost = 6000 + 1
            self.assertAlmostEqual(e["proceeds"], 4699.0, places=2, msg=country)
            self.assertAlmostEqual(e["cost"], 6001.0, places=2, msg=country)
            self.assertAlmostEqual(e["gain"], -1302.0, places=2, msg=country)


# ---------------------------------------------------- 4. Transaction Fees
class TestIbTransactionFees(unittest.TestCase):
    def test_levy_folds_into_same_day_trade(self):
        csv = IB_HEAD + IB_TRADES_H + (
            'Trades,Data,Order,Stocks,GBP,U1,AWE,"2026-03-04, 08:05:12",'
            '100,5.00,0,-500,-1,0,0,0,O\n'
        ) + IB_TXN_FEES_H + (
            'Transaction Fees,Data,Stocks,GBP,U1,"2026-03-04, 08:05:12",'
            'AWE,UK Stamp Tax,100,5.00,-2.50,\n'
            'Transaction Fees,Data,Total,,,,,,,,-2.50,\n'
            'Transaction Fees,Data,Total in CAD,,,,,,,,-4.30,\n'
        )
        parser, txs, _ = _parse(IbBrokerage, csv)
        self.assertEqual([t["action"] for t in txs], ["BUYSELL"],
                         "a folded levy must not ALSO emit a FEE row")
        t = txs[0]
        self.assertEqual(t["symbol"], "AWE.L")
        self.assertAlmostEqual(t["fee"], 3.5)          # 1 comm + 2.5 stamp
        self.assertAlmostEqual(t["net_amount"], 503.5)  # cost incl. levy
        self.assertEqual(parser._skip_counts.get(
            f"{NE}Transaction Fees subtotal row"), 2)
        self.assertEqual(_unaccounted(parser), 0)

    def test_per_fill_levies_all_fold_into_one_order_row(self):
        # Real 2025 export: a 10,000-share AWE buy filled 8,900 + 1,100
        # carried TWO UK Stamp Tax rows against ONE Order row. The
        # taken-once fold sent the second row out as a standalone FEE
        # ("no same-day trade to fold into") — 5.43 GBP that never
        # reached the ACB.
        csv = IB_HEAD + IB_TRADES_H + (
            'Trades,Data,Order,Stocks,GBP,U1,AWE,"2025-03-28, 09:06:18",'
            '"10,000",0.988,0.968,-9880,-54.34,9934.34,0,-200,O;P\n'
        ) + IB_TXN_FEES_H + (
            'Transaction Fees,Data,Stocks,GBP,U1,"2025-03-28, 09:06:18",'
            'AWE,UK Stamp Tax,"8,900",0.988,-43.966,\n'
            'Transaction Fees,Data,Stocks,GBP,U1,"2025-03-28, 09:06:18",'
            'AWE,UK Stamp Tax,"1,100",0.988,-5.434,\n'
            'Transaction Fees,Data,Total,,,,,,,,-49.40,\n'
        )
        parser, txs, _ = _parse(IbBrokerage, csv)
        self.assertEqual([t["action"] for t in txs], ["BUYSELL"])
        t = txs[0]
        self.assertAlmostEqual(t["fee"], 54.34 + 43.966 + 5.434, places=6)
        self.assertAlmostEqual(t["net_amount"], 9934.34 + 49.40, places=6)
        self.assertEqual(_unaccounted(parser), 0)
        # Two same-day trades with one levy each still pair 1:1 by
        # quantity, in either row order.
        csv2 = IB_HEAD + IB_TRADES_H + (
            'Trades,Data,Order,Stocks,GBP,U1,AWE,"2025-03-28, 09:06:18",'
            '100,5.00,0,-500,-1,0,0,0,O\n'
            'Trades,Data,Order,Stocks,GBP,U1,AWE,"2025-03-28, 11:06:18",'
            '300,5.00,0,-1500,-1,0,0,0,O\n'
        ) + IB_TXN_FEES_H + (
            'Transaction Fees,Data,Stocks,GBP,U1,"2025-03-28, 11:06:18",'
            'AWE,UK Stamp Tax,300,5.00,-7.50,\n'
            'Transaction Fees,Data,Stocks,GBP,U1,"2025-03-28, 09:06:18",'
            'AWE,UK Stamp Tax,100,5.00,-2.50,\n'
        )
        _, txs2, _ = _parse(IbBrokerage, csv2)
        fees = {float(t["quantity"]): round(t["fee"], 4) for t in txs2}
        self.assertEqual(fees, {100.0: 3.5, 300.0: 8.5})

    def test_unmatched_levy_becomes_symbol_bound_fee_row(self):
        csv = IB_HEAD + IB_TXN_FEES_H + (
            'Transaction Fees,Data,Stocks,GBP,U1,"2026-03-04, 08:05:12",'
            'AWE,UK Stamp Tax,100,5.00,-2.50,\n'
        )
        parser, txs, _ = _parse(IbBrokerage, csv)
        self.assertEqual(len(txs), 1)
        f = txs[0]
        self.assertEqual(f["action"], "FEE")
        self.assertEqual(f["symbol"], "AWE.L")
        self.assertEqual(f["currency"], "GBP")
        self.assertAlmostEqual(f["net_amount"], 2.5,
                               msg="repo FEE sign: positive = charged")
        self.assertEqual(f["date"], "2026-03-04")
        self.assertIn("UK Stamp Tax", f["description"])
        self.assertEqual(_unaccounted(parser), 0)


# ------------------------------------------------ 5. Commission Adjustments
class TestIbCommissionAdjustments(unittest.TestCase):
    def test_refund_is_negative_fee_row_totals_counted(self):
        csv = IB_HEAD + IB_COMM_ADJ_H + (
            'Commission Adjustments,Data,USD,2026-02-10,'
            '"Refund (KWEB, -200 2026-02-07)",1.25,\n'
            'Commission Adjustments,Data,Total,,,1.25,\n'
            'Commission Adjustments,Data,Total in CAD,,,1.72,\n'
        )
        parser, txs, _ = _parse(IbBrokerage, csv)
        self.assertEqual(len(txs), 1)
        f = txs[0]
        self.assertEqual(f["action"], "FEE")
        self.assertEqual(f["symbol"], "KWEB.US")
        self.assertAlmostEqual(f["net_amount"], -1.25,
                               msg="a refund is a NEGATIVE fee")
        self.assertEqual(f["date"], "2026-02-10")
        self.assertEqual(parser._skip_counts.get(
            f"{NE}Commission Adjustments subtotal row"), 2)
        self.assertEqual(_unaccounted(parser), 0)


# ------------------------------------------------------ 6. tender offers
class TestIbTenderOffers(unittest.TestCase):
    TENDER_PAIR = (
        'Corporate Actions,Data,Stocks,CAD,"2026-04-01, 20:25:00",'
        '"AAUC(CA9990000101) Tendered to 99900001 1 FOR 1 (AAUC.TEN, '
        'ALLIED GOLD CORP - TENDER, CA9990000101)",-500,0,0,0,\n'
        'Corporate Actions,Data,Stocks,CAD,"2026-04-01, 20:25:00",'
        '"AAUC(CA9990000101) Tendered to 99900001 1 FOR 1 (AAUC.TEN, '
        'ALLIED GOLD CORP - TENDER, CA9990000101)",500,0,0,0,\n'
    )
    RETURN_PAIR = (
        'Corporate Actions,Data,Stocks,CAD,"2026-04-20, 20:25:00",'
        '"AAUC.TEN(99900001) Merged(Voluntary Offer Allocation) WITH '
        'CA9990000101 1 for 1 (AAUC, ALLIED GOLD CORP, CA9990000101)",'
        '-500,0,0,0,\n'
        'Corporate Actions,Data,Stocks,CAD,"2026-04-20, 20:25:00",'
        '"AAUC.TEN(99900001) Merged(Voluntary Offer Allocation) WITH '
        'CA9990000101 1 for 1 (AAUC, ALLIED GOLD CORP, CA9990000101)",'
        '500,0,0,0,\n'
    )
    TOTAL = 'Corporate Actions,Data,Total,,,,,,0,0,\n'

    def test_zero_proceeds_round_trip_is_recognized_noop(self):
        csv = IB_HEAD + IB_CA_H + self.TENDER_PAIR + self.RETURN_PAIR + self.TOTAL
        parser, txs, err = _parse(IbBrokerage, csv)
        self.assertEqual(txs, [], "no shares changed hands, no cash: "
                                  "nothing may be booked")
        self.assertEqual(parser._skip_counts.get(
            f"{NE}Corporate Actions tender/voluntary-offer share journal "
            f"(zero proceeds)"), 4)
        self.assertEqual(_unaccounted(parser), 0)
        self.assertIn("recognized no-op", err)
        self.assertNotIn("unhandled Corporate Action", err,
                         "must not land in the generic unhandled tally")

    def test_cash_settlement_is_a_booked_sale_with_note(self):
        cash_leg = (
            'Corporate Actions,Data,Stocks,CAD,"2026-04-20, 20:25:00",'
            '"AAUC.TEN(99900001) Merged(Voluntary Offer Allocation) WITH '
            'CA9990000101 1 for 1 (AAUC, ALLIED GOLD CORP, CA9990000101)",'
            '-500,5250,5250,0,\n'
        )
        csv = IB_HEAD + IB_CA_H + self.TENDER_PAIR + cash_leg + self.TOTAL
        parser, txs, err = _parse(IbBrokerage, csv)
        self.assertEqual(len(txs), 1)
        s = txs[0]
        self.assertEqual(s["action"], "BUYSELL")
        self.assertEqual(s["symbol"], "AAUC.TO",
                         "the sale is of the ROOT position; the .TEN "
                         "placeholder never entered the book")
        self.assertAlmostEqual(s["quantity"], -500.0)
        self.assertAlmostEqual(s["net_amount"], 5250.0)
        self.assertAlmostEqual(s["price"], 10.5)
        self.assertEqual(s["currency"], "CAD")
        self.assertIn("settled for cash", err)
        self.assertIn("500 share(s) disposed for 5250.00 CAD", err)
        self.assertNotIn("unhandled Corporate Action", err)
        self.assertEqual(_unaccounted(parser), 0)

    def test_pending_tender_warns_shares_still_parked(self):
        csv = IB_HEAD + IB_CA_H + self.TENDER_PAIR
        _, txs, err = _parse(IbBrokerage, csv)
        self.assertEqual(txs, [])
        self.assertIn("still sit on the tender placeholder", err)


# ------------------------------------------------ 7. Kraken p2p transfer
class TestKrakenPeerToPeerTransfer(unittest.TestCase):
    def test_outbound_p2p_is_transfer_evidence(self):
        csv = KR_LEDGER_H + (
            '"L9","R9","2026-02-02 09:00:00","transfer","transferpeertopeer",'
            '"currency","USDC","spot","-25","0","75"\n'
        )
        parser, txs, err = _parse(KrakenBrokerage, csv, prefix="kr_ledgers_")
        self.assertEqual(len(txs), 1)
        t = txs[0]
        self.assertEqual(t["action"], "TRANSFER")
        self.assertEqual(t["symbol"], "USDC")
        self.assertAlmostEqual(t["quantity"], -25.0)
        self.assertEqual(t["description"], "transfer/transferpeertopeer")
        self.assertEqual(t["id"], "L9-xfer")
        self.assertNotIn("ignored", err)


# --------------------------------------------- 8. Kraken fiat conversions
class TestKrakenFiatConversions(unittest.TestCase):
    def test_trades_csv_fiat_base_fills_are_non_events(self):
        csv = KR_TRADES_H + (
            'T1,O1,BTC/USD,2026-01-15 10:00:00.1234,buy,limit,60000,6000,10,0.1,,,\n'
            'T2,O2,USD/CAD,2026-01-16 10:00:00.0,sell,market,1.38,1380,2,1000,,,\n'
            'T3,O3,USDC/USD,2026-01-17 10:00:00.0,buy,market,1.0,500,0.5,500,,,\n'
            'T4,O4,USDTZCAD,2026-01-18 10:00:00.0,buy,market,1.37,137,0,100,,,\n'
        )
        parser, txs, err = _parse(KrakenBrokerage, csv, prefix="kr_trades_")
        self.assertEqual([t["symbol"] for t in txs], ["BTC"],
                         "USD/CAD/USDC/USDT must never be traded assets")
        forex = {k: v for k, v in parser._skip_counts.items()
                 if k.startswith(f"{NE}forex conversion")}
        self.assertEqual(sum(forex.values()), 3)
        self.assertIn("recognized non-event", err)

    def test_ledger_fiat_fiat_instant_trades_are_non_events(self):
        csv = KR_LEDGER_H + (
            # USDC dust swept into USD
            '"L1","DS1","2026-03-01 00:00:00","spend","","currency","USDC",'
            '"spot","-0.42","0","0"\n'
            '"L2","DS1","2026-03-01 00:00:00","receive","","currency","ZUSD",'
            '"spot","0.42","0","10"\n'
            # USD -> CAD
            '"L3","FX1","2026-03-02 00:00:00","spend","","currency","ZUSD",'
            '"spot","-100","0","0"\n'
            '"L4","FX1","2026-03-02 00:00:00","receive","","currency","ZCAD",'
            '"spot","137","0","137"\n'
            # a real instant buy still books
            '"L5","IT1","2026-03-03 00:00:00","spend","","currency","ZUSD",'
            '"spot","-100","1.5","0"\n'
            '"L6","IT1","2026-03-03 00:00:00","receive","","currency","XXBT",'
            '"spot","0.001","0","0.001"\n'
        )
        parser, txs, err = _parse(KrakenBrokerage, csv, prefix="kr_ledgers_")
        self.assertEqual([t["symbol"] for t in txs], ["BTC"])
        forex = {k: v for k, v in parser._skip_counts.items()
                 if k.startswith(f"{NE}forex conversion")}
        self.assertEqual(sum(forex.values()), 2)
        self.assertNotIn("orphan", err)


# ------------------------------------------------- 9. Questrade FCH / FXT
class TestQuestradeFchFxt(unittest.TestCase):
    CSV = QT_H + (
        '2026-07-02 09:30:00 AM,2026-07-02 12:00:00 AM,FCH,,'
        'ADR CUSTODY FEE # SHARES TSM RECORD DATE 06/15/26,'
        '0,0.00,0.00,0.00,-3.50,USD,99900001,Fees and rebates,Individual\n'
        '2026-07-03 09:30:00 AM,2026-07-03 12:00:00 AM,FXT,,'
        'CONVERSION - USD/CAD,0,0.00,0.00,0.00,-1000.00,USD,99900001,'
        'FX conversion,Individual\n'
    )

    def test_fch_is_fee_row_fxt_is_non_event(self):
        parser, txs, err = _parse(QuestradeBrokerage, self.CSV)
        self.assertEqual(len(txs), 1)
        f = txs[0]
        self.assertEqual(f["action"], "FEE")
        self.assertEqual(f["symbol"], "TSM.US")
        self.assertAlmostEqual(f["net_amount"], 3.5,
                               msg="charged -> positive")
        self.assertEqual(f["date"], "2026-07-02")
        self.assertEqual(parser._skip_counts,
                         {f"{NE}FX conversion (FXT)": 1})
        self.assertEqual(_unaccounted(parser), 0)
        self.assertIn("recognized non-event", err)
        self.assertNotIn("unclassified", err)

    def test_zero_net_dividend_row_is_counted_not_silently_consumed(self):
        csv = QT_H + (
            '2026-04-01 09:30:00 AM,2026-04-01 12:00:00 AM,DIV,AAPL,'
            'APPLE INC CASH DIV ON 60 SHS REC 03/28/26 PAY 04/01/26,'
            '0,0.00,0.00,0.00,0.00,USD,99900001,Dividends,Individual\n'
            # a CIL row with no fraction: builder counts its own skip —
            # the caller must not ALSO consume it (lint went negative)
            '2026-04-02 09:30:00 AM,2026-04-02 12:00:00 AM,CIL,AAPL,'
            'APPLE INC CASH IN LIEU OF SHARES,0,0.00,0.00,0.00,1.00,USD,'
            '99900001,Other,Individual\n'
        )
        parser, txs, _ = _parse(QuestradeBrokerage, csv)
        self.assertEqual(txs, [])
        self.assertEqual(parser._rows_consumed, 0)
        self.assertEqual(parser._skip_counts.get(
            f"{NE}zero-net dividend row (informational)"), 1)
        self.assertEqual(_unaccounted(parser), 0)


# --------------------------------------------------------- hygiene (a-c)
class TestIbRowAccounting(unittest.TestCase):
    FULL = IB_HEAD + IB_TRADES_H + (
        'Trades,Data,Order,Stocks,USD,U1,MSFT,"2026-02-05, 09:31:00",'
        '50,400.00,0,-20000,-1,0,0,0,O\n'
        'Trades,Data,Order,Forex,CAD,U1,USD.CAD,"2026-02-05, 10:00:00",'
        '1000,1.38,1.381,-1380,-2,0,0,0,AFx\n'
        'Trades,Data,Total,,,,,,,,,-21380,-3,,,,\n'
        'Dividends,Header,Currency,Account,Date,Description,Amount\n'
        'Dividends,Data,USD,U1,2026-06-12,'
        'MSFT(US9990000101) Cash Dividend USD 0.75 per Share,37.50\n'
        'Dividends,Data,Total,,,,37.50\n'
        'Withholding Tax,Header,Currency,Account,Date,Description,Amount\n'
        'Withholding Tax,Data,USD,U1,2026-06-12,'
        'MSFT(US9990000101) Cash Dividend USD 0.75 per Share - US Tax,-5.63\n'
        'Withholding Tax,Data,Total,,,,-5.63\n'
        'Interest,Header,Currency,Account,Date,Description,Amount\n'
        'Interest,Data,USD,U1,2026-02-03,USD Credit Interest for Jan-2026,1.10\n'
        'Interest,Data,Total,,,,1.10\n'
        'Fees,Header,Subtitle,Currency,Date,Description,Amount\n'
        'Fees,Data,Other Fees,USD,2026-02-03,Market Data Fee for Jan 2026,-10\n'
        'Fees,Data,Total,,,,-10\n'
        'Fees,Data,Total in CAD,,,,-13.80\n'
    ) + IB_CA_H + (
        'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
        '"GRTS(US9990000202) Split 1 for 10 (GRTS, GRITSTONE BIO INC, '
        'US9990000202)",-90,0,0,0,\n'
        'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
        '"GRTS(US9990000202) Split 1 for 10 (GRTS, GRITSTONE BIO INC, '
        'US9990000202)",9,0,0,0,\n'
        'Corporate Actions,Data,Total,,,,,,0,0,\n'
        'Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
        'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,'
        'Realized P/L,Cash Amount,Code\n'
        'Transfers,Data,Stocks,USD,XYZ,2026-02-01,Internal,In,--,U9,'
        '100,10,1000,0,0,\n'
        'Transfers,Data,Cash,USD,CASH.USD,2026-02-01,ACATS,In,--,--,'
        ',,,,500,\n'
        'Open Positions,Header,DataDiscriminator,Asset Category,Currency,'
        'Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,'
        'Unrealized P/L,Code\n'
        'Open Positions,Data,Summary,Stocks,USD,MSFT,50,1,400,20000,'
        '410,20500,500,\n'
        'Open Positions,Data,Lot,Stocks,USD,MSFT,50,1,400,20000,'
        '410,20500,500,\n'
        'Open Positions,Data,Total,,USD,,,,,20000,,20500,500,\n'
        'Change in Dividend Accruals,Header,Asset Category,Currency,'
        'Account,Symbol,Date,Ex Date,Pay Date,Quantity,Tax,Fee,Gross Rate,'
        'Gross Amount,Net Amount,Code\n'
        'Change in Dividend Accruals,Data,Stocks,USD,U1,MSFT,2026-11-20,'
        '2026-11-19,2027-01-12,50,0,0,0.83,41.5,41.5,Po\n'
        'Change in Dividend Accruals,Data,Total,,,,,,,,,,,41.5,41.5,\n'
    ) + IB_TXN_FEES_H + (
        'Transaction Fees,Data,Stocks,USD,U1,"2026-02-05, 09:31:00",'
        'MSFT,SEC Transaction Fee,50,400.00,-0.30,\n'
        'Transaction Fees,Data,Total,,,,,,,,-0.30,\n'
    ) + IB_COMM_ADJ_H + (
        'Commission Adjustments,Data,USD,2026-02-10,'
        '"Refund (MSFT, 50 2026-02-05)",0.50,\n'
        'Commission Adjustments,Data,Total,,,0.50,\n'
        # A section with no branch at all.
        'Cash Report,Header,Currency Summary,Currency,Total,Securities,'
        'Futures,Month to Date,Year to Date\n'
        'Cash Report,Data,Starting Cash,Base Currency Summary,1000,1000,'
        '0,0,0\n'
        'Cash Report,Data,Ending Cash,Base Currency Summary,900,900,'
        '0,0,0\n'
    )

    def test_every_section_reconciles_rows_consumed_skipped(self):
        parser, txs, err = _parse(IbBrokerage, self.FULL)
        self.assertEqual(parser._rows_seen, 30)
        self.assertEqual(_unaccounted(parser), 0,
                         f"silent-drop path somewhere: {parser._skip_counts}")
        unclassified = {k: v for k, v in parser._skip_counts.items()
                        if not k.startswith(NE)}
        self.assertEqual(unclassified, {},
                         "every synthetic row is either an event or a "
                         "RECOGNIZED non-event")
        self.assertEqual(parser._skip_counts.get(
            f"{NE}section Cash Report (not translated)"), 2)
        actions = sorted(t["action"] for t in txs)
        self.assertEqual(actions, ["BUYSELL", "DIVIDEND", "FEE", "FEE",
                                   "INTEREST", "SPLIT", "TAX", "TRANSFER"])
        # (a) the summary note actually prints for IB now.
        self.assertIn("recognized non-event", err)
        self.assertNotIn("unclassified", err)
        # (c) the Corporate Actions Total row no longer reaches the
        # currency -> suffix lookup.
        self.assertNotIn("has no exchange-suffix mapping", err)
        # The SEC levy folded into the MSFT buy (0.30) on top of 1 comm.
        msft = next(t for t in txs if t["action"] == "BUYSELL")
        self.assertAlmostEqual(msft["fee"], 1.30)
        self.assertAlmostEqual(msft["net_amount"], 20001.30)

    def test_fees_subtotals_excluded_from_dateless_warning(self):
        csv = IB_HEAD + (
            'Fees,Header,Subtitle,Currency,Date,Description,Amount\n'
            'Fees,Data,Other Fees,USD,2026-02-03,Market Data Fee for Jan 2026,-10\n'
            'Fees,Data,Other Fees,USD,,Snapshot Quote Fee,-1\n'
            'Fees,Data,Total,,,,-11\n'
            'Fees,Data,Total in CAD,,,,-15.18\n'
        )
        parser, txs, err = _parse(IbBrokerage, csv)
        self.assertIn("skipped 1 IB Fees row(s) with no date", err,
                      "only the genuinely dateless row, not the subtotals")
        self.assertIn("(total: 1.00)", err,
                      "subtotals used to be added on top -> doubled total")
        fees = [t for t in txs if t["action"] == "FEE"]
        self.assertEqual(len(fees), 1)
        self.assertAlmostEqual(fees[0]["net_amount"], 10.0,
                               msg="repo FEE sign: charged = positive "
                                   "(IB books it -10)")
        self.assertEqual(parser._skip_counts.get(f"{NE}Fees subtotal row"), 2)
        self.assertEqual(_unaccounted(parser), 0)

    def test_corporate_actions_total_row_no_suffix_warning(self):
        csv = IB_HEAD + IB_CA_H + (
            'Corporate Actions,Data,Total,,,,,,0,0,\n'
            'Corporate Actions,Data,Total in CAD,,,,,,0,0,\n'
        )
        # Fresh warned-set: the warning is one-shot per process.
        from taxjson.lib.brokerages import ib_extractor
        ib_extractor._IB_WARNED_CURRENCIES.discard('')
        parser, txs, err = _parse(IbBrokerage, csv)
        self.assertEqual(txs, [])
        self.assertNotIn("has no exchange-suffix mapping", err)
        self.assertNotIn('', ib_extractor._IB_WARNED_CURRENCIES)
        self.assertEqual(parser._skip_counts.get(
            f"{NE}Corporate Actions subtotal row"), 2)


# ------------------------------------------------------------ hygiene (g)
class TestRbcBlankTrailer(unittest.TestCase):
    def test_blank_trailer_lines_are_not_rows(self):
        base = (REPO_ROOT / "tests" / "fixtures" / "rbc_direct"
                / "sample.csv").read_text(encoding="utf-8")
        ncols = base.splitlines()[0].count(",")
        trailer = ("," * ncols + "\n") * 2 + "\n" + ("," * ncols + "\n")
        p_base, txs_base, _ = _parse(RbcBrokerage, base)
        p_trail, txs_trail, err = _parse(RbcBrokerage, base + trailer)
        self.assertEqual(txs_trail, txs_base)
        self.assertEqual(p_trail._rows_seen, p_base._rows_seen,
                         "blank trailer lines counted as rows")
        self.assertEqual(p_trail._skip_counts, p_base._skip_counts)
        self.assertNotIn("activity ?", err)


# ------------------------------------------------------------ hygiene (h)
class TestCoinbaseKnownNonEvents(unittest.TestCase):
    def test_wallet_shuffles_and_funding_are_recognized(self):
        csv = CB_H + (
            '2026-01-15 10:00:00 UTC,Retail Staking Transfer,ETH,1.0,USD,3000,0,0\n'
            '2026-02-15 10:00:00 UTC,Retail Unstaking Transfer,ETH,1.0,USD,3100,0,0\n'
            '2026-03-01 10:00:00 UTC,Retail ETH Deprecation,ETH2,1.0,USD,3200,0,0\n'
            '2026-03-02 10:00:00 UTC,Deposit,USD,500,USD,1,0,500\n'
            '2026-03-03 10:00:00 UTC,Subscription,USD,29.99,USD,1,0,29.99\n'
            '2026-03-04 10:00:00 UTC,Mystery Thing,ETH,0.5,USD,3300,0,0\n'
        )
        parser, txs, err = _parse(CoinbaseBrokerage, csv)
        self.assertEqual(txs, [])
        known = {k for k in parser._skip_counts if k.startswith(NE)}
        self.assertEqual(len(known), 5)
        self.assertEqual(
            {k for k in parser._skip_counts if not k.startswith(NE)},
            {"type Mystery Thing"})
        self.assertEqual(_unaccounted(parser), 0)
        # The "needs a new branch" note names ONLY the unknown type.
        unclassified_line = next(l for l in err.splitlines()
                                 if "unclassified" in l)
        self.assertIn("Mystery Thing", unclassified_line)
        self.assertNotIn("Staking Transfer", unclassified_line)
        self.assertNotIn("Deposit", unclassified_line)


# ------------------------------------------------------------ hygiene (f)
class TestBrokerageZeroTransactionsGuard(unittest.TestCase):
    def test_transfer_only_file_is_not_a_zero_tx_regression(self):
        csv = KR_LEDGER_H + (
            '"T2","R2","2026-01-06 10:00:00","withdrawal","","currency",'
            '"XXBT","spot","-0.2","0.0002","0.3"\n'
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers.csv"
            p.write_text(csv, encoding="utf-8")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "kraken", "--account", "crypto", str(p)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("parsed to 0 transactions", r.stderr)
        self.assertIn("1 TRANSFER row(s) kept aside", r.stderr)
        self.assertIn("0 tax objects (1 TRANSFER row(s) kept aside)",
                      r.stderr)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------- 5. Transfers: Ca cancel/rebook
IB_XFER_H = ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
             'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
             'Market Value,Realized P/L,Cash Amount,Code\n')


class TestIbTransferCancellations(unittest.TestCase):
    def test_ca_leg_consumes_its_original(self):
        # Real RRSP move IB -> Questrade: MDA listed Out, Ca, Out, Ca,
        # Out. Arithmetically -383, but the Ca legs read as +383
        # acquisitions to the superficial-loss walk. One -383 survives.
        csv = IB_HEAD + IB_XFER_H + (
            'Transfers,Data,Stocks,USD,MDA,2026-08-28,ATON,Out,--,5,'
            '-383,--,"-11,627.88",0.00,0.00,\n'
            'Transfers,Data,Stocks,USD,MDA,2026-08-28,ATON,Out,--,5,'
            '383,--,"11,627.88",0.00,0.00,Ca\n'
            'Transfers,Data,Stocks,USD,MDA,2026-09-01,ATON,Out,--,5,'
            '-383,--,"-11,064.87",0.00,0.00,\n'
            'Transfers,Data,Stocks,USD,MDA,2026-09-01,ATON,Out,--,5,'
            '383,--,"11,064.87",0.00,0.00,Ca\n'
            'Transfers,Data,Stocks,USD,MDA,2026-09-01,ATON,Out,--,5,'
            '-383,--,"-11,064.87",0.00,0.00,\n'
        )
        parser, txs, _ = _parse(IbBrokerage, csv)
        self.assertEqual([(t["action"], t["date"], t["quantity"])
                          for t in txs],
                         [("TRANSFER", "2026-09-01", -383.0)])
        self.assertEqual(_unaccounted(parser), 0)

    def test_ca_without_original_stays_as_reversing_leg(self):
        # The original sits in an earlier statement: keep the reversal
        # so the two files still net, and say so.
        csv = IB_HEAD + IB_XFER_H + (
            'Transfers,Data,Stocks,USD,MDA,2026-09-01,ATON,Out,--,5,'
            '383,--,"11,064.87",0.00,0.00,Ca\n'
        )
        parser, txs, err = _parse(IbBrokerage, csv)
        self.assertEqual([(t["action"], t["quantity"], t["description"])
                          for t in txs],
                         [("TRANSFER", 383.0, "ATON (Ca)")])
        self.assertIn("cancelled", err)
        self.assertEqual(_unaccounted(parser), 0)
