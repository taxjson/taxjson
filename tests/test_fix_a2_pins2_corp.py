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
    """A2-0504, A2-0896, A2-0518, A2-0871, A2-1601."""

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

    def test_a2_0518_cancellation_matches_its_currency(self):
        # One spin-off delivered on both listings; IB cancels the CAD
        # leg. The USD event survives; the CAD one is gone.
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _ib_events(tmp, [
                _ca(_SPIN, 20, 400, cur='USD'),
                _ca(_SPIN, 20, 540, cur='CAD'),
                _ca(_SPIN, -20, -540, cur='CAD', code='Ca')])
        self.assertEqual([(e.source_symbol, e.target_symbol, e.qty_received,
                           e.fmv, e.currency) for e in evs],
                         [('PARNT.US', 'SPNCO.US', 20.0, 400.0, 'USD')], err)

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


# ============================================== close-year and handoff
_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if action == "Buy" else gross
    return (f"{trade} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,55500001,"  # pii-ok
            f"Trades,Individual\n")


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(root, year, files, extra_settings="", acct="margin"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / acct).mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n{extra_settings}'
        f'[accounts.{acct}]\ntype = "taxable"\n')
    for name, text in files.items():
        (root / "inputs" / acct / name).write_text(text)
    return root


# 2025 (settlement basis): XYZ bought, 40 sold Dec 31 settling Jan 2
# 2026; ABC bought and sold in December; LOS sold at a 200 loss on
# Apr 7 and bought back Apr 21 (a superficial loss, replacement still
# held at Dec 31).
Y2025 = (_QT_HEADER
         + _qt("2025-03-03", "2025-03-04", "Buy", "XYZ.TO", 100, 10.0)
         + _qt("2025-12-31", "2026-01-02", "Sell", "XYZ.TO", -40, 12.0)
         + _qt("2025-06-03", "2025-06-04", "Buy", "ABC.TO", 50, 20.0)
         + _qt("2025-12-29", "2025-12-30", "Sell", "ABC.TO", -50, 22.0)
         + _qt("2025-01-06", "2025-01-07", "Buy", "LOS.TO", 100, 10.0)
         + _qt("2025-04-07", "2025-04-08", "Sell", "LOS.TO", -100, 8.0)
         + _qt("2025-04-21", "2025-04-22", "Buy", "LOS.TO", 100, 9.0))

_OPEN = ("BUYSELL 2025-03-04 09:30:00 XYZ.TO 100 CAD 10 1000 0\n"
         "BUYSELL 2025-04-22 09:30:00 LOS.TO 100 CAD 11 1100 0\n")


