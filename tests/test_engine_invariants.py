"""Property-based invariants over machine-generated books.

Example-based tests encode the author's expectations, and every engine
bug that survived five audit rounds lived in a configuration nobody
thought to write down (mixed-direction blended pools, transfer-spelled
contributions, zero-crossing sales). These tests instead assert LAWS of
the domain over seeded-random books, where the generator has no
expectations to encode:

  I1  CONSERVATION — on a fully-liquidated book, wash-adjusted totals
      equal the no-wash baseline plus permanent denials exactly: every
      deferral must be recovered by the final sale, and permanent
      (sheltered-trigger) denials must never be.
  I2  NO PHANTOM DENIALS — a disallowance may only ever be attached to
      a raw LOSS.
  I3  ORDER INVARIANCE — shuffling the input list cannot change any
      total (the engine sorts internally).
  I4  SIGN — denied amounts are never negative; deferred pool dollars
      are never negative.
  I5  SOLVER HEALTH — the superficial-loss solver converges.

Seeds are fixed, so failures are reproducible: a failure prints its
seed and the generated book. Set TAXJSON_FUZZ_BOOKS to run more books
(nightly-style); the default keeps the suite fast.
"""
import io
import json
import os
import random
import unittest
from contextlib import redirect_stderr

from taxjson.lib.core import TaxTransaction, get_tax_rules

# Default raised 25 → 200 (2026-09 round-four audit): mutation testing
# showed 10 surviving engine mutants that the SAME invariants kill at
# 200 books — the default depth was the gap, not the assertions.
# ~2s of extra wall clock in the full suite.
N_BOOKS = int(os.environ.get("TAXJSON_FUZZ_BOOKS", "200"))


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


