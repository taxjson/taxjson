"""Fix round 2026-10-29 (round 2), group transfers: moves between
accounts, listing journals, spin-offs, renames, sanity's cost reasons,
the splits view and distribution sizing.

TJ-0419  find-missing-history --write-purchases dated a drafted IB
         lot on IB's Closed Lots date (the trade date), where a .tt line
         under tax_date settle needs the settlement date.
TJ-0437  an in-kind move whose registered leg posted more than 10 days
         after the taxable one passed in silence: no deemed sale, the
         shares held in both accounts, and an INKIND line booked it "to
         a plan outside the project". A slower pair (up to 45 days, the
         in leg after the out leg, nothing else pairing) is now named
         NOT booked with the line that books it; the line pairs it with
         the plan's leg.
TJ-0438  the RBC US-dollar units warning built its EXTRACT line from the
         first row's description: a transfer's 'TRANSFER' word left the
         fund's sale under the US listing, and ticker-map --suggest then
         called the sale's own line already answered.
TJ-0439  an unbooked in-kind withdrawal's taxable leg was also a NO-cost
         transfer-in "from outside your books", whose advice (the
         sending plan's purchase) booked the wrong cost.
TJ-0440  a journal between two listings the run joins itself (RBC ZZD
         to ZZD.U, IB QZK.US out to Questrade QZK.TO in) was still a
         NO-cost transfer-in to fix: the arrivals' pairing keyed the
         rows through ticker.map alone, not the run's effective map.
TJ-0441  the end-of-run s.86.1 reminder told a Canadian parent's
         spin-off booked as rollover_s_86_1 (as docs/limits.md says to)
         to file an election that is a foreign parent's only.
TJ-0442  a trade in an old ticker after its .tt RENAME: find-missing-
         history asked for the purchase (an older export, a BUYSELL),
         and --write-missing-history said the rows never go short. Both
         now name the RENAME line's late= as the fix, and so does the
         checklist's missing-history step.
TJ-0443  form-export's footer counted an in-kind contribution's loss
         (nil for good, s.40(2)(g)(iv)) as a denied superficial loss
         and pointed to wash-sales, which lists none.
TJ-0444  sanity called the spun-off shares' cost difference
         "unexplained" (a missing or mis-costed purchase) where it is
         the spin-off's allocation, as it already said of the parent.
TJ-0445  splits counted a consolidation's fraction as needing
         attention ("expect cash in lieu") when its cash-in-lieu sale
         was booked.
TJ-0491  a rename-only SPLIT (ratio 0) moved no shares in the
         distribution sizing walk: the new ticker's distribution was
         skipped as "no shares held".
TJ-0507  the standalone taxjson-apply-distributions summed every
         account of a book and put the whole ADJUST on one of them.
TJ-0508  taxjson-apply-distributions without --country crashed on a
         settlement-straddling warning (is_usa(None)).

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0", **(env or {}))
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


# --------------------------------------------------------------------
# In-kind moves slower than the window (TJ-0437, TJ-0439)
# --------------------------------------------------------------------

def _ik_leg(acct, qty, date, plan=""):
    from taxjson.lib.in_kind import Leg
    return Leg(account=acct, broker="ib", key="QZK.TO", plan=plan,
               row={"action": "TRANSFER", "symbol": "QZK.TO",
                    "quantity": qty, "date": date})


class TestSlowInKindPairing(unittest.TestCase):

    @rule("CA-INKIND-01")
    def test_a_lone_pair_15_days_apart_is_named_not_booked(self):
        from taxjson.lib import in_kind as IK
        p = IK.pair_all([_ik_leg("qt", -100, "2024-05-01"),
                         _ik_leg("rrsp", 100, "2024-05-16", "rrsp")])
        self.assertEqual(len(p.moves), 1)
        m = p.moves[0]
        self.assertEqual((m.kind, m.registered.account, m.slow, m.problem,
                          m.booked),
                         ("contribution", "rrsp", 15, "slow", False))
        self.assertEqual(p.unpaired, [])
        self.assertEqual(m.as_dict()["slow"], 15)

    @rule("CA-INKIND-01")
    def test_another_candidate_or_a_later_out_leg_pairs_nothing(self):
        from taxjson.lib import in_kind as IK
        two = IK.pair_all([_ik_leg("qt", -100, "2024-05-01"),
                           _ik_leg("rrsp", 100, "2024-05-16", "rrsp"),
                           _ik_leg("tfsa", 100, "2024-05-20", "tfsa")])
        self.assertEqual(two.moves, [])
        # An in leg BEFORE the out leg is never its arrival.
        early = IK.pair_all([_ik_leg("qt", -100, "2024-05-20"),
                             _ik_leg("rrsp", 100, "2024-05-01", "rrsp")])
        self.assertEqual(early.moves, [])
        # Past 45 days: two moves of their own.
        far = IK.pair_all([_ik_leg("qt", -100, "2024-05-01"),
                           _ik_leg("rrsp", 100, "2024-06-20", "rrsp")])
        self.assertEqual(far.moves, [])
        # A taxable in leg of the same units counts as a candidate too.
        own = IK.pair_all([_ik_leg("qt", -100, "2024-05-01"),
                           _ik_leg("rrsp", 100, "2024-05-16", "rrsp"),
                           _ik_leg("ib", 100, "2024-05-25")])
        self.assertEqual(own.moves, [])

    @rule("CA-INKIND-01")
    def test_a_line_pairs_the_slow_leg_and_books_it(self):
        from taxjson.lib import in_kind as IK
        legs = [_ik_leg("qt", -100, "2024-05-01"),
                _ik_leg("rrsp", 100, "2024-05-16", "rrsp")]
        line = ("qt", {"date": "2024-05-01", "symbol": "QZK.TO",
                       "quantity": -100.0, "currency": "CAD",
                       "total": 3300.0, "plan": "", "source": "a.tt:1"})
        own, declared = IK.line_legs([line], legs)
        moves = IK.pair_all(legs, own=own, declared=declared).moves
        probs = IK.apply_lines(moves, [line], legs)
        self.assertEqual(probs, [])
        self.assertEqual(len(moves), 1)
        m = moves[0]
        self.assertEqual((m.registered.account, m.slow, m.problem, m.fmv,
                          m.booked), ("rrsp", 15, "", 3300.0, True))
        self.assertEqual(m.pair_text(), "qt → rrsp")


def _ik_project(td, files, name="p"):
    from test_feat_in_kind import _project
    return _project(td, files, name=name)


@rule("CA-INKIND-01", "CA-INKIND-02", "CA-INKIND-05")
class TestSlowInKindRun(unittest.TestCase):
    """End to end: margin (IB) and rrsp (Questrade) legs 17 or 19 days
    apart."""

    def _contribution(self, td, line=None):
        from test_feat_in_kind import _QT, _ib, _xfer_qt
        files = {"inputs/margin/ib.csv": _ib(
                    [("2025-01-10", 100, 10.0)],
                    [("2025-03-14", -100, 1500)]),
                 "inputs/rrsp/questrade.csv": _QT
                 + _xfer_qt("2025-03-31", 100)}
        if line:
            files["inputs/margin/inkind.tt"] = line
        return _ik_project(td, files)

    def test_contribution_17_days_apart_is_named_with_its_line(self):
        from test_feat_in_kind import _filing, _flat, _run
        with tempfile.TemporaryDirectory() as td:
            root = self._contribution(td)
            flat = _flat(_run(self, root))
            self.assertIn("in-kind move(s) between your taxable and "
                          "registered accounts NOT booked", flat)
            self.assertIn("contribution margin → rrsp: 100 QZK.TO on "
                          "2025-03-14 (out 2025-03-14, in 2025-03-31: 17 "
                          "days apart): NOT booked", flat)
            self.assertIn("`INKIND 2025-03-14 QZK.TO -100 CAD <price per "
                          "share>`", flat)
            self.assertEqual(_filing(root)["gain"], 0.0)

    def test_its_line_books_the_sale_with_the_plan_leg(self):
        from test_feat_in_kind import _filing, _flat, _run
        with tempfile.TemporaryDirectory() as td:
            root = self._contribution(
                td, "INKIND 2025-03-14 QZK.TO -100 CAD 15\n")
            flat = _flat(_run(self, root))
            self.assertIn("contribution margin → rrsp: 100 QZK.TO on "
                          "2025-03-14 (out 2025-03-14, in 2025-03-31: 17 "
                          "days apart) at 1,500.00 CAD", flat)
            self.assertNotIn("outside the project", flat)
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                             (1500.0, 1000.0, 500.0))

    def _withdrawal(self, td, line=None):
        from test_feat_in_kind import _QT, _ib, _qt, _xfer_qt
        files = {"inputs/rrsp/questrade.csv": _QT
                 + _qt("2025-01-06", "Buy", 50, 8.0)
                 + _xfer_qt("2025-05-01", -50),
                 "inputs/margin/ib.csv": _ib(
                     [("2025-07-02", -50, 20.0)],
                     [("2025-05-20", 50, 750)])}
        if line:
            files["inputs/margin/inkind.tt"] = line
        return _ik_project(td, files)

    def test_withdrawal_is_no_arrival_from_outside(self):
        from test_feat_in_kind import _flat, _run
        with tempfile.TemporaryDirectory() as td:
            flat = _flat(_run(self, self._withdrawal(td)))
            self.assertIn("withdrawal rrsp → margin: 50 QZK.TO on "
                          "2025-05-20 (out 2025-05-01, in 2025-05-20: 19 "
                          "days apart): NOT booked", flat)
            self.assertNotIn("NO cost", flat)
            self.assertNotIn("from outside your books", flat)
            self.assertNotIn("BUYSELL", flat)
            # The closing list names the move its totals miss.
            self.assertIn("Warning: 1 in-kind move with a registered "
                          "account NOT booked: QZK.TO (margin). What each "
                          "books (a contribution's sale, a withdrawal's "
                          "purchase) is NOT in `tjs sum`", flat)

    def test_find_missing_history_names_the_inkind_line(self):
        from tax_rules.dual import cli
        from test_feat_in_kind import _run
        with tempfile.TemporaryDirectory() as td:
            root = self._withdrawal(td)
            _run(self, root)
            r = cli(root, "find-missing-history")
            flat = " ".join((r.stdout + r.stderr).split())
            self.assertIn("! In-kind withdrawal NOT booked: add its INKIND "
                          "line, not a purchase (QZK.TO)", flat)
            self.assertNotIn("Add each AFFECTS row's purchase", flat)
            d = cli(root, "find-missing-history", "--details")
            flat = " ".join((d.stdout + d.stderr).split())
            self.assertIn("the in-kind withdrawal rrsp → margin of 50 on "
                          "2025-05-20 brought these shares and is NOT "
                          "booked", flat)
            self.assertNotIn("To fix a sale with no purchase", flat)

    def test_withdrawal_line_gives_the_fair_market_value_as_cost(self):
        from test_feat_in_kind import _filing, _run
        with tempfile.TemporaryDirectory() as td:
            root = self._withdrawal(
                td, "INKIND 2025-05-20 QZK.TO 50 CAD 15\n")
            _run(self, root)
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                             (1000.0, 750.0, 250.0))


# --------------------------------------------------------------------
# The s.86.1 filing reminder: a foreign parent's only (TJ-0441)
# --------------------------------------------------------------------

@rule("CA-CORP-06")
class TestS861ReminderForeignParentOnly(unittest.TestCase):

    def test_run_reminder_says_which_parent_files(self):
        from test_fix_runcore import _project, _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "inputs" / "margin" / "manifest.json").write_text(
                json.dumps({"elections": {"ev1": {
                    "summary": "2025-03-01 spin-off XEI.TO",
                    "election": "rollover_s_86_1",
                    "hints": {"allocated_acb_cad": 10.0}}}}))
            r = _run_cli(root, "run", "--no-input")
            flat = " ".join(r.stderr.split())
            self.assertIn("FILING REQUIRED for 1 election(s) when the "
                          "parent is a foreign corporation", flat)
            self.assertIn("if the parent is a foreign corporation: file "
                          "the s. 86.1 election with your return", flat)
            self.assertIn("a Canadian parent's tax-deferred spin-off (a "
                          "butterfly, an s. 86 reorganization) has no "
                          "election of yours to file", flat)

    def test_spinoffs_view_and_option_text(self):
        from taxjson.lib import corp_actions as CA
        from taxjson.lib import corp_views as CV
        self.assertIn("a Canadian parent's butterfly or s.86 "
                      "reorganization has nothing to file",
                      CV._ALLOC_ELECTIONS["rollover_s_86_1"])
        self.assertIn("if the parent is a foreign corporation",
                      CA.FILING_REQUIRED_ELECTIONS["rollover_s_86_1"])


# --------------------------------------------------------------------
# form-export's footer: an in-kind contribution's loss (TJ-0443)
# --------------------------------------------------------------------

@rule("CA-INKIND-03")
class TestFormExportContributionLossFooter(unittest.TestCase):

    @staticmethod
    def _ent(symbol, proceeds, cost, **kw):
        e = {"date": "2024-06-03", "date_settle": "2024-06-03",
             "symbol": symbol, "qty": -50, "proceeds": proceeds,
             "cost": cost, "gain": 0.0, "disallowed_amount": 0.0,
             "days_held": 30, "term": None, "direction": "LONG",
             "commission": 0.0, "fee": 0.0, "account": "margin",
             "is_option": False}
        e.update(kw)
        return e

    def _footer(self, entries):
        from taxjson.bin.taxjson_form_export import (build_schedule3,
                                                     render_schedule3)
        rep = build_schedule3(entries, 2024)
        return " ".join(render_schedule3(rep, 2024, "CAD",
                                         details=False).split())

    def test_contribution_loss_is_not_a_superficial_loss(self):
        text = self._footer([self._ent("ZZL.TO", 600.0, 1004.95,
                                       denied_contribution=404.95)])
        self.assertIn("1 row(s) with a loss denied for good on an in-kind "
                      "contribution (s.40(2)(g)(iv)) — tjs form-export "
                      "--details", text)
        self.assertNotIn("superficial", text)
        self.assertNotIn("wash-sales", text)

    def test_each_kind_is_counted_apart(self):
        text = self._footer([
            self._ent("ZZL.TO", 600.0, 1004.95, denied_contribution=404.95),
            self._ent("ZZQ.TO", 500.0, 600.0, gain=-50.0,
                      disallowed_amount=50.0)])
        self.assertIn("1 row(s) with a denied superficial loss: the ACB "
                      "shown is reduced by it — tjs wash-sales", text)
        self.assertIn("1 row(s) with a loss denied for good on an in-kind "
                      "contribution", text)


# --------------------------------------------------------------------
# sanity: the spun-off shares' cost is the allocation (TJ-0444)
# --------------------------------------------------------------------

class TestSanitySpinOffNewShares(unittest.TestCase):

    @staticmethod
    def _row(action, symbol, **kw):
        r = {"action": action, "symbol": symbol, "account": "margin",
             "date": "2024-06-10", "currency": "CAD"}
        r.update(kw)
        return r

    @rule("CA-CORP-06")
    def test_the_new_shares_carry_the_spin_off_reason(self):
        from taxjson.lib import positions_check as PC
        ev = "20240610-zzp-zzqs-0001"
        rows = [self._row("BUYSELL", "ZZP.TO", quantity=100.0,
                          net_amount=-4001.0),
                # The allocation: the parent's cost reduction and the
                # new shares' purchase, one event.
                self._row("ADJUST", "ZZP.TO", net_amount=-800.0,
                          corp_event_id=ev),
                self._row("BUYSELL", "ZZQS.TO", quantity=25.0,
                          net_amount=800.0, corp_event_id=ev),
                # A merger's new shares: no parent cost reduction.
                self._row("BUYSELL", "ZZM.TO", quantity=10.0,
                          net_amount=500.0,
                          corp_event_id="20240701-zzo-zzm-0002"),
                # The same event id in another account's rows only.
                self._row("BUYSELL", "ZZX.TO", quantity=5.0,
                          net_amount=50.0, account="other",
                          corp_event_id=ev)]
        self.assertEqual(PC.spinoff_symbols(rows), {"ZZP.TO", "ZZQS.TO"})
        res = PC.compare_cost(
            symbol="ZZQS.TO", broker_cost=1000.0, cost_currency="CAD",
            cost_kind="book value", position_currency="CAD", base="CAD",
            country="canada", filing={"total_cost": 800.0,
                                      "deferred_wash": 0.0},
            own=800.0, native=None, pooled=False, roc=False,
            spinoff="ZZQS.TO" in PC.spinoff_symbols(rows),
            tol_abs=1.0, tol_rel=0.001)
        self.assertEqual(res["reasons"], ["spin-off"])

    @rule("US-CORP-07")
    def test_a_355_spin_off_names_both(self):
        from taxjson.lib import positions_check as PC
        rows = [self._row("ADJUST", "ZZP.US", net_amount=-80.0,
                          type="spinoff_355"),
                self._row("BUYSELL", "ZZQS.US", quantity=25.0,
                          net_amount=80.0, type="spinoff_355")]
        self.assertEqual(PC.spinoff_symbols(rows), {"ZZP.US", "ZZQS.US"})


# --------------------------------------------------------------------
# splits: a consolidation's fraction whose cash in lieu is booked (TJ-0445)
# --------------------------------------------------------------------

class TestSplitsBookedCashInLieu(unittest.TestCase):

    @staticmethod
    def _items(rows):
        from taxjson.lib.corp_views import splits
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "m_base.json").write_text(json.dumps(rows))
            cfg = {"settings": {"base_currency": "CAD",
                                "country": "canada"},
                   "accounts": {"m": {"type": "taxable"}}}
            return splits(root, cfg)

    @staticmethod
    def _r(action, date, sym, qty, net=0.0):
        return {"action": action, "date": date, "time": "09:30:00",
                "symbol": sym, "quantity": qty, "net_amount": net}

    @rule("CA-CORP-05")
    def test_a_booked_fraction_needs_no_attention(self):
        from taxjson.lib.corp_views import render_splits
        rows = [self._r("BUYSELL", "2024-01-15", "ZZCL.TO", 1005, 1006.0),
                self._r("SPLIT", "2024-03-04", "ZZCL.TO", 0.1),
                self._r("BUYSELL", "2024-03-05", "ZZCL.TO", -0.5, 6.0),
                self._r("BUYSELL", "2024-04-02", "ZZCL.TO", -100, 1199.0)]
        items = self._items(rows)
        self.assertEqual(items[0]["flags"], ["CIL"])
        self.assertIn("cash in lieu is booked (0.5 sold on 2024-03-05)",
                      " ".join(items[0]["why"]))
        text = "\n".join(render_splits(items, width_=120))
        self.assertNotIn("need attention", text)
        self.assertNotIn("expect cash in lieu", text)
        self.assertIn("each fraction's cash in lieu is booked", text)

    @rule("CA-CORP-05")
    def test_an_unpaid_fraction_still_needs_attention(self):
        from taxjson.lib.corp_views import render_splits
        for later in ([],                                     # none
                      [self._r("BUYSELL", "2024-03-05", "ZZCL.TO",
                               -0.4, 5.0)],                   # not it
                      [self._r("BUYSELL", "2024-06-05", "ZZCL.TO",
                               -0.5, 6.0)]):                  # too late
            rows = [self._r("BUYSELL", "2024-01-15", "ZZCL.TO", 1005,
                            1006.0),
                    self._r("SPLIT", "2024-03-04", "ZZCL.TO", 0.1)] + later
            items = self._items(rows)
            self.assertEqual(items[0]["flags"], ["FRACTION"], later)
            self.assertIn("1 split(s) need attention.",
                          "\n".join(render_splits(items, width_=120)))


# --------------------------------------------------------------------
# --write-purchases: an IB lot on the project's tax_date (TJ-0419)
# --------------------------------------------------------------------

def _lot_ib(rows: str) -> str:
    return ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,U1234567\n'      # pii-ok
            'Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
            'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n' + rows)


_LOT_SALE = ('Trades,Data,Order,Stocks,USD,ZZG,"2024-03-18, 10:02:11",-20,'
             '18.50,18.50,370.00,-1.00,-301.00,68.00,0,C\n'
             'Trades,Data,ClosedLot,Stocks,USD,ZZG,"2023-06-01, 09:30:00",'
             '20,15.05,,,,301.00,,,\n')
_LOT_BUY = ('Trades,Data,Order,Stocks,USD,ZZG,"2023-06-01, 09:30:00",20,'
            '15.00,15.00,-300.00,-1.00,301.00,0,0,O\n')


class TestDraftedLotSettlementDate(unittest.TestCase):
    """IB's Closed Lots date is the lot's trade date; a .tt line's one
    date is its settlement date under tax_date settle."""

    @staticmethod
    def _draft(body, **kw):
        from test_basis_drafts import _book, _parsed
        from taxjson.lib.missing_history import draft_purchases
        drafts, gaps = draft_purchases(_book(_parsed(body)), **kw)
        return drafts

    @rule("CA-ACB-15")
    def test_canada_settle_takes_the_settlement_date(self):
        from test_basis_drafts import _lot, _sell
        from taxjson.lib.missing_history import format_purchase_drafts
        body = (_sell('ZZG', '2024-03-18, 10:02:11', -20, 18.5, 301)
                + _lot('ZZG', '2023-06-01', 20, 15.05, 301))
        d, = self._draft(body, country='canada')
        self.assertEqual(d.tt_line().split()[1], '2023-06-05')   # T+2
        notes = ' '.join(d.comments)
        self.assertIn('opened 2023-06-01', notes)
        self.assertIn("dated 2023-06-05, the lot's settlement date", notes)
        self.assertIn('real purchase date, its settlement date', notes)
        text = format_purchase_drafts([d], [], country='canada',
                                      account='margin')
        self.assertIn('(its settlement date: tax_date settle)', text)
        # After the T+1 cutover: one settlement day.
        body = (_sell('ZZG', '2024-09-03, 10:02:11', -20, 18.5, 301)
                + _lot('ZZG', '2024-06-03', 20, 15.05, 301))
        d, = self._draft(body, country='canada')
        self.assertEqual(d.tt_line().split()[1], '2024-06-04')
        # A trade-basis project keeps IB's date.
        body = (_sell('ZZG', '2024-03-18, 10:02:11', -20, 18.5, 301)
                + _lot('ZZG', '2023-06-01', 20, 15.05, 301))
        d, = self._draft(body, country='canada', date_basis='trade')
        self.assertEqual(d.tt_line().split()[1], '2023-06-01')
        self.assertNotIn("the lot's settlement date", ' '.join(d.comments))
        text = format_purchase_drafts([d], [], country='canada',
                                      account='margin', date_basis='trade')
        self.assertNotIn('tax_date settle', text)

    @rule("US-BASIS-08")
    def test_us_trade_basis_keeps_ibs_date(self):
        from test_basis_drafts import _lot, _sell
        body = (_sell('ZZG', '2024-03-18, 10:02:11', -20, 18.5, 301)
                + _lot('ZZG', '2023-06-01', 20, 15.05, 301))
        d, = self._draft(body, country='usa')
        self.assertEqual(d.tt_line().split()[1], '2023-06-01')
        self.assertNotIn("the lot's settlement date", ' '.join(d.comments))

    @rule("CA-ACB-15")
    def test_the_drafted_line_costs_what_ibs_own_row_does(self):
        # 2023-06-01 and its settlement day 2023-06-05 at different
        # rates (a private HOME's rate cache: the suite's synthetic one
        # is flat): the kept draft and IB's own purchase row give one
        # ACB, at the settlement day's rate.
        import _hermetic

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            cache = _hermetic.rate_cache()
            obs = cache["_boc"]["USDCAD"]["obs"]
            obs["2023-06-01"], obs["2023-06-05"] = "1.3000", "1.4000"
            (home / ".currency_price_cache.json").write_text(
                json.dumps(cache))
            env = {"HOME": str(home),
                   "XDG_CACHE_HOME": str(home / ".cache")}

            def project(name, files):
                root = _xl_project(Path(td) / name, files,
                                   '[accounts.margin]\ntype = "taxable"\n')
                (root / "work" / "to_base.csv").unlink()
                return root

            def acb(root):
                r = _cli(root, "run", "--no-input", env=env)
                self.assertEqual(r.returncode, 0,
                                 (r.stdout + r.stderr)[-2000:])
                return json.loads(_cli(root, "sum", "--json",
                                       env=env).stdout)[
                    "filing"]["totals"]["acb"]

            a = project("a", {"inputs/margin/ib_2024.csv":
                              _lot_ib(_LOT_SALE)})
            _cli(a, "run", "--no-input", env=env)
            r = _cli(a, "find-missing-history", "--write-purchases",
                     env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            draft = a / "inputs" / "margin" / "purchases_draft.tt.txt"
            self.assertIn("BUYSELL  2023-06-05  09:30:00  ZZG.US  20  USD",
                          draft.read_text())
            draft.rename(a / "inputs" / "margin" / "purchases.tt")
            b = project("b", {
                "inputs/margin/ib_2024.csv": _lot_ib(_LOT_SALE),
                "inputs/margin/ib_2023.csv": _lot_ib(_LOT_BUY)})
            self.assertEqual(acb(a), acb(b))
            self.assertAlmostEqual(acb(a), 301.00 * 1.40, places=2)


# --------------------------------------------------------------------
# find-missing-history and a trade after a rename (TJ-0442)
# --------------------------------------------------------------------

_LATE_QT = (
    "2025-02-03 09:30:00 AM,2025-02-05 12:00:00 AM,Buy,ZZX.TO,ZZX SAMPLE "
    "CORP,100,10.00,1000.00,0.00,-1000.00,CAD,55500002,Trades,"  # pii-ok
    "Individual\n"
    "2025-05-12 09:30:00 AM,2025-05-14 12:00:00 AM,Sell,ZZX.TO,ZZX SAMPLE "
    "CORP,-50,6.00,300.00,0.00,300.00,CAD,55500002,Trades,"  # pii-ok
    "Individual\n"
    "2025-06-10 09:30:00 AM,2025-06-12 12:00:00 AM,Sell,ZZY.TO,ZZY SAMPLE "
    "CORP,-50,6.00,300.00,0.00,300.00,CAD,55500002,Trades,"  # pii-ok
    "Individual\n")


@rule("CA-ACB-RENAME", "CA-ACB-11")
class TestLateRenameShort(unittest.TestCase):

    def _root(self, td, late=""):
        from test_feat_in_kind import _QT, _project
        return _project(td, {
            "inputs/margin/qt.csv": _QT + _LATE_QT,
            "inputs/margin/events.tt":
                f"RENAME 2025-05-01 ZZX.TO ZZY.TO{late}\n"},
            accounts='[accounts.margin]\ntype = "taxable"\n')

    @staticmethod
    def _flat(r):
        return " ".join((r.stdout + r.stderr).split())

    def test_report_names_the_rename_not_a_purchase(self):
        from tax_rules.dual import cli
        from test_feat_in_kind import _run
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            _run(self, root)
            flat = self._flat(cli(root, "find-missing-history"))
            self.assertIn("ZZX.TO margin CAD -50.0000 2025-05-12", flat)
            self.assertIn("! Trades after a rename: declare them, not a "
                          "purchase (ZZX.TO) — tjs renames", flat)
            self.assertNotIn("Add each AFFECTS row's purchase", flat)
            flat = self._flat(cli(root, "find-missing-history",
                                  "--details"))
            self.assertIn("a trade in ZZX.TO after its rename to ZZY.TO on "
                          "2025-05-01: the broker booked the renamed "
                          "shares under the old ticker", flat)
            self.assertIn("`RENAME 2025-05-01 ZZX.TO ZZY.TO late=fold`",
                          flat)
            self.assertNotIn("To fix a sale with no purchase", flat)

    def test_write_missing_history_names_the_rename(self):
        from tax_rules.dual import cli
        from test_feat_in_kind import _run
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            _run(self, root)
            r = cli(root, "find-missing-history", "--write-missing-history")
            flat = self._flat(r)
            self.assertIn("not written: 1 short(s) a trade in an old "
                          "ticker after its rename makes", flat)
            self.assertIn("`RENAME 2025-05-01 ZZX.TO ZZY.TO late=fold` "
                          "(margin)", flat)
            self.assertNotIn("the rows never go short", flat)
            self.assertNotIn("no sale with no purchase in your files", flat)
            self.assertFalse((root / "inputs" / "margin"
                              / "missing_history.tt").exists())

    def test_the_declaration_clears_it(self):
        from tax_rules.dual import cli
        from test_feat_in_kind import _run
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, " late=fold")
            _run(self, root)
            flat = self._flat(cli(root, "find-missing-history"))
            self.assertNotIn("ZZX.TO margin", flat)
            self.assertNotIn("after a rename", flat)

    def test_checklist_step_names_the_fix(self):
        from types import SimpleNamespace
        from taxjson.lib import checklist as CL
        out = ("AFFECTS 2025 - missing basis distorts this year's gain; "
               "fix before filing:\n"
               "Symbol  Account  Cur  PeakShort\n"
               "-------------------------------\n"
               "ZZX.TO  margin   CAD   -50.0000\n"
               "  a trade in ZZX.TO after its rename to ZZY.TO on "
               "2025-05-01: the broker booked\n"
               "  the renamed shares under the old ticker.\n"
               "QZW.TO  margin   CAD   -30.0000\n"
               "  the in-kind withdrawal rrsp → margin of 30 on "
               "2025-05-20 brought these\n"
               "QZV.TO  margin   CAD   -10.0000\n"
               "\n")
        ctx = SimpleNamespace(sub=lambda *a, **k: (0, out, ""), year=2025)
        r = CL.d_missing_history(ctx)
        self.assertEqual(r.status, "attention")
        self.assertIn("3 position(s) with missing basis affect 2025: "
                      "ZZX.TO (margin; a trade after its rename: `tjs "
                      "renames`), QZW.TO (margin; an in-kind withdrawal "
                      "NOT booked: its INKIND line), QZV.TO (margin)",
                      r.detail)


# --------------------------------------------------------------------
# RBC US-dollar units: the EXTRACT line's words (TJ-0438)
# --------------------------------------------------------------------

_RBC_ACCT = "99900001"  # pii-ok (synthetic)
_RBC_HEAD = ('Account Activity Detail Report\n'
             f'"Account Number: {_RBC_ACCT}"\n'
             '"Date Range: January 1, 2024 - December 31, 2024"\n\n'
             'Date,Activity,Symbol,Description,Quantity,Price,'
             'Settlement Date,Currency,Value,Amount\n')
_RBC_ZZD = (
    '2024-03-04,Buy,ZZD,ZZD US DOLLAR CURRENCY ETF - Buy,300,14.20,'
    '2024-03-06,CAD,-4260.00,-4269.95\n'
    '2024-03-08,Transfers,ZZD,ZZD US DOLLAR CURRENCY ETF TRANSFER,-300,0,'
    '2024-03-08,CAD,0.00,0.00\n'
    '2024-03-08,Transfers,ZZD.U,ZZD US DOLLAR CURRENCY ETF TRANSFER,300,'
    '0,2024-03-08,USD,0.00,0.00\n'
    '2024-03-12,Sell,ZZD.U,ZZD US DOLLAR CURRENCY ETF - Sell,300,10.50,'
    '2024-03-14,USD,3150.00,3140.05\n')


def _rbc_brokerage(tmp: Path, ticker_map: str = ""):
    """(symbol of each transaction and transfer row, stderr) of
    taxjson-brokerage on the ZZD export, with `ticker_map` as the
    project's EXTRACT lines."""
    csv = tmp / "rbc.csv"
    csv.write_text(_RBC_HEAD + _RBC_ZZD)
    args = ["--brokerage", "rbc_direct", "--account", "margin",
            "--transfers-out", str(tmp / "transfers.json")]
    if ticker_map:
        tm = tmp / "ticker.map"
        tm.write_text(ticker_map)
        args += ["--ticker-map", str(tm), "--security-overrides", str(tm)]
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="0")
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_brokerage",
                           args + [str(csv)]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=120)
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)["transactions"]
    moves = json.loads((tmp / "transfers.json").read_text())
    if isinstance(moves, dict):
        moves = moves.get("transfers") or moves.get("transactions") or []
    return ([(t["action"], t["symbol"]) for t in rows]
            + [("TRANSFER", t["symbol"]) for t in moves]), r.stderr


