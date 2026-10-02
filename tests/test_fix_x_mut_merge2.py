"""Mutation pins for bin/taxjson_merge2.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round. merge2 runs in-process here (sys.argv +
captured stdout/stderr) so a pin costs milliseconds.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import taxjson.bin.taxjson_merge2 as M
from taxjson.lib.core import TaxTransaction
from tax_rules import rule


def _row(**kw):
    r = {"action": "BUYSELL", "date": "2025-03-03", "time": "10:00:00",
         "symbol": "XYZ", "quantity": 10.0, "price": 5.0,
         "net_amount": 50.0, "currency": "USD", "account": "margin"}
    r.update(kw)
    return r


def _merge(tmp, *files_rows, args=(), raw=None):
    """Run merge2 on JSON files (one per rows list; `raw` = {name: text}
    adds files verbatim). Returns (exit code, output doc or None,
    stderr)."""
    paths = []
    for i, rows in enumerate(files_rows):
        p = Path(tmp) / f"in{i}.json"
        p.write_text(json.dumps({"transactions": rows}), encoding="utf-8")
        paths.append(str(p))
    for name, text in (raw or {}).items():
        p = Path(tmp) / name
        p.write_text(text, encoding="utf-8")
        paths.append(str(p))
    out, err = io.StringIO(), io.StringIO()
    saved = sys.argv
    sys.argv = ["taxjson-merge2", *args, *paths]
    code = 0
    try:
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                code = M.main() or 0
            except SystemExit as e:
                code = e.code
    finally:
        sys.argv = saved
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None), err.getvalue()


def _div(**kw):
    r = _row(action="DIVIDEND", quantity=100.0, price=0.0,
             net_amount=100.0, gross_amount=100.0,
             description="XYZ(US0000000001) Cash Dividend USD 1.00 per "
                         "Share (Ordinary Dividend)")
    r.update(kw)
    return r


def _tax(**kw):
    r = _row(action="TAX", quantity=0.0, price=0.0, net_amount=15.0,
             description="XYZ(US0000000001) Cash Dividend USD 1.00 per "
                         "Share - US Tax")
    r.update(kw)
    return r


def _tx(d):
    return TaxTransaction(**d)


class TestReconcileDividendTax(unittest.TestCase):
    """reconcile_dividend_tax: the dividend's net is gross minus its
    matched withholding; the TAX row gets the share count and a
    per-share rate (CA-INC-05 / US-INC-03: dividends gross, withholding
    its own TAX row)."""

    @rule("CA-INC-05")
    def test_tax_row_by_action_or_type(self):
        # m812: a TAX row with no `type` still pairs (action == TAX).
        d, t = _tx(_div()), _tx(_tax())
        M.reconcile_dividend_tax([d, t])
        self.assertEqual(d.net_amount, 85.0)
        self.assertEqual((t.quantity, t.price), (100.0, 0.15))

    def test_withholding_typed_row_pairs_whatever_its_action(self):
        # m812: a row typed "tax" is withholding even under another
        # action label.
        from types import SimpleNamespace as NS
        d = _tx(_div())
        t = NS(**dict(_tax(), action="WITHHOLDING", type="Tax"))
        M.reconcile_dividend_tax([d, t])
        self.assertEqual(d.net_amount, 85.0)
        self.assertEqual((t.quantity, t.price), (100.0, 0.15))

    def test_zero_gross_row_in_a_split_payment_keeps_zero(self):
        # m771/m790: a 0-gross row in the group takes no share.
        d1 = _tx(_div(gross_amount=0.0, net_amount=0.0, quantity=0.0))
        d2 = _tx(_div())
        t = _tx(_tax())
        M.reconcile_dividend_tax([d1, d2, t])
        self.assertEqual((d1.net_amount, d2.net_amount), (0.0, 85.0))

    def test_only_tax_rows_pair_with_a_dividend(self):
        # m768: a same-day trade of the symbol (no description) is not
        # withholding for a same-day dividend without one.
        d = _tx(_div(description=""))
        trade = _tx(_row(description=""))
        M.reconcile_dividend_tax([d, trade])
        self.assertEqual(d.net_amount, 100.0)
        self.assertEqual((trade.quantity, trade.price), (10.0, 5.0))

    @rule("CA-INC-05")
    @rule("US-INC-03")
    def test_split_payment_apportions_pro_rata(self):
        # m791 (x -> /), m773 / m794 (8-decimal rounding): 15 withheld
        # on 10 + 20 gross -> nets 5 and 10... with 1.0 withheld on
        # 10/20: 10 - 1/3 = 9.66666667, 20 - 2/3 = 19.33333333; three
        # shares -> 0.33333333 per share.
        d1 = _tx(_div(quantity=1.0, gross_amount=10.0, net_amount=10.0))
        d2 = _tx(_div(quantity=2.0, gross_amount=20.0, net_amount=20.0))
        t = _tx(_tax(net_amount=1.0))
        M.reconcile_dividend_tax([d1, d2, t])
        self.assertEqual(d1.net_amount, 9.66666667)
        self.assertEqual(d2.net_amount, 19.33333333)
        self.assertEqual((t.quantity, t.price), (3.0, 0.33333333))

    def test_zero_withholding_row_withholds_nothing(self):
        # m788/m802, m813/m818: a TAX row of 0 adds no withholding and a
        # 0 per-share rate (not 1.00).
        d, t = _tx(_div()), _tx(_tax(net_amount=0.0))
        M.reconcile_dividend_tax([d, t])
        self.assertEqual(d.net_amount, 100.0)
        self.assertEqual(t.price, 0.0)

    def test_zero_gross_dividend_is_left_alone(self):
        # m733 (<= -> <: 0/0), m789/m803: nothing to apportion.
        d = _tx(_div(gross_amount=0.0, net_amount=0.0))
        t = _tx(_tax())
        M.reconcile_dividend_tax([d, t])
        self.assertEqual(d.net_amount, 0.0)
        self.assertEqual((t.quantity, t.price), (0.0, 0.0))

    def test_small_dividend_on_a_fractional_holding(self):
        # m753 (> 1 gross), m755 (> 1 shares): 0.80 on half a share,
        # 0.12 withheld.
        d = _tx(_div(quantity=0.5, gross_amount=0.8, net_amount=0.8))
        t = _tx(_tax(net_amount=0.12))
        M.reconcile_dividend_tax([d, t])
        self.assertEqual(d.net_amount, 0.68)
        self.assertEqual((t.quantity, t.price), (0.5, 0.24))

    def test_unknown_share_count_leaves_the_tax_row(self):
        # m735 (>= 0: 0/0), m792/m805: no share count -> no per-share
        # rate.
        d = _tx(_div(quantity=0.0))
        t = _tx(_tax())
        M.reconcile_dividend_tax([d, t])
        self.assertEqual(d.net_amount, 85.0)
        self.assertEqual((t.quantity, t.price), (0.0, 0.0))


class TestDuplicateSplits(unittest.TestCase):
    def _warn(self, rows):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = M.warn_duplicate_splits([_tx(r) for r in rows])
        return n, err.getvalue()

    def test_single_split_and_duplicate_trades_are_not_warned(self):
        # m727 (a one-row group), m725 (non-SPLIT rows are not grouped).
        n, err = self._warn([_row(action="SPLIT", quantity=2.0,
                                  price=0.0, net_amount=0.0),
                             _row(), _row()])
        self.assertEqual((n, err), (0, ""))

    def test_conflicting_ratios_print_the_product(self):
        # m750 (prod starts at 1), m811 (a 0 ratio is 0, not 1).
        split = dict(action="SPLIT", price=0.0, net_amount=0.0)
        n, err = self._warn([_row(quantity=2.0, **split),
                             _row(quantity=3.0, **split)])
        self.assertEqual(n, 1)
        self.assertIn("ratios [2.0, 3.0] — EACH is applied (x6 in total)",
                      err)
        n, err = self._warn([_row(quantity=0.0, **split),
                             _row(quantity=3.0, **split)])
        self.assertIn("ratios [0.0, 3.0]", err)


class TestInputErrors(unittest.TestCase):
    def test_unreadable_json_is_a_clean_exit_2(self):
        # m756: the file is skipped (no traceback), the run refuses.
        with tempfile.TemporaryDirectory() as tmp:
            code, doc, err = _merge(tmp, raw={"bad.json": "{not json"})
            self.assertEqual(code, 2)   # unreadable input: exit 2 (A2-0164)
            self.assertIsNone(doc)
            self.assertIn("error: reading", err)
            self.assertIn("could not be read; refusing", err)
            self.assertNotIn("Traceback", err)

    def test_no_transactions_list_is_a_clean_exit_2(self):
        # m740.
        with tempfile.TemporaryDirectory() as tmp:
            code, doc, err = _merge(
                tmp, raw={"x.json": json.dumps({"transactions": 5})})
            self.assertEqual(code, 2)   # unreadable input: exit 2 (A2-0164)
            self.assertIn("has no 'transactions' list", err)

    def test_missing_required_input_exits_2(self):
        # m758: --require-inputs.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ok.json"
            p.write_text(json.dumps({"transactions": [_row()]}))
            out, err = io.StringIO(), io.StringIO()
            saved = sys.argv
            sys.argv = ["taxjson-merge2", "--require-inputs", str(p),
                        str(Path(tmp) / "missing.json")]
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as cm:
                        M.main()
            finally:
                sys.argv = saved
            self.assertEqual(cm.exception.code, 2)   # A2-0164
            self.assertIn("refusing to emit a partial merge", err.getvalue())

    def test_bad_row_exits_2(self):
        # m759.
        with tempfile.TemporaryDirectory() as tmp:
            code, _doc, err = _merge(tmp, [_row(quantity="lots")])
            self.assertEqual(code, 2)   # unreadable input: exit 2 (A2-0164)

    def test_all_inputs_missing_exits_2(self):
        # m760.
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            saved = sys.argv
            sys.argv = ["taxjson-merge2", str(Path(tmp) / "a.json")]
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as cm:
                        M.main()
            finally:
                sys.argv = saved
            self.assertEqual(cm.exception.code, 2)   # A2-0164
            self.assertIn("none of the input files could be read",
                          err.getvalue())

    def test_bad_rates_file_is_a_clean_exit_1(self):
        # m745 / m781: a rates file the loader refuses.
        with tempfile.TemporaryDirectory() as tmp:
            rates = Path(tmp) / "rates.csv"
            rates.write_text("2025-03-03 12:00:00 USD CAD NaN\n")
            code, doc, err = _merge(tmp, [_row()],
                                    args=("--to", "CAD", "--rates",
                                          str(rates)))
            self.assertEqual(code, 1)
            self.assertIsNone(doc)
            self.assertIn("error:", err)

    def test_futures_without_country_is_a_clean_exit_1(self):
        # m746 / m782: the converter's ValueError (futures need the
        # country's lot rule).
        with tempfile.TemporaryDirectory() as tmp:
            rates = Path(tmp) / "rates.csv"
            rates.write_text("2025-03-03 12:00:00 USD CAD 1.40\n")
            fut = _row(symbol="F:ESH5", quantity=1.0, price=5000.0,
                       net_amount=0.0)
            code, doc, err = _merge(tmp, [fut],
                                    args=("--to", "CAD", "--rates",
                                          str(rates)))
            self.assertEqual(code, 1, err)
            self.assertIn("pass --country", err)


    def test_currency_missing_from_the_rates_file_exits_1(self):
        # m761: a currency the --rates file never mentions is fatal
        # without an explicit --default-rate.
        with tempfile.TemporaryDirectory() as tmp:
            rates = Path(tmp) / "rates.csv"
            rates.write_text("2025-03-03 12:00:00 USD CAD 1.40\n")
            code, doc, err = _merge(tmp, [_row(currency="EUR")],
                                    args=("--to", "CAD", "--rates",
                                          str(rates)))
            self.assertEqual(code, 1, err)
            self.assertIsNone(doc)


class TestDedupAndStages(unittest.TestCase):
    def test_dedup_alone_sorts_and_lists_each_dropped_row(self):
        # m719 (--dedup implies --sort), m775-m779 / m796-m809 (the
        # dropped-row line shows time, qty, price, net and id),
        # m816 (stage list).
        with tempfile.TemporaryDirectory() as tmp:
            late = _row(id="T2", date="2025-03-05", quantity=3.0,
                        price=7.0, net_amount=21.0)
            early = _row(id="T1", date="2025-03-04", time="09:31:02",
                         quantity=2.0, price=1.5, net_amount=3.0)
            code, doc, err = _merge(tmp, [late, early], [dict(early)],
                                    args=("--dedup",))
            self.assertEqual(code, 0, err)
            self.assertEqual([t["id"] for t in doc["transactions"]],
                             ["T1", "T2"])
            self.assertIn("removed 1 duplicate row(s)", err)
            line = next(ln for ln in err.splitlines() if "id=T1" in ln)
            self.assertIn("2025-03-04 09:31:02", line)
            self.assertIn("qty=       2.0", line)
            self.assertIn("price=       1.5", line)
            self.assertIn("net=         3.0", line)
            self.assertEqual(doc["metadata"]["stages"], ["sort", "dedup"])

    def test_zero_fields_print_as_zero(self):
        # m796-m809: `or 0` defaults print 0, not 1.
        with tempfile.TemporaryDirectory() as tmp:
            z = _row(id="Z1", quantity=0.0, price=0.0, net_amount=0.0)
            code, _doc, err = _merge(tmp, [z], [dict(z)],
                                     args=("--dedup",))
            line = next(ln for ln in err.splitlines() if "id=Z1" in ln)
            self.assertIn("qty=         0", line)
            self.assertIn("price=         0", line)
            self.assertIn("net=           0", line)

    def test_validate_strict_is_a_validate_stage(self):
        # m817.
        with tempfile.TemporaryDirectory() as tmp:
            code, doc, err = _merge(tmp, [_row()],
                                    args=("--validate-strict",))
            self.assertEqual(code, 0, err)
            self.assertEqual(doc["metadata"]["stages"], ["validate"])

    def test_validate_strict_error_exits_1(self):
        # m763.
        with tempfile.TemporaryDirectory() as tmp:
            bad = _row()
            del bad["currency"]
            code, doc, err = _merge(tmp, [bad],
                                    args=("--validate-strict",))
            self.assertEqual(code, 1)
            self.assertIsNone(doc)


class TestCancellationWarning(unittest.TestCase):
    def test_unmatched_cancellation_prints_the_cancelled_quantity(self):
        # m795: the cancelled fill was a BUY of 5 -> "a trade of 5".
        ca = _tx(_row(quantity=-5.0, type="trade_cancel"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            kept = M.cancel_trade_pairs([ca])
        self.assertEqual(len(kept), 1)
        self.assertIn("cancelled (Ca) a trade of 5 @ 5 on 2025-03-03",
                      err.getvalue())


class TestResidualCurrency(unittest.TestCase):
    """Rows the converter leaves native are counted, five listed
    (m785/m800 `<= 5`, m810/m815 the "(+N more)" tail) and fail the
    stage under --validate (m761 exit code)."""

    def _run(self, n, *extra):
        rows = [_row(id=f"R{i}", symbol=f"S{i}") for i in range(n)]

        def convert(txs, *a, **k):
            return list(txs)            # every row stays USD
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(M, "convert_transactions", convert):
            rates = Path(tmp) / "rates.csv"
            rates.write_text("2025-03-03 12:00:00 USD CAD 1.40\n")
            return _merge(tmp, rows, args=("--to", "CAD", "--rates",
                                           str(rates), *extra))

    def test_five_rows_listed_without_a_tail(self):
        code, doc, err = self._run(5)
        self.assertEqual(code, 0)
        self.assertIn("warning: 5 row(s) still carry a non-CAD currency "
                      "after conversion: S0 2025-03-03 (USD)", err)
        self.assertNotIn("more)", err)

    def test_six_rows_list_five_and_count_the_rest(self):
        code, doc, err = self._run(6)
        self.assertIn("S4 2025-03-03 (USD) (+1 more)", err)
        self.assertNotIn("S5 2025", err)

    def test_validate_fails_the_stage(self):
        code, doc, err = self._run(1, "--validate")
        self.assertEqual(code, 1)
        self.assertIsNone(doc)
        self.assertIn("error: 1 row(s) still carry", err)


if __name__ == "__main__":
    unittest.main()
