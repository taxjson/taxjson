"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
import _paths
from _support.tt_cash_project import _tt_project


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


def _gains(td, entries, name="margin_gains.json", **summary):
    p = Path(td) / name
    p.write_text(json.dumps({"transactions": entries,
                             "summary": summary}))
    return p


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()


@rule(R.CA_RPT_10)
class TestCarryoverAgainstLocks(unittest.TestCase):
    """S047-21, S048-20: a locked year the ledger disagrees with is
    flagged (and a moving option-timing default is warned about).

    Formerly: test_fix_filing_b.TestCarryoverAgainstLocks"""

    def test_disagreeing_lock_is_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                t.replace("option_grant_timing_since = 2025\n", ""))
            # A written option: the unset key is warned about only when
            # the books hold one (a project with none needs no key).
            with (root / "inputs" / "margin" / "m.tt").open("a") as f:
                f.write("BUYSELL 2025-04-01 10:00:00 ZZZ250919C00050000.TO "
                        "-1 CAD 2.00 200.00 0.00\n")
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            (root / "filed").mkdir()
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"schema_version": 1, "year": 2025,
                 "totals": {"realized": 999.0}, "accounts": {}}))
            r = _cli(root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("differs from the filed lock", r.stderr)
        self.assertIn("option_grant_timing_since is not set", r.stderr)
        row = next(x for x in json.loads(r.stdout)["rows"]
                   if x["year"] == 2025)
        self.assertTrue(row["differs_from_filed"])


# ------------------------------------------------------------- T1135 walk
def _tx(action, date, symbol, qty, net, settle=None, time="10:00:00",
        **extra):
    r = {"action": action, "date": date, "date_settle": settle or date,
         "time": time, "symbol": symbol, "quantity": qty,
         "net_amount": net, "currency": "CAD", "account": "margin"}
    r.update(extra)
    return r


@rule(R.CA_RPT_12)
class TestT1135Walk(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestT1135Walk"""
    def _walk(self, rows, year=2025, overrides=None, **kw):
        from taxjson.bin.taxjson_t1135 import walk_costs
        return walk_costs(rows, year, overrides or {}, **kw)

    def test_assigned_put_premium_reduces_cost(self):
        """R1-202 / R1-276: ITA 49(3.1)(b) — the stock leg first in the
        file must not matter."""
        put = "QRS250718P00050000.US"
        rows = [_tx("BUYSELL", "2025-03-03", put, -1, 300.0),
                _tx("BUYSELL", "2025-07-18", "QRS.US", 100, -5000.0,
                    time="16:00:00"),
                _tx("ASSIGN", "2025-07-18", put, 1, 0.0, time="16:00:00")]
        w = self._walk(rows)
        s = w["per_symbol"]["QRS.US"]
        self.assertAlmostEqual(s["year_end_cost"], 4700.0, places=2)
        self.assertAlmostEqual(s["max_cost"], 4700.0, places=2)

    def test_exercised_call_cost_is_added(self):
        call = "QRS250718C00060000.US"
        rows = [_tx("BUYSELL", "2025-03-03", call, 1, -400.0),
                _tx("ASSIGN", "2025-07-18", call, -1, 0.0),
                _tx("BUYSELL", "2025-07-18", "QRS.US", 100, -6000.0)]
        w = self._walk(rows)
        self.assertAlmostEqual(
            w["per_symbol"]["QRS.US"]["year_end_cost"], 6400.0, places=2)

    @rule(R.CA_RPT_01, R.CA_DATE_14)
    def test_same_stamp_order_is_the_engines(self):
        """S008-05: the walk takes a same-stamp round trip in the
        engine's order — since CA-DATE-14 (audit R1-30, owner decision
        D7) the export's row order. Sell row first: a short sale covered
        at once, never property held; buy row first: 125,000 held."""
        rows = [_tx("BUYSELL", "2025-06-02", "CCC.US", -1500, 120000.0),
                _tx("BUYSELL", "2025-06-02", "CCC.US", 1500, -125000.0)]
        a = self._walk(rows)
        b = self._walk(list(reversed(rows)))
        self.assertAlmostEqual(a["max_total_cost"], 0.0, places=2)
        self.assertAlmostEqual(b["max_total_cost"], 125000.0, places=2)

    @rule(R.CA_RPT_01, R.CA_CORP_01)
    def test_split_inside_settle_lag(self):
        """S008-06: a pre-split sale settling after the split."""
        rows = [_tx("BUYSELL", "2025-01-02", "ABC.US", 1000, -100000.0),
                _tx("BUYSELL", "2025-06-10", "ABC.US", -500, 60000.0,
                    settle="2025-06-11"),
                _tx("SPLIT", "2025-06-10", "ABC.US", 2, 0.0,
                    time="20:25:00"),
                _tx("BUYSELL", "2025-07-01", "ABC.US", -1000, 70000.0)]
        w = self._walk(rows)
        self.assertAlmostEqual(
            w["per_symbol"]["ABC.US"]["year_end_cost"], 0.0, places=2)

    @rule(R.CA_RPT_02)
    def test_override_follows_a_rename(self):
        """S051-17: t1135.map keyed on the old ticker."""
        rows = [_tx("BUYSELL", "2025-01-05", "USC.TO", 1000, -150000.0),
                _tx("SPLIT", "2025-12-01", "USC.TO", 1, 0.0,
                    symbol_new="USD.TO")]
        w = self._walk(rows, year=2026, overrides={"USC.TO": "USA"})
        self.assertAlmostEqual(w["max_total_cost"], 150000.0, places=2)
        self.assertIn("USD.TO", w["per_symbol"])

    @rule(R.CA_RPT_01, R.CA_DATE_02)
    def test_trade_basis_year_end(self):
        """S052-10: sold Dec 31 (settles Jan 2) on a trade basis."""
        rows = [_tx("BUYSELL", "2025-03-03", "ZZZ.US", 1000, -120000.0),
                _tx("BUYSELL", "2025-12-31", "ZZZ.US", -1000, 119500.0,
                    settle="2026-01-02")]
        self.assertAlmostEqual(self._walk(rows)["per_symbol"]["ZZZ.US"]
                               ["year_end_cost"], 120000.0, places=2)
        self.assertAlmostEqual(self._walk(rows, tax_date="trade")
                               ["per_symbol"]["ZZZ.US"]["year_end_cost"],
                               0.0, places=2)


@rule(R.CA_RPT_02)
class TestT1135Report(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestT1135Report"""
    def test_bom_map_first_line_applies(self):
        """S008-03 / S051-18."""
        from taxjson.bin.taxjson_t1135 import load_overrides
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_bytes("﻿T1135 QRS.TO USA\nT1135 ABC.US CA\n"
                          .encode("utf-8"))
            ov = load_overrides(p)
        self.assertEqual(ov.get("QRS.TO"), "USA")

    def test_unused_override_and_deferred_wash_are_named(self):
        """S051-17 (unused key). S009-01 / S051-21 / S008-07 (denied
        superficial losses not in the cost columns) once named a gains
        file's deferral in year-only mode; the cost amounts are the
        engine's pools now (backlog TJ-0417), so none is left out."""
        from taxjson.bin.taxjson_t1135 import build_report, render_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2025-03-03", "AAA.US", 1000, -90000.0)]}))
            gains = _gains(td, [], name="margin_gains_wash.json")
            doc = json.loads(gains.read_text())
            doc["inventory"] = [{"symbol": "AAA.US", "qty": 1000,
                                 "total_cost": 120000.0,
                                 "deferred_wash": 30000.0}]
            gains.write_text(json.dumps(doc))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rep = build_report([base], [gains], 2025,
                                   {"NOPE.US": "USA"}, "CAD",
                                   full_history=False)
        self.assertEqual(rep["unused_overrides"], ["NOPE.US"])
        self.assertIn("NOPE.US", err.getvalue())
        # The gains file's inventory is not read for the cost: the
        # engine's pool holds the 90000 paid.
        self.assertEqual(rep["deferred_wash_not_in_cost"], {})
        self.assertEqual(rep["properties"][0]["year_end_cost"], 90000.0)
        text = render_report(rep)
        self.assertNotIn("NOT reliable", text)

    def test_other_user_maps_read_a_bom(self):
        """S051-18: [[distributions]] (taxjson.toml) and the ticker.map
        QUOTE / CRYPTO / EXTRACT lookups (once yf_ticker.map,
        crypto_ticker.map and the security-overrides file) keep their
        first rule behind a BOM."""
        from taxjson.lib.crypto_prices import load_symbol_overrides
        from taxjson.bin.taxjson_apply_distributions import load_rows
        from taxjson.bin.taxjson_brokerage import load_security_overrides
        from taxjson.lib.price_chain import load_yf_map
        bom = "\ufeff"
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "taxjson.toml").write_text(
                bom + '[[distributions]]\nsymbol = "ABC.TO"\n'
                'record_date = 2025-06-30\nper_share = 1.00\n',
                encoding="utf-8")
            self.assertEqual(load_rows(d / "taxjson.toml")[0][0], "ABC.TO")
            for first, check in (
                    ("QUOTE OLDCO.TO NEWCO 0.25",
                     lambda: self.assertIn("OLDCO.TO", load_yf_map([d]))),
                    ("CRYPTO MYCOIN mycoin-id",
                     lambda: self.assertIn("MYCOIN",
                                           load_symbol_overrides([d]))),
                    ("EXTRACT global x us dollar | USD | DLR.U.TO",
                     lambda: self.assertTrue(
                         load_security_overrides(d / "ticker.map")[0][0]
                         .startswith("global")))):
                (d / "ticker.map").write_text(bom + first + "\n",
                                              encoding="utf-8")
                check()
