"""What to check next: the short summary at the end of `taxjson run`.

A run that ends with "Done." can still be missing purchases (the
broker's download rarely reaches back to every one). The summary counts
what the books themselves show is incomplete, each with the command
that lists and fixes it (docs/getting-started.md, step 5):

  * sales with no purchase in the files that missing_history.json does
    not cover and that fall in the tax year (taxable accounts) —
    `taxjson find-missing-history`;
  * positions at a $0 cost: sold in the tax year, or still held —
    `taxjson find-missing-history`;
  * shares that arrived by transfer from outside the books with no
    cost (lib/transfer_in) — `taxjson transfers`;
  * accounts with open positions and no holdings file to check them
    against — `taxjson sanity`;
  * income paid on a security the books do not hold — a holding with no
    purchase at all; `taxjson sanity`.

Silent when every count is zero. The same counts go to
work/run_summary.json. `taxjson sum` uses `uncovered_short_sales` for
its own warning.
"""
from __future__ import annotations

import json
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from taxjson.lib.core import TaxTransaction, is_option_symbol, load_transactions
from taxjson.lib.missing_history import (
    MissingHistoryRow, ZeroBasisRow, _basis_date, _drop_duplicate_splits,
    _walk_key, assess_tax_year_relevance, detect_corp_action_links,
    detect_missing_history, detect_zero_basis_acquisitions,
    is_registered_account)
from taxjson.lib.corporate_timeline import normalize_symbol_new

SUMMARY_FILE = "run_summary.json"
GUIDE = "docs/getting-started.md"

# Income paid this long after the books last held the security is not a
# late payment on shares just sold (a record date before the sale, the
# pay date after it): it is a holding the books never saw.
_INCOME_GRACE_DAYS = 60


def book_files(cache: Path) -> List[Path]:
    """The per-account full-history books (work/<acct>_base.json), as
    `taxjson find-missing-history` reads them."""
    return sorted(p for p in cache.glob("*_base.json")
                  if not p.name.endswith("_raw_base.json")
                  and p.name != "sheltered_base.json"
                  and not p.name.startswith("."))


def load_books(cache: Path) -> Tuple[List[TaxTransaction], List[str]]:
    """(rows of every book, [book files that could not be read])."""
    txs: List[TaxTransaction] = []
    failed: List[str] = []
    for p in book_files(cache):
        try:
            txs.extend(load_transactions(p))
        except (OSError, ValueError):
            failed.append(p.name)
    return txs, failed


def read_missing_history_pairs(path: Optional[Path]) -> Set[Tuple[str, str]]:
    """{(SYMBOL, account)} the project's missing_history.json lists
    (empty when there is none or it cannot be read)."""
    if not path or not Path(path).is_file():
        return set()
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set()
    out = set()
    for e in doc if isinstance(doc, list) else []:
        if isinstance(e, dict) and e.get("symbol") and e.get("account"):
            out.add((str(e["symbol"]).strip().upper(),
                     str(e["account"]).strip()))
    return out


def uncovered_short_sales(txs: Sequence[TaxTransaction], year: Any, *,
                          sheltered: Dict[str, bool],
                          covered: Set[Tuple[str, str]],
                          date_basis: str = "settle",
                          journal: Optional[Set[str]] = None,
                          country: Optional[str] = None,
                          ) -> List[MissingHistoryRow]:
    """The tax year's sales with no purchase in the files, in taxable
    accounts, that missing_history.json does not cover — the AFFECTS
    rows of `taxjson find-missing-history` (broker-marked real shorts,
    registered accounts and the old leg of a reconstructed merger are
    left out, as there)."""
    txs = list(txs)
    linked_old = {(l.old_symbol, l.account)
                  for l in detect_corp_action_links(txs)}
    cands = [c for c in detect_missing_history(
                 txs, include_broker_shorts=True,
                 registered_accounts=sheltered or None,
                 journal_symbols=journal or set())
             if not c.broker_marked_short]
    rows = assess_tax_year_relevance(txs, cands, year,
                                     date_basis=date_basis,
                                     journal_symbols=journal or set())
    cov = {(s.upper(), a.lower()) for s, a in covered}
    return [r for r in rows
            if r.affects_year and not r.candidate.registered
            and (r.candidate.symbol, r.candidate.account) not in linked_old
            and (str(r.candidate.symbol).upper(),
                 str(r.candidate.account).lower()) not in cov]


def zero_cost_positions(txs: Sequence[TaxTransaction], year: Any, *,
                        sheltered: Dict[str, bool],
                        country: Optional[str],
                        date_basis: str = "settle"
                        ) -> Tuple[List[ZeroBasisRow], List[ZeroBasisRow]]:
    """($0-cost shares sold in the tax year, $0-cost shares still held),
    taxable accounts only (no gain to misstate in a sheltered one)."""
    from taxjson.lib.country import stock_dividend_zero_cost
    txs = list(txs)
    linked_new = {(l.new_symbol, l.account)
                  for l in detect_corp_action_links(txs)}
    spread = country is not None and not stock_dividend_zero_cost(country)
    rows = [r for r in detect_zero_basis_acquisitions(
                txs, year, date_basis=date_basis, include_held=True,
                stock_dividends_spread=spread)
            if (r.symbol, r.account) not in linked_new
            and not is_registered_account(r.account, sheltered or None,
                                          country)]
    return ([r for r in rows if r.sold and r.affects_year],
            [r for r in rows if r.still_held_qty > 0])


