"""Backlog group `perf` (2026-10-29): run time of large books.

TJ-0238: for every loss, the US engine walked every replacement record
of the class (and parsed each one's date), and every lot it sold or
split walked them again, so a one-symbol book cost losses x rows (4,000
rows 4.5 s, 8,000 rows 19 s). The records are now indexed by trade-date
window, by row id and by the lot they point at; the scans read the same
records in the same order, so no result changes.

TJ-0239: the wash radar's per-loss capacity test walked every
acquisition of the class; it now reads the window by bisect over the
acquisitions sorted by epoch.

TJ-0240: `strip_json_comments` ran a regex pass with a Python callback
over every string literal of every work/ JSON read (a tenth of a large
run); a text with no `#`, or one that is valid JSON as it stands, is now
returned as is.

The pin tests hold the v0.37.0 results on generated books, captured
before the change; the scaling tests count the per-row work the old
scans did per loss. All data is synthetic (ZZ tickers, made-up prices).

Formerly: test_fix_1029_perf
"""
import contextlib
import io
import json
import random
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as RID

from taxjson.engine.rows import _iso_ordinal
from taxjson.lib import tx_load as TL
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction


def _bdays(lo, hi):
    out, d = [], lo
    while d <= hi:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _row(a, d, sym, q, p, t, mult=1):
    return TaxTransaction(
        action='BUYSELL', date=d.isoformat(), date_settle=d.isoformat(),
        time=t, symbol=sym, quantity=float(q), price=p,
        net_amount=(-1 if q > 0 else 1) * round(abs(q * p * mult), 2) - 1.0,
        commission=1.0, currency='USD', account=a)


def _us(books, blended=False):
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return USATaxRules().compute_gains(
            books['tax'], sheltered_transactions=books['shel'],
            affiliated_transactions=books['aff'],
            per_account_basis=blended)


def _one_symbol_us(pairs):
    """Same-day buy 10 / sell 10 of ZZQ.US, gains and losses alternating,
    four pairs a business day."""
    days = _bdays(date(2024, 1, 2), date(2025, 12, 31))
    rows = []
    for i in range(pairs):
        d = days[i // 4]
        p = 20.0 + (i % 97) * 0.01
        q = round(p + (1 if i % 2 == 0 else -1), 2)
        rows.append(_row('margin', d, 'ZZQ.US', 10, round(p, 2),
                         '10:00:00'))
        rows.append(_row('margin', d, 'ZZQ.US', -10, q, '10:00:01'))
    return rows


class TestUsWashScanScales(unittest.TestCase):
    """TJ-0238: the old scan parsed the date of every replacement record
    of the class for every loss (losses x rows parses); the index parses
    each date once.

    Formerly: test_fix_1029_perf.TestUsWashScanScales"""

    @rule(RID.US_WASH_01)
    def test_date_parses_grow_with_rows_not_losses_x_rows(self):
        import _engine                         # the engine's modules (PLAN.md step 10)
        rows = _one_symbol_us(600)             # 1,200 rows, 300 losses
        parses = {'n': 0}

        class _Counting(datetime):
            @classmethod
            def strptime(cls, s, fmt):
                parses['n'] += 1
                return datetime.strptime(s, fmt)
        _iso_ordinal.cache_clear()
        with _engine.patch_engine('datetime', _Counting):
            res = _us({'tax': rows, 'shel': [], 'aff': []})
        _iso_ordinal.cache_clear()
        losses = [e for e in res['transactions'] if e['raw_gain'] < 0]
        self.assertGreater(len(losses), 300)
        self.assertGreater(len(res['wash_sales']), 250)
        self.assertGreater(parses['n'], 0)
        self.assertLess(parses['n'], 10 * len(rows))


class TestStripJsonComments(unittest.TestCase):
    """TJ-0240: the fast paths return what the pass returns.

    Formerly: test_fix_1029_perf.TestStripJsonComments"""

    def _old(self, text):
        return TL._JSON_COMMENT_RE.sub(
            lambda m: '' if m.group(1) is not None else m.group(0), text)

    def test_text_without_comments_is_returned_as_is(self):
        for text in ('{"a": 1}', '{"a": "x#y", "b#": ["#", "\\"#"]}',
                     '[1, 2]\n', '{"k": "a\\\\"}'):
            self.assertIs(TL.strip_json_comments(text), text)
            self.assertEqual(text, self._old(text))

    def test_comments_are_stripped_as_before(self):
        cases = ['# head\n{"a": 1}', '{"a": 1} # trailing "x',
                 '{"a": "#in"}  # out\n', '{"a": "unterminated # x\n}',
                 '﻿{"a": "#"}', '{"a": NaN} # x', '#',
                 '{"a": "x"\n# "q\n, "b": 2}']
        for text in cases:
            self.assertEqual(TL.strip_json_comments(text),
                             self._old(text), text)

    def test_random_texts_match_the_pass(self):
        r = random.Random(1029)
        alpha = ['"', '#', '\\', '\n', 'a', ' ', '{', '}', '[', ']', ':',
                 ',', '1', 'NaN', '"#"', '\\"', 'true']
        for _ in range(3000):
            text = ''.join(r.choice(alpha) for _ in range(r.randint(0, 24)))
            self.assertEqual(TL.strip_json_comments(text),
                             self._old(text), repr(text))

    def test_large_book_reads_without_the_pass(self):
        """A work/ book with no comment never runs the regex pass."""
        rows = [{"action": "BUYSELL", "date": "2024-01-02", "id": f"r{i}",
                 "description": "ZZQ #1" if i % 7 == 0 else "ZZQ"}
                for i in range(2000)]
        for text in (json.dumps({"transactions": rows}),
                     json.dumps({"transactions": rows[1:7]})):
            with mock.patch.object(TL, '_JSON_COMMENT_RE') as rx:
                out = TL.strip_json_comments(text)
            self.assertIs(out, text)
            rx.sub.assert_not_called()


if __name__ == '__main__':
    unittest.main()
