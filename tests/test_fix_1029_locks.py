"""The 2026-10-29 fix round, locks group: the filed-year locks and the
readers of the years around them.

- TJ-0347 (TJ-0373) a lock an older release wrote whose filed
  dispositions hold another year's rows: estimate, carryover and
  handoff refuse the loss it carried, naming the lock and the re-close;
- TJ-0223 (TJ-0370) the carryover ledger offers to apply none of the
  gain a year's own [carryover] claimed entry used;
- TJ-0224 (TJ-0372) handoff takes a Dec 30-31 row the broker corrected
  after the close as the record's row, and reports the difference;
- TJ-0231 (TJ-0398) `migrate --to-years` records its edit of a frozen
  year's taxjson.toml in the lock; check-filed explains it, and any
  other change is still a changed input;
- TJ-0232 (TJ-0397) the checklist's filed-lock step is attention, not
  "locked; no drift", while a frozen year names the shared folders
  again;
- TJ-0271 (TJ-0410) `years`: a folder at a frozen year's lock path is
  a damaged lock, never an open year;
- TJ-0262 (TJ-0411) `years`' act lines name the year folder from where
  the command ran (`tjs -C ../2024 ...` inside 2025/);
- TJ-0251 (TJ-0375) form-export's readiness counts a sale with no
  purchase (and an unbooked in-kind move, test_fix_backlog_runcon) as
  `sum --json` does, with a `! ` line in its text view;
- TJ-0272 a closed year's deleted lock, named by the next year's
  prior_year_record, is a lock that cannot be read (estimate, amt,
  carryover, handoff, fx-cash v2), never "no record";
- TJ-0273 close-year takes the T1A carry-back from the estimate it
  runs (`losses_carried_back`) instead of building the carryover
  ledger a second time, except for a filed loss above the books' one;
- TJ-0210 `years` marks a filed year whose shared export changed one
  of its rows since the close (INPUTS `shared changed`, a `! ` line,
  `shared_exports_changed` in --json);
- TJ-0254 `compare`'s `prev` stays the last full run's after a `run
  --account`, and the comparison says when reports/ are partial;
- TJ-0211 a late election or send decision on a filed year saved in
  the shared folder is named by check-filed and marked by `years`
  (a per-entry comparison of manifest.json / sends.json);

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))


def _env(**extra):
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _cli(root, *args, cwd=None):
    """`taxjson -C ROOT ARGS` as a child of THIS tree's code (`root`
    None: no -C, run in `cwd`)."""
    pre = [] if root is None else ["-C", str(root)]
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             [*pre, *map(str, args)])
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(), timeout=900)


def _tmp(test):
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return Path(td.name)


# ------------------------------------------------------------- TJ-0347
# The audit's case (TJ-0373): a 2025 lock from before TJ-0298 holds the
# year's gain of 100 and a 2024 loss of 300 among its filed
# dispositions: filed_totals.gain -200, a net capital loss of 200
# carried into 2026.
_OLD_LOCK = {
    "year": 2025, "country": "canada",
    "settings": {"country": "canada"}, "accounts": {},
    "totals": {"realized": 100.0, "dispositions": 1},
    "filed_totals": {"gain": -200.0, "dispositions": 2},
    "filed_dispositions": [
        {"symbol": "ZZQ.TO", "date": "2025-03-01", "qty": 10,
         "proceeds": 200, "cost": 100, "gain": 100},
        {"symbol": "ZZR.TO", "date": "2024-03-01", "qty": 10,
         "proceeds": 100, "cost": 400, "gain": -300}],
    "carryforwards": {"net_capital_loss": {
        "opening": 0.0, "created": 200.0, "applied": 0.0,
        "closing": 200.0},
        "minimum_tax": {"created": 0.0, "closing": 0.0,
                        "recovered_federal": 0.0,
                        "closing_by_year": {}}}}


class TestLockWithAnotherYearsRows(unittest.TestCase):

    def _project(self, lock=None):
        root = _tmp(self) / "p"
        (root / "filed").mkdir(parents=True)
        (root / "filed" / "2025.json").write_text(
            json.dumps(lock or _OLD_LOCK))
        return root

    def _says_reclose(self, msg):
        msg = " ".join(str(msg).split())
        self.assertIn("2025.json", msg)
        self.assertIn("ZZR.TO 2024-03-01", msg)
        self.assertIn("close-year --force --filed-dispositions <CSV>", msg)

    def test_carryover_refuses_the_filed_figure(self):
        from taxjson.bin import taxjson_carryover as C
        root = self._project()
        with self.assertRaises(C.LockUnreadable) as cm:
            C.lock_figure(root / "filed" / "2025.json", "canada")
        self._says_reclose(cm.exception)

    def test_estimate_refuses_the_carried_loss(self):
        from taxjson.lib import carryforward as CF
        root = self._project()
        with self.assertRaises(CF.CarryInputError) as cm:
            CF.resolve_losses(root, {"year": 2026, "country": "canada"},
                              "canada", False)
        self._says_reclose(cm.exception)
        # A loss given by hand ([estimate] other_losses) reads no lock.
        self.assertEqual(CF.resolve_losses(
            root, {"year": 2026, "country": "canada"}, "canada", True),
            {"source": None, "notes": []})

    def test_minimum_tax_carryover_still_read(self):
        """The minimum tax the lock carried is the estimate's, not the
        filed dispositions': it is read as before."""
        from taxjson.lib import carryforward as CF
        root = self._project()
        r = CF.resolve_amt(root, {"year": 2026, "country": "canada"}, {})
        self.assertEqual(r["by_year"], {})
        self.assertIn("2025", r["source"])

    def test_handoff_refuses_the_record(self):
        from taxjson.lib import handoff
        root = self._project()
        with self.assertRaises(handoff.RecordError) as cm:
            handoff.load_record(root / "filed" / "2025.json")
        self._says_reclose(cm.exception)

    def test_a_lock_of_its_own_year_is_read(self):
        from taxjson.bin import taxjson_carryover as C
        from taxjson.lib import carryforward as CF
        lock = json.loads(json.dumps(_OLD_LOCK))
        lock["filed_dispositions"] = lock["filed_dispositions"][:1]
        lock["filed_totals"] = {"gain": 100.0, "dispositions": 1}
        lock["carryforwards"]["net_capital_loss"].update(
            created=0.0, closing=0.0)
        root = self._project(lock)
        self.assertEqual(C.lock_figure(root / "filed" / "2025.json",
                                       "canada")["figure"], 100.0)
        self.assertEqual(CF.resolve_losses(
            root, {"year": 2026, "country": "canada"}, "canada",
            False)["other_losses"], 0.0)

    def test_check_filed_shape_unchanged(self):
        """The lock is not damaged in its own year's checks (check-filed
        and `taxjson years` read it as before): only the readers of
        what it carried stop."""
        from taxjson.bin import taxjson_filed as F
        root = self._project()
        doc = F.read_lock(root / "filed" / "2025.json")
        self.assertEqual(doc["year"], 2025)


