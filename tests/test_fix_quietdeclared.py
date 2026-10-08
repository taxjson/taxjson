"""Owner-reported noise: an answer the user already gave is not a warning.

1. A spin-off whose election writes `fmv_per_share=0` (a warrant
   distributed at no value) is the user's declared $0 cost: an Info line
   with the event id and how to change it — never the run's "$0 cost"
   Warning, find-missing-history's "$0-COST SHARES STILL HELD" section,
   the checklist's elections ATTENTION or quick-start's. A $0 the broker
   booked with no value saved stays a Warning. Both countries.
2. RBC's / Questrade's look-alike rename hint is not printed when a .tt
   `RENAME <date> OLD NEW` line (or a dated ticker.map line) already
   declares the change: a note names the line instead.
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from _style import CapturedWidth

REPO_ROOT = Path(__file__).resolve().parent.parent

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


_CONFIG = """\
[settings]
year = {year}
country = "{country}"
{province}base_currency = "{cur}"
source_currencies = []
tax_date = "{tax_date}"

[accounts.margin]
type = "taxable"
"""


def _config(country="canada", year=2026):
    if country == "canada":
        return _CONFIG.format(year=year, country=country,
                              province='province = "ON"\n', cur="CAD",
                              tax_date="settle")
    return _CONFIG.format(year=year, country=country, province="",
                          cur="USD", tax_date="trade")


def _project(tmp, files, country="canada", year=2026, ticker_map=None):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_config(country, year))
    d = root / "inputs" / "margin"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body)
    if ticker_map is not None:
        (root / "ticker.map").write_text(ticker_map)
    return root


def _manifest(root, records):
    p = root / "inputs" / "margin" / "manifest.json"
    p.write_text(json.dumps({"elections": records}))
    return p


# ------------------------------------------------------------------ 1. $0

_QT_HEAD = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Activity Type,Account #,Account Type\n')
_QT_BUY = ('2025-06-02 12:00:00 AM,2025-06-03 12:00:00 AM,Buy,AAA,'
           'ALPHA CORP,1000,10.00,-10000.00,0,-10000.00,CAD,Trades,'
           '55500001,Margin\n')  # pii-ok
_QT_DIS = ('2026-01-27 12:00:00 AM,2026-01-27 12:00:00 AM,DIS,AAAW,'
           'WTS ALPHA CORP WT SPINOFF ON 1000 SHS FROM SEC# J000001 '
           'ALPHA CORP REC 01/20/26 PAY 01/27/26,100,0,0,0,0,CAD,'
           'Dividends,55500001,Margin\n')  # pii-ok


class TestDeclaredZeroValueRun(unittest.TestCase):
    """End to end (Canada, Questrade warrants spun off at no value)."""

    def test_declared_zero_is_an_info_and_a_missing_value_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"questrade_all.csv":
                                  _QT_HEAD + _QT_BUY + _QT_DIS})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
            doc = json.loads((root / "work" / "pending_elections.json")
                             .read_text())
            eid = doc["accounts"]["margin"]["pending"][0]["event_id"]
            r = _run_cli(root, "elect", "margin", "--set",
                         f"{eid}=taxable_deemed_dividend", "--hint",
                         "fmv_per_share=0")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            said = r.stdout + r.stderr
            self.assertIn("the $0 value you declared", said)
            self.assertNotIn("warning", said.lower())
            self.assertIn(f"--redo --event {eid}", said)

            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            out = r.stdout + r.stderr
            self.assertNotIn("is booked at $0 —", out)
            self.assertNotIn("at a $0 cost (", out)
            self.assertIn("the $0 value you declared", out)
            self.assertIn(f"taxjson elect margin --redo --event {eid}", out)
            summ = json.loads((root / "reports" / "run_summary.json")
                              .read_text())
            self.assertEqual(summ["zero_cost_held"], [])
            self.assertEqual(summ["zero_cost_sold"], [])
            self.assertEqual([d["events"] for d in
                              summ["zero_cost_declared"]], [[eid]])
            diag = (root / "work" / "margin_corp_spinoff_value.diag")
            self.assertNotIn("warning:", diag.read_text())
            self.assertIn("note:", diag.read_text())
            # quick-start / checklist read these: nothing to attend to.
            from taxjson.lib import checklist as cl
            ctx = type("Ctx", (), {"cache": root / "work"})()
            self.assertEqual(cl._zero_value_elections(ctx), 0)

            r = _run_cli(root, "find-missing-history")
            self.assertNotIn("$0-COST SHARES STILL HELD", r.stdout)
            self.assertIn("DECLARED $0 COST", r.stdout)
            self.assertIn(eid, r.stdout)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

            r = _run_cli(root, "spinoffs")
            self.assertNotIn("ZERO-VALUE", r.stdout)
            self.assertIn("the $0 value you declared", r.stdout)

            # The same election with no value saved (a hand-edited or
            # legacy manifest): a Warning, as before.
            man = root / "inputs" / "margin" / "manifest.json"
            m = json.loads(man.read_text())
            m["elections"][eid]["hints"] = {}
            man.write_text(json.dumps(m))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            out = r.stdout + r.stderr
            self.assertIn("is booked at $0", out)
            self.assertIn("at a $0 cost (", out)
            self.assertNotIn("the $0 value you declared", out)
            summ = json.loads((root / "reports" / "run_summary.json")
                              .read_text())
            self.assertEqual(len(summ["zero_cost_held"]), 1)
            self.assertEqual(summ["zero_cost_declared"], [])


def _tx(**kw):
    from taxjson.lib.core import TaxTransaction
    base = dict(action="BUYSELL", date="2026-01-27", symbol="SPW.US",
                quantity=100.0, currency="USD", price=0.0, net_amount=0.0,
                account="margin")
    base.update(kw)
    return TaxTransaction(**base)


class TestDeclaredZeroValueBothCountries(unittest.TestCase):
    """The provenance test and every reader, per country's FMV spin-off
    election (Canada's deemed dividend, the US §301 distribution)."""

    ELECTIONS = (("canada", "taxable_deemed_dividend"),
                 ("usa", "taxable_distribution_301"))

    def test_declares_zero_value(self):
        from taxjson.lib.corp_actions import declares_zero_value
        for _country, el in self.ELECTIONS:
            self.assertTrue(declares_zero_value(el, {"fmv_per_share": 0.0}))
            self.assertFalse(declares_zero_value(el, {}))
            self.assertFalse(declares_zero_value(el, {"fmv_per_share": 2}))
        # A merger's 0 is still the deferred value (a fake loss).
        self.assertFalse(declares_zero_value("taxable_disposition",
                                             {"fmv_per_share": 0.0}))

    def test_project_events_and_walk(self):
        from taxjson.lib.corp_actions import declared_zero_value_events
        from taxjson.lib import first_run as FR
        for country, el in self.ELECTIONS:
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, {}, country=country)
                _manifest(root, {
                    "ev-declared": {"summary": "s", "election": el,
                                    "hints": {"fmv_per_share": 0.0}},
                    "ev-missing": {"summary": "s", "election": el,
                                   "hints": {}}})
                decl = declared_zero_value_events(root)
                self.assertEqual(decl, {("margin", "ev-declared")})
                txs = [_tx(symbol="SPW.US", corp_event_id="ev-declared",
                           corp_election=el),
                       _tx(symbol="SPX.US", corp_event_id="ev-missing",
                           corp_election=el),
                       # A broker row at $0, no election at all.
                       _tx(symbol="SPY.US")]
                sold, held = FR.zero_cost_positions(
                    txs, 2026, sheltered={"margin": False},
                    country=country, declared=decl)
                self.assertEqual(sorted(r.symbol for r in held),
                                 ["SPX.US", "SPY.US"])
                rows = FR._zero_cost_rows(
                    txs, 2026, sheltered={"margin": False},
                    country=country, declared=decl)
                self.assertEqual(FR.declared_zero_cost(rows), [
                    {"symbol": "SPW.US", "account": "margin",
                     "events": ["ev-declared"], "quantity": 100.0}])
                # A pool that also holds an undeclared $0 lot warns.
                mixed = txs[:1] + [_tx(symbol="SPW.US", date="2026-02-02")]
                _s, held = FR.zero_cost_positions(
                    mixed, 2026, sheltered={"margin": False},
                    country=country, declared=decl)
                self.assertEqual([r.symbol for r in held], ["SPW.US"])

    def test_summary_render(self):
        from taxjson.lib import first_run as FR
        doc = {"year": 2026, "zero_cost_declared": [
            {"symbol": "SPW.US", "account": "margin",
             "events": ["ev-1"], "quantity": 100.0}]}
        self.assertFalse(FR.is_clean(doc))
        text = "\n".join(FR.render(doc, width_=0))
        self.assertIn("Info: 1 position at the $0 cost you declared", text)
        self.assertIn("SPW.US (margin, event ev-1)", text)
        self.assertIn("`taxjson elect margin --redo --event ev-1`", text)
        self.assertNotIn("Warning", text)

    def test_run_warning_split(self):
        from taxjson.bin.taxjson_run import _warn_zero_value_spinoffs
        for country, el in self.ELECTIONS:
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp)
                # (the run says each note once: one symbol per country)
                rows = [{"action": "DIVIDEND", "date": "2026-01-27",
                         "symbol": f"{s}{country[:2]}", "net_amount": 0.0,
                         "corp_event_id": e, "corp_election": el}
                        for s, e in (("SPW.US", "ev-declared"),
                                     ("SPX.US", "ev-missing"))]
                corp = cache / "margin_x_corp.json"
                corp.write_text(json.dumps({"transactions": rows}))
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    _warn_zero_value_spinoffs("margin", True, [corp],
                                              cache, {"ev-declared"})
                diag = (cache / "margin_corp_spinoff_value.diag"
                        ).read_text().splitlines()
                self.assertEqual(
                    [ln.split(":")[0] for ln in diag], ["warning", "note"])
                self.assertIn("ev-missing", diag[0])
                self.assertIn("ev-declared", diag[1])
                self.assertIn("--redo --event ev-declared", diag[1])
                self.assertNotIn("ev-declared", err.getvalue())
                self.assertIn("ev-declared", out.getvalue())


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------- 2. a rename already declared

