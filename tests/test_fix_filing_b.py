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
from tax_rules import rule

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
                     "--json", "--country", "canada", *extra])
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


# ----------------------------------------------------- end-to-end books
_TT_MARGIN = ("BUYSELL 2025-01-10 10:00:00 ZZZ.TO 100 CAD 30.00 -3000.00 0.00\n"
              "BUYSELL 2025-03-10 10:00:00 ZZZ.TO -100 CAD 40.00 4000.00 0.00\n")
_TT_CASH = ("BUYSELL 2025-01-10 10:00:00 YYY.TO 100 CAD 30.00 -3000.00 0.00\n"
            "BUYSELL 2025-06-10 10:00:00 YYY.TO -100 CAD 50.00 5000.00 0.00\n")


def _tt_project(root, year=2025, cash=_TT_CASH, extra_settings="",
                instalments=False):
    settings = (f"source_currencies = []\noption_grant_timing_since = 2025\n"
                f'province = "ON"\n{extra_settings}')
    _toml(root, year, settings, [("margin", "taxable", ""),
                                 ("cash", "taxable", "")])
    if instalments:
        with (root / "taxjson.toml").open("a") as f:
            f.write('[instalments]\nbasis = "current_year"\n')
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "cash").mkdir(parents=True)
    (root / "inputs" / "margin" / "m.tt").write_text(_TT_MARGIN)
    (root / "inputs" / "cash" / "c.tt").write_text(cash)


class TestPartialBooksRefused(unittest.TestCase):
    """S006-03, S045-21, S006-07: a taxable account whose stage failed
    is not silently left out of t1135 / form-export / close-year."""

    def test_filing_commands_refuse_partial_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, cash="BUYSELL 2025-13-45 10:00:00 YYY.TO "
                                   "100 CAD 30.00 -3000.00 0.00\n")
            r = _cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((root / "work" / "margin_base.json").exists())
            for cmd in ("t1135", "form-export", "close-year"):
                r = _cli(root, cmd)
                self.assertNotEqual(r.returncode, 0, (cmd, r.stdout))
                self.assertIn("cash", r.stderr, cmd)
                self.assertIn("did not build", r.stderr, cmd)
            self.assertFalse((root / "filed").exists())
            s = _cli(root, "sum")
            self.assertIn("not the clean result", s.stderr)


