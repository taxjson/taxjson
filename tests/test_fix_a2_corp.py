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

from tax_rules import rule, rule_absent

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

    @rule("US-CORP-07")
    def test_a2_0973_us_355_basis_is_usd_on_a_cad_listing(self):
        from taxjson.lib.corp_actions import resolve_event
        ev = _event(source_symbol='PARNT.TO', target_symbol='SPNCO.TO',
                    currency='CAD', target_currency='CAD')
        rows = resolve_event(ev, 'tax_free_355', country='usa',
                             hints={'allocated_acb': 200.0},
                             fx=_flat_fx(1.40))
        self.assertEqual({r['currency'] for r in rows}, {'CAD'})
        buy = next(r for r in rows if r['action'] == 'BUYSELL')
        adj = next(r for r in rows if r['action'] == 'ADJUST')
        self.assertAlmostEqual(buy['net_amount'], 280.0, places=9)
        self.assertAlmostEqual(adj['net_amount'], -280.0, places=9)
        # A USD listing is untouched.
        rows = resolve_event(_event(), 'tax_free_355', country='usa',
                             hints={'allocated_acb': 200.0},
                             fx=_flat_fx(1.40))
        self.assertEqual([(r['currency'], r['net_amount']) for r in rows],
                         [('USD', 200.0), ('USD', -200.0)])

    @rule_absent("US-CORP-07", country="canada")
    def test_us_355_allocation_is_not_offered_in_canada(self):
        from taxjson.lib.corp_actions import resolve_event
        with self.assertRaises(KeyError):
            resolve_event(_event(), 'tax_free_355', country='canada',
                          hints={'allocated_acb': 200.0},
                          fx=_flat_fx(1.40))

    @rule_absent("CA-CORP-06", country="usa")
    def test_s86_1_cad_allocation_is_not_offered_in_the_usa(self):
        from taxjson.lib.corp_actions import resolve_event
        with self.assertRaises(KeyError):
            resolve_event(_event(), 'rollover_s_86_1', country='usa',
                          hints={'allocated_acb_cad': 1400.0},
                          fx=_flat_fx(1.40))

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


_FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,'
          'Description,Conid,Security ID,Underlying,Listing Exch,'
          'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n')


def _fii(sym, exch, isin, conid):
    return (f'Financial Instrument Information,Data,Stocks,{sym},{sym} '
            f'UNITS,{conid},{isin},,{exch},1,,,COMMON,,\n')


class TestIbCorpListingRule(unittest.TestCase):
    """A2-0209, A2-0219 (S010-06 twin): a merger of a TSX USD unit uses
    the statement parser's listing rule (QZAA.U.TO), not the currency."""

    def test_usd_unit_merger_stays_on_the_tsx_listing(self):
        m = ('QZAA.U(CA0000000501) Merged(Acquisition) WITH CA0000000502 '
             '1 for 1 ({t}, {n}, {i})')
        rows = [_ca(m.format(t='QZAA.U', n='QZAA UNITS', i='CA0000000501'),
                    -100, -1000),
                _ca(m.format(t='QZBB.U', n='QZBB UNITS', i='CA0000000502'),
                    100, 1000)]
        head = _IB_HEAD + _FII_H + _fii('QZAA.U', 'TSE', 'CA0000000501',
                                        '990000501')
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, rows, head=head)
        self.assertEqual([(e.source_symbol, e.target_symbol) for e in evs],
                         [('QZAA.U.TO', 'QZBB.U.TO')], err)

    def test_nyse_usd_trade_keeps_us(self):
        rows = [_ca(_M_OUT, -100, -2500), _ca(_M_IN, 50, 2500)]
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, rows)
        self.assertEqual([(e.source_symbol, e.target_symbol) for e in evs],
                         [('ABC.US', 'XYZ.US')], err)

    def test_a2_0556_currency_tagged_target_warns(self):
        m = ('SSX(CA0000000001) Merged(Acquisition) WITH US0000000002 '
             '1 for 16 ({t}, {n}, {i})')
        rows = [_ca(m.format(t='SSX', n='SSX GOLD', i='CA0000000001'),
                    -1600, -25920, cur='CAD'),
                _ca(m.format(t='RGX.CAD', n='RGX GOLD', i='US0000000002'),
                    100, 25840, cur='CAD')]
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, rows)
        self.assertEqual([e.target_symbol for e in evs], ['RGX.CAD.TO'])
        self.assertIn("'RGX.CAD'", err)
        self.assertIn('ticker.map', err)


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


