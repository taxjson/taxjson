"""`taxjson elect` — view / clear corporate-action elections."""
import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_run import cmd_elect

_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"

[accounts.margin]
type = "taxable"
"""

_MANIFEST = {
    "elections": {
        "163ae89a851c": {
            "summary": "2025-07-21 merger: H015283.US -> CVX.US",
            "election": "rollover_s_85_1_5",
            "notes": "rollover",
        }
    }
}


def _project(tmp):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_CONFIG)
    cache = root / "work"
    cache.mkdir()
    (cache / "margin_manifest.json").write_text(json.dumps(_MANIFEST))
    return root


def _elect(root, account=None, redo=False, reset=False, event=None):
    args = argparse.Namespace(dir=str(root), account=account, redo=redo,
                              reset=reset, event=event)
    out = io.StringIO()
    with redirect_stdout(out):
        cmd_elect(args)
    return out.getvalue()


class TestElect(unittest.TestCase):
    def test_list_shows_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _elect(_project(tmp), account="margin")
            self.assertIn("163ae89a851c", out)
            self.assertIn("rollover_s_85_1_5", out)
            self.assertIn("H015283.US", out)

    def test_list_all_accounts_when_no_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _elect(_project(tmp))
            self.assertIn("margin", out)
            self.assertIn("163ae89a851c", out)

    def test_reset_clears_the_manifest(self):
        # A legacy work/ manifest is migrated to inputs/<account>/ on first
        # touch (elections are user decisions — the one non-rebuildable
        # artifact — so they must not live in the disposable cache). The
        # reset lands in the migrated copy.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _elect(root, account="margin", reset=True)
            migrated = root / "inputs" / "margin" / "manifest.json"
            self.assertTrue(migrated.exists())
            data = json.loads(migrated.read_text())
            self.assertEqual(data["elections"], {})

    def test_reset_one_event_keeps_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(_CONFIG)
            cache = root / "work"
            cache.mkdir()
            (cache / "margin_manifest.json").write_text(json.dumps({"elections": {
                "aaa": {"summary": "A", "election": "taxable_disposition"},
                "bbb": {"summary": "B", "election": "rollover_s_85_1_5"},
            }}))
            _elect(root, account="margin", reset=True, event="aaa")
            data = json.loads(
                (root / "inputs" / "margin" / "manifest.json").read_text())
            self.assertNotIn("aaa", data["elections"])
            self.assertIn("bbb", data["elections"])

    def test_unknown_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                _elect(_project(tmp), account="nope")

    def test_redo_without_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                _elect(_project(tmp), redo=True)




class TestElectSet(unittest.TestCase):
    """`taxjson elect ACCOUNT --set EVENT_ID=ELECTION [--hint K=V]` —
    non-interactive election writing for headless/CI bootstrap."""

    def _elect_set(self, root, setarg, hints=None):
        args = argparse.Namespace(dir=str(root), account="margin",
                                  redo=False, reset=False, event=None,
                                  set=setarg, hint=hints or [])
        out = io.StringIO()
        from taxjson.bin.taxjson_run import cmd_elect
        with redirect_stdout(out):
            cmd_elect(args)
        return out.getvalue()

    def test_set_writes_election_with_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            import contextlib, io as _io
            with contextlib.redirect_stderr(_io.StringIO()):
                self._elect_set(root, "163ae89a851c=taxable_disposition",
                                hints=["fmv_per_share=12.5"])
            data = json.loads(
                (root / "inputs" / "margin" / "manifest.json").read_text())
            rec = data["elections"]["163ae89a851c"]
            self.assertEqual(rec["election"], "taxable_disposition")
            self.assertEqual(rec["hints"]["fmv_per_share"], 12.5)
            self.assertIn("elect --set", rec["notes"])
            # Prior summary is preserved (the record existed).
            self.assertIn("merger", rec["summary"])

    def test_set_rejects_unknown_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with self.assertRaises(SystemExit):
                self._elect_set(root, "163ae89a851c=not_a_real_election")

    def test_set_rejects_malformed_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with self.assertRaises(SystemExit):
                self._elect_set(root, "163ae89a851c")


# IB books a merger as paired Corporate Actions legs. The FMV is on the
# rows, so `taxable_disposition` needs no fmv_per_share hint here.
_IB_MERGER_CSV = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    "Corporate Actions,Header,Asset Category,Currency,Report Date,"
    "Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n"
    'Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00",'
    '"SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 '
    '(RGLD.CAD, ROYAL GOLD INC, US0000000002)",100.0026,0,25840.67,0,\n'
    'Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00",'
    '"SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 '
    '(SSL, SANDSTORM GOLD LTD, CA0000000001)",-1600.0416,0,-25920.67,0,\n'
)


class TestElectSetWithoutPendingDoc(unittest.TestCase):
    """`--set` after `--reset --event ID` (the documented headless
    re-election) — no pending doc lists the event and the manifest
    record is gone, so the election must be validated against the
    event RE-EXTRACTED from the account's CSVs. The country-wide
    fallback accepted a spinoff election for a merger and the next
    run died with a raw KeyError traceback."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(_CONFIG)
        (root / "work").mkdir()
        acct = root / "inputs" / "margin"
        acct.mkdir(parents=True)
        (acct / "ib_events.csv").write_text(_IB_MERGER_CSV)
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        events = parse_ib_corporate_actions(acct / "ib_events.csv", "margin")
        self.assertEqual(len(events), 1)
        return root, events[0].event_id

    def _elect_set(self, root, setarg, hints=None):
        import contextlib
        args = argparse.Namespace(dir=str(root), account="margin",
                                  redo=False, reset=False, event=None,
                                  set=setarg, hint=hints or [])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), contextlib.redirect_stderr(err):
            cmd_elect(args)
        return out.getvalue(), err.getvalue()

    def test_wrong_action_type_election_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, eid = self._project(tmp)
            with self.assertRaises(SystemExit) as cm:
                self._elect_set(root, f"{eid}=rollover_s_86_1",
                                hints=["allocated_acb=1"])
            self.assertIn("not valid for event", str(cm.exception))
            self.assertFalse(
                (root / "inputs" / "margin" / "manifest.json").exists(),
                "a refused election must not be saved")

    def test_hint_predicates_evaluated_against_extracted_event(self):
        # IB reports the merger FMV, so taxable_disposition takes no
        # fmv_per_share hint for THIS event — only the event itself
        # (not the election's static hint list) can say so.
        with tempfile.TemporaryDirectory() as tmp:
            root, eid = self._project(tmp)
            with self.assertRaises(SystemExit) as cm:
                self._elect_set(root, f"{eid}=taxable_disposition",
                                hints=["fmv_per_share=12.5"])
            self.assertIn("does not use hint", str(cm.exception))

    def test_valid_reelection_saves_without_bogus_id_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, eid = self._project(tmp)
            out, err = self._elect_set(root, f"{eid}=rollover_s_85_1_5")
            self.assertIn("Election saved", out)
            self.assertNotIn("matches no pending event", err)
            rec = json.loads((root / "inputs" / "margin" /
                              "manifest.json").read_text())["elections"][eid]
            self.assertEqual(rec["election"], "rollover_s_85_1_5")
            self.assertIn("merger: SSL.TO", rec["summary"])


if __name__ == "__main__":
    unittest.main()
