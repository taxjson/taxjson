"""Parser, import and dedup fixes of fix round 7 (wave C, group
parsers2): RBC holdings with a CASH ticker, dotted preferred series L
and V, the generic importer's fee-only and zero-net rows and its named
brokers, a Webull class-share MULT, a mapped warrant exercise target
and dedup keeping a copy's income facts. Every fixture is SYNTHETIC:
fake account ids (99900001, U1234567), invented tickers.
"""
import csv
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _qa_project import project, tj
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
           PYTHONPATH=str(REPO / "src"), NO_COLOR="1")


def _brokerage(td, brokerage, files, *extra):
    """taxjson-brokerage over {name: text} written in `td`: (rc, json or
    None, stderr)."""
    paths = []
    for name, text in files.items():
        p = Path(td) / name
        p.write_text(text, encoding="utf-8")
        if name.endswith(".csv"):
            paths.append(str(p))
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_brokerage",
                           ["--brokerage", brokerage, *extra, *paths]),
        capture_output=True, text=True, env=ENV,
        stdin=subprocess.DEVNULL, check=False)
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        out = None
    return r.returncode, out, r.stderr


def _totals(root):
    """`tjs sum --json`'s filing totals of a project."""
    return json.loads(tj(root, "sum", "--json").stdout)["filing"]["totals"]


def _toml_2025(root):
    """The project's year set to 2025 (the fixtures' trades)."""
    p = root / "taxjson.toml"
    p.write_text(p.read_text().replace("year = 2024", "year = 2025")
                 .replace("option_grant_timing_since = 2024",
                          "option_grant_timing_since = 2025"))


class TestRbcCashTicker(unittest.TestCase):
    """TJ-0968: CASH is a listed ticker; a row whose Product names a
    security is that holding, not a cash balance."""

    HOLD = ("Holdings Export as of Dec 31, 2025\n"
            "Account,Product,Symbol,Name,Quantity,Currency,"
            "Total Book Cost,Total Market Value\n"
            "99900001,Equities,CASH,SYNTHETIC ETF,10,CAD,100,200\n"
            "99900001,Cash,CASH,CASH,,CAD,,\n")

    def test_equity_named_cash_is_a_position(self):
        from taxjson.lib.positions_reports import read_positions
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hold.csv"
            p.write_text(self.HOLD)
            rep = read_positions(p)
        self.assertEqual([(r.symbol, r.quantity, r.cost) for r in rep.rows],
                         [("CASH.TO", 10.0, 100.0)])

    def test_cash_row_without_product_column_stays_cash(self):
        from taxjson.lib.positions_reports import read_positions
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hold.csv"
            p.write_text("Holdings Export as of Dec 31, 2025\n"
                         "Symbol,Quantity,Currency,Total Book Cost\n"
                         "SYN,10,CAD,100\nCASH,500,CAD,500\n")
            rep = read_positions(p)
        self.assertEqual([r.symbol for r in rep.rows], ["SYN.TO"])


