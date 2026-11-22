"""An option's contract size: the units of the underlying one contract
delivers. An equity option's is ticker.map's MULT line for it (or its
root; lib/markets) first, else the size the row declares, else the
standard 100 (ASSUMED, said once per root); a futures option's is one
contract. Above lib/markets, which it reads, and lib/option_symbols;
apart from the option-symbol parsing, which reads no setting (step 8 of
the architecture plan: lib's import cycle)."""

from __future__ import annotations

from typing import Any

from taxjson.lib.option_symbols import _FUTURES_PREFIX_RE, parse_option_underlying


OPTION_CONTRACT_SHARES = 100.0      # shares per standard equity option


def equity_option_size(opt_tx: Any) -> float:
    """Units of the underlying one contract of the equity option
    `opt_tx` delivers: a ticker.map `MULT` line for the option (or its
    root) first; else the size the row declares, unless its parser
    only assumed it; else the standard 100 — ASSUMED, said once per
    option root (tax-logic: the 100-share contract is assumed when the
    export does not state the size; B10)."""
    from taxjson.lib import markets
    sym = str(getattr(opt_tx, 'symbol', '') or '')
    und = parse_option_underlying(sym) or ''
    root = und.split('.', 1)[0]
    ovr = markets.option_contract_size(sym, und)
    if ovr:
        return float(ovr)
    m = float(getattr(opt_tx, 'multiplier', 0.0) or 0.0)
    if m > 0 and getattr(opt_tx, 'contract_size_basis', '') != 'assumed':
        return m
    size = m if m > 0 else OPTION_CONTRACT_SHARES
    if root:
        markets.note_builtin(
            "mult", root,
            f"{root} options: the export does not state the contract size, "
            f"so {size:g} shares per contract is ASSUMED where it matters "
            f"(an exercise or assignment, replacement shares); for a mini "
            f"or an adjusted series add `MULT {root} N` to ticker.map.",
            rollup=(root if size == OPTION_CONTRACT_SHARES
                    else f"{root} ({size:g} shares)",))
    return size


def option_contract_size(opt_tx: Any) -> float:
    """Units of the underlying one contract of `opt_tx` is a right to:
    the declared contract size (` x10` mini, an adjusted deliverable),
    else 100 for an equity option; one contract for a futures option
    (its declared multiplier is the futures' dollar size, not a unit
    count). Audit A2-0049/0957."""
    if _FUTURES_PREFIX_RE.match(getattr(opt_tx, 'symbol', '') or ''):
        return 1.0
    return equity_option_size(opt_tx)
