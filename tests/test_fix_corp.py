"""Regression tests for the 2026-09 audit's corporate-action findings
(area `corp`). All data synthetic: fake tickers, fake ISINs, fake broker
account ids in the 555 range."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import SplitTimeline
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _split(symbol, date, ratio, account='A', symbol_new=''):
    return TaxTransaction(action='SPLIT', date=date, symbol=symbol,
                          quantity=ratio, symbol_new=symbol_new,
                          account=account)


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


_CAD_CONFIG = """\
[settings]
year = 2026
country = "canada"
province = "ON"
base_currency = "CAD"
source_currencies = []
tax_date = "settle"

[accounts.margin]
type = "taxable"
"""


# --------------------------------------------------------------- R1-135
@rule("CA-CORP-01")
class TestSplitBookedOnTwoDates(unittest.TestCase):
    """R1-135: one split booked on two dates by two brokers must apply
    once (the event key used to include the exact date)."""

    def test_engine_dedupe_collapses_nearby_dates(self):
        txs = [_split('XYZ.TO', '2026-06-11', 2.0),
               _split('XYZ.TO', '2026-06-15', 2.0)]
        kept = SplitTimeline.dedupe(txs)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].date, '2026-06-11')   # earliest wins

    def test_engine_dedupe_keeps_earliest_regardless_of_order(self):
        txs = [_split('XYZ.TO', '2026-06-15', 2.0),
               _split('XYZ.TO', '2026-06-11', 2.0)]
        kept = SplitTimeline.dedupe(txs)
        self.assertEqual([t.date for t in kept], ['2026-06-11'])

    def test_distant_or_different_ratio_splits_stay(self):
        txs = [_split('XYZ.TO', '2026-01-10', 2.0),
               _split('XYZ.TO', '2026-06-15', 2.0),
               _split('XYZ.TO', '2026-06-16', 3.0)]
        self.assertEqual(len(SplitTimeline.dedupe(txs)), 3)

    def test_shared_seen_across_lists_uses_window(self):
        seen = set()
        a = SplitTimeline.dedupe([_split('K.US', '2026-06-11', 10.0,
                                         account='tfsa')], seen)
        b = SplitTimeline.dedupe([_split('K.US', '2026-06-15', 10.0,
                                         account='lira')], seen)
        self.assertEqual(len(a), 1)
        self.assertEqual(b, [])

    def test_apply_distributions_balance_on(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        rows = [
            {"action": "BUYSELL", "symbol": "XYZ.TO", "date": "2026-01-05",
             "quantity": 200, "account": "margin"},
            {"action": "SPLIT", "symbol": "XYZ.TO", "date": "2026-06-11",
             "quantity": 2, "account": "margin"},
            {"action": "SPLIT", "symbol": "XYZ.TO", "date": "2026-06-15",
             "quantity": 2, "account": "margin"},
        ]
        self.assertAlmostEqual(balance_on(rows, "XYZ.TO", "2026-07-01"),
                               400.0)

    def test_run_one_account_two_brokers(self):
        # Two broker files of ONE taxable account, each holding 100 XYZ
        # through the same 2:1 split, dated 06-11 by one and 06-15 by
        # the other; both sell all 200. Correct total gain:
        # 2 x (200*6 - 100*10) = 400.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(_CAD_CONFIG)
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "brokerA.tt").write_text(
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 10 -1000 0\n"
                "SPLIT 2026-06-11 20:25:00 XYZ.TO XYZ.TO 2\n"
                "BUYSELL 2026-08-03 10:00:00 XYZ.TO -200 CAD 6 1200 0\n")
            (d / "brokerB.tt").write_text(
                "BUYSELL 2026-01-06 10:00:00 XYZ.TO 100 CAD 10 -1000 0\n"
                "SPLIT 2026-06-15 20:25:00 XYZ.TO XYZ.TO 2\n"
                "BUYSELL 2026-08-04 10:00:00 XYZ.TO -200 CAD 6 1200 0\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            g = json.loads((root / "work" /
                            "margin_gains_wash.json").read_text())
            self.assertAlmostEqual(float(g["summary"]["total_gain"]), 400.0,
                                   places=2)
            self.assertEqual([i for i in g.get("inventory", [])
                              if abs(i["qty"]) > 1e-9], [])
            sums = (root / "reports" / "margin.sum").read_text()
            self.assertIn("booked on two dates", sums + r.stderr)



# --------------------------------------------------------------- helpers
_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,{acct}\n'
            'Account Information,Data,Base Currency,CAD\n')
_IB_TRADES = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
              'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
              'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
_IB_CA = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
          'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
          'Code\n')


def _ib_buy(sym, qty, price, when='2026-01-05, 10:00:00', cur='CAD'):
    cost = qty * price
    return (f'Trades,Data,Order,Stocks,{cur},{sym},"{when}",{qty},{price},'
            f'{price},{-cost:.2f},0,{cost:.2f},0,0,O\n')


def _ib_sell(sym, qty, price, when, cur='CAD'):
    return (f'Trades,Data,Order,Stocks,{cur},{sym},"{when}",{-qty},{price},'
            f'{price},{qty * price:.2f},0,0,0,0,C\n')


def _ib_ca(desc, qty, value, proceeds=0, when='2026-04-01, 20:25:00',
           cur='CAD', code=''):
    return (f'Corporate Actions,Data,Stocks,{cur},{when[:10]},"{when}",'
            f'"{desc}",{qty},{proceeds},{value},0,{code}\n')


def _project(tmp, files, acct_type='taxable'):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        _CAD_CONFIG.replace('type = "taxable"', f'type = "{acct_type}"'))
    d = root / "inputs" / "margin"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body)
    return root


def _pending(root):
    doc = json.loads((root / "work" / "pending_elections.json").read_text())
    return doc["accounts"]["margin"]["pending"]


def _gains(root):
    g = json.loads((root / "work" / "margin_gains_wash.json").read_text())
    inv = {i["symbol"]: (round(i["qty"], 6), round(i["total_cost"], 2))
           for i in g.get("inventory", []) if abs(i["qty"]) > 1e-9}
    return float(g["summary"]["total_gain"]), inv


def _corp_rows(root, broker):
    return json.loads((root / "work" / f"margin_{broker}_corp.json")
                      .read_text())["transactions"]


def _tmp_csv(text):
    f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


_SPIN = 'PARNT(CA0000000777) Spinoff  1 for 5 (SPNCO, SPINCO CORP, CA0000000778)'


def _ib_spinoff_project(tmp, acct_type='taxable'):
    body = (_IB_HEAD.format(acct='U5550001') + _IB_TRADES  # pii-ok
            + _ib_buy('PARNT', 100, 60) + _IB_CA
            + _ib_ca(_SPIN, 20, 900))
    return _project(tmp, {'ib_2026.csv': body}, acct_type)


# --------------------------------------------------------------- R1-137
@rule("CA-CORP-06")
class TestIbSpinoffElection(unittest.TestCase):
    """R1-137: an IB 'Spinoff' row goes through the spin-off election
    (it was always booked as a dividend at IB's Value)."""

    def test_parser_leaves_spinoff_to_corp_actions(self):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import io
        from contextlib import redirect_stderr
        path = _tmp_csv(_IB_HEAD.format(acct='U5550001') + _IB_CA  # pii-ok
                        + _ib_ca(_SPIN, 20, 900))
        try:
            with redirect_stderr(io.StringIO()):
                txs = IbBrokerage().parse_file(path)
            from taxjson.lib.corp_actions import parse_ib_corporate_actions
            evs = parse_ib_corporate_actions(path, 'margin')
        finally:
            path.unlink()
        self.assertEqual(txs, [])
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertEqual((ev.action_type, ev.source_symbol, ev.target_symbol),
                         ('spinoff', 'PARNT.TO', 'SPNCO.TO'))
        self.assertAlmostEqual(ev.qty_received, 20)
        self.assertAlmostEqual(ev.target_fmv, 900)

    def test_run_asks_then_s86_1_allocates_acb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _ib_spinoff_project(tmp)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            pend = _pending(root)
            self.assertEqual(len(pend), 1)
            opts = {o["election"]: o for o in pend[0]["options"]}
            self.assertIn("rollover_s_86_1", opts)
            # IB reported the value: no FMV prompt for the default.
            self.assertEqual(opts["taxable_deemed_dividend"]["hints"], [])
            eid = pend[0]["event_id"]
            e = _run_cli(root, "elect", "margin", "--set",
                         f"{eid}=rollover_s_86_1", "--hint",
                         "allocated_acb_cad=1000")
            self.assertEqual(e.returncode, 0, e.stderr + e.stdout)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            _gain, inv = _gains(root)
            self.assertEqual(inv["PARNT.TO"], (100, 5000.0))
            self.assertEqual(inv["SPNCO.TO"], (20, 1000.0))
            rows = _corp_rows(root, "ib")
            self.assertFalse([t for t in rows if t["action"] == "DIVIDEND"])


# --------------------------------------------------------------- R1-134
@rule("CA-CORP-07")
class TestSpinoffBrokerValue(unittest.TestCase):
    """R1-134: the deemed dividend uses the broker's value when it
    reported one; a $0 taxable spin-off warns on every run."""

    def _event(self, **kw):
        from taxjson.lib.corp_actions import CorporateAction
        base = dict(date='2026-05-14', time='20:25:00',
                    action_type='spinoff', source_symbol='XYZ.US',
                    source_isin='US0000000101', target_symbol='XYZA.US',
                    target_isin='US0000000103', ratio_new=1, ratio_old=4,
                    qty_disposed=0, qty_received=10, fmv=1970.0,
                    target_fmv=1970.0, currency='USD', target_currency='USD',
                    account='margin', fractional_delivery=True)
        base.update(kw)
        return CorporateAction(**base)

    def test_default_uses_broker_value(self):
        from taxjson.lib.corp_actions import resolve_event
        for hints in ({}, {'fmv_per_share': 0.0}):
            rows = resolve_event(self._event(), 'taxable_deemed_dividend',
                                 hints=hints)
            div = next(r for r in rows if r['action'] == 'DIVIDEND')
            buy = next(r for r in rows if r['action'] == 'BUYSELL')
            self.assertAlmostEqual(div['net_amount'], 1970.0)
            self.assertAlmostEqual(buy['net_amount'], 1970.0)

    def test_positive_hint_wins(self):
        from taxjson.lib.corp_actions import resolve_event
        rows = resolve_event(self._event(), 'taxable_deemed_dividend',
                             hints={'fmv_per_share': 150.0})
        div = next(r for r in rows if r['action'] == 'DIVIDEND')
        self.assertAlmostEqual(div['net_amount'], 1500.0)

    def test_hint_prompt_only_without_broker_value(self):
        from taxjson.lib.corp_actions import HINTS_BY_ELECTION
        for key in ('taxable_deemed_dividend', 'taxable_distribution_301'):
            spec = HINTS_BY_ELECTION[key][0]
            self.assertFalse(spec[2](self._event()))
            self.assertTrue(spec[2](self._event(fmv=0.0, target_fmv=0.0)))

    def test_zero_value_taxable_spinoff_warns_every_run(self):
        # A Questrade spin-off (no broker value) deferred at 0.
        h = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
             'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
             'Activity Type,Account #,Account Type\n')
        buy = ('2025-06-02 12:00:00 AM,2025-06-03 12:00:00 AM,Buy,AAA,'
               'ALPHA CORP,1000,10.00,-10000.00,0,-10000.00,CAD,Trades,'
               '55500001,Margin\n')  # pii-ok
        dis = ('2026-01-27 12:00:00 AM,2026-01-27 12:00:00 AM,DIS,AAAW,'
               'WTS ALPHA CORP WT SPINOFF ON 1000 SHS FROM SEC# J000001 '
               'ALPHA CORP REC 01/20/26 PAY 01/27/26,100,0,0,0,0,CAD,'
               'Dividends,55500001,Margin\n')  # pii-ok
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {'questrade_all.csv': h + buy + dis})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            eid = _pending(root)[0]["event_id"]
            _run_cli(root, "elect", "margin", "--set",
                     f"{eid}=taxable_deemed_dividend", "--hint",
                     "fmv_per_share=0")
            for _ in range(2):          # the second run rebuilds nothing
                r = _run_cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                self.assertIn("is booked at $0", r.stderr)
            self.assertIn("is booked at $0",
                          (root / "reports" / "margin.sum").read_text())