class TestConservationFuzz(unittest.TestCase):
    """I1/I2/I4/I5 over N random fully-liquidated books, both engines."""

    def _check_book(self, seed, country):
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
        self.assertAlmostEqual(
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
                self.assertLess(
                    raw, 0.005,
                    f"disallowance on a raw gain: {g}\n{ctx}")
            # I4: signs.
            self.assertGreaterEqual(dis, -1e-9, ctx)
            self.assertGreaterEqual(
                float(g.get("permanently_disallowed") or 0), -1e-9,
                ctx)
        for r in wash.get("inventory") or []:
            self.assertGreaterEqual(
                float(r.get("deferred_wash") or 0), -1e-9,
                f"negative deferred_wash: {r}\n{ctx}")
        # I5: the solver settled.
        conv = wash.get("wash_solver_converged")
        if conv is not None:
            self.assertTrue(conv, f"solver did not converge\n{ctx}")

    def test_canada_fuzz(self):
        for seed in range(N_BOOKS):
            with self.subTest(seed=seed):
                self._check_book(seed, "canada")

    def test_usa_fuzz(self):
        for seed in range(N_BOOKS):
            with self.subTest(seed=seed):
                self._check_book(seed, "usa")


class TestOrderInvariance(unittest.TestCase):
    """I3: input order must not matter (the engine sorts internally)."""

    def test_shuffled_inputs_same_totals(self):
        for seed in range(min(N_BOOKS, 10)):
            with self.subTest(seed=seed):
                txs, shel = make_book(seed)
                a = totals(run_engine("canada", txs, shel))
                rng = random.Random(seed + 999)
                txs2 = list(txs)
                rng.shuffle(txs2)
                b = totals(run_engine("canada", txs2, shel))
                self.assertEqual(a, b,
                                 f"seed={seed}: order changed totals "
                                 f"{a} vs {b}")


class TestCraGoldenExamples(unittest.TestCase):
    """Authoritative hand-computed cases, cited — the oracle side of
    the rigour strategy: not what the engine says, what the LAW says."""

    def _run(self, txs, shel=()):
        return run_engine("canada", txs, list(shel))

    def _T(self, **kw):
        d = dict(action="BUYSELL", time="09:30:00", symbol="X.TO",
                 currency="CAD", account="margin")
        d.update(kw)
        return TaxTransaction(**d)

    def test_cra_full_denial_rebuy_still_held(self):
        # CRA T4037 shape: sell at a loss, repurchase the same number
        # within 30 days, still hold at +30 -> the ENTIRE loss is
        # superficial and is added to the replacement's ACB.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0),
               self._T(date="2025-03-17", quantity=100,
                       net_amount=8200.0),
               # Final sale AFTER the window at the bumped basis:
               # 8200 + 2000 deferred = 10200 ACB.
               self._T(date="2025-06-02", quantity=-100,
                       net_amount=9000.0)]
        r = self._run(txs)
        by_date = {g["date"]: g for g in r["transactions"]
                   if g.get("qty") and "gain" in g}
        self.assertAlmostEqual(
            float(by_date["2025-03-03"]["gain"]), 0.0, places=2)
        self.assertAlmostEqual(
            float(by_date["2025-03-03"]["disallowed_amount"]),
            2000.0, places=2)
        self.assertAlmostEqual(
            float(by_date["2025-06-02"]["gain"]),
            9000.0 - 10200.0, places=2)

    def test_cra_least_of_three_formula(self):
        # CRA's published partial formula: denied = loss x
        # min(S, P, B) / S.  S=40 sold, P=20 bought in window, B=10
        # held at +30 (an old sheltered holding) -> 10/40 denied.
        txs = [self._T(date="2025-01-06", quantity=20,
                       net_amount=44000.0),
               self._T(date="2025-01-16", quantity=10,
                       net_amount=24000.0),
               self._T(date="2025-01-22", quantity=10,
                       net_amount=23000.0),
               self._T(date="2025-01-30", quantity=-40,
                       net_amount=87000.0)]   # loss 4,000 = 100/sh
        shel = [self._T(date="2024-09-26", quantity=10,
                        net_amount=23000.0, account="resp")]
        r = self._run(txs, shel)
        denied = sum(float(w.get("disallowed_amount") or 0)
                     for w in r.get("wash_sales") or [])
        self.assertAlmostEqual(denied, 10 * 100.0, places=2)

    def test_cra_no_acquisition_no_denial(self):
        # s.54(a): mere affiliated OWNERSHIP is not an acquisition —
        # no buy in the window, loss fully allowed.
        txs = [self._T(date="2024-06-03", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0)]
        shel = [self._T(date="2024-09-02", quantity=50,
                        net_amount=5000.0, account="rrsp")]
        r = self._run(txs, shel)
        self.assertEqual(sum(
            float(w.get("disallowed_amount") or 0)
            for w in r.get("wash_sales") or []), 0)

    def test_full_group_exit_rescues(self):
        # In-window rebuy but the ENTIRE affiliated group is flat at
        # +30 (B=0): min(S,P,0)=0, the loss stands.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0),
               self._T(date="2025-03-10", quantity=50,
                       net_amount=4100.0),
               self._T(date="2025-03-20", quantity=-50,
                       net_amount=4150.0)]
        r = self._run(txs)
        self.assertEqual(sum(
            float(w.get("disallowed_amount") or 0)
            for w in r.get("wash_sales") or []), 0)


if __name__ == "__main__":
    unittest.main()


