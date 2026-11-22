"""TJ-2770 and its review (TJ-3208 to TJ-3217): a sale of units an
`OPENING ... cost=unknown` line opens (a tainted pool) was realized at
cost 0 with nothing said at the end of `tjs run`, and the checklist's
missing-history step passed once the line covered the short.

The engine's record is computed at the cost the pool has (nil for the
opened units), marked `tainted`, and carries the OPENING row's own
fields (`unknown_cost_from`: account, symbol, date, signed quantity),
through the shared pool across taxable accounts, renames and option
deliveries; the run routes the row out of every total into the gains
file's manual_reporting_required section (CA-ACB-11), records the year's
rows in reports/run_summary.json (`unknown_cost_sales`, each with the
line it came by) and ends with one line naming the first row (symbol,
date, account) and the cost to add — the fix text, never the row, gives
way to the width. `tjs run --details` and the checklist's missing-history
step name the line (file and line) and the two ways to give it the cost
(the line's cost field, `tjs opening`), or a short's own sale to add; the
step fails as a question a DONE mark answers per row (account, symbol,
date, quantity, place, kind), so a sale added or changed later asks
again. A book with the cost given (a known-cost OPENING line, CA-OPEN-01)
is unchanged. Synthetic books, made-up tickers.

Formerly: test_fix_tj2770_opening_no_cost"""
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
from taxjson.rules import ids as R

from taxjson.lib import checklist as cl
from taxjson.lib import out
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction

OLD = 'OLD.TO'

_SETTINGS = {
    "canada": ('[settings]\nyear = 2025\ncountry = "canada"\n'
               'tax_date = "settle"\nbase_currency = "CAD"\n\n'),
    "usa": ('[settings]\nyear = 2025\ncountry = "usa"\ntax_date = "trade"\n'
            'base_currency = "USD"\nlocal_timezone = "America/New_York"\n\n'),
}
_SALE = "BUYSELL 2025-06-02 10:00:00 QZQ.TO -100 CAD 12.00 1200.00 0\n"
# The units held before the data at a cost the files do not have...
_UNKNOWN = "# synthetic\nOPENING 2024-12-20 QZQ.TO 100 cost=unknown\n" + _SALE
# ...and the same position with its cost given (a snapshot, CA-OPEN-01).
_GIVEN = "# synthetic\nOPENING 2024-12-20 QZQ.TO 100 CAD 1000\n" + _SALE

# What the run's closing list says (one line at the piped width).
WARNING = ("Warning: NOT in the totals: 1 sale at an unknown cost: "
           "QZQ.TO 2025-06-02 in margin — add the cost to its OPENING line")
# The checklist step's finding (the row first: its `! ` line keeps it).
FINDING = ("QZQ.TO 2025-06-02 (margin): 1 sale at an unknown cost, out of "
           "2025's totals — give its OPENING cost=unknown line, "
           "inputs/margin/trades.tt:2 (OPENING 2024-12-20 QZQ.TO in margin), "
           "the cost of the 100 QZQ.TO held before your files (`OPENING "
           "2024-12-20 QZQ.TO 100 CAD <total cost>`), or `tjs opening`")
SOURCE = {"account": "margin", "symbol": "QZQ.TO", "date": "2024-12-20",
          "quantity": 100.0, "where": "inputs/margin/trades.tt:2"}


def _tr(date, sym, qty, net, acct='b', action='BUYSELL'):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          time='10:00:00', symbol=sym, quantity=float(qty),
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=float(net), currency='CAD',
                          account=acct)


def _run(book):
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return CanadaTaxRules().compute_gains(book)


def _sales(res):
    return [(t['date'], t['cost'], t['gain'], t['proceeds'],
             bool(t.get('tainted')), t.get('unknown_cost_from'))
            for t in res['transactions'] if 'gain' in t]


def tjs(root, *args, **env_extra):
    """`tjs -C ROOT ARGS` as a person piping it (width 120: the run's
    console is its one-line form), or at the width `env_extra` sets
    (TAXJSON_WIDTH=0: captured, nothing wrapped)."""
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        capture_output=True, text=True, env=env(**env_extra),
        timeout=900, stdin=subprocess.DEVNULL)


