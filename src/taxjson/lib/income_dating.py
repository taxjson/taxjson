"""Which tax year an income row belongs to, and what a payment in lieu
is — per country (the owner's Phase-C decisions D3, D4, D5, D8).

The parsers record neutral facts on a row (lib/core.TaxTransaction:
``record_date``, ``ex_date``, ``income_label``, ``dealer_country``,
``issuer_country``); this module is the ONE place that turns them into a
tax consequence, gated on the project's country. Every consumer — the
gains run (lib/pipeline.run_gains), the .sum income section
(taxjson-sum-income), the machine report, and the views (`divs-sum`,
`dil-sum`, `roc-sum`) — asks the same ``IncomeRules`` object, so the
numbers cannot disagree. tax-logic states each rule (ids in brackets).

Canada
- [CA-INC-DATE-DIV] A corporation's dividend is income when PAID (s.82(1)).
- [CA-INC-DATE-TRUST] A Canadian trust's distribution is income of the
  year it became PAYABLE (s.104(13)): a row the broker calls a
  distribution ("DIST ON", RBC "Distribution") on a Canadian issuer
  (Canadian listing or CA ISIN) with a printed record date is dated by
  that record date. Split-share corporations say "Distribution" too:
  ``SPLIT_SHARE_ROOTS`` and ``[settings] corporate_distributions`` keep
  them on the pay date. Foreign funds: the pay date.
- [CA-INC-DATE-ROC-TRUST] A Canadian trust's return of capital lowers
  the ACB when payable (s.53(2)(h)): an ADJUST ``roc`` row with a
  record date is moved to it (the engine books it there). With no
  record date (IB), a January-paid one is WARNED about.
- [CA-INC-DATE-ROC] A corporation's (s.53(2)(a)) or a foreign issuer's
  return of capital lowers the ACB when paid.
- [CA-INC-PIL-01] A payment in lieu on a Canadian issuer's share paid by
  a Canadian dealer is a taxable (eligible) dividend (s.260(5)/(5.1),
  as the dealer's T5 box 24 reports it); every other payment in lieu is
  ordinary income. The slip is authoritative.

United States
- [US-INC-DATE-DIV] Dividends and payments in lieu: the pay date.
- [US-INC-DATE-RIC] A RIC/REIT dividend declared in Oct-Dec and paid in
  January is received on Dec 31 (§852(b)(7), §857(b)(9)): taxjson
  cannot tell a fund from a company, so it WARNS when a January payment
  has an Oct-Dec ex/record date, and ``[settings] ric_january_dividends``
  moves the listed payments to Dec 31 of the prior year.
- [US-INC-01] A payment in lieu is ordinary (non-qualified) income.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from taxjson.lib import country as _C

# Listing suffixes of Canadian exchanges (TSX, TSXV, CSE, Cboe Canada).
CA_LISTING_SUFFIXES = frozenset({"TO", "V", "CN", "NE", "VN"})

# Split-share corporations: their payouts are labelled "Distribution"
# by RBC and "DIST ON" by Questrade, but they are corporations' T5
# dividends (s.82(1): taxed when received) and their return of capital
# is a PUC reduction (s.53(2)(a): when paid). Matched on the ticker
# root, every class and preferred series included (FTN.PR.A.TO, FTN.TO).
# A project adds others with [settings] corporate_distributions.
SPLIT_SHARE_ROOTS: FrozenSet[str] = frozenset({
    "BK",    # Canadian Banc Corp
    "DF",    # Dividend 15 Split Corp II
    "DFN",   # Dividend 15 Split Corp
    "DGS",   # Dividend Growth Split Corp
    "ENS",   # E Split Corp
    "FFN",   # North American Financial 15 Split Corp
    "FTN",   # Financial 15 Split Corp
    "GDV",   # Global Dividend Growth Split Corp
    "LBS",   # Life & Banc Split Corp
    "LCS",   # Brompton Lifeco Split Corp
    "LFE",   # Canadian Life Companies Split Corp
    "PDV",   # Prime Dividend Corp
    "PIC",   # Premium Income Corp (PIC.A)
    "PWI",   # Sustainable Power & Infrastructure Split Corp
    "SBC",   # Brompton Split Banc Corp
    "SBN",   # S Split Corp
    "WFS",   # World Financial Split Corp
    "XMF",   # M Split Corp
    "XTD",   # TDb Split Corp
    "YCM",   # Commerce Split Corp
})

# A description that names a split-share corporation ("TDB SPLIT CORP
# ... DIST ON"): a corporation's payout whatever the list says (audit
# A2-0076, A2-0231).
_SPLIT_CORP_RE = re.compile(r"\bSPLIT\s+CORP", re.IGNORECASE)
# Another corporation named in a "distribution" description: not moved
# (a trust can be named "... Corp" too), but warned about.
_CORP_RE = re.compile(r"\b(?:CORP|CORPORATION|INC|LTD)\b", re.IGNORECASE)
# A record date this many days or more before the pay date is not a
# plausible declaration (a typo or a stale field): the pay date is kept
# and the row is warned about (audit A2-0229).
MAX_RECORD_LEAD_DAYS = 92
# Prefix of the warnings `taxjson run` echoes to the console (printed by
# the gains stage as "warning: ATTENTION: income year: ...").
ATTENTION_INCOME_YEAR = "ATTENTION: income year: "

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SETTING_CORPORATE = "corporate_distributions"
SETTING_RIC = "ric_january_dividends"


def _get(row: Any, key: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        v = row.get(key, default)
    else:
        v = getattr(row, key, default)
    return default if v is None else v


def listing_suffix(symbol: str) -> str:
    s = str(symbol or "").upper()
    return s.rsplit(".", 1)[-1] if "." in s else ""


def listing_root(symbol: str) -> str:
    """'FTN.PR.A.TO' -> 'FTN'; 'XIC.TO' -> 'XIC'; 'BRK.B.US' -> 'BRK'."""
    return str(symbol or "").upper().split(".", 1)[0]


def is_canadian_listing(symbol: str) -> bool:
    return listing_suffix(symbol) in CA_LISTING_SUFFIXES


def is_canadian_issuer(row: Any) -> bool:
    """The issuer's country from its ISIN when the export gave one,
    else its listing (a Canadian exchange)."""
    ic = str(_get(row, "issuer_country") or "").upper()
    if ic:
        return ic == "CA"
    return is_canadian_listing(_get(row, "symbol"))


def _iso(v: Any) -> str:
    s = str(v or "")[:10]
    return s if _ISO.match(s) else ""


class IncomeRulesError(ValueError):
    pass


def _symbols(value: Any, key: str) -> Tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)) or not all(
            isinstance(v, str) and v.strip() for v in value):
        raise IncomeRulesError(
            f"[settings] {key} must be a list of symbols, e.g. "
            f"[\"XYZ.TO\"] (got {value!r})")
    return tuple(v.strip().upper() for v in value)


def parse_ric_entries(value: Any, key: str = SETTING_RIC
                      ) -> Tuple[Tuple[str, str], ...]:
    """[settings] ric_january_dividends: "SYM" (every January-paid
    dividend of that fund) or "SYM YYYY-MM-DD" (the payment with that
    pay date) -> ((SYM, date-or-''), ...)."""
    out = []
    for v in _symbols(value, key):
        parts = v.split()
        if len(parts) == 1:
            out.append((parts[0], ""))
            continue
        if (len(parts) == 2 and _ISO.match(parts[1])
                and parts[1][5:7] == "01"):
            try:
                _date.fromisoformat(parts[1])
            except ValueError:
                pass
            else:
                out.append((parts[0], parts[1]))
                continue
        raise IncomeRulesError(
            f"[settings] {key}: {v!r} is not \"SYMBOL\" or \"SYMBOL "
            f"YYYY-01-DD\" (a January pay date)")
    return tuple(out)


@dataclass(frozen=True)
class IncomeRules:
    """The income-dating and payment-in-lieu rules of one project."""
    country: str
    corporate_distributions: Tuple[str, ...] = ()
    ric_january_dividends: Tuple[Tuple[str, str], ...] = ()
    _corp: FrozenSet[str] = field(default=frozenset(), repr=False,
                                  compare=False)

    def __post_init__(self):
        c = _C.canonical_country(self.country, what="IncomeRules.country")
        object.__setattr__(self, "country", c)
        if c == _C.USA and self.corporate_distributions:
            raise IncomeRulesError(
                f"{SETTING_CORPORATE} is Canada-only (s.104(13) trust "
                f"income dating)")
        if c == _C.CANADA and self.ric_january_dividends:
            raise IncomeRulesError(
                f"{SETTING_RIC} is US-only (§852(b)(7) / §857(b)(9))")
        # An entry names an ISSUER: 'GHI.TO', 'ABC.PR.A' and 'DEF.UN'
        # cover every class and series of their root, as the built-in
        # split-share roots do (audit A2-0991: dotted entries without a
        # listing suffix matched nothing, and 'GHI.TO' missed
        # GHI.PR.B.TO).
        object.__setattr__(self, "_corp", frozenset(
            listing_root(s) for s in self.corporate_distributions))

    # ------------------------------------------------------------ build
    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "IncomeRules":
        """From a project's [settings] (country required)."""
        c = _C.settings_country(settings)
        return cls(
            country=c,
            corporate_distributions=_symbols(
                settings.get(SETTING_CORPORATE), SETTING_CORPORATE),
            ric_january_dividends=parse_ric_entries(
                settings.get(SETTING_RIC)))

    def cli_flags(self) -> List[str]:
        """The same choice as flags for taxjson-gains / -sum-income."""
        fl: List[str] = []
        for s in self.corporate_distributions:
            fl += ["--corporate-distribution", s]
        for s, d in self.ric_january_dividends:
            fl += ["--ric-january-dividend", f"{s} {d}".strip()]
        return fl

    # ------------------------------------------------------------ Canada
    def is_corporate(self, symbol: str) -> bool:
        """A Canadian issuer whose "distribution" is a corporation's
        payout (split-share list or the project's own list, by issuer
        root)."""
        root = listing_root(symbol)
        return root in SPLIT_SHARE_ROOTS or root in self._corp

    def is_corporate_row(self, row: Any) -> bool:
        """is_corporate, or a description naming a split-share
        corporation ("... SPLIT CORP ...")."""
        return (self.is_corporate(_get(row, "symbol"))
                or bool(_SPLIT_CORP_RE.search(
                    str(_get(row, "description") or ""))))

    def is_canadian_trust(self, row: Any) -> bool:
        """Canada: a Canadian issuer that is not on the corporate list.
        (The caller adds the label test for income rows.)"""
        return (self.country == _C.CANADA and is_canadian_issuer(row)
                and not self.is_corporate_row(row))

    @staticmethod
    def _plausible_record(rec: str, pay: str) -> bool:
        """A record date on or before the pay date and less than
        MAX_RECORD_LEAD_DAYS before it."""
        try:
            lead = (_date.fromisoformat(pay)
                    - _date.fromisoformat(rec)).days
        except ValueError:
            return False
        return 0 <= lead < MAX_RECORD_LEAD_DAYS

    def trust_record_date(self, row: Any) -> str:
        """Canada: the record date that dates a Canadian trust's
        distribution (s.104(13)), or '' when the pay date applies."""
        if self.country != _C.CANADA:
            return ""
        if str(_get(row, "action")).upper() != "DIVIDEND":
            return ""
        if str(_get(row, "income_label")).lower() != "distribution":
            return ""
        rec, pay = _iso(_get(row, "record_date")), _iso(_get(row, "date"))
        if not rec or not pay or not self._plausible_record(rec, pay):
            return ""
        return rec if self.is_canadian_trust(row) else ""

    def roc_record_date(self, row: Any) -> str:
        """Canada: the record date a Canadian trust's return of capital
        lowers the ACB on (s.53(2)(h)), or '' (the pay date)."""
        if self.country != _C.CANADA:
            return ""
        if (str(_get(row, "action")).upper() != "ADJUST"
                or str(_get(row, "type")).lower() != "roc"):
            return ""
        rec, pay = _iso(_get(row, "record_date")), _iso(_get(row, "date"))
        if not rec or not pay or rec >= pay \
                or not self._plausible_record(rec, pay):
            return ""
        return rec if self.is_canadian_trust(row) else ""

    def pil_is_dividend(self, row: Any) -> bool:
        """Canada: a payment in lieu on a Canadian issuer's share paid by
        a Canadian dealer is a taxable dividend (ITA s.260(5)/(5.1))."""
        if self.country != _C.CANADA:
            return False
        if str(_get(row, "action")).upper() != "DIVIDEND_IN_LIEU":
            return False
        return (str(_get(row, "dealer_country")).upper() == "CA"
                and is_canadian_issuer(row))

    # ------------------------------------------------------------ USA
    def ric_prior_year(self, row: Any) -> bool:
        """USA: a January dividend the project lists as a §852(b)(7) /
        §857(b)(9) payment (received Dec 31 of the prior year)."""
        if self.country != _C.USA or not self.ric_january_dividends:
            return False
        if str(_get(row, "action")).upper() != "DIVIDEND":
            return False
        pay = _iso(_get(row, "date"))
        if not pay or pay[5:7] != "01":
            return False
        sym = str(_get(row, "symbol")).upper()
        for s, d in self.ric_january_dividends:
            if _ric_symbol_match(s, sym) and (not d or d == pay):
                return True
        return False

    # ------------------------------------------------------------ both
    def income_date(self, row: Any) -> str:
        """The date whose year an income row belongs to."""
        pay = str(_get(row, "date") or "")
        if self.country == _C.CANADA:
            return self.trust_record_date(row) or pay
        if self.ric_prior_year(row):
            return f"{int(pay[:4]) - 1}-12-31"
        return pay

    def roc_date(self, row: Any) -> str:
        """The date a return-of-capital ADJUST lowers the cost on."""
        return self.roc_record_date(row) or str(_get(row, "date") or "")

    def row_date(self, row: Any) -> str:
        """income_date for income rows, roc_date for a ROC ADJUST, the
        row's own date otherwise — the one date views window on."""
        a = str(_get(row, "action")).upper()
        if a == "ADJUST":
            return self.roc_date(row)
        return self.income_date(row)

    # ------------------------------------------------------------ warnings
    def warnings(self, rows: Iterable[Any], year: Optional[int] = None
                 ) -> List[str]:
        """What the export cannot decide (one line each): Canada — a
        January-paid return of capital on a Canadian trust with no
        record date (IB), unless the two .tt lines it asks for are in
        the books; a trust-dated distribution whose record-date year is
        not its pay year (ATTENTION: one project year leaves it out); a
        record date implausibly far from the pay date (ATTENTION). USA
        — a January dividend with an Oct-Dec ex/record date that is not
        listed in ric_january_dividends."""
        rows = list(rows)
        out: List[str] = []
        adj_keys = set()
        if self.country == _C.CANADA:
            for r in rows:
                if (str(_get(r, "action")).upper() == "ADJUST"
                        and str(_get(r, "type")).lower() != "roc"):
                    adj_keys.add((str(_get(r, "symbol")),
                                  _iso(_get(r, "date")),
                                  round(float(_get(r, "net_amount", 0.0)
                                              or 0.0), 2)))
        for r in rows:
            pay = _iso(_get(r, "date"))
            if not pay:
                continue
            sym = _get(r, "symbol")
            action = str(_get(r, "action")).upper()
            if self.country == _C.CANADA:
                out += self._ca_year_warnings(r, pay, sym, action, year)
            if pay[5:7] != "01":
                continue
            if year is not None and int(pay[:4]) not in (year, year + 1):
                continue
            if self.country == _C.CANADA:
                if (action == "ADJUST"
                        and str(_get(r, "type")).lower() == "roc"
                        and not _iso(_get(r, "record_date"))
                        and self.is_canadian_trust(r)):
                    amt = -float(_get(r, "net_amount", 0.0) or 0.0)
                    prev = int(pay[:4]) - 1
                    # The pair this warning prescribes is in the books:
                    # handled (audit A2-0561, A2-0993).
                    if ((str(sym), f"{prev}-12-31", round(-amt, 2))
                            in adj_keys
                            and (str(sym), pay, round(amt, 2))
                            in adj_keys):
                        continue
                    out.append(
                        f"{sym}: return of capital {amt:,.2f} "
                        f"{_get(r, 'currency')} paid {pay} with no record "
                        f"date in the export — booked on the pay date. If "
                        f"this Canadian trust's ROC was payable in "
                        f"December (on the {prev} T3, box 42), the ACB "
                        f"drops on {prev}-12-31 (s.53(2)(h)): check the "
                        f"{prev} T3 box 42 and move it with two .tt "
                        f"lines: ADJUST {prev}-12-31 12:00:00 {sym} "
                        f"{_get(r, 'currency')} {-amt:.2f} and ADJUST "
                        f"{pay} 12:00:00 {sym} {_get(r, 'currency')} "
                        f"{amt:.2f}.")
                continue
            if action != "DIVIDEND" or self.ric_prior_year(r):
                continue
            if is_canadian_issuer(r):
                continue
            prev = int(pay[:4]) - 1
            marks = [d for d in (_iso(_get(r, "ex_date")),
                                 _iso(_get(r, "record_date")))
                     if d and d[:4] == str(prev) and d[5:7] in
                     ("10", "11", "12")]
            if not marks:
                continue
            amt = float(_get(r, "gross_amount", 0.0) or 0.0) or float(
                _get(r, "net_amount", 0.0) or 0.0)
            out.append(
                f"{sym}: dividend {amt:,.2f} {_get(r, 'currency')} paid "
                f"{pay} with an ex/record date of {marks[0]} — if it is a "
                f"fund (RIC) or REIT dividend declared in "
                f"October-December, it is received on {prev}-12-31 "
                f"(§852(b)(7), §857(b)(9)) and is on the {prev} Form "
                f"1099-DIV. Check the slip; to move it, add \"{sym} "
                f"{pay}\" to [settings] {SETTING_RIC}.")
        return out

    def _ca_year_warnings(self, r: Any, pay: str, sym: Any, action: str,
                          year: Optional[int]) -> List[str]:
        """Canada ATTENTION lines (prefix ATTENTION_INCOME_YEAR, echoed
        by `taxjson run`): income a record date moves across a year end,
        and a record date too far from the pay date to be used."""
        out: List[str] = []
        rec = _iso(_get(r, "record_date"))
        cur = _get(r, "currency")
        amt = (float(_get(r, "gross_amount", 0.0) or 0.0)
               or float(_get(r, "net_amount", 0.0) or 0.0))
        if action == "ADJUST":
            amt = -float(_get(r, "net_amount", 0.0) or 0.0)
        is_dist = (action == "DIVIDEND" and str(
            _get(r, "income_label")).lower() == "distribution")
        is_roc = (action == "ADJUST"
                  and str(_get(r, "type")).lower() == "roc")
        if (rec and (is_dist or is_roc) and self.is_canadian_trust(r)
                and not self._plausible_record(rec, pay)
                and (year is None or year in (int(rec[:4]),
                                              int(pay[:4])))):
            out.append(
                f"{ATTENTION_INCOME_YEAR}{sym}: record date {rec} is not "
                f"within {MAX_RECORD_LEAD_DAYS} days before the pay date "
                f"{pay} — not a plausible declaration, so the PAY date is "
                f"used for this {amt:,.2f} {cur} "
                f"{'return of capital' if is_roc else 'distribution'}. "
                f"Check the export row (and the T3).")
            return out
        inc = self.trust_record_date(r) if is_dist else (
            self.roc_record_date(r) if is_roc else "")
        if not inc or inc[:4] == pay[:4]:
            return out
        iy, py = int(inc[:4]), int(pay[:4])
        if year is not None and year not in (iy, py):
            return out
        what = "return of capital" if is_roc else "distribution"
        where = ("lowers the ACB in" if is_roc else "is income of")
        if year == py or year is None:
            tail = (f"it is NOT in {py}'s numbers: make sure the {iy} "
                    f"return carries it (the {iy} books end before the "
                    f"pay date)")
        else:
            tail = (f"it is counted in {iy} here; the {py} project leaves "
                    f"it out")
        corp = ""
        if _CORP_RE.search(str(_get(r, "description") or "")):
            corp = (f" Its description names a corporation: if {sym} is "
                    f"one, its payout is income when PAID (s.82(1)) — add "
                    f"it to [settings] {SETTING_CORPORATE}.")
        out.append(
            f"{ATTENTION_INCOME_YEAR}{sym}: {what} {amt:,.2f} {cur} paid "
            f"{pay} with record date {inc} {where} {iy} (a Canadian "
            f"trust's, {'s.53(2)(h)' if is_roc else 's.104(13)'}; the {iy} "
            f"T3) — {tail}.{corp}")
        return out


def _ric_symbol_match(entry: str, symbol: str) -> bool:
    """ric_january_dividends: exact, or a bare root against that one
    fund's US listing (SPY -> SPY.US). A bare root used to match every
    listing and class sharing it — TELUS's T.TO for AT&T's T, and the
    preferred series PSA.PR.H.US for PSA (audit A2-0230, A2-0992)."""
    if entry == symbol:
        return True
    if "." in entry:
        return False
    return symbol in (entry, f"{entry}.US")


def rules_for(country: Any, settings: Optional[Dict[str, Any]] = None
              ) -> IncomeRules:
    """IncomeRules of `country` with a project's [settings] overrides
    (only the country's own keys are read)."""
    s = dict(settings or {})
    s["country"] = country
    return IncomeRules.from_settings(s)
