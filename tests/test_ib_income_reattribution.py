"""IB stamps DIVIDEND/withholding-TAX symbols from the security's ISIN country,
while trades take their suffix from the trade currency. Those disagree for a
dual-listed name held on a non-domicile exchange, orphaning the income onto a
phantom symbol. `_reattribute_income_to_holdings` binds each income row to the
listing actually held for its ticker in the same statement.

Cases pinned here (all observed in real IB data):
  * B2Gold on the TSX (BTO.TO, CAD lots) pays SOME dividends in USD — the
    suffix must stay .TO (currency alone would wrongly say .US).
  * B2Gold on the NYSE (BTG.US) has its dividend stamped BTG.TO by the ISIN
    ('CA...') — must be corrected to BTG.US.
  * Seagate (Irish domicile, ISIN 'IE...') held as STX.US has its dividend and
    withholding tax stamped .L — must be corrected to STX.US.
"""
import os
import tempfile
import unittest
from pathlib import Path


_TRADES_HDR = (
    'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
    'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
    'Realized P/L,MTM P/L,Code\n')
_DIV_HDR = 'Dividends,Header,Currency,Account,Date,Description,Amount\n'
_TAX_HDR = 'Withholding Tax,Header,Currency,Account,Date,Description,Amount\n'


def _trade(cur, sym):
    return (f'Trades,Data,Order,Stocks,{cur},{sym},"2026-01-05, 09:30:00",'
            f'100,10.00,0,1000,1,0,0,0,O\n')


def _div(cur, desc, amt):
    return f'Dividends,Data,{cur},U1,2026-06-30,{desc},{amt}\n'


def _tax(cur, desc, amt):
    return f'Withholding Tax,Data,{cur},U1,2026-06-30,{desc},{amt}\n'


def _parse(csv_text):
    from taxjson.lib.brokerages.ib_extractor import IbBrokerage
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write('Statement,Header,Field Name,Field Value\n'
                'Statement,Data,BrokerName,Interactive Brokers\n')
        f.write(csv_text)
        fname = f.name
    try:
        return IbBrokerage().parse_file(Path(fname))
    finally:
        os.remove(fname)


def _income_symbols(txs, root):
    return {(t['action'], t['symbol']) for t in txs
            if t['action'] in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX')
            and t['symbol'].split('.')[0] == root}


class TestIbIncomeReattribution(unittest.TestCase):
    def test_tsx_holding_usd_dividend_stays_TO(self):
        # BTO.TO held on the TSX; B2Gold pays this dividend in USD. The suffix
        # must follow the holding (.TO), NOT the payment currency (.US).
        csv = (_TRADES_HDR + _trade('CAD', 'BTO')
               + _DIV_HDR
               + _div('USD', 'BTO(CA11777Q2099) Cash Dividend USD 0.02 per Share'
                             ' (Ordinary Dividend)', '200'))
        txs = _parse(csv)
        self.assertEqual(_income_symbols(txs, 'BTO'), {('DIVIDEND', 'BTO.TO')})

    def test_nyse_holding_dividend_isin_TO_corrected_to_US(self):
        # BTG.US held on the NYSE; ISIN 'CA...' would stamp the dividend BTG.TO.
        csv = (_TRADES_HDR + _trade('USD', 'BTG')
               + _DIV_HDR
               + _div('USD', 'BTG(CA11777Q2099) Cash Dividend USD 0.02 per Share'
                             ' (Ordinary Dividend)', '80'))
        txs = _parse(csv)
        self.assertEqual(_income_symbols(txs, 'BTG'), {('DIVIDEND', 'BTG.US')})

    def test_irish_domicile_dividend_and_tax_L_corrected_to_US(self):
        # STX.US held; ISIN 'IE...' stamps both dividend and tax .L.
        csv = (_TRADES_HDR + _trade('USD', 'STX')
               + _DIV_HDR
               + _div('USD', 'STX(IE00BKVD2N49) Cash Dividend USD 0.72 per Share'
                             ' (Ordinary Dividend)', '72')
               + _TAX_HDR
               + _tax('USD', 'STX(IE00BKVD2N49) Cash Dividend USD 0.72 per Share'
                             ' - US Tax', '-10'))
        txs = _parse(csv)
        self.assertEqual(
            _income_symbols(txs, 'STX'),
            {('DIVIDEND', 'STX.US'), ('TAX', 'STX.US')})

    def test_no_position_in_file_keeps_isin_suffix(self):
        # A dividend whose position isn't in this statement falls back to the
        # ISIN-derived suffix (dividend↔tax stay paired) — no phantom guess.
        csv = (_DIV_HDR
               + _div('USD', 'BTG(CA11777Q2099) Cash Dividend USD 0.02 per Share'
                             ' (Ordinary Dividend)', '80'))
        txs = _parse(csv)
        self.assertEqual(_income_symbols(txs, 'BTG'), {('DIVIDEND', 'BTG.TO')})


if __name__ == '__main__':
    unittest.main()