def _project(files, country="canada"):
    """A project whose taxable accounts are `files`' keys, each with its
    .tt text (or {file name: text})."""
    root = Path(private_dir()) / "p"
    for acct, tt in files.items():
        (root / "inputs" / acct).mkdir(parents=True)
        for name, text in (tt.items() if isinstance(tt, dict)
                           else {"trades.tt": tt}.items()):
            (root / "inputs" / acct / name).write_text(text)
    (root / "taxjson.toml").write_text(
        _SETTINGS[country] + "".join(f"[accounts.{a}]\ntype = \"taxable\"\n"
                                     for a in files))
    return root


def _step(root, tc):
    """(the missing-history step's record, the exit code: 1 while a step
    needs attention, 0 when every step shown passed)."""
    r = tjs(root, "checklist", "--run", "--only", "missing-history",
            "--json", TAXJSON_WIDTH=0)
    tc.assertIn(r.returncode, (0, 1), r.stderr[-2000:])
    return (next(s for s in json.loads(r.stdout)["steps"]
                 if s["id"] == "missing-history"), r.returncode)


def _summary(root):
    return json.loads((root / "reports" / "run_summary.json").read_text())


def _gains(root, acct="margin"):
    return json.loads((root / "work" / f"{acct}_gains_wash.json").read_text())


def _closing(r):
    """The run's closing block's Warning lines about unknown costs."""
    return [ln for ln in r.stdout.splitlines()
            if ln.startswith("Warning: NOT in the totals:")
            and "unknown cost" in ln or "closing a short" in ln]


def _ran(tc, root):
    r = tjs(root, "run", "--no-input")
    tc.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
    tc.assertEqual(out.console_lint(r.stdout), [])
    return r


@rule(R.CA_ACB_11)
class TestTheEngine(unittest.TestCase):
    def test_the_sale_is_computed_at_the_pools_cost_and_marked(self):
        """The opening adds units and no cost: a plain sale's gain is its
        proceeds, the record marked tainted for the run to route out of
        the totals (never suppressed by the engine), naming the OPENING
        row it drew on (TJ-3209)."""
        src = {'account': 'b', 'symbol': OLD, 'date': '2024-12-20',
               'quantity': 100.0}
        res = _run([_tr('2024-12-20', OLD, 100, 0, action='OPENING_BALANCE'),
                    _tr('2025-06-02', OLD, -100, 1200)])
        self.assertEqual(_sales(res),
                         [('2025-06-02', 0.0, 1200.0, 1200.0, True, [src])])
        # A partial sale keeps the source; a position drained and
        # reopened with a purchase is clean.
        res = _run([_tr('2024-12-20', OLD, 100, 0, action='OPENING_BALANCE'),
                    _tr('2025-06-02', OLD, -50, 600),
                    _tr('2025-06-03', OLD, -50, 600),
                    _tr('2025-07-01', OLD, 10, 100),
                    _tr('2025-07-02', OLD, -10, 120)])
        self.assertEqual([(s[0], s[4], s[5]) for s in _sales(res)],
                         [('2025-06-02', True, [src]),
                          ('2025-06-03', True, [src]),
                          ('2025-07-02', False, None)])

    def test_the_entrys_book_carries_the_waiting_addition(self):
        """TJ-2770's book (TJ-2768's): the opening balance takes the
        denied 1000 (CA-SL-19), so the sale costs 1000 (+200) — still
        marked tainted: the opened units' own cost is unknown."""
        res = _run([_tr('2024-11-01', OLD, 100, 2000),
                    _tr('2024-12-03', OLD, -100, 1000),
                    _tr('2024-12-08', OLD, 100, 1000),
                    _tr('2024-12-13', OLD, -100, 1000),
                    _tr('2024-12-20', OLD, 100, 1000,
                        action='OPENING_BALANCE'),
                    _tr('2025-06-02', OLD, -100, 1200)])
        self.assertEqual(_sales(res)[-1][:5],
                         ('2025-06-02', 1000.0, 200.0, 1200.0, True))


