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
from taxjson.lib.brokerages.rbc_direct import classify_rbc_row, read_rbc_rows
from taxjson.lib.corp_actions import (
    parse_rbc_corporate_actions, resolve_event,
)

_HEADER = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
           '"Settlement Date","Account","Value","Currency","Description"\n')
_MERGER = (
    '"2025-03-17 00:00:00","Reorganization","A012345","ABC CORPORATION","-12",'
    '"","2025-03-17 00:00:00","123","0","USD","MGR - ABC CORPORATION MERGER '
    'TO ABDCO CORPORATION 1.05 NEW = 1 OLD"\n'
    '"2025-03-17 00:00:00","Reorganization","ABD","ABDCO CORPORATION","12",'
    '"","2025-03-17 00:00:00","123","0","USD","MGR - ABDCO CORPORATION SHRS '
    'RECEIVED THRU MERGER"\n'
)
_SALE = ('"2025-11-19 00:00:00","Sell","ABD","ABDCO CORPORATION","-12",'
         '"110.00","2025-11-20 00:00:00","123","1310.05","USD","ABDCO SALE"\n')
# An ABC dividend row — carries the *real* ticker (ABC) under the same
# company name as the merger removal's temp code (A012345), so the
# extractor can resolve A012345 → ABC.
_SRC_DIV = ('"2025-02-28 00:00:00","Dividends","ABC","ABC CORPORATION","",'
            '"","2025-02-28 00:00:00","123","4.20","USD","DIV - ABC '
            'CORPORATION CASH DIV ON 12 SHS"\n')
# Cash-in-lieu of the 0.6 fractional ABD share (12 * 1.05 = 12.6).
_CIL = ('"2025-03-20 00:00:00","Reorganization","ABD","ABDCO CORPORATION",'
        '"","","2025-03-20 00:00:00","123","30.00","USD","CIL - ABDCO '
        'CORPORATION CASH IN LIEU OF FRAC SHARES 000123400000"\n'
        '"2025-03-20 00:00:00","Reorganization","ABD","ABDCO CORPORATION",'
        '"","","2025-03-21 00:00:00","123","0.40","USD","CIL - ABDCO '
        'CORPORATION ADDITIONAL CIL PAYMENT 000123400000"\n')


def _write(content):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


def _classify(activity, desc, symbol="XYZ", qty=""):
    """The LIVE classifier (rbc_direct.classify_rbc_row) on one row —
    the old is_rbc_merger_row / is_rbc_cil_row phrase tests were dead
    code the parser never ran (audits S073-17, S073-18)."""
    p = _write(_HEADER + (
        f'"2025-03-20 00:00:00","{activity}","{symbol}","XYZ CORP",'
        f'"{qty}","","2025-03-20 00:00:00","123","0","USD","{desc}"\n'))
    try:
        (r,) = read_rbc_rows(p).rows
    finally:
        os.remove(p)
    return classify_rbc_row(r)


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
        self.assertEqual(ev.source_symbol, "A012345.US")
        self.assertEqual(ev.target_symbol, "ABD.US")
        self.assertAlmostEqual(ev.ratio, 1.05)
        self.assertEqual(ev.qty_disposed, 12.0)
        self.assertEqual(ev.qty_received, 12.0)
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


class TestMergerRowClass(unittest.TestCase):
    def test_merger_legs_are_reorg_rows(self):
        # Both legs are paired by pair_rbc_reorganizations (which decides
        # what is a merger: TestRbcExtractor); the classifier files them
        # as reorganization legs by their MGR code.
        self.assertEqual(_classify(
            "Reorganization",
            "MGR - ABC CORPORATION MERGER TO ABDCO 1.05 NEW = 1 OLD",
            "A012345", "-12"), "reorg")
        self.assertEqual(_classify(
            "Reorganization",
            "MGR - ABDCO CORPORATION SHRS RECEIVED THRU MERGER",
            "ABD", "12"), "reorg")

    def test_option_reorg_rows_are_not_reorgs(self):
        # Option expiry / assignment are also 'Reorganization' but not mergers.
        self.assertEqual(_classify(
            "Reorganization", "EXP - CALL .ABE OPTION EXPIRATION - EXPIRED"),
            "expiry")
        self.assertEqual(_classify(
            "Other", "ASN - CALL ABE ASSIGNMENT OF OPTION"), "assignment")


