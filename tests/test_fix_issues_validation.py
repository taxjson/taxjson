"""GitHub issues #14, #16 and #17: required fields are enforced at one
shared boundary, not per command.

#14 taxjson-explain booked a purchase that came WITHOUT its net_amount at
    cost 0 (gain = the whole sale) while taxjson-gains refused it; run_gains
    had no guard either. The gains computation's entry (the engines'
    compute_gains, and pipeline.prepare_books in front of it) now refuses
    it for the main and the context books, from a file or stdin. An
    explicit 0 stays a legal amount.
#16 An empty date or an empty symbol on a trade row, or an action the
    engines do not book, was computed at exit 0. Refused at the same
    boundary; the symbol-less cash rows (FEE, INTEREST, TAX) still load.
#17 A gains document whose one disposition lacked its gain (or units)
    passed because a valid neighbour carried one; form-export skipped the
    broken row in silence. Every row of a gains document is now held to
    the gains-row contract by every filing and summary reader.

All data is synthetic.
"""
import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from _tmpfiles import private_dir

from taxjson.lib.cli_diag import InputContentError
from taxjson.lib.core import (CanadaTaxRules, USATaxRules,
                              coerce_transaction_row, load_transactions,
                              require_computable_rows)
from taxjson.lib.json_input import InputFileError, require_gains_doc
from taxjson.lib.pipeline import (GainsRequest, load_stdin_transactions,
                                  prepare_books, run_gains)


def _cli(module, *args, stdin=None):
    r = subprocess.run([sys.executable, "-m", module, *args],
                       capture_output=True, text=True, input=stdin,
                       stdin=None if stdin is not None
                       else subprocess.DEVNULL)
    r.stderr = " ".join(r.stderr.split())      # unwrap the console lines
    return r


def _write(doc, name="book.json"):
    p = Path(private_dir()) / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _coerce(rows, where="test"):
    return [coerce_transaction_row(r, i, where) for i, r in enumerate(rows)]


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


BUY_NO_NET = {"action": "BUYSELL", "date": "2025-01-02",
              "symbol": "SYNTH.US", "quantity": 10, "currency": "USD"}
SELL = {"action": "BUYSELL", "date": "2025-02-06", "symbol": "SYNTH.US",
        "quantity": -10, "net_amount": 120, "currency": "USD"}
BUY = dict(BUY_NO_NET, net_amount=100)
ISSUE14 = {"transactions": [BUY_NO_NET, SELL]}


