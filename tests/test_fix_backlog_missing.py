"""Backlog group `missing` (2026-10-25): find-missing-history, its drafts
and transfers.

TJ-0008  a transfer row stating a book value of 0 was drafted as a $0.00
         purchase; filled in with its date only it booked a $0 lot. The
         line now leaves the cost as COST (the run refuses it until it is
         filled in), and a row with no id no longer says "(row ?)".
TJ-0009  a transfer-in the run reports PARTLY covered by .tt purchases
         was "not drafted: ... a line would count it twice": the other
         units are drafted now, at COST, with a CHECK.
TJ-0017  a grant-timed written option sold with no purchase in the
         files was called "NOT in the totals" while its premium was.
TJ-0018  one draft line per sale that went short: more than the deepest
         short when a purchase in the files came between two of them.
TJ-0043  `tjs transfers`' `! ` lines were over 100 columns and wrapped.
TJ-0044  the run's closing list named one $0 lot twice (part sold, part
         held): "2 positions at a $0 cost (SAMPK.TO, SAMPK.TO)".
TJ-0055  `tjs transfers` TYPE showed the broker's free text (with the
         other account's number) instead of a transfer type.
TJ-0074  the drafts' slower own-move tier paired a leg of an in-kind
         move (to a registered account) with an outside arrival.
TJ-0077  find-missing-history, sanity and transfers said nothing about
         books older than the inputs, and --write-missing-history wrote
         an OPENING line for a position a just-added purchase fixes.
TJ-0175  the --write-missing-history note was one unwrapped line.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from _style import CapturedWidth
from test_fix_ibparse import HEAD, TRADES_H, _parse_ib

from taxjson.lib.core import TaxTransaction
from taxjson.lib import first_run as FR
from taxjson.lib.missing_history import (COST_PLACEHOLDER, draft_purchases,
                                          format_purchase_drafts)

REPO = Path(__file__).resolve().parent.parent
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root, *args, width=None):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", NO_COLOR="1",
               TAXJSON_LOCAL_TZ="America/Toronto")
    if width is not None:
        env["TAXJSON_WIDTH"] = str(width)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=300)


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type")


def _qt(date, action, sym, desc, qty, price, gross, comm, net,
        kind="Trades", acct="99900001"):
    return (f"{date},{date},{action},{sym},{desc},{qty},{price},{gross},"
            f"{comm},{net},CAD,{kind},{acct},Margin")


_TOML = """\
[settings]
country = "canada"
local_timezone = "America/Toronto"
tax_date = "settle"
year = {year}
base_currency = "CAD"
source_currencies = []
{extra}
[accounts.margin]
type = "taxable"
"""


def _project(tmp, name, rows, *, year=2024, extra="", files=None):
    root = Path(tmp) / name
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_TOML.format(year=year, extra=extra))
    if rows:
        (root / "inputs" / "margin" / "activity.csv").write_text(
            "\n".join([_QT_HEAD] + rows) + "\n")
    for fname, text in (files or {}).items():
        (root / "inputs" / "margin" / fname).write_text(text)
    return root


def _transfer(sym, d, q, desc, acct="margin", **kw):
    return dict(action="TRANSFER", date=d, symbol=sym, quantity=float(q),
                currency="CAD", account=acct, description=desc, **kw)


# ------------------------------------------------------------- the drafts
class TestTransferDrafts(unittest.TestCase):

    def test_tj0008_a_stated_zero_leaves_the_cost_to_fill_in(self):
        sale = TaxTransaction(action="BUYSELL", date="2024-09-10",
                              symbol="SAMPK.TO", quantity=-10,
                              net_amount=115.05, currency="CAD",
                              account="margin")
        rows = [_transfer("SAMPK.TO", "2024-06-03", 40,
                          "SAMPLE K CORP TRANSFER BOOK VALUE 0.00",
                          book_value=0.0)]
        drafts, gaps = draft_purchases([sale], country="canada",
                                       transfer_rows=rows)
        d, = drafts
        self.assertIsNone(d.cost)
        line = d.tt_line().split()
        self.assertEqual(line[6:8], [COST_PLACEHOLDER, COST_PLACEHOLDER])
        notes = " ".join(d.comments)
        self.assertIn("book value of 0", notes)
        self.assertNotIn("(row ?)", notes)
        # (a stated 0 is no average cost: that note is left out)
        self.assertNotIn("average cost", notes)
        self.assertEqual(gaps, [])
        # A stated book value is still the line's cost.
        rows[0]["description"] = "SAMPLE K CORP TRANSFER BOOK VALUE 600.00"
        d, = draft_purchases([sale], country="canada",
                             transfer_rows=rows)[0]
        self.assertEqual(d.cost, 600.0)

    def test_tj0009_a_partly_covered_arrival_drafts_the_other_units(self):
        tt = TaxTransaction(action="BUYSELL", date="2021-03-15",
                            symbol="SAMPL.TO", quantity=10, net_amount=150,
                            currency="CAD", account="margin",
                            source="old.tt")
        sale = TaxTransaction(action="BUYSELL", date="2024-10-01",
                              symbol="SAMPL.TO", quantity=-5,
                              net_amount=120.05, currency="CAD",
                              account="margin")
        rows = [_transfer("SAMPL.TO", "2024-06-03", 20,
                          "SAMPLE L CORP TRANSFER BOOK VALUE 400.00",
                          book_value=400.0)]
        drafts, gaps = draft_purchases([tt, sale], country="canada",
                                       transfer_rows=rows)
        self.assertEqual(gaps, [])
        d, = drafts
        self.assertEqual((d.quantity, d.cost, d.warn), (10.0, None, True))
        notes = " ".join(d.comments)
        self.assertIn("already hold 10 of these 20 units", notes)
        self.assertNotIn("count it twice", notes)
        text = format_purchase_drafts(drafts, gaps, country="canada",
                                      account="margin")
        self.assertIn("SAMPL.TO  10  CAD  COST  COST", text)

    def test_tj0074_an_in_kind_leg_is_no_slower_own_move(self):
        # margin's transfer-out went to the RRSP (an in-kind move, not
        # booked); qb's arrival 15 days later is from outside.
        sale = TaxTransaction(action="BUYSELL", date="2024-06-03",
                              symbol="ZZQ.TO", quantity=-50,
                              net_amount=145.05, currency="CAD",
                              account="qb")
        rows = [_transfer("ZZQ.TO", "2024-03-01", -50,
                          "ZZQ CORP TFER TO 99900002"),
                _transfer("ZZQ.TO", "2024-03-16", 50,
                          "ZZQ CORP TRANSFER BOOK VALUE 0.00", acct="qb",
                          book_value=0.0)]
        drafts, gaps = draft_purchases([sale], country="canada",
                                       transfer_rows=rows)
        self.assertEqual(drafts, [])
        self.assertIn("a move from your account margin",
                      " ".join(g.reason for g in gaps))
        drafts, gaps = draft_purchases(
            [sale], country="canada", transfer_rows=rows,
            in_kind_legs={("margin", "ZZQ.TO", "2024-03-01", -50.0)})
        self.assertEqual([(d.account, d.quantity) for d in drafts],
                         [("qb", 50.0)])
        self.assertNotIn("move from your account",
                         " ".join(g.reason for g in gaps))


def _ib(sym, when, qty, price, basis, code):
    proceeds = -qty * price
    return (f'Trades,Data,Order,Stocks,CAD,U1234567,{sym},"{when}",{qty},'
            f'{price},0,{proceeds},-1,{basis},0,0,{code}\n')


class TestIbDraftsCapAtTheDeepestShort(unittest.TestCase):

    def test_tj0018_a_sale_of_a_purchase_in_the_files_gets_no_line(self):
        # Ten shares held before the data are sold at 09:00 (IB code C,
        # Basis 250); bought again at 15:00; that purchase is sold in
        # September (code C, its Basis 281). Ten units are missing.
        _, rows, _ = _parse_ib(
            HEAD + TRADES_H
            + _ib("ZZS", "2024-06-03, 09:00:00", -10, 30, -250, "C")
            + _ib("ZZS", "2024-06-03, 15:00:00", 10, 28, 281, "O")
            + _ib("ZZS", "2024-09-03, 10:00:00", -10, 40, -281, "C"))
        keys = TaxTransaction.__dataclass_fields__
        txs = [TaxTransaction(**{k: v for k, v in dict(r, account="margin")
                                 .items() if k in keys}) for r in rows]
        drafts, gaps = draft_purchases(txs, country="canada")
        self.assertEqual([(d.quantity, d.cost, d.sale_date) for d in drafts],
                         [(10.0, 250.0, "2024-06-03")])
        self.assertEqual(gaps, [])


# ------------------------------------------------- what the gains booked
class TestGrantTimedWrites(unittest.TestCase):

    def test_tj0017_a_write_booked_at_grant_is_in_the_totals(self):
        opt = "ZZC241220C00040000.TO"
        recs = [
            {"symbol": opt, "direction": "SHORT", "grant": True, "qty": 1.0,
             "id": "w1", "date": "2024-06-03", "date_settle": "2024-06-04"},
            {"symbol": opt, "direction": "SHORT", "grant": False,
             "qty": 1.0, "id": "b1", "date": "2024-06-03",
             "date_settle": "2024-06-04"},
            {"symbol": opt, "direction": "SHORT", "grant": True, "qty": 1.0,
             "id": "w2", "date": "2024-09-03", "date_settle": "2024-09-04"}]
        row = SimpleNamespace(
            candidate=SimpleNamespace(symbol=opt, account="margin"),
            in_year_short_sales=[("w1", 1.0, 1.0), ("w2", 1.0, 1.0)])
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "margin_gains.json").write_text(
                json.dumps({"transactions": recs}))
            got = FR.engine_booking(work, [row], 2024)
            self.assertEqual(got[(opt, "margin")], FR.BOOKED_SHORT_COVER)
            # Not booked at grant (no grant records): the buy closed one
            # write, the other is an open short at the year's end.
            (work / "margin_gains.json").write_text(
                json.dumps({"transactions": [recs[1]]}))
            got = FR.engine_booking(work, [row], 2024)
            self.assertEqual(got[(opt, "margin")], FR.BOOKED_OPEN)

    def test_tj0017_through_the_run(self):
        fii = ("Financial Instrument Information,Header,Asset Category,"
               "Symbol,Description,Conid,Security ID,Underlying,Listing "
               "Exch,Multiplier,Expiry,Delivery Month,Type,Strike,Code\n"
               "Financial Instrument Information,Data,Equity and Index "
               "Options,ZZC   241220C00040000,ZZC 20DEC24 40 C,990000201,,"
               "ZZC,CDE,100,2024-12-20,2024-12,C,40,\n")

        def opt(when, qty, price, basis, code):
            return (f'Trades,Data,Order,Equity and Index Options,CAD,'
                    f'U1234567,ZZC 20DEC24 40 C,"{when}",{qty},{price},0,'
                    f'{-qty * price * 100},-1,{basis},0,0,{code}\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp, "b45", [], extra="option_grant_timing_since = 2024\n",
                files={"ib.csv": (
                    HEAD + fii + TRADES_H
                    + opt("2024-06-03, 09:00:00", -1, 3, -250, "C")
                    + opt("2024-06-03, 15:00:00", 1, 2.8, 281, "O")
                    + opt("2024-09-03, 10:00:00", -1, 4, -281, "C"))})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("NOT in the totals", r.stdout + r.stderr)
            d = json.loads(_cli(root, "sum", "--json").stdout)
            self.assertEqual(d["no_purchase_uncovered"], [])
            self.assertEqual([x["booked"] for x in d["no_purchase_in_totals"]],
                             ["short_cover"])


# ----------------------------------------------------- the closing list
class TestZeroCostPositionNamedOnce(unittest.TestCase):

    def test_tj0044_one_lot_part_sold_part_held_is_one_position(self):
        doc = {"year": 2024,
               "zero_cost_sold": [{"symbol": "SAMPK.TO",
                                   "account": "margin"}],
               "zero_cost_held": [{"symbol": "SAMPK.TO", "account": "margin",
                                   "quantity": 30.0}]}
        lines = [t for _k, t in FR.concise_items(doc) if "$0 cost" in t]
        self.assertEqual(len(lines), 1)
        self.assertIn("1 position at a $0 cost (SAMPK.TO)", lines[0])
        self.assertNotIn("SAMPK.TO, SAMPK.TO", lines[0])
        text = "\n".join(FR.render(doc, width_=0))
        self.assertIn("1 position at a $0 cost (1 sold in 2024, 1 still "
                      "held): SAMPK.TO (margin).", text)


# -------------------------------------------- `tjs transfers` and drafts
class TestTransfersProject(unittest.TestCase):
    """One Questrade project: a transfer stated at 0 (sold in part), one
    partly covered by a .tt purchase, another stated at 0, and a
    transfer out naming the receiving account."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = _project(cls.tmp.name, "qt", [
            _qt("2024-02-01", "Buy", "SAMPA.TO", "SAMPLE A CORP", 10, 10,
                -100, -4.95, -104.95),
            _qt("2024-06-03", "TF6", "SAMPK.TO",
                "SAMPLE K CORP TRANSFER BOOK VALUE 0.00", 40, 0, 0, 0, 0,
                kind="Transfers"),
            _qt("2024-09-10", "Sell", "SAMPK.TO", "SAMPLE K CORP", -10, 12,
                120, -4.95, 115.05),
            _qt("2024-06-03", "TF6", "SAMPL.TO",
                "SAMPLE L CORP TRANSFER BOOK VALUE 400.00", 20, 0, 0, 0, 0,
                kind="Transfers"),
            _qt("2024-10-01", "Sell", "SAMPL.TO", "SAMPLE L CORP", -5, 25,
                125, -4.95, 120.05),
            _qt("2024-06-04", "TF6", "SAMPMLONGNAME.TO",
                "SAMPLE M CORP TRANSFER BOOK VALUE 0.00", 15, 0, 0, 0, 0,
                kind="Transfers"),
            _qt("2024-07-02", "TF6", "SAMPA.TO",
                "SAMPLE A CORP TFER TO 99900077", -5, 0, 0, 0, 0,
                kind="Transfers"),
        ], files={"old.tt": "BUYSELL 2021-03-15 09:30:00 SAMPL.TO 10 CAD "
                            "15.00 150.00 0\n"})
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tj0043_act_lines_are_one_line_within_100_columns(self):
        r = _cli(self.root, "transfers", width=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertEqual(len(acts), 2, r.stdout)
        for ln in acts:
            self.assertLessEqual(len(ln), 100, ln)
            self.assertTrue(ln.endswith("— tjs transfers --details"), ln)
        self.assertIn("! 2 transfer-in(s) with no cost (SAMPK.TO, "
                      "SAMPMLONGNAME.TO)", acts[0])
        self.assertIn("! 1 transfer-in(s) only partly covered, NOT booked "
                      "(SAMPL.TO)", acts[1])

    def test_tj0055_type_is_a_transfer_type_not_the_description(self):
        r = _cli(self.root, "transfers", width=0)
        rows = [ln for ln in r.stdout.splitlines()
                if ln.startswith("2024-")]
        self.assertEqual(len(rows), 4, r.stdout)
        for ln in rows:
            self.assertNotIn("9990", ln)
            self.assertNotIn("SAMPLE", ln)
        out = [ln for ln in rows if " SAMPA.TO " in ln][0]
        self.assertIn(" transfer-out ", out)
        self.assertIn(" transfer-in ", [ln for ln in rows
                                        if " SAMPK.TO " in ln][0])
        # --json keeps the row's own text.
        j = json.loads(_cli(self.root, "transfers", "--json").stdout)
        self.assertIn("SAMPLE A CORP TFER TO 99900077",
                      [t["type"] for t in j["transfers"]])
        self.assertTrue(all(not k.startswith("_")
                            for t in j["transfers"] for k in t))

    def test_tj0008_tj0009_the_drafts_of_the_project(self):
        r = _cli(self.root, "find-missing-history", "--write-purchases",
                 "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.root / "inputs" / "margin"
                / "purchases_draft.tt.txt").read_text()
        self.assertIn("BUYSELL  YYYY-MM-DD  09:30:00  SAMPK.TO  40  CAD  "
                      "COST  COST  0", text)
        self.assertIn("BUYSELL  YYYY-MM-DD  09:30:00  SAMPL.TO  10  CAD  "
                      "COST  COST  0", text)
        self.assertNotIn("0.000000  0.00", text)
        self.assertNotIn("(row ?)", text)
        self.assertNotIn("count it twice", text)


class TestTransferKinds(unittest.TestCase):
    """TJ-0055: the TYPE column of hand-made sidecars of three brokers."""

    def test_tj0055_ib_kind_crypto_type_questrade_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "rows.tt").write_text("# none\n")
            (root / "taxjson.toml").write_text(_TOML.format(year=2024,
                                                            extra=""))
            work = root / "work"
            work.mkdir()

            def side(broker, rows):
                (work / f"margin_{broker}_transfers.json").write_text(
                    json.dumps({"metadata": {"kind": "transfer_sidecar",
                                             "account": "margin",
                                             "brokerage": broker},
                                "transactions": rows}))
            side("ib", [_transfer("ZZQ.US", "2024-03-01", 5,
                                  "ACATS (ZZQ)")])
            side("questrade", [_transfer("ZZR.TO", "2024-03-02", -5,
                                         "ZZR INC TFER TO 99900077")])
            side("kraken", [_transfer("ZZX", "2024-03-03", -2,
                                      "withdrawal (fee 0.25 ZZX)")])
            r = _cli(root, "transfers", width=0)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = {ln.split()[2]: ln.split()[4] for ln in r.stdout.splitlines()
                   if ln.startswith("2024-")}
            self.assertEqual(got, {"ZZQ.US": "ACATS",
                                   "ZZR.TO": "transfer-out",
                                   "ZZX": "withdrawal"})