# ------------------------------------------------------------- TJ-0223
@rule("CA-CARRY-04")
class TestOwnClaimNotOfferedAgain(unittest.TestCase):
    """TJ-0223 (TJ-0370): 2024 a net loss of 200; 2025 a gain of 100
    with [carryover] claimed 2025 = 100. The balance is 100, but the
    2025 gain is used: nothing is left to apply to it."""

    def _ledger(self, claimed):
        from taxjson.bin import taxjson_carryover as C
        nets = {2024: {"net": -200.0, "dispositions": 1},
                2025: {"net": 100.0, "dispositions": 1}}
        return {r["year"]: r for r in
                C.build_canada_ledger(nets, claimed)["rows"]}

    def test_claim_equal_to_the_gain(self):
        rows = self._ledger({2025: 100.0})
        self.assertEqual(rows[2025]["carryforward_balance"], 100.0)
        self.assertEqual(rows[2025]["available_to_apply"], 0.0)

    def test_part_claimed(self):
        rows = self._ledger({2025: 40.0})
        self.assertEqual(rows[2025]["carryforward_balance"], 160.0)
        self.assertEqual(rows[2025]["available_to_apply"], 60.0)

    def test_no_claim(self):
        rows = self._ledger({})
        self.assertEqual(rows[2025]["available_to_apply"], 100.0)

    def test_brief_view_offers_nothing(self):
        from taxjson.bin import taxjson_carryover as C
        nets = {2024: {"net": -200.0, "dispositions": 1},
                2025: {"net": 100.0, "dispositions": 1}}
        out = C.render_brief(C.build_canada_ledger(nets, {2025: 100.0}),
                             "CAD", 2024)
        self.assertNotIn("apply up to", out)


