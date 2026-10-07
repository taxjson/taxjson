"""Second pre-release review of the dated renames.

- 5: `late=fold` re-books only the rows AFTER the account's own rename
  row (date and time), the rows `late_rows` calls late; an OLD row the
  account books before its broker's rename row is still OLD.
- 6: an undated ticker.map line (GLOBAL, TOBASE, legacy JOURNAL, RENAME
  without a date) never joins an option contract or a future to a share
  listing, nor two different contracts; a respelling of one contract
  (same expiry, right, strike and market) stays allowed.
- 8: the event-level `late=` applies to the accounts that held OLD before
  the date; another account's rows in the old ticker are listed as
  unresolved, never folded.
- 9: `taxjson format-map --write` compares only the accounts whose books
  a change can reach (an account without books, or of the other kind, is
  not "changed"), and homes a moved line in an account whose own lines
  agree with it.
- 11: a declared rename that books nothing is said (Warning per line) and
  listed by `taxjson renames`; a same-day chain books both links.

Every fixture is SYNTHETIC: invented QZ* tickers, fake account names.
"""
import json
import tempfile
import unittest

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


def _run(tc, root, *extra):
    r = cli(root, "run", "--no-input", *extra)
    tc.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _bt(day, sym, qty, cur, price, time="10:00:00"):
    return (f"BUYSELL {day} {time} {sym} {qty} {cur} {price:.2f} "
            f"{abs(qty) * price:.2f} 0.00\n")


# ------------------------------------------------------------ 5

