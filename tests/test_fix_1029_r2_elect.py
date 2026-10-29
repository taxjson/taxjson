"""Backlog fix round 2 of 2026-10-29, group `elect`: `tjs elect` (the
corporate-event choices saved in inputs/<account>/manifest.json) and the
option and trading views.

TJ-0539  elect --pending lists only the account asked for and refuses a
         --set/--hint/--redo/--reset/--event beside it; a flag elect
         refuses exits 2 (a usage error), not 1.
TJ-0540  elect --set never saves an election the project's country does
         not know, though a stale pending file offers it.
TJ-0544  two elect commands at once both keep their election: the
         manifest's load-change-save is one step under a lock.
TJ-0556  elect --redo clears nothing it cannot ask again about (no
         corporate-action export, an event no export holds).
TJ-0541  leaps / leaps-sum --json for an empty window keeps the
         taxable_gain and sheltered_gain keys.
TJ-0543  option-yield never divides an option's profit by a share cost
         in another currency: no yield there (CA-PLAN-07).
TJ-0545  stats / win-rates leave a sale with any lot of unknown cost out
         whole: never a trade of its known lots.
TJ-0546  stats / win-rates take a Canadian assigned put's premium out of
         the pooled shares' sales in every account of the pool.
TJ-0563  edge-cases: of the contracts held into their expiry day, those a
         priced trade closed that day are not counted as expiring.
TJ-0564  edge-cases: a split or renamed option series is followed to its
         successor, the retired series holding nothing.
TJ-0565  stats / win-rates / option-yield read a sale the broker codes
         CLOSING (IB code C) with nothing held as the close of a contract
         opened before the data, never a write.
TJ-0553  `tjs --di DIR events sum` (an abbreviation argparse takes for
         --dir) reads account sum of that project, as -C does.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*, SYN*), account names (margin, other), amounts under
1,000.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]

EV = "20251022-zza-zzb-51d7"
_PENDING = {"accounts": {"margin": {"pending": [
    {"event_id": EV,
     "summary": "2025-10-22 merger: ZZA.TO → ZZB.US (1-for-16)",
     "qty_disposed": 160.0, "qty_received": 10.0,
     "options": [
         {"election": "taxable_disposition", "description": "sale now",
          "hints": []},
         {"election": "rollover_s_85_1_5", "description": "defer",
          "hints": [{"key": "fmv_per_share", "prompt": "FMV/share"}]}]}
]}}}


def _env():
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _tjs(root, *args):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    argv = python_module_argv("taxjson.bin.taxjson_run")
    return subprocess.run(argv + ["-C", str(root), *args],
                          capture_output=True, text=True, env=_env(),
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(d, accounts=("margin",), country="canada", pending=None):
    """A project with taxable accounts and work/pending_elections.json."""
    root = Path(d) / "proj"
    (root / "work").mkdir(parents=True)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{base}"\nsource_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    if pending is not None:
        (root / "work" / "pending_elections.json").write_text(
            json.dumps(pending))
    return root


def _two_accounts_pending():
    doc = copy.deepcopy(_PENDING)
    other = copy.deepcopy(doc["accounts"]["margin"])
    other["pending"][0]["event_id"] = "20251022-zza-zzb-0001"
    doc["accounts"]["other"] = other
    return doc


class TestElectPendingScopeAndFlags(unittest.TestCase):
    """TJ-0539: `elect margin --pending` listed every account's pending
    events, and `elect margin --pending --set EV=ignore` exited 0 having
    saved nothing (the --pending branch returned before the account and
    the other flags were read)."""

    def test_pending_lists_only_the_account_asked_for(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, ("margin", "other"),
                            pending=_two_accounts_pending())
            r = _tjs(root, "elect", "margin", "--pending", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(json.loads(r.stdout)["accounts"]),
                             ["margin"])
            t = _tjs(root, "elect", "other", "--pending")
            self.assertEqual(t.returncode, 0, t.stderr)
            self.assertIn("other: 20251022-zza-zzb-0001", t.stdout)
            self.assertNotIn(EV, t.stdout)
            # Every account's without one.
            a = _tjs(root, "elect", "--pending", "--json")
            self.assertEqual(sorted(json.loads(a.stdout)["accounts"]),
                             ["margin", "other"])

    def test_pending_for_an_account_with_none(self):
        with tempfile.TemporaryDirectory() as d:
            pend = copy.deepcopy(_PENDING)
            root = _project(d, ("margin", "other"), pending=pend)
            r = _tjs(root, "elect", "other", "--pending")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("No pending elections for other", r.stdout)
            j = _tjs(root, "elect", "other", "--pending", "--json")
            self.assertEqual(json.loads(j.stdout)["accounts"], {})

    def test_pending_refuses_an_unknown_account(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, pending=copy.deepcopy(_PENDING))
            r = _tjs(root, "elect", "nosuch", "--pending")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("no [accounts.nosuch]", r.stderr)

    def test_pending_refuses_a_set_and_saves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, pending=copy.deepcopy(_PENDING))
            for extra in (["--set", f"{EV}=ignore"],
                          ["--set", f"{EV}=ignore", "--hint", "a=1"],
                          ["--redo"], ["--reset"], ["--event", EV]):
                r = _tjs(root, "elect", "margin", "--pending", *extra)
                self.assertEqual(r.returncode, 2, (extra, r.stderr))
                self.assertIn("--pending lists the pending elections",
                              r.stderr)
                self.assertIn(extra[0], r.stderr)
            self.assertFalse(
                (root / "inputs" / "margin" / "manifest.json").exists())

    def test_event_without_redo_or_reset_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, pending=copy.deepcopy(_PENDING))
            r = _tjs(root, "elect", "margin", "--event", EV)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("--event applies to --redo/--reset only",
                          r.stderr)

    def test_flag_errors_exit_2(self):
        """Round 1 (TJ-0539's code path): elect's flag errors exited 1,
        the code of a finding; a usage error is 2."""
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, pending=copy.deepcopy(_PENDING))
            cases = [
                (("elect", "--redo"), "--redo/--reset need an account"),
                (("elect", "--set", f"{EV}=ignore"),
                 "--set/--hint need an account"),
                (("elect", "nosuch"), "no [accounts.nosuch]"),
                (("elect", "margin", "--hint", "a=1"),
                 "--hint only applies with --set"),
                (("elect", "margin", "--set", "nonsense"),
                 "--set expects EVENT_ID=ELECTION"),
                (("elect", "margin", "--set", f"{EV}=bogus"),
                 "is not valid for event"),
                (("elect", "margin", "--set",
                  f"{EV}=rollover_s_85_1_5"), "needs fmv_per_share"),
                (("elect", "margin", "--set", "zz-none=ignore"),
                 "matches no pending event"),
                (("elect", "margin", "--reset", "--event", "zz-none"),
                 "no election 'zz-none'"),
            ]
            for argv, text in cases:
                r = _tjs(root, *argv)
                self.assertEqual(r.returncode, 2, (argv, r.stderr))
                self.assertIn(text, r.stderr, argv)
            self.assertFalse(
                (root / "inputs" / "margin" / "manifest.json").exists())


def _us_pending():
    doc = copy.deepcopy(_PENDING)
    doc["accounts"]["margin"]["country"] = "usa"
    doc["accounts"]["margin"]["pending"][0]["options"] = [
        {"election": "taxable_exchange", "description": "taxable",
         "hints": []},
        {"election": "reorg_368", "description": "tax-free", "hints": []}]
    return doc


class TestElectNeverSavesAnotherCountrysElection(unittest.TestCase):
    """TJ-0540: a pending Canadian merger kept in
    work/pending_elections.json after the project's country became usa:
    `elect --set EV=rollover_s_85_1_5` took the stale entry's options as
    the event's, saved the Canadian rollover at exit 0, and the next
    `elect --pending` called it a wrong-country election."""

    def test_stale_canadian_option_is_refused_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, country="usa", pending=copy.deepcopy(_PENDING))
            r = _tjs(root, "elect", "margin", "--set",
                     f"{EV}=rollover_s_85_1_5", "--hint", "fmv_per_share=10")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("with another country's elections", r.stderr)
            self.assertIn("taxjson run --no-input", r.stderr)
            self.assertIn("Nothing was saved", r.stderr)
            self.assertFalse(
                (root / "inputs" / "margin" / "manifest.json").exists())
            # The listing says the file is stale.
            p = _tjs(root, "elect", "--pending")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("warning: work/pending_elections.json lists "
                          "margin's events with another country's "
                          "elections", p.stderr)
            self.assertNotIn("NOT UNITED STATES ELECTIONS", p.stdout)

    def test_an_entry_offering_a_foreign_key_is_set_aside(self):
        """One foreign option makes the entry stale: none of its
        options is taken, this country's included."""
        with tempfile.TemporaryDirectory() as d:
            doc = _us_pending()
            doc["accounts"]["margin"]["pending"][0]["options"].append(
                {"election": "rollover_s_85_1_5", "description": "defer",
                 "hints": []})
            root = _project(d, country="usa", pending=doc)
            r = _tjs(root, "elect", "margin", "--set", f"{EV}=reorg_368")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("with another country's elections", r.stderr)

    def test_this_countrys_entry_still_saves(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, country="usa", pending=_us_pending())
            r = _tjs(root, "elect", "margin", "--set", f"{EV}=reorg_368")
            self.assertEqual(r.returncode, 0, r.stderr)
            man = json.loads((root / "inputs" / "margin" /
                              "manifest.json").read_text())
            self.assertEqual(man["elections"][EV]["election"], "reorg_368")
            # A Canadian key for a current US entry: not this event's.
            r = _tjs(root, "elect", "margin", "--set",
                     f"{EV}=rollover_s_85_1_5", "--hint", "fmv_per_share=1")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("election 'rollover_s_85_1_5' is not valid "
                          "for event", r.stderr)


# A child that saves one election with `tjs elect --set`, holding each
# manifest save until the other child is about to save too (or 2 s
# pass): unlocked, both have loaded the original file by then and the
# later save drops the other's election; locked, the second waits
# before it loads, and the first goes on after the 2 s.
_SAVE_BARRIER_CHILD = '''\
import sys, time
from pathlib import Path
from taxjson.lib import corp_actions as ca
from taxjson.bin import taxjson_run as R

root, eid = Path(sys.argv[1]), sys.argv[2]
gate = root / "gate"
_save = ca.Manifest.save


def save(self, path):
    (gate / (eid + ".ready")).touch()
    limit = time.monotonic() + 2.0
    while len(list(gate.glob("*.ready"))) < 2 and time.monotonic() < limit:
        time.sleep(0.01)
    return _save(self, path)


ca.Manifest.save = save
sys.argv = ["tjs", "-C", str(root), "elect", "margin", "--set",
            eid + "=ignore"]
R.main()
'''


class TestConcurrentElectionsBothKept(unittest.TestCase):
    """TJ-0544: two `elect --set`s at once each loaded manifest.json,
    added their own election and replaced the file: both said
    "Election saved", and the file held one. The load, the change and
    the save are one step under the manifest's lock
    (corp_actions.Manifest.update)."""

    def test_two_processes_both_elections_saved(self):
        with tempfile.TemporaryDirectory() as d:
            pend = copy.deepcopy(_PENDING)
            a = pend["accounts"]["margin"]["pending"][0]
            a["event_id"] = "event-a"
            b = copy.deepcopy(a)
            b["event_id"] = "event-b"
            pend["accounts"]["margin"]["pending"].append(b)
            root = _project(d, pending=pend)
            (root / "gate").mkdir()
            child = Path(d) / "child" / "save_child.py"
            child.parent.mkdir()
            child.write_text(_SAVE_BARRIER_CHILD)
            procs = [subprocess.Popen(
                [sys.executable, str(child), str(root), eid],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=_env(), stdin=subprocess.DEVNULL)
                for eid in ("event-a", "event-b")]
            for p in procs:
                out, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, err)
                self.assertIn("Election saved", out)
            man = json.loads((root / "inputs" / "margin" /
                              "manifest.json").read_text())
            self.assertEqual(sorted(man["elections"]),
                             ["event-a", "event-b"])
            # The lock is a hidden file beside the manifest, which no
            # inputs scan reads.
            self.assertTrue((root / "inputs" / "margin" /
                             ".manifest.json.lock").exists())

    def test_update_holds_the_lock_across_load_and_save(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest import mock
        from taxjson.lib import corp_actions as ca
        first_in, second_in = threading.Event(), threading.Event()
        original = ca.Manifest.load.__func__
        calls = []

        def load(cls, path):
            man = original(cls, path)
            calls.append(1)
            if len(calls) == 1:
                first_in.set()
                # Unlocked, the second update loads while this one
                # waits; locked, it cannot, and this times out.
                second_in.wait(timeout=0.5)
            else:
                second_in.set()
            return man

        def elect(path, eid):
            return ca.Manifest.update(path, lambda m: m.set(
                ca.ElectionRecord(event_id=eid, summary="s",
                                  election="ignore")) or True)

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "inputs" / "margin" / "manifest.json"
            with mock.patch.object(ca.Manifest, "load", classmethod(load)):
                with ThreadPoolExecutor(2) as pool:
                    list(pool.map(lambda e: elect(path, e),
                                  ["event-a", "event-b"]))
            self.assertEqual(sorted(ca.Manifest.load(path).records),
                             ["event-a", "event-b"])
            self.assertEqual(ca.manifest_lock_path(path).name,
                             ".manifest.json.lock")

    def test_redo_interrupted_keeps_an_election_saved_meanwhile(self):
        """--redo clears under the lock and, interrupted, puts back the
        cleared elections only: one another command saved meanwhile
        stays (it restored the whole file as it was before)."""
        import argparse
        import contextlib
        import io
        from unittest import mock
        import taxjson.bin.taxjson_run as R
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            root = _project(d)
            (root / "inputs" / "margin" / "zz_ib.csv").write_text("x\n")
            path = root / "inputs" / "margin" / "manifest.json"
            m = ca.Manifest()
            m.set(ca.ElectionRecord(event_id="event-a", summary="A",
                                    election="taxable_disposition"))
            m.save(path)

            def interrupted(cmd, out, **kw):
                # Another command's election, saved during the prompt.
                ca.Manifest.update(path, lambda mm: mm.set(
                    ca.ElectionRecord(event_id="event-b", summary="B",
                                      election="ignore")) or True)
                raise KeyboardInterrupt

            args = argparse.Namespace(
                dir=str(root), account="margin", redo=True, reset=False,
                event="event-a", set=None, hint=None, pending=False,
                json=False)
            with mock.patch.object(R, "run_to_file", interrupted), \
                    mock.patch.object(R, "group_inputs",
                                      lambda _d: {"ib": [_d / "zz_ib.csv"]}), \
                    mock.patch.object(R, "_extracted_event_ids",
                                      lambda *a, **k: {"event-a"}), \
                    mock.patch.object(R.sys.stdin, "isatty", lambda: True), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                R.cmd_elect(args)
            # Exit 1 (SystemExit carries the message).
            self.assertIn("--redo interrupted", str(cm.exception.code))
            recs = ca.Manifest.load(path).records
            self.assertEqual(recs["event-a"].election, "taxable_disposition")
            self.assertEqual(recs["event-b"].election, "ignore")


def _redo(root, event=None, run_to_file=None):
    """`elect margin --redo [--event ID]` in-process on a 'terminal'
    (stdin.isatty patched); the corp-actions child is `run_to_file`
    (default: records each command). Returns (stdout, stderr, calls)."""
    import argparse
    import contextlib
    import io
    from unittest import mock
    import taxjson.bin.taxjson_run as R
    calls = []
    args = argparse.Namespace(
        dir=str(root), account="margin", redo=True, reset=False,
        event=event, set=None, hint=None, pending=False, json=False)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(R, "run_to_file", run_to_file or (
            lambda cmd, o, **kw: calls.append(cmd))), \
            mock.patch.object(R.sys.stdin, "isatty", lambda: True), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        R.cmd_elect(args)
    return out.getvalue(), err.getvalue(), calls


def _save_elections(path, **elections):
    from taxjson.lib import corp_actions as ca
    m = ca.Manifest()
    for eid, election in elections.items():
        m.set(ca.ElectionRecord(event_id=eid, summary=f"synthetic {eid}",
                                election=election))
    m.save(path)


class TestRedoNeverClearsWhatItCannotAskAgain(unittest.TestCase):
    """TJ-0556: `elect margin --redo --event old-event` in an account
    whose exports hold no corporate action (only .tt trades) cleared
    the saved election, then printed "No corp-action events to
    re-elect" and exited 0: the choice was gone and nothing asked."""

    def test_no_corporate_action_export_refused_before_clearing(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 10 10 0\n")
            path = root / "inputs" / "margin" / "manifest.json"
            _save_elections(path, **{"old-event": "ignore"})
            before = path.read_bytes()
            for event in ("old-event", None):
                with self.assertRaises(SystemExit) as cm:
                    _redo(root, event=event)
                self.assertEqual(cm.exception.code, 2)
                self.assertEqual(path.read_bytes(), before)

    def test_an_event_no_export_holds_is_refused_or_kept(self):
        from test_fix_corp import _ib_spinoff_project
        from taxjson.lib import corp_actions as ca
        import taxjson.bin.taxjson_run as R
        with tempfile.TemporaryDirectory() as d:
            root = _ib_spinoff_project(d)
            (root / "ticker.map").write_text("")
            acct = root / "inputs" / "margin"
            live = sorted(R._extracted_event_ids(acct, "margin", root))
            self.assertEqual(len(live), 1, live)
            path = acct / "manifest.json"
            _save_elections(path, **{live[0]: "taxable_deemed_dividend",
                                     "old-event": "ignore"})
            before = path.read_bytes()
            # One event no export has: refused, nothing cleared.
            with self.assertRaises(SystemExit) as cm:
                _redo(root, event="old-event")
            self.assertEqual(cm.exception.code, 2)
            self.assertEqual(path.read_bytes(), before)
            # All: the live event is asked again, old-event kept.
            out, err, calls = _redo(root)
            self.assertEqual(len(calls), 1)
            self.assertIn("Cleared 1 election(s)", out)
            self.assertIn("kept 1 election(s) of events in none of "
                          "margin's exports: old-event", err)
            recs = ca.Manifest.load(path).records
            self.assertEqual(sorted(recs), ["old-event"])


class TestLeapsEmptyJsonKeepsItsKeys(unittest.TestCase):
    """TJ-0541: `leaps --json` / `leaps-sum --json` for a window with no
    close left out `taxable_gain` and `sheltered_gain`, which a window
    with closes has."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _project(cls._tmp.name)
        (cls.root / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2024-01-05 10:00:00 ZZQ260116C00010000.TO 1 CAD 1 100 0\n"
            "BUYSELL 2025-06-02 10:00:00 ZZQ260116C00010000.TO -1 CAD 2 200 0\n")
        r = _tjs(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_empty_window_has_the_populated_keys(self):
        for cmd in ("leaps", "leaps-sum"):
            full = _tjs(self.root, cmd, "2025", "--json")
            empty = _tjs(self.root, cmd, "2026", "--json")
            self.assertEqual(full.returncode, 0, full.stderr)
            self.assertEqual(empty.returncode, 0, empty.stderr)
            full, empty = json.loads(full.stdout), json.loads(empty.stdout)
            self.assertTrue(full["rows"], cmd)
            self.assertEqual(empty["rows"], [], cmd)
            self.assertEqual(sorted(empty), sorted(full), cmd)
            self.assertEqual((empty["taxable_gain"], empty["sheltered_gain"]),
                             (0.0, 0.0), cmd)

    def test_no_leaps_contract_has_them_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 10 10 0\n")
            self.assertEqual(_tjs(root, "run", "--no-input").returncode, 0)
            for cmd in ("leaps", "leaps-sum"):
                doc = json.loads(_tjs(root, cmd, "--json").stdout)
                self.assertEqual(doc["rows"], [])
                self.assertEqual((doc["taxable_gain"],
                                  doc["sheltered_gain"]), (0.0, 0.0))


class TestOptionYieldNeverMixesCurrencies(unittest.TestCase):
    """TJ-0543: 100 ZZQ.US shares bought for CAD 1350 and a USD call
    written on them for USD 100: option-yield divided the USD profit by
    the CAD cost and called the capital USD (yield 7.4%). The shares'
    cost is never converted here, so that yield is not given."""

    CALL = "ZZQ250321C00010000.US"

    def _rows(self):
        def r(d, qty, net, cur="USD"):
            return ("margin", {
                "action": "BUYSELL", "date": d, "time": "10:00:00",
                "symbol": self.CALL, "quantity": float(qty),
                "price": abs(net) / 100.0, "net_amount": float(net),
                "currency": cur, "multiplier": 100.0, "account": "margin"})
        return [r("2025-02-03", -1, 100.0), r("2025-03-21", 1, 0.0)]

    @rule("CA-PLAN-07")
    def test_other_currency_share_cost_gives_no_yield(self):
        from taxjson.lib import option_yield as OY
        doc = OY.analyze(OY.contracts(self._rows()), start="2025-01-01",
                         end="2025-12-31", scope="tax year 2025",
                         shares={("margin", "ZZQ.US", "CAD"): (100.0, 900.0)})
        row = doc["rows"][0]
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["realized"], 100.0)
        self.assertEqual((row["shares_held"], row["shares_cost"]), (0.0, 0.0))
        self.assertEqual(row["shares_other_currency"],
                         {"CAD": {"shares": 100.0, "cost": 900.0}})
        self.assertEqual(row["yield_basis"], OY.OTHER_CURRENCY)
        self.assertIsNone(row["yield"])
        self.assertIsNone(row["annualized"])
        self.assertIsNone(doc["totals"]["USD"]["yield"])
        text = "\n".join(OY.render(doc).lines())
        self.assertIn("Yield, USD", text)
        self.assertIn("none: 100 share(s) held cost 900.00 CAD, not USD",
                      text)
        # Shares in the options' currency are the capital, as before.
        doc = OY.analyze(OY.contracts(self._rows()), start="2025-01-01",
                         end="2025-12-31", scope="tax year 2025",
                         shares={("margin", "ZZQ.US", "USD"): (100.0, 800.0)})
        row = doc["rows"][0]
        self.assertEqual((row["yield_basis"], row["capital"], row["yield"]),
                         ("shares", 800.0, 0.125))
        self.assertEqual(row["shares_other_currency"], {})

    @rule("CA-PLAN-07")
    def test_cli_reads_the_holdings_currency(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d)
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]'))
            (root / "inputs" / "margin" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.US 100 CAD 9 900 0\n"
                f"BUYSELL 2025-02-03 10:00:00 {self.CALL} -1 USD 1 100 0\n"
                f"BUYSELL 2025-03-21 10:00:00 {self.CALL} 1 USD 0 0 0\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            y = _tjs(root, "option-yield", "2025", "--json")
            self.assertEqual(y.returncode, 0, y.stderr)
            row = json.loads(y.stdout)["rows"][0]
            self.assertEqual(row["currency"], "USD")
            self.assertEqual(row["shares_other_currency"],
                             {"CAD": {"shares": 100.0, "cost": 900.0}})
            self.assertIsNone(row["yield"])


def _stats(root, *args):
    r = _tjs(root, "stats", "--json", *args)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestStatsSkipAPartlyUnknownSale(unittest.TestCase):
    """TJ-0545: a US sale of 2 shares, one bought at 10 and one held
    before the data (cost unknown): the gains rows book the known lot
    (a loss of 2) and route the other to manual reporting under the same
    id. The views counted the sale as unknown-cost AND as a losing
    trade; the unknown lot could make it a win."""

    def _docs(self):
        known = dict(id="zz01", date="2025-03-03", symbol="ZZQ.US",
                     account="margin", qty=1, proceeds=8, cost=10, gain=-2,
                     raw_gain=-2, direction="LONG")
        manual = dict(id="zz01", date="2025-03-03", symbol="ZZQ.US",
                      account="margin", qty=1, proceeds=8)
        return {"margin": {"transactions": [known],
                           "manual_reporting_required": [manual]}}

    def _closed(self, docs):
        from taxjson.lib import trade_stats as TS
        return TS.closed_trades(docs, {"margin": []}, lambda d: True,
                                lambda t: t["date"])

    @rule("US-RPT-12")
    def test_the_whole_disposition_is_left_out(self):
        r = self._closed(self._docs())
        self.assertEqual((r["trades"], r["tainted_skipped"]), ([], 1))
        # The known row flagged tainted itself: the same.
        docs = self._docs()
        docs["margin"]["transactions"][0]["tainted"] = True
        docs["margin"]["manual_reporting_required"] = []
        r = self._closed(docs)
        self.assertEqual((r["trades"], r["tainted_skipped"]), ([], 1))
        # Another sale of the same day keeps its trade.
        docs = self._docs()
        docs["margin"]["transactions"].append(dict(
            id="zz02", date="2025-03-03", symbol="ZZQ.US", account="margin",
            qty=1, proceeds=12, cost=10, gain=2, raw_gain=2,
            direction="LONG"))
        r = self._closed(docs)
        self.assertEqual([t["pnl"] for t in r["trades"]], [2.0])
        self.assertEqual(r["tainted_skipped"], 1)

    @rule("US-RPT-12")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, country="usa")
            (root / "inputs" / "margin" / "trades.tt").write_text(
                'OPENING 2024-12-31 ZZQ.US 1 cost=unknown reason="synthetic"\n'
                "BUYSELL 2025-01-06 10:00:00 ZZQ.US 1 USD 10 10 0\n"
                "BUYSELL 2025-03-03 10:00:00 ZZQ.US -2 USD 8 16 0\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = _stats(root)
            self.assertEqual((doc["total"]["trades"],
                              doc["tainted_skipped"]), (0, 1))
            r = _tjs(root, "win-rates", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual((doc["total"]["trades"],
                              doc["tainted_skipped"]), (0, 1))


class TestStatsPooledPutPremium(unittest.TestCase):
    """TJ-0546: `other` holds 100 ZZQ.TO bought at 1; `margin` writes a
    put for 10 and is assigned 100 more at 1; `other` sells its 100 at
    1. The Canadian pool's cost is 190 for 200 shares, so the sale's
    gain is 5: half the premium. The views counted the premium as the
    put's profit and that half again in the sale (total 15, two wins)."""

    PUT = "ZZQ250321P00001000.TO"

    @staticmethod
    def _row(acct, d, sym, q, net, action="BUYSELL", rid=None,
             t="10:00:00"):
        opt = "P0" in sym
        mult = 100 if opt else 1
        return dict(date=d, time=t, account=acct, symbol=sym,
                    action=action, quantity=q, net_amount=net,
                    price=abs(net) / abs(q) / mult if net else 0,
                    currency="CAD", multiplier=mult,
                    **({"id": rid} if rid else {}))

    def _books(self):
        r = self._row
        margin = [r("margin", "2025-02-03", self.PUT, -1, 10),
                  r("margin", "2025-03-21", self.PUT, 1, 0, "ASSIGN"),
                  r("margin", "2025-03-21", "ZZQ.TO", 100, 100, rid="dlv",
                    t="10:01:00")]
        other = [r("other", "2025-01-06", "ZZQ.TO", 100, 100, rid="buy"),
                 r("other", "2025-03-24", "ZZQ.TO", -100, 100, rid="sale")]
        return margin, other

    def _gains(self):
        sale = dict(id="sale", date="2025-03-24", symbol="ZZQ.TO",
                    account="other", qty=100, proceeds=100, cost=95,
                    gain=5, raw_gain=5, direction="LONG")
        return {"margin": {"transactions": []},
                "other": {"transactions": [sale]}}

    @staticmethod
    def _by_class(res):
        out = {}
        for t in res["trades"]:
            out[t["cls"]] = out.get(t["cls"], 0.0) + t["pnl"]
        return {k: round(v, 6) for k, v in out.items()}

    @rule("CA-RPT-16")
    def test_the_pool_s_sales_carry_their_part(self):
        from taxjson.lib import trade_stats as TS
        margin, other = self._books()
        books = {"margin": margin, "other": other}
        pool = [{"margin": margin, "other": other}]
        res = TS.closed_trades(self._gains(), books, lambda d: True,
                               lambda t: t["date"], pools=pool)
        self.assertEqual(self._by_class(res),
                         {"written-options": 10.0, "long-shares": 0.0})
        # Without the pool (a US project: FIFO per account) the other
        # account's sale keeps its P/L, as before.
        res = TS.closed_trades(self._gains(), books, lambda d: True,
                               lambda t: t["date"])
        self.assertEqual(self._by_class(res),
                         {"written-options": 10.0, "long-shares": 5.0})
        # The selling account shown alone: its sale still carries its
        # part of the premium the other account's put put in the pool.
        res = TS.closed_trades({"other": self._gains()["other"]},
                               {"other": other}, lambda d: True,
                               lambda t: t["date"], pools=pool)
        self.assertEqual(self._by_class(res), {"long-shares": 0.0})

    @rule("CA-RPT-16")
    def test_one_account_average_cost(self):
        """In one account the same: 100 shares held, 100 delivered by
        the put, 100 sold: the sale carries half the premium."""
        from taxjson.lib import trade_stats as TS
        margin, other = self._books()
        rows = ([dict(other[0], account="margin")] + margin
                + [dict(other[1], account="margin")])

        def credits(rows):
            wo = TS.written_option_trades(rows, lambda t: t["date"])
            return {k: round(v, 6) for k, v in TS.pooled_put_credits(
                {"margin": rows}, wo["credits"]).items()}
        self.assertEqual(credits(rows), {("margin", "sale"): 5.0})
        # A sale before the delivery keeps its P/L (2026-10-24 audit A49).
        early = [rows[0], dict(rows[-1], date="2025-02-04")] + rows[1:-1]
        self.assertEqual(credits(early), {})
        r = self._row
        # A 2:1 split after the delivery, then every share sold: the
        # whole premium, in the sale.
        split = margin + [
            r("margin", "2025-03-22", "ZZQ.TO", 2, 0, "SPLIT"),
            r("margin", "2025-03-24", "ZZQ.TO", -200, 100, rid="sale")]
        self.assertEqual(credits(split), {("margin", "sale"): 10.0})
        # Delivered shares covering a short of 60 carry 60% in the
        # cover; the 40 kept, the rest (2026-10-24 audit A37).
        short = ([r("margin", "2025-01-06", "ZZQ.TO", -60, 60, rid="short")]
                 + margin
                 + [r("margin", "2025-03-24", "ZZQ.TO", -40, 40,
                      rid="sale")])
        wo = TS.written_option_trades(short, lambda t: t["date"])
        got = TS.pooled_put_credits({"margin": short}, wo["credits"],
                                    {"margin": {"dlv": 60.0}})
        self.assertEqual({k: round(v, 6) for k, v in got.items()},
                         {("margin", "dlv"): 6.0, ("margin", "sale"): 4.0})

    @rule("CA-RPT-16")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, accounts=("margin", "other"))
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-02-03 10:00:00 {self.PUT} -1 CAD 0.1 10 0\n"
                f"ASSIGN 2025-03-21 10:00:00 {self.PUT} 1 CAD 0 0 0\n"
                "BUYSELL 2025-03-21 10:01:00 ZZQ.TO 100 CAD 1 100 0\n")
            (root / "inputs" / "other" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-03-24 10:00:00 ZZQ.TO -100 CAD 1 100 0\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = _stats(root)
            self.assertEqual(doc["classes"]["written-options"]["net_pl"],
                             10.0)
            self.assertEqual(doc["classes"]["long-shares"]["net_pl"], 0.0)
            self.assertEqual((doc["total"]["net_pl"], doc["total"]["wins"],
                              doc["total"]["breakeven"]), (10.0, 1, 1))
            # The account that sold, named alone: the same sale P/L.
            doc = _stats(root, "other")
            self.assertEqual(doc["classes"]["long-shares"]["net_pl"], 0.0)
            r = _tjs(root, "win-rates", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            total = json.loads(r.stdout)["total"]
            self.assertEqual((total["net_pl"], total["trades"],
                              total["wins"]), (10.0, 2, 1))


def _expiries(root):
    r = _tjs(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr
    r = _tjs(root, "edge-cases", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["year_boundary"]["option_expiries"]


class TestEdgeCasesExpiryDay(unittest.TestCase):
    """TJ-0563 / TJ-0564: the year-boundary expiry review."""

    CALL = "ZZQ260102C00010000.US"
    CALL5 = "ZZQ260102C00005000.US"

    @rule("CA-RPT-07")
    def test_a_sale_on_the_expiry_day_is_not_an_expiry(self):
        """TJ-0563: 2 calls held into their Jan 2 expiry, 1 sold for
        cash that morning, 1 expiring: 1 expires, not 2."""
        with tempfile.TemporaryDirectory() as d:
            root = _project(d)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-12-01 10:00:00 {self.CALL} 2 CAD 1 200 0\n"
                f"BUYSELL 2026-01-02 10:00:00 {self.CALL} -1 CAD 2 200 0\n"
                f"BUYSELL 2026-01-02 16:00:00 {self.CALL} -1 CAD 0 0 0\n")
            rows = _expiries(root)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row["symbol"], row["held"], row["traded"],
                              row["units"], row["lands_in"]),
                             (self.CALL, 2.0, 1.0, 1.0, 2026))
            self.assertEqual(row["outcome"], "1 sold, 1 expire")
            self.assertTrue(row["why"].startswith("long 1 expiring"),
                            row["why"])
            text = _tjs(root, "edge-cases").stdout
            self.assertIn("1 sold, 1 expire", text)

    @rule("CA-RPT-07")
    def test_a_split_series_expires_as_its_successor(self):
        """TJ-0564: 1 strike-10 call split into 2 strike-5 calls on Dec
        15, the 2 expiring Jan 2: the strike-5 series, 2 held; the
        retired strike-10 series is not listed."""
        with tempfile.TemporaryDirectory() as d:
            root = _project(d)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-12-01 10:00:00 {self.CALL} 1 CAD 1 100 0\n"
                f"SPLIT 2025-12-15 10:00:00 {self.CALL} {self.CALL5} 2\n"
                f"BUYSELL 2026-01-02 16:00:00 {self.CALL5} -2 CAD 0 0 0\n")
            rows = _expiries(root)
            self.assertEqual([(r["symbol"], r["held"], r["units"])
                              for r in rows], [(self.CALL5, 2.0, 2.0)])


