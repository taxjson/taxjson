"""Backlog fix round 7 (wave E) of 2026-10-29, group `planning`:
buy-check, sell-check, edge-cases, harvest, radar and option-boundary.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ALT*, ZZ*), made-up accounts, small amounts.
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj
from test_fix_1029_planning import _toml
from test_fix_1029_r7d_edgeplan import _edge, _t
from test_fix_1029_r7d_harvest import _project, _quotes
from test_fix_backlog_radar import _radar, _row

from taxjson.bin.taxjson_harvest import (_registered_age_out,
                                         _registered_backed_fraction)
from taxjson.lib.option_boundary import straddling


def _ib_statement(*trades):
    return ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,U1234567\n'
            'Account Information,Data,Base Currency,CAD\n'
            'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
            'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
            'Realized P/L,MTM P/L,Code\n' + "".join(trades))


def _edges_of(window_edges, day):
    return [i for e in window_edges for i in e["items"] if i["date"] == day]


class TestWindowEdgesMarkedShort(unittest.TestCase):
    """TJ-1204, TJ-1207: the engine keeps a broker-marked short in its
    own pool (CA-DISP-04), so it neither nets against another account's
    long nor is a sale of held units. Rows are the engine's own, through
    the same `_edge` driver as the window review's other tests."""
    S = "SYNQ.TO"

    def _qt(self, *extra):
        return [_t("qt", "BUYSELL", "2025-02-03", self.S, 20, -400.0),
                _t("qt", "BUYSELL", "2025-06-02", self.S, -10, 100.0),
                *extra]

    def _marked(self, day, qty=-10, code="O", acct="ibk"):
        return _t(acct, "BUYSELL", day, self.S, qty, -qty * 25.0,
                  open_close=code)

    def test_tj1204_long_sale_beside_an_open_marked_short_is_a_sale(self):
        qt = self._qt(_t("qt", "BUYSELL", "2025-07-03", self.S, -10, 100.0))
        _doc, edge, _text = _edge(
            {"qt": qt, "ibk": [self._marked("2025-01-10")]})
        (it,) = _edges_of(edge["window_edges"], "2025-07-03")
        self.assertEqual(it["kind"], "sale", it)
        self.assertEqual(it["qty"], -10.0)
        self.assertNotIn("opened a short", it["why"])
        self.assertIn("after day 30", it["why"])

    def test_tj1207_marked_short_after_day_30_is_a_short_sale(self):
        for code in ("O", "C;O"):
            with self.subTest(code=code):
                _doc, edge, _text = _edge(
                    {"qt": self._qt(),
                     "ibk": [self._marked("2025-07-03", code=code)]})
                (it,) = _edges_of(edge["window_edges"], "2025-07-03")
                self.assertEqual(it["kind"], "short sale", it)
                self.assertEqual(it["qty"], -10.0)
                self.assertNotIn("still held", it["why"])

    def test_tj1207_rbc_marked_short_is_a_short_sale(self):
        rbc = _t("ibk", "BUYSELL", "2025-07-03", self.S, -10, 250.0,
                 description="SYNQ SHORT.")
        _doc, edge, _text = _edge({"qt": self._qt(), "ibk": [rbc]})
        (it,) = _edges_of(edge["window_edges"], "2025-07-03")
        self.assertEqual(it["kind"], "short sale", it)

    def test_tj1207_crossing_sale_splits_at_the_accounts_own_long(self):
        # C;O past the account's own long of 4: 4 sold, 6 opened short,
        # whatever the other account holds.
        rows = [_t("ibk", "BUYSELL", "2025-02-03", self.S, 4, -80.0),
                self._marked("2025-07-03", code="C;O")]
        _doc, edge, _text = _edge({"qt": self._qt(), "ibk": rows})
        (it,) = _edges_of(edge["window_edges"], "2025-07-03")
        self.assertEqual((it["kind"], it["qty"]), ("sale", -4.0), it)
        self.assertEqual(it["opens_short"], 6.0)
        self.assertIn("the other 6 sold opened a short", it["why"])

    def test_tj1204_cover_of_a_marked_short_is_not_held_units(self):
        # ibk's short is covered in full: no units join the long pool,
        # so qt's unmarked sale past its own long still opens a short.
        ibk = [self._marked("2025-01-10"),
               _t("ibk", "BUYSELL", "2025-03-03", self.S, 10, -200.0,
                  open_close="C")]
        qt = [_t("qt", "BUYSELL", "2025-02-03", self.S, 10, -200.0),
              _t("qt", "BUYSELL", "2025-06-02", self.S, -10, 100.0),
              _t("qt", "BUYSELL", "2025-07-03", self.S, -10, 100.0)]
        _doc, edge, _text = _edge({"qt": qt, "ibk": ibk})
        (it,) = _edges_of(edge["window_edges"], "2025-07-03")
        self.assertEqual(it["kind"], "short sale", it)

    def test_control_unmarked_books_keep_tj1014(self):
        # No mark anywhere: the pooled reading, as TJ-1014 fixed it.
        qt = [_t("qt", "BUYSELL", "2025-02-03", self.S, 10, -200.0),
              _t("qt", "BUYSELL", "2025-06-02", self.S, -10, 100.0),
              _t("qt", "BUYSELL", "2025-07-03", self.S, -10, 100.0)]
        _doc, edge, _text = _edge({"qt": qt})
        (it,) = _edges_of(edge["window_edges"], "2025-07-03")
        self.assertEqual(it["kind"], "short sale", it)

    def test_registered_sale_reads_its_own_position(self):
        # A sale in a registered account beside a marked short elsewhere.
        rrsp = [_t("rrsp", "BUYSELL", "2025-02-03", self.S, 10, -200.0),
                _t("rrsp", "BUYSELL", "2025-07-03", self.S, -10, 100.0)]
        _doc, edge, _text = _edge(
            {"qt": self._qt(), "ibk": [self._marked("2025-01-10")]},
            {"rrsp": rrsp})
        (it,) = _edges_of(edge["window_edges"], "2025-07-03")
        self.assertEqual(it["kind"], "sale", it)
        self.assertEqual(it["account"], "rrsp")


