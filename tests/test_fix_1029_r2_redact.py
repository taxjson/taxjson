"""Backlog fix round 2 of 2026-10-29, the `redact` group: `taxjson
redact` and the bug-report path. A redacted copy holds no names, e-mail
addresses, account numbers or absolute home paths, keeps what the run
needs to reproduce, and is never booked by the project it came from.

Synthetic data only (made-up ids 999000xx / 555000xx, the person
"Jordan Sampleperson", ticker SYN.TO).
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

REPO_ROOT = Path(__file__).resolve().parent.parent

CFG = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
       'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
       '[accounts.margin]\ntype = "taxable"\n')
TT = ("BUYSELL 2025-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
      "BUYSELL 2025-02-03 10:00:00 SYN.TO -10 CAD 20 200 0\n")


def tjs(root, *args, cwd=None):
    """`taxjson -C root <args>`, captured at width 0, offline."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=cwd or root, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


def project(root: Path, year: int = 2025, tt: str = TT) -> Path:
    """A one-account single-folder project (inputs/margin/trades.tt)."""
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(CFG.format(year=year))
    (root / "ticker.map").write_text("")
    (root / "inputs" / "margin" / "trades.tt").write_text(tt)
    return root


def year_folder(top: Path, year: int = 2025, extra: str = "",
                tt: str = TT) -> Path:
    """top/<year>, a year folder of a project whose exports every year
    shares (top/inputs/margin/trades.tt); `extra` ends its taxjson.toml
    (the [accounts.margin] table)."""
    p = top / str(year)
    p.mkdir(parents=True)
    acct = top / "inputs" / "margin"
    if not acct.is_dir():
        acct.mkdir(parents=True)
        (acct / "trades.tt").write_text(tt)
    (p / "taxjson.toml").write_text(
        CFG.format(year=year).replace(
            "[accounts.margin]", 'inputs_dir = "../inputs"\n'
                                 '[accounts.margin]') + extra)
    (p / "ticker.map").write_text("")
    return p


def toml(path: Path) -> dict:
    from taxjson.lib.tomlcompat import tomllib
    return tomllib.loads(path.read_text(encoding="utf-8"))


def gain(root: Path) -> float:
    r = tjs(root, "sum", "--json")
    doc = json.loads(r.stdout)
    return doc["filing"]["accounts"][0]["gain"]


# ------------------------------------------------------------- TJ-0476

class TestRunSkipsARedactedCopyBesideItsExport(unittest.TestCase):
    """`taxjson redact FILE` writes NAME.redacted.EXT beside the export;
    in an inputs/<account>/ folder the next run read both, booking
    every row twice (the copy's placeholder account id kept the
    de-duplication from merging them) with no word."""

    def test_the_copy_is_named_and_not_booked(self):
        with tempfile.TemporaryDirectory() as td:
            root = project(Path(td))
            r = tjs(root, "redact", "--no-denylist",
                    "inputs/margin/trades.tt")
            self.assertEqual(r.returncode, 0, r.stderr)
            copy = root / "inputs" / "margin" / "trades.redacted.tt"
            self.assertTrue(copy.is_file())
            r = tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(gain(root), 100.0)
            self.assertIn("warning: inputs/margin/trades.redacted.tt is "
                          "not read: a copy `taxjson redact` made of the "
                          "export beside it", r.stderr)

    def test_a_folder_of_redacted_copies_alone_is_read(self):
        # A sample project built from FILE-mode copies (no original
        # beside them) still runs.
        with tempfile.TemporaryDirectory() as td:
            root = project(Path(td))
            acct = root / "inputs" / "margin"
            (acct / "trades.tt").rename(acct / "trades.redacted.tt")
            self.assertFalse(PL.redacted_copy_beside_exports(
                acct / "trades.redacted.tt"))
            r = tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("is not read", r.stderr)
            self.assertEqual(gain(root), 100.0)

    def test_the_predicate(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "a.csv").write_text("x\n")
            (d / "a.redacted.csv").write_text("x\n")
            (d / "B.REDACTED.TT").write_text("x\n")
            self.assertTrue(PL.redacted_copy_beside_exports(
                d / "a.redacted.csv"))
            self.assertTrue(PL.redacted_copy_beside_exports(
                d / "B.REDACTED.TT"))
            self.assertFalse(PL.redacted_copy_beside_exports(d / "a.csv"))

