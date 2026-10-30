"""Fix round 7, wave G: an income-only IB statement's rebind backed out to
v0.38.0 (backlog TJ-1328, reopening TJ-0669). Synthetic data only; account
ids are fake.

Dating another statement's listings moved a ROC silently in every form tried:
what the replay reads as flat can be a holding older than its rows whose exit
is in a missing export. So a statement with no position in the ticker takes
the account's listings as names only: two listings stay flagged and keep the
row's ISIN listing. The false flags this leaves are TJ-0669, still open: when
it is fixed, the `TJ-0669 known` rows below change on purpose; the rows that
hold a listing must never become a silent rebind."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.ib_extractor import IbBrokerage

_ISIN = 'CA9990000001'
_HEAD = ('Statement,Header,Field Name,Field Value\n'
         'Statement,Data,BrokerName,Interactive Brokers\n'
         'Statement,Data,Title,Activity Statement\n')
_T_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
        'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
        'Realized P/L,MTM P/L,Code\n')
_CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
         'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n')
_OP_H = ('Open Positions,Header,DataDiscriminator,Asset Category,Currency,'
         'Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,'
         'Unrealized P/L,Code\n')
_DIV_H = 'Dividends,Header,Currency,Date,Description,Amount\n'
_Y23 = ('January 1, 2023', 'December 31, 2023')
_Y25 = ('January 1, 2025', 'December 31, 2025')
_A, _B = 'U1234567', 'U7654321'  # pii-ok


def _head(years=_Y25, acct=None):
    s = _HEAD + f'Statement,Data,Period,"{years[0]} - {years[1]}"\n'
    if acct:
        s += ('Account Information,Header,Field Name,Field Value\n'
              f'Account Information,Data,Account,{acct}\n')
    return s


def _tr(day, q, cur, code='O'):
    return (f'Trades,Data,Order,Stocks,{cur},SYN,"{day}, 10:00:00",{q},10,10,'
            f'{-10 * q},0,{10 * q},0,0,{code}\n')


def _op(cur, q):
    return (f'Open Positions,Data,Summary,Stocks,{cur},SYN,{q},1,10,{q * 10},'
            f'10,{q * 10},0,\n')


def _inc(cur, day, years=_Y25):
    return (_head(years) + _DIV_H
            + f'Dividends,Data,{cur},{day},"SYN({_ISIN}) Return of Capital '
              f'{cur} 1 per Share",10\n')


def _sp(cur, new, old, q):
    return (f'Corporate Actions,Data,Stocks,{cur},2025-07-01,'
            f'"2025-07-01, 20:25:00","SYN({_ISIN}) Split {new} for {old} '
            f'(SYN, SYN CORP, {_ISIN})",{q},0,0,0,\n')


def _run(files):
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for n, t in files.items():
            p = Path(td) / n
            p.write_text(t)
            paths.append(p)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
            ctx = IbBrokerage.prepare_files(paths)
            txs = []
            for p in paths:
                b = IbBrokerage()
                b.account_context = ctx
                txs.extend(b.parse_file(p))
    return ([t['symbol'] for t in txs if t['action'] == 'ADJUST'],
            'more than one listing' in err.getvalue())


_FLAGGED = (['SYN.TO'], True)


def _y23_sold_and_rebought(sell_code=''):
    return (_head(_Y23) + _T_H + _tr('2023-01-05', 1, 'USD')
            + _tr('2023-01-20', -5, 'CAD', sell_code)
            + _tr('2023-02-20', 5, 'CAD')
            + _OP_H + _op('USD', 1) + _op('CAD', 5))


_Y25_US_ONLY = _head() + _OP_H + _op('USD', 1)


class TestAHeldListingNeverMovesSilently(unittest.TestCase):
    """Two listings held on the payment date: flagged, on the ISIN listing."""

    def test_tj1328_sold_and_rebought_exit_in_a_missing_export(self):
        for day in ('2023-01-10', '2023-03-10'):
            for code in ('', 'C'):
                with self.subTest(day=day, code=code):
                    self.assertEqual(_run({
                        'y23.csv': _y23_sold_and_rebought(code),
                        'y25.csv': _Y25_US_ONLY,
                        'i.csv': _inc('CAD', day, _Y23)}), _FLAGGED)

    def test_tj1328_sale_then_a_larger_buy(self):
        y23 = (_head(_Y23) + _T_H + _tr('2023-01-05', 1, 'USD')
               + _tr('2023-03-01', -5, 'CAD', '') + _tr('2023-03-02', 8, 'CAD')
               + _OP_H + _op('USD', 1) + _op('CAD', 8))
        y25 = _head() + _OP_H + _op('USD', 1) + _op('CAD', 3)
        self.assertEqual(_run({'y23.csv': y23, 'y25.csv': y25,
                               'i.csv': _inc('CAD', '2023-01-10', _Y23)}),
                         _FLAGGED)

    def test_older_units_with_their_exit_in_a_missing_export(self):
        # 3 SYN.TO older than the rows (8 at the year end, 5 bought), gone
        # by the latest statement. No form of the replay held this one.
        y23 = (_head(_Y23) + _T_H + _tr('2023-01-05', 1, 'USD')
               + _tr('2023-02-20', 5, 'CAD')
               + _OP_H + _op('USD', 1) + _op('CAD', 8))
        self.assertEqual(_run({'y23.csv': y23, 'y25.csv': _Y25_US_ONLY,
                               'i.csv': _inc('CAD', '2023-01-10', _Y23)}),
                         _FLAGGED)

    def test_an_older_statements_holding_gone_by_the_latest(self):
        y22 = (_head(('January 1, 2022', 'December 31, 2022')) + _OP_H
               + _op('CAD', 100) + _op('USD', 1))
        y23 = (_head(_Y23) + _T_H + _tr('2023-02-20', 5, 'CAD')
               + _OP_H + _op('USD', 1) + _op('CAD', 105))
        self.assertEqual(_run({'y22.csv': y22, 'y23.csv': y23,
                               'y25.csv': _Y25_US_ONLY,
                               'i.csv': _inc('CAD', '2023-01-10', _Y23)}),
                         _FLAGGED)

    def test_tj1045_a_holding_older_than_the_trades(self):
        t = (_head() + _T_H + _tr('2025-01-05', 1, 'USD')
             + _tr('2025-06-02', 5, 'CAD')
             + _OP_H + _op('USD', 1) + _op('CAD', 1005))
        self.assertEqual(_run({'t.csv': t,
                               'i.csv': _inc('CAD', '2025-01-10')}), _FLAGGED)

    def test_tj1178_a_reverse_split_and_a_missing_year(self):
        t = (_head() + _T_H + _tr('2025-01-05', 10, 'CAD')
             + _tr('2025-03-03', 10, 'CAD') + _tr('2025-01-05', 2, 'USD')
             + _CA_H + _sp('CAD', 1, 2, -10) + _sp('USD', 1, 2, -1)
             + _OP_H + _op('CAD', 10) + _op('USD', 1))
        self.assertEqual(_run({'t.csv': t,
                               'i.csv': _inc('CAD', '2025-01-10')}), _FLAGGED)
        y23 = (_head(_Y23) + _T_H + _tr('2023-01-05', 5, 'CAD')
               + _tr('2023-01-05', 1, 'USD')
               + _OP_H + _op('USD', 1) + _op('CAD', 5))
        self.assertEqual(_run({'y23.csv': y23, 'y25.csv': _Y25_US_ONLY,
                               'i.csv': _inc('CAD', '2023-03-10', _Y23)}),
                         _FLAGGED)

    def test_tj1243_two_broker_accounts(self):
        a = (_head(acct=_A) + _T_H + _tr('2025-01-06', 1, 'USD')
             + _OP_H + _op('USD', 1))
        b = (_head(acct=_B) + _T_H + _tr('2025-01-06', 500, 'CAD')
             + _OP_H + _op('CAD', 500))
        self.assertEqual(_run({'a.csv': a, 'b.csv': b,
                               'c.csv': _inc('CAD', '2025-03-10')}), _FLAGGED)

    def test_tj1284_two_accounts_read_one_split(self):
        a = (_head(acct=_A) + _T_H + _tr('2025-01-05', 5, 'USD')
             + _tr('2025-02-03', 151, 'CAD')
             + _CA_H + _sp('CAD', 3, 2, -251) + _sp('CAD', 3, 2, 376)
             + _sp('USD', 3, 2, -5) + _sp('USD', 3, 2, 7)
             + _OP_H + _op('CAD', 376) + _op('USD', 7))
        b = (_head(acct=_B) + _T_H + _tr('2025-02-04', 151, 'CAD')
             + _CA_H + _sp('CAD', 3, 2, -151) + _sp('CAD', 3, 2, 226)
             + _OP_H + _op('CAD', 226))
        self.assertEqual(_run({'a.csv': a, 'b.csv': b,
                               'i.csv': _inc('CAD', '2025-01-10')}), _FLAGGED)


class TestTj0669Known(unittest.TestCase):
    """TJ-0669 known: a listing flat on the payment date still counts as
    held, so the row is flagged and stays on its ISIN listing, where the
    same rows in one statement go to the listing held. Never silent."""

    def test_a_later_round_trip(self):
        buy = _head() + _T_H + _tr('2025-01-02', 10, 'USD')
        later = (_head() + _T_H + _tr('2025-02-03', 10, 'CAD')
                 + _tr('2025-02-04', -10, 'CAD', 'C'))
        self.assertEqual(_run({'b.csv': buy, 'l.csv': later,
                               'i.csv': _inc('CAD', '2025-01-10')}), _FLAGGED)

    def test_tj1328_the_flat_day(self):
        self.assertEqual(_run({'y23.csv': _y23_sold_and_rebought(),
                               'y25.csv': _Y25_US_ONLY,
                               'i.csv': _inc('CAD', '2023-02-01', _Y23)}),
                         _FLAGGED)

    def test_tj1296_an_uncoded_short_before_it_opened(self):
        t = (_head() + _T_H + _tr('2025-01-05', 5, 'USD')
             + _tr('2025-02-03', -5, 'CAD', '')
             + _OP_H + _op('USD', 5) + _op('CAD', -5))
        self.assertEqual(_run({'t.csv': t,
                               'i.csv': _inc('USD', '2025-01-10')}), _FLAGGED)

    def test_one_statement_still_goes_to_the_listing_held(self):
        # The statement's own replay is not what was backed out.
        body = (_head() + _T_H + _tr('2025-01-02', 10, 'USD')
                + _tr('2025-02-03', 10, 'CAD')
                + _tr('2025-02-04', -10, 'CAD', 'C'))
        inc = _inc('CAD', '2025-01-10')[len(_head()):]
        self.assertEqual(_run({'one.csv': body + inc}), (['SYN.US'], False))


if __name__ == '__main__':
    unittest.main()
