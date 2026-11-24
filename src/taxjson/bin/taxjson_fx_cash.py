"""FX capital gains on foreign-currency CASH balances.

Foreign cash is property: a USD balance in a taxable account has a CAD
adjusted cost base, and every time USD is spent the difference between
that day's rate and the pooled average acquisition rate is a capital
gain or loss — ITA s.39(1.1): for individuals, only the year's NET
gain (or loss) on currency dispositions BEYOND $200 is taxable
(deductible). For a US project the same ledger is reported, but §988
treats investment FX as ORDINARY income with no de-minimis (the
$200-per-transaction personal-use exemption of §988(e) is not
modeled) — the report says so.

Two ledgers (tax-logic CA-FX-07 / US-FX-03):

v1, the DEFAULT, is NOT RELIABLE and is never presented as a filing
figure. It is reconstructed from the taxable accounts' NATIVE
transaction books only (cash flows in their own currency) priced with
the pipeline's per-day FX history:

    acquire USD  — sell a USD security, receive a USD dividend or
                   interest payment (GROSS — the withholding leaves
                   through its own TAX row)
    dispose USD  — buy a USD security, pay USD withholding tax or a
                   USD fee

It does not read what the brokers DO export about cash: currency
conversions (IB Trades/Forex rows, Kraken/Coinbase fiat trades),
deposits and withdrawals (IB Deposits & Withdrawals, RBC WIR/DEP/TFI
rows), statement cash balances (IB Cash Report), margin debt in a
foreign currency, or the pool carried in from the year before (each
history starts empty). So the ledger is asked to spend currency it
never saw acquired: such overdrafts dispose only what the pool holds
(the excess moves at that day's rate with zero gain) and are COUNTED,
per tax year with their amounts. Every v1 output says NOT RELIABLE —
do not file this figure (`unreliable_status`).

v2 (lib/fx_cash_v2, opt-in: [settings] fx_cash_ledger = "v2" or
`tjs fx-cash --ledger v2`) reads those cash events (lib/
cash_events), models foreign-currency debt per broker account,
reconciles each account's balance to the statement and REFUSES
(no figure) instead of guessing. Even when it computes it is labelled
"v2 (opt-in, under audit)".

Nothing here changes the capital-gains engine, Schedule 3 / 8949
exports, or `tjs sum` totals.
"""
from taxjson.lib.out import exit_text
import sys
from dataclasses import replace
from typing import Any, Dict, List, Optional

from taxjson.model import money as M
from taxjson.model.evidence import CorpCash, format_corp_cash, parse_corp_cash
from taxjson.lib.cash_legs import _cash_legs


CA_EXEMPTION = 200.0


def _cash_by_day(corp_cash: str) -> Dict[str, str]:
    """{paid: the `corp_cash` of the cash paid that day} of a value whose
    parts were paid on several days ('' for the row's own date), in the
    order the days first appear; a part that does not read is dropped,
    as the ledger would skip it. Each amount is kept as read: it went
    through a float and `.10g`, which cut a typed one to 10 digits (a
    cent off at 123456789.12) while the same cash on the row's own date
    kept them all (backlog TJ-2845)."""
    by_day: Dict[str, List[CorpCash]] = {}
    for c in parse_corp_cash(corp_cash):
        by_day.setdefault(c.paid, []).append(replace(c, paid=''))
    return {paid: format_corp_cash(parts) for paid, parts in by_day.items()}


FX_MAX_RATE_AGE_DAYS = 5      # the converter's lookback (CA-FX-02 / US-FX-02)


