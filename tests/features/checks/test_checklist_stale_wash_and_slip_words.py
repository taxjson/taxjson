"""Re-audit-2 test pins (tests-pins-04/05/06, reports group): labels and
accumulators that held at the audit but that no test failed on when
reverted. Each test names the finding and the mutant it kills.

  A2-0880 / A2-1585  fees-sum's per-brokerage 'non-option' line, its
                     $/UNIT header and 'Non-option vs option fees:'
                     heading; `tjs sum`'s NON-OPT column; the tx
                     views' 'TOTAL STAKING (crypto, ordinary income):'
  A2-1555            fx-cash's per-currency ACQUIRED column sums
  A2-0533            `tjs sum`'s summarize_gains / summarize_income
                     accumulators (2+ rows per ticker and term, 2
                     withholding rows, an 'other' row, all-zero rows)
  A2-0861 / A2-0906 / A2-0907
                     run_gains: the Canadian stock-dividend line and the
                     Canadian option summary keys stay out of a US book;
                     no note for a plain purchase; fees after a non-trade
                     row; by_ticker after a tainted row and with two
                     dividends; a manual-reporting row has no gain/cost
  A2-0865 / A2-0866 / A2-1532
                     checklist: a stale wash file makes audit and
                     form-export 'attention'; the t5-t3 step names the
                     TAXABLE line; the US slip and margin-interest words

All data is synthetic.

Formerly: test_fix_a2_pins2_reports
"""
import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
COUNTRIES = ("canada", "usa")


# ------------------------------------------------------------ checklist
def _cl_ctx(root, country="canada", table=None, accounts=None):
    from taxjson.lib import checklist as cl
    cfg = {"settings": {"year": 2025, "country": country,
                        "base_currency": "USD" if country == "usa"
                        else "CAD"},
           "accounts": accounts or {"margin": {"type": "taxable"}}}
    table = table or {}
    return cl.Ctx(root=root, cfg=cfg, year=2025, today=date(2026, 3, 1),
                  run_sub=lambda argv, timeout=900:
                  table.get(argv[0], (0, "", "")))


class TestChecklistPins(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_reports.TestChecklistPins"""

    @rule(R.CA_SL_09)
    def test_stale_wash_file_is_attention_for_audit_and_form_export(self):
        # A2-0865: _books_state's `stale_wash_inputs` guard.
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            doc = json.dumps({"summary": {"year": 2025}, "transactions": []})
            (work / "margin_gains_wash.json").write_text(doc)
            plain = work / "margin_gains.json"
            plain.write_text(doc)
            later = time.time() + 10
            os.utime(plain, (later, later))
            ctx = _cl_ctx(root, table={"audit": (
                0, "pipeline tie-out 2 tied, 0 MISMATCHED, 0 not found",
                "")})
            for fn in (cl.d_audit, cl.d_form_export):
                r = fn(ctx)
                self.assertEqual(r.status, "attention", (fn.__name__, r))
                self.assertIn("older than their inputs", r.detail)

    def test_t5_t3_names_the_taxable_line(self):
        # A2-0866 (S068-14). A US project compares the 1099-DIV by
        # hand; a Canadian one runs `tjs slip-audit`
        # (tests/test_slip_audit.py).
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            r = cl.DETECTORS["t5-t3"](_cl_ctx(Path(td), country="usa"))
        self.assertIn("TAXABLE line of `tjs divs-sum`", r.detail)

    @rule(R.US_RPT_09)
    def test_us_slip_and_margin_interest_words(self):
        # A2-1532: d_t5008's slip name and d_fees's investment interest.
        from taxjson.lib import checklist as cl
        out = {}
        with tempfile.TemporaryDirectory() as td:
            for c in COUNTRIES:
                root = Path(td) / c
                root.mkdir()
                ctx = _cl_ctx(root, country=c)
                out[c] = (cl.d_t5008(ctx).detail, cl.d_fees(ctx).detail)
        self.assertIn("put the broker 1099-B CSVs", out["usa"][0])
        self.assertNotIn("T5008", out["usa"][0])
        self.assertIn("investment interest (Form 4952)", out["usa"][1])
        self.assertNotIn("22100", out["usa"][1])
        self.assertIn("put the broker T5008 CSVs", out["canada"][0])
        self.assertIn("line 22100", out["canada"][1])


if __name__ == "__main__":
    unittest.main()
