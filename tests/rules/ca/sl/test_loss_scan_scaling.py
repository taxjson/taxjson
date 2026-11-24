"""Backlog group `perf`: the superficial-loss scan's run time.

TJ-0105 / TJ-0060: for every loss sale, the Canada solver walked every
row of the book (the trigger and call scans, the day-30 balance walks,
the window listing) and every denial it had made, so a run cost loss
sales x rows: 8,000 rows of one symbol took minutes, 50,000 rows half an
hour. The scans now read indexes (each identity class's rows by date
window, running sums of the day-30 balances, the denials by loss id)
that hand them the same rows in the same order, so no result changes.

The pin test holds the v0.35.0 engine's results on a generated book of
many loss sales (taxable, registered and affiliated accounts, calls,
a split, a rename, fractional units, same-day fills of one sale),
captured before the change. The scaling test counts the per-row calls
the old scans made per loss sale.

All data is synthetic.

Formerly: test_fix_backlog_perf
"""
import contextlib
import hashlib
import io
import json
import random
import unittest
from datetime import date, timedelta
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as R

import _engine
from taxjson.engine.rows import not_a_purchase
from taxjson.engine.ca.indexes import _WashAdjustIndex
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.corporate_timeline import SplitTimeline

_CALLS = {'ZZA.TO': ('ZZA250117C00020000.TO', 'ZZA241115C00018000.TO'),
          'ZZC.TO': ('ZZC250321C00030000.TO',)}


def _bdays(lo, hi):
    out, d = [], lo
    while d <= hi:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _settle(d, lag):
    s = d
    while lag:
        s += timedelta(days=1)
        if s.weekday() < 5:
            lag -= 1
    return s


def _row(a, d, s, sym, q, p, t='10:00:00', mult=1):
    return TaxTransaction(
        action='BUYSELL', date=d.isoformat(), date_settle=s.isoformat(),
        time=t, symbol=sym, quantity=float(q), price=p,
        net_amount=round(abs(q * p * mult), 2) + (1.0 if q > 0 else -1.0),
        commission=1.0, currency='CAD', account=a)


def _book(seed=20261025, n=420):
    """A made-up year of trading in four symbols across a margin and a
    cash account (taxable), a TFSA (registered) and a spouse's account
    (affiliated): rebuys inside the window, partial sales, shorts,
    same-day fills, calls on two symbols, a 2-for-1 split and a rename."""
    r = random.Random(seed)
    days = _bdays(date(2024, 1, 2), date(2025, 1, 31))
    syms = ['ZZA.TO', 'ZZB.TO', 'ZZC.TO', 'ZZD.TO']
    frac = {'ZZD.TO'}                      # fractional units
    accts = ['margin'] * 5 + ['cash'] * 2 + ['tfsa', 'spouse']
    book = {'margin': 'tax', 'cash': 'tax', 'tfsa': 'shel', 'spouse': 'aff'}
    times = ('09:30:00', '10:00:00', '10:00:00', '13:15:00')
    px = {s: 20.0 + 7 * i for i, s in enumerate(syms)}
    pos, rows = {}, []
    for _ in range(n):
        d, a, s = r.choice(days), r.choice(accts), r.choice(syms)
        px[s] = max(2.0, px[s] * r.uniform(0.88, 1.12))
        lag = r.choice((0, 1, 1, 2))
        if s in _CALLS and r.random() < 0.1:
            c = r.choice(_CALLS[s])
            held = pos.get((a, c), 0.0)
            q = float(r.randint(1, 3))
            if held > 0 and r.random() < 0.5:
                q = -min(held, q)
            rows.append((a, _row(a, d, _settle(d, min(lag, 1)), c, q,
                                 round(r.uniform(0.5, 4), 2),
                                 r.choice(times), 100)))
            pos[(a, c)] = held + q
            continue
        held = pos.get((a, s), 0.0)
        q = (round(r.uniform(0.1, 30), 4) if s in frac
             else float(r.randint(1, 120)))
        if held > 0 and r.random() < 0.55:
            q = -min(held, q) if r.random() < 0.85 else -q
        p = round(px[s], 2)
        t = r.choice(times)
        rows.append((a, _row(a, d, _settle(d, lag), s, q, p, t)))
        pos[(a, s)] = held + q
        if q < 0 and r.random() < 0.15:          # a second fill
            q2 = -0.5 if s in frac else -float(r.randint(1, 20))
            rows.append((a, _row(a, d, _settle(d, lag), s, q2,
                                 round(p * 1.01, 2), t)))
            pos[(a, s)] += q2
    # ZZB.TO splits 2-for-1 on 2024-07-02; ZZC.TO is renamed ZZCN.TO on
    # 2024-09-03 (its later rows trade as ZZCN.TO).
    for a in sorted(book):
        rows.append((a, TaxTransaction(
            action='SPLIT', date='2024-07-02', date_settle='2024-07-02',
            time='00:00:01', symbol='ZZB.TO', quantity=2.0,
            currency='CAD', account=a)))
        rows.append((a, TaxTransaction(
            action='SPLIT', date='2024-09-03', date_settle='2024-09-03',
            time='00:00:01', symbol='ZZC.TO', quantity=1.0,
            symbol_new='ZZCN.TO', currency='CAD', account=a)))
    books = {'tax': [], 'shel': [], 'aff': []}
    for a, t in rows:
        if t.symbol == 'ZZC.TO' and t.action != 'SPLIT' \
                and t.date >= '2024-08-28':
            # No trade inside the rename's settle lag (the engine
            # refuses one), and the renamed shares trade as ZZCN.TO.
            if t.date <= '2024-09-03':
                continue
            t = TaxTransaction(**{**t.to_dict(), 'symbol': 'ZZCN.TO',
                                  'id': None})
        books[book[a]].append(t)
    return books


