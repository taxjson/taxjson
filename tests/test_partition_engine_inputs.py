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


# ------------------------------------------------------------ INPUTS-01
def _usd_rates(path, start="2023-01-01"):
    """A hand-written USD->CAD rates file reaching today (the run keeps
    a fresh one offline)."""
    from datetime import date, timedelta
    d, stop = date.fromisoformat(start), date.today()
    lines = []
    while d <= stop:
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _run_offline(root, home, *args):
    import os
    import subprocess
    import sys
    from tax_rules.dual import SRC
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               HOME=str(home), NO_COLOR="1")
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *args], capture_output=True,
                          text=True, env=env, stdin=subprocess.DEVNULL,
                          timeout=300)


def _stkdiv(date, symbol, qty, **kw):
    return tx("BUYSELL", date, symbol, qty, 0.0, price=0.0,
              type="stock_dividend", **kw)


class TestStockDividend(unittest.TestCase):
    """INPUTS-01: the parsers emit a neutral stock-dividend event; the
    Canada engine books it as a $0 acquisition (unchanged, and it counts
    for s.54), the US engine as §305(a)/§307: the basis is spread over
    old and new shares, the purchase date tacks, and it is not a §1091
    purchase."""

    @rule("CA-STKDIV-01")
    @rule_absent("CA-STKDIV-01", country="usa")
    @rule("US-STKDIV-01")
    @rule_absent("US-STKDIV-01", country="canada")
    def test_new_shares_in_a_later_sale(self):
        book = [tx("BUYSELL", "2023-03-01", "XYZ.US", 100, 5000),
                _stkdiv("2024-06-03", "XYZ.US", 5),
                tx("BUYSELL", "2025-01-15", "XYZ.US", -105, 6300)]
        r = gains_both(book, year=2025)
        us = _gain_rows(r["usa"])
        # US: ONE long-term lot of 105 shares, basis 5000, bought
        # 2023-03-01 — not 100 LT + 5 ST shares at $0.
        self.assertEqual([(g["qty"], g["term"], g["acquired_date"])
                          for g in us], [(105.0, "LONG_TERM", "2023-03-01")])
        self.assertAlmostEqual(us[0]["cost"], 5000.0, places=6)
        self.assertIn("§307", r["usa"]["_stderr"])
        # Canada: the 5 shares joined the pool at $0 (same total here),
        # and the declared amount is left to the user, with a note.
        self.assertAlmostEqual(r["canada"]["summary"]["total_gain"], 1300.0,
                               places=6)
        self.assertIn("declared amount", r["canada"]["_stderr"])
        self.assertNotIn("§307", r["canada"]["_stderr"])

    @rule("CA-STKDIV-01")
    @rule_absent("CA-STKDIV-01", country="usa")
    @rule("US-STKDIV-01")
    @rule_absent("US-STKDIV-01", country="canada")
    def test_stock_dividend_is_a_replacement_only_in_canada(self):
        # A loss 14 days before a 5-share stock dividend, still held.
        book = [tx("BUYSELL", "2024-01-02", "XYZ.US", 200, 10000),
                tx("BUYSELL", "2024-05-20", "XYZ.US", -100, 4000),
                _stkdiv("2024-06-03", "XYZ.US", 5)]
        r = gains_both(book, year=2024)
        # Canada: an acquisition in the window, held at day 30 ->
        # 5/100 of the 1,000 loss is superficial.
        self.assertAlmostEqual(r["canada"]["summary"]["total_disallowed"],
                               50.0, places=6)
        # US: not a purchase -> no wash sale.
        self.assertEqual(r["usa"]["summary"]["total_disallowed"], 0)
        self.assertFalse(any(g.get("is_wash_sale")
                             for g in _gain_rows(r["usa"])))

    @rule("US-STKDIV-01")
    def test_per_account_basis_spreads_only_that_accounts_lots(self):
        from taxjson.lib.core import get_tax_rules
        book = [tx("BUYSELL", "2023-03-01", "XYZ.US", 100, 5000,
                   account="a"),
                tx("BUYSELL", "2023-03-02", "XYZ.US", 100, 7000,
                   account="b"),
                _stkdiv("2024-06-03", "XYZ.US", 10, account="a"),
                tx("BUYSELL", "2025-01-15", "XYZ.US", -110, 6600,
                   account="a")]
        res = get_tax_rules("usa").compute_gains(book,
                                                 per_account_basis=True)
        rows = _gain_rows(res)
        self.assertEqual([(round(g["qty"], 6), g["account"])
                          for g in rows], [(110.0, "a")])
        self.assertAlmostEqual(rows[0]["cost"], 5000.0, places=6)

    @rule("US-STKDIV-02")
    def test_no_shares_held_is_a_warned_zero_cost_purchase(self):
        from taxjson.lib.core import get_tax_rules
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = get_tax_rules("usa").compute_gains(
                [_stkdiv("2024-06-03", "XYZ.US", 5),
                 tx("BUYSELL", "2025-01-15", "XYZ.US", -5, 300)])
        self.assertIn("no shares held", err.getvalue())
        self.assertAlmostEqual(sum(g["gain"] for g in _gain_rows(res)),
                               300.0, places=6)

    def test_parsers_emit_the_neutral_event_without_country_advice(self):
        import contextlib
        import io
        from test_fix_ibparse import CA_H, HEAD, _ca, _parse_ib
        body = (HEAD + CA_H + _ca(
            'QZSD(US9990000999) Stock Dividend US9990000999 1 for 20 '
            '(QZSD, QZSD CORP, US9990000999)', 5, value=100))
        _, txs, err = _parse_ib(body)
        self.assertEqual([(t["action"], t["quantity"], t["net_amount"],
                           t.get("type")) for t in txs],
                         [("BUYSELL", 5.0, 0.0, "stock_dividend")])
        for law in ("ACB", "income", "§", "ITA"):
            self.assertNotIn(law, err)
        from test_parser_activity_coverage import (QUESTRADE_HEADER,
                                                   _parse_csv)
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        csv = QUESTRADE_HEADER + (
            '2026-06-26 12:00:00 AM,2026-06-26 12:00:00 AM,DIS,QSC,'
            'QUILL SPLIT CORP SHS CL A NEW STK DIV ON 1200 SHS REC '
            '06/19/26 PAY 06/26/26,180.0,0.0,0.0,0.0,0.0,CAD,1,'
            'Dividends,Individual\n')
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            qt = _parse_csv(QuestradeBrokerage, csv)
        self.assertEqual([t.get("type") for t in qt], ["stock_dividend"])
        self.assertNotIn("ACB", buf.getvalue())

    @rule("CA-STKDIV-01")
    @rule_absent("CA-STKDIV-01", country="usa")
    @rule("US-STKDIV-01")
    @rule_absent("US-STKDIV-01", country="canada")
    def test_through_taxjson_run(self):
        import json
        import tempfile
        from pathlib import Path
        from tax_rules.dual import projects_both
        from test_fix_ibparse import CA_H, HEAD, TRADES_H, _ca, _trade
        csv = (HEAD + TRADES_H
               + _trade('XYZ', '2023-03-01, 10:00:00', 100, 50, -5000)
               + _trade('XYZ', '2025-01-15, 10:00:00', -105, 60, 6300,
                        code='C')
               + CA_H + _ca('XYZ(US9990000999) Stock Dividend '
                            'US9990000999 5 for 100 (XYZ, XYZ CORP, '
                            'US9990000999)', 5, value=250,
                            when='2024-06-03, 20:25:00'))
        with tempfile.TemporaryDirectory() as td:
            ps = projects_both(Path(td), year=2025,
                               accounts='[accounts.ib]\ntype = "taxable"\n',
                               files={"inputs/ib/ib.csv": csv},
                               canada={"source_currencies": ["USD"],
                                       "option_grant_timing_since": 2025})
            _usd_rates(ps["canada"] / "work" / "to_base.csv")
            rows = {}
            for c, root in ps.items():
                r = _run_offline(root, td, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                g = json.loads((root / "work" / "ib_gains.json").read_text())
                rows[c] = _gain_rows(g)
        self.assertEqual([(g["qty"], g["term"], g["acquired_date"],
                           round(g["cost"], 2)) for g in rows["usa"]],
                         [(105.0, "LONG_TERM", "2023-03-01", 5000.0)])
        # Canada: the $0 acquisition joins the pool (ACB 5000 x 1.35).
        self.assertEqual([(g["qty"], g["term"]) for g in rows["canada"]],
                         [(105.0, None)])
        self.assertAlmostEqual(rows["canada"][0]["cost"], 6750.0, places=2)


if __name__ == "__main__":
    unittest.main()
