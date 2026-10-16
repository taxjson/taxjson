"""`taxjson div-estimate`: upcoming dividends projected from each held
position's own dividend history in the books. Planning numbers only,
never a filing figure (tax-logic CA-PLAN-06 / US-PLAN-07).

Per payer (a listing, across the accounts that hold it):
- the payment frequency from its past pay dates: the grid (weekly,
  monthly, quarterly, semi-annual or annual), anchored at the latest
  payment, that most of the last payments fall on; a payment off the
  grid (a special) or a stretch of empty slots (payments made while
  the position was not held) does not break it. No grid fits, or a
  single payment: irregular (its estimate is pro rata from the
  trailing-12-month amount per share);
- its latest per-share amount (gross, in the payment's currency) and
  its trailing-12-month amount per share.

Per position (account, listing) held now: the shares, the next pay
dates on the payer's schedule and their amounts (latest per-share x
shares held), converted to the base currency at the books' latest rate
(work/to_base.csv; never a network call). Each projected payment is
dated for the tax year by the same rule as divs-sum
(lib/income_dating.IncomeRules.income_date on a copy of the payer's
latest row moved to the projected date: a Canadian trust's
distribution by its record date, a listed US RIC's January dividend in
the year before). Withholding uses the rate the latest payment in that
account shows (its TAX rows), where it has one.

This module is pure: the command (taxjson_run.cmd_div_estimate) reads
the books and hands the rows in.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

# (name, months between payments (0: by days), days between payments)
FREQUENCIES: Tuple[Tuple[str, int, int], ...] = (
    ("weekly", 0, 7),
    ("monthly", 1, 30),
    ("quarterly", 3, 91),
    ("semi-annual", 6, 182),
    ("annual", 12, 365),
)
# A projected payment this many days overdue (by its frequency) is taken
# as missing from the books, not as still to come.
GRACE_DAYS = {"weekly": 3, "monthly": 7, "quarterly": 15, "semi-annual": 20,
              "annual": 30}
# The frequency is read from the last LOOKBACK payments.
LOOKBACK = 12
# A payment is on a frequency's grid when it is within GAP_TOL of a
# period from its slot (a monthly payer's date drifts a few days with
# weekends and the broker's posting; a quarterly one's a few weeks).
GAP_TOL = 0.25
_INCOME = ("DIVIDEND", "DIVIDEND_IN_LIEU")


@dataclass
class Payment:
    pay_date: str
    per_share: float
    gross: float
    qty: float
    currency: str
    account: str
    tax: float
    row: Dict[str, Any] = field(repr=False, default_factory=dict)


def _iso(d: date) -> str:
    return d.isoformat()


def add_months(iso: str, n: int) -> str:
    d = date.fromisoformat(iso)
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return _iso(date(y, m, min(d.day, calendar.monthrange(y, m)[1])))


def parse_window(token: str) -> Optional[int]:
    """Months in a forward window token (6m, 1y, 13w, 90d), or None."""
    m = re.fullmatch(r"\s*(\d+)\s*([dwmy])\s*", str(token or "").lower())
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    months = {"d": n / 30.4375, "w": n * 7 / 30.4375, "m": n,
              "y": n * 12}[u]
    return max(1, int(round(months)))


class Payments(dict):
    """{symbol: [Payment]} that remembers the rows it was read from, so
    `estimate` can read them again as of its own date (a reversal or a
    share count dated after the as-of date must not change what the
    books said on it: 2026-10 audit R09)."""
    rows: List[Tuple[str, Dict[str, Any]]] = []


def payments(rows: Iterable[Tuple[str, Dict[str, Any]]],
             as_of: Optional[str] = None) -> "Payments":
    """{symbol: [Payment] by pay date} from (account, native row) pairs
    dated up to `as_of` (all of them when None):
    DIVIDEND (and DIVIDEND_IN_LIEU: the same stream, paid while the
    shares were lent) rows netted per account, symbol and pay date (a
    reversal and its re-posting are one payment), their withholding
    from the TAX rows of that account, symbol and date. A reversal
    posted on a later date nets against the latest earlier posting of
    that account and symbol (the original it cancels), so a correction
    counts once. A corporate action's deemed dividend (a spin-off
    election) is not part of a payer's schedule; a row with no share
    count (the broker's description names none) takes the count of the
    account's nearest dated payment of that symbol that has one, or is
    left out when none has."""
    rows = list(rows)
    inc: Dict[tuple, Dict[str, Any]] = {}
    tax: Dict[tuple, float] = {}
    for acct, t in rows:
        if not isinstance(t, dict):
            continue
        act = str(t.get("action") or "").upper()
        sym = str(t.get("symbol") or "").strip()
        d = str(t.get("date") or "")[:10]
        if not sym or len(d) != 10:
            continue
        if as_of is not None and d > as_of:
            continue
        k = (acct, sym, d)
        if act == "TAX" or str(t.get("type") or "").lower() == "tax":
            tax[k] = tax.get(k, 0.0) + float(t.get("net_amount") or 0.0)
            continue
        if act not in _INCOME:
            continue
        if t.get("corp_election") or t.get("corp_event_id"):
            continue
        amt = (float(t.get("gross_amount") or 0.0)
               or float(t.get("net_amount") or 0.0))
        e = inc.setdefault(k, {"gross": 0.0, "qty": 0.0, "row": t,
                               "currency": str(t.get("currency") or "")})
        e["gross"] += amt
        q = abs(float(t.get("quantity") or 0.0))
        if q > e["qty"]:
            e["qty"], e["row"] = q, t
    # Per account and symbol, in date order: a net-negative date (a
    # reversal) cancels the latest earlier positive posting(s); a date
    # with no share count borrows the nearest dated one.
    by_pair: Dict[tuple, List[tuple]] = {}
    for (acct, sym, d), e in inc.items():
        by_pair.setdefault((acct, sym), []).append((d, e))
    for ents in by_pair.values():
        ents.sort(key=lambda x: x[0])
        for i, (_d, e) in enumerate(ents):
            if e["gross"] >= -0.005:
                continue
            left = -e["gross"]
            e["gross"] = 0.0
            for _pd, pe in reversed(ents[:i]):
                if pe["gross"] <= 0.005:
                    continue
                take = min(left, pe["gross"])
                pe["gross"] -= take
                left -= take
                if left <= 0.005:
                    break
        for i, (d, e) in enumerate(ents):
            if e["qty"] > 1e-9 or e["gross"] <= 0.005:
                continue
            near = [(abs((date.fromisoformat(d)
                          - date.fromisoformat(pd)).days), pe["qty"])
                    for pd, pe in ents if pe["qty"] > 1e-9]
            if near:
                e["qty"] = min(near)[1]
    out = Payments()
    out.rows = rows
    for (acct, sym, d), e in inc.items():
        if e["gross"] <= 0.005 or e["qty"] <= 1e-9:
            continue
        out.setdefault(sym, []).append(Payment(
            pay_date=d, per_share=e["gross"] / e["qty"], gross=e["gross"],
            qty=e["qty"], currency=e["currency"].upper(), account=acct,
            tax=max(0.0, tax.get((acct, sym, d), 0.0)), row=e["row"]))
    for ps in out.values():
        ps.sort(key=lambda p: (p.pay_date, -p.qty))
    return out


# Postings of one payment a few days apart (two brokers' pay dates, a
# special dividend paid beside the regular one) are one payment.
CLUSTER_DAYS = 5
# A gap matches k payments of a period P when it is within
# GAP_TOL * P (+ GAP_TOL_K * k * P for a long gap) of k * P.
GAP_TOL, GAP_TOL_K = 0.25, 0.05


def schedule(pays: List[Payment]) -> List[Payment]:
    """One payment per pay date across accounts: postings within
    CLUSTER_DAYS of each other are one payment, its per-share amount
    the largest holding's (an account's two rows of it, a special and a
    regular dividend, added)."""
    out: List[Payment] = []
    group: List[Payment] = []

    def _flush():
        if not group:
            return
        by: Dict[str, List[Payment]] = {}
        for p in group:
            by.setdefault(p.account, []).append(p)
        best = None
        for acct, ps in by.items():
            qty = max(p.qty for p in ps)
            per = sum(p.gross for p in ps) / qty if qty else 0.0
            last = max(ps, key=lambda p: (p.pay_date, p.qty))
            cand = Payment(pay_date=last.pay_date, per_share=per,
                           gross=sum(p.gross for p in ps), qty=qty,
                           currency=last.currency, account=acct,
                           tax=sum(p.tax for p in ps), row=last.row)
            if best is None or cand.qty > best.qty:
                best = cand
        out.append(best)
        group.clear()
    for p in sorted(pays, key=lambda p: p.pay_date):
        if group and (date.fromisoformat(p.pay_date)
                      - date.fromisoformat(group[-1].pay_date)).days \
                > CLUSTER_DAYS:
            _flush()
        group.append(p)
    _flush()
    return out


def _slot(anchor: date, months: int, days: int, k: int) -> date:
    """The grid date k periods before `anchor`."""
    if months:
        return date.fromisoformat(add_months(anchor.isoformat(), -months * k))
    return anchor - timedelta(days=days * k)


def detect_frequency(dates: List[str]) -> Tuple[Optional[str],
                                                Optional[int],
                                                Optional[float]]:
    """(name, months between payments (0: weekly), typical gap in days)
    from sorted pay dates; (None, None, gap) when irregular, (None,
    None, None) with fewer than two payments. Each frequency's grid is
    anchored at the latest of the last LOOKBACK payments: a payment is
    on the grid when it is within GAP_TOL of a period from its slot. A
    frequency fits when two thirds of the payments are on its grid, no
    slot holds more than two (a payment and a special, or a second
    broker's posting of it) and two payments fill adjacent slots (a
    stretch of empty slots is the position not held; a payment off the
    grid is a special); the one with the most adjacent slots filled
    wins, then the most slots, then the shortest period (a quarterly
    payer's dates are on the monthly grid too, but fill no adjacent
    monthly slots; they are on the weekly grid as well, 13 weeks to a
    quarter, so weekly needs two thirds of its slot gaps adjacent)."""
    ds = [date.fromisoformat(d) for d in dates[-LOOKBACK:]]
    if len(ds) < 2:
        return None, None, None
    anchor = ds[-1]
    best = None
    for name, months, days in FREQUENCIES:
        period = months * 365.25 / 12 if months else float(days)
        tol = period * GAP_TOL
        slots: Dict[int, List[date]] = {}
        for d in ds:
            k = max(0, int(round((anchor - d).days / period)))
            if abs((d - _slot(anchor, months, days, k)).days) <= tol:
                slots.setdefault(k, []).append(d)
        # A slot holds a payment and at most one more (a special, a
        # second broker's posting): three is a shorter period.
        if any(len(v) > 2 for v in slots.values()):
            continue
        on = sum(len(v) for v in slots.values())
        ks = sorted(slots)
        adj = [(slots[a][-1] - slots[b][-1]).days
               for a, b in zip(ks, ks[1:]) if b == a + 1]
        if not adj or on * 3 < 2 * len(ds):
            continue
        # Every grid is on the weekly one (13 weeks to a quarter): the
        # weekly payer is the one whose slots are mostly adjacent.
        if not months and len(adj) * 3 < 2 * (len(ks) - 1):
            continue
        key = (len(adj), len(slots), -period)
        if best is None or key > best[0]:
            best = (key, name, months, float(median(adj)))
    if best is None:
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        return None, None, float(median(gaps))
    return best[1], best[2], best[3]


def ttm_per_share(sched: List[Payment], as_of: str) -> float:
    lo = _iso(date.fromisoformat(as_of) - timedelta(days=365))
    return sum(p.per_share for p in sched if lo < p.pay_date <= as_of)


def _projected_tax_date(latest: Payment, pay: str,
                        income_date: Optional[Callable]) -> str:
    """The tax date of a payment projected to `pay`: the latest real
    row moved there (its record date moved by the same lead) and dated
    by the project's income rule."""
    if income_date is None:
        return pay
    row = dict(latest.row)
    rec = str(row.get("record_date") or "")[:10]
    if rec:
        try:
            lead = (date.fromisoformat(latest.pay_date)
                    - date.fromisoformat(rec)).days
            row["record_date"] = _iso(date.fromisoformat(pay)
                                      - timedelta(days=lead))
        except ValueError:
            row.pop("record_date", None)
    row["date"] = pay
    row["date_settle"] = pay
    try:
        return str(income_date(row) or pay)
    except Exception:                                      # noqa: BLE001
        return pay