@rule(R.CA_ACB_11)
class TestTheRunAndTheChecklist(unittest.TestCase):
    """The project with the cost=unknown line, run once."""

    @classmethod
    def setUpClass(cls):
        cls.root = _project({"margin": _UNKNOWN})
        cls.ran = tjs(cls.root, "run", "--no-input")
        if cls.ran.returncode != 0:
            raise AssertionError(cls.ran.stdout[-2000:]
                                 + cls.ran.stderr[-2000:])

    def test_the_closing_list_names_the_sale(self):
        lines = self.ran.stdout.splitlines()
        self.assertIn(WARNING, lines)
        self.assertEqual(out.console_lint(self.ran.stdout), [])
        # Out of the totals, routed to manual reporting (CA-ACB-11),
        # the row naming the OPENING row it drew on...
        g = _gains(self.root)
        self.assertEqual([t for t in g["transactions"] if "gain" in t], [])
        self.assertEqual([(t["date"], t["symbol"], t["qty"], t["proceeds"],
                           t["unknown_cost_from"])
                          for t in g["manual_reporting_required"]],
                         [("2025-06-02", "QZQ.TO", 100.0, 1200.0,
                           [{k: v for k, v in SOURCE.items()
                             if k != "where"}])])
        self.assertEqual(g["summary"]["total_gain"], 0.0)
        # ...and recorded for the checklist, with the line's place.
        self.assertEqual(_summary(self.root)["unknown_cost_sales"], [{
            "symbol": "QZQ.TO", "account": "margin", "date": "2025-06-02",
            "qty": 100.0, "proceeds": 1200.0, "currency": "CAD",
            "direction": "LONG", "kind": "sale", "sources": [SOURCE]}])

    def test_details_name_the_line_and_both_ways_to_add_the_cost(self):
        r = tjs(self.root, "run", "--no-input", "--details",
                TAXJSON_WIDTH=0)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        text = " ".join(r.stdout.split())
        self.assertIn("1 sale at an unknown cost in 2025, NOT in `tjs sum`: "
                      "QZQ.TO (margin) on 2025-06-02, 100 units. Give its "
                      "OPENING cost=unknown line, inputs/margin/trades.tt:2 "
                      "(OPENING 2024-12-20 QZQ.TO in margin), the cost of "
                      "the 100 QZQ.TO held before your files (`OPENING "
                      "2024-12-20 QZQ.TO 100 CAD <total cost>`), or `tjs "
                      "opening`; until then, report it by hand (`tjs "
                      "form-export` lists it).", text)

    def test_the_step_fails_a_done_mark_answers_it_and_a_new_sale_asks(self):
        s, rc = _step(self.root, self)
        self.assertEqual((s["status"], s["effective"], s["detail"], rc),
                         ("attention", "attention", FINDING, 1))
        self.assertTrue(s["next"])
        # The `! ` line keeps the row, and the Next line says both ways.
        r = tjs(self.root, "checklist", "--run", "--only", "missing-history")
        self.assertIn("! 10 missing-history: QZQ.TO 2025-06-02 (margin): "
                      "1 sale at an unknown cost", r.stdout)
        self.assertIn("give the OPENING cost=unknown line the cost (or "
                      "`tjs opening` from a positions report), then `tjs "
                      "run`; or report the sale by hand: `tjs checklist "
                      "--done missing-history`", " ".join(r.stdout.split()))
        # A question: the DONE mark answers this sale.
        r = tjs(self.root, "checklist", "--done", "missing-history",
                "--note", "reported by hand")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("it answers: QZQ.TO sold 2025-06-02 (margin), 100 "
                      "units", r.stdout + r.stderr)
        s, rc = _step(self.root, self)
        self.assertEqual((s["status"], s["effective"], s["override"], rc),
                         ("attention", "done", "done", 0))
        self.assertEqual(s["finding"], FINDING)
        # The sale split in two that day (a corrected export): the key
        # holds the quantity and the place, so both ask again (TJ-3215).
        (self.root / "inputs" / "margin" / "trades.tt").write_text(
            "# synthetic\nOPENING 2024-12-20 QZQ.TO 100 cost=unknown\n"
            "BUYSELL 2025-06-02 10:00:00 QZQ.TO -40 CAD 12.00 480.00 0\n"
            "BUYSELL 2025-06-02 11:00:00 QZQ.TO -60 CAD 12.00 720.00 0\n")
        r = _ran(self, self.root)
        # (the fix text gave way to the width; the rows never do)
        self.assertIn("Warning: NOT in the totals: 2 sales at an unknown "
                      "cost: QZQ.TO 2025-06-02 in margin and 1 more",
                      r.stdout.splitlines())
        s, rc = _step(self.root, self)
        self.assertEqual((s["effective"], rc), ("attention", 1))
        self.assertTrue(s["detail"].startswith(
            "QZQ.TO 2025-06-02 (margin) and 1 more: 2 sales at an unknown "
            "cost, out of 2025's totals — give its OPENING cost=unknown "
            "line, inputs/margin/trades.tt:2"))
        self.assertIn("not answered by the done mark of ", s["detail"])
        self.assertIn("QZQ.TO sold 2025-06-02 (margin), 40 units, QZQ.TO "
                      "sold 2025-06-02 (margin), 60 units, no. 2 that day",
                      s["detail"])


