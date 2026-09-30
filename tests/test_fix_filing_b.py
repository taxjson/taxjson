"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sell(symbol="AAPL.US", date="2025-05-02", qty=-100, proceeds=12000.0,
          cost=10000.0, direction="LONG", **extra):
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
         "disallowed_amount": 0.0, "days_held": 100,
         "direction": direction, "commission": 0.0, "fee": 0.0,
         "account": "margin"}
    e.update(extra)
    return e


def _gains(td, entries, name="margin_gains.json", **summary):
    p = Path(td) / name
    p.write_text(json.dumps({"transactions": entries,
                             "summary": summary}))
    return p


def _slip(td, text, name="t5008.csv", encoding="utf-8"):
    p = Path(td) / name
    p.write_bytes(text.encode(encoding))
    return p


def _reconcile(td, slip_text, entries, *extra, encoding="utf-8"):
    from taxjson.bin.taxjson_reconcile_slips import main
    s = _slip(td, slip_text, encoding=encoding)
    g = _gains(td, entries)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(s), "--gains", str(g), "--year", "2025",
                     "--json", *extra])
    rep = json.loads(out.getvalue()) if out.getvalue().strip() else None
    return code, rep, err.getvalue()


def _by(rep):
    return {r["symbol"]: r for r in rep["rows"]}


class TestReconcileSlipRows(unittest.TestCase):
    def test_blank_box21_is_nil_proceeds(self):
        """R1-17: blank box 21 beside a box 20 = expired worthless."""
        opt = "XYZ250117C00100000.US"
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, f"Symbol,Box 16,Box 21,Box 20\n{opt},2,,1000.00\n",
                [_sell(symbol=opt, qty=-2, proceeds=0.0, cost=1000.0)])
        self.assertEqual(code, 0, (rep, err))
        self.assertNotIn("unreadable", err)

    def test_blank_symbol_with_amount_is_unreadable(self):
        """R1-201: a CUSIP-only row with proceeds is not skipped silently."""
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\nAAA,1000,12000.00\n"
                    ",500,25000.00\n",
                [_sell(symbol="AAA.TO", qty=-1000, proceeds=12000.0)])
        self.assertEqual(code, 1)
        self.assertEqual(rep["unreadable_rows"], 1)
        self.assertIn("no symbol", err)

    def test_unreadable_quantity_is_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\nAAA,200 sh,12000.00\n",
                [_sell(symbol="AAA.TO", qty=-1000, proceeds=12000.0)])
        self.assertEqual(code, 1)
        self.assertEqual(rep["unreadable_rows"], 1)

    def test_fully_blank_row_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\nAAA,1000,12000.00\n,,\n",
                [_sell(symbol="AAA.TO", qty=-1000, proceeds=12000.0)])
        self.assertEqual(code, 0, (rep, err))


class TestReconcileGrantPair(unittest.TestCase):
    def test_grant_write_and_buyback_count_once(self):
        """R1-18: a WRITE record plus its buy-back is ONE slip row."""
        opt = "AA250620P00022500.US"
        write = _sell(symbol=opt, qty=5, proceeds=0.0, cost=651.59,
                      direction="SHORT", grant=True)
        close = _sell(symbol=opt, qty=5, proceeds=-160.59, cost=0.0,
                      direction="SHORT")
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Box 16,Box 21,Box 20\n"
                    f"{opt},5,651.59,160.59\n", [write, close])
        self.assertEqual(code, 0, rep)

    def test_open_write_still_counts(self):
        from taxjson.bin.taxjson_reconcile_slips import load_computed
        opt = "AA250620P00022500.US"
        with tempfile.TemporaryDirectory() as td:
            out = load_computed([_gains(td, [_sell(
                symbol=opt, qty=5, proceeds=0.0, cost=651.59,
                direction="SHORT", grant=True)])], 2025)
        self.assertAlmostEqual(out["AA250620P00022500"]["qty"], 5.0)