def _ca(books):
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return CanadaTaxRules().compute_gains(
            books['tax'], sheltered_transactions=books['shel'],
            affiliated_transactions=books['aff'])


def _r(x):
    return round(float(x), 6) if x is not None else None


def _fingerprint(res):
    """Every sale's figures, every denial's ACB additions and the
    closing pools, rounded to 6 decimals (Python versions differ in the
    last bits of a float sum)."""
    sales = []
    for e in res['transactions']:
        if 'proceeds' not in e:
            continue
        w = e.get('wash_window') or {}
        sales.append([
            e['date'], e['symbol'], e['account'], _r(e['qty']),
            _r(e['raw_gain']), _r(e['gain']), _r(e['cost']),
            _r(e['proceeds']), _r(e['disallowed_amount']),
            _r(e['permanently_disallowed']),
            sorted(e.get('replacement_lot_ids') or []),
            _r(w.get('bal_at_end')), _r(w.get('disallowed_qty')),
            [(x['tx_id'], x['role']) for x in w.get('transactions', [])],
            _r((e.get('wash_trigger') or {}).get('adjust_amount'))])
    washes = [[w['loss_tx_id'], _r(w['disallowed_amount']),
               _r(w['disallowed_qty']),
               [(a['symbol'], a['date'], _r(a['amount']), a['after'])
                for a in w['adjusts']]]
              for w in res['wash_sales']]
    pools = sorted([i['symbol'], _r(i.get('qty')), _r(i.get('total_cost'))]
                   for i in res.get('inventory', []))
    return sales, washes, pools


def _digest(res):
    return hashlib.sha256(json.dumps(_fingerprint(res), sort_keys=True)
                          .encode()).hexdigest()


