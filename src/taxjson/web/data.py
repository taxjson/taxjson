"""Data access for the web UI.

Two sources, by design:
  * Pre-computed artifacts the pipeline already wrote (holdings.toml, the
    *.rpt reports) — fast, and reflect the last `taxjson run`.
  * The library itself (taxjson.lib.core) — for LIVE what-if scenarios that
    can't be precomputed.

Pure Python (no FastAPI), so it is unit-testable without the [web] extra.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from taxjson.lib.tomlcompat import tomllib

from taxjson.lib.core import load_transactions, get_tax_rules, TaxTransaction
from .context import ProjectContext


class UnknownAccountError(ValueError):
    """A request named an account that isn't in taxjson.toml. Raised
    before any filesystem access: `account` is interpolated into
    reports/work paths, so validating here also closes the
    `?account=../../x` traversal the 2026-07b audit flagged."""


class ReportArtifactError(RuntimeError):
    """A pipeline artifact exists but can't be parsed (corrupt TOML/JSON).
    Routes render this as an error state — one bad file must not 500 the
    whole dashboard."""


def _require_account(ctx: ProjectContext, account: str) -> None:
    if ctx.account(account) is None:
        raise UnknownAccountError(
            f"no account {account!r} in taxjson.toml (accounts: "
            f"{', '.join(a.name for a in ctx.accounts) or 'none'})")


# ----------------------------------------------------------------- holdings
def load_holdings(ctx: ProjectContext, account: str) -> List[Dict[str, Any]]:
    """Positions for an account, from reports/<account>_holdings.toml (incl.
    base-currency cost and the per-position `trades` history)."""
    _require_account(ctx, account)
    path = ctx.reports / f"{account}_holdings.toml"
    if not path.exists():
        return []
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        raise ReportArtifactError(
            f"{path.name} could not be read ({e}) — re-run `taxjson run` "
            f"or fix/delete the file") from e
    rows = doc.get("holding", [])
    if not isinstance(rows, list) or any(not isinstance(h, dict)
                                         for h in rows):
        # `holding = 5` used to reach the dashboard's len() and 500 the
        # page (S078-18): the same banner as an undecodable file.
        raise ReportArtifactError(
            f"{path.name} has no [[holding]] table array (holding = "
            f"{type(rows).__name__}) — re-run `taxjson run` or fix/delete "
            f"the file")
    # A row's trade history is iterated by the detail page: `trades = 5`
    # was a TypeError there and an HTTP 500 (A2-1187).
    for h in rows:
        tr = h.get("trades", [])
        if not isinstance(tr, list) or any(not isinstance(t, dict)
                                           for t in tr):
            raise ReportArtifactError(
                f"{path.name}: holding {str(h.get('symbol', '?'))!r} has "
                f"trades = {type(tr).__name__}, not a list of tables — "
                f"re-run `taxjson run` or fix/delete the file")
    return rows


def holdings_accounts(ctx: ProjectContext) -> List[str]:
    return [a.name for a in ctx.accounts
            if (ctx.reports / f"{a.name}_holdings.toml").exists()]


def find_holding(ctx: ProjectContext, account: str, symbol: str
                 ) -> Optional[Dict[str, Any]]:
    """The holding for `symbol` — an exact match, else a case-insensitive
    one (a hand-typed `abc.to` read as not held — S079-04)."""
    rows = load_holdings(ctx, account)
    hit = next((h for h in rows if h.get("symbol") == symbol), None)
    if hit is None:
        want = (symbol or "").strip().upper()
        hit = next((h for h in rows
                    if str(h.get("symbol") or "").upper() == want), None)
    return hit


# -------------------------------------------------------------- wash radar
# Row fields the page renders, and which of them may be null.
_RADAR_TEXT_FIELDS = ("ticker", "taxable_display", "sheltered_display",
                      "clears_at", "advisory", "category")
_RADAR_NULLABLE = ("clears_at", "category")


def wash_radar_scope_note(ctx: ProjectContext, account: str) -> str:
    """The CA-PLAN-04 / US-PLAN-04 disclosure for the radar page: the
    verdicts cover the project's own accounts only (A2-0374 — the text
    report and its sidecar carried it, the page did not). Always the
    project country's own wording."""
    return _scope_note(ctx.country)


def _clears_in_display(clears_at: Optional[str],
                       today: Optional[date] = None) -> str:
    """VIEW-TIME countdown from an absolute clears_at date. The generation-day
    "clears in Nd" string in the .rpt goes stale the day after a run; the JSON
    sidecar carries the absolute date so the UI can always show the truth."""
    if not clears_at:
        return "-"
    try:
        target = date.fromisoformat(clears_at)
    except ValueError:
        return "-"
    days = (target - (today or date.today())).days
    # Same "date (days)" shape as the radar report's CLEARS column.
    return "cleared" if days <= 0 else f"{clears_at} ({days}d)"


def _days_until(iso: Optional[str], today: Optional[date] = None
                ) -> Optional[int]:
    try:
        return (date.fromisoformat(iso) - (today or date.today())).days
    except (TypeError, ValueError):
        return None


