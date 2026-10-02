"""Re-audit-2 tests-pins (pins3, group g6b): fixes in taxjson_run.py's
reports, guards, wording gates and settings that held but that no test
failed on when reverted. Each TestCase names the A2 finding and the code
it pins; every test was checked to fail with that code reverted.

All data is synthetic (invented tickers, round amounts).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, projects_both

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"


def _run(root, *args, env=None):
    e = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=300)


def _doc(path, txs, **extra):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"transactions": txs}
    d.update(extra)
    path.write_text(json.dumps(d))
    return path


def _gain(symbol, cur, *, gain=-1.0, disallowed=0.0, term=None, **kw):
    e = {"symbol": symbol, "qty": -100.0, "proceeds": 999.0,
         "cost": 999.0 - gain + disallowed, "gain": gain,
         "direction": "LONG", "date": "2025-05-02",
         "date_settle": "2025-05-02", "commission": 0.0, "fee": 0.0,
         "disallowed_amount": disallowed, "permanently_disallowed": 0.0,
         "days_held": 30, "currency": cur, "account": "margin"}
    if term:
        e["term"] = term
    e.update(kw)
    return e


# ------------------------------------------------------------- A2-0912
class TestSumDeniedRoundingNote(unittest.TestCase):
    """A2-0912 (R1-166 second half): `sum`'s FOR THE RETURN rounds each
    row to the cent; the gain gap was labelled but the DENIED column
    (US: the code-W adjustment) differed from the gains files' unrounded
    total with no note. Both countries' notes are pinned (the US one was
    asserted by no test: taxjson_run.py cmd_sum, `if abs(_round_gap)`
    in the Form 8949 branch)."""

    def _proj(self, td, country):
        cur = "CAD" if country == "canada" else "USD"
        sfx = ".TO" if country == "canada" else ".US"
        term = None if country == "canada" else "SHORT_TERM"
        p = projects_both(td)[country]
        # Three rows, each with 1.004 denied: rounded rows 3.00, engine
        # 3.012 -> 3.01.
        _doc(p / "work" / "margin_gains.json",
             [_gain(f"S{i}{sfx}", cur, disallowed=1.004, term=term)
              for i in range(3)])
        return p

    @rule("CA-DISP-08")
    def test_canada_denied_gap_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td, "canada")
            r = _run(p, "sum")
            j = _run(p, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("unrounded total denied is 3.01 (-0.01 on the RETURN "
                      "row)", r.stdout)
        f = json.loads(j.stdout)["filing"]
        self.assertEqual(f["totals"]["denied"], 3.0)
        self.assertEqual(f["engine_denied_unrounded"], 3.01)

    @rule("US-RPT-09")
    def test_usa_adjustment_gap_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td, "usa")
            r = _run(p, "sum")
            j = _run(p, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FORM 8949", r.stdout)
        self.assertIn("unrounded total adjustment (g) is 3.01 (-0.01 on "
                      "the RETURN row)", r.stdout)
        f = json.loads(j.stdout)["filing"]
        self.assertEqual(f["totals"]["adjustment"], 3.0)
        self.assertEqual(f["engine_denied_unrounded"], 3.01)

    @rule("US-RPT-09")
    def test_usa_gain_gap_is_named(self):
        """The US branch's gain note (unpinned before)."""
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td)["usa"]
            _doc(p / "work" / "margin_gains.json",
                 [_gain(f"S{i}.US", "USD", gain=1.004, term="SHORT_TERM")
                  for i in range(3)])
            r = _run(p, "sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FORM 8949", r.stdout)
        self.assertIn("unrounded total gain is 3.01 (-0.01 on the RETURN "
                      "row)", r.stdout)


# ----------------------------------------------- A2-0934, A2-1586, A2-1587
def _t(action, date, sym, qty=0.0, net=0.0, cur="CAD", **kw):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": sym, "quantity": qty,
         "price": abs(net / qty) if qty else 0.0, "net_amount": net,
         "currency": cur}
    d.update(kw)
    return d


_LEAP1 = "ZZA270115C00050000.US"
_LEAP2 = "ZZB270115C00060000.US"
_FOP = "F:CL271217C00070000.US"


