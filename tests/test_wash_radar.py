"""taxjson-wash-radar must not count cash-flow rows as share positions.

Regression: DIVIDEND/TAX/INTEREST rows carry a `quantity` equal to the shares
the cash event was computed ON (for reconciliation), not shares acquired. The
position walk was adding them, so a fully-sold position (taxable or sheltered)
looked like it still held shares — producing a bogus "Sheltered holdings exist"
wash-sale warning (the WSP.TO case). After the fix the warning reflects the
real reason: a recent realized loss.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(action, date, symbol, qty, net, time="09:30:00"):
    return dict(action=action, date=date, time=time, symbol=symbol,
                quantity=qty, net_amount=net, currency="CAD", account="acct")


def _run(taxable, sheltered, date):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.json"
        s = Path(tmp) / "s.json"
        t.write_text(json.dumps({"transactions": taxable}))
        s.write_text(json.dumps({"transactions": sheltered}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
             "--taxable", str(t), "--sheltered", str(s), "--date", date],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout


# Bought 50 @ 100 then sold 50 @ 80 (a loss); a dividend was paid on the 50
# shares in between. Net position = 0 in BOTH accounts.
_TAXABLE = [
    _tx("BUYSELL", "2026-01-05", "WSP.TO", 50, 5000.0),
    _tx("DIVIDEND", "2026-03-15", "WSP.TO", 50, 100.0),
    _tx("BUYSELL", "2026-06-02", "WSP.TO", -50, 4000.0),
]
_SHELTERED = [
    _tx("BUYSELL", "2025-05-28", "WSP.TO", 50, 5000.0),
    _tx("DIVIDEND", "2025-07-15", "WSP.TO", 50, 100.0),
    _tx("BUYSELL", "2025-10-03", "WSP.TO", -50, 4800.0),
]


class TestWashRadarDividendNotCountedAsPosition(unittest.TestCase):
    def _wsp_line(self, out):
        line = next((ln for ln in out.splitlines() if ln.startswith("WSP.TO")), None)
        self.assertIsNotNone(line, f"no WSP.TO row in:\n{out}")
        return line

    def test_fully_sold_positions_show_zero_not_dividend_qty(self):
        line = self._wsp_line(_run(_TAXABLE, _SHELTERED, "2026-06-15"))
        cells = [c.strip() for c in line.split("|")]
        self.assertEqual(float(cells[1]), 0.0, "taxable should be flat, not 50/150")
        self.assertEqual(float(cells[2]), 0.0, "sheltered should be flat, not the dividend qty")

    def test_no_bogus_sheltered_holdings_warning(self):
        out = _run(_TAXABLE, _SHELTERED, "2026-06-15")
        line = self._wsp_line(out)
        self.assertNotIn("Sheltered holdings exist", line)

    def test_real_recent_loss_risk_is_reported(self):
        # The sale on 2026-06-02 was a loss; 13 days later it's still in-window.
        line = self._wsp_line(_run(_TAXABLE, _SHELTERED, "2026-06-15"))
        self.assertIn("loss", line.lower())


class TestWashRadarRecoveryLabel(unittest.TestCase):
    def test_recoverable_loss_uses_violation_label(self):
        # Sold at a loss, then repurchased substitute shares still held in the
        # window — the loss can still be rescued by selling them, so the
        # advisory is the actionable VIOLATION label (not the old PENDING).
        taxable = [
            _tx("BUYSELL", "2026-01-05", "AAA.US", 100, 10000.0),
            _tx("BUYSELL", "2026-06-02", "AAA.US", -100, 8000.0),   # loss
            _tx("BUYSELL", "2026-06-10", "AAA.US", 100, 8200.0),    # substitute lot
        ]
        out = _run(taxable, [], "2026-06-15")
        line = next(ln for ln in out.splitlines() if ln.startswith("AAA.US"))
        self.assertIn("VIOLATION:", line)
        self.assertNotIn("PENDING", line)
        # The VIOLATION section is the top (most-actionable) group.
        self.assertIn("VIOLATION —", out)


def _split(date, symbol, ratio, account="acct", time="09:30:00"):
    return dict(action="SPLIT", date=date, time=time, symbol=symbol,
                quantity=ratio, net_amount=0.0, currency="CAD", account=account)


def _cols(out, ticker):
    """Return (taxable_qty, sheltered_qty) for a ticker's report row."""
    for ln in out.splitlines():
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) >= 3 and parts[0] == ticker:
            return float(parts[1]), float(parts[2])
    raise AssertionError(f"{ticker} not found in:\n{out}")


