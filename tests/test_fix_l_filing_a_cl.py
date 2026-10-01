"""Low-round fixes to `taxjson checklist` (lib/checklist.py) and its
neighbours: detectors that computed a verdict from the readable subset of
the books (R1-338, S066-19, S067-04, S067-11), from stale or other-year
books (S067-12, S068-16), a missing-history row filter (S067-10),
guidance text (S068-06, S023-08), the form-export rounding check (R1-210),
a US check for options left open past expiry (S066-15), pins (S067-03,
S067-20), the watch state shape (S068-11) and `sanity` naming an
unreadable book (R1-338)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeSub:
    def __init__(self, table=None):
        self.table = table or {}

    def __call__(self, argv, timeout=900):
        return self.table.get(argv[0], (0, "", ""))


CFG = {"settings": {"year": 2025, "country": "canada"},
       "accounts": {"margin": {"type": "taxable"},
                    "margin2": {"type": "taxable"},
                    "coins": {"type": "taxable", "crypto": True}}}


def _ctx(root, table=None, cfg=None, year=2025):
    return cl.Ctx(root=root, cfg=cfg or CFG, year=year,
                  today=date(2026, 9, 23), run_sub=FakeSub(table))


def _gains(root, acct, rows, year=2025, wash=True):
    (root / "work").mkdir(exist_ok=True)
    name = f"{acct}_gains_wash.json" if wash else f"{acct}_gains.json"
    (root / "work" / name).write_text(json.dumps(
        {"summary": {"year": year}, "transactions": rows}))


def _inputs(root, acct):
    d = root / "inputs" / acct
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.csv").write_text("x\n")


DENIED = [{"date": "2025-05-01", "date_settle": "2025-05-02",
           "disallowed_amount": 300.0, "permanently_disallowed": 100.0}]


class TestWashReviewed(unittest.TestCase):

    def test_permanent_amount_is_pinned(self):
        # S067-20: the note names the PERMANENT part, not the total.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _gains(root, "margin", DENIED)
            r = cl.d_wash_reviewed(_ctx(root, cfg={
                "settings": CFG["settings"],
                "accounts": {"margin": {"type": "taxable"}}}))
        self.assertEqual(r.status, "manual")
        self.assertTrue(r.detail.startswith("100.00 permanently denied"),
                        r.detail)

    def test_unreadable_book_blocks(self):
        # R1-338: a truncated margin file left the crypto total as "done".
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _gains(root, "margin", DENIED)
            _gains(root, "margin2", [])
            _gains(root, "coins", [{"date": "2025-03-01",
                                    "date_settle": "2025-03-01",
                                    "disallowed_amount": 10.0}])
            (root / "work" / "margin_gains_wash.json").write_text('{"trunc')
            r = cl.d_wash_reviewed(_ctx(root))
        self.assertEqual(r.status, "blocked")
        self.assertIn("margin_gains_wash.json", r.detail)

    def test_account_with_inputs_but_no_gains_blocks(self):
        # S067-11
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _gains(root, "margin", [])
            _inputs(root, "margin2")
            r = cl.d_wash_reviewed(_ctx(root))
        self.assertEqual(r.status, "blocked")
        self.assertIn("no gains file for margin2", r.detail)

    def test_other_year_books_block(self):
        # S068-16: work/ built for 2026, [settings] year 2025.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _gains(root, "margin", DENIED, year=2026)
            r = cl.d_wash_reviewed(_ctx(root, cfg={
                "settings": CFG["settings"],
                "accounts": {"margin": {"type": "taxable"}}}))
        self.assertEqual(r.status, "blocked")
        self.assertIn("another year (margin: 2026", r.detail)

    def test_stale_wash_file_is_attention(self):
        # S067-12: `run --account margin` rebuilt the plain gains after
        # the wash pass.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _gains(root, "margin", [])
            plain = root / "work" / "margin_gains.json"
            plain.write_text(json.dumps({"summary": {"year": 2025},
                                         "transactions": []}))
            later = time.time() + 10
            os.utime(plain, (later, later))
            r = cl.d_wash_reviewed(_ctx(root, cfg={
                "settings": CFG["settings"],
                "accounts": {"margin": {"type": "taxable"}}}))
        self.assertEqual(r.status, "attention")
        self.assertIn("older than their inputs", r.detail)


class TestBooksStateGuards(unittest.TestCase):
    """S068-16 / S067-12: audit and form-export do not compare other-year
    or stale books (they blamed phantoms or an export bug)."""

    def test_other_year_blocks_audit_and_form_export(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _gains(root, "margin", [], year=2026)
            ctx = _ctx(root, table={"audit": (0, "pipeline tie-out 2 tied, "
                                              "0 MISMATCHED, 2 not found", "")})
            for fn in (cl.d_audit, cl.d_form_export):
                r = fn(ctx)
                self.assertEqual(r.status, "blocked", fn.__name__)
                self.assertIn("built for another tax year", r.detail)
                self.assertNotIn("phantom", r.detail)


class TestBaseBooksReadInFull(unittest.TestCase):
    """S066-19 / S067-04: a corrupt base book is named, not skipped."""

    def _books(self, root):
        (root / "work").mkdir()
        (root / "work" / "margin_base.json").write_text(json.dumps(
            {"transactions": [{"action": "ADJUST", "date": "2025-06-01"},
                              {"action": "ADJUST", "date": "2025-07-01"},
                              {"action": "BUYSELL", "date": "2026-02-11",
                               "date_settle": "2026-02-12"}]}))
        (root / "work" / "margin2_base.json").write_text(json.dumps(
            {"transactions": [{"action": "BUYSELL", "date": "2025-05-07",
                               "date_settle": "2025-05-08"}]}))
        for a in ("margin", "margin2"):
            _inputs(root, a)

    def test_roc_entered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._books(root)
            r = cl.d_roc_entered(_ctx(root))
            self.assertEqual((r.status, r.detail[:24]),
                             ("manual", "2 ADJUST row(s) in 2025;"))
            (root / "work" / "margin_base.json").write_text('{"tr')
            r = cl.d_roc_entered(_ctx(root))
        self.assertEqual(r.status, "blocked")
        self.assertIn("work/margin_base.json", r.detail)

    def test_inputs_frozen(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._books(root)
            _inputs(root, "coins")
            r = cl.d_inputs_frozen(_ctx(root))
            # S067-03: the latest activity is the SETTLEMENT date.
            self.assertEqual((r.status, r.detail),
                             ("done", "latest activity 2026-02-12"))
            (root / "work" / "margin_base.json").write_text("[]")
            r = cl.d_inputs_frozen(_ctx(root))
        self.assertEqual(r.status, "blocked")
        self.assertIn("work/margin_base.json", r.detail)

    def test_inputs_frozen_settle_key_decides(self):
        # S067-03: a trade on Jan 29 that settles Feb 1 is past the Jan
        # 31 cutoff; on the trade date it would not be.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            _inputs(root, "margin")
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [{"action": "BUYSELL", "date": "2026-01-29",
                                   "date_settle": "2026-02-01"}]}))
            cfg = {"settings": CFG["settings"],
                   "accounts": {"margin": {"type": "taxable"}}}
            r = cl.d_inputs_frozen(_ctx(root, cfg=cfg))
        self.assertEqual(r.status, "done")

    def test_run_clean_unreadable_sum(self):
        if os.name != "posix" or os.geteuid() == 0:
            self.skipTest("needs a non-root POSIX user")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "reports").mkdir()
            s = root / "reports" / "margin.sum"
            s.write_text("validation: 3 error(s)\n")
            s.chmod(0)
            try:
                r = cl.d_run_clean(_ctx(root, cfg={
                    "settings": CFG["settings"],
                    "accounts": {"margin": {"type": "taxable"}}}))
            finally:
                s.chmod(0o644)
        self.assertEqual(r.status, "attention")
        self.assertIn("cannot read reports/margin.sum", r.detail)


class TestMissingHistoryRows(unittest.TestCase):
    """S067-10: every AFFECTS row counts, whatever its spelling."""

    OUT = """
