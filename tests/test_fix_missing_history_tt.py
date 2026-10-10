"""Missing history as dated .tt lines; positions views hold its units.

Owner decisions (v0.27.0), synthetic data only:

A. `OPENING <date> <SYMBOL> <qty> cost=unknown [reason="..."]` in an
   account's .tt file opens <qty> units at UNKNOWN cost on <date> — the
   opening a missing_history.json entry gets, with its date and quantity
   the user's (never sized from the rows, no year window). Sales that
   draw on them go to manual reporting in both countries (CA-ACB-11 /
   US-BASIS-04). `find-missing-history --write-missing-history` writes
   the lines into inputs/<account>/missing_history.tt (merged: never
   twice; another quantity flagged, never replaced). missing_history.json
   is still read; a pair both give is the .tt line's (Info). `taxjson
   migrate` converts the file — in the shared layout merging the year
   folders' files (agreeing entries one line; disagreeing ones listed,
   written with --write as the newest year sizes them; entries that open
   nothing dropped) — and the books stay the same.
"""
import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from _style import env
from _tmpfiles import private_dir
from tax_rules import rule

from taxjson.lib.core import TaxTransaction


def tjs(*args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *args],
        capture_output=True, text=True, env=env(TAXJSON_WIDTH=0),
        timeout=900, stdin=subprocess.DEVNULL)


def _ok(tc, r):
    tc.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
    return r


# 100 QZQ held before the data: 60 sold, 100 bought, 140 sold — every
# sale draws on a pool holding unknown-cost units. ZZB is complete.
_TT = {
    "canada": """\
# synthetic
BUYSELL 2024-01-10 10:00:00 ZZB.TO 10 CAD 10.00 109.95 9.95
BUYSELL 2024-02-01 10:00:00 QZQ.TO -60 CAD 20.00 1190.05 9.95
BUYSELL 2024-04-01 10:00:00 QZQ.TO 100 CAD 21.00 2109.95 9.95
BUYSELL 2024-09-01 10:00:00 QZQ.TO -140 CAD 25.00 3490.05 9.95
BUYSELL 2024-10-01 10:00:00 ZZB.TO -10 CAD 12.00 110.05 9.95
""",
    "usa": """\
# synthetic
BUYSELL 2024-01-10 10:00:00 ZZB.US 10 USD 10.00 101.00 1.00
BUYSELL 2024-02-01 10:00:00 QZQ.US -60 USD 20.00 1199.00 1.00
BUYSELL 2024-04-01 10:00:00 QZQ.US 100 USD 21.00 2101.00 1.00
BUYSELL 2024-09-01 10:00:00 QZQ.US -140 USD 25.00 3499.00 1.00
BUYSELL 2024-10-01 10:00:00 ZZB.US -10 USD 12.00 119.00 1.00
""",
}
_SFX = {"canada": "TO", "usa": "US"}


def _toml(country, year, shared=False):
    cur = "CAD" if country == "canada" else "USD"
    basis = "settle" if country == "canada" else "trade"
    return ("[settings]\n" f"year = {year}\n" f'country = "{country}"\n'
            + ('inputs_dir = "../inputs"\n' if shared else "")
            + f'tax_date = "{basis}"\n' f'base_currency = "{cur}"\n\n'
            "[accounts.margin]\n" 'type = "taxable"\n')


def project(country, *, tt_extra="", mh=None, shared_years=None):
    """A single-folder project (or, with shared_years, the folder holding
    year projects that share inputs/): the account's trades, plus
    `tt_extra` lines in inputs/margin/missing_history.tt and `mh` as each
    project's missing_history.json ({year: entries} when shared)."""
    top = Path(private_dir()) / "p"
    ins = top / "inputs" / "margin"
    ins.mkdir(parents=True)
    (ins / "trades.tt").write_text(_TT[country])
    if tt_extra:
        (ins / "missing_history.tt").write_text(tt_extra)
    if shared_years:
        for y in shared_years:
            (top / str(y)).mkdir()
            (top / str(y) / "taxjson.toml").write_text(
                _toml(country, y, shared=True))
            if mh and mh.get(y) is not None:
                (top / str(y) / "missing_history.json").write_text(
                    json.dumps(mh[y]))
        return top
    (top / "taxjson.toml").write_text(_toml(country, 2024))
    if mh is not None:
        (top / "missing_history.json").write_text(json.dumps(mh))
    return top