@rule(R.CA_ACB_11)
class TestTheSharedPool(unittest.TestCase):
    """TJ-3209, TJ-3212: aa opens 1500000 units at an unknown cost (and
    trades the symbol); bb buys 50 and sells them. The s.47 pool spans
    the taxable accounts, so bb's sale is at an unknown cost: named with
    aa's line, its quantity written whole."""

    def test_the_sale_names_the_other_accounts_line(self):
        root = _project({
            "aa": ("# synthetic\n"
                   "BUYSELL 2025-01-10 10:00:00 QZQ.TO 10 CAD 10.00 100.00 0\n"
                   "OPENING 2024-12-20 QZQ.TO 1500000 cost=unknown\n"),
            "bb": ("# synthetic\n"
                   "BUYSELL 2025-07-01 10:00:00 QZQ.TO 50 CAD 10.00 500.00 0\n"
                   "BUYSELL 2025-07-03 10:00:00 QZQ.TO -50 CAD 12.00 600.00 0\n")})
        r = _ran(self, root)
        self.assertIn("Warning: NOT in the totals: 1 sale at an unknown cost: "
                      "QZQ.TO 2025-07-03 in bb — add the cost to its OPENING "
                      "line", r.stdout.splitlines())
        rows = _summary(root)["unknown_cost_sales"]
        self.assertEqual([(x["account"], x["kind"], x["qty"], x["sources"])
                          for x in rows],
                         [("bb", "sale", 50.0, [{
                             "account": "aa", "symbol": "QZQ.TO",
                             "date": "2024-12-20", "quantity": 1500000.0,
                             "where": "inputs/aa/trades.tt:3"}])])
        s, _rc = _step(root, self)
        self.assertTrue(s["detail"].startswith(
            "QZQ.TO 2025-07-03 (bb): 1 sale at an unknown cost, out of "
            "2025's totals — give its OPENING cost=unknown line, "
            "inputs/aa/trades.tt:3 (OPENING 2024-12-20 QZQ.TO in aa), the "
            "cost of the 1500000 QZQ.TO held before your files (`OPENING "
            "2024-12-20 QZQ.TO 1500000 CAD <total cost>`), or `tjs "
            "opening`"), s["detail"])
        self.assertNotIn("e+06", s["detail"])
        # aa's own rows never go short, so find-missing-history calls
        # the line stale; the step never says to delete a line another
        # account's sale drew on (TJ-3249): one route, a question.
        self.assertNotIn("delete", s["detail"])
        self.assertNotIn("still opens its units", s["detail"])
        r = tjs(root, "checklist", "--done", "missing-history")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("it answers: QZQ.TO sold 2025-07-03 (bb), 50 units",
                      r.stdout + r.stderr)
        s, rc = _step(root, self)
        self.assertEqual((s["effective"], rc), ("done", 0))


