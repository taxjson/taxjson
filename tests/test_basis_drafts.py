"""Broker basis as an opt-in cost draft (owner-approved, 2026-10):
`taxjson find-missing-history --write-purchases [FILE]` drafts `.tt`
purchase lines from the broker's own cost evidence — IB's Trades Basis on
a closing sale with no purchase in the files (one line per IB Closed Lot
when the statement lists them), a Questrade transfer-in's TRANSFER BOOK
VALUE — into inputs/<account>/purchases_draft.tt.txt, a file the run does
not read. tax-logic CA-ACB-15 / US-BASIS-08.

Every fixture is synthetic: invented tickers, fake account ids marked
pii-ok."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.missing_history import (DATE_PLACEHOLDER, DRAFT_NAME,
                                          draft_purchases,
                                          format_purchase_drafts,
                                          parse_broker_lots)

from _style import CapturedWidth
from tax_rules import rule, rule_absent
from tax_rules.dual import cli, projects_both
from test_fix_ibparse import HEAD, TRADES_H, _parse_ib

# Captured output, unwrapped (TAXJSON_WIDTH=0): a message naming a long
# temp path must not wrap inside a phrase a test looks for.
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


REPO_ROOT = Path(__file__).resolve().parent.parent


def _row(disc, sym, when, qty, price='', proceeds='', comm='', code='',
         basis='', cur='USD', cat='Stocks'):
    return (f'Trades,Data,{disc},{cat},{cur},U5550001,{sym},"{when}",'  # pii-ok
            f'{qty},{price},0,{proceeds},{comm},{basis},0,0,{code}\n')


def _sell(sym, when, qty, price, basis, code='C', cur='USD'):
    gross = abs(qty) * price
    return _row('Order', sym, when, qty, price, gross, -1, code,
                -basis if basis else '', cur)


def _buy(sym, when, qty, price, code='O', cur='USD'):
    return _row('Order', sym, when, qty, price, -qty * price, -1, code,
                qty * price + 1, cur)


def _lot(sym, opened, qty, price, basis, cur='USD'):
    return _row('ClosedLot', sym, f'{opened}, 09:30:00', qty, price, '',
                '', '', basis, cur)


def _parsed(body, account='margin'):
    _, txs, _ = _parse_ib(HEAD + TRADES_H + body)
    for t in txs:
        t['account'] = account
    return txs


def _book(rows):
    keys = TaxTransaction.__dataclass_fields__
    return [TaxTransaction(**{k: v for k, v in r.items() if k in keys})
            for r in rows]


def _lines(drafts):
    return [d.tt_line().split() for d in drafts]


# One sale of 100 QZA closing two lots bought before the data (IB Closed
# Lots listed), one sale of 10 QZB with IB's Basis only.
MULTI_LOT = (_sell('QZA', '2025-03-18, 10:00:00', -100, 50, 3001)
             + _lot('QZA', '2023-01-10', 60, 18, 1080.50)
             + _lot('QZA', '2023-06-15', 40, 48, 1920.50))
SINGLE = _sell('QZB', '2025-04-01, 10:00:00', -10, 20, 150)


class TestParserKeepsClosedLots(unittest.TestCase):

    def test_lots_ride_on_the_closing_trade(self):
        txs = _parsed(MULTI_LOT + SINGLE)
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0]['broker_basis'], '3,001.00 USD')
        self.assertEqual(txs[0]['broker_lots'],
                         '2023-01-10 60 1080.50;2023-06-15 40 1920.50')
        self.assertNotIn('broker_lots', txs[1])
        self.assertEqual(parse_broker_lots(txs[0]['broker_lots']),
                         [('2023-01-10', 60.0, 1080.5),
                          ('2023-06-15', 40.0, 1920.5)])

    def test_evidence_only_never_in_the_id(self):
        a, = _book(_parsed(SINGLE))
        b, = _book(_parsed(_sell('QZB', '2025-04-01, 10:00:00', -10, 20,
                                 150)
                           + _lot('QZB', '2024-02-01', 10, 14, 150)))
        self.assertEqual(a.id, b.id)
        self.assertNotIn('broker_lots', a.to_dict())
        self.assertEqual(b.to_dict()['broker_lots'], '2024-02-01 10 150.00')

    def test_another_securitys_lot_or_a_bad_lot_is_not_used(self):
        t, = _parsed(_sell('QZB', '2025-04-01, 10:00:00', -10, 20, 150)
                     + _lot('QZZ', '2024-02-01', 10, 14, 150))
        self.assertNotIn('broker_lots', t)
        t, = _parsed(_sell('QZB', '2025-04-01, 10:00:00', -10, 20, 150)
                     + _lot('QZB', '2024-02-01', 10, 14, 'n/a'))
        self.assertEqual(t['broker_lots'], 'invalid')
        self.assertIsNone(parse_broker_lots('invalid'))


@rule("US-BASIS-08")
class TestUsDrafts(unittest.TestCase):

    def test_one_line_per_ib_lot_with_its_date_and_cost(self):
        drafts, gaps = draft_purchases(_book(_parsed(MULTI_LOT)),
                                       country='usa')
        self.assertEqual(gaps, [])
        self.assertEqual(
            [(l[1], l[3], l[4], l[5], l[7]) for l in _lines(drafts)],
            [('2023-01-10', 'QZA.US', '60', 'USD', '1080.50'),
             ('2023-06-15', 'QZA.US', '40', 'USD', '1920.50')])
        self.assertTrue(all(d.source == 'ib-lot' for d in drafts))

    def test_without_lot_detail_the_date_is_a_placeholder(self):
        d, = draft_purchases(_book(_parsed(SINGLE)), country='usa')[0]
        line = d.tt_line().split()
        self.assertEqual(line[1], DATE_PLACEHOLDER)
        self.assertEqual((line[4], line[7]), ('10', '150.00'))
        self.assertIn('one line per lot', ' '.join(d.comments))

    def test_partly_held_sale_drafts_only_the_lots_before_the_data(self):
        # 40 bought in the data, 100 sold: IB's lots name the 60 bought
        # before the data (and the 40 the data holds).
        body = (_buy('QZA', '2025-01-06, 10:00:00', 40, 45)
                + _sell('QZA', '2025-03-18, 10:00:00', -100, 50, 2881)
                + _lot('QZA', '2023-01-10', 60, 18, 1080.50)
                + _lot('QZA', '2025-01-06', 40, 45, 1800.50))
        d, = draft_purchases(_book(_parsed(body)), country='usa')[0]
        self.assertEqual(d.tt_line().split()[1:8:3],
                         ['2023-01-10', '60', '1080.50'])

    def test_partly_held_sale_without_lots_leaves_the_cost_open(self):
        body = (_buy('QZA', '2025-01-06, 10:00:00', 40, 45)
                + _sell('QZA', '2025-03-18, 10:00:00', -100, 50, 2881))
        d, = draft_purchases(_book(_parsed(body)), country='usa')[0]
        line = d.tt_line().split()
        self.assertEqual((line[1], line[4], line[7]),
                         (DATE_PLACEHOLDER, '60', 'COST'))
        self.assertTrue(d.warn)
        self.assertIn('2,881.00 - 1,801.00 = 1,080.00',
                      ' '.join(d.comments))

    def test_a_renamed_symbol_is_drafted_under_the_brokers_spelling(self):
        # ticker.map sends IB's QZB.US into the books as QZB.TO: the
        # line goes under QZB.US, which the run maps like the sale.
        rows = _parsed(SINGLE)
        rows[0]['symbol'] = 'QZB.TO'
        d, = draft_purchases(_book(rows), country='usa',
                             rename_sources={'QZB.TO': ['QZB.US',
                                                        'QZY.US']})[0]
        self.assertEqual(d.symbol, 'QZB.US')
        self.assertIn('maps it to QZB.TO', ' '.join(d.comments))
        # Two spellings of the same root: not guessed, a CHECK instead.
        d, = draft_purchases(_book(rows), country='usa',
                             rename_sources={'QZB.TO': ['QZB.US',
                                                        'QZB.NE']})[0]
        self.assertEqual(d.symbol, 'QZB.TO')
        self.assertIn('enter the purchase under the broker', ' '.join(
            d.comments))

    def test_a_split_in_the_data_is_undone_on_the_draft(self):
        # A 2-for-1 split in the data after the purchase: the line is in
        # the purchase day's units (the run applies the split to it).
        split = TaxTransaction(action='SPLIT', date='2025-02-03',
                               symbol='QZB.US', quantity=2.0,
                               account='margin')
        d, = draft_purchases([split] + _book(_parsed(SINGLE)),
                             country='usa')[0]
        self.assertEqual(d.quantity, 5.0)
        self.assertEqual(d.tt_line().split()[6:8], ['30.000000', '150.00'])

    def test_real_shorts_writes_and_unpriced_sales_are_not_drafted(self):
        body = (_sell('QZS', '2025-05-01, 10:00:00', -10, 20, 0, code='O')
                + _sell('QZN', '2025-05-02, 10:00:00', -5, 20, 0, code='C'))
        drafts, gaps = draft_purchases(_book(_parsed(body)), country='usa')
        self.assertEqual(drafts, [])
        # The real short (code O) is not missing history at all; the
        # closing sale with no Basis is listed as not drafted.
        self.assertEqual([(g.symbol, g.quantity) for g in gaps],
                         [('QZN.US', 5.0)])
        self.assertIn('no broker cost', gaps[0].reason)

    def test_a_pair_is_drafted_whole_when_one_sale_is_in_the_year(self):
        body = (_sell('QZB', '2024-11-04, 10:00:00', -10, 20, 150)
                + _sell('QZB', '2025-04-01, 10:00:00', -5, 20, 70)
                + _sell('QZC', '2024-06-03, 10:00:00', -5, 20, 70))
        book = _book(_parsed(body))
        drafts, _ = draft_purchases(book, country='usa', year=2025)
        self.assertEqual([(d.symbol, d.sale_date) for d in drafts],
                         [('QZB.US', '2024-11-04'),
                          ('QZB.US', '2025-04-01')])
        drafts, _ = draft_purchases(book, country='usa')
        self.assertEqual(len(drafts), 3)

    def test_the_file_lists_what_was_not_drafted(self):
        body = (SINGLE
                + _sell('QZN', '2025-05-02, 10:00:00', -5, 20, 0))
        drafts, gaps = draft_purchases(_book(_parsed(body)), country='usa')
        text = format_purchase_drafts(drafts, gaps, country='usa',
                                      account='margin')
        self.assertTrue(text.startswith(f'# {DRAFT_NAME}'))
        self.assertEqual(sum(1 for l in text.splitlines()
                             if l.startswith('BUYSELL')), 1)
        tail = text.split('# Not drafted', 1)[1]
        self.assertIn('QZN.US 2025-05-02 5 units: no broker cost', tail)
        # Every non-line is a comment: the .tt reader skips them.
        self.assertTrue(all(l.startswith(('#', 'BUYSELL')) or not l
                            for l in text.splitlines()))

    def test_sheltered_accounts_are_not_drafted(self):
        rows = _parsed(SINGLE, account='rrsp')
        drafts, gaps = draft_purchases(_book(rows), country='usa',
                                       registered_accounts={'rrsp': True})
        self.assertEqual(drafts, [])
        self.assertIn('sheltered', gaps[0].reason)


@rule("CA-ACB-15")
class TestCanadaDrafts(unittest.TestCase):

    def test_usd_draft_keeps_the_currency_and_says_how_it_converts(self):
        d, = draft_purchases(_book(_parsed(SINGLE)), country='canada')[0]
        line = d.tt_line().split()
        self.assertEqual((line[1], line[5], line[7]),
                         (DATE_PLACEHOLDER, 'USD', '150.00'))
        notes = ' '.join(d.comments)
        self.assertIn('Bank of Canada rate', notes)
        self.assertIn('not your ACB', notes)

    def test_another_taxable_account_holding_it_is_a_check(self):
        other = _parsed(_buy('QZB', '2025-02-03, 10:00:00', 5, 18),
                        account='cash')
        d, = draft_purchases(_book(_parsed(SINGLE) + other),
                             country='canada',
                             accounts={'margin'})[0]
        self.assertTrue(d.warn)
        self.assertIn('also hold or trade QZB.US in cash',
                      ' '.join(d.comments))

    def test_transfer_in_book_value_is_drafted_with_the_date_open(self):
        rows = [
            {'action': 'TRANSFER', 'date': '2025-02-10', 'symbol': 'QZT.TO',
             'quantity': 30.0, 'currency': 'CAD', 'net_amount': 600.0,
             'account': 'margin', 'id': 'ab12cd',
             'description': 'QZT CORP TRANSFER BOOK VALUE 600.00'},
            # A move from another of the owner's accounts: not drafted.
            {'action': 'TRANSFER', 'date': '2025-02-10', 'symbol': 'QZU.TO',
             'quantity': 10.0, 'currency': 'CAD', 'net_amount': 100.0,
             'account': 'margin',
             'description': 'QZU CORP TRANSFER BOOK VALUE 100.00'},
            {'action': 'TRANSFER', 'date': '2025-02-08', 'symbol': 'QZU.TO',
             'quantity': -10.0, 'currency': 'CAD', 'account': 'cash',
             'description': 'QZU CORP TFER TO'},
            # No stated book value: nothing to draft from.
            {'action': 'TRANSFER', 'date': '2025-02-10', 'symbol': 'QZV.TO',
             'quantity': 5.0, 'currency': 'CAD', 'net_amount': 0.0,
             'account': 'margin', 'description': 'QZV CORP TRANSFER'},
        ]
        drafts, gaps = draft_purchases([], country='canada',
                                       transfer_rows=rows)
        d, = drafts
        line = d.tt_line().split()
        self.assertEqual((line[1], line[3], line[4], line[7]),
                         (DATE_PLACEHOLDER, 'QZT.TO', '30', '600.00'))
        self.assertIn('ORIGINAL purchase date', ' '.join(d.comments))
        self.assertIn('ab***', d.comments[0])
        self.assertEqual([g.symbol for g in gaps], ['QZU.TO'])
        self.assertIn('move from your account cash', gaps[0].reason)

    def test_a_sale_of_transferred_shares_is_fixed_by_the_draft(self):
        # The transfer-in stays out of the taxable books, so its later
        # sale goes short with no broker cost: the transfer's draft is
        # its fix, not a second "not drafted" item.
        sale = TaxTransaction(action='BUYSELL', date='2025-06-02',
                              symbol='QZT.TO', quantity=-30,
                              net_amount=750, currency='CAD',
                              account='margin')
        rows = [{'action': 'TRANSFER', 'date': '2025-02-10',
                 'symbol': 'QZT.TO', 'quantity': 30.0, 'currency': 'CAD',
                 'account': 'margin',
                 'description': 'QZT CORP TRANSFER BOOK VALUE 600.00'}]
        drafts, gaps = draft_purchases([sale], country='canada',
                                       transfer_rows=rows)
        self.assertEqual([d.source for d in drafts], ['transfer'])
        self.assertEqual(gaps, [])

    def test_transfer_the_run_books_at_book_value_is_not_drafted(self):
        booked = TaxTransaction(action='BUYSELL', date='2025-02-10',
                                symbol='QZT.TO', quantity=30,
                                net_amount=600, currency='CAD',
                                account='margin', type='transfer_book_value')
        rows = [{'action': 'TRANSFER', 'date': '2025-02-10',
                 'symbol': 'QZT.TO', 'quantity': 30.0, 'currency': 'CAD',
                 'account': 'margin',
                 'description': 'QZT CORP TRANSFER BOOK VALUE 600.00'}]
        drafts, gaps = draft_purchases([booked], country='canada',
                                       transfer_rows=rows)
        self.assertEqual(drafts, [])
        self.assertIn('already books it', gaps[0].reason)

    def test_rbc_wording_and_a_ticker_map_rename(self):
        # RBC states the book value as "ACCOUNT TRANSFER BOOK VALUE n";
        # the sidecar keeps the broker's spelling (QZW.US) while the
        # books are mapped (QZW.TO): the sale of the transferred shares
        # is matched through the map, and the line keeps QZW.US.
        sale = TaxTransaction(action='BUYSELL', date='2025-06-02',
                              symbol='QZW.TO', quantity=-12,
                              net_amount=750, currency='CAD',
                              account='margin')
        rows = [{'action': 'TRANSFER', 'date': '2025-02-10',
                 'symbol': 'QZW.US', 'quantity': 12.0, 'currency': 'USD',
                 'account': 'margin',
                 'description': 'QZW - QZW CORP ACCOUNT TRANSFER BOOK '
                                'VALUE        480.00 FROM ACCOUNT 1'}]
        drafts, gaps = draft_purchases(
            [sale], country='canada', transfer_rows=rows,
            symbol_key=lambda s: {'QZW.US': 'QZW.TO'}.get(s, s),
            listed_pairs={('QZW.TO', 'margin')})
        d, = drafts
        self.assertIn('remove QZW.TO / margin from missing_history.json',
                      ' '.join(d.comments))
        self.assertEqual(d.tt_line().split()[3:8:2], ['QZW.US', 'USD',
                                                      '480.00'])
        self.assertEqual(gaps, [])
        # The row's own text (which names the other account) is never
        # copied into the draft.
        text = format_purchase_drafts(drafts, gaps, country='canada',
                                      account='margin')
        self.assertNotIn('FROM ACCOUNT', text)

    def test_transfer_whose_shares_the_books_hold_is_not_drafted(self):
        # The shares' purchase is in another export of the account: no
        # sale goes short, so a line would count them twice.
        buy = TaxTransaction(action='BUYSELL', date='2023-04-03',
                             symbol='QZT.TO', quantity=30, net_amount=600,
                             currency='CAD', account='margin')
        sale = TaxTransaction(action='BUYSELL', date='2025-06-02',
                              symbol='QZT.TO', quantity=-30,
                              net_amount=750, currency='CAD',
                              account='margin')
        rows = [{'action': 'TRANSFER', 'date': '2025-02-10',
                 'symbol': 'QZT.TO', 'quantity': 30.0, 'currency': 'CAD',
                 'account': 'margin',
                 'description': 'QZT CORP TRANSFER BOOK VALUE 600.00'}]
        drafts, gaps = draft_purchases([buy, sale], country='canada',
                                       transfer_rows=rows)
        self.assertEqual(drafts, [])
        self.assertIn('count it twice', gaps[0].reason)
        # Partly held: drafted with a CHECK, and the short sale's rest
        # is what the transfer delivered.
        sale.quantity = -50
        drafts, gaps = draft_purchases([buy, sale], country='canada',
                                       transfer_rows=rows)
        d, = drafts
        self.assertTrue(d.warn)
        self.assertIn('at most 20 units short', ' '.join(d.comments))
        self.assertEqual(gaps, [])

    def test_transfer_covered_by_tt_lines_is_not_drafted_again(self):
        tt = TaxTransaction(action='BUYSELL', date='2021-03-15',
                            symbol='QZT.TO', quantity=30, net_amount=600,
                            currency='CAD', account='margin',
                            source='purchases.tt')
        rows = [{'action': 'TRANSFER', 'date': '2025-02-10',
                 'symbol': 'QZT.TO', 'quantity': 30.0, 'currency': 'CAD',
                 'account': 'margin',
                 'description': 'QZT CORP TRANSFER BOOK VALUE 600.00'}]
        drafts, gaps = draft_purchases([tt], country='canada',
                                       transfer_rows=rows)
        self.assertEqual(drafts, [])
        self.assertIn('already cover', gaps[0].reason)


class TestPlaceholdersAreRefused(unittest.TestCase):
    """An unedited draft renamed to .tt cannot book a guess."""

    def _parse(self, line):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = parse_tt_line(line, 'margin', source='purchases.tt:3')
        return tx, err.getvalue()

    def test_date_placeholder_is_refused(self):
        d, = draft_purchases(_book(_parsed(SINGLE)), country='usa')[0]
        with self.assertRaisesRegex(ValueError, "'YYYY-MM-DD' is not a "
                                                "valid YYYY-MM-DD date"):
            self._parse(d.tt_line())

    def test_cost_placeholder_is_refused_once_the_date_is_filled(self):
        body = (_buy('QZA', '2025-01-06, 10:00:00', 40, 45)
                + _sell('QZA', '2025-03-18, 10:00:00', -100, 50, 2881))
        d, = draft_purchases(_book(_parsed(body)), country='usa')[0]
        with self.assertRaisesRegex(ValueError, 'malformed .tt line'):
            self._parse(d.tt_line().replace(DATE_PLACEHOLDER, '2023-01-10'))

    def test_filled_lines_parse_cleanly(self):
        drafts, _ = draft_purchases(_book(_parsed(MULTI_LOT + SINGLE)),
                                    country='usa')
        for d in drafts:
            tx, err = self._parse(d.tt_line().replace(DATE_PLACEHOLDER,
                                                      '2022-05-02'))
            self.assertEqual(err, '')       # the total-vs-price check
            self.assertGreater(tx['quantity'], 0)
            self.assertAlmostEqual(tx['net_amount'], d.cost, places=2)

    def test_the_run_does_not_read_a_draft(self):
        from taxjson.bin.taxjson_run import input_files
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / DRAFT_NAME).write_text('BUYSELL ...\n')
            (Path(td) / 'start.tt').write_text('# x\n')
            self.assertEqual([p.name for p in input_files(Path(td), '.tt')],
                             ['start.tt'])


def _project_files(rows, account='margin'):
    return {f'work/{account}_base.json': json.dumps(
                {'transactions': rows}),
            f'inputs/{account}/ib.csv': ''}


class TestWritePurchasesCommand(unittest.TestCase):

    @rule("CA-ACB-15")
    @rule_absent("CA-ACB-15", country="usa")
    @rule("US-BASIS-08")
    @rule_absent("US-BASIS-08", country="canada")
    def test_same_book_both_countries(self):
        with tempfile.TemporaryDirectory() as td:
            projects = projects_both(
                td, files=_project_files(_parsed(MULTI_LOT + SINGLE)))
            out = {}
            for c, root in projects.items():
                r = cli(root, 'find-missing-history', '--write-purchases')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('Wrote 3 draft purchase line(s) for margin',
                              r.stdout)
                self.assertIn('2 from IB\'s closed lots', r.stdout)
                out[c] = (root / 'inputs' / 'margin' / DRAFT_NAME
                          ).read_text()
            # Same lines in both countries (the original currency, IB's
            # lot dates); the notes follow each country's rule only.
            lines = {c: [l for l in t.splitlines()
                         if l.startswith('BUYSELL')]
                     for c, t in out.items()}
            self.assertEqual(lines['canada'], lines['usa'])
            self.assertIn('CA-ACB-15', out['canada'])
            self.assertIn('s.47', out['canada'])
            self.assertNotIn('US-BASIS-08', out['canada'])
            self.assertNotIn('holding period', out['canada'])
            self.assertIn('US-BASIS-08', out['usa'])
            self.assertIn('holding period', out['usa'])
            self.assertNotIn('CA-ACB-15', out['usa'])
            self.assertNotIn('s.47', out['usa'])
            self.assertNotIn('Bank of Canada', out['usa'])

    @rule("US-BASIS-08")
    def test_never_overwrites_a_draft_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=_project_files(
                _parsed(SINGLE)))['usa']
            draft = root / 'inputs' / 'margin' / DRAFT_NAME
            r = cli(root, 'find-missing-history', '--write-purchases')
            self.assertEqual(r.returncode, 0, r.stderr)
            draft.write_text(draft.read_text() + '# my edit\n')
            r = cli(root, 'find-missing-history', '--write-purchases')
            self.assertEqual(r.returncode, 1)
            self.assertIn('not overwritten', r.stderr)
            self.assertIn('# my edit', draft.read_text())
            r = cli(root, 'find-missing-history', '--write-purchases',
                    '--force')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn('# my edit', draft.read_text())
            self.assertIn('# my edit', draft.with_name(
                DRAFT_NAME + '.bak').read_text())

    @rule("US-BASIS-08")
    def test_file_argument(self):
        with tempfile.TemporaryDirectory() as td:
            rows = (_parsed(SINGLE)
                    + _parsed(_sell('QZD', '2025-04-01, 10:00:00', -3, 9,
                                    20), account='cash'))
            files = dict(_project_files(rows))
            files['work/cash_base.json'] = json.dumps(
                {'transactions': [r for r in rows
                                  if r['account'] == 'cash']})
            files['work/margin_base.json'] = json.dumps(
                {'transactions': [r for r in rows
                                  if r['account'] == 'margin']})
            root = projects_both(
                td, files=files,
                accounts='[accounts.margin]\ntype = "taxable"\n'
                         '[accounts.cash]\ntype = "taxable"\n')['usa']
            # Two accounts' drafts cannot share one .tt file.
            r = cli(root, 'find-missing-history', '--write-purchases',
                    str(root / 'x.txt'))
            self.assertEqual(r.returncode, 2)
            self.assertIn('a .tt file belongs to one account', r.stderr)
            # A name the run would read is refused.
            r = cli(root, 'find-missing-history', 'cash',
                    '--write-purchases', str(root / 'x.tt'))
            self.assertEqual(r.returncode, 2)
            self.assertIn('must not end in .tt', r.stderr)
            r = cli(root, 'find-missing-history', 'cash',
                    '--write-purchases', str(root / 'cash_draft.txt'))
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (root / 'cash_draft.txt').read_text()
            self.assertIn('QZD.US', text)
            self.assertNotIn('QZB.US', text)

    @rule("US-BASIS-08")
    def test_nothing_to_draft_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=_project_files(_parsed(
                _sell('QZN', '2025-05-02, 10:00:00', -5, 20, 0))))['usa']
            r = cli(root, 'find-missing-history', '--write-purchases')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('No purchase to draft', r.stdout)
            self.assertIn('not drafted: QZN.US', r.stdout)
            self.assertFalse((root / 'inputs' / 'margin' / DRAFT_NAME)
                             .exists())

    @rule("US-BASIS-08")
    def test_end_to_end_through_the_run(self):
        # IB's Closed Lots survive the run into the books; the draft is
        # not read; renamed unedited it stops the run; filled in, the
        # sales have their purchases and nothing is missing.
        from tax_rules.dual import settings_for
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'inputs' / 'margin').mkdir(parents=True)
            (root / 'taxjson.toml').write_text(
                settings_for('usa', year=2025)
                + '[accounts.margin]\ntype = "taxable"\n')
            (root / 'inputs' / 'margin' / 'ib.csv').write_text(
                HEAD + TRADES_H + MULTI_LOT + SINGLE)
            r = cli(root, 'run', '--no-input', timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
            def _rows():
                return json.loads((root / 'work' / 'margin_base.json')
                                  .read_text())['transactions']
            base = _rows()
            self.assertEqual(base[0]['broker_lots'],
                             '2023-01-10 60 1080.50;2023-06-15 40 1920.50')
            r = cli(root, 'find-missing-history', '--write-purchases')
            self.assertEqual(r.returncode, 0, r.stderr)
            draft = root / 'inputs' / 'margin' / DRAFT_NAME
            r = cli(root, 'run', '--no-input', timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_rows(), base)
            kept = root / 'inputs' / 'margin' / 'purchases.tt'
            draft.rename(kept)
            r = cli(root, 'run', '--no-input', timeout=600)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("date 'YYYY-MM-DD' is not a valid", r.stderr)
            kept.write_text(kept.read_text().replace(DATE_PLACEHOLDER,
                                                     '2022-05-02'))
            r = cli(root, 'run', '--no-input', timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = cli(root, 'find-missing-history')
            self.assertIn('No missing-cost-basis issues found', r.stdout)

    def test_flags_are_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=_project_files(
                _parsed(SINGLE)))['usa']
            r = cli(root, 'find-missing-history', '--write-purchases',
                    '--write-missing-history')
            self.assertEqual(r.returncode, 2)
            self.assertIn('one at a time', r.stderr)


if __name__ == '__main__':
    unittest.main()
