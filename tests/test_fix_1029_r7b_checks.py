"""Regression tests for fix round 7 (wave B), group checks: check-dates,
check-tt, settlement inference, the cost-timeline reader, filing
readiness, the checklist state lock, instalments and the views' exit
codes.

Synthetic data only; account labels, ids and tickers are made up.
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule

from taxjson.lib.check_dates import analyze as dates_analyze


def _dates_project(td, rows, year=2025, kind="ib"):
    root = Path(td)
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / f"m_{kind}.json").write_text(json.dumps(rows))
    (work / "m_sources.list").write_text(f"{kind}/a.csv\n")
    cfg = {"settings": {"year": year},
           "accounts": {"m": {"type": "taxable"}}}
    return root, cfg


def _dr(d, settle, sym="ABC.US", **kw):
    row = {"action": "BUYSELL", "date": d, "date_settle": settle,
           "time": "10:00:00", "symbol": sym, "quantity": 1,
           "price": 10.0, "currency": "USD", "description": ""}
    row.update(kw)
    return row


def _rule_text(rid):
    from taxjson.lib.tax_logic import catalog, rule_country
    return catalog(rule_country(rid))[rid].text


def _issues(rows, year=2025, kind="ib"):
    with tempfile.TemporaryDirectory() as td:
        root, cfg = _dates_project(td, rows, year=year, kind=kind)
        return dates_analyze(root, cfg, today=date(2026, 1, 15))


class TestOldAcquisitionInRange(unittest.TestCase):
    """TJ-0820: a 1989 purchase that establishes today's cost is real
    history, not a date "far outside the project year"."""

    def test_1989_purchase_is_checked_not_refused(self):
        buy = _dr("1989-06-01", "1989-06-08")
        sell = _dr("2025-06-02", "2025-06-03", quantity=-1)
        for year, rows in ((1989, [buy]), (2025, [buy, sell])):
            doc = _issues(rows, year=year)
            codes = {(i["date"], i["code"]) for i in doc["issues"]}
            self.assertNotIn(("1989-06-01", "out-of-range"), codes, year)
            self.assertEqual(doc["errors"], 0, (year, doc["issues"]))


class TestMlkClosureEra(unittest.TestCase):
    """TJ-0809: the NYSE first closed for Martin Luther King Jr. Day in
    1998; January 20, 1997 was a trading day (a Federal Reserve holiday,
    so not a US settlement day)."""

    def test_calendar(self):
        from taxjson.lib import market_calendar as mc
        self.assertTrue(mc.is_trading_day("1997-01-20", "USD"))
        self.assertFalse(mc.is_trading_day("1998-01-19", "USD"))
        self.assertFalse(mc.is_settlement_day("1997-01-20", "USD"))
        # Not a federal holiday before 1986.
        self.assertTrue(mc.is_settlement_day("1985-01-21", "USD"))

    def test_check_dates(self):
        doc = _issues([_dr("1997-01-20", "1997-01-23"),
                       _dr("1998-01-19", "1998-01-22")])
        codes = {(i["date"], i["code"]) for i in doc["issues"]}
        self.assertNotIn(("1997-01-20", "holiday-trade"), codes)
        self.assertIn(("1998-01-19", "holiday-trade"), codes)


class TestWeekendStockDividend(unittest.TestCase):
    """TJ-0695: a stock dividend the broker posts on a Saturday is an
    event, not an exchange trade or a weekend settlement."""

    def _row(self, day, **kw):
        row = _dr(day, day, sym="SYN.TO", price=0.0, currency="CAD",
                  description="SYN CORP STK DIV ON 10 SHS",
                  type="stock_dividend", time="09:30:00")
        row.update(kw)
        return row

    def test_saturday_posting_is_not_an_error(self):
        for day in ("2025-05-30", "2025-05-31"):
            doc = _issues([self._row(day)], kind="questrade")
            self.assertEqual(doc["errors"], 0, (day, doc["issues"]))
            self.assertEqual(doc["warnings"], 0, (day, doc["issues"]))

    def test_event_settling_before_its_date_is_still_an_error(self):
        doc = _issues([self._row("2025-05-31", date_settle="2025-05-29")],
                      kind="questrade")
        codes = {i["code"] for i in doc["issues"]}
        self.assertIn("settle-before-trade", codes)

    def test_a_saturday_purchase_is_still_an_error(self):
        doc = _issues([_dr("2025-05-31", "2025-06-03", sym="SYN.TO",
                           currency="CAD")], kind="questrade")
        codes = {i["code"] for i in doc["issues"]}
        self.assertIn("weekend-trade", codes)


class TestT5SettlementEra(unittest.TestCase):
    """TJ-0833: North American shares settled T+5 before June 7, 1995
    (SEC Rule 15c6-1; Canada moved with the US)."""

    @rule("CA-DATE-04")
    def test_canada_rule(self):
        from taxjson.lib.dates import settlement_date, settlement_lag_days
        self.assertEqual(settlement_lag_days("1994-01-03", "CAD"), 5)
        self.assertEqual(settlement_date("1994-01-03", "USD"), "1994-01-10")
        self.assertEqual(settlement_date("1995-06-06", "CAD"), "1995-06-13")
        self.assertEqual(settlement_date("1995-06-07", "CAD"), "1995-06-12")
        # A US-dollar unit on a Canadian market: Canada's cycle.
        self.assertEqual(settlement_lag_days("1994-01-03", "CAD+USD"), 5)
        # Options stay T+1; no earlier era is modelled elsewhere.
        self.assertEqual(settlement_lag_days("1994-01-03", "USD", True), 1)
        self.assertEqual(settlement_lag_days("1994-01-03", "GBP"), 3)
        self.assertIn("T+5 before 1995-06-07", _rule_text("CA-DATE-04"))

    @rule("US-DATE-04")
    def test_us_rule(self):
        from taxjson.lib.dates import (last_trade_date_settling_by,
                                       settlement_lag_days)
        self.assertEqual(settlement_lag_days("1995-06-06", "USD"), 5)
        self.assertEqual(settlement_lag_days("1995-06-07", "USD"), 3)
        self.assertEqual(last_trade_date_settling_by("1994-01-10", "USD"),
                         "1994-01-03")
        self.assertIn("T+5 before 1995-06-07", _rule_text("US-DATE-04"))

    def test_check_dates_takes_t5_as_standard(self):
        doc = _issues([_dr("1994-01-03", "1994-01-10")])
        self.assertEqual(doc["issues"], [])


_US_TOML = ('[settings]\nyear = 2025\ncountry = "usa"\n'
            'base_currency = "USD"\n'
            '[accounts.margin]\ntype = "taxable"\n')


class TestCheckTtUsOpening(unittest.TestCase):
    """TJ-0831: check-tt refuses a US OPENING line without its lot date,
    as `tjs run` does (US-OPEN-01), naming the line."""

    def _project(self, td, opening, toml=_US_TOML):
        root = Path(td)
        (root / "taxjson.toml").write_text(toml)
        d = root / "inputs" / "margin"
        d.mkdir(parents=True)
        (d / "trades.tt").write_text(
            opening + "\nBUYSELL 2025-06-03 09:30:00 SYN.US -10 USD 15 150 0"
            "\n")
        return root

    def _errors(self, root, file=False):
        from taxjson.lib import check_tt as CT
        accounts = {"margin": {"type": "taxable"}}
        if file:
            doc = CT.check_paths([root / "inputs/margin/trades.tt"],
                                 root=root)
        else:
            doc = CT.check_project(root, accounts)
        return [p for p in doc["problems"] if p["severity"] == CT.ERROR]

    def test_missing_lot_date_is_an_error_of_its_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "OPENING 2024-12-31 SYN.US 10 USD 100")
            for file in (False, True):
                errs = self._errors(root, file=file)
                self.assertEqual(len(errs), 1, errs)
                self.assertEqual(errs[0]["line"], 1)
                self.assertIn("has no lot date", errs[0]["message"])
                self.assertIn("US-OPEN-01", errs[0]["message"])

    def test_dated_lot_and_canada_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td, "OPENING 2024-12-31 SYN.US 10 USD 100 2024-01-02")
            self.assertEqual(self._errors(root), [])
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td, "OPENING 2024-12-31 SYN.US 10 USD 100",
                toml=_US_TOML.replace('"usa"', '"canada"').replace(
                    '"USD"', '"CAD"'))
            self.assertEqual(self._errors(root), [])


def _timeline():
    """A cost timeline the engine wrote (Canada, one pool)."""
    import contextlib
    import io
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.pipeline import GainsRequest, run_gains
    rows = [TaxTransaction(action="BUYSELL", date="2025-02-03",
                           date_settle="2025-02-04", time="10:00:00",
                           symbol="SYN.TO", quantity=10, net_amount=-100.0,
                           currency="CAD", account="margin", id="b1")]
    with contextlib.redirect_stderr(io.StringIO()):
        res = run_gains(rows, [], [], GainsRequest(
            country="canada", taxable=True, year=2025, cost_timeline=True,
            phantom_hint=False))
    return json.loads(json.dumps(res["cost_timeline"]))


class TestCostTimelineRows(unittest.TestCase):
    """TJ-0684: a damaged cost timeline (a short row, a missing field,
    a text quantity) is refused, never read as zero cost or no
    holdings."""

    def test_the_engines_timeline_passes(self):
        from taxjson.lib import cost_timeline as CT
        tl = _timeline()
        self.assertIsNone(CT.check(tl))
        inv = CT.inventory_on(tl, "2025-12-31")
        self.assertEqual([(i["symbol"], i["qty"], i["total_cost"])
                          for i in inv], [("SYN.TO", 10.0, 100.0)])

    def test_damaged_timelines_are_refused(self):
        from taxjson.lib import cost_timeline as CT
        short = _timeline()
        short["rows"][0] = short["rows"][0][:5]
        self.assertIn("row 0 has 5 values", CT.check(short))
        bare = {"version": 1, "fields": ["seq"], "rows": [[0]]}
        self.assertIn("no field 'date'", CT.check(bare))
        nofields = _timeline()
        del nofields["fields"]
        self.assertIn("field names are missing", CT.check(nofields))
        text = _timeline()
        text["rows"][0][4] = "10"
        self.assertEqual(CT.check(text), "row 0: qty is '10'")
        nan = _timeline()
        nan["rows"][0][5] = float("nan")
        self.assertIn("row 0: total_cost is nan", CT.check(nan))

    def test_dated_list_refuses_a_damaged_sidecar(self):
        from _qa_project import project, tj
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/trades.tt": (
                "BUYSELL 2024-02-01 10:00:00 SYN.TO 10 CAD 10 100 0\n")})
            tj(root, "run", "--no-input")
            tls = sorted((root / "work").glob("*timeline*.json"))
            self.assertTrue(tls)
            for p in tls:
                doc = json.loads(p.read_text())
                doc["rows"] = [r[:5] for r in doc["rows"]]
                p.write_text(json.dumps(doc))
            r = tj(root, "list", "--date", "2024-06-30", check=False)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("has 5 values", r.stderr)
            self.assertIn("Run `tjs run` to rebuild it", r.stderr)


_PREMIUM_TT = (
    "BUYSELL 2025-01-02 10:00:00 SYN.US 1 CAD 10 10 0\n"
    "BUYSELL 2025-01-03 10:00:00 SYN.US -1 CAD 10 10 0\n"
    "BUYSELL 2025-01-06 10:00:00 SYN250620P00010000.US -1 CAD 1 100 0\n"
    "ASSIGN 2025-06-20 16:00:00 SYN250620P00010000.US 1 CAD 0 0 0\n")


class TestReadinessUnconsumedPremium(unittest.TestCase):
    """TJ-0824: the engine's warning that an assignment's premium is in
    no gain (its stock delivery is missing) leaves filing readiness
    incomplete, and the checklist's form-export step says so."""

    def test_build(self):
        from taxjson.lib import readiness as RD
        doc = RD.build(unconsumed_premium=["1 unconsumed ..."])
        self.assertFalse(doc["complete"])
        self.assertEqual([i["kind"] for i in doc["issues"]],
                         ["unconsumed-premium"])
        self.assertTrue(RD.build()["complete"])

    def test_lines_from_the_diag(self):
        from taxjson.lib import readiness as RD
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains_wash.json"
            (Path(td) / "margin_gains.json.diag").write_text(
                "warning: 1 unconsumed option-assignment adjustment(s) at "
                "end of canada gains run — SYN.US (margin): -100.00.\n"
                "warning: something else\n")
            got = RD.unconsumed_premium_lines([g])
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("1 unconsumed option-assignment"))

    def test_project(self):
        from _qa_project import project, tj
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/trades.tt": _PREMIUM_TT})
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace("year = 2024", "year = 2025").replace(
                    "option_grant_timing_since = 2024",
                    "option_grant_timing_since = 2025"))
            tj(root, "run", "--no-input")
            for cmd in ("form-export", "sum"):
                doc = json.loads(tj(root, cmd, "--json").stdout)
                rd = doc["readiness"]
                self.assertFalse(rd["complete"], cmd)
                self.assertIn("unconsumed-premium",
                              [i["kind"] for i in rd["issues"]], cmd)
            doc = json.loads(tj(root, "checklist", "--run", "--only",
                                "form-export", "--json", check=False).stdout)
            step = doc["steps"][0]
            self.assertEqual(step["status"], "attention", step)
            self.assertIn("premium is in no gain", step["detail"])
            self.assertIn("option-assignment premium in no gain",
                          tj(root, "sum").stdout)


