"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.filing_bin_reports import _run


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

from taxjson.bin import taxjson_audit as AU  # noqa: E402


def _ttx(**kw):
    from taxjson.model.transaction import TaxTransaction
    base = dict(action="BUYSELL", date="2025-01-06", symbol="AAA.TO",
                quantity=100.0, currency="CAD", price=10.0,
                net_amount=-1000.0, account="margin",
                date_settle="2025-01-07")
    base.update(kw)
    return TaxTransaction(**base).to_dict()


class _AuditBook:
    """A CAD base book, the engine's saved gains of it, and the audit."""

    def __init__(self, td, rows, timing="close", year=2025):
        from taxjson.engine.by_country import get_tax_rules
        from taxjson.lib.tx_load import load_transactions
        self.td = Path(td)
        self.base = self.td / "margin_base.json"
        self.base.write_text(json.dumps({"transactions": rows}))
        res = get_tax_rules("canada").compute_gains(
            load_transactions(self.base), option_premium_timing=timing)
        self.records = res["transactions"]
        self.gains = self.td / "margin_gains.json"
        self.gains.write_text(json.dumps({"transactions": self.records},
                                         default=str))
        self.timing = timing
        self.year = year

    def audit(self, *extra, check=True, json_out=True):
        argv = ["--country", "canada", "--base", str(self.base),
                "--year", str(self.year), "--no-color",
                "--option-premium-timing", self.timing]
        if check:
            argv += ["--check", str(self.gains)]
        argv += list(extra)
        if json_out:
            argv.append("--json")
        rc, out, err = _run(AU.main, argv)
        return rc, (json.loads(out) if json_out and out.strip() else out), err


class TestAuditCheckFiles(unittest.TestCase):
    """Formerly: test_fix_l_filing_b.TestAuditCheckFiles"""
    def _book(self, td):
        return _AuditBook(td, [
            _ttx(),
            _ttx(quantity=-100.0, net_amount=1200.0, date="2025-03-03",
                 date_settle="2025-03-04")])

    def test_check_file_named_twice_is_read_once(self):
        # S026-01: every tie-out failed as "the books changed".
        with tempfile.TemporaryDirectory() as td:
            b = self._book(td)
            rc, doc, err = b.audit("--check", str(b.gains))
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["reconciliation_failures"], [])
        self.assertIn("given more than once", err)

    def test_id_less_saved_disposition_fails(self):
        # S026-02: a record without an id passed the reverse sweep.
        with tempfile.TemporaryDirectory() as td:
            b = self._book(td)
            recs = json.loads(b.gains.read_text())["transactions"]
            fake = dict(recs[0], symbol="ZZZ.TO", gain=8000.0)
            fake.pop("id", None)
            b.gains.write_text(json.dumps({"transactions": recs + [fake]}))
            rc, doc, err = b.audit()
        self.assertEqual(rc, 1)
        self.assertTrue(any("NO id" in f
                            for f in doc["reconciliation_failures"]))

    def test_year_scope_and_sweep_use_the_settle_date(self):
        # S026-14: a Dec-31 trade settling in January is audited (and
        # swept) in the settle year.
        with tempfile.TemporaryDirectory() as td:
            b = _AuditBook(td, [
                _ttx(date="2025-06-02", date_settle="2025-06-03"),
                _ttx(quantity=-50.0, net_amount=710.0, date="2025-12-31",
                     date_settle="2026-01-02"),
                _ttx(quantity=-50.0, net_amount=1000.0, date="2026-02-02",
                     date_settle="2026-02-03")], year=2026)
            rc, doc, err = b.audit()
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(doc["events"]), 2)
        self.assertEqual(doc["events"][0]["date"], "2025-12-31")
        self.assertAlmostEqual(doc["total_gain"], 710.0 - 500.0
                               + 1000.0 - 500.0, places=2)
        self.assertEqual(doc["reconciliation_failures"], [])
        self.assertIn("unrounded", doc["totals_note"])


