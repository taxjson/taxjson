"""Fix round 7 (wave C) of 2026-10-29, the handoff and in-kind group:
handoff's lot checks with renamed accounts and re-described options,
ALLOWLOSS units, the in-kind contribution date on the registered side,
RESP withdrawal guidance, a per-share INKIND value with a contract
size, the ACQUIRED repair price for options and the SIMILAR warning
(synthetic data only).

Formerly: test_fix_1029_r7c_handoffinkind"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.country import CANADA as _CA


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root, accounts, year=2025, extra=""):
    """A Canadian project of `accounts` ({name: type}, a sheltered one
    a TFSA) with transfers kept, and its input folders."""
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    body = "".join(
        f'[accounts.{a}]\ntype = "{t}"\n'
        + ('plan = "tfsa"\ntransfers = true\n' if t == "sheltered" else "")
        for a, t in accounts.items())
    (root / "taxjson.toml").write_text(head + body + extra)
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


def _transfer(day, sym, qty, num, typ, desc="SYN TRANSFER"):
    return (f"{day},{day},TF6,{sym},{desc},{qty},0,0,0,0,CAD,Transfers,"
            f"{num},{typ}\n")


def _work(root, name):
    p = root / "work" / name
    return json.loads(p.read_text()) if p.exists() else None


class TestRespWithdrawalGuidance(unittest.TestCase):
    """TJ-0926: an RESP withdrawal's value is not said to be, without
    condition, an educational assistance payment on the student's T4A:
    a refund of contributions is tax-free, with no slip.

    Formerly: test_fix_1029_r7c_handoffinkind.TestRespWithdrawalGuidance"""

    @rule(R.CA_INKIND_05)
    def test_resp_text_names_refund_of_contributions(self):
        from types import SimpleNamespace
        from taxjson.lib import in_kind as IK
        m = SimpleNamespace(kind=IK.WITHDRAWAL, plan="resp")
        text = IK.withdrawal_income(m, _CA)
        self.assertIn("refund", text)
        self.assertIn("contributions", text)
        self.assertIn("no slip", text)
        self.assertNotIn("that value is an educational", text)
        m = SimpleNamespace(kind=IK.WITHDRAWAL, plan="rrsp")
        self.assertIn("T4RSP", IK.withdrawal_income(m, _CA))


class TestInKindDeclaredDatePlanSide(unittest.TestCase):
    """TJ-0925: an INKIND line's date is the plan's acquisition date for
    the superficial-loss rule too, not the plan's later posting.

    Formerly: test_fix_1029_r7c_handoffinkind.TestInKindDeclaredDatePlanSide"""

    def _run(self, plan_day):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered"})
            (root / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYN.TO 20 CAD 10 200 0\n"
                "BUYSELL 2025-05-05 09:30:00 SYN.TO -10 CAD 8 80 0\n"
                "INKIND 2025-06-02 SYN.TO -10 CAD 9 plan=tfsa\n")
            (root / "inputs/margin/qt.csv").write_text(
                _QH + _transfer("2025-06-02", "SYN.TO", -10, "99900001",
                                "Margin"))
            (root / "inputs/plan/qt.csv").write_text(
                _QH + _transfer(plan_day, "SYN.TO", 10, "99900002", "TFSA"))
            tj(root, "run", "--no-input")
            sb = _work(root, "sheltered_base.json") or {}
            buys = [t for t in sb.get("transactions", [])
                    if t.get("action") == "BUYSELL"
                    and t.get("account") == "plan"]
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            return [str(t.get("date"))[:10] for t in buys], fe

    @rule(R.CA_INKIND_04)
    def test_late_posting_takes_the_declared_date(self):
        late, fe_late = self._run("2025-06-05")
        same, fe_same = self._run("2025-06-02")
        self.assertEqual(late, ["2025-06-02"])
        self.assertEqual(same, ["2025-06-02"])
        # The May 5 loss of 20 is superficial (the plan holds the shares
        # from June 2, day 28, through day 30); the contribution's loss
        # of 10 is nil (TFSA): no gain, no loss.
        self.assertAlmostEqual(fe_late.get("gain_unrounded"), 0.0)
        self.assertEqual(fe_late.get("gain_unrounded"),
                         fe_same.get("gain_unrounded"))


if __name__ == "__main__":
    unittest.main()
