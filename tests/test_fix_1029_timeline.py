"""The engine's cost timeline and its readers (backlog TJ-0417).

The gains engine writes each pool's quantity and cost per day beside the
gains (lib/cost_timeline); `tjs t1135`, the year-end snapshot and
`tjs list --date` read it instead of replaying the books. Each audit
case gets a parity test against the engine: TJ-0365 (a warrant
exercise's basis), TJ-0366 (a call premium before its delivery),
TJ-0371 / TJ-0348 (wash additions landed out of order in the snapshot),
TJ-0387 / TJ-0278 (an own move's basis in the dated per-account list)
and TJ-0277 (a same-day sale before an arriving own move). All data is
synthetic."""
import contextlib
import io
import unittest

from tax_rules import rule

from taxjson.lib import cost_timeline as CT
from taxjson.lib.core import TaxTransaction
from taxjson.lib.pipeline import GainsRequest, run_gains


def tx(a, d, s, q, n=0.0, acct="a", settle=None, time="10:00:00", **kw):
    return TaxTransaction(action=a, date=d, date_settle=settle or d,
                          time=time, symbol=s, quantity=q, net_amount=n,
                          currency=kw.pop("currency", "CAD"), account=acct,
                          **kw)


def engine(rows, country="canada", year=2025, **kw):
    kw.setdefault("phantom_hint", False)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        return run_gains(list(rows), [], [], GainsRequest(
            country=country, taxable=True, year=year, cost_timeline=True,
            **kw))


def by_symbol(inv, key="total_cost"):
    out = {}
    for i in inv:
        if abs(float(i.get("qty") or 0)) <= 1e-9:
            continue
        q, c = out.get(i["symbol"], (0.0, 0.0))
        out[i["symbol"]] = (round(q + float(i["qty"]), 6),
                            round(c + float(i.get(key) or 0.0), 2))
    return out


# ------------------------------------------------------------ the engine
CA_BOOK = [
    tx("BUYSELL", "2024-03-01", "AAA.US", 100, -10000.0, id="b1"),
    tx("BUYSELL", "2024-11-08", "AAA.US", -50, 4000.0, id="s1"),
    tx("BUYSELL", "2024-11-15", "AAA.US", 50, -3500.0, id="b2"),
    tx("BUYSELL", "2025-02-03", "BBB.US", 10, -1000.0, acct="b", id="b3"),
    tx("SPLIT", "2025-03-03", "BBB.US", 2.0, 0.0, acct="b", id="sp",
       time="00:00:00"),
    tx("SPLIT", "2025-05-01", "BBB.US", 1.0, 0.0, acct="b", id="rn",
       symbol_new="BBN.US"),
    tx("BUYSELL", "2025-06-02", "BBN.US", -5, 300.0, acct="b", id="s2"),
    tx("BUYSELL", "2025-12-30", "AAA.US", 10, -900.0, id="b4",
       settle="2026-01-02"),
    tx("BUYSELL", "2026-02-02", "AAA.US", -30, 2400.0, id="s3"),
]

US_BOOK = [
    tx("BUYSELL", "2025-01-02", "QQQ.US", 10, -1000.0, acct="m1", id="u1",
       currency="USD"),
    tx("BUYSELL", "2025-03-03", "QQQ.US", -10, 700.0, acct="m1", id="u2",
       currency="USD"),
    tx("BUYSELL", "2025-03-10", "QQQ.US", 10, -650.0, acct="m2", id="u3",
       currency="USD"),
    tx("BUYSELL", "2025-12-31", "RRR.US", 5, -500.0, acct="m1", id="u4",
       settle="2026-01-02", currency="USD"),
    tx("BUYSELL", "2026-01-15", "QQQ.US", -4, 300.0, acct="m2", id="u5",
       currency="USD"),
]


