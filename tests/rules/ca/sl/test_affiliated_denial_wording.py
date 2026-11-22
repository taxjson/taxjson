"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import tempfile
import unittest

from _support.tt_fx_project import _cli, _tt_project
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------ A2-1225 / A2-1233
class TestAffiliatedDenialWording(unittest.TestCase):
    """An affiliated person's purchase is permanent for this return, but
    that person adds the loss to their own ACB (s.53(1)(f)) — not 'lost
    for good' (the S033-03 wording, carried to the trace and the
    wash-sales footer).

    Formerly: test_fix_a2_pipecmd.TestAffiliatedDenialWording"""

    @rule(R.CA_SL_09)
    def test_trace_affiliated(self):
        from taxjson.lib.trace_format import _render_wash_explanation
        g = {"raw_gain": -100.0, "disallowed_amount": 100.0,
             "permanently_disallowed": 100.0,
             "wash_trigger": {"is_full_disallowance": True,
                              "trigger_date": "2026-03-02",
                              "trigger_qty": 10, "trigger_price": 9.0,
                              "trigger_account": "spouse",
                              "trigger_affiliated": True}}
        txt = "\n".join(_render_wash_explanation(g))
        self.assertNotIn("lost for good", txt)
        self.assertIn("own ACB", txt)
        g["wash_trigger"]["trigger_affiliated"] = False
        g["wash_trigger"]["trigger_sheltered"] = True
        txt = "\n".join(_render_wash_explanation(g))
        self.assertIn("lost for good", txt)

    @rule(R.CA_SL_09)
    def test_wash_sales_footer(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 50 -5000 0",
                    "BUYSELL 2026-06-10 10:00:00 XYZ.TO -100 CAD 40 4000 0"]),
                "tfsa": ("sheltered", [
                    "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"])})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            # The note on what DENIED means: --details (Essentials first).
            r = _cli(root, home, "wash-sales", "--details")
            self.assertIn("affiliated person", r.stdout)
            self.assertIn("own ACB", r.stdout)


if __name__ == "__main__":
    unittest.main()