class TestWindowEdgesMarkedShortCli(unittest.TestCase):
    """TJ-1204, TJ-1207 from the repro's own files: an IB row coded O in
    one account, the long in a .tt book of the other."""

    def _edge_doc(self, ibk_day, qt_extra=""):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "taxjson.toml").write_text(
                _toml(year=2025).replace("accounts.margin", "accounts.qt")
                + '[accounts.ibk]\ntype = "taxable"\n')
            (root / "inputs" / "qt").mkdir(parents=True)
            (root / "inputs" / "ibk").mkdir(parents=True)
            (root / "inputs" / "qt" / "m.tt").write_text(
                "BUYSELL 2025-02-03 10:00:00 SYNQ.TO 20 CAD 20 -400 0\n"
                "BUYSELL 2025-06-02 10:00:00 SYNQ.TO -10 CAD 10 100 0\n"
                + qt_extra)
            (root / "inputs" / "ibk" / "statement.csv").write_text(
                _ib_statement(
                    'Trades,Data,Order,Stocks,CAD,SYNQ,'
                    f'"{ibk_day}, 10:00:00",-10,25.00,25.00,250.00,0,'
                    '-250.00,0,0,O\n'))
            tj(root, "run", "--no-input")
            return json.loads(
                tj(root, "edge-cases", "--json").stdout)["window_edges"]

    def test_tj1207_short_after_day_30(self):
        items = [i for e in self._edge_doc("2025-07-03")
                 for i in e["items"] if i["account"] == "ibk"]
        self.assertEqual([i["kind"] for i in items], ["short sale"], items)

    def test_tj1204_sale_beside_earlier_short(self):
        we = self._edge_doc(
            "2025-01-10",
            "BUYSELL 2025-07-03 10:00:00 SYNQ.TO -10 CAD 10 100 0\n")
        items = [i for e in we for i in e["items"] if i["account"] == "qt"
                 and i["date"] == "2025-07-03"]
        self.assertEqual([i["kind"] for i in items], ["sale"], items)
        self.assertEqual(items[0]["qty"], -10.0)