# ============================================== election-id migration
def _spin(src, tgt, *, date='2025-06-02', ratio_old=5, action='spinoff',
          account='margin', **kw):
    return _event(date=date, action_type=action, source_symbol=src,
                  target_symbol=tgt, ratio_old=ratio_old, account=account,
                  source_isin='', target_isin='', **kw)


def _rec(eid, ev=None, election='rollover_s_86_1', summary=None):
    from taxjson.lib.corp_actions import ElectionRecord
    return ElectionRecord(event_id=eid, election=election,
                          summary=(summary if summary is not None
                                   else (ev.summary() if ev else '')),
                          hints={'allocated_acb_cad': 1234.5})


def _migrate(records, events):
    from taxjson.lib.corp_actions import Manifest
    m = Manifest({r.event_id: r for r in records})
    n = m.migrate_legacy(events)
    return m, n


class TestElectionMigration(unittest.TestCase):
    """A2-0064, A2-0217, A2-0557, A2-0975, A2-0978 and the R1-301 residue
    A2-0168 / A2-0872: migration never moves a record that is a current
    event's own, never guesses between events that share an alias, and
    lists the records it could not place."""

    def test_a2_0064_0557_current_id_is_never_adopted(self):
        a = _spin('AAA.TO', 'AAAS.TO')
        b = _spin('BBB.TO', 'BBBS.TO')
        self.assertEqual(a.event_id[-4:], b.event_id[-4:])
        for order in ([a, b], [b, a]):
            m, n = _migrate([_rec(a.event_id, a)], order)
            self.assertEqual((n, sorted(m.records)), (0, [a.event_id]))

    def test_a2_0217_0978_shared_legacy_alias_uses_the_summary(self):
        a = _spin('AAA.TO', 'AAAS.TO')
        b = _spin('BBB.TO', 'BBBS.TO')
        self.assertEqual(a.legacy_event_id(), b.legacy_event_id())
        for order in ([a, b], [b, a]):
            m, n = _migrate([_rec(b.legacy_event_id(), b)], order)
            self.assertEqual((n, sorted(m.records)), (1, [b.event_id]))

    def test_shared_legacy_alias_without_summary_is_listed(self):
        a = _spin('AAA.TO', 'AAAS.TO')
        b = _spin('BBB.TO', 'BBBS.TO')
        m, n = _migrate([_rec(b.legacy_event_id(), summary='')], [a, b])
        self.assertEqual(n, 0)
        self.assertEqual(sorted(m.records), [b.legacy_event_id()])
        self.assertTrue(any(b.legacy_event_id() in note and 'elect' in note
                            for note in m.migration_notes),
                        m.migration_notes)

    def test_a2_0975_rename_fallback_needs_same_type_and_ratio(self):
        old = _spin('ABC.TO', 'XYZ.TO', ratio_old=4, account='oldname')
        eid = old.account_salted_event_id()
        for new in (_spin('ABC.TO', 'XYZ.TO', ratio_old=2),
                    _spin('ABC.TO', 'XYZ.TO', ratio_old=4,
                          action='merger')):
            m, n = _migrate([_rec(eid, old,
                                  election='taxable_deemed_dividend')],
                            [new])
            self.assertEqual((n, sorted(m.records)), (0, [eid]),
                             m.migration_notes)
        same = _spin('ABC.TO', 'XYZ.TO', ratio_old=4)
        m, n = _migrate([_rec(eid, old, election='taxable_deemed_dividend')],
                        [same])
        self.assertEqual((n, sorted(m.records)), (1, [same.event_id]))
        self.assertTrue(m.migration_notes)
        self.assertIn('type and ratio match', m.migration_notes[0])

    def test_a2_0168_unrelated_orphan_is_never_adopted(self):
        ge = _spin('QGE.US', 'QGV.US', date='2024-05-06', currency='USD')
        new = _spin('AAA.US', 'BBB.US', date='2025-06-30', currency='USD')
        m, n = _migrate([_rec(ge.event_id, ge)], [new])
        self.assertEqual((n, sorted(m.records)), (0, [ge.event_id]))

    def test_a2_0872_salted_record_with_changed_roots_migrates(self):
        # Saved under the pre-R1-301 (account-salted) id while the parent
        # was still a broker-internal code; the extractor now names it.
        old = _spin('J000001.TO', 'AAAW.TO')
        new = _spin('AAA.TO', 'AAAW.TO')
        salted = old.account_salted_event_id()
        self.assertNotEqual(salted.rsplit('-', 1)[1],
                            new.event_id.rsplit('-', 1)[1])
        m, n = _migrate([_rec(salted, old)], [new])
        self.assertEqual((n, sorted(m.records)), (1, [new.event_id]))
        self.assertEqual(m.records[new.event_id].hints,
                         {'allocated_acb_cad': 1234.5})