class TestIssue14MissingTradeAmount(unittest.TestCase):

    def test_issue_repro_gains_and_explain_both_refuse(self):
        p = _write(ISSUE14)
        g = _cli("taxjson.bin.taxjson_gains", "--country", "usa", str(p))
        e = _cli("taxjson.bin.taxjson_explain", "--country", "usa",
                 "--list", str(p))
        for r in (g, e):
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("net_amount missing", r.stderr)
            self.assertIn("index 0", r.stderr)
        self.assertNotIn("+120", e.stdout)

    def test_explain_refuses_on_stdin(self):
        r = _cli("taxjson.bin.taxjson_explain", "--country", "usa",
                 "--list", stdin=json.dumps(ISSUE14))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("net_amount missing", r.stderr)

    def test_explain_refuses_a_context_book_row(self):
        main = _write({"transactions": [BUY, SELL]})
        ctx = _write({"transactions": [dict(BUY_NO_NET, account="IRA1")]},
                     "sheltered.json")
        r = _cli("taxjson.bin.taxjson_explain", "--country", "usa",
                 "--list", "--sheltered", str(ctx), str(main))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("sheltered.json", r.stderr)
        self.assertIn("net_amount missing", r.stderr)

    def test_run_gains_refuses_main_and_context_books(self):
        for country in ("usa", "canada"):
            req = GainsRequest(country=country)
            with self.subTest(country=country, book="main"):
                with self.assertRaisesRegex(ValueError, "net_amount"):
                    _quiet(run_gains, _coerce([BUY_NO_NET, SELL]), (), (),
                           req)
            with self.subTest(country=country, book="sheltered"):
                with self.assertRaisesRegex(ValueError, "net_amount"):
                    _quiet(run_gains, _coerce([BUY, SELL]),
                           _coerce([dict(BUY_NO_NET, account="R1")]), (),
                           req)
            with self.subTest(country=country, book="affiliated"):
                with self.assertRaisesRegex(ValueError, "net_amount"):
                    _quiet(run_gains, _coerce([BUY, SELL]), (),
                           _coerce([dict(BUY_NO_NET, account="SP1")]), req)

    def test_engines_and_prepare_books_refuse(self):
        rows = _coerce([BUY_NO_NET, SELL])
        for engine in (USATaxRules, CanadaTaxRules):
            with self.subTest(engine=engine.__name__):
                with self.assertRaisesRegex(InputContentError, "net_amount"):
                    _quiet(engine().compute_gains, rows)
        with self.assertRaisesRegex(InputContentError, "net_amount"):
            prepare_books(rows, taxable=True, phantom_hint=False)

    def test_file_and_stdin_loaders_both_mark(self):
        p = _write(ISSUE14)
        stdin = load_stdin_transactions(io.StringIO(json.dumps(ISSUE14)))
        for rows in (load_transactions(p), stdin):
            with self.assertRaisesRegex(InputContentError,
                                        "index 0.*net_amount"):
                require_computable_rows(rows)

    def test_missing_quantity_is_refused_too(self):
        rows = _coerce([{k: v for k, v in BUY.items() if k != "quantity"},
                        SELL])
        with self.assertRaisesRegex(InputContentError, "quantity missing"):
            _quiet(USATaxRules().compute_gains, rows)

    def test_explicit_zero_amount_stays_legal(self):
        doc = {"transactions": [dict(BUY_NO_NET, net_amount=0), SELL]}
        p = _write(doc)
        for mod, extra in (("taxjson.bin.taxjson_gains", ()),
                           ("taxjson.bin.taxjson_explain", ("--list",))):
            with self.subTest(tool=mod):
                r = _cli(mod, "--country", "usa", *extra, str(p))
                self.assertEqual(r.returncode, 0, r.stderr)
        res = _quiet(run_gains, _coerce(doc["transactions"]), (), (),
                     GainsRequest(country="usa"))
        self.assertEqual(res["transactions"][0]["gain"], 120.0)

    def test_a_pass_through_tool_keeps_the_amount_missing(self):
        # taxjson-sort reads the book and writes it again: the missing
        # key stays missing, so the gains run after it still refuses.
        p = _write(ISSUE14)
        s = _cli("taxjson.bin.taxjson_sort", "--no-validation", str(p))
        self.assertEqual(s.returncode, 0, s.stderr)
        rows = json.loads(s.stdout)["transactions"]
        self.assertNotIn("net_amount", rows[0])
        self.assertEqual(rows[1]["net_amount"], 120)
        g = _cli("taxjson.bin.taxjson_gains", "--country", "usa",
                 stdin=s.stdout)
        self.assertEqual(g.returncode, 2, g.stdout + g.stderr)
        self.assertIn("net_amount missing", g.stderr)


def _book(**sale):
    return {"transactions": [BUY, dict(SELL, **sale)]}


