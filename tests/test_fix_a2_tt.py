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
                'time': '09:30:00', 'symbol': 'XYZ250620C00050000.US',
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


if __name__ == '__main__':
    unittest.main()
