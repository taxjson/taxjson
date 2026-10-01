"""What the planning verdicts (wash radar, sell-check, buy-check, watch,
harvest, web what-if) can and cannot see — one wording per country
(tax-logic CA-PLAN-04 / US-PLAN-04; the two never mix).

The superficial-loss rule (ITA s.54, s.251.1 affiliated persons) and the
wash-sale rule (IRC §1091, IRS Pub. 550) both reach purchases by people
outside the project — a spouse or common-law partner, or a corporation
you (or they) control. A project only holds its own accounts, so every
SAFE / CLEAR verdict is "safe as far as these accounts show" (audit
S054-22)."""

from taxjson.lib.country import canonical_country, USA

_NOTE = {
    "canada": ("Scope: these verdicts cover this project's accounts "
               "only. A purchase by your spouse or common-law partner, "
               "or by a corporation you or they control (affiliated "
               "persons, ITA s.251.1), within 30 days before or after a "
               "loss sale also makes it superficial — those accounts "
               "are not in the project, so check them yourself."),
    "usa": ("Scope: these verdicts cover this project's accounts only. "
            "A purchase by your spouse or by a corporation you control "
            "within 30 days before or after a loss sale also makes it a "
            "wash sale (§1091; IRS Pub. 550) — those accounts are not "
            "in the project, so check them yourself."),
}


def scope_note(country: str) -> str:
    """The one-line scope disclosure for `country` (canada | usa)."""
    c = canonical_country(country)
    return _NOTE["usa" if c == USA else "canada"]
