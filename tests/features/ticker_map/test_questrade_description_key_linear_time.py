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
import time
import unittest


class TestQuestradeDescKeyLinear(unittest.TestCase):
    """Formerly: test_fix_prerelease_018.TestQuestradeDescKeyLinear"""
    def test_long_whitespace_run_is_fast_and_keys_unchanged(self):
        from taxjson.lib.symbol_codes import _get_desc_key
        self.assertEqual(_get_desc_key("SAMPLE CORP  CASH DIV ON 100 SHS"),
                         "SAMPLE CORP")
        self.assertEqual(_get_desc_key("SAMPLE CORP CLASS B SUB VTG"),
                         "SAMPLE CORP CL B")
        self.assertEqual(_get_desc_key("SAMPLE\tCORP WE ACTED AS AGENT"),
                         "SAMPLE CORP")
        desc = "SAMPLE" + " " * 40000 + "X"
        t0 = time.perf_counter()
        key = _get_desc_key(desc)
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertEqual(key, "SAMPLE X")
        t0 = time.perf_counter()
        _get_desc_key(" " * 40000)
        _get_desc_key("A" + "\t \n" * 15000 + "TRANSFER")
        self.assertLess(time.perf_counter() - t0, 0.5)


if __name__ == "__main__":
    unittest.main()
