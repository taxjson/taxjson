"""GitHub issue #4: a Questrade internal code of a spun-off WARRANT
resolved to the COMMON stock's ticker (lib/symbol_codes; tax-logic
CA-ACB-CODES / US-BASIS-CODES).

Questrade booked a warrant distribution under an internal code (three
DIS rows: +N, a -N reversal, +N re-book). One of the code's descriptions
carries the issuer's plain name with no warrant word, and that one name
matched the common stock another account (IB) holds; the corp-action
stage meanwhile booked the chain under the code and asked for a GLOBAL
line, and the position split (code +N at $0, the real warrant ticker
-N after its sale).

Fixed:
1. the designators (warrant / right / unit / preferred / class letter)
   are read over ALL of a code's descriptions: a code that states one
   anywhere never resolves to a listing whose name states none (and
   vice versa);
2. the account's own rows come first: a later trade under the real
   ticker described like one of the code's rows, designator included,
   resolves the code (how "account");
3. the corp-action stage books the chain under the symbol-code stage's
   resolution (one source of truth; no contradictory messages);
4. the chain nets under the resolved ticker and the sale closes it.

Every fixture is SYNTHETIC: invented tickers (QZ*), codes and names,
fake account ids (pii-ok: 55500001, U5550001).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from tax_rules import rule
from _style import CapturedWidth

# The assertions read captured console lines (nothing wrapped).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


REPO = Path(__file__).resolve().parent.parent
CODE = 'D0000001'

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def q(td, action, sym, desc, qty, net='0', act='Dividends', price='0',
      gross='0', comm='0', cur='USD'):
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},{sym},\"{desc}\","
            f"{qty},{price},{gross},{comm},{net},{cur},55500001,"  # pii-ok
            f"{act},Individual LIRA\n")


# The issue's wording: the plain-named row is the one that matched.
PLAIN = ('QZD DEVELOPMENT CORP SPINOFF ON 500 SHS FROM SEC# J0000001')
PENDING = ('WTS QZD DEV CORP WT EXP PENDING SPINOFF ON 500 SHS FROM SEC# '
           'J0000001')
REBOOK = ('WTS QZD DEVELOPMENT CORP WARRANT EXP JAN 2028 SPINOFF ON 500 '
          'SHS FROM SEC# J0000001')
WARRANT_NAME = 'WTS QZD DEVELOPMENT CORP WARRANT EXP JAN 2028'
SALE = q('2025-11-14', 'Sell', 'QZDW', f'{WARRANT_NAME} WE ACTED AS AGENT',
         '-50', net='120.05', act='Trades', price='2.5', gross='125',
         comm='-4.95')
# Rows in the export's order: the plain-named reversal first (the name
# the old scan read).
ISSUE_ROWS = (q('2025-10-27', 'DIS', CODE, PLAIN, '-50')
              + q('2025-10-27', 'DIS', CODE, PENDING, '50')
              + q('2025-10-27', 'DIS', CODE, REBOOK, '50'))
# Questrade's full wording (REC / PAY dates): the parser's description
# key of a spinoff leg is the PARENT's name ('... FROM SEC# J0000001
# QZD DEVELOPMENT CORP REC ...'), so every leg read as the common's.
_TAIL = 'FROM SEC# J0000001 QZD DEVELOPMENT CORP REC 10/23/25 PAY 10/27/25'
REC_ROWS = (q('2025-10-27', 'DIS', CODE,
              f'WTS QZD DEV CORP WT EXP PENDING SPINOFF ON 500 SHS {_TAIL}',
              '50')
            + q('2025-10-28', 'DIS', CODE,
                f'WTS QZD DEV CORP WT EXP PENDING SPINOFF ON 500 SHS {_TAIL}'
                f' RELEASING AS RIGHTS DIST', '-50')
            + q('2025-10-28', 'DIS', CODE,
                f'{WARRANT_NAME} SPINOFF ON 500 SHS {_TAIL}', '50'))

IB_STATEMENT = (
    'Statement,Header,Field Name,Field Value\n'
    'Statement,Data,BrokerName,Interactive Brokers\n'
    'Statement,Data,Title,Activity Statement\n'
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    'Financial Instrument Information,Header,Asset Category,Symbol,'
    'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
    'Expiry,Delivery Month,Type,Strike,Code\n'
    'Financial Instrument Information,Data,Stocks,QZD,'
    '"QZD DEVELOPMENT CORP",999000101,,,NASDAQ,1,,,COMMON,,\n'
    'Trades,Header,DataDiscriminator,Asset Category,Currency,Account,'
    'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
    'Realized P/L,MTM P/L,Code\n'
    'Trades,Data,Order,Stocks,USD,U5550001,QZD,'  # pii-ok
    '"2025-03-03, 10:00:00",200,10,0,-2000,-1,2001,0,0,O\n')


def ok_listing(listing, cur):
    return QuestradeBrokerage().apply_currency_suffix(listing, cur) == listing


def ib_name(sym, name, acct='margin'):
    return SC.NameEntry(sym, SC.name_tokens(name), name, acct, 'ib')


# ------------------------------------------------------------ unit level

@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestDesignatorsOverAllDescriptions(unittest.TestCase):
    """The issue's function-level reproduction, and the rule."""

    def test_the_plain_row_alone_still_equals_the_common(self):
        # name_tokens is unchanged: in isolation the plain row IS the
        # common's name — which is why one row must never decide.
        self.assertEqual(SC.name_tokens(SC.questrade_name(PLAIN)),
                         SC.name_tokens('QZD DEVELOPMENT CORP'))

    def test_a_code_states_every_designator_of_its_descriptions(self):
        self.assertEqual(SC.code_designators([PLAIN, PENDING, REBOOK]),
                         frozenset({'~WARRANT'}))
        self.assertEqual(SC.code_designators([PLAIN]), frozenset())
        # A spinoff leg's own name (before SPINOFF) counts, not only
        # the parent's name the parser keys it by.
        leg = REC_ROWS.split('\n')[0].split('"')[1]
        self.assertEqual(SC.name_tokens(SC.questrade_name(leg)),
                         ('QZD', 'DEVELOPMENT'))
        self.assertEqual(SC.code_designators([leg]),
                         frozenset({'~WARRANT'}))
        self.assertEqual(SC.code_designators(['QZX INC CL B CASH DIV ON 5 '
                                              'SHS REC 01/02/25 PAY '
                                              '01/09/25']),
                         frozenset({'~B'}))
        self.assertEqual(SC.code_designators(['QZX CORP PFD SER 2 CASH DIV '
                                              'ON 5 SHS']),
                         frozenset({'~PREFERRED'}))

    def test_designators_agree(self):
        agree = SC.designators_agree
        self.assertFalse(agree({'~WARRANT'}, set())[0])
        self.assertFalse(agree(set(), {'~WARRANT'})[0])      # vice versa
        self.assertFalse(agree({'~RIGHT'}, {'~WARRANT'})[0])
        self.assertFalse(agree({'~UNIT'}, set())[0])
        self.assertFalse(agree({'~PREFERRED'}, set())[0])
        self.assertFalse(agree({'~B'}, set())[0])
        self.assertFalse(agree({'~B'}, {'~A'})[0])
        self.assertTrue(agree({'~WARRANT'}, {'~WARRANT'})[0])
        self.assertTrue(agree(set(), set())[0])
        # Transfer pairing keeps its one-sided class-letter tolerance
        # (IB may leave the letter out); a kind of security never.
        self.assertTrue(agree({'~B'}, set(), mode='pairing')[0])
        self.assertFalse(agree({'~B'}, {'~A'}, mode='pairing')[0])
        self.assertFalse(agree({'~WARRANT'}, set(), mode='pairing')[0])
        ok, why = agree({'~WARRANT'}, set())
        self.assertIn('WARRANT', why)

    def res(self, uses, outs=(), names=()):
        return SC.resolve(uses, list(outs), list(names),
                          listing_ok=ok_listing)

    def test_name_match_refuses_a_listing_without_the_designator(self):
        u = SC.CodeUse(code=CODE, name=SC.questrade_name(PLAIN),
                       currencies=['USD'], rows=3,
                       descriptions=[PLAIN, PENDING, REBOOK])
        r = self.res([u], names=[ib_name('QZD.US', 'QZD DEVELOPMENT CORP')])
        self.assertEqual(r['resolved'], {})
        why = r['unresolved'][CODE]['detail']
        self.assertIn('WARRANT', why)
        self.assertIn('QZD.US', why)
        # ... and never suggests the common's GLOBAL line.
        self.assertNotIn(f'GLOBAL {CODE}.US QZD.US', why)

    def test_vice_versa_a_plain_code_never_takes_a_warrant_listing(self):
        u = SC.CodeUse(code=CODE, name='QZD DEVELOPMENT CORP',
                       currencies=['USD'], rows=1,
                       descriptions=['QZD DEVELOPMENT CORP CASH DIV ON 5 '
                                     'SHS'])
        names = [ib_name('QZDW.US', 'QZD DEVELOPMENT CORP'),
                 ib_name('QZDW.US', 'WTS QZD DEVELOPMENT CORP')]
        self.assertEqual(self.res([u], names=names)['resolved'], {})

    def test_the_designator_rule_also_holds_for_transfer_pairing(self):
        u = SC.CodeUse(code=CODE, name='QZD DEVELOPMENT CORP',
                       currencies=['USD'], rows=2,
                       arrivals=[('2025-10-27', 50.0)],
                       descriptions=['QZD DEVELOPMENT CORP TRANSFER IN',
                                     'WTS QZD DEVELOPMENT CORP CASH DIV'])
        leg = SC.OutLeg('margin', 'ib', 'QZD.US', '2025-10-24', 50.0, 'USD',
                        [SC.name_tokens('QZD DEVELOPMENT CORP')])
        r = self.res([u], outs=[leg])
        self.assertEqual(r['resolved'], {})
        self.assertIn('WARRANT', r['unresolved'][CODE]['detail'])

    def test_an_exact_in_account_match_wins(self):
        u = SC.CodeUse(code=CODE, name=SC.questrade_name(PLAIN),
                       currencies=['USD'], rows=3,
                       descriptions=[PLAIN, PENDING, REBOOK],
                       own={'QZDW.US': [WARRANT_NAME]})
        r = self.res([u], names=[ib_name('QZD.US', 'QZD DEVELOPMENT CORP')])
        got = r['resolved'][CODE]
        self.assertEqual((got['symbol'], got['how'], got['currency']),
                         ('QZDW.US', 'account', 'USD'))
        self.assertIn(WARRANT_NAME, got['evidence'])

    def test_an_in_account_listing_without_the_designator_is_refused(self):
        # The account also trades the common (described with the plain
        # name the reversal row carries): only the warrant listing is
        # the code's.
        u = SC.CodeUse(code=CODE, name=SC.questrade_name(PLAIN),
                       currencies=['USD'], rows=3,
                       descriptions=[PLAIN, PENDING, REBOOK],
                       own={'QZDW.US': [WARRANT_NAME],
                            'QZD.US': ['QZD DEVELOPMENT CORP']})
        got = self.res([u])['resolved'][CODE]
        self.assertEqual(got['symbol'], 'QZDW.US')
        # Two warrant listings in the account: not guessed.
        u.own['QZDX.US'] = ['WTS QZD DEVELOPMENT CORP WARRANT EXP 2029']
        r = self.res([u])
        self.assertEqual(r['resolved'], {})
        self.assertEqual(r['unresolved'][CODE]['candidates'],
                         ['QZDW.US', 'QZDX.US'])


