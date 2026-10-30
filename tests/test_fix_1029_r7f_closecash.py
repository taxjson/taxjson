"""Fix round 7 (wave F) of 2026-10-30, the closecash group: regressions of
wave E's repairs. A broker sale of units the coverage replay cannot see, a
transit whose arrival settled in the year, a later rebuy at another
broker and an INKIND line check-tt cannot size yet (synthetic data only)."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_fix_1029_r7c_coverage import _GENERIC_MAP, _cfg
from test_fix_1029_r7c_coverage import _project as _cov_project
from test_fix_1029_r7e_cash import (_generic, _Q_HDR, _qt, _t1135,
                                    _t1135_project, _tt)
from test_fix_1029_r7e_close import _QH, _project, _row

from taxjson.lib import export_coverage as EC


def _tf(day, qty, bv=None, num="99900001"):
    desc = f"SYN CORP TRANSFER BOOK VALUE {bv}.00" if bv else "SYN CORP TRANSFER"
    return _row(day, "TF6", "SYN.TO", qty, 0, 0, kind="Transfers", desc=desc,
                num=num)


def _sell(day, qty, net, num="99900001"):
    return _row(day, "Sell", "SYN.TO", -qty, net / qty, net, num=num)


def _buy(day, qty, cost, num="99900001"):
    return _row(day, "Buy", "SYN.TO", qty, cost / qty, -cost, num=num)


class TestTJ1283UnitsTheReplayCannotSee(unittest.TestCase):
    """TJ-1283 (a regression of TJ-1179's fix): a broker sale of units the
    account held by other means (an OPENING, a move of your own, a broker
    row's split) spent a .tt purchase waiting for a later arrival."""

    def _run(self, accounts, files):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), accounts)
            for rel, text in files.items():
                (root / "inputs" / rel).write_text(text)
            tj(root, "run", "--no-input")
            r = tj(root, "run", "--no-input")
            self.assertNotIn("go short", r.stdout + r.stderr)
            booked = []
            for p in (root / "work").glob("*_transfer_costs.json"):
                booked += [t["date"][:10] for t in
                           json.loads(p.read_text())["transactions"]]
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            held = json.loads(tj(root, "list", "--json").stdout)["rows"]
            return (sorted(booked), fe["gain_unrounded"],
                    [(h["symbol"], h["qty"]) for h in held])

    def test_an_opening_balance(self):
        got = self._run({"margin": "taxable"}, {
            "margin/t.tt": "OPENING 2024-12-31 SYN.TO 20 CAD 200\n"
                           "BUYSELL 2025-01-05 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "margin/q.csv": _QH + _sell("2025-03-03", 20, 300)
            + _tf("2025-05-05", 10, 100) + _sell("2025-06-02", 10, 200)})
        self.assertEqual(got[0], [])
        self.assertAlmostEqual(got[1], 200.0, places=2)
        self.assertEqual(got[2], [])

    def test_a_sale_beyond_the_opening_still_spends_the_tt_units(self):
        # TJ-1179's own case beside an opening: 5 opening units, 10 .tt
        # units, 15 sold: the arrival has nothing left to cover it.
        got = self._run({"margin": "taxable"}, {
            "margin/t.tt": "OPENING 2024-12-31 SYN.TO 5 CAD 50\n"
                           "BUYSELL 2025-01-05 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "margin/q.csv": _QH + _sell("2025-03-03", 15, 300)
            + _tf("2025-05-05", 10, 300) + _sell("2025-06-02", 10, 400)})
        self.assertEqual(got[0], ["2025-05-05"])
        self.assertAlmostEqual(got[1], 250.0, places=2)
        self.assertEqual(got[2], [])

    def test_a_move_of_your_own(self):
        got = self._run({"aa": "taxable", "bb": "taxable"}, {
            "aa/q.csv": _QH + _buy("2025-01-02", 10, 100)
            + _tf("2025-02-03", -10),
            "bb/t.tt": "BUYSELL 2025-01-05 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "bb/q.csv": _QH + _tf("2025-02-03", 10, num="99900002")
            + _sell("2025-03-03", 10, 150, num="99900002")
            + _tf("2025-05-05", 10, 500, num="99900002")
            + _sell("2025-06-02", 10, 200, num="99900002")})
        self.assertEqual(got[0], [])
        self.assertAlmostEqual(got[1], 150.0, places=2)
        self.assertEqual(got[2], [])

    def test_a_split_by_a_broker_row(self):
        split = _row("2025-02-03", "DIS", "SYN.TO", 10, 0, 0, kind="Other",
                     desc="SYN CORP 2 FOR 1 STOCK SPLIT ON 10 SHS")
        got = self._run({"margin": "taxable"}, {
            "margin/t.tt": "BUYSELL 2025-03-10 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            "margin/q.csv": _QH + _buy("2025-01-02", 10, 200) + split
            + _sell("2025-04-01", 20, 300) + _tf("2025-06-02", 10, 100)
            + _sell("2025-07-02", 10, 200)})
        self.assertEqual(got[0], [])
        self.assertAlmostEqual(got[1], 200.0, places=2)
        self.assertEqual(got[2], [])

    def _replay(self, tt, broker, own_in=None, qty=10.0):
        from taxjson.lib import transfer_in as TI
        a = TI.Arrival(
            account="m", symbol="SYN.TO", key="SYN.TO", date="2025-06-01",
            time="09:30:00", quantity=qty, currency="CAD", book_value=100.0,
            broker="questrade", source="", description="",
            row={"time": "09:30:00"})
        TI.mark_covered([a], {"m": tt}, broker_rows={"m": broker},
                        own_in={"m": own_in or []})
        return a

    @staticmethod
    def _tx(day, q, sym="SYN.TO", action="BUYSELL", new=""):
        return {"action": action, "date": day, "time": "09:30:00",
                "symbol": sym, "quantity": q, "symbol_new": new}

    def test_a_split_that_renames_and_a_reverse_split(self):
        tx = self._tx
        wait = [tx("2025-03-10", 10)]
        # The broker's rows rename OLD.TO: its units are SYN.TO's after it.
        a = self._replay(wait, [tx("2025-01-02", 10, "OLD.TO"),
                                tx("2025-02-03", 1.0, "OLD.TO", "SPLIT",
                                   "SYN.TO"),
                                tx("2025-04-01", -10)])
        self.assertTrue(a.covered)
        # A reverse split adds no units; the sale beyond them is the .tt's.
        a = self._replay(wait, [tx("2025-01-02", 10),
                                tx("2025-02-03", 0.5, "SYN.TO", "SPLIT"),
                                tx("2025-04-01", -15)])
        self.assertFalse(a.covered)

    def test_an_own_move_of_another_symbol_covers_no_sale(self):
        tx = self._tx
        wait = [tx("2025-01-05", 10)]
        sale = [tx("2025-03-03", -10)]
        a = self._replay(wait, sale, own_in=[("2025-02-03", "SYN.TO", 10.0)])
        self.assertTrue(a.covered)
        a = self._replay(wait, sale, own_in=[("2025-02-03", "ZZZ.TO", 10.0)])
        self.assertFalse(a.covered)

    def test_usa_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), {"margin": "taxable"})
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"')
                .replace('America/Toronto', 'America/New_York'))
            (root / "inputs/margin/t.tt").write_text(
                "OPENING 2024-12-31 SYN.US 20 USD 200 2024-01-02\n"
                "BUYSELL 2025-01-05 09:30:00 SYN.US 10 USD 10 100 0\n")
            rows = (_sell("2025-03-03", 20, 300) + _tf("2025-05-05", 10, 100)
                    + _sell("2025-06-02", 10, 200))
            (root / "inputs/margin/q.csv").write_text(
                _QH + rows.replace("SYN.TO", "SYN.US")
                .replace(",CAD,", ",USD,"))
            tj(root, "run", "--no-input")
            tj(root, "run", "--no-input")
            self.assertFalse(
                (root / "work/margin_transfer_costs.json").exists())


