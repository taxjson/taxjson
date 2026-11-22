"""Fix round 8, group stats: the performance views and an option's premium
around deliveries, returns of capital and snapshots. Every fixture is
SYNTHETIC: made-up tickers, ids and amounts.

Formerly: test_fix_r8_stats
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _qa_project import tj


def _project(td, books, *, country="canada", year=2025):
    root = Path(td) / "p"
    for acct, text in books.items():
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "trades.tt").write_text(text)
    (root / "ticker.map").write_text("")
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{"USD" if country == "usa" else "CAD"}"\n'
        'source_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
    tj(root, "run", "--no-input")
    return root


class TestASnapshotAndAPlainRowAtTheStrike(unittest.TestCase):
    """TJ-1332: the snapshot replaced the delivery's row (it is small because
    delivered shares were sold first): a later plain row at the strike takes
    no premium. TJ-1325: nothing says the row is not the delivery and the
    snapshot could hold it: an ordinary trade, and the run says to mark it.

    Formerly: test_fix_r8_stats.TestASnapshotAndAPlainRowAtTheStrike"""

    CALL = "SYNQ251231C00010000.US"
    PUT = "SYNQ251231P00010000.US"
    HINT = "write it as ASSIGN"

    def _run(self, country, book):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"m": book}, country=country, year=2026)
            r = tj(root, "run", "--no-input")
            doc = json.loads(next(
                p for p in (root / "work" / "m_gains_wash.json",
                            root / "work" / "m_gains.json")
                if p.exists()).read_text())
            return ([t for t in doc["transactions"]
                     if t.get("qty") and t.get("symbol") == "SYNQ.US"],
                    r.stdout + r.stderr)

    def _sold_first(self, cur, buy="BUYSELL", opt=None, q=1, cost=100):
        opt = opt or self.CALL
        back = -q
        return (f"BUYSELL 2024-06-03 10:00:00 {opt} {q} {cur} 1 100 0\n"
                f"ASSIGN 2025-12-29 16:00:00 {opt} {back} {cur} 0 0 0\n"
                f"ASSIGN 2025-12-29 16:00:00 SYNQ.US 100 {cur} 10 1000 0\n"
                f"BUYSELL 2025-12-30 10:00:00 SYNQ.US -50 {cur} 11 550 0\n"
                f"OPENING 2025-12-31 SYNQ.US 50 {cur} {cost} 2024-06-03\n"
                f"{buy} 2026-01-05 10:00:00 SYNQ.US 100 {cur} 10 1000 0\n"
                f"BUYSELL 2026-02-10 10:00:00 SYNQ.US -150 {cur} 12 1800 0\n")

    @rule(R.CA_OPEN_03)
    def test_a_cut_row_too_small_to_be_the_delivery(self):
        # 50 shares at the strike cannot be the delivery of 100: the premium
        # stays on the later purchase.
        opt = "SYNQ250620P00010000.TO"
        def gain(cut):
            with tempfile.TemporaryDirectory() as td:
                root = _project(td, {"m": (
                    f"BUYSELL 2024-11-04 10:00:00 {opt} -1 CAD 2 200 0\n"
                    f"ASSIGN 2024-12-30 10:00:00 {opt} 1 CAD 0 0 0\n"
                    + cut +
                    "OPENING 2024-12-31 SYNQ.TO 50 CAD 500\n"
                    "BUYSELL 2025-01-02 10:00:00 SYNQ.TO 100 CAD 10 1000 0\n"
                    "BUYSELL 2025-05-05 10:00:00 SYNQ.TO -150 CAD 12 1800 0\n"
                )}, country="canada", year=2025)
                tj(root, "run", "--no-input")
                doc = json.loads(next(
                    p for p in (root / "work" / "m_gains_wash.json",
                                root / "work" / "m_gains.json")
                    if p.exists()).read_text())
                return sum(float(t.get("gain") or 0)
                           for t in doc["transactions"] if t.get("qty"))
        self.assertAlmostEqual(float(gain(
            "BUYSELL 2024-12-29 10:00:00 SYNQ.TO 50 CAD 10 500 0\n")), 500.0,
            places=2)
        self.assertAlmostEqual(float(gain("")), 500.0, places=2)      # control

    @rule(R.CA_OPEN_03)
    def test_canada_delivered_shares_sold_before_the_snapshot(self):
        rows, out = self._run("canada", self._sold_first("CAD", cost=550))
        self.assertAlmostEqual(float(rows[0]["gain"]), 250.0, places=2)
        self.assertNotIn(self.HINT, out)

    @rule(R.US_OPEN_03)
    def test_us_delivered_shares_sold_before_the_snapshot(self):
        rows, _out = self._run("usa", self._sold_first("USD", cost=550))
        self.assertAlmostEqual(float(rows[-1]["gain"]), 200.0, places=2)
        self.assertAlmostEqual(float(rows[-1]["cost"]), 1000.0, places=2)

    @rule(R.CA_OPEN_03)
    def test_canada_written_put_likewise(self):
        book = self._sold_first("CAD", opt=self.PUT, q=-1, cost=450)
        rows, _out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 350.0, places=2)

    @rule(R.CA_OPEN_03)
    def test_control_a_marked_row_after_it_is_the_delivery(self):
        # The cut row is another exercise's size: the mark still wins.
        book = self._sold_first("CAD", buy="ASSIGN", cost=550)
        rows, _out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 150.0, places=2)

    @rule(R.CA_OPEN_03)
    def test_control_tj_1167_an_off_strike_row_cut_is_not_the_delivery(self):
        # An ordinary purchase the snapshot replaced; the delivery is dated
        # after it, at the strike, and the snapshot cannot hold it.
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.CALL} 1 CAD 1 100 0\n"
                "BUYSELL 2025-12-29 10:00:00 SYNQ.US 50 CAD 8 400 0\n"
                "OPENING 2025-12-31 SYNQ.US 50 CAD 400 2024-06-03\n"
                f"ASSIGN 2025-12-31 16:00:00 {self.CALL} -1 CAD 0 0 0\n"
                "BUYSELL 2026-01-02 09:30:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n")
        rows, out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 200.0, places=2)
        self.assertNotIn(self.HINT, out)

    @rule(R.CA_OPEN_03)
    def test_tj_1325_a_snapshot_that_could_hold_it_says_to_mark_the_row(self):
        for opening, plain, name in (
                ("200 CAD 1600", "100 CAD 10 1000", "purchase"),
                ("200 CAD 1600", "-100 CAD 10 1000", "sale")):
            with self.subTest(name):
                opt = self.CALL
                back = -1
                if name == "sale":
                    back = 1
                book = (f"BUYSELL 2024-06-03 10:00:00 {opt} {-back} CAD 1 100 "
                        "0\n"
                        f"OPENING 2025-12-31 SYNQ.US {opening} 2024-06-03\n"
                        f"ASSIGN 2025-12-31 16:00:00 {opt} {back} CAD 0 0 0\n"
                        f"BUYSELL 2026-01-02 09:30:00 SYNQ.US {plain} 0\n")
                _rows, out = self._run("canada", book)
                self.assertIn(self.HINT, out)
                self.assertIn(f"{opt}: if the row of 2026-01-02", out)
                # Once, as the engine's passes repeat.
                self.assertEqual(out.count(f"{opt}: if the row of"), 1)

    @rule(R.CA_OPEN_03)
    def test_control_tj_1297_the_delivery_cut_needs_no_warning(self):
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.CALL} 1 CAD 1 100 0\n"
                f"ASSIGN 2025-12-30 16:00:00 {self.CALL} -1 CAD 0 0 0\n"
                "ASSIGN 2025-12-30 16:00:00 SYNQ.US 100 CAD 10 1000 0\n"
                "OPENING 2025-12-31 SYNQ.US 100 CAD 1100 2024-06-03\n"
                "BUYSELL 2026-01-05 10:00:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n")
        rows, out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 150.0, places=2)
        self.assertNotIn(self.HINT, out)


if __name__ == "__main__":
    unittest.main()
