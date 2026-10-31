"""Fix round 7 (wave C, 2026-10-29), group "coverage": the third
worktree audit's final continuation (backlog TJ-0930 to TJ-0946).

Export coverage (lib/export_coverage): each broker ACCOUNT of an
explicitly combined IB account is checked against its own statements
(TJ-0930); a split is effective at the open of its day in a Canadian
project, whatever its clock stamp (TJ-0931); one later sale closes at
most its own units, never two brokers' positions (TJ-0932).

T1135: cash in transit between your own accounts at the year end is
counted, or named as unknown (TJ-0946). scripts/land.sh waits for every
check before GitHub's auto-merge, and says when GitHub refused to close
the pull request (TJ-0942, TJ-0943), with `gh` a stub and the remotes
local bare repositories.

Synthetic exports only (ids like U1234567, 99900001; made-up tickers).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_fix_ibparse import TRADES_H, _trade
from test_fix_release_safeguards import _NAME as _PUB_NAME
from test_fix_release_safeguards import _Sandbox as _PublicSandbox
from test_fix_release_safeguards import _public_api

from taxjson.lib import checklist as cl
from taxjson.lib import export_coverage as EC
from taxjson.lib.tomlcompat import tomllib

_REPO = Path(__file__).resolve().parent.parent


def _project(root: Path, year: int, accounts, extra: str = "") -> Path:
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    body = "".join(f'[accounts.{json.dumps(a)}]\ntype = "{t}"\n'
                   for a, t in accounts.items())
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(head + body + extra)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    (root / "ticker.map").write_text("")
    return root


def _cfg(root: Path):
    return tomllib.loads((root / "taxjson.toml").read_text())


def _ib(acct_id: str, end: str, symbol: str, qty: int = 10) -> str:
    return ('Statement,Header,Field Name,Field Value\n'
            f'Statement,Data,Period,"January 1, 2025 - {end}"\n'
            'Account Information,Header,Field Name,Field Value\n'
            f'Account Information,Data,Account,{acct_id}\n'
            + TRADES_H
            + _trade(symbol, '2025-03-03, 10:00:00', qty, 10, -10 * qty,
                     acct=acct_id, cur='CAD'))


def _checklist(root: Path):
    return cl.d_export_coverage(cl.Ctx(
        root=root, cfg=_cfg(root), year=2025, today=date(2026, 3, 1),
        run_sub=cl.default_run_sub(root)))


class TestCombinedIbAccounts(unittest.TestCase):
    """TJ-0930: a combined account holds two IB accounts; the first's
    statement stops March 3 holding 10 SYN, the second's reaches Dec 31
    holding SYNB. The first account's gap stays."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = _project(Path(cls.tmp.name) / "p", 2025,
                        {"a": "taxable"},
                        extra="combined_broker_accounts = true\n")
        (root / "inputs/a/a.csv").write_text(
            _ib("U1234567", "March 3, 2025", "SYN"))
        (root / "inputs/a/b.csv").write_text(
            _ib("U9990002", "December 31, 2025", "SYNB"))
        cls.root = root
        cls.r = tj(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_short_account_keeps_its_gap(self):
        gaps = EC.find_gaps(self.root, _cfg(self.root),
                            today=date(2026, 3, 1))
        self.assertEqual([(g.broker, g.end, g.positions) for g in gaps],
                         [("ib", "2025-03-03", [("SYN.TO", 10.0)])])

    def test_checklist_needs_attention(self):
        res = _checklist(self.root)
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("exports for a end 2025-03-03", res.detail)

    def test_run_warns(self):
        self.assertIn("Interactive Brokers exports for a end 2025-03-03",
                      self.r.stdout + self.r.stderr)


class TestCombinedIbAccountsBothCurrent(unittest.TestCase):
    """TJ-0930's control: both IB accounts' statements reach Dec 31 and
    the second one's last statement holds no trade: no gap (a statement
    with no row still dates its account's end)."""

    def test_no_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, {"a": "taxable"},
                            extra="combined_broker_accounts = true\n")
            (root / "inputs/a/a.csv").write_text(
                _ib("U1234567", "December 31, 2025", "SYN"))
            (root / "inputs/a/b.csv").write_text(
                _ib("U9990002", "June 30, 2025", "SYNB"))
            tj(root, "run", "--no-input")
            self.assertEqual(
                [(g.end, g.positions) for g in EC.find_gaps(
                    root, _cfg(root), today=date(2026, 3, 1))],
                [("2025-06-30", [("SYNB.TO", 10.0)])])
            # The same account's second half: a statement with no trade.
            (root / "inputs/a/b2.csv").write_text(
                _ib("U9990002", "December 31, 2025", "SYNB").replace(
                    "January 1, 2025", "July 1, 2025").split("Trades,")[0])
            tj(root, "run", "--no-input")
            self.assertEqual(EC.find_gaps(root, _cfg(root),
                                          today=date(2026, 3, 1)), [])


