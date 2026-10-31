"""Regression tests for fix round 8, group cash: T1135 cash in transit,
crypto arrivals, the repeated hour and the income lines.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule  # noqa: F401

SRC = Path(__file__).resolve().parent.parent / "src"


def _tjs(root, *args, ok=True):
    from taxjson.lib.dispatch import python_module_argv
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    env.pop("TAXJSON_TICKER_MAP", None)
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)
    if ok and r.returncode != 0:
        raise AssertionError(f"{args}: {r.stdout}\n{r.stderr}")
    return r


def _us_project(td, rows):
    root = Path(td) / "p"
    (root / "inputs" / "a").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\ncountry = "usa"\nyear = 2025\nbase_currency = "USD"\n'
        '[accounts.a]\ntype = "taxable"\n'
        '[estimate]\nother_income = 100000\n')
    (root / "ticker.map").write_text("")
    (root / "inputs" / "a" / "t.tt").write_text("\n".join(rows) + "\n")
    _tjs(root, "run", "--no-input")
    return root


# =================================================== TJ-1318 the estimate's
# payments in lieu
class TestTJ1318EstimateLines(unittest.TestCase):
    SHORT = ["BUYSELL 2025-03-03 10:00:00 SHB.US -100 USD 40 4000 0",
             "BUYSELL 2025-06-20 10:00:00 SHB.US 100 USD 30 3000 0"]

    def _estimate(self, *more):
        with tempfile.TemporaryDirectory() as td:
            root = _us_project(td, self.SHORT + list(more))
            text = _tjs(root, "estimate").stdout
            doc = json.loads(_tjs(root, "estimate", "--json").stdout)
        return [" ".join(ln.split()) for ln in text.splitlines()], \
            doc["estimate"]

    def test_paid_compensation_is_not_an_ordinary_line(self):
        lines, est = self._estimate(
            "DIVIDEND_IN_LIEU 2025-04-15 10:00:00 SHB.US -100 USD 0 -20")
        self.assertIn("Payments in lieu 0.00 [ordinary]", lines)
        self.assertIn("Paid on short sales -20.00 "
                      "[not deducted: Schedule A only]", lines)
        self.assertEqual(est["pil_paid_not_deducted"], 20.0)
        self.assertEqual(est["investment_income"], 1000.0)

    def test_control_a_received_payment_keeps_its_one_line(self):
        lines, est = self._estimate(
            "DIVIDEND_IN_LIEU 2025-08-15 10:00:00 SHB.US 100 USD 0 30")
        self.assertIn("Payments in lieu 30.00 [ordinary]", lines)
        self.assertFalse(any(ln.startswith("Paid on short") for ln in lines))
        self.assertNotIn("pil_paid_not_deducted", est)


# =============================================== TJ-1139 the .sum's income
class TestTJ1139CapitalizedCompensationInTheIncomeSummary(unittest.TestCase):
    SHORT = ["BUYSELL 2025-02-01 10:00:00 ZRS.US -30 USD 100.00 2999.00 1",
             "DIVIDEND_IN_LIEU 2025-02-20 09:30:00 ZRS.US 0 USD 0 -15.00",
             "BUYSELL {} 10:00:00 ZRS.US 30 USD 95.00 2851.00 1"]

    def _income(self, cover):
        rows = list(self.SHORT)
        rows[2] = rows[2].format(cover)
        with tempfile.TemporaryDirectory() as td:
            root = _us_project(td, rows)
            sums = [" ".join(ln.split()) for ln in
                    next(root.rglob("a.sum")).read_text().splitlines()]
            rep = json.loads((root / "work/a_report.json").read_text())
        return sums, rep["income"]

    def test_covered_inside_45_days_is_no_income(self):
        sums, inc = self._income("2025-03-01")
        self.assertEqual(inc["ticker_stats"]["ZRS.US"]["USD"]["pil"], 0.0)
        self.assertEqual(inc["capitalized_263h"], {"USD": -15.0})
        self.assertIn("CAPITALIZED, §263(h) (not in total) -15.00", sums)
        self.assertIn("TOTAL NET INCOME 0.00", sums)

    def test_control_covered_after_45_days_stays_a_payment(self):
        sums, inc = self._income("2025-06-20")
        self.assertEqual(inc["ticker_stats"]["ZRS.US"]["USD"]["pil"], -15.0)
        self.assertNotIn("capitalized_263h", inc)
        self.assertFalse(any("CAPITALIZED" in ln for ln in sums))

    def test_the_part_on_the_shares_still_short(self):
        from taxjson.bin.taxjson_sum_income import (capitalized_pil_shares,
                                                    summarize_income)
        share = capitalized_pil_shares([
            {"id": "x", "action": "DIVIDEND_IN_LIEU", "pil": -5.0,
             "capitalized_263h": 15.0}])
        self.assertEqual(share, {"x": 0.75})
        got = summarize_income(
            [{"id": "x", "date": "2025-02-20", "action": "DIVIDEND_IN_LIEU",
              "symbol": "ZRS.US", "currency": "USD", "gross_amount": -20.0}],
            capitalized=share)
        self.assertEqual(got["ticker_stats"]["ZRS.US"]["USD"]["pil"], -5.0)
        self.assertEqual(got["capitalized_263h"], {"USD": -15.0})


def _ca_cash_project(td, a, b, b_custodian=""):
    """Canada, ledger v2: a is held in the USA; `a` and `b` are the
    accounts' .tt lines (each with its opening and year-end balances)."""
    root = Path(td) / "p"
    root.mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\n'
        'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        'source_currencies = ["USD"]\nfx_cash_ledger = "v2"\n'
        '[accounts.a]\ntype = "taxable"\ncustodian_country = "USA"\n'
        f'[accounts.b]\ntype = "taxable"\n{b_custodian}')
    (root / "ticker.map").write_text("")
    for acct, text in (("a", a), ("b", b)):
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "trades.tt").write_text(text)
    _tjs(root, "run", "--no-input")
    return root