def _report_project(td):
    """Two or more rows behind every printed TOTAL, so a 'total = last
    row' accumulator shows; a short position in the list inventory; a
    futures option with a declared 1,000 multiplier."""
    root = Path(td)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
    _doc(root / "work" / "margin_raw.json", [
        _t("BUYSELL", "2024-12-16", _LEAP1, 2, -400.0, "USD"),
        _t("BUYSELL", "2024-12-16", _LEAP2, 1, -200.0, "USD"),
        _t("BUYSELL", "2025-02-03", "AAA.TO", 10, -100.0),
        _t("BUYSELL", "2025-02-04", "BBB.TO", 5, -250.0),
        _t("DIVIDEND_IN_LIEU", "2025-03-03", "AAA.TO", net=10.0,
           gross_amount=10.0, id="p1"),
        _t("DIVIDEND_IN_LIEU", "2025-03-04", "BBB.TO", net=20.0,
           gross_amount=20.0, id="p2"),
        _t("ADJUST", "2025-04-01", "AAA.TO", net=-5.0, type="roc",
           id="r1"),
        _t("ADJUST", "2025-04-02", "BBB.TO", net=-7.0, type="roc",
           id="r2"),
        _t("DIVIDEND", "2025-03-10", "ZZS.TO", net=60.0, gross_amount=60.0,
           id="d1"),
        _t("DIVIDEND", "2025-04-10", "ZZS.TO", net=40.0, gross_amount=40.0,
           id="d2"),
        _t("DIVIDEND", "2025-05-10", "ZZT.TO", net=50.0, gross_amount=50.0,
           id="d3")])

    def g(sym, date, gain, direction="LONG", qty=-1):
        return {"symbol": sym, "date": date, "date_settle": date,
                "qty": qty, "proceeds": 1000.0 + gain, "cost": 1000.0,
                "gain": gain, "raw_gain": gain, "disallowed_amount": 0.0,
                "days_held": 100, "direction": direction,
                "currency": "CAD", "account": "margin"}
    _doc(root / "work" / "margin_gains.json", [
        g(_LEAP1, "2025-06-02", 100.0), g(_LEAP2, "2025-07-02", 50.0),
        g("ZZC250620C00030000.TO", "2025-05-02", 30.0, "SHORT", 1),
        g("ZZD250620C00040000.TO", "2025-05-03", 70.0, "SHORT", 1),
        g("AAA.TO", "2025-08-01", 200.0, qty=-5)],
        summary={"year": "2025", "tax_date_basis": "settle"},
        inventory=[
            {"symbol": "AAA.TO", "qty": 100.0, "total_cost": 1000.0},
            {"symbol": "BBB.TO", "qty": -50.0, "total_cost": -1000.0},
            {"symbol": _FOP, "qty": 2.0, "total_cost": 4000.0,
             "multiplier": 1000}])
    _doc(root / "work" / "margin_raw_gains.json", [
        g("AAA.TO", "2025-08-01", 200.0), g("BBB.TO", "2025-09-01", 300.0)],
        summary={"year": "all", "tax_date_basis": "settle"})
    (root / "capital_gains_dividends.map").write_text(
        "ZZS.TO 2025 all\nZZT.TO 2025 all\n")
    return root


