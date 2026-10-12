"""GitHub issues #34-#44, #56, #57: editing taxjson.toml (synthetic data).

Every edit `align`, `migrate` and `new-year` make goes through
project_layout.set_key_text, which reads the keys as tomllib does and
reads the result back: it must be the original with exactly the asked
key changed, else LayoutError and nothing is written.

- #34: `migrate` (one shared tobase.map) with [settings] an inline table
  sets the key inside it; an edit that cannot be made changes no file.
- #35: `align --write` changes nothing unless every chosen edit can be
  made and written (ticker.map put back when taxjson.toml fails).
- #36: an account named with a dot is one name: compared, kept private
  (ids, holdings) and written as `[accounts."margin.one"]`.
- #37/#38/#39: `new-year` sets `year` spelt `+2024` / `2_024` / `0x7e8`,
  in an inline [settings] table or as dotted keys, and puts
  prior_year_record in [settings].
- #40: a `new-year` that fails leaves no taxjson.toml (built in a
  staging folder, moved into place when complete).
- #41: `align` adds and removes a [[distributions]] array of tables.
- #42: the accounts' order is a difference.
- #43/#44: `migrate --to-years` keeps a custom holdings_dir and a
  relative ticker.map link pointing where they did.
- #56: a taxjson.toml that does not read is an error, never "the same".
- #57: `new-year` in Canada keeps the cutoff the previous year used by
  default (option_grant_timing_since), so a premium is taxed once.
"""
import argparse
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from _style import CapturedWidth
from _tmpfiles import private_dir
from tax_rules import rule, rule_absent
from test_fix_multi_year import multi, run_ok, single, tjs

from taxjson.lib import project_layout as PL
from taxjson.lib.tomlcompat import tomllib

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cfg(year=2024, accounts=("margin",), extra="", country="canada",
         shared=True) -> str:
    cur = "CAD" if country == "canada" else "USD"
    return (f'[settings]\ncountry = "{country}"\nyear = {year}\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            + ('inputs_dir = "../inputs"\n' if shared else "") + extra
            + "".join(f'\n[accounts.{a}]\ntype = "taxable"\n'
                      for a in accounts))


def _years(*texts) -> Path:
    """A folder of year projects, one per (year, taxjson.toml text)."""
    top = Path(private_dir()) / "taxes"
    (top / "inputs").mkdir(parents=True)
    for y, text in texts:
        (top / str(y)).mkdir()
        (top / str(y) / "taxjson.toml").write_text(text)
        (top / str(y) / "ticker.map").write_text("")
    return top


def _doc(folder: Path):
    return tomllib.loads((folder / "taxjson.toml").read_text())


def _out(r) -> str:
    return r.stdout + r.stderr