class TestBlendSplitDifferential(unittest.TestCase):
    """The pipeline computes ONE blended taxable pass then apportions
    per account (taxjson-split-gains). Differential law: nothing may
    be created or destroyed by the split —
      D1  every disposition lands in exactly one account's artifact;
      D2  sum of per-account gains == blended gains;
      D3  sum of per-account inventory quantities per symbol ==
          blended inventory (phantomless books).
    """

    def _split_all(self, combined, txs):
        # Mirror the pipeline exactly: each account is split against
        # its OWN base book (the pipeline passes {name}_base.json).
        from taxjson.bin.taxjson_split_gains import split_for_account
        accounts = sorted({t.account for t in txs})
        return {a: split_for_account(
                    combined, a,
                    [t.to_dict() for t in txs if t.account == a])
                for a in accounts}

    def test_differential(self):
        for seed in range(min(N_BOOKS, 50)):
            with self.subTest(seed=seed):
                txs, shel = make_book(seed)
                combined = run_engine("canada", txs, shel)
                # split_for_account consumes the SERIALIZED document
                # shape; round-trip through JSON like the pipeline.
                doc = json.loads(json.dumps(combined, default=str))
                parts = self._split_all(doc, txs)
                # D1/D2: dispositions and gains conserve.
                blended = [g for g in doc["transactions"]
                           if g.get("qty") and "gain" in g
                           and not g.get("tainted")]
                split_g = [g for p in parts.values()
                           for g in p.get("transactions") or []
                           if g.get("qty") and "gain" in g
                           and not g.get("tainted")]
                self.assertEqual(
                    len(blended), len(split_g),
                    f"seed={seed}: {len(blended)} blended dispositions "
                    f"vs {len(split_g)} across split artifacts")
                self.assertAlmostEqual(
                    sum(float(g["gain"] or 0) for g in blended),
                    sum(float(g["gain"] or 0) for g in split_g),
                    places=2, msg=f"seed={seed}: split changed totals")
                # D3: per-symbol inventory conserves.
                from collections import defaultdict
                b_inv = defaultdict(float)
                for r in doc.get("inventory") or []:
                    b_inv[r["symbol"]] += float(r["qty"] or 0)
                s_inv = defaultdict(float)
                for p in parts.values():
                    for r in p.get("inventory") or []:
                        s_inv[r["symbol"]] += float(r["qty"] or 0)
                for sym in set(b_inv) | set(s_inv):
                    self.assertAlmostEqual(
                        b_inv[sym], s_inv[sym], places=4,
                        msg=f"seed={seed}: {sym} inventory "
                            f"{b_inv[sym]} blended vs {s_inv[sym]} "
                            f"split")


class TestSettleLagSplitBoundaries(unittest.TestCase):
    """A SPLIT meeting a settle-lagged loss sale (round-four audit,
    finding 3 + the straddle guard). Book: buy 100 @$100; sell all on
    06-10 settling 06-12 (loss $2,000, $20/pre-split share); SPLIT 2:1;
    rebuy 50 POST-split shares (= 25 pre-split) on 06-15. Denial must
    be 25 pre-split shares x $20 = $500 wherever the book is legal."""

    def _book(self, split_date):
        def T(**kw):
            d = dict(action="BUYSELL", time="09:30:00", symbol="Q.TO",
                     currency="CAD", account="m")
            d.update(kw)
            return TaxTransaction(**d)
        return [T(date="2026-01-05", quantity=100, net_amount=10000.0),
                T(date="2026-06-10", date_settle="2026-06-12",
                  quantity=-100, net_amount=8000.0),
                TaxTransaction(action="SPLIT", date=split_date,
                               time="00:00:01", symbol="Q.TO",
                               quantity=2.0, currency="CAD",
                               account="m"),
                T(date="2026-06-15", quantity=50, net_amount=2000.0)]

    def _denied(self, split_date):
        r = run_engine("canada", self._book(split_date), [])
        return round(sum(float(w.get("disallowed_amount") or 0)
                         for w in r.get("wash_sales") or []), 2)

    def test_split_on_settle_date_denies_pre_split_units(self):
        # The loss row pre-exists its sort date, so the ref side of the
        # lineage conversion must apply the same-date split: without
        # ref_inclusive the denial doubled to $1,000.
        self.assertEqual(self._denied("2026-06-12"), 500.0)

    def test_split_after_settle_matches(self):
        self.assertEqual(self._denied("2026-06-13"), 500.0)

    def test_split_strictly_inside_settle_lag_redenominates(self):
        # Pool splits before the pre-split-denominated sale consumes
        # it. The engine used to refuse (and before the guard, booked
        # a +$3,000 "gain" with 150 phantom shares); it now
        # re-denominates the executed quantity through the straddled
        # split (qty x2, price /2, money unchanged) — the same $500
        # denial as every other split date.
        self.assertEqual(self._denied("2026-06-11"), 500.0)
        r = run_engine("canada", self._book("2026-06-11"), [])
        gains = [t for t in r["transactions"]
                 if t.get("qty") and "gain" in t]
        self.assertEqual(round(float(gains[0].get("raw_gain",
                                                  gains[0]["gain"])), 2)
                         + round(float(gains[0].get(
                             "disallowed_amount") or 0), 2),
                         -1500.0, gains)
        inv = {i["symbol"]: i["qty"] for i in r.get("inventory") or []}
        self.assertEqual(inv.get("Q.TO", 0), 50, "no phantom shares")

    def test_rename_split_inside_settle_lag_still_refuses(self):
        # A RENAME-split straddling the lag would need re-symboling
        # mid-flight — the loud refusal stays for that shape.
        from taxjson.lib.core import SplitStraddlesSettlementError
        book = self._book("2026-06-11")
        for t in book:
            if t.action == "SPLIT":
                book[book.index(t)] = TaxTransaction(
                    **{**t.to_dict(), "symbol_new": "QQ.TO"})
        with self.assertRaises(SplitStraddlesSettlementError):
            run_engine("canada", book, [])

    def test_us_engine_unaffected_by_straddle(self):
        # Trade-date ordering: the US engine books the straddle book
        # correctly and needs no guard.
        book = [TaxTransaction(**{**t.to_dict(),
                                  "symbol": "Q.US",
                                  "currency": "USD"})
                for t in self._book("2026-06-11")]
        r = run_engine("usa", book, [])
        gains = [t for t in r["transactions"]
                 if t.get("qty") and "gain" in t]
        self.assertEqual(len(gains), 1)
        self.assertEqual(round(float(gains[0]["gain"]), 2), -1500.0)


