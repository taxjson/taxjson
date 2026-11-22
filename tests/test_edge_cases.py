"""`tjs edge-cases` (lib/edge_cases): year-boundary and superficial-
loss-window edge reporting, on a synthetic project's work files."""
import json
import subprocess
import unittest

from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.edge_cases import analyze, render_text
import _paths
from _support.ca_edge_work_files import _gain, _EdgeProject

SRC = _paths.REPO / "src"


class EdgeCaseProject(_EdgeProject):
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

    def test_no_basis_flip_claim(self):
        # The window is counted on the engine's fixed dates (settle in
        # Canada) whatever tax_date says, so the other date never
        # "decides" a verdict (audit A2-0135): no flag, no claim.
        cfg = json.loads(json.dumps(self.CFG))
        doc = analyze(self.root, cfg, margin=5)
        items = [i for r in doc["window_edges"] for i in r["items"]]
        self.assertTrue(items)
        self.assertFalse(any("basis_flip" in i for i in items))
        self.assertNotIn("DECIDES", "\n".join(render_text(doc)))

    def test_call_in_window_is_advisory(self):
        doc = analyze(self.root, self.CFG)
        cw = doc["calls_in_windows"]
        self.assertEqual([r["symbol"] for r in cw], ["ABC.TO"])
        it = cw[0]["items"][0]
        self.assertEqual(it["option"], "ABC260116C00010000.TO")
        self.assertIn("still held on day 30, so it backs a denial", it["why"])

    def test_deferred_and_text(self):
        doc = analyze(self.root, self.CFG)
        d = doc["year_boundary"]["deferred_at_year_end"]
        self.assertEqual(d[0]["deferred"], 40.0)
        text = "\n".join(render_text(doc))
        self.assertIn("EDGE CASES — tax year 2025", text)
        self.assertIn("SUPERFICIAL-LOSS WINDOW EDGES", text)

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
        # A child Python without the cwd on sys.path (AGENTS.md).
        r = subprocess.run(python_module_argv(
                               "taxjson.bin.taxjson_run",
                               ["-C", str(self.root), "edge-cases", "--json"]),
                           capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["year"], 2025)
        self.assertIn("window_edges", doc)


if __name__ == "__main__":
    unittest.main()