class TestSetKeyText(unittest.TestCase):
    """The editing layer itself."""

    def test_inline_table_set_and_removed(self):
        text = 'settings = {country = "canada", year = 2024}  # mine\n'
        out = PL.set_key_text(text, "settings.year", 2025)
        self.assertEqual(tomllib.loads(out)["settings"],
                         {"country": "canada", "year": 2025})
        self.assertIn("# mine", out)
        out = PL.set_key_text(out, "settings.tobase_map", "../tobase.map")
        self.assertEqual(tomllib.loads(out)["settings"]["tobase_map"],
                         "../tobase.map")
        out = PL.set_key_text(out, "settings.country", None)
        self.assertNotIn("country", tomllib.loads(out)["settings"])

    def test_dotted_keys_stay_in_their_table(self):
        text = 'settings.year = 2024\nsettings.country = "canada"\n\n' \
               '[accounts.margin]\ntype = "taxable"\n'
        out = PL.set_key_text(text, "settings.prior_year_record", "x",
                              after="year")
        doc = tomllib.loads(out)
        self.assertEqual(doc["settings"]["prior_year_record"], "x")
        self.assertNotIn("prior_year_record", doc)
        self.assertIn('settings.year = 2024\nsettings.prior_year_record',
                      out)

    def test_any_integer_spelling(self):
        for lit in ("+2024", "2_024", "0x7e8", "0o3750", "0b11111101000"):
            text = f"[settings]\nyear = {lit}  # the year\n"
            out = PL.set_key_text(text, ("settings", "year"), 2025)
            self.assertEqual(tomllib.loads(out)["settings"]["year"], 2025,
                             lit)
            self.assertIn("# the year", out)

    def test_array_of_tables(self):
        text = '[settings]\nyear = 2025\n\n[accounts.a]\ntype = "taxable"\n'
        dist = [{"symbol": "QZZQ.TO", "per_share": 0.25},
                {"symbol": "QZZR.TO", "per_share": 0.5}]
        out = PL.set_key_text(text, ("distributions",), dist)
        self.assertEqual(tomllib.loads(out)["distributions"], dist)
        self.assertEqual(out.count("[[distributions]]"), 2)
        back = PL.set_key_text(out, ("distributions",), None)
        self.assertNotIn("distributions", tomllib.loads(back))
        self.assertIn("# [[distributions]]", back)
        again = PL.set_key_text(out, ("distributions",), dist[:1])
        self.assertEqual(tomllib.loads(again)["distributions"], dist[:1])

    def test_a_name_holding_a_dot(self):
        text = '[settings]\nyear = 2025\n\n[accounts."margin.one"]\n' \
               'type = "taxable"\nexercise_fee = 2\n'
        out = PL.set_key_text(text, ("accounts", "margin.one",
                                     "exercise_fee"), 1)
        doc = tomllib.loads(out)
        self.assertEqual(doc["accounts"], {"margin.one": {
            "type": "taxable", "exercise_fee": 1}})

    def test_root_key_goes_before_the_first_table(self):
        text = "# my settings\n\n[settings]\nyear = 2025\n"
        out = PL.set_key_text(text, ("schema",), 2)
        self.assertEqual(tomllib.loads(out), {"schema": 2,
                                              "settings": {"year": 2025}})

    def test_an_edit_that_would_read_differently_is_refused(self):
        text = "[settings]\nyear = 2024\n"
        with mock.patch.object(PL, "_edit_text",
                               return_value="[settings]\nyear = 2023\n"):
            with self.assertRaises(PL.LayoutError) as cm:
                PL.set_key_text(text, "settings.year", 2025)
        self.assertIn("write it out as a [table]", str(cm.exception))
        with mock.patch.object(PL, "_edit_text",
                               return_value="[settings\n"):
            with self.assertRaises(PL.LayoutError):
                PL.set_key_text(text, "settings.year", 2025)
        # A path through a value that is not a table, and a text that
        # does not read, are refused too.
        with self.assertRaises(PL.LayoutError):
            PL.set_key_text('settings = 5\n', "settings.year", 2025)
        with self.assertRaises(PL.LayoutError):
            PL.set_key_text('[settings\n', "settings.year", 2025)


class TestSharedTobaseMigration(unittest.TestCase):
    """#34."""

    def _top(self):
        top = _years((2024, 'settings = {country = "canada", year = 2024, '
                            'inputs_dir = "../inputs"}\n'))
        (top / "2024" / "tobase.map").write_text("TOBASE QZZQ.US QZZQ.TO\n")
        return top

    def test_inline_settings(self):
        top = self._top()
        r = tjs("-C", str(top), "migrate")
        self.assertEqual(r.returncode, 0, _out(r))
        st = _doc(top / "2024")["settings"]
        self.assertEqual(st, {"country": "canada", "year": 2024,
                              "inputs_dir": "../inputs",
                              "tobase_map": "../tobase.map",
                              # the layout migrated to (lib/requires)
                              "requires_taxjson": ">=0.27.1"})
        self.assertTrue((top / "tobase.map").is_file())
        self.assertFalse((top / "2024" / "tobase.map").exists())

    def test_a_config_edit_that_fails_changes_no_file(self):
        from taxjson.lib import tobase_map as TB
        top = self._top()
        before = (top / "2024" / "taxjson.toml").read_text()
        plan = TB.plan_shared(top)
        with mock.patch.object(PL, "set_key_after",
                               side_effect=PL.LayoutError("no")):
            with self.assertRaises(PL.LayoutError):
                TB.apply_shared(plan)
        self.assertFalse((top / "tobase.map").exists())
        self.assertTrue((top / "2024" / "tobase.map").is_file())
        self.assertEqual((top / "2024" / "taxjson.toml").read_text(),
                         before)


_DIST = ('\n[[distributions]]\nsymbol = "QZZQ.TO"\n'
         'record_date = 2024-12-20\nper_share = 0.25\n')


