"""T1135: the cash the books hold outside Canada (tax-logic CA-RPT-20;
backlog TJ-0454, TJ-0609).

Funds deposited or held outside Canada are specified foreign property
whatever their currency (category 1 of Form T1135). The books hold such
cash in two kinds of taxable account:

* a crypto account (`crypto = true`): an exchange's US dollars and its
  USD stablecoins (US-dollar cash in a Canadian project, CA-CRYPTO-02),
  any other currency, and its Canadian dollars — its country as the
  report treats crypto (CRYPTO, "check where held") unless the account's
  `custodian_country` gives one (Canada: not foreign property);
* an account whose `custodian_country` is outside Canada: every
  currency it holds, under that country.

The cost of a foreign currency is the FX-on-cash ledger v2's (lib/
fx_cash_v2, `build(..., holdings=True)`): each account's units at the
end of each day times the pool's average cost per unit (one pool per
currency over the taxable accounts, s.47); Canadian dollars are their
own cost, where the books give the balance (_base_holdings: an opening
and the statement balances reconciled). The days are on the project's
tax_date basis, the one the walk dates the securities on: on trade
dates the cash a purchase paid leaves on its trade date, as the shares
come in (`holdings_basis`; backlog TJ-0627). `tjs t1135` builds the
document (`cash_doc`) and passes it to taxjson-t1135 (`--cash FILE`),
whose walk counts each row toward the $100,000 test (bin/taxjson_t1135
`walk_costs`, `build_report`).

Where the ledger refuses (or cannot run), a foreign currency's row is
an UNKNOWN cost and the total is not settled; where the books cannot
give the Canadian dollars' balance, the account's cash is named as not
counted.

Interest the account pays on its cash (an INTEREST row naming no
symbol) is the income of the cash row of the currency it was paid in
(`interest`: the base books keep only its base amount; tax-logic
CA-RPT-14, backlog TJ-0628).
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = 1
# The country of crypto whose platform the books do not tell
# (bin/taxjson_t1135 CRYPTO).
CRYPTO = "CRYPTO"


def counted_accounts(accounts: Dict[str, Any]) -> Dict[str, str]:
    """{account: country} of the taxable accounts whose cash is foreign
    property in the books: an account whose `custodian_country` is
    outside Canada (its ISO alpha-3 code), and a crypto account whose
    custodian_country is unset (CRYPTO). An account held in Canada
    (custodian_country CAN, or unset on a securities account) is not.
    A value the config check refuses is left to it."""
    from taxjson.lib.t1135_country import parse_custodian_country
    out: Dict[str, str] = {}
    for name, acfg in sorted((accounts or {}).items()):
        if not isinstance(acfg, dict) or acfg.get("type") != "taxable":
            continue
        if acfg.get("custodian_country") is not None:
            try:
                cc = parse_custodian_country(acfg["custodian_country"])
            except ValueError:
                continue
            if cc:
                out[str(name)] = cc
        elif acfg.get("crypto"):
            out[str(name)] = CRYPTO
    return out


def _label(book: str) -> str:
    """The account a book (lib/cash_events.book_name) belongs to."""
    return str(book).split("/", 1)[0]


def merge(series: Iterable[List[List[Any]]]) -> List[List[Any]]:
    """Several books' [[date, units, cost], ...] as one: at each date
    any of them changed, the sum of each book's last point on or before
    it."""
    lists = [list(s) for s in series if s]
    dates = sorted({p[0] for s in lists for p in s})
    out: List[List[Any]] = []
    for d in dates:
        u = c = 0.0
        for s in lists:
            last = None
            for p in s:
                if p[0] <= d:
                    last = p
                else:
                    break
            if last is not None:
                u += float(last[1])
                c += float(last[2])
        pt = [d, round(u, 6), round(c, 2)]
        if not out or out[-1][1:] != pt[1:]:
            out.append(pt)
    return out


def _transit(transit: Iterable[Dict[str, Any]], accounts: Dict[str, str],
             base: str, groups: Dict[Tuple[str, str], List[List[List[Any]]]]
             ) -> List[Dict[str, Any]]:
    """The cash in transit between your own accounts at the year end
    (the ledger's holdings `transit`: it left one account in the year
    and arrives in another after it). It is still yours, and no
    account's units hold it (backlog TJ-0946). When every account it
    left and goes to is counted, under one country, it is that
    country's cash held outside Canada: its units and cost join the
    sending account's row from the day it left (`groups`). When only
    some of them are counted (a move between a Canadian and a foreign
    custodian, or two countries), the books do not tell where it was
    held on December 31, as when the books have no arrival for it: it
    is named, of unknown standing, and the total is not settled. A move
    between accounts none of which is counted is no foreign property.
    Returns the not_counted entries."""
    named: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for tr in transit:
        cur = str(tr.get("currency") or "").upper()
        if not cur or cur == base:
            continue
        frm = [_label(b) for b in tr.get("from") or []]
        to = [_label(b) for b in tr.get("to") or []]
        ends = set(frm) | set(to)
        if not ends & set(accounts):
            continue
        units = float(tr.get("units") or 0.0)
        cost = float(tr.get("cost") or 0.0)
        countries = {accounts.get(a) for a in ends}
        if frm and to and tr.get("since") and None not in countries \
                and len(countries) == 1:
            groups.setdefault((frm[0], cur), []).append(
                [[str(tr["since"]), round(units, 6), round(cost, 2)]])
            continue
        n = named.setdefault(
            (", ".join(sorted(set(frm))), ", ".join(sorted(set(to))), cur),
            {"units": 0.0, "cost": 0.0, "arrives": tr.get("arrives") or "",
             "account": (frm or to)[0]})
        n["units"] += units
        n["cost"] += cost
    out: List[Dict[str, Any]] = []
    for (frm_s, to_s, cur), n in sorted(named.items()):
        out.append({
            "account": n["account"], "currency": cur, "unknown": True,
            "why": (f"{n['units']:,.2f} {cur} in transit between your own "
                    f"accounts at the year end (from {frm_s or '?'} to "
                    f"{to_s or '?'}, arriving {n['arrives'] or '?'}; cost "
                    f"{n['cost']:,.2f} {base}): the books do not tell whether "
                    f"it was held outside Canada")})
    return out


def _spans(spans: Iterable[Dict[str, Any]], accounts: Dict[str, str],
           base: str, groups: Dict[Tuple[str, str], List[List[List[Any]]]]
           ) -> List[Dict[str, Any]]:
    """The own moves in transit on days of the year (the ledger's
    holdings `transit_spans`): still yours, in no account's units, so
    they fell out of the at-any-time maximum (backlog TJ-1129). As
    _transit does at the year end: when every account it left and goes
    to is counted, under one country, its units and cost join the sending
    account's row for those days (`groups`); a move between accounts none
    of which is counted is no foreign property. Otherwise the books do
    not tell where it was held on those days: it is named, one entry per
    from, to and currency with its largest cost. The year-end cost is not
    in question, so it leaves the total settled. Returns the not_counted
    entries."""
    named: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for sp in spans:
        cur = str(sp.get("currency") or "").upper()
        if not cur or cur == base:
            continue
        frm = _label(sp["from"]) if sp.get("from") else ""
        to = _label(sp["to"]) if sp.get("to") else ""
        ends = {a for a in (frm, to) if a}
        if not ends & set(accounts):
            continue
        countries = {accounts.get(a) for a in ends}
        if frm and None not in countries and len(countries) == 1:
            groups.setdefault((frm, cur), []).append(sp["points"])
            continue
        n = named.setdefault((frm, to, cur), {
            "units": 0.0, "cost": 0.0, "since": sp.get("since") or ""})
        n["units"] = max(n["units"], float(sp.get("units") or 0.0))
        n["cost"] = max([n["cost"]] + [float(p[2]) for p in sp["points"]])
    out: List[Dict[str, Any]] = []
    for (frm, to, cur), n in sorted(named.items()):
        out.append({
            "account": frm if frm in accounts else to, "currency": cur,
            "unknown": False, "transit": True,
            "why": (f"up to {n['units']:,.2f} {cur} (cost {n['cost']:,.2f} "
                    f"{base}) in transit between your own accounts during "
                    f"the year (from {frm or '?'} to {to or '?'}, first on "
                    f"{n['since']}): the books do not tell whether it was "
                    f"held outside Canada, and it is not in the maximum")})
    return out


def cash_doc(ledger: Optional[Dict[str, Any]], accounts: Dict[str, str],
             year: int, base: str, refusal: str = "",
             stablecoins: Iterable[str] = (),
             basis: str = "settle") -> Dict[str, Any]:
    """The cash rows of the T1135 (`--cash FILE` of taxjson-t1135):

    {"version", "year", "currency", "basis" (the date basis the points
    are on, the project's tax_date: the ledger's holdings are built on
    it, and taxjson-t1135 walks the securities on it; backlog TJ-0627),
    "ledger_status" ("computed", "not_computed", or "refused" when it
    could not run: `refusal`),
    "accounts": {account: country}, "series": [{"account", "currency",
    "country", "points": [[date, units, cost]], "unknown", "why"}],
    "not_counted": [{"account", "currency", "why", "unknown"}],
    "stablecoins": the coins whose income is the account's US-dollar
    cash's, "interest": {row id: currency} — a counted account's
    interest rows naming no symbol, each with the currency it was paid
    in (the ledger's holdings `interest`): the income of that cash row
    (taxjson-t1135 `cash_interest`; backlog TJ-0628)}.

    `ledger`: lib/fx_cash_v2.build's doc with `holdings` (None when it
    could not run). A foreign currency's row is of unknown cost when the
    ledger is not computed and a problem names its currency or none
    (its pool's average cost is the taxpayer's, over every account); a
    currency of a counted account a problem names is a row even with no
    units walked. A problem that reaches no row (it names no book or
    no currency, and the account has no row of unknown cost) leaves the
    account's foreign cash not counted and unknown, so the total is not
    settled (backlog TJ-0657); one of a currency, naming no book, only
    where the account had that currency in the year (TJ-1098), or whose
    file holds the line it could not place (TJ-1174). The base currency of an account is a row
    when every book of the account gives its balance, else it is not
    counted."""
    base = str(base).upper()
    hb = ((ledger or {}).get("holdings") or {}).get("basis")
    if hb is not None and hb != basis:
        raise ValueError(f"the ledger's holdings are on the {hb} basis, "
                         f"the T1135 walk's on the {basis} basis")
    doc: Dict[str, Any] = {
        "version": VERSION, "year": int(year), "currency": base,
        "basis": basis,
        "ledger_status": "refused" if ledger is None else ledger["status"],
        "accounts": dict(sorted(accounts.items())), "series": [],
        "not_counted": [], "stablecoins": sorted({str(s).upper()
                                                  for s in stablecoins}),
        "interest": {}}
    if not accounts:
        return doc
    for r in ((ledger or {}).get("holdings") or {}).get("interest") or []:
        if str(r.get("account") or "") in accounts and r.get("currency"):
            doc["interest"][str(r["id"])] = str(r["currency"]).upper()
    if ledger is None:
        for acct in sorted(accounts):
            doc["not_counted"].append({
                "account": acct, "currency": "", "unknown": True,
                "why": refusal or "the FX-on-cash ledger v2 could not run"})
        return doc
    computed = ledger.get("status") == "computed"
    problems = [] if computed else list(ledger.get("problems") or [])

    def why_unknown(cur: str) -> Optional[str]:
        for p in problems:
            if str(p.get("currency") or "").upper() in ("", cur):
                return str(p.get("text") or p.get("kind") or "")
        return None
    hold = ledger.get("holdings") or {}
    groups: Dict[Tuple[str, str], List[List[List[Any]]]] = {}
    for s in hold.get("foreign") or []:
        acct = _label(s["book"])
        if acct in accounts:
            groups.setdefault((acct, str(s["currency"]).upper()),
                              []).append(s["points"])
    for p in problems:
        cur = str(p.get("currency") or "").upper()
        acct = _label(p.get("book") or "")
        if p.get("book") and cur and cur != base and acct in accounts:
            groups.setdefault((acct, cur), [])
    transit_open = _transit(hold.get("transit") or [], accounts, base,
                            groups)
    transit_open += _spans(hold.get("transit_spans") or [], accounts, base,
                           groups)
    for (acct, cur), lists in sorted(groups.items()):
        why = why_unknown(cur) if not computed else None
        doc["series"].append({"account": acct, "currency": cur,
                              "country": accounts[acct],
                              "points": merge(lists),
                              "unknown": why is not None,
                              "why": why or ""})
    by_acct: Dict[str, List[Dict[str, Any]]] = {}
    for b in hold.get("base") or []:
        if _label(b["book"]) in accounts:
            by_acct.setdefault(_label(b["book"]), []).append(b)
    for acct, rows in sorted(by_acct.items()):
        bad = [r for r in rows if not r.get("ok")]
        if bad:
            doc["not_counted"].append({
                "account": acct, "currency": base, "unknown": False,
                "why": f"{bad[0]['book']}: {bad[0].get('why') or '?'}"})
            continue
        # A negative balance is a debt, not property: no cost amount.
        pts = merge([[[d, max(float(v), 0.0), max(float(v), 0.0)]
                      for d, v in r.get("points") or []] for r in rows])
        doc["series"].append({"account": acct, "currency": base,
                              "country": accounts[acct], "points": pts,
                              "unknown": False, "why": ""})
    doc["not_counted"] += transit_open
    # A refusal that reached no row: the ledger stopped before it walked
    # a counted account's foreign cash (a file of several broker
    # accounts' rows with no CASHBOOK line: a problem naming no book or
    # no currency), so the account has no row to carry it. Its foreign
    # cash is unknown, not none: named, and the total is not settled
    # (backlog TJ-0657). A problem of one currency that names no book (a
    # missing rate, an own move that never arrived) reaches only the
    # accounts with that currency in the year: another account's cash of
    # it is none, not unknown (backlog TJ-1098). A .tt line the ledger
    # could not place never became an event, so `currencies` cannot list
    # it: it reaches the account whose file holds it (backlog TJ-1174). A
    # carried record that does not read says nothing of who held it:
    # every account.
    unknown = {(s["account"], s["currency"]) for s in doc["series"]
               if s["unknown"]}
    had = hold.get("currencies")
    had = None if had is None else {(_label(b), str(c).upper())
                                    for b, c in had}
    for p in problems:
        cur = str(p.get("currency") or "").upper()
        if cur == base:
            continue
        book = str(p.get("book") or "")
        for acct in ([_label(book)] if book else [str(p["account"])]
                     if p.get("account") else sorted(accounts)):
            if acct not in accounts or (acct, cur) in unknown or (
                    not cur and any(a == acct for a, _c in unknown)):
                continue
            if cur and not book and had is not None \
                    and p.get("kind") not in ("carry", "book") \
                    and (acct, cur) not in had:
                continue
            unknown.add((acct, cur))
            doc["not_counted"].append({
                "account": acct, "currency": cur, "unknown": True,
                "why": str(p.get("text") or p.get("kind") or "")})
    return doc


def check(doc: Any) -> Optional[str]:
    """None for a cash document this version reads, else what is
    wrong."""
    if not isinstance(doc, dict) or not isinstance(doc.get("series"), list):
        return "not a T1135 cash document"
    if doc.get("version") != VERSION:
        return (f"T1135 cash document version {doc.get('version')!r}, "
                f"this taxjson reads {VERSION}")
    return None
