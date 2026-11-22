"""A seeded property test of the assignment pairing (lib/core
`_pair_assign_legs`: `_assign_candidates`, `_compete`, `_fill_ties`;
CA-OPT-08, TJ-2750, TJ-3251 to TJ-3253, TJ-3265 to TJ-3268), built like
the review's fuzz: random books of same-strike written puts on one day (a
tie group), with or without another put at another strike, an exercised
call and a put of another day, delivered as marked or plain rows of
various sizes, at the strike or off it, on the day or around it. The
invariants, which hold for any seed (checked on 3000 locally):

- (a) one tie group alone gives one pairing in every placement of its
  option rows among the share rows (its members may share an expiry; its
  deliveries are marked, or plain at the strike or off it, a plain one
  listed after the option rows, since one listed before them is no
  candidate unless it is exactly a member's quantity at the strike,
  TJ-3223);
- (b) the other assignments' claims (every option the code does not put
  in a tie group of two or more members with candidates, a lone member
  among them) are what they were before TJ-2750: a digest computed on
  cdf0f6e5's pairing;
- (c) no option holds more than its delivered quantity, no leg is claimed
  beyond its quantity;
- (d) no leg is left with shares a tied member in need could take, unless
  TJ-0838's guard refuses them (the leg was partly claimed from outside
  the member's group and its rest does not cover the member's need);
- (e) the US pairing (no tie rule) is unchanged: a digest of every seed's
  pairs.

The streams are date-sorted, as the engines' are (a row of another day
never sits among the day's rows); within a day the rows take every
order. Deterministic (seeded), a few hundred books, a few seconds.
Synthetic data only (made-up tickers; no accounts)."""
import hashlib
import itertools
import json
import random
import unittest
from datetime import date, timedelta

from taxjson.engine.assign import _assign_candidates, _assign_delivery_shares, _pair_assign_legs
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.option_symbols import is_option_symbol
from tax_rules import rule
from taxjson.rules import ids as R

SYQ = 'SYQ.TO'
DAY = '2025-12-12'
EPS = 1e-6
EXPIRIES = ('251212', '260116', '260220', '260320')
PURE_SEEDS = 120
MIXED_SEEDS = 300
# The other assignments' pairs over the mixed seeds as cdf0f6e5 (before
# TJ-2750) gave them, and the US pairings as they were: a change to
# either is a change to what "pairs as before" means (CA-OPT-08,
# US-OPT-05). Both were computed on a `git archive cdf0f6e5` export with
# this module's generator and keys: the pairs from that tree's
# `_pair_assign_legs` (which has no tie rule), the tie groups from this
# tree's `_assign_candidates` (a classification of the rows alone), the
# options outside a group of two or more members with candidates being
# the other assignments.
OTHERS_DIGEST = 'd2468029b24fe4426c4cfe8b957ffd19197b74dfba849bb2e563e91534712f24'
US_DIGEST = '718eecdf11ed5cc79f43e5f76728242527612d44724760618a57ebce1614ed3f'


def _sym(kind, strike, exp):
    return f'SYQ{exp}{kind}{int(strike * 1000):08d}.TO'


def _tr(i, action, day, sym, q, px, mini=False):
    extra = {}
    if mini and is_option_symbol(sym):
        extra = dict(multiplier=10.0, contract_size_basis='declared')
    t = TaxTransaction(action=action, date=day, date_settle=day,
                       time=f'16:{i // 60:02d}:{i % 60:02d}', symbol=sym,
                       quantity=float(q), price=float(px),
                       net_amount=float(abs(q) * px), currency='CAD',
                       account='m', **extra)
    t.id = f'r{i}'
    return t