def _align(folder: Path, frm: int):
    """`align --from FRM --write --all` in this process (for mocks)."""
    from taxjson.bin import taxjson_run as R
    args = argparse.Namespace(dir=str(folder), from_year=frm, write=True,
                              all=True, json=False)
    with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
        try:
            R.cmd_align(args)
        except SystemExit as e:
            return e.code
    return 0


class TestAlign(unittest.TestCase):
    def _pair(self):
        top = _years((2024, _cfg(2024) + _DIST), (2025, _cfg(2025)))
        (top / "2024" / "ticker.map").write_text("QUOTE QZZQ.TO QZZQ.TO\n")
        return top

    def test_map_and_array_of_tables_together(self):
        """#35 / #41: the map line and [[distributions]] both."""
        top = self._pair()
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertIn("QUOTE QZZQ.TO QZZQ.TO",
                      (top / "2025" / "ticker.map").read_text())
        self.assertEqual(_doc(top / "2025")["distributions"],
                         _doc(top / "2024")["distributions"])

    def test_array_of_tables_removed(self):
        """#41: the reverse direction."""
        top = _years((2024, _cfg(2024)), (2025, _cfg(2025) + _DIST))
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertNotIn("distributions", _doc(top / "2025"))
        self.assertEqual(PL.compare(top / "2025", top / "2024")["keys"], [])

    def test_a_config_edit_that_fails_writes_nothing(self):
        """#35."""
        top = self._pair()
        with mock.patch.object(PL, "set_key_text",
                               side_effect=PL.LayoutError("no")):
            self.assertEqual(_align(top / "2025", 2024), 2)
        self.assertEqual((top / "2025" / "ticker.map").read_text(), "")
        self.assertNotIn("distributions", _doc(top / "2025"))

    def test_a_config_write_that_fails_puts_the_map_back(self):
        """#35: all or nothing when the second write fails."""
        from taxjson.lib import safe_write
        top = self._pair()
        real = safe_write.write_user_file

        def _fail_toml(path, *a, **k):
            if Path(path).name == "taxjson.toml":
                raise OSError(28, "No space left on device")
            return real(path, *a, **k)
        with mock.patch.object(safe_write, "write_user_file", _fail_toml):
            self.assertEqual(_align(top / "2025", 2024), 2)
        self.assertEqual((top / "2025" / "ticker.map").read_text(), "")
        self.assertNotIn("distributions", _doc(top / "2025"))

    def test_dotted_account_name(self):
        """#36: one name; its ids never shown; written in its table."""
        ids = {2024: "U5550001", 2025: "U5550002"}         # pii-ok
        top = _years(*[(y, _cfg(y, accounts=()) + (
            f'\n[accounts."margin.one"]\ntype = "taxable"\n'
            f'exercise_fee = {fee}\naccount = "{ids[y]}"\n'
            f'holdings = ["holdings/{y}.toml"]\n'))
            for y, fee in ((2024, 1), (2025, 2))])
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--json")
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertNotIn(ids[2024], r.stdout)
        self.assertNotIn("holdings/", r.stdout)
        keys = json.loads(r.stdout)["keys"]
        self.assertEqual([k["path"] for k in keys],
                         [["accounts", "margin.one", "exercise_fee"]])
        self.assertEqual(keys[0]["key"], 'accounts."margin.one".exercise_fee')
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, _out(r))
        accts = _doc(top / "2025")["accounts"]
        self.assertEqual(list(accts), ["margin.one"])
        self.assertEqual(accts["margin.one"]["exercise_fee"], 1)
        self.assertEqual(accts["margin.one"]["account"], ids[2025])

    def test_account_order_is_a_difference(self):
        """#42."""
        top = _years((2024, _cfg(2024, ("a", "b"))),
                     (2025, _cfg(2025, ("b", "a"))))
        keys = PL.compare(top / "2025", top / "2024")["keys"]
        self.assertEqual(keys, [{"key": PL.ACCOUNT_ORDER_KEY,
                                 "path": ["accounts"], "order": True,
                                 "here": ["b", "a"], "there": ["a", "b"]}])
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024")
        self.assertIn("accounts (order)", r.stdout)
        r = tjs("-C", str(top), "years", "--diff", "2024", "2025")
        self.assertIn("accounts (order)", r.stdout)
        before = (top / "2025" / "taxjson.toml").read_text()
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertIn("another order", _out(r))
        self.assertEqual((top / "2025" / "taxjson.toml").read_text(), before)

    def test_an_unreadable_config_is_an_error(self):
        """#56."""
        top = _years((2024, "[settings\ninvalid TOML"), (2025, _cfg(2025)))
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024", "--json")
        self.assertEqual(r.returncode, 2, _out(r))
        self.assertIn("2024", r.stderr)
        self.assertIn("not valid TOML", r.stderr)
        self.assertEqual(r.stdout, "")
        r = tjs("-C", str(top), "years", "--diff", "2024", "2025")
        self.assertEqual(r.returncode, 2, _out(r))
        rep = PL.years_report(top)
        old = next(y for y in rep["years"] if y["year"] == 2024)
        self.assertIsNone(old["differs_from_newest"])
        self.assertIn("not valid TOML", old["problem"])

    def test_an_unreadable_map_is_an_error(self):
        """#56: a ticker.map link to a missing file is not an empty map."""
        top = _years((2024, _cfg(2024)), (2025, _cfg(2025)))
        (top / "2024" / "ticker.map").unlink()
        (top / "2024" / "ticker.map").symlink_to("gone.map")
        with self.assertRaises(PL.LayoutError):
            PL.compare(top / "2025", top / "2024")