class TestPreferredSeriesSuffix(unittest.TestCase):
    """TJ-0970: a dotted preferred series' L or V is the series, not a
    London or Venture suffix."""

    def test_shared_helper_keeps_the_series(self):
        from taxjson.lib.brokerages.base import (BaseBrokerage,
                                                 canonical_ca_listing)
        b = BaseBrokerage()
        for sym, want in (("SYN.PR.L", "SYN.PR.L.TO"),
                          ("SYN.PR.V", "SYN.PR.V.TO"),
                          ("SYN.PR.L.TO", "SYN.PR.L.TO"),
                          ("SYN.PRL", "SYN.PR.L.TO"),
                          ("SYN.PR.CN", "SYN.PR.TO"),
                          ("SYN.V", "SYN.TO")):
            self.assertEqual(b.apply_currency_suffix(sym, "CAD"), want, sym)
        self.assertIsNone(canonical_ca_listing("SYN.PR.V", "CAD"))

    def test_ib_trade_keeps_series_l(self):
        from test_fix_ibparse import HEAD, TRADES_H, _trade
        text = HEAD + TRADES_H + _trade("SYN.PR.L", "2025-01-02, 10:00:00",
                                        10, 10, -100, cur="CAD",
                                        acct="U1234567")
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = _brokerage(td, "ib", {"ib.csv": text}, "--strict")
        self.assertEqual(rc, 0, err)
        self.assertEqual([t["symbol"] for t in out["transactions"]],
                         ["SYN.PR.L.TO"])

    def test_generic_keeps_series_v(self):
        mapping = ('[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
                   'quantity="Quantity"\nprice="Price"\namount="Amount"\n'
                   '[defaults]\ncurrency="CAD"\n[actions]\nBUY="buy"\n')
        csv_text = ("Date,Action,Symbol,Quantity,Price,Amount\n"
                    "2025-01-02,BUY,SYN.PR.V,10,10,-100\n"
                    "2025-01-03,BUY,SYN.PR.L,10,10,-100\n")
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = _brokerage(td, "generic", {
                "generic.csv": csv_text, "generic.toml": mapping},
                "--strict")
        self.assertEqual(rc, 0, err)
        self.assertEqual([t["symbol"] for t in out["transactions"]],
                         ["SYN.PR.V.TO", "SYN.PR.L.TO"])

    def test_questrade_run_keeps_two_series_apart(self):
        from test_fix_rbcqt import QH, q
        rows = []
        for day, series, qty, price in (("2025-01-02", "L", 10, 10),
                                        ("2025-02-03", "V", 10, 20),
                                        ("2025-06-02", "L", -5, 30)):
            rows.append(q(td=day, action="Buy" if qty > 0 else "Sell",
                          sym="SYN.PR." + series,
                          desc="SYN PREFERRED SERIES " + series,
                          qty=str(qty), price=str(price),
                          gross=str(-qty * price), comm="0",
                          net=str(-qty * price), cur="CAD"))
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/questrade.csv": QH + "".join(rows)})
            _toml_2025(root)
            tj(root, "run", "--strict", "--no-input")
            t = _totals(root)
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                         (150.0, 50.0, 100.0))


_GENERIC_FEE_MAP = ('[columns]\ndate="Date"\naction="Action"\n'
                    'symbol="Symbol"\nquantity="Quantity"\nprice="Price"\n'
                    'fee="Fee"\n[defaults]\ncurrency="CAD"\n'
                    '[actions]\nBUY="buy"\n')


class TestGenericUnmappedFee(unittest.TestCase):
    """TJ-0972: an unmapped row whose only cash is its mapped fee is
    UNBOOKED and fails lint."""

    def _run(self, *extra):
        text = ("Date,Action,Symbol,Quantity,Price,Fee\n"
                "2025-01-02,BUY,SYN.TO,10,10,0\n"
                "2025-02-03,SERVICE,CASH,0,0,100\n")
        with tempfile.TemporaryDirectory() as td:
            return _brokerage(td, "generic", {
                "generic.csv": text, "generic.toml": _GENERIC_FEE_MAP},
                "--country", "canada", *extra)

    def test_fee_only_row_is_unbooked(self):
        rc, _out, err = self._run("--strict")
        self.assertIn("UNBOOKED", err)
        self.assertIn("action 'SERVICE' (not in [actions]) carries a "
                      "quantity/amount/fee", err)

    def test_lint_fails(self):
        rc, _out, err = self._run("--lint")
        self.assertEqual(rc, 1, err)


