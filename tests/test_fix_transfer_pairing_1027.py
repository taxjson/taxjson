"""A sheltered account's transfer moves net around its trades (backlog
TJ-0360). Its listing journal (IB InterDepot, TSX line to US line,
joined by tobase.map) and its custody move to another broker fell in
one transfer cluster when 35 days apart or less, which the sale between
them blocked; and a move whose legs two brokers dated around a buy (the
arrival posted after it) was refused as "traded while in transit". The
refused rows were booked as sales and purchases at the rows' values:
gains that never happened, and the pool restarted at the arrival's
price of 0. A trade of the symbol dated strictly between two transfer
rows now ends a cluster (each move nets on its own), and a sheltered
account's own book nets a zero-net move whatever trades of its own sit
inside it (the owner's decision); a taxable book still refuses that.

Every fixture is SYNTHETIC: made-up tickers (QZA), ids and amounts.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib.pipeline import (TransferValidationError,
                                  _drop_self_cancelling_transfers,
                                  _handle_transfers)
from taxjson.lib.core import TaxTransaction
from _planted_rates import plant
from tax_rules import rule
from tax_rules.dual import cli


def _t(date_, qty, action="TRANSFER", net=0.0, symbol="QZA.TO",
       account="qzplan"):
    return TaxTransaction(action=action, date=date_, time="10:00:00",
                          symbol=symbol, quantity=float(qty), price=0.0,
                          net_amount=float(net), currency="CAD",
                          account=account)


# The audit's shape: a journal pair, a sale 26 days later, a custody
# move 8 days after that (one cluster: every gap is 35 days or less).
TWO_MOVES = [_t("2026-06-19", 100, "BUYSELL", 4000.0),
             _t("2026-07-31", -90, net=3600.0),
             _t("2026-07-31", 90, net=2900.0),
             _t("2026-08-26", -20, "BUYSELL", 600.0),
             _t("2026-09-03", -70, net=2100.0),
             _t("2026-09-04", 70)]


def _transfers(rows):
    return [(t.date, t.quantity) for t in rows if t.action == "TRANSFER"]


class TestTheDropper(unittest.TestCase):

    def test_a_trade_between_two_moves_separates_them(self):
        out, dropped = _drop_self_cancelling_transfers(TWO_MOVES)
        self.assertEqual(_transfers(out), [])
        self.assertEqual(dropped, [("QZA.TO", "qzplan", 4)])
        self.assertEqual([t.date for t in out],
                         ["2026-06-19", "2026-08-26"])

    def test_a_position_in_transit_during_the_trade_is_not_netted(self):
        # Out before the trade, in after it: a taxable book (the
        # default) refuses, as before.
        rows = [_t("2026-09-03", -70), _t("2026-09-05", 10, "BUYSELL"),
                _t("2026-09-08", 70)]
        out, dropped = _drop_self_cancelling_transfers(rows)
        self.assertEqual(dropped, [])
        self.assertEqual(_transfers(out),
                         [("2026-09-03", -70.0), ("2026-09-08", 70.0)])

    def test_a_trade_on_a_rows_own_date_still_blocks(self):
        rows = [_t("2026-07-31", -90), _t("2026-07-31", 90),
                _t("2026-08-26", -20, "BUYSELL"), _t("2026-09-03", -70),
                _t("2026-09-04", 5, "BUYSELL"), _t("2026-09-04", 70)]
        out, dropped = _drop_self_cancelling_transfers(rows)
        # The journal nets; the move with a trade on its arrival day
        # does not.
        self.assertEqual(dropped, [("QZA.TO", "qzplan", 2)])
        self.assertEqual(_transfers(out),
                         [("2026-09-03", -70.0), ("2026-09-04", 70.0)])

    def test_a_trade_in_another_account_or_symbol_does_not_separate(self):
        for other in (_t("2026-08-26", -20, "BUYSELL", account="other"),
                      _t("2026-08-26", -20, "BUYSELL", symbol="QZB.TO")):
            rows = [_t("2026-07-31", -90), _t("2026-08-20", 20),
                    other, _t("2026-09-03", 70)]
            out, dropped = _drop_self_cancelling_transfers(rows)
            # One cluster, as before: it nets (no trade of its own).
            self.assertEqual(dropped, [("QZA.TO", "qzplan", 3)], other)

    def test_a_split_between_two_moves_separates_them(self):
        rows = [_t("2026-03-01", -100), _t("2026-03-02", 100),
                TaxTransaction(action="SPLIT", date="2026-03-10",
                               symbol="QZA.TO", quantity=2.0,
                               account="elsewhere"),
                _t("2026-03-20", -200), _t("2026-03-21", 200)]
        out, dropped = _drop_self_cancelling_transfers(rows)
        self.assertEqual(_transfers(out), [])
        self.assertEqual(dropped, [("QZA.TO", "qzplan", 4)])

    def test_a_main_book_split_separates_the_sheltered_context(self):
        sheltered = [_t("2026-03-01", -100), _t("2026-03-02", 100),
                     _t("2026-03-20", -200), _t("2026-03-21", 200)]
        main = [TaxTransaction(action="SPLIT", date="2026-03-10",
                               symbol="QZA.TO", quantity=2.0,
                               account="margin")]
        with redirect_stderr(io.StringIO()):
            out, dropped = _drop_self_cancelling_transfers(
                sheltered, main_transactions=main, near_trade_guard=False)
        self.assertEqual(_transfers(out), [])

    def test_the_taxable_book_nets_both_and_refuses_a_leftover(self):
        with redirect_stderr(io.StringIO()):
            out, _ = _handle_transfers(
                [_t(r.date, r.quantity, r.action, r.net_amount,
                    account="margin") for r in TWO_MOVES], [],
                taxable=True)
        self.assertEqual(_transfers(out), [])
        # A real transfer-out after the journal still stops the run.
        rows = [_t("2026-06-19", 100, "BUYSELL", account="margin"),
                _t("2026-07-31", -90, account="margin"),
                _t("2026-07-31", 90, account="margin"),
                _t("2026-08-26", -20, "BUYSELL", account="margin"),
                _t("2026-09-03", -70, account="margin")]
        with self.assertRaises(TransferValidationError) as cm:
            with redirect_stderr(io.StringIO()):
                _handle_transfers(rows, [], taxable=True)
        self.assertIn("1 TRANSFER row(s)", str(cm.exception))


class TestTheShelteredBook(unittest.TestCase):
    """trades_block=False: a sheltered account's own book."""

    def _drop(self, rows):
        return _drop_self_cancelling_transfers(rows, trades_block=False)

    def test_a_move_around_a_trade_nets(self):
        # The arrival posted after a buy at the new broker.
        rows = [_t("2026-07-23", -90), _t("2026-07-23", 90),
                _t("2026-09-10", -70), _t("2026-09-14", 10, "BUYSELL"),
                _t("2026-09-15", 70)]
        out, dropped = self._drop(rows)
        self.assertEqual(_transfers(out), [])
        self.assertEqual(dropped, [("QZA.TO", "qzplan", 4)])
        # A trade on a row's own date too.
        out, _ = self._drop([_t("2026-09-11", -70),
                             _t("2026-09-11", 10, "BUYSELL"),
                             _t("2026-09-11", 70)])
        self.assertEqual(_transfers(out), [])

    def test_the_chained_cluster_nets(self):
        out, dropped = self._drop(TWO_MOVES)
        self.assertEqual(_transfers(out), [])
        self.assertEqual(dropped, [("QZA.TO", "qzplan", 4)])

    def test_a_move_beside_a_real_transfer_out_nets_and_the_out_stays(self):
        # A journal, then a withdrawal in kind 20 days later: the
        # cluster nets -70, its journal run nets on its own.
        rows = [_t("2026-07-23", -90), _t("2026-07-23", 90),
                _t("2026-08-12", -70)]
        out, dropped = self._drop(rows)
        self.assertEqual(_transfers(out), [("2026-08-12", -70.0)])
        self.assertEqual(dropped, [("QZA.TO", "qzplan", 2)])
        # The run that nets may follow a row that cannot.
        rows = [_t("2026-07-23", 90), _t("2026-08-12", -70),
                _t("2026-08-13", 70)]
        out, _ = self._drop(rows)
        self.assertEqual(_transfers(out), [("2026-07-23", 90.0)])

    def test_a_split_still_blocks(self):
        rows = [_t("2026-03-01", -100),
                TaxTransaction(action="SPLIT", date="2026-03-01",
                               symbol="QZA.TO", quantity=2.0,
                               account="qzplan"),
                _t("2026-03-02", 200)]
        out, dropped = self._drop(rows)
        self.assertEqual(dropped, [])
        self.assertEqual(len(_transfers(out)), 2)

    def test_handle_transfers_nets_a_sheltered_book_and_refuses_a_taxable(
            self):
        rows = [_t("2026-06-19", 100, "BUYSELL", 4000.0),
                _t("2026-09-10", -70, net=2100.0),
                _t("2026-09-14", 10, "BUYSELL", 280.00),
                _t("2026-09-15", 70)]
        with redirect_stderr(io.StringIO()) as err:
            out, _ = _handle_transfers(rows, [], taxable=False)
        self.assertEqual(_transfers(out), [])
        self.assertIn("(net-zero custody moves)", err.getvalue())
        taxable = [_t(r.date, r.quantity, r.action, r.net_amount,
                      account="margin") for r in rows]
        with self.assertRaises(TransferValidationError):
            with redirect_stderr(io.StringIO()):
                _handle_transfers(taxable, [], taxable=True)


