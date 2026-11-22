"""IB's cash events from its own parse (PLAN step 16 commit 10, backlog
TJ-4450 to TJ-4455): read notes the rows, the finish builds them, as
lib/cash_readers/ib.py read them. Commit 10 put its class in
test_ib_forex_rows_and_refund_dates, one of the files the split module
test_fix_1029_r7_ibcash became, though it was never that module's;
commit 12b moved it here (TJ-4464). Synthetic data only; account ids are
fake (pii-ok).
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from _support.ib_statement_rows import HEAD, TRADES_H, _trade

_ACCT = 'U1234567'
_PERIOD = 'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
_CASH_H = 'Cash Report,Header,Currency Summary,Currency,Total\n'


def _cash(lines):
    return _CASH_H + ''.join(f'Cash Report,Data,{a},{c},{v}\n'
                             for a, c, v in lines)


_DW_H = 'Deposits & Withdrawals,Header,Currency,Settle Date,Description,Amount\n'


def _dw(day, amount, desc='Electronic Fund Transfer', cur='USD'):
    return f'Deposits & Withdrawals,Data,{cur},{day},{desc},{amount}\n'


_W1, _W2 = '2025-07-01, 11:00:00', '2025-07-02, 11:00:00'


def _forex(*fills, head=HEAD + _PERIOD, more=''):
    """A made-up statement of Forex fills, each (symbol, when, quantity,
    proceeds[, code, commission, currency, level]) at the price 1.35, then
    `more`, then a Cash Report reconciling the rows the parse reads (a
    Trade-level row beside an Order one is the same execution)."""
    rows, trades, comms = '', {}, {}
    for sym, when, qty, proceeds, *rest in fills:
        code, comm, cur, disc = [*rest] + ['O', 0, None, 'Order'][len(rest):]
        base, quote = sym.split('.')
        rows += _trade(sym, when, qty, 1.35, proceeds, comm, code=code, cat='Forex',
                       cur=cur or quote, acct=_ACCT, disc=disc)
        if disc != 'Trade':
            trades[base] = trades.get(base, 0) + qty
            trades[quote] = trades.get(quote, 0) + proceeds
            if comm:
                comms[cur or quote] = comms.get(cur or quote, 0) + comm
    return (head + TRADES_H + rows + more
            + _cash([('Starting Cash', 'USD', 0), ('Ending Cash', 'USD', 0)]
                    + [('Trades (Sales)', c, round(v, 6)) for c, v in sorted(trades.items())]
                    + [('Commissions', c, round(v, 6)) for c, v in sorted(comms.items())]))


class TestIbCashFromItsParse(unittest.TestCase):
    """PLAN step 16 commit 10: IB's cash events come from its parse (read
    notes the rows, the finish builds them) as lib/cash_readers/ib.py read
    them. A cell the parse itself never reads that the events cannot read
    is the file's cash problem, worded as the reader's error was (each text
    below is the reader's at b8619606), never a refused parse; the events
    are then none."""

    _OK = _cash([('Starting Cash', 'USD', 0), ('Ending Cash', 'USD', 300)])
    _NUM = (" is not a number this parser accepts (decimal commas, "
            "space-grouped digits and text are refused rather than guessed)")
    _FXB_H = 'Forex Balances,Header,Asset Category,Currency,Description,Quantity\n'

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)

    def _write(self, text, name='ib.csv'):
        p = self.dir / name
        p.write_text(text)
        return p

    def _parsed(self, text, raw=False, name='ib.csv'):
        from _support.parser_cash import parsed
        p = self._write(text, name)
        if raw:
            p.write_bytes(text.encode())
        return parsed('ib', p)

    def test_a_cash_cell_the_parse_never_reads_is_the_files_problem(self):
        cases = [
            # A later row's bad date drops the events of the rows before it.
            (_DW_H + _dw('2025-02-03', 100) + _dw('06/02/2025', 200) + self._OK,
             "ib.csv:7: Deposits & Withdrawals date '06/02/2025' is not YYYY-MM-DD"),
            (_DW_H + _dw('2025-02-03', '2OO') + self._OK, "ib.csv:6: Amount '2OO'" + self._NUM),
            (_DW_H + _dw('2025-02-03', 300)
             + _cash([('Starting Cash', 'USD', 'n/a'), ('Ending Cash', 'USD', 300)]),
             "ib.csv:8: Total 'n/a'" + self._NUM),
            (_DW_H + _dw('2025-02-03', 300) + self._OK + self._FXB_H
             + 'Forex Balances,Data,Forex,USD,USD,3OO\n', "ib.csv:11: Quantity '3OO'" + self._NUM),
            # The first in the rows' order.
            (_DW_H + _dw('2025-02-03', 'x1') + _dw('2025/02/04', 2) + self._OK,
             "ib.csv:6: Amount 'x1'" + self._NUM),
        ]
        for body, why in cases:
            with self.subTest(why=why):
                ex = self._parsed(HEAD + _PERIOD + body)
                self.assertEqual((ex.cash_events, ex.cash_problem),
                                 ([], 'BrokerageParseError: ' + why))
        ex = self._parsed(HEAD + _DW_H + _dw('2025-02-03', 300) + self._OK)
        self.assertEqual(ex.cash_problem, 'BrokerageParseError: ib.csv: a Cash Report but '
                                          'no Statement Period — the balances cannot be dated')

    def test_a_cells_line_ends_are_read_as_text_mode_reads_them(self):
        # A quoted cell over two lines of a CRLF file: its line end is "\n"
        # in the problem's text, as the reader's text-mode read had it.
        text = (HEAD + _PERIOD + _DW_H + 'Deposits & Withdrawals,Data,USD,'
                '"2025-02-03\nx",Electronic Fund Transfer,300\n' + self._OK)
        ex = self._parsed(text.replace('\n', '\r\n'), raw=True)
        self.assertEqual(ex.cash_problem, "BrokerageParseError: ib.csv:6: Deposits & "
                                          "Withdrawals date '2025-02-03\\nx' is not YYYY-MM-DD")

    def test_the_problem_reaches_the_sidecar_and_the_parse_passes(self):
        import _paths
        side = self.dir / 'm.cash'
        p = self._write(HEAD + _PERIOD + _DW_H + _dw('2025/02/03', 300) + self._OK)
        r = subprocess.run(python_module_argv(
            'taxjson.bin.taxjson_brokerage',
            ['--brokerage', 'ib', str(p), '--country', 'canada', '--account', 'm',
             '--cash-out', str(side)]),
            cwd=_paths.REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env=dict(os.environ, TAXJSON_OFFLINE='1', TAXJSON_WIDTH='0'), timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr)
        (entry,) = json.loads(side.read_text(encoding='utf-8'))['files']
        self.assertEqual((entry['events'], entry['problem']),
                         ([], "BrokerageParseError: ib.csv:6: Deposits & Withdrawals "
                              "date '2025/02/03' is not YYYY-MM-DD"))

    def test_the_accounts_a_statement_names_are_one_book(self):
        # Accounts Included alone names two: one book, said; an Account
        # cell of a total is no account.
        two = (HEAD + _PERIOD + 'Account Information,Header,Field Name,Field Value\n'
               'Account Information,Data,Accounts Included,"U1234567, U9990002"\n'
               + _DW_H + _dw('2025-02-03', 300) + self._OK)
        notes = [e['text'] for e in self._parsed(two).cash_events if e['kind'] == 'NOTE']
        self.assertEqual(len(notes), 1)
        self.assertIn('one IB statement of 2 accounts (#', notes[0])
        one = (HEAD + _PERIOD + 'Deposits & Withdrawals,Header,Currency,Account,'
               'Settle Date,Description,Amount\n'
               'Deposits & Withdrawals,Data,USD,U1234567,2025-02-03,EFT,300\n'
               'Deposits & Withdrawals,Data,USD,Total,2025-02-03,EFT,0\n' + self._OK)
        self.assertEqual([e['kind'] for e in self._parsed(one).cash_events],
                         ['CASHMOVE', 'PERIOD', 'CASHBAL', 'CASHBAL'])

    def test_a_currencys_cash_report_lines_add_up(self):
        ex = self._parsed(HEAD + _PERIOD + _DW_H + _dw('2025-02-03', 300)
                          + _cash([('Starting Cash', 'USD', 0), ('Ending Cash', 'USD', 100),
                                   ('Ending Cash', 'USD', 200)]))
        self.assertEqual([(e['date'], e['balance']) for e in ex.cash_events
                          if e['kind'] == 'CASHBAL'],
                         [('2025-12-31', 300.0), ('2024-12-31', 0.0)])

    def test_a_moves_cells_are_read_as_the_export_holds_them(self):
        # Its description before IB's temporary symbols are folded (the
        # parse's own rows): its first three words, digits masked.
        from _support.ib_temp_symbol import STAMP, _TEMP_SYMBOL_BODY
        ex = self._parsed(_TEMP_SYMBOL_BODY + _DW_H
                          + _dw('2026-06-02', 300, desc=f'Wire 12 {STAMP}QZHN from'))
        self.assertEqual([(e['kind'], e['desc'], e['line_where']) for e in ex.cash_events],
                         [('CASHMOVE', 'Wire ## ##############QZHN', 'ib.csv:10')])
        # Its day the settle date, before the trade date.
        ex = self._parsed(HEAD + _PERIOD + 'Deposits & Withdrawals,Header,Currency,'
                          'Settle Date,Date,Description,Amount\n'
                          'Deposits & Withdrawals,Data,USD,2025-02-04,2025-02-03,EFT,300\n'
                          + self._OK)
        self.assertEqual(ex.cash_events[0]['date'], '2025-02-04')

    def test_a_forex_balance_is_read_by_its_currency_column(self):
        ex = self._parsed(HEAD + _PERIOD + _DW_H + _dw('2025-02-04', 300)
                          + _dw('2025-02-04', 500, cur='CAD')
                          + _cash([('Starting Cash', 'USD', 0), ('Ending Cash', 'USD', 300),
                                   ('Starting Cash', 'CAD', 0), ('Ending Cash', 'CAD', 500)])
                          + self._FXB_H + 'Forex Balances,Data,Forex,USD,CAD,500\n')
        self.assertEqual([e['text'] for e in ex.cash_events if e['kind'] == 'NOTE'],
                         ["ib.csv: Forex Balances USD 500.00 differs from the Cash "
                          "Report's Ending Cash 300.00"])

    # The review of commit 10 (TJ-4454): a made-up statement for each rule
    # of the moved code the tests left free, each answer the old reader's
    # (lib/cash_readers/ib.py at b8619606 gave the same events and problem
    # on every statement below).
    @staticmethod
    def _seen(ex, *kinds):
        """The events of `kinds`, each by what a rule below decides."""
        def one(e):
            k = e['kind']
            return ((k, e['date'], e['currency'], e['amount'], e['internal'])
                    if k == 'CASHMOVE' else
                    (k, e['date'], e['from_ccy'], e['from_amt'], e['where'])
                    if k == 'FXCONV' else
                    (k, e['currency'], e['amount'], e['where']) if k == 'FLOW' else
                    (k, e['date'], e['currency'], e['where']) if k == 'NOTE' else
                    (k, e['start'], e['end']) if k == 'PERIOD' else
                    (k, e['date'], e['currency'], e['balance']))
        return [one(e) for e in ex.cash_events if e['kind'] in kinds]

    def test_an_export_named_for_its_account_shows_the_name_masked(self):
        # Every event's place and the file's problem name the file as the
        # diagnostics do (`shown_name`), never by the account id it holds.
        shown = 'U1***_base.csv'
        ex = self._parsed(_forex(('USD.CAD', _W1, -300, 405, 'Ca'),
                                 more=_DW_H + _dw('2025-02-03', 300)),
                          name='U1234567_base.csv')
        self.assertEqual([e['kind'] for e in ex.cash_events],
                         ['CASHMOVE', 'FXCONV', 'NOTE', 'PERIOD', 'CASHBAL', 'CASHBAL'])
        self.assertEqual({e['where'].split(':')[0] for e in ex.cash_events}, {shown})
        self.assertEqual([e.get('line_where') for e in ex.cash_events][:2],
                         [shown + ':8', None])
        self.assertTrue(ex.cash_events[2]['text'].startswith(shown + ':6: IB cancelled'))
        self.assertNotIn('U1234567', json.dumps(ex.cash_events))
        ex = self._parsed(HEAD + _PERIOD + _DW_H + _dw('2025/02/03', 300) + self._OK,
                          name='U1234567_base.csv')
        self.assertEqual(ex.cash_problem, 'BrokerageParseError: ' + shown + ":6: Deposits & "
                                          "Withdrawals date '2025/02/03' is not YYYY-MM-DD")

    def test_tags_and_detail_levels_are_read_as_the_reader_read_them(self):
        move = [('CASHMOVE', '2025-02-03', 'USD', 300.0, '')]
        one = [('FXCONV', '2025-07-01', 'CAD', 405.0, 'ib.csv:6')]
        trade = ('USD.CAD', _W1, 300, -405, 'O', 0, None, 'Trade')
        stock = ('Trades,Data,Order,Stocks,USD,' + _ACCT
                 + ',SYNQ,"2025-02-12, 09:35:14",50,4,4,-200,-1,201,0,0,O\n')
        cases = [
            # A section's and a row type's tags padded with spaces.
            ('padded tags', HEAD + _PERIOD + _DW_H + ' Deposits & Withdrawals , Data ,USD,'
             '2025-02-03,EFT,300\n' + self._OK, move),
            # A blank DataDiscriminator is a Trade row.
            ('blank level', _forex(('USD.CAD', _W1, 300, -405, 'O', 0, None, '')), one),
            # A padded Order row sets the Order level: its Trade row is not read.
            ('padded Order', _forex(('USD.CAD', _W1, 300, -405, 'O', 0, None, ' Order'),
                                    trade), one),
            # The level by the Trades section's latest header.
            ('second header', _forex(('USD.CAD', _W1, 300, -405), trade,
                                     head=HEAD + _PERIOD + 'Trades,Header,Asset Category,'
                                     'Currency,Symbol,Quantity\n'),
             [('FXCONV', '2025-07-01', 'CAD', 405.0, 'ib.csv:7')]),
            # A stock's Order row sets the level for the Forex fills too.
            ('stock Order', HEAD + _PERIOD + TRADES_H + stock
             + _trade('USD.CAD', _W1, 300, 1.35, -405, cat='Forex', cur='CAD', acct=_ACCT,
                      disc='Trade')
             + _cash([('Starting Cash', 'USD', 0), ('Ending Cash', 'USD', 0),
                      ('Trades (Purchase)', 'USD', -200), ('Commissions', 'USD', -1)]), []),
        ]
        for name, text, want in cases:
            with self.subTest(name):
                ex = self._parsed(text)
                self.assertEqual((self._seen(ex, 'CASHMOVE', 'FXCONV'), ex.cash_problem),
                                 (want, ''))

    def test_a_header_row_of_two_cells_stops_the_parse(self):
        # It named no columns: the parse refuses it (the owner's ruling
        # of 2026-11-19, TJ-4569 (4)); its cash is read by no one.
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaisesRegex(BrokerageParseError, "the header row of section "
                                    "'Deposits & Withdrawals' names no columns"):
            self._parsed(HEAD + _PERIOD + _DW_H + 'Deposits & Withdrawals,Header\n'
                         + _dw('2025-02-03', 300) + self._OK)

    def test_which_fill_a_ca_row_cancels_and_what_the_pair_leaves(self):
        note6 = ('NOTE', '2025-07-01', '', 'ib.csv')
        nopx = ('Trades,Header,DataDiscriminator,Asset Category,Currency,Account,Symbol,'
                'Date/Time,Quantity,Proceeds,Comm/Fee,Code\n')
        cases = [
            # A Ca code among others (a comma), and a code that only holds "Ca".
            ('codes', _forex(('USD.CAD', _W1, 300, -405, 'O', -1),
                             ('USD.CAD', _W1, -300, 405, '"Ca,P"', 1),
                             ('USD.CAD', _W2, 200, -272, 'Cancel')),
             [('FXCONV', '2025-07-02', 'CAD', 272.0, 'ib.csv:8')]),
            # A Ca row cancels no Ca row.
            ('Ca of a Ca', _forex(('USD.CAD', _W1, -300, 405, 'Ca'),
                                  ('USD.CAD', _W1, 300, -405, 'Ca')),
             [('FXCONV', '2025-07-01', 'USD', 300.0, 'ib.csv:6'), note6,
              ('FXCONV', '2025-07-01', 'CAD', 405.0, 'ib.csv:7'), note6]),
            # Nor a fill of another day, or of another pair.
            ('another day', _forex(('USD.CAD', _W1, 300, -405), ('USD.CAD', _W2, -300, 405, 'Ca')),
             [('FXCONV', '2025-07-01', 'CAD', 405.0, 'ib.csv:6'),
              ('FXCONV', '2025-07-02', 'USD', 300.0, 'ib.csv:7'),
              ('NOTE', '2025-07-02', '', 'ib.csv')]),
            ('another pair', _forex(('USD.CAD', _W1, 300, -405), ('EUR.CAD', _W1, -300, 405, 'Ca')),
             [('FXCONV', '2025-07-01', 'CAD', 405.0, 'ib.csv:6'),
              ('FXCONV', '2025-07-01', 'EUR', 300.0, 'ib.csv:7'), note6]),
            # Nor a quantity off by more than the rounding.
            ('quantity off', _forex(('USD.CAD', _W1, 300, -405),
                                    ('USD.CAD', _W1, -300.005, 405, 'Ca')),
             [('FXCONV', '2025-07-01', 'CAD', 405.0, 'ib.csv:6'),
              ('FXCONV', '2025-07-01', 'USD', 300.005, 'ib.csv:7'), note6]),
            # With no T. Price column the price decides nothing: the pair goes.
            ('no price', HEAD + _PERIOD + nopx
             + f'Trades,Data,Order,Forex,CAD,{_ACCT},USD.CAD,"{_W1}",300,-405,0,O\n'
             + f'Trades,Data,Order,Forex,CAD,{_ACCT},USD.CAD,"{_W1}",-300,405,0,Ca\n'
             + _cash([('Starting Cash', 'USD', 0), ('Ending Cash', 'USD', 0)]), []),
            # One fill is cancelled once: the second Ca row stays, said.
            ('cancelled twice', _forex(('USD.CAD', _W1, 300, -405), ('USD.CAD', _W1, -300, 405, 'Ca'),
                                       ('USD.CAD', _W1, -300, 405, 'Ca')),
             [('FXCONV', '2025-07-01', 'USD', 300.0, 'ib.csv:8'), note6]),
            ('alone', _forex(('USD.CAD', _W1, -300, 405, 'Ca')),
             [('FXCONV', '2025-07-01', 'USD', 300.0, 'ib.csv:6'), note6]),
            # A commission in its row's Currency, not the pair's quote; a
            # cancelled pair's commissions of two currencies, each kept.
            ('commission currency', _forex(('USD.CAD', _W1, 300, -405, 'O', -2, 'USD')),
             [('FXCONV', '2025-07-01', 'CAD', 405.0, 'ib.csv:6'),
              ('FLOW', 'USD', -2.0, 'ib.csv:6')]),
            ('kept fees', _forex(('USD.CAD', _W1, 300, -405, 'O', -2, 'CAD'),
                                 ('USD.CAD', _W1, -300, 405, 'Ca', -1, 'USD')),
             [('FLOW', 'CAD', -2.0, 'ib.csv:7'), ('FLOW', 'USD', -1.0, 'ib.csv:7')]),
            # A fill of no quantity moves nothing.
            ('no quantity', _forex(('USD.CAD', _W1, 0, -405)), []),
        ]
        for name, text, want in cases:
            with self.subTest(name):
                ex = self._parsed(text)
                self.assertEqual((self._seen(ex, 'FXCONV', 'FLOW', 'NOTE'), ex.cash_problem),
                                 (want, ''))

    def test_a_moves_description_currency_and_amount_read_as_the_reader_read_them(self):
        cases = [
            # A currency not in capitals is no currency (a Total row's place).
            ('lower case', _dw('2025-02-03', 300) + _dw('2025-02-04', 50, cur='usd'),
             [('CASHMOVE', '2025-02-03', 'USD', 300.0, '')], ''),
            # The advance and its cancellation, in any case.
            ('advance', _dw('2025-06-02', 100, 'Adjustment: Deposit Advance (First 100.00)')
             + _dw('2025-06-05', -100, 'CANCELLATION (First 100.00)'),
             [('CASHMOVE', '2025-06-02', 'USD', 100.0, 'advance'),
              ('CASHMOVE', '2025-06-05', 'USD', -100.0, 'advance')], ''),
            ('internal', _dw('2025-03-03', -40, 'internal transfer out'),
             [('CASHMOVE', '2025-03-03', 'USD', -40.0, 'transfer')], ''),
            # A blank amount is 0: no move, no problem.
            ('blank amount', _dw('2025-02-03', 300) + _dw('2025-02-04', ''),
             [('CASHMOVE', '2025-02-03', 'USD', 300.0, '')], ''),
            # A lone carriage return in a cell reads as a line end.
            ('lone CR', 'Deposits & Withdrawals,Data,USD,"2025-02-0\r3",EFT,300\n', [],
             "BrokerageParseError: ib.csv:6: Deposits & Withdrawals date '2025-02-0\\n3' "
             "is not YYYY-MM-DD"),
        ]
        for name, rows, want, problem in cases:
            with self.subTest(name):
                ex = self._parsed(HEAD + _PERIOD + _DW_H + rows + self._OK, raw=True)
                self.assertEqual((self._seen(ex, 'CASHMOVE'), ex.cash_problem), (want, problem))

    def test_the_accounts_the_period_and_the_closing_balances_read_as_the_reader_read_them(self):
        ai = 'Account Information,Header,Field Name,Field Value\n'
        two = '"' + _ACCT + ', 99900099"\n'
        both = (_cash([('Starting Cash', 'USD', 0), ('Ending Cash', 'USD', 300),
                       ('Starting Cash', 'CAD', 0), ('Ending Cash', 'CAD', 500)])
                + self._FXB_H)
        close = [('PERIOD', '2025-01-01', '2025-12-31'), ('CASHBAL', '2025-12-31', 'USD', 300.0),
                 ('CASHBAL', '2024-12-31', 'USD', 0.0)]
        close2 = [('PERIOD', '2025-01-01', '2025-12-31'),
                  ('CASHBAL', '2025-12-31', 'CAD', 500.0), ('CASHBAL', '2025-12-31', 'USD', 300.0),
                  ('CASHBAL', '2024-12-31', 'CAD', 0.0), ('CASHBAL', '2024-12-31', 'USD', 0.0)]
        dw = _DW_H + _dw('2025-02-03', 300)
        cases = [
            # An Account cell holding "total" in any case is no account.
            ('subtotal', 'Deposits & Withdrawals,Header,Currency,Account,Settle Date,Description,'
             'Amount\nDeposits & Withdrawals,Data,USD,' + _ACCT + ',2025-02-03,EFT,300\n'
             'Deposits & Withdrawals,Data,USD,Subtotal,2025-02-03,EFT,0\n' + self._OK, close, ''),
            # Two accounts: one book, said on the period's last day.
            ('two accounts', ai + 'Account Information,Data,Accounts Included,' + two + dw
             + self._OK, [('NOTE', '2025-12-31', '', 'ib.csv')] + close, ''),
            # Accounts Included of Account Information only, each name stripped.
            ('another section', 'Statement,Data,Accounts Included,' + two + dw + self._OK, close, ''),
            ('spaced names', ai + 'Account Information,Data,Accounts Included,"' + _ACCT + ', '
             + _ACCT + '"\n' + dw + self._OK, close, ''),
            # A Forex Balance within 1.00 of the Ending Cash is not said; two
            # that differ, said in their currencies' order.
            ('within 1.00', dw + _dw('2025-02-04', 500, cur='CAD') + both
             + 'Forex Balances,Data,Forex,USD,USD,300.5\n', close2, ''),
            ('two differ', dw + _dw('2025-02-04', 500, cur='CAD') + both
             + 'Forex Balances,Data,Forex,USD,USD,400\nForex Balances,Data,Forex,CAD,CAD,700\n',
             close2 + [('NOTE', '2025-12-31', 'CAD', 'ib.csv'),
                       ('NOTE', '2025-12-31', 'USD', 'ib.csv')], ''),
            # A Forex Balances row of no currency is not read.
            ('no currency', dw + self._OK + self._FXB_H + 'Forex Balances,Data,Forex,Total,,n/a\n',
             close, ''),
            # The last Statement Period wins; another section's Period is not one.
            ('two periods', dw + self._OK
             + 'Statement,Data,Period,"January 1, 2024 - December 31, 2024"\n',
             [('PERIOD', '2024-01-01', '2024-12-31'), ('CASHBAL', '2024-12-31', 'USD', 300.0),
              ('CASHBAL', '2023-12-31', 'USD', 0.0)], ''),
            ('another Period', ai + 'Account Information,Data,Period,"January 1, 2024 - '
             'December 31, 2024"\n' + dw + self._OK, close, ''),
            # A Cash Report line's name padded with spaces.
            ('padded line', dw + _cash([('Starting Cash', 'USD', 0), (' Ending Cash ', 'USD', 300)]),
             close, ''),
        ]
        for name, body, want, problem in cases:
            with self.subTest(name):
                ex = self._parsed(HEAD + _PERIOD + body)
                self.assertEqual((self._seen(ex, 'NOTE', 'PERIOD', 'CASHBAL'), ex.cash_problem),
                                 (want, problem))


if __name__ == '__main__':
    unittest.main()
