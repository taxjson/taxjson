"""Re-audit-2 planning lists: the views / pipeline part.

  A2-0128  run --account <sheltered> with a sibling's book deleted refuses
           (sheltered_base.json kept) instead of rebuilding without it
  A2-0366  the same export file in two accounts is ATTENTION; --strict stops
  A2-0380 / A2-0381  every command that reads work/ books prints the
           stale-books run-state banner (stderr; --json stays valid)
  A2-0684 / A2-1167  an account-taking command refuses an account that is
           not in [accounts]; harvest names a symbol filter that matches
           nothing
  A2-1182  all-accounts views name a configured account with inputs but
           no books
  A2-0685  reports/<acct>_holdings.toml applies missing_history.json openings
  A2-0693  grant timing: an expired written call counts as a close in
           ccd-sum, winners and the .sum TRADES line
  A2-1190  wrappers relay the child's error line, not a fixed prefix

All data is synthetic.

Formerly: test_fix_a2_planning_views
"""
import unittest
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ---------------------------------------------------------------- A2-1190
class TestChildErrorRelayed(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestChildErrorRelayed"""
    def test_last_error_line_kept(self):
        from taxjson.cli.common import _child_error
        err = ("Traceback (most recent call last):\n"
               + "".join(f'  File "/very/long/path/{i}.py", line {i}, in f\n'
                         f"    x = y\n" for i in range(40))
               + "ValueError: could not convert string to float: 'abc'\n")
        msg = _child_error(err)
        self.assertIn("ValueError: could not convert string to float", msg)
        self.assertLess(len(msg), 600)
        self.assertEqual(_child_error(""), "(no error output)")
        self.assertEqual(_child_error("one line\n"), "one line")


if __name__ == "__main__":
    unittest.main()
