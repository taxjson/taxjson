"""Generic column-mapped CSV importer — the escape hatch for brokers
without a dedicated parser.

Name the export `generic_<anything>.csv` and describe its layout in a
TOML mapping: either a sidecar (`generic_<anything>.csv.toml`, wins) or
one shared `generic.toml` in the same folder. Example:

    [columns]                   # CSV header names (case-insensitive)
    date     = "Trade Date"     # required; the TRADE date
    settle   = "Settle Date"    # optional; else computed (see below)
    action   = "Type"           # required unless defaults.action
    symbol   = "Ticker"         # required unless defaults.symbol
    quantity = "Shares"
    price    = "Price"
    amount   = "Net Amount"     # fee-INCLUSIVE signed total
    fee      = "Commission"
    currency = "Currency"       # else defaults.currency

    [formats]
    date = "%m/%d/%Y"           # strptime; default %Y-%m-%d
    settle = "%m/%d/%Y"         # optional; default = formats.date
    tax_sign = "cash"           # or "withheld" (positive = withheld)
    fee_sign = "cash"           # or "charged" (positive = charged)

    [actions]                   # CSV action value -> taxjson action
    "BUY"  = "buy"              # buy | sell | dividend | tax |
    "SELL" = "sell"             #   interest | fee | skip
    "DIV"  = "dividend"
    "WHT"  = "tax"
    "JNL"  = "skip"             # explicit ignore (still counted)

    [defaults]
    currency = "CAD"            # required unless [columns].currency

    [options]
    allow_large_fees = false    # fee > 5% of gross refused unless true
    settle_on_trade_date = false  # true for crypto: no settlement cycle

The mapping is validated strictly: an unknown section or key (a typo
such as `ammount`, `commission` or `[format]`) is refused with a
suggestion, dividend/tax/interest/fee targets require `amount`, and
buy/sell targets require `quantity` plus `price` or `amount`.

Conventions match the hand-written parsers: quantity is stored
magnitude-signed by direction (buys positive, sells negative), amounts
keep their CSV sign (a negative dividend is a reversal — never abs()).
A symbol written with an exchange suffix (.TO/.US/.AX/.L) keeps it,
and a Canadian venue (.V/.VN/.CN/.NE) is spelled .TO like every broker
parser spells it; a bare symbol takes the suffix of the row's currency
(CAD -> .TO, USD -> .US). Numbers accept a thousands comma (1,234.56)
and refuse a decimal comma (1234,56). Trade rows settle on the mapped
`settle` column when filled, else on the standard cycle from the
holiday-aware lib.dates.settlement_date (T+1 since May 2024, T+2
before, T+3 before 2017-09-05; options T+1) on the listing's market;
income rows are dated `date`. Symbols are upper-cased (`xyz` and `XYZ`
are one security). Unmapped action values are counted and summarized,
never silently dropped — one that carries a quantity or an amount is an
UNBOOKED warning (echoed by `taxjson run`, refused by `--strict`, a
failure under `taxjson-brokerage --lint`); map it, or map it to `skip`.
A `fee` row is booked positive = charged (the IB/Questrade/RBC
convention): a cash-signed CSV (negative = charged, the default
`[formats].fee_sign = "cash"`) is flipped; `fee_sign = "charged"` takes
the cell as is. A mapping that references columns the CSV doesn't have
refuses loudly.

Mis-mapped columns are the importer's worst failure mode — amounts in
the fee column once inflated a filed return by ~$41k without a word.
So every BUY/SELL row is cross-checked and the import REFUSES when:

* two logical fields name the same CSV header (e.g. fee = amount);
* |amount| is not |qty| × price × multiplier ± fee within 1% (+$0.05)
  — the multiplier is 100 for an OCC option symbol; with no `fee`
  column mapped, the fee is INFERRED as the gap between |amount| and
  qty × price (a commission-inclusive Net column with no commission
  column) and a buy whose amount is below the gross is refused;
* fee is more than 5% of the gross (qty × price × multiplier), unless
  `[options] allow_large_fees = true`; a row with no price is checked
  against |amount| instead (fee >= a buy's whole amount is refused
  outright) — the missing price never switches the check off;
* a futures symbol (`F:` or `/` prefix) has no `amount` — the contract
  size is never guessed, so its fee-inclusive total must come from the
  CSV (futures rows skip the qty × price comparison);
* a record has more cells than the header, or a cell holding a line
  break (an unescaped quote swallowed the next row);

* the quantity is blank or zero, or neither price nor amount is given
  (the fee alone became the cost), or a stock buy costs exactly 0;
* the quantity/amount sign contradicts the mapped action (a negative
  quantity under a `buy` action, a positive quantity with a negative
  amount under `sell`, or a positive-quantity sell in a file whose
  other sells carry negative quantities);
* an income row's amount cell is blank;

and WARNS when amount equals qty × price to the cent while the fee is
nonzero (the GROSS column is probably mapped as `amount`). The
currency must be mapped or set in [defaults] — there is no silent USD.
"""