class TestBrokerClosingCodeIsNoWrite(unittest.TestCase):
    """TJ-0565: IB sells 1 call coded C (closing) with no purchase in
    the data, then buys 1 coded O (opening). The views read the sale as
    a write and the buy as its buy-back: a written-option win of 100.
    The broker says the sale closed a long opened before the data (its
    cost unknown) and the buy opened a new long."""

    CALL = "ZZQ260116C00010000.TO"

    def _rows(self, first=("-1", "C", 200.0), second=("1", "O", 100.0)):
        def r(d, spec):
            q, code, net = spec
            return {"action": "BUYSELL", "date": d, "time": "10:00:00",
                    "symbol": self.CALL, "quantity": float(q),
                    "price": net / 100.0, "net_amount": net,
                    "currency": "CAD", "multiplier": 100.0,
                    "account": "margin", "open_close": code}
        return [r("2025-12-01", first), r("2025-12-15", second)]

    def test_stats(self):
        from taxjson.lib import trade_stats as TS
        wo = TS.written_option_trades(self._rows(), lambda t: t["date"])
        self.assertEqual(wo["trades"], [])
        self.assertEqual([u["date"] for u in wo["unknown"]], ["2025-12-01"])
        # Without the broker's code: a write and its buy-back, as before.
        rows = self._rows()
        for r in rows:
            r["open_close"] = ""
        wo = TS.written_option_trades(rows, lambda t: t["date"])
        self.assertEqual([(t["kind"], t["pnl"]) for t in wo["trades"]],
                         [("bought back", 100.0)])
        # The mirror: a buy coded C with no write held closes a write
        # from before the data; the later sale coded O is a new write.
        wo = TS.written_option_trades(
            self._rows(first=("1", "C", 100.0), second=("-1", "O", 200.0)),
            lambda t: t["date"])
        self.assertEqual(wo["trades"], [])
        self.assertEqual(len(wo["unknown"]), 1)

    def test_option_yield(self):
        from taxjson.lib import option_yield as OY
        lots = OY.contracts([("margin", r) for r in self._rows()])
        self.assertEqual([(lot.side, lot.unknown_cost, lot.open_date,
                           lot.open_units) for lot in lots],
                         [(OY.BOUGHT, True, "", 0.0),
                          (OY.BOUGHT, False, "2025-12-15", 1.0)])
        doc = OY.analyze(lots, start="2025-01-01", end="2025-12-31",
                         scope="tax year 2025")
        self.assertEqual([(c["side"], c["how"], c["pnl"])
                          for c in doc["closed"]],
                         [(OY.BOUGHT, OY.SOLD, None)])
        self.assertEqual([(o["side"], o["contracts"]) for o in doc["open"]],
                         [(OY.BOUGHT, 1.0)])
        total = doc["totals"]["CAD"]
        self.assertIsNone(total["realized"])
        self.assertEqual((total["written_contracts"], total["unknown_cost"]),
                         (0.0, 1))


