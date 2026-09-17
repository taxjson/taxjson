"""TRANSFER-aware differential fuzzer for taxjson.lib.pipeline.

Blind spot being covered: tests/test_engine_invariants.py's make_book
never generates TRANSFER rows, so _handle_transfers / prepare_books
(per-account dropper, restatement pre-pass, attestation blessing,
cross-account netter, journal-candidate post-pass, transfer_rewrite)
had zero fuzz coverage.

Per seed:
  base book from make_book(seed), plus injected sheltered TRANSFER
  churn: same-account custody pairs, cross-account moves, restatement
  clusters (>=3 symbols), lone in/out legs, DECLARED-marked pairs near
  taxable trades, chained-gap clusters, split-straddling pairs.

Checks:
  P1 CRASH     — prepare_books/compute_gains must not raise anything
                 except AmbiguousTransferDateError, and that only when
                 unmatched transfer_rewrite rows survived preparation.
  P2 CONSERVE  — wash_total - parked_deferred == nowash_total + perm
                 (same identity as test_engine_invariants I1), on the
                 PREPARED book, when neither run raises.
  P3 SIGNS     — disallowance only on losses; deferred_wash >= 0;
                 permanent denials >= 0.
  P4 DETERMIN  — full prepare+compute twice on fresh copies gives
                 byte-identical JSON.
  P5 MAIN CLEAN— no TRANSFER action survives into the engine books.
"""
import io
import json
import os
import random
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta

from test_engine_invariants import make_book, totals

from taxjson.lib.core import (
    AmbiguousTransferDateError, TaxTransaction, get_tax_rules,
)
from taxjson.lib.pipeline import (
    MANUAL_TRANSFER_DECLARATION, prepare_books,
)

# Seeds per run. 150 keeps the default suite fast (<2s here) while
# still exercising every generator shape; CI/nightly should raise it
# (the round-five audit ran 2,400 with zero violations).
N_SEEDS = int(os.environ.get("TAXJSON_TRANSFER_FUZZ_BOOKS", "150"))

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


def engine(country, m, s, wash):
    kw = {"detect_wash_sales": wash}
    if country == "usa":
        kw["per_account_basis"] = True
    with redirect_stderr(io.StringIO()):
        return get_tax_rules(country).compute_gains(
            copy_book(m), sheltered_transactions=copy_book(s), **kw)


def check_book(txs, shel, country):
    """Returns None or a violation string."""
    try:
        m, s = prep(txs, shel)
    except Exception as e:  # noqa: BLE001
        return f"P1 prepare_books crash: {type(e).__name__}: {e}"

    # P5: no raw TRANSFER survives into the engine books.
    for label, rows in (("main", m), ("sheltered", s)):
        for t in rows:
            if t.action == "TRANSFER":
                return f"P5 raw TRANSFER survived in {label} book"

    survivors = sum(1 for t in s if getattr(t, "type", "")
                    == "transfer_rewrite")
    try:
        wash = engine(country, m, s, True)
    except AmbiguousTransferDateError as e:
        if survivors == 0:
            return (f"P1 AmbiguousTransferDateError with NO surviving "
                    f"transfer_rewrite rows: {e}")
        return None  # legitimate engine guard on an unmatched leg
    except Exception as e:  # noqa: BLE001
        return f"P1 engine(wash) crash: {type(e).__name__}: {e}"
    try:
        nowash = engine(country, m, s, False)
    except Exception as e:  # noqa: BLE001
        return f"P1 engine(nowash) crash: {type(e).__name__}: {e}"

    w_total, w_perm = totals(wash)
    n_total, _ = totals(nowash)
    parked = round(sum(float(r.get("deferred_wash") or 0.0)
                       for r in wash.get("inventory") or []), 2)
    if abs(round(w_total - parked, 2) - round(n_total + w_perm, 2)) \
            > 0.06:
        return (f"P2 CONSERVATION: wash={w_total} parked={parked} "
                f"nowash={n_total} perm={w_perm} "
                f"delta={round(w_total - parked - n_total - w_perm, 2)}")

    for g in wash["transactions"]:
        if not g.get("qty") or "gain" not in g or g.get("tainted"):
            continue
        dis = float(g.get("disallowed_amount") or 0)
        raw = float(g.get("raw_gain", g.get("gain")) or 0)
        if dis > 0.005 and raw >= 0.005:
            return f"P3 disallowance on a raw gain: {g}"
        if dis < -1e-9:
            return f"P3 negative disallowance: {g}"
        if float(g.get("permanently_disallowed") or 0) < -1e-9:
            return f"P3 negative permanent denial: {g}"
    for r in wash.get("inventory") or []:
        if float(r.get("deferred_wash") or 0) < -1e-9:
            return f"P3 negative deferred_wash: {r}"

    conv = wash.get("wash_solver_converged")
    if conv is not None and not conv:
        return "P3 solver did not converge"
    return None


def check_determinism(txs, shel, country):
    def once():
        try:
            m, s = prep(txs, shel)
            r = engine(country, m, s, True)
        except AmbiguousTransferDateError as e:
            return "AMBIG:" + str(e)
        except Exception as e:  # noqa: BLE001
            return "ERR:" + repr(e)
        return json.dumps(r, sort_keys=True, default=str)

    a, b = once(), once()
    if a != b:
        return "P4 DETERMINISM: two identical runs differ"
    return None


def book_repr(txs, shel):
    return json.dumps({"taxable": [t.to_dict() for t in txs],
                       "sheltered": [t.to_dict() for t in shel]},
                      indent=1)


class TestTransferFuzz(unittest.TestCase):
    """Differential fuzz over the transfer machinery. On failure the
    assertion message carries the seed and the full generated book —
    paste it into a scratch script to shrink."""

    def _run(self, country_filter):
        fails = []
        for seed in range(N_SEEDS):
            txs, shel = make_transfer_book(seed)
            countries = (["canada", "usa"] if seed % 4 == 0
                         else ["canada"])
            for country in countries:
                if country not in country_filter:
                    continue
                v = check_book(txs, shel, country)
                if v is None and seed % 5 == 0:
                    v = check_determinism(txs, shel, country)
                if v:
                    fails.append((seed, country, v))
        if fails:
            seed, country, v = fails[0]
            txs, shel = make_transfer_book(seed)
            self.fail(
                f"{len(fails)} violation(s); first: seed={seed} "
                f"{country}: {v}\nBOOK:\n{book_repr(txs, shel)}")

    def test_canada_transfer_fuzz(self):
        self._run({"canada"})

    def test_usa_transfer_fuzz(self):
        self._run({"usa"})


if __name__ == "__main__":
    unittest.main()
