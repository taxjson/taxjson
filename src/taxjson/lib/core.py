import json
import re
import hashlib
import sys
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timedelta

from taxjson.lib.corporate_timeline import SplitTimeline, event_sort_key
from decimal import Decimal

from taxjson.lib.numeric import D

class SplitStraddlesSettlementError(ValueError):
    """A RENAME-split dated strictly between a trade's execution and
    its settlement: re-denominating the in-flight quantity would also
    need re-symboling mid-trade, so the engine refuses and the user
    re-dates the row's settlement (or the split). PLAIN splits inside
    a settle lag no longer refuse — the executed quantity/price are
    re-denominated through the ratio (money untouched) and booked
    correctly against the post-split pool."""


class AmbiguousTransferDateError(ValueError):
    """A row rewritten from a TRANSFER sits inside a superficial-loss /
    wash-sale trigger window. Its date is the broker ARRIVAL date —
    for a custody move that is not an acquisition date, and using it
    as one could wrongly deny (or wrongly allow) a return-relevant
    loss. The engine refuses to guess; the user declares what the row
    is. (Balance/still-held usage is unaffected: shares held are held
    regardless of how they arrived.)"""


@dataclass
class TaxTransaction:
    """Normalized transaction record. Monetary fields are typed `float`
    on purpose — the parse/IO boundary is float (every brokerage CSV
    arrives as text → float, and JSON has no Decimal). Gains engines
    wrap each value in `D()` before any sum or pool mutation, so the
    precision-sensitive arithmetic happens in Decimal. Don't migrate
    these fields to `Decimal`: it cascades through every parser, every
    test fixture, and the JSON serializer for no observable bug, since
    intermediate float ops have IEEE-754 error well below cent
    precision."""
    action: str
    date: str
    symbol: str = ''
    quantity: float = 0.0
    currency: str = ''
    time: str = '09:30:00'
    date_settle: str = ''
    price: float = 0.0
    proceeds: float = 0.0
    commission: float = 0.0
    fee: float = 0.0
    net_amount: float = 0.0
    account: str = 'PORTFOLIO'
    type: str = ''
    gross_amount: float = 0.0
    description: str = ''
    symbol_new: str = ''
    # Traceability back to the corp-action event/election that emitted
    # this row (empty for ordinary broker rows). NOT part of
    # compute_id — the identifying content is unchanged.
    corp_event_id: str = ''
    corp_election: str = ''
    id: Optional[str] = None

    def __post_init__(self):
        if self.id is None:
            self.id = self.compute_id()

    def compute_id(self) -> str:
        """Deterministic content-hash ID used for dedup, wash-sale
        linkage, and trace cross-references. Floats go in via `repr()`
        rather than fixed-decimal formatting so distinct values cannot
        collide on rounding: Python's float `repr` (≥3.1) is the
        shortest decimal that uniquely round-trips back to the same
        IEEE-754 bit pattern — deterministic across platforms and full
        precision. The earlier `:.8f` formatting truncated below the
        satoshi level, so two distinct sub-satoshi crypto quantities
        could hash to the same ID and dedup as duplicates."""
        q = repr(float(self.quantity))
        p = repr(float(self.price))
        n = repr(float(self.net_amount))
        g = repr(float(self.gross_amount))
        
        # Combine fields into a unique string
        components = [
            self.action,
            self.date,
            self.time,
            self.symbol,
            q,
            self.currency,
            p,
            n,
            g,
            self.account,
            self.type,
            self.date_settle,
            self.description
        ]
        raw_id = "|".join(str(c) for c in components)
        return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]

    def to_dict(self):
        return asdict(self)

# OCC option-symbol pattern: [F:|/|\]<base><yymmdd><C|P><strike-8d>[.<ext>]
# e.g. "AAPL250120C00150000.US", "MDA251219P00029000.TO", or
# "F:CL251220P00053000.US" for futures options.
#
# Single regex covering every option-detection / underlying-extraction
# need across the codebase:
#   - optional futures prefix (F: or / or \)
#   - non-greedy base ticker so the OCC date/strike block consumes greedily
#   - 6-digit yymmdd, C or P, 8-digit strike-×1000
#   - optional market suffix (.TO/.US/.AX/.L)
#   - end-anchored — rejects symbols with trailing junk
#
# Centralized so the Canada engine, US engine, and ticker-map helpers
# can't drift. Sites that previously used the bare `re.search(r'\d{6}[CP]\d+', ...)`
# detector now go through `is_option_symbol` for consistency — the
# anchoring change is safe because real OCC symbols never have trailing
# junk.
_OCC_OPTION_RE = re.compile(
    r'^((?:F:|[\\\/])?'             # optional futures prefix (captured)
    r'[A-Z0-9\.]{1,10}?)'           # base ticker (non-greedy)
    r'(\d{6}[CP]\d+)'               # OCC contract block
    r'(?:\.([A-Z]+))?'              # optional market suffix
    r'$'
)


def parse_option_underlying(symbol: str):
    """Return the underlying ticker (with market suffix) for an OCC
    option symbol, or None when `symbol` doesn't look like one. The
    futures prefix (`F:`/`/`/`\\`) IS preserved — so e.g.
    `F:CL250120P00053000.US` → `F:CL.US`, keeping futures positions
    distinct from same-base equity positions in by-ticker buckets."""
    if not symbol:
        return None
    m = _OCC_OPTION_RE.match(symbol)
    if not m:
        return None
    ext = m.group(3)
    return f"{m.group(1)}.{ext}" if ext else m.group(1)


def held_more_than_one_year(acq_date_str: str, disp_date_str: str) -> bool:
    try:
        acq = datetime.strptime(acq_date_str, '%Y-%m-%d')
        disp = datetime.strptime(disp_date_str, '%Y-%m-%d')
    except ValueError:
        return False
    # Pub 550: the holding period starts the day AFTER
    # acquisition, so "more than one year" is disp > the
    # anniversary. Rev. Rul. 66-7: property acquired on the
    # LAST day of a month starts its period on the 1st of the
    # next month and is held more than one year on the 1st of
    # that month a year later — so Feb 29 -> LT from Mar 1 of
    # the next year (not Mar 2), and Feb 28 of a common year
    # -> LT from Mar 1 as well (2026-09 US-engine audit; the
    # old code treated only the leap day specially and got
    # both directions wrong).
    import calendar as _cal
    last_day = _cal.monthrange(acq.year, acq.month)[1]
    if acq.day == last_day:
        nm_year = acq.year + (1 if acq.month == 12 else 0)
        nm_month = 1 if acq.month == 12 else acq.month + 1
        return disp >= datetime(nm_year + 1, nm_month, 1)
    anniversary = acq.replace(year=acq.year + 1)
    return disp > anniversary


def is_option_symbol(symbol: str) -> bool:
    """True if `symbol` is an OCC-format option ticker. Accepts the
    `F:`/`/`/`\\` futures prefix and optional market suffix."""
    return bool(symbol) and bool(_OCC_OPTION_RE.match(symbol))


def parse_option_expiry(symbol: str) -> Optional[str]:
    """ISO expiry date ('YYYY-MM-DD') from an OCC option symbol, or None
    when `symbol` isn't one (or carries an impossible date). The OCC
    block's first six digits are yymmdd; yy pivots into 20yy — valid
    until 2099, same horizon as every other OCC consumer here."""
    if not symbol:
        return None
    m = _OCC_OPTION_RE.match(symbol)
    if not m:
        return None
    yymmdd = m.group(2)[:6]
    try:
        return datetime.strptime(yymmdd, '%y%m%d').strftime('%Y-%m-%d')
    except ValueError:
        return None


def parse_option_right(symbol: str) -> Optional[str]:
    """'C' or 'P' for an OCC option symbol, else None."""
    m = _OCC_OPTION_RE.match(symbol or '')
    return m.group(2)[6] if m else None


def parse_option_strike(symbol: str) -> Optional[float]:
    """Strike price from an OCC option symbol (the 8-digit block is the
    strike x 1000), else None."""
    m = _OCC_OPTION_RE.match(symbol or '')
    return int(m.group(2)[7:]) / 1000.0 if m else None


def detect_option_replacement_matches(loss_entries, events, *, date_of,
                                      canonical=None, statute_label='',
                                      window_days=30,
                                      check_held_at_end=False):
    """WARN-ONLY option-as-replacement scan (user policy, 2026-07):

      'call_vs_share_loss': loss on LONG shares + LONG CALL acquired on
          the same underlying inside the ±window (CRA s.54 "a right to
          acquire"; IRS §1091 "option to acquire").
      'put_vs_short_loss':  loss from closing a SHORT + LONG PUT acquired
          on the same underlying inside the ±window.

    Deliberately asymmetric, per the decided policy: an option's own loss
    is NEVER triggered by share purchases (options wash only against the
    identical contract — the engines' existing symbol matching), and
    near-identical contracts (same underlying, different strike/expiry)
    are NOT matched. Detection only — computed numbers are never changed.

    `loss_entries`: dicts with symbol, date (already on the calling
    engine's window basis), amount (negative), id, direction.
    `events`: the full multi-scope stream (taxable + sheltered +
    affiliated — an affiliated or registered-account acquisition is still
    a replacement under both regimes).
    `canonical`: symbol canonicalizer (SplitTimeline.canonical) so a
    rename between the loss and the option acquisition still matches.
    Underlying matching is suffix-exact after canonicalization: an option
    suffixed .US never matches shares held under a .TO listing.
    `check_held_at_end`: CRA s.54 additionally requires the replacement
    still be owned at the end of the +window; when True each warning
    carries held_at_window_end for the OPTION's own position (across all
    scopes)."""
    canon = canonical or (lambda s: s)

    acqs = []                       # (canonical underlying, right, tx)
    for ev in events:
        if ev.action != 'BUYSELL' or ev.quantity <= 0:
            continue
        right = parse_option_right(ev.symbol)
        if right is None:
            continue
        acqs.append((canon(parse_option_underlying(ev.symbol)), right, ev))
    if not acqs:
        return []

    def _d(s):
        return datetime.strptime(s, '%Y-%m-%d')

    out = []
    for loss in loss_entries:
        symbol = loss['symbol']
        if is_option_symbol(symbol):
            continue
        want = 'P' if loss.get('direction') == 'SHORT' else 'C'
        try:
            loss_dt = _d(loss['date'])
        except (KeyError, TypeError, ValueError):
            continue
        loss_c = canon(symbol)
        window_end = loss_dt + timedelta(days=window_days)
        by_contract: Dict[str, Dict[str, Any]] = {}
        for und_c, right, ev in acqs:
            if right != want or und_c != loss_c:
                continue
            try:
                ev_dt = _d(date_of(ev))
            except (TypeError, ValueError):
                continue
            if abs((ev_dt - loss_dt).days) > window_days:
                continue
            rec = by_contract.setdefault(ev.symbol,
                                         {'qty': 0.0, 'first': None})
            rec['qty'] += ev.quantity
            if rec['first'] is None or date_of(ev) < rec['first']:
                rec['first'] = date_of(ev)
        for occ, rec in sorted(by_contract.items()):
            held = None
            if check_held_at_end:
                bal = 0.0
                for ev in events:
                    if ev.symbol != occ or \
                            ev.action not in ('BUYSELL', 'ASSIGN'):
                        continue
                    try:
                        if _d(date_of(ev)) <= window_end:
                            bal += ev.quantity
                    except (TypeError, ValueError):
                        continue
                held = bal > 1e-6
            out.append({
                'rule': ('call_vs_share_loss' if want == 'C'
                         else 'put_vs_short_loss'),
                'loss_symbol': symbol,
                'loss_date': loss['date'],
                'loss_amount': round(float(loss['amount']), 2),
                'loss_id': loss.get('id', ''),
                'option_symbol': occ,
                'option_acquired': rec['first'],
                'option_qty': rec['qty'],
                'held_at_window_end': held,
                'statute': statute_label,
            })
    return out


def _emit_option_replacement_stderr(warnings) -> None:
    for w in warnings:
        held = ''
        if w['held_at_window_end'] is not None:
            held = (' — still held at window end'
                    if w['held_at_window_end'] else
                    ' — NOT held at window end (s.54 would likely not '
                    'apply)')
        print(
            f"warning: option-replacement (warn-only, numbers unchanged): "
            f"{w['loss_symbol']} loss {w['loss_amount']:+,.2f} on "
            f"{w['loss_date']} has {w['option_symbol']} acquired "
            f"{w['option_acquired']} in the ±30d window{held}; under "
            f"{w['statute']} this loss would be denied [{w['rule']}]",
            file=sys.stderr,
        )


_JSON_COMMENT_RE = re.compile(r'"(?:[^"\\]|\\.)*"|(#[^\n]*)')


def strip_json_comments(content: str) -> str:
    """Strip `#`-prefixed comments from a JSON-ish document while
    preserving any `#` that appears inside a string literal. JSON has
    no native comment syntax, but our intermediate files sometimes
    carry user-added comments for legibility. A naive line-strip would
    also drop a (perversely-formatted) JSON value beginning at column 0
    with `#`; this string-aware pass walks string literals first so the
    `#`-as-data case is safe."""
    return _JSON_COMMENT_RE.sub(
        lambda m: '' if m.group(1) is not None else m.group(0),
        content,
    )


def coerce_transaction_row(t, i: int, ctx_prefix: str) -> TaxTransaction:
    """One row's coercion + validation — the shared body of
    load_transactions and pipeline.load_stdin_transactions, so the
    stdin path cannot drift back to silently dropping rows or skipping
    the FUZZ #K hard guards. Handles TaxTransaction passthrough,
    mapping coercion, `qty` aliasing, known-field filtering, and the
    non-finite / impossible-date / SPLIT-ratio refusals."""
    import inspect
    valid_keys = inspect.signature(TaxTransaction).parameters.keys()
    if isinstance(t, TaxTransaction):
        return t
    if not isinstance(t, dict):
        # Try last-resort coercion via mapping protocol, mirroring
        # the historical sort.py behavior. A row that can't be
        # dict()'d is data corruption — refuse to silently drop
        # it. One missing buy/sell can invalidate an entire ACB
        # pool downstream, and the silent skip would make the
        # resulting wrong gain numbers very hard to trace back.
        try:
            t = dict(t)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"{ctx_prefix}: transaction at index "
                f"{i} is not a TaxTransaction, dict, or mapping-"
                f"coercible value — got {type(t).__name__}={t!r} "
                f"({e}). Refusing to drop the row silently because "
                f"a missing buy/sell would corrupt the ACB pool "
                f"downstream. Fix the upstream emitter or hand-edit "
                f"the JSON to remove the malformed entry."
            )
    if 'qty' in t and 'quantity' not in t:
        t = {**t, 'quantity': t['qty']}
    clean_t = {k: v for k, v in t.items() if k in valid_keys}
    # Hard input guards (FUZZ #K) — refuse cleanly instead of
    # letting garbage reach the engines (NaN drove the Canada
    # engine into a decimal.InvalidOperation traceback and the USA
    # engine into NaN outputs; SPLIT ratio <= 0 silently corrupted
    # pools; impossible calendar dates passed straight through).
    import math
    _ctx = (f"{ctx_prefix}: transaction at index {i} "
            f"(id={t.get('id', '?')!r}, symbol="
            f"{t.get('symbol', '?')!r})")
    # Type funnel (stage-tools audit): every numeric field must be a
    # real number (a numeric STRING is coerced with float(); null,
    # bools, lists, non-numeric strings are refused) and every string
    # field must be a str (null falls back to the field's default).
    # Before this, `"net_amount": null` reached compute_id's float()
    # as a bare TypeError traceback, `"quantity": "10"` crashed the
    # phantom walk's arithmetic, and `"symbol": 0` crashed the engines
    # — none of them caught by the tools' ValueError handlers.
    for _fld in ('quantity', 'price', 'proceeds', 'commission', 'fee',
                 'net_amount', 'gross_amount'):
        if _fld not in clean_t:
            continue
        _v = clean_t[_fld]
        if _v is None or isinstance(_v, bool) \
                or not isinstance(_v, (int, float, Decimal, str)):
            raise ValueError(
                f"{_ctx}: {_fld}={_v!r} is not a number (got "
                f"{type(_v).__name__}) — fix the input data.")
        if isinstance(_v, str):
            try:
                _v = float(_v)
            except ValueError:
                raise ValueError(
                    f"{_ctx}: non-numeric {_fld}={clean_t[_fld]!r} — "
                    f"fix the input data.")
            clean_t[_fld] = _v
        if isinstance(_v, (float, Decimal)) and not math.isfinite(_v):
            raise ValueError(f"{_ctx}: non-finite {_fld}={_v!r} — "
                             f"fix the input data.")
    for _fld in ('action', 'date', 'symbol', 'currency', 'time',
                 'date_settle', 'account', 'type', 'description',
                 'symbol_new', 'corp_event_id', 'corp_election', 'id'):
        if _fld not in clean_t:
            continue
        _v = clean_t[_fld]
        if _v is None:
            if _fld in ('action', 'date'):
                raise ValueError(
                    f"{_ctx}: required field {_fld} is null — fix the "
                    f"input data.")
            if _fld != 'id':
                del clean_t[_fld]      # dataclass default applies
        elif not isinstance(_v, str):
            raise ValueError(
                f"{_ctx}: {_fld}={_v!r} must be a string (got "
                f"{type(_v).__name__}) — fix the input data.")
    for _fld in ('action', 'date'):
        if _fld not in clean_t:
            raise ValueError(
                f"{_ctx}: required field {_fld} is missing — fix the "
                f"input data.")
    for _fld in ('date', 'date_settle'):
        _d = clean_t.get(_fld)
        if _d:
            try:
                datetime.strptime(str(_d), '%Y-%m-%d')
            except ValueError:
                raise ValueError(
                    f"{_ctx}: impossible {_fld}={_d!r} (not a real "
                    f"calendar date) — fix the input data.")
    if clean_t.get('action') == 'SPLIT' \
            and float(clean_t.get('quantity') or 0) <= 0:
        raise ValueError(
            f"{_ctx}: SPLIT ratio must be > 0 (got "
            f"{clean_t.get('quantity')!r}) — a zero/negative ratio "
            f"is never a real corporate action.")
    return TaxTransaction(**clean_t)


