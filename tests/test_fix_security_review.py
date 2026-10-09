"""2026-10 security review pins (synthetic data only)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
ENV = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")


def _plant(folder: Path) -> Path:
    """A json.py in `folder` that leaves a marker file when imported."""
    marker = folder / "PLANTED_RAN"
    for mod in ("json", "csv", "runpy"):
        (folder / f"{mod}.py").write_text(
            f"open({str(marker)!r}, 'a').write({mod!r})\n")
    return marker


class TestChildPythonNeverImportsFromCwd(unittest.TestCase):
    """H1: a json.py / csv.py planted in a project folder never runs in a
    taxjson child Python process."""

    def test_plain_dash_m_does_import_it(self):
        # The control: what the old launch shape did.
        with tempfile.TemporaryDirectory() as tmp:
            marker = _plant(Path(tmp))
            subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_sort",
                            "--help"], cwd=tmp, env=ENV,
                           capture_output=True, stdin=subprocess.DEVNULL)
            self.assertTrue(marker.exists())

    def test_python_module_argv_both_forms(self):
        from taxjson.lib.dispatch import python_module_argv
        for legacy in (False, True):
            with self.subTest(legacy=legacy), \
                    tempfile.TemporaryDirectory() as tmp:
                marker = _plant(Path(tmp))
                argv = python_module_argv("taxjson.bin.taxjson_sort",
                                          ["--help"], legacy=legacy)
                r = subprocess.run(argv, cwd=tmp, env=ENV,
                                   capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL)
                self.assertFalse(marker.exists(), r.stderr)
                self.assertEqual(r.returncode, 0, r.stderr)
                # Run as -m would: usage names the program.
                self.assertIn("usage:", r.stdout)

    def test_legacy_bootstrap_argv0_is_the_module(self):
        from taxjson.lib.dispatch import python_module_argv
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkgdir"
            pkg.mkdir()
            (pkg / "probe_mod.py").write_text(
                "import sys\nprint(sys.argv)\nprint('' in sys.path)\n")
            env = dict(ENV, PYTHONPATH=str(pkg))
            r = subprocess.run(python_module_argv("probe_mod", ["a", "b"],
                                                  legacy=True),
                               cwd=tmp, env=env, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            argv_line, empty_on_path = r.stdout.splitlines()
            self.assertTrue(argv_line.endswith("'a', 'b']"), argv_line)
            self.assertIn("probe_mod.py", argv_line)
            self.assertEqual(empty_on_path, "False")

    def test_dispatch_subprocess_and_interactive_paths(self):
        # The corp-actions stage (interactive, a real subprocess) and
        # TAXJSON_DISPATCH=subprocess go through dispatch.run_cmd.
        from taxjson.lib import dispatch
        with tempfile.TemporaryDirectory() as tmp:
            marker = _plant(Path(tmp))
            cmd = [sys.executable, "-m", "taxjson.bin.taxjson_sort",
                   "--help"]
            old = dict(os.environ)
            os.environ.update(ENV)
            try:
                with open(Path(tmp) / "out.txt", "w") as f:
                    r = dispatch.run_cmd(cmd, stdout=f, cwd=tmp,
                                         interactive=True)
                self.assertEqual(r.returncode, 0)
                os.environ[dispatch._ENV_FLAG] = "subprocess"
                r = dispatch.run_cmd(cmd, capture_output=True, cwd=tmp)
                self.assertEqual(r.returncode, 0, r.stderr)
            finally:
                os.environ.clear()
                os.environ.update(old)
            self.assertFalse(marker.exists())

    def test_checklist_default_run_sub(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = _plant(root)
            old_cwd, old_env = os.getcwd(), dict(os.environ)
            os.chdir(root)
            os.environ.update(ENV)
            try:
                code, out, err = cl.default_run_sub(root)(["--version"])
            finally:
                os.chdir(old_cwd)
                os.environ.clear()
                os.environ.update(old_env)
            self.assertEqual(code, 0, err)
            self.assertFalse(marker.exists())

    def test_no_bare_dash_m_child_launch_in_the_package(self):
        # Every child Python launch goes through python_module_argv. A
        # `[sys.executable, "-m", "taxjson.bin.X"]` list is only the
        # tool-command marker dispatch.run_cmd takes (in process, or out
        # of process through python_module_argv): taxjson_run hands
        # every one to run_cmd.
        import re
        pat = re.compile(r"sys\.executable\s*,\s*[\"']-m[\"']")
        allowed = {"dispatch.py", "taxjson_run.py"}
        for p in (SRC / "taxjson").rglob("*.py"):
            if p.name not in allowed:
                self.assertIsNone(pat.search(p.read_text()), p)

def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=T", "-c",
                           "user.email=t@example.com", "-c",
                           "core.hooksPath=/dev/null", "-C", str(root),
                           *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


class TestChecklistGitRunsNoRepoCommand(unittest.TestCase):
    """M3: a hostile .git/config + .gitattributes filter is never run by
    the checklist's git checks."""

    def _repo(self, root: Path, key: str = "filter.evil.clean"):
        marker = root.parent / "FILTER_RAN"
        (root / "inputs").mkdir(parents=True)
        (root / "inputs" / "a.csv").write_text("x\n")
        (root / "taxjson.toml").write_text("")
        self.assertEqual(_git(root, "init", "-q").returncode, 0)
        _git(root, "add", "-A")
        self.assertEqual(_git(root, "commit", "-qm", "c").returncode, 0)
        (root / ".gitattributes").write_text("* filter=evil diff=evil\n")
        _git(root, "config", key, f"touch {marker}; cat")
        (root / "inputs" / "a.csv").write_text("changed\n")
        return marker

    def _ctx(self, root):
        from datetime import date
        from taxjson.lib import checklist as cl
        return cl.Ctx(root=root, cfg={"settings": {}, "accounts": {}},
                      year=2025, today=date(2026, 3, 1),
                      run_sub=lambda *a, **k: (0, "", ""))

    def test_plain_git_status_runs_the_filter(self):
        # The control: what the old check ran.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            marker = self._repo(root)
            _git(root, "status", "--porcelain")
            self.assertTrue(marker.exists())

    def test_inputs_and_lock_committed_are_blocked(self):
        from taxjson.lib import checklist as cl
        for key in ("filter.evil.clean", "filter.evil.process",
                    "diff.evil.textconv", "core.attributesFile"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "p"
                marker = self._repo(root, key)
                (root / "filed").mkdir()
                (root / "filed" / "2025.json").write_text("{}")
                r = cl.d_inputs_committed(self._ctx(root))
                self.assertEqual(r.status, "blocked")
                self.assertIn(key.lower(), r.detail)
                r2 = cl.d_lock_committed(self._ctx(root))
                self.assertEqual(r2.status, "blocked", r2.detail)
                self.assertFalse(marker.exists())

    def test_clean_repo_still_checked(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            (root / "inputs").mkdir(parents=True)
            (root / "inputs" / "a.csv").write_text("x\n")
            _git(root, "init", "-q")
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "c")
            self.assertEqual(cl.d_inputs_committed(self._ctx(root)).status,
                             "done")
            (root / "inputs" / "a.csv").write_text("y\n")
            self.assertEqual(cl.d_inputs_committed(self._ctx(root)).status,
                             "todo")


class TestRedactWallets(unittest.TestCase):
    """M4: Solana, Cardano, XRP, Tron and Litecoin addresses are
    pseudonymised; any other wallet-like token goes to REVIEW. All
    addresses below are made up (right alphabet and length only)."""

    ADDRS = {
        "solana": "SoLana5ampLe7xYzABCDEFGHJKMNPQRSTUVWXYZabcde",
        "cardano": "addr1q" + "x9y8w7v6u5t4s3r2q0pzn" * 3,
        "xrp": "rSampLe7xYzABCDEFGHJKMNPQRSTUVw",
        "tron": "TSampLe7xYzABCDEFGHJKMNPQRSTUVWXa",
        "litecoin-L": "LSampLe7xYzABCDEFGHJKMNPQRSTUVWXa",
        "litecoin-M": "MSampLe7xYzABCDEFGHJKMNPQRSTUVWXa",
        "litecoin-bech32": "ltc1q" + "x9y8w7v6u5t4s3r2q0pz" * 2,
    }

    def test_each_chain_is_replaced_stably(self):
        from taxjson.bin.taxjson_redact import redact_text
        for chain, addr in self.ADDRS.items():
            with self.subTest(chain=chain):
                text = ("Timestamp,Notes\n"
                        f"2025-01-01,Sent 1 to {addr}\n"
                        f"2025-01-02,Again to {addr}\n")
                out, rep = redact_text(text)
                self.assertNotIn(addr, out)
                self.assertEqual(len(rep.wallets), 1)
                a, b = (ln.split()[-1] for ln in out.splitlines()[1:])
                self.assertEqual(a, b)
                self.assertEqual(len(a), len(addr))
                self.assertEqual(rep.review, [])

    def test_words_and_ids_are_not_wallets(self):
        from taxjson.bin.taxjson_redact import redact_text
        text = ("Date,Description,Amount\n"
                "2025-01-01,TRANSFERREDFROMSAMPLEHOLDINGSACCOUNTX,1\n"
                "2025-01-02,Rebalancing contribution for the quarterly,1\n")
        out, rep = redact_text(text)
        self.assertEqual(out, text)
        self.assertEqual(rep.wallets, {})

    def test_unknown_chain_goes_to_review(self):
        from taxjson.bin.taxjson_redact import redact_text
        text = ("Date,Notes,Amount\n"
                "2025-01-01,To zz1" + "x9y8w7v6u5t4s3r2q0pzx9y8w" + ",1\n"
                "2025-01-02,Memo 9aBcDeFgHjKmNpQrStUvWxYzAbCdEfGhJkMnPqRsTuVwXyZaBcDeFgHjKm,1\n")
        out, rep = redact_text(text)
        self.assertEqual(sorted({n for n, why in rep.review
                                 if "wallet-like" in why}), [2, 3])


class TestRedactNameReview(unittest.TestCase):
    """M5: upper-case names and "LAST, FIRST" cells go to REVIEW; an
    8-digit number after a broker's name is an account id. Synthetic."""

    def _review_lines(self, text):
        from taxjson.bin.taxjson_redact import redact_text
        out, rep = redact_text(text)
        return out, sorted({n for n, _ in rep.review})

    def test_upper_case_names_are_reviewed(self):
        text = ("Date,Description,Amount\n"
                "2025-01-01,JANE Q SAMPLE,1\n"
                '2025-01-02,"SAMPLE, JANE",1\n'
                '2025-01-03,"Sample, Jane",1\n'
                "2025-01-04,JOHN EXAMPLE,1\n")
        self.assertEqual(self._review_lines(text)[1], [2, 3, 4, 5])

    def test_security_descriptions_are_not(self):
        descs = ("APPLE INC", "ISHARES CORE S&P 500 ETF",
                 "BERKSHIRE HATHAWAY INC CL B", "CALL AAPL 01/17/25 150",
                 "ROYAL BANK OF CANADA - Buy", "META PLATFORMS INC",
                 "TORONTO DOMINION BANK", "NON-RESIDENT TAX WITHHELD",
                 "USD CAD", "GLOBAL X ENHANCED ALL-EQUITY",
                 "VANGUARD FTSE CDN HIGH DIV YLD INDEX ETF",
                 '"Apple, Inc"', "INTEREST ON CREDIT BALANCE")
        text = "Date,Description,Amount\n" + "".join(
            f"2025-01-{i + 1:02d},{d},1\n" for i, d in enumerate(descs))
        self.assertEqual(self._review_lines(text)[1], [])

    def test_demo_csvs_do_not_flood_review(self):
        from taxjson.bin.taxjson_redact import redact_text
        for p in sorted((REPO / "examples").glob("*_demo.csv")):
            with self.subTest(p.name):
                _, rep = redact_text(p.read_text(encoding="utf-8"))
                self.assertLessEqual(len(rep.review), 2)

    def test_account_after_another_brokers_name(self):
        text = ("Date,Description,Amount\n"
                "2025-01-01,Transfer in from Questrade 55500099,1\n"  # pii-ok
                "2025-01-02,TD Direct Investing acct 55500098 journal,1\n"  # pii-ok
                "2025-01-03,Questrade 20250115 statement,1\n")
        out, _ = self._review_lines(text)
        self.assertNotIn("55500099", out)  # pii-ok
        self.assertNotIn("55500098", out)  # pii-ok
        self.assertIn("20250115", out)     # a date, not an id


class TestFormExportCsvFormulas(unittest.TestCase):
    """LOW (a): a text cell a spreadsheet would evaluate is written as
    text; numbers (negative ones included) stay numbers."""

    def test_schedule3_and_8949_rows(self):
        import csv
        import io
        from taxjson.bin.taxjson_form_export import _rows_csv
        evil = '=HYPERLINK("http://example.com/x","click")'
        s3 = {"form": "schedule3", "rows": [{
            "line": "13200", "proceeds_line": "13199", "gain_line": "13200",
            "property": evil, "units": 10, "symbol": "+ZZQ",
            "acq_year": 2024, "proceeds": 100.0, "acb": 150.0,
            "outlays": 0.0, "gain": -50.0, "denied": 0.0,
            "notes": "@SUM(A1)"}]}
        f8949 = {"form": "8949", "part_I": [{
            "description": "-2+3 ZZQ", "date_acquired": "2025-01-02",
            "date_sold": "2025-02-03", "proceeds": "-12.50", "cost": 1.0,
            "code": "", "adjustment": "", "gain": -13.5,
            "account": "\tacct", "boxes": "\rA"}], "part_II": []}
        for rep, want in ((s3, {3: "'" + evil, 5: "'+ZZQ",
                                12: "'@SUM(A1)", 10: "-50.0"}),
                          (f8949, {1: "'-2+3 ZZQ", 4: "-12.50",
                                   8: "-13.5", 9: "'\tacct",
                                   10: "'\rA"})):
            buf = io.StringIO()
            _rows_csv(rep, buf)
            row = list(csv.reader(io.StringIO(buf.getvalue())))[1]
            for i, v in want.items():
                self.assertEqual(row[i], v, (rep["form"], i))


class TestRunLockNeverFollowsALink(unittest.TestCase):
    """LOW (b): work/.run.lock is taken through safe_write.file_lock."""

    def test_planted_lock_symlink_target_untouched(self):
        from taxjson.bin import taxjson_run as tr
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "work"
            cache.mkdir()
            target = Path(tmp) / "outside.txt"
            (cache / ".run.lock").symlink_to(target)
            old = tr._RUN_LOCK_FH
            tr._RUN_LOCK_FH = None
            try:
                tr._acquire_run_lock(cache)
                self.assertFalse(target.exists())
                self.assertFalse((cache / ".run.lock").is_symlink())
                self.assertEqual((cache / ".run.lock").stat().st_mode
                                 & 0o077, 0)
            finally:
                if tr._RUN_LOCK_FH is not None:
                    tr._RUN_LOCK_FH.close()
                tr._RUN_LOCK_FH = old


class TestOutsideFolderLinksRefused(unittest.TestCase):
    """LOW (c): work/, reports/ or inputs/<account>/ linked outside the
    project stops every command (exit 2); a link inside is fine."""

    def _cli(self, root, *args):
        return subprocess.run([sys.executable, "-m",
                               "taxjson.bin.taxjson_run", "-C", str(root),
                               *args], capture_output=True, text=True,
                              env=dict(ENV, TAXJSON_WIDTH="0"),
                              stdin=subprocess.DEVNULL, cwd=str(REPO))

    def test_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            r = self._cli(root, "init", "--country", "canada")
            self.assertEqual(r.returncode, 0, r.stderr)
            acct = sorted(d.name for d in (root / "inputs").iterdir()
                          if d.is_dir())[0]
            outside = Path(tmp) / "elsewhere"
            outside.mkdir()
            for name in ("work", "reports", f"inputs/{acct}"):
                with self.subTest(name=name):
                    link = root / name
                    if link.is_dir() and not link.is_symlink():
                        link.rename(Path(tmp) / "saved")
                    link.symlink_to(outside, target_is_directory=True)
                    r = self._cli(root, "run", "--no-input")
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("symlinks to outside the project",
                                  r.stderr)
                    self.assertIn(f"{name}/ ->", r.stderr)
                    self.assertEqual(list(outside.iterdir()), [])
                    link.unlink()
                    if (Path(tmp) / "saved").exists():
                        (Path(tmp) / "saved").rename(link)
            # inside the project: kept
            (root / "scratch").mkdir()
            if (root / "work").exists():
                import shutil
                shutil.rmtree(root / "work")
            (root / "work").symlink_to(root / "scratch")
            r = self._cli(root, "run", "--no-input")
            self.assertNotIn("symlinks to outside", r.stderr)


if __name__ == "__main__":
    unittest.main()
