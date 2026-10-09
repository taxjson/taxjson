"""One folder of exports for every year (lib/project_layout).

inputs/<account>/ shared by year folders, each a complete project whose
`[settings] inputs_dir` names the shared folder. Synthetic data only
(.tt lines, made-up tickers), both countries:

- each year's `sum --json` is byte-identical to the same data run as a
  single-folder project (Canada and USA, three years, a superficial loss
  / wash sale across a year end);
- a command in the folder holding the years is refused, naming them;
- a year folder holding another year's project, and a folder setting
  that leaves the folder holding the project (by `..` or a symlink), are
  refused;
- an account folder another year has is not read, with one Info line;
- `new-year` copies the previous year's taxjson.toml (year bumped,
  prior_year_record set, [estimate] commented out) and ticker.map;
  `align` brings another year's map lines over, never writing it;
  `years` (and `--json`, `--diff`) report each year;
- a filed year warns (sum, checklist, run DRIFTED) after a download into
  the shared inputs changes it;
- `sanity` finds the year's holdings/ snapshots by broker account id or
  file name; the newest year writes exports/;
- `migrate --to-years` and `init` (and `--single`) make the layout.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from _style import env
from _tmpfiles import private_dir
from tax_rules import rule

YEARS = (2023, 2024, 2025)

_TT = {
    "canada": {
        "margin": """\
# synthetic
BUYSELL 2023-02-01 10:00:00 QZQ.TO 100 CAD 20.00 2009.95 9.95
BUYSELL 2023-06-01 10:00:00 QZQ.TO -50 CAD 25.00 1240.05 9.95
DIVIDEND 2023-09-28 16:00:00 QZQ.TO 50 CAD 0.40 20.00
BUYSELL 2024-03-01 10:00:00 ZZB.TO 40 CAD 10.00 409.95 9.95
BUYSELL 2024-12-16 10:00:00 ZZB.TO -40 CAD 8.00 310.05 9.95
BUYSELL 2025-01-08 10:00:00 ZZB.TO 40 CAD 8.50 349.95 9.95
BUYSELL 2025-05-01 10:00:00 QZQ.TO -50 CAD 30.00 1490.05 9.95
BUYSELL 2025-07-01 10:00:00 ZZB.TO -40 CAD 9.00 350.05 9.95
DIVIDEND 2025-03-28 16:00:00 QZQ.TO 50 CAD 0.40 20.00
""",
        "tfsa": "BUYSELL 2024-05-01 10:00:00 QZQ.TO 10 CAD 22.00 229.95 9.95\n",
    },
    "usa": {
        "margin": """\
