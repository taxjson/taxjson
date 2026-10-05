"""`taxjson opening` end to end, and `taxjson sanity`'s cost and income
checks (tax-logic CA-OPEN-01..03 / US-OPEN-01..03). Synthetic data and
fake account ids only."""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli

from taxjson.lib import positions_check as PC


def _project(root: Path, *, country="canada", cur="CAD", year=2025,
             accounts=("margin",), tt=None):
    root.mkdir(parents=True, exist_ok=True)
    acc = "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts)
    extra = ('option_grant_timing_since = 2025\n'
             if country == "canada" else "")
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{extra}{acc}')
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    for (a, name), text in (tt or {}).items():
        (root / "inputs" / a / name).write_text(text)
    return root


def _toml(path: Path, holdings, as_of=None, account="55500001"):  # pii-ok
    out = [f'[meta]\naccount = "{account}"']
    if as_of:
        out.append(f'as_of = "{as_of}"')
    for h in holdings:
        out.append("\n[[holding]]")
        for k, v in h.items():
            out.append(f"{k} = {json.dumps(v)}")
    path.write_text("\n".join(out) + "\n")
    return path


# A loss sold 10 days after the statement day, half the shares kept.
HIST = ("BUYSELL 2025-02-10 09:30:00 SAMPB.TO -10 CAD 8 80 0\n"
        "BUYSELL 2025-03-10 09:30:00 SAMPD.TO 10 CAD 30 300 0\n")


