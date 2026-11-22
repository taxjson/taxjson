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
from taxjson.lib import dated_events as DE
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


class TestTtRename(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestTtRename"""

    def _check(self, country):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=_TWO_ACCOUNTS,
                                 files=_rename_files(x, cur))[country]
            r = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            # Declared in the plan's .tt, booked in both accounts.
            doc = json.loads(cli(root, "renames", "--json").stdout)
            (ev,) = doc["renames"]
            self.assertEqual((ev["date"], ev["old"], ev["new"],
                              ev["source"]),
                             ("2025-04-01", f"QZOLD.{x}", f"QZNEW.{x}",
                              "tt"))
            self.assertEqual(sorted(ev["accounts"]), ["margin", "plan"])
            self.assertEqual(ev["ticker_map_lines"],
                             ["inputs/plan/p.tt:2"])
            self.assertAlmostEqual(_sum(root)["totals"]["total"], 100.0,
                                   delta=0.011)
            # Recorded once.
            (rec,) = DE.read_state(root / "work" / DE.STATE)["renames"]
            self.assertEqual((rec["source"], rec["where"],
                              sorted(rec["accounts"]), rec["status"]),
                             ("tt", ["inputs/plan/p.tt:2"],
                              ["margin", "plan"], "booked"))
            base = json.loads((root / "work" / "margin_base.json")
                              .read_text())["transactions"]
            (split,) = [t for t in base if t["action"] == "SPLIT"]
            self.assertEqual((split.get("event_source"), split["source"]),
                             ("tt", "p.tt"))

    @rule(R.CA_ACB_RENAME)
    def test_canada_rename_applies_to_every_account(self):
        self._check("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_rename_applies_to_every_account(self):
        self._check("usa")

    def test_declared_twice_is_one_event_contradiction_refused(self):
        x, cur = _HOME["canada"]
        files = _rename_files(x, cur)
        files["inputs/margin/r.tt"] = "RENAME 2025-04-02 QZOLD.TO QZNEW.TO\n"
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=_TWO_ACCOUNTS,
                                 files=files)["canada"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _out(r)[-2000:])
            self.assertIn("one event, booked once", _out(r))
            (root / "inputs/margin/r.tt").write_text(
                "RENAME 2025-04-02 QZOLD.TO QZOTHER.TO\n")
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("contradicts", _out(r))
            (root / "inputs/margin/r.tt").unlink()
            (root / "ticker.map").write_text(
                "RENAME QZOLD.TO QZOTHER.TO 2025-04-01\n")
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("contradicts", _out(r))


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


class TestIbContractIdRename(unittest.TestCase):
    """Formerly: test_fix_dated_events.TestIbContractIdRename"""

    def _check(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/ib.csv": _ib()})[country]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            out = _out(r)
            # (the console starts the line's second part with a capital)
            self.assertIn("booked as a ticker change", out.lower())
            self.assertIn("`DISTINCT QZOA.US QZNB.US`", out)
            self.assertNotIn("go short", out)
            doc = json.loads(cli(root, "renames", "--json").stdout)
            (ev,) = doc["renames"]
            self.assertEqual((ev["old"], ev["new"], ev["date"],
                              ev["source"]),
                             ("QZOA.US", "QZNB.US", "2025-05-12",
                              "ib-conid"))
            (rec,) = DE.read_state(root / "work" / DE.STATE)["renames"]
            self.assertEqual((rec["source"], rec["accounts"]),
                             ("ib-conid", ["margin"]))
            # The cost carried: one sale of the 150 shares.
            g = json.loads((root / "work" / "margin_gains.json")
                           .read_text())
            sales = [t for t in g["transactions"] if "gain" in t]
            self.assertEqual({t["symbol"] for t in sales}, {"QZNB.US"})
            # Nothing left to suggest: the change is booked.
            s = cli(root, "ticker-map", "--suggest", "--json")
            self.assertEqual(json.loads(s.stdout)["suggestions"], [])

    @rule(R.CA_ACB_RENAME)
    def test_canada_contract_id_change_is_booked(self):
        self._check("canada")

    @rule(R.US_BASIS_RENAME)
    def test_usa_contract_id_change_is_booked(self):
        self._check("usa")

    @rule(R.CA_ACB_RENAME)
    def test_distinct_is_the_way_out(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/ib.csv": _ib(),
                "ticker.map": "DISTINCT QZOA.US QZNB.US\n"})["canada"]
            cli(root, "run", "--no-input")
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["renames"], [])

    @rule(R.CA_ACB_RENAME)
    def test_late_separate_is_the_way_out(self):
        # QZOA rows after QZNB's first: the contract id's rows overlap,
        # so nothing is booked from it (pre-release review M1); the
        # ATTENTION line names the .tt line and its late= choices.
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/ib.csv": _ib(late=True)})["canada"]
            r = cli(root, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("`RENAME 2025-05-12 QZOA.US QZNB.US`", _out(r))
            self.assertIn("`late=separate`", _out(r))
            self.assertNotIn("booked as a ticker change", _out(r).lower())
            self.assertIn("RENAME 2025-05-12 QZOA.US QZNB.US",
                          cli(root, "renames").stdout)
            (root / "inputs" / "margin" / "renames.tt").write_text(
                "RENAME 2025-05-12 QZOA.US QZNB.US late=separate\n")
            r = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, _out(r)[-3000:])
            # The .tt line books the change now (IB's booking stands down).
            self.assertNotIn("booked as a ticker change", _out(r).lower())
            doc = json.loads(cli(root, "renames", "--json").stdout)
            self.assertEqual(doc["unresolved"], 0)
            self.assertEqual(doc["renames"][0]["source"], "tt")


if __name__ == "__main__":
    unittest.main()
