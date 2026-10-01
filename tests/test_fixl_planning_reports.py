"""Planning low round: report and diagnostic tools.

  R1-211 / S051-01 / S031-04  sum-gains, `sum`, fees-sum and divs-sum
          labels: the non-option column is not 'STOCK', crypto staking
          is not a dividend
  S051-00 the .sum bottom line is GAIN + DIV + PIL, labelled so
  S051-02 sum-gains FILE keeps wash_solver_iterations (and routed rows)
  R1-313  sum-gains TOTAL row pinned; summary.total_gain leaves income out
  S028-07 ccd-sum CLOSES/QTY do not count grant-timing write records
  R1-239 / S028-08 / S028-11  ccd-gains / leaps-gains per-unit columns,
          break-even legacy rows, per-currency totals
  S028-09 / S035-13 / S051-11 / S079-11  unreadable or wrong-shape
          inputs: a one-line refusal, never a traceback or a partial
          report at exit 0; a bare-array book is accepted
  R1-265 / S029-15 / S029-18 / G7-1  taxjson-diff and taxjson-extractors
  S035-07 / S035-08  taxjson-missing-history --account and registered
          accounts
  S051-13 / S051-14  sum-income help text; income rows with no amount
  S077-14 report.json wash total from 'disallowed_amount'

All data is synthetic.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _env(seed=None):
    env = dict(os.environ, PYTHONPATH=str(SRC), NO_COLOR="1")
    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)
    return env


def _tool(module, *args, stdin=None, seed=None, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", f"taxjson.bin.{module}", *map(str, args)],
        cwd=cwd or REPO_ROOT, capture_output=True, text=True,
        input=stdin, env=_env(seed))


def _gain(symbol, gain, cost, proceeds, *, cur="CAD", days=30, **kw):
    return dict(symbol=symbol, gain=gain, cost=cost, proceeds=proceeds,
                currency=cur, days_held=days, date="2025-03-03",
                action="BUYSELL", **kw)


def _div(symbol, amt, cur="CAD"):
    return dict(symbol=symbol, action="DIVIDEND", dividend=amt, gain=0.0,
                currency=cur, date="2025-02-01")


def _row(date_, symbol, qty, net, account="margin", action="BUYSELL",
         currency="CAD", **kw):
    r = dict(action=action, date=date_, date_settle=date_, time="10:00:00",
             symbol=symbol, quantity=qty, net_amount=net, currency=currency,
             account=account, price=abs(net / qty) if qty else 0.0)
    r.update(kw)
    return r


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)

    def write(self, name, doc):
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(doc, (bytes, str)):
            p.write_bytes(doc if isinstance(doc, bytes) else doc.encode())
        else:
            p.write_text(json.dumps(doc))
        return p


# ------------------------------------------------------------ sum-gains
class TestSumGainsTotals(_Tmp):
    BOOK = {"summary": {"year": "2025"}, "transactions": [
        _gain("XEI.TO", 500.0, 1000.0, 1500.0),
        _gain("F:CLZ5.US", 100.0, 1000.0, 1100.0),
        _gain("XEI250620C00030000.TO", 50.0, 100.0, 150.0,
              direction="LONG"),
        _div("XEI.TO", 18.0), _div("XEI.TO", 18.0), _div("XEI.TO", 18.0),
    ]}

    def test_total_row_and_labels(self):
        r = _tool("taxjson_sum_gains", self.write("g.json", self.BOOK))
        self.assertEqual(r.returncode, 0, r.stderr)
        tot = next(ln for ln in r.stdout.splitlines()
                   if ln.startswith("TOTAL "))
        # TOTAL | COST | PROCEEDS | GAIN | NON-OPT | OPT | DIV | PIL | DAYS
        self.assertEqual(tot.split()[1:9],
                         ["704.00", "2,100.00", "2,750.00", "650.00",
                          "600.00", "50.00", "54.00", "0.00"])
        out = r.stdout
        # R1-211 / S051-01: the future is in the non-option figure, and
        # the label no longer says STOCK.
        self.assertRegex(out, r"TOTAL REALIZED NON-OPTION GAIN:\s+600\.00 CAD")
        self.assertIn("futures and crypto", out)
        self.assertNotIn("REALIZED STOCK GAIN", out)
        self.assertNotIn("ASSET: Stocks", out)
        # S051-00: the bottom line says what it adds up.
        self.assertRegex(out, r"GRAND TOTAL \(GAIN\+DIV\+PIL\):\s+704\.00 CAD")
        self.assertNotIn("GRAND TOTAL REALIZED GAIN", out)
        self.assertRegex(out, r"TOTAL DIVIDENDS / STAKING:\s+54\.00 CAD")

    def test_file_path_keeps_solver_and_routed_rows(self):
        doc = dict(self.BOOK, summary={"year": "2025",
                                       "wash_solver_iterations": 2,
                                       "wash_solver_converged": True},
                   manual_reporting_required=[
                       {"symbol": "ZZZ.TO", "qty": 1, "proceeds": 5},
                       {"symbol": "YYY.TO", "qty": 1, "proceeds": 6}])
        p = self.write("g.json", doc)
        by_file = json.loads(_tool("taxjson_sum_gains", "--json", p).stdout)
        by_stdin = json.loads(_tool("taxjson_sum_gains", "--json",
                                    stdin=p.read_text()).stdout)
        for d in (by_file, by_stdin):
            self.assertEqual(d["tainted_routed"], 2)
            self.assertEqual(d["wash_solver_iterations"], 2)


class TestTotalGainLeavesIncomeOut(_Tmp):
    def test_helper_ignores_income_rows(self):
        sys.path.insert(0, str(SRC))
        from taxjson.lib.pipeline import _trade_gain_total
        rows = [{"action": "BUYSELL", "gain": 500.0},
                {"action": "DIVIDEND", "gain": 1.0},
                {"action": "DIVIDEND_IN_LIEU", "gain": 1.0}]
        self.assertEqual(_trade_gain_total(rows), 500.0)

    def test_gains_run_total(self):
        book = self.write("book.json", {"transactions": [
            _row("2025-01-06", "XEI.TO", 100, -1000.0),
            _row("2025-03-03", "XEI.TO", -100, 1500.0),
            _row("2025-02-01", "XEI.TO", 0, 18.0, action="DIVIDEND",
                 gross_amount=18.0),
            _row("2025-02-15", "XEI.TO", 0, 18.0, action="DIVIDEND",
                 gross_amount=18.0)]})
        r = _tool("taxjson_gains", "--country", "canada", "--year", "2025",
                  book)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["summary"]["total_gain"], 500.0)


# ------------------------------------------------------------ ccd-sum
class TestCcdSumGrantRecords(_Tmp):
    def test_grant_write_is_not_a_close(self):
        root = self.tmp
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        sym = "XYZ250620C00030000.TO"
        rows = [
            # grant timing: the premium recognised at the write ...
            dict(symbol=sym, date="2025-02-03", date_settle="2025-02-04",
                 gain=200.0, cost=-200.0, proceeds=0.0, qty=1,
                 direction="SHORT", grant=True, currency="CAD",
                 account="margin"),
            # ... and the buy-back that closes it.
            dict(symbol=sym, date="2025-03-03", date_settle="2025-03-04",
                 gain=-50.0, cost=0.0, proceeds=-50.0, qty=1,
                 direction="SHORT", currency="CAD", account="margin")]
        self.write("work/margin_gains.json",
                   {"summary": {"year": "2025"}, "transactions": rows})
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "ccd-sum", "--json"], capture_output=True,
            text=True, env=_env(), cwd=REPO_ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(r.stdout)["rows"][0]
        self.assertEqual(rec["contracts"], 1)
        self.assertEqual(rec["qty"], 1.0)
        self.assertAlmostEqual(rec["gain"], 150.0)


# ------------------------------------------------------- ccd/leaps gains
class TestOptionGainsReports(_Tmp):
    def test_per_unit_columns_are_derived(self):
        doc = {"transactions": [
            dict(symbol="AAPL260116C00200000.US", direction="SHORT",
                 qty=1.0, cost=-100.0, proceeds=-300.0, gain=200.0,
                 currency="USD", date="2025-03-03", days_held=10),
            dict(symbol="AAPL260116C00150000.US", direction="LONG",
                 qty=2.0, cost=1000.0, proceeds=1600.0, gain=600.0,
                 currency="USD", date="2025-03-03", days_held=10)]}
        p = self.write("g.json", doc)
        ccd = _tool("taxjson_ccd_gains", p).stdout
        lg = _tool("taxjson_leaps_gains", p).stdout
        self.assertIn("COST/QTY", ccd)
        self.assertRegex(ccd, r"-100\.0000\s+-300\.0000\s+200\.0000")
        self.assertRegex(lg, r"500\.0000\s+800\.0000\s+300\.0000")
        self.assertIn("(amounts in USD)", ccd)

    def test_grant_row_has_no_negative_zero(self):
        doc = {"transactions": [
            dict(symbol="AAPL260116C00200000.US", direction="SHORT",
                 qty=1.0, cost=-428.5, proceeds=-0.0, gain=428.5,
                 grant=True, currency="CAD", date="2025-03-03")]}
        out = _tool("taxjson_ccd_gains", self.write("g.json", doc)).stdout
        self.assertNotIn("-0.00", out)

    def test_break_even_legacy_row_has_one_direction(self):
        doc = {"transactions": [
            dict(symbol="XYZ260116C00050000.US", qty=1.0, cost=250.0,
                 proceeds=250.0, gain=0.0, currency="USD",
                 date="2025-03-03")]}
        p = self.write("g.json", doc)
        self.assertNotIn("XYZ.US", _tool("taxjson_ccd_gains", p).stdout)
        self.assertIn("XYZ.US", _tool("taxjson_leaps_gains", p).stdout)

    def test_mixed_currencies_are_totalled_apart(self):
        doc = {"transactions": [
            dict(symbol="AAA260116C00010000.US", direction="SHORT", qty=1,
                 cost=-100.0, proceeds=0.0, gain=100.0, currency="USD",
                 date="2025-03-03"),
            dict(symbol="BBB260116C00010000.TO", direction="SHORT", qty=1,
                 cost=-100.0, proceeds=0.0, gain=100.0, currency="CAD",
                 date="2025-03-03")]}
        out = _tool("taxjson_ccd_gains", self.write("g.json", doc)).stdout
        self.assertNotRegex(out, r"(?m)^TOTAL\s+200\.00$")
        self.assertRegex(out, r"(?m)^TOTAL\s+CAD\s+100\.00$")
        self.assertRegex(out, r"(?m)^TOTAL\s+USD\s+100\.00$")


# -------------------------------------------- input contract (many tools)
class TestToolInputContract(_Tmp):
    """Each tool refuses an unreadable / non-UTF-8 / wrong-shape input in
    one line (no traceback); a bare-array book is accepted."""

    def _cases(self):
        bad = {"trunc": self.write("trunc.json", '{"transactions": ['),
               "latin1": self.write("latin1.json", b'{"a": "caf\xe9"}'),
               "int": self.write("int.json", "5")}
        good_map = self.write("dist.map", "")
        slip = self.write("slip.csv", "symbol,quantity,proceeds\n")

        def argv(module, f):
            return {
                "taxjson_lint_crosslistings": ["--taxable", f],
                "taxjson_fees": [f],
                "taxjson_apply_distributions": [f, "--map", good_map],
                "taxjson_form_export": ["--form", "schedule3",
                                        "--country", "canada", f],
                "taxjson_split_gains": [f, "--account", "margin"],
                "taxjson_sum_gains": [f],
                "taxjson_sum_income": [f],
                "taxjson_leaps_gains": [f],
                "taxjson_ccd_gains": [f],
                "taxjson_reconcile_slips": [slip, "--gains", f],
                "taxjson_explain": ["--country", "canada", f],
                "taxjson_ticker_map": [f],
                "taxjson_carryover": ["--country", "canada", f],
                "taxjson_diff": [f, f],
            }[module]
        return bad, argv

    MODULES = ("taxjson_lint_crosslistings", "taxjson_fees",
               "taxjson_apply_distributions", "taxjson_form_export",
               "taxjson_split_gains", "taxjson_sum_gains",
               "taxjson_sum_income", "taxjson_leaps_gains",
               "taxjson_ccd_gains", "taxjson_reconcile_slips",
               "taxjson_explain", "taxjson_ticker_map", "taxjson_carryover",
               "taxjson_diff")

    def test_bad_inputs_are_one_line_refusals(self):
        bad, argv = self._cases()
        for mod in self.MODULES:
            for kind, f in bad.items():
                with self.subTest(mod=mod, kind=kind):
                    r = _tool(mod, *argv(mod, f))
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertNotEqual(r.returncode, 0, r.stdout[-500:])
                    self.assertIn(f.name, r.stderr)

    def test_wrong_shape_rows_never_traceback(self):
        _, argv = self._cases()
        f = self.write("shape.json", '[{"date": 5}]')
        for mod in self.MODULES:
            with self.subTest(mod=mod):
                r = _tool(mod, *argv(mod, f))
                self.assertNotIn("Traceback", r.stderr)

    def test_bare_array_book_is_accepted(self):
        book = self.write("bare.json", [
            _row("2025-01-06", "ENB.TO", 10, -500.0),
            _row("2025-03-03", "ENB.TO", -10, 600.0, commission=9.99)])
        for mod, args in (("taxjson_lint_crosslistings",
                           ["--taxable", book]),
                          ("taxjson_sum_gains", [book]),
                          ("taxjson_ccd_gains", [book]),
                          ("taxjson_leaps_gains", [book])):
            with self.subTest(mod=mod):
                r = _tool(mod, *args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_fees_sum_unreadable_cache_file_is_not_skipped(self):
        cache = self.tmp / "work"
        self.write("work/margin_qt.json", {
            "metadata": {"source_brokerage": "questrade"},
            "transactions": [_row("2025-03-03", "ENB.TO", 10, -500.0,
                                  commission=9.95)]})
        self.write("work/margin_ib.json", '{"metadata": {"source_')
        r = _tool("taxjson_fees", "--cache", cache)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("margin_ib.json", r.stderr)
        self.assertNotIn("TOTAL", r.stdout)

    def test_fees_sum_labels(self):
        self.write("work/c.json", {
            "metadata": {"source_brokerage": "questrade"},
            "transactions": [_row("2025-03-03", "ENB.TO", 10, -500.0,
                                  commission=9.95)]})
        r = _tool("taxjson_fees", "--cache", self.tmp / "work")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("non-option", r.stdout)
        self.assertNotIn(" stocks ", r.stdout)
        self.assertIn("$/UNIT", r.stdout)


# ------------------------------------------------------- sum-income
class TestSumIncome(_Tmp):
    def test_missing_amount_is_refused(self):
        rows = [dict(action="DIVIDEND", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", account="margin"),
                dict(action="TAX", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", account="margin", net_amount=None)]
        r = _tool("taxjson_sum_income", self.write("b.json",
                                                   {"transactions": rows}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no amount", r.stderr)
        self.assertNotIn("TOTAL NET INCOME", r.stdout)

    def test_amounts_present_still_sum(self):
        rows = [dict(action="DIVIDEND", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", gross_amount=37.5, net_amount=31.87),
                dict(action="TAX", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", net_amount=5.63)]
        r = _tool("taxjson_sum_income", self.write("b.json",
                                                   {"transactions": rows}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"TOTAL NET INCOME\s+31\.87")

    def test_help_names_the_real_input(self):
        r = _tool("taxjson_sum_income", "--help")
        self.assertNotIn("taxjson_income.py", r.stdout)
        self.assertIn("base", r.stdout)


# --------------------------------------------------------- taxjson-diff
class TestDiff(_Tmp):
    def test_unknown_by_field_is_refused(self):
        a = self.write("a.json", [_row("2025-01-06", "AAA.TO", 1, -1.0)])
        r = _tool("taxjson_diff", "--by", "symbl", a, a)
        self.assertEqual(r.returncode, 2)
        self.assertIn("symbl", r.stderr)

    def test_reordered_same_key_rows_are_unchanged(self):
        rows = [_row("2025-01-22", "CASH", 0, -17.73, action="INTEREST"),
                _row("2025-01-22", "CASH", 0, -81.29, action="INTEREST")]
        a = self.write("a.json", rows)
        b = self.write("b.json", list(reversed(rows)))
        r = _tool("taxjson_diff", "--summary", a, b)
        self.assertIn("0 added | 0 removed | 0 modified | 2 unchanged",
                      r.stdout)

    def test_sub_micro_quantity_change_is_seen(self):
        a = self.write("a.json", [_row("2025-01-06", "BTC", 9.4e-7, 0.0,
                                       action="BUYSELL")])
        b = self.write("b.json", [_row("2025-01-06", "BTC", 5.6e-7, 0.0,
                                       action="BUYSELL")])
        r = _tool("taxjson_diff", "--summary", a, b)
        self.assertNotIn("0 added | 0 removed | 0 modified | 1 unchanged",
                         r.stdout)

    def test_output_does_not_depend_on_hash_seed(self):
        old = [_row("2025-01-06", s, 10, -100.0) for s in
               ("CCC", "DDD", "EEE", "FFF", "AAA", "BBB")]
        old += [_row("2025-02-03", "ZZZ.TO", q, -q * 1.0) for q in
                range(1, 6)]
        new = old[:6] + [_row("2025-02-03", "ZZZ.TO", q, -q * 1.0)
                         for q in range(3, 8)]
        a, b = self.write("a.json", old), self.write("b.json", new)
        outs = {_tool("taxjson_diff", "--show-unchanged", "--limit", "2",
                      a, b, seed=s).stdout for s in range(5)}
        self.assertEqual(len(outs), 1)

    def test_extractors_parses_argv(self):
        r = _tool("taxjson_extractors", "--bogus-flag")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(_tool("taxjson_extractors").returncode, 0)


# ------------------------------------------------- missing-history
class TestMissingHistory(_Tmp):
    def _books(self):
        (self.tmp / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.kids]\ntype = "sheltered"\n')
        m = self.write("work/margin_base.json", {"transactions": [
            _row("2025-01-06", "BBB.TO", 10, -100.0),
            _row("2025-04-01", "BBB.TO", -30, 300.0)]})
        k = self.write("work/kids_base.json", {"transactions": [
            _row("2025-04-01", "CCC.TO", -10, 100.0, account="kids")]})
        return m, k

    def test_unknown_account_is_refused(self):
        m, _ = self._books()
        r = _tool("taxjson_missing_history", "--account", "margn", m)
        self.assertEqual(r.returncode, 2)
        self.assertIn("margin", r.stderr)
        self.assertNotIn("No missing-cost-basis issues", r.stdout)

    def test_sheltered_rows_do_not_affect_the_year(self):
        from taxjson.lib import checklist as cl
        m, k = self._books()
        r = _tool("taxjson_missing_history", "--year", "2025", m, k)
        self.assertEqual(r.returncode, 0, r.stderr)
        aff = r.stdout.split("AFFECTS 2025")[1].split("SHELTERED")[0]
        self.assertIn("BBB.TO", aff)
        self.assertNotIn("CCC.TO", aff)
        self.assertRegex(r.stdout, r"SHELTERED 2025 .*superficial-loss")
        shel = r.stdout.split("SHELTERED 2025")[1]
        self.assertIn("CCC.TO", shel)
        ctx = cl.Ctx(root=self.tmp, cfg={"settings": {"year": 2025}},
                     year=2025, today=date(2026, 9, 29),
                     run_sub=lambda argv, timeout=0: (0, r.stdout, ""))
        res = cl.d_missing_history(ctx)
        self.assertIn("1 position(s)", res.detail)
        self.assertNotIn("CCC.TO", res.detail)


# ------------------------------------------------------- report.json
class TestReportJsonWashTotal(unittest.TestCase):
    def test_disallowed_amount_is_read(self):
        sys.path.insert(0, str(SRC))
        from taxjson.lib.report_model import build_account_report
        # The US engine's wash record carries only disallowed_amount.
        rep = build_account_report(
            {"summary": {"year": "2025"}, "transactions": [],
             "wash_sales": [{"loss_tx_id": "x", "date": "2025-03-03",
                             "disallowed_amount": 1000.0}]}, "margin")
        self.assertEqual(rep["wash"], {"count": 1, "total_disallowed": 1000.0})


if __name__ == "__main__":
    unittest.main()
