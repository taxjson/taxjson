"""Shared test helpers: the engine fuzzer's random fully-liquidated books, a run of the engine over one, its totals and text, a book with dust added and the conservation laws over a seed.

Shared from: test_engine_invariants
"""
import io
import json
import random
from contextlib import redirect_stderr

from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction


def make_book(seed: int, splits=True, collisions=True,
              sheltered_sells=True):
    """A random but LEGAL fully-liquidated book: several symbols and
    accounts, long and short round trips, buys/sells inside and outside
    ±30-day windows, corporate SPLITs mid-history, same-timestamp
    collisions, sheltered buys AND sells, every taxable position closed
    at the end (so deferred wash basis must fully recover)."""
    rng = random.Random(seed)
    symbols = [f"S{i}.TO" for i in range(rng.randint(1, 3))]
    accounts = [f"A{i}" for i in range(rng.randint(1, 3))]
    sheltered_accounts = ["rrsp"] if rng.random() < 0.5 else []
    txs, shel = [], []
    from datetime import date, timedelta
    base = date(2025, 1, 6)

    def d(offset):
        return (base + timedelta(days=offset)).isoformat()

    # One corporate split per symbol sometimes — a single event row
    # (the pipeline dedupes per-broker copies before the engine).
    # Half the splits are RENAME-splits (symbol_new differs): the
    # 2026-09 adversarial audit found the wash balance walks scaled
    # the whole alias class by a rename ratio, and the fuzzer was
    # blind to it because it never generated symbol_new.
    split_days = {}
    renames = {}
    if splits:
        for sym in symbols:
            if rng.random() < 0.35:
                split_days[sym] = rng.randint(20, 100)
                if rng.random() < 0.5:
                    renames[sym] = sym.replace(".TO", "N.TO")
        # Sometimes the rename target is another symbol that ALREADY
        # TRADES (a merge into a LIVE symbol, ratio != 1): audit
        # finding 1 — the class-wide loss-unit conversion divided
        # native target-symbol balances by the merge ratio although
        # the event never scaled them, and the fuzzer was blind to it
        # because every generated rename minted a FRESH symbol. Only
        # symbols with no corporate event of their own are eligible
        # targets, so post-event rows can legally keep trading under
        # the target's name (books stay legal).
        live_targets = [s for s in symbols if s not in split_days]
        no_short = set()
        for sym in sorted(renames):
            if live_targets and rng.random() < 0.5:
                target = rng.choice(live_targets)
                renames[sym] = target
                # The engine refuses to net a LONG position into an
                # existing SHORT pool (and vice versa) at a rename —
                # that close-out must be recorded explicitly. Keep
                # both merge participants long-only so the generated
                # book stays legal.
                no_short.update((sym, target))
    else:
        no_short = set()

    def clock(t):
        # Same-timestamp collisions: several events at 09:30:00 on one
        # day exercise the intra-day priority/tiebreak paths.
        if collisions and rng.random() < 0.3:
            return "09:30:00"
        return f"{rng.randint(9, 15):02d}:{rng.randint(0, 59):02d}:00"

    for sym in symbols:
        sd = split_days.get(sym)
        ratio = rng.choice([2.0, 0.5, 3.0]) if sd is not None else None
        new_sym = renames.get(sym)

        def live(t_off):
            # Rows dated after a rename use the NEW symbol, like real
            # broker files do.
            return (new_sym if new_sym and sd is not None
                    and t_off >= sd else sym)

        def units(t_off):
            # Share quantities must respect the split's terms so the
            # book stays consistent: post-split trades use scaled
            # units when closing pre-split positions.
            return 1.0 if sd is None or t_off < sd else ratio

        for acct in accounts:
            if rng.random() < 0.3:
                continue
            pos = 0.0            # in CURRENT (post-any-split) units
            t = rng.randint(0, 60)
            for _ in range(rng.randint(3, 8)):
                qty = rng.choice([10, 25, 50, 100])
                price = rng.uniform(5.0, 50.0)
                if sd is not None and t >= sd:
                    # pos carried in pre-split units until the split
                    # row applies; the engine scales pools itself, so
                    # generator tracking scales here once.
                    pass
                if pos > 0 and rng.random() < 0.5:
                    q = -min(qty, pos)
                elif pos < 0 and rng.random() < 0.5:
                    q = min(qty, -pos)
                else:
                    q = qty if rng.random() < 0.65 else -qty
                if sym in no_short and q < 0:
                    # Merge participants stay long-only (see above):
                    # cap the sell at the running position.
                    q = -min(-q, pos)
                if q == 0:
                    continue
                if sd is not None and t < sd:
                    pos = pos * 1.0
                pos += q
                txs.append(TaxTransaction(
                    action="BUYSELL", date=d(t), time=clock(t),
                    symbol=live(t), quantity=float(q), currency="CAD",
                    net_amount=round(abs(q) * price, 2),
                    account=acct))
                if sd is not None and t < sd <= t + 1:
                    pass
                _step = rng.randint(1, 25)
                if sd is not None and t < sd <= t + _step:
                    # The split lands between trades: scale the
                    # generator's running position like the engine
                    # will.
                    pos *= ratio
                t += _step
            if abs(pos) > 1e-9:                  # force full close
                txs.append(TaxTransaction(
                    action="BUYSELL", date=d(t + 5), time=clock(t + 5),
                    symbol=live(t + 5), quantity=float(-pos),
                    currency="CAD",
                    net_amount=round(abs(pos) * rng.uniform(5.0, 50.0),
                                     2),
                    account=acct))
        if sd is not None:
            # Brokers emit the corporate split into EVERY account's
            # file; the blend then carries one copy per account and
            # the engine dedupes per corporate event.
            for acct in accounts:
                txs.append(TaxTransaction(
                    action="SPLIT", date=d(sd), time="00:00:01",
                    symbol=sym, symbol_new=(new_sym or ""),
                    quantity=float(ratio), currency="CAD",
                    account=acct))
        for acct in sheltered_accounts:
            if rng.random() < 0.5:
                continue
            # Sheltered round trips — buys can be permanent-denial
            # triggers; sells exercise the CAUTION/downgrade paths.
            t = rng.randint(0, 90)
            q = rng.choice([10, 25, 50])
            if sd is not None and t >= sd:
                q = q * (2 if ratio and ratio >= 1 else 1)
            shel.append(TaxTransaction(
                action="BUYSELL", date=d(t), time=clock(t),
                symbol=live(t), quantity=float(q), currency="CAD",
                net_amount=round(q * rng.uniform(5.0, 50.0), 2),
                account=acct))
            if sheltered_sells and rng.random() < 0.4:
                t2 = t + rng.randint(5, 40)
                if sd is not None and t < sd <= t2:
                    q = q * ratio
                shel.append(TaxTransaction(
                    action="BUYSELL", date=d(t2), time=clock(t2),
                    symbol=live(t2), quantity=float(-q),
                    currency="CAD",
                    net_amount=round(q * rng.uniform(5.0, 50.0), 2),
                    account=acct))
    return txs, shel