class TestFoldAfterTheAccountsOwnRow(unittest.TestCase):
    """An OLD sale BEFORE the account's own broker rename row (the .tt
    SPLIT on 04-03), after the declared date (04-01) with late=fold."""

    def _check(self, country):
        x, cur = _HOME[country]
        files = {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + _bt("2025-04-02", f"QZA.{x}", -20, cur, 12.0)
            + f"SPLIT 2025-04-03 00:00:00 QZA.{x} QZB.{x} 1\n"
            + f"RENAME 2025-04-01 QZA.{x} QZB.{x} late=fold\n"
            + _bt("2025-06-02", f"QZB.{x}", -30, cur, 12.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            _run(self, root, "--strict")
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 100.0,
                                   delta=0.011)
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["late"], [])

    @rule("CA-ACB-RENAME")
    def test_canada(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa(self):
        self._check("usa")

    def _earlier_row(self, country):
        # The broker's row (03-28) BEFORE the declared date (04-01): an OLD
        # buy between the two is late (after the account's own row), and
        # late=fold books it as NEW.
        x, cur = _HOME[country]
        files = {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + f"SPLIT 2025-03-28 00:00:00 QZA.{x} QZB.{x} 1\n"
            + _bt("2025-03-31", f"QZA.{x}", 10, cur, 10.0)
            + f"RENAME 2025-04-01 QZA.{x} QZB.{x} late=fold\n"
            + _bt("2025-06-02", f"QZB.{x}", -60, cur, 12.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            _run(self, root, "--strict")
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 120.0,
                                   delta=0.011)
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["late"], [])

    @rule("CA-ACB-RENAME")
    def test_canada_broker_row_before_the_date(self):
        self._earlier_row("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_broker_row_before_the_date(self):
        self._earlier_row("usa")


# ------------------------------------------------------------ 8

_AB = ('[accounts.margin]\ntype = "taxable"\n\n'
       '[accounts.b]\ntype = "taxable"\n')


class TestEventLateOnlyForHolders(unittest.TestCase):
    """margin held QZOLD and declares late=fold; account b never held
    QZOLD before the date and trades a QZOLD after it (another company
    reusing the ticker): b's rows stay QZOLD, listed as unresolved."""

    def _files(self, x, cur, b_line=""):
        return {
            "inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                + f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold\n"
                + _bt("2025-06-03", f"QZNEW.{x}", -10, cur, 11.0)),
            "inputs/b/b.tt": (
                b_line
                + _bt("2025-05-06", f"QZOLD.{x}", 7, cur, 3.0)
                + _bt("2025-06-10", f"QZOLD.{x}", -7, cur, 4.0))}

    def _check(self, country):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=_AB,
                                 files=self._files(x, cur))[country]
            _run(self, root)
            diag = " ".join(" ".join(f.read_text().split())
                            for f in (root / "work").glob("b_*.diag"))
            self.assertIn(f"QZOLD.{x} row(s) of account b on or after "
                          f"2025-04-01 are kept as QZOLD.{x}", diag)
            base = json.loads((root / "work/b_base.json").read_text())
            self.assertEqual({t["symbol"] for t in base["transactions"]},
                             {f"QZOLD.{x}"})
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(sorted((y["account"], y["resolution"])
                                    for y in doc["late"]),
                             [("b", "unresolved")] * 2)
            self.assertNotEqual(cli(root, "run", "--no-input",
                                    "--strict").returncode, 0)
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 17.0,
                                   delta=0.011)
            # b's own line settles its rows.
            (root / "inputs/b/b.tt").write_text(self._files(
                x, cur, f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} "
                f"late=separate\n")["inputs/b/b.tt"])
            _run(self, root, "--strict")
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual({y["resolution"] for y in doc["late"]},
                             {"separate"})

    @rule("CA-ACB-RENAME")
    def test_canada(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa(self):
        self._check("usa")


# ------------------------------------------------------------ 11

class TestUnusedDeclarations(unittest.TestCase):

    def _chain(self, country):
        x, cur = _HOME[country]
        files = {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + f"RENAME 2025-04-01 QZA.{x} QZB.{x}\n"
            + f"RENAME 2025-04-01 QZB.{x} QZC.{x}\n"
            + _bt("2025-06-02", f"QZC.{x}", -50, cur, 12.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            r = _run(self, root, "--strict")
            self.assertNotIn("books nothing", _out(r))
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 100.0,
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
    def test_canada_same_day_chain(self):
        self._chain("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_same_day_chain(self):
        self._chain("usa")

    def test_typo_is_warned_and_listed(self):
        x, cur = _HOME["canada"]
        files = {"inputs/margin/m.tt": (
            _bt("2024-03-01", f"QZA.{x}", 50, cur, 10.0)
            + f"RENAME 2025-04-01 QZAA.{x} QZB.{x}\n"
            + _bt("2025-06-02", f"QZA.{x}", -50, cur, 12.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)["canada"]
            r = _run(self, root)
            out = _out(r)
            self.assertIn("inputs/margin/m.tt:2: RENAME 2025-04-01 "
                          f"QZAA.{x} QZB.{x} books nothing", out)
            self.assertEqual(out.count("books nothing"), 1)
            r = cli(root, "renames", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual([(u["old"], u["where"]) for u in doc["unused"]],
                             [(f"QZAA.{x}", ["inputs/margin/m.tt:2"])])
            self.assertEqual(doc["pending"], 1)
            p = cli(root, "renames", "--pending")
            self.assertEqual(p.returncode, 1, _out(p))
            self.assertIn("DECLARED, NOT BOOKED (1)", p.stdout)
            self.assertIn(f"RENAME 2025-04-01 QZAA.{x} QZB.{x}", p.stdout)
            pj = json.loads(cli(root, "renames", "--json",
                                "--pending").stdout)
            self.assertEqual(len(pj["unused"]), 1)
            st = json.loads((root / "work/dated_events.state").read_text())
            self.assertEqual([x_["status"] for x_ in st["renames"]],
                             ["unused"])


# ------------------------------------------------------------ 6

def _problems(text):
    from taxjson.bin.taxjson_ticker_map import _parse_map_text
    return _parse_map_text(text, "ticker.map")[1]


class TestUndatedLinesNeverJoinContractsToShares(unittest.TestCase):

    def test_option_and_shares_refused_in_every_undated_keyword(self):
        for kw in ("GLOBAL", "TOBASE", "JOURNAL", "RENAME"):
            for a, b in (("QZK250620C00010000.US", "QZK.US"),
                         ("QZK.US", "QZK250620C00010000.US"),
                         ("F:QZESM5", "QZES.US")):
                with self.subTest(kw=kw, a=a, b=b):
                    probs = _problems(f"{kw} {a} {b}\n")
                    self.assertEqual(len(probs), 1, probs)
                    self.assertIn("ticker.map:1:", probs[0])
                    self.assertIn("with a share listing", probs[0])

    def test_two_contracts_refused(self):
        for b in ("QZK250620C00012000.US",      # another strike
                  "QZK250620P00010000.US",      # another right
                  "QZK250718C00010000.US",      # another expiry
                  "QZK250620C00010000.TO",      # another market
                  "F:QZESM5"):                  # a future
            with self.subTest(b=b):
                probs = _problems(f"GLOBAL QZK250620C00010000.US {b}\n")
                self.assertEqual(len(probs), 1, probs)
                self.assertIn("contract", probs[0])

    def test_a_respelling_of_one_contract_is_allowed(self):
        for a, b in (("QZOQ250620C00010000.US", "QZOP250620C00010000.US"),
                     ("QZB.B250620C00010000.TO", "QZB250620C00010000.TO"),
                     ("QZT1260918C00062000.TO", "QZT260918C00062000.TO"),
                     ("QZK250620C10000", "QZK250620C00010000.US"),
                     ("F:QZESM5", "/QZESM5")):
            with self.subTest(a=a, b=b):
                self.assertEqual(_problems(f"GLOBAL {a} {b}\n"), [])

    def test_format_map_files_it_as_unrecognized(self):
        from taxjson.lib.ticker_map_format import UNRECOGNIZED, format_map
        text = ("JOURNAL QZK250620C00010000.US QZK.US\n"
                "RENAME QZA.US QZB.US 2025-04-01\n")
        res = format_map(text, migrate=True)
        self.assertEqual(res.moved, [])
        self.assertEqual(res.journals, [])
        self.assertTrue(any("with a share listing" in p_
                            for p_ in res.problems), res.problems)
        tail = res.text.split(f"--- {UNRECOGNIZED} ---", 1)[1]
        self.assertIn("JOURNAL QZK250620C00010000.US QZK.US", tail)

    def _run_refused(self, country):
        x, cur = _HOME[country]
        files = {"ticker.map": f"GLOBAL QZK250620C00010000.{x} QZK.{x}\n",
                 "inputs/margin/m.tt": (
                     _bt("2025-03-01", f"QZK.{x}", 10, cur, 10.0)
                     + _bt("2025-06-02", f"QZK.{x}", -10, cur, 12.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files)[country]
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ticker.map:1: GLOBAL joins an option contract "
                          f"(QZK250620C00010000.{x}) with a share listing",
                          _out(r))

    @rule("CA-ACB-RENAME")
    def test_canada_run_refuses(self):
        self._run_refused("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_run_refuses(self):
        self._run_refused("usa")


# ------------------------------------------------------------ 9

class TestFormatMapComparesReachableBooks(unittest.TestCase):

    def test_an_account_without_books_of_the_other_kind(self):
        # kx: a crypto account with no inputs yet (no work/kx_base.json).
        x, cur = _HOME["canada"]
        accounts = ('[accounts.margin]\ntype = "taxable"\n\n'
                    '[accounts.kx]\ntype = "taxable"\ncrypto = true\n')
        files = {"ticker.map": f"RENAME QZOLD.{x} QZNEW.{x} 2025-04-01\n",
                 "inputs/margin/m.tt": (
                     _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                     + _bt("2025-06-03", f"QZNEW.{x}", -10, cur, 11.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=accounts,
                                 files=files)["canada"]
            _run(self, root)
            self.assertFalse((root / "work/kx_base.json").exists())
            before = _sum(root)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertIn(f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}",
                          (root / "inputs/margin/renames.tt").read_text())
            _run(self, root, "--strict")
            self.assertEqual(_sum(root), before)

    def test_a_home_whose_own_lines_agree(self):
        # The map line says late=fold; aa's own .tt line late=separate;
        # bb has no line: the line goes to bb (aa would declare both).
        x, cur = _HOME["canada"]
        accounts = ('[accounts.aa]\ntype = "taxable"\n\n'
                    '[accounts.bb]\ntype = "taxable"\n')
        files = {
            "ticker.map": (f"RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 "
                           f"late=fold\n"),
            "inputs/aa/a.tt": (
                _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                + f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=separate\n"
                + _bt("2025-05-06", f"QZOLD.{x}", 7, cur, 3.0)
                + _bt("2025-06-03", f"QZNEW.{x}", -10, cur, 11.0)),
            "inputs/bb/b.tt": (
                _bt("2024-02-10", f"QZOLD.{x}", 20, cur, 10.0)
                + _bt("2025-04-06", f"QZOLD.{x}", 5, cur, 10.0)
                + _bt("2025-06-03", f"QZNEW.{x}", -25, cur, 11.0))}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=accounts,
                                 files=files)["canada"]
            _run(self, root, "--strict")
            before = _sum(root)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertFalse((root / "inputs/aa/renames.tt").exists())
            self.assertIn(f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold",
                          (root / "inputs/bb/renames.tt").read_text())
            _run(self, root, "--strict")
            self.assertEqual(_sum(root), before)


if __name__ == "__main__":
    unittest.main()