def _xl_project(td, files, accounts):
    """A 2024 Canadian project (work/to_base.csv seeded; the run takes
    its rates from the suite's synthetic cache, USD->CAD 1.35)."""
    import datetime as _dt
    from tax_rules.dual import settings_for
    root = Path(td) / "p"
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for("canada", year=2024, source_currencies=["USD"])
        + accounts)
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    d, lines = _dt.date(2023, 12, 1), []
    while d <= _dt.date(2025, 1, 31):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.2500 boc")
        d += _dt.timedelta(days=1)
    (root / "work").mkdir(exist_ok=True)
    (root / "work" / "to_base.csv").write_text("\n".join(lines) + "\n")
    return root


def _xl_ib(acct="U5550015"):                                    # pii-ok
    """IB: 100 QZK on NYSE bought 2024-02-01, 100 moved out 2024-05-01."""
    return (
        'Statement,Header,Field Name,Field Value\n'
        'Statement,Data,BrokerName,Interactive Brokers\n'
        'Statement,Data,Title,Activity Statement\n'
        'Statement,Data,Period,"January 1, 2024 - December 31, 2024"\n'
        'Account Information,Header,Field Name,Field Value\n'
        f'Account Information,Data,Account,{acct}\n'
        'Account Information,Data,Base Currency,CAD\n'
        'Financial Instrument Information,Header,Asset Category,Symbol,'
        'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
        'Expiry,Delivery Month,Type,Strike,Code\n'
        'Financial Instrument Information,Data,Stocks,QZK,QZK SAMPLE CORP,'
        '999300015,CA9990015015,,NYSE,1,,,COMMON,,\n'
        'Trades,Header,DataDiscriminator,Asset Category,Currency,Account,'
        'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,'
        'Basis,Realized P/L,MTM P/L,Code\n'
        f'Trades,Data,Order,Stocks,USD,{acct},QZK,"2024-02-01, 10:00:00",'
        '100,30.00,30.00,-3000.00,-1.00,3001.00,0,0,O\n'
        'Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
        'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,'
        'Realized P/L,Cash Amount,Code\n'
        'Transfers,Data,Stocks,USD,QZK,2024-05-01,ACATS,Out,--,5550009,'  # pii-ok
        '-100,0,-3300.00,0,0,\n')


