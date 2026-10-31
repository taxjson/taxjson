"""Fix round 8, group corp: spin-offs, renames, splits and reversals.

Every fixture is SYNTHETIC: made-up tickers (SYN, SPNA, ALPH, ZZP), accounts
99900001 and 99900002, invented amounts. Each test's files live in a folder
of their own.
"""
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from test_fix_1029_r7_corprbc import parse_one, rbc_row
from test_fix_1029_r7_transfers import _TAXABLE, _cli, _project
from test_fix_1029_r7e_parsers import _chain, _got
from test_fix_1029_r7d_rbcqt import qt_dis
from test_fix_1029_r7e_engine import _gains, _spin_rows, tx
from test_fix_1029_r7f_useng import (_HEAD, _ONE, _PARENT, _TWO, _blocks, _buy,
                                     _dis, _held, _run_355, _sell_spun, _trade)

from taxjson.lib import corp_actions as CA


# ===================================== TJ-1300 the survivor names the event
class TestTJ1300SurvivorNamesTheEvent(unittest.TestCase):
    def test_a_reversed_larger_posting_leaves_the_survivors_symbol(self):
        evs, _ = _chain(qt_dis("SPNB", 5, "SPNB CORP"), qt_dis("SPNA", 10),
                        qt_dis("SPNX", -10, "SPNX CORP"))
        self.assertEqual(_got(evs), [("SPNB.TO", 5)])

    def test_a_larger_code_posting_reversed_under_the_ticker(self):
        evs, err = _chain(qt_dis("SPNA", 5), qt_dis("D012345", 10, "SPNW WT"),
                          qt_dis("SPNW", -10, "SPNW WT"))
        self.assertEqual(_got(evs), [("SPNA.TO", 5)])
        self.assertNotIn("INTERNAL", err)

    def test_a_posting_reversed_and_reposted_under_another_symbol(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("SPNA", -10),
                        qt_dis("SPNB", 5, "SPNB CORP"))
        self.assertEqual(_got(evs), [("SPNB.TO", 5)])

    def test_control_the_survivor_larger_than_the_reversed_posting(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("D012345", 5, "SPNW WT"),
                        qt_dis("SPNW", -5, "SPNW WT"))
        self.assertEqual(_got(evs), [("SPNA.TO", 10)])

    def test_the_event_id_stays_on_the_first_symbol(self):
        # An election saved before the survivor named the event keeps it.
        evs, _ = _chain(qt_dis("D012345", 10, "SPNA CORP"),
                        qt_dis("D012345", -10, "SPNA CORP"),
                        qt_dis("SPNA", 10, "SPNA CORP"))
        self.assertEqual(evs[0].target_symbol, "SPNA.TO")
        self.assertEqual(evs[0].target_isin, "D012345.TO")

    def test_control_one_symbol_keeps_its_id_symbol(self):
        evs, _ = _chain(qt_dis("SPNA", 10))
        self.assertEqual(evs[0].target_isin, "SPNA.TO")

    def test_control_a_lone_take_back_still_nets_under_the_posting(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("SPNX", -3, "SPNX WT"))
        self.assertEqual(_got(evs), [("SPNA.TO", 7)])

    def test_control_one_symbol(self):
        evs, _ = _chain(qt_dis("SPNA", 10))
        self.assertEqual(_got(evs), [("SPNA.TO", 10)])


# ========================== TJ-1136 one event, whichever copy has the cash
def _merger(acct, n, cash=0.0, cil_cur=""):
    return CA.CorporateAction(
        date="2025-06-11", time="09:30:00", action_type="merger",
        source_symbol="ALPH.TO", source_isin="A", target_symbol="BETA.TO",
        target_isin="B", ratio_new=21.0, ratio_old=20.0, qty_disposed=n,
        qty_received=n, fmv=0.0, currency="CAD", target_currency="CAD",
        account="margin", cash_in_lieu=cash, cash_in_lieu_currency=cil_cur,
        broker_account=acct)