def income_without_position(txs: Sequence[TaxTransaction], year: Any, *,
                            skip_accounts: Iterable[str] = (),
                            date_basis: str = "settle"
                            ) -> List[Dict[str, Any]]:
    """[{symbol, account, rows, first}] — the tax year's dividends (and
    payments in lieu) on a security the account's books did not hold on
    the pay date nor in the 60 days before it: a holding whose purchase
    is not in the files at all (nothing goes negative, so nothing else
    sees it). A payment in the first 60 days of the account's data is
    left out (shares sold just before the data starts are paid after
    it), as are options and the given accounts (crypto staking)."""
    skip = set(skip_accounts)
    ys = str(year) if year is not None else None
    grace = timedelta(days=_INCOME_GRACE_DAYS)

    def _d(s):
        try:
            return _date.fromisoformat(str(s or "")[:10])
        except ValueError:
            return None
    rows = [t for t in txs if t.account not in skip]
    first_row: Dict[str, _date] = {}
    for t in rows:
        d = _d(t.date)
        if d and (t.account not in first_row or d < first_row[t.account]):
            first_row[t.account] = d
    run: Dict[Tuple[str, str], float] = {}
    last_held: Dict[Tuple[str, str], _date] = {}
    hits: Dict[Tuple[str, str], Dict[str, Any]] = {}
    pending: List[TaxTransaction] = []     # the day's income rows

    def _held(key, day):
        lh = last_held.get(key)
        return run.get(key, 0.0) > 1e-9 or (
            lh is not None and day is not None and day - lh <= grace)

    def _settle(day):
        # A day's income is checked after the day's trades: a deemed
        # dividend booked beside the shares it delivers, or a purchase
        # stamped later the same day, is a holding.
        for tx in pending:
            key = (tx.symbol, tx.account)
            dd = _d(tx.date)
            if _held(key, dd):
                continue
            f = first_row.get(tx.account)
            if f is not None and dd is not None and dd - f <= grace:
                continue
            h = hits.setdefault(key, {"symbol": tx.symbol,
                                      "account": tx.account, "rows": 0,
                                      "first": str(tx.date or "")})
            h["rows"] += 1
            h["first"] = min(h["first"], str(tx.date or ""))
        pending.clear()

    day = None
    for tx in _drop_duplicate_splits(sorted(rows, key=_walk_key)):
        td = _d(tx.date)
        if td != day:
            _settle(day)
            day = td
        key = (tx.symbol, tx.account)
        if tx.action == "SPLIT":
            ratio = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol,
                                           getattr(tx, "symbol_new", ""))
            if new_sym:
                nk = (new_sym, tx.account)
                run[nk] = run.get(nk, 0.0) + run.pop(key, 0.0) * ratio
                if key in last_held:
                    last_held[nk] = max(last_held.get(nk, last_held[key]),
                                        last_held[key])
            elif key in run:
                run[key] *= ratio
            continue
        if tx.action in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            before = run.get(key, 0.0)
            run[key] = before + float(tx.quantity or 0.0)
            if (before > 1e-9 or run[key] > 1e-9) and td is not None:
                # Held up to (and including) this row's day.
                last_held[key] = max(last_held.get(key, td), td)
            continue
        if tx.action not in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            continue
        if not tx.symbol or is_option_symbol(tx.symbol):
            continue
        amt = float(tx.gross_amount or 0.0) or float(tx.net_amount or 0.0)
        if amt <= 0:
            continue
        if ys is not None and not _basis_date(tx, date_basis).startswith(ys):
            continue
        pending.append(tx)
    _settle(day)
    return sorted(hits.values(), key=lambda h: (h["account"], h["symbol"]))


def unchecked_accounts(cache: Path, accounts: Dict[str, Any]
                       ) -> List[Tuple[str, int]]:
    """[(account, open positions)] of configured accounts with open
    positions in their books and no `holdings` file in taxjson.toml —
    the UNCHECKED line of `taxjson sanity`."""
    from taxjson.lib.report_model import resolve_gains_files
    try:
        resolved = resolve_gains_files(cache)
    except Exception:                               # noqa: BLE001
        return []
    out = []
    for acct, f in sorted(resolved.items()):
        cfg = accounts.get(acct)
        if not isinstance(cfg, dict) or cfg.get("holdings"):
            continue
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = sum(1 for h in (data.get("inventory") or [])
                if isinstance(h, dict) and abs(float(h.get("qty") or 0))
                > 1e-9)
        if n:
            out.append((acct, n))
    return out