def scan(*bodies):
    from taxjson.lib.brokerages.questrade import scan_code_uses
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f'q{i}.csv'
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        return {u.code: u for u in scan_code_uses(paths)}


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestScan(unittest.TestCase):
    def test_the_scan_reads_every_description_and_the_own_listing(self):
        for rows in (ISSUE_ROWS, REC_ROWS):
            with self.subTest(rec='REC' in rows):
                u = scan(rows, SALE)[CODE]
                self.assertEqual(SC.code_designators(u.descriptions),
                                 frozenset({'~WARRANT'}))
                self.assertEqual(sorted(u.own), ['QZDW.US'])

    def test_a_spinoff_legs_parent_name_is_not_the_codes_name(self):
        u = scan(REC_ROWS)[CODE]
        self.assertNotEqual(SC.name_tokens(u.name), ('QZD', 'DEVELOPMENT'))
        self.assertEqual(u.own, {})

    def test_worse_case_trades_under_the_code_never_join_the_common(self):
        # The warrants TRADED while Questrade still used the code, and
        # nothing in the account names the real ticker: unresolved
        # (ATTENTION), never the common's pool.
        buy = q('2025-10-29', 'Buy', CODE, f'{PENDING.split(" SPINOFF")[0]}'
                f' WE ACTED AS AGENT', '10', net='-25.00', act='Trades',
                price='2.5', gross='-25')
        uses = scan(ISSUE_ROWS, buy)
        r = SC.resolve(list(uses.values()), [],
                       [ib_name('QZD.US', 'QZD DEVELOPMENT CORP')],
                       listing_ok=ok_listing)
        self.assertEqual(r['resolved'], {})
        self.assertIn(CODE, r['unresolved'])


