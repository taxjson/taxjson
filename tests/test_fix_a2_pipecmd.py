"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rates(path: Path, src: str, dst: str, rate: str):
    d = date(2025, 1, 1)
    lines = []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 {src} {dst} {rate} boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _env(home, **kw):
    e = {**os.environ, "HOME": str(home), "TAXJSON_OFFLINE": "1",
         "NO_COLOR": "1", "PYTHONPATH": str(REPO_ROOT / "src")}
    e.update(kw)
    return e


def _cli(root, home, *a):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(home))


def _tt_project(td, lines, *, country="canada", year=2026, accounts=None,
                extra_settings=""):
    """One taxable account `m` holding a.tt (or `accounts`: {name: (type,
    lines)}); FX rates for the project's foreign currency in work/."""
    root = Path(td) / f"proj-{country}"
    base, src = (("CAD", "USD") if country == "canada" else ("USD", "CAD"))
    accounts = accounts or {"m": ("taxable", lines)}
    cfg = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
           + ('province = "ON"\n' if country == "canada" else "")
           + f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n'
           + (f'option_grant_timing_since = {year}\n'
              if country == "canada" else "") + extra_settings)
    for name, (atype, ls) in accounts.items():
        cfg += f'\n[accounts.{name}]\ntype = "{atype}"\n'
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "a.tt").write_text("\n".join(ls) + "\n")
    (root / "taxjson.toml").write_text(cfg)
    _rates(root / "work" / "to_base.csv", src, base,
           "1.3500" if country == "canada" else "0.7400")
    home = Path(td) / "home"
    home.mkdir(exist_ok=True)
    return root, home


# ---------------------------------------------- A2-0010 (R1-126 regression)
class TestTtUsdSplitRuns(unittest.TestCase):
    """A .tt SPLIT is stamped CAD whatever the listing (kept so row ids
    stay stable). The native gains pass then refused a USD pool's split
    or rename with a currency mismatch and `run` exited 1."""

    def _check(self, country, lines, held_sym, held_qty):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, lines, country=country)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertNotIn("Currency mismatch", r.stderr)
            self.assertNotIn("raw holdings skipped", r.stderr)
            hold = (root / "reports" / "m_holdings.toml").read_text()
            self.assertIn(f'symbol = "{held_sym}"', hold)
            self.assertIn(f"quantity = {held_qty}", hold)
            self.assertIn('currency = "USD"', hold)

    def test_usd_split_canada(self):
        self._check("canada", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ.US 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ.US OLDQ.US 2.0",
            "BUYSELL 2026-08-03 10:00:00 OLDQ.US -5 USD 15 75 0"],
            "OLDQ.US", "15.0")

    def test_usd_rename_canada(self):
        self._check("canada", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ.US 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ.US NEWQ.US 1.0",
            "BUYSELL 2026-08-03 10:00:00 NEWQ.US -5 USD 15 75 0"],
            "NEWQ.US", "5.0")

    def test_usd_split_usa(self):
        self._check("usa", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ OLDQ 2.0",
            "BUYSELL 2026-08-03 10:00:00 OLDQ -5 USD 15 75 0"],
            "OLDQ", "15.0")


# ---------------------------------------------------------------- A2-0040
class TestConflictingSplitsAreErrors(unittest.TestCase):
    """Two SPLIT rows for one event with different ratios are both
    applied (x6). One of them is false: it is a validation ERROR, so
    the console says so, `run --strict` stops and checklist run-clean
    is not done."""

    def _proj(self, td):
        root, home = _tt_project(td, [
            "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 10 -1000 0",
            "SPLIT 2026-03-02 09:30:00 XYZ.TO XYZ.TO 2",
            "BUYSELL 2026-06-03 10:00:00 XYZ.TO -200 CAD 6 1200 0"])
        (root / "inputs" / "m" / "manual.tt").write_text(
            "SPLIT 2026-03-02 09:30:00 XYZ.TO XYZ.TO 3\n")
        return root, home

    def test_strict_refuses_and_console_names_it(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0, r.stdout[-2000:])
            self.assertIn("validation ERROR", r.stderr + r.stdout)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            out = r.stdout + r.stderr
            self.assertIn("validation ERROR", out)
            self.assertIn("conflicting split", out)
            import tomllib
            from taxjson.lib import checklist as cl
            cfg = tomllib.loads((root / "taxjson.toml").read_text())
            ctx = cl.Ctx(root=root, cfg=cfg, year=2026, today=date.today(),
                         run_sub=lambda *a, **k: (0, "", ""))
            res = cl.d_run_clean(ctx)
            self.assertNotEqual(res.status, "done", res.detail)
            self.assertIn("validation error", res.detail)

    def test_same_ratio_twice_is_still_only_a_warning(self):
        from taxjson.bin.taxjson_merge2 import warn_duplicate_splits
        from taxjson.lib.core import TaxTransaction
        import contextlib, io
        rows = [TaxTransaction(action="SPLIT", date="2026-03-02",
                               time="09:30:00", symbol="XYZ.TO",
                               symbol_new="XYZ.TO", quantity=2.0,
                               account="m", currency="CAD")
                for _ in range(2)]
        conflicts = []
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(warn_duplicate_splits(rows, conflicts), 1)
        self.assertEqual(conflicts, [])


