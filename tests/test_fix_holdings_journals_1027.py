"""A journal between two listings moves its units with their cost in the
native books (backlog TJ-0361, TJ-0363). The native-currency books behind
reports/<account>_holdings.toml keep each listing apart; the holdings
export moved a journal's units after the gains, so a sale of the new
listing between the journal and the year end was booked against nothing
(a short), and in an account that keeps its transfers in the books
(`transfers = true`) both legs were booked as a sale and a purchase at
the rows' market value. The run now writes each journal into the native
merge as two `listing_journal` rows (lib/listing_journals) that the
engines book on the journal's date: the export's cost is the books'.

Every fixture is SYNTHETIC: made-up tickers (QZA, QZB), ids and amounts.
"""
import inspect
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from taxjson.lib.core import LISTING_JOURNAL_TYPE, TaxTransaction
from taxjson.lib.listing_journals import book_journals
from taxjson.lib.pipeline import _handle_transfers
from taxjson.lib.tomlcompat import tomllib
from tax_rules.dual import cli, gains_both, tx
from test_fix_transfer_pairing_1027 import IB, _project

_VALID = set(inspect.signature(TaxTransaction).parameters)


def _row(action, day, symbol, qty, net, cur):
    return tx(action, day, symbol, qty, net, currency=cur,
              account="qzplan").to_dict()


def _rate(cur, _day):
    return {"CAD": Decimal(1), "USD": Decimal("1.25")}.get(cur)


def _same(a, b):
    return a[:3] == b[:3]


def _book(rows):
    return [TaxTransaction(**{k: v for k, v in r.items() if k in _VALID})
            for r in rows]


def _inv(res):
    return {i["symbol"]: (i["qty"], round(i["total_cost"], 4))
            for i in res["inventory"]}


def _journal(rows):
    return [(r["symbol"], r["quantity"], r.get("price"))
            for r in rows if r.get("type") == LISTING_JOURNAL_TYPE]


# The TJ-0360 shape in a native book: a June buy of the TSX line, its
# journal to the US line (the account's own TRANSFER rows), a sale and a
# buy of the US line after it.
def _shape():
    return [_row("BUYSELL", "2026-06-19", "QZA.TO", 100, 4000, "CAD"),
            _row("TRANSFER", "2026-07-23", "QZA.TO", -90, 3600, "CAD"),
            _row("TRANSFER", "2026-07-23", "QZA.US", 90, 2900, "USD"),
            _row("BUYSELL", "2026-08-26", "QZA.US", -20, 600, "USD"),
            _row("BUYSELL", "2026-09-14", "QZA.US", 10, 280, "USD")]


class TestBookJournals(unittest.TestCase):

    def test_the_books_own_legs_become_a_journal(self):
        rows = _shape()
        got = book_journals(rows, [], _same, _rate)
        self.assertEqual([(j["date"], j["out_symbol"], j["in_symbol"],
                           j["quantity"]) for j in got],
                         [("2026-07-23", "QZA.TO", "QZA.US", 90.0)])
        self.assertFalse([r for r in rows if r["action"] == "TRANSFER"
                          and r.get("type") != LISTING_JOURNAL_TYPE])
        # The cost restated into the US line's currency at the day's
        # rate (CAD over USD), on the in-leg.
        self.assertEqual(_journal(rows), [("QZA.TO", -90.0, 0.0),
                                          ("QZA.US", 90.0, 0.8)])
        # In the book's order: after the June buy, before the sale.
        self.assertEqual([r["date"] for r in rows],
                         ["2026-06-19", "2026-07-23", "2026-07-23",
                          "2026-08-26", "2026-09-14"])

    def test_evidence_legs_and_a_partial_journal(self):
        rows = [_row("BUYSELL", "2026-06-19", "QZA.TO", 100, 4000, "CAD"),
                _row("TRANSFER", "2026-07-23", "QZA.TO", -90, 3600, "CAD")]
        ev = [_row("TRANSFER", "2026-07-24", "QZA.US", 60, 1920, "USD")]
        book_journals(rows, ev, _same, _rate)
        left = [r for r in rows if r["action"] == "TRANSFER"
                and r.get("type") != LISTING_JOURNAL_TYPE]
        # The 30 the journal did not take stay a transfer row, their
        # amount with them; the journal is dated at its first leg.
        self.assertEqual([(r["quantity"], r["net_amount"]) for r in left],
                         [(-30.0, 1200.0)])
        self.assertEqual({r["date"] for r in rows
                          if r.get("type") == LISTING_JOURNAL_TYPE},
                         {"2026-07-23"})

    def test_once_only(self):
        rows = _shape()
        book_journals(rows, [], _same, _rate)
        again = [dict(r) for r in rows]
        self.assertEqual(book_journals(again, [], _same, _rate), [])
        self.assertEqual(again, rows)

    def test_no_rate_is_said_and_the_cost_unknown(self):
        rows, notes = _shape(), []
        book_journals(rows, [], _same, lambda c, d: None, note=notes.append)
        self.assertEqual(_journal(rows)[1], ("QZA.US", 90.0, 0.0))
        self.assertEqual(len(notes), 1)
        self.assertIn("no CAD/USD rate", notes[0])

    def test_far_apart_legs_and_one_listings_moves_are_no_journal(self):
        rows = [_row("BUYSELL", "2026-01-05", "QZA.TO", 100, 4000, "CAD"),
                _row("TRANSFER", "2026-02-02", "QZA.TO", -100, 4000, "CAD"),
                _row("TRANSFER", "2026-06-01", "QZA.US", 100, 3200, "USD"),
                _row("TRANSFER", "2026-07-02", "QZA.US", -100, 3200, "USD"),
                _row("TRANSFER", "2026-07-03", "QZA.US", 100, 3200, "USD")]
        far = []
        self.assertEqual(book_journals(rows, [], _same, _rate,
                                       far_note=far.append), [])
        self.assertTrue(far)
        for n in far:
            self.assertIn("more than 5 business days apart", n)