# ======================================================= RBC free text
class TestRbcDecimalComma(unittest.TestCase):
    """A2-0972, A2-0976: every number in RBC's corporate-action free text
    is read whole, so a decimal comma is refused instead of read from
    after the comma (0,5 -> 5)."""

    def test_ratio_refuses_decimal_comma(self):
        from taxjson.lib import corp_actions as ca
        from taxjson.lib.brokerages.base import BrokerageParseError
        for d in ("MGR - OLDCO MERGER TO NEWCO 1,5 NEW = 1 OLD",
                  "MGR - OLDCO MERGER TO NEWCO 0,75 NEW = 1 OLD",
                  "REV - ABC REV SPLIT TO ABC 1 NEW = 2,5 OLD"):
            with self.assertRaises(BrokerageParseError, msg=d):
                ca.rbc_ratio_parts(d)
        self.assertEqual(ca.rbc_ratio_parts(
            "MGR - OLDCO MERGER TO NEWCO 1,500 NEW = 1 OLD"), (1500.0, 1.0))
        self.assertEqual(ca.rbc_ratio_parts(
            "MGR - OLDCO MERGER TO NEWCO .5 NEW = 1 OLD"), (0.5, 1.0))

    def test_stated_ratio_refuses_decimal_comma(self):
        from taxjson.lib import corp_actions as ca
        from taxjson.lib.brokerages.base import BrokerageParseError
        for d in ("REV - ABC REV SPLIT TO ABC 0,5 NEW = 1 OLD",
                  "XCH - ABC 0,963957 NEW SHS PER 1 OLD",
                  "NAC - ABC NAME CHG TO XYZ; 0,5 FOR 1",
                  "REV - ABC REV SPLIT; 1 FOR 2,5"):
            with self.assertRaises(BrokerageParseError, msg=d):
                ca._rbc_stated_ratio(d)
        self.assertAlmostEqual(ca._rbc_stated_ratio(
            "XCH - ABC .963957 NEW SHS PER 1 OLD"), 0.963957)
        self.assertEqual(ca._rbc_stated_ratio(
            "REV - ABC REV SPLIT; 1 FOR 1,000"), 0.001)

    def test_leg_strike_refuses_decimal_comma(self):
        from types import SimpleNamespace
        from taxjson.lib import corp_actions as ca
        from taxjson.lib.brokerages.base import BrokerageParseError
        leg = SimpleNamespace(desc="XCH - CALL .QTM 05/16/25 7,3 QTM ENERGY "
                                   "ADJ", symdesc='')
        with self.assertRaises(BrokerageParseError):
            ca._rbc_leg_option(leg)
        leg.desc = "XCH - CALL .QTM 05/16/25 4,875 QTM ENERGY ADJ"
        self.assertEqual(ca._rbc_leg_option(leg)[3], '4875')



