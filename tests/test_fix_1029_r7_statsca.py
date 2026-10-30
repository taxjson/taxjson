"""Fix round 2026-10-29 (round 7), group statsca: the assigned option
premium and the written options of `stats` / `win-rates`
(lib/trade_stats).

TJ-0893  two puts of one underlying assigned the same day: each claimed
         every marked delivery leg and put its whole premium in the
         first, so the first sale carried both. The options are now
         paired with their legs together, each leg to one option (by
         strike first), as the engine pairs them.
TJ-0676  the pool's shares were the accounts' long positions added: a
         sale in one account that took another account's shares out of
         the pool left them counted, and a later sale carried too small
         a part of the premium. The pool is the accounts' net position.
TJ-0768  a negative net premium (commission above the premium) never
         came out of the pool's sales.
TJ-0769  an assignment row's fee came out of the option's P/L and again
         out of the shares': the credit is the premium net of it.
TJ-0767  a custody move of a written option out and back (a zero-net
         TRANSFER cluster) lost the write's premium.
TJ-0786  a buy-back whose other contracts were written before the data
         was a definite trade of its known part: the whole close is of
         unknown outcome now.
TJ-0894  a spin-off's ACB allocation left the whole premium on the
         parent: the part of the parent's ACB the spin-off moved takes
         its part of the premium to the new shares (the engine's cost
         timeline gives the part).

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

PUT1 = "ZZQ250321P00001000.TO"        # strike 1
PUT2 = "ZZQ250321P00002000.TO"        # strike 2
CALL = "ZZQ250321C00001000.TO"        # strike 1


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _r(acct, d, sym, q, net, action="BUYSELL", rid=None, **kw):
    mult = 100 if sym in (PUT1, PUT2, CALL) else 1
    return dict(dict(date=d, time="10:00:00", account=acct, symbol=sym,
                     action=action, quantity=q, net_amount=net,
                     price=abs(net) / abs(q) / mult if net and q else 0,
                     currency="CAD", multiplier=mult,
                     **({"id": rid} if rid else {})), **kw)


def _wo(rows):
    from taxjson.lib import trade_stats as TS
    return TS.written_option_trades(rows, lambda t: t["date"])


def _credits(pool, covers=None, timeline=None):
    from taxjson.lib import trade_stats as TS
    puts = [c for rows in pool.values()
            for c in _wo(rows)["credits"] if c.get("right") != "C"]
    kw = {"timeline": timeline} if timeline else {}
    return {k: round(v, 6)
            for k, v in TS.pooled_put_credits(pool, puts, covers,
                                              **kw).items()}


def _project(td: str, accounts=("margin",)) -> Path:
    root = Path(td) / "p"
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    return root


def _run(root: Path) -> None:
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def _stats(root: Path, *args: str) -> dict:
    _run(root)
    r = _cli(root, "stats", "2025", "--json", *args)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def _gains(root: Path, acct: str) -> list:
    doc = json.loads((root / "work" / f"{acct}_gains_wash.json").read_text())
    return [(t["symbol"], t["qty"], round(t["cost"], 6),
             round(t["gain"], 6))
            for t in doc["transactions"] if "gain" in t]


def _cls(doc, cls):
    c = doc["classes"][cls]
    return (c["trades"], c["wins"], c["losses"], c["net_pl"])


# --------------------------------------------------------------------
# Two assignments of one day (TJ-0893)
# --------------------------------------------------------------------

class TestTwoAssignmentsOfOneDay(unittest.TestCase):
    """TJ-0893: a put at strike 1 written for 10 and one at strike 2 for
    20, both assigned Mar 21; 100 shares arrive at 1 that day and are
    sold at 1, the other 100 arrive Mar 24 at 2. The sale's cost is 90
    (the strike-1 put's 10 only): it carries 10, not 30."""

    def _book(self):
        return [_r("margin", "2025-01-02", PUT1, -1, 10, rid="w1"),
                _r("margin", "2025-01-02", PUT2, -1, 20, rid="w2",
                   time="11:00:00"),
                _r("margin", "2025-03-21", PUT1, 1, 0, "ASSIGN", rid="a1",
                   time="09:00:00"),
                _r("margin", "2025-03-21", PUT2, 1, 0, "ASSIGN", rid="a2",
                   time="09:01:00"),
                _r("margin", "2025-03-21", "ZZQ.TO", 100, 100, "ASSIGN",
                   rid="d1"),
                _r("margin", "2025-03-21", "ZZQ.TO", -100, 100, rid="sale",
                   time="11:00:00"),
                _r("margin", "2025-03-24", "ZZQ.TO", 100, 200, "ASSIGN",
                   rid="d2")]

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_each_leg_to_its_own_put(self):
        self.assertEqual(_credits({"margin": self._book()}),
                         {("margin", "sale"): 10.0})
        # The second 100 sold later carry the strike-2 put's 20.
        book = self._book() + [_r("margin", "2025-03-26", "ZZQ.TO", -100,
                                  200, rid="sale2")]
        self.assertEqual(_credits({"margin": book}),
                         {("margin", "sale"): 10.0,
                          ("margin", "sale2"): 20.0})

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_the_per_account_draw_too(self):
        from taxjson.lib import trade_stats as TS
        book = self._book()
        out = TS.credits_by_sale(book, _wo(book)["credits"])
        self.assertEqual({k: round(v, 6) for k, v in out.items()},
                         {"sale": 10.0})

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {PUT1} -1 CAD .1 10 0\n"
                f"BUYSELL 2025-01-02 11:00:00 {PUT2} -1 CAD .2 20 0\n"
                f"ASSIGN 2025-03-21 09:00:00 {PUT1} 1 CAD 0 0 0\n"
                f"ASSIGN 2025-03-21 09:01:00 {PUT2} 1 CAD 0 0 0\n"
                "ASSIGN 2025-03-21 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-03-21 11:00:00 ZZQ.TO -100 CAD 1 100 0\n"
                "ASSIGN 2025-03-24 10:00:00 ZZQ.TO 100 CAD 2 200 0\n")
            doc = _stats(root)
            self.assertEqual(_gains(root, "margin"),
                             [("ZZQ.TO", 100.0, 90.0, 10.0)])
        self.assertEqual(_cls(doc, "written-options"), (2, 2, 0, 30.0))
        self.assertEqual(_cls(doc, "long-shares"), (1, 0, 0, 0.0))
        self.assertEqual(doc["total"]["net_pl"], 30.0)