# --------------------------------------------------------------- R1-136
@rule("CA-CORP-03")
class TestIbMergerShapes(unittest.TestCase):
    """R1-136: IB cash takeovers are sales; decimal-ratio and class-share
    mergers are booked; a stock+cash merger stops the run by name."""

    CASH = ('TGT(CA0000000555) Merged(Acquisition) FOR CAD 30.00 PER SHARE '
            '(TGT, TARGET CO, CA0000000555)')

    def test_cash_takeover_is_a_sale(self):
        body = (_IB_HEAD.format(acct='U5550001') + _IB_TRADES  # pii-ok
                + _ib_buy('TGT', 100, 20) + _IB_CA
                + _ib_ca(self.CASH, -100, -3000, proceeds=3000,
                         when='2026-06-01, 20:25:00'))
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {'ib_2026.csv': body})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            gain, inv = _gains(root)
            self.assertAlmostEqual(gain, 1000.0, places=2)
            self.assertNotIn("TGT.TO", inv)

    def test_decimal_ratio_and_class_share_parse(self):
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        d1 = ('OLDA(CA0000000601) Merged(Acquisition) WITH CA0000000602 '
              '1.025 for 1 ({t}, NEWA CORP, {i})')
        d2 = ('BRK B(US0000000701) Merged(Acquisition) WITH US0000000702 '
              '1 for 1 ({t}, NEW B CORP, {i})')
        body = (_IB_HEAD.format(acct='U5550001') + _IB_CA  # pii-ok
                + _ib_ca(d1.format(t='NEWA', i='CA0000000602'), 41, 4100)
                + _ib_ca(d1.format(t='OLDA', i='CA0000000601'), -40, -4000)
                + _ib_ca(d2.format(t='NEW B', i='US0000000702'), 10, 500,
                         cur='USD')
                + _ib_ca(d2.format(t='BRK B', i='US0000000701'), -10, -500,
                         cur='USD'))
        path = _tmp_csv(body)
        try:
            evs = parse_ib_corporate_actions(path, 'margin')
        finally:
            path.unlink()
        got = {(e.source_symbol, e.target_symbol, e.ratio) for e in evs}
        self.assertIn(('OLDA.TO', 'NEWA.TO', 1.025), got)
        self.assertIn(('BRK.B.US', 'NEW.B.US', 1.0), got)

    def test_stock_plus_cash_blocks_the_run_by_name(self):
        d = ('OLDC(CA0000000801) Merged(Acquisition) WITH CA0000000802 1 '
             'for 2 AND CAD 5.00 ({t}, {n}, {i})')
        body = (_IB_HEAD.format(acct='U5550001') + _IB_TRADES  # pii-ok
                + _ib_buy('OLDC', 100, 20) + _IB_CA
                + _ib_ca(d.format(t='NEWC', n='NEWC CORP', i='CA0000000802'),
                         50, 2000)
                + _ib_ca(d.format(t='OLDC', n='OLDC CORP', i='CA0000000801'),
                         -100, -2500, proceeds=500))
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {'ib_2026.csv': body})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            pend = _pending(root)
            self.assertEqual(len(pend), 1)
            self.assertIn("UNSUPPORTED", pend[0]["summary"])
            self.assertIn("OLDC.TO", pend[0]["summary"])
            self.assertEqual([o["election"] for o in pend[0]["options"]],
                             ["ignore"])


