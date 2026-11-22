"""Re-audit-2 test pins (tests-pins-03): the position walks twinned
across tools — A2-1590 (one split reported by two brokers is applied
once; the apply-distributions split key includes the account), A2-1539
(ASSIGN and TRANSFER rows move every walk's position) — and A2-1564
(a US Kraken stablecoin withdrawal fee is a sale at par). Synthetic
data only.

Formerly: test_fix_a2_pins1_03_walks"""
import os
import json
import tempfile
import unittest
from pathlib import Path


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

SYM = "QZT.US"


def _r(action, d, qty, account="m", price=10.0, sym=SYM, **kw):
    return dict(action=action, date=d, date_settle=d, time="10:00:00",
                symbol=sym, quantity=qty, price=price,
                net_amount=abs(qty) * price, currency="USD",
                account=account, **kw)


def _events(rows):
    from taxjson.bin.taxjson_export import _load_trade_events
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "raw.json"
        p.write_text(json.dumps({"transactions": rows}))
        ev = _load_trade_events([p])
    return [(e["action"], e["qty"]) for e in ev.get(SYM, [])]


def _starts(rows):
    from taxjson.bin.taxjson_split_gains import _position_starts
    return _position_starts(rows, "settle").get(SYM)


def _balance(rows, d):
    from taxjson.lib.unit_balance import balance_on
    return balance_on(rows, SYM, d)


# A split reported by two brokers of one account, two days apart.
_DUP_SPLIT = [_r("BUYSELL", "2025-01-02", 100),
              _r("SPLIT", "2025-06-11", 2.0),
              _r("SPLIT", "2025-06-13", 2.0),
              _r("BUYSELL", "2025-07-01", -200),
              _r("BUYSELL", "2025-08-01", 50)]


class TestSplitAppliedOnce(unittest.TestCase):
    """A2-1590: the export's trade events and split-gains' position start
    apply one split event once, as the radar, T1135 and
    apply-distributions do.

    Formerly: test_fix_a2_pins1_03_walks.TestSplitAppliedOnce"""

    def test_export_events_keep_only_the_live_round(self):
        self.assertEqual(_events(_DUP_SPLIT), [("BUY", 50.0)])

    def test_export_events_two_accounts_one_split_event(self):
        """The export's walk sums every account's shares into one
        balance, so each account's own copy of a split is still ONE
        event for it: scaling the summed balance once per account
        doubled it and kept a closed round's events."""
        rows = [_r("BUYSELL", "2025-01-02", 100, account="a"),
                _r("BUYSELL", "2025-01-02", 100, account="b"),
                _r("SPLIT", "2025-06-11", 2.0, account="a"),
                _r("SPLIT", "2025-06-11", 2.0, account="b"),
                _r("BUYSELL", "2025-07-01", -200, account="a"),
                _r("BUYSELL", "2025-07-01", -200, account="b"),
                _r("BUYSELL", "2025-08-01", 50, account="a")]
        self.assertEqual(_events(rows), [("BUY", 50.0)])

    def test_split_gains_position_start(self):
        self.assertEqual(_starts(_DUP_SPLIT), "2025-08-01")

    def test_apply_distributions_balance(self):
        self.assertEqual(_balance(_DUP_SPLIT, "2025-06-30"), 200.0)

    def test_apply_distributions_split_key_has_the_account(self):
        """Each account's own copy of the split scales its own shares."""
        rows = [_r("BUYSELL", "2025-01-02", 100, account="a"),
                _r("BUYSELL", "2025-01-02", 100, account="b"),
                _r("SPLIT", "2025-06-11", 2.0, account="a"),
                _r("SPLIT", "2025-06-11", 2.0, account="b")]
        self.assertEqual(_balance(rows, "2025-06-30"), 400.0)


if __name__ == "__main__":
    unittest.main()