def radar_staleness(ctx: ProjectContext, account: str) -> Optional[str]:
    """A message when the radar report is OLDER than the books it
    judges: `run --account <x>` rebuilds that account's base book (and
    sheltered_base.json) but not the cross-account radar, so a new
    registered-account buy left the page saying CLEAR / "safe to sell
    at a loss" over a position the live radar marks LOCKED (S038-09,
    web half). None when fresh or when there is no report."""
    paths = [ctx.reports / f"wash_radar_{account}.json",
             ctx.reports / f"wash_radar_{account}.rpt"]
    have = [p for p in paths if p.is_file()]
    if not have:
        return None
    built = min(p.stat().st_mtime for p in have)
    books = ([p for p in ctx.cache.glob("*_base.json")
              if not p.name.startswith(".")]
             if ctx.cache.is_dir() else [])
    newer = sorted(p.name for p in books if p.stat().st_mtime > built + 1)
    if not newer:
        return None
    return (f"The wash radar for {account} is older than the books "
            f"({', '.join(newer[:3])}{' ...' if len(newer) > 3 else ''} "
            f"changed after it was built — e.g. a `taxjson run --account` "
            f"run, which skips the cross-account radar). Its verdicts, "
            f"CLEAR included, may be wrong: run a full `taxjson run`, or "
            f"`taxjson wash-radar` for a live view.")


def wash_radar_sections(ctx: ProjectContext, account: str = "margin",
                        today: Optional[date] = None
                        ) -> List[Dict[str, Any]]:
    """Ordered radar sections for an account. Prefers the structured JSON
    sidecar (written by the pipeline since the report-model work) — with
    countdowns computed at VIEW time — and falls back to parsing the
    fixed-width .rpt for projects that haven't re-run yet."""
    import json as _json
    if account != "COMBINED":
        # COMBINED is the pipeline's cross-account pseudo-account —
        # its report exists without a [accounts.*] entry.
        _require_account(ctx, account)
    json_path = ctx.reports / f"wash_radar_{account}.json"
    stale_tag = (" [STALE radar — the books changed after it was built; "
                 "re-run `taxjson run` (or `taxjson wash-radar` for a "
                 "live view) before trading on it]"
                 if radar_staleness(ctx, account) else "")
    if json_path.exists():
        # A sidecar that exists but cannot be used is an ERROR banner.
        # It used to fall back to the generation-time .rpt in silence —
        # stale countdowns, and "No wash-radar report yet" when the .rpt
        # was gone too (S078-17); a wrong-shape document 500'd the page
        # (S078-18).
        def _bad(why):
            return ReportArtifactError(
                f"{json_path.name} could not be read ({why}) — re-run "
                f"`taxjson run` or fix/delete the file")
        try:
            doc = _json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise _bad(e) from e
        # A document without the "sections" key is not a radar report
        # ({} fell through to the stale .rpt, {"Sections": ...} read as
        # "no report yet" — A2-1188), and a row field of the wrong type
        # 500'd the page with a raw TypeError (A2-0696).
        secs = doc.get("sections") if isinstance(doc, dict) else None
        if not isinstance(secs, list) or any(
                not isinstance(sec, dict)
                or not isinstance(sec.get("rows", []), list)
                or any(not isinstance(r, dict) for r in sec.get("rows", []))
                for sec in secs):
            raise _bad("not a radar document: expected {\"sections\": "
                       "[{\"title\", \"rows\": [{...}]}]}")
        for sec in secs:
            if not isinstance(sec.get("title", ""), str):
                raise _bad("a section title is not text")
            for r in sec.get("rows", []):
                for k in _RADAR_TEXT_FIELDS:
                    v = r.get(k, "")
                    if not (isinstance(v, str)
                            or (v is None and k in _RADAR_NULLABLE)):
                        raise _bad(f"row field {k!r} is "
                                   f"{type(v).__name__}, not text")
        sections: List[Dict[str, Any]] = []
        for sec in secs:
            def _row(r):
                ci = _clears_in_display(r.get("clears_at"), today)
                adv = r.get("advisory", "")
                if r.get("category") == "VIOLATION":
                    # A VIOLATION's date is the rescue DEADLINE: the
                    # LAST trade date for the full exit, inclusive
                    # (the radar's own text says "by" it). The
                    # deadline day itself is still actionable — it
                    # rendered as "deadline passed" (S079-06).
                    vdays = _days_until(r.get("clears_at"), today)
                    if vdays == 0:
                        ci = (f"{r.get('clears_at')} (0d — sell "
                              f"TODAY, last trade day)")
                    elif vdays is not None and vdays < 0:
                        ci = "deadline passed — loss denied"
                        if adv:
                            adv += (" [rescue deadline has PASSED "
                                    "since this report was generated "
                                    "— unless the position was "
                                    "exited in time, the loss is "
                                    "denied; re-run `taxjson run` to "
                                    "reclassify]")
                elif ci == "cleared" and adv:
                    # Category membership and advisory text are
                    # GENERATION-time; weeks later the page said
                    # LOCKED/"do not sell" next to "cleared"
                    # (2026-09 audit). Say which one is current.
                    adv += (" [window has CLEARED since this "
                            "report was generated — re-run "
                            "`taxjson run` to reclassify]")
                return {
                    "ticker": r.get("ticker", ""),
                    "taxable": r.get("taxable_display", ""),
                    "sheltered": r.get("sheltered_display", ""),
                    "clears_in": ci,
                    "advisory": adv + stale_tag,
                }
            rows = [_row(r) for r in sec.get("rows", [])]
            if rows:
                sections.append({"title": f"{sec.get('title', '')} "
                                          f"({len(rows)})",
                                 "rows": rows})
        return sections
    path = ctx.reports / f"wash_radar_{account}.rpt"
    if not path.exists():
        return []
    # Same one-bad-file-must-not-500 contract as load_holdings: an
    # unreadable/undecodable .rpt renders as an error banner, not a
    # traceback (this was the module's one unguarded report read).
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ReportArtifactError(
            f"{path.name} could not be read ({e}) — re-run `taxjson run` "
            f"or fix/delete the file") from e
    sections: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for ln in text.splitlines():
        if ln.startswith("--- "):
            cur = {"title": ln.strip("- ").strip(), "rows": []}
            sections.append(cur)
        elif cur is not None and "|" in ln:
            parts = [p.strip() for p in ln.split("|")]
            if len(parts) >= 5 and parts[0] not in ("TICKER", ""):
                cur["rows"].append({
                    "ticker": parts[0], "taxable": parts[1],
                    "sheltered": parts[2], "clears_in": parts[3],
                    "advisory": parts[4] + stale_tag,
                })
    return [s for s in sections if s["rows"]]


