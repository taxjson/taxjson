"""One folder of exports for every year: the pre-release review's
smaller findings (synthetic projects only).

- A filed year's "inputs changed" sentence only when they did.
- A year folder that does not read ../inputs: the missing account
  folder named by its real path, and the setting to add.
- crypto-sends: the generated file named where it is; a hand-written
  crypto_sends.tt beside the generated one is refused.
- A file written into the shared exports says it applies to every year.
- new-year: last year's holdings lists commented out,
  missing_history.json copied.
- years / align: holdings paths and broker ids are not differences;
  align --write --all skips (and lists) a contradicting map line.
- redact of a year folder: holdings references follow renamed files.
- folder settings inside this project or another year's are refused.
- checklist: next-year done once the folder exists; the sanity step
  knows holdings/.
"""
import contextlib
import io
import json
import types
import unittest
from pathlib import Path

from _tmpfiles import private_dir
from test_fix_multi_year import multi, run_ok, tjs

from taxjson.lib import project_layout as PL


class TestFiledYearHint(unittest.TestCase):
    def test_only_when_the_inputs_changed(self):
        from taxjson.bin.taxjson_run import _filed_year_stale_hint
        root = Path(private_dir())
        (root / "filed").mkdir()
        (root / "filed" / "2024.json").write_text("{}")
        cfg = {"settings": {"year": 2024}}
        self.assertEqual(_filed_year_stale_hint(
            root, cfg, ["the last `taxjson run` did not finish: ..."]), "")
        self.assertIn("2024 is filed", _filed_year_stale_hint(
            root, cfg, ["inputs changed since the last full run "
                        "(added: inputs/margin/x.tt)"]))


class TestYearFolderWithoutInputsDir(unittest.TestCase):
    def test_names_the_folder_and_the_setting(self):
        top = multi("canada", years=(2024,))
        y = top / "2024"
        t = (y / "taxjson.toml").read_text().replace(
            'inputs_dir = "../inputs"\n', "").replace(
            'exports_dir = "../exports"\n', "")
        (y / "taxjson.toml").write_text(t)
        (y / "inputs").mkdir()
        r = tjs("-C", str(y), "run", "--no-input")
        out = r.stdout + r.stderr
        self.assertIn("no inputs dir for account 'margin' (inputs/margin)",
                      out)
        self.assertIn('add `inputs_dir = "../inputs"` to [settings]', out)

    def test_shared_folder_named_by_its_real_path(self):
        top = multi("canada", years=(2024,))
        y = top / "2024"
        t = (y / "taxjson.toml").read_text() + (
            '\n[accounts.rrsp]\ntype = "sheltered"\n')
        (y / "taxjson.toml").write_text(t)
        r = tjs("-C", str(y), "run", "--no-input")
        self.assertIn("no inputs dir for account 'rrsp' (../inputs/rrsp)",
                      r.stdout + r.stderr)


def _shared_year(top: Path, year: int = 2025) -> Path:
    y = top / str(year)
    y.mkdir(parents=True)
    (top / "inputs").mkdir(exist_ok=True)
    (y / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        'inputs_dir = "../inputs"\n')
    return y


