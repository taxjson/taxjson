"""Re-audit-2 test pins (tests-pins-03), the shared dedup rule:
taxjson_sort.plan_dedup (A2-0171, A2-0878, A2-0904) and the transfer-
evidence twin in taxjson-brokerage (A2-1570). Each test fails when the
rule it names is reverted or mutated. Synthetic data only."""
import unittest

from taxjson.bin.taxjson_sort import deduplicate, plan_dedup
from taxjson.lib.core import TaxTransaction


def _tx(date, qty, source, symbol="XYZ.TO"):
    return TaxTransaction(action="BUYSELL", date=date, symbol=symbol,
                          quantity=qty, price=10.0, net_amount=-10.0 * qty,
                          currency="CAD", account="margin",
                          description="", source=source)


def _booked(plan, n):
    """Every row is either kept or dropped, never lost."""
    return sorted(plan.keep + plan.drop) == list(range(n))


class TestTtLineVersusExport(unittest.TestCase):
    """A .tt line equal to an exported row is that row, booked ONCE with
    an ATTENTION line naming the reason — whatever order the pipeline
    lists the files in (it lists .tt files first)."""

    def _check(self, rows):
        plan = plan_dedup(rows)
        self.assertTrue(_booked(plan, len(rows)))
        self.assertEqual(len(plan.keep), 1)
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("a hand-kept .tt line equals an exported row",
                      plan.attention[0])
        self.assertEqual(plan.notes, [])
        return plan

    def test_tt_listed_first_is_booked_once(self):
        # A2-0171 m24 (`elif ta and tb` -> `elif ta` / `elif tb`)
        plan = self._check([_tx("2025-03-03", 100, "start.tt"),
                            _tx("2025-03-03", 100, "q.csv")])
        self.assertEqual(plan.drop, [0])

    def test_tt_listed_last_is_booked_once(self):
        plan = self._check([_tx("2025-03-03", 100, "q.csv"),
                            _tx("2025-03-03", 100, "start.tt")])
        self.assertEqual(plan.drop, [1])

    def test_two_tt_lines_equal_to_exported_rows_keep_the_attention(self):
        """A2-0904 m61 / A2-0878 m29: with two equal rows the overlap
        test alone would call it a confirmed copy and say nothing."""
        rows = [_tx("2025-03-03", 100, "q.csv"), _tx("2025-03-04", 50, "q.csv"),
                _tx("2025-03-03", 100, "hist.tt"), _tx("2025-03-04", 50, "hist.tt")]
        plan = plan_dedup(rows)
        self.assertEqual(plan.drop, [2, 3])
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("a hand-kept .tt line equals an exported row",
                      plan.attention[0])
        self.assertIn("2 identical row(s)", plan.attention[0])


class TestTtSuffixIsCaseInsensitive(unittest.TestCase):
    def test_upper_case_tt_file_is_a_hand_kept_file(self):
        """A2-0171 m31: START.TT is a .tt input (input_files accepts any
        case), so its line and m2.tt's identical line are two records."""
        rows = [_tx("2025-03-04", 100, "START.TT"), _tx("2025-03-04", 100, "m2.tt")]
        plan = plan_dedup(rows)
        self.assertEqual(plan.keep, [0, 1])
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("Hand-kept .tt files are separate records",
                      plan.attention[0])


class TestOneSidedAccountInfo(unittest.TestCase):
    """A2-0171 m33/m34: only one of two overlapping re-exports names its
    broker accounts. Nothing says they are different accounts, so the
    overlap rule decides: a confirmed copy, booked once."""

    def _rows(self):
        a = [_tx("2025-12-1%d" % d, 10 + d, "a.csv") for d in range(5, 9)]
        b = [_tx("2025-12-1%d" % d, 10 + d, "b.csv") for d in range(5, 9)]
        return a + b

    def test_first_file_names_accounts(self):
        rows = self._rows()
        plan = plan_dedup(rows, {"a.csv": ["h1"]})
        self.assertEqual(plan.keep, [0, 1, 2, 3])
        self.assertEqual(plan.drop, [4, 5, 6, 7])
        self.assertEqual((plan.attention, plan.notes), ([], []))

    def test_second_file_names_accounts(self):
        rows = self._rows()
        plan = plan_dedup(rows, {"b.csv": ["h2"]})
        self.assertEqual(plan.keep, [0, 1, 2, 3])
        self.assertEqual(plan.drop, [4, 5, 6, 7])
        self.assertEqual((plan.attention, plan.notes), ([], []))