class TestRbcRocClause(unittest.TestCase):
    """A2-0559 (S073-16 partial): cash on an RBC reorganization leg is a
    return of capital only under RBC's 'ROC OF C$<amount>' clause, not
    when 'ROC OF' / 'RETURN OF CAPITAL' appears in a name or a negation."""

    HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
           '"Price","Settlement Date","Value","Currency","Description"\n')

    def _row(self, date, act, sym, sd, qty, price, val, desc):
        cells = [date, act, sym, sd, qty, price, date, val, 'CAD', desc]
        return ','.join('"%s"' % c for c in cells) + '\n'

    def _parse(self, name, extra):
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        body = (self._row("June 28, 2026", "Reorganization", "Q099003",
                          f"{name} OLD", "-50", "", "100.00",
                          f"MER - {name} OLD DEFAULT: C$2.00 CASH + 1 NEW "
                          f"SHS PER 1 OLD{extra}")
                + self._row("June 28, 2026", "Reorganization", "QRX",
                            f"{name} NEW", "50", "", "0",
                            f"MGR - {name} NEW SHRS RECEIVED THRU MERGER")
                + self._row("May 3, 2026", "Buy", "QRX", f"{name} OLD", "50",
                            "20", "-1000.00", f"{name} UNSOLICITED DA"))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rbc.csv"
            p.write_text(self.HDR + body)
            txs, err = _quiet(RbcBrokerage().parse_file, p)
        return [t for t in txs if t['action'] == 'ADJUST'], err

    def test_only_the_amount_clause_is_a_roc(self):
        adj, _ = self._parse("QUARTZ CORP", " ROC OF C$2.00")
        self.assertEqual([round(t['net_amount'], 2) for t in adj], [-100.0])
        for name, extra in (("ROC OF CANADA HOLDINGS", ""),
                            ("QUARTZ CORP", " RETURN OF CAPITAL NOT "
                                            "APPLICABLE"),
                            ("QUARTZ CORP", " NO ROC OF C$ PAID")):
            adj, err = self._parse(name, extra)
            self.assertEqual(adj, [], f"{name}{extra}")
            self.assertIn('NOT booked', err)



_RBC_H = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
          '"Price","Settlement Date","Account","Value","Currency",'
          '"Description"\n')


def _rbc(date, act, sym, symdesc, qty, value, cur, desc, price=''):
    return (f'"{date} 00:00:00","{act}","{sym}","{symdesc}","{qty}",'
            f'"{price}","{date} 00:00:00","55500001","{value}","{cur}",'  # pii-ok
            f'"{desc}"\n')


def _rbc_events(td, *bodies):
    from taxjson.lib.corp_actions import parse_rbc_corporate_actions
    paths = []
    for i, b in enumerate(bodies):
        p = Path(td) / f"rbc_{i}.csv"
        p.write_text(_RBC_H + ''.join(b))
        paths.append(p)
    out, err = [], ''
    for p in paths:
        evs, e = _quiet(parse_rbc_corporate_actions, p, account='rbc',
                        context_files=paths)
        out += evs
        err += e
    return out, err