# ------------------------------------------------------------ the run

def _run(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


def _config(country):
    base, other = (('CAD', 'USD') if country == 'canada'
                   else ('USD', 'CAD'))
    return (f'[settings]\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{other}"]\n'
            + ('option_grant_timing_since = 2025\n' if country == 'canada'
               else '') + '\n'
            f'[accounts.margin]\ntype = "taxable"\n\n'
            f'[accounts.lira]\ntype = "sheltered"\n')


class _RunBase:
    """The two-account synthetic project of the issue, run end to end
    under one country: margin (an IB statement naming 'QZD DEVELOPMENT
    CORP'), lira (a Questrade export with the code's three DIS rows and
    the later QZDW sale)."""
    COUNTRY = 'canada'
    ROWS = ISSUE_ROWS

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._td.name)
        (root / 'taxjson.toml').write_text(_config(cls.COUNTRY))
        (root / 'inputs' / 'margin').mkdir(parents=True)
        (root / 'inputs' / 'lira').mkdir(parents=True)
        (root / 'inputs' / 'margin' / 'U5550001_2025.csv').write_text(  # pii-ok
            IB_STATEMENT)
        (root / 'inputs' / 'lira' / '55500001_2025.csv').write_text(  # pii-ok
            QH + cls.ROWS + SALE)
        first = _run(root, 'run', '--no-input')
        # The spin-off waits for its election (the lira's books do not
        # depend on it): elected, then run again.
        pending = root / 'work' / 'pending_elections.json'
        assert pending.exists(), first.stdout + first.stderr
        cls.pending = json.loads(pending.read_text())[
            'accounts']['lira']['pending']
        eid = cls.pending[0]['event_id']
        how = ('taxable_deemed_dividend' if cls.COUNTRY == 'canada'
               else 'taxable_distribution_301')
        el = _run(root, 'elect', 'lira', '--set', f'{eid}={how}', '--hint',
                  'fmv_per_share=2')
        assert el.returncode == 0, el.stdout + el.stderr
        cls.r = _run(root, 'run', '--no-input')
        cls.out = first.stdout + first.stderr + cls.r.stdout + cls.r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_run_succeeds(self):
        self.assertEqual(self.r.returncode, 0, self.out[-4000:])

    def test_the_code_resolves_to_the_warrant_not_the_common(self):
        st = json.loads((self.root / 'work' / 'lira_symbol_codes.state')
                        .read_text())
        got = st['resolved'][CODE]
        self.assertEqual((got['symbol'], got['how']), ('QZDW.US', 'account'))
        self.assertNotIn(f'{CODE} → QZD.US', self.out)

    def test_no_contradictory_messages(self):
        self.assertNotIn('INTERNAL code', self.out)
        self.assertNotIn(f'keeps internal symbol code {CODE!r}', self.out)
        self.assertNotIn(f'GLOBAL {CODE}', self.out)

    def test_the_chain_nets_under_the_warrant_and_the_sale_closes_it(self):
        corp = json.loads((self.root / 'work' / 'lira_questrade_corp.json')
                          .read_text())
        rows = corp.get('transactions') or corp.get('rows') or []
        syms = {t['symbol'] for t in rows}
        self.assertIn('QZDW.US', syms)
        self.assertFalse([s for s in syms if s.startswith(CODE)], syms)
        got = sum(float(t['quantity']) for t in rows
                  if t['symbol'] == 'QZDW.US')
        self.assertAlmostEqual(got, 50.0)
        r = _run(self.root, 'shares', '--json')
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        text = r.stdout
        self.assertNotIn(CODE, text)
        doc = json.loads(text)
        held = {(p.get('account'), p.get('symbol')): p.get('shares',
                                                           p.get('quantity'))
                for p in (doc.get('positions') or doc.get('rows') or [])}
        self.assertFalse([k for k, v in held.items()
                          if k[1] and k[1].startswith('QZDW')
                          and abs(float(v or 0)) > 1e-9], held)


@rule("CA-ACB-CODES")
class TestRunCanada(_RunBase, unittest.TestCase):
    COUNTRY = 'canada'


@rule("CA-ACB-CODES")
class TestRunCanadaRecWording(_RunBase, unittest.TestCase):
    COUNTRY = 'canada'
    ROWS = REC_ROWS


@rule("US-BASIS-CODES")
class TestRunUsa(_RunBase, unittest.TestCase):
    COUNTRY = 'usa'


if __name__ == '__main__':
    unittest.main()
