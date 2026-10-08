"""GitHub issues #5, #6 and #8: input contracts of the JSON loaders.

#5  An unpadded date ("2025-2-01") was accepted, but the engines order
    rows by comparing date STRINGS: the row sorted after "2025-10-01",
    which changed FIFO/ACB gains and turned a long sale into a short
    cover. Every date field is now written YYYY-MM-DD at coercion; a
    value that is not a real calendar date in that shape is refused.
#6  A book object without a "transactions" key ("Transactions") was read
    as an empty book: empty gains at exit 0.
#8  A NaN gain in a gains file gave a successful form export with NaN in
    its JSON.

All data is synthetic.
"""
import contextlib
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import (CanadaTaxRules, USATaxRules,
                              coerce_transaction_row, load_transactions)
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _row(date, qty, net, sym="SYNTH.US", cur="USD", **kw):
    return dict(action="BUYSELL", date=date, date_settle=date, symbol=sym,
                currency=cur, quantity=qty, net_amount=net, account="T1",
                **kw)


def _coerce(rows):
    return [coerce_transaction_row(r, i, "test") for i, r in enumerate(rows)]


def _gains(engine, txs):
    with contextlib.redirect_stderr(io.StringIO()):
        return engine().compute_gains(txs, detect_wash_sales=False)


def _disp(result):
    return [(t["date"], t.get("direction"), round(t["cost"], 6),
             round(t["gain"], 6)) for t in result["transactions"]]


def _cli(module, *args, stdin=None):
    return subprocess.run([sys.executable, "-m", module, *args],
                          capture_output=True, text=True, input=stdin,
                          stdin=None if stdin is not None
                          else subprocess.DEVNULL)


class TestIssue5DatesCanonical(unittest.TestCase):

    def _three(self, first):
        return [_row(first, 1, -100), _row("2025-10-01", 1, -200),
                _row("2025-11-01", -1, 120)]

    def test_three_row_repro_both_engines(self):
        for engine in (USATaxRules, CanadaTaxRules):
            with self.subTest(engine=engine.__name__):
                loose = _gains(engine, _coerce(self._three("2025-2-01")))
                tight = _gains(engine, _coerce(self._three("2025-02-01")))
                self.assertEqual(_disp(loose), _disp(tight))
        us = _gains(USATaxRules, _coerce(self._three("2025-2-01")))
        # FIFO: the February share (cost 100) is the one sold.
        self.assertEqual(_disp(us), [("2025-11-01", "LONG", 100.0, 20.0)])

    def test_two_row_long_sale_is_not_a_short_cover(self):
        # "2025-10-01" < "2025-2-01" as strings: the sale sorted first and
        # opened a short that the February buy then covered.
        for engine in (USATaxRules, CanadaTaxRules):
            with self.subTest(engine=engine.__name__):
                r = _gains(engine, _coerce([_row("2025-2-01", 1, -100),
                                            _row("2025-10-01", -1, 130)]))
                self.assertEqual(_disp(r),
                                 [("2025-10-01", "LONG", 100.0, 30.0)])

    def test_every_date_field_is_written_canonically(self):
        row = _row("2025-2-1", 1, -100, lot_date="2024-3-9",
                   record_date="2025-1-31", ex_date="2025-1-30",
                   time="9:30")
        row["date_settle"] = "2025-2-4"
        t = coerce_transaction_row(row, 0, "test")
        self.assertEqual(
            (t.date, t.date_settle, t.lot_date, t.record_date, t.ex_date,
             t.time),
            ("2025-02-01", "2025-02-04", "2024-03-09", "2025-01-31",
             "2025-01-30", "09:30:00"))

    def test_invalid_dates_are_refused(self):
        bad = ("2025-02-30", "2025-13-01", "2025/02/01", "25-2-1",
               "2025-02-01T10:00", "2025-2", "Feb 1 2025", "2025-0-10")
        for fld in ("date", "date_settle", "lot_date", "record_date",
                    "ex_date"):
            for v in bad:
                with self.subTest(field=fld, value=v):
                    row = _row("2025-02-01", 1, -100)
                    row[fld] = v
                    with self.assertRaises(ValueError) as cm:
                        coerce_transaction_row(row, 0, "test")
                    self.assertIn(fld, str(cm.exception))
                    self.assertIn("YYYY-MM-DD", str(cm.exception))

    def test_file_and_stdin_loaders_normalise(self):
        from taxjson.lib.pipeline import load_stdin_transactions
        doc = {"transactions": [_row("2025-2-01", 1, -100)]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "book.json"
            p.write_text(json.dumps(doc))
            self.assertEqual(load_transactions(p)[0].date, "2025-02-01")
        txs = load_stdin_transactions(io.StringIO(json.dumps(doc)))
        self.assertEqual(txs[0].date_settle, "2025-02-01")

    def test_gains_cli_orders_an_unpadded_book_like_a_padded_one(self):
        outs = []
        with tempfile.TemporaryDirectory() as tmp:
            for first in ("2025-2-01", "2025-02-01"):
                p = Path(tmp) / f"book{len(outs)}.json"
                p.write_text(json.dumps(
                    {"transactions": self._three(first)}))
                r = _cli("taxjson.bin.taxjson_gains", str(p),
                         "--country", "usa", "--no-wash")
                self.assertEqual(r.returncode, 0, r.stderr)
                outs.append(json.loads(r.stdout)["summary"])
        self.assertEqual(outs[0], outs[1])


class TestIssue6MissingTransactionsKey(unittest.TestCase):

    _ROWS = [_row("2025-01-01", 1, -100, sym="SYNTH.TO", cur="CAD")]

    def _write(self, tmp, doc):
        p = Path(tmp) / "book.json"
        p.write_text(json.dumps(doc))
        return p

    def test_load_transactions_refuses_an_object_without_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, {"Transactions": self._ROWS})
            with self.assertRaises(ValueError) as cm:
                load_transactions(p)
        msg = str(cm.exception)
        self.assertIn('no "transactions" list', msg)
        self.assertIn("Transactions", msg)        # the keys it found

    def test_bare_list_and_explicit_empty_list_still_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(load_transactions(
                self._write(tmp, self._ROWS))), 1)
            self.assertEqual(load_transactions(
                self._write(tmp, {"transactions": []})), [])

    def test_gains_cli_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, {"Transactions": self._ROWS})
            r = _cli("taxjson.bin.taxjson_gains", str(p),
                     "--country", "canada")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('no "transactions" list', r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_row_list_readers_refuse_the_missing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, {"Transactions": self._ROWS})
            for mod, args in (
                    ("taxjson.bin.taxjson_wash_radar",
                     ("--taxable", str(p), "--country", "canada")),
                    ("taxjson.bin.taxjson_lint_crosslistings",
                     ("--taxable", str(p))),
                    ("taxjson.bin.taxjson_sum_income",
                     (str(p), "--year", "2025")),
                    ("taxjson.bin.taxjson_diff", (str(p), str(p)))):
                with self.subTest(tool=mod):
                    r = _cli(mod, *args)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn('no "transactions" list', r.stderr)


