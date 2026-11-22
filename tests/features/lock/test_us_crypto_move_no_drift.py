"""US engine work deferred from the re-audit (owner request): a wash-sale
replacement sold before the loss in another taxable account (A2-0544),
the §355 spin-off per lot (A2-0065), §356 boot per block (A2-0066), and
custody moves between your own taxable accounts that carry the lots
(A2-0032 securities, A2-0003 crypto). Synthetic data only.

Formerly: test_fix_us_engine
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestOwnMoveFiledLock(unittest.TestCase):
    """check-filed recomputes a locked year the way the run booked it:
    a US crypto move carried by the blended crypto pass is no DRIFT.

    Formerly: test_fix_us_engine.TestOwnMoveFiledLock"""

    @rule(R.US_CRYPTO_05)
    def test_close_year_then_check_filed_is_ok(self):
        import tempfile
        from _support.cb_send_projects import US_ACOIN, US_BKR, _proj
        from _support.crypto_sends import _cli
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"bkr": {"cb_bkr.csv": US_BKR},
                                    "acoin": {"cb_acoin.csv": US_ACOIN}},
                               country="usa")
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, home, "close-year", "--yes")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-2000:])
            k = _cli(root, home, "check-filed")
            self.assertEqual(k.returncode, 0, k.stdout + k.stderr[-2000:])
            self.assertIn("filed 2025: OK", k.stdout)


if __name__ == "__main__":
    unittest.main()
