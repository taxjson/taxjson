"""An in-kind contribution to an RESP at a loss (backlog TJ-5452, the
ruling of 2026-11-26). s.40(2)(g)(iv) does not name an RESP: the sale at
fair market value is an ordinary one (CA-INKIND-03), and the plan's own
acquisition of the shares is its replacement (CA-INKIND-04), so the loss
is superficial and denied for good (s.54; the s.53(1)(f) addition is the
plan's) while the plan still holds them on day 30, and allowed when the
plan sold them first (CRA 2010-0352921E5). The figures were right before
the fix; the run's in-kind warning said "gain 0.00 CAD" for the denied
loss, and that a contribution's loss is "not a superficial loss".

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import tempfile
import unittest

from tax_rules import rule
from _support.transfer_in_kind import _ib, _QT, _qt, _xfer_qt, _project, _run, _flat, _filing
from taxjson.rules import ids as R

_RESP = ('[accounts.margin]\ntype = "taxable"\n'
         '[accounts.resp]\ntype = "sheltered"\nplan = "resp"\n'
         'transfers = true\n')
_MOVE = ("contribution margin → resp: 100 QZK.TO on 2025-03-14 at 950.00 CAD "
         "(Interactive Brokers market value on margin's transfer row): ")


def _resp_contribution(td, plan_rows=""):
    """margin (IB) buys 100 QZK.TO on 01-10 at 10 (cost 1000) and moves
    them out on 03-14 at an IB market value of 950 (a loss of 50); resp
    (Questrade, plan "resp") receives the 100 on 03-17, then
    `plan_rows`."""
    files = {"inputs/margin/ib.csv": _ib([("2025-01-10", 100, 10.0)],
                                         [("2025-03-14", -100, 950)]),
             "inputs/resp/questrade.csv": (_QT + _xfer_qt("2025-03-17", 100)
                                           + plan_rows)}
    return _project(td, files, accounts=_RESP)


def _contribution_row(root):
    doc = json.loads((root / "work" / "margin_gains_wash.json").read_text())
    rows = [e for e in doc["transactions"] if e.get("in_kind") == "contribution"]
    assert len(rows) == 1, rows
    return rows[0]


class TestRespContributionAtALoss(unittest.TestCase):
    """End to end: the figures (already right on a146a42c) and the run's
    in-kind warning (wrong there)."""

    @rule(R.CA_INKIND_03, R.CA_INKIND_04)
    def test_the_plan_holds_on_day_30_superficial_and_denied_for_good(self):
        with tempfile.TemporaryDirectory() as td:
            root = _resp_contribution(td)
            flat = _flat(_run(self, root))
            # The figures: a sale at 950, its loss of 50 denied by s.54
            # for good, not nil by s.40(2)(g)(iv); the trigger is the
            # plan's own acquisition.
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                             (950.0, 950.0, 0.0))
            self.assertEqual((t["denied"], t["permanently_denied"],
                              t["denied_contribution"]), (50.0, 50.0, 0.0))
            row = _contribution_row(root)
            self.assertEqual((row["raw_gain"], row["disallowed_amount"],
                              row["permanently_disallowed"]), (-50.0, 50.0, 50.0))
            self.assertEqual((row["wash_trigger"]["trigger_account"],
                              row["wash_trigger"]["trigger_sheltered"]), ("resp", True))
            # The words: the move's line says the loss and what s.54 did
            # with it (it said "gain 0.00 CAD"); the closing paragraph
            # gives the RESP's rule, not s.40(2)(g)(iv)'s.
            self.assertIn(_MOVE + "loss 50.00 CAD superficial (s.54): DENIED for "
                          "good (a registered account holds the shares on day 30).", flat)
            self.assertIn("A contribution in kind is a sale at fair market value: "
                          "a gain is taxed; to an RESP a loss is an ordinary one, "
                          "superficial (s.54) and lost for good when the plan still "
                          "holds the shares on day 30 (the ACB addition is the "
                          "plan's)", flat)
            self.assertNotIn("gain 0.00 CAD", flat)
            self.assertNotIn("not a superficial loss", flat)

    @rule(R.CA_INKIND_03, R.CA_INKIND_04)
    def test_the_plan_sold_them_before_day_30_the_loss_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = _resp_contribution(td, _qt("2025-03-24", "Sell", -100, 9.4))
            flat = _flat(_run(self, root))
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                             (950.0, 1000.0, -50.0))
            self.assertEqual((t["denied"], t["permanently_denied"],
                              t["denied_contribution"]), (0.0, 0.0, 0.0))
            # It said "loss 50.00 CAD." and that a contribution's loss is
            # denied for good.
            self.assertIn(_MOVE + "loss 50.00 CAD allowed.", flat)
            self.assertIn("to an RESP a loss is an ordinary one", flat)
            self.assertNotIn("not a superficial loss", flat)


class TestTheWordsOfEachCase(unittest.TestCase):
    """The move's line for each way the books can treat the sale (the
    warning reads the gains file's figures), and the closing paragraph
    for the plans booked."""

    def _details(self, plan, r):
        from taxjson.lib import in_kind as IK
        row = {"action": "TRANSFER", "symbol": "QZK.TO", "quantity": -100.0,
               "date": "2025-03-14", "currency": "CAD"}
        leg = IK.Leg(account="margin", broker="ib", row=row, key="QZK.TO")
        reg = IK.Leg(account="plan", broker="questrade", plan=plan, key="QZK.TO",
                     row=dict(row, quantity=100.0, date="2025-03-17"))
        m = IK.Move(IK.CONTRIBUTION, taxable=leg, registered=reg, plan=plan,
                    quantity=100.0, date="2025-03-14", fmv=950.0, currency="CAD",
                    source_text="made-up value")
        _head, details = IK.message([m], [], {("margin", "QZK.TO", "2025-03-14"): r},
                                    "canada", "CAD")
        return details

    @rule(R.CA_INKIND_03, R.CA_INKIND_04)
    def test_each_line(self):
        cases = [
            ("resp", {"gain": 0.0, "denied": 0.0, "disallowed": 50.0, "permanent": 50.0},
             "loss 50.00 CAD superficial (s.54): DENIED for good (a registered "
             "account holds the shares on day 30)"),
            # A taxable account's purchase is the replacement held on day 30.
            ("resp", {"gain": 0.0, "denied": 0.0, "disallowed": 50.0, "permanent": 0.0},
             "loss 50.00 CAD superficial (s.54): denied (added to the replacement's ACB)"),
            ("prpp", {"gain": -20.0, "denied": 0.0, "disallowed": 30.0, "permanent": 30.0},
             "loss 50.00 CAD superficial in part (s.54): 30.00 DENIED for good (a "
             "registered account holds the shares on day 30); 20.00 allowed"),
            ("resp", {"gain": -50.0, "denied": 0.0, "disallowed": 0.0, "permanent": 0.0},
             "loss 50.00 CAD allowed"),
            ("resp", {"gain": 25.0, "denied": 0.0}, "gain 25.00 CAD"),
            # s.40(2)(g)(iv)'s plans, as before.
            ("tfsa", {"gain": 0.0, "denied": 50.0, "disallowed": 0.0, "permanent": 0.0},
             "loss 50.00 CAD DENIED for good (s.40(2)(g)(iv)) — no ACB addition"),
        ]
        for plan, r, want in cases:
            with self.subTest(plan=plan, r=r):
                line = self._details(plan, r)[0]
                self.assertTrue(line.endswith(": " + want + "."), line)

    @rule(R.CA_INKIND_03)
    def test_the_closing_paragraph_names_the_plans_booked(self):
        tfsa = self._details("tfsa", {"gain": 0.0, "denied": 50.0})[1]
        self.assertIn("to an RRSP, RRIF, TFSA, FHSA or RDSP a loss is nil for good "
                      "(s.40(2)(g)(iv); not a superficial loss, never added to an "
                      "ACB); the plan's purchase", tfsa)
        self.assertNotIn("to an RESP", tfsa)
        prpp = self._details("prpp", {"gain": 0.0, "denied": 0.0, "disallowed": 50.0,
                                      "permanent": 50.0})[1]
        self.assertIn("to a PRPP a loss is an ordinary one", prpp)
        self.assertNotIn("s.40(2)(g)(iv)", prpp)


if __name__ == "__main__":
    unittest.main()