class TestAuditEventLabels(unittest.TestCase):
    """Formerly: test_fix_l_filing_b.TestAuditEventLabels"""
    def test_grant_write_is_headed_write_with_the_premium(self):
        # S026-04: a sell-to-open read 'COVER ... (short)', proceeds 0.
        with tempfile.TemporaryDirectory() as td:
            b = _AuditBook(td, [
                _ttx(symbol="XYZ250620C00055000.TO", quantity=-1.0,
                     price=2.0, net_amount=200.0, date="2025-03-04",
                     date_settle="2025-03-05")], timing="grant")
            rc, doc, err = b.audit()
            self.assertEqual(rc, 0, err)
            self.assertTrue(doc["events"][0]["grant"])
            rc, text, err = b.audit("--no-trace", json_out=False)
        self.assertIn("WRITE 1 XYZ250620C00055000.TO", text)
        self.assertNotIn("COVER", text)
        self.assertRegex(text, r"proceeds\s+200\.00")
        self.assertRegex(text, r"cost basis\s+0\.00")

    def _ev(self, **kw):
        ev = {"id": "abc123", "symbol": "SHORTCO.TO", "date": "2025-03-03",
              "date_settle": "2025-03-04", "account": "margin",
              "qty": 100.0, "direction": "LONG", "proceeds": 0.0,
              "cost": 0.0, "gain": 0.0, "disallowed_amount": 0.0,
              "permanently_disallowed": 0.0, "is_wash_sale": False,
              "sources": [], "warnings": [], "failures": [],
              "replacements": [], "tie_out": {}, "fx": None}
        ev.update(kw)
        return "\n".join(AU.render_event(ev, 1, 1, "canada",
                                         show_trace=False))

    def test_short_cover_shows_the_filed_legs(self):
        # S026-08: short 100 @ 50, cover @ 40.
        text = self._ev(direction="SHORT", proceeds=-4000.0, cost=-5000.0,
                        gain=1000.0)
        self.assertRegex(text, r"proceeds\s+5,000\.00\s+\(100 sh @ 50\.0000\)")
        self.assertRegex(text,
                         r"cost basis\s+4,000\.00\s+\(100 sh @ 40\.0000\)")

    def test_option_per_share_uses_the_multiplier(self):
        # S026-05: '(1 sh @ 398.7000)' beside a source @ 3.99.
        text = self._ev(symbol="ABC270115C00050000.TO", qty=1.0,
                        proceeds=398.70, cost=500.65, gain=-101.95,
                        is_option=True)
        self.assertIn("(1 contract × 100 sh @ 3.9870)", text)
        self.assertIn("(1 contract × 100 sh @ 5.0065)", text)

    def test_split_denial_names_both_destinations(self):
        # S026-10: 700 denied, 300 of it permanent.
        text = self._ev(symbol="MIX.TO", proceeds=500.0, cost=1500.0,
                        raw_gain=-1000.0, gain=-300.0,
                        disallowed_amount=700.0,
                        permanently_disallowed=300.0, is_wash_sale=True)
        flat = " ".join(text.split())
        self.assertIn("400.00 denied loss → replacement lot's ACB", flat)
        self.assertIn("300.00 denied loss PERMANENTLY lost", flat)
        self.assertIn("affiliated person", flat)
        # A fully deferred denial names no permanent loss.
        text = self._ev(symbol="S.TO", proceeds=500.0, cost=1500.0,
                        raw_gain=-1000.0, gain=0.0,
                        disallowed_amount=1000.0, is_wash_sale=True)
        self.assertNotIn("PERMANENTLY", text)
        self.assertIn("replacement lot's ACB", text)


