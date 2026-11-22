"""`tjs edge-cases` (lib/edge_cases): the 2026-10-26 report on it
(backlog TJ-0282, TJ-0283), engine-backed — run_gains computes every
gains and inventory row from synthetic trades (ZZ* tickers, made-up
amounts), as `tjs run` writes them to work/.

TJ-0282: the denied losses "still held at the end of <year>" come from
the Canadian engine's pools at the year end (`inventory_year_end`), not
from its final inventory, so a later sale or purchase leaves them alone.

TJ-0283: a loss whose window spans Dec 31 and that the engine denied
nothing of gets the reason the books show (`allowed_by`); the report
never says "the replacement was not still held on day 30" unproved.

Formerly: test_fix_edge_cases_1026
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.edge_cases import analyze, render_text
from _support.ca_edge_cases import STOCK, _t, DENIED, _edge


def _deferred(edge, sym=STOCK):
    return [(r["account"], r["qty"], r["deferred"], r["as_of"])
            for r in edge["year_boundary"]["deferred_at_year_end"]
            if r["symbol"] == sym]


def _window(edge, sym):
    (w,) = [w for w in edge["year_boundary"]["loss_windows"]
            if w["symbol"] == sym]
    return w


class TestDeferredAtYearEnd(unittest.TestCase):
    """TJ-0282: the year-end pools, whatever comes after.

    Formerly: test_fix_edge_cases_1026.TestDeferredAtYearEnd"""

    def test_next_year_partial_sale(self):
        doc, edge, text = _edge({"margin": DENIED + [
            _t("margin", "2026-06-01", "2026-06-02", STOCK, -2, 200)]})
        # The final inventory is after the sale; the year end is not.
        (fin,) = [h for h in doc["inventory"] if h["symbol"] == STOCK]
        self.assertEqual((fin["qty"], fin["deferred_wash"]), (3, 150))
        self.assertEqual(_deferred(edge),
                         [("margin", 5, 250.0, "2025-12-31")])
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertIn("250.00 of denied losses sits in the cost of the 5 "
                      "units still held at the end of 2025", r["why"])
        # The text: the same row and reason.
        self.assertIn("DENIED LOSSES CARRIED IN POSITIONS HELD AT THE END "
                      "OF 2025 (1)", text)
        self.assertIn(r["why"], " ".join(text.split()))

    def test_next_year_full_sale_keeps_the_holding(self):
        doc, edge, _ = _edge({"margin": DENIED + [
            _t("margin", "2026-06-01", "2026-06-02", STOCK, -5, 500)]})
        self.assertFalse([h for h in doc["inventory"]
                          if h["symbol"] == STOCK])
        self.assertEqual(_deferred(edge),
                         [("margin", 5, 250.0, "2025-12-31")])

    def test_next_year_purchase_is_not_counted(self):
        _, edge, _ = _edge({"margin": DENIED + [
            _t("margin", "2026-06-01", "2026-06-02", STOCK, 7, -700)]})
        self.assertEqual(_deferred(edge),
                         [("margin", 5, 250.0, "2025-12-31")])

    def test_later_rows_change_nothing(self):
        # Appending unrelated later trades leaves the year end as it was.
        _, alone, _ = _edge({"margin": DENIED})
        _, more, _ = _edge({"margin": DENIED + [
            _t("margin", "2026-02-02", "2026-02-03", "ZZX.TO", 4, -40),
            _t("margin", "2026-03-02", "2026-03-03", STOCK, -1, 100),
            _t("margin", "2026-04-01", "2026-04-02", STOCK, 3, -300)]})
        self.assertEqual(_deferred(alone), _deferred(more))
        self.assertEqual(alone["year_boundary"]["deferred_at_year_end"],
                         more["year_boundary"]["deferred_at_year_end"])

    def test_january_replacement_is_not_held_at_year_end(self):
        # A December loss whose replacement is bought in January: the
        # denial goes into the January lot, which was not held on Dec 31.
        # The window across Dec 31 says so; the year-end list has none.
        _, edge, _ = _edge({"margin": [
            _t("margin", "2025-11-03", "2025-11-04", STOCK, 10, -1000),
            _t("margin", "2025-12-15", "2025-12-16", STOCK, -10, 500),
            _t("margin", "2026-01-05", "2026-01-06", STOCK, 10, -600)]})
        self.assertEqual(_deferred(edge), [])
        w = _window(edge, STOCK)
        self.assertEqual(w["denied"], 500.0)
        self.assertIsNone(w["allowed_by"])
        self.assertIn("added to the replacement's cost", w["why"])

    def test_blended_accounts_split_at_the_year_end(self):
        # Two taxable accounts, one pool (s.47): each account's share is
        # its balance on Dec 31, not after margin2's 2026 sale.
        _, edge, _ = _edge({
            "margin": DENIED,
            "margin2": [_t("margin2", "2025-12-01", "2025-12-02", STOCK,
                           5, -500),
                        _t("margin2", "2026-06-01", "2026-06-02", STOCK,
                           -5, 500)]})
        rows = sorted(_deferred(edge))
        self.assertEqual([(a, q, s) for a, q, _d, s in rows],
                         [("margin", 5, "2025-12-31"),
                          ("margin2", 5, "2025-12-31")])
        # The pool's 250.00 is shared by the units held, half each.
        self.assertAlmostEqual(sum(d for _a, _q, d, _s in rows), 250.0,
                               places=2)
        self.assertAlmostEqual(rows[0][2], rows[1][2], places=2)

    def test_crypto_pool(self):
        coin = "ZZQ"
        _, edge, _ = _edge({"coins": [
            _t("coins", "2025-11-03", "2025-11-03", coin, 4, -400),
            _t("coins", "2025-11-17", "2025-11-17", coin, -2, 100),
            _t("coins", "2026-03-02", "2026-03-02", coin, -1, 60)]},
            crypto=("coins",))
        self.assertEqual(_deferred(edge, coin),
                         [("coins", 2, 100.0, "2025-12-31")])

    def test_trade_date_project_reads_trade_dates(self):
        # The engine's pools run on settlement dates (CA-SL-01), but a
        # trade-date project reads its year end on trade dates (backlog
        # TJ-0343; rules.ca.rpt.test_trade_date_year_end has the sale that settles in
        # January): no note.
        doc, edge, _ = _edge({"margin": DENIED}, tax_date="trade")
        self.assertEqual(doc["inventory_year_end_basis"], "trade")
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertIn("still held at the end of 2025;", r["why"])
        sdoc, _settle, _ = _edge({"margin": DENIED})
        self.assertNotIn("inventory_year_end_basis", sdoc)

    def test_books_without_the_year_end_say_so(self):
        # A gains file written before inventory_year_end: its final
        # inventory, said to be the end of the books. Both engines now
        # write the year end (the US one since TJ-0284), so a re-run
        # gives it in either country.
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "work").mkdir()
                (root / "work" / "margin_base.json").write_text(
                    json.dumps({"transactions": []}))
                (root / "work" / "margin_gains_wash.json").write_text(
                    json.dumps({"transactions": [], "inventory": [
                        {"symbol": STOCK, "qty": 3.0, "total_cost": 450.0,
                         "currency": "CAD", "deferred_wash": 150.0}]}))
                cfg = {"settings": {"year": 2025, "country": country},
                       "accounts": {"margin": {"type": "taxable"}}}
                edge = analyze(root, cfg)
                (r,) = edge["year_boundary"]["deferred_at_year_end"]
                self.assertEqual(r["as_of"], "end_of_data")
                self.assertIn("held at the end of the books, which may be "
                              "after 2025", r["why"])
                self.assertIn("(re-run `tjs run` for the year-end figure)",
                              r["why"])
                self.assertNotIn("end of 2025;", r["why"])
                text = "\n".join(render_text(edge, details=True))
                self.assertIn("HELD AT THE END OF THE BOOKS (1)", text)


if __name__ == "__main__":
    unittest.main()
