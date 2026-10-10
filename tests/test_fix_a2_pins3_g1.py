"""Pins for surviving mutants in lib/phantom_holdings.py (re-audit-2
tests-pins lists 07: A2-0175, A2-0177, A2-0178, A2-0179, A2-0523,
A2-0541, A2-0901, A2-0902, A2-0908, A2-1554, A2-1615).

Each test names the code it pins; every one was checked to FAIL with
the cited mutant applied. Synthetic data only (fake account ids, all
CAD unless the case needs USD), no FX fetch.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(action, date_, symbol, qty, net=0.0, account="margin",
        time="10:00:00", **kw):
    from taxjson.lib.core import TaxTransaction
    kw.setdefault("currency", "CAD")
    kw.setdefault("price", abs(net / qty) if qty and net else 0.0)
    return TaxTransaction(action=action, date=date_, time=time,
                          symbol=symbol, quantity=qty, net_amount=net,
                          account=account,
                          date_settle=kw.pop("settle", date_), **kw)


def _split(date_, symbol, ratio, account="margin", new="", **kw):
    return _tx("SPLIT", date_, symbol, ratio, account=account,
               time="00:00:00", symbol_new=new, **kw)


def _gains(rows, listed, country="canada", year=2025):
    """run_gains with the .tt lines `listed` converts to; (results, stderr)."""
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with tempfile.TemporaryDirectory() as tmp:
        ph = Path(tmp) / "missing_history.json"
        ph.write_text(json.dumps([{"symbol": s, "account": a}
                                  for s, a in listed]))
        from _mh import from_json
        ph = from_json(ph, rows, until=f"{year}-12-31")
        err = io.StringIO()
        with redirect_stderr(err):
            res = run_gains([TaxTransaction(**t.to_dict()) for t in rows],
                            (), (), GainsRequest(country=country, year=year,
                                                 taxable=True,
                                                 incomplete_history=ph))
    return res, err.getvalue()


def _openings(rows, listed):
    from taxjson.lib.missing_history import synthesize_openings
    out, log = synthesize_openings(list(rows), set(listed), flag_stale=False)
    return ([(t.symbol, t.account, t.quantity, t.date)
             for t in out if t.action == "OPENING_BALANCE"], log)


# ------------------------------------------------------------- A2-0175

class TestBuyCodedClosingIsMissingHistory(unittest.TestCase):
    """A2-0175 / A2-0179 (m1275): the BUY branch of unbacked_close
    (`if q > 0:`). The phantoms round's detector test
    (test_fix_a2_phantoms.TestUnbackedCovers.test_ib_buy_coded_c_with_no_short)
    kills it for a stock; these add the futures case (reported only
    with include_options) and option-boundary's expired_open."""

    def test_unbacked_close_buy_mirror(self):
        from taxjson.lib.missing_history import unbacked_close
        buy = _tx("BUYSELL", "2025-03-03", "XYZ.US", 100, -1000.0,
                  currency="USD", open_close="C")
        self.assertTrue(unbacked_close(buy, 0.0))       # no short held
        self.assertTrue(unbacked_close(buy, -40.0))     # covers more
        self.assertFalse(unbacked_close(buy, -100.0))   # backed
        both = _tx("BUYSELL", "2025-03-03", "XYZ.US", 100, -1000.0,
                   currency="USD", open_close="C;O")
        self.assertTrue(unbacked_close(both, 0.0))
        self.assertFalse(unbacked_close(both, -1.0))

    def test_futures_buy_coded_c_needs_include_options(self):
        from taxjson.lib.missing_history import detect_unbacked_covers
        txs = [_tx("BUYSELL", "2025-11-03", "F:CLG6.US", 1, -62000.0,
                   currency="USD", open_close="C")]
        self.assertEqual(detect_unbacked_covers(txs), [])
        got = detect_unbacked_covers(txs, include_options=True)
        self.assertEqual([(c.symbol, c.marker, c.unbacked_qty) for c in got],
                         [("F:CLG6.US", "IB code C", 1.0)])

    def test_stock_cover_then_long_round_trip(self):
        # The finding's stock case: a C-coded buy, then an O buy and a
        # C sale — the first buy is still the unbacked cover.
        from taxjson.lib.missing_history import detect_unbacked_covers
        txs = [
            _tx("BUYSELL", "2025-03-03", "TSQ.US", 100, -40000.0,
                currency="USD", open_close="C"),
            _tx("BUYSELL", "2025-04-03", "TSQ.US", 100, -20000.0,
                currency="USD", open_close="O"),
            _tx("BUYSELL", "2025-08-05", "TSQ.US", -100, 25000.0,
                currency="USD", open_close="C"),
        ]
        got = detect_unbacked_covers(txs)
        self.assertEqual([(c.symbol, c.date, c.unbacked_qty) for c in got],
                         [("TSQ.US", "2025-03-03", 100.0)])

    def test_expired_open_buy_coded_c_is_broker_closing(self):
        from taxjson.lib.option_boundary import expired_open
        acct = "U5550001"  # pii-ok
        rows = [_tx("BUYSELL", "2025-03-03", "QZY250718C00045000.US", 1,
                    -100.0, account=acct, currency="USD", open_close="C",
                    settle="2025-03-04")]
        out = expired_open(rows, 2025, today=date(2026, 10, 1))
        self.assertEqual([o["broker_closing"] for o in out], [True])
        rows = [_tx("BUYSELL", "2025-03-03", "QZY250718C00045000.US", 1,
                    -100.0, account=acct, currency="USD", open_close="O",
                    settle="2025-03-04")]
        out = expired_open(rows, 2025, today=date(2026, 10, 1))
        self.assertEqual([o["broker_closing"] for o in out], [False])