class TestGenericZeroNetSale(unittest.TestCase):
    """TJ-0989: a sale whose stated net is 0 infers its commission (no
    fee column, large fees allowed)."""

    MAP = ('[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
           'quantity="Quantity"\nprice="Price"\namount="Amount"\n'
           '[defaults]\ncurrency="CAD"\n[actions]\nSELL="sell"\nBUY="buy"\n'
           '[options]\nallow_large_fees=true\n')

    def _parse(self, rows, mapping=None):
        text = "Date,Action,Symbol,Quantity,Price,Amount\n" + rows
        with tempfile.TemporaryDirectory() as td:
            return _brokerage(td, "generic", {
                "generic.csv": text, "generic.toml": mapping or self.MAP},
                "--strict")

    def test_zero_net_sale_infers_the_fee(self):
        rc, out, err = self._parse("2025-06-02,SELL,SYN.TO,1,2,0\n")
        self.assertEqual(rc, 0, err)
        t = out["transactions"][0]
        self.assertEqual((t["net_amount"], t["fee"]), (0.0, 2.0))

    def test_without_the_opt_in_it_is_refused(self):
        rc, _out, err = self._parse(
            "2025-06-02,SELL,SYN.TO,1,2,0\n",
            self.MAP.replace("allow_large_fees=true", "allow_large_fees=false"))
        self.assertNotEqual(rc, 0)
        self.assertIn("allow_large_fees", err)

    def test_zero_amount_buy_still_refused(self):
        rc, _out, err = self._parse("2025-06-02,BUY,SYN.TO,1,2,0\n")
        self.assertNotEqual(rc, 0)
        self.assertIn("the amount cell is 0", err)


class TestGenericNamedBrokers(unittest.TestCase):
    """TJ-0975: one account number at two named brokers is two
    accounts; their identical trades are both booked."""

    MAP = ('[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
           'quantity="Quantity"\nprice="Price"\namount="Amount"\n'
           '[defaults]\ncurrency="CAD"\n[actions]\nBUY="buy"\nSELL="sell"\n'
           '[broker]\nname="{broker}"\naccount="99900001"\n')
    CSV = ("Date,Action,Symbol,Quantity,Price,Amount\n"
           "2025-01-02,BUY,SYN.TO,10,10,-100\n"
           "2025-06-02,SELL,SYN.TO,-10,20,200\n")

    def test_run_books_both_brokers(self):
        files = {}
        for b in ("alpha", "beta"):
            files[f"margin/generic_{b}.csv"] = self.CSV
            files[f"margin/generic_{b}.csv.toml"] = self.MAP.format(broker=b)
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", files)
            _toml_2025(root)
            tj(root, "run", "--strict", "--no-input")
            t = _totals(root)
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                         (400.0, 200.0, 200.0))

    def test_plan_dedup_named_brokers(self):
        from taxjson.bin.taxjson_sort import plan_dedup
        rows = [{"id": "x1", "source": "a.csv", "source_account": "h1",
                 "date": "2025-01-02"},
                {"id": "x1", "source": "b.csv", "source_account": "h1",
                 "date": "2025-01-02"}]
        self.assertEqual(plan_dedup(rows, {}).drop, [1])
        plan = plan_dedup(rows, {}, {"a.csv": "generic:alpha",
                                     "b.csv": "generic:beta"})
        self.assertEqual(plan.drop, [])
        plan = plan_dedup(rows, {}, {"a.csv": "generic:alpha",
                                     "b.csv": "generic:alpha"})
        self.assertEqual(plan.drop, [1])


class TestWebullClassMult(unittest.TestCase):
    """TJ-0973: MULT on a class share (SYN.B) sizes its options."""

    def test_class_root_mult_pairs_the_assignment(self):
        rows = [["Currency", "Date", "Action Code", "Symbol",
                 "Security Description", "Type Code", "Quantity", "Price",
                 "Proceeds"],
                ["CAD", "2025-07-18", "SELL", "@SYN.B",
                 "CALL SYN.B07/18/25 5", "OPC", -1, 0, 0],
                ["CAD", "2025-07-21", "BUY", "SYN.B", "SYN SHARES", "SHS",
                 10, 5, -55]]
        s = io.StringIO()
        csv.writer(s).writerows(rows)
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "ticker.map"
            m.write_text("MULT SYN.B 10\n")
            rc, out, err = _brokerage(
                td, "webull", {"webull.csv": s.getvalue()}, "--country",
                "canada", "--strict", "--exercise-fee", "5",
                "--ticker-map", str(m))
        self.assertEqual(rc, 0, err)
        self.assertEqual([t["action"] for t in out["transactions"]],
                         ["ASSIGN", "ASSIGN"])

    def test_option_contract_size_lookup_order(self):
        from taxjson.lib import markets
        o = markets.overrides()
        saved = dict(o.mult)
        try:
            o.mult.clear()
            o.mult.update({"SYN.B": 10.0, "SYN": 50.0})
            self.assertEqual(markets.option_contract_size(
                "SYN.B250718C00005000.TO", "SYN.B.TO"), 10.0)
            self.assertEqual(markets.option_contract_size(
                "SYN.C250718C00005000.TO", "SYN.C.TO"), 50.0)
        finally:
            o.mult.clear()
            o.mult.update(saved)


