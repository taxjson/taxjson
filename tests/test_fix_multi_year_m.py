"""One folder of exports for every year: pre-release review M3, M4, M5.

Synthetic data only (.tt lines, made-up tickers and broker ids).

- M3: `exports_dir` overlapping the inputs, the holdings folder, a year
  folder, the project's work/ reports/ filed/ or the project itself is
  refused; the newest year's export removes only the files the previous
  export wrote (its manifest), never a file someone else put there.
- M4: `check-dates` in a year folder reading shared exports: rows of
  later years are one Info count, not out-of-range errors; a
  single-folder project still reports them.
- M5: the holdings/ discovery counts a file an account lists in its own
  `holdings = [...]` as claimed (no false "no account claims it").
"""
import contextlib
import io
import json
import unittest
from pathlib import Path

from _tmpfiles import private_dir
from test_fix_multi_year import multi, run_ok, tjs

from taxjson.lib import project_layout as PL


def _year(top: Path, y: int, **settings) -> Path:
    d = top / str(y)
    d.mkdir(parents=True, exist_ok=True)
    lines = ["[settings]", f"year = {y}", 'country = "canada"',
             'inputs_dir = "../inputs"']
    lines += [f"{k} = {json.dumps(v)}" for k, v in settings.items()]
    lines += ["", "[accounts.margin]", 'type = "taxable"', ""]
    (d / "taxjson.toml").write_text("\n".join(lines))
    return d


class TestExportsDirOverlap(unittest.TestCase):
    """M3: the folder the newest year's run replaces files in must be
    a folder of its own."""

    def setUp(self):
        self.top = Path(private_dir()) / "taxes"
        (self.top / "inputs" / "margin").mkdir(parents=True)
        (self.top / "2024").mkdir()
        (self.top / "2024" / "taxjson.toml").write_text(
            "[settings]\nyear = 2024\n")
        self.y = _year(self.top, 2025)

    def _probs(self, **st):
        base = {"year": 2025, "inputs_dir": "../inputs"}
        base.update(st)
        return [p for p in PL.setting_problems(self.y, base)
                if "exports_dir" in p]

    def test_overlaps_are_refused(self):
        cases = {
            "../inputs": "inputs folder",
            "../inputs/margin": "inputs folder",
            "../2024": "year folder 2024",
            "../2031": "year folder 2031",
            "work": "work/",
            "reports/x": "reports/",
            "filed": "filed/",
            "inputs/slips": "inputs/",
            "out": "this project's folder",
            "holdings": "holdings",
        }
        for v, what in cases.items():
            with self.subTest(exports_dir=v):
                probs = self._probs(exports_dir=v)
                self.assertEqual(len(probs), 1, probs)
                self.assertIn("overlaps", probs[0])
                self.assertIn(what, probs[0])

    def test_a_folder_holding_the_inputs_or_holdings_is_refused(self):
        (self.top / "shared" / "inputs").mkdir(parents=True)
        probs = self._probs(inputs_dir="../shared/inputs",
                            exports_dir="../shared")
        self.assertTrue(probs and "inputs folder" in probs[0], probs)
        probs = self._probs(holdings_dir="../snap",
                            exports_dir="../snap")
        self.assertTrue(probs and "holdings folder" in probs[0], probs)

    def test_a_folder_of_its_own_is_fine(self):
        self.assertEqual(self._probs(exports_dir="../exports"), [])
        self.assertEqual(self._probs(), [])

    def test_the_run_refuses_it_naming_the_key(self):
        _year(self.top, 2025, exports_dir="../inputs")
        r = tjs("-C", str(self.y), "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exports_dir", r.stderr)
        self.assertIn("overlaps the inputs folder", r.stderr)


