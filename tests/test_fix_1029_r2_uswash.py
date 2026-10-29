"""Fix round 2 of 2026-10-29, group uswash: the US engine's wash sales,
stock dividends and the reports built on them.

TJ-0490 a partial move between two of your own taxable accounts (or a
        listing journal) split a replacement lot, but the replacement
        record stayed on the sender's half with the whole capacity: a
        later loss put its whole basis add and holding period there,
        and the moved half kept its plain basis and short term.
TJ-0492 a stock dividend grew the replacement lots but not the
        purchase's wash-sale capacity: a loss of the post-dividend
        shares matched only the pre-dividend count of replacements.
TJ-0498 entitled shares sold after the ex-dividend date, before the
        pay date, kept their whole basis and the held lots took the
        whole dividend.
TJ-0506 a broad-based index option's loss was washed, though it is a
        §1256 contract Form 6781's list names: the engine and the
        planning tools read §1091's exclusion from one classifier.
TJ-0463 a basis add the filed year's lock already has (a year closed
        with the next January's rows) was booked again in the loss
        year, and the filed year drifted from its own lock.
TJ-0465 Form 8949, the TXF and reconcile-slips put a re-short's carried
        wash-sale loss out of (d) proceeds instead of into (e) cost.
TJ-0528 the .tt writers dropped a stock dividend's mark: read back, its
        shares were a purchase at $0 and a US sale's gain moved.
TJ-0467 the call-as-replacement flag put the whole loss at risk when
        share purchases had already replaced most of it.
TJ-0471 edge-cases named the lot the loss sold as its day-30
        acquisition.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx


def _gains(book, sheltered=(), year=2025, **req):
    """The US pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country="usa", taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res, **match):
    return [t for t in res["transactions"]
            if t.get("qty") and all(t.get(k) == v for k, v in match.items())]


def _inv(res):
    return {(i["account"], i["symbol"]): (i["qty"], round(i["total_cost"], 2),
                                          i["holding_start_date"])
            for i in res["inventory"]}


# ---------------------------------------------------------------- TJ-0490
def _move_legs(date, sym, qty, src, dst):
    from taxjson.lib.core import LOT_MOVE_TYPE
    desc = f"own-account move #1: {src} -> {dst} (test)"
    return [tx("TRANSFER", date, sym, -qty, 0.0, account=src,
               type=LOT_MOVE_TYPE, description=desc),
            tx("TRANSFER", date, sym, qty, 0.0, account=dst,
               type=LOT_MOVE_TYPE, description=desc)]


def _journal_legs(date, src_sym, dst_sym, qty, account):
    from taxjson.lib.core import LISTING_JOURNAL_TYPE
    return [tx("TRANSFER", date, src_sym, -qty, 0.0, account=account,
               type=LISTING_JOURNAL_TYPE, journal_pair="j1"),
            tx("TRANSFER", date, dst_sym, qty, 0.0, account=account,
               type=LISTING_JOURNAL_TYPE, journal_pair="j1")]


