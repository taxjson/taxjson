"""No hard-coded broker or security data in the flow (owner, 2026-10-04):
option contract sizes (B10), Webull's exercise charge (B11) and RBC's
year-end posting day (B12) come from the export, ticker.map or
taxjson.toml — never a silent constant.

Synthetic data only; account ids are fake (pii-ok).
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.brokerages.webull import WebullBrokerage

_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2025,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H25 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",,'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')

# A long call closed at $0, then 100 shares bought AT THE STRIKE with a
# stock-leg charge of {fee} (net = 5,000 + fee).
_LONGCALL = (_PRE + _H25 +
             'USD,20-03-2025,BUY,@ZZQ,CALL ZZQ03/21/25 50,OPC,1,1.00,,(100.99)\n'
             'USD,21-03-2025,SELL,,,,-1,0.00,,\n'
             'USD,25-03-2025,BUY,ZZQ,ZZQ CORP,SHS,100,50.00,,"({net})"\n')


def _parse_wb(text, exercise_fee=None):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(text, encoding="utf-8")
        err = io.StringIO()
        p = WebullBrokerage()
        if exercise_fee is not None:
            p.exercise_fee = exercise_fee
        with contextlib.redirect_stderr(err):
            tx = p.parse_file(f)
        return tx, err.getvalue()


@rule("CA-OPT-06")
@rule("US-OPT-02")
class TestWebullExerciseFeeIsConfigured(unittest.TestCase):
    """B11: the exercise/assignment charge is `[accounts.X] exercise_fee`;
    without it nothing is inferred (each candidate is named)."""

    def test_no_fee_configured_infers_nothing_and_names_the_key(self):
        tx, err = _parse_wb(_LONGCALL.format(net="5,001.00"))
        self.assertFalse([t for t in tx if t["action"] == "ASSIGN"])
        self.assertIn("NOT inferred", err)
        self.assertIn("exercise_fee", err)

    def test_configured_fee_pairs(self):
        tx, err = _parse_wb(_LONGCALL.format(net="5,001.00"),
                            exercise_fee=1.0)
        self.assertEqual(len([t for t in tx if t["action"] == "ASSIGN"]), 2)
        self.assertIn("inferred an exercise", err)

    def test_another_fee_schedule(self):
        tx, _ = _parse_wb(_LONGCALL.format(net="5,002.50"),
                          exercise_fee=2.5)
        self.assertEqual(len([t for t in tx if t["action"] == "ASSIGN"]), 2)
        tx, err = _parse_wb(_LONGCALL.format(net="5,001.00"),
                            exercise_fee=2.5)
        self.assertFalse([t for t in tx if t["action"] == "ASSIGN"])
        self.assertIn("2.50 exercise/assignment charge", err)

    def test_config_check_and_run_flag(self):
        from taxjson.bin.taxjson_run import _brokerage_account_flags
        from taxjson.lib.config_check import account_type_problems
        ok = {"settings": {}, "accounts": {"wb": {"type": "taxable",
                                                 "exercise_fee": 1.0}}}
        self.assertEqual(account_type_problems(ok), [])
        bad = {"settings": {}, "accounts": {"wb": {"type": "taxable",
                                                  "exercise_fee": "1.00"}}}
        self.assertTrue(any("exercise_fee" in m
                            for m in account_type_problems(bad)))
        self.assertEqual(_brokerage_account_flags({"exercise_fee": 1.0}),
                         ["--exercise-fee", "1"])
        self.assertEqual(_brokerage_account_flags({}), [])

    def test_brokerage_cli_passes_the_fee(self):
        from taxjson.bin import taxjson_brokerage
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "wb.csv"
            f.write_text(_LONGCALL.format(net="5,001.00"), encoding="utf-8")
            outs = {}
            for extra in ([], ["--exercise-fee", "1.00"]):
                out, err = io.StringIO(), io.StringIO()
                import sys
                argv = sys.argv
                sys.argv = ["taxjson-brokerage", str(f), "--brokerage",
                            "webull", *extra]
                try:
                    with contextlib.redirect_stdout(out), \
                            contextlib.redirect_stderr(err):
                        try:
                            taxjson_brokerage.main()
                        except SystemExit as e:
                            self.assertIn(e.code, (0, None), err.getvalue())
                finally:
                    sys.argv = argv
                outs[bool(extra)] = out.getvalue()
            self.assertNotIn('"ASSIGN"', outs[False])
            self.assertIn('"ASSIGN"', outs[True])


class TestRbcYearEndPostingIsASetting(unittest.TestCase):
    """B12: the day RBC has posted the year's back-dated book-cost rows
    is `[accounts.X] year_end_posting` (default 06-30), not a constant."""

    @staticmethod
    def _msgs(as_of, today, **kw):
        from datetime import date
        from taxjson.lib.brokerages.rbc_direct import rbc_coverage_messages
        return rbc_coverage_messages(
            [("rbc_2025.csv", as_of, ["2025-03-03", "2025-11-03"])],
            2025, listings=None, today=date.fromisoformat(today), **kw)

    def _noted(self, msgs):
        return any("year-end book-cost" in m for m in msgs)

    def test_default_is_june_30(self):
        self.assertTrue(self._noted(self._msgs("2026-04-15", "2026-08-01")))
        self.assertFalse(self._noted(self._msgs("2026-06-30", "2026-08-01")))

    def test_the_account_setting_moves_the_day(self):
        early = self._msgs("2026-04-15", "2026-08-01",
                           year_end_posting="03-31")
        self.assertFalse(self._noted(early), early)
        late = self._msgs("2026-08-15", "2026-10-01",
                          year_end_posting="09-30")
        self.assertTrue(self._noted(late), late)
        self.assertTrue(any("2026-09-30" in m for m in late), late)

    def test_bad_value_is_refused(self):
        from taxjson.lib.config_check import account_type_problems
        for bad in ("6/30", "02-30", 630, "13-01"):
            cfg = {"settings": {}, "accounts": {"rbc": {
                "type": "taxable", "year_end_posting": bad}}}
            self.assertTrue(any("year_end_posting" in m for m in
                                account_type_problems(cfg)), bad)
        ok = {"settings": {}, "accounts": {"rbc": {
            "type": "taxable", "year_end_posting": "03-31"}}}
        self.assertEqual(account_type_problems(ok), [])

    def test_run_passes_the_flag(self):
        from taxjson.bin.taxjson_run import _brokerage_account_flags
        self.assertEqual(
            _brokerage_account_flags({"year_end_posting": "03-31"}),
            ["--year-end-posting", "03-31"])


class _MarketsEnv(unittest.TestCase):
    """A ticker.map read through TAXJSON_TICKER_MAP, notes reset."""

    def setUp(self):
        from taxjson.lib import markets
        self.markets = markets
        self._td = tempfile.TemporaryDirectory()
        self._env = os.environ.get(markets.ENV_TICKER_MAP)
        os.environ[markets.ENV_TICKER_MAP] = ""
        markets.use_ticker_map(None)
        markets.reset_notes()

    def tearDown(self):
        m = self.markets
        if self._env is None:
            os.environ.pop(m.ENV_TICKER_MAP, None)
        else:
            os.environ[m.ENV_TICKER_MAP] = self._env
        m.reset_notes()
        self._td.cleanup()

    def use_map(self, text):
        p = Path(self._td.name) / "ticker.map"
        p.write_text(text)
        os.environ[self.markets.ENV_TICKER_MAP] = str(p)


@rule("CA-OPT-08")
@rule("US-OPT-05")
class TestContractSizeInTheEngine(_MarketsEnv):
    """B10: the delivered quantity is contracts x a MULT line, else the
    stated or derived size, else 100 ASSUMED with a once-per-root note."""

    @staticmethod
    def _opt(**kw):
        from taxjson.lib.core import TaxTransaction
        d = dict(action="ASSIGN", date="2025-03-21", symbol=
                 "ZZQ1250321C00050000.US", quantity=-2.0, currency="USD")
        d.update(kw)
        return TaxTransaction(**d)

    def _size(self, tx):
        from taxjson.lib.core import _assign_delivery_shares
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = _assign_delivery_shares(tx)
        return n, err.getvalue()

    def test_undeclared_size_is_assumed_and_noted_once(self):
        n, err = self._size(self._opt())
        self.assertEqual(n, 200.0)
        self.assertIn("ASSUMED", err)
        self.assertIn("MULT ZZQ1 N", err)
        _n, again = self._size(self._opt(quantity=-1.0))
        self.assertEqual(again, "")

    def test_a_parser_assumed_100_is_noted_too(self):
        n, err = self._size(self._opt(multiplier=100.0,
                                      contract_size_basis="assumed"))
        self.assertEqual(n, 200.0)
        self.assertIn("ASSUMED", err)

    def test_a_stated_size_is_used_quietly(self):
        n, err = self._size(self._opt(multiplier=10.0))     # IB states it
        self.assertEqual(n, 20.0)
        self.assertEqual(err, "")
        n, err = self._size(self._opt(multiplier=10.0,
                                      contract_size_basis="derived"))
        self.assertEqual((n, err), (20.0, ""))

    def test_mult_line_overrides_everything(self):
        self.use_map("MULT ZZQ1 150\n")
        n, err = self._size(self._opt(multiplier=100.0))
        self.assertEqual((n, err), (300.0, ""))
        from taxjson.lib.core import option_contract_size
        self.assertEqual(option_contract_size(self._opt()), 150.0)

    def test_ordinary_standard_option_numbers_unchanged(self):
        from taxjson.lib.core import option_contract_size
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(option_contract_size(self._opt()), 100.0)
            self.assertEqual(option_contract_size(
                self._opt(symbol="F:ZZQ250321C00050000.US")), 1.0)


class TestMiniDerivedFromTheRow(_MarketsEnv):
    """B10: a non-IB row whose money fits qty x price x 10 but not x 100
    is a mini (derived, noted); a standard row stays 100 (assumed)."""

    def test_questrade_gross_shows_a_mini(self):
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from test_questrade_parse_hardening import H, _parse, row
        _, txs, err = _parse(H + row(sym='QZA15Jan27C50.00',
                                     desc='CALL QZA 01/15/27 50 QZA CORP',
                                     qty='2', price='1.5', gross='-30',
                                     comm='-2', net='-32'))
        self.assertEqual(txs[0]['multiplier'], 10.0)
        self.assertEqual(txs[0]['contract_size_basis'], 'derived')
        self.assertIn("mini", err)
        _, txs, _ = _parse(H + row(sym='QZA15Jan27C50.00',
                                   desc='CALL QZA 01/15/27 50 QZA CORP',
                                   qty='2', price='1.5', gross='-300',
                                   comm='-2', net='-302'))
        self.assertEqual(txs[0]['multiplier'], 100.0)
        self.assertEqual(txs[0]['contract_size_basis'], 'assumed')

    def test_webull_proceeds_show_a_mini(self):
        rows = (_PRE + _H25 +
                'USD,20-03-2025,BUY,@ZZQ,CALL ZZQ06/20/25 50,OPC,2,1.50,,'
                '(31.00)\n')
        tx, err = _parse_wb(rows)
        opt = [t for t in tx if t["symbol"].startswith("ZZQ2")][0]
        self.assertEqual(opt.get("multiplier"), 10.0)
        self.assertEqual(opt.get("contract_size_basis"), "derived")
        self.assertIn("mini", err)
        rows = rows.replace("(31.00)", "(301.00)")
        tx, _ = _parse_wb(rows)
        opt = [t for t in tx if t["symbol"].startswith("ZZQ2")][0]
        self.assertNotIn("multiplier", opt)

    def test_rbc_value_shows_a_mini(self):
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from test_fix_rbcqt import rrow, rbc_parse, of
        body = rrow("March 3, 2025", "Buy", "8QZQQQ1",
                    "CALL .QZT 01/15/27 21 QZT CORP", "2", "1.40", "-37.95",
                    "USD", "CALL .QZT 01/15/27 21 QZT CORP")
        txs, err, _ = rbc_parse(body)
        b = of(txs, action='BUYSELL')[0]
        self.assertEqual(b.get('multiplier'), 10.0)
        self.assertEqual(b.get('contract_size_basis'), 'derived')
        body = body.replace("-37.95", "-289.95")
        txs, _, _ = rbc_parse(body)
        self.assertNotIn('multiplier', of(txs, action='BUYSELL')[0])


if __name__ == "__main__":
    unittest.main()