class TestEveryRowOfAUidGroupIsExamined(unittest.TestCase):
    """A2-0171 m26/m27: after a row of a uid group is dropped (or kept),
    the later rows of the group are still examined — a second broker
    account's identical trade is never silently lost."""

    def test_repeat_in_one_file_then_another_account(self):
        rows = [_tx("2025-03-03", 100, "a.csv"), _tx("2025-03-03", 100, "a.csv"),
                _tx("2025-03-03", 100, "b.csv")]
        plan = plan_dedup(rows, {"a.csv": ["h1"], "b.csv": ["h2"]})
        self.assertTrue(_booked(plan, 3))
        self.assertEqual(plan.keep, [0, 2])
        self.assertEqual(plan.drop, [1])
        self.assertEqual(len(deduplicate(rows, source_accounts={
            "a.csv": ["h1"], "b.csv": ["h2"]})), 2)

    def test_tt_repeat_then_another_tt_file(self):
        rows = [_tx("2025-03-03", 100, "q.csv"), _tx("2025-03-03", 100, "m1.tt"),
                _tx("2025-03-03", 100, "m1.tt"), _tx("2025-03-03", 100, "m2.tt")]
        plan = plan_dedup(rows)
        self.assertTrue(_booked(plan, 4))
        self.assertEqual(plan.keep, [0, 3])
        self.assertEqual(plan.drop, [1, 2])


class TestSourcelessRowsKeepTheIdRule(unittest.TestCase):
    def test_sourceless_row_colliding_with_an_exported_row(self):
        """A2-0878 m6 / A2-0904 m20: a row with no source (hand-made
        JSON, a generated corp-action row) that shares an id with an
        exported row follows the id-only rule: booked once, quietly."""
        rows = [_tx("2025-03-03", 100, ""), _tx("2025-03-03", 100, "a.csv")]
        plan = plan_dedup(rows)
        self.assertEqual(plan.keep, [0])
        self.assertEqual(plan.drop, [1])
        self.assertEqual((plan.attention, plan.notes), ([], []))


class TestConfirmedCopyIsQuiet(unittest.TestCase):
    def test_confirmed_copy_prints_no_note(self):
        """A2-0904 m52: a confirmed re-export copy is dropped quietly —
        never the 'statements of different broker accounts ... booked
        separately' note."""
        a = [_tx("2025-12-1%d" % d, 10 + d, "q_2025.csv") for d in range(5, 9)]
        b = [_tx("2025-12-1%d" % d, 10 + d, "q_2026.csv") for d in range(5, 9)]
        plan = plan_dedup(a + b)
        self.assertEqual(len(plan.drop), 4)
        self.assertEqual(plan.notes, [])
        self.assertEqual(plan.attention, [])


class TestAmbiguityExplanations(unittest.TestCase):
    """A2-0878 m37/m41: the two AMBIGUOUS explanations stay apart."""

    def test_disagreeing_files_name_the_rows_only_in_each(self):
        a = [_tx("2025-03-03", 1, "qa.csv"), _tx("2025-03-04", 2, "qa.csv"),
             _tx("2025-03-05", 3, "qa.csv"), _tx("2025-03-06", 4, "qa.csv")]
        b = [_tx("2025-03-03", 1, "qb.csv"), _tx("2025-03-04", 2, "qb.csv"),
             _tx("2025-03-05", 7, "qb.csv"), _tx("2025-03-06", 4, "qb.csv")]
        plan = plan_dedup(a + b)
        self.assertEqual(len(plan.attention), 1)
        msg = plan.attention[0]
        self.assertIn("the files disagree", msg)
        self.assertIn("only in qa.csv: 2025-03-05 BUYSELL XYZ.TO 3", msg)
        self.assertIn("only in qb.csv: 2025-03-05 BUYSELL XYZ.TO 7", msg)
        self.assertNotIn("too little overlap", msg)

    def test_thin_overlap_says_too_little_overlap(self):
        a = [_tx("2025-03-03", 100, "qa.csv"), _tx("2025-06-02", -200, "qa.csv")]
        b = [_tx("2025-03-03", 100, "qb.csv")]
        plan = plan_dedup(a + b)
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("too little overlap", plan.attention[0])
        self.assertNotIn("the files disagree", plan.attention[0])


class TestTransferEvidenceLargestCount(unittest.TestCase):
    """A2-1570: the --transfers evidence dedup keeps, per content id, the
    LARGEST count any one file holds (S026-23 / S027-00): two identical
    custody moves in the second export beat one in the first."""

    def test_second_file_with_more_identical_moves_wins(self):
        from taxjson.bin.taxjson_brokerage import _dedup_evidence
        mv = {"action": "TRANSFER", "date": "2025-04-01", "symbol": "XYZ.US",
              "quantity": 50.0, "currency": "USD", "account": "IB"}
        one = [dict(mv)]
        two = [dict(mv), dict(mv)]
        self.assertEqual(len(_dedup_evidence([one, two])), 2)
        self.assertEqual(len(_dedup_evidence([two, one])), 2)
        self.assertEqual(len(_dedup_evidence([one, dict_list(mv, 1)])), 1)


def dict_list(row, n):
    return [dict(row) for _ in range(n)]


if __name__ == "__main__":
    unittest.main()
