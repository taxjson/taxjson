"""The user's decisions of 2026-10-26 on backlog items, group `misc`:
TJ-0073 (div-estimate: a dividend paid while the books show a short),
TJ-0252 (a message about one account's file in a blended account's
`.sum`), TJ-0220 (`tjs init --force` over a read-only user file) and
TJ-0263 (a filed year whose lock was deleted). Synthetic data only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import div_estimate as DE
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _env(**kw):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
               TAXJSON_WIDTH="0",
               PYTHONPATH=str(REPO / "src") + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    env.update(kw)
    return env


def _tjs(*argv, cwd=None, **env):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(argv)),
        capture_output=True, text=True, env=_env(**env), cwd=cwd,
        stdin=subprocess.DEVNULL, check=False)


def _row(sym, d, act, q, amount=0.0, acct="margin", **kw):
    return (acct, dict(date=d, account=acct, symbol=sym, quantity=q,
                       net_amount=amount, action=act, currency="CAD", **kw))


def _short_book():
    """ZZSA.TO: long 100, sold 200 (short 100), a 25.00 dividend paid
    while short (ex-dividend 2024-05-01), bought 200 back (long 100).
    ZZSB.TO: sold 50 with no purchase in the books, then three
    dividends on 50 while short. ZZSC.TO: long all along (control)."""
    return [
        _row("ZZSA.TO", "2023-06-01", "BUYSELL", 100, 1000),
        _row("ZZSA.TO", "2023-11-15", "DIVIDEND", 100, 25),
        _row("ZZSA.TO", "2024-02-15", "DIVIDEND", 100, 25),
        _row("ZZSA.TO", "2024-04-01", "BUYSELL", -200, 2400),
        _row("ZZSA.TO", "2024-05-15", "DIVIDEND", 100, 25,
             ex_date="2024-05-01"),
        _row("ZZSA.TO", "2024-06-03", "BUYSELL", 200, 2400),
        _row("ZZSA.TO", "2024-08-15", "DIVIDEND", 100, 25),
        _row("ZZSB.TO", "2024-01-02", "BUYSELL", -50, 500),
        _row("ZZSB.TO", "2024-03-15", "DIVIDEND", 50, 5),
        _row("ZZSB.TO", "2024-06-14", "DIVIDEND", 50, 5),
        _row("ZZSB.TO", "2024-09-13", "DIVIDEND", 50, 5),
        _row("ZZSC.TO", "2023-06-01", "BUYSELL", 40, 400),
        _row("ZZSC.TO", "2024-03-15", "DIVIDEND", 40, 4),
        _row("ZZSC.TO", "2024-06-14", "DIVIDEND", 40, 4),
        _row("ZZSC.TO", "2024-09-13", "DIVIDEND", 40, 4),
    ]


class TestTJ0073DividendWhileShort(unittest.TestCase):
    """TJ-0073: a positive dividend row while the books show the account
    short still counts (decided 2026-10-26), and the trade-date walk to
    the ex-dividend date stays; div-estimate now warns, once per account
    and security, pointing at find-missing-history."""

    def test_payments_count_it_and_say_so_once(self):
        pays = DE.payments(_short_book())
        # The figures are unchanged: every positive row is a payment.
        self.assertEqual([p.gross for p in pays["ZZSA.TO"]], [25.0] * 4)
        self.assertEqual([p.gross for p in pays["ZZSB.TO"]], [5.0] * 3)
        self.assertEqual(pays.short_paid,
                         {("margin", "ZZSA.TO"): ["2024-05-15"],
                          ("margin", "ZZSB.TO"): ["2024-03-15",
                                                  "2024-06-14",
                                                  "2024-09-13"]})
        # One note per account and security, not one per row.
        self.assertEqual(len(pays.notes[("margin", "ZZSA.TO")]), 1)
        self.assertEqual(len(pays.notes[("margin", "ZZSB.TO")]), 1)
        self.assertIn("3 payments from 2024-03-15",
                      pays.notes[("margin", "ZZSB.TO")][0])
        self.assertIn("find-missing-history",
                      pays.notes[("margin", "ZZSA.TO")][0])
        self.assertNotIn(("margin", "ZZSC.TO"), pays.notes)
        # The walk is to the ex-dividend date: the same payment with its
        # ex-dividend date after the buy-back (long again) is no short.
        rows = [r for r in _short_book() if r[1]["symbol"] == "ZZSA.TO"]
        rows[4][1]["ex_date"] = "2024-06-05"
        rows[4][1]["date"] = "2024-06-14"
        self.assertEqual(DE.payments(rows).short_paid, {})
        # As of a date before the payment it is not read yet.
        self.assertNotIn(("margin", "ZZSA.TO"),
                         DE.payments(_short_book(),
                                     as_of="2024-05-01").short_paid)

    def test_command_warns_once_per_account_and_security(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "work").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [t for _a, t in _short_book()]}))
            (root / "work" / "margin_raw_gains.json").write_text(
                json.dumps({"inventory": [
                    dict(symbol="ZZSA.TO", qty=100, total_cost=1000,
                         currency="CAD"),
                    dict(symbol="ZZSC.TO", qty=40, total_cost=400,
                         currency="CAD")]}))
            r = _tjs("-C", str(root), "div-estimate", "--date",
                     "2024-10-15", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            warn = [ln for ln in r.stderr.splitlines()
                    if "while the books show it short" in ln]
            self.assertEqual(warn, [
                "taxjson div-estimate: warning: margin: ZZSA.TO dividend "
                "paid on 2024-05-15 while the books show it short",
                "taxjson div-estimate: warning: margin: 3 ZZSB.TO "
                "dividends paid from 2024-03-15 while the books show it "
                "short"], r.stderr)
            self.assertIn("`taxjson find-missing-history`", r.stderr)
            doc = json.loads(r.stdout)
            # --json: the flags carry it, the schema is the same.
            self.assertEqual(sorted(doc), sorted([
                "schema_version", "planning_only", "as_of_date", "months",
                "window_end", "tax_year", "currency", "rows", "totals",
                "omitted_no_history", "no_fx"]))
            by = {row["symbol"]: row for row in doc["rows"]}
            self.assertEqual(len(by["ZZSA.TO"]["flags"]), 1)
            self.assertIn("short ZZSA.TO", by["ZZSA.TO"]["flags"][0])
            self.assertEqual(by["ZZSC.TO"]["flags"], [])
            # The figures: 0.25 x 100 a quarter, as before.
            self.assertAlmostEqual(by["ZZSA.TO"]["next_amount"], 25.0)
            self.assertAlmostEqual(by["ZZSA.TO"]["window_amount"], 100.0)


_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@unittest.skipIf(_ROOT, "root ignores permissions")
class TestTJ0220InitForceReadOnly(unittest.TestCase):
    """TJ-0220: `tjs init --force` over a read-only taxjson.toml refuses,
    as every other writer does since TJ-0107: the same message and exit
    code as `format --write`, nothing written (no backup either)."""

    def _check(self, td, rel, *init):
        cfg = Path(td) / rel
        r = _tjs("init", "--country", "canada", "--year", "2024", *init,
                 cwd=td)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg.write_text(cfg.read_text() + "# kept\n")
        before = sorted(p.name for p in cfg.parent.iterdir())
        cfg.chmod(0o444)
        self.addCleanup(lambda: cfg.exists() and cfg.chmod(0o644))
        r = _tjs("init", "--country", "canada", "--year", "2024", *init,
                 "--force", cwd=td)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        text = " ".join(r.stderr.split())
        self.assertIn(f"cannot write {rel}: the file is read-only", text)
        self.assertIn(f"`chmod u+w {rel}` lets taxjson rewrite it. "
                      f"Nothing was changed.", text)
        self.assertTrue(cfg.read_text().endswith("# kept\n"))
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o444)
        self.assertEqual(sorted(p.name for p in cfg.parent.iterdir()),
                         before)
        # Writable again: --force re-templates it, as before.
        cfg.chmod(0o644)
        r = _tjs("init", "--country", "canada", "--year", "2024", *init,
                 "--force", cwd=td)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(cfg.read_text().endswith("# kept\n"))

    def test_year_folder(self):
        with tempfile.TemporaryDirectory() as td:
            self._check(td, "2024/taxjson.toml")

    def test_single_folder(self):
        with tempfile.TemporaryDirectory() as td:
            self._check(td, "taxjson.toml", "--single")


_TWO_ACCOUNTS = ('[settings]\nyear = 2024\ncountry = "canada"\n'
                 'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                 'local_timezone = "America/Toronto"\n\n'
                 '[accounts.margin]\ntype = "taxable"\n\n'
                 '[accounts.ib]\ntype = "taxable"\n')


class TestTJ0252BlendMessageNamesItsAccount(unittest.TestCase):
    """TJ-0252: the blended pass's .diag goes into every blended
    account's .sum DIAGNOSTICS; a message about one account's file
    (named by its inputs/<account>/ path) now names that account in
    front in the other accounts' banners. Nothing is dropped, and a
    blend-wide message stays as it is."""

    def test_collect_diagnostics(self):
        from taxjson.bin.taxjson_run import collect_diagnostics
        blend = (
            "warning: ATTENTION: inputs/margin/hand.tt:1 lists ZZQA.US / "
            "margin, an option the broker never coded CLOSING\n"
            "  its detail line\n"
            "note: superficial loss on ZZQB.US across the accounts\n"
            "warning: inputs/margin/a.tt:2 and inputs/ib/b.tt:3 move "
            "ZZQC.US between the two\n"
            "warning: ATTENTION: inputs/ib/x.tt:4 lists ZZQD.US / ib\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_TWO_ACCOUNTS)
            cache = root / "work"
            cache.mkdir()
            for a in ("margin", "ib"):
                (cache / f"{a}_blend.diag").write_text(blend)
            # Not the blend's: an account's own stage is never tagged.
            (cache / "ib_base.json.diag").write_text(
                "note: inputs/margin/zz.csv read by mistake\n")
            ib = collect_diagnostics(cache, "ib", blended=True).splitlines()
            margin = collect_diagnostics(cache, "margin",
                                         blended=True).splitlines()
        self.assertIn("[margin] warning: ATTENTION: inputs/margin/hand.tt:1 "
                      "lists ZZQA.US / margin, an option the broker never "
                      "coded CLOSING", ib)
        self.assertIn("  its detail line", ib)
        self.assertIn("note: superficial loss on ZZQB.US across the "
                      "accounts", ib)
        self.assertIn("warning: inputs/margin/a.tt:2 and inputs/ib/b.tt:3 "
                      "move ZZQC.US between the two", ib)
        self.assertIn("warning: ATTENTION: inputs/ib/x.tt:4 lists ZZQD.US "
                      "/ ib", ib)
        self.assertIn("note: inputs/margin/zz.csv read by mistake", ib)
        # margin's own banner: its own message as it was, ib's named.
        self.assertIn("warning: ATTENTION: inputs/margin/hand.tt:1 lists "
                      "ZZQA.US / margin, an option the broker never coded "
                      "CLOSING", margin)
        self.assertIn("[ib] warning: ATTENTION: inputs/ib/x.tt:4 lists "
                      "ZZQD.US / ib", margin)
        # Nothing dropped: the blend's five lines, and ib's own.
        self.assertEqual(len(ib), 6)
        self.assertEqual(len(margin), 5)

    def test_run_names_margin_in_ib_wash_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "mh2"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "ib").mkdir()
            os.chmod(root, 0o700)
            (root / "taxjson.toml").write_text(_TWO_ACCOUNTS)
            (root / "inputs" / "margin" / "hand.tt").write_text(
                'OPENING 2023-12-29 ZZL250117C00050000.US 2 cost=unknown '
                'reason="held before the exports"\n'
                "BUYSELL 2024-03-01 10:00:00 ZZL250117C00050000.US -2 USD "
                "3.00 598.70 1.30\n")
            (root / "inputs" / "ib" / "ib_demo.csv").write_bytes(
                (REPO / "examples" / "ib_demo.csv").read_bytes())
            r = _tjs("-C", str(root), "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = root / "reports"
            line = ("warning: ATTENTION: inputs/margin/hand.tt:1 lists "
                    "ZZL250117C00050000.US / margin")
            ib = [ln for ln in (rep / "ib_wash.sum").read_text()
                  .splitlines() if "never coded" in ln]
            self.assertEqual(len(ib), 1, ib)
            self.assertTrue(ib[0].startswith("[margin] " + line), ib)
            for name in ("margin.sum", "margin_wash.sum"):
                own = [ln for ln in (rep / name).read_text().splitlines()
                       if "never coded" in ln]
                self.assertEqual(len(own), 1, (name, own))
                self.assertTrue(own[0].startswith(line), (name, own))
            self.assertNotIn("never coded", (rep / "ib.sum").read_text())


_NOTE = ("## frozen by `taxjson close-year`: this year reads its own copy "
         "of the exports it was filed from")
_HEAD = ('[settings]\nyear = {y}\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n')
_TAIL = '\n[accounts.margin]\ntype = "taxable"\n'


def _frozen_2024(top: Path, *, newest=True):
    """A folder of exports shared by every year: 2024 closed with its
    inputs frozen (the note, its own tobase.map and inputs/), its lock
    deleted; with `newest` also 2025, open, reading the shared folders."""
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "tobase.map").write_text("# shared\n")
    y4 = top / "2024"
    (y4 / "inputs" / "margin").mkdir(parents=True)
    (y4 / "taxjson.toml").write_text(
        _HEAD.format(y=2024) + f'{_NOTE}\n# inputs_dir = "../inputs"\n'
        f'# tobase_map = "../tobase.map"\n' + _TAIL)
    (y4 / "tobase.map").write_text("# shared\n")
    (y4 / "filed").mkdir()
    if newest:
        y5 = top / "2025"
        y5.mkdir()
        (y5 / "taxjson.toml").write_text(
            _HEAD.format(y=2025) + 'inputs_dir = "../inputs"\n'
            'tobase_map = "../tobase.map"\n'
            'prior_year_record = "../2024/filed/2024.json"\n' + _TAIL)
    return y4


class TestTJ0263DeletedLockStaysFrozen(unittest.TestCase):
    """TJ-0263: a filed year whose lock was deleted while taxjson.toml
    still carries close-year's freeze note is treated like a damaged
    lock (TJ-0158): it stays frozen, `years` says to restore the lock
    or run close-year again, `migrate` leaves it alone, and the other
    callers give the damaged lock's answer."""

    def test_frozen_year_reads_the_note(self):
        from taxjson.lib import project_layout as PL
        with tempfile.TemporaryDirectory() as td:
            y4 = _frozen_2024(Path(td))
            self.assertTrue(PL.frozen_year(y4, 2024))
            self.assertTrue(PL.lock_missing_frozen(y4, 2024))
            self.assertEqual(sorted(PL.tobase_layout(Path(td))["frozen"]),
                             [2024])
            # A partial (--force before the year ended) lock froze
            # nothing and wrote no note: not frozen, not missing.
            (y4 / "filed" / "2024.json").write_text(json.dumps(
                {"year": 2024, "frozen": {"partial": True}}))
            (y4 / "taxjson.toml").write_text(
                _HEAD.format(y=2024) + 'inputs_dir = "../inputs"\n'
                + _TAIL)
            self.assertFalse(PL.frozen_year(y4, 2024))
            self.assertFalse(PL.lock_missing_frozen(y4, 2024))
            # A year never closed has no note: a missing lock is no
            # freeze.
            (y4 / "filed" / "2024.json").unlink()
            self.assertFalse(PL.frozen_year(y4, 2024))
            self.assertFalse(PL.lock_missing_frozen(y4, 2024))

    def test_years_and_migrate(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            _frozen_2024(top)
            r = _tjs("-C", str(top), "years")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            row = next(ln for ln in r.stdout.splitlines()
                       if ln.startswith("2024 "))
            self.assertIn("lock missing", row)
            self.assertIn("! 2024: filed/2024.json is missing: restore it "
                          "or run close-year again", r.stdout)
            self.assertIn("frozen copy of its own in 2024", r.stdout)
            self.assertNotIn("tjs migrate", r.stdout)
            self.assertNotIn("What differs", r.stdout)
            det = " ".join(_tjs("-C", str(top), "years", "--details")
                           .stdout.split())
            self.assertIn("restore the lock from git or a backup, or "
                          "`taxjson close-year` locks the year again", det)
            rep = json.loads(_tjs("-C", str(top), "years", "--json").stdout)
            y = rep["years"][0]
            self.assertEqual((y["filed"], y["tobase_frozen"]), (True, True))
            self.assertTrue(y["problem"].startswith(
                "filed/2024.json is missing: restore it or run close-year "
                "again"), y["problem"])
            r = _tjs("-C", str(top), "migrate", "--dry-run")
            self.assertNotIn("2024/tobase.map", r.stdout + r.stderr)
            r = _tjs("-C", str(top), "migrate")
            self.assertTrue((top / "2024" / "tobase.map").is_file())
            self.assertNotRegex((top / "2024" / "taxjson.toml").read_text(),
                                r"(?m)^tobase_map\s*=")

    def test_check_filed_warns_as_for_a_damaged_lock(self):
        with tempfile.TemporaryDirectory() as td:
            y4 = _frozen_2024(Path(td))
            r = _tjs("-C", str(y4), "check-filed")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("warning: filed 2024 could not be checked: "
                          "filed/2024.json is missing: restore it or run "
                          "close-year again", r.stderr)
            self.assertNotIn("No filed/<year>.json", r.stdout)

    def test_checklist_align_and_compare(self):
        from datetime import date
        from taxjson.bin import taxjson_run as TR
        from taxjson.lib import checklist as CL
        from taxjson.lib import compare as CMP
        with tempfile.TemporaryDirectory() as td:
            y4 = _frozen_2024(Path(td))
            cfg = {"settings": {"year": 2024, "country": "canada",
                                "base_currency": "CAD"},
                   "accounts": {"margin": {"type": "taxable"}}}
            ctx = CL.Ctx(root=y4, cfg=cfg, year=2024,
                         today=date(2025, 5, 1),
                         run_sub=lambda argv, timeout=900: (0, "", ""))
            for step in (CL.d_filed_lock, CL.d_lock_committed):
                res = step(ctx)
                self.assertEqual(res.status, "blocked", res)
                self.assertIn("filed/2024.json is missing", res.detail)
            self.assertTrue(CL._filed_after_year_end(ctx))
            self.assertEqual(TR._align_filed_year(y4), 2024)
            with self.assertRaises(CMP.CompareError) as cm:
                CMP.load(y4, cfg, CMP.FILED)
            self.assertIn("filed/2024.json is missing", str(cm.exception))
            # A year never closed (no note): as before.
            (y4 / "taxjson.toml").write_text(
                _HEAD.format(y=2024) + 'inputs_dir = "../inputs"\n'
                + _TAIL)
            self.assertEqual(CL.d_filed_lock(ctx).status, "todo")
            self.assertFalse(CL._filed_after_year_end(ctx))
            self.assertIsNone(TR._align_filed_year(y4))

    def test_new_year_reads_the_shared_folders(self):
        """new-year from it: the next year reads the shared folders,
        never the freeze note copied into a year that was not closed
        (which then counted as a frozen year with its lock missing);
        the same with a damaged lock (TJ-0158)."""
        for damaged in (False, True):
            with tempfile.TemporaryDirectory() as td:
                top = Path(td)
                y4 = _frozen_2024(top, newest=False)
                if damaged:
                    (y4 / "filed" / "2024.json").write_text("{bad")
                r = _tjs("-C", str(top), "new-year", "2025")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                text = (top / "2025" / "taxjson.toml").read_text()
                self.assertNotIn("## frozen by", text)
                self.assertRegex(text, r'(?m)^inputs_dir\s*= "../inputs"')
                self.assertRegex(text,
                                 r'(?m)^tobase_map\s*= "../tobase.map"')
                if not damaged:
                    self.assertIn("warning: 2024: filed/2024.json is "
                                  "missing: restore it or run close-year "
                                  "again", r.stderr)
                    self.assertNotIn("is not locked yet", r.stderr)


if __name__ == "__main__":
    unittest.main()
