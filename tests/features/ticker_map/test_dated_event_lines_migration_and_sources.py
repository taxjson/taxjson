"""Dated events in the books (owner design v0.24.0): ticker.map holds only
standing truths; a JOURNAL between two listings and a dated RENAME are
.tt lines of an account, written date first, and IB's one-contract-id
ticker changes are booked as rename events.

- `.tt` `JOURNAL <date> FROM TO <qty>`: the move's two transfer legs
  (transfer evidence, journal_pair, event_source "tt"), the listings
  joined as one security in both countries (CA-XLIST-04 / US-XLIST-03),
  a duplicate of the broker's own journal booked once, a partial one
  completed;
- `.tt` `RENAME <date> OLD NEW [late=...]`: declared in any account,
  applied to every account whose books hold OLD, recorded once
  (CA-ACB-RENAME / US-BASIS-RENAME);
- IB's contract id under two symbols: booked, with the DISTINCT /
  late=separate way out;
- legacy ticker.map JOURNAL / dated RENAME lines: still read, one
  Warning; `tjs format-map --write` migrates them losslessly.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: U5550001, 55500001).

Formerly: test_fix_dated_events
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}
_TWO_ACCOUNTS = ('[accounts.margin]\ntype = "taxable"\n\n'
                 '[accounts.plan]\ntype = "sheltered"\n')


def _sum(root):
    r = cli(root, "sum", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _holdings(root, acct="margin"):
    p = root / "reports" / f"{acct}_holdings.toml"
    return "\n".join(ln for ln in p.read_text().splitlines()
                     if not ln.startswith("generated_at"))


# ------------------------------------------------------------ .tt lines

class TestTtLines(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestTtLines"""

    def test_journal_line(self):
        from taxjson.lib.tt_format import parse_journal_line
        j = parse_journal_line("JOURNAL 2025-03-05 qzg.to QZG.U.TO 1,000"
                               "  # gambit")
        self.assertEqual((j["date"], j["from"], j["to"], j["quantity"]),
                         ("2025-03-05", "QZG.TO", "QZG.U.TO", 1000.0))
        self.assertIsNone(parse_journal_line("BUYSELL 2025-01-01 ..."))
        for bad, word in (
                ("JOURNAL QZG.U.TO QZG.TO", "old ticker.map line"),
                ("JOURNAL 2025-03-05 QZG.TO QZG.U.TO", "expected"),
                ("JOURNAL 2025-13-05 QZG.TO QZG.U.TO 10", "not YYYY-MM-DD"),
                ("JOURNAL 2025-03-05 QZG.TO QZG.TO 10", "the same listing"),
                ("JOURNAL 2025-03-05 QZG.TO QZG.U.TO -10", "positive"),
                ("JOURNAL 2025-03-05 QZG.TO QZG.U.TO ten", "not a number")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    parse_journal_line(bad, "x.tt:3")
                self.assertIn("x.tt:3", str(cm.exception))
                self.assertIn(word, str(cm.exception))
                self.assertIn("JOURNAL <date> <FROM> <TO> <qty>",
                              str(cm.exception))

    def test_rename_line(self):
        from taxjson.lib.tt_format import parse_rename_line
        r = parse_rename_line("RENAME 2025-04-01 qzold.us QZNEW.US "
                              "late=fold")
        self.assertEqual((r["date"], r["old"], r["new"], r["late"]),
                         ("2025-04-01", "QZOLD.US", "QZNEW.US", "fold"))
        with self.assertRaises(ValueError) as cm:
            parse_rename_line("RENAME QZOLD.US QZNEW.US 2025-04-01", "x.tt:2")
        self.assertIn("date first", str(cm.exception))
        self.assertIn("RENAME 2025-04-01 QZOLD.US QZNEW.US", str(cm.exception))
        for bad in ("RENAME 2025-04-01 QZOLD.US", "RENAME 2025-04-01 A B "
                    "late=maybe", "RENAME 2025-04-01 A A"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    parse_rename_line(bad)
                self.assertIn("RENAME <date> <OLD> <NEW>", str(cm.exception))

    def test_convert_tt_skips_the_event_lines(self):
        from taxjson.lib.tt_format import tt_to_json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.tt"
            p.write_text("BUYSELL 2025-03-03 10:00:00 QZG.TO 100 CAD 10.00 "
                         "1000.00 0.00\n"
                         "JOURNAL 2025-03-05 QZG.TO QZG.U.TO 100\n"
                         "RENAME 2025-04-01 QZG.TO QZH.TO\n")
            doc = tt_to_json(p, "margin")
        self.assertEqual([t["action"] for t in doc["transactions"]],
                         ["BUYSELL"])

    def test_run_refuses_a_malformed_line_naming_the_form(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": "JOURNAL QZG.U.TO QZG.TO\n"})["canada"]
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            out = _out(r)
            self.assertIn("cannot be booked", out)
            self.assertIn("inputs/margin/m.tt:1", out)
            self.assertIn("JOURNAL <date> <FROM> <TO> <qty>", out)

    def test_journal_in_a_crypto_account_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=(
                '[accounts.margin]\ntype = "taxable"\n\n'
                '[accounts.cx]\ntype = "taxable"\ncrypto = true\n'),
                files={"inputs/cx/c.tt": "JOURNAL 2025-03-05 QZC QZD 1\n"}
            )["canada"]
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("crypto account", _out(r))


# ------------------------------------------------------------ JOURNAL

def _gambit_tt(x, cur):
    """Bought on one listing, journaled to the other, sold there: one
    security, a 200 loss (home currency both sides)."""
    return (f"BUYSELL 2025-03-03 10:00:00 QZG.{x} 100 {cur} 10.00 1000.00 "
            f"0.00\n"
            f"JOURNAL 2025-03-05 QZG.{x} QZGB.{x} 100\n"
            f"BUYSELL 2025-03-06 10:00:00 QZGB.{x} -100 {cur} 8.00 "
            f"800.00 0.00\n")


# A .tt JOURNAL joins two listings of one security (one root: pre-release
# review M2): the home-currency line and the TSX US-dollar line, both
# traded in the home currency so the loss is exact.
_TT_PAIR = {"TO": ("QZG.TO", "QZG.U.TO"), "US": ("QZG.US", "QZG.U.TO")}


def _tt_gambit(x, cur):
    a, b = _TT_PAIR[x]
    return (f"BUYSELL 2025-03-03 10:00:00 {a} 100 {cur} 10.00 1000.00 "
            f"0.00\n"
            f"JOURNAL 2025-03-05 {a} {b} 100\n"
            f"BUYSELL 2025-03-06 10:00:00 {b} -100 {cur} 8.00 "
            f"800.00 0.00\n")


# ------------------------------------------------------------ RENAME

def _rename_files(x, cur):
    return {
        "inputs/margin/m.tt": (
            f"BUYSELL 2024-01-10 10:00:00 QZOLD.{x} 50 {cur} 10.00 500.00 "
            f"0.00\n"
            f"BUYSELL 2025-06-03 10:00:00 QZNEW.{x} -50 {cur} 12.00 600.00 "
            f"0.00\n"),
        "inputs/plan/p.tt": (
            f"BUYSELL 2024-02-10 10:00:00 QZOLD.{x} 20 {cur} 10.00 200.00 "
            f"0.00\n"
            f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}\n")}


# ------------------------------------------------------------ IB conid

def _ib(late=False):
    """IB: QZOA bought, then the same contract id as QZNB sold (a ticker
    change with no corporate-action row); `late`: a QZOA buy after the
    change (another company reusing the ticker)."""
    from _support.ib_statement_rows import FII_H, HEAD, TRADES_H, _trade
    body = (HEAD + TRADES_H
            + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
            + _trade('QZOA', '2025-04-01, 10:00:00', 50, 11, -550)
            + _trade('QZNB', '2025-05-12, 10:00:00', -150, 12, 1800,
                     code='C'))
    if late:
        body += (_trade('QZOA', '2025-06-02, 10:00:00', 10, 20, -200)
                 + _trade('QZNB', '2025-07-01, 10:00:00', 5, 12, -60))
    return body + FII_H + (
        'Financial Instrument Information,Data,Stocks,"QZNB, QZOA",'
        'QZNB CORP,990000779,US9990007791,,NYSE,1,,,COMMON,,\n')


# ------------------------------------------------------------ legacy map

_LEGACY_MAP = ("# the fund's two lines\n"
               "JOURNAL QZGB.{x} QZG.{x}  # gambit\n"
               "# renamed by its issuer\n"
               "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01\n")


def _legacy_files(x, cur):
    f = _rename_files(x, cur)
    f["inputs/plan/p.tt"] = f["inputs/plan/p.tt"].replace(
        f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}\n", "")
    f["inputs/margin/m.tt"] += _gambit_tt(x, cur).replace(
        f"JOURNAL 2025-03-05 QZG.{x} QZGB.{x} 100\n", "")
    f["ticker.map"] = _LEGACY_MAP.format(x=x)
    return f


class TestLegacyMapAndMigration(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestLegacyMapAndMigration"""

    def _check(self, country):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=_TWO_ACCOUNTS,
                                 files=_legacy_files(x, cur))[country]
            r = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            out = _out(r)
            self.assertEqual(out.count("dated events written the old way"),
                             1, out)
            before = (_sum(root), _holdings(root), _holdings(root, "plan"))
            self.assertAlmostEqual(before[0]["totals"]["total"], -100.0,
                                   delta=0.011)
            # --check fails while a migration is pending; the dry run
            # shows the .tt additions and writes nothing.
            c = cli(root, "format-map", "--check")
            self.assertEqual(c.returncode, 1)
            self.assertIn("dated events to migrate", _out(c))
            d = cli(root, "format-map")
            self.assertEqual(d.returncode, 0, _out(d))
            self.assertIn(f"+RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}", d.stdout)
            self.assertFalse((root / "inputs/margin/renames.tt").exists())
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            # One file: the first account whose books carry the change.
            tt = (root / "inputs/margin/renames.tt").read_text()
            self.assertIn("# renamed by its issuer\n"
                          f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}\n", tt)
            self.assertFalse((root / "inputs/plan/renames.tt").exists())
            tm = (root / "ticker.map").read_text()
            self.assertIn(f"# the fund's two lines\nTOBASE QZGB.{x} "
                          f"QZG.{x}  # gambit", tm)
            self.assertNotIn("JOURNAL", tm.replace("JOURNAL YYYY", ""))
            self.assertNotIn("--- Dated events ---", tm)
            # Idempotent, --check clean.
            self.assertIn("already formatted",
                          cli(root, "format-map", "--write").stdout)
            self.assertEqual(cli(root, "format-map", "--check")
                             .returncode, 0)
            # The books are the same.
            r = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            self.assertNotIn("written the old way", _out(r))
            after = (_sum(root), _holdings(root), _holdings(root, "plan"))
            self.assertEqual(after, before)

    @rule(R.CA_XLIST_04)
    @rule(R.CA_ACB_RENAME)
    def test_canada_legacy_lines_warned_and_migrated_losslessly(self):
        self._check("canada")

    @rule(R.US_XLIST_03)
    @rule(R.US_BASIS_RENAME)
    def test_usa_legacy_lines_warned_and_migrated_losslessly(self):
        self._check("usa")


class TestFormatMigration(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestFormatMigration"""

    MAP = ("GLOBAL QZA.US QZB.US\n"
           "# the gambit fund\n"
           "JOURNAL QZD.U.TO QZD.TO  # gambit\n"
           "# JOURNAL QZE.U.TO QZE.TO\n"
           "# renamed in 2024\n"
           "RENAME QZOLD.US QZNEW.US 2024-06-10 late=fold  # per notice\n"
           "\n"
           "# RENAME OLDQ.US NEWQ.US 2024-06-10\n"
           "# RENAME QZX.US QZY.US 2023-01-02\n"
           "TOBASE QZF.US QZF.TO\n")

    def test_lossless_and_idempotent(self):
        from taxjson.lib.tt_format import parse_rename_line
        from taxjson.lib.ticker_map_rules import _parse_map_text
        from taxjson.lib.ticker_map_format import format_map
        r = format_map(self.MAP, migrate=True)
        self.assertTrue(r.migration_pending)
        old, _p, _n = _parse_map_text(self.MAP)
        new, problems, _n = _parse_map_text(r.text)
        self.assertEqual(problems, [])
        # The map minus the moved lines plus the .tt lines means the same.
        self.assertEqual(new.glob, old.glob)
        self.assertEqual(new.tobase, old.tobase)
        self.assertEqual((new.journal, new.dated), ({}, ()))
        live = [parse_rename_line(m.tt_line) for m in r.moved
                if not m.commented]
        self.assertEqual([(x["old"], x["new"], x["date"], x["late"])
                          for x in live],
                         [(d.old, d.new, d.date, d.late) for d in old.dated])
        self.assertEqual([m.lines() for m in r.moved], [
            ["# renamed in 2024", "RENAME 2024-06-10 QZOLD.US QZNEW.US "
             "late=fold  # per notice"],
            ["# RENAME 2023-01-02 QZX.US QZY.US"]])
        self.assertIn("# TOBASE QZE.U.TO QZE.TO", r.text)
        self.assertEqual(r.dropped_examples,
                         ["# RENAME OLDQ.US NEWQ.US 2024-06-10"])
        again = format_map(r.text, migrate=True)
        self.assertFalse(again.changed or again.migration_pending)

    def test_init_template_has_no_dated_event(self):
        from taxjson.lib.ticker_map_format import init_template
        t = init_template()
        self.assertNotIn("JOURNAL ABCX", t)
        self.assertNotIn("\n# RENAME", t)
        self.assertNotIn("--- Dated events ---", t)


# ------------------------------------------- the two listing commands

class TestSourcesInTheCommands(unittest.TestCase):
    """`tjs journals` and `tjs renames` name where each dated
    event came from: a .tt line, a legacy ticker.map line, IB's contract
    id (both countries).

    Formerly: test_fix_dated_events.TestSourcesInTheCommands"""

    def _journal(self, country):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": _tt_gambit(x, cur)})[country]
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            doc = json.loads(cli(root, "journals", "--json").stdout)
            got = [(j["source"], j["state"], j["from"], j["to"])
                   for j in doc["journals"]]
            self.assertEqual(got, [("tt", "joined", *_TT_PAIR[x])])
            text = cli(root, "journals").stdout
            self.assertIn(".tt", text)

    @rule(R.CA_XLIST_04)
    def test_canada_journals_names_the_tt_source(self):
        self._journal("canada")

    @rule(R.US_XLIST_03)
    def test_usa_journals_names_the_tt_source(self):
        self._journal("usa")

    def _renames(self, country):
        x, cur = _HOME[country]
        cases = {
            "tt": _rename_files(x, cur),
            "map": _legacy_files(x, cur),
        }
        for want, files in cases.items():
            with self.subTest(source=want), \
                    tempfile.TemporaryDirectory() as td:
                root = projects_both(td, accounts=_TWO_ACCOUNTS,
                                     files=files)[country]
                self.assertEqual(cli(root, "run", "--no-input")
                                 .returncode, 0)
                doc = json.loads(cli(root, "renames", "--json").stdout)
                (ev,) = doc["renames"]
                self.assertEqual(ev["source"], want)
                label = {"tt": ".tt line", "map": "ticker.map line"}[want]
                self.assertTrue(any(s.startswith(label)
                                    for s in ev["sources"]), ev)
                self.assertIn(label, cli(root, "renames").stdout)
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/ib.csv": _ib()})[country]
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual([(e["source"], e["sources"])
                              for e in doc["renames"]],
                             [("ib-conid", ["detected IB contract id"])])
            self.assertIn("detected IB contract id",
                          cli(root, "renames").stdout)

    @rule(R.CA_ACB_RENAME)
    def test_canada_renames_names_tt_map_and_ib_sources(self):
        self._renames("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_renames_names_tt_map_and_ib_sources(self):
        self._renames("usa")


if __name__ == "__main__":
    unittest.main()
