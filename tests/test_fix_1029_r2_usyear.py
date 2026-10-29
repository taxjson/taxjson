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
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib import carryforward as CF
from taxjson.lib import tax_estimate as TE
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))


def _env(**extra):
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _cli(root, *args):
    """`taxjson -C ROOT ARGS` as a child of THIS tree's code."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *map(str, args)])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(), timeout=900)


def _ok(r):
    if r.returncode != 0:
        raise AssertionError(f"rc {r.returncode}\n{r.stdout[-3000:]}\n"
                             f"{r.stderr[-3000:]}")
    return r


def _said(r):
    return " ".join((r.stdout + r.stderr).split())


def _tmp(test):
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return Path(td.name)


def _us_toml(year, tail="", inputs_dir=None):
    return ('[settings]\ncountry = "usa"\n'
            f'year = {year}\nbase_currency = "USD"\n'
            + (f'inputs_dir = "{inputs_dir}"\n' if inputs_dir else "")
            + '\n[accounts.margin]\ntype = "taxable"\n' + tail)


def _us_project(root, year, book, tail=""):
    """A one-folder US project of the account `margin` (a .tt book)."""
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "trades.tt").write_text(book)
    (root / "taxjson.toml").write_text(_us_toml(year, tail))
    return root


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
    at 48,350, the 12% band at 48,475; 2026: 49,450 and 50,400."""

    @rule("US-EST-CEILING")
    def test_2025_gain_between_the_band_ends(self):
        # ordinary taxable 48,350 (64,100 - 15,750): a 125 gain fills
        # 48,350-48,475, 15% stacked (18.75), 12% all-ordinary (15.00).
        r = _usa(lt=125.0, other_income=64100.0)
        self.assertEqual(r["estimated_tax"], 15.0)
        self.assertEqual(r["tax_with"]["total"], 5578.5)
        self.assertEqual(r["tax_with"]["preferential"], 15.0)
        self.assertTrue(r["trace_with"]["ceiling"])
        self.assertEqual(r["trace_with"]["pref_stacked"], 18.75)

    @rule("US-EST-CEILING")
    def test_2026_audit_case(self):
        # The audit's figures: base 5,686, all at the ordinary rates
        # 5,800, so 114 on a 950 gain (stacked: 142.50).
        r = _usa(lt=950.0, other_income=65550.0, year=2026)
        self.assertEqual(r["tax_base"]["total"], 5686.0)
        self.assertEqual(r["tax_with"]["total"], 5800.0)
        self.assertEqual(r["estimated_tax"], 114.0)

    @rule("US-EST-CEILING")
    def test_ceiling_not_reached(self):
        # A gain well inside the 0% band, and one deep in the 15% band:
        # the stacked tax is lower, unchanged.
        low = _usa(lt=500.0, other_income=40000.0)
        self.assertEqual(low["estimated_tax"], 0.0)
        self.assertFalse(low["trace_with"]["ceiling"])
        high = _usa(lt=40000.0, other_income=120000.0)
        self.assertFalse(high["trace_with"]["ceiling"])
        self.assertEqual(high["estimated_tax"], 6000.0)


# ------------------------------------------------------------- TJ-0472
class TestTraceListsEveryBand(unittest.TestCase):
    """TJ-0472: the trace iterated the WITH run's slices only, so a
    band only the base reaches was dropped and the BASE rows did not
    add up to = ORDINARY."""

    def test_rows(self):
        from taxjson.bin.taxjson_run import _trace_bracket_rows
        TE.apply_vintage(2025)
        rows = _trace_bracket_rows(TE.US_ORD_BRACKETS, 49000.0, 46000.0)
        self.assertEqual(rows, [
            ("0-11,925 @ 10.00%", 1192.5, 1192.5),
            ("11,925-48,475 @ 12.00%", 4386.0, 4089.0),
            ("48,475-49,000 @ 22.00%", 115.5, 0.0)])
        base = sum(b for _l, b, _w in rows)
        self.assertAlmostEqual(base, TE._bracket_tax(
            49000.0, TE.US_ORD_BRACKETS), places=6)
        # The other way round (the WITH run higher) is as before.
        up = _trace_bracket_rows(TE.US_ORD_BRACKETS, 46000.0, 49000.0)
        self.assertEqual(up[-1], ("48,475-49,000 @ 22.00%", 0.0, 115.5))

    def test_verbose_trace(self):
        root = _us_project(_tmp(self) / "p", 2025, (
            "BUYSELL 2025-02-03 10:00:00 ZZL.US 10 USD 100.00 -1000.00 0\n"
            "BUYSELL 2025-03-03 10:00:00 ZZL.US -10 USD 10.00 100.00 0\n"))
        _ok(_cli(root, "run", "--no-input"))
        r = _ok(_cli(root, "estimate", "--other-income", "64750",
                     "--verbose"))
        self.assertIn("48,475-49,000 @ 22.00% 115.50 0.00", _said(r))
        self.assertIn("11,925-48,475 @ 12.00% 4,386.00 4,341.00",
                      _said(r))


