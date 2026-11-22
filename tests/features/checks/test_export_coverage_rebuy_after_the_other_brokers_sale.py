"""Fix round 7 (wave F) of 2026-10-30, the closecash group: regressions of
wave E's repairs. A broker sale of units the coverage replay cannot see, a
transit whose arrival settled in the year, a later rebuy at another
broker and an INKIND line check-tt cannot size yet (synthetic data only).

Formerly: test_fix_1029_r7f_closecash"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj
from _support.filing_coverage_project import _GENERIC_MAP, _cfg
from _support.filing_coverage_project import _project as _cov_project
from _support.ca_t1135_cash import _generic

from taxjson.lib import export_coverage as EC


class TestTJ1287LaterRebuyAtTheOtherBroker(unittest.TestCase):
    """TJ-1287 (a regression of TJ-1158's fix): a later buy closed the
    other broker's earlier sale as a short, so the sale closed nothing.

    Formerly: test_fix_1029_r7f_closecash.TestTJ1287LaterRebuyAtTheOtherBroker"""

    def _gaps(self, alpha, beta):
        with tempfile.TemporaryDirectory() as td:
            root = _cov_project(Path(td) / "p", 2025, {"margin": "taxable"},
                                extra="combined_broker_accounts = true\n")
            for broker, acct, rows in (("alpha", "99900001", alpha),
                                       ("beta", "99900002", beta)):
                f = root / "inputs/margin" / f"generic_{broker}.csv"
                f.write_text(_generic(rows))
                f.with_name(f.name + ".toml").write_text(
                    _GENERIC_MAP.format(broker=broker, account=acct))
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [(g.broker, g.positions) for g in gaps if not g.info]

    BUY = ("2025-03-03", "BUY", "SYN.TO", 10)
    SELL = ("2025-05-01", "SELL", "SYN.TO", -10)
    TAIL = ("2025-12-15", "BUY", "ZZZ.TO", 5)

    def test_the_rebuy_changes_nothing(self):
        got = self._gaps([self.BUY], [
            self.SELL, ("2025-09-01", "BUY", "SYN.TO", 10), self.TAIL])
        self.assertEqual(got, [])

    def test_its_own_buy_still_spends_its_sale(self):
        got = self._gaps([self.BUY], [
            ("2025-03-03", "BUY", "SYN.TO", 10), self.SELL, self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 10.0)])])

    def test_only_a_marked_short_or_a_written_option_opens_a_lot(self):
        def spent_of(sym, **extra):
            rows = [{"action": "BUYSELL", "date": "2025-03-03", "symbol": sym,
                     "quantity": -1, **extra},
                    {"action": "BUYSELL", "date": "2025-05-01", "symbol": sym,
                     "quantity": 1}]
            spent = {}
            EC._spend_own_units(rows, spent, False)
            return [spent.get(id(r), 0.0) for r in rows]
        self.assertEqual(spent_of("SYN.TO"), [0.0, 0.0])
        self.assertEqual(spent_of("SYN.TO", open_close="C"), [0.0, 0.0])
        self.assertEqual(spent_of("SYN.TO", open_close="O"), [-1.0, 1.0])
        self.assertEqual(spent_of("SYN.TO", open_close="C;O"), [-1.0, 1.0])
        self.assertEqual(spent_of("SYN260116C00010000"), [-1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
