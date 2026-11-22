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
import hashlib
import io
import json
import random
import unittest
from datetime import date, timedelta

from tax_rules import rule
from taxjson.rules import ids as RID

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction

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
# Re-captured for step 6e (the engine's money in Decimal): every figure the
# same; only signed zeros moved (a residue under 1e-12 the fingerprint's
# round() writes -0.0 in one mode and 0.0 in the other; the engine's
# residues are unsigned zeros since TJ-3098).
# Re-captured for the owner's ruling on backlog TJ-2708 (US-WASH-22 reaches
# short sales, 2026-11-11): another account's short sale covered before a
# cover's loss replaces it, in the order made, so such a loss matches it
# before a later short still open. Symbol-wide 291 washes are 286 (115 on
# shorts are 110; 2 covers whose proceeds a later loss cut), per account
# 244 are 250 (84 on shorts are 90; 9 such covers); the 12 and the 1 sales
# whose basis a later loss raised are as they were.
# Re-captured for backlog TJ-3428 (a loss a wash add makes on the earlier
# sale or cover is tested itself): 11 such rows' losses are disallowed
# symbol-wide (352.08 of them; 286 wash records are 297) and 4 per account
# (81.91; 250 are 254), each deferred onto a replacement of its own 61
# days; the rest of the move in the totals is those deferrals coming back
# on later rows.
# Re-captured for backlog TJ-3462 (the rows a sale's matches turned into
# losses are tested once the whole sale is booked, not lot by lot):
# symbol-wide 9 such rows (347.43; 297 wash records are 295); per account
# the same 4 and the same totals, two rows' replacement lists in another
# order.
_US_PINNED = {
    'plain': {'digest': '1f9657f0d8272fc101f0d88ea27a914c93'
                        '0c63b661255c604499ba6b924081ff',
              'washes': 295, 'disallowed': 47143.44169},
    'blended': {'digest': 'a55245ecc3598331d765ffe5cfd60fff'
                          '71f43c9417e080dc27254503ca870a97',
                'washes': 254, 'disallowed': 34650.131409},
}


class TestUsWashScanPinned(unittest.TestCase):
    """The indexed replacement scans give the walk's results, figure for
    figure, symbol-wide and per account (TJ-0238).

    Formerly: test_fix_1029_perf.TestUsWashScanPinned"""

    @rule(RID.US_WASH_01, RID.US_WASH_22)
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


if __name__ == '__main__':
    unittest.main()
