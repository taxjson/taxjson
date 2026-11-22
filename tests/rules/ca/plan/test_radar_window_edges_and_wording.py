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

from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_tools import REPO_ROOT, _cli, _config, _row
from _style import CapturedWidth
from _support.planning_rows import _run_radar, _rows


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestSignedMoney(unittest.TestCase):
    """S053-24 / S054-06: a sale whose commission exceeds its gross has
    NEGATIVE proceeds — a loss, as the engine books it.

    Formerly: test_fixl_planning.TestSignedMoney"""

    def test_net_debit_sale_is_a_loss(self):
        # Bought for $1.00; sold for 0.50 gross less 9.99 commission.
        book = [_row("2026-08-01", "PNY.TO", 1000, 1.0),
                _row("2026-09-01", "PNY.TO", -1000, -9.49, price=0.0005),
                _row("2026-09-06", "PNY.TO", 1000, 1.0)]
        rows = _rows(book, "2026-09-20")
        self.assertEqual(rows["PNY.TO"]["category"], "VIOLATION",
                         rows["PNY.TO"]["advisory"])

    def test_net_debit_option_close_is_a_loss(self):
        opt = "XYZ261218C00050000.TO"
        book = [_row("2025-05-01", opt, 1, 0.10),
                _row("2025-05-21", opt, -1, -0.35, price=0.01),
                _row("2025-05-26", opt, 1, 0.10)]
        rows = _rows(book, "2025-05-27")
        self.assertEqual(rows[opt]["category"], "VIOLATION",
                         rows[opt]["advisory"])


class TestShortPoolAdjust(unittest.TestCase):
    """S053-22: a return of capital while SHORT is paid by the short
    seller — it lowers the short's gain (the engine's R1-157 rule).

    Formerly: test_fixl_planning.TestShortPoolAdjust"""

    def test_roc_on_a_short_makes_the_cover_a_loss(self):
        book = [_row("2026-09-01", "RT.TO", -100, 2000.0),
                _row("2026-09-10", "RT.TO", 0, 300.0, action="ADJUST"),
                _row("2026-09-15", "RT.TO", 100, 2200.0)]
        rows = _rows(book, "2026-09-20")
        self.assertEqual(rows["RT.TO"]["category"], "COOLING",
                         rows["RT.TO"]["advisory"])
        self.assertIn("500.00 CAD", rows["RT.TO"]["advisory"])


class TestCryptoDeadline(unittest.TestCase):
    """R1-240: crypto settles on its trade date — the last day to sell
    is the settle bound itself, a Sunday included; equities keep the
    T+1 walk-back.

    Formerly: test_fixl_planning.TestCryptoDeadline"""

    def _book(self, sym):
        return [_row("2026-08-01", sym, 1, 3000.0),
                _row("2026-09-18", sym, -1, 2500.0),
                _row("2026-09-20", sym, 0.5, 1300.0)]

    def test_crypto_deadline_is_the_settle_bound(self):
        rows = _rows(self._book("ETH"), "2026-09-25")
        eth = rows["ETH"]
        self.assertEqual(eth["category"], "VIOLATION")
        self.assertEqual(eth["settle_deadline"], "2026-10-18")
        self.assertEqual(eth["clears_at"], "2026-10-18")
        self.assertIn("settles the same day", eth["advisory"])
        self.assertIn("units", eth["advisory"])
        self.assertNotIn("shares", eth["advisory"])

    def test_equity_deadline_walks_back_through_t_plus_1(self):
        rows = _rows(self._book("XYZ.TO"), "2026-09-25")
        self.assertEqual(rows["XYZ.TO"]["clears_at"], "2026-10-15")
        self.assertIn("last TRADE date", rows["XYZ.TO"]["advisory"])


class TestCautionWording(unittest.TestCase):
    """S054-18: nobody holds in-window property, so any loss sale is
    clean — not only a full exit.

    Formerly: test_fixl_planning.TestCautionWording"""

    def test_caution_allows_a_partial_sale(self):
        tax = [_row("2026-03-02", "CAU.TO", 100, 2000.0)]
        lira = [_row("2026-09-12", "CAU.TO", 5, 90.0, account="lira"),
                _row("2026-09-14", "CAU.TO", -5, 88.0, account="lira")]
        rows = _rows(tax, "2026-09-29", sheltered=lira)
        adv = rows["CAU.TO"]["advisory"]
        self.assertEqual(rows["CAU.TO"]["category"], "CAUTION")
        self.assertIn("whole or partial", adv)
        self.assertNotIn("IF you exit your FULL", adv)


