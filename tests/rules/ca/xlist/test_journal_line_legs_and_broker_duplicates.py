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

from taxjson.lib import cross_listings as XL
from taxjson.lib import project_layout as _PL
from taxjson.lib import dated_events as DE
from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R
from _support.rbc_gambit_journal import _rbc_gambit

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}


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


class TestTtJournal(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestTtJournal"""

    def _check(self, country):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": _tt_gambit(x, cur)})[country]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            out = _out(r)
            self.assertNotIn("go short", out)
            self.assertIn("1 journal(s) from .tt lines booked", out)
            # The legs: the account's transfer evidence, one pair id.
            side = json.loads(DE.sidecar_path(root / "work", "margin")
                              .read_text())
            legs = side["transactions"]
            self.assertEqual(sorted((t["symbol"], t["quantity"])
                                    for t in legs),
                             sorted([(_TT_PAIR[x][0], -100.0),
                                     (_TT_PAIR[x][1], 100.0)]))
            self.assertEqual({t["journal_pair"] for t in legs},
                             {"tt:margin:2025-03-05#1"})
            self.assertEqual({t["event_source"] for t in legs}, {"tt"})
            self.assertEqual(side["metadata"]["kind"], "transfer_sidecar")
            # Never rows of the tax books.
            base = json.loads((root / "work" / "margin_base.json")
                              .read_text())
            self.assertFalse(any(t["action"] == "TRANSFER"
                                 for t in base["transactions"]))
            # Joined, source "tt", as a TOBASE line of the effective map.
            st = XL.read_state(root / "work" / XL.STATE)
            self.assertEqual([(j["source"], j["journal"], j["where"])
                              for j in st["joined"]],
                             [("tt", "tt", "inputs/margin/m.tt:2")])
            self.assertIn("TOBASE", (root / "work" / _PL.EFFECTIVE_MAP)
                          .read_text())
            # One security: the loss, nothing short, nothing held.
            self.assertAlmostEqual(_sum(root)["totals"]["total"], -200.0,
                                   delta=0.011)
            self.assertNotIn("[[holding]]", _holdings(root))
            m = cli(root, "find-missing-history")
            self.assertNotIn("QZG", m.stdout)
            # The record of the event.
            rec = DE.read_state(root / "work" / DE.STATE)["journals"]
            self.assertEqual([(j["status"], j["source"], j["legs"])
                              for j in rec],
                             [("booked", "tt", ["out", "in"])])

    @rule(R.CA_XLIST_04)
    def test_canada_journal_books_legs_and_joins(self):
        self._check("canada")

    @rule(R.US_XLIST_03)
    def test_usa_journal_books_legs_and_joins(self):
        self._check("usa")

    def test_distinct_keeps_the_listings_apart(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": _tt_gambit("TO", "CAD"),
                "ticker.map": "DISTINCT QZG.TO QZG.U.TO\n"})["canada"]
            r = cli(root, "run", "--no-input")
            self.assertIn("ticker.map keeps apart (DISTINCT", _out(r))
            st = XL.read_state(root / "work" / XL.STATE)
            self.assertEqual(st["joined"], [])


class TestJournalTheBrokerAlreadyHolds(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestJournalTheBrokerAlreadyHolds"""

    def _run(self, country, both_legs, with_line):
        rbc, tmap = _rbc_gambit(both_legs)
        files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap}
        if with_line:
            files["inputs/margin/j.tt"] = ("JOURNAL 2025-05-06 QZD.TO "
                                           "QZD.U.TO 1000\n")
        td = tempfile.mkdtemp(dir=self.td)
        root = projects_both(td, files=files,
                             usa={"source_currencies": ["CAD"]})[country]
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, _out(r)[-3000:])
        return root, _out(r)

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.td = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def _check_duplicate(self, country):
        root0, _ = self._run(country, True, False)
        root, out = self._run(country, True, True)
        self.assertIn("is already in the broker's rows", out)
        self.assertFalse(DE.sidecar_path(root / "work", "margin").exists())
        rec = DE.read_state(root / "work" / DE.STATE)["journals"]
        self.assertEqual([(j["status"], j["legs"]) for j in rec],
                         [("duplicate", [])])
        self.assertEqual(_sum(root)["totals"], _sum(root0)["totals"])
        st = XL.read_state(root / "work" / XL.STATE)
        self.assertEqual([j["source"] for j in st["joined"]], ["tt"])

    @rule(R.CA_XLIST_04)
    def test_canada_duplicate_is_booked_once(self):
        self._check_duplicate("canada")

    @rule(R.US_XLIST_03)
    def test_usa_duplicate_is_booked_once(self):
        self._check_duplicate("usa")

    @rule(R.CA_XLIST_04)
    def test_one_broker_leg_books_the_other(self):
        root, out = self._run("canada", False, True)
        self.assertIn("in-leg is booked from the .tt line", out.lower())
        rec = DE.read_state(root / "work" / DE.STATE)["journals"]
        self.assertEqual([(j["status"], j["legs"]) for j in rec],
                         [("partial", ["in"])])
        side = json.loads(DE.sidecar_path(root / "work", "margin")
                          .read_text())["transactions"]
        self.assertEqual([(t["symbol"], t["quantity"]) for t in side],
                         [("QZD.U.TO", 1000.0)])
        # The same books as the broker's complete journal.
        root0, _ = self._run("canada", True, False)
        self.assertEqual(_sum(root)["totals"], _sum(root0)["totals"])


class TestSettleJournals(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestSettleJournals"""

    def _j(self, **kw):
        d = dict(account="m", date="2025-05-06", frm="A.TO", to="A.U.TO",
                 quantity=10.0, where="inputs/m/x.tt:1", pair="tt:m:x#1")
        d.update(kw)
        return DE.Journal(**d)

    def _leg(self, sym, q, day="2025-05-07", acct="m"):
        return XL.Leg(acct, "rbc_direct", sym, day, q)

    def test_statuses(self):
        j = self._j()
        DE.settle_journals([j], [self._leg("A.TO", -10),
                                 self._leg("A.U.TO", 10)])
        self.assertEqual((j.status, j.legs), ("duplicate", ()))
        j = self._j()
        DE.settle_journals([j], [self._leg("A.TO", -10)])
        self.assertEqual((j.status, j.legs), ("partial", ("in",)))
        # Another account's legs, another quantity, a week away: not it.
        j = self._j()
        DE.settle_journals([j], [self._leg("A.TO", -10, acct="o"),
                                 self._leg("A.U.TO", 9),
                                 self._leg("A.TO", -10, day="2025-05-16")])
        self.assertEqual((j.status, j.legs), ("booked", ("out", "in")))


if __name__ == "__main__":
    unittest.main()
