"""CLI glue for taxjson-corp-actions: turning events + a manifest of elections
into emitted taxjson rows. The rule/extractor primitives are tested elsewhere;
this covers the previously-untested orchestration (_emit_resolved, _unresolved,
and the non-interactive main path)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.corp_actions import (
    CorporateAction, Manifest, ElectionRecord,
)
from taxjson.bin.taxjson_corp_actions import _emit_resolved, _unresolved

REPO_ROOT = Path(__file__).resolve().parent.parent


def _merger():
    return CorporateAction(
        date="2025-07-21", time="09:30:00", action_type="merger",
        source_symbol="H015283.US", source_isin="H015283.US",
        target_symbol="CVX.US", target_isin="CVX.US",
        ratio_new=1.025, ratio_old=1.0, qty_disposed=15.0, qty_received=15.0,
        fmv=0.0, currency="USD", target_currency="USD", account="margin")


class TestEmitResolved(unittest.TestCase):
    def test_rollover_election_emits_split(self):
        ev = _merger()
        man = Manifest({ev.event_id: ElectionRecord(
            event_id=ev.event_id, summary=ev.summary(),
            election="rollover_s_85_1_5")})
        out = _emit_resolved([ev], man, "canada")
        splits = [r for r in out["transactions"] if r["action"] == "SPLIT"]
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["symbol"], "H015283.US")
        self.assertEqual(splits[0]["symbol_new"], "CVX.US")
        self.assertEqual(out["metadata"]["event_count"], 1)
        self.assertEqual(out["metadata"]["ignored_count"], 0)

    def test_ignore_election_emits_no_rows(self):
        ev = _merger()
        man = Manifest({ev.event_id: ElectionRecord(
            event_id=ev.event_id, summary=ev.summary(), election="ignore")})
        out = _emit_resolved([ev], man, "canada")
        self.assertEqual(out["transactions"], [])
        self.assertEqual(out["metadata"]["ignored_count"], 1)

    def test_unresolved_lists_events_missing_from_manifest(self):
        ev = _merger()
        self.assertEqual(_unresolved([ev], Manifest({})), [ev])
        man = Manifest({ev.event_id: ElectionRecord(
            event_id=ev.event_id, summary="", election="ignore")})
        self.assertEqual(_unresolved([ev], man), [])


class TestCliMainNonInteractive(unittest.TestCase):
    def test_resolved_manifest_emits_json_without_prompting(self):
        # A pre-populated manifest + non-TTY stdin must emit resolved JSON,
        # never block on a prompt.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            csv = tmp / "margin.csv"
            csv.write_text(
                '"Date","Activity","Symbol","Symbol Description","Quantity",'
                '"Price","Settlement Date","Account","Value","Currency",'
                '"Description"\n'
                '"2025-07-21","Reorganization","H015283","HESS CORPORATION",'
                '"-15","","2025-07-21","1","0","USD","MGR - HESS CORPORATION '
                'MERGER TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"\n'
                '"2025-07-21","Reorganization","CVX","CHEVRON CORPORATION","15",'
                '"","2025-07-21","1","0","USD","MGR - CHEVRON CORPORATION SHRS '
                'RECEIVED THRU MERGER"\n')
            # Resolve the event id the extractor will produce.
            from taxjson.lib.corp_actions import parse_rbc_corporate_actions
            ev = parse_rbc_corporate_actions(csv, "margin")[0]
            manifest = tmp / "m.json"
            manifest.write_text(json.dumps({"elections": {ev.event_id: {
                "summary": ev.summary(), "election": "rollover_s_85_1_5"}}}))
            result = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
                 "--brokerage", "rbc_direct", "--country", "canada",
                 "--account-name", "margin", "--manifest", str(manifest),
                 str(csv)],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            splits = [t for t in data["transactions"] if t["action"] == "SPLIT"]
            self.assertEqual(len(splits), 1)
            self.assertEqual(splits[0]["symbol_new"], "CVX.US")


if __name__ == "__main__":
    unittest.main()