class TestTimelineIsTheEnginesPools(unittest.TestCase):
    """The timeline at the books' last day is the gains file's
    inventory, and at the tax year's end its inventory_year_end, in both
    countries; the gains file itself is unchanged by asking for it."""

    def _check(self, rows, country, last):
        r = engine(rows, country)
        tl = r["cost_timeline"]
        self.assertIsNone(CT.check(tl))
        self.assertEqual(by_symbol(CT.inventory_on(tl, last)),
                         by_symbol(r["inventory"]))
        self.assertEqual(by_symbol(CT.inventory_on(tl, "2025-12-31")),
                         by_symbol(r["inventory_year_end"]))
        plain = engine(rows, country)
        plain.pop("cost_timeline")
        r.pop("cost_timeline")
        self.assertEqual(r, plain)

    @rule("CA-ACB-01")
    def test_canada(self):
        self._check(CA_BOOK, "canada", "2026-02-02")

    def test_usa(self):
        self._check(US_BOOK, "usa", "2026-01-15")

    def test_without_the_flag_there_is_none(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = run_gains(list(CA_BOOK), [], [], GainsRequest(
                country="canada", taxable=True, year=2025,
                phantom_hint=False))
        self.assertNotIn("cost_timeline", r)


# ------------------------------------------------------------ T1135
class TestT1135ReadsTheEngine(unittest.TestCase):
    """TJ-0365, TJ-0366: the T1135 cost walk replayed the books and
    lost a warrant's cost on its exercise, and added an exercised call's
    premium to shares an intervening sale then sold. It reads the
    engine's pools now: the cost amounts are the gains inventory's."""

    def _parity(self, rows, sym, want):
        from taxjson.bin.taxjson_t1135 import walk_costs
        r = engine(rows)
        with contextlib.redirect_stderr(io.StringIO()):
            w = walk_costs([t.to_dict() for t in rows], 2025, {})
        (inv,) = [i for i in r["inventory"] if i["symbol"] == sym]
        self.assertEqual(round(inv["total_cost"], 2), want)
        self.assertEqual(w["per_symbol"][sym]["year_end_cost"], want)
        self.assertEqual(w["per_symbol"][sym]["max_cost"], want)
        return w

    @rule("CA-RPT-12")
    def test_tj0365_warrant_exercise_keeps_the_warrant_cost(self):
        rows = [tx("BUYSELL", "2025-02-03", "ZZW.US", 100, -201.0),
                tx("ASSIGN", "2025-05-02", "ZZW.US", -100, 0.0,
                   exercise_of="ZZ.US"),
                tx("BUYSELL", "2025-05-02", "ZZ.US", 100, -400.0)]
        w = self._parity(rows, "ZZ.US", 601.0)
        self.assertEqual(w["max_total_cost"], 601.0)

    @rule("CA-RPT-12")
    def test_tj0366_premium_waits_for_its_delivery(self):
        call = "ZZ250620C00010000.US"
        rows = [tx("BUYSELL", "2025-01-02", "ZZ.US", 100, -500.0),
                tx("BUYSELL", "2025-02-03", call, 1, -100.0),
                tx("ASSIGN", "2025-06-20", call, -1, 0.0, time="09:30:00"),
                tx("BUYSELL", "2025-06-20", "ZZ.US", -100, 600.0,
                   time="09:31:00"),
                tx("BUYSELL", "2025-06-20", "ZZ.US", 100, -1000.0,
                   time="09:32:00")]
        self._parity(rows, "ZZ.US", 1100.0)


# ------------------------------------------------------------ snapshot
def snap(rows, country="canada", crypto=False, acct="a"):
    """handoff.snapshot of one taxable account at 2025-12-31, as
    close-year records it (the real taxjson-gains)."""
    import json
    import tempfile
    from pathlib import Path
    from taxjson.bin import taxjson_run as R
    from taxjson.lib import handoff
    us = country == "usa"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "work").mkdir()
        cfg = {"settings": {"year": 2025, "country": country,
                            "base_currency": "USD" if us else "CAD",
                            "tax_date": "trade" if us else "settle"},
               "accounts": {acct: {"type": "taxable", "crypto": crypto}}}
        (p / "work" / f"{acct}_base.json").write_text(json.dumps(
            {"transactions": [t.to_dict() for t in rows]}))
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return handoff.snapshot(p / "work", cfg, "2025-12-31",
                                    R._filed_run_gains,
                                    R._handoff_gains_flags(cfg["settings"]))


