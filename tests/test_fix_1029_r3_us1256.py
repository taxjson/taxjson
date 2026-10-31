"""The 2026-10-29 fix round 3, US §1256 group: the experimental US
engine's Form 6781 60/40 split and option-replacement wording.

- TJ-0466 the year's net §1256 gain or loss (futures, options on
  futures, broad-based index options) is 40% short-term and 60%
  long-term, whatever the holding period (IRC §1256(a)(3); Form 6781
  lines 8 and 9), in the US estimate, `tjs carryover`'s ledger and the
  close-year lock: they carried it all short-term (the owner's decision
  of 2026-10-29; year-end marking and the §1212(c) carryback stay by
  hand);
- TJ-0611 a locked US year's row in the next project's ledger takes the
  lock's Form 6781 line as the lock's carryover took it, so it adds up to
  the balance carried; a lock closed before the split says so; the
  project year's own lock no longer "differs" from the books by its
  §1256 net;
- TJ-0619 the US option-replacement flag names the contracts bought, not
  a fraction of one, when a call covers only part of its shares.

Synthetic data only (made-up tickers, account U1234567; amounts under a
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


def _env():
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _cli(root, *args):
    """`tjs -C ROOT ARGS` as a child of THIS tree's code."""
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


_IB_HEAD = ("Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
            "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,MTM P/L,Code\n")
# An SPX call (a broad-based index option: §1256) closed at a 402.00
# loss, and a stock sold at a 100.00 gain, in 2025. Form 6781 splits the
# -402.00 into -160.80 short-term and -241.20 long-term, so Schedule D
# has ST 100.00 - 160.80 = -60.80 and LT -241.20.
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

_TOML = ('[settings]\ncountry = "usa"\nyear = 2025\nbase_currency = "USD"\n'
         '{inputs}\n[accounts.a]\ntype = "taxable"\n'
         '\n[carryover]\nclaimed = {{ 2025 = 0 }}\n')


# ------------------------------------------------------------- TJ-0466
class TestSection1256Split(unittest.TestCase):
    """TJ-0466: Form 6781's 60/40 split in the estimate's arithmetic."""

    @rule("US-FUT-03")
    def test_split_adds_up(self):
        st, lt = TE.section_1256_split(-402.0)
        self.assertAlmostEqual(st, -160.8, places=9)
        self.assertAlmostEqual(lt, -241.2, places=9)
        self.assertAlmostEqual(st + lt, -402.0, places=9)
        self.assertEqual(TE.section_1256_split(0.0), (0.0, 0.0))

    @rule("US-FUT-03", "US-RPT-07")
    def test_estimate_carries_each_part_in_its_term(self):
        # The finder's figures: a 100 short-term gain and a 4002 §1256
        # loss. No other income: nothing deducted, the carryover is ST
        # 1500.80 and LT 2401.20 (all short-term before).
        r = TE.estimate_usa(st=100.0, lt=0.0, qualified_div=0.0,
                            pil=0.0, other_income=0.0, other_losses=0.0,
                            year=2025, section_1256=-4002.0)
        self.assertEqual((r["st_carryover_after"], r["lt_carryover_after"]),
                         (1500.8, 2401.2))
        self.assertEqual((r["section_1256_short_term"],
                          r["section_1256_long_term"]), (-1600.8, -2401.2))
        self.assertEqual((r["st_input"], r["lt_input"]), (-1500.8, -2401.2))
        # With income to absorb the 3000 deduction (short-term first):
        # ST 0, LT 902.
        r = TE.estimate_usa(st=100.0, lt=0.0, qualified_div=0.0,
                            pil=0.0, other_income=100000.0,
                            other_losses=0.0, year=2025,
                            section_1256=-4002.0)
        self.assertEqual((r["st_carryover_after"], r["lt_carryover_after"]),
                         (0.0, 902.0))

    @rule("US-FUT-03")
    def test_a_section_1256_gain_is_taxed_60_percent_long_term(self):
        kw = dict(qualified_div=0.0, pil=0.0, other_income=100000.0,
                  other_losses=0.0, year=2025)
        split = TE.estimate_usa(st=0.0, lt=0.0, section_1256=10000.0, **kw)
        by_hand = TE.estimate_usa(st=4000.0, lt=6000.0, **kw)
        self.assertEqual(split["estimated_tax"], by_hand["estimated_tax"])
        all_short = TE.estimate_usa(st=10000.0, lt=0.0, **kw)
        self.assertLess(split["estimated_tax"], all_short["estimated_tax"])

    def test_no_section_1256_nothing_moves(self):
        kw = dict(st=1234.5, lt=-77.0, qualified_div=0.0, pil=0.0,
                  other_income=50000.0, other_losses=10.0, year=2025)
        a, b = TE.estimate_usa(**kw), TE.estimate_usa(section_1256=0.0, **kw)
        self.assertEqual(a, b)
        self.assertEqual(a["section_1256_gain"], 0.0)