def _b4_book():
    """The reviewer's b4: 100 NEW.TO bought; 100 OLD.TO moved in from
    account a (tainted by the move alone); OLD.TO renamed NEW.TO into
    that pool; 200 sold."""
    mv = TaxTransaction(action='TRANSFER', date='2025-02-01',
                        date_settle='2025-02-01', time='10:00:00',
                        symbol=OLD, quantity=100.0, price=0.0,
                        net_amount=0.0, currency='CAD', account='b',
                        type='lot_move',
                        description='own-account move #1: a -> b')
    rename = TaxTransaction(action='SPLIT', date='2025-03-01',
                            date_settle='2025-03-01', time='10:00:00',
                            symbol=OLD, symbol_new='NEW.TO', quantity=1.0,
                            net_amount=0.0, currency='CAD', account='b')
    return [_tr('2025-01-05', 'NEW.TO', 100, 1000), mv, rename,
            _tr('2025-06-02', 'NEW.TO', -200, 2400)]


def _tainted_causes(res):
    return [(t['own_move_from'], t.get('unknown_cost_from'))
            for t in res['transactions'] if 'gain' in t and t.get('tainted')]


@rule(R.CA_ACB_OWN_MOVE)
class TestAnOwnMoveThenARename(unittest.TestCase):
    """TJ-3235: a pool an own move alone tainted keeps that cause
    through a rename into an existing pool (the reviewer's b4 in the
    engine; P12 through the run): the record says the units came by an
    own move (`own_move_from`) and names no OPENING row, as before."""

    def test_the_engine_keeps_the_moves_cause(self):
        self.assertEqual(_tainted_causes(_run(_b4_book())), [(['a'], None)])


    _HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
             "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
             "Activity Type,Account #,Account Type\n")

    def test_the_move_stays_an_own_move(self):
        root = _project({
            "margin": {"questrade_2024.csv": self._HEAD
                       + "2024-03-04,2024-03-06,Buy,ZZB,ZZB MINES LTD,100,"
                       "10.00,-1000.00,-4.95,-1004.95,CAD,Trades,99900001,"
                       "Margin\n"
                       "2024-05-01,2024-05-01,TF6,ZZB,ZZB MINES LTD TFER TO "
                       "99900003,-50,0.00,0.00,0.00,0.00,CAD,Transfers,"
                       "99900001,Margin\n"},
            "margin2": {"questrade_2024.csv": self._HEAD
                        + "2024-03-05,2024-03-07,Buy,ZZC,ZZC MINES LTD,100,"
                        "20.00,-2000.00,-4.95,-2004.95,CAD,Trades,99900003,"
                        "Margin\n"
                        "2024-05-01,2024-05-01,TF6,ZZB,ZZB MINES LTD TRANSFER "
                        "BOOK VALUE 500.00,50,0.00,0.00,0.00,0.00,CAD,"
                        "Transfers,99900003,Margin\n"
                        "2024-10-01,2024-10-03,Sell,ZZC,ZZC MINES LTD,-150,"
                        "30.00,4500.00,-4.95,4495.05,CAD,Trades,99900003,"
                        "Margin\n",
                        "rename.tt": "# synthetic\n"
                        "SPLIT 2024-06-03 10:00:00 ZZB.TO ZZC.TO 1\n"}})
        (root / "taxjson.toml").write_text(
            (root / "taxjson.toml").read_text().replace("year = 2025",
                                                        "year = 2024"))
        r = _ran(self, root)
        self.assertNotIn("unknown cost", r.stdout)
        own = json.loads((root / "work" / "margin2_gains.json").read_text())
        self.assertEqual([(t["own_move_from"], t.get("unknown_cost_from"))
                          for t in own["manual_reporting_required"]],
                         [(["margin"], None)])
        self.assertIn("MOVED IN FROM YOUR OWN ACCOUNT",
                      (root / "reports" / "margin2.sum").read_text())
        self.assertEqual(_summary(root)["unknown_cost_sales"], [])


