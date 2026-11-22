"""A seeded property check of the Canadian engine's own-account moves of
option contracts (backlog TJ-3264): random books of 2-4 taxable accounts,
one or two option series and a stock, with moves of bought and written
contracts built through the pipeline's own pairing (transfer_in.arrivals
and stages._move_legs) under a broker's sign convention. A model keeps
the ruling's positions (each account's bought and written contracts, the
pooled long) and reads each move by the recorded decisions, not by the
engine's order of applying a day's moves: a move is read on the positions
standing at its moment (TJ-3242, TJ-3260), a reading fits when its sender
holds the units (TJ-3261), one that reads more than one way there is
booked as the rows' sign's own reading and named by a Warning (TJ-3243,
TJ-3272), and a sure reading stands while its units fit (TJ-3260). Where
the sign's own reading of an ambiguous move no longer fits once the
moment's other moves apply, or an ambiguous move shares its moment with
another move of the series, what the engine books is its own order's
(`amb-unsettled`: the Warning is still expected, the positions are not
checked from there and no later move is generated). The engine's router,
lots and pools are then compared with the model (`run_one`). Synthetic
data only: made-up tickers, accounts and amounts.

Conventions: 'direction' (Questrade's rows: out negative), 'change' (IB's:
the position's change), and the same rows with the convention not known
('unknown', 'unknownc').
"""
import contextlib
import copy
import datetime as dt
import io
import random
from collections import defaultdict

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction

C = 'SYQ251219C00020000.TO'
P = 'SYQ261218P00010000.TO'
S = 'SYQ.TO'
EPS = 1e-9
YE = '2025-12-31'
SFX = '\x00short'
BROKER = {'direction': 'questrade', 'change': 'ib', 'unknown': 'other', 'unknownc': 'other'}


def tx(account, date, qty, net, sym=C, action='BUYSELL', time='10:00:00'):
    price = abs(net / (qty or 1)) if sym == S else (abs(net / qty) / 100 if qty else 0.0)
    return TaxTransaction(action=action, date=date, date_settle=date, time=time, symbol=sym,
                          quantity=float(qty), currency='CAD', price=price,
                          net_amount=float(net), account=account)


def move_legs(frm, to, date, qty, change, sym, broker, n):
    """The engine's legs for a move of `qty` contracts (negative: written)
    as the pipeline builds them from two TRANSFER rows of `broker`."""
    from taxjson.lib import transfer_in as TI
    from taxjson.pipeline.stages import _move_legs
    out_q = -qty if change else -abs(qty)
    rows = [(a, broker, {'action': 'TRANSFER', 'symbol': sym, 'quantity': float(q),
                         'date': date, 'currency': 'CAD', 'description': 'transfer'})
            for a, q in ((frm, out_q), (to, -out_q))]
    moves = []
    TI.arrivals(rows, moves=moves)
    m, = moves
    by_acct = _move_legs([{'symbol': m.key, 'qty': float(m.quantity), 'from': m.out_account,
                           'to': m.in_account, 'date': min(m.out_date, m.in_date),
                           'time': '00:00:00', 'symbol_out': m.out_symbol,
                           'symbol_in': m.in_symbol, 'currency_out': 'CAD',
                           'currency_in': 'CAD', 'signed_by': m.signed_by}], 'CAD')
    legs = [TaxTransaction(**d) for a in sorted(by_acct) for d in by_acct[a]]
    for t in legs:
        t.description = t.description.replace('#1:', f'#{n}:')
    return legs


class Model:
    """Each account's bought (L) and written (S) contracts of a series, and
    the pooled long (PL): a trade covers the account's own written
    contracts first, past them it buys; a sale closes its bought ones first,
    past them it writes."""

    def __init__(self):
        self.L = defaultdict(float)
        self.S = defaultdict(float)
        self.PL = defaultdict(float)

    def row(self, a, sym, q):
        k = (sym, a)
        if q < 0:
            use = min(self.L[k], -q)
            self.L[k] -= use
            self.PL[sym] -= use
            self.S[k] += (-q - use)
        else:
            use = min(self.S[k], q)
            self.S[k] -= use
            self.L[k] += (q - use)
            self.PL[sym] += (q - use)

    def snapshot(self):
        return ({k: round(v, 6) for k, v in self.L.items() if v > EPS},
                {k: round(v, 6) for k, v in self.S.items() if v > EPS},
                {k: round(v, 6) for k, v in self.PL.items() if v > EPS})


