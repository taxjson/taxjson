"""Backlog fix round 7 of 2026-10-29, group `writers`: the writers that
draft input for the user (`find-missing-history --write-missing-history`
/ `--write-purchases`, `tjs opening`).

TJ-0814  `tjs opening` compared the new snapshot's symbols with the
TJ-0825  account's earlier OPENING lines as spelled, before the project's
         ticker.map (GLOBAL / TOBASE): an earlier OPENING of SYN.A.TO
         (GLOBAL SYN.A.TO SYN.TO) and a new SYN.TO snapshot were written
         side by side, and the run added both. Now compared as the books
         spell them, and refused as the same spelling is.
TJ-0816  `find-missing-history margin.old --write-missing-history` took
         the configured account for an obsolete FILE argument.
TJ-0832  The missing-history walks applied a SPLIT row only to its own
         account, while the engines apply it to every account's shares:
         a purchase drafted for another account was in post-split units.
TJ-0819  A rename moved a broker-marked short (RBC SHORT.) onto the new
         ticker without its marking: a partial cover read as missing
         history and --write-missing-history wrote a long OPENING.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN), amounts under 1,000.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_ENV = {"PYTHONPATH": str(SRC), "TAXJSON_OFFLINE": "1",
        "TAXJSON_WIDTH": "0"}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _tjs(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *map(str, args)]),
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=dict(os.environ, **_ENV), timeout=300)


def _project(root: Path, *, year=2025, country="canada",
             accounts=("margin",)) -> Path:
    cur = "CAD" if country == "canada" else "USD"
    tz = "America/Toronto" if country == "canada" else "America/New_York"
    text = (f'[settings]\nlocal_timezone = "{tz}"\nyear = {year}\n'
            f'country = "{country}"\nbase_currency = "{cur}"\n'
            'source_currencies = []\n')
    for a in accounts:
        text += f'[accounts.{json.dumps(a)}]\ntype = "taxable"\n'
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(text)
    (root / "ticker.map").write_text("")
    return root


# ---- TJ-0814 / TJ-0825: an opening of a mapped spelling ------------------
class TestTJ0825OpeningAliasDuplicate(unittest.TestCase):
    """TJ-0825 / TJ-0814: `tjs opening` refuses a snapshot of a
    security the account already opened under another spelling the
    ticker.map joins with it."""

    _HOLD = ('[meta]\nas_of = "2024-12-31"\n[[holding]]\nsymbol = "SYN.TO"'
             '\nquantity = 10\ncurrency = "CAD"\ntotal_cost = 200\n')

    def _case(self, td, prior, rule_line):
        root = _project(Path(td))
        (root / "ticker.map").write_text(rule_line)
        (root / "inputs/margin/trades.tt").write_text(
            f"OPENING 2024-12-31 {prior} 10 CAD 100\n"
            "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 30 300 0\n")
        (root / "hold.toml").write_text(self._HOLD)
        return root, _tjs(root, "opening", "margin", "hold.toml")

    def _refused(self, root, r, prior):
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        err = _flat(r.stderr)
        self.assertIn("already has OPENING lines for", err)
        self.assertIn(prior, err)
        self.assertEqual(sorted(p.name for p in
                                (root / "inputs/margin").iterdir()),
                         ["trades.tt"])

    def test_global_alias_is_refused_as_the_same_spelling_is(self):
        with tempfile.TemporaryDirectory() as td:
            root, r = self._case(td, "SYN.A.TO", "GLOBAL SYN.A.TO SYN.TO\n")
            self._refused(root, r, "SYN.A.TO")
            self.assertIn("SYN.TO", _flat(r.stderr))
        with tempfile.TemporaryDirectory() as td:
            # The control: the earlier line spells SYN.TO.
            root, r = self._case(td, "SYN.TO", "GLOBAL SYN.A.TO SYN.TO\n")
            self._refused(root, r, "SYN.TO")

    def test_tobase_listing_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, r = self._case(td, "SYN.U.TO", "TOBASE SYN.U.TO SYN.TO\n")
            self._refused(root, r, "SYN.U.TO")

    def test_unrelated_symbol_still_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root, r = self._case(td, "OTHR.TO", "GLOBAL SYN.A.TO SYN.TO\n")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(
                (root / "inputs/margin/opening_2024-12-31.tt").is_file())



# ---- TJ-0816: a dotted account name is not a FILE ------------------------
class TestTJ0816DottedAccountWrite(unittest.TestCase):
    """TJ-0816: `find-missing-history margin.old --write-missing-history`
    took the configured account `margin.old` for an obsolete FILE
    argument (its `.old` read as a suffix) and exited 2."""

    def test_dotted_account_writes_its_opening(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), accounts=("margin.old",))
            (root / "inputs/margin.old/trades.tt").write_text(
                "BUYSELL 2025-01-06 09:30:00 SYN.TO -10 CAD 10 100 0\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = _tjs(root, "find-missing-history", "margin.old",
                     "--write-missing-history")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("takes no FILE", r.stderr)
            text = (root / "inputs/margin.old/missing_history.tt"
                    ).read_text()
            self.assertIn("SYN.TO 10 cost=unknown", _flat(text))

    def test_a_file_name_is_still_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td))
            r = _tjs(root, "find-missing-history", "--write-missing-history",
                     "missing_history.json")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("takes no FILE", _flat(r.stderr))

# ---- TJ-0832: a split another account reports ----------------------------
class TestTJ0832DraftGlobalSplit(unittest.TestCase):
    """TJ-0832: `--write-purchases` drafted a pre-split lot in post-split
    units when the split row sat in another account (the engines apply
    a split to every account's shares)."""

    def _book(self, split_account, *, own_copy=False):
        from test_basis_drafts import _book, _lot, _parsed, _sell
        from taxjson.lib.core import TaxTransaction
        rows = _book(_parsed(
            _sell("SYN", "2025-03-18, 10:00:00", -10, 15, 100)
            + _lot("SYN", "2024-01-02", 10, 10, 100), account="b"))
        split = TaxTransaction(action="SPLIT", date="2025-01-02",
                               symbol="SYN.US", quantity=2.0,
                               account=split_account)
        rows = [split] + rows
        if own_copy:
            rows.append(TaxTransaction(action="SPLIT", date="2025-01-03",
                                       symbol="SYN.US", quantity=2.0,
                                       account="b"))
        return rows

    def _draft(self, rows, country="usa"):
        from taxjson.lib.missing_history import draft_purchases
        drafts, _gaps = draft_purchases(rows, country=country,
                                        accounts={"b"})
        return [(d.account, d.quantity, d.cost) for d in drafts]

    def test_split_in_another_account_is_undone(self):
        for country in ("usa", "canada"):
            with self.subTest(country=country):
                self.assertEqual(self._draft(self._book("a"), country),
                                 self._draft(self._book("b"), country))
                (acct, qty, _cost), = self._draft(self._book("a"), country)
                self.assertEqual((acct, qty), ("b", 5.0))

    def test_two_copies_of_one_split_apply_once(self):
        # Both accounts report the event (a day apart): applied once.
        (_a, qty, _c), = self._draft(self._book("a", own_copy=True))
        self.assertEqual(qty, 5.0)

    def test_split_shares_sold_in_another_account_are_not_short(self):
        # Account b bought 10 before the split account a reports and
        # sells the 20 they became: no missing history (the engines
        # split b's shares too); without the split it is 10 short.
        from taxjson.lib.core import TaxTransaction as T
        from taxjson.lib.missing_history import (assess_tax_year_relevance,
                                                 detect_missing_history)

        def rows(split=True):
            out = [T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                     quantity=10, price=10, net_amount=-100,
                     currency="USD", account="b"),
                   T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                     quantity=1, price=10, net_amount=-10, currency="USD",
                     account="a"),
                   T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
                     quantity=-20, price=10, net_amount=200,
                     currency="USD", account="b")]
            if split:
                out.append(T(action="SPLIT", date="2025-01-02",
                             symbol="SYN.US", quantity=2.0, account="a"))
            return out
        self.assertEqual(detect_missing_history(rows()), [])
        c, = detect_missing_history(rows(split=False))
        self.assertEqual((c.account, c.peak_short), ("b", -10.0))
        self.assertEqual(assess_tax_year_relevance(rows(), [], 2025), [])

    def test_opening_sized_as_with_the_split_in_the_account(self):
        # The suggested opening (synthesize_openings) of a pair short
        # across a split another account reports: in pre-split units.
        from taxjson.lib.core import TaxTransaction as T
        from taxjson.lib.missing_history import synthesize_openings

        def rows(where):
            return [T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                      quantity=5, price=10, net_amount=-50,
                      currency="USD", account="b"),
                    T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
                      quantity=2.0, account=where),
                    T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                      quantity=1, price=10, net_amount=-10,
                      currency="USD", account="a"),
                    T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
                      quantity=-20, price=10, net_amount=200,
                      currency="USD", account="b")]
        got = {}
        for where in ("a", "b"):
            new, _log = synthesize_openings(rows(where),
                                            pairs={("SYN.US", "b")})
            got[where] = [(t.date, t.quantity) for t in new
                          if t.action == "OPENING_BALANCE"]
            # No split copy leaks into the books handed back.
            self.assertEqual(sum(t.action == "SPLIT" for t in new), 1)
        self.assertEqual(got["a"], got["b"])
        self.assertEqual(got["a"], [("2024-06-03", 5.0)])

    def test_draft_from_the_cli_adds_up(self):
        # The probe's round trip, Canada: the accepted line leaves no
        # shares after the sale.
        from test_basis_drafts import _lot, _sell
        from test_fix_ibparse import HEAD, TRADES_H
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), accounts=("a", "b"))
            (root / "ticker.map").write_text("GLOBAL SYN.TO SYN.US\n")
            (root / "inputs/a/trades.tt").write_text(
                "SPLIT 2025-01-02 00:00:00 SYN.US SYN.US 2\n")
            (root / "inputs/b/ib.csv").write_text(
                (HEAD + TRADES_H
                 + _sell("SYN", "2025-03-18, 10:00:00", -10, 15, 100)
                 + _lot("SYN", "2024-01-02", 10, 10, 100)
                 ).replace("USD", "CAD"))
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = _tjs(root, "find-missing-history", "b", "--write-purchases")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = [ln for ln in (root / "inputs/b/purchases_draft.tt.txt")
                     .read_text().splitlines() if ln.startswith("BUYSELL")]
            self.assertEqual(len(lines), 1, lines)
            self.assertEqual(float(lines[0].split()[4]), 5.0, lines)

