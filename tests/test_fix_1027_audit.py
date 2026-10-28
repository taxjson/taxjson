"""Backlog group `audit` (the tjs command audit of 2026-10-27): `tjs
audit` replays what `run` computed, and its JSON, text and exit status
agree.

TJ-0301  a filtered audit still sweeps for saved dispositions missing
         from, or added to, the engine's: both sides in the filter's
         scope.
TJ-0302  the merged --json total_gain leaves out the unknown-cost
         (manual) sales, as one book's total does.
TJ-0310  a US sale of known-cost and unknown-cost lots is two events:
         the computed part ties to the saved gain, the other to MANUAL
         REPORTING REQUIRED, whichever lot comes first.
TJ-0311  US crypto: an own-account move keeps its basis in the audit
         (the run's crypto blend).
TJ-0312  a foreign futures close's FX cross-check re-derives each
         account's settled P/L from its own fills, not one pooled book.
TJ-0313  US: a wash-sale basis add to a replacement sold in a filed
         (locked) year is booked in the loss's year, as the run books it.
TJ-0314  a pending election anywhere holds back every pooled group.
TJ-0315  --json's failed agrees with the exit status (an account not
         audited).
TJ-0332  the tie-out compares the filing fields (quantity, proceeds,
         cost, dates, term, each lot), not the gain alone.
TJ-0333  a buy's amount and a base-currency sale's amount are traced to
         their parsed rows; what is not traced is said.
TJ-0334  --summary and --no-trace compute no traces; the blocks share
         the pool history.
TJ-0335  the standalone audit's --default-rate help names no built-in
         1.35 fallback (there is none).

Synthetic books only: made-up tickers (ZZQ.TO, QZR.US ...), account
names (margin, acct_a ...), amounts under 1,000.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.bin import taxjson_audit as AUDIT
from taxjson.lib.core import TaxTransaction, get_tax_rules, load_transactions
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`taxjson -C root args...` from this tree's sources (width 0)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


def _project(root: Path, country: str, year: int, accounts: dict,
             books: dict) -> Path:
    """A project: {account: extra taxjson.toml lines} (each taxable) and
    {account: .tt text}."""
    cur = "USD" if country == "usa" else "CAD"
    toml = (f'[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n')
    for acct, extra in accounts.items():
        toml += f'\n[accounts.{acct}]\ntype = "taxable"\n{extra}'
    (root / "inputs").mkdir(parents=True)
    (root / "taxjson.toml").write_text(toml)
    for acct in accounts:
        (root / "inputs" / acct).mkdir()
    for acct, text in books.items():
        (root / "inputs" / acct / "trades.tt").write_text(text)
    return root


def _lock(root: Path, year: int, country: str) -> None:
    """A filed-year lock (filed/<year>.json) with no figures."""
    (root / "filed").mkdir(exist_ok=True)
    (root / "filed" / f"{year}.json").write_text(json.dumps({
        "schema_version": 1, "year": year, "country": country,
        "accounts": {}, "totals": {},
        "closed_at": f"{year + 1}-03-01T12:00:00"}))


def _quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    rc = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(*a, **kw)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def _audit(argv):
    """taxjson-audit in process: (rc, the --json document, stderr)."""
    rc, out, err = _quiet(AUDIT.main, list(argv) + ["--json"])
    return rc, (json.loads(out) if out.strip() else {}), err


class TestFilteredSweep(unittest.TestCase):

    def setUp(self):
        from test_audit_command import _Project
        self._td = tempfile.TemporaryDirectory()
        self.p = _Project(Path(self._td.name))
        self.saved = json.loads(self.p.gains.read_text())

    def tearDown(self):
        self._td.cleanup()

    def _json(self, *flags, expect):
        return json.loads(self.p.run([*flags, "--json"], expect=expect))

    def test_tj0301_missing_saved_sale_fails_every_filter(self):
        """TJ-0301: with the sale removed from the saved gains, the
        unfiltered audit failed but --symbol / --account / --date
        audits exited 0 with failed=false."""
        self.p.gains.write_text(json.dumps({"transactions": []}))
        for flags in ((), ("--symbol", "AAPL"), ("--account", "margin"),
                      ("--date", "2026-02-10"), ("--id",
                                                 self.saved["transactions"]
                                                 [0]["id"][:8])):
            d = self._json(*flags, expect=1)
            self.assertTrue(d["failed"], flags)
            self.assertTrue(any("is MISSING from the check file" in f
                                for f in d["reconciliation_failures"]),
                            (flags, d["reconciliation_failures"]))

    def test_tj0301_saved_record_outside_the_filter_is_not_counted(self):
        """The saved side is scoped like the engine side: an extra saved
        sale of another symbol fails the unfiltered audit and the
        audit of its own symbol, not one of AAPL."""
        extra = dict(self.saved["transactions"][0], id="zzqextra0001",
                     symbol="ZZQ.US")
        self.p.gains.write_text(json.dumps(
            {"transactions": self.saved["transactions"] + [extra]}))
        d = self._json(expect=1)
        self.assertTrue(any("zzqextra0001" in f
                            for f in d["reconciliation_failures"]))
        d = self._json("--symbol", "AAPL", expect=0)
        self.assertFalse(d["failed"])
        self.assertEqual(d["reconciliation_failures"], [])
        # Only the saved record matches: a finding, not "no match".
        d = self._json("--symbol", "ZZQ", expect=1)
        self.assertTrue(d["failed"])
        self.assertTrue(any("zzqextra0001" in f
                            for f in d["reconciliation_failures"]))


class TestMixedTaintSale(unittest.TestCase):

    @rule("US-BASIS-04")
    def test_tj0310_known_and_unknown_cost_lots_stay_apart(self):
        """TJ-0310: 10 QZR.US sold for 200, 5 of them from an opening
        with no cost (manual) and 5 bought for 50 (gain 50). The audit
        summed the lots and took the first lot's taint for the whole
        sale: a correct saved file failed, total 0 or 150."""
        from test_fix_a2_filrep_a import _Books, _row
        for opening_first in (True, False):
            od, bd = (("2025-01-02", "2025-02-03") if opening_first
                      else ("2025-02-03", "2025-01-02"))
            rows = [_row("OPENING_BALANCE", od, "QZR.US", 5, 0,
                         currency="USD"),
                    _row("BUYSELL", bd, "QZR.US", 5, 50, currency="USD"),
                    _row("BUYSELL", "2025-03-03", "QZR.US", -10, 200,
                         currency="USD")]
            with tempfile.TemporaryDirectory() as td:
                b = _Books(Path(td), rows, 2025, country="usa")
                self.assertEqual(len(b.results["manual_reporting_required"]),
                                 1)
                rc, d, err = b.audit("--country", "usa", "--year", "2025",
                                     "--check-year", "2025",
                                     "--source", str(b.base))
            self.assertEqual(rc, 0, (opening_first, d, err))
            self.assertFalse(d["failed"])
            self.assertEqual(d["reconciliation_failures"], [])
            self.assertAlmostEqual(d["total_gain"], 50.0, places=2)
            by_manual = {bool(e.get("manual_reporting")): e
                         for e in d["events"]}
            self.assertEqual(len(d["events"]), 2)
            clean, manual = by_manual[False], by_manual[True]
            self.assertEqual((clean["qty"], clean["gain"]), (5.0, 50.0))
            self.assertTrue(clean["tie_out"]["ties"])
            self.assertEqual((manual["qty"], manual["proceeds"]),
                             (5.0, 100.0))
            self.assertTrue(manual["tie_out"].get("manual"))
            self.assertEqual(manual["failures"], [])


class TestFuturesPerAccount(unittest.TestCase):

    @staticmethod
    def _fill(acct, day, q, net):
        return TaxTransaction(action="BUYSELL", date=day, date_settle=day,
                              symbol="F:ZZQZ6.US", quantity=q,
                              price=abs(net / q), net_amount=net,
                              currency="USD", account=acct)

    @rule("CA-FX-04")
    def test_tj0312_each_account_settles_its_own_fills(self):
        """TJ-0312: acct_a buys 1 at 100 and sells at 300, acct_b buys 1
        at 200 and sells at 300 (USD, rate 1.4). Each book settles to
        280 and 140 CAD; the audit settled both accounts as one book
        (150 USD each) and failed both FX cross-checks."""
        from decimal import Decimal
        from taxjson.bin.taxjson_convert_currency import process_transactions
        books = [[self._fill("acct_a", "2026-01-05", 1, 100),
                  self._fill("acct_a", "2026-03-05", -1, 300)],
                 [self._fill("acct_b", "2026-02-05", 1, 200),
                  self._fill("acct_b", "2026-04-06", -1, 300)]]
        rates = {"USD": {r.date: Decimal("1.4") for b in books for r in b}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            raw = [r.to_dict() for b in books for r in b]
            base = [r.to_dict() for b in books for r in process_transactions(
                b, "CAD", rates, None, country="canada")]
            (p / "source.json").write_text(json.dumps({"transactions": raw}))
            (p / "base.json").write_text(json.dumps({"transactions": base}))
            res = get_tax_rules("canada").compute_gains(
                load_transactions(p / "base.json"))
            (p / "check.json").write_text(json.dumps(
                {"transactions": res["transactions"]}, default=str))
            (p / "rates.csv").write_text("\n".join(
                f"{d} 12:00:00 USD CAD 1.4" for d in rates["USD"]))
            rc, doc, err = _audit([
                "--country", "canada", "--year", "2026",
                "--base", str(p / "base.json"),
                "--source", str(p / "source.json"),
                "--check", str(p / "check.json"),
                "--rates", str(p / "rates.csv"), "--base-currency", "CAD"])
        self.assertEqual(rc, 0, (doc.get("events"), err))
        self.assertFalse(doc["failed"])
        fx = {e["account"]: e["fx"] for e in doc["events"]}
        self.assertEqual((fx["acct_a"]["nominal_net"],
                          fx["acct_b"]["nominal_net"]), (200.0, 100.0))
        self.assertEqual((fx["acct_a"]["book_net"],
                          fx["acct_b"]["book_net"]), (280.0, 140.0))
        self.assertTrue(fx["acct_a"]["ties"] and fx["acct_b"]["ties"])


class TestMergedJson(unittest.TestCase):
    """One project, two books: eq (an unknown-cost sale, proceeds 200)
    and coin (a computed gain of 50)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _project(Path(cls._td.name) / "p", "canada", 2025,
                            {"eq": "", "coin": "crypto = true\n"}, {
            "eq": ("OPENING 2024-01-01 ZZS.TO 1 cost=unknown\n"
                   "BUYSELL 2025-03-03 10:00:00 ZZS.TO -1 CAD 200 200 0\n"),
            "coin": ("BUYSELL 2025-01-02 10:00:00 ZZC 1 CAD 100 100 0\n"
                     "BUYSELL 2025-03-03 10:00:00 ZZC -1 CAD 150 150 0\n")})
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-3000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule("CA-ACB-11")
    def test_tj0302_total_gain_leaves_out_the_manual_sale(self):
        """TJ-0302: the merged --json total was 250: the unknown-cost
        sale's 200 (reported by hand) plus the computed 50."""
        r = _cli(self.root, "audit", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        doc = json.loads(r.stdout)
        self.assertFalse(doc["failed"])
        self.assertAlmostEqual(doc["total_gain"], 50.0, places=2)
        self.assertEqual(doc["manual_reporting"],
                         {"events": 1, "qty": 1.0, "proceeds": 200.0})
        r = _cli(self.root, "audit", "--account", "eq", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual(doc["total_gain"], 0)
        self.assertEqual(doc["manual_reporting"]["proceeds"], 200.0)

    def test_tj0315_json_failed_agrees_with_the_exit_status(self):
        """TJ-0315: an account with inputs and no books: exit 1, but the
        JSON said failed=false with no reason."""
        import shutil
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "q"
            shutil.copytree(self.root, root)
            with (root / "taxjson.toml").open("a") as f:
                f.write('\n[accounts.new]\ntype = "taxable"\n')
            (root / "inputs" / "new").mkdir()
            (root / "inputs" / "new" / "trades.tt").write_text(
                "BUYSELL 2025-01-02 10:00:00 ZZN.TO 1 CAD 100 100 0\n")
            r = _cli(root, "audit", "--json")
            self.assertEqual(r.returncode, 1, r.stderr[-3000:])
            doc = json.loads(r.stdout)
            self.assertTrue(doc["failed"])
            self.assertEqual(doc["not_audited"],
                             [{"account": "new", "reason": "no_books"}])
            self.assertEqual(len(doc["coverage_failures"]), 1)
            self.assertIn("not audited — no books for new",
                          doc["coverage_failures"][0])
            self.assertIn("not audited — no books for new", r.stderr)
            # Scoped to an account that has books: nothing is missing.
            r = _cli(root, "audit", "--account", "coin", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            doc = json.loads(r.stdout)
            self.assertFalse(doc["failed"])
            self.assertEqual(doc["not_audited"], [])


_COINBASE_HEAD = (
    "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price "
    "Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or "
    "spread),Fees and/or Spread,Notes\n")


class TestUsCryptoMove(unittest.TestCase):

    @rule("US-CRYPTO-05")
    def test_tj0311_own_move_keeps_its_basis_in_the_audit(self):
        """TJ-0311: 1 coin bought for 100 in sender, moved to receiver,
        sold there for 110: the run's blended crypto pass carries the
        lot (gain 10); the audit ran receiver on its own (proceeds 110 at
        an unknown cost) and failed."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "usa", 2025,
                            {"sender": "crypto = true\n",
                             "receiver": "crypto = true\n"}, {})
            (root / "inputs" / "sender" / "coinbase.csv").write_text(
                _COINBASE_HEAD
                + "u1,2025-05-01 12:00:00 UTC,Buy,BTC,1,USD,100,100,100,0,"
                  "Bought 1 BTC\n"
                + "u2,2025-07-01 12:00:00 UTC,Send,BTC,1,USD,105,105,105,0,"
                  "Sent 1 BTC\n")
            (root / "inputs" / "receiver" / "coinbase.csv").write_text(
                _COINBASE_HEAD
                + "v1,2025-07-01 13:00:00 UTC,Receive,BTC,1,USD,105,105,105,"
                  "0,Received 1 BTC\n"
                + "v2,2025-07-10 12:00:00 UTC,Sell,BTC,1,USD,110,110,110,0,"
                  "Sold 1 BTC\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            saved = json.loads((root / "work" / "receiver_gains_wash.json")
                               .read_text())
            self.assertEqual([g["gain"] for g in saved["transactions"]
                              if g.get("qty")], [10])
            for args in (("audit", "--json"),
                         ("audit", "--account", "receiver", "--json")):
                r = _cli(root, *args)
                self.assertEqual(r.returncode, 0,
                                 (args, r.stdout[-3000:], r.stderr[-3000:]))
                doc = json.loads(r.stdout)
                self.assertFalse(doc["failed"], args)
                ev = [e for e in doc["events"]
                      if e["account"] == "receiver"]
                self.assertEqual([e["gain"] for e in ev], [10.0], args)
                self.assertFalse(ev[0].get("manual_reporting"), args)


class TestPendingElectionsEverywhere(unittest.TestCase):

    @rule("CA-SL-13")
    def test_tj0314_an_equity_election_holds_back_the_crypto_blend(self):
        """TJ-0314: margin waits on a spin-off election, so the run ran
        no cross-account pass: the two crypto accounts' saved gains are
        their own (ca: 200). The audit blended them (150) and failed;
        `--account ca` hid the pending election."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "canada", 2026,
                            {"margin": "", "ca": "crypto = true\n",
                             "cb": "crypto = true\n"}, {
                "ca": ("BUYSELL 2026-01-02 10:00:00 ZZC 1 CAD 100 100 0\n"
                       "BUYSELL 2026-03-03 10:00:00 ZZC -1 CAD 300 300 0\n"),
                "cb": "BUYSELL 2026-02-02 10:00:00 ZZC 1 CAD 200 200 0\n"})
            toml = (root / "taxjson.toml").read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]')
            (root / "taxjson.toml").write_text(toml)
            from datetime import date, timedelta
            (root / "work").mkdir()
            d, lines = date(2025, 12, 1), []
            while d <= date(2026, 12, 31):
                lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.40 boc\n")
                d += timedelta(days=1)
            (root / "work" / "to_base.csv").write_text("".join(lines))
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                "Transaction Date,Settlement Date,Action,Symbol,Description,"
                "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
                "Account #,Activity Type,Account Type\n"
                "2026-01-05,2026-01-06,Buy,ZZPA,ZZPARENT INC WE ACTED AS "
                "AGENT,5,20,-100,0,-100,USD,99900001,Trades,Individual "
                "margin\n"
                "2026-06-15,2026-06-15,DIS,ZZSP,ZZSPIN INC SPINOFF FROM SEC# "
                "X123456 ZZPARENT INC REC 06/01/26 PAY 06/15/26 ON 5 SHS,1,0,"
                "0,0,0,USD,99900001,Dividends,Individual margin\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr[-3000:])
            self.assertTrue((root / "work" /
                             "margin_pending_elections.json").exists())
            saved = json.loads((root / "work" / "ca_gains.json").read_text())
            self.assertEqual([g["gain"] for g in saved["transactions"]
                              if g.get("qty")], [200])
            for args in (("audit", "--summary"), ("audit", "--json"),
                         ("audit", "--account", "ca", "--json")):
                r = _cli(root, *args)
                self.assertEqual(r.returncode, 1, args)
                both = " ".join((r.stdout + r.stderr).split())
                self.assertIn("not audited — corp-action elections pending "
                              "for margin (so ca, cb too: no cross-account "
                              "pass)", both, args)
                for bad in ("MISMATCH", "TIE-OUT FAILED", "stale work/"):
                    self.assertNotIn(bad, both, args)


# margin's 2026-01-05 loss of 200 is a wash sale: other bought the
# replacement on 2025-12-15 and sold it on 2025-12-17, in filed 2025.
# The basis add is booked as its own row in 2026 (US-WASH-22).
_RETRO_BOOKS = {
    "margin": ("BUYSELL 2025-01-02 09:30:00 ZZQ.US 10 USD 50 500 0\n"
               "BUYSELL 2026-01-05 09:30:00 ZZQ.US -10 USD 30 300 0\n"),
    "other": ("BUYSELL 2025-12-15 09:30:00 ZZQ.US 10 USD 30 300 0\n"
              "BUYSELL 2025-12-17 09:30:00 ZZQ.US -10 USD 31 310 0\n")}


class TestLockedYearRetroWash(unittest.TestCase):

    @staticmethod
    def _tx(acct, day, q, net):
        return TaxTransaction(action="BUYSELL", date=day, date_settle=day,
                              symbol="ZZQ.US", quantity=q,
                              price=abs(net / q), net_amount=net,
                              currency="USD", account=acct)

    @rule("US-WASH-22")
    def test_tj0313_standalone_books_the_add_in_the_loss_year(self):
        """TJ-0313: saved 2026 gain -200 (the add row), audit gain 0 and
        the add row called fabricated."""
        from taxjson.lib.pipeline import GainsRequest, run_gains
        rows = [self._tx("margin", "2025-01-02", 10, 500),
                self._tx("other", "2025-12-15", 10, 300),
                self._tx("other", "2025-12-17", -10, 310),
                self._tx("margin", "2026-01-05", -10, 300)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "base.json").write_text(json.dumps(
                {"transactions": [r.to_dict() for r in rows]}))
            with contextlib.redirect_stderr(io.StringIO()):
                res = run_gains(rows, req=GainsRequest(
                    country="usa", taxable=True, year=2026,
                    locked_years=(2025,)))
            self.assertAlmostEqual(res["summary"]["total_gain"], -200.0)
            (p / "check.json").write_text(json.dumps(res, default=str))
            argv = ["--country", "usa", "--year", "2026",
                    "--base", str(p / "base.json"),
                    "--source", str(p / "base.json"),
                    "--check", str(p / "check.json"),
                    "--base-currency", "USD", "--per-account-basis"]
            rc, doc, err = _audit(argv + ["--locked-year", "2025"])
            self.assertEqual(rc, 0, (doc, err))
            self.assertFalse(doc["failed"])
            self.assertAlmostEqual(doc["total_gain"], -200.0, places=2)
            add = [e for e in doc["events"]
                   if e["id"].endswith("-wash-basis")]
            self.assertEqual(len(add), 1)
            self.assertTrue(add[0]["tie_out"]["ties"])
            self.assertEqual(add[0]["warnings"], [])
            self.assertTrue(add[0]["sources"])
            self.assertNotIn("ATTENTION", err)
            _rc, out, _e = _quiet(AUDIT.main,
                                  argv + ["--locked-year", "2025"])
            self.assertIn("WASH-SALE BASIS ADD", " ".join(out.split()))
            # Without the lock the books differ: the audit says so.
            rc, doc, _err = _audit(argv)
            self.assertEqual(rc, 1)

    @rule("US-WASH-22")
    def test_tj0313_project_audit_passes_the_locks(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "usa", 2026,
                            {"margin": "", "other": ""}, _RETRO_BOOKS)
            _lock(root, 2025, "usa")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = _cli(root, "audit", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            doc = json.loads(r.stdout)
            self.assertFalse(doc["failed"])
            self.assertTrue(any(e["id"].endswith("-wash-basis")
                                for e in doc["events"]))
            self.assertAlmostEqual(doc["total_gain"], -200.0, places=2)


class TestTraceOnDemand(unittest.TestCase):
    """TJ-0334: one pool of 61 units sold one at a time. Every event kept
    a copy of the pool's whole history (20500 lines for 200 sales,
    322000 for 800), with --summary and --no-trace too."""

    N = 60

    def _book(self, tmp: Path) -> Path:
        from datetime import datetime, timedelta
        rows = []
        for i in range(self.N + 1):
            when = datetime(2025, 1, 2, 10) + timedelta(seconds=i)
            rows.append(TaxTransaction(
                action="BUYSELL", date=when.date().isoformat(),
                time=when.time().isoformat(), date_settle="2025-01-03",
                symbol="ZZQ.TO", account="margin",
                quantity=self.N + 1 if i == 0 else -1, currency="CAD",
                price=1 if i == 0 else 2,
                net_amount=self.N + 1 if i == 0 else 2).to_dict())
        book = tmp / "book.json"
        book.write_text(json.dumps({"transactions": rows}))
        return book

    def _events(self, *flags):
        from unittest.mock import patch
        seen = []
        real = AUDIT.build_event

        def spy(*a, **kw):
            ev = real(*a, **kw)
            seen.append(ev)
            return ev
        with tempfile.TemporaryDirectory() as td:
            book = self._book(Path(td))
            with patch.object(AUDIT, "build_event", spy):
                rc, out, err = _quiet(AUDIT.main, [
                    "--country", "canada", "--year", "2025",
                    "--base", str(book), "--source", str(book),
                    "--no-wash", *flags])
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(seen), self.N)
        return seen, out

    @rule("CA-ACB-01")
    def test_tj0334_summary_and_no_trace_keep_no_trace(self):
        for flags in (("--summary",), ("--no-trace",),
                      ("--json", "--no-trace")):
            seen, _out = self._events(*flags)
            self.assertEqual(sum(len(e["trace"]) for e in seen), 0, flags)

    @rule("CA-ACB-01")
    def test_tj0334_blocks_share_the_pool_history(self):
        from taxjson.lib.trace_format import SharedTrace
        seen, out = self._events()
        traces = [e["trace"] for e in seen]
        self.assertTrue(all(isinstance(t, SharedTrace) for t in traces))
        self.assertEqual(len({id(t.base) for t in traces}), 1)
        # Still the whole history in each block (the last sale's
        # block lists every earlier row of the pool).
        self.assertGreater(len(traces[-1]), self.N)
        self.assertIn("Pool history (ACB trace)", out)
        seen, out = self._events("--json")
        doc = json.loads(out)
        self.assertEqual([len(e["trace"]) for e in doc["events"]],
                         [len(t) for t in traces])


def _sales(doc):
    return [r for r in doc["transactions"] if "gain" in r and r.get("qty")]


class TestTieOutFields(unittest.TestCase):
    """TJ-0332: the tie-out compared the gain and the disallowed amount
    only, so a saved gains file with another term, quantity or date, or
    with proceeds and cost moved alike, tied at exit 0."""

    def setUp(self):
        from test_audit_command import _Project
        self._td = tempfile.TemporaryDirectory()
        self.p = _Project(Path(self._td.name))
        self.saved = json.loads(self.p.gains.read_text())

    def tearDown(self):
        self._td.cleanup()

    def _with_saved(self, change):
        doc = json.loads(json.dumps(self.saved))
        for r in _sales(doc):
            change(r)
        self.p.gains.write_text(json.dumps(doc))
        return json.loads(self.p.run(["--json"], expect=1))

    @rule("CA-ACB-01")
    def test_tj0332_saved_fields_are_compared(self):
        def shift(r):
            r["proceeds"] += 20
            r["cost"] += 20

        cases = {("proceeds", "cost"): shift,
                 ("qty",): lambda r: r.update(qty=r["qty"] + 1),
                 ("date_settle",): lambda r: r.update(
                     date_settle="2026-02-12"),
                 ("account",): lambda r: r.update(account="other")}
        for fields, change in cases.items():
            with self.subTest(fields=fields):
                d = self._with_saved(change)
                self.assertTrue(d["failed"])
                ev = d["events"][0]
                self.assertIs(ev["tie_out"]["ties"], False)
                self.assertEqual(
                    tuple(m["field"] for m in ev["tie_out"]["mismatched"]),
                    fields)
                self.assertTrue(any(f.startswith("PIPELINE TIE-OUT FAILED")
                                    for f in ev["failures"]))

    def test_tj0332_the_scope_is_said(self):
        d = json.loads(self.p.run(["--json"]))
        self.assertFalse(d["failed"])
        self.assertIn("term", d["scope"]["tie_out"])
        self.assertTrue(d["scope"]["not_checked"])
        text = self.p.run()
        self.assertIn("  not traced ", text)
        self.assertNotIn("The tie-out compares", self.p.run(["--brief"]))
        self.assertIn("The tie-out compares", " ".join(text.split()))


class TestUsLotTieOut(unittest.TestCase):
    """TJ-0332, US: 10 QZR.US sold for 200 on 2025-03-03 from 5 bought
    on 2024-01-02 for 50 (long-term) and 5 on 2025-01-03 for 60
    (short-term)."""

    def _books(self, tmp):
        from test_fix_a2_filrep_a import _Books, _row
        rows = [_row("BUYSELL", "2024-01-02", "QZR.US", 5, 50,
                     currency="USD"),
                _row("BUYSELL", "2025-01-03", "QZR.US", 5, 60,
                     currency="USD"),
                _row("BUYSELL", "2025-03-03", "QZR.US", -10, 200,
                     currency="USD")]
        return _Books(tmp, rows, 2025, country="usa")

    def _audit_with(self, change):
        with tempfile.TemporaryDirectory() as td:
            b = self._books(Path(td))
            doc = json.loads(b.gains.read_text())
            lots = sorted(_sales(doc), key=lambda r: r["acquired_date"])
            self.assertEqual([r["term"] for r in lots],
                             ["LONG_TERM", "SHORT_TERM"])
            change(lots)
            b.gains.write_text(json.dumps(doc))
            return b.audit("--country", "usa", "--year", "2025",
                           "--check-year", "2025", "--source", str(b.base))

    @rule("US-HOLD-01")
    def test_tj0332_term_and_lot_shares_are_compared(self):
        rc, d, err = self._audit_with(lambda lots: None)
        self.assertEqual(rc, 0, (d, err))
        self.assertEqual(d["events"][0]["term"], "MIXED")

        def relabel(lots):
            lots[1]["term"] = "LONG_TERM"

        def move_cost(lots):
            # The sale's totals unchanged; 10 of cost moved between lots.
            for r, dc in ((lots[0], 10), (lots[1], -10)):
                r["cost"] += dc
                r["gain"] -= dc
                r["raw_gain"] -= dc

        for change, want in ((relabel, {"term"}),
                             (move_cost, {"cost", "gain"})):
            with self.subTest(change=change.__name__):
                rc, d, err = self._audit_with(change)
                self.assertEqual(rc, 1, err)
                ev = d["events"][0]
                got = {m["field"] for m in ev["tie_out"]["mismatched"]}
                self.assertEqual(got, want)
                if change is move_cost:
                    self.assertTrue(all(m.get("lot") for m in
                                        ev["tie_out"]["mismatched"]))
                    self.assertIn("(lot 2024-01-02 LONG_TERM)",
                                  ev["failures"][0])

    @rule("US-BASIS-04")
    def test_tj0332_manual_row_is_compared(self):
        """The unknown-cost part's MANUAL REPORTING REQUIRED row: its
        quantity and proceeds."""
        from test_fix_a2_filrep_a import _Books, _row
        rows = [_row("OPENING_BALANCE", "2025-01-02", "QZR.US", 5, 0,
                     currency="USD"),
                _row("BUYSELL", "2025-03-03", "QZR.US", -5, 100,
                     currency="USD")]
        with tempfile.TemporaryDirectory() as td:
            b = _Books(Path(td), rows, 2025, country="usa")
            doc = json.loads(b.gains.read_text())
            doc["manual_reporting_required"][0]["proceeds"] += 20
            b.gains.write_text(json.dumps(doc))
            rc, d, err = b.audit("--country", "usa", "--year", "2025",
                                 "--check-year", "2025",
                                 "--source", str(b.base))
        self.assertEqual(rc, 1, err)
        ev = d["events"][0]
        self.assertTrue(ev["manual_reporting"])
        self.assertIs(ev["tie_out"]["ties"], False)
        self.assertEqual([m["field"] for m in ev["tie_out"]["mismatched"]],
                         ["proceeds"])
        self.assertTrue(ev["failures"][0].startswith(
            "MANUAL REPORTING TIE-OUT FAILED"))


class TestSourceAmounts(unittest.TestCase):
    """TJ-0333: the parsed rows kept, the base book changed and the
    saved gains made from it: a buy's amount or conversion, or a sale's
    amount in the base currency, passed (a base-currency row was marked
    "native" with no amount compared; buys were not traced at all)."""

    def _audit(self, *, foreign=False, change=None, drop_source=None):
        rate = 1.4
        cur = "USD" if foreign else "CAD"

        def tx(q, price, day):
            return TaxTransaction(
                action="BUYSELL", date=day, date_settle=day,
                symbol="ZZQ.US" if foreign else "ZZQ.TO", quantity=q,
                price=price, net_amount=abs(q) * price, currency=cur,
                account="margin").to_dict()
        rows = [tx(10, 10, "2026-01-05"), tx(-10, 15, "2026-02-10")]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            src = [r for i, r in enumerate(rows) if i != drop_source]
            (p / "source.json").write_text(json.dumps(
                {"transactions": src}))
            books = [dict(r) for r in rows]
            if foreign:
                for r in books:
                    for f in ("price", "net_amount"):
                        r[f] = float(r[f]) * rate
                    r["currency"] = "CAD"
            if change:
                change(books)
            (p / "base.json").write_text(json.dumps({"transactions": books}))
            saved = get_tax_rules("canada").compute_gains(
                load_transactions(p / "base.json"))
            (p / "check.json").write_text(json.dumps(
                {"transactions": saved["transactions"]}, default=str))
            argv = ["--country", "canada", "--year", "2026",
                    "--base", str(p / "base.json"),
                    "--source", str(p / "source.json"),
                    "--check", str(p / "check.json"),
                    "--base-currency", "CAD"]
            if foreign:
                (p / "rates.csv").write_text(
                    f"2026-01-05 12:00:00 USD CAD {rate}\n"
                    f"2026-02-10 12:00:00 USD CAD {rate}\n")
                argv += ["--rates", str(p / "rates.csv")]
            return _audit(argv)

    @staticmethod
    def _buy_plus_20(books):
        books[0]["net_amount"] += 20
        books[0]["price"] += 2

    @rule("CA-FX-01")
    def test_tj0333_buy_amounts_are_traced(self):
        for foreign in (False, True):
            with self.subTest(foreign=foreign):
                rc, d, err = self._audit(foreign=foreign)
                self.assertEqual(rc, 0, (d, err))
                self.assertEqual(
                    {k: d["pool_trades"][k]
                     for k in ("checked", "tied", "untraced")},
                    {"checked": 1, "tied": 1, "untraced": 0})
                rc, d, err = self._audit(foreign=foreign,
                                         change=self._buy_plus_20)
                self.assertEqual(rc, 1, err)
                self.assertTrue(d["failed"])
                [m] = d["pool_trades"]["mismatched"]
                self.assertEqual(m["date"], "2026-01-05")
                self.assertTrue(m["failure"].startswith(
                    "FX cross-check FAILED" if foreign
                    else "AMOUNT cross-check FAILED"), m)
                self.assertTrue(any(f.startswith("pool trade ")
                                    for f in d["reconciliation_failures"]))

    @rule("CA-ACB-01")
    def test_tj0333_base_currency_sale_amount_is_compared(self):
        rc, d, err = self._audit()
        fx = d["events"][0]["fx"]
        self.assertTrue(fx["native"])
        self.assertTrue(fx["ties"])
        self.assertEqual((fx["nominal_net"], fx["book_net"]), (150.0, 150.0))

        def sale_170(books):
            books[1].update(net_amount=170.0, price=17.0)
        rc, d, err = self._audit(change=sale_170)
        self.assertEqual(rc, 1, err)
        ev = d["events"][0]
        self.assertFalse(ev["fx"]["ties"])
        self.assertIn("AMOUNT cross-check FAILED: the parsed row carries "
                      "150.00 CAD but the base book row carries 170.00 CAD",
                      ev["failures"][0])

    def test_tj0333_a_buy_with_no_parsed_row_is_counted(self):
        """A row the run made (no parsed row) is counted, not failed:
        the audit says it re-ran it without tracing it."""
        rc, d, err = self._audit(drop_source=0)
        self.assertEqual(rc, 0, (d, err))
        self.assertEqual(d["pool_trades"]["untraced"], 1)
        self.assertEqual(d["pool_trades"]["checked"], 0)
        self.assertTrue(any("no parsed broker row" in s
                            for s in d["scope"]["not_checked"]))

    @rule("CA-FX-01")
    def test_a_swap_leg_priced_by_the_fill_stage_ties(self):
        """Found in passing: fill-crypto-prices values both legs of a
        crypto swap at one figure (the received coin's value), so a leg
        parsed at 100 USD is converted from 105 USD. The audit multiplied
        the parsed 100 and failed correct books ("FX cross-check FAILED:
        100.00 USD x 1.4 = 140.00 but the base book row carries 147.00
        CAD")."""
        from decimal import Decimal
        gid = "swap0001"
        g = {"id": gid, "symbol": "QTK", "date": "2026-05-18",
             "date_settle": "2026-05-18", "qty": 1.0, "gain": 5.0,
             "account": "crypto"}
        raw = {"id": gid, "currency": "USD", "net_amount": 100.0,
               "date": "2026-05-18"}
        priced = {"id": gid, "currency": "USD", "net_amount": 105.0,
                  "date": "2026-05-18"}
        ev = AUDIT.build_event(
            g, {gid: {"id": gid, "net_amount": 147.0, "currency": "CAD"}},
            {gid: [{"label": "kraken", "row": raw}]}, check_index={},
            fx_history={"USD": {"2026-05-18": Decimal("1.4")}},
            default_rate=None, base_currency="CAD", tmap=None,
            replacement_lookup={}, checks_supplied=False,
            filled_index={gid: priced})
        self.assertEqual(ev["failures"], [])
        self.assertTrue(ev["fx"]["ties"])
        self.assertTrue(ev["fx"]["priced_by_fill"])
        self.assertEqual(ev["fx"]["nominal_net"], 105.0)


class TestDefaultRateHelp(unittest.TestCase):

    def test_tj0335_help_names_no_built_in_rate(self):
        """TJ-0335: the help said "default: 1.35 for USD->CAD" while the
        option defaults to None and a row with no rate fails."""
        rc, out, _err = _quiet(AUDIT.parse_args, ["--help"])
        self.assertEqual(rc, 0)
        flat = " ".join(out.split())
        self.assertNotIn("1.35", flat)
        self.assertIn("there is no built-in rate", flat)
        args = AUDIT.parse_args(["--country", "canada", "--base", "b.json"])
        self.assertIsNone(args.default_rate)


if __name__ == "__main__":
    unittest.main()
