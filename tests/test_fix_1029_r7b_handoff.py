"""Fix round 7 (wave B) of 2026-10-29, the handoff group: the year
handoff's straddler and double checks, the deleted prior-year lock, the
all-history selection recompute and the journals report (synthetic
data only)."""
import csv
import io
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price, acct="99900001"):
    gross = abs(qty) * price
    net = -gross if action == "Buy" else gross
    return (f"{trade} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,{acct},"
            f"Trades,Individual\n")


def _project(root, year, accounts=("margin",), prior=None):
    root.mkdir(parents=True, exist_ok=True)
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    if prior:
        head += f'prior_year_record = "{prior}"\n'
    (root / "taxjson.toml").write_text(head + "".join(
        f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


class TestStraddlerAccount(unittest.TestCase):
    """TJ-0807: a same-size sale in ANOTHER account does not stand in
    for the closed year's straddling sale that this project lost."""

    def _run(self, mode):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            old = _project(top / "2024", 2024, ("a", "b"))
            buys_a = _QT_HEADER + _qt("2024-03-01", "2024-03-04", "Buy",
                                      "SYN.TO", 10, 10)
            buys_b = _QT_HEADER + _qt("2024-03-01", "2024-03-04", "Buy",
                                      "SYN.TO", 10, 10, "99900002")
            cross = _qt("2024-12-31", "2025-01-02", "Sell", "SYN.TO", -5,
                        20)
            (old / "inputs/a/q.csv").write_text(buys_a + cross)
            (old / "inputs/b/q.csv").write_text(buys_b)
            tj(old, "run", "--no-input")
            tj(old, "close-year", "--yes", "--force")
            p = _project(top / "2025", 2025, ("a", "b"),
                         "../2024/filed/2024.json")
            (p / "inputs/a/q.csv").write_text(
                buys_a + (cross if mode == "complete" else ""))
            (p / "inputs/b/q.csv").write_text(
                buys_b + _qt("2025-01-02", "2025-01-03", "Sell", "SYN.TO",
                             -5, 20, "99900002"))
            tj(p, "run", "--no-input")
            r = tj(p, "handoff", "--json", check=False)
            return r.returncode, json.loads(r.stdout)

    def test_other_accounts_sale_does_not_clear_the_missing_one(self):
        rc, rep = self._run("missing_a")
        self.assertEqual(rc, 1, rep)
        self.assertEqual([(m["account"], m["symbol"]) for m in rep["missed"]],
                         [("a", "SYN.TO")])

    def test_complete_books_pass(self):
        rc, rep = self._run("complete")
        self.assertEqual((rc, rep["missed"], rep["double"]), (0, [], []),
                         rep)


class TestOwnClosedSplit(unittest.TestCase):
    """TJ-0808: the closed year's sale out of split-created units is this
    project's own row, not a double of a same-size January sale."""

    def _run(self, start):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            old = _project(top / "2024", 2024)
            hist = (start
                    + "BUYSELL 2024-12-30 09:30:00 SYN.TO -2 CAD 15 30 0\n"
                    "BUYSELL 2024-12-31 09:30:00 SYN.TO 2 CAD 10 20 0\n")
            (old / "inputs/margin/trades.tt").write_text(hist)
            tj(old, "run", "--no-input")
            tj(old, "close-year", "--yes", "--force")
            p = _project(top / "2025", 2025,
                         prior="../2024/filed/2024.json")
            (p / "inputs/margin/trades.tt").write_text(
                hist + "BUYSELL 2025-01-02 09:30:00 SYN.TO -2 CAD 15 30 0\n")
            tj(p, "run", "--no-input")
            r = tj(p, "handoff", "--json", check=False)
            return r.returncode, json.loads(r.stdout)

    def test_split_history_passes(self):
        rc, rep = self._run(
            "BUYSELL 2024-01-02 09:30:00 SYN.TO 1 CAD 10 10 0\n"
            "SPLIT 2024-12-02 09:30:00 SYN.TO SYN.TO 2\n")
        self.assertEqual((rc, rep["double"]), (0, []), rep)

    def test_no_split_control_passes(self):
        rc, rep = self._run(
            "BUYSELL 2024-01-02 09:30:00 SYN.TO 2 CAD 5 10 0\n")
        self.assertEqual((rc, rep["double"]), (0, []), rep)


def _rbc_row(day, action, q, cur, desc, price="", value=0):
    return [day, action, "SYN", "SYNTHETIC FUND", q, price, day, "99900001",
            value, cur, desc]


class TestJournalsSameDay(unittest.TestCase):
    """TJ-0850: two same-day, same-size RBC journals with distinct J~
    references are two journals in `tjs journals`."""

    def _run(self, second_day):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, ("a",))
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]'))
            rows = [["Date", "Activity", "Symbol", "Symbol Description",
                     "Quantity", "Price", "Settlement Date", "Account",
                     "Value", "Currency", "Description"],
                    _rbc_row("March 11, 2025", "Buy", 40, "USD",
                             "SYNTHETIC FUND UNSOLICITED DA", 10, -400)]
            for n, day in enumerate(("May 12, 2025", second_day)):
                ref = f"J~0TFR{n + 1}"
                rows += [_rbc_row(day, "Transfers", -10, "USD",
                                  f"TFR - SYNTHETIC FUND TRANSFER TO C$  "
                                  f"{ref}"),
                         _rbc_row(day, "Transfers", 10, "CAD",
                                  f"TFR - SYNTHETIC FUND TRANSFER FROM U$  "
                                  f"{ref}")]
            buf = io.StringIO()
            csv.writer(buf).writerows(rows)
            (p / "inputs/a/rbc.csv").write_text(
                '"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
                + buf.getvalue())
            (p / "work").mkdir()
            d, rates = date(2025, 1, 1), []
            while d <= date(2026, 1, 31):
                rates.append(f"{d} 12:00:00 USD CAD 1.3 boc")
                d += timedelta(days=1)
            (p / "work/to_base.csv").write_text("\n".join(rates) + "\n")
            tj(p, "run", "--no-input")
            return json.loads(tj(p, "journals", "--json").stdout)

    def test_same_day_journals_are_two(self):
        rep = self._run("May 12, 2025")
        self.assertEqual(len(rep["journals"]), 2, rep)
        self.assertEqual(rep["counts"]["joined"], 2, rep)
        self.assertEqual(sum(j["quantity"] for j in rep["journals"]), 20)

    def test_different_days_control(self):
        rep = self._run("May 13, 2025")
        self.assertEqual(len(rep["journals"]), 2, rep)
        self.assertEqual(rep["counts"]["joined"], 2, rep)