class TestPrintedReportTotals(unittest.TestCase):
    """A2-0934, A2-1586 (S040-22 class): each printed TOTAL line over two
    or more rows, so 'total = last row' fails — trades TOTAL BUY, dil-sum,
    roc-sum, divs-sum TOTAL CAPITAL-GAINS DIVIDENDS (CA-INC-06), ccd-sum,
    leaps-sum, leaps, gains (native) — and `list`'s book cost with a
    short (signed, not abs). A2-1587 (S026-22): `list` COST/SH divides a
    futures option by its declared multiplier."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _report_project(cls._td.name)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _out(self, *args):
        r = _run(self.root, *args)
        self.assertEqual(r.returncode, 0, (args, r.stderr[-2000:]))
        return r.stdout

    def test_trades_total_buy(self):
        self.assertIn("TOTAL BUY:      350.00 CAD", self._out("trades"))

    def test_dil_sum_total(self):
        self.assertIn("TOTAL DIVIDEND IN LIEU: 30.00 CAD",
                      self._out("dil-sum"))

    def test_roc_sum_total(self):
        self.assertIn("TOTAL CAPITAL RETURNED (ACB reduced): 12.00 CAD",
                      self._out("roc-sum"))

    @rule("CA-INC-06")
    def test_divs_sum_capital_gains_dividend_total(self):
        out = self._out("divs-sum")
        self.assertIn("TOTAL CAPITAL-GAINS DIVIDENDS: 150.00 CAD", out)
        j = json.loads(self._out("divs-sum", "--json"))
        self.assertEqual(j["capital_gains_dividends"]["totals"],
                         {"CAD": 150.0})

    def test_ccd_sum_total(self):
        self.assertIn("TOTAL COVERED-CALL GAIN: 100.00 CAD",
                      self._out("ccd-sum"))

    def test_leaps_sum_and_leaps_totals(self):
        self.assertIn("TOTAL REALIZED GAIN: 150.00 CAD",
                      self._out("leaps-sum"))
        self.assertIn("TOTAL REALIZED GAIN: 150.00 CAD", self._out("leaps"))

    def test_gains_native_total(self):
        self.assertIn("TOTAL GAIN: 500.00 CAD", self._out("gains"))

    def test_list_book_cost_is_signed_with_a_short(self):
        self.assertIn("3 position(s), total book cost 4,000.00 CAD",
                      self._out("list"))
        j = json.loads(self._out("list", "--json"))
        self.assertEqual(j["totals"]["book_cost"], 4000.0)

    def test_list_futures_option_cost_per_share_uses_multiplier(self):
        row = next(r for r in json.loads(self._out("list", "--json"))["rows"]
                   if r["symbol"] == _FOP)
        # 4,000 / (2 contracts x 1,000) = 2.00, not 2,000 per contract.
        self.assertAlmostEqual(row["cost_per_share"], 2.0)
        line = next(ln for ln in self._out("list").splitlines()
                    if _FOP in ln)
        self.assertIn(" 2.00 ", line)


# ------------------------------------------------------ A2-0911, A2-1604
class TestUnreadableBooksStopTheView(unittest.TestCase):
    """S041-17: a view never answers from part of the books. A2-0911:
    `gains` reads <acct>_raw_gains.json through _load_json_or_die
    (cmd_gains); A2-1604: the same die-on-unreadable load in
    _dist_adjust_rows (roc / roc-sum), _box18_fractions (divs-sum,
    estimate, t1135) and cmd_option_boundary. A warn-and-continue load
    made each answer from the readable part with exit 0."""

    def _proj(self, td):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n'
            '[accounts.cash]\ntype = "taxable"\n')
        return root

    def test_gains_stops_on_a_truncated_raw_gains_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            _doc(root / "work" / "margin_raw_gains.json",
                 [_gain("AAA.TO", "CAD", gain=200.0)],
                 summary={"year": "all", "tax_date_basis": "settle"})
            (root / "work" / "cash_raw_gains.json").write_text(
                '{"transactions": [')
            r = _run(root, "gains")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("cash_raw_gains.json", r.stderr)
        self.assertNotIn("TOTAL GAIN", r.stdout)

    def test_dist_adjust_rows_stops_on_a_truncated_base_book(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            _doc(root / "work" / "margin_base.json", [])
            (root / "work" / "cash_base.json").write_text('{"transac')
            with self.assertRaises(SystemExit) as cm:
                R._dist_adjust_rows(root / "work", ["margin", "cash"],
                                    lambda _d: True)
        self.assertIn("cash_base.json", str(cm.exception.code))

    @rule("CA-INC-06")
    def test_box18_fractions_stops_on_a_truncated_native_book(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            _doc(root / "work" / "margin_raw.json", [
                _t("DIVIDEND", "2025-03-10", "ZZS.TO", net=60.0,
                   gross_amount=60.0, id="d1")])
            (root / "work" / "cash_raw.json").write_text('{"transac')
            (root / "capital_gains_dividends.map").write_text(
                "ZZS.TO 2025 all margin\n")
            with self.assertRaises(SystemExit) as cm:
                R._box18_fractions(root)
        self.assertIn("cash_raw.json", str(cm.exception.code))

    def test_option_boundary_stops_on_a_truncated_base_book(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            _doc(root / "work" / "margin_base.json", [])
            (root / "work" / "cash_base.json").write_text('{"transac')
            r = _run(root, "option-boundary")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("cash_base.json", r.stderr)
        self.assertNotIn("OPTION YEAR-BOUNDARY REVIEW", r.stdout)


# ------------------------------------------------------------- A2-0929
class TestFloatYearRefused(unittest.TestCase):
    """S039-06: `year = 2024.0` passed the plausible-range test and the
    summaries printed 'No trades in tax year 2024.0' with exit 0. The
    shared check is lib/config_check.settings_problems (A2-1373), which
    every config reader runs; the summaries (not only `run`) refuse."""

    def test_float_year_is_refused_by_the_summaries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024.0\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _doc(root / "work" / "margin_raw.json",
                 [_t("BUYSELL", "2024-02-03", "AAA.TO", 10, -100.0)])
            for cmd in ("trades-sum", "divs-sum"):
                with self.subTest(cmd=cmd):
                    r = _run(root, cmd)
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    self.assertIn("year must be an integer tax year, got "
                                  "2024.0", r.stderr)

    def test_settings_problems_names_a_float_year(self):
        from taxjson.lib.config_check import settings_problems
        p = settings_problems({"settings": {"country": "canada",
                                            "year": 2024.0}})
        self.assertIn("[settings] year must be an integer tax year, got "
                      "2024.0", p)


# ------------------------------------------------------------- A2-1609
class TestCurrencySettingsSpelling(unittest.TestCase):
    """S030-20 siblings: source_currencies are upper-cased and stripped
    for every reader (_normalize_settings), and base_currency must be a
    3-letter code (lib/config_check) — 'CA' / 'C$D' get the spelling
    message, not a misleading country-currency one."""

    def test_source_currencies_are_canonical(self):
        from taxjson.bin import taxjson_run as R
        cfg = {"settings": {"country": "canada", "base_currency": "CAD",
                            "source_currencies": [" usd", "Eur "]}}
        R._normalize_settings(cfg)
        self.assertEqual(cfg["settings"]["source_currencies"],
                         ["USD", "EUR"])

    def test_base_currency_must_be_three_letters(self):
        from taxjson.lib.config_check import settings_problems
        for bad in ("CA", "C$D", "CADD"):
            with self.subTest(base=bad):
                p = settings_problems({"settings": {
                    "country": "canada", "base_currency": bad}})
                self.assertEqual(len(p), 1, p)
                self.assertIn("base_currency must be a 3-letter currency "
                              "code", p[0])


# ------------------------------------------------------------- A2-1533
class TestTaxDateDepartureWording(unittest.TestCase):
    """partition INPUTS-05 (validate_config): a tax_date against the
    country's practice is said in that country's own terms — a US
    project is never told 'CRA practice ... the Canadian default'."""

    def _warn(self, country, tax_date):
        from taxjson.bin import taxjson_run as R
        cur = "CAD" if country == "canada" else "USD"
        w = R.validate_config({
            "settings": {"year": 2025, "country": country,
                         "base_currency": cur, "tax_date": tax_date},
            "accounts": {"margin": {"type": "taxable"}}})
        return " ".join(x for x in w if "tax_date" in x)

    @rule("US-DATE-02")
    @rule_absent("CA-DATE-01", country="usa")
    def test_usa_settle_names_the_irs_trade_date(self):
        w = self._warn("usa", "settle")
        self.assertIn("the IRS dates a disposition by its TRADE date (the "
                      "US default)", w)
        self.assertNotIn("CRA", w)
        self.assertNotIn("Canadian", w)

    @rule("CA-DATE-02")
    def test_canada_trade_names_cra_settlement_practice(self):
        w = self._warn("canada", "trade")
        self.assertIn("CRA practice dates a disposition by its SETTLEMENT "
                      "date (the Canadian default)", w)
        self.assertNotIn("IRS", w)


# ------------------------------------------------------------- A2-1577
def _ago(n):
    from datetime import date, timedelta
    return (date.today() - timedelta(days=n)).isoformat()


def _trade_row(day, sym, qty, net, acct="margin"):
    return {"action": "BUYSELL", "date": day, "date_settle": day,
            "time": "10:00:00", "symbol": sym, "quantity": qty,
            "net_amount": net, "currency": "USD", "account": acct,
            "price": abs(net / qty)}


class TestBuySellCheckWordingByCountry(unittest.TestCase):
    """buy-check / sell-check say each country's own rule (the `_usa`
    ternaries of cmd_buy_check / cmd_sell_check): Canada's s.54 still-
    held-at-day-30 condition and 'sheltered'/'registered' never reach a
    US project, whose text says IRA / tax-deferred and the §1091
    partial-sale rule. The radar's dates are relative to today."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        acc = ('[accounts.margin]\ntype = "taxable"\n'
               '[accounts.ira]\ntype = "sheltered"\n')
        from datetime import date
        taxable = [
            _trade_row(_ago(5), "OPN.US", 100, 1000.0),       # window open
            _trade_row(_ago(100), "LSS.US", 100, 1500.0),     # recent loss
            _trade_row(_ago(5), "LSS.US", -100, 1200.0),
            _trade_row(_ago(200), "OLD.US", 100, 1500.0)]     # quiet
        sheltered = [_trade_row(_ago(100), "IRA.US", 10, 100.0, "ira")]
        p = projects_both(cls._td.name, year=date.today().year,
                          accounts=acc, files={
                              "work/margin_base.json": json.dumps(
                                  {"transactions": taxable}),
                              "work/sheltered_base.json": json.dumps(
                                  {"transactions": sheltered})})
        cls.out = {}
        for c, root in p.items():
            for cmd, sym in (("buy-check", "OPN.US"),
                             ("buy-check", "LSS.US"),
                             ("sell-check", "IRA.US"),
                             ("sell-check", "OLD.US")):
                cls.out[(c, cmd, sym)] = cli(root, cmd, sym).stdout

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_buy_check_open_window(self):
        ca = self.out[("canada", "buy-check", "OPN.US")]
        us = self.out[("usa", "buy-check", "OPN.US")]
        self.assertIn("would be superficial if you still hold the bought "
                      "shares 30 days after the sale — a full exit is not",
                      ca)
        self.assertIn("a PARTIAL loss sale of this name within 30 days of "
                      "a buy would be a wash sale; selling the full "
                      "position is not", us)
        self.assertNotIn("30 days after the sale", us)

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_buy_check_recent_loss(self):
        ca = self.out[("canada", "buy-check", "LSS.US")]
        us = self.out[("usa", "buy-check", "LSS.US")]
        self.assertIn("if you still hold the shares 30 days after that "
                      "sale (DEFERRED if bought taxable, PERMANENT if "
                      "bought sheltered)", ca)
        self.assertIn("(DEFERRED if bought taxable, PERMANENT if bought in "
                      "an IRA)", us)
        self.assertNotIn("still hold", us)
        self.assertNotIn("sheltered", us)

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_sell_check_sheltered_only_and_clear(self):
        ca = self.out[("canada", "sell-check", "IRA.US")]
        us = self.out[("usa", "sell-check", "IRA.US")]
        self.assertIn("held only in sheltered account(s) — nothing to sell "
                      "at a loss (a registered disposition", ca)
        self.assertIn("held only in IRA(s) — nothing to sell at a loss (a "
                      "tax-deferred disposition", us)
        ca = self.out[("canada", "sell-check", "OLD.US")]
        us = self.out[("usa", "sell-check", "OLD.US")]
        self.assertIn("(taxable or sheltered) for 30 days", ca)
        self.assertIn("(taxable or IRA) for 30 days", us)
        for c in ("IRA.US", "OLD.US"):
            u = self.out[("usa", "sell-check", c)]
            self.assertNotIn("sheltered", u)
            self.assertNotIn("registered", u)