def build_ledger(transactions: List[Dict[str, Any]], base: str,
                 fx_history: Dict[str, Dict], year: int,
                 rate_of=None, country: Optional[str] = None
                 ) -> Dict[str, Any]:
    """Walk the FULL history (the currency pool's ACB needs it), report
    the tax year's dispositions. Returns per-currency summaries plus
    diagnostics. `rate_of(cur, date) -> rate|None` overrides the
    fx_history lookup (tests). `country` picks the futures settlement's
    lot rule (lib/futures.method_for); a book with plain futures and no
    country raises ValueError."""
    from taxjson.lib.price_chain import latest_rate

    def _rate(cur: str, on: str) -> Optional[float]:
        if rate_of is not None:
            return rate_of(cur, on)
        # Bounded lookback (audit R1-151): the rates file fills every
        # calendar day, so a rate older than a week means the rates
        # stop before the event — count it unrated (and say so) rather
        # than price it at a months-old rate, the same 5-day lookback
        # the converter applies.
        r, _d = latest_rate(fx_history, cur, on,
                            max_age_days=FX_MAX_RATE_AGE_DAYS)
        return r

    pools: Dict[str, List[float]] = {}       # cur -> [units, cost_base]
    per_cur: Dict[str, Dict[str, float]] = {}
    events: List[Dict[str, Any]] = []
    overdrafts: Dict[str, int] = {}
    # In the tax year only (settle date in it): count and the units the
    # ledger could not cover — what the NOT RELIABLE headline quotes.
    overdrafts_year: Dict[str, Dict[str, float]] = {}
    unrated: Dict[str, int] = {}
    ystr = str(year)

    # Plain futures on the settlement basis (native books carry each
    # fill's notional).
    from taxjson.lib.futures import (has_plain_futures, method_for,
                                     settle_futures_dicts)
    if has_plain_futures(transactions):
        if not country:
            raise ValueError("fx-cash: a book with futures needs the "
                             "project's country (its lot rule settles "
                             "each close's P/L)")
        transactions = settle_futures_dicts(list(transactions),
                                            method_for(country))
    # Settle date, then TRADE date, then clock time: a holiday makes a
    # Friday sale and a Monday buy settle the same day, and clock time
    # alone walked the buy first — a phantom overdraft (A2-0244; the
    # futures and distribution walks already sort this way).
    walked: List[Dict[str, Any]] = []
    for t in transactions:
        # Cash paid after its row's date is walked on the day it came.
        cc = str(t.get("corp_cash") or "")
        if "@" not in cc or not t.get("corp_event_id"):
            walked.append(t)
            continue
        for paid, cash in _cash_by_day(cc).items():
            part = dict(t, corp_cash=cash)
            if paid:
                part.update(date=paid, date_settle=paid)
            walked.append(part)
    rows = sorted(walked,
                  key=lambda t: (str(t.get("date_settle")
                                     or t.get("date") or ""),
                                 str(t.get("date") or ""),
                                 str(t.get("time") or "")))
    year_end = f"{ystr}-12-31"
    pools_ye: Optional[Dict[str, Dict[str, float]]] = None

    def _snap() -> Dict[str, Dict[str, float]]:
        return {c: {"units": M.shown(p[0], 2), "acb": M.shown(p[1], 2)}
                for c, p in sorted(pools.items()) if p[0] > 0.005}

    def _walk_flow(tx, cur, flow, cost=None) -> None:
        if not cur or cur == base or not flow:
            return
        d = str(tx.get("date_settle") or tx.get("date") or "")
        rate = (flow and cost / flow) if cost is not None and flow > 0 \
            else _rate(cur, d)
        if rate is None:
            unrated[cur] = unrated.get(cur, 0) + 1
            return
        pool = pools.setdefault(cur, [0.0, 0.0])
        stat = per_cur.setdefault(cur, {"acquired": 0.0, "disposed": 0.0,
                                        "gain": 0.0})
        if flow > 0:                                     # acquire
            pool[0] += flow
            pool[1] += flow * rate
            if d.startswith(ystr):
                stat["acquired"] += flow
            return
        units = -flow                                    # dispose
        covered = min(units, pool[0])
        gain = 0.0
        if covered > 0:
            avg = pool[1] / pool[0]
            gain = covered * (rate - avg)
            pool[0] -= covered
            pool[1] -= covered * avg
        if units > covered + 1e-9:
            # Overdraft: currency spent that the ledger never saw
            # acquired (a conversion/deposit the CSV doesn't carry).
            # Moves at today's rate, zero gain, loudly counted.
            overdrafts[cur] = overdrafts.get(cur, 0) + 1
            if d.startswith(ystr):
                oy = overdrafts_year.setdefault(cur, {"count": 0,
                                                      "units": 0.0})
                oy["count"] += 1
                oy["units"] += units - covered
        if d.startswith(ystr):
            stat["disposed"] += units
            stat["gain"] += gain
            events.append({"date": d, "currency": cur,
                           "units": M.shown(units, 2),
                           "rate": rate, "gain": M.shown(gain, 2),
                           "action": tx.get("action"),
                           "symbol": tx.get("symbol"),
                           "account": tx.get("account")})

    # A reward paid in a currency (a USD stablecoin is US-dollar cash
    # in Canada, CA-CRYPTO-02) that the export prices in another: its
    # own units come in, at the income's value when that is in the base
    # currency — as ledger v2 books it (backlog TJ-0038, TJ-0221).
    from taxjson.lib.country import stablecoin_model
    from taxjson.lib.cash_legs import _reward_cash
    stable_cash = bool(country) and stablecoin_model(country) == "cash"
    for tx in rows:
        if pools_ye is None and str(tx.get("date_settle")
                                    or tx.get("date") or "") > year_end:
            pools_ye = _snap()           # the balance at Dec 31
        rw = _reward_cash(tx, stable_cash)
        if rw is not None:
            v = abs(float(tx.get("gross_amount") or tx.get("net_amount")
                          or 0.0))
            _walk_flow(tx, rw[0], rw[1],
                       v if str(tx.get("currency") or "").upper() == base
                       else None)
            continue
        for cur, flow in _cash_legs(tx):
            _walk_flow(tx, cur, flow)

    net = sum(s["gain"] for s in per_cur.values())
    return {"per_currency": {c: {k: M.shown(v, 2) for k, v in s.items()}
                             for c, s in sorted(per_cur.items())},
            "events": events,
            "net_gain": M.shown(net, 2),
            "overdrafts": overdrafts,
            "overdrafts_year": {c: {"count": int(v["count"]),
                                    "units": M.shown(v["units"], 2)}
                                for c, v in sorted(overdrafts_year.items())},
            "unrated": unrated,
            # End of the whole history (the books may run past the
            # tax year) and at Dec 31 of the tax year — the latter is
            # what a broker's year-end cash balance can be compared to.
            "pools": _snap(),
            "pools_year_end": pools_ye if pools_ye is not None
            else _snap()}


