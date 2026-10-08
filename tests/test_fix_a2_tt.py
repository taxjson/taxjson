"""Re-audit-2 fixes: the .tt converter, the schema validator, Canadian
venue suffixes, bare rename targets and partial trade cancellations
(fix list parsers-common-03 + A2-0635). Synthetic data only."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_convert_tt import (parse_tt_line, tt_to_json,
                                            tx_to_tt_line)
from tax_rules import rule
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _parse(line):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        tx = parse_tt_line(line, account_name="m", source="h.tt:1")
    return tx, err.getvalue()


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


class TestNegativeSellTotal(unittest.TestCase):
    """A2-0292, A2-0620, A2-0621, A2-0622, A2-0623, A2-1073, A2-1226,
    A2-1227: a sale whose commission exceeds its gross nets negative;
    the writers emit that signed total and the reader books it."""

    PENNY = {'action': 'BUYSELL', 'date': '2025-06-16',
             'time': '09:30:00', 'symbol': 'ZZZ250620C00050000.US',
             'quantity': -1.0, 'currency': 'USD', 'price': 0.01,
             'net_amount': -8.95, 'fee': 9.95}

    def test_json_tt_json_round_trip_keeps_negative_proceeds(self):
        line = tx_to_tt_line(dict(self.PENNY))
        back, err = _parse(line)
        self.assertAlmostEqual(back['net_amount'], -8.95, places=5)
        self.assertAlmostEqual(back['fee'], 9.95, places=5)
        self.assertEqual(err, "")

    def test_hand_typed_negative_total_with_its_fee_is_read(self):
        tx, err = _parse("BUYSELL 2025-06-16 09:30:00 "
                         "ZZZ250620C00050000.US -1 USD 0.01 -8.95 9.95")
        self.assertAlmostEqual(tx['net_amount'], -8.95)
        self.assertEqual(err, "")

    def test_cash_signed_negative_total_still_refused(self):
        # R1-117: the fee does not explain the sign -> a typed '-'.
        with self.assertRaises(ValueError):
            _parse("BUYSELL 2025-01-05 09:31:00 ABC.US -100 USD 50.00 "
                   "-4995.00 5.00")

    def test_negative_total_that_does_not_match_is_refused(self):
        with self.assertRaises(ValueError):
            _parse("BUYSELL 2025-06-16 09:30:00 ZZZ250620C00050000.US "
                   "-1 USD 0.01 -50.00 9.95")

    def test_zero_total_now_warns_about_the_lost_commission(self):
        # The old README advice (enter 0) left the excess commission out
        # of the loss.
        _, err = _parse("BUYSELL 2025-06-16 09:30:00 "
                        "ZZZ250620C00050000.US -1 USD 0.01 0 9.95")
        self.assertIn("-8.95", err)
        self.assertIn("out of the loss", err)

    def test_futures_negative_total_needs_its_size(self):
        with self.assertRaises(ValueError) as cm:
            _parse("BUYSELL 2025-06-16 09:30:00 F:CLN5.US -1 USD 0.001 "
                   "-4.00 5.00")
        self.assertIn("x1000", str(cm.exception))
        tx, _ = _parse("BUYSELL 2025-06-16 09:30:00 F:CLN5.US -1 USD "
                       "0.001 -4.00 5.00 x1000")
        self.assertAlmostEqual(tx['net_amount'], -4.0)

    def test_events_view_line_reimports(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        line = _tx_display_line(dict(self.PENNY))
        back, _ = _parse(line)
        self.assertAlmostEqual(back['net_amount'], -8.95)

    def test_events_view_carries_contract_size(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        fut = {'action': 'BUYSELL', 'date': '2025-04-01',
               'time': '09:30:00', 'symbol': 'F:CLK5.US', 'quantity': 1,
               'currency': 'USD', 'price': 60.0, 'net_amount': 2.5,
               'fee': 2.5, 'multiplier': 1000}
        line = _tx_display_line(fut)
        self.assertTrue(line.endswith(" x1000"), line)
        mini = {'action': 'BUYSELL', 'date': '2025-04-01',
                'time': '09:30:00', 'symbol': 'ABC250718C00045000.US',
                'quantity': 1, 'currency': 'USD', 'price': 1.0,
                'net_amount': 11.0, 'fee': 1.0, 'multiplier': 10}
        self.assertTrue(_tx_display_line(mini).endswith(" x10"))
        back, _ = _parse(_tx_display_line(mini))
        self.assertEqual(back['multiplier'], 10.0)


class TestRoundTripFacts(unittest.TestCase):
    """A2-0291, A2-0631: income facts survive json -> tt -> json;
    A2-1072: quantity/price precision; A2-1086: a cut last line."""

    def test_pil_dealer_and_issuer_country_round_trip(self):
        tx = {'action': 'DIVIDEND_IN_LIEU', 'date': '2025-03-31',
              'time': '09:30:00', 'symbol': 'XEI.TO', 'quantity': 100,
              'currency': 'CAD', 'price': 0.25, 'net_amount': 25.0,
              'gross_amount': 25.0, 'dealer_country': 'CA',
              'issuer_country': 'CA'}
        line = tx_to_tt_line(tx)
        self.assertIn("dealer=CA", line)
        back, err = _parse(line)
        self.assertEqual(back['dealer_country'], 'CA')
        self.assertEqual(back['issuer_country'], 'CA')
        self.assertEqual(err, "")

    def test_record_date_label_and_roc_type_round_trip(self):
        div = {'action': 'DIVIDEND', 'date': '2026-01-05',
               'time': '00:00:00', 'symbol': 'XEI.TO', 'quantity': 1000,
               'currency': 'CAD', 'price': 0.1, 'net_amount': 100.0,
               'record_date': '2025-12-30',
               'income_label': 'distribution'}
        back, _ = _parse(tx_to_tt_line(div))
        self.assertEqual(back['record_date'], '2025-12-30')
        self.assertEqual(back['income_label'], 'distribution')
        roc = {'action': 'ADJUST', 'date': '2026-01-05',
               'time': '09:30:00', 'symbol': 'XEI.TO', 'currency': 'CAD',
               'net_amount': -50.0, 'type': 'roc',
               'record_date': '2025-12-30'}
        back, _ = _parse(tx_to_tt_line(roc))
        self.assertEqual(back['type'], 'roc')
        self.assertEqual(back['record_date'], '2025-12-30')
        self.assertAlmostEqual(back['net_amount'], -50.0)

    def test_facts_do_not_change_the_id(self):
        a, _ = _parse("DIVIDEND 2026-01-05 09:30:00 XEI.TO 1000 CAD 0.1 "
                      "100.00")
        b, _ = _parse("DIVIDEND 2026-01-05 09:30:00 XEI.TO 1000 CAD 0.1 "
                      "100.00 record=2025-12-30")
        self.assertEqual(a['id'], b['id'])

    def test_bad_fact_tokens_refused(self):
        for tail in ("record=2025-13-40", "dealer=Canada", "type=roc",
                     "foo=1"):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                _parse("DIVIDEND 2026-01-05 09:30:00 XEI.TO 1000 CAD 0.1 "
                       f"100.00 {tail}")
        with self.assertRaises(ValueError):
            _parse("BUYSELL 2025-01-05 09:31:00 ABC.US 100 USD 50.00 "
                   "5005.00 5.00 record=2025-01-01")

    def test_ten_decimal_quantity_round_trips(self):
        tx = {'action': 'BUYSELL', 'date': '2025-03-01',
              'time': '09:30:00', 'symbol': 'ETH', 'quantity': 0.0123456789,
              'currency': 'CAD', 'price': 2000.123456789,
              'net_amount': 24.69, 'fee': 0.0}
        back, _ = _parse(tx_to_tt_line(tx))
        self.assertEqual(back['quantity'], 0.0123456789)
        self.assertEqual(back['price'], 2000.123456789)
        dust = dict(tx, quantity=1.2345e-06, price=50000.0, net_amount=0.06)
        back, _ = _parse(tx_to_tt_line(dust))
        self.assertEqual(back['quantity'], 1.2345e-06)
        # An exact 8-decimal value keeps the familiar spelling.
        self.assertIn(" 1.00000000 ", tx_to_tt_line(
            dict(tx, quantity=1.0, price=10.0, net_amount=10.0)))

    def test_last_line_without_line_end_warns(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.tt"
            p.write_text("BUYSELL 2025-01-05 09:31:00 SHOP.TO 30 CAD 95.00 "
                         "2854.95 4", encoding="utf-8")
            _, err = _quiet(tt_to_json, p, "m")
            self.assertIn("no line end", err)
            p.write_text("BUYSELL 2025-01-05 09:31:00 SHOP.TO 30 CAD 95.00 "
                         "2854.95 4.95\n", encoding="utf-8")
            _, err = _quiet(tt_to_json, p, "m")
            self.assertNotIn("no line end", err)


class TestCanadianVenues(unittest.TestCase):
    """A2-0300, A2-0635, A2-1077: a .tt line spells a Canadian listing
    as the broker parsers do, and every venue set knows .VN."""

    @rule("CA-ACB-04")
    @rule("US-BASIS-06")
    def test_tt_canonicalizes_canadian_listings(self):
        for raw, want in (("ABC.V", "ABC.TO"), ("ABC.VN", "ABC.TO"),
                          ("XYZ.CN", "XYZ.TO"), ("QQ.NE", "QQ.TO"),
                          ("FTN.PRA.TO", "FTN.PR.A.TO"),
                          ("ABC.TO", "ABC.TO")):
            with self.subTest(raw=raw):
                tx, err = _parse(f"BUYSELL 2025-03-03 09:30:00 {raw} -100 "
                                 f"CAD 4.00 396.00 4.00")
                self.assertEqual(tx['symbol'], want)
                self.assertNotIn("not a known market suffix", err)
        div, _ = _parse("DIVIDEND 2025-03-31 09:30:00 FTN.PRA.TO 100 CAD "
                        "0.1 10.00")
        self.assertEqual(div['symbol'], 'FTN.PR.A.TO')
        # .V outside CAD may be a class letter: kept.
        tx, _ = _parse("BUYSELL 2025-03-03 09:30:00 BRK.V 1 USD 4.00 "
                       "4.00 0")
        self.assertEqual(tx['symbol'], 'BRK.V')

    def test_tt_and_parser_rows_share_one_id_shape(self):
        a, _ = _parse("BUYSELL 2025-03-03 09:30:00 ABC.VN 100 CAD 4.00 "
                      "404.00 4.00")
        b, _ = _parse("BUYSELL 2025-03-03 09:30:00 ABC.TO 100 CAD 4.00 "
                      "404.00 4.00")
        self.assertEqual(a['id'], b['id'])

    def test_venue_sets_know_vn(self):
        from taxjson.lib.brokerages.schema import (KNOWN_SUFFIXES,
                                                   validate_transactions)
        self.assertIn('VN', KNOWN_SUFFIXES)
        _, warns = validate_transactions([{
            'action': 'BUYSELL', 'date': '2025-01-02', 'symbol': 'QZV.VN',
            'quantity': 1, 'currency': 'CAD', 'price': 1.0,
            'net_amount': 1.0}])
        self.assertFalse([w for w in warns if 'suffix' in w], warns)
        from taxjson.bin.taxjson_t1135 import classify_country
        self.assertIsNone(classify_country('QZV.VN', {}))

    def test_lint_flags_vn_and_undotted_preferred_splits(self):
        from taxjson.bin.taxjson_lint_crosslistings import venue_splits
        rows = [{'symbol': 'ABC.TO'}, {'symbol': 'ABC.VN'},
                {'symbol': 'FTN.PRA.TO'}, {'symbol': 'FTN.PR.A.TO'}]
        roots = {f['root'] for f in venue_splits(rows, [])}
        self.assertEqual(roots, {'ABC', 'FTN.PR.A'})


class TestSchemaFutures(unittest.TestCase):
    """A2-0302, A2-1082, A2-1087, A2-1089, A2-1088."""

    def _v(self, **kw):
        from taxjson.lib.brokerages.schema import validate_transactions
        tx = dict(action='BUYSELL', date='2025-04-01', currency='USD',
                  quantity=1, net_amount=10.0, price=10.0)
        tx.update(kw)
        return validate_transactions([tx])

    def test_every_futures_prefix_may_have_a_negative_price(self):
        for sym in ('F:CLK0.US', '/CLK0.US', '\\CLK0.US'):
            with self.subTest(sym=sym):
                errs, _ = self._v(symbol=sym, price=-5.0, net_amount=2.5,
                                  multiplier=1000, quantity=-1)
                self.assertFalse([e for e in errs if 'negative price' in e])
        from taxjson.bin.taxjson_validate import validate_transactions as V
        flat = str(V([{'action': 'BUYSELL', 'date': '2020-04-20',
                       'symbol': '/CLK0.US', 'quantity': 1, 'price': -5.0,
                       'net_amount': 2.5, 'currency': 'USD'}]))
        self.assertNotIn('Price is negative', flat)

    def test_negative_price_futures_buy_may_carry_signed_net(self):
        errs, _ = self._v(symbol='F:CLK0.US', price=-5.0,
                          net_amount=-4997.5, multiplier=1000)
        self.assertEqual(errs, [])
        # A share buy with a negative net is still wrong.
        errs, _ = self._v(symbol='XYZ.US', net_amount=-10.0)
        self.assertTrue([e for e in errs if '>= 0' in e])

    def test_undeclared_futures_size_is_not_guessed(self):
        for sym in ('F:CLK5.US', '/CLK5.US'):
            errs, warns = self._v(symbol=sym, price=20.0,
                                  net_amount=20002.37)
            self.assertEqual((errs, warns), ([], []), sym)
        # Declared, the check still runs (and is an error).
        errs, _ = self._v(symbol='F:CLK5.US', price=20.0,
                          net_amount=2000.0, multiplier=1000)
        self.assertTrue(errs)

    def test_option_settling_after_expiry_warns(self):
        _, warns = self._v(symbol='XYZ251231C00050000.US', date='2025-12-31',
                           date_settle='2026-01-02', price=0.0,
                           net_amount=0.0)
        self.assertTrue([w for w in warns if 'after the expiry day' in w])
        # A trade on the expiry day settles T+1 (CA-DATE-04): not flagged.
        _, warns = self._v(symbol='XYZ251231C00050000.US', date='2025-12-31',
                           date_settle='2026-01-02', price=0.5,
                           net_amount=49.0, quantity=-1)
        self.assertFalse([w for w in warns if 'expiry' in w])
        _, warns = self._v(symbol='XYZ251231C00050000.US', date='2025-12-30',
                           date_settle='2025-12-31', price=0.0,
                           net_amount=0.0)
        self.assertFalse([w for w in warns if 'expiry' in w])

    @rule("CA-FX-04")
    @rule("US-FUT-01")
    def test_negative_price_buy_books_a_gain_in_both_countries(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.futures import method_for, settle_futures

        def fill(date, qty, price, net):
            return TaxTransaction(action='BUYSELL', date=date,
                                  date_settle=date, symbol='F:CLK0.US',
                                  quantity=qty, price=price,
                                  net_amount=net, currency='USD',
                                  fee=2.5, account='m')
        for country in ('canada', 'usa'):
            with self.subTest(country=country):
                rows, _ = settle_futures(
                    [fill('2020-04-20', 1, -5.0, -4997.5),
                     fill('2020-04-22', -1, 1.0, 997.5)],
                    method_for(country))
                pl = sum(r.net_amount for r in rows if r.quantity < 0)
                # Paid -4,997.50 (received cash) to open, received
                # 997.50 to close: +5,995.00, not a 4,000 loss.
                self.assertAlmostEqual(pl, 5995.0, places=2)


class TestBareRenameTargets(unittest.TestCase):
    """A2-0304: a ticker.map GLOBAL or a security override that renames
    a listed symbol to a bare one is an ATTENTION line."""

    def test_rule_detection(self):
        from taxjson.bin.taxjson_ticker_map import (bare_rename_target,
                                                    bare_target_warnings)
        self.assertTrue(bare_rename_target('RY.TO', 'RY'))
        self.assertFalse(bare_rename_target('RY.TO', 'RY.US'))
        self.assertFalse(bare_rename_target('ETH2', 'ETH'))   # crypto
        self.assertFalse(bare_rename_target(
            'ABC250718C00045000.US', 'ABC'))
        self.assertEqual(len(bare_target_warnings(['RY.TO'],
                                                  {'RY.TO': 'RY'})), 1)
        self.assertEqual(bare_target_warnings(['AB.TO'],
                                              {'RY.TO': 'RY'}), [])

    def test_merge2_prints_attention(self):
        import subprocess
        import sys
        import os
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "b.json"
            book.write_text(json.dumps({"transactions": [{
                "action": "DIVIDEND", "date": "2025-03-01",
                "symbol": "RY.TO", "currency": "CAD",
                "net_amount": 148.0, "quantity": 100}]}))
            m = Path(d) / "ticker.map"
            m.write_text("GLOBAL RY.TO RY\n")
            env = dict(os.environ, PYTHONPATH=str(
                Path(__file__).resolve().parents[1] / "src"))
            r = subprocess.run([sys.executable, "-m",
                                "taxjson.bin.taxjson_merge2", str(book),
                                "--map", str(m)],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("warning: ATTENTION: ticker.map: RY.TO -> RY",
                          r.stderr)

    def test_security_override_to_bare_symbol_warns(self):
        import subprocess
        import sys
        import os
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "qt.csv"
            csv.write_text(
                'Transaction Date,Settlement Date,Action,Symbol,'
                'Description,Quantity,Price,Gross Amount,Commission,'
                'Net Amount,Currency,Account #,Activity Type,Account Type\n'
                '2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,RY,'
                'ROYAL BANK OF CANADA,10,150.00,-1500.00,0,-1500.00,CAD,'
                '55500001,Trades,Individual\n')  # pii-ok
            ovr = Path(d) / "ticker.map"
            ovr.write_text("EXTRACT ROYAL BANK | CAD | RY\n")
            env = dict(os.environ, PYTHONPATH=str(
                Path(__file__).resolve().parents[1] / "src"))
            r = subprocess.run([sys.executable, "-m",
                                "taxjson.bin.taxjson_brokerage",
                                "--brokerage", "questrade",
                                "--security-overrides", str(ovr), str(csv)],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("warning: ATTENTION: a ticker.map EXTRACT line",
                          r.stderr)


class TestPartialTradeCancel(unittest.TestCase):
    """A2-0298: a Ca naming one execution of a multi-fill Order cancels
    that part of the aggregated Order row."""

    HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n'
            'Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
            'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')

    @staticmethod
    def _tr(when, qty, price, comm, code, disc='Order'):
        proceeds = round(-qty * price, 6)
        return (f'Trades,Data,{disc},Stocks,USD,U5550001,QZK,"{when}",'  # pii-ok
                f'{qty},{price},{price},{proceeds},{comm},0,0,0,{code}\n')

    def _parse_ib(self, body):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ib.csv"
            p.write_text(self.HEAD + body, encoding="utf-8")
            return _quiet(IbBrokerage().parse_file, p)

    def test_execution_cancel_reduces_the_order(self):
        w = '2025-03-03, 10:00:00'
        body = (self._tr(w, 440, 10, -2.2, 'O')
                + self._tr(w, 400, 10, -2.0, 'O', 'Trade')
                + self._tr(w, 40, 10, -0.2, 'O', 'Trade')
                + self._tr(w, -40, 10, 0.2, 'Ca')
                + self._tr(w, -40, 10, 0.2, 'Ca', 'Trade')
                + self._tr(w, 40, 10.05, -0.2, 'O')
                + self._tr(w, 40, 10.05, -0.2, 'O', 'Trade'))
        txs, err = self._parse_ib(body)
        trades = sorted((t['quantity'], t['price']) for t in txs
                        if t['action'] == 'BUYSELL')
        self.assertEqual(trades, [(40, 10.05), (400, 10)])
        order = next(t for t in txs if t['quantity'] == 400)
        self.assertAlmostEqual(order['net_amount'], 4002.0, places=6)
        self.assertIn("one execution", err)

    def test_merge2_reduces_an_order_from_another_statement(self):
        from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE
        base = dict(action='BUYSELL', date='2025-03-03', time='10:00:00',
                    symbol='QZK.US', currency='USD', price=10.0,
                    account='m')
        order = TaxTransaction(**base, quantity=500, net_amount=5002.5,
                               fee=2.5)
        ca = TaxTransaction(**base, quantity=-50, net_amount=500.25,
                            fee=-0.25, type=TRADE_CANCEL_TYPE)
        kept, err = _quiet(cancel_trade_pairs, [order, ca])
        self.assertEqual([(t.quantity, round(t.net_amount, 6))
                          for t in kept], [(450, 4502.25)])
        self.assertNotIn("none of this account's inputs", err)

    def test_two_candidate_orders_stay_unmatched(self):
        from taxjson.lib.trade_cancel import (TRADE_CANCEL_TYPE,
                                              pair_cancellations)
        base = dict(action='BUYSELL', date='2025-03-03', time='10:00:00',
                    symbol='QZK.US', currency='USD', price=10.0,
                    account='m')
        rows = [dict(base, quantity=440, net_amount=4400.0),
                dict(base, quantity=300, net_amount=3000.0),
                dict(base, quantity=-40, net_amount=400.0,
                     type=TRADE_CANCEL_TYPE)]
        kept, pairs, unmatched = pair_cancellations(rows)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(len(kept), 3)


if __name__ == '__main__':
    unittest.main()