class TestRbcSpinoffParentListing(unittest.TestCase):
    def test_a2_0210_parent_is_the_listing_held_on_the_date(self):
        rows = [
            _rbc('2025-01-05', 'Buy', 'QUUX', 'QUUX CORP', '100', '-1000',
                 'CAD', 'QUUX CORP BUY', '10'),
            _rbc('2025-01-20', 'Sell', 'QUUX', 'QUUX CORP', '-100', '1100',
                 'CAD', 'QUUX CORP SALE', '11'),
            _rbc('2025-02-03', 'Buy', 'QUUX', 'QUUX CORP', '100', '-800',
                 'USD', 'QUUX CORP BUY', '8'),
            _rbc('2025-03-05', 'Reorganization', 'NEWC', 'NEWCO INC', '10',
                 '0', 'USD', 'DIS - NEWCO INC SPINOFF ON 100 SHS FROM SEC# '
                 'J000009 QUUX CORP REC 03/01/25 PAY 03/05/25')]
        with tempfile.TemporaryDirectory() as td:
            evs, err = _rbc_events(td, rows)
        self.assertEqual([(e.source_symbol, e.target_symbol) for e in evs],
                         [('QUUX.US', 'NEWC.US')], err)


    def test_a2_0213_reverse_entry_cancels_its_spinoff(self):
        spin = ('DIS - QZN CORP SPINOFF ON 100 SHS FROM SEC# 123 QZN CORP')
        rows = [
            _rbc('2025-06-09', 'Reorganization', 'QZV', 'QZV CORP', '30', '0',
                 'USD', spin),
            _rbc('2025-06-05', 'Reorganization', 'QZV', 'QZV CORP', '-30',
                 '0', 'USD', spin + ' REVERSE ENTRY'),
            _rbc('2025-06-02', 'Reorganization', 'QZV', 'QZV CORP', '30', '0',
                 'USD', spin),
            _rbc('2025-03-03', 'Buy', 'QZN', 'QZN CORP', '100', '-1009.95',
                 'USD', 'QZN CORP', '10')]
        with tempfile.TemporaryDirectory() as td:
            evs, err = _rbc_events(td, rows)
        self.assertEqual([(e.date, e.qty_received) for e in evs],
                         [('2025-06-09', 30.0)], err)
        self.assertNotIn('SHORT', err)

    def test_a2_0214_merger_legs_in_two_statements_pair(self):
        a = [_rbc('2025-12-31', 'Reorganization', 'H099006',
                  'HESSO CORPORATION', '-15', '0', 'USD',
                  'MGR - HESSO CORPORATION MERGER TO CHEVRO CORPORATION '
                  '1.025 NEW = 1 OLD'),
             _rbc('2025-03-03', 'Buy', 'HESO', 'HESSO CORPORATION', '15',
                  '-1509.95', 'USD', 'HESSO CORPORATION', '100')]
        b = [_rbc('2026-01-02', 'Reorganization', 'CVXX',
                  'CHEVRO CORPORATION', '15', '0', 'USD',
                  'MGR - CHEVRO CORPORATION SHRS RECEIVED THRU MERGER')]
        from taxjson.lib.corp_actions import combine_broker_copies
        with tempfile.TemporaryDirectory() as td:
            evs, err = _rbc_events(td, a, b)
        evs = combine_broker_copies(evs, stream=io.StringIO())
        self.assertEqual([(e.action_type, e.source_symbol, e.target_symbol,
                           e.qty_disposed, e.qty_received) for e in evs],
                         [('merger', 'HESO.US', 'CVXX.US', 15.0, 15.0)], err)
        self.assertNotIn('NO matching', err)

    def test_a2_0223_corp_symbols_match_the_parser(self):
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        from taxjson.lib.corp_actions import _rbc_ca_symbol
        b = RbcBrokerage()
        for sym, cur in (('FTN.PRA', 'CAD'), ('ABC.V', 'CAD'),
                         ('ABC.CN', 'CAD'), ('ABC.VN', 'CAD'),
                         ('ABC', 'EUR'), ('XYZ', 'USD'), ('BRK B', 'USD')):
            self.assertEqual(_rbc_ca_symbol(sym, cur),
                             b.apply_currency_suffix(sym, cur), (sym, cur))
        self.assertEqual(_rbc_ca_symbol('FTN.PRA', 'CAD'), 'FTN.PR.A.TO')

    def test_a2_0214_unpaired_merger_removal_blocks(self):
        a = [_rbc('2025-12-31', 'Reorganization', 'H099006',
                  'HESSO CORPORATION', '-15', '0', 'USD',
                  'MGR - HESSO CORPORATION MERGER TO CHEVRO CORPORATION '
                  '1.025 NEW = 1 OLD'),
             _rbc('2025-03-03', 'Buy', 'HESO', 'HESSO CORPORATION', '15',
                  '-1509.95', 'USD', 'HESSO CORPORATION', '100')]
        with tempfile.TemporaryDirectory() as td:
            evs, err = _rbc_events(td, a)
        self.assertEqual([e.action_type for e in evs], ['unsupported'], err)
        self.assertIn('NO matching', err)