# ------------------------------------------------------------- TJ-0224
class TestCorrectedDecemberRow(unittest.TestCase):
    """TJ-0224 (TJ-0372): the 2025 record counted a Dec 31 dividend of
    100; the corrected export here has the same row at 110. Not "in
    neither return": the 2025 return reported 10 less."""

    CFG = {"settings": {"year": 2026, "country": "canada",
                        "base_currency": "CAD", "tax_date": "settle"},
           "accounts": {"a": {"type": "taxable"}}}
    REC_ROW = {"account": "a", "action": "DIVIDEND", "symbol": "ZZQ.US",
               "date": "2025-12-31", "date_settle": "2025-12-31",
               "quantity": 0, "net": 100.0, "tax_date": "2025-12-31"}

    def _issues(self, net, notes=None):
        from taxjson.lib import handoff
        work = _tmp(self) / "work"
        work.mkdir()
        (work / "a_base.json").write_text(json.dumps({"transactions": [
            {"id": "d1", "action": "DIVIDEND", "date": "2025-12-31",
             "date_settle": "2025-12-31", "time": "10:00:00",
             "symbol": "ZZQ.US", "quantity": 0.0, "price": 0.0,
             "net_amount": net, "currency": "CAD", "account": "a"}]}))
        rec = {"year": 2025, "boundary_rows": [dict(self.REC_ROW)]}
        return handoff._boundary_issues(work, self.CFG, rec, "settle",
                                        set(), notes)

    @rule("CA-RPT-08")
    def test_the_difference_not_the_whole_row(self):
        out = self._issues(110.0)
        self.assertEqual(len(out), 1, out)
        it = out[0]
        self.assertEqual((it["amount"], it["recorded_amount"],
                          it["difference"]), (110.0, 100.0, 10.0))
        why = " ".join(it["why"].split())
        self.assertNotIn("neither return", why)
        self.assertIn("at 100.00; this project reads it at 110.00", why)
        self.assertIn("reported 10.00 less", why)

    @rule("CA-RPT-08")
    def test_same_amount_is_no_issue(self):
        self.assertEqual(self._issues(100.0), [])

    @rule("CA-RPT-08")
    def test_a_row_the_record_never_had_is_still_in_neither(self):
        from taxjson.lib import handoff
        work = _tmp(self) / "work"
        work.mkdir()
        (work / "a_base.json").write_text(json.dumps({"transactions": [
            {"id": "d1", "action": "DIVIDEND", "date": "2025-12-31",
             "date_settle": "2025-12-31", "time": "10:00:00",
             "symbol": "ZZR.US", "quantity": 0.0, "price": 0.0,
             "net_amount": 110.0, "currency": "CAD", "account": "a"}]}))
        rec = {"year": 2025, "boundary_rows": [dict(self.REC_ROW)]}
        out = handoff._boundary_issues(work, self.CFG, rec, "settle",
                                       set())
        self.assertEqual(len(out), 1)
        self.assertIn("neither return", out[0]["why"])


