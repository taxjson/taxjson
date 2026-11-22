"""t1135.map becomes a ticker.map keyword (owner decision).

- `T1135 SYMBOL COUNTRY` lines in ticker.map are the T1135 domicile
  overrides (COUNTRY: an ISO 3166 alpha-3 code, or CA/CAN/CANADA/EXCLUDE
  for "not foreign property" — the old t1135.map vocabulary). taxjson-t1135
  `--map` and `tjs t1135` read them from ticker.map.
- A T1135 line that cannot be read (bad country, wrong shape, the same
  symbol given two countries) is a ticker.map problem: `tjs run`
  refuses the map and taxjson-t1135 stops naming it — never a silent
  fallback to the listing suffix.
- t1135.map is an old per-purpose file: every command stops while it is
  in the project; `tjs migrate` turns it into T1135 lines (with the
  old loader's rules) and renames it t1135.map.migrated. The migrated
  project's t1135 output is identical to one written with T1135 lines.

Synthetic data only.

Formerly: test_fix_t1135_tickermap
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli
from taxjson.rules import ids as R

ACCT = "55500001"  # pii-ok

_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type\n")
# 100 units at 1,500 = 150,000 CAD: foreign property above the threshold
# only when the override calls the TSX listing a US company.
_ROWS = [
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,QZT.TO,QZT CORP,"
    "100,1500.00,-150000.00,0.00,-150000.00,CAD,{a},Trades,"
    "Individual margin",
    "2025-02-10 09:30:00 AM,2025-02-11 12:00:00 AM,Buy,QZC.TO,QZC CORP,"
    "10,10.00,-100.00,0.00,-100.00,CAD,{a},Trades,Individual margin",
]
_TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\nprovince = "ON"\n'
         'option_grant_timing_since = 2025\n\n'
         '[accounts.margin]\ntype = "taxable"\n')


def _project(root: Path) -> Path:
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT + "\n".join(r.format(a=ACCT) for r in _ROWS) + "\n")
    (root / "taxjson.toml").write_text(_TOML)
    return root


class TestProject(unittest.TestCase):
    """Formerly: test_fix_t1135_tickermap.TestProject"""

    @rule(R.CA_RPT_02)
    def test_taxjson_t1135_reads_ticker_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            before = json.loads(cli(root, "t1135", "--json").stdout)
            # ticker.map is an input: t1135 reads books a full run built
            # from it (TJ-4347).
            (root / "ticker.map").write_text("T1135 QZT.TO USA\n")
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            r = cli(root, "t1135", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            after = json.loads(r.stdout)
            (root / "ticker.map").write_text("T1135 QZT.TO USAA\n")
            # The run refuses the line first, naming it; t1135 then refuses
            # the books that run did not finish.
            bad = cli(root, "run", "--no-input")
            after_bad = cli(root, "t1135", "--json")
        self.assertFalse(before["filing_required"])
        self.assertTrue(after["filing_required"])
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("USAA", bad.stderr)
        self.assertNotIn("Traceback", bad.stderr)
        self.assertEqual(after_bad.returncode, 1, after_bad.stdout)
        self.assertIn("`tjs run`", after_bad.stderr)

    def test_old_file_stops_every_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            (root / "t1135.map").write_text("QZT.TO USA\n")
            for args in (("run", "--no-input"), ("t1135",), ("sum",)):
                with self.subTest(args=args):
                    r = cli(root, *args)
                    self.assertEqual(r.returncode, 2, r.stderr[-2000:])
                    self.assertIn("t1135.map", r.stderr)
                    self.assertIn("T1135", r.stderr)
                    self.assertIn("tjs migrate", r.stderr)

    def test_migrate_converts_with_the_old_rules(self):
        from taxjson.bin.taxjson_t1135 import load_overrides
        old = ("﻿# domicile overrides\n"
               "ENB.US  CA   # Canadian corp on NYSE\n"
               "glxy.to usa\n"
               "BTC EXCLUDE\n"
               "malformed-line-here\n"
               "QZX.US EXCLUDED\n"
               "QZY.US USA\n"
               "QZY.US GBR   # the later line won\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            (root / "ticker.map").write_text("GLOBAL FB.US META.US\n")
            (root / "t1135.map").write_text(old)
            dry = cli(root, "migrate", "--dry-run")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertTrue((root / "t1135.map").exists())
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            tm = (root / "ticker.map").read_text()
            names = sorted(p.name for p in root.iterdir())
            ov = load_overrides(root / "ticker.map")
        self.assertIn("t1135.map -> ticker.map: 4 T1135 line(s)", r.stdout)
        self.assertIn("t1135.map.migrated", names)
        self.assertNotIn("t1135.map", names)
        self.assertIn("GLOBAL FB.US META.US", tm)
        self.assertIn("T1135 ENB.US CA  # Canadian corp on NYSE", tm)
        self.assertIn("# domicile overrides", tm)
        # What the old loader read, no more, no less.
        self.assertEqual(ov, {"ENB.US": None, "GLXY.TO": "USA", "BTC": None,
                              "QZY.US": "GBR"})
        for skipped in ("malformed-line-here", "QZX.US"):
            self.assertIn(skipped, r.stdout)

    @rule(R.CA_RPT_02)
    def test_round_trip_gives_identical_t1135_output(self):
        with tempfile.TemporaryDirectory() as td:
            old = _project(Path(td) / "old")
            new = _project(Path(td) / "new")
            (old / "t1135.map").write_text("QZT.TO USA  # US company\n"
                                           "QZC.TO GBR\n")
            (new / "ticker.map").write_text("T1135 QZT.TO USA\n"
                                            "T1135 QZC.TO GBR\n")
            r = cli(old, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            outs = {}
            for root in (old, new):
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                outs[root.name] = (cli(root, "t1135", "--json").stdout,
                                   cli(root, "t1135").stdout)
        self.assertEqual(outs["old"], outs["new"])
        rep = json.loads(outs["new"][0])
        self.assertTrue(rep["filing_required"])

    def test_init_template_documents_the_keyword(self):
        from taxjson.lib.ticker_map_format import init_template
        self.assertIn("T1135", init_template())


if __name__ == "__main__":
    unittest.main()