class TestMappedWarrantTarget(unittest.TestCase):
    """TJ-0978: a GLOBAL rename maps a warrant exercise's target with
    the share rows."""

    def test_apply_mapping_maps_exercise_of(self):
        from taxjson.bin.taxjson_ticker_map import apply_mapping
        from taxjson.lib.core import TaxTransaction
        tx = TaxTransaction(action="ASSIGN", date="2025-06-02",
                            time="10:00:00", symbol="SYN.WT.TO",
                            quantity=-10, currency="CAD", price=0,
                            net_amount=0, gross_amount=0, account="margin",
                            exercise_of="SYN.TO")
        apply_mapping(tx, {"SYN.TO": "NEW.TO"})
        self.assertEqual(tx.exercise_of, "NEW.TO")

    def test_run_realizes_no_loss_on_exercise(self):
        tt = ("BUYSELL 2025-01-02 10:00:00 SYN.WT.TO 10 CAD 1 10 0\n"
              "ASSIGN 2025-06-02 10:00:00 SYN.WT.TO -10 CAD 0 0 "
              "exercise_of=SYN.TO\n"
              "BUYSELL 2025-06-02 10:00:00 SYN.TO 10 CAD 5 50 0\n"
              "BUYSELL 2026-09-02 10:00:00 SYN.TO -10 CAD 10 100 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/book.tt": tt},
                           ticker_map="GLOBAL SYN.TO NEW.TO\n")
            _toml_2025(root)
            tj(root, "run", "--strict", "--no-input")
            t = _totals(root)
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]), (0, 0, 0))


_IB_HEAD = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,{broker}\n"
            "Statement,Data,Title,Activity Statement\n")
_IB_PIL = ("Dividends,Header,Currency,Account,Date,Description,Amount\n"
           "Dividends,Data,CAD,U1234567,2025-06-02,SYN(CA9990000001) "
           "Payment in Lieu of Dividend CAD 1 per Share,100\n")


class TestDedupKeepsIncomeFacts(unittest.TestCase):
    """TJ-0980: the kept copy of a duplicate keeps an income fact only
    the dropped copy states."""

    def test_plan_fills_missing_fact(self):
        from taxjson.bin.taxjson_sort import deduplicate
        from taxjson.lib.core import TaxTransaction

        def row(src, dealer):
            return TaxTransaction(
                id="x1", action="DIVIDEND_IN_LIEU", date="2025-06-02",
                time="", symbol="SYN.TO", quantity=0, currency="CAD",
                price=0, net_amount=100, gross_amount=100, account="m",
                source=src, dealer_country=dealer)
        kept = deduplicate([row("a.csv", ""), row("b.csv", "CA")])
        self.assertEqual([(t.source, t.dealer_country) for t in kept],
                         [("a.csv", "CA")])
        kept = deduplicate([row("a.csv", "US"), row("b.csv", "CA")])
        self.assertEqual([t.dealer_country for t in kept], ["US"])

    def test_run_keeps_the_canadian_dealer(self):
        files = {"margin/a_plain.csv": _IB_HEAD.format(
                     broker="Interactive Brokers") + _IB_PIL,
                 "margin/b_full.csv": _IB_HEAD.format(
                     broker="Interactive Brokers Canada Inc.") + _IB_PIL}
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", files)
            _toml_2025(root)
            tj(root, "run", "--strict", "--no-input")
            s = json.loads(tj(root, "sum", "--json").stdout)
        self.assertEqual(s["totals"].get("dividend"), 100.0)
        self.assertFalse(s["totals"].get("pil"))


if __name__ == "__main__":
    unittest.main()
