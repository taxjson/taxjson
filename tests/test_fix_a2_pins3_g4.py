"""Re-audit-2 test pins (tests-pins lists, group G4): Schedule 3 / Form
8949 export and the filed-year lock's recompute settings.

Each test pins behaviour the code already gets right but no test held:
a mutant (named in the docstring) of the cited line survived the whole
suite. All data is synthetic.

  A2-0173 / A2-0174 / A2-0516 / A2-0882  build_schedule3: the pre-2025
      share line, every per-(line, symbol) accumulator with two rows of
      one symbol, the futures P/L split, the 0.00 line-4 default, the
      row notes and the property label.
  A2-0932  render_schedule3's general note carve-out for a permanent
      (registered-account / affiliated-person) denial.
  A2-0514 / A2-1614  build_8949 / render_8949 / filing_parts_8949 /
      filing_totals with two rows per part, the negative-disallowance
      and drift warnings, and the row order.
  A2-0511 / A2-1605  _lock_timing_flags: the lock's recorded since
      (default: the locked year) and its buy-back flag.
  A2-0864 / A2-0931  lock_settings: a lock is recomputed on the date
      basis it recorded (unit + end-to-end check-filed).
  A2-1551  recompute_year's `crypto=crypto` on a one-crypto-account
      project (close-year then check-filed is OK).
"""
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.bin.taxjson_form_export import (build_8949, build_schedule3,
                                             filing_parts_8949,
                                             filing_totals, mark_crypto,
                                             render_8949, render_schedule3)
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def ent(symbol, proceeds, cost, gain=None, disallowed=0.0, commission=0.0,
        date="2025-06-10", qty=-10, direction="LONG", **extra):
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost,
         "gain": (proceeds - cost + disallowed) if gain is None else gain,
         "disallowed_amount": disallowed, "days_held": 30, "term": None,
         "direction": direction, "commission": commission, "fee": 0.0,
         "account": "margin", "is_option": False}
    e.update(extra)
    return e


def row(rep, symbol):
    rows = [r for r in rep["rows"] if r["symbol"] == symbol]
    assert len(rows) == 1, rows
    return rows[0]


SERIES = "XYZ250620C00050000.US"


class TestSchedule3PreFormShareLine(unittest.TestCase):
    """A2-0173 m1676: schedule3_line folds only CRYPTO into the other
    properties before 2025 (`key == "crypto" and not new_form`)."""

    @rule("CA-DISP-03")
    def test_2024_share_sale_stays_on_13199_13200(self):
        share = ent("AAA.TO", 5000.0, 4900.0, date="2024-05-02")
        coin = mark_crypto([ent("BTC", 3000.0, 2000.0, date="2024-07-02",
                                account="kr1")])
        rep = build_schedule3([share] + coin, 2024)
        r = row(rep, "AAA.TO")
        self.assertEqual((r["proceeds_line"], r["gain_line"]),
                         ("13199", "13200"))
        self.assertEqual(r["line"], "")       # pre-2025: codes only
        t = rep["totals"]
        self.assertAlmostEqual(t["proceeds_13199"], 5000.0, places=2)
        self.assertAlmostEqual(t["gain_13200"], 100.0, places=2)
        # Only the crypto moved to the other properties.
        self.assertAlmostEqual(t["proceeds_15199"], 3000.0, places=2)
        self.assertAlmostEqual(t["gain_15300"], 1000.0, places=2)
        self.assertNotIn("proceeds_15200", t)