class TestWindowBoundaryGoldens(unittest.TestCase):
    """Exact-day boundaries of s.40(2)(g)'s ±30-day window, pinned at
    the ENGINE level (mutation testing showed `<= 30` could become
    `< 30` or `31` with no test noticing — the radar pins its own
    boundaries, but the engine's were unguarded)."""

    def _T(self, **kw):
        d = dict(action="BUYSELL", time="09:30:00", symbol="B.TO",
                 currency="CAD", account="m")
        d.update(kw)
        return TaxTransaction(**d)

    def _denied(self, rebuy_date, sell_all_by=None):
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0),           # the loss
               self._T(date=rebuy_date, quantity=100,
                       net_amount=8100.0)]
        if sell_all_by:
            txs.append(self._T(date=sell_all_by, quantity=-100,
                               net_amount=8200.0))
        r = run_engine("canada", txs, [])
        return round(sum(float(w.get("disallowed_amount") or 0)
                         for w in r.get("wash_sales") or []), 2)

    def test_rebuy_on_day_30_denies(self):
        # 2025-03-03 + 30d = 2025-04-02: still inside the window.
        self.assertGreater(self._denied("2025-04-02"), 0)

    def test_rebuy_on_day_31_allows(self):
        # 2025-03-03 + 31d = 2025-04-03: the FIRST safe day — pinning
        # exactly this day is what kills a 30->31 window mutant.
        self.assertEqual(self._denied("2025-04-03"), 0)

    def test_prebuy_on_day_minus_30_denies(self):
        # Buy 2025-02-01, loss 2025-03-03 (30 days later), still held.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-02-01", quantity=50,
                       net_amount=5000.0),
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0)]
        r = run_engine("canada", txs, [])
        self.assertGreater(round(sum(
            float(w.get("disallowed_amount") or 0)
            for w in r.get("wash_sales") or []), 2), 0)

    def test_full_exit_on_day_30_rescues(self):
        # Selling the rebought shares ON day +30 defeats still-held.
        self.assertEqual(
            self._denied("2025-03-10", sell_all_by="2025-04-02"), 0)

    def test_full_exit_on_day_31_too_late(self):
        self.assertGreater(
            self._denied("2025-03-10", sell_all_by="2025-04-03"), 0)

    def test_long_loss_with_short_balance_not_denied(self):
        # Direction gate: a LONG loss with the group net SHORT at +30
        # fails the still-held test — an `or` in that gate would deny.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0),           # long loss
               self._T(date="2025-03-10", quantity=-50,
                       net_amount=4000.0),           # go short
               self._T(date="2025-06-02", quantity=50,
                       net_amount=4000.0)]           # cover later
        r = run_engine("canada", txs, [])
        losses = [w for w in r.get("wash_sales") or []
                  if float(w.get("disallowed_amount") or 0) > 0]
        for w in losses:
            # Only the SHORT-side machinery may deny here; the 03-03
            # LONG loss must not be superficial.
            self.assertNotEqual(w.get("loss_tx_id"),
                                None)  # structural sanity
        by_date = {g["date"]: g for g in r["transactions"]
                   if g.get("qty") and "gain" in g}
        self.assertAlmostEqual(
            float(by_date["2025-03-03"].get("disallowed_amount") or 0),
            0.0, places=2)

    def test_crossing_loss_does_not_self_trigger(self):
        # A sale crossing long->short at a loss: the leftover opens a
        # short, but the sale must not act as its OWN replacement.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-150,
                       net_amount=12000.0),          # cross at a loss
               self._T(date="2025-06-02", quantity=50,
                       net_amount=4100.0)]           # cover way later
        r = run_engine("canada", txs, [])
        by_date = {g["date"]: g for g in r["transactions"]
                   if g.get("qty") and "gain" in g
                   and g["date"] == "2025-03-03"}
        g = by_date.get("2025-03-03")
        self.assertIsNotNone(g)
        self.assertAlmostEqual(
            float(g.get("disallowed_amount") or 0), 0.0, places=2)

    def test_postloss_trigger_allocated_first(self):
        # Two triggers, one pre-loss one post-loss: the post-loss buy
        # is the primary replacement and must carry the deferral (its
        # id first in replacement_lot_ids).
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-02-20", quantity=10,
                       net_amount=1000.0),           # pre-loss trigger
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0),           # loss
               self._T(date="2025-03-10", quantity=10,
                       net_amount=810.0)]            # post-loss trigger
        r = run_engine("canada", txs, [])
        rec = next(g for g in r["transactions"]
                   if g.get("qty") and g["date"] == "2025-03-03")
        reps = rec.get("replacement_lot_ids") or []
        self.assertTrue(reps, "expected replacement linkage")
        post_id = next(t.id for t in txs if t.date == "2025-03-10")
        self.assertEqual(reps[0], post_id,
                         "post-loss trigger must be primary")


