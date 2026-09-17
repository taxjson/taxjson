"""Tests for the IB year-end dividend-accrual diagnostic.

IB accrues a declared dividend ('Po' code in Change in Dividend Accruals)
and reverses it ('Re') once the cash posts into the Dividends section.
A statement downloaded before the cash is booked shows the accrual but
not the posted dividend — which once surprised a user whose 2025 report
was missing ~$634 of dividends that only appeared in a later statement.

The extractor does NOT treat accruals as income (they're estimates, often
in the account base currency rather than the dividend's native currency,
and would double-count once the real Dividends row lands). Instead it
emits a stderr warning naming accruals that are payable within the
statement period but not yet booked, so the user re-downloads before
filing.
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib.brokerages.ib_extractor import IbBrokerage


_HEADER = '''\
Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
'''

_ACCRUAL_HEADER = (
    'Change in Dividend Accruals,Header,Asset Category,Currency,Account,'
    'Symbol,Date,Ex Date,Pay Date,Quantity,Tax,Fee,Gross Rate,'
    'Gross Amount,Net Amount,Code\n'
)
_DIV_HEADER = 'Dividends,Header,Currency,Account,Date,Description,Amount\n'

# An accrual posted (Po) with pay date inside the statement period and
# never reversed — the surprising case the diagnostic exists for.
_OPEN_ACCRUAL = _HEADER + _ACCRUAL_HEADER + (
    'Change in Dividend Accruals,Data,Stocks,CAD,U111,TOU,2025-12-12,'
    '2025-12-15,2025-12-31,700,0,0,0.5,350,350,Po\n'
)

# Po and matching Re — the dividend was booked; nets to zero.
_REVERSED_ACCRUAL = _HEADER + _ACCRUAL_HEADER + (
    'Change in Dividend Accruals,Data,Stocks,CAD,U111,TOU,2025-12-12,'
    '2025-12-15,2025-12-31,700,0,0,0.5,350,350,Po\n'
    'Change in Dividend Accruals,Data,Stocks,CAD,U111,TOU,2025-12-31,'
    '2025-12-15,2025-12-31,700,0,0,0.5,-350,-350,Re\n'
)

# Open accrual, but the posted Dividends row for the same security and
# pay date is present in the file — income is already captured.
_ACCRUAL_WITH_POSTED_DIVIDEND = _HEADER + _DIV_HEADER + (
    'Dividends,Data,CAD,U111,2025-12-31,'
    'TOU(CA89156V1067) Cash Dividend CAD 0.50 per Share (Ordinary Dividend),350\n'
) + _ACCRUAL_HEADER + (
    'Change in Dividend Accruals,Data,Stocks,CAD,U111,TOU,2025-12-12,'
    '2025-12-15,2025-12-31,700,0,0,0.5,350,350,Po\n'
)

# Open accrual whose pay date is AFTER the statement period — a normal
# pending dividend (January income), not a missed one.
_FUTURE_DATED_ACCRUAL = _HEADER + _ACCRUAL_HEADER + (
    'Change in Dividend Accruals,Data,Stocks,CAD,U111,ARX,2025-12-20,'
    '2025-12-31,2026-01-15,1000,0,0,0.63,630,630,Po\n'
)


def _run(content):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    buf = io.StringIO()
    try:
        with patch.object(sys, 'stderr', buf):
            txs = IbBrokerage().parse_file(Path(f.name))
        return txs, buf.getvalue()
    finally:
        os.remove(f.name)


class TestDividendAccrualDiagnostic(unittest.TestCase):
    def test_open_accrual_warns(self):
        txs, stderr = _run(_OPEN_ACCRUAL)
        self.assertIn('accrued but not yet booked', stderr)
        self.assertIn('TOU', stderr)
        self.assertIn('2025-12-31', stderr)
        # Accruals are never emitted as transactions.
        self.assertEqual(txs, [])

    def test_reversed_accrual_no_warning(self):
        _, stderr = _run(_REVERSED_ACCRUAL)
        self.assertNotIn('accrued but not yet booked', stderr)

    def test_posted_dividend_suppresses_warning(self):
        txs, stderr = _run(_ACCRUAL_WITH_POSTED_DIVIDEND)
        self.assertNotIn('accrued but not yet booked', stderr)
        # The posted dividend itself is still parsed as income.
        divs = [t for t in txs if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(divs), 1)

    def test_future_dated_accrual_no_warning(self):
        _, stderr = _run(_FUTURE_DATED_ACCRUAL)
        # Pay date 2026-01-15 is past the 2025 statement period — normal
        # pending accrual, not a missed dividend.
        self.assertNotIn('accrued but not yet booked', stderr)


# A dividend split between an actual Cash Dividend and a Payment-in-Lieu
# (shares lent out). The PIL row carries no per-share rate in its own
# description, but the accrual for the same (symbol, pay date) does.
# NOTE: the real STRC rows this was modeled on carry a "(Return of
# Capital)" marker — those now reclassify to ADJUST (ACB reduction) and
# are pinned in test_roc.py. This fixture drops the marker to keep
# testing the PIL rate-backfill machinery on income-classified rows.
_PIL_CASE = _HEADER + _ACCRUAL_HEADER + (
    'Change in Dividend Accruals,Data,Stocks,USD,U111,STRC,2026-06-12,'
    '2026-06-15,2026-06-30,1500,0,0,0.958333,1437.5,1437.5,Po\n'
) + _DIV_HEADER + (
    'Dividends,Data,USD,U111,2026-06-30,STRC(US5949728530) Cash Dividend '
    'USD 0.958333 per Share,886.46\n'
    'Dividends,Data,USD,U111,2026-06-30,STRC(US5949728530) Payment in Lieu '
    'of Dividend,551.04\n'
)

_PIL_NO_ACCRUAL = _HEADER + _DIV_HEADER + (
    'Dividends,Data,USD,U111,2026-06-30,ZZZ(US0000000000) Payment in Lieu '
    'of Dividend,100.00\n'
)


class TestPaymentInLieuReconcile(unittest.TestCase):
    def test_pil_qty_price_backfilled_from_accrual_rate(self):
        txs, _ = _run(_PIL_CASE)
        pil = [t for t in txs if t['action'] == 'DIVIDEND_IN_LIEU'
               and t['symbol'] == 'STRC.US']
        self.assertEqual(len(pil), 1)
        self.assertAlmostEqual(pil[0]['price'], 0.958333, places=6)
        # Snapped to the true share count (551.04 is 575 x 0.958333
        # within cent rounding) — was pinned to the raw 574.9985.
        self.assertEqual(pil[0]['quantity'], 575.0)
        self.assertAlmostEqual(pil[0]['net_amount'], 551.04, places=2)  # income unchanged
        # Regular cash dividend still parsed from its own description, and the
        # two share counts sum to the accrual's 1500 shares.
        div = [t for t in txs if t['action'] == 'DIVIDEND'
               and t['symbol'] == 'STRC.US'][0]
        self.assertAlmostEqual(div['quantity'] + pil[0]['quantity'], 1500.0, places=2)

    def test_pil_dotted_ticker_backfill(self):
        # A dotted ticker (preferred share "FTN PR A" → symbol FTN.PR.A.TO):
        # the accrual is keyed on "FTN.PR.A" and the emitted symbol strips only
        # the exchange ext, so they must still match for the back-fill.
        # Real IB format uses a dotted ticker (FTN.PR.A), CA ISIN → .TO ext.
        content = _HEADER + _ACCRUAL_HEADER + (
            'Change in Dividend Accruals,Data,Stocks,CAD,U111,FTN.PR.A,'
            '2025-10-01,2025-10-05,2025-10-10,1000,0,0,0.06,60,60,Po\n'
        ) + _DIV_HEADER + (
            'Dividends,Data,CAD,U111,2025-10-10,FTN.PR.A(CA3175041084) '
            'Payment in Lieu of Dividend (Ordinary Dividend),36.00\n'
        )
        txs, _ = _run(content)
        pil = [t for t in txs if t['action'] == 'DIVIDEND_IN_LIEU'
               and t['symbol'] == 'FTN.PR.A.TO']
        self.assertEqual(len(pil), 1)
        self.assertAlmostEqual(pil[0]['price'], 0.06, places=6)
        self.assertAlmostEqual(pil[0]['quantity'], 36.0 / 0.06, places=2)  # 600

    def test_pil_without_accrual_stays_zero(self):
        txs, _ = _run(_PIL_NO_ACCRUAL)
        pil = [t for t in txs if t['action'] == 'DIVIDEND_IN_LIEU'][0]
        self.assertEqual(pil['price'], 0.0)
        self.assertEqual(pil['quantity'], 0.0)
        self.assertAlmostEqual(pil['net_amount'], 100.0, places=2)


if __name__ == '__main__':
    unittest.main()
