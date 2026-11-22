"""What to check next: the short summary at the end of `tjs run`.

A run that ends with "Done." can still be missing purchases (the
broker's download rarely reaches back to every one). The summary counts
what the books themselves show is incomplete, each with the command
that lists and fixes it (docs/getting-started.md, step 5):

  * sales with no purchase in the files that missing history (.tt
    OPENING cost=unknown lines) does
    not cover and that fall in the tax year (taxable accounts) —
    `tjs find-missing-history`;
  * sales at an unknown cost: the tax year's rows routed out of the
    totals to manual reporting (CA-ACB-11) — a sale drawing on the units
    an OPENING cost=unknown line opens, a buy closing a short sold before
    the data — each named with its row and the line it came by (the
    engine writes the line's own fields on the record,
    `unknown_cost_from`), and the cost to add: the OPENING line's cost,
    or `tjs opening` (backlog TJ-2770, TJ-3209);
  * positions at a $0 cost: sold in the tax year, or still held —
    `tjs find-missing-history` (a spin-off whose election declares
    the $0, fmv_per_share=0, is an Info naming its event instead);
  * shares that arrived by transfer from outside the books with no
    cost (lib/transfer_in) — `tjs transfers`;
  * accounts with open positions and no holdings file to check them
    against — `tjs sanity`;
  * income paid on a security the books do not hold — a holding with no
    purchase at all; `tjs sanity`.

Silent when every count is zero. The same counts go to
reports/run_summary.json. `tjs sum` uses `uncovered_short_sales` for
its own warning.
"""
from __future__ import annotations


import json
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from taxjson.project.accounts import configured_accounts, sheltered_by_name
from taxjson.project.settings import settings_of
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.tx_load import load_transactions
from taxjson.lib.option_symbols import is_option_symbol
from taxjson.lib.missing_history import (
    MissingHistoryRow, UnbackedCover, ZeroBasisRow, _basis_date,
    _drop_duplicate_splits, _walk_key, assess_tax_year_relevance,
    detect_corp_action_links, detect_missing_history,
    detect_unbacked_covers, detect_zero_basis_acquisitions,
    drop_answered_covers, is_registered_account)
from taxjson.lib.corporate_timeline import normalize_symbol_new
from taxjson.lib.in_kind import in_kind_unbooked
from taxjson.lib import project_layout as _PL

SUMMARY_FILE = "run_summary.json"

# A parser's warning for the end-of-run list, as its .diag captured it
# (lib/out.END_OF_RUN_MARK).
_MARKED = "warning: ATTENTION: "

# Income paid this long after the books last held the security is not a
# late payment on shares just sold (a record date before the sale, the
# pay date after it): it is a holding the books never saw.
_INCOME_GRACE_DAYS = 60


def load_books(cache: Path) -> Tuple[List[TaxTransaction], List[str]]:
    """(rows of every book, [book files that could not be read])."""
    txs: List[TaxTransaction] = []
    failed: List[str] = []
    for p in _PL.book_files(cache):
        try:
            txs.extend(load_transactions(p))
        except (OSError, ValueError):
            failed.append(p.name)
    return txs, failed


def read_missing_history_pairs(path: Optional[Path]) -> Set[Tuple[str, str]]:
    """{(SYMBOL, account)} the .tt OPENING cost=unknown lines of the
    project folder `path` open (lib/missing_history.load_missing_history;
    empty when there are none or they cannot be read)."""
    if not path:
        return set()
    from taxjson.lib.missing_history import load_missing_history
    try:
        return set(load_missing_history(Path(path)))
    except (OSError, ValueError):
        return set()


def logged_openings(cache: Optional[Path]
                    ) -> Dict[Tuple[str, str], List[TaxTransaction]]:
    """{(SYMBOL, account): [OPENING_BALANCE rows]} — the .tt OPENING
    cost=unknown lines the account's gains file (the plain one, this
    run's) says its run booked (its `missing_history_log`): each row on
    the date and the symbol the engine opened it, keyed by the pair the
    line declares. {} without a readable gains file."""
    out: Dict[Tuple[str, str], List[TaxTransaction]] = {}
    if cache is None:
        return out
    from taxjson.lib.report_model import resolve_gains_files
    try:
        files = resolve_gains_files(cache, prefer_wash=False,
                                    warn_stale=False)
    except Exception:                               # noqa: BLE001
        return out
    for acct, f in files.items():
        try:
            doc = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        log = doc.get("missing_history_log") if isinstance(doc, dict) \
            else None
        for e in log or []:
            if not isinstance(e, dict) or e.get("account") != acct \
                    or not e.get("inserted"):
                continue
            try:
                qty = float(e.get("opening_qty") or 0.0)
            except (TypeError, ValueError):
                continue
            day = str(e.get("anchor_date") or "")[:10]
            if not qty or not day:
                continue
            sym = str(e.get("symbol") or "")
            out.setdefault((sym.upper(), str(acct).lower()), []).append(
                TaxTransaction(
                    action="OPENING_BALANCE", date=day, date_settle=day,
                    time="00:00:00",
                    symbol=str(e.get("anchor_symbol") or sym),
                    quantity=qty, net_amount=0.0, account=str(acct),
                    id=f"missing-history-opening:{sym}:{acct}"))
    return out


def uncovered_short_sales(txs: Sequence[TaxTransaction], year: Any, *,
                          sheltered: Dict[str, bool],
                          covered: Set[Tuple[str, str]],
                          date_basis: str = "settle",
                          journal: Optional[Set[str]] = None,
                          country: Optional[str] = None,
                          cache: Optional[Path] = None,
                          ) -> List[MissingHistoryRow]:
    """The tax year's sales with no purchase in the files, in taxable
    accounts, that no .tt OPENING cost=unknown line covers — the AFFECTS
    rows of `tjs find-missing-history` (broker-marked real shorts,
    registered accounts and the old leg of a reconstructed merger are
    left out, as there).

    `cache` (work/): a declared pair whose opening the gains files say
    the run booked (logged_openings) is walked WITH that opening, on its
    date and quantity, so a sale its units do not reach (an opening used
    up by earlier sales, or dated after the sale) is still a sale with
    no purchase; a declared pair without one is covered as a whole
    (backlog TJ-0958)."""
    txs = list(txs)
    cov = {(s.upper(), a.lower()) for s, a in covered}
    logged = {k: v for k, v in logged_openings(cache).items() if k in cov}
    if logged:
        rows = [r for rows in logged.values() for r in rows]
        # (A short line, TJ-0844, is no units a sale draws on: its cover
        # leaves the walk instead.)
        txs = drop_answered_covers(txs, rows) + [r for r in rows
                                                 if r.quantity > 0]
        cov -= set(logged)
    linked_old = {(l.old_symbol, l.account)
                  for l in detect_corp_action_links(txs)}
    cands = [c for c in detect_missing_history(
                 txs, include_broker_shorts=True,
                 registered_accounts=sheltered or None,
                 journal_symbols=journal or set())
             if not c.broker_marked_short]
    rows = assess_tax_year_relevance(txs, cands, year,
                                     date_basis=date_basis,
                                     journal_symbols=journal or set(),
                                     registered_accounts=sheltered or None)
    return [r for r in rows
            if r.affects_year and not r.candidate.registered
            and (r.candidate.symbol, r.candidate.account) not in linked_old
            and (str(r.candidate.symbol).upper(),
                 str(r.candidate.account).lower()) not in cov]