def collect(root: Path, cfg: Dict[str, Any], *,
            missing_history: Optional[Path] = None,
            arrivals: Sequence[Any] = ()) -> Dict[str, Any]:
    """The summary's findings for the project at `root` (its work/
    books, the run just written)."""
    from taxjson.lib.country import settings_country, settings_tax_date
    cache = root / "work"
    settings = cfg.get("settings") or {}
    accounts = cfg.get("accounts") or {}
    country = settings_country(settings)
    year = settings.get("year")
    basis = settings_tax_date(settings)
    sheltered = {str(n): (a.get("type") == "sheltered")
                 for n, a in accounts.items() if isinstance(a, dict)
                 and a.get("type") in ("taxable", "sheltered")}
    crypto = {n for n, a in accounts.items()
              if isinstance(a, dict) and a.get("crypto")}
    journal: Set[str] = set()
    tm = root / "ticker.map"
    if tm.is_file():
        try:
            from taxjson.lib.missing_history import journal_targets
            journal = journal_targets(str(tm))
        except Exception:                           # noqa: BLE001
            journal = set()
    txs, failed = load_books(cache)
    covered = read_missing_history_pairs(missing_history)
    shorts = uncovered_short_sales(txs, year, sheltered=sheltered,
                                   covered=covered, date_basis=basis,
                                   journal=journal, country=country)
    zero_sold, zero_held = zero_cost_positions(
        txs, year, sheltered=sheltered, country=country, date_basis=basis)
    income = income_without_position(txs, year, skip_accounts=crypto,
                                     date_basis=basis)
    no_cost = [a for a in arrivals if getattr(a, "status", "") == "no_cost"]
    booked = [a for a in arrivals
              if getattr(a, "status", "") == "book_value"]
    return {
        "schema_version": 1,
        "year": year,
        "no_purchase": [{"symbol": r.candidate.symbol,
                         "account": r.candidate.account,
                         "sales": r.in_year_dispositions}
                        for r in shorts],
        "zero_cost_sold": [{"symbol": r.symbol, "account": r.account}
                           for r in zero_sold],
        "zero_cost_held": [{"symbol": r.symbol, "account": r.account,
                            "quantity": r.still_held_qty}
                           for r in zero_held],
        "transfer_in_no_cost": [{"symbol": a.symbol, "account": a.account,
                                 "date": a.date, "quantity": a.quantity}
                                for a in no_cost],
        "transfer_in_book_value": [{"symbol": a.symbol,
                                    "account": a.account, "date": a.date,
                                    "quantity": a.quantity}
                                   for a in booked],
        "unchecked_accounts": [{"account": a, "positions": n}
                               for a, n in unchecked_accounts(cache,
                                                              accounts)],
        "income_not_held": income,
        "books_not_read": failed,
    }


def _names(items: Sequence[Dict[str, Any]], n: int = 3) -> str:
    shown = ", ".join(f"{i['symbol']} ({i['account']})" for i in items[:n])
    return shown + (f" +{len(items) - n} more" if len(items) > n else "")


def is_clean(doc: Dict[str, Any]) -> bool:
    return not any(doc.get(k) for k in (
        "no_purchase", "zero_cost_sold", "zero_cost_held",
        "transfer_in_no_cost", "unchecked_accounts", "income_not_held"))


def render(doc: Dict[str, Any], *, mh_name: str = "missing_history.json"
           ) -> List[str]:
    """The summary's lines (none when clean)."""
    if is_clean(doc):
        return []
    yr = doc.get("year")
    lines = ["==> before you trust these numbers (docs/getting-started.md, "
             "step 5)"]
    np_ = doc.get("no_purchase") or []
    if np_:
        lines.append(f"  {len(np_)} position(s) sold in {yr} with no "
                     f"purchase in your files, not in {mh_name} — those "
                     f"sales are NOT in `taxjson sum`: {_names(np_)}. "
                     f"Run `taxjson find-missing-history`.")
    zs, zh = doc.get("zero_cost_sold") or [], doc.get("zero_cost_held") or []
    if zs or zh:
        parts = ([f"{len(zs)} sold in {yr}"] if zs else []) \
            + ([f"{len(zh)} still held"] if zh else [])
        lines.append(f"  {len(zs) + len(zh)} position(s) at a $0 cost "
                     f"({', '.join(parts)}): {_names(zs + zh)}. Run "
                     f"`taxjson find-missing-history`.")
    tn = doc.get("transfer_in_no_cost") or []
    if tn:
        lines.append(f"  {len(tn)} transfer-in(s) from outside your books "
                     f"kept out with no cost: {_names(tn)}. Run `taxjson "
                     f"transfers`.")
    un = doc.get("unchecked_accounts") or []
    if un:
        shown = ", ".join(f"{u['account']} ({u['positions']})"
                          for u in un[:4])
        lines.append(f"  {len(un)} account(s) with open positions and no "
                     f"holdings file to check them against: {shown}. Run "
                     f"`taxjson sanity` with the broker's positions.")
    inc = doc.get("income_not_held") or []
    if inc:
        lines.append(f"  {len(inc)} security(ies) paid income in {yr} that "
                     f"the books do not hold (a holding with no purchase "
                     f"in your files?): {_names(inc)}. Run `taxjson "
                     f"sanity`.")
    lines.append("  Then `taxjson checklist`. Every step is in "
                 f"{GUIDE}.")
    return lines
