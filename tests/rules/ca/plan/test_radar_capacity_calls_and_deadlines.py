"""Re-audit-2 planning fixes: the wash radar and the tools built on it
(wash-radar, sell-check, buy-check, safe-to-sell).

Capacity (CA-SL-08: CRA's formula per sale; the fills of one sale
share a replacement unit) — A2-0009, A2-0038, A2-0377, A2-0689, A2-0691,
A2-0167; split lineage
(A2-0382); declared contract sizes (A2-0373); futures options and
class-share roots (A2-0378, A2-0690); the trust ROC record date and the
s.40(3) floor in the radar's own pool (A2-0372, A2-1174, A2-1178); a
.tt row traded today (A2-0130); the past rescue deadline (A2-0688,
A2-0368); short wording (A2-0371, A2-0686, A2-1184); settlement
calendars (A2-1181, A2-1183); dust (A2-1168).

All data is synthetic.

Formerly: test_fix_a2_planning
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from taxjson.rules import ids as R
from _support.planning_radar_checks import _row, _rows, _check
from _support.planning_radar_row import base_rows


def _engine(taxable, sheltered=(), country="canada"):
    """The gains engine's verdict on the same rows, for the oracle
    assertions: {date: (raw_gain, disallowed_amount, permanent)}."""
    from taxjson.engine.by_country import get_tax_rules
    from taxjson.lib.tx_load import coerce_transaction_row
    tx = [coerce_transaction_row(dict(r), i, "t") for i, r in
          enumerate(taxable)]
    sh = [coerce_transaction_row(dict(r), i, "s") for i, r in
          enumerate(sheltered)]
    with contextlib.redirect_stderr(io.StringIO()):
        res = get_tax_rules(country).compute_gains(
            tx, sheltered_transactions=sh, detect_wash_sales=True)
    out = {}
    for r in res["transactions"]:
        if r.get("action") or not r.get("qty"):
            continue
        out.setdefault(r["date"], []).append(
            (round(float(r.get("raw_gain") or 0), 2),
             round(float(r.get("disallowed_amount") or 0), 2),
             round(float(r.get("permanently_disallowed") or 0), 2)))
    return out


def _engine_gains(taxable, sheltered=(), year=2026):
    """A gains file the radar's --gains reads, from the Canada engine."""
    from taxjson.engine.by_country import get_tax_rules
    from taxjson.lib.tx_load import coerce_transaction_row
    tx = [coerce_transaction_row(dict(r), i, "t") for i, r in
          enumerate(taxable)]
    sh = [coerce_transaction_row(dict(r), i, "s") for i, r in
          enumerate(sheltered)]
    with contextlib.redirect_stderr(io.StringIO()):
        res = get_tax_rules("canada").compute_gains(
            tx, sheltered_transactions=sh, detect_wash_sales=True)
    return {"summary": {"year": year, "tax_date_basis": "settle"},
            "metadata": {"account": "margin"},
            "transactions": res["transactions"]}


