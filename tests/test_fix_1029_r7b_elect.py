"""Backlog fix round 7 (wave B) of 2026-10-29, group `elect`: corporate-
event elections (`tjs elect`) and the rename / split reviews (`tjs
renames`, `tjs splits`), and the per-account holdings of a blended pool.

TJ-0849  elect re-extracts an event with the run's ticker.map: a spin-off
         whose parent is a broker code the map names keeps its id.
TJ-0848  elect --redo / --reset --event take a saved election's legacy id
         or the current id it migrates to.
TJ-0847  elect --redo --event asks and saves that event only, and an
         interrupted redo saves no other event's election.
TJ-0851  renames ACCOUNT shows that account's holding and cost only.
TJ-0687  renames --pending lists the renames of the old symbol that has a
         late trade, not another rename to the same symbol that day.
TJ-0853  splits: each link of a same-day rename chain holds the shares
         the link before carried into it.
TJ-0678  splits: a short holding's fraction is covered by a buy; one not
         yet covered is paid, never "booked as a small sale".
TJ-0852  renames: the book cost a rename carries takes an assigned put's
         premium out of the delivered shares' cost, as the books do.
TJ-0696  a split one account of a blended pool reports splits the other
         accounts' shares in their holdings (list, list DATE, sanity).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*, SYN*, PARENT, CHILD*), account ids (U1234567,
99900001), amounts under 1,000.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


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


def _project(d, books, ticker_map="", settings=""):
    """A Canadian project of taxable accounts, each with a book.tt."""
    root = Path(d) / "proj"
    for a, text in books.items():
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "book.tt").write_text(text)
    (root / "taxjson.toml").write_text(
        '[settings]\ncountry = "canada"\nyear = 2025\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'local_timezone = "America/Toronto"\n' + settings
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
    (root / "ticker.map").write_text(ticker_map)
    return root


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")
_QT_SPINOFF = ("2025-06-02 12:00:00 AM,2025-06-02 12:00:00 AM,DIS,CHILD.TO,"
               "CHILD SPINCO INC SPINOFF FROM SEC# X123456 PARENT CORP REC "
               "05/20/25 PAY 06/02/25 ON 100 SHS,10,0,0,0,0,CAD,99900001,"
               "Dividends,Individual margin\n")


class TestElectExtractsWithTheTickerMap(unittest.TestCase):
    """TJ-0849: a Questrade spin-off names its parent by SEC# X123456 and
    ticker.map says `GLOBAL X123456 PARENT.TO`. The run's id is the
    mapped parent's; elect re-extracted without the map, got another id,
    and `--redo --event ID` said the event is in none of the exports,
    `--set ID=...` after `--reset` that it matches no event."""

    def test_reset_then_set_and_the_redo_preflight_find_the_event(self):
        import taxjson.bin.taxjson_run as R
        with tempfile.TemporaryDirectory() as d:
            root = _project(
                d, {"margin": "BUYSELL 2025-01-02 09:00:00 PARENT.TO 100 "
                              "CAD 10 1000 0\n"},
                ticker_map="GLOBAL X123456 PARENT.TO\n")
            (root / "inputs" / "margin" / "qt.csv").write_text(
                _QT_HEADER + _QT_SPINOFF)
            first = _tjs(root, "run", "--no-input")
            self.assertEqual(first.returncode, 3, first.stderr[-2000:])
            pend = json.loads((root / "work" / "pending_elections.json")
                              .read_text())
            eid = pend["accounts"]["margin"]["pending"][0]["event_id"]
            self.assertIn("parent", eid)
            s = _tjs(root, "elect", "margin", "--set",
                     f"{eid}=rollover_s_86_1", "--hint",
                     "allocated_acb_cad=200")
            self.assertEqual(s.returncode, 0, s.stderr)
            run = _tjs(root, "run", "--no-input")
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            # The pending file is gone: the event is re-extracted.
            self.assertIn(eid, R._extracted_event_ids(
                root / "inputs" / "margin", "margin", root))
            r = _tjs(root, "elect", "margin", "--reset", "--event", eid)
            self.assertEqual(r.returncode, 0, r.stderr)
            again = _tjs(root, "elect", "margin", "--set",
                         f"{eid}=rollover_s_86_1", "--hint",
                         "allocated_acb_cad=200")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn(f"Election saved: {eid}", again.stdout)


_IB_HEADER = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n"
    "Corporate Actions,Header,Asset Category,Currency,Report Date,"
    "Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n")


def _ib_spinoff(day, child):
    return (f'Corporate Actions,Data,Stocks,CAD,{day},"{day}, 10:00:00",'
            f'"PARENT(US0000000001) Spinoff 1 for 2 ({child}, SYNTHETIC '
            f'CHILD, US0000000002)",5,0,50,0,\n')


def _save(path, records):
    """manifest.json holding `records`: {id: (election, summary, hints)}."""
    from taxjson.lib import corp_actions as ca
    m = ca.Manifest()
    for eid, (election, summary, hints) in records.items():
        m.set(ca.ElectionRecord(event_id=eid, summary=summary,
                                election=election, hints=dict(hints)))
    m.save(path)


def _redo(root, account, event=None, run_to_file=None):
    """`elect ACCOUNT --redo [--event ID]` in-process on a 'terminal'
    (stdin.isatty patched); the corp-actions child is `run_to_file`
    (default: records each command). Returns (stdout, stderr, calls)."""
    import argparse
    import contextlib
    import io
    from unittest import mock
    import taxjson.bin.taxjson_run as R
    calls = []
    args = argparse.Namespace(
        dir=str(root), account=account, redo=True, reset=False,
        event=event, set=None, hint=None, pending=False, json=False)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(R, "run_to_file", run_to_file or (
            lambda cmd, o, **kw: calls.append(cmd))), \
            mock.patch.object(R.sys.stdin, "isatty", lambda: True), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        R.cmd_elect(args)
    return out.getvalue(), err.getvalue(), calls


class TestElectTakesALegacyIdAndItsCurrentId(unittest.TestCase):
    """TJ-0848: a year folder reading shared exports (../inputs) whose
    manifest holds an election under the pre-2026-07 hash id. The run
    applies it (the id migrated in memory: a run never rewrites the
    shared manifest), but `elect --redo --event OLD` said the event is
    in none of the exports and `--event CURRENT` that there is no such
    election: neither id could redo it. TJ-0556's refusals stay."""

    def _year(self, d):
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        top = Path(d) / "taxes"
        year = _project(top / "y", {"margin": ""},
                        settings='inputs_dir = "../inputs"\n')
        year = year.rename(top / "2025")
        import shutil
        shutil.rmtree(year / "inputs" / "margin")
        shared = top / "inputs" / "margin"
        shared.mkdir(parents=True)
        (shared / "book.tt").write_text(
            "BUYSELL 2025-01-02 09:00:00 PARENT.US 10 CAD 10 100 0\n")
        csv = shared / "ib.csv"
        csv.write_text(_IB_HEADER + _ib_spinoff("2025-06-02", "CHILD"))
        ev = parse_ib_corporate_actions(csv, account="margin")[0]
        old, new = ev.legacy_event_id(), ev.event_id
        self.assertNotEqual(old, new)
        path = shared / "manifest.json"
        records = {old: ("taxable_deemed_dividend", ev.summary(),
                         {"fmv_per_share": 10})}
        _save(path, records)
        return year, path, old, new, records

    def test_redo_takes_either_id(self):
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            year, path, old, new, records = self._year(d)
            run = _tjs(year, "run", "--no-input")
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            # The run kept the shared manifest as it was.
            self.assertEqual(sorted(ca.Manifest.load(path).records), [old])
            # One event, by either id; all (it was kept as "in none of
            # margin's exports").
            for event in (old, new, None):
                _save(path, records)
                out, err, calls = _redo(year, "margin", event=event)
                self.assertIn("Cleared 1 election(s)", out, event)
                self.assertEqual(len(calls), 1, event)
                self.assertEqual(ca.Manifest.load(path).records, {}, event)

    def test_redo_interrupted_puts_the_old_record_back(self):
        import subprocess as sp
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            year, path, old, new, records = self._year(d)

            def interrupted(cmd, out, **kw):
                raise sp.CalledProcessError(3, cmd)
            with self.assertRaises(SystemExit):
                _redo(year, "margin", event=new, run_to_file=interrupted)
            recs = ca.Manifest.load(path).records
            self.assertEqual(sorted(recs), [old])
            self.assertEqual(recs[old].election, "taxable_deemed_dividend")

    def test_reset_takes_the_current_id(self):
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            year, path, old, new, records = self._year(d)
            r = _tjs(year, "elect", "margin", "--reset", "--event", new)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(ca.Manifest.load(path).records, {})
            # An id that is neither stays refused, nothing cleared.
            _save(path, records)
            r = _tjs(year, "elect", "margin", "--reset", "--event",
                     "20250602-parent-other-0000")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("no election", r.stderr)
            self.assertEqual(sorted(ca.Manifest.load(path).records), [old])