# -------------------------------------------------------------- freshness
def freshness(ctx: ProjectContext) -> Optional[Dict[str, Any]]:
    """Are the reports/ artifacts older than the inputs they were built
    from? Returns {"generated": iso-date, "stale": bool, "why": str} or
    None when there are no reports yet (the templates show their own
    empty-state hints).

    The same rule as the checklist's run-clean step (S078-21): every
    input `taxjson run` reads counts — the project-root maps
    (ticker.map, distributions.map, phantoms.json,
    ticker_extraction_overrides.txt, crypto_ticker.map) as well as
    inputs/** and taxjson.toml — compared by CONTENT against what the
    last full run recorded, else by mtime against the OLDEST per-account
    report. The newest report used to decide, so a `run --account tfsa`
    after a margin edit cleared the banner over stale margin numbers."""
    from datetime import datetime as _dt
    from taxjson.lib.checklist import (_fingerprint_diff, _input_paths,
                                       _load_fingerprint, input_fingerprint)
    sums = sorted(ctx.reports.glob("*.sum")) if ctx.reports.is_dir() else []
    report_files = sums or ([p for p in ctx.reports.glob("*") if p.is_file()]
                            if ctx.reports.is_dir() else [])
    if not report_files:
        return None
    oldest = min(p.stat().st_mtime for p in report_files)
    cfg = {"accounts": {a.name: {} for a in ctx.accounts}}
    why = ""
    recorded = _load_fingerprint(ctx.root)
    if recorded is not None:
        why = _fingerprint_diff(recorded, input_fingerprint(ctx.root, cfg))
        extra = [ctx.root / "crypto_ticker.map"]
    else:
        # The checklist's own input set (its run-clean fallback): every
        # file under inputs/ counted before, so a Finder .DS_Store or a
        # stray notes file marked the dashboard stale while the
        # checklist called the run clean (A2-1185).
        extra = _input_paths(ctx.root, cfg) + [ctx.root / "crypto_ticker.map"]
    if not why:
        newer = [p for p in extra
                 if p.is_file() and p.stat().st_mtime > oldest + 1]
        if newer:
            why = "changed: " + ", ".join(
                sorted({p.relative_to(ctx.root).as_posix()
                        for p in newer})[:3])
    return {
        "generated": _dt.fromtimestamp(oldest).strftime("%Y-%m-%d %H:%M"),
        "stale": bool(why),
        "why": why,
    }


# ------------------------------------------------------- what-if sell (live)
def _price_to_base(ctx: ProjectContext, price: float,
                   price_currency: Optional[str], on: str):
    """Convert a per-share `price` given in `price_currency` to the base
    currency at the `on` date, using the pipeline's rates file. Returns
    (price_base, fx_rate, note); (None, None, reason) when the currency
    has no rate on or before `on`. No-op when the price is already in
    base.

    The rate is the LATEST one on or before `on` (rates end at the last
    `taxjson run`), labelled with its date when it is older than
    harvest's _STALE_RATE_DAYS — the same rule as `taxjson harvest`
    (the page used 4 days against harvest's 7, A2-1179). A hardcoded 1.35 used
    to replace any rate more than 5 days old, and was applied to GBP or
    EUR prices too (R1-149)."""
    base = ctx.base_currency.strip().upper()
    cur = (price_currency or "").strip().upper()
    if not cur or cur == base:
        return price, 1.0, None
    from datetime import datetime
    from taxjson.bin.taxjson_convert_currency import load_exchange_rates
    from taxjson.lib.price_chain import latest_rate
    rates_file = ctx.cache / "to_base.csv"
    history = (load_exchange_rates(rates_file, target_curr=base)
               if rates_file.exists() else {})
    rate, rate_date = latest_rate(history, cur, on)
    if rate is None:
        return None, None, (
            f"no {cur}->{base} rate on or before {on} in "
            f"work/to_base.csv — the what-if cannot convert a {cur} "
            f"price; enter it in {base}, or run `taxjson run` for a "
            f"project that holds {cur}")
    note = None
    try:
        age = (datetime.strptime(on, "%Y-%m-%d")
               - datetime.strptime(rate_date, "%Y-%m-%d")).days
    except ValueError:
        age = 0
    from taxjson.bin.taxjson_harvest import _STALE_RATE_DAYS
    if age > _STALE_RATE_DAYS:
        note = (f"{cur}->{base} rate {rate:g} is from {rate_date}, "
                f"{age} days before {on} (rates end at the last "
                f"`taxjson run`) — re-run `taxjson run` for a current "
                f"rate")
    return price * rate, rate, note


