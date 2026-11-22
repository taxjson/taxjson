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
from pathlib import Path

from taxjson.bin.taxjson_carryover import lock_figure
from taxjson.bin.taxjson_filed import diff_snapshot
from taxjson.cli.file import _replaced_totals_moved
from taxjson.model import money as M
from taxjson.pipeline import run as pipeline_run

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


class TestCheckFiled(unittest.TestCase):
    """TJ-3119: check-filed's drift on the locked totals and form lines.

    Formerly: test_fix_tj3119_whole_cents.TestCheckFiled"""

    def drift(self, filed, now, key="realized"):
        lock = {"accounts": {"m": {key: filed, "dispositions": 1}}}
        return diff_snapshot(lock, {"m": {key: now, "dispositions": 1}})

    def lines(self, filed, now):
        lock = {"accounts": {"m": {"form_lines": {"13199": filed}}}}
        return diff_snapshot(lock, {"m": {"form_lines": {"13199": now}}})

    def test_a_cent_is_no_drift(self):
        self.assertGreater(abs(100.11 - 100.10), 0.01)
        self.assertEqual(self.drift(100.10, 100.11), [])
        self.assertEqual(self.drift(100.11, 100.10, "proceeds"), [])
        self.assertEqual(self.lines(317.40, 317.41), [])

    def test_two_cents_are(self):
        self.assertEqual(self.drift(100.10, 100.12), [
            "m: realized filed 100.10 -> now 100.12 (drift +0.02)"])
        self.assertEqual(self.lines(317.42, 317.40), [
            "m: form line 13199 filed 317.42 -> now 317.40 (drift -0.02)"])

    def test_the_sweep(self):
        for i, (a, one, two) in enumerate(_sweep(n=400)):
            for b in one:
                self.assertEqual(self.drift(a, b), [], (a, b))
                self.assertEqual(self.lines(b, a), [], (a, b))
            self.assertEqual(len(self.drift(a, two[i % len(two)])), 1, a)

    def test_an_account_not_in_the_lock(self):
        lock = {"accounts": {}}
        for now, said in ((0.01, False), (-0.01, False), (0.02, True),
                          (NAN, False), (None, False)):
            lines = diff_snapshot(lock, {"m": {"realized": now}})
            self.assertEqual(bool(lines), said, now)

    def test_nan_and_none(self):
        """A NaN locked is a damaged lock; one recomputed drifts from
        nothing; a form line not recomputed is 0; a None recomputed total
        is refused, as float() refused it."""
        with self.assertRaises(ValueError):
            self.drift(NAN, 100.10)
        self.assertEqual(self.drift(100.10, NAN), [])
        self.assertEqual(self.lines(317.40, NAN), [])
        self.assertEqual(self.lines(0.01, None), [])
        self.assertEqual(len(self.lines(0.02, None)), 1)
        with self.assertRaises(ValueError):
            self.lines(None, 317.40)
        with self.assertRaises(TypeError):
            self.drift(100.10, None)


class TestReplacedLock(unittest.TestCase):
    """TJ-3381, TJ-3390: close-year --force's word on the replaced lock's
    totals: money on any cent, in whole cents (the replaced lock is the
    reference), the dispositions count on any change.

    Formerly: test_fix_tj3119_whole_cents.TestReplacedLock"""

    def test_a_cent_is_said(self):
        old = {"realized": 318.40, "disallowed": 12.25, "income": 77.10,
               "dispositions": 6}
        new = dict(old, realized=318.41, disallowed=12.24, income=77.11)
        self.assertEqual(_replaced_totals_moved(old, new), [
            "realized 318.40 -> 318.41", "disallowed 12.25 -> 12.24",
            "income 77.10 -> 77.11"])
        self.assertEqual(_replaced_totals_moved(old, dict(old)), [])
        # Float noise below the cent is no move.
        self.assertEqual(_replaced_totals_moved(
            {"realized": 0.1 + 0.2}, {"realized": 0.3}), [])

    def test_two_cents_and_a_count_are(self):
        old = {"realized": 318.40, "disallowed": 12.25, "dispositions": 6}
        new = dict(old, realized=318.42, disallowed=12.23, dispositions=7)
        self.assertEqual(_replaced_totals_moved(old, new), [
            "realized 318.40 -> 318.42", "disallowed 12.25 -> 12.23",
            "dispositions 6 -> 7"])

    def test_nan(self):
        self.assertEqual(_replaced_totals_moved({"realized": NAN},
                                                {"realized": 1.0}), [])


class TestCarryIn(unittest.TestCase):
    """TJ-3381: a later lock's opening against the closing its earlier
    year's record carries now (the run's and check-filed's T1-ADJ word,
    close-year's on a replaced lock; `tjs carryover`'s stale opening).

    Formerly: test_fix_tj3119_whole_cents.TestCarryIn"""

    SOURCE = "filed/2024.json (the 2024 close-year record)"

    def moved(self, opening, closing):
        later = {"carryforwards": {"net_capital_loss": {
            "opening": opening, "applied": opening, "closing": 0.0,
            "opening_source": "../2024/" + self.SOURCE}}}
        rec = {"carryforwards": {"net_capital_loss": {"closing": closing}}}
        return pipeline_run._carry_in_moved(later, rec, 2024)

    def test_the_run_and_check_filed(self):
        for closing in (731.19, 731.21):
            self.assertIsNone(self.moved(731.20, closing), closing)
        for closing in (731.18, 731.22):
            self.assertEqual(self.moved(731.20, closing),
                             (731.20, 731.20, closing))
        self.assertIsNone(self.moved(731.20, NAN))

    def figure(self, opening, closing):
        with tempfile.TemporaryDirectory() as td:
            filed = Path(td) / "filed"
            filed.mkdir()
            (filed / "2024.json").write_text(json.dumps({
                "year": 2024, "totals": {"realized": -900.0},
                "carryforwards": {"net_capital_loss": {"closing": closing}}}))
            lock = filed / "2025.json"
            lock.write_text(json.dumps({
                "year": 2025, "totals": {"realized": 40.0},
                "carryforwards": {"net_capital_loss": {
                    "opening": opening, "applied": 40.0,
                    "closing": opening - 40.0,
                    "opening_source": self.SOURCE}}}))
            return lock_figure(lock, "canada")

    def test_carryovers_stale_opening(self):
        for closing in (845.69, 845.71):
            got = self.figure(845.70, closing)
            self.assertEqual(got.get("opening"), 845.70, closing)
            self.assertNotIn("opening_stale", got)
        got = self.figure(845.70, 845.72)
        self.assertEqual(got["opening_stale"],
                         {"opening": 845.70, "record_now": 845.72})
        self.assertNotIn("opening", got)


if __name__ == "__main__":
    unittest.main()