# ------------------------------------------------- a frozen, migrated year
_CA24_TOML = ('[settings]\nyear = 2024\ncountry = "canada"\n'
              'base_currency = "CAD"\nsource_currencies = []\n'
              'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
_CA24_TRADES = ("# made-up trades\n"
                "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 "
                "0.00\n"
                "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 20.00 100.00 "
                "0.00\n")


class _FrozenMigrated(unittest.TestCase):
    """A single-folder 2024 project, run, closed (its inputs frozen) and
    moved with `migrate --to-years`; each test works on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(_CA24_TRADES)
        (top / "taxjson.toml").write_text(_CA24_TOML)
        for args in (("run", "--no-input"), ("close-year", "--yes"),
                     ("migrate", "--to-years")):
            r = _cli(top, *args)
            assert r.returncode == 0, (args, r.stdout[-2000:],
                                       r.stderr[-2000:])
        cls.migrate_out = r.stdout
        r = _cli(top / "2024", "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.run_out = r.stdout + r.stderr
        cls.base = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy(self):
        top = _tmp(self) / "p"
        shutil.copytree(self.base, top, symlinks=True)
        return top


class TestMigrateEditOfAFrozenYear(_FrozenMigrated):

    def test_lock_records_the_edit(self):
        lock = json.loads((self.base / "2024" / "filed" / "2024.json")
                          .read_text())
        m = lock["frozen"]["migrated"]
        self.assertEqual(m["by"], "taxjson migrate --to-years")
        self.assertIn("settings.requires_taxjson", m["keys"])
        self.assertNotEqual(m["taxjson.toml"],
                            lock["frozen"]["inputs"]["taxjson.toml"])
        self.assertIn("as migrate's own", " ".join(
            self.migrate_out.split()))

    def test_run_and_check_filed_say_no_changed_input(self):
        self.assertNotIn("frozen input(s) changed", self.run_out)
        top = self.copy()
        r = _cli(top / "2024", "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = " ".join(r.stdout.split())
        self.assertNotIn("frozen input(s) changed", out)
        self.assertIn("taxjson.toml is as `taxjson migrate --to-years` "
                      "rewrote it on", out)
        self.assertIn("not a changed input", out)

    def test_a_later_edit_is_still_a_changed_input(self):
        top = self.copy()
        cfg = top / "2024" / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            'country = "canada"',
            'country = "canada"\ntax_date = "trade"', 1))
        r = _cli(top / "2024", "check-filed")
        out = " ".join(r.stdout.split())
        self.assertIn("1 frozen input(s) changed since the year was "
                      "closed: taxjson.toml", out)
        self.assertNotIn("rewrote it on", out)


class TestFrozenYearNamesTheSharedFoldersAgain(_FrozenMigrated):
    """TJ-0232 (TJ-0397): the frozen 2024 names inputs_dir = "../inputs"
    again; check-filed warns (exit 0) and the checklist said done."""

    def test_filed_lock_step_is_attention(self):
        from taxjson.lib import project_layout as PL
        top = self.copy()
        y = top / "2024"
        cfg = y / "taxjson.toml"
        cfg.write_text(PL.set_key_text(cfg.read_text(),
                                       ("settings", "inputs_dir"),
                                       "../inputs"))
        r = _cli(y, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        r = _cli(y, "checklist", "--run", "--only", "filed-lock",
                 "--json")
        doc = json.loads(r.stdout)
        item = next(i for i in doc["steps"] if i["id"] == "filed-lock")
        self.assertEqual(item["status"], "attention", item)
        self.assertIn("names inputs_dir again", item["detail"])
        self.assertIn("delete that line", item["detail"])

    def test_untouched_frozen_year_is_done(self):
        top = self.copy()
        r = _cli(top / "2024", "checklist", "--run", "--only",
                 "filed-lock", "--json")
        doc = json.loads(r.stdout)
        item = next(i for i in doc["steps"] if i["id"] == "filed-lock")
        self.assertEqual(item["status"], "done", item)


# ---------------------------------------------------- TJ-0271, TJ-0262
class TestYearsLockPathAndHints(unittest.TestCase):

    def test_a_folder_at_the_lock_path_is_a_damaged_lock(self):
        """TJ-0271 (TJ-0410)."""
        from test_fix_backlog_years import _years
        from taxjson.lib import project_layout as PL
        top = _tmp(self)
        y4, _y5 = _years(top)
        lock = y4 / "filed" / "2024.json"
        lock.unlink()
        lock.mkdir()
        rec = next(r for r in PL.years_report(top)["years"]
                   if r["year"] == 2024)
        self.assertTrue(rec["filed"])
        self.assertIn("filed/2024.json cannot be read (a directory)",
                      rec["problem"])
        r = _cli(top, "years")
        row = next(ln for ln in r.stdout.splitlines()
                   if ln.startswith("2024 "))
        self.assertIn("lock damaged", row)
        self.assertNotIn(" open ", row)

    def test_act_lines_from_a_year_folder(self):
        """TJ-0262 (TJ-0411): inside 2025/, `tjs -C 2024 ...` names
        2025/2024."""
        from test_fix_backlog_years import _years
        from taxjson.lib import project_layout as PL
        top = _tmp(self)
        y4, y5 = _years(top)
        (y4 / "work").mkdir()
        PL.record_filed_check(y4, {2024: "drifted"})
        r = _cli(None, "years", cwd=y5)
        self.assertIn("tjs -C ../2024 check-filed", r.stdout)
        r = _cli(None, "years", cwd=top)
        self.assertIn("tjs -C 2024 check-filed", r.stdout)
        r = _cli(None, "years", "--details", cwd=y5)
        self.assertIn("`taxjson -C ../2024 check-filed`",
                      " ".join(r.stdout.split()))


# ------------------------------------------------------------- TJ-0251
class TestFormExportNoPurchase(unittest.TestCase):
    """TJ-0251: a 2025 sale with no purchase in the files is in no
    total; form-export said its rows were complete."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(
            "# made-up trades\n"
            "BUYSELL 2025-01-02 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
            "BUYSELL 2025-06-02 10:00:00 ZZQ.TO -10 CAD 20.00 200.00 0.00\n"
            "BUYSELL 2025-07-02 10:00:00 ZZN.TO -10 CAD 30.00 300.00 0.00\n")
        (root / "taxjson.toml").write_text(_CA24_TOML.replace(
            "year = 2024", "year = 2025"))
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_json_readiness_as_sum(self):
        s = json.loads(_cli(self.root, "sum", "--json").stdout)
        self.assertIn("no-purchase", [i["kind"] for i in
                                      s["readiness"]["issues"]])
        doc = json.loads(_cli(self.root, "form-export", "--json").stdout)
        self.assertFalse(doc["readiness"]["complete"])
        self.assertEqual(
            sorted(i["kind"] for i in doc["readiness"]["issues"]),
            sorted(i["kind"] for i in s["readiness"]["issues"]))

    def test_text_act_line(self):
        r = _cli(self.root, "form-export")
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertTrue(any("no purchase" in a and "NOT in these rows" in a
                            and "tjs find-missing-history" in a
                            for a in acts), r.stdout)