class TestChecklistResetTakesTheLock(unittest.TestCase):
    """TJ-0854: `checklist --reset` waits for a mark being written; it
    never runs between the mark's read and its save, so the removed
    marks never come back."""

    def test_reset_waits_for_a_mark_in_progress(self):
        import threading
        from unittest import mock
        from taxjson.lib import checklist as cl
        old, new = cl.item_ids()[:2]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cl.set_override(root, 2025, old, cl.MARK_DONE)
            real_save = cl.save_state
            seen = {}

            def save_with_a_reset_waiting(r, state, year):
                t = threading.Thread(
                    target=lambda: seen.setdefault("reset",
                                                   cl.reset_state(root)))
                t.start()
                t.join(0.5)
                seen["blocked"] = t.is_alive()
                real_save(r, state, year)
                seen["thread"] = t

            with mock.patch.object(cl, "save_state",
                                   save_with_a_reset_waiting):
                cl.set_override(root, 2025, new, cl.MARK_DONE)
            seen["thread"].join(5)
            self.assertTrue(seen["blocked"])
            self.assertTrue(seen["reset"])
            # The mark came first, so the reset leaves no marks at all.
            self.assertFalse((root / cl.STATE_FILE).exists())
            self.assertEqual(cl.load_state(root)["overrides"], {})


