"""Backlog fix round 8, group `planning`: the views and checks that read
the books (buy-check, sell-check, harvest, edge-cases, option-boundary,
watch, div-estimate, check-tt, holdings read-back).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ALT*, NEX*, ZZ*), made-up accounts, small amounts.
"""
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import test_fix_1029_r7b_harvest as _r7b
from _qa_project import tj
from test_fix_1029_planning import _toml
from test_fix_1029_r7d_edgeplan import (_PUT, _assigned_2025, _b, _edge,
                                        _engine_lock, _t)
from test_fix_1029_r7d_harvest import _project as _r7d_project
from test_fix_1029_r7d_harvest import _quotes
from test_fix_1029_r7e_close import _project
from test_fix_1029_r7e_fetchforms import _BLENDED, _NIL
from test_fix_1029_r7e_fetchforms import _export as _r7e_export
from test_fix_1029_r7e_fetchforms import _toml as _r7e_toml
from test_fix_1029_r7e_planning import (_harvest_project, _harvestable,
                                        _sales_today)
from test_fix_1029_r7f_closecash import _IB_HEAD, _SYM

from taxjson.bin.taxjson_harvest import _locked_fraction
from taxjson.lib.div_estimate import Payment, detect_schedule, schedule
from taxjson.lib.option_boundary import straddling


def _d(days):
    return (date.today() - timedelta(days=days)).isoformat()


class TestOptionSpellingThroughShareLines(unittest.TestCase):
    """TJ-1301 (a chain of same-market lines), TJ-1280 (a line between
    markets, only for a spelling the rows hold). Keeps TJ-1172 and R1-16
    (a native Montreal contract is another contract)."""
    EX = (date.today() + timedelta(days=400)).strftime("%y%m%d")
    SER = EX + "P00001000"

    def _project(self, tmp, map_text, *spellings):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(_toml("canada"))
        (root / "ticker.map").write_text(map_text)
        (root / "inputs" / "margin").mkdir(parents=True)
        text = ""
        for s in spellings:
            text += (f"BUYSELL {_d(60)} 10:00:00 {s} 1 CAD .2 20 0\n"
                     f"BUYSELL {_d(9)} 10:00:00 {s} -1 CAD .1 10 0\n")
        (root / "inputs" / "margin" / "m.tt").write_text(text)
        tj(root, "run", "--no-input")
        return root

    def _check(self, root, cmd, sym):
        r = tj(root, cmd, sym, "--json", check=False)
        return r.returncode, json.loads(r.stdout)["results"][0]

    def _alike(self, root, spelled, booked):
        rc_b, b = self._check(root, "buy-check", booked)
        rc_s, s = self._check(root, "buy-check", spelled)
        self.assertEqual((rc_b, b["verdict"]), (1, "UNSAFE"), b)
        self.assertEqual((rc_s, s["verdict"]), (1, "UNSAFE"), s)
        self.assertEqual(s["clears_at"], b["clears_at"])
        self.assertEqual(s["last_loss"]["date"], b["last_loss"]["date"])
        _rc, sell_b = self._check(root, "sell-check", booked)
        _rc, sell_s = self._check(root, "sell-check", spelled)
        self.assertIsNotNone(sell_s["last_loss"])
        self.assertEqual(sell_s["last_loss"], sell_b["last_loss"])

    def test_tj1301_chain_of_two_lines(self):
        syn = f"SYN{self.SER}.TO"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                tmp, "GLOBAL SYN.TO ALT.TO\nGLOBAL ALT.TO NEX.TO\n", syn)
            self._alike(root, syn, f"NEX{self.SER}.TO")
            self._alike(root, f"ALT{self.SER}.TO", f"NEX{self.SER}.TO")

    def test_tj1280_us_rows_booked_on_the_canadian_line(self):
        us = f"SYN{self.SER}.US"
        for word in ("TOBASE", "GLOBAL"):
            with self.subTest(word=word), \
                    tempfile.TemporaryDirectory() as tmp:
                root = self._project(tmp, f"{word} SYN.US SYN.TO\n", us)
                self._alike(root, us, f"SYN{self.SER}.TO")

    def test_tj1280_native_montreal_contract_stays_another_contract(self):
        # R1-16: no .US row was booked: the .US code is a USD-strike
        # contract nobody traded here.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, "TOBASE SYN.US SYN.TO\n",
                                 f"SYN{self.SER}.TO")
            _rc, us = self._check(root, "buy-check", f"SYN{self.SER}.US")
            rc_to, to = self._check(root, "buy-check", f"SYN{self.SER}.TO")
        self.assertEqual(us["verdict"], "SAFE", us)
        self.assertIsNone(us["last_loss"])
        self.assertEqual((rc_to, to["verdict"]), (1, "UNSAFE"), to)

    def test_tj1280_another_series_of_booked_us_rows_stays_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, "TOBASE SYN.US SYN.TO\n",
                                 f"SYN{self.SER}.US")
            _rc, r = self._check(root, "buy-check",
                                 f"SYN{self.EX}P00002000.US")
        self.assertEqual(r["verdict"], "SAFE", r)


