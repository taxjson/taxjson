"""IB one-contract-id ticker changes (pre-release review of v0.24.0):
the change is booked only when the contract id's own rows date it.

- H4: the change is booked at NEW's earliest row of ANY section (a
  corporate action, a return of capital, a dividend), not its first
  trade: the split / return of capital before that trade is kept (both
  countries).
- H6: a corporate action that names OLD and NEW together (a split that
  changes the symbol) is IB's own rename: no contract-id event on top.
- M1: dates per (contract id, symbol): another company that used the
  ticker (its own contract id) never dates the change; a symbol listed
  under two contract ids in one statement, or NEW's rows starting on or
  before OLD's last row of any section, is not booked (ATTENTION with
  the .tt line).
- LOW: a .tt RENAME of OLD to another symbol stops the booking; IB's
  `.OLD` placeholder is never a rename target.

The cases are tests/_support/ib_conid_renames's mixins, run here in a
single-folder project
(rules.ca.acb.test_contract_id_ticker_changes_in_a_year_folder runs them
again in a year folder).

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: U5550001).

Formerly: test_fix_conid_review
"""
import unittest

from _support.ib_conid_renames import (CorporateActionRenamesCases,
                                       DatesPerContractIdCases,
                                       DeclarationsAndPlaceholdersCases,
                                       NewEventBeforeNewFirstTradeCases,
                                       OldRowAfterNewFirstRowCases)


class TestNewEventBeforeNewFirstTrade(NewEventBeforeNewFirstTradeCases, unittest.TestCase):
    pass


class TestCorporateActionRenames(CorporateActionRenamesCases, unittest.TestCase):
    pass


class TestDatesPerContractId(DatesPerContractIdCases, unittest.TestCase):
    pass


class TestOldRowAfterNewFirstRow(OldRowAfterNewFirstRowCases, unittest.TestCase):
    pass


class TestDeclarationsAndPlaceholders(DeclarationsAndPlaceholdersCases, unittest.TestCase):
    pass


if __name__ == "__main__":
    unittest.main()
