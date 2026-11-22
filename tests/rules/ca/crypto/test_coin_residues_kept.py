"""Deferred round, engine-t1135 area: the T1135 cost walk carries every
s.53(1)(f) addition (S008-07, S009-01, S051-21).

Formerly: test_fix_x_engine_t1135"""
import contextlib
import io
import os
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


def _t(i, d, sym, q, p, account="margin", time="10:00:00", settle=None):
    amt = abs(q * p)
    return dict(id=i, date=d, date_settle=settle or d, time=time,
                action="BUYSELL", symbol=sym, quantity=q, price=p,
                gross_amount=amt, net_amount=-amt if q > 0 else amt,
                commission=0.0, fee=0.0, currency="CAD", account=account)


def _canada(rows):
    from taxjson.engine.ca.taxrules import CanadaTaxRules
    from taxjson.lib.tx_load import coerce_transaction_row
    txs = [coerce_transaction_row(r, i, "t") for i, r in enumerate(rows)]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(txs)
    return {i["symbol"]: i for i in res["inventory"]}, res


class TestCryptoResidueKept(unittest.TestCase):
    """S069-13: a coin residue under a millionth stays a holding with its
    own cost; float noise is still absorbed, and share pools keep the
    millionth-of-a-share tolerance.

    Formerly: test_fix_x_engine_t1135.TestCryptoResidueKept"""

    @rule(R.CA_CRYPTO_09)
    def test_sub_micro_residue_stays_with_its_cost(self):
        rows = [_t("b1", "2025-01-06", "BTC", 1.0000009, 100000.0),
                _t("s1", "2025-03-03", "BTC", -1, 120000.0),
                _t("b2", "2025-05-05", "BTC", 0.5, 120000.0)]
        rows[0]["net_amount"] = -100000.09
        rows[0]["gross_amount"] = 100000.09
        inv, _res = _canada(rows)
        self.assertAlmostEqual(inv["BTC"]["qty"], 0.5000009, places=12)
        self.assertAlmostEqual(inv["BTC"]["total_cost"], 60000.09, places=4)

    @rule(R.CA_CRYPTO_09)
    def test_residue_alone_is_listed(self):
        rows = [_t("b1", "2025-01-06", "BTC", 1.0000009, 100000.0),
                _t("s1", "2025-03-03", "BTC", -1, 120000.0)]
        rows[0]["net_amount"] = -100000.09
        inv, _res = _canada(rows)
        self.assertIn("BTC", inv)
        self.assertAlmostEqual(inv["BTC"]["qty"], 9e-7, places=12)
        self.assertAlmostEqual(inv["BTC"]["total_cost"], 0.09, places=4)

    @rule(R.CA_CRYPTO_09)
    def test_float_noise_still_drains(self):
        rows = [_t("b1", "2025-01-06", "ETH", 0.1, 3000.0),
                _t("b2", "2025-01-07", "ETH", 0.2, 3000.0),
                _t("s1", "2025-03-03", "ETH", -0.3, 3500.0),
                _t("b3", "2025-06-03", "ETH", 1.0, 2000.0)]
        inv, _res = _canada(rows)
        self.assertEqual(inv["ETH"]["qty"], 1.0)
        self.assertAlmostEqual(inv["ETH"]["total_cost"], 2000.0, places=6)

    @rule(R.CA_CRYPTO_09)
    def test_share_pool_keeps_the_millionth_tolerance(self):
        rows = [_t("b1", "2025-01-06", "XYZ.US", 100.0000005, 10.0),
                _t("s1", "2025-03-03", "XYZ.US", -100, 12.0)]
        inv, _res = _canada(rows)
        self.assertNotIn("XYZ.US", inv)


