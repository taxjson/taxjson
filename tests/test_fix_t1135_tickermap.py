"""t1135.map becomes a ticker.map keyword (owner decision).

- `T1135 SYMBOL COUNTRY` lines in ticker.map are the T1135 domicile
  overrides (COUNTRY: an ISO 3166 alpha-3 code, or CA/CAN/CANADA/EXCLUDE
  for "not foreign property" — the old t1135.map vocabulary). taxjson-t1135
  `--map` and `taxjson t1135` read them from ticker.map.
- A T1135 line that cannot be read (bad country, wrong shape, the same
  symbol given two countries) is a ticker.map problem: `taxjson run`
  refuses the map and taxjson-t1135 stops naming it — never a silent
  fallback to the listing suffix.
- t1135.map is an old per-purpose file: every command stops while it is
  in the project; `taxjson migrate` turns it into T1135 lines (with the
  old loader's rules) and renames it t1135.map.migrated. The migrated
  project's t1135 output is identical to one written with T1135 lines.

Synthetic data only.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli

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


def _load(text: str, name: str = "ticker.map"):
    from taxjson.bin.taxjson_t1135 import load_overrides
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text)
        return load_overrides(p)


class TestT1135Lines(unittest.TestCase):

    @rule("CA-RPT-02")
    def test_lines_read_with_the_old_vocabulary(self):
        from taxjson.lib.ticker_map import SIDE_KEYWORDS
        self.assertIn("T1135", SIDE_KEYWORDS)
        ov = _load("# overrides\n"
                   "T1135 ENB.US CA      # Canadian corp on NYSE\n"
                   "t1135 glxy.to usa\n"
                   "T1135 BTC EXCLUDE\n"
                   "T1135 QZA.US CANADA\n"
                   "T1135 QZB.US CAN\n"
                   "QUOTE PNG.TO PNG.V\n"
                   "GLOBAL FB.US META.US\n")
        self.assertEqual(ov, {"ENB.US": None, "GLXY.TO": "USA", "BTC": None,
                              "QZA.US": None, "QZB.US": None})

    def test_bom_and_no_lines(self):
        from taxjson.bin.taxjson_t1135 import load_overrides
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_bytes("﻿T1135 XYZ.TO USA\n".encode("utf-8"))
            self.assertEqual(load_overrides(p), {"XYZ.TO": "USA"})
            p.write_text("QUOTE PNG.TO PNG.V\n")
            self.assertEqual(load_overrides(p), {})
        self.assertEqual(load_overrides(None), {})

    def test_other_readers_ignore_t1135_lines(self):
        from taxjson.lib.price_chain import load_yf_map
        from taxjson.bin.taxjson_ticker_map import map_file_problems
        from taxjson.lib.ticker_map import read_side_rules
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text("T1135 ENB.US CA\nQUOTE PNG.TO PNG.V\n")
            self.assertEqual(map_file_problems(p), [])
            self.assertEqual(load_yf_map([td]), {"PNG.TO": ("PNG.V", 1.0)})
            side = read_side_rules(p)
            self.assertEqual(side.problems, [])
            self.assertEqual(side.t1135, {"ENB.US": "CA"})
            self.assertFalse(side.empty())

    @rule("CA-RPT-02")
    def test_bad_lines_are_map_problems_and_stop_t1135(self):
        from taxjson.bin.taxjson_ticker_map import map_file_problems
        from taxjson.lib.cli_diag import InputContentError
        for text, want in (("T1135 ENB.US EXCLUDED\n", "did you mean"),
                           ("T1135 ENB.US CDN\n", "ISO 3166"),
                           ("T1135 ENB.US\n", "T1135 SYMBOL COUNTRY"),
                           ("T1135 ENB.US CA\nT1135 enb.us USA\n",
                            "given twice"),
                           # An old t1135.map line pasted in as is.
                           ("ENB.US CA\n", "T1135 SYMBOL COUNTRY")):
            with self.subTest(text=text):
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "ticker.map"
                    p.write_text(text)
                    self.assertTrue(map_file_problems(p))
                with self.assertRaises(InputContentError) as cm:
                    _load(text)
                self.assertIn("ticker.map", str(cm.exception))
                self.assertIn(want, str(cm.exception))

    def test_other_lookup_problems_do_not_stop_t1135(self):
        # A bad QUOTE line is `taxjson run`'s to refuse; T1135 reads on.
        self.assertEqual(_load("QUOTE PNG.TO\nT1135 ENB.US CA\n"),
                         {"ENB.US": None})

    def test_unused_override_warning_names_the_t1135_line(self):
        from taxjson.bin.taxjson_t1135 import build_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": []}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                build_report([base], [], 2025, {"NOPE.US": "USA"}, "CAD",
                             full_history=False)
        self.assertIn("T1135 NOPE.US", err.getvalue())
        self.assertNotIn("t1135.map", err.getvalue())


class TestProject(unittest.TestCase):

    @rule("CA-RPT-02")
    def test_taxjson_t1135_reads_ticker_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            before = json.loads(cli(root, "t1135", "--json").stdout)
            (root / "ticker.map").write_text("T1135 QZT.TO USA\n")
            r = cli(root, "t1135", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            after = json.loads(r.stdout)
            (root / "ticker.map").write_text("T1135 QZT.TO USAA\n")
            bad = cli(root, "t1135", "--json")
        self.assertFalse(before["filing_required"])
        self.assertTrue(after["filing_required"])
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("USAA", bad.stderr)
        self.assertNotIn("Traceback", bad.stderr)

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
                    self.assertIn("taxjson migrate", r.stderr)

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

    @rule("CA-RPT-02")
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
