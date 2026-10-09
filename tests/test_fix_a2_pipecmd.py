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
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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
    cfg = (f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "{country}"\n'
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
    taxjson-audit and taxjson-explain booked a trust's ROC on its pay
    date and contradicted the .sum (gain 500 there, 0 in
    audit/explain)."""

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
    def test_events_view_dates_withholding_with_the_payment(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2025-06-02 10:00:00 ZXT.TO 100 CAD 50 -5000 0",
                "DIVIDEND 2026-01-15 10:00:00 ZXT.TO 100 CAD 0.5 50 "
                "label=distribution record=2025-12-30",
                "TAX 2026-01-15 10:00:00 ZXT.TO 100 CAD 0.075 7.5"])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r25 = _cli(root, home, "events", "2025").stdout
            r26 = _cli(root, home, "events", "2026").stdout
            self.assertIn("TAX ", r25)
            self.assertNotIn("TAX ", r26)

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


# ------------------------------------------------------ A2-0395 / A2-0137
class TestImpossibleShortsAreLoud(unittest.TestCase):
    """A registered account or a spot-crypto book cannot be short: a sale
    with nothing held is missing history. The run says so on the console
    (ATTENTION) and `run --strict` refuses."""

    def _run_both(self, root, home, needle):
        r = _cli(root, home, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = r.stdout + r.stderr
        self.assertIn("ATTENTION: short:", out)
        self.assertIn(needle, out)
        r = _cli(root, home, "run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertIn("cannot be short", r.stderr)

    def test_registered_account_short(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 50 -5000 0",
                    "BUYSELL 2026-06-10 10:00:00 XYZ.TO -100 CAD 40 4000 0"]),
                "tfsa": ("sheltered", [
                    "BUYSELL 2026-03-02 10:00:00 XYZ.TO -100 CAD 45 4500 0",
                    "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"]),
            })
            self._run_both(root, home, "XYZ.TO (tfsa)")

    def test_spot_crypto_short(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "kr": ("taxable", [
                    "BUYSELL 2026-03-02 10:00:00 BTC -1 CAD 100000 100000 0"]),
            }, extra_settings="")
            cfg = (root / "taxjson.toml").read_text().replace(
                '[accounts.kr]\ntype = "taxable"\n',
                '[accounts.kr]\ntype = "taxable"\ncrypto = true\n')
            (root / "taxjson.toml").write_text(cfg)
            self._run_both(root, home, "BTC (kr)")

    def test_taxable_short_stays_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-03-02 10:00:00 XYZ.TO -100 CAD 45 4500 0",
                "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"])
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("ATTENTION: short:", r.stdout + r.stderr)


# --------------------------------------- A2-0140 / A2-0402 / A2-0399 / A2-1222
class TestArtifactNamespacesNeverCollide(unittest.TestCase):
    """One account's work files must never be another's: an account named
    <other>_tt_<x> or <other>_<broker> is refused, and two .tt files
    of one account that convert to the same work file are refused."""

    def test_pair_names_refused(self):
        from taxjson.lib.config_check import account_type_problems
        for a, b in (("m", "m_tt_x"), ("a", "a_questrade"),
                     ("a", "a_ib_corp"), ("a", "a_generic-ws"),
                     ("a", "a_rbc_direct")):
            with self.subTest(b=b):
                cfg = {"accounts": {a: {"type": "taxable"},
                                    b: {"type": "taxable"}}}
                probs = account_type_problems(cfg)
                self.assertTrue(any(f"[accounts.{b}]" in p for p in probs),
                                probs)
        ok = {"accounts": {"rrsp": {"type": "sheltered"},
                           "rrsp2": {"type": "sheltered"},
                           "rrsp_spousal": {"type": "sheltered"},
                           "margin": {"type": "taxable"}}}
        self.assertEqual(account_type_problems(ok), [])

    def test_run_refuses_pair(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "m": ("taxable", ["BUYSELL 2026-01-05 10:00:00 XYZ.TO 1 "
                                  "CAD 10 -10 0"]),
                "m_tt_x": ("taxable", ["BUYSELL 2026-01-05 10:00:00 "
                                       "ABC.TO 1 CAD 10 -10 0"])})
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("[accounts.m_tt_x]", r.stderr)

    def test_tt_suffix_case_twins_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 AAA.TO 10 CAD 10 -100 0"])
            twin = root / "inputs" / "m" / "a.TT"
            twin.write_text("BUYSELL 2026-01-05 10:00:00 BBB.TO 10 CAD 10 "
                            "-100 0\n")
            if len(list((root / "inputs" / "m").iterdir())) < 2:
                self.skipTest("case-insensitive file system")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
            self.assertIn("a.TT", r.stderr)
            self.assertIn("a.tt", r.stderr)


# ------------------------------------- A2-0143 / A2-0403 / A2-0144 / A2-0401
class TestUnreadableInputsAreRefused(unittest.TestCase):
    """A file that exists as a name but cannot be read (a dangling or
    looping symlink, a directory) is never taken as absent."""

    def _proj(self, td):
        return _tt_project(td, [
            "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"])

    def test_dangling_statement_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            (root / "inputs" / "m" / "questrade_h2.csv").symlink_to(
                root / "nowhere.csv")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
            self.assertIn("questrade_h2.csv", r.stderr)

    def test_dangling_ticker_map_in_read_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            (root / "ticker.map").symlink_to(root / "gone.map")
            for cmd in (["sanity"], ["tips"], ["harvest"], ["fees"]):
                with self.subTest(cmd=cmd):
                    r = _cli(root, home, *cmd)
                    self.assertNotEqual(r.returncode, 0, r.stdout[-800:])
                    self.assertIn("ticker.map", r.stderr)

    def test_dangling_config_is_not_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            cfg = root / "taxjson.toml"
            cfg.rename(root / "moved.toml")
            cfg.symlink_to(root / "gone.toml")
            r = _cli(root, home, "fees")
            self.assertNotEqual(r.returncode, 0, r.stdout[-800:])
            self.assertIn("taxjson.toml", r.stderr)


# ---------------------------------------------------------------- A2-0394
class TestArtifactNamesAreNotAccounts(unittest.TestCase):
    """sum / list / winners / wash-sales took `margin_raw` (the native-
    currency books) as an account and printed USD under a CAD header."""

    def test_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 XYZ.US 10 USD 10 -100 0",
                "BUYSELL 2026-03-05 10:00:00 XYZ.US -10 USD 12 120 0"])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            for cmd in ("sum", "list", "winners", "wash-sales"):
                for acct in ("m_raw", "m_raw_base"):
                    with self.subTest(cmd=cmd, acct=acct):
                        r = _cli(root, home, cmd, acct)
                        self.assertNotEqual(r.returncode, 0, r.stdout[-600:])
                        self.assertIn("not an account", r.stderr)
            r = _cli(root, home, "sum", "m")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])


# ------------------------------------------- A2-0400 / A2-0694 / A2-0405
class TestViewsSayTheBooksAreNotClean(unittest.TestCase):
    """wash-sales, list and winners carry the run-state banner (per-account
    books after `run --account`), and wash-sales the other-year banner."""

    def _two_taxable(self, td):
        return _tt_project(td, [], accounts={
            "margin": ("taxable", [
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 50 -5000 0",
                "BUYSELL 2026-06-10 10:00:00 XYZ.TO -100 CAD 40 4000 0"]),
            "cash": ("taxable", [
                "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"])})

    def test_per_account_books_banner(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._two_taxable(td)
            for a in ("margin", "cash"):
                r = _cli(root, home, "run", "--no-input", "--account", a)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            for cmd in ("wash-sales", "list", "winners"):
                with self.subTest(cmd=cmd):
                    r = _cli(root, home, cmd)
                    self.assertIn("not the clean result", r.stderr,
                                  (r.stdout + r.stderr)[-1000:])

    def test_wash_sales_other_year_banner(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._two_taxable(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace("year = 2026",
                                                   "year = 2025"))
            r = _cli(root, home, "wash-sales")
            self.assertIn("built for another tax year", r.stderr,
                          (r.stdout + r.stderr)[-1000:])


# ------------------------------------------------------ A2-1225 / A2-1233
class TestAffiliatedDenialWording(unittest.TestCase):
    """An affiliated person's purchase is permanent for this return, but
    that person adds the loss to their own ACB (s.53(1)(f)) — not 'lost
    for good' (the S033-03 wording, carried to the trace and the
    wash-sales footer)."""

    @rule("CA-SL-09")
    def test_trace_affiliated(self):
        from taxjson.lib.trace_format import _render_wash_explanation
        g = {"raw_gain": -100.0, "disallowed_amount": 100.0,
             "permanently_disallowed": 100.0,
             "wash_trigger": {"is_full_disallowance": True,
                              "trigger_date": "2026-03-02",
                              "trigger_qty": 10, "trigger_price": 9.0,
                              "trigger_account": "spouse",
                              "trigger_affiliated": True}}
        txt = "\n".join(_render_wash_explanation(g))
        self.assertNotIn("lost for good", txt)
        self.assertIn("own ACB", txt)
        g["wash_trigger"]["trigger_affiliated"] = False
        g["wash_trigger"]["trigger_sheltered"] = True
        txt = "\n".join(_render_wash_explanation(g))
        self.assertIn("lost for good", txt)

    @rule("CA-SL-09")
    def test_wash_sales_footer(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 50 -5000 0",
                    "BUYSELL 2026-06-10 10:00:00 XYZ.TO -100 CAD 40 4000 0"]),
                "tfsa": ("sheltered", [
                    "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"])})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, home, "wash-sales")
            self.assertIn("affiliated person", r.stdout)
            self.assertIn("own ACB", r.stdout)


# ---------------------------------------------------------------- A2-0404
class TestScanIsNotCleanOverMissingBooks(unittest.TestCase):

    def test_missing_holdings_or_raw_book(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"]),
                "tfsa": ("sheltered", [
                    "BUYSELL 2026-01-05 10:00:00 ABC.TO 10 CAD 10 -100 0"])})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, home, "tips")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for f in (root / "reports" / "tfsa_holdings.toml",
                      root / "work" / "tfsa_raw.json"):
                with self.subTest(f=f.name):
                    data = f.read_bytes()
                    f.unlink()
                    r = _cli(root, home, "tips")
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertNotIn("No tips —", r.stdout)
                    self.assertIn("tfsa", r.stderr)
                    f.write_bytes(data)


# ---------------------------------------------------------------- A2-0712
class TestRunDefaultsBaseCurrency(unittest.TestCase):

    def test_unset_base_is_the_countrys(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0",
                "BUYSELL 2026-03-05 10:00:00 XYZ.TO -10 CAD 12 120 0"])
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                'base_currency = "CAD"\n', ""))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertIn("20.00", (root / "reports" / "m.sum").read_text())


# --------------------------------------------- A2-0713 / A2-1228 / A2-0714
class TestUnusableStatementFiles(unittest.TestCase):

    def _proj(self, td):
        return _tt_project(td, [
            "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"])

    def test_empty_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            (root / "inputs" / "m" / "questrade_2026.csv").write_text("")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is empty", r.stderr)
            self.assertNotIn("Rename to start", r.stderr)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root reads any file")
    def test_unreadable_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            f = root / "inputs" / "m" / "questrade_2026.csv"
            f.write_text("x,y\n1,2\n")
            f.chmod(0)
            try:
                r = _cli(root, home, "run", "--no-input")
            finally:
                f.chmod(0o600)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("permission denied", r.stderr)
            self.assertNotIn("Rename to start", r.stderr)

    def test_numbers_export_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            (root / "inputs" / "m" / "Activity_2026.numbers").write_bytes(
                b"PK\x03\x04")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Activity_2026.numbers", r.stderr)


# ------------------------------------------------------ A2-0717 / A2-1232
class TestTransfersViewNeverSilent(unittest.TestCase):

    def test_missing_config_and_base(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"]),
                "rrsp": ("sheltered", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 10 -1000 0",
                    "TRANSFER 2026-02-05 10:00:00 XYZ.TO -100 CAD 10 1000 "
                    "DECLARED"])}, extra_settings="")
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                '[accounts.rrsp]\ntype = "sheltered"\n',
                '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n'))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, home, "transfers")
            self.assertIn("XYZ.TO", r.stdout, r.stderr)
            base = root / "work" / "rrsp_base.json"
            data = base.read_bytes()
            base.unlink()
            r = _cli(root, home, "transfers")
            self.assertIn("rrsp_base.json", r.stderr)
            base.write_bytes(data)
            cfg.rename(root / "moved.toml")
            r = _cli(root, home, "transfers")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("taxjson.toml", r.stderr)


# ---------------------------------------------------------------- A2-0715
class TestCoveredCallNamesTheHeldClassShare(unittest.TestCase):

    def test_ccd_groups_under_held_class_share(self):
        from taxjson.bin._option_gains_report import process_data
        doc = {"transactions": [{
                   "symbol": "QRL270618C00038000.TO", "direction": "SHORT",
                   "cost": -199.0, "proceeds": 0.0, "gain": 199.0,
                   "currency": "CAD", "date": "2026-02-17"}],
               "inventory": [{"symbol": "QRL.B.TO", "qty": 100.0}]}
        groups = {}
        process_data(doc, groups, direction="SHORT", calls_only=True)
        self.assertIn("QRL.B.TO", groups)
        self.assertNotIn("QRL.TO", groups)


# ------------------------------------------------------ A2-1230 / A2-1231
class TestSmallRunReaders(unittest.TestCase):

    def test_overlap_note_counts_transfers(self):
        import contextlib, io
        from taxjson.bin.taxjson_run import _warn_cross_taxable_overlap
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.json", Path(td) / "b.json"
            a.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "symbol": "XYZ.TO"}]}))
            b.write_text(json.dumps({"transactions": [
                {"action": "TRANSFER", "symbol": "XYZ.TO"}]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _warn_cross_taxable_overlap([("a", a), ("b", b)],
                                            {"country": "canada"})
            self.assertIn("XYZ.TO", err.getvalue())

    def test_sanity_refuses_boolean_quantity(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 30 CAD 10 -300 0"])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            h = Path(td) / "h.toml"
            h.write_text('[[holding]]\nsymbol = "XYZ.TO"\nquantity = true\n')
            r = _cli(root, home, "sanity", f"m={h}")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is not a number", r.stderr)


# ---------------------------------------------------------------- A2-0707
class TestOutputToADirectory(unittest.TestCase):

    def test_convert_tt_to_a_directory(self):
        with tempfile.TemporaryDirectory() as td:
            tt = Path(td) / "hand.tt"
            tt.write_text("BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 "
                          "-100 0\n")
            out = Path(td) / "outdir"
            out.mkdir()
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_convert_tt",
                 str(tt), str(out)], cwd=REPO_ROOT, capture_output=True,
                text=True, env=_env(Path(td)), stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(f"cannot write {out}: is a directory", r.stderr)
            self.assertFalse((Path(td) / "outdir.part").exists())

    def test_write_text_atomic_leaves_no_part(self):
        from taxjson.lib.cli_diag import OutputWriteError, write_text_atomic
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "missing" / "x.json"
            with self.assertRaises(OutputWriteError) as cm:
                write_text_atomic(p, "{}")
            self.assertIn("cannot write", str(cm.exception))
            ok = Path(td) / "y.json"
            write_text_atomic(ok, "{}")
            self.assertEqual(ok.read_text(), "{}")
            self.assertFalse((Path(td) / "y.json.part").exists())


# ---------------------------------------------------------------- A2-0708
class TestTraceAndTableRoundAlike(unittest.TestCase):

    def test_recomputed_noise_rounds_like_the_saved_value(self):
        from taxjson.lib.report_model import fmt_money
        from taxjson.lib.trace_format import _fmt_money, _fmt_signed_money
        self.assertEqual(fmt_money(530.425), "530.42")
        self.assertEqual(_fmt_money(530.4250000000001), "$530.42")
        self.assertEqual(_fmt_signed_money(530.4250000000001), "+$530.42")


# ---------------------------------------------------------------- A2-0710
class TestByTickerFollowsRowsWithoutYear(unittest.TestCase):

    @rule("CA-INC-03")
    def test_deemed_dividend_pil(self):
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2025-01-06", "ABC.TO", 100, 1000,
                   currency="CAD"),
                tx("DIVIDEND_IN_LIEU", "2025-03-03", "ABC.TO", 0, 12.0,
                   gross_amount=12.0, currency="CAD", dealer_country="CA",
                   issuer_country="CA")]
        for year in (None, 2025):
            with self.subTest(year=year):
                r = _gains_one(book, "canada", year=year)
                bt = r["by_ticker"]["ABC.TO"]
                self.assertEqual(bt["total_pil"], 0.0)
                self.assertEqual(bt["total_div"], 12.0)


# --------------------------- A2-0709 / A2-0711 / A2-1220 / A2-1224 / A2-1218
class TestCanadaStockDividendNote(unittest.TestCase):

    @staticmethod
    def _book(adjust=None):
        from tax_rules.dual import tx
        b = [tx("BUYSELL", "2024-02-03", "QSC.TO", 1200, 12000,
                currency="CAD"),
             tx("BUYSELL", "2024-06-26", "QSC.TO", 180, 0.0, price=0.0,
                currency="CAD", type="stock_dividend")]
        if adjust:
            b.append(tx("ADJUST", adjust, "QSC.TO", 0, 1332.0,
                        currency="CAD"))
        return b

    @rule("CA-STKDIV-01")
    def test_wording_year_and_quiet_once_added(self):
        e24 = _gains_one(self._book(), "canada", year=2024)["_stderr"]
        self.assertIn("stock dividend of 180", e24)
        self.assertIn("books the ACB only", e24)
        self.assertNotIn("ACB and income", e24)
        # Another year's run: not this year's event.
        e25 = _gains_one(self._book(), "canada", year=2025)["_stderr"]
        self.assertNotIn("stock dividend of 180", e25)
        # The cost added (distributions.map's record-date row): quiet.
        e24b = _gains_one(self._book("2024-06-19"), "canada",
                          year=2024)["_stderr"]
        self.assertNotIn("stock dividend of 180", e24b)

    @rule("CA-STKDIV-01")
    def test_sheltered_book_is_quiet(self):
        import copy, contextlib, io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(copy.deepcopy(self._book()), [], [],
                      req=GainsRequest(country="canada", taxable=False,
                                       year=2024))
        self.assertNotIn("stock dividend of 180", err.getvalue())

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_income_dating_advice_not_for_a_sheltered_book(self):
        import copy, contextlib, io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2024-06-03", "ZZR.TO", 1000, 10000,
                   currency="CAD"),
                tx("ADJUST", "2025-01-08", "ZZR.TO", 0, -500.0, type="roc",
                   currency="CAD")]
        for taxable, want in ((True, True), (False, False)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                run_gains(copy.deepcopy(book), [], [], req=GainsRequest(
                    country="canada", taxable=taxable, year=2025))
            self.assertEqual("no record date" in err.getvalue(), want,
                             err.getvalue())


# ---------------------------------------------------------------- A2-1221
class TestTaxableTransferWording(unittest.TestCase):

    def test_run_names_the_account_not_a_flag(self):
        from taxjson.lib.pipeline import (TransferValidationError,
                                          prepare_books)
        from tax_rules.dual import tx
        book = [tx("TRANSFER", "2025-03-03", "XYZ.TO", 100, 1000,
                   currency="CAD", account="margin")]
        with self.assertRaises(TransferValidationError) as cm:
            prepare_books(book, taxable=True)
        self.assertIn("taxable account margin", str(cm.exception))
        self.assertNotIn("--taxable was set", str(cm.exception))


# ------------------------------------------ A2-1214 / A2-1216 / A2-1217 / A2-1215
class TestExportInputs(unittest.TestCase):

    def test_dust_threshold_refuses_nan_inf_negative(self):
        for bad in ("nan", "inf", "-1"):
            with self.subTest(bad=bad):
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_export",
                     "--dust-threshold", bad, "--report", "x.json"],
                    cwd=REPO_ROOT, capture_output=True, text=True,
                    env=_env(Path("/nonexistent")), stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("--dust-threshold", r.stderr)

    def test_transfer_in_counts_toward_the_round(self):
        from taxjson.bin.taxjson_export import _load_trade_events
        for opener in ("TRANSFER", "OPENING_BALANCE"):
            with self.subTest(opener=opener):
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "raw.json"
                    p.write_text(json.dumps({"transactions": [
                        {"action": opener, "symbol": "XYZ.US",
                         "quantity": 100, "date": "2026-01-02"},
                        {"action": "BUYSELL", "symbol": "XYZ.US",
                         "quantity": 50, "price": 10, "date": "2026-02-02"},
                        {"action": "BUYSELL", "symbol": "XYZ.US",
                         "quantity": -50, "price": 11,
                         "date": "2026-03-02"}]}))
                    ev = _load_trade_events([p])
                    self.assertEqual([e["action"] for e in ev["XYZ.US"]],
                                     ["BUY", "SELL"])


# ---------------------------------------------------------------- A2-1219
class TestStdinIsUtf8(unittest.TestCase):

    def test_piped_json_under_an_ascii_locale(self):
        doc = json.dumps({"transactions": [{
            "action": "DIVIDEND", "date": "2026-03-03", "time": "10:00:00",
            "symbol": "GLE.PA", "quantity": 0, "price": 0,
            "net_amount": 10.0, "gross_amount": 10.0, "currency": "EUR",
            "account": "m", "description": "Société Générale"}]},
            ensure_ascii=False).encode("utf-8")
        for mod in ("taxjson.bin.taxjson_sort", "taxjson.bin.taxjson_sum_income"):
            with self.subTest(mod=mod):
                r = subprocess.run(
                    [sys.executable, "-m", mod], cwd=REPO_ROOT,
                    input=doc, capture_output=True,
                    env=_env(Path("/nonexistent"), PYTHONIOENCODING="ascii:backslashreplace",
                             LC_ALL="C", LANG="C"))
                self.assertEqual(r.returncode, 0, r.stderr.decode(
                    "utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