class TestSection1256OneProject(unittest.TestCase):
    """TJ-0466 (and TJ-0611's project-year lock): one US project with a
    §1256 loss; carryover, estimate and close-year split it 60/40."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name) / "p"
        (root / "inputs" / "a").mkdir(parents=True)
        (root / "inputs" / "a" / "a.csv").write_text(_IB_1256)
        (root / "taxjson.toml").write_text(_TOML.format(inputs=""))
        _ok(_cli(root, "run", "--no-input"))
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule("US-FUT-03")
    def test_1_carryover_splits_it(self):
        led = json.loads(_ok(_cli(self.root, "carryover", "--json")).stdout)
        (row,) = led["rows"]
        self.assertEqual((row["net_st"], row["net_lt"]), (-60.8, -241.2))
        self.assertEqual((row["st_carryover"], row["lt_carryover"]),
                         (60.8, 241.2))
        self.assertEqual((row["section_1256"],
                          row["section_1256_short_term"],
                          row["section_1256_long_term"]),
                         (-402.0, -160.8, -241.2))
        self.assertNotIn("section_1256_unsplit", row)
        brief = _said(_ok(_cli(self.root, "carryover")))
        self.assertNotIn("carried as short-term", brief)
        self.assertNotIn("§1256 P/L all short-term", brief)
        full = _said(_ok(_cli(self.root, "carryover", "--details")))
        self.assertIn("2025: NET ST and NET LT take its -402.00 of §1256 "
                      "P/L (futures, futures options, broad-based index "
                      "options) as Form 6781 splits it, whatever the "
                      "holding period: -160.80 short-term (40%), -241.20 "
                      "long-term (60%).", full)
        self.assertIn("is not carried back (§1212(c))", full)

    @rule("US-FUT-03", "US-RPT-07")
    def test_2_estimate_splits_it(self):
        est = json.loads(_ok(_cli(self.root, "estimate", "--json")
                             ).stdout)["estimate"]
        self.assertEqual((est["section_1256_gain"],
                          est["section_1256_short_term"],
                          est["section_1256_long_term"]),
                         (-402.0, -160.8, -241.2))
        self.assertEqual((est["st_input"], est["lt_input"]),
                         (-60.8, -241.2))
        self.assertTrue(any("split 60/40 as Form 6781 does" in n
                            for n in est["notes"]), est["notes"])
        text = _said(_ok(_cli(self.root, "estimate")))
        self.assertIn("Long-term gains (net) 0.00 [-241.20 less carryover "
                      "0.00]", text)

    @rule("US-FUT-03")
    def test_3_close_year_lock_and_its_own_year(self):
        close = _ok(_cli(self.root, "close-year", "--yes", "--details"))
        said = _said(close)
        self.assertIn("Capital loss carryover into 2026: short-term 60.80, "
                      "long-term 241.20", said)
        self.assertIn("the carryover takes the 2025 §1256 net, -402.00, as "
                      "Form 6781 splits it: -160.80 short-term (40%), "
                      "-241.20 long-term (60%)", said)
        lock = json.loads((self.root / "filed" / "2025.json").read_text())
        cl = lock["carryforwards"]["capital_loss"]
        self.assertEqual((cl["st_closing"], cl["lt_closing"]),
                         (60.8, 241.2))
        self.assertEqual((cl["section_1256_gain"],
                          cl["section_1256_short_term"],
                          cl["section_1256_long_term"]),
                         (-402.0, -160.8, -241.2))
        # TJ-0611: the project year's lock is compared with the books'
        # net, its §1256 line included: it "differed" by the -402.00.
        again = _ok(_cli(self.root, "carryover", "--json"))
        (row,) = json.loads(again.stdout)["rows"]
        self.assertEqual(row["filed_realized"], -302.0)
        self.assertNotIn("differs_from_filed", row)
        self.assertNotIn("differs from the filed lock", _said(again))


class TestPlotTaxMonthsSplitToo(unittest.TestCase):
    """TJ-0466: plot-tax runs the estimate through each month's end; a
    month splits the §1256 P/L it holds as the year does, so the months
    add up to the year's estimate (no "dated to the year" line)."""

    @rule("US-FUT-03", "US-PLAN-09")
    def test_a_section_1256_gain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "a").mkdir(parents=True)
            # An SPX call closed at a 398.00 gain in March.
            (root / "inputs" / "a" / "a.csv").write_text(
                _IB_1256.replace(
                    '-1,1.00,1.00,100.00,-1.00,-501.00,-402.00',
                    '-1,9.00,9.00,900.00,-1.00,-501.00,398.00'))
            (root / "taxjson.toml").write_text(_TOML.format(inputs=""))
            _ok(_cli(root, "run", "--no-input"))
            est = json.loads(_ok(_cli(root, "estimate", "--other-income",
                                      "100000", "--json")).stdout
                             )["estimate"]
            self.assertEqual((est["section_1256_short_term"],
                              est["section_1256_long_term"]),
                             (159.2, 238.8))
            doc = json.loads(_ok(_cli(root, "plot-tax", "--other-income",
                                      "100000", "--json")).stdout)
            self.assertEqual(doc["year_estimate"], est["estimated_tax"])
            self.assertEqual(doc["months_total"], est["estimated_tax"])
            self.assertEqual(doc["year_adjustment"], 0.0)


