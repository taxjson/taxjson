"""A share-exchange merger booked as a cost carry-over is a rename in the
books, so the superficial-loss rule does not count the shares it brings
(CA-SL-07): the run names each one whose shares arrive inside a loss's
30-day window, at its end, and denies nothing for it (docs/limits.md;
backlog TJ-4992, TJ-4410). A ticker change says nothing.

Synthetic data only; account labels and tickers are made up.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib import corp_actions as CA
from taxjson.lib import out
from taxjson.lib import share_exchange_windows as SEW
from taxjson.lib.pipeline import GainsRequest, run_gains
from taxjson.model.transaction import TaxTransaction
from _support.ib_merger_election import _SSL_RGLD_CSV

OLD, NEW = 'OLD.TO', 'SYQ.TO'
EVENT = '20250125-old-syq-1a2b'


def _tr(date, sym, qty, net, acct='a'):
    return TaxTransaction(
        action='BUYSELL', date=date, date_settle=date, time='10:00:00',
        symbol=sym, quantity=float(qty), price=abs(net / qty),
        net_amount=float(net), currency='CAD', account=acct)


def _exchange(date, election='rollover_s_85_1_5', acct='a'):
    return TaxTransaction(
        action='SPLIT', date=date, date_settle=date, time='09:00:00',
        symbol=OLD, symbol_new=NEW, quantity=1.0, net_amount=0.0,
        currency='CAD', account=acct, corp_election=election,
        corp_event_id=EVENT if election else '')


# The entry's repro: OLD bought for 50 and SYQ for 100 in November, SYQ sold
# for 80 on January 20 (a loss of 20), the received SYQ sold for 50 in April.
_HEAD = [('2024-11-06', OLD, 100, -50), ('2024-11-07', NEW, 100, -100),
         ('2025-01-20', NEW, -100, 80)]
_TAIL = [('2025-04-01', NEW, -100, 50)]


def _run(middle, sheltered=()):
    book = [_tr(*r) for r in _HEAD] + list(middle) + [_tr(*r) for r in _TAIL]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(book, list(sheltered),
                        req=GainsRequest(country='canada', year=2025, taxable=True))
    gains = [(g['date'], g['symbol'], round(float(g['gain']), 2))
             for g in res['transactions']]
    return gains, [ln for ln in err.getvalue().splitlines() if 'share exchange' in ln]


class TestTheRunNamesTheExchange(unittest.TestCase):
    @rule(R.CA_SL_07)
    def test_an_exchange_inside_the_window_is_named_and_nothing_is_denied(self):
        gains, said = _run([_exchange('2025-01-25')])
        # The limit: the loss stays allowed, the later sale has no gain.
        self.assertEqual(gains, [('2025-01-20', NEW, -20.0), ('2025-04-01', NEW, 0.0)])
        self.assertEqual(len(said), 1)
        line = said[0]
        self.assertTrue(line.startswith(SEW.ATTENTION_SHARE_EXCHANGE + f"{NEW}: "), line)
        for word in ('the 2025-01-20 loss', 'the 2025-01-25 exchange of OLD.TO (a)',
                     'The OLD.TO shares became SYQ.TO shares', 'docs/limits.md',
                     'the loss in a stays allowed in full', 'Review the loss',
                     f'`tjs elect a --set {EVENT}=ignore`',
                     'a sale of the OLD.TO shares at their cost and a purchase '
                     'of the SYQ.TO shares at that cost'):
            self.assertIn(word, line)

    @rule(R.CA_SL_07)
    def test_an_exchange_before_the_loss_is_named_too(self):
        # 200 shares at 150 after the exchange: 100 sold for 60, a loss of 15.
        book = ([_tr(*r) for r in _HEAD[:2]] + [_exchange('2025-01-10')]
                + [_tr('2025-01-20', NEW, -100, 60)])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(book, req=GainsRequest(country='canada', year=2025, taxable=True))
        self.assertIn('the 2025-01-10 exchange of OLD.TO (a)', err.getvalue())

    @rule(R.CA_SL_07)
    def test_a_registered_accounts_exchange_is_named(self):
        own = [_tr('2024-11-06', OLD, 100, -50, acct='plan'),
               _exchange('2025-01-25', election='sheltered_default', acct='plan')]
        book = [_tr('2024-11-07', NEW, 100, -100), _tr('2025-01-20', NEW, -100, 80)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(book, own, req=GainsRequest(country='canada', year=2025,
                                                  taxable=True))
        self.assertIn('the 2025-01-25 exchange of OLD.TO (plan)', err.getvalue())

    @rule(R.CA_SL_01, R.CA_SL_09)
    def test_the_remedy_booked_by_hand_is_counted(self):
        # The exchange as a sale at cost and a purchase at that cost: the
        # purchase is in the window and held at day 30, so the loss is
        # denied and added to the shares received.
        gains, said = _run([_tr('2025-01-25', OLD, -100, 50),
                            _tr('2025-01-25', NEW, 100, -50)])
        self.assertEqual(gains, [('2025-01-20', NEW, 0.0), ('2025-01-25', OLD, 0.0),
                                 ('2025-04-01', NEW, -20.0)])
        self.assertEqual(said, [])


class TestControls(unittest.TestCase):
    def test_a_ticker_change_says_nothing(self):
        for election in ('rename', ''):
            gains, said = _run([_exchange('2025-01-25', election=election)])
            self.assertEqual(gains, [('2025-01-20', NEW, -20.0), ('2025-04-01', NEW, 0.0)])
            self.assertEqual(said, [], election)

    def test_an_exchange_outside_the_window_says_nothing(self):
        _gains, said = _run([_exchange('2025-02-20')])       # day 31
        self.assertEqual(said, [])
        _gains, said = _run([_exchange('2025-02-19')])       # day 30
        self.assertEqual(len(said), 1)

    def test_nothing_held_at_day_30_says_nothing(self):
        # (review X2) The received shares and the rest sold at a loss after
        # the exchange, nothing held 30 days later: no superficial loss.
        book = [_tr('2024-11-06', OLD, 100, -50), _tr('2024-11-07', NEW, 100, -100),
                _exchange('2025-01-10'), _tr('2025-01-20', NEW, -200, 120)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(book, req=GainsRequest(country='canada', year=2025, taxable=True))
        self.assertNotIn('share exchange', err.getvalue())
        # Sold out only after day 30: held at day 30, so it is said.
        book[-1:] = [_tr('2025-01-20', NEW, -100, 60), _tr('2025-03-03', NEW, -100, 60)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(book, req=GainsRequest(country='canada', year=2025, taxable=True))
        self.assertEqual(err.getvalue().count('share exchange: '), 1)

    def test_a_gain_says_nothing(self):
        book = [_tr('2024-11-06', OLD, 100, -50), _tr('2024-11-07', NEW, 100, -100),
                _tr('2025-01-20', NEW, -100, 130), _exchange('2025-01-25')]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(book, req=GainsRequest(country='canada', year=2025, taxable=True))
        self.assertNotIn('share exchange', err.getvalue())

    def test_a_loss_on_another_symbol_says_nothing(self):
        book = [_tr('2024-11-06', OLD, 100, -50), _tr('2024-11-07', 'ZZO.TO', 100, -100),
                _tr('2025-01-20', 'ZZO.TO', -100, 80), _exchange('2025-01-25')]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run_gains(book, req=GainsRequest(country='canada', year=2025, taxable=True))
        self.assertNotIn('share exchange', err.getvalue())


class TestTheElectionsAndTheTopic(unittest.TestCase):
    def test_the_elections_are_the_merger_carry_overs(self):
        # The Canadian merger's cost carry-over and a registered account's
        # default: what lib/corp_actions stamps on the SPLIT row.
        rollover = [name for name, _text in CA.CANADA_MERGER.options
                    if name.startswith('rollover')]
        self.assertEqual(SEW.SHARE_EXCHANGE_ELECTIONS,
                         frozenset(rollover + [CA.SHELTERED_DEFAULT]))
        self.assertNotIn(CA.NAME_CHANGE.options[0][0], SEW.SHARE_EXCHANGE_ELECTIONS)

    def test_the_topic_is_shown_and_the_line_is_marked_for_the_end_of_the_run(self):
        self.assertIn('share exchange', out.ATTENTION_TOPICS)
        self.assertIn(out.END_OF_RUN_MARK, SEW.ATTENTION_SHARE_EXCHANGE)


class TestInAProject(unittest.TestCase):
    """An IB share merger elected as a rollover, nine days after a loss on
    the acquirer's shares: said mid-run and again at the end of the run."""

    def test_the_run_ends_with_the_exchange(self):
        buy = ('Trades,Data,Order,Stocks,CAD,ABG,"2025-02-05, 09:31:00",1600,15.90,0,'
               '-25440.0,-1,0,0,0,O\n')
        self.assertIn(buy, _SSL_RGLD_CSV)
        more = (buy + 'Trades,Data,Order,Stocks,CAD,ABH,"2025-02-06, 09:31:00",100,300,0,'
                '-30000.0,-1,0,0,0,O\n'
                'Trades,Data,Order,Stocks,CAD,ABH,"2025-10-10, 09:31:00",-100,250,0,'
                '25000.0,-1,0,0,0,C\n')
        csv = _SSL_RGLD_CSV.replace(buy, more).replace('(ABH.CAD, ', '(ABH, ')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'inputs' / 'margin').mkdir(parents=True)
            (root / 'taxjson.toml').write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
                'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / 'ticker.map').write_text('')
            (root / 'inputs' / 'margin' / 'ib_events.csv').write_text(csv)
            first = tj(root, 'run', '--no-input', check=False)
            self.assertEqual(first.returncode, 3, first.stderr)
            pend = json.loads((root / 'work' / 'pending_elections.json').read_text())
            event = pend['accounts']['margin']['pending'][0]['event_id']
            tj(root, 'elect', 'margin', '--set', f'{event}=rollover_s_85_1_5')
            tj(root, 'run', '--no-input')
            r = tj(root, 'run', '--no-input')
            text = ' '.join((r.stdout + r.stderr).split())
            head = ('Share exchange: ABH.TO: the 2025-10-10 loss is not tested '
                    'against the 2025-10-22 exchange of ABG.TO (margin)')
            done = text.index('Before you trust these numbers')
            self.assertIn(f'Warning: {head}', text[:done])
            self.assertIn(f'Warning: [margin] {head}', text[done:])
            self.assertIn(f'`tjs elect margin --set {event}=ignore`', text[done:])
            # Nothing is denied: the loss is in the year's figure.
            fe = json.loads(tj(root, 'form-export', '--json').stdout)
            self.assertAlmostEqual(fe['gain_unrounded'], -5002.0, places=2)