class TestWindowEdgesSplitOneAccount(unittest.TestCase):
    """TJ-1205: the engine applies a split or rename one taxable account
    reports to the whole pool (TJ-0696), so a sale of the split units in
    another account opens no short."""

    def _run(self, a_split, b_rows, b_split=""):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "taxjson.toml").write_text(
                _toml(year=2025).replace("accounts.margin", "accounts.a")
                + '[accounts.b]\ntype = "taxable"\n')
            for n in "ab":
                (root / "inputs" / n).mkdir(parents=True)
            (root / "inputs" / "a" / "m.tt").write_text(
                a_split + "BUYSELL 2025-01-05 10:00:00 ZZZ.TO 1 CAD 20 -20 0\n")
            (root / "inputs" / "b" / "m.tt").write_text(b_split + b_rows)
            tj(root, "run", "--no-input")
            we = json.loads(tj(root, "edge-cases", "--json").stdout)
            listed = tj(root, "list").stdout
            return we["window_edges"], listed

    _HEAD = ("BUYSELL 2025-01-02 10:00:00 SYN.TO 40 CAD 20 -800 0\n"
             "BUYSELL 2025-06-02 10:00:00 SYN.TO -20 CAD 10 200 0\n")
    _SPLIT = "SPLIT 2025-06-10 09:30:00 SYN.TO SYN.TO 2\n"

    def _sale(self, we, day="2025-07-03"):
        (it,) = _edges_of(we, day)
        return it

    def test_tj1205_split_in_another_account(self):
        we, listed = self._run(
            self._SPLIT,
            self._HEAD + "BUYSELL 2025-07-03 10:00:00 SYN.TO -40 CAD 5 200 0\n")
        it = self._sale(we)
        self.assertEqual((it["kind"], it["qty"]), ("sale", -40.0), it)
        self.assertNotIn("opened a short", it["why"])
        self.assertNotIn("SYN.TO", listed)

    def test_tj1205_control_same_split_in_both_counts_once(self):
        we, _ = self._run(
            self._SPLIT,
            self._HEAD + "BUYSELL 2025-07-03 10:00:00 SYN.TO -40 CAD 5 200 0\n",
            b_split=self._SPLIT)
        it = self._sale(we)
        self.assertEqual((it["kind"], it["qty"]), ("sale", -40.0), it)
        self.assertNotIn("opens_short", it)

    def test_tj1205_both_accounts_report_and_the_sale_oversells(self):
        # Counted once: 40 held after the split, so 50 opens a short of
        # 10. Scaling the pool twice would hide the short.
        we, _ = self._run(
            self._SPLIT,
            self._HEAD + "BUYSELL 2025-07-03 10:00:00 SYN.TO -50 CAD 5 250 0\n",
            b_split=self._SPLIT)
        it = self._sale(we)
        self.assertEqual((it["qty"], it["opens_short"]), (-40.0, 10.0), it)

    def test_tj1205_real_short_past_the_split_units_is_still_named(self):
        # One account reports it; 50 sold of the 40 held opens 10 short.
        we, listed = self._run(
            self._SPLIT,
            self._HEAD + "BUYSELL 2025-07-03 10:00:00 SYN.TO -50 CAD 5 250 0\n")
        it = self._sale(we)
        self.assertEqual((it["qty"], it["opens_short"]), (-40.0, 10.0), it)
        self.assertIn("SYN.TO", listed)

    def test_tj1205_rename_in_another_account(self):
        we, listed = self._run(
            "SPLIT 2025-05-15 09:30:00 SYN.TO NEWS.TO 1\n",
            "BUYSELL 2025-01-02 10:00:00 SYN.TO 40 CAD 20 -800 0\n"
            "BUYSELL 2025-06-02 10:00:00 NEWS.TO -20 CAD 10 200 0\n"
            "BUYSELL 2025-07-03 10:00:00 NEWS.TO -20 CAD 5 100 0\n")
        it = self._sale(we)
        self.assertEqual((it["kind"], it["qty"]), ("sale", -20.0), it)
        self.assertNotIn("NEWS.TO", listed)