def run_engine(country, txs, shel, **kw):
    with redirect_stderr(io.StringIO()):
        return get_tax_rules(country).compute_gains(
            [TaxTransaction(**t.to_dict()) for t in txs],
            sheltered_transactions=[TaxTransaction(**t.to_dict())
                                    for t in shel], **kw)


def totals(result):
    gains = [g for g in result["transactions"]
             if g.get("qty") and "gain" in g and not g.get("tainted")]
    return (round(sum(float(g["gain"] or 0) for g in gains), 2),
            round(sum(float(g.get("permanently_disallowed") or 0)
                      for g in gains), 2))


def book_repr(txs, shel):
    return json.dumps(
        {"taxable": [t.to_dict() for t in txs],
         "sheltered": [t.to_dict() for t in shel]}, indent=1)


def _check_conservation(case, seed, country):
    """The laws I1, I2, I4 and I5 over make_book(SEED) in COUNTRY's
    engine, asserted through CASE (a TestCase)."""
    txs, shel = make_book(seed)
    kw = {}
    if country == "usa":
        kw["per_account_basis"] = True
    wash = run_engine(country, txs, shel, **kw)
    nowash = run_engine(country, txs, shel,
                        detect_wash_sales=False, **kw)
    w_total, w_perm = totals(wash)
    n_total, _ = totals(nowash)
    ctx = (f"seed={seed} country={country}\n"
           + book_repr(txs, shel))
    # I1 (general form): realized(wash) - deferrals still parked
    # in held inventory - permanent denials == realized(no-wash).
    # Holds whether or not the book ends flat: a parked deferral
    # is a future recovery, a permanent denial never is.
    still_parked = round(sum(
        float(r.get("deferred_wash") or 0.0)
        for r in wash.get("inventory") or []), 2)
    case.assertAlmostEqual(
        round(w_total - still_parked, 2),
        round(n_total + w_perm, 2), places=1,
        msg=f"CONSERVATION violated: wash={w_total} "
            f"parked={still_parked} nowash={n_total} "
            f"perm={w_perm}\n{ctx}")
    for g in wash["transactions"]:
        if not g.get("qty") or "gain" not in g or g.get("tainted"):
            continue
        dis = float(g.get("disallowed_amount") or 0)
        raw = float(g.get("raw_gain", g.get("gain")) or 0)
        # I2: disallowance only on losses.
        if dis > 0.005:
            case.assertLess(
                raw, 0.005,
                f"disallowance on a raw gain: {g}\n{ctx}")
        # I4: signs.
        case.assertGreaterEqual(dis, -1e-9, ctx)
        case.assertGreaterEqual(
            float(g.get("permanently_disallowed") or 0), -1e-9,
            ctx)
    for r in wash.get("inventory") or []:
        case.assertGreaterEqual(
            float(r.get("deferred_wash") or 0), -1e-9,
            f"negative deferred_wash: {r}\n{ctx}")
    # I5: the solver settled.
    conv = wash.get("wash_solver_converged")
    if conv is not None:
        case.assertTrue(conv, f"solver did not converge\n{ctx}")


def _with_dust(txs, seed):
    """`txs` plus, in some books, a round trip under the 1e-6 share
    tolerance that carries real money (2026-10 audit #101: the
    Canadian engine read it as flat and stranded its cost)."""
    rng = random.Random(seed + 4242)
    if rng.random() < 0.5:
        return list(txs)
    q = rng.choice([5e-7, 9e-7, 1e-6])
    acct = txs[0].account if txs else "A0"
    return list(txs) + [
        TaxTransaction(action="BUYSELL", date="2025-02-03",
                       time="10:00:00", symbol="DUST.TO", quantity=q,
                       currency="CAD", net_amount=round(rng.uniform(1, 90), 2),
                       account=acct),
        TaxTransaction(action="BUYSELL", date="2025-03-03",
                       time="10:00:00", symbol="DUST.TO", quantity=-q,
                       currency="CAD", net_amount=round(rng.uniform(1, 90), 2),
                       account=acct)]
