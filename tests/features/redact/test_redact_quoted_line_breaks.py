"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_redact as R
from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


# ------------------------------------------------------------- TJ-0414

def _tjs(*args, cwd=REPO_ROOT):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(args)),
        cwd=cwd, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


ACCT_HEAD = "Date,Description,Account #\n"


class TestRedactReadsCsvRecords(unittest.TestCase):
    """TJ-0414: redact read physical lines. A quoted Description with a
    line break before the Account # cell kept the account id (and
    `--check` said none was found); a quoted alias with a line break
    was blanked on its first line only, its second line kept and the
    next cell (the symbol) blanked in its place.

    Formerly: test_fix_1029_messages.TestRedactReadsCsvRecords"""

    def test_an_account_after_a_multiline_cell_is_replaced(self):
        for nl in ("\n", "\r\n"):
            with self.subTest(newline=repr(nl)):
                src = (ACCT_HEAD.replace("\n", nl)
                       + f'2026-01-05,"memo{nl}keep",99900077{nl}'
                       + f'2026-01-06,"memo",ZZD{nl}')
                out, rep = R.redact_text(src)
                self.assertEqual(rep.accounts, {"99900077": "99900001"})
                self.assertNotIn("99900077", out)
                # The structure is the original's: the cell keeps its
                # line break, the line endings theirs.
                self.assertEqual(out, src.replace("99900077", "99900001"))
                self.assertEqual(rep.unreplaced, {})

    def test_a_multiline_alias_is_blanked_whole(self):
        src = ('ClientAccountID,AccountAlias,Symbol\n'
               'U1234567,"Synthetic\nAlias",ZZZ\n')
        out, rep = R.redact_text(src)
        self.assertEqual(out, 'ClientAccountID,AccountAlias,Symbol\n'
                              'U9990001,"REDACTED",ZZZ\n')
        self.assertEqual(rep.identity_rows, 1)

    def test_review_lines_are_the_copys(self):
        # A name in a free-text column after a blanked two-line alias:
        # the copy's line 3 (the alias is one line there).
        src = ('ClientAccountID,AccountAlias,Description\n'
               'U1234567,"Synthetic\nAlias",x\n'
               'U1234567,,FROM SAM SAMPLE\n')
        out, rep = R.redact_text(src)
        self.assertEqual(out.splitlines()[2],
                         "U9990001,,FROM SAM SAMPLE")
        self.assertIn(3, [n for n, _why in rep.review])

    def test_an_unclosed_quote_is_read_line_by_line_and_listed(self):
        src = (ACCT_HEAD + '2026-01-05,"memo,99900077\n'
               + "2026-01-06,x,99900077\n")
        out, rep = R.redact_text(src)
        self.assertNotIn("99900077", out)
        self.assertIn((2, R.UNBALANCED_REASON), rep.review)

    def test_check_finds_the_account_of_a_multiline_row(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "synthetic.csv"
            f.write_text(ACCT_HEAD + '2026-01-05,"memo\nkeep",99900077\n')
            r = _tjs("redact", "--no-denylist", "--check", str(f))
        self.assertIn("account ids: 1 distinct, every occurrence replaced",
                      r.stdout)
        self.assertNotIn("account ids: none found", r.stdout)


if __name__ == "__main__":
    unittest.main()
