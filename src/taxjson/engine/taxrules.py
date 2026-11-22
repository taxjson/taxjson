"""The base class both countries' engines build on.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

from taxjson.rules.country import (
    CountryError as _CountryError, canonical_country as _canonical_country,
)

from taxjson.model.transaction import TaxTransaction

from taxjson.engine.splits import _dedupe_corporate_splits
from taxjson.engine.replacement import _emit_option_replacement_stderr

if TYPE_CHECKING:
    from taxjson.engine.rows import RowId


class TaxRules:
    country: str                # rules/country's CANADA or USA: the engine's rules
    # run_gains turns this off on the instance and prints the warnings
    # after its year filter (audit S070-04).
    emit_replacement_stderr: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.country = _canonical_country(getattr(cls, 'country', None),
                                         what='TaxRules.country')
        for base in cls.__mro__[1:]:
            if base is TaxRules:
                break
            inherited = getattr(base, 'country', None)
            if inherited and inherited != cls.country:
                raise _CountryError(
                    f"TaxRules.country: {cls.__name__} says {cls.country!r} "
                    f"but inherits {base.__name__}'s {inherited!r}; an "
                    f"engine's rules are one country's (rules/country)")

    def _emit_replacement_warnings(self, warnings: Iterable[Dict[str, Any]]) -> None:
        """The option-replacement warnings to stderr (the epilogue both
        engines share), unless the caller prints them itself."""
        if self.emit_replacement_stderr:
            _emit_option_replacement_stderr(warnings, country=self.country)

    def _stamp_overrides(self, plan: Optional[_LO.Plan], would: Dict[RowId, Dict[str, Any]],
                         entries: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """The user's filing positions (lib/loss_overrides) summarised,
        stamped on the gain entries and returned for the result; None
        with no plan, and the result then carries no `loss_overrides`
        (the epilogue both engines share)."""
        if not plan:
            _LO.stamp_entries(entries, [])
            return None
        items = _LO.summarize(plan, would, country=self.country)
        _LO.stamp_entries(entries, items)
        return items

    def _dedupe_splits(self, transactions: List[TaxTransaction],
                       sheltered_transactions: Optional[List[TaxTransaction]],
                       affiliated_transactions: Optional[List[TaxTransaction]]
                       ) -> Tuple[List[TaxTransaction], List[TaxTransaction],
                                  List[TaxTransaction]]:
        """The stage both engines run once their rows are named and
        disambiguated: one corporate split = one application, so per-account
        SPLIT duplicates collapse across the three books (one shared `seen`)
        before any symbol-global pool or window walk sees them. Returns the
        three books (step 4f of the engine refactor: the shared preamble,
        stage by stage)."""
        _seen_splits: Set[Tuple[Any, ...]] = set()
        transactions = _dedupe_corporate_splits(transactions, _seen_splits)
        sheltered_transactions = _dedupe_corporate_splits(
            sheltered_transactions or [], _seen_splits)
        affiliated_transactions = _dedupe_corporate_splits(
            affiliated_transactions or [], _seen_splits)
        return transactions, sheltered_transactions, affiliated_transactions

    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: Optional[List[TaxTransaction]] = None, affiliated_transactions: Optional[List[TaxTransaction]] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True) -> Dict[str, Any]:
        raise NotImplementedError()
