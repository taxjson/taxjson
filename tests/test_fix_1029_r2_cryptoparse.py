"""Backlog fixes 2026-10-29 round 2, group `cryptoparse`: the Coinbase and
Kraken parsers, their custody evidence and the crypto-sends matching.

TJ-0510 (a Coinbase reward with no Subtotal or Price booked its gross
Total as the income), TJ-0511 (a Coinbase layout with no fee column lost
the fee the Total and Subtotal state), TJ-0512 (a Coinbase Convert with
no Subtotal dropped its fee), TJ-0538 (a Coinbase Convert or reward whose
Total, Subtotal and fee disagree was booked), TJ-0533 (a ticker.map
GLOBAL alias of a stablecoin was booked as property), TJ-0523 (a custody
row two exports disagree on was folded into the first file's version),
TJ-0524 (a reward's commission evidence two exports disagree on was
folded the same way), TJ-0525 (a Kraken instant trade whose legs are in
two exports was dropped from both), TJ-0526 (a send that arrived in two
parts booked a network-fee sale of the second part).
Synthetic data only.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from taxjson.bin import fill_crypto_prices as Fill
from taxjson.lib import crypto_sends, markets
from taxjson.lib.brokerages.coinbase import (CoinbaseBrokerage,
                                             coinbase_cash_events)
from taxjson.lib.dispatch import python_module_argv
from tax_rules import rule


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_CB_H = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
         "Price Currency,Price at Transaction,Subtotal,"
         "Total (inclusive of fees and/or spread),Fees and/or Spread,"
         "Notes\n")


def _cb(body, header=_CB_H, name="cb_2025.csv"):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(header + body, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            return CoinbaseBrokerage().parse_file(p)


def _fill(rows, price=20.0):
    """fill-crypto over `rows`, every lookup answering `price` USD."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "in.json"
        p.write_text(json.dumps({"transactions": rows}))
        out = io.StringIO()
        with patch.object(Fill, "load_cache", return_value={}), \
                patch.object(Fill, "save_cache"), \
                patch.object(Fill, "get_crypto_price", return_value=price), \
                patch.object(Fill.time, "sleep"), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            Fill._fill(SimpleNamespace(input=str(p)))
    return json.loads(out.getvalue())["transactions"]


def _by(rows, action):
    return [r for r in rows if r["action"] == action]


class TestRewardWithoutSubtotalOrPrice(unittest.TestCase):
    """TJ-0510: a Staking Income row of 2 SOL with blank Price and
    Subtotal, Total 30 and fee 10 booked income 30 and coins costing 30,
    and fill-crypto derived 15 a coin from that gross Total. The net is
    the Total less the commission, 20, as when the Subtotal is given."""

    _ROW = "r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,,,30,10,\n"

    def test_net_is_total_less_fee(self):
        rows = _cb(self._ROW)
        self.assertEqual(sorted((r["action"], r["net_amount"]) for r in rows),
                         [("BUYSELL", 20.0), ("DIVIDEND", 20.0)])
        ev = _by(rows, "DIVIDEND")[0]["staking_reward"]
        self.assertEqual((ev["gross_value"], ev["fee_value"],
                          ev["net_value"]), (30.0, 10.0, 20.0))

    def test_same_as_the_full_export(self):
        full = _cb("r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,"
                   "10,20,30,10,\n")
        part = _cb(self._ROW)
        self.assertEqual([r["net_amount"] for r in full],
                         [r["net_amount"] for r in part])

    def test_fill_derives_the_price_from_the_net(self):
        filled = _fill(_cb(self._ROW))
        div = _by(filled, "DIVIDEND")[0]
        self.assertEqual((div["net_amount"], div["price"], div["currency"]),
                         (20.0, 10.0, "CAD"))

    def test_fee_not_below_total_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _cb("r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,,,"
                "10,10,\n")
        msg = str(cm.exception)
        self.assertIn("cb_2025.csv line 2", msg)
        self.assertIn("fee 10.00 is not less than the Total 10.00", msg)

    def test_blank_fee_keeps_the_total(self):
        # The commission unknown: the Total is all there is.
        rows = _cb("r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,"
                   ",,30,,\n")
        self.assertEqual({r["net_amount"] for r in rows}, {30.0})
        ev = _by(rows, "DIVIDEND")[0]["staking_reward"]
        self.assertIsNone(ev["net_value"])
        self.assertFalse(ev["commission_known"])


