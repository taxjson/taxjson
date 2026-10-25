"""Fixes for the 2026-10-25 workflow audit, group fxv2: the FX-on-cash
ledger v2 (lib/fx_cash_v2, lib/cash_events). One test per finding, each
named for it. Synthetic data only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as FX
from taxjson.lib.dispatch import python_module_argv

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _build(cash=None, carry=None, rate=1.0):
    return FX.build([], cash or {"events": [], "lines": []}, "CAD", 2026,
                    lambda c, d: rate, country="canada", carry=carry)


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *args])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=e, stdin=subprocess.DEVNULL,
                          timeout=600)


def _project(root: Path):
    """A 2026 project opted into ledger v2 whose books are already built
    (an empty work/margin_sorted.json): `fx-cash --ledger v2 --json`
    then reads only the cash events and the prior-year record."""
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "filed").mkdir(exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'fx_cash_ledger = "v2"\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_sorted.json").write_text(
        json.dumps({"transactions": []}))


class TestCarriedPool(unittest.TestCase):
    @rule("CA-FX-07")
    def test_year_end_2_carried_negative_cost_refused_as_cashopen(self):
        """year-end#2: fx-cash --ledger v2 computes and certifies a year
        from a carried pool with positive units and a negative cost,
        which CASHOPEN refuses (incomplete C02-C04 fix). The carried
        book is held to the CASHOPEN line's rule (one function): a cost
        below zero, or a cost on no units, is a damaged record and the
        ledger computes nothing. A cost of 0 opens, as it does for
        CASHOPEN."""
        def carry(v):
            return {"year": 2025, "currency": "CAD",
                    "books": {"margin/ib": {"USD": v}}}
        for units, cost in ((100, -5), (-100, -5), (0, 50)):
            doc = _build(carry=carry({"units": units, "cost": cost}))
            self.assertEqual(doc["status"], "not_computed", (units, cost))
            self.assertEqual(doc["opening"], {}, (units, cost))
            self.assertEqual(doc["pools_year_end"], {}, (units, cost))
            self.assertEqual(doc["carried_in"], 0, (units, cost))
            texts = [p["text"] for p in doc["problems"]
                     if p["kind"] == "carry"]
            self.assertEqual(len(texts), 1, doc["problems"])
            self.assertIn("margin/ib USD", texts[0])
            self.assertIn("filed/2025.json", texts[0])
            # The same words a CASHOPEN line of those figures gets.
            line = f"CASHOPEN 2026-01-01 USD {units} {cost} at=ib"
            with self.assertRaises(CE.CashLineError) as cm:
                CE.parse_line(line, "x.tt:1")
            why = CE.opening_problem(float(units), float(cost))
            self.assertTrue(why)
            self.assertIn(why, str(cm.exception))
            self.assertIn(why, texts[0])
        # What a CASHOPEN line accepts still opens the year.
        for units, cost, acb in ((100, 0, 0.0), (100, 140, 140.0)):
            doc = _build(carry=carry({"units": units, "cost": cost}))
            self.assertEqual(doc["status"], "computed", doc["problems"])
            self.assertEqual(doc["carried_in"], 1)
            self.assertEqual(doc["pools_year_end"],
                             {"USD": {"units": 100.0, "acb": acb}})
            self.assertIsNotNone(CE.parse_line(
                f"CASHOPEN 2026-01-01 USD {units} {cost} at=ib", "x.tt:1"))
        # The command: a record whose close was edited to a negative
        # cost computes nothing (it used to report a gain on it).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada",
                 "fx_cash_v2": carry({"units": 100, "cost": -5})}))
            r = _cli(root, "fx-cash", "--ledger", "v2", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "not_computed")
            self.assertEqual(out["pools_year_end"], {})
            self.assertEqual([p["kind"] for p in out["problems"]],
                             ["carry"])
            self.assertIn("-5.00 CAD", out["problems"][0]["text"])



def _cash(opening, events):
    """A cash book: a CASHOPEN (units, cost) per book, then `events`
    (book, event) in that order (their seq breaks a tie)."""
    cash = {"events": [], "lines": []}
    for book, (units, cost) in opening.items():
        cash["lines"].append(dict(
            kind="CASHOPEN", book=book, date="2026-01-01",
            currency="USD", units=units, cost=cost, origin="tt"))
    for book, ev in events:
        cash["events"].append(dict(ev, book=book))
    return cash


def _own(td, amount, sd):
    return dict(CE.move(td, "USD", amount, settle=sd), decl={"how": "own"})


def _two_accounts(root: Path, moved: str):
    """The finding's project: account a converts 1350.00 CAD into
    1000.00 USD on 2025-03-04 and moves the USD to account b on
    `moved` (both .tt lines)."""
    (root / "work").mkdir(parents=True)
    for acct in ("a", "b"):
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "work" / f"{acct}_sorted.json").write_text(
            json.dumps({"transactions": []}))
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        'fx_cash_ledger = "v2"\n[accounts.a]\ntype = "taxable"\n'
        '[accounts.b]\ntype = "taxable"\n')
    (root / "inputs" / "a" / "cash.tt").write_text(
        "CASHBOOK a\nCASHOPEN 2025-01-01 USD 0 0\n"
        "FXCONV 2025-03-04 CAD 1350.00 USD 1000.00\n"
        f"CASHMOVE {moved} USD -1000.00 own\n"
        "CASHBAL 2025-12-31 USD 0.00\n")
    (root / "inputs" / "b" / "cash.tt").write_text(
        "CASHBOOK b\nCASHOPEN 2025-01-01 USD 0 0\n"
        f"CASHMOVE {moved} USD 1000.00 own\n"
        "CASHBAL 2025-12-31 USD 1000.00\n")


class TestOwnMoveOrder(unittest.TestCase):
    @rule("CA-FX-07")
    def test_diff_engine_2_own_move_funded_same_day_is_not_a_debt(self):
        """diff-engine#2: A20 over-reach: fx-cash v2 now walks an
        own-account departure before the conversion that funded it on
        the same settle day, which makes up a debt, a debt gain, and a
        pool cost at spot instead of the amount paid. One settlement
        day is walked in trade-date order again; an own arrival the
        units in transit do not cover waits for its departure, or the
        departure comes up to it when its account holds the units."""
        # The finding: a conversion and the move of its USD on one day
        # give the figure of the move a day later — 1,000 USD at the
        # 1350.00 CAD paid, no debt, no gain.
        docs = {}
        for moved in ("2025-03-04", "2025-03-05"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _two_accounts(root, moved)
                r = _cli(root, "fx-cash", "--ledger", "v2", "--json")
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                docs[moved] = d = json.loads(r.stdout)
            self.assertEqual(d["status"], "computed", d["problems"])
            self.assertEqual(d["borrowed"], [], moved)
            self.assertEqual(d["repaid"], [], moved)
            self.assertEqual(d["net_gain"], 0.0, moved)
            self.assertEqual(d["pools_year_end"],
                             {"USD": {"units": 1000.0, "acb": 1350.0}})
        for k in ("net_gain", "per_currency", "pools_year_end", "close"):
            self.assertEqual(docs["2025-03-04"][k], docs["2025-03-05"][k])

        # The receiving broker books the move a day before the sending
        # one, which a same-day conversion funds: the arrival waits.
        doc = _build(_cash({"margin/a": (0, 0), "margin/b": (0, 0)}, [
            ("margin/b", _own("2026-03-03", 1000, "2026-03-04")),
            ("margin/a", CE.conv("2026-03-04", "CAD", 1350, "USD", 1000)),
            ("margin/a", _own("2026-03-04", -1000, "2026-03-04")),
            ("margin/a", CE.balance("2026-03-04", "USD", 0)),
            ("margin/b", CE.balance("2026-03-04", "USD", 1000))]),
            rate=1.3572)
        self.assertEqual([p["text"] for p in doc["problems"]], [])
        self.assertEqual(doc["borrowed"], [])
        self.assertEqual(doc["per_currency"]["USD"]["debt_gain"], 0.0)
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 1000.0, "acb": 1350.0}})

        # A20 kept, and the departure comes up to the arrival when its
        # account holds the units: account b spends 40 USD of them the
        # day after the arrival's trade date, a disposition at the
        # pool's 1.35 (gain 40 x (1.40 - 1.35) = 2.00), not a debt.
        doc = _build(_cash({"margin/a": (100, 135), "margin/b": (0, 0)}, [
            ("margin/b", _own("2026-02-01", 100, "2026-02-03")),
            ("margin/b", CE.flow("2026-02-02", "USD", -40,
                                 settle="2026-02-03")),
            ("margin/a", _own("2026-02-03", -100, "2026-02-03")),
            ("margin/a", CE.balance("2026-02-03", "USD", 0)),
            ("margin/b", CE.balance("2026-02-03", "USD", 60))]), rate=1.40)
        self.assertEqual([p["text"] for p in doc["problems"]], [])
        self.assertEqual(doc["borrowed"], [])
        self.assertEqual(doc["per_currency"]["USD"],
                         {"acquired": 0.0, "disposed": 40.0, "gain": 2.0,
                          "debt_gain": 0.0})
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 60.0, "acb": 81.0}})

        # ... and not when it would overdraw it: account a spends 50 of
        # its 100 USD (at 1.35, gain 50 x 0.05 = 2.50), converts 70.00
        # CAD into 50 USD and only then moves 100 USD out — the arrival
        # booked first waits for it. The pool: 50 USD at 67.50 + 50 at
        # 70.00 = 100 at 137.50.
        doc = _build(_cash({"margin/a": (100, 135), "margin/b": (0, 0)}, [
            ("margin/b", _own("2026-02-01", 100, "2026-02-03")),
            ("margin/a", CE.flow("2026-02-02", "USD", -50,
                                 settle="2026-02-03")),
            ("margin/a", CE.conv("2026-02-03", "CAD", 70, "USD", 50)),
            ("margin/a", _own("2026-02-03", -100, "2026-02-03")),
            ("margin/a", CE.balance("2026-02-03", "USD", 0)),
            ("margin/b", CE.balance("2026-02-03", "USD", 100))]),
            rate=1.40)
        self.assertEqual([p["text"] for p in doc["problems"]], [])
        self.assertEqual(doc["borrowed"], [])
        self.assertEqual(doc["per_currency"]["USD"],
                         {"acquired": 50.0, "disposed": 50.0, "gain": 2.5,
                          "debt_gain": 0.0})
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 137.5}})

        # Between the two: the departure comes up to the point its
        # account holds the units (after the conversion that funds it),
        # ahead of account b spending 400 of them; the arrival comes
        # right after it. 1000 USD at 1350.00; 400 spent at 1.40:
        # proceeds 560.00 - cost 540.00 = 20.00, a disposition, no debt;
        # the pool 600 USD at 810.00.
        doc = _build(_cash({"margin/a": (0, 0), "margin/b": (0, 0)}, [
            ("margin/b", _own("2026-03-02", 1000, "2026-03-04")),
            ("margin/a", CE.conv("2026-03-03", "CAD", 1350, "USD", 1000,
                                 settle="2026-03-04")),
            ("margin/b", CE.flow("2026-03-03", "USD", -400,
                                 settle="2026-03-04")),
            ("margin/a", _own("2026-03-04", -1000, "2026-03-04")),
            ("margin/a", CE.balance("2026-03-04", "USD", 0)),
            ("margin/b", CE.balance("2026-03-04", "USD", 600))]),
            rate=1.40)
        self.assertEqual([p["text"] for p in doc["problems"]], [])
        self.assertEqual(doc["borrowed"], [])
        self.assertEqual(doc["per_currency"]["USD"],
                         {"acquired": 1000.0, "disposed": 400.0,
                          "gain": 20.0, "debt_gain": 0.0})
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 600.0, "acb": 810.0}})

if __name__ == "__main__":
    unittest.main()
