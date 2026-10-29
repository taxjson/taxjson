"""Backlog fix round 6 of 2026-10-29, the `redact` group: `taxjson
redact` of a year folder bundles the close-year record its estimate
carries from (prior/<name>, backlog TJ-0581); the record's folder paths
of this computer are redacted too, and `--check` of a copy names one
left in it (backlog TJ-0700).

Synthetic data only (the person "Casey Example", made-up folders, the
ticker SYN.TO).
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_redact as R
from taxjson.lib import project_layout as PL
from taxjson.lib.dispatch import python_module_argv

CFG = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
       'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
       'province = "ON"\n[accounts.margin]\ntype = "taxable"\n')
TT = ("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 30 300 0\n"
      "BUYSELL 2024-02-05 10:00:00 SYN.TO -10 CAD 10 100 0\n"
      "BUYSELL 2025-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
      "BUYSELL 2025-02-03 10:00:00 SYN.TO -10 CAD 20 200 0\n")
EST = '\n[estimate]\nother_income = 400\n'
PERSON = "Casey Example"


def tjs(root, *args):
    """`taxjson -C root <args>`, captured at width 0, offline."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=root, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


def two_years(top: Path) -> Path:
    """The audit's layout: the exports in top/Casey Example/inputs
    (top/inputs a link to it); 2024 names that folder by its absolute
    path and is closed with a 200 loss, which 2025 (reading ../inputs)
    carries in by its prior_year_record. The 2025 year folder."""
    shared = top / PERSON / "inputs"
    (shared / "margin").mkdir(parents=True)
    (shared / "margin" / "trades.tt").write_text(TT)
    (top / "inputs").symlink_to(shared, target_is_directory=True)
    for year in (2024, 2025):
        p = top / str(year)
        p.mkdir()
        (p / "inputs").mkdir()
        (p / "ticker.map").write_text("")
        text = PL.set_key_text(CFG.format(year=year),
                               ("settings", "inputs_dir"),
                               str(shared) if year == 2024 else "../inputs")
        if year == 2025:
            text = PL.set_key_text(text, ("settings", "prior_year_record"),
                                   "../2024/filed/2024.json")
        (p / "taxjson.toml").write_text(text + EST)
        r = tjs(p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        if year == 2024:
            r = tjs(p, "close-year", "--yes")
            assert r.returncode == 0, r.stdout + r.stderr
    return p


def losses_opening(root: Path) -> float:
    r = tjs(root, "estimate", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)["estimate"]["losses_opening"]


def leaks(folder: Path, *words: str) -> list:
    """The copy's files holding any of `words`."""
    return sorted(f.relative_to(folder).as_posix()
                  for f in folder.rglob("*") if f.is_file()
                  and any(w in f.read_text(errors="replace") for w in words))


# ------------------------------------------------------------- TJ-0700

class TestTheBundledRecordHoldsNoFolderPath(unittest.TestCase):
    """The prior-year record TJ-0581 bundles into the copy kept the
    absolute folder path the 2024 project read its exports from (a
    person's name in it), and redact and the copy's `redact --check`
    both said there was nothing to redact."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.top = Path(cls._td.name)
        cls.year = two_years(cls.top)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy(self, name: str):
        dst = self.top / name
        r = tjs(self.year, "redact", "--no-denylist", "--out", str(dst))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return dst, r

    def test_the_copy_holds_no_folder_path_and_still_carries_the_loss(self):
        dst, r = self.copy("copy-clean")
        self.assertEqual(leaks(dst, PERSON, str(self.top)), [])
        self.assertIn("1 folder path of this computer", r.stdout)
        rec = json.loads((dst / "prior" / "2024.json").read_text())
        self.assertEqual(rec["frozen"]["inputs_dir"], R.PATH_PLACEHOLDER)
        # What the next year reads from it is kept (TJ-0581).
        self.assertEqual(rec["carryforwards"]["net_capital_loss"]
                         ["closing"], 200.0)
        self.assertEqual(rec["frozen"]["inputs"], json.loads(
            (self.year.parent / "2024" / "filed" / "2024.json")
            .read_text())["frozen"]["inputs"])
        # The copy checked again: nothing left to redact.
        r = tjs(dst, "redact", "--check", "--no-denylist")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Nothing to redact", r.stdout)
        # Its estimate still carries the loss in.
        r = tjs(dst, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(losses_opening(dst), 200.0)

    def test_check_names_a_folder_path_left_in_the_copy(self):
        # A copy an older taxjson wrote: the record keeps the path.
        dst, _r = self.copy("copy-old")
        rec = dst / "prior" / "2024.json"
        text = rec.read_text()
        lineno = next(n for n, ln in enumerate(text.split("\n"), start=1)
                      if R.PATH_PLACEHOLDER in ln)
        rec.write_text(text.replace(
            json.dumps(R.PATH_PLACEHOLDER),
            json.dumps(str(self.top / PERSON / "inputs"))))
        r = tjs(dst, "redact", "--check", "--no-denylist")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        out = " ".join((r.stdout + r.stderr).split())
        self.assertIn("1 folder path of this computer", out)
        self.assertIn(f"a folder path of this computer in the copy: "
                      f"prior/2024.json line {lineno}", out)
        self.assertNotIn("Nothing to redact", out)
        # --check writes nothing.
        self.assertIn(PERSON, rec.read_text())

    def test_check_of_the_project_counts_the_path(self):
        r = tjs(self.year, "redact", "--check", "--no-denylist")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("1 folder path of this computer", r.stdout)


def record(doc) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


class TestRecordPaths(unittest.TestCase):
    """redact_record_paths: each string value that names a folder of
    this computer (absolute, home, a drive, or a relative path out of
    the folder holding the years or through a name-like folder) is the
    placeholder; the rest of the record, its figures and its keys, is
    byte for byte the same."""

    DOC = {
        "carryforwards": {
            "net_capital_loss": {
                "closing": 200.0,
                "opening_source": f"/data/{PERSON}/2023/filed/2023.json "
                                  f"(the 2023 close-year record)"},
            "minimum_tax": {"closing_by_year": {"2023": 5.0},
                            "opening_source": "none"}},
        "dispositions": [{"account": "margin", "symbol": "/ESZ4"}],
        "filed_totals": {"source": "~/Downloads/filed.csv"},
        "frozen": {"inputs": {"inputs/margin/trades.tt": "0e5b"},
                   "inputs_dir": f"/data/{PERSON}/inputs",
                   "tobase_map": f"D:\\Taxes\\{PERSON}\\tobase.map"},
        "relative": {"beside": "../inputs",
                     "climbs": "../../taxes/inputs",
                     "named": f"../{PERSON}/inputs",
                     "own": "inputs/margin/trades.tt"},
    }

    def redact(self, doc):
        rep = R.Report()
        text = record(doc)
        return text, R.redact_record_paths(text, rep), rep

    def test_the_paths_are_the_placeholder(self):
        text, out, rep = self.redact(self.DOC)
        got = json.loads(out)
        ph = R.PATH_PLACEHOLDER
        self.assertEqual(got["carryforwards"]["net_capital_loss"]
                         ["opening_source"],
                         f"{ph} (the 2023 close-year record)")
        self.assertEqual(got["filed_totals"]["source"], ph)
        self.assertEqual(got["frozen"]["inputs_dir"], ph)
        self.assertEqual(got["frozen"]["tobase_map"], ph)
        self.assertEqual(got["relative"]["climbs"], ph)
        self.assertEqual(got["relative"]["named"], ph)
        self.assertNotIn(PERSON, out)
        self.assertEqual(len(rep.paths), 6)
        self.assertTrue(rep.found_anything())
        self.assertIn((6, "folder path(s) of this computer"),
                      rep.identity_counts())

    def test_the_rest_is_kept(self):
        text, out, _rep = self.redact(self.DOC)
        got = json.loads(out)
        self.assertEqual(got["carryforwards"]["minimum_tax"],
                         self.DOC["carryforwards"]["minimum_tax"])
        self.assertEqual(got["carryforwards"]["net_capital_loss"]
                         ["closing"], 200.0)
        self.assertEqual(got["dispositions"], self.DOC["dispositions"])
        self.assertEqual(got["frozen"]["inputs"],
                         self.DOC["frozen"]["inputs"])
        self.assertEqual(got["relative"]["beside"], "../inputs")
        self.assertEqual(got["relative"]["own"], "inputs/margin/trades.tt")
        # Line for line: only the lines holding a path changed.
        a, b = text.split("\n"), out.split("\n")
        self.assertEqual(len(a), len(b))
        changed = [n for n, (x, y) in enumerate(zip(a, b), start=1)
                   if x != y]
        _t, _o, rep = self.redact(self.DOC)
        self.assertEqual(changed, sorted(rep.paths))

    def test_a_redacted_record_has_nothing_left(self):
        _text, out, _rep = self.redact(self.DOC)
        rep = R.Report()
        self.assertEqual(R.redact_record_paths(out, rep), out)
        self.assertEqual(rep.paths, [])
        self.assertFalse(rep.found_anything())

    def test_a_key_gets_a_placeholder_of_its_own(self):
        # A fingerprint key of a tobase.map far from the project
        # (checklist._fp_key: its path from the project).
        doc = {"frozen": {"inputs": {f"../../{PERSON}/tobase.map": "ab",
                                     "../../other/tobase.map": "cd",
                                     "../tobase.map": "ef",
                                     "inputs/margin/trades.tt": "01"}}}
        _text, out, rep = self.redact(doc)
        got = json.loads(out)["frozen"]["inputs"]
        self.assertEqual(sorted(got.items()), [
            ("../tobase.map", "ef"), ("<redacted path 1>", "ab"),
            ("<redacted path 2>", "cd"), ("inputs/margin/trades.tt", "01")])
        self.assertEqual(len(rep.paths), 2)


if __name__ == "__main__":
    unittest.main()