class TestAllHistoryPooledCost(unittest.TestCase):
    """TJ-0677: a window outside the tax year keeps the ACB pooled across
    the taxable accounts, as the tax year's books do."""

    def test_stats_all_equals_the_tax_year(self):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, ("a", "b"))
            (p / "inputs/a/t.tt").write_text(
                "BUYSELL 2025-01-02 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-06-03 10:00:00 SYN.TO -100 CAD 3 300 0\n")
            (p / "inputs/b/t.tt").write_text(
                "BUYSELL 2025-02-03 10:00:00 SYN.TO 100 CAD 2 200 0\n")
            tj(p, "run", "--no-input")
            year = json.loads(tj(p, "stats", "2025", "--json").stdout)
            full = json.loads(tj(p, "stats", "all", "--json").stdout)
            net = [d["classes"]["long-shares"]["net_pl"]
                   for d in (year, full)]
            self.assertEqual(net, [150.0, 150.0])


class TestDeletedLockMigratedYear(unittest.TestCase):
    """TJ-0272 (left open): a year closed as a single folder and then
    moved by `migrate --to-years` carries no freeze note; its deleted
    lock is still a missing lock for the next year, read from the
    reports close-year kept in filed/<year>/reports/."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
            "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 5.00 25.00 0.00\n")
        (top / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
        for d, args in ((top, ("run", "--no-input")),
                        (top, ("close-year", "--yes")),
                        (top, ("migrate", "--to-years")),
                        (top / "2024", ("new-year", "2025")),
                        (top / "2025", ("run", "--no-input"))):
            tj(d, *args)
        cls.top = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.p = Path(td.name) / "p"
        shutil.copytree(self.top, self.p, symlinks=True)
        self.y5 = self.p / "2025"

    def test_the_year_has_no_freeze_note(self):
        self.assertNotIn("## frozen by",
                         (self.p / "2024" / "taxjson.toml").read_text())
        r = tj(self.y5, "estimate", "--json")
        self.assertEqual(
            json.loads(r.stdout)["estimate"]["losses_opening"], 25.0)

    def test_deleted_lock_stops_estimate_carryover_handoff(self):
        (self.p / "2024" / "filed" / "2024.json").unlink()
        for cmd, rc in (("estimate", 1), ("carryover", 1), ("handoff", 2)):
            r = tj(self.y5, cmd, check=False)
            self.assertEqual(r.returncode, rc, (cmd, r.stdout, r.stderr))
            self.assertIn("the 2024 close-year lock is missing, while the "
                          "2024 project holds the reports close-year kept "
                          "as filed", r.stdout + r.stderr, cmd)

    def test_never_closed_is_still_no_record(self):
        (self.p / "2024" / "filed" / "2024.json").unlink()
        shutil.rmtree(self.p / "2024" / "filed" / "2024")
        r = tj(self.y5, "estimate", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
