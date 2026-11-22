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


class TestChannelTag(unittest.TestCase):
    """Formerly: test_fix_prerelease_018.TestChannelTag"""
    def test_trailing_newline_is_not_a_release_tag(self):
        from taxjson.lib import channels as ch
        self.assertIsNone(ch.TAG_RE.match("v1.2.3\n"))
        self.assertIsNotNone(ch.TAG_RE.match("v1.2.3"))
        with self.assertRaises(ch.ChannelsError):
            ch.parse_channels('{"stable": "v1.2.3\\n"}')


if __name__ == "__main__":
    unittest.main()
