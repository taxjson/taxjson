"""Regression tests for the tier-4 audit fixes:

- RBC merger `taxable_disposition` election values the legs from the
  fmv_per_share hint (+ cash-in-lieu) when the broker booked $0 rows.
- RBC removal/receipt on different dates pair within a ±7-day window; an
  unmatched removal warns instead of silently vanishing shares.
- is_rbc_cil_row matches 'CIL' as a whole word (FACILITIES/COUNCIL no longer
  swallow real securities' rows).
- taxjson-corp-actions emits each event_id once (overlapping statement CSVs
  double-emitted the same event).
- WASH virtual-tx ids carry FULL transaction ids (8-hex prefixes collided).
- US §1091 same-date replacements match in order ACQUIRED (intra-day time
  tiebreak; a later sheltered lot no longer beats an earlier taxable one).
- IB reverse splits with negative Quantity translate to a SPLIT (once, even
  for paired negative+positive legs).
- IB space-form class tickers ('BRK B') parse in Dividends/WHT.
- IB Open Positions section seeds income reattribution (hold-year dividends
  and interlisted names).
- Kraken ignored ledger rows are summarized instead of silently dropped.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_RBC_HEADER = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
               '"Settlement Date","Account","Value","Currency","Description"\n')


def _write(content):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


class TestRbcCilWordBoundary(unittest.TestCase):
    def test_facilities_and_cilantro_not_cil(self):
        from taxjson.lib.corp_actions import is_rbc_cil_row
        self.assertFalse(is_rbc_cil_row(
            "Dividends", "DIV - MEDICAL FACILITIES FUND CASH DIV ON 100 SHS"))
        self.assertFalse(is_rbc_cil_row("Buy", "CILANTRO HOLDINGS PURCHASE"))

    def test_genuine_cil_rows_still_match(self):
        from taxjson.lib.corp_actions import is_rbc_cil_row
        self.assertTrue(is_rbc_cil_row(
            "Reorganization",
            "CIL - CHEVRON CORPORATION CASH IN LIEU OF FRAC SHARES 166764"))
        self.assertTrue(is_rbc_cil_row(
            "Reorganization",
            "CIL - CHEVRON CORPORATION ADDITIONAL CIL PAYMENT 166764"))


class TestRbcDifferentDateMerger(unittest.TestCase):
    _REMOVAL = ('"2025-07-21 00:00:00","Reorganization","H015283","HESS CORPORATION","-15",'
                '"","2025-07-21 00:00:00","123","0","USD","MGR - HESS CORPORATION MERGER '
                'TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"\n')
    _RECEIPT_LATE = ('"2025-07-22 00:00:00","Reorganization","CVX","CHEVRON CORPORATION","15",'
                     '"","2025-07-22 00:00:00","123","0","USD","MGR - CHEVRON CORPORATION SHRS '
                     'RECEIVED THRU MERGER"\n')

    def test_receipt_next_day_still_pairs(self):
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions
        p = _write(_RBC_HEADER + self._REMOVAL + self._RECEIPT_LATE)
        try:
            events = parse_rbc_corporate_actions(p)
        finally:
            os.remove(p)
        self.assertEqual(len(events), 1, "different-date legs must pair")
        self.assertEqual(events[0].qty_disposed, 15)

    def test_unmatched_removal_warns(self):
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions
        p = _write(_RBC_HEADER + self._REMOVAL)          # no receipt at all
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                events = parse_rbc_corporate_actions(p)
        finally:
            os.remove(p)
        self.assertEqual(events, [])
        self.assertIn("NO matching share receipt", err.getvalue())


class TestRbcTaxableElectionFmvHint(unittest.TestCase):
    def _event(self):
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions
        merger = (
            '"2025-07-21 00:00:00","Reorganization","H015283","HESS CORPORATION","-15",'
            '"","2025-07-21 00:00:00","123","0","USD","MGR - HESS CORPORATION MERGER '
            'TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"\n'
            '"2025-07-21 00:00:00","Reorganization","CVX","CHEVRON CORPORATION","15",'
            '"","2025-07-21 00:00:00","123","0","USD","MGR - CHEVRON CORPORATION SHRS '
            'RECEIVED THRU MERGER"\n'
            '"2025-07-24 00:00:00","Reorganization","CVX","CHEVRON CORPORATION",'
            '"","","2025-07-24 00:00:00","123","55.82","USD","CIL - CHEVRON '
            'CORPORATION CASH IN LIEU OF FRAC SHARES 166764100000"\n')
        p = _write(_RBC_HEADER + merger)
        try:
            return parse_rbc_corporate_actions(p)[0]
        finally:
            os.remove(p)

    def test_hint_values_both_legs_and_cil(self):
        from taxjson.lib.corp_actions import resolve_event
        ev = self._event()
        rows = resolve_event(ev, 'taxable_disposition',
                             hints={'fmv_per_share': 150.0})
        sell = next(r for r in rows if r['quantity'] < 0)
        buy = next(r for r in rows if r['quantity'] > 0)
        # 15 whole CVX shares @150 = 2250 basis; proceeds include the 55.82
        # cash-in-lieu on top of the share consideration.
        self.assertGreater(sell['net_amount'], 2250.0)
        self.assertAlmostEqual(buy['net_amount'], 2250.0, delta=60.0)
        self.assertGreater(buy['price'], 0.0)

    def test_no_hint_zero_rows_warn(self):
        # Without CIL and without a hint the event truly has no value —
        # emit zero rows but say so loudly.
        from taxjson.lib.corp_actions import (parse_rbc_corporate_actions,
                                              resolve_event)
        merger = (
            '"2025-07-21 00:00:00","Reorganization","H015283","HESS CORPORATION","-15",'
            '"","2025-07-21 00:00:00","123","0","USD","MGR - HESS CORPORATION MERGER '
            'TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"\n'
            '"2025-07-21 00:00:00","Reorganization","CVX","CHEVRON CORPORATION","15",'
            '"","2025-07-21 00:00:00","123","0","USD","MGR - CHEVRON CORPORATION SHRS '
            'RECEIVED THRU MERGER"\n')
        p = _write(_RBC_HEADER + merger)
        try:
            ev = parse_rbc_corporate_actions(p)[0]
        finally:
            os.remove(p)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            resolve_event(ev, 'taxable_disposition', hints={})
        self.assertIn("NO fair market value", err.getvalue())

    def test_hint_prompt_declared_for_taxable_disposition(self):
        from taxjson.lib.corp_actions import HINTS_BY_ELECTION
        specs = HINTS_BY_ELECTION.get('taxable_disposition', [])
        self.assertTrue(any(s[0] == 'fmv_per_share' for s in specs))
        ev = self._event()
        needed = next(s[2] for s in specs if len(s) > 2)
        self.assertTrue(needed(ev), "RBC $0 event must prompt for FMV")


class TestCorpActionsEmitDedup(unittest.TestCase):
    def test_same_event_from_two_csvs_emits_once(self):
        from taxjson.bin.taxjson_corp_actions import _emit_resolved
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions, Manifest
        merger = (
            '"2025-07-21 00:00:00","Reorganization","H015283","HESS CORPORATION","-15",'
            '"","2025-07-21 00:00:00","123","0","USD","MGR - HESS CORPORATION MERGER '
            'TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"\n'
            '"2025-07-21 00:00:00","Reorganization","CVX","CHEVRON CORPORATION","15",'
            '"","2025-07-21 00:00:00","123","0","USD","MGR - CHEVRON CORPORATION SHRS '
            'RECEIVED THRU MERGER"\n')
        p1, p2 = _write(_RBC_HEADER + merger), _write(_RBC_HEADER + merger)
        try:
            events = (parse_rbc_corporate_actions(p1)
                      + parse_rbc_corporate_actions(p2))   # overlapping CSVs
        finally:
            os.remove(p1); os.remove(p2)
        self.assertEqual(len(events), 2)
        from taxjson.lib.corp_actions import ElectionRecord
        manifest = Manifest({
            ev.event_id: ElectionRecord(
                event_id=ev.event_id, summary=ev.summary(),
                election='rollover_s_85_1_5')
            for ev in events})
        out = _emit_resolved(events, manifest, 'canada')
        self.assertEqual(out['metadata']['event_count'], 1,
                         "duplicate event_id must emit once")


class TestWashIdsAndReplacementOrder(unittest.TestCase):
    def test_wash_record_pairs_full_loss_and_trigger_ids(self):
        # The virtual ADJUST/DISALLOW ids now embed FULL tx ids (WASH_<loss>
        # __<trigger>); the observable contract is that each wash_sales
        # record carries the exact full loss/trigger ids and its adjust_cmd
        # matches its own loss (8-hex prefixes used to collide, attaching
        # another loss's adjustment to the report row).
        from taxjson.lib.core import CanadaTaxRules, TaxTransaction
        txs = [
            TaxTransaction(action="BUYSELL", date="2026-01-05", time="09:30:00",
                           symbol="W.TO", quantity=100, price=20.0,
                           net_amount=2000.0, currency="CAD", account="m"),
            TaxTransaction(action="BUYSELL", date="2026-02-01", time="09:30:00",
                           symbol="W.TO", quantity=-100, price=10.0,
                           net_amount=1000.0, currency="CAD", account="m"),
            TaxTransaction(action="BUYSELL", date="2026-02-10", time="09:30:00",
                           symbol="W.TO", quantity=100, price=11.0,
                           net_amount=1100.0, currency="CAD", account="m"),
        ]
        res = CanadaTaxRules().compute_gains(txs)
        ws = res.get("wash_sales") or []
        self.assertTrue(ws, "expected a wash_sales record")
        self.assertEqual(ws[0].get("loss_tx_id"), txs[1].id)
        self.assertEqual(ws[0].get("trigger_lot_id"), txs[2].id)
        self.assertIn("ADJUST", ws[0].get("adjust_cmd") or "")

    def test_same_date_replacement_matches_order_acquired(self):
        from taxjson.lib.core import USATaxRules, TaxTransaction
        def T(**kw):
            base = dict(action="BUYSELL", date="2026-02-10", time="09:30:00",
                        symbol="ORD", quantity=0.0, price=0.0, net_amount=0.0,
                        currency="USD", account="m")
            base.update(kw)
            return TaxTransaction(**base)
        taxable = [
            T(date="2026-01-05", quantity=100, price=10.0, net_amount=1000.0),
            T(date="2026-02-01", quantity=-50, price=5.0, net_amount=250.0),  # loss 250
            # Taxable rebuy at 09:00 — acquired FIRST on the day.
            T(date="2026-02-10", time="09:00:00", quantity=50, price=5.0,
              net_amount=250.0),
        ]
        sheltered = [
            # Sheltered rebuy at 10:00 — later the same day.
            T(date="2026-02-10", time="10:00:00", quantity=50, price=5.0,
              net_amount=250.0, account="rrsp"),
        ]
        res = USATaxRules().compute_gains(taxable,
                                          sheltered_transactions=sheltered)
        loss_rows = [g for g in res["transactions"]
                     if g.get("qty") and float(g.get("raw_gain") or 0) < 0]
        self.assertTrue(loss_rows)
        perm = sum(float(g.get("permanently_disallowed") or 0)
                   for g in loss_rows)
        dis = sum(float(g.get("disallowed_amount") or 0) for g in loss_rows)
        # Order-acquired: the earlier taxable lot wins → deferral, not the
        # sheltered lot's permanent denial.
        self.assertAlmostEqual(perm, 0.0, places=2,
                               msg="sheltered lot must not beat the earlier "
                                   "taxable lot")
        self.assertAlmostEqual(dis, 250.0, places=2)


_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n')


def _parse_ib(csv_text):
    from taxjson.lib.brokerages.ib_extractor import IbBrokerage
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write(_IB_HEAD + csv_text)
        name = f.name
    try:
        return IbBrokerage().parse_file(Path(name))
    finally:
        os.remove(name)


class TestIbTier4(unittest.TestCase):
    def test_reverse_split_negative_qty_translates_once(self):
        csv_text = (
            'Corporate Actions,Header,Asset Category,Currency,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
            '"GRTS(US40638K1016) Split 1 for 10 (GRTS, GRITSTONE BIO INC, US40638K1016)",-90,0,0,0,\n'
            'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
            '"GRTS(US40638K1016) Split 1 for 10 (GRTS, GRITSTONE BIO INC, US40638K1016)",9,0,0,0,\n'
        )
        txs = _parse_ib(csv_text)
        splits = [t for t in txs if t["action"] == "SPLIT"
                  and t["symbol"].startswith("GRTS")]
        self.assertEqual(len(splits), 1,
                         "paired reverse-split legs must yield ONE SPLIT")
        self.assertAlmostEqual(splits[0]["quantity"], 0.1, places=9)

    def test_brk_b_space_ticker_income_parses(self):
        csv_text = (
            'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
            'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
            'Realized P/L,MTM P/L,Code\n'
            'Trades,Data,Order,Stocks,USD,BRK B,"2026-01-05, 09:30:00",'
            '10,400.00,0,4000,1,0,0,0,O\n'
            'Dividends,Header,Currency,Account,Date,Description,Amount\n'
            'Dividends,Data,USD,U1,2026-03-15,'
            'BRK B(US0846707026) Cash Dividend USD 1.00 per Share (Ordinary Dividend),10\n'
        )
        txs = _parse_ib(csv_text)
        div = next(t for t in txs if t["action"] == "DIVIDEND")
        self.assertEqual(div["symbol"], "BRK.B.US",
                         "space-form class ticker must match the Trades "
                         "section's BRK.B.US")

    def test_open_positions_seed_fixes_hold_year_dividends(self):
        # Dividend-only statement (buy-and-hold year): no trade rows, but the
        # Open Positions section shows STX held as USD → dividend must be
        # STX.US, not the ISIN fallback STX.L.
        csv_text = (
            'Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code\n'
            'Open Positions,Data,Summary,Stocks,USD,STX,100,1,70,7000,80,8000,1000,\n'
            'Dividends,Header,Currency,Account,Date,Description,Amount\n'
            'Dividends,Data,USD,U1,2026-03-15,'
            'STX(IE00BKVD2N49) Cash Dividend USD 0.72 per Share (Ordinary Dividend),72\n'
        )
        txs = _parse_ib(csv_text)
        div = next(t for t in txs if t["action"] == "DIVIDEND")
        self.assertEqual(div["symbol"], "STX.US")

    def test_open_positions_ambiguity_protects_interlisted(self):
        # ENB held on the TSX per Open Positions, but this file only trades
        # the NYSE listing: the root is ambiguous → the ISIN-stamped .TO
        # dividend must NOT be rewritten to .US.
        csv_text = (
            'Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code\n'
            'Open Positions,Data,Summary,Stocks,CAD,ENB,100,1,50,5000,55,5500,500,\n'
            'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
            'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
            'Realized P/L,MTM P/L,Code\n'
            'Trades,Data,Order,Stocks,USD,ENB,"2026-01-05, 09:30:00",'
            '10,40.00,0,400,1,0,0,0,O\n'
            'Dividends,Header,Currency,Account,Date,Description,Amount\n'
            'Dividends,Data,CAD,U1,2026-03-15,'
            'ENB(CA29250N1050) Cash Dividend CAD 0.9425 per Share (Ordinary Dividend),94.25\n'
        )
        txs = _parse_ib(csv_text)
        div = next(t for t in txs if t["action"] == "DIVIDEND")
        self.assertEqual(div["symbol"], "ENB.TO",
                         "cross-listed root held under both suffixes must "
                         "not be rewritten")


class TestKrakenIgnoredRowsWarn(unittest.TestCase):
    def test_deposit_rows_summarized(self):
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        csv_text = (
            '"txid","refid","time","type","subtype","aclass","asset","wallet","amount","fee","balance"\n'
            '"T1","R1","2026-01-05 10:00:00","deposit","","currency","XXBT","spot","0.5","0","0.5"\n'
            '"T2","R2","2026-01-06 10:00:00","withdrawal","","currency","XXBT","spot","-0.2","0.0002","0.3"\n'
        )
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         prefix="kr_ledgers_") as f:
            f.write(csv_text)
            name = f.name
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                KrakenBrokerage().parse_file(Path(name))
        finally:
            os.remove(name)
        msg = err.getvalue()
        # 2026-09: deposits/withdrawals graduated from ignored-and-
        # summarized to TRANSFER evidence rows (custody sidecar) — no
        # "ignored" note for them anymore; other unhandled types
        # (margin etc.) still summarize.
        self.assertNotIn("deposit", msg)
        self.assertNotIn("withdrawal", msg)




class TestIbSplitBrokerRounding(unittest.TestCase):
    """IB books fractional split results to 4 dp: 5,000 shares 1-for-3
    becomes 1666.6667, not 5000/3. The text ratio left the pool at the
    exact-math quantity, so selling the broker's full position showed
    -0.0000333 phantom dust (real AAUC.TO case)."""

    def test_ratio_snaps_to_broker_leg_quantities(self):
        csv_text = (
            'Corporate Actions,Header,Asset Category,Currency,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,CAD,"2025-05-21, 20:25:00",'
            '"AAUC(CA01921D1050) Split 1 for 3 (AAUC, ALLIED GOLD CORP, CA01921D2041)",1666.6667,0,0,0,\n'
            'Corporate Actions,Data,Stocks,CAD,"2025-05-21, 20:25:00",'
            '"AAUC(CA01921D1050) Split 1 for 3 (AAUC.OLD, ALLIED GOLD CORP, CA01921D1050)",-5000,0,0,0,\n'
        )
        txs = _parse_ib(csv_text)
        splits = [t for t in txs if t["action"] == "SPLIT"]
        self.assertEqual(len(splits), 1)
        # 1666.6667/5000, NOT 1/3: 5000 shares must land on exactly
        # the broker's 1666.6667.
        self.assertEqual(splits[0]["quantity"], 1666.6667 / 5000)
        self.assertAlmostEqual(5000 * splits[0]["quantity"], 1666.6667,
                               places=10)

    def test_single_leg_keeps_text_ratio(self):
        # Some events ship only the share-reduction leg — no refinement
        # possible, the description's ratio stands.
        csv_text = (
            'Corporate Actions,Header,Asset Category,Currency,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
            '"GRTS(US40638K1016) Split 1 for 10 (GRTS, GRITSTONE BIO INC, US40638K1016)",-90,0,0,0,\n'
        )
        txs = _parse_ib(csv_text)
        splits = [t for t in txs if t["action"] == "SPLIT"]
        self.assertEqual(len(splits), 1)
        self.assertAlmostEqual(splits[0]["quantity"], 0.1, places=12)

    def test_divergent_legs_keep_text_ratio(self):
        # A whole-share floor on a small position (10 → 3, cash in lieu
        # for the 0.3333) diverges >5% from the text ratio. With no
        # cash-in-lieu row in the file to account for the fraction,
        # refusing to refine keeps the text ratio rather than guessing.
        csv_text = (
            'Corporate Actions,Header,Asset Category,Currency,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
            '"TINY(US0000000000) Split 1 for 3 (TINY, TINY CORP, US0000000001)",3,0,0,0,\n'
            'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
            '"TINY(US0000000000) Split 1 for 3 (TINY.OLD, TINY CORP, US0000000000)",-10,0,0,0,\n'
        )
        txs = _parse_ib(csv_text)
        splits = [t for t in txs if t["action"] == "SPLIT"]
        self.assertEqual(len(splits), 1)
        self.assertAlmostEqual(splits[0]["quantity"], 1 / 3, places=12)

    def test_cash_in_lieu_row_disposes_fraction_and_snaps_ratio(self):
        # Same 10 → 3 reverse split, now with IB's cash-in-lieu row for
        # the 0.3333. The fraction is SOLD for the cash (a BUYSELL —
        # the RBC parser's CIL shape) instead of landing in the
        # unhandled tally, and it counts as part of the new leg so the
        # ratio is snapped: 10 × ratio − 0.3333 is exactly 3 shares.
        # Order-independent: IB may file the CIL row after or before
        # the legs.
        hdr = ('Corporate Actions,Header,Asset Category,Currency,Date/Time,'
               'Description,Quantity,Proceeds,Value,Realized P/L,Code\n')
        legs = (
            'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
            '"TINY(US0000000000) Split 1 for 3 (TINY, TINY CORP, US0000000001)",3,0,0,0,\n'
            'Corporate Actions,Data,Stocks,USD,"2026-03-02, 20:25:00",'
            '"TINY(US0000000000) Split 1 for 3 (TINY.OLD, TINY CORP, US0000000000)",-10,0,0,0,\n'
        )
        cil = (
            'Corporate Actions,Data,Stocks,USD,"2026-03-03, 20:25:00",'
            '"TINY(US0000000001) Cash in Lieu of Fractional Shares '
            '(TINY, TINY CORP, US0000000001)",-0.3333,3.10,0,0,\n'
        )
        for csv_text in (hdr + legs + cil, hdr + cil + legs):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                txs = _parse_ib(csv_text)
            splits = [t for t in txs if t["action"] == "SPLIT"]
            self.assertEqual(len(splits), 1)
            self.assertEqual(splits[0]["quantity"], (3 + 0.3333) / 10)
            self.assertAlmostEqual(10 * splits[0]["quantity"] - 0.3333, 3.0,
                                   places=9)
            sells = [t for t in txs if t["action"] == "BUYSELL"]
            self.assertEqual(len(sells), 1)
            self.assertEqual(sells[0]["symbol"], "TINY.US")
            self.assertEqual(sells[0]["date"], "2026-03-03")
            self.assertAlmostEqual(sells[0]["quantity"], -0.3333)
            self.assertAlmostEqual(sells[0]["net_amount"], 3.10)
            self.assertAlmostEqual(sells[0]["price"], 3.10 / 0.3333, places=6)
            self.assertNotIn("TINY", err.getvalue(),
                             "the CIL row must not reach the unhandled tally")


if __name__ == "__main__":
    unittest.main()