class TestTheEngines(unittest.TestCase):

    def test_a_long_moves_with_its_cost(self):
        rows = _shape()
        book_journals(rows, [], _same, _rate)
        for c, res in gains_both(_book(rows), taxable=False).items():
            with self.subTest(country=c):
                # 90 of the 100 at 40.00 CAD carried to the US line at
                # 0.8: 2880 USD; the sale of 20 takes 640; the buy adds
                # 280.
                self.assertEqual(_inv(res), {"QZA.TO": (10.0, 400.0),
                                             "QZA.US": (80.0, 2520.0)})
                self.assertEqual([(d["date"], d["symbol"], d["gain"])
                                  for d in res["transactions"]],
                                 [("2026-08-26", "QZA.US", -40.0)])
                us = [i for i in res["inventory"]
                      if i["symbol"] == "QZA.US"][0]
                self.assertEqual(us["position_start_date"], "2026-06-19")
                self.assertNotIn("conservation", res["_stderr"])

    def test_a_base_currency_book_moves_the_cost_as_is(self):
        rows = _shape()
        book_journals(rows, [], _same, _rate)
        for r in rows:
            r["currency"] = "CAD"
        for c, res in gains_both(_book(rows), taxable=False).items():
            with self.subTest(country=c):
                # 3600 carried, 800 of it sold, 280 bought.
                self.assertEqual(_inv(res)["QZA.US"], (80.0, 3080.0))

    def test_a_short_moves_the_other_way(self):
        rows = [_row("BUYSELL", "2026-03-02", "QZB.TO", -10, 400, "CAD"),
                _row("BUYSELL", "2026-03-10", "QZB.US", 10, 300, "USD")]
        ev = [_row("TRANSFER", "2026-03-04", "QZB.TO", 10, 0, "CAD"),
              _row("TRANSFER", "2026-03-04", "QZB.US", -10, 0, "USD")]
        book_journals(rows, ev, _same, _rate)
        for c, res in gains_both(_book(rows), taxable=False).items():
            with self.subTest(country=c):
                # The short's 400 CAD of proceeds carried as 320 USD and
                # covered at 300.
                self.assertEqual(_inv(res), {})
                self.assertEqual([(d["date"], d["symbol"], d["gain"])
                                  for d in res["transactions"]],
                                 [("2026-03-10", "QZB.US", 20.0)])

    def test_units_beyond_the_holding_are_held_at_an_unknown_cost(self):
        rows = [_row("BUYSELL", "2026-06-19", "QZA.TO", 50, 2000, "CAD"),
                _row("TRANSFER", "2026-07-23", "QZA.TO", -90, 3600, "CAD"),
                _row("TRANSFER", "2026-07-23", "QZA.US", 90, 2900, "USD")]
        book_journals(rows, [], _same, _rate)
        for c, res in gains_both(_book(rows), taxable=False).items():
            with self.subTest(country=c):
                self.assertEqual(_inv(res), {"QZA.TO": (-40.0, 0.0),
                                             "QZA.US": (90.0, 1600.0)})

    def test_the_transfer_handling_keeps_the_legs(self):
        rows = _shape()
        book_journals(rows, [], _same, _rate)
        kept, _ = _handle_transfers(_book(rows), [], taxable=True)
        self.assertEqual(sorted((t.symbol, t.quantity) for t in kept
                                if t.action == "TRANSFER"),
                         [("QZA.TO", -90.0), ("QZA.US", 90.0)])


