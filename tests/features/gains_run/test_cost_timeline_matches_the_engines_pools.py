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

from taxjson.lib import cost_timeline as CT
from taxjson.model.transaction import TaxTransaction
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
    countries; the gains file itself is unchanged by asking for it.

    Formerly: test_fix_1029_timeline.TestTimelineIsTheEnginesPools"""

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

    @rule(R.CA_ACB_01)
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


if __name__ == "__main__":
    unittest.main()
