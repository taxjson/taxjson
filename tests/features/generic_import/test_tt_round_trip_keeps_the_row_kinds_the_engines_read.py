"""A `.tt` round trip keeps every row kind an engine gives meaning to, or
refuses the row (backlog TJ-5210).

A BUYSELL / ASSIGN line kept a row's `type` for a stock dividend, a
settled futures part and a trade cancellation only. TJ-5036 refuses the
kinds whose cash differs (a transfer at book value, the in-kind moves).
Every other kind `tjs run` makes and an engine reads (lib/row_types: a
section 355 spin-off's two rows, a section 356 exchange's two rows, a
sheltered account's transfer in the loss-rule context, a listing
journal's legs, a netted own-account move) was written as an ordinary
line and read back without it: the cash was the same, the booking was
not (a spin-off's shares read back as a purchase on the distribution
date, their holding period gone).

Each such kind is now carried by a token the reader reads back to the
same row and the same id (the description aside: no line carries one,
whatever the row), or the row is refused at the conversion, naming the
row and the type: a lot move, whose pairing and peer account are in its
description; any `type` the line does not read back with.

No tax rule is pinned here: what the engines do with each kind has its
own tests. Synthetic rows only (made-up tickers QZRX, QZSP, QZNW).
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import row_types as RT
from taxjson.lib.cli_diag import InputContentError
from taxjson.lib.opening import opening_row
from taxjson.lib.tt_format import (compute_tt_id, json_to_tt_lines, not_tt_why,
                                   parse_tt_line, tx_to_tt_line)
from taxjson.model.transaction import TaxTransaction
from _support.us_corp_engine import _boot_rows, _engine, _spin_rows

# (the two names TJ-5210 adds to lib/row_types, spelled out: the first
# test checks them against it)
REORG_356, TRANSFER_REWRITE, CIL_TAKEOVER = 'reorg_356', 'transfer_rewrite', 'cil_takeover'
BASE = dict(date='2025-03-03', date_settle='2025-03-03', time='10:00:00',
            currency='USD', account='margin')


def _trade(typ, qty=10.0, **more):
    return dict(BASE, action='BUYSELL', type=typ, symbol='QZRX.US',
                quantity=qty, price=5.0, net_amount=50.0, **more)


def _transfer(typ, qty=10.0, **more):
    return dict(BASE, action='TRANSFER', type=typ, symbol='QZRX.US',
                quantity=qty, price=0.0, net_amount=0.0, **more)


def _spin():
    return [r.to_dict() for r in _spin_rows(200, 50)]


def _boot():
    return [r.to_dict() for r in _boot_rows(100, 50, 300, 20)]


def _back(row):
    """`row` written as its .tt line and read back, and the line."""
    line = tx_to_tt_line(row)
    with contextlib.redirect_stderr(io.StringIO()):
        return parse_tt_line(line, account_name=row['account']), line


# Every kind an engine reads, as the row `tjs run` (or a parser) makes
# it: {type: [rows]}. CARRIED: a line says it. REFUSED: no line does.
CARRIED = {
    RT.STOCK_DIVIDEND: [dict(_trade(RT.STOCK_DIVIDEND), price=0.0, net_amount=0.0,
                             ex_date='2025-02-20'),
                        # (a covered short's row with what its delivery cost)
                        dict(_trade(RT.STOCK_DIVIDEND), price=0.0, net_amount=0.0,
                             ex_date='2025-02-20', delivered_cost=45.5)],
    'futures_settlement': [dict(_trade('futures_settlement'), symbol='F:QZM26.US',
                                net_amount=0.0, multiplier=10.0)],
    'trade_cancel': [_trade('trade_cancel', -10.0)],
    RT.OPENING_TYPE: [opening_row(symbol='QZRX.US', quantity=10.0, total_cost=50.0,
                                  day='2025-03-03', currency='USD',
                                  account='margin', lot_date='2024-01-08')],
    'roc': [dict(BASE, action='ADJUST', type='roc', symbol='QZRX.US',
                 net_amount=-5.0)],
    'dist': [dict(BASE, action='ADJUST', type='dist', symbol='QZRX.US',
                  net_amount=5.0, record_date='2025-02-28')],
    RT.SPINOFF_355_TYPE: _spin() + [dict(
        _trade(RT.SPINOFF_355_TYPE), corp_event_id='ev-1',
        ex_date='2025-02-20', record_date='2025-02-21')],
    REORG_356: _boot(),
    RT.TRANSFER_CUSTODY_TYPE: [_trade(RT.TRANSFER_CUSTODY_TYPE),
                               _trade(RT.TRANSFER_CUSTODY_TYPE, -10.0)],
    TRANSFER_REWRITE: [_trade(TRANSFER_REWRITE)],
    RT.LISTING_JOURNAL_TYPE: [
        dict(_transfer(RT.LISTING_JOURNAL_TYPE, -10.0), symbol='QZRX.TO',
             currency='CAD', journal_pair='listing-journal-1'),
        dict(_transfer(RT.LISTING_JOURNAL_TYPE), price=0.74,
             journal_pair='listing-journal-1')],
    RT.OWN_MOVE_TYPE: [_transfer(RT.OWN_MOVE_TYPE), _transfer(RT.OWN_MOVE_TYPE, -10.0)],
}
REFUSED = {
    RT.LOT_MOVE_TYPE: [dict(_transfer(RT.LOT_MOVE_TYPE, -10.0), description=(
        'own-account move #1: margin -> other (basis and purchase dates '
        'carried)'))],
    RT.TRANSFER_BOOK_VALUE_TYPE: [_trade(RT.TRANSFER_BOOK_VALUE_TYPE)],
    RT.IN_KIND_CONTRIBUTION_TYPE: [_trade(RT.IN_KIND_CONTRIBUTION_TYPE, -10.0)],
    RT.IN_KIND_DISPOSITION_TYPE: [_trade(RT.IN_KIND_DISPOSITION_TYPE, -10.0)],
    RT.IN_KIND_WITHDRAWAL_TYPE: [_trade(RT.IN_KIND_WITHDRAWAL_TYPE)],
    # (merge2 drops the marked pair before the engines: a book that still
    # holds the mark has no line for it)
    CIL_TAKEOVER: [_trade(CIL_TAKEOVER, -0.5)],
}
# The fields an engine reads off a row of each kind, besides the line's
# own (the symbol, the units, the money, the dates).
KEPT = ('type', 'corp_event_id', 'corp_election', 'corp_parent_qty', 'journal_pair',
        'ex_date', 'record_date', 'lot_date', 'multiplier', 'delivered_cost')


class TestEveryKindIsDecided(unittest.TestCase):

    def test_the_table_holds_every_kind_row_types_names(self):
        named = {v for k, v in vars(RT).items()
                 if isinstance(v, str) and (k.endswith('_TYPE')
                                            or k == 'STOCK_DIVIDEND')}
        self.assertEqual(named - set(CARRIED) - set(REFUSED), set())
        self.assertEqual(set(CARRIED) & set(REFUSED), set())

    def test_the_engines_own_name_of_the_356_rows(self):
        from taxjson.engine.us.terms import REORG_356_TYPE
        self.assertEqual(RT.REORG_356_TYPE, REORG_356_TYPE)
        self.assertEqual((RT.REORG_356_TYPE, RT.TRANSFER_REWRITE_TYPE),
                         (REORG_356, TRANSFER_REWRITE))
        from taxjson.lib.corp_actions import CIL_TAKEOVER_TYPE
        self.assertEqual((RT.CIL_TAKEOVER_TYPE, CIL_TAKEOVER_TYPE),
                         (CIL_TAKEOVER, CIL_TAKEOVER))
        self.assertEqual({r['type'] for r in _boot()}, {RT.REORG_356_TYPE})


class TestACarriedKindReadsBackAsTheSameRow(unittest.TestCase):

    def test_the_type_the_fields_and_the_id(self):
        # Base: the kinds this change adds (spinoff_355, reorg_356,
        # transfer_custody, transfer_rewrite, listing_journal,
        # own_account_move) read back with no type and another id.
        for typ, rows in CARRIED.items():
            for row in rows:
                with self.subTest(type=typ, action=row['action'],
                                  quantity=row.get('quantity')):
                    back, line = _back(row)
                    for key in KEPT:
                        if row.get(key) not in (None, ''):
                            self.assertEqual(back.get(key), row[key], (key, line))
                    for key in ('action', 'symbol', 'date', 'date_settle',
                                'currency'):
                        self.assertEqual(back[key], row[key], (key, line))
                    for key in ('quantity', 'price', 'net_amount', 'gross_amount'):
                        self.assertAlmostEqual(float(back.get(key) or 0.0),
                                               float(row.get(key) or 0.0),
                                               msg=(key, line))
                    # (no line carries a description, whatever the row:
                    # the id is the row's with the reader's own)
                    self.assertEqual(back['id'], compute_tt_id(
                        {**row, 'description': back.get('description', '')}), line)
                    self.assertEqual(not_tt_why(row), '', line)

    def test_the_tokens_as_written(self):
        spin_buy, spin_adj = _spin()
        sell, buy = _boot()
        for row, want in (
                (spin_buy, f" type=spinoff_355 corp={spin_buy['corp_event_id']} "),
                (spin_adj, f" type=spinoff_355 corp={spin_adj['corp_event_id']} "),
                (sell, " type=reorg_356 boot=300.00000000 corp="),
                (buy, " type=reorg_356 corp="),
                (CARRIED[RT.TRANSFER_CUSTODY_TYPE][0], " 0.00000 type=transfer_custody"),
                (CARRIED[RT.LISTING_JOURNAL_TYPE][1],
                 " 0.74000000 0.00000 type=listing_journal pair=listing-journal-1"),
                (CARRIED[RT.OWN_MOVE_TYPE][0], " 0.00000 type=own_account_move")):
            with self.subTest(want=want):
                self.assertIn(want, tx_to_tt_line(row) + " ")

    def test_a_stock_dividends_delivered_cost_is_carried(self):
        row = CARRIED[RT.STOCK_DIVIDEND][1]
        back, line = _back(row)
        self.assertIn(" type=stock_dividend ex=2025-02-20 delivered_cost=45.50000000",
                      line)
        self.assertEqual(back['delivered_cost'], 45.5)
        # (no part of the id: the row with it and without are one row)
        self.assertEqual(back['id'], _back(CARRIED[RT.STOCK_DIVIDEND][0])[0]['id'])
        for bad in ("delivered_cost=0", "delivered_cost=-5", "delivered_cost=x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_tt_line(line.split(" delivered_cost=")[0] + " " + bad, 'margin')
        with self.assertRaises(ValueError):      # (a stock dividend's line only)
            parse_tt_line("BUYSELL 2025-03-03 10:00:00 QZRX.US 10 USD 5 50 0 "
                          "delivered_cost=45", 'margin')

    def test_the_boot_of_a_356_sale_is_the_rows_gross_amount(self):
        sell = _boot()[0]
        self.assertEqual(sell['gross_amount'], 300.0)
        self.assertEqual(_back(sell)[0]['gross_amount'], 300.0)

    def test_a_plain_row_writes_no_token(self):
        for row in (dict(_trade(''), type=None), dict(_transfer(''), type=None),
                    dict(_transfer(''), type=None, quantity=-10.0,
                         description='manual declaration (.tt)')):
            with self.subTest(row=row['action']):
                back, line = _back(row)
                self.assertNotIn("type=", line)
                self.assertNotIn("pair=", line)
                self.assertFalse(back.get('type'))


class TestTheGainsOfARoundTrippedBook(unittest.TestCase):
    """The entry's repro: a US book holding a spin-off, converted to .tt
    and back, books the same gains."""

    @staticmethod
    def _t(i, date, sym, qty, net):
        return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                              symbol=sym, quantity=qty, currency='USD',
                              price=abs(net / qty), net_amount=net, account='b',
                              id=i)

    @staticmethod
    def _trip(book):
        out = []
        for r in book:
            with contextlib.redirect_stderr(io.StringIO()):
                out.append(TaxTransaction(**parse_tt_line(
                    tx_to_tt_line(r.to_dict()), 'b')))
        return out

    @staticmethod
    def _figures(r):
        return (sorted((e['symbol'], e['qty'], round(e['gain'], 2),
                        e.get('acquired_date')) for e in r['transactions']),
                sorted((lot['symbol'], lot['qty'], round(lot['basis'], 2),
                        lot['date']) for lot in r['lots_at']))

    def test_a_355_spin_off_keeps_the_parents_dates(self):
        book = ([self._t('b1', '2024-01-08', 'QZRX', 100, -1000)]
                + _spin_rows(200, 50)
                + [self._t('s1', '2025-06-02', 'QZSP', -50, 400)])
        made = self._figures(_engine(book))
        self.assertEqual(made[0], [('QZSP', 50.0, 200.0, '2024-01-08')])
        # Base: the round trip's sale is of shares acquired 2025-04-01.
        self.assertEqual(self._figures(_engine(self._trip(book))), made)

    def test_a_356_exchange_keeps_the_boot_gain_and_the_dates(self):
        book = ([self._t('b1', '2024-01-08', 'QZRX', 100, -1000)]
                + _boot_rows(100, 50, 300, 20)
                + [self._t('s2', '2025-12-01', 'QZNW', -50, 1200)])
        made = self._figures(_engine(book))
        self.assertEqual(made[0], [('QZNW', 50.0, 200.0, '2024-01-08'),
                                   ('QZRX', 100, 300.0, '2024-01-08')])
        # Base: the new shares read back as bought 2025-06-20.
        self.assertEqual(self._figures(_engine(self._trip(book))), made)


class TestARowNoLineSaysIsRefused(unittest.TestCase):

    def _convert(self, rows):
        with tempfile.TemporaryDirectory() as td:
            book = Path(td) / "margin.json"
            book.write_text(json.dumps({"transactions": rows}))
            with contextlib.redirect_stderr(io.StringIO()):
                return list(json_to_tt_lines(book))

    def test_every_refused_kind_names_the_row_and_the_type(self):
        for typ, rows in REFUSED.items():
            for row in rows:
                with self.subTest(type=typ), self.assertRaises(
                        InputContentError) as cm:
                    self._convert([_trade(None), dict(row, id='r-1')])
                text = str(cm.exception)
                self.assertIn("margin.json: transaction at index 1 (id='r-1', "
                              f"{row['action']} 2025-03-03 QZRX.US)", text)
                self.assertIn(f"a row of type {typ} ", text)
                self.assertIn("nothing was written", text)

    def test_a_lot_move_says_why(self):
        # Base: written `TRANSFER ... -10.00000000 USD 0.00000000 0.00000`,
        # a plain transfer out.
        with self.assertRaises(InputContentError) as cm:
            self._convert(REFUSED[RT.LOT_MOVE_TYPE])
        self.assertIn("a row of type lot_move is one leg of a move between two "
                      "of your taxable accounts, which `tjs run` pairs with the "
                      "other account's leg by the rows' description", str(cm.exception))
        self.assertEqual(not_tt_why(REFUSED[RT.LOT_MOVE_TYPE][0]), 'row_type')

    def test_a_type_no_line_reads_back(self):
        # Base: each written as an ordinary line.
        for row, said in (
                (_trade('made_up_kind'), "a row of type made_up_kind is a kind no "
                 "BUYSELL line carries: its .tt line would read back as an "
                 "ordinary row"),
                (dict(BASE, action='DIVIDEND', type='dividend_in_lieu',
                      symbol='QZRX.US', quantity=10.0, price=0.5, net_amount=5.0,
                      gross_amount=5.0),
                 "a row of type dividend_in_lieu is a kind no DIVIDEND line "
                 "carries: its .tt line would read back as a row of type "
                 "dividend"),
                (dict(_trade(RT.SPINOFF_355_TYPE)),
                 "a row of type spinoff_355 is a kind `tjs run` makes, and this "
                 "one lacks what its line needs"),
                (dict(_transfer(RT.LISTING_JOURNAL_TYPE), journal_pair='two words'),
                 "is not a single word a `pair=` token can carry")):
            with self.subTest(said=said), self.assertRaises(
                    InputContentError) as cm:
                self._convert([row])
            self.assertIn(said, str(cm.exception))
            self.assertEqual(not_tt_why(row), 'row_type')

    def test_the_rows_a_parser_makes_convert_as_before(self):
        rows = [dict(_trade(None), id='a'),
                dict(BASE, action='DIVIDEND', type='dividend', symbol='QZRX.US',
                     quantity=10.0, price=0.5, net_amount=5.0, gross_amount=5.0),
                dict(BASE, action='TAX', type='tax', symbol='QZRX.US',
                     quantity=0.0, price=0.0, net_amount=1.0, gross_amount=1.0),
                dict(BASE, action='INTEREST', type='interest', symbol='CASH',
                     net_amount=1.0),
                dict(BASE, action='FEE', type='fee', symbol='CASH', net_amount=1.0),
                dict(_transfer(None), type='')]
        self.assertEqual([ln.split()[0] for ln in self._convert(rows)],
                         ['BUYSELL', 'DIVIDEND', 'TAX', 'INTEREST', 'FEE',
                          'TRANSFER'])


class TestTheReaderOnTheNewTokens(unittest.TestCase):

    def refused(self, line):
        with self.assertRaises(ValueError) as cm, \
                contextlib.redirect_stderr(io.StringIO()):
            parse_tt_line(line, account_name='margin', source='hand.tt:3')
        text = str(cm.exception)
        self.assertTrue(text.startswith("hand.tt:3: "), text)
        return text

    def test_a_lot_move_line_is_refused(self):
        text = self.refused("TRANSFER 2025-03-03 10:00:00 QZRX.US -10 USD 0 0 "
                            "type=lot_move")
        self.assertIn("type=lot_move: a move between two of your taxable "
                      "accounts is two rows `tjs run` pairs by their "
                      "description", text)

    def test_what_a_transfer_line_does_not_take(self):
        head = "TRANSFER 2025-03-03 10:00:00 QZRX.US 10 USD 0 0 "
        for tail, said in (
                ("type=listing_journal", "pair= names the journal the leg belongs to"),
                ("pair=j-1", "unknown .tt token pair=j-1"),
                ("type=own_account_move pair=j-1", "on a type=listing_journal line only"),
                ("type=listing_journal pair=a/b", "pair= names the journal"),
                ("type=trade_cancel", "unknown .tt token type=trade_cancel"),
                ("type=futures_settlement", "unknown .tt token type=futures_settlement"),
                ("type=spinoff_355", "unknown .tt token type=spinoff_355"),
                ("exercise_of=QZSP.US", "unknown .tt token exercise_of=QZSP.US"),
                ("type=own_account_move type=listing_journal", "type= is given twice")):
            with self.subTest(tail=tail):
                self.assertIn(said, self.refused(head + tail))
        self.assertIn("its QTY is not 0", self.refused(
            head.replace(" 10 USD", " 0 USD") + "type=own_account_move"))

    def test_what_a_trade_line_does_not_take(self):
        head = "BUYSELL 2025-03-03 10:00:00 QZRX.US 10 USD 5 50 0 "
        for tail, said in (
                ("type=spinoff_355", "the line needs the event it belongs to"),
                ("type=reorg_356", "the line needs the event it belongs to"),
                ("type=reorg_356 boot=3 corp=e", "on the old shares' line (QTY "
                 "negative)"),
                ("type=transfer_custody boot=3", "unknown .tt token boot=3"),
                ("type=transfer_custody ex=2025-02-20", "unknown .tt token ex="),
                ("type=spinoff_355 ex=nope corp=e", "ex=nope is not a date"),
                ("type=lot_move", "a BUYSELL line's only types are")):
            with self.subTest(tail=tail):
                self.assertIn(said, self.refused(head + tail))
        self.assertIn("its QTY is positive", self.refused(
            head.replace(" 10 USD", " -10 USD") + "type=spinoff_355 corp=e"))
        self.assertIn("only a BUYSELL line takes it", self.refused(
            head.replace("BUYSELL", "ASSIGN") + "type=transfer_custody"))
        self.assertIn("boot=-3", self.refused(
            head.replace(" 10 USD", " -10 USD") + "type=reorg_356 boot=-3 corp=e"))

    def test_an_adjust_line_takes_a_spin_off_with_its_event_only(self):
        head = "ADJUST 2025-03-03 10:00:00 QZRX.US USD -5.00 "
        self.assertIn("the line needs the event it belongs to",
                      self.refused(head + "type=spinoff_355"))
        self.assertIn("only an ADJUST line takes a type",
                      self.refused(head + "type=reorg_356 corp=e"))
        with contextlib.redirect_stderr(io.StringIO()):
            tx = parse_tt_line(head + "type=spinoff_355 corp=e", 'margin')
        self.assertEqual((tx['type'], tx['corp_event_id']), ('spinoff_355', 'e'))


if __name__ == "__main__":
    unittest.main()