def _gains_doc(gain):
    return {"summary": {"country": "canada", "base_currency": "CAD"},
            "transactions": [{"date": "2025-10-01",
                              "date_settle": "2025-10-01",
                              "action": "BUYSELL", "symbol": "SYNTH.TO",
                              "qty": 1, "proceeds": 120.0, "cost": 100.0,
                              "gain": gain, "currency": "CAD",
                              "direction": "LONG"}]}


class TestIssue8NonFinite(unittest.TestCase):

    def test_form_export_refuses_a_nan_gain(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in (float("nan"), float("inf"), float("-inf")):
                p = Path(tmp) / "gains.json"
                p.write_text(json.dumps(_gains_doc(bad)))
                with self.subTest(value=bad):
                    r = _cli("taxjson.bin.taxjson_form_export", str(p),
                             "--country", "canada", "--form", "schedule3",
                             "--year", "2025", "--json")
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertIn("non-finite", r.stderr)
                    self.assertNotIn("NaN", r.stdout)
            # The same file with a real number still exports.
            p.write_text(json.dumps(_gains_doc(20.0)))
            r = _cli("taxjson.bin.taxjson_form_export", str(p),
                     "--country", "canada", "--form", "schedule3",
                     "--year", "2025", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            json.loads(r.stdout)

    def test_other_report_readers_refuse_a_nan_gain(self):
        doc = json.dumps(_gains_doc(float("nan")))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "gains.json"
            p.write_text(doc)
            for mod, args, stdin in (
                    ("taxjson.bin.taxjson_sum_gains",
                     ("--country", "canada", str(p)), None),
                    ("taxjson.bin.taxjson_sum_gains",
                     ("--country", "canada", "--json"), doc),
                    ("taxjson.bin.taxjson_export",
                     ("--report", str(p)), None)):
                with self.subTest(tool=mod, stdin=stdin is not None):
                    r = _cli(mod, *args, stdin=stdin)
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertIn("non-finite", r.stderr)

    def test_shared_loader_refuses_nan_anywhere(self):
        from taxjson.lib.json_input import InputFileError, read_json_doc
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "doc.json"
            p.write_text('{"summary": {"total_gain": NaN}, '
                         '"transactions": []}')
            with self.assertRaises(InputFileError) as cm:
                read_json_doc(p)
            self.assertIn("non-finite", str(cm.exception))
            self.assertIn(str(p), str(cm.exception))

    def test_row_check_refuses_non_finite_numbers(self):
        from taxjson.lib.json_input import InputFileError, check_row_types
        for bad in (math.nan, math.inf):
            with self.subTest(value=bad):
                with self.assertRaises(InputFileError) as cm:
                    check_row_types([{"symbol": "SYNTH.TO", "gain": bad}],
                                    "g.json")
                self.assertIn("non-finite", str(cm.exception))

    def test_filing_writer_refuses_non_finite(self):
        from taxjson.lib.json_input import dump_filing_json
        buf = io.StringIO()
        with self.assertRaises(ValueError) as cm:
            dump_filing_json({"lines": [{"gain": math.nan}]}, buf)
        self.assertIn("lines[0].gain", str(cm.exception))
        self.assertEqual(buf.getvalue(), "")
        dump_filing_json({"lines": [{"gain": 1.5}]}, buf)
        self.assertEqual(json.loads(buf.getvalue()),
                         {"lines": [{"gain": 1.5}]})


class TestFormExportCsvOwnTempFile(unittest.TestCase):
    """--csv wrote through a FIXED `<out>.part` (the issue #9 pattern):
    a leftover or concurrent `<out>.part` made the write fail, and two
    writers shared one temp file. It now writes through
    safe_write.atomic_open (a unique temp of its own)."""

    def _rep(self, gain):
        from taxjson.bin import taxjson_form_export as FE
        return FE.build_schedule3(_gains_doc(gain)["transactions"])

    def test_csv_ignores_a_leftover_part_file(self):
        from taxjson.bin.taxjson_form_export import write_csv
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s3.csv"
            stale = Path(tmp) / "s3.csv.part"
            stale.write_text("someone else's temp\n")
            write_csv(self._rep(20.0), out)
            self.assertIn("SYNTH.TO", out.read_text())
            self.assertEqual(stale.read_text(), "someone else's temp\n")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             ["s3.csv", "s3.csv.part"])
            self.assertEqual(out.stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
