"""Backlog fix round 7 (wave D) of 2026-10-29, group `edgeplan`: edge
cases, option-boundary, the estimate and the carryover.

TJ-1046  option-boundary's already-amended test left out the same-day
         buy-back in the lock's figure for a series and day, so a lock
         still reporting the assigned premium read as amended.
TJ-1067  under grant timing a write across Dec 31 is an option write in
         both project years, not an "option sale" in the new one.
TJ-1068  the expiry review names the share leg the engine's premium
         ledger used, also one in the opposite direction.
TJ-1113  the window review's units held on day 30 are the holders' the
         engine tests, not every account's.
TJ-1030  a minimum tax carryover is claimed only while the claim lowers
         tax (or the origin would lapse anyway).
TJ-1075  a damaged prior-year lock (its year field is not its file's)
         fails `tjs carryover` as damaged, not with --filed-lock advice.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*, LOSS*), account names (margin), small
amounts (or round test figures).
"""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule
from test_fix_1029_r7b_forms import _project, _run, _tjs

from taxjson.lib import amt_report as AR
from taxjson.lib import carryforward as CF
from taxjson.lib import tax_estimate as TE
from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from taxjson.lib.edge_cases import analyze, render_text
from taxjson.lib.option_boundary import reported_premiums, straddling
from taxjson.lib.pipeline import GainsRequest, run_gains
from taxjson.bin.taxjson_split_gains import split_for_account


def _t(acct, action, day, sym, qty, net, settle=None, price=None, **kw):
    return TaxTransaction(
        account=acct, action=action, date=day, date_settle=settle or day,
        time=kw.pop("time", "10:00:00"), symbol=sym, quantity=qty,
        net_amount=net, currency="CAD",
        price=(abs(net / qty) if price is None and qty else price or 0.0),
        id=kw.pop("id", f"{acct}-{action}-{day}-{sym}-{qty}"), **kw)