class TestLegacyLayoutFee(unittest.TestCase):
    """TJ-0511: in a layout with no fee column, a Buy of 2 at 10 with
    Subtotal 20 and Total 22 booked fee 0 (the fees reports left out 2)
    while the same row with the column books fee 2."""

    _NOFEE_H = _CB_H.replace("Fees and/or Spread,", "")

    def test_buy_fee_is_the_gap(self):
        rows = _cb("t1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,10,20,22,"
                   "Bought 2 SOL\n", header=self._NOFEE_H)
        with_col = _cb("t1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,10,20,22,"
                       "2,Bought 2 SOL\n")
        self.assertEqual([(r["net_amount"], r["fee"]) for r in rows],
                         [(22.0, 2.0)])
        self.assertEqual([(r["net_amount"], r["fee"]) for r in rows],
                         [(r["net_amount"], r["fee"]) for r in with_col])

    def test_sell_fee_is_the_gap(self):
        rows = _cb("t1,2025-06-01 12:00:00 UTC,Sell,SOL,2,CAD,10,20,18,"
                   "Sold 2 SOL\n", header=self._NOFEE_H)
        self.assertEqual([(r["net_amount"], r["fee"]) for r in rows],
                         [(18.0, 2.0)])

    def test_no_subtotal_claims_no_fee(self):
        # Against quantity x price the gap holds the spread too.
        hdr = self._NOFEE_H.replace("Subtotal,", "")
        rows = _cb("t1,2025-06-01 12:00:00 UTC,Buy,SOL,2,CAD,10,21,"
                   "Bought 2 SOL\n", header=hdr)
        self.assertEqual([(r["net_amount"], r["fee"]) for r in rows],
                         [(21.0, 0.0)])


class TestConvertFee(unittest.TestCase):
    """TJ-0512: a Convert of 2 SOL to 1 ETH with blank Subtotal and Price,
    Total 22 and fee 2 shipped both legs unvalued: fill-crypto valued
    them at 20 USD and the 2 CAD fee was gone. The value is the Total
    less the fee, as the full export states it."""

    _FULL = ("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,10,20,22,2,"
             "Converted 2 SOL to 1 ETH\n")

    @staticmethod
    def _legs(rows):
        return sorted((r["symbol"], r["quantity"], r["net_amount"], r["fee"],
                       r["currency"]) for r in rows)

    def test_blank_subtotal_and_price(self):
        rows = _cb("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,,,22,2,"
                   "Converted 2 SOL to 1 ETH\n")
        self.assertEqual(self._legs(rows), self._legs(_cb(self._FULL)))
        self.assertEqual(self._legs(rows),
                         [("ETH", 1.0, 22.0, 2.0, "CAD"),
                          ("SOL", -2.0, 20.0, 0.0, "CAD")])
        # Valued by the export: fill-crypto leaves them as they are.
        self.assertEqual(self._legs(_fill(rows)), self._legs(rows))

    def test_blank_subtotal_with_price(self):
        # Used to be refused: the Total 22 did not fit 2 x 10 = 20.
        rows = _cb("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,10,,22,2,"
                   "Converted 2 SOL to 1 ETH\n")
        self.assertEqual(self._legs(rows), self._legs(_cb(self._FULL)))

    def test_only_a_fee_and_a_price(self):
        rows = _cb("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,10,,,2,"
                   "Converted 2 SOL to 1 ETH\n")
        self.assertEqual(self._legs(rows), self._legs(_cb(self._FULL)))

    def test_only_a_fee_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _cb("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,,,,2,"
                "Converted 2 SOL to 1 ETH\n")
        msg = str(cm.exception)
        self.assertIn("cb_2025.csv line 2", msg)
        self.assertIn("fee of 2.00 CAD", msg)

    def test_fee_not_below_total_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _cb("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,,,2,2,"
                "Converted 2 SOL to 1 ETH\n")
        self.assertIn("fee 2.00 is not less than the Total 2.00",
                      str(cm.exception))

    def test_no_value_and_no_fee_still_goes_to_fill(self):
        rows = _cb("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,,,,,"
                   "Converted 2 SOL to 1 ETH\n")
        self.assertEqual({(r["price"], r["net_amount"]) for r in rows},
                         {(0.0, 0.0)})


