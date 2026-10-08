"""Re-audit-2 fixes, corp-actions-02 helper list (views, corporate
timeline ordering, run --fast fingerprint, elect --redo, manifest
migration). Synthetic data only."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.corp_views import (ViewError, render_spinoffs, render_splits,
                                    spinoffs, splits)
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _proj(rows, elections=None, sheltered=False, country="canada",
          manifest_text=None):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "work").mkdir()
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "work" / "m_base.json").write_text(json.dumps(rows))
    (root / "inputs" / "m" / "manifest.json").write_text(
        manifest_text if manifest_text is not None
        else json.dumps({"elections": elections or {}}))
    cfg = {"settings": {"base_currency": "USD" if country == "usa"
                        else "CAD", "country": country},
           "accounts": {"m": {"type": "sheltered" if sheltered
                              else "taxable"}}}
    return td, root, cfg


def _row(action, date, sym, qty, net=0.0, **kw):
    d = {"action": action, "date": date, "time": "09:30:00", "symbol": sym,
         "quantity": qty, "net_amount": net}
    d.update(kw)
    return d


EV = "20250415-parn-spnc-1dc1"
DESC = "Spinoff PARN.US→SPNC.US (x)"


def _spin_rows(net):
    return [_row("BUYSELL", "2025-01-02", "PARN.US", 100, 5000),
            _row("DIVIDEND", "2025-04-15", "SPNC.US", 0, net,
                 corp_event_id=EV, description=DESC),
            _row("BUYSELL", "2025-04-15", "SPNC.US", 10, net,
                 corp_event_id=EV, description=DESC)]


class TestSpinoffsViewUs(unittest.TestCase):
    """A2-0063 / A2-0221: the view knew only the Canadian keys."""

    @rule("US-CORP-06")
    def test_a2_0063_us_301_zero_value_flagged(self):
        # No value saved (a fmv_per_share=0 written by the user is a
        # declared $0, not a flag: test_fix_quietdeclared).
        td, root, cfg = _proj(_spin_rows(0.0), {EV: {
            "election": "taxable_distribution_301",
            "hints": {}}}, country="usa")
        with td:
            doc = spinoffs(root, cfg)
        s = doc["spinoffs"][0]
        self.assertEqual(s["flags"], ["ZERO-VALUE"])
        text = "\n".join(render_spinoffs(doc))
        self.assertIn("§301", text)
        self.assertIn("=taxable_distribution_301 --hint fmv_per_share", text)
        self.assertNotIn("Every taxable spin-off is booked", text)

    @rule("US-CORP-07")
    def test_a2_0221_us_355_no_allocation_flagged(self):
        rows = [_row("BUYSELL", "2025-01-02", "PARN.US", 100, 5000),
                _row("BUYSELL", "2025-04-15", "SPNC.US", 10, 0.0,
                     corp_event_id=EV, description=DESC)]
        for hints in ({}, {"allocated_acb": 0.0}):
            td, root, cfg = _proj(rows, {EV: {
                "election": "tax_free_355", "hints": hints}}, country="usa")
            with td:
                doc = spinoffs(root, cfg)
            s = doc["spinoffs"][0]
            self.assertEqual(s["flags"], ["NO-ALLOCATION"], hints)
            text = "\n".join(render_spinoffs(doc))
            self.assertIn("§355", text)
            self.assertIn("allocated_acb=<amount>", text)
            self.assertNotIn("allocated_acb_cad", text)
            self.assertNotIn("s.86.1", text)

    @rule("US-CORP-07")
    def test_us_355_with_allocation_clean_and_stale_detected(self):
        rows = [_row("BUYSELL", "2025-04-15", "SPNC.US", 10, 400.0,
                     corp_event_id=EV, description=DESC)]
        td, root, cfg = _proj(rows, {
            EV: {"election": "tax_free_355",
                 "hints": {"allocated_acb": 400.0}},
            "20240101-old-gone-0000": {"election":
                                       "taxable_distribution_301"}},
            country="usa")
        with td:
            doc = spinoffs(root, cfg)
        self.assertEqual(doc["spinoffs"][0]["flags"], [])
        # the real US key is recognised as a spin-off election when stale
        self.assertEqual([s["event_id"] for s in doc["stale"]],
                         ["20240101-old-gone-0000"])

    @rule("US-CORP-06")
    def test_us_ira_wording(self):
        td, root, cfg = _proj(_spin_rows(0.0), {EV: {
            "election": "taxable_distribution_301",
            "hints": {"fmv_per_share": 0.0}}}, country="usa",
            sheltered=True)
        with td:
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertEqual(s["flags"], [])
        self.assertIn("tax-advantaged account (IRA): no tax effect",
                      s["why"])
        self.assertNotIn("registered account: no tax effect", s["why"])

    @rule("CA-CORP-06")
    def test_canada_wording_unchanged(self):
        td, root, cfg = _proj(_spin_rows(0.0), {EV: {
            "election": "rollover_s_86_1", "hints": {}}}, sheltered=True)
        with td:
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertIn("registered account: no tax effect", s["why"])
        self.assertIn("NO-ALLOCATION", s["flags"])
        self.assertTrue(any("allocated_acb_cad" in w for w in s["why"]))


class TestViewsRefuseUnreadable(unittest.TestCase):
    """A2-0968 / A2-0969: unreadable inputs read as 'nothing here'."""

    def test_a2_0968_wrong_shape_manifest_one_line_error(self):
        for text in ("[1, 2]", '"x"', '{"elections": [1]}', '{"elec'):
            td, root, cfg = _proj(_spin_rows(10.0), manifest_text=text)
            with td, self.assertRaises(ViewError) as cm:
                spinoffs(root, cfg)
            self.assertIn("manifest", str(cm.exception), text)

    def test_a2_0968_unreadable_manifest_refused(self):
        td, root, cfg = _proj(_spin_rows(10.0))
        with td:
            p = root / "inputs" / "m" / "manifest.json"
            p.unlink()
            p.mkdir()                       # a directory, not a file
            with self.assertRaises(ViewError):
                spinoffs(root, cfg)
            p.rmdir()
            p.write_bytes(b'{"elections": {"\xff": {}}}')   # not UTF-8
            with self.assertRaises(ViewError):
                spinoffs(root, cfg)

    def test_missing_manifest_is_no_elections(self):
        td, root, cfg = _proj(_spin_rows(10.0))
        with td:
            (root / "inputs" / "m" / "manifest.json").unlink()
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertEqual(s["flags"], ["PENDING"])   # booked, no record

    def test_a2_0969_truncated_base_refused(self):
        rows = [_row("BUYSELL", "2025-01-02", "XYZ.TO", 100, 1000),
                _row("SPLIT", "2025-03-03", "XYZ.TO", 2.0)]
        td, root, cfg = _proj(rows)
        with td:
            self.assertEqual(len(splits(root, cfg)), 1)
            base = root / "work" / "m_base.json"
            base.write_text(base.read_text()[:20])
            with self.assertRaises(ViewError) as cm:
                splits(root, cfg)
            self.assertIn("m_base.json", str(cm.exception))
            base.unlink()                   # never built: just empty
            self.assertIn("No splits in the books.",
                          render_splits(splits(root, cfg)))

    def test_a2_0979_corrupt_manifest_advice(self):
        from taxjson.lib.corp_actions import Manifest, ManifestError
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "manifest.json"
            p.write_text('{"elections": {"a": ')
            with self.assertRaises(ManifestError) as cm:
                Manifest.load(p)
        msg = str(cm.exception)
        self.assertNotIn("--manifest", msg)
        self.assertNotIn("start fresh", msg)
        self.assertIn("restore", msg)
        self.assertIn("Do not delete", msg)


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(root, *args, env=None):
    import subprocess
    import sys
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _ttx(action, date, time, sym, qty, price=0.0, settle="", **kw):
    from taxjson.lib.core import TaxTransaction
    return TaxTransaction(action=action, date=date, time=time,
                          date_settle=settle, symbol=sym, quantity=qty,
                          price=price, net_amount=-qty * price,
                          currency="CAD", account="margin", **kw)


class TestSameSettleTradeOrder(unittest.TestCase):
    """A2-0067: two rows settling the same day were applied by clock
    time, so a Monday buy went before the previous Friday's sale."""

    def _rows(self, buy_time):
        # Columbus Day 2025-10-13 (Fed holiday, NYSE open): the Friday
        # sale and the Monday buy both settle 2025-10-14.
        return [_ttx("BUYSELL", "2025-09-02", "10:00:00", "XYZ.US", 100, 10,
                     settle="2025-09-03"),
                _ttx("BUYSELL", "2025-10-10", "15:00:00", "XYZ.US", -50, 12,
                     settle="2025-10-14"),
                _ttx("BUYSELL", "2025-10-13", buy_time, "XYZ.US", 100, 20,
                     settle="2025-10-14")]

    @rule("CA-DATE-14")
    def test_friday_sale_before_monday_buy(self):
        import contextlib
        import io
        from taxjson.lib.core import CanadaTaxRules
        for buy_time in ("09:45:00", "16:00:00"):
            rows = self._rows(buy_time)
            for order in (rows, [rows[0], rows[2], rows[1]]):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    res = CanadaTaxRules().compute_gains(
                        list(order), option_premium_timing="grant")
                sale = [e for e in res["transactions"] if "proceeds" in e]
                self.assertEqual(len(sale), 1)
                self.assertAlmostEqual(sale[0]["gain"], 100.0, places=2)
                self.assertAlmostEqual(sale[0]["disallowed_amount"], 0.0)
                inv = {i["symbol"]: (i["qty"], i["total_cost"])
                       for i in res["inventory"]}
                self.assertEqual(inv["XYZ.US"], (150.0, 2500.0))
                self.assertNotIn("negative days_held", err.getvalue())

    def test_sort_key_trade_date_before_clock(self):
        from taxjson.lib.corporate_timeline import event_sort_key
        sale, buy = self._rows("09:45:00")[1:]
        for prof in ("ca_main", "ca_balance"):
            self.assertLess(event_sort_key(sale, profile=prof),
                            event_sort_key(buy, profile=prof))
        # an opening balance still leads its settle day's pre-existing rows
        ob = _ttx("OPENING_BALANCE", "2025-10-14", "00:00:00", "XYZ.US", 5)
        self.assertLess(event_sort_key(ob, profile="ca_main"),
                        event_sort_key(sale, profile="ca_main"))


