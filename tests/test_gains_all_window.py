"""A windowed view over more than the tax year (`stats 2y`, `winners
all`, `ccd-sum 2023`, --all-history): the seven views that read the
tax year's gains files (stats, win-rates, winners, losers, ccd-sum,
leaps, leaps-sum) read the full-history file built on demand from the
same consolidated base-currency book (work/<account>_gains_all.json),
said in an Info line, instead of warning that the rows outside the
year are missing. Synthetic books only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _paths import SRC
from _qa_project import console, project, tj
from _support.qt_trades import QT_HEAD
from _tmpfiles import private_dir
from taxjson.lib.records import record_path, sha256_file

ROWS = (QT_HEAD
        + "2023-02-01,2023-02-03,Buy,ZZQ,ZZQ CORP,100,40.00,-4000.00,0,"
          "-4000.00,USD,Trades,55500001,Margin\n"           # pii-ok
        + "2023-06-01,2023-06-05,Sell,ZZQ,ZZQ CORP,-50,30.00,1500.00,0,"
          "1500.00,USD,Trades,55500001,Margin\n"            # pii-ok
        + "2024-01-02,2024-01-04,Buy,ZZQ,ZZQ CORP,100,50.00,-5000.00,0,"
          "-5000.00,USD,Trades,55500001,Margin\n"           # pii-ok
        + "2024-03-01,2024-03-05,Sell,ZZQ,ZZQ CORP,-150,60.00,9000.00,0,"
          "9000.00,USD,Trades,55500001,Margin\n")           # pii-ok

NOTE = "read from the full-history books"
OLD_WARNING = "cover tax year"
VIEWS = ("stats", "win-rates", "winners", "losers", "ccd-sum", "leaps",
         "leaps-sum")


def _json(root, *args):
    r = tj(root, *args, "--json")
    return json.loads(r.stdout), r


class TestTwoYearProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "two",
                           {"margin/questrade.csv": ROWS})
        tj(cls.root, "run", "--no-input")
        cls.all_file = cls.root / "work" / "margin_gains_all.json"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_default_window_is_the_tax_year_file(self):
        d, r = _json(self.root, "stats")
        self.assertEqual(d["classes"]["long-shares"]["trades"], 1)
        self.assertNotIn(NOTE, r.stderr)
        self.assertNotIn(OLD_WARNING, r.stderr)

    def test_stats_over_all_history_counts_both_years(self):
        d, r = _json(self.root, "stats", "all")
        self.assertEqual(d["classes"]["long-shares"]["trades"], 2)
        self.assertEqual(d["classes"]["long-shares"]["losses"], 1)
        self.assertIn(NOTE, r.stderr)
        self.assertNotIn(OLD_WARNING, r.stderr)
        self.assertTrue(self.all_file.is_file())
        d2, _r = _json(self.root, "stats", "--all-history")
        self.assertEqual(d2["classes"]["long-shares"]["trades"], 2)
        d3, _r = _json(self.root, "stats", "2y")
        self.assertEqual(d3["scope"], "last 2y")

    def test_an_earlier_year_window(self):
        d, r = _json(self.root, "stats", "2023")
        self.assertEqual(d["scope"], "tax year 2023")
        self.assertEqual(d["classes"]["long-shares"]["trades"], 1)
        self.assertLess(d["classes"]["long-shares"]["net_pl"], 0)
        w, _r = _json(self.root, "losers", "2023")
        self.assertEqual([x["ticker"] for x in w["rows"]], ["ZZQ.US"])
        self.assertLess(w["total_gain"], 0)
        self.assertEqual(w["basis"], "wash-adjusted, full history")

    def test_in_year_rows_equal_the_filing_file(self):
        """The full-history file comes from the same consolidated book
        with the same flags: the tax year's rows match."""
        year, _r = _json(self.root, "winners")
        both, _r = _json(self.root, "winners", "all")
        prior, _r = _json(self.root, "winners", "2023")
        self.assertAlmostEqual(both["total_gain"],
                               year["total_gain"] + prior["total_gain"], 2)
        s_year, _r = _json(self.root, "stats")
        s_all, _r = _json(self.root, "stats", "all")
        self.assertAlmostEqual(
            s_all["classes"]["long-shares"]["largest_win"],
            s_year["classes"]["long-shares"]["largest_win"], 2)

    def test_every_view_takes_the_wide_window(self):
        for view in VIEWS:
            r = tj(self.root, view, "all")
            self.assertIn(NOTE, r.stderr, (view, console(r)))
            self.assertNotIn(OLD_WARNING, r.stderr, view)
        w, _r = _json(self.root, "win-rates", "all")
        self.assertEqual(w["total"]["trades"], 2)

    def test_built_every_time_a_view_reads_it(self):
        # No record's word serves a view (the owner's rule, TJ-4307): with
        # nothing changed, the next view builds the file again. Its record is
        # still written, for the sweep (`_vouched`).
        tj(self.root, "stats", "all")
        old = self.all_file.stat().st_mtime_ns - 10 ** 9
        os.utime(self.all_file, ns=(old, old))
        tj(self.root, "winners", "all")
        self.assertGreater(self.all_file.stat().st_mtime_ns, old)
        rec = json.loads(record_path(self.all_file).read_text(encoding="utf-8"))
        self.assertEqual(rec["out"], sha256_file(self.all_file))

    def test_built_once_per_command(self):
        # A command that reads the file again (the grant expiries, a pool's
        # covers, a blend's members) takes the one it built: its own build.
        # The next command builds it again (`run_state.begin_command`).
        from taxjson.cli import common
        from taxjson.pipeline import run_state
        settings = common.load_settings(self.root, missing_ok=True)
        work = self.root / "work"
        self.addCleanup(run_state.begin_command)
        with mock.patch.object(common, "run_to_file", wraps=common.run_to_file) as built:
            run_state.begin_command()
            for _ in range(2):
                self.assertEqual(common._gains_all_file(self.root, work, "margin", settings),
                                 self.all_file)
            self.assertEqual(built.call_count, 1)
            run_state.begin_command()
            common._gains_all_file(self.root, work, "margin", settings)
            self.assertEqual(built.call_count, 2)

    def test_the_tax_year_files_are_not_the_full_history_one(self):
        from taxjson.lib.report_model import resolve_gains_files
        files = resolve_gains_files(self.root / "work")
        self.assertEqual(list(files), ["margin"])
        self.assertFalse(files["margin"].name.endswith("_gains_all.json"))


