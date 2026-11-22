"""No hard-coded broker or security data in the flow (owner, 2026-10-04):
option contract sizes (B10), Webull's exercise charge (B11) and RBC's
year-end posting day (B12) come from the export, ticker.map or
taxjson.toml — never a silent constant.

Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_generalize_c
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.parsers.settings import ParseSettings
import _paths

_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2025,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H25 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",,'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')


def _parse_wb(text, exercise_fee=None):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(text, encoding="utf-8")
        err = io.StringIO()
        p = WebullBrokerage()
        p.configure(ParseSettings(exercise_fee=exercise_fee))
        with contextlib.redirect_stderr(err):
            tx = p.parse_file(f)
        return tx, err.getvalue()


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


@rule(R.CA_OPT_08)
@rule(R.US_OPT_05)
class TestContractSizeInTheEngine(_MarketsEnv):
    """B10: the delivered quantity is contracts x a MULT line, else the
    stated or derived size, else 100 ASSUMED with a once-per-root note.

    Formerly: test_fix_generalize_c.TestContractSizeInTheEngine"""

    @staticmethod
    def _opt(**kw):
        from taxjson.model.transaction import TaxTransaction
        d = dict(action="ASSIGN", date="2025-03-21", symbol=
                 "ZZQ1250321C00050000.US", quantity=-2.0, currency="USD")
        d.update(kw)
        return TaxTransaction(**d)

    def _size(self, tx):
        from taxjson.engine.assign import _assign_delivery_shares
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
        from taxjson.lib.contract_size import option_contract_size
        self.assertEqual(option_contract_size(self._opt()), 150.0)

    def test_ordinary_standard_option_numbers_unchanged(self):
        from taxjson.lib.contract_size import option_contract_size
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(option_contract_size(self._opt()), 100.0)
            self.assertEqual(option_contract_size(
                self._opt(symbol="F:ZZQ250321C00050000.US")), 1.0)


class TestMiniDerivedFromTheRow(_MarketsEnv):
    """B10: a non-IB row whose money fits qty x price x 10 but not x 100
    is a mini (derived, noted); a standard row stays 100 (assumed).

    Formerly: test_fix_generalize_c.TestMiniDerivedFromTheRow"""

    def test_questrade_gross_shows_a_mini(self):
        import sys
        sys.path.insert(0, str(_paths.TESTS))
        from _support.qt_rows import H, _parse, row
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
        sys.path.insert(0, str(_paths.TESTS))
        from _support.parser_qt_rbc_rows import rrow, rbc_parse, of
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
