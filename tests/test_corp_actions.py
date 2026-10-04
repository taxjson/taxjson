"""Tests for the corporate-action extractor + rules + manifest."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.corp_actions import (
    CorporateAction,
    ElectionRecord,
    IGNORE_ELECTION,
    Manifest,
    options_for,
    parse_ib_corporate_actions,
    parse_questrade_corporate_actions,
    resolve_event,
)
from tax_rules import rule


# IB-shaped fixture of a cross-border share merger (tickers, quantities,
# amounts, ISINs and conids are synthetic). Includes the noise we have to
# filter: the .CAD cross-listing journal (collapsed into the main event),
# the `Code=Ca` cancellation rows (dropped), and the Total summary rows
# (dropped).
_QSAN_QRGD_CSV = '''\
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,CAD,2025-08-25,"2025-08-20, 20:25:00","QSAN(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 8 (QRGD.CAD, QUEEN ROYALTY INC, US0000000002)",120.0041,0,25860.88355,0,
Corporate Actions,Data,Stocks,CAD,2025-08-25,"2025-08-20, 20:25:00","QSAN(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 8 (QSAN, QUARTZ SANDS LTD, CA0000000001)",-960.0328,0,-25920.8856,0,
Corporate Actions,Data,Stocks,CAD,2025-08-27,"2025-08-26, 20:25:00","QRGD.CAD(10000001) Merged(Acquisition) WITH QRGD 1 for 1 (QRGD.CAD, QUEEN ROYALTY INC, US0000000002)",-120,0,-25860.0,0,
Corporate Actions,Data,Stocks,CAD,2025-08-27,"2025-08-26, 20:25:00","QRGD.CAD(10000001) Merged(Acquisition) WITH QRGD 1 for 1 (QRGD.CAD, QUEEN ROYALTY INC, US0000000002)",120,0,25860.0,0,Ca
Corporate Actions,Data,Total,,,,,,0,-60.00205,0,
Corporate Actions,Data,Stocks,USD,2025-08-27,"2025-08-26, 20:25:00","QRGD.CAD(10000001) Merged(Acquisition) WITH QRGD 1 for 1 (QRGD, QUEEN ROYALTY INC, US0000000002)",120,0,18600.0,0,
Corporate Actions,Data,Stocks,USD,2025-08-27,"2025-08-26, 20:25:00","QRGD.CAD(10000001) Merged(Acquisition) WITH QRGD 1 for 1 (QRGD, QUEEN ROYALTY INC, US0000000002)",-120,0,-18600.0,0,Ca
Corporate Actions,Data,Total,,,,,,0,18600.0,0,
'''

# IB's rebook of the cancelled 08-26 journal (IB's statement shape).
_QRGD_REBOOK = (
    'Corporate Actions,Data,Stocks,CAD,2025-08-28,"2025-08-27, 20:25:00",'
    '"QRGD.CAD(10000001) Merged(Acquisition) WITH QRGD 1 for 1 '
    '(QRGD.CAD, QUEEN ROYALTY INC, US0000000002)",-120,0,-25860.0,0,\n'
    'Corporate Actions,Data,Stocks,USD,2025-08-28,"2025-08-27, 20:25:00",'
    '"QRGD.CAD(10000001) Merged(Acquisition) WITH QRGD 1 for 1 '
    '(QRGD, QUEEN ROYALTY INC, US0000000002)",120,0,18600.0,0,\n')


def _write_csv(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


# A split-up / separation: one old XYZ share exchanged for shares of
# TWO successors (new XYZ "-WI" + XYZA aerospace) in a single
# Merged(Acquisition) description with a comma-separated WITH clause.
# The single-target regex can't parse it, and dropping it left a
# phantom short in XYZA and a phantom long in XYZ. Synthetic tickers,
# ISINs and values; the row layout is IB's.
_SPLITUP_CSV = '''\
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2026-05-15,"2026-05-14, 20:25:00","XYZ(US0000000101) Merged(Acquisition) WITH XYZAV 1 for 2, US0000000102 1 for 2 (20260514173000XYZ, XYZ HOLDINGS INC, US0000000101)",-20,0,-4210.0,0,
Corporate Actions,Data,Stocks,USD,2026-05-15,"2026-05-14, 20:25:00","XYZ(US0000000101) Merged(Acquisition) WITH XYZAV 1 for 2, US0000000102 1 for 2 (XYZ, XYZ HOLDINGS INC -WI, US0000000102)",10,0,2240.0,0,
Corporate Actions,Data,Stocks,USD,2026-05-15,"2026-05-14, 20:25:00","XYZ(US0000000101) Merged(Acquisition) WITH XYZAV 1 for 2, US0000000102 1 for 2 (XYZA, XYZ AEROSPACE, US0000000103)",10,0,1970.0,0,
'''


class TestEventIdMigration(unittest.TestCase):
    """Pre-2026-07 manifests keyed elections by the opaque 12-hex hash.
    The human-legible scheme must REKEY them silently — elections are
    the non-rebuildable user artifact."""

    def _event(self):
        return CorporateAction(
            date='2025-08-20', time='20:25:00', action_type='merger',
            source_symbol='QSAN.TO', source_isin='CA0000000001',
            target_symbol='QRGD.US', target_isin='US0000000002',
            ratio_new=1, ratio_old=8, qty_disposed=960.0328,
            qty_received=120.0041, fmv=25920.89, currency='CAD',
            target_currency='USD', account='rrsp')

    def test_id_is_human_legible_and_deterministic(self):
        a, b = self._event(), self._event()
        self.assertEqual(a.event_id, b.event_id)
        self.assertTrue(a.event_id.startswith('20250820-qsan-qrgd-'),
                        a.event_id)

    def test_readable_collision_disambiguated_by_suffix(self):
        a = self._event()
        b = self._event()
        b.ratio_old = 5            # same day/symbols, different ratio
        b.event_id = b._compute_id()
        self.assertNotEqual(a.event_id, b.event_id)
        self.assertEqual(a.event_id.rsplit('-', 1)[0],
                         b.event_id.rsplit('-', 1)[0])

    def test_manifest_migrates_legacy_keys(self):
        from taxjson.lib.corp_actions import ElectionRecord, Manifest
        ev = self._event()
        man = Manifest()
        man.set(ElectionRecord(event_id=ev.legacy_event_id(),
                               summary='old', election='rollover_s_85_1_5'))
        self.assertIsNone(man.get(ev.event_id))
        self.assertEqual(man.migrate_legacy([ev]), 1)
        rec = man.get(ev.event_id)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.election, 'rollover_s_85_1_5')
        self.assertEqual(rec.event_id, ev.event_id)
        self.assertIsNone(man.get(ev.legacy_event_id()))
        # Idempotent.
        self.assertEqual(man.migrate_legacy([ev]), 0)


class TestIBSplitUp(unittest.TestCase):
    """Multi-counterparty Merged(Acquisition) — a split-up — decomposes
    into a merger (continuing entity) plus a spinoff per extra
    successor, so the rules layer needs no new event types."""

    def _events(self):
        return parse_ib_corporate_actions(_write_csv(_SPLITUP_CSV),
                                          account='rrsp')

    def test_decomposes_into_merger_plus_spinoff(self):
        events = self._events()
        self.assertEqual(
            sorted(e.action_type for e in events), ['merger', 'spinoff'])
        merger = next(e for e in events if e.action_type == 'merger')
        spin = next(e for e in events if e.action_type == 'spinoff')
        # Continuing entity: the in-leg whose ticker matches the source.
        self.assertEqual(merger.source_symbol, 'XYZ.US')
        self.assertEqual(merger.target_symbol, 'XYZ.US')
        self.assertEqual(merger.source_isin, 'US0000000101')
        self.assertEqual(merger.target_isin, 'US0000000102')
        self.assertEqual(merger.qty_disposed, 20.0)
        self.assertEqual(merger.qty_received, 10.0)
        self.assertAlmostEqual(merger.fmv, 4210.0)
        self.assertAlmostEqual(merger.target_fmv, 2240.0)
        # The extra successor spins off FROM the continuing entity.
        self.assertEqual(spin.source_symbol, 'XYZ.US')
        self.assertEqual(spin.target_symbol, 'XYZA.US')
        self.assertEqual(spin.target_isin, 'US0000000103')
        self.assertEqual(spin.qty_received, 10.0)
        self.assertAlmostEqual(spin.fmv, 1970.0)
        # Spinoff rows must sort after the merger rename.
        self.assertGreater(spin.time, merger.time)

    def test_event_ids_stable_across_runs(self):
        a = {e.event_id for e in self._events()}
        b = {e.event_id for e in self._events()}
        self.assertEqual(a, b)
        self.assertEqual(len(a), 2)

    def test_half_event_not_emitted(self):
        # Only the out-leg present (statement split): never half an
        # exchange — and never a silent skip either (audit S020-00): a
        # blocking `unsupported` event names it until the other leg's
        # statement is added or it is booked by hand.
        lines = _SPLITUP_CSV.strip().splitlines()
        path = _write_csv("\n".join(lines[:2]) + "\n")
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            evs = parse_ib_corporate_actions(path)
        self.assertEqual([e.action_type for e in evs], ['unsupported'])


class TestIBExtractor(unittest.TestCase):
    def test_merger_extraction_finds_one_event(self):
        """Nine raw rows collapse to one logical QSAN→QRGD merger."""
        path = _write_csv(_QSAN_QRGD_CSV)
        events = parse_ib_corporate_actions(path)
        mergers = [e for e in events if e.action_type == 'merger' and e.qty_disposed > 0]
        self.assertEqual(len(mergers), 1,
                         f"expected one merger event, got {len(mergers)}: "
                         f"{[e.summary() for e in events]}")

    def test_cross_listing_journal_collapses_into_main_event(self):
        """IB's QRGD.CAD → QRGD.US 1-for-1 journal is noise from the
        user's perspective — collapse it so the result is QSAN.TO→QRGD.US.
        The fixture's 08-26 journal is cancelled (Ca) and rebooked on
        08-27, as IB statements do (A2-0069)."""
        path = _write_csv(_QSAN_QRGD_CSV + _QRGD_REBOOK)
        events = parse_ib_corporate_actions(path)
        primary = next(e for e in events if e.source_symbol == 'QSAN.TO')
        # Target ISIN should be QRGD's US ISIN (chain-collapsed).
        self.assertEqual(primary.target_isin, 'US0000000002')
        # Target symbol carries the .US suffix because the final leg of
        # the chain is in USD — the position ends up on the NYSE listing.
        self.assertEqual(primary.target_symbol, 'QRGD.US')
        self.assertEqual(primary.target_currency, 'USD')
        # Raw descriptions preserved for the audit trail — 2 from the
        # primary merger leg + 2 from the collapsed cross-listing journal.
        self.assertEqual(len(primary.raw_descriptions), 4)

    def test_cancellation_rows_dropped(self):
        """The two `Code=Ca` rows would double-count the position if not
        filtered — confirm they don't show up as separate events."""
        path = _write_csv(_QSAN_QRGD_CSV)
        events = parse_ib_corporate_actions(path)
        # Only one chain-head, one chain-tail in non-cancelled rows; chain
        # collapses to one event.
        ssl_events = [e for e in events if e.source_symbol == 'QSAN.TO']
        self.assertEqual(len(ssl_events), 1)

    def test_ratio_and_quantities(self):
        path = _write_csv(_QSAN_QRGD_CSV)
        events = parse_ib_corporate_actions(path)
        ev = next(e for e in events if e.source_symbol == 'QSAN.TO')
        self.assertEqual(ev.ratio_new, 1)
        self.assertEqual(ev.ratio_old, 8)
        self.assertAlmostEqual(ev.ratio, 0.125)
        self.assertAlmostEqual(ev.qty_disposed, 960.0328)
        self.assertAlmostEqual(ev.qty_received, 120.0041)
        self.assertAlmostEqual(ev.fmv, 25920.8856, places=4)
        self.assertEqual(ev.currency, 'CAD')

    def test_event_id_stable_across_date_format_drift(self):
        """IB occasionally appends timezone hints to its date field
        (`"2025-08-20 EST"`). Re-running against a re-issued statement
        that drops or normalizes the suffix would otherwise produce a
        different event_id and orphan the user's election in the
        manifest. event_id must normalize the date to ISO `YYYY-MM-DD`
        before hashing so the same logical event hashes the same way."""
        ev_clean = CorporateAction(
            date='2025-08-20', time='20:25:00', action_type='merger',
            source_symbol='QSAN.TO', source_isin='CA0000000001',
            target_symbol='QRGD.US', target_isin='US0000000002',
            ratio_new=1, ratio_old=8,
            qty_disposed=960.0, qty_received=120.0,
            fmv=25920.89, currency='CAD', target_currency='USD',
            account='Margin',
        )
        ev_with_tz = CorporateAction(
            date='2025-08-20 EST', time='20:25:00', action_type='merger',
            source_symbol='QSAN.TO', source_isin='CA0000000001',
            target_symbol='QRGD.US', target_isin='US0000000002',
            ratio_new=1, ratio_old=8,
            qty_disposed=960.0, qty_received=120.0,
            fmv=25920.89, currency='CAD', target_currency='USD',
            account='Margin',
        )
        self.assertEqual(ev_clean.event_id, ev_with_tz.event_id)

    def test_event_id_is_stable(self):
        """Re-parsing the same CSV produces the same event_id — required
        for the manifest to stay valid across runs."""
        path = _write_csv(_QSAN_QRGD_CSV)
        events1 = parse_ib_corporate_actions(path)
        events2 = parse_ib_corporate_actions(path)
        ids1 = sorted(e.event_id for e in events1)
        ids2 = sorted(e.event_id for e in events2)
        self.assertEqual(ids1, ids2)
        # Human-legible scheme (2026-07): YYYYMMDD-src-tgt-hhhh; the
        # 4-hex suffix hashes the full identifying fields.
        import re
        self.assertTrue(all(
            re.fullmatch(r"\d{8}-[a-z0-9]+-[a-z0-9]+-[0-9a-f]{4}",
                         e.event_id) for e in events1),
            [e.event_id for e in events1])

    def test_multi_hop_chain_collapses_to_single_event(self):
        """3-hop chain A→B→C→D collapses to one event. Previously the
        single-hop collapse left an A→C result plus a stray C→D journal,
        forcing the user to mark the second one `ignore` manually. The
        fix walks the chain to fixed point in one pass."""
        # Fabricated fixture: QSAN→QRGD with TWO 1-for-1 journals after
        # the main merger (.TO_CAD → .CAD → .US). Real IB statements
        # have stopped at one hop in practice, but corporate-action
        # paperwork sometimes ships more.
        triple_csv = (
            'Corporate Actions,Header,Asset Category,Currency,Report Date,'
            'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,CAD,2025-08-25,'
            '"2025-08-20, 20:25:00","QSAN(CA0000000001) Merged(Acquisition) '
            'WITH US0000000002 1 for 8 (QRGD.CAD, QUEEN ROYALTY INC, US0000000002)",'
            '120.0041,0,25860.88355,0,\n'
            'Corporate Actions,Data,Stocks,CAD,2025-08-25,'
            '"2025-08-20, 20:25:00","QSAN(CA0000000001) Merged(Acquisition) '
            'WITH US0000000002 1 for 8 (QSAN, QUARTZ SANDS LTD, CA0000000001)",'
            '-960.0328,0,-25920.8856,0,\n'
            # Hop 1: CAD-side journal QRGD.CAD → QRGD.MID
            'Corporate Actions,Data,Stocks,CAD,2025-08-27,'
            '"2025-08-26, 20:25:00","QRGD.CAD(10000001) Merged(Acquisition) '
            'WITH QRGDMID 1 for 1 (QRGD.MID, QUEEN ROYALTY INC, US0000000002)",'
            '-120,0,-25860.0,0,\n'
            'Corporate Actions,Data,Stocks,CAD,2025-08-27,'
            '"2025-08-26, 20:25:00","QRGD.CAD(10000001) Merged(Acquisition) '
            'WITH QRGDMID 1 for 1 (QRGD.MID, QUEEN ROYALTY INC, US0000000002)",'
            '120,0,25860.0,0,\n'
            # Hop 2: MID-side journal QRGD.MID → QRGD (USD)
            'Corporate Actions,Data,Stocks,CAD,2025-08-28,'
            '"2025-08-27, 20:25:00","QRGD.MID(10000002) Merged(Acquisition) '
            'WITH QRGD 1 for 1 (QRGD.MID, QUEEN ROYALTY INC, US0000000002)",'
            '-120,0,-25860.0,0,\n'
            'Corporate Actions,Data,Stocks,USD,2025-08-28,'
            '"2025-08-27, 20:25:00","QRGD.MID(10000002) Merged(Acquisition) '
            'WITH QRGD 1 for 1 (QRGD, QUEEN ROYALTY INC, US0000000002)",'
            '120,0,18600.0,0,\n'
        )
        path = _write_csv(triple_csv)
        events = parse_ib_corporate_actions(path)
        self.assertEqual(len(events), 1,
                         f"3-hop chain should collapse to one event; got "
                         f"{[e.summary() for e in events]}")
        ev = events[0]
        self.assertEqual(ev.source_symbol, 'QSAN.TO')
        # Final hop's target — QRGD on the USD market.
        self.assertEqual(ev.target_symbol, 'QRGD.US')
        self.assertEqual(ev.target_currency, 'USD')
        # All hops' descriptions preserved (2 from main + 2 from hop 1
        # + 2 from hop 2 = 6).
        self.assertEqual(len(ev.raw_descriptions), 6)

    def test_cancelled_journal_and_its_rebook_are_one_event(self):
        """IB cancels the 08-26 cross-listing journal (Ca) and rebooks it
        on 08-27. The cancelled journal used to surface as a second
        event the user had to mark `ignore` by hand; each Ca row now
        removes its original, so only the merger (collapsed through the
        rebooked journal) is offered (A2-0069)."""
        events = parse_ib_corporate_actions(
            _write_csv(_QSAN_QRGD_CSV + _QRGD_REBOOK))
        self.assertEqual([(e.source_symbol, e.target_symbol)
                          for e in events], [('QSAN.TO', 'QRGD.US')])

    def test_cancelled_journal_without_rebook_is_undone(self):
        """With no rebook, the cancelled journal moved nothing: the
        merger lands on the CAD-side line."""
        events = parse_ib_corporate_actions(_write_csv(_QSAN_QRGD_CSV))
        self.assertEqual([(e.source_symbol, e.target_symbol)
                          for e in events], [('QSAN.TO', 'QRGD.CAD.TO')])