import csv
import difflib
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.tomlcompat import tomllib

from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         canonical_ca_listing,
                                         parse_strict_number)
from taxjson.lib.core import is_option_symbol
from taxjson.lib.dates import settlement_date

# Row-level cross-check tolerance — the same 1% + $0.05 the .tt
# converter uses for hand-entered totals.
_REL_TOL = 0.01
_ABS_TOL = 0.05
# A fee above this share of the gross is almost always a column mix-up
# (the amount or the gross in the fee column).
_MAX_FEE_SHARE = 0.05
_OPTIONS = ("allow_large_fees", "settle_on_trade_date")

_VALID_TARGETS = ("buy", "sell", "dividend", "tax", "interest", "fee",
                  "skip")
# Income-style targets book the amount cell as the money: it must be
# mapped and non-blank (an unmapped amount booked every dividend as 0).
_INCOME_TARGETS = ("dividend", "tax", "interest", "fee")

# The mapping vocabulary. Anything else is a typo that used to be
# ignored without a word (`ammount`, `commission`, `[format]`,
# `tax_sgn`), so it is refused with a suggestion.
_SECTIONS = ("columns", "formats", "actions", "defaults", "options")
_COLUMN_KEYS = ("date", "settle", "action", "symbol", "quantity", "price",
                "amount", "fee", "currency")
_FORMAT_KEYS = ("date", "settle", "tax_sign", "fee_sign")
_DEFAULT_KEYS = ("currency", "action", "symbol")
# Common spellings difflib cannot guess.
_KEY_ALIASES = {
    "commission": "fee", "commissions": "fee", "fees": "fee",
    "qty": "quantity", "shares": "quantity", "units": "quantity",
    "ticker": "symbol", "security": "symbol",
    "type": "action", "transaction_type": "action",
    "net": "amount", "net_amount": "amount", "total": "amount",
    "value": "amount",
    "settlement": "settle", "settle_date": "settle",
    "settlement_date": "settle", "date_settle": "settle",
    "trade_date": "date", "ccy": "currency",
    "format": "formats", "action_map": "actions", "default": "defaults",
    "option": "options", "column": "columns",
}
# Exchange suffixes a user may write explicitly. One of these on the
# symbol is the LISTING and is kept; the row currency is only the
# settlement currency (DLR.U.TO bought in USD is still DLR.U.TO).
_KNOWN_SUFFIXES = ("TO", "V", "CN", "NE", "US", "AX", "L")
_CA_SUFFIXES = (".TO", ".V", ".CN", ".NE")
# Futures symbol prefixes (lib/futures.py): the contract size is not in
# the row, so it is never guessed.
_FUTURES_PREFIXES = ("F:", "/", "\\")


def _did_you_mean(key: str, valid) -> str:
    k = str(key).strip().lower()
    alias = _KEY_ALIASES.get(k)
    if alias in valid:
        return f" Did you mean {alias!r}?"
    guess = difflib.get_close_matches(k, list(valid), n=1, cutoff=0.6)
    return f" Did you mean {guess[0]!r}?" if guess else ""


def _check_keys(path_name: str, section: str, table, valid) -> None:
    for k in table:
        if k not in valid:
            raise ValueError(
                f"generic importer: {path_name}: unknown [{section}].{k}."
                f"{_did_you_mean(k, valid)} Valid keys: "
                f"{', '.join(valid)}. An unknown key used to be ignored "
                f"silently (and its column never read).")


