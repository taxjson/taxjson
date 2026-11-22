"""`tjs edge-cases` on a US project (backlog TJ-0284, also the tjs
command audit's TJ-0339): the disallowed losses carried in the positions
held at the end of the project year come from the US engine's lots at
the end of Dec 31 (`inventory_year_end`, tax-logic US-RPT-13), not from
the gains file's final inventory, so a later sale or purchase leaves
them alone.

Engine-backed: run_gains computes every row from synthetic trades
(ZZQ.US, ZZR.US; made-up amounts), written to work/ as `tjs run`
writes them; one test drives `tjs run` and `tjs edge-cases` on a
.tt project, as the audit's reproduction did.

Formerly: test_fix_us_year_end_1027
"""
import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.engine.by_country import get_tax_rules
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.edge_cases import render_text
from taxjson.lib.pipeline import GainsRequest, run_gains
import _paths
from _support.us_edge_cases import STOCK, _t, _edge

SRC = _paths.REPO / "src"
TITLE = "DENIED LOSSES CARRIED IN POSITIONS HELD AT THE END OF 2025 (1)"


# A 500.00 loss on 2025-11-17, disallowed: 10 shares bought back on
# 2025-11-20 take it into their basis (§1091(d)). At the end of 2025:
# 10 units, 500.00 deferred.
WASHED = [_t("margin", "2025-11-03", 10, -1000),
          _t("margin", "2025-11-17", -10, 500),
          _t("margin", "2025-11-20", 10, -500)]


def _deferred(edge, sym=STOCK):
    return [(r["account"], r["qty"], r["deferred"], r["as_of"])
            for r in edge["year_boundary"]["deferred_at_year_end"]
            if r["symbol"] == sym]


def _held(rows, sym=STOCK):
    return [(h.get("account"), h["qty"], h["deferred_wash"])
            for h in rows if h["symbol"] == sym]


