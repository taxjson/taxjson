"""Fix round 7 wave F of 2026-10-30, group misc: regressions of wave E's own
repairs (the fetch one, TJ-1291, is in packages/taxjson-fetch/tests). Every
fixture is SYNTHETIC: made-up tickers, ids and amounts.

TJ-1297  a later plain trade at the strike took a premium the snapshot holds.
TJ-1298  a USD opening on a TSX USD unit was restated into CAD.
TJ-1303  close timing: per-account costs of a written option did not add up.
TJ-1308  ccd-sum and winners counted one tax-year write twice.
TJ-1309  a return of capital's cut cancelled a premium delivered after it.
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from _planted_rates import plant
from _qa_project import gains, project, tj, toml


def _sales(root, sym):
    return [g for g in gains(root) if g.get("qty") and g.get("symbol") == sym]


def _gain(root, sym):
    return round(sum(float(g["gain"]) for g in _sales(root, sym)), 4)


class TestALaterPlainTradeTakesNoPremiumTheSnapshotHolds(unittest.TestCase):
    """TJ-1297: the delivery is cut by a snapshot that holds it: a plain
    trade at the strike just after is not the delivery."""

    OPT = "SYNQ251231C00010000.US"
    PUT = "SYNQ251231P00010000.US"

    def _book(self, cur, buy="BUYSELL 2026-01-05 10:00:00 SYNQ.US 100 {c} 10 "
                            "1000 0\n"):
        return (f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 {cur} 1 100 0\n"
                f"ASSIGN 2025-12-30 16:00:00 {self.OPT} -1 {cur} 0 0 0\n"
                f"ASSIGN 2025-12-30 16:00:00 SYNQ.US 100 {cur} 10 1000 0\n"
                f"OPENING 2025-12-31 SYNQ.US 100 {cur} 1100 2024-06-03\n"
                + buy.format(c=cur))

    def _run(self, country, book):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": book})
            (root / "taxjson.toml").write_text(toml(country, year=2026))
            tj(root, "run", "--no-input")
            return _sales(root, "SYNQ.US")

    @rule("CA-OPEN-03")
    def test_canada_the_item_s_own_book(self):
        rows = self._run("canada", self._book("CAD") + (
            "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n"))
        self.assertAlmostEqual(float(rows[0]["gain"]), 150.0, places=2)

    @rule("CA-OPEN-03")
    def test_canada_sizes_and_dates(self):
        for buy, qty, gain in (
                ("BUYSELL 2026-01-05 10:00:00 SYNQ.US 50 {c} 10 500 0\n",
                 150, 1800 - 1600),
                ("BUYSELL 2026-01-05 10:00:00 SYNQ.US 200 {c} 10 2000 0\n",
                 300, 3600 - 3100),
                ("BUYSELL 2026-01-02 10:00:00 SYNQ.US 100 {c} 10 1000 0\n",
                 200, 2400 - 2100),
                ("BUYSELL 2026-01-06 10:00:00 SYNQ.US 100 {c} 10 1000 0\n",
                 200, 2400 - 2100)):
            with self.subTest(buy):
                rows = self._run("canada", self._book("CAD", buy) + (
                    f"BUYSELL 2026-02-10 10:00:00 SYNQ.US -{qty} CAD 12 "
                    f"{qty * 12} 0\n"))
                self.assertAlmostEqual(float(rows[0]["gain"]), gain, places=2)

    @rule("US-OPEN-03")
    def test_us_later_lot_keeps_its_price(self):
        rows = self._run("usa", self._book("USD") + (
            "BUYSELL 2026-02-10 10:00:00 SYNQ.US -200 USD 12 2400 0\n"))
        self.assertAlmostEqual(sum(float(g["gain"]) for g in rows),
                               2400 - 2100, places=2)
        self.assertAlmostEqual(float(rows[-1]["cost"]), 1000.0, places=2)

    @rule("CA-OPEN-03")
    def test_written_put_cut_with_a_plain_buy(self):
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.PUT} -1 CAD 1 100 0\n"
                f"ASSIGN 2025-12-30 16:00:00 {self.PUT} 1 CAD 0 0 0\n"
                "ASSIGN 2025-12-30 16:00:00 SYNQ.US 100 CAD 10 1000 0\n"
                "OPENING 2025-12-31 SYNQ.US 100 CAD 900 2024-06-03\n"
                "BUYSELL 2026-01-05 10:00:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n")
        rows = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 250.0, places=2)

    @rule("CA-OPEN-03")
    def test_written_call_cut_with_a_plain_sale(self):
        call = "SYNQ251231C00010000.US"
        book = (f"BUYSELL 2024-06-03 10:00:00 {call} -1 CAD 1 100 0\n"
                f"ASSIGN 2025-12-30 16:00:00 {call} 1 CAD 0 0 0\n"
                "ASSIGN 2025-12-30 16:00:00 SYNQ.US -100 CAD 10 1000 0\n"
                "OPENING 2025-12-31 SYNQ.US 200 CAD 1600 2024-06-03\n"
                "BUYSELL 2026-01-05 10:00:00 SYNQ.US -100 CAD 10 1000 0\n")
        rows = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 200.0, places=2)

    @rule("CA-OPEN-03")
    def test_tj_1167_a_snapshot_too_small_to_hold_it_still_gives_way(self):
        # 50 other shares in the snapshot; 100 delivered at the strike
        # after it: the snapshot cannot hold them.
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 CAD 1 100 0\n"
                "OPENING 2025-12-31 SYNQ.US 50 CAD 400 2024-06-03\n"
                f"ASSIGN 2025-12-31 16:00:00 {self.OPT} -1 CAD 0 0 0\n"
                "BUYSELL 2026-01-02 09:30:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n")
        rows = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 200.0, places=2)

    @rule("CA-OPEN-03")
    def test_tj_1167_a_marked_leg_is_the_delivery_whatever_the_snapshot(self):
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 CAD 1 100 0\n"
                "OPENING 2025-12-31 SYNQ.US 200 CAD 1600 2024-06-03\n"
                f"ASSIGN 2025-12-31 16:00:00 {self.OPT} -1 CAD 0 0 0\n"
                "ASSIGN 2026-01-02 09:30:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n")
        rows = self._run("canada", book)
        # A pool of 300 at 1600 + 1100.
        self.assertAlmostEqual(float(rows[0]["gain"]), 1200.0 - 900.0, places=2)


class TestANonBaseOpeningKeepsItsOwnCurrency(unittest.TestCase):
    """TJ-1298: a USD opening on a TSX USD unit with no trade: its currency
    is already the holding's; only a base-currency line is restated
    (TJ-1169)."""

    def _holding(self, opening, sym):
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": opening + (
                "BUYSELL 2026-01-05 10:00:00 SYNR.TO 5 CAD 20 100 0\n")})
            (root / "taxjson.toml").write_text(toml("canada", year=2026))
            text = "".join(f"{y}-{m:02d}-{dd:02d} 16:00:00 USD CAD 1.25 BOC\n"
                           for y, m in ((2025, 12), (2026, 1), (2026, 2))
                           for dd in range(1, 29)) + "".join(
                f"2025-12-{dd} 16:00:00 USD CAD 1.25 BOC\n"
                for dd in (29, 30, 31))
            plant(root / "work" / "to_base.csv", text, cleanup=self.addCleanup)
            r = tj(root, "run", "--no-input")
            doc = tomllib.loads((root / "reports" / "margin_holdings.toml")
                                .read_text())
            return (next(x for x in doc["holding"] if x["symbol"] == sym),
                    r.stdout + r.stderr)

    def test_a_usd_unit_stays_in_usd(self):
        for line, sym in (
                ("OPENING 2025-12-31 SYND.U.TO 100 USD 1000 2024-06-03\n",
                 "SYND.U.TO"),
                ("OPENING 2025-12-31 SYNB.TO 100 USD 1000 2024-06-03\n",
                 "SYNB.TO")):
            with self.subTest(sym):
                h, out = self._holding(line, sym)
                self.assertNotIn("restated", out)
                self.assertEqual(h["currency"], "USD")
                self.assertAlmostEqual(h["total_cost"], 1000.0, places=2)
                self.assertAlmostEqual(h["base_total_cost"], 1250.0, places=2)

    def test_tj_1169_a_base_line_on_a_foreign_listing_is_still_restated(self):
        h, out = self._holding(
            "OPENING 2025-12-31 SYNQ.US 10 CAD 150 2024-06-03\n", "SYNQ.US")
        self.assertIn("restated", out)
        self.assertEqual(h["currency"], "USD")
        self.assertAlmostEqual(h["total_cost"], 120.0, places=2)
        self.assertAlmostEqual(h["base_total_cost"], 150.0, places=2)


_OPT = "SYQ261218P00010000.TO"
_CLOSE = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
          'country = "canada"\nbase_currency = "CAD"\n'
          'source_currencies = ["USD"]\noption_premium_timing = "close"\n'
          '[accounts.a]\ntype = "taxable"\n[accounts.b]\ntype = "taxable"\n'
          '[accounts.c]\ntype = "taxable"\n')


def _open_costs(root, accounts):
    out = {}
    for a in accounts:
        doc = json.loads((root / "work" / f"{a}_gains_wash.json").read_text())
        for i in doc["inventory"]:
            if i["symbol"] == _OPT:
                out[a] = (round(i["qty"], 6), round(i["total_cost"], 2))
    return out


class TestCloseTimingCostsAddUpToThePool(unittest.TestCase):
    """TJ-1303: when any account's units are not its own lots, every account
    goes by share (the fallback TJ-1251 named); otherwise TJ-1251 stands."""

    def _run(self, files):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = project(d.name, "p", files)
        (root / "taxjson.toml").write_text(_CLOSE)
        tj(root, "run", "--no-input")
        return root

    A = f"BUYSELL 2025-01-02 10:00:00 {_OPT} -2 CAD 100 200\n"
    C = f"BUYSELL 2025-01-02 10:00:00 {_OPT} -1 CAD 900 900\n"
    B = (f"BUYSELL 2025-01-02 10:00:00 {_OPT} -2 CAD 300 600\n"
         f"BUYSELL 2025-02-05 10:00:00 {_OPT} 3 CAD 50 150\n")

    @rule("CA-OPT-06")
    def test_one_account_closed_more_than_it_wrote(self):
        root = self._run({"a/t.tt": self.A, "b/t.tt": self.B, "c/t.tt": self.C})
        self.assertEqual(_open_costs(root, "abc"),
                         {"a": (-2.0, -1000.0), "b": (1.0, 500.0),
                          "c": (-1.0, -500.0)})
        self.assertIn("-1,000.00", tj(root, "list").stdout)

    @rule("CA-OPT-06")
    def test_a_long_in_one_account_beside_two_writers(self):
        # b's long meets a's first write in the pool: a's units are not its
        # lots, c's are. They still add up.
        root = self._run({
            "b/t.tt": f"BUYSELL 2025-01-02 09:00:00 {_OPT} 1 CAD 50 50\n",
            "a/t.tt": f"BUYSELL 2025-01-03 10:00:00 {_OPT} -2 CAD 100 200\n",
            "c/t.tt": f"BUYSELL 2025-01-04 10:00:00 {_OPT} -1 CAD 900 900\n"})
        got = _open_costs(root, "abc")
        pool = json.loads((root / "work" / ".blend_gains_wash.json")
                          .read_text())["inventory"]
        total = next(i["total_cost"] for i in pool if i["symbol"] == _OPT)
        self.assertAlmostEqual(sum(c for _q, c in got.values()), total,
                               places=2)

    @rule("CA-OPT-06")
    def test_each_account_with_its_own_lots_keeps_its_premium(self):
        root = self._run({
            "a/t.tt": self.A,
            "b/t.tt": f"BUYSELL 2025-01-02 10:00:00 {_OPT} -2 CAD 300 600\n",
            "c/t.tt": self.C})
        self.assertEqual(_open_costs(root, "abc"),
                         {"a": (-2.0, -200.0), "b": (-2.0, -600.0),
                          "c": (-1.0, -900.0)})

    @rule("CA-OPT-06")
    def test_a_split_one_account_reports_keeps_the_lots(self):
        root = self._run({
            "a/t.tt": self.A + f"SPLIT 2025-03-03 09:30:00 {_OPT} {_OPT} 2\n",
            "b/t.tt": f"BUYSELL 2025-01-02 10:00:00 {_OPT} -2 CAD 300 600\n"})
        self.assertEqual(_open_costs(root, "ab"),
                         {"a": (-4.0, -200.0), "b": (-4.0, -600.0)})

    @rule("CA-OPT-06")
    def test_a_write_closed_in_another_account_at_engine_level(self):
        from taxjson.bin.taxjson_split_gains import _split_inventory

        def row(acct, q):
            return {"action": "BUYSELL", "symbol": _OPT, "quantity": q,
                    "date": "2025-01-02", "date_settle": "2025-01-02",
                    "account": acct}
        rows = [{"symbol": _OPT, "qty": -3.0, "total_cost": -700.0,
                 "account_premiums": {
                     "a": {"units": 1.0, "premium": 100.0},
                     "b": {"units": 2.0, "premium": 600.0}}}]
        pool = [row("a", -1.0), row("b", -1.0), row("c", -1.0)]
        got = [_split_inventory(rows, a, [r for r in pool
                                          if r["account"] == a], {},
                                "9999-12-31", pool_rows=pool)[0]["total_cost"]
               for a in "abc"]
        self.assertAlmostEqual(sum(got), -700.0, places=4)
        self.assertEqual(got[0], got[1])
        # The pool's rows matching every account's lots: their own premium.
        pool = [row("a", -1.0), row("b", -2.0)]
        (one,) = _split_inventory(rows, "a", pool[:1], {}, "9999-12-31",
                                  pool_rows=pool)
        self.assertEqual(one["total_cost"], -100.0)


class TestGrantExpiryIsOneRecordPerWrite(unittest.TestCase):
    """TJ-1308: a tax-year write the year pass expired part of and the full
    history expired more of is one record: the extra goes to qty alone."""

    OPT = "SYN260320C00001000.TO"

    def _views(self, rows):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "m").mkdir(parents=True)
            (root / "inputs" / "m" / "trades.tt").write_text(rows)
            (root / "ticker.map").write_text("")
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                'option_premium_timing = "grant"\n'
                'option_grant_timing_since = 2025\n'
                '[accounts.m]\ntype = "taxable"\n')
            tj(root, "run", "--no-input")

            def view(*a):
                return json.loads(tj(root, *a, "--json").stdout)
            return (view("ccd-sum"), view("ccd-sum", "all"), view("winners"),
                    view("winners", "all"))

    def _check(self, rows):
        ccd, ccd_all, win, win_all = self._views(rows)

        def counts(doc):
            return (sum(r["contracts"] for r in doc["rows"]),
                    sum(r["qty"] for r in doc["rows"]))
        self.assertEqual(counts(ccd), counts(ccd_all))
        self.assertEqual(sum(r["closes"] for r in win["rows"]),
                         sum(r["closes"] for r in win_all["rows"]))
        return ccd

    def test_a_write_for_two_one_year_pass_expiry(self):
        ccd = self._check(
            "BUYSELL 2025-11-03 10:00:00 SYN.TO 400 CAD 1 400 0\n"
            f"BUYSELL 2025-12-01 10:00:00 {self.OPT} -1 CAD 0.1 10 0\n"
            f"BUYSELL 2026-01-05 10:00:00 {self.OPT} -2 CAD 0.1 20 0\n"
            f"BUYSELL 2026-02-02 10:00:00 {self.OPT} 1 CAD 0.05 5 0\n"
            f"BUYSELL 2026-03-20 10:00:00 {self.OPT} 2 CAD 0 0 0\n")
        self.assertEqual(ccd["rows"][0]["contracts"], 2)
        self.assertEqual(ccd["rows"][0]["qty"], 3.0)
        self.assertEqual(ccd["total_gain"], 15.0)

    def test_two_writes_of_two(self):
        ccd = self._check(
            "BUYSELL 2025-11-03 10:00:00 SYN.TO 400 CAD 1 400 0\n"
            f"BUYSELL 2025-12-01 10:00:00 {self.OPT} -2 CAD 0.1 20 0\n"
            f"BUYSELL 2026-01-05 10:00:00 {self.OPT} -2 CAD 0.1 20 0\n"
            f"BUYSELL 2026-02-02 10:00:00 {self.OPT} 1 CAD 0.05 5 0\n"
            f"BUYSELL 2026-03-20 10:00:00 {self.OPT} 3 CAD 0 0 0\n")
        self.assertEqual(ccd["rows"][0]["contracts"], 3)


class TestACutOnlyCoversAPremiumAlreadyInTheACB(unittest.TestCase):
    """TJ-1309: a premium delivered after the return of capital's cut was
    not in the ACB it cut, so it still comes off the sale."""

    HEAD = ("BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 1 100 0\n"
            "BUYSELL 2025-01-10 10:00:00 SYN.TO -100 CAD .8 80 0\n"
            "BUYSELL 2025-01-13 10:00:00 SYN.TO 100 CAD 1 100 0\n")
    PUT = ("BUYSELL 2025-04-01 10:00:00 SYN250516P00001000.TO -1 CAD .2 20 0\n"
           "ASSIGN 2025-05-16 09:30:00 SYN250516P00001000.TO 1 CAD 0 0 0\n"
           "ASSIGN 2025-05-16 10:00:00 SYN.TO 100 CAD 1 100 0\n")
    SALE = "BUYSELL 2025-07-02 10:00:00 SYN.TO -200 CAD .5 100 0\n"

    def _stats(self, rows, accounts=None, year=2025):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            trades = accounts or {"m": rows}
            for a, text in trades.items():
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / "trades.tt").write_text(text)
            (root / "ticker.map").write_text("")
            (root / "taxjson.toml").write_text(
                f'[settings]\nyear = {year}\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                + "".join(f'[accounts.{a}]\ntype = "taxable"\n'
                          for a in trades))
            tj(root, "run", "--no-input")
            return json.loads(tj(root, "stats", str(year), "--json").stdout)

    @staticmethod
    def _pl(doc, cls=None):
        if cls is None:
            return doc["total"]["net_pl"]
        return (doc["classes"].get(cls) or {"net_pl": 0.0})["net_pl"]

    def _control(self, rows):
        return self._stats(rows + 'ALLOWLOSS 2025-01-10 SYN.TO '
                                  'reason="control"\n')

    @rule("CA-RPT-16")
    def test_the_item_s_own_book(self):
        for roc in (110, 115):
            with self.subTest(roc=roc):
                rows = (self.HEAD + f"ADJUST 2025-03-03 10:00:00 SYN.TO CAD "
                        f"-{roc} type=roc\n" + self.PUT + self.SALE)
                doc = self._stats(rows)
                self.assertEqual(self._pl(doc, "long-shares"), -20.0)
                self.assertEqual(self._pl(doc, "written-options"), 20.0)
                self.assertEqual(self._pl(doc), 0.0)
                self.assertEqual(self._pl(self._control(rows)), 0.0)

    @rule("CA-RPT-16")
    def test_the_sale_in_two_parts(self):
        rows = (self.HEAD + "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -110 "
                "type=roc\n" + self.PUT
                + "BUYSELL 2025-07-02 10:00:00 SYN.TO -100 CAD .5 50 0\n"
                "BUYSELL 2025-07-03 10:00:00 SYN.TO -100 CAD .5 50 0\n")
        self.assertEqual(self._pl(self._stats(rows)), 0.0)

    @rule("CA-RPT-16")
    def test_a_rename_after_the_delivery(self):
        rows = (self.HEAD + "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -110 "
                "type=roc\n" + self.PUT
                + "SPLIT 2025-06-15 09:30:00 SYN.TO SYQ.TO 2\n"
                "BUYSELL 2025-07-02 10:00:00 SYQ.TO -400 CAD .25 100 0\n")
        self.assertEqual(self._pl(self._stats(rows)), 0.0)

    @rule("CA-RPT-16")
    def test_a_year_end_between_the_cut_and_the_sale(self):
        rows = (self.HEAD + "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -110 "
                "type=roc\n" + self.PUT
                + "BUYSELL 2026-02-02 10:00:00 SYN.TO -200 CAD .5 100 0\n")
        doc = self._stats(rows, year=2026)
        self.assertEqual(self._pl(doc, "long-shares"), 0.0)

    @rule("CA-RPT-16")
    def test_two_accounts_of_one_pool(self):
        doc = self._stats("", accounts={
            "a": self.HEAD + "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -110 "
                 "type=roc\n",
            "b": self.PUT + self.SALE})
        self.assertEqual(self._pl(doc), 0.0)

    @rule("CA-RPT-16")
    def test_tj_1217_a_premium_in_the_acb_at_the_cut_is_still_covered(self):
        # A put delivered before the return of capital, one after.
        rows = ("BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-01-10 10:00:00 SYN.TO -100 CAD .8 80 0\n"
                "BUYSELL 2025-01-02 10:00:00 SYN250117P00001000.TO -1 CAD .2 "
                "20 0\n"
                "ASSIGN 2025-01-17 09:30:00 SYN250117P00001000.TO 1 CAD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -100 type=roc\n"
                + self.PUT + self.SALE)
        # Cash -100 +80 +20 -100 +100 +20 -100 +100.
        self.assertEqual(self._pl(self._stats(rows)), 20.0)

    @rule("CA-RPT-16")
    def test_a_written_call_s_premium_in_the_proceeds(self):
        rows = (self.HEAD + "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -110 "
                "type=roc\n"
                "BUYSELL 2025-04-01 10:00:00 SYN250516C00001000.TO -1 CAD .2 "
                "20 0\n"
                "ASSIGN 2025-05-16 09:30:00 SYN250516C00001000.TO 1 CAD 0 0 0\n"
                "ASSIGN 2025-05-16 10:00:00 SYN.TO -100 CAD 1 100 0\n")
        self.assertEqual(self._pl(self._stats(rows)),
                         self._pl(self._control(rows)))

    @rule("CA-RPT-16")
    def test_a_second_return_of_capital_takes_the_later_premium_in(self):
        # The later premium was in the ACB the second cut read.
        rows = (self.HEAD + "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -110 "
                "type=roc\n" + self.PUT
                + "ADJUST 2025-06-02 10:00:00 SYN.TO CAD -90 type=roc\n"
                + self.SALE)
        doc = self._stats(rows)
        self.assertEqual(self._pl(doc), self._pl(self._control(rows)))


if __name__ == "__main__":
    unittest.main()