class TestIssue16EmptyRequiredFields(unittest.TestCase):

    def _gains(self, doc, country="usa"):
        return _cli("taxjson.bin.taxjson_gains", "--country", country,
                    str(_write(doc)))

    def test_empty_date_is_refused(self):
        for country in ("usa", "canada"):
            with self.subTest(country=country):
                r = self._gains(_book(date=""), country)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("index 1", r.stderr)
                self.assertIn("date is empty", r.stderr)

    def test_blank_date_is_refused(self):
        r = self._gains(_book(date="   "))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("date", r.stderr)

    def test_missing_and_null_date_are_refused_on_read(self):
        no_date = {k: v for k, v in SELL.items() if k != "date"}
        r = self._gains({"transactions": [BUY, no_date]})
        self.assertEqual(r.returncode, 2)
        self.assertIn("required field date is missing", r.stderr)
        r = self._gains(_book(date=None))
        self.assertEqual(r.returncode, 2)
        self.assertIn("required field date is null", r.stderr)

    def test_empty_symbol_is_refused(self):
        for country in ("usa", "canada"):
            with self.subTest(country=country):
                r = self._gains(_book(symbol=""), country)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("index 1", r.stderr)
                self.assertIn("symbol is empty on a BUYSELL row", r.stderr)

    def test_missing_and_null_symbol_are_refused(self):
        no_sym = {k: v for k, v in SELL.items() if k != "symbol"}
        for doc in ({"transactions": [BUY, no_sym]}, _book(symbol=None),
                    _book(symbol="  ")):
            with self.subTest(sale=doc["transactions"][1].get("symbol",
                                                              "<absent>")):
                r = self._gains(doc)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("symbol is empty", r.stderr)

    def test_empty_symbol_refused_for_every_position_action(self):
        rows = {
            "ASSIGN": dict(BUY, action="ASSIGN"),
            "SPLIT": {"action": "SPLIT", "date": "2025-01-03",
                      "quantity": 2},
            "TRANSFER": {"action": "TRANSFER", "date": "2025-01-03",
                         "quantity": 1},
            "ADJUST": {"action": "ADJUST", "date": "2025-01-03",
                       "net_amount": -5},
            "OPENING_BALANCE": {"action": "OPENING_BALANCE",
                                "date": "2025-01-01", "quantity": 1},
        }
        for action, row in rows.items():
            with self.subTest(action=action):
                txs = _coerce([BUY, dict(row, symbol="")])
                with self.assertRaisesRegex(InputContentError,
                                            "symbol is empty"):
                    require_computable_rows(txs)

    def test_symbol_less_cash_rows_still_compute(self):
        doc = {"transactions": [
            BUY,
            {"action": "FEE", "date": "2025-01-10", "net_amount": 2,
             "currency": "USD"},
            {"action": "INTEREST", "date": "2025-01-11", "net_amount": 3,
             "currency": "USD", "symbol": ""},
            {"action": "TAX", "date": "2025-01-12", "net_amount": -1,
             "currency": "USD"},
            SELL]}
        for country in ("usa", "canada"):
            with self.subTest(country=country):
                r = self._gains(doc, country)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(
                    len(json.loads(r.stdout)["transactions"]), 1)

    def test_unsupported_action_is_refused(self):
        for action in ("BUY", "buysell", ""):
            with self.subTest(action=action):
                r = self._gains(_book(action=action))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("unsupported action", r.stderr)

    def test_explain_and_engines_refuse_too(self):
        p = _write(_book(date=""))
        r = _cli("taxjson.bin.taxjson_explain", "--country", "usa",
                 "--list", str(p))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("date is empty", r.stderr)
        from taxjson.lib.core import TaxTransaction
        for bad in (TaxTransaction(action="BUYSELL", date="",
                                   symbol="SYNTH.US", quantity=-1,
                                   net_amount=5),
                    TaxTransaction(action="BUYSELL", date="2025-02-06",
                                   symbol="", quantity=-1, net_amount=5)):
            for engine in (USATaxRules, CanadaTaxRules):
                with self.subTest(engine=engine.__name__, row=bad.id):
                    with self.assertRaises(InputContentError):
                        _quiet(engine().compute_gains, [bad])


def _disp(**kw):
    r = {"date": "2025-01-02", "symbol": "SYNTH.TO", "qty": 1, "cost": 10,
         "proceeds": 12, "gain": 2, "currency": "CAD"}
    r.update(kw)
    return {k: v for k, v in r.items() if v is not ...}


ISSUE17 = {"transactions": [
    _disp(),
    _disp(date="2025-02-02", proceeds=15, gain=...)]}


