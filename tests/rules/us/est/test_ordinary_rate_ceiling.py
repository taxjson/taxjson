"""The 2026-10-29 fix round 2, US year-end group: the experimental US
engine's estimate, carryover and close-year lock.

- TJ-0509 the US estimate caps the tax at the tax on all of the taxable
  income at the ordinary rates (Pub 505 Worksheet 2-7 lines 39-40): a
  long-term gain stacked between the 0% capital-gain band's end and the
  12% ordinary band's end paid 15%;
- TJ-0472 `estimate --verbose` lists every band either run reaches, so
  the BASE column adds up to its ORDINARY total when an investment loss
  moves the WITH run down a bracket;
- TJ-0464 close-year's US lock counts the year's [carryover] claimed
  entry as the deduction used (it carried the whole loss when the
  estimate had no other income), says when neither it nor [estimate]
  other_income was set; the next year's ledger shows a locked year's
  offset as its lock's, and handoff flags a claimed entry that differs;
- TJ-0554 a US close-year record keeps each lot held at Dec 31 (its
  acquisition date and basis), and handoff compares them: an opening with
  the same units and total basis but the lots' costs swapped passed,
  and the next year's FIFO sale took another basis;
- TJ-0555 handoff against a record that is not the year before the
  project's (a 2024 lock in a 2026 project) is a problem of the record
  itself (exit 1, in the JSON), not a clean verdict with a note;
- TJ-0466 carryover and close-year say how much §1256 P/L they carry
  (round 3: split 40% short-term / 60% long-term, as Form 6781 does);
- TJ-0470 with no close-year lock before the year and no carryover
  entered, the US estimate says its carryovers in are 0 and why, and
  points to `tjs carryover`;
- TJ-0473 no T1A carry-back or `amt` in US output, and no IRA in the
  Canadian checklist;
- TJ-0468 docs/filing.md has a US variant of its Canada-only steps
  instead of "the same steps apply with the form names swapped".

Synthetic data only (made-up tickers and accounts; amounts under a
thousand where a figure is written with a thousands separator).

Formerly: test_fix_1029_r2_usyear
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import tax_estimate as TE


# ------------------------------------------------------------- TJ-0509
def _usa(**kw):
    args = dict(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, year=2025)
    args.update(kw)
    return TE.estimate_usa(**args)


class TestOrdinaryRateCeiling(unittest.TestCase):
    """TJ-0509: Pub 505 Worksheet 2-7 lines 39-40 — the tax is the
    smaller of the stacked computation and the tax on all of the
    taxable income at the ordinary rates. 2025 single: the 0% band ends
    at 48,350, the 12% band at 48,475; 2026: 49,450 and 50,400.

    Formerly: test_fix_1029_r2_usyear.TestOrdinaryRateCeiling"""

    @rule(R.US_EST_CEILING)
    def test_2025_gain_between_the_band_ends(self):
        # ordinary taxable 48,350 (64,100 - 15,750): a 125 gain fills
        # 48,350-48,475, 15% stacked (18.75), 12% all-ordinary (15.00).
        r = _usa(lt=125.0, other_income=64100.0)
        self.assertEqual(r["estimated_tax"], 15.0)
        self.assertEqual(r["tax_with"]["total"], 5578.5)
        self.assertEqual(r["tax_with"]["preferential"], 15.0)
        self.assertTrue(r["trace_with"]["ceiling"])
        self.assertEqual(r["trace_with"]["pref_stacked"], 18.75)

    @rule(R.US_EST_CEILING)
    def test_2026_audit_case(self):
        # The audit's figures: base 5,686, all at the ordinary rates
        # 5,800, so 114 on a 950 gain (stacked: 142.50).
        r = _usa(lt=950.0, other_income=65550.0, year=2026)
        self.assertEqual(r["tax_base"]["total"], 5686.0)
        self.assertEqual(r["tax_with"]["total"], 5800.0)
        self.assertEqual(r["estimated_tax"], 114.0)

    @rule(R.US_EST_CEILING)
    def test_ceiling_not_reached(self):
        # A gain well inside the 0% band, and one deep in the 15% band:
        # the stacked tax is lower, unchanged.
        low = _usa(lt=500.0, other_income=40000.0)
        self.assertEqual(low["estimated_tax"], 0.0)
        self.assertFalse(low["trace_with"]["ceiling"])
        high = _usa(lt=40000.0, other_income=120000.0)
        self.assertFalse(high["trace_with"]["ceiling"])
        self.assertEqual(high["estimated_tax"], 6000.0)


if __name__ == "__main__":
    unittest.main()
