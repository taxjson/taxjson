"""Medium-round fixes, carryover ledger (filing-a): S001-03, S027-13,
S028-03, R1-279, R1-203/S066-16 (units guidance). Synthetic data only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_carryover import (build_canada_ledger, load_claimed,
                                           main)


def tx(date, qty, net, price, symbol="XEI.TO"):
    return {"action": "BUYSELL", "date": date, "date_settle": date,
            "time": "09:30:00", "symbol": symbol, "quantity": qty,
            "price": price, "net_amount": net, "currency": "CAD"}


class TestPendingClaimCarrybackWindow(unittest.TestCase):
    """S001-03: a claim recorded under 2021 (pre-book losses) must not
    be satisfied by a 2025 loss — a net capital loss carries back only
    3 years (ITA s.111(1)(b), T1A)."""

    def test_claim_outside_window_stays_unmatched(self):
        nets = {2021: {"net": 10000.0, "dispositions": 1},
                2025: {"net": -5000.0, "dispositions": 1}}
        err = io.StringIO()
        with redirect_stderr(err):
            ledger = build_canada_ledger(nets, {2021: 3000.0})
        by_year = {r["year"]: r for r in ledger["rows"]}
        self.assertAlmostEqual(by_year[2025]["claimed_applied"], 0.0)
        self.assertAlmostEqual(ledger["final_carryforward"], 5000.0)
        self.assertAlmostEqual(ledger["unmatched_claims"], 3000.0)

    def test_claim_inside_window_consumed(self):
        # Recorded under the target year 2022, loss arrives 2025.
        nets = {2022: {"net": 5000.0, "dispositions": 1},
                2025: {"net": -8000.0, "dispositions": 1}}
        ledger = build_canada_ledger(nets, {2022: 5000.0})
        self.assertAlmostEqual(ledger["final_carryforward"], 3000.0)
        self.assertNotIn("unmatched_claims", ledger)

    def test_expired_part_only(self):
        # Claim 2021 of 3000; a 2023 loss of 1000 (inside) covers part;
        # the rest can never be met by the 2025 loss.
        nets = {2021: {"net": 10000.0, "dispositions": 1},
                2023: {"net": -1000.0, "dispositions": 1},
                2025: {"net": -5000.0, "dispositions": 1}}
        ledger = build_canada_ledger(nets, {2021: 3000.0})
        self.assertAlmostEqual(ledger["final_carryforward"], 5000.0)
        self.assertAlmostEqual(ledger["unmatched_claims"], 2000.0)


class TestClaimedThousands(unittest.TestCase):
    """S027-13: '1,234.56' and '$1,234.56' are the forms a T1 / NOA
    shows — accepted, not dropped."""

    def test_separators_and_dollar(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "claimed_losses.txt"
            p.write_text("2024 1,234.56\n2025 $2,000\n2026 $15.5\n"
                         "2027 12,34.5\n2028 1,2345\n")
            err = io.StringIO()
            with redirect_stderr(err):
                claimed = load_claimed(p)
        self.assertEqual(claimed, {2024: 1234.56, 2025: 2000.0,
                                   2026: 15.5})
        self.assertEqual(err.getvalue().count("line ignored"), 2)


HISTORY = [
    tx("2022-01-05", 100, 10000.0, 100.0),
    tx("2023-03-01", -50, 6000.0, 120.0),       # +1,000
    tx("2024-03-01", -50, 3000.0, 60.0),        # -2,000
]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestScopeAndPriorYears(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name) / "margin_base.json"
        self.base.write_text(json.dumps({"transactions": HISTORY}))

    def tearDown(self):
        self.td.cleanup()

    def test_scope_note_canada(self):
        """S028-03: slip gains (17400/17600) and the line-15300 FX gain
        are not in NET GAIN(LOSS) — say so."""
        rc, text, _ = _run([str(self.base), "--country", "canada"])
        self.assertEqual(rc, 0)
        for s in ("17400", "17600", "15300", "fx-cash"):
            self.assertIn(s, text)
        rc, js, _ = _run([str(self.base), "--country", "canada", "--json"])
        self.assertIn("15300", json.loads(js)["scope_note"])

    def test_scope_note_usa(self):
        # A US ledger needs USD books: CAD rows under --country usa are
        # refused now (A2-1118: the $3,000 offset applied to CAD).
        usd = Path(self.td.name) / "usd_base.json"
        usd.write_text(json.dumps({"transactions": [
            dict(t, currency="USD") for t in HISTORY]}))
        rc, text, _ = _run([str(usd), "--country", "usa"])
        self.assertIn("line 13", text)

    def test_prior_year_rows_flagged(self):
        """R1-279: rows before the project year are rebuilt from this
        project's books and may be partial."""
        rc, js, _ = _run([str(self.base), "--country", "canada",
                          "--project-year", "2024", "--json"])
        rows = {r["year"]: r for r in json.loads(js)["rows"]}
        self.assertTrue(rows[2023]["prior_year"])
        self.assertFalse(rows[2024]["prior_year"])
        rc, text, _ = _run([str(self.base), "--country", "canada",
                            "--project-year", "2024"])
        self.assertIn("before 2024", text)
        self.assertIn("partial", text)

    def test_claimed_help_states_units(self):
        """R1-203 / S066-16: --claimed says which amount to record."""
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main(["--help"])
        h = " ".join(out.getvalue().split())
        self.assertIn("25300", h)
        self.assertIn("line 21", h)


class TestWrapperPassesProjectYear(unittest.TestCase):
    def test_wrapper(self):
        import argparse
        from taxjson.bin.taxjson_run import cmd_carryover
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": HISTORY}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cmd_carryover(argparse.Namespace(dir=str(root),
                                                     claimed=None,
                                                     json=True))
            rows = {r["year"]: r for r in json.loads(out.getvalue())["rows"]}
        self.assertTrue(rows[2023]["prior_year"])


if __name__ == "__main__":
    unittest.main()