# --------------------------------------------------------------------
# Another account's sale out of the pool (TJ-0676)
# --------------------------------------------------------------------

class TestAnotherAccountsSale(unittest.TestCase):
    """TJ-0676: `a` buys 100 at 1, `b` sells 100 at 1 (out of the one
    pool: it closes), then `a` is assigned a put (10) for 100 at 1 and
    sells 100 at 1. The pool holds the 100 delivered shares alone: the
    sale carries the whole 10, not half."""

    def _pool(self):
        a = [_r("a", "2025-01-02", "ZZQ.TO", 100, 100, rid="buy"),
             _r("a", "2025-02-03", PUT1, -1, 10, rid="write"),
             _r("a", "2025-02-21", PUT1, 1, 0, "ASSIGN", rid="asg"),
             _r("a", "2025-02-21", "ZZQ.TO", 100, 100, "ASSIGN", rid="dlv"),
             _r("a", "2025-03-03", "ZZQ.TO", -100, 100, rid="sale")]
        b = [_r("b", "2025-01-06", "ZZQ.TO", -100, 100, rid="bsale")]
        return {"a": a, "b": b}

    @rule("CA-RPT-16", "CA-ACB-01")
    def test_the_pool_is_the_net_position(self):
        self.assertEqual(_credits(self._pool()), {("a", "sale"): 10.0})

    @rule("CA-RPT-16", "CA-ACB-01")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ("a", "b"))
            (root / "inputs" / "a" / "trades.tt").write_text(
                "BUYSELL 2025-01-02 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                f"BUYSELL 2025-02-03 10:00:00 {PUT1} -1 CAD 0.1 10 0\n"
                f"ASSIGN 2025-02-21 10:00:00 {PUT1} 1 CAD 0 0 0\n"
                "ASSIGN 2025-02-21 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-03-03 10:00:00 ZZQ.TO -100 CAD 1 100 0\n")
            (root / "inputs" / "b" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO -100 CAD 1 100 0\n")
            doc = _stats(root)
        self.assertEqual(_cls(doc, "written-options"), (1, 1, 0, 10.0))
        self.assertEqual(doc["classes"]["long-shares"]["net_pl"], 0.0)
        self.assertEqual(doc["total"]["net_pl"], 10.0)


# --------------------------------------------------------------------
# A negative net premium (TJ-0768)
# --------------------------------------------------------------------

class TestANegativePremium(unittest.TestCase):
    """TJ-0768: a put written for 5 with a commission of 10 (net -5),
    assigned 100 at 1, sold at 1. The shares cost 105: the sale's -5
    is the option's, not the shares'."""

    def _book(self, net=-5):
        return [_r("margin", "2025-01-02", PUT1, -1, net, rid="write",
                   price=0.05, commission=10),
                _r("margin", "2025-01-17", PUT1, 1, 0, "ASSIGN", rid="asg"),
                _r("margin", "2025-01-17", "ZZQ.TO", 100, 100, "ASSIGN",
                   rid="dlv"),
                _r("margin", "2025-01-20", "ZZQ.TO", -100, 100, rid="sale")]

    @rule("CA-RPT-16")
    def test_it_comes_out_of_the_sale(self):
        self.assertEqual(_credits({"margin": self._book()}),
                         {("margin", "sale"): -5.0})
        self.assertEqual(_credits({"margin": self._book(5)}),
                         {("margin", "sale"): 5.0})

    @rule("CA-RPT-16")
    def test_closed_trades(self):
        from taxjson.lib import trade_stats as TS
        book = self._book()
        gains = {"margin": {"transactions": [
            {"id": "sale", "symbol": "ZZQ.TO", "qty": 100.0, "cost": 105.0,
             "proceeds": 100.0, "gain": -5.0, "raw_gain": -5.0,
             "direction": "LONG", "date": "2025-01-20"}]}}
        res = TS.closed_trades(gains, {"margin": book}, lambda d: True,
                               lambda r: r["date"],
                               pools=[{"margin": book}])
        got = sorted((t["cls"], round(t["pnl"], 6)) for t in res["trades"])
        self.assertEqual(got, [("long-shares", 0.0),
                               ("written-options", -5.0)])


# --------------------------------------------------------------------
# An assignment row's fee (TJ-0769)
# --------------------------------------------------------------------

class TestAnAssignmentFee(unittest.TestCase):
    """TJ-0769: a put (or call) written for 10, a fee of 2 on the
    assignment row, the shares at the strike. The option earns 8 and
    the books fold 8 into the shares: the credit is 8, not 10."""

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_a_put(self):
        book = [_r("margin", "2025-01-02", PUT1, -1, 10, rid="write"),
                _r("margin", "2025-01-17", PUT1, 1, 2, "ASSIGN", rid="asg",
                   price=0, fee=2),
                _r("margin", "2025-01-17", "ZZQ.TO", 100, 100, "ASSIGN",
                   rid="dlv"),
                _r("margin", "2025-01-20", "ZZQ.TO", -100, 100, rid="sale")]
        wo = _wo(book)
        self.assertEqual([round(t["pnl"], 6) for t in wo["trades"]], [8.0])
        self.assertEqual([round(c["amount"], 6) for c in wo["credits"]],
                         [8.0])
        self.assertEqual(_credits({"margin": book}),
                         {("margin", "sale"): 8.0})

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_a_call(self):
        from taxjson.lib import trade_stats as TS
        book = [_r("margin", "2025-01-02", "ZZQ.TO", 100, 100, rid="buy"),
                _r("margin", "2025-01-03", CALL, -1, 10, rid="write"),
                _r("margin", "2025-01-17", CALL, 1, 2, "ASSIGN", rid="asg",
                   price=0, fee=2),
                _r("margin", "2025-01-17", "ZZQ.TO", -100, 100, "ASSIGN",
                   rid="dlv")]
        out = TS.credits_by_sale(book, _wo(book)["credits"])
        self.assertEqual({k: round(v, 6) for k, v in out.items()},
                         {"dlv": 8.0})


# --------------------------------------------------------------------
# A custody move out and back (TJ-0767)
# --------------------------------------------------------------------

class TestACustodyRoundTrip(unittest.TestCase):
    """TJ-0767: a put written for 10, moved out and back (TRANSFER +1,
    -1 a day apart), bought back for 5: one trade, a win of 5."""

    def _book(self, moves=True):
        rows = [_r("margin", "2025-01-02", PUT1, -1, 10, rid="write")]
        if moves:
            rows += [_r("margin", "2025-05-01", PUT1, 1, 0, "TRANSFER",
                        rid="out"),
                     _r("margin", "2025-05-02", PUT1, -1, 0, "TRANSFER",
                        rid="back")]
        return rows + [_r("margin", "2025-06-02", PUT1, 1, 5, rid="buy")]

    @rule("CA-RPT-16")
    def test_the_round_trip_is_a_no_op(self):
        for moves in (False, True):
            with self.subTest(moves=moves):
                wo = _wo(self._book(moves))
                self.assertEqual([(t["kind"], round(t["pnl"], 6))
                                  for t in wo["trades"]],
                                 [("bought back", 5.0)])
                self.assertEqual(wo["unknown"], [])

    @rule("CA-RPT-16")
    def test_a_move_out_alone_still_counts(self):
        # Out and never back: the write left the books (no trade, no
        # unknown close).
        rows = self._book()[:2]
        wo = _wo(rows)
        self.assertEqual((wo["trades"], wo["unknown"]), ([], []))

    @rule("CA-RPT-16")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {PUT1} -1 CAD 0.1 10 0\n"
                f"TRANSFER 2025-05-01 10:00:00 {PUT1} 1 CAD 0 0\n"
                f"TRANSFER 2025-05-02 10:00:00 {PUT1} -1 CAD 0 0\n"
                f"BUYSELL 2025-06-02 10:00:00 {PUT1} 1 CAD 0.05 5 0\n")
            doc = _stats(root)
        self.assertEqual(_cls(doc, "written-options"), (1, 1, 0, 5.0))
        self.assertEqual(doc["tainted_skipped"], 0)


