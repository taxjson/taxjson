"""Audit 2026-10-24, group "inkind": B20, B43, B05, B16, A13, B36, B37,
B14 — in-kind moves, the corporate views, the holdings folder, the
holdings TOML reader and the [[distributions]] ADJUST. Synthetic data
only.

Formerly: test_fix_audit_1024_inkind"""
import tempfile
import unittest
from pathlib import Path

OPT = "ZZQ270115C00010000.US"


class TestHoldingsToml(unittest.TestCase):
    """Formerly: test_fix_audit_1024_inkind.TestHoldingsToml"""

    def test_b36_nonpositive_contract_multiplier_is_refused(self):
        """B36: Holdings import accepts a negative contract multiplier
        and creates negative cost — refused, naming the row."""
        from taxjson.lib import positions_reports as pr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "holdings.toml"

            def write(key, value):
                p.write_text('[meta]\nas_of="2026-01-02"\n[[holding]]\n'
                             f'symbol="{OPT}"\nquantity=1\ncurrency="USD"\n'
                             f'average_entry_price=2\n{key}={value}\n')
            for key in ("contract_multiplier", "multiplier"):
                for bad in ("-50", "0"):
                    write(key, bad)
                    with self.assertRaisesRegex(
                            pr.PositionsReportError,
                            f"{key} {bad} is not a positive number"):
                        pr.read_positions(p)
            write("contract_multiplier", "50")
            self.assertEqual(pr.read_positions(p).rows[0].cost, 100.0)

    def test_b37_declared_holdings_count_must_match_the_tables(self):
        """B37: Holdings import ignores a declared holding count larger
        than the rows present — a file with fewer (or more) [[holding]]
        tables than its `holdings_count` is refused as incomplete."""
        from taxjson.lib import positions_reports as pr
        one = '[[holding]]\nsymbol="ZZQ.US"\nquantity=1\n'
        two = one + '[[holding]]\nsymbol="ZZR.US"\nquantity=2\n'
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "holdings.toml"
            for count, body in ((2, one), (1, two), (0, one)):
                p.write_text(f'[meta]\nholdings_count={count}\n'
                             f'as_of="2026-01-02"\n{body}')
                with self.assertRaisesRegex(
                        pr.PositionsReportError,
                        f"holdings_count = {count} but the file has "
                        f"{body.count('[[holding]]')} "):
                    pr.read_positions(p)
            p.write_text('[meta]\nholdings_count=2\nas_of="2026-01-02"\n'
                         + two)
            self.assertEqual(len(pr.read_positions(p).rows), 2)
            p.write_text('[meta]\nas_of="2026-01-02"\n' + one)
            self.assertEqual(len(pr.read_positions(p).rows), 1)


if __name__ == "__main__":
    unittest.main()
