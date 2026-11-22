"""TJ-2376: an OCC series in IB's Financial Instrument Information with a
six-character root (a five-letter ticker's series, an adjusted root) has no
padding between the root and the date; the pre-scan must index it as it
indexes a padded root."""
import unittest

from taxjson.lib.brokerages.ib_extractor import _IB_FII_OCC_RE, _ib_prescan

_HEAD = ["Financial Instrument Information", "Header", "Asset Category", "Symbol", "Description",
         "Conid", "Security ID", "Underlying", "Listing Exch", "Multiplier", "Expiry",
         "Delivery Month", "Type", "Strike", "Code"]


def _fii(symbol, conid, underlying):
    return ["Financial Instrument Information", "Data", "Equity and Index Options", symbol,
            f"{underlying} 20JUN25 20 C", conid, "", underlying, "CBOE", "100", "2025-06-20",
            "2025-06", "C", "20", ""]


class TestOccRoot(unittest.TestCase):
    def test_the_pattern(self):
        self.assertEqual(_IB_FII_OCC_RE.match("SAMPLF250620C00020000").groups(),
                         ("SAMPLF", "250620", "C", "00020000"))
        self.assertEqual(_IB_FII_OCC_RE.match("SAMPL 250620C00020000").groups(),
                         ("SAMPL", "250620", "C", "00020000"))
        self.assertEqual(_IB_FII_OCC_RE.match("AB1   250620P00005000").groups(),
                         ("AB1", "250620", "P", "00005000"))
        # A longer root, padded or not, is read as before (TJ-2408).
        self.assertEqual(_IB_FII_OCC_RE.match("TOOLONG 250620C00020000").groups(),
                         ("TOOLONG", "250620", "C", "00020000"))
        self.assertIsNone(_IB_FII_OCC_RE.match("SAMPLF 250620 C 00020000"))

    def test_a_six_character_root_is_indexed(self):
        rows = [["Statement", "Header", "Field Name", "Field Value"],
                ["Statement", "Data", "BrokerName", "Interactive Brokers"],
                _HEAD, _fii("SAMPLF250620C00020000", "99900002", "SAMPLF"),
                _fii("SAMPL 250620C00020000", "99900003", "SAMPL")]
        out = _ib_prescan(rows, "sample.csv")
        self.assertEqual(out["occ_by_conid"].get("99900002"), {("SAMPLF", "250620", "C", "00020000")})
        self.assertEqual(out["occ_by_conid"].get("99900003"), {("SAMPL", "250620", "C", "00020000")})


if __name__ == "__main__":
    unittest.main()
