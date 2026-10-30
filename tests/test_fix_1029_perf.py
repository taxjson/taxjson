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
"""
import contextlib
import hashlib
import io
import json
import random
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from tax_rules import rule

from taxjson.lib import core
from taxjson.lib.core import TaxTransaction, USATaxRules

_OPT = 'ZZA250117C00020000.US'


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


def _us_book(seed=20261029, n=420):
    """A made-up year of US trading in four symbols across two taxable
    accounts, an IRA (sheltered) and a spouse's account (affiliated):
    rebuys inside the window, partial sales, shorts, same-day fills,
    written calls (some assigned), a 2-for-1 split and a rename."""
    r = random.Random(seed)
    days = _bdays(date(2024, 1, 2), date(2025, 1, 31))
    syms = ['ZZA.US', 'ZZB.US', 'ZZC.US', 'ZZD.US']
    frac = {'ZZD.US'}
    accts = ['margin'] * 5 + ['cash'] * 2 + ['ira', 'spouse']
    kind = {'margin': 'tax', 'cash': 'tax', 'ira': 'shel', 'spouse': 'aff'}
    times = ('09:30:00', '10:00:00', '10:00:00', '13:15:00', '')
    px = {s: 20.0 + 7 * i for i, s in enumerate(syms)}
    pos, rows = {}, []
    for _ in range(n):
        d, a, s = r.choice(days), r.choice(accts), r.choice(syms)
        px[s] = max(2.0, px[s] * r.uniform(0.85, 1.15))
        t = r.choice(times)
        if s == 'ZZA.US' and r.random() < 0.06 and kind[a] == 'tax':
            rows.append((a, _row(a, d, _OPT, -1,
                                 round(r.uniform(0.5, 3), 2), t, 100)))
            if r.random() < 0.5:
                d2 = r.choice([x for x in days if x >= d])
                rows.append((a, TaxTransaction(
                    action='ASSIGN', date=d2.isoformat(),
                    date_settle=d2.isoformat(), time='16:00:00',
                    symbol=_OPT, quantity=1.0, price=0.0, net_amount=0.0,
                    currency='USD', account=a)))
                rows.append((a, _row(a, d2, 'ZZA.US', -100, 20.0,
                                     '16:00:00')))
                pos[(a, s)] = pos.get((a, s), 0.0) - 100
            continue
        held = pos.get((a, s), 0.0)
        q = (round(r.uniform(0.1, 30), 4) if s in frac
             else float(r.randint(1, 120)))
        if r.random() < 0.5:
            if kind[a] == 'shel':
                q = -min(held, q) if held > 0 else q
            else:
                q = -min(held, q) if (held > 0 and r.random() < 0.85) else -q
        p = round(px[s], 2)
        rows.append((a, _row(a, d, s, q, p, t)))
        pos[(a, s)] = held + q
        if r.random() < 0.12:            # a second fill
            q2 = (0.5 if s in frac else float(r.randint(1, 20))) * (
                1 if q > 0 else -1)
            if kind[a] == 'shel' and q2 < 0 and pos[(a, s)] + q2 < 0:
                q2 = 0
            if q2:
                rows.append((a, _row(a, d, s, q2, round(p * 1.01, 2), t)))
                pos[(a, s)] += q2
    for a in sorted(kind):
        rows.append((a, TaxTransaction(
            action='SPLIT', date='2024-07-02', date_settle='2024-07-02',
            time='00:00:01', symbol='ZZB.US', quantity=2.0,
            currency='USD', account=a)))
    books = {'tax': [], 'shel': [], 'aff': []}
    for a in sorted(kind):
        books[kind[a]].append(TaxTransaction(
            action='SPLIT', date='2024-09-03', date_settle='2024-09-03',
            time='00:00:01', symbol='ZZC.US', quantity=1.0,
            symbol_new='ZZCN.US', currency='USD', account=a))
    for a, t in rows:
        if t.symbol == 'ZZC.US' and t.date > '2024-09-03':
            t = TaxTransaction(**{**t.to_dict(), 'symbol': 'ZZCN.US',
                                  'id': None})
        books[kind[a]].append(t)
    return books


def _us(books, blended=False):
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return USATaxRules().compute_gains(
            books['tax'], sheltered_transactions=books['shel'],
            affiliated_transactions=books['aff'],
            per_account_basis=blended)


def _r(x):
    return round(float(x), 6) if x is not None else None


def _us_fingerprint(res):
    """Every sale's figures and replacements, every wash record and the
    closing lots, rounded to 6 decimals (Python versions differ in the
    last bits of a float sum)."""
    sales = [[e['date'], e['symbol'], e['account'], e['direction'],
              _r(e['qty']), _r(e['raw_gain']), _r(e['gain']),
              _r(e['cost']), _r(e['proceeds']), _r(e['disallowed_amount']),
              _r(e['permanently_disallowed']), e['term'],
              e.get('acquired_date'), list(e.get('replacement_lot_ids')
                                           or []),
              [(w['tx_id'], _r(w['match_qty']))
               for w in e.get('wash_replacements') or []]]
             for e in res['transactions'] if 'proceeds' in e]
    washes = [[w['loss_tx_id'], w['direction'], _r(w['qty']),
               _r(w['disallowed_amount']), _r(w['permanently_disallowed']),
               list(w['replacement_lot_ids'])] for w in res['wash_sales']]
    lots = sorted([i['symbol'], _r(i.get('qty')), _r(i.get('total_cost')),
                   _r(i.get('deferred_wash')), i.get('holding_start_date')]
                  for i in res.get('inventory', []))
    return sales, washes, lots


def _digest(fp):
    return hashlib.sha256(json.dumps(fp, sort_keys=True)
                          .encode()).hexdigest()


# Captured from the v0.37.0 engine (1a3fb24), before the change: 291
# washes symbol-wide (115 on shorts, 74 with a permanent part, 12 sales
# whose basis a later loss raised: US-WASH-22), 244 per account.
_US_PINNED = {
    'plain': {'digest': '895ba0cafc152143798adc04f209438565f2322c'
                        '0e05d7bdc484ea7ac7942642',
              'washes': 291, 'disallowed': 46767.56552},
    'blended': {'digest': 'ae55a53db06706be4bb63f87a96eb0881290028'
                          '224a0aa9c0884143babe19503',
                'washes': 244, 'disallowed': 34448.497974},
}


class TestUsWashScanPinned(unittest.TestCase):
    """The indexed replacement scans give the walk's results, figure for
    figure, symbol-wide and per account (TJ-0238)."""

    @rule("US-WASH-01", "US-WASH-22")
    def test_generated_book_unchanged(self):
        books = _us_book()
        for mode in ('plain', 'blended'):
            with self.subTest(mode=mode):
                res = _us(books, blended=(mode == 'blended'))
                fp = _us_fingerprint(res)
                pin = _US_PINNED[mode]
                self.assertEqual(len(fp[1]), pin['washes'])
                self.assertAlmostEqual(
                    sum(w[3] for w in fp[1]), pin['disallowed'], places=4)
                self.assertEqual(_digest(fp), pin['digest'])


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
    each date once."""

    @rule("US-WASH-01")
    def test_date_parses_grow_with_rows_not_losses_x_rows(self):
        rows = _one_symbol_us(600)             # 1,200 rows, 300 losses
        parses = {'n': 0}

        class _Counting(datetime):
            @classmethod
            def strptime(cls, s, fmt):
                parses['n'] += 1
                return datetime.strptime(s, fmt)
        core._iso_ordinal.cache_clear()
        with mock.patch.object(core, 'datetime', _Counting):
            res = _us({'tax': rows, 'shel': [], 'aff': []})
        core._iso_ordinal.cache_clear()
        losses = [e for e in res['transactions'] if e['raw_gain'] < 0]
        self.assertGreater(len(losses), 300)
        self.assertGreater(len(res['wash_sales']), 250)
        self.assertLess(parses['n'], 10 * len(rows))