def _edge(books, sheltered=None, *, year=2025, timing="close"):
    """analyze() and the --details text of a Canadian project whose
    taxable accounts are `books` and sheltered accounts `sheltered`."""
    sheltered = sheltered or {}
    taxable = [t for rows in books.values() for t in rows]
    shel = [t for rows in sheltered.values() for t in rows]
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        doc = run_gains(copy.deepcopy(taxable), copy.deepcopy(shel),
                        req=GainsRequest(country="canada", year=year,
                                         taxable=True, tax_date="settle",
                                         option_premium_timing=timing,
                                         option_grant_since=year))
    accounts = {n: {"type": "taxable"} for n in books}
    accounts.update({n: {"type": "sheltered"} for n in sheltered})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        for n, rows in dict(books, **sheltered).items():
            (work / f"{n}_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in rows]}))
        for n, rows in books.items():
            out = doc if len(books) == 1 else split_for_account(
                doc, n, [t.to_dict() for t in rows])
            (work / f"{n}_gains_wash.json").write_text(json.dumps(out))
        cfg = {"settings": {"year": year, "country": "canada",
                            "tax_date": "settle",
                            "option_premium_timing": timing},
               "accounts": accounts}
        with contextlib.redirect_stderr(io.StringIO()):
            edge = analyze(root, cfg)
        text = "\n".join(render_text(edge, details=True, width_=200))
    return doc, edge, text


_PUT = "SYN250117P00001000.TO"


def _b(day, qty, net, settle=None):
    return TaxTransaction(
        action="BUYSELL", currency="CAD", account="margin",
        date=f"{day} 10:00:00",
        date_settle=settle or day, symbol=_PUT, quantity=qty,
        price=abs(net) / 100, net_amount=net)


def _assigned_2025():
    return [
        TaxTransaction(action="ASSIGN", currency="CAD", account="margin",
                       date="2025-01-17 10:00:00", date_settle="2025-01-17",
                       symbol=_PUT, quantity=1, price=0, net_amount=0.0),
        TaxTransaction(action="ASSIGN", currency="CAD", account="margin",
                       date="2025-01-17 10:00:00", date_settle="2025-01-17",
                       symbol="SYN.TO", quantity=100, price=1,
                       net_amount=-100.0)]


def _engine_lock(rows):
    """The return as first filed: the engine's grant-timing dispositions."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        res = CanadaTaxRules().compute_gains(
            copy.deepcopy(rows), option_premium_timing="grant")
    return reported_premiums({"dispositions": [
        {"symbol": d.get("symbol"), "date": d.get("date"),
         "date_settle": d.get("date_settle"), "gain": d.get("gain")}
        for d in res.get("transactions", [])]})


class TestAmendedTestCountsTheDaysBuyBack(unittest.TestCase):
    """TJ-1046: a lock's figure for a series and day also holds the loss
    of a buy-back that day. Keeps TJ-0679 (test_fix_1029_r7b_planning):
    an amended lock is still recognised, for the whole day."""

    def _action(self, y2024, lock):
        rows = straddling(y2024 + _assigned_2025(), 2025, "grant", 2024,
                          filed_years={2024}, today=date(2026, 9, 29),
                          filed_premiums={2024: lock})
        (r,) = [r for r in rows if r["close_kind"] == "assignment"]
        return r["action"]

    def _hours(self, *rows):
        for n, t in enumerate(rows):
            t.date = f"{t.date[:10]} {9 + n:02d}:00:00"
        return list(rows)

    @rule("CA-OPT-07")
    def test_same_day_write_bought_back_that_day(self):
        y2024 = self._hours(_b("2024-12-02", -1, 20.0),
                            _b("2024-12-02", 1, -25.0),
                            _b("2024-12-02", -1, 10.0))
        lock = _engine_lock(y2024)
        self.assertEqual(lock[(_PUT, "2024-12-02")], 5.0)
        self.assertTrue(self._action(y2024, lock).startswith(
            "T1-ADJ 2024: remove the 10.00 premium"))
        # Amended: the lock reports B's write and buy-back only.
        amended = {(_PUT, "2024-12-02"): -5.0}
        self.assertTrue(self._action(y2024, amended).startswith(
            "no amendment"))

    @rule("CA-OPT-07")
    def test_older_write_bought_back_on_the_assigned_writes_day(self):
        y2024 = self._hours(_b("2024-11-01", -1, 20.0),
                            _b("2024-12-02", 1, -25.0),
                            _b("2024-12-02", -1, 10.0))
        lock = _engine_lock(y2024)
        self.assertEqual(lock[(_PUT, "2024-12-02")], -15.0)
        self.assertTrue(self._action(y2024, lock).startswith("T1-ADJ 2024"))
        self.assertTrue(self._action(
            y2024, {(_PUT, "2024-12-02"): -25.0}).startswith("no amendment"))

    @rule("CA-OPT-07")
    def test_buy_back_traded_on_the_writes_settlement_day(self):
        # The lock keys a row by its trade and settlement dates both.
        y2024 = self._hours(
            _b("2024-11-01", -1, 20.0, settle="2024-11-04"),
            _b("2024-12-03", 1, -25.0, settle="2024-12-04"),
            _b("2024-12-02", -1, 10.0, settle="2024-12-03"))
        y2024 = [y2024[0], y2024[2], y2024[1]]
        lock = _engine_lock(y2024)
        self.assertEqual(lock[(_PUT, "2024-12-03")], -15.0)
        self.assertTrue(self._action(y2024, lock).startswith("T1-ADJ 2024"))

    @rule("CA-OPT-07")
    def test_writes_on_consecutive_days_share_a_key(self):
        # X settles the day Y trades: the lock's Dec 3 figure holds both.
        y2024 = self._hours(_b("2024-12-02", -1, 10.0, settle="2024-12-03"),
                            _b("2024-12-03", -1, 20.0, settle="2024-12-04"))
        lock = {(_PUT, "2024-12-02"): 0.0, (_PUT, "2024-12-03"): 20.0,
                (_PUT, "2024-12-04"): 20.0}
        rows = straddling(y2024 + _assigned_2025(), 2025, "grant", 2024,
                          filed_years={2024}, today=date(2026, 9, 29),
                          filed_premiums={2024: lock})
        (r,) = [r for r in rows if r["close_kind"] == "assignment"]
        self.assertTrue(r["action"].startswith("no amendment"), r["action"])


class TestWriteAcrossYearEndUnderGrant(unittest.TestCase):
    """TJ-1067: the premium's gain row carries the write's own id; that
    is no closing disposition. Keeps TJ-0690 (a pooled share sale)."""
    CALL = "ZZG260320C00001000.TO"

    def _row(self, year, rows):
        _doc, edge, _text = _edge({"margin": rows}, year=year, timing="grant")
        return edge["year_boundary"]["straddles"]

    @rule("CA-RPT-07")
    def test_both_project_years_call_it_a_write(self):
        rows = [_t("margin", "BUYSELL", "2025-12-31", self.CALL, -1, 50.0,
                   settle="2026-01-02")]
        for year in (2025, 2026):
            (r,) = self._row(year, rows)
            self.assertIn("2026 option write; under grant timing the "
                          "premium is a gain in 2026", r["why"])
            self.assertNotIn("option sale", r["why"])
        self.assertEqual(self._row(2026, rows)[0]["gain"], 50.0)

    @rule("CA-RPT-07")
    def test_sale_of_a_long_option_stays_an_option_sale(self):
        rows = [_t("margin", "BUYSELL", "2025-06-02", self.CALL, 1, -20.0),
                _t("margin", "BUYSELL", "2025-12-31", self.CALL, -1, 50.0,
                   settle="2026-01-02")]
        (r,) = self._row(2026, rows)
        self.assertIn("2026 option sale: a disposition", r["why"])

    @rule("CA-RPT-07")
    def test_buy_to_close_stays_a_buy_to_close(self):
        rows = [_t("margin", "BUYSELL", "2025-06-02", self.CALL, -1, 50.0),
                _t("margin", "BUYSELL", "2025-12-31", self.CALL, 1, -20.0,
                   settle="2026-01-02")]
        (r,) = self._row(2026, rows)
        self.assertIn("buy-to-close of a written option", r["why"])


class TestExpiryReviewOppositeLeg(unittest.TestCase):
    """TJ-1068: the engine's ledger gives an unpaired premium to an
    opposite-direction trade when no leg of its own direction is to come.
    Keeps TJ-0674 (the leg's own settlement year; no leg: the warning)."""
    S = "ZZX.TO"
    CALL = "ZZX251231C00001000.TO"

    def _rows(self):
        return [_t("margin", "BUYSELL", "2025-01-02", self.S, 100, -50.0),
                _t("margin", "BUYSELL", "2025-11-03", self.CALL, -1, 10.0),
                _t("margin", "ASSIGN", "2025-12-31", self.CALL, 1, 0.0,
                   price=0.0)]

    @rule("CA-RPT-07", "CA-OPT-08")
    def test_names_the_leg_the_engine_folded_into(self):
        rows = self._rows() + [_t("margin", "ASSIGN", "2025-12-31", self.S,
                                  100, -100.0, settle="2026-01-02")]
        for year in (2025, 2026):
            doc, edge, _text = _edge({"margin": rows}, year=year)
            (r,) = edge["year_boundary"]["option_expiries"]
            self.assertEqual(r["lands_in"], 2026)
            self.assertIn("folds into the ZZX.TO leg", r["why"])
            self.assertNotIn("add the missing share row", r["why"])
        inv = [(h["symbol"], h["qty"], h["total_cost"])
               for h in doc["inventory_year_end"]]
        self.assertEqual(inv, [(self.S, 200.0, 140.0)])

    @rule("CA-RPT-07", "CA-OPT-08")
    def test_same_direction_leg_is_preferred(self):
        # The sale (own direction) comes after the buy: the ledger skips
        # the buy, and so does the review.
        rows = self._rows() + [
            _t("margin", "BUYSELL", "2025-12-31", self.S, 10, -10.0,
               settle="2026-01-02", time="11:00:00"),
            _t("margin", "BUYSELL", "2026-01-05", self.S, -100, 200.0,
               time="11:00:00")]
        _doc, edge, _text = _edge({"margin": rows}, year=2026)
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertIn("leg of 2026-01-05", r["why"])

    @rule("CA-RPT-07", "CA-OPT-08")
    def test_no_trade_in_the_window_still_asks_for_the_row(self):
        _doc, edge, _text = _edge({"margin": self._rows()})
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertIsNone(r["lands_in"])
        self.assertIn("add the missing share row", r["why"])


class TestHeldOnDay30PerHolder(unittest.TestCase):
    """TJ-1113: a holder that acquired nothing in the window backs no
    denial (CA-SL-08), so its old units are not the loss's day-30 count.
    Keeps TJ-0689 (calls_in_windows per holder)."""
    S = "ZZH.TO"

    def _books(self, loss="2025-11-03", rrsp_buy="2025-10-04",
               rrsp_sell="2025-10-20"):
        margin = [_t("margin", "BUYSELL", "2025-01-06", self.S, 100, -2000.0),
                  _t("margin", "BUYSELL", loss, self.S, -100, 1000.0)]
        tfsa = [_t("tfsa", "BUYSELL", "2025-01-10", self.S, 50, -500.0)]
        rrsp = [_t("rrsp", "BUYSELL", rrsp_buy, self.S, 10, -100.0)]
        if rrsp_sell:
            rrsp.append(_t("rrsp", "BUYSELL", rrsp_sell, self.S, -10, 90.0))
        return {"margin": margin}, {"tfsa": tfsa, "rrsp": rrsp}

    @rule("CA-RPT-07", "CA-SL-08")
    def test_another_holders_old_units_do_not_count(self):
        books, shel = self._books()
        doc, edge, text = _edge(books, shel)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 0.0)
        self.assertIn("none of it was still held on day 30",
                      w["items"][0]["why"])

    @rule("CA-RPT-07", "CA-SL-08")
    def test_a_long_call_is_not_judged_by_the_shares_held(self):
        books, shel = self._books()
        shel["rrsp"] = [_t("rrsp", "BUYSELL", "2025-10-04",
                           "ZZH260116C00001000.TO", 1, -5.0)]
        _doc, edge, _text = _edge(books, shel)
        (w,) = edge["window_edges"]
        (it,) = [i for i in w["items"] if i["kind"] == "long call"]
        self.assertNotIn("none of it was still held", it["why"])

    @rule("CA-RPT-07", "CA-SL-08")
    def test_a_holder_that_acquired_in_the_window_counts(self):
        books, shel = self._books(rrsp_sell=None)
        doc, edge, text = _edge(books, shel)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 10.0)
        self.assertNotIn("none of it was still held", w["items"][0]["why"])

    @rule("CA-RPT-07", "CA-SL-08")
    def test_allowed_reason_across_the_year_end(self):
        books, shel = self._books(loss="2025-12-10", rrsp_buy="2026-01-05",
                                  rrsp_sell="2026-01-07")
        doc, edge, _text = _edge(books, shel)
        (r,) = edge["year_boundary"]["loss_windows"]
        self.assertEqual(r["denied"], 0.0)
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertIn("no units were held at the end of day 30", r["why"])
        self.assertIn("50 held by another holder", r["why"])

    @rule("CA-RPT-07", "CA-SL-08")
    def test_no_other_holder_keeps_the_plain_reason(self):
        books, shel = self._books(loss="2025-12-10", rrsp_buy="2026-01-05",
                                  rrsp_sell="2026-01-07")
        del shel["tfsa"]
        _doc, edge, _text = _edge(books, shel)
        (r,) = edge["year_boundary"]["loss_windows"]
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertNotIn("another holder", r["why"])


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, foreign_div=60000.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON", year=2025,
                actual_withheld=9000.0, amt_carryover={2022: 5000.0},
                amt_carryover_source="x")
    args.update(kw)
    return TE.estimate_canada(**args)


