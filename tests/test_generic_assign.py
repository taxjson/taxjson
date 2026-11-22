"""Recommendation 9's third tier-1 gap: the generic importer's
`assign_buy` and `assign_sell` targets, one leg of an exercise or an
assignment each. The four questions: which cell is which (the leg's
symbol, the contracts or shares, the price: the option leg's 0, the
stock leg's strike), what money it may carry (the option leg none but
its fee; the stock leg a trade's), what the no-op is (none: a leg is
always booked, the engines pair it or name it), and the shared shape
(the schema's ASSIGN row, which the engines pair and fold the premium
of into the shares, CA-OPT-06 / US-OPT-02)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from _tmpfiles import isolate_layouts
from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib.brokerages.generic import GenericBrokerage
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction

isolate_layouts()

TOML = """\
[columns]
date = "Date"
action = "Action"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
fee = "Fee"
currency = "Currency"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"ASSIGNED IN" = "assign_buy"
"ASSIGNED OUT" = "assign_sell"
"""
HEADER = "Date,Action,Symbol,Quantity,Price,Amount,Fee,Currency\n"
PUT = "ZZQ250620P00030000"
# A put written for 10.00 a share, assigned: the contract bought back at
# 0 and 100 shares bought at the strike.
SHORT_PUT = (f"2025-01-15,SELL,{PUT},1,10.00,1000.00,,USD\n"
             f"2025-06-20,ASSIGNED IN,{PUT},1,0,0,,USD\n"
             "2025-06-20,ASSIGNED IN,ZZQ,100,30.00,-3000.00,,USD\n")


class TestAssignTargets(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def parse(self, rows, toml=TOML, said=None):
        csv = self.dir / "activity.csv"
        csv.write_text(HEADER + rows)
        csv.with_name(csv.name + ".toml").write_text(toml)
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            got = GenericBrokerage().parse_file(csv)
        if said is not None:
            said.append(out.getvalue())
        return got

    def refused(self, rows, toml=TOML):
        with self.assertRaises(ValueError) as cm:
            self.parse(rows, toml)
        return str(cm.exception)

    def test_the_legs_are_assign_rows(self):
        got = [(t["action"], t["symbol"], t["quantity"], t["net_amount"])
               for t in self.parse(SHORT_PUT)]
        self.assertEqual(got, [
            ("BUYSELL", PUT + ".US", -1.0, 1000.0),
            ("ASSIGN", PUT + ".US", 1.0, 0.0),
            ("ASSIGN", "ZZQ.US", 100.0, 3000.0)])

    @rule(R.CA_DATE_04)
    def test_the_option_leg_settles_with_its_share_leg(self):
        # TJ-2661: an exercise or an assignment takes its stock leg's
        # settle date, the expiry day's leg and a leg posted later alike.
        for day in ("2025-06-20", "2025-06-23"):
            rows = SHORT_PUT.replace("2025-06-20,ASSIGNED IN," + PUT,
                                     day + ",ASSIGNED IN," + PUT)
            got = self.parse(rows)
            self.assertEqual(got[1]["date_settle"], got[2]["date_settle"])
            self.assertEqual(got[2]["date_settle"], "2025-06-23")

    def _folded(self, rules):
        # The engines pair the legs: the shares cost the strike less the
        # premium, 3000 - 1000, whichever leg comes first.
        fields = TaxTransaction.__dataclass_fields__
        rows = SHORT_PUT.splitlines(keepends=True)
        for order in (rows, rows[:1] + rows[2:] + rows[1:2]):
            txs = [TaxTransaction(**{k: v for k, v in t.items() if k in fields})
                   for t in self.parse("".join(order))]
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                res = rules.compute_gains(txs)
            inv = [i for i in res["inventory"] if i["symbol"] == "ZZQ.US"]
            self.assertAlmostEqual(inv[0]["total_cost"], 2000.0, places=2)
            self.assertFalse([g for g in res["transactions"]
                              if g.get("symbol") == PUT + ".US"])

    @rule(R.CA_OPT_06)
    def test_the_premium_folds_into_the_shares_ca(self):
        self._folded(CanadaTaxRules())

    @rule(R.US_OPT_02)
    def test_the_premium_folds_into_the_shares_us(self):
        self._folded(USATaxRules())

    def test_a_written_call_assigned(self):
        got = [(t["action"], t["symbol"], t["quantity"], t["net_amount"])
               for t in self.parse(
                   "2025-01-02,BUY,ZZQ,100,25.00,-2500.00,,USD\n"
                   "2025-01-15,SELL,ZZQ250620C00030000,1,5.00,500.00,,USD\n"
                   "2025-06-20,ASSIGNED IN,ZZQ250620C00030000,1,0,0,,USD\n"
                   "2025-06-20,ASSIGNED OUT,ZZQ,100,30.00,3000.00,,USD\n")]
        self.assertEqual(got[2:], [
            ("ASSIGN", "ZZQ250620C00030000.US", 1.0, 0.0),
            ("ASSIGN", "ZZQ.US", -100.0, 3000.0)])

    def test_the_option_legs_fee(self):
        # A charge on the option leg is its fee; the leg is not an
        # expiry, so a leg posted after the expiry keeps its own date.
        got = self.parse(f"2025-06-23,ASSIGNED IN,{PUT},1,0,-5.00,5.00,USD\n")
        self.assertEqual((got[0]["action"], got[0]["date"], got[0]["fee"]),
                         ("ASSIGN", "2025-06-23", 5.0))

    def test_what_is_refused(self):
        self.assertIn("carries money", self.refused(
            f"2025-06-20,ASSIGNED IN,{PUT},1,10.00,-1000.00,,USD\n"))
        self.assertIn("futures", self.refused(
            "2025-06-20,ASSIGNED IN,F:ESZ5,1,0,0,,USD\n"))
        # The shared checks' refusals name the leg's own target (TJ-2667).
        self.assertIn("mapped to assign_buy but the quantity is NEGATIVE", self.refused(
            "2025-06-20,ASSIGNED IN,ZZQ,-100,30.00,-3000.00,,USD\n"))
        self.assertIn("(-> assign_sell) has a blank or zero quantity", self.refused(
            "2025-06-20,ASSIGNED OUT,ZZQ,0,30.00,3000.00,,USD\n"))
        # At load: the cells a leg books from (TJ-2668).
        toml = TOML.replace('quantity = "Quantity"\n', "").replace(
            '"BUY" = "buy"\n"SELL" = "sell"\n', "")
        self.assertIn("maps to assign_buy", self.refused(
            "2025-06-20,ASSIGNED IN,ZZQ,100,30.00,-3000.00,,USD\n", toml))
        toml = TOML.replace('price = "Price"\n', "").replace('amount = "Amount"\n', "")
        toml = toml.replace('"BUY" = "buy"\n"SELL" = "sell"\n', "")
        self.assertIn("maps to assign_buy but neither [columns].price", self.refused(
            "2025-06-20,ASSIGNED IN,ZZQ,100,30.00,-3000.00,,USD\n", toml))

    def test_a_share_leg_at_zero(self):
        # TJ-2662: a share leg is at the strike; a warrant's exercise at
        # $0 would book a loss of the warrants' cost.
        self.assertIn("at $0", self.refused(
            "2025-06-20,ASSIGNED OUT,ZZQW,100,0,0,,USD\n"
            "2025-06-20,ASSIGNED IN,ZZQ,100,5.00,-500.00,,USD\n"))
        # TJ-2670: its commission is no price, stated or blank.
        for leg in ("ZZQW,100,0,-5.00,5.00", "ZZQW,100,,-5.00,5.00"):
            self.assertIn("at $0", self.refused(
                f"2025-06-20,ASSIGNED OUT,{leg},USD\n"
                "2025-06-20,ASSIGNED IN,ZZQ,100,5.00,-500.00,,USD\n"))

    def test_the_option_legs_side(self):
        # TJ-2663: a written put's leg mapped out sells the shares its
        # share leg buys: refused, naming each pair's targets.
        rows = SHORT_PUT.replace(f"2025-06-20,ASSIGNED IN,{PUT}",
                                 f"2025-06-20,ASSIGNED OUT,{PUT}")
        self.assertIn("written put assigned is assign_buy on both", self.refused(rows))

    def test_a_half_mapped_pair_is_named(self):
        # TJ-2664: the option's $0 close under buy beside a share leg
        # under assign_buy is the ATTENTION line's case too.
        said = []
        self.parse(SHORT_PUT.replace(f"2025-06-20,ASSIGNED IN,{PUT}",
                                     f"2025-06-20,BUY,{PUT}"), said=said)
        self.assertIn("possible exercise/assignment", said[0])
        # TJ-2673: a real expiry at the same strike beside a pair mapped
        # as one is not: the share leg is the pair's.
        said = []
        self.parse("2025-06-20,BUY,ZZQ250620P00030000,1,0,0,,USD\n"
                   "2025-06-20,ASSIGNED IN,ZZQ250718P00030000,1,0,0,,USD\n"
                   "2025-06-20,ASSIGNED IN,ZZQ,100,30.00,-3000.00,,USD\n",
                   said=said)
        self.assertNotIn("possible exercise/assignment", said[0])

    def test_the_fee_as_the_importer_reads_it(self):
        # TJ-2665, TJ-2666: the fee inferred with no fee column, signed
        # by fee_sign, and a blank price: the option leg's fee alone.
        no_fee = TOML.replace('fee = "Fee"\n', "")
        got = self.parse(f"2025-06-20,ASSIGNED IN,{PUT},1,0,-5.00,,USD\n", no_fee)
        self.assertEqual((got[0]["action"], got[0]["fee"]), ("ASSIGN", 5.0))
        signed = TOML.replace("[actions]", '[formats]\nfee_sign = "cash"\n\n[actions]')
        got = self.parse(f"2025-06-20,ASSIGNED IN,{PUT},1,0,-5.00,-5.00,USD\n", signed)
        self.assertEqual(got[0]["fee"], 5.0)
        got = self.parse(f"2025-06-20,ASSIGNED IN,{PUT},1,,-5.00,5.00,USD\n")
        self.assertEqual((got[0]["price"], got[0]["fee"]), (0.0, 5.0))
        # TJ-2671: an early exercise or assignment, months before the
        # expiry, with no fee column: its charge is its commission too.
        got = self.parse(f"2025-03-10,ASSIGNED IN,{PUT},1,0,-5.00,,USD\n"
                         "2025-03-10,ASSIGNED IN,ZZQ,100,30.00,-3000.00,,USD\n",
                         no_fee)
        self.assertEqual((got[0]["action"], got[0]["fee"]), ("ASSIGN", 5.0))

    def test_paired_as_the_engines_pair(self):
        # TJ-2672: a written put whose share leg is a plain BUY at the
        # strike, beside a written call's marked pair the same day: the
        # put pairs with its BUY, as the engines pair it, and settles
        # with it.
        call = "ZZQ250620C00035000"
        got = self.parse(
            f"2025-06-20,ASSIGNED IN,{PUT},1,0,0,,USD\n"
            "2025-06-20,BUY,ZZQ,100,30.00,-3000.00,,USD\n"
            f"2025-06-20,ASSIGNED IN,{call},1,0,0,,USD\n"
            "2025-06-20,ASSIGNED OUT,ZZQ,100,35.00,3500.00,,USD\n")
        self.assertEqual(got[0]["date_settle"], got[1]["date_settle"])
        # TJ-2674: two puts assigned on one underlying: each takes its
        # own share leg's settle date (the strike first, as the engines
        # choose), not the nearest leg's.
        other = "ZZQ250620P00025000"
        got = self.parse(
            f"2025-06-20,ASSIGNED IN,{PUT},1,0,0,,USD\n"
            f"2025-06-20,ASSIGNED IN,{other},1,0,0,,USD\n"
            "2025-06-20,ASSIGNED IN,ZZQ,100,25.00,-2500.00,,USD\n"
            "2025-06-24,ASSIGNED IN,ZZQ,100,30.00,-3000.00,,USD\n")
        self.assertEqual([t["date_settle"] for t in got],
                         ["2025-06-25", "2025-06-23", "2025-06-23", "2025-06-25"])

    def test_the_finish_names_the_target(self):
        # TJ-2675: the file's own sign convention, said of a leg by its
        # target; the settle-on-trade-date note said of a file of legs
        # (TJ-2678).
        self.assertIn("a row mapped to assign_sell with a POSITIVE quantity",
                      self.refused(
                          "2025-06-02,SELL,ZZQ,-10,30.00,300.00,,USD\n"
                          "2025-06-20,ASSIGNED OUT,ZZQ,100,30.00,3000.00,,USD\n"))
        said = []
        self.parse(SHORT_PUT.split("\n", 1)[1],
                   TOML + '\n[options]\nsettle_on_trade_date = true\n', said=said)
        self.assertIn("settle_on_trade_date = true settles each trade", said[0])


if __name__ == "__main__":
    unittest.main()