def _filing(d):
    r = _ok(unittest.TestCase(), tjs("-C", str(d), "sum", "--json"))
    return json.loads(r.stdout)["filing"]["totals"]


class TestTheLine(unittest.TestCase):
    """The .tt line: checked by convert-tt, never a row of the books."""

    def test_parse_and_write_back(self):
        from taxjson.bin.taxjson_convert_tt import (
            parse_unknown_opening_line, unknown_opening_text)
        o = parse_unknown_opening_line(
            'OPENING 2019-12-31 qzq.us 25 cost=unknown reason="from the '
            'old broker # 2 accounts" # a note')
        self.assertEqual((o["date"], o["symbol"], o["quantity"],
                          o["reason"]),
                         ("2019-12-31", "QZQ.US", 25.0,
                          "from the old broker # 2 accounts"))
        self.assertEqual(parse_unknown_opening_line(
            unknown_opening_text("2019-12-31", "QZQ.US", 25.5, "x"))
            ["quantity"], 25.5)
        # A positions-report OPENING line is not one.
        self.assertIsNone(parse_unknown_opening_line(
            "OPENING 2019-12-31 QZQ.US 25 USD 500.00"))
        for bad in ("OPENING 2019-12-31 QZQ.US 0 cost=unknown",
                    "OPENING 2019-12-31 QZQ.US 5 cost=12.50",
                    "OPENING 2019-13-31 QZQ.US 5 cost=unknown",
                    "OPENING 2019-12-31 QZQ.US cost=unknown",
                    "OPENING 2099-12-31 QZQ.US 5 cost=unknown"):
            with self.assertRaises(ValueError, msg=bad):
                parse_unknown_opening_line(bad)

    def test_convert_tt_skips_it(self):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line, tt_to_json
        d = Path(private_dir())
        f = d / "x.tt"
        f.write_text("OPENING 2019-12-31 QZQ.US 25 cost=unknown\n"
                     "BUYSELL 2024-02-01 10:00:00 QZQ.US -5 USD 20.00 "
                     "99.00 1.00\n")
        rows = tt_to_json(f, "margin")["transactions"]
        self.assertEqual([r["action"] for r in rows], ["BUYSELL"])
        with self.assertRaises(ValueError):
            parse_tt_line("OPENING 2019-12-31 QZQ.US 25 cost=unknown")

    def test_one_line_per_symbol_per_account(self):
        from taxjson.lib.missing_history import (TtOpeningError,
                                                 read_tt_openings)
        d = project("usa", tt_extra=(
            "OPENING 2023-12-31 QZQ.US 100 cost=unknown\n"
            "OPENING 2023-12-30 QZQ.US 10 cost=unknown\n"))
        with self.assertRaises(TtOpeningError) as cm:
            read_tt_openings(d)
        self.assertIn("missing_history.tt:2", str(cm.exception))


def _t(date, sym, qty, action="BUYSELL", new=""):
    return TaxTransaction(action=action, date=date, time="10:00:00",
                          symbol=sym, symbol_new=new, quantity=qty,
                          currency="USD", price=1.0, net_amount=-qty,
                          account="margin")


