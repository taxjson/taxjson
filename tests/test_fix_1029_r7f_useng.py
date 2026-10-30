"""Regression tests for fix round 7 (wave F), group useng: the US engine.

TJ-1290 a Questrade §355 of two broker accounts in one folder: the shares
        the broker paid on are the sum of each account's "ON n SHS".
TJ-1310, TJ-1311, TJ-1312 the reading of a charge dated after a cover as
        paid on that short (TJ-1257) moved right figures; it is backed out,
        so those rows are as booked in v0.38.0. TJ-1257 is open again.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import tx
from test_fix_1029_r7d_us import _covers, _gains, _paid, _pil, _project, _tjs

from _qa_project import tj


def _short(date, cover, *, sym="ZQA.US", qty=100, proceeds=1000.0, cost=900.0):
    return [tx("BUYSELL", date, sym, -qty, proceeds),
            tx("BUYSELL", cover, sym, qty, cost)]


def _as_booked(test, res, total):
    """The charge stays paid compensation: no §263(h) cost on a cover."""
    test.assertAlmostEqual(res["summary"]["total_gain"], total)
    rows = [t for t in res["transactions"]
            if t.get("action") == "DIVIDEND_IN_LIEU"]
    for r in rows:
        test.assertNotIn("capitalized_263h", r)
    test.assertNotIn("read as paid on that short", res["_stderr"])
    return rows


def _estimate(rows):
    with tempfile.TemporaryDirectory() as td:
        root = _project(td, rows, year=2025,
                        more_toml="\n[estimate]\nother_income = 100000\n")
        _tjs(root, "run", "--no-input")
        est = json.loads(_tjs(root, "estimate", "--json").stdout)["estimate"]
        doc = json.loads((root / "work/margin_gains.json").read_text())
    return est, doc


SHORT_OPENED = "BUYSELL 2025-05-01 10:00:00 ZQA.US -100 USD 10 1000 0"
COVERED = "BUYSELL 2025-06-12 10:00:00 ZQA.US 100 USD 9 900 0"
CHARGE = "DIVIDEND_IN_LIEU 2025-06-16 09:30:00 ZQA.US 0 USD 0 -20.00"


# --------------------------------------------------------------- TJ-1310
class TestLongAgainAfterTheCover(unittest.TestCase):
    """Shares that come back after the cover by another way than a
    separate buy: the three books agree."""

    def _same_as_a_separate_buy(self, rows):
        est, doc = _estimate(rows)
        self.assertAlmostEqual(doc["summary"]["total_gain"], 100.0)
        pil = [t for t in doc["transactions"]
               if t.get("action") == "DIVIDEND_IN_LIEU"][0]
        self.assertNotIn("capitalized_263h", pil)
        self.assertAlmostEqual(pil["pil"], -20.0)
        self.assertEqual(est["estimated_tax"], 22.0)

    def test_a_separate_buy(self):
        self._same_as_a_separate_buy([
            SHORT_OPENED, COVERED,
            "BUYSELL 2025-06-13 10:00:00 ZQA.US 100 USD 10 1000 0",
            CHARGE])

    def test_a_buy_that_covers_and_goes_long(self):
        self._same_as_a_separate_buy([
            SHORT_OPENED,
            "BUYSELL 2025-06-12 10:00:00 ZQA.US 200 USD 9 1800 0",
            CHARGE])

    def test_an_assignment(self):
        self._same_as_a_separate_buy([
            SHORT_OPENED,
            "BUYSELL 2025-06-01 10:00:00 ZQA250620P00010000.US -1 USD 0.5 50 0",
            COVERED,
            "ASSIGN 2025-06-13 16:00:00 ZQA250620P00010000.US 1 USD 0 0 0",
            "ASSIGN 2025-06-13 16:00:01 ZQA.US 100 USD 10 1000 0",
            CHARGE])


# --------------------------------------------------------------- TJ-1311
class TestShortOpenedAndCoveredTheSameDay(unittest.TestCase):
    def test_it_takes_no_charge(self):
        res = _gains([*_short("2025-06-12", "2025-06-12", sym="ZQB.US"),
                      _paid("2025-06-16", "ZQB.US", -20.0)])
        rows = _as_booked(self, res, 100.0)
        self.assertAlmostEqual(rows[0]["pil"], -20.0)

    def test_beside_a_60_day_short_it_takes_none_of_it(self):
        res = _gains([*_short("2025-04-01", "2025-06-10"),
                      *_short("2025-06-12", "2025-06-12", qty=900,
                              proceeds=9000.0, cost=8100.0),
                      _paid("2025-06-16", "ZQA.US", -20.0)])
        rows = _as_booked(self, res, 1000.0)
        self.assertAlmostEqual(rows[0]["pil"], -20.0)
        self.assertAlmostEqual(sum(c["gain"] for c in _covers(res)), 1000.0)

    def test_beside_a_receipt_it_stays_a_reversal(self):
        res = _gains([tx("BUYSELL", "2025-06-01", "ZQA.US", 100, 1000.0),
                      _paid("2025-06-05", "ZQA.US", 30.0),
                      tx("BUYSELL", "2025-06-10", "ZQA.US", -100, 1100.0),
                      *_short("2025-06-12", "2025-06-12"),
                      _paid("2025-06-16", "ZQA.US", -20.0)])
        rows = _as_booked(self, res, 200.0)
        self.assertAlmostEqual(sum(r["pil"] for r in rows), 10.0)


# --------------------------------------------------------------- TJ-1312
class TestChargeTheBrokerCancels(unittest.TestCase):
    ROWS = [SHORT_OPENED, COVERED, CHARGE,
            "DIVIDEND_IN_LIEU 2025-06-18 09:30:00 ZQA.US 0 USD 0 20.00"]

    def test_it_moves_no_cost(self):
        res = _gains([*_short("2025-05-01", "2025-06-12"),
                      _paid("2025-06-16", "ZQA.US", -20.0),
                      _paid("2025-06-18", "ZQA.US", 20.0)])
        rows = _as_booked(self, res, 100.0)
        self.assertAlmostEqual(sum(r["pil"] for r in rows), 0.0)

    def test_the_tax_with_a_capital_loss_beside_it(self):
        est, _ = _estimate([
            *self.ROWS,
            "BUYSELL 2025-01-02 10:00:00 ZQB.US 100 USD 100 10000 0",
            "BUYSELL 2025-02-03 10:00:00 ZQB.US -100 USD 50 5000 0"])
        self.assertEqual(est["estimated_tax"], -660.0)


# --------------------------------------------------------------- TJ-1257
class TestChargeAfterTheCoverIsAsBooked(unittest.TestCase):
    """Backed out: a charge dated after the cover is what v0.38.0 made of
    it. TJ-1257, open: such a charge on a short held over the ex-date."""

    def test_short_covered_within_45_days(self):
        res = _gains([*_short("2025-05-01", "2025-06-12"),
                      _paid("2025-06-16", "ZQA.US", -20.0)])
        rows = _as_booked(self, res, 100.0)
        self.assertAlmostEqual(rows[0]["pil"], -20.0)
        self.assertNotIn("paid_on_short", rows[0])

    @rule("US-INC-04")
    def test_a_short_open_on_the_pay_date_still_capitalizes(self):
        res = _gains([*_short("2025-05-01", "2025-06-12"),
                      _paid("2025-06-10", "ZQA.US", -20.0)])
        self.assertAlmostEqual(res["summary"]["total_gain"], 80.0)
        self.assertAlmostEqual(_pil(res)["capitalized_263h"], 20.0)
        self.assertTrue(_pil(res)["paid_on_short"])


# --------------------------------------------------------------- TJ-1290
_HEAD = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
         'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
         'Activity Type,Account #,Account Type\n')
_PARENT = 'ZZP PARENT CORP'
_ONE, _TWO = '99900001', '99900002'


def _trade(date, action, sym, desc, qty, net, acct=_ONE):
    return (f'{date},{date},{action},{sym},{desc},{qty},'
            f'{abs(net / qty):.4f},{net},0,{net},USD,Trades,{acct},Margin\n')


def _dis(on, qty, acct=_ONE):
    return (f'2025-06-16,2025-06-16,DIS,ZZS,ZZS SPINCO INC SPINOFF FROM SEC# '
            f'X123456 {_PARENT} REC 06/02/25 PAY 06/16/25 ON {on} SHS,{qty},'
            f'0,0,0,0,USD,Other,{acct},Margin\n')


def _buy(date, qty, net, acct=_ONE):
    return _trade(date, 'Buy', 'ZZP', _PARENT, qty, -net, acct)


def _sell_spun(qty, net, acct=_ONE):
    return _trade('2025-07-03', 'Sell', 'ZZS', 'ZZS SPINCO', -qty, net, acct)


def _run_355(files, alloc=40):
    """`tjs run`, the election, `tjs run`: (gains doc, output, event ids)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'p'
        for name, rows in files.items():
            p = root / 'inputs' / 'margin' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_HEAD + rows)
        (root / 'taxjson.toml').write_text(
            '[settings]\nyear = 2025\ncountry = "usa"\n'
            'base_currency = "USD"\nsource_currencies = ["USD"]\n'
            'combined_broker_accounts = true\n'
            '[accounts.margin]\ntype = "taxable"\n')
        first = tj(root, 'run', '--no-input', check=False)
        ids = sorted(set(re.findall(r'\b(\d{8}-zzp-zzs-[0-9a-f]{4})\b',
                                    first.stdout + first.stderr)))
        for i in ids:
            tj(root, 'elect', 'margin', '--set', f'{i}=tax_free_355', '--hint',
               f'allocated_acb={alloc}')
        again = tj(root, 'run', '--no-input', '--details', check=False)
        doc = json.loads((root / 'work/margin_gains.json').read_text())
    return doc, again.stdout + again.stderr, ids


