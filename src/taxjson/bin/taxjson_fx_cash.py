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

The ledger is reconstructed from the taxable accounts' NATIVE
transaction books (cash flows in their own currency) priced with the
pipeline's per-day FX history:

    acquire USD  — sell a USD security, receive a USD dividend or
                   interest payment
    dispose USD  — buy a USD security, pay USD withholding tax or a
                   USD fee

What the broker CSVs do NOT carry is explicit cash conversions and
cash deposits/withdrawals, so the ledger can be asked to spend
currency it never saw acquired. Such overdrafts dispose only what the
pool holds (the excess moves at that day's rate with zero gain) and
are COUNTED — a large overdraft count means conversion/deposit rows
are missing and the result understates activity. This is why the
feature is OFF by default (`fx_cash_gains = true` under [settings]
turns the end-of-run report on); the `taxjson fx-cash` command works
either way.

Nothing here changes the capital-gains engine, Schedule 3 / 8949
exports, or `taxjson sum` totals — the output is a standalone report
of the s.39(1.1) number and where to file it.
"""
import sys
from typing import Any, Dict, List, Optional

CA_EXEMPTION = 200.0

# Cash-moving actions and their direction. Sign-preserving amounts:
# a negative dividend (reversal) disposes, a negative fee (rebate)
# acquires — handled by flipping direction on negative flow.
_INFLOW = ("DIVIDEND", "DIVIDEND_IN_LIEU", "INTEREST")
_OUTFLOW = ("TAX", "FEE")


def _flows(tx: Dict[str, Any]) -> Optional[float]:
    """Signed cash flow of a transaction in its NATIVE currency:
    positive = cash received (acquires currency), negative = cash paid
    (disposes). None = not a cash event for this ledger."""
    action = tx.get("action")
    net = float(tx.get("net_amount") or 0.0)
    if action == "BUYSELL":
        qty = float(tx.get("quantity") or 0.0)
        if net == 0:
            return None
        # Buys consume cash, sells raise it; base books store net
        # magnitudes with the direction on quantity.
        return -abs(net) if qty > 0 else abs(net)
    if action in _INFLOW:
        return net                       # sign-preserving (reversals)
    if action in _OUTFLOW:
        # Repo convention: TAX positive = withheld (cash out); FEE
        # positive = charged.
        return -net
    return None


def build_ledger(transactions: List[Dict[str, Any]], base: str,
                 fx_history: Dict[str, Dict], year: int,
                 rate_of=None) -> Dict[str, Any]:
    """Walk the FULL history (the currency pool's ACB needs it), report
    the tax year's dispositions. Returns per-currency summaries plus
    diagnostics. `rate_of(cur, date) -> rate|None` overrides the
    fx_history lookup (tests)."""
    from taxjson.lib.price_chain import latest_rate

    def _rate(cur: str, on: str) -> Optional[float]:
        if rate_of is not None:
            return rate_of(cur, on)
        r, _d = latest_rate(fx_history, cur, on)
        return r

    pools: Dict[str, List[float]] = {}       # cur -> [units, cost_base]
    per_cur: Dict[str, Dict[str, float]] = {}
    events: List[Dict[str, Any]] = []
    overdrafts: Dict[str, int] = {}
    unrated: Dict[str, int] = {}
    ystr = str(year)

    rows = sorted(transactions,
                  key=lambda t: (str(t.get("date_settle")
                                     or t.get("date") or ""),
                                 str(t.get("time") or "")))
    for tx in rows:
        cur = str(tx.get("currency") or "").upper()
        if not cur or cur == base:
            continue
        flow = _flows(tx)
        if flow is None or flow == 0:
            continue
        d = str(tx.get("date_settle") or tx.get("date") or "")
        rate = _rate(cur, d)
        if rate is None:
            unrated[cur] = unrated.get(cur, 0) + 1
            continue
        pool = pools.setdefault(cur, [0.0, 0.0])
        stat = per_cur.setdefault(cur, {"acquired": 0.0, "disposed": 0.0,
                                        "gain": 0.0})
        if flow > 0:                                     # acquire
            pool[0] += flow
            pool[1] += flow * rate
            if d.startswith(ystr):
                stat["acquired"] += flow
            continue
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
            stat["disposed"] += units
            stat["gain"] += gain
            events.append({"date": d, "currency": cur,
                           "units": round(units, 2),
                           "rate": rate, "gain": round(gain, 2),
                           "action": tx.get("action"),
                           "symbol": tx.get("symbol"),
                           "account": tx.get("account")})

    net = sum(s["gain"] for s in per_cur.values())
    return {"per_currency": {c: {k: round(v, 2) for k, v in s.items()}
                             for c, s in sorted(per_cur.items())},
            "events": events,
            "net_gain": round(net, 2),
            "overdrafts": overdrafts,
            "unrated": unrated,
            "pools": {c: {"units": round(p[0], 2),
                          "acb": round(p[1], 2)}
                      for c, p in sorted(pools.items()) if p[0] > 0.005}}


def apply_jurisdiction(net_gain: float, country: str) -> Dict[str, Any]:
    """The reportable figure. Canada (s.39(1.1)): only the net beyond
    $200 counts, symmetric for losses. US (§988): ordinary income, no
    de-minimis modeled."""
    if country in ("us", "usa"):
        return {"rule": "§988 (ordinary income)",
                "reportable": round(net_gain, 2),
                "note": "§988 FX gain/loss on investment cash is "
                        "ORDINARY income, not capital — report the "
                        "full net; the §988(e) $200-per-transaction "
                        "personal-use exemption is not modeled."}
    if net_gain > CA_EXEMPTION:
        reportable = net_gain - CA_EXEMPTION
    elif net_gain < -CA_EXEMPTION:
        reportable = net_gain + CA_EXEMPTION
    else:
        reportable = 0.0
    return {"rule": "ITA s.39(1.1) (capital, $200 de minimis)",
            "reportable": round(reportable, 2),
            "note": "Only the net gain (or loss) on foreign-currency "
                    "dispositions beyond $200/year is a capital "
                    "gain (loss) — Schedule 3, 'Bonds, debentures, "
                    "promissory notes, and other similar properties'."}


def render_report(doc: Dict[str, Any], base: str, year: int,
                  country: str, verdict: Dict[str, Any]) -> str:
    from taxjson.lib.report_model import fmt_money, render_table
    lines = [f"FX GAINS ON CASH — {base}, tax year {year}, "
             f"{verdict['rule']}",
             f"(ESTIMATE ONLY, not filing numbers — reconstructed "
             f"from broker cash flows)",
             f"Foreign cash is property: spending it realizes the FX "
             f"move since acquisition.",
             f"Ledger: taxable accounts' native books, pooled average "
             f"cost.", ""]
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
    foot = [["NET", "", "", fmt_money(doc["net_gain"])],
            ["REPORTABLE", "", "", fmt_money(verdict["reportable"])]]
    lines += render_table(["CUR", "ACQUIRED", "DISPOSED", "GAIN(LOSS)"],
                          ["<", ">", ">", ">"], body, foot)
    lines += ["", f"All amounts {base}. {verdict['note']}"]
    warn: List[str] = []
    if doc["overdrafts"]:
        counts = ", ".join(f"{c} {n}" for c, n
                           in sorted(doc["overdrafts"].items()))
        warn.append(f"WARNING: disposals exceeded the ledgered "
                    f"balance ({counts}; full history) — cash "
                    f"conversions/deposits the broker CSVs don't "
                    f"carry. Those move at the day's rate with zero "
                    f"gain, so the result understates activity.")
    if doc["unrated"]:
        counts = ", ".join(f"{c} {n}" for c, n
                           in sorted(doc["unrated"].items()))
        warn.append(f"WARNING: cash events skipped with no FX rate on "
                    f"file ({counts}) — run `taxjson run` to refresh "
                    f"rates.")
    if warn:
        lines.append("")
        lines += warn
    return "\n".join(lines)


if __name__ == "__main__":                       # pragma: no cover
    sys.exit("taxjson-fx-cash is not a standalone tool — use "
             "`taxjson fx-cash` (it needs the project's native books "
             "and FX history).")
