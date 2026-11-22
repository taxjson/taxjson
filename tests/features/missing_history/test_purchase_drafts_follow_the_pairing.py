"""The 2026-10-29 fix round 3, group wash: the superficial-loss views
and the planning tools (backlog TJ-0600, TJ-0620, TJ-0599, TJ-0615).
Synthetic data only: made-up tickers (ZZR, OTH, SYN, ZZC, ZZQ) and
account numbers in throwaway folders.

Formerly: test_fix_1029_r3_wash"""
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R
from _support.transfer_cases import _three_accounts


def _flat(text: str) -> str:
    """Whitespace-collapsed: where a line breaks differs with the temp
    path's length (backlog TJ-0625)."""
    return " ".join(text.split())


class TestDraftsReadTheRunsPairing(unittest.TestCase):
    """TJ-0599: qa sends 100 ZZQ out on 2024-03-01; qc receives 100 on
    03-02 (the move's other leg: the run pairs them) and qb 100 on
    03-08 from outside the books, at a stated book value.
    `find-missing-history qb --write-purchases` dropped qc's transfer-in
    before pairing, so it paired qb's with qa's transfer-out: "a move
    from your account qa", while the run books it at its book value.

    Formerly: test_fix_1029_r3_wash.TestDraftsReadTheRunsPairing"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _three_accounts(Path(cls._td.name) / "p", "canada")
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _drafts(self, *account):
        r = tj(self.root, "find-missing-history", *account,
               "--write-purchases", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return _flat(r.stdout)

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_TRANSFER_BV)
    def test_tj0599_one_account_names_the_runs_pairing(self):
        out = self._drafts("qb")
        self.assertIn("[qb] 2024-03-08 100 units — the run already books "
                      "it at the stated book value", out)
        self.assertNotIn("move from your account", out)
        self.assertIn("[qc] 2024-03-02 100 units — a move from your "
                      "account qa", self._drafts("qc"))
        both = self._drafts()
        self.assertIn("[qb] 2024-03-08 100 units — the run already books "
                      "it at the stated book value", both)
        self.assertIn("[qc] 2024-03-02 100 units — a move from your "
                      "account qa", both)

    @rule(R.CA_ACB_OWN_MOVE)
    def test_tj0599_without_a_project_every_row_is_paired(self):
        # The library's own pairing (no project around the books): an
        # account's drafts still pair every account's rows.
        from taxjson.lib.purchase_drafts import draft_purchases

        def tr(acct, d, q, desc):
            return {"action": "TRANSFER", "date": d, "symbol": "ZZQ.TO",
                    "quantity": q, "currency": "CAD", "account": acct,
                    "net_amount": 0.0, "description": desc}
        rows = [tr("qa", "2024-03-01", -100, "ZZQ CORP TRANSFER OUT"),
                tr("qc", "2024-03-02", 100,
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00"),
                tr("qb", "2024-03-08", 100,
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00")]
        drafts, gaps = draft_purchases([], country="canada",
                                       accounts={"qb"}, transfer_rows=rows)
        self.assertEqual([(d.account, d.quantity) for d in drafts],
                         [("qb", 100.0)])
        self.assertEqual(gaps, [])
        # The run's pairing, given, is the one read: here it paired
        # nothing, so qc's transfer-in is drafted too.
        drafts, gaps = draft_purchases([], country="canada",
                                       transfer_rows=rows, own_moves=[])
        self.assertEqual(sorted((d.account, d.quantity) for d in drafts),
                         [("qb", 100.0), ("qc", 100.0)])


if __name__ == "__main__":
    unittest.main()