class TestPartialMoveSplitsTheReplacement(unittest.TestCase):
    """TJ-0490: qc's old 100 shares (cost 1,000) are sold at a 400
    loss; qa bought 100 replacement shares (cost 500) ten days before
    and moved 50 of them to qb. The disallowed 400 is 4.00 a share,
    200 on each half, and each half takes the loss shares' holding
    period: qb's later sale of its 50 is cost 450, long-term, and qa
    keeps 50 at 450 — as when qa and qb had bought 50 each."""

    def _book(self, moved_rows):
        return ([tx("BUYSELL", "2024-01-01", "ZZQ.US", 100, 1000.0,
                    account="qc"),
                 tx("BUYSELL", "2025-01-10", "ZZQ.US", 100, 500.0,
                    account="qa")]
                + moved_rows
                + [tx("BUYSELL", "2025-01-20", "ZZQ.US", -100, 600.0,
                      account="qc"),
                   tx("BUYSELL", "2025-03-01", "ZZQ.US", -50, 500.0,
                      account="qb")])

    def _check(self, res):
        loss = _sales(res, account="qc")[0]
        self.assertAlmostEqual(loss["disallowed_amount"], 400.0, places=6)
        sale = _sales(res, account="qb")[0]
        self.assertAlmostEqual(sale["cost"], 450.0, places=6)
        self.assertAlmostEqual(sale["gain"], 50.0, places=6)
        self.assertEqual(sale["term"], "LONG_TERM")
        qty, cost, _held = _inv(res)[("qa", "ZZQ.US")]
        self.assertAlmostEqual(qty, 50.0)
        self.assertAlmostEqual(cost, 450.0, places=2)

    @rule("US-BASIS-05", "US-WASH-09", "US-WASH-10")
    def test_moved_half_takes_its_share_of_the_basis_and_term(self):
        res = _gains(self._book(
            _move_legs("2025-01-15", "ZZQ.US", 50, "qa", "qb")))
        self._check(res)

    @rule("US-BASIS-05", "US-WASH-09", "US-WASH-10")
    def test_two_buys_of_fifty_are_the_control(self):
        res = _gains([
            tx("BUYSELL", "2024-01-01", "ZZQ.US", 100, 1000.0, account="qc"),
            tx("BUYSELL", "2025-01-10", "ZZQ.US", 50, 250.0, account="qa"),
            tx("BUYSELL", "2025-01-10", "ZZQ.US", 50, 250.0, account="qb"),
            tx("BUYSELL", "2025-01-20", "ZZQ.US", -100, 600.0,
               account="qc"),
            tx("BUYSELL", "2025-03-01", "ZZQ.US", -50, 500.0,
               account="qb")])
        self._check(res)

    @rule("US-BASIS-05", "US-WASH-21")
    def test_a_sale_of_the_moved_half_uses_up_only_its_capacity(self):
        # qb sells its moved 50 before the loss: the purchase replaces
        # the loss with the 50 qa still holds (and, US-WASH-22, the 50
        # sold in another account), never 100 shares onto qa's 50.
        res = _gains(
            [tx("BUYSELL", "2024-01-01", "ZZQ.US", 100, 1000.0,
                account="qc"),
             tx("BUYSELL", "2025-01-10", "ZZQ.US", 100, 500.0,
                account="qa")]
            + _move_legs("2025-01-12", "ZZQ.US", 50, "qa", "qb")
            + [tx("BUYSELL", "2025-01-14", "ZZQ.US", -50, 300.0,
                  account="qb"),
               tx("BUYSELL", "2025-01-20", "ZZQ.US", -100, 600.0,
                  account="qc")])
        qty, cost, _held = _inv(res)[("qa", "ZZQ.US")]
        self.assertAlmostEqual(qty, 50.0)
        # qa's 50 take 200 of the 400 (4.00 a share), never all 400.
        self.assertAlmostEqual(cost, 450.0, places=2)

    @rule("US-XLIST-01", "US-WASH-09")
    def test_a_listing_journal_splits_the_replacement_too(self):
        res = _gains(self._book(
            _journal_legs("2025-01-15", "ZZQ.US", "ZZQX.US", 50, "qa"))[:-1]
            + [tx("BUYSELL", "2025-03-01", "ZZQX.US", -50, 500.0,
                  account="qa")])
        sale = _sales(res, symbol="ZZQX.US")[0]
        self.assertAlmostEqual(sale["cost"], 450.0, places=6)
        self.assertEqual(sale["term"], "LONG_TERM")
        inv = {k: v for k, v in _inv(res).items() if k[1] == "ZZQ.US"}
        qty, cost, _held = next(iter(inv.values()))
        self.assertAlmostEqual(qty, 50.0)
        self.assertAlmostEqual(cost, 450.0, places=2)


# ---------------------------------------------------------------- TJ-0492
def _stkdiv(account, date, qty, ex_date):
    return tx("BUYSELL", date, "ZZQ.US", qty, 0.0, account=account,
              type="stock_dividend", ex_date=ex_date)


