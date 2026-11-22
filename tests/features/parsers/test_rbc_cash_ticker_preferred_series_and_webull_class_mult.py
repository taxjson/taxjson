"""Parser, import and dedup fixes of fix round 7 (wave C, group
parsers2): RBC holdings with a CASH ticker, dotted preferred series L
and V, the generic importer's fee-only and zero-net rows and its named
brokers, a Webull class-share MULT, a mapped warrant exercise target
and dedup keeping a copy's income facts. Every fixture is SYNTHETIC:
fake account ids (99900001, U1234567), invented tickers.

Formerly: test_fix_1029_r7c_parsers2
"""
import csv
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from _qa_project import project, tj
from taxjson.lib.dispatch import python_module_argv
import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
import _paths

REPO = _paths.REPO
ENV = _hermetic.environ(TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
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
    security is that holding, not a cash balance.

    Formerly: test_fix_1029_r7c_parsers2.TestRbcCashTicker"""

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
    London or Venture suffix.

    Formerly: test_fix_1029_r7c_parsers2.TestPreferredSeriesSuffix"""

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
        from _support.ib_statement_rows import HEAD, TRADES_H, _trade
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
        from _support.parser_qt_rbc_rows import QH, q
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


class TestWebullClassMult(unittest.TestCase):
    """TJ-0973: MULT on a class share (SYN.B) sizes its options.

    Formerly: test_fix_1029_r7c_parsers2.TestWebullClassMult"""

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


if __name__ == "__main__":
    unittest.main()
