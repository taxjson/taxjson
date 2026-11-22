"""Audit 2026-10-24, group "inkind": B20, B43, B05, B16, A13, B36, B37,
B14 — in-kind moves, the corporate views, the holdings folder, the
holdings TOML reader and the [[distributions]] ADJUST. Synthetic data
only.

Formerly: test_fix_audit_1024_inkind"""
import tempfile
import unittest
from pathlib import Path


class TestHoldingsDir(unittest.TestCase):
    """Formerly: test_fix_audit_1024_inkind.TestHoldingsDir"""

    def test_a13_another_accounts_snapshot_is_not_claimed_by_file_name(self):
        """A13: Holdings discovery assigns an excluded account snapshot to
        another account by filename — a snapshot stating the broker id
        of an account with its own `holdings = [...]` is that account's
        (not compared, said in a note), not margin's for its name."""
        from taxjson.lib import holdings_dir as hd
        cfg = {"margin": {"account": "99900001"},
               "other": {"account": "99900002", "holdings": ["manual.toml"]}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "holdings").mkdir()
            snap = root / "holdings" / "margin.toml"
            snap.write_text('[meta]\nbroker_account="99900002"\n'
                            '[[holding]]\nsymbol="ZZQ.US"\nquantity=1\n')
            found, notes = hd.discover(root / "holdings", cfg, root)
            self.assertEqual(found, {})
            self.assertEqual(len(notes), 1)
            self.assertIn("is other's", notes[0])
            self.assertIn("99***", notes[0])
            self.assertNotIn("99900002", notes[0])
            # Its own id, or no id at all: margin's, as before.
            for meta in ('broker_account="99900001"\n', ""):
                snap.write_text(f"[meta]\n{meta}"
                                '[[holding]]\nsymbol="ZZQ.US"\nquantity=1\n')
                found, notes = hd.discover(root / "holdings", cfg, root)
                self.assertEqual((list(found), notes), (["margin"], []))


if __name__ == "__main__":
    unittest.main()