class TestSpecialRowIdentity(unittest.TestCase):
    """TJ-0538: a Convert with Subtotal 20, fee 2 and Total 220 booked
    20 / 22, and a reward with Subtotal 20, fee 40 and Total 30 booked
    20 and showed a commission of 40 out of a gross 30. Total =
    Subtotal + fee is checked, as on a Buy or Sell."""

    def test_convert_total_must_fit(self):
        with self.assertRaises(ValueError) as cm:
            _cb("c1,2025-06-01 12:00:00 UTC,Convert,SOL,-2,CAD,10,20,220,2,"
                "Converted 2 SOL to 1 ETH\n")
        msg = str(cm.exception)
        self.assertIn("cb_2025.csv line 2", msg)
        self.assertIn("Total 220.00 does not fit Subtotal 20.00 + fee 2.00",
                      msg)

    def test_reward_total_must_fit(self):
        with self.assertRaises(ValueError) as cm:
            _cb("r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,10,20,"
                "30,40,\n")
        msg = str(cm.exception)
        self.assertIn("cb_2025.csv line 2", msg)
        self.assertIn("Total 30.00 does not fit Subtotal 20.00 + fee 40.00",
                      msg)

    def test_reward_without_subtotal_checks_quantity_x_price(self):
        with self.assertRaises(ValueError) as cm:
            _cb("r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,10,,"
                "90,10,\n")
        self.assertIn("|Quantity| x Price 20.00 + fee 10.00",
                      str(cm.exception))

    def test_consistent_rows_book(self):
        rows = _cb("r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,10,"
                   "20,30,10,\n"
                   "c1,2025-06-02 12:00:00 UTC,Convert,SOL,-2,CAD,10,20,22,"
                   "2,Converted 2 SOL to 1 ETH\n")
        self.assertEqual(len(rows), 4)