class TestSolverMutantKillers(unittest.TestCase):
    """Goldens aimed at specific mutation survivors in the trigger
    scan and allocation logic."""

    def _T(self, **kw):
        d = dict(action="BUYSELL", time="09:30:00", symbol="B.TO",
                 currency="CAD", account="m")
        d.update(kw)
        return TaxTransaction(**d)

    def _denied_for(self, r, date):
        rec = next((g for g in r["transactions"]
                    if g.get("qty") and g["date"] == date), None)
        return round(float((rec or {}).get("disallowed_amount") or 0),
                     2)

    def test_day31_rebuy_with_retained_shares_still_allowed(self):
        # Partial loss sale (50 of 100 kept -> still-held is satisfied
        # by the RETAINED shares); the only buy is on day +31. A
        # window widened by one day would deny — the retained balance
        # means the still-held bound cannot mask the widening.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-50,
                       net_amount=4000.0),
               self._T(date="2025-04-03", quantity=10,
                       net_amount=820.0)]
        r = run_engine("canada", txs, [])
        self.assertEqual(self._denied_for(r, "2025-03-03"), 0.0)

    def test_long_loss_with_long_trigger_but_short_balance(self):
        # Account A crosses to net short at a loss; account B opens a
        # small LONG in-window (a real trigger). Balance at +30 is
        # net SHORT, so the LONG loss fails still-held and must not
        # be denied — an or-mutant of the direction gate denies it.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0, account="A"),
               self._T(date="2025-03-03", quantity=-150,
                       net_amount=11000.0, account="A"),
               self._T(date="2025-03-10", quantity=10,
                       net_amount=800.0, account="B"),
               self._T(date="2025-06-02", quantity=40,
                       net_amount=3000.0, account="A")]
        r = run_engine("canada", txs, [])
        self.assertEqual(self._denied_for(r, "2025-03-03"), 0.0)

    def test_other_symbol_buy_is_never_a_trigger(self):
        # Identical property means THE SAME symbol class: a C.TO buy
        # inside B.TO's loss window is not a replacement.
        # RETAIN 50 B.TO so the still-held bound is satisfied — the
        # only thing standing between the C.TO buy and a denial is
        # the identical-property test itself.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-03-03", quantity=-50,
                       net_amount=4000.0),
               self._T(date="2025-03-10", quantity=100,
                       net_amount=8100.0, symbol="C.TO"),
               self._T(date="2025-06-02", quantity=-100,
                       net_amount=8200.0, symbol="C.TO")]
        r = run_engine("canada", txs, [])
        self.assertEqual(self._denied_for(r, "2025-03-03"), 0.0)

    def test_same_date_later_time_is_postloss_primary(self):
        # Post/pre-loss classification tie-breaks on intra-day time:
        # a buy later the SAME day is a post-loss (primary)
        # replacement and carries the deferral.
        txs = [self._T(date="2025-01-06", quantity=100,
                       net_amount=10000.0),
               self._T(date="2025-02-20", quantity=10,
                       net_amount=1000.0),
               self._T(date="2025-03-03", quantity=-100,
                       net_amount=8000.0, time="09:30:00"),
               self._T(date="2025-03-03", quantity=10,
                       net_amount=810.0, time="15:00:00")]
        r = run_engine("canada", txs, [])
        rec = next(g for g in r["transactions"]
                   if g.get("qty") and g["date"] == "2025-03-03"
                   and float(g["qty"]) == 100.0)
        reps = rec.get("replacement_lot_ids") or []
        self.assertTrue(reps)
        same_day_id = next(t.id for t in txs
                           if t.date == "2025-03-03"
                           and t.quantity == 10)
        self.assertEqual(reps[0], same_day_id)