class TestTheRunStep(unittest.TestCase):
    """taxjson_run._raw_book_journals: the evidence under the merge's
    own symbols."""

    def test_a_sidecar_leg_under_a_renamed_symbol(self):
        from taxjson.bin.taxjson_run import _raw_book_journals
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            raw = d / "qzplan_raw.json"
            raw.write_text(json.dumps({"transactions": [
                _row("BUYSELL", "2026-06-19", "QZC.TO", 100, 4000, "CAD"),
                _row("BUYSELL", "2026-07-02", "QZC.US", -60, 1920,
                     "USD")]}))
            # The parser wrote the old ticker; the map's GLOBAL line
            # renames it in the merge.
            side = d / "qzplan_ib_transfers.json"
            side.write_text(json.dumps({
                "metadata": {"kind": "transfer_sidecar"},
                "transactions": [
                    _row("TRANSFER", "2026-06-30", "QZX.TO", -100, 0,
                         "CAD"),
                    _row("TRANSFER", "2026-06-30", "QZC.US", 100, 0,
                         "USD")]}))
            tmap = d / "ticker.map"
            tmap.write_text("GLOBAL QZX.TO QZC.TO\n"
                            "TOBASE QZC.US QZC.TO\n")
            rates = d / "to_base.csv"
            rates.write_text("2026-06-30 12:00:00 USD CAD 1.2500 boc\n")
            self.assertEqual(_raw_book_journals(raw, [side], tmap, rates,
                                                "CAD"), [])
            rows = json.loads(raw.read_text())["transactions"]
        self.assertEqual(_journal(rows), [("QZC.TO", -100.0, 0.0),
                                          ("QZC.US", 100.0, 0.8)])


# ------------------------------------------------------------ the run

def _holding(root, symbol):
    doc = tomllib.loads((root / "reports" / "qzplan_holdings.toml")
                        .read_text())
    return [h for h in doc["holding"] if h["symbol"] == symbol]


def _books(root):
    g = json.loads((root / "work" / "qzplan_gains.json").read_text())
    return {i["symbol"]: (i["qty"], round(i["total_cost"], 2))
            for i in g["inventory"]}


class _Run:
    COUNTRY = "canada"
    US_LINE = "QZA.US"

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name) / cls.COUNTRY
        cls.root.mkdir()
        _project(cls.root, cls.COUNTRY, journal="2026-07-23",
                 aton="2026-09-10", arrival="2026-09-11")
        cls.edit(cls.root)
        cls.r = cli(cls.root, "run", "--no-input")

    @classmethod
    def edit(cls, root):
        pass

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_the_run_passes(self):
        self.assertEqual(self.r.returncode, 0, self.r.stdout + self.r.stderr)

    def test_the_export_carries_the_books_cost(self):
        (sym, (qty, cost)), = _books(self.root).items()
        h, = _holding(self.root, self.US_LINE)
        self.assertEqual(h["quantity"], qty)
        self.assertAlmostEqual(h["base_total_cost"], cost, places=2)
        self.assertNotIn("mixed_currency", h)
        self.assertEqual(h["currency"], "USD")


class TestCanadaSheltered(_Run, unittest.TestCase):
    """transfers = true: the journal's legs are the account's own rows."""


class TestUsaSheltered(_Run, unittest.TestCase):
    COUNTRY = "usa"


def _gambit(root):
    """A taxable account (its transfers kept out of the books): 100 of
    the TSX line bought, journaled to the US line, 60 of it sold."""
    p = root / "taxjson.toml"
    p.write_text(p.read_text().replace(
        'type = "sheltered"\ntransfers = true\n', 'type = "taxable"\n'))
    (root / "inputs" / "qzplan" / "99900001.csv").unlink()
    head, rest = IB.split("Trades,Header", 1)
    keep = [ln for ln in ("Trades,Header" + rest).splitlines()
            if ln.startswith("Trades,Header") or "2026-06-19" in ln]
    keep += [
        'Trades,Data,Order,Stocks,USD,U1234567,QZA,"2026-07-02, 10:00:00",'
        '-60,32.00,0,1920.00,0,0,0,0,C',
        'Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
        'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,'
        'Realized P/L,Cash Amount,Code',
        'Transfers,Data,Stocks,CAD,QZA,2026-06-30,InterDepot,Out,IB,'
        'U1234567,-100,0,-4000,0,0,',
        'Transfers,Data,Stocks,USD,QZA,2026-06-30,InterDepot,In,IB,'
        'U1234567,100,0,3200,0,0,']
    (root / "inputs" / "qzplan" / "U1234567_2026.csv").write_text(
        head + "\n".join(keep) + "\n")


class TestCanadaGambit(_Run, unittest.TestCase):
    """transfers = false: the journal's legs are in the sidecar; the sale
    of the US line found no units in the native books (TJ-0363)."""
    edit = classmethod(lambda cls, root: _gambit(root))

    def test_the_rest_is_on_the_us_line(self):
        h, = _holding(self.root, "QZA.US")
        self.assertEqual(h["quantity"], 40.0)
        self.assertEqual(_holding(self.root, "QZA.TO"), [])


class TestUsaGambit(TestCanadaGambit):
    COUNTRY = "usa"


del _Run


if __name__ == "__main__":
    unittest.main()
