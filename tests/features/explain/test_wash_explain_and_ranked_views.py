"""Backlog group `views` (2026-10-25): the stats, win-rates,
winners / losers, `wash-sales --explain`, audit and Schedule 3 views.

TJ-0025  win-rates SYMBOL: the denied total and the unknown-cost count
         cover the symbols named, not every ticker.
TJ-0061  stats / win-rates: a denied loss on an option whose
         replacement option is exercised counts once (the deferral
         rides the premium into the shares' tally).
TJ-0062  stats: an s.86.1 spin-off moves its share of the parent's
         deferral tally to the new shares, as it moves the ACB.
TJ-0096  wash-sales --explain: a denial part permanent names the
         taxable replacement's date, not the registered one's.
TJ-0111  wash-sales --explain: the window's ACB/SH after the trigger
         row includes the deferred loss added on that row.
TJ-0113  audit's pool history: an ADJUST's amount is not a quantity.
TJ-0119  sum / form-export notes: a short sale's opening commission
         stays netted in its proceeds (the filed cells unchanged).
TJ-0120  winners / losers: a DENIED column, so PROCEEDS - COST + DENIED
         is the GAIN.
TJ-0183  winners / losers scoped to an account name it on the total.
TJ-0196  audit with an account deferred at a corp-action election:
         not audited (the blended accounts with it too), never
         MISMATCHED / FAILED with a stale-work/ diagnosis.

Synthetic books only: made-up tickers (ZZA.TO, ZZB.TO ...), account
names (margin, margin2, rrsp), amounts under 1,000.

Formerly: test_fix_backlog_views
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs -C root args...` from this tree's sources, captured
    (width 0)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


def _project(tmp: str, country: str, year: int, books: dict,
             types: dict) -> Path:
    """A project with one .tt book per account ({account: lines})."""
    root = Path(tmp) / "p"
    base = "USD" if country == "usa" else "CAD"
    toml = (f'[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = []\n')
    for acct, typ in types.items():
        toml += f'[accounts.{acct}]\ntype = "{typ}"\n'
    (root / "inputs").mkdir(parents=True)
    (root / "taxjson.toml").write_text(toml)
    for acct, lines in books.items():
        d = root / "inputs" / acct
        d.mkdir()
        (d / f"{acct}.tt").write_text("\n".join(lines) + "\n")
    return root


def _flat(text: str) -> str:
    return " ".join(text.split())


# One Canadian project for the views: ZZA a partial superficial loss
# (240 of 600 denied, rebought in margin), ZZB a plain winner and ZZS a
# short sale (margin2), ZZU a loss rebought partly in the RRSP (for
# good) and partly in margin2 (deferred), ZZD a stock dividend booked
# as an ADJUST.
_BOOKS = {
    "margin": [
        "BUYSELL 2024-02-01 09:30:00 ZZA.TO 100 CAD 9.00 900.00 0",
        "BUYSELL 2024-03-01 09:30:00 ZZA.TO -100 CAD 3.00 300.00 0",
        "BUYSELL 2024-03-11 09:30:00 ZZA.TO 40 CAD 3.00 120.00 0",
        "BUYSELL 2024-03-01 09:30:00 ZZU.TO 100 CAD 9.00 900.00 0",
        "BUYSELL 2024-08-01 09:30:00 ZZU.TO -100 CAD 5.00 500.00 0",
        "BUYSELL 2024-03-01 09:30:00 ZZD.TO 20 CAD 20.00 404.95 4.95",
        "BUYSELL 2024-09-16 09:30:00 ZZD.TO 2 CAD 0 0 0",
        "ADJUST 2024-09-16 09:30:00 ZZD.TO CAD 50.00",
        "BUYSELL 2024-11-01 09:30:00 ZZD.TO -22 CAD 25.00 550.00 0",
        "BUYSELL 2024-01-10 09:30:00 ZZP.TO 100 CAD 9.00 900.00 0",
        "BUYSELL 2024-02-20 09:30:00 ZZP.TO 50 CAD 5.00 250.00 0",
        "BUYSELL 2024-03-01 09:30:00 ZZP.TO -100 CAD 4.00 400.00 0",
    ],
    "margin2": [
        "BUYSELL 2024-02-01 09:30:00 ZZB.TO 10 CAD 10.00 100.00 0",
        "BUYSELL 2024-05-01 09:30:00 ZZB.TO -10 CAD 12.00 120.00 0",
        "BUYSELL 2024-05-01 09:30:00 ZZS.TO -100 CAD 5.00 495.05 4.95",
        "BUYSELL 2024-06-03 09:30:00 ZZS.TO 100 CAD 4.00 404.95 4.95",
        "BUYSELL 2024-08-20 09:30:00 ZZU.TO 20 CAD 5.50 110.00 0",
    ],
    "rrsp": [
        "BUYSELL 2024-08-15 09:30:00 ZZU.TO 60 CAD 5.00 300.00 0",
    ],
}
_TYPES = {"margin": "taxable", "margin2": "taxable", "rrsp": "sheltered"}


class TestCanadaViews(unittest.TestCase):
    """Formerly: test_fix_backlog_views.TestCanadaViews"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = _project(cls.tmp.name, "canada", 2024, _BOOKS, _TYPES)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _ok(self, *args) -> subprocess.CompletedProcess:
        r = _cli(self.root, *args)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r

    @rule(R.CA_RPT_16)
    def test_tj0025_win_rates_symbol_scopes_the_denied_total(self):
        """TJ-0025: `win-rates ZZB` printed every ticker's denials (here
        ZZA's 240 and ZZU's 320) beside ZZB's own trades."""
        doc = json.loads(self._ok("win-rates", "ZZB", "--json").stdout)
        self.assertEqual((doc["denied_total"], doc["denied_count"]),
                         (0.0, 0))
        doc = json.loads(self._ok("win-rates", "ZZA", "--json").stdout)
        self.assertEqual((doc["denied_total"], doc["denied_count"]),
                         (240.0, 1))
        doc = json.loads(self._ok("win-rates", "--json").stdout)
        self.assertEqual((doc["denied_total"], doc["denied_count"]),
                         (743.33, 3))
        text = _flat(self._ok("win-rates", "ZZB").stdout)
        self.assertIn("NOT subtracted above): 0.00 CAD over 0 "
                      "disposition(s)", text)

    def test_tj0120_losers_rows_add_up_with_a_denied_column(self):
        """TJ-0120: COST was the raw cost beside the allowed GAIN, so
        PROCEEDS - COST was not the GAIN on a row with a denial."""
        r = self._ok("losers")
        text = _flat(r.stdout)
        self.assertIn("TICKER CLOSES PROCEEDS COST DENIED GAIN", text)
        self.assertIn("ZZA.TO 1 300.00 900.00 240.00 -360.00", text)
        self.assertIn("ZZU.TO 1 500.00 900.00 320.00 -80.00", text)
        self.assertIn("GAIN = PROCEEDS − COST + DENIED", text)
        rows = {x["ticker"]: x for x in
                json.loads(self._ok("losers", "--json").stdout)["rows"]}
        for t in ("ZZA.TO", "ZZU.TO", "ZZB.TO"):
            x = rows[t]
            self.assertAlmostEqual(x["proceeds"] - x["cost"] + x["denied"],
                                   x["gain"], places=6, msg=t)
        # No denial among the rows shown: no DENIED column.
        self.assertNotIn("DENIED", self._ok("winners").stdout)

    def test_tj0183_scoped_total_names_the_account(self):
        """TJ-0183: `winners margin2` said "all accounts" on a total of
        margin2 alone."""
        text = _flat(self._ok("winners", "margin2").stdout)
        self.assertIn("(2 ticker(s), margin2; the losers: tjs losers)", text)
        self.assertNotIn("all accounts", text)
        text = _flat(self._ok("losers", "2024", "margin").stdout)
        self.assertIn("ticker(s), margin; the winners: tjs winners)", text)
        self.assertIn("all accounts;", _flat(self._ok("winners").stdout))

    def test_tj0096_tj0111_explain_names_the_taxable_bump(self):
        """TJ-0096: part of the ZZU denial is permanent (the RRSP's 60
        units), the rest deferred onto margin2's 20 units on
        2024-08-20; the text gave the RRSP's 240 as the 'first bump' on
        2024-08-15. TJ-0111: the window row of margin2's trigger showed
        the ACB/SH before the 80 was added (5.5000, not 9.5000)."""
        text = _flat(self._ok("wash-sales", "--explain").stdout)
        self.assertIn("deferred 80.00: added to the ACB of the replacement "
                      "units in your taxable pool (recovered when they are "
                      "sold), on 2024-08-20 (margin2)", text)
        self.assertNotIn("first bump", text)
        self.assertIn("PERMANENTLY denied 240.00", text)
        self.assertIn("T+19 2024-08-20 margin2 BUY +20.0000 5.5000 "
                      "+80.0000 9.5000 trigger", text)
        lst = _flat(self._ok("list").stdout)
        self.assertIn("margin2 ZZU.TO 20 190.00 9.50 80.00", lst)

    def test_tj0111_the_row_the_bump_lands_after(self):
        """TJ-0111: ZZA's replacement, bought after the loss: its row's
        ACB/SH includes the 240 (120 + 240 over 40 units). ZZP's, bought
        before it: the bump lands after the loss sale, whose row shows
        it (383.33 + 183.33 over 50 units)."""
        text = _flat(self._ok("wash-sales", "--explain").stdout)
        self.assertIn("T+10 2024-03-11 margin BUY +40.0000 3.0000 "
                      "+40.0000 9.0000 trigger", text)
        self.assertIn("T-10 2024-02-20 margin BUY +50.0000 5.0000 "
                      "+150.0000 7.6667 trigger", text)
        self.assertIn("T+0 2024-03-01 margin SELL -100.0000 4.0000 "
                      "+50.0000 11.3333 loss sale", text)

    def test_tj0113_audit_pool_history_adjust_has_no_quantity(self):
        """TJ-0113: the ADJUST's 50.00 sat under QTY (POOL_QTY 22)."""
        text = self._ok("audit", "ZZD.TO").stdout
        row = next(ln for ln in text.splitlines()
                   if "2024-09-16" in ln and "ADJUST" in ln)
        self.assertEqual(row.split()[:4],
                         ["2024-09-16", "ADJUST", "-", "-"], row)
        self.assertIn("50.0000", row)

    def test_tj0119_short_sale_commission_note_and_cells(self):
        """TJ-0119: the note said sell-side commissions are outlays while
        a short sale's opening commission is netted in its PROCEEDS.
        The note now says so; the filed cells are unchanged."""
        self.assertIn("A short sale shows what it brought in, net of its "
                      "opening commission, as PROCEEDS",
                      _flat(self._ok("sum", "--details").stdout))
        fe = _flat(self._ok("form-export", "--details").stdout)
        self.assertIn("ZZS.TO 2024 495.05 404.95 0.00 90.10", fe)
        self.assertIn("a short sale's opening commission, stay netted in "
                      "PROCEEDS", fe)

    def test_tj0196_audit_with_a_deferred_account(self):
        """TJ-0196: margin2 deferred at an election: the cross-account
        pass never ran, so margin's saved gains cannot tie. Not audited
        either, and said; never MISMATCHED and a stale-work/ FAILED."""
        pend = self.root / "work" / "margin2_pending_elections.json"
        pend.write_text('{"pending": []}')
        try:
            for args in (("audit", "--summary"), ("audit", "ZZA"),
                         ("audit", "--account", "margin")):
                r = _cli(self.root, *args)
                self.assertEqual(r.returncode, 1, args)
                both = _flat(r.stdout + r.stderr)
                self.assertIn("not audited — corp-action elections pending "
                              "for margin2 (so margin too: no cross-account "
                              "pass)", both, args)
                self.assertIn("tjs elect --pending", both, args)
                for bad in ("MISMATCH", "stale work/", "no disposition "
                            "matches"):
                    self.assertNotIn(bad, both, args)
        finally:
            pend.unlink()
        r = self._ok("audit", "--summary")
        self.assertIn("0 MISMATCHED", r.stdout)


if __name__ == "__main__":
    unittest.main()