## Truncated history - positions go short (missing a buy): 3 pair(s)
   3 affect tax year 2025; 0 do not.

AFFECTS 2025 - missing basis distorts this year's gain; fix before filing:
------------------------------------------------------------
Symbol                   Account    Cur     PeakShort FirstNeg ...
------------------------------------------------------------
BRK/B                    margin     USD      10.0000 2025-03-01            1        100.00
btc                      coins      ?         1.0000 2025-03-01            1        100.00
XUSDT                    coins      USDT      1.0000 2025-03-01            1        100.00

NOT relevant to 2025 - short only from other-year sales:
------------------------------------------------------------
OLD.TO                   margin     CAD      10.0000 2023-03-01            0          0.00
"""

    def test_all_rows_count(self):
        with tempfile.TemporaryDirectory() as td:
            r = cl.d_missing_history(_ctx(Path(td), {
                "find-missing-history": (0, self.OUT, "")}))
        self.assertEqual(r.status, "attention")
        self.assertTrue(r.detail.startswith("3 position(s)"), r.detail)
        self.assertIn("BRK/B (margin)", r.detail)
        self.assertNotIn("OLD.TO", r.detail)

    def test_zero_cost_section_detail_lines_are_not_rows(self):
        out = ("\nAFFECTS 2025 - sold this year against a $0 basis; the gain "
               "is overstated by the missing basis:\n" + "-" * 20 + "\n"
               "Symbol   Account Cur ZeroQty\n" + "-" * 20 + "\n"
               "NEW.TO   margin  CAD 10.0 2025-01-02 1 100.00 corp action\n"
               "    └ spun off from OLD\n")
        with tempfile.TemporaryDirectory() as td:
            r = cl.d_missing_history(_ctx(Path(td), {
                "find-missing-history": (0, out, "")}))
        self.assertTrue(r.detail.startswith("1 position(s)"), r.detail)


class TestGuidanceText(unittest.TestCase):

    def test_missing_history_why_covers_both_directions(self):
        # S068-06: truncated history understates; $0 cost overstates.
        why = cl.step_meta("missing-history", "canada")[4]
        self.assertIn("understating", why)
        self.assertIn("overstate", why)
        self.assertNotIn("booked at $0 cost — the gain is overstated", why)

    def test_t5_t3_names_box_18(self):
        # S023-08: split-share corps report on a T5 (box 18 -> 17400).
        why = cl.step_meta("t5-t3", "canada")[4]
        self.assertIn("report on a T5", why)
        self.assertIn("box 18", why)


class TestFormExportRounding(unittest.TestCase):
    """R1-210: per-row rounding is compared on the unrounded rows."""

    def test_many_rows_rounding_is_not_attention(self):
        from taxjson.bin.taxjson_form_export import build_schedule3
        entries = [{"symbol": f"S{i}.TO", "qty": 1, "proceeds": 200.004,
                    "cost": 100.0, "gain": 100.004, "date": "2025-03-03",
                    "date_settle": "2025-03-04", "account": "margin"}
                   for i in range(100)]
        rep = build_schedule3(entries, 2025)
        self.assertEqual(rep["totals"]["gain_all"], 10000.0)
        self.assertAlmostEqual(rep["gain_unrounded"], 10000.4, places=6)
        summ = {"accounts": [{"account": "margin", "realized": 10000.40}],
                "filing": {"totals": {"proceeds": rep["totals"]["proceeds_all"],
                                      "gain": 10000.0}}}
        cfg = {"settings": CFG["settings"],
               "accounts": {"margin": {"type": "taxable"}}}
        with tempfile.TemporaryDirectory() as td:
            r = cl.d_form_export(_ctx(Path(td), cfg=cfg, table={
                "form-export": (0, json.dumps(rep), ""),
                "sum": (0, json.dumps(summ), "")}))
            self.assertEqual(r.status, "done", r.detail)
            # A real gap (a missing 1.00 row) is still caught.
            summ["accounts"][0]["realized"] = 10001.40
            r = cl.d_form_export(_ctx(Path(td), cfg=cfg, table={
                "form-export": (0, json.dumps(rep), ""),
                "sum": (0, json.dumps(summ), "")}))
        self.assertEqual(r.status, "attention")


class TestUsExpiredOptions(unittest.TestCase):
    """S066-15: a US project checks for option positions (long or
    written) still open past expiry instead of marking the step n/a."""

    CFG = {"settings": {"year": 2025, "country": "usa"},
           "accounts": {"margin": {"type": "taxable"}}}

    def _book(self, root, rows):
        (root / "work").mkdir(exist_ok=True)
        (root / "work" / "margin_base.json").write_text(
            json.dumps({"transactions": rows}))

    def test_written_and_long_expired_are_attention(self):
        rows = [{"action": "BUYSELL", "date": "2025-02-03",
                 "date_settle": "2025-02-04", "symbol": "ZZW251219C00015000.US",
                 "quantity": -1, "price": 2.0, "net_amount": 199.0,
                 "currency": "USD", "account": "margin"},
                {"action": "BUYSELL", "date": "2025-02-03",
                 "date_settle": "2025-02-04", "symbol": "ZZL251219P00015000.US",
                 "quantity": 1, "price": 2.0, "net_amount": 201.0,
                 "currency": "USD", "account": "margin"}]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._book(root, rows)
            ctx = _ctx(root, cfg=self.CFG)
            res = {r.id: r for r in cl.evaluate(ctx, only=["option-boundary"])}
        r = res["option-boundary"]
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("2 option position(s)", r.detail)
        self.assertIn("ZZW251219C00015000.US -1", r.detail)
        self.assertNotIn("s.49", cl.step_meta("option-boundary", "usa")[2])

    def test_closed_contract_is_done(self):
        rows = [{"action": "BUYSELL", "date": "2025-02-03",
                 "date_settle": "2025-02-04", "symbol": "ZZW251219C00015000.US",
                 "quantity": -1, "price": 2.0, "net_amount": 199.0,
                 "currency": "USD", "account": "margin"},
                {"action": "BUYSELL", "date": "2025-12-19",
                 "date_settle": "2025-12-19", "symbol": "ZZW251219C00015000.US",
                 "quantity": 1, "price": 0.0, "net_amount": 0.0,
                 "currency": "USD", "account": "margin"}]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._book(root, rows)
            r = cl.d_option_boundary(_ctx(root, cfg=self.CFG))
        self.assertEqual(r.status, "done", r.detail)


class TestWatchStateShape(unittest.TestCase):

    def test_list_state_is_no_baseline(self):
        # S068-11
        from taxjson.bin.taxjson_watch import load_state
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".watch_state.json"
            p.write_text("[]")
            self.assertIsNone(load_state(p))


class TestSanityNamesUnreadableBook(unittest.TestCase):
    """R1-338: a corrupt gains file is named, not 'not an account'."""

    def test_corrupt_book(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.tfsa]\ntype = "sheltered"\n')
            (root / "work").mkdir()
            (root / "work" / "margin_gains_wash.json").write_text('{"trunc')
            (root / "work" / "tfsa_gains.json").write_text(
                json.dumps({"inventory": []}))
            h = root / "h.toml"
            h.write_text("")
            p = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "sanity", f"margin={h}"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("the books of 'margin' cannot be read", p.stderr)
        self.assertNotIn("is not an account", p.stderr)


if __name__ == "__main__":
    unittest.main()


class TestExportTradeRoundSettleKey(unittest.TestCase):
    """S067-03 (EX1): holdings.toml's current-round trades are ordered on
    the settlement date — a sale that settles after a later trade's
    settlement does not end the round."""

    def test_settle_order(self):
        from taxjson.bin.taxjson_export import _load_trade_events
        rows = [{"action": "BUYSELL", "symbol": "ABC.TO", "quantity": 10,
                 "price": 10, "date": "2025-06-02", "date_settle": "2025-06-03"},
                {"action": "BUYSELL", "symbol": "ABC.TO", "quantity": -10,
                 "price": 11, "date": "2026-01-02", "date_settle": "2026-01-06"},
                {"action": "BUYSELL", "symbol": "ABC.TO", "quantity": 10,
                 "price": 12, "date": "2026-01-03", "date_settle": "2026-01-05"}]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            ev = _load_trade_events([p])
        self.assertEqual(len(ev["ABC.TO"]), 3)