class TestOpeningCanada(unittest.TestCase):
    @rule("CA-OPEN-01")
    def test_snapshot_lines_and_no_false_superficial_loss(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", tt={("margin", "h.tt"): HIST})
            rep = _toml(Path(td) / "pos.toml", [
                {"symbol": "SAMPB.TO", "quantity": 20, "currency": "CAD",
                 "total_cost": 200.0},
                {"symbol": "SAMPE.TO", "quantity": 7, "currency": "CAD",
                 "market_value": 70.0}], as_of="2025-01-31")
            r = cli(root, "opening", "margin", str(rep))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("skipped SAMPE.TO", r.stderr)
            self.assertIn("market value is never a cost", r.stderr)
            f = root / "inputs" / "margin" / "opening_2025-01-31.tt"
            body = [ln for ln in f.read_text().splitlines()
                    if ln and not ln.startswith("#")]
            self.assertEqual(body, ["OPENING 2025-01-31 SAMPB.TO 20 CAD "
                                    "200.00"])
            # A second run refuses to overwrite without --force.
            r2 = cli(root, "opening", "margin", str(rep))
            self.assertNotEqual(r2.returncode, 0)
            self.assertIn("--force", r2.stderr)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = json.loads(cli(root, "sum", "--json").stdout)
            txt = json.dumps(doc)
            # The 2025-02-10 loss is allowed: the opening is not a
            # purchase in its window (a BUYSELL on 2025-01-31 denied it).
            g = json.loads((root / "work" / "margin_gains_wash.json")
                           .read_text())
            sale = [t for t in g["transactions"]
                    if t.get("symbol") == "SAMPB.TO"][0]
            self.assertAlmostEqual(sale["gain"], -20.0, places=2)
            self.assertFalse(sale.get("is_wash_sale"))
            self.assertIn("total", txt)
            fm = cli(root, "find-missing-history")
            self.assertEqual(fm.returncode, 0, fm.stderr)
            self.assertNotIn("SAMPB.TO", fm.stdout)

    @rule("CA-OPEN-01")
    def test_control_buysell_opening_is_denied(self):
        # The old hand-written opening keeps working unchanged — and
        # keeps its known flaw (the reproduced false denial).
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", tt={
                ("margin", "h.tt"): HIST,
                ("margin", "margin_start.tt"):
                    "BUYSELL 2025-01-31 09:30:00 SAMPB.TO 20 CAD 10 200 0\n"})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            g = json.loads((root / "work" / "margin_gains_wash.json")
                           .read_text())
            sale = [t for t in g["transactions"]
                    if t.get("symbol") == "SAMPB.TO"][0]
            self.assertTrue(sale.get("is_wash_sale"))

    @rule("CA-OPEN-03")
    def test_overlapping_history_is_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as td:
            hist = ("BUYSELL 2024-03-01 09:30:00 SAMPB.TO 30 CAD 10 300 0\n"
                    "BUYSELL 2024-06-03 09:30:00 SAMPB.TO -10 CAD 12 120 0\n"
                    "BUYSELL 2025-02-10 09:30:00 SAMPB.TO -10 CAD 8 80 0\n")
            root = _project(Path(td) / "p", tt={
                ("margin", "h.tt"): hist,
                ("margin", "opening_2024-12-31.tt"):
                    "OPENING 2024-12-31 SAMPB.TO 20 CAD 200\n"})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("ATTENTION: opening: margin", r.stdout + r.stderr)
            inv = {i["symbol"]: i for i in json.loads(
                (root / "work" / "margin_gains_wash.json").read_text()
            )["inventory"]}
            self.assertAlmostEqual(inv["SAMPB.TO"]["qty"], 10.0)
            self.assertAlmostEqual(inv["SAMPB.TO"]["total_cost"], 100.0)

    @rule("CA-OPEN-03")
    def test_snapshot_inside_the_tax_year_with_a_sale_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            hist = ("BUYSELL 2025-01-06 09:30:00 SAMPB.TO 30 CAD 10 300 0\n"
                    "BUYSELL 2025-02-10 09:30:00 SAMPB.TO -10 CAD 8 80 0\n")
            root = _project(Path(td) / "p", tt={
                ("margin", "h.tt"): hist,
                ("margin", "opening_2025-03-31.tt"):
                    "OPENING 2025-03-31 SAMPB.TO 20 CAD 200\n"})
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("would leave out 1 sale(s) of the 2025 tax year",
                          r.stdout + r.stderr)

    @rule("CA-OPEN-01")
    def test_opening_from_an_ib_statement(self):
        stmt = (
            "Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers Canada Inc.\n"
            "Statement,Data,Title,Activity Statement\n"
            'Statement,Data,Period,"January 1, 2024 - December 31, 2024"\n'
            "Account Information,Header,Field Name,Field Value\n"
            "Account Information,Data,Account,U5550001\n"  # pii-ok
            "Account Information,Data,Base Currency,CAD\n"
            "Trades,Header,DataDiscriminator,Asset Category,Currency,"
            "Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,"
            "Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n"
            'Trades,Data,Order,Stocks,CAD,ZZA,"2024-02-12, 09:35:14",100,'
            "10.50,10.50,-1050.00,0,1050.00,0,0,O\n"
            "Open Positions,Header,DataDiscriminator,Asset Category,"
            "Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,"
            "Close Price,Value,Unrealized P/L,Code\n"
            "Open Positions,Data,Summary,Stocks,CAD,ZZA,100,1,10.5,1050,"
            "12,1200,150,\n"
            "Open Positions,Data,Summary,Stocks,CAD,ZZC,50,1,4,200,5,250,"
            "50,\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            f = root / "inputs" / "margin" / "ib_2024.csv"
            f.write_text(stmt)
            r = cli(root, "opening", "margin", str(f))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("U5550001", r.stdout + r.stderr)  # pii-ok
            lines = [ln for ln in (root / "inputs" / "margin" /
                                   "opening_2024-12-31.tt").read_text()
                     .splitlines() if ln.startswith("OPENING")]
            self.assertEqual(lines, [
                "OPENING 2024-12-31 ZZA.TO 100 CAD 1050.00",
                "OPENING 2024-12-31 ZZC.TO 50 CAD 200.00"])
            self.assertIn("LOT basis", r.stderr)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            inv = {i["symbol"]: i for i in json.loads(
                (root / "work" / "margin_gains_wash.json").read_text()
            )["inventory"]}
            # The statement's own 2024 buy is replaced by the snapshot.
            self.assertAlmostEqual(inv["ZZA.TO"]["qty"], 100.0)
            self.assertAlmostEqual(inv["ZZC.TO"]["qty"], 50.0)
            # Sanity reads the same statement: quantities and costs tie.
            s = cli(root, "sanity", f"margin={f}", "--json")
            self.assertEqual(s.returncode, 0, s.stdout + s.stderr)
            doc = json.loads(s.stdout)
            self.assertTrue(doc["clean"])
            self.assertEqual(doc["cost"]["matched"], 2)
            self.assertEqual(doc["cost_differences"], [])

    def test_holdings_toml_in_inputs_is_listed_and_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", tt={("margin", "h.tt"): HIST})
            _toml(root / "inputs" / "margin" / "positions.toml", [
                {"symbol": "SAMPB.TO", "quantity": 20, "currency": "CAD",
                 "total_cost": 200.0}], as_of="2025-01-31")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("inputs/margin/positions.toml → positions report "
                          "(holdings TOML, as of 2025-01-31) — not "
                          "activity; skipped", r.stdout)

    def test_clashing_opening_lines_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", tt={
                ("margin", "old.tt"):
                    "OPENING 2024-06-30 SAMPB.TO 5 CAD 50\n"})
            rep = _toml(Path(td) / "pos.toml", [
                {"symbol": "SAMPB.TO", "quantity": 20, "currency": "CAD",
                 "total_cost": 200.0}], as_of="2024-12-31")
            r = cli(root, "opening", "margin", str(rep))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("already has OPENING lines for SAMPB.TO",
                          r.stderr)


