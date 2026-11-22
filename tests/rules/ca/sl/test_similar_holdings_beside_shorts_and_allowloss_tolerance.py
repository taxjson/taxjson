"""Fix round 7 (wave C) of 2026-10-29, the handoff and in-kind group:
handoff's lot checks with renamed accounts and re-described options,
ALLOWLOSS units, the in-kind contribution date on the registered side,
RESP withdrawal guidance, a per-share INKIND value with a contract
size, the ACQUIRED repair price for options and the SIMILAR warning
(synthetic data only).

Formerly: test_fix_1029_r7c_handoffinkind"""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R

from taxjson.lib import loss_overrides as LO
from taxjson.lib.country import CANADA as _CA


def _ov(date, symbol, qty=None, account="margin", reason="r"):
    return {"account": account, "date": date, "symbol": symbol,
            "qty": qty, "reason": reason, "where": "inputs/margin/x.tt:1",
            "line": f"ALLOWLOSS {date} {symbol} reason=\"{reason}\""}


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


def _work(root, name):
    p = root / "work" / name
    return json.loads(p.read_text()) if p.exists() else None


def _gains(book, **req):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(copy.deepcopy(list(book)), [], (),
                         req=GainsRequest(country=_CA, taxable=True, **req))


class TestAllowLossUnits(unittest.TestCase):
    """TJ-0921: the units an ALLOWLOSS line gives match a sale's units
    within a tolerance relative to them, not an absolute 0.000001 that
    is half a percent of a small coin quantity.

    Formerly: test_fix_1029_r7c_handoffinkind.TestAllowLossUnits"""

    _BOOK = [tx("BUYSELL", "2025-01-02", "SYN.KR", 0.0001, -100,
                currency="CAD"),
             tx("BUYSELL", "2025-06-02", "SYN.KR", -0.0001, 90,
                currency="CAD"),
             tx("BUYSELL", "2025-06-03", "SYN.KR", 0.0001, -90,
                currency="CAD")]

    @rule(R.CA_SL_18)
    def test_small_quantity_must_match(self):
        for q, want in ((0.0001, LO.STATUS_APPLIED),
                        (0.0001005, LO.STATUS_UNMATCHED),
                        (0.0002, LO.STATUS_UNMATCHED)):
            with self.subTest(q=q):
                res = _gains(self._BOOK, loss_overrides=(
                    _ov("2025-06-02", "SYN.KR", qty=q),))
                self.assertEqual(res["loss_overrides"][0]["status"], want)

    @rule(R.CA_SL_18)
    def test_float_noise_still_matches(self):
        res = _gains(self._BOOK, loss_overrides=(
            _ov("2025-06-02", "SYN.KR", qty=0.0001 * (1 + 1e-12)),))
        self.assertEqual(res["loss_overrides"][0]["status"],
                         LO.STATUS_APPLIED)


class TestSimilarStillHeld(unittest.TestCase):
    """TJ-0935 / TJ-0936: the SIMILAR warning's still-held test counts
    the registered account's shares whatever another account's short
    says, and follows a dated rename of the replacement.

    Formerly: test_fix_1029_r7c_handoffinkind.TestSimilarStillHeld"""

    def _state(self, plan_extra="", other=""):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered",
                                       "other": "taxable"})
            (root / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYNA.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-06-02 09:30:00 SYNA.TO -10 CAD 8 80 0\n")
            (root / "inputs/plan/trades.tt").write_text(
                "BUYSELL 2025-06-03 09:30:00 SYNB.TO 10 CAD 10 100 0\n"
                + plan_extra)
            (root / "inputs/other/trades.tt").write_text(other)
            (root / "ticker.map").write_text("SIMILAR SYNA.TO SYNB.TO\n")
            r = tj(root, "run", "--no-input", check=False)
            st = _work(root, "similar.state") or {}
            return r, st.get("findings", [])

    def test_control(self):
        _r, f = self._state()
        self.assertEqual(len(f), 1)

    def test_short_elsewhere_does_not_hide_held_shares(self):
        r, f = self._state(other="BUYSELL 2025-01-02 09:30:00 SYNB.TO -10 "
                                  "CAD 12 120 0\n")
        self.assertEqual(len(f), 1, r.stdout[-2000:] + r.stderr[-2000:])

    def test_rename_inside_the_window_keeps_the_holding(self):
        r, f = self._state(plan_extra="RENAME 2025-06-05 SYNB.TO SYNC.TO\n")
        self.assertEqual(len(f), 1, r.stdout[-2000:] + r.stderr[-2000:])

    def test_renamed_then_sold_is_not_held(self):
        r, f = self._state(plan_extra=(
            "RENAME 2025-06-05 SYNB.TO SYNC.TO\n"
            "BUYSELL 2025-06-10 09:30:00 SYNC.TO -10 CAD 10 100 0\n"))
        self.assertEqual(f, [], r.stdout[-2000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