class TestTJ1136OneEventEitherOrder(unittest.TestCase):
    def _combined(self, *evs):
        return CA.combine_broker_copies(list(evs), stream=io.StringIO())

    def test_either_order_is_one_event(self):
        a = _merger("99900001", 12.0, 10.0, "CAD")
        b = _merger("99900002", 24.0)
        for pair in ((a, b), (b, a)):
            evs = self._combined(*pair)
            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0].qty_disposed, 36.0)
            self.assertEqual(evs[0].cash_in_lieu, 10.0)
            self.assertEqual(evs[0].cash_in_lieu_currency, "CAD")

    def test_the_emitted_rows_land_on_the_delivered_shares(self):
        # Two events each moved the pool: 36 x 1.05, less one fraction,
        # left units the broker never delivered.
        evs = self._combined(_merger("99900001", 12.0, 10.0, "CAD"),
                             _merger("99900002", 24.0))
        rows = CA._canada_merger_rollover(evs[0], "rollover_s_85_1_5", {})
        split, sale = rows
        self.assertAlmostEqual(36 * split["quantity"] + sale["quantity"], 36.0)

    def test_control_two_cash_currencies_stay_two_events(self):
        evs = self._combined(_merger("99900001", 12.0, 10.0, "CAD"),
                             _merger("99900002", 24.0, 5.0, "USD"))
        self.assertEqual(len(evs), 2)

    def test_control_same_currency_adds_up(self):
        evs = self._combined(_merger("99900001", 12.0, 10.0, "CAD"),
                             _merger("99900002", 24.0, 5.0, "CAD"))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].cash_in_lieu, 15.0)


# ================== TJ-1137 a split under a temporary code, the parent held
class TestTJ1137SplitUnderCodeCountsTowardsTheParent(unittest.TestCase):
    """The TJ-0764 book, its split under a temporary code."""

    @staticmethod
    def _row(d, a, s, q, p, v, c, desc, name="SYN PARENT CORP"):
        return rbc_row(d, a, s, name, q, v, desc, p, cur=c)

    def _rows(self, split_symbol, usd_sold=1):
        row = self._row
        return [row("2025-01-06", "Buy", "SYN", 1, 5, -5, "USD",
                    "SYN PARENT CORP BUY"),
                row("2025-01-07", "Sell", "SYN", -usd_sold, 5, 5, "USD",
                    "SYN PARENT CORP SELL"),
                row("2025-02-03", "Buy", "SYN", 10, 10, -100, "CAD",
                    "SYN PARENT CORP BUY"),
                row("2025-03-03", "Reorganization", split_symbol, 10, "", 0,
                    "CAD", "DIS - SYN PARENT CORP STK SPLIT ON 10 SHS"),
                row("2025-04-01", "Sell", "SYN", -15, 5, 75, "CAD",
                    "SYN PARENT CORP SELL"),
                row("2025-06-11", "Reorganization", "SPNA", 1, "", 0, "CAD",
                    "DIS - SPINCO CORP SPINOFF ON 5 SHS FROM SEC# K000001 "
                    "SYN PARENT CORP REC 06/02/25 PAY 06/11/25",
                    "SPINCO CORP")]

    def test_the_split_counts_towards_the_listing_it_resolves_to(self):
        evs, err = parse_one(self._rows("X000004"))
        self.assertEqual([e.source_symbol for e in evs], ["SYN.TO"], err)

    def test_two_listings_held_are_not_guessed(self):
        # Short of the split, the other listing read as the only one held.
        evs, err = parse_one(self._rows("X000004", usd_sold=0.5))
        self.assertEqual([e.source_symbol for e in evs], ["K000001"], err)
        self.assertIn("several listings", " ".join(err.split()))

    def test_control_the_split_under_the_ticker(self):
        evs, err = parse_one(self._rows("SYN"))
        self.assertEqual([e.source_symbol for e in evs], ["SYN.TO"], err)

    def test_control_a_code_of_another_company_is_not_counted(self):
        rows = self._rows("X000004")
        rows[3] = self._row("2025-03-03", "Reorganization", "X000004", 10, "",
                            0, "CAD", "DIS - OTHER CORP STK SPLIT ON 10 SHS",
                            name="OTHER CORP")
        evs, err = parse_one(rows)
        self.assertNotEqual([e.source_symbol for e in evs], ["SYN.TO"], err)


