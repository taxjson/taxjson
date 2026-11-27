"""Which purchase closes a marked short follows the broker's convention
(CA-DISP-04; backlog TJ-4993, TJ-1357, TJ-5235). A broker that marks a short
account's trades in the description (RBC: "SHORT." / "COVER SHORT."; the
rows' `short_marks`): only the marked purchase closes the short, and an
unmarked purchase of its exports is a long purchase, whatever file it is
in. Any other row (IB, Questrade, a .tt line, the generic importer): a
purchase covers while the account is short and holds no long, as before
(how Questrade marks a short account's trades is not known: TJ-5236).
Never silent: a marked cover that finds the short already covered, and an
account that ends long and short the same shares, are said. The history
check reads the same cover as the books, by country.

Synthetic data only; account labels and tickers are made up.
"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.ca.router import _MarkedShortRouter
from taxjson.lib.brokerages.base import BaseBrokerage
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.missing_history import (detect_unbacked_covers,
                                         drop_answered_covers)
from _support.ca_engine_runs import _run
from _support.qt_close import _QH, _project, _row
from _support.rbc_activity import HDR as RBC_HDR, row as rbc_row

SYM = 'SYN.TO'
WORDS = 'description'           # the rows of a broker that marks in words
COVER = 'SYN CORP COVER SHORT.'
SHORT = 'SYN CORP SHORT.'
IB, TT, Q1, Q2 = 'ib_2025.csv', 'covers.tt', 'q_jan.csv', 'q_mar.csv'


def _tr(date, qty, net, desc='', oc='', action='BUYSELL', acct='m', sym=SYM,
        src='', marks=''):
    return TaxTransaction(
        action=action, date=date, date_settle=date, time='10:00:00', symbol=sym,
        quantity=float(qty), price=abs(net / qty) if qty else 0.0,
        net_amount=float(net), currency='CAD', account=acct, description=desc,
        open_close=oc, source=src, short_marks=marks)


def _opening_short():
    return _tr('2024-12-31', -10, 0, action='OPENING_BALANCE')


def _rows(res):
    return [(g['date'], g['direction'], round(float(g['cost']), 2),
             round(float(g['gain']), 2), bool(g.get('tainted')))
            for g in res['transactions']]


def _held(res):
    return sorted((i['symbol'], i['qty'], i['total_cost']) for i in res['inventory'])


def _said(err, topic):
    return [ln for ln in err.splitlines() if f'ATTENTION: {topic}: ' in ln]


def _entry(marks='', cover_desc='', cover_oc='', files=('', '')):
    """The entry's book: a short of 10 at an unknown cost, 10 bought for
    500 on February 3, 10 bought for 400 on March 3, 10 sold for 700."""
    return [_opening_short(),
            _tr('2025-02-03', 10, -500, marks=marks, src=files[0]),
            _tr('2025-03-03', 10, -400, desc=cover_desc, oc=cover_oc, marks=marks,
                src=files[1]),
            _tr('2025-05-05', -10, 700, marks=marks, src=files[1])]


class TestABrokerThatMarksInWords(unittest.TestCase):
    """RBC: only the marked purchase closes the short."""

    @rule(R.CA_DISP_04)
    def test_the_marked_row_closes_the_short(self):
        res, err = _run(_entry(WORDS, cover_desc=COVER))
        # March closes the short (its cost unknown); February's long costs 500.
        self.assertEqual(_rows(res), [('2025-03-03', 'SHORT', 0.0, -400.0, True),
                                      ('2025-05-05', 'LONG', 500.0, 200.0, False)])
        self.assertEqual(res['inventory'], [])
        self.assertEqual(_said(err, 'long and short') + _said(err, 'marked cover'), [])

    @rule(R.CA_DISP_04)
    def test_over_two_files_of_the_broker(self):
        # The plain buy in one export, the marked cover in another: the same.
        res, err = _run(_entry(WORDS, cover_desc=COVER, files=(Q1, Q2)))
        self.assertEqual(_rows(res), [('2025-03-03', 'SHORT', 0.0, -400.0, True),
                                      ('2025-05-05', 'LONG', 500.0, 200.0, False)])
        self.assertEqual(_said(err, 'marked cover'), [])

    @rule(R.CA_DISP_04)
    def test_a_marked_short_then_a_plain_buy_then_the_marked_cover(self):
        res, _err = _run([_tr('2025-01-06', -10, 600, desc=SHORT, marks=WORDS),
                          _tr('2025-02-03', 10, -500, marks=WORDS),
                          _tr('2025-03-03', 10, -400, desc=COVER, marks=WORDS),
                          _tr('2025-05-05', -10, 700, marks=WORDS)])
        self.assertEqual(_rows(res), [('2025-03-03', 'SHORT', -600.0, 200.0, False),
                                      ('2025-05-05', 'LONG', 500.0, 200.0, False)])

    @rule(R.CA_DISP_04)
    def test_with_no_marked_cover_the_buy_is_long_and_the_run_says_both(self):
        res, err = _run([_tr('2025-01-06', -10, 600, desc=SHORT, marks=WORDS, src=Q1),
                         _tr('2025-02-03', 10, -500, marks=WORDS, src=Q1)])
        self.assertEqual(_rows(res), [])
        self.assertEqual(_held(res), [(SYM, -10.0, -600.0), (SYM, 10.0, 500.0)])
        line, = _said(err, 'long and short')
        for word in ('long and short: SYN.TO (m): ', '10 held and 10 short',
                     '`COVER SHORT.`', 'If the broker holds both, nothing is wrong',
                     'the export that holds the marked cover is missing'):
            self.assertIn(word, line)

    def test_rbc_alone_says_so(self):
        self.assertEqual(RbcBrokerage.SHORT_MARKS, WORDS)
        self.assertEqual((BaseBrokerage.SHORT_MARKS, IbBrokerage.SHORT_MARKS,
                          QuestradeBrokerage.SHORT_MARKS), ('', '', ''))


class TestTheOtherSourcesReadAsBefore(unittest.TestCase):
    """IB (one netted position), a .tt line, the generic importer: a buy
    while the account is short and holds no long covers."""

    def test_control_no_convention_the_first_buy_covers(self):
        res, _err = _run(_entry())
        self.assertEqual(_rows(res),
                         [('2025-02-03', 'SHORT', 0.0, -500.0, True),
                          ('2025-05-05', 'LONG', 400.0, 300.0, False)])

    @rule(R.CA_DISP_04, R.CA_ACB_11)
    def test_no_convention_a_marked_cover_past_the_short_opens_no_long(self):
        # February covers (no convention); March is marked a cover and
        # finds no short: a row the broker marks CLOSE opens nothing, so
        # it is reported by hand as the cover of a short sold before the
        # files, and May's sale finds no long (it was a long of 400 sold
        # for a gain of 300: backlog TJ-5349). The run says both rows.
        for cover in ({'cover_oc': 'C'}, {'cover_desc': COVER}):
            with self.subTest(cover=cover):
                res, err = _run(_entry(**cover))
                self.assertEqual(_rows(res),
                                 [('2025-02-03', 'SHORT', 0.0, -500.0, True),
                                  ('2025-03-03', 'SHORT', 0.0, -400.0, True)])
                self.assertEqual([(s, q) for s, q, _c in _held(res)], [(SYM, -10.0)])
                self.assertEqual(len(_said(err, 'marked cover')), 1)

    def test_control_ib_a_blank_code_buy_covers(self):
        # (the reviewer's I3: no long beside an open short)
        res, err = _run([_tr('2025-02-03', -10, 600, oc='O', src=IB),
                         _tr('2025-03-03', 10, -500, src=IB)])
        self.assertEqual(_rows(res), [('2025-03-03', 'SHORT', -600.0, 100.0, False)])
        self.assertEqual(_held(res), [])
        self.assertEqual(_said(err, 'long and short'), [])

    def test_control_a_cover_written_in_a_tt_file_covers(self):
        # TJ-5235: the IB export marks its covers, the cover is a .tt line.
        res, err = _run([_tr('2025-01-06', -5, 100, oc='O', sym='ZZO.TO', src=IB),
                         _tr('2025-01-20', 5, -90, oc='C', sym='ZZO.TO', src=IB),
                         _tr('2025-02-03', -10, 600, oc='O', src=IB),
                         _tr('2025-03-03', 10, -500, src=TT)])
        self.assertEqual([r for r in _rows(res) if r[0] >= '2025-02'],
                         [('2025-03-03', 'SHORT', -600.0, 100.0, False)])
        self.assertEqual(_held(res), [])
        self.assertEqual(_said(err, 'marked cover') + _said(err, 'long and short'), [])

    def test_control_a_tt_cover_of_a_short_marked_in_words(self):
        # A .tt line beside an RBC export: the line has no convention.
        res, _err = _run([_tr('2025-01-06', -10, 600, desc=SHORT, marks=WORDS, src=Q1),
                          _tr('2025-02-03', 10, -500, src=TT)])
        self.assertEqual(_rows(res), [('2025-02-03', 'SHORT', -600.0, 100.0, False)])

    def test_control_a_buy_cut_in_two(self):
        # An unmarked buy past the short is cut into a cover and a long (a
        # code of the engine's own): the next unmarked buy still covers.
        res, _err = _run([_tr('2025-01-06', -10, 600, desc=SHORT),
                          _tr('2025-02-03', 15, -750),
                          _tr('2025-03-03', -5, 300),
                          _tr('2025-04-01', -10, 650, desc=SHORT),
                          _tr('2025-05-05', 10, -500)])
        self.assertEqual([r[:2] for r in _rows(res)],
                         [('2025-02-03', 'SHORT'), ('2025-03-03', 'LONG'),
                          ('2025-05-05', 'SHORT')])
        self.assertEqual(res['inventory'], [])

    def test_the_router_alone(self):
        words, plain = _MarkedShortRouter(), _MarkedShortRouter()
        self.assertEqual([words.walk(t) for t in _entry(WORDS, cover_desc=COVER)],
                         [True, False, True, False])
        # (no convention: February covers, and March, marked a cover,
        # finds no short left: it is the short pool's all the same, and
        # books no long: CA-ACB-11)
        self.assertEqual([plain.walk(t) for t in _entry(cover_desc=COVER)],
                         [True, True, True, False])
        self.assertEqual(plain.long.get((SYM, 'm'), 0.0), -10.0)


class TestAMarkedCoverThatFindsTheShortGone(unittest.TestCase):
    """A .tt purchase read as the cover, then the broker's marked cover of
    the same short: said, with both rows, never silent."""

    def _book(self):
        return [_tr('2025-02-03', -10, 600, oc='O', src=IB),
                _tr('2025-03-03', 10, -500, src=TT),
                _tr('2025-04-01', 10, -450, oc='C', src=IB)]

    @rule(R.CA_DISP_04)
    def test_the_run_names_both_rows(self):
        res, err = _run(self._book())
        # The .tt line covered; the marked cover found no short: it is
        # reported by hand and adds no long (it was a long of 450).
        self.assertEqual(_rows(res), [('2025-03-03', 'SHORT', -600.0, 100.0, False),
                                      ('2025-04-01', 'SHORT', 0.0, -450.0, True)])
        self.assertEqual(_held(res), [])
        line, = _said(err, 'marked cover')
        for word in ('warning: ATTENTION: marked cover: SYN.TO (m): ',
                     f'the purchase of 10 on 2025-03-03 ({TT}) carries no cover mark',
                     f"the broker's marked cover of 10 on 2025-04-01 ({IB})",
                     'found less short than it buys', 'reported by hand', 'add no long',
                     'remove the unmarked one'):
            self.assertIn(word, line)

    @rule(R.CA_DISP_04)
    def test_a_tt_cover_beside_a_cover_marked_in_words(self):
        res, err = _run([_tr('2025-02-03', -10, 600, desc=SHORT, marks=WORDS, src=Q1),
                         _tr('2025-03-03', 10, -500, src=TT),
                         _tr('2025-04-01', 10, -450, desc=COVER, marks=WORDS, src=Q1)])
        self.assertEqual(_held(res), [])
        self.assertEqual(_rows(res)[-1], ('2025-04-01', 'SHORT', 0.0, -450.0, True))
        self.assertEqual(len(_said(err, 'marked cover')), 1)

    @rule(R.CA_DISP_04)
    def test_a_marked_cover_past_what_the_tt_line_left(self):
        # The .tt line covered 6 of the 10: the marked 10 covers 4, and
        # its other 6 are reported by hand (they were a long of 6).
        book = self._book()
        book[1] = _tr('2025-03-03', 6, -300, src=TT)
        res, err = _run(book)
        self.assertEqual([(r[0], r[1], r[4]) for r in _rows(res)],
                         [('2025-03-03', 'SHORT', False), ('2025-04-01', 'SHORT', False),
                          ('2025-04-01', 'SHORT', True)])
        self.assertEqual(_held(res), [])
        self.assertEqual(len(_said(err, 'marked cover')), 1)

    def test_control_the_marked_cover_alone_says_nothing(self):
        book = self._book()
        del book[1]
        res, err = _run(book)
        self.assertEqual(_rows(res), [('2025-04-01', 'SHORT', -600.0, 150.0, False)])
        self.assertEqual(_said(err, 'marked cover'), [])

    def test_control_a_later_short_closed_in_full_clears_the_blame(self):
        # (the reviewer's L1) The .tt line covered the first short; a second
        # short is opened and covered in full; a last marked cover finds no
        # short. The .tt line is not blamed for it.
        book = [_opening_short(), _tr('2025-01-15', 10, -500, src=TT),
                _tr('2025-03-03', -5, 300, desc=SHORT, marks=WORDS, src=Q1),
                _tr('2025-04-03', 5, -250, desc=COVER, marks=WORDS, src=Q1),
                _tr('2025-09-03', 5, -250, desc=COVER, marks=WORDS, src=Q1)]
        res, err = _run(book)
        self.assertEqual(_said(err, 'marked cover'), [])
        # (the last marked cover: by hand, no long; it was a long of 5)
        self.assertEqual(_held(res), [])
        self.assertEqual(_rows(res)[-1], ('2025-09-03', 'SHORT', 0.0, -250.0, True))


class TestTheHistoryCheckReadsTheSameCover(unittest.TestCase):
    """`drop_answered_covers` and `detect_unbacked_covers` follow the books
    by country (TJ-1357's warning that stayed; the reviewer's repro A)."""

    def _left(self, book, country):
        left = drop_answered_covers(book, [_opening_short()], country)
        return [(t.date, t.quantity) for t in left]

    def test_canada_the_marked_cover_answers_the_short_line(self):
        book = _entry(WORDS, cover_desc=COVER)[1:]
        self.assertEqual(self._left(book, 'canada'),
                         [('2025-02-03', 10.0), ('2025-05-05', -10.0)])
        left = drop_answered_covers(book, [_opening_short()], 'canada')
        self.assertEqual(detect_unbacked_covers(left, country='canada'), [])

    def test_canada_an_unmarked_buy_in_words_never_answers(self):
        book = [_tr('2025-02-03', 10, -500, marks=WORDS),
                _tr('2025-03-03', 4, -160, desc=COVER, marks=WORDS),
                _tr('2025-05-05', 10, -450, marks=WORDS)]
        self.assertEqual(self._left(book, 'canada'),
                         [('2025-02-03', 10.0), ('2025-05-05', 10.0)])

    def test_control_the_us_and_rows_with_no_convention_take_the_first_buys(self):
        words = _entry(WORDS, cover_desc=COVER)[1:]
        plain = _entry(cover_oc='C')[1:]
        first = [('2025-03-03', 10.0), ('2025-05-05', -10.0)]
        self.assertEqual(self._left(words, 'usa'), first)
        self.assertEqual(self._left(words, None), first)
        self.assertEqual(self._left(plain, 'canada'), first)

    def test_canada_a_marked_cover_after_a_plain_buy_is_backed(self):
        # Repro A: a marked short, a plain buy (a long), the marked cover.
        book = [_tr('2025-01-06', -10, 600, desc=SHORT, marks=WORDS),
                _tr('2025-02-03', 10, -500, marks=WORDS),
                _tr('2025-03-03', 10, -400, desc=COVER, marks=WORDS),
                _tr('2025-05-05', -10, 700, marks=WORDS)]
        self.assertEqual(detect_unbacked_covers(book, country='canada'), [])
        # The netted reading (the US engine's; no country) still lists it.
        for country in ('usa', None):
            cover, = detect_unbacked_covers(book, country=country)
            self.assertEqual((cover.date, cover.unbacked_qty), ('2025-03-03', 10.0))

    def test_canada_a_marked_cover_with_no_short_is_still_unbacked(self):
        book = [_tr('2025-02-03', 10, -500, marks=WORDS),
                _tr('2025-03-03', 10, -400, desc=COVER, marks=WORDS)]
        cover, = detect_unbacked_covers(book, country='canada')
        self.assertEqual((cover.date, cover.unbacked_qty), ('2025-03-03', 10.0))


def _q(day, qty, net, desc='SYN CORP'):
    return _row(day, 'Buy' if qty > 0 else 'Sell', SYM, qty, abs(net / qty),
                net if qty < 0 else -abs(net), desc=desc)


_RBC_HEAD = '"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n' + RBC_HDR
_DAYS = {'2025-01-06': 'January 6, 2025', '2025-02-03': 'February 3, 2025',
         '2025-03-03': 'March 3, 2025', '2025-05-05': 'May 5, 2025'}


def _r(day, qty, net, words=''):
    """An RBC activity row of SYN: `words` its short-account marker."""
    return rbc_row(_DAYS[day], 'Buy' if qty > 0 else 'Sell', 'SYN', 'SYN CORP',
                   str(qty), str(abs(net / qty)), str(net if qty < 0 else -abs(net)),
                   'CAD', f'SYN CORP{words} UNSOLICITED')


def _run_project(files):
    with tempfile.TemporaryDirectory() as d:
        root = _project(Path(d), {'margin': 'taxable'})
        for rel, text in files.items():
            (root / 'inputs' / rel).write_text(text)
        tj(root, 'run', '--no-input')
        r = tj(root, 'run', '--no-input')
        text = ' '.join((r.stdout + r.stderr).split())
        gain = json.loads(tj(root, 'form-export', '--json').stdout)['gain_unrounded']
        rows = json.loads((root / 'work/margin_base.json').read_text())['transactions']
        found = tj(root, 'find-missing-history').stdout
        wrote = tj(root, 'find-missing-history', '--write-missing-history', check=False)
        held = json.loads(tj(root, 'list', '--json').stdout)['rows']
        return (gain, text, rows, found, wrote.stdout + wrote.stderr,
                [(h['symbol'], h['qty']) for h in held])


class TestAnRbcProject(unittest.TestCase):
    """RBC exports through `tjs run`: the rows carry the convention."""

    @rule(R.CA_DISP_04)
    def test_the_short_line_the_plain_buy_and_the_marked_cover_in_two_files(self):
        # TJ-1357 (the review's repro B, as RBC rows): the entry's 200.
        gain, text, rows, found, _w, _held = _run_project({
            'margin/t.tt': 'OPENING 2024-12-31 SYN.TO -10 cost=unknown\n',
            'margin/rbc_a.csv': _RBC_HEAD + _r('2025-02-03', 10, 500),
            'margin/rbc_b.csv': _RBC_HEAD + _r('2025-05-05', -10, 700)
            + _r('2025-03-03', 10, 400, ' COVER SHORT.')})
        self.assertAlmostEqual(gain, 200.0, places=2)
        self.assertIn('SYN.TO 2025-03-03 in margin', text)
        self.assertNotIn('booked as a new long', text)
        self.assertNotIn('Marked cover', text)
        self.assertIn('No missing-cost-basis issues found', found)
        self.assertEqual(sorted({(t.get('source'), t.get('short_marks')) for t in rows}),
                         [('rbc_a.csv', WORDS), ('rbc_b.csv', WORDS)])

    @rule(R.CA_DISP_04)
    def test_a_marked_short_a_plain_buy_and_the_marked_cover_say_nothing(self):
        # The review's repro A: the books were right; now the history check
        # agrees, and its remedy writes nothing.
        gain, text, _rows_, found, wrote, _held = _run_project({
            'margin/rbc.csv': _RBC_HEAD + _r('2025-05-05', -10, 700)
            + _r('2025-03-03', 10, 400, ' COVER SHORT.') + _r('2025-02-03', 10, 500)
            + _r('2025-01-06', -10, 600, ' SHORT.')})
        self.assertAlmostEqual(gain, 400.0, places=2)
        self.assertNotIn('cover of a short opened before your files', text)
        self.assertNotIn('COVERS OF A SHORT OPENED BEFORE THE DATA', found)
        self.assertIn('nothing to add', wrote)

    @rule(R.CA_DISP_04)
    def test_a_short_left_open_beside_a_long_is_said(self):
        _gain, text, _rows_, _found, _w, held = _run_project({
            'margin/t.tt': 'OPENING 2024-12-31 SYN.TO -10 cost=unknown\n',
            'margin/rbc.csv': _RBC_HEAD + _r('2025-02-03', 10, 500)})
        self.assertIn('Warning: Long and short: SYN.TO (margin)', text)
        self.assertEqual(sorted(held), [(SYM, -10.0), (SYM, 10.0)])


class TestAQuestradeProject(unittest.TestCase):
    """Questrade rows carry no convention (its short-account marking is not
    known: TJ-5236): the reading they had, and the Marked cover Warning on a
    clash."""

    def test_control_a_short_line_and_plain_buys(self):
        # The review's Q1: the first buy covers the short line, the sale
        # costs the second buy. 300, nothing held, no Long and short.
        gain, text, rows, _found, _w, held = _run_project({
            'margin/t.tt': 'OPENING 2024-12-31 SYN.TO -10 cost=unknown\n',
            'margin/q.csv': _QH + _q('2025-02-03', 10, 500) + _q('2025-06-03', 10, 400)
            + _q('2025-09-05', -10, 700)})
        self.assertAlmostEqual(gain, 300.0, places=2)
        self.assertEqual(held, [])
        self.assertIn('SYN.TO 2025-02-03 in margin', text)
        self.assertNotIn('Long and short', text)
        self.assertNotIn('Marked cover', text)
        self.assertEqual({t.get('short_marks') for t in rows}, {None})

    @rule(R.CA_DISP_04)
    def test_a_marked_cover_after_a_plain_buy_is_said(self):
        # RBC's words in a Questrade file: the plain buy covers as before,
        # and the marked cover that finds no short is named and reported
        # by hand: it opens no long, so the later sale has none to sell
        # (it was a gain of 300 on a long of 400; backlog TJ-5349).
        gain, text, _rows_, _found, _w, _held = _run_project({
            'margin/t.tt': 'OPENING 2024-12-31 SYN.TO -10 cost=unknown\n',
            'margin/q.csv': _QH + _q('2025-02-03', 10, 500)
            + _q('2025-03-03', 10, 400, desc=COVER) + _q('2025-05-05', -10, 700)})
        self.assertAlmostEqual(gain, 0.0, places=2)
        self.assertIn('Warning: Marked cover: SYN.TO (margin)', text)


if __name__ == '__main__':
    unittest.main()
