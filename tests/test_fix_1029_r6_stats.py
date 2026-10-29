"""Fix round 2026-10-29 (round 6), group stats: the pooled put premium
of `stats` / `win-rates` (lib/trade_stats pooled_put_credits, added for
TJ-0546, extended by TJ-0624).

TJ-0673  a put assigned in several share deliveries brought its whole
         premium into the pool at the first delivery: a sale between
         the deliveries carried all of it. Each delivery now brings its
         own part, per share; the last one what is left.
TJ-0682  a split recorded by one account of the blended pool split
         that account's shares only: the premium's part of a sale was
         figured on too few shares. A split is the security's: it
         splits the shares of every account of the pool, as the books'
         one pool does.
TJ-0683  the pool was walked on trade dates: a purchase traded before a
         sale but settling after it took part of the premium the sale
         carried. The pool is now walked in the books' own order
         (settlement date first, lib/corporate_timeline event_sort_key).

Every fixture is synthetic: invented tickers (ZZ*), account names and
amounts.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

PUT = "ZZQ250117P00001000.TO"         # strike 1, expires 2025-01-17


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _r(acct, d, sym, q, net, action="BUYSELL", rid=None, **kw):
    mult = 100 if sym == PUT else 1
    return dict(dict(date=d, time="10:00:00", account=acct, symbol=sym,
                     action=action, quantity=q, net_amount=net,
                     price=abs(net) / abs(q) / mult if net else 0,
                     currency="CAD", multiplier=mult,
                     **({"id": rid} if rid else {})), **kw)


def _credits(pool, covers=None):
    from taxjson.lib import trade_stats as TS
    puts = [c for rows in pool.values()
            for c in TS.written_option_trades(rows, lambda t: t["date"])[
                "credits"] if c.get("right") != "C"]
    return {k: round(v, 6)
            for k, v in TS.pooled_put_credits(pool, puts, covers).items()}


def _project(td: str, accounts=("margin",)) -> Path:
    root = Path(td) / "p"
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    return root


def _stats(root: Path, *args: str) -> dict:
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr[-2000:]
    r = _cli(root, "stats", "2025", "--json", *args)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def _sale_gain(root: Path, acct: str) -> list:
    doc = json.loads((root / "work" / f"{acct}_gains_wash.json").read_text())
    return [(t["qty"], round(t["cost"], 6), round(t["gain"], 6))
            for t in doc["transactions"] if "gain" in t]


# --------------------------------------------------------------------
# A put delivered in parts (TJ-0673)
# --------------------------------------------------------------------

class TestAPutDeliveredInParts(unittest.TestCase):
    """TJ-0673: 2 puts written for 20, their 200 shares delivered 100
    on Jan 17 and 100 on Jan 21; the first 100 sold at the strike on
    Jan 20, between the deliveries. Its cost is 90 (its 10 of the
    premium): the sale carries 10, not 20."""

    def _book(self):
        return [_r("margin", "2025-01-02", PUT, -2, 20, rid="write"),
                _r("margin", "2025-01-17", PUT, 2, 0, "ASSIGN", rid="asg"),
                _r("margin", "2025-01-17", "ZZQ.TO", 100, 100, "ASSIGN",
                   rid="dlv1"),
                _r("margin", "2025-01-20", "ZZQ.TO", -100, 100, rid="sale"),
                _r("margin", "2025-01-21", "ZZQ.TO", 100, 100, "ASSIGN",
                   rid="dlv2")]

    @rule("CA-RPT-16")
    def test_each_delivery_brings_its_part(self):
        book = self._book()
        self.assertEqual(_credits({"margin": book}),
                         {("margin", "sale"): 10.0})
        # The second 100 sold later carry the other 10.
        book.append(_r("margin", "2025-01-24", "ZZQ.TO", -100, 100,
                       rid="sale2"))
        self.assertEqual(_credits({"margin": book}),
                         {("margin", "sale"): 10.0,
                          ("margin", "sale2"): 10.0})

    @rule("CA-RPT-16")
    def test_a_smaller_last_delivery_takes_the_rest(self):
        # 150 shares delivered for the 200 (an adjusted deliverable):
        # the first 100 bring 10, the last 50 the other 10 (the books'
        # last leg takes what is left).
        book = self._book()
        book[-1] = _r("margin", "2025-01-21", "ZZQ.TO", 50, 50, "ASSIGN",
                      rid="dlv2")
        book.append(_r("margin", "2025-01-24", "ZZQ.TO", -50, 50,
                       rid="sale2"))
        self.assertEqual(_credits({"margin": book}),
                         {("margin", "sale"): 10.0,
                          ("margin", "sale2"): 10.0})

    @rule("CA-RPT-16")
    def test_a_delivery_that_covers_a_short(self):
        # A short of 60 before: the first delivery covers it, its 60
        # shares carrying 6 in the cover; 40 join the pool with 4, the
        # second delivery's 100 with 10; the sale of all 140 takes 14.
        book = ([_r("margin", "2025-01-03", "ZZQ.TO", -60, 60,
                    rid="short")]
                + [r for r in self._book() if r.get("id") != "sale"]
                + [_r("margin", "2025-01-24", "ZZQ.TO", -140, 140,
                      rid="sale2")])
        self.assertEqual(_credits({"margin": book},
                                  {"margin": {"dlv1": 60.0}}),
                         {("margin", "dlv1"): 6.0,
                          ("margin", "sale2"): 14.0})

    @rule("CA-RPT-16")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {PUT} -2 CAD 0.1 20 0\n"
                f"ASSIGN 2025-01-17 10:00:00 {PUT} 2 CAD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-01-20 10:00:00 ZZQ.TO -100 CAD 1 100 0\n"
                "ASSIGN 2025-01-21 10:00:00 ZZQ.TO 100 CAD 1 100 0\n")
            doc = _stats(root)
            # The books: the sale's cost 90, a gain of 10.
            self.assertEqual(_sale_gain(root, "margin"),
                             [(100.0, 90.0, 10.0)])
        self.assertEqual(doc["classes"]["written-options"]["net_pl"], 20.0)
        shares = doc["classes"]["long-shares"]
        self.assertEqual((shares["trades"], shares["breakeven"],
                          shares["net_pl"]), (1, 1, 0.0))
        self.assertEqual((doc["total"]["net_pl"], doc["total"]["wins"],
                          doc["total"]["losses"]), (20.0, 1, 0))


# --------------------------------------------------------------------
# A split recorded by one account of the pool (TJ-0682)
# --------------------------------------------------------------------

class TestASplitOfThePool(unittest.TestCase):
    """TJ-0682: `a` is assigned a put (10) for 100 ZZQ.TO at 1, `b`
    holds 100 bought at 1; a 2:1 split that only `a`'s broker records,
    then `a` sells 100 at 0.5. The pool is 400 shares costing 190: the
    sale's cost 47.50, its part of the premium 2.50 (100 of the 400 shares),
    not 3.33 (of 300)."""

    def _pool(self, split_in=("a",)):
        a = [_r("a", "2025-01-02", PUT, -1, 10, rid="write"),
             _r("a", "2025-01-17", PUT, 1, 0, "ASSIGN", rid="asg"),
             _r("a", "2025-01-17", "ZZQ.TO", 100, 100, "ASSIGN",
                rid="dlv"),
             _r("a", "2025-02-04", "ZZQ.TO", -100, 50, rid="sale")]
        b = [_r("b", "2025-01-06", "ZZQ.TO", 100, 100, rid="buy")]
        pool = {"a": a, "b": b}
        for n, acct in enumerate(split_in):
            # A second broker's copy of the split a day later: one event.
            pool[acct].append(_r(acct, f"2025-02-0{3 + n}", "ZZQ.TO", 2, 0,
                                 "SPLIT", rid=f"split{n}",
                                 time="09:30:00"))
            pool[acct].sort(key=lambda r: r["date"])
        return pool

    @rule("CA-RPT-16", "CA-CORP-12")
    def test_every_account_of_the_pool_splits(self):
        self.assertEqual(_credits(self._pool()), {("a", "sale"): 2.5})
        # Both brokers record it (a day apart): one split, the same.
        self.assertEqual(_credits(self._pool(("a", "b"))),
                         {("a", "sale"): 2.5})
        # Only the other account's broker records it.
        self.assertEqual(_credits(self._pool(("b",))),
                         {("a", "sale"): 2.5})

    @rule("CA-RPT-16")
    def test_one_account_alone_unchanged(self):
        # `b` a pool of its own (not blended): `a`'s split, `a`'s pool
        # of 200, all sold by the half: the sale carries half (5).
        pool = self._pool()
        self.assertEqual(_credits({"a": pool["a"]}), {("a", "sale"): 5.0})

    @rule("CA-RPT-16")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ("a", "b"))
            (root / "inputs" / "a" / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {PUT} -1 CAD 0.1 10 0\n"
                f"ASSIGN 2025-01-17 10:00:00 {PUT} 1 CAD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "SPLIT 2025-02-03 09:30:00 ZZQ.TO ZZQ.TO 2\n"
                "BUYSELL 2025-02-04 10:00:00 ZZQ.TO -100 CAD 0.5 50 0\n")
            (root / "inputs" / "b" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 100 CAD 1 100 0\n")
            doc = _stats(root)
            self.assertEqual(_sale_gain(root, "a"), [(100.0, 47.5, 2.5)])
        self.assertEqual(doc["classes"]["written-options"]["net_pl"], 10.0)
        shares = doc["classes"]["long-shares"]
        self.assertEqual((shares["trades"], shares["breakeven"],
                          shares["net_pl"]), (1, 1, 0.0))
        self.assertEqual(doc["total"]["net_pl"], 10.0)


# --------------------------------------------------------------------
# A purchase that settles after the sale (TJ-0683)
# --------------------------------------------------------------------

class TestThePoolOnSettlementDates(unittest.TestCase):
    """TJ-0683: a put's 100 ZZQ.TO delivered Jan 17 and sold Jan 20; a
    purchase of 100 more traded Jan 17 settles Jan 21. The books walk
    the pool on settlement dates: the sale takes the delivered shares
    alone (cost 90, all 10 of the premium), not half of 200."""

    def _book(self):
        return [_r("margin", "2025-01-02", PUT, -1, 10, rid="write",
                   date_settle="2025-01-03"),
                _r("margin", "2025-01-17", PUT, 1, 0, "ASSIGN", rid="asg",
                   date_settle="2025-01-17"),
                _r("margin", "2025-01-17", "ZZQ.TO", 100, 100, "ASSIGN",
                   rid="dlv", date_settle="2025-01-17"),
                _r("margin", "2025-01-17", "ZZQ.TO", 100, 100, rid="buy",
                   date_settle="2025-01-21", time="09:30:00"),
                _r("margin", "2025-01-20", "ZZQ.TO", -100, 100, rid="sale",
                   date_settle="2025-01-20")]

    @rule("CA-RPT-16", "CA-DATE-01")
    def test_a_purchase_settling_later_takes_none(self):
        self.assertEqual(_credits({"margin": self._book()}),
                         {("margin", "sale"): 10.0})
        # Settled before the sale: it is in the pool, the sale half.
        book = [dict(r, date_settle="2025-01-20") if r.get("id") == "buy"
                else r for r in self._book()]
        self.assertEqual(_credits({"margin": book}),
                         {("margin", "sale"): 5.0})

    @rule("CA-RPT-16", "CA-CORP-01", "CA-CORP-12")
    def test_a_sale_settling_after_a_split(self):
        # 100 held and 100 delivered (premium 10); 100 sold Jan 30,
        # settling Feb 3; a 2:1 split Jan 31 inside its lag. The books
        # split the pool first and book the sale as 200 post-split
        # shares: half the pool, half the premium; the rest 5.
        book = ([_r("margin", "2025-01-06", "ZZQ.TO", 100, 100, rid="b0",
                    date_settle="2025-01-07")]
                + [r for r in self._book() if r.get("id") not in
                   ("buy", "sale")]
                + [_r("margin", "2025-01-30", "ZZQ.TO", -100, 100,
                      rid="sale", date_settle="2025-02-03",
                      time="15:00:00"),
                   _r("margin", "2025-01-31", "ZZQ.TO", 2, 0, "SPLIT",
                      rid="split", date_settle="2025-01-31",
                      time="09:30:00"),
                   _r("margin", "2025-02-05", "ZZQ.TO", -200, 100,
                      rid="sale2", date_settle="2025-02-06")])
        self.assertEqual(_credits({"margin": book}),
                         {("margin", "sale"): 5.0, ("margin", "sale2"): 5.0})

    @rule("CA-RPT-16", "CA-DATE-01", "CA-DATE-02")
    def test_cli(self):
        # tax_date places a row in a year, never in the books' walk:
        # a trade-date project's sale takes the same cost, and the view
        # the same premium.
        for basis in ("", 'tax_date = "trade"\n'):
            with self.subTest(basis=basis):
                self._cli(basis)

    def _cli(self, basis):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []\n",
                'source_currencies = ["USD"]\n' + basis))
            (root / "ticker.map").write_text("TOBASE ZZQ.US ZZQ.TO\n")
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {PUT} -1 CAD 0.1 10 0\n"
                f"ASSIGN 2025-01-17 10:00:00 {PUT} 1 CAD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-01-20 10:00:00 ZZQ.TO -100 CAD 1 100 0\n")
            # A US purchase on Friday Jan 17 settles Tuesday Jan 21
            # (the Monday a US settlement holiday).
            buy = root / "inputs" / "margin" / "generic_buy.csv"
            buy.write_text(
                "Date,Settle,Type,Ticker,Shares,Price,Amount,Commission,"
                "Currency\n"
                "2025-01-17,2025-01-21,BUY,ZZQ.US,100,1,100,0,USD\n")
            buy.with_name(buy.name + ".toml").write_text(
                '[columns]\ndate = "Date"\nsettle = "Settle"\n'
                'action = "Type"\nsymbol = "Ticker"\nquantity = "Shares"\n'
                'price = "Price"\namount = "Amount"\nfee = "Commission"\n'
                'currency = "Currency"\n[actions]\nBUY = "buy"\n')
            doc = _stats(root)
            self.assertEqual(_sale_gain(root, "margin"),
                             [(100.0, 90.0, 10.0)])
        self.assertEqual(doc["classes"]["written-options"]["net_pl"], 10.0)
        shares = doc["classes"]["long-shares"]
        self.assertEqual((shares["trades"], shares["breakeven"],
                          shares["net_pl"]), (1, 1, 0.0))
        self.assertEqual(doc["total"]["net_pl"], 10.0)


if __name__ == "__main__":
    unittest.main()