# --------------------------------------------------------------------
# A buy-back partly of a write before the data (TJ-0786)
# --------------------------------------------------------------------

class TestAPartlyUnknownBuyBack(unittest.TestCase):
    """TJ-0786: one call written for 10 in the data, then a buy-back of
    two for 30 that the broker codes C: the other contract was written
    before the data. The close's outcome is unknown: no trade, one
    close of unknown premium."""

    def _book(self, known=1):
        rows = [_r("margin", "2025-02-03", CALL, -known, 10 * known,
                   rid="write", open_close="O"),
                _r("margin", "2025-03-03", CALL, 2, 30, rid="close",
                   open_close="C")]
        return rows

    @rule("CA-RPT-16")
    def test_the_whole_close_is_unknown(self):
        wo = _wo(self._book())
        self.assertEqual(wo["trades"], [])
        self.assertEqual(len(wo["unknown"]), 1)
        # Both contracts known: one trade, a loss of 10.
        wo = _wo(self._book(2))
        self.assertEqual([round(t["pnl"], 6) for t in wo["trades"]],
                         [-10.0])
        self.assertEqual(wo["unknown"], [])

    @rule("CA-RPT-16")
    def test_a_close_of_a_known_and_an_unknown_write(self):
        # An opening short of unknown premium first, then a known write
        # of one: the buy-back of both is of unknown outcome.
        rows = [_r("margin", "2025-01-01", CALL, -1, 0, "OPENING_BALANCE",
                   rid="ob"),
                _r("margin", "2025-02-03", CALL, -1, 10, rid="write"),
                _r("margin", "2025-03-03", CALL, 2, 30, rid="close")]
        wo = _wo(rows)
        self.assertEqual((wo["trades"], len(wo["unknown"])), ([], 1))

    @rule("CA-RPT-16")
    def test_closed_trades_counts_it_unknown(self):
        from taxjson.lib import trade_stats as TS
        book = self._book()
        res = TS.closed_trades({"margin": {"transactions": []}},
                               {"margin": book}, lambda d: True,
                               lambda r: r["date"])
        self.assertEqual((res["trades"], res["tainted_skipped"]), ([], 1))


