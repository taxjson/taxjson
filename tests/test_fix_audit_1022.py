"""Workflow audit 2026-10-22: the verified findings
(~/workflow-audit-2026-10-22.md is the maintainer's report).

Every fixture is SYNTHETIC: account 99900001, made-up tickers ZZ*.
"""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule

ACCT = '99900001'


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


# ---------------------------------------------------------------------------
# RBC: an export without a 'Symbol Description' column (the demo's layout)

RH_DEMO = ('Date,Activity,Symbol,Description,Quantity,Price,'
           'Settlement Date,Currency,Value,Amount\n')


def _rbc_demo(day, act, sym, desc, qty, price, value):
    return ','.join([day, act, sym, desc, qty, price, day, 'CAD', value,
                     value]) + '\n'


RBC_SPIN = (
    _rbc_demo('01/05/2026', 'Buy', 'ZZP', 'ZZP PARENT CORP - Buy', '105',
              '10.00', '-1050.00')
    + _rbc_demo('06/15/2026', 'Reorganization', 'ZZS',
                'DIS - ZZS SPINCO INC SPINOFF ON 105 SHS FROM SEC# X123456 '
                'ZZP PARENT CORP REC 06/01/26 PAY 06/15/26', '10', '0', '0'))


def rbc_events(body):
    from taxjson.lib.corp_actions import parse_rbc_corporate_actions
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'rbc_2026.csv'
        p.write_text('Account Activity Detail Report\n'
                     f'"Account Number: {ACCT}"\n\n' + RH_DEMO + body)
        return _quiet(parse_rbc_corporate_actions, p, 'margin',
                      context_files=[p])


class TestRbcSpinoffParentWithoutSymbolDescription(unittest.TestCase):

    def test_parent_found_from_the_trade_description(self):
        evs, err = rbc_events(RBC_SPIN)
        self.assertEqual(len(evs), 1, err)
        self.assertEqual(evs[0].source_symbol, 'ZZP.TO')
        self.assertNotIn('is not traded in this statement', err)

    def test_unrelated_trade_is_still_not_the_parent(self):
        body = RBC_SPIN.replace('ZZP PARENT CORP - Buy',
                                'ZZQ OTHER HOLDINGS - Buy')
        evs, err = rbc_events(body)
        self.assertNotEqual(evs[0].source_symbol, 'ZZP.TO')
        self.assertIn('is not traded in this statement', err)


# ---------------------------------------------------------------------------
# close-year: a T1A carry-back recorded in [carryover] claimed

class TestCarryBackLeavesTheLock(unittest.TestCase):
    NETS = {2023: {"net": 990.10, "st": 0, "lt": 0, "dispositions": 1},
            2024: {"net": -2009.90, "st": 0, "lt": 0, "dispositions": 1}}

    @rule("CA-CARRY-01")
    def test_ledger_names_the_carry_back(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        by = {r["year"]: r for r in build_canada_ledger(
            self.NETS, {2023: 990.10})["rows"]}
        self.assertAlmostEqual(by[2024]["carried_back"], 990.10, places=2)
        self.assertAlmostEqual(by[2024]["carryforward_balance"], 1019.80,
                               places=2)
        self.assertEqual(by[2023]["carried_back"], 0.0)
        # A claim under the loss year itself is applied, not carried back.
        nets = dict(self.NETS)
        nets[2025] = {"net": 500.0, "st": 0, "lt": 0, "dispositions": 1}
        by = {r["year"]: r for r in build_canada_ledger(
            nets, {2025: 500.0})["rows"]}
        self.assertEqual(by[2025]["carried_back"], 0.0)

    @rule("CA-CARRY-01")
    def test_lock_carries_out_the_balance_after_the_carry_back(self):
        from taxjson.bin import taxjson_run as R
        est = {"losses_opening": 0.0, "losses_created": 2009.90,
               "losses_applied": 0.0, "losses_unused": 2009.90}
        ledger = {"rows": [{"year": 2023, "carried_back": 0.0},
                           {"year": 2024, "carried_back": 990.10}]}

        def fake(argv, capture_output=False, **_k):
            doc = ledger if "carryover" in argv else {"estimate": est}
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'province = "ON"\n[accounts.margin]\ntype = "taxable"\n')
            cfg = R.load_config(root)
            with mock.patch("taxjson.lib.dispatch.run_cmd", fake):
                block = R._carryforwards_for_lock(root, cfg)
        ncl = block["net_capital_loss"]
        self.assertAlmostEqual(ncl["carried_back"], 990.10, places=2)
        self.assertAlmostEqual(ncl["closing"], 1019.80, places=2)
        line = "\n".join(R._carryforwards_summary(block, 2024))
        self.assertIn("carried back 990.10", line)


# ---------------------------------------------------------------------------
# Generic CSV: a futures BUY at a negative price receives cash

_GHDR = "Date,Action,Symbol,Quantity,Price,Amount,Fee,Currency\n"
_GTOML = ('[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
          'quantity="Quantity"\nprice="Price"\namount="Amount"\n'
          'fee="Fee"\ncurrency="Currency"\n'
          '[actions]\n"BUY"="buy"\n"SELL"="sell"\n')
_GROWS = ("2024-04-15,BUY,/ZQX,1,-5.00,5000.00,0,USD\n"
          "2024-04-20,SELL,/ZQX,-1,-10.00,-10000.00,0,USD\n")


def _generic(csv_text):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    from taxjson.lib.brokerages.schema import validate_transactions
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_t.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(_GTOML)
        txs, _err = _quiet(GenericBrokerage().parse_file, c)
    return txs, validate_transactions(txs)[0]


def _native_gain(txs, country):
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.pipeline import GainsRequest, run_gains
    rows = [TaxTransaction(**{k: t[k] for k in (
        "action", "date", "date_settle", "time", "symbol", "quantity",
        "price", "net_amount", "currency")}) for t in txs]
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(rows, (), (), GainsRequest(
            country=country, taxable=True, phantom_hint=False))
    return sum(t["gain"] for t in res["transactions"] if "gain" in t)


class TestGenericNegativeFuturesBuy(unittest.TestCase):

    def test_price_keeps_its_sign_and_the_schema_accepts_it(self):
        txs, errors = _generic(_GHDR + _GROWS)
        self.assertEqual([t["price"] for t in txs], [-5.0, -10.0])
        self.assertAlmostEqual(txs[0]["net_amount"], -5000.0)
        self.assertEqual(errors, [])

    @rule("CA-FX-04")
    def test_canada_native_gain(self):
        txs, _ = _generic(_GHDR + _GROWS)
        self.assertAlmostEqual(_native_gain(txs, "canada"), -5000.0)

    @rule("US-FUT-01")
    def test_usa_native_gain(self):
        txs, _ = _generic(_GHDR + _GROWS)
        self.assertAlmostEqual(_native_gain(txs, "usa"), -5000.0)

    def test_stock_price_still_unsigned(self):
        txs, _ = _generic(_GHDR + "2024-04-15,BUY,ZZA,10,5.00,50.00,0,USD\n")
        self.assertEqual(txs[0]["price"], 5.0)


if __name__ == '__main__':
    unittest.main()