# --------------------------------------------------------------- R1-142 / S019-07
@rule("CA-CORP-03")
class TestOneValuation(unittest.TestCase):
    def _merger(self, **kw):
        from taxjson.lib.corp_actions import CorporateAction
        base = dict(date='2026-03-02', time='20:25:00', action_type='merger',
                    source_symbol='ABC.TO', source_isin='CA0000000001',
                    target_symbol='DEF.TO', target_isin='CA0000000002',
                    ratio_new=1, ratio_old=1, qty_disposed=100,
                    qty_received=100, fmv=1500.0, target_fmv=1450.0,
                    currency='CAD', target_currency='CAD', account='margin',
                    fractional_delivery=True)
        base.update(kw)
        return CorporateAction(**base)

    def test_r1_142_same_value_for_both_legs(self):
        from taxjson.lib.corp_actions import resolve_event
        sell, buy = resolve_event(self._merger(), 'taxable_disposition')
        self.assertAlmostEqual(sell['net_amount'], 1450.0)
        self.assertAlmostEqual(buy['net_amount'], 1450.0)

    def test_s019_07_cross_currency_hint_converted(self):
        # RBC: no broker value; hint 12 USD per NEW share; CAD-listed old
        # shares. Proceeds = 1,200 USD in CAD; cost = 1,200 USD.
        from taxjson.lib.corp_actions import resolve_event
        ev = self._merger(target_symbol='NEWC.US', target_currency='USD',
                          fmv=0.0, target_fmv=0.0,
                          fractional_delivery=False)
        fx = lambda a, f, t, d: a * 1.3684 if (f, t) == ('USD', 'CAD') \
            else a / 1.3684
        sell, buy = resolve_event(ev, 'taxable_disposition',
                                  hints={'fmv_per_share': 12}, fx=fx)
        self.assertEqual(sell['currency'], 'CAD')
        self.assertAlmostEqual(sell['net_amount'], 1642.08, places=2)
        self.assertEqual(buy['currency'], 'USD')
        self.assertAlmostEqual(buy['net_amount'], 1200.0)

    def test_no_rate_books_in_consideration_currency(self):
        from taxjson.lib.corp_actions import resolve_event
        import io
        from contextlib import redirect_stderr
        ev = self._merger(target_symbol='NEWC.US', target_currency='USD',
                          fmv=0.0, target_fmv=0.0)
        with redirect_stderr(io.StringIO()) as err:
            sell, _buy = resolve_event(ev, 'taxable_disposition',
                                       hints={'fmv_per_share': 12})
        self.assertEqual((sell['currency'], sell['net_amount']),
                         ('USD', 1200.0))
        self.assertIn('no USD->CAD rate', err.getvalue())

    def test_rates_converter(self):
        from taxjson.lib.corp_actions import rates_converter
        path = _tmp_csv('2026-03-02 12:00:00 USD CAD 1.40 boc\n')
        try:
            fx = rates_converter(path, 'CAD')
        finally:
            path.unlink()
        self.assertAlmostEqual(fx(100, 'USD', 'CAD', '2026-03-02'), 140.0)
        self.assertAlmostEqual(fx(140, 'CAD', 'USD', '2026-03-04'), 100.0)
        self.assertIsNone(fx(1, 'USD', 'CAD', '2026-04-30'))

    def test_chain_keeps_the_merger_hop_value(self):
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        m1 = ('ABG(CA0000000001) Merged(Acquisition) WITH US0000000002 1 '
              'for 16 ({t}, ABH GOLD INC, {i})')
        m2 = ('ABH.CAD(10000001) Merged(Acquisition) WITH ABH 1 for 1 '
              '({t}, ABH GOLD INC, US0000000002)')
        body = (_IB_HEAD.format(acct='U5550001') + _IB_CA  # pii-ok
                + _ib_ca(m1.format(t='ABH.CAD', i='US0000000002'), 100,
                         80823.48, when='2025-10-22, 20:25:00')
                + _ib_ca(m1.format(t='ABG', i='CA0000000001'), -1600,
                         -81138.28, when='2025-10-22, 20:25:00')
                + _ib_ca(m2.format(t='ABH.CAD'), -100, -80000,
                         when='2025-10-28, 20:25:00')
                + _ib_ca(m2.format(t='ABH'), 100, 56705.26, cur='USD',
                         when='2025-10-28, 20:25:00'))
        path = _tmp_csv(body)
        try:
            evs = parse_ib_corporate_actions(path, 'rrsp')
        finally:
            path.unlink()
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertEqual(ev.target_symbol, 'ABH.US')
        self.assertEqual((ev.target_fmv, ev.target_fmv_currency),
                         (80823.48, 'CAD'))


