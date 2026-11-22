"""The 2026-10 result and formatting audit: ten result-changing defects
(R01-R10) and three formatting ones (F01-F03), each pinned here on
synthetic books. R01 changes a filing figure (CA-CORP-13 / US-CORP-14);
the rest are the analytics and planning views.

Formerly: test_fix_result_audit_2026_10"""
import unittest

from taxjson.lib import tax_plot as TP


class TestPlotTaxFormatting(unittest.TestCase):
    """Formerly: test_fix_result_audit_2026_10.TestPlotTaxFormatting"""
    def _rows(self, taxes):
        pts, ct = [], 0.0
        for i, t in enumerate(taxes):
            ct += t
            pts.append({"month": f"2025-{i + 1:02d}", "cum_tax": ct,
                        "cum_income": ct * 4})
        return TP.build(pts, year=2025, base="USD", year_estimate=ct,
                        year_income=ct * 4, label="usa", accounts=[])["rows"]

    def test_F01_cent_sized_levels_are_labelled_in_cents(self):
        lines = TP.chart(self._rows([0.04] * 12), 0.48, 120)
        labels = [ln.split("|")[0].strip() for ln in lines[1:]
                  if "|" in ln]
        self.assertNotIn("0", labels)
        self.assertIn("0.04", labels)

    def test_F02_fits_a_60_column_terminal(self):
        rows = self._rows([10.0] * 12)
        for w in (60, 48, 40):
            lines = TP.chart(rows, 120.0, w)
            self.assertTrue(all(len(ln) <= w for ln in lines),
                            (w, max(len(ln) for ln in lines)))
            self.assertEqual(len(lines[-1].split()), 12)


if __name__ == '__main__':
    unittest.main()