_IBH = ('Statement,Header,Field Name,Field Value\n'
        'Statement,Data,BrokerName,Interactive Brokers\n'
        'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,'
        'T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
_LOSS = (_IBH + 'Trades,Data,Order,Stocks,CAD,ABH,"2025-02-06, 09:31:00",100,300,0,'
         '-30000.0,-1,0,0,0,O\n'
         'Trades,Data,Order,Stocks,CAD,ABH,"2025-10-10, 09:31:00",-100,250,0,'
         '25000.0,-1,0,0,0,C\n')
_MERGER = _SSL_RGLD_CSV.replace('(ABH.CAD, ', '(ABH, ')


class TestTwoAccounts(unittest.TestCase):
    """The loss in one account, the exchange in another: only the pooled
    pass sees both. Its notice reaches the console and the closing list
    (review X3, X4)."""

    def _end_of_run(self, accounts, files, elect=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            body = ''.join(
                f'[accounts.{a}]\ntype = "{t}"\n'
                + ('plan = "tfsa"\ntransfers = true\n' if t == 'sheltered' else '')
                for a, t in accounts.items())
            (root / 'taxjson.toml').write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
                'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
                + body)
            (root / 'ticker.map').write_text('')
            for rel, text in files.items():
                (root / 'inputs' / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / 'inputs' / rel).write_text(text)
            tj(root, 'run', '--no-input', check=False)
            if elect:
                pend = json.loads((root / 'work' / 'pending_elections.json').read_text())
                event = pend['accounts'][elect]['pending'][0]['event_id']
                tj(root, 'elect', elect, '--set', f'{event}=rollover_s_85_1_5')
            tj(root, 'run', '--no-input')
            r = tj(root, 'run', '--no-input')
            text = ' '.join((r.stdout + r.stderr).split())
            done = text.index('Before you trust these numbers')
            return text[:done], text[done:]

    def _assert_said(self, mid, end, where):
        head = ('Share exchange: ABH.TO: the 2025-10-10 loss is not tested against '
                f'the 2025-10-22 exchange of ABG.TO ({where})')
        self.assertEqual(mid.count(f'Warning: {head}'), 1, mid)
        self.assertEqual(end.count(head), 1, end)
        self.assertIn('Review the loss for the rule', end)

    @rule(R.CA_SL_07)
    def test_two_taxable_accounts(self):
        mid, end = self._end_of_run(
            {'margin': 'taxable', 'other': 'taxable'},
            {'margin/ib_events.csv': _MERGER, 'other/ib.csv': _LOSS}, elect='margin')
        self._assert_said(mid, end, 'margin')

    @rule(R.CA_SL_07)
    def test_a_registered_accounts_merger_and_a_taxable_loss(self):
        mid, end = self._end_of_run(
            {'margin': 'taxable', 'tfsa': 'sheltered'},
            {'tfsa/ib_events.csv': _MERGER, 'margin/ib.csv': _LOSS})
        self._assert_said(mid, end, 'tfsa')


if __name__ == '__main__':
    unittest.main()