class TestCanadaMergerRules(unittest.TestCase):
    def _event(self, target_fmv=0.0):
        return CorporateAction(
            date='2025-08-20', time='20:25:00',
            action_type='merger',
            source_symbol='QSAN.TO', source_isin='CA0000000001',
            target_symbol='QRGD.US', target_isin='US0000000002',
            ratio_new=1, ratio_old=8,
            qty_disposed=960.0328, qty_received=120.0041,
            fmv=25920.89, currency='CAD', target_currency='USD',
            target_fmv=target_fmv,
            account='Margin',
        )

    @rule("CA-CORP-03")
    def test_taxable_disposition_emits_buysell_pair(self):
        """Default treatment: QSAN sold at FMV, QRGD bought at FMV. Two
        rows, one of each side, with prices derived from FMV/qty."""
        rows = resolve_event(self._event(), 'taxable_disposition')
        self.assertEqual(len(rows), 2)
        sell, buy = rows
        self.assertEqual(sell['action'], 'BUYSELL')
        self.assertEqual(sell['symbol'], 'QSAN.TO')
        self.assertLess(sell['quantity'], 0)
        self.assertAlmostEqual(sell['net_amount'], 25920.89, places=2)
        self.assertEqual(buy['action'], 'BUYSELL')
        self.assertEqual(buy['symbol'], 'QRGD.US')
        self.assertGreater(buy['quantity'], 0)
        # Buy time bumped one second past sell so the sort stage tie-breaks
        # deterministically — without this, the running ACB pool would
        # match the new buy against itself.
        self.assertEqual(sell['time'], '20:25:00')
        self.assertEqual(buy['time'], '20:25:01')

    @rule("CA-CORP-03")
    def test_taxable_disposition_uses_target_currency_on_buy(self):
        """Cross-currency merger: QSAN.TO (CAD) → QRGD.US (USD). The BUY
        leg must be expressed in the target market's currency and use
        the target-side FMV. The fractional 0.0041 residue snaps to
        cash-in-lieu (see `_snap_qty_to_whole_shares`), so net_amount
        is `target_fmv × 120/120.0041` — per-share basis preserved."""
        # Event with explicit target_fmv (USD value at acquisition).
        # ONE valuation (2026-09 audit): the QSAN proceeds are the FMV of
        # the QRGD received — 18,600 USD at the event-date rate — not
        # the broker's separate out-leg figure (25920.89 CAD), whose
        # gap to the in-leg was a permanent phantom gain.
        ev = self._event(target_fmv=18600.0)
        fx = lambda a, f, t, d: a * 1.4 if (f, t) == ('USD', 'CAD') \
            else a / 1.4
        rows = resolve_event(ev, 'taxable_disposition', fx=fx)
        sell, buy = rows
        # Sell side stays in source currency, valued from the in-leg.
        self.assertEqual(sell['currency'], 'CAD')
        self.assertAlmostEqual(sell['net_amount'], 18600.0 * 1.4, places=2)
        # Buy side flips to target currency and uses target_fmv,
        # proportionally reduced to the whole-share count.
        self.assertEqual(buy['currency'], 'USD')
        self.assertEqual(buy['quantity'], 120.0)  # snapped from 120.0041
        expected = (120.0 / 120.0041) * 18600.0
        self.assertAlmostEqual(buy['net_amount'], expected, places=2)
        # Per-share basis is preserved through the snap.
        self.assertAlmostEqual(
            buy['net_amount'] / buy['quantity'],
            18600.0 / 120.0041,
            places=4,
        )

    def test_taxable_disposition_falls_back_when_target_fmv_missing(self):
        """Back-compat: a CorporateAction missing target_fmv (older
        manifest or a future parser variant) still produces a sensible
        BUY row by falling back to source-side fmv. The snap applies
        here too — per-share basis preserved after the floor."""
        ev = self._event(target_fmv=0.0)  # missing
        rows = resolve_event(ev, 'taxable_disposition')
        _, buy = rows
        # Fallback path uses event.fmv (CAD value), snapped.
        expected = (120.0 / 120.0041) * 25920.89
        self.assertAlmostEqual(buy['net_amount'], expected, places=2)

    @rule("CA-CORP-04")
    def test_rollover_emits_single_split(self):
        """s. 85.1(5) rollover: SPLIT row preserves the source's ACB,
        renames to target, scales qty by ratio. No realized gain."""
        rows = resolve_event(self._event(), 'rollover_s_85_1_5')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['action'], 'SPLIT')
        self.assertEqual(rows[0]['symbol'], 'QSAN.TO')
        self.assertEqual(rows[0]['symbol_new'], 'QRGD.US')
        self.assertAlmostEqual(rows[0]['quantity'], 0.125)

    def test_unknown_election_raises(self):
        """Caller must pass a known election key — silent fallback would
        risk emitting wrong taxjson rows."""
        with self.assertRaises(KeyError):
            resolve_event(self._event(), 'not_a_real_election')

    @rule("CA-CORP-08")
    def test_ignore_election_emits_no_rows(self):
        """`ignore` is the escape hatch for IB noise (cross-listing
        journals, duplicates, etc.). It's a universal option — works
        regardless of country or event_type — and produces no taxjson
        rows so the event is effectively dropped from the pipeline."""
        rows = resolve_event(self._event(), IGNORE_ELECTION[0])
        self.assertEqual(rows, [])

    def test_options_for_appends_ignore(self):
        """Every event prompt offers `ignore` at the end. options_for
        guarantees this contract for the CLI's prompt formatter."""
        opts = options_for('canada', 'merger')
        self.assertEqual(opts[-1], IGNORE_ELECTION)
        # The real tax-treatment options come first.
        keys = [k for k, _ in opts]
        self.assertIn('taxable_disposition', keys)
        self.assertIn('rollover_s_85_1_5', keys)

    def test_options_for_unknown_event_still_offers_ignore(self):
        """Even if no country/event_type rule exists, the user can still
        mark the event ignored — important for IB rows whose semantics
        we don't know how to handle yet."""
        opts = options_for('canada', 'unknown_event_type')
        self.assertEqual(opts, [IGNORE_ELECTION])


