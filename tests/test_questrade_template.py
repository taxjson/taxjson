"""Questrade on the parse template (step 5h (4)): what the cut fixed in
passing and what it kept. Synthetic data only."""
import unittest

from _support.parser_qt_rbc_rows import ACCT, QH, of, q, qt_parse


def _rei(day, n):
    return q(td=day, action='REI', sym='SYN', desc='SYN CORP REINV@C$10',
             qty=str(n), price='10', gross=str(-10 * n), comm='0',
             net=str(-10 * n), cur='CAD', act='Dividend reinvestment',
             atype='Cash')


def _brw(day, sym, qty, desc, cur='CAD'):
    return q(td=day, action='BRW', sym=sym, desc=desc, qty=qty, price='0',
             gross='0', comm='0', net='0', cur=cur, act='Other')


class TestQuestradeOnTheTemplate(unittest.TestCase):
    BUY = q(td='2025-01-06', action='Buy', sym='SYN', desc='SYN CORP',
            qty='10', price='10', gross='-100', comm='0', net='-100', cur='CAD')

    def test_a_repeated_header_is_no_account(self):
        # Two downloads pasted together: the second header's literal
        # cells are a non-event, never an account of the file.
        _txs, _err, parsers = qt_parse(self.BUY + QH + self.BUY.replace('2025-01-06', '2025-02-06'))
        self.assertEqual(parsers[0].statement_accounts(), {ACCT})

    def test_the_last_row_keeps_its_account_after_a_reversal_removal(self):
        # A reinvestment and its reversal (both dropped by the pairing)
        # before the last row: the last row's transaction still names
        # its account (the loop's slice index missed it).
        body = (self.BUY + _rei('2025-06-02', 2) + _rei('2025-06-03', -2)
                + self.BUY.replace('2025-01-06', '2025-07-07'))
        txs, _err, _ = qt_parse(body, taxable=True)
        self.assertEqual([t.get('broker_account') for t in txs], [ACCT, ACCT])

    def test_journal_costing_follows_the_marks(self):
        # Two lots of one security journaled to the USD line on one day,
        # with different book values: the two IN legs are byte-identical
        # as the loop made them, so the second is a split fill, and each
        # keeps its own cost (the marks saw the legs before the costing).
        day = '2025-03-03'
        body = (_brw(day, 'SAMPLF.TO', '-100', 'SAMPLE US DOLLAR FUND JOURNAL POSITION TO USD')
                + _brw(day, 'SAMPLF.U.TO', '100', 'SAMPLE US DOLLAR FUND JOURNAL POSITION FROM '
                       'CAD BOOK VALUE: $1300.00 CNV@ 1.3000', cur='USD')
                + _brw(day, 'SAMPLF.TO', '-100', 'SAMPLE US DOLLAR FUND JOURNAL POSITION TO USD')
                + _brw(day, 'SAMPLF.U.TO', '100', 'SAMPLE US DOLLAR FUND JOURNAL POSITION FROM '
                       'CAD BOOK VALUE: $1350.00 CNV@ 1.3500', cur='USD'))
        txs, _err, _ = qt_parse(body)
        ins = of(txs, symbol='SAMPLF.U.TO')
        self.assertEqual(len(ins), 2)
        self.assertEqual([t['description'].endswith('[fill #2]') for t in ins], [False, True])
        self.assertNotEqual(ins[0]['net_amount'], ins[1]['net_amount'])


if __name__ == "__main__":
    unittest.main()
