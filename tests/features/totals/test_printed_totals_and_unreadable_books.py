"""Re-audit-2 tests-pins (pins3, group g6b): fixes in taxjson_run.py's
reports, guards, wording gates and settings that held but that no test
failed on when reverted. Each TestCase names the A2 finding and the code
it pins; every test was checked to fail with that code reverted.

All data is synthetic (invented tickers, round amounts).

Formerly: test_fix_a2_pins3_g6b
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO = _paths.REPO
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
    _cgd(root, ("ZZS.TO", ""), ("ZZT.TO", ""))
    return root


def _cgd(root, *entries):
    """[[capital_gains_dividends]] entries (symbol, account) for 2025,
    amount "all", appended to taxjson.toml."""
    with (root / "taxjson.toml").open("a") as f:
        for sym, acct in entries:
            f.write(f'\n[[capital_gains_dividends]]\nsymbol = "{sym}"\n'
                    f'year = 2025\namount = "all"\n'
                    + (f'account = "{acct}"\n' if acct else ""))


class TestPrintedReportTotals(unittest.TestCase):
    """A2-0934, A2-1586 (S040-22 class): each printed TOTAL line over two
    or more rows, so 'total = last row' fails — trades TOTAL BUY, dil-sum,
    roc-sum, divs-sum TOTAL CAPITAL-GAINS DIVIDENDS (CA-INC-06), ccd-sum,
    leaps-sum, leaps, gains (native) — and `list`'s book cost with a
    short (signed, not abs). A2-1587 (S026-22): `list` COST/SH divides a
    futures option by its declared multiplier.

    Formerly: test_fix_a2_pins3_g6b.TestPrintedReportTotals"""

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

    @rule(R.CA_INC_06)
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
    made each answer from the readable part with exit 0.

    Formerly: test_fix_a2_pins3_g6b.TestUnreadableBooksStopTheView"""

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
        from taxjson.cli import common as cli_common
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            _doc(root / "work" / "margin_base.json", [])
            (root / "work" / "cash_base.json").write_text('{"transac')
            err = io.StringIO()
            with self.assertRaises(SystemExit) as cm, \
                    contextlib.redirect_stderr(err):
                cli_common._dist_adjust_rows(root / "work", ["margin", "cash"],
                                    lambda _d: True)
        # (exit 2, an input that cannot be read, its Error printed
        # first: backlog TJ-0596)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("work/cash_base.json", err.getvalue())

    @rule(R.CA_INC_06)
    def test_box18_fractions_stops_on_a_truncated_native_book(self):
        from taxjson.cli import common as cli_common
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            _doc(root / "work" / "margin_raw.json", [
                _t("DIVIDEND", "2025-03-10", "ZZS.TO", net=60.0,
                   gross_amount=60.0, id="d1")])
            (root / "work" / "cash_raw.json").write_text('{"transac')
            _cgd(root, ("ZZS.TO", "margin"))
            err = io.StringIO()
            with self.assertRaises(SystemExit) as cm, \
                    contextlib.redirect_stderr(err):
                cli_common._box18_fractions(root)
        # (exit 2, an input that cannot be read: backlog TJ-0596)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("work/cash_raw.json", err.getvalue())

    def test_option_boundary_stops_on_a_truncated_base_book(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            _doc(root / "work" / "margin_base.json", [])
            (root / "work" / "cash_base.json").write_text('{"transac')
            r = _run(root, "option-boundary")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("cash_base.json", r.stderr)
        self.assertNotIn("OPTION YEAR-BOUNDARY REVIEW", r.stdout)


if __name__ == "__main__":
    unittest.main()