class TestOpeningUsa(unittest.TestCase):
    @rule("US-OPEN-01")
    def test_lots_with_dates_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", country="usa", cur="USD",
                            tt={("margin", "h.tt"):
                                "BUYSELL 2025-02-10 09:30:00 SAMPX.US -10 "
                                "USD 8 80 0\n"})
            nodate = _toml(Path(td) / "a.toml", [
                {"symbol": "SAMPX.US", "quantity": 20, "currency": "USD",
                 "total_cost": 200.0}], as_of="2025-01-31")
            r = cli(root, "opening", "margin", str(nodate))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("US-OPEN-01", r.stderr)
            lots = _toml(Path(td) / "b.toml", [
                {"symbol": "SAMPX.US", "quantity": 10, "currency": "USD",
                 "total_cost": 150.0, "acquired": "2019-03-04"},
                {"symbol": "SAMPX.US", "quantity": 10, "currency": "USD",
                 "total_cost": 50.0, "acquired": "2024-11-04"}],
                as_of="2025-01-31")
            r = cli(root, "opening", "margin", str(lots))
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = [ln for ln in (root / "inputs" / "margin" /
                                   "opening_2025-01-31.tt").read_text()
                     .splitlines() if ln.startswith("OPENING")]
            self.assertEqual(lines, [
                "OPENING 2025-01-31 SAMPX.US 10 USD 150.00 2019-03-04",
                "OPENING 2025-01-31 SAMPX.US 10 USD 50.00 2024-11-04"])
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            g = json.loads((root / "work" / "margin_gains_wash.json")
                           .read_text())
            sale = [t for t in g["transactions"]
                    if t.get("symbol") == "SAMPX.US"][0]
            # FIFO: the 2019 lot, long-term, its own basis; no wash sale.
            self.assertEqual(sale["term"], "LONG_TERM")
            self.assertAlmostEqual(sale["gain"], 80 - 150, places=2)
            self.assertFalse(sale.get("is_wash_sale"))
            # Sanity: lot basis against the books' FIFO lots.
            pos = _toml(Path(td) / "now.toml", [
                {"symbol": "SAMPX.US", "quantity": 10, "currency": "USD",
                 "total_cost": 55.0, "cost_kind": "lots"}])
            s = cli(root, "sanity", f"margin={pos}", "--json")
            doc = json.loads(s.stdout)
            self.assertEqual(s.returncode, 0, s.stdout + s.stderr)
            diff = doc["cost_differences"][0]
            self.assertEqual(diff["symbol"], "SAMPX.US")
            self.assertAlmostEqual(diff["diff"], -5.0, places=2)
            self.assertIn("lot-method", diff["reasons"])