def _rbc_rename_files():
    """RBC's look-alike ticker change (test_fix_rbc.RQA_ROWS), in CAD:
    RQA stops with shares open, RQB's first row sells them."""
    from test_fix_rbc import HDR, row
    name = "NORTHWIND CAPITAL CORPORATION COMMON STOCK"
    rows = [row("September 6, 2023", "Sell", "RQB", name, "-1170", "15",
                "17540.05", "CAD", "NORTHWIND CAPITAL CORP UNSOLICITED CA"),
            row("May 30, 2023", "Buy", "RQA", name, "450", "13",
                "-5859.95", "CAD", "SOUTHWIND CAPITAL CORP UNSOLICITED DA"),
            row("April 11, 2023", "Buy", "RQA", name, "720", "13",
                "-9369.95", "CAD", "SOUTHWIND CAPITAL CORP UNSOLICITED DA")]
    return {"rbc.csv": HDR + "".join(rows)}


class TestDeclaredRenameQuietsLookAlikeHint(unittest.TestCase):
    """RBC's (and Questrade's) look-alike rename hint is answered by a .tt
    RENAME line: the parse says a note naming the line, not the
    ATTENTION: nothing on the run console, a note in the .sum."""

    def _run(self, files, tt=None, ticker_map=""):
        from test_fix_a2_rbcqt import _project, _cli_run
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", 2023, files, ticker_map=ticker_map)
            if tt is not None:
                (p / "inputs" / "margin" / "renames.tt").write_text(tt)
            r = _cli_run(p, "run", "--no-input")
            sums = "".join(f.read_text() for f in
                           (p / "reports").rglob("*.sum"))
            diags = "".join(f.read_text() for f in
                            (p / "work").glob("*.diag"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout + r.stderr, sums, diags

    def test_rbc_hint_answered_by_tt_rename(self):
        files = _rbc_rename_files()
        out, _sums, _d = self._run(files)
        self.assertIn("looks renamed", out)       # the hint, unanswered
        tt = "# the ticker change\nRENAME 2023-08-01 RQA.TO RQB.TO\n"
        out, sums, diags = self._run(files, tt)
        for text in (out, sums, diags):
            self.assertNotIn("looks renamed", text)
            self.assertNotIn("ATTENTION: rbc.csv", text)
        # The .sum keeps the answer as a note; the console stays quiet.
        self.assertNotIn("answered by", out)
        self.assertIn("answered by inputs/margin/renames.tt:2", sums)

    def test_rbc_hint_answered_by_dated_map_line(self):
        out, sums, _d = self._run(
            _rbc_rename_files(),
            ticker_map="RENAME RQA.TO RQB.TO 2023-08-01\n")
        self.assertNotIn("looks renamed", out)
        self.assertNotIn("looks renamed", sums)

    def test_questrade_hint_answered_by_tt_rename(self):
        from test_fix_rbcqt import QH, q
        body = (q(td="2023-03-03", sd="2023-03-07", sym="QQOL",
                  desc="QQ HOLDINGS CORP WE ACTED AS AGENT", qty="500",
                  price="10", gross="-5000", comm="0", net="-5000",
                  cur="CAD")
                + q(td="2023-09-11", sd="2023-09-13", action="Sell",
                    sym="QQNW", desc="QQ HOLDINGS CORP WE ACTED AS AGENT",
                    qty="-500", price="20", gross="10000", comm="0",
                    net="10000", cur="CAD"))
        files = {"questrade_2023.csv": QH + body}
        out, _s, _d = self._run(files)
        self.assertIn("looks renamed", out)
        out, sums, _d = self._run(files,
                                  "RENAME 2023-09-01 QQOL.TO QQNW.TO\n")
        self.assertNotIn("looks renamed", out)
        self.assertNotIn("looks renamed", sums)
        self.assertIn("answered by inputs/margin/renames.tt:1", sums)


class TestBrokerageDeclaredRenames(unittest.TestCase):
    """taxjson-brokerage --declared-renames (what `taxjson run` passes):
    each parser's look-alike hint for a declared pair is a note naming
    the line; another pair, or --lint, keeps the hint."""

    def test_each_parser(self):
        from test_fix_def_rest import (_brokerage, QH, QT_RENAME,
                                       WB_RENAME)
        cases = (("questrade", {"q.csv": QH + QT_RENAME},
                  "QQOL.US\tQQNW.US", "looks renamed"),
                 ("rbc", _rbc_rename_files(), "RQA.TO\tRQB.TO",
                  "looks renamed"),
                 ("webull", {"wb.csv": WB_RENAME}, "QQOL.US\tQQNW.US",
                  "ticker change Webull"))
        with tempfile.TemporaryDirectory() as d:
            for brk, files, pair, hint in cases:
                with self.subTest(broker=brk):
                    f = Path(d) / f"{brk}.list"
                    f.write_text(f"{pair}\tinputs/margin/renames.tt:4\n")
                    rc, err = _brokerage(brk, files, None,
                                         "--declared-renames", str(f))
                    self.assertEqual(rc, 0, err)
                    self.assertNotIn(hint, err)
                    self.assertIn("answered by inputs/margin/renames.tt:4",
                                  err)
                    rc, err = _brokerage(brk, files, None, "--lint",
                                         "--declared-renames", str(f))
                    self.assertIn(hint, err)
                    f.write_text("QZX.US\tQZY.US\tinputs/margin/r.tt:1\n")
                    rc, err = _brokerage(brk, files, None,
                                         "--declared-renames", str(f))
                    self.assertIn(hint, err)