@rule(R.US_RPT_13)
class TestUSDeferredAtYearEnd(unittest.TestCase):
    """The US engine's lots at the end of Dec 31, whatever comes after."""

    def test_next_year_partial_sale(self):
        doc, edge, text = _edge({"margin": WASHED + [
            _t("margin", "2026-02-10", -4, 320)]})
        # The final inventory is after the sale; the year end is not.
        self.assertEqual(_held(doc["inventory"]), [("margin", 6, 300.0)])
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", 10, 500.0)])
        self.assertEqual(_deferred(edge),
                         [("margin", 10, 500.0, "2025-12-31")])
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertIn("500.00 of denied losses sits in the cost of the 10 "
                      "units still held at the end of 2025; it reduces",
                      r["why"])
        self.assertNotIn("end of the books", r["why"])
        # The text: the same row and reason (--details), and the row in
        # the default view.
        self.assertIn(TITLE, text)
        self.assertIn(r["why"], " ".join(text.split()))
        default = "\n".join(render_text(edge, width_=200))
        self.assertIn(TITLE, default)
        self.assertRegex(default, r"margin\s+ZZQ\.US\s+10\s+500\.00")

    def test_next_year_full_sale_keeps_the_holding(self):
        doc, edge, _ = _edge({"margin": WASHED + [
            _t("margin", "2026-02-10", -10, 800)]})
        self.assertEqual(_held(doc["inventory"]), [])
        self.assertEqual(_deferred(edge),
                         [("margin", 10, 500.0, "2025-12-31")])

    def test_next_year_purchase_is_not_counted(self):
        doc, edge, _ = _edge({"margin": WASHED + [
            _t("margin", "2026-03-02", 7, -700)]})
        self.assertEqual(_held(doc["inventory"]), [("margin", 17, 500.0)])
        self.assertEqual(_deferred(edge),
                         [("margin", 10, 500.0, "2025-12-31")])

    def test_later_rows_change_nothing(self):
        # Appending later trades outside any 2025 window leaves the year
        # end, and every figure of the year, as they were.
        alone_doc, alone, _ = _edge({"margin": WASHED})
        more_doc, more, _ = _edge({"margin": WASHED + [
            _t("margin", "2026-02-02", 4, -40, sym="ZZR.US"),
            _t("margin", "2026-03-02", -1, 100),
            _t("margin", "2026-04-01", 3, -300)]})
        self.assertEqual(alone["year_boundary"]["deferred_at_year_end"],
                         more["year_boundary"]["deferred_at_year_end"])
        self.assertEqual(alone_doc["inventory_year_end"],
                         more_doc["inventory_year_end"])
        for key in ("transactions", "wash_sales"):
            self.assertEqual(alone_doc[key], more_doc[key])

    def test_january_replacement_is_not_held_at_year_end(self):
        # A December loss whose replacement is bought in January: the
        # walk is not cut at Dec 31, so the January purchase still
        # disallows the loss, and its lot takes the basis add, but that
        # lot was not held on Dec 31.
        doc, edge, _ = _edge({"margin": [
            _t("margin", "2025-11-03", 10, -1000),
            _t("margin", "2025-12-15", -10, 500),
            _t("margin", "2026-01-05", 10, -600)]})
        (loss,) = [g for g in doc["transactions"]
                   if g.get("symbol") == STOCK and g.get("qty")]
        self.assertAlmostEqual(loss["disallowed_amount"], 500.0)
        self.assertEqual(_held(doc["inventory"]), [("margin", 10, 500.0)])
        self.assertEqual(_held(doc["inventory_year_end"]), [])
        self.assertEqual(_deferred(edge), [])
        (w,) = [w for w in edge["year_boundary"]["loss_windows"]
                if w["symbol"] == STOCK]
        self.assertEqual(w["denied"], 500.0)

    def test_next_year_loss_on_a_december_purchase(self):
        # A January loss whose replacement was bought in December: on
        # Dec 31 nothing was disallowed yet, so no 2025 position carries
        # it (the final inventory's 250.00 is a 2026 deferral).
        doc, edge, _ = _edge({"margin": [
            _t("margin", "2025-11-03", 10, -1000),
            _t("margin", "2025-12-22", 5, -400),
            _t("margin", "2026-01-05", -10, 500)]})
        self.assertEqual(_held(doc["inventory"]), [("margin", 5, 250.0)])
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", 15, 0.0)])
        self.assertEqual(_deferred(edge), [])

    def test_short_lots(self):
        # A short covered at a loss and re-shorted in the window: the
        # replacement short's proceeds carry the deferral (Reg.
        # §1.1091-1(g)); covered in 2026, it is still open on Dec 31.
        doc, edge, _ = _edge({"margin": [
            _t("margin", "2025-11-03", -10, 1000),
            _t("margin", "2025-11-17", 10, -1500),
            _t("margin", "2025-11-20", -10, 1000),
            _t("margin", "2026-02-10", 10, -900)]})
        self.assertEqual(_held(doc["inventory"]), [])
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", -10, 500.0)])
        self.assertEqual([(a, q, d) for a, q, d, _s in _deferred(edge)],
                         [("margin", -10, 500.0)])

    def test_blended_accounts_keep_their_own_lots(self):
        # margin's loss is washed by margin2's purchase (§1091 across
        # your accounts); margin2 sells in 2026. The split gives margin2
        # its own Dec 31 lot (FIFO is per account, US-BASIS-01).
        doc, edge, _ = _edge({
            "margin": WASHED[:2],
            "margin2": [_t("margin2", "2025-11-20", 10, -500),
                        _t("margin2", "2026-03-02", -10, 900)]})
        self.assertEqual(_held(doc["inventory"]), [])
        self.assertEqual(_deferred(edge),
                         [("margin2", 10, 500.0, "2025-12-31")])

    def test_settle_project_reads_settlement_dates(self):
        # The engine's walk runs on trade dates (US-WASH-01), but a
        # project on tax_date = "settle" reads its year end on
        # settlement dates (backlog TJ-0343; rules.us.rpt.test_settle_year_end_and_allowed_loss_reasons
        # has the sale that settles in January): no note.
        doc, edge, text = _edge({"margin": WASHED}, tax_date="settle")
        self.assertEqual(doc["inventory_year_end_basis"], "settle")
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertEqual(r["as_of"], "2025-12-31")
        self.assertIn("still held at the end of 2025;", r["why"])
        self.assertIn(r["why"], " ".join(text.split()))
        tdoc, trade, _ = _edge({"margin": WASHED})
        self.assertNotIn("inventory_year_end_basis", tdoc)
        self.assertNotIn("(by ", trade["year_boundary"]
                         ["deferred_at_year_end"][0]["why"])

    def test_no_filed_figure_changes(self):
        # The engine with and without a year end: the same rows,
        # disallowances, lots and totals; only the new key differs.
        rules = get_tax_rules("usa")
        rules.emit_replacement_stderr = False
        rows = WASHED + [_t("margin", "2025-12-15", -4, 300),
                         _t("margin", "2026-01-05", 4, -320),
                         _t("margin", "2026-02-10", -6, 480)]
        with contextlib.redirect_stderr(io.StringIO()):
            plain = rules.compute_gains(copy.deepcopy(rows),
                                        per_account_basis=True)
            ye = rules.compute_gains(copy.deepcopy(rows),
                                     per_account_basis=True,
                                     year_end="2025-12-31")
        self.assertNotIn("inventory_year_end", plain)
        self.assertEqual(_held(ye.pop("inventory_year_end")),
                         [("margin", 6, 300.0)])
        self.assertEqual(json.dumps(plain, sort_keys=True, default=str),
                         json.dumps(ye, sort_keys=True, default=str))