class TestInstalmentWeekendBalanceDay(unittest.TestCase):
    """TJ-0915: April 30, 2023 was a Sunday, so a payment of the 2022
    balance on Monday May 1 was on time: it belongs to 2022 (and earns
    no credit interest; interest still runs to April 30)."""

    def _config(self, year, day):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                f'[settings]\nyear = {year}\ncountry = "canada"\n'
                f'base_currency = "CAD"\n[instalments]\n'
                f'paid = [{{ date = "{day}", amount = 500 }}]\n')
            return R._instalment_config(root, year)

    @rule("CA-INST-DUE")
    def test_next_business_day_belongs_to_the_year(self):
        from taxjson.bin import taxjson_instalments as INST
        self.assertEqual(INST.balance_payment_deadline(2022),
                         date(2023, 5, 1))
        self.assertEqual(INST.balance_payment_deadline(2025),
                         date(2026, 4, 30))
        self.assertEqual(self._config(2022, "2023-05-01")["paid"],
                         [{"date": "2023-05-01", "amount": 500.0}])
        self.assertIn("next business day is on time",
                      _rule_text("CA-INST-DUE"))

    def test_a_weekday_april_30_still_ends_the_year(self):
        # 2026-04-30 is a Thursday: May 1 is outside 2025.
        with self.assertRaises(SystemExit) as cm:
            self._config(2025, "2026-05-01")
        self.assertIn("outside tax year 2025", str(cm.exception))

    def test_the_late_payment_earns_no_credit(self):
        from taxjson.bin import taxjson_instalments as INST
        kw = dict(year=2022, basis="current_year", current_net_tax=8000.0,
                  annual_rate=0.05, as_of=date(2023, 6, 1))
        late = INST.build(payments=[{"date": "2023-05-01",
                                     "amount": 8000.0}], **kw)
        none = INST.build(payments=[], **kw)
        self.assertEqual(late["credit_interest"], 0.0)
        self.assertEqual(late["charge_interest"], none["charge_interest"])
        self.assertEqual(late["paid_total"], 8000.0)