class TestCarryoverStopsWhereTaxStopsFalling(unittest.TestCase):
    """TJ-1030: s.120.2(1) allows the amount claimed, up to its limit.
    Keeps TJ-0719 (test_fix_1029_r7_estimate): the limit stays the before-FTC
    one, and an origin in its last usable year is claimed up to it."""

    @rule("CA-AMT-04", "CA-AMT-07")
    def test_federal_only_claims_nothing_and_keeps_it_open(self):
        c = _ca(province=None)["amt"]["carryover"]
        self.assertEqual(c["limit"], 6518.8)
        self.assertEqual(c["recovered_federal"], 0.0)
        self.assertEqual(c["recovered"], 0.0)
        self.assertEqual(c["closing_by_year"], {"2022": 5000.0})
        self.assertEqual(c["closing"], 5000.0)
        self.assertEqual(c["claim_kept"], 5000.0)

    @rule("CA-AMT-04", "CA-AMT-07")
    def test_ontario_claims_what_the_provincial_share_saves(self):
        full = _ca()["amt"]["carryover"]
        self.assertAlmostEqual(full["recovered"], 196.75, places=2)
        # Each dollar takes a dollar off the federal credit, which T2036
        # then gives back, on top of the province's 24.63% share: 196.75 /
        # 1.2463 is the claim where tax stops falling.
        self.assertEqual(full["recovered_federal"], 157.87)
        self.assertEqual(full["claim_kept"], 4842.13)
        self.assertEqual(full["closing_by_year"], {"2022": 4842.13})
        # A larger carryover saves no more; a smaller one saves less.
        small = _ca(amt_carryover={2022: 800.0})["amt"]["carryover"]
        self.assertEqual(small["recovered"], full["recovered"])
        self.assertEqual(small["recovered_federal"], 157.87)
        less = _ca(amt_carryover={2022: 150.0})["amt"]["carryover"]
        self.assertEqual(less["recovered_federal"], 150.0)
        self.assertLess(less["recovered"], full["recovered"] - 5.0)
        self.assertNotIn("claim_kept", less)

    @rule("CA-AMT-04")
    def test_a_lapsing_origin_is_still_claimed_up_to_the_limit(self):
        c = _ca(province=None, amt_carryover={2018: 10000.0})["amt"][
            "carryover"]
        self.assertEqual(c["recovered_by_year"], {"2018": 6518.8})
        self.assertEqual(c["lapsing_by_year"], {"2018": 3481.2})

    @rule("CA-AMT-04")
    def test_a_lapsing_origin_does_not_spend_a_later_one(self):
        c = _ca(province=None, amt_carryover={2018: 1000.0, 2022: 5000.0})[
            "amt"]["carryover"]
        self.assertEqual(c["recovered_by_year"], {"2018": 1000.0})
        self.assertEqual(c["closing_by_year"], {"2022": 5000.0})

    @rule("CA-AMT-04")
    def test_no_foreign_credit_claim_is_the_whole_limit(self):
        r = _ca(foreign_div=0.0, actual_withheld=0.0, other_income=100000.0,
                amt_carryover={2024: 900.5})
        c = r["amt"]["carryover"]
        self.assertEqual(c["recovered_federal"], 900.5)
        self.assertNotIn("claim_kept", c)

    @rule("CA-AMT-04")
    def test_said_in_the_notes_and_the_report(self):
        r = _ca(province=None)
        self.assertTrue(any("lowers no tax" in n for n in r["notes"]),
                        r["notes"])
        r = _ca()
        self.assertTrue(any("lowers no tax" in n for n in r["notes"]),
                        r["notes"])
        text = "\n".join(AR.render(AR.build(r, 2025, "CAD"), details=True))
        self.assertIn("lowers no tax", text)

    @rule("CA-AMT-07", "CA-CARRY-03")
    def test_the_lock_keeps_the_carryover_open(self):
        r = _ca(province=None)
        blk = CF.record_block("canada", r, {"other_income_entered": True,
                                            "other_income_value": 0.0})
        mt = blk["minimum_tax"]
        self.assertEqual(mt["recovered_by_year"], {})
        self.assertEqual(mt["closing_by_year"], {"2022": 5000.0})
        self.assertEqual(mt["closing"], 5000.0)
        self.assertIsNone(CF.block_problem(blk))