def _entry(check, file):
    """An account's entry in the run's record (lib/report_model
    `record_loss_checks`): its tax-year file's check."""
    return {"tax_year": {"file": file, "check": check}}


class TestBasisLabel(unittest.TestCase):
    """The label says what the superficial-loss / wash-sale rule checked
    of the sales (backlog TJ-5441, TJ-5471, TJ-5473). A tax-year file: from
    the run's record of what checked each account's file (lib/report_model
    `loss_checks`). A full-history file: from what its own build read, kept
    in the file's record (`record_full_history_check`), never from the tax
    year's record nor from its name. "wash-adjusted" when every account of
    the set a rule reaches was checked, by the run's pass across accounts,
    by a full-history build that read all the rule needs, or by the
    account's own engine pass where that is the whole check, and there is
    one; else "pre-wash"; ", full history" added for full-history files.
    margin (taxable, the pass's file), m2 (taxable, built by `tjs run
    --account` alone: not yet), rrsp (registered), uscoin (a US crypto
    account's blended file), coins (a lone Canadian crypto account, no
    registered account with inputs)."""

    CHECKS = {"margin": _entry("pass", "margin_gains_wash.json"),
              "m2": _entry("not_yet", "m2_gains.json"),
              "rrsp": _entry("none", "rrsp_gains.json"),
              "uscoin": _entry("none", "uscoin_gains_wash.json"),
              "coins": _entry("own", "coins_gains.json")}
    # What each account's full-history build read.
    BUILDS = {"margin": "pass", "m2": "not_yet", "rrsp": "none", "uscoin": "none",
              "coins": "own"}

    def setUp(self):
        self.work = Path(private_dir()) / "work"
        self.work.mkdir()

    def _file(self, account, kind, builds):
        from taxjson.lib.report_model import record_full_history_check
        path = self.work / f"{account}_{kind}.json"
        path.write_text('{"transactions": []}\n', encoding="utf-8")
        if kind == "gains_all" and account in builds:
            record_full_history_check(path, builds[account])
        return path

    def _label(self, checks=CHECKS, builds=BUILDS, **files):
        from taxjson.lib.report_model import gains_basis_label
        return gains_basis_label({a: self._file(a, kind, builds) for a, kind in files.items()},
                                 checks=checks)

    def test_the_tax_year(self):
        self.assertEqual(self._label(margin="gains_wash"), "wash-adjusted")
        # Another file than the one the record names is served: not checked.
        self.assertEqual(self._label(margin="gains"), "pre-wash")
        self.assertEqual(self._label(m2="gains"), "pre-wash")
        self.assertEqual(self._label(rrsp="gains"), "pre-wash")
        # A US crypto account's blended file holds no denial.
        self.assertEqual(self._label(uscoin="gains_wash"), "pre-wash")
        self.assertEqual(self._label(margin="gains_wash", rrsp="gains"), "wash-adjusted")
        self.assertEqual(self._label(rrsp="gains", uscoin="gains_wash"), "pre-wash")

    def test_the_engines_own_pass_is_the_whole_check(self):
        # TJ-5473: it said "pre-wash" over the loss its rule denied.
        self.assertEqual(self._label(coins="gains"), "wash-adjusted")
        self.assertEqual(self._label(coins="gains_all"), "wash-adjusted, full history")
        self.assertEqual(self._label(coins="gains", rrsp="gains"), "wash-adjusted")

    def test_every_account_the_rule_reaches(self):
        # The review's F1: one checked account made a set "wash-adjusted"
        # over another's loss the pass never saw.
        self.assertEqual(self._label(coins="gains", m2="gains"), "pre-wash")
        self.assertEqual(self._label(margin="gains_wash", m2="gains"), "pre-wash")
        self.assertEqual(self._label(margin="gains_wash", coins="gains", rrsp="gains"),
                         "wash-adjusted")

    def test_a_full_history_file_by_its_own_build(self):
        # The review's F3: a full-history file counted as checked whatever
        # built it.
        self.assertEqual(self._label(margin="gains_all"), "wash-adjusted, full history")
        self.assertEqual(self._label(m2="gains_all"), "pre-wash, full history")
        self.assertEqual(self._label(margin="gains_all", m2="gains_all"), "pre-wash, full history")
        self.assertEqual(self._label(margin="gains_all", rrsp="gains_all"),
                         "wash-adjusted, full history")
        self.assertEqual(self._label(rrsp="gains_all"), "pre-wash, full history")
        self.assertEqual(self._label(rrsp="gains_all", uscoin="gains_all"),
                         "pre-wash, full history")
        # A list: each file's account from its name.
        from taxjson.lib.report_model import gains_basis_label
        self.assertEqual(gains_basis_label([self._file("margin", "gains_wash", {}),
                                            self._file("rrsp", "gains_all", self.BUILDS)],
                                           checks=self.CHECKS),
                         "wash-adjusted, full history")

    def test_the_tax_years_record_says_nothing_of_a_full_history_file(self):
        # The verify's V3: m2's tax-year file is not yet checked (built by
        # `tjs run --account`), its full-history build read the whole check
        # (it is the only taxable account, with the registered book): the
        # label said "pre-wash, full history" over the build's denials.
        self.assertEqual(self._label(builds={"m2": "pass"}, m2="gains_all"),
                         "wash-adjusted, full history")
        # And the other way (V1): the pass checked margin's tax-year file;
        # a full-history build that read part of the rule's books, or one
        # with nothing on record, is not checked by it.
        self.assertEqual(self._label(builds={"margin": "not_yet"}, margin="gains_all"),
                         "pre-wash, full history")
        self.assertEqual(self._label(builds={}, margin="gains_all"), "pre-wash, full history")
        self.assertEqual(self._label(builds={}, coins="gains_all"), "pre-wash, full history")

    def test_a_full_history_check_is_of_the_bytes_it_was_recorded_for(self):
        from taxjson.lib.report_model import full_history_check, gains_basis_label
        path = self._file("margin", "gains_all", self.BUILDS)
        self.assertEqual(full_history_check(path), "pass")
        # The fingerprint, not the name: the same bytes written again hold.
        path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(full_history_check(path), "pass")
        path.write_text('{"transactions": [1]}\n', encoding="utf-8")
        self.assertEqual(full_history_check(path), "not_yet")
        self.assertEqual(gains_basis_label({"margin": path}, checks=self.CHECKS),
                         "pre-wash, full history")
        # The file's build record keeps the field (pipeline/freshness
        # `_write_record` keeps what is not its own).
        rec = json.loads(record_path(path).read_text(encoding="utf-8"))
        self.assertEqual(rec["loss_check"]["check"], "pass")

    def test_the_commands_own_build_before_the_record(self):
        # The second verify's N1: the record could not be written, an
        # earlier build's stayed and its fingerprint matched the same bytes.
        # The command that built the file gives its own word (`built`, by
        # resolved path); the record speaks only for a file it did not build.
        from taxjson.lib.report_model import forget_full_history_check, gains_basis_label
        path = self._file("margin", "gains_all", self.BUILDS)
        files = {"margin": path}
        self.assertEqual(gains_basis_label(files, checks=self.CHECKS,
                                           built={str(path.resolve()): "not_yet"}),
                         "pre-wash, full history")
        self.assertEqual(gains_basis_label(files, checks=self.CHECKS, built={}),
                         "wash-adjusted, full history")
        forget_full_history_check(path)
        self.assertFalse(record_path(path).exists())
        self.assertEqual(gains_basis_label(files, checks=self.CHECKS), "pre-wash, full history")
        forget_full_history_check(path)

    def test_a_record_that_vouches_for_nothing(self):
        # A record of no account (every one configured since the run).
        self.assertEqual(self._label({}, margin="gains_wash"), "pre-wash")
        self.assertEqual(self._label({}, coins="gains_all"), "wash-adjusted, full history")

    def test_without_the_runs_record_the_tax_year_file_names(self):
        # `checks` None: the filed-year lock (filed_basis_label) and
        # taxjson-harvest alone. A tax-year file by its name, as before the
        # record; a full-history file still by its own build (the verify's
        # V1: its name made any set "wash-adjusted, full history").
        from taxjson.lib.report_model import filed_basis_label, gains_basis_label
        self.assertEqual(gains_basis_label([Path("rrsp_gains.json")], checks=None), "pre-wash")
        self.assertEqual(filed_basis_label([Path("margin_gains_wash.json")]), "wash-adjusted")
        self.assertEqual(filed_basis_label({"margin": Path("margin_gains_wash.json"),
                                            "m2": Path("m2_gains.json")}), "wash-adjusted")
        self.assertEqual(gains_basis_label([self.work / "rrsp_gains_all.json"], checks=None),
                         "pre-wash, full history")
        self.assertEqual(gains_basis_label([self._file("margin", "gains_all", self.BUILDS)],
                                           checks=None), "wash-adjusted, full history")
        self.assertEqual(gains_basis_label([self._file("margin", "gains_all", self.BUILDS),
                                            Path("m2_gains.json")], checks=None),
                         "pre-wash, full history")

    def test_every_caller_says_which_record(self):
        # A label without it reads the file names: "wash-adjusted" over a
        # set the pass never saw. Each call states the record (None: the
        # file names, said why beside it).
        import ast
        src = SRC / "taxjson"
        calls = []
        for path in sorted(src.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "gains_basis_label"):
                    calls.append((path.relative_to(src).as_posix(), node.lineno,
                                  any(k.arg == "checks" for k in node.keywords)))
        self.assertGreaterEqual(len(calls), 4)
        self.assertEqual([c for c in calls if not c[2]], [])


