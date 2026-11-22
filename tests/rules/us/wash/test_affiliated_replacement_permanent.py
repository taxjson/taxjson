"""Deferred round, engine-t1135 area: the T1135 cost walk carries every
s.53(1)(f) addition (S008-07, S009-01, S051-21).

Formerly: test_fix_x_engine_t1135"""
import contextlib
import io
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestUSAffiliatedReplacement(unittest.TestCase):
    """SPEC-30 / ENGINE-I1: a spouse's (affiliated) purchase in the window
    disallows the loss; §1091(d) puts the basis adjustment on THEIR
    replacement shares, so these books never recover it — it is reported
    as permanently disallowed, not as a deferral no lot carries.

    Formerly: test_fix_x_engine_t1135.TestUSAffiliatedReplacement"""

    @staticmethod
    def _tx(i, d, q, net, acct):
        from taxjson.model.transaction import TaxTransaction
        return TaxTransaction(id=i, action="BUYSELL", date=d, date_settle=d,
                              time="10:00:00", symbol="XYZ.US", quantity=q,
                              net_amount=net, price=abs(net / q),
                              currency="USD", account=acct)

    def _run(self, short=False, detect=True):
        from taxjson.engine.us.taxrules import USATaxRules
        if short:
            mine = [self._tx("o1", "2025-01-02", -100, 1000.0, "U5550001"),  # pii-ok
                    self._tx("c1", "2025-06-02", 100, -2000.0, "U5550001")]  # pii-ok
            theirs = [self._tx("a1", "2025-06-10", -100, 1000.0, "SPOUSE")]
        else:
            mine = [self._tx("b1", "2025-01-02", 100, -2000.0, "U5550001"),  # pii-ok
                    self._tx("s1", "2025-06-02", -100, 1000.0, "U5550001")]  # pii-ok
            theirs = [self._tx("a1", "2025-06-10", 100, -1000.0, "SPOUSE")]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return USATaxRules().compute_gains(
                mine, affiliated_transactions=theirs,
                detect_wash_sales=detect)

    @rule(R.US_WASH_16)
    def test_spouse_buy_is_permanent_in_these_books(self):
        res = self._run()
        (g,) = [g for g in res["transactions"] if g.get("is_wash_sale")]
        self.assertAlmostEqual(g["disallowed_amount"], 1000.0, 2)
        self.assertAlmostEqual(g["permanently_disallowed"], 1000.0, 2)
        self.assertTrue(g["wash_replacements"][0]["is_affiliated"])
        self.assertEqual(sum(float(i.get("deferred_wash") or 0)
                             for i in res["inventory"]), 0.0)
        # Conservation (I1): realized - parked - permanent = no-wash.
        nowash = self._run(detect=False)
        tot = lambda r: sum(float(x["gain"]) for x in r["transactions"]
                            if x.get("qty") and "gain" in x)
        self.assertAlmostEqual(tot(res) - 1000.0, tot(nowash), 2)

    @rule(R.US_WASH_16)
    def test_spouse_short_is_permanent_in_these_books(self):
        res = self._run(short=True)
        (g,) = [g for g in res["transactions"] if g.get("is_wash_sale")]
        self.assertAlmostEqual(g["permanently_disallowed"],
                               g["disallowed_amount"], 2)
        self.assertGreater(g["disallowed_amount"], 0)

    @rule(R.US_WASH_16)
    def test_trace_names_the_affiliated_holder(self):
        from taxjson.lib.trace_format import _render_wash_explanation as render_wash_block
        res = self._run()
        (g,) = [g for g in res["transactions"] if g.get("is_wash_sale")]
        text = "\n".join(render_wash_block(g))
        self.assertIn("affiliated", text)
        self.assertNotIn("Rev. Rul. 2008-5", text)


if __name__ == "__main__":
    unittest.main()
