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


class TestInKindPerShareOptionSize(unittest.TestCase):
    """TJ-0933: an INKIND line's per-share price of an option is valued
    at the contract's own size (a ticker.map MULT line), not 100.

    Formerly: test_fix_1029_r7c_handoffinkind.TestInKindPerShareOptionSize"""

    _SYM = "SYN251219C00010000.TO"

    def _fmv(self, value):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered"})
            (root / "ticker.map").write_text(f"MULT {self._SYM} 10\n")
            (root / "inputs/margin/trades.tt").write_text(
                f"BUYSELL 2025-01-02 09:30:00 {self._SYM} 1 CAD 1 10 0 "
                f"x10\nINKIND 2025-06-02 {self._SYM} -1 CAD {value} "
                f"plan=tfsa\n")
            for a, q, num, typ in (("margin", -1, "99900001", "Margin"),
                                   ("plan", 1, "99900002", "TFSA")):
                (root / "inputs" / a / "qt.csv").write_text(
                    _QH + _transfer("2025-06-02", self._SYM, q, num, typ))
            r = tj(root, "run", "--no-input", check=False)
            ik = _work(root, "in_kind.json") or {}
            return r, [m.get("fmv") for m in ik.get("moves", [])], \
                list(ik.get("problems", []))

    def test_per_share_price_uses_mult(self):
        r, fmv, probs = self._fmv("2")
        self.assertEqual(fmv, [20.0], r.stdout[-2000:] + r.stderr[-2000:])
        self.assertEqual(probs, [])

    def test_total_control(self):
        _r, fmv, _p = self._fmv("0 20")
        self.assertEqual(fmv, [20.0])

    def test_price_and_total_agree_at_the_real_size(self):
        r, fmv, probs = self._fmv("2 20")
        self.assertEqual(fmv, [20.0], r.stdout[-2000:] + r.stderr[-2000:])
        self.assertEqual(probs, [])

    def test_price_and_total_disagree_at_the_real_size(self):
        _r, _fmv, probs = self._fmv("2 200")
        self.assertTrue(any("differs from" in p and "x 10" in p
                            for p in probs), probs)


if __name__ == "__main__":
    unittest.main()
