"""Shared test helpers: a Canadian project's work/ files written by hand, with no engine run (a fixture base), and their transaction and gains rows: a sale across the year end, losses at a window's edge, and a call and an RRSP purchase inside a loss window.

Shared from: test_edge_cases
"""
import json
import tempfile
import unittest
from pathlib import Path


def _tx(acct, action, date, settle, sym, qty, net=0.0, tid=None):
    return {"account": acct, "action": action, "date": date,
            "date_settle": settle, "time": "10:00:00", "symbol": sym,
            "quantity": qty, "net_amount": net, "currency": "CAD",
            "id": tid or f"{sym}-{date}-{qty}"}


def _gain(acct, date, settle, sym, qty, raw, denied=0.0, perm=0.0, tid=None):
    return {"account": acct, "date": date, "date_settle": settle,
            "symbol": sym, "qty": qty, "gain": raw + denied,
            "raw_gain": raw, "disallowed_amount": denied,
            "permanently_disallowed": perm, "proceeds": 1000.0,
            "cost": 1000.0 - raw, "id": tid or f"g-{sym}-{date}"}


class _EdgeProject(unittest.TestCase):
    """A Canadian project's work/ files (settlement basis): a sale across
    the year end, losses at a window's edge on either basis, and a call
    and an RRSP purchase inside a loss window: the fixture of
    EdgeCaseProject, and of the edge-case tests elsewhere."""

    CFG = {"settings": {"year": 2025, "country": "canada",
                        "tax_date": "settle"},
           "accounts": {"margin": {"type": "taxable"},
                        "rrsp": {"type": "sheltered"}}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        work = self.root / "work"
        work.mkdir()
        margin = [
            # Straddles the 2025/2026 year end: a 2026 disposition.
            _tx("margin", "BUYSELL", "2025-01-02", "2025-01-03", "XYZ.US", 100),
            _tx("margin", "BUYSELL", "2025-12-31", "2026-01-02", "XYZ.US", -100),
            # Loss on 2025-03-03 (settle 03-04); rebuy traded 04-02
            # settling 04-03: day 30 on the settlement basis (inside),
            # day 30 on the trade basis too; another rebuy traded 04-03
            # settling 04-04 is day 31 (outside) on settle, day 32 trade.
            _tx("margin", "BUYSELL", "2025-01-10", "2025-01-13", "ABC.TO", 100),
            _tx("margin", "BUYSELL", "2025-03-03", "2025-03-04", "ABC.TO", -100),
            _tx("margin", "BUYSELL", "2025-04-03", "2025-04-04", "ABC.TO", 50),
            # Settlement basis day 30 exactly, trade basis day 31.
            _tx("margin", "BUYSELL", "2025-02-01", "2025-02-03", "DEF.TO", 10),
            _tx("margin", "BUYSELL", "2025-03-03", "2025-03-04", "DEF.TO", -10),
            _tx("margin", "BUYSELL", "2025-04-02", "2025-04-03", "DEF.TO", 10),
            # A call on ABC bought inside the ABC loss window, still held.
            _tx("margin", "BUYSELL", "2025-03-10", "2025-03-11",
                "ABC260116C00010000.TO", 1),
        ]
        rrsp = [_tx("rrsp", "BUYSELL", "2025-04-01", "2025-04-02",
                    "ABC.TO", 5)]
        (work / "margin_base.json").write_text(json.dumps(margin))
        (work / "rrsp_base.json").write_text(json.dumps(rrsp))
        gains = {"transactions": [
            _gain("margin", "2025-12-31", "2026-01-02", "XYZ.US", 100, 500.0),
            _gain("margin", "2025-03-03", "2025-03-04", "ABC.TO", 100, -300.0,
                  denied=15.0, perm=15.0),
            _gain("margin", "2025-03-03", "2025-03-04", "DEF.TO", 10, -40.0,
                  denied=40.0)],
            "inventory": [{"account": "margin", "symbol": "DEF.TO",
                           "qty": 10, "deferred_wash": 40.0}]}
        (work / "margin_gains_wash.json").write_text(json.dumps(gains))

    def tearDown(self):
        self.tmp.cleanup()
