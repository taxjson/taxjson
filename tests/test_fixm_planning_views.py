"""Medium-round planning fixes: the report/view commands.

Synthetic projects only. Each test names the audit finding it pins.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TOML = ('[settings]\nyear = 2026\ncountry = "canada"\n'
         'base_currency = "CAD"\n'
         '[accounts.margin]\ntype = "taxable"\n'
         '[accounts.rrsp]\ntype = "sheltered"\n')


def _runsub(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _json(root, *args):
    r = _runsub(root, *args, "--json")
    assert r.returncode == 0, (r.stdout, r.stderr)
    return json.loads(r.stdout)


def _project(tmp, toml=_TOML):
    root = Path(tmp)
    (root / "work").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(toml)
    return root


def _write(root, name, txs, summary=None, **extra):
    doc = {"transactions": txs}
    if summary is not None:
        doc["summary"] = summary
    doc.update(extra)
    (root / "work" / name).write_text(json.dumps(doc))


def _trade(date, sym, qty, price, net, fee=0.0, cur="CAD", **kw):
    t = {"action": "BUYSELL", "date": date, "time": "10:00:00",
         "symbol": sym, "quantity": qty, "price": price,
         "net_amount": net, "fee": fee, "currency": cur}
    t.update(kw)
    return t


class TestFeesAndTradesSigns(unittest.TestCase):
    """R1-54 / R1-269 (FEE rows positive = charged) and S039-11
    (trades view signed net/fee, signed totals)."""

    def _proj(self, tmp):
        root = _project(tmp)
        _write(root, "margin_raw.json", [
            _trade("2026-03-02", "AAA.TO", 100, 10.0, 1002.0, fee=2.0),
            _trade("2026-03-03", "AAA.TO", -100, 12.0, 1197.0, fee=3.0),
            # IB rebate on a trade: fee -0.50
            _trade("2026-03-04", "BBB.TO", -10, 1.0, 10.5, fee=-0.5),
            # Penny option close: commission exceeds the gross.
            _trade("2026-03-05", "BBB260116C00010000.TO", -1, 0.01,
                   -0.25, fee=1.25),
            {"action": "FEE", "date": "2026-03-06", "time": "09:30:00",
             "symbol": "", "currency": "CAD", "net_amount": 10.0},
            {"action": "FEE", "date": "2026-03-07", "time": "09:30:00",
             "symbol": "", "currency": "CAD", "net_amount": -1.0},
        ])
        return root

    def test_fee_rows_positive_is_charged(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            doc = _json(root, "fees")
        fees = {(r["action"], r["date"]): r["fee"] for r in doc["rows"]}
        self.assertEqual(fees[("FEE", "2026-03-06")], 10.0)
        self.assertEqual(fees[("FEE", "2026-03-07")], -1.0)
        # 2 + 3 - 0.5 + 1.25 trade fees, + 10 charged - 1 refunded
        self.assertAlmostEqual(doc["totals"]["CAD"], 14.75, places=2)

    def test_trades_view_is_signed_taxtext(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            r = _runsub(root, "trades", "2026", "margin")
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = [ln.split() for ln in r.stdout.splitlines()
                     if ln.startswith("BUYSELL")]
            rebate = next(ln for ln in lines if ln[3] == "BBB.TO")
            self.assertEqual(rebate[-1], "-0.50")
            penny = next(ln for ln in lines if ln[3].startswith("BBB26"))
            self.assertEqual(penny[-2], "-0.25")
            # Signed sell total: 1197 + 10.5 - 0.25
            self.assertIn("TOTAL SELL:     1,207.25 CAD", r.stdout)
            doc = _json(root, "trades", "2026", "margin")
            self.assertAlmostEqual(doc["totals"]["sell"]["CAD"], 1207.25)

    def test_trades_sum_sold_is_signed(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            doc = _json(root, "trades-sum")
        self.assertAlmostEqual(doc["totals"]["CAD"]["sold"], 1207.25)
        self.assertAlmostEqual(doc["totals"]["CAD"]["bought"], 1002.0)


_LEAP = "AAA270115C00050000.TO"
_CC = "BBB260220C00030000.TO"


def _window_project(tmp, year=2026):
    toml = _TOML.replace("year = 2026", f"year = {year}")
    root = _project(tmp, toml)
    _write(root, "margin_raw.json", [
        _trade("2025-01-02", _LEAP, 1, 5.0, 500.0),
        _trade("2025-12-31", _LEAP, -1, 5.5, 550.0, date_settle="2026-01-02"),
        _trade("2025-12-31", _CC, -1, 1.0, 100.0, date_settle="2026-01-02"),
        _trade("2026-03-02", "AAA.TO", -10, 30.0, 300.0),
    ])
    rows = [
        {"date": "2025-12-31", "date_settle": "2026-01-02", "symbol": _LEAP,
         "qty": 1, "proceeds": 550.0, "cost": 500.0, "gain": 50.0,
         "direction": "LONG", "currency": "CAD", "days_held": 363},
        {"date": "2025-12-31", "date_settle": "2026-01-02", "symbol": _CC,
         "qty": -1, "proceeds": 0.0, "cost": -100.0, "gain": 100.0,
         "direction": "SHORT", "currency": "CAD", "days_held": 1},
        {"date": "2026-03-02", "date_settle": "2026-03-03",
         "symbol": "AAA.TO", "qty": 10, "proceeds": 300.0, "cost": 100.0,
         "gain": 200.0, "direction": "LONG", "currency": "CAD",
         "days_held": 60},
    ]
    summ = {"year": "2026", "tax_date_basis": "settle"}
    _write(root, "margin_gains_wash.json", rows, summary=summ)
    _write(root, "margin_gains.json", rows, summary=summ)
    _write(root, "margin_raw_gains.json", rows,
           summary={"year": "all", "tax_date_basis": "settle"})
    return root


class TestSettleBasisWindows(unittest.TestCase):
    """R1-171 / R1-186 / R1-238 / R1-273: a tax-year window on a
    settle-basis project keeps a Dec-31 trade that settles in January
    in the NEXT year, like `sum` and the gains artifacts."""

    def test_default_window_is_settle_basis(self):
        with tempfile.TemporaryDirectory() as d:
            root = _window_project(d)
            self.assertAlmostEqual(_json(root, "winners")["total_gain"], 350.0)
            self.assertAlmostEqual(_json(root, "ccd-sum")["total_gain"], 100.0)
            self.assertAlmostEqual(_json(root, "leaps-sum")["total_gain"], 50.0)
            self.assertAlmostEqual(_json(root, "leaps")["total_gain"], 50.0)
            g = _json(root, "gains", "2026")
            self.assertAlmostEqual(g["totals"]["CAD"], 350.0)
            self.assertEqual(len(g["rows"]), 3)

    def test_prior_year_does_not_claim_january_settlements(self):
        with tempfile.TemporaryDirectory() as d:
            root = _window_project(d)
            r = _runsub(root, "winners", "2025", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["total_gain"], 0.0)
            g = _json(root, "gains", "2025")
            self.assertEqual(g["rows"], [])


class TestLeapsScopeGuard(unittest.TestCase):
    def test_leaps_warn_outside_artifact_year(self):
        """S048-11: leaps/leaps-sum warn like ccd-sum/winners."""
        with tempfile.TemporaryDirectory() as d:
            root = _window_project(d)
            for cmd in ("leaps", "leaps-sum"):
                r = _runsub(root, cmd, "all")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("cover tax year 2026 only", r.stderr, cmd)

    def test_default_window_refuses_artifacts_of_another_year(self):
        """S048-14: [settings].year = 2025 but work/ built for 2026."""
        with tempfile.TemporaryDirectory() as d:
            root = _window_project(d, year=2025)
            for cmd in ("winners", "ccd-sum", "leaps", "leaps-sum"):
                r = _runsub(root, cmd)
                self.assertNotEqual(r.returncode, 0, (cmd, r.stdout))
                self.assertIn("2026", r.stderr, cmd)
                self.assertNotIn("No ", r.stdout, cmd)


def _div(date, sym, amt, action="DIVIDEND", cur="CAD"):
    return {"action": action, "date": date, "time": "09:30:00",
            "symbol": sym, "quantity": 100, "price": amt / 100,
            "gross_amount": amt, "net_amount": amt, "currency": cur}


class TestDividendViews(unittest.TestCase):
    def _proj(self, tmp):
        root = _project(tmp)
        _write(root, "margin_raw.json", [
            _div("2026-03-02", "AAA.TO", 100.0),
            _div("2026-03-03", "AAA.TO", 10.0, action="DIVIDEND_IN_LIEU"),
        ])
        _write(root, "rrsp_raw.json", [_div("2026-03-04", "BBB.TO", 50.0)])
        return root

    def test_divs_sum_excludes_payments_in_lieu(self):
        """R1-272: dil-sum is 'split out from divs-sum' — PIL is not a
        dividend (no gross-up) and not on the T5."""
        with tempfile.TemporaryDirectory() as d:
            doc = _json(self._proj(d), "divs-sum")
        self.assertAlmostEqual(doc["totals"]["CAD"], 150.0)

    def test_divs_help_matches_code(self):
        with tempfile.TemporaryDirectory() as d:
            r = _runsub(self._proj(d), "--help")
        self.assertNotIn("only DIVIDEND rows", r.stdout)

    def test_divs_sum_splits_taxable_and_sheltered(self):
        """S041-04: the slip tie-out figure is the taxable total."""
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            doc = _json(root, "divs-sum")
            r = _runsub(root, "divs-sum")
        self.assertAlmostEqual(doc["totals_taxable"]["CAD"], 100.0)
        self.assertAlmostEqual(doc["totals_sheltered"]["CAD"], 50.0)
        self.assertIn("TAXABLE", r.stdout)
        self.assertIn("rrsp", r.stdout)


class TestWinnersScope(unittest.TestCase):
    def _proj(self, tmp):
        root = _project(tmp)
        summ = {"year": "2026", "tax_date_basis": "settle"}
        _write(root, "margin_gains_wash.json", [
            {"date": "2026-03-02", "date_settle": "2026-03-03",
             "symbol": "AAA.TO", "qty": 10, "proceeds": 300.0,
             "cost": 100.0, "gain": 200.0, "currency": "CAD"}],
            summary=summ,
            manual_reporting_required=[
                {"date": "2026-04-01", "date_settle": "2026-04-02",
                 "symbol": "ZZZ.TO", "qty": 10, "proceeds": 500.0,
                 "currency": "CAD"}])
        _write(root, "rrsp_gains.json", [
            {"date": "2026-05-01", "date_settle": "2026-05-02",
             "symbol": "BBB.TO", "qty": 10, "proceeds": 2000.0,
             "cost": 1000.0, "gain": 1000.0, "currency": "CAD"}],
            summary=summ)
        return root

    def test_winners_splits_sheltered(self):
        """S040-13."""
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            doc = _json(root, "winners")
            r = _runsub(root, "winners")
        self.assertAlmostEqual(doc["total_gain_taxable"], 200.0)
        self.assertAlmostEqual(doc["total_gain_sheltered"], 1000.0)
        self.assertIn("TAXABLE", r.stdout)
        self.assertIn("rrsp", r.stdout)

    def test_winners_counts_routed_phantom_rows(self):
        """S040-15: pipeline files route tainted rows out of transactions."""
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            doc = _json(root, "winners")
            r = _runsub(root, "winners")
        self.assertEqual(doc["tainted_skipped"], 1)
        self.assertIn("1 tainted", r.stderr)


class TestUnreadableInputsAreErrors(unittest.TestCase):
    """S045-01 / S042-05 / S049-00: a view never answers from part of
    the books with exit 0."""

    def _proj(self, tmp):
        root = _window_project(tmp)
        _write(root, "rrsp_gains.json", [
            {"date": "2026-05-01", "date_settle": "2026-05-02",
             "symbol": "BBB.TO", "qty": 10, "proceeds": 2000.0,
             "cost": 1000.0, "gain": 1000.0, "currency": "CAD"}],
            summary={"year": "2026", "tax_date_basis": "settle"},
            inventory=[{"symbol": "BBB.TO", "qty": 5, "total_cost": 500.0}])
        return root

    def test_truncated_gains_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            (root / "work" / "rrsp_gains.json").write_text('{"transactions": [')
            for cmd in (["winners"], ["ccd-sum"], ["leaps-sum"], ["list"],
                        ["shares"], ["wash-sales"]):
                r = _runsub(root, *cmd)
                self.assertNotEqual(r.returncode, 0, (cmd, r.stdout))
                self.assertIn("rrsp_gains.json", r.stderr, cmd)

    def test_truncated_raw_book_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            (root / "work" / "margin_raw.json").write_text('{"transac')
            (root / "reports").mkdir()
            (root / "reports" / "margin_holdings.toml").write_text(
                '[[holding]]\nsymbol = "AAA.TO"\nquantity = 10\n')
            for cmd in (["leaps"], ["scan"], ["divs-sum"], ["events"]):
                r = _runsub(root, *cmd)
                self.assertNotEqual(r.returncode, 0, (cmd, r.stdout))
                self.assertIn("margin_raw.json", r.stderr, cmd)
                self.assertNotIn("clean scan", r.stdout, cmd)

    def test_unreadable_config_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d)
            (root / "taxjson.toml").write_text(_TOML + "oops = = 1\n")
            r = _runsub(root, "fees-sum")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("taxjson.toml", r.stderr)


class TestScanUsListing(unittest.TestCase):
    """S042-06 / S049-09: US-LISTING needs the US line itself to pay and
    ticker.map to say the listings are one security; DISTINCT wins."""

    def _proj(self, tmp, tmap):
        toml = ('[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.tfsa]\ntype = "sheltered"\n')
        root = _project(tmp, toml)
        (root / "ticker.map").write_text(tmap)
        (root / "reports").mkdir()
        (root / "reports" / "margin_holdings.toml").write_text(
            "".join(f'[[holding]]\nsymbol = "{s}"\nquantity = 10\n'
                    for s in ("EFX.US", "ZZZ.US", "CNQ.US")))
        (root / "reports" / "tfsa_holdings.toml").write_text(
            "".join(f'[[holding]]\nsymbol = "{s}"\nquantity = 10\n'
                    for s in ("EFX.TO", "ZZZ.TO")))
        _write(root, "margin_raw.json", [
            _div("2026-03-02", "EFX.US", 10.0, cur="USD"),
            _div("2026-03-02", "CNQ.US", 10.0, cur="USD")])
        _write(root, "tfsa_raw.json", [_div("2026-03-02", "ZZZ.TO", 10.0)])
        return root

    def test_distinct_pairs_and_non_payers_are_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._proj(d, "DISTINCT EFX.US EFX.TO\n"
                                 "DISTINCT ZZZ.US ZZZ.TO\n"
                                 "TOBASE CNQ.US CNQ.TO\n")
            r = _runsub(root, "scan")
        flagged = [ln for ln in r.stdout.splitlines() if "US-LISTING" in ln]
        self.assertFalse(any("EFX" in ln or "ZZZ" in ln for ln in flagged),
                         r.stdout)
        # Control: a mapped pair whose US line pays is still flagged.
        self.assertTrue(any("CNQ.US" in ln for ln in flagged), r.stdout)


def _tool(mod, *args):
    return subprocess.run([sys.executable, "-m", f"taxjson.bin.{mod}", *args],
                          cwd=REPO_ROOT, capture_output=True, text=True)


class TestCrossReports(unittest.TestCase):
    """R1-173: reports/ccd.rpt and leaps.rpt follow the query commands'
    tainted policy, title what they hold, and fail on unreadable input."""

    def _gains(self, tmp):
        p = Path(tmp) / "g.json"
        p.write_text(json.dumps({"summary": {"year": "2026"}, "transactions": [
            {"date": "2026-03-01", "symbol": _CC, "qty": -1,
             "proceeds": 0.0, "cost": -500.0, "gain": 500.0,
             "direction": "SHORT", "currency": "CAD"},
            {"date": "2026-03-02", "symbol": _CC, "qty": -1,
             "proceeds": 0.0, "cost": -900.0, "gain": 900.0,
             "direction": "SHORT", "currency": "CAD", "tainted": True},
            {"date": "2026-03-03", "symbol": _LEAP, "qty": 1,
             "proceeds": 800.0, "cost": 500.0, "gain": 300.0,
             "direction": "LONG", "currency": "CAD"},
            {"date": "2026-03-04", "symbol": _LEAP, "qty": 1,
             "proceeds": 1500.0, "cost": 500.0, "gain": 1000.0,
             "direction": "LONG", "currency": "CAD", "tainted": True},
        ]}))
        return p

    def test_tainted_rows_excluded_and_counted(self):
        with tempfile.TemporaryDirectory() as d:
            g = self._gains(d)
            ccd = _tool("taxjson_ccd_gains", str(g))
            lg = _tool("taxjson_leaps_gains", str(g))
        self.assertRegex(ccd.stdout, r"\nTOTAL\s+500\.00\n")
        self.assertIn("1 tainted", ccd.stdout)
        self.assertRegex(lg.stdout, r"\nTOTAL\s+300\.00\n")
        # Every long option of any tenor: say so, point at leaps-sum.
        self.assertNotIn("(LEAPS)", lg.stdout)
        self.assertIn("leaps-sum", lg.stdout)

    def test_unreadable_input_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text('{"transactions": [')
            for mod in ("taxjson_ccd_gains", "taxjson_leaps_gains"):
                for arg in (str(bad), str(Path(d) / "missing.json")):
                    r = _tool(mod, arg)
                    self.assertNotEqual(r.returncode, 0, (mod, arg))
                    self.assertNotIn("TOTAL", r.stdout)


if __name__ == "__main__":
    unittest.main()