def _on_a_terminal(root, *args, answers=""):
    """The CLI with a pseudo-terminal as its stdin, `answers` typed
    ahead ("\x04" at a line start is an end of input)."""
    import pty
    import signal
    master, slave = pty.openpty()
    argv = python_module_argv("taxjson.bin.taxjson_run")
    z = subprocess.Popen(argv + ["-C", str(root), *args], stdin=slave,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True, env=_env())
    os.close(slave)
    try:
        if answers:
            os.write(master, answers.encode())
        try:
            out, err = z.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(z.pid, signal.SIGKILL)
            out, err = z.communicate(timeout=10)
            err += "\nTIMEOUT"
    finally:
        os.close(master)
    return z.returncode, out, err


class TestRedoOfOneEventAsksAboutItOnly(unittest.TestCase):
    """TJ-0847: two spin-offs in one export, the later elected. `elect
    margin --redo --event LATER` asked about the earlier, unanswered
    event first and saved that answer too; interrupted after it, the
    redo said "restored unchanged" and kept the earlier event's new
    election."""

    def _project(self, d):
        from taxjson.lib.corp_actions import (options_for,
                                              parse_ib_corporate_actions)
        root = _project(d, {"margin": "BUYSELL 2025-01-02 09:00:00 "
                                      "PARENT.US 10 CAD 10 100 0\n"})
        csv = root / "inputs" / "margin" / "ib.csv"
        csv.write_text(_IB_HEADER + _ib_spinoff("2025-06-02", "CHILDONE")
                       + _ib_spinoff("2025-07-02", "CHILDTWO"))
        evs = parse_ib_corporate_actions(csv, account="margin")
        self.assertEqual(len({e.event_id for e in evs}), 2)
        path = root / "inputs" / "margin" / "manifest.json"
        _save(path, {evs[1].event_id: (
            "taxable_deemed_dividend", evs[1].summary(),
            {"fmv_per_share": 10})})
        keys = [k for k, _ in options_for("canada", "spinoff")]
        return root, path, evs[0].event_id, evs[1].event_id, keys

    def test_only_the_chosen_event_is_asked_and_saved(self):
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            root, path, earlier, later, keys = self._project(d)
            n = keys.index("ignore") + 1
            rc, out, err = _on_a_terminal(
                root, "elect", "margin", "--redo", "--event", later,
                answers=f"{n}\n\n{n}\n\n")
            self.assertEqual(rc, 0, err[-2000:])
            self.assertNotIn(earlier, err)
            self.assertIn("Found 1 new corp-action event", err)
            recs = ca.Manifest.load(path).records
            self.assertEqual(sorted(recs), [later])
            self.assertEqual(recs[later].election, "ignore")
            # The corp file `run` reads is not the redo's partial one.
            self.assertFalse(
                (root / "work" / "margin_ib_corp.json").exists())

    def test_interrupted_redo_saves_nothing(self):
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            root, path, earlier, later, keys = self._project(d)
            before = sorted(ca.Manifest.load(path).records)
            # One answer, then the end of input: it answered the earlier
            # event, which stayed saved after "restored unchanged".
            n = keys.index("ignore") + 1
            rc, out, err = _on_a_terminal(
                root, "elect", "margin", "--redo", "--event", later,
                answers=f"{n}\n\n\x04")
            self.assertNotIn(earlier, ca.Manifest.load(path).records,
                             err[-2000:])
            _save(path, {later: ("taxable_deemed_dividend", "synthetic",
                                 {"fmv_per_share": 10})})
            rc, out, err = _on_a_terminal(
                root, "elect", "margin", "--redo", "--event", later,
                answers="\x04")
            self.assertEqual(rc, 1, err[-2000:])
            self.assertIn("--redo interrupted", err)
            recs = ca.Manifest.load(path).records
            self.assertEqual(sorted(recs), before)
            self.assertEqual(recs[later].election,
                             "taxable_deemed_dividend")