# ------------------------------------------------- A2-0177 / A2-0179

class TestOpeningSizingCountsAssign(unittest.TestCase):
    """synthesize_openings counts ASSIGN rows in the listed pair's
    running position (the counted-actions tuple; A2-0177 m1273,
    A2-0179 M1)."""

    @rule("CA-ACB-11")
    def test_put_assignment_then_oversell(self):
        rows = [
            _tx("ASSIGN", "2025-03-21", "AAA.TO", 100, -4000.0),
            _tx("BUYSELL", "2025-04-10", "AAA.TO", -200, 9000.0),
            _tx("BUYSELL", "2025-05-10", "AAA.TO", 50, -2000.0),
            _tx("BUYSELL", "2025-06-10", "AAA.TO", -50, 2100.0),
        ]
        ops, _ = _openings(rows, [("AAA.TO", "margin")])
        self.assertEqual(ops, [("AAA.TO", "margin", 100.0, "2025-03-21")])
        res, _err = _gains(rows, [("AAA.TO", "margin")])
        self.assertAlmostEqual(res["summary"]["total_gain"], 100.0, places=2)
        clean = [(t["date"], t.get("qty"), round(t["gain"], 2))
                 for t in res["transactions"] if "gain" in t]
        self.assertEqual(clean, [("2025-06-10", 50.0, 100.0)])
        self.assertEqual([m["date"] for m in
                          res.get("manual_reporting_required") or []],
                         ["2025-04-10"])

    def test_call_assignment_delivers_pre_window_shares(self):
        rows = [
            _tx("ASSIGN", "2025-02-21", "AAA.US", -100, 5000.0,
                currency="USD"),
            _tx("BUYSELL", "2025-03-03", "AAA.US", 100, -1000.0,
                currency="USD"),
            _tx("BUYSELL", "2025-04-03", "AAA.US", -50, 500.0,
                currency="USD"),
        ]
        ops, log = _openings(rows, [("AAA.US", "margin")])
        self.assertEqual(ops, [("AAA.US", "margin", 100.0, "2025-02-21")])
        self.assertTrue(log[0]["inserted"])


