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
import tempfile
import unittest
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO
sys.path.insert(0, str(REPO_ROOT / "src"))



class TestNamedSymbolsLookups(unittest.TestCase):
    """F8b: named_symbols(lookups=True) holds every symbol a lookup line
    names; the default (the rename rules only) is unchanged.

    Formerly: test_fix_review_low.TestNamedSymbolsLookups"""

    def test_lookup_lines(self):
        from taxjson.lib.ticker_map_rules import named_symbols
        from taxjson.lib.map_file import _parse_map_file
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text("GLOBAL QZA.US QZB.US\n"
                         "QUOTE qzq.us QZQ\n"
                         "T1135 QZT.US USA\n"
                         "CRYPTO QZC QZC-USD\n"
                         "MULT QZM.US 10\n"
                         "STABLE QZS USD\n"
                         "EXTRACT QZ WIDGET FUND | USD | QZE.U.TO\n"
                         "SPLITSHARE QZR\n")
            tm, problems, _n = _parse_map_file(p)
        self.assertEqual(problems, [])
        self.assertEqual(named_symbols(tm), {"QZA.US", "QZB.US"})
        self.assertEqual(named_symbols(tm, lookups=True),
                         {"QZA.US", "QZB.US", "QZQ.US", "QZT.US", "QZC",
                          "QZM.US", "QZS", "QZE.U.TO"})


# ------------------------------------------------ F9: account references
ACCTS = ("555-55501-13", "55500001", "555-55502-21")  # pii-ok


class TestAccountReferenceIsCut(unittest.TestCase):
    """Formerly: test_fix_review_low.TestAccountReferenceIsCut"""

    def assertNoAccount(self, text):
        for a in ACCTS + ("5550",):
            self.assertNotIn(a, text)

    def test_rbc_name(self):
        from taxjson.lib.symbol_codes import rbc_name
        cases = {
            "TFO - QZX CORP TO ACCOUNT 555-55501-13": "QZX CORP",  # pii-ok
            "TFR - QZX CORP TFR TO 55500001": "QZX CORP",  # pii-ok
            "TFR - QZX CORP TFR FROM 555-55502-21": "QZX CORP",  # pii-ok
            "TFI - QZX CORP FROM ACCOUNT 55500001": "QZX CORP",  # pii-ok
            "TFI - QZX CORP CL B FROM ACCT 55500001":  # pii-ok
                "QZX CORP CL B",
            "TFR - QZX CORP TO 555-55501-13": "QZX CORP",  # pii-ok
            "TFI - QZX CORP FROM ACCOUNT": "QZX CORP",
            # with the TRANSFER word, as before
            "TFO - QZX CORP ACCOUNT TRANSFER BOOK VALUE 900.00 TO "
            "ACCOUNT 555-55501-13": "QZX CORP",  # pii-ok
            # only a reference: no name (never a number)
            "TFR TO 55500001": "",  # pii-ok
            "TO ACCOUNT 555-55501-13": "",  # pii-ok
            # a name's own words stay
            "QZ TARGET 2030 TO 2035 FUND": "QZ TARGET 2030 TO 2035 FUND",
            "TO QZ CORP": "TO QZ CORP",
        }
        for desc, want in cases.items():
            with self.subTest(desc=desc):
                got = rbc_name(desc)
                self.assertEqual(got, want)
                self.assertNoAccount(got)

    def test_questrade_name(self):
        from taxjson.lib.symbol_codes import questrade_name
        for desc in ("QZX CORP TO ACCOUNT 555-55501-13",  # pii-ok
                     "QZX CORP TFR FROM 55500001",  # pii-ok
                     "QZX CORP FROM ACCOUNT 55500001"):  # pii-ok
            with self.subTest(desc=desc):
                got = questrade_name(desc)
                self.assertEqual(got, "QZX CORP")

    def test_rbc_scan_names_carry_no_account(self):
        from taxjson.lib import listing_suffix as LS
        from taxjson.lib.symbol_codes import exact_name
        from _support.symbols_listing_suffix import RBC_H, _rbc
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rbc.csv"
            p.write_text(
                RBC_H
                + _rbc("2026-09-03", "Transfers", "QZAX", "", "24", "0",
                       "USD", "TFI - QZALPHA MINES CORP TFR FROM "
                       "555-55502-21")  # pii-ok
                + _rbc("2026-10-05", "Transfers", "QZAX", "", "-4", "0",
                       "USD", "TFO - QZALPHA MINES CORP TO ACCOUNT "
                       "555-55501-13"))  # pii-ok
            scan = LS.scan_rbc([p])
        c = scan.cands["QZAX.US"]
        self.assertEqual(c.names, {exact_name("QZALPHA MINES CORP")})
        for shown in c.shown.values():
            self.assertNoAccount(shown)
        self.assertEqual(scan.names["QZAX.US"],
                         {exact_name("QZALPHA MINES CORP")})


if __name__ == "__main__":
    unittest.main()