class TestRadarRenameAcrossMarkets(unittest.TestCase):
    """TJ-1241: a rename target with no trade of its own takes the old
    listing's currencies only on the same market. Keeps TJ-1047 (the
    US-dollar unit renamed on the TSX stays on the joint calendar)."""

    def _rename(self, old, new, **kw):
        rows = [_row("2026-01-05", old, 100, 10.0, settle="2026-01-06"),
                _row("2026-05-19", old, 100, 9.0, settle="2026-05-20"),
                {"action": "SPLIT", "date": "2026-05-25", "time": "00:00:00",
                 "date_settle": "2026-05-25", "symbol": old,
                 "symbol_new": new, "quantity": 1.0, "currency": "CAD",
                 "account": "margin", "description": "rename", "id": "rn1"}]
        for r in rows:
            r.update(kw)
        return rows

    def _clears(self, old, new, **kw):
        return _radar("canada", self._rename(old, new, **kw),
                      as_of="2026-06-01")[new]["clears_at"]

    def test_tj1241_us_listing_into_tsx_is_on_the_canadian_calendar(self):
        self.assertEqual(
            self._clears("ZZOLD.US", "ZZNEW.TO", trade_currency="USD"),
            "2026-06-19")

    def test_tj1047_same_market_us_dollar_unit_keeps_the_joint_calendar(self):
        self.assertEqual(
            self._clears("ZZOLD.TO", "ZZNEW.TO", trade_currency="USD"),
            "2026-06-18")

    def test_tj1241_chain_through_a_tsx_listing(self):
        rows = self._rename("ZZOLD.US", "ZZMID.TO", trade_currency="USD")
        rows.append(dict(rows[2], date="2026-05-26", date_settle="2026-05-26",
                         symbol="ZZMID.TO", symbol_new="ZZNEW.TO", id="rn2"))
        r = _radar("canada", rows, as_of="2026-06-01")["ZZNEW.TO"]
        self.assertEqual(r["clears_at"], "2026-06-19")

    def test_tj1241_tsx_listing_into_us_keeps_the_us_market(self):
        self.assertEqual(self._clears("ZZOLD.TO", "ZZNEW.US"),
                         self._clears("ZZNEW.US", "ZZNEW2.US"))


def _harvest_project(tmp, accounts, books):
    root = _project(tmp, accounts)
    for acct, text in books.items():
        (root / "inputs" / acct / "trades.tt").write_text(text)
    tj(root, "run", "--no-input")
    return root


def _harvestable(root, symbol="ZZSYN.TO", price=10.0):
    _quotes(root, {symbol: price})
    doc = json.loads(tj(root, "harvest", "--no-ibkr", "--json").stdout)
    return doc["totals"]["harvestable"]


def _sales_today(root, acct):
    doc = json.loads((root / "work" / f"{acct}_gains_wash.json").read_text())
    today = date.today().isoformat()
    return [t for t in doc["transactions"] if t.get("date") == today]


def _d(n):
    return (date.today() - timedelta(days=n)).isoformat()


class TestHarvestRegisteredCall(unittest.TestCase):
    """TJ-1177: a registered long call bought in the window of a loss
    sale today backs the shares (s.54 para (i)), so its part of the loss
    waits for the call's buy to age out, as shares do. The engine agrees:
    the actual sale's loss is denied for good."""

    def _call(self):
        yy = (date.today().year + 1) % 100
        return f"ZZSYN{yy}1218C00010000.TO"

    def _books(self, sold_today=False, call_days=5, reg_shares=""):
        margin = (f"BUYSELL {_d(150)} 10:00:00 ZZSYN.TO 200 CAD 20 4000 0\n"
                  f"BUYSELL {_d(10)} 10:00:00 ZZSYN.TO -100 CAD 10 1000 0\n")
        if sold_today:
            margin += f"BUYSELL {_d(0)} 10:00:00 ZZSYN.TO -100 CAD 10 1000 0\n"
        return {"margin": margin,
                "registered": (f"BUYSELL {_d(call_days)} 10:00:00 "
                               f"{self._call()} 1 CAD 2 200 0\n" + reg_shares)}

    _ACC = {"margin": "taxable", "registered": "sheltered"}

    def test_tj1177_call_waits_like_shares(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, self._ACC, self._books())
            h = _harvestable(root)
        self.assertAlmostEqual(h["now"], 0.0)
        self.assertAlmostEqual(h["14d"], 0.0)
        self.assertAlmostEqual(h["30d"], 1000.0)

    def test_tj1177_the_engine_agrees_on_the_actual_sale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, self._ACC,
                                    self._books(sold_today=True))
            (sale,) = _sales_today(root, "margin")
        self.assertAlmostEqual(sale["permanently_disallowed"], 1000.0)

    def test_tj1177_a_later_share_buy_decides_when_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, self._ACC, self._books(
                call_days=25,
                reg_shares=f"BUYSELL {_d(2)} 11:00:00 ZZSYN.TO 100 CAD 10 "
                           "1000 0\n"))
            h = _harvestable(root)
        self.assertAlmostEqual(h["14d"], 0.0)
        self.assertAlmostEqual(h["30d"], 1000.0)

    def test_tj1177_older_call_decides_the_earlier_date(self):
        # 25 days ago: still in the window of a sale today however far
        # off its settlement is, and out of it within 7 days.
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, self._ACC,
                                    self._books(call_days=25))
            h = _harvestable(root)
        self.assertAlmostEqual(h["now"], 0.0)
        self.assertAlmostEqual(h["7d"], 1000.0)

    def test_tj1177_call_in_the_taxable_account_stays_claimable(self):
        # A taxable call backs a denial a full exit rescues.
        books = self._books()
        books["margin"] += books.pop("registered")
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, {"margin": "taxable"}, books)
            h = _harvestable(root)
        self.assertAlmostEqual(h["now"], 1000.0)

    def test_tj1177_sidecar_without_the_field_reads_as_before(self):
        row = {"sheltered_known": True, "sheltered_last_add": None,
               "radar": {"category": "VIOLATION"}}
        self.assertEqual(_registered_age_out(row), date.min.isoformat())
        row["radar"]["registered_window_last_add"] = "2026-01-10"
        self.assertEqual(_registered_age_out(row), "2026-02-10")
        row["sheltered_known"] = False
        self.assertIsNone(_registered_age_out(row))