class TestManifest(unittest.TestCase):
    def test_round_trip(self):
        """Save → load round-trips election records exactly. The manifest
        is the audit artifact, so loss-of-fidelity here would mean a
        re-run produces different taxjson than the user authorized."""
        m = Manifest()
        m.set(ElectionRecord(
            event_id='abc123',
            summary='QSAN.TO → QRGD.US merger 2025-08-20',
            election='taxable_disposition',
            notes='No election filed; cross-border default',
        ))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'elections.json'
            m.save(path)
            loaded = Manifest.load(path)
        rec = loaded.get('abc123')
        self.assertIsNotNone(rec)
        self.assertEqual(rec.election, 'taxable_disposition')
        self.assertIn('cross-border', rec.notes)

    def test_load_missing_file_returns_empty(self):
        """Pipeline tools call Manifest.load on a possibly-fresh path —
        treating absence as empty (not an error) keeps the first-run UX
        clean."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'does_not_exist.json'
            loaded = Manifest.load(path)
        self.assertEqual(loaded.records, {})

    def test_load_empty_file_returns_empty(self):
        """An empty file is treated like a missing one — common when a
        previous run touched the path but never wrote any elections."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'empty.json'
            path.write_text('')
            self.assertEqual(Manifest.load(path).records, {})

    def test_load_invalid_json_gives_actionable_error(self):
        """Common user error: pointed --manifest at a CSV or other file.
        We surface the path + diagnostic instead of a Python traceback so
        the user can fix the invocation without reading our internals."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wrong.json'
            path.write_text('Trades,Header,Field Name,Field Value\n')
            with self.assertRaises(ValueError) as cm:
                Manifest.load(path)
            self.assertIn(str(path), str(cm.exception))
            self.assertIn('not valid JSON', str(cm.exception))

    def test_hints_round_trip(self):
        """Spinoff elections carry numeric hints (FMV, allocated ACB).
        Those must survive save→load so the manifest is the authoritative
        record — re-running emits identical taxjson rows from cached
        decisions."""
        m = Manifest()
        m.set(ElectionRecord(
            event_id='xyz789',
            summary='QDEF spinoff',
            election='taxable_deemed_dividend',
            notes='FMV per the IRC OPRA quote on receipt date',
            hints={'fmv_per_share': 0.5},
        ))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'elections.json'
            m.save(path)
            loaded = Manifest.load(path)
        rec = loaded.get('xyz789')
        self.assertEqual(rec.hints['fmv_per_share'], 0.5)


# ============================================================================
# Questrade — DIS rows for spinoffs (warrant→rights conversion bookkeeping)
# ============================================================================

# Mirrors the Questrade export pattern for a warrant spinoff: three DIS rows that net to +80 of
# a Questrade-internal warrant code (Q051234) spun off from QDEF parent
# (parent reference K071234, Questrade's internal code for QUANTA DEV CORP).
# The Buy row is how the parent's TICKER is known: the DIS rows name it
# only by SEC# code + company name.
_QT_WARRANT_SPINOFF_CSV = (
    "Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,"
    "Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type\n"
    "2025-03-03 12:00:00 AM,2025-03-04 12:00:00 AM,Buy,QDEF,"
    "QUANTA DEVELOPMENT CORP WE ACTED AS AGENT,800,20.00,-16000.00,-4.95,"
    "-16004.95,USD,12345678,Trades,Individual LIRA\n"
    "2025-08-25 12:00:00 AM,2025-08-25 12:00:00 AM,DIS,Q051234,"
    "\"WTS QUANTA DEV CORP WT EXP PENDING SPINOFF ON 800 SHS FROM SEC# "
    "K071234 QUANTA DEVELOPMENT CORP REC 08/21/25 PAY 08/25/25\","
    "80.0,0.0,0.0,0.0,0.0,USD,12345678,Dividends,Individual LIRA\n"
    "2025-08-27 12:00:00 AM,2025-08-27 12:00:00 AM,DIS,Q051234,"
    "\"WTS QUANTA DEV CORP WT EXP PENDING SPINOFF ON 800 SHS FROM SEC# "
    "K071234 QUANTA DEVELOPMENT CORP REC 08/21/25 PAY 08/25/25 RELEASING AS RIGHTS DIST\","
    "-80.0,0.0,0.0,0.0,0.0,USD,12345678,Dividends,Individual LIRA\n"
    "2025-08-27 12:00:00 AM,2025-08-27 12:00:00 AM,DIS,Q051234,"
    "\"WTS QUANTA DEV CORP WT EXP PENDING RTS DIST ON 800 SHS "
    "REC 08/21/25 PAY 08/25/25\","
    "80.0,0.0,0.0,0.0,0.0,USD,12345678,Dividends,Individual LIRA\n"
)


class TestQuestradeExtractor(unittest.TestCase):
    def test_three_row_warrant_pattern_collapses_to_one_event(self):
        """Questrade's warrant→rights bookkeeping spreads a single
        spinoff across 3 DIS rows (+80 / -80 / +80). The user only
        cares about the net position, so the extractor must collapse
        them into one event with qty_received=80."""
        path = _write_csv(_QT_WARRANT_SPINOFF_CSV)
        events = parse_questrade_corporate_actions(path)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action_type, 'spinoff')
        # Currency-suffixed since the warrant split-position fix (the
        # bare symbol split one position across two book symbols).
        self.assertEqual(ev.target_symbol, 'Q051234.US')
        self.assertAlmostEqual(ev.qty_received, 80.0)
        # Spinoff doesn't dispose of the parent — qty_disposed must be 0
        # so downstream rules don't try to emit a sell of the parent.
        self.assertEqual(ev.qty_disposed, 0.0)

    def test_parent_extracted_from_description(self):
        """The `FROM SEC# X NAME` block names the parent by Questrade's
        internal code and company name; the ticker the parent is BOOKED
        under comes from the statement's trade of that company. The
        s. 86.1 rollover's parent-ACB-reduction ADJUST must point at that
        pool — on the bare code it hit an empty pool and the allocated
        basis was counted twice."""
        path = _write_csv(_QT_WARRANT_SPINOFF_CSV)
        ev = parse_questrade_corporate_actions(path)[0]
        self.assertEqual(ev.source_symbol, 'QDEF.US')
        self.assertEqual(ev.source_isin, 'K071234')

    def test_unresolvable_parent_keeps_code_and_warns(self):
        """No trade/transfer of the parent in this statement: the code
        stays (nothing better is known) and the extractor says so."""
        import io
        from contextlib import redirect_stderr
        header, _buy, rest = _QT_WARRANT_SPINOFF_CSV.split('\n', 2)
        path = _write_csv(header + '\n' + rest)
        buf = io.StringIO()
        with redirect_stderr(buf):
            ev = parse_questrade_corporate_actions(path)[0]
        self.assertEqual(ev.source_symbol, 'K071234')
        self.assertIn('ticker is unknown', buf.getvalue())

    def test_ratio_extracted_from_on_shs(self):
        """`ON 800 SHS` is the parent share count the user held; 80
        warrants / 800 shares = 1:10. Ratio drives the s. 86.1 ACB
        allocation suggestion if user picks rollover."""
        path = _write_csv(_QT_WARRANT_SPINOFF_CSV)
        ev = parse_questrade_corporate_actions(path)[0]
        self.assertEqual(ev.ratio_new, 80.0)
        self.assertEqual(ev.ratio_old, 800.0)
        self.assertAlmostEqual(ev.ratio, 0.1)

    def test_account_label_applied(self):
        """--account-name flows through to the event so downstream tools
        pool the corp-action rows with the same broker's trades for that
        account (Margin/LIRA/TFSA/etc.)."""
        path = _write_csv(_QT_WARRANT_SPINOFF_CSV)
        events = parse_questrade_corporate_actions(path, account='LIRA')
        self.assertEqual(events[0].account, 'LIRA')

    def test_account_label_default_when_none_passed(self):
        """When --account-name is omitted, the extractor's own default
        ('Questrade' / 'IB') wins — NOT the literal 'default' string.
        Regression for the silent pool-fragmentation bug where the CLI
        defaulted to 'default' and clobbered the parser's label."""
        path = _write_csv(_QT_WARRANT_SPINOFF_CSV)
        events = parse_questrade_corporate_actions(path)
        self.assertEqual(events[0].account, 'Questrade')


