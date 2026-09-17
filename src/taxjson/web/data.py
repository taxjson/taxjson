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
    return doc.get("holding", [])


def holdings_accounts(ctx: ProjectContext) -> List[str]:
    return [a.name for a in ctx.accounts
            if (ctx.reports / f"{a.name}_holdings.toml").exists()]


def find_holding(ctx: ProjectContext, account: str, symbol: str
                 ) -> Optional[Dict[str, Any]]:
    return next((h for h in load_holdings(ctx, account)
                 if h.get("symbol") == symbol), None)


# -------------------------------------------------------------- wash radar
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
    if json_path.exists():
        try:
            doc = _json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = None
        if doc:
            sections: List[Dict[str, Any]] = []
            for sec in doc.get("sections", []):
                def _row(r):
                    ci = _clears_in_display(r.get("clears_at"), today)
                    adv = r.get("advisory", "")
                    if ci == "cleared" and (r.get("category")
                                            == "VIOLATION"):
                        # A VIOLATION's date is the rescue DEADLINE
                        # (last trade date for the full exit), not a
                        # clearing date: once it passes the loss is
                        # denied — "cleared" said the opposite.
                        ci = "deadline passed — loss denied"
                        if adv:
                            adv += (" [rescue deadline has PASSED since "
                                    "this report was generated — unless "
                                    "the position was exited in time, "
                                    "the loss is denied; re-run "
                                    "`taxjson run` to reclassify]")
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
                        "advisory": adv,
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
                    "advisory": parts[4],
                })
    return [s for s in sections if s["rows"]]


# -------------------------------------------------------------- freshness
def freshness(ctx: ProjectContext) -> Optional[Dict[str, Any]]:
    """Are the reports/ artifacts older than the newest input? Returns
    {"generated": iso-date, "stale": bool} or None when there are no
    reports yet (the templates show their own empty-state hints)."""
    from datetime import datetime as _dt
    report_files = [p for p in ctx.reports.glob("*") if p.is_file()]
    if not report_files:
        return None
    reports_mtime = max(p.stat().st_mtime for p in report_files)
    inputs_mtime = 0.0
    inputs_dir = ctx.root / "inputs"
    if inputs_dir.is_dir():
        for p in inputs_dir.rglob("*"):
            if p.is_file():
                inputs_mtime = max(inputs_mtime, p.stat().st_mtime)
    cfg = ctx.root / "taxjson.toml"
    if cfg.exists():
        inputs_mtime = max(inputs_mtime, cfg.stat().st_mtime)
    return {
        "generated": _dt.fromtimestamp(reports_mtime).strftime("%Y-%m-%d %H:%M"),
        "stale": inputs_mtime > reports_mtime,
    }


