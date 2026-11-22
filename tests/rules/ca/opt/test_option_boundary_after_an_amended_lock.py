"""Backlog group `handoff` — handoff, carryover, carryforward, the
estimate, amt and option-boundary: TJ-0014, TJ-0016, TJ-0052, TJ-0068,
TJ-0069, TJ-0078, TJ-0084, TJ-0085, TJ-0115, TJ-0144, TJ-0145,
TJ-0146. Synthetic data only.

Formerly: test_fix_backlog_handoff"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth

from taxjson.model.transaction import TaxTransaction

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------------ TJ-0052
def _lock(path, year, realized, ncl, **extra):
    doc = {"schema_version": 2, "year": year, "country": "canada",
           "accounts": {"margin": {"realized": realized}},
           "totals": {"realized": realized},
           "carryforwards": {"version": 1, "net_capital_loss": ncl}}
    doc.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    return path


# ------------------------------------------------------------ TJ-0069
_TIMING = {"option_premium_timing": "grant", "option_grant_since": 2023}


# ------------------------------------------------------------ TJ-0085
def _T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD",
                             "account": "margin", **kw})


_OPT = "QZW250117C00030000.TO"


def _assigned_book():
    return [_T(date="2024-11-04", date_settle="2024-11-05", symbol=_OPT,
               quantity=-1, price=2.0, net_amount=198.0),
            _T(date="2025-01-10", date_settle="2025-01-13", symbol="QZW.TO",
               quantity=100, price=28, net_amount=-2800.0),
            _T(action="ASSIGN", date="2025-01-17", date_settle="2025-01-17",
               symbol=_OPT, quantity=1, price=0, net_amount=0.0),
            _T(action="ASSIGN", date="2025-01-17", date_settle="2025-01-20",
               symbol="QZW.TO", quantity=-100, price=30,
               net_amount=3000.0)]


class TestOptionBoundaryAmended(unittest.TestCase):
    """TJ-0085: once the write year's lock no longer reports the
    assigned contract's premium (the amendment re-closed), no T1-ADJ is
    asked for.

    Formerly: test_fix_backlog_handoff.TestOptionBoundaryAmended"""
    TODAY = date(2026, 9, 29)

    def _action(self, premiums):
        from taxjson.lib.option_boundary import straddling
        rows = straddling(_assigned_book(), 2025, "grant", 2024,
                          filed_years={2024}, today=self.TODAY,
                          filed_premiums=premiums)
        return [r for r in rows if r["close_kind"] == "assignment"][0]

    @rule(R.CA_OPT_07)
    def test_amended_lock_asks_no_t1_adj(self):
        from taxjson.lib.option_boundary import reported_premiums
        filed = reported_premiums({"dispositions": [
            {"symbol": _OPT, "date": "2024-11-04",
             "date_settle": "2024-11-05", "gain": 198.0}]})
        self.assertTrue(self._action(None)["action"].startswith("T1-ADJ"))
        self.assertTrue(self._action({2024: filed})["action"]
                        .startswith("T1-ADJ"))
        amended = reported_premiums({"dispositions": [
            {"symbol": "ZZOT.TO", "date": "2024-05-01", "gain": 5.0}]})
        act = self._action({2024: amended})["action"]
        self.assertTrue(act.startswith("no amendment"), act)
        self.assertIn("no longer reports the 198.00 premium", act)
        # A lock with no disposition list says nothing: the T1-ADJ stays.
        self.assertIsNone(reported_premiums({"year": 2024}))
        from taxjson.lib import tax_logic as TL
        txt = [rr.text for _t, rs in TL.rule_sections(
            "canada", {"country": "canada", "year": 2025})
            for rr in rs if rr.id == "CA-OPT-07"][0]
        self.assertIn("no longer reports the premium", txt)

    @rule(R.CA_OPT_07)
    def test_filed_locks_collect_the_reported_premiums(self):
        from taxjson.lib.option_boundary import filed_locks
        with tempfile.TemporaryDirectory() as td:
            _lock(Path(td) / "filed" / "2024.json", 2024, 0.0,
                  {"opening": 0.0, "created": 0.0, "applied": 0.0,
                   "closing": 0.0}, option_timing=_TIMING,
                  dispositions=[{"symbol": _OPT, "date": "2024-11-04",
                                 "gain": 198.0}])
            got = {}
            filed_locks(td, {"country": "canada", "year": 2025},
                        premiums=got)
            self.assertEqual(got, {2024: {(_OPT, "2024-11-04"): 198.0}})


if __name__ == "__main__":
    unittest.main()
