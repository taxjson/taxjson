"""`taxjson redact` with no FILE: the project's inputs/ copied to
inputs_redact/ (or --out DIR) and the COPY redacted; inputs/ is never
written. Every fixture is synthetic (fake ids, marked pii-ok)."""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import FIXTURES, SRC, assert_console

from taxjson.bin import taxjson_redact as R

# Synthetic identifiers planted in the project; none may reach the copy.
QT_ACCT = "55500001"            # pii-ok (synthetic)
NAME_ONLY = "55500009"          # pii-ok (synthetic): only in a file name
IB_ACCT = "U1234567"            # pii-ok (synthetic, the style fixtures')
SIDECAR_ACCT = "U5550001"       # pii-ok (synthetic)
FIXTURE_ACCT = "12345678"       # pii-ok (synthetic, the style fixtures')
SECRETS = (QT_ACCT, NAME_ONLY, IB_ACCT, SIDECAR_ACCT, FIXTURE_ACCT,
           "Demo User", "sam.sample@example.org")

QT_CSV = (
    "Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
    "Price,Gross Amount,Commission,Net Amount,Currency,Activity Type,"
    "Account #,Account Type\n"
    "2024-02-01,2024-02-05,Buy,MSFT,MICROSOFT CORP,5,400.00,-2000.00,-4.95,"
    "-2004.95,USD,Trades,55500001,Margin\n"                      # pii-ok
    "2024-04-01,2024-04-03,Sell,MSFT,MICROSOFT CORP,-5,420.00,2100.00,-4.95,"
    "2095.05,USD,Trades,55500001,Margin\n"                       # pii-ok
    "2024-04-10,2024-04-10,CON,,CONTRIBUTION sam.sample@example.org,0,0,0,0,"
    "100.00,CAD,Deposits,55500001,Margin\n")                     # pii-ok


def _env(**extra):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(SRC) + os.pathsep + e.get("PYTHONPATH", "")
    e.update({k: str(v) for k, v in extra.items()})
    return e


def tj(root: Path, *args, **env):
    """`taxjson -C root <args>` (captured width 0 unless env says)."""
    env.setdefault("TAXJSON_WIDTH", "0")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=root, capture_output=True, text=True, timeout=900,
        env=_env(**env), stdin=subprocess.DEVNULL)


def snapshot(d: Path):
    """Relative path -> sha256 of every file under `d` (links as links)."""
    out = {}
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d).as_posix()
        if p.is_symlink():
            out[rel] = "link:" + os.readlink(p)
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            out[rel] = "dir"
    return out


def all_text(d: Path) -> str:
    """Every path and every file's bytes under `d`, as one string."""
    parts = []
    for p in sorted(d.rglob("*")):
        parts.append(p.relative_to(d).as_posix())
        if p.is_file() and not p.is_symlink():
            parts.append(p.read_bytes().decode("utf-8", "replace"))
    return "\n".join(parts)


def flat(r) -> str:
    return " ".join((r.stdout + "\n" + r.stderr).split())


def build_style_project(tmp: Path) -> Path:
    """The canada style project plus: a Questrade export named after its
    account, an IB export named after its account, a .tt named after an
    account only its name carries (with an id in a comment), a .toml
    sidecar with an account key, a README.txt, a binary .xlsx and a
    hidden file."""
    root = tmp / "proj"
    shutil.copytree(FIXTURES / "canada", root)
    inp = root / "inputs"
    (inp / "qt" / f"{QT_ACCT}.csv").write_text(QT_CSV)
    (inp / "margin" / "ib_extra.csv").rename(
        inp / "margin" / f"{IB_ACCT}_extra.csv")
    tt = (inp / "tfsa" / "tfsa_extra.tt").read_text()
    (inp / "tfsa" / "tfsa_extra.tt").unlink()
    (inp / "tfsa" / f"{NAME_ONLY}_manual.tt").write_text(
        f"# copied from account {QT_ACCT}\n" + tt)
    (inp / "margin" / "broker.toml").write_text(
        f'broker_account = "{SIDECAR_ACCT}"\nlabel = "margin"\n')
    (inp / "rrsp" / "README.txt").write_text(
        "Put the Webull statement CSV here.\n")
    (inp / "margin" / "statement.xlsx").write_bytes(b"PK\x03\x04binary")
    (inp / ".DS_Store").write_bytes(b"\x00\x01hidden")
    return root