class TestUSRetroWash(unittest.TestCase):
    @rule(R.US_RPT_13, R.US_WASH_22)
    def test_basis_add_to_a_sold_replacement_is_in_no_lot(self):
        # margin's 2025-01-10 loss is washed by margin2's purchase bought
        # and sold in December 2024, a filed (locked) year: the basis add
        # is booked as its own 2025 row (place_retro_wash_adjustments),
        # never in a lot held on Dec 31 2025.
        books = {
            "margin": [_t("margin", "2024-11-01", 10, -1000),
                       _t("margin", "2025-01-10", -10, 500),
                       _t("margin", "2025-06-02", 3, -300)],
            "margin2": [_t("margin2", "2024-12-20", 10, -800),
                        _t("margin2", "2024-12-28", -10, 850)]}
        doc, edge, _ = _edge(books, locked_years=(2024,))
        (add,) = [g for g in doc["transactions"]
                  if str(g.get("id", "")).endswith("-wash-basis")]
        self.assertAlmostEqual(add["gain"], -500.0)
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", 3, 0.0)])
        self.assertEqual(_deferred(edge), [])
        self.assertEqual((add["direction"], add["cost"], add["proceeds"]),
                         ("LONG", 500.0, 0.0))

    @rule(R.US_WASH_22)
    def test_a_cut_to_a_covered_shorts_proceeds_is_a_shorts_row(self):
        # The short mirror (backlog TJ-2708, TJ-3441): margin's cover of
        # 2025-01-10 loses 500, washed by margin2's short sold and
        # covered in December 2024, a filed year. The cut is booked as
        # its own 2025 row: a short's row (direction SHORT, the amount a
        # cover's cost), worded for a short sale's proceeds.
        books = {
            "margin": [_t("margin", "2024-11-01", -10, 1000),
                       _t("margin", "2025-01-10", 10, -1500)],
            "margin2": [_t("margin2", "2024-12-20", -10, 850),
                        _t("margin2", "2024-12-28", 10, -800)]}
        doc, _edge_doc, _ = _edge(books, locked_years=(2024,))
        (add,) = [g for g in doc["transactions"]
                  if str(g.get("id", "")).endswith("-wash-basis")]
        self.assertEqual((add["direction"], add["cost"], add["proceeds"],
                          add["gain"], add["term"]),
                         ("SHORT", 0.0, -500.0, -500.0, "SHORT_TERM"))
        self.assertIn("cut to the proceeds of the short sale covered "
                      "2024-12-28", add["deemed_desc"])
        self.assertIn("CUT TO THE PROCEEDS of the replacement short covered",
                      add["note"])

    @rule(R.US_WASH_22)
    def test_a_filed_rows_new_loss_is_tested_in_the_row_booked_instead(self):
        # margin2's December 2024 sale (a gain of 50, filed) takes
        # margin's 500 and would be a loss of 450; margin2 bought 10 more
        # on 2025-01-15, inside that sale's 61 days, so the 450 is a wash
        # sale of its own (backlog TJ-3428). The filed row stays as
        # filed; the row booked in 2025 carries the whole add as its loss
        # and the 450 as its disallowance (an allowed loss of 50), and
        # the 2025 purchase the deferral.
        books = {
            "margin": [_t("margin", "2024-11-01", 10, -1000),
                       _t("margin", "2025-01-10", -10, 500)],
            "margin2": [_t("margin2", "2024-12-20", 10, -800),
                        _t("margin2", "2024-12-28", -10, 850),
                        _t("margin2", "2025-01-15", 10, -900)]}
        doc, _edge_doc, _ = _edge(books, locked_years=(2024,))
        (add,) = [g for g in doc["transactions"]
                  if str(g.get("id", "")).endswith("-wash-basis")]
        self.assertEqual((add["date"], add["raw_gain"],
                          add["disallowed_amount"], add["gain"],
                          add["is_wash_sale"]),
                         ("2025-01-10", -500.0, 450.0, -50.0, True))
        self.assertEqual(
            sorted((w["date"], w["disallowed_amount"])
                   for w in doc["wash_sales"]),
            [("2025-01-10", 450.0), ("2025-01-10", 500.0)])
        # The filed year's own view: the sale as filed, no wash sale.
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            y24 = run_gains(
                copy.deepcopy([t for b in books.values() for t in b]), [],
                req=GainsRequest(country="usa", year=2024, taxable=True,
                                 tax_date="trade", locked_years=(2024,)))
        (filed,) = [g for g in y24["transactions"]
                    if g.get("date") == "2024-12-28"]
        self.assertEqual((filed["gain"], filed["disallowed_amount"],
                          filed["wash_replacements"], filed["is_wash_sale"]),
                         (50.0, 0.0, None, False))
        self.assertEqual(y24["wash_sales"], [])
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin2", 10, 450.0)])

    @rule(R.US_WASH_22)
    def test_a_chains_second_add_does_not_land_on_a_filed_row(self):
        # margin's 2025 loss of 500 goes to margin2's filed sale (a gain
        # of 50), whose new loss of 450 is replaced by margin3's
        # purchase, sold in the filed year too (a gain of 100): that add
        # belongs to the 2025 loss's chain and is booked in 2025 like
        # the first (backlog TJ-3461: it stayed on margin3's filed row,
        # 2024's total 150 turned -300). 2024 as filed; 2025: the loss
        # disallowed, 500 booked (450 of it disallowed) and 450 booked.
        books = {
            "margin": [_t("margin", "2024-11-01", 10, -1000),
                       _t("margin", "2025-01-10", -10, 500)],
            "margin2": [_t("margin2", "2024-12-20", 10, -800),
                        _t("margin2", "2024-12-28", -10, 850)],
            "margin3": [_t("margin3", "2024-12-22", 10, -800),
                        _t("margin3", "2024-12-30", -10, 900)]}
        rows = [t for b in books.values() for t in b]

        def run(year, lock=None, said=None):
            # `lock`: {account: the cost its 2024 sale was filed at}.
            err = io.StringIO()
            with tempfile.TemporaryDirectory() as tmp, \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                recs = ()
                if lock:
                    path = Path(tmp) / "2024.json"
                    path.write_text(json.dumps({
                        "year": 2024, "dispositions": [
                            {"account": a, "symbol": STOCK, "qty": 10,
                             "date": d, "date_settle": d, "cost": lock[a]}
                            for a, d in (("margin2", "2024-12-28"),
                                         ("margin3", "2024-12-30"))]}))
                    recs = (str(path),)
                doc = run_gains(copy.deepcopy(rows), [], req=GainsRequest(
                    country="usa", year=year, taxable=True,
                    tax_date="trade", locked_years=(2024,),
                    locked_records=recs))
            if said is not None:
                said.append(err.getvalue())
            return doc
        self.assertEqual(
            sorted((g["date"], g["gain"], g["disallowed_amount"])
                   for g in run(2024)["transactions"]),
            [("2024-12-28", 50.0, 0.0), ("2024-12-30", 100.0, 0.0)])
        self.assertEqual(
            sorted((g["account"], g["raw_gain"], g["disallowed_amount"],
                    g["gain"], bool(g.get("deemed")))
                   for g in run(2025)["transactions"]),
            [("margin", -500.0, 500.0, 0.0, False),
             ("margin2", -500.0, 450.0, -50.0, True),
             ("margin3", -450.0, 0.0, -450.0, True)])
        # The second line names the link (backlog TJ-3467).
        said = []
        run(2025, said=said)
        self.assertIn("is a wash sale whose replacement, through the "
                      "2024-12-28 sale in margin2 that it turned into a "
                      "loss, was bought and sold 2024-12-30 in margin3",
                      said[0])

        def rows_of(doc):
            return sorted((g["account"], g["cost"], g["disallowed_amount"],
                           g["gain"], bool(g.get("deemed")))
                          for g in doc["transactions"])
        # A 2024 closed by a version before the re-test, with January's
        # rows in its inputs: margin2 filed at 1300 (the 500 in it), its
        # loss of 450 allowed; margin3 at 800. Both stay exactly so: no
        # disallowance on margin2's row, no add on margin3's, nothing
        # booked in 2025 for either, and the run names the loss the test
        # would now disallow (backlog TJ-3468: margin2 read 0 with 450
        # disallowed, and 2025 booked -450 for margin3, counted twice).
        old = {"margin2": 1300.0, "margin3": 800.0}
        said = []
        self.assertEqual(rows_of(run(2024, old, said)),
                         [("margin2", 1300.0, 0.0, -450.0, False),
                          ("margin3", 800.0, 0.0, 100.0, False)])
        self.assertEqual(rows_of(run(2025, old, said)),
                         [("margin", 1000.0, 500.0, 0.0, False)])
        for text in said:
            self.assertEqual(text.count(
                "the 2024-12-28 sale in margin2, in filed year 2024, was "
                "filed with the 2025-01-10 loss (margin) in its basis and "
                "its own loss allowed. 450.00 of that loss would now be "
                "disallowed as a wash sale of that sale's own (replaced by "
                "10 bought 2024-12-22 in margin3, sold 2024-12-30). The "
                "filed year is left as filed and nothing is booked for it. "
                "Your 2024 return may need an amendment (Form 1040-X) for "
                "that sale; after amending, close 2024 again with this "
                "version: in that year's project move filed/2024.json "
                "out of the filed folder (keep it: the record of what you "
                "first filed), then `tjs run` and `tjs close-year`"), 1)
            self.assertNotIn("tested for a wash sale of their own", text)
        # A 2024 closed by this version (margin2's loss disallowed,
        # margin3 at 1250 with the chain's add): as filed too, the
        # re-test and its add where the lock has them.
        new = {"margin2": 1300.0, "margin3": 1250.0}
        self.assertEqual(rows_of(run(2024, new)),
                         [("margin2", 1300.0, 450.0, 0.0, False),
                          ("margin3", 1250.0, 0.0, -350.0, False)])
        self.assertEqual(rows_of(run(2025, new)),
                         [("margin", 1000.0, 500.0, 0.0, False)])

    @rule(R.US_WASH_22)
    def test_a_filed_years_own_chain_is_named_and_a_damaged_row_is_not_read(
            self):
        # The chain wholly inside 2024, closed by a version before the
        # re-test: qa's loss of 500 (12-20) is in qc's sale of 11-25
        # (cost 1300, its loss of 400 allowed) and qb's sale of 12-10 is
        # a gain of 50. Left as filed, and said on the 2024 run and on
        # the 2025 run alike (backlog TJ-3475: it was left in silence),
        # the replacement said as sold (TJ-3473).
        rows = [_t("qa", "2024-10-01", 10, -1000),
                _t("qa", "2024-12-20", -10, 500),
                _t("qb", "2024-12-01", 10, -800),
                _t("qb", "2024-12-10", -10, 850),
                _t("qc", "2024-11-20", 10, -800),
                _t("qc", "2024-11-25", -10, 900)]

        def run(year, disp):
            err = io.StringIO()
            with tempfile.TemporaryDirectory() as tmp, \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                path = Path(tmp) / "2024.json"
                path.write_text(json.dumps({"year": 2024,
                                            "dispositions": disp}))
                doc = run_gains(copy.deepcopy(rows), [], req=GainsRequest(
                    country="usa", year=year, taxable=True,
                    tax_date="trade", locked_years=(2024,),
                    locked_records=(str(path),)))
            return doc, err.getvalue()

        def lock(**qc):
            return [dict({"account": "qc", "symbol": STOCK, "qty": 10,
                          "date": "2024-11-25", "date_settle": "2024-11-25",
                          "cost": 1300.0, "gain": -400.0, "denied": 0.0},
                         **qc),
                    {"account": "qb", "symbol": STOCK, "qty": 10,
                     "date": "2024-12-10", "date_settle": "2024-12-10",
                     "cost": 800.0, "gain": 50.0, "denied": 0.0}]
        line = ("the 2024-11-25 sale in qc, in filed year 2024, was filed "
                "with the 2024-12-20 loss (qa) in its basis and its own "
                "loss allowed. 400.00 of that loss would now be disallowed "
                "as a wash sale of that sale's own (replaced by 10 bought "
                "2024-12-01 in qb, sold 2024-12-10).")
        doc, said = run(2024, lock())
        self.assertEqual(
            sorted((g["account"], g["gain"], g["disallowed_amount"])
                   for g in doc["transactions"]),
            [("qa", 0.0, 500.0), ("qb", 50.0, 0.0), ("qc", -400.0, 0.0)])
        self.assertEqual(said.count(line), 1)
        self.assertEqual(run(2025, lock())[1].count(line), 1)
        # A hand-edited lock (backlog TJ-3478): `denied` null is read as
        # not stated (the other rows decide: left as filed here); a row
        # whose units are no number is named and not read (the row is
        # then tested as in a year not closed with the add). Never a
        # TypeError.
        doc, said = run(2024, lock(denied=None))
        self.assertEqual(said.count(line), 1)
        doc, said = run(2024, lock(qty="ten"))
        self.assertEqual(said.count(
            "filed 2024: the lock's row for ZZQ.US in qc on 2024-11-25 has "
            "units or a cost that is not a number: not read"), 1)
        self.assertNotIn(line, said)


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