class TestSchedule3Accumulators(unittest.TestCase):
    """A2-0516 / A2-0173: every per-(line, symbol) accumulator in
    build_schedule3 summed over TWO rows of one symbol (each '+=' -> '='
    mutant keeps only the last row). Rows still foot, so only these
    cells catch it."""

    @rule("CA-DISP-01")
    def test_long_sales_sum_proceeds_outlays_units(self):
        # :502 proceeds, :503 outlays, :512 units.
        rep = build_schedule3([
            ent("XYZ.TO", 1000.0, 800.0, commission=5.0, qty=-10),
            ent("XYZ.TO", 1000.0, 800.0, commission=5.0, qty=-10,
                date="2025-07-10")], 2025)
        r = row(rep, "XYZ.TO")
        self.assertAlmostEqual(r["proceeds"], 2010.0, places=2)
        self.assertAlmostEqual(r["outlays"], 10.0, places=2)
        self.assertAlmostEqual(r["acb"], 1600.0, places=2)
        self.assertAlmostEqual(r["gain"], 400.0, places=2)
        self.assertEqual(r["units"], 20.0)
        self.assertEqual(r["dispositions"], 2)

    @rule("CA-DISP-06")
    def test_two_grant_writes_sum_gross_premium_outlays_and_units(self):
        # :488 proceeds, :489 outlays, :509 grant_units (A2-0173 m1764,
        # m1715). Each write: 199 net premium after a 1.00 commission.
        w = dict(direction="SHORT", grant=True, is_option=True, qty=-1)
        rep = build_schedule3([
            ent(SERIES, 0.0, -199.0, gain=199.0, commission=1.0, **w),
            ent(SERIES, 0.0, -299.0, gain=299.0, commission=1.0,
                date="2025-06-11", **w)], 2025)
        r = row(rep, SERIES)
        self.assertAlmostEqual(r["proceeds"], 500.0, places=2)
        self.assertAlmostEqual(r["outlays"], 2.0, places=2)
        self.assertAlmostEqual(r["acb"], 0.0, places=2)
        self.assertAlmostEqual(r["gain"], 498.0, places=2)
        self.assertEqual(r["units"], 2.0)

    @rule("CA-DISP-01")
    def test_two_short_covers_sum_the_short_sale_proceeds(self):
        # :497 (`rec["proceeds"] += _sold`): engine-signed short rows.
        s = dict(direction="SHORT", qty=10)
        rep = build_schedule3([
            ent("SHT.TO", -900.0, -1000.0, **s),
            ent("SHT.TO", -950.0, -1000.0, date="2025-07-10", **s)], 2025)
        r = row(rep, "SHT.TO")
        self.assertAlmostEqual(r["proceeds"], 2000.0, places=2)
        self.assertAlmostEqual(r["acb"], 1850.0, places=2)
        self.assertAlmostEqual(r["gain"], 150.0, places=2)
        self.assertIn("includes short position(s)", r["notes"])

    @rule("CA-DISP-08")
    def test_two_debit_writes_sum_their_outlays(self):
        # The net-DEBIT write branch (`rec["outlays"] += -_sold`):
        # commission above the premium, nothing received.
        w = dict(direction="SHORT", is_option=True, qty=1)
        rep = build_schedule3([
            ent(SERIES, 0.0, 2.0, gain=-2.0, **w),
            ent(SERIES, 0.0, 3.0, gain=-3.0, date="2025-06-11", **w)], 2025)
        r = row(rep, SERIES)
        self.assertAlmostEqual(r["proceeds"], 0.0, places=2)
        self.assertAlmostEqual(r["outlays"], 5.0, places=2)
        self.assertAlmostEqual(r["acb"], 0.0, places=2)
        self.assertAlmostEqual(r["gain"], -5.0, places=2)

    @rule("CA-DISP-06")
    def test_two_buybacks_of_grant_writes_are_not_counted_again(self):
        # :509 short_close_units: two 1-contract buy-backs of a 2-
        # contract grant write in the same year count 2 units, not 3.
        w = ent(SERIES, 0.0, -400.0, gain=400.0, direction="SHORT",
                grant=True, is_option=True, qty=-2)
        bb = dict(direction="SHORT", is_option=True, qty=1,
                  grant_closed={"2025": {"units": 1}})
        rep = build_schedule3([
            w,
            ent(SERIES, -100.0, 0.0, gain=-100.0, date="2025-06-12", **bb),
            ent(SERIES, -120.0, 0.0, gain=-120.0, date="2025-06-13", **bb),
        ], 2025)
        self.assertEqual(row(rep, SERIES)["units"], 2.0)

    @rule("CA-SL-09")
    def test_deferred_denials_sum(self):
        # `rec["denied"] +=`: two denied losses of one symbol.
        rep = build_schedule3([
            ent("DEN.TO", 700.0, 1000.0, disallowed=300.0),
            ent("DEN.TO", 800.0, 1000.0, disallowed=200.0,
                date="2025-06-12")], 2025)
        r = row(rep, "DEN.TO")
        self.assertAlmostEqual(r["denied"], 500.0, places=2)
        self.assertIn("superficial loss 500.00 denied", r["notes"])
        self.assertNotIn("PERMANENTLY", r["notes"])

    @rule("CA-SL-09")
    def test_two_permanent_denials_one_note(self):
        # A2-0173 m1687 (`perm_denied +=`): 40 + 60 permanently denied
        # is ONE 'PERMANENTLY denied' note of 100, and no deferral note
        # telling the user to add 40 to a replacement's ACB.
        rep = build_schedule3([
            ent("PRM.TO", 960.0, 1000.0, disallowed=40.0,
                permanently_disallowed=40.0),
            ent("PRM.TO", 940.0, 1000.0, disallowed=60.0,
                permanently_disallowed=60.0, date="2025-06-12")], 2025)
        r = row(rep, "PRM.TO")
        self.assertIn("superficial loss 100.00 PERMANENTLY denied",
                      r["notes"])
        self.assertNotIn("add it to the ACB of the replacement",
                         r["notes"])
        self.assertAlmostEqual(r["denied"], 100.0, places=2)