class TestUsRenameSplitConservation(unittest.TestCase):
    """Regression: a RENAME-split (symbol_new set AND ratio != 1) used
    to re-sort the migrated FIFO lots by effective_acq_date — a field
    that §1223(3) tacking rewrites to a fictitious earlier date, and
    only when wash detection is on. The post-rename sale then consumed
    DIFFERENT shares in the wash and no-wash runs, breaking the
    conservation identity by exactly the swapped shares' plain-basis
    difference. FIFO must follow actual acquisition order
    (Reg. 1.1012-1(c)); tacking adjusts holding period only."""

    def _T(self, **kw):
        d = dict(action="BUYSELL", time="09:30:00", symbol="X.TO",
                 currency="CAD", account="A1")
        d.update(kw)
        return TaxTransaction(**d)

    def test_conservation_across_rename_split(self):
        txs = [
            # A1 long since early January — held long enough that the
            # §1223(3) tack pulls the replacement lot's effective date
            # BEFORE A2's first (cheap) lot's actual date.
            self._T(date="2025-01-06", quantity=50, net_amount=2500.0),
            # A2's cheap early lot (outside the loss window).
            self._T(date="2025-02-13", quantity=50, net_amount=500.0,
                    account="A2"),
            # A2's in-window replacement (expensive).
            self._T(date="2025-03-01", quantity=50, net_amount=1800.0,
                    account="A2"),
            # A1 sells at a 1000 loss; the 03-01 A2 buy is the §1091
            # replacement — the basis bump AND the tack land on it.
            self._T(date="2025-03-26", quantity=-50,
                    net_amount=1500.0),
        ]
        # 3:1 RENAME-split, one broker copy per account (the engine
        # dedupes per corporate event).
        for acct in ("A1", "A2"):
            txs.append(TaxTransaction(
                action="SPLIT", date="2025-04-07", time="00:00:01",
                symbol="X.TO", symbol_new="XN.TO", quantity=3.0,
                currency="CAD", account=acct))
        # Post-rename PARTIAL sale: deferred basis must stay parked on
        # the unsold shares, not leak into realized gains.
        txs.append(self._T(date="2025-04-08", quantity=-65.0,
                           net_amount=2600.0, symbol="XN.TO",
                           account="A2"))

        wash = run_engine("usa", txs, [], per_account_basis=True)
        nowash = run_engine("usa", txs, [], per_account_basis=True,
                            detect_wash_sales=False)
        w_total, w_perm = totals(wash)
        n_total, _ = totals(nowash)
        parked = round(sum(float(r.get("deferred_wash") or 0.0)
                           for r in wash.get("inventory") or []), 2)
        # Sanity: the wash machinery actually fired on the 03-26 loss
        # (guards the assertion below against a silent no-match pass).
        self.assertAlmostEqual(round(sum(
            float(g.get("disallowed_amount") or 0)
            for g in wash["transactions"]
            if g.get("qty") and "gain" in g), 2), 1000.0, places=2)
        # I1: realized(wash) - parked deferrals - permanent denials
        # == realized(no-wash), to the cent.
        self.assertAlmostEqual(
            round(w_total - parked, 2), round(n_total + w_perm, 2),
            places=2,
            msg=f"rename-split conservation: wash={w_total} "
                f"parked={parked} nowash={n_total} perm={w_perm}")