# --------------------------------------------------------------------
# A spin-off of the delivered shares (TJ-0894)
# --------------------------------------------------------------------

def _spin_pool(sell_child=False):
    rows = [_r("margin", "2025-01-02", PUT2, -1, 20, rid="write"),
            _r("margin", "2025-01-17", PUT2, 1, 0, "ASSIGN", rid="asg"),
            _r("margin", "2025-01-17", "ZZQ.TO", 100, 200, "ASSIGN",
               rid="dlv"),
            _r("margin", "2025-02-03", "ZZK.TO", 100, 90, rid="child",
               corp_event_id="ev1", corp_election="rollover_s_86_1"),
            _r("margin", "2025-02-03", "ZZQ.TO", 0, -90, "ADJUST",
               rid="adj", corp_event_id="ev1",
               corp_election="rollover_s_86_1"),
            _r("margin", "2025-03-03", "ZZQ.TO", -100, 100, rid="sale")]
    if sell_child:
        rows.append(_r("margin", "2025-04-01", "ZZK.TO", -100, 90,
                       rid="csale"))
    return {"margin": rows}


def _timeline(parent_before=180.0, parent_after=90.0):
    fields = ["seq", "date", "account", "symbol", "qty", "total_cost",
              "deferred_wash", "unknown_cost", "currency",
              "position_start_date", "last_acq_date", "renamed_to"]

    def row(seq, d, sym, q, cost):
        return [seq, d, "", sym, q, cost, 0.0, False, "CAD", d, d, ""]
    return {"version": 1, "basis": "settle", "fields": fields, "rows": [
        row(2, "2025-01-17", "ZZQ.TO", 100.0, parent_before),
        row(3, "2025-02-03", "ZZK.TO", 100.0, 90.0),
        row(4, "2025-02-03", "ZZQ.TO", 100.0, parent_after),
        row(5, "2025-03-03", "ZZQ.TO", 0.0, 0.0)]}