class TestRenameChains(unittest.TestCase):
    """A2-0178: the S021-04 fold (`if owners:` builds folded_into) with
    the ANCESTOR sorting first (AAA -> ZZZ), so sorted() order alone
    does not satisfy it. A2-0179 M2: the rename-cycle guard."""

    def _chain(self):
        return [
            _tx("BUYSELL", "2025-01-10", "AAA.TO", -10, 100.0),
            _split("2025-03-03", "AAA.TO", 1.0, new="ZZZ.TO"),
            _tx("BUYSELL", "2025-04-10", "ZZZ.TO", -5, 60.0),
        ]

    @rule("CA-ACB-11")
    def test_both_ends_listed_ancestor_sorts_first(self):
        ops, log = _openings(self._chain(),
                             [("AAA.TO", "margin"), ("ZZZ.TO", "margin")])
        self.assertEqual(ops, [("AAA.TO", "margin", 15.0, "2025-01-10")])
        by = {e["symbol"]: e for e in log}
        self.assertFalse(by["AAA.TO"]["inserted"])
        self.assertEqual(by["AAA.TO"]["opening_qty"], 0.0)
        self.assertIn("same rename chain as ZZZ.TO", by["AAA.TO"]["note"])
        self.assertEqual(by["ZZZ.TO"]["opening_qty"], 15.0)

    def test_rename_cycle_terminates(self):
        code = (
            "from taxjson.lib.core import TaxTransaction as T\n"
            "from taxjson.lib.missing_history import synthesize_openings\n"
            "def t(a,d,s,q,**k):\n"
            "    return T(action=a,date=d,date_settle=d,time='10:00:00',"
            "symbol=s,quantity=q,price=0.0,net_amount=k.pop('net',0.0),"
            "currency='USD',account='margin',**k)\n"
            "rows=[t('SPLIT','2025-02-01','OLD.US',1.0,symbol_new='NEW.US'),"
            "t('SPLIT','2025-06-01','NEW.US',1.0,symbol_new='OLD.US'),"
            "t('BUYSELL','2025-07-01','OLD.US',-10,net=100.0)]\n"
            "out,log=synthesize_openings(rows,{('OLD.US','margin')},"
            "flag_stale=False)\n"
            "print([e['opening_qty'] for e in log])\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(REPO_ROOT / "src"), env.get("PYTHONPATH")) if p)
        try:
            r = subprocess.run([sys.executable, "-c", code], env=env,
                               capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            self.fail("synthesize_openings hangs on a rename cycle")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[10.0]")


# -------------------------------------------- A2-0177 / A2-0541 / A2-0901

class TestDetectPhantomsWalk(unittest.TestCase):
    """detect_missing_history' position walk: the counted actions (ASSIGN,
    TRANSFER — A2-0177 m1180/m1183), per-account split dedupe
    (A2-0177 m1145, A2-0541), the SPLIT branch and rename merge
    (A2-0177 m1288, A2-0901 m1053/m1223), and the candidate fields
    (A2-0901 m1054/m1154/m1227/m1291/m1294/m1297/m1298/m1389)."""

    def _detect(self, rows, **kw):
        from taxjson.lib.missing_history import detect_missing_history
        return [(c.symbol, c.account, c.peak_short)
                for c in detect_missing_history(list(rows), country="canada", **kw)]

    def test_assign_in_then_oversell_peak(self):
        rows = [
            _tx("ASSIGN", "2025-03-21", "AAA.TO", 100, -4000.0),
            _tx("BUYSELL", "2025-04-10", "AAA.TO", -200, 9000.0),
        ]
        self.assertEqual(self._detect(rows), [("AAA.TO", "margin", -100.0)])

    def test_transfer_in_then_sale_is_not_a_phantom(self):
        rows = [
            _tx("TRANSFER", "2025-02-10", "CCC.TO", 100, -3000.0),
            _tx("BUYSELL", "2025-04-10", "CCC.TO", -100, 3500.0),
        ]
        self.assertEqual(self._detect(rows), [])

    def test_same_split_in_two_accounts(self):
        rows = [
            _tx("BUYSELL", "2025-01-10", "BBB.TO", 100, -5000.0),
            _tx("BUYSELL", "2025-01-11", "BBB.TO", 100, -5000.0,
                account="cash2"),
            _split("2025-03-03", "BBB.TO", 2.0),
            _split("2025-03-03", "BBB.TO", 2.0, account="cash2"),
            _tx("BUYSELL", "2025-04-10", "BBB.TO", -200, 6000.0),
            _tx("BUYSELL", "2025-04-11", "BBB.TO", -200, 6000.0,
                account="cash2"),
        ]
        self.assertEqual(self._detect(rows), [])

    def test_duplicate_split_rows_in_one_account_count_once(self):
        # Two brokers feeding one account each book the 2:1 split (S033-14).
        rows = [
            _tx("BUYSELL", "2025-01-10", "XYZ.US", 100, -5000.0,
                currency="USD"),
            _split("2025-03-03", "XYZ.US", 2.0, id="brkA-1",
                   currency="USD"),
            _split("2025-03-03", "XYZ.US", 2.0, id="brkB-1",
                   currency="USD"),
            _tx("BUYSELL", "2025-04-10", "XYZ.US", -400, 8000.0,
                currency="USD"),
        ]
        self.assertEqual(self._detect(rows), [("XYZ.US", "margin", -200.0)])

    def test_find_missing_history_reports_the_duplicate_split_short(self):
        rows = [
            _tx("BUYSELL", "2025-01-10", "XYZ.US", 100, -5000.0,
                currency="USD"),
            _split("2025-03-03", "XYZ.US", 2.0, id="brkA-1",
                   currency="USD"),
            _split("2025-03-03", "XYZ.US", 2.0, id="brkB-1",
                   currency="USD"),
            _tx("BUYSELL", "2025-04-10", "XYZ.US", -400, 8000.0,
                currency="USD"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                t.to_dict() for t in rows]}))
            e = dict(os.environ)
            e["TAXJSON_OFFLINE"] = "1"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_missing_history",
                 "--year", "2025", str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True, env=e,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("No missing-cost-basis issues", r.stdout)
            self.assertIn("AFFECTS 2025", r.stdout)
            line, = [ln for ln in r.stdout.splitlines()
                     if ln.startswith("XYZ.US")]
            self.assertIn("-200.0000", line)

    def test_rename_into_already_held_symbol(self):
        rows = [
            _tx("BUYSELL", "2025-01-10", "NEW.TO", 100, -1000.0),
            _tx("BUYSELL", "2025-01-11", "OLD.TO", 50, -500.0),
            _split("2025-03-03", "OLD.TO", 1.0, new="NEW.TO"),
            _tx("BUYSELL", "2025-04-10", "NEW.TO", -150, 1800.0),
        ]
        self.assertEqual(self._detect(rows), [])

    def test_rename_ratio_multiplies(self):
        # 100 OLD x 0.5 = 50 NEW; selling 80 leaves a 30-share phantom.
        rows = [
            _tx("BUYSELL", "2025-01-10", "OLD.TO", 100, -1000.0),
            _split("2025-03-03", "OLD.TO", 0.5, new="NEW.TO"),
            _tx("BUYSELL", "2025-04-10", "NEW.TO", -80, 1600.0),
        ]
        self.assertEqual(self._detect(rows), [("NEW.TO", "margin", -30.0)])

    def test_split_row_is_not_a_trade(self):
        # 10 shares, 2:1 split = 20; selling 30 leaves -10 (not -8: the
        # split's ratio is no quantity).
        rows = [
            _tx("BUYSELL", "2025-01-10", "SPL.TO", 10, -100.0),
            _split("2025-03-03", "SPL.TO", 2.0),
            _tx("BUYSELL", "2025-04-10", "SPL.TO", -30, 300.0),
        ]
        from taxjson.lib.missing_history import detect_missing_history
        c, = detect_missing_history(rows)
        self.assertEqual((c.peak_short, c.end_position), (-10.0, -10.0))

    def test_candidate_fields(self):
        from taxjson.lib.missing_history import detect_missing_history
        rows = [
            _tx("BUYSELL", "2025-01-10", "FLD.TO", 10, -100.0),
            _tx("BUYSELL", "2025-02-10", "FLD.TO", -10, 120.0),   # to 0
            _tx("BUYSELL", "2025-03-10", "FLD.TO", 30, -300.0),
            _tx("BUYSELL", "2025-04-10", "FLD.TO", -35, 400.0),   # to -5
            _tx("BUYSELL", "2025-05-10", "FLD.TO", -5, 60.0),     # to -10
            _tx("BUYSELL", "2025-06-10", "FLD.TO", 10, -90.0),    # to 0
        ]
        c, = detect_missing_history(rows)
        self.assertEqual(c.first_negative_date, "2025-04-10")
        self.assertEqual(c.disposition_count, 2)
        self.assertEqual(c.peak_short, -10.0)
        self.assertEqual(c.end_position, 0.0)
        self.assertEqual(c.currency, "CAD")
        # A sale that ends exactly flat is no phantom disposition; a buy
        # while short is no disposition.
        rows = [
            _tx("BUYSELL", "2025-01-10", "FLT.TO", 10, -100.0),
            _tx("BUYSELL", "2025-02-10", "FLT.TO", -10, 120.0),
            _tx("BUYSELL", "2025-03-10", "FLT.TO", -1, 12.0),
        ]
        c, = detect_missing_history(rows)
        self.assertEqual((c.disposition_count, c.first_negative_date),
                         (1, "2025-03-10"))

    def test_mixed_currency_candidate(self):
        from taxjson.lib.missing_history import detect_missing_history
        rows = [
            _tx("BUYSELL", "2025-01-10", "MIX.TO", 10, -100.0),
            _tx("BUYSELL", "2025-04-10", "MIX.TO", -30, 300.0,
                currency="USD"),
        ]
        c, = detect_missing_history(rows)
        self.assertEqual(c.currency, "CAD/USD")

    def test_futures_short_left_out_by_default(self):
        rows = [_tx("BUYSELL", "2025-04-10", "F:ESH5.US", -1, 0.0,
                    currency="USD")]
        self.assertEqual(self._detect(rows), [])
        self.assertEqual(self._detect(rows, include_options=True),
                         [("F:ESH5.US", "margin", -1.0)])


