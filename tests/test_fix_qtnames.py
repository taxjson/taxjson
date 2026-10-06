"""Questrade internal symbol codes: name agreement (lib/symbol_codes,
tax-logic CA-ACB-CODES / US-BASIS-CODES).

Owner report (new-user run, Questrade website export after an IB ->
Questrade transfer): six codes stayed unresolved although the IB transfer
out of the same quantity and date existed, because the two brokers spell
the same security differently:

- abbreviations (RES / RESOURCES, MFG / MANUFACTURING, N V / NV,
  HLDG / HOLDING, REGISTRY / REG ...);
- broker boilerplate (REPSTG 5 COM ..., TRANSFER IN INTERACTIVE BROKER...,
  an IB domicile suffix /CAYMAN ISL);
- a share designator stated by ONE broker only (ORDINARY, SPONSORED ADR,
  CLASS A) — not a conflict for a transfer that already pairs by quantity
  and date;
- a description truncated by the export (a dividend row of a code named
  only by a cut-off description) — agrees with the same broker's full
  description elsewhere in the project, when unique.

The v0.20 guarantees stay: another share class or company is never joined
silently (both sides naming different classes / forms, NEW, one-word
names), name-only matching stays exact apart from truncation within the
same broker, and a ticker.map rule always wins.

Every fixture is SYNTHETIC: invented QZ* names and tickers that mirror the
real shapes, invented codes.
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages.questrade import (QuestradeBrokerage,
                                              _get_desc_key, scan_code_uses)
from tax_rules import rule
from test_fix_qtcodes import QH, q


def ok_listing(listing, cur):
    return QuestradeBrokerage().apply_currency_suffix(listing, cur) == listing


def use(code, desc, arrivals=(), cur='USD', cut=False):
    """A code as scan_code_uses builds it: the description KEY."""
    return SC.CodeUse(code=code, name=_get_desc_key(desc), currencies=[cur],
                      arrivals=list(arrivals), rows=1, name_cut=cut)


def entry(sym, name, acct='ibm', broker='ib', cut=False):
    return SC.NameEntry(sym, SC.name_tokens(name), name, acct, broker,
                        cut=cut)


def leg(sym, date, qty, name, cur='USD'):
    return SC.OutLeg('ibm', 'ib', sym, date, qty, cur,
                     [SC.name_tokens(name)])


def res(uses, outs=(), names=(), mapped=lambda c: False):
    return SC.resolve(uses, list(outs), list(names), listing_ok=ok_listing,
                      mapped=mapped)


# The six real-shaped cases: (code, Questrade description, IB leg symbol,
# IB instrument name).
SIX_PAIRED = [
    ('T000001', 'QZTAI SEMICONDUCTOR MFG CO LTD-SPONSORED ADR REPSTG 5 COM T',
     'QZTSM.US', 'QZTAI SEMICONDUCTOR-SP ADR'),
    ('E000002', 'QZEOG RES INC TRANSFER IN INTERACTIVE BROKER',
     'QZEOG.US', 'QZEOG RESOURCES INC'),
    ('N000003', 'QZNU HOLDINGS LTD CLASS A ORDINARY SHARES TRANSFER IN '
                'INTERACT', 'QZNU.US', 'QZNU HOLDINGS LTD/CAYMAN ISL-A'),
    ('A000004', 'QZASML HOLDING N V N Y REGISTRY SHS 2012 TRANSFER IN '
                'INTERACTI', 'QZASM.US', 'QZASML HOLDING NV-NY REG SHS'),
    ('S000005', 'QZSHARKNINJA INC ORDINARY SHARES TRANSFER IN INTERACTIVE '
                'BROKE', 'QZSN.US', 'QZSHARKNINJA INC'),
]


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestNormalisation(unittest.TestCase):
    def test_abbreviations_are_one_word_on_both_sides(self):
        t = SC.name_tokens
        for a, b in (('QZEOG RES INC', 'QZEOG RESOURCES INC'),
                     ('QZTAI MFG CO', 'QZTAI MANUFACTURING COMPANY'),
                     ('QZI INTL GRP', 'QZI INTERNATIONAL GROUP'),
                     ('QZH HLDGS', 'QZH HOLDINGS'),
                     ('QZH HLDG', 'QZH HOLDING'),
                     ('QZT TECH SYS CORP', 'QZT TECHNOLOGIES SYSTEMS '
                                           'CORPORATION'),
                     ('QZT TECHNOLOGY', 'QZT TECH'),
                     ('QZA HOLDING N V', 'QZA HOLDING NV'),
                     ('QZA N.V. N Y REGISTRY SHS', 'QZA NV-NY REG SHARES'),
                     ('QZJ & QZK INC', 'QZJ AND QZK INC')):
            with self.subTest(a=a):
                self.assertEqual(t(a), t(b))

    def test_boilerplate_is_cut(self):
        t = SC.name_tokens
        self.assertEqual(t('QZTAI SEMICONDUCTOR MFG CO LTD-SPONSORED ADR '
                           'REPSTG 5 COM T'),
                         ('QZTAI', 'SEMICONDUCTOR', 'MANUFACTURING', '~ADR'))
        self.assertEqual(t('QZEOG RES INC TRANSFER IN INTERACTIVE BROKER'),
                         t('QZEOG RESOURCES INC'))
        self.assertEqual(t('QZSN INC INTERACTIVE BROKERS LLC 146.16'),
                         ('QZSN',))
        # The IB domicile suffix (after '/', up to the class '-').
        self.assertEqual(t('QZNU HOLDINGS LTD/CAYMAN ISL-A'),
                         ('QZNU', 'HOLDINGS', '~A'))
        # ... but a designator after '/' is kept, and A/S is a form.
        self.assertEqual(t('QZCO CORP/NEW'), ('QZCO', '~NEW'))
        self.assertEqual(t('QZNOVO NORDISK A/S-SPONS ADR'),
                         ('QZNOVO', 'NORDISK', '~ADR'))
        # A name with TRANSFER inside it keeps it.
        self.assertEqual(t('QZZ TRANSFER PARTNERS LP'),
                         ('QZZ', 'PARTNERS'))

    def test_the_table_is_language_not_security_data(self):
        # Every entry maps a generic word to a generic word: no ticker,
        # no issuer name.
        for k, v in SC.ABBREVIATIONS.items():
            self.assertNotIn('QZ', k + v)
            self.assertTrue(k.isalpha() and v.isalpha(), (k, v))


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestSixRealShapes(unittest.TestCase):
    def test_each_pairs_with_the_ib_transfer_out(self):
        for code, desc, sym, ib_name in SIX_PAIRED:
            with self.subTest(code=code):
                r = res([use(code, desc, [('2026-09-06', 12.0)])],
                        [leg(sym, '2026-09-02', 12, ib_name)])
                self.assertEqual(r['unresolved'], {}, r['unresolved'])
                got = r['resolved'][code]
                self.assertEqual((got['symbol'], got['how']),
                                 (sym, 'transfer'))

    def test_one_sided_designators_are_said_in_the_evidence(self):
        r = res([use('S000005', SIX_PAIRED[4][1], [('2026-09-06', 12.0)])],
                [leg('QZSN.US', '2026-09-02', 12, 'QZSHARKNINJA INC')])
        self.assertIn('ORDINARY named on one side only',
                      r['resolved']['S000005']['evidence'])
        # The note still parses with the longer evidence.
        line = SC.codes_note(r['resolved'])
        self.assertEqual(SC.parse_codes_note(line)['S000005'][0], 'QZSN.US')

    def test_class_letter_on_one_side_only(self):
        r = res([use('N000003', 'QZNU HOLDINGS LTD CLASS A ORDINARY SHARES',
                     [('2026-09-06', 12.0)])],
                [leg('QZNU.US', '2026-09-02', 12, 'QZNU HOLDINGS LTD')])
        self.assertEqual(r['resolved']['N000003']['symbol'], 'QZNU.US')

    def test_truncated_dividend_name_matches_the_same_brokers_full_name(self):
        # A dividend-only code whose description the export cut mid-word;
        # the full description is on another account's Questrade rows.
        full = ('QZSEL SECTOR SPDR TRUST STATE STREET HEALTH CARE SELECT '
                'SECTOR SPDR ETF')
        r = res([use('S000006', 'QZSEL SECTOR SPDR TRUST STATE STREET '
                                'HEALTH CARE SELECT SEC')],
                names=[entry('QZXLV.US', full, acct='tfsa',
                             broker='questrade')])
        got = r['resolved']['S000006']
        self.assertEqual((got['symbol'], got['how']), ('QZXLV.US', 'name'))
        self.assertIn('cut off', got['evidence'])

    def test_truncation_at_a_word_boundary_needs_the_cut_flag(self):
        full = ('QZSEL SECTOR SPDR TRUST STATE STREET HEALTH CARE SELECT '
                'SECTOR SPDR ETF')
        short = 'QZSEL SECTOR SPDR TRUST STATE STREET HEALTH CARE SELECT'
        names = [entry('QZXLV.US', full, acct='tfsa', broker='questrade')]
        self.assertEqual(res([use('S000006', short)],
                             names=names)['resolved'], {})
        r = res([use('S000006', short, cut=True)], names=names)
        self.assertEqual(r['resolved']['S000006']['symbol'], 'QZXLV.US')


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestStillRefused(unittest.TestCase):
    PAIRING_REFUSED = [
        ('QZGOOG INC CL A', 'QZGOOG INC-CL C'),
        ('QZBRK HATHAWAY INC CL A', 'QZBRK HATHAWAY INC-CL B'),
        ('QZTEL CORP SUBORDINATE VOTING SHARES',
         'QZTEL CORP MULTIPLE VOTING SHARES'),
        ('QZTEL CORP CL B SUB VTG', 'QZTEL CORP CL A VOTING'),
        ('QZX PLC SPONSORED ADR', 'QZX PLC ORDINARY SHARES'),
        ('QZX PLC ORD SHS', 'QZX PLC-SP ADR'),
        ('QZX PLC UNSPONSORED ADR', 'QZX PLC SPONSORED ADR'),
        ('NEW QZY INC', 'QZY CORP'),
        ('QZY CORP/NEW', 'QZY CORP'),
        ('QZM PFD SER A', 'QZM CORP'),
        ('QZM UNITS', 'QZM CORP'),
        ('QZTEL CORP SUB VTG', 'QZTEL CORP'),
        ('BANK', 'BANK OF QZLAND'),
        ('QZLAND CORP', 'QZLAND GOLD CORP'),
        ('QZA NETWORKS', 'QZB NETWORKS'),
    ]

    def test_names_agree_refuses_in_pairing(self):
        for a, b in self.PAIRING_REFUSED:
            for x, y in ((a, b), (b, a)):
                with self.subTest(a=x, b=y):
                    ok, why = SC.names_agree(x, y, mode="pairing")
                    self.assertFalse(ok)
                    self.assertTrue(why)

    def test_a_refused_leg_never_resolves_and_names_the_global_line(self):
        for a, b in self.PAIRING_REFUSED:
            with self.subTest(code=a):
                r = res([use('X000012', a, [('2026-09-06', 5.0)])],
                        [leg('QZZ.US', '2026-09-02', 5, b)])
                self.assertEqual(r['resolved'], {})
                self.assertIn('GLOBAL X000012.US QZZ.US',
                              r['unresolved']['X000012']['detail'])

    def test_a_class_less_code_with_two_classes_in_the_window(self):
        r = res([use('X000012', 'QZGOOG INC', [('2026-09-06', 15.0)])],
                [leg('QZGA.US', '2026-09-02', 15, 'QZGOOG INC-CL A'),
                 leg('QZGC.US', '2026-09-02', 15, 'QZGOOG INC-CL C')])
        self.assertEqual(r['resolved'], {})
        self.assertEqual(r['unresolved']['X000012']['candidates'],
                         ['QZGA.US', 'QZGC.US'])

    def test_name_only_keeps_designators_exact(self):
        for a, b in (('QZSHARKNINJA INC ORDINARY SHARES', 'QZSHARKNINJA INC'),
                     ('QZNU HOLDINGS LTD CL A', 'QZNU HOLDINGS LTD'),
                     ('QZTAI SEMICONDUCTOR SPONSORED ADR',
                      'QZTAI SEMICONDUCTOR')):
            with self.subTest(a=a):
                self.assertFalse(SC.names_agree(a, b, mode="name_only")[0])
                r = res([use('X000012', a)], names=[entry('QZZ.US', b)])
                self.assertEqual(r['resolved'], {})
                self.assertIn('GLOBAL X000012.US QZZ.US',
                              r['unresolved']['X000012']['detail'])

    def test_truncation_guards(self):
        full = ('QZSEL SECTOR SPDR TRUST STATE STREET HEALTH CARE SELECT '
                'SECTOR SPDR ETF')
        cut = 'QZSEL SECTOR SPDR TRUST STATE STREET HEALTH CARE SELECT SEC'
        qt = dict(acct='tfsa', broker='questrade')
        # Another broker's name: never by truncation.
        self.assertEqual(res([use('X000012', cut)],
                             names=[entry('QZXLV.US', full)])['resolved'],
                         {})
        self.assertFalse(SC.names_agree(cut, full, mode="name_only")[0])
        self.assertTrue(SC.names_agree(cut, full, mode="name_only",
                                       same_broker=True)[0])
        # Two listings it is a prefix of: never guessed.
        r = res([use('X000012', cut)],
                names=[entry('QZXLV.US', full, **qt),
                       entry('QZXLR.US', full.replace('ETF', 'FUND'), **qt)])
        self.assertEqual(r['resolved'], {})
        self.assertEqual(r['unresolved']['X000012']['reason'], 'ambiguous')
        # A share designator in the part cut off: never.
        r = res([use('X000012', 'QZCO GLOBAL HOLDINGS INTERNATIONAL CL')],
                names=[entry('QZCB.US', 'QZCO GLOBAL HOLDINGS '
                             'INTERNATIONAL CL B', **qt)])
        self.assertEqual(r['resolved'], {})
        # Fewer than three strong words in common.
        r = res([use('X000012', 'QZCO GOLD MIN')],
                names=[entry('QZCM.US', 'QZCO GOLD MINING ROYALTY', **qt)])
        self.assertEqual(r['resolved'], {})
        # A complete shorter name is another security (the hedged fund),
        # not a cut one.
        r = res([use('X000012', 'QZSHARES CORE QZP 500 INDEX ETF')],
                names=[entry('QZXSP.US', 'QZSHARES CORE QZP 500 INDEX ETF '
                             'CAD HEDGED', **qt)])
        self.assertEqual(r['resolved'], {})

    def test_ticker_map_still_wins(self):
        code, desc, sym, ib_name = SIX_PAIRED[1]
        r = res([use(code, desc, [('2026-09-06', 12.0)])],
                [leg(sym, '2026-09-02', 12, ib_name)],
                mapped=lambda c: c == code)
        self.assertEqual(r['resolved'], {})
        self.assertEqual(r['mapped'][code]['how'], 'ticker.map')


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestNamesAgreeApi(unittest.TestCase):
    def test_reason_strings(self):
        ok, why = SC.names_agree('QZEOG RES INC', 'QZEOG RESOURCES INC')
        self.assertEqual((ok, why), (True, 'same company and share'))
        ok, why = SC.names_agree('QZSN INC ORDINARY SHARES', 'QZSN INC')
        self.assertTrue(ok)
        self.assertIn('ORDINARY named on one side only', why)
        ok, why = SC.names_agree('QZGOOG INC CL A', 'QZGOOG INC-CL C')
        self.assertFalse(ok)
        self.assertIn('class', why)
        ok, why = SC.names_agree('QZA NETWORKS', 'QZB NETWORKS')
        self.assertEqual((ok, why), (False, 'different companies'))
        with self.assertRaises(ValueError):
            SC.names_agree('A', 'B', mode='loose')


@rule("CA-ACB-CODES")
class TestScanFlagsACutDescription(unittest.TestCase):
    def test_a_full_width_name_is_flagged_cut(self):
        width = SC.QT_DESC_WIDTH
        name = ('QZSEL SECTOR SPDR TRUST STATE STREET HEALTH CARE SELECT '
                'SECTOR')[:width]
        self.assertEqual(len(name), width)
        div = q('2026-10-15', 'DIV', 'S000006', name, '0', net='5.00',
                act='Dividends', cur='USD')
        div2 = q('2026-10-15', 'DIV', 'S000007', 'QZB BANK CORP CASH DIV ON '
                 '50 SHS REC 10/01/26 PAY 10/15/26', '0', net='5.00',
                 act='Dividends', cur='USD')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'q.csv'
            p.write_text(QH + div + div2)
            uses = {u.code: u for u in scan_code_uses([p])}
        self.assertTrue(uses['S000006'].name_cut)
        self.assertFalse(uses['S000007'].name_cut)


if __name__ == "__main__":
    unittest.main()


class TestRowWordingIsNotTheName(unittest.TestCase):
    """A Questrade dividend / substitute-payment row describes the event
    after the name ("<NAME> CASH DIV ON 49 SHS REC ..."): the wording is
    cut before the names are compared, so the dividend's code matches the
    same security's plain name elsewhere at the broker."""

    NAME = "QZSECTOR TRUST STATE QZSTREET HEALTH CARE QZSECTOR ETF"

    def test_cash_dividend_and_substitute_payment_rows(self):
        from taxjson.lib import symbol_codes as S
        for row in (self.NAME + " CASH DIV ON 49 SHS REC 09/21/26 PAY 09/23/26",
                    self.NAME + " SUBST PAY ON 41 SHS REC 09/21/26 PAY 09/23/26"
                    " IN LIEU OF DIVIDEND"):
            ok, why = S.names_agree(row, self.NAME, "name_only",
                                    same_broker=True)
            self.assertTrue(ok, why)

    def test_class_still_decides(self):
        from taxjson.lib import symbol_codes as S
        ok, _ = S.names_agree("QZALPHA INC CLASS A CASH DIV ON 5 SHS",
                              "QZALPHA INC CLASS C", "name_only",
                              same_broker=True)
        self.assertFalse(ok)