class TestRenamesForOneAccount(unittest.TestCase):
    """TJ-0851: OLD.TO renamed NEW.TO, held in margin (10 shares, cost
    100) and other (20, cost 200). `renames margin --json` listed both
    accounts and both holdings and costs."""

    def test_the_account_asked_for_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {
                "margin": "BUYSELL 2025-01-02 09:00:00 OLD.TO 10 CAD 10 "
                          "100 0\nRENAME 2025-06-02 OLD.TO NEW.TO\n",
                "other": "BUYSELL 2025-01-02 09:00:00 OLD.TO 20 CAD 10 "
                         "200 0\n"})
            run = _tjs(root, "run", "--no-input")
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            both = json.loads(_tjs(root, "renames", "--json").stdout)
            self.assertEqual(sorted(both["renames"][0]["accounts"]),
                             ["margin", "other"])
            for acct, qty, cost in (("margin", 10, 100), ("other", 20, 200)):
                r = _tjs(root, "renames", acct, "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                ev = json.loads(r.stdout)["renames"][0]
                self.assertEqual(ev["accounts"], [acct])
                self.assertEqual(
                    [(c["account"], c["qty"], c["book_cost"])
                     for c in ev["carried"]], [(acct, qty, cost)])
                text = _tjs(root, "renames", acct).stdout
                self.assertNotIn("other" if acct == "margin" else "margin:",
                                 text)


class TestPendingRenamesJoinTheOldSymbol(unittest.TestCase):
    """TJ-0687: ALFA.TO (margin) and BETA.TO (other) both renamed NEWX.TO
    on one day; only ALFA.TO has a trade after its rename. `renames
    --pending --json` listed both renames (the late trades were joined
    to the events by date and new symbol only)."""

    def test_only_the_rename_with_the_late_trade(self):
        with tempfile.TemporaryDirectory() as d:
            books = {}
            for a, old in (("margin", "ALFA"), ("other", "BETA")):
                books[a] = (f"BUYSELL 2025-01-02 10:00:00 {old}.TO 10 CAD 1 "
                            f"10 0\nRENAME 2025-02-03 {old}.TO NEWX.TO\n"
                            + (f"BUYSELL 2025-03-03 10:00:00 {old}.TO -1 "
                               f"CAD 1 1 0\n" if a == "margin" else ""))
            root = _project(d, books)
            _tjs(root, "run", "--no-input")
            every = json.loads(_tjs(root, "renames", "--json").stdout)
            self.assertEqual(len(every["renames"]), 2)
            r = _tjs(root, "renames", "--pending", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual([(e["old"], e["new"]) for e in doc["renames"]],
                             [("ALFA.TO", "NEWX.TO")])
            self.assertEqual(doc["unresolved"], 1)
            text = _tjs(root, "renames", "--pending").stdout
            self.assertIn("ALFA.TO -> NEWX.TO", text)
            self.assertNotIn("BETA.TO", text)
            # The full listing puts the late trade under ALFA.TO only.
            full = _tjs(root, "renames").stdout
            beta = full[full.index("BETA.TO -> NEWX.TO", full.index(
                "BETA.TO -> NEWX.TO") + 1):]
            self.assertNotIn("trade(s) in BETA.TO", beta)


class TestSplitsSameDayRenameChain(unittest.TestCase):
    """TJ-0853: FIRST.TO renamed SECOND.TO and SECOND.TO renamed THIRD.TO
    on the same day, ten shares carried into THIRD.TO. `splits --json`
    said the second link held 0 before and after ("nothing held just
    before: the split changes nothing here"): it counted the day's
    opening holding only, not the shares the first link moved in."""

    def _splits(self, d, lines):
        root = _project(d, {"margin": "BUYSELL 2025-01-02 10:00:00 "
                                      "FIRST.TO 10 CAD 10 100 0\n"
                                      + "".join(lines)})
        run = _tjs(root, "run", "--no-input")
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        r = _tjs(root, "splits", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        rows = doc["splits"] if isinstance(doc, dict) else doc
        return {(x["symbol"], x["new_symbol"]): x for x in rows}

    def test_each_link_holds_the_ten_shares(self):
        first = "RENAME 2025-06-02 FIRST.TO SECOND.TO\n"
        second = "RENAME 2025-06-02 SECOND.TO THIRD.TO\n"
        for n, lines in enumerate(([first, second], [second, first])):
            with tempfile.TemporaryDirectory() as d:
                got = self._splits(d, lines)
                for link in (("FIRST.TO", "SECOND.TO"),
                             ("SECOND.TO", "THIRD.TO")):
                    x = got[link]
                    self.assertEqual((x["held_before"], x["held_after"]),
                                     (10, 10), (n, link))
                    self.assertFalse(any("nothing held" in w
                                         for w in x["why"]), (n, link))


class TestSplitsShortFraction(unittest.TestCase):
    """TJ-0678: a short of 3 SYN.TO consolidated 1-for-2 is short 1.5;
    the half share bought back is its fraction's cash in lieu. `splits`
    said "[FRACTION] ... expect cash in lieu for the fraction (booked as
    a small sale)": a sale would add to the short."""

    def _split(self, d, cover):
        root = _project(d, {"margin": (
            "BUYSELL 2025-01-02 10:00:00 SYN.TO -3 CAD 1 3 0\n"
            "SPLIT 2025-01-03 09:30:00 SYN.TO SYN.TO 0.5\n" + cover)})
        run = _tjs(root, "run", "--no-input")
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        r = _tjs(root, "splits", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["splits"][0], _tjs(root, "splits").stdout

    def test_the_buy_back_is_the_fraction_s_cash_in_lieu(self):
        with tempfile.TemporaryDirectory() as d:
            x, text = self._split(
                d, "BUYSELL 2025-01-06 10:00:00 SYN.TO 0.5 CAD 2 1 0\n")
            self.assertEqual(x["held_after"], -1.5)
            self.assertEqual(x["flags"], ["CIL"])
            self.assertIn("0.5 bought back on 2025-01-06", " ".join(x["why"]))
            self.assertIn("each fraction's cash in lieu is booked", text)

    def test_an_open_short_fraction_is_to_be_paid(self):
        with tempfile.TemporaryDirectory() as d:
            x, _text = self._split(d, "")
            self.assertEqual(x["flags"], ["FRACTION"])
            why = " ".join(x["why"])
            self.assertIn("(short): expect to pay cash in lieu", why)
            self.assertNotIn("small sale", why)

    def test_a_sale_of_the_fraction_is_no_cover(self):
        with tempfile.TemporaryDirectory() as d:
            x, _text = self._split(
                d, "BUYSELL 2025-01-06 10:00:00 SYN.TO -0.5 CAD 2 1 0\n")
            self.assertEqual(x["flags"], ["FRACTION"])


class TestRenameCarriesTheAssignedPutPremium(unittest.TestCase):
    """TJ-0852: a put written for 100 and assigned into 100 SYN at a
    strike of 10 (1000), then SYN renamed NEW. The books carry 900 into
    NEW; `renames --json` said book_cost 1000 (the premium left out). A
    direct purchase at 900 is the control."""

    def _carried(self, d, country, book):
        cur = "CAD" if country == "canada" else "USD"
        sfx = ".US" if country == "canada" else ""
        root = _project(d, {"margin": book.format(c=cur, s=sfx)
                            + f"RENAME 2025-07-02 SYN{sfx} NEW{sfx}\n"},
                        settings="option_grant_timing_since = 2025\n")
        if country == "usa":
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                t.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"')
                .replace('province = "ON"\n', "")
                .replace("option_grant_timing_since = 2025\n", ""))
        run = _tjs(root, "run", "--no-input")
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        r = _tjs(root, "renames", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        inv = json.loads((root / "work" / "margin_gains_wash.json")
                         .read_text()
                         if (root / "work" / "margin_gains_wash.json")
                         .exists() else
                         (root / "work" / "margin_gains.json").read_text())
        held = {x["symbol"]: round(float(x["total_cost"]), 2)
                for x in inv["inventory"]}
        return json.loads(r.stdout)["renames"][0]["carried"][0], held

    def test_the_premium_is_out_of_the_carried_cost(self):
        assigned = ("BUYSELL 2025-01-02 10:00:00 SYN250620P00010000{s} -1 "
                    "{c} 1 100 0\n"
                    "ASSIGN 2025-06-10 16:00:00 SYN250620P00010000{s} 1 "
                    "{c} 0 0 0\n"
                    "ASSIGN 2025-06-10 16:01:00 SYN{s} 100 {c} 10 1000 0\n")
        bought = "BUYSELL 2025-01-02 10:00:00 SYN{s} 100 {c} 9 900 0\n"
        # A call bought for 100 and exercised: its cost into the shares.
        called = ("BUYSELL 2025-01-02 10:00:00 SYN250620C00010000{s} 1 "
                  "{c} 1 100 0\n"
                  "ASSIGN 2025-06-10 16:00:00 SYN250620C00010000{s} -1 "
                  "{c} 0 0 0\n"
                  "ASSIGN 2025-06-10 16:01:00 SYN{s} 100 {c} 10 1000 0\n")
        for country in ("canada", "usa"):
            sfx = ".US" if country == "canada" else ""
            for book, cost in ((assigned, 900), (bought, 900),
                               (called, 1100)):
                with tempfile.TemporaryDirectory() as d:
                    carried, held = self._carried(d, country, book)
                    self.assertEqual(held.get(f"NEW{sfx}"), cost,
                                     (country, held))
                    self.assertEqual((carried["qty"], carried["book_cost"]),
                                     (100, cost), (country, book))


class TestASplitOneAccountReportsSplitsThePool(unittest.TestCase):
    """TJ-0696: alpha and beta each buy 10 SYN for 100; only alpha's
    books carry the 2-for-1 split. The engine split the whole security
    (40 shares, cost 200), but beta's share of the pool was apportioned
    on its unsplit 10 (10 shares, cost 50), `list beta DATE` said the
    same, a conservation warning said 10 short, and `sanity` against
    beta's 20 found QTY_MISMATCH. With the split in both accounts' books
    it is one event: 20 shares, cost 100 each."""

    def _run(self, d, country, both):
        cur = "CAD" if country == "canada" else "USD"
        sym = "SYN.TO" if country == "canada" else "SYN"
        split = f"SPLIT 2025-05-01 09:30:00 {sym} {sym} 2\n"
        buy = f"BUYSELL 2025-01-02 10:00:00 {sym} 10 {cur} 10 100 0\n"
        root = _project(d, {
            "alpha": buy + split,
            "beta": buy + (split if both else "")
            + f"BUYSELL 2025-07-02 10:00:00 ZZOTHER 1 {cur} 10 10 0\n"})
        if country == "usa":
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                t.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"')
                .replace('province = "ON"\n', ""))
        run = _tjs(root, "run", "--no-input")
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        self.assertNotIn("per-account split accounts for only", run.stderr)
        snap = root / "snap.toml"
        snap.write_text(f'[meta]\nas_of = "2025-06-30"\naccount = "beta"\n'
                        f'[[holding]]\nsymbol = "{sym}"\nquantity = 20\n')
        s = _tjs(root, "sanity", f"beta={snap}", "--json")
        self.assertEqual(s.returncode, 0, s.stdout[-2000:] + s.stderr)
        out = {}
        for when in (["2025-06-30"], []):
            r = _tjs(root, "list", "beta", *when, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            out[tuple(when)] = {x["symbol"]: (x["qty"], x["cost"])
                                for x in json.loads(r.stdout)["rows"]}[sym]
        return out

    def test_an_event_the_account_reports_is_not_added(self):
        """A merger with per-account ratios (alpha's 1, beta's 1.05) is
        each account's own row; gamma, with none, takes one copy (the
        earliest), never two."""
        from taxjson.lib.positions_check import pool_splits

        def split(acct, day, ratio):
            return {"action": "SPLIT", "account": acct, "date": day,
                    "symbol": "ZZOLD", "symbol_new": "ZZNEW",
                    "quantity": ratio}
        pool = [split("alpha", "2025-06-03", 1.0),
                split("beta", "2025-06-04", 1.05),
                {"action": "BUYSELL", "account": "alpha", "symbol": "ZZOLD",
                 "quantity": 10, "date": "2025-01-02"},
                split("alpha", "2025-09-01", 2.0)]
        self.assertEqual(
            [(r["date"], r["quantity"]) for r in pool_splits(pool, "beta")],
            [("2025-09-01", 2.0)])
        got = pool_splits(pool, "gamma")
        self.assertEqual([(r["account"], r["date"], r["quantity"],
                           r["pool_split_of"]) for r in got],
                         [("gamma", "2025-06-03", 1.0, "alpha"),
                          ("gamma", "2025-09-01", 2.0, "alpha")])

    def test_each_account_holds_its_split_shares(self):
        for country in ("canada", "usa"):
            for both in (False, True):
                with tempfile.TemporaryDirectory() as d:
                    got = self._run(d, country, both)
                    for when, (qty, cost) in got.items():
                        self.assertEqual((qty, round(cost, 2)), (20, 100),
                                         (country, both, when))


if __name__ == "__main__":
    unittest.main()