@rule(R.CA_ACB_11)
class TestOneLineWhateverTheRows(unittest.TestCase):
    """TJ-3208, TJ-3210, TJ-3214, TJ-3236: the closing line is one line
    and names the first row's symbol and date; the fix text, the
    phrase, the count of the others and the account give way."""

    def test_an_option_symbol_in_a_long_account_fits(self):
        root = _project({"questrade_margin_usd": (
            "# synthetic\nOPENING 2024-12-31 QZQ250620C00010000.TO 2 "
            "cost=unknown\n"
            "BUYSELL 2025-03-02 10:00:00 QZQ250620C00010000.TO -1 CAD 2.00 "
            "200.00 0\n"
            "BUYSELL 2025-04-02 10:00:00 QZQ250620C00010000.TO -1 CAD 2.00 "
            "200.00 0\n")})
        r = _ran(self, root)
        lines = r.stdout.splitlines()
        self.assertIn("Warning: NOT in the totals: 2 trades at an unknown "
                      "cost: QZQ250620C00010000.TO 2025-03-02 in "
                      "questrade_margin_usd", lines)
        self.assertTrue(all(len(ln) <= 120 for ln in lines))

    def test_a_long_account_name_keeps_the_row(self):
        root = _project({"margin_long_account_name": _UNKNOWN})
        r = _ran(self, root)
        self.assertIn("Warning: NOT in the totals: 1 sale at an unknown cost: "
                      "QZQ.TO 2025-06-02 in margin_long_account_name",
                      r.stdout.splitlines())

    def test_a_short_inside_the_files_is_not_one_sold_before_them(self):
        root = _project({"margin": (
            "# synthetic\nOPENING 2024-12-20 QZQ.TO 100 cost=unknown\n"
            "BUYSELL 2025-06-02 10:00:00 QZQ.TO -150 CAD 12.00 1800.00 0\n"
            "BUYSELL 2025-08-02 10:00:00 QZQ.TO 50 CAD 11.00 550.00 0\n")})
        r = _ran(self, root)
        lines = r.stdout.splitlines()
        self.assertIn("Warning: NOT in the totals: 2 trades at an unknown "
                      "cost: QZQ.TO 2025-06-02 in margin and 1 more", lines)
        self.assertEqual([(x["kind"], x["date"], x["qty"])
                          for x in _summary(root)["unknown_cost_sales"]],
                         [("sale", "2025-06-02", 100.0),
                          ("cover", "2025-08-02", 50.0)])
        s, _rc = _step(root, self)
        self.assertTrue(s["detail"].startswith(
            "QZQ.TO 2025-06-02 (margin) and 1 more: 1 sale at an unknown "
            "cost and 1 buy closing a short at an unknown cost, out of "
            "2025's totals — give its OPENING cost=unknown line"), s["detail"])
        self.assertNotIn("sold before your files", s["detail"])
        # Beside the walk's own finding (the 50 short read as a sale with
        # no purchase), Next still says what to do with these rows.
        r = tjs(root, "checklist", "--run", "--only", "missing-history")
        self.assertIn("! 10 missing-history: QZQ.TO 2025-06-02 (margin) and "
                      "1 more: 1 sale at an unknown", r.stdout)
        self.assertIn("fix what `tjs find-missing-history` lists, then: give "
                      "the OPENING cost=unknown line the cost",
                      " ".join(r.stdout.split()))


@rule(R.CA_ACB_11)
class TestAShortSoldBeforeTheFiles(unittest.TestCase):
    """TJ-3213: a negative line's cover: the short's own sale is what to
    add; Next and the key say so."""

    def test_the_cover_is_named_with_the_short_sale_to_add(self):
        root = _project({"margin": (
            "# synthetic\nOPENING 2024-12-20 QZQ.TO -100 cost=unknown\n"
            "BUYSELL 2025-03-01 10:00:00 QZQ.TO 100 CAD 9.00 900.00 0\n")})
        r = _ran(self, root)
        self.assertIn("Warning: NOT in the totals: 1 buy closing a short sold "
                      "before your files: QZQ.TO 2025-03-01 in margin",
                      r.stdout.splitlines())
        self.assertEqual([x["kind"] for x in _summary(root)["unknown_cost_sales"]],
                         ["cover-before"])
        s, _rc = _step(root, self)
        self.assertEqual(s["detail"], (
            "QZQ.TO 2025-03-01 (margin): 1 buy closing a short sold before "
            "your files, out of 2025's totals — the short's own sale is not "
            "in your files: add it as a .tt line in place of the OPENING "
            "cost=unknown line (inputs/margin/trades.tt:2: OPENING 2024-12-20 "
            "QZQ.TO in margin), or report the buy by hand (`tjs form-export`)"))
        r = tjs(root, "checklist", "--run", "--only", "missing-history")
        self.assertIn("add the short's own sale as a .tt line in place of the "
                      "OPENING cost=unknown line, then `tjs run`; or report "
                      "the buy by hand: `tjs checklist --done missing-history`",
                      " ".join(r.stdout.split()))
        r = tjs(root, "checklist", "--done", "missing-history")
        self.assertIn("it answers: QZQ.TO bought 2025-03-01 (margin), 100 "
                      "units", r.stdout + r.stderr)