# ------------------------------------------ books older than the inputs
class TestStaleBooks(unittest.TestCase):

    def test_tj0077_tj0175_stale_books_and_the_written_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "fm", [
                _qt("2025-02-03", "Buy", "SAMPY.TO", "SAMPLE Y CORP", 10,
                    10, -100, -4.95, -104.95),
                _qt("2025-04-10", "Sell", "SAMPZ.TO", "SAMPLE Z CORP", -5,
                    10, 50, -4.95, 45.05)], year=2025)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for cmd in ("find-missing-history", "sanity", "transfers"):
                self.assertNotIn("not the clean result",
                                 _cli(root, cmd).stderr, cmd)
            # The purchase is added, and no run follows.
            mh = root / "inputs" / "margin" / "missing_history.tt"
            (root / "inputs" / "margin" / "z.tt").write_text(
                "BUYSELL  2022-01-10  09:30:00  SAMPZ.TO  5  CAD  8.00  "
                "40.00  0\n")
            for cmd in ("find-missing-history", "sanity", "transfers"):
                self.assertIn("not the clean result",
                              _cli(root, cmd).stderr, cmd)
            for flag in ("--write-missing-history", "--write-purchases"):
                r = _cli(root, "find-missing-history", flag)
                self.assertEqual(r.returncode, 1, (flag, r.stderr))
                self.assertIn("older than the inputs", r.stderr)
                self.assertIn("nothing was written", r.stderr)
            self.assertFalse(mh.exists())
            self.assertFalse((root / "inputs" / "margin"
                              / "purchases_draft.tt.txt").exists())
            # The purchase taken out again and the books rebuilt: the
            # lines are written, the note wrapped at the width.
            (root / "inputs" / "margin" / "z.tt").unlink()
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "find-missing-history",
                     "--write-missing-history", width=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(mh.is_file())
            lines = r.stderr.splitlines()
            self.assertTrue(any("`tjs run` reads them." in ln
                                for ln in lines), r.stderr)
            for ln in lines:
                self.assertLessEqual(len(ln), 120, ln)
            # The write's own changes (missing_history.tt, the
            # requires_taxjson pin) do not stop a second one.
            r = _cli(root, "find-missing-history",
                     "--write-missing-history")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("already there", r.stderr)


if __name__ == "__main__":
    unittest.main()