# ------------------------------------------ A2-0139 / A2-0201 / A2-0397
class TestTrustRocRecordDateEverywhere(unittest.TestCase):
    """CA-INC-DATE-ROC-TRUST was applied only in pipeline.run_gains:
    taxjson-audit, taxjson-explain and the web what-if booked a trust's
    ROC on its pay date and contradicted the .sum (gain 500 there, 0
    in audit/explain; what-if cost 10,000 instead of 9,500)."""

    _LINES = [
        "BUYSELL 2025-01-06 10:00:00 ZZR.TO 2000 CAD 10 -20000 0",
        "BUYSELL 2025-03-10 10:00:00 ZZR.TO -1000 CAD 10 10000 0",
        "ADJUST 2025-03-20 12:00:00 ZZR.TO CAD -1000 type=roc "
        "record=2025-03-03",
    ]

    def _proj(self, td):
        root, home = _tt_project(td, self._LINES, year=2025)
        r = _cli(root, home, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        g = json.loads((root / "work" / "m_gains.json").read_text())
        sale = [t for t in g["transactions"] if t.get("gain") is not None]
        self.assertEqual(round(sale[0]["gain"], 2), 500.0)
        return root, home

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_audit_ties_out(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "audit")
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            self.assertNotIn("TIE-OUT FAILED", r.stdout + r.stderr)

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_explain_traces_the_booked_gain(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_explain",
                 "--country", "canada", "--year", "2025",
                 str(root / "work" / "m_base.json")],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, env=_env(home))
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("500.00", r.stdout)
            self.assertNotIn("EMPTY pool", r.stdout + r.stderr)

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_web_what_if_cost(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            from taxjson.web import data
            from taxjson.web.context import ProjectContext
            res = data.what_if_sell(ProjectContext.load(root), "m",
                                    "ZZR.TO", 1000, 10.0, on="2025-03-12")
            self.assertTrue(res.get("ok"), res)
            self.assertAlmostEqual(res["cost_basis"], 9500.0, places=2)
            self.assertFalse(any("EMPTY" in w for w in res["warnings"]),
                             res["warnings"])


def _gains_one(book, country, **req):
    import copy, contextlib, io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)), [], [],
                        req=GainsRequest(country=country, taxable=True,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


# ---------------------------------------------------------------- A2-0039
class TestRocMovedIntoAClosedYearNamesTheSale(unittest.TestCase):
    """A January-paid trust ROC with a December record date lowers the
    ACB of a December sale: the pay-year run says which prior-year sale
    changed (that year may be filed without it)."""

    def _book(self):
        from tax_rules.dual import tx
        return [tx("BUYSELL", "2024-06-03", "ZZR.TO", 1000, 10000,
                   currency="CAD", settle="2024-06-04"),
                tx("BUYSELL", "2024-12-30", "ZZR.TO", -1000, 10000,
                   currency="CAD", settle="2024-12-31"),
                tx("ADJUST", "2025-01-08", "ZZR.TO", 0, -500.0, type="roc",
                   currency="CAD", record_date="2024-12-30",
                   description="RETURN OF CAPITAL REC 2024-12-30")]

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_pay_year_run_names_the_prior_year_sale(self):
        err = _gains_one(self._book(), "canada", year=2025)["_stderr"]
        line = [ln for ln in err.splitlines() if "2024 sale" in ln]
        self.assertTrue(line, err)
        self.assertIn("ATTENTION", line[0])
        self.assertIn("2024-12-31", line[0])        # the settle date
        self.assertIn("500.00", line[0])

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_same_year_move_is_quiet(self):
        book = self._book()
        book[2].date = book[2].date_settle = "2024-12-31"
        err = _gains_one(book, "canada", year=2024)["_stderr"]
        self.assertNotIn(" sale", err)


# ---------------------------------------------------------------- A2-0396
class TestWithholdingFollowsItsDividend(unittest.TestCase):
    """A TAX row withheld on a payment is in the same year as the
    payment's dividend when income dating moves the dividend (a US
    January RIC dividend on Dec 31; a Canadian trust's distribution on
    its record date)."""

    def _us(self):
        from tax_rules.dual import tx
        return [tx("BUYSELL", "2025-06-02", "VXUS.US", 100, 5000),
                tx("DIVIDEND", "2026-01-05", "VXUS.US", 0, 92.0,
                   gross_amount=100.0, description="VXUS DIVIDEND"),
                tx("TAX", "2026-01-05", "VXUS.US", 0, 8.0,
                   description="VXUS DIVIDEND"),
                tx("TAX", "2026-02-05", "VXUS.US", 0, 3.0,
                   description="VXUS other")]

    def _ca(self):
        from tax_rules.dual import tx
        return [tx("BUYSELL", "2025-06-02", "ZXT.TO", 100, 5000,
                   currency="CAD"),
                tx("DIVIDEND", "2026-01-15", "ZXT.TO", 0, 50.0,
                   gross_amount=50.0, currency="CAD",
                   record_date="2025-12-30", income_label="distribution",
                   description="DIST ON 100 SHS REC 12/30/25 PAY 01/15/26"),
                tx("TAX", "2026-01-15", "ZXT.TO", 0, 7.5, currency="CAD",
                   description="DIST ON 100 SHS REC 12/30/25 PAY 01/15/26")]

    @staticmethod
    def _withheld(rows, year, settings):
        from taxjson.bin.taxjson_sum_income import summarize_income
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules.from_settings(settings)
        res = summarize_income([t.to_dict() for t in rows], year,
                               rules=rules)
        return round(sum(float(c.get("tax", 0.0) or 0.0)
                         for per in res["ticker_stats"].values()
                         for c in per.values()), 2)

    @rule("US-INC-DATE-RIC")
    def test_us_ric_withholding_moves_with_the_dividend(self):
        st = {"country": "usa",
              "ric_january_dividends": ["VXUS.US 2026-01-05"]}
        self.assertEqual(self._withheld(self._us(), 2025, st), 8.0)
        # The unpaired February withholding keeps its pay date.
        self.assertEqual(self._withheld(self._us(), 2026, st), 3.0)

    @rule("CA-INC-DATE-TRUST")
    def test_ca_trust_withholding_moves_with_the_distribution(self):
        st = {"country": "canada"}
        self.assertEqual(self._withheld(self._ca(), 2025, st), 7.5)
        self.assertEqual(self._withheld(self._ca(), 2026, st), 0.0)

# ---------------------------------------------------------------- A2-0398
class TestListedRicDividendIsNamed(unittest.TestCase):
    """A January dividend listed in ric_january_dividends leaves the pay
    year's books: both project years say so on the ATTENTION channel."""

    @rule("US-INC-DATE-RIC")
    def test_both_years_name_the_moved_dividend(self):
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2025-06-02", "VTI.US", 10, 2000),
                tx("DIVIDEND", "2026-01-05", "VTI.US", 0, 90.0,
                   gross_amount=90.0, description="VTI DIVIDEND")]
        kw = dict(ric_january_dividends=("VTI.US 2026-01-05",))
        e26 = _gains_one(book, "usa", year=2026, **kw)["_stderr"]
        e25 = _gains_one(book, "usa", year=2025, **kw)["_stderr"]
        self.assertIn("ATTENTION: income year: VTI.US: dividend 90.00", e26)
        self.assertIn("NOT in 2026's numbers", e26)
        self.assertIn("counted in 2025 here", e25)
        e24 = _gains_one(book, "usa", year=2024, **kw)["_stderr"]
        self.assertNotIn("VTI.US", e24)


if __name__ == "__main__":
    unittest.main()