class TestTJ1285ArrivalWalkedInTheYear(unittest.TestCase):
    """TJ-1285 (a regression of TJ-1185's fix): on trade dates a departure
    settling after the year whose arrival settled in it read as an unknown
    year-end transit; the units are in the receiver's row."""

    def _rep(self, b_rows, b_move, b_bal, b_extra=""):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": "USA"}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                            -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": {"q.csv": _Q_HDR + b_rows,
                      "c.tt": _tt(0, 0, *b_move, b_bal) + b_extra}},
                tax_date="trade")
            return _t1135(root)

    @rule("CA-RPT-20")
    def test_the_whole_move(self):
        rep = self._rep(_qt("2025-12-31", "2025-12-31", "DEP", 100, 99900002),
                        ("2025-12-31", 100), 100)
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))
        self.assertEqual(rep["cash_not_counted"], [])
        self.assertEqual(round(sum(p["year_end_cost"] or 0
                                   for p in rep["properties"]
                                   if p.get("cash")), 2), 125.0)

    @rule("CA-RPT-20")
    def test_the_part_that_arrived_stays_in_the_receivers_row_only(self):
        rep = self._rep(_qt("2025-12-31", "2025-12-31", "DEP", 60, 99900002)
                        + _qt("2026-01-03", "2026-01-03", "DEP", 40, 99900002),
                        ("2025-12-31", 60), 60,
                        "CASHMOVE 2026-01-03 USD 40 own\n")
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))
        self.assertEqual(rep["cash_not_counted"], [])
        self.assertEqual(sorted(p["year_end_cost"] for p in rep["properties"]
                                if p.get("cash")), [50.0, 75.0])

    def test_settlement_dates_are_unchanged(self):
        # No trade basis: the walk's own close, as before the fix.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": "USA"}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                            -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt("2025-12-31", "2025-12-31", "DEP",
                                            100, 99900002),
                      "c.tt": _tt(0, 0, "2025-12-31", 100, 100)}})
            rep = _t1135(root)
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))

    def test_a_receiver_in_canada_names_nothing(self):
        # A Canadian receiver: the arrival walked in the year is at b, so
        # nothing is unknown at the year end.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": None}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                            -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt("2025-12-31", "2025-12-31", "DEP",
                                            100, 99900002),
                      "c.tt": _tt(0, 0, "2025-12-31", 100, 100)}},
                tax_date="trade")
            rep = _t1135(root)
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))
        self.assertEqual(rep["cash_not_counted"], [])


