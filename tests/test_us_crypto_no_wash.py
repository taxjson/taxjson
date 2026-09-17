"""US crypto accounts must NOT run §1091 wash-sale detection.

The IRS treats digital assets as property, not "securities" — the
wash-sale rule does not reach them (this is the basis of crypto
tax-loss harvesting). Before the 2026-07b audit fix, `taxjson run`
passed a usa-project crypto account the same --taxable flags as
equities and the engine denied legitimate crypto losses.

Canada is the opposite and stays wash-checked: s.54's superficial-loss
rule covers any identical property, crypto included.
"""

import unittest

from taxjson.bin.taxjson_run import _wash_flags


class TestWashFlags(unittest.TestCase):
    def test_usa_crypto_gets_no_wash(self):
        self.assertEqual(_wash_flags(True, True, "usa"),
                         ["--taxable", "--no-wash"])

    def test_usa_equity_stays_wash_checked(self):
        self.assertEqual(_wash_flags(True, False, "usa"), ["--taxable"])

    def test_canada_crypto_stays_wash_checked(self):
        self.assertEqual(_wash_flags(True, True, "canada"), ["--taxable"])

    def test_canada_equity_unchanged(self):
        self.assertEqual(_wash_flags(True, False, "canada"), ["--taxable"])

    def test_sheltered_gets_nothing(self):
        self.assertEqual(_wash_flags(False, True, "usa"), [])
        self.assertEqual(_wash_flags(False, False, "canada"), [])


if __name__ == "__main__":
    unittest.main()