class TestProratedDenialText(unittest.TestCase):
    """S054-07: a rebuy denies only the rebought units' share.

    Formerly: test_fixl_planning.TestProratedDenialText"""

    def test_blocked_states_the_per_unit_denial(self):
        book = [_row("2026-06-01", "BLK.TO", 200, 4000.0),
                _row("2026-09-20", "BLK.TO", -100, 1000.0)]
        r = _rows(book, "2026-09-25")["BLK.TO"]
        self.assertEqual(r["category"], "BLOCKED")
        self.assertIn("10.00 CAD of it for each unit bought back",
                      r["advisory"])
        self.assertEqual(r["recent_loss"], 1000.0)
        self.assertEqual(r["recent_loss_qty"], 100.0)

    def test_risk_says_as_many_shares_as_it_buys(self):
        tax = [_row("2026-01-05", "RSK.TO", 100, 2000.0)]
        rrsp = [_row("2026-01-05", "RSK.TO", 1000, 20000.0,
                     account="rrsp")]
        r = _rows(tax, "2026-09-29", sheltered=rrsp)["RSK.TO"]
        self.assertEqual(r["category"], "RISK")
        self.assertIn("on as many shares as it buys", r["advisory"])


class TestScopeDisclosure(unittest.TestCase):
    """S054-22: SAFE/CLEAR covers this project's accounts only.

    Formerly: test_fixl_planning.TestScopeDisclosure"""

    @rule(R.CA_PLAN_04)
    def test_canada_radar_names_affiliated_persons(self):
        book = [_row("2026-01-05", "CLR.TO", 100, 2000.0)]
        r = _run_radar(book, "2026-09-29", json_out=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("s.251.1", r.stdout)
        self.assertIn("common-law partner", r.stdout)
        self.assertNotIn("§1091", r.stdout.split("DEFINITIONS")[1]
                         .split("Scope:")[1])
        j = json.loads(_run_radar(book, "2026-09-29").stdout)
        self.assertIn("s.251.1", j["scope_note"])

    @rule(R.US_PLAN_04)
    def test_usa_radar_names_spouse_and_controlled_corporation(self):
        book = [_row("2026-01-05", "CLR.US", 100, 2000.0, currency="USD")]
        r = _run_radar(book, "2026-09-29", country="usa", json_out=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        scope = r.stdout.split("Scope:")[1]
        self.assertIn("Pub. 550", scope)
        self.assertIn("spouse", scope)
        self.assertNotIn("s.251.1", scope)

    @rule(R.CA_PLAN_04)
    def test_buy_check_and_sell_check_state_the_scope(self):
        today = date.today()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        tt = (f"BUYSELL {d(-60)} 10:00:00 BLK.TO 200 CAD 10.00 2000.00 0.00\n"
              f"BUYSELL {d(-5)} 10:00:00 BLK.TO -100 CAD 8.00 800.00 0.00\n")
        year = (today - timedelta(days=5)).year
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                _config(year, [("margin", "taxable")]))
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "m.tt").write_text(tt)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            b = _cli(root, "buy-check", "BLK.TO")
            s = _cli(root, "sell-check", "BLK.TO", "--json")
        self.assertEqual(b.returncode, 1, b.stdout + b.stderr)
        self.assertIn("UNSAFE", b.stdout)
        # S054-07: per-unit, not "the loss"
        # Money as the advisories print it (backlog TJ-0244).
        self.assertIn("on as many units as you buy (about 2.00 CAD of "
                      "it per unit)", " ".join(b.stdout.split()))
        self.assertIn("s.251.1", b.stdout.splitlines()[-1])
        self.assertIn("s.251.1", json.loads(s.stdout)["scope_note"])