QT_H = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
        "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
        "Account #,Activity Type,Account Type\n")
QT_SPLIT = QT_H + (
    "2025-01-10 12:00:00 AM,2025-01-13 12:00:00 AM,Buy,XYZ.TO,XYZ CORP,300,"
    "10.00,-3000.00,-4.95,-3004.95,CAD,55500001,Trades,Individual margin\n"
    "2025-03-10 12:00:00 AM,2025-03-10 12:00:00 AM,DIS,XYZ.TO,XYZ CORP STK "
    "SPLIT ON 300 SHS REC 03/05/25 PAY 03/10/25,400,0.00,0.00,0.00,0.00,CAD,"
    "55500001,Dividends,Individual margin\n"
    "2025-06-10 12:00:00 AM,2025-06-11 12:00:00 AM,Sell,XYZ.TO,XYZ CORP,-700,"
    "5.00,3500.00,-4.95,3495.05,CAD,55500001,Trades,Individual margin\n"
)  # pii-ok
CONFIG_CA = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""


class TestRoundedSplitCopy(unittest.TestCase):
    """A2-0070: a manual .tt SPLIT repeating a broker split with a
    rounded ratio was applied twice, silently."""

    def test_split_seen_near_ratio(self):
        import contextlib
        import io
        from taxjson.lib.corporate_timeline import split_seen
        seen = set()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(split_seen(seen, "XYZ.TO", "2025-03-10",
                                         2.333333333, ""))
            self.assertEqual(split_seen(seen, "XYZ.TO", "2025-03-07",
                                        2.333333, ""), "2025-03-10")
            self.assertEqual(split_seen(seen, "XYZ.TO", "2025-03-10",
                                        2.33333333, ""), "2025-03-10")
            # a genuinely different ratio, or outside the window, is not
            self.assertIsNone(split_seen(seen, "XYZ.TO", "2025-03-10",
                                         2.34, ""))
            self.assertIsNone(split_seen(seen, "XYZ.TO", "2025-04-30",
                                         2.333333, ""))
            # per-account keys stay per account
            self.assertIsNone(split_seen(seen, "XYZ.TO", "2025-03-10",
                                         2.333333333, "", account="a"))
            self.assertIsNone(split_seen(seen, "XYZ.TO", "2025-03-10",
                                         2.333333, "", account="b"))
        self.assertIn("applied ONCE", err.getvalue())

    @rule("CA-CORP-01")
    def test_run_applies_rounded_copy_once_and_says_so(self):
        for date, ratio in (("2025-03-10", "2.333333"),
                            ("2025-03-07", "2.333333"),
                            ("2025-03-07", "2.33333333")):
            with self.subTest(date=date, ratio=ratio), \
                    tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "taxjson.toml").write_text(CONFIG_CA)
                acct = root / "inputs" / "margin"
                acct.mkdir(parents=True)
                (acct / "questrade_2025.csv").write_text(QT_SPLIT)
                (acct / "manual.tt").write_text(
                    f"SPLIT {date} 09:30:00 XYZ.TO XYZ.TO {ratio}\n")
                r = _run_cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                self.assertIn("ATTENTION: split:", r.stdout + r.stderr)
                g = json.loads((root / "work" / "margin_gains.json")
                               .read_text())
                self.assertAlmostEqual(g["summary"]["total_gain"], 490.10,
                                       places=2)
                self.assertFalse([i for i in g.get("inventory", [])
                                  if i["symbol"] == "XYZ.TO"
                                  and abs(i["qty"]) > 1e-6])

    def test_merge2_same_date_rounded_is_attention_not_conflict(self):
        import contextlib
        import io
        from taxjson.bin.taxjson_merge2 import warn_duplicate_splits
        a = _ttx("SPLIT", "2025-03-10", "00:00:00", "XYZ.TO", 2.333333333)
        b = _ttx("SPLIT", "2025-03-10", "09:30:00", "XYZ.TO", 2.333333)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(warn_duplicate_splits([a, b]), 1)
        self.assertIn("warning: ATTENTION: split:", err.getvalue())
        self.assertNotIn("EACH is applied", err.getvalue())


