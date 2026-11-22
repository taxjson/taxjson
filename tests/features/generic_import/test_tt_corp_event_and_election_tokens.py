"""Fix round 7 (2026-10-29, wave D), group corp: corporate actions (backlog
TJ-1022, TJ-1025, TJ-1026, TJ-1117, TJ-1132). Synthetic data only: made-up
tickers and account ids.

Formerly: test_fix_1029_r7d_corp"""
import tempfile
import unittest
from pathlib import Path

from _qa_project import console, flat, tj


class TestTJ1117CorpProvenanceRoundTrip(unittest.TestCase):
    """TJ-1117: a .tt round trip dropped corp_event_id and corp_election, so
    an s.86.1 reduction written to .tt read as a plain return of capital.

    Formerly: test_fix_1029_r7d_corp.TestTJ1117CorpProvenanceRoundTrip"""

    def _rows(self):
        from taxjson.lib import corp_actions as CA
        ev = CA.CorporateAction(
            date="2025-01-02", time="10:00:00", action_type="spinoff",
            source_symbol="PARENT.US", source_isin="US0000000001",
            target_symbol="CHILD.US", target_isin="US0000000002",
            ratio_new=1, ratio_old=10, qty_disposed=0, qty_received=1,
            fmv=500, target_fmv=500, currency="CAD", target_currency="CAD",
            account="margin", fractional_delivery=True)
        return CA.resolve_event(ev, "rollover_s_86_1", "canada",
                                {"allocated_acb_cad": 500})

    def test_line_round_trip_keeps_the_fields(self):
        from taxjson.lib.tt_format import parse_tt_line, tx_to_tt_line
        for row in self._rows():
            with self.subTest(action=row["action"]):
                line = tx_to_tt_line(row)
                self.assertIn(f"corp={row['corp_event_id']}", line)
                self.assertIn("election=rollover_s_86_1", line)
                back = parse_tt_line(line, "margin")
                self.assertEqual(back["corp_event_id"], row["corp_event_id"])
                self.assertEqual(back["corp_election"], "rollover_s_86_1")
                if row["action"] == "ADJUST":
                    self.assertEqual(back["corp_parent_qty"], 10.0)

    def test_a_plain_row_writes_no_token(self):
        from taxjson.lib.tt_format import tx_to_tt_line
        line = tx_to_tt_line(dict(
            action="ADJUST", date="2025-01-02", date_settle="2025-01-02",
            time="10:00:00", symbol="ZZQ.TO", currency="CAD", net_amount=-5))
        self.assertNotIn("corp=", line)
        self.assertNotIn("election=", line)

    def test_a_bad_token_is_refused(self):
        from taxjson.lib.tt_format import parse_tt_line
        base = "ADJUST 2025-01-02 10:00:00 ZZQ.TO CAD -5.00000 "
        for bad in ("election=rollover_s_86_1", "corp=e election=Bad-Name",
                    "corp=e parent_qty=nope", "corp=e parent_qty=0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_tt_line(base + bad, "margin")
        with self.assertRaises(ValueError):
            parse_tt_line("BUYSELL 2025-01-02 10:00:00 ZZQ.TO 1 CAD 5 5 0 "
                          "corp=e parent_qty=5", "margin")

    def test_the_tt_book_moves_the_reduction_to_the_sale(self):
        from taxjson.lib.tt_format import tx_to_tt_line
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs/margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "ticker.map").write_text("")
            lines = ["BUYSELL 2024-01-02 09:30:00 PARENT.US 10 CAD 100 1000 0",
                     "BUYSELL 2024-12-31 09:30:00 PARENT.US -10 CAD 100 1000 0"]
            lines += [tx_to_tt_line(r) for r in self._rows()]
            (root / "inputs/margin/book.tt").write_text(
                "\n".join(lines) + "\n")
            tj(root, "run", "--no-input", check=False)
            sum24 = flat(tj(root, "sum", check=False).stdout)
            self.assertIn("margin 500.00 0.00 500.00", sum24)
            toml = root / "taxjson.toml"
            toml.write_text(toml.read_text().replace("year = 2024",
                                                     "year = 2025"))
            run25 = tj(root, "run", "--no-input", check=False)
            self.assertNotIn("EMPTY pool", console(run25))
            sum25 = flat(tj(root, "sum", check=False).stdout)
            self.assertIn("margin 0.00 0.00 0.00", sum25)


if __name__ == "__main__":
    unittest.main()