class TestApplySuffix(unittest.TestCase):
    """`_apply_suffix` decides whether a symbol already has a market
    suffix or needs one appended. The old broad check `if '.' in symbol`
    silently broke class-share tickers (BRK.B, BRK.A, RDS.A) by treating
    the share-class dot as a market suffix."""

    def test_class_share_gets_market_suffix(self):
        from taxjson.lib.corp_actions import _apply_suffix
        # BRK.B is a class-B share, NOT a market suffix.
        self.assertEqual(_apply_suffix('BRK.B', 'US'), 'BRK.B.US')

    def test_already_suffixed_symbol_unchanged(self):
        from taxjson.lib.corp_actions import _apply_suffix
        self.assertEqual(_apply_suffix('AAPL.US', 'US'), 'AAPL.US')
        self.assertEqual(_apply_suffix('SHOP.TO', 'TO'), 'SHOP.TO')

    def test_bare_symbol_gets_suffix(self):
        from taxjson.lib.corp_actions import _apply_suffix
        self.assertEqual(_apply_suffix('AAPL', 'US'), 'AAPL.US')

    def test_unknown_suffix_treated_as_class_share(self):
        """Documents the trade-off in the `BRK.B` fix: a symbol with a
        suffix that ISN'T in the known-market set (`TO/US/AX/L`) gets
        treated like a class-share dot and the new suffix is appended.
        For `QRGD.MID` (the cross-listing intermediate marker IB uses),
        this means `_apply_suffix('QRGD.MID', 'US')` → `'QRGD.MID.US'`.
        If the broker ever ships a new market suffix the helper doesn't
        know about, this test will catch the resulting double-suffix
        and prompt extending `_CURRENCY_SUFFIX`."""
        from taxjson.lib.corp_actions import _apply_suffix
        self.assertEqual(_apply_suffix('QRGD.MID', 'US'), 'QRGD.MID.US')