class TestReconcileSymbols(unittest.TestCase):
    def test_ticker_map_renames_slip_symbols(self):
        """R1-19: the books carry K / K...TO (TOBASE KGC.US K.TO)."""
        opt = "K270115C00012000.TO"
        with tempfile.TemporaryDirectory() as td:
            tm = Path(td) / "ticker.map"
            tm.write_text("TOBASE KGC.US K.TO\n")
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\n"
                    "KGC270115C00012000,1,6979.10\nKGC,100,1500.00\n",
                [_sell(symbol=opt, qty=-1, proceeds=6979.10),
                 _sell(symbol="K.TO", qty=-100, proceeds=1500.0)],
                "--ticker-map", str(tm))
        self.assertEqual(code, 0, (rep, err))

    def test_broker_symbol_forms(self):
        """R1-209: share classes and broker option descriptions."""
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\n"
                    "BRK B,10,5000.00\n"
                    "XYZ 21MAR25 50 C,1,300.00\n"
                    "PUT ABC04/17/25 22.5,2,100.00\n",
                [_sell(symbol="BRK.B.US", qty=-10, proceeds=5000.0),
                 _sell(symbol="XYZ250321C00050000.US", qty=-1,
                       proceeds=300.0),
                 _sell(symbol="ABC250417P00022500.US", qty=-2,
                       proceeds=100.0)])
        self.assertEqual(code, 0, (rep, err))

    def test_utf16_and_french_headers(self):
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "﻿Symbole,Quantité,Produit de disposition\n"
                    "AAA,100,1500.00\n",
                [_sell(symbol="AAA.TO", qty=-100, proceeds=1500.0)],
                encoding="utf-16")
        self.assertEqual(code, 0, (rep, err))

    def test_two_listings_of_one_root_are_not_folded(self):
        """R1-292: DLR.TO and DLR.US are different securities."""
        comp = [_sell(symbol="DLR.TO", qty=-95, proceeds=1050.0),
                _sell(symbol="DLR.US", qty=-15, proceeds=2750.0)]
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\nDLR,110,3800.00\n", comp)
        self.assertEqual(code, 1)
        self.assertEqual(_by(rep)["DLR"]["status"], "AMBIGUOUS_LISTING")
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\nDLR.TO,100,1300.00\n"
                    "DLR.US,10,2500.00\n", comp)
        self.assertEqual(code, 1)
        by = _by(rep)
        self.assertEqual(by["DLR.TO"]["status"], "MISMATCH")
        self.assertEqual(by["DLR.US"]["status"], "MISMATCH")

    def test_single_listing_still_matches_bare_symbol(self):
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity,Proceeds\nAAPL,100,12000.00\n",
                [_sell()])
        self.assertEqual(code, 0, rep)

    def test_worthless_expiry_without_slip_row_is_not_a_failure(self):
        """R1-1: IB issues no T5008 row for a long option that expired."""
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Identification of securities,Quantity of securities,"
                    "Proceeds of disposition or settlement amount\n"
                    "AAPL,100,12000.00\n",
                [_sell(), _sell(symbol="XYZ250117C00100000.US", qty=-2,
                                proceeds=0.0, cost=500.0)])
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["counts"]["no_slip_expected"], 1)


class TestReconcileHeaders(unittest.TestCase):
    def test_box23_label_does_not_steal_quantity(self):
        """S036-02: exact 'Quantity' wins over box 23's longer label."""
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Quantity of securities received on settlement,"
                    "Quantity,Proceeds of disposition\n"
                    "XYZ,,200,1000.00\n",
                [_sell(symbol="XYZ.TO", qty=-100, proceeds=1000.0)])
        self.assertEqual(code, 1)
        self.assertIn("quantity off", _by(rep)["XYZ"]["detail"])

    def test_two_proceeds_columns_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            code, rep, err = _reconcile(
                td, "Symbol,Proceeds (USD),Proceeds (CAD)\n"
                    "XYZ,700,1000.00\n",
                [_sell(symbol="XYZ.TO", qty=-100, proceeds=1000.0)])
        self.assertEqual(code, 2)
        self.assertIn("ambiguous header", err)

    def test_cost_basis_method_does_not_take_cost(self):
        from taxjson.bin.taxjson_reconcile_slips import _map_headers
        cols = _map_headers(["Symbol", "Proceeds", "Cost Basis Method",
                             "Cost Basis"])
        self.assertEqual(cols["cost"], "Cost Basis")


def _toml(root, year=2025, extra_settings="", accounts=None):
    accounts = accounts or [("margin", "taxable", "")]
    t = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
         f'base_currency = "CAD"\n{extra_settings}')
    for n, ty, more in accounts:
        t += f'[accounts.{n}]\ntype = "{ty}"\n{more}'
    (root / "taxjson.toml").write_text(t)


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


class TestReconcileStaleBooks(unittest.TestCase):
    def test_year_mismatch_names_the_cause(self):
        """S047-24 / S049-06: books for 2026, project year 2025."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _toml(root, 2025)
            (root / "work").mkdir()
            _gains(root / "work", [_sell(date="2026-03-02")],
                   name="margin_gains_wash.json", year=2026)
            slip = _slip(td, "Symbol,Proceeds\nAAPL,12000.00\n")
            r = _cli(root, "reconcile-slips", str(slip))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("built for another tax year", r.stderr)
        self.assertNotIn("MISSING_FROM_COMPUTED", r.stdout)


if __name__ == "__main__":
    unittest.main()