def _kinds(country="canada", **types):
    """lib/report_model `account_kinds` of accounts typed "taxable",
    "registered" or "crypto" (taxable crypto)."""
    from types import SimpleNamespace

    from taxjson.lib.report_model import account_kinds
    return account_kinds({n: SimpleNamespace(taxable=t != "registered", crypto=t == "crypto")
                          for n, t in types.items()}, country)


KINDS = dict(m1="taxable", coins="crypto", rrsp="registered")


class TestTheRunsRecord(unittest.TestCase):
    """lib/report_model `record_loss_checks`: what checked each account's
    gains, as the run decided it, kept in work/.deps/run.json; `loss_checks`
    reads it back with the accounts configured now (pipeline/run
    `_record_loss_checks` decides; pipeline/sanity `loss_check_kinds` reads
    taxjson.toml; the CLI's cases are in
    tests/features/summaries/test_full_history_sees_registered_purchases.py)."""

    def setUp(self):
        self.root = Path(private_dir()) / "rec"
        (self.root / "work").mkdir(parents=True)
        (self.root / "taxjson.toml").write_text("[settings]\nyear = 2025\n", encoding="utf-8")
        for name in ("m1_gains_wash.json", "m1_gains.json", "coins_gains.json",
                     "rrsp_gains.json"):
            (self.root / "work" / name).write_text("{}\n", encoding="utf-8")

    def _record(self, decided, *, full, kinds=KINDS):
        from taxjson.lib.report_model import record_loss_checks
        record_loss_checks(self.root, decided, kinds=_kinds(**kinds), full=full,
                           no_inputs_in=["inputs/rrsp"])

    def _checks(self, kinds=KINDS, country="canada"):
        from taxjson.lib.report_model import loss_checks
        return loss_checks(self.root, _kinds(country, **kinds))

    def _edit_toml(self):
        with (self.root / "taxjson.toml").open("a", encoding="utf-8") as f:
            f.write("# edited\n")

    def _full_run(self):
        self._record({"m1": "pass", "coins": "own", "rrsp": "none"}, full=True)

    def _tax_year(self):
        return {a: (e["tax_year"]["file"], e["tax_year"]["check"])
                for a, e in (self._checks() or {}).items()}

    def test_no_record(self):
        # The verify's V1: books of an older taxjson, a first run that
        # failed, work/.deps/run.json damaged or deleted. The file served
        # says what checked it: a wash-adjusted file is a pass's; no rule
        # for an account none reaches; a plain file, nothing yet (a lone
        # Canadian crypto account's too, until a run records its own check).
        named = {"m1": ("m1_gains_wash.json", "pass"), "rrsp": ("rrsp_gains.json", "none")}
        self.assertEqual(self._tax_year(), named)
        self.assertEqual(self._tax_year_with(country="usa"),
                         {"m1": "pass", "rrsp": "none", "coins": "none"})
        (self.root / "work/.deps").mkdir()
        for damaged in ("", '{"format": 2, "inst', "[]", '{"format": 2, "loss_checks": 5}'):
            (self.root / "work/.deps/run.json").write_text(damaged, encoding="utf-8")
            self.assertEqual(self._tax_year(), named)

    def test_a_partial_run_with_no_earlier_record(self):
        # The verify's V2: an older taxjson's full run, then `tjs run
        # --account coins`. The record began with the built account alone,
        # and m1's wash-adjusted file, "wash-adjusted" a moment before,
        # read "pre-wash".
        self._record({"coins": None}, full=False)
        self.assertEqual(self._tax_year(), {"m1": ("m1_gains_wash.json", "pass"),
                                            "coins": ("coins_gains.json", "not_yet"),
                                            "rrsp": ("rrsp_gains.json", "none")})
        # The account built alone, its earlier pass's file still served.
        (self.root / "work/.deps/run.json").unlink()
        self._record({"m1": None}, full=False)
        self.assertEqual(self._tax_year()["m1"], ("m1_gains_wash.json", "pass"))
        # A full run that defers m1 (elections) keeps it too.
        (self.root / "work/.deps/run.json").unlink()
        self._record({"coins": "own", "rrsp": "none"}, full=True)
        self.assertEqual(self._tax_year(), {"m1": ("m1_gains_wash.json", "pass"),
                                            "coins": ("coins_gains.json", "own"),
                                            "rrsp": ("rrsp_gains.json", "none")})

    def test_a_full_run(self):
        self._full_run()
        self.assertEqual(self._tax_year(), {"m1": ("m1_gains_wash.json", "pass"),
                                            "coins": ("coins_gains.json", "own"),
                                            "rrsp": ("rrsp_gains.json", "none")})
        # A full-history file's check is its own build's, in its own record.
        self.assertEqual(sorted(self._checks()["coins"]), ["no_inputs_in", "tax_year"])
        doc = json.loads((self.root / "work/.deps/run.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["format"], 2)

    def test_a_partial_run(self):
        self._full_run()
        # `tjs run --account m1`: the earlier pass's file still served (stale,
        # warned), its check kept; the others kept.
        self._record({"m1": None}, full=False)
        self.assertEqual(self._tax_year()["m1"], ("m1_gains_wash.json", "pass"))
        self.assertEqual(len(self._tax_year()), 3)
        # With no file of an earlier pass: not yet.
        (self.root / "work/m1_gains_wash.json").unlink()
        self._record({"m1": None}, full=False)
        self.assertEqual(self._tax_year()["m1"], ("m1_gains.json", "not_yet"))

    def _tax_year_with(self, kinds=KINDS, country="canada"):
        return {a: e["tax_year"]["check"]
                for a, e in (self._checks(kinds, country) or {}).items()}

    def test_a_toml_edit_that_touches_no_account(self):
        # taxjson.toml changed since the run (another hash), its accounts
        # not: every check kept (TJ-0452: a denial the pass made is shown
        # as one).
        self._full_run()
        self._edit_toml()
        self.assertEqual(self._tax_year_with(), {"m1": "pass", "coins": "own", "rrsp": "none"})

    def test_a_registered_account_added(self):
        # The own check held for the registered accounts the run saw: gone
        # (not yet); the pass's and no rule's kept; the new one none yet.
        self._full_run()
        self._edit_toml()
        added = dict(KINDS, tfsa="registered")
        self.assertEqual(self._tax_year_with(added), {"m1": "pass", "rrsp": "none"})
        # A partial run records its own account, the others kept as they
        # stand; the record is then this taxjson.toml's.
        self._record({"coins": "own"}, full=False, kinds=added)
        self.assertEqual(self._tax_year_with(added),
                         {"m1": "pass", "coins": "own", "rrsp": "none"})
        doc = json.loads((self.root / "work/.deps/run.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc["loss_checks"]["kinds"]), ["coins", "m1", "rrsp", "tfsa"])

    def test_an_account_removed(self):
        # Its books are read until a run sweeps them: a pass's check is
        # still true of its file (the stale-file rule); nothing else is.
        self._full_run()
        self._edit_toml()
        self.assertEqual(self._tax_year_with(dict(coins="crypto", rrsp="registered")),
                         {"m1": "pass", "coins": "own", "rrsp": "none"})
        # A registered one: its no rule gone, and the own check held for it.
        self.assertEqual(self._tax_year_with(dict(m1="taxable", coins="crypto")),
                         {"m1": "pass"})
        # A full run records only the accounts configured.
        self._record({"coins": "own", "rrsp": "none"}, full=True,
                     kinds=dict(coins="crypto", rrsp="registered"))
        self.assertEqual(self._tax_year_with(dict(coins="crypto", rrsp="registered")),
                         {"coins": "own", "rrsp": "none"})

    def test_a_type_change(self):
        # The file served was built under the old type. A pass's check is
        # still true of it; no rule is not (a registered account made
        # taxable: nothing checked its sales), nor an own check (another
        # registered account, another crypto roster).
        self._full_run()
        self._edit_toml()
        self.assertEqual(self._tax_year_with(dict(KINDS, rrsp="taxable")), {"m1": "pass"})
        self.assertEqual(self._tax_year_with(dict(KINDS, m1="registered")),
                         {"m1": "pass", "rrsp": "none"})
        self.assertEqual(self._tax_year_with(dict(KINDS, m1="crypto")),
                         {"m1": "pass", "rrsp": "none"})
        self.assertEqual(self._tax_year_with(country="usa"), {"m1": "pass"})
        # The same taxjson.toml: the record as written.
        (self.root / "taxjson.toml").write_text("[settings]\nyear = 2025\n", encoding="utf-8")
        self.assertEqual(self._tax_year_with(dict(KINDS, rrsp="taxable")),
                         {"m1": "pass", "coins": "own", "rrsp": "none"})

    def test_an_input_in_a_registered_account_undoes_an_own_check(self):
        self._full_run()
        (self.root / "inputs/rrsp").mkdir(parents=True)
        (self.root / "inputs/rrsp/.hidden.csv").write_text("", encoding="utf-8")
        self.assertIn("coins", self._checks())
        (self.root / "inputs/rrsp/TRADES.TT").write_text("", encoding="utf-8")
        self.assertEqual(sorted(self._checks()), ["m1", "rrsp"])

    def test_a_check_names_the_file_it_checked(self):
        # An own check, then a wash file served that no record names: not
        # that file's.
        self._full_run()
        (self.root / "work/coins_gains_wash.json").write_text("{}\n", encoding="utf-8")
        self.assertNotIn("coins", {a for a, e in self._checks().items()
                                   if e["tax_year"]["file"] == "coins_gains_wash.json"})
        self._full_run()
        self.assertEqual(self._tax_year()["coins"], ("coins_gains_wash.json", "not_yet"))


if __name__ == "__main__":
    unittest.main()