# ------------------------------------------------------------- A2-1535
class TestSpinoffInTwoBrokerAccounts(unittest.TestCase):
    """S029-11 twins: a spin-off held in two IB broker accounts of one
    taxjson account is ONE event (combine_broker_copies) at the elect
    re-extract (_reextract_pending_entry, an `elect --set` typed before
    any run) and in corp_views._current_events (`spinoffs`): FMV 900 +
    450, not one copy's 900 or 450."""

    @rule("CA-CORP-07")
    def test_elect_before_run_and_spinoffs_see_the_combined_fmv(self):
        import test_fix_corp as T
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        spin = ("PARNT(CA0000000777) Spinoff  1 for 5 (SPNCO, SPINCO CORP, "
                "CA0000000778)")

        def stmt(acct, q):
            return (T._IB_HEAD.format(acct=acct) + T._IB_TRADES
                    + T._ib_buy("PARNT", q * 5, 60) + T._IB_CA
                    + T._ib_ca(spin, q, q * 45))
        with tempfile.TemporaryDirectory() as td:
            root = T._project(td, {
                "ib_u1.csv": stmt("U5550001", 20),   # pii-ok (synthetic)
                "ib_u2.csv": stmt("U5550002", 10)})  # pii-ok (synthetic)
            eid = parse_ib_corporate_actions(
                root / "inputs" / "margin" / "ib_u1.csv",
                "margin")[0].event_id
            e = _run(root, "elect", "margin", "--set",
                     f"{eid}=taxable_deemed_dividend")
            self.assertEqual(e.returncode, 0, e.stderr[-2000:])
            man = json.loads((root / "inputs" / "margin" /
                              "manifest.json").read_text())
            self.assertIn("FMV 1350.00 CAD",
                          man["elections"][eid]["summary"])
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s = _run(root, "spinoffs", "--json")
            self.assertEqual(s.returncode, 0, s.stderr[-2000:])
        d = json.loads(s.stdout)
        rows = d.get("spinoffs", d if isinstance(d, list) else [])
        self.assertEqual([x["broker_fmv"] for x in rows], [1350.0])


