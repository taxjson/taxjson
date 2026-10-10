"""Fixes from a new user's walkthrough of v0.27.1: the printed setup
recipes, the year default's January-to-April hint, a .tt symbol without
its market suffix, the same account number at two brokers, the demo
exports and `tjs init --demo`, maintainer commands in the help page,
close-year with open checklist items, the FX download window, product
text that contradicted v0.27.x, and option_grant_timing_since in a new
taxjson.toml. Synthetic data only (account ids 999000xx)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from _style import CapturedWidth

REPO = Path(__file__).resolve().parents[1]
SRC = str(REPO / "src")
EXAMPLES = REPO / "examples"

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def tearDownModule():
    _WIDTH.stop()


def _env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TAXJSON_OFFLINE"] = "1"
    env.update(extra)
    return env


def _cli(*args, cwd=None, env=None, stdin=subprocess.DEVNULL):
    return subprocess.run(
        [sys.executable, "-P", "-m", "taxjson.bin.taxjson_run", *args],
        cwd=cwd, stdin=stdin, capture_output=True, text=True,
        env=env or _env())


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


_TOML = """[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
tax_date = "settle"
option_grant_timing_since = 2025
"""

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type")


def _qbuy(d, s, acct, sym="XEI.TO"):
    return (f"{d} 12:00:00 AM,{s} 12:00:00 AM,Buy,{sym},SAMPLE TEST FUND,"
            f"100,25,-2500,-4.95,-2504.95,CAD,{acct},Trades,"
            f"Individual margin")


def _webull(acct, rows):
    return ("Webull Securities (Canada) Ltd.\nSynthetic Statement\n"
            f"Account Number: {acct}\n"
            "Date Range: January 1 2025 - December 31 2025\n\n"
            '"Currency","Date","Action Code","Symbol","Security Description",'
            '"Type Code","Quantity","Price","Proceeds"\n' + rows)


def _pinned(day):
    class _Today(date):
        @classmethod
        def today(cls):
            return day
    return mock.patch("taxjson.bin.taxjson_run.date_cls", _Today)


def _init(path, year=None, country="canada", single=False):
    import argparse
    from taxjson.bin.taxjson_run import cmd_init
    out = io.StringIO()
    with contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(io.StringIO()):
        cmd_init(argparse.Namespace(single=single, path=str(path), dir=".",
                                    force=False, country=country, year=year,
                                    demo=False))
    return out.getvalue()


# ------------------------------------------- 1. init in a YYYY folder
class TestInitInAYearFolder(unittest.TestCase):

    def test_empty_year_folder_is_refused_with_the_right_command(self):
        top = _tmp(self)
        y = top / "taxes" / "2025"
        y.mkdir(parents=True)
        r = _cli("init", "--country", "canada", cwd=y)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        flat = " ".join(r.stderr.split())
        self.assertIn("is named like a tax year and is empty", flat)
        self.assertIn("this would build 2025/2025/ in it", flat)
        self.assertIn(f"cd {top / 'taxes'} && taxjson init --country "
                      f"canada --year 2025 && cd 2025", flat)
        self.assertEqual(list(y.iterdir()), [])
        # --single is one folder for one year: fine there.
        r = _cli("init", "--single", "--country", "canada", cwd=y)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((y / "taxjson.toml").is_file())

    def test_the_folder_for_your_taxes_is_fine(self):
        top = _tmp(self)
        r = _cli("init", "--country", "canada", "--year", "2025",
                 cwd=top)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((top / "2025" / "taxjson.toml").is_file())

    def test_installer_recipe(self):
        text = (REPO / "install.sh").read_text()
        self.assertIn("mkdir -p ~/taxes && cd ~/taxes && tjs init "
                      "--country canada && cd $(date +%Y)", text)
        self.assertNotIn("mkdir -p ~/taxes/$(date", text)


# ------------------------------------------ 2. January to April hint
class TestFilingLastYearHint(unittest.TestCase):

    def test_hint_from_january_to_april_only(self):
        for day, shown in ((date(2026, 1, 1), True),
                           (date(2026, 4, 30), True),
                           (date(2026, 5, 1), False),
                           (date(2026, 12, 31), False)):
            with self.subTest(day=day):
                top = _tmp(self)
                with _pinned(day):
                    out = _init(top)
                self.assertTrue((top / "2026" / "taxjson.toml").is_file())
                flat = " ".join(out.split())
                hint = ("Filing 2025 now? Run `taxjson init --country "
                        f"canada --year 2025` in {top} for its folder.")
                self.assertEqual(hint in flat, shown, out)

    def test_no_hint_when_the_year_is_given(self):
        with _pinned(date(2026, 2, 10)):
            out = _init(_tmp(self), year=2026)
        self.assertNotIn("Filing 2025 now?", out)


# ------------------------------------- 10. option_grant_timing_since
class TestGrantSinceInANewProject(unittest.TestCase):

    def test_init_leaves_it_commented_with_one_line(self):
        top = _tmp(self)
        _init(top, year=2025)
        text = (top / "2025" / "taxjson.toml").read_text()
        import re
        m = re.search(r"(?m)^## (.*)\n(?:## (.*)\n)?# option_grant_timing"
                      r"_since\s+= 2025$", text)
        self.assertTrue(m, text)
        self.assertIn("Only if you write (sell to open) options", m[1])

    def _project(self, since=None, written=False):
        root = _tmp(self)
        (root / "inputs" / "margin").mkdir(parents=True)
        tt = "BUYSELL 2025-02-03 10:00:00 QZA.TO 100 CAD 10 -1000.00 0.00\n"
        if written:
            tt += ("BUYSELL 2025-04-01 10:00:00 QZA251219C00012000.TO -1 "
                   "CAD 2.00 200.00 0.00\n")
        (root / "inputs" / "margin" / "m.tt").write_text(tt)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            + (f"option_grant_timing_since = {since}\n" if since else "")
            + '\n[accounts.margin]\ntype = "taxable"\n')
        return root

    def test_unset_key_is_quiet_without_written_options(self):
        r = _cli("-C", str(self._project()), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("option_grant_timing_since", r.stdout + r.stderr)

    def test_unset_key_is_warned_with_a_written_option(self):
        r = _cli("-C", str(self._project(written=True)), "run",
                 "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("option_grant_timing_since is not set",
                      r.stdout + r.stderr)

    def test_a_since_later_than_the_year_is_one_line(self):
        r = _cli("-C", str(self._project(since=2026)), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: option_grant_timing_since = 2026 is after "
                      "year = 2025: 2025's written options are taxed at "
                      "the close\n", r.stderr)
        r = _cli("-C", str(self._project(since=2025)), "run", "--no-input")
        self.assertNotIn("is after year", r.stdout + r.stderr)


# ------------------------------------ 7. close-year with open items
class TestCloseYearWithAttentionItems(unittest.TestCase):
    """close-year lists the checklist steps before the lock that need
    attention; without a terminal it refuses unless --yes, on one it
    asks."""

    def _project(self):
        root = _tmp(self)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            "BUYSELL 2025-02-03 10:00:00 QZA.TO 100 CAD 10 -1000.00 0.00\n"
            "BUYSELL 2025-06-03 10:00:00 QZA.TO -100 CAD 12 1200.00 0.00\n")
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '\n[accounts.margin]\ntype = "taxable"\n')
        r = _cli("-C", str(root), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root

    def test_refused_without_a_terminal_listing_the_items(self):
        root = self._project()
        r = _cli("-C", str(root), "close-year")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("checklist item(s) before the lock need attention",
                      r.stderr)
        self.assertIn("- inputs-committed: not a git repository", r.stderr)
        self.assertIn("pass --yes to lock 2025 anyway", r.stderr)
        self.assertFalse((root / "filed" / "2025.json").exists())
        r = _cli("-C", str(root), "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((root / "filed" / "2025.json").exists())

    def test_a_terminal_is_asked(self):
        import argparse
        from taxjson.bin import taxjson_run as R
        root = self._project()
        for answer, locked in (("n", False), ("y", True)):
            with self.subTest(answer=answer), \
                    mock.patch.object(R.sys.stdin, "isatty",
                                      lambda: True), \
                    mock.patch("builtins.input",
                               lambda _p: answer) as _i, \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    R.cmd_close_year(argparse.Namespace(
                        dir=str(root), year=None, force=False,
                        filed_dispositions=None, yes=False))
                except SystemExit:
                    pass
                self.assertEqual((root / "filed" / "2025.json").exists(),
                                 locked, err.getvalue())


# ------------------------------------------------ 4. one number, two brokers
class TestSameNumberAtTwoBrokers(unittest.TestCase):
    """A Questrade and a Webull export that print the same account
    number are two broker accounts: no "feeds two taxjson accounts"."""

    def _project(self, webull_rows=None, second_questrade=False):
        root = _tmp(self)
        acct = "99900021"                                  # pii-ok
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "qt" / "questrade.csv").write_text(
            _QH + "\n" + _qbuy("2025-03-03", "2025-03-04", acct) + "\n")
        (root / "inputs" / "wb").mkdir(parents=True)
        if second_questrade:
            (root / "inputs" / "wb" / "questrade.csv").write_text(
                _QH + "\n" + _qbuy("2025-03-03", "2025-03-04", acct) + "\n")
        else:
            (root / "inputs" / "wb" / "webull.csv").write_text(_webull(
                acct, webull_rows or
                'CAD,05-03-2025,BUY,@XEI,SAMPLE TEST FUND,EQ,10,25.00,'
                '"(250.00)"\n'))
        (root / "taxjson.toml").write_text(
            _TOML + '\n[accounts.qt]\ntype = "taxable"\n'
            '\n[accounts.wb]\ntype = "taxable"\n')
        return root

    def test_two_brokers_same_number_is_quiet(self):
        r = _cli("-C", str(self._project()), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("feeds two taxjson accounts", r.stdout + r.stderr)
        self.assertNotIn("99900021", r.stdout + r.stderr)   # pii-ok

    def test_one_broker_same_number_still_loud(self):
        r = _cli("-C", str(self._project(second_questrade=True)), "run",
                 "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("feeds two taxjson accounts, qt and wb", r.stdout)


# ------------------------------------- 3. a .tt symbol without its suffix
class TestBareTtSymbolOnTheConsole(unittest.TestCase):
    """`XEI` on a .tt line while the broker rows hold `XEI.TO` is a pool
    of its own: the run's console names the file:line and the listing,
    in a year folder (exports shared beside it) too."""

    def _project(self, years):
        top = _tmp(self)
        root = top / "2025" if years else top
        root.mkdir(exist_ok=True)
        qt = top / "inputs" / "qt"
        qt.mkdir(parents=True)
        (qt / "questrade.csv").write_text(
            _QH + "\n" + _qbuy("2025-03-03", "2025-03-04", "99900090")
            + "\n")
        (qt / "extra.tt").write_text(
            "# bought before the download\n"
            "BUYSELL 2025-02-03 09:30:00 XEI 10 CAD 25 -250.00 0.00\n")
        (root / "taxjson.toml").write_text(
            _TOML + ('inputs_dir = "../inputs"\n' if years else "")
            + '\n[accounts.qt]\ntype = "taxable"\n')
        return root

    def test_console_names_file_line_and_listing(self):
        for years in (False, True):
            with self.subTest(years=years):
                r = _cli("-C", str(self._project(years)), "run",
                         "--no-input")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                # (captured: `taxjson: warning: ...` on stderr)
                self.assertIn(
                    "warning: inputs/qt/extra.tt:2: XEI has no market "
                    "suffix: a pool of its own, apart from XEI.TO in the "
                    "books\n", r.stderr)


# ------------------------------------- 6. maintainer commands in help
class TestMaintainerCommandsHidden(unittest.TestCase):
    MAINT = ("channels", "deploy", "promote")

    def _env_user_install(self):
        """This checkout as the production copy (the installer's clone):
        no development checkout, as on a user's machine."""
        env = _env(TAXJSON_PROD_DIR=str(REPO))
        env.pop("TAXJSON_DEV_DIR", None)
        return env

    def _listed(self, out):
        import re
        return re.findall(r"(?m)^  ([a-z][a-z0-9-]+)\s{2,}", out)

    def test_user_help_hides_them_and_all_lists_them(self):
        env = self._env_user_install()
        d = _tmp(self)
        out = _cli("help", cwd=d, env=env).stdout
        listed = self._listed(out)
        for c in self.MAINT:
            self.assertNotIn(c, listed)
        self.assertNotIn("\nMaintainer:\n", out)
        self.assertIn("redact", listed)
        every = _cli("help", "--all", cwd=d, env=env).stdout
        self.assertIn("\nMaintainer:\n", every)
        for c in self.MAINT:
            self.assertIn(c, self._listed(every))
        # Still runnable.
        r = _cli("channels", "--offline", "--json", cwd=d, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dev_checkout_lists_them(self):
        env = _env(TAXJSON_DEV_DIR=str(REPO))
        out = _cli("help", cwd=_tmp(self), env=env).stdout
        self.assertIn("\nMaintainer:\n", out)
        for c in self.MAINT:
            self.assertIn(c, self._listed(out))


# ---------------------------------- 9. text that contradicted v0.27.x
class TestProductText(unittest.TestCase):

    def test_help_pages(self):
        init = " ".join(_cli("init", "-h").stdout.split())
        self.assertIn("in Canada also tobase.map (the interlisted pairs) "
                      "beside the year folders, one file every year reads",
                      init)
        ny = " ".join(_cli("new-year", "-h").stdout.split())
        self.assertIn("ticker.map (the shared tobase.map is read, not "
                      "copied)", ny)
        run = " ".join(_cli("run", "-h").stdout.split())
        self.assertIn("(inputs/, or [settings] inputs_dir)", run)
        self.assertNotIn("names holdings files", run)

    def test_init_next_steps_and_readme(self):
        for country, tobase in (("canada", True), ("usa", False)):
            with self.subTest(country=country):
                top = _tmp(self)
                r = _cli("init", "--country", country, "--year", "2025",
                         str(top))
                self.assertEqual(r.returncode, 0, r.stderr)
                flat = " ".join(r.stdout.split())
                self.assertIn("copies this year's taxjson.toml and "
                              "ticker.map into 2026/", flat)
                self.assertEqual("tobase.map is one file every year reads"
                                 in flat, tobase)
                self.assertNotIn("ticker.map and tobase.map", flat)
                readme = " ".join((top / "inputs" / "margin" /
                                   "README.txt").read_text().split())
                self.assertIn("keep each year's in YYYY/inputs/slips/",
                              readme)
                self.assertIn("Kraken", (top / "inputs" / "crypto" /
                                         "README.txt").read_text())
                self.assertIn("Coinbase", (top / "inputs" / "crypto" /
                                           "README.txt").read_text())
        single = _tmp(self)
        _cli("init", "--single", "--country", "canada", str(single))
        self.assertIn("keep each year's in inputs/slips/", " ".join(
            (single / "inputs" / "margin" / "README.txt").read_text()
            .split()))

    def test_cdr_pair_advice_says_nothing_to_do(self):
        import inspect
        from taxjson.bin import taxjson_run as R
        src = inspect.getsource(R.cmd_tips)
        self.assertIn("nothing to do: \"\n", src.replace("f\"", "\""))
        self.assertIn("look-alike listings are never", src)
        self.assertNotIn("to record this and silence", src)
        self.assertNotIn("declare \"\n", src.replace("f\"", "\""))


# ------------------------------------------------------ 8. the FX window
class TestFxWindow(unittest.TestCase):
    """The rates stage asks only for the dates the project's files
    reach (lib/rates_window), never Yahoo for a 2024 project."""

    def test_date_shapes(self):
        from taxjson.lib.rates_window import earliest_in_text
        cases = {
            b"2024-03-05,x": date(2024, 3, 5),
            b'"05-03-2023",x': date(2023, 3, 5),       # either order:
            b"03/15/2022 x": date(2022, 3, 15),         # the valid one
            b"Date Range: January 1, 2021 - x": date(2021, 1, 1),
            b"x 1 Feb 2020 x": date(2020, 2, 1),
            b"20190104;093000": date(2019, 1, 4),       # an IB Flex date
            b"2024-01-02 and order 20010101": date(2024, 1, 2),
            b"price 2003.45 qty 1999": None,
        }
        for text, want in cases.items():
            self.assertEqual(earliest_in_text(text, 2027), want, text)

    def test_window_start(self):
        from taxjson.lib.rates_window import window_start
        d = _tmp(self)
        (d / "a.csv").write_text("2024-02-12,BUY\n2024-12-05,SELL\n")
        (d / "b.tt").write_text("OPENING 2022-06-03 QZA.TO 10 "
                                "cost=unknown\n")
        self.assertEqual(window_start(2024, [d / "a.csv"]),
                         date(2023, 12, 18))
        self.assertEqual(window_start(2024, [d / "a.csv", d / "b.tt"]),
                         date(2022, 5, 20))
        self.assertEqual(window_start(2024, []), date(2023, 12, 18))

    def test_a_2024_project_asks_the_bank_from_december_2023_only(self):
        """A stubbed fetcher counts the requests: the Bank of Canada is
        asked from the window's start, Yahoo never."""
        from taxjson.bin import to_base_curr as T
        from taxjson.lib.rates_window import window_start
        d = _tmp(self)
        shutil.copy(EXAMPLES / "questrade_demo.csv", d / "q.csv")
        start = window_start(2024, [d / "q.csv"]).isoformat()
        boc, yahoo, noon = [], [], []

        def fake_boc(cur, a, b):
            boc.append((a, b))
            out, x = {}, a
            while x <= b:
                if date.fromisoformat(x).weekday() < 5:
                    out[x] = "1.3500"
                x = T._shift(x, 1)
            return out

        with mock.patch.object(T, "CACHE_FILE", str(d / "fx.json")):
            rows, errors, _n = T.build_rates(
                "USD", "CAD", start, "2026-10-09", today="2026-10-09",
                fetch_boc_fn=fake_boc,
                fetch_yahoo_fn=lambda *a: yahoo.append(a) or {},
                fetch_noon_fn=lambda *a: noon.append(a) or {})
        self.assertEqual(errors, [])
        self.assertEqual(start, "2023-12-18")
        self.assertTrue(boc)
        self.assertGreaterEqual(min(a for a, _b in boc), start)
        self.assertEqual(yahoo, [])
        self.assertEqual(rows[0][0], start)

    def test_the_stage_passes_the_window_and_records_it(self):
        from taxjson.bin import taxjson_run as R
        d = _tmp(self)
        cache = d / "work"
        (d / "inputs" / "qt").mkdir(parents=True)
        shutil.copy(EXAMPLES / "questrade_demo.csv",
                    d / "inputs" / "qt" / "q.csv")
        seen = []

        def capture(argv, *a, **k):
            seen.append(argv)
            return b"2023-12-18 12:00:00 USD CAD 1.35 boc\n"
        settings = {"base_currency": "CAD", "source_currencies": ["USD"],
                    "year": 2024}
        files = R._rates_inputs(d, d / "inputs", {"qt": {}})
        with mock.patch.object(R, "run_capture", capture):
            R.stage_currency_rates(settings, cache, files)
        self.assertEqual(seen[0][-2:], ["--start", "2023-12-18"])
        self.assertEqual((cache / R.RATES_START_STAMP).read_text().strip(),
                         "2023-12-18")
        # An export reaching further back rebuilds from its date.
        (d / "inputs" / "qt" / "old.tt").write_text(
            "OPENING 2021-03-01 QZA.TO 10 cost=unknown\n")
        files = R._rates_inputs(d, d / "inputs", {"qt": {}})
        with mock.patch.object(R, "run_capture", capture), \
                mock.patch.object(R, "_rates_coverage_stale",
                                  lambda *a, **k: False), \
                mock.patch.object(R, "needs_rebuild", lambda *a: False):
            R.stage_currency_rates(settings, cache, files)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1][-2:], ["--start", "2021-02-15"])


if __name__ == "__main__":
    unittest.main()
