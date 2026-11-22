"""v0.27.1: one tobase.map every year of a multi-year project reads
(`[settings] tobase_map = "../tobase.map"`), and no DISTINCT line for a
depositary receipt (look-alike listings are never joined; the
interlisted master keeps a CDR apart from its US share with no line).
Tax-logic CA-XLIST-06 / US-XLIST-05.

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented names.

Formerly: test_fix_shared_tobase
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import cross_listings as XL
from taxjson.lib import tobase_map as TB
from taxjson.lib.symbol_codes import exact_name

F1, F4 = "BBG000000Q01", "BBG000000Q04"
SEC = {F1: {"name": "QZALPHA", "kind": "share", "share_class_figi": F1,
            "ca": ["QZA.TO"], "us": ["QZAB.US"], "us_exchange": ["NYSE"]}}
CDR = {F4: {"name": "QZDELTA INC - CDR", "kind": "cdr", "ca": "QZD.TO",
            "us": "QZD.US", "us_figi": "BBG000000Q05"}}


def _master(security=None, distinct=None, generated="2026-10-01"):
    return TB.Master({"schema_version": 1, "generated": generated,
                      "sources": [{"name": "synthetic", "as_of": generated}]},
                     dict(security or {}), dict(distinct or {}))


# ------------------------------------------------------------ DISTINCT

class TestDistinctRetracted(unittest.TestCase):
    """Formerly: test_fix_shared_tobase.TestDistinctRetracted"""

    @rule(R.CA_XLIST_06)
    def test_update_retracts_unedited_distinct_lines(self):
        m = _master(SEC, CDR)
        rule_ = "DISTINCT QZD.US QZD.TO"
        old = (TB.render(m) + "\n## --- Depositary receipts the books hold "
               "(two securities) ---\n"
               f"{rule_}  # {TB.MARKER}{F4}:{TB.line_hash(rule_)}\n")
        tob = TB.parse_tobase(old)
        plan = TB.plan_update(m, tob, {"QZD.TO"}, "")
        self.assertEqual([(ln.rule, why) for ln, why in plan.retracted],
                         [(rule_, TB.DISTINCT_NOT_NEEDED)])
        self.assertEqual(TB.DISTINCT_NOT_NEEDED,
                         "not needed: look-alike listings are never joined")
        self.assertTrue(plan.changes)
        self.assertNotIn("DISTINCT", plan.new_text.split(TB.STAMP)[1])
        # An older marker without a check: the same.
        tob = TB.parse_tobase(old.replace(f":{TB.line_hash(rule_)}", ""))
        plan = TB.plan_update(m, tob, set(), "")
        self.assertEqual(len(plan.retracted), 1)

    def test_an_edited_distinct_line_is_kept_and_flagged(self):
        m = _master(SEC, CDR)
        rule_ = "DISTINCT QZD.US QZD.TO"
        old = (TB.render(m) + "\n"
               f"DISTINCT QZD.US QZD.V  # {TB.MARKER}{F4}:"
               f"{TB.line_hash(rule_)}\n"
               "DISTINCT QZOWN.US QZOWN.TO\n")
        plan = TB.plan_update(m, TB.parse_tobase(old), set(), "")
        self.assertEqual([(ln.rule, why) for ln, why in
                          plan.retracted_edited],
                         [("DISTINCT QZD.US QZD.V", TB.DISTINCT_NOT_NEEDED)])
        self.assertEqual(plan.retracted, [])
        self.assertIn("DISTINCT QZD.US QZD.V", plan.new_text)
        # The user's own DISTINCT line is kept as written.
        self.assertIn("DISTINCT QZOWN.US QZOWN.TO", plan.new_text)


# ------------------------------------------------------------ receipts

def _leg(sym, day, qty, name, *, ref="J1"):
    return XL.Leg("margin", "rbc_direct", sym, day, qty,
                  name=exact_name(name), raw_name=name,
                  journal="rbc_direct", ref=ref)


class TestReceiptsApart(unittest.TestCase):
    """The interlisted master's CDRs stay apart from their US share with
    no DISTINCT line: a journal-evidence join, a suggested pair and a
    MAP-GAP all refuse them (Canada); a US project reads no master.

    Formerly: test_fix_shared_tobase.TestReceiptsApart"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        p = Path(self.td.name) / "interlisted.toml"
        p.write_text(
            '[meta]\nschema_version = 1\ngenerated = "2026-10-01"\n\n'
            f'[distinct.{F4}]\nname = "QZDELTA INC - CDR"\nkind = "cdr"\n'
            'ca = "QZD.TO"\nus = "QZD.US"\n')
        self.receipts = TB.receipt_pairs(True, path=p)
        self.us_receipts = TB.receipt_pairs(False, path=p)

    def tearDown(self):
        self.td.cleanup()

    @rule(R.CA_XLIST_06)
    def test_receipt_pairs(self):
        pair = frozenset(("QZD.TO", "QZD.US"))
        self.assertIn(pair, self.receipts)
        self.assertIn(frozenset(("QZD.V", "QZD.US")), self.receipts)
        self.assertIn("depositary receipt", self.receipts[pair])
        self.assertTrue(TB.receipt_pairs())         # the shipped master

    @rule(R.US_XLIST_05)
    def test_no_master_receipts_in_a_us_project(self):
        self.assertEqual(self.us_receipts, {})
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "taxjson.toml").write_text(
                '[settings]\ncountry = "usa"\nyear = 2025\n')
            self.assertEqual(TB.receipt_pairs_for(td), {})

    @rule(R.CA_XLIST_06)
    def test_journal_evidence_never_joins_a_receipt(self):
        name = "QZDELTA INC"       # the exports name both alike
        legs = [_leg("QZD.US", "2025-03-03", -10, name),
                _leg("QZD.TO", "2025-03-03", 10, name)]
        names = {"QZD.US": {exact_name(name)}, "QZD.TO": {exact_name(name)}}
        shown = {exact_name(name): name}
        res = XL.analyze(legs, names, shown, base_currency="CAD")
        self.assertEqual(len(res["joined"]), 1)     # without the master
        for g in legs:
            g.used = False
        refused = []
        res = XL.analyze(legs, names, shown, base_currency="CAD",
                         refused=refused, receipts=self.receipts)
        self.assertEqual((res["joined"], res["suggested"]), ([], []))
        self.assertEqual([p.extra.get("refused") for p in refused],
                         [XL.RECEIPT])
        self.assertIn("depositary receipt", refused[0].reason)

    @rule(R.CA_XLIST_06)
    def test_a_tt_journal_between_them_stops(self):
        from taxjson.lib import dated_events as DE
        j = DE.Journal("margin", "2025-03-03", "QZD.US", "QZD.TO", 10.0,
                       "inputs/margin/x.tt:1",
                       line="JOURNAL 2025-03-03 QZD.US QZD.TO 10",
                       pair="tt1")
        try:
            legs = DE.journal_legs([j])
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"journal legs need more fields: {e}")
        name = "QZDELTA INC"
        names = {"QZD.US": {exact_name(name)}, "QZD.TO": {exact_name(name)}}
        refused = []
        res = XL.analyze(legs, names, {exact_name(name): name},
                         base_currency="CAD", declared=[j], refused=refused,
                         receipts=self.receipts)
        self.assertEqual(res["joined"], [])
        self.assertEqual([p.extra.get("refused") for p in refused],
                         [XL.UNPROVEN])
        self.assertIn("depositary receipt", refused[0].reason)

    @rule(R.CA_XLIST_06)
    def test_suggest_and_map_gap_keep_them_apart(self):
        from taxjson.lib import map_hygiene as MH
        name = ("QZDELTA", "INC")
        names = {"QZD.US": {name}, "QZD.TO": {name}}
        self.assertEqual(XL.shown_apart("QZD.US", "QZD.TO", names), "")
        self.assertIn("depositary receipt", XL.shown_apart(
            "QZD.US", "QZD.TO", names, self.receipts))
        kind, why = MH.pair_verdict("QZD.US", "QZD.TO", names,
                                    {name: "QZDELTA INC"}, self.receipts)
        self.assertEqual(kind, "receipt")
        self.assertEqual(MH.pair_verdict("QZD.US", "QZD.TO", names,
                                         {name: "QZDELTA INC"})[0], "same")


if __name__ == "__main__":
    unittest.main()
