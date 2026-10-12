"""Owner decision of 2026-10-02: exercising a warrant or right is not a
disposition (audits A2-0090, A2-0274). Every fixture is synthetic:
invented tickers, fake account ids marked pii-ok."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import gains_both, tx


# --------------------------------------------------------------- warrants
def _warrant_book(mark=True):
    w = tx("ASSIGN", "2025-05-02", "XYZW.US", -100, 0, time="16:20:00")
    if mark:
        w.exercise_of = "XYZ.US"
    return [tx("BUYSELL", "2025-02-03", "XYZW.US", 100, 201),
            w,
            tx("BUYSELL", "2025-05-02", "XYZ.US", 100, 1150, time="16:20:00"),
            tx("BUYSELL", "2025-09-10", "XYZ.US", -100, 1999)]


class TestWarrantExerciseEngines(unittest.TestCase):

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_exercise_rolls_the_warrant_cost_into_the_shares(self):
        r = gains_both(_warrant_book(), year=2025)
        for c in ("canada", "usa"):
            g = [x for x in r[c]["transactions"] if "gain" in x]
            self.assertEqual([(x["symbol"], x["date"]) for x in g],
                             [("XYZ.US", "2025-09-10")], c)
            self.assertAlmostEqual(g[0]["gain"], 1999 - 1351, places=2,
                                   msg=c)
        # US: the holding period runs from the exercise.
        us = [x for x in r["usa"]["transactions"] if "gain" in x][0]
        self.assertEqual(us.get("days_held"), 131)

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_unmarked_leg_keeps_the_old_booking(self):
        r = gains_both(_warrant_book(mark=False), year=2025)
        for c in ("canada", "usa"):
            syms = [x["symbol"] for x in r[c]["transactions"] if "gain" in x]
            self.assertIn("XYZW.US", syms, c)

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_exercise_across_a_year_end_moves_no_loss(self):
        book = _warrant_book()
        book[1].date = book[1].date_settle = "2024-12-30"
        book[2].date = book[2].date_settle = "2024-12-30"
        book[0].date = book[0].date_settle = "2024-02-03"
        r = gains_both(book, year=2024)
        for c in ("canada", "usa"):
            self.assertEqual([x for x in r[c]["transactions"]
                              if "gain" in x], [], c)


class TestWarrantExerciseIb(unittest.TestCase):

    def _body(self, *extra):
        from test_fix_ibparse import HEAD, TRADES_H, _trade
        return (HEAD + TRADES_H
                + _trade('XYZW', '2025-02-03, 10:00:00', 100, 2.0, -200,
                         comm=-1, cat='Warrants')
                + _trade('XYZW', '2025-05-02, 16:20:00', -100, 0, 0,
                         code='C;Ex', cat='Warrants')
                + ''.join(extra))

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_ib_pairs_the_legs_and_the_engines_roll_the_cost(self):
        from test_fix_ibparse import _trade, _parse_ib
        from test_fix_l_ibparse import _book
        _, txs, err = _parse_ib(self._body(
            _trade('XYZ', '2025-05-02, 16:20:00', 100, 11.5, -1150,
                   code='Ex;O'),
            _trade('XYZ', '2025-09-10, 10:00:00', -100, 20.0, 2000,
                   comm=-1, code='C')))
        w = [t for t in txs if t['symbol'].startswith('XYZW')
             and t['action'] == 'ASSIGN']
        self.assertEqual(len(w), 1, err)
        self.assertEqual(w[0]['exercise_of'], 'XYZ.US')
        st = [t for t in txs if t['symbol'] == 'XYZ.US'
              and t['quantity'] > 0][0]
        self.assertEqual(w[0]['date_settle'], st['date_settle'])
        self.assertFalse([ln for ln in err.splitlines()
                          if 'ATTENTION' in ln and 'warrant' in ln], err)
        r = gains_both(_book(txs), year=2025)
        for c in ("canada", "usa"):
            g = [x for x in r[c]["transactions"] if "gain" in x]
            self.assertEqual([x["symbol"] for x in g], ["XYZ.US"], c)
            self.assertAlmostEqual(g[0]["cost"], 1351.0, places=2, msg=c)

    def test_ib_unpaired_leg_is_said(self):
        from test_fix_ibparse import _parse_ib
        _, txs, err = _parse_ib(self._body())
        w = [t for t in txs if t['action'] == 'ASSIGN']
        self.assertFalse(w[0].get('exercise_of'))
        self.assertIn('ATTENTION', err)
        self.assertIn('no share leg', err)


_RBC_H = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
          '"Price","Settlement Date","Account","Value","Currency",'
          '"Description"\n')


def _rbc(date, act, sym, symdesc, qty, value, desc, price=''):
    return (f'"{date} 00:00:00","{act}","{sym}","{symdesc}","{qty}",'
            f'"{price}","{date} 00:00:00","55500001","{value}","CAD",'  # pii-ok
            f'"{desc}"\n')


class TestWarrantExerciseRbc(unittest.TestCase):

    def _parse(self, rows):
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rbc.csv"
            p.write_text(_RBC_H + ''.join(rows))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                txs = RbcBrokerage().parse_file(p)
        return txs, err.getvalue()

    _BUY = _rbc('2025-02-03', 'Buy', 'QZW.WT', 'QZ CORP WTS', '100',
                '-100.00', 'QZ CORP WTS BUY', '1.00')
    _WLEG = _rbc('2025-05-05', 'Exercise', 'QZW.WT', 'QZ CORP WTS', '-100',
                 '0', 'EXERCISE QZ CORP WTS', '0')
    _SLEG = _rbc('2025-05-05', 'Exercise', 'QZ', 'QZ CORP', '100',
                 '-500.00', 'EXERCISE QZ CORP WTS', '5.00')

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_rbc_exercise_is_no_disposition(self):
        from test_fix_l_ibparse import _book
        txs, err = self._parse([self._BUY, self._WLEG, self._SLEG])
        w = [t for t in txs if t['symbol'].startswith('QZW')
             and t['quantity'] < 0]
        self.assertEqual(w[0]['action'], 'ASSIGN')
        self.assertEqual(w[0]['exercise_of'], 'QZ.TO')
        self.assertNotIn('_exercise', w[0])
        book = _book(txs) + [tx("BUYSELL", "2025-09-10", "QZ.TO", -100,
                                 900, currency="CAD", account="RBC")]
        r = gains_both(book, year=2025)
        for c in ("canada", "usa"):
            g = [x for x in r[c]["transactions"] if "gain" in x]
            self.assertEqual([(x["symbol"], round(x["cost"], 2))
                              for x in g], [("QZ.TO", 600.0)], c)

    def test_rbc_exercise_without_share_leg_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaises(BrokerageParseError) as cm:
            self._parse([self._BUY, self._WLEG])
        self.assertIn("Exercise of QZW.WT", str(cm.exception))


if __name__ == '__main__':
    unittest.main()
