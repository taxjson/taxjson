"""Step 6h(2): three checks of two money figures cut apart compare in whole
cents (`M.cents_apart`), so a difference of a cent or less passes and float
error never trips them: check-filed's drift (TJ-3119, TJ-3130), the handoff's
claim less the ledger's carry-back against the record (TJ-3132) and the
checklist's form-export step against `tjs sum` (TJ-3136); and the readers of
an old lock against a new one that compared at 0.005: a year's opening
against its earlier year's record (TJ-3381) and the ledger's word that a
lock applied more than it carried in (TJ-3389). A one-cent difference of two
floats is a bit over 0.01 for most pairs, so a right book read as drift. Two
cents still fail; a NaN or a None answers as before. close-year's replaced
totals report any cent, in whole cents: they are the reference (TJ-3390).

Formerly: test_fix_tj3119_whole_cents"""
import random
import unittest
from pathlib import Path

from taxjson.bin.taxjson_carryover import build_canada_ledger
from taxjson.lib.carryforward import LedgerCarryBack, _close, handoff_issues
from taxjson.model import money as M
from taxjson.project.settings import Settings

NAN = float("nan")


def _sweep(n=4000, seed=3119):
    """(today's cut, its pairs a cent apart, its pairs two cents apart) for
    n random amounts: some anywhere, some on an exact half cent, where
    round()'s cut (the lock a taxjson before step 6h(4)'s flip wrote) and
    today's half-up `shown` part by a cent: the upgrade case."""
    rng = random.Random(seed)
    for i in range(n):
        if i % 2:
            x = rng.uniform(-1e7, 1e7)
        else:
            x = (rng.randrange(-10 ** 9, 10 ** 9) + 0.5) / 100
        a = M.shown(x, 2)
        one = [a + 0.01, a - 0.01, M.shown(a + 0.01, 2), M.shown(a - 0.01, 2),
               round(x, 2)]
        two = [a + 0.02, a - 0.02, M.shown(a + 0.02, 2), M.shown(a - 0.02, 2)]
        yield a, one, two


class TestHandoff(unittest.TestCase):
    """TJ-3132: the claim under the record year less the part the ledger
    carries back to it, against what the record applied.

    Formerly: test_fix_tj3119_whole_cents.TestHandoff"""

    def issues(self, applied, back=333.33, claimed=1000.0):
        cfg = {"settings": Settings.from_table({"country": "canada",
                                                "year": 2024}),
               "carryover": {"claimed": {"2023": claimed}}}
        record = {"year": 2023, "carryforwards": {"net_capital_loss": {
            "applied": applied, "closing": 0.0}}}
        return handoff_issues(Path("."), cfg, record,
                              LedgerCarryBack(amounts={2023: back})).issues

    def test_a_cent_is_no_mismatch(self):
        self.assertGreater(abs((1000.0 - 333.33) - 666.66), 0.01)
        self.assertEqual(self.issues(666.66), [])
        self.assertEqual(self.issues(666.68), [])

    def test_two_cents_are(self):
        for applied in (666.65, 666.69):
            got = self.issues(applied)
            self.assertEqual(len(got), 1, applied)
            self.assertIn("333.33 of it carried back", got[0]["why"])

    def test_the_sweep(self):
        for a, one, two in _sweep(n=400):
            for b in one:
                self.assertTrue(_close(a, b), (a, b))
            self.assertFalse(any(_close(a, b) for b in two), a)

    def test_a_claim_of_three_places(self):
        """TJ-3383: the claim less the carry-back taken exactly. In floats
        1608.165 - 675.10 is 933.0649999999999, cut 933.06; exactly it is
        933.065, cut 933.07."""
        self.assertEqual(M.cents_apart(1608.165 - 675.10, 933.05), 1)
        self.assertEqual(len(self.issues(933.05, back=675.10,
                                         claimed=1608.165)), 1)
        self.assertEqual(self.issues(933.08, back=675.10,
                                     claimed=1608.165), [])

    def test_nan_and_none(self):
        """A NaN is never within a cent, as before; None is refused."""
        self.assertEqual(len(self.issues(NAN)), 1)
        self.assertFalse(_close(NAN, NAN))
        with self.assertRaises(TypeError):
            _close(1.0, None)


class TestOverApplication(unittest.TestCase):
    """TJ-3389: the ledger's word that a later lock applied more than the
    record before it carries in now, by the carry-in check's rule: whole
    cents, over only, more than a cent.

    Formerly: test_fix_tj3119_whole_cents.TestOverApplication"""

    def row(self, applied, carried_in):
        nets = {y: {"net": v, "st": 0.0, "lt": 0.0, "dispositions": 1}
                for y, v in ((2024, -812.47), (2025, 1300.0))}
        led = build_canada_ledger(nets, {}, carried={2024: carried_in,
                                                     2025: 0.0},
                                  locked_applied={2024: 0.0, 2025: applied})
        return next(r for r in led["rows"] if r["year"] == 2025)

    def test_a_cent_over_or_under_is_none(self):
        for carried_in in (812.46, 812.48, 812.49):
            self.assertNotIn("applied_over_balance",
                             self.row(812.47, carried_in), carried_in)

    def test_two_cents_over(self):
        self.assertEqual(self.row(812.47, 812.45)["applied_over_balance"],
                         {"applied": 812.47, "carried_in": 812.45})


if __name__ == "__main__":
    unittest.main()
