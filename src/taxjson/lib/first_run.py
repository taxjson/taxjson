"""What to check next: the short summary at the end of `taxjson run`.

A run that ends with "Done." can still be missing purchases (the
broker's download rarely reaches back to every one). The summary counts
what the books themselves show is incomplete, each with the command
that lists and fixes it (docs/getting-started.md, step 5):

  * sales with no purchase in the files that missing_history.json does
    not cover and that fall in the tax year (taxable accounts) —
    `taxjson find-missing-history`;
  * positions at a $0 cost: sold in the tax year, or still held —
    `taxjson find-missing-history` (a spin-off whose election declares
    the $0, fmv_per_share=0, is an Info naming its event instead);
  * shares that arrived by transfer from outside the books with no
    cost (lib/transfer_in) — `taxjson transfers`;
  * accounts with open positions and no holdings file to check them
    against — `taxjson sanity`;
  * income paid on a security the books do not hold — a holding with no
    purchase at all; `taxjson sanity`.

Silent when every count is zero. The same counts go to
reports/run_summary.json. `taxjson sum` uses `uncovered_short_sales` for
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


# How the gains engine booked the year's sales the missing-history walk
# reads as sold with no purchase (engine_booking):
#   "open"         a sale the gains files lack — the engine still holds
#                  it as an open short at the year's end: its gain is
#                  NOT in `taxjson sum`;
#   "short_cover"  booked as a short sale a later purchase of the year
#                  closed: in `taxjson sum`, at that purchase's cost;
#   "matched"      the engine sold it from a purchase in the files (it
#                  reads the day's rows in another order than the walk):
#                  in `taxjson sum` as an ordinary sale.
BOOKED_OPEN, BOOKED_SHORT_COVER, BOOKED_MATCHED = (
    "open", "short_cover", "matched")


def engine_booking(cache: Path, rows: Sequence[MissingHistoryRow],
                   year: Any, *, date_basis: str = "settle",
                   prefer_wash: bool = True
                   ) -> Dict[Tuple[str, str], str]:
    """{(symbol, account): BOOKED_*} for each uncovered row: what the
    account's gains file (the one `taxjson sum` reads —
    report_model.resolve_gains_files) did with the year's sales the walk
    found short (MissingHistoryRow.in_year_short_sales). A sale's units
    the engine closed against a long position carry the sale's id
    (matched); units still short are covered by the year's short-close
    records of the symbol (short_cover); what is left the gains files
    lack (open). A row whose account has no readable gains file is
    "open" (the claim the summary always made). `prefer_wash=False`
    reads each account's plain gains file: a run's mid-run note comes
    before the cross-account wash pass rebuilds `<acct>_gains_wash.json`,
    so the wash file there is the LAST run's (third pre-release review,
    finding 5)."""
    from taxjson.lib.report_model import resolve_gains_files
    ys = str(year) if year is not None else ""
    try:
        files = resolve_gains_files(cache, prefer_wash=prefer_wash)
    except Exception:                               # noqa: BLE001
        files = {}
    docs: Dict[str, List[Dict[str, Any]]] = {}
    out: Dict[Tuple[str, str], str] = {}
    for r in rows:
        sym, acct = r.candidate.symbol, r.candidate.account
        if acct not in docs:
            recs: List[Dict[str, Any]] = []
            f = files.get(acct)
            if f is not None:
                try:
                    d = json.loads(Path(f).read_text(encoding="utf-8"))
                    recs = [x for x in (d.get("transactions") or [])
                            if isinstance(x, dict)]
                except (OSError, ValueError, AttributeError):
                    recs = []
            docs[acct] = recs
        recs = [x for x in docs[acct] if x.get("symbol") == sym]
        if not recs:
            out[(sym, acct)] = BOOKED_OPEN
            continue
        long_by_id: Dict[str, float] = {}
        covered = 0.0
        for x in recs:
            try:
                q = abs(float(x.get("qty") or 0.0))
            except (TypeError, ValueError):
                continue
            if x.get("direction") == "SHORT":
                d = str((x.get("date_settle") if date_basis == "settle"
                         else None) or x.get("date") or "")
                if not x.get("grant") and d.startswith(ys):
                    covered += q
            else:
                i = str(x.get("id") or "")
                long_by_id[i] = long_by_id.get(i, 0.0) + q
        matched = rest = 0.0
        for sid, units in r.in_year_short_sales:
            m = min(units, long_by_id.get(sid, 0.0))
            matched += m
            rest += units - m
        sales = bool(r.in_year_short_sales)
        if rest - covered > 1e-6:
            out[(sym, acct)] = BOOKED_OPEN
        elif rest > 1e-6 or (not sales and covered > 1e-6):
            # (no sale of the year: a cover of a short carried in)
            out[(sym, acct)] = BOOKED_SHORT_COVER
        else:
            out[(sym, acct)] = BOOKED_MATCHED if sales else BOOKED_OPEN
    return out


def _zero_cost_rows(txs: Sequence[TaxTransaction], year: Any, *,
                    sheltered: Dict[str, bool], country: Optional[str],
                    date_basis: str = "settle",
                    declared: Iterable[Tuple[str, str]] = ()
                    ) -> List[ZeroBasisRow]:
    """The $0-cost pools of the taxable accounts (no gain to misstate in
    a sheltered one), sold in the tax year or still held."""
    from taxjson.lib.country import stock_dividend_zero_cost
    txs = list(txs)
    linked_new = {(l.new_symbol, l.account)
                  for l in detect_corp_action_links(txs)}
    spread = country is not None and not stock_dividend_zero_cost(country)
    return [r for r in detect_zero_basis_acquisitions(
                txs, year, date_basis=date_basis, include_held=True,
                stock_dividends_spread=spread, declared_events=declared)
            if (r.symbol, r.account) not in linked_new
            and not is_registered_account(r.account, sheltered or None,
                                          country)
            and ((r.sold and r.affects_year) or r.still_held_qty > 0)]


def zero_cost_positions(txs: Sequence[TaxTransaction], year: Any, *,
                        sheltered: Dict[str, bool],
                        country: Optional[str],
                        date_basis: str = "settle",
                        declared: Iterable[Tuple[str, str]] = ()
                        ) -> Tuple[List[ZeroBasisRow], List[ZeroBasisRow]]:
    """($0-cost shares sold in the tax year, $0-cost shares still held),
    taxable accounts only (no gain to misstate in a sheltered one). A
    spin-off whose election declares the $0 (`declared`: {(account,
    event id)}, corp_actions.declared_zero_value_events) is the user's
    answer, left out of both (declared_zero_cost)."""
    rows = [r for r in _zero_cost_rows(txs, year, sheltered=sheltered,
                                       country=country,
                                       date_basis=date_basis,
                                       declared=declared)
            if not r.declared]
    return ([r for r in rows if r.sold and r.affects_year],
            [r for r in rows if r.still_held_qty > 0])


def declared_zero_cost(rows: Iterable[ZeroBasisRow]) -> List[Dict[str, Any]]:
    """[{symbol, account, events, quantity}] of the $0-cost pools whose
    every $0 acquisition is a spin-off booked at the $0 value its
    election declares: the run's closing summary lists them as an Info
    (`quantity`: still held)."""
    return [{"symbol": r.symbol, "account": r.account,
             "events": list(r.event_ids), "quantity": r.still_held_qty}
            for r in rows if r.declared]


def income_without_position(txs: Sequence[TaxTransaction], year: Any, *,
                            skip_accounts: Iterable[str] = (),
                            date_basis: str = "settle",
                            declared: Iterable[Tuple[str, str]] = ()
                            ) -> List[Dict[str, Any]]:
    """[{symbol, account, rows, first}] — the tax year's dividends (and
    payments in lieu) on a security the account's books did not hold on
    the pay date nor in the 60 days before it: a holding whose purchase
    is not in the files at all (nothing goes negative, so nothing else
    sees it). A payment in the first 60 days of the account's data is
    left out (shares sold just before the data starts are paid after
    it), as are options, the given accounts (crypto staking) and the
    `declared` (SYMBOL, account) pairs — missing_history.json's, whose
    holding the user has already declared."""
    skip = set(skip_accounts)
    known = {(str(sy).upper(), str(a)) for sy, a in declared}
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
        if (str(tx.symbol).upper(), tx.account) in known:
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
    tm = root / "ticker.map"
    from taxjson.lib.missing_history import walk_journal_symbols
    journal = walk_journal_symbols(cache, tm if tm.is_file() else None)
    txs, failed = load_books(cache)
    covered = read_missing_history_pairs(missing_history)
    shorts = uncovered_short_sales(txs, year, sheltered=sheltered,
                                   covered=covered, date_basis=basis,
                                   journal=journal, country=country)
    booked = engine_booking(cache, shorts, year, date_basis=basis)
    from taxjson.lib.corp_actions import declared_zero_value_events
    _zrows = _zero_cost_rows(txs, year, sheltered=sheltered,
                             country=country, date_basis=basis,
                             declared=declared_zero_value_events(root))
    zero_sold = [r for r in _zrows if not r.declared and r.sold
                 and r.affects_year]
    zero_held = [r for r in _zrows if not r.declared
                 and r.still_held_qty > 0]
    income = income_without_position(txs, year, skip_accounts=crypto,
                                     date_basis=basis, declared=covered)
    no_cost = [a for a in arrivals if getattr(a, "status", "") == "no_cost"]
    book_value = [a for a in arrivals
                  if getattr(a, "status", "") == "book_value"]

    def _how(r):
        return booked.get((r.candidate.symbol, r.candidate.account),
                          BOOKED_OPEN)
    return {
        "schema_version": 1,
        "year": year,
        # Sales the gains files lack: NOT in `taxjson sum`.
        "no_purchase": [{"symbol": r.candidate.symbol,
                         "account": r.candidate.account,
                         "sales": r.in_year_dispositions}
                        for r in shorts if _how(r) == BOOKED_OPEN],
        # Sales the walk reads short that the gains engine booked
        # (engine_booking): in `taxjson sum`.
        "no_purchase_in_sum": [{"symbol": r.candidate.symbol,
                                "account": r.candidate.account,
                                "sales": r.in_year_dispositions,
                                "booked": _how(r)}
                               for r in shorts if _how(r) != BOOKED_OPEN],
        "zero_cost_sold": [{"symbol": r.symbol, "account": r.account}
                           for r in zero_sold],
        "zero_cost_held": [{"symbol": r.symbol, "account": r.account,
                            "quantity": r.still_held_qty}
                           for r in zero_held],
        # A $0 cost the user declared (fmv_per_share=0): an Info.
        "zero_cost_declared": declared_zero_cost(_zrows),
        "transfer_in_no_cost": [{"symbol": a.symbol, "account": a.account,
                                 "date": a.date, "quantity": a.quantity}
                                for a in no_cost],
        "transfer_in_book_value": [{"symbol": a.symbol,
                                    "account": a.account, "date": a.date,
                                    "quantity": a.quantity}
                                   for a in book_value],
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
        "no_purchase", "no_purchase_in_sum", "zero_cost_sold",
        "zero_cost_held", "zero_cost_declared", "transfer_in_no_cost",
        "unchecked_accounts", "income_not_held"))


def _accounts_shown(items: Sequence[Dict[str, Any]], n: int = 6) -> str:
    """`margin (8), qt (1)` — the first `n` accounts and `+N more`, so
    the names add up to the count the line states."""
    shown = ", ".join(f"{u['account']} ({u['positions']})"
                      for u in items[:n])
    return shown + (f" +{len(items) - n} more" if len(items) > n else "")


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def render(doc: Dict[str, Any], *, mh_name: str = "missing_history.json",
           width_: Optional[int] = None) -> List[str]:
    """The summary's lines (none when clean), on the run's console
    (docs/output-style.md, The run's console): a `==> ` heading, one
    `Warning:` (a number the totals miss) or `Info:` (a check not yet
    made) per finding — its wrapped lines flush-left, a blank line after
    a finding of more than one line; one line each when nothing wraps —
    and the closing `Info:` line."""
    from taxjson.lib import out
    return out.join_blocks(render_blocks(doc, mh_name=mh_name,
                                         width_=width_))


def render_blocks(doc: Dict[str, Any], *,
                  mh_name: str = "missing_history.json",
                  width_: Optional[int] = None) -> List[List[str]]:
    """render() as its entries (the heading, each finding, the closing
    line), each a list of lines: lib/out.show_blocks prints them with a
    blank line after an entry of more than one line."""
    if is_clean(doc):
        return []
    from taxjson.lib import out
    yr = doc.get("year")
    items: List[Tuple[str, str]] = []
    np_ = doc.get("no_purchase") or []
    if np_:
        items.append(("Warning", f"{_n(len(np_), 'position', 'positions')} "
                     f"sold in {yr} with no purchase in your files, not in "
                     f"{mh_name}: {_names(np_)}. Those sales are NOT in "
                     f"`taxjson sum`; run `taxjson find-missing-history`."))
    ins = doc.get("no_purchase_in_sum") or []
    cov = [i for i in ins if i.get("booked") == BOOKED_SHORT_COVER]
    mat = [i for i in ins if i.get("booked") != BOOKED_SHORT_COVER]
    if cov:
        items.append(("Warning", f"{_n(len(cov), 'position', 'positions')} "
                     f"sold in {yr} with no purchase in your files, not in "
                     f"{mh_name}: {_names(cov)}. `taxjson sum` books those "
                     f"sales as short sales closed by a later purchase, at "
                     f"that purchase's cost — not yours if you held the "
                     f"shares before your files start; run `taxjson "
                     f"find-missing-history`."))
    if mat:
        items.append(("Info", f"{_n(len(mat), 'position', 'positions')} "
                     f"read short in {yr} by `taxjson find-missing-history`"
                     f": {_names(mat)}. The gains engine sold them from "
                     f"purchases in your files, so they are in `taxjson "
                     f"sum`; the check reads that day's rows in another "
                     f"order: compare it with the broker's trades."))
    zs, zh = doc.get("zero_cost_sold") or [], doc.get("zero_cost_held") or []
    if zs or zh:
        parts = ([f"{len(zs)} sold in {yr}"] if zs else []) \
            + ([f"{len(zh)} still held"] if zh else [])
        items.append(("Warning", f"{_n(len(zs) + len(zh), 'position', 'positions')}"
                     f" at a $0 cost ({', '.join(parts)}): "
                     f"{_names(zs + zh)}. Run `taxjson "
                     f"find-missing-history`."))
    zd = doc.get("zero_cost_declared") or []
    if zd:
        shown = ", ".join(
            f"{i['symbol']} ({i['account']}, event "
            f"{', '.join(i.get('events') or []) or '?'})" for i in zd[:3]) \
            + (f" +{len(zd) - 3} more" if len(zd) > 3 else "")
        one = zd[0]
        how = (f"`taxjson elect {one['account']} --redo --event "
               f"{(one.get('events') or ['ID'])[0]}`"
               if len(zd) == 1 and len(one.get("events") or []) == 1
               else "`taxjson elect ACCOUNT --redo --event ID`")
        items.append(("Info", f"{_n(len(zd), 'position', 'positions')} at "
                     f"the $0 cost you declared (fmv_per_share=0 in the "
                     f"spin-off's election): {shown}. To change it: "
                     f"{how}."))
    tn = doc.get("transfer_in_no_cost") or []
    if tn:
        items.append(("Warning", f"{_n(len(tn), 'transfer-in', 'transfer-ins')}"
                     f" from outside your books kept out with no cost: "
                     f"{_names(tn)}. Run `taxjson transfers`."))
    un = doc.get("unchecked_accounts") or []
    if un:
        items.append(("Info", f"{_n(len(un), 'account', 'accounts')} with "
                     f"open positions and no holdings file to check them "
                     f"against: {_accounts_shown(un)}. Run `taxjson "
                     f"sanity` with the broker's positions."))
    inc = doc.get("income_not_held") or []
    if inc:
        items.append(("Warning", f"{_n(len(inc), 'security', 'securities')} "
                     f"paid income in {yr} that the books do not hold (a "
                     f"holding with no purchase in your files?): "
                     f"{_names(inc)}. Run `taxjson sanity`."))
    w = out.width() if width_ is None else width_
    hang = "" if w > 0 else "  "
    blocks = [["==> Before you trust these numbers "
               "(docs/getting-started.md, step 5)"]]
    for kind, it in items:
        blocks.append(out.wrap(f"{kind}: {it}", w, "", hang))
    blocks.append(out.wrap("Info: Then run `taxjson checklist`. `taxjson "
                           "quick-start` lists every step and names the "
                           "next one.", w, "", hang))
    return blocks
