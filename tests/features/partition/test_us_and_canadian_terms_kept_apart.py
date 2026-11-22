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
import _paths

REPO_ROOT = _paths.REPO


# ------------------------------------------------------------- TJ-0473
class TestCountryTerms(unittest.TestCase):
    """TJ-0473: Canadian terms in US output (a T1A carry-back, `amt`
    reading the lock) and a US one (the IRA) in Canada's checklist.

    Formerly: test_fix_1029_r2_usyear.TestCountryTerms"""

    def test_carryover_rows_after_the_project_year(self):
        from taxjson.bin import taxjson_carryover as CO
        nets = {2024: {"net": -500.0, "st": -500.0, "lt": 0.0,
                       "dispositions": 1},
                2025: {"net": 20.0, "st": 20.0, "lt": 0.0,
                       "dispositions": 1}}
        us = CO.build_usa_ledger(nets, {})
        us["project_year"] = 2024
        us["rows"][1]["after_project_year"] = True
        text = " ".join(CO.render(us, "USD", None, False,
                                  in_project=True).split())
        self.assertIn("are partial — only the trades of that year this "
                      "project's inputs happen to hold. The carryover "
                      "above stops at 2024", text)
        self.assertNotIn("T1A", text)

    def test_close_year_names_amt_only_with_a_minimum_tax(self):
        from taxjson.cli.file import _carryforwards_summary
        us = {"capital_loss": {"st_closing": 10.0, "lt_closing": 0.0,
                               "deduction_used": 0.0,
                               "deduction_available": 0.0,
                               "other_income_entered": True}}
        text = " ".join(" ".join(_carryforwards_summary(us, 2024)).split())
        self.assertIn("The 2025 project's estimate and carryover read "
                      "these", text)
        self.assertNotIn("amt", text)
        ca = {"net_capital_loss": {"applied": 0.0, "created": 10.0,
                                   "closing": 10.0},
              "minimum_tax": {"recovered_federal": 0.0, "created": 0.0,
                              "closing": 0.0,
                              "other_income_entered": True}}
        text = " ".join(" ".join(_carryforwards_summary(ca, 2024)).split())
        self.assertIn("estimate, carryover and amt read these", text)

    def test_canadian_checklist_names_no_ira(self):
        from taxjson.lib import checklist as cl
        ca = {i.id: i for i in cl.items(2025, "canada")}
        self.assertNotIn("IRA", ca["wash-reviewed"].why)
        self.assertIn("registered-account repurchase",
                      ca["wash-reviewed"].why)
        us = {i.id: i for i in cl.items(2025, "usa")}
        self.assertIn("IRA purchase", us["wash-reviewed"].why)


# ------------------------------------------------------------- TJ-0468
class TestFilingDocUSVariant(unittest.TestCase):
    """TJ-0468: filing.md told a US project to follow its Canadian steps
    (t1135, instalments, amt; claimed as line 25300 / inclusion rate).

    Formerly: test_fix_1029_r2_usyear.TestFilingDocUSVariant"""

    def test_us_section(self):
        doc = (REPO_ROOT / "docs" / "filing.md").read_text()
        self.assertNotIn("the same steps apply with the form names "
                         "swapped", doc)
        us = doc[doc.index("\n## A US return\n"):]
        flat = " ".join(us.split())
        for want in ("tjs form-export --form 8949", "Form 6781 by hand",
                     "Schedule D line 21", "tjs reconcile-slips",
                     "refuse a US project"):
            self.assertIn(want, flat)
        self.assertNotIn("25300", us)
        from taxjson.lib.country import COMMAND_COUNTRY
        # Each Canada-only command the doc names is said to be.
        intro = " ".join(doc[:doc.index("## 1.")].split())
        for cmd, owner in COMMAND_COUNTRY.items():
            if owner == "canada" and ":" not in cmd \
                    and f"`tjs {cmd}`" in doc:
                self.assertTrue(f"`{cmd}`" in intro
                                or f"`tjs {cmd}`" in intro, cmd)


if __name__ == "__main__":
    unittest.main()