@rule(R.CA_PLAN_01)
class TestWindowEdges(unittest.TestCase):
    """S054-10 / S050-00: the edges are inclusive at day 30 and the
    Canadian window runs on SETTLE dates.

    Formerly: test_fixl_planning.TestWindowEdges"""

    def test_trigger_exactly_30_days_before_the_loss(self):
        base = [_row("2026-09-01", "EDG.TO", -50, 750.0)]
        on = _rows([_row("2026-08-02", "EDG.TO", 100, 2000.0)] + base,
                   "2026-09-05")
        self.assertEqual(on["EDG.TO"]["category"], "VIOLATION")
        off = _rows([_row("2026-08-01", "EDG.TO", 100, 2000.0)] + base,
                    "2026-09-05")
        self.assertEqual(off["EDG.TO"]["category"], "BLOCKED")

    def test_sheltered_buy_ages_out_after_day_30(self):
        # A loss sale is measured from its own settlement (backlog
        # TJ-0243): Thursday 09-24's settles Friday 09-25, 30 days after
        # 08-26; Friday 09-25's settles Monday 09-28.
        tax = [_row("2026-01-05", "SHL.TO", 100, 2000.0)]
        rrsp30 = [_row("2026-08-26", "SHL.TO", 10, 200.0, account="rrsp")]
        rrsp31 = [_row("2026-08-25", "SHL.TO", 10, 200.0, account="rrsp")]
        self.assertEqual(_rows(tax, "2026-09-24", rrsp30)["SHL.TO"]
                         ["category"], "LOCKED")
        self.assertEqual(_rows(tax, "2026-09-24", rrsp31)["SHL.TO"]
                         ["category"], "RISK")
        self.assertEqual(_rows(tax, "2026-09-25", rrsp30)["SHL.TO"]
                         ["category"], "RISK")

    def test_window_runs_on_the_settle_date(self):
        # Traded Fri 03-06, settled Mon 03-09: day 30 counts from 03-09,
        # and a loss sale from its own settlement (backlog TJ-0243):
        # Tuesday 04-07's settles 04-08, Wednesday 04-08's 04-09.
        book = [_row("2026-01-05", "STL.TO", 100, 2000.0),
                _row("2026-03-06", "STL.TO", 10, 200.0,
                     settle="2026-03-09")]
        row = _rows(book, "2026-04-07")["STL.TO"]
        self.assertEqual((row["category"], row["clears_at"]),
                         ("EXITABLE", "2026-04-08"))
        self.assertEqual(_rows(book, "2026-04-08")["STL.TO"]["category"],
                         "CLEAR")

    def test_last_loss_line_uses_the_country_window_date(self):
        from taxjson.cli.plan import _last_loss_by_class
        with tempfile.TemporaryDirectory() as tmp:
            g = Path(tmp) / "margin_gains_wash.json"
            g.write_text(json.dumps({"transactions": [
                {"symbol": "LL.TO", "qty": 10, "gain": -5.0,
                 "date": "2026-03-06", "date_settle": "2026-03-09"}]}))
            ca = _last_loss_by_class({"margin": g}, str, set(), usa=False)
            us = _last_loss_by_class({"margin": g}, str, set(), usa=True)
        self.assertEqual(ca["LL.TO"]["date"], "2026-03-09")
        self.assertEqual(us["LL.TO"]["date"], "2026-03-06")


class TestSameDayOrdering(unittest.TestCase):
    """S053-19: a loss sale at 10:00 and a rebuy at 14:00 the same day
    is a VIOLATION even when the file lists the buy first.

    Formerly: test_fixl_planning.TestSameDayOrdering"""

    def test_clock_order_not_file_order(self):
        sell = dict(_row("2026-09-10", "ORD.TO", -100, 1500.0),
                    time="10:00:00")
        buy = dict(_row("2026-09-10", "ORD.TO", 100, 500.0),
                   time="14:00:00")
        book = [_row("2026-09-01", "ORD.TO", 100, 2000.0), buy, sell]
        rows = _rows(book, "2026-09-15")
        self.assertEqual(rows["ORD.TO"]["category"], "VIOLATION",
                         rows["ORD.TO"]["advisory"])


class TestRadarExitableShelteredCount(unittest.TestCase):
    """S034-06: the EXITABLE note's pre-window sheltered share count.

    Formerly: test_fixl_planning.TestRadarExitableShelteredCount"""

    def test_sheltered_count_is_the_holding(self):
        tax = [_row("2026-01-05", "EXS.TO", 100, 2000.0),
               _row("2026-09-20", "EXS.TO", 10, 200.0)]
        tfsa = [_row("2026-01-05", "EXS.TO", 10, 200.0, account="tfsa")]
        r = _rows(tax, "2026-09-29", sheltered=tfsa)["EXS.TO"]
        self.assertEqual(r["category"], "EXITABLE")
        self.assertIn("Sheltered accounts hold 10 sh bought before the "
                      "window", r["advisory"])