# ------------------------------------------------------------- TJ-0272
_LOSS24 = ("# made-up trades\n"
           "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
           "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 5.00 25.00 0.00\n")


class _ClosedPerYear(unittest.TestCase):
    """Year folders sharing one inputs/; 2024 (a loss of 25) closed
    with its inputs frozen, 2025 started with new-year (its
    prior_year_record names ../2024/filed/2024.json); each test works
    on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(_LOSS24)
        (top / "taxjson.toml").write_text(_CA24_TOML)
        for d, args in ((top, ("migrate", "--to-years")),
                        (top / "2024", ("run", "--no-input")),
                        (top / "2024", ("close-year", "--yes")),
                        (top / "2024", ("new-year", "2025")),
                        (top / "2025", ("run", "--no-input"))):
            r = _cli(d, *args)
            assert r.returncode == 0, (args, r.stdout[-2000:],
                                       r.stderr[-2000:])
        cls.base = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy_top(self):
        top = _tmp(self) / "p"
        shutil.copytree(self.base, top, symlinks=True)
        return top


class TestDeletedPriorLock(_ClosedPerYear):
    """TJ-0272: each test deletes the 2024 lock in a copy."""

    def copy(self, delete=True):
        top = self.copy_top()
        if delete:
            (top / "2024" / "filed" / "2024.json").unlink()
        return top / "2025"

    _GONE = ("the 2024 close-year lock is missing, while the 2024 "
             "project's taxjson.toml still carries close-year's freeze "
             "note (the year was closed) — restore filed/2024.json from "
             "git or a backup, or run `taxjson close-year` in the 2024 "
             "project again")

    def _said(self, r):
        return " ".join((r.stdout + r.stderr).split())

    def test_the_lock_carries_the_loss(self):
        y5 = self.copy(delete=False)
        r = _cli(y5, "estimate", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            json.loads(r.stdout)["estimate"]["losses_opening"], 25.0)

    def test_estimate_and_amt_stop(self):
        y5 = self.copy()
        for cmd in ("estimate", "amt"):
            r = _cli(y5, cmd)
            self.assertEqual(r.returncode, 1, (cmd, r.stdout, r.stderr))
            self.assertIn("[settings] prior_year_record "
                          "(../2024/filed/2024.json): " + self._GONE,
                          self._said(r))

    def test_carryover_stops(self):
        y5 = self.copy()
        r = _cli(y5, "carryover")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(self._GONE, self._said(r))

    def test_handoff_names_the_lock(self):
        y5 = self.copy()
        r = _cli(y5, "handoff")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        said = self._said(r)
        self.assertIn("[settings] prior_year_record "
                      "(../2024/filed/2024.json): " + self._GONE, said)
        self.assertNotIn("then set [settings] prior_year_record", said)

    def test_fx_cash_v2_not_computed(self):
        y5 = self.copy()
        r = _cli(y5, "fx-cash", "--ledger", "v2", "--json")
        doc = json.loads(r.stdout)
        self.assertNotEqual(doc.get("status"), "computed", doc)
        self.assertIn(self._GONE, json.dumps(doc, ensure_ascii=False))

    def test_a_year_never_closed_is_still_no_record(self):
        """Without the freeze note the year was never closed: a missing
        lock stays "no record" (new-year names it before the close)."""
        y5 = self.copy()
        toml = y5.parent / "2024" / "taxjson.toml"
        toml.write_text("".join(
            ln for ln in toml.read_text().splitlines(keepends=True)
            if not ln.startswith("## frozen by")))
        r = _cli(y5, "estimate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        from taxjson.bin import taxjson_filed as TF
        self.assertIsNone(TF.missing_prior_lock(
            y5, {"year": 2025,
                 "prior_year_record": "../2024/filed/2024.json"}))


# ------------------------------------------------------------- TJ-0273
class TestCloseYearOneLedgerBuild(unittest.TestCase):
    """TJ-0273: close-year's carry-forwards block takes the carry-back
    from its estimate (`losses_carried_back`); the carryover ledger is
    built again only when a filed loss above the books' one may take
    more of the claim than the estimate's figure (capped by the books'
    loss) shows."""

    _CFG = {"settings": {"year": 2025, "country": "canada",
                         "province": "ON"},
            "carryover": {"claimed": {"2023": 300.0}}}

    def _block(self, est, ledger_back, filed_gain=None):
        from unittest import mock
        from taxjson.bin import taxjson_run as TR
        doc = {"estimate": dict(est, carry_sources={}),
               "filing": {"totals": {"gain": -est["losses_created"]}},
               "totals": {"realized": -est["losses_created"]}}
        res = subprocess.CompletedProcess([], 0, stdout=json.dumps(doc),
                                          stderr="")
        calls = []

        def _cb(root, year):
            calls.append(year)
            return ledger_back
        with mock.patch("taxjson.lib.dispatch.run_cmd",
                        return_value=res), \
                mock.patch.object(TR, "_carried_back", side_effect=_cb):
            block = TR._carryforwards_for_lock(
                Path("."), self._CFG, filed_totals=(
                    None if filed_gain is None else {"gain": filed_gain}))
        return block["net_capital_loss"], calls

    _EST = {"losses_opening": 0.0, "losses_created": 100.0,
            "losses_applied": 0.0, "losses_unused": 100.0}

    def test_the_estimates_figure_no_second_build(self):
        ncl, calls = self._block(dict(self._EST, losses_carried_back=30.0),
                                 ledger_back=30.0)
        self.assertEqual(calls, [])
        self.assertEqual((ncl["carried_back"], ncl["closing"]),
                         (30.0, 70.0))

    def test_no_claim_back(self):
        ncl, calls = self._block(dict(self._EST, losses_carried_back=0.0),
                                 ledger_back=0.0)
        self.assertEqual(calls, [])
        self.assertNotIn("carried_back", ncl)
        self.assertEqual(ncl["closing"], 100.0)

    def test_a_filed_loss_below_the_books_caps_it(self):
        ncl, calls = self._block(dict(self._EST, losses_carried_back=80.0),
                                 ledger_back=80.0, filed_gain=-50.0)
        self.assertEqual(calls, [])
        self.assertEqual((ncl["carried_back"], ncl["closing"]),
                         (50.0, 0.0))

    def test_a_filed_loss_above_the_books_rebuilds(self):
        """The estimate's 100.00 is capped by the books' loss; the
        filed loss of 400.00 takes the whole 300.00 claim."""
        ncl, calls = self._block(
            dict(self._EST, losses_carried_back=100.0),
            ledger_back=300.0, filed_gain=-400.0)
        self.assertEqual(calls, [2025])
        self.assertEqual((ncl["carried_back"], ncl["closing"]),
                         (300.0, 100.0))


# ------------------------------------------------------------- TJ-0210
class TestYearsSharedExportChanged(_ClosedPerYear):
    """TJ-0210: a correction saved in the shared inputs/ to a row of the
    filed 2024 showed only in a run's check-filed note."""

    def _edit(self, top, old, new):
        f = top / "inputs" / "margin" / "trades.tt"
        text = f.read_text()
        self.assertIn(old, text)
        f.write_text(text.replace(old, new))

    def test_a_changed_2024_row_is_marked(self):
        top = self.copy_top()
        self._edit(top, "ZZQ.TO -5 CAD 5.00 25.00", "ZZQ.TO -5 CAD 6.00 30.00")
        r = _cli(None, "years", cwd=top)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = next(ln for ln in r.stdout.splitlines()
                   if ln.startswith("2024 "))
        self.assertIn("shared changed", row)
        self.assertIn("! 2024: 1 shared file changed for 2024 since the "
                      "close — tjs -C 2024 check-filed", r.stdout)
        doc = json.loads(_cli(top, "years", "--json").stdout)
        by = {y["year"]: y for y in doc["years"]}
        self.assertEqual(by[2024]["shared_exports_changed"],
                         ["inputs/margin/trades.tt"])
        self.assertEqual(by[2025]["shared_exports_changed"], [])
        det = " ".join(_cli(top, "years", "--details").stdout.split())
        self.assertIn("1 shared file(s) gained, changed or lost a row of "
                      "2024 (or a decision on it, or a generic mapping that "
                      "reads it) since the close "
                      "(inputs/margin/trades.tt): the year reads its frozen "
                      "copy", det)

    def test_a_later_years_row_is_no_mark(self):
        top = self.copy_top()
        f = top / "inputs" / "margin" / "trades.tt"
        f.write_text(f.read_text() + "BUYSELL 2025-03-03 10:00:00 ZZQ.TO "
                     "-5 CAD 7.00 35.00 0.00\n")
        doc = json.loads(_cli(top, "years", "--json").stdout)
        self.assertEqual([y["shared_exports_changed"] for y in doc["years"]],
                         [[], []])
        self.assertNotIn("shared changed", _cli(top, "years").stdout)