# ---- TJ-0819: a broker-marked short through a rename ---------------------
class TestTJ0819RenamedRealShort(unittest.TestCase):
    """TJ-0819: RBC marks an OLDS.TO short sale (SHORT.), the ticker
    becomes NEWS.TO (a .tt RENAME), and a partial cover follows: the
    rename moved the short without its broker-short marking, so NEWS.TO
    read as missing history and --write-missing-history wrote a long
    OPENING cost=unknown for it."""

    _CSV = ("Date,Activity,Symbol,Description,Quantity,Price,"
            "Settlement Date,Currency,Value,Amount\n"
            "01/06/2025,Sell,OLDS.TO,OLDS SYNTHETIC INC SHORT. "
            "UNSOLICITED,10,15,01/07/2025,CAD,150,150\n"
            "03/03/2025,Buy,{sym},SYNTHETIC INC COVER SHORT. "
            "UNSOLICITED,5,14,03/04/2025,CAD,-70,-70\n")

    def _project(self, td, renamed):
        root = _project(Path(td))
        (root / "inputs/margin/rbc.csv").write_text(
            self._CSV.format(sym="NEWS.TO" if renamed else "OLDS.TO"))
        if renamed:
            (root / "inputs/margin/trades.tt").write_text(
                "RENAME 2025-02-03 OLDS.TO NEWS.TO\n")
        r = _tjs(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root

    def test_library_walk_keeps_the_marking(self):
        from taxjson.lib.core import TaxTransaction as T
        from taxjson.lib.missing_history import detect_missing_history
        rows = [T(action="BUYSELL", date="2025-01-06", symbol="OLDS.TO",
                  quantity=-10, price=15, net_amount=150, currency="CAD",
                  account="margin",
                  description="OLDS SYNTHETIC INC SHORT. UNSOLICITED"),
                T(action="SPLIT", date="2025-02-03", symbol="OLDS.TO",
                  symbol_new="NEWS.TO", quantity=1.0, account="margin"),
                T(action="BUYSELL", date="2025-03-03", symbol="NEWS.TO",
                  quantity=5, price=14, net_amount=-70, currency="CAD",
                  account="margin",
                  description="SYNTHETIC INC COVER SHORT. UNSOLICITED")]
        self.assertEqual(detect_missing_history(rows), [])
        got = {(c.symbol, c.broker_marked_short) for c in
               detect_missing_history(rows, include_broker_shorts=True)}
        self.assertEqual(got, {("OLDS.TO", True), ("NEWS.TO", True)})

    def test_no_opening_is_written_for_the_renamed_short(self):
        for renamed in (True, False):
            with self.subTest(renamed=renamed), \
                    tempfile.TemporaryDirectory() as td:
                root = self._project(td, renamed)
                r = _tjs(root, "find-missing-history", "--details")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("AFFECTS 2025", r.stdout)
                self.assertIn("BROKER-MARKED SHORT SALES", r.stdout)
                r = _tjs(root, "find-missing-history",
                         "--write-missing-history")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertFalse(
                    (root / "inputs/margin/missing_history.tt").exists(),
                    r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