class TestTheOpening(unittest.TestCase):
    """synthesize_openings with a .tt line: its date and quantity."""

    def _apply(self, txs, sym, date, qty, json_pairs=()):
        from taxjson.lib.missing_history import (MissingHistoryPairs,
                                                 TtOpening,
                                                 synthesize_openings)
        pairs = MissingHistoryPairs(set(json_pairs) | {(sym, "margin")})
        pairs.fixed[(sym, "margin")] = TtOpening(
            "margin", date, sym, qty, "inputs/margin/missing_history.tt:9")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out, log = synthesize_openings(txs, pairs, until="2024-12-31")
        ob = [t for t in out if t.action == "OPENING_BALANCE"]
        return ob, log, err.getvalue()

    @rule("US-BASIS-04")
    def test_quantity_and_date_as_written(self):
        txs = [_t("2024-02-01", "QZQ.US", -60), _t("2024-04-01", "QZQ.US",
                                                   100),
               _t("2025-09-01", "QZQ.US", -140)]
        # No year window: 100 stated, the 2025 sale included.
        ob, log, err = self._apply(txs, "QZQ.US", "2023-12-31", 100)
        self.assertEqual([(t.date, t.symbol, t.quantity) for t in ob],
                         [("2023-12-31", "QZQ.US", 100.0)])
        self.assertTrue(log[0]["fixed"])
        self.assertNotIn("short again", err)
        # Too few units: said, naming the line.
        ob, log, err = self._apply(txs, "QZQ.US", "2023-12-31", 60)
        self.assertIn("inputs/margin/missing_history.tt:9 opens 60 "
                      "QZQ.US / margin: the position goes short again on "
                      "2025-09-01 (40 units)", err)
        # Dated after the first sale it should cover: said.
        ob, log, err = self._apply(txs, "QZQ.US", "2024-03-01", 100)
        self.assertIn("already short on 2024-02-01 (60 units)", err)

    @rule("US-BASIS-04")
    def test_a_rename_after_the_date_opens_the_old_symbol(self):
        txs = [_t("2024-02-01", "OLDQ.US", -60),
               _t("2024-03-01", "OLDQ.US", 2, "SPLIT", "NEWQ.US"),
               _t("2024-04-01", "NEWQ.US", -80)]
        ob, log, err = self._apply(txs, "NEWQ.US", "2023-12-31", 100)
        self.assertEqual([(t.symbol, t.quantity) for t in ob],
                         [("OLDQ.US", 100.0)])
        self.assertEqual(err, "")

    def test_a_file_entry_of_the_same_chain_is_the_lines(self):
        txs = [_t("2024-02-01", "OLDQ.US", -60),
               _t("2024-03-01", "OLDQ.US", 1, "SPLIT", "NEWQ.US"),
               _t("2024-04-01", "NEWQ.US", -40)]
        ob, log, _e = self._apply(txs, "OLDQ.US", "2023-12-31", 100,
                                  json_pairs={("NEWQ.US", "margin")})
        self.assertEqual(len(ob), 1)
        note = [e for e in log if e["symbol"] == "NEWQ.US"][0]
        self.assertFalse(note["inserted"])
        self.assertIn("covered by the .tt line", note["note"])


class TestBothCountries(unittest.TestCase):
    """A .tt line books exactly what the same missing_history.json entry
    books (its recorded quantity), in each country's engine."""

    def _check(self, country):
        sym = f"QZQ.{_SFX[country]}"
        with_json = project(country, mh=[{"symbol": sym, "account":
                                          "margin", "quantity": 100}])
        with_tt = project(country, tt_extra=(
            f"OPENING 2023-12-31 {sym} 100 cost=unknown "
            f'reason="bought at the old broker"\n'))
        for d in (with_json, with_tt):
            _ok(self, tjs("-C", str(d), "run", "--no-input"))
        a, b = _filing(with_json), _filing(with_tt)
        self.assertEqual(a, b)
        # Canada: the pool held unknown-cost units through both QZQ
        # sales, only the complete ZZB sale is in the totals. US: FIFO
        # lots — the second sale's 100 units from the known purchase
        # are in them too, its 40 opening units are not.
        doc = json.loads(_ok(self, tjs("-C", str(with_tt), "sum",
                                       "--json")).stdout)
        self.assertEqual(doc["filing"]["accounts"][0]["dispositions"],
                         {"canada": 1, "usa": 2}[country])
        return with_tt

    @rule("CA-ACB-11")
    def test_canada(self):
        self._check("canada")

    @rule("US-BASIS-04")
    def test_usa(self):
        self._check("usa")

    def test_both_give_it_the_tt_line_wins(self):
        d = project("usa", mh=[{"symbol": "QZQ.US", "account": "margin",
                                "quantity": 30}],
                    tt_extra="OPENING 2023-12-31 QZQ.US 100 cost=unknown\n")
        r = _ok(self, tjs("-C", str(d), "run", "--no-input"))
        out = r.stdout + r.stderr
        self.assertIn("a .tt OPENING cost=unknown line both open QZQ.US / "
                      "margin", out)
        self.assertIn("`taxjson migrate` converts them", out)
        self.assertNotIn("goes short again", out)
        log = json.loads((d / "work" / "margin_gains.json").read_text())[
            "missing_history_log"]
        self.assertEqual([e["opening_qty"] for e in log if e["inserted"]],
                         [100.0])