class TestOccHelpers(unittest.TestCase):
    """Pin the canonical OCC parsers to prevent regressions when the
    regex gets touched. Particularly: the F: futures prefix MUST be
    preserved in the returned underlying so futures option buckets
    (e.g. F:CL.US) stay distinct from same-base equity buckets."""

    def test_parse_equity_option(self):
        from taxjson.lib.core import parse_option_underlying
        self.assertEqual(parse_option_underlying('AAPL250120C00150000.US'), 'AAPL.US')
        self.assertEqual(parse_option_underlying('MDX260320P00027000.TO'), 'MDX.TO')

    def test_parse_preserves_futures_prefix(self):
        """Regression: a previous consolidation pulled `F:` outside the
        capture group, stripping it from the returned underlying. That
        merged futures positions into their same-base equity buckets in
        the summary report. The pipeline diff caught this; the test
        pins it."""
        from taxjson.lib.core import parse_option_underlying
        self.assertEqual(
            parse_option_underlying('F:CL250120P00053000.US'),
            'F:CL.US',
        )
        self.assertEqual(
            parse_option_underlying('/CL250120P00053000.US'),
            '/CL.US',
        )

    def test_is_option_symbol_handles_prefix_and_anchors(self):
        from taxjson.lib.core import is_option_symbol
        self.assertTrue(is_option_symbol('AAPL250120C00150000.US'))
        self.assertTrue(is_option_symbol('F:CL250120P00053000.US'))
        self.assertFalse(is_option_symbol('AAPL'))
        self.assertFalse(is_option_symbol('F:CL.US'))  # futures STOCK, not option
        self.assertFalse(is_option_symbol(''))
        self.assertFalse(is_option_symbol(None))


