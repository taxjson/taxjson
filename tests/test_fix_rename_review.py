"""Pre-release review of the dated renames (v0.24 dated events).

- H2: `taxjson format-map --write` never changes the books: it simulates
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
"""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

from taxjson.lib import dated_events as DE
from taxjson.lib import renames as RN
from taxjson.lib.renames import DatedRename
from tax_rules import rule
from tax_rules.dual import cli, projects_both

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


# ------------------------------------------------------------ H2

class TestFormatMapKeepsTheBooks(unittest.TestCase):
    """A ticker.map dated RENAME with a .tt twin in the project."""

    def _files(self, x, cur, tt_line, map_line):
        return {
            "ticker.map": map_line,
            "inputs/margin/a.tt": (
                _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                + tt_line
                # a late buy in the old ticker, then the whole sale
                + _bt("2025-04-06", f"QZOLD.{x}", 5, cur, 10.0)
                + _bt("2025-06-03", f"QZNEW.{x}", -15, cur, 11.0))}

    def _same_books(self, country, tt_line, map_line, total):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._files(
                x, cur, tt_line.format(x=x), map_line.format(x=x)))[country]
            _run(self, root)
            before = _books(root)
            self.assertAlmostEqual(before[0]["totals"]["total"], total,
                                   delta=0.011)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertIn("declare one change differently", _out(w))
            self.assertNotIn(f"RENAME QZOLD.{x}",
                             (root / "ticker.map").read_text())
            _run(self, root)
            self.assertEqual(_books(root), before)

    @rule("CA-ACB-RENAME")
    def test_canada_late_fold_kept_by_the_migration(self):
        # Input A: the map line holds late=fold, its .tt twin none: the
        # late buy is the renamed shares before and after (gain 15).
        self._same_books("canada",
                         "RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    @rule("US-BASIS-RENAME")
    def test_usa_late_fold_kept_by_the_migration(self):
        self._same_books("usa",
                         "RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    @rule("CA-ACB-RENAME")
    def test_canada_twin_on_another_date_is_one_event(self):
        # Input B: map 04-01 vs .tt 04-05 — one event on the earliest
        # date, whichever file holds which line.
        self._same_books("canada",
                         "RENAME 2025-04-05 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    @rule("US-BASIS-RENAME")
    def test_usa_twin_on_another_date_is_one_event(self):
        self._same_books("usa",
                         "RENAME 2025-04-05 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    def test_a_move_that_would_change_the_books_is_refused(self):
        # The map line says late=separate, the account's own .tt line
        # late=fold (its own choice): moving the map line into that
        # account would make it declare both — refused, nothing written.
        x, cur = _HOME["canada"]
        tmap = f"RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=separate\n"
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._files(
                x, cur, f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold\n",
                tmap))["canada"]
            _run(self, root)
            a_tt = (root / "inputs/margin/a.tt").read_text()
            w = cli(root, "format-map", "--write")
            self.assertEqual(w.returncode, 2, _out(w))
            out = _out(w)
            self.assertIn("would change the books — nothing was written",
                          out)
            self.assertIn("inputs/margin/a.tt:2", out)
            self.assertEqual((root / "ticker.map").read_text(), tmap)
            self.assertEqual((root / "inputs/margin/a.tt").read_text(),
                             a_tt)
            self.assertFalse((root / "inputs/margin/renames.tt").exists())
            self.assertFalse((root / "ticker.map.bak").exists())

    def test_resolution_ignores_the_reading_order(self):
        # The same declarations in any order give the same events.
        a = DatedRename("QZA.TO", "QZB.TO", "2025-04-05", "", "x/a.tt:1",
                        source="tt", account="margin", kind="securities")
        b = DatedRename("QZA.TO", "QZB.TO", "2025-04-01", "fold",
                        "x/renames.tt:4", source="tt", account="margin",
                        kind="securities")
        e1 = DE.resolve_renames([a, b], [])[0]
        e2 = DE.resolve_renames([b, a], [])[0]
        self.assertEqual([(e.date, e.late_for("margin"), e.late_for("tfsa"))
                          for e in e1],
                         [(e.date, e.late_for("margin"), e.late_for("tfsa"))
                          for e in e2])
        self.assertEqual([(e.date, e.late_for("margin")) for e in e1],
                         [("2025-04-01", "fold")])


# ------------------------------------------------------------ H3

class TestRenameChain(unittest.TestCase):

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

    @rule("CA-ACB-RENAME")
    def test_canada_chain_in_any_order(self):
        x, cur = _HOME["canada"]
        for order in ("in order", "reversed"):
            with self.subTest(order=order):
                self._chain("canada", self._files(x, cur, order))

    @rule("US-BASIS-RENAME")
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

    @rule("CA-ACB-RENAME")
    def test_canada_chain_migrated_across_accounts(self):
        self._accounts_case("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_chain_migrated_across_accounts(self):
        self._accounts_case("usa")


# ------------------------------------------------------------ H5 / M8

_KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
           "margin,misc,ledgers\n")


def _kraken(pair):
    return (_KR_HDR
            + f"TXA1,OA1,{pair},2025-01-15 10:00:00.1234,buy,limit,50000,"
              f"50000,0,1.0,,,\n"
            + f"TXA2,OA2,{pair},2025-06-02 14:30:00.5678,sell,limit,60000,"
              f"60000,0,1.0,,,\n")


class TestRenameKindPartition(unittest.TestCase):

    def _check(self, country):
        x, cur = _HOME[country]
        accounts = ('[accounts.margin]\ntype = "taxable"\n\n'
                    '[accounts.kx]\ntype = "taxable"\ncrypto = true\n')
        files = {
            # A securities account declares a change of a symbol a coin
            # also uses: it must not move the coin.
            "inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZS.{x}", 10, cur, 10.0)
                + "RENAME 2025-04-01 BTC QZBTC\n"
                + _bt("2025-06-03", f"QZS.{x}", -10, cur, 11.0)),
            "inputs/kx/kr_trades.csv": _kraken(f"BTC/{cur}")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=accounts, files=files)[country]
            _run(self, root)
            base = json.loads((root / "work" / "kx_base.json").read_text())
            self.assertFalse([t for t in base["transactions"]
                              if t["action"] == "SPLIT"])
            self.assertFalse([t for t in base["transactions"]
                              if t["symbol"] == "QZBTC"])
            self.assertAlmostEqual(_sum(root)["totals"]["total"],
                                   10000.0 + 10.0, delta=0.011)

    @rule("CA-CRYPTO-RENAME")
    def test_canada_security_rename_never_moves_a_coin(self):
        self._check("canada")

    @rule("US-CRYPTO-RENAME")
    def test_usa_security_rename_never_moves_a_coin(self):
        self._check("usa")

    def _apply(self, kind_decl, kind_books):
        from taxjson.lib.core import TaxTransaction
        rows = [TaxTransaction(action="BUYSELL", date="2024-01-10",
                               time="10:00:00", date_settle="2024-01-10",
                               symbol="QZC", quantity=1.0, price=10.0,
                               net_amount=-10.0, currency="CAD",
                               account="acct")]
        dr = DatedRename("QZC", "QZD", "2025-04-01", "", "x.tt:1",
                         source="tt", account="decl", kind=kind_decl)
        import io
        out = RN.apply_dated_renames(rows, [dr], stream=io.StringIO(),
                                     kind=kind_books)
        return [t.action for t in out]

    @rule("CA-CRYPTO-RENAME")
    def test_canada_a_coin_rename_books_in_crypto_accounts_only(self):
        self.assertEqual(self._apply("crypto", "securities"), ["BUYSELL"])
        self.assertEqual(self._apply("crypto", "crypto"),
                         ["BUYSELL", "SPLIT"])
        self.assertEqual(self._apply("securities", "crypto"), ["BUYSELL"])
        # A legacy map line (no kind) books everywhere.
        self.assertEqual(self._apply("", "crypto"), ["BUYSELL", "SPLIT"])

    @rule("US-CRYPTO-RENAME")
    def test_usa_a_coin_rename_books_in_crypto_accounts_only(self):
        self.assertEqual(self._apply("crypto", "securities"), ["BUYSELL"])
        self.assertEqual(self._apply("securities", "securities"),
                         ["BUYSELL", "SPLIT"])

    def test_home_account_is_never_a_crypto_account_for_a_security(self):
        with tempfile.TemporaryDirectory() as td:
            accounts = {"cx": {"type": "taxable", "crypto": True},
                        "margin": {"type": "taxable"}}
            self.assertEqual(DE.home_account(Path(td), accounts, "QZA.TO",
                                             "QZB.TO", "2025-04-01")[0],
                             "margin")
            # Its books name the coin: the crypto account.
            (Path(td) / "work").mkdir()
            (Path(td) / "work" / "cx_base.json").write_text(json.dumps(
                {"transactions": [{"action": "BUYSELL", "symbol": "QZA",
                                   "date": "2024-01-01"}]}))
            self.assertEqual(DE.home_accounts(Path(td), accounts, "QZA",
                                              "QZB", "2025-04-01"),
                             [("cx", "its books hold QZA or QZB")])


# ------------------------------------------------------------ M3

def _decl(old, new, date, where, late="", acct="margin",
          kind="securities", source="tt"):
    return DatedRename(old, new, date, late, where, f"RENAME {date} {old} "
                       f"{new}", source=source, account=acct, kind=kind)


class TestContradictions(unittest.TestCase):

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

    @rule("CA-ACB-RENAME")
    def test_canada_late_is_per_account(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
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


# ------------------------------------------------------------ lows

class TestFormatMapLows(unittest.TestCase):

    def _project(self, td, accounts=_TWO, extra=None):
        x, cur = _HOME["canada"]
        files = {"ticker.map": (f"# renamed by its issuer\n"
                                f"RENAME QZOLD.{x} QZNEW.{x} 2025-04-01\n"),
                 "inputs/margin/m.tt": (
                     _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                     + _bt("2025-06-03", f"QZNEW.{x}", -10, cur, 11.0))}
        files.update(extra or {})
        return projects_both(td, accounts=accounts, files=files)["canada"]

    def test_check_needs_no_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, accounts="")
            r = cli(root, "format-map", "--check")
            self.assertEqual(r.returncode, 1, _out(r))
            self.assertIn("dated events to migrate", _out(r))

    def test_inputs_folder_linked_outside_is_refused(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as away:
            root = self._project(td)
            _run(self, root)
            (root / "inputs" / "margin").rename(Path(away) / "margin")
            os.symlink(Path(away) / "margin", root / "inputs" / "margin")
            tmap = (root / "ticker.map").read_text()
            w = cli(root, "format-map", "--write")
            self.assertEqual(w.returncode, 2, _out(w))
            # Refused before anything runs: every command stops on a
            # written folder linked outside the project (security
            # review LOW c).
            self.assertIn("symlinks to outside the project", _out(w))
            self.assertIn("inputs/margin/ ->", _out(w))
            self.assertFalse((Path(away) / "margin" / "renames.tt").exists())
            self.assertEqual((root / "ticker.map").read_text(), tmap)

    def test_restored_backup_does_not_duplicate_the_tt_lines(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            _run(self, root)
            tmap = (root / "ticker.map").read_text()
            w = cli(root, "format-map", "--write")
            self.assertEqual(w.returncode, 0, _out(w))
            tt = (root / "inputs/margin/renames.tt").read_text()
            # The user restores the old map (or a write stopped half-way).
            (root / "ticker.map").write_text(tmap)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertEqual((root / "inputs/margin/renames.tt").read_text(),
                             tt)
            self.assertEqual(tt.count("# renamed by its issuer"), 1)

    def test_journal_gap_suggests_a_tt_journal_line(self):
        x, cur = _HOME["canada"]
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, extra={
                "ticker.map": f"JOURNAL QZG.{x} QZGB.{x}\n",
                "inputs/margin/m.tt": (
                    _bt("2025-03-03", f"QZG.{x}", 100, cur, 10.0)
                    + _bt("2025-03-06", f"QZGB.{x}", -100, cur, 8.0))})
            r = _run(self, root)
            self.assertIn("A JOURNAL line no longer moves units in the "
                          "holdings view", _out(r))
            d = cli(root, "format-map")
            self.assertEqual(d.returncode, 0, _out(d))
            self.assertIn(f"JOURNAL <date> QZG.{x} QZGB.{x} 100", _out(d))


class TestStateAndHints(unittest.TestCase):

    def test_state_cleared_by_a_failed_run(self):
        x, cur = _HOME["canada"]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={"inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZA.{x}", 10, cur, 10.0)
                + f"RENAME 2025-03-01 QZA.{x} QZB.{x}\n")})["canada"]
            _run(self, root)
            st = root / "work" / DE.STATE
            self.assertTrue(DE.read_state(st)["renames"])
            (root / "inputs/margin/x.tt").write_text(
                f"RENAME 2025-03-01 QZA.{x} QZOTHER.{x}\n")
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(st.exists())

    def test_read_state_guards(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s"
            for doc in ({"format": DE.FORMAT, "renames": 5},
                        {"format": DE.FORMAT, "journals": "x",
                         "renames": [1, {"old": "A"}]}, [1, 2], "x"):
                p.write_text(json.dumps(doc))
                st = DE.read_state(p)
                self.assertEqual(set(st), {"journals", "renames"})
                self.assertTrue(all(isinstance(r, dict) for v in st.values()
                                    for r in v))

    def test_an_old_row_on_the_event_date_is_late(self):
        ev = RN.rename_events([{"action": "SPLIT", "symbol": "QZA",
                                "symbol_new": "QZB", "date": "2025-04-01",
                                "time": "00:00:00", "_acct": "m"}])
        rows = [{"action": "BUYSELL", "symbol": "QZA", "date": "2025-04-01",
                 "time": "10:00:00", "_acct": "m"},
                {"action": "BUYSELL", "symbol": "QZA", "date": "2025-04-01",
                 "time": "11:00:00", "_acct": "other"},
                {"action": "BUYSELL", "symbol": "QZA", "date": "2025-03-31",
                 "time": "15:00:00", "_acct": "m"}]
        late = RN.late_rows(rows, ev)
        self.assertEqual([r["_acct"] for r, _e in late], ["m", "other"])
        # A broker's rename row later that day: an earlier trade is not
        # late in its account in the US engine's order (trade date and
        # clock); the Canada engine takes the SPLIT ahead of every
        # execution of its day, so it is late there (third pre-release
        # review, 1). Without a country the order is refused.
        ev = RN.rename_events([{"action": "SPLIT", "symbol": "QZA",
                                "symbol_new": "QZB", "date": "2025-04-01",
                                "time": "16:00:00", "_acct": "m"}])
        self.assertEqual(RN.late_rows(rows[:1], ev, "usa"), [])
        self.assertEqual(len(RN.late_rows(rows[:1], ev, "canada")), 1)
        with self.assertRaises(RN.RenameNeedsCountry):
            RN.late_rows(rows[:1], ev)

    def test_hint_regexes_are_bounded(self):
        text = ("RBC symbol A looks renamed to B " + "x " * 5000) * 2
        t0 = time.perf_counter()
        for _ in range(5):
            self.assertIsNone(RN._LOOKS_RE.search(text))
            self.assertIsNone(RN._LOOKS_TT_RE.search(text))
        self.assertLess(time.perf_counter() - t0, 2.0)
        ok = ("warning: ATTENTION: x.csv: Questrade symbol QZA looks renamed "
              "to QZB — if they are one security, add to a .tt file of this "
              "account:  RENAME 2025-04-01 QZA.TO QZB.TO  — QZA stops")
        m = RN._LOOKS_TT_RE.search(ok)
        self.assertEqual(m.groups()[1:], ("2025-04-01", "QZA.TO", "QZB.TO"))

    def test_map_rules_beside_a_tt_rename_are_warned(self):
        from taxjson.bin.taxjson_ticker_map import _parse_map_text
        decl = DE.Declarations(tt_declared=[
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "inputs/m/a.tt:1")])
        for line, word in (("TOBASE QZB.TO QZB.US", "TOBASE"),
                           ("DELETE QZA.TO", "DELETE QZA.TO"),
                           ("DISTINCT QZA.TO QZB.TO", "DISTINCT")):
            with self.subTest(line=line):
                d = DE.Declarations(tt_declared=list(decl.tt_declared))
                tmap = _parse_map_text(line + "\n", "ticker.map")[0]
                self.assertEqual(DE.check_against_map(d, tmap), [])
                self.assertEqual(len(d.warnings), 1, d.warnings)
                self.assertIn(word, d.warnings[0])
                self.assertIn("inputs/m/a.tt:1", d.warnings[0])


if __name__ == "__main__":
    unittest.main()