class TestStockDividendGrowsReplacementCapacity(unittest.TestCase):
    """TJ-0492: qc's old 100 (cost 1,000) and qa's replacement 100
    (cost 500, ten days before the loss) each get a 100-share stock
    dividend; qc then sells its 200 for 600. All 200 of qa's shares
    stand for the recent purchase: the whole 400 loss is disallowed
    and qa's basis is 900, as with a 2-for-1 split."""

    def _book(self, event_rows, rep_account="qa"):
        return ([tx("BUYSELL", "2024-01-01", "ZZQ.US", 100, 1000.0,
                    account="qc"),
                 tx("BUYSELL", "2025-01-10", "ZZQ.US", 100, 500.0,
                    account=rep_account)]
                + event_rows
                + [tx("BUYSELL", "2025-01-20", "ZZQ.US", -200, 600.0,
                      account="qc")])

    @rule("US-STKDIV-01", "US-WASH-02")
    def test_the_loss_matches_every_replacement_share(self):
        res = _gains(self._book([
            _stkdiv("qa", "2025-01-15", 100, "2025-01-14"),
            _stkdiv("qc", "2025-01-15", 100, "2025-01-14")]))
        loss = _sales(res, account="qc")[0]
        self.assertAlmostEqual(loss["disallowed_amount"], 400.0, places=6)
        self.assertAlmostEqual(loss["gain"], 0.0, places=6)
        qty, cost, _held = _inv(res)[("qa", "ZZQ.US")]
        self.assertAlmostEqual(qty, 200.0)
        self.assertAlmostEqual(cost, 900.0, places=2)

    @rule("US-STKDIV-01", "US-WASH-02")
    def test_a_split_is_the_control(self):
        res = _gains(self._book([tx("SPLIT", "2025-01-15", "ZZQ.US", 2)]))
        loss = _sales(res, account="qc")[0]
        self.assertAlmostEqual(loss["disallowed_amount"], 400.0, places=6)
        self.assertAlmostEqual(_inv(res)[("qa", "ZZQ.US")][1], 900.0,
                               places=2)

    @rule("US-STKDIV-01")
    def test_a_purchase_after_the_ex_date_does_not_grow(self):
        # qa's purchase on 2025-01-14 (the ex date) is not entitled: it
        # keeps 100 shares and replaces 100 of the 200 loss shares.
        res = _gains(
            [tx("BUYSELL", "2024-01-01", "ZZQ.US", 100, 1000.0,
                account="qc"),
             tx("BUYSELL", "2025-01-14", "ZZQ.US", 100, 500.0,
                account="qa"),
             _stkdiv("qc", "2025-01-15", 100, "2025-01-14"),
             tx("BUYSELL", "2025-01-20", "ZZQ.US", -200, 600.0,
                account="qc")])
        loss = _sales(res, account="qc")[0]
        self.assertAlmostEqual(loss["disallowed_amount"], 200.0, places=6)

    @rule("US-STKDIV-01", "US-WASH-04", "US-WASH-11")
    def test_an_ira_replacement_grows_with_its_dividend(self):
        ira = [tx("BUYSELL", "2025-01-10", "ZZQ.US", 100, 500.0,
                  account="ira"),
               _stkdiv("ira", "2025-01-15", 100, "2025-01-14")]
        book = [tx("BUYSELL", "2024-01-01", "ZZQ.US", 100, 1000.0,
                   account="qc"),
                _stkdiv("qc", "2025-01-15", 100, "2025-01-14"),
                tx("BUYSELL", "2025-01-20", "ZZQ.US", -200, 600.0,
                   account="qc")]
        res = _gains(book, sheltered=ira)
        loss = _sales(res, account="qc")[0]
        self.assertAlmostEqual(loss["disallowed_amount"], 400.0, places=6)
        self.assertAlmostEqual(loss["permanently_disallowed"], 400.0,
                               places=6)


