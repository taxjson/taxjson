"""TJ-5008: a declared RENAME of an underlying carries its option
contracts too.

The stock's `RENAME <date> OLD NEW` line is the one to write for an
option whose underlying changed its ticker (CA-ACB-RENAME /
US-BASIS-RENAME: "an option follows its underlying's change"), and it
booked only the shares' row: a contract bought as OLD... and sold as
NEW... stayed two positions (a long left on OLD, a written one on NEW;
Canada taxed the "written" premium, the US booked nothing), silently
when the account held OLD shares too, and with a "books nothing" Warning
in an account holding only the contracts. Now the account holding a
contract on OLD carries the event, and each contract still open at it
moves to the same expiry, right, strike and market on NEW.

Every fixture is SYNTHETIC: invented QZ* tickers, fake account names.
"""
import io
import json
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}
_CALL = "271217C00010000"       # one contract, far from its expiry
_PUT = "271217P00008000"


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _bt(day, sym, qty, cur, price, time="10:00:00"):
    return (f"BUYSELL {day} {time} {sym} {qty} {cur} {price:.2f} "
            f"{abs(qty) * price * (100 if len(sym) > 12 else 1):.2f} "
            f"0.00\n")


def _books(root):
    """(total gain, {symbol: (quantity, cost)}) of the account's books."""
    f = root / "work" / "margin_gains_wash.json"
    if not f.exists():
        f = root / "work" / "margin_gains.json"
    d = json.loads(f.read_text())
    return (round(d["summary"]["total_gain"], 2),
            {i["symbol"]: (i["qty"], round(i.get("total_cost") or 0, 2))
             for i in d.get("inventory") or []})


class _Both(unittest.TestCase):
    def _project(self, country, lines):
        x, cur = _HOME[country]
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = projects_both(td.name, files={
            "inputs/margin/m.tt": lines(x, cur)})[country]
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 0, _out(r)[-3000:])
        self.assertNotIn("books nothing", _out(r))
        return root, x