class TestSchedule3Futures(unittest.TestCase):
    """A2-0173 m1714 / A2-0174 m1580: a plain future shows its settled
    P/L (a gain as PROCEEDS, a loss as ACB), summed over closes, and a
    row without raw_gain falls back to the gain."""

    @rule("CA-DISP-03")
    def test_two_futures_closes_sum_the_pl(self):
        rep = build_schedule3([
            ent("F:CLZ5.US", 0.0, 0.0, gain=1250.0, raw_gain=1250.0),
            ent("F:CLZ5.US", 0.0, 0.0, gain=800.0, date="2025-07-10",
                raw_gain=800.0),
            ent("F:CLZ5.US", 0.0, 0.0, gain=-300.0, date="2025-08-10",
                raw_gain=-300.0)], 2025)
        r = row(rep, "F:CLZ5.US")
        self.assertAlmostEqual(r["proceeds"], 2050.0, places=2)
        self.assertAlmostEqual(r["acb"], 300.0, places=2)
        self.assertAlmostEqual(r["gain"], 1750.0, places=2)
        self.assertEqual(r["line"], "6")
        self.assertIn("settled P/L", r["notes"])

    @rule("CA-DISP-03")
    def test_future_without_raw_gain_uses_the_gain(self):
        e = ent("F:ESZ5.US", 0.0, 0.0, gain=200.0)
        e.pop("raw_gain", None)
        rep = build_schedule3([e], 2025)
        r = row(rep, "F:ESZ5.US")
        self.assertAlmostEqual(r["proceeds"], 200.0, places=2)
        self.assertAlmostEqual(r["acb"], 0.0, places=2)