class TestUnitsOfOneFile(unittest.TestCase):
    """TJ-0930: an export of two broker accounts in ONE file (a Questrade
    export, an Account column) reaches its last row for both: a quiet
    account in it is not short; separate files date separately."""

    def test_units(self):
        p, q = Path("both.csv"), Path("other.csv")
        r1 = {"source_account": "h1", "date": "2025-03-03"}
        r2 = {"source_account": "h2", "date": "2025-12-15"}
        units = EC._units([r1, r2], [p], {p: ["h1", "h2"]},
                          {id(r1): p, id(r2): p})
        self.assertEqual([(u[0], u[1], u[2]) for u in units],
                         [([r1], [p], [r1, r2]), ([r2], [p], [r2, r1])])
        units = EC._units([r1, r2], [p, q], {p: ["h1"], q: ["h2"]},
                          {id(r1): p, id(r2): q})
        self.assertEqual([(u[0], u[1], u[2]) for u in units],
                         [([r1], [p], [r1]), ([r2], [q], [r2])])
        # One broker account: as before.
        self.assertEqual(EC._units([r1], [p], {p: ["h1"]}, {}),
                         [([r1], [p], [r1])])


class TestSplitAtTheOpen(unittest.TestCase):
    """TJ-0931: an IB statement ends March 3 holding 10 SYN; a June 2
    reverse split 0.5 stamped noon and a 09:30 sale of the 5 resulting
    shares close the position (Canada: the split is effective at the
    open of its day, as the engine orders it)."""

    def _gaps(self, clock):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, {"margin": "taxable"})
            (root / "inputs/margin/ib.csv").write_text(
                _ib("U1234567", "March 3, 2025", "SYN"))
            (root / "inputs/margin/trades.tt").write_text(
                f"SPLIT 2025-06-02 {clock} SYN.TO SYN.TO 0.5\n"
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -5 CAD 30 150 0\n")
            tj(root, "run", "--strict", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [(g.end, g.positions, g.closed_by_tt, g.info)
                    for g in gaps]

    def test_noon_split_closes_like_midnight(self):
        want = [("2025-03-03", [], ["SYN.TO"], True)]
        self.assertEqual(self._gaps("00:00:01"), want)
        self.assertEqual(self._gaps("12:00:00"), want)

    def test_us_orders_by_the_clock(self):
        rows = [{"action": "SPLIT", "date": "2025-06-02", "time": "12:00:00",
                 "symbol": "SYN", "symbol_new": "SYN", "quantity": 0.5,
                 "source": "trades.tt"},
                {"action": "BUYSELL", "date": "2025-06-02",
                 "time": "09:30:00", "symbol": "SYN", "quantity": -5,
                 "source": "trades.tt"}]
        still, by_tt, _o = EC.closed_later([("SYN", 10.0)], rows)
        self.assertEqual((still, by_tt), ([("SYN", 2.5)], []))
        still, by_tt, _o = EC.closed_later([("SYN", 10.0)], rows,
                                           split_at_open=True)
        self.assertEqual((still, by_tt), ([], ["SYN"]))

    def test_a_trade_before_an_evening_split_stays_before_it(self):
        # IB posts a split at 20:25 dated the trade day: a sale that
        # morning (settling the next day) was of pre-split shares, as
        # the engine re-denominates it — both countries.
        rows = [{"action": "SPLIT", "date": "2025-06-02", "time": "20:25:00",
                 "symbol": "SYN", "symbol_new": "SYN", "quantity": 2.0,
                 "source": "ib.csv"},
                {"action": "BUYSELL", "date": "2025-06-02",
                 "date_settle": "2025-06-03", "time": "10:00:00",
                 "symbol": "SYN", "quantity": -4, "source": "ib.csv"}]
        for at_open in (False, True):
            still, _t, _o = EC.closed_later([("SYN", 10.0)], rows,
                                            split_at_open=at_open)
            self.assertEqual(still, [("SYN", 12.0)], at_open)
            self.assertEqual(EC.open_positions(
                [{"action": "BUYSELL", "date": "2025-06-01",
                  "symbol": "SYN", "quantity": 10}] + rows,
                date(2025, 6, 2), at_open), [("SYN", 12.0)])


_GENERIC_MAP = '''[columns]
date="Date"
action="Action"
symbol="Symbol"
quantity="Quantity"
price="Price"
amount="Amount"
[defaults]
currency="CAD"
[actions]
BUY="buy"
SELL="sell"
[broker]
name="{broker}"
account="{account}"
'''


class TestOneSaleClosesOnce(unittest.TestCase):
    """TJ-0932: one combined account holds 10 SYN at each of two
    generic brokers whose exports stop March 3; one June 2 .tt sale of
    10 closes 10 of the 20: a gap stays (as with one broker holding
    20)."""

    def _gaps(self, brokers):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, {"margin": "taxable"},
                            extra="combined_broker_accounts = true\n")
            for broker, acct, qty in brokers:
                f = root / "inputs/margin" / f"generic_{broker}.csv"
                f.write_text("Date,Action,Symbol,Quantity,Price,Amount\n"
                             f"2025-03-03,BUY,SYN.TO,{qty},10,"
                             f"-{qty * 10}\n")
                f.with_name(f.name + ".toml").write_text(
                    _GENERIC_MAP.format(broker=broker, account=acct))
            (root / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 15 150 0\n")
            tj(root, "run", "--strict", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [g for g in gaps if not g.info], gaps

    def test_one_broker_control(self):
        open_, _all = self._gaps([("alpha", "99900001", 20)])
        self.assertEqual([g.positions for g in open_], [[("SYN.TO", 10.0)]])

    def test_two_brokers_keep_a_gap(self):
        open_, gaps = self._gaps([("alpha", "99900001", 10),
                                  ("beta", "99900002", 10)])
        self.assertEqual([g.positions for g in open_], [[("SYN.TO", 10.0)]],
                         [g.record() for g in gaps])
        # The sale closed one broker's 10: that one is an Info.
        self.assertEqual(len(gaps), 2)

    def test_closed_later_shares_the_rows(self):
        sale = {"action": "BUYSELL", "date": "2025-06-02", "time": "09:30:00",
                "symbol": "SYN", "quantity": -10, "source": "trades.tt"}
        spent = {}
        a = EC.closed_later([("SYN", 10.0)], [sale], spent=spent)
        b = EC.closed_later([("SYN", 10.0)], [sale], spent=spent)
        self.assertEqual(a, ([], ["SYN"], []))
        self.assertEqual(b, ([("SYN", 10.0)], [], []))


# ================================================ TJ-0946 T1135: cash in
# transit between your own accounts at the year end
def _cash_line(line, book, n=1):
    from taxjson.lib import cash_events as CE
    label = book.split("/", 1)[0]
    ev = CE.parse_line(line, f"inputs/{label}/book.tt:{n}")
    ev.update(label=label, file="book.tt", book=book, book_problem="")
    return ev


def _ledger(arrival):
    """a's 100 USD (cost 125) leave on 2025-12-31 as an own move and
    arrive in b on `arrival` (fx_cash_v2 with holdings)."""
    from taxjson.lib import fx_cash_v2 as V2
    a, b = "a/manual", "b/manual"
    lines = [_cash_line("CASHOPEN 2025-01-01 USD 100 125", a),
             _cash_line("CASHMOVE 2025-12-31 USD -100 own", a),
             _cash_line("CASHBAL 2025-12-31 USD 0", a),
             _cash_line("CASHOPEN 2025-01-01 USD 0 0", b),
             _cash_line(f"CASHMOVE {arrival} USD 100 own", b),
             _cash_line("CASHBAL 2025-12-31 USD "
                        + ("100" if arrival <= "2025-12-31" else "0"), b)]
    return V2.build([], {"events": [], "lines": lines, "problems": [],
                         "stablecoins_as_cash": True}, "CAD", 2025,
                    lambda c, d: 1.25, country="canada", holdings=True)


def _year_end(doc, cur="USD"):
    tot = 0.0
    for s in doc["series"]:
        if s["currency"] == cur and s["points"]:
            tot += s["points"][-1][2]
    return round(tot, 2)


class TestTJ0946CashInTransitAtTheYearEnd(unittest.TestCase):
    """TJ-0946: 100 USD (cost 125) leave a (USA custodian) on December
    31 and reach b (USA) on January 2. The FX ledger carries them in
    transit; T1135 counted none of it, settled."""

    @rule("CA-RPT-20")
    def test_both_accounts_abroad_counts_it_under_the_sender(self):
        from taxjson.lib import t1135_cash as TC
        led = _ledger("2026-01-02")
        self.assertEqual(led["status"], "computed", led["problems"])
        self.assertEqual(led["holdings"]["transit"], [
            {"currency": "USD", "units": 100.0, "cost": 125.0,
             "from": ["a/manual"], "to": ["b/manual"],
             "since": "2025-12-31", "arrives": "2026-01-02"}])
        doc = TC.cash_doc(led, {"a": "USA", "b": "USA"}, 2025, "CAD")
        self.assertEqual(_year_end(doc), 125.0)
        self.assertEqual(doc["not_counted"], [])
        # a's own units fall to 0 on 12-31 as its transit starts: its
        # row holds the 100 through the year end.
        a_row, = [s for s in doc["series"] if s["account"] == "a"]
        self.assertEqual(a_row["points"], [["2024-12-31", 100.0, 125.0]])

    @rule("CA-RPT-20")
    def test_same_day_arrival_control(self):
        from taxjson.lib import t1135_cash as TC
        led = _ledger("2025-12-31")
        self.assertEqual(led["holdings"]["transit"], [])
        doc = TC.cash_doc(led, {"a": "USA", "b": "USA"}, 2025, "CAD")
        self.assertEqual(_year_end(doc), 125.0)

    @rule("CA-RPT-20")
    def test_a_canadian_side_leaves_it_unknown(self):
        from taxjson.lib import t1135_cash as TC
        doc = TC.cash_doc(_ledger("2026-01-02"), {"a": "USA"}, 2025, "CAD")
        self.assertEqual(_year_end(doc), 0.0)
        n, = doc["not_counted"]
        self.assertEqual((n["account"], n["currency"], n["unknown"]),
                         ("a", "USD", True))
        self.assertIn("100.00 USD in transit between your own accounts",
                      n["why"])
        # Two countries: the same.
        doc = TC.cash_doc(_ledger("2026-01-02"), {"a": "USA", "b": "GBR"},
                          2025, "CAD")
        self.assertTrue(doc["not_counted"][0]["unknown"])

    def test_no_counted_side_is_no_foreign_property(self):
        from taxjson.lib import t1135_cash as TC
        doc = TC.cash_doc(_ledger("2026-01-02"), {"c": "USA"}, 2025, "CAD")
        self.assertEqual(doc["not_counted"], [])
        self.assertEqual(doc["series"], [])


class TestTJ0946TheCommand(unittest.TestCase):
    """TJ-0946 through `tjs t1135 --json` (the audit's repro)."""

    def _t1135(self, arrival):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            root.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\nfx_cash_ledger = "v2"\n'
                '[accounts.a]\ntype = "taxable"\ncustodian_country = '
                '"USA"\n[accounts.b]\ntype = "taxable"\n'
                'custodian_country = "USA"\n')
            (root / "ticker.map").write_text("")
            for acct, text in (
                    ("a", "CASHOPEN 2025-01-01 USD 100 125\n"
                          "CASHMOVE 2025-12-31 USD -100 own\n"
                          "CASHBAL 2025-12-31 USD 0\n"),
                    ("b", "CASHOPEN 2025-01-01 USD 0 0\n"
                          f"CASHMOVE {arrival} USD 100 own\n"
                          "CASHBAL 2025-12-31 USD "
                          + ("100" if arrival <= "2025-12-31" else "0")
                          + "\n")):
                (root / "inputs" / acct).mkdir(parents=True)
                (root / "inputs" / acct / "trades.tt").write_text(
                    text + "CASHOPEN 2025-01-01 CAD 0 0\n"
                           "CASHBAL 2025-12-31 CAD 0\n")
            tj(root, "run", "--no-input")
            r = tj(root, "t1135", "--json")
            return json.loads(r.stdout)

    @rule("CA-RPT-20")
    def test_year_end_cost_counts_the_transit(self):
        rep = self._t1135("2026-01-02")
        cash = [p for p in rep["properties"] if p.get("cash")]
        self.assertEqual(round(sum(p["year_end_cost"] or 0 for p in cash),
                               2), 125.0, cash)
        self.assertTrue(rep["total_settled"])
        self.assertEqual(rep["cash_not_counted"], [])


# ============================================== TJ-0942 / TJ-0943 land.sh
# A gh stub for land.sh: main's rules (required checks, up to date), the
# pushed commit's Actions jobs (`jobs`: [[name, pass|fail]]), GitHub's
# auto-merge (it merges on the next `pr view` once on — GitHub waits only
# for the REQUIRED checks, here always passed — unless `hold`), and calls
# GitHub refuses (`refuse`: "close", "disable-auto"). `move_after_auto`:
# another commit reaches origin's main once auto-merge is on.
_LAND_STUB = r"""#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile
args = sys.argv[1:]
with open(os.environ["GH_STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
sp = os.environ["LAND_STATE"]
st = json.load(open(sp))
def save():
    json.dump(st, open(sp, "w"))
def git_in(td, *a):
    subprocess.run(["git", *a], cwd=td, check=True, capture_output=True)
def merge():
    with tempfile.TemporaryDirectory() as td:
        git_in(td, "clone", "-q", os.environ["LAND_ORIGIN"], ".")
        if subprocess.run(["git", "merge-base", "--is-ancestor", "main",
                           "origin/" + st["branch"]], cwd=td).returncode:
            return False            # strict: not onto a main that moved
        git_in(td, "cherry-pick", "main..origin/" + st["branch"])
        git_in(td, "push", "-q", "origin", "main", ":refs/heads/" + st["branch"])
    st["state"] = "MERGED"; save()
    return True
def move_main():
    with tempfile.TemporaryDirectory() as td:
        git_in(td, "clone", "-q", os.environ["LAND_ORIGIN"], ".")
        with open(os.path.join(td, "other.txt"), "w") as f:
            f.write("merged meanwhile\n")
        git_in(td, "add", "other.txt")
        git_in(td, "commit", "-q", "-m", "another pull request")
        git_in(td, "push", "-q", "origin", "main")
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[:1] == ["api"]:
    path = [a for a in args[1:] if not a.startswith("-")][0]
    if path.endswith("/rules/branches/main"):
        print(1 if st.get("rules") else 0); sys.exit(0)
    if "/actions/runs?" in path:
        print("9\tcompleted\tCI"); sys.exit(0)
    if "/jobs" in path:
        for name, res in st["jobs"]:
            print("%s\t%s" % (name, res))
        sys.exit(0)
    if path.endswith("/status"):
        sys.exit(0)
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    st["branch"] = args[args.index("--head") + 1]; st["state"] = "OPEN"; save()
    print("https://github.com/taxjson/taxjson/pull/7"); sys.exit(0)
if args[:2] == ["pr", "merge"]:
    if "--disable-auto" in args:
        if "disable-auto" in st.get("refuse", []):
            sys.exit(1)
        st["auto"] = False; save(); sys.exit(0)
    if "--auto" in args:
        st["auto"] = True; save()
        if st.get("move_after_auto"):
            move_main()
        sys.exit(0)
    sys.exit(0 if merge() else 1)
if args[:2] == ["pr", "view"]:
    if st.get("auto") and not st.get("hold") and st["state"] == "OPEN":
        merge()
    print(st["state"]); sys.exit(0)
if args[:2] == ["pr", "close"]:
    if "close" in st.get("refuse", []):
        sys.exit(1)
    st["state"] = "CLOSED"; st["auto"] = False; save(); sys.exit(0)
sys.exit(0)
"""

_PR = "https://github.com/taxjson/taxjson/pull/7"


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"),
                     "bash and git required")
class TestLandWaitsForEveryCheck(unittest.TestCase):
    """TJ-0942: main's rules require checks and an up-to-date branch, so
    land.sh turned GitHub's auto-merge on at once; GitHub waits only for
    the REQUIRED checks, and one the rules do not name failed while the
    pull request merged. TJ-0943: when GitHub refused to turn auto-merge
    off and to close the pull request, land.sh said it was closed."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        d = self.d = Path(self._td.name)
        home = d / "home"
        home.mkdir()
        (d / "denylist").write_text("# test denylist\n")
        self.origin = d / "origin.git"
        self.dev = d / "dev"
        self.env = dict(
            os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Sam Synthetic", GIT_AUTHOR_EMAIL="sam@example.com",
            GIT_COMMITTER_NAME="Sam Synthetic",
            GIT_COMMITTER_EMAIL="sam@example.com", GIT_TERMINAL_PROMPT="0",
            HOME=str(home), TAXJSON_PII_DENYLIST=str(d / "denylist"),
            TMPDIR=str(d), TAXJSON_LAND_POLL_SECS="0",
            LAND_STATE=str(d / "land.json"), LAND_ORIGIN=str(self.origin),
            GH_STUB_LOG=str(d / "gh.log"))
        for k in ("TAXJSON_LAND_DIRECT", "TAXJSON_SLUG",
                  "TAXJSON_PII_AMOUNTS"):
            self.env.pop(k, None)
        (d / "bin").mkdir()
        gh = d / "bin" / "gh"
        gh.write_text(_LAND_STUB)
        gh.chmod(0o755)
        self.env["PATH"] = f"{d / 'bin'}:{os.environ['PATH']}"
        self.git(d, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(d, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.dev / "scripts" / "hooks").mkdir(parents=True)
        for f in ("land.sh", "check-pii.sh", "hooks/pre-push"):
            shutil.copy(_REPO / "scripts" / f, self.dev / "scripts" / f)
        (self.dev / "f.txt").write_text("0\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "start")
        self.git(self.dev, "push", "-q", "origin", "main")
        self.start = self.git(self.dev, "rev-parse", "HEAD")
        (self.dev / "f.txt").write_text("a change\n")
        self.git(self.dev, "commit", "-q", "-am", "a change")

    def tearDown(self):
        self._td.cleanup()

    def git(self, cwd, *args):
        r = subprocess.run(["git", *args], cwd=cwd, env=self.env,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def state(self, **kw):
        st = {"rules": True, "auto": False, "state": "NONE", "branch": "",
              "jobs": [["required", "pass"], ["extra", "pass"]]}
        st.update(kw)
        (self.d / "land.json").write_text(json.dumps(st))

    def stub_state(self):
        return json.loads((self.d / "land.json").read_text())

    def land(self, *args):
        return subprocess.run(["bash", str(self.dev / "scripts" / "land.sh"),
                               *args], cwd=self.d, env=self.env,
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def merges(self, flag):
        log = self.d / "gh.log"
        calls = [json.loads(x) for x in log.read_text().splitlines()]
        return [c for c in calls if c[:2] == ["pr", "merge"] and flag in c]

    def origin_main(self):
        return self.git(self.dev, "ls-remote", "origin",
                        "refs/heads/main").split()[0]

    def test_a_failed_check_the_rules_do_not_require_never_merges(self):
        self.state(jobs=[["required", "pass"], ["extra", "fail"]])
        r = self.land()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Check(s) failed: extra", r.stderr)
        self.assertFalse(self.merges("--auto"))
        self.assertEqual(self.origin_main(), self.start)
        self.assertEqual(self.stub_state()["state"], "CLOSED")

    def test_every_check_passed_then_auto_merge_lands_it(self):
        self.state()
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("waiting for every check, required or not", r.stdout)
        self.assertEqual(len(self.merges("--auto")), 1)
        self.assertEqual(self.git(self.dev, "log", "-1", "--format=%s",
                                  "origin/main"), "a change")

    def test_no_wait_turns_nothing_on(self):
        self.state()
        r = self.land("--no-wait")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing merges it by itself", r.stdout)
        self.assertIn(f"gh pr merge {_PR} --rebase --delete-branch", r.stdout)
        self.assertFalse(self.merges("--auto"))

    def test_refused_close_and_disable_are_said(self):
        # Auto-merge on (every check passed), then main moves: GitHub
        # holds the merge, and refuses both clean-up calls.
        self.state(hold=True, move_after_auto=True,
                   refuse=["close", "disable-auto"])
        r = self.land()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("main moved on while the checks ran", r.stderr)
        self.assertNotIn("the pull request is closed", r.stderr)
        self.assertIn("refused both turning auto-merge off and closing the "
                      f"pull request: {_PR} is still open with auto-merge on",
                      r.stderr)
        self.assertIn(f"gh pr close {_PR} --delete-branch", r.stderr)
        st = self.stub_state()
        self.assertEqual((st["state"], st["auto"]), ("OPEN", True))

    def test_a_refused_close_alone_is_said(self):
        self.state(rules=False, jobs=[["tests", "fail"]], refuse=["close"])
        r = self.land()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Check(s) failed: tests", r.stderr)
        self.assertNotIn("the pull request is closed", r.stderr)
        self.assertIn(f"GitHub refused to close the pull request: {_PR} is "
                      "still open", r.stderr)


# ============================================== TJ-0944 check-public.sh:
# pull-request review bodies
class TestCheckPublicReviews(_PublicSandbox):
    """TJ-0944: the public privacy scan read issues, conversation
    comments, line (review) comments and commit comments, but not the
    text a pull-request review is submitted with: GitHub keeps it apart,
    one list per pull request. A denylisted name there was "clean"."""

    PR = "https://github.com/taxjson/taxjson/pull/5"

    def setUp(self):
        super().setUp()
        self.env["TAXJSON_SLUG"] = "taxjson/taxjson"

    def run_check(self, reviews, *args, **public):
        table = dict(_public_api(**public))
        table["repos/taxjson/taxjson/pulls/5/reviews?"] = reviews
        f = self.d / "api.json"
        f.write_text(json.dumps(table))
        self.env["GH_STUB_API"] = str(f)
        return subprocess.run(
            ["bash", str(_REPO / "scripts" / "check-public.sh"), *args],
            capture_output=True, text=True, env=self.env,
            stdin=subprocess.DEVNULL)

    def pr(self):
        return [{"html_url": self.PR, "number": 5, "title": "Fix a typo",
                 "body": "One word.", "pull_request": {"url": "x"}}]

    def test_a_review_body_is_scanned(self):
        r = self.run_check(
            [{"html_url": self.PR + "#pullrequestreview-1",
              "body": f"Looks good. {_PUB_NAME}", "state": "APPROVED"}],
            issues=self.pr())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("!! reviews of taxjson/taxjson:", r.stdout)
        self.assertIn(f"   in {self.PR}#pullrequestreview-1:", r.stdout)
        self.assertIn("private denylist match", r.stdout)
        self.assertNotIn(_PUB_NAME, r.stdout + r.stderr)
        self.assertIn("repos/taxjson/taxjson/pulls/5/reviews?per_page=100",
                      [c[2] for c in self.gh_calls() if c[:2] == ["api",
                                                                  "--paginate"]])

    def test_clean_reviews_are_counted(self):
        r = self.run_check(
            [{"html_url": self.PR + "#pullrequestreview-1", "body": "Thanks."},
             {"html_url": self.PR + "#pullrequestreview-2", "body": None}],
            issues=self.pr())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 commit-comments, 2 reviews", r.stdout)

    def test_an_issue_asks_for_no_reviews(self):
        r = self.run_check([], issues=[{"html_url": "https://github.com/"
                                        "taxjson/taxjson/issues/3",
                                        "number": 3, "title": "t",
                                        "body": "b"}])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse([c for c in self.gh_calls() if "reviews" in str(c)])
        self.assertIn("0 reviews", r.stdout)

    def test_unreadable_reviews_fail_closed(self):
        r = self.run_check({"__fail__": 1}, issues=self.pr())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("could not read the reviews of pull request 5", r.stderr)
        self.assertNotIn("clean", r.stdout)


if __name__ == "__main__":
    unittest.main()