# ------------------------------------------------------------- TJ-0464
# 2024: a short-term loss of 900; 2025: a short-term gain of 50.
_BOOK = ("BUYSELL 2024-02-01 10:00:00 ZZL.US 10 USD 100.00 -1000.00 0\n"
         "BUYSELL 2024-09-03 10:00:00 ZZL.US -10 USD 10.00 100.00 0\n"
         "BUYSELL 2025-02-03 10:00:00 ZZM.US 1 USD 100.00 -100.00 0\n"
         "BUYSELL 2025-06-02 10:00:00 ZZM.US -1 USD 150.00 150.00 0\n")


class TestClaimedDeduction(unittest.TestCase):
    """TJ-0464: close-year's US lock took the carryover from the
    estimate, which counted the deduction as used only as far as its
    own taxable income absorbed it — none with no other income — and
    never read [carryover] claimed."""

    @rule("US-EST-CARRY-TI")
    def test_estimate_counts_the_claimed_deduction(self):
        r = _usa(st=-900.0, deduction_claimed=400.0)
        self.assertEqual(r["ordinary_offset"], 900.0)
        self.assertEqual(r["offset_used_for_carryover"], 400.0)
        self.assertEqual(r["offset_used_from"], "claimed")
        self.assertEqual(r["st_carryover_after"], 500.0)
        self.assertEqual(r["losses_unused"], 500.0)
        # More than the loss allows is capped at it; none given: the
        # taxable income decides (0 here: no other income).
        self.assertEqual(_usa(st=-900.0, deduction_claimed=990.0)
                         ["st_carryover_after"], 0.0)
        plain = _usa(st=-900.0)
        self.assertEqual(plain["st_carryover_after"], 900.0)
        self.assertEqual(plain["offset_used_from"], "taxable_income")
        # The tax itself is the estimate's: unchanged by the entry.
        self.assertEqual(r["estimated_tax"], plain["estimated_tax"])

    def _closed(self, tail):
        top = _tmp(self) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(_BOOK)
        (top / "2024").mkdir()
        (top / "2024" / "taxjson.toml").write_text(
            _us_toml(2024, tail, "../inputs"))
        _ok(_cli(top / "2024", "run", "--no-input"))
        close = _ok(_cli(top / "2024", "close-year", "--yes"))
        lock = json.loads((top / "2024" / "filed" / "2024.json")
                          .read_text())
        return top, close, lock["carryforwards"]["capital_loss"]

    @rule("US-CARRY-01", "US-CARRY-04", "US-CARRY-03")
    def test_lock_ledger_and_handoff(self):
        top, close, cl = self._closed(
            "\n[carryover]\nclaimed = { 2024 = 400.00 }\n")
        self.assertEqual((cl["st_closing"], cl["lt_closing"]),
                         (500.0, 0.0))
        self.assertEqual(cl["deduction_used"], 400.0)
        self.assertEqual(cl["deduction_from"], "[carryover] claimed")
        self.assertIn("Capital loss carryover into 2025: short-term "
                      "500.00, long-term 0.00", _said(close))
        self.assertIn("Deducted in 2024: 400.00 ([carryover] claimed)",
                      _said(close))
        _ok(_cli(top / "2024", "new-year", "2025"))
        y5 = top / "2025"
        _ok(_cli(y5, "run", "--no-input"))
        rows = json.loads(_ok(_cli(y5, "carryover", "--json")).stdout)[
            "rows"]
        r24, r25 = rows
        self.assertEqual((r24["ordinary_income_offset"],
                          r24["st_carryover"]), (400.0, 500.0))
        self.assertNotIn("claimed_offset", r24)
        self.assertEqual((r25["ordinary_income_offset"],
                          r25["st_carryover"]), (450.0, 0.0))
        est = json.loads(_ok(_cli(y5, "estimate", "--json")).stdout)[
            "estimate"]
        self.assertEqual(est["carryover_short_term"], 500.0)
        h = _ok(_cli(y5, "handoff", "--json"))
        self.assertEqual(json.loads(h.stdout)["carry"], [])
        # A claimed entry for 2024 that differs from what the record
        # counted is named, and the ledger says so.
        t = (y5 / "taxjson.toml").read_text()
        (y5 / "taxjson.toml").write_text(
            t.replace("claimed = { 2024 = 400.0 }",
                      "claimed = { 2024 = 300.0 }"))
        h = _cli(y5, "handoff", "--json")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        (item,) = json.loads(h.stdout)["carry"]
        self.assertEqual((item["what"], item["here"], item["record"]),
                         ("[carryover] claimed 2024", 300.0, 400.0))
        led = _ok(_cli(y5, "carryover"))
        self.assertIn("2024: claimed 300.00, its lock deducted 400.00",
                      _said(led))

    @rule("US-CARRY-01")
    def test_no_claim_no_income_is_said(self):
        _top, close, cl = self._closed("")
        self.assertEqual((cl["st_closing"], cl["deduction_used"],
                          cl["deduction_available"]), (900.0, 0.0, 900.0))
        self.assertEqual(cl["deduction_from"],
                         "the estimate's taxable income")
        self.assertIn("no [estimate] other_income and no [carryover] "
                      "claimed entry for 2024: 0.00 of the 900.00 "
                      "deduction against ordinary income is counted as "
                      "used", _said(close))
        # With other income the estimate's taxable income absorbs it:
        # no note.
        _top, close, cl = self._closed(
            "\n[estimate]\nother_income = 80000\n")
        self.assertEqual((cl["st_closing"], cl["deduction_used"]),
                         (0.0, 900.0))
        self.assertNotIn("counted as used", _said(close))

    @rule("US-CARRY-03", "US-CARRY-04")
    def test_older_lock_without_the_deduction(self):
        """A lock an older release wrote (no deduction_used) carried the
        whole loss: the ledger's row shows the offset its balance
        implies (0), and handoff names the claimed entry."""
        top, _close, _cl = self._closed("")
        p = top / "2024" / "filed" / "2024.json"
        doc = json.loads(p.read_text())
        for k in ("deduction_used", "deduction_available",
                  "deduction_from", "other_income",
                  "other_income_entered"):
            doc["carryforwards"]["capital_loss"].pop(k)
        p.write_text(json.dumps(doc, indent=2))
        self.assertEqual(CF.us_deduction_used(doc), (0.0, 900.0))
        _ok(_cli(top / "2024", "new-year", "2025"))
        y5 = top / "2025"
        t = (y5 / "taxjson.toml").read_text()
        (y5 / "taxjson.toml").write_text(
            t + "\n[carryover]\nclaimed = { 2024 = 400.00 }\n")
        _ok(_cli(y5, "run", "--no-input"))
        r24 = json.loads(_ok(_cli(y5, "carryover", "--json")).stdout)[
            "rows"][0]
        self.assertEqual((r24["ordinary_income_offset"],
                          r24["claimed_offset"], r24["st_carryover"]),
                         (0.0, 400.0, 900.0))
        h = _cli(y5, "handoff")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        self.assertIn("[carryover] claimed records 400.00 deducted "
                      "against ordinary income on the 2024 return",
                      _said(h))