_ZERO_CAD = "CASHOPEN 2025-01-01 CAD 0 0\nCASHBAL 2025-12-31 CAD 0\n"


# ============================================ TJ-1271 the wording of cash
# in transit at the year end
class TestTJ1271YearEndTransitWording(unittest.TestCase):
    def _texts(self, a, b):
        with tempfile.TemporaryDirectory() as td:
            root = _ca_cash_project(td, a, b)
            text = _tjs(root, "t1135").stdout
            rep = json.loads(_tjs(root, "t1135", "--json").stdout)
            chk = _tjs(root, "checklist", "--run", ok=False).stdout
        return " ".join(text.split()), rep, " ".join(chk.split())

    def test_the_ledger_computed_so_it_is_not_blamed(self):
        text, rep, chk = self._texts(
            "CASHOPEN 2025-01-01 USD 100 125\n"
            "CASHMOVE 2025-12-31 USD -100 own\n"
            "CASHBAL 2025-12-31 USD 0\n" + _ZERO_CAD,
            "CASHOPEN 2025-01-01 USD 0 0\nCASHMOVE 2026-01-02 USD 100 own\n"
            "CASHBAL 2025-12-31 USD 0\n" + _ZERO_CAD)
        self.assertEqual(rep["cash_ledger_status"], "computed")
        self.assertFalse(rep["total_settled"])
        self.assertIn("a: USD cash in transit at the year end, where it was "
                      "held is open — tjs t1135 --details", text)
        self.assertIn("but where cash in transit at the year end was held is "
                      "open", text)
        self.assertNotIn("could not run", text)
        self.assertNotIn("cost is unknown", text)
        self.assertIn("cash of a was in transit at the year end", chk)
        self.assertNotIn("ledger v2 refuses", chk)

    def test_control_a_ledger_that_refuses_is_still_named(self):
        # A statement balance that does not reconcile: not computed.
        text, rep, chk = self._texts(
            "CASHOPEN 2025-01-01 USD 100 125\n"
            "CASHBAL 2025-12-31 USD 40\n" + _ZERO_CAD,
            "CASHOPEN 2025-01-01 USD 0 0\nCASHBAL 2025-12-31 USD 0\n"
            + _ZERO_CAD)
        self.assertEqual(rep["cash_ledger_status"], "not_computed")
        self.assertFalse(rep["total_settled"])
        self.assertIn("fx-cash --ledger v2", text)
        self.assertIn("ledger v2 refuses", chk)