class _CountingEpoch(float):
    """An epoch that counts its comparisons (the radar's window tests)."""
    n = 0

    def __lt__(self, o):
        _CountingEpoch.n += 1
        return float.__lt__(self, o)

    def __le__(self, o):
        _CountingEpoch.n += 1
        return float.__le__(self, o)

    def __gt__(self, o):
        _CountingEpoch.n += 1
        return float.__gt__(self, o)

    def __ge__(self, o):
        _CountingEpoch.n += 1
        return float.__ge__(self, o)


def _radar_row(d, qty, px, rid):
    return {"action": "BUYSELL", "date": d, "date_settle": d,
            "time": "10:00:00", "symbol": "ZZQ.TO", "quantity": qty,
            "price": px, "net_amount": round(-qty * px, 2),
            "currency": "CAD", "account": "margin", "commission": 0.0,
            "id": rid}


def _radar_json(rows, as_of):
    from taxjson.bin import taxjson_wash_radar as R
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "margin_base.json"
        tp.write_text(json.dumps({"transactions": rows}))
        argv = ["taxjson-wash-radar", "--country", "canada", "--date",
                as_of, "--taxable", str(tp), "--all", "--json"]
        out, old = io.StringIO(), sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                R.main()
        finally:
            sys.argv = old
    return out.getvalue()


