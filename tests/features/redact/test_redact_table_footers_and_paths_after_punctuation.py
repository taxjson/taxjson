"""Generic importer and text-tool fixes of 2026-10-29, round 7 wave D
(group generic).

TJ-1034 (a ticker.map MULT line read as the premium multiplier of an
adjusted series), TJ-1035 (a Dec-31 $0 expiry whose commission is only
in the net amount), TJ-1077 (a buy-to-close $0 expiry refused as a
swapped fee/amount), TJ-1121 (a fee-only zero-price close needing
allow_large_fees), TJ-1100 (a tab table's footer name no longer blanked),
TJ-1101 (a folder path right after a colon in a settings comment).

Every fixture is SYNTHETIC: made-up tickers (SYN, QZX), invented amounts.
Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7d_generic
"""
import unittest

from taxjson.bin import taxjson_redact as R


class TestRedactDelimitedFooter(unittest.TestCase):
    """TJ-1100: a bare name after a tab, semicolon or pipe table is
    replaced, and the table's alias cells still are (TJ-0569).

    Formerly: test_fix_1029_r7d_generic.TestRedactDelimitedFooter"""

    def test_the_footer_name_is_replaced(self):
        for d in ("\t", ";", "|"):
            with self.subTest(delimiter=d):
                text = (f"AccountAlias{d}Symbol{d}Description\n"
                        f"Sample Person{d}SYN.TO{d}Dividend\n"
                        "Jordan Example\n")
                out, rep = R.redact_text(text)
                self.assertEqual(out, (
                    f"AccountAlias{d}Symbol{d}Description\n"
                    f"REDACTED{d}SYN.TO{d}Dividend\n"
                    "REDACTED\n"))
                self.assertEqual(rep.review, [])

    def test_a_name_account_table_too(self):
        text = ("Name\tAccount\tSymbol\n"
                "Sample Person\tU1234567\tSYN.TO\n"
                "Jordan Example\n")
        out, _rep = R.redact_text(text)
        self.assertNotIn("Jordan Example", out)
        self.assertNotIn("Sample Person", out)

    def test_a_row_with_commas_does_not_end_the_rule(self):
        text = ("AccountAlias\tSymbol\tDescription\n"
                "Main\tSYN.TO\tOne, two, three, four\n"
                "Jordan Example\n")
        out, _rep = R.redact_text(text)
        self.assertIn("\tOne, two, three, four\n", out)
        self.assertNotIn("Jordan Example", out)

    def test_table_rows_are_not_read_as_names(self):
        # A row's cells stay, whatever they look like: only its identity
        # column is replaced.
        text = ("AccountAlias\tBook\tDescription\n"
                "Main\tSample Book\tJordan Example\n")
        out, _rep = R.redact_text(text)
        self.assertEqual(out, "AccountAlias\tBook\tDescription\n"
                              "REDACTED\tSample Book\tJordan Example\n")

    def test_a_short_data_line_stays(self):
        text = ("Date\tAccountAlias\tSymbol\n"
                "2025-01-03\tSample Person\tSYN.TO\n"
                "Total\t2\n")
        out, _rep = R.redact_text(text)
        self.assertIn("Total\t2\n", out)

    def test_a_redacted_copy_checks_clean(self):
        text = ("AccountAlias\tSymbol\tDescription\n"
                "Main\tSYN.TO\tDividend\n"
                "Jordan Example\n")
        out, _rep = R.redact_text(text)
        again, rep = R.redact_text(out, stand_ins=True)
        self.assertEqual(again, out)
        self.assertFalse(rep.found_anything())


class TestRedactCommentPathAfterAnyCharacter(unittest.TestCase):
    """TJ-1101: a folder path right after a colon, in a file:// URL, or
    after other punctuation; TJ-0714's URL and relative shapes stay.

    Formerly: test_fix_1029_r7d_generic.TestRedactCommentPathAfterAnyCharacter"""

    def test_replaced(self):
        for comment, want in (
                ("# inputs_dir:/srv/casey/taxes/inputs",
                 "# inputs_dir:<redacted path>"),
                ('# was:"/srv/casey/taxes/inputs"',
                 '# was:"<redacted path>"'),
                ("# was:'/srv/casey/taxes/inputs'",
                 "# was:'<redacted path>'"),
                ("# see file:///srv/casey/taxes/inputs",
                 "# see file://<redacted path>"),
                ("# see FILE:///srv/casey/taxes/inputs",
                 "# see FILE://<redacted path>"),
                ("# see file://server/share/taxes",
                 "# see file:<redacted path>"),
                ("# see file:/srv/casey/taxes",
                 "# see file:<redacted path>"),
                ("# see file:///C:/data/casey/taxes",
                 "# see file://<redacted path>"),
                ("# moved->/srv/casey/taxes", "# moved-><redacted path>"),
                ("# a|/srv/casey/taxes", "# a|<redacted path>"),
                ("# old;/srv/casey/taxes", "# old;<redacted path>"),
                ("# inputs_dir:C:\\Users\\casey\\taxes",
                 "# inputs_dir:<redacted path>"),
                ("# share:\\\\server\\share\\taxes",
                 "# share:<redacted path>")):
            with self.subTest(comment=comment):
                self.assertEqual(R._redact_comment_paths(comment),
                                 (want, 1))

    def test_kept(self):
        for comment in ("# https://example.com/docs/x/",
                        "# http://localhost:8080/docs/x/",
                        "# see:https://example.com/docs/x/",
                        "# ssh://git@example.com/a/b/",
                        "# ./inputs/margin/x", "# ../inputs/margin/x",
                        "# 1/2/3", "# ratio 1:2", "# time 12:30",
                        "# /ES futures", "# and/or/maybe",
                        "# ~/taxes/2023/taxjson.toml",
                        "# see:~/taxes/2023/taxjson.toml",
                        "# docs/commands/x.md"):
            with self.subTest(comment=comment):
                self.assertEqual(R._redact_comment_paths(comment),
                                 (comment, 0))

    def test_function_and_check(self):
        text = "[settings]\n# inputs_dir:/srv/casey/taxes/inputs\nyear = 2025\n"
        out, rep = R.redact_project_text(text, [], {})
        self.assertEqual(out, "[settings]\n# inputs_dir:<redacted path>\n"
                              "year = 2025\n")
        self.assertEqual(rep.paths, [2])
        self.assertTrue(rep.found_anything())

    def test_no_generated_comment_has_a_path(self):
        for line in R._generated_comments():
            with self.subTest(line=line):
                self.assertEqual(R._redact_comment_paths(line)[1], 0)


if __name__ == "__main__":
    unittest.main()
