"""Regression pins for the pipeline-area audit fixes (2026-09).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no FX fetch.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

# Buy 100 @ 10.00 (comm 9.95) -> ACB 1009.95; sell 100 @ 15.00 (comm
# 9.95) -> proceeds 1490.05; gain 480.10.
_MARGIN_CSV = _QT_HEADER + (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,ISHARES COMP,"
    "-100,15.00,1500.00,9.95,1490.05,CAD,55500001,Trades,Individual\n")


def _run_cli(root, *args, env=None):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(tmp, config=_CONFIG):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(
        _MARGIN_CSV)
    return root


def _realized(root):
    """The margin account's realized gain, from `sum --json`."""
    r = _run_cli(root, "sum", "margin", "--json")
    assert r.returncode == 0, r.stderr + r.stdout
    doc = json.loads(r.stdout)
    return doc


class TestTtStemCollision(unittest.TestCase):
    """R1-116: a .tt file whose stem equals a broker group name wrote
    its converted JSON over that broker's parse (both were
    work/<acct>_<name>.json) and every CSV trade vanished, rc 0."""

    def test_tt_named_after_broker_keeps_the_broker_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "inputs" / "margin" / "questrade.tt").write_text(
                "DIVIDEND 2025-03-01 00:00:00 XEI.TO 100 CAD 0.25 25.00\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            work = root / "work"
            parsed = json.loads((work / "margin_questrade.json").read_text())
            rows = parsed.get("transactions", parsed)
            self.assertEqual(
                sorted(t["action"] for t in rows), ["BUYSELL", "BUYSELL"])
            self.assertTrue((work / "margin_tt_questrade.json").exists())
            base = json.loads((work / "margin_base.json").read_text())
            brows = base.get("transactions", base)
            self.assertEqual(
                sorted(t["action"] for t in brows),
                ["BUYSELL", "BUYSELL", "DIVIDEND"])
            summ = (root / "reports" / "margin.sum").read_text()
            self.assertIn("480.10", summ)

    def test_check_dates_reads_the_namespaced_tt_output(self):
        from taxjson.lib.check_dates import sources
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "m_sources.list").write_text(
                "questrade/q.csv\ntt/questrade.tt\n")
            got = {(k, str(p.name)) for k, _l, p in sources(cache, "m")}
        self.assertIn(("tt", "m_tt_questrade.json"), got)
        self.assertIn(("broker", "m_questrade.json"), got)


if __name__ == "__main__":
    unittest.main()
