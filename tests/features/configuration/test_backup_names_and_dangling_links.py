"""Pre-release security review fixes (round 018). Synthetic data, fake
account ids, no network.

- M1/L5: a file replaced by `opening --force`, `find-missing-history
  --write-purchases --force`, `init --force` / `format --write` keeps
  its previous version at the next free <name>.bak / .bakN; an earlier
  backup is never overwritten and a symlink planted at a .bak name is
  never written through (lib/safe_write.backup_copy).
- M2/I4: an opening line is written only from a symbol, currency and
  lot date of a safe shape; the positions readers refuse control
  characters / whitespace in symbol and currency cells.
- L2: Questrade's description key is linear on long whitespace runs.
- L3: the `# From:` header of an opening file carries no control
  character of the report's file name.
- L4: the cannot-detect message masks an id in the file name.
- L5: a draft's account name is checked before it names a folder.
- I1: a channel tag with a trailing newline is not a release tag.
- I3: `init` never writes through a dangling symlink.

Formerly: test_fix_prerelease_018
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli

from taxjson.lib.safe_write import backup_copy


class TestBackupCopy(unittest.TestCase):
    """Formerly: test_fix_prerelease_018.TestBackupCopy"""
    def test_next_free_name_and_planted_symlink_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            victim = d / "victim.txt"
            victim.write_text("keep me\n")
            f = d / "x.tt"
            f.write_text("one\n")
            (d / "x.tt.bak").symlink_to(victim)
            b1 = backup_copy(f)
            self.assertEqual(b1.name, "x.tt.bak1")
            self.assertEqual(b1.read_text(), "one\n")
            self.assertEqual(victim.read_text(), "keep me\n")
            # Same content again: the existing backup is reused.
            self.assertEqual(backup_copy(f), b1)
            f.write_text("two\n")
            b2 = backup_copy(f)
            self.assertEqual(b2.name, "x.tt.bak2")
            self.assertEqual(b1.read_text(), "one\n")
            # A dangling link at a .bak name is taken, not followed.
            (d / "y.tt").write_text("y\n")
            (d / "y.tt.bak").symlink_to(d / "nowhere")
            self.assertEqual(backup_copy(d / "y.tt").name, "y.tt.bak1")
            self.assertFalse((d / "nowhere").exists())


class TestInitDanglingSymlink(unittest.TestCase):
    """Formerly: test_fix_prerelease_018.TestInitDanglingSymlink"""
    def test_init_never_writes_through_a_dangling_link(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            for rel in ("ticker.map", ".gitignore"):
                (root / rel).symlink_to(outside / rel)
            r = cli(root, "init", "--single", "--country", "canada")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertTrue((root / "ticker.map").is_symlink())
            self.assertIn("ticker.map", r.stdout + r.stderr)
            self.assertTrue((root / "taxjson.toml").is_file())


if __name__ == "__main__":
    unittest.main()
