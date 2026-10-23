"""`taxjson rebuy`: every ticker sold at a loss whose repurchase window is
still open, and the first day it can be bought back without the loss
being denied (tax-logic CA-PLAN-05 / US-PLAN-06).

Nothing here decides a window: the rows come from the wash radar's
--json document (bin/taxjson_wash_radar: each row's `losses` and
`rebuy_at`, its `reentry_date`), which applies the engine's own rule
per country — Canada's s.54 window on settlement dates with the
still-held test, the US engine's §1091 verdict on trade dates. This
module only groups the radar's losses per ticker (options under their
underlying, each series with its own date), names the other listings
of the same security (ticker.map / tobase.map, as buy-check matches
them) and renders the view.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from taxjson.lib.country import USA, canonical_country

# The default view's tail: tickers whose re-entry date passed in the
# last CLEARED_DAYS days (one line; --details lists them).
CLEARED_DAYS = 7

SCHEMA_VERSION = 1


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def _class_rows(radar: Dict[str, Dict[str, Any]]):
    """[(tickers, row)] one per radar class: rows whose `losses` lists
    are the same records (a rename class, a cross-listing class) are one
    class, shown once."""
    seen: Dict[str, int] = {}
    out: List[list] = []
    for t in sorted(radar):
        r = radar[t]
        ls = r.get("losses") or []
        if not ls:
            continue
        key = json.dumps(ls, sort_keys=True)
        if key in seen:
            out[seen[key]][0].append(t)
            continue
        seen[key] = len(out)
        out.append([[t], r])
    return out


def build(radar: Dict[str, Dict[str, Any]], as_of: str, country: str, *,
          canon: Optional[Callable[[str], str]] = None,
          underlying: Optional[Callable[[str], str]] = None,
          want: Optional[Iterable[str]] = None,
          cleared_days: int = CLEARED_DAYS) -> Dict[str, Any]:
    """The rebuy document from a flattened radar ({ticker: row}, as
    taxjson_watch.flatten_radar makes it, run with --all and
    --cleared-days): `rows` (open windows, one per ticker sold at a
    loss), `cleared` (re-entry dates passed in the last `cleared_days`
    days). `canon` folds a symbol to its class root (options to their
    underlying), `want` is the set of class roots asked for (None: all),
    `underlying` the listing an option groups under."""
    usa = canonical_country(country) == USA
    canon = canon or (lambda s: s)
    underlying = underlying or (lambda s: s)
    members = getattr(canon, "members", {}) or {}
    # {class root: the class roots ticker.map SIMILAR lines put beside
    # it} (lib/similar; bin/taxjson_run._wash_class_context).
    similar = getattr(canon, "similar", {}) or {}
    want_set = set(want) if want is not None else None
    rows: List[Dict[str, Any]] = []
    cleared: Dict[str, Dict[str, Any]] = {}
    open_syms = set()
    for tickers, r in _class_rows(radar):
        cat = r.get("category") or ""
        by_sym: Dict[str, List[dict]] = {}
        for l in r.get("losses") or []:
            sym = str(l.get("symbol") or tickers[0])
            if want_set is not None and canon(sym) not in want_set:
                continue
            by_sym.setdefault(sym, []).append(l)
        for sym, ls in sorted(by_sym.items()):
            live = [l for l in ls if not l.get("cleared")]
            done = [l for l in ls if l.get("cleared")]
            if live:
                open_syms.add(sym)
                rows.append(_row(sym, live, r, cat, as_of, usa, canon,
                                 underlying, members, similar))
            for l in done:
                ra = str(l.get("rebuy_at") or "")
                if not ra or not (0 <= _days(as_of, ra) < cleared_days):
                    continue
                c = cleared.setdefault(sym, {
                    "ticker": sym, "underlying": underlying(sym),
                    "rebuy_at": ra, "sold_on": l.get("trade_date"),
                    "loss": 0.0, "accounts": set()})
                if ra >= c["rebuy_at"]:
                    c["rebuy_at"] = ra
                    c["sold_on"] = l.get("trade_date")
                c["loss"] += float(l.get("loss") or 0.0)
                if l.get("account"):
                    c["accounts"].add(l["account"])
    rows.sort(key=lambda x: (x["underlying"], x["is_option"], x["ticker"]))
    clr = [dict(c, accounts=sorted(c["accounts"]), loss=round(c["loss"], 2))
           for s, c in sorted(cleared.items()) if s not in open_syms]
    clr.sort(key=lambda c: (c["rebuy_at"], c["ticker"]), reverse=True)
    return {"schema_version": SCHEMA_VERSION,
            "country": "usa" if usa else "canada",
            "as_of_date": as_of, "cleared_days": cleared_days,
            "rows": rows, "cleared": clr}


def _row(sym, live, r, cat, as_of, usa, canon, underlying, members,
         similar=None):
    from taxjson.lib.core import is_option_symbol
    last = max(live, key=lambda l: (l.get("trade_date") or "",
                                    l.get("window_date") or ""))
    rebuy_at = r.get("rebuy_at")
    status = "wait"
    rescue_by = None
    if usa:
        if not rebuy_at:
            if all(l.get("direction") == "SHORT" for l in live):
                status = "short-cover"
            elif all(float(l.get("loss") or 0.0)
                     - float(l.get("disallowed") or 0.0) <= 0.01
                     for l in live):
                status = "washed"
            else:
                status = "no-window"
        elif cat == "WASHED":
            status = "washed-part"
    elif cat == "VIOLATION":
        if r.get("deadline_passed"):
            status = "denied"
        else:
            status = "rebought"
            rescue_by = r.get("clears_at")
    opt = is_option_symbol(sym.strip().upper())
    others = [] if opt else [m for m in members.get(canon(sym), [])
                             if m != sym]
    alike = [] if opt else sorted({
        m for root in (similar or {}).get(canon(sym), [])
        for m in (members.get(root) or [root])})
    return {
        "ticker": sym,
        "underlying": underlying(sym),
        "is_option": opt,
        "sold_on": last.get("trade_date"),
        "loss": round(sum(float(l.get("loss") or 0.0) for l in live), 2),
        "qty": round(sum(float(l.get("qty") or 0.0) for l in live), 6),
        "accounts": sorted({l.get("account") for l in live
                            if l.get("account")}),
        "rebuy_at": rebuy_at,
        "days_left": (_days(rebuy_at, as_of) if rebuy_at else None),
        "status": status,
        "rescue_by": rescue_by,
        "also": others,
        # Look-alike funds (ticker.map SIMILAR): a buy of these is
        # flagged, not counted (CA-SL-20 / US-WASH-26).
        "similar": alike,
        "losses": live,
    }


# ------------------------------------------------------------- wording
def any_account_line(country: str) -> str:
    """The one line every view carries: where a buy counts."""
    if canonical_country(country) == USA:
        return ("A buy in ANY of your accounts counts, IRAs included; "
                "through an IRA the loss is lost for good.")
    return ("A buy in ANY of your accounts counts, RRSP and TFSA "
            "included; there the loss is denied for good.")


def _when(r: Dict[str, Any]) -> str:
    if r["rebuy_at"]:
        d = r["days_left"]
        return f"{r['rebuy_at']} ({d}d)" if d and d > 0 else r["rebuy_at"]
    return {"short-cover": "now (buy)", "washed": "now (washed)"}.get(
        r["status"], "now")


def _money(v: float) -> str:
    from taxjson.lib.report_model import fmt_money
    return fmt_money(v)


def _few(names: List[str], n: int) -> str:
    return ", ".join(names[:n]) + (f" +{len(names) - n} more"
                                   if len(names) > n else "")


def _acts(doc: Dict[str, Any], country: str) -> List[str]:
    from taxjson.lib import out
    usa = canonical_country(country) == USA
    acts = []
    reb = [r for r in doc["rows"] if r["status"] == "rebought"]
    if reb:
        if len(reb) == 1:
            r = reb[0]
            line = (f"{r['ticker']}: already bought back; sell it by "
                    f"{r['rescue_by']} to keep the loss")
        else:
            line = (f"{len(reb)} already bought back "
                    f"({_few([r['ticker'] for r in reb], 2)}): sell to "
                    f"keep the loss")
        acts.append(out.act(line, "tjs wash-radar"))
    den = [r for r in doc["rows"] if r["status"] == "denied"]
    if den:
        acts.append(out.act(f"{len(den)} loss(es) already denied "
                            f"({_few([r['ticker'] for r in den], 2)})",
                            "tjs wash-radar --details"))
    if usa:
        sc = [r for r in doc["rows"] if r["status"] == "short-cover"]
        if sc:
            acts.append(out.act(
                f"{_few([r['ticker'] for r in sc], 2)}: a short-cover "
                f"loss; only a new short before 31 days replaces it",
                "tjs buy-check"))
    return acts


def _title(doc: Dict[str, Any], currency: str) -> str:
    usa = doc["country"] == "usa"
    return ("REBUY — "
            + ("wash sales (§1091, trade dates)" if usa
               else "superficial losses (s.54, settlement dates)")
            + f", as of {doc['as_of_date']} ({currency})")


def _table(doc: Dict[str, Any], w: int) -> List[str]:
    from taxjson.lib import out
    body = []
    for r in doc["rows"]:
        tick = ("  " + r["ticker"]) if r["is_option"] and any(
            x["ticker"] == r["underlying"] for x in doc["rows"]) \
            else r["ticker"]
        body.append([tick, r["sold_on"] or "", _money(r["loss"]),
                     ",".join(r["accounts"]), _when(r)])
    return out.fit_table(["TICKER", "SOLD ON", "LOSS", "ACCOUNTS",
                          "REBUY ON"], body,
                         aligns=["<", "<", ">", "<", "<"], drop=(3, 1),
                         width_=w)


def render(doc: Dict[str, Any], currency: str, details: bool = False):
    """The text view (lib/out.Doc): the essentials, or with `details`
    every sale behind each row, the other listings that count, the
    cleared tail in full and the scope."""
    from taxjson.lib import out
    from taxjson.lib.wash_scope import scope_more
    country = doc["country"]
    d = out.Doc(_title(doc, currency))
    if doc["rows"]:
        d.para("REBUY ON: the first day a buy no longer cancels the "
               "loss; option series under their underlying.")
        d.line("\n".join(_table(doc, d.w)))
    else:
        d.para("No ticker sold at a loss has an open repurchase window.")
    tail = doc["cleared"]
    if details:
        _details(d, doc, currency)
        return d
    acts = _acts(doc, country)
    d.blank()
    for a in acts:
        d.item(a[len(out.ACT):], bullet=out.ACT)
    if doc["rows"]:
        d.para(any_account_line(country))
    if tail:
        # One line, never wrapped (docs/output-style.md): as many names
        # as fit, the rest counted; the dates are in --details.
        names = [c["ticker"] for c in tail]
        for n in (3, 2, 1, 0):
            line = (f"Cleared in the last {doc['cleared_days']} days: "
                    + (_few(names, n) if n else f"{len(names)} ticker(s)")
                    + ".")
            if len(line) <= 100:
                break
        d.line(line)
    d.para(scope_more("tjs rebuy --details", country))
    return d


def _details(d, doc: Dict[str, Any], currency: str) -> None:
    from taxjson.lib import out
    from taxjson.lib.wash_scope import scope_lines
    country = doc["country"]
    usa = country == "usa"
    d.blank()
    d.para(any_account_line(country))
    acts = _acts(doc, country)
    for a in acts:
        d.item(a[len(out.ACT):], bullet=out.ACT)
    if doc["rows"]:
        d.section("SALES BEHIND EACH ROW")
        for r in doc["rows"]:
            d.item(_row_detail(r, usa), indent="  ")
    d.section(f"CLEARED IN THE LAST {doc['cleared_days']} DAYS")
    if doc["cleared"]:
        d.table(["TICKER", "SOLD ON", "LOSS", "ACCOUNTS", "CLEARED ON"],
                [[c["ticker"], c["sold_on"] or "", _money(c["loss"]),
                  ",".join(c["accounts"]), c["rebuy_at"]]
                 for c in doc["cleared"]], indent="  ",
                aligns=["<", "<", ">", "<", "<"])
    else:
        d.line("  (none)")
    d.section("HOW THE DATE IS SET")
    if usa:
        d.items([
            "A loss is a wash sale when the same security is bought "
            "within 30 days before or after the sale (trade dates, "
            "§1091) in any account, IRAs included; no still-held test.",
            "REBUY ON is the 31st day after the latest loss sale a buy "
            "can still disallow; a loss already disallowed in full is "
            "not one.",
            "After a loss on covering a short only a new short sale "
            "replaces it (§1091(e)); a long call is a note only "
            "(US-PLAN-02); futures and their options are outside §1091 "
            "(US-WASH-18)."], "  ")
    else:
        d.items([
            "A loss is superficial when the same property is acquired "
            "within 30 days before or after the sale (settlement dates, "
            "s.54) by you or an affiliated person and still held 30 "
            "days after it.",
            "REBUY ON is the day after the latest loss sale's window "
            "closes, as a trade date: a buy that day settles after the "
            "window (wash-radar's CLEARS, buy-check's safe-from date).",
            "A long call on the shares is a replacement too "
            "(CA-PLAN-02); an option series is replaced only by the "
            "same series."], "  ")
    d.blank()
    d.line("\n".join(scope_lines(country, d.w)))


def _row_detail(r: Dict[str, Any], usa: bool) -> str:
    sales = "; ".join(
        f"{l.get('trade_date')} {l.get('account') or '?'} "
        f"{_qty(l.get('qty'))} units, loss {_money(float(l.get('loss') or 0))}"
        + (f", {_money(float(l['disallowed']))} disallowed"
           if usa and float(l.get("disallowed") or 0.0) > 0.005 else "")
        + (f", {_qty(l['denied_qty'])} units denied as things stand"
           if not usa and float(l.get("denied_qty") or 0.0) > 1e-9
           else "")
        for l in r["losses"])
    text = f"{r['ticker']}: {sales}. Rebuy on {_when(r)}."
    if r["status"] == "rebought":
        text += (f" Already bought back: sell the replacement by "
                 f"{r['rescue_by']} (last trade date) to keep the loss.")
    elif r["status"] == "denied":
        text += " The rescue deadline has passed: the loss is denied."
    elif r["status"] == "washed":
        text += " Already disallowed in full; a buy now changes nothing."
    elif r["status"] == "washed-part":
        text += " Part of it is already disallowed."
    elif r["status"] == "short-cover":
        text += " A buy does not replace it; a new short before 31 days does."
    if r["also"]:
        text += (" A buy of " + ", ".join(r["also"])
                 + " counts too (the same security).")
    if r.get("similar"):
        text += (" A buy of " + ", ".join(r["similar"])
                 + " is flagged, not counted (ticker.map SIMILAR: two "
                 "securities unless you treat them as "
                 + ("substantially identical" if usa
                    else "identical property") + ").")
    return text


def _qty(v) -> str:
    s = f"{float(v or 0.0):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def as_of_default() -> str:
    return date.today().isoformat()


__all__ = ["build", "render", "any_account_line", "CLEARED_DAYS",
           "as_of_default"]
