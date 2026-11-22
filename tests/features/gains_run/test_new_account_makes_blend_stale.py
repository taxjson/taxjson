"""Audit 2026-10-24, group "reports-state": the recorded wash inputs
and a new account (D01), the T1135 cost walk and an assignment that
covers a short (D02), a damaged work/cross_listings.state read as "no
pending journals" (D05), an incomplete work/<acct>_report.json served
by `sum` (D07), and `migrate` sharing a tobase.map that is not UTF-8
text (A42). One test per finding, each the audit's probe.

Every fixture is synthetic: made-up tickers (ZZQ.US), account names
(a, b, c, margin, tfsa), no ids, no amounts of 1,000 or more with cents.

Formerly: test_fix_audit_1024_reports
"""
import tempfile
import unittest
from pathlib import Path

from _support.view_wash_staleness import _older


class TestD01NewAccountMakesTheBlendStale(unittest.TestCase):
    """Formerly: test_fix_audit_1024_reports.TestD01NewAccountMakesTheBlendStale"""

    def _blend(self, root: Path, blend: str = "ca", *, roster=True):
        """A recorded blend of a and b (a tfsa book present), the wash
        file a_gains_wash.json; `roster=False` strips the `books` list
        as a record of an older release has none."""
        from taxjson.lib import report_model as rm
        inputs = []
        for acct in ("a", "b"):
            for suffix in ("_base.json", "_gains.json"):
                p = root / (acct + suffix)
                p.write_text('{"transactions":[]}')
                inputs.append(p)
        for suffix in ("_base.json", "_gains.json"):
            (root / ("tfsa" + suffix)).write_text('{"transactions":[]}')
        wash = root / "a_gains_wash.json"
        wash.write_text('{"transactions":[]}')
        rm.record_wash_inputs(root, [wash], inputs, blend=blend)
        if not roster:
            from taxjson.lib.records import read_record, write_record
            rec = read_record(wash)
            rec["wash"].pop("books")
            write_record(wash, rec)
        return wash

    def _new_account(self, root: Path, wash: Path) -> None:
        (root / "c_base.json").write_text(
            '{"transactions":[{"symbol":"ZZQ.US"}]}')
        (root / "c_gains.json").write_text('{"transactions":[]}')
        for suffix in ("_base.json", "_gains.json"):
            _older(root / ("c" + suffix), wash, 10)

    def test_d01_new_account_books_newer_than_the_blend_are_stale(self):
        """D01: Recorded wash inputs fail to detect a newly added
        account. A wash result recorded from accounts a and b did not
        become stale when account c was added and its books built: the
        recorded-input branch checked the recorded names only. The
        record now carries the roster of accounts the pass saw; c, not
        in it, with books newer than the wash file, makes it stale."""
        from taxjson.lib import report_model as rm
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wash = self._blend(root)
            from taxjson.lib.records import read_record
            self.assertEqual(read_record(wash)["wash"]["books"],
                             ["a", "b", "tfsa"])
            self.assertEqual(rm.stale_wash_inputs(wash), [])
            self._new_account(root, wash)
            self.assertEqual(rm.stale_wash_inputs(wash),
                             ["c_base.json", "c_gains.json"])
            # The account's own books rebuilt unchanged are current, and
            # a sheltered book the pass saw (in the roster) rebuilt
            # unchanged is too: GitHub #15's precision kept.
            (root / "c_base.json").unlink()
            (root / "c_gains.json").unlink()
            for n in ("a_base.json", "a_gains.json", "tfsa_base.json",
                      "tfsa_gains.json"):
                _older(root / n, wash, 20)
            self.assertEqual(rm.stale_wash_inputs(wash), [])
        with tempfile.TemporaryDirectory() as td:
            # A record without a roster (an older release's): the new
            # account is still found — the conservative side.
            root = Path(td)
            wash = self._blend(root, roster=False)
            self._new_account(root, wash)
            self.assertEqual(rm.stale_wash_inputs(wash),
                             ["c_base.json", "c_gains.json"])
        with tempfile.TemporaryDirectory() as td:
            # A single-account pass (the blend tag is the account's own
            # name) depends on no other account.
            root = Path(td)
            wash = self._blend(root, blend="a")
            self._new_account(root, wash)
            self.assertEqual(rm.stale_wash_inputs(wash), [])


if __name__ == "__main__":
    unittest.main()