def _blocks(doc, sym='ZZS.US'):
    return sorted((t['term'], round(t['qty'], 4), round(t['cost'], 4))
                  for t in doc['transactions'] if t['symbol'] == sym)


def _held(doc, sym='ZZP.US'):
    return next(round(i['total_cost'], 4) for i in doc['inventory']
                if i['symbol'] == sym)


class TestQuestrade355TwoBrokerAccounts(unittest.TestCase):
    DATED = (_buy('2024-01-08', 100, 100) + _buy('2024-01-09', 100, 100, _TWO)
             + _buy('2025-06-10', 100, 300)
             + _dis(100, 10) + _dis(100, 10, _TWO) + _sell_spun(20, 200))
    DUE_BILL = (_buy('2024-01-08', 100, 100)
                + _buy('2025-06-10', 100, 300, _TWO)
                + _dis(100, 10) + _dis(100, 10, _TWO)
                + _trade('2025-07-02', 'Sell', 'ZZP', _PARENT, -100, 200)
                + _sell_spun(20, 200))

    def _due_bill_figures(self, doc, out):
        self.assertEqual(_blocks(doc), [('LONG_TERM', 10.0, 10.0),
                                        ('SHORT_TERM', 10.0, 30.0)])
        sale = [t for t in doc['transactions'] if t['symbol'] == 'ZZP.US']
        self.assertAlmostEqual(sale[0]['cost'], 90.0)
        self.assertAlmostEqual(_held(doc), 270.0)
        self.assertNotIn('ATTENTION: ZZP', out)

    @rule("US-CORP-07")
    def test_the_lots_before_the_record_date_in_two_accounts(self):
        doc, out, _ = _run_355({'qt.csv': self.DATED})
        self.assertEqual(_blocks(doc), [('LONG_TERM', 10.0, 20.0),
                                        ('LONG_TERM', 10.0, 20.0)])
        self.assertAlmostEqual(_held(doc), 460.0)
        self.assertNotIn('ATTENTION: ZZP', out)

    @rule("US-CORP-07")
    def test_a_due_bill_across_two_accounts(self):
        doc, out, _ = _run_355({'qt.csv': self.DUE_BILL})
        self._due_bill_figures(doc, out)

    def test_one_export_per_account(self):
        lines = self.DUE_BILL.splitlines(keepends=True)
        doc, out, ids = _run_355({
            'a.csv': ''.join(x for x in lines if _ONE in x),
            'b.csv': ''.join(x for x in lines if _TWO in x)})
        self.assertEqual(len(ids), 1)
        self._due_bill_figures(doc, out)

    def test_the_event_id_keeps_a_saved_election(self):
        """The id hashes the ratio, so the sum stays out of it: an election
        saved before the fix still finds its event."""
        from taxjson.lib.corp_actions import CorporateAction
        ids = _run_355({'qt.csv': self.DUE_BILL})[2]
        ev = CorporateAction(
            date='2025-06-16', time='09:30:00', action_type='spinoff',
            source_symbol='ZZP.US', source_isin='X123456',
            target_symbol='ZZS.US', target_isin='ZZS.US',
            ratio_new=20.0, ratio_old=100.0, qty_disposed=0.0,
            qty_received=20.0, fmv=0.0, currency='USD',
            target_currency='USD', account='margin')
        self.assertEqual(ids, [ev.event_id])

    def test_a_reposted_chain_counts_its_account_once(self):
        """A posting, its reversal and the repost repeat ON 100 SHS in one
        account: paid on 100, and the later lot is not entitled."""
        doc, _out, _ = _run_355({'qt.csv': (
            _buy('2024-01-08', 100, 100) + _buy('2025-06-10', 100, 300)
            + _dis(100, 10) + _dis(100, -10) + _dis(100, 10)
            + _sell_spun(10, 100))}, alloc=10)
        self.assertEqual(_blocks(doc), [('LONG_TERM', 10.0, 10.0)])
        self.assertAlmostEqual(_held(doc), 390.0)

    def test_an_account_whose_posting_was_taken_back_is_not_counted(self):
        doc, _out, _ = _run_355({'qt.csv': (
            _buy('2024-01-08', 100, 100) + _buy('2025-06-10', 100, 300)
            + _dis(100, 10) + _dis(100, 10, _TWO) + _dis(100, -10, _TWO)
            + _sell_spun(10, 100))}, alloc=10)
        self.assertEqual(_blocks(doc), [('LONG_TERM', 10.0, 10.0)])
        self.assertAlmostEqual(_held(doc), 390.0)

    def test_one_account_is_as_before(self):
        doc, out, _ = _run_355({'qt.csv': (
            _buy('2024-01-08', 100, 100) + _buy('2025-06-10', 100, 300)
            + _dis(200, 20)
            + _trade('2025-07-02', 'Sell', 'ZZP', _PARENT, -100, 200)
            + _sell_spun(20, 200))})
        self._due_bill_figures(doc, out)

    def test_two_counts_are_two_events_and_said(self):
        """ON 100 and ON 50 are two chains: each event's count is its own
        account's, which squares with no lot of the folder, so it is said
        and every lot shares it (docs/troubleshooting.md)."""
        _doc, out, ids = _run_355({'qt.csv': (
            _buy('2024-01-08', 100, 100) + _buy('2024-01-09', 50, 50, _TWO)
            + _buy('2025-06-10', 50, 150)
            + _dis(100, 10) + _dis(50, 5, _TWO))}, alloc=10)
        self.assertEqual(len(ids), 2)
        self.assertIn('was paid on', re.sub(r'\s+', ' ', out))


if __name__ == '__main__':
    unittest.main()