def mirror(conv, n, p, u, L, S, sym):
    """The engine's reading of a move whose negative leg is account n's
    and positive leg p's (core._MarkedShortRouter.move_pair): a reading
    fits when its sender holds at least the units of that kind; the pick
    among fitting readings only."""
    ln, sn, sp = L[(sym, n)], S[(sym, n)], S[(sym, p)]
    if conv == 'direction':
        readings = [('written', n, p, sn >= u - EPS), ('bought', n, p, ln >= u - EPS)]
    else:
        readings = [('bought', n, p, ln >= u - EPS),
                    ('written', n, p, conv != 'change' and sn >= u - EPS),
                    ('written', p, n, sp >= u - EPS)]
    fits = [r for r in readings if r[3]]
    if not fits:
        return None
    return fits[0][:3] + ('ambiguous' if len(fits) > 1 else '',)


def legs_np(conv, kind, frm, to):
    """(the negative leg's account, the positive leg's) as the pairing builds them."""
    if conv in ('change', 'unknownc') and kind == 'written':
        return to, frm
    return frm, to


def gen_book(seed, conv):
    rnd = random.Random(seed)
    accts = rnd.sample(list('abcd'), rnd.choice([2, 2, 3, 3, 4]))
    series = [C] if rnd.random() < 0.6 else [C, P]
    m = Model()
    SL = defaultdict(float)
    rows, moves, flags = [], [], []
    n_amb = 0
    unsettled = False
    ye_snap = None
    base = dt.date(2025, 1, 2)
    dates = [str(base + dt.timedelta(days=x)) for x in sorted(rnd.sample(range(0, 410), rnd.randint(5, 14)))]
    if rnd.random() < 0.7 and not any(d > YE for d in dates):
        dates.append(str(dt.date(2026, 1, rnd.randint(5, 28))))
    for d in dates:
        day_rows, day_moves = [], []
        tsec = [9 * 3600]

        def nxt():
            tsec[0] += rnd.randint(60, 1800)
            return f"{tsec[0] // 3600:02d}:{(tsec[0] // 60) % 60:02d}:{tsec[0] % 60:02d}"

        def apply(mm, k2, f2, t2, s2, q2):
            if k2 == 'written':
                mm.S[(s2, f2)] -= q2
                mm.S[(s2, t2)] += q2
            else:
                mm.L[(s2, t2)] += q2
                mm.L[(s2, f2)] = max(mm.L[(s2, f2)] - q2, 0.0)

        def fits_earlier(kind, frm, to, sym, q):
            """Whether the engine reads the move at an earlier moment of the
            day: on the positions standing there (TJ-3260), or once that
            moment's moves apply (the unread ones read again, TJ-3256)."""
            mm = copy.deepcopy(start)
            n, p = legs_np(conv, kind, frm, to)
            for i, r in enumerate([None] + list(day_rows)):
                if r is not None and r.symbol != S:
                    mm.row(r.account, r.symbol, float(r.quantity))
                if mirror(conv, n, p, q, mm.L, mm.S, sym) is not None:
                    return True
                for (k2, f2, t2, s2, q2, mo, _amb) in day_moves:
                    if mo == i:
                        apply(mm, k2, f2, t2, s2, q2)
                if mirror(conv, n, p, q, mm.L, mm.S, sym) is not None:
                    return True
            return False

        def crowded(sym, moment, amb):
            """Whether the moment's moves of the series leave an ambiguous
            move's booking to the engine's order (the model does not share
            it): an ambiguous one beside any other, or any beside one."""
            others = [mv for mv in day_moves if mv[3] == sym and mv[5] == moment]
            return bool(others) and (amb or any(mv[6] for mv in others))

        start = copy.deepcopy(m)
        snapped, snap = [-1], [None]
        for _ in range(rnd.choice([1, 1, 2, 2, 3, 4])):
            kind = rnd.choice(['write', 'buyback', 'buy', 'sell', 'mv_w', 'mv_b', 'stock', 'stock',
                               'assign', 'expiry', 'mv_s', 'write_mv'])
            sym = rnd.choice(series)
            a = rnd.choice(accts)
            if kind == 'write':
                q = rnd.randint(1, 3)
                day_rows.append(tx(a, d, -q, q * rnd.randint(100, 600), sym, time=nxt()))
                m.row(a, sym, -q)
            elif kind == 'buyback':
                s = m.S[(sym, a)]
                if s < 1:
                    continue
                q = rnd.randint(1, int(s) + (1 if rnd.random() < 0.3 else 0))
                day_rows.append(tx(a, d, q, q * rnd.randint(20, 500), sym, time=nxt()))
                m.row(a, sym, q)
            elif kind == 'buy':
                q = rnd.randint(1, 3)
                day_rows.append(tx(a, d, q, q * rnd.randint(100, 600), sym, time=nxt()))
                m.row(a, sym, q)
            elif kind == 'sell':
                lq = m.L[(sym, a)]
                if lq < 1:
                    continue
                q = rnd.randint(1, int(lq) + (1 if rnd.random() < 0.3 else 0))
                day_rows.append(tx(a, d, -q, q * rnd.randint(20, 700), sym, time=nxt()))
                m.row(a, sym, -q)
            elif kind == 'expiry':
                if sym != C or d < '2025-12-19' or d > '2025-12-31':
                    continue
                s, lq = m.S[(sym, a)], m.L[(sym, a)]
                if s >= 1:
                    day_rows.append(tx(a, d, int(s), 0, sym, time=nxt()))
                    m.row(a, sym, int(s))
                elif lq >= 1:
                    day_rows.append(tx(a, d, -int(lq), 0, sym, time=nxt()))
                    m.row(a, sym, -int(lq))
            elif kind == 'assign':
                s = m.S[(sym, a)]
                if s < 1 or SL[a] < 100:
                    continue
                q = min(int(s), int(SL[a] // 100))
                t = nxt()
                day_rows.append(tx(a, d, q, 0, sym, action='ASSIGN', time=t))
                h, mi, se = map(int, t.split(':'))
                day_rows.append(tx(a, d, -100 * q, 2000 * q, S,
                                   time=f"{h:02d}:{mi:02d}:{se + 1:02d}" if se < 59 else t))
                m.row(a, sym, q)
                SL[a] -= 100 * q
            elif kind == 'stock':
                if rnd.random() < 0.6 or SL[a] < 100:
                    q = rnd.choice([100, 200, 300])
                    day_rows.append(tx(a, d, q, q * rnd.randint(10, 15), S, time=nxt()))
                    SL[a] += q
                else:
                    day_rows.append(tx(a, d, -100, 100 * rnd.randint(8, 18), S, time=nxt()))
                    SL[a] -= 100
            elif kind == 'mv_s':
                b = rnd.choice([x for x in accts if x != a])
                if SL[a] < 100 or day_rows or day_moves or unsettled:
                    continue
                moves.append(dict(date=d, kind='bought', frm=a, to=b, sym=S, q=100))
                day_moves.append(('bought', a, b, S, 100, 0, False))
                SL[b] += 100
                SL[a] -= 100
            elif kind in ('mv_w', 'mv_b'):
                b = rnd.choice([x for x in accts if x != a])
                k2 = 'written' if kind == 'mv_w' else 'bought'
                held = m.S[(sym, a)] if k2 == 'written' else m.L[(sym, a)]
                if held < 1 or unsettled:
                    continue
                q = rnd.randint(1, int(held))
                moment = len(day_rows)
                if moment > 0 and fits_earlier(k2, a, b, sym, q):
                    continue
                n, p = legs_np(conv, k2, a, b)
                if snapped[0] != moment:
                    snapped[0] = moment
                    snap[0] = (copy.deepcopy(m.L), copy.deepcopy(m.S))
                # A move is read on the positions standing at its moment
                # (TJ-3260): more than one reading fitting there is named by
                # the Warning whatever is booked (TJ-3272); the sign's own
                # reading is booked when its units still fit once the
                # moment's earlier moves apply, else (sure) read again.
                r_ = mirror(conv, n, p, q, snap[0][0], snap[0][1], sym)
                amb = r_ is not None and r_[3] == 'ambiguous'
                if r_ is not None and (m.S if r_[0] == 'written' else m.L)[(sym, r_[1])] < q - EPS:
                    if amb:
                        n_amb += 1
                        flags.append('amb-unsettled')
                        unsettled = True
                        moves.append(dict(date=d, kind=k2, frm=a, to=b, sym=sym, q=q))
                        continue
                    r_ = mirror(conv, n, p, q, m.L, m.S, sym)
                    amb = r_ is not None and r_[3] == 'ambiguous'
                if r_ is None:
                    flags.append('nofit')
                    continue
                if amb:
                    n_amb += 1
                    flags.append('amb')
                elif r_[:3] != (k2, a, b):
                    flags.append('divergent')
                    continue
                moves.append(dict(date=d, kind=k2, frm=a, to=b, sym=sym, q=q))
                if crowded(sym, moment, amb):
                    flags.append('amb-unsettled')
                    unsettled = True
                day_moves.append(r_[:3] + (sym, q, moment, amb))
                apply(m, *r_[:3], sym, q)
            elif kind == 'write_mv':
                b = rnd.choice([x for x in accts if x != a])
                if m.S[(sym, a)] > EPS or m.L[(sym, a)] > EPS or unsettled:
                    continue
                q = rnd.randint(1, 2)
                if fits_earlier('written', a, b, sym, q):
                    continue
                day_rows.append(tx(a, d, -q, q * rnd.randint(100, 600), sym, time=nxt()))
                m.row(a, sym, -q)
                n, p = legs_np(conv, 'written', a, b)
                r_ = mirror(conv, n, p, q, m.L, m.S, sym)
                if r_ is None or r_[:3] != ('written', a, b):
                    flags.append('w+m-odd')
                    continue
                amb = r_[3] == 'ambiguous'
                if amb:
                    n_amb += 1
                    flags.append('amb')
                moves.append(dict(date=d, kind='written', frm=a, to=b, sym=sym, q=q))
                if crowded(sym, len(day_rows), amb):
                    flags.append('amb-unsettled')
                    unsettled = True
                day_moves.append(('written', a, b, sym, q, len(day_rows), amb))
                apply(m, 'written', a, b, sym, q)
        rows.extend(day_rows)
        if d <= YE:
            ye_snap = m.snapshot()
    return dict(accts=accts, series=series, rows=rows, moves=moves, flags=flags, n_amb=n_amb,
                model_end=m.snapshot(), model_ye=ye_snap or m.snapshot())


def build_txs(book, conv):
    txs = list(book['rows'])
    for i, mv in enumerate(book['moves'], 1):
        q = mv['q'] if mv['kind'] == 'bought' else -mv['q']
        txs.extend(move_legs(mv['frm'], mv['to'], mv['date'], q, conv in ('change', 'unknownc'),
                             mv['sym'], BROKER[conv], i))
    return txs


def figures(res):
    out = [f"G {r['date']} {r['account']} {r['symbol']} q={r['qty']:g} gain={round(r['gain'], 2)} "
           f"dis={round(r.get('disallowed_amount') or 0.0, 2)}" for r in res['transactions']]
    for key in ('inventory', 'inventory_year_end'):
        for i in sorted(res.get(key) or [], key=lambda i: (i['symbol'], i.get('account') or '')):
            if abs(i['qty']) > 1e-9:
                out.append(f"{key[0]} {i['symbol']} {i.get('account') or '-'} q={i['qty']:g} "
                           f"cost={round(i['total_cost'], 2)}")
    return out


KEEP = {}


def patch_keep():
    """Make the engine's `keep` record the router and the pools; returns
    the original to restore."""
    from taxjson.engine.ca import holders
    cls = next(c for c in vars(holders).values() if isinstance(c, type)
               and 'final_short_units' in getattr(c, '__dataclass_fields__', {}))
    orig = cls.keep

    def keep(self, *, it, msr, tl, tl_shorts, tl_dates, ye_pools, ye_shorts):
        def snap(pools):
            return {k: (p.qty, [(lot.get('account'), lot['units']) for lot in p.grants]
                        or [(a, e['units']) for a, e in (p.account_lots or {}).items()])
                    for k, p in (pools or {}).items()}
        KEEP.update(short=dict(msr.short), long=dict(msr.long), pools=snap(it.global_pools),
                    ye_pools=snap(ye_pools) if ye_pools is not None else None,
                    ye_shorts=dict(ye_shorts or {}))
        return orig(self, it=it, msr=msr, tl=tl, tl_shorts=tl_shorts, tl_dates=tl_dates,
                    ye_pools=ye_pools, ye_shorts=ye_shorts)
    cls.keep = keep
    return cls, orig


def check_pools(pools, shorts, tag):
    """(c): each option short pool's lots by account are the router's shorts,
    and its units their sum; no router short without a pool."""
    probs, seen = [], set()
    for k, (qty, lots) in pools.items():
        sym = k[:-len(SFX)] if k.endswith(SFX) else k
        if sym == S or not (k.endswith(SFX) or (qty < -EPS and lots)):
            continue
        by = defaultdict(float)
        for a, u in lots:
            by[a or ''] += u
        rt = {a: round(u, 6) for (s_, a), u in shorts.items() if s_ == sym and u > 1e-6}
        lb = {a: round(u, 6) for a, u in by.items() if u > 1e-6}
        if lots and abs(qty + sum(by.values())) > 1e-6:
            probs.append((tag, 'pool qty vs lots', sym, qty, dict(by)))
        if rt != lb and (lots or tag == 'end'):
            probs.append((tag, 'router shorts vs lots', sym, rt, lb))
        seen.add(sym)
    probs += [(tag, 'router short without a pool', s_, a, u)
              for (s_, a), u in shorts.items() if s_ != S and u > 1e-6 and s_ not in seen]
    return probs


def check_positions(book):
    """(b), (g): the router's positions and the pools against the model, at
    the end and at the year end."""
    probs = []

    def pools_vs(pools, mS, mPL, tag):
        for sym in book['series']:
            ml = mPL.get(sym, 0.0)
            ms = sum(v for (s_, a), v in mS.items() if s_ == sym)
            pl = pools.get(sym, (0.0, []))[0]
            ps = pools.get(sym + SFX, (0.0, []))[0]
            if pl < -EPS and ps == 0.0:
                ps, pl = pl, 0.0
            if abs(pl - ml) > 1e-6 or abs(-ps - ms) > 1e-6:
                probs.append((tag, 'pools vs model', sym, pl, ps, ml, ms))
    mL, mS, mPL = book['model_end']
    rL = {k: round(v, 6) for k, v in KEEP['long'].items() if v > 1e-6 and k[0] != S}
    rS = {k: round(v, 6) for k, v in KEEP['short'].items() if v > 1e-6}
    if rL != {k: v for k, v in mL.items() if k[0] != S}:
        probs.append(('end', 'router long vs model', rL, mL))
    if rS != mS:
        probs.append(('end', 'router short vs model', rS, mS))
    pools_vs(KEEP['pools'], mS, mPL, 'end')
    if KEEP['ye_pools'] is not None:
        yL, yS, yPL = book['model_ye']
        yS_r = {k: round(v, 6) for k, v in KEEP['ye_shorts'].items() if v > 1e-6}
        if yS_r != yS:
            probs.append(('ye', 'router short vs model', yS_r, yS))
        pools_vs(KEEP['ye_pools'], yS, yPL, 'ye')
    return probs


def run_one(book, conv, us=False):
    """The engine on the book: its figures, its move Warnings and, for
    Canada with `patch_keep` on, the invariants' problems (empty = held)."""
    txs = build_txs(book, conv)
    err = io.StringIO()
    KEEP.clear()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = (USATaxRules() if us else CanadaTaxRules()).compute_gains(txs, year_end=YE)
    out = dict(fig=figures(res),
               warn=[ln for ln in err.getvalue().splitlines() if 'own-account move' in ln])
    if not us and KEEP:
        probs = check_pools(KEEP['pools'], KEEP['short'], 'end')
        if KEEP['ye_pools'] is not None:
            probs += check_pools(KEEP['ye_pools'], KEEP['ye_shorts'], 'ye')
        out['problems'] = probs + check_positions(book)
    return out
