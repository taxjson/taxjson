"""CA-SL-08 per disposition (owner decision, A2-0167).

CRA's formula is applied to EACH disposition on its own: denied units =
least of (units sold, units acquired in the window, units held at day
30). The same held unit may back the denials of two sales. A sale split
into same-second fills of one order is ONE disposition, so its fills
still share. The US engine (§1091: each replacement share matched once)
is unchanged.

All data is synthetic.
"""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from tax_rules import rule
from tax_rules.dual import gains_both, tx


def _row(d, q, px, acct='margin', sym='XYZ.TO', t='10:00:00'):
    m = 100 if len(sym) > 12 else 1
    return TaxTransaction(action='BUYSELL', date=d, date_settle=d, time=t,
                          symbol=sym, quantity=float(q), price=float(px),
                          net_amount=round(abs(q * px * m), 2),
                          currency='CAD', account=acct)


def _ca(tax, shel=None, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return CanadaTaxRules().compute_gains(
            tax, sheltered_transactions=shel, **kw)


def _losses(res, sym='XYZ.TO'):
    """(date, denied units, denied, permanent) per loss sale."""
    out = []
    for e in res['transactions']:
        if 'proceeds' not in e or e['symbol'] != sym:
            continue
        raw = float(e['raw_gain'])
        if raw >= 0:
            continue
        units = e['disallowed_amount'] / (-raw / e['qty'])
        out.append((e['date'], round(units, 6),
                    round(e['disallowed_amount'], 2),
                    round(e.get('permanently_disallowed', 0.0), 2)))
    return out


class TestCaPerDisposition(unittest.TestCase):

    @rule("CA-SL-08")
    def test_same_account_rebuy_then_trim(self):
        # Sell 300 at a loss, rebuy 20, sell 10 of them at a loss: 10
        # are held at day 30 of BOTH windows, so each sale has 10 units
        # denied (min(300, 20, 10) and min(10, 20, 10)).
        rows = [_row('2025-06-02', 300, 100), _row('2026-03-04', -300, 58),
                _row('2026-03-05', 20, 60), _row('2026-03-09', -10, 56)]
        got = _losses(_ca(rows))
        self.assertEqual([(d, u) for d, u, _a, _p in got],
                         [('2026-03-04', 10.0), ('2026-03-09', 10.0)])
        self.assertEqual(got[0][2], 420.0)

    @rule("CA-SL-08", "CA-SL-09")
    def test_registered_holder_backs_both_sales(self):
        # The TFSA buys 50 in the window and sells 20: 30 held at day
        # 30. Each taxable sale of 100: min(100, 50, 30) = 30 units,
        # denied for good (the replacement is registered).
        tax = [_row('2026-01-05', 200, 50), _row('2026-03-05', -100, 40),
               _row('2026-03-06', -100, 40)]
        tfsa = [_row('2026-03-02', 50, 41, 'tfsa'),
                _row('2026-03-03', -20, 41, 'tfsa')]
        res = _ca(tax, tfsa)
        self.assertEqual(_losses(res),
                         [('2026-03-05', 30.0, 300.0, 300.0),
                          ('2026-03-06', 30.0, 300.0, 300.0)])
        self.assertAlmostEqual(res['summary']['total_disallowed'], 600.0)

    @rule("CA-SL-08", "CA-SL-03")
    def test_s47_pool_across_taxable_accounts(self):
        # Account B buys 50 in the window and sells 20 (itself at a loss
        # against the pooled ACB: min(20, 50, 30)); the taxpayer's one
        # pool holds 30 at day 30. Account A's two sales of 100: 30
        # units each.
        tax = [_row('2026-01-05', 200, 50, 'A'), _row('2026-03-02', 50, 41, 'B'),
               _row('2026-03-03', -20, 41, 'B'), _row('2026-03-05', -100, 40, 'A'),
               _row('2026-03-06', -100, 40, 'A')]
        got = _losses(_ca(tax))
        self.assertEqual([(d, u) for d, u, _a, _p in got],
                         [('2026-03-03', 20.0), ('2026-03-05', 30.0),
                          ('2026-03-06', 30.0)])

    @rule("CA-SL-08", "CA-SL-05")
    def test_one_held_call_backs_two_sales(self):
        # One call (100 shares) bought in the window and held: each of
        # the two 100-share loss sales has 100 units denied.
        C = 'XYZ261218C00020000.TO'
        base = [_row('2025-01-06', 200, 50), _row('2025-03-03', -100, 40),
                _row('2025-03-05', -100, 40)]
        rep = [_row('2025-03-06', 1, 3, 'rrsp', C)]
        res = _ca(base, rep)
        self.assertEqual(_losses(res),
                         [('2025-03-03', 100.0, 1000.0, 1000.0),
                          ('2025-03-05', 100.0, 1000.0, 1000.0)])

    @rule("CA-SL-08")
    def test_fills_of_one_sale_share(self):
        # A sale split into fills — the same day, one account, no buy in
        # between, whatever the clock says (partial fills are stamped
        # minutes apart, or all at midnight) — is one sale:
        # min(200, 100, 100) = 100 units in total, shared pro rata.
        for t2 in ('10:00:00', '10:07:00', '00:00:00'):
            rows = [_row('2025-01-02', 200, 50),
                    _row('2025-03-03', -100, 40, t=t2[:2] == '00' and t2
                         or '10:00:00'),
                    _row('2025-03-03', -100, 40, t=t2),
                    _row('2025-03-10', 100, 40)]
            got = _losses(_ca(rows))
            self.assertEqual([u for _d, u, _a, _p in got], [50.0, 50.0], t2)

    @rule("CA-SL-08")
    def test_sales_on_two_days_are_two_dispositions(self):
        rows = [_row('2025-01-02', 200, 50), _row('2025-03-03', -100, 40),
                _row('2025-03-04', -100, 40), _row('2025-03-10', 100, 40)]
        got = _losses(_ca(rows))
        self.assertEqual([u for _d, u, _a, _p in got], [100.0, 100.0])
        # A buy between two same-day sales splits them too.
        rows = [_row('2025-01-02', 200, 50),
                _row('2025-03-03', -100, 40, t='10:00:00'),
                _row('2025-03-03', 10, 40, t='10:05:00'),
                _row('2025-03-03', -100, 40, t='10:10:00')]
        got = _losses(_ca(rows))
        self.assertEqual([u for _d, u, _a, _p in got], [10.0, 10.0])

    @rule("CA-SL-08")
    def test_fills_never_exceed_the_units_held(self):
        # One sale in two fills: 50 acquired in the window, 30 held at
        # day 30 -> min(220, 50, 30) = 30 units across both fills, for a
        # taxable and for a registered replacement alike.
        for acct in ('margin', 'tfsa'):
            tax = [_row('2025-01-02', 200, 50),
                   _row('2025-03-03', -100, 40, t='10:00:00'),
                   _row('2025-03-03', -120, 40, t='10:05:00')]
            rep = [_row('2025-03-01', 50, 41, acct)]
            if acct == 'tfsa':
                rep.append(_row('2025-03-02', -20, 41, acct))
                tax[2] = _row('2025-03-03', -100, 40, t='10:05:00')
                res = _ca(tax, rep)
            else:
                res = _ca(sorted(tax + rep, key=lambda r: (r.date, r.time)))
            got = _losses(res)
            self.assertAlmostEqual(sum(u for _d, u, _a, _p in got), 30.0,
                                   places=6, msg=acct)

    @rule("CA-SL-08", "CA-SL-09")
    def test_double_backing_is_recovered_on_the_final_sale(self):
        # Both deferrals land on the one replacement lot, and a full
        # exit recovers them: the book's total gain equals the no-denial
        # total (conservation).
        rows = [_row('2025-06-02', 300, 100), _row('2026-03-04', -300, 58),
                _row('2026-03-05', 20, 60), _row('2026-03-09', -10, 56),
                _row('2026-06-01', -10, 70)]
        res = _ca(rows)
        base = _ca(rows, detect_wash_sales=False)
        self.assertGreater(res['summary']['total_disallowed'], 600.0)
        self.assertAlmostEqual(res['summary']['total_gain'],
                               base['summary']['total_gain'], places=2)


def _compact_book(seed):
    """Every trade after the opening lot falls inside ten days, so every
    loss's window covers every trade and ends after the last one."""
    import random
    from datetime import date, timedelta
    R = random.Random(seed)
    rows = [('2025-06-02', 'A', 300, 100.0)]
    pos = {'A': 300, 'B': 0, 'S': 0, 'F': 0}
    for dd in sorted(R.randint(0, 9) for _ in range(R.randint(3, 9))):
        d = (date(2026, 3, 2) + timedelta(days=dd)).isoformat()
        a = R.choice('AABSF')
        if pos[a] > 0 and R.random() < 0.5:
            q = -R.choice([pos[a], max(1, pos[a] // 2), min(pos[a], 10)])
        else:
            q = R.choice([10, 20, 50])
        pos[a] += q
        rows.append((d, a, q, float(R.randint(50, 60))))
    return rows


class TestCompactWindowFormula(unittest.TestCase):
    """Property: with every trade inside every loss's window, each sale's
    denied units are min(its loss units, sum over holders of min(units
    acquired, units held at the end)) — CRA's formula per sale."""

    @rule("CA-SL-08", "CA-SL-03")
    def test_compact_books(self):
        import os
        n = int(os.environ.get("TAXJSON_FUZZ_BOOKS", "300"))
        for seed in range(n):
            rows = _compact_book(seed)
            T, S, F = [], [], []
            for k, (d, a, q, px) in enumerate(rows):
                t = _row(d, q, px, a, t=f"10:{k:02d}:00")
                (T if a in 'AB' else S if a == 'S' else F).append(t)
            with contextlib.redirect_stderr(io.StringIO()):
                res = CanadaTaxRules().compute_gains(
                    T, sheltered_transactions=S, affiliated_transactions=F)
            cap = (min(sum(q for d, a, q, p in rows[1:]
                           if a in 'AB' and q > 0),
                       sum(q for d, a, q, p in rows if a in 'AB'))
                   + sum(max(0, sum(q for d, a, q, p in rows if a == h))
                         for h in 'SF'))
            # One sale = one account's same-day sales, no buy between.
            sale_of, prev = {}, {}
            for k, (d, a, q, p) in enumerate(rows):
                if q > 0:
                    prev.pop(a, None)
                    continue
                g = prev.get(a)
                if g is None or rows[g][0] != d:
                    g = k
                prev[a] = g
                sale_of[k] = g
            got, units = {}, {}
            for e in res['transactions']:
                if 'proceeds' not in e or e['raw_gain'] >= -1e-9:
                    continue
                k = next(i for i, (d, a, q, p) in enumerate(rows)
                         if d == e['date'] and a == e['account']
                         and q == -e['qty']
                         and i not in got)
                got[k] = (e['disallowed_amount']
                          / (-e['raw_gain'] / e['qty']))
                units[k] = e['qty']
            for g in set(sale_of[k] for k in got):
                ks = [k for k in got if sale_of[k] == g]
                want = min(sum(units[k] for k in ks), cap)
                have = sum(got[k] for k in ks)
                self.assertAlmostEqual(have, want, places=6,
                                       msg=f"seed {seed}: {rows}")


class TestPerDispositionIsCanadaOnly(unittest.TestCase):

    @rule("CA-SL-08")
    @rule("US-WASH-02")
    def test_one_rebuy_two_losses_both_countries(self):
        # Two loss sales a day apart, one 100-share rebuy held: Canada
        # denies 100 units on each sale (per disposition); the US
        # matches each replacement share once (the first loss only).
        def b(sym):
            return [tx("BUYSELL", "2025-01-06", sym, 200, 10000),
                    tx("BUYSELL", "2025-03-03", sym, -100, 4000),
                    tx("BUYSELL", "2025-03-04", sym, -100, 4000),
                    tx("BUYSELL", "2025-03-10", sym, 100, 4000)]
        r = gains_both(b("XYZ.US"), year=2025)
        self.assertAlmostEqual(r["canada"]["summary"]["total_disallowed"],
                               2000.0, places=2)
        self.assertAlmostEqual(r["usa"]["summary"]["total_disallowed"],
                               1000.0, places=2)


if __name__ == "__main__":
    unittest.main()