class TestHarvestRegisteredPerAccount(unittest.TestCase):
    """TJ-1242: the engine applies the registered units to each sale up
    to that sale's own quantity (CA-SL-08), so the fraction is of each
    account's row, not of every account's."""
    _ACC = {"margin": "taxable", "margin2": "taxable",
            "registered": "sheltered"}

    def _books(self, reg=5, m2_today=False, m1_today=False):
        m1 = (f"BUYSELL {_d(150)} 10:00:00 ZZSYN.TO 20 CAD 20 400 0\n"
              f"BUYSELL {_d(10)} 10:00:00 ZZSYN.TO -10 CAD 10 100 0\n")
        m2 = f"BUYSELL {_d(150)} 10:00:00 ZZSYN.TO 10 CAD 20 200 0\n"
        today = f"BUYSELL {_d(0)} 10:00:00 ZZSYN.TO -10 CAD 10 100 0\n"
        return {"margin": m1 + (today if m1_today else ""),
                "margin2": m2 + (today if m2_today else ""),
                "registered": f"BUYSELL {_d(5)} 10:00:00 ZZSYN.TO {reg} CAD "
                              f"10 {10 * reg} 0\n"}

    def _now(self, reg):
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, self._ACC, self._books(reg))
            return _harvestable(root)

    def test_tj1242_each_account_keeps_its_own_part(self):
        h = self._now(5)
        self.assertAlmostEqual(h["now"], 100.0)
        self.assertAlmostEqual(h["30d"], 200.0)

    def test_tj1242_more_registered_units_than_one_account_holds(self):
        h = self._now(15)
        self.assertAlmostEqual(h["now"], 0.0)
        self.assertAlmostEqual(h["30d"], 200.0)

    def test_tj1242_the_engine_agrees_on_each_sale(self):
        for kw, acct in (({"m1_today": True}, "margin"),
                         ({"m2_today": True}, "margin2")):
            with self.subTest(acct=acct), tempfile.TemporaryDirectory() as tmp:
                root = _harvest_project(tmp, self._ACC, self._books(5, **kw))
                (sale,) = _sales_today(root, acct)
                self.assertAlmostEqual(sale["permanently_disallowed"], 50.0)
                self.assertAlmostEqual(sale["gain"], -50.0)

    def test_tj1242_row_without_a_quantity_reads_the_class(self):
        rec = {"taxable_qty": 20.0, "registered_window_qty": 5.0}
        self.assertEqual(_registered_backed_fraction(rec), 0.25)
        self.assertEqual(_registered_backed_fraction(rec, 10.0), 0.5)
        self.assertEqual(_registered_backed_fraction(rec, 4.0), 1.0)
        self.assertEqual(_registered_backed_fraction(rec, -10.0), 0.5)