# ------------------------------------------------------------- TJ-0611
class TestLockedYearRowAddsUp(unittest.TestCase):
    """TJ-0611: a per-year layout whose 2025 lock holds a §1256 loss;
    the 2026 project's ledger row for 2025 adds up to the carryover."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "books"
        (top / "inputs" / "a").mkdir(parents=True)
        (top / "inputs" / "a" / "a.csv").write_text(_IB_1256)
        (top / "2025").mkdir()
        (top / "2025" / "taxjson.toml").write_text(
            _TOML.format(inputs='inputs_dir = "../inputs"\n'))
        _ok(_cli(top / "2025", "run", "--no-input"))
        _ok(_cli(top / "2025", "close-year", "--yes"))
        _ok(_cli(top / "2025", "new-year", "2026"))
        _ok(_cli(top / "2026", "run", "--no-input"))
        cls.top = top
        cls.lock = top / "2025" / "filed" / "2025.json"

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _row(self):
        r = _ok(_cli(self.top / "2026", "carryover", "--json"))
        rows = [x for x in json.loads(r.stdout)["rows"]
                if x["year"] == 2025]
        self.assertEqual(len(rows), 1, r.stdout)
        return rows[0], r

    @rule("US-FUT-03", "US-CARRY-04")
    def test_1_row_adds_up_to_the_carry(self):
        row, r = self._row()
        self.assertTrue(row["from_lock"])
        self.assertTrue(row["balance_from_lock"])
        self.assertEqual((row["net_st"], row["net_lt"]), (-60.8, -241.2))
        self.assertEqual((row["st_carryover"], row["lt_carryover"]),
                         (60.8, 241.2))
        # [carryover] claimed 2025 = 0: nothing deducted, so the carry is
        # the nets' loss.
        self.assertEqual(row["ordinary_income_offset"], 0.0)
        self.assertEqual(round(-(row["net_st"] + row["net_lt"]), 2),
                         round(row["st_carryover"] + row["lt_carryover"], 2))
        # The lock's figure is the books' net: no "uses the filed figure"
        # note for a §1256 net the figure left out.
        self.assertEqual((row["filed_realized"], row["rebuilt_net_gain"]),
                         (-302.0, -302.0))
        self.assertNotIn("uses the filed figure", _said(r))

    @rule("US-FUT-03")
    def test_2_a_lock_closed_before_the_split(self):
        # The lock as an earlier taxjson wrote it: no Form 6781 parts,
        # the whole §1256 loss carried short-term.
        doc = json.loads(self.lock.read_text())
        saved = self.lock.read_text()
        self.addCleanup(self.lock.write_text, saved)
        cl = doc["carryforwards"]["capital_loss"]
        del cl["section_1256_short_term"], cl["section_1256_long_term"]
        cl["st_closing"], cl["lt_closing"] = 302.0, 0.0
        self.lock.write_text(json.dumps(doc))
        row, _r = self._row()
        self.assertTrue(row["section_1256_unsplit"])
        self.assertEqual((row["net_st"], row["net_lt"]), (-302.0, 0.0))
        self.assertEqual((row["st_carryover"], row["lt_carryover"]),
                         (302.0, 0.0))
        brief = _said(_ok(_cli(self.top / "2026", "carryover")))
        self.assertIn("! 2025: its lock carries -402.00 of §1256 P/L all "
                      "short-term (closed before the 60/40 split)", brief)
        full = _said(_ok(_cli(self.top / "2026", "carryover", "--details")))
        self.assertIn("2025's close-year lock was closed before taxjson "
                      "split §1256 P/L as Form 6781 does", full)
        self.assertIn("Close 2025 again with --force in its project.", full)
        est = _said(_ok(_cli(self.top / "2026", "estimate", "--details")))
        self.assertIn("was closed before taxjson split §1256 P/L as Form "
                      "6781 does: its carryover counts its -402.00 of §1256 "
                      "P/L as short-term", est)

    @rule("US-FUT-03")
    def test_4_an_old_lock_that_carried_nothing_needs_no_new_close(self):
        # The split moves a carryover between its terms, never its
        # total: an earlier lock that carried no loss out is right.
        from taxjson.bin.taxjson_carryover import us_lock_1256
        doc = json.loads(self.lock.read_text())
        cl = doc["carryforwards"]["capital_loss"]
        del cl["section_1256_short_term"], cl["section_1256_long_term"]
        self.assertEqual(us_lock_1256(doc), (-402.0, True))
        cl["st_closing"] = cl["lt_closing"] = 0.0
        self.assertEqual(us_lock_1256(doc), (-402.0, False))

    @rule("US-FUT-03")
    def test_3_deduction_cap_counts_the_section_1256_loss(self):
        # us_deduction_used read the year's loss from the Form 8949 lines
        # only: a year whose loss is all §1256 allowed no deduction, so a
        # [carryover] claimed entry looked like a mismatch.
        rec = {"year": 2025, "accounts": {"a": {"form_lines": {
            "I_gain": 100.0, "6781_gain": -4002.0}}},
            "carryforwards": {"capital_loss": {
                "st_opening": 0.0, "lt_opening": 0.0,
                "st_closing": 0.0, "lt_closing": 902.0,
                "deduction_used": 3000.0,
                "section_1256_gain": -4002.0,
                "section_1256_short_term": -1600.8,
                "section_1256_long_term": -2401.2}}}
        self.assertEqual(CF.us_deduction_used(rec), (3000.0, 3000.0))


# ------------------------------------------------------------- TJ-0619
def _us_gains(book):
    """The US engine's run_gains on `book` (stderr captured)."""
    import contextlib
    import io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), [], [],
                        req=GainsRequest(country="usa", taxable=True,
                                         year=2025))
    res["_stderr"] = err.getvalue()
    return res


