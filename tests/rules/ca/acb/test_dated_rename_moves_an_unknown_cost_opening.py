"""TJ-5042 (and TJ-3240): a dated rename moves an unknown-cost opening.

A `.tt` line `RENAME <date> OLD NEW` is booked in every account whose
books hold OLD before the date (CA-ACB-RENAME / US-BASIS-RENAME). Units
a `.tt` line `OPENING <date> OLD <qty> cost=unknown` opens on or before
that date are held: before the fix the merge stage could not see them
(the line was opened later, by the stages that compute), so the run
said the rename "books nothing", kept 10 OLD at an unknown cost, read
the sale of NEW as a new short with no purchase, left
`manual_reporting_required` empty and `tjs renames --json` listed the
declaration as unused.

Now the merge holds the lines as rows while it books the renames
(lib/missing_history `hold_declared_openings`): the rename row is
booked, the opening moves to NEW, its sale stays an unknown-cost
disposition for manual reporting, both holdings end flat, and `tjs
renames` reports the rename with the units it carried.

Every fixture is synthetic: invented tickers, amounts and dates.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, settings_for
from taxjson.rules import ids as R
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_CUR = {"canada": "CAD", "usa": "USD"}
_SFX = {"canada": ".TO", "usa": ".US"}

# The entry's repro (probe TJ-3240_unknown_opening_not_renamed).
_REPRO = ("OPENING 2024-12-20 QZOLD{s} 10 cost=unknown\n"
          "RENAME 2025-01-10 QZOLD{s} QZNEW{s}\n"
          "BUYSELL 2025-02-10 10:00:00 QZNEW{s} -10 {c} 12 120 0\n")
# The opening dated on the rename's day: booked first on its day.
_SAME_DAY = _REPRO.replace("2024-12-20", "2025-01-10")
# Control: the same rename over a purchase in the files.
_BOUGHT = ("BUYSELL 2024-12-20 10:00:00 QZOLD{s} 10 {c} 10 100 0\n"
           "RENAME 2025-01-10 QZOLD{s} QZNEW{s}\n"
           "BUYSELL 2025-02-10 10:00:00 QZNEW{s} -10 {c} 12 120 0\n")
# Control: no account holds OLD before the date.
_NOT_HELD = ("RENAME 2025-01-10 QZOLD{s} QZNEW{s}\n"
             "BUYSELL 2025-02-10 10:00:00 QZNEW{s} 10 {c} 12 120 0\n")


def _project(tmp, country, tt):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2025, source_currencies=[])
        + '[accounts.margin]\ntype = "taxable"\n')
    acct = root / "inputs" / "margin"
    acct.mkdir(parents=True)
    (acct / "trades.tt").write_text(
        tt.format(s=_SFX[country], c=_CUR[country]))
    return root


def _held(doc):
    return {h["symbol"]: h["qty"] for h in doc.get("inventory") or []
            if abs(h.get("qty") or 0.0) > 1e-9}


class _Runs(unittest.TestCase):

    def _run(self, country, tt):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(td.name, country, tt)
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return root, r

    def _renames(self, root):
        r = cli(root, "renames", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return json.loads(r.stdout)

    def _moved(self, country, tt):
        s = _SFX[country]
        root, r = self._run(country, tt)
        said = " ".join((r.stdout + r.stderr).split())
        self.assertNotIn("books nothing", said)
        self.assertNotIn("go short", said)
        self.assertNotIn("with no purchase in your files", said)
        self.assertIn(f"1 sale at an unknown cost in 2025, NOT in `tjs "
                      f"sum`: QZNEW{s} (margin) on 2025-02-10, 10 units",
                      said)
        for name in ("margin_gains.json", "margin_gains_wash.json",
                     ".blend_gains_wash.json", "margin_raw_gains.json",
                     "margin_raw_base_gains.json"):
            with self.subTest(file=name):
                doc = json.loads((root / "work" / name).read_text())
                # The sale of the declared unknown-cost units: listed
                # for manual reporting, in no total; both holdings flat.
                self.assertEqual(doc["transactions"], [])
                self.assertEqual(
                    [(t["symbol"], t["date"], t["proceeds"])
                     for t in doc["manual_reporting_required"]],
                    [(f"QZNEW{s}", "2025-02-10", 120.0)])
                self.assertEqual(doc["summary"]["total_gain"], 0)
                self.assertEqual(_held(doc), {})
        # The books carry the rename's row, never the line's.
        rows = json.loads((root / "work" / "margin_base.json")
                          .read_text())["transactions"]
        self.assertEqual(
            [(t["date"], t["symbol"], t["symbol_new"], t["quantity"])
             for t in rows if t["action"] == "SPLIT"],
            [("2025-01-10", f"QZOLD{s}", f"QZNEW{s}", 1.0)])
        self.assertFalse([t for t in rows
                          if t["action"] == "OPENING_BALANCE"])
        doc = self._renames(root)
        self.assertEqual(doc["unused"], [])
        self.assertEqual(doc["pending"], 0)
        self.assertEqual(len(doc["renames"]), 1, doc)
        ev = doc["renames"][0]
        self.assertEqual((ev["date"], ev["old"], ev["new"], ev["source"]),
                         ("2025-01-10", f"QZOLD{s}", f"QZNEW{s}", "tt"))
        self.assertEqual(ev["carried"], [{
            "account": "margin", "sheltered": False, "qty": 10.0,
            "qty_after": 10.0, "book_cost": 0.0, "cost_unknown": True}])
        return root


class TestCanada(_Runs):

    @rule(R.CA_ACB_RENAME)
    def test_rename_moves_the_unknown_cost_opening(self):
        root = self._moved("canada", _REPRO)
        r = cli(root, "renames")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("margin: held 10 QZOLD.TO -> 10 QZNEW.TO",
                      " ".join(r.stdout.split()))
        self.assertIn("units opened at an unknown cost among them",
                      " ".join(r.stdout.split()))

    @rule(R.CA_ACB_RENAME)
    def test_an_opening_on_the_rename_day_is_moved(self):
        self._moved("canada", _SAME_DAY)

    @rule(R.CA_ACB_RENAME)
    def test_control_a_purchase_in_the_files_moves_as_before(self):
        root, r = self._run("canada", _BOUGHT)
        self.assertNotIn("books nothing",
                         " ".join((r.stdout + r.stderr).split()))
        doc = json.loads((root / "work" / "margin_gains.json").read_text())
        self.assertEqual([(t["symbol"], t["cost"], t["gain"])
                          for t in doc["transactions"]],
                         [("QZNEW.TO", 100.0, 20.0)])
        self.assertEqual(doc.get("manual_reporting_required") or [], [])
        ev = self._renames(root)["renames"][0]
        # No unknown-cost units: the key is absent, as before.
        self.assertEqual(ev["carried"], [{
            "account": "margin", "sheltered": False, "qty": 10.0,
            "qty_after": 10.0, "book_cost": 100.0}])

    @rule(R.CA_ACB_RENAME)
    def test_control_a_rename_no_account_holds_still_books_nothing(self):
        root, r = self._run("canada", _NOT_HELD)
        self.assertIn("RENAME 2025-01-10 QZOLD.TO QZNEW.TO books nothing",
                      " ".join((r.stdout + r.stderr).split()))
        doc = self._renames(root)
        self.assertEqual(doc["renames"], [])
        self.assertEqual([u["line"] for u in doc["unused"]],
                         ["RENAME 2025-01-10 QZOLD.TO QZNEW.TO"])


class TestUsa(_Runs):

    @rule(R.US_BASIS_RENAME)
    def test_rename_moves_the_unknown_cost_opening(self):
        self._moved("usa", _REPRO)


class TestCryptoAccount(unittest.TestCase):
    """The crypto path has no merge2: its map stage (taxjson-ticker-map)
    holds the lines the same way."""

    @rule(R.CA_CRYPTO_RENAME)
    def test_a_coin_rename_moves_the_unknown_cost_opening(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                settings_for("canada", year=2025, source_currencies=[])
                + '[accounts.kx]\ntype = "taxable"\ncrypto = true\n')
            acct = root / "inputs" / "kx"
            acct.mkdir(parents=True)
            (acct / "trades.tt").write_text(
                "OPENING 2024-12-20 QZOC 10 cost=unknown\n"
                "RENAME 2025-01-10 QZOC QZNC\n"
                "BUYSELL 2025-02-10 10:00:00 QZNC -10 CAD 12 120 0\n")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            said = " ".join((r.stdout + r.stderr).split())
            self.assertNotIn("books nothing", said)
            self.assertNotIn("cannot be short", said)
            doc = json.loads((root / "work" / "kx_gains.json").read_text())
            self.assertEqual(doc["transactions"], [])
            self.assertEqual([(t["symbol"], t["proceeds"])
                              for t in doc["manual_reporting_required"]],
                             [("QZNC", 120.0)])
            self.assertEqual(_held(doc), {})


class TestTheMergeHoldsTheLines(unittest.TestCase):
    """lib/missing_history hold_declared_openings / lib/renames
    apply_dated_renames: the line is a row while the renames are booked,
    and is taken out again."""

    @rule(R.CA_ACB_RENAME)
    def test_held_row_books_the_rename_and_is_released(self):
        import io
        from taxjson.model.transaction import TaxTransaction as T
        from taxjson.lib.missing_history import (hold_declared_openings,
                                                 release_declared_openings)
        from taxjson.lib.rename_decl import DatedRename
        from taxjson.lib.renames import apply_dated_renames
        sale = T(action="BUYSELL", date="2025-02-10", time="10:00:00",
                 symbol="QZNEW.TO", quantity=-10, price=12, net_amount=120,
                 currency="CAD", account="margin")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", _REPRO)
            rows, held = hold_declared_openings([sale], root)
            self.assertEqual(
                [(t.action, t.date, t.symbol, t.quantity, t.currency)
                 for t in rows],
                [("OPENING_BALANCE", "2024-12-20", "QZOLD.TO", 10.0, "CAD"),
                 ("BUYSELL", "2025-02-10", "QZNEW.TO", -10, "CAD")])
            self.assertEqual([t.where for t in held.values()],
                             ["inputs/margin/trades.tt:1"])
            dr = DatedRename(date="2025-01-10", old="QZOLD.TO",
                             new="QZNEW.TO", where="trades.tt:2",
                             line="RENAME 2025-01-10 QZOLD.TO QZNEW.TO")
            for books, n in ((rows, 1), ([sale], 0)):
                with self.subTest(held=bool(n)):
                    out = release_declared_openings(
                        apply_dated_renames(list(books), [dr],
                                            stream=io.StringIO()), held)
                    self.assertEqual(
                        [(t.date, t.symbol, t.symbol_new) for t in out
                         if t.action == "SPLIT"],
                        [("2025-01-10", "QZOLD.TO", "QZNEW.TO")] * n)
                    self.assertFalse([t for t in out
                                      if t.action == "OPENING_BALANCE"])
            # Another account's books: its lines are not this stage's.
            other = T(action="BUYSELL", date="2025-02-10", symbol="QZNEW.TO",
                      quantity=1, price=1, net_amount=-1, currency="CAD",
                      account="other")
            rows, held = hold_declared_openings([other], root)
            self.assertEqual((rows, held), ([other], {}))


if __name__ == "__main__":
    unittest.main()
