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
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_split_gains import split_for_account
from taxjson.lib.core import TaxTransaction
from taxjson.lib.edge_cases import analyze, render_text
from taxjson.lib.pipeline import GainsRequest, run_gains

STOCK = "ZZY.US"
CALL = "ZZZ280121C00010000.US"


def _t(acct, day, settle, sym, qty, net):
    return TaxTransaction(account=acct, action="BUYSELL", date=day,
                          date_settle=settle, time="10:00:00", symbol=sym,
                          quantity=qty, net_amount=net, currency="CAD",
                          id=f"{acct}-{day}-{sym}-{qty}")


# A 250.00 loss on 2025-11-17 (settled 11-18), denied: the other 5 units
# are held on day 30. 5 units and 250.00 deferred at the end of 2025.
DENIED = [_t("margin", "2025-11-03", "2025-11-04", STOCK, 10, -1000),
          _t("margin", "2025-11-17", "2025-11-18", STOCK, -5, 250)]


def _edge(books, sheltered=(), *, tax_date="settle", crypto=(),
          settings=None, **req):
    """analyze() and the --details text of a project whose taxable
    accounts are `books` ({account: rows}) and sheltered account rrsp
    `sheltered`; more than one taxable account is the blended pass split
    per account (taxjson-split-gains), as the run does."""
    settings = dict({"year": 2025, "country": "canada",
                     "tax_date": tax_date}, **(settings or {}))
    accounts = {n: dict({"type": "taxable"},
                        **({"crypto": True} if n in crypto else {}))
                for n in books}
    if sheltered:
        accounts["rrsp"] = {"type": "sheltered"}
    taxable = [t for rows in books.values() for t in rows]
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        doc = run_gains(taxable, list(sheltered),
                        req=GainsRequest(country="canada", year=2025,
                                         taxable=True, tax_date=tax_date,
                                         **req))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        bases = dict(books, **({"rrsp": list(sheltered)} if sheltered
                               else {}))
        for n, rows in bases.items():
            (work / f"{n}_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in rows]}))
        for n, rows in books.items():
            out = doc if len(books) == 1 else split_for_account(
                doc, n, [t.to_dict() for t in rows])
            (work / f"{n}_gains_wash.json").write_text(json.dumps(out))
        cfg = {"settings": settings, "accounts": accounts}
        if crypto:
            cfg["settings"]["local_timezone"] = "America/Toronto"
        edge = analyze(root, cfg)
        text = "\n".join(render_text(edge, details=True, width_=200))
    return doc, edge, text


def _deferred(edge, sym=STOCK):
    return [(r["account"], r["qty"], r["deferred"], r["as_of"])
            for r in edge["year_boundary"]["deferred_at_year_end"]
            if r["symbol"] == sym]


def _window(edge, sym):
    (w,) = [w for w in edge["year_boundary"]["loss_windows"]
            if w["symbol"] == sym]
    return w


class TestDeferredAtYearEnd(unittest.TestCase):
    """TJ-0282: the year-end pools, whatever comes after."""

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
        # TJ-0343; test_fix_1029_edgecases has the sale that settles in
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


class TestAllowedReason(unittest.TestCase):
    """TJ-0283: why a loss across Dec 31 was allowed, from the books."""

    # A written call bought back at a loss on 2025-12-15 (settled 12-16),
    # 2 contracts bought: one closes the short, one opens a long. rrsp
    # buys the same series on 2026-01-02. On day 30 (2026-01-15) both
    # longs are held.
    OPTION = [_t("margin", "2025-12-10", "2025-12-11", CALL, -1, 100),
              _t("margin", "2025-12-15", "2025-12-16", CALL, 2, -300)]
    RRSP = [_t("rrsp", "2026-01-02", "2026-01-05", CALL, 1, -150)]

    def _option(self, timing, buyback):
        req = dict(option_premium_timing=timing,
                   option_buyback_loss_superficial=buyback,
                   option_grant_since=2025 if timing == "grant" else None)
        settings = {"option_premium_timing": timing,
                    "option_buyback_loss_superficial": buyback,
                    "option_grant_timing_since": 2025}
        return _edge({"margin": self.OPTION}, self.RRSP,
                     settings=settings, **req)

    def test_buyback_exempt_though_held_on_day_30(self):
        for timing, raw in (("grant", -150.0), ("close", -50.0)):
            with self.subTest(timing=timing):
                _, edge, text = self._option(timing, False)
                w = _window(edge, CALL)
                self.assertEqual((w["raw_loss"], w["denied"]), (raw, 0.0))
                self.assertEqual(w["allowed_by"], "buyback_exempt")
                self.assertIn("a written option's loss is exempt from the "
                              "rule (option_buyback_loss_superficial = "
                              "false), so its window is not tested",
                              w["why"])
                self.assertNotIn("day 30", w["why"])
                self.assertIn(w["why"], " ".join(text.split()))

    def test_buyback_tested_when_the_project_opts_in(self):
        _, edge, _ = self._option("close", True)
        w = _window(edge, CALL)
        self.assertGreater(w["denied"], 0)
        self.assertIsNone(w["allowed_by"])
        self.assertNotIn("exempt", w["why"])

    # A 250.00 loss on 2025-12-19 (settled 12-22) whose window runs to
    # 2026-01-21; a rebuy on 2026-01-05 (settled 01-06).
    LOSS = [_t("margin", "2025-11-03", "2025-11-04", STOCK, 5, -1000),
            _t("margin", "2025-12-19", "2025-12-19", STOCK, -5, 750)]
    LOSS[1].date_settle = "2025-12-22"
    REBUY = _t("margin", "2026-01-05", "2026-01-06", STOCK, 5, -760)

    def test_allowloss_position_though_the_replacement_is_kept(self):
        ov = {"account": "margin", "date": "2025-12-19", "symbol": STOCK,
              "qty": None, "reason": "a synthetic filing position",
              "where": "inputs/margin/x.tt:1",
              "line": f'ALLOWLOSS 2025-12-19 {STOCK} '
                      f'reason="a synthetic filing position"'}
        _, edge, text = _edge({"margin": self.LOSS + [self.REBUY]},
                              loss_overrides=(ov,))
        w = _window(edge, STOCK)
        self.assertEqual(w["denied"], 0.0)
        self.assertEqual(w["allowed_by"], "filing_position")
        self.assertIn("allowed by your filing position: ALLOWLOSS "
                      "inputs/margin/x.tt:1", w["why"])
        self.assertIn('"a synthetic filing position"', w["why"])
        self.assertNotIn("day 30", w["why"])
        self.assertIn(w["why"], " ".join(text.split()))

    def test_replacement_sold_before_day_30(self):
        _, edge, _ = _edge({"margin": self.LOSS + [
            self.REBUY,
            _t("margin", "2026-01-12", "2026-01-13", STOCK, -5, 770)]})
        w = _window(edge, STOCK)
        self.assertEqual(w["denied"], 0.0)
        self.assertEqual(w["allowed_by"], "not_held_day30")
        self.assertIn("allowed: no units were held at the end of day 30 "
                      "(2026-01-21)", w["why"])

    def test_replacement_kept_is_denied(self):
        _, edge, _ = _edge({"margin": self.LOSS + [self.REBUY]})
        w = _window(edge, STOCK)
        self.assertEqual(w["denied"], 250.0)
        self.assertIsNone(w["allowed_by"])


class TestAllowedReasonFromTheBooks(unittest.TestCase):
    """The rows the engine writes for a loss the rule does not reach, and
    one it allowed for a reason the books do not record."""

    def _edge(self, gain, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            (work / "margin_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in rows]}))
            (work / "margin_gains_wash.json").write_text(json.dumps(
                {"transactions": [gain], "inventory": [],
                 "inventory_year_end": []}))
            cfg = {"settings": {"year": 2025, "country": "canada",
                                "tax_date": "settle"},
                   "accounts": {"margin": {"type": "taxable"}}}
            return _window(analyze(root, cfg), STOCK)

    ROWS = [_t("margin", "2025-11-03", "2025-11-04", STOCK, 5, -1000),
            _t("margin", "2025-12-19", "2025-12-22", STOCK, -5, 750),
            _t("margin", "2026-01-05", "2026-01-06", STOCK, 5, -760)]

    def _gain(self, **extra):
        return dict({"account": "margin", "date": "2025-12-19",
                     "date_settle": "2025-12-22", "symbol": STOCK, "qty": 5,
                     "gain": -250.0, "raw_gain": -250.0,
                     "disallowed_amount": 0.0, "proceeds": 750.0,
                     "cost": 1000.0, "direction": "LONG",
                     "id": "margin-2025-12-19-ZZY.US--5"}, **extra)

    def test_in_kind_contribution(self):
        w = self._edge(self._gain(in_kind="contribution",
                                  denied_contribution=250.0, gain=0.0),
                       self.ROWS)
        self.assertEqual(w["allowed_by"], "in_kind_contribution")
        self.assertIn("not a superficial loss: an in-kind contribution",
                      w["why"])
        self.assertIn("s.40(2)(g)(iv) denies for good", w["why"])
        self.assertNotIn("allowed", w["why"])

    def test_no_reason_recorded_claims_none(self):
        w = self._edge(self._gain(), self.ROWS)
        self.assertEqual(w["allowed_by"], "unknown")
        self.assertIn("no denial applied by the engine (5 units were held "
                      "at the end of day 30, 2026-01-21; the books do not "
                      "record why)", w["why"])
        self.assertNotIn("not still held", w["why"])


if __name__ == "__main__":
    unittest.main()