def unanswered_covers(txs: Sequence[TaxTransaction], year: Any, *,
                      sheltered: Dict[str, bool],
                      date_basis: str = "settle",
                      journal: Optional[Set[str]] = None,
                      country: Optional[str] = None,
                      cache: Optional[Path] = None) -> List[UnbackedCover]:
    """Buys in taxable accounts, through the tax year, that the broker
    codes as closing a short the data does not hold and no .tt short line
    answers (TJ-0844). The books carry each as a new long, in the cost of
    later sales, so a cover before the year still counts."""
    ys = str(year) if year is not None else ""
    shorts = [r for rows in logged_openings(cache).values() for r in rows
              if r.quantity < 0]
    return [c for c in detect_unbacked_covers(
                drop_answered_covers(txs, shorts), date_basis=date_basis,
                journal_symbols=journal or set(),
                registered_accounts=sheltered or None, country=country)
            if not is_registered_account(c.account, sheltered or None,
                                         country)
            and (not ys or c.date[:4] <= ys)]


# How the gains engine booked the year's sales the missing-history walk
# reads as sold with no purchase (engine_booking):
#   "open"         a sale the gains files lack — the engine still holds
#                  it as an open short at the year's end: its gain is
#                  NOT in `tjs sum`;
#   "short_cover"  booked as a short sale a later purchase of the year
#                  closed: in `tjs sum`, at that purchase's cost;
#   "matched"      the engine sold it from a purchase in the files (it
#                  reads the day's rows in another order than the walk):
#                  in `tjs sum` as an ordinary sale.
BOOKED_OPEN, BOOKED_SHORT_COVER, BOOKED_MATCHED = (
    "open", "short_cover", "matched")