@rule(R.CA_ACB_11)
class TestAnExercisedOptionAtAnUnknownCost(unittest.TestCase):
    """TJ-3237 (the reviewer's P10b): a call opened at an unknown cost,
    exercised into 100 shares, the shares sold. The step names the
    call's line with the cost to add and nothing else — never "remove
    it", whatever find-missing-history's heuristic says — and asks the
    question, so `--done` records."""

    def test_one_route_and_the_question(self):
        root = _project({"margin": (
            "# synthetic\nOPENING 2024-12-31 QZQ250620C00010000.TO 1 "
            "cost=unknown\n"
            "ASSIGN 2025-06-10 16:00:00 QZQ250620C00010000.TO -1 CAD 0 0 0\n"
            "ASSIGN 2025-06-10 16:01:00 QZQ.TO 100 CAD 10.00 1000.00 0\n"
            "BUYSELL 2025-07-02 10:00:00 QZQ.TO -100 CAD 12.00 1200.00 0\n")})
        r = _ran(self, root)
        self.assertIn("Warning: NOT in the totals: 1 sale at an unknown cost: "
                      "QZQ.TO 2025-07-02 in margin — add the cost to its "
                      "OPENING line", r.stdout.splitlines())
        s, rc = _step(root, self)
        self.assertEqual(s["detail"], (
            "QZQ.TO 2025-07-02 (margin): 1 sale at an unknown cost, out of "
            "2025's totals — give its OPENING cost=unknown line, "
            "inputs/margin/trades.tt:2 (OPENING 2024-12-31 "
            "QZQ250620C00010000.TO in margin), the cost of the 1 "
            "QZQ250620C00010000.TO held before your files (`OPENING "
            "2024-12-31 QZQ250620C00010000.TO 1 CAD <total cost>`), or "
            "`tjs opening`"))
        self.assertNotIn("remove", s["detail"])
        r = tjs(root, "checklist", "--done", "missing-history")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("it answers: QZQ.TO sold 2025-07-02 (margin), 100 units",
                      r.stdout + r.stderr)
        s, rc = _step(root, self)
        self.assertEqual((s["effective"], rc), ("done", 0))


@rule(R.CA_ACB_11)
class TestQuantitiesAndNoCountry(unittest.TestCase):
    def test_a_fractional_quantity_is_written_whole(self):
        """TJ-3212: the line to copy carries the quantity as written."""
        root = _project({"margin": (
            "# synthetic\nOPENING 2024-12-20 QZQ.TO 0.123456789 cost=unknown\n"
            "BUYSELL 2025-06-02 10:00:00 QZQ.TO -0.123456789 CAD 12.00 1.48 0\n")})
        _ran(self, root)
        s, _rc = _step(root, self)
        self.assertIn("`OPENING 2024-12-20 QZQ.TO 0.123456789 CAD <total "
                      "cost>`", s["detail"])
        self.assertNotIn("0.123457", s["detail"])

    def test_done_without_a_country_records_the_mark(self):
        """TJ-3211: the keys come from the stored list, no country
        needed; a year that cannot be read gives none."""
        root = Path(private_dir()) / "p"
        (root / "reports").mkdir(parents=True)
        (root / "reports" / "run_summary.json").write_text(json.dumps({
            "year": 2025, "unknown_cost_sales": [{
                "symbol": "QZQ.TO", "account": "margin", "date": "2025-06-02",
                "qty": 100.0, "kind": "sale", "sources": []}]}))
        self.assertEqual(
            cl.question_answers(root, {"settings": {"year": 2025}},
                                "missing-history"),
            ["margin|QZQ.TO|2025-06-02|100|0|sale"])
        self.assertEqual(cl.question_answers(root, {"settings": {}},
                                             "missing-history"), [])
        self.assertEqual(cl.question_answers(root, {"settings": {"year": 2024}},
                                             "missing-history"), [])


