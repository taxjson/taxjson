"""Crypto reward source evidence survives pricing/conversion; synthetic data."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from decimal import Decimal

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.brokerages.schema import validate_transactions
from taxjson.lib.core import TaxTransaction, coerce_transaction_row
from taxjson.bin import fill_crypto_prices as filler
from taxjson.bin.taxjson_convert_currency import convert_transaction
from taxjson.bin import taxjson_convert_currency as converter


CB_HEADER = ('ID,Timestamp,Transaction Type,Asset,Quantity Transacted,'
             'Price Currency,Price at Transaction,Subtotal,'
             'Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n')
KR_HEADER = ('txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,'
             'balance,amountusd,feeusd,feecurrency\n')


def parse(parser, name, content):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / name
        path.write_text(content)
        with patch.dict(os.environ, {'TAXJSON_LOCAL_TZ': 'America/Toronto'}), \
                contextlib.redirect_stderr(io.StringIO()):
            return parser.parse_file(path)


def coinbase(kind='Staking Income', fee='$20.00', header=CB_HEADER):
    return parse(CoinbaseBrokerage(), 'cb_rewards.csv', header +
                 f'c1,2025-07-16 08:21:37 UTC,{kind},ZZC,0.2,CAD,'
                 f'$200.00,$40.00,$60.00,{fee},\n')


def kraken(amount='0.2', fee='0.05', gross='30', feeusd='7.5',
           feeasset='QZC', kind='earn', subtype='reward', asset='QZC'):
    return parse(KrakenBrokerage(), 'kr_ledgers_rewards.csv', KR_HEADER +
                 f'L1,E1,2025-07-16 08:21:37,{kind},{subtype},currency,'
                 f'{asset},earn,{amount},{fee},0.15,{gross},{feeusd},{feeasset}\n')


def income(rows):
    return next(r for r in rows if r['action'] == 'DIVIDEND')


class TestCoinbaseRewardEvidence(unittest.TestCase):
    def test_commission_value_and_net_basis_remain_distinct(self):
        rows = coinbase()
        div = income(rows)
        ev = div['staking_reward']
        self.assertEqual((ev['gross_value'], ev['fee_value'], ev['net_value']),
                         (60, 20, 40))
        self.assertEqual(ev['net_quantity'], .2)
        self.assertIsNone(ev['gross_quantity'])
        self.assertIsNone(ev['fee_quantity'])
        self.assertEqual(ev['fee_currency'], 'CAD')
        self.assertEqual(ev['commission_method'], 'withheld_from_reward')
        self.assertTrue(ev['commission_known'])
        self.assertEqual(ev['source_file'], 'cb_rewards.csv')
        self.assertEqual(ev['source_line'], 2)
        self.assertEqual(ev['source_id'], 'c1')
        self.assertEqual(ev['source_timestamp'], '2025-07-16 08:21:37 UTC')
        self.assertEqual(div['time'], '04:21:37')
        self.assertEqual([r['net_amount'] for r in rows], [40, 40])
        self.assertNotIn('staking_reward', rows[1])

    def test_blank_or_missing_fee_is_unknown_not_zero(self):
        for content in (
                CB_HEADER + 'c1,2025-07-16 08:21:37 UTC,Staking Income,ZZC,.2,CAD,200,40,40,,\n',
                CB_HEADER.replace('Fees and/or Spread,', '') +
                'c1,2025-07-16 08:21:37 UTC,Staking Income,ZZC,.2,CAD,200,40,40,\n'):
            ev = income(parse(CoinbaseBrokerage(), 'cb_rewards.csv', content))['staking_reward']
            self.assertFalse(ev['commission_known'])
            self.assertIsNone(ev['fee_value'])
            self.assertEqual(ev['commission_method'], 'unknown')
        ev = income(coinbase(fee='0'))['staking_reward']
        self.assertTrue(ev['commission_known'])
        self.assertEqual(ev['fee_value'], 0)

    def test_other_rewards_are_not_relabelled_staking(self):
        for kind in ('Learning Reward', 'Incentives Rewards Payout', 'Rewards Income'):
            ev = income(coinbase(kind=kind))['staking_reward']
            self.assertEqual(ev['kind'], 'other_reward')
            self.assertEqual(ev['source_type'], kind)
        self.assertEqual(income(coinbase())['staking_reward']['kind'], 'staking')


class TestKrakenRewardEvidence(unittest.TestCase):
    def test_same_coin_fee_keeps_units_and_known_values(self):
        rows = kraken()
        ev = income(rows)['staking_reward']
        self.assertEqual((ev['gross_quantity'], ev['fee_quantity'], ev['net_quantity']),
                         (.2, .05, .15))
        self.assertEqual((ev['gross_value'], ev['fee_value'], ev['net_value']),
                         (30, 7.5, 22.5))
        self.assertEqual(ev['fee_currency'], 'QZC')
        self.assertEqual(ev['valuation_currency'], 'USD')
        self.assertEqual(ev['source_type'], 'earn/reward')
        self.assertEqual(ev['commission_method'], 'withheld_from_reward')
        self.assertTrue(ev['commission_known'])
        self.assertNotIn('staking_reward', rows[1])
        self.assertEqual([r['quantity'] for r in rows], [.15, .15])
        self.assertEqual([r['net_amount'] for r in rows], [22.5, 22.5])

    def test_old_unpriced_export_keeps_unknown_money(self):
        rows = kraken(gross='', feeusd='')
        ev = income(rows)['staking_reward']
        self.assertEqual(ev['fee_quantity'], .05)
        self.assertTrue(ev['commission_known'])
        self.assertIsNone(ev['gross_value'])
        self.assertIsNone(ev['fee_value'])
        self.assertIsNone(ev['net_value'])
        self.assertNotIn('valuation', ev)
        self.assertEqual([r['net_amount'] for r in rows], [0, 0])

    def test_selected_price_is_booked_net_price_not_gross_source_ratio(self):
        div = income(kraken(feeusd='7.49'))
        ev = div['staking_reward']
        self.assertEqual(ev['price'], 150)
        self.assertEqual(ev['gross_value'], 30)
        self.assertEqual(ev['fee_value'], 7.49)
        self.assertEqual(ev['valuation']['price'], div['price'])
        self.assertEqual(ev['valuation']['price'], 150.06666667)

    def test_fiat_selected_price_keeps_its_own_currency(self):
        div = income(kraken(asset='CAD', feeasset='CAD', kind='dividend',
                            subtype='', gross='.15', feeusd='.0375'))
        ev = div['staking_reward']
        self.assertEqual(ev['valuation_currency'], 'USD')
        self.assertAlmostEqual(ev['price'], .75)
        self.assertEqual((ev['valuation']['price'], ev['valuation']['currency']),
                         (1, 'CAD'))
        self.assertEqual(ev['valuation']['source'], 'fiat unit value')

    def test_other_coin_fee_does_not_reduce_reward_units_or_basis(self):
        rows = kraken(amount='10', fee='.001', gross='50', feeusd='3', feeasset='ETH')
        div = income(rows)
        ev = div['staking_reward']
        self.assertEqual(ev['commission_method'], 'paid_in_other_asset')
        self.assertEqual(ev['fee_currency'], 'ETH')
        self.assertEqual(ev['fee_quantity'], .001)
        self.assertEqual(ev['net_quantity'], 10)
        self.assertEqual(ev['net_value'], 47)
        reward_buy = next(r for r in rows if r['action'] == 'BUYSELL' and r['symbol'] == 'QZC')
        self.assertEqual((div['net_amount'], reward_buy['net_amount']), (47, 50))
        fee_sale = next(r for r in rows if r['symbol'] == 'ETH')
        self.assertEqual((fee_sale['quantity'], fee_sale['net_amount']), (-.001, 3))
        self.assertEqual(sum('staking_reward' in r for r in rows), 1)

    def test_explicit_zero_and_blank_fee_are_distinct(self):
        zero = income(kraken(fee='0', feeusd='0'))['staking_reward']
        missing = income(kraken(fee='', feeusd=''))['staking_reward']
        self.assertTrue(zero['commission_known'])
        self.assertEqual(zero['fee_value'], 0)
        self.assertFalse(missing['commission_known'])
        self.assertIsNone(missing['fee_quantity'])
        self.assertIsNone(missing['fee_value'])

    def test_cash_reward_kind_and_par_assumption_are_visible(self):
        ev = income(kraken(asset='USDC', feeasset='USDC', kind='dividend',
                           subtype='', gross='', feeusd=''))['staking_reward']
        self.assertEqual(ev['kind'], 'other_reward')
        self.assertEqual(ev['valuation']['source'], 'stablecoin par assumption')


class TestRewardEvidencePipeline(unittest.TestCase):
    def test_core_roundtrip_keeps_optional_evidence_without_changing_id(self):
        row = income(coinbase())
        tx = coerce_transaction_row(row, 0, 'test')
        self.assertEqual(tx.to_dict()['staking_reward'], row['staking_reward'])
        without = dict(row); without.pop('staking_reward')
        without.pop('id'); row.pop('id')
        self.assertEqual(TaxTransaction(**without).id, TaxTransaction(**row).id)
        self.assertNotIn('staking_reward', TaxTransaction(**without).to_dict())
        duplicate = TaxTransaction(**tx.to_dict())
        duplicate.staking_reward['valuation']['price'] = 123
        self.assertEqual(tx.staking_reward['valuation']['price'], 200)

    def test_malformed_evidence_is_refused_without_silent_fee_zero(self):
        for change in ({'commission_known': 'false'}, {'fee_value': float('nan')},
                       {'schema_version': True}, {'fee_quantity': -1}):
            row = income(coinbase())
            row['staking_reward'].update(change)
            with self.assertRaisesRegex(ValueError, 'staking_reward'):
                coerce_transaction_row(row, 0, 'test')
            self.assertTrue(validate_transactions([row])[0])
        row = income(coinbase()); row['action'] = 'BUYSELL'
        with self.assertRaisesRegex(ValueError, 'income leg only'):
            coerce_transaction_row(row, 0, 'test')

    def test_pricing_and_fx_keep_source_facts_and_capture_actual_quote(self):
        rows = kraken(gross='', feeusd='')
        original = copy.deepcopy(income(rows)['staking_reward'])
        output = io.StringIO()
        with patch.object(filler, 'load_cache', return_value={'QZC-2025-07-16': 150}), \
                patch.object(filler, 'yahoo_id', side_effect=lambda s: s), \
                patch.object(filler, 'ids_seen_in_cache', return_value=[]), \
                patch('sys.stdin', io.StringIO(json.dumps({'transactions': rows}))), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            filler._fill(SimpleNamespace(input=None))
        filled = income(json.loads(output.getvalue())['transactions'])
        ev = filled['staking_reward']
        self.assertEqual({k: v for k, v in ev.items() if k != 'valuation'}, original)
        self.assertEqual(ev['valuation']['source'], 'Yahoo daily close')
        self.assertEqual(ev['valuation']['market_symbol'], 'QZC')
        self.assertEqual(filled['net_amount'], 22.5)
        tx = coerce_transaction_row(filled, 0, 'test')
        before = copy.deepcopy(tx.to_dict())
        converted = convert_transaction(tx, 'CAD', {'USD': {'2025-07-16': Decimal('1.4')}}, None)
        self.assertEqual(tx.to_dict(), before)
        self.assertEqual(converted.net_amount, 31.5)
        valuation = converted.staking_reward['valuation']
        self.assertEqual((valuation['price'], valuation['currency'], valuation['fx_rate'],
                          valuation['target_currency']), (150, 'USD', 1.4, 'CAD'))
        self.assertEqual({k: v for k, v in converted.staking_reward.items() if k != 'valuation'}, original)
        repeat = convert_transaction(converted, 'CAD', {}, None)
        self.assertEqual(repeat.staking_reward, converted.staking_reward)

    def test_fx_evidence_names_selected_weekend_rate_without_second_lookup(self):
        tx = coerce_transaction_row(income(kraken()), 0, 'test')
        tx.date = tx.date_settle = '2025-07-19'  # Saturday
        history = {'USD': {'2025-07-18': Decimal('1.4')}}
        converter.reset_fallback_tally()
        with patch.object(converter, 'get_rate_for_date',
                          wraps=converter.get_rate_for_date) as lookup:
            converted = convert_transaction(tx, 'CAD', history, None)
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(converted.net_amount, 31.5)
        valuation = converted.staking_reward['valuation']
        self.assertEqual(valuation['fx_date'], '2025-07-18')
        self.assertEqual(valuation['fx_source'], 'history prior date')
        self.assertFalse(converter._DEFAULT_RATE_FALLBACKS)

    def test_default_and_identity_rates_have_no_invented_quote_date(self):
        tx = coerce_transaction_row(income(kraken()), 0, 'test')
        converter.reset_fallback_tally()
        converted = convert_transaction(tx, 'CAD', {}, Decimal('1.4'))
        valuation = converted.staking_reward['valuation']
        self.assertEqual(converted.net_amount, 31.5)
        self.assertEqual(valuation['fx_source'], 'explicit default')
        self.assertEqual(valuation['fx_date'], '')
        self.assertEqual(sum(converter._DEFAULT_RATE_FALLBACKS.values()), 1)
        self.assertEqual(len(converter._FALLBACK_ROWS), 1)
        unchanged = convert_transaction(tx, 'USD', {}, None)
        identity = unchanged.staking_reward['valuation']
        self.assertEqual(identity['fx_rate'], 1)
        self.assertEqual(identity['fx_source'], 'identity')
        self.assertEqual(identity['fx_date'], '')
        converter.reset_fallback_tally()


if __name__ == '__main__':
    unittest.main()