# ------------------------------------------------------------- TJ-0554
def _lots_book(first, second, sale_year=None):
    """Two lots of ZZQ.US bought in 2024 at `first` then `second`; a
    sale of one unit at 30.00 in `sale_year`."""
    book = (f"BUYSELL 2024-03-01 10:00:00 ZZQ.US 1 USD {first:.2f} "
            f"-{first:.2f} 0\n"
            f"BUYSELL 2024-06-03 10:00:00 ZZQ.US 1 USD {second:.2f} "
            f"-{second:.2f} 0\n")
    if sale_year:
        book += (f"BUYSELL {sale_year}-06-02 10:00:00 ZZQ.US -1 USD 30.00 "
                 f"30.00 0\n")
    return book


class TestHandoffComparesLots(unittest.TestCase):
    """TJ-0554: the record kept each security's units and total basis
    only, so an opening whose lots carry each other's cost (the same
    total) passed, and the next year's FIFO sale took the other lot's
    basis."""

    def _closed_2024(self, top):
        p24 = _us_project(top / "y24", 2024, _lots_book(10.0, 20.0))
        _ok(_cli(p24, "run", "--no-input"))
        # No sale in 2024: --force locks the year anyway.
        _ok(_cli(p24, "close-year", "--yes", "--force"))
        return p24 / "filed" / "2024.json"

    def _handoff(self, top, first, second):
        rec = self._closed_2024(top)
        p25 = _us_project(top / "y25", 2025,
                          _lots_book(first, second, 2025))
        _ok(_cli(p25, "run", "--no-input"))
        return rec, _cli(p25, "handoff", "--prior", rec, "--json")

    @rule("US-RPT-06")
    def test_the_lots_are_recorded(self):
        rec = json.loads(self._closed_2024(_tmp(self)).read_text())
        lots = rec["year_end_lots"]["equity"]["ZZQ.US"]
        self.assertEqual(
            [(x["account"], x["date"], x["qty"], x["basis"])
             for x in lots],
            [("margin", "2024-03-01", 1.0, 10.0),
             ("margin", "2024-06-03", 1.0, 20.0)])

    @rule("US-RPT-06")
    def test_swapped_costs_are_a_problem(self):
        _rec, h = self._handoff(_tmp(self), 20.0, 10.0)
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        rep = json.loads(h.stdout)
        (item,) = rep["positions"]
        self.assertEqual(item["symbol"], "ZZQ.US")
        self.assertEqual((item["qty_diff"], item["acb_diff"]), (0.0, 0.0))
        self.assertEqual(
            [(x["date"], x["basis"]) for x in item["lots"]["closed"]],
            [("2024-03-01", 10.0), ("2024-06-03", 20.0)])
        self.assertEqual(
            [(x["date"], x["basis"]) for x in item["lots"]["opening"]],
            [("2024-03-01", 20.0), ("2024-06-03", 10.0)])
        self.assertIn("lot by lot", item["why"])

    @rule("US-RPT-06")
    def test_the_same_lots_pass(self):
        _rec, h = self._handoff(_tmp(self), 10.0, 20.0)
        self.assertEqual(h.returncode, 0, h.stdout + h.stderr)
        rep = json.loads(h.stdout)
        self.assertEqual(rep["positions"], [])
        self.assertFalse([n for n in rep["notes"] if "lot" in n])

    def test_a_record_without_lots_is_said(self):
        top = _tmp(self)
        rec = self._closed_2024(top)
        doc = json.loads(rec.read_text())
        doc.pop("year_end_lots")
        rec.write_text(json.dumps(doc, indent=2))
        p25 = _us_project(top / "y25", 2025, _lots_book(20.0, 10.0, 2025))
        _ok(_cli(p25, "run", "--no-input"))
        h = _ok(_cli(p25, "handoff", "--prior", rec))
        self.assertIn("records no lots", _said(h))


