"""Planning low round (2026-09 audit): taxjson-export findings.

  R1-246 / R1-285  tv_exchange.map comes from the project root, not the cwd
  R1-291 / S030-19 an unreadable input / --base-gains / --trades /
                   --transfer-evidence fails the tool (the run keeps the
                   previous snapshot) instead of an empty snapshot at rc 0
  S030-14          a JSON input with no 'inventory' key exits non-zero
  S030-10          a .toml input with no [[holding]] array is refused
  S030-00          the dust guard keeps a small quantity that has real cost
  S030-01          the .sum HOLDINGS REPORT says it is end-of-data
  S030-02          an option's underlying resolves to the held class listing
  S030-04          holdings trades[] follow SPLIT ratios and renames
  S030-05          tied rows: trades[] follow the export's row order
  S030-06          .L/.AX listings are in neither currency split
  S030-09          futures: asset_type "future", no guessed 100 multiplier

All data is synthetic.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _export(*args, cwd=REPO_ROOT, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_export", *map(str, args)],
        cwd=cwd, capture_output=True, text=True,
        stdin=subprocess.DEVNULL if stdin is None else None, input=stdin)


def _inv(*rows):
    return {"inventory": [
        {"symbol": s, "qty": q, "total_cost": c, "currency": cur}
        for s, q, c, cur in rows]}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)

    def w(self, name, content):
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str)
                     else json.dumps(content))
        return p


class TestTvMapFromProjectRoot(_Tmp):
    """R1-246 / R1-285."""

    def _project(self):
        root = self.tmp / "proj"
        self.w("proj/tv_exchange.map", "NVO.US NYSE\n")
        g = self.w("proj/work/margin_gains.json",
                   _inv(("NVO.US", 10, 1000, "USD")))
        return root, g

    def test_run_stage_passes_the_root_map(self):
        from taxjson.bin import taxjson_run
        root, g = self._project()
        # A decoy map in the cwd must not win either.
        other = self.tmp / "elsewhere"
        other.mkdir()
        (other / "tv_exchange.map").write_text("NVO.US XETR\n")
        old = os.getcwd()
        os.chdir(other)
        try:
            taxjson_run.stage_exports([g], root / "reports")
        finally:
            os.chdir(old)
        tv = (root / "reports/exports/AAll_TV.txt").read_text().split()
        self.assertEqual(tv, ["NYSE:NVO"])

    def test_standalone_finds_the_map_next_to_work(self):
        root, g = self._project()
        r = _export("--tradingview", g, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["NYSE:NVO"])

    def test_explicit_map_that_cannot_be_read_is_refused(self):
        root, g = self._project()
        r = _export("--tradingview", "--tv-map", self.tmp / "nope.map", g)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope.map", r.stderr)


class TestUnreadableInputsFail(_Tmp):
    """R1-291 / S030-19 / S030-14: a named input that cannot be used
    stops the tool with a non-zero exit and writes no snapshot."""

    def setUp(self):
        super().setUp()
        self.good = self.w("g.json", _inv(("ABC.TO", 10, 100, "CAD")))
        self.trunc = self.w("t.json", '{"inventory": [')

    def _assert_refused(self, r, name):
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(name, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("holdings_count", r.stdout)

    def test_positional_inputs(self):
        for mode in ("--holdings-toml", "--report", "--tradingview"):
            self._assert_refused(_export(mode, self.good, self.trunc), "t.json")
            self._assert_refused(
                _export(mode, self.tmp / "nosuch.json"), "nosuch.json")

    def test_base_gains_trades_and_evidence(self):
        for flag in ("--base-gains", "--trades", "--transfer-evidence"):
            r = _export("--holdings-toml", "--map", self.w("m.map", ""),
                        flag, self.trunc, self.good)
            self._assert_refused(r, "t.json")

    def test_bad_toml(self):
        bad = self.w("h.toml", "[[holding]\n")
        self._assert_refused(_export("--report", bad), "h.toml")

    def test_json_without_inventory(self):
        nokey = self.w("n.json", {"transactions": []})
        for mode in ("--report", "--seekingalpha"):
            self._assert_refused(_export(mode, nokey), "n.json")
            self._assert_refused(_export(mode, self.good, nokey), "n.json")

    def test_run_keeps_prior_snapshot(self):
        from taxjson.bin.taxjson_run import run_to_file, _cmd
        out = self.tmp / "margin_holdings.toml"
        out.write_text("prior snapshot\n")
        with self.assertRaises(subprocess.CalledProcessError):
            run_to_file(_cmd("taxjson-export") + ["--holdings-toml",
                                                  str(self.trunc)],
                        out, capture_diag=False)
        self.assertEqual(out.read_text(), "prior snapshot\n")


class TestTomlInputShape(_Tmp):
    """S030-10."""

    def test_wrong_table_name_refused(self):
        p = self.w("h.toml", '[[holdings]]\nsymbol = "ABC.TO"\nquantity = 1\n')
        r = _export("--tradingview", p)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("holding", r.stderr)
        self.assertIn("holdings", r.stderr)     # names what it found

    def test_empty_snapshot_is_valid(self):
        empty = self.w("e.toml", 'schema_version = "1.2"\n\n[meta]\n'
                                 'holdings_count = 0\n')
        r = _export("--report", empty)
        self.assertEqual(r.returncode, 0, r.stderr)
        good = self.w("h.toml", '[[holding]]\nsymbol = "ABC.TO"\n'
                                'quantity = 1.0\ntotal_cost = 5.0\n')
        r = _export("--tradingview", good)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["TSX:ABC"]))


class TestDust(_Tmp):
    """S030-00: 0.0009 BTC that cost 120 is a holding, not dust."""

    def test_small_quantity_with_cost_is_kept(self):
        g = self.w("g.json", _inv(("BTC", 0.0009, 120.0, "CAD"),
                                  ("ETH", 0.5, 2000.0, "CAD"),
                                  ("XRP", 6.4e-5, 0.0, "CAD"),
                                  ("AUX.TO", -3.3e-5, 0.0007, "CAD")))
        r = _export("--report", g)
        self.assertEqual(r.returncode, 0, r.stderr)
        syms = [ln.split()[0] for ln in r.stdout.splitlines()
                if ln[:3] in ("BTC", "ETH", "XRP", "AUX")]
        self.assertEqual(syms, ["BTC", "ETH"])
        r = _export("--holdings-toml", g)
        doc = tomllib.loads(r.stdout)
        self.assertEqual([h["symbol"] for h in doc["holding"]],
                         ["BTC", "ETH"])


class TestReportAsOf(_Tmp):
    """S030-01: the holdings block says it is end-of-data, not year-end."""

    def test_title_names_end_of_data_and_the_year(self):
        doc = _inv(("ABC.TO", 10, 100, "CAD"))
        doc["summary"] = {"year": "2025"}
        r = _export("--report", self.w("g.json", doc))
        title = [ln for ln in r.stdout.splitlines() if "HOLDINGS REPORT" in ln]
        self.assertEqual(len(title), 1)
        self.assertIn("end of the data", title[0])
        self.assertIn("not 2025-12-31", title[0])


class TestOptionUnderlying(_Tmp):
    """S030-02: RCI option root -> the held RCI.B.TO listing."""

    def test_class_share_underlying(self):
        g = self.w("g.json", _inv(("RCI.B.TO", 100, 5000, "CAD"),
                                  ("RCI270115C00046000.TO", -1, -300, "CAD"),
                                  ("BCE260116C00030000.TO", 1, 50, "CAD")))
        doc = tomllib.loads(_export("--holdings-toml", g).stdout)
        und = {h["symbol"]: h.get("underlying") for h in doc["holding"]}
        self.assertEqual(und["RCI270115C00046000.TO"], "RCI.B.TO")
        # No held stock line: the root spelling stays.
        self.assertEqual(und["BCE260116C00030000.TO"], "BCE.TO")


class TestTradesFollowCorporateActions(_Tmp):
    """S030-04 and S030-05."""

    def _trades(self, rows, inventory):
        g = self.w("g.json", {"inventory": inventory})
        raw = self.w("raw.json", {"transactions": rows})
        r = _export("--holdings-toml", "--trades", raw, g)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {h["symbol"]: [(str(t["date"]), t["action"], t["qty"])
                              for t in h.get("trades", [])]
                for h in tomllib.loads(r.stdout)["holding"]}

    @staticmethod
    def _t(d, sym, action, q, price=10.0, t="10:00:00", new=""):
        return {"date": d, "date_settle": d, "time": t, "symbol": sym,
                "action": action, "quantity": q, "price": price,
                "symbol_new": new}

    def test_split_and_rename(self):
        rows = [self._t("2025-01-06", "SPL.TO", "BUYSELL", 100),
                self._t("2025-02-03", "SPL.TO", "SPLIT", 2),
                self._t("2025-03-03", "SPL.TO", "BUYSELL", -200),
                self._t("2025-04-01", "SPL.TO", "BUYSELL", 50),
                self._t("2025-01-06", "OLD.TO", "BUYSELL", 100),
                self._t("2025-02-10", "OLD.TO", "SPLIT", 1, new="NEW.TO")]
        got = self._trades(rows, [
            {"symbol": "SPL.TO", "qty": 50, "total_cost": 500,
             "currency": "CAD"},
            {"symbol": "NEW.TO", "qty": 100, "total_cost": 1000,
             "currency": "CAD"}])
        self.assertEqual(got["SPL.TO"], [("2025-04-01", "BUY", 50.0)])
        self.assertEqual(got["NEW.TO"], [("2025-01-06", "BUY", 100.0)])

    def test_tied_rows_follow_row_order(self):
        # The engines replay tied trades in the export's row order
        # (CA-DATE-14 / US-DATE-13); the trade list does the same.
        buy = self._t("2025-01-06", "XYZ.TO", "BUYSELL", 100)
        sell = self._t("2025-02-03", "XYZ.TO", "BUYSELL", -100)
        buy2 = self._t("2025-02-03", "XYZ.TO", "BUYSELL", 50)
        inv = [{"symbol": "XYZ.TO", "qty": 50, "total_cost": 500,
                "currency": "CAD"}]
        self.assertEqual(self._trades([buy, sell, buy2], inv)["XYZ.TO"],
                         [("2025-02-03", "BUY", 50.0)])
        self.assertEqual(self._trades([buy, buy2, sell], inv)["XYZ.TO"],
                         [("2025-01-06", "BUY", 100.0),
                          ("2025-02-03", "BUY", 50.0),
                          ("2025-02-03", "SELL", 100.0)])


class TestCurrencySplit(_Tmp):
    """S030-06: an overseas listing is in neither currency split."""

    def test_foreign_listing_in_neither_split(self):
        g = self.w("g.json", _inv(("AAPL.US", 1, 1, "USD"),
                                  ("XIU.TO", 1, 1, "CAD"),
                                  ("VOD.L", 1, 1, "GBP"),
                                  ("NST.AX", 1, 1, "AUD")))
        usd = _export("--seekingalpha", "--no-cad", g).stdout.strip()
        cad = _export("--seekingalpha", "--no-usd", g).stdout.strip()
        self.assertEqual(usd, "AAPL")
        self.assertEqual(cad, "XIU:CA")
        both = _export("--seekingalpha", g).stdout
        self.assertIn("VOD", both)


class TestFuturesHandoff(_Tmp):
    """S030-09."""

    def test_future_and_future_option(self):
        g = self.w("g.json", _inv(("F:MBTM6.US", 1, 38128.27, "USD"),
                                  ("F:CL260114P00053000.US", 10, 9543.6,
                                   "USD"),
                                  ("AAPL260116C00200000.US", 1, 500, "USD")))
        r = _export("--holdings-toml", "--futures", g)
        self.assertEqual(r.returncode, 0, r.stderr)
        h = {x["symbol"]: x for x in tomllib.loads(r.stdout)["holding"]}
        self.assertEqual(h["F:MBTM6.US"]["asset_type"], "future")
        fo = h["F:CL260114P00053000.US"]
        self.assertEqual(fo["asset_type"], "option")
        self.assertNotIn("contract_multiplier", fo)
        self.assertEqual(h["AAPL260116C00200000.US"]["contract_multiplier"],
                         100)
        rep = _export("--report", "--futures", g).stdout
        row = [ln for ln in rep.splitlines() if ln.startswith("F:CL")][0]
        self.assertIn("954.3600", row)       # per contract, not /100


if __name__ == "__main__":
    unittest.main()