class TestHarvestMissingShelteredBook(unittest.TestCase):
    """TJ-1206: a saved radar of an earlier day sends harvest to a live
    radar; without work/sheltered_base.json that radar drops a registered
    buy's LOCKED units. Harvest refuses, as the checks do (TJ-0350)."""
    _ACC = {"margin": "taxable", "rrsp": "sheltered"}

    def _root(self, tmp):
        root = _harvest_project(tmp, self._ACC, {
            "margin": f"BUYSELL {_d(120)} 10:00:00 SYN.TO 10 CAD 20 200 0\n",
            "rrsp": f"BUYSELL {_d(14)} 10:00:00 SYN.TO 5 CAD 10 50 0\n"})
        _quotes(root, {"SYN.TO": 10.0})
        return root

    def _age_radar(self, root):
        p = root / "reports" / "wash_radar_margin.json"
        doc = json.loads(p.read_text())
        doc["as_of_date"] = _d(2)
        p.write_text(json.dumps(doc))

    def test_tj1206_earlier_day_radar_without_the_book_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self._age_radar(root)
            (root / "work" / "sheltered_base.json").unlink()
            r = tj(root, "harvest", "--no-ibkr", "--json", check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no sheltered_base.json", " ".join(r.stderr.split()))
        self.assertIn("tjs run", " ".join(r.stderr.split()))

    def test_tj1206_with_the_book_the_units_stay_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self._age_radar(root)
            doc = json.loads(
                tj(root, "harvest", "--no-ibkr", "--json").stdout)
        self.assertAlmostEqual(doc["totals"]["harvestable"]["now"], 50.0)

    def test_tj1206_todays_radar_needs_no_live_run(self):
        # Nothing to bring up to date: the saved LOCKED row stands.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "work" / "sheltered_base.json").unlink()
            doc = json.loads(
                tj(root, "harvest", "--no-ibkr", "--json").stdout)
        self.assertAlmostEqual(doc["totals"]["harvestable"]["now"], 50.0)

    def test_tj1206_no_registered_account_is_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, {"margin": "taxable"}, {
                "margin": f"BUYSELL {_d(120)} 10:00:00 SYN.TO 10 CAD 20 "
                          "200 0\n"})
            _quotes(root, {"SYN.TO": 10.0})
            self._age_radar(root)
            doc = json.loads(
                tj(root, "harvest", "--no-ibkr", "--json").stdout)
        self.assertAlmostEqual(doc["totals"]["harvestable"]["now"], 100.0)