def _load_mapping(csv_path: Path) -> Dict[str, Any]:
    sidecar = csv_path.with_name(csv_path.name + ".toml")
    shared = csv_path.parent / "generic.toml"
    path = sidecar if sidecar.exists() else shared
    if not path.exists():
        raise ValueError(
            f"generic importer: no mapping for {csv_path.name} — write "
            f"{sidecar.name} (or a shared generic.toml in the same "
            f"folder). See examples/generic_wealthsimple.toml for a "
            f"template.")
    if tomllib is None:                  # pragma: no cover
        raise ValueError(
            "generic importer: TOML support unavailable — install "
            "`tomli` (Python < 3.11).")
    try:
        mapping = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"generic importer: {path.name}: bad TOML: {e}")
    for sec, val in mapping.items():
        if sec not in _SECTIONS:
            raise ValueError(
                f"generic importer: {path.name}: unknown section "
                f"[{sec}].{_did_you_mean(sec, _SECTIONS)} Valid sections: "
                f"{', '.join('[' + x + ']' for x in _SECTIONS)}.")
        if not isinstance(val, dict):
            raise ValueError(
                f"generic importer: {path.name}: {sec} must be a "
                f"[{sec}] table, got {val!r}")
    cols = mapping.get("columns") or {}
    _check_keys(path.name, "columns", cols, _COLUMN_KEYS)
    _check_keys(path.name, "formats", mapping.get("formats") or {},
                _FORMAT_KEYS)
    _check_keys(path.name, "defaults", mapping.get("defaults") or {},
                _DEFAULT_KEYS)
    for k, v in cols.items():
        if not isinstance(v, str):
            raise ValueError(
                f"generic importer: {path.name}: [columns].{k} must be "
                f"a quoted header NAME, got {v!r}")
    defaults = mapping.get("defaults") or {}
    # Two logical fields on ONE header: fee = "Net" next to amount =
    # "Net" booked the whole trade value as commission.
    by_header: Dict[str, List[str]] = {}
    for k, v in cols.items():
        by_header.setdefault(v.strip().lower(), []).append(k)
    shared = {h: ks for h, ks in by_header.items() if len(ks) > 1}
    if shared:
        detail = "; ".join(f"{', '.join(ks)} -> {h!r}"
                           for h, ks in sorted(shared.items()))
        raise ValueError(
            f"generic importer: {path.name}: several [columns] fields "
            f"map to the SAME CSV header ({detail}) — each logical field "
            f"needs its own column. A fee/amount mix-up books wrong "
            f"money silently; fix the mapping.")
    if "currency" not in cols and not str(
            defaults.get("currency", "")).strip():
        raise ValueError(
            f"generic importer: {path.name}: no currency — map "
            f"[columns].currency or set [defaults].currency (e.g. "
            f"\"CAD\"). There is no implicit USD default: a CAD account "
            f"read as USD is converted at the wrong rate.")
    options = mapping.get("options") or {}
    for k, v in options.items():
        if k not in _OPTIONS:
            raise ValueError(
                f"generic importer: {path.name}: unknown [options].{k}."
                f"{_did_you_mean(k, _OPTIONS)} (valid: "
                f"{', '.join(_OPTIONS)})")
        if not isinstance(v, bool):
            raise ValueError(
                f"generic importer: {path.name}: [options].{k} must be "
                f"true or false, got {v!r}")
    if "date" not in cols:
        raise ValueError(f"generic importer: {path.name}: "
                         f"[columns].date is required")
    if "action" not in cols and "action" not in defaults:
        raise ValueError(f"generic importer: {path.name}: map "
                         f"[columns].action or set [defaults].action")
    if "symbol" not in cols and "symbol" not in defaults:
        raise ValueError(f"generic importer: {path.name}: map "
                         f"[columns].symbol or set [defaults].symbol")
    for raw, target in (mapping.get("actions") or {}).items():
        if target not in _VALID_TARGETS:
            raise ValueError(
                f"generic importer: {path.name}: [actions] {raw!r} maps "
                f"to unknown target {target!r} (valid: "
                f"{', '.join(_VALID_TARGETS)})")
    # Required fields per target actually used.
    targets = set((mapping.get("actions") or {}).values())
    income = sorted(targets & set(_INCOME_TARGETS))
    if income and "amount" not in cols:
        raise ValueError(
            f"generic importer: {path.name}: [actions] map to "
            f"{', '.join(income)} but [columns].amount is not mapped — "
            f"income/tax/fee rows book the amount cell, and without it "
            f"every such row would be booked as 0.")
    if targets & {"buy", "sell"}:
        if "quantity" not in cols:
            raise ValueError(
                f"generic importer: {path.name}: [actions] map to buy/"
                f"sell but [columns].quantity is not mapped.")
        if "price" not in cols and "amount" not in cols:
            raise ValueError(
                f"generic importer: {path.name}: [actions] map to buy/"
                f"sell but neither [columns].price nor [columns].amount "
                f"is mapped — the cost/proceeds would be the fee alone.")
    mapping["_path"] = path.name
    return mapping