def _shift(day, n):
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def _gen(rng, pure):
    """(mini, options, legs): options as (right, strike, expiry, contracts,
    date), legs as (action, quantity, price, date). `pure`: one tie
    group alone, two or three same-strike puts that may share an
    expiry, its deliveries marked or plain (TJ-3275)."""
    mini = rng.random() < 0.15
    unit = 10 if mini else 100
    if pure:
        exps = (EXPIRIES[:3] if rng.random() < 0.7
                else (EXPIRIES[0], EXPIRIES[0], EXPIRIES[1]))
        opts = [('P', 12.0, exps[k], rng.randint(1, 3), DAY)
                for k in range(rng.randint(2, 3))]
    else:
        opts = [('P', 12.0, EXPIRIES[k], rng.randint(1, 3), DAY)
                for k in range(rng.randint(2, 3))]
    if not pure:
        if rng.random() < 0.4:
            opts.append(('P', 10.0, EXPIRIES[3], rng.randint(1, 2), DAY))
        if rng.random() < 0.4:
            opts.append(('C', 12.0, EXPIRIES[3], -rng.randint(1, 2), DAY))
        if rng.random() < 0.2:
            opts.append(('P', 12.0, EXPIRIES[3], rng.randint(1, 2),
                         _shift(DAY, rng.choice([-1, 1]))))
    legs = []
    for _ in range(rng.randint(1, 3 if pure else 5)):
        q = rng.choice([0.5, 1, 1.5, 2, 2.5, 3, 4]) * unit
        legs.append((rng.choice(['ASSIGN', 'ASSIGN', 'BUYSELL']), q,
                     rng.choice([12.0, 12.0, 12.0, 10.0, 11.0]),
                     _shift(DAY, rng.choice([0, 0, 0, 0, -1, 1, 2]))))
    return mini, opts, legs


def _build(mini, opts, legs, order):
    """The rows in `order` as the engine streams them: by date, the
    order kept within a day (its rows' times follow `order`)."""
    rows = []
    for i, (kind, idx) in enumerate(order):
        if kind == 'O':
            right, strike, exp, q, day = opts[idx]
            rows.append(_tr(i, 'ASSIGN', day, _sym(right, strike, exp), q,
                            0.0, mini))
        else:
            action, q, px, day = legs[idx]
            rows.append(_tr(i, action, day, SYQ, q, px))
    return sorted(rows, key=lambda t: t.date)


def _mixed(seed):
    rng = random.Random(seed)
    mini, opts, legs = _gen(rng, pure=False)
    order = ([('O', i) for i in range(len(opts))]
             + [('L', i) for i in range(len(legs))])
    rng.shuffle(order)
    return _build(mini, opts, legs, order)


def _placements(n_opts, n_legs, plain=()):
    """Every placement of the option rows among the share rows, the
    share rows' order kept: [(kind, index), ...] per placement. `plain`:
    the legs (indices) every option row must precede, a plain purchase
    listed before its option row being no candidate (TJ-3223)."""
    out = []
    first_plain = min(plain) if plain else n_legs
    for perm in itertools.permutations(range(n_opts)):
        for slots in itertools.combinations_with_replacement(
                range(min(n_legs, first_plain) + 1), n_opts):
            order, oi = [], 0
            for s in range(n_legs + 1):
                while oi < n_opts and slots[oi] == s:
                    order.append(('O', perm[oi]))
                    oi += 1
                if s < n_legs:
                    order.append(('L', s))
            out.append(order)
    return out


def _tied(rows):
    """The tied members' ids as the code groups them: `_assign_candidates`'
    peers (one account, underlying, strike, kind and day) of two or more
    members with a candidate leg (TJ-3276)."""
    members = {}
    for c in _assign_candidates(rows, lambda _t: True, None, True):
        if c.peer is not None:
            members.setdefault(c.peer, set()).add(c.okey)
    return {o for ms in members.values() if len(ms) > 1 for o in ms}


def _pairs(rows, tie, only=None):
    """{(option symbol, the leg's rank among the share rows): shares}, the
    rows of one series summed (they fold one premium per share, so their
    split is nothing to the figures): a key no placement of the option
    rows changes; `only`: these option ids alone."""
    p = _pair_assign_legs(rows, lambda _t: True, None, tie_pro_rata=tie)
    sym = {t.id: t.symbol for t in rows}
    rank = {t.id: n for n, t in enumerate(
        r for r in rows if not is_option_symbol(r.symbol))}
    out = {}
    for (o, lk), v in p.qty.items():
        if only is None or o in only:
            k = (sym[o], rank[lk])
            out[k] = round(out.get(k, 0.0) + v, 6)
    return out


def _digest(items):
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def _others_digest(seeds=MIXED_SEEDS):
    out = []
    for seed in range(seeds):
        rows = _mixed(seed)
        others = {t.id for t in rows if is_option_symbol(t.symbol)
                  and t.action == 'ASSIGN'} - _tied(rows)
        out.append(sorted((f'{k[0]}@{k[1]}', v)
                          for k, v in _pairs(rows, True, others).items()))
    return _digest(out)


