"""Fix round 7 (2026-10-29, wave D), group corp: corporate actions (backlog
TJ-1022, TJ-1025, TJ-1026, TJ-1117, TJ-1132). Synthetic data only: made-up
tickers and account ids.

Formerly: test_fix_1029_r7d_corp"""
import tempfile
import unittest
from pathlib import Path

from _qa_project import console, flat, tj
from taxjson.model.transaction import TaxTransaction as T
from taxjson.lib.missing_history import (assess_tax_year_relevance,
                                         detect_missing_history,
                                         detect_unbacked_covers,
                                         synthesize_openings)
from taxjson.lib.purchase_drafts import draft_purchases


def _project(root: Path, country: str, b_files: dict, ticker_map: str = ""):
    tz = "America/Toronto" if country == "canada" else "America/New_York"
    cur = "CAD" if country == "canada" else "USD"
    sym = "SYN.TO" if country == "canada" else "SYN"
    (root / "inputs/a").mkdir(parents=True)
    (root / "inputs/b").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "{tz}"\nyear = 2025\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        'source_currencies = []\n'
        '[accounts.a]\ntype = "sheltered"\n'
        '[accounts.b]\ntype = "taxable"\n')
    (root / "ticker.map").write_text(ticker_map)
    (root / "inputs/a/trades.tt").write_text(
        f"BUYSELL 2024-06-03 09:30:00 {sym} 1 {cur} 10 -10 0\n"
        f"SPLIT 2025-01-02 00:00:00 {sym} {sym} 2\n")
    for name, text in b_files.items():
        (root / "inputs/b" / name).write_text(text)