class TestCryptoSendsPaths(unittest.TestCase):
    def test_generated_file_is_named_where_it_is(self):
        from taxjson.bin.taxjson_run import (_sends_json_shown,
                                             _sends_tt_shown)
        y = _shared_year(Path(private_dir()))
        self.assertEqual(_sends_tt_shown(y, "crypto"),
                         "work/crypto_sends/crypto/crypto_sends.tt")
        self.assertEqual(_sends_json_shown(y, "crypto"),
                         "../inputs/crypto/sends.json")

    def test_hand_written_and_generated_are_refused(self):
        from taxjson.bin.taxjson_run import _account_tt_files
        from taxjson.lib import crypto_sends as CS
        y = _shared_year(Path(private_dir()))
        acct = y.parent / "inputs" / "crypto"
        acct.mkdir(parents=True)
        (acct / "crypto_sends.tt").write_text(
            "# mine\nBUYSELL 2025-02-01 10:00:00 QZC 1 CAD 1 1\n")
        gen = CS.tt_path(y, "crypto")
        gen.parent.mkdir(parents=True)
        gen.write_text(CS.GENERATED_MARK + "\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit):
            _account_tt_files(acct, y / "work", "crypto")
        self.assertIn("have the same name", err.getvalue())
        self.assertIn("../inputs/crypto/crypto_sends.tt", err.getvalue())


class TestSharedInputsNote(unittest.TestCase):
    def test_said_for_the_shared_folder_only(self):
        from taxjson.bin import taxjson_run as R
        y = _shared_year(Path(private_dir()))
        (y.parent / "inputs" / "margin").mkdir()
        f = y.parent / "inputs" / "margin" / "opening_2025-01-01.tt"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(err):
            R._SHOWN_THIS_RUN.clear()
            R._shared_inputs_note(y, f, "taxjson opening")
            R._shared_inputs_note(y, y / "ticker.map", "taxjson opening")
        self.assertIn("../inputs/margin/opening_2025-01-01.tt is in the "
                      "exports folder every year's project shares: it "
                      "applies to every year", err.getvalue())
        self.assertNotIn("ticker.map", err.getvalue())

    def test_write_purchases_says_so(self):
        from taxjson.bin import taxjson_missing_history as M
        self.assertTrue(M._inside(Path("/a/b/c"), Path("/a/b")))
        self.assertFalse(M._inside(Path("/a/x"), Path("/a/b")))


class TestNewYear(unittest.TestCase):
    def test_holdings_lists_commented_and_missing_history_copied(self):
        from taxjson.lib.tomlcompat import tomllib
        top = multi("canada", years=(2024,))
        y = top / "2024"
        t = (y / "taxjson.toml").read_text().replace(
            '[accounts.margin]\ntype = "taxable"\n',
            '[accounts.margin]\ntype = "taxable"\nholdings = [\n'
            '  "holdings/margin_2024.toml",\n]\n')
        (y / "taxjson.toml").write_text(t)
        (y / "missing_history.json").write_text(
            '[{"symbol": "QZQ.TO", "account": "margin"}]\n')
        r = tjs("-C", str(top), "new-year", "2025")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = tomllib.loads((top / "2025" / "taxjson.toml").read_text())
        self.assertNotIn("holdings", doc["accounts"]["margin"])
        self.assertIn("# holdings = [", (top / "2025" / "taxjson.toml")
                      .read_text())
        self.assertEqual((top / "2025" / "missing_history.json")
                         .read_text(), (y / "missing_history.json")
                         .read_text())
        self.assertIn("missing_history.json", r.stdout)


class TestCompare(unittest.TestCase):
    def test_holdings_and_ids_are_not_differences(self):
        top = Path(private_dir())
        a, b = top / "2024", top / "2025"
        for d, h, acct in ((a, "holdings/x.toml", "55500001"),  # pii-ok
                           (b, "holdings/y.toml", "55500002")):  # pii-ok
            d.mkdir()
            (d / "taxjson.toml").write_text(
                f'[settings]\nyear = {d.name}\ncountry = "canada"\n\n'
                f'[accounts.margin]\ntype = "taxable"\n'
                f'holdings = ["{h}"]\naccount = "{acct}"\n'
                f'broker_accounts = ["{acct}"]\n')
        self.assertEqual(PL.compare(a, b)["keys"], [])

    def test_align_skips_a_contradicting_line(self):
        top = multi("canada", years=(2024, 2025))
        (top / "2024" / "ticker.map").write_text(
            "GLOBAL QZA.TO QZB.TO\nGLOBAL QZD.TO QZE.TO\n")
        (top / "2025" / "ticker.map").write_text("GLOBAL QZA.TO QZC.TO\n")
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = (top / "2025" / "ticker.map").read_text()
        self.assertIn("GLOBAL QZD.TO QZE.TO", text)
        self.assertNotIn("GLOBAL QZA.TO QZB.TO", text)
        self.assertIn("1 ticker.map line(s) of 2024 not brought over",
                      r.stdout + r.stderr)
        self.assertIn("GLOBAL QZA.TO QZB.TO", r.stdout + r.stderr)


class TestRedactHoldings(unittest.TestCase):
    def test_holdings_references_follow_the_renamed_files(self):
        from taxjson.lib.tomlcompat import tomllib
        top = multi("canada", years=(2024,))
        y = top / "2024"
        run_ok(self, y)
        (y / "holdings").mkdir()
        name = "U5550001_positions.toml"                    # pii-ok
        (y / "holdings" / name).write_text(
            '[meta]\naccount = "U5550001"\n\n'               # pii-ok
            '[[holding]]\nsymbol = "QZQ.TO"\nquantity = 50\n')
        outside = Path(private_dir()) / "elsewhere.toml"
        outside.write_text("[meta]\n")
        t = (y / "taxjson.toml").read_text().replace(
            '[accounts.margin]\ntype = "taxable"\n',
            '[accounts.margin]\ntype = "taxable"\n'
            f'holdings = ["holdings/{name}", "{outside}"]\n')
        (y / "taxjson.toml").write_text(t)
        r = tjs("-C", str(y), "redact")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        red = y / "inputs_redact"
        doc = tomllib.loads((red / "taxjson.toml").read_text())
        listed = doc["accounts"]["margin"]["holdings"]
        self.assertEqual(len(listed), 1)
        self.assertNotIn("5550001", listed[0])
        self.assertTrue((red / listed[0]).is_file(), listed)
        self.assertNotIn(str(outside), (red / "taxjson.toml").read_text())


class TestFolderSettingPlaces(unittest.TestCase):
    def test_inputs_inside_the_project_is_refused(self):
        y = _shared_year(Path(private_dir()))
        with self.assertRaises(PL.LayoutError) as e:
            PL.folder_setting(y, PL.INPUTS_KEY,
                              {PL.INPUTS_KEY: "exports_here"})
        self.assertIn("inside this project", str(e.exception))
        (y / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'inputs_dir = "inputs"\n')
        # Not a shared folder: the project reads its own inputs/.
        self.assertFalse(PL.shared_inputs(y))

    def test_a_folder_inside_another_year_is_refused(self):
        top = Path(private_dir())
        y = _shared_year(top, 2025)
        (top / "2024" / "inputs").mkdir(parents=True)
        for key in (PL.INPUTS_KEY, PL.HOLDINGS_KEY):
            with self.assertRaises(PL.LayoutError) as e:
                PL.folder_setting(y, key, {key: "../2024/inputs"})
            self.assertIn("inside 2024/, another year's project",
                          str(e.exception))


class TestChecklistSteps(unittest.TestCase):
    def test_next_year_done_once_its_folder_exists(self):
        from taxjson.lib.checklist import s_next_year
        top = Path(private_dir())
        y = _shared_year(top, 2024)
        ctx = types.SimpleNamespace(root=y, year=2024)
        self.assertEqual(s_next_year(ctx, None).status, "review")
        _shared_year(top, 2025)
        res = s_next_year(ctx, None)
        self.assertEqual(res.status, "done")
        self.assertIn("2025/ is started", res.detail)

    def test_sanity_step_names_holdings(self):
        from taxjson.lib.checklist import d_sanity
        y = _shared_year(Path(private_dir()), 2024)
        ctx = types.SimpleNamespace(root=y, accounts={"margin": {}})
        res = d_sanity(ctx)
        self.assertEqual(res.status, "manual")
        self.assertIn("no positions snapshot in holdings/", res.detail)


if __name__ == "__main__":
    unittest.main()