# ------------------------------------------------------------- TJ-0254
class TestPrevAfterAnAccountRun(unittest.TestCase):
    """TJ-0254: a full run, a corrected sale rebuilt with `run --account
    margin`, then a full run: `prev` recorded the account run's mixed
    reports (margin.sum with the corrected sale already), so the full
    run's compare said margin.sum had not changed."""

    def _realized(self, doc):
        return {r["figure"]: (r["before"], r["after"])
                for r in doc["accounts"] if r["account"] == "margin"
                }.get("realized")

    def test_prev_is_the_last_full_run(self):
        root = _tmp(self) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        trades = root / "inputs" / "margin" / "trades.tt"
        trades.write_text(_CA24_TRADES)
        (root / "taxjson.toml").write_text(_CA24_TOML)
        for args in (("run", "--no-input"), ("run", "--no-input")):
            r = _cli(root, *args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        trades.write_text(_CA24_TRADES.replace("20.00 100.00", "22.00 110.00"))
        r = _cli(root, "run", "--no-input", "--account", "margin")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(_cli(root, "compare", "--json").stdout)
        self.assertEqual(doc["partial_now"], ["margin"])
        text = " ".join(_cli(root, "compare").stdout.split())
        self.assertIn("Partial: reports/ hold a `taxjson run --account "
                      "margin` over the last full run's", text)
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(_cli(root, "compare", "--json").stdout)
        self.assertEqual((doc["partial_now"], doc["partial_before"]),
                         ([], []))
        # The run before is the full one, its sale at 100.00.
        self.assertIn("margin.sum", doc["reports"]["changed"])
        self.assertEqual(self._realized(doc), (50.0, 60.0))
        self.assertFalse((root / "work" / "compare" / ".partial-run")
                         .exists())

    def test_a_saved_snapshot_of_partial_reports_says_so(self):
        root = _tmp(self) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(_CA24_TRADES)
        (root / "taxjson.toml").write_text(_CA24_TOML)
        for args in (("run", "--no-input"),
                     ("run", "--no-input", "--account", "margin"),
                     ("compare", "--save", "mixed"),
                     ("run", "--no-input")):
            r = _cli(root, *args)
            self.assertEqual(r.returncode, 0, (args, r.stdout, r.stderr))
        doc = json.loads(_cli(root, "compare", "mixed", "--json").stdout)
        self.assertEqual(doc["partial_before"], ["margin"])
        self.assertEqual(doc["partial_now"], [])


# ------------------------------------------------------------- TJ-0211
class TestSharedDecisionOnAFiledYear(_ClosedPerYear):
    """TJ-0211: an election or a send decision on 2024 saved by hand in
    the shared inputs/margin/ after 2024 was closed showed nowhere."""

    _ELECT = {"summary": "2024-03-01 split: ZZQ.TO \u2192 ZZQ.TO "
                         "(2-for-1, ratio 2, FMV 0.00 CAD)",
              "election": "ignore"}

    def _write(self, top, name, doc):
        (top / "inputs" / "margin" / name).write_text(
            json.dumps(doc, indent=1))

    def _note(self, top):
        r = _cli(top / "2024", "check-filed")
        return " ".join((r.stdout + r.stderr).split())

    def test_a_late_2024_election_is_named(self):
        top = self.copy_top()
        self._write(top, "manifest.json",
                    {"elections": {"ZZQ-split-1": self._ELECT}})
        self.assertIn("filed 2024: ../inputs/margin/manifest.json (1 "
                      "entry) in the shared folder holds a decision on 2024 "
                      "or earlier that differs from the copy 2024 was "
                      "closed with", self._note(top))
        doc = json.loads(_cli(top, "years", "--json").stdout)
        by = {y["year"]: y for y in doc["years"]}
        self.assertEqual(by[2024]["shared_exports_changed"],
                         ["inputs/margin/manifest.json"])

    def test_a_later_years_decision_is_no_difference(self):
        top = self.copy_top()
        (top / "2024" / "inputs" / "margin" / "sends.json").write_text(
            json.dumps({"schema_version": 1, "sends": {
                "ab12": {"decision": "self", "summary": "Kraken "
                         "withdrawal 2024-05-01 10:00:00 1 ZZC"}}}))
        # Rewritten whole by a later year: the 2024 entry as it was, a
        # 2025 one added.
        self._write(top, "sends.json", {"schema_version": 1, "sends": {
            "ab12": {"decision": "self", "summary": "Kraken withdrawal "
                     "2024-05-01 10:00:00 1 ZZC"},
            "cd34": {"decision": "gift", "summary": "Kraken withdrawal "
                     "2025-02-01 10:00:00 1 ZZC"}}})
        self._write(top, "manifest.json", {"elections": {
            "ZZQ-split-2": dict(self._ELECT, summary=self._ELECT[
                "summary"].replace("2024-03-01", "2025-03-03"))}})
        self.assertNotIn("in the shared folder hold a decision",
                         self._note(top))
        # The 2024 send re-decided: named.
        self._write(top, "sends.json", {"schema_version": 1, "sends": {
            "ab12": {"decision": "gift", "summary": "Kraken withdrawal "
                     "2024-05-01 10:00:00 1 ZZC"}}})
        self.assertIn("../inputs/margin/sends.json (1 entry) in the shared "
                      "folder", self._note(top))


if __name__ == "__main__":
    unittest.main()
