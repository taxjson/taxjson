"""Tests for self-describing dividend / withholding-tax records.

Two pieces:

  1. `_parse_div_qty_rate` must extract the per-share rate from IB's
     no-"per Share" dividend descriptions ("Cash Dividend CAD 0.97"),
     not just the "USD 0.555 per Share" form. Without this an IB
     Canadian dividend lands with quantity=0 / price=0.

  2. `taxjson-merge2` reconciles each DIVIDEND with its withholding TAX
     post-dedup: the DIVIDEND's net_amount becomes gross minus tax, and
     the TAX row inherits the dividend's share count + a per-share rate.
     Reconciliation must run after dedup (it can't be done per-file in
     the parser — IB splits a dividend and its withholding, and even
     withholding *reversals*, across overlapping statement files, so a
     per-file pairing double-counts). It must also keep a regular
     dividend and a same-day Payment-in-Lieu separate.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import _parse_div_qty_rate

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestParseDivQtyRate(unittest.TestCase):
    def test_cash_dividend_no_per_share(self):
        # IB Canadian form — rate has no "per Share" suffix.
        qty, rate = _parse_div_qty_rate(
            "ENB (CA29250N1050) Cash Dividend CAD 0.97 (Ordinary Dividend)",
            873.0)
        self.assertEqual(rate, 0.97)
        self.assertEqual(qty, 900.0)

    def test_per_share_form_still_works(self):
        qty, rate = _parse_div_qty_rate(
            "EMR(US2910111044) Cash Dividend USD 0.555 per Share (Ordinary Dividend)",
            55.5)
        self.assertEqual(rate, 0.555)
        self.assertEqual(qty, 100.0)

    def test_pil_has_no_rate(self):
        # Payment-in-Lieu rows carry no per-share figure.
        qty, rate = _parse_div_qty_rate(
            "PPL(CA7063271034) Payment in Lieu of Dividend (Ordinary Dividend)",
            84.14)
        self.assertEqual((qty, rate), (0.0, 0.0))


def _run_merge2(*files):
    cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_merge2', '--sort', '--dedup', *files]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)['transactions']


def _div(symbol, date, gross, qty, price, desc):
    return {'action': 'DIVIDEND', 'date': date, 'time': '09:30:00',
            'date_settle': date, 'symbol': symbol, 'quantity': qty,
            'price': price, 'currency': 'CAD', 'net_amount': gross,
            'gross_amount': gross, 'type': 'dividend', 'account': 'margin',
            'description': desc}


def _tax(symbol, date, amount, desc):
    return {'action': 'TAX', 'date': date, 'time': '09:30:00',
            'date_settle': date, 'symbol': symbol, 'quantity': 0.0,
            'price': 0.0, 'currency': 'CAD', 'net_amount': amount,
            'gross_amount': 0.0, 'type': 'tax', 'account': 'margin',
            'description': desc}


class TestMerge2DividendReconciliation(unittest.TestCase):
    def _write(self, tmp, name, txs):
        p = Path(tmp) / name
        p.write_text(json.dumps({'transactions': txs}))
        return str(p)

    def test_us_dividend_net_after_withholding(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._write(tmp, 'a.json', [
                _div('EMR.US', '2026-03-10', 75.40785, 100.0, 0.7540785,
                     'EMR(US2910111044) Cash Dividend USD 0.555 per Share (Ordinary Dividend)'),
                _tax('EMR.US', '2026-03-10', 11.317971,
                     'EMR(US2910111044) Cash Dividend USD 0.555 per Share - US Tax'),
            ])
            txs = _run_merge2(f)
            div = next(t for t in txs if t['action'] == 'DIVIDEND')
            tax = next(t for t in txs if t['action'] == 'TAX')
            # net = gross - withholding
            self.assertAlmostEqual(div['net_amount'], 64.089879, places=6)
            self.assertEqual(div['gross_amount'], 75.40785)
            # TAX row inherits the share count + per-share rate.
            self.assertEqual(tax['quantity'], 100.0)
            self.assertAlmostEqual(tax['price'], 0.11317971, places=6)

    def test_canadian_dividend_no_withholding(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._write(tmp, 'a.json', [
                _div('ENB.TO', '2026-03-02', 873.0, 900.0, 0.97,
                     'ENB (CA29250N1050) Cash Dividend CAD 0.97 (Ordinary Dividend)'),
            ])
            div = next(t for t in _run_merge2(f) if t['action'] == 'DIVIDEND')
            # No withholding row → net stays equal to gross.
            self.assertEqual(div['net_amount'], 873.0)
            self.assertEqual(div['gross_amount'], 873.0)

    def test_dividend_and_pil_same_day_stay_separate(self):
        # EDV pays a Cash Dividend and a Payment-in-Lieu on the same date,
        # each with its own withholding. The cash dividend's net must
        # subtract only the cash withholding, not the PIL's.
        with tempfile.TemporaryDirectory() as tmp:
            f = self._write(tmp, 'a.json', [
                _div('EDV.US', '2025-04-03', 117.84, 148.0, 0.7962,
                     'EDV(US9219107094) Cash Dividend USD 0.7938 per Share (Ordinary Dividend)'),
                _tax('EDV.US', '2025-04-03', 17.68,
                     'EDV(US9219107094) Cash Dividend USD 0.7938 per Share - US Tax'),
                _tax('EDV.US', '2025-04-03', 12.62,
                     'EDV(US9219107094) Payment in Lieu of Dividend - US Tax'),
            ])
            txs = _run_merge2(f)
            div = next(t for t in txs if t['action'] == 'DIVIDEND')
            # Only the cash-dividend withholding (17.68) is subtracted.
            self.assertAlmostEqual(div['net_amount'], 117.84 - 17.68, places=6)




class TestDerivedQtySnapsCentRounding(unittest.TestCase):
    """IB's Dividends section reports rate + TOTAL CASH (rounded to
    cents), no share count — deriving qty = amount/rate landed NEAR the
    true count, not on it, and `taxjson divs` showed phantom fractional
    holdings (real rows: 25 sh x 0.271 = 6.775 paid as 6.77 ->
    24.98154982 sh; 137 x 0.475 = 65.075 paid as 65.08 ->
    137.01052632). Snap to the nearest integer when it explains the
    paid amount within IB's half-cent rounding; keep genuine DRIP
    fractions."""

    def test_rounded_down_payment_snaps(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        q, r = _parse_div_qty_rate(
            "O(US7561091049) Cash Dividend USD 0.271 per Share "
            "(Ordinary Dividend)", 6.77)
        self.assertEqual(q, 25.0)
        self.assertEqual(r, 0.271)

    def test_rounded_up_payment_snaps(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        q, _ = _parse_div_qty_rate(
            "XYZ(US0000000002) Cash Dividend USD 0.475 per Share "
            "(Ordinary Dividend)", 65.08)
        self.assertEqual(q, 137.0)

    def test_genuine_fractional_kept(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        # DRIP 24.5 sh x 0.271 = 6.6395 -> paid 6.64: nearest integer
        # (25) would imply 6.775 — off by 13 cents, NOT cent rounding.
        q, _ = _parse_div_qty_rate(
            "X Cash Dividend USD 0.271 per Share", 6.64)
        self.assertNotEqual(q, 25.0)
        self.assertAlmostEqual(q, 24.50184502, places=6)




class TestDerivedRateSnapping(unittest.TestCase):
    """A DERIVED rate must not manufacture precision the reported cash
    can't support. Motivating case: 37 shares paid $20.54 is a dividend
    declared at 0.555, but back-computing gave 0.55513514 — while the
    SAME payment in another account, from a broker whose statement
    states the rate, showed a clean 0.555. The two rows disagreed on
    screen for one economic event."""

    def test_derived_rate_snaps_to_the_declared_value(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        qty, rate = _parse_div_qty_rate(
            "XYZ HOLDINGS INC COMMON STOCK CASH DIV ON 37 SHS "
            "REC 08/10/26 PAY 08/18/26", 20.54)
        self.assertEqual(qty, 37.0)
        self.assertAlmostEqual(rate, 0.555, places=6)
        # Whatever is shown must still explain the paid cash to the
        # cent — that is the whole constraint the snap respects.
        self.assertLessEqual(abs(20.54 - rate * qty), 0.005 + 1e-9)

    def test_genuinely_fine_grained_rate_survives(self):
        # 0.3728 x 150 = 55.92 exactly; no shorter form reproduces it.
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        _qty, rate = _parse_div_qty_rate(
            "OPEN TEXT CORP CASH DIV ON 150 SHS", 55.92)
        self.assertAlmostEqual(rate, 0.3728, places=6)

    def test_stated_rate_is_never_touched(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        qty, rate = _parse_div_qty_rate(
            "LNG(US16411R2085) Cash Dividend USD 0.555 per Share",
            26.09)
        self.assertAlmostEqual(rate, 0.555, places=6)
        self.assertEqual(qty, 47.0)

    def test_snap_never_moves_the_cash_amount(self):
        # Property check across a spread of counts and rates: the
        # snapped rate always reproduces the reported cash.
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        for shares in (7, 31, 100, 263, 3000):
            for true_rate in (0.05, 0.555, 1.63, 0.0375):
                amount = round(shares * true_rate, 2)
                qty, rate = _parse_div_qty_rate(
                    f"X CORP CASH DIV ON {shares} SHS", amount)
                self.assertEqual(qty, float(shares))
                self.assertLessEqual(
                    abs(amount - rate * qty), 0.005 + 1e-9,
                    f"{shares} sh @ {true_rate} -> {rate}")


if __name__ == "__main__":
    unittest.main()


class TestSnapIsNeverWorseThanTheQuotient(unittest.TestCase):
    """The snap must recover a declared rate when the cash can resolve
    it, and otherwise leave the raw quotient alone — at small share
    counts it used to pick a NEIGHBOUR (3 sh of a 0.555 dividend gave
    0.557) and could even degrade an exact quotient (4 sh at 0.0375
    gave 0.037)."""

    def test_grid_stays_consistent_with_the_reported_cash(self):
        # The guarantee the data can actually support: whatever rate is
        # shown must reproduce the paid cash to the cent. At small
        # share counts several rates do (7 sh paying $0.26 fits both
        # 0.037 and the declared 0.0375), and no algorithm can pick
        # between them — the snap takes the shortest, which asserts no
        # precision the cash cannot back.
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        bad = []
        for shares in range(1, 61):
            for declared in (0.05, 0.0375, 0.555, 1.63, 0.3728):
                cash = round(shares * declared, 2)
                if cash == 0:
                    continue
                _q, snapped = _parse_div_qty_rate(
                    f"X CORP CASH DIV ON {shares} SHS", cash)
                if abs(cash - snapped * shares) > 0.005 + 1e-9:
                    bad.append((shares, declared, snapped, cash))
        self.assertEqual(bad, [], f"{len(bad)} rates cannot explain "
                                  f"their own cash")

    def test_snap_never_overshoots_the_precision_it_claims(self):
        # A displayed rate must be right to within one unit of its own
        # last decimal — the failure mode was showing a NEIGHBOUR
        # (0.557 for a 0.555 dividend), which is off by more.
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        for shares in range(1, 61):
            for declared in (0.0375, 0.555, 1.63):
                cash = round(shares * declared, 2)
                if cash == 0:
                    continue
                _q, snapped = _parse_div_qty_rate(
                    f"X CORP CASH DIV ON {shares} SHS", cash)
                txt = f"{snapped:.8f}".rstrip("0")
                places = len(txt.split(".")[1]) or 1
                ulp = 10.0 ** -places
                # Plus the irreducible cash-rounding uncertainty: a
                # half-cent spread over `shares` bounds what ANY
                # algorithm can recover (2 sh gives +/-0.0025 of rate).
                bound = ulp + 0.005 / shares + 1e-9
                self.assertLessEqual(
                    abs(snapped - declared), bound,
                    f"{shares} sh @ {declared} -> {snapped}")

    def test_exact_quotient_is_left_alone(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        # 4 sh x 0.0375 = 0.15 exactly; a shorter form also fits the
        # half-cent tolerance, so the snap must NOT take it.
        _q, rate = _parse_div_qty_rate("X CASH DIV ON 4 SHS", 0.15)
        self.assertAlmostEqual(rate, 0.0375, places=6)

    def test_unambiguous_case_still_snaps(self):
        from taxjson.lib.brokerages.base import _parse_div_qty_rate
        _q, rate = _parse_div_qty_rate("X CASH DIV ON 37 SHS", 20.54)
        self.assertAlmostEqual(rate, 0.555, places=6)