_XL_QT = (
    "Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
    "Price,Gross Amount,Commission,Net Amount,Currency,Activity Type,"
    "Account #,Account Type\n"
    "2024-05-06,2024-05-06,TF6,QZK.TO,QZK SAMPLE CORP TRANSFER,100,0.00,"
    "0.00,0.00,0.00,CAD,Transfers,55500131,Margin\n"  # pii-ok
    "2024-09-03,2024-09-04,Sell,QZK.TO,QZK SAMPLE CORP,-100,46.00,4600.00,"
    "-4.95,4595.05,CAD,Trades,55500131,Margin\n")  # pii-ok


@rule("CA-ACB-TRANSFER-BV", "CA-ACB-OWN-MOVE")
class TestRunJoinedJournalIsNoArrival(unittest.TestCase):
    """TJ-0440: a journal between two listings the run joins itself is
    one move inside the same holdings, never a NO-cost arrival."""

    @staticmethod
    def _flat(r):
        return " ".join((r.stdout + r.stderr).split())

    def test_rbc_units_journal_in_one_account(self):
        from tax_rules.dual import cli
        with tempfile.TemporaryDirectory() as td:
            root = _xl_project(
                td, {"inputs/margin/rbc.csv": _RBC_HEAD + _RBC_ZZD},
                '[accounts.margin]\ntype = "taxable"\n')
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, self._flat(r)[-2000:])
            flat = self._flat(r)
            self.assertIn("joined as one security by their transfer "
                          "journal: ZZD.TO ↔ ZZD.U.US", flat)
            self.assertNotIn("NO cost", flat)
            self.assertNotIn("kept out with no cost", flat)
            t = self._flat(cli(root, "transfers"))
            self.assertIn("2024-03-08 margin ZZD.U.US 300 transfer-in "
                          "0.00 - USD sidecar own_move", t)
            # The filing figures were right and stay as they were.
            tot = json.loads(cli(root, "sum", "--json").stdout)[
                "filing"]["totals"]
            self.assertEqual((tot["proceeds"], tot["acb"], tot["gain"]),
                             (4252.5, 4269.95, -30.88))

    def test_ib_to_questrade_listing_change_across_accounts(self):
        from tax_rules.dual import cli
        with tempfile.TemporaryDirectory() as td:
            root = _xl_project(td, {
                "inputs/ib/ib.csv": _xl_ib(),
                "inputs/qt/qt.csv": _XL_QT},
                '[accounts.ib]\ntype = "taxable"\n'
                '[accounts.qt]\ntype = "taxable"\n')
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, self._flat(r)[-2000:])
            flat = self._flat(r)
            self.assertIn("joined as one security by their transfer "
                          "journal: QZK.US ↔ QZK.TO", flat)
            self.assertNotIn("NO cost", flat)
            self.assertNotIn("go short", flat)
            t = self._flat(cli(root, "transfers"))
            self.assertIn("qt QZK.TO 100 transfer-in", t)
            self.assertIn("own_move", t)
            m = self._flat(cli(root, "find-missing-history"))
            self.assertNotIn("AFFECTS", m)
            # 4,595.05 less 3,001 USD at the suite's 1.35 + 4.95: as  # pii-ok
            # before the fix.
            tot = json.loads(cli(root, "sum", "--json").stdout)[
                "filing"]["totals"]
            self.assertEqual((tot["proceeds"], tot["acb"], tot["gain"]),
                             (4600.0, 4051.35, 543.7))