class TestIssue17GainsRowContract(unittest.TestCase):

    def test_issue_repro_form_export_refuses(self):
        p = _write(ISSUE17, "gains.json")
        r = _cli("taxjson.bin.taxjson_form_export", "--country", "canada",
                 "--form", "schedule3", "--year", "2025", str(p))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('"transactions" row 1 (SYNTH.TO 2025-02-02)',
                      r.stderr)
        self.assertIn("without gain", r.stderr)
        self.assertNotIn("Line 13199", r.stdout)

    def test_each_missing_figure_is_named(self):
        for field in ("gain", "qty", "date"):
            with self.subTest(field=field):
                doc = {"transactions": [_disp(), _disp(**{field: ...})]}
                with self.assertRaisesRegex(InputFileError,
                                            f"row 1 .*without {field}"):
                    require_gains_doc(doc, "g.json")
                doc = {"transactions": [_disp(), _disp(**{field: None})]}
                with self.assertRaisesRegex(InputFileError,
                                            f"without {field}"):
                    require_gains_doc(doc, "g.json")

    def test_income_and_manual_rows_stay_legal(self):
        doc = {"transactions": [
            _disp(),
            {"action": "DIVIDEND", "date": "2025-03-01",
             "symbol": "SYNTH.TO", "dividend": 4.0, "currency": "CAD"},
            {"action": "DIVIDEND_IN_LIEU", "date": "2025-03-02",
             "symbol": "SYNTH.TO", "pil": 1.0, "currency": "CAD"},
            {"date": "2025-04-01", "symbol": "NOBUY.TO", "qty": 3,
             "proceeds": 30, "tainted": True, "currency": "CAD"}],
            "manual_reporting_required": [
                {"date": "2025-04-02", "symbol": "NOBUY.TO", "qty": 1,
                 "proceeds": 9, "currency": "CAD"}]}
        self.assertIs(require_gains_doc(doc, "g.json"), doc)
        p = _write(doc, "gains.json")
        r = _cli("taxjson.bin.taxjson_form_export", "--country", "canada",
                 "--form", "schedule3", "--year", "2025", str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MANUAL REPORTING REQUIRED — 2 sale(s)", r.stdout)

    def test_settle_date_alone_is_a_date(self):
        doc = {"transactions": [_disp(date=..., date_settle="2025-01-03")]}
        self.assertIs(require_gains_doc(doc, "g.json"), doc)
        doc = {"transactions": [_disp(date="", date_settle="")]}
        with self.assertRaisesRegex(InputFileError, "without date"):
            require_gains_doc(doc, "g.json")

    def test_a_manual_row_needs_its_units(self):
        doc = {"transactions": [_disp(), {"date": "2025-04-01",
                                          "symbol": "NOBUY.TO",
                                          "proceeds": 30, "tainted": True}]}
        with self.assertRaisesRegex(InputFileError,
                                    "unknown-cost.*without qty"):
            require_gains_doc(doc, "g.json")

    def test_stage_file_keeps_its_own_message(self):
        doc = {"transactions": [BUY, SELL]}
        with self.assertRaisesRegex(InputFileError, "pipeline stage file"):
            require_gains_doc(doc, "base.json")
        # A gains file with one stage row mixed in is refused by row.
        doc = {"transactions": [_disp(), BUY]}
        with self.assertRaisesRegex(InputFileError, "row 1 .*without"):
            require_gains_doc(doc, "mixed.json")

    def test_summary_readers_refuse(self):
        p = _write(ISSUE17, "gains.json")
        r = _cli("taxjson.bin.taxjson_sum_gains", str(p))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("without gain", r.stderr)
        r = _cli("taxjson.bin.taxjson_sum_gains", stdin=json.dumps(ISSUE17))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("without gain", r.stderr)

    def test_filing_readers_refuse(self):
        from taxjson.bin import taxjson_filed
        from taxjson.bin.taxjson_carryover import yearly_nets
        from taxjson.bin.taxjson_form_export import load_dispositions
        with self.assertRaisesRegex(ValueError, "without gain"):
            taxjson_filed.aggregates_from_gains(ISSUE17, year=2025)
        with self.assertRaisesRegex(ValueError, "without gain"):
            yearly_nets(ISSUE17, "trade")
        with self.assertRaisesRegex(ValueError, "without gain"):
            load_dispositions([_write(ISSUE17, "gains.json")], 2025, "date")
        good = {"transactions": [_disp(), _disp(date="2025-02-02", gain=5,
                                                proceeds=15)]}
        agg = taxjson_filed.aggregates_from_gains(good, year=2025)
        self.assertEqual(agg["dispositions"], 2)
        self.assertEqual(yearly_nets(good, "trade")[2025]["net"], 7.0)


if __name__ == "__main__":
    unittest.main()