def engine_booking(cache: Path, rows: Sequence[MissingHistoryRow],
                   year: Any, *, date_basis: str = "settle",
                   prefer_wash: bool = True
                   ) -> Dict[Tuple[str, str], str]:
    """{(symbol, account): BOOKED_*} for each uncovered row: what the
    account's gains file (the one `tjs sum` reads —
    report_model.resolve_gains_files) did with the year's sales the walk
    found short (MissingHistoryRow.in_year_short_sales). A sale's units
    the engine closed against a long position carry the sale's id
    (matched) only as far as the engine closed the whole sale against a
    long position; units still short are covered by the year's short-close
    records of the symbol, or booked as an option written at grant (a
    `grant` record carrying the sale's id: its premium is in the totals,
    backlog TJ-0017) — short_cover; what is left the gains files lack
    (open). A row whose account has no readable gains file is
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
                    # A cover of the year whose loss is the next year's
                    # (US-DATE-01; backlog TJ-1189): the sale has its
                    # cover, dated by its trade.
                    recs += [dict(x, date=x.get("trade_date"),
                                  date_settle=x.get("trade_date"))
                             for x in (d.get("covers_realized_next_year")
                                       or []) if isinstance(x, dict)]
                except (OSError, ValueError, AttributeError):
                    recs = []
            docs[acct] = recs
        recs = [x for x in docs[acct] if x.get("symbol") == sym]
        if not recs:
            out[(sym, acct)] = BOOKED_OPEN
            continue
        long_by_id: Dict[str, float] = {}
        written_by_id: Dict[str, float] = {}
        covered = 0.0
        # (date, units) of each short close of the year: a cover closes
        # a short opened before it, so it covers only a sale dated on or
        # before it (backlog TJ-0957).
        covers: List[List[Any]] = []
        for x in recs:
            try:
                q = abs(float(x.get("qty") or 0.0))
            except (TypeError, ValueError):
                continue
            if x.get("direction") == "SHORT":
                d = str((x.get("date_settle") if date_basis == "settle"
                         else None) or x.get("date") or "")
                if x.get("grant"):
                    # A write booked at grant (option_grant_timing_since,
                    # ITA s.49(1)): the sale's premium is in the totals
                    # on its own day, carrying the sale's id (backlog
                    # TJ-0017).
                    i = str(x.get("id") or "")
                    written_by_id[i] = written_by_id.get(i, 0.0) + q
                elif d.startswith(ys):
                    covered += q
                    covers.append([d[:10], q])
            else:
                i = str(x.get("id") or "")
                long_by_id[i] = long_by_id.get(i, 0.0) + q
        matched = rest = written = left = 0.0
        covers.sort(key=lambda c: c[0])
        for sale in sorted(r.in_year_short_sales,
                           key=lambda s: str(s[3]) if len(s) > 3 else ""):
            sid, units = sale[0], float(sale[1])
            # The engine's short units of the sale are the sale less what
            # it closed against a long position — not the walk's short
            # units less that (min(10, 40) called a 50-unit sale with 40
            # long and 10 left open "matched": audit 2026-10-22 item 5).
            whole = float(sale[2]) if len(sale) > 2 else units
            m = units - min(units, max(0.0, whole
                                       - long_by_id.get(sid, 0.0)))
            matched += m
            # Units booked as a write at grant are in the totals too, at
            # no cost of yours: said as a short covered (a Warning).
            w = min(units - m, written_by_id.get(sid, 0.0))
            written += w
            short = units - m - w
            rest += short
            # The units still short take the year's covers dated on or
            # after the sale, each cover's units once; an earlier cover
            # closed another short (an older sale of the symbol).
            day = str(sale[3])[:10] if len(sale) > 3 else ""
            for c in covers:
                if short <= 1e-9:
                    break
                if c[0] < day or c[1] <= 1e-9:
                    continue
                take = min(short, c[1])
                c[1] -= take
                short -= take
            left += max(0.0, short)
        sales = bool(r.in_year_short_sales)
        if left > 1e-6:
            out[(sym, acct)] = BOOKED_OPEN
        elif (rest > 1e-6 or written > 1e-6
              or (not sales and covered > 1e-6)):
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
                            declared: Iterable[Tuple[str, str]] = (),
                            rules: Any = None
                            ) -> List[Dict[str, Any]]:
    """[{symbol, account, rows, first}] — the tax year's dividends (and
    payments in lieu) on a security the account's books did not hold on
    the pay date nor in the 60 days before it, nor on the row's record
    date: a holding whose purchase is not in the files at all (nothing
    goes negative, so nothing else sees it). A payment in the first 60
    days of the account's data is left out (shares sold just before the
    data starts are paid after it), as are options, the given accounts
    (crypto staking) and the `declared` (SYMBOL, account) pairs — the
    .tt OPENING cost=unknown lines', whose holding the user has already
    declared.

    `rules` (characterize/income_dating.IncomeRules, the project's): the year a
    row belongs to is the one the income stage gives it
    (IncomeRules.income_date: a Canadian trust's distribution is the
    year of its record date) — a distribution paid in March on units
    sold after its December record date is the year before's income,
    held when it was earned (backlog TJ-0811). A record date the
    income stage would accept (on or before the pay date, less than
    MAX_RECORD_LEAD_DAYS before it) is held when the books held the
    security on it, however long before the pay date it is."""
    from taxjson.characterize.income_dating import MAX_RECORD_LEAD_DAYS
    skip = set(skip_accounts)
    known = {(str(sy).upper(), str(a)) for sy, a in declared}
    ys = str(year) if year is not None else None
    grace = timedelta(days=_INCOME_GRACE_DAYS)

    def _d(s):
        try:
            return _date.fromisoformat(str(s or "")[:10])
        except ValueError:
            return None

    def _record(tx):
        """The row's record date, when a plausible one (else None)."""
        rec, pay = _d(getattr(tx, "record_date", "")), _d(tx.date)
        if rec is None or pay is None:
            return None
        return rec if 0 <= (pay - rec).days < MAX_RECORD_LEAD_DAYS \
            else None

    def _year_date(tx) -> str:
        if rules is not None:
            try:
                d = str(rules.income_date(tx) or "")
            except (TypeError, ValueError):
                d = ""
            if d and d[:10] != str(tx.date or "")[:10]:
                return d
        return _basis_date(tx, date_basis)
    rows = [t for t in txs if t.account not in skip]
    first_row: Dict[str, _date] = {}
    for t in rows:
        d = _d(t.date)
        if d and (t.account not in first_row or d < first_row[t.account]):
            first_row[t.account] = d
    run: Dict[Tuple[str, str], float] = {}
    last_held: Dict[Tuple[str, str], _date] = {}
    # The first day of the latest stretch the position was held.
    held_since: Dict[Tuple[str, str], _date] = {}
    hits: Dict[Tuple[str, str], Dict[str, Any]] = {}
    pending: List[TaxTransaction] = []     # the day's income rows

    def _held(key, day, rec=None):
        lh = last_held.get(key)
        if run.get(key, 0.0) > 1e-9 or (
                lh is not None and day is not None and day - lh <= grace):
            return True
        # Held on the record date: the latest stretch of holding covers
        # it (the units sold after it, the payment months later).
        hs = held_since.get(key)
        return (rec is not None and hs is not None and lh is not None
                and hs <= rec <= lh)

    def _settle(day):
        # A day's income is checked after the day's trades: a deemed
        # dividend booked beside the shares it delivers, or a purchase
        # stamped later the same day, is a holding.
        for tx in pending:
            key = (tx.symbol, tx.account)
            dd = _d(tx.date)
            if _held(key, dd, _record(tx)):
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
                if key in held_since:
                    held_since[nk] = min(held_since.get(nk, held_since[key]),
                                         held_since[key])
            elif key in run:
                run[key] *= ratio
            continue
        if tx.action in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            before = run.get(key, 0.0)
            run[key] = before + float(tx.quantity or 0.0)
            if before <= 1e-9 < run[key] and td is not None:
                held_since[key] = td
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
        if ys is not None and not _year_date(tx).startswith(ys):
            continue
        pending.append(tx)
    _settle(day)
    return sorted(hits.values(), key=lambda h: (h["account"], h["symbol"]))


def unchecked_accounts(cache: Path, accounts: Dict[str, Any],
                       root: Optional[Path] = None
                       ) -> List[Tuple[str, int]]:
    """[(account, open positions)] of configured accounts with open
    positions in their books and no holdings to compare them with:
    neither a `holdings` file in taxjson.toml nor a snapshot of the
    project's holdings/ folder that `tjs sanity` claims for it (by
    broker account id or file name, lib/holdings_dir — `root`: the
    project, default the folder holding `cache`)."""
    from taxjson.lib.report_model import resolve_gains_files
    try:
        resolved = resolve_gains_files(cache)
    except Exception:                               # noqa: BLE001
        return []
    from taxjson.lib import holdings_dir as _HD
    root = Path(root) if root is not None else Path(cache).parent
    snapshots: Dict[str, List[str]] = {}
    try:
        folder, _note = _HD.folder_for(root)
        if folder is not None:
            snapshots, _notes = _HD.discover(folder, accounts, root)
    except Exception:                               # noqa: BLE001
        snapshots = {}
    out = []
    accounts = configured_accounts(accounts)     # `margin = 42` is no account
    for acct, f in sorted(resolved.items()):
        cfg = accounts.get(acct)
        if cfg is None or cfg.holdings or snapshots.get(acct):
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


def in_kind_not_booked(cache: Path, year: Any) -> List[Dict[str, Any]]:
    """The in-kind moves with a registered account the run did NOT book
    (work/in_kind.json; on or before the year's end): {symbol, account
    (the taxable one), kind, date}. Their sale or purchase is not in the
    totals; `tjs sum` reads the same moves (lib/in_kind.in_kind_unbooked,
    TJ-3527)."""
    return [{"symbol": str(m.get("symbol") or ""),
             "account": str(m.get("taxable") or ""),
             "kind": str(m.get("kind") or ""),
             "date": str(m.get("date") or "")[:10]}
            for m in in_kind_unbooked(cache, year)]


def _opening_where(root: Optional[Path]) -> Dict[Tuple[str, str, str], str]:
    """{(account, date, SYMBOL): "inputs/<acct>/<file>.tt:<n>"} for the
    project's OPENING cost=unknown lines, by the symbol as written and
    as the books spell it (ticker.map / tobase.map); {} without a
    project or when a line cannot be read."""
    out: Dict[Tuple[str, str, str], str] = {}
    if root is None:
        return out
    from taxjson.lib.missing_history import (project_missing_history,
                                             read_tt_openings)
    try:
        lines = list(read_tt_openings(Path(root)))
        mh = project_missing_history(Path(root))
    except (OSError, ValueError):
        return out
    for t in lines:
        out[(str(t.account), str(t.date)[:10], str(t.symbol).upper())] = t.where
    for t in (mh.fixed.values() if mh is not None else ()):
        out.setdefault((str(t.account), str(t.date)[:10],
                        str(t.symbol).upper()), t.where)
    return out


def unknown_cost_sales(cache: Path, year: Any, *,
                       root: Optional[Path] = None,
                       sheltered: Optional[Dict[str, bool]] = None,
                       date_basis: str = "settle") -> List[Dict[str, Any]]:
    """The tax year's rows at an unknown cost, out of every total: the
    rows the taxable accounts' gains files (the ones `tjs sum` reads)
    route to `manual_reporting_required` (lib/pipeline; CA-ACB-11),
    dated on the project's basis. Each: symbol, account, date, qty,
    proceeds, currency, direction, `kind` ("sale"; "cover", a buy
    closing a short the tainted pool went into inside the files;
    "cover-before", a buy closing a short a negative OPENING
    cost=unknown line declares) and `sources`: the OPENING cost=unknown
    rows the unknown units came by, as the engine wrote them on the
    record (`unknown_cost_from`: account, symbol, date, signed
    quantity), each with the line's `where` when the project's .tt files
    hold it. Units moved in from another of your accounts
    (`own_move_from`) are left out: their cost is in the blended figures
    (CA-ACB-OWN-MOVE). Written to reports/run_summary.json, which the
    checklist reads (one copy of the list; backlog TJ-2770, TJ-3209,
    TJ-3216). [] without a readable gains file."""
    from taxjson.lib.report_model import resolve_gains_files
    ys = str(year) if year is not None else ""
    try:
        files = resolve_gains_files(Path(cache), prefer_wash=True,
                                    warn_stale=False)
    except (OSError, ValueError):
        return []
    where = _opening_where(root)
    out: List[Dict[str, Any]] = []
    for acct, f in files.items():
        if sheltered and sheltered.get(acct):
            continue
        try:
            doc = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = doc.get("manual_reporting_required") \
            if isinstance(doc, dict) else None
        for t in rows or []:
            if (not isinstance(t, dict) or t.get("qty") is None
                    or t.get("own_move_from")):
                continue
            d = str((t.get("date_settle") if date_basis == "settle"
                     else None) or t.get("date") or "")[:10]
            if ys and not d.startswith(ys):
                continue
            # (the gains file's own numbers: money is read as written)
            qty, proceeds = t.get("qty"), t.get("proceeds") or 0.0
            if not isinstance(qty, (int, float)) \
                    or not isinstance(proceeds, (int, float)):
                continue
            sources = []
            for src in t.get("unknown_cost_from") or []:
                if not isinstance(src, dict):
                    continue
                sq = src.get("quantity")
                key = (str(src.get("account") or ""),
                       str(src.get("date") or "")[:10],
                       str(src.get("symbol") or "").upper())
                sources.append({
                    "account": key[0], "symbol": str(src.get("symbol") or ""),
                    "date": key[1],
                    "quantity": sq if isinstance(sq, (int, float)) else 0.0,
                    "where": where.get(key)})
            direction = str(t.get("direction") or "LONG")
            kind = ("sale" if direction != "SHORT"
                    else "cover-before" if any(x["quantity"] < 0
                                               for x in sources)
                    else "cover")
            out.append({
                "symbol": str(t.get("symbol") or ""),
                "account": str(t.get("account") or acct),
                "date": d, "qty": abs(qty), "proceeds": proceeds,
                "currency": str(t.get("currency") or ""),
                "direction": direction, "kind": kind,
                "sources": sources,
            })
    out.sort(key=lambda e: (e["date"], e["account"], e["symbol"]))
    return out


def collect(root: Path, cfg: Dict[str, Any], *,
            missing_history: Optional[Path] = None,
            arrivals: Sequence[Any] = ()) -> Dict[str, Any]:
    """The summary's findings for the project at `root` (its work/
    books, the run just written)."""
    from taxjson.lib.country import settings_country, settings_tax_date
    cache = root / "work"
    settings = settings_of(cfg)
    accounts = configured_accounts(cfg.get("accounts"))
    country = settings_country(settings)
    year = settings.year
    basis = settings_tax_date(settings)
    sheltered = sheltered_by_name(accounts)
    crypto = {n for n, a in accounts.items() if a.crypto}
    tm = _PL.ticker_map_path(root)
    from taxjson.lib.missing_history import walk_journal_symbols
    journal = walk_journal_symbols(cache, tm if tm.is_file() else None)
    txs, failed = load_books(cache)
    covered = read_missing_history_pairs(missing_history)
    shorts = uncovered_short_sales(txs, year, sheltered=sheltered,
                                   covered=covered, date_basis=basis,
                                   journal=journal, country=country,
                                   cache=cache)
    booked = engine_booking(cache, shorts, year, date_basis=basis)
    covers = unanswered_covers(txs, year, sheltered=sheltered,
                               date_basis=basis, journal=journal,
                               country=country, cache=cache)
    from taxjson.lib.corp_actions import declared_zero_value_events
    _zrows = _zero_cost_rows(txs, year, sheltered=sheltered,
                             country=country, date_basis=basis,
                             declared=declared_zero_value_events(root))
    zero_sold = [r for r in _zrows if not r.declared and r.sold
                 and r.affects_year]
    zero_held = [r for r in _zrows if not r.declared
                 and r.still_held_qty > 0]
    # (the income stage's year of each row: characterize/income_dating)
    try:
        from taxjson.lib.income_dating import rules_for
        _rules = rules_for(country, settings)
    except ValueError:
        _rules = None
    income = income_without_position(txs, year, skip_accounts=crypto,
                                     date_basis=basis, declared=covered,
                                     rules=_rules)
    # A partly covered arrival (its .tt purchases account for some of
    # its units; nothing booked — lib/transfer_in, audit 2026-10-24
    # B41) is kept out with no cost too.
    no_cost = [a for a in arrivals
               if getattr(a, "status", "") in ("no_cost", "partly_covered")]
    book_value = [a for a in arrivals
                  if getattr(a, "status", "") == "book_value"]

    def _how(r):
        return booked.get((r.candidate.symbol, r.candidate.account),
                          BOOKED_OPEN)
    return {
        "schema_version": 1,
        "year": year,
        # Sales the gains files lack: NOT in `tjs sum`.
        "no_purchase": [{"symbol": r.candidate.symbol,
                         "account": r.candidate.account,
                         "sales": r.in_year_dispositions}
                        for r in shorts if _how(r) == BOOKED_OPEN],
        # The year's sales at an unknown cost (units an OPENING
        # cost=unknown line opens): routed out of the totals to manual
        # reporting, each named with the cost to add (backlog TJ-2770).
        "unknown_cost_sales": unknown_cost_sales(
            cache, year, root=root, sheltered=sheltered, date_basis=basis),
        # Sales the walk reads short that the gains engine booked
        # (engine_booking): in `tjs sum`.
        "no_purchase_in_sum": [{"symbol": r.candidate.symbol,
                                "account": r.candidate.account,
                                "sales": r.in_year_dispositions,
                                "booked": _how(r)}
                               for r in shorts if _how(r) != BOOKED_OPEN],
        # Buys the broker codes as closing with no short held: the books
        # carry them as new longs.
        "unbacked_covers": [{"symbol": c.symbol, "account": c.account,
                             "date": c.date, "quantity": c.unbacked_qty}
                            for c in covers],
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
        # In-kind moves NOT booked: their deemed sale (a withdrawal's
        # purchase) is missing from the totals, and the closing list
        # named none (backlog TJ-0439).
        "in_kind_not_booked": in_kind_not_booked(cache, year),
        "transfer_in_book_value": [{"symbol": a.symbol,
                                    "account": a.account, "date": a.date,
                                    "quantity": a.quantity}
                                   for a in book_value],
        "unchecked_accounts": [{"account": a, "positions": n}
                               for a, n in unchecked_accounts(
                                   cache, accounts, root)],
        "income_not_held": income,
        "books_not_read": failed,
        # One export (or broker account) in two accounts, as the run
        # recorded it: every row booked in both (backlog TJ-0478).
        "booked_twice": _booked_twice(root),
        # The parsers' warnings marked for this list (owner's rulings of
        # 2026-11-19): a key only when there is one, so a summary
        # without one keeps its bytes.
        **({"parser_warnings": pw}
           if (pw := parser_warnings(cache, list(accounts))) else {}),
    }


def parser_warnings(cache: Path, accounts: Sequence[str]
                    ) -> List[Dict[str, str]]:
    """The parsers' warnings marked for the end-of-run list (a booking
    the owner ruled is said, not refused: lib/out.END_OF_RUN_MARK), read
    back from each account's work/<account>_*.json.diag (not another
    account's whose name starts `<account>_`, as collect_diagnostics
    reads them): {"account", "text"}, the text as captured after
    `warning: ATTENTION: ` and the mark, once per account, in the
    accounts' and the files' order. Two accounts' exports of one name
    say one text: each account keeps its own (review round 1). A
    parser's warning otherwise reaches only the console mid-run, which
    scrolls away, and its .diag."""
    from taxjson.lib.out import END_OF_RUN_MARK
    head = _MARKED + END_OF_RUN_MARK
    found: List[Dict[str, str]] = []
    for acct in accounts:
        others = [a for a in accounts if a != acct
                  and a.startswith(f"{acct}_")]
        for p in sorted(cache.glob(f"{acct}_*.json.diag")):
            if any(p.name.startswith(f"{o}_") for o in others):
                continue
            try:
                lines = p.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for ln in lines:
                it = {"account": acct, "text": ln[len(head):]}
                if ln.startswith(head) and it not in found:
                    found.append(it)
    return found


def _parser_items(doc: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The marked parser warnings (parser_warnings) as the list shows
    them: one `Warning:` each, its account in front (`[margin] `, as the
    .sum names another account's message) and its text as the run's
    console shows it."""
    from taxjson.lib.out import shown_topic
    return [("Warning", f"[{w['account']}] {shown_topic(w['text'])}")
            for w in doc.get("parser_warnings") or []]


def _booked_twice(root: Path) -> List[str]:
    from taxjson.lib.fingerprint_record import recorded_booked_twice
    return recorded_booked_twice(root)


def _names(items: Sequence[Dict[str, Any]], n: int = 3) -> str:
    shown = ", ".join(f"{i['symbol']} ({i['account']})" for i in items[:n])
    return shown + (f" +{len(items) - n} more" if len(items) > n else "")


def is_clean(doc: Dict[str, Any]) -> bool:
    return not any(doc.get(k) for k in (
        "no_purchase", "unknown_cost_sales", "no_purchase_in_sum",
        "unbacked_covers", "zero_cost_sold",
        "zero_cost_held", "zero_cost_declared", "transfer_in_no_cost",
        "in_kind_not_booked", "unchecked_accounts", "income_not_held",
        "booked_twice", "parser_warnings"))


def _accounts_shown(items: Sequence[Dict[str, Any]], n: int = 6) -> str:
    """`margin (8), qt (1)` — the first `n` accounts and `+N more`, so
    the names add up to the count the line states."""
    shown = ", ".join(f"{u['account']} ({u['positions']})"
                      for u in items[:n])
    return shown + (f" +{len(items) - n} more" if len(items) > n else "")


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def render(doc: Dict[str, Any], *, mh_name: str = "missing_history.tt",
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


def _distinct(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`items` with one entry per (symbol, account), in order."""
    return list({(i["symbol"], i["account"]): i for i in items}.values())


def _syms(items: Sequence[Dict[str, Any]], n: int = 2) -> str:
    """`GHOSTQ.TO, OLDCO.US +1 more`: the symbols only (one-line form)."""
    shown = ", ".join(str(i["symbol"]) for i in items[:n])
    return shown + (f" +{len(items) - n} more" if len(items) > n else "")


def unknown_cost_phrase(items: Sequence[Dict[str, Any]]) -> str:
    """What the rows at an unknown cost are, by `kind`: `1 sale at an
    unknown cost`, `2 buys closing a short at an unknown cost` (the
    tainted pool went short inside the files), `1 buy closing a short
    sold before your files` (a negative OPENING cost=unknown line),
    joined by `and`."""
    sales = [i for i in items if i.get("kind", "sale") == "sale"]
    covers = [i for i in items if i.get("kind") == "cover"]
    before = [i for i in items if i.get("kind") == "cover-before"]
    parts = []
    if sales:
        parts.append(f"{_n(len(sales), 'sale', 'sales')} at an unknown cost")
    if covers:
        parts.append(f"{_n(len(covers), 'buy', 'buys')} closing a short at "
                     f"an unknown cost")
    if before:
        parts.append(f"{_n(len(before), 'buy', 'buys')} closing a short "
                     f"sold before your files")
    return " and ".join(parts)


def unknown_cost_row(i: Dict[str, Any]) -> str:
    """`QZQ.TO 2025-06-02 in margin`: the row, as the closing line
    names it (no parentheses: the line's width rule strips those)."""
    return f"{i['symbol']} {i['date']} in {i['account']}"


def unknown_cost_lines(items: Sequence[Dict[str, Any]]
                       ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """The OPENING cost=unknown lines the rows' units came by, keyed
    (account, date, symbol) in order — only when every row names at
    least one (the engine wrote `unknown_cost_from`); {} otherwise (a
    hand-written book, an older run). The one copy of the rule the run's
    texts and the checklist name a line by (backlog TJ-3216)."""
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for i in items:
        srcs = i.get("sources") or []
        if not srcs:
            return {}
        for src in srcs:
            out.setdefault((src["account"], src["date"], src["symbol"]), src)
    return out


def line_name(src: Dict[str, Any], parens: bool = True) -> str:
    """How a text names an OPENING cost=unknown line: its file and line
    when the project holds it, with the line's own fields (in
    parentheses, or after a colon inside a list), else the fields."""
    own = f"OPENING {src['date']} {src['symbol']} in {src['account']}"
    if not src.get("where"):
        return f"the line {own}"
    return f"{src['where']} ({own})" if parens else f"{src['where']}: {own}"


def unknown_cost_advice(items: Sequence[Dict[str, Any]]
                        ) -> Tuple[str, str, str]:
    """(kind, the one-line fix, the whole advice) for the rows at an
    unknown cost. `kind`: "cost" when every line is a long opening (give
    it the cost, or `tjs opening`), "short-sale" when every line is a
    negative one (the short's own sale is what to add), "by-hand"
    otherwise (no line to name, or both)."""
    from taxjson.lib.report_fmt import qty_text
    lines = unknown_cost_lines(items)
    one = len(items) == 1
    it = "it" if one else "them"
    if lines and all(src["quantity"] > 0 for src in lines.values()):
        if len(lines) == 1:
            (src,) = lines.values()
            cur = next((i.get("currency") for i in items
                        if i.get("currency")), "") or "<CUR>"
            return ("cost", "add the cost to its OPENING line",
                    f"give its OPENING cost=unknown line, {line_name(src)}, "
                    f"the cost of the {qty_text(src['quantity'])} "
                    f"{src['symbol']} held before your files (`OPENING "
                    f"{src['date']} {src['symbol']} "
                    f"{qty_text(src['quantity'])} {cur} <total cost>`), or "
                    f"`tjs opening`")
        shown = "; ".join(line_name(src, parens=False)
                          for src in list(lines.values())[:3]) \
            + (f" +{len(lines) - 3} more" if len(lines) > 3 else "")
        return ("cost", "add the cost to their OPENING lines",
                f"give their OPENING cost=unknown lines ({shown}) the cost "
                f"of the units held before your files (`OPENING <date> "
                f"<SYMBOL> <qty> <CUR> <total cost>` each), or `tjs opening`")
    if lines and all(src["quantity"] < 0 for src in lines.values()):
        shown = "; ".join(line_name(src, parens=False)
                          for src in list(lines.values())[:3]) \
            + (f" +{len(lines) - 3} more" if len(lines) > 3 else "")
        return ("short-sale", "add the short's own sale as a .tt line",
                f"the short's own sale is not in your files: add it as a "
                f".tt line in place of the OPENING cost=unknown line "
                f"({shown}), or report the "
                f"{'buy' if one else 'buys'} by hand (`tjs form-export`)")
    named = (f"; the units came by {'; '.join(line_name(src, parens=False) for src in list(lines.values())[:3])}"
             if lines else
             "; `tjs find-missing-history` names what opened the units")
    return ("by-hand", "`tjs find-missing-history`",
            f"report {it} by hand (`tjs form-export` lists {it}){named}")


def _unknown_cost_item(items: Sequence[Dict[str, Any]],
                       width_: Optional[int]) -> str:
    """The closing block's one line for the rows at an unknown cost:
    always one line, always naming the first row's symbol and date; the
    fix text, the long phrase, the count of the others and the account
    give way to the width, in that order (backlog TJ-3208, TJ-3214,
    TJ-3236)."""
    n = len(items)
    first = items[0]
    row = unknown_cost_row(first)
    more = f" and {n - 1} more" if n > 1 else ""
    kind, fix, _long = unknown_cost_advice(items)
    phrase = unknown_cost_phrase(items)
    short = f"{_n(n, 'trade', 'trades')} at an unknown cost"
    # Each shorter than the last: the fix text, the long phrase, "and
    # N more", then the account go; the symbol and date never do
    # (TJ-3236: an option symbol in a long account's name).
    texts = [f"NOT in the totals: {phrase}: {row}{more} — {fix}",
             f"NOT in the totals: {phrase}: {row}{more}",
             f"NOT in the totals: {short}: {row}{more}",
             f"NOT in the totals: {short}: {row}",
             f"NOT in the totals: {short}: {first['symbol']} {first['date']}"]
    for text in texts:
        if not width_ or width_ <= 0 or len("Warning: ") + len(text) <= width_:
            return text
    return texts[-1]


def _unknown_cost_detail(items: Sequence[Dict[str, Any]], yr: Any) -> str:
    """The whole text for the rows at an unknown cost (`tjs run
    --details`): each row, the OPENING cost=unknown line its units came
    by and the ways to settle it (unknown_cost_advice)."""
    from taxjson.lib.report_fmt import qty_text
    rows = "; ".join(
        f"{i['symbol']} ({i['account']}) on {i['date']}, "
        f"{qty_text(i['qty'])} units"
        for i in items[:3]) + (f" +{len(items) - 3} more"
                               if len(items) > 3 else "")
    kind, _fix, advice = unknown_cost_advice(items)
    one = len(items) == 1
    it = "it" if one else "them"
    text = f"{unknown_cost_phrase(items)} in {yr}, NOT in `tjs sum`: {rows}."
    if kind != "cost":
        # (the advice already offers the report by hand)
        return f"{text} {advice[0].upper()}{advice[1:]}."
    return (f"{text} {advice[0].upper()}{advice[1:]}; until then, report "
            f"{it} by hand (`tjs form-export` lists {it}).")


# The closing block's one-line form (docs/output-style.md, Essentials
# first): at most this many lines under its heading.
CONCISE_BUDGET = 6


def concise_items(doc: Dict[str, Any],
                  width_: Optional[int] = None) -> List[Tuple[str, str]]:
    """Each finding as (kind, one line naming the command with the
    detail): the default view of the run's closing block. Every warning
    class render_blocks says is here; the explanation is in --details.
    `width_`: the console's, for the item that shortens itself to one
    line (the rows at an unknown cost)."""
    items: List[Tuple[str, str]] = []
    tw = doc.get("booked_twice") or []
    if tw:
        items.append(("Warning", f"rows booked in two accounts "
                      f"({'; '.join(tw)}) — keep each export in one "
                      f"inputs/<account>/ folder, then `tjs run`"))
    np_ = doc.get("no_purchase") or []
    if np_:
        items.append(("Warning", f"NOT in the totals: "
                      f"{_n(len(np_), 'sale', 'sales')} with no purchase "
                      f"({_syms(np_)}) — `tjs find-missing-history`"))
    uc = doc.get("unknown_cost_sales") or []
    if uc:
        # The first row named (symbol, date, account) with the cost to
        # add, one line whatever the width (backlog TJ-2770, TJ-3208);
        # the line's file and `tjs opening` are in --details and in
        # `tjs checklist`.
        items.append(("Warning", _unknown_cost_item(uc, width_)))
    ins = doc.get("no_purchase_in_sum") or []
    cov = [i for i in ins if i.get("booked") == BOOKED_SHORT_COVER]
    mat = [i for i in ins if i.get("booked") != BOOKED_SHORT_COVER]
    if cov:
        items.append(("Warning", f"{_n(len(cov), 'sale', 'sales')} with no "
                      f"purchase booked as shorts covered later "
                      f"({_syms(cov)}) — `tjs find-missing-history`"))
    ub = doc.get("unbacked_covers") or []
    if ub:
        items.append(("Warning", f"{_n(len(ub), 'cover', 'covers')} of a "
                      f"short opened before your files, booked as a new "
                      f"long ({_syms(ub)}) — `tjs find-missing-history`"))
    zs, zh = doc.get("zero_cost_sold") or [], doc.get("zero_cost_held") or []
    if zs or zh:
        # A $0 lot part sold and part held is one position (backlog
        # TJ-0044: "2 positions ... (SAMPK.TO, SAMPK.TO)").
        zp = _distinct(zs + zh)
        items.append(("Warning", f"{_n(len(zp), 'position', 'positions')}"
                      f" at a $0 cost ({_syms(zp)}) — "
                      f"`tjs find-missing-history`"))
    tn = doc.get("transfer_in_no_cost") or []
    if tn:
        items.append(("Warning", f"{_n(len(tn), 'transfer-in', 'transfer-ins')}"
                      f" kept out with no cost ({_syms(tn)}) — "
                      f"`tjs transfers`"))
    ik = doc.get("in_kind_not_booked") or []
    if ik:
        items.append(("Warning", f"NOT in the totals: "
                      f"{_n(len(ik), 'in-kind move', 'in-kind moves')} "
                      f"with a registered account NOT booked "
                      f"({_syms(_distinct(ik))}) — `tjs run "
                      f"--details`"))
    inc = doc.get("income_not_held") or []
    if inc:
        items.append(("Warning", f"{_n(len(inc), 'security', 'securities')}"
                      f" paid income the books do not hold ({_syms(inc)}) — "
                      f"`tjs sanity`"))
    items += _parser_items(doc)
    if mat:
        items.append(("Info", f"{_n(len(mat), 'position', 'positions')} "
                      f"read short by the history check "
                      f"{'is' if len(mat) == 1 else 'are'} in the totals "
                      f"({_syms(mat)}) — `tjs find-missing-history`"))
    zd = doc.get("zero_cost_declared") or []
    if zd:
        items.append(("Info", f"{_n(len(zd), 'position', 'positions')} at "
                      f"the $0 cost you declared ({_syms(zd)}) — "
                      f"`tjs spinoffs`"))
    un = doc.get("unchecked_accounts") or []
    if un:
        items.append(("Info", f"{len(un)} "
                      f"{'account' if len(un) == 1 else 'accounts'} with "
                      f"positions not checked against the broker's "
                      f"holdings — `tjs sanity`"))
    return items


def render_blocks(doc: Dict[str, Any], *,
                  mh_name: str = "missing_history.tt",
                  width_: Optional[int] = None,
                  concise: bool = False,
                  details_hint: bool = False) -> List[List[str]]:
    """render() as its entries (the heading, each finding, the closing
    line), each a list of lines: lib/out.show_blocks prints them with a
    blank line after an entry of more than one line. `concise`: the
    run's default console — one line per finding (concise_items), at
    most CONCISE_BUDGET lines under the heading (the Info findings folded
    into one when there are more), and the closing line naming
    `tjs run --details` when `details_hint`."""
    if is_clean(doc) and not (concise and details_hint):
        return []
    from taxjson.lib import out
    if concise:
        w = out.width() if width_ is None else width_
        heading = ["==> Before you trust these numbers "
                   "(docs/getting-started.md, step 5)"]
        items = concise_items(doc, w)
        room = CONCISE_BUDGET - 1
        if len(items) > room:
            warns = [i for i in items if i[0] == "Warning"]
            infos = [i for i in items if i[0] != "Warning"]
            keep = max(0, room - len(warns) - 1)
            items = warns + infos[:keep]
            rest = infos[keep:]
            if rest:
                items.append(("Info", f"{len(rest)} more check(s) not yet "
                              f"made — `tjs checklist --run`"))
        closing = ("Info: Then `tjs checklist --run` (the next step); "
                   "each message's detail: `tjs run --details`"
                   if details_hint else
                   "Info: Then run `tjs checklist --run`: it checks "
                   "every step and names the next one.")

        def _one(text: str) -> List[str]:
            if 0 < w < len(text):
                # Still too long: the names go (the command lists them).
                import re as _re
                text = _re.sub(r" \([^()]*\)(?= — )", "", text, count=1)
            return out.wrap(text, w, "", "" if w > 0 else "  ")
        return (([heading] if items else [])
                + [_one(f"{kind}: {it}") for kind, it in items] + [_one(closing)])
    yr = doc.get("year")
    items: List[Tuple[str, str]] = []
    for tw in doc.get("booked_twice") or []:
        items.append(("Warning", f"rows booked in two accounts ({tw}): "
                      f"every total counts them twice; keep each broker "
                      f"account's exports in one inputs/<account>/ folder "
                      f"(delete the copy), then `tjs run`."))
    np_ = doc.get("no_purchase") or []
    if np_:
        items.append(("Warning", f"{_n(len(np_), 'position', 'positions')} "
                     f"sold in {yr} with no purchase in your files, not in "
                     f"{mh_name}: {_names(np_)}. Those sales are NOT in "
                     f"`tjs sum`; run `tjs find-missing-history`. A short "
                     f"sale still open reads the same: it is in no total "
                     f"until it is covered."))
    uc = doc.get("unknown_cost_sales") or []
    if uc:
        items.append(("Warning", _unknown_cost_detail(uc, yr)))
    ins = doc.get("no_purchase_in_sum") or []
    cov = [i for i in ins if i.get("booked") == BOOKED_SHORT_COVER]
    mat = [i for i in ins if i.get("booked") != BOOKED_SHORT_COVER]
    if cov:
        items.append(("Warning", f"{_n(len(cov), 'position', 'positions')} "
                     f"sold in {yr} with no purchase in your files, not in "
                     f"{mh_name}: {_names(cov)}. `tjs sum` books those "
                     f"sales as short sales closed by a later purchase (or "
                     f"shares moved in from another of your accounts), at "
                     f"its cost — not yours if you held the shares before "
                     f"your files start; run `tjs find-missing-history`."))
    if mat:
        items.append(("Info", f"{_n(len(mat), 'position', 'positions')} "
                     f"read short in {yr} by `tjs find-missing-history`"
                     f": {_names(mat)}. The gains engine sold them from "
                     f"purchases in your files, so they are in `tjs "
                     f"sum`; the check reads that day's rows in another "
                     f"order: compare it with the broker's trades."))
    ub = doc.get("unbacked_covers") or []
    if ub:
        items.append(("Warning", f"{_n(len(ub), 'buy', 'buys')} the broker "
                     f"codes as closing a short your files do not hold: "
                     f"{_names(ub)}. The books carry each as a new long, in "
                     f"the cost of later sales, and the cover's gain or "
                     f"loss is in no total. Add the short sale, or the `.tt` "
                     f"line `tjs find-missing-history "
                     f"--write-missing-history` drafts."))
    zs, zh = doc.get("zero_cost_sold") or [], doc.get("zero_cost_held") or []
    if zs or zh:
        parts = ([f"{len(zs)} sold in {yr}"] if zs else []) \
            + ([f"{len(zh)} still held"] if zh else [])
        zp = _distinct(zs + zh)
        items.append(("Warning", f"{_n(len(zp), 'position', 'positions')}"
                     f" at a $0 cost ({', '.join(parts)}): "
                     f"{_names(zp)}. Run `tjs "
                     f"find-missing-history`."))
    zd = doc.get("zero_cost_declared") or []
    if zd:
        shown = ", ".join(
            f"{i['symbol']} ({i['account']}, event "
            f"{', '.join(i.get('events') or []) or '?'})" for i in zd[:3]) \
            + (f" +{len(zd) - 3} more" if len(zd) > 3 else "")
        one = zd[0]
        how = (f"`tjs elect {one['account']} --redo --event "
               f"{(one.get('events') or ['ID'])[0]}`"
               if len(zd) == 1 and len(one.get("events") or []) == 1
               else "`tjs elect ACCOUNT --redo --event ID`")
        items.append(("Info", f"{_n(len(zd), 'position', 'positions')} at "
                     f"the $0 cost you declared (fmv_per_share=0 in the "
                     f"spin-off's election): {shown}. To change it: "
                     f"{how}."))
    tn = doc.get("transfer_in_no_cost") or []
    if tn:
        items.append(("Warning", f"{_n(len(tn), 'transfer-in', 'transfer-ins')}"
                     f" from outside your books kept out with no cost: "
                     f"{_names(tn)}. Run `tjs transfers`."))
    ik = doc.get("in_kind_not_booked") or []
    if ik:
        items.append(("Warning", f"{_n(len(ik), 'in-kind move', 'in-kind moves')}"
                     f" with a registered account NOT booked: "
                     f"{_names(_distinct(ik))}. What each books (a "
                     f"contribution's sale, a withdrawal's purchase) is "
                     f"NOT in `tjs sum`; the in-kind warning above "
                     f"names the INKIND line for each."))
    un = doc.get("unchecked_accounts") or []
    if un:
        items.append(("Info", f"{_n(len(un), 'account', 'accounts')} with "
                     f"open positions and no holdings file to check them "
                     f"against: {_accounts_shown(un)}. Run `tjs "
                     f"sanity` with the broker's positions."))
    inc = doc.get("income_not_held") or []
    if inc:
        items.append(("Warning", f"{_n(len(inc), 'security', 'securities')} "
                     f"paid income in {yr} that the books do not hold (a "
                     f"holding with no purchase in your files?): "
                     f"{_names(inc)}. Run `tjs sanity`."))
    items += _parser_items(doc)
    w = out.width() if width_ is None else width_
    hang = "" if w > 0 else "  "
    blocks = [["==> Before you trust these numbers "
               "(docs/getting-started.md, step 5)"]]
    for kind, it in items:
        blocks.append(out.wrap(f"{kind}: {it}", w, "", hang))
    blocks.append(out.wrap("Info: Then run `tjs checklist --run`: it "
                           "checks every step and names the next one.", w,
                           "", hang))
    return blocks