# --------------------------------------------------- A2-0523 / A2-0901

class TestTaxYearRelevance(unittest.TestCase):
    """assess_tax_year_relevance: TRANSFER counted in the walk and
    ASSIGN counted as a cover (A2-0523 B01-B03), the `run.get(key,
    0.0)` start, proceeds sums and defaults (A2-0901 m1160/m1240/
    m1242), and plain-split scaling (A2-1554 / A2-1615 ph476)."""

    def _rel(self, rows, year=2025):
        from taxjson.lib.missing_history import (assess_tax_year_relevance,
                                                  detect_missing_history)
        rows = list(rows)
        cands = detect_missing_history(rows, include_options=True)
        return {r.candidate.symbol: (r.affects_year, r.in_year_dispositions,
                                     r.in_year_proceeds)
                for r in assess_tax_year_relevance(rows, cands, year)}

    def test_transfer_in_then_clean_sale_does_not_affect_year(self):
        from taxjson.lib.missing_history import (MissingHistoryCandidate,
                                                  assess_tax_year_relevance)
        # A candidate from an earlier short; the 2025 sale is backed by
        # a TRANSFER-in.
        rows = [
            _tx("BUYSELL", "2024-03-10", "TRF.TO", -10, 100.0),
            _tx("BUYSELL", "2024-05-10", "TRF.TO", 10, -90.0),
            _tx("TRANSFER", "2025-02-10", "TRF.TO", 100, -3000.0),
            _tx("BUYSELL", "2025-04-10", "TRF.TO", -100, 3500.0),
        ]
        self.assertEqual(self._rel(rows), {"TRF.TO": (False, 0, 0.0)})

    def test_assign_cover_of_carried_short_affects_year(self):
        rows = [
            _tx("BUYSELL", "2024-03-10", "ASG.TO", -100, 1000.0),
            _tx("ASSIGN", "2025-03-21", "ASG.TO", 100, -900.0),
        ]
        # The cover bears on the year but is no sale: InYrSales counts
        # sales only, and a purchase's cost is never proceeds (QA F4).
        self.assertEqual(self._rel(rows), {"ASG.TO": (True, 0, 0.0)})

    def test_one_unit_phantom_and_zero_proceeds(self):
        rows = [_tx("BUYSELL", "2025-04-10", "ONE.TO", -1, 12.0)]
        self.assertEqual(self._rel(rows), {"ONE.TO": (True, 1, 12.0)})
        rows = [_tx("BUYSELL", "2025-04-10", "NIL.TO", -1, 0.0)]
        self.assertEqual(self._rel(rows), {"NIL.TO": (True, 1, 0.0)})

    def test_candidate_without_in_year_rows(self):
        rows = [_tx("BUYSELL", "2024-04-10", "OLD.TO", -1, 12.0)]
        self.assertEqual(self._rel(rows), {"OLD.TO": (False, 0, 0.0)})

    def test_plain_split_scales_the_walk(self):
        # 100 bought, 2:1 split (200), three sales of 100: only the
        # third draws on missing history.
        rows = [
            _tx("BUYSELL", "2025-01-10", "SPX.TO", 100, -2000.0),
            _split("2025-02-03", "SPX.TO", 2.0),
            _tx("BUYSELL", "2025-03-10", "SPX.TO", -100, 2000.0),
            _tx("BUYSELL", "2025-04-10", "SPX.TO", -100, 2000.0),
            _tx("BUYSELL", "2025-05-10", "SPX.TO", -100, 2800.0),
        ]
        self.assertEqual(self._rel(rows), {"SPX.TO": (True, 1, 2800.0)})