def apply_jurisdiction(net_gain: float, country: str,
                       debt_gain: float = 0.0) -> Dict[str, Any]:
    """The reportable figure. Canada (s.39(1.1)): only the net beyond
    $200 counts, symmetric for losses — of the gain on disposing of
    currency; `debt_gain`, the part of `net_gain` realised on repaying
    a debt in a foreign currency (s.39(2), ledger v2's `debt_gain`),
    counts in full (backlog TJ-0724: the $200 erased it too). US
    (§988): ordinary income, no de-minimis modeled. An unknown country
    raises (lib/country)."""
    from taxjson.lib.country import is_usa
    if is_usa(country):
        return {"rule": "§988 (ordinary income)",
                "reportable": M.shown(net_gain, 2),
                "note": "§988 FX gain/loss on investment cash is "
                        "ORDINARY income, not capital — report the "
                        "full net; the §988(e) $200-per-transaction "
                        "personal-use exemption is not modeled."}
    currency = net_gain - debt_gain
    if currency > CA_EXEMPTION:
        reportable = currency - CA_EXEMPTION
    elif currency < -CA_EXEMPTION:
        reportable = currency + CA_EXEMPTION
    else:
        reportable = 0.0
    reportable += debt_gain
    note = ("Only the net gain (or loss) on foreign-currency "
            "dispositions beyond $200/year is a capital "
            "gain (loss) — Schedule 3, 'Bonds, debentures, "
            "promissory notes, and other similar properties'.")
    if abs(debt_gain) >= 0.005:
        note += (f" The {M.shown(debt_gain, 2):,.2f} on repaying debts in a foreign "
                 f"currency (s.39(2)) counts in full, outside the $200.")
    return {"rule": "ITA s.39(1.1) (capital, $200 de minimis)",
            "reportable": M.shown(reportable, 2),
            "note": note}