# synthetic
BUYSELL 2023-02-01 10:00:00 QZQ.US 100 USD 20.00 2001.00 1.00
BUYSELL 2023-06-01 10:00:00 QZQ.US -50 USD 25.00 1249.00 1.00
DIVIDEND 2023-09-28 16:00:00 QZQ.US 50 USD 0.40 20.00
BUYSELL 2024-03-01 10:00:00 ZZB.US 40 USD 10.00 401.00 1.00
BUYSELL 2024-12-16 10:00:00 ZZB.US -40 USD 8.00 319.00 1.00
BUYSELL 2025-01-08 10:00:00 ZZB.US 40 USD 8.50 341.00 1.00
BUYSELL 2025-05-01 10:00:00 QZQ.US -50 USD 30.00 1499.00 1.00
BUYSELL 2025-07-01 10:00:00 ZZB.US -40 USD 9.00 359.00 1.00
""",
        "roth": "BUYSELL 2024-05-01 10:00:00 QZQ.US 10 USD 22.00 221.00 1.00\n",
    },
}


def _toml(country: str, year: int, shared: bool) -> str:
    sheltered = "tfsa" if country == "canada" else "roth"
    lines = ["[settings]", f"year = {year}", f'country = "{country}"']
    if shared:
        lines += ['inputs_dir = "../inputs"', 'exports_dir = "../exports"']
    if country == "canada":
        lines += ['tax_date = "settle"', 'base_currency = "CAD"',
                  'source_currencies = ["USD"]',
                  "option_grant_timing_since = 2023"]
    else:
        lines += ['tax_date = "trade"', 'base_currency = "USD"']
    lines += ["", "[accounts.margin]", 'type = "taxable"', "",
              f"[accounts.{sheltered}]", 'type = "sheltered"',
              "transfers = true", ""]
    return "\n".join(lines)


def _inputs(country: str, where: Path) -> None:
    for acct, text in _TT[country].items():
        d = where / acct
        d.mkdir(parents=True, exist_ok=True)
        (d / "trades.tt").write_text(text)


def tjs(*args, cwd=None, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *args],
        cwd=cwd, capture_output=True, text=True, env=env(TAXJSON_WIDTH=0),
        timeout=900,
        stdin=subprocess.DEVNULL if stdin is None else stdin)


def multi(country: str, years=YEARS) -> Path:
    """A folder of exports shared by the year projects."""
    top = Path(private_dir()) / "taxes"
    _inputs(country, top / "inputs")
    for y in years:
        (top / str(y)).mkdir(parents=True)
        (top / str(y) / "taxjson.toml").write_text(_toml(country, y, True))
    return top


def single(country: str, year: int) -> Path:
    root = Path(private_dir()) / "p"
    _inputs(country, root / "inputs")
    root.mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(_toml(country, year, False))
    return root


def run_ok(tc, folder: Path):
    r = tjs("-C", str(folder), "run", "--no-input")
    tc.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
    return r


class TestSameBooksAsSingleFolder(unittest.TestCase):
    """Each year of the shared layout = the single-folder project."""

    def _check(self, country):
        top = multi(country)
        for y in YEARS:
            run_ok(self, top / str(y))
            got = tjs("-C", str(top / str(y)), "sum", "--json")
            self.assertEqual(got.returncode, 0, got.stderr)
            s = single(country, y)
            run_ok(self, s)
            want = tjs("-C", str(s), "sum", "--json")
            self.assertEqual(got.stdout, want.stdout,
                             f"{country} {y}: the year folder's sum differs")
            self.assertTrue(json.loads(got.stdout))
        return top

    def test_canada_three_years(self):
        top = self._check("canada")
        # The superficial loss of December 2024 (re-bought in January
        # 2025) is denied in 2024's books: the history is shared.
        doc = json.loads(tjs("-C", str(top / "2024"), "sum",
                             "--json").stdout)
        acct = doc["filing"]["accounts"][0]
        self.assertGreater(acct["denied"], 0)

    def test_usa_three_years(self):
        self._check("usa")


class TestRefusals(unittest.TestCase):
    def test_command_in_the_years_folder_is_refused_with_the_list(self):
        top = multi("canada")
        r = tjs("sum", cwd=top)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("2023, 2024, 2025", r.stderr)
        self.assertIn("cd 2025", r.stderr)
        # `years` works there.
        r = tjs("years", cwd=top)
        self.assertEqual(r.returncode, 0, r.stderr)
        for y in YEARS:
            self.assertIn(str(y), r.stdout)

    def test_year_folder_holding_another_year_is_refused(self):
        top = multi("canada", years=(2025,))
        cfg = top / "2025" / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace("year = 2025",
                                               "year = 2024"))
        r = tjs("-C", str(top / "2025"), "run", "--no-input")
        self.assertEqual(r.returncode, 1)
        self.assertIn("year = 2024 but this folder is 2025", r.stderr)

    def test_a_folder_setting_leaving_the_parent_is_refused(self):
        top = multi("canada", years=(2025,))
        cfg = top / "2025" / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace('"../inputs"',
                                               '"../../elsewhere"'))
        r = tjs("-C", str(top / "2025"), "run", "--no-input")
        self.assertEqual(r.returncode, 1)
        self.assertIn("inputs_dir", r.stderr)
        self.assertIn("outside", r.stderr)

    def test_a_symlink_leaving_the_parent_is_refused(self):
        top = multi("canada", years=(2025,))
        outside = Path(private_dir())
        shutil.move(str(top / "inputs"), str(outside / "inputs"))
        os.symlink(outside / "inputs", top / "inputs")
        r = tjs("-C", str(top / "2025"), "run", "--no-input")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("outside", r.stderr)

    def test_another_years_account_folder_is_an_info_line(self):
        top = multi("canada", years=(2024,))
        (top / "inputs" / "rrsp2").mkdir()
        (top / "inputs" / "rrsp2" / "x.tt").write_text(
            "BUYSELL 2025-02-01 10:00:00 QZQ.TO 1 CAD 20.00 29.95 9.95\n")
        r = run_ok(self, top / "2024")
        out = r.stdout + r.stderr
        self.assertIn("rrsp2 — not an account of 2024", out)
        self.assertNotIn("will NOT be processed", out)


class TestYearTools(unittest.TestCase):
    def test_new_year_copies_last_years_files(self):
        top = multi("canada", years=(2025,))
        cfg = top / "2025" / "taxjson.toml"
        cfg.write_text(cfg.read_text() + "\n[estimate]\nother_income = 900\n")
        (top / "2025" / "ticker.map").write_text("TOBASE ZZB.US ZZB.TO\n")
        r = tjs("new-year", "2026", cwd=top)
        self.assertEqual(r.returncode, 0, r.stderr)
        new = (top / "2026" / "taxjson.toml").read_text()
        self.assertIn("year = 2026", new)
        self.assertIn('prior_year_record = "../2025/filed/2025.json"', new)
        self.assertIn("# other_income = 900", new)
        self.assertNotIn("\nother_income", new)
        self.assertEqual((top / "2026" / "ticker.map").read_text(),
                         "TOBASE ZZB.US ZZB.TO\n")
        self.assertTrue((top / "2026" / "holdings").is_dir())
        self.assertIn("2026/holdings/", r.stdout)
        # Again: refused, the folder is there.
        self.assertEqual(tjs("new-year", "2026", cwd=top).returncode, 2)

    def test_align_brings_lines_over_and_never_writes_the_other(self):
        top = multi("canada", years=(2024, 2025))
        (top / "2025" / "ticker.map").write_text("TOBASE ZZB.US ZZB.TO\n")
        cfg25 = top / "2025" / "taxjson.toml"
        cfg25.write_text(cfg25.read_text().replace(
            "[accounts.margin]", 'province = "ON"\n\n[accounts.margin]'))
        before = cfg25.read_text()
        r = tjs("years", "--diff", "2024", "2025", "--json", cwd=top)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["map_only_there"], ["TOBASE ZZB.US ZZB.TO"])
        self.assertEqual([k["key"] for k in doc["keys"]],
                         ["settings.province"])
        r = tjs("align", "--from", "2025", "--write", "--all",
                cwd=top / "2024")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("TOBASE ZZB.US ZZB.TO",
                      (top / "2024" / "ticker.map").read_text())
        self.assertIn('province = "ON"',
                      (top / "2024" / "taxjson.toml").read_text())
        self.assertTrue((top / "2024" / "taxjson.toml.bak").is_file())
        self.assertEqual(cfg25.read_text(), before)
        r = tjs("align", "--from", "2025", cwd=top / "2024")
        self.assertIn("the same ticker.map rules", r.stdout)

    def test_years_json(self):
        top = multi("canada", years=(2024, 2025))
        run_ok(self, top / "2024")
        doc = json.loads(tjs("years", "--json", cwd=top / "2024").stdout)
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["newest"], 2025)
        y = {r["year"]: r for r in doc["years"]}
        self.assertIsNotNone(y[2024]["last_run"])
        self.assertIs(y[2024]["stale"], False)
        self.assertIsNone(y[2025]["last_run"])
        self.assertFalse(y[2024]["filed"])


class TestFiledYearAndSharedInputs(unittest.TestCase):
    def test_a_later_download_is_reported_against_the_filed_year(self):
        top = multi("canada", years=(2024, 2025))
        y24 = top / "2024"
        run_ok(self, y24)
        r = tjs("-C", str(y24), "close-year")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # A 2024 sale downloaded later, into the shared inputs.
        (top / "inputs" / "margin" / "late.tt").write_text(
            "BUYSELL 2024-08-01 10:00:00 QZQ.TO -10 CAD 28.00 270.05 9.95\n")
        r = tjs("-C", str(y24), "sum")
        self.assertIn("added: inputs/margin/late.tt", r.stderr)
        self.assertIn("2024 is filed", r.stderr)
        r = tjs("-C", str(y24), "checklist", "--only", "filed-lock",
                "--json")
        st = json.loads(r.stdout)["steps"][0]
        self.assertEqual(st["status"], "attention", st)
        self.assertIn("inputs changed", st["detail"])
        doc = json.loads(tjs("years", "--json", cwd=top).stdout)
        y = {r["year"]: r for r in doc["years"]}
        self.assertTrue(y[2024]["filed"])
        self.assertTrue(y[2024]["stale"])
        r = tjs("-C", str(y24), "run", "--no-input")
        self.assertIn("filed 2024 DRIFTED", r.stdout + r.stderr)


class TestHoldingsAndExports(unittest.TestCase):
    def test_sanity_finds_the_years_snapshots(self):
        top = multi("canada", years=(2025,))
        y = top / "2025"
        cfg = y / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            '[accounts.margin]\ntype = "taxable"',
            '[accounts.margin]\ntype = "taxable"\n'
            'broker_accounts = ["U5550001"]'))  # pii-ok (synthetic)
        run_ok(self, y)
        (y / "holdings").mkdir()
        # By broker account id (portoml writes only generated_at).
        (y / "holdings" / "ibkr_U5550001.toml").write_text(  # pii-ok
            '[meta]\naccount = "U5550001"\n'  # pii-ok (synthetic)
            'generated_at = "2026-01-05T10:00:00"\n\n'
            '[[holding]]\nsymbol = "QZQ.TO"\nquantity = 0\ncurrency = "CAD"\n')
        # By file name, and one no account claims.
        (y / "holdings" / "tfsa_holdings.toml").write_text(
            '[meta]\nas_of = "2025-12-31"\n\n'
            '[[holding]]\nsymbol = "QZQ.TO"\nquantity = 10\n'
            'currency = "CAD"\n')
        (y / "holdings" / "other.toml").write_text(
            '[meta]\naccount = "U5550009"\n\n'  # pii-ok (synthetic)
            '[[holding]]\nsymbol = "QZQ.TO"\nquantity = 1\n'
            'currency = "CAD"\n')
        r = tjs("-C", str(y), "sanity", "--json")
        self.assertIn(r.returncode, (0, 1), r.stderr)
        doc = json.loads(r.stdout)
        groups = sorted(tuple(g["accounts"]) for g in doc["groups"])
        self.assertEqual(groups, [("margin",), ("tfsa",)])
        notes = " ".join(doc["notes"])
        self.assertIn("other.toml: no account claims it", notes)
        self.assertNotIn("5550009", notes + r.stderr)
        self.assertFalse(doc["complete"])
        self.assertIn("after the books' last day", r.stderr)

    def test_the_newest_year_writes_exports(self):
        top = multi("canada", years=(2024, 2025))
        run_ok(self, top / "2024")
        self.assertFalse((top / "exports").exists())
        run_ok(self, top / "2025")
        ex = top / "exports"
        self.assertTrue((ex / "margin_holdings.toml").is_file())
        self.assertTrue((ex / "wash_radar_margin.json").is_file())
        self.assertIn("2025", (ex / "README.txt").read_text())


def _tree(folder: Path) -> dict:
    """{relative path: bytes} of every file under `folder`."""
    return {str(q.relative_to(folder)): q.read_bytes()
            for q in sorted(folder.rglob("*")) if q.is_file()}


class TestRunNeverWritesSharedInputs(unittest.TestCase):
    """Two years with different maps share the style project's exports
    (a spin-off election, a crypto send): a run leaves inputs/ byte for
    byte as it was; only `elect --set` / `crypto-sends --set` write
    there, saying so."""

    def test_two_years_share_the_exports(self):
        from _style import FIXTURES
        top = Path(private_dir()) / "taxes"
        shutil.copytree(FIXTURES / "canada" / "inputs", top / "inputs")
        y24, y25 = top / "2024", top / "2025"
        for y, d in ((2024, y24), (2025, y25)):
            d.mkdir(parents=True)
            text = (FIXTURES / "canada" / "taxjson.toml").read_text()
            text = text.replace("year                              = 2024",
                                f"year = {y}")
            text = text.replace("[settings]\n",
                                '[settings]\ninputs_dir = "../inputs"\n', 1)
            (d / "taxjson.toml").write_text(text)
            if (top / "inputs" / "slips").exists():
                (d / "inputs").mkdir()
                shutil.move(str(top / "inputs" / "slips"),
                            str(d / "inputs" / "slips"))
        (y25 / "ticker.map").write_text("GLOBAL ZZOLDQ.TO ZZNEWQ.TO\n")
        before = _tree(top / "inputs")
        r = tjs("-C", str(y24), "run", "--no-input")
        self.assertEqual(r.returncode, 3, r.stderr[-2000:])
        self.assertEqual(_tree(top / "inputs"), before,
                         "a run wrote into the shared inputs/")
        pend = json.loads((y24 / "work" / "pending_elections.json")
                          .read_text())
        for acct, doc in pend["accounts"].items():
            for ev in doc["pending"]:
                r = tjs("-C", str(y24), "elect", acct, "--set",
                        f"{ev['event_id']}=taxable_deemed_dividend")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("every year reading these exports", r.stderr)
        sends = json.loads(tjs("-C", str(y24), "crypto-sends",
                               "--json").stdout or "{}")
        for acct, d in (sends.get("accounts") or {}).items():
            for snd in d.get("sends") or []:
                r = tjs("-C", str(y24), "crypto-sends", acct, "--set",
                        f"{snd['id']}=gift", "--note", "synthetic")
                self.assertEqual(r.returncode, 0, r.stderr)
        decided = _tree(top / "inputs")
        self.assertNotEqual(decided, before)
        for d in (y24, y25, y24):
            r = tjs("-C", str(d), "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_tree(top / "inputs"), decided,
                             f"the {d.name} run wrote into inputs/")
        self.assertFalse(list((top / "inputs").rglob("crypto_sends.tt")))
        gen = list((y24 / "work" / "crypto_sends").rglob("crypto_sends.tt"))
        self.assertEqual(len(gen), 1)
        # A generated file an older taxjson left in the shared folder is
        # not read (no sale booked twice), and stays as it was.
        want = tjs("-C", str(y24), "sum", "--json").stdout
        shutil.copy(gen[0], top / "inputs" / "crypto" / "crypto_sends.tt")
        kept = _tree(top / "inputs")
        r = tjs("-C", str(y24), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("generated by an older taxjson", r.stderr)
        self.assertEqual(tjs("-C", str(y24), "sum", "--json").stdout, want)
        self.assertEqual(_tree(top / "inputs"), kept)


_MH_TT = """\
# synthetic: 10 sold before any purchase in the files (missing history)
BUYSELL 2023-03-01 10:00:00 QZQ.US -10 USD 20.00 199.00 1.00
BUYSELL 2023-06-01 10:00:00 QZQ.US 50 USD 21.00 1051.00 1.00
BUYSELL 2024-08-01 10:00:00 QZQ.US -20 USD 25.00 499.00 1.00
# 2025: an intraday crossing short (100 sold holding 30, 70 bought back)
BUYSELL 2025-11-03 10:00:00 QZQ.US -100 USD 30.00 2999.00 1.00
BUYSELL 2025-11-03 10:02:00 QZQ.US 70 USD 30.10 2108.00 1.00
"""


class TestMissingHistoryFirstEpisode(unittest.TestCase):
    """A missing_history.json entry fills its first shortage only: a
    later year's short (shared exports bring it into every year) never
    grows the opening — the earlier year's sales stay matched."""

    def _project(self, year, entry):
        top = Path(private_dir()) / "taxes"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "t.tt").write_text(_MH_TT)
        d = top / str(year)
        d.mkdir()
        (d / "taxjson.toml").write_text(
            "[settings]\n" f"year = {year}\n" 'country = "usa"\n'
            'inputs_dir = "../inputs"\n' 'tax_date = "trade"\n'
            'base_currency = "USD"\n\n[accounts.margin]\n'
            'type = "taxable"\n')
        (d / "missing_history.json").write_text(json.dumps([entry]))
        return d

    @rule("US-BASIS-04")
    def test_a_later_short_does_not_grow_the_opening(self):
        d = self._project(2024, {"symbol": "QZQ.US", "account": "margin"})
        r = run_ok(self, d)
        self.assertIn("entry records no quantity", r.stdout + r.stderr)
        doc = tjs("-C", str(d), "sum", "--json")
        acct = json.loads(doc.stdout)["filing"]["accounts"][0]
        # The 2024 sale of 20 is matched to the 2023 purchase, not to
        # phantom opening shares the 2025 short would have added (it was
        # sent to manual reporting, out of the totals).
        self.assertEqual(acct["dispositions"], 1)
        self.assertAlmostEqual(acct["proceeds"], 499.00, places=2)

    @rule("CA-ACB-11")
    def test_the_engine_sizes_the_first_episode(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.missing_history import (MissingHistoryPairs,
                                                 synthesize_openings)

        def tx(date, qty):
            return TaxTransaction(action="BUYSELL", date=date,
                                  time="10:00:00", symbol="QZQ.TO",
                                  quantity=qty, currency="CAD", price=1.0,
                                  net_amount=-qty, account="margin")
        txs = [tx("2023-03-01", -10), tx("2023-06-01", 50),
               tx("2024-08-01", -20), tx("2025-11-03", -100),
               tx("2025-11-04", 70)]
        pairs = MissingHistoryPairs({("QZQ.TO", "margin")})
        _o, applied = synthesize_openings(txs, pairs, flag_stale=False)
        self.assertEqual(applied[0]["opening_qty"], 10)
        # A recorded quantity caps it.
        pairs.quantities[("QZQ.TO", "margin")] = 4.0
        _o, applied = synthesize_openings(txs, pairs, flag_stale=False)
        self.assertEqual(applied[0]["opening_qty"], 4.0)

    def test_find_missing_history_records_the_quantity(self):
        d = self._project(2024, {"symbol": "QZQ.US", "account": "margin"})
        (d / "missing_history.json").unlink()
        run_ok(self, d)
        r = tjs("-C", str(d), "find-missing-history", "--all-history",
                "--write-missing-history", str(d / "new.json"))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads((d / "new.json").read_text())
        q = {(e["symbol"], e["account"]): e.get("quantity") for e in rows}
        self.assertEqual(q.get(("QZQ.US", "margin")), 10)


class TestRedactAYearFolder(unittest.TestCase):
    """`taxjson redact` in a year folder: a single-folder project of that
    year (the shared exports, the year's slips and holdings/, its
    taxjson.toml and ticker.map), written in the year folder only, with
    the broker ids pseudonymised everywhere, holdings/ included."""

    def test_redacted_year_runs_as_a_single_project(self):
        top = multi("canada", years=(2024, 2025))
        repo = Path(__file__).resolve().parent.parent
        shutil.copy(repo / "examples" / "ib_demo.csv",
                    top / "inputs" / "margin" / "U1234567_2024.csv")  # pii-ok
        y = top / "2024"
        (y / "inputs" / "slips").mkdir(parents=True)
        (y / "inputs" / "slips" / "slips.toml").write_text("year = 2024\n")
        (y / "holdings").mkdir()
        (y / "holdings" / "U1234567_holdings.toml").write_text(  # pii-ok
            '[meta]\naccount = "U1234567"\nas_of = "2024-12-31"\n\n'  # pii-ok
            '[[holding]]\nsymbol = "QZQ.TO"\nquantity = 50\n'
            'currency = "CAD"\n')
        (y / "ticker.map").write_text("GLOBAL ZZOLDQ.TO ZZNEWQ.TO\n")
        run_ok(self, y)
        want = json.loads(tjs("-C", str(y), "sum", "--json").stdout)
        # A file link in the shared inputs is followed when it stays in
        # the folder holding the years, skipped when it leaves it.
        (top / "notes").mkdir()
        (top / "notes" / "n.txt").write_text("synthetic note\n")
        far = Path(private_dir()) / "private.txt"
        far.write_text("not for sharing\n")
        os.symlink(top / "notes" / "n.txt", top / "inputs" / "near.txt")
        os.symlink(far, top / "inputs" / "far.txt")
        outside = {k: v for k, v in _tree(top).items()
                   if not k.startswith("2024/")}
        r = tjs("-C", str(y), "redact")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual({k: v for k, v in _tree(top).items()
                          if not k.startswith("2024/")}, outside,
                         "redact wrote outside the year folder")
        red = y / "inputs_redact"
        copy = _tree(red)
        blob = b"".join(copy.values()) + "".join(copy).encode()
        self.assertNotIn(b"U1234567", blob)  # pii-ok (synthetic)
        self.assertIn("inputs/slips/slips.toml", copy)
        self.assertTrue(any(k.startswith("holdings/") for k in copy))
        self.assertIn("ticker.map", copy)
        self.assertIn("inputs/near.txt", copy)
        self.assertNotIn("inputs/far.txt", copy)
        self.assertIn("far.txt not copied", r.stderr)
        cfg = (red / "taxjson.toml").read_text()
        self.assertNotRegex(cfg, r"(?m)^inputs_dir")
        # It runs where it is, as a single-folder project, to the same
        # figures.
        run_ok(self, red)
        got = json.loads(tjs("-C", str(red), "sum", "--json").stdout)
        self.assertEqual(got["filing"], want["filing"])


class TestScaffolds(unittest.TestCase):
    def test_init_makes_the_layout_and_single_the_old_one(self):
        top = Path(private_dir()) / "t"
        r = tjs("init", "--country", "usa", "--year", "2025", str(top))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((top / "inputs" / "margin").is_dir())
        self.assertTrue((top / "2025" / "holdings").is_dir())
        cfg = (top / "2025" / "taxjson.toml").read_text()
        self.assertRegex(cfg, r'(?m)^inputs_dir +=  *"\.\./inputs"$')
        self.assertFalse((top / "taxjson.toml").exists())
        one = Path(private_dir()) / "s"
        r = tjs("init", "--single", "--country", "usa", "--year", "2025",
                str(one))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((one / "taxjson.toml").is_file())
        self.assertNotIn("inputs_dir =", (one / "taxjson.toml").read_text())

    def test_migrate_to_years_keeps_the_books(self):
        s = single("usa", 2025)
        run_ok(self, s)
        want = tjs("-C", str(s), "sum", "--json").stdout
        (s / "inputs" / "slips").mkdir()
        (s / "inputs" / "slips" / "t.csv").write_text("x\n")
        r = tjs("-C", str(s), "migrate", "--to-years", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((s / "2025").exists())
        r = tjs("-C", str(s), "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, r.stderr)
        y = s / "2025"
        self.assertTrue((y / "taxjson.toml").is_file())
        self.assertTrue((y / "inputs" / "slips" / "t.csv").is_file())
        self.assertFalse((s / "taxjson.toml").exists())
        self.assertTrue((s / "inputs" / "margin" / "trades.tt").is_file())
        run_ok(self, y)
        self.assertEqual(tjs("-C", str(y), "sum", "--json").stdout, want)


if __name__ == "__main__":
    unittest.main()