# ------------------------------------------------- CA-SL-08 capacity
@rule(R.CA_SL_08, R.CA_PLAN_01)
class TestRadarSharesReplacementCapacity(unittest.TestCase):
    """CA-SL-08 as the engine applies it: each sale on its own (owner
    decision on A2-0167 — a held unit may back the denials of two
    sales), the fills of one sale sharing a replacement unit
    (A2-0689)."""

    BOOK_CLAIMED = [
        _row("2026-05-04", "XYZ.TO", 300, 15000.0, rid="b1"),
        _row("2026-08-26", "XYZ.TO", -100, 4000.0, rid="s1"),
        _row("2026-08-28", "XYZ.TO", 100, 4100.0, rid="b2"),
        _row("2026-09-12", "XYZ.TO", -100, 3500.0, rid="s2"),
    ]

    def test_rebuy_backing_an_older_loss_backs_a_new_one_too(self):
        # The 08-28 rebuy backs the 08-26 loss and, still held, the
        # 09-12 loss as well: the engine denies both and the radar's
        # VIOLATION follows it (100 units of the open loss).
        eng = _engine(self.BOOK_CLAIMED)
        self.assertEqual(eng["2026-08-26"][0][1], 1000.0)
        self.assertEqual(eng["2026-09-12"][0][1], 1533.33)
        for gains in (None, [_engine_gains(self.BOOK_CLAIMED)]):
            row = _rows(self.BOOK_CLAIMED, "2026-10-01", gains=gains)[
                "XYZ.TO"]
            self.assertEqual(row["category"], "VIOLATION", row)
            self.assertAlmostEqual(row["denied_qty"], 100.0)

    def test_two_sales_each_backed_by_one_rebuy(self):
        book = [
            _row("2026-06-01", "XYZ.TO", 200, 10000.0, rid="b1"),
            _row("2026-09-01", "XYZ.TO", -100, 4000.0, rid="s1"),
            _row("2026-09-02", "XYZ.TO", -100, 4000.0, rid="s2"),
            _row("2026-09-10", "XYZ.TO", 100, 4100.0, rid="b2"),
        ]
        eng = _engine(book)
        self.assertEqual(eng["2026-09-01"][0][1] + eng["2026-09-02"][0][1],
                         2000.0)                    # 100 units each
        row = _rows(book, "2026-09-15")["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertAlmostEqual(row["denied_qty"], 200.0)
        self.assertIn("(200 units denied as things stand)",
                      row["advisory"])
        self.assertIn("Sell 100.0000 shares", row["advisory"])

    def test_registered_units_back_an_old_and_a_new_loss(self):
        # The TFSA's 100 units back the 08-10 loss (its window closed
        # long ago) AND the 09-20 loss: both are denied for good.
        tax = [_row("2026-05-01", "XYZ.TO", 200, 10000.0, rid="b1"),
               _row("2026-08-10", "XYZ.TO", -100, 4000.0, rid="s1"),
               _row("2026-09-20", "XYZ.TO", -100, 3800.0, rid="s2")]
        shl = [_row("2026-09-01", "XYZ.TO", 100, 4000.0, account="tfsa",
                    rid="t1")]
        eng = _engine(tax, shl)
        self.assertEqual(eng["2026-09-20"][0][2], 1200.0)
        self.assertEqual(eng["2026-08-10"][0][2], 1000.0)
        row = _rows(tax, "2026-10-01", sheltered=shl)["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION", row)
        self.assertAlmostEqual(row["denied_qty"], 100.0)
        self.assertIn("PERMANENTLY", row["advisory"])

    def test_split_fill_sale_counts_a_shared_rebuy_once(self):
        # A2-0689: 50+50 fills, 60 rebought -> 60 denied, not 100.
        book = [_row("2026-05-04", "XYZ.TO", 100, 5000.0, rid="b1"),
                _row("2026-09-21", "XYZ.TO", -50, 2000.0, rid="s1"),
                _row("2026-09-21", "XYZ.TO", -50, 2000.0, rid="s2"),
                _row("2026-09-26", "XYZ.TO", 30, 1230.0, rid="b2"),
                _row("2026-09-26", "XYZ.TO", 30, 1230.0, rid="b3")]
        row = _rows(book, "2026-10-01")["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertAlmostEqual(row["denied_qty"], 60.0)


@rule(R.CA_SL_08, R.CA_PLAN_01)
class TestRadarForwardViewSkipsSpentBacking(unittest.TestCase):
    """Canada: a sale today is a disposition of its own (CA-SL-08 per
    sale), so a registered purchase that backs an earlier loss still
    puts a sale today at risk (A2-0377 / A2-0691 reversed by the owner's
    decision on A2-0167)."""

    def test_registered_backing_of_an_earlier_loss_still_locks(self):
        tax = [_row("2026-06-01", "XYZ.TO", 100, 2000.0, rid="b1"),
               _row("2026-08-20", "XYZ.TO", -25, 250.0, rid="s1")]
        shl = [_row("2026-09-09", "XYZ.TO", 20, 220.0, account="rrsp",
                    rid="r1")]
        self.assertEqual(_engine(tax, shl)["2026-08-20"][0][2], 200.0)
        row = _rows(tax, "2026-10-01", sheltered=shl)["XYZ.TO"]
        self.assertEqual(row["category"], "LOCKED", row)
        self.assertAlmostEqual(row["at_risk_qty"], 20.0)

    def test_unspent_registered_backing_stays_locked(self):
        tax = [_row("2026-06-01", "XYZ.TO", 100, 2000.0, rid="b1")]
        shl = [_row("2026-09-09", "XYZ.TO", 20, 220.0, account="rrsp",
                    rid="r1")]
        row = _rows(tax, "2026-10-01", sheltered=shl)["XYZ.TO"]
        self.assertEqual(row["category"], "LOCKED")
        self.assertAlmostEqual(row["at_risk_qty"], 20.0)


@rule(R.US_WASH_02, R.US_PLAN_01)
class TestUsRadarForwardViewSkipsSpentBacking(unittest.TestCase):
    """A2-0377 / A2-0691 (USA): an IRA buy the engine already matched to
    an earlier loss (share for share) is not 'at risk' again."""

    def test_spent_ira_backing_is_not_locked(self):
        tax = [_row("2026-06-01", "XYZ.US", 100, 2000.0, rid="b1",
                    currency="USD"),
               _row("2026-08-20", "XYZ.US", -25, 250.0, rid="s1",
                    currency="USD")]
        shl = [_row("2026-09-09", "XYZ.US", 20, 220.0, account="ira",
                    rid="r1", currency="USD")]
        row = _rows(tax, "2026-10-01", sheltered=shl, country="usa")[
            "XYZ.US"]
        self.assertNotEqual(row["category"], "LOCKED", row)
        self.assertIsNone(row["at_risk_qty"])


# ------------------------------------------- units: splits, contracts
@rule(R.CA_SL_08)
class TestRadarUnitsAcrossASplit(unittest.TestCase):
    """A2-0382: a rebuy before a split is counted in post-split units."""

    def test_split_inside_the_window(self):
        book = [_row("2025-01-06", "XYZ.TO", 100, 2000.0, rid="b1"),
                _row("2025-03-03", "XYZ.TO", 100, 1000.0, rid="b2"),
                dict(_row("2025-03-05", "XYZ.TO", 2.0, 0.0,
                          action="SPLIT", rid="sp"), price=0.0),
                _row("2025-03-10", "XYZ.TO", -200, 600.0, rid="s1")]
        row = _rows(book, "2025-03-12")["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertAlmostEqual(row["denied_qty"], 200.0)


@rule(R.CA_SL_05, R.CA_PLAN_02)
class TestRadarCallContractSize(unittest.TestCase):
    """A2-0373: a mini call backs its declared size, not 100."""

    def test_declared_x10_call(self):
        book = [_row("2026-05-01", "XYZ.TO", 1000, 50000.0, rid="b1"),
                _row("2026-09-08", "XYZ.TO", -1000, 40000.0, rid="s1"),
                _row("2026-09-15", "XYZ261218C00050000.TO", 1, 20.0,
                     rid="c1", multiplier=10.0)]
        row = _rows(book, "2026-09-20")["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertAlmostEqual(row["denied_qty"], 10.0)

    def test_class_share_root_call_backs_the_class_line(self):
        # RBC books Rogers calls under RCI while the shares are
        # RCI.B.TO (CA-SL-05): the radar matches it like the engine.
        book = [_row("2026-05-01", "RCI.B.TO", 100, 5000.0, rid="b1"),
                _row("2026-09-08", "RCI.B.TO", -100, 4000.0, rid="s1"),
                _row("2026-09-15", "RCI261218C00050000.TO", 1, 200.0,
                     rid="c1")]
        eng = _engine(book)
        self.assertEqual(eng["2026-09-08"][0][1], 1000.0)
        row = _rows(book, "2026-09-20")["RCI.B.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertAlmostEqual(row["denied_qty"], 100.0)


@rule(R.CA_SL_15)
class TestRadarFuturesOptionIsFlagOnly(unittest.TestCase):
    """A2-0378 / A2-0690: a futures option on the loss's own contract is
    flagged for a manual check, whatever its spelling — never a VIOLATION
    sized as 100 (or 1) units."""

    def _book(self, opt):
        return [_row("2026-08-01", "F:CLG7.US", 5, 0.0, rid="f1",
                     currency="USD", multiplier=1000.0),
                _row("2026-09-08", "F:CLG7.US", -5, -50000.0, rid="f2",
                     currency="USD", multiplier=1000.0,
                     type="futures_settlement"),
                _row("2026-09-10", opt, 1, 1500.0, rid="o1",
                     currency="USD", multiplier=1000.0)]

    def test_same_prefix_spelling(self):
        for opt in ("F:CLG7261216C00070000.US", "/CLG7261216C00070000.US"):
            rows = _rows(self._book(opt), "2026-09-20")
            row = rows["F:CLG7.US"]
            self.assertNotEqual(row["category"], "VIOLATION", (opt, row))
            self.assertIsNone(row["denied_qty"])
            self.assertIn("check it by hand", row["advisory"])


# --------------------------------------------- the radar's own pool
@rule(R.CA_ACB_07)
class TestRadarOwnPoolRocFloor(unittest.TestCase):
    """A2-0372: a return of capital above the ACB floors it at nil
    (s.40(3)); the excess never makes the cost negative."""

    def test_roc_above_acb(self):
        book = [_row("2026-05-01", "XYZ.TO", 10, 100.0, rid="b1"),
                dict(_row("2026-06-01", "XYZ.TO", 0, -300.0,
                          action="ADJUST", rid="r1"), type="roc"),
                _row("2026-07-01", "XYZ.TO", 10, 1000.0, rid="b2"),
                _row("2026-09-01", "XYZ.TO", -20, 950.0, rid="s1"),
                _row("2026-09-05", "XYZ.TO", 5, 240.0, rid="b3")]
        row = _rows(book, "2026-09-10")["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION", row)


@rule(R.CA_INC_DATE_ROC_TRUST)
class TestRadarOwnPoolTrustRocRecordDate(unittest.TestCase):
    """A2-1174 / A2-1178: the radar's own walk books a Canadian trust's
    return of capital on its record date, as the engine does."""

    def test_sale_between_record_and_pay_date(self):
        book = [_row("2025-06-02", "XTR.TO", 100, 2000.0, rid="b1"),
                dict(_row("2026-01-15", "XTR.TO", 0, -300.0,
                          action="ADJUST", rid="r1"), type="roc",
                     record_date="2025-12-29"),
                _row("2026-01-05", "XTR.TO", -100, 1800.0, rid="s1"),
                _row("2026-01-08", "XTR.TO", 10, 180.0, rid="b2")]
        row = _rows(book, "2026-01-20")["XTR.TO"]
        self.assertNotEqual(row["category"], "VIOLATION", row)


# ---------------------------------------- a .tt trade made today
@rule(R.CA_PLAN_01)
class TestTtRowTradedToday(unittest.TestCase):
    """A2-0130: a .tt row's single date is its settle date in a settle
    project; one settling tomorrow was traded today and is booked."""

    def test_tt_sale_settling_tomorrow_is_booked(self):
        book = [_row("2026-06-01", "XYZ.TO", 100, 1000.0, rid="b1",
                     source="trades.tt"),
                _row("2026-10-02", "XYZ.TO", -100, 800.0, rid="s1",
                     source="trades.tt")]
        row = _rows(book, "2026-10-01")["XYZ.TO"]
        self.assertEqual(row["category"], "COOLING", row)
        self.assertEqual(float(row["taxable_qty"]), 0.0)

    def test_tt_row_two_days_out_is_not_yet_traded(self):
        book = [_row("2026-06-01", "XYZ.TO", 100, 1000.0, rid="b1",
                     source="trades.tt"),
                _row("2026-10-05", "XYZ.TO", -100, 800.0, rid="s1",
                     source="trades.tt")]
        row = _rows(book, "2026-10-01")["XYZ.TO"]
        self.assertEqual(float(row["taxable_qty"]), 100.0)


# ------------------------------------------ past rescue deadline
@rule(R.CA_PLAN_01)
class TestPastRescueDeadline(unittest.TestCase):
    """A2-0688: once no sale can settle by day 30, the radar says the
    loss is denied — never 'Sell ... by <yesterday>'."""

    BOOK = [_row("2026-06-01", "XYZ.TO", 100, 5000.0, rid="b1"),
            _row("2026-07-20", "XYZ.TO", -100, 4000.0,
                 settle="2026-07-21", rid="s1")]
    SHL = [_row("2026-07-22", "XYZ.TO", 100, 4100.0, account="tfsa",
                rid="t1")]

    def test_deadline_day_is_still_actionable(self):
        row = _rows(self.BOOK, "2026-08-19", sheltered=self.SHL)["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertIn("Sell", row["advisory"])
        self.assertFalse(row.get("deadline_passed"))

    def test_day_after_the_deadline(self):
        row = _rows(self.BOOK, "2026-08-20", sheltered=self.SHL)["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertTrue(row["deadline_passed"])
        self.assertNotIn("Sell 100", row["advisory"])
        self.assertIn("has passed", row["advisory"])
        self.assertIn("PERMANENTLY", row["advisory"])
        self.assertIsNone(row["rescue"])


# ------------------------------------------------- short wording
@rule(R.US_PLAN_01)
class TestShortWording(unittest.TestCase):
    """A2-0686 / A2-1184 / A2-0371: a short position is described as a
    short: covering, re-shorting."""

    def test_us_exitable_short(self):
        book = [_row("2025-03-10", "XYZ.US", -100, 5000.0, rid="s1",
                     currency="USD")]
        row = _rows(book, "2025-03-20", country="usa")["XYZ.US"]
        self.assertEqual(row["category"], "EXITABLE")
        self.assertIn("Recent short sale", row["advisory"])
        self.assertIn("Covering the FULL", row["advisory"])
        self.assertNotIn("Recent buy", row["advisory"])

    def test_us_short_with_ira_holding_is_not_ira_buy_advice(self):
        book = [_row("2025-01-10", "SHT.US", -10, 500.0, rid="s1",
                     currency="USD")]
        shl = [_row("2024-06-03", "SHT.US", 5, 200.0, account="ira",
                    rid="i1", currency="USD")]
        row = _rows(book, "2025-06-15", sheltered=shl, country="usa")[
            "SHT.US"]
        self.assertNotIn("IRA buy", row["advisory"])
        self.assertIn("cover", row["advisory"].lower())

    def test_us_clear_short(self):
        book = [_row("2025-01-10", "CLR.US", -10, 500.0, rid="s1",
                     currency="USD")]
        row = _rows(book, "2025-06-15", country="usa")["CLR.US"]
        self.assertEqual(row["category"], "CLEAR")
        self.assertIn("cover", row["advisory"].lower())
        self.assertNotIn("Safe to sell", row["advisory"])


@rule(R.CA_PLAN_01)
class TestWrittenOptionWording(unittest.TestCase):
    def test_written_call_is_bought_back_not_covered_with_shares(self):
        book = [_row("2026-08-03", "ABC261218C00050000.TO", -1, 200.0,
                     rid="w1")]
        row = _rows(book, "2026-10-01")["ABC261218C00050000.TO"]
        self.assertEqual(row["category"], "CLEAR")
        self.assertIn("buy it back", row["advisory"])
        self.assertNotIn("shares", row["advisory"])


# ------------------------------------------------ settle calendars
@rule(R.CA_PLAN_01)
class TestRescueCalendars(unittest.TestCase):

    def test_futures_rescue_settles_on_the_trade_date(self):
        # A2-1181: futures settle on the trade date (CA-DATE-09).
        book = [_row("2026-08-03", "F:ESZ6.US", 1, 0.0, rid="f1",
                     currency="USD", multiplier=50.0),
                _row("2026-09-18", "F:ESZ6.US", -1, -1000.0, rid="f2",
                     currency="USD", multiplier=50.0,
                     type="futures_settlement"),
                _row("2026-09-21", "F:ESZ6.US", 1, 0.0, rid="f3",
                     currency="USD", multiplier=50.0)]
        row = _rows(book, "2026-09-25")["F:ESZ6.US"]
        self.assertEqual(row["category"], "VIOLATION", row)
        self.assertEqual(row["clears_at"], "2026-10-16")
        self.assertIn("contract", row["advisory"])

    def test_tsx_usd_unit_walks_back_on_the_tsx_calendar(self):
        # A2-1183: DLR.U.TO trades on the TSX whatever its currency;
        # 2025-07-01 (Canada Day) is not a trading day there.
        book = [_row("2025-01-06", "DLR.U.TO", 100, 1100.0, rid="b1",
                     currency="USD"),
                _row("2025-05-30", "DLR.U.TO", -100, 1000.0,
                     settle="2025-06-02", rid="s1", currency="USD"),
                _row("2025-06-05", "DLR.U.TO", 100, 1000.0,
                     settle="2025-06-06", rid="b2", currency="USD")]
        row = _rows(book, "2025-06-10")["DLR.U.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertEqual(row["clears_at"], "2025-06-30")


@rule(R.CA_SL_08)
class TestCryptoDust(unittest.TestCase):
    """A2-1168: a coin rebuy below 1e-6 units is held (the pool's own
    relative tolerance), so it still backs a denial."""

    def test_dust_rebuy_is_held(self):
        book = [_row("2026-01-05", "BTC", 1, 100000.0, rid="b1"),
                _row("2026-09-20", "BTC", -1, 90000.0, rid="s1"),
                _row("2026-09-25", "BTC", 0.0000009, 0.08, rid="b2")]
        row = _rows(book, "2026-09-28")["BTC"]
        self.assertEqual(row["category"], "VIOLATION", row)

    def test_buy_at_the_books_zero_backs_nothing(self):
        """A taxable coin buy of 1e-9 units, the least a parser books
        (schema QTY_ZERO: a Kraken reward of 0.0000000010), is no recent
        buy for a loss sale today: the engine takes a replacement only
        above 1e-9 units (engine/ca/denial), so the row is CLEAR; 2e-9
        units is a recent buy (EXITABLE)."""
        for qty, cat in ((1e-9, "CLEAR"), (2e-9, "EXITABLE")):
            book = [_row("2026-01-05", "ZVBTC", 0.5, 50.0, rid="b1"),
                    _row("2026-09-25", "ZVBTC", qty, 0.0000001, rid="b2",
                         account="cash2")]
            row = _rows(book, "2026-09-28")["ZVBTC"]
            self.assertEqual(row["category"], cat, qty)


@rule(R.CA_SL_15)
class TestEdgeCasesFuturesOption(unittest.TestCase):
    """A2-0378: edge-cases never calls a futures option on the loss's own
    contract replacement property."""

    def test_same_contract_futures_option_is_not_a_backing_call(self):
        from taxjson.lib.edge_cases import analyze
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            book = [_row("2025-08-01", "F:CLG7.US", 5, 0.0, rid="f1",
                         currency="USD"),
                    _row("2025-09-08", "F:CLG7.US", -5, -5000.0, rid="f2",
                         currency="USD"),
                    _row("2025-09-10", "F:CLG7261216C00070000.US", 1,
                         1500.0, rid="o1", currency="USD")]
            (root / "work" / "margin_base.json").write_text(json.dumps(book))
            (root / "work" / "margin_gains_wash.json").write_text(json.dumps(
                {"transactions": [{"account": "margin", "id": "g-f2",
                                   "date": "2025-09-08",
                                   "date_settle": "2025-09-08",
                                   "symbol": "F:CLG7.US", "qty": 5,
                                   "gain": -5000.0, "raw_gain": -5000.0,
                                   "proceeds": 0.0, "cost": 5000.0}]}))
            doc = analyze(root, {"settings": {"year": 2025,
                                              "country": "canada"},
                                 "accounts": {"margin":
                                              {"type": "taxable"}}})
        self.assertEqual(doc["calls_in_windows"], [])


@rule(R.US_PLAN_02)
class TestUsRadarForwardCallNote(unittest.TestCase):
    """A2-0687: a long call bought by an IRA in the last 30 days is noted
    for a loss sale TODAY (the engine will flag it)."""

    def test_forward_note(self):
        tax = [_row("2026-01-05", "XYZ.US", 100, 2000.0, rid="b1",
                    currency="USD")]
        shl = [_row("2026-09-23", "XYZ261218C00015000.US", 1, 300.0,
                    account="ira", rid="c1", currency="USD")]
        row = _rows(tax, "2026-10-01", sheltered=shl, country="usa")[
            "XYZ.US"]
        self.assertIn("long call", row["advisory"])
        self.assertTrue(row["notes"])


@rule(R.CA_SL_14, R.CA_PLAN_02)
class TestRadarWarrantNote(unittest.TestCase):
    """A2-0129 (radar side): a warrant bought in an existing loss's
    window is noted for a manual check."""

    def test_warrant_in_window(self):
        book = [_row("2026-05-01", "XYZ.TO", 100, 5000.0, rid="b1"),
                _row("2026-09-08", "XYZ.TO", -100, 4000.0, rid="s1"),
                _row("2026-09-10", "XYZ.WS.TO", 100, 100.0, rid="w1")]
        row = _rows(book, "2026-09-20")["XYZ.TO"]
        self.assertIn("warrant/right", row["advisory"])
        self.assertIn("right_vs_share_loss", row["advisory"])


# ------------------------------------------- buy-check / sell-check
@rule(R.CA_SL_05, R.CA_PLAN_02)
class TestBuyCheckBuyToClose(unittest.TestCase):
    """A2-0370: buying back a written call acquires nothing."""

    RADAR = {"ABC.TO": {"category": "BLOCKED", "taxable_qty": 100.0,
                        "clears_at": "2026-10-26"}}

    def test_short_call_buy_back_is_safe(self):
        radar = dict(self.RADAR)
        radar["ABC261218C00050000.TO"] = {"category": "CLEAR",
                                          "taxable_qty": -1.0,
                                          "sheltered_qty": 0.0}
        out, code = _check("cmd_buy_check", radar,
                           ["ABC261218C00050000.TO"])
        self.assertEqual(code, 0, out)
        self.assertIn("SAFE*", out)
        self.assertIn("short 1 contract", out)
        self.assertNotIn("BLOCKED — a loss", out)

    def test_opening_call_buy_stays_unsafe(self):
        out, code = _check("cmd_buy_check", self.RADAR,
                           ["ABC261218C00050000.TO"])
        self.assertEqual(code, 1, out)
        self.assertIn("UNSAFE", out)


@rule(R.CA_SL_14, R.CA_SL_15, R.CA_PLAN_02)
class TestBuyCheckFlagsRights(unittest.TestCase):
    """A2-0129: a warrant or an adjusted-series call bought after a share
    loss is flagged for a manual check, never 'no wash exposure'."""

    RADAR = {"XYZ.TO": {"category": "BLOCKED", "taxable_qty": 0.0,
                        "clears_at": "2026-10-26"}}

    def test_warrant(self):
        out, code = _check("cmd_buy_check", self.RADAR, ["XYZ.WS.TO"])
        self.assertEqual(code, 0, out)
        self.assertIn("SAFE*", out)
        self.assertIn("warrant/right", out)
        self.assertIn("check it by hand", out)
        self.assertNotIn("no wash exposure", out)

    def test_adjusted_series_call(self):
        out, code = _check("cmd_buy_check", self.RADAR,
                           ["XYZ1261218C00040000.TO"])
        self.assertIn("adjusted option series", out)
        self.assertIn("superficial", out)
        self.assertNotIn("no wash exposure", out)

    def test_futures_option_on_a_futures_loss(self):
        radar = {"F:CLG7.US": {"category": "COOLING", "taxable_qty": 0.0,
                               "clears_at": "2026-10-09"}}
        out, code = _check("cmd_buy_check", radar,
                           ["F:CLG7261216C00070000.US"])
        self.assertEqual(code, 0, out)
        self.assertIn("futures contract", out)
        self.assertNotIn("UNSAFE", out)


@rule(R.US_WASH_14, R.US_PLAN_02)
class TestUsBuyCheckFlagsRights(unittest.TestCase):
    def test_us_warrant(self):
        radar = {"XYZ.US": {"category": "BLOCKED", "taxable_qty": 0.0}}
        out, code = _check("cmd_buy_check", radar, ["XYZ.WS.US"],
                           usa=True)
        self.assertEqual(code, 0, out)
        self.assertIn("wash sale", out)
        self.assertIn("check it by hand", out)


class TestBuyCheckUsCallIsANote(unittest.TestCase):
    """US-WASH-12: a US long call bought after a share loss is a note,
    not UNSAFE; in Canada it is a replacement (CA-SL-05)."""

    RADAR = {"XYZ.US": {"category": "BLOCKED", "taxable_qty": 50.0,
                        "clears_at": "2026-10-26"}}

    @rule(R.US_PLAN_02)
    @rule_absent(R.US_PLAN_02, country="canada")
    @rule(R.CA_PLAN_02)
    def test_call_after_share_loss(self):
        out, code = _check("cmd_buy_check", self.RADAR,
                           ["XYZ261218C00040000.US"], usa=True)
        self.assertEqual(code, 0, out)
        self.assertIn("only flags", out)
        out, code = _check("cmd_buy_check", self.RADAR,
                           ["XYZ261218C00040000.US"], usa=False)
        self.assertEqual(code, 1, out)
        self.assertIn("UNSAFE", out)


class TestLastLossLineIsOptionAware(unittest.TestCase):
    """A2-1172: a written call's buy-back loss is not a share query's
    'last loss sale'."""

    def test_option_loss_keyed_by_its_contract(self):
        from taxjson.cli import plan as cli_plan
        from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying
        with tempfile.TemporaryDirectory() as tmp:
            g = Path(tmp) / "margin_gains.json"
            g.write_text(json.dumps({"transactions": [
                {"symbol": "ABC261218C00050000.TO", "qty": 1,
                 "gain": -100.0, "date": "2026-09-28",
                 "date_settle": "2026-09-29"}]}))

            def canon(t):
                t = t.strip().upper()
                return (parse_option_underlying(t) if is_option_symbol(t)
                        else t)
            ll = cli_plan._last_loss_by_class({"margin": g}, canon, set(),
                                       usa=False)
        self.assertIsNone(cli_plan._last_loss_for(ll, "ABC.TO", "ABC.TO", "buy"))
        self.assertIsNone(cli_plan._last_loss_for(ll, "ABC.TO", "ABC.TO", "sell"))
        got = cli_plan._last_loss_for(ll, "ABC261218C00050000.TO", "ABC.TO", "buy")
        self.assertEqual(got["symbol"], "ABC261218C00050000.TO")


class TestSellCheckPastDeadline(unittest.TestCase):
    """A2-0368: a VIOLATION whose rescue date passed is not an ACTION."""

    @rule(R.CA_PLAN_01)
    def test_deadline_passed_violation(self):
        radar = {"XYZ.TO": {"category": "VIOLATION",
                            "deadline_passed": True,
                            "clears_at": "2026-09-30",
                            "advisory": "VIOLATION: the loss ... has passed",
                            "rescue": None,
                            "taxable_qty": 0.0, "sheltered_qty": 100.0}}
        res, code = _check("cmd_sell_check", radar, ["XYZ.TO"],
                           as_json=True)
        r = res["results"][0]
        self.assertEqual(code, 0)
        self.assertNotIn(r["verdict"], ("ACTION", "UNSAFE"))
        self.assertIsNone(r["act_by"])
        self.assertFalse(any("unless they also sell" in ln
                             for ln in r["detail"]))

    @rule(R.US_PLAN_02)
    def test_sell_check_relays_a_us_call_note(self):
        # A2-0687: CLEAR, but an IRA bought a call 8 days ago.
        radar = {"XYZ.US": {"category": "CLEAR", "taxable_qty": 100.0,
                            "sheltered_qty": 0.0,
                            "notes": ["NOTE: a long call on these shares "
                                      "was bought in the last 30 days — "
                                      "check it by hand."]}}
        out, code = _check("cmd_sell_check", radar, ["XYZ.US"], usa=True)
        self.assertEqual(code, 0)
        self.assertIn("SAFE*", out)
        self.assertIn("long call", out)


class TestClassDatesAggregation(unittest.TestCase):
    """A2-0369: the binding date sits on either leg, first- or
    second-sorted, and the --json clears_at / act_by carry it."""

    def test_buy_check_latest_clear_date(self):
        radar = {"XYZ.TO": {"category": "BLOCKED", "clears_at": "2026-09-20"},
                 "XYZ.US": {"category": "BLOCKED", "clears_at": "2026-09-01"}}
        res, _ = _check("cmd_buy_check", radar, ["XYZ"], as_json=True)
        self.assertEqual(res["results"][0]["clears_at"], "2026-09-20")

    def test_sell_check_latest_clear_and_earliest_act_by(self):
        radar = {"XYZ.TO": {"category": "LOCKED", "clears_at": "2026-09-20",
                            "advisory": "LOCKED: ...", "taxable_qty": 10.0},
                 "XYZ.US": {"category": "LOCKED", "clears_at": "2026-09-01",
                            "advisory": "LOCKED: ...", "taxable_qty": 10.0}}
        res, _ = _check("cmd_sell_check", radar, ["XYZ"], as_json=True)
        self.assertEqual(res["results"][0]["clears_at"], "2026-09-20")
        radar = {"XYZ.TO": {"category": "VIOLATION",
                            "clears_at": "2026-09-01",
                            "advisory": "VIOLATION: ...", "rescue": [],
                            "taxable_qty": 10.0},
                 "XYZ.US": {"category": "VIOLATION",
                            "clears_at": "2026-09-20",
                            "advisory": "VIOLATION: ...", "rescue": [],
                            "taxable_qty": 10.0}}
        res, _ = _check("cmd_sell_check", radar, ["XYZ"], as_json=True)
        self.assertEqual(res["results"][0]["act_by"], "2026-09-01")

    @rule(R.CA_PLAN_01)
    def test_sell_check_earliest_act_by_on_the_second_sorted_leg(self):
        # The class's sell-by date is the earlier of its legs' wherever
        # it sorts: here the second leg's, after a later one. Keeping
        # the first date carried across the rows gave 2024-12-02 (the
        # step 15 sell-check review's pair run).
        radar = {"XYZ.TO": {"category": "VIOLATION",
                            "clears_at": "2024-12-02",
                            "advisory": "VIOLATION: sell the replacement.",
                            "rescue": [], "taxable_qty": 10.0},
                 "XYZ.US": {"category": "VIOLATION",
                            "clears_at": "2024-11-27",
                            "advisory": "VIOLATION: sell the replacement.",
                            "rescue": [], "taxable_qty": 10.0}}
        res, _ = _check("cmd_sell_check", radar, ["XYZ"], as_json=True)
        self.assertEqual(res["results"][0]["verdict"], "ACTION")
        self.assertEqual(res["results"][0]["act_by"], "2024-11-27")


def _project(tmp, rows, ticker_map=None):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    if ticker_map is not None:
        (root / "ticker.map").write_bytes(ticker_map)
    (root / "work" / "margin_base.json").write_text(json.dumps(base_rows(rows)))
    return root


class TestWashClassContextInputs(unittest.TestCase):

    def test_bare_array_book_is_a_book(self):
        # A2-1180: the radar accepts a bare-array base book; buy-check
        # and sell-check crashed on it.
        from taxjson.cli import plan as cli_plan
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [_row("2026-06-01", "MSFT.US", 10,
                                       4000.0, currency="USD")])
            with contextlib.redirect_stderr(io.StringIO()):
                radar, canon, _ = cli_plan._wash_class_context(
                    root, root / "work", "t")
        self.assertIn("MSFT.US", radar)

    def test_unreadable_ticker_map_refuses(self):
        # A2-0683: a map that cannot be read stops buy/sell-check, as
        # it stops `tjs run`.
        from taxjson.cli import plan as cli_plan
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [_row("2026-06-01", "MSFT.US", 10,
                                       4000.0, currency="USD")],
                            ticker_map=b"TOBASE AAPL.NE \xff\xfe AAPL.US\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    cli_plan._wash_class_context(root, root / "work", "t")
        # Exit 2, an input to fix, never UNSAFE's 1 (backlog TJ-0351).
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("ticker.map cannot be read", err.getvalue())


if __name__ == "__main__":
    unittest.main()
