"""`taxjson edge-cases` (lib/edge_cases): year-boundary and superficial-
loss-window edge reporting, on a synthetic project's work files."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.edge_cases import analyze, render_text

SRC = Path(__file__).resolve().parents[1] / "src"


def _tx(acct, action, date, settle, sym, qty, net=0.0, tid=None):
    return {"account": acct, "action": action, "date": date,
            "date_settle": settle, "time": "10:00:00", "symbol": sym,
            "quantity": qty, "net_amount": net, "currency": "CAD",
            "id": tid or f"{sym}-{date}-{qty}"}


def _gain(acct, date, settle, sym, qty, raw, denied=0.0, perm=0.0, tid=None):
    return {"account": acct, "date": date, "date_settle": settle,
            "symbol": sym, "qty": qty, "gain": raw + denied,
            "raw_gain": raw, "disallowed_amount": denied,
            "permanently_disallowed": perm, "proceeds": 1000.0,
            "cost": 1000.0 - raw, "id": tid or f"g-{sym}-{date}"}


class EdgeCaseProject(unittest.TestCase):
    CFG = {"settings": {"year": 2025, "country": "canada",
                        "tax_date": "settle"},
           "accounts": {"margin": {"type": "taxable"},
                        "rrsp": {"type": "sheltered"}}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        work = self.root / "work"
        work.mkdir()
        margin = [
            # Straddles the 2025/2026 year end: a 2026 disposition.
            _tx("margin", "BUYSELL", "2025-01-02", "2025-01-03", "XYZ.US", 100),
            _tx("margin", "BUYSELL", "2025-12-31", "2026-01-02", "XYZ.US", -100),
            # Loss on 2025-03-03 (settle 03-04); rebuy traded 04-02
            # settling 04-03: day 30 on the settlement basis (inside),
            # day 30 on the trade basis too; another rebuy traded 04-03
            # settling 04-04 is day 31 (outside) on settle, day 32 trade.
            _tx("margin", "BUYSELL", "2025-01-10", "2025-01-13", "ABC.TO", 100),
            _tx("margin", "BUYSELL", "2025-03-03", "2025-03-04", "ABC.TO", -100),
            _tx("margin", "BUYSELL", "2025-04-03", "2025-04-04", "ABC.TO", 50),
            # Settlement basis day 30 exactly, trade basis day 31.
            _tx("margin", "BUYSELL", "2025-02-01", "2025-02-03", "DEF.TO", 10),
            _tx("margin", "BUYSELL", "2025-03-03", "2025-03-04", "DEF.TO", -10),
            _tx("margin", "BUYSELL", "2025-04-02", "2025-04-03", "DEF.TO", 10),
            # A call on ABC bought inside the ABC loss window, still held.
            _tx("margin", "BUYSELL", "2025-03-10", "2025-03-11",
                "ABC260116C00010000.TO", 1),
        ]
        rrsp = [_tx("rrsp", "BUYSELL", "2025-04-01", "2025-04-02",
                    "ABC.TO", 5)]
        (work / "margin_base.json").write_text(json.dumps(margin))
        (work / "rrsp_base.json").write_text(json.dumps(rrsp))
        gains = {"transactions": [
            _gain("margin", "2025-12-31", "2026-01-02", "XYZ.US", 100, 500.0),
            _gain("margin", "2025-03-03", "2025-03-04", "ABC.TO", 100, -300.0,
                  denied=15.0, perm=15.0),
            _gain("margin", "2025-03-03", "2025-03-04", "DEF.TO", 10, -40.0,
                  denied=40.0)],
            "inventory": [{"account": "margin", "symbol": "DEF.TO",
                           "qty": 10, "deferred_wash": 40.0}]}
        (work / "margin_gains_wash.json").write_text(json.dumps(gains))

    def tearDown(self):
        self.tmp.cleanup()

    def test_year_straddle_lands_in_settlement_year(self):
        doc = analyze(self.root, self.CFG)
        s = [r for r in doc["year_boundary"]["straddles"]
             if r["symbol"] == "XYZ.US"]
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["lands_in"], 2026)
        self.assertIn("2026 sale: a disposition", s[0]["why"])

    def test_trade_basis_moves_it_back(self):
        cfg = json.loads(json.dumps(self.CFG))
        cfg["settings"]["tax_date"] = "trade"
        doc = analyze(self.root, cfg)
        s = [r for r in doc["year_boundary"]["straddles"]
             if r["symbol"] == "XYZ.US"]
        self.assertEqual(s[0]["lands_in"], 2025)

    def test_window_edge_day_counts_and_basis_flip(self):
        doc = analyze(self.root, self.CFG)
        edges = {r["symbol"]: r for r in doc["window_edges"]}
        abc = edges["ABC.TO"]["items"]
        acq = [i for i in abc if i["kind"] == "acquisition"]
        self.assertEqual(sorted(i["day"] for i in acq), [29, 31])
        self.assertTrue(any(i["account"] == "rrsp" and i["sheltered"]
                            and i["inside"] for i in acq))
        self.assertTrue(any(not i["inside"] for i in acq))
        deff = {i["day"]: i for i in edges["DEF.TO"]["items"]}
        self.assertEqual(sorted(deff), [-29, 30])
        self.assertTrue(deff[30]["inside"])
        self.assertEqual(deff[30]["other_basis_day"], 30)
        # The pre-loss lot is the one sold: the day-30 holding is the
        # rebuy, so it is annotated as not a replacement.
        self.assertIn("lot that was sold", deff[-29]["why"])
        self.assertNotIn("lot that was sold", deff[30]["why"])

    def test_basis_flip_flagged(self):
        cfg = json.loads(json.dumps(self.CFG))
        doc = analyze(self.root, cfg, margin=5)
        items = [i for r in doc["window_edges"] for i in r["items"]]
        # ABC rebuy settling 04-04 is day 31 on settle, day 31 on trade
        # (03-03 -> 04-03): no flip; RRSP 04-02 is day 29 / 29.
        self.assertFalse(any(i["basis_flip"] for i in items
                             if i["date"] == "2025-04-02"))

    def test_call_in_window_is_advisory(self):
        doc = analyze(self.root, self.CFG)
        cw = doc["calls_in_windows"]
        self.assertEqual([r["symbol"] for r in cw], ["ABC.TO"])
        it = cw[0]["items"][0]
        self.assertEqual(it["option"], "ABC260116C00010000.TO")
        self.assertIn("still held on day 30", it["why"])
        self.assertIn("does not deny", it["why"])

    def test_deferred_and_text(self):
        doc = analyze(self.root, self.CFG)
        d = doc["year_boundary"]["deferred_at_year_end"]
        self.assertEqual(d[0]["deferred"], 40.0)
        text = "\n".join(render_text(doc))
        self.assertIn("EDGE CASES — tax year 2025", text)
        self.assertIn("Superficial-loss window edges", text)

    def test_registered_losses_not_reported(self):
        work = self.root / "work"
        (work / "rrsp_gains.json").write_text(json.dumps({"transactions": [
            _gain("rrsp", "2025-06-02", "2025-06-03", "QQQ.US", 1, -99.0)]}))
        doc = analyze(self.root, self.CFG)
        self.assertFalse(any(r["symbol"] == "QQQ.US"
                             for r in doc["window_edges"]))

    def test_cli(self):
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'tax_date = "settle"\n\n[accounts.margin]\ntype = "taxable"\n\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')
        env = {"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"}
        r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                            "-C", str(self.root), "edge-cases", "--json"],
                           capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["year"], 2025)
        self.assertIn("window_edges", doc)


if __name__ == "__main__":
    unittest.main()
