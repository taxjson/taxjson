"""Pins for re-audit-2 test-gap findings (fix lists tests-pins-04/05/06):
corporate-action elections, IB / Questrade corporate-action parsing,
corp views and the close-year hand-off. Each test fails when the
mutant named in its finding is applied. All data is synthetic: fake
tickers, fake ISINs, fake broker account ids."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _event(**kw):
    from taxjson.lib.corp_actions import CorporateAction
    base = dict(date='2025-06-02', time='09:30:00', action_type='spinoff',
                source_symbol='PARX.TO', source_isin='',
                target_symbol='SPNC.TO', target_isin='',
                ratio_new=1, ratio_old=5, qty_disposed=0, qty_received=20,
                fmv=0.0, currency='CAD', target_currency='CAD',
                account='margin')
    base.update(kw)
    return CorporateAction(**base)


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


# ================================================ election-id migration
class TestElectionMigrationPins(unittest.TestCase):
    """A2-0168, A2-0169, A2-0505, A2-1536: Manifest.migrate_legacy never
    hands a stale record to an unrelated event, never guesses between
    two re-symbolled candidates, and migrates every event of a run that
    mixes id schemes."""

    def test_a2_0168_0169_same_day_unrelated_record_is_not_adopted(self):
        # A stale election of another event of the same day, type and
        # ratio (its saved summary passes the type/ratio check) sits in
        # the manifest; the run's only event is a different pair of
        # symbols with its own ISINs, so no alias or suffix links them.
        stale = _event(source_symbol='AAA.TO', target_symbol='AAAS.TO',
                       source_isin='CA0000000A11', target_isin='CA0000000A22')
        new = _event(source_symbol='BBB.TO', target_symbol='BBBS.TO',
                     source_isin='CA0000000B11', target_isin='CA0000000B22')
        self.assertNotEqual(stale.event_id[-4:], new.event_id[-4:])
        m, n = _migrate([_rec(stale.event_id, stale)], [new])
        self.assertEqual((n, sorted(m.records)), (0, [stale.event_id]),
                         m.migration_notes)

    def test_a2_0169_mixed_id_schemes_all_migrate(self):
        # A has its current-id record; B a pre-2026-07 opaque id; C an
        # account-salted (pre-R1-301) id; X and the Y pair have no record
        # at all; D and E were saved under another account name. Every
        # saved election must reach its event whatever comes before it.
        def ev(src, tgt, isin, **kw):
            return _event(source_symbol=src, target_symbol=tgt,
                          source_isin=isin, target_isin=isin + 'T', **kw)
        a = ev('AAA.TO', 'AAAS.TO', 'CA00000000A1')
        # R's record was saved while its parent was a broker-internal
        # code: only the re-symbolled lookup finds it.
        r = ev('RRR.TO', 'RRRS.TO', 'CA00000000R1')
        r_old = ev('J000009.TO', 'RRRS.TO', 'CA00000000R1')
        b = ev('BBB.TO', 'BBBS.TO', 'CA00000000B1')
        c = ev('CCC.TO', 'CCCS.TO', 'CA00000000C1')
        x = ev('XXX.TO', 'XXXS.TO', 'CA00000000X1')
        y1 = ev('YYY.TO', 'YYYS.TO', 'CA00000000Y1')
        y2 = ev('YYY.TO', 'YYYS.TO', 'CA00000000Y1', ratio_old=4)
        d = ev('DDD.TO', 'DDDS.TO', 'CA00000000D1')
        e = ev('EEE.TO', 'EEES.TO', 'CA00000000E1')
        d_old = ev('DDD.TO', 'DDDS.TO', 'CA00000000D1', account='oldname')
        e_old = ev('EEE.TO', 'EEES.TO', 'CA00000000E1', account='oldname')
        recs = [_rec(a.event_id, a, election='ignore'),
                _rec(r_old.event_id, r_old, election='taxable_spinoff'),
                _rec(b.legacy_event_id(), b, election='taxable_disposition'),
                _rec(c.account_salted_event_id(), c,
                     election='taxable_deemed_dividend'),
                _rec(d_old.account_salted_event_id(), d_old,
                     election='rollover_s_85_1_5'),
                _rec(e_old.account_salted_event_id(), e_old,
                     election='rollover_s_86_1')]
        m, n = _migrate(recs, [a, r, b, c, x, y1, y2, d, e])
        self.assertEqual(n, 5, m.migration_notes)
        got = {k: v.election for k, v in m.records.items()}
        self.assertEqual(got, {a.event_id: 'ignore',
                               r.event_id: 'taxable_spinoff',
                               b.event_id: 'taxable_disposition',
                               c.event_id: 'taxable_deemed_dividend',
                               d.event_id: 'rollover_s_85_1_5',
                               e.event_id: 'rollover_s_86_1'})

    def test_a2_0169_salted_alias_is_tried_after_the_opaque_one(self):
        # C and Z are ISIN-less spin-offs of one day and ratio: their
        # opaque pre-2026-07 ids are equal, their account-salted ids are
        # not. C's election was saved (without a summary) under its
        # salted id. The opaque alias misses; the salted alias must
        # still be tried — the re-symbolled lookup cannot place it
        # (Z shares the suffix) and the fallback needs a summary.
        c = _event(source_symbol='CCC.TO', target_symbol='CCCS.TO')
        z = _event(source_symbol='ZZZ.TO', target_symbol='ZZZS.TO')
        self.assertEqual(c.legacy_event_id(), z.legacy_event_id())
        rec = _rec(c.account_salted_event_id(), summary='')
        m, n = _migrate([rec], [c, z])
        self.assertEqual((n, sorted(m.records)), (1, [c.event_id]),
                         m.migration_notes)

    def test_a2_0505_1536_two_resymbolled_candidates_adopt_neither(self):
        # Two saved records share the event's date and hash suffix and
        # differ only in their readable roots: which one is this event's
        # is a guess, so neither moves.
        old1 = _event(source_symbol='J000001.TO', target_symbol='AAAW.TO')
        old2 = _event(source_symbol='J000002.TO', target_symbol='AAAW.TO')
        new = _event(source_symbol='AAA.TO', target_symbol='AAAW.TO')
        self.assertEqual(old1.event_id[-4:], new.event_id[-4:])
        self.assertEqual(old2.event_id[-4:], new.event_id[-4:])
        recs = [_rec(old1.event_id, old1, election='rollover_s_85_1_5'),
                _rec(old2.event_id, old2, election='taxable_disposition')]
        m, n = _migrate(recs, [new])
        self.assertEqual(n, 0, m.migration_notes)
        self.assertNotIn(new.event_id, m.records)
        self.assertEqual(sorted(m.records),
                         sorted([old1.event_id, old2.event_id]))
        # control: with one candidate it migrates
        m, n = _migrate(recs[:1], [new])
        self.assertEqual((n, sorted(m.records)), (1, [new.event_id]))


# ============================================ IB corporate-action parsing
_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,U5550001\n'  # pii-ok
            'Account Information,Data,Base Currency,CAD\n')
_IB_CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
            'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
            'Code\n')
_SPIN = ('PARNT(US0000000777) Spinoff  1 for 5 '
         '(SPNCO, SPINCO CORP, US0000000778)')


def _ca(desc, qty, value, when='2025-03-03, 20:25:00', cur='USD', code=''):
    return (f'Corporate Actions,Data,Stocks,{cur},{when[:10]},'
            f'"{when}","{desc}",{qty},0,{value},0,{code}\n')


def _ib_files(tmp, *bodies):
    paths = []
    for i, b in enumerate(bodies):
        p = Path(tmp) / f"ib_{i}.csv"
        p.write_text(_IB_HEAD + _IB_CA_H + ''.join(b))
        paths.append(p)
    return paths


def _ib_events(tmp, *bodies):
    from taxjson.bin.taxjson_corp_actions import extract_events
    from taxjson.lib.corp_actions import (combine_broker_copies,
                                          parse_ib_corporate_actions)
    paths = _ib_files(tmp, *bodies)
    buf = io.StringIO()
    with redirect_stderr(buf):
        evs = extract_events(parse_ib_corporate_actions, paths, 'margin')
    return combine_broker_copies(evs, stream=io.StringIO()), buf.getvalue()


def _cad(amount, a, b, _d):
    rate = {'CAD': 1.0, 'USD': 1.40}
    return amount * rate[a.upper()] / rate[b.upper()]


class TestIbSpinoffParsing(unittest.TestCase):
    """A2-0504, A2-0896, A2-0871, A2-1601."""

    @rule("CA-CORP-06")
    def test_a2_0504_dotted_parent_takes_the_s86_1_reduction(self):
        desc = ('PAR.B(CA0000000777) Spinoff  1 for 5 '
                '(SPNCO, SPINCO CORP, CA0000000778)')
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, [_ca(desc, 20, 1000, cur='CAD')])
        self.assertEqual(len(evs), 1, err)
        ev = evs[0]
        self.assertEqual((ev.source_symbol, ev.target_symbol),
                         ('PAR.B.TO', 'SPNCO.TO'))
        from taxjson.lib.corp_actions import resolve_event
        rows = resolve_event(ev, 'rollover_s_86_1',
                             hints={'allocated_acb_cad': 1000.0}, fx=_cad)
        adj = [r for r in rows if r['action'] == 'ADJUST']
        self.assertEqual([r['symbol'] for r in adj], ['PAR.B.TO'])
        self.assertAlmostEqual(adj[0]['net_amount'], -1000.0, places=6)

    @rule("CA-CORP-07")
    def test_a2_0896_dotted_target_is_a_spinoff_event(self):
        desc = ('PARNT(CA0000000777) Spinoff  1 for 5 '
                '(ABC.WT, SPINCO CORP WTS, CA0000000778)')
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, [_ca(desc, 20, 900, cur='CAD')])
        self.assertEqual([(e.action_type, e.source_symbol, e.target_symbol,
                           e.qty_received, e.fmv) for e in evs],
                         [('spinoff', 'PARNT.TO', 'ABC.WT.TO', 20.0, 900.0)],
                         err)
        from taxjson.lib.corp_actions import resolve_event
        rows = resolve_event(evs[0], 'taxable_deemed_dividend', fx=_cad)
        self.assertEqual(sorted((r['action'], r['symbol']) for r in rows),
                         [('BUYSELL', 'ABC.WT.TO'),
                          ('DIVIDEND', 'ABC.WT.TO')])

    def test_a2_0871_1601_decimal_comma_quantity_or_value_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        for qty, val in (('"100,5"', '2000'), ('20', '"25840,67"')):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(BrokerageParseError, msg=(qty, val)):
                    _ib_events(tmp, [_ca(_SPIN, qty, val)])
        # thousands separators are still read whole
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, [_ca(_SPIN, '"1,000"', '"25,840.67"')])
        self.assertEqual([(e.qty_received, e.fmv) for e in evs],
                         [(1000.0, 25840.67)], err)


if __name__ == '__main__':
    unittest.main()