class TestSchedule3DefaultsNotesProperty(unittest.TestCase):
    """A2-0174 (m1545 0.00 default, m1563 property label, m1541 -0.00)
    and A2-0882 (m2095/m2097/m2099 spurious notes on a plain row,
    m2125 a long sale netted against grant units)."""

    @rule("CA-DISP-03")
    def test_options_only_book_has_zero_line_4(self):
        rep = build_schedule3([ent(SERIES, 450.0, 100.0, is_option=True,
                                   qty=-1)], 2025)
        t = rep["totals"]
        self.assertEqual(t["proceeds_13199"], 0.0)
        self.assertEqual(t["gain_13200"], 0.0)
        self.assertAlmostEqual(t["gain_15300"], 350.0, places=2)

    @rule("CA-DISP-01")
    def test_plain_share_row_has_no_notes_and_its_own_class(self):
        rep = build_schedule3([ent("XYZ.TO", 1500.0, 1000.0)], 2025)
        r = row(rep, "XYZ.TO")
        self.assertEqual(r["notes"], "")
        self.assertEqual(r["property"], "shares")

    @rule("CA-DISP-01")
    def test_single_class_rows_are_not_mixed(self):
        # "mixed" is only for a row whose dispositions span classes.
        rep = build_schedule3([ent("ZZZ", 100.0, 50.0)], 2025)
        self.assertEqual(row(rep, "ZZZ")["property"], "shares")
        rep = build_schedule3([ent(SERIES, 100.0, 50.0, is_option=True,
                                   qty=-1)], 2025)
        self.assertEqual(row(rep, SERIES)["property"], "option")

    @rule("CA-DISP-08")
    def test_no_negative_zero_cells(self):
        # A sub-cent loss rounds to 0.00, never -0.00 (`+ 0.0`).
        rep = build_schedule3([ent("TNY.TO", 100.0, 100.001,
                                   gain=-0.001)], 2025)
        r = row(rep, "TNY.TO")
        for k in ("proceeds", "acb", "outlays", "gain"):
            self.assertEqual(math.copysign(1.0, r[k]), 1.0, (k, r[k]))
        self.assertNotIn("-0.00", render_schedule3(rep, 2025, "CAD"))

    @rule("CA-DISP-06")
    def test_long_sale_and_grant_write_in_one_series_count_both(self):
        rep = build_schedule3([
            ent(SERIES, 300.0, 100.0, is_option=True, qty=-2),
            ent(SERIES, 0.0, -150.0, gain=150.0, direction="SHORT",
                grant=True, is_option=True, qty=-1, date="2025-06-11")],
            2025)
        self.assertEqual(row(rep, SERIES)["units"], 3.0)


class TestSchedule3GeneralNote(unittest.TestCase):
    """A2-0932 (S043-02): the general note's carve-out — a registered-
    account or affiliated-person denial is NOT added to a replacement's
    ACB on this return."""

    @rule("CA-SL-09")
    def test_general_note_carves_out_permanent_denials(self):
        rep = build_schedule3([ent("XYZ.TO", 1500.0, 1000.0)], 2025)
        text = " ".join(render_schedule3(rep, 2025, "CAD").split())
        self.assertIn("added to the ACB of the replacement property "
                      "instead — except a denial caused by a "
                      "registered-account or affiliated-person "
                      "acquisition, which is permanent for this return "
                      "with no ACB addition here", text)
        self.assertIn("s.53(1)(f); noted per row", text)


# ------------------------------------------------------------- Form 8949

E8949 = [
    dict(symbol="AAA.US", date="2025-04-01", acquired_date="2025-03-01",
         term="SHORT_TERM", qty=5, proceeds=400.0, cost=500.0,
         disallowed_amount=60.0, gain=-40.0),
    dict(symbol="BBB.US", date="2025-03-03", acquired_date="2025-02-03",
         term="SHORT_TERM", qty=10, proceeds=900.0, cost=1000.0,
         disallowed_amount=100.0, gain=0.0),
]


def quiet_8949(entries):
    err = io.StringIO()
    with redirect_stderr(err):
        rep = build_8949(entries)
    return rep, err.getvalue()