class TestCloseYearAndHandoffPins(unittest.TestCase):
    """A2-0515, A2-0881 (the year-end cost carries the s.53(1)(f)
    addition and the deferral), A2-1557 (the record's own date basis
    and filed dispositions), A2-1567 (the matching windows)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.p25 = _project(cls.base / "p2025", 2025,
                           {"questrade.csv": Y2025})
        r = _run_cli(cls.p25, "run", "--no-input")
        assert r.returncode == 0, r.stderr
        r = _run_cli(cls.p25, "close-year")
        assert r.returncode == 0, r.stderr
        cls.rec_path = cls.p25 / "filed" / "2025.json"
        cls.record = json.loads(cls.rec_path.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _p26(self, name, tt, extra="", record=None):
        rec = record or self.rec_path
        p = _project(self.base / name, 2026, {"start.tt": tt},
                     f'prior_year_record = "{rec}"\n{extra}')
        r = _run_cli(p, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        return p

    def _handoff(self, p):
        r = _run_cli(p, "handoff", "--json")
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r.returncode, json.loads(r.stdout)

    @rule("CA-RPT-08")
    def test_a2_0515_0881_year_end_cost_carries_the_denied_loss(self):
        los = self.record["year_end"]["equity"]["LOS.TO"]
        self.assertAlmostEqual(los["qty"], 100.0)
        # 900 paid for the replacement + the 200 denied (s.53(1)(f))
        self.assertAlmostEqual(los["acb"], 1100.0, places=2)
        self.assertAlmostEqual(los["deferred"], 200.0, places=2)
        # and the next year opening at that cost hands off cleanly
        p = self._p26("clean", _OPEN
                      + "BUYSELL 2026-01-02 09:30:00 XYZ.TO -40 CAD 12 480 0\n")
        rc, rep = self._handoff(p)
        self.assertEqual((rc, rep["positions"]), (0, []), rep)

    @rule("CA-RPT-08")
    def test_a2_1557_1567_record_basis_and_windows(self):
        # This project is on TRADE dates; the 2025 record on settlement
        # dates. The record's Dec-31 sale settling in 2026 is not here
        # (only a distinct same-size XYZ sale on Jan 20): missed. ABC
        # bought and sold Jan 5/7 for the closed sale's proceeds is a
        # distinct sale 8 days after it settled: not a double.
        self.assertEqual(self.record["date_basis"], "settle")
        p = self._p26("windows", _OPEN
                      + "BUYSELL 2026-01-20 09:30:00 XYZ.TO -40 CAD 12 480 0\n"
                      + "BUYSELL 2026-01-05 09:30:00 ABC.TO 50 CAD 20 1000 0\n"
                      + "BUYSELL 2026-01-07 09:30:00 ABC.TO -50 CAD 22 1100 0\n",
                      extra='tax_date = "trade"\n')
        rc, rep = self._handoff(p)
        self.assertEqual((rep["record_basis"], rep["basis"]),
                         ("settle", "trade"))
        self.assertEqual([m["symbol"] for m in rep["missed"]], ["XYZ.TO"],
                         rep)
        self.assertEqual(rep["double"], [], rep)

    def test_a2_1557_doubles_are_checked_against_the_filed_return(self):
        # The 2025 return was prepared with another tool and reported
        # DEF, not ABC: a January ABC sale like the books' is no double;
        # a January DEF sale like the filed one is.
        rec = dict(self.record)
        rec["filed_dispositions"] = [{
            "symbol": "DEF.TO", "date": "2025-12-29",
            "date_settle": "2025-12-30", "qty": 30.0, "proceeds": 600.0,
            "cost": 450.0, "gain": 150.0}]
        rp = self.base / "rec_filed.json"
        rp.write_text(json.dumps(rec))
        p = self._p26("filed", _OPEN
                      + "BUYSELL 2026-01-02 09:30:00 XYZ.TO -40 CAD 12 480 0\n"
                      + "BUYSELL 2026-01-02 09:30:00 ABC.TO 50 CAD 20 1000 0\n"
                      + "BUYSELL 2026-01-02 10:30:00 ABC.TO -50 CAD 22 1100 0\n"
                      + "BUYSELL 2026-01-02 09:30:00 DEF.TO 30 CAD 15 450 0\n"
                      + "BUYSELL 2026-01-02 10:30:00 DEF.TO -30 CAD 20 600 0\n",
                      record=rp)
        rc, rep = self._handoff(p)
        self.assertEqual([d["symbol"] for d in rep["double"]], ["DEF.TO"],
                         rep)

    def test_a2_1567_early_january_rows_of_the_closed_inputs(self):
        # The closed project's inputs held January rows (they count in
        # 2026, so the 2025 return left them out). One dated Jan 5 that
        # this project lacks is in neither return; one dated Jan 20 is
        # past the window the two projects overlap in, not checked.
        rec = dict(self.record)
        rows = [dict(group="equity", account="margin", action="BUYSELL",
                     symbol="QQQ.TO", date=d, date_settle=d, quantity=10.0,
                     net=-100.0, tax_date=d)
                for d in ("2026-01-05", "2026-01-20")]
        rec["boundary_rows"] = list(rec.get("boundary_rows") or []) + rows
        rp = self.base / "rec_boundary.json"
        rp.write_text(json.dumps(rec))
        p = self._p26("boundary", _OPEN
                      + "BUYSELL 2026-01-02 09:30:00 XYZ.TO -40 CAD 12 480 0\n",
                      record=rp)
        rc, rep = self._handoff(p)
        self.assertEqual([(b["symbol"], b["date"]) for b in rep["boundary"]],
                         [("QQQ.TO", "2026-01-05")], rep)


_GENERIC_MAP = """[columns]
date="Date"
settle="Settle"
action="Type"
symbol="Ticker"
quantity="Shares"
price="Price"
amount="Amount"
currency="Currency"
[actions]
"BUY"="buy"
"SELL"="sell"
"""


class TestCloseYearAccountOrder(unittest.TestCase):
    """A2-1556: the close-year snapshot blends the taxable accounts in
    taxjson.toml order, as the run does — rows of two accounts at one
    moment (the generic importer prints no clock time) follow it."""

    @rule("CA-DATE-14")
    def test_a2_1556_year_end_follows_the_toml_account_order(self):
        rows = {
            "zeta": ["2025-01-06,2025-01-07,BUY,XYZ.TO,100,10,-1000,CAD",
                     "2025-03-03,2025-03-04,SELL,XYZ.TO,-100,12,1200,CAD"],
            "alpha": ["2025-03-03,2025-03-04,BUY,XYZ.TO,100,20,-2000,CAD"],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            root.mkdir()
            # zeta (the sale) is listed first: it sells from the shares
            # held before alpha's same-moment buy — a 200 gain, nothing
            # denied. Alphabetical order would sell from the blended
            # pool at a loss and record a deferral the return never had.
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n'
                '[accounts.zeta]\ntype = "taxable"\n\n'
                '[accounts.alpha]\ntype = "taxable"\n')
            for a, rs in rows.items():
                d = root / "inputs" / a
                d.mkdir(parents=True)
                (d / "generic.toml").write_text(_GENERIC_MAP)
                (d / "generic_t.csv").write_text(
                    "Date,Settle,Type,Ticker,Shares,Price,Amount,Currency\n"
                    + "\n".join(rs) + "\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("disallowed 0.00", r.stdout + r.stderr)
            rec = json.loads((root / "filed" / "2025.json").read_text())
        xyz = rec["year_end"]["equity"]["XYZ.TO"]
        self.assertAlmostEqual(xyz["qty"], 100.0)
        self.assertAlmostEqual(xyz["acb"], 2000.0, places=2)
        self.assertAlmostEqual(xyz.get("deferred", 0.0), 0.0, places=2)


if __name__ == '__main__':
    unittest.main()