def _radar_book(pairs, span_days):
    days = _bdays(date(2022, 1, 3), date(2022, 1, 3)
                  + timedelta(days=span_days))
    rows = []
    for i in range(pairs):
        d = days[i * len(days) // pairs].isoformat()
        p = 20.0 + (i % 97) * 0.01
        q = round(p + (1 if i % 2 == 0 else -1), 2)
        rows.append(_radar_row(d, 10.0, round(p, 2), f"b{i}"))
        rows.append(_radar_row(d, -10.0, q, f"s{i}"))
    return rows


class TestRadarCapacityWindow(unittest.TestCase):
    """TJ-0239: the radar's per-loss capacity test reads the loss's
    window, not every acquisition of the class."""

    @rule("CA-SL-08")
    def test_window_comparisons_grow_with_rows_not_losses_x_rows(self):
        from taxjson.bin import taxjson_wash_radar as R
        rows = _radar_book(600, 1100)         # 1,200 rows over 3 years
        real = R.date_to_epoch
        _CountingEpoch.n = 0
        with mock.patch.object(R, 'date_to_epoch',
                               lambda s: _CountingEpoch(real(s))):
            doc = json.loads(_radar_json(rows, "2025-06-30"))
        self.assertIsInstance(doc, dict)
        self.assertLess(_CountingEpoch.n, 40 * len(rows))

    @rule("CA-SL-08")
    def test_dense_book_report_unchanged(self):
        """A dense one-symbol book: the radar's document as v0.37.0
        wrote it (the run date aside)."""
        rows = _radar_book(150, 60)
        doc = json.loads(_radar_json(rows, "2022-02-20"))
        doc.pop('generated_at', None)
        # The keys buy-check's per-unit denial added since (a5c3bb4,
        # TJ-0559), a settled VIOLATION's forward view (TJ-0775) and the
        # registered units bought in the window and the last such buy
        # (TJ-1039, TJ-1177): every figure v0.37.0 wrote is unchanged.
        added = ('recent_buy', 'recent_loss_per_unit',
                 'recent_loss_per_unit_qty', 'forward',
                 'registered_window_qty', 'registered_window_last_add')

        def _v037(o):
            if isinstance(o, dict):
                return {k: _v037(v) for k, v in o.items() if k not in added}
            return [_v037(x) for x in o] if isinstance(o, list) else o
        doc = _v037(doc)
        self.assertEqual(
            hashlib.sha256(json.dumps(doc, sort_keys=True).encode())
            .hexdigest(), '6be9608d23f528ebbba6ec9bf93db8debff93a14'
                          'bffcec4272a8a7263699cc5c')


class TestStripJsonComments(unittest.TestCase):
    """TJ-0240: the fast paths return what the pass returns."""

    def _old(self, text):
        return core._JSON_COMMENT_RE.sub(
            lambda m: '' if m.group(1) is not None else m.group(0), text)

    def test_text_without_comments_is_returned_as_is(self):
        for text in ('{"a": 1}', '{"a": "x#y", "b#": ["#", "\\"#"]}',
                     '[1, 2]\n', '{"k": "a\\\\"}'):
            self.assertIs(core.strip_json_comments(text), text)
            self.assertEqual(text, self._old(text))

    def test_comments_are_stripped_as_before(self):
        cases = ['# head\n{"a": 1}', '{"a": 1} # trailing "x',
                 '{"a": "#in"}  # out\n', '{"a": "unterminated # x\n}',
                 '﻿{"a": "#"}', '{"a": NaN} # x', '#',
                 '{"a": "x"\n# "q\n, "b": 2}']
        for text in cases:
            self.assertEqual(core.strip_json_comments(text),
                             self._old(text), text)

    def test_random_texts_match_the_pass(self):
        r = random.Random(1029)
        alpha = ['"', '#', '\\', '\n', 'a', ' ', '{', '}', '[', ']', ':',
                 ',', '1', 'NaN', '"#"', '\\"', 'true']
        for _ in range(3000):
            text = ''.join(r.choice(alpha) for _ in range(r.randint(0, 24)))
            self.assertEqual(core.strip_json_comments(text),
                             self._old(text), repr(text))

    def test_large_book_reads_without_the_pass(self):
        """A work/ book with no comment never runs the regex pass."""
        rows = [{"action": "BUYSELL", "date": "2024-01-02", "id": f"r{i}",
                 "description": "ZZQ #1" if i % 7 == 0 else "ZZQ"}
                for i in range(2000)]
        for text in (json.dumps({"transactions": rows}),
                     json.dumps({"transactions": rows[1:7]})):
            with mock.patch.object(core, '_JSON_COMMENT_RE') as rx:
                out = core.strip_json_comments(text)
            self.assertIs(out, text)
            rx.sub.assert_not_called()


if __name__ == '__main__':
    unittest.main()