# ---------------------------------------------------------------- TJ-0498
class TestEntitledSharesSoldBeforeThePayDate(unittest.TestCase):
    """TJ-0498: 200 bought for 1,000; a 20-share stock dividend (ex
    2025-06-02, paid 2025-06-16); 100 sold on 2025-06-10 for 600. The
    220 old and new shares share the 1,000: the sale's cost is
    454.55 (gain 145.45) and the 120 kept cost 545.45."""

    def _book(self, sale_date="2025-06-10", sold=100, proceeds=600.0,
              ex="2025-06-02", pay="2025-06-16"):
        return [tx("BUYSELL", "2024-01-01", "ZZQ.US", 200, 1000.0),
                tx("BUYSELL", sale_date, "ZZQ.US", -sold, proceeds),
                tx("BUYSELL", pay, "ZZQ.US", 20, 0.0,
                   type="stock_dividend", ex_date=ex)]

    @rule("US-STKDIV-05")
    def test_the_sale_gives_its_part_of_the_basis_to_the_new_shares(self):
        res = _gains(self._book())
        sale = _sales(res)[0]
        self.assertAlmostEqual(sale["cost"], 1000.0 / 220 * 100, places=4)
        self.assertAlmostEqual(sale["gain"], 600.0 - 1000.0 / 220 * 100,
                               places=4)
        self.assertEqual(sale["term"], "LONG_TERM")
        qty, cost, held = _inv(res)[("margin", "ZZQ.US")]
        self.assertAlmostEqual(qty, 120.0)
        self.assertAlmostEqual(cost, 545.45, places=2)
        self.assertEqual(held, "2024-01-01")
        self.assertIn("100 of them were sold on or after 2025-06-02, "
                      "before the pay date (2025-06-10): those sales' "
                      "cost falls by 45.45", res["_stderr"])

    @rule("US-STKDIV-05")
    def test_sold_out_before_the_pay_date_with_an_ex_date(self):
        res = _gains(self._book(sold=200, proceeds=1200.0))
        sale = _sales(res)[0]
        self.assertAlmostEqual(sale["cost"], 1000.0 / 1.1, places=4)
        qty, cost, held = _inv(res)[("margin", "ZZQ.US")]
        self.assertAlmostEqual(qty, 20.0)
        self.assertAlmostEqual(cost, 90.91, places=2)
        self.assertEqual(held, "2024-01-01")
        self.assertNotIn("$0 purchase", res["_stderr"])

    @rule("US-STKDIV-01")
    def test_a_sale_before_the_ex_date_is_not_entitled(self):
        res = _gains(self._book(sale_date="2025-06-01"))
        self.assertAlmostEqual(_sales(res)[0]["cost"], 500.0, places=6)
        qty, cost, _held = _inv(res)[("margin", "ZZQ.US")]
        self.assertAlmostEqual(qty, 120.0)
        self.assertAlmostEqual(cost, 500.0, places=2)

    @rule("US-STKDIV-05")
    def test_a_sale_in_the_year_before_is_left_for_a_manual_move(self):
        res = _gains(self._book(sale_date="2025-12-30", ex="2025-12-29",
                                pay="2026-01-05"), year=2026)
        self.assertIn("100 of the shares entitled to it were sold on "
                      "2025-12-30 (margin), in 2025, so their 10 new "
                      "share(s) are booked at $0: move 45.45 of that "
                      "sale's cost to them by hand", res["_stderr"])
        qty, cost, _held = _inv(res)[("margin", "ZZQ.US")]
        self.assertAlmostEqual(qty, 120.0)
        self.assertAlmostEqual(cost, 500.0, places=2)