class TestLineageSameDayChain(unittest.TestCase):
    """A2-0983: lineage_factor walked a same-day rename chain in input
    order."""

    def test_order_independent(self):
        from taxjson.lib.corporate_timeline import SplitTimeline
        ab = _ttx("SPLIT", "2025-03-10", "00:00:00", "AAA.TO", 2.0,
                  symbol_new="BBB.TO")
        bc = _ttx("SPLIT", "2025-03-10", "00:00:00", "BBB.TO", 3.0,
                  symbol_new="CCC.TO")
        got = set()
        for order in ([ab, bc], [bc, ab]):
            tl = SplitTimeline.from_transactions(order)
            got.add(tl.lineage_factor("BBB.TO", "2025-03-01",
                                      "AAA.TO", "2025-03-01"))
            self.assertEqual(tl.factor("CCC.TO", "2025-03-01",
                                       "2025-03-31"), 6.0)
        self.assertEqual(got, {0.5})

    @rule("CA-SL-01")
    def test_engine_allowed_loss_order_independent(self):
        import contextlib
        import io
        from taxjson.lib.core import CanadaTaxRules
        base = [_ttx("BUYSELL", "2025-01-02", "10:00:00", "AAA.TO", 100, 20,
                     settle="2025-01-03"),
                _ttx("BUYSELL", "2025-03-03", "10:00:00", "AAA.TO", -100, 10,
                     settle="2025-03-04"),
                _ttx("BUYSELL", "2025-03-05", "10:00:00", "BBB.TO", 30, 5,
                     settle="2025-03-06")]
        ab = _ttx("SPLIT", "2025-03-10", "00:00:00", "AAA.TO", 2.0,
                  symbol_new="BBB.TO")
        bc = _ttx("SPLIT", "2025-03-10", "00:00:00", "BBB.TO", 3.0,
                  symbol_new="CCC.TO")
        gains = set()
        for sp in ([ab, bc], [bc, ab]):
            with contextlib.redirect_stderr(io.StringIO()):
                res = CanadaTaxRules().compute_gains(
                    base + sp, option_premium_timing="grant")
            gains.add(round(sum(e["gain"] for e in res["transactions"]
                                if "proceeds" in e), 2))
        self.assertEqual(len(gains), 1, gains)