class TestSnapshotIsTheEnginesYearEnd(unittest.TestCase):
    """TJ-0371 (TJ-0348): the year-end snapshot re-ran the rows to Dec 31
    with the superficial-loss additions as ADJUST rows sorted by their
    stamp, so one landed ahead of a same-moment sale and the record held
    60 shares at 351.82 with 90.00 deferred for the engine's 310.91 and
    49.09. It reads the engine's own pools at Dec 31 now."""

    @rule("CA-RPT-08")
    def test_tj0371_addition_after_its_losing_sale(self):
        rows = [tx("BUYSELL", "2025-01-02", "ZZ.US", 100, -500.0, id="b1"),
                tx("BUYSELL", "2025-02-01", "ZZ.US", 100, -400.0, id="b2"),
                tx("BUYSELL", "2025-02-10", "ZZ.US", -100, 300.0, id="s1"),
                tx("BUYSELL", "2025-02-10", "ZZ.US", 10, -30.0, id="b3"),
                tx("BUYSELL", "2025-02-10", "ZZ.US", -50, 300.0, id="s2")]
        (ye,) = engine(rows)["inventory_year_end"]
        got = snap(rows)["equity"]["ZZ.US"]
        self.assertEqual(got, {"qty": 60.0, "acb": 310.91,
                               "deferred": 49.09})
        self.assertEqual((got["acb"], got["deferred"]),
                         (round(ye["total_cost"], 2),
                          round(ye["deferred_wash"], 2)))

    @rule("US-WASH-13")
    def test_usa_equity_and_crypto(self):
        rows = [tx("BUYSELL", "2025-10-01", "ZZQ.US", 10, -500.0,
                   acct="m", currency="USD"),
                tx("BUYSELL", "2025-11-01", "ZZQ.US", -10, 300.0,
                   acct="m", currency="USD"),
                tx("BUYSELL", "2025-11-10", "ZZQ.US", 10, -300.0,
                   acct="m", currency="USD"),
                tx("BUYSELL", "2026-01-05", "ZZQ.US", -10, 400.0,
                   acct="m", currency="USD")]
        self.assertEqual(snap(rows, "usa", acct="m")["equity"]["ZZQ.US"],
                         {"qty": 10.0, "acb": 500.0, "deferred": 200.0})
        coins = [tx(t.action, t.date, "ZZQC", t.quantity, t.net_amount,
                    acct="c", currency="USD") for t in rows]
        self.assertEqual(snap(coins, "usa", True, "c")["crypto"]["ZZQC"],
                         {"qty": 10.0, "acb": 300.0, "deferred": 0.0})


# ------------------------------------------------------------ list --date
class TestDatedListReadsTheEngine(unittest.TestCase):
    """TJ-0387 (TJ-0278), TJ-0277: `list --date` recomputed each
    account's books alone, so units that came by a move between your own
    accounts had no cost in the receiving account (100 moved qa -> qb at
    a cost of 200, 50 sold: qb showed 50 at 0.00 in both countries;
    plain `list` 50 at 100.00), and a US account's sale on the day the
    units arrived could come before them. It reads the cost timeline of
    the run's own (blended) pass now: the figures plain `list` shows."""

    def _case(self, country, *dates):
        import json
        import tempfile
        from pathlib import Path
        from test_fix_audit_1025_transfers import _cli, _project
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(Path(td.name) / "p", country, "2024-03-06")
        f = root / "inputs" / "qb" / "questrade_2024.csv"
        f.write_text(f.read_text()
                     .replace("2024-06-03,2024-06-04,Sell",
                              "2024-03-06,2024-03-07,Sell")
                     .replace("CORP,-100,3.00,300.00,0.00,300.00",
                              "CORP,-50,3.00,150.00,0.00,150.00"))
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])

        def rows(*args):
            r = _cli(root, "list", *args, "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = json.loads(r.stdout)
            return doc, [(x["account"], x["symbol"], x["qty"], x["cost"],
                          x["missing_history_suspect"])
                         for x in doc["rows"]]
        _plain, plain = rows()
        self.assertEqual([(a, q, c) for a, _s, q, c, _m in plain],
                         [("qb", 50.0, 100.0)])
        for d in dates:
            doc, dated = rows("--date", d)
            self.assertEqual(dated, plain, d)
            self.assertIn(f"as of {d}", doc["basis"])

    @rule("CA-ACB-01")
    def test_tj0387_canada_own_move_carries_its_cost(self):
        self._case("canada", "2024-03-08")

    @rule("US-BASIS-05")
    def test_tj0387_tj0277_usa_own_move_and_same_day_sale(self):
        self._case("usa", "2024-03-06", "2024-03-08")


if __name__ == "__main__":
    unittest.main()
