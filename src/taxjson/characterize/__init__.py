"""Characterisation: the country-keyed step between parsing and the
engine (the re-extraction's recommendation 8, PLAN.md step 5's
`characterize/` bullet). A parser emits the neutral fact a broker's row
states; a function here applies the Canadian or US rule to it, citing
its rule id, so a parse under either country gives the same rows.
taxjson-brokerage applies `characterize_rows` to each file's rows as it
is parsed, before the account's cross-statement pass (`reconcile_files`)
and anything else reads them: a slice that must see all of an account's
rows does not fit there as it is. The first slice: a foreign issuer's return of capital
(`foreign_roc`).

Not every country rule is a step after the parse. The USD stablecoins'
model (US-dollar cash, CA-CRYPTO-02, or property, US-CRYPTO-02) is the
basis a parse books on: the two models book different rows, count a row
differently and refuse different shapes, so there is no neutral row set
to fold. The parse takes it as an input, chosen by rules/country
`parse_basis`; past it the country only words the messages (the second
slice; tests/test_characterize.py `TestStablecoinBasis`)."""
from typing import Any, Dict, List, Optional

from taxjson.characterize.foreign_roc import (NoteRule, characterize_foreign_roc,
                                              foreign_roc_treatment)

__all__ = ["characterize_rows", "characterize_foreign_roc", "foreign_roc_treatment"]


def characterize_rows(rows: List[Dict[str, Any]], *, country: Optional[str],
                      foreign_return_of_capital: Optional[str] = None,
                      note_rule: Optional[NoteRule] = None) -> None:
    """Apply each characterisation to `rows` in place: the parse's
    neutral facts become the rows the country's rules book. `note_rule`
    hears each row a characterisation booked and the rule it applied."""
    characterize_foreign_roc(
        rows, treatment=foreign_roc_treatment(country, foreign_return_of_capital),
        country=country, note_rule=note_rule)
