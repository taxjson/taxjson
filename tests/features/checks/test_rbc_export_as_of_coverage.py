"""Deferred-round fixes, area ib-futures (2026-10 audit follow-up):

* S013-00 / S058-02 / S060-12 — IB's Trades open/close Code (O / C /
  C;O) is carried on the transaction (`open_close`), so the missing-
  history checks tell a broker-declared short (O) from a sale of a
  position bought before the data (C).
* S026-22 — a futures option keeps its declared contract size
  (`multiplier`) through the books: holdings export, list, .tt check,
  what-if.
* S063-22 — an RBC export's "as of" timestamp is a coverage bound,
  checked against the tax year (parse ATTENTION, checklist
  inputs-frozen) and against RBC's spring posting of back-dated Dec-31
  book-cost adjustments.

Every fixture is synthetic: invented tickers, fake account ids marked
pii-ok.

Formerly: test_fix_ib_futures"""
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _support.ib_statement_rows import _brokerage_cli
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------------- S063-22
RBC_HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
           '"Price","Settlement Date","Value","Currency","Description"\n')


def _rbc_row(date_, activity, symbol, qty, price, value, desc):
    cells = [date_, activity, symbol, 'QZ FUND UNITS', qty, price, date_,
             value, 'CAD', desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def _rbc(as_of, rows):
    pre = f'"Activity Export as of {as_of}"\n\n' if as_of else ''
    return pre + RBC_HDR + ''.join(rows)


BUYS = [_rbc_row('December 1, 2025', 'Buy', 'QZF', '100', '10', '-1000',
                 'QZ FUND UNITS'),
        _rbc_row('March 3, 2025', 'Buy', 'QZF', '100', '10', '-1000',
                 'QZ FUND UNITS')]


class TestRbcAsOfCoverage(unittest.TestCase):
    """Formerly: test_fix_ib_futures.TestRbcAsOfCoverage"""

    def test_as_of_preamble_variants(self):
        from taxjson.lib.brokerages.rbc_direct import read_rbc_rows
        for pre, want in (('Jan 5, 2026 at 8:59:00 am ET', '2026-01-05'),
                          ('Date Jan 5, 2026', '2026-01-05'),
                          ('December 15, 2025', '2025-12-15'),
                          ('', '')):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / 'rbc.csv'
                p.write_text(_rbc(pre, BUYS))
                self.assertEqual(read_rbc_rows(p).as_of, want, pre)

    def _msgs(self, files, year=2025, today=date(2026, 2, 10)):
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for i, (pre, rows) in enumerate(files):
                p = Path(td) / f'rbc{i}.csv'
                p.write_text(_rbc(pre, rows))
                paths.append(p)
            with contextlib.redirect_stderr(io.StringIO()):
                ctx = RbcBrokerage.prepare_files(paths)
            return RbcBrokerage.coverage_messages(ctx, year, today=today)

    def test_export_taken_before_year_end_is_attention(self):
        m, = self._msgs([('Dec 15, 2025', BUYS)])
        self.assertTrue(m.startswith('warning: ATTENTION: rbc0.csv'))
        self.assertIn('2025-12-16 to 2025-12-31', m)

    def test_open_year_and_later_export_are_quiet_on_coverage(self):
        self.assertEqual(self._msgs([('Dec 15, 2025', BUYS)],
                                    today=date(2025, 12, 20)), [])
        m = self._msgs([('Dec 15, 2025', BUYS),
                        ('Jan 5, 2026 at 9:00:28 am ET', BUYS)])
        self.assertFalse(any('ATTENTION' in x for x in m))

    def test_back_dated_book_cost_window_is_a_note(self):
        # Held at the year end, exports taken before RBC's spring posting.
        m, = self._msgs([('Jan 5, 2026', BUYS)])
        self.assertTrue(m.startswith('note:'))
        self.assertIn('2025-12-31', m)
        self.assertIn('2026-06-30', m)
        # An export taken after the posting covers it.
        self.assertEqual(self._msgs([('Jan 5, 2026', BUYS),
                                     ('Jul 2, 2026', BUYS)],
                                    today=date(2026, 7, 3)), [])
        # Nothing held at the year end: nothing to adjust.
        flat = BUYS[:1] + [_rbc_row('December 5, 2025', 'Sell', 'QZF',
                                    '-200', '11', '2200', 'QZ FUND UNITS')] \
            + BUYS[1:]
        self.assertEqual(self._msgs([('Jan 5, 2026', flat)]), [])

    def test_brokerage_cli_prints_the_attention_with_the_year(self):
        rc, _doc, err, _ = _brokerage_cli(
            {'rbc.csv': _rbc('Dec 15, 2025', BUYS)}, '--tax-year', '2025',
            brokerage='rbc')
        self.assertEqual(rc, 0, err)
        self.assertIn('warning: ATTENTION: rbc.csv', err)
        rc, _doc, err, _ = _brokerage_cli(
            {'rbc.csv': _rbc('Dec 15, 2025', BUYS)}, brokerage='rbc')
        self.assertNotIn('ATTENTION', err)      # no year: not judged

    def test_checklist_inputs_frozen_per_rbc_export(self):
        from taxjson.lib import checklist as cl
        from _support.filing_checklist_run_project import _project, _ctx
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)                # another source runs to Feb 2026
            (root / 'inputs' / 'margin' / 'rbc.csv').write_text(
                _rbc('Dec 15, 2025', BUYS))
            r = cl.d_inputs_frozen(_ctx(root, {}, today=date(2026, 3, 1)))
            self.assertEqual(r.status, 'attention')
            self.assertIn('RBC export as of 2025-12-15', r.detail)
            (root / 'inputs' / 'margin' / 'rbc2.csv').write_text(
                _rbc('Feb 2, 2026 at 8:00:00 am ET', BUYS))
            r = cl.d_inputs_frozen(_ctx(root, {}, today=date(2026, 3, 1)))
            self.assertEqual(r.status, 'done')


if __name__ == '__main__':
    unittest.main()
