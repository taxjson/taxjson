"""Generic column-mapped CSV importer — the escape hatch for brokers
without a dedicated parser.

Name the export `generic_<anything>.csv` and describe its layout in a
TOML mapping: either a sidecar (`generic_<anything>.csv.toml`, wins) or
one shared `generic.toml` in the same folder. Example:

    [columns]                   # CSV header names (case-insensitive)
    date     = "Trade Date"     # required
    action   = "Type"           # required unless defaults.action
    symbol   = "Ticker"         # required unless defaults.symbol
    quantity = "Shares"
    price    = "Price"
    amount   = "Net Amount"     # fee-INCLUSIVE signed total
    fee      = "Commission"
    currency = "Currency"       # else defaults.currency

    [formats]
    date = "%m/%d/%Y"           # strptime; default %Y-%m-%d

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

Conventions match the hand-written parsers: quantity is stored
magnitude-signed by direction (buys positive, sells negative), amounts
keep their CSV sign (a negative dividend is a reversal — never abs()),
and the currency-of-listing suffix (.TO/.US) is applied from the row's
currency. Unmapped action values are counted and summarized, never
silently dropped; a mapping that references columns the CSV doesn't
have refuses loudly.

Mis-mapped columns are the importer's worst failure mode — amounts in
the fee column once inflated a filed return by ~$41k without a word.
So every BUY/SELL row is cross-checked and the import REFUSES when:

* two logical fields name the same CSV header (e.g. fee = amount);
* |amount| is not |qty| × price × multiplier ± fee within 1% (+$0.05)
  — the multiplier is 100 for an OCC option symbol;
* fee is more than 5% of the gross (qty × price × multiplier), unless
  `[options] allow_large_fees = true`;

and WARNS when amount equals qty × price to the cent while the fee is
nonzero (the GROSS column is probably mapped as `amount`). The
currency must be mapped or set in [defaults] — there is no silent USD.
"""

import csv
import sys
from pathlib import Path
from typing import Any, Dict, List

from taxjson.lib.tomlcompat import tomllib

from taxjson.lib.brokerages.base import BaseBrokerage
from taxjson.lib.core import is_option_symbol

# Row-level cross-check tolerance — the same 1% + $0.05 the .tt
# converter uses for hand-entered totals.
_REL_TOL = 0.01
_ABS_TOL = 0.05
# A fee above this share of the gross is almost always a column mix-up
# (the amount or the gross in the fee column).
_MAX_FEE_SHARE = 0.05
_OPTIONS = ("allow_large_fees",)

_VALID_TARGETS = ("buy", "sell", "dividend", "tax", "interest", "fee",
                  "skip")


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
    cols = mapping.get("columns") or {}
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
                f"generic importer: {path.name}: unknown [options].{k} "
                f"(valid: {', '.join(_OPTIONS)})")
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
    mapping["_path"] = path.name
    return mapping


def _check_trade_row(where: str, target: str, qty: float, price: float,
                     amount: float, fee: float, mult: float,
                     allow_large_fees: bool) -> None:
    """Refuse a BUY/SELL row whose numbers don't hang together — the
    signature of a mis-mapped column. See the module docstring."""
    gross = abs(qty) * abs(price) * mult
    fee = abs(fee)
    hint = ("check the [columns] mapping — is the fee, gross or amount "
            "column mapped to the wrong field?")
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
        if abs(abs(amount) - expected) > max(_ABS_TOL,
                                             _REL_TOL * max(expected, 1.0)):
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


def _trade_net(fname: str, target: str, qty: float, price: float,
               amount: float, fee: float, mult: float = 1.0) -> float:
    """Fee-inclusive trade total: the amount column when present, else
    derived. A derived SELL net that goes NEGATIVE (fee > gross —
    worthless-position cleanup sells) is clamped to 0 with a warning:
    the engines consume net_amount as a magnitude, so a negative value
    would silently UNDERSTATE the loss."""
    if amount:
        return abs(amount)
    derived = (abs(qty) * abs(price) * mult
               + (abs(fee) if target == "buy" else -abs(fee)))
    if derived < 0:
        print(f"warning: generic importer: {fname}: sell fee "
              f"({abs(fee):.2f}) exceeds gross proceeds "
              f"({abs(qty) * abs(price) * mult:.2f}) — net proceeds clamped "
              f"to 0; hand-check this disposition (the excess fee is "
              f"not deducted from the basis).", file=sys.stderr)
        return 0.0
    return derived


class GenericBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Generic"

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        mapping = _load_mapping(path)
        cols: Dict[str, str] = {k: v for k, v in
                                (mapping.get("columns") or {}).items()}
        actions: Dict[str, str] = {
            str(k).upper(): v for k, v in
            (mapping.get("actions") or {}).items()}
        defaults = mapping.get("defaults") or {}
        allow_large_fees = bool((mapping.get("options") or {})
                                .get("allow_large_fees", False))
        date_fmt = (mapping.get("formats") or {}).get("date", "%Y-%m-%d")
        tax_sign = (mapping.get("formats") or {}).get("tax_sign", "cash")
        if tax_sign not in ("cash", "withheld"):
            raise ValueError(
                f"generic importer: {mapping['_path']}: [formats]."
                f"tax_sign must be 'cash' (negative = withheld, the "
                f"default) or 'withheld' (positive = withheld), got "
                f"{tax_sign!r}")

        transactions: List[Dict[str, Any]] = []
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

            def num(row, field):
                """STRICT numeric: the importer's charter is refuse-
                loudly, and clean_number's garbage->0.0 silently booked
                $0-basis buys from 'C$10.00'-style cells. Empty is 0."""
                raw = str(cell(row, field, "")).strip()
                if not raw:
                    return 0.0
                s = raw.replace(",", "")
                for pre in ("CA$", "C$", "US$", "A$", "$", "€", "£"):
                    s = s.replace(pre, "")
                s = s.strip()
                # Accounting-negative: "(138.00)" means -138.00. The
                # old strip-as-magnitude lost the sign on the
                # SIGN-PRESERVING branches (a parenthesized DIVIDEND
                # reversal booked as MORE income; a parenthesized TAX
                # withholding flipped to a refund). Trade branches are
                # unaffected — they re-sign via signed_quantity/abs().
                negative = s.startswith("(") and s.endswith(")")
                if negative:
                    s = s[1:-1].strip()
                try:
                    v = float(s)
                    return -v if negative else v
                except ValueError:
                    raise ValueError(
                        f"generic importer: {path.name}: unparseable "
                        f"{field} value {raw!r} — fix the cell or the "
                        f"[columns].{field} mapping.")

            def cell(row, field, default=""):
                col = cols.get(field)
                if not col:
                    return default
                return (row.get(header[col.strip().lower()]) or default)

            for row in reader:
                if not any((v or "").strip() for v in row.values()
                           if isinstance(v, str) or v is None):
                    continue
                raw_action = (str(cell(row, "action")
                                  or defaults.get("action", ""))
                              .strip().upper())
                target = actions.get(raw_action)
                if target is None:
                    self.count_skip(f"action {raw_action or '?'!s}")
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
                symbol = (self.apply_currency_suffix(symbol_raw, currency)
                          if symbol_raw else "")
                qty = num(row, "quantity")
                price = num(row, "price")
                amount = num(row, "amount")
                fee = num(row, "fee")

                if target in ("buy", "sell"):
                    mult = (float(self.OPTION_MULTIPLIER)
                            if is_option_symbol(symbol_raw)
                            or is_option_symbol(symbol) else 1.0)
                    _check_trade_row(where, target, qty, price, amount,
                                     fee, mult, allow_large_fees)
                    transactions.append({
                        "action": "BUYSELL",
                        "date": date, "time": "09:30:00",
                        "date_settle": date,
                        "symbol": symbol,
                        "quantity": self.signed_quantity(
                            qty, action_is_sell=(target == "sell")),
                        "currency": currency,
                        "price": abs(price),
                        # Amount column when present (fee-inclusive,
                        # magnitude); else derive from qty*price±fee.
                        "net_amount": _trade_net(
                            path.name, target, qty, price, amount, fee,
                            mult),
                        "gross_amount": self.theoretical_gross(
                            qty, abs(price), is_option=(mult > 1)),
                        "fee": abs(fee),
                        "account": self.DEFAULT_ACCOUNT,
                        "description": raw_action,
                    })
                elif target == "dividend":
                    transactions.append({
                        "action": "DIVIDEND",
                        "date": date, "time": "09:30:00",
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
                        "net_amount": (
                            (amount if tax_sign == "withheld"
                             else -amount) if target == "tax"
                            else amount),
                        "type": target,
                        "account": self.DEFAULT_ACCOUNT,
                        "description": raw_action,
                    })
        self.disambiguate_split_fills(transactions)
        self.emit_skip_summary(path.name)
        return transactions
