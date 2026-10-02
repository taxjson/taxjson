"""Re-audit-2 fixes: the filing checklist (lib/checklist.py) and the run
state it judges (run-clean's input fingerprint, --strict, locks)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
           NO_COLOR="1")

TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')
BOOK = ("BUYSELL 2025-02-03 10:00:00 XYZ.TO 100.00000000 CAD 10.00000000 1000.00000 0.00000\n"
        "BUYSELL 2025-05-05 10:00:00 XYZ.TO -100.00000000 CAD 12.00000000 1200.00000 0.00000\n")


def tj(root, *args, check=True):
    r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                        "-C", str(root), *args], capture_output=True,
                       text=True, env=ENV, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise AssertionError(f"{args}: rc {r.returncode}\n{r.stderr[-3000:]}")
    return r


def make_project(root, toml=TOML, book=BOOK, run=True):
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin" / "book.tt").write_text(book)
    (root / "taxjson.toml").write_text(toml)
    if run:
        tj(root, "run", "--no-input")
    return root


def ctx(root, table=None, today=date(2026, 9, 23), year=2025):
    from taxjson.lib.tomlcompat import tomllib
    cfg = tomllib.loads((root / "taxjson.toml").read_text())
    table = table or {}
    return cl.Ctx(root=root, cfg=cfg, year=year, today=today,
                  run_sub=lambda argv, timeout=900: table.get(argv[0], (0, "", "")))


class _Built(unittest.TestCase):
    """One full run, copied per test (a run takes seconds)."""
    tmp = None
    built = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.built = make_project(Path(cls.tmp.name) / "built")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy(self):
        d = tempfile.mkdtemp(dir=self.tmp.name)
        dst = Path(d) / "p"
        shutil.copytree(self.built, dst, symlinks=True)
        return dst


class TestFingerprintCoversEveryRunInput(_Built):
    """A2-0124, A2-0126, A2-0358, A2-0363, A2-1158: the elections
    manifest, sends.json and crypto_ticker.map are run inputs."""

    def test_clean_after_run(self):
        p = self.copy()
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")

    def test_manifest_change_is_stale(self):
        p = self.copy()
        man = p / "inputs" / "margin" / "manifest.json"
        man.write_text(json.dumps({"elections": {"e1": {"choice": "taxable_disposition"}}}))
        r = cl.d_run_clean(ctx(p))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("inputs/margin/manifest.json", r.detail)
        from taxjson.web.context import ProjectContext
        from taxjson.web.data import freshness
        fr = freshness(ProjectContext.load(p))
        self.assertTrue(fr["stale"], fr)

    def test_empty_manifest_is_not_an_input_change(self):
        p = self.copy()
        (p / "inputs" / "margin" / "manifest.json").write_text('{"elections": {}}')
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")

    def test_sends_json_and_crypto_ticker_map(self):
        for rel in ("inputs/margin/sends.json", "crypto_ticker.map"):
            p = self.copy()
            (p / rel).write_text("{}" if rel.endswith(".json") else "FOO FOO123\n")
            r = cl.d_run_clean(ctx(p))
            self.assertEqual(r.status, "attention", rel)
            self.assertIn(rel, r.detail)

    def test_root_maps_match_the_run(self):
        from taxjson.bin import taxjson_run as R
        self.assertEqual(set(cl.PROJECT_ROOT_MAPS), set(R._PROJECT_ROOT_INPUTS))
        self.assertEqual(set(cl.SPREADSHEET_SUFFIXES), set(R.SPREADSHEET_SUFFIXES))


class TestPlanningEditsKeepBooksCurrent(_Built):
    """A2-0681, A2-1157: taxjson.toml content no run stage reads (a
    comment, [instalments], [estimate], province, prior_year_record,
    holdings, fetch keys) does not make the books stale."""

    def test_planning_only_edits(self):
        edits = [
            "# a comment\n",
            '[instalments]\nbasis = "current_year"\npaid = [{date = "2025-03-17", amount = 5000}]\n',
            "[estimate]\nother_income = 50000\n",
        ]
        for extra in edits:
            p = self.copy()
            with (p / "taxjson.toml").open("a") as f:
                f.write(extra)
            self.assertEqual(cl.d_run_clean(ctx(p)).status, "done", extra)
        p = self.copy()
        t = (p / "taxjson.toml").read_text().replace(
            'option_grant_timing_since = 2025\n',
            'option_grant_timing_since = 2025\nprovince = "BC"\n'
            'prior_year_record = "../2024/filed/2024.json"\n').replace(
            'type = "taxable"\n', 'type = "taxable"\nholdings = "h.toml"\n'
            'brokerage = "questrade"\n')
        (p / "taxjson.toml").write_text(t)
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")
        # close-year no longer refuses on it
        r = tj(p, "close-year", check=False)
        self.assertNotIn("not the clean result", r.stderr)

    def test_a_run_setting_is_stale(self):
        p = self.copy()
        t = (p / "taxjson.toml").read_text().replace(
            'base_currency = "CAD"\n', 'base_currency = "CAD"\ntax_date = "trade"\n')
        (p / "taxjson.toml").write_text(t)
        r = cl.d_run_clean(ctx(p))
        self.assertEqual(r.status, "attention")
        self.assertIn("taxjson.toml", r.detail)


class TestFingerprintRecordEdges(_Built):
    def test_damaged_record_is_attention(self):
        """A2-1155: a truncated or malformed record never reads as 'no
        record' (the mtime fallback called stale books clean)."""
        for bad in ('{"files": [1, 2]}', '{"files": {"a": '):
            p = self.copy()
            (p / "work" / cl.FINGERPRINT_FILE).write_text(bad)
            r = cl.d_run_clean(ctx(p))
            self.assertEqual(r.status, "attention", bad)
            self.assertIn("cannot be read", r.detail)

    def test_legacy_record_still_judged(self):
        """A record from an older taxjson (no version) is compared the
        old way: an upgrade does not mark every project stale."""
        p = self.copy()
        f = p / "work" / cl.FINGERPRINT_FILE
        f.write_text(json.dumps({"files": cl._legacy_input_fingerprint(p, ctx(p).cfg)}))
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")
        (p / "inputs" / "margin" / "book.tt").write_text(BOOK + "\n")
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "attention")

    def test_dangling_sum_symlink(self):
        """A2-1148: the step reports the unreadable report, it does not crash."""
        p = self.copy()
        (p / "reports" / "zz.sum").symlink_to(p / "reports" / "gone.sum")
        r = cl.d_run_clean(ctx(p))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("cannot read reports/zz.sum", r.detail)


