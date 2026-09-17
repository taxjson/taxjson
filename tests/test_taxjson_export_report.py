"""Tests for the --report mode of taxjson-export."""
import argparse
import unittest

from taxjson.bin.taxjson_export import (
    _fmt_qty,
    process_data_report,
    render_report,
)


def _args(**overrides):
    base = dict(
        platform='report',
        long=False, short=False,
        no_equities=False, no_options=False, no_futures=True,
        no_cad=False, no_usd=False,
        inputs=[],
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _input(rows):
    return {"inventory": [
        {"symbol": s, "qty": q, "total_cost": tc, "currency": cur}
        for s, q, tc, cur in rows
    ]}


class TestFmtQty(unittest.TestCase):
    def test_integer_quantities_no_decimals(self):
        self.assertEqual(_fmt_qty(100), "100")
        self.assertEqual(_fmt_qty(100.0), "100")
        self.assertEqual(_fmt_qty(-50), "-50")

    def test_fractional_quantities(self):
        self.assertEqual(_fmt_qty(0.5), "0.5")
        self.assertEqual(_fmt_qty(0.12345), "0.12345")
        self.assertEqual(_fmt_qty(1.10), "1.1")


class TestRenderReport(unittest.TestCase):
    def _header_idx(self, lines):
        """Find the TICKER header row (banner pushes it down by 3)."""
        for i, ln in enumerate(lines):
            if 'TICKER' in ln and 'QTY' in ln:
                return i
        raise AssertionError("No TICKER header found in output")

    def test_basic_table(self):
        agg = {
            'AAPL.US': {'qty': 100.0, 'total_cost': 15000.0, 'currency': 'USD'},
            'SHOP.TO': {'qty': 50.0, 'total_cost': 5000.0, 'currency': 'CAD'},
        }
        lines = render_report(agg)
        self.assertGreater(len(lines), 2)
        # Banner header announces the report.
        self.assertTrue(any('HOLDINGS REPORT' in ln for ln in lines[:3]))
        hdr = self._header_idx(lines)
        self.assertIn('TICKER', lines[hdr])
        self.assertIn('QTY', lines[hdr])
        self.assertIn('COST/SHARE', lines[hdr])
        self.assertIn('TOTAL COST', lines[hdr])
        self.assertIn('CUR', lines[hdr])
        # Rows sorted alphabetically, starting after header + separator.
        body = lines[hdr + 2:]
        self.assertTrue(body[0].lstrip().startswith('AAPL.US'))
        self.assertTrue(body[1].lstrip().startswith('SHOP.TO'))
        # AAPL row contains cost/share = 150 and total = 15000.
        self.assertIn('150.0000', body[0])
        self.assertIn('15000.00', body[0])

    def test_currency_column_omitted_when_all_blank(self):
        agg = {'AAPL.US': {'qty': 100.0, 'total_cost': 15000.0, 'currency': ''}}
        lines = render_report(agg)
        hdr = self._header_idx(lines)
        self.assertNotIn('CUR', lines[hdr])

    def test_short_position_cost_per_share_positive(self):
        """Short qty=-10, total_cost=-500 (proceeds credited) → cost/share=+50."""
        agg = {'NVDA.US': {'qty': -10.0, 'total_cost': -500.0, 'currency': 'USD'}}
        lines = render_report(agg)
        hdr = self._header_idx(lines)
        self.assertIn('50.0000', lines[hdr + 2])

    def test_option_cost_per_share_divided_by_contract_multiplier(self):
        """Options trade in 100-share contracts but cost/share is quoted
        per underlying share. 12 contracts at total $23524.07 → per-contract
        $1960.34, per-underlying-share $19.60."""
        agg = {
            'TLT280121C00075000.US': {
                'qty': 12.0, 'total_cost': 23524.07, 'currency': 'USD',
            },
        }
        lines = render_report(agg)
        hdr = self._header_idx(lines)
        row = lines[hdr + 2]
        self.assertIn('19.6034', row)
        # Total cost stays as actual dollars paid.
        self.assertIn('23524.07', row)

    def test_short_option_cost_per_share(self):
        """Short -2 contracts, total_cost=-1000 (proceeds credited).
        Per contract = +500; per underlying share = +5.00."""
        agg = {
            'AAPL250620C00190000.US': {
                'qty': -2.0, 'total_cost': -1000.0, 'currency': 'USD',
            },
        }
        lines = render_report(agg)
        hdr = self._header_idx(lines)
        self.assertIn('5.0000', lines[hdr + 2])

    def test_zero_qty_filtered_out(self):
        """A pool with effectively zero qty shouldn't render even if it leaked in."""
        agg = {
            'ZERO.US': {'qty': 0.0, 'total_cost': 0.0, 'currency': 'USD'},
            'AAPL.US': {'qty': 100.0, 'total_cost': 15000.0, 'currency': 'USD'},
        }
        lines = render_report(agg)
        hdr = self._header_idx(lines)
        body = "\n".join(lines[hdr + 2:])
        self.assertNotIn('ZERO.US', body)
        self.assertIn('AAPL.US', body)

    def test_empty_agg_returns_no_lines(self):
        self.assertEqual(render_report({}), [])


class TestProcessDataReport(unittest.TestCase):
    def test_basic_aggregation_single_file(self):
        agg = {}
        process_data_report(_input([
            ('AAPL.US', 100, 15000, 'USD'),
            ('SHOP.TO', 50, 5000, 'CAD'),
        ]), _args(), agg)
        self.assertEqual(set(agg.keys()), {'AAPL.US', 'SHOP.TO'})
        self.assertAlmostEqual(agg['AAPL.US']['qty'], 100)
        self.assertAlmostEqual(agg['AAPL.US']['total_cost'], 15000)
        self.assertEqual(agg['AAPL.US']['currency'], 'USD')

    def test_multi_file_aggregation_sums_qty_and_cost(self):
        """Same ticker in two input files: qty and total_cost should sum."""
        agg = {}
        process_data_report(_input([('AAPL.US', 100, 15000, 'USD')]), _args(), agg)
        process_data_report(_input([('AAPL.US', 50, 8000, 'USD')]), _args(), agg)
        self.assertAlmostEqual(agg['AAPL.US']['qty'], 150)
        self.assertAlmostEqual(agg['AAPL.US']['total_cost'], 23000)

    def test_long_filter(self):
        agg = {}
        process_data_report(_input([
            ('AAPL.US', 100, 15000, 'USD'),
            ('NVDA.US', -10, -500, 'USD'),  # short
        ]), _args(long=True), agg)
        self.assertEqual(set(agg.keys()), {'AAPL.US'})

    def test_short_filter(self):
        agg = {}
        process_data_report(_input([
            ('AAPL.US', 100, 15000, 'USD'),
            ('NVDA.US', -10, -500, 'USD'),
        ]), _args(short=True), agg)
        self.assertEqual(set(agg.keys()), {'NVDA.US'})

    def test_no_cad_filter(self):
        agg = {}
        process_data_report(_input([
            ('AAPL.US', 100, 15000, 'USD'),
            ('SHOP.TO', 50, 5000, 'CAD'),
        ]), _args(no_cad=True), agg)
        self.assertEqual(set(agg.keys()), {'AAPL.US'})

    def test_missing_currency_tolerated(self):
        """Inventory entries without 'currency' (e.g. from USA engine) still work."""
        agg = {}
        data = {"inventory": [
            {"symbol": "AAPL.US", "qty": 100, "total_cost": 15000},
        ]}
        process_data_report(data, _args(), agg)
        self.assertEqual(agg['AAPL.US']['currency'], '')


if __name__ == '__main__':
    unittest.main()


class TestMixedCurrencyBuckets(unittest.TestCase):
    def test_journal_fold_blanks_flat_cost_keeps_per_currency(self):
        import io
        from contextlib import redirect_stderr
        from taxjson.bin.taxjson_export import process_data_report
        import argparse
        agg = {}
        args = argparse.Namespace(no_options=False, no_equities=False,
                                  no_cad=False, no_usd=False,
                                  short=False, long=False, inputs=["x"])
        data = {"inventory": [
            {"symbol": "DLR.US", "qty": 100, "total_cost": 1000.0,
             "currency": "USD"},
            {"symbol": "DLR.TO", "qty": 100, "total_cost": 1400.0,
             "currency": "CAD"}]}
        buf = io.StringIO()
        with redirect_stderr(buf):
            process_data_report(data, args, agg,
                                mapping={"DLR.US": "DLR.TO"},
                                drops=set())
        b = agg["DLR.TO"]
        self.assertTrue(b.get("mixed_currency"))
        self.assertAlmostEqual(b["cost_by_currency"]["USD"], 1000.0)
        self.assertAlmostEqual(b["cost_by_currency"]["CAD"], 1400.0)
        self.assertIn("omitted", buf.getvalue())