class TestDamagedPriorLockCarryover(unittest.TestCase):
    """TJ-1075: filed/2024.json whose year field says 2023 named as the
    2025 project's prior_year_record. `tjs carryover` keyed it 2023 (the
    year field) and the TJ-0904 check refused `--filed-lock 2023=...`
    with advice about an argument the user never typed (exit 2)."""

    def test_damaged_lock_named_as_damaged(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "y2024", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2024-06-03 10:00:00 SYN.TO -10 CAD 20 200 0\n"),
                settings="option_grant_timing_since = 2024\n")
            _run(self, p)
            r = _tjs(p, "close-year", "--yes", "--force")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lock = p / "filed" / "2024.json"
            doc = json.loads(lock.read_text())
            doc["year"] = 2023
            lock.write_text(json.dumps(doc, indent=2))
            q = _project(
                Path(d) / "y2025", year=2025,
                tt=("BUYSELL 2025-01-03 10:00:00 LOSS.TO 10 CAD 20 200 0\n"
                    "BUYSELL 2025-06-03 10:00:00 LOSS.TO -10 CAD 10 100 0\n"),
                settings=("option_grant_timing_since = 2025\n"
                          'prior_year_record = "../y2024/filed/2024.json"\n'))
            _run(self, q)
            r = _tjs(q, "carryover")
            err = " ".join((r.stdout + r.stderr).split())
            self.assertEqual(r.returncode, 1, err)
            self.assertIn("says year 2023, not 2024", err)
            self.assertIn("re-close that year", err)
            self.assertNotIn("give it as", err)


if __name__ == "__main__":
    unittest.main()
