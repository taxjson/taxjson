"""External audit of 2026-10-24 (against 75392b5), group "misc": the
small commands. One test per finding, each a regression of its probe.

B06 xlsx-to-csv wrote through an output symlink; B07 watch skipped the
harvest_now check when the state had no radar; B08 watch told a
sheltered-only holder to sell at a loss; B09 a settled futures row let a
raw fill beside it keep its notional; B29 dedup's replacement id could
equal an id already in the book; B33 a finite Decimal rate overflowed
the converted float to inf; B46 sum-income dropped the funnel's
normalized row (a null `type` crashed); B49 the `amount` alias was never
validated; D03 a non-finite dividend printed an infinite total; D04
gains files of two years were summed under the last file's year.

All data is synthetic (made-up tickers such as ZZQ.US, no real amounts).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from taxjson.bin import taxjson_convert_currency as fx
from taxjson.bin import taxjson_sort as sorter
from taxjson.bin import taxjson_sum_income as si
from taxjson.bin import taxjson_watch as watch
from taxjson.bin import xlsx_to_csv as xlsx
from taxjson.lib import futures as fut
from taxjson.lib.core import TaxTransaction
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.json_input import InputFileError, check_row_types

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _tx(d, q, sym="ZZQ.US", action="BUYSELL", **kw):
    return dict(date=d, date_settle=d, time="10:00:00", account="margin",
                symbol=sym, action=action, quantity=q, net_amount=0,
                currency="USD", **kw)


def _sum_gains(*args):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_sum_gains", list(args)),
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))


class _Sheet:
    """The DataFrame the mocked reader returns: only to_csv is used."""

    def __init__(self, text):
        self.text = text

    def to_csv(self, path_or_buf=None, **kw):
        if path_or_buf is None:
            return self.text
        path_or_buf.write(self.text)


class TestMisc(unittest.TestCase):

    def test_B06_xlsx_output_symlink_is_replaced_not_followed(self):
        """B06: XLSX conversion follows an output symlink and overwrites
        its target. The CSV is now staged and renamed over `-o`
        (lib/safe_write.write_atomic): the link is replaced by the CSV
        and its target keeps its contents."""
        stub = {} if "pandas" in sys.modules or _importable("pandas") \
            else {"pandas": types.ModuleType("pandas")}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "input.xlsx"
            inp.touch()
            victim = root / "original.csv"
            victim.write_text("original contents")
            dest = root / "output.csv"
            dest.symlink_to(victim)
            with patch.dict(sys.modules, stub), \
                    patch.object(xlsx, "read_sheet",
                                 return_value=_Sheet("symbol\nZZQ\n")), \
                    patch.object(xlsx, "_apply_cells", lambda df, fn: df), \
                    contextlib.redirect_stderr(io.StringIO()):
                xlsx.convert_xlsx_to_csv(str(inp), str(dest))
            self.assertEqual(victim.read_text(), "original contents")
            self.assertFalse(dest.is_symlink())
            self.assertEqual(dest.read_text(), "symbol\nZZQ\n")
            # No temp file left beside the output.
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             ["input.xlsx", "original.csv", "output.csv"])

    def test_B07_watch_damaged_harvest_state_without_radar_rebaselines(self):
        """B07: Watch skips harvest-state validation when the radar
        field is absent. A state with no radar and a text harvest_now
        is damaged: warned, and a new baseline is recorded (None)."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            p.write_text(json.dumps({"schema_version": 1,
                                     "harvest_now": "broken"}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(watch.load_state(p))
            self.assertIn("damaged", err.getvalue())
            self.assertIn('"harvest_now" is not a number', err.getvalue())
            self.assertIn("NEW baseline", err.getvalue())
            # A non-finite number is no harvest total either.
            p.write_text('{"schema_version": 1, "harvest_now": NaN}')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(watch.load_state(p))
            # Control: a harvest-only state (no radar yet) is a baseline.
            p.write_text(json.dumps({"schema_version": 1,
                                     "harvest_now": 12.5}))
            with contextlib.redirect_stderr(io.StringIO()):
                state = watch.load_state(p)
            self.assertEqual(state["harvest_now"], 12.5)
            self.assertIsNone(watch.diff_harvest(state["harvest_now"],
                                                 12.5, 1))

    def test_B08_watch_cleared_sheltered_only_holding_has_no_sale_line(self):
        """B08: Watch gives loss-sale instructions for a position held
        only in sheltered accounts. A LOCKED -> CLEAR row with no
        taxable units and a sheltered holding says so instead."""
        ch = watch.diff_radar(
            {"ZZQ.US": {"category": "LOCKED"}},
            {"ZZQ.US": {"category": "CLEAR", "taxable_qty": 0,
                        "sheltered_qty": 10}})
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0]["kind"], "cleared")
        self.assertEqual(ch[0]["scope"], "sheltered")
        self.assertNotIn("safe to sell", ch[0]["line"])
        self.assertIn("held only in sheltered account(s)", ch[0]["line"])
        self.assertIn("nothing to sell at a loss", ch[0]["line"])
        # Control: a taxable holding keeps the sale instruction, and a
        # row without the scope fields (an older radar) is not judged.
        for cur in ({"category": "CLEAR", "taxable_qty": 10,
                     "sheltered_qty": 10},
                    {"category": "CLEAR"}):
            ch = watch.diff_radar({"ZZQ.US": {"category": "LOCKED"}},
                                  {"ZZQ.US": cur})
            self.assertIn("safe to sell at a loss", ch[0]["line"])
            self.assertNotIn("scope", ch[0])

    def test_B09_futures_book_mixing_settled_and_raw_rows_is_refused(self):
        """B09: One already-settled futures row causes raw contracts in
        the same book to bypass settlement. A mixed book is refused
        (ValueError naming both rows); a settled book is still returned
        as is, and a raw book is still settled."""
        a = TaxTransaction(**_tx("2026-01-01", 1, sym="F:ZZH6"))
        a.net_amount = 100
        settled, _ = fut.settle_futures([a], "average")
        self.assertEqual(settled[0].type, fut.FUTURES_SETTLEMENT)
        b = TaxTransaction(**_tx("2026-01-02", 1, sym="F:ZZM6"))
        b.net_amount = 200
        with self.assertRaises(ValueError) as cm:
            fut.settle_futures([*settled, b], "average")
        msg = str(cm.exception)
        self.assertIn("F:ZZH6", msg)
        self.assertIn("F:ZZM6", msg)
        self.assertIn("settled once, as a whole", msg)
        # Idempotent on a settled book; a raw fill alone is settled.
        again, _ = fut.settle_futures(settled, "average")
        self.assertEqual([r.to_dict() for r in again],
                         [r.to_dict() for r in settled])
        rows, _ = fut.settle_futures([b], "average")
        self.assertEqual([(r.type, r.net_amount) for r in rows],
                         [(fut.FUTURES_SETTLEMENT, 0.0)])

    def test_B29_dedup_replacement_id_skips_an_id_already_in_the_book(self):
        """B29: Dedup-generated replacement ids collide with an existing
        suffixed id. Three records (two `same` of different accounts
        and one `same~2`) keep three distinct ids."""
        rows = [dict(_tx("2026-01-02", 1), id="same", source="a.csv",
                     source_account="one"),
                dict(_tx("2026-01-02", 1), id="same", source="b.csv",
                     source_account="two"),
                dict(_tx("2026-01-03", 1), id="same~2", source="b.csv",
                     source_account="two")]
        plan = sorter.plan_dedup(rows)
        self.assertEqual(plan.drop, [])
        self.assertEqual(plan.relabel, {1: "same~3"})
        for i, new_id in plan.relabel.items():
            rows[i]["id"] = new_id
        self.assertEqual(len({r["id"] for r in rows}), 3)

    def test_B33_fx_rate_that_overflows_a_float_is_refused(self):
        """B33: A finite Decimal FX rate can overflow float amounts
        without refusing conversion. The rates reader refuses a rate
        whose float is inf or 0, and convert_transaction refuses a
        non-finite result instead of booking net_amount=inf."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rates.csv"
            for bad in ("1e400", "1e-400"):
                p.write_text(f"2026-01-02 12:00:00 USD CAD {bad}\n")
                with self.assertRaises(ValueError) as cm:
                    fx.load_exchange_rates(p, "CAD")
                self.assertIn("not a positive finite number",
                              str(cm.exception))
                self.assertIn(bad, str(cm.exception))
            # Control: a plain rate loads.
            p.write_text("2026-01-02 12:00:00 USD CAD 1.35\n")
            history = fx.load_exchange_rates(p, "CAD")
            self.assertEqual(history["USD"]["2026-01-02"], Decimal("1.35"))
        # The conversion itself: a finite rate times a huge amount.
        row = TaxTransaction(**_tx("2026-01-02", 1))
        row.net_amount = 1e300
        history = {"USD": {"2026-01-02": Decimal("1e10")}}
        with self.assertRaises(ValueError) as cm:
            fx.convert_transaction(row, "CAD", history, None)
        msg = str(cm.exception)
        self.assertIn("ZZQ.US", msg)
        self.assertIn("net_amount", msg)
        self.assertIn("not a finite number", msg)
        self.assertEqual(row.currency, "USD")      # nothing booked
        # Control: an ordinary amount converts.
        row.net_amount = 10.0
        out = fx.convert_transaction(row, "CAD", history, None)
        self.assertEqual(out.currency, "CAD")
        self.assertEqual(out.net_amount, 1e11)

    def test_B46_income_rows_keep_the_funnels_normalized_fields(self):
        """B46: Income summary discards normalized rows and crashes on an
        accepted null type. The loaded row carries the funnel's values
        (a null `type` is the default ''), and the dividend is summed."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "income.json"
            row = _tx("2026-01-02", 10, action="DIVIDEND", type=None)
            row["net_amount"] = "10"            # a numeric string
            row["date"] = "2026-1-02"           # an unpadded date
            p.write_text(json.dumps({"transactions": [row]}))
            loaded = si.load_income_data(p)
            self.assertEqual(loaded[0]["type"], "")
            self.assertEqual(loaded[0]["net_amount"], 10.0)
            self.assertEqual(loaded[0]["date"], "2026-01-02")
            report = si.summarize_income(loaded, 2026)
            self.assertEqual(report["ticker_stats"]["ZZQ.US"]["USD"]["div"],
                             10.0)
            # A key the funnel does not know stays as written.
            row["note"] = "kept"
            p.write_text(json.dumps({"transactions": [row]}))
            self.assertEqual(si.load_income_data(p)[0]["note"], "kept")

    def test_B49_income_amount_alias_is_validated_like_the_others(self):
        """B49: Income amount alias bypasses finite-number validation.
        `amount` ("NaN", a bool, text) is refused like net_amount is; a
        numeric string is accepted as its number."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "income.json"
            row = _tx("2026-01-02", 0, action="INTEREST")
            row.pop("net_amount")
            for bad, word in (("NaN", "non-finite amount"),
                              (True, "is not a number"),
                              ("ten", "non-numeric amount")):
                row["amount"] = bad
                p.write_text(json.dumps({"transactions": [row]}))
                with self.assertRaises(InputFileError) as cm:
                    si.load_income_data(p)
                self.assertIn(word, str(cm.exception))
                self.assertIn(str(p), str(cm.exception))
            row["amount"] = "12.5"
            p.write_text(json.dumps({"transactions": [row]}))
            loaded = si.load_income_data(p)
            self.assertEqual(loaded[0]["amount"], 12.5)
            report = si.summarize_income(loaded, 2026)
            self.assertEqual(report["interest_totals"], {"USD": 12.5})
            # A null amount is still the 'no amount' case, not an error
            # here (main refuses it with its own line).
            row["amount"] = None
            p.write_text(json.dumps({"transactions": [row]}))
            loaded = si.load_income_data(p)
            self.assertIsNone(loaded[0]["amount"])
            self.assertEqual(
                len(si.summarize_income(loaded, 2026)["missing_amount"]), 1)

    def test_D03_sum_gains_refuses_a_non_finite_dividend(self):
        """D03: Overflowing dividend numbers pass validation and print an
        infinite total. `dividend` and `pil` are finite-number fields of
        the row funnel: the file is refused, no `inf` is printed."""
        with self.assertRaises(InputFileError) as cm:
            check_row_types([{"symbol": "ZZQ.US", "dividend": float("inf")}],
                            "g.json")
        self.assertIn("dividend is inf", str(cm.exception))
        with self.assertRaises(InputFileError):
            check_row_types([{"symbol": "ZZQ.US", "pil": float("nan")}],
                            "g.json")
        check_row_types([{"symbol": "ZZQ.US", "dividend": 1.5, "pil": None}],
                        "g.json")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "infinite.json"
            p.write_text('{"summary": {"year": 2026}, "transactions": [{'
                         '"date": "2026-01-02", "action": "DIVIDEND", '
                         '"symbol": "ZZQ.US", "currency": "CAD", "qty": 1, '
                         '"dividend": 1e400, "gain": 0}]}')
            r = _sum_gains(str(p), "--no-color")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("dividend is inf", r.stderr)
            self.assertNotIn("inf", r.stdout.lower())

    def test_D04_sum_gains_refuses_gains_files_of_different_years(self):
        """D04: Multi-year gains files are combined under the last files
        year. Files whose summary.year differ are refused, naming each
        file and its year; files of one year are still summed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = []
            for y, g in ((2025, 10), (2026, 20)):
                p = root / f"{y}.json"
                p.write_text(json.dumps({"summary": {"year": y}, "transactions": [
                    {"date": f"{y}-01-02", "symbol": "ZZQ.US",
                     "currency": "CAD", "qty": 1, "cost": 10,
                     "proceeds": 10 + g, "gain": g}]}))
                paths.append(str(p))
            r = _sum_gains("--json", *paths)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertIn("different tax years", r.stderr)
            for p, y in zip(paths, ("2025", "2026")):
                self.assertIn(f"{p} ({y})", r.stderr.replace("\n", " "))
            # Control: two files of one year sum under that year.
            r = _sum_gains("--json", paths[0], paths[0])
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["total_year"], 2025)
            self.assertEqual(doc["ticker_stats"]["ZZQ.US"]["CAD"]["cap"], 20.0)


def _importable(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    unittest.main()