def _tx(*a, **k):
    from tax_rules.dual import tx
    return tx(*a, **k)


class TestOptionReplacementNamesContractsBought(unittest.TestCase):
    """TJ-0619: a call that covers only part of its shares against a
    loss was named as a fraction of a contract ("0.1 contract(s) x 100
    shares")."""

    CALL = "ZZQ250620C00050000.US"

    def _warn(self, book):
        from taxjson.lib.core import format_option_replacement_warning
        r = _us_gains(book)
        (w,) = r.get("option_replacement_warnings") or []
        return w, format_option_replacement_warning(w, country="usa")

    @rule("US-WASH-12")
    def test_ten_shares_left_for_the_call(self):
        # 100 ZZQ sold at a 100.00 loss; 90 bought back in the window
        # (they replace 90 of the loss shares); then one call: 10 of
        # its 100 shares count against the loss.
        w, text = self._warn([
            _tx("BUYSELL", "2025-01-02", "ZZQ.US", 100, -500),
            _tx("BUYSELL", "2025-03-03", "ZZQ.US", -100, 400),
            _tx("BUYSELL", "2025-03-10", "ZZQ.US", 90, -369),
            _tx("BUYSELL", "2025-03-12", self.CALL, 1, -200)])
        self.assertEqual((w["option_qty"], w["contract_size"],
                          round(w["covered_shares"], 6),
                          round(w["replaced_qty"], 6)),
                         (1.0, 100, 10.0, 90.0))
        self.assertNotIn("0.1 contract", text)
        self.assertIn("up to 10 of the 100 shares' loss (-10.00) would be "
                      "denied (1 contract(s) bought x 100 shares, each "
                      "share used once: 10 against this loss; 90 of the "
                      "loss shares are already replaced by shares bought "
                      "in the window, and denied)", text)

    @rule("US-WASH-12")
    def test_a_fifty_share_loss_and_one_call(self):
        w, text = self._warn([
            _tx("BUYSELL", "2025-01-02", "ZZQ.US", 50, -250),
            _tx("BUYSELL", "2025-03-03", "ZZQ.US", -50, 200),
            _tx("BUYSELL", "2025-03-12", self.CALL, 1, -200)])
        self.assertEqual((w["option_qty"], w["covered_shares"]),
                         (1.0, 50.0))
        self.assertIn("up to 50 of the 50 shares' loss (-50.00) would be "
                      "denied (1 contract(s) bought x 100 shares, each "
                      "share used once: 50 against this loss)", text)

    @rule("US-WASH-12")
    def test_whole_contracts_say_no_more(self):
        w, text = self._warn([
            _tx("BUYSELL", "2025-01-02", "ZZQ.US", 200, -1000),
            _tx("BUYSELL", "2025-03-03", "ZZQ.US", -200, 800),
            _tx("BUYSELL", "2025-03-12", self.CALL, 2, -400)])
        self.assertEqual((w["option_qty"], w["covered_shares"]),
                         (2.0, 200.0))
        self.assertIn("(2 contract(s) bought x 100 shares, each share used "
                      "once)", text)


if __name__ == "__main__":
    unittest.main()