# ------------------------------------------------------------- A2-1565
class TestLeapsWalkCarriesTheSplitRatio(unittest.TestCase):
    """_leaps_contracts carries an option rename-SPLIT (an OCC
    adjustment) WITH its ratio: short 1, renamed 2-for-1, a buy of 2 is
    a buy-to-close, not a LEAPS entry; a long LEAPS renamed 2-for-1 is
    2 contracts open."""

    OLD, NEW = "XYZ270115C00050000.US", "XYZ1270115C00025000.US"

    def _leaps(self, rows):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _doc(root / "work" / "margin_raw.json", rows)
            return R._leaps_contracts(root, "margin", "leaps")

    def _r(self, day, act, sym, q, new=""):
        return {"date": day, "date_settle": day, "time": "10:00:00",
                "action": act, "symbol": sym, "symbol_new": new,
                "quantity": q, "account": "margin", "net_amount": 0.0,
                "currency": "USD"}

    def test_buy_to_close_after_a_ratio_rename_is_not_a_leaps(self):
        self.assertEqual(self._leaps([
            self._r("2025-01-06", "BUYSELL", self.OLD, -1),
            self._r("2025-03-03", "SPLIT", self.OLD, 2.0, self.NEW),
            self._r("2025-04-01", "BUYSELL", self.NEW, 2)]), {})

    def test_renamed_leaps_open_quantity_scales(self):
        got = self._leaps([
            self._r("2025-01-06", "BUYSELL", self.OLD, 1),
            self._r("2025-03-03", "SPLIT", self.OLD, 2.0, self.NEW)])
        self.assertEqual(got.get(self.NEW), 2.0)