class TestViewsWithoutGainsExit2(unittest.TestCase):
    """TJ-0644: with no gains files in work/, every view stops with exit
    2 (a named input that is missing), as sanity and wash-sales do."""

    def test_exit_2(self):
        from _qa_project import tj, toml
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(toml(year=2025))
            (root / "work").mkdir()
            for args in (("sum",), ("estimate",),
                         ("plot-tax", "--province", "ON"), ("shares",),
                         ("list",), ("leaps",), ("leaps-sum",),
                         ("ccd-sum",), ("winners",), ("gains",)):
                r = tj(root, *args, check=False)
                self.assertEqual(r.returncode, 2,
                                 (args, r.stdout[-500:], r.stderr[-800:]))
                self.assertIn("no ", r.stderr, args)
                self.assertIn("gains", r.stderr, args)


class TestCarryoverDamagedBaseNamedOnce(unittest.TestCase):
    """TJ-0645: carryover's capital-gains-dividends reader names a
    damaged work/<account>_base.json once, from the project, with exit
    2, as the estimate's twin does (TJ-0641)."""

    def test_named_once_exit_2(self):
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            from _qa_project import toml
            root = Path(td)
            (root / "taxjson.toml").write_text(toml(year=2025))
            (root / "work").mkdir()
            base = root / "work" / "margin_base.json"
            base.write_text("{")
            err = io.StringIO()
            with mock.patch.object(R, "_box18_fractions",
                                   return_value={("margin", "x1"): 1.0}), \
                    contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                R._box18_by_year(root, ["margin"])
        self.assertEqual(cm.exception.code, 2)
        text = " ".join(err.getvalue().split())
        self.assertIn("could not read work/margin_base.json: not valid "
                      "JSON", text)
        self.assertEqual(text.count("margin_base.json"), 1, text)
        self.assertNotIn(str(root), text)
        self.assertIn("[[capital_gains_dividends]]", text)


if __name__ == "__main__":
    unittest.main()