def _check_trade_row(where: str, target: str, qty: float, price: float,
                     amount: float, fee: float, mult: float,
                     allow_large_fees: bool, fee_mapped: bool = True,
                     is_future: bool = False) -> float:
    """Refuse a BUY/SELL row whose numbers don't hang together — the
    signature of a mis-mapped column. See the module docstring. Returns
    the fee to book: the mapped fee, or — with no fee column mapped —
    the commission inferred from |amount| vs qty x price (S056-23)."""
    # A futures contract size is never guessed (S012-01): no qty x
    # price comparison is possible, so check against the amount only.
    gross = 0.0 if is_future else abs(qty) * abs(price) * mult
    fee = abs(fee)
    hint = ("check the [columns] mapping — is the fee, gross or amount "
            "column mapped to the wrong field?")
    if not fee_mapped and amount and gross > 0:
        # A commission-inclusive Net column with no commission column:
        # the gap between the net and qty x price IS the commission.
        # A negative gap (a buy's amount below the gross) is left for
        # the mismatch refusal below.
        inferred = (abs(amount) - gross if target == "buy"
                    else gross - abs(amount))
        if inferred > 0.005:
            fee = round(inferred, 6)
    if gross <= 0 and amount:
        # No price (unmapped, blank or 0) or a future: the qty x price
        # cross-check cannot run, but a fee against the booked total
        # still can — a swapped fee/amount mapping books the commission
        # as the cost (S011-03).
        if target == "buy" and fee and fee >= abs(amount) - 0.005:
            raise ValueError(
                f"generic importer: {where}: fee {fee:.2f} is not less "
                f"than the buy's whole amount {abs(amount):.2f} — {hint}")
        if fee and fee > _MAX_FEE_SHARE * abs(amount) \
                and not allow_large_fees:
            raise ValueError(
                f"generic importer: {where}: fee {fee:.2f} is "
                f"{fee / abs(amount):.0%} of the amount {abs(amount):.2f} "
                f"(no price to cross-check qty x price) — {hint} If the "
                f"fee really is that large, set `[options] "
                f"allow_large_fees = true` in the mapping.")
    if fee and gross > 0 and fee > _MAX_FEE_SHARE * gross \
            and not allow_large_fees:
        raise ValueError(
            f"generic importer: {where}: fee {fee:.2f} is "
            f"{fee / gross:.0%} of the gross {gross:.2f} "
            f"(qty {abs(qty):g} x price {abs(price):g}"
            f"{' x 100' if mult > 1 else ''}) — {hint} If the fee really "
            f"is that large (tiny odd-lot trades), set "
            f"`[options] allow_large_fees = true` in the mapping.")
    if amount and gross > 0:
        expected = gross + fee if target == "buy" else gross - fee
        # A sell whose commission exceeds its gross nets NEGATIVE (a
        # penny option close): its amount is fee - gross in magnitude,
        # whichever sign the file writes (S057-02).
        cmp = abs(expected)
        if abs(abs(amount) - cmp) > max(_ABS_TOL,
                                        _REL_TOL * max(cmp, 1.0)):
            raise ValueError(
                f"generic importer: {where}: amount {abs(amount):.2f} "
                f"differs from qty x price{' x 100' if mult > 1 else ''} "
                f"{'+' if target == 'buy' else '-'} fee = {expected:.2f} "
                f"(qty {abs(qty):g}, price {abs(price):g}, fee "
                f"{fee:g}) by more than 1% — {hint}")
        if fee and abs(abs(amount) - gross) < 0.005:
            print(f"warning: generic importer: {where}: amount "
                  f"{abs(amount):.2f} equals qty x price exactly while the "
                  f"fee is {fee:.2f} — `amount` must be the fee-INCLUSIVE "
                  f"net; is the GROSS column mapped as amount?",
                  file=sys.stderr)
    return fee


def _trade_net(fname: str, target: str, qty: float, price: float,
               amount: float, fee: float, mult: float = 1.0,
               is_future: bool = False) -> float:
    """Fee-inclusive trade total: the amount column when present, else
    derived. A SELL whose commission exceeds its gross (a penny option
    close, a worthless-position cleanup) nets NEGATIVE: the engine
    deducts those negative proceeds (core._trade_money) and the schema
    accepts them (S017-00). It used to be clamped to 0 — the excess
    commission never reached the loss — or, with an amount column,
    refused as a mis-mapping (S057-02)."""
    gross = 0.0 if is_future else abs(qty) * abs(price) * mult
    if amount:
        if target == "sell" and gross > 0 and abs(fee) > gross:
            return -abs(amount)
        return abs(amount)
    return gross + (abs(fee) if target == "buy" else -abs(fee))


class GenericBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Generic"

    def _listing_symbol(self, symbol_raw: str, currency: str) -> str:
        """The row's symbol with its exchange suffix. An explicit known
        suffix (.TO/.V/.CN/.NE/.US/.AX/.L) is the LISTING and is kept:
        the row currency is only what the trade settled in, and deriving
        the suffix from it turned DLR.U.TO bought in USD into DLR.U.US —
        a different identity, so a superficial loss across accounts was
        missed (audit R1-122). A bare symbol still takes its suffix from
        the currency (XEI in CAD -> XEI.TO)."""
        sym = symbol_raw.strip()
        # One security, one spelling (audit S010-04): the ACB pool and
        # the superficial-loss match key on the exact symbol string.
        #  * An OCC symbol padded to 21 characters ('XYZ   250321C...')
        #    is the compact contract, not 'XYZ...250321C...'.
        #  * A class-share separator '-' or '/' is the dot every broker
        #    parser uses: BRK-B and BRK/B are BRK.B.
        #  * A broker option DESCRIPTION ('XYZ 21MAR25 50 C', 'CALL XYZ
        #    03/21/25 50') is not a symbol: booked as a share, it never
        #    expired. Refused — give the OCC symbol.
        _occ = re.match(r'^([A-Za-z0-9.]+)\s+(\d{6}[CPcp]\d{8})$', sym)
        if _occ:
            sym = _occ.group(1) + _occ.group(2).upper()
        elif (re.match(r'^\S+\s+\d{1,2}[A-Za-z]{3}\d{2}\s+[\d.,]+\s+[CPcp]$',
                       sym)
              or re.match(r'^(?:CALL|PUT)\s', sym, re.IGNORECASE)):
            raise BrokerageParseError(
                f"generic importer: symbol {symbol_raw!r} is an option "
                f"DESCRIPTION, not a symbol — give the OCC symbol (e.g. "
                f"XYZ250321C00050000) so the contract is an option.")
        _cls = re.match(r'^([A-Za-z0-9]+)[-/]([A-Za-z]{1,2})(\.[A-Za-z]{1,2})?$',
                        sym)
        if _cls:
            sym = f"{_cls.group(1)}.{_cls.group(2)}{_cls.group(3) or ''}"
        # Upper-cased: `xyz` and `XYZ` are one security; left as typed
        # they were two ACB pools and a sale opened a phantom short
        # (R1-128).
        sym = sym.replace(" ", ".").upper()
        up = sym
        # A Canadian venue (.TO/.V/.VN/.CN/.NE) is one Canadian listing,
        # spelled ROOT.TO by every broker parser (base.canonical_ca_
        # listing, audit S010-05): an explicit .V here must not split
        # the pool from the same shares bought at IB. The venue, not the
        # row currency, decides -- DLR.U.TO bought in USD stays .TO.
        for suf in ("TO", "V", "VN", "CN", "NE"):
            if up.endswith("." + suf) and len(up) > len(suf) + 1:
                return canonical_ca_listing(sym, "CAD")
        for suf in _KNOWN_SUFFIXES:
            if up.endswith("." + suf) and len(up) > len(suf) + 1:
                return sym[:-len(suf)] + suf
        return self.apply_currency_suffix(sym, currency)

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        mapping = _load_mapping(path)
        cols: Dict[str, str] = {k: v for k, v in
                                (mapping.get("columns") or {}).items()}
        actions: Dict[str, str] = {
            str(k).upper(): v for k, v in
            (mapping.get("actions") or {}).items()}
        defaults = mapping.get("defaults") or {}
        options = mapping.get("options") or {}
        allow_large_fees = bool(options.get("allow_large_fees", False))
        settle_on_trade_date = bool(options.get("settle_on_trade_date",
                                                False))
        formats = mapping.get("formats") or {}
        date_fmt = formats.get("date", "%Y-%m-%d")
        settle_fmt = formats.get("settle", date_fmt)
        tax_sign = formats.get("tax_sign", "cash")
        fee_sign = formats.get("fee_sign", "cash")
        if fee_sign not in ("cash", "charged"):
            raise ValueError(
                f"generic importer: {mapping['_path']}: [formats]."
                f"fee_sign must be 'cash' (negative = charged, the "
                f"default) or 'charged' (positive = charged), got "
                f"{fee_sign!r}")
        fee_mapped = "fee" in cols
        # Parser-reported problems `taxjson-brokerage --lint` fails on.
        self.lint_findings: List[str] = []
        unbooked: List[str] = []
        if tax_sign not in ("cash", "withheld"):
            raise ValueError(
                f"generic importer: {mapping['_path']}: [formats]."
                f"tax_sign must be 'cash' (negative = withheld, the "
                f"default) or 'withheld' (positive = withheld), got "
                f"{tax_sign!r}")

        transactions: List[Dict[str, Any]] = []
        # (where, target, raw signed quantity) per trade row, for the
        # file-level sign-convention check after the loop.
        trade_signs: List[tuple] = []
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as e:
            raise ValueError(
                f"generic importer: {path.name} is not UTF-8 ({e}) — "
                f"re-export as UTF-8 CSV (UTF-16/latin-1 exports must "
                f"be converted first).")
        import io as _io
        if True:
            reader = csv.DictReader(_io.StringIO(text))
            fieldnames = reader.fieldnames or []
            header: Dict[str, str] = {}
            _dupes = set()
            for h in fieldnames:
                key = (h or "").strip().lower()
                if key in header:
                    _dupes.add(key)
                header[key] = h
            # A MAPPED column that exists more than once is ambiguous
            # (real exports carry twin Amount columns — native vs
            # account currency); DictReader silently keeps the last.
            _ambig = [f"{k} -> {v!r}" for k, v in cols.items()
                      if v.strip().lower() in _dupes]
            if _ambig:
                raise ValueError(
                    f"generic importer: {path.name}: mapped column(s) "
                    f"appear MORE THAN ONCE in the header — rename the "
                    f"duplicates or map the exact one you mean: "
                    f"{', '.join(_ambig)}. Header: {fieldnames}.")
            missing = [f"{k} -> {v!r}" for k, v in cols.items()
                       if v.strip().lower() not in header]
            if missing:
                raise ValueError(
                    f"generic importer: {path.name}: mapped column(s) "
                    f"not in the CSV header: {', '.join(missing)}. "
                    f"Header: {fieldnames}. Fix "
                    f"{mapping['_path']}.")

            def num(row, field, where) -> Optional[float]:
                """STRICT numeric, None for a blank or unmapped cell.
                The importer's charter is refuse-loudly: clean_number's
                garbage->0.0 silently booked $0-basis buys from
                'C$10.00'-style cells, and stripping every comma read a
                decimal comma ('12,50') as 1250 (audit R1-118). A comma
                is only a thousands separator here; accounting
                parentheses are negative ('(138.00)' == -138.00)."""
                raw = str(cell(row, field, "")).strip()
                if not raw:
                    return None
                s = raw
                for pre in ("CA$", "C$", "US$", "A$", "$", "€", "£"):
                    s = s.replace(pre, "")
                s = s.strip()
                try:
                    return parse_strict_number(s, field=field)
                except BrokerageParseError:
                    why = (" — a decimal comma? Only a thousands comma "
                           "(1,234.56) is accepted; re-export with a "
                           "decimal point" if "," in s else "")
                    raise ValueError(
                        f"generic importer: {where}: unparseable "
                        f"{field} value {raw!r}{why} — fix the cell or "
                        f"the [columns].{field} mapping.")

            def cell(row, field, default=""):
                col = cols.get(field)
                if not col:
                    return default
                return (row.get(header[col.strip().lower()]) or default)

            for row in reader:
                if not any((v or "").strip() for v in row.values()
                           if isinstance(v, str) or v is None):
                    continue
                # A record wider than the header, or a cell holding a
                # line break: an unescaped quote in a text cell swallowed
                # the following row(s) and the columns now carry another
                # row's numbers — every cross-check can still pass
                # (S057-08). Refuse, naming the line.
                _extra = row.get(None)
                _nl = [k for k, v in row.items()
                       if k is not None and isinstance(v, str)
                       and ("\n" in v or "\r" in v)]
                if _extra or _nl:
                    raise ValueError(
                        f"generic importer: {path.name} record ending on "
                        f"line {reader.line_num}: "
                        + (f"{len(_extra)} cell(s) more than the header"
                           if _extra else
                           f"column {_nl[0]!r} holds a line break")
                        + " — an unescaped quote in a text cell usually "
                        "swallows the next row(s) this way. Fix the quoting "
                        "(double an inner quote: \"\") and re-run.")
                raw_action = (str(cell(row, "action")
                                  or defaults.get("action", ""))
                              .strip().upper())
                target = actions.get(raw_action)
                if target is None:
                    self.count_skip(f"action {raw_action or '?'!s}")
                    # A row that moves shares or cash is a real event
                    # the mapping does not book (a DRIP reinvest left
                    # the position short): surfaced as UNBOOKED, not
                    # just counted (R1-129).
                    _moves = False
                    for _f in ("quantity", "amount"):
                        try:
                            _v = num(row, _f, "")
                        except ValueError:
                            _v = 1.0        # unparseable: treat as live
                        if _v:
                            _moves = True
                    if _moves:
                        _w = (f"{path.name} line {reader.line_num}: "
                              f"action {raw_action or '?'!r} (not in "
                              f"[actions]) carries a quantity/amount")
                        unbooked.append(_w)
                        self.lint_findings.append(
                            f"{_w} — not booked")
                    continue
                if target == "skip":
                    self.count_skip(f"action {raw_action} (mapped skip)")
                    continue
                self.note_row_consumed()

                date_raw = str(cell(row, "date")).strip()
                dt = self.parse_date(date_raw, date_fmt)
                if dt is None:
                    # Same policy as Coinbase/Kraken: a silently
                    # stamped or dropped date distorts years/holding
                    # periods invisibly.
                    raise ValueError(
                        f"generic importer: {path.name}: unparseable "
                        f"date {date_raw!r} with [formats].date="
                        f"{date_fmt!r}")
                date = dt.strftime("%Y-%m-%d")
                where = f"{path.name} line {reader.line_num}"
                currency = (str(cell(row, "currency")).strip()
                            or str(defaults.get("currency", ""))
                            ).strip().upper()
                if not currency:
                    raise ValueError(
                        f"generic importer: {where}: empty currency cell "
                        f"and no [defaults].currency in "
                        f"{mapping['_path']} — refusing to assume USD.")
                symbol_raw = (str(cell(row, "symbol")).strip()
                              or str(defaults.get("symbol", "")))
                symbol = (self._listing_symbol(symbol_raw, currency)
                          if symbol_raw else "")
                qty_v = num(row, "quantity", where)
                price_v = num(row, "price", where)
                amount_v = num(row, "amount", where)
                fee_v = num(row, "fee", where)
                qty = qty_v or 0.0
                price = price_v or 0.0
                amount = amount_v or 0.0
                fee = fee_v or 0.0

                if target in ("buy", "sell"):
                    mult = (float(self.OPTION_MULTIPLIER)
                            if is_option_symbol(symbol_raw)
                            or is_option_symbol(symbol) else 1.0)
                    is_future = symbol.startswith(_FUTURES_PREFIXES)
                    self._check_trade_shape(
                        where, target, raw_action, qty, price_v, amount_v,
                        fee, mult, is_future)
                    trade_signs.append((where, target, qty))
                    fee = _check_trade_row(
                        where, target, qty, price, amount, fee, mult,
                        allow_large_fees, fee_mapped, is_future)
                    date_settle = self._settle_for(
                        row, cell, where, date, settle_fmt, symbol,
                        currency, mult > 1, settle_on_trade_date)
                    transactions.append({
                        "action": "BUYSELL",
                        "date": date, "time": "09:30:00",
                        "date_settle": date_settle,
                        "symbol": symbol,
                        "quantity": self.signed_quantity(
                            qty, action_is_sell=(target == "sell")),
                        "currency": currency,
                        "price": abs(price),
                        # Amount column when present (fee-inclusive,
                        # magnitude); else derive from qty*price±fee.
                        "net_amount": _trade_net(
                            path.name, target, qty, price, amount, fee,
                            mult, is_future),
                        "gross_amount": (
                            # Futures: the implied qty x price x size.
                            abs(amount) + (abs(fee) if target == "sell"
                                           else -abs(fee))
                            if is_future else self.theoretical_gross(
                                qty, abs(price), is_option=(mult > 1))),
                        "fee": abs(fee),
                        "account": self.DEFAULT_ACCOUNT,
                        "description": raw_action,
                    })
                    continue
                # Income-style rows: the amount IS the money. A blank
                # cell used to book 0 silently.
                if amount_v is None:
                    raise ValueError(
                        f"generic importer: {where}: {raw_action} row "
                        f"(-> {target}) has a blank amount cell — "
                        f"refusing to book it as 0. Fill it in, or map "
                        f"the action to skip if the row carries no "
                        f"money.")
                if target == "dividend":
                    transactions.append({
                        "action": "DIVIDEND",
                        "date": date, "time": "09:30:00",
                        # Income is dated its payment day: no
                        # settlement cycle.
                        "date_settle": date,
                        "symbol": symbol, "quantity": qty,
                        "currency": currency, "price": 0.0,
                        # SIGNED: reversals stay negative (schema).
                        "net_amount": amount,
                        "gross_amount": amount,
                        "type": "dividend",
                        "account": self.DEFAULT_ACCOUNT,
                        "description": raw_action,
                    })
                elif target in ("tax", "interest", "fee"):
                    transactions.append({
                        "action": target.upper(),
                        "date": date, "time": "09:30:00",
                        "date_settle": date,
                        "symbol": symbol, "quantity": 0.0,
                        "currency": currency,
                        # TAX convention: positive = withheld. Most
                        # CSVs book withholding as negative cash (flip,
                        # like IB/RBC); statements that book it POSITIVE
                        # set [formats].tax_sign = "withheld".
                        # FEE convention (IB/Questrade/RBC, fx-cash):
                        # positive = charged. A cash-signed CSV (the
                        # default) books a charge as negative cash —
                        # flipped, like tax (R1-124).
                        "net_amount": (
                            (amount if tax_sign == "withheld"
                             else -amount) if target == "tax"
                            else (amount if fee_sign == "charged"
                                  else -amount) if target == "fee"
                            else amount),
                        "type": target,
                        "account": self.DEFAULT_ACCOUNT,
                        "description": raw_action,
                    })
        # A file that signs its quantities (a sell row carried a
        # negative quantity) must not also carry a sell row with a
        # POSITIVE quantity: that row is a buy under the file's own
        # convention, booked as a sell through a mis-mapped action.
        if any(q < 0 for _, _, q in trade_signs):
            bad = [w for w, t, q in trade_signs if t == "sell" and q > 0]
            if bad:
                raise ValueError(
                    f"generic importer: {bad[0]}: a sell row with a "
                    f"POSITIVE quantity in a file whose other sell rows "
                    f"carry negative quantities — this row is a buy by "
                    f"the file's own sign convention. Check the "
                    f"[actions] mapping ({len(bad)} such row(s)).")
        if unbooked:
            shown = "; ".join(unbooked[:5])
            more = (f" (+{len(unbooked) - 5} more)"
                    if len(unbooked) > 5 else "")
            print(f"warning: UNBOOKED: generic importer: {len(unbooked)} "
                  f"row(s) with an unmapped action move shares or cash "
                  f"and are NOT in the books: {shown}{more}. Map the "
                  f"action in [actions] (or to \"skip\" if it really "
                  f"is not an event).", file=sys.stderr)
        self.disambiguate_split_fills(transactions)
        self.emit_skip_summary(path.name)
        return transactions

    @staticmethod
    def _check_trade_shape(where: str, target: str, raw_action: str,
                           qty: float, price_v: Optional[float],
                           amount_v: Optional[float], fee: float,
                           mult: float, is_future: bool = False) -> None:
        """Refuse a buy/sell row that cannot be booked without a guess:
        no quantity, no price AND no amount (the fee alone became the
        whole cost or proceeds, audit R1-120), a zero-cost stock buy,
        or a quantity/amount sign that contradicts the mapped action
        (a sell under an action mapped to buy was booked as a buy,
        audit R1-121)."""
        if not qty:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> "
                f"{target}) has a blank or zero quantity.")
        if is_future and not (amount_v or 0.0):
            raise ValueError(
                f"generic importer: {where}: {raw_action} row is a "
                f"futures contract with no amount — the contract size "
                f"(multiplier) is never guessed, so qty x price cannot "
                f"give the cost/proceeds. Map the fee-inclusive "
                f"[columns].amount (or enter the trade in a .tt file).")
        if price_v is None and amount_v is None:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> "
                f"{target}) has neither a price nor an amount — the "
                f"{'cost' if target == 'buy' else 'proceeds'} would be "
                f"{'the fee alone' if fee else '0'}. Fill one in, or map "
                f"the action to skip (a transfer-in needs its real cost "
                f"as a .tt BUYSELL).")
        if (target == "buy" and mult == 1.0 and not (price_v or 0.0)
                and not (amount_v or 0.0)):
            raise ValueError(
                f"generic importer: {where}: {raw_action} row is a stock "
                f"buy at ZERO cost (price and amount both 0). A zero-cost "
                f"buy is almost always a transfer or journal row — map "
                f"it to skip and supply the real cost basis (.tt).")
        if target == "buy" and qty < 0:
            raise ValueError(
                f"generic importer: {where}: {raw_action} is mapped to "
                f"buy but the quantity is NEGATIVE ({qty:g}) — a sell by "
                f"its own sign. Map sells to their own action value; "
                f"booking it as a buy would drop the disposition.")
        _gross = abs(qty) * abs(price_v or 0.0) * mult
        _neg_net = (fee > _gross > 0 and abs(abs(amount_v or 0.0)
                                             - (fee - _gross))
                    <= max(_ABS_TOL, _REL_TOL * (fee - _gross)))
        if (target == "sell" and qty > 0 and (amount_v or 0.0) < 0
                and not _neg_net):
            # (Not a sell whose commission exceeds its gross: that one
            # really is cash out — S057-02.)
            raise ValueError(
                f"generic importer: {where}: {raw_action} is mapped to "
                f"sell but the quantity is positive and the amount "
                f"negative ({amount_v:g}, cash paid out) — a buy by its "
                f"own signs. Map buys to their own action value.")

    @staticmethod
    def _settle_for(row, cell, where: str, date: str, settle_fmt: str,
                    symbol: str, currency: str, is_option: bool,
                    settle_on_trade_date: bool) -> str:
        """Settlement date of a trade row: the mapped `settle` column
        when the cell is filled, else the standard cycle from the
        shared holiday-aware helper (T+1 since the 2024 cutover, T+2
        before, T+3 before 2017-09-05; options T+1), on the listing's
        market (a Canadian suffix settles on the Canadian calendar,
        .US on the US one, otherwise the row currency). Booking the
        trade date put a Dec-31 sale in the wrong Canadian tax year
        (audits R1-123/R1-185). `[options] settle_on_trade_date = true`
        (crypto) and futures (`F:` symbols) settle on the trade date."""
        raw = str(cell(row, "settle")).strip()
        if raw:
            try:
                sd = datetime.strptime(raw, settle_fmt)
            except ValueError:
                raise ValueError(
                    f"generic importer: {where}: unparseable settle date "
                    f"{raw!r} with [formats].settle={settle_fmt!r}")
            settle = sd.strftime("%Y-%m-%d")
            if settle < date:
                raise ValueError(
                    f"generic importer: {where}: settle date {settle} is "
                    f"before the trade date {date} — are the date and "
                    f"settle columns swapped?")
            return settle
        if settle_on_trade_date or symbol.upper().startswith("F:"):
            return date
        up = symbol.upper()
        market = ("CAD" if up.endswith(_CA_SUFFIXES)
                  else "USD" if up.endswith(".US") else currency)
        return settlement_date(date, market, is_option)