class TestCrossPoolDeferralRouting(unittest.TestCase):
    """Fuzz seed 2183 (round-four audit): loss realized on S0 while the
    still-held substituted property sits entirely in sibling pool S2 of
    the alias class (united by a LATER rename-merge S2→S0). The
    deferral ADJUST used to land on the trigger's own — empty — S0
    pool, parking $413.66 where nothing could consume it: the denied
    loss vanished from conservation. The bump must land on the pool
    that actually holds backing at +30 and recover into its sales."""

    BOOK = [
        dict(action="BUYSELL", date="2025-02-12", time="15:54:00",
             symbol="S0.TO", quantity=50.0, currency="CAD",
             net_amount=772.96, account="A0"),
        dict(action="BUYSELL", date="2025-02-14", time="09:16:00",
             symbol="S0.TO", quantity=-50.0, currency="CAD",
             net_amount=359.30, account="A0"),
        dict(action="BUYSELL", date="2025-02-12", time="14:54:00",
             symbol="S2.TO", quantity=100.0, currency="CAD",
             net_amount=4924.29, account="A1"),
        dict(action="BUYSELL", date="2025-02-25", time="09:30:00",
             symbol="S2.TO", quantity=-25.0, currency="CAD",
             net_amount=287.77, account="A1"),
        dict(action="BUYSELL", date="2025-02-28", time="09:15:00",
             symbol="S2.TO", quantity=-10.0, currency="CAD",
             net_amount=301.63, account="A1"),
        dict(action="BUYSELL", date="2025-03-06", time="09:30:00",
             symbol="S2.TO", quantity=-25.0, currency="CAD",
             net_amount=1210.79, account="A1"),
        dict(action="BUYSELL", date="2025-03-18", time="09:30:00",
             symbol="S2.TO", quantity=100.0, currency="CAD",
             net_amount=3222.48, account="A1"),
        dict(action="BUYSELL", date="2025-04-05", time="10:28:00",
             symbol="S2.TO", quantity=-140.0, currency="CAD",
             net_amount=5999.73, account="A1"),
        dict(action="SPLIT", date="2025-04-08", time="00:00:01",
             symbol="S2.TO", symbol_new="S0.TO", quantity=3.0,
             currency="CAD", account="A0"),
    ]

    def _run(self, wash):
        txs = [TaxTransaction(**r) for r in self.BOOK]
        with redirect_stderr(io.StringIO()):
            return get_tax_rules("canada").compute_gains(
                txs, detect_wash_sales=wash)

    def test_deferral_conserves_through_sibling_pool(self):
        rw, rn = self._run(True), self._run(False)
        def tot(r):
            return sum(float(t.get("gain") or 0)
                       for t in r["transactions"]
                       if t.get("qty") and "gain" in t)
        deferred = sum(float(i.get("deferred_wash") or 0)
                       for i in rw.get("inventory") or [])
        # Everything is flat by book end: the denied loss must be
        # fully recovered, not parked or vanished.
        self.assertAlmostEqual(deferred, 0.0, places=2)
        self.assertAlmostEqual(tot(rw), tot(rn), places=2)

    def test_s0_denial_still_fires(self):
        rw = self._run(True)
        denials = [round(float(w.get("disallowed_amount") or 0), 2)
                   for w in rw.get("wash_sales") or []]
        self.assertIn(413.66, denials, denials)


