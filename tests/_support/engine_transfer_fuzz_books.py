"""Shared test helpers: the transfer fuzzer's random books and their preparation for the engine.

Shared from: test_transfer_fuzz
"""
import io
import random
from contextlib import redirect_stderr
from datetime import date, timedelta

from _support.engine_fuzz_books import make_book

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import prepare_books
from taxjson.lib.tt_format import MANUAL_TRANSFER_DECLARATION


BASE = date(2025, 1, 6)


def d(off):
    return (BASE + timedelta(days=int(off))).isoformat()


def T(**kw):
    kw.setdefault("time", "09:30:00")
    kw.setdefault("currency", "CAD")
    return TaxTransaction(**kw)


def transfer(sym, acct, off, qty, declared=False, net=0.0):
    return T(action="TRANSFER", date=d(off), symbol=sym, account=acct,
             quantity=float(qty), net_amount=float(net),
             description=MANUAL_TRANSFER_DECLARATION if declared else "")


def make_transfer_book(seed):
    rng = random.Random(10_000_000 + seed)
    txs, shel = make_book(seed)
    symbols = sorted({t.symbol for t in txs if t.action == "BUYSELL"})
    if not symbols:
        symbols = ["S0.TO"]
    shel_accts = ["rrsp", "rrsp2", "tfsa"]
    trade_offs = sorted({(date.fromisoformat(t.date) - BASE).days
                         for t in txs if t.action == "BUYSELL"})

    def rsym():
        return rng.choice(symbols)

    def roff():
        return rng.randint(0, 140)

    def near_trade_off():
        # An offset deliberately within +-30d of a taxable trade.
        if not trade_offs or rng.random() < 0.2:
            return roff()
        return rng.choice(trade_offs) + rng.randint(-25, 25)

    n_shapes = rng.randint(1, 5)
    for _ in range(n_shapes):
        shape = rng.randint(0, 7)
        q = float(rng.choice([10, 25, 50, 100]))
        a = rng.choice(shel_accts)
        if shape == 0:
            # Same-account custody pair (zero-net), gap 0..40d (gaps
            # >35 split segments so both legs survive — also legal).
            t0 = roff() if rng.random() < 0.5 else near_trade_off()
            gap = rng.randint(0, 40)
            s = rsym()
            shel.append(transfer(s, a, t0, -q))
            shel.append(transfer(s, a, t0 + gap, q))
        elif shape == 1:
            # Cross-account move rrsp -> rrsp2.
            t0 = roff() if rng.random() < 0.5 else near_trade_off()
            gap = rng.randint(0, 10)
            s = rsym()
            b = rng.choice([x for x in shel_accts if x != a])
            shel.append(transfer(s, a, t0, -q))
            shel.append(transfer(s, b, t0 + gap, q))
        elif shape == 2:
            # Account-wide restatement cluster: 3..6 symbols (padded
            # with synthetic ones), zero-net per symbol, tight window.
            t0 = near_trade_off()
            n_sym = rng.randint(3, 6)
            pool = list(symbols) + [f"R{i}.TO" for i in range(6)]
            rng.shuffle(pool)
            for s in pool[:n_sym]:
                dq = float(rng.choice([10, 25, 50]))
                o1 = t0 + rng.randint(0, 2)
                o2 = o1 + rng.randint(0, 2)
                shel.append(transfer(s, a, o1, -dq))
                shel.append(transfer(s, a, o2, dq))
        elif shape == 3:
            # Lone leg (in or out) — a genuine in-kind contribution or
            # withdrawal; survives to the rewrite.
            shel.append(transfer(rsym(), a, near_trade_off(),
                                 q if rng.random() < 0.6 else -q,
                                 net=q * rng.uniform(5, 50)))
        elif shape == 4:
            # DECLARED zero-net pair near a taxable trade.
            t0 = near_trade_off()
            s = rsym()
            both = rng.random() < 0.7
            shel.append(transfer(s, a, t0, -q, declared=True))
            shel.append(transfer(s, a, t0 + rng.randint(0, 5), q,
                                 declared=both))
        elif shape == 5:
            # DECLARED pair plus a NON-declared zero-net pair chained
            # 8..30 days away (inside 35d gap, outside 7d blessing):
            # exercises the partial-bless / refuse-whole branch.
            t0 = near_trade_off()
            s = rsym()
            if rng.random() < 0.5:
                # Balanced declared pair + far non-declared pair:
                # partial-bless SUCCESS branch.
                shel.append(transfer(s, a, t0, -q, declared=True))
                shel.append(transfer(s, a, t0 + 1, q, declared=True))
                far = t0 + rng.randint(9, 30)
                q2 = float(rng.choice([10, 25]))
                shel.append(transfer(s, a, far, q2))
                shel.append(transfer(s, a, far + 1, -q2))
            else:
                # Declared leg whose counter-leg sits OUTSIDE the 7d
                # blessing pad: blessed subset unbalanced -> the
                # refuse-whole ("attestation does not net") branch.
                far = t0 + rng.randint(9, 30)
                shel.append(transfer(s, a, t0, -q, declared=True))
                shel.append(transfer(s, a, far, q))
        elif shape == 6:
            # Chained-gap residue: 3 pairs each <=35d apart spanning
            # >45d total — the span cap must refuse the drop.
            t0 = roff()
            s = rsym()
            for k in range(3):
                shel.append(transfer(s, a, t0 + 30 * k, -q))
                shel.append(transfer(s, a, t0 + 30 * k + 2, q))
        else:
            # One-sided same-sign cluster (never a move) or unbalanced
            # cluster.
            t0 = roff()
            s = rsym()
            for _k in range(rng.randint(2, 3)):
                shel.append(transfer(s, a, t0 + rng.randint(0, 5),
                                     q if rng.random() < 0.5 else
                                     float(rng.choice([10, 25]))))
    return txs, shel


def copy_book(rows):
    return [TaxTransaction(**t.to_dict()) for t in rows]


def prep(txs, shel):
    with redirect_stderr(io.StringIO()):
        m, s, _a, _log = prepare_books(
            copy_book(txs), copy_book(shel), taxable=True,
            phantom_hint=False)
    return m, s