class TestFastFingerprintCoversDecisions(unittest.TestCase):
    """A2-0224 / A2-0985 / A2-1229: run --fast's content fingerprint left
    out the elections manifest, sends.json and taxjson.toml, so a copy
    restored with an older mtime kept the previous books at exit 0."""

    @rule("CA-CORP-06")
    def test_manifest_restored_with_old_mtime(self):
        from test_fix_corp import _gains, _ib_spinoff_project, _pending
        with tempfile.TemporaryDirectory() as tmp:
            root = _ib_spinoff_project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 3)
            eid = _pending(root)[0]["event_id"]
            e = _run_cli(root, "elect", "margin", "--set",
                         f"{eid}=rollover_s_86_1", "--hint",
                         "allocated_acb_cad=1000")
            self.assertEqual(e.returncode, 0, e.stderr)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_gains(root)[1]["SPNCO.TO"], (20, 1000.0))
            man = root / "inputs" / "margin" / "manifest.json"
            doc = json.loads(man.read_text())
            doc["elections"][eid]["hints"]["allocated_acb_cad"] = 2500.0
            old = man.stat().st_mtime - 86400
            man.write_text(json.dumps(doc))
            os.utime(man, (old, old))
            r = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_gains(root)[1]["SPNCO.TO"], (20, 2500.0))

    def test_config_and_sends_in_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(CONFIG_CA)
            acct = root / "inputs" / "margin"
            acct.mkdir(parents=True)
            (acct / "questrade_2025.csv").write_text(QT_SPLIT)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            fp = root / "work" / "margin_inputs.fingerprint"
            names = [ln.split()[0] for ln in fp.read_text().splitlines()]
            self.assertIn("taxjson.toml", names)
            gains = root / "work" / "margin_gains.json"
            # unchanged inputs: --fast keeps the cached books
            t0 = gains.stat().st_mtime_ns
            self.assertEqual(_run_cli(root, "run", "--fast", "--no-input")
                             .returncode, 0)
            self.assertEqual(gains.stat().st_mtime_ns, t0)
            # a setting edited in a file carrying an OLDER mtime rebuilds
            toml = root / "taxjson.toml"
            old = toml.stat().st_mtime - 86400
            toml.write_text(CONFIG_CA.replace(
                "option_grant_timing_since = 2025",
                "option_grant_timing_since = 2024"))
            os.utime(toml, (old, old))
            self.assertEqual(_run_cli(root, "run", "--fast", "--no-input")
                             .returncode, 0)
            self.assertNotEqual(gains.stat().st_mtime_ns, t0)
            self.assertIn("option_grant_timing_since = 2024", toml.read_text())
            # a sends.json (crypto send decisions) is fingerprinted too
            (acct / "sends.json").write_text("{}")
            self.assertEqual(_run_cli(root, "run", "--fast", "--no-input")
                             .returncode, 0)
            self.assertIn("sends.json", fp.read_text())