class TestTJ1287LaterRebuyAtTheOtherBroker(unittest.TestCase):
    """TJ-1287 (a regression of TJ-1158's fix): a later buy closed the
    other broker's earlier sale as a short, so the sale closed nothing."""

    def _gaps(self, alpha, beta):
        with tempfile.TemporaryDirectory() as td:
            root = _cov_project(Path(td) / "p", 2025, {"margin": "taxable"},
                                extra="combined_broker_accounts = true\n")
            for broker, acct, rows in (("alpha", "99900001", alpha),
                                       ("beta", "99900002", beta)):
                f = root / "inputs/margin" / f"generic_{broker}.csv"
                f.write_text(_generic(rows))
                f.with_name(f.name + ".toml").write_text(
                    _GENERIC_MAP.format(broker=broker, account=acct))
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [(g.broker, g.positions) for g in gaps if not g.info]

    BUY = ("2025-03-03", "BUY", "SYN.TO", 10)
    SELL = ("2025-05-01", "SELL", "SYN.TO", -10)
    TAIL = ("2025-12-15", "BUY", "ZZZ.TO", 5)

    def test_the_rebuy_changes_nothing(self):
        got = self._gaps([self.BUY], [
            self.SELL, ("2025-09-01", "BUY", "SYN.TO", 10), self.TAIL])
        self.assertEqual(got, [])

    def test_its_own_buy_still_spends_its_sale(self):
        got = self._gaps([self.BUY], [
            ("2025-03-03", "BUY", "SYN.TO", 10), self.SELL, self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 10.0)])])

    def test_only_a_marked_short_or_a_written_option_opens_a_lot(self):
        def spent_of(sym, **extra):
            rows = [{"action": "BUYSELL", "date": "2025-03-03", "symbol": sym,
                     "quantity": -1, **extra},
                    {"action": "BUYSELL", "date": "2025-05-01", "symbol": sym,
                     "quantity": 1}]
            spent = {}
            EC._spend_own_units(rows, spent, False)
            return [spent.get(id(r), 0.0) for r in rows]
        self.assertEqual(spent_of("SYN.TO"), [0.0, 0.0])
        self.assertEqual(spent_of("SYN.TO", open_close="C"), [0.0, 0.0])
        self.assertEqual(spent_of("SYN.TO", open_close="O"), [-1.0, 1.0])
        self.assertEqual(spent_of("SYN.TO", open_close="C;O"), [-1.0, 1.0])
        self.assertEqual(spent_of("SYN260116C00010000"), [-1.0, 1.0])


