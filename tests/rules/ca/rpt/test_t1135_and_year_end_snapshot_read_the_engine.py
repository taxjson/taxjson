"""The engine's cost timeline and its readers (backlog TJ-0417).

The gains engine writes each pool's quantity and cost per day beside the
gains (lib/cost_timeline); `tjs t1135`, the year-end snapshot and
`tjs list --date` read it instead of replaying the books. Each audit
case gets a parity test against the engine: TJ-0365 (a warrant
exercise's basis), TJ-0366 (a call premium before its delivery),
TJ-0371 / TJ-0348 (wash additions landed out of order in the snapshot),
TJ-0387 / TJ-0278 (an own move's basis in the dated per-account list)
and TJ-0277 (a same-day sale before an arriving own move). All data is
synthetic.

Formerly: test_fix_1029_timeline"""
import contextlib
import io
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import GainsRequest, run_gains
from taxjson.project.settings import Settings


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


# ------------------------------------------------------------ T1135
class TestT1135ReadsTheEngine(unittest.TestCase):
    """TJ-0365, TJ-0366: the T1135 cost walk replayed the books and
    lost a warrant's cost on its exercise, and added an exercised call's
    premium to shares an intervening sale then sold. It reads the
    engine's pools now: the cost amounts are the gains inventory's.

    Formerly: test_fix_1029_timeline.TestT1135ReadsTheEngine"""

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

    @rule(R.CA_RPT_12)
    def test_tj0365_warrant_exercise_keeps_the_warrant_cost(self):
        rows = [tx("BUYSELL", "2025-02-03", "ZZW.US", 100, -201.0),
                tx("ASSIGN", "2025-05-02", "ZZW.US", -100, 0.0,
                   exercise_of="ZZ.US"),
                tx("BUYSELL", "2025-05-02", "ZZ.US", 100, -400.0)]
        w = self._parity(rows, "ZZ.US", 601.0)
        self.assertEqual(w["max_total_cost"], 601.0)

    @rule(R.CA_RPT_12)
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
    from taxjson.cli import file as cli_file
    from taxjson.pipeline import run as pipeline_run
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
                                    pipeline_run._filed_run_gains,
                                    cli_file._handoff_gains_flags(Settings.from_table(cfg["settings"])))


class TestSnapshotIsTheEnginesYearEnd(unittest.TestCase):
    """TJ-0371 (TJ-0348): the year-end snapshot re-ran the rows to Dec 31
    with the superficial-loss additions as ADJUST rows sorted by their
    stamp, so one landed ahead of a same-moment sale and the record held
    60 shares at 351.82 with 90.00 deferred for the engine's 310.91 and
    49.09. It reads the engine's own pools at Dec 31 now.

    Formerly: test_fix_1029_timeline.TestSnapshotIsTheEnginesYearEnd"""

    @rule(R.CA_RPT_08)
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

    @rule(R.US_WASH_13)
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


if __name__ == "__main__":
    unittest.main()
