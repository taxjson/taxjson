"""Regression pins for the stage-tools audit (merge2 / sort / validate /
ticker-map / convert-currency / fill-crypto / convert-tt).

One class per defect, numbered as in the audit:

  1. null numerics crashed every loader with a bare `float(None)`
     traceback while `taxjson-validate` said OK.
  2. wrong-typed but validate-clean rows (`"quantity": "10"`,
     `"symbol": 0`) crashed downstream tools; coerce_transaction_row is
     now the single type funnel.
  3. `taxjson-validate` itself crashed on wrong JSON types.
  4. merge2 / convert-currency bypassed the shared loader (no `#`
     comment stripping, no qty alias) and merge2 emitted an EMPTY merge
     at exit 0 on a commented input.
  5. ticker-map DELETE vs GLOBAL order differed between merge2 and the
     standalone tool.
  6. currency codes compared case/whitespace-sensitively, and a
     currency entirely absent from --rates converted at the implicit
     default with only a warning.
  7. rates-file parser skipped malformed lines silently and accepted
     NaN / negative / zero rates.
  8. fill-crypto cached on the raw symbol (stale after a ticker remap)
     and priced qty=0 rows at one unit of FMV.
  9. `taxjson-sort --dedup` (non-strict) silently DROPPED rows failing
     a private validator, with a stderr line the pipeline's diag
     marker regex ignored.
 10. `.tt` files had no split-fill disambiguation, so two identical
     hand-entered lines collapsed under --dedup.

Formerly: test_stage_tools_audit
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from taxjson.model.transaction import TaxTransaction
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO

ROW = {
    'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
    'symbol': 'AAPL.US', 'quantity': 10, 'price': 15.0,
    'net_amount': 150.0, 'currency': 'USD', 'account': 'Margin',
}


def _run(module, *args, stdin=None):
    return subprocess.run(
        [sys.executable, '-m', module, *args], cwd=REPO_ROOT,
        capture_output=True, text=True, input=stdin,
        env={**os.environ, 'TAXJSON_OFFLINE': '1'},
    )


def _write(tmp, name, rows, text=None):
    p = Path(tmp) / name
    p.write_text(text if text is not None
                 else json.dumps({'transactions': rows}), encoding='utf-8')
    return p


class TestD6CurrencyNormalization(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD6CurrencyNormalization"""
    def test_target_currency_with_case_or_whitespace_is_left_alone(self):
        from taxjson.bin.taxjson_convert_currency import (
            convert_transaction, reset_fallback_tally,
            _DEFAULT_RATE_FALLBACKS)
        hist = {'USD': {'2025-01-15': Decimal('1.40')}}
        reset_fallback_tally()
        for cur in ('cad ', 'Cad', ' CAD'):
            tx = TaxTransaction(**{**ROW, 'currency': cur, 'symbol': 'A.TO'})
            cv = convert_transaction(tx, 'CAD', hist, Decimal('1.35'))
            self.assertEqual(cv.net_amount, 150.0, cur)
            self.assertEqual(cv.currency, 'CAD', cur)
        self.assertEqual(_DEFAULT_RATE_FALLBACKS, {})

    def test_lowercase_source_currency_uses_its_rates(self):
        from taxjson.bin.taxjson_convert_currency import convert_transaction
        hist = {'USD': {'2025-01-15': Decimal('1.40')}}
        tx = TaxTransaction(**{**ROW, 'currency': 'usd'})
        cv = convert_transaction(tx, 'cad', hist, Decimal('1.35'))
        self.assertAlmostEqual(cv.net_amount, 210.0)
        self.assertEqual(cv.currency, 'CAD')

    def test_uncovered_currency_fatal_unless_default_rate_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', [{**ROW, 'currency': 'EUR'}])
            rates = Path(tmp) / 'r.csv'
            rates.write_text('2025-01-15 12:00:00 USD CAD 1.40\n')
            r = _run('taxjson.bin.taxjson_convert_currency', str(p),
                     '--to', 'CAD', '--rates', str(rates))
            self.assertNotEqual(r.returncode, 0)
            # No built-in rate (owner, 2026-10-04): the row's pair and
            # date are named.
            self.assertIn('EUR->CAD on', r.stderr)
            self.assertIn('no rates for currency', r.stderr)
            r2 = _run('taxjson.bin.taxjson_convert_currency', str(p),
                      '--to', 'CAD', '--rates', str(rates),
                      '--default-rate', '1.5')
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertAlmostEqual(
                json.loads(r2.stdout)['transactions'][0]['net_amount'], 225.0)
            m = _run('taxjson.bin.taxjson_merge2', '--to', 'CAD',
                     '--rates', str(rates), str(p))
            self.assertNotEqual(m.returncode, 0)
            self.assertIn('EUR->CAD on', m.stderr)