_SYM = "SYN251219C00010000.TO"
_IB_HEAD = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers Canada Inc.\n"
    "Statement,Data,Title,Activity Statement\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n"
    "Account Information,Data,Base Currency,CAD\n"
    "Financial Instrument Information,Header,Asset Category,Symbol,Description,"
    "Conid,Security ID,Underlying,Listing Exch,Multiplier,Expiry,Delivery Month,"
    "Type,Strike,Code\n"
    "Financial Instrument Information,Data,Equity and Index Options,"
    "SYN 19DEC25 10 C,SYN CALL,999000101,,SYN,TSE,10,2025-12-19,,C,10,\n"
    "Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,"
    "Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,"
    "Cash Amount,Code\n")


class TestTJ1288InKindSizeNotKnownYet(unittest.TestCase):
    """TJ-1288 (a regression of TJ-1163's fix): check-tt called a valid
    INKIND line refused when only the broker's transfer row, not yet read
    by a run, states the contract size."""

    def _ib_project(self, td, value="2 20"):
        root = _project(Path(td), {"margin": "taxable", "plan": "sheltered"})
        (root / "inputs/margin/trades.tt").write_text(
            f"BUYSELL 2025-01-02 09:30:00 {_SYM} 1 CAD 1 10 0 x10\n"
            f"INKIND 2025-06-03 {_SYM} -1 CAD {value} plan=tfsa\n")
        (root / "inputs/margin/ib.csv").write_text(
            _IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
            "SYN 19DEC25 10 C,2025-06-03,ACATS,Out,Other Broker,99900002,-1,0,"
            "-20,0,0,\n")
        (root / "inputs/plan/ib.csv").write_text(
            _IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
            "SYN 19DEC25 10 C,2025-06-03,ACATS,In,Other Broker,99900002,1,0,"
            "20,0,0,\n")
        return root

    def test_not_an_error_before_the_run_reads_the_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td)
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("may state another contract size",
                          r.stdout + r.stderr)
            r = tj(root, "checklist", "--run", "--only", "tt-lines", "--json",
                   check=False)
            self.assertEqual(json.loads(r.stdout)["counts"]["attention"], 0)
            tj(root, "run", "--no-input")
            r = tj(root, "check-tt")
            self.assertNotIn("contract size", r.stdout + r.stderr)

    def test_a_line_of_no_size_is_refused_once_the_run_read_the_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td, value="2 77")
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("price and total disagree", r.stderr)
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_a_broker_that_states_no_size_stays_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable"})
            (root / "inputs/margin/trades.tt").write_text(
                f"INKIND 2025-06-02 {_SYM} -1 CAD 2 2000 plan=tfsa\n")
            (root / "inputs/margin/qt.csv").write_text(
                _QH + _row("2025-06-02", "TF6", _SYM, -1, 0, 0,
                           kind="Transfers", desc="SYN TRANSFER"))
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_a_line_the_run_does_not_read_is_not_refused(self):
        # The run reads the INKIND lines of taxable accounts only.
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered"})
            (root / "inputs/plan/t.tt").write_text(
                f"INKIND 2025-06-03 {_SYM} -1 CAD 2 2000 plan=tfsa\n")
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            tj(root, "run", "--no-input")


if __name__ == "__main__":
    unittest.main()
