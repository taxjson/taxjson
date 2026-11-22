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
import math
import random
import unittest
from decimal import Decimal
from taxjson.model import money as M

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


class TestCentsApart(unittest.TestCase):
    """Formerly: test_fix_tj3119_whole_cents.TestCentsApart"""
    def test_whole_cents(self):
        self.assertEqual(M.cents_apart(100.10, 100.11), 1)
        self.assertEqual(M.cents_apart(100.10, 100.12), 2)
        self.assertEqual(M.cents_apart(0.1 + 0.2, 0.3), 0)
        self.assertEqual(M.cents_apart(Decimal("1.005"), 1), 1)
        self.assertEqual(M.cents_apart(5, "5.01"), 1)
        # Each figure cut half up from its exact value, as present cuts.
        self.assertEqual(M.cents_apart(-0.005, 0.0), 1)
        self.assertEqual(M.cents_apart(1234.565, 1234.56), 1)
        # A figure with more places: the gap is the cuts' (TJ-3382).
        self.assertEqual(M.cents_apart(0.005, -0.005), 2)
        self.assertEqual(M.cents_apart(0.005, 0.02499), 1)

    def test_the_sweep(self):
        """TJ-3119's sweep: no pair a cent apart is more than 1, every pair
        two cents apart is 2; the float check failed most of the first.
        The fifth figure is round()'s cut, the lock a taxjson before step
        6h(4)'s flip wrote: the upgrade case, a cent from today's cut on
        about a quarter of the half-cent amounts, each within a cent."""
        over = pairs = locks = 0
        for a, one, two in _sweep():
            for b in one:
                self.assertLessEqual(M.cents_apart(a, b), 1, (a, b))
                pairs += 1
                over += abs(a - b) > 0.01
            locks += one[-1] != a
            for b in two:
                self.assertEqual(M.cents_apart(a, b), 2, (a, b))
        self.assertGreater(over, pairs // 4)
        self.assertGreater(locks, 500, locks)

    def test_nan_and_none(self):
        """A NaN has no cents: every comparison is False, as the float
        one was; an infinity is inf. None is refused, as float() refuses it."""
        for a, b in ((NAN, 1.0), (1.0, NAN), (float("inf"), float("inf"))):
            gap = M.cents_apart(a, b)
            self.assertFalse(gap > 1 or gap <= 1, (a, b, gap))
        self.assertEqual(M.cents_apart(float("inf"), 1.0), math.inf)
        self.assertGreater(M.cents_apart(1e300, 2e300), 1)
        with self.assertRaises(TypeError):
            M.cents_apart(None, 1.0)


if __name__ == "__main__":
    unittest.main()