# ---------------------------------------------------------------- TJ-0506
class TestIndexOptionOutsideTheWashSaleRule(unittest.TestCase):
    """TJ-0506: a broad-based index option (SPX, the built-in §1256
    root list) is a §1256 contract: its loss is never washed, as a
    future's is not (US-WASH-18). Bought for 100, sold for 50, the same
    contract bought again two days later: the -50 stays a loss, the
    new contract costs 100, and Form 6781's list has the -50."""

    _SYM = "SPX260116C00100000"

    def _book(self, sym):
        return [tx("BUYSELL", "2025-01-06", sym, 1, 100.0),
                tx("BUYSELL", "2025-03-03", sym, -1, 50.0),
                tx("BUYSELL", "2025-03-05", sym, 1, 100.0)]

    @rule("US-WASH-18", "US-OPT-04")
    def test_the_loss_stays_and_form_6781_lists_it(self):
        from taxjson.bin.taxjson_form_export import build_8949
        res = _gains(self._book(self._SYM))
        sale = _sales(res)[0]
        self.assertAlmostEqual(sale["disallowed_amount"], 0.0)
        self.assertAlmostEqual(sale["gain"], -50.0, places=6)
        self.assertAlmostEqual(_inv(res)[("margin", self._SYM)][1], 100.0)
        rules = [w["rule"] for w in res["option_replacement_warnings"]]
        self.assertEqual(rules, ["futures_vs_loss"])
        self.assertIn("a broad-based index option) is marked to market, "
                      "outside §1091: the loss is NOT denied",
                      res["_stderr"])
        form = build_8949(res["transactions"], year=2025)
        self.assertEqual([round(r["gain"], 2) for r in form["section_1256"]],
                         [-50.0])

    @rule("US-WASH-01")
    def test_an_equity_option_is_still_washed(self):
        res = _gains(self._book("ZZQ260116C00100000"))
        self.assertAlmostEqual(_sales(res)[0]["disallowed_amount"], 50.0,
                               places=6)

    @rule("US-WASH-18")
    def test_the_radar_says_outside_for_an_index_option(self):
        from taxjson.bin.taxjson_wash_radar import _outside_1091
        self.assertTrue(_outside_1091(self._SYM))
        self.assertTrue(_outside_1091("F:CLG7.US"))
        self.assertFalse(_outside_1091("ZZQ260116C00100000"))
        self.assertFalse(_outside_1091("ZZQ.US"))


# ---------------------------------------------------------------- TJ-0463
def _filed_book():
    """qa: 100 bought 2024-03-01 for 5,000, sold 2025-01-10 for 4,000 (a
    1,000 loss); qb: 40 bought 2024-12-16 for 1,800 and sold 2024-12-27
    for 2,400. qb's purchase replaces 40 of qa's loss shares
    (US-WASH-22): 400 goes into qb's 2024 sale's basis."""
    return [tx("BUYSELL", "2024-03-01", "ZZQ.US", 100, 5000.0,
               account="qa"),
            tx("BUYSELL", "2024-12-16", "ZZQ.US", 40, 1800.0,
               account="qb"),
            tx("BUYSELL", "2024-12-27", "ZZQ.US", -40, 2400.0,
               account="qb"),
            tx("BUYSELL", "2025-01-10", "ZZQ.US", -100, 4000.0,
               account="qa")]