class TestRedoPassesRunFlags(unittest.TestCase):
    """A2-0981 / A2-0982: `elect --redo` ran corp-actions without the
    --rates / --base-currency / --ticker-map that `run` passes."""

    def test_redo_command_matches_run(self):
        import argparse
        import contextlib
        import io
        from unittest import mock
        import taxjson.bin.taxjson_run as R
        from test_fix_corp import _ib_spinoff_project, _pending
        with tempfile.TemporaryDirectory() as tmp:
            root = _ib_spinoff_project(tmp)
            (root / "ticker.map").write_text("GLOBAL QQQQ.TO QQQR.TO\n")
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 3)
            eid = _pending(root)[0]["event_id"]
            self.assertEqual(_run_cli(
                root, "elect", "margin", "--set",
                f"{eid}=taxable_deemed_dividend").returncode, 0)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            calls = []
            args = argparse.Namespace(dir=str(root), account="margin",
                                      redo=True, reset=False, event=None)
            with mock.patch.object(R, "run_to_file",
                                   lambda cmd, out, **kw: calls.append(cmd)), \
                    mock.patch.object(R.sys.stdin, "isatty",
                                      lambda: True), \
                    contextlib.redirect_stdout(io.StringIO()):
                R.cmd_elect(args)
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        for flag, val in (("--rates", str(root / "work" / "to_base.csv")),
                          ("--base-currency", "CAD"),
                          ("--ticker-map", str(root / "ticker.map"))):
            self.assertIn(flag, cmd)
            self.assertEqual(cmd[cmd.index(flag) + 1], val)


class TestLegacyManifestMigrationAtomic(unittest.TestCase):
    """A2-0218: the legacy work/ manifest was copied in place; a failed
    write left a truncated canonical copy that won from then on."""

    def test_failed_copy_leaves_legacy_authoritative(self):
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin.taxjson_run import _resolve_manifest
        text = json.dumps({"elections": {"e1": {
            "election": "rollover_s_86_1",
            "hints": {"allocated_acb_cad": 1234.5},
            "summary": "x" * 5000}}})
        # The copy is written through lib/safe_write; the disk fills
        # after the data went into the temp file, before the rename.
        def failing_write(fd):
            raise OSError(27, "File too large")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acct_dir, cache = root / "inputs" / "m", root / "work"
            cache.mkdir()
            (cache / "m_manifest.json").write_text(text)
            from taxjson.lib import safe_write as _sw
            with mock.patch.object(_sw.os, "fsync", failing_write), \
                    self.assertRaises(OSError):
                _resolve_manifest(acct_dir, cache, "m")
            self.assertFalse((acct_dir / "manifest.json").exists())
            self.assertEqual(list(acct_dir.iterdir()), [])
            with contextlib.redirect_stdout(io.StringIO()):
                p = _resolve_manifest(acct_dir, cache, "m")
            self.assertEqual(p.read_text(), text)


if __name__ == "__main__":
    unittest.main()
