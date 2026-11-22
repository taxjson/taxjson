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

from tax_rules import rule  # noqa: E402
from taxjson.rules import ids as R  # noqa: E402


# ------------------------------------------------ F8a: IB temporary symbol
from taxjson.lib.brokerages import base as _base  # noqa: E402
from taxjson.lib.brokerages.ib_extractor import IbBrokerage  # noqa: E402
from _support.ib_statement_rows import _parse_account  # noqa: E402
from _support.ib_temp_symbol import STAMP, _TEMP_SYMBOL_BODY  # noqa: E402

TEMP = f"{STAMP}QZHN"
BODY = _TEMP_SYMBOL_BODY


def _project_parse(tmap, lone=False):
    """Parse BODY as inputs/margin/ib.csv of a project whose ticker.map
    is `tmap` (no map loaded: the parser finds the project's). ->
    (symbols, stderr)."""
    import contextlib
    import io
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        acct = Path(td) / "inputs" / "margin"
        acct.mkdir(parents=True)
        p = acct / "ib.csv"
        p.write_text(BODY)
        (Path(td) / "ticker.map").write_text(tmap)
        with contextlib.redirect_stderr(err):
            b = IbBrokerage()
            if not lone:
                b.account_context = IbBrokerage.prepare_files([p])
            txs = b.parse_file(p)
    return {t["symbol"] for t in txs}, err.getvalue()


@rule(R.CA_ACB_RENAME)
class TestIbTempFoldMapWins(unittest.TestCase):
    """A ticker.map line naming IB's temporary symbol decides it.

    Formerly: test_fix_review_low.TestIbTempFoldMapWins"""

    def tearDown(self):
        _base.set_ticker_map(None)

    def _loaded(self, tmap):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(tmap)
            _base.set_ticker_map(p)
        return _parse_account({"ib.csv": BODY})

    def assertKept(self, syms, err):
        self.assertIn(f"{TEMP}.US", syms, err)
        self.assertIn(f"a ticker.map line names {TEMP}", err)
        self.assertIn("the map line decides", err)
        self.assertNotIn("its rows are booked as QZHN", err)
        self.assertNotIn("several symbols", err)

    def test_loaded_map_lines_of_any_keyword(self):
        for line in (f"GLOBAL {TEMP}.US QZQQ.US",
                     f"RENAME {TEMP}.US QZQQ.US 2026-06-29",
                     f"TOBASE {TEMP}.US QZHN.TO",
                     f"DISTINCT {TEMP}.US QZHN.US",
                     f"EXTRACT QZHN CORP | USD | {TEMP}.US",
                     f"QUOTE {TEMP} QZHN"):
            with self.subTest(line=line):
                txs, err = self._loaded(line + "\n")
                self.assertKept({t["symbol"] for t in txs}, err)

    def test_project_map_account_and_lone_parse(self):
        for lone in (False, True):
            with self.subTest(lone=lone):
                syms, err = _project_parse(
                    f"DISTINCT {TEMP}.US QZHN.US\n", lone=lone)
                self.assertKept(syms, err)

    def test_a_map_naming_other_symbols_keeps_the_fold(self):
        txs, err = self._loaded("QUOTE QZHN.US QZHN\n"
                                "GLOBAL QZOO.US QZPP.US\n")
        self.assertEqual({t["symbol"] for t in txs}, {"QZHN.US"}, err)
        self.assertIn(f"{TEMP} is IB's temporary symbol for QZHN", err)
        syms, err = _project_parse("GLOBAL QZOO.US QZPP.US\n")
        self.assertEqual(syms, {"QZHN.US"}, err)


if __name__ == "__main__":
    unittest.main()