# --------------------------------------------------------------- S002-04 / S019-04
@rule("CA-CORP-03")
class TestEventInTwoBrokerAccounts(unittest.TestCase):
    D = 'ABC(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2'

    def _ca(self, q):
        return (_ib_ca(f'{self.D} (DEF, DEF CORP, CA0000000002)', q / 2,
                       q * 12, when='2026-06-11, 20:25:00')
                + _ib_ca(f'{self.D} (ABC, ABC CORP, CA0000000001)', -q,
                         -q * 12, when='2026-06-11, 20:25:00'))

    def _file(self, acct, q):
        return (_IB_HEAD.format(acct=acct) + _IB_TRADES
                + _ib_buy('ABC', q, 10, when='2026-03-03, 10:00:00')
                + _IB_CA + self._ca(q))

    def _run(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, files)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            ids = {p["event_id"] for p in _pending(root)}
            self.assertEqual(len(ids), 1)
            for eid in ids:
                _run_cli(root, "elect", "margin", "--set",
                         f"{eid}=taxable_disposition")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            return _gains(root)

    def test_two_sub_account_statements(self):
        gain, inv = self._run({
            'ib_u1.csv': self._file('U5550001', 1000),   # pii-ok
            'ib_u2.csv': self._file('U5550002', 600)})   # pii-ok
        self.assertAlmostEqual(gain, 3200.0, places=2)
        self.assertEqual(inv, {'DEF.TO': (800, 19200.0)})

    def test_overlapping_statements_of_one_account(self):
        gain, inv = self._run({
            'ib_u1.csv': self._file('U5550001', 1000),        # pii-ok
            'ib_u1_copy.csv': self._file('U5550001', 1000)})  # pii-ok
        self.assertEqual(inv.get('DEF.TO', (0,))[0], 500)

    def test_rbc_two_accounts_in_one_export(self):
        from taxjson.lib.corp_actions import (combine_broker_copies,
                                              parse_rbc_corporate_actions)
        import io
        from contextlib import redirect_stderr
        h = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
             '"Price","Settlement Date","Account","Value","Currency",'
             '"Description"\n')

        def rows(acct, q):
            return (f'"2025-06-11 00:00:00","Reorganization","A012345",'
                    f'"ABC CORP","-{q}","","2025-06-11 00:00:00","{acct}",'
                    f'"0","CAD","MGR - ABC CORP MERGER TO DEF CORP 0.5 NEW '
                    f'= 1 OLD"\n'
                    f'"2025-06-11 00:00:00","Reorganization","DEF",'
                    f'"DEF CORP","{q // 2}","","2025-06-11 00:00:00",'
                    f'"{acct}","0","CAD","MGR - DEF CORP SHRS RECEIVED THRU '
                    f'MERGER"\n'
                    f'"2025-03-03 00:00:00","Buy","ABC","ABC CORP","{q}",'
                    f'"10","2025-03-04 00:00:00","{acct}","-{10 * q}","CAD",'
                    f'"ABC CORP UNSOLICITED"\n')
        path = _tmp_csv(h + rows('55500001', 100)     # pii-ok
                        + rows('55500002', 60))       # pii-ok
        try:
            with redirect_stderr(io.StringIO()):
                evs = combine_broker_copies(
                    parse_rbc_corporate_actions(path, 'margin'))
        finally:
            path.unlink()
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0].qty_disposed, evs[0].qty_received),
                         (160.0, 80.0))