class TestAuditMerge(unittest.TestCase):
    """Formerly: test_fix_l_filing_b.TestAuditMerge"""
    def test_us_lots_of_mixed_term_are_labelled_mixed(self):
        # S026-15: a sale over LT and ST lots read LONG_TERM.
        recs = [{"id": "s1", "symbol": "AAPL.US", "qty": 10.0,
                 "gain": 1000.0, "term": "LONG_TERM", "days_held": 609,
                 "direction": "LONG"},
                {"id": "s1", "symbol": "AAPL.US", "qty": 10.0,
                 "gain": 500.0, "term": "SHORT_TERM", "days_held": 92,
                 "direction": "LONG"}]
        (ev,) = AU.merge_lot_records(recs)
        self.assertEqual(ev["term"], "MIXED")
        self.assertEqual(ev["term_split"],
                         {"LONG_TERM": 1000.0, "SHORT_TERM": 500.0})
        self.assertIsNone(ev["days_held"])
        self.assertEqual(ev["days_held_range"], [92, 609])
        self.assertEqual(ev["lots"], 2)
        self.assertEqual(ev["qty"], 20.0)

    def test_cross_zero_close_and_write_stay_two_events(self):
        # S026-19: long 2 puts, one SELL of 5 under grant timing.
        with tempfile.TemporaryDirectory() as td:
            sym = "QQQ250919P00040000.TO"
            b = _AuditBook(td, [
                _ttx(symbol=sym, quantity=2.0, price=1.0, net_amount=-201.0,
                     date="2025-07-02", date_settle="2025-07-03"),
                _ttx(symbol=sym, quantity=-5.0, price=2.0,
                     net_amount=999.0, date="2025-08-01",
                     date_settle="2025-08-04")], timing="grant")
            rc, doc, err = b.audit()
        self.assertEqual(rc, 0, err)
        evs = sorted(doc["events"], key=lambda e: e["direction"])
        self.assertEqual(len(evs), 2)
        self.assertEqual([e["direction"] for e in evs], ["LONG", "SHORT"])
        self.assertEqual(evs[0]["qty"], 2.0)
        self.assertTrue(evs[1]["grant"])
        self.assertTrue(all(e["tie_out"]["ties"] for e in evs))


class TestAuditTotalsPinned(unittest.TestCase):
    """S026-11: the audit total gain (text and multi-book JSON) and the
    *.traces TOTAL row were unpinned.

    Formerly: test_fix_l_filing_b.TestAuditTotalsPinned"""

    def test_reconciliation_block_total(self):
        evs = [{"gain": 100.25, "disallowed_amount": 0.0, "tie_out": {}},
               {"gain": -40.5, "disallowed_amount": 12.0, "tie_out": {}}]
        text = "\n".join(AU.render_reconciliation(evs, [], False))
        self.assertRegex(text, r"total gain\s+59\.75")
        self.assertRegex(text, r"total disallowed\s+12\.00")

    def test_multi_book_json_sums_every_book(self):
        from taxjson.cli.explain import _merge_audit_json
        docs = [{"events": [{"id": "a"}], "total_gain": 100.0,
                 "total_disallowed": 1.0, "failed": False,
                 "reconciliation_failures": []},
                {"events": [{"id": "b"}], "total_gain": -30.5,
                 "total_disallowed": 2.0, "failed": True,
                 "reconciliation_failures": ["x"]}]
        doc = _merge_audit_json(docs, "CAD", "canada")
        self.assertEqual(doc["total_gain"], 69.5)
        self.assertEqual(doc["total_disallowed"], 3.0)
        self.assertEqual(len(doc["events"]), 2)
        self.assertTrue(doc["failed"])
        self.assertEqual(doc["reconciliation_failures"], ["x"])

    def test_traces_total_row(self):
        from taxjson.lib.trace_format import render_summary_table
        lines = render_summary_table([
            {"symbol": "AAA.TO", "qty": -10, "gain": 300.0,
             "disallowed_amount": 0.0},
            {"symbol": "AAA.TO", "qty": -5, "gain": -100.0,
             "disallowed_amount": 20.0},
            {"symbol": "BBB.TO", "qty": -1, "gain": 50.0,
             "disallowed_amount": 0.0}])
        total = next(l for l in lines if l.startswith("# TOTAL"))
        self.assertEqual(total.split()[2], "3")
        self.assertIn("+$250.00", total)
        self.assertIn("$20.00", total)
