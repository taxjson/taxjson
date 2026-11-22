"""The privacy rule for identifiers (TJ-2063, TJ-2352): one implementation,
`taxjson.lib.privacy`; the parsers' toolbox re-exports `mask_id`; the
helpers lib, pipeline and bin kept call it; and one grammar for an id inside
a file name or a message, the one a row's `source` has followed."""
import pathlib
import re
import unittest

from taxjson.lib import corp_actions, crypto_sends, holdings_dir, missing_history, positions_reports, privacy
from taxjson.lib.brokerages import base
from taxjson.parsers.toolbox.masking import mask_id
from taxjson.pipeline import sanity


class TestOneMaskEverywhere(unittest.TestCase):
    def test_the_toolbox_re_exports_the_library_rule(self):
        self.assertIs(mask_id, privacy.mask_id)

    def test_the_kept_helpers_follow_the_rule(self):
        self.assertEqual(sanity._mask_ids_in_path("in/U1234567_2025.csv"), "in/U1***_2025.csv")
        self.assertEqual(holdings_dir.mask("holdings/99900001.csv"), "holdings/99***.csv")
        self.assertEqual(corp_actions._mask_account("99900001+99900002"), "99***+99***")
        self.assertEqual(corp_actions._mask_account(""), "")
        self.assertEqual(positions_reports.mask_account(" U1234567 "), "U1***")
        self.assertEqual(positions_reports.mask_account(None), "")
        self.assertEqual(missing_history._mask_id("abcdef0123"), "ab***")
        self.assertEqual(missing_history._mask_id(""), "?")
        self.assertEqual(crypto_sends.mask_ref("LQ9ABC-xfer"), "LQ***")

    def test_no_inline_spelling_is_left(self):
        root = pathlib.Path(privacy.__file__).resolve().parents[1]
        spelled = re.compile(r"\[:2\]\}?\s*\*\*\*|\[:2\]\s*\+\s*['\"]\*\*\*")
        hits = sorted(str(f.relative_to(root)) for f in root.rglob("*.py")
                      if f.name != "privacy.py" and spelled.search(f.read_text(encoding="utf-8")))
        self.assertEqual(hits, [])


class TestOneGrammarForIdsInText(unittest.TestCase):
    """`mask_ids` and `shown_name` read the same grammar: an IB account id
    with its prefix, a digit run of 7 to 12 that is not a date; a run of
    5 or 6 digits and a date stay readable."""

    def test_the_grammar(self):
        self.assertEqual(privacy.mask_ids("U1234567_20250101_20251231.csv"), "U1***_20250101_20251231.csv")
        self.assertEqual(privacy.mask_ids("holdings/DU1234567.csv"), "holdings/DU***.csv")   # pii-ok
        self.assertEqual(privacy.mask_ids("acct 99900001 and U1234567"), "acct 99*** and U1***")
        self.assertEqual(privacy.mask_ids("statement_20251231.csv"), "statement_20251231.csv")
        self.assertEqual(privacy.mask_ids("x_12345 2025-12 SAMPLF"), "x_12345 2025-12 SAMPLF")

    def test_a_rows_source_and_a_message_agree(self):
        for name in ("U1234567_2025.csv", "DU1234567.csv", "99900001_positions.csv",   # pii-ok
                     "trades_20250131.csv", "sample.csv"):
            self.assertEqual(base.shown_name(pathlib.Path(name)), privacy.mask_ids(name), name)


if __name__ == "__main__":
    unittest.main()