# Captured from the v0.35.0 engine (4ddfb03), before the change: 178
# sales, 91 of them denied in part or whole (two backed by calls), the
# replacements in all four accounts. Re-captured when the window
# listing moved to settlement dates (backlog TJ-0258): only the window
# rows' (tx id, role) lists changed, every figure, denial and pool the
# same. Re-captured for backlog TJ-0725: the 2024-10-15 ZZA.TO loss's
# window ends 2024-11-15, the day its replacement call ZZA241115C
# expires, so the call is no longer held at day 30 and the denial goes
# to the share replacements instead; the later ZZA.TO figures follow
# from their changed ACB. Re-captured for backlog TJ-2711 and TJ-2720,
# the same 91 denials: the taxable pool of ZZC.TO and ZZD.TO runs short
# in places (the TFSA and the spouse hold the rest), so after some
# triggers the taxable position is sold out before day 30 and a later
# purchase opens the one held then; those losses' additions now go
# after that purchase (the 2024-05-08 ZZC.TO loss's first addition to
# the 05-28 purchase, the 2024-08-22 ZZD.TO loss's to the 09-03 one,
# the three late-December ZZD.TO losses' to the 01-23 one) and the
# sales between carry less recovered deferral, the later ones more
# (TJ-2711). Two sales in fills of different prices backed by two
# holders (2024-03-04 ZZB.TO margin, 2024-11-05 ZZA.TO cash) split the
# holders' units pro rata over the fills instead of fill by fill: the
# same denied and permanent units per sale, the permanent amount moved
# with the fills' prices (TJ-2720). Everything else follows from the
# changed ACBs. Re-captured for backlog TJ-2756 and TJ-2758: a sale in
# fills is allocated over its triggers once and each fill takes its
# units' share of every trigger's take, so the sales in fills backed by
# two taxable triggers (2024-03-04 ZZB.TO margin, 2024-09 and 2025-01-02
# ZZA.TO, 2024-11-11 ZZD.TO cash) split each trigger's take over the
# fills instead of filling the first trigger from the first fill; the
# sale's own denial and the holders' units are the same, the ACB between
# the two triggers' dates moves a little and the sales there with it.
# The placements (TJ-2711) are unchanged: the pools sold short between a
# trigger and day 30 still pass the addition to the purchase that brings
# them long. Re-captured for backlog TJ-2761 and TJ-2762: a trigger whose
# position is sold out before day 30 is no replacement, so the ZZD.TO
# losses whose taxable rebuy was sold short again before day 30 (the
# 2024-08-22 loss; the 12-25, 12-30 and 01-01 losses) now go to the
# TFSA's and the spouse's purchases, earlier than the taxable one held
# at day 30 in CA-SL-10's order: lost for good where the first round
# deferred them onto that purchase; the 2024-05-08 ZZC.TO loss's two
# additions are one, on the 05-28 purchase that is its replacement. The
# later ZZD.TO figures follow from their changed ACB. Re-captured for
# backlog TJ-2767: a registered or affiliated trigger's position is
# judged sold out from its own account's balance, not from the taxable
# pool's state its rows inherit, so four ZZD.TO losses now name the
# TFSA's earlier purchase as the trigger where the later one stood; the
# amounts (all lost for good, one holder) are the same, as is every
# figure. Re-captured for step 6e (the engine's money in Decimal): one
# denial is exactly 128.9963125, which this fingerprint's round(x, 6) now
# writes 128.996312 (its float lies below the half; float arithmetic
# landed above it), and signed zeros of the same round() (the engine's
# residues are unsigned zeros since TJ-3098); every figure at the gains
# file's 4 places is the same.
# Re-captured for TJ-2735 (an option series' written contracts are the
# writing account's own position, CA-OPT-12): the cash account's
# 2024-05-28 purchase of 2 ZZA250117C00020000.TO contracts, booked as a
# buy-back of margin's written ones (a loss of 179.67 in cash), is cash's
# own bought call; held at day 30 it replaces margin's ZZA.TO losses of
# 2024-05-23 and 2024-06-13 (CA-SL-05), now denied in full onto the
# call's ACB (CA-SL-09; +215.07 and +153.29 disallowed, and the 05-23
# denial's 675.94 is no longer permanent: it was the affiliated
# account's purchase that backed it), and margin's 2024-12-18 purchase
# covers its own 2 remaining contracts (a loss of 69.33) and buys 1 long
# with the rest. 91 denied sales as before.
# Re-captured for backlog TJ-4985 (a rename's destination is part of its
# row's id): the id of the book's one rename row, named in two losses'
# window rows, changed; every figure, denial and pool is the same.
_PINNED = {
    'digest': '246c5603cc331225d776a443a9186d5e'
              '3962526a61fda065d1a7c7d82a7c4b4d',
    'denied_sales': 91,
    'total_disallowed': 29180.315851,
    'total_permanent': 14732.53997,
}


