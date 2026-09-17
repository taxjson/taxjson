"""Tests for `taxjson-detect-brokerage` CSV header sniffing.

Detection must work off generic, brokerage-distinctive markers — never
a hardcoded account number (that would only match one user's files and
leak personal data into the repo)."""
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_detect_brokerage import detect_brokerage


def _detect(content):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                      delete=False) as f:
        f.write(content)
        fname = f.name
    try:
        return detect_brokerage(Path(fname))
    finally:
        Path(fname).unlink()


class TestDetectBrokerage(unittest.TestCase):
    def test_webull_detected_by_action_code_column(self):
        # No account number anywhere — detection keys on the generic
        # "Action Code" trade column every Webull export carries.
        content = (
            "Currency,Date,Action Code,Symbol,Name,Status,Filled,Price\n"
            "USD,01/15/2024,BUY,AAPL,APPLE INC,Filled,100,150.00\n"
        )
        self.assertEqual(_detect(content), "webull")

    def test_questrade_detected(self):
        content = (
            "Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Account #,Activity Type,Account Type\n"
        )
        self.assertEqual(_detect(content), "questrade")

    def test_unknown_csv_returns_none(self):
        self.assertIsNone(_detect("col_a,col_b,col_c\n1,2,3\n"))

    def test_rbc_excel_roundtrip_without_preamble_detects_structurally(self):
        """An RBC export round-tripped through Excel/Sheets loses the
        'Activity Export'/'Account:' preamble entirely — the file starts
        at the column header with ISO datetimes. The distinctive header
        shape (bare Date + Activity + Symbol + Settlement Date) must
        detect as rbc_direct; the parser already handles this variant.
        Real-data regression (margin.csv, 2026-07-05)."""
        content = (
            "Date,Activity,Symbol,Symbol Description,Quantity,Price,"
            "Settlement Date,Account,Value,Currency,Description\n"
            "2026-05-28 00:00:00,Deposits & Contributions,,,,,"
            "2026-05-28 00:00:00,12345678,5649,USD,"
            "DEP - TRANSFER FUNDS FROM RBC\n"
        )
        self.assertEqual(_detect(content), "rbc_direct")

    def test_rbc_marker_detection_is_case_insensitive(self):
        content = ("Some preamble\nRBC DIRECT INVESTING INC.\n"
                   "Date,Activity,Description\n")
        self.assertEqual(_detect(content), "rbc_direct")

    def test_structural_rbc_does_not_swallow_generic_csvs(self):
        # A header missing any one of the four required columns must not
        # match (e.g. a generic export with Date+Activity only).
        self.assertIsNone(_detect("Date,Activity,Amount\n2026-01-01,Buy,5\n"))

    def test_ib_detection_requires_canonical_broker_name(self):
        """Detection now requires the canonical "Interactive Brokers"
        string in the Statement Data row, not a bare `"IB" in val`
        substring (which previously matched any uppercase value
        containing IB — e.g. "LIBOR" — and misdetected unrelated
        Statement-format exports as ib)."""
        content = (
            "Statement,Header,Field Name,Field Value\n"
            "Statement,Data,Notes,LIBOR Rate Source\n"
        )
        self.assertIsNone(_detect(content))


if __name__ == '__main__':
    unittest.main()