class TestWashRadarSplits(unittest.TestCase):
    def test_split_scales_position(self):
        # Buy 100, 2-for-1 split (ratio 2 → 200), sell 50 ⇒ 150 held.
        # Without applying the split the walk would show 100 − 50 = 50, and a
        # bigger post-split sell would underflow to a phantom NEGATIVE (the
        # LFE.TO bug). The ratio lives in the SPLIT row's `quantity`.
        taxable = [
            _tx("BUYSELL", "2025-01-01", "ZZZ.TO", 100, 1000.0),
            _split("2025-02-01", "ZZZ.TO", 2.0),
            _tx("BUYSELL", "2025-03-01", "ZZZ.TO", -50, 600.0),
        ]
        tax, _ = _cols(_run(taxable, [], "2025-06-01"), "ZZZ.TO")
        self.assertEqual(tax, 150.0)

    def test_split_applies_per_account_not_combined_pool(self):
        # All sheltered accounts share one --sheltered file. A 2:1 split in the
        # TFSA must scale ONLY the TFSA's 10 shares (→20); the LIRA's 5 shares
        # are untouched ⇒ combined sheltered = 25. A per-file pool would split
        # the combined 15 → 30.
        sheltered = [
            dict(action="BUYSELL", date="2025-01-01", time="09:30:00",
                 symbol="YYY.TO", quantity=10, net_amount=100.0,
                 currency="CAD", account="tfsa"),
            _split("2025-02-01", "YYY.TO", 2.0, account="tfsa"),
            dict(action="BUYSELL", date="2025-01-01", time="09:30:00",
                 symbol="YYY.TO", quantity=5, net_amount=50.0,
                 currency="CAD", account="lira"),
        ]
        _, shl = _cols(_run([], sheltered, "2025-06-01"), "YYY.TO")
        self.assertEqual(shl, 25.0)