class TestHiddenAndLockFiles(_Built):
    """A2-1145, A2-1166: run and the checklist agree on the input set —
    hidden files and Excel '~$' lock files are neither read nor
    fingerprinted."""

    def test_skipped_everywhere(self):
        from taxjson.bin.taxjson_run import input_files
        p = self.copy()
        acct = p / "inputs" / "margin"
        (acct / ".hidden.tt").write_text(
            "BUYSELL 2025-06-03 10:00:00 ABC.TO 1.00000000 CAD 1.00000000 1.00000 0.00000\n")
        (acct / "~$book.csv").write_bytes(b"\x00lock")
        self.assertEqual([x.name for x in input_files(acct, ".tt")], ["book.tt"])
        self.assertEqual(input_files(acct, ".csv"), [])
        self.assertEqual([x.name for x in cl._data_files(acct)], ["book.tt"])
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")
        (p / "inputs" / "slips").mkdir()
        (p / "inputs" / "slips" / "~$t5008.csv").write_bytes(b"\x00")
        self.assertEqual(cl.slip_files(p), [])
        self.assertEqual(cl._unread_slip_files(p), [])

    def test_numbers_spreadsheet(self):
        """A2-1156: an Apple Numbers export is refused by run and
        flagged by the checklist, like .xlsx."""
        p = self.copy()
        (p / "inputs" / "margin" / "Activity_2025.numbers").write_bytes(b"PK\x03\x04")
        r = tj(p, "run", "--no-input", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Activity_2025.numbers", r.stderr)
        rc = cl.d_run_clean(ctx(p))
        self.assertEqual(rc.status, "attention")
        self.assertIn("unread spreadsheet", rc.detail)


if __name__ == "__main__":
    unittest.main()