# ------------------------------------------------------------- A2-1610
class TestOtherYearBanner(unittest.TestCase):
    """S067-12 twins: t1135 and form-export say when the work/ books were
    built for another tax year (_warn_artifact_year), as sum does."""

    def test_t1135_and_form_export_banner(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _doc(root / "work" / "margin_base.json", [
                _t("BUYSELL", "2025-02-03", "AAA.US", 10, -1000.0),
                _t("BUYSELL", "2025-05-03", "AAA.US", -10, 1200.0)])
            _doc(root / "work" / "margin_gains.json",
                 [_gain("AAA.US", "CAD", gain=200.0)],
                 summary={"year": "2025", "tax_date_basis": "settle"})
            for cmd in ("t1135", "form-export"):
                with self.subTest(cmd=cmd):
                    r = _run(root, cmd)
                    self.assertIn("built for another tax year: margin "
                                  "(2025)", r.stderr,
                                  (r.stdout + r.stderr)[-1500:])


# ------------------------------------------------------------- A2-1593
class TestBox18EstimateByIssuer(unittest.TestCase):
    """_box18_into_estimate moves a mapped T5 box 18 amount out of the
    Canadian dividends for a Canadian issuer and out of the foreign
    dividends otherwise (income_dating.is_canadian_issuer) — both
    branches pinned (CA-INC-06)."""

    @rule("CA-INC-06")
    def test_each_issuer_leaves_its_own_bucket(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            rows = [_t("DIVIDEND", "2025-03-10", "ZZS.TO", net=100.0,
                       gross_amount=100.0, id="d1"),
                    _t("DIVIDEND", "2025-03-11", "ZZF.US", net=30.0,
                       gross_amount=30.0, id="d2")]
            _doc(root / "work" / "margin_raw.json", rows)
            _doc(root / "work" / "margin_base.json", rows)
            (root / "capital_gains_dividends.map").write_text(
                "ZZS.TO 2025 all\nZZF.US 2025 all\n")
            est = {"div_ca": 500.0, "div_foreign": 300.0}
            fb = {"margin": 300.0}
            R._box18_into_estimate(root, est, ["margin"], 2025, fb)
        self.assertAlmostEqual(est["div_ca"], 400.0)
        self.assertAlmostEqual(est["div_foreign"], 270.0)
        self.assertAlmostEqual(fb["margin"], 270.0)
        self.assertAlmostEqual(est["cg_div"], 130.0)


if __name__ == "__main__":
    unittest.main()