def load_transactions(path: Path) -> List[TaxTransaction]:
    """Canonical taxjson loader — the single implementation that every
    bin tool should use. Handles:

    * `#`-prefixed comments (full-line and trailing). String-aware so
      a `#` inside a JSON string value is preserved.
    * `qty` → `quantity` aliasing (older parsers/tests emit `qty`).
    * Filtering to TaxTransaction's known fields so extra keys from
      enrichment passes don't blow up the dataclass init.
    * Already-TaxTransaction items in the list (passthrough — useful
      when an in-memory pipeline composes loaders).
    * The FUZZ #K hard input guards (via coerce_transaction_row).
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.loads(strip_json_comments(f.read()))

    txs = data.get("transactions", []) if isinstance(data, dict) else data
    return [coerce_transaction_row(t, i, f"load_transactions({path})")
            for i, t in enumerate(txs)]

def convert_currency(amount: float, from_curr: str, to_curr: str, rates: Dict[Any, Decimal], default_rate: float) -> float:
    """Convert amount from from_curr to to_curr.

    rates may be keyed by (from_curr, to_curr) tuples (preferred), or by
    from_curr strings — in the string form the dict's implicit target must be
    to_curr or the result is nonsense, so callers should prefer tuple keys.
    """
    if from_curr == to_curr:
        return amount
    rate = rates.get((from_curr, to_curr))
    if rate is None:
        rate = rates.get(from_curr)
    if rate is None:
        rate = Decimal(str(default_rate))
    return float(Decimal(str(amount)) * rate)

# Registry for brokerages: {id: parser_class}.
_BROKERAGES = {}


def register_brokerage(id, cls):
    _BROKERAGES[id] = cls


def load_brokerage(id):
    if id not in _BROKERAGES:
        raise ValueError(f"Unknown brokerage: {id}")
    return _BROKERAGES[id]

def _effective_fee_for_trace(tx) -> float:
    """Fee actually paid on this transaction, for display in trace lines.

    When the parser broke out commission/fee explicitly, return their sum.
    When both are zero — some brokers fold fees into net_amount only (RBC
    being the canonical case) — back-compute from |qty * price * multiplier|
    vs |net_amount|. OCC option symbols get the 100x contract multiplier so
    a -75 @ 0.4193 option sale shows the ~$100 commission RBC charged instead
    of a misleading 0.

    The math the engine uses for cost basis / proceeds is unaffected — it
    already keys off net_amount, which includes the fee. This is purely a
    display-side enrichment.
    """
    explicit = float(tx.commission) + float(tx.fee)
    if abs(explicit) > 1e-6:
        return explicit
    qty = abs(float(tx.quantity))
    price = float(tx.price)
    net = abs(float(tx.net_amount))
    if qty < 1e-9 or price < 1e-9 or net < 1e-9:
        return 0.0
    is_option = is_option_symbol(tx.symbol or '')
    multiplier = 100 if is_option else 1
    theoretical = qty * price * multiplier
    derived = abs(theoretical - net)
    # Sanity guard: a > 25% discrepancy is almost always a units mismatch
    # (e.g. price is a per-contract quote vs net is per-share), not a fee.
    # Better to show 0 than a fictitious large number.
    if derived > 0.25 * net:
        return 0.0
    return derived


def _warn_undrained_adjustments(pending: Dict[str, float], engine: str) -> None:
    """End-of-run drainage check for staged option-assignment premium.

    ASSIGN option legs park their premium in a pending-adjustments dict to
    be consumed by the matching stock leg's BUYSELL. When the stock leg is
    absent or its symbol doesn't match (missing statement rows, symbol
    drift), the premium used to be dropped SILENTLY — the option's economics
    simply vanished from the books. Say so, per symbol, with the amount."""
    undrained = {s: amt for s, amt in pending.items() if abs(amt) > 0.005}
    if not undrained:
        return
    def _kname(k):
        return f"{k[1]} ({k[0]})" if isinstance(k, tuple) else str(k)
    detail = ", ".join(f"{_kname(s)}: {amt:+.2f}"
                       for s, amt in sorted(undrained.items(),
                                            key=lambda kv: str(kv[0])))
    print(
        f"warning: {len(undrained)} unconsumed option-assignment "
        f"adjustment(s) at end of {engine} gains run — {detail}. An ASSIGN "
        f"option leg staged this premium for a stock leg that never "
        f"arrived (missing rows or symbol mismatch); that premium is NOT "
        f"reflected in any gain. Check the underlying's buy/sell rows "
        f"around the assignment date.",
        file=sys.stderr,
    )


def _verify_share_conservation(position_rows, actual_qty_by_symbol,
                                engine_label, *, zero_ratio_skips):
    """Conservation post-condition: replay signed position quantities with
    plain arithmetic (+= qty; SPLIT scales; rename migrates) and compare
    against the engine's end inventory per symbol. The replay is
    deliberately SIMPLE — no chunking, no wash logic, no basis — so
    regressions in the complex pool/lot machinery (ordering, crossing-zero,
    OB scaling, rename merges, double-applied splits) surface as one
    mismatch warning here. It shares the upstream split dedup and sort
    order with the engine (a second independent copy of those would
    recreate the exact multi-copy drift this guards against); everything
    downstream of them is independent.

    `position_rows`: engine-ordered rows already filtered to what the
    engine lets mutate inventory (taxable scope; income/transfer rows
    removed). `zero_ratio_skips`: the US engine skips a ratio-0 SPLIT's
    SCALING but still applies its rename migration (2026-08 fix — the
    old full-skip forked the book); the Canada engine applies the
    ratio blindly — mirror each.

    Emits `warning:`-prefixed stderr lines (picked up by the .diag /
    DIAGNOSTICS banner); never raises."""
    expected: Dict[str, float] = {}
    redirect: Dict[str, str] = {}

    def rkey(sym: str) -> str:
        while sym in redirect:
            sym = redirect[sym]
        return sym

    for t in position_rows:
        if t.action == 'SPLIT':
            ratio = float(t.quantity or 0.0)
            k = rkey(t.symbol)
            if not (ratio == 0.0 and zero_ratio_skips):
                expected[k] = expected.get(k, 0.0) * ratio
            target = (t.symbol_new or '').strip()
            if target and target != t.symbol:
                tk = rkey(target)
                if tk != k:
                    expected[tk] = expected.get(tk, 0.0) + expected.pop(k, 0.0)
                    redirect[k] = tk
        elif t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE'):
            k = rkey(t.symbol)
            expected[k] = expected.get(k, 0.0) + t.quantity

    actual: Dict[str, float] = {}
    for sym, qty in actual_qty_by_symbol.items():
        k = rkey(sym)
        actual[k] = actual.get(k, 0.0) + qty

    for k in sorted(set(expected) | set(actual)):
        e, a = expected.get(k, 0.0), actual.get(k, 0.0)
        if abs(e - a) > 1e-3:
            print(
                f"warning: conservation: {engine_label} share-count "
                f"mismatch for {k}: replaying the transactions gives "
                f"{e:.4f} but the engine's inventory holds {a:.4f} "
                f"(delta {a - e:+.4f}). The gains for this symbol are "
                f"NOT trustworthy — this indicates an engine ordering/"
                f"split/opening-balance bug, not bad input data.",
                file=sys.stderr,
            )


def _warn_stranded_basis(pools) -> None:
    """Basis-residue post-condition (Canada pools): a pool whose quantity
    has drained to zero must carry ~zero cost — the average-cost math
    consumes exactly the remaining basis on a full drain. Residue means
    leaked/duplicated basis (chunk-math regressions) or a real-world
    late ADJUST (e.g. an ETF's ROC posting after a full exit — which is
    taxable as a gain and needs manual handling, not a silent strand).

    The full basis identity (cost in == cost consumed + cost remaining)
    is deliberately NOT asserted: reproducing the engine's premium
    apportioning, taint gating, and crossing-zero proportional cost
    would be a second implementation of the pooling itself — the
    residue check is the honest subset."""
    seen_objs = set()
    for sym, pool in pools.items():
        if id(pool) in seen_objs:
            continue                    # rename aliases share one object
        seen_objs.add(id(pool))
        if abs(pool['qty']) <= 1e-6 and abs(float(pool['total_cost'])) > 0.02:
            print(
                f"warning: conservation: {sym} pool is EMPTY but carries "
                f"{float(pool['total_cost']):.2f} of stranded basis — "
                f"either an engine chunk-math bug or an ADJUST/ROC row "
                f"posted after the position was fully closed (the latter "
                f"is a taxable event needing manual review).",
                file=sys.stderr,
            )


def _dedupe_corporate_splits(txs: List[TaxTransaction], seen: set) -> List[TaxTransaction]:
    """Drop repeated SPLIT rows for the same corporate event.

    A stock split is a property of the SECURITY, not of an account: every
    brokerage parser emits its own SPLIT row per account, and their ids differ
    (account/description feed compute_id), so `--dedup` can't collapse them.
    On merged multi-account input — one account fed by several brokers, or the
    combined sheltered context file — the same event then arrives N times and
    the engines' symbol-global pools/walks get scaled by ratio**N (e.g. two
    accounts holding CRWD through its split doubled the pool).

    Keyed on (symbol, date, ratio, symbol_new); `seen` is shared across the
    taxable/sheltered/affiliated lists so a split present in more than one
    list is still applied exactly once. Limitation: two brokers reporting
    slightly different ratios for the same event (rounding) won't collapse.

    Delegates to SplitTimeline.dedupe — the one definition of split-event
    identity (see lib/corporate_timeline.py).
    """
    return SplitTimeline.dedupe(txs, seen)


class TaxRules:
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True) -> Dict[str, Any]:
        raise NotImplementedError()

class CanadaTaxRules(TaxRules):
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True) -> Dict[str, Any]:
        """
        Detects Superficial Losses (Wash Sales) based on CRA rules and calculates gains.
        Handles multi-account pooling and iterative adjustments.

        ITA 54 disallows a loss as "superficial" when an affiliated person
        (you, your spouse, a corporation you control, your RRSP/TFSA, etc.)
        acquired identical property in the ±30 day window and still holds
        some at the end of day 30. We split the inputs:

        - `transactions`: YOUR taxable trades (the gains/losses we compute).
        - `sheltered_transactions`: YOUR sheltered-account trades (RRSP,
          TFSA, etc.). Same taxpayer, but the basis adjustment goes onto
          property whose ACB doesn't matter for tax purposes.
        - `affiliated_transactions`: OTHER affiliated persons' trades (e.g.
          your spouse). Trigger the same superficial-loss treatment from
          your perspective; the deferred loss attaches to the affiliated
          person's substituted property per ITA 53(1)(f.1) — tracked by
          your spouse on THEIR return, not yours.

        Both `sheltered` and `affiliated` are pure additive context for
        wash detection; passing them only ever increases the set of
        candidate-replacement trades.
        """
        # One corporate split = one application: collapse per-account SPLIT
        # duplicates across all three lists (shared `seen`) before any
        # symbol-global pool or window walk sees them.
        _seen_splits: set = set()
        transactions = _dedupe_corporate_splits(transactions, _seen_splits)
        sheltered_transactions = _dedupe_corporate_splits(
            sheltered_transactions or [], _seen_splits)
        affiliated_transactions = _dedupe_corporate_splits(
            affiliated_transactions or [], _seen_splits)

        all_txs = transactions + (sheltered_transactions or []) + (affiliated_transactions or [])
        sheltered_ids = {t.id for t in (sheltered_transactions or [])}
        affiliated_ids = {t.id for t in (affiliated_transactions or [])}
        taxable_ids = {t.id for t in transactions}

        def get_sort_date(tx):
            return tx.date_settle if tx.date_settle else tx.date

        # A SPLIT inside a trade's settle lag — after the execution
        # MOMENT (date, clock time) and before the settle date: the
        # engine orders by settle date, so the pool splits before the
        # executed (pre-split-denominated) quantity is consumed. The
        # executed quantity/price are re-denominated into post-split
        # terms (qty x ratio, price / ratio; the MONEY — net_amount —
        # is untouched), which books the trade correctly against the
        # post-split pool. Splits ON the settle date are already
        # handled by the pre-existing phase ladder and excluded here.
        # The execution side compares clock time because IB posts
        # corporate actions in an evening batch (20:25) dated the
        # trade day: a sale executed that morning was pre-split, and
        # a date-only "strictly between" test left 84 x 0.1 phantom
        # shares (real FFN 11-for-10, 2026-07-02). A split with no
        # meaningful time (00:00:01, parser default) still sorts
        # before every same-day execution — the ladder's convention.
        # A RENAME-split (symbol changes) straddling the lag would
        # also need re-symboling mid-flight — refuse loudly, as
        # before (rare squared).
        _lag_splits = [t for t in all_txs
                       if t.action == 'SPLIT' and t.date]
        if _lag_splits:
            _redenoms: Dict[int, float] = {}
            for _i, _t in enumerate(all_txs):
                if not (_t.action in ('BUYSELL', 'ASSIGN') and _t.date
                        and _t.date_settle
                        and _t.date < _t.date_settle):
                    continue
                _f = 1.0
                for _sp in _lag_splits:
                    # The ladder places a SPLIT at its SORT date
                    # (settle when set): the straddle test must use
                    # the same date, or a settle-desynced SPLIT row
                    # re-denominates the trade while the pool splits
                    # later — silent phantom shares (round-six
                    # adversarial audit finding 2).
                    _spd = _sp.date_settle or _sp.date
                    if not ((_t.date, _t.time or '00:00:00')
                            < (_spd, _sp.time or '00:00:00')
                            and _spd < _t.date_settle
                            and _sp.symbol == _t.symbol):
                        continue
                    _new = getattr(_sp, 'symbol_new', '') or ''
                    if _new and _new != _sp.symbol:
                        raise SplitStraddlesSettlementError(
                            f"{_t.symbol}: the RENAME-split dated "
                            f"{_sp.date} {_sp.time or ''} ({_sp.symbol} "
                            f"-> {_new}) falls between the {_t.date} "
                            f"{_t.time or ''} execution and "
                            f"{_t.date_settle} "
                            f"settlement of a {_t.quantity:g}-share "
                            f"trade (account {_t.account}) — the "
                            f"trade would need re-symboling "
                            f"mid-flight. Set that row's date_settle "
                            f"equal to its trade date, or re-date "
                            f"the SPLIT off the lag.")
                    if float(_sp.quantity or 0):
                        _f *= float(_sp.quantity)
                if abs(_f - 1.0) > 1e-12:
                    _redenoms[_i] = _f
            if _redenoms:
                all_txs = list(all_txs)
                for _i, _f in _redenoms.items():
                    _t = all_txs[_i]
                    _d = _t.to_dict()
                    _d['quantity'] = float(_t.quantity) * _f
                    if float(_t.price or 0):
                        _d['price'] = float(_t.price) / _f
                    _r = TaxTransaction(**{**_d, 'id': _t.id})
                    all_txs[_i] = _r
                    print(f"NOTE: {_t.symbol}: re-denominated a "
                          f"{_t.quantity:g}-share trade executed "
                          f"{_t.date} through the x{_f:g} split "
                          f"inside its settle lag (booked as "
                          f"{_d['quantity']:g} post-split shares; "
                          f"money unchanged).", file=sys.stderr)
                # Rebuild the per-book lists from the same slices
                # (all_txs is transactions + sheltered + affiliated,
                # in order) so every downstream walk sees the
                # re-denominated rows.
                _n1 = len(transactions)
                transactions = all_txs[:_n1]
                _n2 = _n1 + len(sheltered_transactions or [])
                sheltered_transactions = all_txs[_n1:_n2]
                affiliated_transactions = all_txs[_n2:]

        # Sort keys of explicitly-marked assignment STOCK legs
        # (action='ASSIGN' on a non-option symbol — the parsers' two-row
        # convention), per symbol, TAXABLE book only (re-audit: a
        # sheltered/affiliated marked leg must not gate the taxable
        # book's own consumption — only taxable rows can pop the staged
        # premium). When an UPCOMING marked leg exists, only it may
        # consume the premium; an unrelated same-symbol BUYSELL sorted
        # between the option leg and the stock leg used to hijack it.
        # Time-scoped on purpose: once a marked leg has passed, later
        # plain-convention trades pop normally — a mixed-convention
        # book (two brokers) would otherwise strand every later premium
        # behind a long-gone marked leg.
        # Keyed per (ACCOUNT, symbol): in a combined multi-account book
        # (the blended pass) one account's marked leg must neither gate
        # nor absorb another account's premium — assignment legs and
        # their stock legs always share an account. Single-account
        # books behave identically (the account component is constant).
        assign_stock_leg_keys: Dict[Any, list] = {}
        for _t in transactions:
            if _t.action == 'ASSIGN' and not is_option_symbol(_t.symbol):
                assign_stock_leg_keys.setdefault(
                    (_t.account, _t.symbol), []).append(
                    (get_sort_date(_t), _t.time or ''))

        def _upcoming_marked_leg(acct, sym, d, tm):
            return any(k >= (d, tm or '')
                       for k in assign_stock_leg_keys.get((acct, sym), ()))

        # (ACCOUNT, underlying) pairs that actually trade as STOCK in
        # the taxable book. An option ASSIGN whose underlying is absent
        # for ITS OWN account is cash-settled (or missing its leg) —
        # its P&L must be realized on the option itself, not staged for
        # a stock leg that cannot exist. OPENING_BALANCE deliberately
        # does NOT count (re-audit: an OB-only underlying means the
        # assignment's stock leg is missing from the input — realizing
        # with the diagnosable note beats staging a premium nothing can
        # ever consume).
        taxable_stock_symbols = {
            (t.account, t.symbol) for t in transactions
            if not is_option_symbol(t.symbol)
            and t.action in ('BUYSELL', 'ASSIGN')}

        # One split/rename timeline per compute (SETTLE-basis dates, matching
        # this engine's window measurements). Virtual solver txs are never
        # SPLITs, so the timeline is stable across solver iterations — the
        # per-loss unit conversions below all query it.
        split_timeline = SplitTimeline.from_transactions(
            all_txs, date_of=get_sort_date)

        # Ordering (phase ladder + tie-break priorities) is centralized in
        # lib/corporate_timeline.event_sort_key — profile 'ca_main' for the
        # solver pass, 'ca_balance' (no priority rung) for the running-
        # balance walks. The settle-lagged phase rationale and the
        # OB/SPLIT 00:00:00 tie rationale are documented there.

        # Iterative solver state
        final_virtual_txs = []
        final_realized_gains = []
        final_wash_sales = []
        final_global_pools = {}
        adjust_to_trigger: Dict[str, str] = {}  # adj_id -> trigger_lot.id (full)
        loss_to_trigger: Dict[str, str] = {}  # loss tx.id -> PRIMARY trigger id
        loss_to_triggers_multi: Dict[str, list] = {}  # loss tx.id -> all allocated trigger ids
        permanent_by_loss: Dict[str, float] = {}  # loss tx.id -> sheltered-allocated denial
        wash_windows: Dict[str, Dict[str, Any]] = {}  # loss tx.id -> ±30d window listing
        final_pool_snapshots: Dict[str, Dict[str, float]] = {}  # tx.id -> pool state after that tx

        # Symbol-alias map for SPLIT-renames. CRA s. 85.1(5) and
        # similar reorgs treat the pre- and post-rename security as
        # substantially identical for wash-sale purposes — so SSL.TO
        # and RGLD.US must share a single time-series when computing
        # the 30-day affiliated balance, and a loss on SSL.TO must
        # consider RGLD.US buys as potential wash triggers.
        #
        # Build equivalence classes via simple union-find on every
        # SPLIT with a non-empty symbol_new. The map sends every
        # symbol to its canonical class representative.
        # The union-find lives on the SplitTimeline (same union condition:
        # every SPLIT with a non-empty, non-self symbol_new; rename applies
        # regardless of the ratio's truthiness). This engine used to carry
        # its own copy — one more hand-maintained duplicate of the rename
        # chain, now deleted.
        alias_of = split_timeline.canonical

        # Running affiliated balance per tx — same definition the wash test
        # uses (BUYSELL/ASSIGN/TRANSFER summed across all_txs, multiplicative
        # for SPLIT). Independent of the iteration loop because virtual
        # DISALLOW/ADJUST txs don't move this balance.
        #
        # Group trades by equivalence class (not raw symbol) so SSL.TO
        # → RGLD.US shares one chronological running balance. Without
        # this, post-rename RGLD.US buys wouldn't show up in the 30-day
        # affiliated check for an SSL.TO loss.
        running_bal_by_tx: Dict[str, float] = {}
        for rep in set(alias_of(t.symbol) for t in all_txs):
            txs_sym = sorted(
                [t for t in all_txs if alias_of(t.symbol) == rep],
                key=lambda t: event_sort_key(t, profile='ca_balance',
                                              date_of=get_sort_date),
            )
            # Per-RAW-SYMBOL sub-balances: a rename-SPLIT's ratio
            # scales only the shares of the symbol the event names —
            # scaling the whole class multiplied target-symbol shares
            # acquired BEFORE the rename, fabricating still-held
            # balance (2026-09 adversarial audit; the US engine's
            # per-symbol walks were the reference model).
            sub: Dict[str, float] = {}
            for t in txs_sym:
                # OPENING_BALANCE counts: phantom shares ARE held, and
                # excluding them made every walk that spans the opening
                # under-count the position (a clean loss after a phantom
                # drain could net to zero and dodge a real wash sale).
                if t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                'OPENING_BALANCE'):
                    sub[t.symbol] = sub.get(t.symbol, 0.0) + t.quantity
                elif t.action == 'SPLIT':
                    _dst = (t.symbol_new or '').strip() or t.symbol
                    sub[_dst] = (sub.get(_dst, 0.0)
                                 if _dst != t.symbol else 0.0) \
                        + sub.pop(t.symbol, 0.0) * t.quantity
                running_bal_by_tx[t.id] = sum(sub.values())
        
        # Limit iterations to prevent infinite loops. Pathological inputs
        # (very dense same-symbol activity producing daisy-chained losses
        # across many accounts) can in principle exhaust this. We detect
        # non-convergence below and warn — silently truncating would
        # silently mis-state gains.
        solver_converged = False
        solver_iterations_used = 0
        for iteration in range(1000):
            solver_iterations_used = iteration + 1
            current_tx_list = all_txs + final_virtual_txs
            current_tx_list.sort(
                key=lambda x: event_sort_key(x, profile='ca_main',
                                             date_of=get_sort_date))
            
            # Pools indexed by symbol
            global_pools = {}  # symbol -> {'qty', 'total_cost', 'last_acq_date', 'currency', 'tainted'}

            pending_adjustments = {} # symbol -> amount
            iteration_realized_gains = []
            iteration_losses = []
            iteration_trace = []
            symbol_acb_traces = {}
            pool_snapshots_this_iter: Dict[str, Dict[str, float]] = {}
            
            if trace:
                iteration_trace.append("# " + "-" * 215)
                iteration_trace.append("# ACCOUNT                    | NOTE         | DATE                | SYMBOL                     | ACTION   | QTY        | PRICE      | FEE/SH     | PRICE_FEE  | VALUE_NET  | ADJUSTMENT | GLOBAL_BAL | POOL_ACB   | ACB/SHARE  | REALIZED   | DISALLOWED | TRIGGER_INFO")
                iteration_trace.append("# " + "-" * 215)

            for tx in current_tx_list:
                symbol = tx.symbol
                account = tx.account
                is_sheltered = tx.id in sheltered_ids
                is_affiliated = tx.id in affiliated_ids
                # Sheltered and affiliated trades both live OUTSIDE the
                # taxable ACB pool — sheltered because registered-account
                # property is treated as separate for cost-basis purposes,
                # affiliated because the spouse/related-person owns it,
                # not the user. The wash-sale walk still sees them via
                # current_tx_list (so the 30-day "still held" balance and
                # the substitution-property rule fire correctly), but the
                # pool stays clean.
                is_other_scope = is_sheltered or is_affiliated

                # Non-capital cash-flow events (dividends, interest, fees,
                # taxes, transfers) don't touch the ACB pool or account
                # balances. They often live on a synthetic "CASH" symbol
                # whose currency varies by row, so skip them before
                # currency/pool initialization. TRANSFER is handled at the
                # CLI layer — for sheltered files it's rewritten to BUYSELL
                # before reaching the engine, and for taxable files the CLI
                # errors out before invoking compute_gains. By the time
                # the engine runs, no TRANSFER rows should be present.
                if tx.action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER') \
                   or tx.type in ('dividend', 'dividend_in_lieu', 'tax', 'interest', 'fee'):
                    continue

                if symbol not in global_pools:
                    # total_cost is held as Decimal to avoid drift across many
                    # accumulating transactions; qty stays float (share counts
                    # don't suffer the same way at sub-cent scales).
                    # 'tainted' goes True when a phantom OPENING_BALANCE enters
                    # the pool (pre-data-window shares with unknown ACB) and
                    # back False when the pool drains to zero. Dispositions
                    # from a tainted pool are excluded from gain computation.
                    # `position_start_date` tracks the date of the trade
                    # that re-opened the current run of holding this
                    # symbol (cleared on each drain-to-zero, set again
                    # on the next open). Used by the inventory section
                    # so a downstream tool can look up the price as of
                    # the position entry date.
                    global_pools[symbol] = {'qty': 0.0, 'total_cost': Decimal(0), 'last_acq_date': '1970-01-01', 'currency': '', 'tainted': False, 'position_start_date': None, 'deferred_wash': 0.0}
                pool = global_pools[symbol]

                # Currency-mix guard: a single ACB pool must be
                # denominated in one currency. Gated on the taxable
                # scope — sheltered/affiliated trades live in their
                # own books and must not stamp (or be enforced
                # against) the taxable pool's currency. Without this
                # gate, an opening sheltered trade in USD would set
                # the pool's currency, then a later taxable CAD trade
                # on the same symbol would hard-error on the mismatch.
                if not is_other_scope:
                    if tx.currency and pool['currency'] and tx.currency != pool['currency']:
                        raise ValueError(
                            f"Currency mismatch for {symbol}: pool is in {pool['currency']!r} "
                            f"but transaction {tx.id} on {tx.date} is in {tx.currency!r}. "
                            f"Run taxjson_convert_currency first to unify currencies."
                        )
                    if tx.currency and not pool['currency']:
                        pool['currency'] = tx.currency

                # Internal adjustment logic (Option Assignment).
                # Gated on the taxable scope: only a taxable BUYSELL
                # on the underlying may consume the pending premium.
                # Popping for a sheltered/affiliated trade would
                # silently discard the adjustment (the pass branch
                # downstream skips pool mutation for is_other_scope),
                # leaving a later taxable trade on the same underlying
                # with no premium roll.
                if is_other_scope:
                    internal_adj = 0.0
                elif (tx.action == 'ASSIGN'
                      or not _upcoming_marked_leg(
                          tx.account, symbol, get_sort_date(tx), tx.time)):
                    internal_adj = pending_adjustments.pop(
                        (tx.account, symbol), 0.0)
                else:
                    # A marked ASSIGN stock leg exists in the stream —
                    # the premium belongs to it, not to this unrelated
                    # trade on the same underlying.
                    internal_adj = 0.0
                
                action = tx.action
                qty = tx.quantity
                
                # Variables for trace
                realized_pl = None
                disallowed_amt = 0.0
                note = ""
                trigger_info = ""
                adjustment_shown = 0.0
                
                is_option_assign = False
                if action == 'ASSIGN' and is_option_symbol(symbol):
                    _und = parse_option_underlying(symbol)
                    if (_und and (tx.account, _und)
                            in taxable_stock_symbols) \
                            or is_other_scope:
                        # Sheltered/affiliated assignment pairs keep the
                        # premium-roll semantics regardless of the
                        # TAXABLE stock set — their pools aren't
                        # computed here, and the cash-settled note would
                        # be spurious for a complete sheltered pair
                        # (re-audit).
                        is_option_assign = True
                    else:
                        # Cash-settled assignment/exercise (index
                        # options like XSP/SPX — the underlying never
                        # trades as stock in this book). No stock leg
                        # can ever consume a staged premium, so fall
                        # through to NORMAL disposition accounting: the
                        # option's own P&L is realized here instead of
                        # being staged forever and dropped. The note
                        # keeps the missing-data case diagnosable.
                        print(f"note: {symbol}: assignment treated as "
                              f"cash-settled ({_und or '?'} never "
                              f"trades as stock in this book) — option "
                              f"P&L realized directly. If a stock leg "
                              f"is missing from your input, add it and "
                              f"re-run.", file=sys.stderr)
                
                if action == 'DISALLOW':
                    note = "DISALLOWANCE"
                    adjustment_shown = tx.net_amount
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        symbol_acb_traces[symbol].append(f"# {tx.date} DISALLOW {tx.net_amount:10.4f} | (Loss disallowed on this date)")
                elif action == 'ADJUST':
                    _applied_adj = float(tx.net_amount)
                    if not is_other_scope:
                        # A user ROC ADJUST landing on a DRAINED pool
                        # (position fully sold before the distribution
                        # posted) has no shares to adjust: the residual
                        # silently folds into the NEXT position's ACB,
                        # misstating BOTH dispositions. Retroactive
                        # reallocation is out of scope — warn loudly so
                        # the user re-dates or hand-applies it. Wash
                        # virtual ADJUSTs (WASH_*) always target held
                        # pools by construction.
                        if (abs(pool['qty']) <= 1e-6
                                and not str(tx.id or '').startswith('WASH_')
                                and abs(float(tx.net_amount)) > 0.005
                                and iteration == 0):
                            # iteration gate: the solver replays the
                            # whole book each pass, so an unconditional
                            # warning printed once PER ITERATION — one
                            # data problem masqueraded as several.
                            print(
                                f"warning: {symbol} ADJUST of "
                                f"{float(tx.net_amount):.2f} on {tx.date} "
                                f"hits an EMPTY pool — the position was "
                                f"fully sold before this ROC posted, so "
                                f"the amount would leak into the NEXT "
                                f"position's ACB instead of the one that "
                                f"earned it. Re-date the ADJUST before "
                                f"the final sale (adjusting that "
                                f"disposition's gain) or apply it "
                                f"manually.", file=sys.stderr)
                        _applied_adj = float(tx.net_amount)
                        if str(tx.id or '').startswith('WASH_'):
                            # Superficial-loss deferral: the invariant is
                            # "reduce future gains by the denied loss".
                            # For a LONG pool that means RAISING ACB
                            # (+amt); for a SHORT pool, LOWERING the
                            # entry proceeds held in total_cost (-amt).
                            # The creation-time sign keyed off the LOSS
                            # side, but s.47 pools are symbol-GLOBAL: in
                            # a blended multi-account book the pool at
                            # the landing site can be long while the
                            # loss (and its trigger) were short — the
                            # unconditioned -amt then LOWERED long ACB,
                            # inflating every later gain. Decide by the
                            # pool's actual direction here; a flat pool
                            # keeps the creation-sign proxy (the trigger
                            # opens on the loss side).
                            _mag = abs(_applied_adj)
                            if pool['qty'] > 1e-6:
                                _applied_adj = _mag
                            elif pool['qty'] < -1e-6:
                                _applied_adj = -_mag
                            else:
                                # FLAT pool: the sign cannot be decided
                                # yet — it belongs to whichever
                                # direction the pool NEXT opens
                                # (creation-sign fallback leaked a
                                # short-signed deferral into a
                                # subsequent LONG opening with
                                # inverted effect — conservation
                                # fuzzer, seed 65). Park the magnitude;
                                # the next opening applies it.
                                pool['pending_wash'] = (
                                    pool.get('pending_wash', 0.0)
                                    + _mag)
                                _applied_adj = 0.0
                        pool['total_cost'] += D(_applied_adj)
                        # Superficial-loss deferrals arrive as virtual
                        # ADJUSTs (id WASH_<loss>__...). Tally the
                        # dollars parked in this pool so inventory can
                        # report how much of the basis is deferred
                        # loss vs. real purchase cost.
                        if str(tx.id or '').startswith('WASH_'):
                            pool['deferred_wash'] = (
                                pool.get('deferred_wash', 0.0)
                                + abs(float(tx.net_amount)))
                        # ITA s.40(3): return-of-capital that drives ACB
                        # below zero is a deemed capital gain in that
                        # year. Rare enough to warrant human eyes rather
                        # than silent computation — flag, don't book.
                        if pool['qty'] > 1e-6 and pool['total_cost'] < D('-0.005'):
                            print(
                                f"warning: {symbol} ACB went NEGATIVE "
                                f"({float(pool['total_cost']):.2f}) after "
                                f"ADJUST on {tx.date} — under s.40(3) the "
                                f"excess is a deemed capital gain in that "
                                f"year, which this engine does NOT "
                                f"compute. Verify the ROC amounts and "
                                f"report the deemed gain manually.",
                                file=sys.stderr,
                            )
                    _shown_adj = (_applied_adj if not is_other_scope
                                  else tx.net_amount)
                    adjustment_shown = _shown_adj
                    note = "ACB_ADJUST"
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        pool_cost_f = float(pool['total_cost'])
                        acb_sh = pool_cost_f / pool['qty'] if abs(pool['qty']) > 1e-6 else 0.0
                        symbol_acb_traces[symbol].append(f"# {tx.date} ADJUST   {_shown_adj:10.4f} | Fee: 0.0000 | Cost_Added: {_shown_adj:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
                elif action == 'SPLIT':
                    if not is_other_scope:
                        pool['qty'] *= qty
                    # Rename the pool when symbol_new is set and differs
                    # from the source symbol — that's how mergers and
                    # corporate reorganizations move ACB onto a new
                    # ticker (e.g. SSL.TO → RGLD.US 1-for-16 via the
                    # s. 85.1(5) rollover election). Before this, the
                    # SPLIT silently dropped symbol_new and any future
                    # trade of the new ticker started from a zero-cost
                    # pool with no relationship to the source's basis.
                    target_symbol = (tx.symbol_new or '').strip()
                    if target_symbol and target_symbol != symbol:
                        if not is_other_scope:
                            existing = global_pools.get(target_symbol)
                            if existing is None:
                                global_pools[target_symbol] = pool
                            else:
                                # Pool already exists for the new ticker
                                # — merge (cost + qty add, taint sticks
                                # if either side was tainted). Currency
                                # mix would be an upstream bug; guard it.
                                if existing['currency'] and pool['currency'] \
                                        and existing['currency'] != pool['currency']:
                                    raise ValueError(
                                        f"SPLIT {symbol}→{target_symbol}: "
                                        f"currency mismatch "
                                        f"{pool['currency']!r} vs "
                                        f"{existing['currency']!r}. Run "
                                        f"taxjson_convert_currency first."
                                    )
                                if (existing['qty'] * pool['qty']
                                        < -1e-9):
                                    # Merging a LONG pool into a SHORT
                                    # one (or vice versa) is a close-out
                                    # event, not an addition — blind
                                    # summing turned short-sale proceeds
                                    # into phantom COST (FUZZ #F10).
                                    # Refuse loudly rather than corrupt.
                                    raise ValueError(
                                        f"SPLIT {symbol}→{target_symbol}"
                                        f": rename would merge a "
                                        f"{'LONG' if pool['qty'] > 0 else 'SHORT'}"
                                        f" position into an existing "
                                        f"{'LONG' if existing['qty'] > 0 else 'SHORT'}"
                                        f" pool — that is a close-out, "
                                        f"which this engine does not "
                                        f"net automatically. Record the "
                                        f"close explicitly (e.g. a .tt "
                                        f"BUYSELL) before the rename.")
                                existing['qty'] += pool['qty']
                                existing['total_cost'] += pool['total_cost']
                                existing['deferred_wash'] = (
                                    existing.get('deferred_wash', 0.0)
                                    + pool.get('deferred_wash', 0.0))
                                # Parked flat-pool deferral dollars ride
                                # the rename too — dropping them here
                                # silently erased the denied loss's
                                # future recovery (2026-09 adversarial
                                # audit). Apply signed by the TARGET
                                # pool's direction when it has one;
                                # else keep parking.
                                _src_pw = pool.pop('pending_wash', 0.0)
                                if _src_pw > 1e-9:
                                    if existing['qty'] > 1e-6:
                                        existing['total_cost'] += D(
                                            _src_pw)
                                    elif existing['qty'] < -1e-6:
                                        existing['total_cost'] += D(
                                            -_src_pw)
                                    else:
                                        existing['pending_wash'] = (
                                            existing.get(
                                                'pending_wash', 0.0)
                                            + _src_pw)
                                existing['tainted'] = (
                                    existing.get('tainted', False)
                                    or pool.get('tainted', False)
                                )
                                # Keep the EARLIER acquisition date when
                                # merging — CRA s. 85.1(5) rollover
                                # inherits the source's holding period
                                # onto the target. Picking max would
                                # pessimize the days-held column and
                                # could push a long-term holding into
                                # short-term territory under the wash-
                                # sale 30-day window.
                                #
                                # Skip the source's date if the source
                                # pool was auto-created with no prior
                                # trades — its `last_acq_date` is still
                                # the dataclass sentinel ('1970-01-01')
                                # and would overwrite the target's real
                                # acquisition date, producing wildly
                                # wrong days-held on later sales.
                                SENTINEL = '1970-01-01'
                                if (pool['qty'] != 0 and pool['last_acq_date'] != SENTINEL
                                        and pool['last_acq_date'] < existing['last_acq_date']):
                                    existing['last_acq_date'] = pool['last_acq_date']
                                if not existing['currency']:
                                    existing['currency'] = pool['currency']
                                # Preserve the EARLIEST position_start
                                # across the merged pools — the combined
                                # position's continuous-holding date is
                                # whichever side was entered first.
                                src_psd = pool.get('position_start_date')
                                ex_psd = existing.get('position_start_date')
                                if src_psd and (not ex_psd or src_psd < ex_psd):
                                    existing['position_start_date'] = src_psd
                            del global_pools[symbol]
                        if trace:
                            if target_symbol not in symbol_acb_traces:
                                symbol_acb_traces[target_symbol] = [
                                    f"# --- ACB CALCULATION TRACE: {target_symbol} ---"
                                ]
                            symbol_acb_traces[target_symbol].append(
                                f"# {tx.date} SPLIT-RENAME {symbol}→{target_symbol} "
                                f"ratio={qty:.6g} | pool moved to new ticker"
                            )
                elif action == 'OPENING_BALANCE':
                    # Phantom opening: pre-data-window shares with unknown
                    # ACB. Quantity is added at cost=0 (won't be trusted —
                    # the pool is marked tainted, so any disposition while
                    # phantom shares remain gets suppressed from gains).
                    # When the pool later drains to zero, taint clears and
                    # subsequent buys form a fresh, fully-known ACB pool.
                    if not is_other_scope:
                        if pool['position_start_date'] is None:
                            # OB seeds the position-start when the pool
                            # was empty. Position_start is approximate
                            # for tainted pools (the real entry is
                            # pre-data-window), but the OB date is the
                            # best signal available.
                            pool['position_start_date'] = tx.date
                        pool['qty'] += qty
                        # No cost added — taint flag tracks the unknown.
                        pool['tainted'] = True
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        symbol_acb_traces[symbol].append(f"# {tx.date} OPENING_BALANCE {qty:10.4f} | Phantom — pool TAINTED until drain to zero")
                else:
                    if abs(qty) < 1e-6: continue
                    is_opening = (pool['qty'] > 1e-6 and qty > 0) or \
                                 (pool['qty'] < -1e-6 and qty < 0) or \
                                 (abs(pool['qty']) <= 1e-6)

                    if is_other_scope:
                        # Sheltered (RRSP/TFSA/LIRA/RESP) and affiliated
                        # (spouse / related-person / controlled-corp) shares
                        # do NOT enter the taxable ACB pool — registered
                        # accounts are separate property per CRA, and
                        # affiliated-party trades belong on the other
                        # person's return. The wash-sale walk still sees
                        # them via current_tx_list, so the 30-day "still
                        # held" balance and ITA 54 substitution-property
                        # rule fire correctly.
                        pass
                    else:
                        if is_opening:
                            # BUY (or Short opening)
                            effective_cost = abs(tx.net_amount) + (internal_adj if qty > 0 else -internal_adj)
                            pool['total_cost'] += D(effective_cost)
                            _pw = pool.pop('pending_wash', 0.0)
                            if _pw > 1e-9:
                                # Deferred superficial-loss dollars
                                # parked while the pool was flat attach
                                # to this fresh position: raise a long
                                # position's ACB / lower a short's
                                # entry proceeds, so the next
                                # disposition recovers the loss.
                                pool['total_cost'] += D(
                                    _pw if qty > 0 else -_pw)
                            # Seed the position-start on the trade that
                            # re-opens the pool (cleared at the last
                            # drain-to-zero). Subsequent same-direction
                            # buys don't reset it — the position is a
                            # continuous run.
                            if pool['position_start_date'] is None:
                                pool['position_start_date'] = tx.date
                            pool['qty'] += qty
                            pool['last_acq_date'] = tx.date

                            if trace:
                                if symbol not in symbol_acb_traces:
                                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                fee_amt = _effective_fee_for_trace(tx)
                                pool_cost_f = float(pool['total_cost'])
                                acb_sh = pool_cost_f / pool['qty'] if abs(pool['qty']) > 1e-6 else 0.0
                                symbol_acb_traces[symbol].append(f"# {tx.date} {tx.action} {qty:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt:6.4f} | Cost_Added: {effective_cost:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
                        else:
                            # SELL (or Short covering) — divide in exact arithmetic.
                            if abs(pool['qty']) > 1e-6:
                                # SIGNED per-unit basis: total/|qty|,
                                # not abs(total/qty) (FUZZ #F11). A
                                # short pool whose opening proceeds
                                # went NEGATIVE under a large wash
                                # deferral (legitimate: deferred loss
                                # can exceed the re-short's premium)
                                # was silently flipped positive — the
                                # final cover booked +3500 instead of
                                # recovering the -4500.
                                avg_cost_unit_d = (pool['total_cost']
                                                   / D(abs(pool['qty'])))
                            else:
                                avg_cost_unit_d = Decimal(0)
                            closing_qty = min(abs(qty), abs(pool['qty']))
                            cost_basis = float(D(closing_qty) * avg_cost_unit_d)
                            
                            # Apportion adjustment
                            chunk_adj = internal_adj * (closing_qty / abs(qty))
                            proceeds = abs(tx.net_amount) * (closing_qty / abs(qty))
                            effective_proceeds = proceeds + (chunk_adj if pool['qty'] < 0 else -chunk_adj)
                            
                            gain = (effective_proceeds - cost_basis) if pool['qty'] > 0 else (cost_basis - effective_proceeds)
                            
                            # Days held
                            try:
                                acq_dt = datetime.strptime(pool['last_acq_date'], '%Y-%m-%d')
                                disp_dt = datetime.strptime(tx.date, '%Y-%m-%d')
                                raw_days = (disp_dt - acq_dt).days
                                if raw_days < 0:
                                    print(
                                        f"warning: negative days_held for {tx.symbol} "
                                        f"on {tx.date} (last_acq={pool['last_acq_date']}); "
                                        f"this usually indicates out-of-order transactions",
                                        file=sys.stderr,
                                    )
                                days_held = max(0, raw_days)
                            except ValueError: days_held = 0
                            
                            # Check for taxable event (if tx is in the main transactions list)
                            is_taxable = tx.id in taxable_ids
                            
                            if is_taxable and not is_option_assign:
                                # Check for virtual disallowance for this specific ID
                                disallowance_tx = next((v for v in final_virtual_txs if v.action == 'DISALLOW' and v.id == tx.id), None)
                                if disallowance_tx:
                                    disallowed_amt = disallowance_tx.net_amount
                                
                                realized_pl = gain
                                
                                rg_trace = []
                                if trace:
                                    if symbol not in symbol_acb_traces:
                                        symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                    fee_amt = _effective_fee_for_trace(tx)
                                    rg_trace = list(symbol_acb_traces[symbol])
                                    gain_sh = gain / abs(qty) if abs(qty) > 1e-6 else 0.0
                                    rg_trace.append(f"# {tx.date} {tx.action} {qty:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt:6.4f} | Proceeds: {effective_proceeds:10.4f} | Cost_Basis: {cost_basis:10.4f} | Gain: {gain:10.4f} | Gain/Sh: {gain_sh:7.4f}")
                                
                                # Apportion the sell tx's commission/fee to this
                                # gain by the closing qty share. Matches the
                                # convention sum-gains uses to roll up
                                # per-trade fees.
                                tx_qty_abs = abs(qty) if abs(qty) > 1e-9 else 1.0
                                fee_share = closing_qty / tx_qty_abs
                                # Tainted dispositions consume phantom shares (pool
                                # has an OPENING_BALANCE that hasn't drained yet).
                                # The 'gain' value is computed against cost=0 and
                                # is bogus by construction. Surface tainted=True
                                # so the report can split them into the
                                # "manual reporting required" section.
                                is_tainted = pool.get('tainted', False)
                                iteration_realized_gains.append({
                                    'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
                                    'date_settle': tx.date_settle or tx.date,
                                    'gain': gain,
                                    'qty': closing_qty, 'cost': cost_basis, 'proceeds': effective_proceeds,
                                    'disallowed': disallowed_amt, 'taxable_gain': gain + disallowed_amt,
                                    'days_held': days_held, 'account': account,
                                    'currency': tx.currency,
                                    'commission': float(tx.commission or 0) * fee_share,
                                    'fee': float(tx.fee or 0) * fee_share,
                                    'direction': 'LONG' if pool['qty'] > 0 else 'SHORT',
                                    'tainted': is_tainted,
                                    'trace': rg_trace
                                })
                                
                                # Tainted losses never feed the superficial-
                                # loss solver: they're computed against a
                                # phantom zero-cost pool and are bogus by
                                # construction. Letting them through spawned
                                # DISALLOW/ADJUST virtual rows whose ACB bump
                                # could land on CLEAN lots after the taint
                                # cleared (drain-to-zero), corrupting clean
                                # gains — and fabricated wash_sales records
                                # flowed into total_disallowed. The tainted
                                # disposition itself is already excluded from
                                # the gains report (manual_reporting_required).
                                if (gain + disallowed_amt) < -0.001 and not is_tainted:
                                    iteration_losses.append({
                                        'tx': tx, 'loss_amount': abs(gain),
                                        'qty': closing_qty, 'direction': 'LONG' if pool['qty'] > 0 else 'SHORT'
                                    })
                            
                            if is_option_assign:
                                underlying = parse_option_underlying(symbol)
                                if underlying:
                                    _pk = (tx.account, underlying)
                                    pending_adjustments[_pk] = pending_adjustments.get(_pk, 0.0) - gain

                            pool['total_cost'] -= D(cost_basis)
                            # Deferred-wash dollars are part of the ACB,
                            # so a partial close releases them in the
                            # same proportion (a full drain releases
                            # all — they were recovered in this gain).
                            _pre_abs = abs(pool['qty'])
                            if _pre_abs > 1e-6:
                                pool['deferred_wash'] = (
                                    pool.get('deferred_wash', 0.0)
                                    * max(0.0, 1.0 - closing_qty
                                          / _pre_abs))
                            pool['qty'] += (closing_qty if pool['qty'] < 0 else -closing_qty)

                            if trace:
                                if symbol not in symbol_acb_traces:
                                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                fee_amt = _effective_fee_for_trace(tx)
                                pool_cost_f = float(pool['total_cost'])
                                pool_acb_sh = pool_cost_f / pool['qty'] if abs(pool['qty']) > 1e-6 else 0.0
                                symbol_acb_traces[symbol].append(f"# {tx.date} {tx.action} {qty:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt:6.4f} | Cost_Rmvd: {cost_basis:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {pool_acb_sh:7.4f}")

                            # Handle leftover if it crosses zero — this is a fresh
                            # position in the opposite direction, so last_acq_date resets.
                            # Apportion any remaining internal_adj (e.g. an option-
                            # premium roll-in) across the new opening, matching
                            # tt_gains.pl:325-330 which apportions chunk_adj per
                            # chunk's qty share.
                            leftover = abs(qty) - closing_qty
                            if leftover > 1e-6:
                                leftover_ratio = leftover / abs(qty)
                                leftover_adj = internal_adj * leftover_ratio
                                eff_cost_leftover = (
                                    abs(tx.net_amount) * leftover_ratio
                                    + (leftover_adj if qty > 0 else -leftover_adj)
                                )
                                pool['qty'] = (leftover if qty > 0 else -leftover)
                                pool['total_cost'] = D(eff_cost_leftover)
                                pool['deferred_wash'] = 0.0
                                pool['last_acq_date'] = tx.date
                                # Position flipped direction (long→short
                                # or vice versa via a cross-zero SELL).
                                # New position begins at this trade.
                                pool['position_start_date'] = tx.date


                                if trace:
                                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                    fee_amt_leftover = _effective_fee_for_trace(tx) * leftover_ratio
                                    pool_cost_f = float(pool['total_cost'])
                                    acb_sh = pool_cost_f / pool['qty'] if abs(pool['qty']) > 1e-6 else 0.0
                                    symbol_acb_traces[symbol].append(f"# {tx.date} {tx.action} {pool['qty']:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt_leftover:6.4f} | Cost_Added: {eff_cost_leftover:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")

                if trace:
                    fee_sh = (tx.commission + tx.fee) / abs(tx.quantity) if abs(tx.quantity) > 1e-6 else 0.0
                    price_fee = tx.price + (fee_sh if tx.quantity > 0 else -fee_sh)
                    pool_cost_f = float(pool['total_cost'])
                    acb_sh = abs(pool_cost_f / pool['qty']) if abs(pool['qty']) > 1e-6 else 0.0
                    realized_pl_str = f"{realized_pl:10.2f}" if realized_pl is not None else " " * 10
                    disallowed_amt_str = f"{disallowed_amt:10.2f}" if disallowed_amt != 0 else " " * 10
                    trace_line = f"# {account:<26} | {note:<12} | {tx.date} {tx.time} | {symbol:<26} | {action:<8} | {qty:10.4f} | {tx.price:10.4f} | {fee_sh:10.4f} | {price_fee:10.4f} | {tx.net_amount:10.2f} | {adjustment_shown:10.2f} | {pool['qty']:11.4f} | {pool_cost_f:10.2f} | {acb_sh:10.4f} | {realized_pl_str} | {disallowed_amt_str} | {trigger_info}"
                    iteration_trace.append(trace_line)

                if abs(pool['qty']) < 1e-6:
                    pool['qty'] = 0.0
                    # Pool drained — clear position_start_date so the
                    # next open seeds a fresh start. Critical for the
                    # "opened, closed, reopened" pattern: the user
                    # wants the date of the SECOND open, not the first.
                    pool['position_start_date'] = None
                    # Phantom shares are fully drained — the pool re-cleans.
                    # Subsequent buys form a fresh, fully-known ACB.
                    if pool.get('tainted', False):
                        pool['tainted'] = False
                        if trace:
                            if symbol not in symbol_acb_traces:
                                symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                            symbol_acb_traces[symbol].append(f"# {tx.date} pool drained to zero — TAINT CLEARED")
                    # Only wipe cost if it's effectively zero (to allow adjustments to persist)
                    if abs(pool['total_cost']) < Decimal('0.001'):
                        pool['total_cost'] = Decimal(0)
                        if trace and symbol in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]

                # Snapshot taxable-pool state after this tx (sheltered txs
                # don't move the pool, so they inherit the prior snapshot).
                # Used by the wash-window renderer for the pool_qty / acb/sh
                # columns. Per-iteration so the converged state wins.
                pool_snapshots_this_iter[tx.id] = {
                    'pool_qty': pool['qty'],
                    'pool_acb': float(pool['total_cost']),
                }

            # --- Detection ---
            found_new_wash_sale = False
            # Running balance per (account, alias) BEFORE each tx —
            # used to split a buy into its COVERING portion (closes a
            # short: not replacement property) and its OPENING portion
            # (acquires/extends: the only §54/§1091-relevant part).
            # FUZZ #B: a short-covering buy was accepted as a LONG
            # loss's trigger, so the +ADJUST landed on a SHORT pool and
            # inflated its opening proceeds — a deferred LOSS became a
            # phantom GAIN with no warning.
            _bal_before: Dict[str, float] = {}
            _running: Dict[tuple, float] = {}
            # Rename chain for ADJUST placement (FUZZ #F8): a wash
            # ADJUST keyed to the trigger's TRADE-TIME symbol landed on
            # a pool the rename had already deleted — the deferred loss
            # stranded on an invisible empty pool and a later sale of
            # the new symbol used the un-bumped basis.
            _renames: Dict[str, tuple] = {}
            for _t in current_tx_list:
                if _t.action == 'SPLIT':
                    _new_sym = (_t.symbol_new or '').strip()
                    if _new_sym and _new_sym != _t.symbol:
                        _renames[_t.symbol] = (
                            get_sort_date(_t), _t.time or '', _new_sym)

            def _symbol_asof(sym: str, d: str, tm: str) -> str:
                seen_syms = set()
                while sym in _renames and sym not in seen_syms:
                    r_date, r_time, r_new = _renames[sym]
                    if (r_date, r_time) <= (d, tm):
                        seen_syms.add(sym)
                        sym = r_new
                    else:
                        break
                return sym
            # Phase-aware ordering (round-six settle-straddle fuzzer):
            # the naive (date, time, id) sort placed a settle-lagged
            # row AFTER a same-date SPLIT, so its pre-split-denominated
            # quantity was applied to an already-scaled balance —
            # phantom shares in the per-account running position, and
            # the cover-vs-opening trigger gate misfired in BOTH
            # directions (spurious denials and masked real triggers).
            # Every other balance walk already orders through the
            # phase ladder; this one must too.
            for _t in sorted(current_tx_list,
                             key=lambda x: (event_sort_key(
                                 x, profile='ca_balance',
                                 date_of=get_sort_date),
                                 x.id or '')):
                # Keyed by (account, RAW symbol): a rename-split
                # scales/moves only the named symbol's shares (in
                # every account — the event is corporate-wide), never
                # target-symbol shares acquired pre-rename.
                _k = (_t.account, _t.symbol)
                _bal_before[_t.id] = _running.get(_k, 0.0)
                if _t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                 'OPENING_BALANCE'):
                    _running[_k] = _running.get(_k, 0.0) + _t.quantity
                elif _t.action == 'SPLIT':
                    _dst_sym = (getattr(_t, 'symbol_new', '')
                                or '').strip() or _t.symbol
                    for _kk in list(_running):
                        if _kk[1] != _t.symbol:
                            continue
                        _moved = _running.pop(_kk) * _t.quantity
                        _dk = (_kk[0], _dst_sym)
                        _running[_dk] = _running.get(_dk, 0.0) + _moved

            def _opening_qty(t, direction: str) -> float:
                """The portion of candidate `t` that OPENS/extends a
                position on the loss's side (vs closing the opposite
                side), from the running balance in t's own account."""
                bal = _bal_before.get(t.id, 0.0)
                q = t.quantity
                if direction == 'LONG':          # candidate is a buy
                    covering = min(q, max(0.0, -bal))
                    return max(0.0, q - covering)
                closing = min(-q, max(0.0, bal))  # candidate is a sell
                return max(0.0, -q - closing)
            # Skip wash-sale detection entirely when disabled. The main loop
            # has already produced realized gains assuming no disallowance,
            # so converging on iteration 1 with no virtual txs is correct.
            iteration_losses_to_check = [] if not detect_wash_sales else iteration_losses
            for loss in iteration_losses_to_check:
                tx = loss['tx']
                # SETTLEMENT-date basis for the whole ±30-day window (CRA's
                # disposition timing; matches get_sort_date and the config's
                # tax_date="settle" default). Mixing trade-date detection
                # with the settle-date balance walk below let a disallowance
                # flip near T+1/T+2 window edges — a rebuy at trade-day +30
                # but settle-day +32 was admitted as a trigger the balance
                # walk then excluded.
                loss_date = datetime.strptime(get_sort_date(tx), '%Y-%m-%d')
                # Bridge SPLIT-renames: a post-rename RGLD.US buy in the
                # 30-day window after an SSL.TO loss is a candidate
                # trigger under CRA's substantially-identical rule. Match
                # on equivalence-class representative instead of raw
                # symbol. `alias_of` is identity for symbols never
                # renamed, so this is a no-op for the common case.
                loss_alias = alias_of(tx.symbol)
                potential_triggers = []
                for t in all_txs:
                    if t.id == tx.id or alias_of(t.symbol) != loss_alias: continue
                    # Only real acquisitions trigger a superficial loss.
                    # SPLIT carries positive `quantity` (the ratio) and
                    # was being misclassified as a long-side buy — a
                    # 16-for-1 reorg on the same ticker post-loss could
                    # silently disallow the entire loss. Same exclusion
                    # for TRANSFER (informational), OPENING_BALANCE
                    # (synthetic), and the bookkeeping actions.
                    if t.action not in ('BUYSELL', 'ASSIGN'):
                        continue
                    t_date = datetime.strptime(get_sort_date(t), '%Y-%m-%d')
                    if abs((t_date - loss_date).days) <= 30:
                        if (loss['direction'] == 'LONG' and t.quantity > 0) or \
                           (loss['direction'] == 'SHORT' and t.quantity < 0):
                            # Only the OPENING portion is replacement
                            # property; a pure cover/close is not a
                            # trigger (FUZZ #B).
                            if _opening_qty(t, loss['direction']) > 1e-6:
                                if getattr(t, 'type', '') \
                                        == 'transfer_rewrite':
                                    raise AmbiguousTransferDateError(
                                        f"{t.symbol}: a TRANSFER-in "
                                        f"dated {t.date} (account "
                                        f"{t.account}, qty "
                                        f"{t.quantity:g}) sits inside "
                                        f"the +/-30-day window of the "
                                        f"{tx.date} loss sale, but its "
                                        f"date is a broker ARRIVAL "
                                        f"date that may not be an "
                                        f"acquisition. If this was a "
                                        f"custody/account move, add a "
                                        f"counter-TRANSFER to a .tt "
                                        f"file in the same account "
                                        f"(TRANSFER {t.date} 09:30:00 "
                                        f"{t.symbol} {-t.quantity:g} "
                                        f"<cur> <price> <total> "
                                        f"DECLARED — the trailing "
                                        f"DECLARED token marks a "
                                        f"deliberate declaration) "
                                        f"so the pair nets out, plus a "
                                        f"BUYSELL at the TRUE original "
                                        f"acquisition date to keep the "
                                        f"balance"
                                        + (f" — or the one-line form: "
                                           f"ACQUIRED <true-date> "
                                           f"09:30:00 {t.symbol} "
                                           f"{t.quantity:.8g} <cur> "
                                           f"<price> <total> ARRIVED "
                                           f"{t.date}"
                                           if t.quantity > 0 else "")
                                        + f"; if it was a genuine "
                                        f"in-kind contribution, record "
                                        f"it as a BUYSELL dated the "
                                        f"contribution day instead.")
                                potential_triggers.append(t)
                if not potential_triggers: continue

                end_window_date = (loss_date + timedelta(days=30)).strftime('%Y-%m-%d')

                # Never compare share quantities denominated at different
                # dates OR different raw symbols: a SPLIT inside the window
                # leaves balances in day-+30 units while loss['qty'] /
                # trigger quantities are in their own trade-date units —
                # min() across them corrupted disallowed_qty (a 1-for-10
                # reverse split shrank a $500 disallowance to $50). Every
                # quantity is therefore normalized to LOSS-DATE units of
                # the LOSS SYMBOL at the point it enters a sum, via a
                # PER-RAW-SYMBOL lineage factor (settle-basis dates; one
                # shared definition in lib/corporate_timeline.py). The old
                # class-wide alias_factor pooled every ratio of the rename
                # class, so a rename-split A->B into a symbol that ALREADY
                # TRADES divided native-B balances — which the event never
                # scaled — by the rename ratio too (2026-09 adversarial
                # audit, finding 1: $2,000 of superficial loss denied as
                # $1,800). Because each row converts independently, the
                # walks no longer need per-symbol fold dicts: SPLIT rows
                # contribute nothing and rename re-keying is implicit in
                # the lineage path.
                loss_sort = get_sort_date(tx)
                # The loss row itself pre-exists loss_sort when it
                # settles later than it trades: the ladder processed it
                # in the pre-existing phase, BEFORE a same-date SPLIT,
                # so loss qty / per-share loss are in pre-split units
                # and the ref side of every conversion must apply a
                # split dated exactly loss_sort (round-four finding 3).
                loss_pre = bool(tx.date and tx.date < loss_sort)

                def _row_loss_units(t, q: float) -> float:
                    # Convert q — denominated in t's RAW symbol at t's
                    # sort date — into loss-date loss-symbol units.
                    # Boundary matches the ca_main phase ladder: rows
                    # that PRE-EXIST their sort date (OPENING_BALANCE,
                    # settle-lagged executions) are scaled by a
                    # same-date SPLIT; rows executed that day are not.
                    d = get_sort_date(t)
                    pre = (t.action == 'OPENING_BALANCE'
                           or bool(t.date and t.date < d))
                    return q * split_timeline.lineage_factor(
                        t.symbol, d, tx.symbol, loss_sort,
                        from_inclusive=pre, ref_inclusive=loss_pre)

                bal_at_end = sum(
                    _row_loss_units(t, t.quantity)
                    for t in current_tx_list
                    if alias_of(t.symbol) == loss_alias
                    and get_sort_date(t) <= end_window_date
                    # OPENING_BALANCE counts — phantom shares are held
                    # (see running_bal_by_tx walk above).
                    and t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                     'OPENING_BALANCE'))

                if (loss['direction'] == 'LONG' and bal_at_end > 1e-6) or \
                   (loss['direction'] == 'SHORT' and bal_at_end < -1e-6):
                    acquired_qty = sum(
                        _row_loss_units(t, _opening_qty(t, loss['direction']))
                        for t in potential_triggers)
                    disallowed_qty = min(loss['qty'], acquired_qty, abs(bal_at_end))
                    disallowed_amt = disallowed_qty * (loss['loss_amount'] / loss['qty'])

                    # --- Allocation across ALL in-window triggers ---
                    # FUZZ #D: routing the ENTIRE disallowance to one
                    # trigger made the answer depend on rebuy ORDER: a
                    # taxable+sheltered window either recovered the
                    # sheltered-attributed portion (tax understated) or
                    # permanently lost the taxable-attributed portion
                    # (overstated). Allocate pro-rata in acquisition
                    # order — post-loss buys first (primary
                    # replacements), then pre-loss buys latest-first —
                    # each capped at its OPENING quantity in loss-date
                    # units. Sheltered/affiliated portions are
                    # PERMANENT (their ADJUST is scoped out of the
                    # taxable pool); taxable portions defer as ACB.
                    post_loss = [t for t in potential_triggers
                                 if get_sort_date(t) > get_sort_date(tx)
                                 or (get_sort_date(t) == get_sort_date(tx)
                                     and t.time > tx.time)]
                    pre_loss = [t for t in potential_triggers
                                if t not in post_loss]
                    ordered = (sorted(post_loss,
                                      key=lambda x: (get_sort_date(x),
                                                     x.time, x.id))
                               + sorted(pre_loss,
                                        key=lambda x: (get_sort_date(x),
                                                       x.time, x.id),
                                        reverse=True))
                    per_share_loss = loss['loss_amount'] / loss['qty']
                    allocations = []      # (trigger, qty, amount)
                    perm_amt = 0.0
                    _rem = disallowed_qty
                    for trg in ordered:
                        cap = _row_loss_units(
                            trg, _opening_qty(trg, loss['direction']))
                        take = min(_rem, cap)
                        if take > 1e-9:
                            amt = take * per_share_loss
                            allocations.append((trg, take, amt))
                            if (trg.id in sheltered_ids
                                    or trg.id in affiliated_ids):
                                perm_amt += amt
                            _rem -= take
                        if _rem <= 1e-9:
                            break

                    # A deferral is only real to the extent TAXABLE
                    # still-held shares back it at the window's end —
                    # s.53(1)(f) bumps the basis of the substituted
                    # property STILL OWNED, and a bump inside a
                    # registered account is moot. Allocating purely by
                    # trigger let a taxable trigger whose own shares
                    # were gone by +30 collect a deferral ADJUST that
                    # parked on an empty pool and never recovered
                    # (found by the conservation fuzzer; the FFH.TO
                    # shape): denied-but-sheltered-backed portions are
                    # PERMANENT, not deferred.
                    _bal_tax = sum(
                        _row_loss_units(t, t.quantity)
                        for t in current_tx_list
                        if alias_of(t.symbol) == loss_alias
                        and get_sort_date(t) <= end_window_date
                        and t.id not in sheltered_ids
                        and t.id not in affiliated_ids
                        and t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                         'OPENING_BALANCE'))
                    _backed = (max(0.0, _bal_tax)
                               if loss['direction'] == 'LONG'
                               else max(0.0, -_bal_tax))
                    _defer_room = min(disallowed_qty, _backed)
                    _trimmed = []
                    for trg, take, amt in allocations:
                        if (trg.id in sheltered_ids
                                or trg.id in affiliated_ids):
                            _trimmed.append((trg, take, amt))
                            continue
                        _keep = min(take, max(0.0, _defer_room))
                        _defer_room -= _keep
                        if _keep > 1e-9:
                            _trimmed.append(
                                (trg, _keep, _keep * per_share_loss))
                        _excess = take - _keep
                        if _excess > 1e-9:
                            perm_amt += _excess * per_share_loss
                    allocations = _trimmed

                    # Route each kept (deferred) bump onto a pool that
                    # actually HOLDS substituted property at +30. The
                    # class-level _backed test above can be satisfied
                    # entirely by shares in a SIBLING pool of the alias
                    # class (loss realized on S0 while the still-held
                    # backing sits in S2, pre-merge): landing the
                    # ADJUST on the trigger's own — possibly empty —
                    # pool parked the deferral where nothing would ever
                    # consume it, and the denied loss silently left
                    # conservation (fuzz seed 2183, 2026-09 round-four
                    # audit). Greedy: a trigger whose own pool holds
                    # the deferred quantity keeps its pool; otherwise
                    # the bump lands on the class pool with the most
                    # remaining still-held backing.
                    _pool_back: Dict[str, float] = {}
                    _pool_repr: Dict[str, str] = {}
                    for t in current_tx_list:
                        if (alias_of(t.symbol) == loss_alias
                                and get_sort_date(t) <= end_window_date
                                and t.id not in sheltered_ids
                                and t.id not in affiliated_ids
                                and t.action in ('BUYSELL', 'ASSIGN',
                                                 'TRANSFER',
                                                 'OPENING_BALANCE')):
                            _k = _symbol_asof(t.symbol,
                                              end_window_date,
                                              '23:59:59')
                            _pool_back[_k] = (_pool_back.get(_k, 0.0)
                                              + _row_loss_units(
                                                  t, t.quantity))
                            _pool_repr.setdefault(_k, t.symbol)
                    _lsign = (1.0 if loss['direction'] == 'LONG'
                              else -1.0)
                    _back_left = {k: max(0.0, _lsign * v)
                                  for k, v in _pool_back.items()}
                    _adjust_land: Dict[str, str] = {}
                    for trg, take, _amt in allocations:
                        if (trg.id in sheltered_ids
                                or trg.id in affiliated_ids):
                            continue      # moot pools; leave in place
                        _own = _symbol_asof(trg.symbol,
                                            end_window_date, '23:59:59')
                        if _back_left.get(_own, 0.0) >= take - 1e-9:
                            _back_left[_own] -= take
                            continue      # own pool holds the backing
                        _best = max(_back_left,
                                    key=lambda k: _back_left[k],
                                    default=None)
                        if (_best is not None
                                and _back_left.get(_best, 0.0) > 1e-9):
                            _back_left[_best] -= take
                            _adjust_land[trg.id] = _pool_repr[_best]
                        # No positive pool anywhere: keep the default
                        # landing — _defer_room already converted the
                        # unbacked portion to a permanent denial.

                    def _mk_adjust(trg, amt):
                        a_id = f"WASH_{tx.id}__{trg.id}"
                        if get_sort_date(trg) < get_sort_date(tx) or \
                           (get_sort_date(trg) == get_sort_date(tx)
                                and trg.time <= tx.time):
                            a_date, a_settle = tx.date, tx.date_settle
                            # Loader accepts time='' — don't crash the
                            # engine on it (2026-09 audit).
                            a_time = (datetime.strptime(
                                          tx.time or '09:30:00',
                                          '%H:%M:%S')
                                      + timedelta(seconds=1)
                                      ).strftime('%H:%M:%S')
                            if a_time == '00:00:00':
                                a_time = '23:59:59'
                        else:
                            a_date, a_settle = trg.date, trg.date_settle
                            a_time = trg.time
                        # Sign follows the loss side (post-FUZZ-#B every
                        # trigger OPENS on that side, so the proxy is
                        # sound); symbol keyed to the trigger's pool AS
                        # OF the adjust date — following renames so the
                        # bump lands on the LIVE pool (FUZZ #F8).
                        a_amt = amt if loss['direction'] == 'LONG' else -amt
                        # Backing-routed landing (see _adjust_land):
                        # the pool that still holds the substituted
                        # property, named as of the adjust date so the
                        # bump enters it while live and rides any later
                        # rename with the pool's own state.
                        a_sym = _symbol_asof(
                            _adjust_land.get(trg.id, trg.symbol),
                            a_date, a_time)
                        v = TaxTransaction(action='ADJUST', date=a_date,
                                           time=a_time, symbol=a_sym,
                                           currency=tx.currency,
                                           net_amount=a_amt,
                                           account=trg.account, id=a_id,
                                           date_settle=a_settle)
                        if trg.id in sheltered_ids:
                            sheltered_ids.add(a_id)
                        if trg.id in affiliated_ids:
                            affiliated_ids.add(a_id)
                        adjust_to_trigger[a_id] = trg.id
                        return v

                    if allocations:
                        loss_to_trigger[tx.id] = allocations[0][0].id
                        loss_to_triggers_multi[tx.id] = [
                            trg.id for trg, _q, _a in allocations]

                    # Check if we already have a disallowance for this loss. If
                    # so, update it if the amount has changed (due to basis
                    # adjustments from a previous iteration). This fixes the
                    # "solver stale-mate" where DISALLOW amounts would stay
                    # stuck at their first-iteration values even as the
                    # underlying realized loss drifted.
                    existing_disallow = next((v for v in final_virtual_txs if v.id == tx.id and v.action == 'DISALLOW'), None)
                    if existing_disallow:
                        if abs(float(existing_disallow.net_amount) - float(disallowed_amt)) < 0.001:
                            continue
                        existing_disallow.net_amount = disallowed_amt
                        existing_disallow.quantity = disallowed_qty
                        # Update the corresponding ADJUST vtx as well.
                        # FULL loss-tx id: 8-hex prefixes collided across losses, so a
                        # solver re-iteration could update the WRONG ADJUST and a
                        # report row could attach another loss's adjustment.
                        adj_id_prefix = f"WASH_{tx.id}__"
                        _new_amts = {f"WASH_{tx.id}__{trg.id}":
                                     (amt if loss['direction'] == 'LONG'
                                      else -amt)
                                     for trg, _q, amt in allocations}
                        _seen_adj = set()
                        for v in final_virtual_txs:
                            if v.action == 'ADJUST' and v.id.startswith(adj_id_prefix):
                                v.net_amount = _new_amts.get(v.id, 0.0)
                                _seen_adj.add(v.id)
                        for trg, _q, amt in allocations:
                            a_id = f"WASH_{tx.id}__{trg.id}"
                            if a_id not in _seen_adj:
                                final_virtual_txs.append(_mk_adjust(trg, amt))
                        permanent_by_loss[tx.id] = perm_amt
                        found_new_wash_sale = True
                        continue

                    disallow_vtx = TaxTransaction(action='DISALLOW', date=tx.date, time=tx.time, symbol=tx.symbol, currency=tx.currency, net_amount=disallowed_amt, quantity=disallowed_qty, id=tx.id, date_settle=tx.date_settle)

                    adjust_vtxs = [_mk_adjust(trg, amt)
                                   for trg, _q, amt in allocations]
                    permanent_by_loss[tx.id] = perm_amt

                    # Capture the full ±30 day window listing for the trace.
                    # Includes every same-symbol transaction across all
                    # accounts (taxable + sheltered) so the reader can see
                    # which buys were eligible candidates and which were
                    # actually selected as the ACB-bump anchor.
                    window_start_dt = loss_date - timedelta(days=30)
                    window_end_dt = loss_date + timedelta(days=30)
                    win_txs = []
                    seen = set()
                    for t in all_txs:
                        if t.id in seen:
                            continue
                        seen.add(t.id)
                        if t.symbol != tx.symbol:
                            continue
                        if t.action in ('DISALLOW', 'ADJUST', 'DIVIDEND', 'TAX', 'INTEREST', 'FEE'):
                            continue
                        try:
                            t_dt = datetime.strptime(t.date, '%Y-%m-%d')
                        except ValueError:
                            continue
                        if not (window_start_dt <= t_dt <= window_end_dt):
                            continue
                        days_from = (t_dt - loss_date).days
                        if t.id == tx.id:
                            role = 'loss_sale'
                        elif t.id in loss_to_triggers_multi.get(tx.id, []):
                            role = 'trigger'
                        elif t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER') and (
                            (loss['direction'] == 'LONG' and t.quantity > 0) or
                            (loss['direction'] == 'SHORT' and t.quantity < 0)
                        ):
                            role = 'candidate'
                        elif t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER') and (
                            (loss['direction'] == 'LONG' and t.quantity < 0) or
                            (loss['direction'] == 'SHORT' and t.quantity > 0)
                        ):
                            role = 'other_sell' if loss['direction'] == 'LONG' else 'other_buy'
                        else:
                            role = 'context'
                        win_txs.append({
                            'tx_id': t.id,
                            'date': t.date,
                            'days_from_loss': days_from,
                            'account': t.account,
                            'action': t.action,
                            'qty': float(t.quantity),
                            'price': float(t.price),
                            'sheltered': t.id in sheltered_ids,
                            'affiliated': t.id in affiliated_ids,
                            'role': role,
                            'running_bal': running_bal_by_tx.get(t.id),
                        })
                    win_txs.sort(key=lambda r: (r['days_from_loss'], r['date'], r['account']))
                    wash_windows[tx.id] = {
                        'window_start': window_start_dt.strftime('%Y-%m-%d'),
                        'window_end': end_window_date,
                        'loss_date': tx.date,
                        'loss_direction': loss['direction'],
                        'bal_at_end': bal_at_end,
                        'loss_qty': loss['qty'],
                        'disallowed_qty': disallowed_qty,
                        'transactions': win_txs,
                    }
                    final_virtual_txs.extend([disallow_vtx] + adjust_vtxs)
                    found_new_wash_sale = True

            if detect_wash_sales:
                # Retract STALE disallowances: basis adjustments from a
                # later iteration can turn a first-iteration loss into a
                # raw gain, but its DISALLOW was still attached
                # unconditionally by id — the final entry then reported
                # taxable = raw gain + phantom disallowance, and
                # wash_sales listed a "superficial loss" on a
                # disposition that is not a loss. Remove the DISALLOW
                # and its ADJUSTs and re-iterate; a pathological
                # oscillation ends at the iteration cap with the loud
                # non-convergence warning rather than a silent wrong
                # number.
                _gain_by_id: Dict[str, float] = {}
                for _rg in iteration_realized_gains:
                    _gain_by_id[_rg['tx_id']] = (
                        _gain_by_id.get(_rg['tx_id'], 0.0) + _rg['gain'])
                for _v in [v for v in final_virtual_txs
                           if v.action == 'DISALLOW'
                           and _gain_by_id.get(v.id, 0.0) >= -0.001]:
                    _pref = f"WASH_{_v.id}__"
                    final_virtual_txs[:] = [
                        x for x in final_virtual_txs
                        if x is not _v
                        and not (x.action == 'ADJUST'
                                 and x.id.startswith(_pref))]
                    permanent_by_loss.pop(_v.id, None)
                    loss_to_trigger.pop(_v.id, None)
                    loss_to_triggers_multi.pop(_v.id, None)
                    wash_windows.pop(_v.id, None)
                    found_new_wash_sale = True

            if not found_new_wash_sale:
                final_realized_gains = iteration_realized_gains
                final_global_pools = global_pools
                final_pool_snapshots = pool_snapshots_this_iter

                # Backfill wash-window rows with the final, converged pool
                # state. Earlier iterations may have written different pool
                # values (before later ADJUSTs landed), so this overwrite is
                # important for accuracy.
                for ww in wash_windows.values():
                    for entry in ww.get('transactions', []):
                        snap = final_pool_snapshots.get(entry.get('tx_id', ''))
                        if snap:
                            entry['pool_qty_after'] = snap['pool_qty']
                            entry['pool_acb_after'] = snap['pool_acb']
                            if abs(snap['pool_qty']) > 1e-6:
                                entry['acb_per_share_after'] = snap['pool_acb'] / snap['pool_qty']
                            else:
                                entry['acb_per_share_after'] = 0.0
                final_wash_sales = []
                for v in final_virtual_txs:
                    if v.action == 'DISALLOW':
                        # ALL of this loss's ADJUST vtxs (a multi-trigger
                        # allocation emits several; solver updates can
                        # leave zeroed stubs). `next()` took the first —
                        # possibly a zeroed one — so adjust_cmd disagreed
                        # with disallow_cmd and replaying the pair
                        # stranded the difference. Present the poolable
                        # (taxable-deferral) total; the permanent/
                        # sheltered share has no ADJUST by design.
                        _adjs = [a for a in final_virtual_txs
                                 if a.action == 'ADJUST'
                                 and a.id.startswith(f"WASH_{v.id}__")
                                 and abs(a.net_amount) > 1e-9
                                 # sheltered/affiliated allocations DO
                                 # get ADJUST vtxs (their ids join
                                 # sheltered_ids/affiliated_ids at
                                 # creation) but that share is a
                                 # permanent denial — presenting it as
                                 # poolable would bump next-year
                                 # taxable ACB by money that never
                                 # defers (re-audit).
                                 and a.id not in sheltered_ids
                                 and a.id not in affiliated_ids]
                        adj = _adjs[0] if _adjs else None
                        adj_total = sum(a.net_amount for a in _adjs)
                        
                        # Gather the trace for this specific wash sale
                        wash_trace = []
                        if trace and v.symbol in symbol_acb_traces:
                            # Add a bit of context: +/- 30 days around the wash sale
                            try:
                                loss_dt = datetime.strptime(v.date, '%Y-%m-%d')
                                window_start = (loss_dt - timedelta(days=30)).strftime('%Y-%m-%d')
                                window_end = (loss_dt + timedelta(days=30)).strftime('%Y-%m-%d')
                                
                                wash_trace.append(f"# --- (D-30 Window Start: {window_start}) ---")
                                # Since symbol_acb_traces[v.symbol] gets reset to just the last segment when pool reaches 0,
                                # we need to trace over iteration_trace to get the full chronological context.
                                for line in iteration_trace:
                                    # Example line: # margin | NOTE | 2024-12-19 ...
                                    match = re.search(r'\|\s+(\d{4}-\d{2}-\d{2})', line)
                                    if match:
                                        line_date = match.group(1)
                                        # Also ensure the symbol matches
                                        if f"| {v.symbol}" in line:
                                            if window_start <= line_date <= window_end:
                                                wash_trace.append(line)
                                wash_trace.append(f"# --- (D+30 Window End: {window_end}) ---")
                                wash_trace.append("# ")
                            except (ValueError, TypeError) as e:
                                # Narrowed from a bare `except Exception:
                                # pass`. The trace context is best-effort
                                # — if a virtual ADJUST row has a
                                # malformed date, we still want the
                                # disallow/adjust output to print. Log
                                # so an unexpected error doesn't
                                # disappear: a missing trace block is
                                # otherwise hard to diagnose.
                                print(f"warning: skipped wash-trace "
                                      f"context for {v.symbol}@{v.date} "
                                      f"({e})", file=sys.stderr)
                        
                        disallow_cmd = f"DISALLOW {v.date} {v.time} {v.symbol} {v.currency} {v.net_amount:.4f}"
                        adjust_cmd = f"ADJUST {adj.date} {adj.time} {adj.symbol} {adj.currency} {adj_total:.4f}" if adj else ""
                        
                        if trace:
                            wash_trace.append(disallow_cmd)
                            if adjust_cmd: wash_trace.append(adjust_cmd)
                        
                        final_wash_sales.append({
                            'loss_tx': v.to_dict(),
                            'loss_tx_id': v.id,
                            'trigger_lot_id': loss_to_trigger.get(v.id, ''),
                            'amount': v.net_amount,
                            'disallowed_amount': v.net_amount,
                            'disallowed_qty': v.quantity,
                            'disallow_cmd': disallow_cmd,
                            'adjust_cmd': adjust_cmd,
                            'trace': wash_trace if trace else []
                        })
                solver_converged = True
                break
            # Otherwise (a wash was found or an existing one was updated this
            # iteration) loop again: DISALLOW/ADJUST vtxs were already added to
            # final_virtual_txs inline, so the next pass recomputes against
            # them until the amounts stop changing.

        # If the solver ran out without converging, the last iteration's
        # state never got promoted to the `final_*` vars. Use it as a
        # best-effort fallback and emit a stderr warning so the caller
        # knows the result may be inconsistent.
        if not solver_converged:
            print(
                f"warning: CRA wash-sale solver did not converge within "
                f"{solver_iterations_used} iterations — the wash-sale list "
                f"may be incomplete and ACB pools may be inconsistent. "
                f"Check for unusual same-symbol activity in your input.",
                file=sys.stderr,
            )
            # Fall back to the last iteration's state.
            if not final_realized_gains:
                final_realized_gains = iteration_realized_gains
                final_global_pools = global_pools
        
        # Format results
        processed_gains = []
        by_ticker = {}
        for g in final_realized_gains:
            # Signed cash-flow convention for SHORT positions: a sell-to-open
            # is a cash inflow (negative cost) and a buy-to-close is a cash
            # outflow (negative proceeds). This matches tt_gains.pl:401-405
            # so that consumers like taxjson_sum_gains.py can compute
            # `proceeds - cost = signed gain` without direction-aware logic,
            # and so taxjson_ccd_gains.py can filter shorts by `cost < 0`.
            entry_cost = g['cost']
            entry_proceeds = g['proceeds']
            if g['direction'] == 'SHORT':
                entry_cost = -entry_cost
                entry_proceeds = -entry_proceeds
            # Cross-engine schema: emit the same keys the US engine emits so
            # downstream consumers (sum-gains, explain, diff) can rely on
            # field presence without per-engine branching. Canada-specific
            # fields use None / 0.0 / [] for "concept doesn't apply here":
            #   - term: Canadian tax has no ST/LT distinction.
            #   - permanently_disallowed: ITA 54 is always deferred via ACB
            #     bump, never permanent.
            #   - wash_replacements: US-specific shape (basis_bump per rep);
            #     Canada uses wash_trigger + wash_window instead.
            tx_id_full = g['id' if 'id' in g else 'tx_id']
            trigger_id = loss_to_trigger.get(tx_id_full, '')
            gain_entry = {
                'date': g['date'],
                'date_settle': g.get('date_settle', g['date']),
                'symbol': g['symbol'], 'qty': g['qty'],
                'gain': g['taxable_gain'], 'raw_gain': g['gain'],
                'cost': entry_cost, 'proceeds': entry_proceeds,
                'currency': g.get('currency') or '',
                'commission': g.get('commission', 0.0),
                'fee': g.get('fee', 0.0),
                'disallowed_amount': g['disallowed'], 'is_wash_sale': g['disallowed'] > 0.001,
                # Sheltered/affiliated-allocated portion of the denial —
                # lost for good (was hardcoded 0.0: FUZZ #D/#16 audit
                # hole; wash-sales/form-export described a deferral that
                # never existed).
                'permanently_disallowed': round(
                    permanent_by_loss.get(tx_id_full, 0.0), 6)
                if g['disallowed'] > 0.001 else 0.0,
                'replacement_lot_ids': loss_to_triggers_multi.get(
                    tx_id_full, [trigger_id] if trigger_id else []),
                'is_option': is_option_symbol(g['symbol'] or ''),
                'days_held': g['days_held'], 'account': g['account'], 'id': tx_id_full,
                'direction': g['direction'],
                'term': None,
                'wash_replacements': None,
                'tainted': g.get('tainted', False),
            }
            if 'trace' in g:
                gain_entry['trace'] = g['trace']

            # Attach the wash-sale trigger info inline so the trace can
            # explain *why* the loss was disallowed alongside the disposition
            # itself, rather than punting to a separate section. Includes the
            # trigger buy and the ACB-adjust virtual transaction.
            if g['disallowed'] > 0.001:
                trigger_id = loss_to_trigger.get(g['tx_id'])
                trigger_tx = None
                if trigger_id:
                    trigger_tx = next((t for t in all_txs if t.id == trigger_id), None)
                adj_vtx = next(
                    (v for v in final_virtual_txs
                     if v.action == 'ADJUST' and v.id.startswith(f"WASH_{g['tx_id']}__")),
                    None,
                )
                wash_trigger: Dict[str, Any] = {
                    'is_full_disallowance': abs(g['taxable_gain']) < 0.01,
                    'loss_raw_gain': g['gain'],
                    'disallowed_amount': g['disallowed'],
                }
                if trigger_tx is not None:
                    wash_trigger['trigger_tx_id'] = trigger_id
                    wash_trigger['trigger_date'] = trigger_tx.date
                    wash_trigger['trigger_qty'] = trigger_tx.quantity
                    wash_trigger['trigger_price'] = trigger_tx.price
                    wash_trigger['trigger_account'] = trigger_tx.account
                    wash_trigger['trigger_sheltered'] = trigger_id in sheltered_ids
                    wash_trigger['trigger_affiliated'] = trigger_id in affiliated_ids
                if adj_vtx is not None:
                    wash_trigger['adjust_amount'] = adj_vtx.net_amount
                    wash_trigger['adjust_date'] = adj_vtx.date
                gain_entry['wash_trigger'] = wash_trigger
                if g['tx_id'] in wash_windows:
                    gain_entry['wash_window'] = wash_windows[g['tx_id']]

            processed_gains.append(gain_entry)
            # Skip tainted entries from by_ticker totals. They carry
            # fabricated numbers (cost basis = 0 against a phantom
            # OPENING_BALANCE) and the CLI will route them out of
            # `transactions` into `manual_reporting_required` later.
            # Without this skip, any downstream consumer reading
            # by_ticker (rather than transactions) sees phantom gains
            # mixed with real ones. The --year-rebuild path in
            # taxjson_gains.py has the same guard; this is the
            # primary-construction parallel.
            if gain_entry.get('tainted'):
                continue
            s = gain_entry['symbol']
            if s not in by_ticker:
                by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
            stats = by_ticker[s]
            stats['total_cost'] += gain_entry['cost']
            stats['total_proceeds'] += gain_entry['proceeds']
            stats['total_gain'] += gain_entry['gain']
            stats['trade_count'] += 1
            stats['hold_days'].append(gain_entry['days_held'])

        for tx in transactions:
            # PIL goes to a dedicated entry below so sum-gains can show it
            # in its own column. Keep it out of the eligible-dividend total
            # so T5/Schedule B reporting stays accurate.
            if tx.action == 'DIVIDEND_IN_LIEU' or tx.type == 'dividend_in_lieu':
                pil_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
                pil_entry = {
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'time': tx.time, 'symbol': tx.symbol, 'qty': tx.quantity, 'currency': tx.currency,
                    'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0, 'pil': pil_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND_IN_LIEU'
                }
                processed_gains.append(pil_entry)
                s = pil_entry['symbol']
                if s not in by_ticker:
                    by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
                by_ticker[s]['total_pil'] += pil_entry['pil']
                continue

            if (tx.action == 'DIVIDEND' or tx.type == 'dividend') \
               and tx.action != 'DIVIDEND_IN_LIEU':
                # Report the gross dividend (pre-withholding) — that's what
                # T5/T3/1099-DIV expect on the income line. The withheld
                # foreign tax flows separately as TAX records and feeds the
                # foreign tax credit, not the dividend total.
                # Convention: parsers set gross_amount as the pre-withholding
                # dividend (e.g. RBC computes net/0.85 when description shows
                # NON-RES TAX WITHHELD); they leave it at the dataclass
                # default 0.0 when only net is known. We use gross if it's
                # truthy (positive — actual dividends are always > 0), else
                # net. Treating 0 and unset identically is intentional here:
                # a 0-gross "dividend" is contradictory data — no real
                # parser emits gross=0 + net>0 for a real dividend, and a
                # return-of-capital event should use action='ADJUST', not
                # DIVIDEND. If you ever need to distinguish "explicit zero"
                # from "missing", introduce a sentinel field on
                # TaxTransaction rather than changing this check.
                div_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
                div_entry = {
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'time': tx.time, 'symbol': tx.symbol, 'qty': tx.quantity, 'currency': tx.currency,
                    'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0, 'dividend': div_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND'
                }
                processed_gains.append(div_entry)
                s = div_entry['symbol']
                if s not in by_ticker:
                    by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
                by_ticker[s]['total_div'] += div_entry['dividend']

        # Invariant: every dollar of disallowed loss reported in wash_sales should
        # also appear as the (taxable_gain - raw_gain) bump on the corresponding
        # gain entry. If they diverge, the engine has lost track of a wash sale.
        sum_taxable = sum(g['taxable_gain'] for g in final_realized_gains)
        sum_raw = sum(g['gain'] for g in final_realized_gains)
        sum_disallowed_on_gains = sum(g['disallowed'] for g in final_realized_gains)
        sum_disallowed_on_washes = sum(w['amount'] for w in final_wash_sales)
        if abs((sum_taxable - sum_raw) - sum_disallowed_on_gains) > 0.01:
            print(
                f"warning: gain disallowance invariant broken — "
                f"taxable-raw={sum_taxable - sum_raw:.4f} vs gains-disallowed={sum_disallowed_on_gains:.4f}",
                file=sys.stderr,
            )
        if abs(sum_disallowed_on_gains - sum_disallowed_on_washes) > 0.01:
            print(
                f"warning: wash-sale disallowance invariant broken — "
                f"gains-disallowed={sum_disallowed_on_gains:.4f} vs wash-sales={sum_disallowed_on_washes:.4f}",
                file=sys.stderr,
            )
        _warn_undrained_adjustments(pending_adjustments, "canada")

        # Warn-only option-as-replacement scan (user policy; numbers are
        # never changed — gated on cross_asset until enforcement is
        # decided). Losses on the settle basis, matching the engine's
        # superficial-loss window convention.
        option_replacement_warnings: List[Dict[str, Any]] = []
        if cross_asset:
            _orw_losses = [
                {'symbol': g['symbol'],
                 'date': g.get('date_settle') or g['date'],
                 'amount': g['gain'],
                 'id': g.get('tx_id', ''),
                 'direction': g.get('direction', 'LONG')}
                for g in final_realized_gains
                if g['gain'] < -0.005 and not g.get('tainted')]
            option_replacement_warnings = detect_option_replacement_matches(
                _orw_losses, all_txs,
                date_of=get_sort_date,
                canonical=split_timeline.canonical,
                statute_label="CRA s.54 ('a right to acquire')",
                check_held_at_end=True)
            _emit_option_replacement_stderr(option_replacement_warnings)

        # Conservation post-conditions (see the helpers' docstrings).
        _pool_qty: Dict[str, float] = {}
        _seen_pool_objs: set = set()
        for _s, _p in final_global_pools.items():
            if id(_p) in _seen_pool_objs:
                continue                # rename aliases share one object
            _seen_pool_objs.add(id(_p))
            _pool_qty[_s] = _p['qty']
        _verify_share_conservation(
            [t for t in current_tx_list
             if t.id in taxable_ids
             and t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                              'SPLIT')],
            _pool_qty, 'canada',
            zero_ratio_skips=False)     # CA applies a ratio-0 SPLIT blindly
        _warn_stranded_basis(final_global_pools)

        return {
            'transactions': processed_gains,
            'by_ticker': by_ticker,
            'wash_sales': final_wash_sales,
            'option_replacement_warnings': option_replacement_warnings,
            'inventory': [
                {
                    'symbol': s,
                    'qty': p['qty'],
                    # Short pools hold the opening proceeds positive
                    # internally, but the repo-wide inventory convention
                    # (holdings export, `list`, USA engine) is NEGATIVE
                    # total_cost for shorts — proceeds credited, so
                    # cost_per_share = total_cost/qty is positive.
                    # FUZZ #21: this used to leak the internal positive
                    # sign, disagreeing with the USA engine and flipping
                    # COST vs COST/SH signs in `list`.
                    'total_cost': (float(p['total_cost'])
                                   if p['qty'] >= 0
                                   else -float(p['total_cost'])),
                    'currency': p.get('currency', ''),
                    # Earliest open of the current run of holding this
                    # symbol. None for pools that drained and didn't
                    # reopen (those don't survive the qty filter
                    # anyway). Pre-data-window pools use the synthetic
                    # OPENING_BALANCE date — approximate but the best
                    # signal available.
                    'position_start_date': p.get('position_start_date'),
                    # Most recent acquisition (any buy, incl. adds to an
                    # old position) — the date the superficial-loss /
                    # wash 30-day window measures from. None when the
                    # pool never saw an in-window buy (sentinel).
                    'last_acq_date': (None
                                      if p.get('last_acq_date')
                                      in (None, '1970-01-01')
                                      else p.get('last_acq_date')),
                    # Denied superficial losses still parked in this
                    # pool's ACB (deferred; recovered on a clean sale).
                    'deferred_wash': round(
                        float(p.get('deferred_wash', 0.0)), 4),
                }
                for s, p in final_global_pools.items() if abs(p['qty']) > 1e-6
            ],
            'summary': {
                'total_gain': sum_taxable,
                'total_disallowed': sum_disallowed_on_washes,
                'wash_solver_converged': solver_converged,
                'wash_solver_iterations': solver_iterations_used,
            }
        }