@rule(R.US_BASIS_04)
class TestAUSProject(unittest.TestCase):
    """The US engine's lot carries the row too: the run names the sale
    and the line."""

    def test_an_own_move_alone_keeps_its_cause_through_a_rename(self):
        """TJ-3235's b4 in the US engine: the moved lot keeps its
        `moved_from`; no OPENING row named (as Canada)."""
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res = USATaxRules().compute_gains(_b4_book(),
                                              per_account_basis=True)
        self.assertEqual(_tainted_causes(res), [(['a'], None)])

    def test_the_sale_is_named_with_its_line(self):
        root = _project({"margin": (
            "# synthetic\nOPENING 2024-12-20 QZQ.US 100 cost=unknown\n"
            "BUYSELL 2025-06-02 10:00:00 QZQ.US -100 USD 12.00 1200.00 0\n")},
            country="usa")
        r = _ran(self, root)
        self.assertIn("Warning: NOT in the totals: 1 sale at an unknown cost: "
                      "QZQ.US 2025-06-02 in margin — add the cost to its "
                      "OPENING line", r.stdout.splitlines())
        rows = _summary(root)["unknown_cost_sales"]
        self.assertEqual([(x["kind"], x["sources"]) for x in rows],
                         [("sale", [{"account": "margin", "symbol": "QZQ.US",
                                     "date": "2024-12-20", "quantity": 100.0,
                                     "where": "inputs/margin/trades.tt:2"}])])
        s, rc = _step(root, self)
        self.assertEqual((s["status"], rc), ("attention", 1))
        self.assertIn("`OPENING 2024-12-20 QZQ.US 100 USD <total cost>`",
                      s["detail"])

    def test_the_shares_an_exercised_call_delivers_name_its_line(self):
        """TJ-3238: the US lot the exercise creates keeps the call lot's
        `unknown_from`, so the share sale names the call's line as
        Canada does."""
        root = _project({"margin": (
            "# synthetic\nOPENING 2024-12-31 QZQ250620C00010000.US 1 "
            "cost=unknown\n"
            "ASSIGN 2025-06-10 16:00:00 QZQ250620C00010000.US -1 USD 0 0 0\n"
            "ASSIGN 2025-06-10 16:01:00 QZQ.US 100 USD 10.00 1000.00 0\n"
            "BUYSELL 2025-07-02 10:00:00 QZQ.US -100 USD 12.00 1200.00 0\n")},
            country="usa")
        r = _ran(self, root)
        self.assertIn("Warning: NOT in the totals: 1 sale at an unknown cost: "
                      "QZQ.US 2025-07-02 in margin — add the cost to its "
                      "OPENING line", r.stdout.splitlines())
        self.assertEqual(
            [(x["kind"], [(src["symbol"], src["where"]) for src in x["sources"]])
             for x in _summary(root)["unknown_cost_sales"]],
            [("sale", [("QZQ250620C00010000.US", "inputs/margin/trades.tt:2")])])


@rule(R.CA_OPEN_01)
class TestTheCostGiven(unittest.TestCase):
    """The same book with the OPENING line's cost: a snapshot that sets
    the position and its cost — the sale is in the totals, nothing is
    said, the step passes."""

    def test_unchanged(self):
        root = _project({"margin": _GIVEN})
        r = tjs(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertNotIn("unknown cost", r.stdout + r.stderr)
        self.assertNotIn("Before you trust", r.stdout)
        g = _gains(root)
        self.assertEqual([(t["date"], t["cost"], t["gain"])
                          for t in g["transactions"] if "gain" in t],
                         [("2025-06-02", 1000.0, 200.0)])
        self.assertEqual(g.get("manual_reporting_required", []), [])
        self.assertEqual(_summary(root)["unknown_cost_sales"], [])
        s, rc = _step(root, self)
        self.assertEqual((s["status"], s["detail"], rc),
                         ("done", "nothing affects the year", 0))


if __name__ == "__main__":
    unittest.main()