# -------------------------------------------------------- safe-to-sell
def _sts(taxable, as_of, sheltered=None, taxable2=None):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "margin_base.json"
        t.write_text(json.dumps({"transactions": taxable}))
        cmd = [sys.executable, "-m", "taxjson.bin.taxjson_safe_to_sell",
               "--country", "canada", "--taxable", str(t), "--date", as_of]
        if taxable2 is not None:
            t2 = Path(tmp) / "cash_base.json"
            t2.write_text(json.dumps({"transactions": taxable2}))
            cmd += ["--taxable", str(t2)]
        if sheltered is not None:
            sp = Path(tmp) / "sheltered_base.json"
            sp.write_text(json.dumps({"transactions": sheltered}))
            cmd += ["--sheltered", str(sp)]
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                           text=True)
    assert r.returncode == 0, r.stderr
    rows = {}
    for ln in r.stdout.splitlines():
        c = ln.split()
        if len(c) >= 3 and c[1].replace(".", "").lstrip("-").isdigit():
            rows[c[0]] = (float(c[1]), c[2])
    return rows, r.stdout


class TestSafeToSellAlreadyFixed(unittest.TestCase):
    """The safe-to-sell findings closed by its rewrite as a radar view
    (R1-233); pinned here.

    Formerly: test_fixl_planning.TestSafeToSellAlreadyFixed"""

    @rule(R.CA_PLAN_01, R.CA_PLAN_04)
    def test_full_exit_is_allowed_after_a_recent_add(self):   # S049-19
        rows, out = _sts([_row("2025-08-01", "AAA.TO", 100, 2000.0),
                          _row("2025-09-18", "AAA.TO", 50, 900.0)],
                         "2025-09-29")
        self.assertEqual(rows["AAA.TO"], (150.0, "FULL-EXIT-ONLY"))
        self.assertIn("s.251.1", out)            # S054-22 scope note

    @rule(R.CA_PLAN_01)
    def test_sub_milli_crypto_lot_is_listed(self):   # S049-20
        rows, _ = _sts([_row("2026-09-20", "BTC", 0.0009, 119.70),
                        _row("2026-09-20", "ETH", 0.5, 2000.0)],
                       "2026-09-25")
        self.assertIn("BTC", rows)
        self.assertEqual(rows["BTC"][1], "FULL-EXIT-ONLY")

    @rule(R.CA_PLAN_01)
    def test_day_31_edges(self):   # S050-02
        # A loss sale is measured from its settlement (T+1, backlog
        # TJ-0243): Tuesday 04-01's settles 04-02, day 30 after 03-03.
        book = [_row("2025-03-03", "EDG.TO", 100, 2000.0)]
        self.assertEqual(_sts(book, "2025-03-03")[0]["EDG.TO"][1],
                         "FULL-EXIT-ONLY")
        self.assertEqual(_sts(book, "2025-04-01")[0]["EDG.TO"][1],
                         "FULL-EXIT-ONLY")
        self.assertEqual(_sts(book, "2025-04-02")[0]["EDG.TO"][1], "SAFE")

    @rule(R.CA_PLAN_01)
    def test_split_applies_once_per_account(self):   # S050-04
        a = [_row("2025-01-06", "XYZ.TO", 100, 1000.0),
             _row("2025-03-03", "XYZ.TO", 2.0, 0.0, action="SPLIT")]
        b = [dict(r, account="cash") for r in a]
        rows, _ = _sts(a, "2025-09-29", taxable2=b)
        self.assertEqual(rows["XYZ.TO"][0], 400.0)

    @rule(R.CA_PLAN_01)
    def test_duplicate_split_applies_once(self):   # S050-05
        book = [_row("2026-01-06", "XYZ.TO", 100, 1000.0),
                dict(_row("2026-06-01", "XYZ.TO", 2.0, 0.0,
                          action="SPLIT"), id="s1"),
                dict(_row("2026-06-01", "XYZ.TO", 2.0, 0.0,
                          action="SPLIT"), id="s2", time="11:00:00")]
        rows, _ = _sts(book, "2026-09-01")
        self.assertEqual(rows["XYZ.TO"][0], 200.0)

    @rule(R.CA_PLAN_01)
    def test_exited_sheltered_buy_does_not_lock(self):   # S050-08
        rows, _ = _sts(
            [_row("2026-01-05", "ABC.TO", 100, 2000.0),
             _row("2026-09-20", "XYZ.TO", 100, 2000.0)], "2026-09-29",
            sheltered=[_row("2026-09-18", "ABC.TO", 50, 1000.0,
                            account="tfsa"),
                       _row("2026-09-22", "ABC.TO", -50, 990.0,
                            account="tfsa")])
        self.assertEqual(rows["ABC.TO"][1], "SAFE*")
        self.assertEqual(rows["XYZ.TO"][1], "FULL-EXIT-ONLY")


if __name__ == "__main__":
    unittest.main()
