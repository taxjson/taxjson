"""Re-audit-2 fixes in the US engine (lib/core.USATaxRules).

All data is synthetic.
"""
import contextlib
import io
import time
import unittest

from taxjson.lib.core import TaxTransaction, USATaxRules
from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx


def _t(d, q, p, *, sym='XYZ.US', acct='T1', tm='10:00:00', action='BUYSELL',
       **kw):
    return TaxTransaction(action=action, date=d, date_settle=d, time=tm,
                          symbol=sym, quantity=q, price=p,
                          net_amount=round(abs(q * p), 6), currency='USD',
                          account=acct, **kw)


def _us(rows, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return USATaxRules().compute_gains(rows, **kw)


class TestSameSaleNoCascade(unittest.TestCase):
    """A2-0001 / A2-0017 / A2-0553: shares (or shorts) closed by the SAME
    sale (cover) are never replacements for each other's losses — no
    cascade, no fragmentation, no compounding of the tacked holding
    period."""

    @rule("US-WASH-17")
    def test_tiny_old_lot_whole_position_sold(self):
        # A2-0001: A 1 sh @100 (2024), B 100 @50 (2025-02-20), sell 101 @40.
        for a in (1.0, 0.001):
            rows = [_t('2024-06-03', a, 100), _t('2025-02-20', 100, 50),
                    _t('2025-03-03', -(100 + a), 40)]
            t0 = time.time()
            r = _us(rows)
            self.assertLess(time.time() - t0, 2.0)
            tr = r['transactions']
            self.assertEqual(len(tr), 2, a)
            self.assertEqual(r['wash_sales'], [])
            self.assertAlmostEqual(r['summary']['total_disallowed'], 0.0)
            self.assertAlmostEqual(r['summary']['total_gain'],
                                   -(a * 60 + 1000), places=4)
            self.assertTrue(all(x['term'] == 'SHORT_TERM' for x in tr))

    @rule("US-WASH-17")
    def test_buy_time_split_seed(self):
        # A2-0017: a washed fraction seeds the buy-time sub-lot; the whole
        # replacement is then sold in one sale: 2 rows, no new wash sale.
        for a in (1.0, 0.001):
            rows = [_t('2024-06-03', a, 60), _t('2025-03-03', -a, 40),
                    _t('2025-03-10', 100, 45), _t('2025-03-20', -100, 40)]
            r = _us(rows)
            w = [x for x in r['transactions'] if x['date'] == '2025-03-20']
            self.assertEqual(len(w), 2, a)
            self.assertTrue(all(x['disallowed_amount'] == 0 for x in w))
            self.assertEqual(len(r['wash_sales']), 1)
            self.assertAlmostEqual(r['summary']['total_gain'],
                                   -(a * 20 + 500), places=4)

    @rule("US-WASH-17")
    def test_one_cover_closes_two_shorts(self):
        # A2-0553 short side: one buy-to-cover of 200 closes an old short
        # and one opened 4 days earlier; neither replaces the other.
        rows = [_t('2026-01-02', -100, 50), _t('2026-03-02', -100, 45),
                _t('2026-03-06', 200, 60)]
        r = _us(rows)
        self.assertEqual(r['wash_sales'], [])
        self.assertAlmostEqual(r['summary']['total_disallowed'], 0.0)
        self.assertAlmostEqual(r['summary']['total_gain'], -2500.0)

    @rule("US-WASH-06", "US-WASH-17")
    def test_retained_shares_of_same_purchase_still_replace(self):
        # US-WASH-06 is kept: shares KEPT after the sale (bought within 30
        # days before it) are replacements. A 100 old, B 100 recent; sell
        # A and 50 of B: A's loss washes into B's 50 retained shares.
        rows = [_t('2024-06-03', 100, 100), _t('2025-02-20', 100, 50),
                _t('2025-03-03', -150, 40)]
        r = _us(rows)
        self.assertAlmostEqual(r['summary']['total_disallowed'], 3000.0,
                               places=4)
        self.assertEqual(len(r['transactions']), 2)

    @rule("US-WASH-17")
    def test_same_second_fills_of_one_order(self):
        # A full exit split into two fills at one timestamp is one
        # disposition: no wash between the fills.
        rows = [_t('2024-06-03', 100, 100), _t('2026-03-02', 100, 55),
                _t('2026-03-06', -150, 40, tm='10:31:07'),
                _t('2026-03-06', -50, 40, tm='10:31:07')]
        r = _us(rows)
        self.assertAlmostEqual(r['summary']['total_disallowed'], 0.0)
        self.assertEqual(r['wash_sales'], [])


class TestFullExitPlanningAgrees(unittest.TestCase):
    """A2-0553: the radar's US EXITABLE advice ("a full exit is fine")
    now matches the engine, and says the exit must be one order."""

    @rule("US-WASH-17", "US-PLAN-01")
    def test_engine_full_exit_one_sale_not_washed(self):
        rows = [_t('2024-06-03', 100, 100), _t('2026-03-02', 100, 55),
                _t('2026-03-06', -200, 40)]
        r = _us(rows)
        self.assertEqual(r['wash_sales'], [])
        st = [x for x in r['transactions'] if x['acquired_date']
              == '2026-03-02']
        self.assertEqual(st[0]['term'], 'SHORT_TERM')

    @rule("US-PLAN-01")
    def test_radar_us_exitable_says_one_order(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from test_fix_planning import REPO_ROOT, _row
        tax = [_row("2024-06-03", "XYZ.US", 100, 10000.0, currency="USD"),
               _row("2026-09-28", "XYZ.US", 100, 5500.0, currency="USD")]
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "margin_base.json"
            t.write_text(json.dumps({"transactions": tax}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
                 "--country", "usa", "--taxable", str(t), "--date",
                 "2026-10-01", "--all", "--json"], cwd=REPO_ROOT,
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {row["ticker"]: row for sec in json.loads(r.stdout)["sections"]
                for row in sec["rows"]}
        adv = rows["XYZ.US"]["advisory"]
        self.assertIn("EXITABLE", adv)
        self.assertIn("in one order at a loss is fine now", adv)


def _by_year(res):
    out = {}
    for g in res['transactions']:
        if g.get('raw_gain') is None:
            continue
        y = g['date'][:4]
        out[y] = round(out.get(y, 0.0) + g['gain'], 2)
    return out


class TestReplacementSubLots(unittest.TestCase):
    """A2-0054 (split units), A2-0060 (one sub-lot per matched loss),
    A2-0206 (short side split share for share)."""

    def _split_book(self, split):
        k = 2 if split else 1

        def row(d, q, p, acct):
            return TaxTransaction(action='BUYSELL', date=d, date_settle=d,
                                  time='10:00:00', symbol='XYZ.US',
                                  quantity=q, price=p, net_amount=-q * p,
                                  currency='USD', account=acct)
        rows = [row('2025-03-11', 140, 8, 'B')]
        if split:
            rows.append(TaxTransaction(
                action='SPLIT', date='2025-03-20', date_settle='2025-03-20',
                time='00:00:01', symbol='XYZ.US', quantity=2.0,
                currency='USD', account='B'))
        rows += [row('2025-04-04', 30 * k, 11 / k, 'A'),
                 row('2025-04-07', -30 * k, 8 / k, 'A'),
                 row('2025-06-23', -140 * k, 11 / k, 'B')]
        ira = [row('2025-06-30', 10 * k, 11 / k, 'IRA')]
        return _us(rows, sheltered_transactions=ira, per_account_basis=True)

    @rule("US-WASH-09", "US-CORP-01")
    def test_bump_lands_on_post_split_matched_shares(self):
        a, b = self._split_book(False), self._split_book(True)
        ta = sum(e['gain'] for e in a['transactions'])
        tb = sum(e['gain'] for e in b['transactions'])
        self.assertAlmostEqual(ta, 330.0, places=2)
        self.assertAlmostEqual(tb, 330.0, places=2)
        bsale = [e for e in b['transactions'] if e['account'] == 'B']
        self.assertAlmostEqual(bsale[0]['qty'], 60.0)
        self.assertAlmostEqual(bsale[0]['cost'], 330.0, places=2)

    @rule("US-WASH-10")
    def test_later_purchase_matched_by_two_losses_keeps_two_blocks(self):
        txs = [_t('2023-01-05', 100, 50), _t('2025-01-02', 100, 60),
               _t('2025-03-03', -100, 40), _t('2025-03-04', -100, 40),
               _t('2025-03-10', 200, 40),
               _t('2025-06-02', -100, 45), _t('2025-07-01', -100, 45)]
        r = _us(txs)
        later = [e for e in r['transactions'] if e['date'] >= '2025-06-01']
        self.assertEqual([round(e['cost'], 2) for e in later],
                         [5000.0, 6000.0])
        self.assertEqual([e['term'] for e in later],
                         ['LONG_TERM', 'SHORT_TERM'])
        self.assertEqual([round(e['gain'], 2) for e in later],
                         [-500.0, -1500.0])

    @rule("US-WASH-02", "US-WASH-05")
    def test_short_replacement_bigger_than_loss_is_split(self):
        def row(d, q, p):
            return _t(d, q, p, sym='XYZ.US', acct='margin')
        pending = [row('2025-01-02', -100, 10), row('2025-02-03', 100, 12),
                   row('2025-02-10', -300, 11), row('2025-12-15', 100, 11),
                   row('2026-01-15', 200, 11)]
        existing = [row('2025-01-02', -100, 10), row('2025-01-27', -300, 11),
                    row('2025-02-03', 100, 12), row('2025-12-15', 100, 11),
                    row('2026-01-15', 200, 11)]
        for book in (pending, existing):
            yrs = _by_year(_us(book))
            self.assertEqual(yrs, {'2025': -200.0, '2026': 0.0})


class TestSameMomentAccountOrder(unittest.TestCase):
    """A2-0200 / A2-0208: same-moment replacement lots of different
    accounts follow the merged book's order (taxjson.toml order), never
    the account label."""

    def _book(self, first, second):
        a = [_t('2025-01-06', 100, 20, acct='zeta'),
             _t('2025-03-03', -100, 18, acct='zeta'),
             _t('2025-03-10', 100, 10, acct='zeta', tm='09:30:00'),
             _t('2025-11-03', -100, 9, acct='zeta')]
        b = [_t('2025-03-10', 100, 10, acct='alpha', tm='09:30:00')]
        rows = a + b if first == 'zeta' else b + a
        return _us(rows, per_account_basis=True)

    @rule("US-DATE-13")
    def test_toml_order_not_label(self):
        z = self._book('zeta', 'alpha')
        late = [e for e in z['transactions'] if e['date'] == '2025-11-03']
        self.assertAlmostEqual(late[0]['cost'], 1200.0)
        a = self._book('alpha', 'zeta')
        late = [e for e in a['transactions'] if e['date'] == '2025-11-03']
        self.assertAlmostEqual(late[0]['cost'], 1000.0)


class TestStockDividendAfterSale(unittest.TestCase):
    """A2-0205: a stock dividend posted after the shares were sold is not
    a §1091 purchase (US-STKDIV-01); the warning names the case."""

    @rule("US-STKDIV-01")
    def test_not_a_replacement_with_nothing_held(self):
        rows = [_t('2025-01-02', 100, 100), _t('2025-03-10', -100, 80),
                TaxTransaction(action='BUYSELL', date='2025-03-20',
                               date_settle='2025-03-20', time='10:00:00',
                               symbol='XYZ.US', quantity=5, price=0.0,
                               net_amount=0.0, currency='USD',
                               account='T1', type='stock_dividend')]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = USATaxRules().compute_gains(rows)
        self.assertAlmostEqual(r['summary']['total_disallowed'], 0.0)
        self.assertAlmostEqual(r['summary']['total_gain'], -2000.0)
        self.assertIn("sold before it was paid", err.getvalue())


class TestFuturesOutside1091(unittest.TestCase):
    """A2-0053: the US engine never disallows a loss on a futures
    contract or an option on one (flag only); Canada's s.54 covers any
    property and keeps denying."""

    @rule("US-WASH-18")
    @rule_absent("US-WASH-18", country="canada")
    def test_rebought_future_flagged_not_denied(self):
        for sym, loss in (("F:CLG7.US", 10000.0), ("F:ESZ6.US", 5000.0),
                          ("F:CL261216C00070000.US", 1000.0)):
            book = [tx("BUYSELL", "2025-03-03", sym, 1, 20000),
                    tx("BUYSELL", "2025-03-10", sym, -1, 20000 - loss),
                    tx("BUYSELL", "2025-03-18", sym, 1, 20000 - loss)]
            r = gains_both(book, year=2025)
            self.assertAlmostEqual(
                r["usa"]["summary"]["total_disallowed"], 0.0, msg=sym)
            self.assertAlmostEqual(
                r["canada"]["summary"]["total_disallowed"], loss, msg=sym)
            rules = [w["rule"] for w in
                     r["usa"].get("option_replacement_warnings") or []]
            self.assertIn("futures_vs_loss", rules, sym)

    @rule("US-WASH-18")
    def test_flag_printed(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            USATaxRules().compute_gains(
                [_t("2025-03-03", 1, 200, sym="F:CLG7.US"),
                 _t("2025-03-10", -1, 100, sym="F:CLG7.US"),
                 _t("2025-03-18", 1, 100, sym="F:CLG7.US")])
        self.assertIn("[futures_vs_loss]", err.getvalue())
        self.assertIn("NOT denied", err.getvalue())


if __name__ == '__main__':
    unittest.main()