@rule(R.US_RPT_13)
class TestUSYearEndCLI(unittest.TestCase):
    """The audit's reproduction (TJ-0339): `tjs run`, then
    `tjs edge-cases --json`, before and after a 2026 sale is added."""

    TT = ("BUYSELL 2025-11-03 10:00:00 ZZQ.US 10 USD 10 100 0\n"
          "BUYSELL 2025-11-17 10:00:00 ZZQ.US -10 USD 5 50 0\n"
          "BUYSELL 2025-11-20 10:00:00 ZZQ.US 10 USD 5 50 0\n")

    def test_a_next_year_sale_leaves_the_year_end(self):
        with tempfile.TemporaryDirectory(prefix="tj0284-") as td:
            root = Path(td)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = []\n\n'
                '[accounts.margin]\ntype = "taxable"\n')
            seen = []
            for later in ("", "BUYSELL 2026-02-10 10:00:00 ZZQ.US -10 USD "
                              "8 80 0\n"):
                (root / "inputs" / "margin" / "m.tt").write_text(
                    self.TT + later)
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr)
                gains = json.loads((root / "work" / "margin_gains.json")
                                   .read_text(encoding="utf-8"))
                self.assertIn("inventory_year_end", gains)
                r = _cli(root, "edge-cases", "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                rows = json.loads(r.stdout)["year_boundary"][
                    "deferred_at_year_end"]
                seen.append(rows)
                r = _cli(root, "edge-cases", "--details")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(TITLE, r.stdout)
                self.assertIn("50.00 of denied losses sits in the cost of "
                              "the 10 units still held at the end of 2025",
                              " ".join(r.stdout.split()))
            (first, after) = seen
            self.assertEqual(first, after)
            self.assertEqual([(x["account"], x["symbol"], x["qty"],
                               x["deferred"], x["as_of"]) for x in first],
                             [("margin", "ZZQ.US", 10.0, 50.0,
                               "2025-12-31")])


if __name__ == "__main__":
    unittest.main()