class TestCorruptWorkFile(unittest.TestCase):
    """R1-277, S004-09, S005-05, S007-04, S005-04, S046-19."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _tt_project(cls.root, instalments=True)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _truncate(self, name):
        p = self.root / "work" / name
        keep = p.read_bytes()
        p.write_bytes(keep[:len(keep) // 2])
        self.addCleanup(p.write_bytes, keep)

    def test_truncated_gains_file_fails_filing_commands(self):
        self._truncate("margin_gains_wash.json")
        for cmd in ("sum", "estimate", "instalments", "t1135",
                    "form-export", "close-year"):
            r = _cli(self.root, cmd)
            self.assertNotEqual(r.returncode, 0, (cmd, r.stdout[-500:]))
            self.assertIn("margin_gains_wash.json", r.stderr, cmd)
            self.assertNotIn("Traceback", r.stderr, cmd)
        self.assertFalse((self.root / "filed").exists())

    def test_truncated_native_file_fails_fx_cash(self):
        self._truncate("margin_raw.json")
        r = _cli(self.root, "fx-cash")
        self.assertNotEqual(r.returncode, 0)
        s = _cli(self.root, "sum")
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("FX-on-cash (line 15300) estimate omitted", s.stderr)


class TestInstalmentsCaveats(unittest.TestCase):
    def test_estimate_warnings_are_relayed(self):
        """S047-22: books built for 2025, project year bumped to 2026."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, instalments=True)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                t.replace("year = 2025", "year = 2026"))
            r = _cli(root, "instalments")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("built for another tax year", r.stderr)

    def test_vintage_note_for_an_old_year(self):
        """S043-16: a 2023 schedule says it used later tables."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, year=2023, instalments=True,
                        cash=_TT_CASH.replace("2025", "2023"))
            (root / "inputs" / "margin" / "m.tt").write_text(
                _TT_MARGIN.replace("2025", "2023"))
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "instalments")
            j = _cli(root, "instalments", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("predates the earliest built-in rate", r.stdout)
        self.assertTrue(json.loads(j.stdout)["vintage_notes"])


class TestRunStateSurfaced(unittest.TestCase):
    """R1-252, S048-21, S048-23, S049-11: books older than the inputs
    (a failed rerun) are flagged by the report commands and not
    locked."""

    def test_changed_inputs_banner_and_lock_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            (root / "inputs" / "margin" / "m2.tt").write_text(
                "BUYSELL 2025-04-10 10:00:00 QQQ.TO 10 CAD 1.00 -10.00 "
                "0.00\n")
            for cmd in ("sum", "form-export", "t1135", "carryover"):
                r = _cli(root, cmd)
                self.assertIn("not the clean result", r.stderr, cmd)
            j = json.loads(_cli(root, "sum", "--json").stdout)
            self.assertTrue(j["run_state_problems"])
            r = _cli(root, "close-year")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("inputs changed", r.stderr)
            self.assertFalse((root / "filed").exists())
            r = _cli(root, "close-year", "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("WARNING: locking books", r.stderr)
            r = _cli(root, "check-filed")
            self.assertIn("not the clean result", r.stderr)

    @rule("CA-ACB-03")
    def test_unblended_books_are_not_locked(self):
        """S046-01: only `run --account` ran — no blended pass."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            for a in ("margin", "cash"):
                self.assertEqual(
                    _cli(root, "run", "--account", a,
                         "--no-input").returncode, 0)
            r = _cli(root, "close-year")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unblended", r.stderr)


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if qty > 0 else gross
    return (f"{trade} 10:00:00 AM,{settle} 12:00:00 AM,{action},{sym},"
            f"{sym} DESC,{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,"
            f"55500001,Trades,Individual\n")  # pii-ok


@rule("CA-DATE-02")
class TestTradeBasisProject(unittest.TestCase):
    """R1-192, R1-200, S052-10: a Canada project on tax_date = "trade"
    — the Dec-31 sale settling in January belongs to 2025 everywhere."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._td.name)
        _toml(root, 2025, 'source_currencies = []\ntax_date = "trade"\n'
                          'option_grant_timing_since = 2025\n',
              [("margin", "taxable", "")])
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "qt.csv").write_text(
            _QT_HEADER
            + _qt("2025-03-03", "2025-03-04", "Buy", "ZZZ.TO", 100, 10.0)
            + _qt("2025-06-02", "2025-06-03", "Sell", "ZZZ.TO", -50, 13.0)
            + _qt("2025-12-31", "2026-01-02", "Sell", "ZZZ.TO", -50, 14.0))
        # T1135 needs foreign property: classify the CAD test symbol.
        (root / "t1135.map").write_text("ZZZ.TO USA\n")
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule("CA-DISP-03")
    def test_form_export_and_sum_keep_the_year_end_sale(self):
        fe = json.loads(_cli(self.root, "form-export", "--json").stdout)
        self.assertAlmostEqual(fe["totals"]["gain_all"], 350.0, places=2)
        sm = json.loads(_cli(self.root, "sum", "--json").stdout)
        self.assertAlmostEqual(sm["filing"]["totals"]["gain"], 350.0,
                               places=2)

    @rule("CA-RPT-10")
    def test_carryover_uses_the_project_basis(self):
        co = json.loads(_cli(self.root, "carryover", "--json").stdout)
        rows = {r["year"]: r for r in co["rows"]}
        self.assertAlmostEqual(rows[2025]["net_gain"], 350.0, places=2)
        self.assertNotIn(2026, rows)

    @rule("CA-RPT-01")
    def test_t1135_follows_the_basis(self):
        r = _cli(self.root, "t1135", "--json")
        rep = json.loads(r.stdout)
        row = next(p for p in rep["properties"] if p["symbol"] == "ZZZ.TO")
        self.assertAlmostEqual(row["gain"], 350.0, places=2)
        self.assertAlmostEqual(row["year_end_cost"], 0.0, places=2)


@rule("CA-RPT-10")
class TestCarryoverAgainstLocks(unittest.TestCase):
    """S047-21, S048-20: a locked year the ledger disagrees with is
    flagged (and a moving option-timing default is warned about)."""

    def test_disagreeing_lock_is_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                t.replace("option_grant_timing_since = 2025\n", ""))
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            (root / "filed").mkdir()
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"schema_version": 1, "year": 2025,
                 "totals": {"realized": 999.0}, "accounts": {}}))
            r = _cli(root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("differs from the filed lock", r.stderr)
        self.assertIn("option_grant_timing_since is not set", r.stderr)
        row = next(x for x in json.loads(r.stdout)["rows"]
                   if x["year"] == 2025)
        self.assertTrue(row["differs_from_filed"])


class TestConfigCryptoFlag(unittest.TestCase):
    def test_quoted_crypto_flag_is_refused(self):
        """S005-00: crypto = "false" read as truthy by every reader."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(t.replace(
                '[accounts.margin]\ntype = "taxable"\n',
                '[accounts.margin]\ntype = "taxable"\ncrypto = "false"\n'))
            for cmd in ("form-export", "estimate", "sum"):
                r = _cli(root, cmd)
                self.assertNotEqual(r.returncode, 0, cmd)
                self.assertIn("crypto must be true or false", r.stderr)


