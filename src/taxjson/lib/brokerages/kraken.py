import csv
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import BaseBrokerage


_FIAT_ASSETS = ('USD', 'CAD', 'EUR', 'GBP', 'USDC', 'USDT', 'DAI')
# Post-fold fiat currencies (USDC/USDT/DAI have become USD by the time
# a leg is compared against this). A fill or instant trade whose BOTH
# sides are in here is a currency conversion — cash moving between
# denominations — not a disposition of property. It used to emit a
# BUYSELL of a phantom `USD`/`CAD` asset that corrupted the position
# book; now it is a recognized non-event (KNOWN_ISSUES "Kraken fiat
# conversions are not modeled").
_FIAT_CURRENCIES = ('USD', 'CAD', 'EUR', 'GBP')
# USD-pegged stablecoins: a staking reward in one is worth 1.0/unit by
# definition, so the parser prices it directly instead of shipping a
# $0 row for taxjson-fill-crypto to look up.
_STABLECOINS = ('USDC', 'USDT', 'DAI')


def _normalize_asset(asset: str, fold_stable: bool = True) -> str:
    """Kraken prefixes assets with Z (fiat) and X (crypto) for historical reasons.
    Strip those and treat USD-pegged stablecoins as their USD anchor.
    `fold_stable=False` keeps the stablecoin's own name — the custody-
    evidence rows must say WHAT was sent (a USDC gift is a disposition
    of USDC the property, not a USD cash movement), even though the
    trade books fold it for pricing."""
    asset = re.sub(r'^Z(USD|CAD|EUR|GBP)$', r'\1', asset)
    # The X-prefix strip covers every classic X-prefixed Kraken asset
    # (KNOWN_ISSUES enumerated the missing ones: XLM, XMR, ZEC, XDG
    # [Dogecoin], ETC) — an unstripped `XXLM` reached
    # taxjson-fill-crypto as `XXLM-USD`, which Yahoo can't resolve.
    asset = re.sub(r'^X(XBT|ETH|LTC|XRP|XLM|XMR|ZEC|XDG|ETC|MLN|REP)$',
                   r'\1', asset)
    if asset == 'XBT':
        asset = 'BTC'
    if asset == 'XDG':
        asset = 'DOGE'      # Kraken's Dogecoin code; Yahoo wants DOGE
    # Fold USD-pegged stablecoins. DAI is included so that a `BTC/DAI`
    # row resolves consistently — otherwise DAI would be flagged as
    # fiat (via `_FIAT_ASSETS`) but pass through to the gain engine
    # as a non-USD currency that downstream FX conversion can't anchor.
    if fold_stable and asset in ('USDC', 'USDT', 'DAI'):
        asset = 'USD'
    return asset


def _split_pair(pair: str, time_raw: str = '') -> tuple:
    """(base, quote) for a Kraken trades-CSV pair.

    Modern exports use the slashed form (`XBT/USD`). Legacy exports
    concatenate (`XXBTZUSD`, `ADAUSD`, `XETHXXBT`); the old fallback
    dumped the whole string into `base` and stamped `quote='USD'`,
    which mis-denominated every legacy row. Recognize the documented
    legacy shapes and refuse loudly on anything else — a wrong quote
    silently corrupts the disposition currency."""
    if '/' in pair:
        base, quote = pair.split('/', 1)
        return base, quote
    p = pair.strip().upper()
    m = re.fullmatch(r'X([A-Z]{3,4})Z(USD|CAD|EUR|GBP)', p)
    if m:                                   # XXBTZUSD
        return m.group(1), m.group(2)
    m = re.fullmatch(r'X([A-Z]{3,4})X([A-Z]{3,4})', p)
    if m:                                   # XETHXXBT (crypto/crypto)
        return m.group(1), m.group(2)
    # Z-prefixed fiat QUOTE behind a stablecoin/fiat base (USDTZUSD,
    # ZUSDZCAD — Kraken's actual legacy names): matched BEFORE the
    # generic concatenation, whose greedy base otherwise eats the Z and
    # mints garbage symbols like 'USDTZ'. Kept to the EXPLICIT base
    # list — a permissive Z? base would wrongly split XTZUSD (Tezos)
    # into XT/USD.
    m = re.fullmatch(r'(USDT|USDC|DAI|Z(?:USD|CAD|EUR|GBP))'
                     r'Z(USD|CAD|EUR|GBP)', p)
    if m:
        base = m.group(1)
        if base.startswith('Z') and len(base) == 4:
            base = base[1:]                 # ZUSDZCAD → USD/CAD
        return base, m.group(2)
    m = re.fullmatch(r'([A-Z0-9]{2,8})(USDC|USDT|DAI|USD|CAD|EUR|GBP)', p)
    if m:                                   # ADAUSD, SOLUSDT
        return m.group(1), m.group(2)
    raise ValueError(
        f"Kraken trades row has an unrecognized pair format: {pair!r} "
        f"(time={time_raw!r}). The slashed form (XBT/USD) and the "
        f"documented legacy concatenations (XXBTZUSD, XETHXXBT, ADAUSD) "
        f"are supported; add the new shape to _split_pair rather than "
        f"letting the row parse with a guessed quote currency.")


class KrakenBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Kraken"

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        with open(path, 'r', encoding='utf-8-sig') as f:
            header_line = f.readline()
        if 'ordertxid' in header_line.lower() or 'pair' in header_line.lower():
            return self._parse_trades(path)
        if 'refid' in header_line.lower() or 'subtype' in header_line.lower():
            return self._parse_ledgers(path)
        return []

    # ---------------------------------------------------------------- trades
    # Kraken's *trades* CSV (the per-fill order log). Fiat-quoted pairs
    # emit one BUYSELL; crypto/crypto pairs emit the same two-leg
    # SELL+BUY pattern as the ledgers path's _build_instant_trade
    # (implemented 2026-08 per the KNOWN_ISSUES fix template).
    def _parse_trades(self, path: Path) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        # Zero-drop policy (base.py): rows we don't translate are
        # counted and summarized, never silently continued past.
        ignored_types: Dict[str, int] = {}
        with open(path, 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if not row:
                    continue
                type_ = (row.get('type') or '').lower()
                if type_ not in ('buy', 'sell'):
                    ignored_types[type_ or '?'] = (
                        ignored_types.get(type_ or '?', 0) + 1)
                    continue

                pair = row.get('pair') or ''
                time_raw = row.get('time') or ''
                dt = self.parse_date((time_raw.split('.')[0] if time_raw else ''), "%Y-%m-%d %H:%M:%S")
                if dt is None:
                    # Same policy as the Coinbase parser: stamping or
                    # skipping distorts holding periods / year filters
                    # invisibly; a changed Kraken timestamp format must
                    # be added to the format list, not dropped.
                    raise ValueError(
                        f"Kraken trades row has an unparseable "
                        f"timestamp: {time_raw!r} (pair={pair!r}). Add "
                        f"the format to parse_date if this is a new "
                        f"Kraken export variant.")

                price = self.clean_number(row.get('price'))
                cost = abs(self.clean_number(row.get('cost')))
                fee = abs(self.clean_number(row.get('fee')))
                vol = self.clean_number(row.get('vol'))

                base, quote = _split_pair(pair, time_raw)
                base = _normalize_asset(base)
                quote = _normalize_asset(quote)

                if base in _FIAT_CURRENCIES:
                    # USD/CAD, USDC/USD (base folds to USD), USDT/CAD:
                    # a currency conversion. Emitting it as a BUYSELL
                    # of a `USD`/`CAD` "asset" put a phantom position
                    # in the book; FX cash gains are not modeled here
                    # (KNOWN_ISSUES) — count it and move on.
                    self.count_nonevent(f"forex conversion {pair} "
                                        f"(not modeled — KNOWN_ISSUES)")
                    continue

                if quote not in _FIAT_ASSETS:
                    # Crypto-to-crypto fill: two USD-denominated legs,
                    # the mirror of the ledgers path's
                    # _build_instant_trade (CRA s. 40(1) / IRS Notice
                    # 2014-21 — the swap disposes the one asset at FMV
                    # and acquires the other at the same FMV). `vol` is
                    # the base-asset quantity, `cost` the quote-asset
                    # quantity; both legs ship price=0 so
                    # taxjson-fill-crypto backfills the FMV. The fee is
                    # quote-crypto-denominated and unconvertible here —
                    # zeroed for the same reason as the ledgers path
                    # (typically pennies; convert manually if material).
                    date_s = dt.strftime("%Y-%m-%d")
                    time_s = dt.strftime("%H:%M:%S")
                    base_leg = {
                        'action': 'BUYSELL',
                        'date': date_s, 'time': time_s,
                        'date_settle': date_s,
                        'symbol': base,
                        'quantity': self.signed_quantity(
                            abs(vol), action_is_sell=(type_ == 'sell')),
                        'currency': 'USD', 'price': 0.0,
                        'net_amount': 0.0, 'gross_amount': 0.0,
                        'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                        'description': f'Trade {pair} '
                                       f'({type_} leg of crypto-to-crypto)',
                    }
                    quote_leg = {
                        'action': 'BUYSELL',
                        'date': date_s, 'time': time_s,
                        'date_settle': date_s,
                        'symbol': quote,
                        'quantity': self.signed_quantity(
                            abs(cost), action_is_sell=(type_ == 'buy')),
                        'currency': 'USD', 'price': 0.0,
                        'net_amount': 0.0, 'gross_amount': 0.0,
                        'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                        'description': f'Trade {pair} '
                                       f'(counter leg of crypto-to-crypto)',
                    }
                    _txid = (row.get('txid') or '').strip()
                    if _txid:
                        base_leg['id'] = f'{_txid}-base'
                        quote_leg['id'] = f'{_txid}-quote'
                    transactions.extend([base_leg, quote_leg])
                    continue

                qty = self.signed_quantity(abs(vol), action_is_sell=(type_ == 'sell'))
                net = (cost + fee) if type_ == 'buy' else (cost - fee)

                tx = {
                    'action': 'BUYSELL',
                    'date': dt.strftime("%Y-%m-%d"),
                    'time': dt.strftime("%H:%M:%S"),
                    'date_settle': dt.strftime("%Y-%m-%d"),
                    'symbol': base, 'quantity': qty, 'currency': quote,
                    'price': price, 'net_amount': net,
                    'gross_amount': cost, 'fee': fee,
                    'account': self.DEFAULT_ACCOUNT,
                }
                # Preserve Kraken's per-fill txid as the transaction id. Split
                # fills land in the same second with identical qty/price/cost,
                # so without a unique id the sort-stage dedup collapses them
                # into one and the inventory loses real fills (e.g. 8 BNB
                # fills on 2025-10-17 within ~1.5s).
                txid = (row.get('txid') or '').strip()
                if txid:
                    tx['id'] = txid
                transactions.append(tx)
        if ignored_types:
            detail = ', '.join(f"{k} x{v}"
                               for k, v in sorted(ignored_types.items()))
            print(f"note: Kraken trades {path.name}: ignored "
                  f"{sum(ignored_types.values())} row(s) with unhandled "
                  f"type(s): {detail}.", file=sys.stderr)
        self.emit_skip_summary(path.name)
        return transactions

    # --------------------------------------------------------------- ledgers
    def _parse_ledgers(self, path: Path) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        instant_trades: Dict[str, Dict[str, Any]] = {}
        # type -> count of ledger rows we don't translate (deposit,
        # withdrawal, transfer, staking, margin, ...). Summarized once
        # at end of parse — previously silently dropped.
        ignored_types: Dict[str, int] = {}

        with open(path, 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if not row:
                    continue
                txid = (row.get('txid') or '').strip()
                refid = row.get('refid') or ''
                type_raw = (row.get('type') or '').lower()
                subtype = (row.get('subtype') or '').lower()
                asset_raw = row.get('asset') or ''
                time_raw = row.get('time') or ''
                dt = self.parse_date((time_raw.split('.')[0] if time_raw else ''), "%Y-%m-%d %H:%M:%S")
                if dt is None:
                    # Mirror the trades path / Coinbase policy: never
                    # silently drop a dated ledger row.
                    raise ValueError(
                        f"Kraken ledger row has an unparseable "
                        f"timestamp: {time_raw!r} "
                        f"(type={type_raw!r}, asset={asset_raw!r}). Add "
                        f"the format to parse_date if this is a new "
                        f"Kraken export variant.")
                date = dt.strftime("%Y-%m-%d")
                time = dt.strftime("%H:%M:%S")
                amount = self.clean_number(row.get('amount'))
                fee = self.clean_number(row.get('fee'))
                asset = _normalize_asset(asset_raw)

                if type_raw == 'earn' and subtype == 'reward':
                    # Stablecoin rewards keep their own name: folded to
                    # `USD` they reached taxjson-fill-crypto as the one
                    # symbol it refuses to price, so a USDC reward
                    # booked $0 income. _build_staking_reward prices a
                    # stablecoin at 1.0/unit itself (net = qty).
                    transactions.extend(self._build_staking_reward(
                        _normalize_asset(asset_raw, fold_stable=False),
                        date, time, abs(amount), abs(fee), txid))
                elif ((type_raw in ('withdrawal', 'deposit')
                       # Peer-to-peer transfers (Kraken "send to a
                       # Kraken user") leave custody exactly like a
                       # withdrawal: same evidence row, same send NOTE.
                       or (type_raw == 'transfer'
                           and subtype == 'transferpeertopeer'))
                      and _normalize_asset(asset_raw, fold_stable=False)
                      not in _FIAT_CURRENCIES):
                    # True FIAT movements (bank funding/cash-outs)
                    # fall through to the ignored-types note below —
                    # moving your own cash is neither custody evidence
                    # of property nor a disposition, and the FMV note
                    # would be wrong tax advice for it (round-six
                    # audit finding 4). Stablecoins are PROPERTY and
                    # stay in the evidence branch.
                    # Custody EVIDENCE, not tax events: emitted as
                    # TRANSFER rows so the standard machinery keeps
                    # them queryable (`taxjson transfers crypto` via
                    # the sidecar) instead of silently dropping them.
                    # An off-platform SEND that left your ownership
                    # (gift/payment) is a taxable disposition at FMV —
                    # the parse-time note downstream says so; the
                    # ledger carries no fiat value, so price/net stay
                    # 0 (declare the disposition as a .tt BUYSELL).
                    # Kraken Earn shuffles keep their own type string
                    # (e.g. 'hybridearnwithdrawal' does NOT land here
                    # — internal moves, position unchanged).
                    tx = {
                        'action': 'TRANSFER',
                        'date': date, 'time': time,
                        'date_settle': date,
                        'symbol': _normalize_asset(asset_raw,
                                                   fold_stable=False),
                        'quantity': amount,
                        'currency': 'USD', 'price': 0.0,
                        'net_amount': 0.0, 'fee': abs(fee),
                        'account': self.DEFAULT_ACCOUNT,
                        'description': (f"{type_raw}/{subtype}" if subtype
                                        else type_raw),
                    }
                    if txid:
                        tx['id'] = f'{txid}-xfer'
                    transactions.append(tx)
                    self.note_row_consumed()
                elif type_raw in ('spend', 'receive') and refid:
                    _legs = instant_trades.setdefault(refid, {})
                    if type_raw in _legs and _legs[type_raw]['asset'] == asset:
                        # Split settlement: a refid can carry two rows of
                        # the same leg — assignment silently discarded
                        # the earlier amount.
                        _legs[type_raw]['amount'] += abs(amount)
                        _legs[type_raw]['fee'] += abs(fee)
                    else:
                        if type_raw in _legs:
                            # Same leg type, DIFFERENT asset under one
                            # refid: the leg model can only carry one
                            # asset per side, so the earlier amount is
                            # replaced — say so loudly (zero-drop
                            # policy) instead of silently understating
                            # the basis/proceeds.
                            _old = _legs[type_raw]
                            print(f"warning: Kraken ledger refid "
                                  f"{refid!r}: {type_raw} leg reported "
                                  f"in TWO assets ({_old['asset']} "
                                  f"{_old['amount']:g} then {asset} "
                                  f"{abs(amount):g}) — keeping the "
                                  f"latter; hand-check this instant "
                                  f"trade's basis.", file=sys.stderr)
                        _legs[type_raw] = {
                            'date': date, 'time': time, 'asset': asset,
                            'amount': abs(amount), 'fee': abs(fee),
                        }
                else:
                    ignored_types[type_raw or '?'] = ignored_types.get(type_raw or '?', 0) + 1

        # Ledger `trade` rows are NOT parsed here (the trades export is
        # the authoritative per-fill record) — the generic "transfers
        # don't affect gains" wording was actively misleading for a
        # ledgers-only user whose actual trades were being dropped.
        trade_rows = ignored_types.pop('trade', 0)
        if trade_rows:
            print(
                f"note: Kraken ledger {path.name}: {trade_rows} trade "
                f"row(s) ignored — supply the trades export "
                f"(trades.csv); the ledger's trade rows are not parsed.",
                file=sys.stderr,
            )
        if ignored_types:
            detail = ', '.join(f"{k} x{v}" for k, v in sorted(ignored_types.items()))
            print(
                f"note: Kraken ledger {path.name}: ignored {sum(ignored_types.values())} "
                f"row(s) with unhandled type(s): {detail}. Deposits/withdrawals/"
                f"transfers don't affect gains directly, but if a deposit "
                f"established a position, its cost basis must come from the "
                f"source platform's export.",
                file=sys.stderr,
            )
        for refid, trade in instant_trades.items():
            transactions.extend(self._build_instant_trade(trade, refid))
        self.emit_skip_summary(path.name)
        return transactions

    def _build_staking_reward(self, asset, date, time, qty, fee, txid=''):
        # Carry the reward qty on the DIVIDEND record so fill_crypto_prices
        # computes income as qty*FMV. Without it the prices-filler defaults
        # qty to 1.0 and a $4k ETH reward of 0.001 ETH ends up as $4k income.
        div = {
            'action': 'DIVIDEND',
            'date': date, 'time': time, 'date_settle': date,
            'symbol': asset, 'quantity': qty, 'currency': 'USD',
            'net_amount': 0.0, 'gross_amount': 0.0,
            'type': 'dividend', 'account': self.DEFAULT_ACCOUNT,
            'description': 'Staking Reward',
        }
        buy = {
            'action': 'BUYSELL',
            'date': date, 'time': time, 'date_settle': date,
            'symbol': asset, 'quantity': qty, 'currency': 'USD',
            'price': 0.0, 'net_amount': 0.0, 'fee': fee,
            'account': self.DEFAULT_ACCOUNT,
        }
        if txid:
            # Suffix distinguishes the paired emissions so the sort-stage
            # dedup keeps both.
            div['id'] = f'{txid}-div'
            buy['id'] = f'{txid}-buy'
        if asset in _STABLECOINS:
            # Worth 1.0/unit by definition: income = qty, priced here.
            # NO acquisition leg: the trade books fold USDC/USDT/DAI to
            # USD (the coin is later SPENT as a fiat quote, never sold
            # as an asset), so a USDC position would sit in the book
            # forever as a phantom long that nothing ever closes.
            div['price'] = 1.0
            div['net_amount'] = qty
            div['gross_amount'] = qty
            return [div]
        return [div, buy]

    def _build_instant_trade(self, trade, refid=''):
        """Build the BUYSELL transaction(s) for one Kraken instant trade.

        Returns a list — typically one tx for a fiat-paired swap, two
        for a crypto-to-crypto swap (both legs are taxable events per
        CRA s. 40(1) / IRS Notice 2014-21: the spent crypto is disposed
        at FMV and the received crypto is acquired at the same FMV).
        Returns [] if either leg is missing — WARNED loudly, never
        silently (a lone `spend` is a real taxable disposition the user
        must know was skipped; a lone `receive` is an acquisition whose
        basis would otherwise vanish).
        """
        if 'spend' not in trade or 'receive' not in trade:
            for leg_type, leg in trade.items():
                missing = 'receive' if leg_type == 'spend' else 'spend'
                print(f"warning: Kraken ledger refid {refid!r}: orphan "
                      f"{leg_type} row ({leg['asset']} "
                      f"{leg['amount']:g} on {leg['date']}) has no "
                      f"matching {missing} leg — row SKIPPED. A lone "
                      f"spend is a real taxable disposition (and a lone "
                      f"receive an acquisition with basis); this event "
                      f"is NOT in the output — find the missing "
                      f"counter-leg (truncated export?) or enter the "
                      f"trade manually via a .tt file.",
                      file=sys.stderr)
                self.count_skip(f"orphan {leg_type} (refid {refid})")
            return []
        spend = trade['spend']
        recv = trade['receive']
        total_fee = spend['fee'] + recv['fee']

        if (spend['asset'] in _FIAT_CURRENCIES
                and recv['asset'] in _FIAT_CURRENCIES):
            # Fiat-for-fiat (USDC dust swept to USD, USD -> CAD): a
            # currency conversion, not a disposition of property. The
            # fiat-spend branch below would have booked a BUYSELL of a
            # phantom `CAD`/`USD` asset. Counted, not modeled
            # (KNOWN_ISSUES "Kraken fiat conversions are not modeled").
            self.count_nonevent(
                f"forex conversion {spend['asset']}->{recv['asset']} "
                f"(instant trade / dust sweep, not modeled — "
                f"KNOWN_ISSUES)")
            return []

        if spend['asset'] in _FIAT_ASSETS:
            is_buy = True
            quote_asset, quote_amt = spend['asset'], spend['amount']
            base_asset, base_amt = recv['asset'], recv['amount']
        elif recv['asset'] in _FIAT_ASSETS:
            is_buy = False
            quote_asset, quote_amt = recv['asset'], recv['amount']
            base_asset, base_amt = spend['asset'], spend['amount']
        else:
            # Crypto-to-crypto: emit TWO transactions — a SELL of the
            # spent asset and a BUY of the received asset — both priced
            # in USD. Per CRA s. 40(1) and IRS Notice 2014-21, the swap
            # is a taxable disposition of the spent crypto at FMV; the
            # received crypto is acquired at the same FMV. The legacy
            # behaviour squashed both legs into one BUYSELL of the
            # received asset denominated in the spent crypto, silently
            # dropping the spent-leg gain. Each leg's USD FMV is filled
            # later by taxjson-fill-crypto (it backfills any BUYSELL
            # with price=0 and a non-fiat symbol).
            # Fee handling: Kraken's ledger `fee` is in the row's asset's
            # units (e.g. a few thousandths of an ETH on an ETH→BTC swap),
            # while the emitted legs are USD-denominated. The parser has
            # no FMV at this stage (taxjson-fill-crypto fills it later),
            # so we can't faithfully convert the crypto-denominated fee
            # to USD here. We zero it out rather than stamp a wrong-
            # denominated value onto the gain entry — Kraken instant-
            # trade fees are typically pennies, so the dropped deduction
            # is negligible. Convert manually if material.
            sell_leg = {
                'action': 'BUYSELL',
                'date': spend['date'], 'time': spend['time'],
                'date_settle': spend['date'],
                'symbol': spend['asset'],
                'quantity': self.signed_quantity(spend['amount'], action_is_sell=True),
                'currency': 'USD', 'price': 0.0,
                'net_amount': 0.0, 'gross_amount': 0.0,
                'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                'description': 'Instant Trade (sell leg of crypto-to-crypto swap)',
            }
            buy_leg = {
                'action': 'BUYSELL',
                'date': recv['date'], 'time': recv['time'],
                'date_settle': recv['date'],
                'symbol': recv['asset'],
                'quantity': self.signed_quantity(recv['amount'], action_is_sell=False),
                'currency': 'USD', 'price': 0.0,
                'net_amount': 0.0, 'gross_amount': 0.0,
                'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                'description': 'Instant Trade (buy leg of crypto-to-crypto swap)',
            }
            if refid:
                sell_leg['id'] = f'{refid}-sell'
                buy_leg['id'] = f'{refid}-buy'
            return [sell_leg, buy_leg]

        price = (round(quote_amt / base_amt, 8)
                 if base_amt > 0 else 0.0)
        qty = self.signed_quantity(base_amt, action_is_sell=not is_buy)
        # Engine convention (core.py: "net_amount ... includes the
        # fee"): Kraken debits the ledger fee IN ADDITION to the
        # amount, so an instant BUY's true cash out is quote_amt + fee
        # and an instant SELL's true proceeds quote_amt − fee — same as
        # the trades path. Booking bare quote_amt understated basis /
        # overstated proceeds by the ~1.5% instant-trade fee.
        # FIAT-side fee only: the crypto leg's fee is denominated in
        # the crypto asset and cannot be summed into a fiat net
        # (re-audit — same rationale as the crypto/crypto path's
        # zeroing; crypto-leg fees are typically 0/pennies).
        fiat_fee = spend['fee'] if is_buy else recv['fee']
        net = quote_amt + fiat_fee if is_buy else quote_amt - fiat_fee
        gross = quote_amt

        tx = {
            'action': 'BUYSELL',
            'date': spend['date'], 'time': spend['time'], 'date_settle': spend['date'],
            'symbol': base_asset, 'quantity': qty, 'currency': quote_asset,
            'price': price, 'net_amount': net, 'gross_amount': gross,
            'fee': fiat_fee, 'account': self.DEFAULT_ACCOUNT,
            'description': 'Instant Trade',
        }
        if refid:
            tx['id'] = refid
        return [tx]