# ------------------------------------------- A2-0901 / A2-1554 / A2-1615

class TestZeroBasisWalk(unittest.TestCase):
    """detect_zero_basis_acquisitions: the first acquisition date
    (A2-0901 m1262), plain-split scaling of `running` (A2-1554 /
    A2-1615 ph586) and the rename carrying zero_qty * ratio (ph594)."""

    def _zb(self, rows):
        from taxjson.lib.missing_history import detect_zero_basis_acquisitions
        return detect_zero_basis_acquisitions(list(rows), 2025)

    def test_first_acquisition_date_kept(self):
        rows = [
            _tx("BUYSELL", "2025-01-15", "ZZZ.TO", 10, 0.0,
                description="SPINOFF SHRS RECEIVED"),
            _tx("BUYSELL", "2025-02-15", "ZZZ.TO", 10, 0.0,
                description="SPINOFF SHRS RECEIVED"),
            _tx("BUYSELL", "2025-03-15", "ZZZ.TO", -20, 400.0),
        ]
        r, = self._zb(rows)
        self.assertEqual(r.acquisition_date, "2025-01-15")
        self.assertEqual(r.zero_cost_qty, 20.0)

    def test_plain_split_keeps_the_pool_contaminated(self):
        # 100 at $0, 2:1 split = 200: two sales of 100 both draw on it.
        rows = [
            _tx("BUYSELL", "2025-01-15", "ZBS.TO", 100, 0.0,
                description="SPINOFF SHRS RECEIVED"),
            _split("2025-02-03", "ZBS.TO", 2.0),
            _tx("BUYSELL", "2025-03-10", "ZBS.TO", -100, 3000.0),
            _tx("BUYSELL", "2025-04-10", "ZBS.TO", -100, 2600.0),
        ]
        r, = self._zb(rows)
        self.assertEqual((r.in_year_dispositions, r.in_year_proceeds),
                         (2, 5600.0))

    def test_rename_carries_zero_quantity_times_ratio(self):
        rows = [
            _tx("BUYSELL", "2025-01-15", "ZBO.TO", 100, 0.0,
                description="SPINOFF SHRS RECEIVED"),
            _split("2025-02-03", "ZBO.TO", 2.0, new="ZBN.TO"),
            _tx("BUYSELL", "2025-03-10", "ZBN.TO", -200, 3000.0),
        ]
        r, = self._zb(rows)
        self.assertEqual((r.symbol, r.zero_cost_qty), ("ZBN.TO", 200.0))