def v2_verdict(doc: Dict[str, Any], country: str
               ) -> Optional[Dict[str, Any]]:
    """apply_jurisdiction of a ledger v2 doc (lib/fx_cash_v2.build):
    None unless it computed; its gains on repaying debts (`debt_gain`)
    counted outside the $200 (s.39(2); backlog TJ-0724)."""
    if doc.get("status") != "computed":
        return None
    debt = sum(float(s.get("debt_gain") or 0.0)
               for s in (doc.get("per_currency") or {}).values())
    return apply_jurisdiction(doc["net_gain"], country, debt_gain=debt)


NOT_READ = ("conversions, deposits/withdrawals and margin balances are "
            "not read")


def has_activity(doc: Dict[str, Any]) -> bool:
    """The v1 ledger saw foreign-currency cash move in the tax year."""
    return bool(doc.get("overdrafts_year")) or any(
        abs(float(v or 0.0)) > 0.005
        for s in (doc.get("per_currency") or {}).values()
        for v in s.values())


def unreliable_status(doc: Dict[str, Any], year: int,
                      verdict: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """The v1 ledger's status: NEVER a filing figure (CA-FX-07 /
    US-FX-03). `headline` is the one line every output prints in place
    of the reportable figure; the raw numbers stay under
    `unreliable_raw` for whoever wants to look at them."""
    od = doc.get("overdrafts_year") or {}
    n = sum(int(v.get("count") or 0) for v in od.values())
    amounts = ", ".join(f"{M.shown(float(v.get('units') or 0.0), 2):,.2f} {c}"
                        for c, v in sorted(od.items()))
    active = has_activity(doc)
    if active:
        headline = (f"FX on foreign cash: NOT RELIABLE for {year} — {n} "
                    f"in-year overdraft{'' if n == 1 else 's'}"
                    + (f" ({amounts})" if amounts else "")
                    + f"; {NOT_READ}; do not file this figure")
    else:
        headline = (f"FX on foreign cash: no foreign-currency cash flow "
                    f"in the taxable books for {year} ({NOT_READ})")
    reasons = [f"{n} in-year overdraft(s): the ledger spent currency it "
               f"never saw acquired" + (f" ({amounts})" if amounts
                                        else ""),
               "currency conversions (IB Forex, Kraken/Coinbase fiat "
               "trades, bank conversions) are not read",
               "cash deposits and withdrawals are not read",
               "foreign-currency margin balances (debt) are not modelled",
               "each history starts with an empty pool (no opening "
               "balance carried from the year before)"]
    raw = {"net_gain": doc.get("net_gain"),
           "reportable": (verdict or {}).get("reportable")}
    return {"ledger": "v1", "status": "unreliable", "reliable": False,
            "active": active, "headline": headline, "reasons": reasons,
            "overdrafts_in_year": od, "unreliable_raw": raw}


def render_brief(doc: Dict[str, Any], base: str, year: int,
                 country: str, verdict: Dict[str, Any],
                 width_: Optional[int] = None) -> str:
    """`tjs fx-cash`'s default view (docs/output-style.md, Essentials
    first): the NOT RELIABLE line first, a legend, the table, the
    year-end balance to compare and the lines to act on; the reasons,
    the rule and the caveats with --details (render_report)."""
    from taxjson.lib import out
    from taxjson.lib.report_fmt import fmt_money
    w = out.width() if width_ is None else width_
    st = unreliable_status(doc, year, verdict)
    od = doc.get("overdrafts_year") or {}
    n = sum(int(v.get("count") or 0) for v in od.values())
    if st["active"]:
        head = (f"FX on foreign cash: NOT RELIABLE for {year} — {n} "
                f"in-year overdraft{'' if n == 1 else 's'}; do not file "
                f"this figure")
    else:
        head = st["headline"]
    lines = out.wrap(head + ".", w, "", "  ")
    lines.append("")
    lines += out.wrap(f"FX GAINS ON CASH — {base}, tax year {year}, "
                      f"{verdict['rule']}, ledger v1 (default)", w)
    active = {c: s for c, s in doc["per_currency"].items()
              if any(abs(v) > 0.005 for v in s.values())}
    if not active:
        lines.append("No foreign-currency cash activity in taxable "
                     "accounts this year.")
        return "\n".join(lines)
    lines.append(f"GAIN(LOSS): the FX move realized when foreign cash is "
                 f"spent (pooled average cost), in {base}.")
    body = [[c, fmt_money(s["acquired"]), fmt_money(s["disposed"]),
             fmt_money(s["gain"])]
            for c, s in sorted(active.items(),
                               key=lambda kv: -kv[1]["disposed"])]
    foot = [["NET (NOT RELIABLE)", "", "", fmt_money(doc["net_gain"])]]
    lines += out.fit_table(["CUR", "UNITS IN", "UNITS OUT",
                            f"GAIN(LOSS) {base}"],
                           body, aligns=["<", ">", ">", ">"], foot=foot,
                           width_=w)
    lines.append("")
    ye = doc.get("pools_year_end") or {}
    if ye:
        bal = ", ".join(f"{c} {fmt_money(v['units'])}"
                        for c, v in sorted(ye.items()))
        lines += out.kv_lines([(f"Ledger balance {year}-12-31",
                                f"{bal} (compare with the brokers' "
                                f"year-end cash)")], width_=w)
    if doc["unrated"]:
        lines.append(out.act("Cash events skipped with no FX rate on file",
                             "tjs run"))
    lines.append(out.act("It misses conversions, deposits and margin; "
                         "ledger v2 reads them", "tjs fx-cash --ledger v2"))
    lines.append(out.details_hint("tjs fx-cash --details",
                                  "why it is not reliable"))
    return "\n".join(lines)


def render_report(doc: Dict[str, Any], base: str, year: int,
                  country: str, verdict: Dict[str, Any],
                  width_: Optional[int] = None) -> str:
    """The report in the house layout (docs/output-style.md), wrapped at
    `width_` (default: the house width; 0 = never). Its FIRST
    paragraph is the NOT RELIABLE headline (`unreliable_status`); the
    checklist reads `fx-cash --json`."""
    from taxjson.lib import out
    from taxjson.lib.country import is_usa as _is_usa
    from taxjson.lib.report_fmt import fmt_money
    w = out.width() if width_ is None else width_
    st = unreliable_status(doc, year, verdict)
    # The caveat FIRST: whoever reads only the top never takes the
    # figure below for a filing number.
    lines = out.wrap(st["headline"] + ".", w, "", "  ")
    lines.append("")
    lines += out.wrap(f"FX GAINS ON CASH — {base}, tax year {year}, "
                      f"{verdict['rule']}, ledger v1 (default)", w)
    lines += out.wrap("NOT RELIABLE, not filing numbers — reconstructed "
                      "from the trades and income in the taxable "
                      "accounts' books only. Foreign cash is property: "
                      "spending it realizes the FX move since "
                      "acquisition (pooled average cost). The opt-in "
                      "ledger v2 reads conversions, deposits and "
                      "statement balances: [settings] fx_cash_ledger = "
                      "\"v2\" or `tjs fx-cash --ledger v2`.", w)
    lines.append("")
    # Currencies with in-year activity only — a stale pool with no
    # movement this year is noise. Largest flow first.
    active = {c: s for c, s in doc["per_currency"].items()
              if any(abs(v) > 0.005 for v in s.values())}
    if not active:
        lines.append("No foreign-currency cash activity in taxable "
                     "accounts this year.")
        return "\n".join(lines)
    body = [[c, fmt_money(s["acquired"]), fmt_money(s["disposed"]),
             fmt_money(s["gain"])]
            for c, s in sorted(active.items(),
                               key=lambda kv: -kv[1]["disposed"])]
    foot = [["NET (NOT RELIABLE)", "", "", fmt_money(doc["net_gain"])]]
    lines += out.fit_table(["CUR", "UNITS IN", "UNITS OUT",
                            f"GAIN(LOSS) {base}"],
                           body, aligns=["<", ">", ">", ">"], foot=foot,
                           width_=w)
    lines.append("")
    # The units are the currency's own, the gains the base currency's:
    # "All amounts CAD" over both read US dollars as CAD (TJ-0458).
    lines += out.wrap(f"UNITS IN / OUT: units of each currency; the gains "
                      f"in {base}. {verdict['note']}", w)
    # What to check, one paragraph each (continuation lines indented).
    warn: List[str] = []
    _W = out.label("warning", w)     # `Warning: ` shown, `warning: ` at 0
    ye = doc.get("pools_year_end") or {}
    if ye:
        bal = ", ".join(f"{c} {fmt_money(v['units'])}"
                        for c, v in sorted(ye.items()))
        warn.append(f"Ledger's foreign-cash balance at {year}-12-31: "
                    f"{bal}. Compare it with the brokers' year-end "
                    f"cash balances — a gap means conversions or "
                    f"deposits/withdrawals the ledger cannot see.")
    if doc["overdrafts"]:
        counts = ", ".join(f"{c} {n}" for c, n
                           in sorted(doc["overdrafts"].items()))
        oy = doc.get("overdrafts_year") or {}
        inyear = ", ".join(f"{c} {int(v['count'])}x "
                           f"{fmt_money(v['units'])}"
                           for c, v in sorted(oy.items()))
        warn.append(f"{_W}disposals exceeded the ledgered "
                    f"balance ({counts}; full history"
                    + (f"; in {year}: {inyear}" if inyear else "")
                    + ") — the cash conversions, deposits and margin "
                    "debt this ledger does not read. The excess moves "
                    "at the day's rate with zero gain.")
    # Always: explicit conversions (IB Forex rows, bank FX, deposits)
    # are not read, so a lot converted away and later re-bought is
    # priced against the wrong pool (R1-148).
    warn.append("CAVEAT: currency conversions, cash "
                "deposits/withdrawals and foreign-currency margin "
                "balances are not in the ledger, so this figure can be "
                "wrong in either direction — not only understated. It "
                "is not the "
                + ("§988" if _is_usa(country) else "s.39(1.1)")
                + " figure; do not file it.")
    if doc["unrated"]:
        counts = ", ".join(f"{c} {n}" for c, n
                           in sorted(doc["unrated"].items()))
        warn.append(f"{_W}cash events skipped with no FX rate on "
                    f"file ({counts}) — run `tjs run` to refresh "
                    f"rates.")
    lines.append("")
    for i, t in enumerate(warn):
        if w > 0 and t.startswith(_W):
            # A message (docs/output-style.md, Messages): its lines
            # flush-left, one blank line after it when it wraps.
            block = out.wrap(t, w, "", "")
            lines += block
            if len(block) > 1 and i + 1 < len(warn):
                lines.append("")
            continue
        lines += out.wrap(t, w, "", "  ")
    return "\n".join(lines)


def render_events(events: List[Dict[str, Any]],
                  width_: Optional[int] = None) -> List[str]:
    """`fx-cash --events`: the ledger's cash events as a table."""
    from taxjson.lib import out
    return out.fit_table(
        ["DATE", "ACCOUNT", "CUR", "UNITS", "RATE", "GAIN", "SYMBOL"],
        [[e["date"], e["account"], e["currency"], f"{M.shown(e['units'], 2):,.2f}",
          f"{e['rate']:g}", f"{M.shown(e['gain'], 2):+,.2f}", e["symbol"] or "-"]
         for e in events],
        aligns=["<", "<", "<", ">", ">", ">", "<"], width_=width_,
        key=0)


if __name__ == "__main__":                       # pragma: no cover
    sys.exit(exit_text("taxjson-fx-cash is not a standalone tool — use "
                       "`tjs fx-cash` (it needs the project's native "
                       "books and FX history)."))