class TestEventIdDateNormalization(unittest.TestCase):
    """`_normalize_date` strips anything past the first 10 ISO chars
    so IB timezone-suffix drift doesn't orphan elections in the
    manifest. Non-ISO inputs fall through unchanged — covered here so
    a future refactor that tries to be smarter doesn't accidentally
    swallow a `10/22/2025`-style input as a 10-char-prefix match."""

    def test_iso_date_passes_through(self):
        from taxjson.lib.corp_actions import CorporateAction
        self.assertEqual(CorporateAction._normalize_date('2025-08-20'), '2025-08-20')

    def test_iso_date_with_trailing_timezone_strips(self):
        from taxjson.lib.corp_actions import CorporateAction
        self.assertEqual(CorporateAction._normalize_date('2025-08-20 EST'), '2025-08-20')

    def test_non_iso_format_falls_through(self):
        from taxjson.lib.corp_actions import CorporateAction
        # Non-ISO format isn't ISO-prefix, so the first-10-chars test
        # doesn't apply — we return the raw string and let the hash
        # differ (the caller is responsible for canonical date format).
        self.assertEqual(CorporateAction._normalize_date('10/22/2025'), '10/22/2025')

    def test_empty_returns_empty(self):
        from taxjson.lib.corp_actions import CorporateAction
        self.assertEqual(CorporateAction._normalize_date(''), '')