class TestSplitGainsPositionStart(unittest.TestCase):
    """A2-1615 sg89: split-gains' position-start walk multiplies the
    balance by the split ratio (bal.pop(sym) * ratio)."""

    def test_split_then_partial_sale_keeps_start(self):
        from taxjson.bin.taxjson_split_gains import _position_starts
        rows = [
            {"action": "BUYSELL", "date": "2025-01-02", "symbol": "SGX.TO",
             "quantity": 100, "account": "margin"},
            {"action": "SPLIT", "date": "2025-02-03", "symbol": "SGX.TO",
             "quantity": 2.0, "account": "margin", "symbol_new": ""},
            {"action": "BUYSELL", "date": "2025-03-03", "symbol": "SGX.TO",
             "quantity": -150, "account": "margin"},
        ]
        self.assertEqual(_position_starts(rows, "trade"),
                         {"SGX.TO": "2025-01-02"})


# ------------------------------------------------------------ A2-0901

class TestSynthLogAndSuggestions(unittest.TestCase):
    """The synthesize_openings log entry for a pair that needs no
    opening (A2-0901 m1205) and format_suggestions' layout (m1282)."""

    def test_no_opening_needed_entry(self):
        rows = [_tx("BUYSELL", "2025-01-10", "CMP.TO", 10, -100.0),
                _tx("BUYSELL", "2025-02-10", "CMP.TO", -10, 120.0)]
        _ops, log = _openings(rows, [("CMP.TO", "margin")])
        self.assertEqual(log, [{
            "symbol": "CMP.TO", "account": "margin", "opening_qty": 0.0,
            "inserted": False, "note": "no opening needed — data does not "
                                       "go negative for this pair"}])

    def test_format_suggestions_layout(self):
        from taxjson.lib.missing_history import (detect_missing_history,
                                                  format_suggestions)
        c = detect_missing_history([_tx("BUYSELL", "2025-04-10", "FMT.TO", -5,
                                 50.0)])
        text = format_suggestions(c)
        self.assertTrue(text.startswith('[\n  {\n    "symbol": "FMT.TO",'),
                        text)
        self.assertTrue(text.endswith("\n]\n"))