class TestContractsOnly(_Both):
    """The account holds one bought call on QZA and no QZA share; the
    call is sold under QZB after the change: one contract, gain 100."""

    def _check(self, country):
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late=separate\n"
            + _bt("2025-03-03", f"QZB{_CALL}.{x}", -1, cur, 2.0)))
        self.assertEqual(_books(root), (100.0, {}))
        doc = json.loads(cli(root, "renames", "--json").stdout)
        self.assertEqual(doc["unused"], [])
        self.assertEqual(doc["late"], [])
        moved = {(e["old"], e["new"]): e for e in doc["renames"]}
        e = moved[(f"QZA{_CALL}.{x}", f"QZB{_CALL}.{x}")]
        # The contract's row names the stock's line and its late= choice.
        self.assertEqual(e["ticker_map_lines"], ["inputs/margin/m.tt:2"])
        self.assertEqual(e["late"], "separate")
        self.assertEqual(e["carried"][0]["qty"], 1.0)
        self.assertEqual(e["carried"][0]["book_cost"], 100.0)
        # The stock's row carries no units here: no "held 0" line
        # (review, P3b); the view says what the account carried.
        self.assertEqual(moved[(f"QZA.{x}", f"QZB.{x}")]["carried"], [])
        text = _out(cli(root, "renames"))
        self.assertNotIn("held 0", text)
        self.assertIn(f"margin: no QZA.{x} held; its option contracts on "
                      f"it follow the change", text)

    @rule(R.CA_ACB_RENAME)
    def test_canada(self):
        self._check("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa(self):
        self._check("usa")


class TestContractsBesideShares(_Both):
    """The silent case: QZA shares held too. The share moved and the
    contracts did not; a written put and a bought call both follow."""

    def _check(self, country):
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-03", f"QZA.{x}", 1, cur, 10.0)
            + _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + _bt("2025-01-07", f"QZA{_PUT}.{x}", -1, cur, 3.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x}\n"
            + _bt("2025-03-03", f"QZB{_CALL}.{x}", -1, cur, 2.0)
            + _bt("2025-03-04", f"QZB{_PUT}.{x}", 1, cur, 0.5)))
        # call: 200 - 100; put: 300 - 50.
        self.assertEqual(_books(root), (350.0, {f"QZB.{x}": (1.0, 10.0)}))

    @rule(R.CA_ACB_RENAME)
    def test_canada(self):
        self._check("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa(self):
        self._check("usa")


class TestLateContractRows(_Both):
    """A contract opened under the OLD root after the change is a late
    row, as a share is: `late=fold` books it as NEW (the broker still
    using the old ticker), `late=separate` keeps it (another company's
    contract) — in an account that held only contracts too."""

    def _lines(self, late):
        return lambda x, cur: (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late={late}\n"
            + _bt("2025-02-10", f"QZA{_PUT}.{x}", 1, cur, 1.0)
            + _bt("2025-03-03", f"QZB{_CALL}.{x}", -1, cur, 2.0))

    def _check(self, country, late, held):
        root, x = self._project(country, self._lines(late))
        self.assertEqual(_books(root),
                         (100.0, {f"{held}{_PUT}.{x}": (1.0, 100.0)}))
        doc = json.loads(cli(root, "renames", "--json").stdout)
        self.assertEqual(doc["unresolved"], 0)
        if late == "separate":
            self.assertEqual([(w["symbol"], w["resolution"])
                              for w in doc["late"]],
                             [(f"QZA{_PUT}.{x}", "separate")])

    @rule(R.CA_ACB_RENAME)
    def test_canada_fold(self):
        self._check("canada", "fold", "QZB")

    @rule(R.CA_ACB_RENAME)
    def test_canada_separate(self):
        self._check("canada", "separate", "QZA")

    @rule(R.US_BASIS_RENAME)
    def test_usa_fold(self):
        self._check("usa", "fold", "QZB")

    @rule(R.US_BASIS_RENAME)
    def test_usa_separate(self):
        self._check("usa", "separate", "QZA")

    def _undeclared(self, country):
        # No late= choice: the late contract row is listed, and stops
        # `run --strict`, as a late share row does.
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={"inputs/margin/m.tt": (
                _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
                + f"RENAME 2025-02-01 QZA.{x} QZB.{x}\n"
                + _bt("2025-02-10", f"QZA{_PUT}.{x}", 1, cur, 1.0))
            })[country]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual([(w["symbol"], w["renamed_to"],
                               w["resolution"]) for w in doc["late"]],
                             [(f"QZA{_PUT}.{x}", f"QZB.{x}",
                               "unresolved")])
            self.assertEqual(_books(root)[1], {
                f"QZB{_CALL}.{x}": (1.0, 100.0),
                f"QZA{_PUT}.{x}": (1.0, 100.0)})

    @rule(R.CA_ACB_RENAME)
    def test_canada_undeclared_late_contract_is_listed(self):
        self._undeclared("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_undeclared_late_contract_is_listed(self):
        self._undeclared("usa")


class TestLateCloseOfAMovedContract(_Both):
    """Owner rulings on the reviews: while the account still holds a
    contract the event moved, every row after the change under its OLD
    symbol is the same contract, booked as NEW whatever late= says: a
    close, an addition, a close of more than is left. Left on OLD
    (late=separate, or no choice) it made a bought contract on NEW
    beside a written one on OLD: Canada taxed the whole proceeds, the US
    nothing, with no Warning."""

    def _sold_under_old(self, country, late):
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-03", f"QZA.{x}", 1, cur, 10.0)
            + _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x}{late}\n"
            + _bt("2025-03-03", f"QZA{_CALL}.{x}", -1, cur, 2.0)))
        self.assertEqual(_books(root), (100.0, {f"QZB.{x}": (1.0, 10.0)}))
        doc = json.loads(cli(root, "renames", "--json").stdout)
        self.assertEqual((doc["late"], doc["unresolved"]), ([], 0))
        diag = (root / "work" / "margin_base.json.diag").read_text()
        self.assertIn(
            f"note: inputs/margin/m.tt:3: RENAME QZA.{x} -> QZB.{x}: 1 "
            f"row(s) of account margin under QZA.{x} after the change "
            f"belong to option contract(s) it moved to QZB.{x} and still "
            f"held: the same contracts, booked as QZB.{x} whatever late= "
            f"says (2025-03-03 BUYSELL QZA{_CALL}.{x} -1 -> QZB{_CALL}.{x} "
            f"[m.tt]).", diag)
        # `tjs renames` lists the row under the contract's event, and
        # --json carries it (review 2, call a).
        (e,) = [e for e in doc["renames"] if e["old"] == f"QZA{_CALL}.{x}"]
        self.assertEqual(e["booked_as_new"], [{
            "account": "margin", "date": "2025-03-03", "action": "BUYSELL",
            "symbol": f"QZA{_CALL}.{x}", "qty": -1.0,
            "booked_as": f"QZB{_CALL}.{x}", "file": "m.tt",
            "declared": "inputs/margin/m.tt:3",
            "why": "a row of a contract the change moved and the account "
                   "still held: the same contract, whatever late= says"}])
        text = _out(cli(root, "renames"))
        self.assertIn(
            f"1 row(s) under QZA{_CALL}.{x} after the change booked as "
            f"QZB{_CALL}.{x} whatever late= says (the contract was still "
            f"held; declared at inputs/margin/m.tt:3): 2025-03-03 margin "
            f"BUYSELL QZA{_CALL}.{x} -1 (m.tt)", text)

    @rule(R.CA_ACB_RENAME)
    def test_canada_separate(self):
        self._sold_under_old("canada", " late=separate")

    @rule(R.CA_ACB_RENAME)
    def test_canada_no_choice(self):
        self._sold_under_old("canada", "")

    @rule(R.CA_ACB_RENAME)
    def test_canada_fold(self):
        self._sold_under_old("canada", " late=fold")

    @rule(R.US_BASIS_RENAME)
    def test_usa_separate(self):
        self._sold_under_old("usa", " late=separate")

    @rule(R.US_BASIS_RENAME)
    def test_usa_no_choice(self):
        self._sold_under_old("usa", "")

    @rule(R.US_BASIS_RENAME)
    def test_usa_fold(self):
        self._sold_under_old("usa", " late=fold")

    def _expired_under_old(self, country):
        # A written put, its expiry row (a BUYSELL at 0) under OLD.
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-07", f"QZA250620P00008000.{x}", -1, cur, 3.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late=separate\n"
            + f"BUYSELL 2025-06-20 16:00:00 QZA250620P00008000.{x} 1 "
              f"{cur} 0 0 0\n"))
        self.assertEqual(_books(root), (300.0, {}))

    @rule(R.CA_ACB_RENAME)
    def test_canada_expiry_row(self):
        self._expired_under_old("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_expiry_row(self):
        self._expired_under_old("usa")

    def _over_closed(self, country, late):
        # Review 2 (P1): 1 bought contract moved; 2 sold under OLD. The
        # sale is booked as NEW: it closes the 1 held (gain 200 - 100)
        # and opens 1 written on NEW (Canada: its 200 premium is income
        # at the write; the US books it at the close). An ATTENTION line
        # names the row and the excess, on the console.
        x, cur = _HOME[country]
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = projects_both(td.name, files={"inputs/margin/m.tt": (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x}{late}\n"
            + _bt("2025-03-03", f"QZA{_CALL}.{x}", -2, cur, 2.0))})[country]
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, _out(r)[-3000:])
        self.assertEqual(_books(root), (
            {"canada": 300.0, "usa": 100.0}[country],
            {f"QZB{_CALL}.{x}": (-1.0, -200.0)}))
        self.assertIn(
            f"warning: ATTENTION: rename: inputs/margin/m.tt:2: RENAME "
            f"QZA.{x} -> QZB.{x}: margin: 2025-03-03 BUYSELL QZA{_CALL}.{x} "
            f"-2 closes 1 more than the 1 contract(s) the account held of "
            f"it The row is booked as QZB{_CALL}.{x} (the contract the "
            f"change moved): it closes the 1 held and opens 1 written on "
            f"QZB{_CALL}.{x}.", _out(r))
        doc = json.loads(cli(root, "renames", "--json").stdout)
        self.assertEqual((doc["late"], doc["unresolved"]), ([], 0))

    @rule(R.CA_ACB_RENAME)
    def test_canada_a_close_of_more_than_is_held(self):
        for late in (" late=separate", "", " late=fold"):
            self._over_closed("canada", late)

    @rule(R.US_BASIS_RENAME)
    def test_usa_a_close_of_more_than_is_held(self):
        for late in (" late=separate", "", " late=fold"):
            self._over_closed("usa", late)

    def _added_then_closed(self, country, sales):
        # Review 2 (P1): 1 moved, 1 more bought under OLD after the
        # change (late=separate), then both sold under OLD in one row or
        # two: one contract throughout, 2 x (200 - 100), nothing held.
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late=separate\n"
            + _bt("2025-02-10", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + "".join(_bt(f"2025-03-0{3 + i}", f"QZA{_CALL}.{x}", q, cur,
                          2.0) for i, q in enumerate(sales))))
        self.assertEqual(_books(root), (200.0, {}))

    @rule(R.CA_ACB_RENAME)
    def test_canada_an_addition_then_the_close(self):
        self._added_then_closed("canada", (-2,))
        self._added_then_closed("canada", (-1, -1))

    @rule(R.US_BASIS_RENAME)
    def test_usa_an_addition_then_the_close(self):
        self._added_then_closed("usa", (-2,))
        self._added_then_closed("usa", (-1, -1))

    def _closed_then_another_company(self, country):
        # Once the moved contract is fully closed (under NEW here), the
        # same symbol under OLD is a late row again: late=separate keeps
        # another company's contract apart (100 + 300).
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late=separate\n"
            + _bt("2025-03-03", f"QZB{_CALL}.{x}", -1, cur, 2.0)
            + _bt("2025-06-02", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + _bt("2025-06-03", f"QZA{_CALL}.{x}", -1, cur, 4.0)))
        self.assertEqual(_books(root), (400.0, {}))
        doc = json.loads(cli(root, "renames", "--json").stdout)
        self.assertEqual([(w["symbol"], w["resolution"])
                          for w in doc["late"]],
                         [(f"QZA{_CALL}.{x}", "separate")] * 2)

    @rule(R.CA_ACB_RENAME)
    def test_canada_late_decides_once_the_moved_contract_is_closed(self):
        self._closed_then_another_company("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_late_decides_once_the_moved_contract_is_closed(self):
        self._closed_then_another_company("usa")

    def _two_alike(self, country):
        # Review 3 (P2): two sales of 1 under OLD at one time and price
        # are two rows of the view, not one.
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 2, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late=separate\n"
            + _bt("2025-03-03", f"QZA{_CALL}.{x}", -1, cur, 2.0) * 2))
        self.assertEqual(_books(root), (200.0, {}))
        doc = json.loads(cli(root, "renames", "--json").stdout)
        (e,) = [e for e in doc["renames"] if e["old"] == f"QZA{_CALL}.{x}"]
        self.assertEqual([(b["date"], b["qty"]) for b in e["booked_as_new"]],
                         [("2025-03-03", -1.0)] * 2)
        self.assertIn(f"2 row(s) under QZA{_CALL}.{x} after the change "
                      f"booked as QZB{_CALL}.{x}", _out(cli(root, "renames")))
        # Review 3 (P3): without the last run's diagnostics the view
        # says where the rows are read from; --json keeps its shape.
        for f in (root / "work").glob("*.diag"):
            f.unlink()
        self.assertIn("Info: the rows under an old symbol booked as the "
                      "new contract are read from the last run's "
                      "diagnostics (work/*.diag), and none is there: run "
                      "`tjs run` to list them.", _out(cli(root, "renames")))
        bare = json.loads(cli(root, "renames", "--json").stdout)
        (b,) = [e for e in bare["renames"] if e["old"] == f"QZA{_CALL}.{x}"]
        self.assertEqual((b["booked_as_new"], sorted(b)), ([], sorted(e)))

    @rule(R.CA_ACB_RENAME)
    def test_canada_two_rows_alike_are_two_rows(self):
        self._two_alike("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_two_rows_alike_are_two_rows(self):
        self._two_alike("usa")

    def _reopened_under_new(self, country):
        # Review 3 (P3, owner ruling): only the units the event moved
        # count. The moved contract is closed as NEW and 1 bought again
        # as NEW; a buy and a sale under OLD after that are late rows
        # (late=separate: another company's, kept on OLD, 300 on their
        # own), though the account holds the NEW contract.
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late=separate\n"
            + _bt("2025-03-03", f"QZB{_CALL}.{x}", -1, cur, 2.0)
            + _bt("2025-04-01", f"QZB{_CALL}.{x}", 1, cur, 1.0)
            + _bt("2025-06-02", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + _bt("2025-06-03", f"QZA{_CALL}.{x}", -1, cur, 4.0)))
        self.assertEqual(_books(root),
                         (400.0, {f"QZB{_CALL}.{x}": (1.0, 100.0)}))
        doc = json.loads(cli(root, "renames", "--json").stdout)
        self.assertEqual([(w["date"], w["symbol"], w["resolution"])
                          for w in doc["late"]], [
            ("2025-06-02", f"QZA{_CALL}.{x}", "separate"),
            ("2025-06-03", f"QZA{_CALL}.{x}", "separate")])
        self.assertEqual([e["booked_as_new"] for e in doc["renames"]],
                         [[], []])

    @rule(R.CA_ACB_RENAME)
    def test_canada_only_moved_units_count(self):
        self._reopened_under_new("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_only_moved_units_count(self):
        self._reopened_under_new("usa")

    def _reopened(self, country):
        # Review, 3: the contract's rename row is final for the position
        # it moved only. The same contract symbol bought again after the
        # first change (another company's, late=separate) is moved by
        # the second change.
        root, x = self._project(country, lambda x, cur: (
            _bt("2025-01-06", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-02-01 QZA.{x} QZB.{x} late=separate\n"
            + _bt("2025-03-03", f"QZB{_CALL}.{x}", -1, cur, 2.0)
            + _bt("2025-06-02", f"QZA{_CALL}.{x}", 1, cur, 1.0)
            + f"RENAME 2025-09-01 QZA.{x} QZD.{x} late=separate\n"
            + _bt("2025-10-03", f"QZD{_CALL}.{x}", -1, cur, 2.0)))
        self.assertEqual(_books(root), (200.0, {}))

    @rule(R.CA_ACB_RENAME)
    def test_canada_contract_opened_again_follows_the_next_change(self):
        self._reopened("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_contract_opened_again_follows_the_next_change(self):
        self._reopened("usa")


class TestAClosedContractIsNoHolding(unittest.TestCase):
    """Review, 2: an account whose only QZA row is a contract closed a
    year before the change does not hold QZA: it carries no row of the
    event and does not inherit the other account's late=fold, so its
    later QZA purchase stays QZA, listed as undeclared."""

    @rule(R.CA_ACB_RENAME)
    def test_canada(self):
        old = "QZA241220C00010000.TO"
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=(
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.second]\ntype = "taxable"\n'), files={
                "inputs/margin/m.tt": (
                    _bt("2025-01-03", "QZA.TO", 1, "CAD", 10.0)
                    + "RENAME 2025-02-01 QZA.TO QZB.TO late=fold\n"),
                "inputs/second/s.tt": (
                    _bt("2024-01-08", old, 1, "CAD", 1.0)
                    + _bt("2024-02-06", old, -1, "CAD", 2.0)
                    + _bt("2025-06-02", "QZA.TO", 10, "CAD", 5.0))})["canada"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertIn("1 trade(s) in QZA.TO after its rename to QZB.TO "
                          "on 2025-02-01", _out(r))
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual([(e["old"], e["accounts"])
                              for e in doc["renames"]],
                             [("QZA.TO", ["margin"])])
            self.assertEqual([(w["account"], w["symbol"], w["resolution"])
                              for w in doc["late"]],
                             [("second", "QZA.TO", "unresolved")])


def _t(action, date, symbol, qty=0.0, acct="a", **kw):
    from taxjson.model.transaction import TaxTransaction
    return TaxTransaction(action=action, date=date, date_settle=date,
                          time=kw.pop("time", "10:00:00"), symbol=symbol,
                          quantity=qty, price=1.0, net_amount=-qty * 100.0,
                          currency="CAD", account=acct, **kw)


class TestWhichContractsMove(unittest.TestCase):
    """lib/renames.apply_dated_renames: the contract rows it books."""

    def _apply(self, txs, late="", mapping=None, date="2025-02-01"):
        from taxjson.lib.rename_decl import DatedRename
        from taxjson.lib.renames import apply_dated_renames
        dr = DatedRename("QZA.TO", "QZB.TO", date, late,
                         "inputs/a/m.tt:1", "RENAME ...", "tt")
        err = io.StringIO()
        # (the pipeline's order: by date)
        txs = sorted(txs, key=lambda t: (t.date, t.time))
        out = apply_dated_renames(txs, [dr], stream=err, country="canada",
                                  mapping=mapping)
        return ([(t.account, t.date, t.time, t.symbol, t.symbol_new,
                  t.quantity) for t in out if t.action == "SPLIT"],
                out, err.getvalue())

    @rule(R.CA_ACB_RENAME)
    def test_each_open_contract_once_beside_the_accounts_row(self):
        splits, out, note = self._apply([
            _t("BUYSELL", "2025-01-06", "QZA.TO", 10),
            _t("BUYSELL", "2025-01-06", "QZA271217C00010000.TO", 2),
            _t("BUYSELL", "2025-01-08", "QZA271217C00010000.TO", -1),
            _t("BUYSELL", "2025-01-09", "QZA271217P00008000.TO", -3),
            _t("BUYSELL", "2025-01-06", "QZA271217C00010000.TO", 1,
               acct="b"),
            _t("BUYSELL", "2025-01-06", "QZC271217C00010000.TO", 1),
            _t("BUYSELL", "2025-03-03", "QZB271217C00010000.TO", -1)])
        self.assertEqual(splits, [
            ("a", "2025-02-01", "00:00:00", "QZA.TO", "QZB.TO", 1.0),
            ("a", "2025-02-01", "00:00:00", "QZA271217C00010000.TO",
             "QZB271217C00010000.TO", 1.0),
            ("a", "2025-02-01", "00:00:00", "QZA271217P00008000.TO",
             "QZB271217P00008000.TO", 1.0),
            ("b", "2025-02-01", "00:00:00", "QZA.TO", "QZB.TO", 1.0),
            ("b", "2025-02-01", "00:00:00", "QZA271217C00010000.TO",
             "QZB271217C00010000.TO", 1.0)])
        # Before the first row dated on or after the change.
        self.assertEqual([t.date for t in out],
                         sorted(t.date for t in out))
        self.assertIn("3 open option contract(s) on QZA.TO follow it to "
                      "QZB.TO (a: QZA271217C00010000.TO, "
                      "QZA271217P00008000.TO; b: QZA271217C00010000.TO)",
                      note)
        row = next(t for t in out if t.symbol_new
                   == "QZB271217P00008000.TO")
        self.assertEqual((row.source, row.event_source, row.currency),
                         ("m.tt", "tt", "CAD"))

    @rule(R.CA_ACB_RENAME)
    def test_a_closed_or_expired_contract_stays(self):
        splits, _, note = self._apply([
            # closed out before the change
            _t("BUYSELL", "2025-01-06", "QZA271217C00010000.TO", 1),
            _t("BUYSELL", "2025-01-20", "QZA271217C00010000.TO", -1),
            # expired before it, its expiry row missing
            _t("BUYSELL", "2025-01-06", "QZA250117C00010000.TO", 1),
            # bought on the day itself: a late row, the late= choice's
            _t("BUYSELL", "2025-02-01", "QZA271217P00008000.TO", 1)])
        # No contract open at the date: the account does not hold QZA,
        # and carries no row of the event (review, 2).
        self.assertEqual(splits, [])
        self.assertNotIn("option contract", note)

    @rule(R.CA_ACB_RENAME)
    def test_a_contract_with_a_rename_row_of_its_own_is_not_moved_twice(
            self):
        # The .tt line users wrote before: a SPLIT of the contract.
        splits, _, _ = self._apply([
            _t("BUYSELL", "2025-01-06", "QZA271217C00010000.TO", 1),
            _t("SPLIT", "2025-02-03", "QZA271217C00010000.TO", 1.0,
               symbol_new="QZB271217C00010000.TO", time="09:30:00"),
            # a broker's adjustment before the change, into another
            # contract on QZA: the contract it made follows
            _t("BUYSELL", "2025-01-06", "QZA271217P00008000.TO", 1),
            _t("SPLIT", "2025-01-15", "QZA271217P00008000.TO", 1.0,
               symbol_new="QZA271217P00004000.TO", time="09:30:00")])
        self.assertEqual(splits, [
            ("a", "2025-01-15", "09:30:00", "QZA271217P00008000.TO",
             "QZA271217P00004000.TO", 1.0),
            ("a", "2025-02-01", "00:00:00", "QZA.TO", "QZB.TO", 1.0),
            ("a", "2025-02-01", "00:00:00", "QZA271217P00004000.TO",
             "QZB271217P00004000.TO", 1.0),
            ("a", "2025-02-03", "09:30:00", "QZA271217C00010000.TO",
             "QZB271217C00010000.TO", 1.0)])

    @rule(R.CA_ACB_RENAME)
    def test_beside_the_brokers_own_row_and_on_a_raw_spelling(self):
        # The broker booked the shares' change two days later and not
        # the contract's; the contract is spelled on the raw symbol an
        # undated rename maps onto OLD.
        splits, _, _ = self._apply([
            _t("BUYSELL", "2025-01-06", "QZA.TO", 10),
            _t("BUYSELL", "2025-01-06", "QZRAW271217C00010000.TO", 1),
            _t("SPLIT", "2025-02-03", "QZA.TO", 1.0, symbol_new="QZB.TO",
               time="20:25:00")],
            mapping={"QZRAW.TO": "QZA.TO"})
        self.assertEqual(splits, [
            ("a", "2025-02-03", "20:25:00", "QZA.TO", "QZB.TO", 1.0),
            ("a", "2025-02-03", "20:25:00", "QZRAW271217C00010000.TO",
             "QZB271217C00010000.TO", 1.0)])

    @rule(R.CA_ACB_RENAME)
    def test_late_rows_of_a_moved_contract(self):
        # 3 bought contracts moved. After the change, under the OLD
        # symbol, while any is held: a sale of 2 (NEW), a purchase of 1
        # (NEW: 2 held), a sale of 3 (NEW: 1 more than held, 1 written,
        # said), a purchase of 1 under NEW (flat). Then the moved
        # position is closed: a purchase under OLD follows late=
        # (separate: kept as QZA).
        c, n = "QZA271217C00010000.TO", "QZB271217C00010000.TO"
        splits, out, note = self._apply([
            _t("BUYSELL", "2025-01-06", c, 3),
            _t("BUYSELL", "2025-03-03", c, -2),
            _t("BUYSELL", "2025-03-04", c, 1),
            _t("BUYSELL", "2025-03-05", c, -3),
            _t("BUYSELL", "2025-03-06", n, 1),
            _t("BUYSELL", "2025-03-07", c, 1)], late="separate")
        self.assertEqual([(t.date, t.symbol[:3], t.quantity) for t in out
                          if t.action == "BUYSELL"], [
            ("2025-01-06", "QZA", 3), ("2025-03-03", "QZB", -2),
            ("2025-03-04", "QZB", 1), ("2025-03-05", "QZB", -3),
            ("2025-03-06", "QZB", 1), ("2025-03-07", "QZA", 1)])
        self.assertIn(
            "3 row(s) of account a under QZA.TO after the change belong "
            "to option contract(s) it moved to QZB.TO and still held: the "
            "same contracts, booked as QZB.TO whatever late= says "
            f"(2025-03-03 BUYSELL {c} -2 -> {n} []; 2025-03-04 BUYSELL {c} "
            f"1 -> {n} []; 2025-03-05 BUYSELL {c} -3 -> {n} []).", note)
        self.assertEqual(note.count("ATTENTION"), 1, note)
        self.assertIn(
            f"warning: ATTENTION: rename: inputs/a/m.tt:1: RENAME QZA.TO "
            f"-> QZB.TO: a: 2025-03-05 BUYSELL {c} -3 closes 1 more than "
            f"the 2 contract(s) the account held of it\n  The row is "
            f"booked as {n}", note)

    @rule(R.CA_ACB_RENAME)
    def test_an_assignment_under_the_old_root_takes_its_share_leg(self):
        p = "QZA271217P00008000.TO"
        _, out, note = self._apply([
            _t("BUYSELL", "2025-01-07", p, -1),
            _t("ASSIGN", "2025-03-03", p, 1, time="16:00:00"),
            _t("ASSIGN", "2025-03-03", "QZA.TO", 100, time="16:00:01"),
            # a later purchase under the old ticker: late=
            _t("BUYSELL", "2025-04-01", "QZA.TO", 5)], late="separate")
        self.assertEqual([(t.action, t.symbol) for t in out[3:]], [
            ("ASSIGN", "QZB271217P00008000.TO"), ("ASSIGN", "QZB.TO"),
            ("BUYSELL", "QZA.TO")])
        self.assertIn(
            f"(2025-03-03 ASSIGN {p} 1 -> QZB271217P00008000.TO []; "
            f"2025-03-03 ASSIGN QZA.TO 100 -> QZB.TO [])", note)

    @rule(R.CA_ACB_RENAME)
    def test_a_contract_that_cannot_be_respelled_is_said(self):
        # (no market suffix: the underlying rule cannot re-spell it)
        from taxjson.lib.rename_decl import DatedRename
        from taxjson.lib.renames import apply_dated_renames
        dr = DatedRename("QZA", "QZB.TO", "2025-02-01", "",
                         "inputs/a/m.tt:1", "RENAME ...", "tt")
        err = io.StringIO()
        out = apply_dated_renames(
            [_t("BUYSELL", "2025-01-06", "QZA271217C00010000", 1)], [dr],
            stream=err, country="canada")
        self.assertEqual([t.symbol for t in out if t.action == "SPLIT"],
                         ["QZA"])
        self.assertIn("warning: inputs/a/m.tt:1: RENAME QZA -> QZB.TO: 1 "
                      "open option contract(s) on QZA stay under it",
                      err.getvalue())
        self.assertIn("(a: QZA271217C00010000)", err.getvalue())


class TestUnderlyingChange(unittest.TestCase):
    @rule(R.CA_ACB_RENAME)
    def test_one_contract_under_two_underlyings(self):
        from taxjson.lib.rename_rows import underlying_change as uc
        self.assertEqual(uc("QZA271217C00010000.TO",
                            "QZB271217C00010000.TO"), ("QZA.TO", "QZB.TO"))
        for old, new in (("QZA.TO", "QZB.TO"),
                         ("QZA271217C00010000.TO", "QZB.TO"),
                         # adjusted on its own underlying
                         ("QZA271217C00010000.TO", "QZA271217C00005000.TO"),
                         # another strike, right, expiry
                         ("QZA271217C00010000.TO", "QZB271217C00005000.TO"),
                         ("QZA271217C00010000.TO", "QZB271217P00010000.TO"),
                         ("QZA271217C00010000.TO", "QZB280121C00010000.TO")):
            self.assertIsNone(uc(old, new), (old, new))


if __name__ == "__main__":
    unittest.main()
