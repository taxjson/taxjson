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
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R


def _load(text: str, name: str = "ticker.map"):
    from taxjson.bin.taxjson_t1135 import load_overrides
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text)
        return load_overrides(p)


class TestT1135Lines(unittest.TestCase):
    """Formerly: test_fix_t1135_tickermap.TestT1135Lines"""

    @rule(R.CA_RPT_02)
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
        from taxjson.lib.map_file import map_file_problems
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

    @rule(R.CA_RPT_02)
    def test_bad_lines_are_map_problems_and_stop_t1135(self):
        from taxjson.lib.map_file import map_file_problems
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
        # A bad QUOTE line is `tjs run`'s to refuse; T1135 reads on.
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


if __name__ == "__main__":
    unittest.main()
