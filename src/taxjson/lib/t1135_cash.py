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
come in (`holdings_basis`; backlog TJ-0627). `taxjson t1135` builds the
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
    units walked. The base currency of an account is a row when every
    book of the account gives its balance, else it is not counted."""
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