class TestCoinPoolFuzz(unittest.TestCase):
    """S069-13 audit: random coin books with 8-decimal quantities, full
    exits (float noise), residues of 1e-8..1e-6 coins and sheltered
    buys. Units are conserved exactly (a residue stays, noise drains),
    cost is conserved with and without the wash pass, and no pool is
    left empty with stranded cost. TAXJSON_FUZZ_BOOKS books (default
    200; the audit ran 2000+).

    Formerly: test_fix_x_engine_t1135.TestCoinPoolFuzz"""

    N = int(os.environ.get("TAXJSON_FUZZ_BOOKS", "200"))

    @staticmethod
    def _book(seed):
        import random
        from datetime import date, timedelta
        rng = random.Random(seed)
        coins = rng.sample(["BTC", "ETH", "SOL", "ADA"], rng.randint(1, 3))
        base = date(2025, 1, 6)
        rows, shel, n = [], [], 0
        for coin in coins:
            for acct in ("ex1", "ex2")[:rng.randint(1, 2)]:
                pos, day = 0.0, rng.randint(0, 20)
                for _ in range(rng.randint(3, 9)):
                    day += rng.randint(1, 25)
                    d = (base + timedelta(days=day)).isoformat()
                    price = rng.uniform(10.0, 1000.0)
                    r = rng.random()
                    if pos <= 0 or r < 0.45:
                        q = rng.randint(1, 3 * 10**8) / 1e8
                    elif r < 0.65:
                        q = -pos                     # full exit: noise
                    elif r < 0.85:                   # leave a residue
                        q = -(pos - rng.randint(1, 100) * 1e-8)
                        if q >= 0:
                            q = -pos
                    else:
                        q = -round(pos * rng.uniform(0.1, 0.9), 8)
                    n += 1
                    rows.append(_t(f"r{n}", d, coin, q, price,
                                   account=acct, time=f"1{n % 10}:00:00"))
                    pos += q
                if rng.random() < 0.3:
                    n += 1
                    sd = (base + timedelta(days=day + rng.randint(-20, 20))
                          ).isoformat()
                    shel.append(_t(f"r{n}", sd, coin,
                                   rng.randint(1, 10**8) / 1e8, 50.0,
                                   account="tfsa"))
        return rows, shel

    def _run(self, rows, shel, wash):
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        from taxjson.lib.tx_load import coerce_transaction_row
        txs = [coerce_transaction_row(r, i, "t") for i, r in enumerate(rows)]
        sh = [coerce_transaction_row(r, i, "s") for i, r in enumerate(shel)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = CanadaTaxRules().compute_gains(
                txs, sheltered_transactions=sh, detect_wash_sales=wash)
        return res, err.getvalue()

    @rule(R.CA_CRYPTO_09)
    def test_coin_books_conserve_units_and_cost(self):
        from taxjson.lib.pool_eps import pool_qty_eps
        for seed in range(self.N):
            rows, shel = self._book(seed)
            for wash in (False, True):
                res, err = self._run(rows, shel, wash)
                ctx = f"seed={seed} wash={wash}"
                self.assertNotIn("stranded basis", err, ctx)
                inv = {i["symbol"]: i for i in res["inventory"]}
                for coin in {r["symbol"] for r in rows}:
                    qs = [r["quantity"] for r in rows if r["symbol"] == coin]
                    pos = scale = 0.0
                    for q in qs:
                        pos += q
                        scale = max(scale, abs(pos))
                    held = inv.get(coin, {}).get("qty", 0.0)
                    if abs(pos) < pool_qty_eps(coin, scale):
                        self.assertEqual(held, 0.0, ctx)
                    else:
                        self.assertAlmostEqual(held, pos, delta=1e-9,
                                               msg=ctx)
                if not wash:
                    bought = sum(-r["net_amount"] for r in rows
                                 if r["quantity"] > 0)
                    sold = sum(g["cost"] for g in res["transactions"]
                               if g.get("qty") and "gain" in g)
                    left = sum(i["total_cost"] for i in res["inventory"])
                    self.assertAlmostEqual(bought - sold, left, delta=0.01,
                                           msg=ctx)
                else:
                    gain = sum(float(g["gain"]) for g in res["transactions"]
                               if g.get("qty") and "gain" in g)
                    perm = sum(float(g.get("permanently_disallowed") or 0)
                               for g in res["transactions"]
                               if g.get("qty") and "gain" in g)
                    parked = sum(float(i.get("deferred_wash") or 0)
                                 for i in res["inventory"])
                    nowash, _e = self._run(rows, shel, False)
                    n_gain = sum(float(g["gain"])
                                 for g in nowash["transactions"]
                                 if g.get("qty") and "gain" in g)
                    self.assertAlmostEqual(gain - parked, n_gain + perm,
                                           delta=0.05, msg=ctx)


if __name__ == "__main__":
    unittest.main()
