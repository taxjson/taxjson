"""Backlog group `handoff` — handoff, carryover, carryforward, the
estimate, amt and option-boundary: TJ-0014, TJ-0016, TJ-0052, TJ-0068,
TJ-0069, TJ-0078, TJ-0084, TJ-0085, TJ-0115, TJ-0144, TJ-0145,
TJ-0146. Synthetic data only.

Formerly: test_fix_backlog_handoff"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------------ TJ-0016
class TestMinimumTaxProvincialShare(unittest.TestCase):
    """TJ-0016: the ON share of a minimum-tax carryover recovery is the
    federal amount x the factor, off basic ON tax; the surtax it saves
    is shown apart, never inside the share.

    Formerly: test_fix_backlog_handoff.TestMinimumTaxProvincialShare"""

    def _est(self):
        from taxjson.lib import tax_estimate as TE
        return TE.estimate_canada(
            realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
            other_income=200000.0, other_losses=0.0, province="ON",
            year=2025, amt_carryover={2024: 900.5},
            amt_carryover_source="[estimate] amt_carryover")

    @rule(R.CA_AMT_05)
    def test_share_is_the_factor_of_the_federal_amount(self):
        from taxjson.lib import amt_report as AR
        r = self._est()
        a = r["amt"]
        c = a["carryover"]
        self.assertEqual(c["recovered_federal"], 900.5)
        share = round(900.5 * a["provincial_factor"], 2)
        self.assertAlmostEqual(c["recovered_provincial_credit"], share,
                               places=2)
        # The surtax it saves is the rest of the provincial drop.
        self.assertGreater(c["recovered_provincial_surtax"], 0.0)
        self.assertAlmostEqual(c["recovered_provincial_credit"]
                               + c["recovered_provincial_surtax"],
                               c["recovered_provincial"], places=2)
        text = "\n".join(AR.render(AR.build(r, 2025, "CAD")))
        line = next(ln for ln in text.splitlines()
                    if "Recovered: ON share" in ln)
        self.assertIn(f"{share:,.2f}", line)
        self.assertNotIn(f"{c['recovered_provincial']:,.2f}", line)
        self.assertIn("ON surtax it saves", text)
        from taxjson.lib import tax_logic as TL
        txt = [rr.text for _t, rs in TL.rule_sections(
            "canada", {"country": "canada", "year": 2025})
            for rr in rs if rr.id == "CA-AMT-05"][0]
        self.assertIn("surtax it saves apart", txt)

    @rule(R.CA_AMT_05)
    def test_lock_records_the_share(self):
        from taxjson.lib import amt_report as AR
        from taxjson.lib import carryforward as CF
        r = self._est()
        mt = CF.record_block("canada", r, {})["minimum_tax"]
        c = r["amt"]["carryover"]
        self.assertEqual(mt["recovered_provincial_credit"],
                         c["recovered_provincial_credit"])
        text = "\n".join(AR.render_recorded(2025, "filed/2025.json", mt))
        self.assertIn("provincial surtax it saves", text)
        # A lock written before: its figure is the whole drop, said so.
        del mt["recovered_provincial_credit"]
        text = "\n".join(AR.render_recorded(2025, "filed/2025.json", mt))
        self.assertIn("provincial tax (surtax included)", text)


if __name__ == "__main__":
    unittest.main()