class TestForm8949MultiRow(unittest.TestCase):
    """A2-0514: two code-W rows in one part (E08 total adjustment, E20
    the rendered TOTALS line, E17 the part's disposition count, E15 the
    row order)."""

    @rule("US-RPT-02")
    def test_part_totals_sum_both_rows(self):
        rep, _ = quiet_8949(E8949)
        self.assertEqual(rep["part_I_totals"],
                         {"proceeds": 1300.0, "cost": 1500.0,
                          "adjustment": 160.0, "gain": -40.0})
        self.assertEqual([r["code"] for r in rep["part_I"]], ["W", "W"])

    @rule("US-RPT-01")
    def test_rows_sorted_by_date_sold(self):
        rep, _ = quiet_8949(E8949)
        self.assertEqual([r["date_sold"] for r in rep["part_I"]],
                         ["2025-03-03", "2025-04-01"])

    @rule("US-RPT-02")
    def test_rendered_totals_line(self):
        rep, _ = quiet_8949(E8949)
        text = render_8949(rep, 2025, "USD")
        self.assertIn("TOTALS (to Schedule D part I): proceeds 1,300.00 | "
                      "cost 1,500.00 | adjustments 160.00 | gain -40.00",
                      text)

    @rule("US-RPT-01")
    def test_filing_parts_count_dispositions(self):
        err = io.StringIO()
        with redirect_stderr(err):
            parts = filing_parts_8949(E8949)
        self.assertEqual([(p["part"], p["dispositions"]) for p in parts],
                         [("I", 2)])
        self.assertEqual(parts[0]["adjustment"], 160.0)

    @rule("US-RPT-02")
    def test_negative_disallowance_warns(self):
        # A2-1614: a negative disallowed_amount is rendered without an
        # adjustment and named on stderr.
        e = dict(E8949[0], disallowed_amount=-25.0, gain=-100.0)
        rep, err = quiet_8949([e])
        self.assertIn("AAA.US 2025-04-01: NEGATIVE disallowed_amount "
                      "-25.00", err)
        self.assertIn("inspect before filing", err)
        self.assertEqual(rep["part_I"][0]["code"], "")

    @rule("US-RPT-09")
    def test_engine_drift_warns(self):
        # E07: (d)-(e)+(g) off the engine's gain by more than $0.02.
        e = dict(E8949[0], disallowed_amount=0.0, gain=-99.0)
        _rep, err = quiet_8949([e])
        self.assertIn("1 row(s) where (d)-(e)+(g) differs", err)
        _rep, err = quiet_8949([dict(e, gain=-100.0)])
        self.assertNotIn("differs", err)


class TestSchedule3FilingTotals(unittest.TestCase):
    """A2-0514 E18: filing_totals counts every disposition."""

    @rule("CA-DISP-01")
    def test_dispositions_count(self):
        es = [ent("AAA.TO", 900.0, 1000.0), ent("BBB.TO", 400.0, 500.0)]
        t = filing_totals(es, 2025)
        self.assertEqual(t["dispositions"], 2)
        self.assertAlmostEqual(t["gain"], -200.0, places=2)


# ------------------------------------------------------- filed-year lock

class TestLockTimingFlags(unittest.TestCase):
    """A2-0511 / A2-1605 (R1-188): a locked year is recomputed under the
    option timing its lock RECORDED — since defaults to the locked year
    (not the project's), and the recorded buy-back flag wins."""

    def _cmd(self, settings, option_timing, year=2025):
        from taxjson.bin.taxjson_filed import recompute_year
        seen = []

        def fake_run(cmd, outp):
            seen.append(list(cmd))
            Path(outp).write_text('{"transactions": []}')
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "m_base.json").write_text('{"transactions": []}')
            recompute_year(cache, "m", year, settings, "", fake_run,
                           option_timing=option_timing)
        return seen[0]

    PROJECT = {"country": "canada", "year": 2026,
               "option_premium_timing": "grant",
               "option_buyback_loss_superficial": False}

    @rule("CA-OPT-01")
    def test_since_defaults_to_the_locked_year(self):
        cmd = self._cmd(self.PROJECT, {"option_premium_timing": "grant"})
        self.assertEqual(cmd[cmd.index("--option-grant-since") + 1],
                         "2025")

    @rule("CA-OPT-01")
    def test_recorded_since_is_used(self):
        cmd = self._cmd(dict(self.PROJECT, option_grant_timing_since=2026),
                        {"option_premium_timing": "grant",
                         "option_grant_since": 2024})
        self.assertEqual(cmd[cmd.index("--option-grant-since") + 1],
                         "2024")

    @rule("CA-SL-11")
    def test_recorded_buyback_flag_wins_both_ways(self):
        on = {"option_premium_timing": "grant", "option_grant_since": 2025,
              "option_buyback_loss_superficial": True}
        self.assertIn("--option-buyback-wash", self._cmd(self.PROJECT, on))
        off = dict(on, option_buyback_loss_superficial=False)
        self.assertNotIn("--option-buyback-wash",
                         self._cmd(dict(self.PROJECT,
                                        option_buyback_loss_superficial=True),
                                   off))