def _us_digest(seeds=MIXED_SEEDS):
    out = []
    for seed in range(seeds):
        rows = _mixed(seed)
        out.append(sorted((f'{k[0]}@{k[1]}', v)
                          for k, v in _pairs(rows, False).items()))
    return _digest(out)


def check_bounds_and_fill(rows):
    """(c) and (d) on one book; the problems found, as text."""
    bad = []
    cands = _assign_candidates(rows, lambda _t: True, None, True)
    final = _pair_assign_legs(rows, lambda _t: True, None, tie_pro_rata=True)
    size = {t.id: _assign_delivery_shares(t) for t in rows
            if is_option_symbol(t.symbol)}
    legq = {t.id: abs(t.quantity) for t in rows
            if not is_option_symbol(t.symbol)}
    held, claimed = {}, {}
    for (o, lk), v in final.qty.items():
        if v < -EPS:
            bad.append(f'negative pair {o} {lk} {v}')
        held[o] = held.get(o, 0.0) + v
        claimed[lk] = claimed.get(lk, 0.0) + v
    for o, v in held.items():
        if size[o] is not None and v > size[o] + EPS:
            bad.append(f'{o} holds {v} of {size[o]}')
    for lk, v in claimed.items():
        if v > legq[lk] + EPS:
            bad.append(f'{lk} claimed {v} of {legq[lk]}')
    members = {}
    for c in cands:
        if c.peer is not None:
            members.setdefault(c.peer, set()).add(c.okey)
    group_of = {o: p for p, ms in members.items() if len(ms) > 1 for o in ms}
    for c in cands:
        if c.okey not in group_of:
            continue
        need = size[c.okey] - held.get(c.okey, 0.0)
        rest = legq[c.lkey] - claimed.get(c.lkey, 0.0)
        if need <= EPS or rest <= EPS:
            continue
        outside = sum(v for (o, lk), v in final.qty.items()
                      if lk == c.lkey and group_of.get(o) != group_of[c.okey])
        if not (outside > EPS and rest < need - EPS):
            bad.append(f'{c.okey} needs {need}, {c.lkey} has {rest} left '
                       f'(outside {outside})')
    return bad


class TestOneTieGroupAloneGivesOneAnswer(unittest.TestCase):
    """(a): random books of one tie group alone (2 or 3 same-strike puts
    on one day, an expiry shared or not, 1 to 3 marked or plain rows at
    the strike or off it), every placement of the option rows among the
    share rows that keeps a plain row after them: one pairing."""

    @rule(R.CA_OPT_08)
    def test_every_placement_agrees(self):
        for seed in range(PURE_SEEDS):
            rng = random.Random(seed)
            mini, opts, legs = _gen(rng, pure=True)
            plain = [i for i, leg in enumerate(legs) if leg[0] == 'BUYSELL']
            answers = {}
            for order in _placements(len(opts), len(legs), plain):
                rows = _build(mini, opts, legs, order)
                key = json.dumps(sorted((f'{k[0]}@{k[1]}', v)
                                        for k, v in _pairs(rows, True).items()))
                answers.setdefault(key, []).append(order)
            self.assertEqual(len(answers), 1,
                             f'seed {seed}: {len(answers)} answers; '
                             f'opts {opts} legs {legs}')


class TestTheOtherAssignmentsPairAsBefore(unittest.TestCase):
    """(b): over the mixed books, the pairs of every option that is not a
    tied member equal cdf0f6e5's (the tree before TJ-2750), as a digest
    computed there with this module's generator."""

    @rule(R.CA_OPT_08)
    def test_the_digest(self):
        self.assertEqual(_others_digest(), OTHERS_DIGEST)


class TestBoundsAndNothingLeftToTake(unittest.TestCase):
    """(c) and (d) over the mixed books."""

    @rule(R.CA_OPT_08)
    def test_every_book(self):
        for seed in range(MIXED_SEEDS):
            bad = check_bounds_and_fill(_mixed(seed))
            self.assertEqual(bad, [], f'seed {seed}')


class TestTheUSPairingIsUnchanged(unittest.TestCase):
    """(e): the pairing without the tie rule (the US engine's, US-OPT-05)
    over the mixed books, as a digest of every seed's pairs: the Canadian
    rule never reaches it (TJ-3173)."""

    @rule(R.US_OPT_05)
    def test_the_digest(self):
        self.assertEqual(_us_digest(), US_DIGEST)


if __name__ == '__main__':
    unittest.main()
