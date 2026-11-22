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
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


# ------------------------------------------------------------ list --date
class TestDatedListReadsTheEngine(unittest.TestCase):
    """TJ-0387 (TJ-0278), TJ-0277: `list --date` recomputed each
    account's books alone, so units that came by a move between your own
    accounts had no cost in the receiving account (100 moved qa -> qb at
    a cost of 200, 50 sold: qb showed 50 at 0.00 in both countries;
    plain `list` 50 at 100.00), and a US account's sale on the day the
    units arrived could come before them. It reads the cost timeline of
    the run's own (blended) pass now: the figures plain `list` shows.

    Formerly: test_fix_1029_timeline.TestDatedListReadsTheEngine"""

    def _case(self, country, *dates):
        import json
        import tempfile
        from pathlib import Path
        from _support.transfer_out_and_in import _cli, _project
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

    @rule(R.CA_ACB_01)
    def test_tj0387_canada_own_move_carries_its_cost(self):
        self._case("canada", "2024-03-08")

    @rule(R.US_BASIS_05)
    def test_tj0387_tj0277_usa_own_move_and_same_day_sale(self):
        self._case("usa", "2024-03-06", "2024-03-08")


if __name__ == "__main__":
    unittest.main()
