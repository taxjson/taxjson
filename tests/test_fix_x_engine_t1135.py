"""Deferred round, engine-t1135 area: the T1135 cost walk carries every
s.53(1)(f) addition (S008-07, S009-01, S051-21)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent


def _t(i, d, sym, q, p, account="margin", time="10:00:00", settle=None):
    amt = abs(q * p)
    return dict(id=i, date=d, date_settle=settle or d, time=time,
                action="BUYSELL", symbol=sym, quantity=q, price=p,
                gross_amount=amt, net_amount=-amt if q > 0 else amt,
                commission=0.0, fee=0.0, currency="CAD", account=account)


def _gains(base: Path, year: int, out: Path, *extra) -> dict:
    g = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_gains", "--country",
         "canada", "--year", str(year), "--taxable", *extra, str(base)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    assert g.returncode == 0, g.stderr[-2000:]
    out.write_text(g.stdout)
    return json.loads(g.stdout)


def _canada(rows):
    from taxjson.lib.core import CanadaTaxRules, coerce_transaction_row
    txs = [coerce_transaction_row(r, i, "t") for i, r in enumerate(rows)]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(txs)
    return {i["symbol"]: i for i in res["inventory"]}, res


class TestCryptoResidueKept(unittest.TestCase):
    """S069-13: a coin residue under a millionth stays a holding with its
    own cost; float noise is still absorbed, and share pools keep the
    millionth-of-a-share tolerance."""

    @rule("CA-CRYPTO-09")
    def test_sub_micro_residue_stays_with_its_cost(self):
        rows = [_t("b1", "2025-01-06", "BTC", 1.0000009, 100000.0),
                _t("s1", "2025-03-03", "BTC", -1, 120000.0),
                _t("b2", "2025-05-05", "BTC", 0.5, 120000.0)]
        rows[0]["net_amount"] = -100000.09
        rows[0]["gross_amount"] = 100000.09
        inv, _res = _canada(rows)
        self.assertAlmostEqual(inv["BTC"]["qty"], 0.5000009, places=12)
        self.assertAlmostEqual(inv["BTC"]["total_cost"], 60000.09, places=4)

    @rule("CA-CRYPTO-09")
    def test_residue_alone_is_listed(self):
        rows = [_t("b1", "2025-01-06", "BTC", 1.0000009, 100000.0),
                _t("s1", "2025-03-03", "BTC", -1, 120000.0)]
        rows[0]["net_amount"] = -100000.09
        inv, _res = _canada(rows)
        self.assertIn("BTC", inv)
        self.assertAlmostEqual(inv["BTC"]["qty"], 9e-7, places=12)
        self.assertAlmostEqual(inv["BTC"]["total_cost"], 0.09, places=4)

    @rule("CA-CRYPTO-09")
    def test_float_noise_still_drains(self):
        rows = [_t("b1", "2025-01-06", "ETH", 0.1, 3000.0),
                _t("b2", "2025-01-07", "ETH", 0.2, 3000.0),
                _t("s1", "2025-03-03", "ETH", -0.3, 3500.0),
                _t("b3", "2025-06-03", "ETH", 1.0, 2000.0)]
        inv, _res = _canada(rows)
        self.assertEqual(inv["ETH"]["qty"], 1.0)
        self.assertAlmostEqual(inv["ETH"]["total_cost"], 2000.0, places=6)

    @rule("CA-CRYPTO-09")
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
    200; the audit ran 2000+)."""

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
        from taxjson.lib.core import CanadaTaxRules, coerce_transaction_row
        txs = [coerce_transaction_row(r, i, "t") for i, r in enumerate(rows)]
        sh = [coerce_transaction_row(r, i, "s") for i, r in enumerate(shel)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = CanadaTaxRules().compute_gains(
                txs, sheltered_transactions=sh, detect_wash_sales=wash)
        return res, err.getvalue()

    @rule("CA-CRYPTO-09")
    def test_coin_books_conserve_units_and_cost(self):
        from taxjson.lib.core import pool_qty_eps
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


class TestT1135EarlierYearDenial(unittest.TestCase):
    """A superficial loss denied in an EARLIER year stays in the
    replacement's ACB, so it stays in its T1135 cost amount."""

    def _report(self, rows, year, sheltered=None, **kw):
        from taxjson.bin.taxjson_t1135 import build_report
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": rows}))
            extra = []
            sh_paths = []
            if sheltered is not None:
                sh = Path(tmp) / "sheltered_base.json"
                sh.write_text(json.dumps({"transactions": sheltered}))
                extra = ["--sheltered", str(sh)]
                sh_paths = [sh]
            gains = Path(tmp) / "margin_gains_wash.json"
            doc = _gains(base, year, gains, *extra)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rep = build_report([base], [gains], year, {}, "CAD",
                                   sheltered_paths=sh_paths, **kw)
        return rep, doc

    @rule("CA-RPT-12")
    def test_s008_07_denial_in_prior_year_lifts_the_threshold(self):
        """S008-07: buy 1000 @120, sell @90, rebuy @90 within 30 days,
        all in 2025. The 2026 cost amount is 120,000 — over 100,000."""
        rows = [_t("b1", "2025-01-06", "XYZ.US", 1000, 120),
                _t("s1", "2025-06-02", "XYZ.US", -1000, 90),
                _t("b2", "2025-06-09", "XYZ.US", 1000, 90)]
        rep, doc = self._report(rows, 2026)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 120000.0, 2)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 120000.0)
        self.assertEqual(p["max_cost"], 120000.0)
        self.assertEqual(rep["max_total_cost"], 120000.0)
        self.assertTrue(rep["filing_required"])
        self.assertEqual(rep["deferred_wash_not_in_cost"], {})

    @rule("CA-RPT-12")
    def test_s009_01_partial_sale_and_later_year(self):
        """S009-01 / S051-21: the addition leaves with the shares sold
        (average cost), the rest stays in the Dec-31 cost of a later
        year — equal to the engine's inventory ACB."""
        rows = [_t("b1", "2024-03-01", "AAPL.US", 100, 200),
                _t("s1", "2024-11-20", "AAPL.US", -100, 100),
                _t("b2", "2024-12-02", "AAPL.US", 100, 100),
                _t("s2", "2025-04-01", "AAPL.US", -40, 150)]
        rep, doc = self._report(rows, 2025)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        (p,) = [r for r in rep["properties"] if r["symbol"] == "AAPL.US"]
        self.assertAlmostEqual(inv["AAPL.US"]["total_cost"], 12000.0, 2)
        self.assertEqual(p["year_end_cost"], 12000.0)      # 60 x 200
        self.assertEqual(p["max_cost"], 20000.0)           # Jan 1: 100 x 200

    @rule("CA-RPT-12")
    def test_replacement_bought_before_the_loss_in_prior_year(self):
        """The engine applies a pre-loss replacement's bump right after
        the losing sale; the walk replays it there."""
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("b2", "2024-11-01", "XYZ.US", 100, 5),
                _t("s1", "2024-11-08", "XYZ.US", -100, 5)]
        rep, doc = self._report(rows, 2025)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertGreater(inv["XYZ.US"]["deferred_wash"], 0)
        self.assertEqual(p["year_end_cost"],
                         round(inv["XYZ.US"]["total_cost"], 2))

    @rule("CA-RPT-12")
    def test_sheltered_replacement_adds_nothing(self):
        """A replacement bought in a registered account makes the loss
        permanently denied: no addition to the taxable cost."""
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("s1", "2024-11-08", "XYZ.US", -50, 5)]
        sh = [_t("r1", "2024-11-15", "XYZ.US", 50, 5, account="tfsa")]
        rep, doc = self._report(rows, 2025, sheltered=sh)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 500.0)
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 500.0, 2)


class TestT1135WrapperFullHistory(unittest.TestCase):
    """`taxjson t1135` in a project: the 2025 denial reaches the 2026
    cost columns."""

    @rule("CA-RPT-12")
    def test_project_year_after_the_denial(self):
        book = ("BUYSELL 2025-01-06 10:00:00 XYZ.US 1000 CAD 120 120000 0\n"
                "BUYSELL 2025-06-02 10:00:00 XYZ.US -1000 CAD 90 90000 0\n"
                "BUYSELL 2025-06-09 10:00:00 XYZ.US 1000 CAD 90 90000 0\n")
        env = dict(os.environ, TAXJSON_OFFLINE="1",
                   PYTHONPATH=str(REPO / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "book.tt").write_text(book)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')

            def cli(*args):
                return subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                     str(root), *args], capture_output=True, text=True,
                    env=env, stdin=subprocess.DEVNULL)
            r = cli("run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = cli("t1135", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            rep = json.loads(r.stdout)
        (p,) = [x for x in rep["properties"] if x["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 120000.0)
        self.assertTrue(rep["filing_required"])


if __name__ == "__main__":
    unittest.main()
