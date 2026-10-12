"""Third pre-release review of the dated renames.

- 1: `late=fold` re-books the OLD rows that come after the account's own
  rename row in the ENGINE's order for the project's country. The Canada
  engine takes a SPLIT ahead of every execution on its settle date (a
  morning sale on the day of an evening rename row is after it); the US
  engine orders by trade date and clock time.
- 3: a rename row into OLD on the date of a declared `RENAME OLD NEW`
  (a broker's or a .tt SPLIT A -> OLD) counts as holding OLD: the chain
  carries the position.
- 4: a declared rename of a symbol that ticker.map respells (`GLOBAL RAW
  OLD`) books on the raw rows.
- 7: a declared rename that books nothing stops `run --strict`.

Every fixture is SYNTHETIC: invented QZ* tickers, fake account names.
"""
import json
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import cli, projects_both

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _sum(root):
    r = cli(root, "sum", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _run(tc, root, *extra):
    r = cli(root, "run", "--no-input", *extra)
    tc.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _bt(day, sym, qty, cur, price, time="10:00:00"):
    return (f"BUYSELL {day} {time} {sym} {qty} {cur} {price:.2f} "
            f"{abs(qty) * price:.2f} 0.00\n")


# ------------------------------------------------------------ 1

class TestFoldFollowsTheEnginesOrder(unittest.TestCase):
    """A 10:00 OLD sale and a 21:00 OLD buy on the day of the account's
    own 20:25 rename row, late=fold declared. Canada: the engine takes
    the SPLIT first that day, so both OLD rows are late (folded); the
    US: the 10:00 sale precedes the split (an OLD sale), the 21:00 buy
    is late. Either way the books carry no short and total 120."""

    def _files(self, x, cur):
        return {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + _bt("2025-04-03", f"QZA.{x}", -20, cur, 12.0)
            + f"SPLIT 2025-04-03 20:25:00 QZA.{x} QZB.{x} 1\n"
            + _bt("2025-04-03", f"QZA.{x}", 10, cur, 10.0, "21:00:00")
            + f"RENAME 2025-04-01 QZA.{x} QZB.{x} late=fold\n"
            + _bt("2025-06-02", f"QZB.{x}", -40, cur, 12.0))}

    def _check(self, country, late_symbols):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._files(x, cur))[country]
            _run(self, root, "--strict")
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 120.0,
                                   delta=0.011)
            base = json.loads((root / "work/margin_base.json").read_text())
            day = [(t["time"], t["symbol"]) for t in base["transactions"]
                   if t["date"] == "2025-04-03" and t["action"] == "BUYSELL"]
            self.assertEqual(sorted(day), late_symbols(x))
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["late"], [])

    @rule("CA-ACB-RENAME")
    def test_canada(self):
        self._check("canada", lambda x: [("10:00:00", f"QZB.{x}"),
                                         ("21:00:00", f"QZB.{x}")])

    @rule("US-BASIS-RENAME")
    def test_usa(self):
        self._check("usa", lambda x: [("10:00:00", f"QZA.{x}"),
                                      ("21:00:00", f"QZB.{x}")])

    def _undeclared(self, country):
        # Without a declaration the same-day rows the engine takes after
        # the rename row are listed as late, and --strict stops.
        x, cur = _HOME[country]
        files = {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + _bt("2025-04-03", f"QZA.{x}", -20, cur, 12.0)
            + f"SPLIT 2025-04-03 20:25:00 QZA.{x} QZB.{x} 1\n"
            + _bt("2025-06-02", f"QZB.{x}", -30, cur, 12.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            doc = json.loads(cli(root, "renames", "--json").stdout)
            return root, [(t["date"], t["qty"]) for t in doc["late"]]

    @rule("CA-ACB-RENAME")
    def test_canada_undeclared_lists_the_morning_sale(self):
        root, late = self._undeclared("canada")
        self.assertEqual(late, [("2025-04-03", -20.0)])
        r = cli(root, "run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0, _out(r))

    @rule("US-BASIS-RENAME")
    def test_usa_undeclared_morning_sale_is_not_late(self):
        root, late = self._undeclared("usa")
        self.assertEqual(late, [])


class TestAfterRenameRowByCountry(unittest.TestCase):
    """The order helper itself: each country's engine ladder."""

    def _row(self, date, time, settle="", action="BUYSELL"):
        return {"action": action, "date": date, "time": time,
                "date_settle": settle or date, "quantity": -1.0,
                "symbol": "QZA.TO"}

    def test_orders(self):
        from taxjson.lib.renames import after_rename_row
        split = {"action": "SPLIT", "date": "2025-04-03",
                 "time": "20:25:00", "date_settle": "2025-04-03",
                 "quantity": 1.0, "symbol": "QZA.TO",
                 "symbol_new": "QZB.TO"}
        cases = [
            # (row, canada, usa)
            (self._row("2025-04-03", "10:00:00"), True, False),
            (self._row("2025-04-03", "21:00:00"), True, True),
            (self._row("2025-04-03", "20:25:00"), True, True),
            (self._row("2025-04-02", "10:00:00", "2025-04-03"), False,
             False),
            (self._row("2025-04-03", "21:00:00", "2025-04-04"), True, True),
            (self._row("2025-04-04", "09:00:00"), True, True),
            (self._row("2025-04-02", "23:00:00"), False, False),
        ]
        for row, ca, us in cases:
            with self.subTest(row=row):
                self.assertEqual(after_rename_row(row, split, "canada"), ca)
                self.assertEqual(after_rename_row(row, split, "usa"), us)

    def test_no_country_is_refused_only_when_it_matters(self):
        from taxjson.lib.renames import after_rename_row
        split = {"action": "SPLIT", "date": "2025-04-03",
                 "time": "20:25:00", "quantity": 1.0, "symbol": "QZA.TO",
                 "symbol_new": "QZB.TO"}
        self.assertTrue(after_rename_row(self._row("2025-04-04", "09:00:00"),
                                         split, ""))
        with self.assertRaises(ValueError):
            after_rename_row(self._row("2025-04-03", "10:00:00"), split, "")


# ------------------------------------------------------------ 3

class TestSameDayRenameRowIntoOld(unittest.TestCase):
    """A .tt SPLIT QZA -> QZB on 04-01 (the broker's row) and a declared
    `RENAME 2025-04-01 QZB QZC`: the account holds QZB on the date, so
    the chain carries the position to QZC."""

    def _check(self, country, time):
        x, cur = _HOME[country]
        files = {"inputs/margin/m.tt": (
            _bt("2024-01-10", f"QZA.{x}", 10, cur, 10.0)
            + f"SPLIT 2025-04-01 {time} QZA.{x} QZB.{x} 1\n"
            + f"RENAME 2025-04-01 QZB.{x} QZC.{x}\n"
            + _bt("2025-06-03", f"QZC.{x}", -10, cur, 11.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            r = _run(self, root, "--strict")
            self.assertNotIn("books nothing", _out(r))
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 10.0,
                                   delta=0.011)
            base = json.loads((root / "work/margin_base.json").read_text())
            self.assertEqual([(t["symbol"], t["symbol_new"])
                              for t in base["transactions"]
                              if t["action"] == "SPLIT"],
                             [(f"QZA.{x}", f"QZB.{x}"),
                              (f"QZB.{x}", f"QZC.{x}")])
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["unused"], [])

    @rule("CA-ACB-RENAME")
    def test_canada(self):
        self._check("canada", "00:00:00")

    @rule("US-BASIS-RENAME")
    def test_usa(self):
        self._check("usa", "00:00:00")

    @rule("CA-ACB-RENAME")
    def test_canada_evening_row(self):
        self._check("canada", "20:25:00")

    @rule("US-BASIS-RENAME")
    def test_usa_evening_row(self):
        self._check("usa", "20:25:00")


# ------------------------------------------------------------ 4

class TestRenameOfARespelledSymbol(unittest.TestCase):
    """ticker.map `GLOBAL QZAX.<x> QZA.<x>`, the rows in QZAX, and a
    declared `RENAME 2025-04-01 QZA QZB`: the rename books on the raw
    rows."""

    def _check(self, country):
        x, cur = _HOME[country]
        files = {
            "ticker.map": f"GLOBAL QZAX.{x} QZA.{x}\n",
            "inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZAX.{x}", 10, cur, 10.0)
                + f"RENAME 2025-04-01 QZA.{x} QZB.{x}\n"
                + _bt("2025-06-03", f"QZB.{x}", -10, cur, 11.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            r = _run(self, root, "--strict")
            self.assertNotIn("books nothing", _out(r))
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 10.0,
                                   delta=0.011)
            base = json.loads((root / "work/margin_base.json").read_text())
            self.assertEqual([(t["symbol"], t["symbol_new"])
                              for t in base["transactions"]
                              if t["action"] == "SPLIT"],
                             [(f"QZA.{x}", f"QZB.{x}")])
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["unused"], [])
            self.assertEqual(doc["pending"], 0)

    @rule("CA-ACB-RENAME")
    def test_canada(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa(self):
        self._check("usa")

    def _fold(self, country):
        # A late raw QZAX row is folded into QZB too.
        x, cur = _HOME[country]
        files = {
            "ticker.map": f"GLOBAL QZAX.{x} QZA.{x}\n",
            "inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZAX.{x}", 10, cur, 10.0)
                + f"RENAME 2025-04-01 QZA.{x} QZB.{x} late=fold\n"
                + _bt("2025-05-05", f"QZAX.{x}", 5, cur, 10.0)
                + _bt("2025-06-03", f"QZB.{x}", -15, cur, 11.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            _run(self, root, "--strict")
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 15.0,
                                   delta=0.011)

    def test_the_raw_spelling_names_the_line_to_write(self):
        x, cur = _HOME["canada"]
        files = {
            "ticker.map": f"GLOBAL QZAX.{x} QZA.{x}\n",
            "inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZAX.{x}", 10, cur, 10.0)
                + f"RENAME 2025-04-01 QZAX.{x} QZB.{x}\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)["canada"]
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(f"`RENAME 2025-04-01 QZA.{x} QZB.{x}`", _out(r))

    @rule("CA-ACB-RENAME")
    def test_canada_late_fold(self):
        self._fold("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_late_fold(self):
        self._fold("usa")


# ------------------------------------------------------------ 7

class TestUnusedDeclarationStopsStrict(unittest.TestCase):

    def _check(self, country):
        x, cur = _HOME[country]
        files = {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + f"RENAME 2025-04-01 QZAA.{x} QZB.{x}\n"
            + _bt("2025-06-02", f"QZA.{x}", -50, cur, 12.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            _run(self, root)
            r = cli(root, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0, _out(r))
            self.assertIn("books nothing", _out(r))

    @rule("CA-ACB-RENAME")
    def test_canada(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa(self):
        self._check("usa")


# ------------------------------------------------------------ 6

class TestFastRunAfterTheLastOtherIbStatementGoes(unittest.TestCase):
    """Two IB accounts share one contract id under two symbols; mb's
    QZNB row (05-08) dates the change for ma too. When mb's IB statement
    is replaced by a .tt file, `run --fast` re-parses ma: its change is
    dated from its own rows again (05-12)."""

    def _check(self, country):
        from test_fix_review2_ib import _ONE_ID, _U2
        from test_fix_ibparse import FII_H, HEAD, TRADES_H, _trade
        ma = (HEAD + TRADES_H
              + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
              + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                       code='C')
              + FII_H + _ONE_ID)
        mb = (HEAD + TRADES_H
              + _trade('QZOA', '2025-02-05, 10:00:00', 50, 10, -500,
                       acct=_U2)
              + _trade('QZNB', '2025-05-08, 10:00:00', -50, 12, 600,
                       code='C', acct=_U2)
              + FII_H + _ONE_ID)
        accounts = ('[accounts.ma]\ntype = "taxable"\n'
                    '[accounts.mb]\ntype = "taxable"\n')
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=accounts,
                                 files={"inputs/ma/ib.csv": ma,
                                        "inputs/mb/ib.csv": mb})[country]

            def split_dates():
                doc = json.loads((root / "work/ma_ib.json").read_text())
                rows = doc.get("transactions", doc) \
                    if isinstance(doc, dict) else doc
                return [t["date"] for t in rows
                        if t.get("action") == "SPLIT"]
            _run(self, root)
            self.assertEqual(split_dates(), ["2025-05-08"])
            (root / "inputs/mb/ib.csv").unlink()
            (root / "inputs/mb/m.tt").write_text(
                _bt("2025-01-06", f"QZZ.{_HOME[country][0]}", 1,
                    _HOME[country][1], 5.0))
            _run(self, root, "--fast")
            self.assertEqual(split_dates(), ["2025-05-12"])
            state = json.loads(
                (root / "work/ma_ib_project.state").read_text())
            self.assertEqual(state["accounts"], {})

    @rule("CA-ACB-RENAME")
    def test_canada(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa(self):
        self._check("usa")


if __name__ == "__main__":
    unittest.main()