# ------------------------------------------------------------ the run

IB = ('Statement,Header,Field Name,Field Value\n'
      'Statement,Data,BrokerName,Interactive Brokers\n'
      'Statement,Data,Title,Activity Statement\n'
      'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n'
      'Financial Instrument Information,Header,Asset Category,Symbol,'
      'Description,Conid,Security ID,Underlying,Listing Exch,'
      'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n'
      'Financial Instrument Information,Data,Stocks,QZA,'
      '"QZALPHA MINES CORP",999000201,CA9990002011,,TSE,1,,,COMMON,,\n'
      'Trades,Header,DataDiscriminator,Asset Category,Currency,'
      'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
      'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n'
      'Trades,Data,Order,Stocks,CAD,U1234567,QZA,"2026-06-19, 10:00:00",'
      '100,40.00,0,-4000,0,0,0,0,O\n'
      'Trades,Data,Order,Stocks,CAD,U1234567,QZA,"2026-06-30, 10:00:00",'
      '-10,41.00,0,410.00,0,0,0,0,C\n'
      'Trades,Data,Order,Stocks,USD,U1234567,QZA,"2026-08-26, 10:00:00",'
      '-20,30.00,0,600.00,0,0,0,0,C\n'
      'Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
      'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
      'Market Value,Realized P/L,Cash Amount,Code\n'
      'Transfers,Data,Stocks,CAD,QZA,2026-07-31,InterDepot,Out,'
      'IB,U1234567,-90,0,-3600,0,0,\n'
      'Transfers,Data,Stocks,USD,QZA,2026-07-31,InterDepot,In,'
      'IB,U1234567,90,0,2900,0,0,\n'
      'Transfers,Data,Stocks,USD,QZA,2026-09-03,ATON,Out,'
      'Other Broker,99900009,-70,0,-2100,0,0,\n')
QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def _qt(td, action, desc, qty, net, act, price, gross):
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},QZA,{desc},{qty},"
            f"{price},{gross},0,{net},USD,99900001,{act},Individual RRSP\n")


def _qt_export(arrival):
    return (QH + _qt(arrival, "TFI", "QZALPHA MINES CORP TRANSFER IN "
                     "INTERACTIVE BROKER", "70", "0", "Transfers", "0", "0")
            + _qt("2026-09-14", "Buy", "QZALPHA MINES CORP WE ACTED AS "
                  "AGENT", "10", "-280.00", "Trades", "28.00", "-280.00"))

COUNTRY = {
    "canada": ('base_currency = "CAD"\nsource_currencies = ["USD"]\n'
               'local_timezone = "America/Toronto"\n'
               'option_grant_timing_since = 2026\n',
               "TOBASE QZA.US QZA.TO\n", "USD CAD 1.2500"),
    "usa": ('base_currency = "USD"\nsource_currencies = ["CAD"]\n'
            'local_timezone = "America/New_York"\n',
            "TOBASE QZA.TO QZA.US\n", "CAD USD 0.8000"),
}


def _project(root: Path, country: str, *, journal: str, aton: str,
             arrival: str, cleanup) -> None:
    """The project; its rates also in the suite's HOME (put back by
    `cleanup`): a run that rebuilds them keeps them (TJ-0635)."""
    settings, tobase, pair = COUNTRY[country]
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n' + settings
        + '\n[accounts.qzplan]\ntype = "sheltered"\ntransfers = true\n')
    (root / "ticker.map").write_text("# synthetic\n")
    (root / "tobase.map").write_text(tobase)
    inp = root / "inputs" / "qzplan"
    inp.mkdir(parents=True)
    (inp / "U1234567_2026.csv").write_text(
        IB.replace("2026-07-31", journal).replace("2026-09-03", aton))
    (inp / "99900001.csv").write_text(_qt_export(arrival))
    d, lines = date(2025, 12, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 {pair} boc")
        d += timedelta(days=1)
    plant(root / "work" / "to_base.csv", "\n".join(lines) + "\n",
          cleanup=cleanup)


class _Run:
    COUNTRY = "canada"
    # The chained shape: the journal and the move 34 days apart, the
    # Aug 26 sale between them.
    DATES = dict(journal="2026-07-31", aton="2026-09-03",
                 arrival="2026-09-04")

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name) / cls.COUNTRY
        cls.root.mkdir()
        _project(cls.root, cls.COUNTRY, **cls.DATES,
                 cleanup=cls.addClassCleanup)
        cls.r = cli(cls.root, "run", "--no-input")
        cls.gains = json.loads(
            (cls.root / "work" / "qzplan_gains.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_the_run_passes(self):
        self.assertEqual(self.r.returncode, 0, self.r.stdout + self.r.stderr)

    def test_both_moves_are_netted(self):
        diag = (self.root / "work" / "qzplan_gains.json.diag").read_text()
        self.assertIn("auto-dropped 4 self-cancelling TRANSFER row(s) "
                      "(net-zero custody moves)", diag)
        self.assertNotIn("rewrote", diag)

    def test_the_pool_carries_through_the_moves(self):
        (inv,) = self.gains["inventory"]
        self.assertEqual(inv["qty"], 80.0)
        self.assertAlmostEqual(inv["total_cost"], self.COST, places=2)
        self.assertEqual(inv["position_start_date"], "2026-06-19")

    def test_only_the_real_sales_are_dispositions(self):
        sales = sorted(r["date"] for r in self.gains["transactions"]
                       if r.get("gain") is not None)
        self.assertEqual(sales, ["2026-06-30", "2026-08-26"])


# 70 of the June 100 at 40.00 CAD, plus 10 at 28.00 USD x 1.25.
CA_COST = 70 * 40.00 + 10 * 28.00 * 1.25
# 70 of the June lot at 40.00 CAD x 0.80, plus 10 at 28.00 USD.
US_COST = 70 * 40.00 * 0.80 + 10 * 28.00
# The report's signature: the move's legs dated around the Sep 14 buy
# (the arrival posted after it), the journal 49 days before.
LATE = dict(journal="2026-07-23", aton="2026-09-10", arrival="2026-09-15")


@rule("CA-SL-16")
class TestCanada(_Run, unittest.TestCase):
    COUNTRY = "canada"
    COST = CA_COST


@rule("US-WASH-23")
class TestUsa(_Run, unittest.TestCase):
    COUNTRY = "usa"
    COST = US_COST


@rule("CA-SL-16")
class TestCanadaLateArrival(_Run, unittest.TestCase):
    COUNTRY = "canada"
    COST = CA_COST
    DATES = LATE


@rule("US-WASH-23")
class TestUsaLateArrival(_Run, unittest.TestCase):
    COUNTRY = "usa"
    COST = US_COST
    DATES = LATE


if __name__ == "__main__":
    unittest.main()
