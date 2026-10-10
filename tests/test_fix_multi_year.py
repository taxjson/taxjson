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


def _mh_tx(date, qty, symbol="QZQ.TO", account="margin"):
    from taxjson.lib.core import TaxTransaction
    return TaxTransaction(action="BUYSELL", date=date, time="10:00:00",
                          symbol=symbol, quantity=qty, currency="CAD",
                          price=1.0, net_amount=-qty, account=account)


def _mh_open(txs, until, quantity=None, pair=("QZQ.TO", "margin")):
    from taxjson.lib.missing_history import (MissingHistoryPairs,
                                             synthesize_openings)
    pairs = MissingHistoryPairs({pair})
    if quantity is not None:
        pairs.quantities[pair] = float(quantity)
    _o, applied = synthesize_openings(txs, pairs, flag_stale=False,
                                      until=until)
    return applied[0]


class TestMissingHistoryYearWindow(unittest.TestCase):
    """A pair with no .tt line (a find-missing-history suggestion, an old
    missing_history.json entry `taxjson migrate` converts) with no
    `quantity` fills the deepest shortage of the rows dated up to the
    project year's end — main's
    peak-short sizing within that window; rows after the year end
    (later years' exports, shared by every year) never size it. A
    recorded `quantity` sets the opening exactly. A position that goes
    short again once its opening is used up is said (ATTENTION)."""

    @rule("CA-ACB-11")
    def test_shares_held_before_the_data_stay_unknown_cost(self):
        # 100 held before the data: sell 60, buy 100, sell 140. Every
        # sale draws on a pool still holding unknown-cost shares: the
        # opening is the peak shortage (100), not the first dip (60).
        txs = [_mh_tx("2024-02-01", -60), _mh_tx("2024-04-01", 100),
               _mh_tx("2024-09-01", -140)]
        e = _mh_open(txs, "2024-12-31")
        self.assertEqual(e["opening_qty"], 100)
        self.assertNotIn("short_again", e)

    @rule("CA-ACB-11")
    def test_two_missing_transfer_ins(self):
        # Two lots transferred in with no record: 20 sold, 20 bought, 50
        # sold — the opening covers the deeper second shortage.
        txs = [_mh_tx("2024-02-01", -20), _mh_tx("2024-03-01", 20),
               _mh_tx("2024-06-01", -50)]
        self.assertEqual(_mh_open(txs, "2024-12-31")["opening_qty"], 50)

    @rule("CA-ACB-11")
    def test_a_later_year_never_sizes_an_earlier_one(self):
        txs = [_mh_tx("2023-03-01", -10), _mh_tx("2023-06-01", 50),
               _mh_tx("2024-08-01", -20), _mh_tx("2025-11-03", -100),
               _mh_tx("2025-11-04", 70)]
        e24 = _mh_open(txs, "2024-12-31")
        self.assertEqual(e24["opening_qty"], 10)
        self.assertEqual(e24["opening_all_rows"], 80)
        # ... and the 2025 shortage beyond it is said, not silent.
        self.assertEqual(e24["short_again"],
                         {"date": "2025-11-03", "qty": 70.0})
        from taxjson.lib.missing_history import short_again_message
        msg = short_again_message(e24)
        self.assertIn("goes short again on 2025-11-03", msg)
        self.assertIn("does not change 2024", msg)
        # The 2025 year sizes its own (the 2025 shortage included).
        self.assertEqual(_mh_open(txs, "2025-12-31")["opening_qty"], 80)
        # No tax year (a standalone tool): every row, as before.
        self.assertEqual(_mh_open(txs, None)["opening_qty"], 80)

    @rule("CA-ACB-11")
    def test_quantity_sets_the_opening_exactly(self):
        txs = [_mh_tx("2024-02-01", -60), _mh_tx("2024-04-01", 100),
               _mh_tx("2024-09-01", -140)]
        raised = _mh_open(txs, "2024-12-31", quantity=150)
        self.assertEqual(raised["opening_qty"], 150)
        self.assertNotIn("short_again", raised)
        lowered = _mh_open(txs, "2024-12-31", quantity=80)
        self.assertEqual(lowered["opening_qty"], 80)
        self.assertEqual(lowered["recorded_quantity"], 80)
        # Lowered below the shortage: the position goes short again.
        self.assertEqual(lowered["short_again"],
                         {"date": "2024-09-01", "qty": 20.0})
        from taxjson.lib.missing_history import short_again_message
        self.assertIn("Raise `quantity`", short_again_message(lowered))

    def test_the_alert_is_printed(self):
        import contextlib
        import io
        from taxjson.lib.missing_history import (MissingHistoryPairs,
                                                 synthesize_openings)
        txs = [_mh_tx("2024-02-01", -10), _mh_tx("2024-03-01", 10),
               _mh_tx("2025-02-01", -30)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            synthesize_openings(txs, MissingHistoryPairs(
                {("QZQ.TO", "margin")}), until="2024-12-31")
        self.assertIn("warning: ATTENTION: missing_history.tt lists "
                      "QZQ.TO / margin: the position goes short again on "
                      "2025-02-01 (20 units)", err.getvalue())

    def _project(self, year, entry, tt=None, shared=True, sheltered=False,
                 country="usa"):
        top = Path(private_dir()) / "taxes"
        d = top / str(year) if shared else top
        ins = top / "inputs" if shared else d / "inputs"
        acct = "tfsa" if sheltered else "margin"
        (ins / acct).mkdir(parents=True)
        (ins / acct / "t.tt").write_text(tt or _MH_TT)
        d.mkdir(exist_ok=True)
        cur = "USD" if country == "usa" else "CAD"
        (d / "taxjson.toml").write_text(
            "[settings]\n" f"year = {year}\n" f'country = "{country}"\n'
            + ('inputs_dir = "../inputs"\n' if shared else "")
            + ('tax_date = "trade"\n' if country == "usa"
               else 'tax_date = "settle"\n')
            + f'base_currency = "{cur}"\n\n[accounts.{acct}]\n'
            + ('type = "sheltered"\n' if sheltered
               else 'type = "taxable"\n'))
        # (the .tt OPENING cost=unknown line the entry converts to, sized
        # as `taxjson migrate` sizes it)
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        from _mh import tt_lines, write_lines
        rows = [r for r in (parse_tt_line(ln, acct)
                            for ln in (tt or _MH_TT).splitlines()) if r]
        write_lines(ins, tt_lines(rows, [entry], until=f"{year}-12-31"))
        return d

    @rule("US-BASIS-04")
    def test_a_later_years_short_does_not_change_an_earlier_year(self):
        d = self._project(2024, {"symbol": "QZQ.US", "account": "margin"})
        r = run_ok(self, d)
        out = r.stdout + r.stderr
        # The line opens 10 (the shortage through 2024-12-31, as
        # migrate sized the entry); the 2025 short beyond it is said.
        self.assertIn("missing_history.tt:1 opens 10 QZQ.US / margin: the "
                      "position goes short again on 2025-11-03", out)
        doc = tjs("-C", str(d), "sum", "--json")
        acct = json.loads(doc.stdout)["filing"]["accounts"][0]
        # The 2024 sale of 20 is matched to the 2023 purchase, not to
        # opening shares the 2025 short would have added (it was sent
        # to manual reporting, out of the totals).
        self.assertEqual(acct["dispositions"], 1)
        self.assertAlmostEqual(acct["proceeds"], 499.00, places=2)
        # find-missing-history reports the size the run applies.
        # (each row's detail: --details, docs/output-style.md)
        r = tjs("-C", str(d), "find-missing-history", "--details")
        self.assertIn("the run opens the 10 units its line states",
                      r.stdout)

    @rule("CA-ACB-11")
    def test_single_folder_keeps_every_sale_unknown_cost(self):
        tt = ("BUYSELL 2024-02-01 10:00:00 QZQ.TO -60 CAD 20.00 1190.05 9.95\n"
              "BUYSELL 2024-04-01 10:00:00 QZQ.TO 100 CAD 21.00 2109.95 9.95\n"
              "BUYSELL 2024-09-01 10:00:00 QZQ.TO -140 CAD 25.00 3490.05 9.95\n")
        d = self._project(2024, {"symbol": "QZQ.TO", "account": "margin"},
                          tt=tt, shared=False, country="canada")
        r = run_ok(self, d)
        out = r.stdout + r.stderr
        # Every row is in the year: the year end decides nothing.
        self.assertNotIn("records no quantity", out)
        self.assertNotIn("goes short again", out)
        doc = json.loads(tjs("-C", str(d), "sum", "--json").stdout)
        acct = doc["filing"]["accounts"][0]
        # Both sales draw on a pool holding unknown-cost shares: none
        # is in the totals.
        self.assertEqual(acct["dispositions"], 0)

    @rule("CA-ACB-11")
    def test_registered_account_never_goes_short(self):
        tt = ("BUYSELL 2024-02-01 10:00:00 QZQ.TO -60 CAD 20.00 1190.05 9.95\n"
              "BUYSELL 2024-04-01 10:00:00 QZQ.TO 100 CAD 21.00 2109.95 9.95\n"
              "BUYSELL 2024-09-01 10:00:00 QZQ.TO -140 CAD 25.00 3490.05 9.95\n")
        d = self._project(2024, {"symbol": "QZQ.TO", "account": "tfsa"},
                          tt=tt, shared=False, sheltered=True,
                          country="canada")
        r = tjs("-C", str(d), "run", "--no-input", "--strict")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out[-3000:])
        self.assertNotIn("ATTENTION: short:", out)
        self.assertNotIn("goes short again", out)

    def test_find_missing_history_records_the_quantity(self):
        d = self._project(2024, {"symbol": "QZQ.US", "account": "margin"})
        f = d.parent / "inputs" / "margin" / "missing_history.tt"
        f.unlink()
        run_ok(self, d)
        r = tjs("-C", str(d), "find-missing-history", "--all-history",
                "--write-missing-history")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OPENING 2023-02-28 QZQ.US 10 cost=unknown reason="
                      "\"find-missing-history: the shortage of the rows "
                      "through 2024-12-31\"", f.read_text())


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

    def test_migrate_to_years_keeps_paths_into_moved_folders(self):
        from taxjson.lib.tomlcompat import tomllib
        s = single("usa", 2025)
        (s / "holdings").mkdir()
        (s / "holdings" / "margin.toml").write_text("")
        (s / "notes.txt").write_text("mine\n")
        cfg = (s / "taxjson.toml").read_text().replace(
            '[accounts.margin]\ntype = "taxable"\n',
            '[accounts.margin]\ntype = "taxable"\nholdings = '
            '["holdings/margin.toml", "./holdings/b.toml", '
            '"inputs/margin/pos.toml"]\n')
        (s / "taxjson.toml").write_text(cfg)
        r = tjs("-C", str(s), "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = tomllib.loads((s / "2025" / "taxjson.toml").read_text())
        # holdings/ moves into the year folder with taxjson.toml: its
        # paths stay as written; inputs/ stays at the top.
        self.assertEqual(doc["accounts"]["margin"]["holdings"],
                         ["holdings/margin.toml", "./holdings/b.toml",
                          "../inputs/margin/pos.toml"])
        self.assertIn("Left at the top (no year reads them", r.stdout)
        self.assertIn("notes.txt", r.stdout)

    def test_migrate_to_years_refuses_a_legacy_map_first(self):
        s = single("usa", 2025)
        (s / "yf_ticker.map").write_text("# old\n")
        r = tjs("-C", str(s), "migrate", "--to-years")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("yf_ticker.map", r.stdout + r.stderr)
        self.assertFalse((s / "2025").exists())
        self.assertTrue((s / "taxjson.toml").is_file())


if __name__ == "__main__":
    unittest.main()