class TestWriteMissingHistory(unittest.TestCase):
    """find-missing-history --write-missing-history writes .tt lines."""

    def test_written_merged_and_flagged(self):
        d = project("usa")
        _ok(self, tjs("-C", str(d), "run", "--no-input"))
        r = _ok(self, tjs("-C", str(d), "find-missing-history",
                          "--write-missing-history"))
        f = d / "inputs" / "margin" / "missing_history.tt"
        lines = [ln for ln in f.read_text().splitlines()
                 if ln.startswith("OPENING")]
        # The day before the account's first row, the units the run
        # opens (the deepest shortage through the year's end).
        self.assertEqual(len(lines), 1, f.read_text())
        self.assertTrue(lines[0].startswith(
            "OPENING 2024-01-09 QZQ.US 100 cost=unknown"), lines[0])
        self.assertIn("Wrote 1 OPENING cost=unknown line(s) to "
                      "inputs/margin/missing_history.tt", r.stderr)
        # Again: never written twice.
        r = _ok(self, tjs("-C", str(d), "find-missing-history",
                          "--write-missing-history"))
        self.assertEqual(f.read_text().count("OPENING 2024"), 1)
        self.assertIn("1 line(s) already there", r.stdout + r.stderr)
        # Another quantity on the line: flagged, left as it is.
        f.write_text(f.read_text().replace(" 100 cost", " 90 cost"))
        r = _ok(self, tjs("-C", str(d), "find-missing-history",
                          "--write-missing-history"))
        self.assertIn("missing_history.tt:", r.stderr)
        self.assertIn("opens 90, the books size 100 — left as it is",
                      r.stderr)
        self.assertIn(" 90 cost=unknown", f.read_text())
        # The run reads the line: no missing_history.json needed.
        r = _ok(self, tjs("-C", str(d), "run", "--no-input"))
        self.assertIn("Reading 1 .tt OPENING cost=unknown line",
                      r.stdout + r.stderr)


class TestMigrate(unittest.TestCase):
    """`taxjson migrate`: missing_history.json -> .tt lines."""

    def test_single_folder_books_unchanged(self):
        d = project("canada", mh=[{"symbol": "QZQ.TO",
                                   "account": "margin"}])
        _ok(self, tjs("-C", str(d), "run", "--no-input"))
        before = _filing(d)
        r = _ok(self, tjs("-C", str(d), "migrate"))
        self.assertIn("QZQ.TO 100", r.stdout)
        self.assertFalse((d / "missing_history.json").exists())
        self.assertTrue((d / "missing_history.json.migrated").exists())
        tt = (d / "inputs" / "margin" / "missing_history.tt").read_text()
        self.assertIn("OPENING 2024-01-09 QZQ.TO 100 cost=unknown", tt)
        _ok(self, tjs("-C", str(d), "run", "--no-input"))
        self.assertEqual(_filing(d), before)

    @rule("CA-ACB-11")
    def test_year_folders_merged(self):
        sym = "QZQ.TO"
        mh = {2024: [{"symbol": sym, "account": "margin"},
                     {"symbol": "ZZB.TO", "account": "margin"},
                     {"symbol": "NOPE.TO", "account": "margin"}],
              2025: [{"symbol": sym, "account": "margin",
                      "quantity": 120}]}
        top = project("canada", mh=mh, shared_years=(2024, 2025))
        before = {}
        for y in (2024, 2025):
            _ok(self, tjs("-C", str(top / str(y)), "run", "--no-input"))
            before[y] = _filing(top / str(y))
        r = tjs("-C", str(top / "2025"), "migrate")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"{sym} / margin: 2024: 100, 2025: 120 — written: "
                      f"120 (2025's view)", r.stdout)
        self.assertIn("ZZB.TO / margin: 2024: no opening needed", r.stdout)
        self.assertIn("NOPE.TO / margin: 2024: no rows", r.stdout)
        self.assertIn("pass --write", r.stdout + r.stderr)
        self.assertFalse((top / "inputs" / "margin" /
                          "missing_history.tt").exists())
        self.assertTrue((top / "2024" / "missing_history.json").exists())
        r = _ok(self, tjs("-C", str(top / "2025"), "migrate", "--write"))
        tt = (top / "inputs" / "margin" / "missing_history.tt").read_text()
        self.assertIn(f"OPENING 2024-01-09 {sym} 120 cost=unknown", tt)
        self.assertIn("apply to every year", tt)
        self.assertNotIn("ZZB.TO", tt)
        for y in (2024, 2025):
            self.assertFalse((top / str(y) /
                              "missing_history.json").exists())
            _ok(self, tjs("-C", str(top / str(y)), "run", "--no-input"))
        # Every QZQ sale stays out of the totals either way (the pool
        # holds unknown-cost units through both): the books are the same.
        for y in (2024, 2025):
            self.assertEqual(_filing(top / str(y)), before[y])


if __name__ == "__main__":
    unittest.main()
