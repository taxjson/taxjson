"""Fix round 7 of 2026-10-29, group transfers: transfers between
accounts and in-kind moves to and from registered plans.

TJ-0823: a .tt sale between two covered arrivals sold the first
          arrival's units twice (the arrival had consumed them, the
          sale took them again), so the second arrival's own .tt
          purchase looked spent and its broker book value was booked
          as well: a phantom holding and a lower gain.
TJ-0828: a declared in-kind withdrawal from a plan outside the project
          never took the shares out of that plan's side of the loss
          rule, so a loss whose replacement the plan had already given
          back was denied for good.
TJ-0829: a nearby unrelated transfer-in of the same account was paired
          with a declared in-kind contribution as a journal; the line
          then booked a move to a plan outside the project, whose
          replacement shares the real plan's sale never cleared.
TJ-0830: IB's transfer rows dropped the contract size they were priced
          with, so a mini call moved into a plan replaced 100 shares.
TJ-0827: the in-kind repair lines named the project's own inputs/ when
          its exports are shared (../inputs): a line added there is
          never read.
TJ-0821: a DISTINCT line did not stop the arrival check pairing two
          listings of one root in one account as a listing journal.
TJ-0822: the .tt coverage of a transfer-in skipped the dated RENAME
          lines and the INKIND contributions the converted rows leave out.
TJ-0654: a replacement a registered account bought in a loss's window
          and moved to another registered account was lost to the
          per-account still-held test.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7_transfers
"""
import os
import tempfile
import unittest
from pathlib import Path

from _support.transfer_registered import _cli, _QH, _project, _run, _totals, _TAXABLE, _TFSA
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestRepairLineNamesSharedInputs(unittest.TestCase):
    """TJ-0827: a year folder reading its exports from ../inputs. An
    in-kind contribution the offline run cannot value names the folder
    the run reads; the line added there lets the run book it.

    Formerly: test_fix_1029_r7_transfers.TestRepairLineNamesSharedInputs"""

    def test_offline_refusal_names_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            root = _project(top / "2025", {"margin": _TAXABLE,
                                           "plan": _TFSA}, {})
            cfg = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(cfg.replace(
                "[settings]\n", '[settings]\ninputs_dir = "../inputs"\n'))
            (root / "inputs").rename(top / "inputs")
            (top / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n")
            for a, q, day, no, kind in (
                    ("margin", -10, "2025-06-02", "99900001", "Margin"),
                    ("plan", 10, "2025-06-03", "99900002", "TFSA")):
                (top / "inputs" / a / "qt.csv").write_text(
                    _QH + f"{day},{day},TF6,SYN.TO,SYN CORP TRANSFER,{q},0,"
                    f"0,0,0,CAD,Transfers,{no},{kind}\n")
            r = _cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("add to a .tt file in ../inputs/margin/: INKIND "
                          "2025-06-02 SYN.TO -10 CAD", r.stderr)
            (top / "inputs/margin/fix.tt").write_text(
                "INKIND 2025-06-02 SYN.TO -10 CAD 15 plan=tfsa\n")
            _run(self, root)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 50.0,
                                   places=2)

    def test_messages_take_the_folder(self):
        from taxjson.lib import in_kind as IK
        row = {"action": "TRANSFER", "symbol": "SYN.TO", "quantity": -10.0,
               "date": "2025-06-02", "currency": "CAD"}
        leg = IK.Leg(account="margin", broker="questrade", row=row,
                     key="SYN.TO")
        m = IK.Move(IK.CONTRIBUTION, taxable=leg, registered=None,
                    plan="tfsa", quantity=10.0, date="2025-06-02",
                    problem="no_value")
        _head, details = IK.message([m], [], {}, "canada", "CAD",
                                    "../inputs")
        self.assertIn("a .tt file in ../inputs/margin/", details[0])
        plan = IK.Leg(account="plan", broker="questrade", plan="tfsa",
                      row=dict(row, quantity=6.0), key="SYN.TO")
        _head, details = IK.parts_message([(leg, [plan])], "../inputs")
        self.assertIn("a .tt file in ../inputs/margin/", details[0])


if __name__ == "__main__":
    unittest.main()
