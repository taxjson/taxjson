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
import unittest
from pathlib import Path


class TestCannotDetectNamesTheFile(unittest.TestCase):
    """The message goes to the person's own terminal: it names the file
    as it is on disk (a masked name could not tell 55500001.csv from
    55500001_2.csv); saved diagnostics mask instead (owner,
    2026-10-05).

    Formerly: test_fix_prerelease_018.TestCannotDetectNamesTheFile"""
    def test_file_name_is_shown_as_given(self):
        from taxjson.bin.taxjson_detect_brokerage import (
            cannot_detect_message)

        class Det:
            path = Path("/tmp/U5550001_activity.csv")  # pii-ok
            hint = ""
        msg = cannot_detect_message(Det())
        self.assertIn("/tmp/U5550001_activity.csv", msg)  # pii-ok
        msg = cannot_detect_message(
            Det(), shown="inputs/m/U5550001_activity.csv")  # pii-ok
        self.assertIn("for inputs/m/U5550001_activity.csv.", msg)  # pii-ok
        self.assertIn(" U5550001_activity.csv.toml", msg)  # pii-ok


if __name__ == "__main__":
    unittest.main()
