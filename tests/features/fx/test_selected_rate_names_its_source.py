"""Fixes of 2026-10-29, round 7 (wave C), group taxtext: the standalone
pipeline tools. The validator survives an impossible trade date beside
a settlement date (backlog TJ-0987), reads the qty alias (TJ-0977) and
checks multiplier and market_value (TJ-0979); the .tt conversion reads
the qty alias too (TJ-0977), writes an untimed row's clock (TJ-0983),
reads an income line's per-share price (TJ-0984) and keeps a broker
trade cancellation (TJ-0985); currency conversion prices income on its
pay date (TJ-0974) and names a selected rate's own source (TJ-0986);
sort and conversion keep the per-file broker accounts (TJ-0976). One
test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented tickers (SYN*), invented account
ids, small amounts.

Formerly: test_fix_1029_r7c_taxtext
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path


# =============================== TJ-0986 the source of the selected rate
class TestTJ0986SourceOfTheSelectedRate(unittest.TestCase):
    """TJ-0986: a five-column USD/CAD 1.25 row followed by a 1.50 quote
    of the same day tagged `boc`: the conversion applied 1.25 (the first
    row) but its summary named the Bank of Canada, the ignored row's
    source.

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0986SourceOfTheSelectedRate"""

    def _sources(self, text):
        from taxjson.bin import taxjson_convert_currency as CC
        from taxjson.lib import exchange_rates
        with tempfile.TemporaryDirectory() as d:
            rates = Path(d) / "rates.txt"
            rates.write_text(text)
            with contextlib.redirect_stderr(io.StringIO()):
                history = exchange_rates.load_exchange_rates(rates, "CAD")
            return history, CC.load_rate_sources(rates, "CAD")

    def test_an_untagged_selected_rate_has_no_source(self):
        history, sources = self._sources(
            "2025-06-02 12:00:00 USD CAD 1.25\n"
            "2025-06-02 16:00:00 USD CAD 1.50 boc\n")
        self.assertEqual(str(history["USD"]["2025-06-02"]), "1.25")
        self.assertEqual(sources.get("USD", {}), {})

    def test_the_summary_says_no_source_column(self):
        from taxjson.bin import taxjson_convert_currency as CC
        _h, sources = self._sources(
            "2025-06-02 12:00:00 USD CAD 1.25\n"
            "2025-06-02 16:00:00 USD CAD 1.50 boc\n")
        CC.reset_fallback_tally()
        CC._RATES_USED.add(("USD", "2025-06-02"))
        try:
            text = CC.rate_source_summary(sources)
        finally:
            CC.reset_fallback_tally()
        self.assertIn("Bank of Canada Valet for 0 dates", text)
        self.assertIn("1 from a rates file without a source column", text)

    def test_a_skipped_row_does_not_reserve_the_day(self):
        # (A row to another currency is the one the loader still skips:
        # a rate that is no plain decimal stops it, TJ-5045.)
        _h, sources = self._sources(
            "2025-06-02 12:00:00 USD EUR 1_25 yahoo\n"
            "2025-06-02 12:00:00 USD EUR 1.10 yahoo\n"
            "2025-06-02 16:00:00 USD CAD 1.50 boc\n"
            "2025-06-03 16:00:00 USD CAD 1.40 boc\n"
            "2025-06-03 17:00:00 USD CAD 1.45 yahoo\n")
        self.assertEqual(sources["USD"], {"2025-06-02": "boc",
                                          "2025-06-03": "boc"})


if __name__ == "__main__":
    unittest.main()
