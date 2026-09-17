"""taxjson harvest — unrealized gain/(loss) at current prices.

Offline throughout: prices come from an injected fake fetcher (the
price chain's `fetchers` test hook), never the network.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from taxjson.bin.taxjson_harvest import main as harvest_main

# margin: AAA.TO underwater (cost 1,240 vs value 1,085), BBB.US ahead,
# one OCC option row (must be skipped — unpriceable by the chain).
# last_acq_date is dynamic (TX_ADD renders signed days from today).
_AAA_TX_ADD = (date.today() - timedelta(days=100)).isoformat()
_BBB_TX_ADD = (date.today() - timedelta(days=400)).isoformat()
GAINS = {
    "summary": {"year": 2026},
    "transactions": [],
    "inventory": [
        {"symbol": "AAA.TO", "qty": 100, "total_cost": 1240.0,
         "position_start_date": "2026-01-15",
         "last_acq_date": _AAA_TX_ADD},
        {"symbol": "BBB.US", "qty": 50, "total_cost": 4005.0,
         "position_start_date": "2024-03-01",
         "last_acq_date": _BBB_TX_ADD},
        {"symbol": "AAA.TO260116C00010000", "qty": 2, "total_cost": 300.0},
        {"symbol": "CLOSED.TO", "qty": 0, "total_cost": 0.0},
    ],
}

PRICES = {"AAA.TO": 10.85, "BBB.US": 95.20, "ETN.US": 399.56}

USD_CAD = 1.25


def _fake_fetcher(remaining):
    return {s: (PRICES[s], "fake") for s in remaining if s in PRICES}


def _radar_doc(clears_at):
    return {"sections": [{"title": "LOCKED", "rows": [{
        "ticker": "AAA.TO", "category": "LOCKED",
        "advisory": "LOCKED: Recent buy ...", "clears_at": clears_at}]}]}


def _project(td, *, wash=True, radar_clears=None, rates=True):
    root = Path(td)
    name = "margin_gains_wash.json" if wash else "margin_gains.json"
    (root / name).write_text(json.dumps(GAINS))
    if rates:
        (root / "to_base.csv").write_text(
            f"{date.today().isoformat()} 12:00:00 USD CAD {USD_CAD}\n")
    radar = None
    if radar_clears is not None:
        radar = root / "wash_radar_margin.json"
        radar.write_text(json.dumps(_radar_doc(radar_clears)))
    return root / name, radar


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=[_fake_fetcher])
    return rc, out.getvalue(), err.getvalue()


class TestHarvest(unittest.TestCase):
    def test_losses_first_with_verdicts_and_basis(self):
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td)
            rc, out, err = _run([str(gains), "--no-ibkr",
                                 "--country", "canada"])
        self.assertEqual(rc, 0)
        self.assertIn("basis: wash-adjusted", out)
        # Loss row (AAA.TO: 1,085 - 1,240 = -155) sorts before the gain.
        self.assertLess(out.index("AAA.TO"), out.index("BBB.US"))
        self.assertIn("LOSS", out)
        self.assertIn("GAIN", out)
        self.assertIn("-155.00", out)
        self.assertIn("1,945.00", out)      # 50*95.20*1.25 - 4005 (in CAD)
        # PRICE is BASE currency (native x FX collapsed) with a source
        # mark; the CUR/FX/SRC/VALUE columns are gone.
        bbb = next(ln for ln in out.splitlines() if "BBB.US" in ln)
        self.assertIn("119.0000?", bbb)     # 95.20 USD x 1.25, 'fake' src
        self.assertNotIn("USD", bbb)
        self.assertNotIn("5,950.00", bbb)   # VALUE dropped
        aaa = next(ln for ln in out.splitlines() if "AAA.TO" in ln)
        self.assertIn("10.8500?", aaa)
        for col in ("CUR", "FX", "SRC", "VALUE"):
            self.assertNotIn(col, out.splitlines()[0])
        # Option and fully-closed rows never appear.
        self.assertNotIn("C00010000", out)
        self.assertNotIn("CLOSED.TO", out)

    def test_radar_advisory_with_view_time_countdown(self):
        clears = (date.today() + timedelta(days=12)).isoformat()
        with tempfile.TemporaryDirectory() as td:
            gains, radar = _project(td, radar_clears=clears)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada",
                               "--radar", str(radar)])
        self.assertEqual(rc, 0)
        # Absolute date AND view-time countdown, aligned with the radar
        # report's CLEARS column ("DATE (Nd)").
        self.assertIn(f"LOCKED(clears:{clears},+12d)", out)
        # The winning position carries no advisory.
        gain_row = next(ln for ln in out.splitlines() if "BBB.US" in ln)
        self.assertNotIn("LOCKED", gain_row)

    def test_symbol_filter(self):
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada",
                               "--symbol", "bbb.us"])
        self.assertEqual(rc, 0)
        self.assertIn("BBB.US", out)
        self.assertNotIn("AAA.TO", out)

    def test_multiple_symbol_filters(self):
        data = dict(GAINS)
        data["inventory"] = GAINS["inventory"] + [
            {"symbol": "CCC.TO", "qty": 10, "total_cost": 100.0}]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gains = root / "margin_gains_wash.json"
            gains.write_text(json.dumps(data))
            (root / "to_base.csv").write_text(
                f"{date.today().isoformat()} 12:00:00 USD CAD {USD_CAD}\n")
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada",
                               "--symbol", "aaa.to", "--symbol", "BBB.US"])
        self.assertEqual(rc, 0)
        self.assertIn("AAA.TO", out)
        self.assertIn("BBB.US", out)
        self.assertNotIn("CCC.TO", out)

    def test_no_positions_scope_lists_all_requested(self):
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada",
                               "--symbol", "X.TO", "--symbol", "Y.US"])
        self.assertEqual(rc, 0)
        self.assertIn("No open positions for X.TO, Y.US.", out)

    def test_usa_shows_days_to_long_term(self):
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "usa"])
        self.assertEqual(rc, 0)
        self.assertIn("LT_IN", out)
        bbb = next(ln for ln in out.splitlines() if "BBB.US" in ln)
        self.assertIn(" LT", bbb)           # held since 2024 — long-term
        aaa = next(ln for ln in out.splitlines() if "AAA.TO" in ln)
        self.assertRegex(aaa, r"\d+d")      # 2026 buy — still counting

    def test_recovery_schedule_locked_loss_lands_in_its_bucket(self):
        # AAA.TO is the only loss (155), locked for 12 more days: not
        # recoverable now or within 7d; recoverable by 14d and 30d.
        clears = (date.today() + timedelta(days=12)).isoformat()
        with tempfile.TemporaryDirectory() as td:
            gains, radar = _project(td, radar_clears=clears)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada",
                               "--radar", str(radar)])
        self.assertEqual(rc, 0)
        self.assertIn("HARVESTABLE LOSSES", out)
        line = next(ln for ln in out.splitlines()
                    if "HARVESTABLE" in ln)
        self.assertIn("now 0.00", line)
        self.assertIn("<=7d 0.00", line)
        self.assertIn("<=14d 155.00", line)
        self.assertIn("<=30d 155.00", line)
        self.assertIn("cumulative", line)

    def test_recovery_schedule_no_radar_is_unknown_not_now(self):
        # 2026-08 deep-audit: NO radar data is not the same as "no
        # lock" — a genuinely LOCKED position was summed into
        # "claimable now" whenever the sidecar was absent. Unknown
        # losses land in the no-clear-date bucket instead.
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td)              # no radar sidecar
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada"])
        self.assertEqual(rc, 0)
        line = next(ln for ln in out.splitlines()
                    if "HARVESTABLE" in ln)
        self.assertNotIn("now 155.00", line)
        self.assertIn("155.00 with no clear date", line)

    def test_recovery_schedule_in_json_totals(self):
        clears = (date.today() + timedelta(days=5)).isoformat()
        with tempfile.TemporaryDirectory() as td:
            gains, radar = _project(td, radar_clears=clears)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada", "--json",
                               "--radar", str(radar)])
        self.assertEqual(rc, 0)
        h = json.loads(out)["totals"]["harvestable"]
        self.assertEqual(h["now"], 0.0)
        self.assertEqual(h["7d"], 155.0)         # clears in 5d
        self.assertEqual(h["30d"], 155.0)        # cumulative
        self.assertEqual(h["no_clear"], 0.0)

    def test_no_losses_no_schedule_line(self):
        # Only a winning position: the schedule line stays out.
        winner = {"summary": {"year": 2026}, "transactions": [],
                  "inventory": [
                      {"symbol": "BBB.US", "qty": 50, "total_cost": 4005.0,
                       "position_start_date": "2024-03-01",
                       "last_acq_date": _BBB_TX_ADD}]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gains = root / "margin_gains_wash.json"
            gains.write_text(json.dumps(winner))
            (root / "to_base.csv").write_text(
                f"{date.today().isoformat()} 12:00:00 USD CAD {USD_CAD}\n")
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada"])
        self.assertEqual(rc, 0)
        self.assertNotIn("HARVESTABLE LOSSES", out)

    def test_stale_taxable_file_dashes_tx_add_and_warns(self):
        # A taxable gains file predating last_acq_date: TX_ADD shows '-'
        # (never an approximation) and the warning names the column.
        old = {"summary": {"year": 2026}, "transactions": [], "inventory": [
            {"symbol": "AAA.TO", "qty": 100, "total_cost": 1240.0,
             "position_start_date": "2026-01-15"}]}
        with tempfile.TemporaryDirectory() as td:
            gains = Path(td) / "margin_gains_wash.json"
            gains.write_text(json.dumps(old))
            rc, out, err = _run([str(gains), "--no-ibkr",
                                 "--country", "canada"])
        self.assertEqual(rc, 0)
        aaa = next(ln for ln in out.splitlines() if "AAA.TO" in ln)
        self.assertRegex(aaa, r"LOSS\s+-\s")    # TX_ADD dash after VERDICT
        self.assertIn("TX_ADD shows '-'", err)
        self.assertIn("re-run", err)

    def test_json_output_parseable(self):
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["totals"]["positions"], 2)
        self.assertAlmostEqual(doc["totals"]["unrealized"], 1790.0)
        bbb = next(r for r in doc["rows"] if r["symbol"] == "BBB.US")
        self.assertEqual(bbb["price_currency"], "USD")
        self.assertAlmostEqual(bbb["fx_rate"], USD_CAD)
        self.assertEqual(doc["rows"][0]["verdict"], "LOSS")

    def test_unpriced_symbol_warned_and_omitted(self):
        data = dict(GAINS)
        data["inventory"] = GAINS["inventory"] + [
            {"symbol": "NOPRICE.TO", "qty": 5, "total_cost": 50.0}]
        with tempfile.TemporaryDirectory() as td:
            gains = Path(td) / "margin_gains.json"
            gains.write_text(json.dumps(data))
            rc, out, err = _run([str(gains), "--no-ibkr",
                                 "--country", "canada"])
        self.assertEqual(rc, 0)
        self.assertIn("NOPRICE.TO", err)
        self.assertIn("warning:", err)
        self.assertNotIn("NOPRICE.TO", out)

    def test_no_positions_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            gains = Path(td) / "margin_gains.json"
            gains.write_text(json.dumps({"inventory": []}))
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada"])
        self.assertEqual(rc, 0)
        self.assertIn("No open positions.", out)

    def test_pre_wash_basis_labelled(self):
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td, wash=False)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada"])
        self.assertEqual(rc, 0)
        self.assertIn("basis: pre-wash", out)


class TestShelteredInfo(unittest.TestCase):
    """--sheltered FILEs add SH_QTY (shares held in sheltered accounts)
    and SH_ADD (days since the sheltered side last acquired — the
    permanent-denial signal for the 30-day window)."""

    @staticmethod
    def _sheltered_doc(qty=25, last_acq_days_ago=10, *, with_last_acq=True,
                       start_days_ago=400):
        row = {"symbol": "AAA.TO", "qty": qty, "total_cost": 300.0,
               "position_start_date":
                   (date.today() - timedelta(days=start_days_ago))
                   .isoformat()}
        if with_last_acq:
            row["last_acq_date"] = (
                date.today() - timedelta(days=last_acq_days_ago)
            ).isoformat()
        return {"summary": {"year": 2026}, "transactions": [],
                "inventory": [row]}

    def _run_with_sheltered(self, td, *sheltered_docs):
        root = Path(td)
        gains, _ = _project(td)
        argv = [str(gains), "--no-ibkr", "--country", "canada"]
        for i, doc in enumerate(sheltered_docs):
            f = root / f"shel{i}_gains.json"
            f.write_text(json.dumps(doc))
            argv += ["--sheltered", str(f)]
        return _run(argv)

    def test_sheltered_columns_qty_and_days(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out, _ = self._run_with_sheltered(td, self._sheltered_doc())
        self.assertEqual(rc, 0)
        self.assertIn("SH_QTY", out)
        self.assertIn("SH_ADD", out)
        self.assertIn("TX_ADD", out)
        aaa = next(ln for ln in out.splitlines() if "AAA.TO" in ln)
        self.assertIn(" 25 ", aaa)
        sh_date = (date.today() - timedelta(days=10)).isoformat()
        self.assertIn(f"{sh_date}(-10d)", aaa)          # SH_ADD
        self.assertIn(f"{_AAA_TX_ADD}(-100d)", aaa)     # TX_ADD
        # BBB.US isn't held sheltered: dash in SH_QTY (right after
        # QTY) and a dash SH_ADD, while TX_ADD still shows its own add.
        bbb = next(ln for ln in out.splitlines() if "BBB.US" in ln)
        self.assertRegex(bbb, r"BBB\.US\s+50\s+-\s")
        self.assertIn(f"{_BBB_TX_ADD}(-400d)", bbb)
        self.assertRegex(bbb, r"\(-400d\)\s+-\s+-\s*$")  # SH_ADD/ADVISORY
        # The banner explains the 30-day permanent-denial stake.
        self.assertIn("PERMANENTLY denied", out)

    def test_stale_file_shows_dash_and_warns_never_approximates(self):
        # Pre-field gains files have no last_acq_date. NO fallback to
        # position_start_date: the position may have opened long before
        # its latest add, so a fallback could only overstate the age —
        # "34d, clear of the window" when the truth was "13d,
        # permanently denied" (real user report). Dash + warning instead.
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = self._run_with_sheltered(
                td, self._sheltered_doc(with_last_acq=False,
                                        start_days_ago=40))
        self.assertEqual(rc, 0)
        aaa = next(ln for ln in out.splitlines() if "AAA.TO" in ln)
        self.assertNotIn("(-40d)", aaa)         # the trap this test pins
        self.assertIn(" 25 ", aaa)              # SH_QTY still shown
        self.assertIn("predate the last_acq_date field", err)
        self.assertIn("SH_QTY/SH_ADD", err)
        self.assertIn("re-run", err)

    def test_aggregates_across_sheltered_accounts(self):
        # qty sums; the MOST RECENT add wins (that's the binding window).
        with tempfile.TemporaryDirectory() as td:
            rc, out, _ = self._run_with_sheltered(
                td,
                self._sheltered_doc(qty=25, last_acq_days_ago=60),
                self._sheltered_doc(qty=10, last_acq_days_ago=5))
        self.assertEqual(rc, 0)
        aaa = next(ln for ln in out.splitlines() if "AAA.TO" in ln)
        self.assertIn(" 35 ", aaa)
        newest = (date.today() - timedelta(days=5)).isoformat()
        self.assertIn(f"{newest}(-5d)", aaa)
        self.assertNotIn("(-60d)", aaa)

    def test_no_sheltered_flag_keeps_old_shape(self):
        with tempfile.TemporaryDirectory() as td:
            gains, _ = _project(td)
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada"])
        self.assertEqual(rc, 0)
        self.assertNotIn("SH_QTY", out)
        self.assertNotIn("SH_ADD", out)
        self.assertIn("TX_ADD", out)            # taxable side always shows

    def test_json_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gains, _ = _project(td)
            f = root / "rrsp_gains.json"
            f.write_text(json.dumps(self._sheltered_doc()))
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada", "--json",
                               "--sheltered", str(f)])
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        aaa = next(r for r in doc["rows"] if r["symbol"] == "AAA.TO")
        self.assertEqual(aaa["sheltered_qty"], 25)
        self.assertEqual(aaa["sheltered_last_add_days"], -10)   # signed
        self.assertEqual(aaa["taxable_last_add_days"], -100)
        self.assertEqual(aaa["taxable_last_add"], _AAA_TX_ADD)
        bbb = next(r for r in doc["rows"] if r["symbol"] == "BBB.US")
        self.assertEqual(bbb["sheltered_qty"], 0.0)
        self.assertIsNone(bbb["sheltered_last_add_days"])
        self.assertEqual(bbb["taxable_last_add_days"], -400)


class TestOptionsMode(unittest.TestCase):
    """--options: OCC positions priced ONLY via the IBKR tier (injected
    here through the option_fetchers hook), x100 multiplier, per-share
    COST/SH, DTE column; unpriced contracts warn about IBKR."""

    # 2 contracts, 300 CAD book (1.50/sh premium), ~200 days to expiry.
    _EXP = (date.today() + timedelta(days=200)).strftime("%y%m%d")
    OPT = f"XEQT.TO{_EXP}C00030000"

    def _project(self, td, *, opt_qty=2):
        root = Path(td)
        gains = root / "margin_gains_wash.json"
        gains.write_text(json.dumps({"inventory": [
            {"symbol": "AAA.TO", "qty": 100, "total_cost": 1240.0,
             "last_acq_date": _AAA_TX_ADD},
            {"symbol": self.OPT, "qty": opt_qty, "total_cost": 300.0,
             "position_start_date": "2026-01-15"},
            {"symbol": f"F:CL{self._EXP}P00053000.US", "qty": 1,
             "total_cost": 100.0},              # futures option: never
        ]}))
        (root / "to_base.csv").write_text(
            f"{date.today().isoformat()} 12:00:00 USD CAD {USD_CAD}\n")
        return gains

    @staticmethod
    def _run_opt(argv, option_prices):
        def _opt_fetcher(remaining):
            return {s: (option_prices[s], "ibkr") for s in remaining
                    if s in option_prices}
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = harvest_main(argv, fetchers=[_fake_fetcher],
                              option_fetchers=[_opt_fetcher])
        return rc, out.getvalue(), err.getvalue()

    def test_option_priced_with_multiplier_and_dte(self):
        with tempfile.TemporaryDirectory() as td:
            gains = self._project(td)
            rc, out, err = self._run_opt(
                [str(gains), "--no-ibkr", "--country", "canada",
                 "--options"],
                {self.OPT: 2.10})
        self.assertEqual(rc, 0, err)
        self.assertIn("DTE", out)
        row = next(ln for ln in out.splitlines() if self.OPT in ln)
        # value = 2 x 2.10 x 100 = 420; unrealized = +120 on 300 book.
        self.assertIn("120.00", row)
        self.assertIn("GAIN", row)
        self.assertIn("1.5000", row)            # COST/SH per-share terms
        self.assertIn("2.1000^", row)           # premium, ibkr mark
        self.assertIn("200d", row)              # DTE
        self.assertIn("per-share premium", out)  # legend
        # The futures option never appears, even with --options.
        self.assertNotIn("F:CL", out)

    def test_options_excluded_without_flag(self):
        with tempfile.TemporaryDirectory() as td:
            gains = self._project(td)
            rc, out, _ = self._run_opt(
                [str(gains), "--no-ibkr", "--country", "canada"],
                {self.OPT: 2.10})
        self.assertEqual(rc, 0)
        self.assertNotIn(self.OPT, out)
        self.assertNotIn("DTE", out)

    def test_unpriced_option_warns_about_ibkr(self):
        with tempfile.TemporaryDirectory() as td:
            gains = self._project(td)
            rc, out, err = self._run_opt(
                [str(gains), "--no-ibkr", "--country", "canada",
                 "--options"],
                {})                              # no option quotes at all
        self.assertEqual(rc, 0)
        self.assertIn("option position(s) unpriced", err)
        self.assertIn("IBKR", err)
        self.assertNotIn(self.OPT, out)          # omitted, not faked
        self.assertIn("AAA.TO", out)             # stocks unaffected

    def test_option_loss_joins_recovery_schedule(self):
        with tempfile.TemporaryDirectory() as td:
            gains = self._project(td)
            rc, out, _ = self._run_opt(
                [str(gains), "--no-ibkr", "--country", "canada",
                 "--options"],
                {self.OPT: 1.00})                # 200 value vs 300 book
        self.assertEqual(rc, 0)
        line = next(ln for ln in out.splitlines() if "HARVESTABLE" in ln)
        # option loss 100 + AAA.TO loss 155 — no radar sidecar, so both
        # are UNKNOWN (no-clear bucket), not "now" (2026-08 deep-audit).
        self.assertIn("255.00 with no clear date", line)


class TestHarvestWrapperCrypto(unittest.TestCase):
    """`taxjson harvest` wrapper: crypto = true accounts are excluded by
    default (--crypto opts in). Offline: an empty inventory makes the
    tool return before any price resolution."""

    def _project(self, tmp, *, crypto_only=False):
        root = Path(tmp)
        (root / "work").mkdir()
        acct = "" if crypto_only else (
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n' + acct +
            '[accounts.btc]\ntype = "taxable"\ncrypto = true\n')
        empty = json.dumps({"inventory": []})
        if not crypto_only:
            (root / "work" / "margin_gains.json").write_text(empty)
        (root / "work" / "btc_gains.json").write_text(empty)
        return root

    @staticmethod
    def _wrap(root, *args):
        import subprocess, sys as _sys
        return subprocess.run(
            [_sys.executable, "-m", "taxjson.bin.taxjson_run",
             "-C", str(root), "harvest", "--no-ibkr", *args],
            capture_output=True, text=True)

    def test_crypto_account_excluded_by_default_with_note(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._wrap(self._project(td))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("crypto account(s) excluded: btc", r.stderr)
        self.assertIn("--crypto", r.stderr)

    def test_crypto_flag_includes_them(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._wrap(self._project(td), "--crypto")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("excluded", r.stderr)

    def test_all_crypto_project_needs_the_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, crypto_only=True)
            r = self._wrap(root)
            r2 = self._wrap(root, "--crypto")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pass --crypto", r.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)


class TestFxConversion(unittest.TestCase):
    """Regression for the reported ETN.US bug: base-currency (CAD) book
    cost was compared against the raw USD quote, overstating the loss by
    the full FX factor. Non-base quotes must convert via --rates, and
    with no usable rate the row is OMITTED loudly, never mixed in raw."""

    ETN = {"summary": {"year": 2026}, "transactions": [], "inventory": [
        {"symbol": "ETN.US", "qty": 40, "total_cost": 22200.47,
         "position_start_date": "2026-05-01"}]}

    def test_usd_quote_converts_to_base(self):
        with tempfile.TemporaryDirectory() as td:
            gains = Path(td) / "margin_gains_wash.json"
            gains.write_text(json.dumps(self.ETN))
            rates = Path(td) / "to_base.csv"
            rates.write_text(f"{date.today().isoformat()} 12:00:00 "
                             f"USD CAD 1.37\n")
            rc, out, _ = _run([str(gains), "--no-ibkr",
                               "--country", "canada",
                               "--rates", str(rates)])
        self.assertEqual(rc, 0)
        # 40 * 399.56 * 1.37 = 21,895.89 CAD -> loss -304.58, NOT the
        # -6,218 the unconverted USD comparison produced.
        self.assertIn("-304.58", out)
        self.assertNotIn("-6,218", out)

    def test_no_rate_omits_row_with_warning(self):
        with tempfile.TemporaryDirectory() as td:
            gains = Path(td) / "margin_gains_wash.json"
            gains.write_text(json.dumps(self.ETN))
            rc, out, err = _run([str(gains), "--no-ibkr",
                                 "--country", "canada"])   # no rates file
        self.assertEqual(rc, 0)
        self.assertIn("no USD->CAD rate", err)
        self.assertNotIn("ETN.US   40", out)

    def test_stale_rate_used_with_warning(self):
        with tempfile.TemporaryDirectory() as td:
            gains = Path(td) / "margin_gains_wash.json"
            gains.write_text(json.dumps(self.ETN))
            rates = Path(td) / "to_base.csv"
            old = (date.today() - timedelta(days=30)).isoformat()
            rates.write_text(f"{old} 12:00:00 USD CAD 1.37\n")
            rc, out, err = _run([str(gains), "--no-ibkr",
                                 "--country", "canada",
                                 "--rates", str(rates)])
        self.assertEqual(rc, 0)
        self.assertIn("-304.58", out)               # converted anyway
        self.assertIn("30d old", err)               # but said so




class TestBreakevenExitColumn(unittest.TestCase):
    """EXIT@: native-currency no-loss exit price (+2% buffer) at
    today's FX rate."""

    GAINS_BE = {"summary": {"year": 2026}, "transactions": [],
                "inventory": [
        {"symbol": "LOSER.US", "qty": 100.0, "total_cost": 1500.0,
         "position_start_date": "2026-01-05",
         "last_acq_date": "2026-01-05"},
        {"symbol": "CADL.TO", "qty": 100.0, "total_cost": 1000.0,
         "position_start_date": "2026-01-05",
         "last_acq_date": "2026-01-05"},
    ]}

    def _run_be(self, td, *extra):
        root = Path(td)
        gains = root / "margin_gains_wash.json"
        gains.write_text(json.dumps(self.GAINS_BE))
        (root / "to_base.csv").write_text(
            f"{date.today().isoformat()} 12:00:00 USD CAD {USD_CAD}\n")
        prices = {"LOSER.US": (10.0, "fake"), "CADL.TO": (8.0, "fake")}
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = harvest_main(
                [str(gains), "--no-ibkr", "--country", "canada", *extra],
                fetchers=[lambda remaining: {
                    s: prices[s] for s in remaining if s in prices}])
        return rc, out.getvalue(), err.getvalue()

    def test_fx_aware_breakeven_with_buffer(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out, _ = self._run_be(td)
        self.assertEqual(rc, 0)
        loser = next(ln for ln in out.splitlines() if "LOSER.US" in ln)
        # cost 1,500 CAD / (100 sh x 1.25 CAD/USD) = 12 USD; +2% = 12.24.
        self.assertIn("12.2400USD", loser)
        cadl = next(ln for ln in out.splitlines() if "CADL.TO" in ln)
        # Native CAD position: 1,000/100 x 1.02 = 10.20 CAD.
        self.assertIn("10.2000CAD", cadl)
        self.assertIn("EXIT@", out.splitlines()[
            next(i for i, ln in enumerate(out.splitlines())
                 if "ACCOUNT" in ln)])

    def test_json_carries_the_fields(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out, _ = self._run_be(td, "--json")
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        loser = next(r for r in doc["rows"]
                     if r["symbol"] == "LOSER.US")
        self.assertAlmostEqual(loser["breakeven_exit_native"], 12.24,
                               places=4)
        self.assertEqual(loser["breakeven_exit_currency"], "USD")




class TestRiskCountsAsClaimableNow(unittest.TestCase):
    """2026-08-24 fix: s.40(2)(g) needs an acquisition INSIDE the
    ±30-day window — RISK (sheltered holds, but no buys in the past
    30 days) is sellable TODAY, with a forward-window caveat. It was
    bucketed as no_clear, as if unharvestable."""

    def test_risk_lands_in_now_bucket(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        rows = [{"verdict": "LOSS", "unrealized": -1000.0,
                 "radar": {"category": "RISK", "advisory": "RISK: ...",
                           "clears_at": None}},
                {"verdict": "LOSS", "unrealized": -500.0,
                 "radar": None}]                    # unknown stays put
        out = _recovery_schedule(rows)
        self.assertAlmostEqual(out["now"], 1000.0)
        self.assertAlmostEqual(out["no_clear"], 500.0)


class TestBlockedCountsAsClaimableNow(unittest.TestCase):
    """2026-09 audit: BLOCKED = recent loss, NO in-window acquisition,
    still holding — a further loss sale TODAY is clean (the engine
    disallows nothing); only a forward REBUY is the risk. It was
    bucketed like LOCKED, by clears_at, so `watch --harvest`'s
    harvestable-now total under-reported."""

    def test_blocked_lands_in_now_bucket(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        clears = (date.today() + timedelta(days=21)).isoformat()
        rows = [{"verdict": "LOSS", "unrealized": -2297.0,
                 "radar": {"category": "BLOCKED", "advisory": "BLOCKED: ...",
                           "clears_at": clears}},
                {"verdict": "LOSS", "unrealized": -100.0,
                 "radar": {"category": "LOCKED", "advisory": "LOCKED: ...",
                           "clears_at": clears}}]
        out = _recovery_schedule(rows)
        self.assertAlmostEqual(out["now"], 2297.0)      # BLOCKED only
        self.assertAlmostEqual(out["30d"], 2397.0)      # + LOCKED later

    def test_blocked_advisory_reads_as_no_rebuy_not_no_sell(self):
        from taxjson.bin.taxjson_harvest import _advisory_display
        today = date(2026, 9, 10)
        cell = _advisory_display({"category": "BLOCKED",
                                  "clears_at": "2026-10-01"}, today)
        self.assertEqual(cell, "BLOCKED(sell-ok,no-rebuy-until:2026-10-01,+21d)")
        self.assertNotIn(" ", cell)                     # single token
        cell = _advisory_display({"category": "VIOLATION",
                                  "clears_at": "2026-09-17"}, today)
        self.assertEqual(cell, "VIOLATION(sell-by:2026-09-17,+7d)")


class TestDaysToLongTermMatchesEngine(unittest.TestCase):
    """LT_IN used a fixed 366-day offset: one day EARLY whenever the
    year after acquisition held a Feb 29 (the engine terms a
    2024-06-01 sale of a 2023-06-01 lot SHORT_TERM), and blind to the
    Rev. Rul. 66-7 end-of-month rule. It now derives the boundary from
    the engine's own `held_more_than_one_year`."""

    def test_pins(self):
        from taxjson.bin.taxjson_harvest import _days_to_long_term as f
        # acq 2023-06-01 -> LT from 2024-06-02 (not 06-01).
        self.assertEqual(f("2023-06-01", today=date(2024, 6, 1)), 1)
        self.assertEqual(f("2023-06-01", today=date(2024, 6, 2)), 0)
        # acq 2024-02-29 (end of month) -> LT from 2025-03-01.
        self.assertEqual(f("2024-02-29", today=date(2025, 2, 28)), 1)
        self.assertEqual(f("2024-02-29", today=date(2025, 3, 1)), 0)
        # Plain non-leap year: anniversary itself is still short-term.
        self.assertEqual(f("2025-03-10", today=date(2026, 3, 10)), 1)
        self.assertIsNone(f(None))
        self.assertIsNone(f("garbage"))


if __name__ == "__main__":
    unittest.main()
