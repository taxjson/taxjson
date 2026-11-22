"""Pre-release review fixes (low): synthetic data only.

F5  — a command started without stdout or stderr (`>&-`, `2>&-`: Python
      sets the stream to None) printed a traceback / stopped with exit 1:
      lib/out.settling_streams wrapped the None stream.
F8b — the listing-suffix correction (lib/listing_suffix) moved rows away
      from a QUOTE / T1135 line written for the `.US` spelling: a
      ticker.map line naming the symbol in any keyword now blocks it.
F8a — IB's temporary-symbol fold (ib_extractor._ib_temp_folds) never
      consulted ticker.map: a line naming the stamped symbol now wins (the
      fold is skipped, the Info line says the map line decides).
F9  — symbol_codes.rbc_name kept a transfer's account reference ("TO
      ACCOUNT n", "TFR TO n", "TFR FROM n") when no TRANSFER word came
      before it, so an account number reached a name, a suggestion or a
      warning. Synthetic 555-style account numbers only.

Formerly: test_fix_review_low
"""
import sys
import unittest
import _paths

REPO_ROOT = _paths.REPO
sys.path.insert(0, str(REPO_ROOT / "src"))

from tax_rules import rule  # noqa: E402
from taxjson.rules import ids as R  # noqa: E402


from _support.symbols_listing_suffix import _RunBase  # noqa: E402


@rule(R.CA_XLIST_02)
class TestListingSuffixLookupLinesWin(_RunBase, unittest.TestCase):
    """A QUOTE or T1135 line written for the `.US` spelling keeps the
    row currency's listing (with no map both are read as `.TO`:
    rules.ca.xlist.test_listing_read_from_evidence.TestRunCanada).

    Formerly: test_fix_review_low.TestListingSuffixLookupLinesWin"""
    MAP = "QUOTE QZBX.US QZBX\nT1135 QZAX.US USA\n"

    def test_lookup_lines_keep_the_listing(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        moved = self.symbols("qt_questrade_transfers")
        self.assertIn(("TRANSFER", "QZBX.US"), moved)
        self.assertIn(("TRANSFER", "QZAX.US"), moved)
        self.assertFalse({s for _a, s in moved} & {"QZAX.TO", "QZBX.TO"})
        from taxjson.lib import listing_suffix as LS
        st = LS.read_state(LS.state_path(self.root / "work", "qt",
                                         "questrade"))
        self.assertEqual(st.get("corrected") or {}, {})
        self.assertIn("ticker.map", st["kept"]["QZBX.US"]["reason"])


if __name__ == "__main__":
    unittest.main()
