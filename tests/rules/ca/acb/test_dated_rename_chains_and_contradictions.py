"""Pre-release review of the dated renames (v0.24 dated events).

- H2: `tjs format-map --write` never changes the books: it simulates
  the rename events the run books after the move (the project's .tt lines
  plus the moved map lines) and refuses, writing nothing, unless they are
  the ones it books now; a moved line that repeats a .tt line written
  differently is reported. Every declaration of one change is one event
  whatever order the files are read in (earliest date; late= per account).
- H3: dated renames apply in date order, and an account holds OLD when an
  earlier rename moved a position into it (a chain A -> B -> C).
- H5 / M8: a .tt RENAME applies only to accounts of its declaring
  account's kind (securities or crypto).
- M3: contradictory declarations stop the run naming the lines (a swap or
  cycle, one change on two dates).
- M4: late= describes one account's rows: its own account's late rows,
  and those of accounts without a line of their own.
- Lows: --check, symlinked inputs folder, idempotent .tt append, the
  dated_events.state of a failed run, read_state guards, an OLD row on
  the event date, the bounded hint regexes, map rules beside a .tt
  RENAME, home_account and crypto accounts, the legacy JOURNAL wording.

Every fixture is SYNTHETIC: invented QZ* tickers, fake account names.

Formerly: test_fix_rename_review
"""
import json
import tempfile
import unittest