# ================================================= TJ-1146 the base currency
# in transit
class TestTJ1146BaseCurrencyInTransit(unittest.TestCase):
    @staticmethod
    def _tt(arrival, amount="500"):
        bal = amount if arrival <= "2025-12-31" else "0"
        return ("CASHOPEN 2025-01-01 USD 0 0\nCASHBAL 2025-12-31 USD 0\n"
                f"CASHOPEN 2025-01-01 CAD {amount} {amount}\n"
                f"CASHMOVE 2025-06-01 CAD -{amount} own\n"
                "CASHBAL 2025-12-31 CAD 0\n",
                "CASHOPEN 2025-01-01 USD 0 0\nCASHBAL 2025-12-31 USD 0\n"
                "CASHOPEN 2025-01-01 CAD 0 0\n"
                f"CASHMOVE {arrival} CAD {amount} own\n"
                f"CASHBAL 2025-12-31 CAD {bal}\n")

    def _rep(self, arrival, custodian='custodian_country = "USA"\n'):
        a, b = self._tt(arrival)
        with tempfile.TemporaryDirectory() as td:
            root = _ca_cash_project(td, a, b, custodian)
            rep = json.loads(_tjs(root, "t1135", "--json").stdout)
            text = " ".join(_tjs(root, "t1135").stdout.split())
        return rep, text

    @rule("CA-RPT-20")
    def test_it_is_named_and_no_figure_moves(self):
        rep, text = self._rep("2025-06-05")
        n, = rep["cash_not_counted"]
        self.assertEqual((n["account"], n["currency"], n["transit"],
                          n["unknown"]), ("a", "CAD", True, False))
        self.assertIn("up to 500.00 CAD in transit between your own accounts "
                      "(from a to b, first on 2025-06-01)", n["why"])
        self.assertNotIn("year-end", n["why"])
        self.assertIn("a: CAD cash in transit during the year", text)
        self.assertTrue(rep["total_settled"])
        self.assertEqual(rep["max_total_cost"], 500.0)

    @rule("CA-RPT-20")
    def test_over_the_year_end_the_year_end_cost_is_said(self):
        rep, _text = self._rep("2026-01-05")
        n, = rep["cash_not_counted"]
        self.assertIn("not in the maximum or the year-end cost", n["why"])
        self.assertTrue(rep["total_settled"])

    @rule("CA-RPT-20")
    def test_control_a_same_day_move_has_no_days(self):
        a, b = self._tt("2025-06-01")
        with tempfile.TemporaryDirectory() as td:
            root = _ca_cash_project(td, a, b, 'custodian_country = "USA"\n')
            rep = json.loads(_tjs(root, "t1135", "--json").stdout)
        self.assertEqual(rep["cash_not_counted"], [])
        self.assertEqual(rep["max_total_cost"], 500.0)

    def test_control_the_walk(self):
        from taxjson.lib.fx_cash_v2 import _base_transit
        out = dict(sd="2025-06-01", td="2025-06-01", book="a/m",
                   amount=-100.0, own=True)
        back = dict(sd="2025-06-05", td="2025-06-05", book="b/m",
                    amount=60.0, own=True)
        y = ("2025-01-01", "2025-12-31", False)
        self.assertEqual(_base_transit([out, back], *y), [
            {"units": 60.0, "from": "a/m", "to": "b/m",
             "since": "2025-06-01", "until": "2025-06-05"}])
        # Not declared own, no arrival, or no account named: nothing.
        self.assertEqual(_base_transit([dict(out, own=False), back], *y), [])
        self.assertEqual(_base_transit([out], *y), [])
        # A move wholly in another year has no days in this one.
        self.assertEqual(_base_transit(
            [out, back], "2026-01-01", "2026-12-31", False), [])

    def test_control_no_counted_account_is_no_foreign_property(self):
        from taxjson.lib.t1135_cash import _base_spans
        sp = [{"units": 5.0, "from": "x/m", "to": "y/m",
               "since": "2025-06-01", "until": "2025-06-02"}]
        self.assertEqual(_base_spans(sp, {"a": "USA"}, "CAD", 2025), [])
        # Two books of one counted account: its own row holds it.
        sp = [dict(sp[0], **{"from": "a/m", "to": "a/n"})]
        self.assertEqual(_base_spans(sp, {"a": "USA"}, "CAD", 2025), [])