# ----------------------------------------------------- TJ-0479, TJ-0569

QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")
QT_WIRE = ("2024-01-02,2024-01-02,WDR,,WIRE TO BENEFICIARY JORDAN "
           "SAMPLEPERSON,0,0,-100.00,0,-100.00,CAD,Withdrawals,99900090,"
           "Margin\n")
QT_DIV = ("2024-08-15,2024-08-15,DIV,AAPL,APPLE INC CASH DIV ON 10 SHS,0,"
          "0,5.00,0,5.00,USD,Dividends,99900090,Margin\n")


class TestADataRowIsNeverAHeader(unittest.TestCase):
    """A data row holding a holder word (BENEFICIARY, HOLDER ...) was
    read as a column header: its name kept with no review line, every
    later Description blanked (or the copy refused); a digit-free data
    row was too, and its alias kept; a two-column header was never one,
    so header and row were blanked whole."""

    def test_a_holder_word_row_is_data_wherever_it_is(self):
        for src in (QT_HEAD + QT_WIRE + QT_DIV, QT_HEAD + QT_DIV + QT_WIRE):
            with self.subTest(first=src.splitlines()[1][:14]):
                out, rep = R.redact_text(src)
                self.assertNotIn("SAMPLEPERSON", out)
                self.assertIn("WIRE TO BENEFICIARY REDACTED", out)
                self.assertIn("APPLE INC CASH DIV ON 10 SHS", out)
                self.assertEqual(rep.names, 1)

    def test_a_plan_holders_name_is_listed(self):
        row = ("2024-01-02,2024-01-02,TFI,,TRANSFER IN FROM PLAN HOLDER "
               "JORDAN SAMPLEPERSON,0,0,100.00,0,100.00,CAD,Transfers,"
               "99900090,LIRA\n")
        out, rep = R.redact_text(QT_HEAD + row + QT_DIV)
        self.assertIn((2, "name-like words after a plan holder's word in "
                          "column 'Description'"), rep.review)
        self.assertIn("APPLE INC CASH DIV ON 10 SHS", out)

    def test_a_digit_free_data_row_keeps_its_header(self):
        out, rep = R.redact_text("AccountAlias,Symbol,Description\n"
                                 "Sample Person,SYN.TO,Dividend\n")
        self.assertEqual(out, "AccountAlias,Symbol,Description\n"
                              "REDACTED,SYN.TO,Dividend\n")
        self.assertTrue(rep.found_anything())

    def test_a_two_column_header(self):
        out, _rep = R.redact_text("AccountAlias,Symbol\n"
                                  "Sample Person,SYN.TO\n")
        self.assertEqual(out, "AccountAlias,Symbol\nREDACTED,SYN.TO\n")
        # A label and its value stay an identity line (no header).
        out, _rep = R.redact_text("Name,Jordan Sampleperson\n"
                                  + QT_HEAD + QT_DIV)
        self.assertNotIn("Sampleperson", out)
        self.assertIn(QT_HEAD, out)

    def test_a_new_header_still_starts_a_table(self):
        # A second table of another width, and one as wide whose cells
        # name columns, each take over the columns.
        src = ("Date,Symbol,Amount\n2024-01-02,SYN.TO,5.00\n"
               "Date,AccountAlias,Amount\n2024-01-03,Sample Person,6.00\n")
        out, _rep = R.redact_text(src)
        self.assertIn("2024-01-03,REDACTED,6.00", out)
        self.assertIn("2024-01-02,SYN.TO,5.00", out)

    def test_the_questrade_copy_is_written(self):
        # The row second in the file: the copy was refused ("a number
        # would change in the redacted copy").
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "qt.csv"
            f.write_text(QT_HEAD + QT_WIRE + QT_DIV)
            r = tjs(td, "redact", "--no-denylist", str(f))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            copy = (Path(td) / "qt.redacted.csv").read_text()
        self.assertNotIn("SAMPLEPERSON", copy)
        self.assertIn("APPLE INC CASH DIV ON 10 SHS", copy)


# ------------------------------------------------------------- TJ-0579