from taxjson.lib import dated_events as DE
from taxjson.lib.rename_decl import DatedRename
from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}
_TWO = ('[accounts.margin]\ntype = "taxable"\n\n'
        '[accounts.plan]\ntype = "sheltered"\n')


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _sum(root):
    r = cli(root, "sum", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _holdings(root, acct="margin"):
    p = root / "reports" / f"{acct}_holdings.toml"
    return "\n".join(ln for ln in p.read_text().splitlines()
                     if not ln.startswith("generated_at"))


def _books(root, accts=("margin",)):
    return (_sum(root),) + tuple(_holdings(root, a) for a in accts)


def _run(tc, root, *extra):
    r = cli(root, "run", "--no-input", *extra)
    tc.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _bt(day, sym, qty, cur, price):
    return (f"BUYSELL {day} 10:00:00 {sym} {qty} {cur} {price:.2f} "
            f"{abs(qty) * price:.2f} 0.00\n")


# ------------------------------------------------------------ H3

class TestRenameChain(unittest.TestCase):
    """Formerly: test_fix_rename_review.TestRenameChain"""

    def _chain(self, country, files, accounts='[accounts.margin]\n'
               'type = "taxable"\n', total=100.0):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=accounts,
                                 files=files)[country]
            _run(self, root, "--strict")
            self.assertAlmostEqual(_sum(root)["totals"]["total"], total,
                                   delta=0.011)
            return root

    def _files(self, x, cur, order):
        lines = [f"RENAME 2025-04-01 QZA.{x} QZB.{x}\n",
                 f"RENAME 2025-05-01 QZB.{x} QZC.{x}\n"]
        if order == "reversed":
            lines.reverse()
        return {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + "".join(lines)
            + _bt("2025-06-02", f"QZC.{x}", -50, cur, 12.0))}

    @rule(R.CA_ACB_RENAME)
    def test_canada_chain_in_any_order(self):
        x, cur = _HOME["canada"]
        for order in ("in order", "reversed"):
            with self.subTest(order=order):
                self._chain("canada", self._files(x, cur, order))

    @rule(R.US_BASIS_RENAME)
    def test_usa_chain_in_any_order(self):
        x, cur = _HOME["usa"]
        for order in ("in order", "reversed"):
            with self.subTest(order=order):
                self._chain("usa", self._files(x, cur, order))

    def _accounts_case(self, country):
        # Map lines in date order; the migration homes them in two
        # accounts read the other way round (aa before zz).
        x, cur = _HOME[country]
        files = {
            "ticker.map": (f"RENAME QZA.{x} QZB.{x} 2025-04-01\n"
                           f"RENAME QZB.{x} QZC.{x} 2025-05-01\n"),
            "inputs/aa/a.tt": (_bt("2025-04-15", f"QZB.{x}", 10, cur, 10.0)
                               + _bt("2025-06-01", f"QZC.{x}", -10, cur,
                                     11.0)),
            "inputs/zz/z.tt": (_bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
                               + _bt("2025-06-02", f"QZC.{x}", -50, cur,
                                     12.0))}
        accounts = ('[accounts.aa]\ntype = "taxable"\n\n'
                    '[accounts.zz]\ntype = "taxable"\n')
        root = None
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=accounts, files=files)[country]
            _run(self, root, "--strict")
            before = _books(root, ("aa", "zz"))
            self.assertAlmostEqual(before[0]["totals"]["total"], 110.0,
                                   delta=0.011)
            # zz's position went QZA -> QZB -> QZC and was sold: nothing
            # left (the second change used to miss zz, which held QZB
            # only through the first one's row).
            base = json.loads((root / "work/zz_base.json").read_text())
            self.assertEqual(sorted((t["symbol"], t["symbol_new"])
                                    for t in base["transactions"]
                                    if t["action"] == "SPLIT"),
                             [(f"QZA.{x}", f"QZB.{x}"),
                              (f"QZB.{x}", f"QZC.{x}")])
            self.assertNotIn("QZ", before[2])
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertIn(f"RENAME 2025-05-01 QZB.{x} QZC.{x}",
                          (root / "inputs/aa/renames.tt").read_text())
            self.assertIn(f"RENAME 2025-04-01 QZA.{x} QZB.{x}",
                          (root / "inputs/zz/renames.tt").read_text())
            _run(self, root, "--strict")
            self.assertEqual(_books(root, ("aa", "zz")), before)

    @rule(R.CA_ACB_RENAME)
    def test_canada_chain_migrated_across_accounts(self):
        self._accounts_case("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_chain_migrated_across_accounts(self):
        self._accounts_case("usa")


# ------------------------------------------------------------ M3

def _decl(old, new, date, where, late="", acct="margin",
          kind="securities", source="tt"):
    return DatedRename(old, new, date, late, where, f"RENAME {date} {old} "
                       f"{new}", source=source, account=acct, kind=kind)


class TestContradictions(unittest.TestCase):
    """Formerly: test_fix_rename_review.TestContradictions"""

    def test_swap_and_cycle(self):
        ev, _n, bad = DE.resolve_renames([
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "inputs/margin/a.tt:1"),
            _decl("QZB.TO", "QZA.TO", "2025-06-01", "inputs/plan/p.tt:4",
                  acct="plan")], [])
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("cycle", bad[0])
        self.assertIn("inputs/margin/a.tt:1", bad[0])
        self.assertIn("inputs/plan/p.tt:4", bad[0])
        _ev, _n, bad = DE.resolve_renames([
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "a.tt:1"),
            _decl("QZB.TO", "QZC.TO", "2025-04-01", "a.tt:2"),
            _decl("QZC.TO", "QZA.TO", "2025-05-01", "a.tt:3")], [])
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("QZA.TO -> QZB.TO -> QZC.TO -> QZA.TO", bad[0])
        # A chain is fine.
        _ev, _n, bad = DE.resolve_renames([
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "a.tt:1"),
            _decl("QZB.TO", "QZC.TO", "2025-04-01", "a.tt:2")], [])
        self.assertEqual(bad, [])

    def test_one_change_on_two_dates(self):
        _ev, _n, bad = DE.resolve_renames([
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "inputs/margin/a.tt:1"),
            _decl("QZA.TO", "QZB.TO", "2025-03-31", "inputs/plan/p.tt:1",
                  acct="plan")], [])
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("one change has one date", bad[0])
        self.assertIn("inputs/margin/a.tt:1", bad[0])
        self.assertIn("inputs/plan/p.tt:1", bad[0])
        # A map line 32 days from a .tt twin: the same.
        _ev, _n, bad = DE.resolve_renames(
            [_decl("QZA.TO", "QZB.TO", "2025-03-01", "inputs/a/a.tt:1")],
            [_decl("QZA.TO", "QZB.TO", "2025-04-02", "ticker.map:3",
                   acct="", kind="", source="map")])
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("ticker.map:3", bad[0])

    def test_the_run_stops_naming_both_lines(self):
        x, cur = _HOME["canada"]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=_TWO, files={
                "inputs/margin/m.tt": (
                    _bt("2024-01-10", f"QZA.{x}", 10, cur, 10.0)
                    + f"RENAME 2025-03-01 QZA.{x} QZB.{x}\n"),
                "inputs/plan/p.tt": (
                    f"RENAME 2025-06-01 QZB.{x} QZA.{x}\n")})["canada"]
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            out = _out(r)
            self.assertIn("form a cycle", out)
            self.assertIn("inputs/margin/m.tt:2", out)
            self.assertIn("inputs/plan/p.tt:1", out)