# ================================================ TJ-1270 rows of one second
# from two Kraken exports
class TestTJ1270KrakenAcrossExports(unittest.TestCase):
    """TJ-1270: a sale and a rebuy that share a local second, each in its
    own Kraken trades export: Coinbase's instants ordered such rows across
    files (TJ-1227), Kraken's stopped at the parser."""

    def _order(self, buy, sale, rebuy, files=("a", "b", "b")):
        from test_fix_1029_r7_kraken import _LG_H, _TR_H
        rows = [("T1", buy, "buy", 5, 50), ("T2", sale, "sell", 10, 100),
                ("T3", rebuy, "buy", 10.01, 100.1)]
        bodies = {}
        ledger = _LG_H
        for (tid, stamp, typ, price, cost), which in zip(rows, files):
            bodies[which] = bodies.get(which, _TR_H) + (
                f"{tid},O{tid},SOL/CAD,{stamp},{typ},market,{price},{cost},0,"
                f"10,0,,\n")
            sign = 1 if typ == "buy" else -1
            ledger += (f"{tid}B,{tid},{stamp},trade,,currency,SOL,{10 * sign},"
                       f"0,10\n{tid}Q,{tid},{stamp},trade,,currency,CAD,"
                       f"{-cost * sign},0,100\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "c"
            (root / "inputs" / "coin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
                'country = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = []\n'
                '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
            (root / "ticker.map").write_text("")
            for which, body in bodies.items():
                (root / "inputs/coin" / f"kraken_{which}_trades.csv"
                 ).write_text(body)
            (root / "inputs/coin/kraken_ledgers.csv").write_text(ledger)
            _tjs(root, "run", "--no-input")
            ids = [r["id"] for r in json.loads(
                (root / "work/coin_sorted.json").read_text())["transactions"]]
            gain = json.loads((root / "work/coin_gains.json").read_text())[
                "summary"]["total_gain"]
        return ids, gain

    BUY = "2025-01-06 15:00:00.1000"

    @rule("CA-DATE-12")
    def test_a_compressed_second_across_two_exports(self):
        # The rebuy's file first (a < b): file order booked the rebuy first.
        for files in (("b", "b", "a"), ("a", "a", "b")):
            with self.subTest(files=files):
                ids, gain = self._order(self.BUY, "2025-11-02 05:00:00.0000",
                                        "2025-11-02 05:00:01.0000", files)
                self.assertLess(ids.index("T2"), ids.index("T3"))
                self.assertAlmostEqual(gain, 50.0, places=2)

    @rule("CA-ACB-01")
    def test_a_fraction_of_a_second_across_two_exports(self):
        ids, gain = self._order(self.BUY, "2025-03-03 15:00:00.1000",
                                "2025-03-03 15:00:00.9000", ("b", "b", "a"))
        self.assertLess(ids.index("T2"), ids.index("T3"))
        self.assertAlmostEqual(gain, 50.0, places=2)

    def test_control_one_instant_keeps_the_file_order(self):
        ids, _gain = self._order(self.BUY, "2025-03-03 15:00:00.5000",
                                 "2025-03-03 15:00:00.5000", ("b", "b", "a"))
        self.assertLess(ids.index("T3"), ids.index("T2"))

    def test_control_different_seconds_are_untouched(self):
        ids, gain = self._order(self.BUY, "2025-03-03 15:00:00.0000",
                                "2025-03-03 15:00:05.0000", ("b", "b", "a"))
        self.assertLess(ids.index("T2"), ids.index("T3"))
        self.assertAlmostEqual(gain, 50.0, places=2)


# ================================================ TJ-0652 what an arrival is
_CB_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
           "Price Currency,Price at Transaction,Subtotal,"
           "Total (inclusive of Fees and/or Spread),Fees and/or Spread,"
           "Notes\n")


class TestTJ0652TheArrivalDecision(unittest.TestCase):
    """100 USDC bought for CAD 100, sent `self`; 100 USDC arrive later; 100
    USDC go as a gift. The rates are stated (usd_pool), so CAD 2 a dollar
    on the arrival's day."""

    def _pool(self, fresh):
        from datetime import datetime
        from taxjson.lib import crypto_sends as CS

        def row(day, qty, acct):
            return {"date": day, "time": "12:00:00", "symbol": "USDC",
                    "quantity": qty, "fee": 0, "exchange": "coinbase",
                    "account": acct, "currency": "USD", "price": 1}
        flows = [{"dt": datetime(2025, 1, 10, 12), "units": 100.0,
                  "cad": -100.0, "what": "buy", "key": None}]
        out = row("2025-01-20", -100.0, "a")
        arrive = row("2025-02-10", 100.0, "b")
        gift = row("2025-03-01", -100.0, "b")
        rows = [out, arrive, gift]
        CS.assign_arrival_ids(rows)
        rates = CS.Rates({"USD": {d: (r, "boc") for d, r in
                                  {"2025-01-20": 1.0, "2025-02-10": 2.0,
                                   "2025-03-01": 2.0}.items()}}, "CAD")
        return CS.usd_pool(flows, rows, [],
                           {id(out): "self1", id(gift): "gift1"}, rates,
                           kept={id(out)},
                           fresh={id(arrive)} if fresh else None)

    @rule("CA-CRYPTO-08")
    def test_new_money_joins_the_pool(self):
        r = self._pool(True)
        g = r["results"]["gift1"]
        self.assertEqual((g["acb"], g["gain"]), (150.0, 50.0))
        self.assertEqual(r["units_now"], 100.0)
        self.assertEqual(r["returns"], [])

    @rule("CA-CRYPTO-08")
    def test_control_the_default_takes_the_coins_back(self):
        r = self._pool(False)
        g = r["results"]["gift1"]
        self.assertEqual((g["acb"], g["gain"]), (100.0, 100.0))
        self.assertEqual(r["units_now"], 0.0)
        self.assertEqual(r["returns"][0]["id"],
                         "in-cb-20250210T120000-USDC-100")

    def _project(self, td):
        root = Path(td) / "c"
        (root / "inputs" / "coin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = ["USD"]\n'
            '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
        (root / "ticker.map").write_text("")
        (root / "inputs/coin/coinbase.csv").write_text(
            _CB_HDR
            + "c1,2025-01-10 17:00:00 UTC,Buy,USDC,100,CAD,1.40,140,140,0,"
              "Bought 100 USDC\n"
              "c2,2025-01-20 17:00:00 UTC,Send,USDC,100,CAD,1.40,140,140,0,"
              "Sent 100 USDC\n"
              "c3,2025-02-10 17:00:00 UTC,Receive,USDC,100,CAD,1.40,140,140,0,"
              "Received 100 USDC\n"
              "c4,2025-03-03 17:00:00 UTC,Send,USDC,100,CAD,1.40,140,140,0,"
              "Sent 100 USDC\n")
        _tjs(root, "run", "--no-input", ok=False)
        return root

    def _doc(self, root):
        return json.loads(_tjs(root, "crypto-sends", "--json").stdout)

    @rule("CA-CRYPTO-08")
    def test_the_command_through_sends_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            s1, s2 = (s["id"] for s in self._doc(root)["accounts"]["coin"][
                "sends"])
            _tjs(root, "crypto-sends", "coin", "--set", f"{s1}=self")
            _tjs(root, "crypto-sends", "coin", "--set", f"{s2}=gift")
            before = self._doc(root)
            self.assertEqual(before["pool"]["units_now"], 0.0)
            aid = before["pool"]["returns"][0]["id"]
            self.assertTrue(aid.startswith("in-cb-20250210T"))
            text = _tjs(root, "crypto-sends").stdout
            self.assertIn(aid, text)

            # `return` is the default, said out loud.
            _tjs(root, "crypto-sends", "coin", "--set", f"{aid}=return")
            self.assertEqual(self._doc(root)["pool"], before["pool"])

            _tjs(root, "crypto-sends", "coin", "--set", f"{aid}=new",
                 "--note", "from outside")
            saved = json.loads(
                (root / "inputs/coin/sends.json").read_text())
            self.assertEqual(saved["arrivals"][aid]["decision"], "new")
            self.assertEqual(saved["arrivals"][aid]["note"], "from outside")
            self.assertEqual(saved["sends"][s1]["decision"], "self")
            doc = self._doc(root)
            self.assertEqual(doc["pool"]["units_now"], 100.0)
            self.assertEqual(doc["pool"]["returns"], [])
            self.assertEqual(doc["accounts"]["coin"]["arrivals"][0]["id"], aid)
            self.assertIn("decided `new`", _tjs(root, "crypto-sends").stdout)

            # --unset: the default again, and the send decisions stay.
            _tjs(root, "crypto-sends", "coin", "--unset", aid)
            saved = json.loads((root / "inputs/coin/sends.json").read_text())
            self.assertNotIn("arrivals", saved)
            self.assertEqual(saved["sends"][s2]["decision"], "gift")
            self.assertEqual(self._doc(root)["pool"], before["pool"])

    def test_refusals_name_the_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            r = _tjs(root, "crypto-sends", "coin", "--set",
                     "in-cb-20250101T000000-USDC-1=new", ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no stablecoin arrival", r.stderr)
            aid = self._doc(root)["accounts"]["coin"]["arrival_ids"][0]
            r = _tjs(root, "crypto-sends", "coin", "--set", f"{aid}=gift",
                     ok=False)
            self.assertIn("expected one of new, return", r.stderr)
            # A hand-edited bad value says so.
            p = root / "inputs/coin/sends.json"
            p.write_text(json.dumps({"schema_version": 2, "sends": {},
                                     "arrivals": {aid: {"decision": "x"}}}))
            r = _tjs(root, "crypto-sends", ok=False)
            self.assertIn("expected one of new, return", r.stderr)

    def test_a_return_with_nothing_parked_and_an_orphan_are_said(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            doc = self._doc(root)
            aid = doc["accounts"]["coin"]["arrival_ids"][0]
            s2 = doc["accounts"]["coin"]["sends"][1]["id"]
            # The later send only is decided: the first is not `self`.
            _tjs(root, "crypto-sends", "coin", "--set", f"{s2}=gift")
            _tjs(root, "crypto-sends", "coin", "--set", f"{aid}=return")
            p = root / "inputs/coin/sends.json"
            saved = json.loads(p.read_text())
            saved["arrivals"]["in-cb-20240101T000000-USDC-1"] = {
                "decision": "new"}
            p.write_text(json.dumps(saved))
            out = " ".join(_tjs(root, "crypto-sends").stdout.split())
            self.assertIn(f"arrival {aid} is saved as `return`", out)
            self.assertIn("arrival in-cb-20240101T000000-USDC-1", out)


# ===================================== TJ-1145 decisions in the repeated hour
class TestTJ1145RepeatedHourDecisions(unittest.TestCase):
    """Two sends of one coin and quantity at 05:25 and 05:50 UTC on the
    fall-back day; a gift saved under v0.38.0's id of the first (01:25:00).
    Since TJ-0747 that id is the second send's. Saved keys are not moved."""

    ROWS = ("b1,2025-01-10 17:00:00 UTC,Buy,ZZE,5,CAD,10,50,50,0,Bought 5 ZZE\n"
            "s1,2025-11-02 05:25:00 UTC,Send,ZZE,1,CAD,10,10,10,0,Sent 1 ZZE\n"
            "s2,2025-11-02 05:50:00 UTC,Send,ZZE,1,CAD,10,10,10,0,Sent 1 ZZE\n")
    OLD_FIRST = "cb-20251102T012500-ZZE-1"

    def _project(self, td, rows, saved):
        root = Path(td) / "c"
        (root / "inputs" / "coin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n'
            '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
        (root / "ticker.map").write_text("")
        (root / "inputs/coin/coinbase.csv").write_text(_CB_HDR + rows)
        (root / "inputs/coin/sends.json").write_text(json.dumps(
            {"schema_version": 2, "sends": {
                k: {"decision": "gift", "price": 10.0,
                    "price_currency": "CAD"} for k in saved}}))
        _tjs(root, "run", "--no-input", ok=False)
        return root

    def test_the_moved_id_is_said_and_not_rekeyed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, self.ROWS, [self.OLD_FIRST])
            before = (root / "inputs/coin/sends.json").read_text()
            doc = json.loads(_tjs(root, "crypto-sends", "--json").stdout)
            acct = doc["accounts"]["coin"]
            # The bug: the saved id is now the later send's.
            self.assertEqual(
                {s["id"]: s["decision"] for s in acct["sends"]},
                {"cb-20251102T011230-ZZE-1": None, self.OLD_FIRST: "gift"})
            self.assertEqual(acct["repeated_hour"], [self.OLD_FIRST])
            out = " ".join(_tjs(root, "crypto-sends").stdout.split())
            self.assertIn("1 saved decision(s) are of a send in the hour the "
                          "clocks repeat", out)
            self.assertIn(self.OLD_FIRST, out)
            self.assertEqual((root / "inputs/coin/sends.json").read_text(),
                             before)

    def test_control_another_hour_of_that_day_says_nothing(self):
        rows = self.ROWS.replace("05:25:00", "09:25:00").replace(
            "05:50:00", "09:50:00")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, rows, ["cb-20251102T042500-ZZE-1"])
            acct = json.loads(_tjs(root, "crypto-sends", "--json").stdout)[
                "accounts"]["coin"]
            self.assertEqual(acct["repeated_hour"], [])
            self.assertEqual(
                {s["id"]: s["decision"] for s in acct["sends"]},
                {"cb-20251102T042500-ZZE-1": "gift",
                 "cb-20251102T045000-ZZE-1": None})
            self.assertNotIn("clocks repeat",
                             _tjs(root, "crypto-sends").stdout)

    def test_control_the_filter(self):
        from taxjson.lib.crypto_sends import repeated_hour_decisions as f
        tz = "America/Toronto"
        ids = ["cb-20251102T012500-ZZE-1", "cb-20251102T012500-ZZE-1-fee",
               "cb-20251102T012500-ZZE-1-2",
               # The spring day, an ordinary day, a later fall-back
               # (no decision can predate the release), a bad stamp.
               "cb-20250309T023000-ZZE-1", "cb-20250612T012500-ZZE-1",
               "cb-20261101T012500-ZZE-1", "cb-20251102T999999-ZZE-1"]
        self.assertEqual(f(ids, tz), sorted(ids[:3]))
        # No zone named, or a zone with no repeated hour on that day.
        self.assertEqual(f(ids, None), [])
        self.assertEqual(f(ids, "UTC"), [])


if __name__ == "__main__":
    unittest.main()
