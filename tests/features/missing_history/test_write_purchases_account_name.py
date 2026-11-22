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
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli, projects_both


class TestWritePurchasesAccountName(unittest.TestCase):
    """Formerly: test_fix_prerelease_018.TestWritePurchasesAccountName"""
    def _book(self, account):
        from _support.ib_basis_drafts import SINGLE, _parsed
        return {"work/margin_base.json": json.dumps(
                    {"transactions": _parsed(SINGLE, account=account)}),
                "inputs/margin/ib.csv": ""}

    def test_a_path_like_or_unconfigured_account_is_refused(self):
        for acct in ("../../escape", "other"):
            with tempfile.TemporaryDirectory() as td:
                root = projects_both(td, files=self._book(acct))["usa"]
                r = cli(root, "find-missing-history", "--write-purchases")
                self.assertNotEqual(r.returncode, 0, (acct, r.stdout))
                self.assertIn("account", r.stderr)
                self.assertEqual(
                    [p for p in Path(td).rglob("purchases_draft*")], [])

    def test_force_keeps_every_earlier_draft(self):
        from taxjson.lib.missing_history import DRAFT_NAME
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._book("margin"))["usa"]
            draft = root / "inputs" / "margin" / DRAFT_NAME
            victim = Path(td) / "victim.txt"
            victim.write_text("keep me\n")
            r = cli(root, "find-missing-history", "--write-purchases")
            self.assertEqual(r.returncode, 0, r.stderr)
            draft.write_text(draft.read_text() + "# edit 1\n")
            (draft.parent / (DRAFT_NAME + ".bak")).symlink_to(victim)
            r = cli(root, "find-missing-history", "--write-purchases",
                    "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(victim.read_text(), "keep me\n")
            self.assertIn("# edit 1", (draft.parent / (DRAFT_NAME + ".bak1"))
                          .read_text())
            draft.write_text(draft.read_text() + "# edit 2\n")
            r = cli(root, "find-missing-history", "--write-purchases",
                    "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("# edit 1", (draft.parent / (DRAFT_NAME + ".bak1"))
                          .read_text())
            self.assertIn("# edit 2", (draft.parent / (DRAFT_NAME + ".bak2"))
                          .read_text())
            self.assertIn(DRAFT_NAME + ".bak2", r.stderr)


if __name__ == "__main__":
    unittest.main()
