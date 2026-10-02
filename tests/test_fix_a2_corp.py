"""Regression tests for the second re-audit's corporate-action findings
(fix lists corp-actions-01 / corp-actions-02, A2-NNNN ids). All data is
synthetic: fake tickers, fake ISINs, fake broker account ids."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
             "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
             "Account #,Activity Type,Account Type")


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


def _env(home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["TAXJSON_OFFLINE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=str(REPO_ROOT), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(root.parent / "home"))


def _qt_project(td, csv_text, *, year=2026, rate=1.40):
    """A one-account (margin, Questrade) Canada project with a flat
    USD->CAD to_base.csv pre-seeded, so no FX download happens."""
    root = Path(td) / "proj"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "work").mkdir()
    (Path(td) / "home").mkdir()
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        f'option_grant_timing_since = {year}\n'
        '[accounts.margin]\ntype = "taxable"\n')
    d, lines = date(year - 1, 12, 1), []
    while d <= max(date.today(), date(year, 12, 31)):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD {rate:.2f} yahoo\n")
        d += timedelta(days=1)
    (root / "work" / "to_base.csv").write_text("".join(lines))
    (root / "inputs" / "margin" / f"qt_{year}.csv").write_text(
        QT_HEADER + "\n" + csv_text.strip() + "\n")
    return root


def _event(**kw):
    from taxjson.lib.corp_actions import CorporateAction
    base = dict(date='2026-06-15', time='09:30:00', action_type='spinoff',
                source_symbol='PARX.US', source_isin='',
                target_symbol='SPNC.US', target_isin='',
                ratio_new=1, ratio_old=5, qty_disposed=0, qty_received=20,
                fmv=0.0, currency='USD', target_currency='USD',
                account='margin')
    base.update(kw)
    return CorporateAction(**base)


def _flat_fx(rate):
    """fx(amount, from, to, date) with USD->CAD = rate."""
    to_cad = {'CAD': 1.0, 'USD': rate}

    def fx(amount, a, b, _d):
        return amount * to_cad[a.upper()] / to_cad[b.upper()]
    return fx


# ================================================ s.86.1 on a USD parent
SPIN_CSV = """\
2026-01-05 12:00:00 AM,2026-01-06 12:00:00 AM,Buy,PARX,PARENTCO INC WE ACTED AS AGENT,100,50,-5000,0,-5000,USD,55500001,Trades,Individual margin
2026-06-15 12:00:00 AM,2026-06-15 12:00:00 AM,DIS,SPNC,SPINCO INC SPINOFF FROM SEC# X123456 PARENTCO INC REC 06/01/26 PAY 06/15/26 ON 100 SHS,20,0,0,0,0,USD,55500001,Dividends,Individual margin
"""  # pii-ok (synthetic account id)


class TestS861UsdParent(unittest.TestCase):
    """A2-0002 (regression of S072-15), A2-0215, A2-0967."""

    @rule("CA-CORP-06")
    def test_cad_allocation_books_in_the_listing_currency(self):
        # The CAD figure is expressed in the parent's listing currency at
        # the spin-off date's rate, so the conversion stage gives back
        # exactly the CAD amount and the native view stays one currency.
        from taxjson.lib.corp_actions import resolve_event
        rows = resolve_event(_event(), 'rollover_s_86_1',
                             hints={'allocated_acb_cad': 1400.0},
                             fx=_flat_fx(1.40))
        buy = next(r for r in rows if r['action'] == 'BUYSELL')
        adj = next(r for r in rows if r['action'] == 'ADJUST')
        self.assertEqual(buy['currency'], 'USD')
        self.assertAlmostEqual(buy['net_amount'], 1000.0, places=9)
        self.assertAlmostEqual(buy['price'], 50.0, places=9)
        self.assertEqual((adj['symbol'], adj['currency']),
                         ('PARX.US', 'USD'))
        self.assertAlmostEqual(adj['net_amount'], -1000.0, places=9)
        self.assertIn('1400.00 CAD', buy['description'])

    def test_no_rate_keeps_cad_rows(self):
        from taxjson.lib.corp_actions import resolve_event
        rows, err = _quiet(resolve_event, _event(), 'rollover_s_86_1',
                           hints={'allocated_acb_cad': 1400.0},
                           fx=lambda *a: None)
        self.assertEqual({r['currency'] for r in rows}, {'CAD'})
        self.assertIn('no CAD->USD rate', err)

    def test_mixed_currency_guard_counts_adjust_rows(self):
        from taxjson.bin.taxjson_run import _raw_mixed_currency_symbols
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "raw.json"
            p.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "symbol": "PARX.US",
                 "currency": "USD", "quantity": 100, "net_amount": 5000},
                {"action": "ADJUST", "symbol": "PARX.US",
                 "currency": "CAD", "net_amount": -1000},
            ]}))
            self.assertEqual(_raw_mixed_currency_symbols(p), ["PARX.US"])

    @rule("CA-CORP-06")
    def test_run_builds_the_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = _qt_project(td, SPIN_CSV)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr[-2000:])
            pend = json.loads((root / "work" / "pending_elections.json")
                              .read_text())
            eid = pend["accounts"]["margin"]["pending"][0]["event_id"]
            r = _run_cli(root, "elect", "margin", "--set",
                         f"{eid}=rollover_s_86_1", "--hint",
                         "allocated_acb_cad=1400")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertNotIn("raw holdings skipped", r.stderr)
            self.assertTrue((root / "reports" /
                             "margin_holdings.toml").exists())
            g = json.loads((root / "work" / "margin_gains.json").read_text())
            inv = {h["symbol"]: h for h in g["inventory"]}
            par = inv.get("PARX.US") or {}
            spn = inv.get("SPNC.US") or {}
            self.assertAlmostEqual(spn["total_cost"], 1400.0, places=2)
            self.assertAlmostEqual(par["total_cost"], 7000.0 - 1400.0,
                                   places=2)


# ======================================================= IB cancellations
_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,U5550001\n'  # pii-ok
            'Account Information,Data,Base Currency,CAD\n')
_IB_CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
            'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
            'Code\n')
_MRG = ('ABC(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2 '
        '({t}, {n}, {i})')
_M_OUT = _MRG.format(t='ABC', n='ABC CORP', i='CA0000000001')
_M_IN = _MRG.format(t='XYZ', n='XYZ CORP', i='CA0000000002')
_SPIN = ('PARNT(US0000000777) Spinoff  1 for {r} '
         '(SPNCO, SPINCO CORP, US0000000778)')


def _ca(desc, qty, value, when='2025-03-03, 20:25:00', cur='USD', code=''):
    return (f'Corporate Actions,Data,Stocks,{cur},{when[:10]},'
            f'"{when}","{desc}",{qty},0,{value},0,{code}\n')


def _ib_events(tmp, *bodies, head=_IB_HEAD):
    from taxjson.bin.taxjson_corp_actions import extract_events
    from taxjson.lib.corp_actions import (combine_broker_copies,
                                          parse_ib_corporate_actions)
    paths = []
    for i, b in enumerate(bodies):
        p = Path(tmp) / f"ib_{i}.csv"
        p.write_text(head + _IB_CA_H + ''.join(b))
        paths.append(p)
    evs, err = _quiet(extract_events, parse_ib_corporate_actions, paths,
                      'margin')
    return combine_broker_copies(evs, stream=io.StringIO()), err


def _shape(evs):
    return sorted((e.action_type, e.date, e.qty_disposed, e.qty_received,
                   round(e.target_fmv, 2)) for e in evs)


class TestIbCancellations(unittest.TestCase):
    """A2-0018, A2-0019, A2-0020, A2-0068, A2-0069, A2-0212, A2-0220,
    A2-0970, A2-0971: a `Ca` row cancels its original wherever it sits
    among the account's statements, for mergers, spin-offs and the
    merger shapes taxjson cannot book."""

    def test_merger_cancelled_and_rebooked_same_statement(self):
        rows = [_ca(_M_OUT, -100, -2500), _ca(_M_IN, 50, 2500),
                _ca(_M_OUT, 100, 2500, code='Ca'),
                _ca(_M_IN, -50, -2500, code='Ca'),
                _ca(_M_OUT, -100, -2600, when='2025-03-04, 20:25:00'),
                _ca(_M_IN, 50, 2600, when='2025-03-04, 20:25:00')]
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, rows)
        self.assertEqual(_shape(evs),
                         [('merger', '2025-03-04', 100.0, 50.0, 2600.0)],
                         err)

    def test_merger_cancelled_without_rebook_is_no_event(self):
        rows = [_ca(_M_OUT, -100, -2500), _ca(_M_IN, 50, 2500),
                _ca(_M_OUT, 100, 2500, code='Ca'),
                _ca(_M_IN, -50, -2500, code='Ca')]
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, rows)
        self.assertEqual(evs, [], err)

    def test_merger_cancelled_in_the_next_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(
                tmp, [_ca(_M_OUT, -100, -2500), _ca(_M_IN, 50, 2500)],
                [_ca(_M_OUT, 100, 2500, when='2026-01-05, 20:25:00',
                     code='Ca'),
                 _ca(_M_IN, -50, -2500, when='2026-01-05, 20:25:00',
                     code='Ca')])
        self.assertEqual(evs, [], err)

    def test_code_tokens_split_like_the_statement_parser(self):
        for code in ('"Ca,P"', 'Ca P', 'P;Ca'):
            rows = [_ca(_M_OUT, -100, -2500), _ca(_M_IN, 50, 2500),
                    _ca(_M_OUT, 100, 2500, code=code),
                    _ca(_M_IN, -50, -2500, code=code)]
            with tempfile.TemporaryDirectory() as tmp:
                evs, err = _ib_events(tmp, rows)
            self.assertEqual(evs, [], f"{code}: {err}")
            self.assertNotIn('SHORT', err)

    def test_spinoff_cancelled_in_the_next_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(
                tmp, [_ca(_SPIN.format(r=5), 20, 400,
                          when='2025-12-15, 20:25:00')],
                [_ca(_SPIN.format(r=5), -20, -400,
                     when='2026-01-10, 20:25:00', code='Ca'),
                 _ca(_SPIN.format(r=10), 10, 200,
                     when='2026-01-10, 20:25:00')])
        self.assertEqual(_shape(evs),
                         [('spinoff', '2026-01-10', 0.0, 10.0, 200.0)], err)

    def test_overlapping_vintages_keep_the_rebook(self):
        h1 = [_ca(_SPIN.format(r=5), 20, 400, when='2025-06-02, 20:25:00')]
        fy = h1 + [_ca(_SPIN.format(r=5), -20, -400,
                       when='2025-06-05, 20:25:00', code='Ca'),
                   _ca(_SPIN.format(r=4), 25, 500,
                       when='2025-06-05, 20:25:00')]
        for order in ((h1, fy), (fy, h1)):
            for head in (_IB_HEAD, ''):
                with tempfile.TemporaryDirectory() as tmp:
                    evs, err = _ib_events(tmp, *order, head=head)
                self.assertEqual(
                    _shape(evs),
                    [('spinoff', '2025-06-05', 0.0, 25.0, 500.0)], err)

    def test_cancelled_unsupported_merger_is_no_event(self):
        odd = ('SSX(CA0000000301) Merged(Acquisition) WITH CA0000000302 '
               '1 for 2 AND CAD 2.50 (SSX, SSX CORP, CA0000000301)')
        rows = [_ca(odd, -1600, -16000, cur='CAD'),
                _ca(odd, 1600, 16000, cur='CAD', code='Ca')]
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, rows)
        self.assertEqual(evs, [], err)
        self.assertNotIn('by hand', err)


class TestIbDateTimeOrder(unittest.TestCase):
    def test_a2_0984_unpadded_hour_sorts_as_a_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, [
                _ca(_M_OUT, -100, -2500, when='2026-05-14, 20:25:00'),
                _ca(_M_IN, 50, 2500, when='2026-05-14, 9:30:00')])
        self.assertEqual([(e.date, e.time) for e in evs],
                         [('2026-05-14', '09:30:00')], err)


class TestCombineBrokerCopies(unittest.TestCase):
    def test_a2_0977_order_independent(self):
        import itertools
        from taxjson.lib.corp_actions import combine_broker_copies
        a = _event(action_type='merger', qty_disposed=1600,
                   qty_received=100, broker_account='U5550001')  # pii-ok
        b = _event(action_type='merger', qty_disposed=800, qty_received=50,
                   broker_account='')
        c = _event(action_type='merger', qty_disposed=800, qty_received=50,
                   broker_account='U5550001')  # pii-ok
        got = set()
        for perm in itertools.permutations([a, b, c]):
            out = combine_broker_copies(list(perm), stream=io.StringIO())
            got.add(tuple(sorted((e.qty_disposed, e.qty_received)
                                 for e in out)))
        self.assertEqual(len(got), 1, got)


if __name__ == "__main__":
    unittest.main()