def estimate(positions: Iterable[Dict[str, Any]],
             pays: Dict[str, List[Payment]], *, as_of: str, months: int,
             tax_year: int, base: str,
             fx: Callable[[str], Optional[float]],
             income_date: Optional[Callable] = None) -> Dict[str, Any]:
    """The estimate document. `positions`: {account, group ("taxable" |
    "sheltered"), symbol, qty} held now (long shares only); `fx(cur)`:
    the base-currency rate of `cur` (None: no rate in the books)."""
    horizon = add_months(as_of, months)
    ystart, yend = f"{tax_year}-01-01", f"{tax_year}-12-31"
    if isinstance(pays, Payments) and pays.rows:
        pays = payments(pays.rows, as_of=as_of)
    # The projection runs to the later of the window and the tax year
    # (plus the month a December-record distribution is paid in): the
    # rest-of-year total is the year's, whatever window was asked for
    # (2026-10 audit R08).
    reach = max(horizon, add_months(yend, 1))
    rows: List[Dict[str, Any]] = []
    omitted: List[Dict[str, Any]] = []
    for pos in sorted(positions, key=lambda p: (p["symbol"],
                                                p["account"])):
        sym, acct = pos["symbol"], pos["account"]
        # The history as of the date (a later payment in the books is
        # not known yet on it).
        hist = [p for p in (pays.get(sym) or []) if p.pay_date <= as_of]
        if not hist:
            omitted.append({"account": acct, "symbol": sym,
                            "shares": pos["qty"]})
            continue
        sched = schedule(hist)
        latest = sched[-1]
        name, step, gap = detect_frequency([p.pay_date for p in sched])
        ttm = ttm_per_share(sched, as_of)
        cur = latest.currency
        rate = 1.0 if cur == base.upper() else fx(cur)
        mine = [p for p in hist if p.account == acct]
        wrate = None
        if mine and mine[-1].tax > 0.005 and mine[-1].gross > 0:
            wrate = mine[-1].tax / mine[-1].gross
        shares = float(pos["qty"])
        per_pay = latest.per_share * shares
        flags: List[str] = []
        projected: List[Dict[str, Any]] = []
        missed = 0
        status = "ok"
        win = roy = 0.0
        if name:
            grace = GRACE_DAYS[name]
            k = 1
            while True:
                d = (add_months(latest.pay_date, step * k) if step
                     else _iso(date.fromisoformat(latest.pay_date)
                               + timedelta(days=7 * k)))
                if d > reach:
                    break
                k += 1
                if (date.fromisoformat(as_of)
                        - date.fromisoformat(d)).days > grace:
                    missed += 1
                    continue
                td = _projected_tax_date(latest, d, income_date)
                projected.append({"pay_date": d, "tax_date": td,
                                  "amount": round(per_pay, 6)})
            if missed >= 2:
                status = "stopped"
                flags.append(f"{missed} expected payments not in the books")
                projected = []
            elif missed == 1:
                status = "late"
                flags.append("1 expected payment not in the books")
            roy = sum(p["amount"] for p in projected
                      if ystart <= p["tax_date"] <= yend)
            projected = [p for p in projected if p["pay_date"] <= horizon]
            win = sum(p["amount"] for p in projected)
        else:
            status = "irregular"
            flags.append("single payment" if len(sched) < 2
                         else "irregular schedule")
            if ttm <= 1e-12:
                status = "stopped"
                flags.append("no payment in the last 12 months")
            else:
                win = ttm * shares * months / 12.0
                start = max(date.fromisoformat(as_of),
                            date.fromisoformat(ystart) - timedelta(days=1))
                left = (date.fromisoformat(yend) - start).days
                roy = ttm * shares * max(0, left) / 365.0 if left > 0 \
                    else 0.0
        def _b(v):
            return None if rate is None else round(v * rate, 2)
        nxt = projected[0] if projected else None
        rows.append({
            "account": acct, "group": pos.get("group") or "",
            "symbol": sym, "currency": cur, "shares": shares,
            "frequency": name or ("1 payment" if len(sched) < 2
                                  else "irregular"),
            "median_gap_days": gap, "payments": len(sched),
            "last_pay_date": latest.pay_date,
            "last_per_share": round(latest.per_share, 6),
            "ttm_per_share": round(ttm, 6),
            "next_pay_date": nxt["pay_date"] if nxt else None,
            "next_tax_date": nxt["tax_date"] if nxt else None,
            "next_amount": round(nxt["amount"], 2) if nxt else None,
            "next_amount_base": _b(nxt["amount"]) if nxt else None,
            "window_amount": round(win, 2),
            "window_amount_base": _b(win),
            "rest_of_year_amount": round(roy, 2),
            "rest_of_year_base": _b(roy),
            "fx_rate": rate,
            "withholding_rate": (round(wrate, 6) if wrate is not None
                                 else None),
            "withholding_window_base": (_b(win * wrate)
                                        if wrate is not None else None),
            "withholding_rest_of_year_base": (_b(roy * wrate)
                                              if wrate is not None
                                              else None),
            "status": status, "flags": flags,
            "projected": [dict(p, amount=round(p["amount"], 2),
                               amount_base=_b(p["amount"]))
                          for p in projected],
        })

    def _tot(key):
        t = {"taxable": 0.0, "sheltered": 0.0}
        for r in rows:
            v = r.get(key)
            if v is not None and r["group"] in t:
                t[r["group"]] += v
        return {g: round(v, 2) for g, v in t.items()}
    return {
        "schema_version": SCHEMA_VERSION, "planning_only": True,
        "as_of_date": as_of, "months": months, "window_end": horizon,
        "tax_year": tax_year, "currency": base.upper(),
        "rows": rows,
        "totals": {
            "rest_of_year": _tot("rest_of_year_base"),
            "window": _tot("window_amount_base"),
            "withholding_window": _tot("withholding_window_base"),
            "withholding_rest_of_year": _tot(
                "withholding_rest_of_year_base"),
        },
        "omitted_no_history": omitted,
        "no_fx": sorted({r["currency"] for r in rows
                         if r["fx_rate"] is None}),
    }


