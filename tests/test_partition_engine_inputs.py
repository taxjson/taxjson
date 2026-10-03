"""Canada / USA partition — phase B, engine and inputs.

Each test runs the SAME synthetic book (or project) under both countries
and asserts the rule fires in its own country and not in the other
(tests/tax_rules: @rule / @rule_absent). Findings: ENGINE-02 (futures),
INPUTS-01 (stock dividends), INPUTS-03 (taxjson-brokerage s.90(1)),
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
        # The §307 note belongs to the dividend's year, not every later
        # year's .sum (audit A2-0956).
        self.assertNotIn("§307", r["usa"]["_stderr"])
        self.assertIn("§307",
                      gains_both(book, year=2024)["usa"]["_stderr"])
        # Canada: the 5 shares joined the pool at $0 (same total here),
        # and the declared amount is left to the user, with a note in
        # the dividend's year only, like the US note (re-audit A2-0711).
        self.assertAlmostEqual(r["canada"]["summary"]["total_gain"], 1300.0,
                               places=6)
        self.assertNotIn("declared amount", r["canada"]["_stderr"])
        r24 = gains_both(book, year=2024)
        self.assertIn("declared amount", r24["canada"]["_stderr"])
        # The Canadian declared-amount note never reaches a US run
        # (A2-0857, A2-1488).
        self.assertNotIn("declared amount", r24["usa"]["_stderr"])
        self.assertNotIn("distributions", r24["usa"]["_stderr"])
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

    @rule("US-STKDIV-03")
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


# ------------------------------------------------------------ INPUTS-03
class TestStandaloneBrokerageRoc(unittest.TestCase):
    """INPUTS-03 / SPEC-35: standalone taxjson-brokerage no longer
    applies ITA s.90(1) without a country."""

    BODY = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Dividends,Header,Currency,Account,Date,Description,Amount\n'
            'Dividends,Data,USD,U5550001,2026-06-30,'  # pii-ok
            'QZRX(US0000000017) Return of Capital USD 0.12 per Share,'
            '24.00\n')

    def _brokerage(self, *flags):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from tax_rules.dual import SRC
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ib.csv"
            p.write_text(self.BODY, encoding="utf-8")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "ib", *flags, str(p)],
                capture_output=True, text=True,
                env=dict(os.environ, PYTHONPATH=str(SRC)))
        txs = []
        if r.returncode == 0:
            data = json.loads(r.stdout)
            txs = data["transactions"] if isinstance(data, dict) else data
        return r, [t["action"] for t in txs]

    @rule("CA-ACB-08")
    @rule_absent("CA-ACB-08", country="usa")
    def test_country_picks_the_rule_and_none_is_not_canada(self):
        r, acts = self._brokerage("--country", "canada")
        self.assertEqual((r.returncode, acts), (0, ["DIVIDEND"]), r.stderr)
        r, acts = self._brokerage("--country", "usa")
        self.assertEqual((r.returncode, acts), (0, ["ADJUST"]), r.stderr)
        # No country: the neutral cost reduction, with a note.
        r, acts = self._brokerage()
        self.assertEqual((r.returncode, acts), (0, ["ADJUST"]), r.stderr)
        self.assertIn("--country canada", r.stderr)
        # s.90(1) asked for with --country usa: refused.
        r, _ = self._brokerage("--country", "usa", "--foreign-roc",
                               "dividend")
        self.assertEqual(r.returncode, 2)
        self.assertIn("s.90(1)", r.stderr)


# ------------------------------------------------------ SPEC-01 / INPUTS-04
class TestSavedCryptoGift(unittest.TestCase):
    """A `gift` already saved in sends.json (carried over from a Canada
    project, copied, hand-edited) is booked as a sale in Canada and
    refused at WRITE time in a US project — not only at --set."""

    @rule("CA-CRYPTO-05")
    @rule_absent("CA-CRYPTO-05", country="usa")
    @rule("US-SEND-02")
    @rule_absent("US-SEND-02", country="canada")
    def test_saved_gift_by_country(self):
        import json
        import tempfile
        from pathlib import Path
        from test_fix_sends import (TAO_ID, _cad_usd_rates_file, _cli,
                                    _project)
        out = {}
        for c in C.COUNTRIES:
            with tempfile.TemporaryDirectory() as td:
                root, home = _project(td, country=c)
                if c == "usa":
                    (root / "taxjson.toml").write_text(
                        '[settings]\nyear = 2026\ncountry = "usa"\n'
                        'base_currency = "USD"\n'
                        'source_currencies = ["CAD"]\n'
                        '[accounts.crypto]\ntype = "taxable"\n'
                        'crypto = true\n')
                    _cad_usd_rates_file(root / "work" / "to_base.csv")
                    (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
                r = _cli(root, home, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                # The decision as a carried-over / hand-edited manifest.
                man = root / "inputs" / "crypto" / "sends.json"
                man.write_text(json.dumps(
                    {"sends": {TAO_ID: {"decision": "gift"}}}))
                w = _cli(root, home, "crypto-sends", "crypto", "--write")
                tt = root / "inputs" / "crypto" / "crypto_sends.tt"
                body = tt.read_text() if tt.exists() else ""
                run = _cli(root, home, "run", "--no-input")
                chk = _cli(root, home, "crypto-sends", "crypto")
                out[c] = (w, body, run, chk)
        w, body, run, _ = out["canada"]
        self.assertEqual(w.returncode, 0, w.stderr)
        self.assertIn("TAO -0.1 CAD", body)
        self.assertIn("ITA s.69(1)(b)", body)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        w, body, run, chk = out["usa"]
        self.assertNotEqual(w.returncode, 0)
        self.assertIn("not a sale for a US donor", w.stderr)
        self.assertNotIn("TAO -0.1", body)
        self.assertNotIn("s.69", body)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn(TAO_ID, run.stderr)
        self.assertIn("REFUSED", chk.stdout)


class TestCryptoSendBookedTwice(unittest.TestCase):
    """A send booked by a hand-written .tt AND the generated
    crypto_sends.tt is counted twice: the run and `crypto-sends` warn
    with both file names and the timestamp, and delete nothing."""

    def test_warns_with_both_files_and_keeps_them(self):
        import tempfile
        from test_fix_sends import TAO_ID, _cli, _project
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            _cli(root, home, "crypto-sends", "crypto", "--set",
                 f"{TAO_ID}=payment")
            mine = root / "inputs" / "crypto" / "tao_payment_2026.tt"
            line = ("BUYSELL 2026-05-04 18:50:14 TAO -0.1 CAD 387.813 "
                    "38.78 0\n")
            mine.write_text(line)
            run = _cli(root, home, "run", "--no-input")
            lst = _cli(root, home, "crypto-sends", "crypto")
            for text in (run.stderr, lst.stdout):
                self.assertIn("inputs/crypto/tao_payment_2026.tt line 1",
                              text)
                self.assertIn("inputs/crypto/crypto_sends.tt", text)
                self.assertIn("2026-05-04 18:50:14", text)
                self.assertIn("counted twice", text)
            # Nothing deleted.
            self.assertEqual(mine.read_text(), line)
            self.assertIn("TAO -0.1", (root / "inputs" / "crypto" /
                                       "crypto_sends.tt").read_text())


# ---------------------------------------- ENGINE-04/05, INPUTS-11, SPEC-34
class TestManualLossWarningsByCountry(unittest.TestCase):
    """The phantom-basis loss warnings measure the window on the
    country's own dates and name its rule: settle dates and s.54 in
    Canada, trade dates and §1091 in the US; none where the US rule does
    not apply (crypto)."""

    def _run(self, book, **kw):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            ph = Path(td) / "missing_history.json"
            ph.write_text(json.dumps([{"symbol": "NNN.US",
                                       "account": "margin"}]))
            return gains_both(book, year=2025, incomplete_history=ph, **kw)

    # Not a @rule_absent pair (A2-0830): US FIFO never reaches the
    # partial-taint path, so this book cannot show the US date basis;
    # test_cross_year_window_dates is the pair.
    @rule("CA-ACB-12")
    @rule("US-BASIS-04")
    def test_partial_taint_window_dates(self):
        # A phantom-basis loss that settles 03-07 (traded 03-03) and a
        # rebuy that settles 04-04 (traded 04-03): 28 settle days, 31
        # trade days.
        settle_near = [
            tx("BUYSELL", "2025-01-02", "NNN.US", 100, 2000,
               settle="2025-01-03"),
            tx("BUYSELL", "2025-03-03", "NNN.US", -150, 1500,
               settle="2025-03-07"),
            tx("BUYSELL", "2025-04-03", "NNN.US", 100, 1000,
               settle="2025-04-04")]
        def rebuy_warnings(res):
            # The partial-taint warnings (a phantom-basis loss with a
            # purchase in the window); the cross-year ones have no
            # acquisition_date.
            return [w for w in res.get("superficial_loss_warnings") or []
                    if w.get("acquisition_date")]
        r = self._run(settle_near)
        self.assertTrue(rebuy_warnings(r["canada"]))
        self.assertIn("superficial-loss", r["canada"]["_stderr"])
        self.assertFalse(rebuy_warnings(r["usa"]))
        # US FIFO takes the $0 phantom lot first, so its phantom-basis
        # leg is a gain (never a rebuy warning); the clean loss beside it
        # is flagged in §1091 terms, never the CRA's.
        us = r["usa"].get("superficial_loss_warnings")
        self.assertTrue(us)
        self.assertIn("§1091", us[0]["message"])
        self.assertIn("wash-sale check", r["usa"]["_stderr"])
        self.assertNotIn("superficial", r["usa"]["_stderr"])
        # The trade-date twin: 29 trade days, 34 settle days.
        trade_near = [
            tx("BUYSELL", "2025-01-02", "NNN.US", 100, 2000,
               settle="2025-01-03"),
            tx("BUYSELL", "2025-03-03", "NNN.US", -150, 1500,
               settle="2025-03-04"),
            tx("BUYSELL", "2025-04-01", "NNN.US", 100, 1000,
               settle="2025-04-07")]
        r = self._run(trade_near)
        self.assertFalse(rebuy_warnings(r["canada"]))
        self.assertNotIn("superficial", r["usa"]["_stderr"])

    @rule("CA-ACB-12")
    @rule("US-BASIS-04")
    def test_thirty_days_is_the_edge(self):
        # A2-1497: day 30 is inside the window, day 31 is not — on settle
        # dates for Canada (both the engine's partial-taint path and the
        # cross-year detector), on trade dates for the US.
        from taxjson.lib.missing_history import (
            detect_superficial_loss_warnings)

        def partial(rebuy_settle):
            book = [tx("BUYSELL", "2025-01-02", "NNN.US", 100, 2000,
                       settle="2025-01-03"),
                    tx("BUYSELL", "2025-03-03", "NNN.US", -150, 1500,
                       settle="2025-03-04"),
                    tx("BUYSELL", rebuy_settle, "NNN.US", 100, 1000,
                       settle=rebuy_settle)]
            return [w for w in self._run(book)["canada"].get(
                        "superficial_loss_warnings") or []
                    if w.get("acquisition_date")]
        self.assertTrue(partial("2025-04-03"))
        self.assertFalse(partial("2025-04-04"))
        for c, key, day30, day31 in (
                ("canada", "date_settle", "2025-04-03", "2025-04-04"),
                ("usa", "date", "2025-04-02", "2025-04-03")):
            tainted = [{"date": "2025-03-03", "date_settle": "2025-03-04",
                        "symbol": "MMM.US", "qty": -50}]
            for d, want in ((day30, 1), (day31, 0)):
                loss = {"date": "2025-01-01", "date_settle": "2025-01-01",
                        "symbol": "MMM.US", "gain": -200.0}
                loss[key] = d
                got = detect_superficial_loss_warnings([loss], tainted,
                                                       country=c)
                self.assertEqual(len(got), want, (c, d))

    @rule("CA-ACB-12")
    @rule_absent("CA-ACB-12", country="usa")
    @rule("US-BASIS-04")
    @rule_absent("US-BASIS-04", country="canada")
    def test_cross_year_window_dates(self):
        from taxjson.lib.missing_history import (
            detect_superficial_loss_warnings)
        loss = [{"date": "2025-04-06", "date_settle": "2025-04-03",
                 "symbol": "MMM.US", "gain": -200.0}]
        tainted = [{"date": "2025-03-03", "date_settle": "2025-03-04",
                    "symbol": "MMM.US", "qty": -50}]
        ca = detect_superficial_loss_warnings(loss, tainted,
                                              country="canada")
        us = detect_superficial_loss_warnings(loss, tainted, country="usa")
        # 30 settle days: Canada warns; 34 trade days: the US does not.
        self.assertEqual(len(ca), 1)
        self.assertIn("s.54", ca[0]["message"])
        self.assertEqual(us, [])
        loss[0].update(date="2025-04-02", date_settle="2025-04-07")
        self.assertEqual(detect_superficial_loss_warnings(
            loss, tainted, country="canada"), [])
        us = detect_superficial_loss_warnings(loss, tainted, country="usa")
        self.assertEqual(len(us), 1)
        self.assertIn("§1091", us[0]["message"])

    @rule("US-BASIS-04")
    def test_no_wash_check_without_the_wash_rule(self):
        # A clean loss next to a phantom-basis sale: a manual wash-sale
        # check — except where the rule does not apply (US crypto: wash
        # detection off).
        book = [tx("BUYSELL", "2025-01-02", "NNN.US", 100, 2000),
                tx("BUYSELL", "2025-03-03", "NNN.US", -150, 1500)]
        import json
        import tempfile
        from pathlib import Path
        from taxjson.lib.pipeline import GainsRequest, run_gains
        with tempfile.TemporaryDirectory() as td:
            ph = Path(td) / "missing_history.json"
            ph.write_text(json.dumps([{"symbol": "NNN.US",
                                       "account": "margin"}]))
            res = {nw: run_gains(list(book), req=GainsRequest(
                country="usa", taxable=True, year=2025, no_wash=nw,
                incomplete_history=ph)) for nw in (False, True)}
        self.assertTrue(res[False].get("superficial_loss_warnings"))
        self.assertFalse(res[True].get("superficial_loss_warnings"))


# ------------------------------------------------ ENGINE-08, -09, -14
class TestSharedHelpersSpeakTheCountry(unittest.TestCase):
    """Shared helpers used to carry one country's law or words."""

    @rule("CA-SL-14")
    @rule_absent("CA-SL-14", country="usa")
    @rule("US-WASH-14")
    @rule_absent("US-WASH-14", country="canada")
    def test_warrant_warning_wording(self):
        book = [tx("BUYSELL", "2025-01-02", "KKK.US", 100, 1000),
                tx("BUYSELL", "2025-02-03", "KKK.US", -100, 800),
                tx("BUYSELL", "2025-02-10", "KKK.WS.US", 10, 20)]
        r = gains_both(book)
        ca, us = r["canada"]["_stderr"], r["usa"]["_stderr"]
        self.assertIn("right_vs_share_loss", ca)
        self.assertIn("superficial", ca)
        self.assertNotIn("§1091", ca)
        self.assertIn("right_vs_share_loss", us)
        self.assertIn("wash sale", us)
        self.assertNotIn("superficial", us)
        # Warn-only in both: nothing denied.
        for c in C.COUNTRIES:
            self.assertEqual(r[c]["summary"]["total_disallowed"], 0)

    def test_us_spinoff_never_books_a_canadian_cad_allocation(self):
        from taxjson.lib import corp_actions as ca
        ev = ca.CorporateAction(
            date="2025-06-02", time="09:30:00", action_type="spinoff",
            source_symbol="PPP.US", source_isin="", target_symbol="QQQ.US",
            target_isin="", ratio_new=1, ratio_old=10, qty_disposed=100,
            qty_received=10, currency="USD", fmv=0.0, target_currency="USD",
            account="margin")
        us = ca.RULES_BY_COUNTRY["usa"]["spinoff"].apply(
            ev, "tax_free_355", {"allocated_acb_cad": 100.0})
        self.assertTrue(all(r.get("currency") != "CAD" for r in us), us)
        self.assertFalse(any(float(r.get("net_amount") or 0) for r in us))
        can = ca.RULES_BY_COUNTRY["canada"]["spinoff"].apply(
            ev, "rollover_s_86_1", {"allocated_acb_cad": 100.0})
        self.assertEqual({r["currency"] for r in can}, {"CAD"})
        self.assertAlmostEqual(sum(float(r["net_amount"]) for r in can
                                   if r["action"] == "BUYSELL"), 100.0)

    def test_registered_label_fallback_knows_each_countrys_plans(self):
        from taxjson.lib.missing_history import is_registered_account
        self.assertTrue(is_registered_account("ROTH_IRA", country="usa"))
        self.assertTrue(is_registered_account("IRA-2", country="usa"))
        self.assertFalse(is_registered_account("MIRAGE", country="usa"))
        self.assertFalse(is_registered_account("ROTH_IRA",
                                               country="canada"))
        self.assertTrue(is_registered_account("RRSP", country="canada"))
        self.assertFalse(is_registered_account("RRSP", country="usa"))
        # Country unknown (a standalone tool outside a project): either.
        self.assertTrue(is_registered_account("ROTH_IRA"))
        self.assertTrue(is_registered_account("TFSA"))