class TestInKindLineAtTheRowSize(unittest.TestCase):
    """TJ-1289: a transfer row's stated size is the size check-tt compares
    at. TJ-1330: an export a run has not read, in an account with an older
    parsed export, is still unread. Keeps TJ-1163, TJ-1288."""

    @staticmethod
    def _transfer(direction, qty, value):
        return ("Transfers,Data,Equity and Index Options,CAD,"
                f"SYN 19DEC25 10 C,2025-06-03,ACATS,{direction},Other Broker,"
                f"99900002,{qty},0,{value},0,0,\n")

    def _ib_project(self, td, value):
        root = _project(Path(td), {"margin": "taxable", "plan": "sheltered"})
        (root / "inputs/margin/trades.tt").write_text(
            f"BUYSELL 2025-01-02 09:30:00 {_SYM} 1 CAD 1 10 0 x10\n"
            f"INKIND 2025-06-03 {_SYM} -1 CAD {value} plan=tfsa\n")
        (root / "inputs/margin/ib.csv").write_text(
            _IB_HEAD + self._transfer("Out", -1, -20))
        (root / "inputs/plan/ib.csv").write_text(
            _IB_HEAD + self._transfer("In", 1, 20))
        return root

    def test_tj1289_agreeing_only_at_100_is_refused_at_the_row_size(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td, "2 200")
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("x 10 = 20.00", r.stderr)
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("x 10 = 20.00", r.stdout + r.stderr)
            r = tj(root, "checklist", "--run", "--only", "tt-lines", "--json",
                   check=False)
            self.assertEqual(json.loads(r.stdout)["counts"]["attention"], 1)

    def test_tj1289_no_size_names_the_row_size_as_the_run_does(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td, "2 77")
            tj(root, "run", "--no-input", check=False)
            r = tj(root, "check-tt", check=False)
            self.assertIn("x 10 = 20.00", r.stdout + r.stderr)
            self.assertNotIn("x 100", r.stdout + r.stderr)

    def test_tj1289_agreeing_at_the_row_size_still_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td, "2 20")
            tj(root, "run", "--no-input")
            tj(root, "check-tt")

    def _second_export(self, td, value="2 20"):
        root = _project(Path(td), {"margin": "taxable", "plan": "sheltered"})
        head = _IB_HEAD.split("Transfers,Header")[0]
        (root / "inputs/margin/trades.tt").write_text(
            f"BUYSELL 2025-01-02 09:30:00 {_SYM} 1 CAD 1 10 0 x10\n")
        (root / "inputs/margin/ib0.csv").write_text(head)
        (root / "inputs/plan/ib0.csv").write_text(head)
        tj(root, "run", "--no-input")
        with (root / "inputs/margin/trades.tt").open("a") as f:
            f.write(f"INKIND 2025-06-03 {_SYM} -1 CAD {value} plan=tfsa\n")
        (root / "inputs/margin/ib.csv").write_text(
            _IB_HEAD + self._transfer("Out", -1, -20))
        (root / "inputs/plan/ib.csv").write_text(
            _IB_HEAD + self._transfer("In", 1, 20))
        return root

    def test_tj1330_export_added_after_a_run_is_unread(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._second_export(td)
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("may state another contract size",
                          r.stdout + r.stderr)
            tj(root, "run", "--no-input")
            r = tj(root, "check-tt")
            self.assertNotIn("contract size", r.stdout + r.stderr)

    def test_tj1330_export_replaced_after_a_run_is_unread(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._second_export(td)
            tj(root, "run", "--no-input")
            work = sorted((root / "work").glob("margin_ib*.json"))
            self.assertTrue(work)
            # A newer download under the old name, the size row gone from
            # work/ until the run reads it.
            newer = max(w.stat().st_mtime for w in work) + 60
            for w in (root / "work").glob("*_transfers.json"):
                w.unlink()
            os.utime(root / "inputs/margin/ib.csv", (newer, newer))
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_tj1330_no_export_added_a_bad_line_stays_an_error(self):
        # The run read every export: the line is judged, not a warning.
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td, "2 77")
            tj(root, "run", "--no-input", check=False)
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertNotIn("may state another", r.stdout + r.stderr)


class TestBlendedReadBack(unittest.TestCase):
    """TJ-1143: a lone blended holding read back under another
    --base-currency. TJ-1293: a quantity-0 input in another currency holds
    nothing. Keeps TJ-0733, TJ-1071, TJ-1232."""

    def _left_out(self, r, doc):
        self.assertEqual(r.returncode, 0, r.stderr)
        h = doc["holding"][0]
        self.assertEqual(h["quantity"], 4.0)
        self.assertTrue(h["cost_unknown"])
        for key in ("total_cost", "cost_per_share", "cost_source",
                    "base_total_cost"):
            self.assertNotIn(key, h)
        self.assertIn("is left out", r.stderr)

    def test_tj1143_another_base_currency_says_it_left_it_out(self):
        r, doc = _r7e_export({"a.toml": _r7e_toml(_BLENDED)}, "--holdings-toml",
                             "--base-currency", "USD", "{a.toml}")
        self._left_out(r, doc)
        self.assertIn("is in CAD, not the base currency USD", r.stderr)
        self.assertIn("in CAD, and this snapshot's base currency is USD",
                      " ".join(r.stdout.replace("#", " ").split()))

    def test_tj1143_control_its_own_base_currency_keeps_it(self):
        for args in (("--base-currency", "CAD"), ()):
            with self.subTest(args=args):
                r, doc = _r7e_export({"a.toml": _r7e_toml(_BLENDED)},
                                     "--holdings-toml", *args, "{a.toml}")
                self.assertEqual(r.returncode, 0, r.stderr)
                h = doc["holding"][0]
                self.assertEqual((h["total_cost"], h["cost_source"],
                                  h["base_total_cost"]),
                                 (80.0, "blended", 80.0))
                self.assertNotIn("cost_unknown", h)

    def test_tj1293_a_zero_row_in_another_currency_holds_nothing(self):
        usd = _NIL.replace('"CAD"', '"USD"')
        for names in (("{a.toml}", "{b.toml}"), ("{b.toml}", "{a.toml}")):
            with self.subTest(names=names):
                r, doc = _r7e_export(
                    {"a.toml": _r7e_toml(_BLENDED),
                     "b.toml": _r7e_toml(usd, account="acct2")},
                    "--holdings-toml", "--base-currency", "CAD", *names)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("mismatched", r.stderr)
                h = doc["holding"][0]
                self.assertEqual(
                    (h["quantity"], h["total_cost"], h["cost_source"],
                     h["base_total_cost"], h["currency"]),
                    (4.0, 80.0, "blended", 80.0, "CAD"))
                self.assertNotIn("cost_unknown", h)

    def test_tj1293_control_a_real_holder_in_another_currency_still_mixes(self):
        plain = ('symbol = "ZZQ.TO"\nquantity = 4.0\ncurrency = "CAD"\n'
                 'total_cost = 40.0\n')
        usd = ('symbol = "ZZQ.TO"\nquantity = 3.0\ncurrency = "USD"\n'
               'total_cost = 30.0\n')
        r, doc = _r7e_export(
            {"a.toml": _r7e_toml(plain),
             "b.toml": _r7e_toml(usd, account="acct2")},
            "--holdings-toml", "--base-currency", "CAD", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mismatched currencies", r.stderr)
        h = doc["holding"][0]
        self.assertTrue(h["mixed_currency"])
        self.assertNotIn("total_cost", h)

    def test_tj1293_control_a_mixed_snapshot_read_back_stays_mixed(self):
        # Its second item is at quantity 0 with a cost: not a zero row.
        mixed = ('symbol = "ZZQ.TO"\nquantity = 7.0\nmixed_currency = true\n'
                 'total_cost_CAD = 40.0\ntotal_cost_USD = 30.0\n')
        r, doc = _r7e_export({"a.toml": _r7e_toml(mixed)}, "--holdings-toml",
                             "--base-currency", "CAD", "{a.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = doc["holding"][0]
        self.assertTrue(h["mixed_currency"])
        self.assertEqual(h["total_cost_usd"], 30.0)


class TestHarvestLockedPerAccount(unittest.TestCase):
    """TJ-1279: a LOCKED holding's at-risk units are judged against each
    account's row, as an open VIOLATION's are (TJ-1242, CA-SL-08)."""
    _ACC = {"margin": "taxable", "margin2": "taxable",
            "registered": "sheltered"}

    def _books(self, reg=5, today=False):
        row = f"BUYSELL {_d(150)} 10:00:00 ZZSYN.TO 10 CAD 20 200 0\n"
        sale = f"BUYSELL {_d(0)} 10:00:00 ZZSYN.TO -10 CAD 10 100 0\n"
        return {"margin": row + (sale if today else ""), "margin2": row,
                "registered": f"BUYSELL {_d(5)} 10:00:00 ZZSYN.TO {reg} CAD "
                              f"10 {10 * reg} 0\n"}

    def test_tj1279_each_account_keeps_its_own_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _harvestable(_harvest_project(tmp, self._ACC, self._books()))
        self.assertAlmostEqual(h["now"], 100.0)
        self.assertAlmostEqual(h["30d"], 200.0)

    def test_tj1279_more_at_risk_than_one_account_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _harvestable(_harvest_project(tmp, self._ACC,
                                              self._books(15)))
        self.assertAlmostEqual(h["now"], 0.0)
        self.assertAlmostEqual(h["30d"], 200.0)

    def test_tj1279_the_engine_agrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _harvest_project(tmp, self._ACC, self._books(today=True))
            (sale,) = _sales_today(root, "margin")
        self.assertAlmostEqual(sale["permanently_disallowed"], 50.0)
        self.assertAlmostEqual(sale["gain"], -50.0)

    def test_tj1279_control_one_account_and_no_row_quantity(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _harvestable(_harvest_project(
                tmp, {"margin": "taxable", "registered": "sheltered"},
                {"margin": self._books()["margin"],
                 "registered": self._books()["registered"]}))
        self.assertAlmostEqual(h["now"], 50.0)
        self.assertAlmostEqual(h["30d"], 100.0)
        rec = {"taxable_qty": 20.0, "at_risk_qty": 5.0}
        self.assertEqual(_locked_fraction(rec), 0.25)
        self.assertEqual(_locked_fraction(rec, 10.0), 0.5)
        self.assertEqual(_locked_fraction(rec, -10.0), 0.5)
        self.assertEqual(_locked_fraction(rec, 4.0), 1.0)
        self.assertEqual(_locked_fraction({"taxable_qty": 20.0}, 10.0), 1.0)


def _pay(day, per):
    return Payment(pay_date=day, per_share=per, gross=per * 100, qty=100.0,
                   currency="CAD", account="margin", tax=0.0, row=None,
                   source="", tax_fx={})


class TestScheduleSpecialInTheLastSlot(unittest.TestCase):
    """TJ-1155: a special within the grid's tolerance of the latest date
    is not the forecast's base. Keeps TJ-0900, TJ-1078."""
    M = ["2025-01-15", "2025-02-15", "2025-03-15", "2025-04-15"]

    def _latest(self, *extra):
        name, months, _gap, latest = detect_schedule(self.M + list(extra))
        self.assertEqual((name, months), ("monthly", 1))
        return latest

    def test_tj1155_two_specials_after_the_last_regular(self):
        self.assertEqual(self._latest("2025-05-01", "2025-05-08"),
                         "2025-04-15")

    def test_tj1155_regular_then_a_special_a_week_later(self):
        self.assertEqual(self._latest("2025-05-15", "2025-05-22"),
                         "2025-05-15")

    def test_tj1155_control_ordinary_and_drifting_payers(self):
        self.assertEqual(self._latest(), "2025-04-15")
        self.assertEqual(self._latest("2025-05-15"), "2025-05-15")
        self.assertEqual(self._latest("2025-05-20"), "2025-05-20")
        self.assertEqual(self._latest("2025-05-01"), "2025-04-15")
        self.assertEqual(self._latest("2025-05-15", "2025-05-16"),
                         "2025-05-16")

    def test_tj1155_control_a_new_day_of_the_month_is_a_new_day(self):
        self.assertEqual(
            self._latest("2025-05-08", "2025-06-08", "2025-07-08"),
            "2025-07-08")

    def test_tj1155_forecast_flags_both_specials(self):
        pays = [_pay(d, 0.10) for d in self.M]
        pays += [_pay("2025-05-01", 5.0), _pay("2025-05-08", 7.0)]
        sched = schedule(pays)
        self.assertEqual(len(sched), 6)
        latest = detect_schedule([p.pay_date for p in sched])[3]
        off = [p.pay_date for p in sched if p.pay_date > latest]
        self.assertEqual(off, ["2025-05-01", "2025-05-08"])
        base = [p for p in sched if p.pay_date <= latest][-1]
        self.assertEqual(base.per_share, 0.10)


class TestEdgeCasesPoolAndMarkedShort(unittest.TestCase):
    """TJ-1281: a marked short opening beside another account's long is no
    sale in the cross-year review. TJ-1282: HELD DAY 30 is the engine's
    pool. Rows through the engine: the same `_edge` driver as the review's
    other tests. Keeps TJ-0690, TJ-1204, TJ-1207, TJ-1260."""
    S = "SYNQ.TO"

    def _straddle(self, *row_kw, qty=-10):
        qt = [_t("qt", "BUYSELL", "2025-02-03", self.S, 20, -400.0)]
        ibk = [_t("ibk", "BUYSELL", "2025-12-30", self.S, qty, -qty * 25.0,
                  settle="2026-01-02", **dict(row_kw))]
        _doc, edge, _text = _edge({"qt": qt, "ibk": ibk})
        (it,) = edge["year_boundary"]["straddles"]
        return it

    def test_tj1281_marked_short_opening_is_a_short_sale(self):
        for code in ("O", "C;O"):
            with self.subTest(code=code):
                it = self._straddle(("open_close", code))
                self.assertIn("so it is a 2026 short sale", it["why"])
                self.assertNotIn("disposition", it["why"])
                self.assertIn("opens a short position", it["why"])
                self.assertIsNone(it["gain"])

    def test_tj1281_control_an_unmarked_sale_of_the_pool_stays_a_sale(self):
        # TJ-0690: the pool's units, whichever account holds them.
        it = self._straddle()
        self.assertIn("so it is a 2026 sale", it["why"])
        self.assertIn("disposition", it["why"])

    def test_tj1281_control_a_closing_mark_stays_a_sale(self):
        it = self._straddle(("open_close", "C"))
        self.assertIn("so it is a 2026 sale", it["why"])

    def _day30(self, books):
        _doc, edge, _text = _edge(books)
        (w,) = edge["window_edges"]
        return w["held_at_day30"]

    def test_tj1282_a_split_another_account_reports(self):
        S = "SYN.TO"
        self.assertEqual(self._day30({
            "a": [_t("a", "SPLIT", "2025-06-10", S, 2, 0.0, price=0.0,
                     symbol_new=S)],
            "b": [_t("b", "BUYSELL", "2025-01-02", S, 40, -800.0),
                  _t("b", "BUYSELL", "2025-06-02", S, -20, 200.0),
                  _t("b", "BUYSELL", "2025-07-03", S, -40, 800.0)]}), 40.0)

    def test_tj1282_a_rename_another_account_reports(self):
        S, N = "SYN.TO", "NEWN.TO"
        self.assertEqual(self._day30({
            "a": [_t("a", "SPLIT", "2025-06-10", S, 2, 0.0, price=0.0,
                     symbol_new=N)],
            "b": [_t("b", "BUYSELL", "2025-01-02", S, 30, -1200.0),
                  _t("b", "BUYSELL", "2025-06-20", N, -20, 200.0),
                  _t("b", "BUYSELL", "2025-07-23", N, -40, 400.0)]}), 40.0)

    def _marked(self, *cover):
        S = self.S
        return {"qt": [_t("qt", "BUYSELL", "2025-02-03", S, 20, -400.0),
                       _t("qt", "BUYSELL", "2025-06-02", S, -10, 100.0),
                       _t("qt", "BUYSELL", "2025-07-03", S, -10, 100.0)],
                "ibk": [_t("ibk", "BUYSELL", "2025-01-10", S, -10, 250.0,
                           open_close="O"), *cover]}

    def test_tj1282_a_marked_short_does_not_net_the_long_pool(self):
        self.assertEqual(self._day30(self._marked()), 10.0)

    def test_tj1282_a_cover_of_the_marked_short_is_not_held(self):
        cover = _t("ibk", "BUYSELL", "2025-06-20", self.S, 10, -250.0,
                   open_close="C")
        self.assertEqual(self._day30(self._marked(cover)), 10.0)

    def test_tj1282_control_one_account_no_split(self):
        S = "SYN.TO"
        self.assertEqual(self._day30({
            "b": [_t("b", "BUYSELL", "2025-01-02", S, 40, -800.0),
                  _t("b", "BUYSELL", "2025-06-02", S, -20, 200.0),
                  _t("b", "BUYSELL", "2025-07-03", S, -20, 200.0)]}), 20.0)

    def test_tj1282_control_a_split_each_account_reports_counts_once(self):
        S = "SYN.TO"
        self.assertEqual(self._day30({
            "a": [_t("a", "BUYSELL", "2025-01-02", S, 10, -200.0),
                  _t("a", "SPLIT", "2025-06-10", S, 2, 0.0, price=0.0,
                     symbol_new=S)],
            "b": [_t("b", "BUYSELL", "2025-01-02", S, 40, -800.0),
                  _t("b", "BUYSELL", "2025-06-02", S, -20, 200.0),
                  _t("b", "SPLIT", "2025-06-10", S, 2, 0.0, price=0.0,
                     symbol_new=S),
                  _t("b", "BUYSELL", "2025-07-03", S, -40, 800.0)]}), 60.0)


class TestAmendedTestBesideALongSale(unittest.TestCase):
    """TJ-1152: a long option of the series sold on the write's day shares
    the lock's figure, and the books hold no long cost: the amended test
    says it cannot tell. Keeps TJ-0085, TJ-0679, TJ-1046."""

    def _action(self, y2024, lock):
        rows = straddling(y2024 + _assigned_2025(), 2025, "grant", 2024,
                          filed_years={2024}, today=date(2026, 9, 29),
                          filed_premiums={2024: lock})
        (r,) = [r for r in rows if r["close_kind"] == "assignment"]
        return r

    @staticmethod
    def _day(*rows):
        for n, t in enumerate(rows):
            t.date = f"{t.date[:10]} {9 + n:02d}:00:00"
        return list(rows)

    def _books(self, day="2024-12-02"):
        return self._day(_b(day, 1, -20.0), _b(day, -1, 10.0),
                         _b("2024-12-02", -1, 10.0))

    def test_tj1152_a_loss_there_does_not_read_as_amended(self):
        y2024 = self._books()
        lock = _engine_lock(y2024)
        self.assertEqual(lock[(_PUT, "2024-12-02")], 0.0)
        r = self._action(y2024, lock)
        self.assertFalse(r["action"].startswith("no amendment"), r["action"])
        self.assertIn("cannot tell", r["action"])
        self.assertTrue(r["attention"])

    def test_tj1152_a_gain_there_does_not_ask_for_a_t1adj_for_sure(self):
        y2024 = self._day(_b("2024-12-02", 1, -5.0), _b("2024-12-02", -1, 15.0),
                          _b("2024-12-02", -1, 10.0))
        r = self._action(y2024, {(_PUT, "2024-12-02"): 10.0})
        self.assertFalse(r["action"].startswith("T1-ADJ"), r["action"])
        self.assertIn("cannot tell", r["action"])

    def test_tj1152_control_a_long_sale_on_another_day(self):
        y2024 = self._books(day="2024-11-04")
        lock = _engine_lock(y2024)
        r = self._action(y2024, lock)
        self.assertTrue(r["action"].startswith("T1-ADJ 2024"), r["action"])
        amended = dict(lock)
        amended[(_PUT, "2024-12-02")] = 0.0
        self.assertTrue(self._action(y2024, amended)["action"].startswith(
            "no amendment"))

    def test_tj1152_control_no_long_sale(self):
        y2024 = [_b("2024-12-02", -1, 10.0)]
        r = self._action(y2024, _engine_lock(y2024))
        self.assertTrue(r["action"].startswith("T1-ADJ 2024"), r["action"])
        self.assertTrue(self._action(y2024, {(_PUT, "2024-12-02"): 0.0})
                        ["action"].startswith("no amendment"))


class TestWatchSaysAPositionStaysLeftOut(unittest.TestCase):
    """TJ-1154: the baseline stays (TJ-0912's rule; the state holds totals
    only), and the warning says what ends it for a cost left unknown."""

    def _watch(self, root, state):
        return tj(root, "watch", "--harvest", "--threshold", "0",
                  "--no-ibkr", "--state", str(state), "--json")

    def _baseline(self, tmp):
        y = date.today().year
        root = _r7d_project(tmp, {"margin": "taxable"})
        (root / "inputs" / "margin" / "trades.tt").write_text(
            f"BUYSELL {y}-01-03 10:00:00 ZZALP.TO 10 CAD 20 200 0\n"
            f"BUYSELL {y}-01-03 11:00:00 ZZBET.TO 10 CAD 20 200 0\n")
        tj(root, "run", "--no-input")
        _quotes(root, {"ZZALP.TO": 10.0, "ZZBET.TO": 10.0})
        state = root / "work" / "w_state.json"
        self._watch(root, state)
        return root, state

    def test_tj1154_unknown_cost_names_the_line_and_what_it_stops(self):
        y = date.today().year
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self._baseline(tmp)
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                f"OPENING {y - 1}-12-31 ZZBET.TO 10 cost=unknown\n")
            tj(root, "run", "--no-input")
            for price in (9.0, 8.0):
                _quotes(root, {"ZZALP.TO": price, "ZZBET.TO": 10.0})
                r = self._watch(root, state)
                self.assertEqual(json.loads(r.stdout)["changes"], [])
                self.assertIn("until you give its cost", r.stderr)
                self.assertIn("OPENING", r.stderr)

    def test_tj1154_control_a_missing_rate_is_not_told_about_a_cost(self):
        h = _r7b.TestWatchHarvestCoverage()
        with tempfile.TemporaryDirectory() as tmp:
            root = h._project(tmp)
            h._watch(root, h._hdoc(200.0))
            _doc, err = h._watch(root, h._hdoc(100.0, ["ZZB.TO"]))
        self.assertIn("baseline valued", err)
        self.assertNotIn("until you give its cost", err)


if __name__ == "__main__":
    unittest.main()