class TestStablecoinAlias(unittest.TestCase):
    """TJ-0533: under ticker.map `GLOBAL SYNUSD USDC`, a Coinbase Buy of
    100 SYNUSD for 100 USD was booked as a USDC position (the raw code
    was tested for a stablecoin before the alias), and the cash and
    stablecoin pool readers saw nothing. It is US-dollar cash, as a USDC
    buy is."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        tm = Path(self._td.name) / "ticker.map"
        tm.write_text("GLOBAL SYNUSD USDC\n")
        markets.use_ticker_map(tm)

    def tearDown(self):
        markets.use_ticker_map(None)
        self._td.cleanup()

    def _all(self, coin):
        body = (f"b1,2025-03-03 12:00:00 UTC,Buy,{coin},100,CAD,1.4,140,140,"
                f"0,Bought 100 {coin}\n")
        f = Path(self._td.name) / f"cb_{coin}.csv"
        f.write_text(_CB_H + body)
        with contextlib.redirect_stderr(io.StringIO()):
            return (_cb(body), coinbase_cash_events(f),
                    crypto_sends.coinbase_pool_flows(f))

    def test_alias_books_as_the_stablecoin(self):
        parsed, cash, pool = self._all("SYNUSD")
        self.assertEqual(parsed, [])
        u_parsed, u_cash, u_pool = self._all("USDC")
        self.assertEqual([(e["kind"], e.get("amount")) for e in cash],
                         [(e["kind"], e.get("amount")) for e in u_cash])
        self.assertEqual(len(cash), 1)
        self.assertEqual([(p["units"], p["cad"]) for p in pool],
                         [(100.0, -140.0)])
        self.assertEqual([(p["units"], p["cad"]) for p in pool],
                         [(p["units"], p["cad"]) for p in u_pool])

    def test_alias_quote_of_an_advanced_trade(self):
        # The pair's quote under its alias is cash, not a crypto leg.
        rows = _cb("a1,2025-03-03 12:00:00 UTC,Advanced Trade Buy,ETH,1,"
                   "USD,2000,2000,2002,2,Bought 1 ETH for 2002 SYNUSD on "
                   "ETH-SYNUSD at 2000 SYNUSD/ETH\n")
        self.assertEqual([(r["symbol"], r["quantity"]) for r in rows],
                         [("ETH", 1.0)])


REPO_ROOT = Path(__file__).resolve().parent.parent
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")


def _brokerage(files, broker, *extra):
    """taxjson-brokerage --country canada over `files` (alone in a
    folder of their own, in name order) with a transfer sidecar: (the
    CompletedProcess, the sidecar's rows or None)."""
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in sorted(files.items()):
            (Path(td) / name).write_text(text)
            paths.append(str(Path(td) / name))
        side = Path(td) / "side.json"
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", broker, "--country", "canada",
                                "--transfers-out", str(side), *extra,
                                *paths]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)
        rows = (json.loads(side.read_text())["transactions"]
                if side.exists() else None)
        return r, rows


def _error_line(r):
    return next(x for x in r.stderr.splitlines() if "error:" in x)