class TestDirAbbreviationKeepsTheAccount(unittest.TestCase):
    """TJ-0553: in a project with an account named `sum`, `tjs -C DIR
    events sum --json` is one JSON document of account sum, but `tjs
    --di DIR events sum --json` (argparse takes the abbreviation) ran
    `events` for every account and then `sum`."""

    def test_the_prefix_readers_resolve_it(self):
        from taxjson.bin import taxjson_run as R
        p, sub = R._build_parser()
        for spelling in (["--di", "X"], ["--di=X"],
                         ["--dir=X"], ["-CX"], ["-C", "X"],
                         ["--dir", "Y", "--di", "X"]):
            self.assertEqual(R._prefix_dir(p, spelling), "X", spelling)
        self.assertIsNone(R._prefix_dir(p, ["--version"]))
        self.assertTrue(R._no_command(["--di", "run"], set(sub.choices), p))
        self.assertFalse(R._no_command(["--di", "run", "sum"],
                                       set(sub.choices), p))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, accounts=("sum", "margin"))
            (root / "inputs" / "sum" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 10 10 0\n")
            (root / "inputs" / "margin" / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZR.TO 1 CAD 20 20 0\n")
            self.assertEqual(_tjs(root, "run", "--no-input").returncode, 0)
            argv = python_module_argv("taxjson.bin.taxjson_run")
            for prefix in (["--di", str(root)], [f"--di={root}"]):
                r = subprocess.run(argv + prefix + ["events", "sum",
                                                    "--json"],
                                   capture_output=True, text=True,
                                   env=_env(), stdin=subprocess.DEVNULL,
                                   timeout=600, cwd=d)
                self.assertEqual(r.returncode, 0, r.stderr)
                doc = json.loads(r.stdout)
                self.assertEqual({row["account"] for row in doc["rows"]},
                                 {"sum"}, prefix)


if __name__ == "__main__":
    unittest.main()