def what_if_sell(ctx: ProjectContext, account: str, symbol: str,
                 qty: float, price: float, on: Optional[str] = None,
                 price_currency: Optional[str] = None) -> Dict[str, Any]:
    """Simulate selling `qty` of `symbol` at `price` today, and report the
    realized gain/loss and whether it would be a superficial loss / wash sale —
    computed live by the same engine the CLI uses.

    `price` may be given in the holding's NATIVE currency: pass
    `price_currency` (e.g. 'USD') and it is converted to the base currency at
    the sale date before the engine (which works on the base-currency
    <account>_base.json) sees it. With `price_currency` omitted, `price` is
    taken to already be in the base currency.

    Books are prepared by the SHARED lib/pipeline.prepare_books (the same
    preprocessing taxjson-gains runs), so the simulation can't drift from
    the CLI. Preprocessing problems are surfaced in the result's
    `warnings` list (render-ready, like fx_note) instead of being
    swallowed.
    """
    _require_account(ctx, account)
    import math
    # Book symbols are upper-case; a hand-typed `abc.to` found no
    # position and was reported as "closed" (S079-04). The CLI's
    # buy-check/sell-check upper-case the same way.
    symbol = (symbol or "").strip().upper()
    # Reject before anything touches the engine: qty/price of nan/inf
    # bubbled a non-JSON-compliant float into the response and 500'd
    # the route (REVIEW #30); a negative price produced internally
    # contradictory ok-styled numbers (proceeds -150, gain +50 —
    # REVIEW #31).
    if not (math.isfinite(qty) and qty != 0):
        return {"ok": False, "warnings": [],
                "reason": f"qty must be a non-zero number of units (a "
                          f"positive qty sells a long position, a "
                          f"negative qty buys to cover a short), got "
                          f"{qty!r}"}
    # A SHORT position (a written option, a short sale) closes with a
    # BUY: a negative qty simulates the buy-to-cover. The form's default
    # qty for a short holding is already negative (S079-00).
    side = "sell" if qty > 0 else "cover"
    if not (math.isfinite(price) and price > 0):
        return {"ok": False, "warnings": [],
                "reason": f"price must be a positive finite number, "
                          f"got {price!r}"}
    on = on or date.today().isoformat()
    warnings: List[str] = []
    price_native = price
    price, fx_rate, fx_note = _price_to_base(ctx, price, price_currency, on)
    if price is None:
        return {"ok": False, "warnings": warnings, "reason": fx_note}
    base = ctx.cache / f"{account}_base.json"
    if not base.exists():
        raise FileNotFoundError(f"no {base.name}; run `taxjson run` first")
    txs = load_transactions(base)

    # Cross-account wash context: every OTHER sheltered account. The
    # simulated account must be excluded — it is already the main book,
    # and double-loading it made its own buys act as their own wash
    # triggers (2026-07b audit, web §1).
    sheltered_raw: List[TaxTransaction] = []
    missing_sheltered = []
    for a in ctx.sheltered():
        if a.name == account:
            continue
        f = ctx.cache / f"{a.name}_base.json"
        if f.exists():
            sheltered_raw.extend(load_transactions(f))
        elif _has_inputs(ctx, a.name):
            # A registered account with activity files but no book (a
            # deleted work/ file, a run that stopped before it): its
            # purchases are missing from the wash context, so a loss
            # they deny PERMANENTLY showed as fully allowed with no
            # warning (audit A2-0383; the taxable twins below warn).
            missing_sheltered.append(a.name)
    if missing_sheltered:
        warnings.append(
            f"no work/<account>_base.json for registered account(s) "
            f"{', '.join(missing_sheltered)} although they have inputs — "
            f"their purchases are NOT in this "
            f"{'wash-sale' if ctx.country == 'usa' else 'superficial-loss'}"
            f" check, so a loss they make "
            f"{'a wash sale' if ctx.country == 'usa' else 'superficial'} "
            f"can show as allowed; run `taxjson run`.")

    # Shared preprocessing: TRANSFER handling (incl. stripping sheltered
    # TRANSFERs so they can't act as wash triggers) + the root
    # phantoms.json (the same file `taxjson run` auto-applies via
    # --incomplete-history — without it, positions with pre-window
    # history were falsely rejected). taxable=False so a stray TRANSFER
    # in the main file is rewritten rather than sys.exit()ing the server.
    from taxjson.lib.pipeline import prepare_books
    phantoms_file = ctx.root / "phantoms.json"
    incomplete = phantoms_file if phantoms_file.exists() else None

    def _prepare(main_rows):
        try:
            m, sh, _aff, _log = prepare_books(
                list(main_rows), list(sheltered_raw), [], taxable=False,
                incomplete_history=incomplete, phantom_hint=False)
        except Exception as exc:
            # A corrupt phantoms.json must not 500 the endpoint — but
            # neither may it be silent: the simulation runs on different
            # books than the .sum, and the user must know.
            _w = (f"phantoms.json could not be applied ({exc}) — "
                  f"simulated on raw books; positions with pre-window "
                  f"history may be rejected or mispriced. Fix or "
                  f"regenerate phantoms.json.")
            if _w not in warnings:
                warnings.append(_w)
            m, sh, _aff, _log = prepare_books(
                list(main_rows), list(sheltered_raw), [], taxable=False,
                incomplete_history=None, phantom_hint=False)
        return m, sh

    txs, sheltered = _prepare(txs)

    # The UI links holdings by their RAW per-listing symbol (holdings.toml is
    # built pre-TOBASE), while <account>_base.json is consolidated — a
    # cross-listed AEM.US holding lives as AEM.TO here. Map through
    # ticker.map's GLOBAL+TOBASE renames so those names are simulatable.
    # map_symbol, not an exact dict lookup: the pipeline (merge2) moves
    # an OPTION onto its underlying's rule too (TOBASE AEM.US AEM.TO
    # renames AEM...C...US -> ...TO). The exact lookup left the option
    # on a symbol the book never holds, and the engine simulated
    # WRITING a new short (2026-09 audit).
    map_file = ctx.root / "ticker.map"
    if map_file.exists():
        try:
            from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                        map_symbol,
                                                        merge_renames)
            renames = merge_renames(load_map_file(map_file), to_base=True)
            symbol = map_symbol(symbol, renames)
        except Exception as exc:
            warnings.append(
                f"ticker.map could not be applied ({exc}) — cross-listed "
                f"symbols may not resolve to their consolidated pool.")

    # Contract multiplier: the form asks for the per-share QUOTE (the
    # price brokers show, and what the trade table lists), and the
    # books store an option at qty x price x its contract size. The
    # size is the one the book's rows declare (a mini option's x10, a
    # futures option's CL 1000 / ES 50 — audit S026-22, A2-0131,
    # A2-0384); an equity option with none declared is the standard
    # 100. A futures option with no declared size is refused rather
    # than guessed.
    from taxjson.lib.core import is_option_symbol
    from taxjson.lib.futures import is_plain_future
    from taxjson.lib.pipeline import declared_multipliers
    if is_plain_future(symbol):
        # A plain future's gain is its settled P/L (lib/futures: the
        # notional is never paid, the base book carries the P/L of each
        # close), which a sale price alone cannot reproduce: priced at
        # x1 the what-if showed a +83.71 gain for a -1,418.80 loss
        # (A2-0131) and dated it as an equity T+1 sale (A2-0132).
        return {"ok": False, "warnings": warnings,
                "reason": f"{symbol} is a plain futures contract — its gain "
                          f"is the settled P/L of the contract, which the "
                          f"what-if does not simulate; use "
                          f"`taxjson-explain` on a booked close."}
    _declared = declared_multipliers(txs).get(symbol)
    multiplier = 1
    is_option = is_option_symbol(symbol)
    is_futopt = is_option and symbol.upper().startswith(("F:", "/", "\\"))
    if is_futopt and not _declared:
        return {"ok": False, "warnings": warnings,
                "reason": f"{symbol} is a futures option whose "
                          f"contract multiplier no booked row "
                          f"declares — the what-if cannot price "
                          f"it; use `taxjson-explain` on a booked "
                          f"sale."}
    if is_option:
        multiplier = _declared or 100
        if multiplier == int(multiplier):
            multiplier = int(multiplier)
    proceeds = abs(qty) * price * multiplier
    acct_cfg = ctx.account(account)
    # The simulated trade settles like a real one (era- and holiday-
    # aware, T+1 today): the superficial-loss window and the tax year
    # both run on the settle date for a Canadian project. A Dec-31 sale
    # settles in January — a loss the what-if called "deductible now"
    # lands in the NEXT tax year (R1-197). The calendar is the LISTING's
    # market (AEM.US on the US one, DLR.U.TO on the Canadian one),
    # never the currency the price was typed in: the same sale flipped
    # between superficial and allowed with the price currency (A2-0375,
    # A2-0437, A2-1189).
    from taxjson.lib.dates import listing_market_currency, settlement_date
    mkt_cur = listing_market_currency(
        symbol, (price_currency or ctx.base_currency or "CAD")
        .strip().upper())
    settle_on = settlement_date(on, mkt_cur, is_option)
    if acct_cfg is not None and acct_cfg.crypto:
        # A crypto trade settles when it fills (the parsers' date_settle
        # = date; the radar's same-day bound, R1-240).
        settle_on = on
    if is_futopt:
        # A futures option settles as the project's futures_settle says
        # (the parser's rule: the trade date, or the next business day).
        from taxjson.lib.country import futures_settle_mode
        if futures_settle_mode(ctx.settings or {}) == "next_day":
            from taxjson.lib.market_calendar import add_settlement_days
            settle_on = add_settlement_days(on, 1, mkt_cur).isoformat()
        else:
            settle_on = on
    basis = ctx.tax_date
    tax_year = int((settle_on if basis == "settle" else on)[:4])
    if tax_year != int(on[:4]):
        warnings.append(
            f"a trade on {on} settles {settle_on}: it is a {tax_year} "
            f"disposition (tax year {tax_year}, settlement-date basis), "
            f"not {on[:4]} — a loss here is deductible in {tax_year}.")
    elif ctx.year and tax_year != int(ctx.year):
        warnings.append(
            f"this sale falls in tax year {tax_year}; the project is "
            f"set up for {ctx.year}.")
    # Explicit non-content id: the deterministic content hash COLLIDED
    # with a real same-day sale of identical symbol/qty/price already
    # in the book, so the aggregation below summed the real sale's
    # entries with the simulated one — doubled cost basis and gain
    # reported as ok (REVIEW #26). Real ids are 16-hex content hashes;
    # this can never match one.
    synth = TaxTransaction(
        action="BUYSELL", date=on, date_settle=settle_on, symbol=symbol,
        quantity=(-abs(qty) if side == "sell" else abs(qty)),
        price=price, net_amount=proceeds, proceeds=proceeds,
        currency=ctx.base_currency, account=account,
        multiplier=float(_declared) if _declared else 0.0,
        id=f"whatif-simulated-{symbol}-{on}")

    # Same wash policy as the CLI (GainsRequest.effective_detect_wash +
    # the usa-crypto carve-out): only taxable accounts get wash detection,
    # and US crypto is property — §1091 doesn't reach it. Previously
    # hard-set True, so sheltered simulations wash-checked themselves.
    is_usa = ctx.country == "usa"
    detect_wash = (acct_cfg is not None and acct_cfg.type == "taxable"
                   and not (acct_cfg.crypto and is_usa))
    rules = get_tax_rules(ctx.country)
    from taxjson.lib.core import AmbiguousTransferDateError
    from taxjson.lib.pipeline import option_timing_from_settings

    # The run's option kwargs, grant-year basis included (run_gains
    # passes option_grant_basis = tax_date; the what-if omitted it, so a
    # trade-basis project tested the since year on settle dates —
    # partition SPEC-13).
    _timing_kw = dict(option_timing_from_settings(ctx.settings))
    if _timing_kw:
        _timing_kw["option_grant_basis"] = basis

    # Canada: a trust ROC with a printed record date lowers the ACB on
    # it, as run_gains books it (CA-INC-DATE-ROC-TRUST); the what-if
    # priced a sale between the record and pay dates on the unreduced
    # ACB (A2-1175). IncomeRules answers '' outside Canada.
    from taxjson.lib.income_dating import IncomeRules
    try:
        _income = IncomeRules.from_settings(ctx.settings)
    except Exception as exc:
        _income = None
        warnings.append(
            f"income-dating settings could not be applied ({exc}) — a "
            f"trust return of capital stays on its pay date here.")
    # The engine's option/right-replacement flags (US-WASH-12/14/15,
    # CA-SL-14/15: warn-only, numbers unchanged) for the simulated sale
    # go into the result, not the server's stderr (A2-0687).
    rules.emit_replacement_stderr = False
    replacement_flags: List[str] = []

    def _simulate(main_rows, context_rows, **extra_kw):
        if _income is not None:
            # The same move run_gains makes (pipeline.
            # apply_roc_record_dates, which takes the run's request).
            for _t in main_rows:
                _rec = _income.roc_record_date(_t)
                if _rec:
                    _t.date = _rec
                    _t.date_settle = _rec
        try:
            after = rules.compute_gains(
                main_rows + [synth], sheltered_transactions=context_rows,
                detect_wash_sales=detect_wash,
                **_timing_kw, **extra_kw)
        except AmbiguousTransferDateError as e:
            # Surface as a structured error instead of a 500.
            raise ValueError(str(e))
        from taxjson.lib.core import format_option_replacement_warning
        for _w in after.get("option_replacement_warnings") or []:
            if (_w.get("loss_symbol") == symbol
                    and _w.get("loss_date") in (on, settle_on)):
                _txt = format_option_replacement_warning(
                    _w, country=ctx.country)
                if _txt not in replacement_flags:
                    replacement_flags.append(_txt)
        # The US (FIFO) engine emits ONE gain entry PER CLOSED LOT, all
        # sharing the selling tx's id — reading only the first falsely
        # rejected any sell spanning multiple lots. Aggregate them.
        # The position AFTER the simulated trade rides along, so a
        # refusal can say what the account actually holds.
        _after_qty = sum(float(r.get("qty") or 0.0)
                         for r in after.get("inventory") or []
                         if r.get("symbol") == symbol
                         and (r.get("account") in (None, "", account)))
        return ([t for t in after.get("transactions", [])
                 if t.get("id") == synth.id and t.get("qty")], _after_qty)

    def _closing(rows):
        # A sale closes a LONG position; a buy-to-cover closes a SHORT
        # one. A grant record (writing an option under grant timing) or
        # a short-side entry on a SALE means it OPENED a short, which
        # is not a what-if disposition.
        want = "LONG" if side == "sell" else "SHORT"
        return [t for t in rows if not t.get("grant")
                and (t.get("direction") or "LONG") == want]

    all_rows, after_qty = _simulate(txs, sheltered)
    entries = _closing(all_rows)
    before_qty = after_qty + (abs(qty) if side == "sell" else -abs(qty))
    if not entries:
        if side == "sell" and before_qty < -1e-6:
            reason = (f"{symbol} is not held LONG in {account}: selling "
                      f"would open or add to a SHORT position. To "
                      f"simulate closing a short, enter a negative "
                      f"quantity (buy to cover).")
        elif side == "cover":
            reason = (f"no SHORT position in {symbol} to cover in "
                      f"{account} — a negative quantity buys to cover "
                      f"a short; enter a positive quantity to sell a "
                      f"long position.")
        else:
            reason = ("no disposition produced — the position is "
                      "closed (nothing to sell)")
        return {"ok": False, "warnings": warnings, "reason": reason}
    # Oversell guard: if the engine could only close fewer units than
    # asked, the request exceeds the holding — reject rather than report
    # a misleading partial gain (selling into a new short, or covering
    # into a new long, is not a harvest). Checked on the account's OWN
    # book: the blended pool below may hold more.
    closed = sum(abs(float(t.get("qty", 0) or 0)) for t in entries)
    if closed + 1e-6 < abs(qty):
        return {"ok": False, "warnings": warnings,
                "reason": (f"quantity exceeds holding — only {closed:g} "
                           f"share(s) of {symbol} held in {account}"
                           if side == "sell" else
                           f"quantity exceeds the short position — only "
                           f"{closed:g} unit(s) of {symbol} short in "
                           f"{account}")}

    # Canada: the filing's ACB is ONE s.47 pool across every taxable
    # account (the run's blended pass), and a sibling taxable account's
    # recent buy is a superficial-loss trigger. Re-run the sale on the
    # blended books so cost, gain and the denial match the filing
    # (R1-258). Crypto blends with crypto (two or more exchanges), as
    # the pipeline does. The US keeps per-account FIFO basis.
    basis_label = "per-account, pre-blend"
    if (is_usa and detect_wash and acct_cfg is not None
            and acct_cfg.type == "taxable"):
        # US: basis stays FIFO per account, but §1091 reaches every
        # account — a sibling taxable account's purchase in the window
        # washes the loss (the run's blended pass: --per-account-basis).
        # IRAs are already in the context books. Crypto is out of
        # §1091 (detect_wash is False for a US crypto account).
        # (partition COMMANDS-07)
        group = [a.name for a in ctx.taxable()
                 if not a.crypto and a.name != account]
        if group:
            blend_raw = list(load_transactions(base))
            missing = []
            for name in group:
                f = ctx.cache / f"{name}_base.json"
                if f.exists():
                    blend_raw.extend(load_transactions(f))
                else:
                    missing.append(name)
            btxs, bsh = _prepare(blend_raw)
            bentries = [t for t in _closing(
                _simulate(btxs, bsh, per_account_basis=True)[0])
                if (t.get("account") or account) == account]
            bclosed = sum(abs(float(t.get("qty", 0) or 0))
                          for t in bentries)
            if bentries and bclosed + 1e-6 >= abs(qty):
                entries = bentries
                basis_label = ("FIFO per account; wash sales across "
                               "taxable accounts " + ", ".join(
                                   [account] + group)
                               + (" and the IRAs" if ctx.sheltered()
                                  else ""))
                if missing:
                    warnings.append(
                        f"no work/<account>_base.json for "
                        f"{', '.join(missing)} — their purchases are not "
                        f"in the wash-sale check; run `taxjson run`.")
            else:
                warnings.append(
                    "the cross-account (§1091) simulation produced no "
                    "matching disposition — showing this account's own "
                    "book, without the other accounts' purchases.")
    if (not is_usa and acct_cfg is not None
            and acct_cfg.type == "taxable"):
        group = [a.name for a in ctx.taxable()
                 if bool(a.crypto) == bool(acct_cfg.crypto)]
        if len(group) >= 2:
            blend_raw = list(load_transactions(base))
            missing = []
            for name in group:
                if name == account:
                    continue
                f = ctx.cache / f"{name}_base.json"
                if f.exists():
                    blend_raw.extend(load_transactions(f))
                else:
                    missing.append(name)
            btxs, bsh = _prepare(blend_raw)
            bentries = _closing(_simulate(btxs, bsh)[0])
            bclosed = sum(abs(float(t.get("qty", 0) or 0))
                          for t in bentries)
            if bentries and bclosed + 1e-6 >= abs(qty):
                entries = bentries
                basis_label = ("blended s.47 pool across taxable "
                               "accounts " + ", ".join(group))
                if missing:
                    warnings.append(
                        f"no work/<account>_base.json for "
                        f"{', '.join(missing)} — the blended pool leaves "
                        f"them out; run `taxjson run`.")
            else:
                warnings.append(
                    "the blended (s.47) simulation produced no matching "
                    "disposition — showing this account's own book.")

    for _txt in replacement_flags:
        if _txt not in warnings:
            warnings.append(_txt)

    def _sum(key, alt=None):
        return sum(float(t.get(key, (t.get(alt, 0) if alt else 0)) or 0)
                   for t in entries)

    entry = entries[0]
    # The engine reports both: `raw_gain` is the ECONOMIC gain/loss, `gain` is
    # the ALLOWED (deductible) figure after the superficial-loss disallowance
    # is added back (gain == raw_gain + disallowed_amount for a denied loss).
    disallowed = _sum("disallowed_amount")
    perm = _sum("permanently_disallowed")
    allowed = _sum("gain")
    economic = _sum("raw_gain", alt="gain")
    # A registered (sheltered) account has no capital-gains tax and no
    # deductible loss at all — "Deductible now: -500" on an RRSP sale
    # was a false promise (2026-09 audit). Flag it for the renderers.
    sheltered = acct_cfg is not None and acct_cfg.type != "taxable"
    if sheltered and economic < 0:
        warnings.append(
            f"{account} is a tax-deferred IRA: the loss is not "
            f"deductible, and an IRA sale is never a wash-sale loss."
            if is_usa else
            f"{account} is a registered/sheltered account: the loss is "
            f"not deductible (registered account) and is never a "
            f"superficial-loss event.")
    return {
        "ok": True, "symbol": symbol, "account": account, "date": on,
        "side": side,                     # "sell" | "cover" (buy to cover)
        "settle_date": settle_on,
        "tax_year": tax_year,
        # A Canadian taxable sale runs on the blended s.47 pool when the
        # project has two or more taxable accounts of its kind; the US
        # (per-account FIFO) and single-account projects run on the
        # account's own book. Surfaced so the UI can say which.
        "basis": basis_label,
        "warnings": warnings,
        "sheltered": sheltered,
        "account_type": (acct_cfg.type if acct_cfg is not None
                         else "unknown"),
        "qty": abs(qty),
        "price": round(price_native, 6),           # as entered (native)
        "price_currency": (price_currency or ctx.base_currency),
        "price_base": round(price, 6),             # converted to base
        "fx_rate": round(fx_rate, 6),
        "fx_note": fx_note,
        "multiplier": multiplier,                  # declared, else 100
        "proceeds": round(proceeds, 2),            # base-currency
        "cost_basis": round(_sum("cost"), 2),
        "economic_gain": round(economic, 2),      # true gain/loss on the sale
        "allowed_gain": round(allowed, 2),        # deductible now (post-wash)
        "disallowed_amount": round(disallowed, 2),
        "permanently_disallowed": round(perm, 2),
        "is_loss": economic < 0,
        # ACROSS-LOT aggregation: reading these off entries[0] labeled
        # a mixed LT/ST US sale entirely by its first lot, and showed
        # "superficial loss? no" beside a nonzero disallowed amount
        # (2026-09 audit).
        "is_wash_sale": any(t.get("is_wash_sale") for t in entries),
        # The rule's name in the project's law, for the renderers
        # (never "superficial" in a US project; partition COMMANDS-12).
        "country": ctx.country,
        "rule_name": ("Wash sale (§1091)" if is_usa
                      else "Superficial loss (s.54)"),
        # A "no" covers this project's accounts only (CA-PLAN-04 /
        # US-PLAN-04, audit S054-22).
        "scope_note": _scope_note(ctx.country),
        "term": _term_label(entries),
        "days_held": entry.get("days_held"),
        "currency": ctx.base_currency,
    }


def _has_inputs(ctx: ProjectContext, account: str) -> bool:
    """The account has activity files `taxjson run` reads (so a missing
    book is a gap, not an account that never traded)."""
    from taxjson.lib.checklist import _data_files
    return bool(_data_files(ctx.root / "inputs" / account))


def _scope_note(country):
    from taxjson.lib.wash_scope import scope_note
    return scope_note(country)


def _term_label(entries):
    """One term for the whole simulated sale — or an explicit MIXED
    breakdown when US lots straddle the long-term boundary."""
    terms = {t.get("term") for t in entries if t.get("term")}
    if len(terms) <= 1:
        return next(iter(terms), None)
    by_term = {}
    for t in entries:
        k = t.get("term") or "?"
        by_term[k] = by_term.get(k, 0.0) + float(t.get("gain") or 0.0)
    parts = ", ".join(f"{k} {v:,.2f}"
                      for k, v in sorted(by_term.items()))
    return f"MIXED ({parts})"