# ------------------------------------------------------------- render
def _few(names: List[str], n: int) -> str:
    names = list(dict.fromkeys(names))
    return ", ".join(names[:n]) + (f" +{len(names) - n} more"
                                   if len(names) > n else "")


def _ps(v: float) -> str:
    return f"{v:,.4f}".rstrip("0").rstrip(".") if v else "0"


def render(doc: Dict[str, Any], details: bool = False,
           cmd: str = "tjs div-estimate"):
    """The text view (lib/out.Doc): a legend, one row per position,
    the totals as figures, then what to check; --details adds each
    payer's schedule and projected payments and the omitted positions."""
    from taxjson.lib import out
    from taxjson.lib.report_model import fmt_money
    base = doc["currency"]
    n = doc["months"]
    d = out.Doc(f"DIVIDEND ESTIMATE — planning numbers, as of "
                f"{doc['as_of_date']} ({base})")
    rows = doc["rows"]
    if rows:
        d.para("Planning numbers, not filing figures: each payer's last "
               "per-share amount x shares held now, on its own past "
               "schedule.")
        body = []
        for r in rows:
            body.append([
                r["symbol"], r["account"], r["frequency"],
                f"{r['shares']:,.4f}".rstrip("0").rstrip("."),
                f"{_ps(r['last_per_share'])} {r['currency']}",
                r["next_pay_date"] or "-",
                ("-" if r["next_amount_base"] is None
                 else fmt_money(r["next_amount_base"])),
                ("-" if r["window_amount_base"] is None
                 else fmt_money(r["window_amount_base"]))])
        d.table(["TICKER", "ACCOUNT", "FREQ", "SHARES", "LAST/SH",
                 "NEXT", "NEXT AMT", f"{n}M"], body,
                aligns=["<", "<", "<", ">", ">", "<", ">", ">"],
                drop=(3, 4, 6))
    else:
        d.para("No position held now has a dividend history in the "
               "books.")
    t = doc["totals"]

    def _pair(x):
        return (f"taxable {fmt_money(x['taxable'])}, sheltered "
                f"{fmt_money(x['sheltered'])}")
    if rows:
        d.blank()
        yr = doc["tax_year"]
        roy = (_pair(t["rest_of_year"])
               if doc["as_of_date"] <= f"{yr}-12-31"
               else "the tax year is over")
        kv = [(f"Rest of tax year {yr}", roy),
              (f"Next {n} months", _pair(t["window"]))]
        if any(r["withholding_rate"] is not None for r in rows):
            kv.append((f"Withholding, {n} months",
                       _pair(t["withholding_window"])))
        d.kv(kv)
    check = [r for r in rows if r["status"] != "ok"]
    acts = []
    if check:
        syms = list(dict.fromkeys(r["symbol"] for r in check))
        acts.append(out.act(f"{len(syms)} payer(s) to check: "
                            f"{_few(syms, 2)}", f"{cmd} --details"))
    if doc["no_fx"]:
        acts.append(out.act(f"no {', '.join(doc['no_fx'])} rate in the "
                            f"books: those rows are out of the totals",
                            "tjs run"))
    if not details:
        if acts:
            d.blank()
            for a in acts:
                d.item(a[len(out.ACT):], bullet=out.ACT)
        om = doc["omitted_no_history"]
        if om:
            d.para(f"{len(om)} held position(s) with no dividend history "
                   f"in the books are not estimated.")
        d.para(out.details_hint(f"{cmd} --details",
                                "schedules, projected payments"))
        return d
    if acts:
        d.blank()
        for a in acts:
            d.item(a[len(out.ACT):], bullet=out.ACT)
    d.section("SCHEDULES AND PROJECTED PAYMENTS")
    for r in rows:
        proj = "; ".join(
            f"{p['pay_date']}"
            + (f" (tax date {p['tax_date']})"
               if p["tax_date"] != p["pay_date"] else "")
            + f" {fmt_money(p['amount'])} {r['currency']}"
            for p in r["projected"]) or "none"
        gap = (f", typical gap {r['median_gap_days']:g} days"
               if r["median_gap_days"] is not None else "")
        wh = (f"; withholding {r['withholding_rate'] * 100:.1f}% (the "
              f"latest payment in {r['account']})"
              if r["withholding_rate"] is not None else "")
        fxs = (f"; at {r['fx_rate']:g} {base}/{r['currency']}"
               if r["fx_rate"] not in (None, 1.0) else "")
        fl = (" Check: " + "; ".join(r["flags"]) + "."
              if r["flags"] else "")
        d.item(f"{r['symbol']} ({r['account']}): {r['payments']} "
               f"payment(s), {r['frequency']}{gap}; last "
               f"{r['last_pay_date']} at {_ps(r['last_per_share'])} "
               f"{r['currency']}/share, 12-month {_ps(r['ttm_per_share'])}"
               f"/share; projected: {proj}{wh}{fxs}.{fl}", indent="  ")
    d.section("NOT ESTIMATED (no dividend history in the books)")
    if doc["omitted_no_history"]:
        d.para(", ".join(f"{o['symbol']} ({o['account']})"
                         for o in doc["omitted_no_history"]), indent="  ")
    else:
        d.line("  (none)")
    d.section("HOW IT IS ESTIMATED")
    d.para("The frequency is the grid (weekly, monthly, quarterly, "
           "semi-annual, annual) most of the last payments fall on, "
           "anchored at the latest one: a payment off the grid (a "
           "special) or a stretch of empty slots (the position not "
           "held) does not break it, and postings a few days apart are "
           "one payment; no grid, or a single payment, is estimated pro "
           "rata from the 12-month amount per share. Each projected "
           "payment is the "
           "latest per-share amount x the shares held now, converted at "
           "the books' latest rate, and counts in the tax year of its "
           "pay date (a Canadian trust's distribution: its record date), "
           "as in divs-sum. A payment more than a few days overdue is "
           "taken as missing from the books; two missing ones stop the "
           "projection. Planning numbers only: a payer can cut, raise or "
           "skip a dividend at any time.", indent="  ")
    return d
