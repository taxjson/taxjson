"""Deferred-round fixes, area dedup-ids: cross-file dedup (R1-296,
S031-02), account-independent corp-action election ids (R1-301), and
the generic importer's broker name (S027-05). Synthetic data only."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.corp_actions import (CorporateAction, ElectionRecord,
                                      Manifest)

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TAXJSON_OFFLINE"] = "1"
    return env


# ---------------------------------------------------------------- R1-301

_SSL_RGLD_CSV = '''\
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (RGLD.CAD, ROYAL GOLD INC, US0000000002)",100.0026,0,25840.67184,0,
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (SSL, SANDSTORM GOLD LTD, CA0000000001)",-1600.0416,0,-25920.67392,0,
'''


def _old_scheme_id(ev: CorporateAction, account: str) -> str:
    """The id a manifest written before R1-301 carries: the readable
    part plus a 4-hex suffix hashed WITH the account name. Computed
    here independently of the library so the test pins the old
    on-disk format."""
    parts = (ev.date[:10], ev.action_type, ev.source_isin, ev.target_isin,
             f"{ev.ratio_new}-for-{ev.ratio_old}", account)
    suffix = hashlib.sha256("|".join(parts).encode()).hexdigest()[:4]
    return ev.event_id.rsplit('-', 1)[0] + '-' + suffix


def _event(account):
    return CorporateAction(
        date='2025-10-22', time='20:25:00', action_type='merger',
        source_symbol='SSL.TO', source_isin='CA0000000001',
        target_symbol='RGLD.US', target_isin='US0000000002',
        ratio_new=1, ratio_old=16, qty_disposed=1600.0416,
        qty_received=100.0026, fmv=25920.67, currency='CAD',
        target_currency='USD', account=account)


class TestElectionIdsSurviveRename(unittest.TestCase):
    """R1-301: renaming [accounts.rrsp] to retireA orphaned every
    election, because the event id hashed the account name."""

    def test_event_id_does_not_depend_on_account_name(self):
        self.assertEqual(_event('rrsp').event_id,
                         _event('retireA').event_id)

    def test_old_scheme_manifest_same_account_migrates(self):
        ev = _event('rrsp')
        old = _old_scheme_id(ev, 'rrsp')
        self.assertNotEqual(old, ev.event_id)
        self.assertEqual(ev.account_salted_event_id(), old)
        man = Manifest({old: ElectionRecord(
            event_id=old, summary='', election='rollover_s_85_1_5')})
        self.assertEqual(man.migrate_legacy([ev]), 1)
        self.assertIsNone(man.get(old))
        self.assertEqual(man.get(ev.event_id).election, 'rollover_s_85_1_5')
        self.assertEqual(man.migration_notes, [])     # a silent rekey
        self.assertEqual(man.migrate_legacy([ev]), 0)

    def test_old_scheme_manifest_renamed_account_migrates(self):
        """A manifest written by the old scheme under 'rrsp', read after
        the account became 'retireA' (the old salted id cannot be
        recomputed): the one record with the event's date and symbols
        is carried over, with a note."""
        old = _old_scheme_id(_event('rrsp'), 'rrsp')
        ev = _event('retireA')
        man = Manifest({old: ElectionRecord(
            event_id=old, summary='', election='rollover_s_85_1_5')})
        self.assertEqual(man.migrate_legacy([ev]), 1)
        self.assertEqual(man.get(ev.event_id).election, 'rollover_s_85_1_5')
        self.assertEqual(len(man.migration_notes), 1)
        self.assertIn('another account name', man.migration_notes[0])

    def test_rename_fallback_refuses_an_ambiguous_prefix(self):
        """Two events share the readable prefix (same day, same symbols,
        different ratio): the renamed-account fallback cannot tell which
        record is which, so neither is adopted (the run asks again)."""
        a = _event('retireA')
        b = _event('retireA')
        b.ratio_old = 8
        b.event_id = b._compute_id()
        old_a = _old_scheme_id(_event('rrsp'), 'rrsp')
        man = Manifest({old_a: ElectionRecord(
            event_id=old_a, summary='', election='rollover_s_85_1_5')})
        self.assertEqual(man.migrate_legacy([a, b]), 0)
        self.assertIsNotNone(man.get(old_a))

    def test_twelve_hex_legacy_id_still_migrates(self):
        ev = _event('rrsp')
        parts = ('2025-10-22', 'merger', 'CA0000000001', 'US0000000002',
                 '1-for-16', 'rrsp')
        legacy = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
        man = Manifest({legacy: ElectionRecord(
            event_id=legacy, summary='', election='taxable_disposition')})
        self.assertEqual(man.migrate_legacy([ev]), 1)
        self.assertEqual(man.get(ev.event_id).election,
                         'taxable_disposition')

    def test_cli_renamed_account_needs_no_new_election(self):
        """End to end: taxjson-corp-actions --no-input over a manifest
        written by the old scheme for 'rrsp', with the account now
        named 'retireA', resolves the event (exit 0, no pending)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            csv_p = td / "ib.csv"
            csv_p.write_text(_SSL_RGLD_CSV)
            from taxjson.lib.corp_actions import parse_ib_corporate_actions
            [ev] = parse_ib_corporate_actions(csv_p, account='rrsp')
            old = _old_scheme_id(ev, 'rrsp')
            self.assertNotEqual(old, ev.event_id)
            man_p = td / "manifest.json"
            man_p.write_text(json.dumps({"elections": {old: {
                "summary": "", "election": "rollover_s_85_1_5",
                "notes": ""}}}))
            pend = td / "pending.json"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
                 "--brokerage", "ib", "--account-name", "retireA",
                 "--country", "canada", "--manifest", str(man_p),
                 "--no-input", "--pending-json", str(pend), str(csv_p)],
                capture_output=True, text=True, env=_env())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(pend.exists() and json.loads(
                pend.read_text()).get("pending"), r.stderr)
            self.assertIn("another account name", r.stderr)
            saved = json.loads(man_p.read_text())["elections"]
            self.assertEqual(list(saved), [ev.event_id])


if __name__ == "__main__":
    unittest.main()