class TestNewYear(unittest.TestCase):
    def _new_year(self, text: str, year=2025):
        top = _years((2024, text))
        r = tjs("-C", str(top), "new-year", str(year))
        return top, r

    def test_nonstandard_integer_year(self):
        """#37."""
        for lit in ("+2024", "2_024", "0x7e8"):
            top, r = self._new_year(_cfg().replace("year = 2024",
                                                   f"year = {lit}"))
            self.assertEqual(r.returncode, 0, _out(r))
            st = _doc(top / "2025")["settings"]
            self.assertEqual(st["year"], 2025, lit)
            self.assertEqual(st["prior_year_record"],
                             "../2024/filed/2024.json")

    def test_inline_settings(self):
        """#38."""
        top, r = self._new_year(
            'settings = {country = "canada", year = 2024, '
            'inputs_dir = "../inputs", option_grant_timing_since = 2023}\n')
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertEqual(_doc(top / "2025"), {"settings": {
            "country": "canada", "year": 2025, "inputs_dir": "../inputs",
            "option_grant_timing_since": 2023,
            "prior_year_record": "../2024/filed/2024.json",
            # shared exports (inputs_dir): lib/requires
            "requires_taxjson": ">=0.26.0"}})

    def test_dotted_settings(self):
        """#39."""
        top, r = self._new_year(
            'settings.country = "canada"\nsettings.year = 2024\n'
            'settings.inputs_dir = "../inputs"\n'
            'settings.option_grant_timing_since = 2023\n')
        self.assertEqual(r.returncode, 0, _out(r))
        doc = _doc(top / "2025")
        self.assertEqual(set(doc), {"settings"})
        self.assertEqual(doc["settings"]["prior_year_record"],
                         "../2024/filed/2024.json")
        self.assertEqual(doc["settings"]["year"], 2025)

    def test_a_failure_leaves_nothing_to_block_a_retry(self):
        """#40."""
        top = _years((2024, _cfg()))
        (top / "2025").mkdir()
        (top / "2025" / "holdings").write_text("pre-existing file\n")
        r = tjs("-C", str(top), "new-year", "2025")
        self.assertEqual(r.returncode, 2, _out(r))
        self.assertIn("holdings exists and is not a folder", r.stderr)
        self.assertFalse((top / "2025" / "taxjson.toml").exists())
        self.assertFalse((top / "2025" / "ticker.map").exists())
        (top / "2025" / "holdings").unlink()
        r = tjs("-C", str(top), "new-year", "2025")
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertTrue((top / "2025" / "holdings").is_dir())
        self.assertEqual(sorted(p.name for p in top.iterdir()),
                         ["2024", "2025", "inputs"])

    def test_a_failed_move_is_taken_back(self):
        """#40: a step failing after others were placed."""
        from taxjson.bin import taxjson_run as R
        top = _years((2024, _cfg()))
        (top / "2025").mkdir()
        real = os.rename

        def _rename(a, b):
            if Path(b).name == "taxjson.toml":
                raise OSError(13, "Permission denied")
            return real(a, b)
        with mock.patch.object(os, "rename", _rename):
            with self.assertRaises(OSError):
                R._new_year_publish(top, top / "2025", "x = 1\n",
                                    {"ticker.map": b""}, True)
        self.assertEqual(list((top / "2025").iterdir()), [])
        self.assertEqual(sorted(p.name for p in top.iterdir()),
                         ["2024", "2025", "inputs"])

    @rule("CA-OPT-01")
    def test_canada_keeps_the_default_cutoff(self):
        """#57: a premium written in 2024 and closed in 2025 is taxed in
        2024 only."""
        top = _years((2024, _cfg()))
        (top / "inputs" / "margin").mkdir()
        (top / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2024-12-20 09:30:00 QZZQ250117C00010000.TO -1 CAD 1 "
            "100 0\n"
            "BUYSELL 2025-01-17 09:30:00 QZZQ250117C00010000.TO 1 CAD 0 0 "
            "0\n")
        r = tjs("-C", str(top), "new-year", "2025")
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertIn("option_grant_timing_since = 2024", r.stdout)
        self.assertEqual(
            _doc(top / "2025")["settings"]["option_grant_timing_since"],
            2024)
        gains = {}
        for y in ("2024", "2025"):
            run_ok(self, top / y)
            gains[y] = json.loads(
                (top / y / "work" / "margin_gains_wash.json").read_text()
            )["summary"]["total_gain"]
        self.assertEqual(gains, {"2024": 100, "2025": 0})

    @rule("CA-OPT-01")
    def test_canada_set_or_close_timing_is_kept_as_is(self):
        for extra, want in (("option_grant_timing_since = 2022\n", 2022),
                            ('option_premium_timing = "close"\n', None)):
            top, r = self._new_year(_cfg(extra=extra))
            self.assertEqual(r.returncode, 0, _out(r))
            self.assertEqual(_doc(top / "2025")["settings"].get(
                "option_grant_timing_since"), want)

    @rule_absent("CA-OPT-01", country="usa")
    def test_usa_adds_no_cutoff(self):
        top, r = self._new_year(_cfg(country="usa"))
        self.assertEqual(r.returncode, 0, _out(r))
        self.assertNotIn("option_grant_timing_since",
                         _doc(top / "2025")["settings"])


