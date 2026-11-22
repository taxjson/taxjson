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
import json
import random
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl
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


class _Sub:
    def __init__(self, table):
        self.table = table

    def __call__(self, argv, timeout=900):
        return self.table.get(argv[0], (0, "", ""))


class TestChecklistFormExport(unittest.TestCase):
    """TJ-3136: form-export's totals against `tjs sum`'s FOR THE RETURN.

    Formerly: test_fix_tj3119_whole_cents.TestChecklistFormExport"""

    def step(self, gain, sum_gain, form="schedule3"):
        if form == "8949":
            fe = {"form": "8949", "part_I": [{}], "part_II": [],
                  "part_I_totals": {"proceeds": 1000.0, "gain": gain},
                  "part_II_totals": {"proceeds": 0.0, "gain": 0.0}}
        else:
            fe = {"form": "schedule3", "rows": [{}],
                  "totals": {"proceeds_all": 1000.0, "gain_all": gain}}
        summ = {"accounts": [{"account": "margin", "realized": gain}],
                "filing": {"totals": {"proceeds": 1000.0, "gain": sum_gain}}}
        cfg = {"settings": {"year": 2025, "country": "canada"},
               "accounts": {"margin": {"type": "taxable"}}}
        with tempfile.TemporaryDirectory() as td:
            ctx = cl.Ctx(root=Path(td), cfg=cfg, year=2025,
                         today=date(2026, 9, 23), run_sub=_Sub({
                             "form-export": (0, json.dumps(fe), ""),
                             "sum": (0, json.dumps(summ), "")}))
            return cl.d_form_export(ctx)

    def test_a_cent_is_no_problem(self):
        self.assertGreater(abs(317.41 - 317.40), 0.01)
        for form in ("schedule3", "8949"):
            r = self.step(317.40, 317.41, form)
            self.assertEqual(r.status, "done", (form, r.detail))

    def test_two_cents_are(self):
        r = self.step(317.40, 317.42)
        self.assertEqual(r.status, "attention")
        self.assertIn("Schedule 3 gain 317.40 vs FOR THE RETURN 317.42", r.detail)
        r = self.step(317.42, 317.40, "8949")
        self.assertIn("Form 8949 gain 317.42 vs FOR THE RETURN 317.40", r.detail)

    def test_the_sweep(self):
        for i, (a, one, two) in enumerate(_sweep(n=40)):
            b = one[i % len(one)]
            self.assertNotIn("vs FOR THE RETURN", self.step(a, b).detail, (a, b))
            b = two[i % len(two)]
            self.assertIn("vs FOR THE RETURN", self.step(a, b).detail, (a, b))

    def test_nan(self):
        """A NaN total compares as before: no line of its own."""
        self.assertNotIn("vs FOR THE RETURN", self.step(317.40, NAN).detail)


if __name__ == "__main__":
    unittest.main()