class TestRestatedCustodyRow(unittest.TestCase):
    """TJ-0523 (and round 1's restated custody row): a Kraken withdrawal
    of ledger id L1 for 2 SOL in one export and 3 SOL in another passed
    --strict, the transfer sidecar keeping the first file's 2. Custody
    rows left the book before its restated-row check and the evidence
    dedup kept one per id by file order. They are checked like the
    book's rows now; an identical overlap still passes."""

    _OLD = _KL_H + "L1,R1,2025-06-01 12:00:00,withdrawal,,currency,SOL,spot,-2,0,0\n"
    _NEW = _KL_H + "L1,R1,2025-06-01 12:00:00,withdrawal,,currency,SOL,spot,-3,0,0\n"

    def test_kraken_restated_withdrawal_is_refused(self):
        r, side = _brokerage({"kr_a.csv": self._OLD, "kr_b.csv": self._NEW},
                             "kraken", "--strict")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = _error_line(r)
        self.assertIn("kr_a.csv and kr_b.csv: row id L1*** is in both with "
                      "DIFFERENT content", line)
        self.assertIn("TRANSFER SOL -2", line)
        self.assertIn("TRANSFER SOL -3", line)
        self.assertIn("the custody evidence keeps one row per id", line)
        self.assertIsNone(side)

    def test_kraken_identical_overlap_passes(self):
        r, side = _brokerage({"kr_a.csv": self._OLD, "kr_b.csv": self._OLD},
                             "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([t["quantity"] for t in side], [-2.0])

    def test_coinbase_restated_send_is_refused(self):
        row = ("s1,2025-06-01 12:00:00 UTC,Send,SOL,{q},CAD,10,{v},{v},0,"
               "Sent SOL\n")
        r, side = _brokerage(
            {"cb_a.csv": _CB_H + row.format(q=2, v=20),
             "cb_b.csv": _CB_H + row.format(q=3, v=30)}, "coinbase")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("cb_a.csv and cb_b.csv: row id s1*** is in both with "
                      "DIFFERENT content", _error_line(r))
        self.assertIsNone(side)


class TestRestatedRewardEvidence(unittest.TestCase):
    """TJ-0524: one Coinbase reward ID with gross 30 / commission 10 in
    one export and gross 40 / commission 20 in another (both net 20)
    passed --strict, and the staking views showed whichever file sorted
    first. A reward's evidence is part of what two files' rows of one
    id must agree on."""

    _ROW = ("r1,2025-06-01 12:00:00 UTC,Staking Income,SOL,2,CAD,10,20,{t},"
            "{f},Reward\n")

    def test_restated_commission_is_refused(self):
        r, _side = _brokerage(
            {"cb_a.csv": _CB_H + self._ROW.format(t=30, f=10),
             "cb_b.csv": _CB_H + self._ROW.format(t=40, f=20)},
            "coinbase", "--strict")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = _error_line(r)
        self.assertIn("row id r1*** is in both with DIFFERENT content", line)
        self.assertIn("reward gross 30.00 commission 10.00", line)
        self.assertIn("reward gross 40.00 commission 20.00", line)

    def test_identical_reward_overlap_passes(self):
        row = self._ROW.format(t=30, f=10)
        r, _side = _brokerage({"cb_a.csv": _CB_H + row, "cb_b.csv": _CB_H + row},
                              "coinbase", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)


_KL_H2 = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance,amountusd,feeusd,feecurrency\n")
_SPEND = "L1,R1,2025-12-31 23:59:59,spend,,currency,SOL,spot,-2,0,0,20,0,SOL\n"
_RECV = "L2,R1,2026-01-01 00:00:01,receive,,currency,ETH,spot,1,0,1,20,0,ETH\n"


def _trades(r):
    return sorted((t["symbol"], t["quantity"], t["net_amount"], t.get("id"))
                  for t in json.loads(r.stdout)["transactions"])


class TestKrakenInstantTradeAcrossExports(unittest.TestCase):
    """TJ-0525: an instant trade (refid R1) spending 2 SOL on Dec 31 UTC
    in the 2025 ledger export and receiving 1 ETH a second later in the
    2026 one was dropped from both as two orphan legs (UNBOOKED), though
    both exports were given together. The account's ledger exports are
    indexed by refid first, and the trade is booked once."""

    _BOOKED = [("ETH", 1.0, 20.0, "R1-buy"), ("SOL", -2.0, 20.0, "R1-sell")]

    def test_split_exports_book_the_trade(self):
        r, _side = _brokerage({"kr_2025.csv": _KL_H2 + _SPEND,
                               "kr_2026.csv": _KL_H2 + _RECV},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_trades(r), self._BOOKED)
        self.assertNotIn("UNBOOKED", r.stderr)
        self.assertNotIn("parsed to 0 transactions", r.stderr)

    def test_same_as_one_export(self):
        r, _side = _brokerage({"kr_all.csv": _KL_H2 + _SPEND + _RECV},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_trades(r), self._BOOKED)

    def test_overlapping_exports_book_it_once(self):
        from taxjson.bin.taxjson_sort import plan_dedup
        r, _side = _brokerage({"kr_2025.csv": _KL_H2 + _SPEND,
                               "kr_2026.csv": _KL_H2 + _RECV,
                               "kr_all.csv": _KL_H2 + _SPEND + _RECV},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        rows = doc["transactions"]
        plan = plan_dedup(rows, doc["metadata"].get("source_accounts"))
        kept = sorted((rows[i]["symbol"], rows[i]["quantity"],
                       rows[i]["net_amount"], rows[i]["id"])
                      for i in plan.keep)
        self.assertEqual(kept, self._BOOKED)

    def test_a_true_orphan_still_warns(self):
        other = _RECV.replace("L2,R1,", "L2,R2,")
        r, _side = _brokerage({"kr_2025.csv": _KL_H2 + _SPEND,
                               "kr_2026.csv": _KL_H2 + other}, "kraken")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: UNBOOKED: Kraken ledger refid R1***: orphan "
                      "spend row", r.stderr)
        self.assertIn("warning: UNBOOKED: Kraken ledger refid R2***: orphan "
                      "receive row", r.stderr)
        self.assertEqual(_trades(r), [])

    def test_restated_leg_is_refused(self):
        other = _SPEND.replace(",-2,0,0,20,", ",-3,0,0,30,")
        r, _side = _brokerage({"kr_a.csv": _KL_H2 + _SPEND,
                               "kr_b.csv": _KL_H2 + _RECV,
                               "kr_c.csv": _KL_H2 + other}, "kraken")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = _error_line(r)
        self.assertIn("kr_a.csv and kr_c.csv both carry ledger txid L1*** "
                      "with DIFFERENT content", line)


def _move(acct, exchange, quantity, time):
    return dict(account=acct, exchange=exchange, date="2025-03-03",
                time=time, symbol="ZZS", quantity=quantity, price=10,
                currency="USD", fee=0,
                kind="send" if quantity < 0 else "receive", ref="")


def _fees(rows):
    unmatched, pairs = crypto_sends.match_transfers(rows)
    fees = crypto_sends.network_fees(pairs, "cb", crypto_sends.Rates({}, "USD"),
                                     lambda s, d: 10, "canada")
    return unmatched, pairs, fees


@rule("CA-CRYPTO-06")
class TestSendArrivingInTwoParts(unittest.TestCase):
    """TJ-0526: 100 coins sent from Coinbase that arrived on Kraken as 95
    and 5 a minute apart were paired with the 95 alone (the one-to-one
    assignment ran first, and the split pass skipped a paired send), so
    the 5 left over became a network fee: a sale of 5 coins. A pair that
    lost coins now takes a leftover arrival that completes it."""

    def test_both_parts_are_the_arrival(self):
        unmatched, pairs, fees = _fees([
            _move("cb", "coinbase", -100, "12:00:00"),
            _move("kr", "kraken", 95, "12:01:00"),
            _move("kr", "kraken", 5, "12:02:00")])
        self.assertEqual(unmatched, [])
        self.assertEqual(len(pairs), 1)
        arrival = pairs[0][1]
        self.assertEqual(arrival["quantity"], 100)
        self.assertEqual([r["quantity"] for r in arrival["parts"]], [95, 5])
        self.assertEqual(fees, [])

    def test_a_short_arrival_still_has_its_fee(self):
        _unmatched, _pairs, fees = _fees([
            _move("cb", "coinbase", -100, "12:00:00"),
            _move("kr", "kraken", 95, "12:01:00")])
        self.assertEqual([f["quantity"] for f in fees], [5])

    def test_an_arrival_another_send_takes_is_not_borrowed(self):
        _unmatched, pairs, fees = _fees([
            _move("cb", "coinbase", -100, "12:00:00"),
            _move("cb", "coinbase", -5, "12:00:30"),
            _move("kr", "kraken", 95, "12:01:00"),
            _move("kr", "kraken", 5, "12:02:00")])
        self.assertEqual(sorted((-o["quantity"], i["quantity"])
                                for o, i in pairs), [(5, 5), (100, 95)])
        self.assertEqual([f["quantity"] for f in fees], [5])

    def test_a_part_that_overshoots_is_not_taken(self):
        _unmatched, pairs, fees = _fees([
            _move("cb", "coinbase", -100, "12:00:00"),
            _move("kr", "kraken", 95, "12:01:00"),
            _move("kr", "kraken", 6, "12:02:00")])
        self.assertEqual([i["quantity"] for _o, i in pairs], [95])
        self.assertEqual([f["quantity"] for f in fees], [5])

    def test_a_part_in_another_account_is_not_taken(self):
        _unmatched, pairs, fees = _fees([
            _move("cb", "coinbase", -100, "12:00:00"),
            _move("kr", "kraken", 95, "12:01:00"),
            _move("bx", "kraken", 5, "12:02:00")])
        self.assertEqual([i["quantity"] for _o, i in pairs], [95])
        self.assertEqual([f["quantity"] for f in fees], [5])


if __name__ == "__main__":
    unittest.main()
