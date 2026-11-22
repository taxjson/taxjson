"""A foreign issuer's return of capital (IB's "(Return of Capital)" label
on a non-Canadian ISIN): a dividend or a cost reduction.

The parse marks the row (a dividend row carrying ISSUER_ROC_KEY) and
leaves it; this step decides:

- CA-ACB-08: in a Canadian project a non-resident corporation's
  distribution on a portfolio share is a foreign dividend (ITA s.90(1));
  a US "return of capital" (a distribution beyond E&P) lowers the cost
  only when the issuer really reduced its paid-up capital
  (s.53(2)(b)(ii)). s.90(2)/(3) are the foreign-affiliate rules, not a
  portfolio holding's (audit S058-07). The default.
- CA-ACB-09: `[settings] foreign_return_of_capital = "acb"`, the
  issuer's own designation: the cost reduction.
- US-ROC-01: in a US project every issuer's return of capital lowers
  basis (§301(c)(2)); s.90(1) is Canadian law and never applies there.

Without a country the issuer's designation stands (the cost reduction)
unless `--foreign-roc dividend` asks for s.90(1)."""
from typing import Any, Callable, Dict, List, Optional

from taxjson.lib.brokerages.base import roc_adjust_row
from taxjson.rules import ids as R
from taxjson.rules.country import USA, canonical_country, foreign_roc_choice
from taxjson.parsers.toolbox.descriptions import FILL_MARK_RE, ISSUER_ROC_KEY

# What the booked dividend's description says of the rule.
S90_NOTE = (" [IB-designated return of capital, treated as a foreign "
            "dividend (ITA s.90(1)); a cost reduction only if the issuer "
            "reduced its paid-up capital, s.53(2)(b)(ii)]")


def foreign_roc_treatment(country: Optional[str], setting: Optional[str] = None) -> str:
    """"dividend" (CA-ACB-08) or "acb" (CA-ACB-09, US-ROC-01): how a
    foreign issuer's return of capital is booked for `country` under
    `[settings] foreign_return_of_capital` (`setting`; None: the
    country's default), by taxjson.rules.country `foreign_roc_choice`, the rule
    `tjs tax-logic` renders. No country (a bare taxjson-brokerage run):
    the issuer's designation, the cost reduction, unless the setting
    asks for s.90(1)."""
    return foreign_roc_choice(country, setting, no_country="acb")


# What a characterisation tells its caller of each row it books (PLAN step
# 11): the row, as booked, and the rule id. taxjson-brokerage passes none.
NoteRule = Callable[[Dict[str, Any], str], None]


def foreign_roc_rule(treatment: str, country: Optional[str]) -> Optional[str]:
    """The rule a row booked under `treatment` applies: s.90(1)'s foreign
    dividend (CA-ACB-08), or the cost reduction under Canadian law
    (CA-ACB-09) or US law (US-ROC-01). The dividend treatment is s.90(1)
    wherever it is asked for (the `dividend` value is Canada's,
    `FLAG_VALUE_COUNTRY`); with no country the cost reduction is the
    issuer's designation and no country's rule: None (the owner's ruling
    on TJ-4153, question 6)."""
    if treatment == "dividend":
        return R.CA_ACB_08
    if not str(country or "").strip():
        return None
    return R.US_ROC_01 if canonical_country(country) == USA else R.CA_ACB_09


def characterize_foreign_roc(rows: List[Dict[str, Any]], *, treatment: str,
                             country: Optional[str] = None,
                             note_rule: Optional[NoteRule] = None) -> None:
    """Each row carrying ISSUER_ROC_KEY, in place (its identity kept: the
    parser's notes of the row's export line hold it): the key taken
    off; under "dividend" the row stays a dividend and says why
    (S90_NOTE); under "acb" it becomes the cost reduction the parse
    books for a Canadian issuer (base.py `roc_adjust_row`: net_amount
    the cash received, negated; its issuer country kept). `note_rule`
    hears each row booked and its rule (`foreign_roc_rule` for
    `country`), when there is one."""
    rule = foreign_roc_rule(treatment, country)
    for tx in rows:
        if not tx.pop(ISSUER_ROC_KEY, False):
            continue
        desc = str(tx.get("description") or "")
        if treatment == "dividend":
            # Before a split-fill mark, as the parse wrote them.
            m = FILL_MARK_RE.search(desc)
            tx["description"] = (desc[:m.start()] + S90_NOTE + m.group()
                                 if m else desc + S90_NOTE)
            if note_rule is not None and rule is not None:
                note_rule(tx, rule)
            continue
        row = roc_adjust_row(symbol=tx["symbol"], currency=tx["currency"],
                             date=tx["date"], desc=desc,
                             amount=float(tx.get("net_amount") or 0.0),
                             account=tx["account"])
        if tx.get("issuer_country"):
            row["issuer_country"] = tx["issuer_country"]
        tx.clear()
        tx.update(row)
        if note_rule is not None and rule is not None:
            note_rule(tx, rule)