class TestD7RatesFileParser(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD7RatesFileParser"""
    def _load(self, text, **kw):
        from taxjson.lib.exchange_rates import load_exchange_rates
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'r.csv'
            p.write_text(text)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                hist = load_exchange_rates(p, target_curr='CAD', **kw)
        return hist, err.getvalue()

    def test_malformed_lines_counted_and_reported(self):
        hist, err = self._load(
            "2025-01-02 12:00:00 USD CAD 1.44\n"
            "2025-01-06 USD CAD 1.46\n"              # no TIME column
            "2025-01-07,12:00:00,USD,CAD,1.48\n"    # comma-separated
            "2025/01/12 12:00:00 USD CAD 1.50\n"    # slash date
            "# a comment line with five tokens\n"
            "\n")
        self.assertEqual(hist, {'USD': {'2025-01-02': Decimal('1.44')}})
        self.assertIn('skipped 3 malformed FX rate line(s)', err)

    def test_currency_columns_uppercased(self):
        hist, _ = self._load("2025-01-13 12:00:00 usd cad 1.51\n")
        self.assertEqual(hist, {'USD': {'2025-01-13': Decimal('1.51')}})

    def test_non_finite_or_non_positive_rate_is_fatal(self):
        for bad in ('NaN', 'Infinity', '-1.5', '0'):
            with self.assertRaises(ValueError, msg=bad) as cm:
                self._load(f"2025-01-08 12:00:00 USD CAD {bad}\n")
            self.assertIn('line 1', str(cm.exception))

    def test_a_date_that_is_no_calendar_day_is_refused(self):
        # Backlog TJ-5046: `2025-02-30` has the shape of a date, sorts
        # after every real February day and was the newest observation.
        # Refused, naming the line, as a rate that is no positive number
        # is: skipped, another day's rate would stand in for its day.
        good = "2025-02-28 12:00:00 USD CAD 1.35 boc\n"
        for bad, date in (
                ("2025-02-30 12:00:00 USD CAD 9.99 boc", "2025-02-30"),
                ("2025-13-01 12:00:00 USD CAD 9.98 boc", "2025-13-01"),
                ("2025-02-29 12:00:00 USD CAD 9.98 boc", "2025-02-29"),
                # main refused this one for its rate: still refused
                ("2025-02-30 12:00:00 USD CAD nan", "2025-02-30"),
                # a filled row whose published date is no calendar day
                ("2025-03-02 12:00:00 USD CAD 9.97 boc 2025-02-31",
                 "2025-02-31")):
            with self.assertRaises(ValueError, msg=bad) as cm:
                self._load(good + bad + "\n")
            msg = str(cm.exception)
            self.assertIn(f"line 2: '{date}' is not a day of the calendar",
                          msg)
            self.assertIn(bad, msg)
            self.assertIn("refusing to convert money", msg)
        # A row to another currency is skipped as before, whatever its
        # date; a real leap day and an earlier published day are kept.
        hist, err = self._load(
            good + "2025-02-30 12:00:00 USD EUR 0.9\n"
            "2024-02-29 12:00:00 USD CAD 1.34\n"
            "2025-03-03 12:00:00 USD CAD 1.36 boc 2025-02-28\n")
        self.assertEqual(hist, {'USD': {'2025-02-28': Decimal('1.35'),
                                        '2024-02-29': Decimal('1.34'),
                                        '2025-03-03': Decimal('1.36')}})
        self.assertEqual(hist.published,
                         {'USD': {'2025-03-03': '2025-02-28'}})
        self.assertIn('skipped 1 FX rate row(s) where the TO', err)
        self.assertNotIn('malformed', err)

    def test_one_grammar_for_the_conversion_and_the_send_reader(self):
        # Backlog TJ-5045 / TJ-5046: lib/base_rates.load_rates (a crypto
        # send's value) read `1_35` as 135 while this reader skipped the
        # line; both go through exchange_rates.parse_rate_line.
        from taxjson.lib import base_rates
        from taxjson.lib import exchange_rates as ER

        def send(text):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / 'to_base.csv'
                p.write_text(text)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    return base_rates.load_rates(p, 'CAD'), err.getvalue()
        text = ("2025-02-28 12:00:00 USD CAD 1.35 boc\n"
                "2025-03-01 12:00:00 USD CAD +1.4e0\n"
                "2025-03-02 USD CAD 1.5\n")           # no row: no TIME
        hist, err = self._load(text)
        table, send_err = send(text)
        self.assertEqual({d: float(r) for d, r in hist['USD'].items()},
                         {'2025-02-28': 1.35, '2025-03-01': 1.4})
        self.assertEqual({d: r for d, (r, _src) in table['USD'].items()},
                         {'2025-02-28': 1.35, '2025-03-01': 1.4})
        self.assertIn('skipped 1 malformed FX rate line(s)', err)
        self.assertEqual(send_err, '')
        # One rule for every rates reader: a row whose rate is no plain
        # decimal is refused naming the line, never skipped (`1_35` was
        # 135 for a send and a counted skip here; `abc` and `1,35` were
        # skipped by both): another day's rate would stand in.
        for bad in ('1_35', 'abc', '1,35', '0x1p0'):
            more = text + f"2025-03-04 12:00:00 USD CAD {bad} boc\n"
            for load, what in ((self._load, 'convert money'),
                               (send, 'value a send')):
                with self.assertRaises(ValueError, msg=bad) as cm:
                    load(more)
                self.assertIn(f"line 4: rate {bad!r} for USD->CAD on "
                              f"2025-03-04 is not a plain decimal number",
                              str(cm.exception))
                self.assertIn(f"refusing to {what}", str(cm.exception))
        # The conversion still skips a row to another currency first.
        hist, err = self._load(text + "2025-03-04 12:00:00 USD EUR abc\n")
        self.assertNotIn('2025-03-04', hist['USD'])
        for line, problem in (
                ("", ER.RATE_BLANK), ("# 2025-01-02 x USD CAD 1", ER.RATE_BLANK),
                ("2025-01-02 USD CAD 1.3", ER.RATE_NOT_A_ROW),
                ("Jan-02 12:00:00 USD CAD 1.3", ER.RATE_NOT_A_ROW),
                ("2025-02-29 12:00:00 USD CAD 1.3", ER.RATE_BAD_DATE),
                ("2025-02-29 12:00:00 USD CAD nan", ER.RATE_BAD_DATE),
                ("2025-03-02 12:00:00 USD CAD 1.3 boc 2025-02-31",
                 ER.RATE_BAD_DATE),
                ("2024-02-29 12:00:00 USD CAD 1.3", ""),
                ("2025-01-02 12:00:00 USD CAD abc", ER.RATE_UNREADABLE),
                ("2025-01-02 12:00:00 USD CAD 1_3", ER.RATE_UNREADABLE),
                ("2025-01-02 12:00:00 USD CAD nan", ER.RATE_NOT_POSITIVE),
                ("2025-01-02 12:00:00 USD CAD 1e400", ER.RATE_NOT_POSITIVE),
                ("2025-01-02 12:00:00 USD CAD -1_0", ER.RATE_NOT_POSITIVE)):
            self.assertEqual(ER.parse_rate_line(line)[0], problem, line)
        row = ER.parse_rate_line(
            " 2025-01-05 12:00:00 usd cad 1.30 BoC 2025-01-03 ")[1]
        self.assertEqual(row, ER.RateLine(
            '2025-01-05', 'USD', 'CAD', '1.30', Decimal('1.30'), 'boc',
            '2025-01-03'))

    BAD_ROWS = (("2025-01-16 12:00:00 USD CAD abc yahoo",
                 "is not a plain decimal number"),
                ("2025-02-30 12:00:00 USD CAD 1.4 yahoo",
                 "is not a day of the calendar"),
                ("2025-01-16 12:00:00 USD CAD nan yahoo",
                 "is not a positive finite number"))

    def test_a_refused_row_is_one_error_line_from_the_tools(self):
        # The refusals reach the person as `error: ...` naming the file
        # and the line, exit 2 (a refused input), never a traceback.
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', [ROW])
            rates = Path(tmp) / 'r.csv'
            for bad, why in self.BAD_ROWS:
                rates.write_text('2025-01-15 12:00:00 USD CAD 1.40\n'
                                 + bad + '\n')
                for r in (
                        _run('taxjson.bin.taxjson_convert_currency', str(p),
                             '--to', 'CAD', '--rates', str(rates)),
                        _run('taxjson.bin.taxjson_merge2', '--to', 'CAD',
                             '--rates', str(rates), str(p)),
                        _run('taxjson.bin.taxjson_fees', str(p),
                             '--to', 'CAD', '--rates', str(rates))):
                    text = ' '.join((r.stdout + r.stderr).split())
                    self.assertEqual(r.returncode, 2, (bad, r.args, text))
                    self.assertNotIn('Traceback', text)
                    self.assertIn(f"rates file {rates}, line 2:", text)
                    self.assertIn(why, text)
                    self.assertRegex(r.stderr, r'(?m)^(\S+: )?[Ee]rror: ')

    def test_a_refused_row_is_one_error_line_from_div_estimate(self):
        # `tjs div-estimate` printed a traceback (the review of
        # TJ-5045 / TJ-5046; a NaN rate did on main).
        from _qa_project import tj, toml
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "work").mkdir(parents=True)
            (root / "taxjson.toml").write_text(toml(year=2025))
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [{
                    "action": "DIVIDEND", "date": "2025-01-02",
                    "date_settle": "2025-01-02", "symbol": "ZZD.US",
                    "quantity": 10, "gross_amount": 10.0,
                    "net_amount": 10.0, "currency": "USD",
                    "account": "margin"}]}))
            (root / "work" / "margin_raw_gains.json").write_text(
                json.dumps({"inventory": [{"symbol": "ZZD.US", "qty": 10,
                                           "total_cost": 100,
                                           "currency": "USD"}]}))
            rates = root / "work" / "to_base.csv"
            for bad, why in self.BAD_ROWS:
                rates.write_text('2025-01-15 12:00:00 USD CAD 1.40\n'
                                 + bad + '\n')
                r = tj(root, "div-estimate", "--date", "2025-06-01",
                       check=False)
                text = ' '.join((r.stdout + r.stderr).split())
                self.assertEqual(r.returncode, 2, (bad, text))
                self.assertNotIn('Traceback', text)
                self.assertIn("line 2:", text)
                self.assertIn(why, text)
                self.assertRegex(r.stderr,
                                 r'(?m)^(tjs div-estimate: e|E)rror: rates file ')
            rates.write_text('2025-01-15 12:00:00 USD CAD 1.40\n')
            tj(root, "div-estimate", "--date", "2025-06-01")

    def test_a_dropped_line_names_no_source(self):
        from taxjson.bin.taxjson_convert_currency import load_rate_sources
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'r.csv'
            p.write_text("2025-02-28 12:00:00 USD CAD 1.35 boc\n"
                         "2025-02-30 12:00:00 USD CAD 9.99 yahoo\n"
                         "2025-03-03 12:00:00 USD CAD 1_36 yahoo\n"
                         "2025-03-03 12:00:00 USD EUR 0.9 yahoo\n")
            self.assertEqual(load_rate_sources(p, 'CAD'),
                             {'USD': {'2025-02-28': 'boc'}})


if __name__ == '__main__':
    unittest.main()