def tax_object_counts(r) -> list:
    """The run's per-file `N tax objects` counts, sorted (file names
    differ between a project and its redacted copy)."""
    return sorted(int(m) for m in re.findall(r": (\d+) tax objects",
                                             r.stdout + r.stderr))


class TestRedactProject(unittest.TestCase):
    """The full contract on the style project (several brokers' demo
    files, a .tt, a sidecar, account-number file names)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.root = build_style_project(cls.tmp)
        cls.before = snapshot(cls.root / "inputs")
        cls.r = tj(cls.root, "redact", "--no-denylist", TAXJSON_WIDTH="100")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_copy_written_and_originals_untouched(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr)
        self.assertEqual(snapshot(self.root / "inputs"), self.before)
        self.assertTrue((self.root / "inputs_redact" / R.TREE_MARKER).is_file())
        # No temporary build folder is left beside it.
        self.assertEqual(sorted(p.name for p in self.root.iterdir()
                                if p.name.startswith(".inputs_redact")), [])

    def test_tree_mirrors_inputs(self):
        out = self.root / "inputs_redact"
        dirs = sorted(p.relative_to(out).as_posix() for p in out.rglob("*")
                      if p.is_dir())
        self.assertEqual(dirs, sorted(p.relative_to(self.root / "inputs")
                                      .as_posix() for p in
                                      (self.root / "inputs").rglob("*")
                                      if p.is_dir()))
        files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*")
                       if p.is_file())
        # Same files except the binary (.xlsx) and hidden ones; names
        # with ids renamed.
        self.assertIn("margin/ib_demo.csv", files)
        self.assertIn("margin/broker.toml", files)
        self.assertIn("rrsp/README.txt", files)
        self.assertIn("slips/t5008.csv", files)
        self.assertIn("margin/margin_extra.tt", files)
        self.assertNotIn("margin/statement.xlsx", files)
        self.assertNotIn(".DS_Store", files)
        n_src = len([p for p in (self.root / "inputs").rglob("*")
                     if p.is_file()])
        self.assertEqual(len(files), n_src - 2 + 1)   # - xlsx, hidden; + marker

    def test_identifiers_gone_from_copy(self):
        text = all_text(self.root / "inputs_redact")
        for s in SECRETS:
            self.assertNotIn(s.lower(), text.lower(), s)

    def test_names_masked_uniquely_and_mapped_on_console_only(self):
        out = self.root / "inputs_redact"
        qt = sorted(p.name for p in (out / "qt").iterdir())
        self.assertEqual(len(qt), 2)
        self.assertEqual(len({n.lower() for n in qt}), 2)
        self.assertIn("questrade_demo.csv", qt)
        renamed = [n for n in qt if n != "questrade_demo.csv"][0]
        self.assertRegex(renamed, r"^9990\d{4}\.csv$")
        margin = sorted(p.name for p in (out / "margin").iterdir())
        self.assertTrue(any(re.fullmatch(r"U9990\d{3}_extra\.csv", n)
                            for n in margin), margin)
        tfsa = sorted(p.name for p in (out / "tfsa").iterdir())
        self.assertTrue(any(re.fullmatch(r"9990\d{4}_manual\.tt", n)
                            for n in tfsa), tfsa)
        # The IB account's file-name placeholder is its content's.
        ib = [n for n in margin if n.endswith("_extra.csv")][0]
        self.assertIn(ib.split("_")[0],
                      (out / "margin" / "ib_demo.csv").read_text())
        # The map: on the console, never in the copy.
        console = self.r.stdout
        self.assertIn(f"inputs/qt/{QT_ACCT}.csv → inputs_redact/qt/{renamed}",
                      console)
        self.assertIn(f"inputs/tfsa/{NAME_ONLY}_manual.tt →", console)

    def test_console_style(self):
        r = self.r
        assert_console(self, r.stdout)
        assert_console(self, r.stderr)
        self.assertTrue(r.stdout.startswith(
            "==> Copying inputs/ to inputs_redact/\n==> Redacting "), r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith(
            "==> Done. Review inputs_redact/ before sharing it."), r.stdout)
        self.assertIn("Info: margin/ib_demo.csv: 1 account id", r.stdout)
        self.assertIn("Info: margin/broker.toml: 1 account id", r.stdout)
        self.assertIn("Warning: inputs/margin/statement.xlsx not copied",
                      r.stderr)
        # Counts only: no identifier is printed except in the name map.
        no_map = re.sub(r"(?m)^  inputs/.*→.*$", "", r.stdout + r.stderr)
        for s in SECRETS:
            self.assertNotIn(s, no_map)

    def test_redacted_copy_parses_and_run_ignores_it(self):
        # The original project (inputs_redact/ beside inputs/): the run
        # never reads the copy.
        # (A copy without the .xlsx, which the run refuses in inputs/.)
        base = self.tmp / "origproj"
        shutil.copytree(self.root, base, symlinks=True)
        (base / "inputs" / "margin" / "statement.xlsx").unlink()
        orig = tj(base, "run", "--no-input")
        self.assertIn(orig.returncode, (0, 3), orig.stderr[-2000:])
        self.assertNotIn("inputs_redact", orig.stdout + orig.stderr)
        # A project whose inputs/ IS the redacted copy: every file is
        # still detected (by content — the names changed) and parses to
        # the same number of tax objects.
        red = self.tmp / "redproj"
        red.mkdir()
        for f in ("taxjson.toml", "holdings.toml"):
            shutil.copy(self.root / f, red / f)
        shutil.copytree(self.root / "inputs_redact", red / "inputs",
                        symlinks=True)
        r = tj(red, "run", "--no-input")
        self.assertIn(r.returncode, (0, 3), r.stderr[-3000:])
        both = r.stdout + r.stderr
        for broker in ("Interactive Brokers", "Questrade", "RBC Direct",
                       "Webull", "Coinbase", "Kraken"):
            self.assertIn(f"→ {broker}", both, broker)
        self.assertEqual(tax_object_counts(r), tax_object_counts(orig))
        self.assertTrue(tax_object_counts(r))

    def test_init_gitignore_lists_the_copy(self):
        from taxjson.bin.taxjson_run import _TEMPLATE_GITIGNORE
        self.assertIn("\ninputs_redact/\n", _TEMPLATE_GITIGNORE)


def small_project(tmp: Path) -> Path:
    root = tmp / "small"
    (root / "inputs" / "qt").mkdir(parents=True)
    (root / "inputs" / "qt" / f"{QT_ACCT}.csv").write_text(QT_CSV)
    return root


class TestRedactProjectModes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = small_project(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_refuses_existing_copy_without_force(self):
        self.assertEqual(tj(self.root, "redact", "--no-denylist").returncode, 0)
        out = self.root / "inputs_redact"
        before = snapshot(out)
        r = tj(self.root, "redact", "--no-denylist")
        self.assertEqual(r.returncode, 2)
        self.assertIn("exists (use --force to replace it)", flat(r))
        self.assertEqual(snapshot(out), before)

    def test_force_replaces_the_copy(self):
        self.assertEqual(tj(self.root, "redact", "--no-denylist").returncode, 0)
        out = self.root / "inputs_redact"
        (out / "stale.csv").write_text("old\n")
        src = self.root / "inputs" / "qt" / f"{QT_ACCT}.csv"
        src.write_text(QT_CSV + QT_CSV.splitlines(True)[1].replace(
            "2024-02-01", "2024-06-01"))
        r = tj(self.root, "redact", "--no-denylist", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((out / "stale.csv").exists())
        self.assertTrue((out / R.TREE_MARKER).is_file())
        files = list((out / "qt").iterdir())
        self.assertEqual(len(files), 1)
        self.assertIn("2024-06-01", files[0].read_text())
        self.assertEqual([p.name for p in self.root.iterdir()
                          if p.name.startswith(".inputs_redact")], [])

    def test_force_never_replaces_a_folder_it_did_not_make(self):
        other = self.root / "inputs_redact"
        other.mkdir()
        (other / "mine.txt").write_text("keep\n")
        r = tj(self.root, "redact", "--no-denylist", "--force")
        self.assertEqual(r.returncode, 2)
        self.assertIn("was not made by taxjson redact", flat(r))
        self.assertEqual((other / "mine.txt").read_text(), "keep\n")

    def test_check_writes_nothing(self):
        before = snapshot(self.root)
        r = tj(self.root, "redact", "--no-denylist", "--check")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(snapshot(self.root), before)
        self.assertIn("1 account id", flat(r))
        self.assertIn("would be renamed", flat(r))

    def test_check_clean_tree_exits_0(self):
        (self.root / "inputs" / "qt" / f"{QT_ACCT}.csv").unlink()
        (self.root / "inputs" / "qt" / "t.tt").write_text(
            "BUYSELL 2024-01-10 09:30:00 SAMPA.TO 1 CAD 4.00 4.00 0\n")
        r = tj(self.root, "redact", "--no-denylist", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.root / "inputs_redact").exists())

    def test_out_dir(self):
        dest = self.tmp / "share" / "sample"
        r = tj(self.root, "redact", "--no-denylist", "--out", str(dest))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.root / "inputs_redact").exists())
        self.assertTrue((dest / R.TREE_MARKER).is_file())
        self.assertNotIn(QT_ACCT, all_text(dest))

    def test_out_inside_or_over_inputs_refused(self):
        for dest in (self.root / "inputs" / "x", self.root / "inputs",
                     self.root):
            r = tj(self.root, "redact", "--no-denylist", "--force",
                   "--out", str(dest))
            self.assertEqual(r.returncode, 2, dest)
            self.assertIn("redacted copy goes beside inputs/", flat(r))
        self.assertEqual(sorted(p.name for p in (self.root / "inputs" / "qt")
                                .iterdir()), [f"{QT_ACCT}.csv"])

    def test_no_inputs_folder(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        r = tj(empty, "redact", "--no-denylist")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no inputs/ folder", flat(r))

    def test_explicit_file_mode_unchanged(self):
        f = self.tmp / "activity.csv"
        f.write_text(QT_CSV)
        r = tj(self.root, "redact", "--no-denylist", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        copy = self.tmp / "activity.redacted.csv"
        self.assertTrue(copy.is_file())
        self.assertNotIn(QT_ACCT, copy.read_text())
        self.assertEqual(f.read_text(), QT_CSV)
        self.assertFalse((self.root / "inputs_redact").exists())


@unittest.skipIf(os.name == "nt", "symlinks")
class TestRedactProjectSymlinks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = small_project(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_copy_path_symlink_refused_even_with_force(self):
        target = self.tmp / "elsewhere"
        target.mkdir()
        (target / R.TREE_MARKER).write_text("x")
        (target / "keep.txt").write_text("keep\n")
        (self.root / "inputs_redact").symlink_to(target)
        for extra in ((), ("--force",)):
            r = tj(self.root, "redact", "--no-denylist", *extra)
            self.assertEqual(r.returncode, 2)
            self.assertIn("is a symlink", flat(r))
        self.assertEqual(sorted(p.name for p in target.iterdir()),
                         sorted([R.TREE_MARKER, "keep.txt"]))
        self.assertTrue((self.root / "inputs_redact").is_symlink())

    def test_links_inside_inputs(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.csv").write_text(QT_CSV)
        inp = self.root / "inputs"
        # A linked file is read through (the run reads it too) and
        # written as a regular, redacted file; a linked folder is not
        # followed; a dangling link is skipped.
        (inp / "qt" / "linked.csv").symlink_to(outside / "secret.csv")
        (inp / "linkdir").symlink_to(outside)
        (inp / "qt" / "gone.csv").symlink_to(self.tmp / "missing.csv")
        r = tj(self.root, "redact", "--no-denylist")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.root / "inputs_redact"
        linked = out / "qt" / "linked.csv"
        self.assertTrue(linked.is_file() and not linked.is_symlink())
        self.assertNotIn(QT_ACCT, linked.read_text())
        self.assertFalse(os.path.lexists(out / "linkdir"))
        self.assertFalse(os.path.lexists(out / "qt" / "gone.csv"))
        text = flat(r)
        self.assertIn("inputs/linkdir not copied: a symlinked folder", text)
        self.assertIn("inputs/qt/gone.csv not copied", text)
        self.assertEqual((outside / "secret.csv").read_text(), QT_CSV)


class TestTreeNames(unittest.TestCase):
    def test_renamed_name_never_takes_an_existing_one(self):
        known = {QT_ACCT: "99900001"}                       # pii-ok
        files = [R._TreeFile(Path("qt") / f"{QT_ACCT}.csv"),
                 R._TreeFile(Path("qt") / "99900001.csv")]
        names = R._tree_names([Path("qt")], files, known, [])
        self.assertEqual(names[Path("qt") / "99900001.csv"].name,
                         "99900001.csv")
        self.assertEqual(names[Path("qt") / f"{QT_ACCT}.csv"].name,
                         "99900001-2.csv")

    def test_folder_named_after_an_account_is_renamed(self):
        known = {}
        files = [R._TreeFile(Path(IB_ACCT) / "a.csv")]
        names = R._tree_names([Path(IB_ACCT)], files, known, [])
        self.assertRegex(names[Path(IB_ACCT)].name, r"^U9990\d{3}$")
        self.assertEqual(names[Path(IB_ACCT) / "a.csv"].parent,
                         names[Path(IB_ACCT)])


if __name__ == "__main__":
    unittest.main()