class TestAShortQueryIdIsReplacedInItsFieldOnly(unittest.TestCase):
    """A 4-digit query id was replaced through the whole taxjson.toml
    of the copy: `query_id = "2025"` made `year = 2025` 9999, and the
    copy's run stopped on the year."""

    def test_the_year_stays(self):
        with tempfile.TemporaryDirectory() as td:
            p = year_folder(Path(td), extra='query_id = "2025"  # 2025\n')
            r = tjs(p, "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            cfg = p / "inputs_redact" / "taxjson.toml"
            doc = toml(cfg)
            self.assertEqual(doc["settings"]["year"], 2025)
            self.assertEqual(doc["accounts"]["margin"]["query_id"], "9999")
            # The comment after the value is the user's: kept.
            self.assertIn('query_id = "9999"  # 2025', cfg.read_text())
            r = tjs(p / "inputs_redact", "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_an_inline_table_and_an_array(self):
        text = ('[settings]\nyear = 2025\n[accounts]\n'
                'ib = { type = "taxable", query_id = "2025" }\n'
                '[accounts.qt]\nbroker_accounts = [\n  "2025",\n]\n')
        out, rep = R.redact_project_text(
            text, [], R.IdTable(), ([], ["2025"]))
        self.assertIn("year = 2025\n", out)
        self.assertIn('query_id = "9999" }', out)
        self.assertIn('  "9999",\n', out)
        self.assertEqual(rep.accounts, {"2025": "9999"})


# ------------------------------------------------------------- TJ-0581

TT_2Y = ("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 30 300 0\n"
         "BUYSELL 2024-02-05 10:00:00 SYN.TO -10 CAD 10 100 0\n" + TT)
EST = '\n[estimate]\nother_income = 40000\n'


def two_years(top: Path) -> Path:
    """2024 closed with a 200 loss, carried into 2025 by its
    prior_year_record; the 2025 year folder."""
    for year in (2024, 2025):
        p = year_folder(top, year, tt=TT_2Y)
        text = (p / "taxjson.toml").read_text().replace(
            'inputs_dir = "../inputs"\n',
            'inputs_dir = "../inputs"\nprovince = "ON"\n'
            + ('prior_year_record = "../2024/filed/2024.json"\n'
               if year == 2025 else ""))
        (p / "taxjson.toml").write_text(text + EST)
        r = tjs(p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        if year == 2024:
            r = tjs(p, "close-year", "--yes")
            assert r.returncode == 0, r.stdout + r.stderr
    return p


def losses_opening(root: Path) -> float:
    r = tjs(root, "estimate", "--json")
    return json.loads(r.stdout)["estimate"]["losses_opening"]


class TestTheCopyCarriesThePriorYearRecord(unittest.TestCase):
    """The copy of a year folder kept `prior_year_record =
    "../2024/filed/2024.json"` without the record: from inside the
    copy the path named nothing, and its estimate dropped the loss
    carried in (and the tax rose)."""

    def test_the_copys_estimate_carries_the_loss(self):
        with tempfile.TemporaryDirectory() as td:
            p = two_years(Path(td))
            self.assertEqual(losses_opening(p), 200.0)
            r = tjs(p, "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("and prior/2024.json (the close-year record its "
                          "estimate carries from)", r.stdout)
            dst = p / "inputs_redact"
            self.assertEqual(toml(dst / "taxjson.toml")["settings"]
                             ["prior_year_record"], "prior/2024.json")
            rec = json.loads((dst / "prior" / "2024.json").read_text())
            self.assertEqual(rec["carryforwards"]["net_capital_loss"]
                             ["closing"], 200.0)
            r = tjs(dst, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(losses_opening(dst), 200.0)

    def test_a_record_that_is_not_there_is_not_named(self):
        with tempfile.TemporaryDirectory() as td:
            p = year_folder(Path(td))
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                'inputs_dir = "../inputs"\n',
                'inputs_dir = "../inputs"\n'
                'prior_year_record = "../2024/filed/2024.json"\n'))
            r = tjs(p, "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = toml(p / "inputs_redact" / "taxjson.toml")
            self.assertNotIn("prior_year_record", doc["settings"])
            self.assertFalse((p / "inputs_redact" / "prior").exists())


# ------------------------------------------------------------- TJ-0580

class TestALinkedConfigIsCopied(unittest.TestCase):
    """A year folder whose taxjson.toml is a link to config-real.toml
    beside it: the copy, said to be runnable, had no taxjson.toml, with
    no word of it."""

    def test_a_link_inside_the_project_is_followed(self):
        with tempfile.TemporaryDirectory() as td:
            p = year_folder(Path(td))
            cfg = p / "taxjson.toml"
            cfg.rename(p / "config-real.toml")
            cfg.symlink_to("config-real.toml")
            r = tjs(p, "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            copy = p / "inputs_redact" / "taxjson.toml"
            self.assertFalse(copy.is_symlink())
            self.assertEqual(toml(copy)["settings"]["year"], 2025)
            self.assertIn("`taxjson -C inputs_redact/ run` runs it (and",
                          r.stdout)
            r = tjs(p / "inputs_redact", "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_link_out_of_the_project_is_named(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as elsewhere:
            p = year_folder(Path(td))
            real = Path(elsewhere) / "config-real.toml"
            (p / "taxjson.toml").rename(real)
            (p / "taxjson.toml").symlink_to(real)
            r = tjs(p, "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((p / "inputs_redact" / "taxjson.toml").exists())
            self.assertIn("warning: taxjson.toml not copied: a symlink to a "
                          "file outside the folder holding the project",
                          r.stderr)
            self.assertNotIn("runs it", r.stdout)
            self.assertIn("its taxjson.toml was not copied (the warning "
                          "above): add one before running it.", r.stdout)


# ------------------------------------------------------------- TJ-0486

class TestTheRunHintIsQuoted(unittest.TestCase):
    """`taxjson -C <copy> run`, the closing line's hint, was not
    shell-quoted: pasted, a path with a space stopped with "invalid
    choice"."""

    def test_a_path_with_a_space(self):
        import re
        import shlex
        with tempfile.TemporaryDirectory() as td:
            p = year_folder(Path(td) / "sp ace")
            r = tjs(p, "redact", "--no-denylist", cwd=td)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            hint = re.search(r"`(taxjson -C .*? run)` runs it", r.stdout)
            argv = shlex.split(hint.group(1))
            self.assertEqual(argv[:2], ["taxjson", "-C"])
            self.assertEqual(Path(argv[2]), p / "inputs_redact")
            self.assertEqual(argv[3:], ["run"])


# ----------------------------------------------------- TJ-0480, TJ-0484

class TestTheCopysCheckReadsAllOfIt(unittest.TestCase):
    """`taxjson -C inputs_redact redact --check`, the check the
    troubleshooting page gives for a copy laid out as a project, read
    its inputs/ only: an e-mail address or an account id in the copy's
    taxjson.toml or ticker.map passed with "Nothing to redact"; and the
    work/ and reports/ a run of the copy writes there (holding this
    computer's folder paths) were never named."""

    def _copy(self, td):
        p = year_folder(Path(td))
        r = tjs(p, "redact", "--no-denylist")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("runs it (and writes work/ and reports/ there, "
                      "holding this computer's folder paths: delete them "
                      "before sharing the copy).", r.stdout)
        return p / "inputs_redact"

    def test_a_clean_copy_passes(self):
        with tempfile.TemporaryDirectory() as td:
            dst = self._copy(td)
            r = tjs(dst, "redact", "--check", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("==> Checking the redacted copy (nothing is "
                          "written)", r.stdout)
            self.assertIn("Done. Nothing to redact", r.stdout)

    def test_the_settings_files_are_read(self):
        with tempfile.TemporaryDirectory() as td:
            dst = self._copy(td)
            with open(dst / "ticker.map", "a") as fh:
                fh.write("# jordan.sampleperson@example.org\n")
            cfg = dst / "taxjson.toml"
            cfg.write_text("# IB account U1234567\n"  # pii-ok: synthetic
                           + cfg.read_text())
            r = tjs(dst, "redact", "--check", "--no-denylist", "--details")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("note: ticker.map: 1 e-mail address", r.stdout)
            self.assertIn("note: taxjson.toml: 1 account id", r.stdout)

    def test_what_a_run_of_the_copy_wrote_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            dst = self._copy(td)
            r = tjs(dst, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = tjs(dst, "redact", "--check", "--no-denylist")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("warning: reports/ and work/ in the copy: not "
                          "part of it — what a run of the copy wrote "
                          "(work/ holds this computer's folder paths)",
                          r.stderr)
            self.assertIn("Delete them before sharing the copy.", r.stderr)


# ------------------------------------------------------------- TJ-0481

class TestNamesInFileNames(unittest.TestCase):
    """An e-mail address or a person's name in an export's file or
    folder name was copied into inputs_redact/ as it was, with no word,
    though getting-started said names were replaced "file names
    included"."""

    def test_the_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = project(Path(td))
            acct = root / "inputs" / "margin"
            (acct / "trades.tt").rename(
                acct / "jordan.sampleperson@example.org trades.tt")
            (root / "inputs" / "Jordan Sampleperson").mkdir()
            (root / "inputs" / "Jordan Sampleperson" / "notes.tt").write_text(
                TT)
            r = tjs(root, "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            names = sorted(x.relative_to(root / "inputs_redact").as_posix()
                           for x in (root / "inputs_redact").rglob("*"))
            self.assertIn("margin/redacted@example.com trades.tt", names)
            self.assertNotIn("sampleperson@", " ".join(names))
            self.assertIn("1 name held an account id, an e-mail address or "
                          "a denylisted word; renamed in the copy",
                          r.stdout)
            # A name is listed (once for the folder), not guessed at.
            self.assertIn("warning: name-like words in 1 file or folder "
                          "name of the copy: rename it by hand before "
                          "sharing it\n  inputs_redact/Jordan Sampleperson/\n",
                          r.stdout)
            r = tjs(root, "redact", "--no-denylist", "--check")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("\n  inputs/Jordan Sampleperson/\n", r.stdout)

    def test_a_file(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("jordan.sampleperson@example.org 2025.tt",
                         "Jordan Sampleperson 2025.tt"):
                (Path(td) / name).write_text(TT)
                r = tjs(td, "redact", "--no-denylist", name)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            made = sorted(x.name for x in Path(td).iterdir()
                          if ".redacted." in x.name)
            self.assertEqual(made, ["Jordan Sampleperson 2025.redacted.tt",
                                    "redacted@example.com 2025.redacted.tt"])
            self.assertIn("the file name holds name-like words: rename "
                          "the copy by hand before sharing it", r.stdout)
            r = tjs(td, "redact", "--no-denylist", "--check",
                    "jordan.sampleperson@example.org 2025.tt")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("e-mail addresses: 1", r.stdout)


# ------------------------------------------------------------- TJ-0487

class TestAnAccentedAccountName(unittest.TestCase):
    """An account named 'céli' (French for TFSA) was refused with "use
    letters, digits, ...", though é is a letter: the rule is ASCII."""

    def test_the_message_says_ascii_and_names_the_spelling(self):
        with tempfile.TemporaryDirectory() as td:
            root = project(Path(td))
            (root / "inputs" / "margin").rename(root / "inputs" / "céli")
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace("[accounts.margin]",
                                                   '[accounts."céli"]'))
            r = tjs(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            out = " ".join((r.stdout + r.stderr).split())
            self.assertIn("[accounts.'céli'] is not a valid account name — "
                          "use a-z or A-Z letters (no accents), digits, "
                          "'_', '-' or '.'", out)
            self.assertIn("'celi' would do: rename the table "
                          "[accounts.celi] and the inputs/céli/ folder.", out)
            r = tjs(root, "sum", "céli")
            self.assertIn("(a-z or A-Z letters (no accents), digits, '_', "
                          "'-' or '.')", " ".join(r.stderr.split()))

    def test_no_spelling_is_offered_when_none_would_do(self):
        from taxjson.lib.config_check import account_name_problem
        self.assertNotIn("would do", account_name_problem("../x"))
        self.assertNotIn("would do", account_name_problem("x_gaïns"))
        self.assertEqual(account_name_problem("celi"), "")


# ------------------------------------------------------------- TJ-0571

class TestTheProjectAboveIsQuoted(unittest.TestCase):
    """Run in a project's inputs/margin, the advice named the project
    above as `cd <path>` and `tjs -C <path> sum` unquoted: a path with
    a space split into two arguments."""

    def test_a_path_with_a_space(self):
        with tempfile.TemporaryDirectory() as td:
            root = project(Path(td) / "synthetic project")
            r = tjs(root / "inputs" / "margin", "sum", cwd=td)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("The project is in synthetic project: `cd "
                          "'synthetic project'`, or `", r.stderr)
            self.assertIn(" -C 'synthetic project' sum`.", r.stderr)


if __name__ == "__main__":
    unittest.main()