class TestCanadaSpinoffRules(unittest.TestCase):
    def _event(self):
        # The extractor's own output (not a hand-built event), so the
        # symbols the rules emit on are the ones the books carry.
        path = _write_csv(_QT_WARRANT_SPINOFF_CSV)
        return parse_questrade_corporate_actions(path, account='LIRA')[0]

    @rule("CA-CORP-07")
    def test_deemed_dividend_emits_div_plus_buysell(self):
        """Default treatment: foreign dividend at FMV + cost-basis BUYSELL
        at same FMV so the later sale realizes only post-receipt change."""
        rows = resolve_event(
            self._event(), 'taxable_deemed_dividend',
            country='canada', hints={'fmv_per_share': 0.5},
        )
        self.assertEqual(len(rows), 2)
        div, buy = rows
        self.assertEqual(div['action'], 'DIVIDEND')
        self.assertEqual(div['symbol'], 'Q051234.US')
        self.assertAlmostEqual(div['net_amount'], 40.0)  # 80 × 0.5
        self.assertEqual(buy['action'], 'BUYSELL')
        self.assertAlmostEqual(buy['quantity'], 80.0)
        self.assertAlmostEqual(buy['net_amount'], 40.0)
        # Buy must sort after dividend so the cost basis is in place
        # before any same-day sale (rare but possible).
        self.assertGreater(buy['time'], div['time'])

    def test_deemed_dividend_with_zero_fmv_still_emits(self):
        """Zero FMV (user hasn't filled it in yet) shouldn't block the
        pipeline — better to emit zero-value rows so the rest of the run
        completes, and the user patches the numbers later."""
        rows = resolve_event(
            self._event(), 'taxable_deemed_dividend',
            country='canada', hints={},
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['net_amount'], 0.0)
        self.assertEqual(rows[1]['net_amount'], 0.0)

    @rule("CA-CORP-06")
    def test_rollover_s_86_1_emits_acquire_plus_parent_adjust(self):
        """s. 86.1 rollover: new position acquired at allocated cost,
        parent's ACB reduced by the same amount (ADJUST row) — on the
        parent's TRADED symbol, not Questrade's internal SEC# code."""
        rows = resolve_event(
            self._event(), 'rollover_s_86_1',
            country='canada', hints={'allocated_acb': 1000.0},
        )
        self.assertEqual(len(rows), 2)
        buy, adj = rows
        self.assertEqual(buy['action'], 'BUYSELL')
        self.assertEqual(buy['symbol'], 'Q051234.US')
        self.assertAlmostEqual(buy['net_amount'], 1000.0)
        self.assertEqual(adj['action'], 'ADJUST')
        self.assertEqual(adj['symbol'], 'QDEF.US')
        self.assertAlmostEqual(adj['net_amount'], -1000.0)

    def test_manifest_rekeys_election_saved_under_parent_code_id(self):
        """The corrected parent ticker changes only the readable root of
        the event id (`…-k071234-…` → `…-qdef-…`); the hashed suffix is
        unchanged. An election saved under the old id must follow the
        event rather than be orphaned and re-prompted."""
        from taxjson.lib.corp_actions import ElectionRecord, Manifest
        ev = self._event()
        date, _src, tgt, suffix = ev.event_id.split('-')
        old_id = f"{date}-k071234-{tgt}-{suffix}"
        self.assertNotEqual(old_id, ev.event_id)
        man = Manifest({old_id: ElectionRecord(
            event_id=old_id, summary='', election='rollover_s_86_1',
            hints={'allocated_acb': 1000.0})})
        self.assertEqual(man.migrate_legacy([ev]), 1)
        self.assertIsNone(man.get(old_id))
        self.assertEqual(man.get(ev.event_id).election, 'rollover_s_86_1')

    def test_ignore_works_for_spinoff(self):
        rows = resolve_event(self._event(), IGNORE_ELECTION[0])
        self.assertEqual(rows, [])

    def test_spinoff_offered_in_options_for(self):
        """The interactive prompter pulls from options_for; spinoff
        elections must appear there for the resolver to show them."""
        keys = [k for k, _ in options_for('canada', 'spinoff')]
        self.assertIn('taxable_deemed_dividend', keys)
        self.assertIn('rollover_s_86_1', keys)
        self.assertIn(IGNORE_ELECTION[0], keys)