def _gains(root, acct, inv, name=None):
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "work" / (name or f"{acct}_gains.json")).write_text(json.dumps(
        {"summary": {"year": "2025"}, "transactions": [],
         "inventory": [dict(symbol=s, qty=q, total_cost=c, currency=cur,
                            deferred_wash=dw)
                       for s, (q, c, cur, dw) in inv.items()]}))


class TestSanityCostCanada(unittest.TestCase):
    """The books' s.47 ACB (pooled over taxable accounts, superficial
    losses added) against one account's broker book value."""

    def _root(self, td):
        root = Path(td) / "p"
        root.mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.cash]\ntype = "taxable"\n')
        # Filing books: SAMPC pooled at 20/sh over both accounts, SAMPD
        # carries a 15.00 denied superficial loss, SAMPF ties.
        _gains(root, "margin", {"SAMPC.TO": (10, 200.0, "CAD", 0.0),
                                "SAMPD.TO": (5, 115.0, "CAD", 15.0),
                                "SAMPF.TO": (3, 30.0, "CAD", 0.0)},
               name="margin_gains_wash.json")
        _gains(root, "margin", {"SAMPC.TO": (10, 100.0, "CAD", 0.0),
                                "SAMPD.TO": (5, 115.0, "CAD", 15.0),
                                "SAMPF.TO": (3, 30.0, "CAD", 0.0)})
        _gains(root, "cash", {"SAMPC.TO": (10, 200.0, "CAD", 0.0)},
               name="cash_gains_wash.json")
        _gains(root, "cash", {"SAMPC.TO": (10, 300.0, "CAD", 0.0)})
        return root

    @rule("CA-OPEN-01")
    def test_pooled_and_superficial_loss_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            pos = _toml(Path(td) / "m.toml", [
                {"symbol": "SAMPC.TO", "quantity": 10, "currency": "CAD",
                 "total_cost": 100.0},
                {"symbol": "SAMPD.TO", "quantity": 5, "currency": "CAD",
                 "total_cost": 100.0},
                {"symbol": "SAMPF.TO", "quantity": 3, "currency": "CAD",
                 "total_cost": 30.4}])
            r = cli(root, "sanity", f"margin={pos}", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertTrue(doc["clean"])
            self.assertEqual(doc["cost"]["matched"], 1)       # SAMPF
            by = {d["symbol"]: d for d in doc["cost_differences"]}
            self.assertEqual(by["SAMPC.TO"]["reasons"], ["pooled"])
            self.assertTrue(by["SAMPC.TO"]["explained"])
            self.assertEqual(by["SAMPD.TO"]["reasons"],
                             ["superficial-loss"])
            self.assertTrue(by["SAMPD.TO"]["explained"])
            t = cli(root, "sanity", f"margin={pos}")
            self.assertEqual(t.returncode, 0)
            self.assertIn("OK: tickers and quantities agree", t.stdout)
            self.assertIn("COST — books vs the reports' cost: 3 compared",
                          t.stdout)
            self.assertIn("pooled: the books average", t.stdout)

    @rule("CA-OPEN-01")
    def test_both_accounts_together_tie(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            pos = _toml(Path(td) / "b.toml", [
                {"symbol": "SAMPC.TO", "quantity": 20, "currency": "CAD",
                 "total_cost": 400.0},
                {"symbol": "SAMPD.TO", "quantity": 5, "currency": "CAD",
                 "total_cost": 115.0},
                {"symbol": "SAMPF.TO", "quantity": 3, "currency": "CAD",
                 "total_cost": 30.0}])
            r = cli(root, "sanity", f"margin+cash={pos}", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual(doc["cost"]["matched"], 3)


class TestIncomeShareCount(unittest.TestCase):
    def _rows(self):
        return [
            {"action": "BUYSELL", "date": "2025-01-06",
             "date_settle": "2025-01-07", "symbol": "SAMPB.TO",
             "quantity": 300.0},
            {"action": "BUYSELL", "date": "2025-03-03",
             "date_settle": "2025-03-04", "symbol": "SAMPB.TO",
             "quantity": 100.0},
            # stated count matches the record-date position (300)
            {"action": "DIVIDEND", "date": "2025-03-15", "symbol": "SAMPB.TO",
             "quantity": 300.0, "record_date": "2025-02-28",
             "description": "DIV - SAMPLE B CASH DIV ON 300 SHS REC "
                            "02/28/25 PAY 03/15/25"},
            # stated 500, the books hold 400 on the record date
            {"action": "DIVIDEND", "date": "2025-06-15", "symbol": "SAMPB.TO",
             "quantity": 500.0, "record_date": "2025-05-30",
             "net_amount": 25.0,
             "description": "DIV - SAMPLE B CASH DIV ON 500 SHS"},
            # no count stated: never compared
            {"action": "DIVIDEND", "date": "2025-06-15", "symbol": "SAMPB.TO",
             "quantity": 7.0, "description": "Cash Dividend CAD 0.05 per "
                                             "Share"},
        ]

    def test_stated_share_count_against_the_books(self):
        out = PC.income_share_mismatches(self._rows(), "margin")
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["stated_shares"], out[0]["books_shares"]),
                         (500.0, 400.0))
        self.assertEqual(out[0]["basis"], "record date")

    def test_sanity_lists_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            root.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _gains(root, "margin", {"SAMPB.TO": (400, 4000.0, "CAD", 0.0)})
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": self._rows()}))
            pos = _toml(Path(td) / "m.toml", [
                {"symbol": "SAMPB.TO", "quantity": 400}])
            r = cli(root, "sanity", f"margin={pos}")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("INCOME ON SHARES THE BOOKS DO NOT HOLD — 1",
                          r.stdout)
            doc = json.loads(cli(root, "sanity", f"margin={pos}",
                                 "--json").stdout)
            self.assertEqual(len(doc["income_share_mismatches"]), 1)
            self.assertTrue(doc["clean"])


if __name__ == "__main__":
    unittest.main()


class TestCompareCost(unittest.TestCase):
    """lib/positions_check.compare_cost: which books a cost is compared
    with, and the reasons."""

    def _cmp(self, **kw):
        args = dict(symbol="SAMPU.US", broker_cost=1000.0,
                    cost_currency="USD", cost_kind="lot basis",
                    position_currency="USD", base="CAD", country="canada",
                    filing={"total_cost": 1440.0, "deferred_wash": 0.0,
                            "currency": "CAD", "qty": 10},
                    own=1440.0,
                    native={"total_cost": 1000.0, "deferred_wash": 0.0,
                            "currency": "USD", "qty": 10},
                    pooled=False, roc=False, tol_abs=1.0, tol_rel=0.001)
        args.update(kw)
        return PC.compare_cost(**args)

    def test_foreign_cost_against_the_native_books(self):
        r = self._cmp()
        self.assertEqual(r["status"], "match")
        self.assertTrue(r["basis"].startswith("native USD books"))
        r = self._cmp(broker_cost=990.0)
        self.assertEqual(r["status"], "differs")
        self.assertEqual(r["reasons"], ["lot-basis"])

    def test_base_currency_book_value_of_a_foreign_listing(self):
        r = self._cmp(broker_cost=1400.0, cost_currency="CAD",
                      cost_kind="average cost")
        self.assertEqual(r["status"], "differs")
        self.assertEqual(r["reasons"], ["broker-fx"])
        self.assertAlmostEqual(r["diff"], 40.0)

    def test_no_books_in_that_currency(self):
        r = self._cmp(native=None)
        self.assertEqual(r["status"], "n/a")
        self.assertIn("no USD books", r["note"])

    def test_tolerance(self):
        self.assertEqual(self._cmp(broker_cost=1000.7)["status"], "match")
        # 0.1% of a large cost
        r = self._cmp(broker_cost=100000.0, native={
            "total_cost": 100080.0, "currency": "USD"})
        self.assertEqual(r["status"], "match")

    def test_return_of_capital_and_unexplained(self):
        r = self._cmp(broker_cost=900.0, roc=True, cost_kind="average cost")
        self.assertEqual(r["reasons"], ["return-of-capital"])
        r = self._cmp(broker_cost=900.0, cost_kind="average cost")
        self.assertEqual(r["reasons"], ["unexplained"])
        self.assertFalse(r["explained"])
