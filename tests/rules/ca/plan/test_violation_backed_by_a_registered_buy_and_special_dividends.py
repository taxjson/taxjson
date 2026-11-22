"""Fix round 7 (wave B), 2026-10-29: harvest, watch's harvest baseline,
tips, div-estimate and fees (backlog TJ-0908, TJ-0709, TJ-0710,
TJ-0911, TJ-0912, TJ-0913, TJ-0914, TJ-0900, TJ-0897, TJ-0776).

Synthetic data only; offline throughout (injected fetchers or a cache).

Formerly: test_fix_1029_r7b_harvest
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from taxjson.bin.taxjson_harvest import main as harvest_main
from tax_rules import rule
from taxjson.rules import ids as R


def _run_harvest(argv, prices):
    """harvest_main with a fake live tier keyed by the QUOTE spelling."""
    def _fetch(remaining):
        return {s: (prices[y], "fake") for s, y in remaining.items()
                if y in prices}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=[_fetch])
    return rc, out.getvalue(), err.getvalue()


def _gains(root: Path, inventory, name="margin_gains_wash.json"):
    p = root / name
    p.write_text(json.dumps({"summary": {"year": 2026}, "transactions": [],
                             "inventory": inventory}))
    return p


def _iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


class TestRegisteredBackedViolation(unittest.TestCase):
    """TJ-0908: an OPEN VIOLATION whose replacement units a registered
    account bought in the window and still holds is not harvestable
    now: a loss sale today is superficial for those units too, and the
    denial is permanent. It waits for that buy to age out (+31 days).

    Formerly: test_fix_1029_r7b_harvest.TestRegisteredBackedViolation"""

    def _row(self, rescue, sh_add=None, sh_known=True, tq=10.0):
        return {"verdict": "LOSS", "unrealized": -100.0,
                "radar": {"category": "VIOLATION", "clears_at": _iso(19),
                          "taxable_qty": tq, "rescue": rescue},
                "sheltered_last_add": sh_add,
                "sheltered_known": sh_known}

    def test_registered_backed_waits_for_age_out(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "sheltered", "account": "registered",
              "symbol": "ZZSYN.TO", "qty": 20.0}], _iso(-5))])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["14d"], 0.0)
        self.assertEqual(s["30d"], 100.0)       # 26 days from now

    def test_partly_backed_splits(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "sheltered", "account": "registered",
              "symbol": "ZZSYN.TO", "qty": 4.0},
             {"holder": "taxable", "account": "",
              "symbol": "ZZSYN.TO", "qty": 6.0}], _iso(-5))])
        self.assertEqual(s["now"], 60.0)
        self.assertEqual(s["30d"], 100.0)

    def test_unknown_registered_side_is_no_clear(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "sheltered", "account": "registered",
              "symbol": "ZZSYN.TO", "qty": 20.0}], None, sh_known=False)])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["no_clear"], 100.0)

    def test_taxable_backed_still_now(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "taxable", "account": "", "symbol": "ZZSYN.TO",
              "qty": 10.0}], _iso(-5))])
        self.assertEqual(s["now"], 100.0)

    def test_cli_with_radar_and_sheltered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 200.0,
                               "position_start_date": _iso(-150),
                               "last_acq_date": _iso(-150)}])
            sh = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 20,
                                "total_cost": 200.0,
                                "position_start_date": _iso(-5),
                                "last_acq_date": _iso(-5),
                                "last_acq_settle": _iso(-4)}],
                        name="registered_gains_wash.json")
            radar = root / "wash_radar_margin.json"
            radar.write_text(json.dumps({"sections": [{
                "category": "VIOLATION", "rows": [{
                    "ticker": "ZZSYN.TO", "category": "VIOLATION",
                    "advisory": "VIOLATION: Sell ...",
                    "clears_at": _iso(19), "taxable_qty": 10.0,
                    "rescue": [{"holder": "sheltered",
                                "account": "registered",
                                "symbol": "ZZSYN.TO", "qty": 20.0}]}]}]}))
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--radar", str(radar), "--sheltered", str(sh)],
                {"ZZSYN.TO": 10.0})
        self.assertEqual(rc, 0, err)
        h = json.loads(out)["totals"]["harvestable"]
        self.assertEqual(h["now"], 0.0)
        self.assertEqual(h["30d"], 100.0)



class TestDivEstimateSpecialPaidLast(unittest.TestCase):
    """TJ-0900: an off-cycle special paid AFTER the regular monthly
    payments does not turn the payer quarterly: the forecast keeps the
    monthly schedule from the latest regular payment, at its amount,
    and names the special.

    Formerly: test_fix_1029_r7b_harvest.TestDivEstimateSpecialPaidLast"""

    @staticmethod
    def _rows(special=True):
        from _support.view_div_estimate import _div
        rows = [("margin", _div(f"2025-0{m}-15", "ZZD.TO", 10, 1.0))
                for m in (1, 2, 3, 4)]
        if special:
            rows.append(("margin", _div("2025-05-01", "ZZD.TO", 10, 5.0)))
        return rows

    @rule(R.CA_PLAN_06)
    def test_special_last_keeps_the_monthly_forecast(self):
        from _support.view_div_estimate import _est, _pos
        plain = _est(self._rows(False), [_pos("ZZD.TO", 10)],
                     as_of="2025-05-02", months=6, year=2025)["rows"][0]
        r = _est(self._rows(), [_pos("ZZD.TO", 10)], as_of="2025-05-02",
                 months=6, year=2025)["rows"][0]
        self.assertEqual(r["frequency"], "monthly")
        self.assertEqual(r["next_pay_date"], "2025-05-15")
        self.assertAlmostEqual(r["next_amount"], 10.0)
        self.assertAlmostEqual(r["window_amount"], plain["window_amount"])
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any("2025-05-01" in f and "special" in f
                            for f in r["flags"]), r["flags"])
        # The latest payment is still shown as the latest.
        self.assertEqual(r["last_pay_date"], "2025-05-01")

    def test_detect_schedule_names_the_latest_regular_payment(self):
        from taxjson.lib.div_estimate import detect_schedule
        ds = ["2025-01-15", "2025-02-15", "2025-03-15", "2025-04-15",
              "2025-05-01"]
        self.assertEqual(detect_schedule(ds)[0::3], ("monthly",
                                                    "2025-04-15"))
        # Regular payments after the special: anchored at the latest.
        ds = ["2025-01-15", "2025-02-15", "2025-02-27", "2025-03-15",
              "2025-04-15"]
        self.assertEqual(detect_schedule(ds)[0::3], ("monthly",
                                                    "2025-04-15"))



if __name__ == "__main__":
    unittest.main()