class TestOptionShareLevelAlias(unittest.TestCase):
    """TJ-1172: ticker.map's share line (GLOBAL SYN.TO ALT.TO) books
    SYN<series>.TO as ALT<series>.TO. buy-check and sell-check of the
    SYN spelling dropped the loss and answered SAFE. Keeps TJ-1000 (the
    option-level line, test_fix_1029_r7c_planning)."""

    def _project(self, tmp, suffix=".TO", country="canada", tt=None,
                 map_line=None, cur="CAD"):
        ex = (date.today() + timedelta(days=400)).strftime("%y%m%d")
        syn = f"SYN{ex}P00001000{suffix}"
        alt = f"ALT{ex}P00001000{suffix}"
        root = Path(tmp)
        (root / "taxjson.toml").write_text(_toml(country))
        (root / "ticker.map").write_text(
            map_line if map_line is not None
            else f"GLOBAL SYN{suffix} ALT{suffix}\n")
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            tt.format(syn=syn, alt=alt) if tt else
            f"BUYSELL {_d(60)} 10:00:00 {syn} 1 {cur} .2 20 0\n"
            f"BUYSELL {_d(9)} 10:00:00 {syn} -1 {cur} .1 10 0\n")
        tj(root, "run", "--no-input")
        return root, syn, alt

    def _check(self, root, cmd, sym):
        r = tj(root, cmd, sym, "--json", check=False)
        return r.returncode, json.loads(r.stdout)["results"][0]

    def _both_alike(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            root, syn, alt = self._project(tmp, **kw)
            rc_a, a = self._check(root, "buy-check", alt)
            rc_s, s = self._check(root, "buy-check", syn)
            self.assertEqual((rc_a, a["verdict"]), (1, "UNSAFE"), a)
            self.assertEqual((rc_s, s["verdict"]), (1, "UNSAFE"), s)
            self.assertEqual(s["clears_at"], a["clears_at"])
            self.assertEqual(s["last_loss"]["date"], a["last_loss"]["date"])
            _rc, sa = self._check(root, "sell-check", alt)
            _rc, ss = self._check(root, "sell-check", syn)
            self.assertEqual(ss["last_loss"], sa["last_loss"])
            self.assertIsNotNone(ss["last_loss"])

    def test_tj1172_canada(self):
        self._both_alike()

    def test_tj1172_usa(self):
        self._both_alike(suffix=".US", country="usa", cur="USD")

    def test_tj1172_both_spellings_booked_stay_two_contracts(self):
        # The engine keeps a collision apart: the SYN contract is its
        # own, and only the ALT one has the loss.
        tt = (f"BUYSELL {_d(60)} 10:00:00 {{alt}} 1 CAD .2 20 0\n"
              f"BUYSELL {_d(9)} 10:00:00 {{alt}} -1 CAD .1 10 0\n"
              f"BUYSELL {_d(60)} 10:00:00 {{syn}} 1 CAD .1 10 0\n"
              f"BUYSELL {_d(30)} 10:00:00 {{syn}} -1 CAD .2 20 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root, syn, alt = self._project(tmp, tt=tt)
            _rc, s = self._check(root, "buy-check", syn)
            _rc, a = self._check(root, "buy-check", alt)
        self.assertEqual(s["verdict"], "SAFE", s)
        self.assertEqual(a["verdict"], "UNSAFE", a)

    def test_tj1172_another_root_stays_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, syn, _alt = self._project(tmp)
            _rc, r = self._check(root, "buy-check",
                                 syn.replace("SYN", "OTHER"))
        self.assertEqual(r["verdict"], "SAFE", r)

    def test_tj1172_another_series_of_the_root_stays_clean(self):
        # A put on another strike is another contract (its right is a
        # put: no replacement of the first put).
        with tempfile.TemporaryDirectory() as tmp:
            root, syn, _alt = self._project(tmp)
            _rc, r = self._check(root, "buy-check",
                                 syn.replace("00001000", "00002000"))
        self.assertEqual(r["verdict"], "SAFE", r)

    def test_tj1172_no_map_line_two_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _syn, alt = self._project(tmp, map_line="")
            _rc, r = self._check(root, "buy-check", alt)
        self.assertEqual(r["verdict"], "SAFE", r)


class TestAmendedFigureIsTheLocksYear(unittest.TestCase):
    """TJ-1176: a lock's figure for a series and day holds only the rows
    that land in the lock's year. Keeps TJ-1046 (a row of the lock's own
    year whose other date it is, test_fix_1029_r7d_edgeplan)."""
    P = "SYN270115P00001000.TO"

    def _row(self, trade, settle, qty, net):
        return _t("margin", "BUYSELL", trade, self.P, qty, net, settle=settle,
                  id=f"r-{trade}-{settle}-{qty}-{net}")

    def _assign(self, day, n=1):
        return [_t("margin", "ASSIGN", day, self.P, n, 0.0, price=0.0,
                   id=f"a-{day}"),
                _t("margin", "ASSIGN", day, "SYN.TO", 100 * n, -100.0 * n,
                   price=1.0, id=f"s-{day}", time="10:00:01")]

    def _rows(self, books, lock_year, lock, tax_date="settle"):
        return straddling(books, lock_year + 1, "grant", lock_year,
                          filed_years={lock_year}, tax_date=tax_date,
                          today=date(2027, 3, 1),
                          filed_premiums={lock_year: lock})

    def _case_a(self):
        return ([self._row("2025-12-30", "2025-12-31", -1, 10.0),
                 self._row("2025-12-31", "2026-01-02", -1, 20.0)]
                + self._assign("2026-02-02"))

    def test_tj1176_lock_still_reporting_asks_for_the_t1adj(self):
        rows = self._rows(self._case_a(), 2025,
                          {(self.P, "2025-12-30"): 10.0,
                           (self.P, "2025-12-31"): 10.0})
        (r,) = [x for x in rows if x["close_kind"] == "assignment"]
        self.assertTrue(r["action"].startswith(
            "T1-ADJ 2025: remove the 10.00 premium"), r["action"])

    def test_tj1176_lock_that_dropped_it_is_amended(self):
        rows = self._rows(self._case_a(), 2025, {})
        (r,) = [x for x in rows if x["close_kind"] == "assignment"]
        self.assertTrue(r["action"].startswith("no amendment"), r["action"])

    def _case_b(self):
        return ([self._row("2025-11-03", "2025-11-04", -1, 20.0),
                 self._row("2025-12-30", "2025-12-31", -1, 10.0),
                 self._row("2025-12-31", "2026-01-02", 1, -25.0)]
                + self._assign("2026-02-02"))

    def test_tj1176_amended_lock_beside_a_buy_back_of_the_next_year(self):
        rows = self._rows(self._case_b(), 2025,
                          {(self.P, "2025-11-03"): 20.0,
                           (self.P, "2025-11-04"): 20.0})
        (r,) = [x for x in rows if x["close_kind"] == "assignment"]
        self.assertTrue(r["action"].startswith("no amendment"), r["action"])

    def test_tj1176_unamended_lock_beside_a_buy_back_of_the_next_year(self):
        rows = self._rows(self._case_b(), 2025,
                          {(self.P, "2025-11-03"): 20.0,
                           (self.P, "2025-11-04"): 20.0,
                           (self.P, "2025-12-30"): 10.0,
                           (self.P, "2025-12-31"): 10.0})
        (r,) = [x for x in rows if x["close_kind"] == "assignment"]
        self.assertTrue(r["action"].startswith(
            "T1-ADJ 2025: remove the 10.00 premium"), r["action"])

    def test_tj1176_trade_basis_jan_2(self):
        # On the trade date the Dec 31 write is 2025's, though it settles
        # on the day the 2026 lock keys its own write by.
        books = ([self._row("2025-12-31", "2026-01-02", -1, 20.0),
                  self._row("2026-01-02", "2026-01-05", -1, 10.0)]
                 + self._assign("2027-02-02", 2))
        rows = self._rows(books, 2026,
                          {(self.P, "2026-01-02"): 10.0,
                           (self.P, "2026-01-05"): 10.0}, tax_date="trade")
        (r,) = [x for x in rows if x["close_kind"] == "assignment"
                and x["write_year"] == 2026]
        self.assertTrue(r["action"].startswith(
            "T1-ADJ 2026: remove the 10.00 premium"), r["action"])


class TestExpiryReviewLegAlreadyTaken(unittest.TestCase):
    """TJ-1230: a leg takes its own assignment's premium first, and its
    shares are then used up: another assignment's premium stays
    unconsumed, as the run warns. Keeps TJ-1068 (an unpaired premium in
    an opposite trade with shares free,
    test_fix_1029_r7d_edgeplan)."""
    S = "ZZX.TO"
    CALL = "ZZX251231C00001000.TO"
    PUT = "ZZX251231P00001000.TO"

    def _rows(self, leg_qty=100):
        return [
            _t("margin", "BUYSELL", "2025-01-02", self.S, 100, -50.0),
            _t("margin", "BUYSELL", "2025-11-03", self.CALL, -1, 10.0),
            _t("margin", "BUYSELL", "2025-11-03", self.PUT, -1, 20.0,
               time="10:01:00"),
            _t("margin", "ASSIGN", "2025-12-31", self.CALL, 1, 0.0, price=0.0,
               time="16:00:00"),
            _t("margin", "ASSIGN", "2025-12-31", self.PUT, 1, 0.0, price=0.0,
               time="16:00:01"),
            _t("margin", "ASSIGN", "2025-12-31", self.S, leg_qty,
               -1.0 * leg_qty, time="16:00:02", settle="2026-01-02")]

    def _by_symbol(self, rows):
        doc, edge, _text = _edge({"margin": rows}, year=2026)
        return doc, {r["symbol"]: r
                     for r in edge["year_boundary"]["option_expiries"]}

    def test_tj1230_used_up_leg_takes_no_other_premium(self):
        doc, rows = self._by_symbol(self._rows())
        self.assertIsNone(rows[self.CALL]["lands_in"], rows[self.CALL])
        self.assertIn("add the missing share row", rows[self.CALL]["why"])
        self.assertEqual(rows[self.PUT]["lands_in"], 2026)
        self.assertIn("folds into the ZZX.TO leg", rows[self.PUT]["why"])
        # The engine agrees: only the put's premium is in the leg.
        (h,) = [h for h in doc["inventory_year_end"]
                if h["symbol"] == self.S]
        self.assertEqual(h["total_cost"], 130.0)

    def test_tj1230_a_bigger_leg_has_shares_for_both(self):
        doc, rows = self._by_symbol(self._rows(200))
        self.assertEqual(rows[self.CALL]["lands_in"], 2026)
        self.assertIn("folds into the ZZX.TO leg", rows[self.CALL]["why"])
        (h,) = [h for h in doc["inventory_year_end"]
                if h["symbol"] == self.S]
        self.assertEqual(h["total_cost"], 220.0)

    def test_tj1230_each_option_with_its_own_leg_keeps_it(self):
        rows = self._rows()
        rows.append(_t("margin", "BUYSELL", "2025-12-31", self.S, -100, 100.0,
                       price=10.0, time="16:00:03"))
        _doc, by_sym = self._by_symbol(rows)
        self.assertIn("folds into the ZZX.TO leg", by_sym[self.CALL]["why"])
        self.assertIn("folds into the ZZX.TO leg", by_sym[self.PUT]["why"])


if __name__ == "__main__":
    unittest.main()