class TestAuditYearScope(unittest.TestCase):
    def test_other_year_is_not_called_stale(self):
        """R1-191: the saved gains files hold one year."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, year=2026, cash=_TT_CASH + (
                "BUYSELL 2026-01-10 10:00:00 YYY.TO 10 CAD 30.00 -300.00 "
                "0.00\nBUYSELL 2026-02-10 10:00:00 YYY.TO -10 CAD 31.00 "
                "310.00 0.00\n"))
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            for extra in (["--year", "2025"], ["--all-years"], []):
                r = _cli(root, "audit", "--summary", *extra)
                self.assertEqual(r.returncode, 0,
                                 (extra, r.stdout[-1500:], r.stderr[-800:]))
                self.assertNotIn("stale or truncated", r.stdout + r.stderr)

    def test_many_rounded_rows_still_tie(self):
        """S026-16: 4-dp rounding of hundreds of rows is not a failure."""
        rows = []
        for i in range(400):
            sym = f"Q{i:03d}.TO"
            rows.append(f"BUYSELL 2025-02-03 10:00:00 {sym} 3 CAD 3.3333 "
                        f"-10.00 0.00\n")
            rows.append(f"BUYSELL 2025-05-05 10:00:00 {sym} -1 CAD 5.00 "
                        f"5.00 0.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, cash="".join(rows))
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "audit", "--summary")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:])
        self.assertNotIn("do not tie", r.stdout + r.stderr)


class TestAuditPricedByFill(unittest.TestCase):
    def test_fill_priced_row_ties(self):
        """R1-271: a coin fee parsed at 0.00 and priced by the fill
        stage ties against the book when the filled row is supplied."""
        from decimal import Decimal
        from taxjson.bin import taxjson_audit as A
        gid = "fee1"
        g = {"id": gid, "symbol": "TAO", "date": "2026-05-04",
             "date_settle": "2026-05-04", "qty": 0.002, "gain": 0.11,
             "account": "crypto"}
        raw = {"id": gid, "currency": "USD", "net_amount": 0.0,
               "date": "2026-05-04"}
        base = {"id": gid, "net_amount": 0.7756, "currency": "CAD"}
        filled = {"id": gid, "net_amount": 0.5698, "currency": "USD"}
        fx = {"USD": {"2026-05-04": Decimal("1.3612")}}
        kw = dict(check_index={}, fx_history=fx,
                  default_rate=Decimal("1.35"), base_currency="CAD",
                  tmap=None, replacement_lookup={}, checks_supplied=False)
        src = {gid: [{"label": "kraken", "row": raw}]}
        bad = A.build_event(g, {gid: base}, src, **kw)
        good = A.build_event(g, {gid: base}, src, filled_index={gid: filled},
                             **kw)
        self.assertFalse(bad["fx"]["ties"])
        self.assertTrue(good["fx"]["ties"], good["fx"])
        self.assertTrue(good["fx"]["priced_by_fill"])


class TestSumFileManualSection(unittest.TestCase):
    def test_routed_rows_listed_in_the_sum(self):
        """R1-320: the .sum names phantom-basis sales it leaves out."""
        from taxjson.bin.taxjson_sum_gains import (format_report,
                                                   summarize_gains)
        data = {"transactions": [_sell(symbol="DEF.TO", qty=-10,
                                       proceeds=150.0, cost=100.0)],
                "manual_reporting_required": [
                    {"symbol": "ABC.TO", "date": "2025-04-15",
                     "date_settle": "2025-04-16", "qty": -50,
                     "proceeds": 2000.0, "account": "margin"}],
                "summary": {"year": 2025}}
        text = format_report(summarize_gains(data), no_color=True)
        self.assertIn("MANUAL REPORTING REQUIRED", text)
        self.assertIn("ABC.TO", text)


# ------------------------------------------------------------- T1135 walk
def _tx(action, date, symbol, qty, net, settle=None, time="10:00:00",
        **extra):
    r = {"action": action, "date": date, "date_settle": settle or date,
         "time": time, "symbol": symbol, "quantity": qty,
         "net_amount": net, "currency": "CAD", "account": "margin"}
    r.update(extra)
    return r


@rule("CA-RPT-12")
class TestT1135Walk(unittest.TestCase):
    def _walk(self, rows, year=2025, overrides=None, **kw):
        from taxjson.bin.taxjson_t1135 import walk_costs
        return walk_costs(rows, year, overrides or {}, **kw)

    def test_assigned_put_premium_reduces_cost(self):
        """R1-202 / R1-276: ITA 49(3.1)(b) — the stock leg first in the
        file must not matter."""
        put = "XYZ250620P00050000.US"
        rows = [_tx("BUYSELL", "2025-03-03", put, -1, 300.0),
                _tx("BUYSELL", "2025-06-20", "XYZ.US", 100, -5000.0,
                    time="16:00:00"),
                _tx("ASSIGN", "2025-06-20", put, 1, 0.0, time="16:00:00")]
        w = self._walk(rows)
        s = w["per_symbol"]["XYZ.US"]
        self.assertAlmostEqual(s["year_end_cost"], 4700.0, places=2)
        self.assertAlmostEqual(s["max_cost"], 4700.0, places=2)

    def test_exercised_call_cost_is_added(self):
        call = "XYZ250620C00060000.US"
        rows = [_tx("BUYSELL", "2025-03-03", call, 1, -400.0),
                _tx("ASSIGN", "2025-06-20", call, -1, 0.0),
                _tx("BUYSELL", "2025-06-20", "XYZ.US", 100, -6000.0)]
        w = self._walk(rows)
        self.assertAlmostEqual(
            w["per_symbol"]["XYZ.US"]["year_end_cost"], 6400.0, places=2)

    @rule("CA-RPT-01", "CA-DATE-14")
    def test_same_stamp_order_is_the_engines(self):
        """S008-05: the walk takes a same-stamp round trip in the
        engine's order — since CA-DATE-14 (audit R1-30, owner decision
        D7) the export's row order. Sell row first: a short sale covered
        at once, never property held; buy row first: 125,000 held."""
        rows = [_tx("BUYSELL", "2025-06-02", "CCC.US", -1500, 120000.0),
                _tx("BUYSELL", "2025-06-02", "CCC.US", 1500, -125000.0)]
        a = self._walk(rows)
        b = self._walk(list(reversed(rows)))
        self.assertAlmostEqual(a["max_total_cost"], 0.0, places=2)
        self.assertAlmostEqual(b["max_total_cost"], 125000.0, places=2)

    @rule("CA-RPT-01", "CA-CORP-01")
    def test_split_inside_settle_lag(self):
        """S008-06: a pre-split sale settling after the split."""
        rows = [_tx("BUYSELL", "2025-01-02", "ABC.US", 1000, -100000.0),
                _tx("BUYSELL", "2025-06-10", "ABC.US", -500, 60000.0,
                    settle="2025-06-11"),
                _tx("SPLIT", "2025-06-10", "ABC.US", 2, 0.0,
                    time="20:25:00"),
                _tx("BUYSELL", "2025-07-01", "ABC.US", -1000, 70000.0)]
        w = self._walk(rows)
        self.assertAlmostEqual(
            w["per_symbol"]["ABC.US"]["year_end_cost"], 0.0, places=2)

    @rule("CA-RPT-02")
    def test_override_follows_a_rename(self):
        """S051-17: t1135.map keyed on the old ticker."""
        rows = [_tx("BUYSELL", "2025-01-05", "USC.TO", 1000, -150000.0),
                _tx("SPLIT", "2025-12-01", "USC.TO", 1, 0.0,
                    symbol_new="USD.TO")]
        w = self._walk(rows, year=2026, overrides={"USC.TO": "USA"})
        self.assertAlmostEqual(w["max_total_cost"], 150000.0, places=2)
        self.assertIn("USD.TO", w["per_symbol"])

    @rule("CA-RPT-01", "CA-DATE-02")
    def test_trade_basis_year_end(self):
        """S052-10: sold Dec 31 (settles Jan 2) on a trade basis."""
        rows = [_tx("BUYSELL", "2025-03-03", "ZZZ.US", 1000, -120000.0),
                _tx("BUYSELL", "2025-12-31", "ZZZ.US", -1000, 119500.0,
                    settle="2026-01-02")]
        self.assertAlmostEqual(self._walk(rows)["per_symbol"]["ZZZ.US"]
                               ["year_end_cost"], 120000.0, places=2)
        self.assertAlmostEqual(self._walk(rows, tax_date="trade")
                               ["per_symbol"]["ZZZ.US"]["year_end_cost"],
                               0.0, places=2)


@rule("CA-RPT-02")
class TestT1135Report(unittest.TestCase):
    def test_bom_map_first_line_applies(self):
        """S008-03 / S051-18."""
        from taxjson.bin.taxjson_t1135 import load_overrides
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t1135.map"
            p.write_bytes("﻿XYZ.TO USA\nABC.US CA\n".encode("utf-8"))
            ov = load_overrides(p)
        self.assertEqual(ov.get("XYZ.TO"), "USA")

    def test_unused_override_and_deferred_wash_are_named(self):
        """S051-17 (unused key) and S009-01 / S051-21 / S008-07 (denied
        superficial losses not in the cost columns)."""
        from taxjson.bin.taxjson_t1135 import build_report, render_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2025-03-03", "AAA.US", 1000, -90000.0)]}))
            gains = _gains(td, [], name="margin_gains_wash.json")
            doc = json.loads(gains.read_text())
            doc["inventory"] = [{"symbol": "AAA.US", "qty": 1000,
                                 "total_cost": 120000.0,
                                 "deferred_wash": 30000.0}]
            gains.write_text(json.dumps(doc))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                # The note is the year-only mode's (--year-wash-only);
                # the default full-history pass carries every addition
                # (S008-07, tests/test_fix_x_engine_t1135).
                rep = build_report([base], [gains], 2025,
                                   {"NOPE.US": "USA"}, "CAD",
                                   full_history=False)
        self.assertEqual(rep["unused_overrides"], ["NOPE.US"])
        self.assertIn("NOPE.US", err.getvalue())
        self.assertEqual(rep["deferred_wash_not_in_cost"],
                         {"AAA.US": 30000.0})
        text = render_report(rep)
        self.assertIn("NOT reliable", text)

    def test_other_user_maps_read_a_bom(self):
        """S051-18: distributions.map, yf_ticker.map, crypto_ticker.map
        and the security-overrides file keep their first rule."""
        from taxjson.bin.fill_crypto_prices import load_symbol_overrides
        from taxjson.bin.taxjson_apply_distributions import load_map
        from taxjson.bin.taxjson_brokerage import load_security_overrides
        from taxjson.lib.price_chain import load_yf_map
        bom = "﻿"
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "distributions.map").write_text(
                bom + "ABC.TO 2025-06-30 1.00\n", encoding="utf-8")
            (d / "yf_ticker.map").write_text(bom + "OLDCO.TO NEWCO 0.25\n",
                                             encoding="utf-8")
            (d / "crypto_ticker.map").write_text(bom + "MYCOIN mycoin-id\n",
                                                 encoding="utf-8")
            (d / "ov.txt").write_text(bom + "global x us dollar | USD | "
                                            "DLR.U.TO\n", encoding="utf-8")
            self.assertEqual(load_map(d / "distributions.map")[0][0],
                             "ABC.TO")
            self.assertIn("OLDCO.TO", load_yf_map([d]))
            self.assertIn("MYCOIN", load_symbol_overrides([d]))
            self.assertTrue(load_security_overrides(d / "ov.txt")[0][0]
                            .startswith("global"))


class TestOptionBoundaryPhantoms(unittest.TestCase):
    def test_phantom_long_option_sold_is_not_a_write(self):
        """S044-09: a phantom-backed long option sold to close."""
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.option_boundary import write_lots
        from taxjson.lib.missing_history import synthesize_openings
        opt = "ZZZ250919C00050000.TO"
        sale = TaxTransaction(action="BUYSELL", date="2025-06-10",
                              date_settle="2025-06-11", time="10:00:00",
                              symbol=opt, quantity=-2, currency="CAD",
                              price=4.5, net_amount=900.0,
                              account="margin", id="s1")
        self.assertEqual(len(write_lots([sale])), 1)     # no phantom
        txs, _log = synthesize_openings([sale], {(opt, "margin")})
        self.assertEqual(write_lots(txs), [])


class TestWashExplainBlended(unittest.TestCase):
    def test_explain_traces_the_blended_pool(self):
        """S046-09: margin 100@20 + margin2 100@10, margin2 sells @12
        and rebuys — the blended ACB is 15, so a 300 loss is denied."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _toml(root, 2025, "source_currencies = []\n"
                              "option_grant_timing_since = 2025\n",
                  [("margin", "taxable", ""), ("margin2", "taxable", "")])
            for a, tt in (("margin", "BUYSELL 2025-02-03 10:00:00 XYZ.TO "
                                     "100 CAD 20.00 -2000.00 0.00\n"),
                          ("margin2", "BUYSELL 2025-02-03 10:00:00 XYZ.TO "
                                      "100 CAD 10.00 -1000.00 0.00\n"
                                      "BUYSELL 2025-09-02 10:00:00 XYZ.TO "
                                      "-100 CAD 12.00 1200.00 0.00\n"
                                      "BUYSELL 2025-09-08 10:00:00 XYZ.TO "
                                      "100 CAD 12.00 -1200.00 0.00\n")):
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / "x.tt").write_text(tt)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "wash-sales", "--explain")
        self.assertIn("disallowed +$300.00", r.stdout)
        self.assertNotIn("no matching gains", r.stdout + r.stderr)


class TestRunCleanCount(unittest.TestCase):
    def test_one_error_is_counted_once(self):
        """R1-252 side note: <acct>.sum and <acct>_wash.sum both carry
        the account's validation line."""
        from datetime import date
        from taxjson.lib.checklist import Ctx, d_run_clean
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "reports").mkdir()
            (root / "work").mkdir()
            for n in ("margin.sum", "margin_wash.sum"):
                (root / "reports" / n).write_text(
                    "validation: 1 error(s)\nvalidation: 1 error(s)\n")
            cfg = {"settings": {"year": 2025}, "accounts": {}}
            r = d_run_clean(Ctx(root, cfg, 2025, date.today(),
                                lambda *a, **k: (0, "", "")))
        self.assertIn("1 validation error(s)", r.detail)
