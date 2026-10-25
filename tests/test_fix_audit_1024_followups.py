"""Follow-ups of the 2026-10-24 audit fixes that cross two groups' files.

B21 made taxjson-export write `contract_multiplier_unknown = true` for an
option whose inputs disagree on the contract size. The holdings reader
`tjs sanity` uses (lib/positions_reports.py, the same TOML shape) must
read that key back as "unknown" rather than assume the equity 100.
"""
import tempfile
import unittest
from pathlib import Path

OPT = "ZZQ260918C00010000.US"


class TestUnknownMultiplierReadBack(unittest.TestCase):

    def _write(self, p, body):
        p.write_text('[meta]\nas_of="2026-01-02"\n[[holding]]\n'
                     f'symbol="{OPT}"\nquantity=1\ncurrency="USD"\n' + body)

    def test_unknown_size_derives_no_cost_from_a_per_share_price(self):
        """B21 follow-up: a per-share price times an unknown contract size
        is no cost (it used to be the price times 100)."""
        from taxjson.lib import positions_reports as pr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "holdings.toml"
            self._write(p, 'average_entry_price=2\n'
                           'contract_multiplier_unknown = true\n')
            row = pr.read_positions(p).rows[0]
            self.assertIsNone(row.cost)
            self.assertEqual(row.multiplier, 0.0)
            # A stated total still reads: the size does not enter it.
            self._write(p, 'total_cost=150\n'
                           'contract_multiplier_unknown = true\n')
            self.assertEqual(pr.read_positions(p).rows[0].cost, 150.0)
            # `false` is the known state: the equity 100 as before.
            self._write(p, 'average_entry_price=2\n'
                           'contract_multiplier_unknown = false\n')
            self.assertEqual(pr.read_positions(p).rows[0].cost, 200.0)

    def test_malformed_or_contradictory_key_is_refused(self):
        """B21 follow-up: the key is true or false, and never beside a
        declared contract size."""
        from taxjson.lib import positions_reports as pr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "holdings.toml"
            self._write(p, 'contract_multiplier_unknown = "yes"\n')
            with self.assertRaisesRegex(pr.PositionsReportError,
                                        "contract_multiplier_unknown "
                                        "'yes' is not true or false"):
                pr.read_positions(p)
            self._write(p, 'contract_multiplier = 50\n'
                           'contract_multiplier_unknown = true\n')
            with self.assertRaisesRegex(pr.PositionsReportError,
                                        "beside a contract size of 50"):
                pr.read_positions(p)


if __name__ == "__main__":
    unittest.main()