class TestRbcOptionAdjustPairing(unittest.TestCase):
    def test_a2_0222_strike_pairing_is_order_independent(self):
        import itertools
        from test_fix_m_corp import _rbc_pairing, _rbc_row
        d = "XCH - CALL .TUX   03/21/25    {k} TUX CORP ADJ: SPCL CASH DIVD"
        rows = [
            _rbc_row("2024-11-15", "Reorganization", "8AAAAA1", "", "-1",
                     "0", "CAD", d.format(k=60)),
            _rbc_row("2024-11-15", "Reorganization", "8AAAAA2", "", "-1",
                     "0", "CAD", d.format(k=65)),
            _rbc_row("2024-11-15", "Reorganization", "8BBBBB1", "", "1",
                     "0", "CAD", d.format(k=55)),
            _rbc_row("2024-11-15", "Reorganization", "8BBBBB2", "", "1",
                     "0", "CAD", d.format(k=60)),
        ]
        seen = set()
        for perm in itertools.permutations(range(4)):
            with tempfile.TemporaryDirectory() as tmp:
                p = _rbc_pairing(tmp, *[rows[i] for i in perm])
            seen.add(tuple(sorted((e.removal.symbol, e.receipt.symbol)
                                  for e in p.events
                                  if e.kind == 'option_adjust')))
        self.assertEqual(seen, {(('8AAAAA1', '8BBBBB1'),
                                 ('8AAAAA2', '8BBBBB2'))})



# ================================================================ Questrade
class TestQuestradeCorpUnbooked(unittest.TestCase):
    def test_a2_0211_corp_unbooked_is_echoed_and_strict_refuses(self):
        import test_fix_rbcqt as R
        csv = (R.q(sym='PARR.TO', desc='PAR CORP RIGHTS', qty='1000',
                   price='0.10', gross='-100', comm='0', net='-100',
                   cur='CAD', td='2025-03-03')
               + R.q(td='2025-03-17', action='DIS', sym='PARR.TO',
                     desc='PAR CORP RTS DIST ON 1000 SHS REC 03/14/25 PAY '
                          '03/17/25 RIGHTS LAPSED', qty='-1000', price='0',
                     gross='0', comm='0', net='0', cur='CAD',
                     act='Dividends'))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "inputs" / "margin").mkdir(parents=True)
            (Path(td) / "home").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n'
                'province = "ON"\nbase_currency = "CAD"\n'
                'source_currencies = []\noption_grant_timing_since = 2025\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade_2025.csv").write_text(
                R.QH + csv)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("UNBOOKED", r.stdout + r.stderr)
            rs = _run_cli(root, "run", "--no-input", "--strict")
            self.assertNotEqual(rs.returncode, 0)
            self.assertIn("--strict", rs.stdout + rs.stderr)


    def test_a2_0966_internal_code_hint_names_the_booked_symbol(self):
        import test_fix_rbcqt as R
        from taxjson.lib.corp_actions import parse_questrade_corporate_actions
        leg = ('WTS QZD DEV CORP WT EXP RTS DIST ON 500 SHS FROM SEC# '
               'J000001 QZD DEVELOPMENT CORP REC 08/11/25 PAY 08/13/25')
        body = R.QH + R.q(td='2025-08-13', action='DIS', sym='D056068',
                          desc=leg, qty='50', price='0', gross='0',
                          comm='0', net='0', cur='CAD', act='Dividends')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "qt.csv"
            p.write_text(body)
            ev, err = _quiet(parse_questrade_corporate_actions, p)
            self.assertEqual([e.target_symbol for e in ev], ['D056068.TO'])
            self.assertIn('GLOBAL D056068.TO <TICKER>.TO', err)
            ev, err = _quiet(parse_questrade_corporate_actions, p,
                             renames={'D056068.TO': 'QZDW.TO'})
            self.assertNotIn('INTERNAL code', err)


    def test_a2_0980_parent_in_a_start_tt_is_named_by_ticker_map(self):
        import test_fix_rbcqt as R
        from taxjson.lib.corp_actions import parse_questrade_corporate_actions
        leg = ('WTS ALPHA CORP WT SPINOFF ON 1000 SHS FROM SEC# J000001 '
               'ALPHA CORP REC 01/20/26 PAY 01/27/26')
        body = R.QH + R.q(td='2026-01-27', action='DIS', sym='AAAW',
                          desc=leg, qty='100', price='0', gross='0',
                          comm='0', net='0', cur='CAD', act='Dividends')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "qt_2026.csv"
            p.write_text(body)
            ev, err = _quiet(parse_questrade_corporate_actions, p)
            self.assertEqual(ev[0].source_symbol, 'J000001')
            self.assertIn('GLOBAL J000001 <PARENT>.TO', err)
            ev, err = _quiet(parse_questrade_corporate_actions, p,
                             renames={'J000001': 'AAA.TO'})
            self.assertEqual(ev[0].source_symbol, 'AAA.TO')
            self.assertNotIn('not traded', err)