# ------------------------------------------------------------- TJ-0555
class TestHandoffSkippedYear(unittest.TestCase):
    """TJ-0555: a 2026 project checked against the 2024 record exited 0
    with problems 0; only a stderr note named the years."""

    def test_a_record_two_years_back_is_a_problem(self):
        top = _tmp(self)
        p24 = _us_project(top / "y24", 2024, _lots_book(10.0, 20.0))
        _ok(_cli(p24, "run", "--no-input"))
        _ok(_cli(p24, "close-year", "--yes", "--force"))
        rec = p24 / "filed" / "2024.json"
        p26 = _us_project(top / "y26", 2026, _lots_book(10.0, 20.0, 2026))
        _ok(_cli(p26, "run", "--no-input"))
        h = _cli(p26, "handoff", "--prior", rec, "--json")
        self.assertEqual(h.returncode, 1, h.stdout + h.stderr)
        rep = json.loads(h.stdout)
        self.assertEqual(rep["positions"], [])
        (item,) = rep["partial"]
        self.assertEqual((item["record_year"], item["project_year"]),
                         (2024, 2026))
        self.assertIn("2025", item["why"])
        self.assertEqual(rep["problems"], 1)
        text = _cli(p26, "handoff", "--prior", rec)
        self.assertEqual(text.returncode, 1)
        self.assertIn("HAND-OFF CHECK — 2024 (closed) into 2026 (this "
                      "project)", _said(text))
        self.assertIn("- the record is for 2024, this project's year is "
                      "2026", _said(text))
        self.assertIn("1 problem(s).", _said(text))
        # The checklist's handoff step names it (not "a partial-year
        # snapshot").
        from datetime import date
        from taxjson.lib import checklist as cl
        ctx = cl.Ctx(root=p26, cfg={"settings": {
            "year": 2026, "prior_year_record": str(rec)}}, year=2026,
            today=date(2027, 3, 1),
            run_sub=lambda argv, timeout=900: (1, h.stdout, ""))
        r = cl.d_handoff(ctx)
        self.assertEqual(r.status, "attention")
        self.assertEqual(r.detail, "the record is for 2024, not 2025 — "
                                   "`taxjson handoff`")


