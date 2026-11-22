"""Pre-release review 022 (synthetic QZ-style data only).

M2  a name-only truncation match needs width evidence: a complete last
    word that starts a longer word is not a cut.
M3  a broker's event wording is cut only from Questrade descriptions, and
    a designator in the cut part is carried / refuses one-sided tolerance.
M4  a long whitespace run in a name is collapsed before any regex.
L3  a listing joined / suggested must be one ticker.map token.
M5  a short carried through a rename keeps its pair in the year scope.
L1  a quoted broker description in a .diag is never a suggestion.
L2  `ticker-map --write` keeps a symlinked ticker.map a link (mode kept).
L4  --outside-year counts only its own entries, refuses a non-object
    entry before writing, and keeps a symlinked file a link.
L5  the wash radar honours --transfers-as-acquisitions.
M1  the default transfer policy lists the netted moves (cross-account and
    zero-net clusters) inside a loss's window in its one warning; the
    main-book SPLIT guard holds in cross-account netting.

Formerly: test_fix_prerelease_022
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr

from tax_rules import rule
from tax_rules.dual import gains_both, tx
from taxjson.rules import ids as R
import _paths


def _m1_book():
    """The reviewer's case: a taxable loss on 2025-04-15; 100 shares
    leave the RRSP on 04-20 and arrive in the TFSA on 04-21."""
    main = [tx("BUYSELL", "2025-01-10", "SAMPXF.US", 100, -5000.0),
            tx("BUYSELL", "2025-04-15", "SAMPXF.US", -100, 4000.0)]
    shel = [tx("TRANSFER", "2025-04-20", "SAMPXF.US", -100, 0.0,
               account="rrsp"),
            tx("TRANSFER", "2025-04-21", "SAMPXF.US", 100, 0.0,
               account="tfsa")]
    return main, shel


class TestM1NettedMovesInLossWindows(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestM1NettedMovesInLossWindows"""
    @rule(R.CA_SL_16)
    @rule(R.US_WASH_23)
    def test_cross_account_move_in_the_window_is_listed(self):
        main, shel = _m1_book()
        r = gains_both(main, sheltered=shel, year=2025)
        for c, rule_words in (("canada", "the loss may be superficial "
                               "(s.54)"),
                              ("usa", "the loss may be a wash sale")):
            with self.subTest(country=c):
                self.assertEqual(r[c]["transfers_in_loss_windows"], [{
                    "kind": "move", "account": "rrsp→tfsa",
                    "symbol": "SAMPXF.US", "from_accounts": ["rrsp"],
                    "to_accounts": ["tfsa"], "qty": 100.0,
                    "date": "2025-04-20", "date_end": "2025-04-21",
                    "loss_date": "2025-04-15", "loss_account": "margin"}])
                err = " ".join(r[c]["_stderr"].split())
                self.assertIn("1 transfer in a taxable loss's 30-day "
                              "window counted as an account move", err)
                self.assertIn("SAMPXF.US 100 moved rrsp→tfsa 2025-04-20/21 "
                              "inside the 2025-04-15 loss window in margin "
                              "— if one leg was a contribution, "
                              + rule_words, err)
                # Still a move: the loss stands (the user decides).
                self.assertAlmostEqual(r[c]["summary"]["total_gain"],
                                       -1000.0, places=2)

    @rule(R.CA_SL_16)
    @rule(R.US_WASH_23)
    def test_move_outside_the_window_is_not_listed(self):
        main, _ = _m1_book()
        shel = [tx("TRANSFER", "2025-06-20", "SAMPXF.US", -100, 0.0,
                   account="rrsp"),
                tx("TRANSFER", "2025-06-21", "SAMPXF.US", 100, 0.0,
                   account="tfsa")]
        r = gains_both(main, sheltered=shel, year=2025)
        for c in r:
            self.assertNotIn("transfers_in_loss_windows", r[c])

    @rule(R.CA_SL_17)
    @rule(R.US_WASH_24)
    def test_strict_policy_unchanged(self):
        from taxjson.engine.rows import AmbiguousTransferDateError
        from taxjson.lib.pipeline import GainsRequest, run_gains
        main, shel = _m1_book()
        for c in ("canada", "usa"):
            with self.subTest(country=c), \
                    redirect_stderr(io.StringIO()), \
                    self.assertRaises(AmbiguousTransferDateError):
                run_gains(list(main), list(shel), (), GainsRequest(
                    country=c, year=2025, taxable=True,
                    transfers_as_acquisitions=True))

    def test_main_book_split_blocks_cross_account_netting(self):
        from taxjson.lib.pipeline import _net_cross_account_transfers
        shel = [tx("TRANSFER", "2025-06-01", "SAMPXF.US", -100, 0.0,
                   account="rrsp"),
                tx("TRANSFER", "2025-06-03", "SAMPXF.US", 100, 0.0,
                   account="tfsa")]
        split = [tx("SPLIT", "2025-06-02", "SAMPXF.US", 2, 0.0)]
        for guard in (False, True):
            with self.subTest(near_trade_guard=guard):
                got = _net_cross_account_transfers(
                    list(shel), main_transactions=split,
                    near_trade_guard=guard)
                self.assertEqual(len(got), 2)
        # Without the split the default policy nets it.
        self.assertEqual(_net_cross_account_transfers(
            list(shel), main_transactions=[], near_trade_guard=False), [])


REPO = _paths.REPO


def _env():
    return dict(os.environ, PYTHONPATH=str(REPO / "src"),
                TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")


class TestL5RadarTransferPolicy(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestL5RadarTransferPolicy"""
    def _radar(self, td, *extra):
        tax, shl, out = (Path(td) / n for n in ("tax.json", "shl.json",
                                                 "out.json"))
        tax.write_text(json.dumps({"transactions": [
            {"action": "BUYSELL", "date": "2026-01-05", "time": "09:30:00",
             "symbol": "QZXF.TO", "quantity": 100, "price": 50,
             "net_amount": -5000.0, "currency": "CAD",
             "account": "margin"},
            {"action": "BUYSELL", "date": "2026-01-20", "time": "09:30:00",
             "symbol": "QZXF.TO", "quantity": -100, "price": 40,
             "net_amount": 4000.0, "currency": "CAD",
             "account": "margin"}]}))
        shl.write_text(json.dumps({"transactions": [
            {"action": "TRANSFER", "date": "2026-01-25", "time": "09:30:00",
             "symbol": "QZXF.TO", "quantity": 100, "net_amount": 0.0,
             "currency": "CAD", "account": "rrsp"}]}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
             "--country", "canada", "--taxable", str(tax), "--sheltered",
             str(shl), "--date", "2026-02-01", "--json-out", str(out),
             *extra], capture_output=True, text=True, env=_env(),
            timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(out.read_text())
        return {s["category"]: len(s["rows"]) for s in doc["sections"]}

    @rule(R.CA_SL_16, R.CA_SL_17)
    def test_default_custody_move_strict_acquisition(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._radar(td).get("VIOLATION"), 0)
            self.assertEqual(self._radar(
                td, "--transfers-as-acquisitions").get("VIOLATION"), 1)

    def test_run_passes_the_project_policy(self):
        from taxjson.pipeline.stages import _radar_engine_args
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for val, want in (("true", True), ("false", False)):
                (root / "taxjson.toml").write_text(
                    f'[settings]\ncountry = "canada"\nyear = 2025\n'
                    f'transfers_as_acquisitions = {val}\n')
                args = _radar_engine_args([], root,
                                          "canada")
                self.assertEqual("--transfers-as-acquisitions" in args, want)


if __name__ == "__main__":
    unittest.main()