# ------------------------------------------------------------ M4

class TestLatePerAccount(unittest.TestCase):
    """Formerly: test_fix_rename_review.TestLatePerAccount"""

    def _files(self, x, cur, plan_line):
        return {
            "inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                + f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold\n"
                + _bt("2025-04-06", f"QZOLD.{x}", 5, cur, 10.0)
                + _bt("2025-06-03", f"QZNEW.{x}", -15, cur, 11.0)),
            "inputs/plan/p.tt": (
                _bt("2024-02-10", f"QZOLD.{x}", 20, cur, 10.0)
                + plan_line
                # another company now using the old ticker in the plan
                + _bt("2025-05-06", f"QZOLD.{x}", 7, cur, 3.0))}

    def _check(self, country):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            # The plan's own line keeps its late buy separate; the
            # margin's late=fold is not a contradiction of it.
            root = projects_both(td, accounts=_TWO, files=self._files(
                x, cur, f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} "
                f"late=separate\n"))[country]
            _run(self, root, "--strict")
            # (the margin's late buy is folded: booked as the new symbol,
            # it is no longer a row of the old one)
            doc = json.loads(cli(root, "renames", "--json").stdout)
            got = sorted((x_["account"], x_["resolution"])
                         for x_ in doc["late"])
            self.assertEqual(got, [("plan", "separate")])
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 15.0,
                                   delta=0.011)
            h = _holdings(root, "plan")
            self.assertIn(f'symbol = "QZOLD.{x}"', h)
            self.assertIn(f'symbol = "QZNEW.{x}"', h)
            # No line of its own: the plan's late rows take the margin's
            # choice (the line applies to every account without one).
            (root / "inputs/plan/p.tt").write_text(self._files(
                x, cur, "")["inputs/plan/p.tt"])
            _run(self, root, "--strict")
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["late"], [])
            h = _holdings(root, "plan")
            self.assertNotIn(f'symbol = "QZOLD.{x}"', h)
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 15.0,
                                   delta=0.011)

    @rule(R.CA_ACB_RENAME)
    def test_canada_late_is_per_account(self):
        self._check("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_late_is_per_account(self):
        self._check("usa")

    def test_one_account_choosing_both_is_refused(self):
        _ev, _n, bad = DE.resolve_renames([
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "a.tt:1", late="fold"),
            _decl("QZA.TO", "QZB.TO", "2025-03-02", "b.tt:1",
                  late="separate")], [])
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("both late=fold and late=separate", bad[0])

    def test_disagreeing_accounts_leave_the_others_unresolved(self):
        ev, notes, bad = DE.resolve_renames([
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "a.tt:1", late="fold"),
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "b.tt:1",
                  late="separate", acct="plan")], [])
        self.assertEqual(bad, [])
        (e,) = ev
        self.assertEqual((e.late_for("margin"), e.late_for("plan"),
                          e.late_for("tfsa")), ("fold", "separate", ""))
        self.assertTrue(any("unresolved" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