class TestStillHeldTest(unittest.TestCase):
    """s. 40(2)(g) still-held: an in-window buy whose account has since
    SOLD TO ZERO must not hard-LOCK the taxable loss (real MTZ.US case:
    lira bought, sold out ten days later, margin stayed 'permanently
    denied'). It downgrades to CAUTION with the forward-looking caveat;
    an acquirer that still holds stays LOCKED."""

    _TAX = [dict(action="BUYSELL", date="2026-05-26", time="09:30:00",
                 symbol="MTZ.US", quantity=25, net_amount=12500.0,
                 currency="CAD", account="margin")]

    def _line(self, out):
        line = next((ln for ln in out.splitlines()
                     if ln.startswith("MTZ.US")), None)
        self.assertIsNotNone(line, out)
        return line

    def test_exited_sheltered_buyer_downgrades_to_caution(self):
        shl = [dict(action="BUYSELL", date="2026-07-02", time="09:30:00",
                    symbol="MTZ.US", quantity=5, net_amount=2500.0,
                    currency="CAD", account="lira"),
               dict(action="BUYSELL", date="2026-07-16", time="09:30:00",
                    symbol="MTZ.US", quantity=-5, net_amount=2400.0,
                    currency="CAD", account="lira")]
        line = self._line(_run(self._TAX, shl, "2026-07-27"))
        self.assertIn("CAUTION", line)
        self.assertNotIn("LOCKED", line)
        self.assertIn("all sheltered accounts are now at 0", line)
        self.assertIn("30 days AFTER your sale", line)

    def test_still_holding_sheltered_buyer_stays_locked(self):
        shl = [dict(action="BUYSELL", date="2026-07-02", time="09:30:00",
                    symbol="MTZ.US", quantity=5, net_amount=2500.0,
                    currency="CAD", account="lira")]
        line = self._line(_run(self._TAX, shl, "2026-07-27"))
        self.assertIn("LOCKED", line)
        self.assertIn("lira", line)

    def test_other_sheltered_holder_blocks_downgrade(self):
        # Fungibility (real ALK.TO case): the acquirer sold out, but a
        # DIFFERENT sheltered account still holds identical shares —
        # the group's holding keeps the loss superficial: LOCKED.
        shl = [dict(action="BUYSELL", date="2025-01-10", time="09:30:00",
                    symbol="MTZ.US", quantity=100, net_amount=40000.0,
                    currency="CAD", account="rrsp"),
               dict(action="BUYSELL", date="2026-07-02", time="09:30:00",
                    symbol="MTZ.US", quantity=5, net_amount=2500.0,
                    currency="CAD", account="lira"),
               dict(action="BUYSELL", date="2026-07-16", time="09:30:00",
                    symbol="MTZ.US", quantity=-5, net_amount=2400.0,
                    currency="CAD", account="lira")]
        line = self._line(_run(self._TAX, shl, "2026-07-27"))
        self.assertIn("LOCKED", line)
        self.assertNotIn("CAUTION", line)

    def test_combined_with_flat_sheltered_is_exitable_with_note(self):
        # Real SLV.US case: taxable in-window buy AND a sheltered
        # in-window buy whose whole group has since exited — the
        # combined branch used to keep "permanently denied" LOCKED.
        tax = self._TAX + [
            dict(action="BUYSELL", date="2026-07-02", time="09:30:00",
                 symbol="MTZ.US", quantity=10, net_amount=4500.0,
                 currency="CAD", account="margin")]
        shl = [dict(action="BUYSELL", date="2026-07-08", time="09:30:00",
                    symbol="MTZ.US", quantity=5, net_amount=2500.0,
                    currency="CAD", account="lira"),
               dict(action="BUYSELL", date="2026-07-16", time="09:30:00",
                    symbol="MTZ.US", quantity=-5, net_amount=2400.0,
                    currency="CAD", account="lira")]
        line = self._line(_run(tax, shl, "2026-07-27"))
        self.assertIn("EXITABLE", line)
        self.assertIn("NOTE: SHELTERED", line)
        self.assertNotIn("permanently denied", line)

    def test_taxable_only_recent_buy_is_exitable(self):
        # Real TA.TO case: your own recent taxable buy is not a hard
        # lock — a FULL exit realizes the loss; only a partial sale
        # is superficial.
        tax = self._TAX + [
            dict(action="BUYSELL", date="2026-07-23", time="09:30:00",
                 symbol="MTZ.US", quantity=10, net_amount=4500.0,
                 currency="CAD", account="margin")]
        line = self._line(_run(tax, [], "2026-07-27"))
        self.assertIn("EXITABLE", line)
        self.assertIn("FULL position", line)
        self.assertNotIn("LOCKED", line)


def _stx(action, date, settle, symbol, qty, price, **kw):
    d = dict(action=action, date=date, time="09:30:00", date_settle=settle,
             symbol=symbol, quantity=qty, currency="CAD", price=price,
             net_amount=abs(qty) * price, fee=0.0, account="margin")
    d.update(kw)
    return d


