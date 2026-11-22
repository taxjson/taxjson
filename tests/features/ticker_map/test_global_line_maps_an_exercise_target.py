"""Parser, import and dedup fixes of fix round 7 (wave C, group
parsers2): RBC holdings with a CASH ticker, dotted preferred series L
and V, the generic importer's fee-only and zero-net rows and its named
brokers, a Webull class-share MULT, a mapped warrant exercise target
and dedup keeping a copy's income facts. Every fixture is SYNTHETIC:
fake account ids (99900001, U1234567), invented tickers.

Formerly: test_fix_1029_r7c_parsers2
"""
import json
import tempfile
import unittest

from _qa_project import project, tj


def _totals(root):
    """`tjs sum --json`'s filing totals of a project."""
    return json.loads(tj(root, "sum", "--json").stdout)["filing"]["totals"]


def _toml_2025(root):
    """The project's year set to 2025 (the fixtures' trades)."""
    p = root / "taxjson.toml"
    p.write_text(p.read_text().replace("year = 2024", "year = 2025")
                 .replace("option_grant_timing_since = 2024",
                          "option_grant_timing_since = 2025"))


class TestMappedWarrantTarget(unittest.TestCase):
    """TJ-0978: a GLOBAL rename maps a warrant exercise's target with
    the share rows.

    Formerly: test_fix_1029_r7c_parsers2.TestMappedWarrantTarget"""

    def test_apply_mapping_maps_exercise_of(self):
        from taxjson.bin.taxjson_ticker_map import apply_mapping
        from taxjson.model.transaction import TaxTransaction
        tx = TaxTransaction(action="ASSIGN", date="2025-06-02",
                            time="10:00:00", symbol="SYN.WT.TO",
                            quantity=-10, currency="CAD", price=0,
                            net_amount=0, gross_amount=0, account="margin",
                            exercise_of="SYN.TO")
        apply_mapping(tx, {"SYN.TO": "NEW.TO"})
        self.assertEqual(tx.exercise_of, "NEW.TO")

    def test_run_realizes_no_loss_on_exercise(self):
        tt = ("BUYSELL 2025-01-02 10:00:00 SYN.WT.TO 10 CAD 1 10 0\n"
              "ASSIGN 2025-06-02 10:00:00 SYN.WT.TO -10 CAD 0 0 "
              "exercise_of=SYN.TO\n"
              "BUYSELL 2025-06-02 10:00:00 SYN.TO 10 CAD 5 50 0\n"
              "BUYSELL 2026-09-02 10:00:00 SYN.TO -10 CAD 10 100 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/book.tt": tt},
                           ticker_map="GLOBAL SYN.TO NEW.TO\n")
            _toml_2025(root)
            tj(root, "run", "--strict", "--no-input")
            t = _totals(root)
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