class TestSuperficialLossScanPinned(unittest.TestCase):
    """The indexed scans give the full walk's results, figure for figure
    (TJ-0105, TJ-0060)."""

    @rule(R.CA_SL_08, R.CA_SL_09)
    def test_many_loss_sales_unchanged(self):
        res = _ca(_book())
        sales, washes, _pools = _fingerprint(res)
        denied = [s for s in sales if s[8] > 0.001]
        self.assertEqual(len(denied), _PINNED['denied_sales'])
        self.assertAlmostEqual(sum(s[8] for s in denied),
                               _PINNED['total_disallowed'], places=4)
        self.assertAlmostEqual(sum(s[9] for s in denied),
                               _PINNED['total_permanent'], places=4)
        self.assertEqual(len(washes), _PINNED['denied_sales'])
        self.assertEqual(_digest(res), _PINNED['digest'])


class TestSuperficialLossScanScales(unittest.TestCase):
    """TJ-0060: one symbol, many loss sales. The old scans classed and
    tested every row of the book for every loss sale (3 x sales x rows
    class lookups); the indexes class each row a few times per pass."""

    def test_class_lookups_grow_with_rows_not_sales_x_rows(self):
        days = _bdays(date(2024, 1, 2), date(2024, 12, 20))
        rows = []
        for i in range(600):                 # 1,200 rows, 300 losses
            d = days[i * len(days) // 600]
            p = 20.0 + (i % 97) * 0.01
            s = _settle(d, 1)
            rows.append(_row('margin', d, s, 'ZZQ.TO', 10, round(p, 2)))
            rows.append(_row('margin', d, s, 'ZZQ.TO', -10,
                             round(p + (1 if i % 2 == 0 else -1), 2)))
        calls = {'cls': 0, 'buy': 0}
        real_cls, real_buy = SplitTimeline.class_of_row, not_a_purchase

        def cls(self, t):
            calls['cls'] += 1
            return real_cls(self, t)

        def buy(t):
            calls['buy'] += 1
            return real_buy(t)
        with mock.patch.object(SplitTimeline, 'class_of_row', cls), \
                _engine.patch_engine('not_a_purchase', buy):
            res = _ca({'tax': rows, 'shel': [], 'aff': []})
        losses = [e for e in res['transactions']
                  if 'proceeds' in e and e['raw_gain'] < 0]
        self.assertEqual(len(losses), 300)
        self.assertLess(calls['cls'], 10 * len(rows))
        self.assertGreater(calls['buy'], 0)
        self.assertLess(calls['buy'], 10 * len(rows))


class TestWashAdjustIndex(unittest.TestCase):
    """`of(x)` is exactly the ADJUST rows whose id starts WASH_<x>__."""

    def test_prefix_matches_startswith(self):
        def adj(i):
            return TaxTransaction(action='ADJUST', date='2024-01-02', id=i)
        rows = [adj('WASH_a__b'), adj('WASH_a__b__c'), adj('WASH_a_b__c'),
                adj('WASH___x'), adj('WASH_a___d'), adj('OTHER_a__b'),
                TaxTransaction(action='DISALLOW', date='2024-01-02',
                               id='WASH_a__z')]
        idx = _WashAdjustIndex(rows)
        for x in ('a', 'a__b', 'a_b', '', 'a_', '_x', 'zz'):
            want = [v for v in rows if v.action == 'ADJUST'
                    and v.id.startswith(f"WASH_{x}__")]
            self.assertEqual(idx.of(x), want, x)


if __name__ == '__main__':
    unittest.main()