# ================== TJ-1321 s.86.1 of two broker accounts, its parent count
def _sell_parent(date, qty, net, acct):
    return _trade(date, "Sell", "ZZP", _PARENT, -qty, net, acct)


def _run_86_1(rows):
    """`tjs run`, the s.86.1 election of 40 CAD, `tjs run`: (the parent's
    sales as (date, qty, cost), the ADJUST rows' parent counts)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        p = root / "inputs" / "margin" / "qt.csv"
        p.parent.mkdir(parents=True)
        p.write_text(_HEAD + rows)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            '[accounts.margin]\ntype = "taxable"\n'
            "combined_broker_accounts = true\n")
        first = tj(root, "run", "--no-input", check=False)
        ids = set(re.findall(r"\b(\d{8}-zzp-zzs-[0-9a-f]{4})\b",
                             first.stdout + first.stderr))
        for i in ids:
            tj(root, "elect", "margin", "--set", f"{i}=rollover_s_86_1",
               "--hint", "allocated_acb_cad=40")
        tj(root, "run", "--no-input", check=False)
        doc = json.loads((root / "work/margin_gains.json").read_text())
        base = json.loads((root / "work/.blend_base.json").read_text())
    return ([(t["date"], t["qty"], round(t["cost"], 2))
             for t in doc["transactions"] if t["symbol"] == "ZZP.US"],
            [r["corp_parent_qty"] for r in base["transactions"]
             if r.get("action") == "ADJUST"])


class TestTJ1321ParentCountOfBothAccounts(unittest.TestCase):
    def test_both_accounts_sold_share_the_cost_cut(self):
        sales, counts = _run_86_1(
            _buy("2024-01-08", 100, 100, _ONE)
            + _buy("2024-01-09", 100, 100, _TWO)
            + _sell_parent("2025-06-03", 100, 150, _ONE)
            + _sell_parent("2025-06-04", 100, 150, _TWO)
            + _dis(100, 10, _ONE) + _dis(100, 10, _TWO))
        self.assertEqual(counts, [200.0])
        self.assertEqual(sales[0][2], sales[1][2])
        self.assertAlmostEqual(sales[0][2] + sales[1][2], 230.0, 1)

    def test_control_one_account_with_the_same_shares(self):
        sales, counts = _run_86_1(
            _buy("2024-01-08", 100, 100, _ONE)
            + _buy("2024-01-09", 100, 100, _ONE)
            + _sell_parent("2025-06-03", 100, 150, _ONE)
            + _sell_parent("2025-06-04", 100, 150, _ONE)
            + _dis(200, 20, _ONE))
        self.assertEqual(counts, [200.0])
        self.assertEqual(sales[0][2], sales[1][2])

    def test_control_a_trim_before_the_record_date_keeps_its_gain(self):
        # 100 sold from each account, then the holding's last 100: only
        # the shares the event was paid on (200) take the cut.
        sales, counts = _run_86_1(
            _buy("2024-01-08", 200, 200, _ONE)
            + _buy("2024-01-09", 100, 100, _TWO)
            + _sell_parent("2025-05-01", 100, 150, _ONE)
            + _sell_parent("2025-06-03", 100, 150, _ONE)
            + _sell_parent("2025-06-04", 100, 150, _TWO)
            + _dis(100, 10, _ONE) + _dis(100, 10, _TWO))
        self.assertEqual(counts, [200.0])
        by_day = {d: c for d, _q, c in sales}
        self.assertAlmostEqual(by_day["2025-05-01"]
                               - by_day["2025-06-03"], 20.0, 1)
        self.assertEqual(by_day["2025-06-03"], by_day["2025-06-04"])


# ===================================== TJ-1323 the ratio two accounts show
class TestTJ1323RatioOfBothAccounts(unittest.TestCase):
    def _texts(self, rows):
        """(the pending line, `tjs spinoffs`) of one Canadian spin-off."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            p = root / "inputs" / "margin" / "qt.csv"
            p.parent.mkdir(parents=True)
            p.write_text(_HEAD + rows)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                '[accounts.margin]\ntype = "taxable"\n'
                "combined_broker_accounts = true\n")
            first = tj(root, "run", "--no-input", check=False)
            text = first.stdout + first.stderr
            eid = re.search(r"\b(\d{8}-zzp-zzs-[0-9a-f]{4})\b", text).group(1)
            tj(root, "elect", "margin", "--set", f"{eid}=rollover_s_86_1",
               "--hint", "allocated_acb_cad=40")
            tj(root, "run", "--no-input", check=False)
            view = tj(root, "spinoffs", check=False)
        return text, view.stdout

    def test_two_accounts_read_on_the_shares_of_both(self):
        pending, view = self._texts(
            _buy("2024-01-08", 100, 100, _ONE)
            + _buy("2024-01-09", 100, 100, _TWO)
            + _dis(100, 10, _ONE) + _dis(100, 10, _TWO))
        self.assertIn("20.0-for-200.0, ratio 0.1,", pending)
        self.assertIn("0.1 new per share (20 for 200)", view)

    def test_control_one_account(self):
        pending, view = self._texts(
            _buy("2024-01-08", 200, 200, _ONE) + _dis(200, 20, _ONE))
        self.assertIn("20.0-for-200.0, ratio 0.1,", pending)
        self.assertIn("0.1 new per share (20 for 200)", view)

    def test_the_event_id_keeps_one_accounts_count(self):
        def ev(all_accounts):
            return CA.CorporateAction(
                date="2025-06-16", time="09:30:00", action_type="spinoff",
                source_symbol="ZZP.US", source_isin="X123456",
                target_symbol="ZZS.US", target_isin="ZZS.US", ratio_new=20.0,
                ratio_old=100.0, qty_disposed=0.0, qty_received=20.0, fmv=0.0,
                currency="USD", target_currency="USD", account="margin",
                parent_qty_all_accounts=all_accounts)
        self.assertEqual(ev(200.0).event_id, ev(0.0).event_id)
        self.assertEqual(ev(200.0).ratio_old, 100.0)

    def test_a_saved_summary_identifies_the_event_in_either_form(self):
        e = CA.CorporateAction(
            date="2025-06-16", time="09:30:00", action_type="spinoff",
            source_symbol="ZZP.US", source_isin="X123456",
            target_symbol="ZZS.US", target_isin="ZZS.US", ratio_new=20.0,
            ratio_old=100.0, qty_disposed=0.0, qty_received=20.0, fmv=0.0,
            currency="USD", target_currency="USD", account="margin",
            parent_qty_all_accounts=200.0)
        self.assertTrue(CA._summary_identifies(e.summary(), e))
        self.assertTrue(CA._summary_identifies(
            e.summary().replace("-for-200.0", "-for-100.0"), e))
        self.assertFalse(CA._summary_identifies(
            e.summary().replace("-for-200.0", "-for-50.0"), e))