class TestASpinOff(unittest.TestCase):
    """TJ-0894: a put written for 20 delivers 100 ZZQ.TO at 2 (cost
    180); an s.86.1 spin-off moves 90 of it to 100 ZZK.TO (half). The
    parent's sale (cost 90, gain 10) carries 10 of the premium; the
    child's the other 10."""

    @rule("CA-RPT-16", "CA-CORP-06")
    def test_the_premium_follows_the_acb(self):
        self.assertEqual(_credits(_spin_pool(True), timeline=_timeline()),
                         {("margin", "sale"): 10.0,
                          ("margin", "csale"): 10.0})
        # A quarter of the ACB moved: a quarter of the premium.
        self.assertEqual(
            _credits(_spin_pool(True),
                     timeline=_timeline(360.0, 270.0)),
            {("margin", "sale"): 15.0, ("margin", "csale"): 5.0})

    @rule("CA-RPT-16")
    def test_no_timeline_keeps_it_on_the_parent(self):
        # (an older run: no cost timeline to read the part from)
        self.assertEqual(_credits(_spin_pool(True)),
                         {("margin", "sale"): 20.0})

    @rule("CA-RPT-16", "CA-CORP-06")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            inp = root / "inputs" / "margin"
            (inp / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {PUT2} -1 CAD .2 20 0\n"
                f"ASSIGN 2025-01-17 10:00:00 {PUT2} 1 CAD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 ZZQ.TO 100 CAD 2 200 0\n"
                "BUYSELL 2025-03-03 10:00:00 ZZQ.TO -100 CAD 1 100 0\n")
            (inp / "spin.csv").write_text(
                "Statement,Header,Field Name,Field Value\n"
                "Statement,Data,BrokerName,Interactive Brokers\n"
                "Corporate Actions,Header,Asset Category,Currency,"
                "Report Date,Date/Time,Description,Quantity,Proceeds,"
                "Value,Realized P/L,Code\n"
                "Corporate Actions,Data,Stocks,CAD,2025-02-03,"
                '"2025-02-03, 10:00:00","ZZQ(CA0000000921) Spinoff 1 for 1 '
                '(ZZK, ZZK INC, CA0000000922)",100,0,100,0,\n')
            r = _cli(root, "run", "--no-input")
            pend = json.loads((root / "work" / "pending_elections.json")
                              .read_text())
            ev = json.dumps(pend)
            import re
            m = re.search(r'"(\d{8}-zzq-zzk-[0-9a-f]+)"', ev)
            self.assertTrue(m, r.stderr[-2000:])
            r = _cli(root, "elect", "margin", "--set",
                     f"{m[1]}=rollover_s_86_1", "--hint",
                     "allocated_acb_cad=90")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = _stats(root)
            self.assertEqual(_gains(root, "margin"),
                             [("ZZQ.TO", 100.0, 90.0, 10.0)])
        self.assertEqual(_cls(doc, "written-options"), (1, 1, 0, 20.0))
        self.assertEqual(_cls(doc, "long-shares"), (1, 0, 0, 0.0))
        self.assertEqual(doc["total"]["net_pl"], 20.0)


if __name__ == "__main__":
    unittest.main()