class TestMigrateToYears(unittest.TestCase):
    def test_custom_holdings_dir(self):
        """#43."""
        s = single("canada", 2024)
        (s / "snapshots").mkdir()
        (s / "snapshots" / "margin.toml").write_text(
            '[[holding]]\nsymbol = "QZQ.TO"\nquantity = 5\n')
        p = s / "taxjson.toml"
        p.write_text(p.read_text().replace(
            "[settings]\n", '[settings]\nholdings_dir = "snapshots"\n'))
        r = tjs("-C", str(s), "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, _out(r))
        y = s / "2024"
        self.assertEqual(PL.holdings_folder(y), (y / "snapshots").resolve())
        self.assertTrue((y / "snapshots" / "margin.toml").is_file())
        self.assertEqual(_doc(y)["settings"]["holdings_dir"], "snapshots")

    def test_holdings_dir_further_away_is_named_from_the_year(self):
        """#43: one not moved is named from the year folder."""
        s = single("usa", 2025)
        (s / "data" / "snaps").mkdir(parents=True)
        p = s / "taxjson.toml"
        p.write_text(p.read_text().replace(
            "[settings]\n", '[settings]\nholdings_dir = "data/snaps"\n'))
        r = tjs("-C", str(s), "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, _out(r))
        y = s / "2025"
        self.assertEqual(_doc(y)["settings"]["holdings_dir"],
                         "../data/snaps")
        self.assertEqual(PL.holdings_folder(y), (s / "data" / "snaps")
                         .resolve())

    def test_relative_ticker_map_link(self):
        """#44."""
        s = single("canada", 2024)
        (s / "maps").mkdir()
        (s / "maps" / "current.map").write_text("# mine\n")
        (s / "ticker.map").symlink_to("maps/current.map")
        r = tjs("-C", str(s), "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, _out(r))
        link = s / "2024" / "ticker.map"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "../maps/current.map")
        self.assertEqual(link.read_text(), "# mine\n")
        self.assertNotIn("Left at the top", r.stdout)
        run_ok(self, s / "2024")


if __name__ == "__main__":
    unittest.main()