# ------------------------------------------------------------- TJ-0466
_IB_HEAD = ("Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
            "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,MTM P/L,Code\n")
# An SPX call (a broad-based index option: §1256) closed at a 402.00
# loss, and a stock sold at a 100.00 gain, in 2025.
_IB_1256 = (
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n" + _IB_HEAD
    + 'Trades,Data,Order,Equity and Index Options,USD,SPX 21MAR25 5000 C,'
      '"2025-02-03, 10:00:00",1,5.00,5.00,-500.00,-1.00,501.00,0,0,O\n'
    + 'Trades,Data,Order,Equity and Index Options,USD,SPX 21MAR25 5000 C,'
      '"2025-03-03, 10:00:00",-1,1.00,1.00,100.00,-1.00,-501.00,-402.00,0,'
      'C\n'
    + 'Trades,Data,Order,Stocks,USD,ZZM,"2025-02-03, 10:00:00",10,100.00,'
      '100.00,-1000.00,0,1000.00,0,0,O\n'
    + 'Trades,Data,Order,Stocks,USD,ZZM,"2025-06-02, 10:00:00",-10.0,110.00,'
      '110.00,1100.00,0,-1000.00,100.00,0,C\n')


class TestSection1256CarriedShortTerm(unittest.TestCase):
    """TJ-0466: carryover and the close-year lock carried §1256 P/L as
    all short-term without a word (the estimate names it). Round 3 (the
    owner's decision) splits it as Form 6781 does, 40% short-term and
    60% long-term: test_fix_1029_r3_us1256 has the rest."""

    def _project(self):
        root = _tmp(self) / "p"
        (root / "inputs" / "a").mkdir(parents=True)
        (root / "inputs" / "a" / "a.csv").write_text(_IB_1256)
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "usa"\nyear = 2025\n'
            'base_currency = "USD"\n\n[accounts.a]\ntype = "taxable"\n'
            '\n[carryover]\nclaimed = { 2025 = 0 }\n')
        _ok(_cli(root, "run", "--no-input"))
        return root

    @rule("US-FUT-03")
    def test_carryover_and_close_year_say_so(self):
        root = self._project()
        led = json.loads(_ok(_cli(root, "carryover", "--json")).stdout)
        (row,) = led["rows"]
        # 100.00 + 40% of -402.00 short-term, 60% long-term.
        self.assertEqual((row["net_st"], row["st_carryover"]),
                         (-60.8, 60.8))
        self.assertEqual((row["net_lt"], row["lt_carryover"]),
                         (-241.2, 241.2))
        self.assertEqual(row["section_1256"], -402.0)
        full = _said(_ok(_cli(root, "carryover", "--details")))
        self.assertIn("as Form 6781 splits it, whatever the holding "
                      "period", full)
        close = _ok(_cli(root, "close-year", "--yes", "--details"))
        self.assertIn("the carryover takes the 2025 §1256 net, -402.00, "
                      "as Form 6781 splits it", _said(close))
        lock = json.loads((root / "filed" / "2025.json").read_text())
        cl = lock["carryforwards"]["capital_loss"]
        self.assertEqual((cl["st_closing"], cl["lt_closing"],
                          cl["section_1256_gain"]), (60.8, 241.2, -402.0))

    @rule("US-FUT-02")
    def test_no_note_without_a_carryover(self):
        root = _us_project(_tmp(self) / "p", 2025, (
            "BUYSELL 2025-02-03 10:00:00 ZZM.US 1 USD 100.00 -100.00 0\n"
            "BUYSELL 2025-06-02 10:00:00 ZZM.US -1 USD 150.00 150.00 0\n"))
        _ok(_cli(root, "run", "--no-input"))
        self.assertNotIn("§1256", _said(_ok(_cli(root, "carryover",
                                                  "--details"))))


