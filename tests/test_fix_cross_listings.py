"""Two listings of one security joined by their transfer journal
(CA-XLIST-01 / US-XLIST-01): when an out-leg of one listing and an
in-leg of another pair uniquely and the exports' security names agree,
`taxjson run` books them as one security, as a ticker.map TOBASE line
would — with one Info line per account. A ticker.map rule (DISTINCT
included) wins; ambiguous pairs and disagreeing names stay suggestions.

Synthetic data only.
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib.country import COUNTRIES
from taxjson.lib.symbol_codes import name_tokens
from tax_rules import rule
from tax_rules.dual import cli, projects_both


def _leg(sym, date, qty, account="rrsp"):
    return XL.Leg(account, "questrade", sym, date, qty)


def _names(**kw):
    return {s.replace("_", "."): {name_tokens(n)} for s, n in kw.items()}


class TestAnalyze(unittest.TestCase):
    """The pairing and the name test, on legs."""

    def _run(self, legs, names, **kw):
        shown = {t: " ".join(t) for v in names.values() for t in v}
        return XL.analyze(legs, names, shown, **kw)

    @rule("CA-XLIST-01")
    def test_unique_pair_with_agreeing_names_joins(self):
        r = self._run([_leg("SAMPQ.US", "2025-03-03", -100),
                       _leg("SAMPQ.TO", "2025-03-05", 100)],
                      _names(SAMPQ_US="SAMPQ ENERGY INC",
                             SAMPQ_TO="SAMPQ ENERGY CORP"),
                      base_currency="CAD")
        self.assertEqual([(p.frm, p.to) for p in r["joined"]],
                         [("SAMPQ.US", "SAMPQ.TO")])
        self.assertEqual(r["suggested"], [])
        self.assertEqual(XL.map_lines(r["joined"])[0].split("#")[0].strip(),
                         "TOBASE SAMPQ.US SAMPQ.TO")
        self.assertEqual(XL.joined_note("rrsp", r["joined"]),
                         "rrsp: joined as one security: SAMPQ.US ↔ "
                         "SAMPQ.TO (transfer 2025-03-03)")

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
                 "the names differ in the share class or kind"),
                (_names(SAMPQ_US="SAMPQ ENERGY INC SPONSORED ADR",
                        SAMPQ_TO="SAMPQ ENERGY INC"),
                 "the names differ in the share class or kind"),
                (_names(SAMPQ_US="SAMPQ ENERGY INC",
                        SAMPQ_TO="QZWV MINING LTD"),
                 "the names do not match")):
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
            self.assertIn(f"margin: joined as one security: SAMPQ.{sfx} ↔ "
                          f"SAMPR.{sfx} (transfer 2025-03-03)", out)
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
    def test_canada_class_conflict_is_a_suggestion(self):
        st = self._check_not_joined(
            "canada", in_desc="SAMPQ ENERGY INC CL B TRANSFER")
        self.assertEqual(st["joined"], [])
        self.assertEqual([s["reason"] for s in st["suggested"]],
                         ["the names differ in the share class or kind"])

    @rule("US-XLIST-01")
    def test_usa_adr_is_never_joined(self):
        st = self._check_not_joined(
            "usa", in_desc="SAMPQ ENERGY INC SPONSORED ADR TRANSFER")
        self.assertEqual(len(st["suggested"]), 1)


if __name__ == "__main__":
    unittest.main()
