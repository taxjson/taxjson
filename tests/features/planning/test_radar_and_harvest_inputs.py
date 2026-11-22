"""Planning tools, low audit round (2026-09): the wash radar and the
checks built on it.

  S049-22 / S053-16 / S079-11  radar books go through the canonical row
          funnel: a bad row stops with one line, a bare-array book loads
  S053-24 / S054-06  signed sale proceeds (commission above gross)
  S053-22 a return of capital on a SHORT pool (engine R1-157 rule)
  R1-240  a crypto rescue deadline is the settle bound (same-day
          settlement, weekends included); crypto quantities are units
  S054-18 CAUTION: any size of loss sale is clean
  S054-07 BLOCKED / RISK / buy-check state the per-unit denial
  S054-22 verdicts disclose they cover the project's accounts only
          (CA-PLAN-04 / US-PLAN-04)
  S054-10 the ±30-day trigger edge and the sheltered day-30 age-out
  S053-19 same-day loss sale and rebuy follow the clock, not file order
  S050-00 settle-date window edges (radar / safe-to-sell, last-loss line)

All data is synthetic.

Formerly: test_fixl_planning
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _support.planning_tools import REPO_ROOT, _row
from _style import CapturedWidth
from _support.planning_rows import _run_radar


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestRadarInputFunnel(unittest.TestCase):
    """S049-22, S053-16, S079-11.

    Formerly: test_fixl_planning.TestRadarInputFunnel"""

    def test_non_numeric_quantity_is_one_line(self):
        bad = [_row("2026-01-05", "ZZZ.TO", 100, 1000.0)]
        bad[0]["quantity"] = "abc"
        r = _run_radar(bad, "2026-09-29")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("non-numeric quantity", r.stderr)
        self.assertIn("margin_base.json", r.stderr)

    def test_row_without_date_is_refused_not_skipped(self):
        book = [_row("2026-08-01", "ZZZ.TO", 100, 2000.0),
                _row("2026-09-08", "ZZZ.TO", -100, 1500.0),
                _row("2026-09-10", "ZZZ.TO", 100, 1500.0)]
        del book[2]["date"]
        r = _run_radar(book, "2026-09-15")
        self.assertEqual(r.returncode, 2)
        self.assertIn("required field date is missing", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_bare_array_book_loads(self):
        book = [_row("2026-01-05", "ZZZ.TO", 100, 1000.0)]
        r = _run_radar(None, "2026-09-29", raw_taxable=json.dumps(book))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_truncated_book_is_one_line(self):
        r = _run_radar(None, "2026-09-29",
                       raw_taxable='{"transactions": [')
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not valid JSON", r.stderr)

    def test_safe_to_sell_passes_the_refusal_through(self):
        bad = [_row("2026-01-05", "ZZZ.TO", 100, 1000.0)]
        bad[0]["quantity"] = "abc"
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.json"
            t.write_text(json.dumps({"transactions": bad}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_safe_to_sell",
                 "--country", "canada", "--taxable", str(t)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)


# ------------------------------------------------------------- harvest
def _harvest(argv, fetchers=None):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from taxjson.bin.taxjson_harvest import main as harvest_main
    from _support.planning_harvest import _fake_fetcher
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = harvest_main(argv, fetchers=fetchers or [_fake_fetcher])
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def _inv(*rows):
    return {"summary": {"year": 2026}, "transactions": [],
            "inventory": [dict(symbol=s, qty=q, total_cost=c,
                               position_start_date="2026-01-15",
                               last_acq_date="2026-01-15", **kw)
                          for s, q, c, kw in rows]}


class TestHarvestInputs(unittest.TestCase):
    """S033-22, S034-08, S051-11/S079-11 (harvest): an unreadable named
    input stops the tool; S034-12: a native-currency book is refused.

    Formerly: test_fixl_planning.TestHarvestInputs"""

    def _write(self, td, name, content):
        p = Path(td) / name
        p.write_text(content if isinstance(content, str)
                     else json.dumps(content))
        return p

    def test_missing_gains_file_is_an_error_not_no_positions(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = _harvest([str(Path(td) / "margin_gains.json"),
                                     "--no-ibkr", "--country", "canada"])
        self.assertEqual(rc, 2)
        self.assertNotIn("No open positions", out)
        self.assertIn("margin_gains.json", err)

    def test_truncated_sheltered_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._write(td, "margin_gains_wash.json",
                            _inv(("AAA.TO", 100, 1240.0, {})))
            sh = self._write(td, "tfsa_gains_wash.json", '{"inventory": [')
            rc, out, err = _harvest([str(g), "--sheltered", str(sh),
                                     "--no-ibkr", "--country", "canada"])
        self.assertEqual(rc, 2)
        self.assertIn("tfsa_gains_wash.json", err)
        self.assertNotIn("Traceback", err)

    def test_wrong_shape_and_bare_array_are_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            for content in ('[{"date": 5}]', '{"inventory": 5}',
                            b"\xe9".decode("latin-1")):
                g = self._write(td, "margin_gains.json", content)
                rc, _out, err = _harvest([str(g), "--no-ibkr",
                                          "--country", "canada"])
                self.assertEqual(rc, 2, content)
                self.assertNotIn("Traceback", err)

    def test_native_currency_book_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._write(td, "margin_raw_gains.json",
                            _inv(("BBB.US", 10, 1500.0,
                                  {"currency": "USD"})))
            rc, out, err = _harvest([str(g), "--no-ibkr",
                                     "--country", "canada"])
        self.assertEqual(rc, 2)
        self.assertIn("USD", err)
        self.assertNotIn("GAIN", out)

    def test_base_currency_rows_are_fine(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._write(td, "margin_gains_wash.json",
                            _inv(("AAA.TO", 100, 1240.0,
                                  {"currency": "CAD"})))
            rc, out, err = _harvest([str(g), "--no-ibkr",
                                     "--country", "canada"])
        self.assertEqual(rc, 0, err)
        self.assertIn("AAA.TO", out)


class TestHarvestTotalsAndMap(unittest.TestCase):
    """Formerly: test_fixl_planning.TestHarvestTotalsAndMap"""
    def test_total_pct_uses_gross_capital(self):   # S034-05
        prices = {"HOLD.TO": 18.0, "OPENSH.TO": 33.0}

        def fetch(rem):
            return {s: (prices[s], "fake") for s in rem if s in prices}
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains_wash.json"
            g.write_text(json.dumps(_inv(("HOLD.TO", 100, 2000.0, {}),
                                         ("OPENSH.TO", -100, -3000.0, {}))))
            rc, out, err = _harvest([str(g), "--no-ibkr",
                                     "--country", "canada"], [fetch])
            rcj, outj, _ = _harvest([str(g), "--no-ibkr", "--json",
                                     "--country", "canada"], [fetch])
        self.assertEqual(rc, 0, err)
        total = next(ln for ln in out.splitlines()
                     if ln.startswith("TOTAL"))
        self.assertIn("-10.0%", total)
        self.assertNotIn("-50.0%", total)
        self.assertEqual(json.loads(outj)["totals"]["gross_cost"], 5000.0)

    def test_project_yf_map_found_from_another_cwd(self):   # R1-246
        asked = []

        def fetch(rem):
            asked.extend(rem.values())
            return {s: (1.0, "fake") for s in rem}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "ticker.map").write_text("QUOTE PNG.TO PNG.V\n")
            g = root / "work" / "margin_gains_wash.json"
            g.write_text(json.dumps(_inv(("PNG.TO", 100, 200.0, {}))))
            rc, _out, err = _harvest([str(g), "--no-ibkr",
                                      "--country", "canada"], [fetch])
        self.assertEqual(rc, 0, err)
        self.assertIn("PNG.V", asked)


class TestHarvestEdges(unittest.TestCase):
    """S033-23: the 7/14/30-day buckets are inclusive and a lock that
    clears today reads 'cleared'; S034-06: the RISK line is printed.

    Formerly: test_fixl_planning.TestHarvestEdges"""

    def test_buckets_at_exactly_7_14_30_days(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        t = date(2026, 9, 1)

        def row(days):
            return {"verdict": "LOSS", "unrealized": -100.0,
                    "radar": {"category": "COOLING",
                              "clears_at": (t + timedelta(days=days))
                              .isoformat()}}
        for days, bucket, nxt in ((7, "7d", "now"), (14, "14d", "7d"),
                                  (30, "30d", "14d"), (31, "later", "30d")):
            sch = _recovery_schedule([row(days)], today=t)
            self.assertEqual(sch[bucket], 100.0, (days, sch))
            self.assertEqual(sch[nxt], 0.0, (days, sch))

    def test_lock_clearing_today_reads_cleared(self):
        from taxjson.bin.taxjson_harvest import _advisory_display
        t = date(2026, 9, 1)
        rec = {"category": "LOCKED", "clears_at": t.isoformat()}
        self.assertEqual(_advisory_display(rec, today=t),
                         "LOCKED(cleared:2026-09-01)")

    def test_risk_line_is_printed_and_country_worded(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains_wash.json"
            g.write_text(json.dumps(_inv(("AAA.TO", 100, 1240.0, {}))))
            r = Path(td) / "wash_radar_margin.json"
            r.write_text(json.dumps({"sections": [{"rows": [{
                "ticker": "AAA.TO", "category": "RISK",
                "advisory": "RISK: ...", "clears_at": None}]}]}))
            rc, out, err = _harvest([str(g), "--radar", str(r),
                                     "--no-ibkr", "--country", "canada"])
        self.assertEqual(rc, 0, err)
        self.assertIn("RISK rows (155.00) count as claimable now", out)
        self.assertIn("pause DRIPs/sheltered adds", out)
        self.assertIn("s.251.1", out)


class TestHarvestCountry(unittest.TestCase):
    """Formerly: test_fixl_planning.TestHarvestCountry"""
    def test_unknown_country_is_refused(self):   # S034-01 (already fixed)
        rc, _out, err = _harvest(["x_gains.json", "--country",
                                  "united states"])
        self.assertEqual(rc, 2)
        self.assertIn("--country must be", err)


if __name__ == "__main__":
    unittest.main()