class TestExportsManifest(unittest.TestCase):
    """M3: only the files the previous export wrote are removed."""

    def test_only_the_previous_exports_files_are_replaced(self):
        from taxjson.bin import taxjson_run as R
        top = Path(private_dir()) / "taxes"
        (top / "inputs" / "margin").mkdir(parents=True)
        y = _year(top, 2025, exports_dir="../exports")
        rep = y / "reports"
        rep.mkdir()
        (rep / "margin_holdings.toml").write_text("a\n")
        (rep / "wash_radar_margin.json").write_text("{}\n")
        ex = top / "exports"
        ex.mkdir()
        # Files someone else put there, one matching the export's names.
        (ex / "broker_holdings.toml").write_text("mine\n")
        (ex / "notes.txt").write_text("mine\n")
        settings = {"year": 2025, "inputs_dir": "../inputs",
                    "exports_dir": "../exports"}
        with contextlib.redirect_stdout(io.StringIO()):
            R._write_exports(y, settings, rep)
        self.assertEqual((ex / "margin_holdings.toml").read_text(), "a\n")
        man = json.loads((ex / ".taxjson-exports.json").read_text())
        self.assertEqual(man["files"], ["README.txt", "margin_holdings.toml",
                                        "wash_radar_margin.json"])
        # The next export no longer writes the radar: it goes, nothing
        # else does.
        (rep / "wash_radar_margin.json").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            R._write_exports(y, settings, rep)
        self.assertFalse((ex / "wash_radar_margin.json").exists())
        self.assertTrue((ex / "margin_holdings.toml").is_file())
        self.assertEqual((ex / "broker_holdings.toml").read_text(), "mine\n")
        self.assertEqual((ex / "notes.txt").read_text(), "mine\n")

    def test_a_manifest_naming_another_folder_deletes_nothing_there(self):
        from taxjson.bin import taxjson_run as R
        top = Path(private_dir()) / "taxes"
        (top / "inputs" / "margin").mkdir(parents=True)
        keep = top / "inputs" / "margin" / "t.tt"
        keep.write_text("# kept\n")
        y = _year(top, 2025, exports_dir="../exports")
        rep = y / "reports"
        rep.mkdir()
        ex = top / "exports"
        ex.mkdir()
        (ex / ".taxjson-exports.json").write_text(json.dumps(
            {"files": ["../inputs/margin/t.tt", "/etc/hosts"]}))
        with contextlib.redirect_stdout(io.StringIO()):
            R._write_exports(y, {"year": 2025, "inputs_dir": "../inputs",
                                 "exports_dir": "../exports"}, rep)
        self.assertTrue(keep.is_file())


_DATES_TT = """\
# synthetic
BUYSELL 2024-03-06 10:00:00 QZQ.TO 10 CAD 20.00 209.95 9.95
BUYSELL 2026-03-04 10:00:00 QZQ.TO -5 CAD 22.00 100.05 9.95
BUYSELL 2026-03-11 10:00:00 QZQ.TO -5 CAD 23.00 105.05 9.95
"""


class TestCheckDatesLaterYears(unittest.TestCase):
    """M4: a year folder reading shared exports sees later years' rows."""

    def test_shared_layout_counts_later_rows_as_info(self):
        top = Path(private_dir()) / "taxes"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "t.tt").write_text(_DATES_TT)
        y = _year(top, 2024)
        run_ok(self, y)
        r = tjs("-C", str(y), "check-dates", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["errors"], 0, doc["counts"])
        self.assertEqual(doc["later_years"], 2)
        # (the later-years note: --details, docs/output-style.md)
        r = tjs("-C", str(y), "check-dates", "--details")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("Info: 2 row(s) dated after 2025", r.stdout)

    def test_single_folder_still_reports_them(self):
        root = Path(private_dir()) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "t.tt").write_text(_DATES_TT)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n\n'
            '[accounts.margin]\ntype = "taxable"\n')
        run_ok(self, root)
        r = tjs("-C", str(root), "check-dates", "--json")
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["counts"]["ERROR"].get("out-of-range"), 2)
        self.assertEqual(doc["later_years"], 0)


class TestHoldingsListedByAnAccount(unittest.TestCase):
    """M5: a file an account's own `holdings = [...]` lists is claimed."""

    def test_discover_skips_files_an_account_lists(self):
        from taxjson.lib import holdings_dir as HD
        root = Path(private_dir()) / "p"
        h = root / "holdings"
        h.mkdir(parents=True)
        for n in ("U5550001_positions.toml", "tfsa_x.toml",  # pii-ok
                  "stray.toml"):
            (h / n).write_text('[[holding]]\nsymbol = "QZQ.TO"\n'
                               'quantity = 1\ncurrency = "CAD"\n')
        accounts = {
            "margin": {"type": "taxable",
                       "holdings": ["holdings/U5550001_positions.toml"]},  # pii-ok
            "tfsa": {"type": "sheltered"},
        }
        found, notes = HD.discover(h, accounts, root)
        self.assertEqual(found, {"tfsa": [str(h / "tfsa_x.toml")]})
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("stray.toml: no account claims it", notes[0])

    def test_sanity_names_no_false_unclaimed_file(self):
        top = multi("canada", years=(2025,))
        y = top / "2025"
        (y / "holdings").mkdir()
        (y / "holdings" / "ib_U5550001.toml").write_text(  # pii-ok
            '[meta]\nas_of = "2025-12-31"\n\n'
            '[[holding]]\nsymbol = "QZQ.TO"\nquantity = 0\n'
            'currency = "CAD"\n')
        cfg = y / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            '[accounts.margin]\ntype = "taxable"',
            '[accounts.margin]\ntype = "taxable"\n'
            'holdings = ["holdings/ib_U5550001.toml"]'))  # pii-ok
        run_ok(self, y)
        r = tjs("-C", str(y), "sanity", "--json")
        self.assertIn(r.returncode, (0, 1), r.stderr)
        doc = json.loads(r.stdout)
        self.assertNotIn("no account claims it", " ".join(doc["notes"])
                         + r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