class TestLockDateBasis(unittest.TestCase):
    """A2-0864 / A2-0931 (COMMANDS-08 date half, S032-12): a lock is
    recomputed on the date basis it RECORDED."""

    @rule("US-RPT-06")
    def test_lock_settings_takes_the_recorded_basis_us(self):
        from taxjson.bin.taxjson_filed import lock_settings
        s = lock_settings({"date_basis": "trade"},
                          {"country": "usa", "tax_date": "settle"})
        self.assertEqual(s["tax_date"], "trade")

    @rule("CA-RPT-08")
    def test_lock_settings_takes_the_recorded_basis_ca(self):
        from taxjson.bin.taxjson_filed import lock_settings
        s = lock_settings({"date_basis": "settle"},
                          {"country": "canada", "tax_date": "trade"})
        self.assertEqual(s["tax_date"], "settle")
        # No (or an unknown) recorded basis: the project's own.
        self.assertEqual(lock_settings({}, {"tax_date": "trade"})
                         ["tax_date"], "trade")

    @rule("CA-RPT-08")
    def test_check_filed_after_switching_to_trade_dates(self):
        # Closed on settle dates: the Dec 31 sale settles in January and
        # is not in the 2025 lock. After tax_date = "trade" it is a 2025
        # sale on trade dates — check-filed must still say OK.
        hdr = ("Transaction Date,Settlement Date,Action,Symbol,"
               "Description,Quantity,Price,Gross Amount,Commission,"
               "Net Amount,Currency,Account #,Activity Type,"
               "Account Type\n")
        rows = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
                "D,200,10.00,2000.00,0.00,-2000.00,CAD,1,Trades,Individual\n"
                "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,"
                "D,-100,15.00,1500.00,0.00,1500.00,CAD,1,Trades,Individual\n"
                "2025-12-31 10:15:00 AM,2026-01-02 12:00:00 AM,Sell,XEI.TO,"
                "D,-100,18.00,1800.00,0.00,1800.00,CAD,1,Trades,Individual\n")
        cfg = ('[settings]\nyear = 2025\ncountry = "canada"\n'
               'base_currency = "CAD"\nsource_currencies = []\n'
               'tax_date = "settle"\n'
               '[accounts.margin]\ntype = "taxable"\n')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                hdr + rows)
            (root / "taxjson.toml").write_text(cfg)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr)
            lock = json.loads((root / "filed" / "2025.json").read_text())
            self.assertEqual(lock["date_basis"], "settle")
            self.assertAlmostEqual(lock["totals"]["realized"], 500.0,
                                   places=2)
            (root / "taxjson.toml").write_text(
                cfg.replace('tax_date = "settle"', 'tax_date = "trade"'))
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            k = _cli(root, "check-filed")
            self.assertEqual(k.returncode, 0, k.stdout + k.stderr)
            self.assertIn("filed 2025: OK", k.stdout)


def _cli(root, *a):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *a], cwd=REPO_ROOT,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


class TestSingleCryptoAccountCheckFiled(unittest.TestCase):
    """A2-1551: check-filed recomputes a project's ONE crypto account
    with `crypto=True`, so its sales stay on line 7 (15200/15301) as
    close-year recorded them — dropping it moved them to line 4 and
    showed a false DRIFT."""

    @rule("CA-RPT-08")
    def test_close_year_then_check_filed_is_ok(self):
        hdr = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,"
               "vol,margin,misc,ledgers\n")
        body = (hdr + "TXA1,OA1,BTC/CAD,2025-01-15 10:00:00.1234,buy,"
                "limit,50000,50000,0,1.0,,,\nTXA2,OA2,BTC/CAD,2025-06-02 "
                "14:30:00.5678,sell,limit,60000,60000,0,1.0,,,\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "kr1").mkdir(parents=True)
            (root / "inputs" / "kr1" / "kr_trades.csv").write_text(body)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.kr1]\ntype = "taxable"\ncrypto = true\n')
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr)
            k = _cli(root, "check-filed")
            self.assertEqual(k.returncode, 0, k.stdout + k.stderr)
            self.assertIn("filed 2025: OK", k.stdout)


if __name__ == "__main__":
    unittest.main()