# ------------------------------------------------------------- TJ-0470
class TestEstimateSaysWhereNoCarryoverCameFrom(unittest.TestCase):
    """TJ-0470: with no lock and nothing entered the US estimate used a
    0 carryover in silence, while `tjs carryover` on the same books
    showed one carried in."""

    def test_no_lock_no_entry(self):
        # 2024: a short-term loss of 3,400 (400 left after the 3,000
        # deduction); 2025: a gain of 50.
        root = _us_project(_tmp(self) / "p", 2025, (
            "BUYSELL 2024-02-01 10:00:00 ZZL.US 10 USD 400.00 -4000.00 0\n"
            "BUYSELL 2024-09-03 10:00:00 ZZL.US -10 USD 60.00 600.00 0\n"
            + _BOOK.split("\n", 2)[2]))
        _ok(_cli(root, "run", "--no-input"))
        led = json.loads(_ok(_cli(root, "carryover", "--json")).stdout)
        self.assertEqual(led["rows"][0]["st_carryover"], 400.0)
        out = _said(_ok(_cli(root, "estimate")))
        self.assertIn("Capital loss carryovers in: 0.00 — no close-year "
                      "lock before 2025 and none entered: see `tjs "
                      "carryover`", out)
        est = json.loads(_ok(_cli(root, "estimate", "--json")).stdout)[
            "estimate"]
        self.assertIsNone(est["carry_sources"]["other_losses"])
        # Entered: its source, as before.
        out = _said(_ok(_cli(root, "estimate", "--other-losses", "40")))
        self.assertIn("Capital loss carryovers in: short-term 40.00, "
                      "long-term 0.00 — from --other-losses", out)
        self.assertNotIn("none entered", out)


# ------------------------------------------------------------- TJ-0473
class TestCountryTerms(unittest.TestCase):
    """TJ-0473: Canadian terms in US output (a T1A carry-back, `amt`
    reading the lock) and a US one (the IRA) in Canada's checklist."""

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
        from taxjson.bin.taxjson_run import _carryforwards_summary
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
    (t1135, instalments, amt; claimed as line 25300 / inclusion rate)."""

    def test_us_section(self):
        doc = (REPO_ROOT / "docs" / "filing.md").read_text()
        self.assertNotIn("the same steps apply with the form names "
                         "swapped", doc)
        us = doc[doc.index("\n## A US return\n"):]
        flat = " ".join(us.split())
        for want in ("taxjson form-export --form 8949", "Form 6781 by hand",
                     "Schedule D line 21", "taxjson reconcile-slips",
                     "refuse a US project"):
            self.assertIn(want, flat)
        self.assertNotIn("25300", us)
        from taxjson.lib.country import COMMAND_COUNTRY
        # Each Canada-only command the doc names is said to be.
        intro = " ".join(doc[:doc.index("## 1.")].split())
        for cmd, owner in COMMAND_COUNTRY.items():
            if owner == "canada" and ":" not in cmd \
                    and f"`taxjson {cmd}`" in doc:
                self.assertTrue(f"`{cmd}`" in intro
                                or f"`taxjson {cmd}`" in intro, cmd)


if __name__ == "__main__":
    unittest.main()