# ================= TJ-0950 a partial exchange is said, and the way round works
_IB_PARTIAL = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n"
    "Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,"
    "Description,Quantity,Proceeds,Value,Realized P/L,Code\n"
    'Corporate Actions,Data,Stocks,CAD,2025-06-02,"2025-06-02, 10:00:00",'
    '"OLD(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2 '
    '(OLD, OLD SYNTHETIC INC, CA0000000001)",-10,0,-150,0,\n'
    'Corporate Actions,Data,Stocks,CAD,2025-06-02,"2025-06-02, 10:00:00",'
    '"OLD(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2 '
    '(NEW, NEW SYNTHETIC INC, CA0000000002)",5,0,150,0,\n')
_SALES = ("BUYSELL 2025-09-02 10:00:00 NEW.TO -5 CAD 30 150 0\n"
          "BUYSELL 2025-10-02 10:00:00 OLD.TO -10 CAD 12 120 0\n")


def _run_exchange(tt, election="rollover_s_85_1_5"):
    """The IB exchange of 10 OLD for 5 NEW with `tt` beside it, elected:
    (the run's text, the .sum, total gain, open positions)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        d = root / "inputs" / "a"
        d.mkdir(parents=True)
        (d / "ib.csv").write_text(_IB_PARTIAL)
        (d / "book.tt").write_text(tt)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            '[accounts.a]\ntype = "taxable"\n')
        first = tj(root, "run", "--no-input", check=False)
        eid = re.search(r"\b(\d{8}-old-new-[0-9a-f]{4})\b",
                        first.stdout + first.stderr).group(1)
        tj(root, "elect", "a", "--set", f"{eid}={election}")
        again = tj(root, "run", "--no-input", "--details", check=False)
        doc = json.loads((root / "work/a_gains.json").read_text())
        return (again.stdout + again.stderr,
                (root / "reports/a.sum").read_text(),
                doc["summary"]["total_gain"],
                {i["symbol"]: i["qty"] for i in doc.get("inventory") or []})


class TestTJ0950PartialExchangeIsSaid(unittest.TestCase):
    SAID = "only 10 were exchanged"

    def test_more_held_than_exchanged_warns_with_the_way_round(self):
        text, report, _gain, _held = _run_exchange(
            "BUYSELL 2025-01-06 10:00:00 OLD.TO 20 CAD 10 200 0\n" + _SALES)
        flat = " ".join(text.split())
        self.assertIn("moves all 20 shares held, but " + self.SAID, flat)
        self.assertIn("docs/limits.md", flat)
        self.assertIn("=ignore", flat)
        self.assertIn(self.SAID, " ".join(report.split()))

    def test_the_way_round_the_message_names_gives_the_right_figures(self):
        text, _sum, gain, held = _run_exchange(
            "BUYSELL 2025-01-06 10:00:00 OLD.TO 20 CAD 10 200 0\n"
            "BUYSELL 2025-06-02 10:00:00 OLD.TO -10 CAD 10 100 0\n"
            "BUYSELL 2025-06-02 10:01:00 NEW.TO 5 CAD 20 100 0\n" + _SALES,
            election="ignore")
        self.assertNotIn(self.SAID, " ".join(text.split()))
        self.assertAlmostEqual(gain, 70.0)
        self.assertEqual(held, {})

    def test_control_everything_held_is_exchanged(self):
        text, _sum, gain, held = _run_exchange(
            "BUYSELL 2025-01-06 10:00:00 OLD.TO 10 CAD 10 100 0\n"
            "BUYSELL 2025-09-02 10:00:00 NEW.TO -5 CAD 30 150 0\n")
        self.assertNotIn("exchanged", " ".join(text.split()))
        self.assertAlmostEqual(gain, 50.0)
        self.assertEqual(held, {})

    def test_control_a_position_sold_down_to_the_exchange(self):
        text, _sum, _gain, _held = _run_exchange(
            "BUYSELL 2025-01-06 10:00:00 OLD.TO 20 CAD 10 200 0\n"
            "BUYSELL 2025-03-03 10:00:00 OLD.TO -10 CAD 12 120 0\n")
        self.assertNotIn(self.SAID, " ".join(text.split()))

    def test_control_a_taxable_election_sells_only_what_was_exchanged(self):
        text, _sum, _gain, _held = _run_exchange(
            "BUYSELL 2025-01-06 10:00:00 OLD.TO 20 CAD 10 200 0\n",
            election="taxable_disposition")
        self.assertNotIn(self.SAID, " ".join(text.split()))


# ======================= TJ-1132 the exercise follows where its shares arrive
class TestTJ1132ExerciseBesideLateRows(unittest.TestCase):
    HEAD = ("BUYSELL 2025-01-02 10:00:00 SYN.WT.TO 10 CAD 1 10 0\n"
            "BUYSELL 2025-01-03 10:00:00 SYN.TO 5 CAD 4 20 0\n")
    # Another company's shares under the old ticker, after the date.
    LATE = "BUYSELL 2025-04-01 10:00:00 SYN.TO 3 CAD 2 6 0\n"
    EXERCISE = ("ASSIGN 2025-06-02 10:00:00 SYN.WT.TO -10 CAD 0 0 "
                "exercise_of=SYN.TO\n")

    def _run(self, tt):
        """(gain, {symbol: (qty, cost)}) of the book through `tjs run`."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs/margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = []\noption_grant_timing_since = 2025\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "ticker.map").write_text("")
            (root / "inputs/margin/book.tt").write_text(tt)
            tj(root, "run", "--no-input", check=False)
            doc = json.loads((root / "work/margin_gains.json").read_text())
        return (doc["summary"]["total_gain"],
                {i["symbol"]: (i["qty"], round(i["total_cost"], 2))
                 for i in doc.get("inventory") or []})

    def test_shares_on_the_new_symbol_beside_late_old_rows(self):
        gain, held = self._run(
            self.HEAD + "RENAME 2025-03-01 SYN.TO NEW.TO late=separate\n"
            + self.LATE + self.EXERCISE
            + "BUYSELL 2025-06-02 10:00:00 NEW.TO 10 CAD 5 50 0\n")
        self.assertAlmostEqual(gain, 0.0)
        self.assertEqual(held["NEW.TO"], (15.0, 80.0))
        self.assertEqual(held["SYN.TO"], (3.0, 6.0))

    def test_control_shares_on_the_old_symbol_keep_the_target(self):
        gain, held = self._run(
            self.HEAD + "RENAME 2025-03-01 SYN.TO NEW.TO late=separate\n"
            + self.LATE + self.EXERCISE
            + "BUYSELL 2025-06-02 10:00:00 SYN.TO 10 CAD 5 50 0\n")
        self.assertAlmostEqual(gain, 0.0)
        self.assertEqual(held["SYN.TO"], (13.0, 66.0))
        self.assertEqual(held["NEW.TO"], (5.0, 20.0))

    def test_control_no_late_rows(self):
        gain, held = self._run(
            self.HEAD + "RENAME 2025-03-01 SYN.TO NEW.TO\n" + self.EXERCISE
            + "BUYSELL 2025-06-02 10:00:00 NEW.TO 10 CAD 5 50 0\n")
        self.assertAlmostEqual(gain, 0.0)
        self.assertEqual(held["NEW.TO"], (15.0, 80.0))