class TestRbcUsdUnitsExtractWords(unittest.TestCase):
    _LINE = "EXTRACT ZZD US DOLLAR CURRENCY ETF | USD | ZZD.U.TO"

    def test_suggested_words_are_common_to_every_usd_row(self):
        with tempfile.TemporaryDirectory() as td:
            _syms, err = _rbc_brokerage(Path(td))
        self.assertIn(self._LINE, err)
        self.assertNotIn("ETF TRANSFER | USD", err)

    def test_the_suggested_line_moves_the_sale_and_the_transfer(self):
        with tempfile.TemporaryDirectory() as td:
            syms, err = _rbc_brokerage(Path(td), self._LINE + "\n")
        self.assertIn(("BUYSELL", "ZZD.U.TO"), syms)
        self.assertIn(("TRANSFER", "ZZD.U.TO"), syms)
        self.assertNotIn(("BUYSELL", "ZZD.U.US"), syms)
        self.assertNotIn("US-dollar class", err)

    def test_a_narrower_line_does_not_answer_the_suggestion(self):
        from taxjson.lib.ticker_map_suggest import (Suggestion, already,
                                                    map_state)
        sug = Suggestion(self._LINE, "reads as a US-dollar class",
                         "work/margin_rbc.json.diag")
        with tempfile.TemporaryDirectory() as td:
            tm = Path(td) / "ticker.map"
            tm.write_text("EXTRACT ZZD US DOLLAR CURRENCY ETF TRANSFER | "
                          "USD | ZZD.U.TO\n")
            self.assertIsNone(already(sug, map_state(tm)))
            tm.write_text("EXTRACT ZZD US DOLLAR | USD | ZZD.U.TO\n")
            self.assertEqual(already(sug, map_state(tm)),
                             "ticker.map has an EXTRACT line for it")