class TestParserSkipsMergerRows(unittest.TestCase):
    def test_merger_rows_dropped_sale_kept(self):
        p = _write(_HEADER + _MERGER + _SALE)
        try:
            with contextlib.redirect_stderr(io.StringIO()):   # mute skip note
                txs = RbcBrokerage().parse_file(p)
        finally:
            os.remove(p)
        self.assertEqual([t for t in txs if t['symbol'].startswith('A012345')], [])
        receipts = [t for t in txs if t['symbol'] == 'ABD.US'
                    and 'RECEIVED THRU MERGER' in (t.get('description') or '')]
        self.assertEqual(receipts, [])
        # The November sale survives.
        sale = [t for t in txs if t['symbol'] == 'ABD.US' and t['quantity'] == -12.0]
        self.assertEqual(len(sale), 1)


class TestTempSymbolResolution(unittest.TestCase):
    def test_resolves_temp_code_to_real_ticker_via_company_name(self):
        # With an ABC dividend row present, the merger removal booked under
        # the temp code A012345 resolves to the real ticker ABC.US.
        p = _write(_HEADER + _SRC_DIV + _MERGER)
        try:
            ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        self.assertEqual(ev.source_symbol, "ABC.US")
        self.assertEqual(ev.target_symbol, "ABD.US")

    def test_falls_back_to_temp_code_when_unresolvable(self):
        # No ABC row anywhere → nothing to resolve against; keep the temp
        # code (and warn) rather than guess.
        p = _write(_HEADER + _MERGER)
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        self.assertEqual(ev.source_symbol, "A012345.US")
        self.assertIn("temporary reorg symbol", err.getvalue())


class TestCashInLieu(unittest.TestCase):
    def test_cil_folded_into_event(self):
        p = _write(_HEADER + _SRC_DIV + _MERGER + _CIL)
        try:
            ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        self.assertAlmostEqual(ev.cash_in_lieu, 30.40)   # 30.00 + 0.40
        self.assertEqual(ev.cash_in_lieu_currency, "USD")

    def test_cil_rows_match_and_merger_rows_do_not(self):
        self.assertEqual(_classify(
            "Reorganization",
            "CIL - ABDCO CORPORATION CASH IN LIEU OF FRAC SHARES"), "cil")
        self.assertEqual(_classify(
            "Reorganization",
            "CIL - ABDCO CORPORATION ADDITIONAL CIL PAYMENT"), "cil")
        # Merger removal/receipt rows are NOT cash-in-lieu.
        self.assertEqual(_classify(
            "Reorganization",
            "MGR - ABC CORPORATION MERGER TO ABDCO 1.05 NEW = 1 OLD",
            "A012345", "-12"), "reorg")
        # A code-less "ADDITIONAL CIL PAYMENT" is not guessed: unknown,
        # which the parser reports as UNCLASSIFIED.
        self.assertEqual(_classify(
            "Reorganization", "ABDCO CORPORATION ADDITIONAL CIL PAYMENT"),
            "unknown")

    def test_parser_skips_cil_rows(self):
        # The CIL 'Reorganization' rows must not become 0-quantity trades.
        p = _write(_HEADER + _SRC_DIV + _MERGER + _CIL + _SALE)
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
        # disposed ratio (12/12 = 1.0), landing on the broker's whole-share
        # delivery rather than the nominal 1.05 (which would leave 0.6
        # phantom dust). Source stays A012345.US (nothing to resolve to).
        p = _write(_HEADER + _MERGER)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        rows = resolve_event(ev, "rollover_s_85_1_5", country="canada")
        splits = [r for r in rows if r['action'] == 'SPLIT']
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]['symbol'], "A012345.US")
        self.assertEqual(splits[0]['symbol_new'], "ABD.US")
        self.assertAlmostEqual(splits[0]['quantity'], 1.0)

    def test_rollover_with_cash_in_lieu_sells_fractional(self):
        # With cash-in-lieu present, the SPLIT keeps the nominal ratio
        # (12 → 12.6) and a SELL retires the 0.6 fractional at the
        # cash proceeds, netting to 12 whole shares.
        p = _write(_HEADER + _SRC_DIV + _MERGER + _CIL)
        try:
            ev = parse_rbc_corporate_actions(p, "margin")[0]
        finally:
            os.remove(p)
        rows = resolve_event(ev, "rollover_s_85_1_5", country="canada")
        splits = [r for r in rows if r['action'] == 'SPLIT']
        sells = [r for r in rows if r['action'] == 'BUYSELL']
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]['symbol'], "ABC.US")
        self.assertEqual(splits[0]['symbol_new'], "ABD.US")
        self.assertAlmostEqual(splits[0]['quantity'], 1.05)
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]['symbol'], "ABD.US")
        self.assertAlmostEqual(sells[0]['quantity'], -0.6)
        self.assertAlmostEqual(sells[0]['net_amount'], 30.40)
        self.assertEqual(sells[0]['currency'], "USD")


if __name__ == "__main__":
    unittest.main()