# ========= TJ-1168, TJ-1268 a rename in another account, the rows it replaced
SPLIT_2025 = "SPLIT 2025-03-02 10:00:00 SYNO.US SYNN.US 1\n"


def _t1135_max_cost(rename_in_both):
    """TJ-1168's book, in foreign shares: the year's maximum cost."""
    margin = ("BUYSELL 2024-06-03 10:00:00 SYNO.US 10000 CAD 100 1000000 0\n"
              + (SPLIT_2025 if rename_in_both else "")
              + "BUYSELL 2025-04-10 10:00:00 SYNN.US 4000 CAD 300 1200000 0\n"
              "OPENING 2025-06-30 SYNN.US 24000 CAD 2200000\n"
              "BUYSELL 2025-07-02 10:00:00 SYNN.US -4000 CAD 200 800000 0\n")
    other = ("BUYSELL 2024-06-03 10:00:00 SYNO.US 1000 CAD 100 100000 0\n"
             + SPLIT_2025
             + "BUYSELL 2025-04-03 10:00:00 SYNN.US -1000 CAD 100 100000 0\n")
    with tempfile.TemporaryDirectory() as d:
        root = _project(Path(d) / "p", {"margin": _TAXABLE, "other": _TAXABLE},
                        {"inputs/margin/t.tt": margin,
                         "inputs/other/t.tt": other})
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
        rep = _cli(root, "t1135")
    return re.search(r"Maximum total cost in 2025: ([\d,.]+) CAD on (\S+)",
                     rep.stdout).groups(), re.findall(
        r"^SYNN\.US.*$", rep.stdout, re.M)