# ------------------------------------------------------------ A2-0902

class TestApplicationLogOrder(unittest.TestCase):
    """R1-318 (A2-0902): synthesize_openings iterates
    sorted(min_running.items()), so missing_history_log has one
    order whatever the hash seed."""

    SYMS = ["QAA.TO", "QBB.TO", "QCC.TO", "QDD.TO", "QEE.TO", "QFF.TO",
            "QGG.TO", "QHH.TO", "QII.TO", "QJJ.TO"]

    def _rows(self):
        return [_tx("BUYSELL", "2025-04-10", s, -5, 50.0) for s in self.SYMS]

    @rule("CA-ACB-11")
    def test_log_is_sorted(self):
        _ops, log = _openings(self._rows(),
                              [(s, "margin") for s in self.SYMS])
        self.assertEqual([e["symbol"] for e in log], self.SYMS)

    @rule("CA-ACB-11")
    def test_gains_output_identical_across_hash_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            books = Path(tmp) / "books.json"
            books.write_text(json.dumps({"transactions": [
                t.to_dict() for t in self._rows()]}))
            ph = Path(tmp) / "missing_history.json"
            ph.write_text(json.dumps([{"symbol": s, "account": "margin"}
                                      for s in reversed(self.SYMS)]))
            from _mh import from_json
            ph = from_json(ph, self._rows())
            outs = []
            for seed in ("0", "1", "7"):
                e = dict(os.environ)
                e.update(TAXJSON_OFFLINE="1", PYTHONHASHSEED=seed)
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                     "--country", "canada", "--year", "2025", "--taxable",
                     "--incomplete-history", str(ph), str(books)],
                    cwd=REPO_ROOT, capture_output=True, text=True, env=e,
                    stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stderr)
                outs.append(r.stdout)
                log = json.loads(r.stdout)["missing_history_log"]
                self.assertEqual([x["symbol"] for x in log], self.SYMS)
            self.assertEqual(len(set(outs)), 1)