# ------------------------------------------------------- what-if sell (live)
def _price_to_base(ctx: ProjectContext, price: float,
                   price_currency: Optional[str], on: str):
    """Convert a per-share `price` given in `price_currency` to the base
    currency at the `on` date, using the pipeline's rates file. Returns
    (price_base, fx_rate, note). No-op when the price is already in base."""
    base = ctx.base_currency
    if not price_currency or price_currency.upper() == base.upper():
        return price, 1.0, None
    from datetime import datetime, timedelta
    from decimal import Decimal
    from taxjson.bin.taxjson_convert_currency import (
        load_exchange_rates, get_rate_for_date)
    rates_file = ctx.cache / "to_base.csv"
    history = (load_exchange_rates(rates_file, target_curr=base)
               if rates_file.exists() else {})
    rate = float(get_rate_for_date(price_currency, on, history, Decimal("1.35")))

    def _has_recent_rate() -> bool:
        # get_rate_for_date falls back to the default when no rate exists
        # within ~5 days of `on` — EVEN when the currency is in the history
        # (rates only extend to the last `taxjson run`). Detect that case so
        # the UI's FX warning fires instead of silently using 1.35 for a
        # what-if run weeks later.
        dates = history.get(price_currency) or {}
        try:
            target = datetime.strptime(on, "%Y-%m-%d")
        except ValueError:
            return False
        for back in range(0, 6):
            d = (target - timedelta(days=back)).strftime("%Y-%m-%d")
            if d in dates:
                return True
        return False

    note = (None if _has_recent_rate() else
            f"no {price_currency}->{base} rate near {on} (rates end at the "
            f"last `taxjson run`); used fallback {rate} — re-run `taxjson "
            f"run` for a current rate")
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
    # Reject before anything touches the engine: qty/price of nan/inf
    # bubbled a non-JSON-compliant float into the response and 500'd
    # the route (REVIEW #30); a negative price produced internally
    # contradictory ok-styled numbers (proceeds -150, gain +50 —
    # REVIEW #31).
    if not (math.isfinite(qty) and abs(qty) > 0):
        return {"ok": False, "warnings": [],
                "reason": f"qty must be a nonzero finite number, "
                          f"got {qty!r}"}
    if not (math.isfinite(price) and price > 0):
        return {"ok": False, "warnings": [],
                "reason": f"price must be a positive finite number, "
                          f"got {price!r}"}
    on = on or date.today().isoformat()
    warnings: List[str] = []
    price_native = price
    price, fx_rate, fx_note = _price_to_base(ctx, price, price_currency, on)
    base = ctx.cache / f"{account}_base.json"
    if not base.exists():
        raise FileNotFoundError(f"no {base.name}; run `taxjson run` first")
    txs = load_transactions(base)

    # Cross-account wash context: every OTHER sheltered account. The
    # simulated account must be excluded — it is already the main book,
    # and double-loading it made its own buys act as their own wash
    # triggers (2026-07b audit, web §1).
    sheltered: List[TaxTransaction] = []
    for a in ctx.sheltered():
        if a.name == account:
            continue
        f = ctx.cache / f"{a.name}_base.json"
        if f.exists():
            sheltered.extend(load_transactions(f))

    # Shared preprocessing: TRANSFER handling (incl. stripping sheltered
    # TRANSFERs so they can't act as wash triggers) + the root
    # phantoms.json (the same file `taxjson run` auto-applies via
    # --incomplete-history — without it, positions with pre-window
    # history were falsely rejected). taxable=False so a stray TRANSFER
    # in the main file is rewritten rather than sys.exit()ing the server.
    from taxjson.lib.pipeline import prepare_books
    phantoms_file = ctx.root / "phantoms.json"
    incomplete = phantoms_file if phantoms_file.exists() else None
    try:
        txs, sheltered, _aff, _log = prepare_books(
            txs, sheltered, [], taxable=False,
            incomplete_history=incomplete, phantom_hint=False)
    except Exception as exc:
        # A corrupt phantoms.json must not 500 the endpoint — but neither
        # may it be silent: the simulation runs on different books than
        # the .sum, and the user must know.
        warnings.append(
            f"phantoms.json could not be applied ({exc}) — simulated on "
            f"raw books; positions with pre-window history may be "
            f"rejected or mispriced. Fix or regenerate phantoms.json.")
        txs, sheltered, _aff, _log = prepare_books(
            txs, sheltered, [], taxable=False,
            incomplete_history=None, phantom_hint=False)

    # The UI links holdings by their RAW per-listing symbol (holdings.toml is
    # built pre-TOBASE), while <account>_base.json is consolidated — a
    # cross-listed AEM.US holding lives as AEM.TO here. Map through
    # ticker.map's GLOBAL+TOBASE renames so those names are simulatable.
    map_file = ctx.root / "ticker.map"
    if map_file.exists():
        try:
            from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                        merge_renames)
            renames = merge_renames(load_map_file(map_file), to_base=True)
            symbol = renames.get(symbol, symbol)
        except Exception as exc:
            warnings.append(
                f"ticker.map could not be applied ({exc}) — cross-listed "
                f"symbols may not resolve to their consolidated pool.")

    proceeds = abs(qty) * price
    # Explicit non-content id: the deterministic content hash COLLIDED
    # with a real same-day sale of identical symbol/qty/price already
    # in the book, so the aggregation below summed the real sale's
    # entries with the simulated one — doubled cost basis and gain
    # reported as ok (REVIEW #26). Real ids are 16-hex content hashes;
    # this can never match one.
    synth = TaxTransaction(
        action="BUYSELL", date=on, symbol=symbol, quantity=-abs(qty),
        price=price, net_amount=proceeds, proceeds=proceeds,
        currency=ctx.base_currency, account=account,
        id=f"whatif-simulated-{symbol}-{on}")

    # Same wash policy as the CLI (GainsRequest.effective_detect_wash +
    # the usa-crypto carve-out): only taxable accounts get wash detection,
    # and US crypto is property — §1091 doesn't reach it. Previously
    # hard-set True, so sheltered simulations wash-checked themselves.
    acct_cfg = ctx.account(account)
    is_usa = ctx.country.strip().lower() in ("us", "usa")
    detect_wash = (acct_cfg is not None and acct_cfg.type == "taxable"
                   and not (acct_cfg.crypto and is_usa))
    rules = get_tax_rules(ctx.country)
    from taxjson.lib.core import AmbiguousTransferDateError
    try:
        after = rules.compute_gains(
            txs + [synth], sheltered_transactions=sheltered,
            detect_wash_sales=detect_wash)
    except AmbiguousTransferDateError as e:
        # Surface as a structured error instead of a 500.
        raise ValueError(str(e))
    # The US (FIFO) engine emits ONE gain entry PER CLOSED LOT, all sharing
    # the selling tx's id — reading only the first falsely rejected any sell
    # spanning multiple lots ("only <first lot> held"). Aggregate them.
    entries = [t for t in after.get("transactions", [])
               if t.get("id") == synth.id and t.get("qty")]
    if not entries:
        return {"ok": False, "warnings": warnings,
                "reason": "no disposition produced — the position "
                          "is closed (nothing to sell)"}
    # Oversell guard: if the engine could only close fewer shares than asked,
    # the request exceeds the holding — reject rather than report a misleading
    # partial gain (selling into a new short is not a tax-loss-harvest).
    closed = sum(abs(float(t.get("qty", 0) or 0)) for t in entries)
    if closed + 1e-6 < abs(qty):
        return {"ok": False, "warnings": warnings,
                "reason": f"quantity exceeds holding — only {closed:g} "
                          f"share(s) of {symbol} held in {account}"}

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
            f"{account} is a registered/sheltered account: the loss is "
            f"not deductible (registered account) and is never a "
            f"superficial-loss event.")
    return {
        "ok": True, "symbol": symbol, "account": account, "date": on,
        # This simulation runs on ONE account's book (plus sheltered
        # context). Canada's s.47 ACB actually blends across taxable
        # accounts, and a wash trigger in a sibling taxable account is
        # invisible here — so the filed delta after a real sale can
        # differ. Surfaced so the UI can say so.
        "basis": "per-account, pre-blend",
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
        "term": _term_label(entries),
        "days_held": entry.get("days_held"),
        "currency": ctx.base_currency,
    }


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
