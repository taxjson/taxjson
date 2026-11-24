"""Backlog group `errors` (2026-10-25): error messages, atomic writes and
the smaller commands (elect, fetch --list, slips, t1135, tax-logic).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account 99900001.

Formerly: test_fix_backlog_errors
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.project.settings import Settings


def _slip_report(suggest=False):
    """A synthetic slip-audit report (lib/slip_audit.audit's shape): two
    accounts with one USD T5 each, one difference, an income on no slip;
    `suggest`: a T5 box-18 suggestion too."""
    def bucket(slip, books):
        return {"currency": "USD", "slips": ["T5 ZZB"], "split_by_t3": [],
                "lines": [{"label": "Foreign income", "boxes": "T5 15",
                           "slip": slip, "books": books,
                           "diff": books - slip,
                           "status": "ok" if slip == books else "differs",
                           "books_cad_daily": books * 1.35,
                           "slip_cad_annual_average": slip * 1.36}]}

    def account(name, slip, books):
        return {"account": name, "groups": [{
            "broker_account": None, "buckets": [bucket(slip, books)],
            "payments": {"missing_from_books": [], "missing_from_slip": [],
                         "amount_differs": [], "other_year": []},
            "record_year": []}]}
    sg = {"capital_gains_dividends": [], "tt_lines": [], "notes": []}
    if suggest:
        sg["capital_gains_dividends"] = [{"toml": "[[distributions]]\n"
                                                  "symbol = \"ZZQ.TO\""}]
    return {"year": 2024, "tolerance": 1.0,
            "sources": [{"kind": "slips.toml",
                         "file": "inputs/slips/slips.toml", "slips": 2}],
            "annual_average": {},
            "accounts": [account("ib", 75.0, 75.0),
                         account("questrade", 30.0, 18.0)],
            "coverage": {"no_slip": [{"account": "margin",
                                      "sources": ["zz_demo.csv"],
                                      "income": {"CAD": 68.0}}],
                         "slips_without_books": []},
            "t5008": [], "suggestions": sg, "problems": [],
            "issues": [{"account": "questrade"}, {"account": "margin"}]}


class TestSlipAuditClosing(unittest.TestCase):
    """TJ-0179: "apply the suggestions" only when a SUGGESTIONS section
    was printed; the default view within the 6-line budget.

    Formerly: test_fix_backlog_errors.TestSlipAuditClosing"""

    def test_no_suggestions(self):
        from taxjson.lib import out
        from taxjson.lib.slip_audit import render
        text = "\n".join(render(_slip_report(), width_=0, details=True))
        self.assertNotIn("SUGGESTIONS", text)
        self.assertNotIn("suggestion", text)
        self.assertIn("2 finding(s) (margin 1, questrade 1): correct the "
                      "books or slips.toml where one is wrong, or explain "
                      "each difference and mark the checklist's t5-t3 step "
                      "done.", text)
        brief = "\n".join(render(_slip_report(), width_=100))
        self.assertLessEqual(len(out.prose_lines(brief)), 6, brief)
        self.assertIn("): correct or explain each — tjs slip-audit "
                      "--details", brief)
        self.assertIn("USD:  T5 ZZB", brief)

    def test_with_suggestions(self):
        from taxjson.lib.slip_audit import render
        text = "\n".join(render(_slip_report(suggest=True), width_=0,
                                details=True))
        self.assertIn("SUGGESTIONS", text)
        self.assertIn("apply the suggestions, or explain each difference",
                      text)
        brief = "\n".join(render(_slip_report(suggest=True), width_=100))
        self.assertIn("): apply or explain each — tjs slip-audit --details",
                      brief)


class TestT1135Overrides(unittest.TestCase):
    """TJ-0083: the notes write the override with its keyword; a line
    with none is refused naming what to write (`tjs migrate` only
    when a t1135.map is there); the checklist's blocked line names
    `tjs t1135`, not migrate, and shows the problem, not the path.

    Formerly: test_fix_backlog_errors.TestT1135Overrides"""

    def _refusal(self, text, t1135_map=False):
        from taxjson.bin.taxjson_t1135 import load_overrides
        from taxjson.lib.cli_diag import InputContentError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(text)
            if t1135_map:
                (Path(td) / "t1135.map").write_text("ZZQ.US CA\n")
            with self.assertRaises(InputContentError) as cm:
                load_overrides(p)
            msg = str(cm.exception)
            self.assertNotIn(td, msg)
        return msg

    def test_no_keyword_line(self):
        msg = self._refusal("TOBASE ZZQ.US ZZQ.TO\nZZC CA\n")
        self.assertTrue(msg.startswith(
            "ticker.map:2: 'ZZC CA' has no ticker.map keyword — "), msg)
        self.assertIn("`T1135 ZZC CA`", msg)
        self.assertNotIn("migrate", msg)
        msg = self._refusal("FOOBAR ZZQ.US ZZQ.TO\n")
        self.assertIn("`T1135 SYMBOL COUNTRY`", msg)
        self.assertIn("fix it or delete it", msg)
        self.assertNotIn("migrate", msg)

    def test_migrate_only_with_an_old_t1135_map(self):
        msg = self._refusal("ZZC CA\n", t1135_map=True)
        self.assertIn("`tjs migrate` converts the old t1135.map", msg)

    def test_other_problems_keep_their_text(self):
        msg = self._refusal("T1135 ZZQ.US CDN\n")
        self.assertIn("ISO 3166", msg)
        self.assertIn("`T1135 SYMBOL COUNTRY`", msg)
        self.assertNotIn("migrate", msg)

    def test_notes_name_the_keyword(self):
        import contextlib
        import io
        import json
        from taxjson.bin.taxjson_t1135 import build_report, render_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": []}))
            with contextlib.redirect_stderr(io.StringIO()):
                rep = build_report([base], [], 2025, {}, "CAD",
                                   full_history=False)
        rep["crypto_symbols"] = ["ZZC"]
        text = " ".join(render_report(rep).split())
        self.assertIn("a ticker.map `T1135 SYMBOL COUNTRY` line (`CA`: not "
                      "foreign property", text)
        self.assertIn("(`T1135 SYMBOL CA` once checked)", text)
        self.assertNotIn("(`SYMBOL CA", text)

    def test_checklist_blocked_line_names_t1135(self):
        from types import SimpleNamespace
        from taxjson.lib import checklist as CL
        err = ("taxjson-t1135: error: ticker.map:54: 'ZZC CA' has no "
               "ticker.map keyword — a T1135 override is a `T1135 SYMBOL "
               "COUNTRY` line (`T1135 ZZC CA`); another line: fix it or "
               "delete it (`tjs migrate` converts the old t1135.map)\n")
        ctx = SimpleNamespace(settings=Settings.from_table({"country": "canada"}),
                              sub=lambda *a: (2, "", err))
        r = CL.d_t1135(ctx)
        self.assertEqual(r.status, "blocked")
        self.assertEqual(r.cmd, "tjs t1135")
        self.assertTrue(r.detail.startswith("ticker.map:54: 'ZZC CA'"),
                        r.detail)


if __name__ == "__main__":
    unittest.main()