class TestMergeIntoLiveSymbol(unittest.TestCase):
    """Regression, 2026-09 adversarial audit finding 1: a rename-split
    A->B (ratio r) whose target B ALREADY TRADES. B-holders' shares are
    never scaled by the event, but the Canada engine's class-wide
    `alias_factor` loss-unit conversion pooled every ratio of the
    rename class and divided the whole still-held balance (and any
    post-event trigger quantity) by r anyway — wrong denial amounts in
    both directions. The fix converts each row with a per-raw-symbol
    lineage factor (SplitTimeline.lineage_factor)."""

    def _T(self, **kw):
        d = dict(action="BUYSELL", time="09:30:00", symbol="B.TO",
                 currency="CAD", account="m")
        d.update(kw)
        return TaxTransaction(**d)

    def _book(self, ratio, a_qty, sell_amount, rebuy_qty, rebuy_date):
        return [
            self._T(date="2025-01-06", quantity=100,
                    net_amount=10000.0),
            self._T(date="2025-03-03", quantity=-100,
                    net_amount=sell_amount),          # the loss sale
            self._T(date=rebuy_date, quantity=float(rebuy_qty),
                    net_amount=81.0 * rebuy_qty),     # rebuy, held
            # Separately-held A.TO, renaming A->B INSIDE the window.
            self._T(date="2025-01-06", quantity=float(a_qty),
                    net_amount=30.0 * a_qty, symbol="A.TO"),
            TaxTransaction(action="SPLIT", date="2025-03-20",
                           time="00:00:01", symbol="A.TO",
                           symbol_new="B.TO", quantity=float(ratio),
                           currency="CAD", account="m"),
        ]

    def _denied(self, r):
        return round(sum(float(w.get("disallowed_amount") or 0)
                         for w in r.get("wash_sales") or []), 2)

    def _assert_conserved(self, txs):
        wash = run_engine("canada", txs, [])
        nowash = run_engine("canada", txs, [],
                            detect_wash_sales=False)
        w_total, w_perm = totals(wash)
        n_total, _ = totals(nowash)
        parked = round(sum(float(r.get("deferred_wash") or 0.0)
                           for r in wash.get("inventory") or []), 2)
        self.assertAlmostEqual(
            round(w_total - parked, 2), round(n_total + w_perm, 2),
            places=2,
            msg=f"merge-into-live conservation: wash={w_total} "
                f"parked={parked} nowash={n_total} perm={w_perm}")

    def test_ratio_2_full_denial(self):
        # $2,000 loss; 100 B rebought in-window and held (plus 40 A
        # -> 80 B through the merge). B shares never scaled, so the
        # FULL loss is denied. Pre-fix: the 2:1 merge ratio halved
        # the class balance to 90 -> $1,800 denied, gain -200 filed.
        txs = self._book(2.0, a_qty=40, sell_amount=8000.0,
                         rebuy_qty=100, rebuy_date="2025-03-10")
        r = run_engine("canada", txs, [])
        self.assertAlmostEqual(self._denied(r), 2000.0, places=2)
        loss_rec = next(g for g in r["transactions"]
                        if g.get("qty") and g["date"] == "2025-03-03")
        self.assertAlmostEqual(float(loss_rec["gain"]), 0.0, places=2)
        self._assert_conserved(txs)

    def test_ratio_half_partial_denial_not_inflated(self):
        # $1,000 loss on 100 sold; only 50 B rebought (after the
        # 1-for-2 merge date) and held -> denied = 50/100 = $500.
        # Pre-fix: the class factor doubled both the trigger quantity
        # and the balance, denying the full $1,000 (over-denial).
        txs = self._book(0.5, a_qty=100, sell_amount=9000.0,
                         rebuy_qty=50, rebuy_date="2025-03-25")
        r = run_engine("canada", txs, [])
        self.assertAlmostEqual(self._denied(r), 500.0, places=2)
        self._assert_conserved(txs)

    def test_ratio_2_post_merge_trigger_not_halved(self):
        # Same as the full-denial shape but the rebuy lands AFTER the
        # merge date: the trigger's 100 B shares were not scaled by
        # A's merge, so the full $2,000 is still denied. Pre-fix the
        # per-trigger conversion halved acquired_qty -> $1,000.
        txs = self._book(2.0, a_qty=40, sell_amount=8000.0,
                         rebuy_qty=100, rebuy_date="2025-03-25")
        r = run_engine("canada", txs, [])
        self.assertAlmostEqual(self._denied(r), 2000.0, places=2)
        self._assert_conserved(txs)