if __name__ == '__main__':
    unittest.main()


class TestQuestradeSpinoffChainGrouping(unittest.TestCase):
    """Warrant-distribution chain: Questrade books a warrant
    distribution as a symbol-less placeholder (+80), a reversal under
    the real symbol (-80), and the actual delivery (+80). Grouping
    by symbol split the chain — an event with an EMPTY target symbol
    (invalid book row downstream) plus a skipped net-zero warrant group.
    The chain identity is REC/PAY + ON-N-SHS."""

    _HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Account #,Activity Type,Account Type\n")

    def _rows(self):
        d = ("WTS QUANTA DEV CORP WT EXP PENDING SPINOFF ON 800 SHS "
             "FROM SEC# K071234 QUANTA DEVELOPMENT CORP REC 08/21/25 "
             "PAY 08/25/25")
        return (
            f"2025-08-25 12:00:00 AM,2025-08-25 12:00:00 AM,DIS,,"
            f"{d},80,0,0,0,0,USD,1,Dividends,API\n"
            f"2025-08-27 12:00:00 AM,2025-08-25 12:00:00 AM,DIS,QDEFW,"
            f"{d} RELEASING AS RIGHTS DIST,-80,0,0,0,0,USD,1,"
            f"Dividends,API\n"
            f"2025-08-27 12:00:00 AM,2025-08-27 12:00:00 AM,DIS,QDEFW,"
            f"WTS QUANTA DEV CORP WT EXP PENDING RTS DIST ON 800 SHS "
            f"REC 08/21/25 PAY 08/25/25,80,0,0,0,0,USD,1,"
            f"Dividends,API\n")

    def test_chain_nets_to_one_event_with_the_real_symbol(self):
        import tempfile
        from pathlib import Path
        from taxjson.lib.corp_actions import (
            parse_questrade_corporate_actions)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "questrade_2025.csv"
            f.write_text(self._HDR + self._rows())
            events = parse_questrade_corporate_actions(f)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.target_symbol, "QDEFW.US",
                         "the chain's real symbol, currency-suffixed "
                         "like the parser's trade rows — never the "
                         "placeholder's empty one")
        self.assertEqual(ev.qty_received, 80.0)
        self.assertEqual(ev.source_isin, "K071234")

    def test_unresolvable_chain_is_skipped_loudly(self):
        import io
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        from taxjson.lib.corp_actions import (
            parse_questrade_corporate_actions)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "questrade_2025.csv"
            f.write_text(self._HDR + (
                "2025-08-25 12:00:00 AM,2025-08-25 12:00:00 AM,DIS,,"
                "MYSTERY SPINOFF ON 500 SHS FROM SEC# X1 REC 08/21/25 "
                "PAY 08/25/25,50,0,0,0,0,USD,1,Dividends,API\n"))
            buf = io.StringIO()
            with redirect_stderr(buf):
                events = parse_questrade_corporate_actions(f)
        self.assertEqual(events, [],
                         "an empty-symbol event must never be emitted")
        self.assertIn("NO resolvable target symbol", buf.getvalue())