# ------------------------------------------------------------ A2-0908

class TestSuperficialLossWarningBounds(unittest.TestCase):
    """detect_superficial_loss_warnings: the ±30-day window bound
    (window_days, A2-0908 m1028) and the loss amount's sign (m1334 /
    m1387); pipeline: only real clean losses (gain < -0.001, m1471) and
    only tainted LOSSES (the raw >= -0.001 skip, m1442) warn."""

    def _warn(self, country):
        from taxjson.lib.missing_history import \
            detect_superficial_loss_warnings
        loss = {"symbol": "SLW.TO", "date": "2025-03-01",
                "date_settle": "2025-03-01", "gain": -50.0,
                "account": "margin"}
        tainted = [{"symbol": "SLW.TO", "date": d, "date_settle": d,
                    "qty": -1, "proceeds": 10.0, "account": "margin"}
                   for d in ("2025-01-29", "2025-01-30", "2025-03-31",
                             "2025-04-01")]
        return detect_superficial_loss_warnings([loss], tainted,
                                                country=country)

    @rule("CA-ACB-12")
    def test_canada_window_is_30_days_each_side(self):
        w, = self._warn("canada")
        self.assertEqual([t["days_offset"] for t in w["tainted_dispositions"]],
                         [-30, 30])
        self.assertEqual(w["loss_amount"], 50.0)
        self.assertTrue(w["message"].startswith("Clean loss of 50.00 on "
                                                "SLW.TO"), w["message"])
        self.assertIn("±30 days", w["message"])

    @rule("US-BASIS-04")
    def test_usa_window_is_30_days_each_side(self):
        w, = self._warn("usa")
        self.assertEqual([t["days_offset"] for t in w["tainted_dispositions"]],
                         [-30, 30])
        self.assertEqual(w["loss_amount"], 50.0)

    @rule("CA-ACB-12")
    def test_pipeline_no_warning_for_denied_or_non_loss_rows(self):
        # p1: a tainted $0-basis sale at a GAIN with a buy inside its
        # window; p3: a clean loss fully denied (gain 0): neither is a
        # manual superficial-loss case.
        rows = [
            _tx("BUYSELL", "2025-02-03", "PHW.TO", -50, 500.0),
            _tx("BUYSELL", "2025-02-10", "PHW.TO", 100, -1200.0),
            _tx("BUYSELL", "2025-02-20", "PHW.TO", -40, 360.0),
            _tx("BUYSELL", "2025-03-05", "PHW.TO", 10, -90.0),
        ]
        res, err = _gains(rows, [("PHW.TO", "margin")])
        p3 = [t for t in res["transactions"]
              if t.get("date") == "2025-02-20" and "gain" in t]
        self.assertEqual(len(p3), 1)
        self.assertAlmostEqual(p3[0]["gain"], 0.0, places=6)
        self.assertLess(p3[0]["raw_gain"], -0.001)
        self.assertNotIn("superficial_loss_warnings", res)
        self.assertNotIn("superficial-loss check", err)


if __name__ == "__main__":
    unittest.main()