class TestFiledYearWithTheBasisAdd(unittest.TestCase):
    """TJ-0463: 2024 closed with the January 2025 rows in its inputs (as
    the docs ask): its lock's row for qb's sale has the 400 add (cost
    2,200). Once the lock exists, a run kept the add out of that sale
    and booked it again as a 2025 loss: deducted twice, the 2024 return
    called DRIFTED from its own lock, and an amendment advised. A lock
    without the add (a year filed before the loss was known) still
    gets the 2025 row and the ATTENTION line."""

    def _lock(self, td, cost):
        import json
        from pathlib import Path
        p = Path(td) / "2024.json"
        p.write_text(json.dumps({"year": 2024, "dispositions": [
            {"account": "qb", "symbol": "ZZQ.US", "date": "2024-12-27",
             "date_settle": "2024-12-27", "qty": 40.0, "proceeds": 2400.0,
             "cost": cost, "gain": round(2400.0 - cost, 2),
             "denied": 0.0}]}))
        return str(p)

    def _run(self, cost, year=None):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            return _gains(_filed_book(), year=year, locked_years=(2024,),
                          locked_records=(self._lock(td, cost),))

    @rule("US-WASH-22")
    def test_a_lock_with_the_add_keeps_it_in_the_filed_sale(self):
        res = self._run(2200.0)
        sale = _sales(res, account="qb")[0]
        self.assertAlmostEqual(sale["cost"], 2200.0, places=2)
        self.assertEqual([t for t in res["transactions"]
                          if t.get("deemed")], [])
        self.assertNotIn("reaches a filed year", res["_stderr"])
        y24 = self._run(2200.0, year=2024)
        y25 = self._run(2200.0, year=2025)
        self.assertAlmostEqual(y24["summary"]["total_gain"], 200.0,
                               places=2)
        # 2025: qa's loss after the wash only (-1,000 + 400).
        self.assertAlmostEqual(y25["summary"]["total_gain"], -600.0,
                               places=2)

    @rule("US-WASH-22")
    def test_a_lock_without_the_add_books_it_in_the_loss_year(self):
        res = self._run(1800.0)
        sale = _sales(res, account="qb")[0]
        self.assertAlmostEqual(sale["cost"], 1800.0, places=2)
        moved = [t for t in res["transactions"] if t.get("deemed")]
        self.assertEqual([(t["date"], round(t["gain"], 2)) for t in moved],
                         [("2025-01-10", -400.0)])
        self.assertIn("ATTENTION: wash sale reaches a filed year",
                      res["_stderr"])

    @rule("US-WASH-22")
    def test_a_lock_with_no_rows_moves_it_as_before(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "2024.json"
            p.write_text(json.dumps({"year": 2024}))
            res = _gains(_filed_book(), locked_years=(2024,),
                         locked_records=(str(p),))
        self.assertEqual(len([t for t in res["transactions"]
                              if t.get("deemed")]), 1)


# ---------------------------------------------------------------- TJ-0465
class TestReShortCarriedLossIsBasis(unittest.TestCase):
    """TJ-0465: a short of 20 ZZS covered at a 200 loss, then a re-short
    in the window (2,100 in) covered for 1,900. The engine carries the
    washed 200 off the re-short's opening proceeds; Form 8949, the TXF
    and reconcile-slips showed (d) 1,900 against the 1099-B's 2,100.
    The 1099-B reports what the short sale brought in, and the §1091(d)
    add is basis: (d) 2,100, (e) 2,100, (h) 0 — the gain unchanged."""

    def _rows(self):
        book = [tx("BUYSELL", "2025-08-04", "ZZS.US", -20, 2000.0),
                tx("BUYSELL", "2025-08-20", "ZZS.US", 20, 2200.0),
                tx("BUYSELL", "2025-09-05", "ZZS.US", -20, 2100.0),
                tx("BUYSELL", "2025-10-01", "ZZS.US", 20, 1900.0)]
        res = _gains(book)
        return res, [t for t in res["transactions"] if t.get("qty")]

    @rule("US-RPT-01")
    def test_8949_and_txf_carry_it_in_the_cost(self):
        from taxjson.bin.taxjson_form_export import build_8949, build_txf
        _res, rows = self._rows()
        rep = build_8949(rows, 2025)
        cells = [(r["date_sold"], r["proceeds"], r["cost"], r["adjustment"],
                  r["gain"]) for r in rep["part_I"]]
        self.assertEqual(cells, [("2025-08-20", 2000.0, 2200.0, 200.0, 0.0),
                                 ("2025-10-01", 2100.0, 2100.0, 0.0, 0.0)])
        self.assertEqual(rep["part_I_totals"]["proceeds"], 4100.0)
        txf = build_txf(rep, "A")
        self.assertIn("D10/01/2025\n$2100.00\n$2100.00", txf)

    @rule("US-RPT-01")
    def test_reconcile_slips_reads_the_1099b_proceeds(self):
        import json
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_reconcile_slips import load_computed
        res, _rows = self._rows()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_gains_wash.json"
            p.write_text(json.dumps({"transactions": res["transactions"]}))
            comp = load_computed([p], 2025, "trade")
        self.assertAlmostEqual(comp["ZZS"]["proceeds_net"], 4100.0, places=2)
        self.assertAlmostEqual(comp["ZZS"]["cost"], 4300.0, places=2)

    def test_a_canadian_row_is_left_as_it_is(self):
        from taxjson.bin.taxjson_form_export import short_carried_loss
        row = {"direction": "SHORT", "deferred_loss_recovered": 200.0}
        self.assertEqual(short_carried_loss(row), 0.0)
        self.assertEqual(short_carried_loss(dict(row, term="SHORT_TERM")),
                         200.0)
        self.assertEqual(short_carried_loss(
            {"direction": "LONG", "term": "SHORT_TERM",
             "deferred_loss_recovered": 200.0}), 0.0)


# ---------------------------------------------------------------- TJ-0528
class TestStockDividendRoundTripsThroughTt(unittest.TestCase):
    """TJ-0528: 100 ZZQ bought for 1,000, a 10-share stock dividend
    (record date 2025-02-28), 100 sold for 1,500. The .tt writers
    dropped the stock-dividend mark: read back, the 10 shares were a
    purchase at $0 and the US sale's gain was 500 instead of 1,500 -
    1,000 x 100/110 = 590.91."""

    def _rows(self):
        from taxjson.lib.core import TaxTransaction

        def t(day, qty, price, net, **kw):
            return TaxTransaction(action="BUYSELL", date=day,
                                  date_settle=day, time="10:00:00",
                                  symbol="ZZQ.US", quantity=qty,
                                  currency="USD", price=price,
                                  net_amount=net, account="margin", **kw)
        return [t("2023-01-03", 100, 10, 1000),
                t("2025-03-03", 10, 0, 0, type="stock_dividend",
                  record_date="2025-02-28"),
                t("2025-04-03", -100, 15, 1500)]

    def _reread(self, lines):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        from taxjson.lib.core import TaxTransaction
        return [TaxTransaction(**parse_tt_line(x, account_name="margin"))
                for x in lines]

    @rule("US-STKDIV-01")
    def test_both_writers_keep_it(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line
        from taxjson.bin.taxjson_run import _tx_display_line
        rows = self._rows()
        want = round(_gains(rows)["summary"]["total_gain"], 4)
        self.assertAlmostEqual(want, 590.9091, places=4)
        for lines in ([tx_to_tt_line(x.to_dict(), date_basis="trade")
                       for x in rows],
                      [_tx_display_line(x.to_dict(), settle=False)
                       for x in rows]):
            self.assertTrue(lines[1].endswith(
                " type=stock_dividend record=2025-02-28"), lines[1])
            back = self._reread(lines)
            self.assertEqual(back[1].type, "stock_dividend")
            self.assertEqual(back[1].record_date, "2025-02-28")
            self.assertAlmostEqual(_gains(back)["summary"]["total_gain"],
                                   want, places=4)

    def test_the_tokens_are_checked(self):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        ok = parse_tt_line("BUYSELL 2025-03-03 10:00:00 ZZQ.US 10 USD 0 0 "
                           "ex=2025-02-27 type=stock_dividend",
                           account_name="margin")
        self.assertEqual((ok["type"], ok["ex_date"]),
                         ("stock_dividend", "2025-02-27"))
        for bad, why in (
                ("BUYSELL 2025-03-03 10:00:00 ZZQ.US -10 USD 0 0 "
                 "type=stock_dividend", "QTY is positive"),
                ("ASSIGN 2025-03-03 10:00:00 ZZQ.US 10 USD 0 0 "
                 "type=stock_dividend", "only a BUYSELL line"),
                ("BUYSELL 2025-03-03 10:00:00 ZZQ.US 10 USD 0 0 "
                 "type=split", "only type"),
                ("BUYSELL 2025-03-03 10:00:00 ZZQ.US 10 USD 0 0 "
                 "type=stock_dividend record=2025-02-30", "not a date"),
                ("BUYSELL 2025-03-03 10:00:00 ZZQ.US 10 USD 0 0 "
                 "type=stock_dividend label=x", "unknown .tt token"),
                ("BUYSELL 2025-03-03 10:00:00 ZZQ.US 10 USD 0 0 "
                 "record=2025-02-28", "unknown .tt token")):
            with self.assertRaises(ValueError) as cm:
                parse_tt_line(bad, account_name="margin")
            self.assertIn(why, str(cm.exception))


# ---------------------------------------------------------------- TJ-0467
class TestCallFlagSizedOnTheLossStillAllowed(unittest.TestCase):
    """TJ-0467: 100 ZZQ sold at a 1,000 loss; 90 shares bought back a
    week later (900 denied), then one call on 100 shares. Each loss
    share is matched once, so the call can replace at most the 10 still
    allowed: the flag said all 100 shares' loss (-1,000.00) was at risk
    when only -100.00 is."""

    CALL = "ZZQ250620C00050000.US"

    def _book(self, rebuy):
        return [tx("BUYSELL", "2025-01-02", "ZZQ.US", 100, 5000.0),
                tx("BUYSELL", "2025-03-03", "ZZQ.US", -100, 4000.0),
                tx("BUYSELL", "2025-03-10", "ZZQ.US", rebuy, 41.0 * rebuy),
                tx("BUYSELL", "2025-03-12", self.CALL, 1, 200.0)]

    def _flags(self, res):
        return [ln for ln in res["_stderr"].splitlines()
                if "call_vs_share_loss" in ln]

    @rule("US-WASH-12")
    def test_the_part_still_allowed_is_at_risk(self):
        res = _gains(self._book(90))
        sale = _sales(res, symbol="ZZQ.US", date="2025-03-03")[0]
        self.assertAlmostEqual(sale["disallowed_amount"], 900.0, places=2)
        flags = self._flags(res)
        self.assertEqual(len(flags), 1, res["_stderr"])
        self.assertIn("up to 10 of the 100 shares' loss (-100.00)",
                      flags[0])
        # The wording since TJ-0619: the contracts bought, then the
        # shares of them counted against this loss.
        self.assertIn("90 of the loss shares are already replaced",
                      flags[0])

    @rule("US-WASH-12")
    def test_a_loss_replaced_in_full_is_not_flagged(self):
        self.assertEqual(self._flags(_gains(self._book(100))), [])

    @rule("US-WASH-12")
    def test_with_no_share_replacement_the_whole_loss_is(self):
        book = [r for r in self._book(90) if r.date != "2025-03-10"]
        flags = self._flags(_gains(book))
        self.assertEqual(len(flags), 1)
        self.assertIn("up to 100 of the 100 shares' loss (-1,000.00)",
                      flags[0])
        self.assertNotIn("already replaced", flags[0])


# ---------------------------------------------------------------- TJ-0471
class TestEdgeCasesSkipTheLotSold(unittest.TestCase):
    """TJ-0471: 100 ZZQ bought 2025-02-03 and sold 2025-03-05 at a loss
    (day 30), 100 bought back 2025-03-14 (day 9). The lot sold is no
    replacement (shares closed by the same sale never replace each
    other), but WASH-SALE WINDOW EDGES named it as the day-30
    acquisition INSIDE the window."""

    def _edge(self, rows):
        from test_fix_us_year_end_1027 import _edge
        return _edge({"margin": rows})

    @rule("US-WASH-01")
    def test_the_loss_s_own_lot_is_no_edge(self):
        from test_fix_us_year_end_1027 import _t
        doc, edge, text = self._edge([
            _t("margin", "2025-02-03", 100, -5000),
            _t("margin", "2025-03-05", -100, 4000),
            _t("margin", "2025-03-14", 100, -4100)])
        sale = [r for r in doc["transactions"]
                if r.get("qty") and r.get("date") == "2025-03-05"][0]
        self.assertAlmostEqual(sale["disallowed_amount"], 1000.0, places=2)
        self.assertNotIn("acquisition of 100 in margin on 2025-02-03",
                         text)

    @rule("US-WASH-01")
    def test_shares_of_that_purchase_still_held_are_listed(self):
        from test_fix_us_year_end_1027 import _t
        # 150 bought 2025-02-03, 100 of them sold at a loss: the 50 still
        # held at the loss can replace it, day 30 before.
        _doc, _edge, text = self._edge([
            _t("margin", "2025-02-03", 150, -7500),
            _t("margin", "2025-03-05", -100, 4000)])
        self.assertIn("acquisition of 50 in margin on 2025-02-03: day 30 "
                      "before the loss -> INSIDE the window", text)


if __name__ == "__main__":
    unittest.main()