# ------------------------------------------------------ INPUTS-10, SPEC-32
class TestRecordDateHolder(unittest.TestCase):
    """The holder of record is the settled position in both countries:
    a US project's trade tax_date used to credit a buy traded ON the
    record date."""

    @rule("CA-DIST-01")
    @rule("US-DIST-01")
    def test_same_adjust_in_both_countries(self):
        import json
        import tempfile
        from pathlib import Path
        from tax_rules.dual import projects_both
        from test_fix_ibparse import HEAD, TRADES_H, _trade
        csv = (HEAD + TRADES_H
               + _trade('XYZ', '2025-06-02, 10:00:00', 100, 50, -5000)
               # Traded on the record date, settles the next day.
               + _trade('XYZ', '2025-12-29, 10:00:00', 50, 50, -2500))
        with tempfile.TemporaryDirectory() as td:
            ps = projects_both(Path(td), year=2025,
                               accounts='[accounts.ib]\ntype = "taxable"\n',
                               files={"inputs/ib/ib.csv": csv},
                               tail='\n[[distributions]]\nsymbol = "XYZ.US"\n'
                                    'record_date = 2025-12-29\n'
                                    'per_share = 0.50\n',
                               canada={"source_currencies": ["USD"],
                                       "option_grant_timing_since": 2025})
            _usd_rates(ps["canada"] / "work" / "to_base.csv")
            adj, err = {}, {}
            for c, root in ps.items():
                r = _run_offline(root, td, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                base = json.loads((root / "work" / "ib_base.json")
                                  .read_text())
                adj[c] = [t["net_amount"] for t in base["transactions"]
                          if t["action"] == "ADJUST"]
                err[c] = "".join(p.read_text() for p in
                                 (root / "work").glob("*.diag")) + r.stderr
        # 100 settled shares x 0.50 — the 50 bought on the record date
        # are not the holder's yet, in either country.
        self.assertEqual(adj["canada"], [50.0])
        self.assertEqual(adj["usa"], [50.0])
        # The income note names the country's slip.
        import contextlib
        import io
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        book = {"transactions": [dict(action="BUYSELL", date="2025-06-02",
                                      date_settle="2025-06-03",
                                      symbol="XYZ.US", quantity=100.0)]}
        notes = {}
        for c in C.COUNTRIES:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                apply_distributions(json.loads(json.dumps(book)),
                                    [("XYZ.US", "2025-12-29", 0.5)], "ib",
                                    country=c)
            notes[c] = buf.getvalue()
        self.assertIn("T3/T5", notes["canada"])
        self.assertIn("1099-DIV", notes["usa"])
        self.assertNotIn("T3", notes["usa"])
        self.assertNotIn("ACB", notes["usa"])


# ------------------------------------------------ INPUTS-09, INPUTS-14(c)
class TestDatingSettings(unittest.TestCase):
    """Crypto local time is a project setting (both countries), and
    convert-tt writes the project's own tax_date, never a silent
    Canadian settle date."""

    @rule("CA-DATE-12", "CA-DATE-07")
    @rule("US-DATE-11", "US-DATE-07")
    def test_local_timezone_setting_dates_crypto_rows(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        from taxjson.bin import taxjson_run as run
        from tax_rules.dual import settings_for
        from test_crypto_parse_hardening import _KT_H, _kraken_dir, _run
        csv = _KT_H + ("T1,O1,SOL/USD,2026-01-01 05:30:00.1,buy,limit,"
                       "200,2000,5,10,,,\n")
        for c in C.COUNTRIES:
            with tempfile.TemporaryDirectory() as td, \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TAXJSON_LOCAL_TZ", None)
                root = Path(td)
                (root / "taxjson.toml").write_text(
                    settings_for(c, year=2025,
                                 local_timezone="America/Los_Angeles")
                    + '[accounts.crypto]\ntype = "taxable"\n'
                      'crypto = true\n')
                run.load_config(root)
                self.assertEqual(os.environ.get("TAXJSON_LOCAL_TZ"),
                                 "America/Los_Angeles")
                ktd, K = _kraken_dir({"kr_trades.csv": csv})
                with ktd:
                    txs, _ = _run(K().parse_file,
                                  Path(ktd.name) / "kr_trades.csv")
                # 05:30 UTC on Jan 1 is Dec 31 in Los Angeles: tax year
                # 2025, not 2026 (Toronto: 00:30 Jan 1).
                self.assertEqual((txs[0]["date"], txs[0]["time"]),
                                 ("2025-12-31", "21:30:00"), c)
                self.assertEqual(txs[0]["date_settle"], txs[0]["date"])
        from taxjson.lib.config_check import settings_problems
        bad = {"settings": {"country": "usa", "local_timezone": "Mars/Base"}}
        self.assertTrue(any("local_timezone" in p
                            for p in settings_problems(bad)))
        from taxjson.lib import tax_logic as TL
        for c in C.COUNTRIES:
            text = TL.render(c, {"local_timezone": "America/Denver"})
            self.assertIn("America/Denver", text)

    @rule("CA-DATE-13")
    @rule("US-DATE-10")
    def test_convert_tt_writes_the_projects_tax_date(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from tax_rules.dual import SRC, projects_both
        book = {"transactions": [dict(
            action="BUYSELL", date="2025-12-31", time="10:00:00",
            date_settle="2026-01-02", symbol="AAA.US", quantity=-10.0,
            price=10.0, net_amount=100.0, currency="USD")]}

        def convert(path, *flags):
            return subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_convert_tt",
                 str(path), *flags], capture_output=True, text=True,
                env=dict(os.environ, PYTHONPATH=str(SRC)))
        with tempfile.TemporaryDirectory() as td:
            ps = projects_both(Path(td), files={
                "work/margin_base.json": json.dumps(book)})
            out = {c: convert(p / "work" / "margin_base.json")
                   for c, p in ps.items()}
            for c, r in out.items():
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("BUYSELL 2026-01-02 ", out["canada"].stdout)
            self.assertIn("BUYSELL 2025-12-31 ", out["usa"].stdout)
            loose = Path(td) / "loose.json"
            loose.write_text(json.dumps(book))
            r = convert(loose)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--date-basis", r.stderr)
            r = convert(loose, "--date-basis", "trade")
            self.assertIn("BUYSELL 2025-12-31 ", r.stdout)


# ------------------------------------------ SPEC-14/18/20: shared rules
class TestRulesBothCountriesState(unittest.TestCase):
    """Rules the US section did not state although US projects run them
    (partition SPEC-14, SPEC-18, SPEC-20, INPUTS-12/13)."""

    @rule("US-CORP-01", "US-CORP-02")
    def test_us_split_and_rename_keep_basis_and_dates(self):
        from taxjson.lib.core import get_tax_rules
        book = [tx("BUYSELL", "2023-03-01", "OLD.US", 100, 5000),
                tx("SPLIT", "2024-02-01", "OLD.US", 2, 0),
                tx("SPLIT", "2024-06-03", "OLD.US", 1, 0,
                   symbol_new="NEW.US"),
                tx("BUYSELL", "2025-01-15", "NEW.US", -200, 6000)]
        res = get_tax_rules("usa").compute_gains(book)
        rows = _gain_rows(res)
        self.assertEqual([(g["symbol"], round(g["qty"], 6), g["term"],
                           g["acquired_date"]) for g in rows],
                         [("NEW.US", 200.0, "LONG_TERM", "2023-03-01")])
        self.assertAlmostEqual(rows[0]["cost"], 5000.0, places=6)

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    @rule("US-CRYPTO-02")
    @rule_absent("US-CRYPTO-02", country="canada")
    def test_stablecoins_are_cash_in_canada_property_in_the_us(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from tax_rules.dual import SRC
        from test_fix_sends import CB_CSV
        from test_crypto_parse_hardening import _KT_H
        head = CB_CSV.splitlines()[0]
        cb = (head + "\n"
              "d0,2023-03-01 12:00:00 UTC,Buy,USDC,1000,USD,1.00,1000,"
              "1000,0,Bought 1000 USDC\n"
              "d1,2023-03-11 12:00:00 UTC,Sell,USDC,1000,USD,0.88,880,"
              "880,0,Sold 1000 USDC\n")
        kr = _KT_H + ("T1,O1,USDC/USD,2023-03-01 12:00:00.1,buy,limit,"
                      "1.00,500,0,500,,,\n"
                      "T2,O2,USDC/USD,2023-03-11 12:00:00.1,sell,limit,"
                      "0.88,440,0,500,,,\n"
                      "T3,O3,SOL/USDC,2023-04-03 12:00:00.1,buy,limit,"
                      "20,200,0,10,,,\n")

        def parse(broker, name, text, country):
            with tempfile.TemporaryDirectory() as td:
                f = Path(td) / name
                f.write_text(text)
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                     "--brokerage", broker, "--country", country, str(f)],
                    capture_output=True, text=True,
                    env=dict(os.environ, PYTHONPATH=str(SRC),
                             TAXJSON_LOCAL_TZ="America/Toronto"))
            self.assertEqual(r.returncode, 0, r.stderr)
            d = json.loads(r.stdout)
            return (d["transactions"] if isinstance(d, dict) else d,
                    r.stderr)
        from taxjson.lib.core import TaxTransaction, get_tax_rules
        for broker, name, text, units in (
                ("coinbase", "cb.csv", cb, 1000), ("kraken", "kr_trades.csv",
                                                   kr, 500)):
            ca, ca_err = parse(broker, name, text, "canada")
            us, us_err = parse(broker, name, text, "usa")
            # Canada: US-dollar cash — no USDC position, the de-peg said.
            self.assertEqual([t for t in ca if t["symbol"] == "USDC"], [],
                             broker)
            self.assertIn("de-peg", ca_err, broker)
            # US: property — bought, then sold at 0.88: a loss.
            usdc = [t for t in us if t["symbol"] == "USDC"
                    and t["action"] == "BUYSELL"]
            self.assertGreaterEqual(len(usdc), 2, broker)
            self.assertNotIn("de-peg", us_err, broker)
            rows = [TaxTransaction(**{k: v for k, v in t.items()
                                      if k in TaxTransaction.__dataclass_fields__})
                    for t in us if t["action"] == "BUYSELL"]
            res = get_tax_rules("usa").compute_gains(rows)
            loss = [g for g in _gain_rows(res) if g["symbol"] == "USDC"
                    and g["date"].startswith("2023-03-11")]
            self.assertAlmostEqual(sum(g["gain"] for g in loss),
                                   -0.12 * units, places=2, msg=broker)
            if broker == "kraken":
                # A SOL buy paid in USDC: both legs at the USDC par.
                sol = [t for t in us if t["symbol"] == "SOL"]
                spent = [t for t in us if t["symbol"] == "USDC"
                         and t["date"] == "2023-04-03"]
                self.assertEqual([round(t["net_amount"], 2) for t in sol],
                                 [200.0])
                self.assertEqual([(t["quantity"], round(t["net_amount"], 2))
                                  for t in spent], [(-200.0, 200.0)])

    @rule("US-CRYPTO-02")
    def test_us_stablecoin_reward_and_convert_are_property(self):
        from test_parser_coverage_audit import KR_LEDGER_H, _parse
        from taxjson.lib.brokerages.kraken import KrakenBrokerage

        class UsKraken(KrakenBrokerage):
            stablecoins_as_cash = False
        csv = KR_LEDGER_H + (
            '"L1","","2026-01-15 10:00:00","earn","reward","currency",'
            '"USDC","earn","12.5","0","112.5"\n')
        _, txs, _ = _parse(UsKraken, csv, prefix="kr_ledgers_")
        self.assertEqual(sorted((t["action"], t["symbol"],
                                 round(t["net_amount"], 2)) for t in txs),
                         [("BUYSELL", "USDC", 12.5),
                          ("DIVIDEND", "USDC", 12.5)])

    @rule("CA-CRYPTO-08")
    def test_kraken_pyusd_is_a_cash_send(self):
        # SPEC-26 pinned PYUSD/GUSD as coins on Kraken; the owner decided
        # (audit S060-24) they are US-dollar cash there too, as on
        # Coinbase, so a Kraken PYUSD send gets the currency gain, not a
        # sale line.
        from taxjson.lib import crypto_sends as cs
        self.assertTrue(cs.is_cash_stablecoin("PYUSD", "kraken"))
        self.assertTrue(cs.is_cash_stablecoin("PYUSD", "coinbase"))
        self.assertTrue(cs.is_cash_stablecoin("USDC", "kraken"))

        class R:
            base = "CAD"

            def get(self, cur, day):
                return (1.40, "boc", day)
        send = {"symbol": "PYUSD", "date": "2025-05-01", "quantity": -10.0,
                "exchange": "kraken", "price": 0.99, "currency": "USD"}
        fv = cs.fair_value(send, R(), None)
        self.assertIn("US-dollar cash", fv["source"])
        self.assertAlmostEqual(fv["price"], 1.40, places=4)

    @rule("CA-CRYPTO-06")
    @rule("US-CRYPTO-05")
    def test_send_paired_with_its_arrival_within_3_days_and_90pct(self):
        from taxjson.lib import crypto_sends as cs

        def row(acct, ex, date, time, qty):
            return {"account": acct, "exchange": ex, "date": date,
                    "time": time, "symbol": "SOL", "quantity": qty,
                    "kind": "withdrawal" if qty < 0 else "deposit",
                    "fee": 0.0, "ref": ""}
        send = row("a", "coinbase", "2025-07-13", "07:13:27", -50.0)
        ok = row("b", "kraken", "2025-07-13", "07:15:42", 49.9)
        unmatched, pairs = cs.match_transfers([send, ok])
        self.assertEqual((len(unmatched), len(pairs)), (0, 1))
        short = row("b", "kraken", "2025-07-13", "07:15:42", 44.0)
        unmatched, pairs = cs.match_transfers([dict(send), short])
        self.assertEqual(len(pairs), 0)          # 88% arrived: not paired
        late = row("b", "kraken", "2025-07-17", "08:00:00", 50.0)
        unmatched, pairs = cs.match_transfers([dict(send), late])
        self.assertEqual(len(pairs), 0)          # 4 days: not paired

    @rule("CA-FX-02")
    @rule("US-FX-02")
    def test_rate_gap_uses_5_days_back_then_an_error(self):
        from taxjson.bin import taxjson_convert_currency as CC
        hist = {"USD": {"2025-06-02": Decimal("1.37")}}
        CC.reset_fallback_tally()
        near = CC.convert_transaction(
            tx("BUYSELL", "2025-06-06", "AAA.US", 1, 10), "CAD", hist,
            Decimal("1.35"))
        self.assertAlmostEqual(near.net_amount, 13.7, places=6)
        self.assertEqual(CC.fallback_rows(), [])
        far = CC.convert_transaction(
            tx("BUYSELL", "2025-06-09", "AAA.US", 1, 10), "CAD", hist,
            Decimal("1.35"))
        self.assertAlmostEqual(far.net_amount, 13.5, places=6)
        issues = CC.fallback_validation_issues("CAD", Decimal("1.35"))
        CC.reset_fallback_tally()
        self.assertEqual(len(issues), 1)
        self.assertIn("default rate", list(issues.values())[0][0])


if __name__ == "__main__":
    unittest.main()
