"""RBC Direct corporate-action extraction.

RBC fragments a merger into two $0-value 'Reorganization' rows (old shares
removed under a temp symbol + acquirer shares received). These tests pin that:
  - the extractor pairs them into one `merger` CorporateAction,
  - the brokerage parser no longer emits them as $0 trades (corp-actions owns
    them), while option expiry/assignment 'Reorganization' rows are untouched,
  - the event resolves through the election machinery (rollover -> SPLIT).
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.corp_actions import (
    parse_rbc_corporate_actions, is_rbc_merger_row, is_rbc_cil_row,
    resolve_event,
)

_HEADER = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
           '"Settlement Date","Account","Value","Currency","Description"\n')
_MERGER = (
    '"2025-07-21 00:00:00","Reorganization","H015283","HESS CORPORATION","-15",'
    '"","2025-07-21 00:00:00","123","0","USD","MGR - HESS CORPORATION MERGER '
    'TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"\n'
    '"2025-07-21 00:00:00","Reorganization","CVX","CHEVRON CORPORATION","15",'
    '"","2025-07-21 00:00:00","123","0","USD","MGR - CHEVRON CORPORATION SHRS '
    'RECEIVED THRU MERGER"\n'
)
_SALE = ('"2025-12-23 00:00:00","Sell","CVX","CHEVRON CORPORATION","-15",'
         '"150.36","2025-12-24 00:00:00","123","2245.45","USD","CHEVRON SALE"\n')
# A HESS dividend row — carries the *real* ticker (HES) under the same
# company name as the merger removal's temp code (H015283), so the
# extractor can resolve H015283 → HES.
_HES_DIV = ('"2025-06-30 00:00:00","Dividends","HES","HESS CORPORATION","",'
            '"","2025-06-30 00:00:00","123","6.38","USD","DIV - HESS '
            'CORPORATION CASH DIV ON 15 SHS"\n')
# Cash-in-lieu of the 0.375 fractional CVX share (15 * 1.025 = 15.375).
_CIL = ('"2025-07-24 00:00:00","Reorganization","CVX","CHEVRON CORPORATION",'
        '"","","2025-07-24 00:00:00","123","55.82","USD","CIL - CHEVRON '
        'CORPORATION CASH IN LIEU OF FRAC SHARES 166764100000"\n'
        '"2025-07-24 00:00:00","Reorganization","CVX","CHEVRON CORPORATION",'
        '"","","2025-07-25 00:00:00","123","0.54","USD","CIL - CHEVRON '
        'CORPORATION ADDITIONAL CIL PAYMENT 166764100000"\n')


def _write(content):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


class TestRbcExtractor(unittest.TestCase):
    def test_pairs_merger_into_one_event(self):
        p = _write(_HEADER + _MERGER + _SALE)
        try:
            events = parse_rbc_corporate_actions(p, account="margin")
        finally:
            os.remove(p)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action_type, "merger")
        self.assertEqual(ev.source_symbol, "H015283.US")
        self.assertEqual(ev.target_symbol, "CVX.US")
        self.assertAlmostEqual(ev.ratio, 1.025)
        self.assertEqual(ev.qty_disposed, 15.0)
        self.assertEqual(ev.qty_received, 15.0)
        self.assertEqual(ev.account, "margin")
        self.assertTrue(ev.event_id)               # stable id for the manifest

    def test_event_id_is_stable_across_runs(self):
        p = _write(_HEADER + _MERGER)
        try:
            a = parse_rbc_corporate_actions(p, "margin")[0]
            b = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        self.assertEqual(a.event_id, b.event_id)


class TestIsMergerRow(unittest.TestCase):
    def test_matches_merger_rows(self):
        self.assertTrue(is_rbc_merger_row(
            "Reorganization", "MGR - HESS CORPORATION MERGER TO CHEVRON 1.025 NEW = 1 OLD"))
        self.assertTrue(is_rbc_merger_row(
            "Reorganization", "MGR - CHEVRON CORPORATION SHRS RECEIVED THRU MERGER"))

    def test_ignores_option_reorg_rows(self):
        # Option expiry / assignment are also 'Reorganization' but not mergers.
        self.assertFalse(is_rbc_merger_row(
            "Reorganization", "EXP - CALL .BNS OPTION EXPIRATION - EXPIRED"))
        self.assertFalse(is_rbc_merger_row(
            "Other", "ASN - CALL COIN ASSIGNMENT OF OPTION"))


class TestParserSkipsMergerRows(unittest.TestCase):
    def test_merger_rows_dropped_sale_kept(self):
        p = _write(_HEADER + _MERGER + _SALE)
        try:
            with contextlib.redirect_stderr(io.StringIO()):   # mute skip note
                txs = RbcBrokerage().parse_file(p)
        finally:
            os.remove(p)
        self.assertEqual([t for t in txs if t['symbol'].startswith('H015283')], [])
        receipts = [t for t in txs if t['symbol'] == 'CVX.US'
                    and 'RECEIVED THRU MERGER' in (t.get('description') or '')]
        self.assertEqual(receipts, [])
        # The real December sale survives.
        sale = [t for t in txs if t['symbol'] == 'CVX.US' and t['quantity'] == -15.0]
        self.assertEqual(len(sale), 1)


class TestTempSymbolResolution(unittest.TestCase):
    def test_resolves_temp_code_to_real_ticker_via_company_name(self):
        # With a HES dividend row present, the merger removal booked under
        # the temp code H015283 resolves to the real ticker HES.US.
        p = _write(_HEADER + _HES_DIV + _MERGER)
        try:
            ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        self.assertEqual(ev.source_symbol, "HES.US")
        self.assertEqual(ev.target_symbol, "CVX.US")

    def test_falls_back_to_temp_code_when_unresolvable(self):
        # No HES row anywhere → nothing to resolve against; keep the temp
        # code (and warn) rather than guess.
        p = _write(_HEADER + _MERGER)
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        self.assertEqual(ev.source_symbol, "H015283.US")
        self.assertIn("temporary reorg symbol", err.getvalue())


class TestCashInLieu(unittest.TestCase):
    def test_cil_folded_into_event(self):
        p = _write(_HEADER + _HES_DIV + _MERGER + _CIL)
        try:
            ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        self.assertAlmostEqual(ev.cash_in_lieu, 56.36)   # 55.82 + 0.54
        self.assertEqual(ev.cash_in_lieu_currency, "USD")

    def test_is_cil_row_matches_and_excludes_merger(self):
        self.assertTrue(is_rbc_cil_row(
            "Reorganization", "CIL - CHEVRON CORPORATION CASH IN LIEU OF FRAC SHARES"))
        self.assertTrue(is_rbc_cil_row(
            "Reorganization", "CIL - CHEVRON CORPORATION ADDITIONAL CIL PAYMENT"))
        # Merger removal/receipt rows are NOT cash-in-lieu.
        self.assertFalse(is_rbc_cil_row(
            "Reorganization", "MGR - HESS CORPORATION MERGER TO CHEVRON 1.025 NEW = 1 OLD"))
        self.assertFalse(is_rbc_cil_row(
            "Reorganization", "MGR - CHEVRON CORPORATION SHRS RECEIVED THRU MERGER"))

    def test_parser_skips_cil_rows(self):
        # The CIL 'Reorganization' rows must not become 0-quantity trades.
        p = _write(_HEADER + _HES_DIV + _MERGER + _CIL + _SALE)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                txs = RbcBrokerage().parse_file(p)
        finally:
            os.remove(p)
        cil = [t for t in txs if 'CASH IN LIEU' in (t.get('description') or '').upper()
               or 'CIL' in (t.get('description') or '').upper()]
        self.assertEqual(cil, [])
        # No 0-quantity BUYSELL rows survive.
        zero_qty = [t for t in txs
                    if t.get('action') == 'BUYSELL' and abs(t.get('quantity') or 0) < 1e-9]
        self.assertEqual(zero_qty, [])


class TestResolveThroughElection(unittest.TestCase):
    def test_rollover_emits_split_renaming_to_target(self):
        # No cash-in-lieu row → the SPLIT scales by the empirical received/
        # disposed ratio (15/15 = 1.0), landing on the broker's whole-share
        # delivery rather than the nominal 1.025 (which would leave 0.375
        # phantom dust). Source stays H015283.US (nothing to resolve to).
        p = _write(_HEADER + _MERGER)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        rows = resolve_event(ev, "rollover_s_85_1_5", country="canada")
        splits = [r for r in rows if r['action'] == 'SPLIT']
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]['symbol'], "H015283.US")
        self.assertEqual(splits[0]['symbol_new'], "CVX.US")
        self.assertAlmostEqual(splits[0]['quantity'], 1.0)

    def test_rollover_with_cash_in_lieu_sells_fractional(self):
        # With cash-in-lieu present, the SPLIT keeps the nominal ratio
        # (15 → 15.375) and a SELL retires the 0.375 fractional at the
        # cash proceeds, netting to 15 whole shares.
        p = _write(_HEADER + _HES_DIV + _MERGER + _CIL)
        try:
            ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        rows = resolve_event(ev, "rollover_s_85_1_5", country="canada")
        splits = [r for r in rows if r['action'] == 'SPLIT']
        sells = [r for r in rows if r['action'] == 'BUYSELL']
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]['symbol'], "HES.US")
        self.assertEqual(splits[0]['symbol_new'], "CVX.US")
        self.assertAlmostEqual(splits[0]['quantity'], 1.025)
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]['symbol'], "CVX.US")
        self.assertAlmostEqual(sells[0]['quantity'], -0.375)
        self.assertAlmostEqual(sells[0]['net_amount'], 56.36)
        self.assertEqual(sells[0]['currency'], "USD")


if __name__ == "__main__":
    unittest.main()
