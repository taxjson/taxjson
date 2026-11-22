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
  P5 MAIN CLEAN— no TRANSFER action survives into the engine books
                 (except balance-only own-account moves, netting to 0).
"""
import io
import json
import os
import unittest
from contextlib import redirect_stderr

from _support.engine_fuzz_books import totals
from _support.engine_transfer_fuzz_books import make_transfer_book, copy_book, prep

from taxjson.engine.rows import AmbiguousTransferDateError
from taxjson.engine.by_country import get_tax_rules

# Seeds per run. 150 keeps the default suite fast (<2s here) while
# still exercising every generator shape; CI/nightly should raise it
# (the round-five audit ran 2,400 with zero violations).
N_SEEDS = int(os.environ.get("TAXJSON_TRANSFER_FUZZ_BOOKS", "150"))


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

    # P5: no raw TRANSFER survives into the engine books, except a
    # netted own-account move kept in the wash context as a
    # balance-only row (S018-05) — which must net to zero per symbol.
    own = {}
    for label, rows in (("main", m), ("sheltered", s)):
        for t in rows:
            if (label == "sheltered" and t.action == "TRANSFER"
                    and getattr(t, "type", "") == "own_account_move"):
                own[t.symbol] = own.get(t.symbol, 0.0) + t.quantity
                continue
            if t.action == "TRANSFER":
                return f"P5 raw TRANSFER survived in {label} book"
    for sym, q in own.items():
        if abs(q) > 1e-9:
            return f"P5 own-account move rows do not net for {sym}: {q}"

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