def _run_json(taxable, date):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.json"
        t.write_text(json.dumps({"transactions": taxable}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
             "--taxable", str(t), "--date", date, "--json"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    return {r["ticker"]: r for s in doc["sections"] for r in s["rows"]}


def _disallowed(rows):
    from taxjson.lib.core import CanadaTaxRules, TaxTransaction
    res = CanadaTaxRules().compute_gains([TaxTransaction(**r) for r in rows])
    return float(res["summary"].get("total_disallowed", 0.0) or 0.0)


class TestRescueDeadlineIsATradeDate(unittest.TestCase):
    """2026-09 audit: the VIOLATION advisory quoted loss_settle+30 as
    "sell by", but the engine tests the rescue sale's SETTLE date
    against that bound — trading ON the printed date settled T+1 and
    the loss stayed denied. Loss settles 2026-08-21, so the settle
    bound is 2026-09-20, a Sunday: the last trade that settles in time
    is Thu 2026-09-17 (a Friday trade settles Monday the 21st)."""

    ROWS = [_stx("BUYSELL", "2026-06-01", "2026-06-02", "DL.TO", 100, 50.0),
            _stx("BUYSELL", "2026-08-20", "2026-08-21", "DL.TO", -100, 40.0),
            _stx("BUYSELL", "2026-08-24", "2026-08-25", "DL.TO", 100, 41.0)]

    def test_sidecar_and_text_carry_the_trade_date(self):
        row = _run_json(self.ROWS, "2026-09-10")["DL.TO"]
        self.assertEqual(row["category"], "VIOLATION")
        self.assertEqual(row["clears_at"], "2026-09-17")
        self.assertEqual(row["settle_deadline"], "2026-09-20")
        self.assertIn("by 2026-09-17", row["advisory"])
        self.assertIn("SETTLE by 2026-09-20", row["advisory"])
        self.assertTrue(row["clears_in_at_generation"].startswith(
            "2026-09-17 (7d)"))

    def test_engine_agrees_with_both_sides_of_the_printed_date(self):
        # Trading on the printed date (settles the 18th) rescues it...
        ok = self.ROWS + [_stx("BUYSELL", "2026-09-17", "2026-09-18",
                               "DL.TO", -100, 42.0)]
        self.assertAlmostEqual(_disallowed(ok), 0.0, places=2)
        # ...trading on the old (settle-bound) date's last weekday
        # settles the 21st and the loss is denied.
        late = self.ROWS + [_stx("BUYSELL", "2026-09-18", "2026-09-21",
                                 "DL.TO", -100, 42.0)]
        self.assertGreater(_disallowed(late), 0.0)


class TestSplitRenameClassMatching(unittest.TestCase):
    """2026-09 audit: the radar keyed losses/triggers by RAW ticker
    while the engine matches on the SPLIT-rename class (alias_of). A
    loss on OLD.TO, a rename to NEW.TO, then a NEW.TO rebuy showed
    COOLING + EXITABLE; the engine denies the loss."""

    ROWS = [
        _stx("BUYSELL", "2026-06-01", "2026-06-02", "OLD.TO", 100, 50.0),
        _stx("BUYSELL", "2026-08-24", "2026-08-25", "OLD.TO", -100, 40.0),
        {"action": "SPLIT", "date": "2026-08-27", "time": "00:00:00",
         "date_settle": "2026-08-27", "symbol": "OLD.TO",
         "symbol_new": "NEW.TO", "quantity": 1.0, "currency": "CAD",
         "account": "margin", "description": "rename"},
        _stx("BUYSELL", "2026-09-01", "2026-09-02", "NEW.TO", 100, 41.0),
    ]

    def test_rebuy_under_the_new_name_is_a_violation(self):
        self.assertGreater(_disallowed(self.ROWS), 0.0)   # engine premise
        rows = _run_json(self.ROWS, "2026-09-10")
        cats = {t: r["category"] for t, r in rows.items()}
        self.assertEqual(cats.get("NEW.TO"), "VIOLATION", cats)
        self.assertNotIn("COOLING", cats.values())
        self.assertNotIn("EXITABLE", cats.values())
        # loss settle 08-25 + 30 = 09-24 (Thu); last T+1 trade = 09-23.
        self.assertEqual(rows["NEW.TO"]["clears_at"], "2026-09-23")


if __name__ == "__main__":
    unittest.main()