# --------------------------------------------------------------------
# Distribution sizing (taxjson-apply-distributions)
# --------------------------------------------------------------------

def _row(i, action, d, sym, q, n=0.0, acct="a", ds=None, **kw):
    out = {"id": i, "action": action, "date": d, "date_settle": ds or d,
           "symbol": sym, "quantity": q, "net_amount": n,
           "currency": "CAD", "account": acct}
    out.update(kw)
    return out


def _dist_main(book: dict, per_share: float, record_date: str,
               *argv: str):
    """Run taxjson-apply-distributions' main on `book` in a folder of
    its own: (exit code, the book after, stderr)."""
    from taxjson.bin.taxjson_apply_distributions import main
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "base.json"
        cfg = Path(td) / "taxjson.toml"
        base.write_text(json.dumps(book))
        cfg.write_text(f'[[distributions]]\nsymbol = "SYN"\n'
                       f'record_date = {record_date}\n'
                       f'per_share = {per_share}\n')
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main([str(base), "--config", str(cfg), *argv])
        return rc, json.loads(base.read_text()), err.getvalue()


@rule("CA-DIST-01")
class TestRenameOnlySplitSizing(unittest.TestCase):
    """TJ-0491: a SPLIT of ratio 0 is a rename only (the US engine
    skips its scaling and still moves the shares)."""

    def test_zero_ratio_rename_moves_the_shares(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions, balance_on)
        book = [_row("b", "BUYSELL", "2025-01-02", "OLD", 100, 500),
                _row("s", "SPLIT", "2025-06-01", "OLD", 0.0,
                     symbol_new="NEW")]
        self.assertAlmostEqual(balance_on(book, "NEW", "2025-12-31"), 100)
        self.assertAlmostEqual(balance_on(book, "OLD", "2025-12-31"), 0)
        self.assertAlmostEqual(balance_on(book, "OLD", "2025-05-31"), 100)
        err = io.StringIO()
        with redirect_stderr(err):
            doc, n = apply_distributions(
                {"transactions": [dict(t) for t in book]},
                [("NEW", "2025-12-31", 1.0)], "a", country="canada")
        self.assertEqual(n, 1, err.getvalue())
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"]
        self.assertEqual(len(adj), 1)
        self.assertEqual(adj[0]["symbol"], "NEW")
        self.assertAlmostEqual(adj[0]["net_amount"], 100.0)

    def test_a_real_split_still_scales(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        book = [_row("b", "BUYSELL", "2025-01-02", "OLD", 100, 500),
                _row("s", "SPLIT", "2025-06-01", "OLD", 2.0,
                     symbol_new="NEW")]
        self.assertAlmostEqual(balance_on(book, "NEW", "2025-12-31"), 200)


@rule("US-DIST-01")
class TestMultiAccountBookRefused(unittest.TestCase):
    """TJ-0507: one book is one account's; a book of two accounts is
    refused, never sized as one."""

    def _book(self):
        return {"metadata": {"target_currency": "USD"},
                "transactions": [
                    _row("a", "BUYSELL", "2025-01-01", "SYN", 10, 100, "a"),
                    _row("b", "BUYSELL", "2025-01-01", "SYN", 20, 200, "b")]}

    def test_two_accounts_refused_and_book_unchanged(self):
        for acct in ("a", "b"):
            book = self._book()
            rc, after, err = _dist_main(book, 1, "2025-06-01",
                                        "--account", acct,
                                        "--country", "usa")
            self.assertEqual(rc, 2, err)
            self.assertEqual(after, book)
            self.assertIn("2 accounts", err)
            self.assertIn("'a', 'b'", err)
            self.assertNotIn("Traceback", err)

    def test_one_account_book_still_applied(self):
        book = self._book()
        book["transactions"] = book["transactions"][:1]
        rc, after, err = _dist_main(book, 1, "2025-06-01",
                                    "--account", "a", "--country", "usa")
        self.assertEqual(rc, 0, err)
        adj = [t for t in after["transactions"] if t["action"] == "ADJUST"]
        self.assertEqual([(t["account"], t["net_amount"]) for t in adj],
                         [("a", 10.0)])


@rule("CA-DIST-01")
@rule("US-DIST-01")
class TestNoCountryNeutral(unittest.TestCase):
    """TJ-0508: the documented call without --country runs (neutral
    words), the settlement-straddle warning included."""

    def _book(self):
        return {"metadata": {"target_currency": "CAD"},
                "transactions": [
                    _row("old", "BUYSELL", "2025-01-01", "SYN", 100, 500),
                    _row("sale", "BUYSELL", "2025-06-02", "SYN", -50, 400,
                         ds="2025-06-04"),
                    _row("buy", "BUYSELL", "2025-06-02", "SYN", 10, 100,
                         ds="2025-06-03")]}

    def test_without_country_exits_zero_and_warns(self):
        rc, after, err = _dist_main(self._book(), 1, "2025-06-03")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("CountryError", err)
        adj = [t for t in after["transactions"] if t["action"] == "ADJUST"]
        self.assertEqual(len(adj), 1)
        self.assertAlmostEqual(adj[0]["net_amount"], 110.0)
        self.assertIn("cost adjustment", err)
        self.assertIn("per-lot engine", err)

    def test_canada_says_no_per_lot_warning(self):
        rc, _after, err = _dist_main(self._book(), 1, "2025-06-03",
                                     "--country", "canada")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("per-lot engine", err)

    def test_usa_warns(self):
        rc, _after, err = _dist_main(self._book(), 1, "2025-06-03",
                                     "--country", "usa")
        self.assertEqual(rc, 0, err)
        self.assertIn("per-lot engine", err)


class TestSecondRunNoStaleWarning(unittest.TestCase):
    """The renames view's denied-loss note (TJ-0357) read the gains files
    from the run's late-rename check, before the blended pass rebuilt
    them: a second `tjs run` of the demo warned four times that the
    wash-adjusted numbers were stale (TJ-0616)."""

    def test_the_demos_second_run_is_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "d"
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run",
                                   ["init", "--demo", str(d)]),
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=dict(os.environ, PYTHONPATH=str(SRC),
                         TAXJSON_OFFLINE="1"), timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            year = next(p for p in d.iterdir()
                        if (p / "taxjson.toml").is_file())
            for _ in range(2):
                r = _cli(year, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("was built before", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