class USATaxRules(TaxRules):
    """US capital-gains engine: FIFO matching for long AND short positions
    with IRC §1091 wash-sale handling on both sides.

    Long positions:
    - FIFO sell-to-close matching, gain = proceeds - basis.
    - §1091: disallowed loss adds to basis of replacement BUY (deferred).
    - §1223(3): replacement inherits loss lot's holding-period start.
    - Rev. Rul. 2008-5: replacement in a sheltered account → *permanent*
      disallowance with no basis transfer.

    Short positions:
    - Sell-to-open creates a short lot (qty owed, proceeds received).
    - Buy-to-close pops short lots FIFO. Gain = opening_proceeds - closing_cost.
    - Holding period: stand-alone shorts are SHORT_TERM (the holding
      period of borrowed property is conventionally zero — see §1222).
    - §1091 extends to short sales (Reg §1.1091-1): a loss on closing a
      short followed by a substantially-identical sell-to-open within
      ±30 days disallows the loss and REDUCES the replacement short's
      effective opening proceeds (the short-side analog of basis bump).
    - Sheltered replacement short → permanent disallowance.

    Position flips:
    - A BUY exceeding short inventory closes shorts FIFO; leftover qty
      opens a new long lot.
    - A SELL exceeding long inventory closes longs FIFO; leftover qty
      opens a new short lot.

    Out of scope (v1) — to be added when a use case exists:
    - §1233(b)(1) anti-conversion: long held >1yr + same-symbol short →
      gain on short = SHORT_TERM, loss = LONG_TERM. (Detection requires
      "substantially identical" reasoning beyond simple symbol match.)
    - §1233(b)(2) long holding-period reset when short opens on a long
      held ≤1 year of substantially identical property.
    - §1259 constructive sale of appreciated long when hedged by short.
    - Section 1256 60/40 mark-to-market for futures and broad-based
      index options. (Affects symbols like SPX, NDX, futures.)
    """

    WASH_WINDOW_DAYS = 30

    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True, per_account_basis: bool = False) -> Dict[str, Any]:
        # §1091 contemplates a narrower "related party" rule than CRA's
        # affiliated-persons test, but the mechanics are the same: an
        # affiliated person's BUY/SELL is treated as a replacement for
        # wash-sale purposes. We additively merge both pools — passing
        # neither flag yields the baseline single-taxpayer behavior.
        #
        # One corporate split = one application: collapse per-account SPLIT
        # duplicates across all three lists (shared `seen`) before the
        # symbol-keyed inventory/replacement walks see them.
        _seen_splits: set = set()
        transactions = _dedupe_corporate_splits(transactions, _seen_splits)
        sheltered_transactions = _dedupe_corporate_splits(
            sheltered_transactions or [], _seen_splits)
        affiliated_transactions = _dedupe_corporate_splits(
            affiliated_transactions or [], _seen_splits)

        sheltered_ids = {t.id for t in (sheltered_transactions or [])}
        affiliated_ids = {t.id for t in (affiliated_transactions or [])}
        epsilon = 1e-8

        def get_sort_date(tx):
            # TRADE date, deliberately NOT settlement: the IRS recognizes
            # capital transactions on the trade date — acquisition order for
            # FIFO, the §1091 61-day window, and the holding period all run
            # on trade dates. Sorting by settlement consumed the WRONG lot
            # whenever settlement lags differed between two lots of one
            # symbol (verified ±$500 on a two-lot scenario), and it also
            # created a settle-date collision class with same-day SPLITs
            # that trade-date ordering never has. (The Canada engine keeps
            # its settle basis — CRA disposition timing.)
            return tx.date

        def non_capital(action: str, tx_type: str) -> bool:
            # TRANSFER is handled at the CLI layer (rewritten to BUYSELL
            # for sheltered files, hard-errors out for --taxable).
            return action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER') \
                   or tx_type in ('dividend', 'dividend_in_lieu', 'tax', 'interest', 'fee')

        # ASSIGN must be processed before BUYSELL when they share a settle
        # date+time — the option-leg ASSIGN sets up a pending adjustment
        # that the stock-leg BUYSELL then consumes.
        # Ordering centralized in lib/corporate_timeline.event_sort_key,
        # profile 'us_main': (trade date, time, priority) — priority sorts
        # AFTER time here, a deliberate divergence from Canada's phase
        # ladder, pinned in test_event_sort_key.py.

        # Use the module-level OCC parser. Local binding kept for
        # backward compatibility with this scope's existing callers.
        option_underlying = parse_option_underlying

        # Combined timeline so future sheltered openings can match against
        # earlier taxable losses — sheltered matches result in permanent
        # disallowance (Rev. Rul. 2008-5).
        all_events = sorted(
            transactions + (sheltered_transactions or []) + (affiliated_transactions or []),
            key=lambda x: event_sort_key(x, profile='us_main',
                                         date_of=get_sort_date),
        )

        # === PRE-PASS: classify each event and build replacement indexes. ===
        # The "opening portion" of each transaction is what's eligible to be
        # a wash-sale replacement. A flip transaction (closes one side, opens
        # the other) contributes only its opening portion.
        long_replacements: Dict[str, List[Dict[str, Any]]] = {}
        short_replacements: Dict[str, List[Dict[str, Any]]] = {}

        def _rep_key(sym: str) -> str:
            """Replacement records are keyed by the rename-chain
            CANONICAL symbol (FUZZ #C): keying by raw symbol made a
            pre-rename loss look up 'OLD.TO' while the post-rename
            rebuy sat under 'NEW.TO' — §1091 silently missed across
            mergers (the SPLIT-time migration ran too late for losses
            processed before the SPLIT row). Canada already
            canonicalizes via alias_of; this is the USA twin. Unit
            conversion stays with _rep_units_factor."""
            return split_timeline.canonical(sym)
        net_qty_state: Dict[Any, float] = {}

        def _nkey(acct, s):
            # Blended mode: the taxable open/cover partition is per
            # ACCOUNT (an account's sell closes ITS position), matching
            # the per-(account, symbol) FIFO pools in the main pass.
            return (acct, s) if per_account_basis else s
        # Context (sheltered/affiliated) books' running balances, keyed
        # (account, symbol) — partitions an other-scope SELL into its
        # long-close vs short-open portions, mirroring the taxable
        # partition. BUY registration deliberately stays full-quantity
        # (Rev. Rul. 2008-5: any sheltered acquisition is replacement-
        # eligible); only the SELL side consults this.
        other_qty_state: Dict[tuple, float] = {}
        # Per-symbol split timeline (TRADE-basis dates, IRS semantics), ALL
        # scopes — a split is a property of the security, and
        # _dedupe_corporate_splits at entry guarantees one row per event.
        # Replacement records stay denominated in their own trade-date units
        # forever; _rep_units_factor converts between a rep's units and the
        # loss-date's units at match time. Built in all_events order (rename
        # migration is positional); the non_capital filter mirrors this
        # pre-pass loop's own skip.
        split_timeline = SplitTimeline.from_transactions(
            ev for ev in all_events if not non_capital(ev.action, ev.type))

        for ev in all_events:
            if non_capital(ev.action, ev.type):
                continue
            sym = ev.symbol
            is_sheltered_ev = ev.id in sheltered_ids
            is_affiliated_ev = ev.id in affiliated_ids
            is_other_scope = is_sheltered_ev or is_affiliated_ev

            # SPLIT and OPENING_BALANCE aren't real acquisitions and
            # never contribute to the §1091 replacement lists, but
            # they DO change the running taxable position the main
            # pass tracks in inventory_long/short. Mirror those
            # position changes in net_qty_state so a later BUYSELL is
            # classified against the correct running balance — without
            # this, a SELL drawing from a phantom OPENING_BALANCE long
            # registers as a fake short-replacement (because the
            # pre-pass thought net_qty was 0), and a post-split SELL
            # mis-counts open/close against pre-split units.
            if ev.action == 'SPLIT':
                # (Schedule recording moved to split_timeline above.)
                # A split is a property of the SECURITY, and the entry
                # dedupe keeps ONE row per corporate event in an
                # arbitrary book/account — so scale the taxable running
                # position AND every context book's balance regardless
                # of which copy survived (previously a sheltered-book
                # survivor left the taxable balance unscaled and vice
                # versa).
                ratio = ev.quantity
                if ratio:
                    if per_account_basis:
                        for _nk in list(net_qty_state):
                            if _nk[1] == sym:
                                net_qty_state[_nk] *= ratio
                    elif sym in net_qty_state:
                        net_qty_state[sym] *= ratio
                    for _ok in list(other_qty_state):
                        if _ok[1] == sym:
                            other_qty_state[_ok] *= ratio
                # Handle ticker rename (e.g. merger). Migration ensures
                # future BUYSELLs on the new ticker correctly partition
                # into open/close portions against the migrated balance.
                target_symbol = (ev.symbol_new or '').strip()
                if target_symbol and target_symbol != sym:
                    if per_account_basis:
                        for _nk in list(net_qty_state):
                            if _nk[1] == sym:
                                _tk = (_nk[0], target_symbol)
                                net_qty_state[_tk] = (
                                    net_qty_state.get(_tk, 0.0)
                                    + net_qty_state.pop(_nk))
                    else:
                        net_qty_state[target_symbol] = net_qty_state.get(target_symbol, 0.0) + net_qty_state.get(sym, 0.0)
                        # pop (not `del`): a rename-SPLIT with a falsy
                        # ratio skips the scaling above, so `sym` may
                        # never have been added to net_qty_state — `del`
                        # would KeyError.
                        net_qty_state.pop(sym, None)
                    for _ok in list(other_qty_state):
                        if _ok[1] == sym:
                            _nk = (_ok[0], target_symbol)
                            other_qty_state[_nk] = (
                                other_qty_state.get(_nk, 0.0)
                                + other_qty_state.pop(_ok))
                continue
            if ev.action == 'OPENING_BALANCE':
                if abs(ev.quantity) >= epsilon:
                    if is_other_scope:
                        _ok = (ev.account, sym)
                        other_qty_state[_ok] = (
                            other_qty_state.get(_ok, 0.0) + ev.quantity)
                    else:
                        _nk = _nkey(ev.account, sym)
                        net_qty_state[_nk] = net_qty_state.get(_nk, 0.0) + ev.quantity
                continue
            if ev.action not in ('BUYSELL', 'ASSIGN'):
                continue
            if abs(ev.quantity) < epsilon:
                continue

            prev = net_qty_state.get(_nkey(ev.account, sym), 0.0)

            if ev.quantity > 0:
                # Taxable buys close any taxable shorts first; leftover
                # opens long. Sheltered/affiliated buys live in a
                # separate book — their full qty is §1091 replacement-
                # eligible (Rev. Rul. 2008-5 for sheltered; §1091(a)
                # spouse/controlled-entity for affiliated), and they
                # don't move the taxable running position.
                if is_other_scope:
                    open_qty = ev.quantity
                else:
                    open_qty = ev.quantity - min(ev.quantity, max(0.0, -prev))
                if open_qty > epsilon:
                    long_replacements.setdefault(_rep_key(sym), []).append({
                        'tx': ev,
                        'date': ev.date,
                        'open_qty': open_qty,
                        'remaining_qty': open_qty,
                        'is_sheltered': is_sheltered_ev,
                        'is_affiliated': is_affiliated_ev,
                        'lot_ref': None,
                        'pending_basis_add': Decimal(0),
                        'effective_acq_date': ev.date,
                    })
            else:
                if is_other_scope:
                    # A sheltered/affiliated SELL is a short-side §1091
                    # replacement only for the portion that actually
                    # OPENS a short in its own book. Registering the
                    # full quantity meant a routine registered-account
                    # sale of a long (registered accounts cannot even
                    # open shorts) permanently destroyed a taxable
                    # short-cover loss — inconsistent with both the
                    # taxable partition below and the Canada engine.
                    _oprev = other_qty_state.get((ev.account, sym), 0.0)
                    open_qty = abs(ev.quantity) - min(abs(ev.quantity),
                                                      max(0.0, _oprev))
                    if open_qty > epsilon and is_sheltered_ev:
                        # A registered account cannot open a short: a
                        # sheltered sale exceeding its recorded balance
                        # is missing acquisition history (an in-kind
                        # move between two IRAs, an incomplete export)
                        # — registering it as a §1091 short replacement
                        # PERMANENTLY denied real taxable losses. Warn
                        # and skip; affiliated (spouse) accounts keep
                        # the partition result, since they genuinely
                        # can short.
                        print(f"warning: sheltered account "
                              f"{ev.account!r} sells {open_qty:g} "
                              f"{sym} beyond its recorded balance on "
                              f"{ev.date} — missing acquisition "
                              f"history; NOT treated as a §1091 short "
                              f"replacement. Add the missing sheltered "
                              f"buy/transfer rows for exact wash "
                              f"matching.", file=sys.stderr)
                        open_qty = 0.0
                else:
                    open_qty = abs(ev.quantity) - min(abs(ev.quantity), max(0.0, prev))
                if open_qty > epsilon:
                    short_replacements.setdefault(_rep_key(sym), []).append({
                        'tx': ev,
                        'date': ev.date,
                        'remaining_qty': open_qty,
                        'is_sheltered': is_sheltered_ev,
                        'is_affiliated': is_affiliated_ev,
                        'short_lot_ref': None,
                        'pending_proceeds_reduction': Decimal(0),
                    })
            if not is_other_scope:
                net_qty_state[_nkey(ev.account, sym)] = prev + ev.quantity
            else:
                _ok = (ev.account, sym)
                other_qty_state[_ok] = (other_qty_state.get(_ok, 0.0)
                                        + ev.quantity)

        # === MAIN PASS STATE ===
        inventory_long: Dict[str, List[Dict[str, Any]]] = {}
        inventory_short: Dict[str, List[Dict[str, Any]]] = {}
        realized_gains: List[Dict[str, Any]] = []
        wash_sale_records: List[Dict[str, Any]] = []
        symbol_traces: Dict[str, List[str]] = {}
        # Track currency per symbol across the full input set (taxable +
        # sheltered + affiliated). The inventory only reports symbols whose
        # taxable lots are still open, but a symbol-keyed lookup means we
        # can attach currency at emission time without re-walking lots.
        # Mirrors the Canada engine's per-pool currency tracking + mismatch
        # guard at line ~286.
        symbol_currency: Dict[str, str] = {}
        _main_ids = {id(t) for t in transactions}
        for _tx in (transactions + (sheltered_transactions or []) + (affiliated_transactions or [])):
            if not _tx.currency or _tx.action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER'):
                continue
            existing = symbol_currency.get(_tx.symbol)
            if existing and existing != _tx.currency:
                if id(_tx) not in _main_ids:
                    # CONTEXT books (sheltered/affiliated) exist for
                    # wash matching only — a native-currency sheltered
                    # file must not kill the whole run (the Canada
                    # engine gates its check to the taxable scope the
                    # same way). Warn and keep the main book's label.
                    print(f"warning: {_tx.symbol}: context book "
                          f"transaction {_tx.id} on {_tx.date} is in "
                          f"{_tx.currency!r} but the main book uses "
                          f"{existing!r} — context amounts assumed "
                          f"comparable; convert the context file for "
                          f"exact wash amounts.", file=sys.stderr)
                    continue
                raise ValueError(
                    f"Currency mismatch for {_tx.symbol}: previously seen as {existing!r} "
                    f"but transaction {_tx.id} on {_tx.date} is in {_tx.currency!r}. "
                    f"Run taxjson_convert_currency first to unify currencies."
                )
            if not existing:
                symbol_currency[_tx.symbol] = _tx.currency
        # When an option closes via ASSIGN/exercise, the premium is rolled
        # into the underlying stock's basis/proceeds rather than recognized
        # as a separate gain (IRS Pub 550). Key: underlying symbol, value:
        # negative of the would-be gain on the option close, matching the
        # Canada engine's sign convention (so SELL adds, BUY subtracts).
        pending_option_adjustments: Dict[str, float] = {}

        def _rep_units_factor(sym: str, from_date: str, to_date: str) -> float:
            """Cumulative split factor converting a share quantity denominated
            at `from_date` into `to_date` units — the (from, to] boundary and
            reciprocal-backward semantics live in lib/corporate_timeline.py.
            This is what lets a replacement record stay in its own trade-date
            units and still match correctly against a loss in different
            units — including a split BETWEEN the loss and a later
            replacement, which a scale-at-split approach can never see (the
            loss is processed first).

            Queries go through the rename-chain canonical symbol: the
            timeline migrates a symbol's schedule onto the rename target at
            build time, so a raw pre-rename symbol would see an empty
            schedule (factor 1.0) even for splits genuinely inside
            (from, to] — reps are keyed canonically (_rep_key); the unit
            conversion must be too."""
            return split_timeline.factor(
                split_timeline.canonical(sym), from_date, to_date)

        def find_replacements_in_window(rep_list: List[Dict[str, Any]], loss_date_str: str):
            # No ID-based self-exclusion: the FIFO pop/partial branches
            # zero or decrement the consumed shares' rep capacity BEFORE
            # the wash match runs, so a loss can never match the very
            # shares it just sold — but the RETAINED shares of a
            # partially-sold purchase are genuine §1091 replacements
            # (brokers report code W on them), and excluding the whole
            # originating purchase by tx id made the disallowance depend
            # on order granularity: one 200-share order → loss allowed,
            # two 100-share orders same day → loss disallowed.
            try:
                loss_dt = datetime.strptime(loss_date_str, '%Y-%m-%d')
            except ValueError:
                return []
            out = []
            for rep in rep_list:
                if rep['remaining_qty'] <= epsilon:
                    continue
                try:
                    rep_dt = datetime.strptime(rep['date'], '%Y-%m-%d')
                except ValueError:
                    continue
                if abs((rep_dt - loss_dt).days) <= self.WASH_WINDOW_DAYS:
                    if getattr(rep['tx'], 'type', '') \
                            == 'transfer_rewrite':
                        raise AmbiguousTransferDateError(
                            f"{rep['tx'].symbol}: a TRANSFER-in dated "
                            f"{rep['tx'].date} (account "
                            f"{rep['tx'].account}) sits inside the "
                            f"wash-sale window of the {loss_date_str} "
                            f"loss, but its date is a broker ARRIVAL "
                            f"date that may not be an acquisition. "
                            f"Import the prior broker's history (the "
                            f"pair then nets out), add a DECLARED "
                            f"counter-TRANSFER .tt row (custody move; "
                            f"the one-line ACQUIRED ... ARRIVED ... "
                            f"form covers move + true cost), or "
                            f"record the true acquisition as a "
                            f"BUYSELL row.")
                    out.append(rep)
            # Reg. 1.1091-1(c): replacement shares match in the ORDER
            # ACQUIRED. Date alone left same-date ties to pre-pass insertion
            # order (which follows SETTLE date), so which lot inherited the
            # deferred loss + holding period was effectively random — and a
            # sheltered same-date lot could beat an earlier-acquired taxable
            # one, flipping a deferral into a permanent denial. Tie-break by
            # intra-day time, then id for determinism.
            out.sort(key=lambda r: (r['date'], r['tx'].time or '', r['tx'].id))
            return out

        # === MAIN PASS ===
        # Underlyings whose assignment STOCK leg is explicitly marked
        # (action='ASSIGN' on a non-option symbol). When one exists,
        # only it may consume the staged option premium — see the
        # Canada engine's twin set for the hijack rationale.
        # Time-scoped marked-leg keys (trade-date basis, matching this
        # engine's us_main sort), keyed per (ACCOUNT, symbol) — in a
        # blended combined book one account's marked leg must neither
        # gate nor absorb another account's premium; see the Canada
        # twin for the full rationale. Single-book callers behave
        # identically.
        assign_stock_leg_keys: Dict[Any, list] = {}
        for _t in transactions:
            if _t.action == 'ASSIGN' and not is_option_symbol(_t.symbol):
                assign_stock_leg_keys.setdefault(
                    (_t.account, _t.symbol), []).append(
                    (_t.date, _t.time or ''))

        def _upcoming_marked_leg(acct, sym, d, tm):
            return any(k >= (d, tm or '')
                       for k in assign_stock_leg_keys.get((acct, sym), ()))
        # (ACCOUNT, underlying) pairs that trade as STOCK in the
        # taxable book — an ASSIGN whose underlying is absent for ITS
        # OWN account is cash-settled; see the Canada twin (OB
        # excluded: an OB-only underlying means the stock leg is
        # missing).
        taxable_stock_symbols = {
            (t.account, t.symbol) for t in transactions
            if not is_option_symbol(t.symbol)
            and t.action in ('BUYSELL', 'ASSIGN')}
        taxable_sorted = sorted(
            transactions,
            key=lambda x: event_sort_key(x, profile='us_main',
                                         date_of=get_sort_date))

        # Blended (combined multi-account) mode: FIFO basis pools are
        # per-(account, symbol) — the IRS keys basis per account — while
        # everything symbol-keyed (replacement lists, wash matching,
        # currency, traces) stays cross-account, which is exactly the
        # §1091 scope. Single-book callers (per_account_basis=False)
        # keep plain symbol keys — bit-identical behavior.
        def _ikey(acct, sym):
            return (acct, sym) if per_account_basis else sym

        def _inv_keys_for(inv, sym):
            if per_account_basis:
                return [k for k in inv if k[1] == sym]
            return [sym] if sym in inv else []

        for tx in taxable_sorted:
            symbol = tx.symbol
            ikey = _ikey(tx.account, symbol)
            inventory_long.setdefault(ikey, [])
            inventory_short.setdefault(ikey, [])
            if trace and symbol not in symbol_traces:
                symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} ---"]

            if non_capital(tx.action, tx.type):
                continue

            # ----- SPLIT (handles both classic splits and merger rollovers) -----
            # Mirror of the Canada engine's SPLIT branch (core.py:341).
            # Without this, SPLIT falls through to the default BUY path
            # and the engine would add `ratio` (e.g. 0.0625 for a 1-for-16
            # merger) as new shares at $0 cost — completely wrong.
            if tx.action == 'SPLIT':
                ratio = tx.quantity
                # A zero/falsy ratio skips only the SCALING — the
                # rename migration below must still run. The old
                # `continue` dropped the whole event while the pre-pass
                # and SplitTimeline both honored it, forking the book
                # into a stranded long under the old symbol plus a
                # phantom short under the new one.
                if ratio:
                    # Scale every existing lot's qty by ratio and its
                    # cost-per-share by 1/ratio (so total cost stays put).
                    # Blended mode: a split is a property of the
                    # SECURITY — scale every account's key.
                    for inv in (inventory_long, inventory_short):
                        for _k in _inv_keys_for(inv, symbol):
                            for lot in inv[_k]:
                                lot['qty'] = lot['qty'] * ratio
                            # Cost basis is held in Decimal; multiplying
                            # qty by ratio implicitly scales cost-per-
                            # share by 1/ratio because the cost field
                            # stays unchanged.
                # Replacement records are NOT rescaled here: they stay
                # denominated in their own trade-date units, and the match
                # sites convert with _rep_units_factor. The old blanket
                # rescale multiplied every rep for the symbol — including
                # buys dated AFTER the split that were already in post-
                # split units — doubling their match quantity (2x wash
                # disallowance and inflated deferred basis).
                # If symbol_new is set, move all lots to the new symbol.
                target_symbol = (tx.symbol_new or '').strip()
                if target_symbol and target_symbol != symbol:
                    for inv in (inventory_long, inventory_short):
                        for _k in _inv_keys_for(inv, symbol):
                            _tk = ((_k[0], target_symbol)
                                   if per_account_basis else target_symbol)
                            inv.setdefault(_tk, []).extend(inv[_k])
                            del inv[_k]
                            # FIFO is DATE order, not append order
                            # (FUZZ #G): merging migrated lots after
                            # existing target lots made the newest lot
                            # sell first, flipping LONG_TERM to
                            # SHORT_TERM and misstating per-lot gains.
                            # Sort by ACTUAL acquisition date only —
                            # never effective_acq_date: §1223(3)
                            # tacking rewrites effective_acq_date to a
                            # fictitious earlier date (holding-period
                            # only; Reg. 1.1012-1(c) FIFO goes by the
                            # order shares were actually acquired), and
                            # it only does so when wash detection is
                            # on. Leading the key with the tacked date
                            # made a rename-split's merge order — and
                            # hence WHICH shares a later sale consumed
                            # — depend on wash processing, silently
                            # shifting plain basis between realized
                            # gains and held inventory (conservation
                            # broke by exactly that basis difference).
                            # Stable sort keeps append order (itself
                            # chronological per source symbol) for
                            # same-date ties in both runs.
                            inv[_tk].sort(key=lambda l: l['date'])
                    # Move wash-sale replacement records too. Wash-sale
                    # matching is by symbol key, so a post-rename SELL
                    # of RGLD.US would otherwise miss any open
                    # replacement window opened on SSL.TO. CRA / IRS
                    # treat substantially-identical property across the
                    # rename as continuous for wash purposes.
                    for rep_dict in (long_replacements, short_replacements):
                        if symbol in rep_dict:
                            rep_dict.setdefault(target_symbol, []).extend(rep_dict[symbol])
                            del rep_dict[symbol]
                    # Carry the symbol→currency map too — without this,
                    # the currency guard above flags the post-rename
                    # ticker as "unseen" and the per-ticker stats lose
                    # their currency association.
                    if symbol in symbol_currency:
                        symbol_currency.setdefault(target_symbol, symbol_currency[symbol])
                        del symbol_currency[symbol]
                continue

            # ----- OPENING_BALANCE (phantom pre-data-window shares) -------
            # Synthesized by --incomplete-history. The Canada engine
            # tracks a `tainted` flag per pool so dispositions from a
            # phantom pool can be split out into `manual_reporting_required`.
            # The US engine doesn't have a pool concept (FIFO lot list
            # instead), so we tag each phantom lot directly and the
            # disposition path below propagates the flag to its gain
            # entry — same end-state as Canada's tainted-pool model.
            if tx.action == 'OPENING_BALANCE':
                qty = tx.quantity
                if abs(qty) < epsilon:
                    continue
                if qty > 0:
                    inventory_long.setdefault(ikey, []).append({
                        'qty': abs(qty),
                        'cost_basis': Decimal(0),
                        'date': tx.date,
                        'effective_acq_date': tx.date,
                        'id': tx.id,
                        'tainted': True,
                    })
                else:
                    # Short-side lots use a different shape than long
                    # lots: the disposition path reads `proceeds`, not
                    # `cost_basis` (core.py:1378). A naive long-lot
                    # shape here would KeyError on the first BUY-CLOSE.
                    # `effective_open_date` mirrors the live short-open
                    # path's field name.
                    inventory_short.setdefault(ikey, []).append({
                        'qty': abs(qty),
                        'proceeds': Decimal(0),
                        'date': tx.date,
                        'effective_open_date': tx.date,
                        'id': tx.id,
                        'tainted': True,
                    })
                continue

            # ----- ADJUST (return of capital / manual basis adjustment) ---
            # Mirror of the Canada engine's ADJUST branch: a signed basis
            # delta in net_amount, no share movement. US treatment (IRC
            # §301(c)(2), Pub 550): a nondividend distribution reduces
            # stock basis — apportioned per share across the open long
            # lots. Excess over basis is capital gain in the distribution
            # year (§301(c)(3)), which — like Canada's s.40(3) — is
            # flagged for manual reporting, not silently computed.
            # Before this branch, ADJUST rows fell through to the
            # qty-epsilon skip below and were silently dropped, leaving
            # lot basis unreduced and understating gains at sale.
            if tx.action == 'ADJUST':
                lots = inventory_long.get(ikey, [])
                open_qty = sum(l['qty'] for l in lots)
                if open_qty <= epsilon:
                    print(
                        f"warning: {symbol} ADJUST of {tx.net_amount:.2f} "
                        f"on {tx.date} found no open long lots (position "
                        f"closed or short) — a return of capital with no "
                        f"basis to reduce is a taxable event needing "
                        f"manual review; the row was NOT applied.",
                        file=sys.stderr,
                    )
                    continue
                delta = D(tx.net_amount)
                applied = Decimal(0)
                went_negative = False
                for i, lot in enumerate(lots):
                    if i == len(lots) - 1:
                        # Last lot absorbs the division remainder so the
                        # applied total equals net_amount exactly.
                        share = delta - applied
                    else:
                        share = (delta * D(lot['qty'])) / D(open_qty)
                    lot['cost_basis'] += share
                    applied += share
                    if lot['cost_basis'] < D('-0.005'):
                        went_negative = True
                if went_negative:
                    print(
                        f"warning: {symbol} lot basis went NEGATIVE after "
                        f"ADJUST on {tx.date} — under IRC §301(c)(3) the "
                        f"excess of a nondividend distribution over basis "
                        f"is capital gain in that year, which this engine "
                        f"does NOT compute. Verify the ROC amounts and "
                        f"report the deemed gain manually.",
                        file=sys.stderr,
                    )
                if trace:
                    symbol_traces[symbol].append(
                        f"# {tx.date} ADJUST   {tx.net_amount:10.4f} | "
                        f"spread pro-rata over {len(lots)} lot(s), "
                        f"{open_qty:.4f} sh")
                continue

            if abs(tx.quantity) < epsilon:
                continue

            tx_qty_abs = abs(tx.quantity)
            tx_net = abs(tx.net_amount)
            # Commission and fee are split separately on each gain entry
            # (see make_gain_entry below) — apportioned by chunk_qty share.


            is_option_sym = is_option_symbol(symbol)

            def make_gain_entry(*, chunk_qty, chunk_cost, chunk_proceeds, raw_gain, allowed_gain,
                                disallowed_amt, permanently_disallowed_amt, replacement_ids, wash_reps,
                                acq_for_holding, days_held, is_long_term, direction, rg_trace,
                                tainted=False, raw_acq_date=''):
                fee_share = chunk_qty / tx_qty_abs if tx_qty_abs > 0 else 1.0
                total_disallowed = disallowed_amt + permanently_disallowed_amt
                entry = {
                    'date': tx.date,
                    'date_settle': tx.date_settle or tx.date,
                    'symbol': symbol,
                    'qty': chunk_qty,
                    'cost': chunk_cost,
                    'proceeds': chunk_proceeds,
                    'gain': allowed_gain,
                    'raw_gain': raw_gain,
                    'disallowed_amount': total_disallowed,
                    'permanently_disallowed': permanently_disallowed_amt,
                    'replacement_lot_ids': replacement_ids,
                    'days_held': max(0, days_held),
                    # The lot's ACTUAL purchase/open date. Form 8949
                    # column (b) wants this (brokers report the real
                    # date and adjust term separately); days_held stays
                    # tacked for the term computation.
                    'acquired_date': raw_acq_date,
                    'account': tx.account,
                    'currency': tx.currency,
                    'commission': float(tx.commission or 0) * fee_share,
                    'fee': float(tx.fee or 0) * fee_share,
                    'is_wash_sale': total_disallowed > epsilon,
                    'is_option': is_option_sym,
                    'id': tx.id,
                    'trace': rg_trace,
                    'direction': direction,
                    'term': 'LONG_TERM' if is_long_term else 'SHORT_TERM',
                    # Schema symmetry with CanadaTaxRules — these are
                    # Canada-side concepts; US emits None so downstream
                    # consumers can read either engine's output with
                    # .get() and the same field set.
                    'wash_trigger': None,
                    'wash_window': None,
                    'wash_replacements': wash_reps if wash_reps else None,
                    # True when the consumed lot was a phantom
                    # OPENING_BALANCE (cost basis = 0 from synthesized
                    # `--incomplete-history` rows). The CLI's tainted-
                    # split path routes these to manual_reporting_required
                    # — same end-state as Canada's tainted-pool model.
                    'tainted': bool(tainted),
                }
                return entry

            # Option-leg ASSIGN: the premium rolls into the underlying's
            # basis/proceeds instead of being recognized here. We still pop
            # the position from inventory, but emit no gain entry — the
            # would-be gain is staged in pending_option_adjustments[underlying]
            # and consumed by the stock-leg BUYSELL processed next.
            is_option_assign = (tx.action == 'ASSIGN')
            underlying_for_assign = option_underlying(symbol) if is_option_assign else None
            if not underlying_for_assign:
                is_option_assign = False
            elif (tx.account, underlying_for_assign) not in taxable_stock_symbols:
                # Cash-settled assignment/exercise (index options): the
                # underlying never trades as stock in this book, so no
                # stock leg can consume a staged premium — realize the
                # option's own P&L via normal disposition accounting
                # (mirrors the Canada engine, note and all).
                print(f"note: {symbol}: assignment treated as "
                      f"cash-settled ({underlying_for_assign} never "
                      f"trades as stock in this book) — option P&L "
                      f"realized directly. If a stock leg is missing "
                      f"from your input, add it and re-run.",
                      file=sys.stderr)
                is_option_assign = False
                underlying_for_assign = None

            # ============================================================
            # BUY path: first close any existing shorts FIFO; if leftover
            # quantity remains, open a new long lot with the remainder.
            # ============================================================
            if tx.quantity > 0:
                qty_remaining = tx.quantity

                # Pop the pending option-assignment premium once, up front.
                # We apportion it across BOTH the buy-to-close chunks (below)
                # and any leftover buy-to-open (further down) so the full
                # premium is consumed even when an assignment crosses zero
                # (a short-put assignment that closes an existing short and
                # opens a new long). Earlier the pop happened only in the
                # leftover branch, silently discarding the close-share.
                # Stored as -gain (Canada convention). Only the marked
                # ASSIGN stock leg may consume it when one exists
                # (mirrors the Canada engine): an unrelated same-symbol
                # trade sorted between the option leg and the stock leg
                # used to hijack the premium.
                if (tx.action == 'ASSIGN'
                        or not _upcoming_marked_leg(tx.account, symbol,
                                                    tx.date, tx.time)):
                    option_adj = pending_option_adjustments.pop(
                        (tx.account, symbol), 0.0)
                else:
                    option_adj = 0.0

                # --- buy-to-close: pop short lots FIFO ---
                while qty_remaining > epsilon and inventory_short[ikey]:
                    short_lot = inventory_short[ikey][0]
                    if short_lot['qty'] <= qty_remaining + epsilon:
                        chunk_qty = short_lot['qty']
                        chunk_open_proceeds_d = short_lot['proceeds']
                        inventory_short[ikey].pop(0)
                        # Mark any short_replacement records that pointed at
                        # this lot as fully consumed. A later wash-sale match
                        # against this rep would otherwise silently mutate
                        # the detached dict and lose the disallowance — see
                        # `fully_consumed` branch in the wash-match below.
                        for r in short_replacements.get(_rep_key(symbol), []):
                            if r.get('short_lot_ref') is short_lot:
                                r['fully_consumed'] = True
                                r['short_lot_ref'] = None
                                # Covered shares can no longer serve as
                                # §1091 replacement (FUZZ #A): leaving
                                # capacity here matched later losses
                                # against DEAD lots -> permanent denial
                                # of real losses in taxable-only chains.
                                r['remaining_qty'] = 0.0
                    else:
                        chunk_qty = qty_remaining
                        chunk_open_proceeds_d = (D(chunk_qty) / D(short_lot['qty'])) * short_lot['proceeds']
                        short_lot['qty'] -= chunk_qty
                        short_lot['proceeds'] -= chunk_open_proceeds_d
                        # Release embedded deferral proportionally on a
                        # partial cover (mirrors the long side's
                        # wash_deferred proration).
                        _swd = short_lot.get('wash_deferred', Decimal(0))
                        if _swd:
                            short_lot['wash_deferred'] = _swd - (
                                D(chunk_qty)
                                / D(short_lot['qty'] + chunk_qty)) * _swd
                        for r in short_replacements.get(_rep_key(symbol), []):
                            if r.get('short_lot_ref') is short_lot:
                                # remaining_qty is denominated in the
                                # rep's own trade-date units; chunk_qty
                                # is in TODAY's units. With a split in
                                # between, the raw subtraction left
                                # phantom capacity (or erased real
                                # capacity) — and since this decrement
                                # is the self-match guard, that skewed
                                # §1091 quantities directly.
                                _cuf = _rep_units_factor(
                                    symbol, r['date'], tx.date)
                                r['remaining_qty'] = max(
                                    0.0, r['remaining_qty']
                                    - chunk_qty / (_cuf or 1.0))

                    chunk_open_proceeds = float(chunk_open_proceeds_d)
                    # Close cost is apportioned from this BUY by qty share.
                    chunk_close_cost = (chunk_qty / tx_qty_abs) * tx_net
                    # Apportion the option-assignment premium to this
                    # chunk's close cost (short-put assignment: the
                    # premium reduces the effective cost of acquiring
                    # the stock used to cover the short).
                    if option_adj != 0:
                        chunk_close_cost += option_adj * (chunk_qty / tx_qty_abs)
                    # Gain on closing a short: opening_proceeds − closing_cost.
                    raw_gain = chunk_open_proceeds - chunk_close_cost

                    if is_option_assign:
                        # Premium rolls into the underlying — accumulate the
                        # would-be gain (with sign flipped to match the
                        # CanadaTaxRules / Pub 550 convention: BUY of stock
                        # subtracts pending_adj from cost; SELL of stock adds
                        # pending_adj to proceeds).
                        _pk = (tx.account, underlying_for_assign)
                        pending_option_adjustments[_pk] = \
                            pending_option_adjustments.get(_pk, 0.0) - raw_gain
                        if trace:
                            symbol_traces[symbol].append(
                                f"# {tx.date} ASSIGN-CLOSE {chunk_qty:10.4f} | "
                                f"OpenProc: {chunk_open_proceeds:10.4f} → "
                                f"premium rolled into {underlying_for_assign} basis "
                                f"(would-be gain {raw_gain:.4f})"
                            )
                        qty_remaining -= chunk_qty
                        continue

                    disallowed_amt = 0.0
                    permanently_disallowed_amt = 0.0
                    replacement_ids: List[str] = []
                    wash_reps: List[Dict[str, Any]] = []

                    # Tainted (phantom OPENING_BALANCE) short lots have
                    # proceeds=0, so covering them ALWAYS books a bogus loss
                    # — it must never feed §1091 matching (it would fabricate
                    # wash records and push proceeds-reductions onto clean
                    # lots). Mirrors the Canada engine's tainted-loss gate.
                    if (raw_gain < -epsilon and detect_wash_sales
                            and not short_lot.get('tainted')):
                        # §1091 on shorts: a loss on closing a short is
                        # disallowed if a substantially-identical short was
                        # opened within ±30 days. The disallowed loss reduces
                        # the replacement short's effective opening proceeds.
                        remaining_loss_qty = chunk_qty
                        chunk_loss = abs(raw_gain)
                        loss_per_share_d = D(chunk_loss) / D(chunk_qty)
                        candidates = find_replacements_in_window(
                            short_replacements.get(_rep_key(symbol), []),
                            tx.date,
                        )
                        for rep in candidates:
                            if remaining_loss_qty <= epsilon:
                                break
                            # rep['remaining_qty'] is in the rep's own trade-
                            # date units; convert to the loss-date's units
                            # for the comparison, and consume in rep units.
                            _uf = _rep_units_factor(symbol, rep['date'], tx.date)
                            match_qty = min(remaining_loss_qty,
                                            rep['remaining_qty'] * _uf)
                            match_disallowed_d = D(match_qty) * loss_per_share_d
                            match_disallowed = float(match_disallowed_d)
                            rep['remaining_qty'] -= match_qty / _uf
                            remaining_loss_qty -= match_qty

                            if rep['is_sheltered']:
                                permanently_disallowed_amt += match_disallowed
                            elif rep.get('fully_consumed'):
                                # Replacement short was already opened AND
                                # closed before this wash sale fired. The
                                # proceeds reduction has nowhere to land —
                                # the gain entry for the replacement's close
                                # has already been emitted. Treat as
                                # permanently disallowed; the loss is
                                # disallowed under §1091 but the deferral
                                # mechanism is unavailable. (This mirrors
                                # Rev. Rul. 2008-5's "no basis transfer"
                                # treatment for unrecoverable replacements.)
                                permanently_disallowed_amt += match_disallowed
                            else:
                                disallowed_amt += match_disallowed
                                if rep.get('short_lot_ref') is not None:
                                    # Replacement short is already open —
                                    # reduce its remaining proceeds now,
                                    # and track the embedded deferral so
                                    # inventory reports show it (the
                                    # long side already did; short-side
                                    # deferrals were invisible).
                                    rep['short_lot_ref']['proceeds'] -= match_disallowed_d
                                    rep['short_lot_ref']['wash_deferred'] = (
                                        rep['short_lot_ref'].get(
                                            'wash_deferred', Decimal(0))
                                        + match_disallowed_d)
                                else:
                                    rep['pending_proceeds_reduction'] += match_disallowed_d
                            replacement_ids.append(rep['tx'].id)
                            wash_reps.append({
                                'tx_id': rep['tx'].id,
                                'date': rep['date'],
                                'qty_total': float(rep['tx'].quantity),
                                'price': float(rep['tx'].price),
                                'account': rep['tx'].account,
                                'match_qty': match_qty,
                                'proceeds_reduction': match_disallowed,
                                'is_sheltered': rep['is_sheltered'],
                                'is_affiliated': rep.get('is_affiliated', False),
                            })

                    # Stand-alone shorts: holding period is conventionally
                    # zero — gain on close is SHORT_TERM per §1222.
                    # §1233(b)(1) anti-conversion rule applies only when
                    # offsetting same-symbol long is held >1yr at short-open;
                    # not implemented in v1.
                    is_long_term = False
                    acq_for_holding = short_lot.get('effective_open_date', short_lot['date'])
                    try:
                        days_held = (datetime.strptime(tx.date, '%Y-%m-%d') -
                                     datetime.strptime(acq_for_holding, '%Y-%m-%d')).days
                    except ValueError:
                        days_held = 0

                    allowed_gain = raw_gain + disallowed_amt + permanently_disallowed_amt

                    if trace:
                        wash_tag = ''
                        if disallowed_amt > 0 or permanently_disallowed_amt > 0:
                            wash_tag = f" [WASH disallowed={disallowed_amt + permanently_disallowed_amt:.4f}]"
                        symbol_traces[symbol].append(
                            f"# {tx.date} BUY-CLOSE {chunk_qty:10.4f} | "
                            f"Against Short: {short_lot['date']} | "
                            f"OpenProc: {chunk_open_proceeds:10.4f} | "
                            f"CloseCost: {chunk_close_cost:10.4f} | "
                            f"RawGain: {raw_gain:10.4f} | "
                            f"AllowedGain: {allowed_gain:10.4f}{wash_tag}"
                        )
                    rg_trace = []
                    if trace:
                        rg_trace = list(symbol_traces[symbol])
                        symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} (CONT...) ---"]

                    # Signed cash-flow convention for SHORT — the SAME
                    # field order as the Canada engine (FUZZ #E: the
                    # engines had premium/buyback in OPPOSITE fields,
                    # and USA rows broke gain == proceeds - cost):
                    # cost = -opening premium (cash IN at open),
                    # proceeds = -close cost (cash OUT at close), so
                    # proceeds - cost = signed gain, direction-free.
                    # Old line: cost is the cash
                    # outflow on the closing buy (positive number stored as
                    # negative so downstream tools can compute
                    # proceeds-cost=signed gain regardless of direction).
                    # Matches the Canada engine's SHORT signing.
                    entry = make_gain_entry(
                        chunk_qty=chunk_qty,
                        chunk_cost=-chunk_open_proceeds,
                        chunk_proceeds=-chunk_close_cost,
                        raw_gain=raw_gain,
                        allowed_gain=allowed_gain,
                        disallowed_amt=disallowed_amt,
                        permanently_disallowed_amt=permanently_disallowed_amt,
                        replacement_ids=replacement_ids,
                        wash_reps=wash_reps,
                        acq_for_holding=acq_for_holding,
                        days_held=days_held,
                        is_long_term=is_long_term,
                        direction='SHORT',
                        rg_trace=rg_trace,
                        tainted=short_lot.get('tainted', False),
                        raw_acq_date=short_lot['date'],
                    )
                    realized_gains.append(entry)

                    total_disallowed = disallowed_amt + permanently_disallowed_amt
                    if total_disallowed > epsilon:
                        wash_sale_records.append({
                            'loss_tx_id': tx.id,
                            'symbol': symbol,
                            'date': tx.date,
                            # Settlement date too: the --year filter keys on
                            # tax_date; without this the record fell back to
                            # trade date while the gain entries used settle,
                            # splitting total_disallowed and its rows across
                            # two years.
                            'date_settle': tx.date_settle or tx.date,
                            'disallowed_amount': total_disallowed,
                            'permanently_disallowed': permanently_disallowed_amt,
                            'qty': chunk_qty,
                            'replacement_lot_ids': replacement_ids,
                            'direction': 'SHORT',
                        })
                    qty_remaining -= chunk_qty

                # --- buy-to-open: any leftover quantity opens a new long lot ---
                if qty_remaining > epsilon:
                    rep_record = next(
                        (r for r in long_replacements.get(_rep_key(symbol), [])
                         if r['tx'].id == tx.id and not r['is_sheltered']),
                        None,
                    )
                    leftover_share = qty_remaining / tx_qty_abs
                    leftover_cost_d = D(leftover_share) * D(tx_net)
                    pending_wash_d = (rep_record['pending_basis_add']
                                      if rep_record is not None
                                      else Decimal(0))
                    if rep_record is not None:
                        leftover_cost_d += rep_record['pending_basis_add']
                    # Apportion the leftover share of the option-assignment
                    # premium (the buy-to-close branch already consumed its
                    # share above — see the pop at the top of the BUY
                    # path). For a short put assignment, the premium
                    # received reduces the cost basis of the stock acquired
                    # at strike (per IRS Pub 550). Stored as -gain (Canada
                    # convention), so adding it here yields cost - premium.
                    if option_adj != 0:
                        leftover_cost_d += D(option_adj * leftover_share)
                    lot = {
                        'qty': qty_remaining,
                        'cost_basis': leftover_cost_d,
                        'wash_deferred': pending_wash_d,
                        'date': tx.date,
                        'effective_acq_date': rep_record['effective_acq_date'] if rep_record else tx.date,
                        'id': tx.id,
                    }
                    inventory_long[ikey].append(lot)
                    # A replacement matched BEFORE its own buy (the loss
                    # preceded it): only the matched shares carry the
                    # tacked holding period and the deferred basis —
                    # the rest of the buy is an ordinary lot. Split at
                    # creation (the post-loss twin of the split in the
                    # match loop); the matched sub-lot is `lot`, the
                    # remainder follows it in FIFO order and becomes
                    # the rep's lot_ref for later matches.
                    if rep_record is not None:
                        _matched = (rep_record.get('open_qty', 0.0)
                                    - rep_record['remaining_qty'])
                        if epsilon < _matched < qty_remaining - epsilon:
                            _frac = D(_matched) / D(qty_remaining)
                            _rem = {
                                'qty': qty_remaining - _matched,
                                'cost_basis': (D(leftover_share) * D(tx_net)
                                               * (1 - _frac)),
                                'wash_deferred': Decimal(0),
                                'date': tx.date,
                                'effective_acq_date': tx.date,
                                'id': tx.id,
                            }
                            if option_adj != 0:
                                _rem['cost_basis'] += (
                                    D(option_adj * leftover_share)
                                    * (1 - _frac))
                            lot['qty'] = _matched
                            lot['cost_basis'] = (
                                leftover_cost_d - _rem['cost_basis'])
                            inventory_long[ikey].append(_rem)
                            rep_record['lot_ref'] = (
                                _rem if rep_record['remaining_qty']
                                > epsilon else lot)
                        else:
                            rep_record['lot_ref'] = lot
                        rep_record['pending_basis_add'] = Decimal(0)
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} BUY-OPEN  {qty_remaining:10.4f} @ {tx.price:10.4f} | "
                            f"Lot_Cost: {float(lot['cost_basis']):10.4f} | "
                            f"Effective_Acq: {lot['effective_acq_date']}"
                        )
                continue

            # ============================================================
            # SELL path: first close any existing longs FIFO; if leftover
            # quantity remains, open a new short lot.
            # ============================================================
            qty_remaining = tx_qty_abs

            # Pull any pending option-assignment premium for this stock symbol.
            # For a short call assignment, the premium increases the proceeds
            # of the stock sold at strike. Stored as -gain (Canada convention)
            # — subtracting it from per-chunk proceeds yields proceeds + gain.
            # Same marked-leg gate as the BUY path above.
            if (tx.action == 'ASSIGN'
                    or not _upcoming_marked_leg(tx.account, symbol,
                                                tx.date, tx.time)):
                option_adj_sell = pending_option_adjustments.pop(
                    (tx.account, symbol), 0.0)
            else:
                option_adj_sell = 0.0

            # --- sell-to-close: pop long lots FIFO ---
            while qty_remaining > epsilon and inventory_long[ikey]:
                lot = inventory_long[ikey][0]
                if lot['qty'] <= qty_remaining + epsilon:
                    chunk_qty = lot['qty']
                    chunk_cost_d = lot['cost_basis']
                    inventory_long[ikey].pop(0)
                    # Same defensive marker as the short side: if any
                    # long_replacement rep was pointing at this lot, a
                    # future wash-sale bump would otherwise hit a detached
                    # dict and disappear. Mark `fully_consumed` so the
                    # match falls through to permanent disallowance.
                    for r in long_replacements.get(_rep_key(symbol), []):
                        if r.get('lot_ref') is lot:
                            r['fully_consumed'] = True
                            r['lot_ref'] = None
                            # Sold shares can no longer serve as §1091
                            # replacement (FUZZ #A): stale capacity made
                            # later losses match DEAD lots and land in the
                            # permanent-denial branch, destroying real
                            # losses (economic 0 reported as +4000 on a
                            # 20-cycle ladder).
                            r['remaining_qty'] = 0.0
                else:
                    chunk_qty = qty_remaining
                    chunk_cost_d = (D(chunk_qty) / D(lot['qty'])) * lot['cost_basis']
                    _wd = lot.get('wash_deferred', Decimal(0))
                    if _wd:
                        lot['wash_deferred'] = _wd - (
                            D(chunk_qty) / D(lot['qty'])) * _wd
                    lot['qty'] -= chunk_qty
                    lot['cost_basis'] -= chunk_cost_d
                    for r in long_replacements.get(_rep_key(symbol), []):
                        if r.get('lot_ref') is lot:
                            # Same unit conversion as the short side
                            # above: consume the rep's capacity in ITS
                            # trade-date units, not today's.
                            _cuf = _rep_units_factor(
                                symbol, r['date'], tx.date)
                            r['remaining_qty'] = max(
                                0.0, r['remaining_qty']
                                - chunk_qty / (_cuf or 1.0))
                chunk_cost = float(chunk_cost_d)
                chunk_proceeds = (chunk_qty / tx_qty_abs) * tx_net
                # Apportion any option-assignment premium to this chunk's
                # proceeds (short-call assignment case: premium boosts proceeds).
                if option_adj_sell != 0:
                    chunk_proceeds -= option_adj_sell * (chunk_qty / tx_qty_abs)
                raw_gain = chunk_proceeds - chunk_cost

                if is_option_assign:
                    # Long option exercised: roll premium into underlying.
                    _pk = (tx.account, underlying_for_assign)
                    pending_option_adjustments[_pk] = \
                        pending_option_adjustments.get(_pk, 0.0) - raw_gain
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} ASSIGN-EXERCISE {chunk_qty:10.4f} | "
                            f"Cost: {chunk_cost:10.4f} Proc: {chunk_proceeds:10.4f} → "
                            f"premium rolled into {underlying_for_assign} basis "
                            f"(would-be gain {raw_gain:.4f})"
                        )
                    qty_remaining -= chunk_qty
                    continue

                disallowed_amt = 0.0
                permanently_disallowed_amt = 0.0
                replacement_ids: List[str] = []
                wash_reps: List[Dict[str, Any]] = []

                # Tainted long lots (basis 0) can't normally lose, but gate
                # symmetrically with the short side / Canada engine.
                if (raw_gain < -epsilon and detect_wash_sales
                        and not lot.get('tainted')):
                    remaining_loss_qty = chunk_qty
                    chunk_loss = abs(raw_gain)
                    loss_per_share_d = D(chunk_loss) / D(chunk_qty)
                    candidates = find_replacements_in_window(
                        long_replacements.get(_rep_key(symbol), []),
                        tx.date,
                    )
                    for rep in candidates:
                        if remaining_loss_qty <= epsilon:
                            break
                        # Convert the rep's own-units quantity into loss-date
                        # units for matching; consume in rep units.
                        _uf = _rep_units_factor(symbol, rep['date'], tx.date)
                        match_qty = min(remaining_loss_qty,
                                        rep['remaining_qty'] * _uf)
                        match_disallowed_d = D(match_qty) * loss_per_share_d
                        match_disallowed = float(match_disallowed_d)
                        rep['remaining_qty'] -= match_qty / _uf
                        remaining_loss_qty -= match_qty

                        # §1223(3) tacking: the replacement's holding
                        # period gains the PERIOD the wash-sold shares
                        # were held, not their calendar acquisition
                        # date. Inheriting the raw date wrongly
                        # included the sale→repurchase gap (up to 30
                        # days) in the holding period — flipping
                        # SHORT/LONG_TERM near the anniversary — and
                        # collapsed the overlap for replacements bought
                        # before the sale. Broker convention: adjusted
                        # acquisition date = the replacement's own buy
                        # date minus the prior holding period.
                        loss_acq_eff = lot.get('effective_acq_date', lot['date'])
                        try:
                            _prior_held = (
                                datetime.strptime(tx.date, '%Y-%m-%d')
                                - datetime.strptime(loss_acq_eff,
                                                    '%Y-%m-%d'))
                            _tacked_eff = (
                                datetime.strptime(rep['date'], '%Y-%m-%d')
                                - _prior_held).strftime('%Y-%m-%d')
                        except ValueError:
                            _tacked_eff = loss_acq_eff
                        # Tacking and the §1091(d) bump apply to the
                        # MATCHED shares only. A replacement lot bigger
                        # than the match is split at the match point:
                        # the matched sub-lot (kept as the original
                        # object, so lot identity elsewhere holds)
                        # takes the tack + bump; the untouched
                        # remainder becomes a new lot right behind it
                        # in FIFO order and the rep's lot_ref for any
                        # later match. Whole-lot tacking mis-termed the
                        # remainder (2026-09 US-engine audit: a 200-share
                        # rep for a 100-share loss reported all 200 as
                        # LONG_TERM).
                        _tack_lot = rep.get('lot_ref')
                        if _tack_lot is not None:
                            _mq = match_qty / (_uf or 1.0)
                            if _tack_lot['qty'] > _mq + epsilon:
                                _frac = D(_mq) / D(_tack_lot['qty'])
                                _rem = dict(_tack_lot)
                                _rem['qty'] = _tack_lot['qty'] - _mq
                                _rem['cost_basis'] = (
                                    _tack_lot['cost_basis'] * (1 - _frac))
                                _rem['wash_deferred'] = (
                                    _tack_lot.get('wash_deferred',
                                                  Decimal(0))
                                    * (1 - _frac))
                                _tack_lot['qty'] = _mq
                                _tack_lot['cost_basis'] = (
                                    _tack_lot['cost_basis'] * _frac)
                                _tack_lot['wash_deferred'] = (
                                    _tack_lot.get('wash_deferred',
                                                  Decimal(0)) * _frac)
                                for _lots in inventory_long.values():
                                    for _li, _l in enumerate(_lots):
                                        if _l is _tack_lot:
                                            _lots.insert(_li + 1, _rem)
                                            break
                                rep['lot_ref'] = _rem
                        if _tacked_eff < rep['effective_acq_date']:
                            rep['effective_acq_date'] = _tacked_eff
                        if (_tack_lot is not None
                                and _tacked_eff
                                < _tack_lot['effective_acq_date']):
                            _tack_lot['effective_acq_date'] = _tacked_eff

                        if rep['is_sheltered']:
                            permanently_disallowed_amt += match_disallowed
                        elif rep.get('fully_consumed'):
                            # Replacement lot was already sold before this
                            # wash sale fired. The basis bump has nowhere
                            # to land — the gain entry for the replacement's
                            # sale has already been emitted. Treat as
                            # permanently disallowed; the loss is
                            # disallowed under §1091 but the deferral
                            # mechanism is unavailable.
                            permanently_disallowed_amt += match_disallowed
                        else:
                            disallowed_amt += match_disallowed
                            if _tack_lot is not None:
                                _tack_lot['cost_basis'] += match_disallowed_d
                                _tack_lot['wash_deferred'] = (
                                    _tack_lot.get('wash_deferred',
                                                  Decimal(0))
                                    + match_disallowed_d)
                            else:
                                rep['pending_basis_add'] += match_disallowed_d
                        replacement_ids.append(rep['tx'].id)
                        wash_reps.append({
                            'tx_id': rep['tx'].id,
                            'date': rep['date'],
                            'qty_total': float(rep['tx'].quantity),
                            'price': float(rep['tx'].price),
                            'account': rep['tx'].account,
                            'match_qty': match_qty,
                            'basis_bump': match_disallowed,
                            'is_sheltered': rep['is_sheltered'],
                            'is_affiliated': rep.get('is_affiliated', False),
                        })

                acq_for_holding = lot.get('effective_acq_date', lot['date'])
                is_long_term = held_more_than_one_year(acq_for_holding, tx.date)
                try:
                    days_held = (datetime.strptime(tx.date, '%Y-%m-%d') -
                                 datetime.strptime(acq_for_holding, '%Y-%m-%d')).days
                except ValueError:
                    days_held = 0

                allowed_gain = raw_gain + disallowed_amt + permanently_disallowed_amt

                if trace:
                    wash_tag = ''
                    if disallowed_amt > 0 or permanently_disallowed_amt > 0:
                        wash_tag = f" [WASH disallowed={disallowed_amt + permanently_disallowed_amt:.4f}]"
                    symbol_traces[symbol].append(
                        f"# {tx.date} SELL-CLOSE {chunk_qty:10.4f} | "
                        f"Against Lot: {lot['date']} (eff {acq_for_holding}) | "
                        f"Cost: {chunk_cost:10.4f} | Proceeds: {chunk_proceeds:10.4f} | "
                        f"RawGain: {raw_gain:10.4f} | AllowedGain: {allowed_gain:10.4f}{wash_tag}"
                    )
                rg_trace = []
                if trace:
                    rg_trace = list(symbol_traces[symbol])
                    symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} (CONT...) ---"]

                entry = make_gain_entry(
                    chunk_qty=chunk_qty,
                    chunk_cost=chunk_cost,
                    chunk_proceeds=chunk_proceeds,
                    raw_gain=raw_gain,
                    allowed_gain=allowed_gain,
                    disallowed_amt=disallowed_amt,
                    permanently_disallowed_amt=permanently_disallowed_amt,
                    replacement_ids=replacement_ids,
                    wash_reps=wash_reps,
                    acq_for_holding=acq_for_holding,
                    days_held=days_held,
                    is_long_term=is_long_term,
                    direction='LONG',
                    rg_trace=rg_trace,
                    raw_acq_date=lot['date'],
                    tainted=lot.get('tainted', False),
                )
                realized_gains.append(entry)

                total_disallowed = disallowed_amt + permanently_disallowed_amt
                if total_disallowed > epsilon:
                    wash_sale_records.append({
                        'loss_tx_id': tx.id,
                        'symbol': symbol,
                        'date': tx.date,
                        'date_settle': tx.date_settle or tx.date,  # see short-side note
                        'disallowed_amount': total_disallowed,
                        'permanently_disallowed': permanently_disallowed_amt,
                        'qty': chunk_qty,
                        'replacement_lot_ids': replacement_ids,
                        'direction': 'LONG',
                    })
                qty_remaining -= chunk_qty

            # --- sell-to-open: any leftover quantity opens a new short lot ---
            if qty_remaining > epsilon:
                rep_record = next(
                    (r for r in short_replacements.get(_rep_key(symbol), [])
                     if r['tx'].id == tx.id and not r['is_sheltered']),
                    None,
                )
                leftover_share = qty_remaining / tx_qty_abs
                leftover_proceeds_d = D(leftover_share) * D(tx_net)
                # Apportion the remaining option-assignment premium to
                # the leftover short open (mirror of the close-long
                # application above so the full premium is consumed).
                # Short-call assignment: premium boosts the opening
                # proceeds of the resulting short. Without this the
                # leftover share was silently discarded.
                if option_adj_sell != 0:
                    leftover_proceeds_d -= D(option_adj_sell * leftover_share)
                if rep_record is not None:
                    # Wash-sale carryover: pending proceeds reduction baked in.
                    leftover_proceeds_d -= rep_record['pending_proceeds_reduction']
                short_lot = {
                    'qty': qty_remaining,
                    'proceeds': leftover_proceeds_d,
                    'date': tx.date,
                    'effective_open_date': tx.date,
                    'id': tx.id,
                    # Deferral already baked into leftover_proceeds_d —
                    # surfaced so inventory reports show it.
                    'wash_deferred': (rep_record['pending_proceeds_reduction']
                                      if rep_record is not None
                                      else Decimal(0)),
                }
                inventory_short[ikey].append(short_lot)
                if rep_record is not None:
                    rep_record['short_lot_ref'] = short_lot
                    rep_record['pending_proceeds_reduction'] = Decimal(0)
                if trace:
                    symbol_traces[symbol].append(
                        f"# {tx.date} SELL-OPEN {qty_remaining:10.4f} @ {tx.price:10.4f} | "
                        f"Short_Proc: {float(short_lot['proceeds']):10.4f} | "
                        f"Effective_Open: {short_lot['effective_open_date']}"
                    )

        # Dividends — report the gross dividend (1099-DIV box 1a). Withheld
        # foreign tax flows via separate TAX records into the foreign tax
        # credit, not the dividend income line.
        for tx in transactions:
            # PIL gets its own entry so sum-gains can show it in a dedicated
            # column. Kept out of the eligible-dividend total — IRS treats
            # PIL as a non-qualified substitute payment (ordinary income).
            if tx.action == 'DIVIDEND_IN_LIEU' or tx.type == 'dividend_in_lieu':
                pil_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
                realized_gains.append({
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'symbol': tx.symbol, 'qty': tx.quantity,
                    'currency': tx.currency, 'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                    'pil': pil_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND_IN_LIEU',
                })
                continue

            if tx.action == 'DIVIDEND':
                # Convention: parsers set gross_amount as the pre-withholding
                # dividend (e.g. RBC computes net/0.85 when description shows
                # NON-RES TAX WITHHELD); they leave it at the dataclass
                # default 0.0 when only net is known. We use gross if it's
                # truthy (positive — actual dividends are always > 0), else
                # net. Treating 0 and unset identically is intentional here:
                # a 0-gross "dividend" is contradictory data — no real
                # parser emits gross=0 + net>0 for a real dividend, and a
                # return-of-capital event should use action='ADJUST', not
                # DIVIDEND. If you ever need to distinguish "explicit zero"
                # from "missing", introduce a sentinel field on
                # TaxTransaction rather than changing this check.
                div_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
                realized_gains.append({
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'symbol': tx.symbol, 'qty': tx.quantity,
                    'currency': tx.currency, 'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                    'dividend': div_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND',
                })

        # Invariant: across all close events (long and short), the
        # difference between allowed and raw gain must equal the total
        # disallowed amount. Permanently disallowed losses also add back.
        sell_entries = [g for g in realized_gains if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        sum_allowed = sum(g['gain'] for g in sell_entries)
        sum_raw = sum(g.get('raw_gain', g['gain']) for g in sell_entries)
        sum_disallowed = sum(g.get('disallowed_amount', 0.0) for g in sell_entries)
        if abs((sum_allowed - sum_raw) - sum_disallowed) > 0.01:
            print(
                f"warning: US gain disallowance invariant broken — "
                f"allowed-raw={sum_allowed - sum_raw:.4f} vs disallowed={sum_disallowed:.4f}",
                file=sys.stderr,
            )
        _warn_undrained_adjustments(pending_option_adjustments, "usa")

        # Warn-only option-as-replacement scan (user policy; numbers are
        # never changed — gated on cross_asset). Losses on the TRADE
        # basis, matching the §1091 window convention. No held-at-end
        # requirement in §1091 (that is a CRA s.54 condition).
        option_replacement_warnings: List[Dict[str, Any]] = []
        if cross_asset:
            _orw_losses = [
                {'symbol': g['symbol'],
                 'date': g['date'],
                 'amount': g.get('raw_gain', g['gain']),
                 'id': g.get('id', ''),
                 'direction': g.get('direction', 'LONG')}
                for g in sell_entries
                if g.get('raw_gain', g['gain']) < -0.005
                and not g.get('tainted')]
            option_replacement_warnings = detect_option_replacement_matches(
                _orw_losses, all_events,
                date_of=lambda t: t.date,
                canonical=split_timeline.canonical,
                statute_label="IRS §1091 ('option to acquire')",
                check_held_at_end=False)
            _emit_option_replacement_stderr(option_replacement_warnings)

        # Conservation post-condition (see _verify_share_conservation).
        _inv_qty: Dict[str, float] = {}
        for _k, _lots in inventory_long.items():
            _sym = _k[1] if per_account_basis else _k
            _inv_qty[_sym] = _inv_qty.get(_sym, 0.0) \
                + sum(l['qty'] for l in _lots)
        for _k, _lots in inventory_short.items():
            _sym = _k[1] if per_account_basis else _k
            _inv_qty[_sym] = _inv_qty.get(_sym, 0.0) \
                - sum(l['qty'] for l in _lots)
        _verify_share_conservation(
            [t for t in taxable_sorted
             if not non_capital(t.action, t.type)
             and t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                              'SPLIT')],
            _inv_qty, 'usa',
            zero_ratio_skips=True)      # US: ratio-0 skips the scale,
                                        # rename still migrates

        by_ticker: Dict[str, Dict[str, Any]] = {}
        for g in realized_gains:
            # Mirror the Canada engine: tainted dispositions (phantom
            # OPENING_BALANCE consumption) carry fabricated cost=0
            # numbers and the CLI splits them out into
            # `manual_reporting_required` after this point. Letting them
            # into by_ticker totals would silently inflate any
            # downstream consumer reading by_ticker rather than
            # transactions.
            if g.get('tainted'):
                continue
            s = g['symbol']
            if s not in by_ticker:
                by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
            stats = by_ticker[s]
            if g.get('action') == 'DIVIDEND':
                stats['total_div'] += g.get('dividend', 0.0)
            elif g.get('action') == 'DIVIDEND_IN_LIEU':
                stats['total_pil'] += g.get('pil', 0.0)
            else:
                stats['total_cost'] += g['cost']
                stats['total_proceeds'] += g['proceeds']
                stats['total_gain'] += g['gain']
                stats['trade_count'] += 1
                stats['hold_days'].append(g.get('days_held', 0))

        # Inventory report includes both unsold long lots and unclosed short
        # lots (short qty reported as a negative number for clarity).
        # Currency comes from the symbol_currency map built at pass start;
        # falls back to '' if a symbol somehow has no currency on any tx
        # (defensive — taxjson-export tolerates the empty case).
        inventory_report = []
        for s, lots in inventory_long.items():
            _acct = s[0] if per_account_basis else ''
            s = s[1] if per_account_basis else s
            tot_qty = sum(l['qty'] for l in lots)
            if tot_qty > epsilon:
                inventory_report.append({
                    'symbol': s, 'qty': tot_qty,
                    **({'account': _acct} if _acct else {}),
                    'total_cost': float(sum(l['cost_basis'] for l in lots)),
                    'currency': symbol_currency.get(s, ''),
                    # Earliest acquisition date among the REMAINING
                    # lots. After a FIFO close, the consumed lots are
                    # gone, so `min` correctly returns the new
                    # position-start when the original lots have all
                    # been closed and replaced by later buys.
                    'position_start_date': min(l['date'] for l in lots),
                    # Most recent surviving lot — the wash-window signal.
                    'last_acq_date': max(l['date'] for l in lots),
                    # §1091 basis bumps still embedded in surviving lots.
                    'deferred_wash': round(float(sum(
                        l.get('wash_deferred', Decimal(0))
                        for l in lots)), 4),
                })
        for s, lots in inventory_short.items():
            _acct = s[0] if per_account_basis else ''
            s = s[1] if per_account_basis else s
            tot_qty = sum(l['qty'] for l in lots)
            if tot_qty > epsilon:
                inventory_report.append({
                    'symbol': s, 'qty': -tot_qty,  # signed: negative = short
                    **({'account': _acct} if _acct else {}),
                    # NEGATIVE: opening proceeds credited — the repo-wide
                    # short-inventory convention (cost_per_share =
                    # total_cost/qty comes out positive; see
                    # test_holdings_toml / test_taxjson_export_report).
                    'total_cost': float(-sum(l['proceeds'] for l in lots)),
                    'currency': symbol_currency.get(s, ''),
                    'position_start_date': min(l['date'] for l in lots),
                    'last_acq_date': max(l['date'] for l in lots),
                    'deferred_wash': round(float(sum(
                        l.get('wash_deferred', Decimal(0))
                        for l in lots)), 4),
                })

        return {
            'transactions': realized_gains,
            'by_ticker': by_ticker,
            'wash_sales': wash_sale_records,
            'option_replacement_warnings': option_replacement_warnings,
            'inventory': inventory_report,
            'summary': {
                'total_gain': sum(g['gain'] for g in realized_gains),
                'total_disallowed': sum(w['disallowed_amount'] for w in wash_sale_records),
                'count': len(realized_gains),
            },
        }

def get_tax_rules(country: str) -> TaxRules:
    if country.lower() in ("canada", "ca"):
        return CanadaTaxRules()
    if country.lower() in ("usa", "us"):
        return USATaxRules()
    raise ValueError(f"Unsupported country: {country}")
