"""Re-audit 2 fixes for the RBC Direct and Questrade parsers (fix lists
parsers-rbc and parsers-questrade). Every fixture is synthetic (fake
account ids 55500001 / 55500002, made-up option codes)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_close_check import (unbacked_option_closes,
                                            unbacked_option_close_messages)

from test_fix_rbc import HDR, parse_files, parse_one, row

REPO = Path(__file__).resolve().parent.parent
ACCT = "55500001"  # pii-ok (synthetic)


def _cli_run(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600, env=env)


def _project(root, year, files, extra_settings="", ticker_map=""):
    root = Path(root)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n'
        f'option_grant_timing_since = 2024\n{extra_settings}'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text(ticker_map)
    for name, text in files.items():
        (root / "inputs" / "margin" / name).write_text(text)
    return root


# RBC re-describes one contract between yearly exports: ".RCI" with the
# 2024 OPEN CONTRACT buy, ".RCI.B" with the 2025 CLOSE CONTRACT sale.
RCI_BUY = row("December 23, 2024", "Buy", "8ZZZZZ1", "", "3", "3.55",
              "-1075.70", "CAD",
              "CALL .RCI   01/15/27    46 ROGERS COMMUNICATIONS INC DA "
              "OPEN CONTRACT", settle="December 24, 2024")
RCI_SELL = row("December 29, 2025", "Sell", "8ZZZZZ1", "", "-3", "5.00",
               "1489.30", "CAD",
               "CALL .RCI.B   01/15/27    46 ROGERS COMMUNICATIONS INC CA "
               "CLOSE CONTRACT", settle="December 30, 2025")
TT_RCI = "BUYSELL 2024-12-24 09:30:00 RCI270115C00046000.TO 3 CAD 3.55 1075.70 10.70\n"
TT_RCIB = TT_RCI.replace("RCI270115", "RCI.B270115")


def _tx(**kw):
    base = dict(action="BUYSELL", time="09:30:00", currency="CAD",
                price=1.0, account="margin")
    base.update(kw)
    base.setdefault("date_settle", base["date"])
    return TaxTransaction(**base)


class TestRbcOpenCloseMarker(unittest.TestCase):
    """A2-0006 / A2-0267 / A2-0266 / A2-0095: RBC's OPEN/CLOSE CONTRACT
    marker is carried as the neutral open_close code, and a closing row
    the books cannot back is named on the console with the ticker.map
    line when the contract is held under another root."""

    def test_marker_is_carried_on_option_rows_only(self):
        stock = row("March 3, 2025", "Buy", "XYZ", "XYZ CORP", "10", "5.00",
                    "-59.95", "CAD", "XYZ CORP OPEN CONTRACT")
        txs, _err, _ = parse_files({"a.csv": RCI_BUY, "b.csv": RCI_SELL
                                    + stock})
        oc = {(t["symbol"], t["quantity"]): t.get("open_close") for t in txs}
        self.assertEqual(oc[("RCI270115C00046000.TO", 3.0)], "O")
        self.assertEqual(oc[("RCI270115C00046000.TO", -3.0)], "C")
        self.assertIsNone(oc[("XYZ.TO", 10.0)])

    def test_expiry_row_is_closing(self):
        # A2-0266: RBC's expiry of a long (Reorganization, signed -3).
        exp = row("January 19, 2026", "Reorganization", "8ZZZZZ2", "", "-3",
                  "", "0", "CAD",
                  "EXP - CALL .RCX.B 01/16/26 46 ROGERS COMMUNICATIONS INC "
                  "OPTION EXPIRATION - EXPIRED")
        txs, _err, _ = parse_one(exp)
        self.assertEqual([t.get("open_close") for t in txs], ["C"])

    def test_close_sale_under_other_root_names_the_held_contract(self):
        books = [
            _tx(date="2024-12-24", symbol="RCI270115C00046000.TO",
                quantity=3, net_amount=1075.70),
            _tx(date="2025-12-29", symbol="RCI.B270115C00046000.TO",
                quantity=-3, net_amount=1489.30, open_close="C"),
        ]
        msgs = unbacked_option_close_messages(books)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith("warning: ATTENTION: "))
        self.assertIn("GLOBAL RCI270115C00046000.TO RCI.B270115C00046000.TO",
                      msgs[0])

    def test_buy_to_close_opening_a_long_is_named(self):
        # A2-0267: the written call is in the .tt as RCX; RBC's buy-back
        # says CLOSE CONTRACT under RCX.B.
        books = [
            _tx(date="2024-12-20", symbol="RCX270115C00046000.TO",
                quantity=-3, net_amount=1054.70),
            _tx(date="2025-06-02", symbol="RCX.B270115C00046000.TO",
                quantity=3, net_amount=310.70, open_close="C"),
        ]
        f = unbacked_option_closes(books)
        self.assertEqual([(x["side"], x["partners"]) for x in f],
                         [("purchase", [("RCX270115C00046000.TO", -3.0)])])
        self.assertIn("GLOBAL RCX270115C00046000.TO RCX.B270115C00046000.TO",
                      unbacked_option_close_messages(books)[0])

    def test_adjusted_root_digit_partner(self):
        # A2-0095: the .tt holds TRX, the XCH-renamed close says TRX1.
        books = [
            _tx(date="2024-05-01", symbol="TRX260116C00055000.TO",
                quantity=5, net_amount=1038.20),
            _tx(date="2025-03-03", symbol="TRX1260116C00055000.TO",
                quantity=-5, net_amount=8986.80, open_close="C"),
        ]
        self.assertIn("GLOBAL TRX260116C00055000.TO TRX1260116C00055000.TO",
                      unbacked_option_close_messages(books)[0])

    def test_backed_close_and_opening_write_are_silent(self):
        books = [
            _tx(date="2024-12-24", symbol="RCI.B270115C00046000.TO",
                quantity=3, net_amount=1075.70),
            _tx(date="2025-12-29", symbol="RCI.B270115C00046000.TO",
                quantity=-3, net_amount=1489.30, open_close="C"),
            _tx(date="2025-12-29", symbol="ZZZ270115C00010000.TO",
                quantity=-1, net_amount=100.0, open_close="O"),
        ]
        self.assertEqual(unbacked_option_close_messages(books), [])

    def test_unrelated_root_is_not_a_partner(self):
        books = [
            _tx(date="2024-12-24", symbol="ABC270115C00046000.TO",
                quantity=3, net_amount=1075.70),
            _tx(date="2025-12-29", symbol="XYZ270115C00046000.TO",
                quantity=-3, net_amount=1489.30, open_close="C"),
        ]
        (m,) = unbacked_option_close_messages(books)
        self.assertNotIn("GLOBAL ABC", m)
        self.assertIn("find-missing-history", m)


class TestRbcCloseContractEndToEnd(unittest.TestCase):
    """A2-0006 end to end: the run console names the CLOSE CONTRACT row,
    and `taxjson handoff` accepts the re-described root and refuses the
    closed year's root."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.p24 = _project(cls.base / "p2024", 2024,
                           {"rbc_2024.csv": HDR + RCI_BUY})
        r = _cli_run(cls.p24, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        r = _cli_run(cls.p24, "close-year", "--force")
        assert r.returncode == 0, r.stdout + r.stderr
        cls.rec = cls.p24 / "filed" / "2024.json"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _p25(self, name, tt):
        p = _project(self.base / name, 2025,
                     {"rbc_2025.csv": HDR + RCI_SELL, "margin_start.tt": tt},
                     f'prior_year_record = "{self.rec}"\n')
        r = _cli_run(p, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return p, r.stdout + r.stderr

    def test_wrong_root_in_tt_is_loud_and_fails_handoff(self):
        p, out = self._p25("wrong", TT_RCI)
        self.assertIn("ATTENTION: RCI.B270115C00046000.TO", out)
        self.assertIn("GLOBAL RCI270115C00046000.TO RCI.B270115C00046000.TO",
                      out)
        h = _cli_run(p, "handoff")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        self.assertIn("marked CLOSING", h.stdout)

    def test_this_years_root_in_tt_passes_handoff(self):
        p, out = self._p25("right", TT_RCIB)
        self.assertNotIn("ATTENTION: RCI", out)
        h = _cli_run(p, "handoff")
        self.assertEqual(h.returncode, 0, h.stdout + h.stderr)
        self.assertIn("re-described", h.stdout)

    def test_expiry_under_other_root_points_at_the_map_line(self):
        # A2-0266: the EXP row of RCX.B for a long the .tt holds as RCX:
        # the run named a missing expiry row (wrong) for both legs.
        exp = row("January 19, 2026", "Reorganization", "8ZZZZZ2", "", "-3",
                  "", "0", "CAD",
                  "EXP - CALL .RCX.B   01/16/26    46 ROGERX COMMUNICATIONS "
                  "INC OPTION EXPIRATION - EXPIRED")
        tt = ("BUYSELL 2025-06-23 10:00:00 RCX260116C00046000.TO 3 CAD 3.55 "
              "1075.70 10.70\n")
        p = _project(self.base / "exp", 2026,
                     {"rbc.csv": HDR + exp, "start.tt": tt})
        r = _cli_run(p, "run", "--no-input")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("GLOBAL RCX260116C00046000.TO RCX.B260116C00046000.TO",
                      out)
        self.assertNotIn("missing its expiry", out)
        self.assertNotIn("add the missing purchase", out)


if __name__ == "__main__":
    unittest.main()