class TestTJ1168T1135ReadsTheMergedUnits(unittest.TestCase):
    def test_rename_in_another_account_gives_the_control_s_figures(self):
        self.assertEqual(_t1135_max_cost(False), _t1135_max_cost(True))

    def test_control_the_figure(self):
        (top, day), _rows = _t1135_max_cost(True)
        self.assertEqual((top, day), ("2,200,000.00", "2025-06-30"))  # pii-ok


_H_SPLIT = "SPLIT 2024-03-02 10:00:00 SYNO.TO SYNN.TO 1\n"
_H_OTHER = ("BUYSELL 2023-06-05 10:00:00 SYNO.TO 1 CAD 10 10 0\n" + _H_SPLIT
            + "BUYSELL 2024-04-03 10:00:00 SYNN.TO -1 CAD 10 10 0\n")
_H_SALES = ("BUYSELL 2024-12-27 10:00:00 SYNN.TO -5 CAD 20 100 0\n"
            "BUYSELL 2024-12-30 10:00:00 SYNN.TO -5 CAD 20 100 0\n")
_H_OLD = "BUYSELL 2023-06-05 10:00:00 SYNO.TO 10 CAD 10 100 0\n"


def _handoff(rename_in_both, opening, later):
    """The closed 2024 project, then 2025 with a snapshot in margin:
    `tjs handoff --json`'s doubles and opening differences."""
    accounts = {"margin": _TAXABLE, "other": _TAXABLE}
    with tempfile.TemporaryDirectory() as d:
        old = _project(Path(d) / "2024", accounts, {
            "inputs/margin/t.tt": _H_OLD + _H_SPLIT + _H_SALES,
            "inputs/other/t.tt": _H_OTHER}, year=2024)
        assert _cli(old, "run", "--no-input").returncode == 0
        assert _cli(old, "close-year", "--yes", "--force").returncode == 0
        root = _project(Path(d) / "2025", accounts, {
            "inputs/margin/t.tt": (_H_OLD
                                   + (_H_SPLIT if rename_in_both else "")
                                   + _H_SALES + opening + later),
            "inputs/other/t.tt": _H_OTHER})
        cfg = root / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            "[settings]\n",
            '[settings]\nprior_year_record = "../2024/filed/2024.json"\n', 1))
        assert _cli(root, "run", "--no-input").returncode == 0
        rep = json.loads(_cli(root, "handoff", "--json").stdout)
    return ([(x["date"], x["qty"], x["closed_date"]) for x in rep["double"]],
            [(x["symbol"], x["closed_qty"], x["opening_qty"])
             for x in rep["positions"]])


