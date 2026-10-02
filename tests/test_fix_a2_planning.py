"""Re-audit-2 planning fixes: the wash radar and the tools built on it
(wash-radar, sell-check, buy-check, safe-to-sell).

Capacity sharing (CA-SL-08: each replacement unit backs a single
denial) — A2-0009, A2-0038, A2-0377, A2-0689, A2-0691; split lineage
(A2-0382); declared contract sizes (A2-0373); futures options and
class-share roots (A2-0378, A2-0690); the trust ROC record date and the
s.40(3) floor in the radar's own pool (A2-0372, A2-1174, A2-1178); a
.tt row traded today (A2-0130); the past rescue deadline (A2-0688,
A2-0368); short wording (A2-0371, A2-0686, A2-1184); settlement
calendars (A2-1181, A2-1183); dust (A2-1168).

All data is synthetic.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent
RADAR = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar"]


def _row(date_, symbol, qty, net, settle=None, account="margin",
         action="BUYSELL", rid=None, currency="CAD", **kw):
    r = dict(action=action, date=date_, date_settle=settle or date_,
             time="10:00:00", symbol=symbol, quantity=qty,
             net_amount=net, currency=currency, account=account,
             price=abs(net / qty) if qty else 0.0, **kw)
    if rid:
        r["id"] = rid
    return r


def _radar_doc(taxable, as_of, sheltered=None, country="canada",
               gains=None, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        t = tmp / "margin_base.json"
        t.write_text(json.dumps({"transactions": taxable}))
        cmd = RADAR + ["--country", country, "--taxable", str(t),
                       "--date", as_of, "--all", "--json", *extra]
        if sheltered is not None:
            s = tmp / "sheltered_base.json"
            s.write_text(json.dumps({"transactions": sheltered}))
            cmd += ["--sheltered", str(s)]
        for i, g in enumerate(gains or []):
            gp = tmp / f"g{i}_gains_wash.json"
            gp.write_text(json.dumps(g))
            cmd += ["--gains", str(gp)]
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                           text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


def _rows(*a, **kw):
    doc = _radar_doc(*a, **kw)
    return {row["ticker"]: row for sec in doc["sections"]
            for row in sec["rows"]}


def _engine(taxable, sheltered=(), country="canada"):
    """The gains engine's verdict on the same rows, for the oracle
    assertions: {date: (raw_gain, disallowed_amount, permanent)}."""
    from taxjson.lib.core import coerce_transaction_row, get_tax_rules
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
    from taxjson.lib.core import coerce_transaction_row, get_tax_rules
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
@rule("CA-SL-08", "CA-PLAN-01")
class TestRadarSharesReplacementCapacity(unittest.TestCase):
    """A2-0009 / A2-0038 / A2-0689: each replacement unit backs ONE
    denial, as the engine applies it."""

    BOOK_CLAIMED = [
        _row("2026-05-04", "XYZ.TO", 300, 15000.0, rid="b1"),
        _row("2026-08-26", "XYZ.TO", -100, 4000.0, rid="s1"),
        _row("2026-08-28", "XYZ.TO", 100, 4100.0, rid="b2"),
        _row("2026-09-12", "XYZ.TO", -100, 3500.0, rid="s2"),
    ]

    def test_rebuy_claimed_by_an_older_loss_backs_no_new_violation(self):
        eng = _engine(self.BOOK_CLAIMED)
        self.assertEqual(eng["2026-09-12"][0][1], 0.0)   # engine allows
        for gains in (None, [_engine_gains(self.BOOK_CLAIMED)]):
            row = _rows(self.BOOK_CLAIMED, "2026-10-01", gains=gains)[
                "XYZ.TO"]
            self.assertNotEqual(row["category"], "VIOLATION", row)
            self.assertIsNone(row["denied_qty"])
            self.assertNotIn("Sell 200", row["advisory"])

    def test_two_losses_share_one_rebuy(self):
        book = [
            _row("2026-06-01", "XYZ.TO", 200, 10000.0, rid="b1"),
            _row("2026-09-01", "XYZ.TO", -100, 4000.0, rid="s1"),
            _row("2026-09-02", "XYZ.TO", -100, 4000.0, rid="s2"),
            _row("2026-09-10", "XYZ.TO", 100, 4100.0, rid="b2"),
        ]
        eng = _engine(book)
        self.assertEqual(eng["2026-09-01"][0][1] + eng["2026-09-02"][0][1],
                         1000.0)                    # 100 units denied
        row = _rows(book, "2026-09-15")["XYZ.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertAlmostEqual(row["denied_qty"], 100.0)
        self.assertIn("(100 units denied as things stand)",
                      row["advisory"])

    def test_registered_units_claimed_by_an_old_loss(self):
        # A2-0038: the TFSA's 100 units back the 08-10 loss (its window
        # closed long ago); the 09-20 loss is allowed.
        tax = [_row("2026-05-01", "XYZ.TO", 200, 10000.0, rid="b1"),
               _row("2026-08-10", "XYZ.TO", -100, 4000.0, rid="s1"),
               _row("2026-09-20", "XYZ.TO", -100, 3800.0, rid="s2")]
        shl = [_row("2026-09-01", "XYZ.TO", 100, 4000.0, account="tfsa",
                    rid="t1")]
        eng = _engine(tax, shl)
        self.assertEqual(eng["2026-09-20"][0][1], 0.0)
        self.assertEqual(eng["2026-08-10"][0][2], 1000.0)
        row = _rows(tax, "2026-10-01", sheltered=shl)["XYZ.TO"]
        self.assertNotEqual(row["category"], "VIOLATION", row)
        self.assertIsNone(row["denied_qty"])

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


@rule("CA-SL-08", "CA-PLAN-01")
class TestRadarForwardViewSkipsSpentBacking(unittest.TestCase):
    """A2-0377 / A2-0691 (Canada): a registered purchase that already
    backs an earlier loss is not 'at risk' for a sale today."""

    def test_spent_registered_backing_is_not_locked(self):
        tax = [_row("2026-06-01", "XYZ.TO", 100, 2000.0, rid="b1"),
               _row("2026-08-20", "XYZ.TO", -25, 250.0, rid="s1")]
        shl = [_row("2026-09-09", "XYZ.TO", 20, 220.0, account="rrsp",
                    rid="r1")]
        self.assertEqual(_engine(tax, shl)["2026-08-20"][0][2], 200.0)
        row = _rows(tax, "2026-10-01", sheltered=shl)["XYZ.TO"]
        self.assertNotEqual(row["category"], "LOCKED", row)
        self.assertIsNone(row["at_risk_qty"])

    def test_unspent_registered_backing_stays_locked(self):
        tax = [_row("2026-06-01", "XYZ.TO", 100, 2000.0, rid="b1")]
        shl = [_row("2026-09-09", "XYZ.TO", 20, 220.0, account="rrsp",
                    rid="r1")]
        row = _rows(tax, "2026-10-01", sheltered=shl)["XYZ.TO"]
        self.assertEqual(row["category"], "LOCKED")
        self.assertAlmostEqual(row["at_risk_qty"], 20.0)


@rule("US-WASH-02", "US-PLAN-01")
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
@rule("CA-SL-08")
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


@rule("CA-SL-05", "CA-PLAN-02")
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


@rule("CA-SL-15")
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
@rule("CA-ACB-07")
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


@rule("CA-INC-DATE-ROC-TRUST")
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
@rule("CA-PLAN-01")
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
@rule("CA-PLAN-01")
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
@rule("US-PLAN-01")
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


# ------------------------------------------------ settle calendars
@rule("CA-PLAN-01")
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


@rule("CA-SL-08")
class TestCryptoDust(unittest.TestCase):
    """A2-1168: a coin rebuy below 1e-6 units is held (the pool's own
    relative tolerance), so it still backs a denial."""

    def test_dust_rebuy_is_held(self):
        book = [_row("2026-01-05", "BTC", 1, 100000.0, rid="b1"),
                _row("2026-09-20", "BTC", -1, 90000.0, rid="s1"),
                _row("2026-09-25", "BTC", 0.0000009, 0.08, rid="b2")]
        row = _rows(book, "2026-09-28")["BTC"]
        self.assertEqual(row["category"], "VIOLATION", row)


@rule("CA-SL-15")
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


@rule("US-PLAN-02")
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


@rule("CA-SL-14", "CA-PLAN-02")
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
def _check(cmd_name, radar, symbols, last_loss=None, usa=False,
           as_json=False):
    """Drive buy-check / sell-check against a synthetic radar."""
    from argparse import Namespace
    from contextlib import redirect_stdout
    from unittest.mock import patch
    import taxjson.bin.taxjson_run as R
    from taxjson.lib.core import is_option_symbol, parse_option_underlying

    def canon(t):
        t = t.strip().upper()
        if is_option_symbol(t):
            return parse_option_underlying(t) or t
        return t
    ctx = (radar, canon, last_loss or {})
    buf = io.StringIO()
    code = 0
    with patch.object(R, "_wash_class_context", return_value=ctx), \
            patch.object(R, "_radar_country_is_usa", return_value=usa):
        try:
            with redirect_stdout(buf):
                getattr(R, cmd_name)(Namespace(dir=".", symbol=list(symbols),
                                               json=as_json))
        except SystemExit as e:
            code = e.code or 0
    out = buf.getvalue()
    return (json.loads(out) if as_json else out), code


@rule("CA-SL-05", "CA-PLAN-02")
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


@rule("CA-SL-14", "CA-SL-15", "CA-PLAN-02")
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


@rule("US-WASH-14", "US-PLAN-02")
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

    @rule("US-PLAN-02")
    @rule_absent("US-PLAN-02", country="canada")
    @rule("CA-PLAN-02")
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
        import taxjson.bin.taxjson_run as R
        from taxjson.lib.core import (is_option_symbol,
                                      parse_option_underlying)
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
            ll = R._last_loss_by_class({"margin": g}, canon, set(),
                                       usa=False)
        self.assertIsNone(R._last_loss_for(ll, "ABC.TO", "ABC.TO", "buy"))
        self.assertIsNone(R._last_loss_for(ll, "ABC.TO", "ABC.TO", "sell"))
        got = R._last_loss_for(ll, "ABC261218C00050000.TO", "ABC.TO", "buy")
        self.assertEqual(got["symbol"], "ABC261218C00050000.TO")


class TestSellCheckPastDeadline(unittest.TestCase):
    """A2-0368: a VIOLATION whose rescue date passed is not an ACTION."""

    @rule("CA-PLAN-01")
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

    @rule("US-PLAN-02")
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
    """A2-0369: the binding date sits on the FIRST-sorted leg, and the
    --json clears_at / act_by carry it."""

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


def _project(tmp, rows, ticker_map=None):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    if ticker_map is not None:
        (root / "ticker.map").write_bytes(ticker_map)
    (root / "work" / "margin_base.json").write_text(json.dumps(rows))
    return root


class TestWashClassContextInputs(unittest.TestCase):

    def test_bare_array_book_is_a_book(self):
        # A2-1180: the radar accepts a bare-array base book; buy-check
        # and sell-check crashed on it.
        import taxjson.bin.taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [_row("2026-06-01", "MSFT.US", 10,
                                       4000.0, currency="USD")])
            with contextlib.redirect_stderr(io.StringIO()):
                radar, canon, _ = R._wash_class_context(
                    root, root / "work", "t")
        self.assertIn("MSFT.US", radar)

    def test_unreadable_ticker_map_refuses(self):
        # A2-0683: a map that cannot be read stops buy/sell-check, as
        # it stops `taxjson run`.
        import taxjson.bin.taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [_row("2026-06-01", "MSFT.US", 10,
                                       4000.0, currency="USD")],
                            ticker_map=b"TOBASE AAPL.NE \xff\xfe AAPL.US\n")
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    R._wash_class_context(root, root / "work", "t")
        self.assertIn("ticker.map", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