class TestTJ1022SplitOfAnotherBook(unittest.TestCase):
    """TJ-1022 (a regression of TJ-0832): the engines apply a SPLIT row to
    the shares of that row's book only, so a split only a sheltered or
    affiliated account reports must not re-denominate a taxable account's
    units in the checks and drafts. A split another TAXABLE account
    reports still does (TJ-0832's own tests,
    features.missing_history.test_opening_and_purchase_writers).

    Formerly: test_fix_1029_r7d_corp.TestTJ1022SplitOfAnotherBook"""

    TYPES = {"a": True, "b": False}

    def _rows(self, split_account="a"):
        return [
            T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
              quantity=10, price=10, net_amount=-100, currency="USD",
              account="b"),
            T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
              quantity=1, price=10, net_amount=-10, currency="USD",
              account=split_account),
            T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
              quantity=-20, price=10, net_amount=200, currency="USD",
              account="b"),
            T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
              quantity=2.0, account=split_account)]

    def test_sheltered_split_leaves_taxable_short(self):
        c, = detect_missing_history(self._rows(),
                                    registered_accounts=self.TYPES)
        self.assertEqual((c.account, c.peak_short), ("b", -10.0))
        r, = assess_tax_year_relevance(self._rows(), [c], 2025,
                                       registered_accounts=self.TYPES)
        self.assertTrue(r.affects_year)

    def test_label_reads_the_book_when_the_type_is_unknown(self):
        # No configured type: the label heuristic, as the rest of the walk.
        rows = self._rows("MY-TFSA")
        c, = detect_missing_history(rows)
        self.assertEqual((c.account, c.peak_short), ("b", -10.0))

    def test_affiliated_split_leaves_taxable_short(self):
        c, = detect_missing_history(self._rows("spouse"),
                                    registered_accounts={"b": False},
                                    affiliated_accounts={"spouse"})
        self.assertEqual((c.account, c.peak_short), ("b", -10.0))

    def test_taxable_split_still_reaches_taxable(self):
        # TJ-0832 stays fixed, with the types known.
        self.assertEqual(detect_missing_history(
            self._rows("c"), registered_accounts={"b": False, "c": False}),
            [])

    def test_same_book_spread_inside_the_sheltered_book(self):
        # Two sheltered accounts: one's split reaches the other.
        rows = [T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=10, price=10, net_amount=-100, currency="USD",
                  account="s1"),
                T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=1, price=10, net_amount=-10, currency="USD",
                  account="s2"),
                T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
                  quantity=-20, price=10, net_amount=200, currency="USD",
                  account="s1"),
                T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
                  quantity=2.0, account="s2")]
        self.assertEqual(detect_missing_history(
            rows, registered_accounts={"s1": True, "s2": True}), [])

    def test_earlier_sheltered_copy_keeps_the_taxable_event(self):
        # A sheltered copy dated first must not hide the taxable account's
        # own report from a third, taxable account.
        rows = self._rows("c") + [
            T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
              quantity=1, price=10, net_amount=-10, currency="USD",
              account="a"),
            T(action="SPLIT", date="2025-01-01", symbol="SYN.US",
              quantity=2.0, account="a")]
        self.assertEqual(detect_missing_history(
            rows, registered_accounts={"a": True, "b": False, "c": False}),
            [])

    def test_opening_is_sized_on_the_book(self):
        # Left in opening-date units with the split ignored: 10, not 5.
        new, _log = synthesize_openings(
            self._rows(), pairs={("SYN.US", "b")},
            registered_accounts=self.TYPES)
        self.assertEqual(sum(t.quantity for t in new
                             if t.action == "OPENING_BALANCE"), 10.0)

    def test_cover_walk_uses_the_book(self):
        # A sheltered split doubles no taxable short: a 20 cover of 10
        # short is unbacked by 10.
        rows = [T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=-10, price=10, net_amount=100, currency="USD",
                  account="b", description="SHORT."),
                T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=1, price=10, net_amount=-10, currency="USD",
                  account="a"),
                T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
                  quantity=2.0, account="a"),
                T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
                  quantity=20, price=10, net_amount=-200, currency="USD",
                  account="b", description="COVER SHORT.")]
        c, = detect_unbacked_covers(rows, registered_accounts=self.TYPES)
        self.assertEqual((c.account, c.unbacked_qty), ("b", 10.0))

    def test_draft_ignores_the_sheltered_split(self):
        from _support.ib_basis_drafts import _book, _lot, _parsed, _sell
        rows = _book(_parsed(
            _sell("SYN", "2025-03-18, 10:00:00", -10, 15, 100)
            + _lot("SYN", "2024-01-02", 10, 10, 100), account="b"))
        rows = [T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
                  quantity=2.0, account="a"),
                T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=1, price=10, net_amount=-10, currency="USD",
                  account="a")] + rows
        drafts, _gaps = draft_purchases(rows, country="usa", accounts={"b"},
                                        registered_accounts=self.TYPES)
        self.assertEqual([(d.account, d.quantity, d.cost) for d in drafts],
                         [("b", 10.0, 100.0)])

    def _checks_flag_it(self, country):
        sym = "SYN.TO" if country == "canada" else "SYN"
        cur = "CAD" if country == "canada" else "USD"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            _project(root, country, {"trades.tt":
                     f"BUYSELL 2024-06-03 09:30:00 {sym} 10 {cur} 10 -100 0\n"
                     f"BUYSELL 2025-03-03 09:30:00 {sym} -20 {cur} 10 200 0\n"
                     })
            tj(root, "run", "--no-input", check=False)
            fm = tj(root, "find-missing-history", check=False)
            self.assertIn("AFFECTS", fm.stdout)
            self.assertNotIn("No missing-cost-basis issues", fm.stdout)
            cl = tj(root, "checklist", "--run", check=False)
            line, = [ln for ln in cl.stdout.splitlines()
                     if "10. missing-history" in ln]
            self.assertIn("[!]", line)
            if country == "canada":
                self.assertIn("NOT in these totals",
                              flat(console(tj(root, "sum", check=False))))

    def test_checks_flag_the_short_canada(self):
        self._checks_flag_it("canada")

    def test_checks_flag_the_short_usa(self):
        self._checks_flag_it("usa")

    def test_cli_draft_adds_up(self):
        from _support.ib_statement_rows import HEAD, TRADES_H
        from _support.ib_basis_drafts import _lot, _sell
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            _project(root, "canada", {"ib.csv": (
                HEAD + TRADES_H
                + _sell("SYN", "2025-03-18, 10:00:00", -10, 15, 100, cur="CAD")
                + _lot("SYN", "2024-01-02", 10, 10, 100, cur="CAD"))},
                ticker_map="GLOBAL SYN SYN.TO\n")
            tj(root, "run", "--no-input", check=False)
            tj(root, "find-missing-history", "b", "--write-purchases",
               check=False)
            draft = root / "inputs/b/purchases_draft.tt.txt"
            buys = [ln for ln in draft.read_text().splitlines()
                    if ln.startswith("BUYSELL")]
            self.assertEqual(len(buys), 1, buys)
            self.assertEqual(buys[0].split()[4:5], ["10"], buys)


if __name__ == "__main__":
    unittest.main()