class TestElectionWording(unittest.TestCase):
    def test_a2_0558_rows_name_the_elected_key(self):
        from taxjson.lib.corp_actions import resolve_event
        for country, ev, key in (
                ('canada', _event(), 'taxable_deemed_dividend'),
                ('usa', _event(), 'taxable_distribution_301'),
                ('usa', _event(action_type='merger', qty_disposed=100),
                 'taxable_exchange')):
            rows, _ = _quiet(resolve_event, ev, key,
                             hints={'fmv_per_share': 5.0}, country=country)
            for r in rows:
                self.assertNotIn('election=none', r['description'])
                self.assertIn(f'election={key}', r['description'])

    def test_a2_0974_zero_fmv_names_the_saved_zero(self):
        from taxjson.lib.corp_actions import resolve_event
        ev = _event(action_type='merger', qty_disposed=100,
                    target_symbol='NEW.US')
        _, err = _quiet(resolve_event, ev, 'taxable_disposition',
                        hints={'fmv_per_share': 0.0})
        self.assertIn('fmv_per_share=0)', err)
        self.assertNotIn('no fmv_per_share hint was given', err)
        self.assertIn(f'taxjson elect margin --set {ev.event_id}=', err)
        _, err = _quiet(resolve_event, ev, 'taxable_disposition', hints={})
        self.assertIn('no fmv_per_share hint was given', err)



class TestUsBootCurrencies(unittest.TestCase):
    @rule("US-CORP-05")
    def test_a2_0216_hints_are_combined_in_usd(self):
        from taxjson.lib.corp_actions import resolve_event
        ev = _event(action_type='merger', source_symbol='OLDC.TO',
                    target_symbol='NEWC.US', currency='CAD',
                    target_currency='USD', qty_disposed=100,
                    qty_received=100)
        rows, err = _quiet(resolve_event, ev, 'reorg_368_boot',
                           country='usa', fx=_flat_fx(1.37),
                           hints={'cash_boot': 500.0,
                                  'source_basis_total': 730.0,
                                  'fmv_per_share': 10.0})
        sell = next(r for r in rows if r['quantity'] < 0)
        buy = next(r for r in rows if r['quantity'] > 0)
        # Amount realized (1000 of new shares + 500 boot, USD) on the
        # CAD leg; the boot beside it; the new shares' value in USD. The
        # engine computes the per-lot gain from its own lots (A2-0066).
        self.assertEqual(sell['currency'], 'CAD')
        self.assertAlmostEqual(sell['net_amount'], (1000 + 500) * 1.37,
                               places=6)
        self.assertAlmostEqual(sell['gross_amount'], 500 * 1.37, places=6)
        self.assertEqual(buy['currency'], 'USD')
        self.assertAlmostEqual(buy['net_amount'], 1000.0, places=6)

    def test_same_currency_boot_unchanged(self):
        from taxjson.lib.corp_actions import resolve_event
        ev = _event(action_type='merger', source_symbol='OLDC.US',
                    target_symbol='NEWC.US', qty_disposed=100,
                    qty_received=100)
        for fx in (None, _flat_fx(1.37)):
            rows, _ = _quiet(resolve_event, ev, 'reorg_368_boot',
                             country='usa', fx=fx,
                             hints={'cash_boot': 500.0,
                                    'source_basis_total': 730.0,
                                    'fmv_per_share': 10.0})
            self.assertEqual([(r['currency'], round(r['net_amount'], 6))
                              for r in rows],
                             [('USD', 1500.0), ('USD', 1000.0)])


if __name__ == "__main__":
    unittest.main()