class TestTJ1268HandoffReadsTheMergedUnits(unittest.TestCase):
    OPENING = "OPENING 2024-06-30 SYNN.TO 5 CAD 50\n"
    LATER = ("BUYSELL 2024-12-31 10:00:00 SYNN.TO 10 CAD 20 200 0\n"
             "BUYSELL 2025-01-02 10:00:00 SYNN.TO -5 CAD 20 100 0\n")

    def test_a_sale_reported_twice_is_said_as_with_the_rename_in_both(self):
        got = _handoff(False, self.OPENING, self.LATER)
        self.assertEqual(got, _handoff(True, self.OPENING, self.LATER))
        self.assertEqual(got[0], [("2025-01-02", 5.0, "2024-12-30")])

    def test_opening_positions_are_the_merged_books(self):
        later = "BUYSELL 2025-01-02 10:00:00 SYNN.TO -5 CAD 20 100 0\n"
        opening = "OPENING 2024-12-31 SYNN.TO 5 CAD 50\n"
        got = _handoff(False, opening, later)
        self.assertEqual(got, _handoff(True, opening, later))


# ===================== TJ-1267, TJ-1337 (US) a sale between the dates is said
class TestTJ1267SoldBetweenTheDatesIsSaid(unittest.TestCase):
    """Lots A and B before the record date, a June lot after it, B sold
    before the pay date, ON 200 SHS: 200 is both the shares held on the
    record date and those held on the pay date, so the books cannot tell a
    due bill from B's right. The figures stay; the run says so."""

    def _run(self, *rows):
        return _gains([tx("BUYSELL", "2024-01-08", "ZZP.US", 100, 100), *rows],
                      "usa")

    def test_one_account(self):
        res, err = self._run(
            tx("BUYSELL", "2024-01-09", "ZZP.US", 100, 100),
            tx("BUYSELL", "2025-06-10", "ZZP.US", 100, 300),
            tx("BUYSELL", "2025-06-12", "ZZP.US", -100, 150),
            *_spin_rows(200))
        flat = " ".join(err.split())
        self.assertIn("ATTENTION: ZZP.US", flat)
        self.assertIn("100 held before its record date 2025-06-02 were sold",
                      flat)
        self.assertNotIn("came with a due bill", flat)
        inv = {i["symbol"]: round(i["total_cost"], 4) for i in res["inventory"]}
        # The due-bill figures, unchanged (v0.38.0's): 10% off each lot.
        self.assertEqual(inv["ZZP.US"], 360.0)

    def test_control_no_sale_stays_a_plain_due_bill(self):
        res, err = self._run(tx("BUYSELL", "2025-06-10", "ZZP.US", 100, 300),
                             *_spin_rows(200))
        flat = " ".join(err.split())
        self.assertIn("came with a due bill", flat)
        self.assertNotIn("ATTENTION: ZZP.US", flat)
        inv = {i["symbol"]: round(i["total_cost"], 4) for i in res["inventory"]}
        self.assertEqual(inv["ZZP.US"], 360.0)

    def test_control_a_sale_before_the_record_date_stays_plain(self):
        _res, err = self._run(
            tx("BUYSELL", "2024-01-09", "ZZP.US", 100, 100),
            tx("BUYSELL", "2025-05-12", "ZZP.US", -100, 150),
            tx("BUYSELL", "2025-06-10", "ZZP.US", 100, 300),
            *_spin_rows(200))
        self.assertNotIn("ATTENTION: ZZP.US", " ".join(err.split()))

    def test_two_accounts_tj1337_older_lot_sold_between(self):
        doc, out, _ = _run_355({"qt.csv": (
            _buy("2024-01-08", 100, 100) + _buy("2024-01-09", 100, 100, _TWO)
            + _buy("2025-06-05", 100, 300)
            + _trade("2025-06-11", "Sell", "ZZP", _PARENT, -100, 150, _TWO)
            + _dis(100, 10) + _dis(100, 10, _TWO) + _sell_spun(20, 200))})
        self.assertIn("were sold before", " ".join(out.split()))
        # v0.38.0's figures stay (test_fix_1029_r7g_small).
        self.assertEqual(_blocks(doc), [("LONG_TERM", 10.0, 10.0),
                                        ("SHORT_TERM", 10.0, 30.0)])
        self.assertAlmostEqual(_held(doc), 360.0)

    def test_control_two_accounts_due_bill_with_no_sale(self):
        _doc, out, _ = _run_355({"qt.csv": (
            _buy("2024-01-08", 100, 100) + _buy("2025-06-10", 100, 300, _TWO)
            + _dis(100, 10) + _dis(100, 10, _TWO) + _sell_spun(20, 200))})
        self.assertNotIn("were sold before", " ".join(out.split()))


if __name__ == "__main__":
    unittest.main()
