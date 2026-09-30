"""Canada / USA partition — phase B, engine and inputs.

Each test runs the SAME synthetic book (or project) under both countries
and asserts the rule fires in its own country and not in the other
(tests/tax_rules: @rule / @rule_absent). Findings: ENGINE-02 (futures),
INPUTS-01 (stock dividends), INPUTS-03 (taxjson-brokerage s.90(2)),
SPEC-01/INPUTS-04 (a saved crypto gift), stablecoins, the shared-helper
leaks, US return of capital.

All data is synthetic (fake account numbers only).
"""
import unittest
from decimal import Decimal

from taxjson.lib import country as C
from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx


def _gain_rows(res):
    return [t for t in res["transactions"]
            if t.get("action") not in ("DIVIDEND", "DIVIDEND_IN_LIEU")]


# ------------------------------------------------------------ ENGINE-02
def _fut(date, qty, net, time="10:00:00", symbol="F:ESZ5.US"):
    return tx("BUYSELL", date, symbol, qty, net, time=time)


def _settled(book, country, to=None, rates=None):
    from taxjson.bin.taxjson_convert_currency import (
        process_transactions, reset_fallback_tally)
    reset_fallback_tally()
    return process_transactions(list(book), to or C.home_currency(country),
                                rates or {}, Decimal("1.35"),
                                country=country)


class TestFuturesByCountry(unittest.TestCase):
    """ENGINE-02: the settlement basis is chosen by the country, not the
    base currency; each engine books settlement rows with the right sign;
    a partial close follows the country's lot rule."""

    @rule("CA-FX-04")
    @rule_absent("CA-FX-04", country="usa")
    @rule("US-FUT-01")
    @rule_absent("US-FUT-01", country="canada")
    def test_partial_close_average_vs_fifo(self):
        book = [_fut("2025-01-02", 1, 1000), _fut("2025-01-03", 1, 1100),
                _fut("2025-01-06", -1, 1200)]
        out = {}
        for c in C.COUNTRIES:
            rows = _settled(book, c, to="USD")
            out[c] = [round(r.net_amount, 6) for r in rows]
        # Canada: average cost 1050 -> P/L 150; US: FIFO 1000 -> 200.
        self.assertEqual(out["canada"], [0.0, 0.0, 150.0])
        self.assertEqual(out["usa"], [0.0, 0.0, 200.0])
        # And the engines book exactly that P/L.
        for c in C.COUNTRIES:
            r = gains_both(_settled(book, c, to="USD"), year=2025)
            self.assertAlmostEqual(r[c]["summary"]["total_gain"],
                                   150.0 if c == "canada" else 200.0,
                                   places=6)

    @rule("US-FUT-01")
    def test_us_engine_books_a_short_cover_with_the_right_sign(self):
        # A short closed at a +5,000 profit: settlement rows 0 / +5,000.
        rows = _settled([_fut("2025-11-03", -1, 300000),
                         _fut("2025-11-10", 1, 295000)], "usa")
        self.assertEqual([r.net_amount for r in rows], [0.0, 5000.0])
        from taxjson.lib.core import get_tax_rules
        res = get_tax_rules("usa").compute_gains(rows)
        self.assertAlmostEqual(sum(g["gain"] for g in _gain_rows(res)),
                               5000.0, places=6)

    @rule("US-FUT-01")
    def test_non_usd_future_in_a_us_book_has_no_fx_on_notional(self):
        # A CAD future in a USD project: the P/L (+1,000 CAD) at the
        # closing rate (0.72) — not notional x (0.72 - 0.70).
        rates = {"CAD": {"2025-03-03": Decimal("0.70"),
                         "2025-03-10": Decimal("0.72")}}
        book = [tx("BUYSELL", "2025-03-03", "F:SXFH5.TO", 1, 100000,
                   currency="CAD"),
                tx("BUYSELL", "2025-03-10", "F:SXFH5.TO", -1, 101000,
                   currency="CAD")]
        rows = _settled(book, "usa", rates=rates)
        from taxjson.lib.core import get_tax_rules
        res = get_tax_rules("usa").compute_gains(rows)
        self.assertAlmostEqual(sum(g["gain"] for g in _gain_rows(res)),
                               720.0, places=6)

    @rule("US-FUT-02")
    def test_open_contract_is_not_marked_at_year_end(self):
        rows = _settled([_fut("2025-12-01", 1, 300000)], "usa")
        from taxjson.lib.core import get_tax_rules
        res = get_tax_rules("usa").compute_gains(rows)
        self.assertEqual(_gain_rows(res), [])

    def test_no_country_is_refused_for_a_futures_book(self):
        from taxjson.bin.taxjson_convert_currency import process_transactions
        with self.assertRaises(ValueError) as cm:
            process_transactions([_fut("2025-01-02", 1, 1000)], "USD", {},
                                 Decimal("1"))
        self.assertIn("--country", str(cm.exception))
        # A book with no futures still converts without one.
        out = process_transactions(
            [tx("BUYSELL", "2025-01-02", "AAA.US", 1, 10)], "USD", {},
            Decimal("1"))
        self.assertEqual(out[0].net_amount, 10)


if __name__ == "__main__":
    unittest.main()
