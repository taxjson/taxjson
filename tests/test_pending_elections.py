"""Non-TTY / --no-input corp-action elections (the desktop-app path).

A headless `taxjson run` must never hang on the election prompt or
crash: unresolved events become work/pending_elections.json (options,
descriptions, required hints), the run exits 3, `taxjson elect
--pending` shows ready-to-copy --set lines, and after `elect --set`
the re-run completes. Uses an IB-shaped SSL->RGLD merger CSV (synthetic
amounts).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# An IB-shaped SSL->RGLD merger corp-action section, wrapped in a
# minimal IB statement (Statement header + one trade) so
# taxjson-detect-brokerage recognizes the file as IB.
_SSL_RGLD_CSV = '''\
Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,CAD,SSL,"2025-02-05, 09:31:00",1600,15.90,0,-25440.0,-1,0,0,0,O
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (RGLD.CAD, ROYAL GOLD INC, US0000000002)",100.0026,0,25840.67184,0,
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (SSL, SANDSTORM GOLD LTD, CA0000000001)",-1600.0416,0,-25920.67392,0,
'''


def _taxjson(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        # The "non-TTY" cases must not depend on how the suite itself was
        # launched: from a terminal the child would inherit the TTY and
        # stop at the election prompt.
        stdin=subprocess.DEVNULL)


def _project(tmp):
    root = Path(tmp)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin" / "ib_events.csv").write_text(
        _SSL_RGLD_CSV)
    return root


class TestCorpActionsPendingJson(unittest.TestCase):
    def test_no_input_writes_pending_and_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv = Path(tmp) / "events.csv"
            csv.write_text(_SSL_RGLD_CSV)
            pending = Path(tmp) / "pending.json"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
                 "--brokerage", "ib", "--no-input",
                 "--pending-json", str(pending), str(csv)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("need an election", r.stderr)
            doc = json.loads(pending.read_text())
        self.assertEqual(doc["schema_version"], 1)
        evs = doc["pending"]
        self.assertGreaterEqual(len(evs), 1)
        ev = evs[0]
        self.assertIn("SSL", ev["summary"])
        elections = {o["election"] for o in ev["options"]}
        self.assertIn("ignore", elections)
        self.assertTrue(any(e != "ignore" for e in elections))
        for o in ev["options"]:
            self.assertIn("description", o)
            self.assertIsInstance(o["hints"], list)


class TestRunNoInput(unittest.TestCase):
    def test_headless_run_defers_exits_3_then_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)

            # 1. Headless run: exit 3, aggregate pending doc, guidance.
            r = _taxjson(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("elections", r.stderr)
            self.assertIn("taxjson elect margin --set", r.stderr)
            agg = json.loads(
                (root / "work" / "pending_elections.json").read_text())
            pend = agg["accounts"]["margin"]["pending"]
            self.assertGreaterEqual(len(pend), 1)
            event_id = pend[0]["event_id"]

            # 2. elect --pending lists ready-to-copy --set lines.
            p = _taxjson(root, "elect", "--pending")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn(event_id, p.stdout)
            self.assertIn(f"taxjson elect margin --set {event_id}=",
                          p.stdout)
            pj = _taxjson(root, "elect", "--pending", "--json")
            self.assertEqual(
                json.loads(pj.stdout)["accounts"]["margin"]["pending"][0]
                ["event_id"], event_id)

            # 3. Resolve (ignore needs no hints) and re-run: completes,
            #    pending doc cleaned up.
            e = _taxjson(root, "elect", "margin", "--set",
                         f"{event_id}=ignore")
            self.assertEqual(e.returncode, 0, e.stderr)
            r2 = _taxjson(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertFalse(
                (root / "work" / "pending_elections.json").exists())

    def test_non_tty_run_without_flag_also_defers(self):
        # Tests run with stdin not a TTY: even WITHOUT --no-input the
        # run must defer gracefully (previously: crash), because there
        # is nobody to answer a prompt.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _taxjson(root, "run")
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("pending_elections.json", r.stderr)

    def test_no_pending_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            r = _taxjson(root, "elect", "--pending")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No pending elections", r.stdout)


if __name__ == "__main__":
    unittest.main()