# --------------------------------------------------------------- S020-04 / S020-08
_QT_H = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
         'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
         'Activity Type,Account #,Account Type\n')


@rule("CA-CORP-06")
class TestQuestradeSpinoffParent(unittest.TestCase):
    def test_s020_04_parent_from_another_export(self):
        from taxjson.bin.taxjson_corp_actions import extract_events
        from taxjson.lib.corp_actions import parse_questrade_corporate_actions
        buy = ('2025-06-02 12:00:00 AM,2025-06-03 12:00:00 AM,Buy,AAA,'
               'ALPHA CORP,1000,10.00,-10000.00,0,-10000.00,CAD,Trades,'
               '55500001,Margin\n')  # pii-ok
        dis = ('2026-01-27 12:00:00 AM,2026-01-27 12:00:00 AM,DIS,AAAW,'
               'WTS ALPHA CORP WT SPINOFF ON 1000 SHS FROM SEC# J000001 '
               'ALPHA CORP REC 01/20/26 PAY 01/27/26,100,0,0,0,0,CAD,'
               'Dividends,55500001,Margin\n')  # pii-ok
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'qt_2025.csv', Path(tmp) / 'qt_2026.csv'
            a.write_text(_QT_H + buy)
            b.write_text(_QT_H + dis)
            evs = extract_events(parse_questrade_corporate_actions, [a, b],
                                 'margin')
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0].source_symbol, evs[0].target_symbol),
                         ('AAA.TO', 'AAAW.TO'))

    def test_s020_08_fx_settled_parent_is_us_listed(self):
        from taxjson.lib.corp_actions import parse_questrade_corporate_actions
        rows = (
            '2026-01-05 12:00:00 AM,2026-01-06 12:00:00 AM,Buy,ABC,ABC '
            'HOLDINGS INC WE ACTED AS AGENT EXCHANGE RATE 1.40000000,100,10,'
            '-1000,0,-1400,CAD,Trades,55500001,Individual margin\n'  # pii-ok
            '2026-03-02 12:00:00 AM,2026-03-02 12:00:00 AM,DIS,NEWCO,NEWCO '
            'CORP SPINOFF ON 100 SHS FROM SEC# S012345 ABC HOLDINGS INC REC '
            '02/27/26 PAY 03/02/26,10,0,0,0,0,CAD,Other,55500001,'  # pii-ok
            'Individual margin\n')
        path = _tmp_csv(_QT_H + rows)
        try:
            evs = parse_questrade_corporate_actions(path, 'margin')
        finally:
            path.unlink()
        self.assertEqual((evs[0].source_symbol, evs[0].target_symbol),
                         ('ABC.US', 'NEWCO.US'))


if __name__ == '__main__':
    unittest.main()
