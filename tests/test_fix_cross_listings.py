"""Two listings of one security joined by their transfer journal
(CA-XLIST-01 / US-XLIST-01): when an out-leg of one listing and an
in-leg of another pair uniquely and the exports' security names agree,
`taxjson run` books them as one security, as a ticker.map TOBASE line
would — with one Warning per account naming the DISTINCT line that
undoes it. The names must be EQUAL word for word (pre-release review
H1): the corporate form and every designator count. A ticker.map rule (DISTINCT
included) wins; ambiguous pairs and disagreeing names stay suggestions.

Synthetic data only.
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib.country import COUNTRIES
from taxjson.lib.symbol_codes import exact_name
from tax_rules import rule
from tax_rules.dual import cli, projects_both


def _leg(sym, date, qty, account="rrsp"):
    return XL.Leg(account, "questrade", sym, date, qty)


def _names(**kw):
    return {s.replace("_", "."): {exact_name(n)} for s, n in kw.items()}


class TestExactNames(unittest.TestCase):
    """Pre-release review H1: the auto-join takes EQUAL names only —
    no subset rule, no designator stated by one side only, and the
    corporate form counts."""

    def _run(self, a, b):
        names = _names(SAMPQ_US=a, SAMPQ_TO=b)
        shown = {t: " ".join(t) for v in names.values() for t in v}
        return XL.analyze([_leg("SAMPQ.US", "2025-03-03", -100),
                           _leg("SAMPQ.TO", "2025-03-04", 100)],
                          names, shown, base_currency="CAD")

    @rule("CA-XLIST-01")
    def test_different_securities_are_only_suggested(self):
        for a, b in (("QZCO CORP", "QZCO CORP CL B"),
                     ("QZFIELD INFRASTRUCTURE PARTNERS LP",
                      "QZFIELD INFRASTRUCTURE CORP"),
                     ("QZALPHA BANK", "QZALPHA BANK OF CANADA"),
                     ("QZWORLD S&P 500 INDEX ETF",
                      "QZWORLD S&P 500 INDEX ETF CAD HEDGED"),
                     ("QZTRUST UNITS TRUST", "QZTRUST UNITS FUND"),
                     ("QZCO INC", "QZCO CORP"),
                     ("QZCO INC CLASS B SUB VTG", "QZCO INC CL B")):
            for x, y in ((a, b), (b, a)):
                with self.subTest(a=x, b=y):
                    r = self._run(x, y)
                    self.assertEqual(r["joined"], [])
                    self.assertEqual(len(r["suggested"]), 1)
                    self.assertTrue(r["suggested"][0].reason.startswith(
                        "the names are not equal word for word"))

    @rule("CA-XLIST-01")
    def test_spellings_of_the_same_words_still_join(self):
        for a, b in (("QZCO INC CL B SUB VTG", "QZCO INC CLASS B "
                      "SUBORDINATE VOTING SHARES"),
                     ("QZEN RES LTD", "QZEN RESOURCES LIMITED"),
                     ("QZX N.V. SPONSORED ADR", "QZX NV ADR"),
                     ("QZPIPE & CO", "QZPIPE AND COMPANY")):
            with self.subTest(a=a, b=b):
                self.assertEqual(len(self._run(a, b)["joined"]), 1)

    @rule("CA-XLIST-01")
    def test_another_name_of_a_listing_with_another_class_refuses(self):
        names = {"SAMPQ.US": {exact_name("QZCO CORP")},
                 "SAMPQ.TO": {exact_name("QZCO CORP"),
                              exact_name("QZCO CORP CL B")}}
        shown = {t: " ".join(t) for v in names.values() for t in v}
        r = XL.analyze([_leg("SAMPQ.US", "2025-03-03", -100),
                        _leg("SAMPQ.TO", "2025-03-04", 100)],
                       names, shown)
        self.assertEqual(r["joined"], [])
        self.assertTrue(r["suggested"][0].reason.startswith(
            "another name of the listings states another share"))

    @rule("US-XLIST-01")
    def test_usa_lp_never_joins_its_exchangeable_corp(self):
        r = self._run("QZFIELD INFRASTRUCTURE PARTNERS LP",
                      "QZFIELD INFRASTRUCTURE CORP")
        self.assertEqual(r["joined"], [])


class TestAnalyze(unittest.TestCase):
    """The pairing and the name test, on legs."""

    def _run(self, legs, names, **kw):
        shown = {t: " ".join(t) for v in names.values() for t in v}
        return XL.analyze(legs, names, shown, **kw)

    @rule("CA-XLIST-01")
    def test_unique_pair_with_agreeing_names_joins(self):
        r = self._run([_leg("SAMPQ.US", "2025-03-03", -100),
                       _leg("SAMPQ.TO", "2025-03-05", 100)],
                      _names(SAMPQ_US="SAMPQ ENERGY INC COMMON SHARES",
                             SAMPQ_TO="Sampq Energy Incorporated"),
                      base_currency="CAD")
        self.assertEqual([(p.frm, p.to) for p in r["joined"]],
                         [("SAMPQ.US", "SAMPQ.TO")])
        self.assertEqual(r["suggested"], [])
        self.assertEqual(XL.map_lines(r["joined"])[0].split("#")[0].strip(),
                         "TOBASE SAMPQ.US SAMPQ.TO")
        head, details = XL.joined_note("rrsp", r["joined"])
        self.assertEqual(head, "rrsp: joined as one security by their "
                         "transfer journal: SAMPQ.US ↔ SAMPQ.TO (transfer "
                         "2025-03-03)")
        self.assertIn("changes your books", details[0])
        self.assertIn("add `DISTINCT SAMPQ.US SAMPQ.TO` to ticker.map",
                      details[1])

    @rule("CA-XLIST-01")
    def test_a_map_rule_wins(self):
        r = self._run([_leg("SAMPQ.US", "2025-03-03", -100),
                       _leg("SAMPQ.TO", "2025-03-04", 100)],
                      _names(SAMPQ_US="SAMPQ ENERGY INC",
                             SAMPQ_TO="SAMPQ ENERGY INC"),
                      map_named={"SAMPQ.TO"})
        self.assertEqual(r, {"joined": [], "suggested": []})

    @rule("CA-XLIST-01")
    def test_distinct_keeps_only_its_own_pair_apart(self):
        legs = [_leg("SAMPQ.US", "2025-03-03", -100),
                _leg("SAMPQ.TO", "2025-03-04", 100)]
        names = _names(SAMPQ_US="SAMPQ ENERGY INC",
                       SAMPQ_TO="SAMPQ ENERGY INC")
        r = self._run(legs, names,
                      map_distinct=[("SAMPQ.TO", "SAMPQ.US")])
        self.assertEqual(r, {"joined": [], "suggested": []})
        r = self._run(legs, names,
                      map_distinct=[("SAMPQ.TO", "SAMPZ.US")])
        self.assertEqual(len(r["joined"]), 1)

    @rule("CA-XLIST-01")
    def test_ambiguous_pairing_is_only_suggested(self):
        r = self._run([_leg("SAMPQ.US", "2025-03-03", -100),
                       _leg("SAMPQ.TO", "2025-03-04", 100),
                       _leg("SAMPR.TO", "2025-03-04", 100)],
                      _names(SAMPQ_US="SAMPQ ENERGY INC",
                             SAMPQ_TO="SAMPQ ENERGY INC",
                             SAMPR_TO="SAMPQ ENERGY INC"))
        self.assertEqual(r["joined"], [])
        self.assertEqual({p.reason for p in r["suggested"]},
                         {"the legs pair with more than one other leg"})

    @rule("CA-XLIST-01")
    def test_names_missing_or_disagreeing_are_only_suggested(self):
        legs = [_leg("SAMPQ.US", "2025-03-03", -100),
                _leg("SAMPQ.TO", "2025-03-04", 100)]
        for names, why in (
                ({}, "no security name for either listing"),
                (_names(SAMPQ_US="SAMPQ ENERGY INC"),
                 "no security name for one listing"),
                (_names(SAMPQ_US="SAMPQ ENERGY INC CL A",
                        SAMPQ_TO="SAMPQ ENERGY INC CL B"),
                 "the names are not equal word for word ('SAMPQ ENERGY "
                 "INC CLASS A' vs 'SAMPQ ENERGY INC CLASS B')"),
                (_names(SAMPQ_US="SAMPQ ENERGY INC SPONSORED ADR",
                        SAMPQ_TO="SAMPQ ENERGY INC"),
                 "the names are not equal word for word ('SAMPQ ENERGY "
                 "INC ADR' vs 'SAMPQ ENERGY INC')"),
                (_names(SAMPQ_US="SAMPQ ENERGY INC PFD SER 2",
                        SAMPQ_TO="SAMPQ ENERGY INC"),
                 "the names are not equal word for word ('SAMPQ ENERGY "
                 "INC PREFERRED SERIES 2' vs 'SAMPQ ENERGY INC')"),
                (_names(SAMPQ_US="SAMPQ ENERGY INC",
                        SAMPQ_TO="QZWV MINING LTD"),
                 "the names are not equal word for word ('SAMPQ ENERGY "
                 "INC' vs 'QZWV MINING LTD')")):
            with self.subTest(why=why, names=names):
                r = self._run(legs, names)
                self.assertEqual(r["joined"], [])
                self.assertEqual([p.reason for p in r["suggested"]], [why])

    @rule("CA-XLIST-01")
    def test_same_symbol_legs_cancel_first(self):
        # A custody move of SAMPQ.US (out, back in) is not a journal to
        # the other listing that happens to share the quantity.
        r = self._run([_leg("SAMPQ.US", "2025-03-03", -100),
                       _leg("SAMPQ.US", "2025-03-04", 100),
                       _leg("SAMPQ.TO", "2025-03-04", 100)],
                      _names(SAMPQ_US="SAMPQ ENERGY INC",
                             SAMPQ_TO="SAMPQ ENERGY INC"))
        self.assertEqual(r, {"joined": [], "suggested": []})

    @rule("CA-XLIST-01")
    def test_far_apart_or_unequal_legs_do_not_pair(self):
        names = _names(SAMPQ_US="SAMPQ ENERGY INC",
                       SAMPQ_TO="SAMPQ ENERGY INC")
        for legs in ([_leg("SAMPQ.US", "2025-03-03", -100),
                      _leg("SAMPQ.TO", "2025-03-12", 100)],
                     [_leg("SAMPQ.US", "2025-03-03", -100),
                      _leg("SAMPQ.TO", "2025-03-04", 90)]):
            with self.subTest(legs=legs):
                self.assertEqual(self._run(legs, names),
                                 {"joined": [], "suggested": []})


_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qt(date, action, sym, desc, qty, price, net, cur, act="Trades"):
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},55500001,{act},Individual\n")


def _journal(cur, *, in_desc="SAMPQ ENERGY INC TRANSFER"):
    """A taxable account: 100 bought as SAMPQ, journaled to the SAMPR
    line (the same company's other listing), sold as SAMPR."""
    return (_QT
            + _qt("2025-01-10", "Buy", "SAMPQ", "SAMPQ ENERGY INC", 100,
                  30.0, -3000.0, cur)
            + _qt("2025-03-03", "TF6", "SAMPQ", "SAMPQ ENERGY INC TRANSFER",
                  -100, 0.0, 0.0, cur, act="Transfers")
            + _qt("2025-03-04", "TF6", "SAMPR", in_desc, 100, 0.0, 0.0, cur,
                  act="Transfers")
            + _qt("2025-06-10", "Sell", "SAMPR", "SAMPQ ENERGY INC", -100,
                  45.0, 4500.0, cur))


def _projects(tmp, tail="", **kw):
    out = {}
    for c in COUNTRIES:
        cur = "CAD" if c == "canada" else "USD"
        files = {"inputs/margin/questrade.csv": _journal(cur, **kw)}
        if tail:
            files["ticker.map"] = tail
        out[c] = projects_both(
            Path(tmp) / c, files=files,
            canada={"source_currencies": []},
            usa={"source_currencies": []})[c]
    return out


def _gain(root):
    doc = json.loads((root / "work" / "margin_gains_wash.json").read_text())
    return doc["summary"]["total_gain"]


class TestRun(unittest.TestCase):
    def _check_joined(self, country):
        with tempfile.TemporaryDirectory() as tmp:
            root = _projects(tmp)[country]
            r = cli(root, "run", "--no-input")
            out = " ".join((r.stdout + r.stderr).split())
            self.assertEqual(r.returncode, 0, out)
            sfx = "TO" if country == "canada" else "US"
            self.assertEqual(out.count("joined as one security"), 1, out)
            # A Warning (`warning:` when captured at width 0).
            self.assertIn(f"warning: margin: joined as one security by "
                          f"their transfer journal: sampq.{sfx.lower()} ↔ "
                          f"sampr.{sfx.lower()} (transfer 2025-03-03)",
                          out.lower())
            self.assertIn(f"add `DISTINCT SAMPQ.{sfx} SAMPR.{sfx}` to "
                          f"ticker.map", out)
            # Bought as SAMPQ, sold as SAMPR: one security, gain 1500.
            self.assertAlmostEqual(_gain(root), 1500.0, places=2)
            eff = (root / "work" / XL.EFFECTIVE_MAP).read_text()
            self.assertIn("TOBASE ", eff)
            state = XL.read_state(root / "work" / XL.STATE)
            self.assertEqual(len(state["joined"]), 1)
            # A second run: the same joins, the map file untouched.
            mtime = (root / "work" / XL.EFFECTIVE_MAP).stat().st_mtime_ns
            r2 = cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0)
            self.assertEqual(
                (root / "work" / XL.EFFECTIVE_MAP).stat().st_mtime_ns, mtime)

    @rule("CA-XLIST-01")
    def test_canada_journal_pair_is_joined(self):
        self._check_joined("canada")

    @rule("US-XLIST-01")
    def test_usa_journal_pair_is_joined(self):
        self._check_joined("usa")

    def _check_not_joined(self, country, **kw):
        """Run; nothing joined. Returns the cross_listings state."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _projects(tmp, **kw)[country]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("joined as one security", r.stdout + r.stderr)
            eff = root / "work" / XL.EFFECTIVE_MAP
            self.assertFalse(eff.exists()
                             and XL.EFFECTIVE_HEAD in eff.read_text())
            return XL.read_state(root / "work" / XL.STATE)

    @rule("CA-XLIST-01")
    def test_canada_distinct_wins(self):
        st = self._check_not_joined(
            "canada", tail="DISTINCT SAMPQ.TO SAMPR.TO\n")
        self.assertEqual(st, {"joined": [], "suggested": []})

    @rule("US-XLIST-01")
    def test_usa_distinct_wins(self):
        self._check_not_joined("usa", tail="DISTINCT SAMPQ.US SAMPR.US\n")

    @rule("CA-XLIST-01")
    def test_canada_preferred_vs_common_is_a_suggestion(self):
        st = self._check_not_joined(
            "canada", in_desc="SAMPQ ENERGY INC PFD SER 2 TRANSFER")
        self.assertEqual(st["joined"], [])
        self.assertEqual(len(st["suggested"]), 1)
        # SAMPR is also sold as "SAMPQ ENERGY INC": its names disagree.
        self.assertTrue(st["suggested"][0]["reason"].startswith(
            "another name of the listings states another share"),
            st["suggested"][0]["reason"])

    @rule("US-XLIST-01")
    def test_usa_adr_is_never_joined(self):
        st = self._check_not_joined(
            "usa", in_desc="SAMPQ ENERGY INC SPONSORED ADR TRANSFER")
        self.assertEqual(len(st["suggested"]), 1)


if __name__ == "__main__":
    unittest.main()
