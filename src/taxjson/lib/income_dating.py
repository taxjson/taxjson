"""Which tax year an income row belongs to, and what a payment in lieu
is: the rules moved to `taxjson.characterize.income_dating` (PLAN step
14), which states each one ([CA-INC-DATE-DIV], [CA-INC-DATE-TRUST],
[CA-INC-DATE-ROC-TRUST], [CA-INC-DATE-ROC], [CA-INC-DATE-ISSUER],
[CA-INC-03], [CA-INC-07]; [US-INC-DATE-DIV], [US-INC-DATE-RIC],
[US-INC-01]).

What stays here reads a project's [settings], which characterize may not
import: `IncomeRules.from_settings` and `rules_for`. Every other name is
the moved module's: import it from there (the re-exports kept here
until the test importers moved went after v1.0.0).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from taxjson.characterize import income_dating as _rules
from taxjson.characterize.income_dating import SETTING_CORPORATE, _symbols, parse_ric_entries
from taxjson.lib import country as _C
from taxjson.project.settings import as_settings


class IncomeRules(_rules.IncomeRules):
    """The income-dating and payment-in-lieu rules of one project
    (`taxjson.characterize.income_dating.IncomeRules`), built from its
    [settings] here."""

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "IncomeRules":
        """From a project's [settings] (country required)."""
        settings = as_settings(settings)
        c = _C.settings_country(settings)
        return cls(
            country=c,
            corporate_distributions=_symbols(
                settings.corporate_distributions, SETTING_CORPORATE),
            ric_january_dividends=parse_ric_entries(
                settings.ric_january_dividends))


def rules_for(country: Any, settings: Optional[Dict[str, Any]] = None
              ) -> IncomeRules:
    """IncomeRules of `country` with a project's [settings] overrides
    (only the country's own keys are read)."""
    return IncomeRules.from_settings(as_settings(settings).replace(country=country))
