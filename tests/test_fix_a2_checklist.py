"""Re-audit-2 fixes: the filing checklist (lib/checklist.py) and the run
state it judges (run-clean's input fingerprint, --strict, locks)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl
from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
           NO_COLOR="1")

TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')
BOOK = ("BUYSELL 2025-02-03 10:00:00 XYZ.TO 100.00000000 CAD 10.00000000 1000.00000 0.00000\n"
        "BUYSELL 2025-05-05 10:00:00 XYZ.TO -100.00000000 CAD 12.00000000 1200.00000 0.00000\n")


def tj(root, *args, check=True):
    r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                        "-C", str(root), *args], capture_output=True,
                       text=True, env=ENV, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise AssertionError(f"{args}: rc {r.returncode}\n{r.stderr[-3000:]}")
    return r


def make_project(root, toml=TOML, book=BOOK, run=True):
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin" / "book.tt").write_text(book)
    (root / "taxjson.toml").write_text(toml)
    if run:
        tj(root, "run", "--no-input")
    return root


def ctx(root, table=None, today=date(2026, 9, 23), year=2025):
    from taxjson.lib.tomlcompat import tomllib
    cfg = tomllib.loads((root / "taxjson.toml").read_text())
    table = table or {}
    return cl.Ctx(root=root, cfg=cfg, year=year, today=today,
                  run_sub=lambda argv, timeout=900: table.get(argv[0], (0, "", "")))


class _Built(unittest.TestCase):
    """One full run, copied per test (a run takes seconds)."""
    tmp = None
    built = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.built = make_project(Path(cls.tmp.name) / "built")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy(self):
        d = tempfile.mkdtemp(dir=self.tmp.name)
        dst = Path(d) / "p"
        shutil.copytree(self.built, dst, symlinks=True)
        return dst


class TestFingerprintCoversEveryRunInput(_Built):
    """A2-0124, A2-0126, A2-0358, A2-0363, A2-1158: the elections
    manifest, sends.json and crypto_ticker.map are run inputs."""

    def test_clean_after_run(self):
        p = self.copy()
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")

    def test_manifest_change_is_stale(self):
        p = self.copy()
        man = p / "inputs" / "margin" / "manifest.json"
        man.write_text(json.dumps({"elections": {"e1": {"choice": "taxable_disposition"}}}))
        r = cl.d_run_clean(ctx(p))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("inputs/margin/manifest.json", r.detail)
        from taxjson.web.context import ProjectContext
        from taxjson.web.data import freshness
        fr = freshness(ProjectContext.load(p))
        self.assertTrue(fr["stale"], fr)

    def test_empty_manifest_is_not_an_input_change(self):
        p = self.copy()
        (p / "inputs" / "margin" / "manifest.json").write_text('{"elections": {}}')
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")

    def test_sends_json_and_crypto_ticker_map(self):
        for rel in ("inputs/margin/sends.json", "crypto_ticker.map"):
            p = self.copy()
            (p / rel).write_text("{}" if rel.endswith(".json") else "FOO FOO123\n")
            r = cl.d_run_clean(ctx(p))
            self.assertEqual(r.status, "attention", rel)
            self.assertIn(rel, r.detail)

    def test_root_maps_match_the_run(self):
        from taxjson.bin import taxjson_run as R
        self.assertEqual(set(cl.PROJECT_ROOT_MAPS), set(R._PROJECT_ROOT_INPUTS))
        self.assertEqual(set(cl.SPREADSHEET_SUFFIXES), set(R.SPREADSHEET_SUFFIXES))


class TestPlanningEditsKeepBooksCurrent(_Built):
    """A2-0681, A2-1157: taxjson.toml content no run stage reads (a
    comment, [instalments], [estimate], province, prior_year_record,
    holdings, fetch keys) does not make the books stale."""

    def test_planning_only_edits(self):
        edits = [
            "# a comment\n",
            '[instalments]\nbasis = "current_year"\npaid = [{date = "2025-03-17", amount = 5000}]\n',
            "[estimate]\nother_income = 50000\n",
        ]
        for extra in edits:
            p = self.copy()
            with (p / "taxjson.toml").open("a") as f:
                f.write(extra)
            self.assertEqual(cl.d_run_clean(ctx(p)).status, "done", extra)
        p = self.copy()
        t = (p / "taxjson.toml").read_text().replace(
            'option_grant_timing_since = 2025\n',
            'option_grant_timing_since = 2025\nprovince = "BC"\n'
            'prior_year_record = "../2024/filed/2024.json"\n').replace(
            'type = "taxable"\n', 'type = "taxable"\nholdings = "h.toml"\n'
            'brokerage = "questrade"\n')
        (p / "taxjson.toml").write_text(t)
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")
        # close-year no longer refuses on it
        r = tj(p, "close-year", check=False)
        self.assertNotIn("not the clean result", r.stderr)

    def test_a_run_setting_is_stale(self):
        p = self.copy()
        t = (p / "taxjson.toml").read_text().replace(
            'base_currency = "CAD"\n', 'base_currency = "CAD"\ntax_date = "trade"\n')
        (p / "taxjson.toml").write_text(t)
        r = cl.d_run_clean(ctx(p))
        self.assertEqual(r.status, "attention")
        self.assertIn("taxjson.toml", r.detail)


class TestFingerprintRecordEdges(_Built):
    def test_damaged_record_is_attention(self):
        """A2-1155: a truncated or malformed record never reads as 'no
        record' (the mtime fallback called stale books clean)."""
        for bad in ('{"files": [1, 2]}', '{"files": {"a": '):
            p = self.copy()
            (p / "work" / cl.FINGERPRINT_FILE).write_text(bad)
            r = cl.d_run_clean(ctx(p))
            self.assertEqual(r.status, "attention", bad)
            self.assertIn("cannot be read", r.detail)

    def test_legacy_record_still_judged(self):
        """A record from an older taxjson (no version) is compared the
        old way: an upgrade does not mark every project stale."""
        p = self.copy()
        f = p / "work" / cl.FINGERPRINT_FILE
        f.write_text(json.dumps({"files": cl._legacy_input_fingerprint(p, ctx(p).cfg)}))
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")
        (p / "inputs" / "margin" / "book.tt").write_text(BOOK + "\n")
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "attention")

    def test_dangling_sum_symlink(self):
        """A2-1148: the step reports the unreadable report, it does not crash."""
        p = self.copy()
        (p / "reports" / "zz.sum").symlink_to(p / "reports" / "gone.sum")
        r = cl.d_run_clean(ctx(p))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("cannot read reports/zz.sum", r.detail)


class TestHiddenAndLockFiles(_Built):
    """A2-1145, A2-1166: run and the checklist agree on the input set —
    hidden files and Excel '~$' lock files are neither read nor
    fingerprinted."""

    def test_skipped_everywhere(self):
        from taxjson.bin.taxjson_run import input_files
        p = self.copy()
        acct = p / "inputs" / "margin"
        (acct / ".hidden.tt").write_text(
            "BUYSELL 2025-06-03 10:00:00 ABC.TO 1.00000000 CAD 1.00000000 1.00000 0.00000\n")
        (acct / "~$book.csv").write_bytes(b"\x00lock")
        self.assertEqual([x.name for x in input_files(acct, ".tt")], ["book.tt"])
        self.assertEqual(input_files(acct, ".csv"), [])
        self.assertEqual([x.name for x in cl._data_files(acct)], ["book.tt"])
        self.assertEqual(cl.d_run_clean(ctx(p)).status, "done")
        (p / "inputs" / "slips").mkdir()
        (p / "inputs" / "slips" / "~$t5008.csv").write_bytes(b"\x00")
        self.assertEqual(cl.slip_files(p), [])
        self.assertEqual(cl._unread_slip_files(p), [])

    def test_numbers_spreadsheet(self):
        """A2-1156: an Apple Numbers export is refused by run and
        flagged by the checklist, like .xlsx."""
        p = self.copy()
        (p / "inputs" / "margin" / "Activity_2025.numbers").write_bytes(b"PK\x03\x04")
        r = tj(p, "run", "--no-input", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Activity_2025.numbers", r.stderr)
        rc = cl.d_run_clean(ctx(p))
        self.assertEqual(rc.status, "attention")
        self.assertIn("unread spreadsheet", rc.detail)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- run state
class TestRunCleanSeesWhatStrictRefuses(_Built):
    def test_unbooked_lines_in_a_report(self):
        """A2-0357: an UNBOOKED parser row in a .sum keeps run-clean open."""
        p = self.copy()
        s = p / "reports" / "margin.sum"
        s.write_text("warning: UNBOOKED: Kraken ledger x.csv: 1 row(s) ...\n" + s.read_text())
        r = cl.d_run_clean(ctx(p))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("1 UNBOOKED event(s) in reports/margin.sum", r.detail)

    def test_renamed_account_orphans(self):
        """A2-1165: work/ books of an account the config no longer has are
        counted twice — run --strict refuses, run-clean says so."""
        p = self.copy()
        (p / "taxjson.toml").write_text(TOML.replace("[accounts.margin]", "[accounts.cash]"))
        shutil.move(str(p / "inputs" / "margin"), str(p / "inputs" / "cash"))
        r = tj(p, "run", "--no-input", "--strict", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not in taxjson.toml: margin", r.stderr)
        tj(p, "run", "--no-input")
        res = cl.d_run_clean(ctx(p))
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("not in taxjson.toml: margin", res.detail)


KR_SEND = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
           "LA1AAA,RA1,2025-01-05 12:00:00,deposit,,currency,SOL,spot,100,0,100\n"
           "LB1BBB,RB1,2025-03-04 12:00:00,withdrawal,,currency,SOL,spot,-50,0,50\n")


class TestCryptoSendsGates(unittest.TestCase):
    """A2-0127, A2-0362, A2-1151: a crypto send the run could not book, an
    undecided one, or one booked twice is not a clean run."""

    def setUp(self):
        from test_fix_sends import _rates_file
        self._td = tempfile.TemporaryDirectory()
        td = Path(self._td.name)
        self.root = td / "proj"
        acct = self.root / "inputs" / "crypto"
        acct.mkdir(parents=True)
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
        (acct / "kr_ledgers.csv").write_text(KR_SEND)
        (self.root / "work").mkdir()
        _rates_file(self.root / "work" / "to_base.csv")
        self.home = td / "home"
        self.home.mkdir()
        (self.home / ".crypto_price_cache.json").write_text("{}")

    def tearDown(self):
        self._td.cleanup()

    def cli(self, *a):
        from test_fix_sends import _cli
        return _cli(self.root, self.home, *a)

    def sid(self):
        r = self.cli("crypto-sends", "crypto", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["accounts"]["crypto"]["sends"][0]["id"]

    def cl_ctx(self):
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        return cl.Ctx(root=self.root, cfg=cfg, year=2025, today=date(2026, 9, 1),
                      run_sub=lambda argv, timeout=900: (0, "", ""))

    def test_undecided_and_unpriced(self):
        r = self.cli("run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        r = self.cli("run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not yet classified", r.stderr)
        sid = self.sid()
        self.assertEqual(self.cli("crypto-sends", "crypto", "--set", f"{sid}=gift").returncode, 0)
        r = self.cli("run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        diag = (self.root / "work" / "crypto_crypto_sends.diag").read_text()
        self.assertTrue(diag.startswith("warning: UNBOOKED: crypto: crypto sends: no fair value"), diag)
        res = cl.d_run_clean(self.cl_ctx())
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("UNBOOKED", res.detail)
        r = self.cli("run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no fair value", r.stderr)
        # Priced by hand: booked, the diag is gone, strict passes.
        self.assertEqual(self.cli("crypto-sends", "crypto", "--set", f"{sid}=gift",
                                  "--price", "100").returncode, 0)
        r = self.cli("run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertFalse((self.root / "work" / "crypto_crypto_sends.diag").exists())
        self.assertEqual(cl.d_run_clean(self.cl_ctx()).status, "done")
        self.assertEqual(cl.d_crypto_sends(self.cl_ctx()).status, "done")

        # A2-1151: a new price saved without --write/run: the .tt is stale.
        self.assertEqual(self.cli("crypto-sends", "crypto", "--set", f"{sid}=gift",
                                  "--price", "250").returncode, 0)
        res = cl.d_crypto_sends(self.cl_ctx())
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("out of date", res.detail)
        self.assertEqual(cl.d_run_clean(self.cl_ctx()).status, "attention")

        # A2-0127: a hand-written line selling the same send.
        self.assertEqual(self.cli("run", "--no-input").returncode, 0)
        tt = (self.root / "inputs" / "crypto" / "crypto_sends.tt").read_text()
        line = [ln for ln in tt.splitlines() if ln.startswith("BUYSELL")][0]
        (self.root / "inputs" / "crypto" / "gift_by_hand.tt").write_text(line + "\n")
        res = cl.d_crypto_sends(self.cl_ctx())
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("counted twice", res.detail)
        r = self.cli("run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("counted twice", r.stderr)
        r = self.cli("run", "--no-input")
        self.assertEqual(r.returncode, 0)
        self.assertIn("counted twice", (self.root / "work" / "crypto_crypto_sends.diag").read_text())


# --------------------------------------------------------------- inputs-frozen
def _frozen_project(root, margin_to, crypto_to):
    (root / 'taxjson.toml').write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
    for a in ('margin', 'crypto'):
        (root / 'inputs' / a).mkdir(parents=True)
    (root / 'inputs' / 'crypto' / 'cb_x.csv').write_text('x\n')
    (root / 'work').mkdir()
    for a, to in (('margin', margin_to), ('crypto', crypto_to)):
        rows = [{'action': 'BUYSELL', 'date': to, 'date_settle': to, 'symbol': 'X.TO',
                 'quantity': 1, 'account': a}]
        (root / 'work' / f'{a}_base.json').write_text(json.dumps({'transactions': rows}))


_RBC_HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
            '"Price","Settlement Date","Account","Value","Currency","Description"\n')


def _rbc(as_of, acct, d):
    c = [d, 'Buy', 'QZF', 'QZ FUND UNITS', '100', '10', d, acct, '-1000', 'CAD', 'QZ FUND UNITS']
    return f'"Activity Export as of {as_of}"\n\n' + _RBC_HDR + ','.join('"%s"' % x for x in c) + '\n'


class TestInputsFrozenPerStatement(unittest.TestCase):
    T = date(2026, 3, 1)

    def test_rbc_per_account(self):
        """A2-1147 (a): another RBC account's later export in the same
        folder does not certify this account's early one."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _frozen_project(root, '2025-12-02', '2026-02-02')
            (root / 'inputs' / 'margin' / 'rbc_a.csv').write_text(
                _rbc('Dec 15, 2025', '55500001', 'December 1, 2025'))  # pii-ok
            (root / 'inputs' / 'margin' / 'rbc_b.csv').write_text(
                _rbc('Feb 2, 2026', '55500002', 'December 2, 2025'))  # pii-ok
            r = cl.d_inputs_frozen(ctx(root, today=self.T))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("of account 55***", r.detail)
        self.assertIn("2025-12-15", r.detail)

    def test_ib_statement_period(self):
        """A2-0125, A2-1147 (b): an IB statement ending Dec 15 is not
        certified by another source's later rows (fixed by A2-0262)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _frozen_project(root, '2025-12-10', '2026-02-02')
            (root / 'inputs' / 'margin' / 'ib.csv').write_text(
                'Statement,Header,Field Name,Field Value\n'
                'Statement,Data,Title,Activity Statement\n'
                'Statement,Data,Period,"January 1, 2025 - December 15, 2025"\n')
            r = cl.d_inputs_frozen(ctx(root, today=self.T))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("IB statements", r.detail)


# ------------------------------------------------------------------- locks
class TestLocks(unittest.TestCase):
    def test_partial_year_lock_is_not_a_filed_return(self):
        """A2-0679, A2-1164: a lock taken before the year ended
        (close-year --force) is named as a snapshot by filed-lock,
        lock-committed, check-filed and option-boundary."""
        y = date.today().year                     # always still open
        book = BOOK.replace("2025-", f"{y}-")
        with tempfile.TemporaryDirectory() as td:
            p = make_project(Path(td), toml=TOML.replace("2025", str(y)), book=book)
            r = tj(p, "close-year", "--force")
            c = ctx(p, today=date.today(), year=y)
            for sid in ("filed-lock", "lock-committed"):
                res = cl.DETECTORS[sid](c)
                self.assertEqual(res.status, "attention", (sid, res.detail))
                self.assertIn("before the year ended", res.detail)
            r = tj(p, "check-filed")
            self.assertIn(f"filed {y}: OK", r.stdout)
            self.assertIn("not a filed return", r.stdout)
            r = tj(p, "option-boundary")
            self.assertIn(f"{y} (partial: taken before the year ended)", r.stdout)
            doc = json.loads(tj(p, "option-boundary", "--json").stdout)
            self.assertIn(str(y), doc["partial_locks"])

    def test_lock_directory_is_blocked(self):
        """A2-1146: filed/<year>.json as a directory is not 'no lock'."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "filed" / "2025.json").mkdir(parents=True)
            (p / "taxjson.toml").write_text(TOML)
            for sid in ("filed-lock", "lock-committed"):
                res = cl.DETECTORS[sid](ctx(p))
                self.assertEqual(res.status, "blocked", (sid, res.detail))
                self.assertIn("a directory", res.detail)

    def test_prior_year_record_type_and_missing_year(self):
        """A2-1161: every reader refuses a non-path prior_year_record;
        A2-1162: handoff never looks for filed/-1.json."""
        with tempfile.TemporaryDirectory() as td:
            p = make_project(Path(td), run=False)
            (p / "taxjson.toml").write_text(TOML.replace(
                "option_grant_timing_since = 2025\n",
                'option_grant_timing_since = 2025\n'
                'prior_year_record = ["../2023/filed/2023.json"]\n'))
            for cmd in (("handoff",), ("checklist", "--quick"), ("run", "--no-input")):
                r = tj(p, *cmd, check=False)
                self.assertNotEqual(r.returncode, 0, cmd)
                self.assertIn("prior_year_record must be a path string", r.stderr, cmd)
            (p / "taxjson.toml").write_text(TOML.replace("year = 2025\n", ""))
            r = tj(p, "handoff", check=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("-1.json", r.stderr + r.stdout)
            self.assertIn("year is required", r.stderr)


# ------------------------------------------------------------ crypto-sends
class TestCryptoSendsCommand(unittest.TestCase):
    def setUp(self):
        from test_fix_sends import CB_CSV, KR_LEDGER, _rates_file
        self._td = tempfile.TemporaryDirectory()
        td = Path(self._td.name)
        self.root = td / "proj"
        for a, fn, body in (("cb", "cb_2025.csv", CB_CSV), ("kr", "kr_ledgers.csv", KR_LEDGER)):
            (self.root / "inputs" / a).mkdir(parents=True)
            (self.root / "inputs" / a / fn).write_text(body)
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            '[accounts.cb]\ntype = "taxable"\ncrypto = true\n'
            '[accounts.kr]\ntype = "taxable"\ncrypto = true\n')
        (self.root / "work").mkdir()
        _rates_file(self.root / "work" / "to_base.csv")
        self.home = td / "home"
        self.home.mkdir()
        (self.home / ".crypto_price_cache.json").write_text(json.dumps({
            "TAO22974-2026-05-04": 284.9259948730469,
            "TAO22974-2026-01-05": 300.0}))

    def tearDown(self):
        self._td.cleanup()

    def cli(self, *a):
        from test_fix_sends import _cli
        return _cli(self.root, self.home, *a)

    def test_unparsed_peer_guard_and_unset(self):
        """A2-0359: with kr not parsed, a send to it is not offered as a
        gift; A2-1163: a saved decision can be removed."""
        sol = "cb-20250713T071327-SOL-50.0001"
        r = self.cli("run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        # kr's evidence gone (never parsed): the guard the command
        # applies (main's stale_evidence) holds the checklist too.
        (self.root / "work" / "kr_kraken_transfers.json").unlink()
        r = self.cli("crypto-sends", "cb", "--set", f"{sol}=gift")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("kr (kraken: not parsed yet)", r.stderr)
        r = self.cli("crypto-sends", "cb")
        self.assertIn("kr (kraken: not parsed yet)", r.stderr)
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        c = cl.Ctx(root=self.root, cfg=cfg, year=2026, today=date(2026, 9, 1),
                   run_sub=lambda argv, timeout=900: (0, "", ""))
        res = cl.d_crypto_sends(c)
        self.assertEqual(res.status, "blocked", res.detail)
        self.assertIn("kr (kraken: not parsed yet)", res.detail)
        # Full run: SOL matched; decide the BTC send, then take it back.
        self.assertEqual(self.cli("run", "--no-input").returncode, 0)
        btc = "cb-20250801T080000-BTC-0.001"
        self.assertEqual(self.cli("crypto-sends", "cb", "--set", f"{btc}=self").returncode, 0)
        r = self.cli("crypto-sends", "cb", "--unset", btc)
        self.assertEqual(r.returncode, 0, r.stderr)
        man = json.loads((self.root / "inputs" / "cb" / "sends.json").read_text())
        self.assertNotIn(btc, man["sends"])
        r = self.cli("crypto-sends", "cb", "--unset", btc)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no saved decision", r.stderr)


# ------------------------------------------------------------- roc-entered
QT_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,"
          "Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type\n")
QT_ROWS = [
    "2025-02-03 10:00:00 AM,2025-02-04 12:00:00 AM,Buy,XYZ.UN.TO,XYZ REIT UNITS,100,10.00,"
    "1000.00,0,-1000.00,CAD,55500001,Trades,Individual margin",  # pii-ok
    "2026-01-08 12:00:00 AM,2026-01-08 12:00:00 AM,DIV,XYZ.UN.TO,XYZ REIT RETURN OF CAPITAL "
    "ON 100 SHS REC 12/30/25 PAY 01/08/26,0,0,0,0,20.00,CAD,55500001,Dividends,Individual margin",  # pii-ok
]


class TestRocEntered(unittest.TestCase):
    def test_counts_on_the_roc_sum_date(self):
        """A2-0680: a trust ROC with a Dec 30 record date paid Jan 8 is a
        2025 ADJUST (roc-sum's window), not a 2026 one."""
        for y, want in ((2025, 1), (2026, 0)):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td)
                (p / "inputs" / "margin").mkdir(parents=True)
                (p / "inputs" / "margin" / "qt.csv").write_text(QT_HDR + "\n".join(QT_ROWS) + "\n")
                (p / "taxjson.toml").write_text(TOML.replace("2025", str(y)))
                tj(p, "run", "--no-input")
                r = cl.d_roc_entered(ctx(p, year=y))
                self.assertIn(f"{want} ADJUST row(s) in {y}", r.detail)

    def test_double_roc_is_flagged_by_run_and_checklist(self):
        """A2-0361: the same ROC as a .tt ADJUST and a distributions.map row."""
        with tempfile.TemporaryDirectory() as td:
            p = make_project(Path(td), book=(
                "BUYSELL 2025-01-06 09:30:00 QZR.TO 1000 CAD 20.00 -20000.00 0\n"
                "ADJUST 2025-12-31 12:00:00 QZR.TO CAD -500.00\n"), run=False)
            (p / "distributions.map").write_text("QZR.TO 2025-12-31 -0.50\n")
            r = tj(p, "run", "--no-input")
            self.assertIn("reduced twice", r.stderr.lower())   # apply-distributions
            res = cl.d_roc_entered(ctx(p))
            self.assertEqual(res.status, "attention", res.detail)
            self.assertIn("QZR.TO 2025-12-31", res.detail)


# ---------------------------------------------------------------- wording
US_TOML = ('[settings]\nyear = 2025\ncountry = "usa"\nbase_currency = "USD"\n'
           '[accounts.cb]\ntype = "taxable"\ncrypto = true\n')


class TestStepWording(unittest.TestCase):
    def test_t1135_done_is_qualified(self):
        """A2-0682: 'below the threshold' is on these books only."""
        rep = {"filing_required": False, "year_complete": True}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "taxjson.toml").write_text(TOML)
            r = cl.d_t1135(ctx(p, {"t1135": (0, json.dumps(rep), "")}))
        self.assertEqual(r.status, "done")
        self.assertIn("on these books", r.detail)
        self.assertIn("foreign bank account", r.detail)

    def test_t1135_json_carries_the_scope(self):
        import inspect
        from taxjson.bin import taxjson_t1135
        self.assertIn('"scope_note"', inspect.getsource(taxjson_t1135))

    @rule("US-RPT-09")
    def test_us_crypto_slip_is_1099_da(self):
        """A2-1149: a US crypto account's 2025 sales come on Form 1099-DA."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "taxjson.toml").write_text(US_TOML)
            r = cl.d_t5008(ctx(p))
            self.assertIn("1099-DA", r.detail)
            self.assertNotIn("1099-B", r.detail)
            (p / "taxjson.toml").write_text(US_TOML + '[accounts.ib]\ntype = "taxable"\n')
            r = cl.d_t5008(ctx(p))
            self.assertIn("1099-B / 1099-DA", r.detail)
            (p / "taxjson.toml").write_text(US_TOML.replace("2025", "2024"))
            r = cl.d_t5008(ctx(p, year=2024))
            self.assertIn("1099-B", r.detail)
            self.assertNotIn("1099-DA", r.detail)
        self.assertIn("1099-DA", cl.step_meta("t5008", "usa")[2])

    def test_t5_t3_command_names_the_taxable_line(self):
        """A2-1152: the command field too, both countries."""
        for c in ("canada", "usa"):
            self.assertIn("TAXABLE", cl.step_meta("t5-t3", c)[3], c)

    def test_8949_rounding_gap_passes(self):
        """A2-1154: 40 gains of 1.0053 and 40 superficial losses — the
        per-row rounding gap is not a false ATTENTION in a US project."""
        from taxjson.bin.taxjson_form_export import build_8949
        entries = ([{"term": "SHORT_TERM", "symbol": "A", "date": "2025-03-03",
                     "proceeds": 11.0053, "cost": 10.0, "gain": 1.0053, "qty": 1}
                    for _ in range(40)]
                   + [{"term": "SHORT_TERM", "symbol": "B", "date": "2025-03-04",
                       "proceeds": 10.0, "cost": 10.0049, "gain": 0.0,
                       "disallowed_amount": 0.0049, "qty": 1} for _ in range(40)])
        rep = build_8949(entries)
        self.assertAlmostEqual(rep["gain_unrounded"], 40.212, places=6)
        realized = round(rep["gain_unrounded"], 2)
        summ = {"accounts": [{"account": "ib", "realized": realized}],
                "filing": {"totals": {
                    "proceeds": rep["part_I_totals"]["proceeds"],
                    "gain": rep["part_I_totals"]["gain"]}}}
        cfg = ('[settings]\nyear = 2025\ncountry = "usa"\nbase_currency = "USD"\n'
               '[accounts.ib]\ntype = "taxable"\n')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "taxjson.toml").write_text(cfg)
            r = cl.d_form_export(ctx(p, {"form-export": (0, json.dumps(rep), ""),
                                         "sum": (0, json.dumps(summ), "")}))
        self.assertEqual(r.status, "done", r.detail)
        # A §1256 future stays off Form 8949 but is in the realized gain.
        fut = entries + [{"term": "SHORT_TERM", "symbol": "F:ESZ5", "date": "2025-03-05",
                          "proceeds": 600.0, "cost": 100.0, "gain": 500.0, "qty": 1}]
        rep2 = build_8949(fut)
        summ2 = json.loads(json.dumps(summ))
        summ2["accounts"][0]["realized"] = round(realized + 500.0, 2)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "taxjson.toml").write_text(cfg)
            r = cl.d_form_export(ctx(p, {"form-export": (0, json.dumps(rep2), ""),
                                         "sum": (0, json.dumps(summ2), "")}))
        self.assertEqual(r.status, "done", r.detail)


class TestAuditPhantomBasis(unittest.TestCase):
    def test_manual_reporting_rows_tie_out(self):
        """A2-1150: a phantom-backed sale (manual_reporting_required) is
        tied out as manual reporting, not 'not found' forever."""
        with tempfile.TemporaryDirectory() as td:
            p = make_project(Path(td), book=(
                "BUYSELL 2025-03-03 10:00:00 XYZ.TO -100.00000000 CAD 14.48900000 1448.90000 0.00000\n"),
                run=False)
            (p / "phantoms.json").write_text('[{"symbol": "XYZ.TO", "account": "margin"}]')
            tj(p, "run", "--no-input")
            g = json.loads((p / "work" / "margin_gains.json").read_text())
            self.assertTrue(g.get("manual_reporting_required"))
            r = tj(p, "audit", "--summary", check=False)
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
            self.assertIn("1 phantom-basis disposition(s) tied to MANUAL REPORTING", r.stdout)
            self.assertNotIn("MISSING from the check", r.stdout + r.stderr)
            c = cl.Ctx(root=p, cfg=ctx(p).cfg, year=2025, today=date(2026, 9, 1),
                       run_sub=lambda argv, timeout=900: (lambda r: (r.returncode, r.stdout, r.stderr))(
                           tj(p, *argv, check=False)))
            res = cl.d_audit(c)
            self.assertEqual(res.status, "done", res.detail)
            self.assertIn("1 phantom-basis sale(s) routed to manual reporting", res.detail)


class TestChecklistMarks(unittest.TestCase):
    def test_repeated_flags_all_recorded(self):
        """A2-1159: --done A --done B records both."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "taxjson.toml").write_text(TOML)
            r = tj(p, "checklist", "--done", "roc-entered", "--done", "t5-t3",
                   "--skip", "fees", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual([x["step"] for x in doc["recorded"]],
                             ["roc-entered", "t5-t3", "fees"])
            ov = json.loads((p / "checklist.json").read_text())["overrides"]
            self.assertEqual(set(ov), {"roc-entered", "t5-t3", "fees"})
            r = tj(p, "checklist", "--done", "fees", "--done", "bogus", check=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("unknown step 'bogus'", r.stderr)

    def test_concurrent_marks_are_not_lost(self):
        """A2-1160: simultaneous set_override calls keep every mark."""
        import multiprocessing as mp
        ids = [s[0] for s in cl.STEPS][:12]
        with tempfile.TemporaryDirectory() as td:
            ctxm = mp.get_context("fork")
            procs = [ctxm.Process(target=cl.set_override,
                                  args=(Path(td), 2025, sid, "done"))
                     for sid in ids]
            for pr in procs:
                pr.start()
            for pr in procs:
                pr.join()
            self.assertTrue(all(pr.exitcode == 0 for pr in procs))
            ov = json.loads((Path(td) / "checklist.json").read_text())["overrides"]
        self.assertEqual(set(ov), set(ids))


class TestGrantSinceGate(unittest.TestCase):
    def test_crypto_only_project_gets_no_option_timing_warning(self):
        """A2-1144: carryover and option-boundary share run's gate."""
        from test_fix_sends import _rates_file
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "proj"
            (p / "inputs" / "crypto").mkdir(parents=True)
            (p / "inputs" / "crypto" / "kr_ledgers.csv").write_text(KR_SEND)
            (p / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            (p / "work").mkdir()
            _rates_file(p / "work" / "to_base.csv")
            home = Path(td) / "home"
            home.mkdir()
            (home / ".crypto_price_cache.json").write_text("{}")
            from test_fix_sends import _cli
            self.assertEqual(_cli(p, home, "run", "--no-input").returncode, 0)
            for cmd in ("carryover", "option-boundary"):
                r = _cli(p, home, cmd)
                self.assertNotIn("option_grant_timing_since is not set", r.stderr, cmd)
            from taxjson.bin.taxjson_run import _grant_since_warning
            st = {"year": 2025, "country": "canada"}
            self.assertIsNotNone(_grant_since_warning(st, accounts={"m": {"type": "taxable"}}))
            self.assertIsNone(_grant_since_warning(
                st, accounts={"c": {"type": "taxable", "crypto": True}}))


class TestOptionBoundaryPriorYearRecord(unittest.TestCase):
    TT = ("BUYSELL 2025-02-03 10:00:00 YYY.US 100 CAD 20.0 -2000.0 0\n"
          "BUYSELL 2025-06-03 10:00:00 YYY.US -100 CAD 10.0 1000.0 0\n"
          "BUYSELL 2025-12-15 10:00:00 ZZZ260116C00050000.US -1 CAD 3.99 399.0 0\n"
          "BUYSELL 2026-01-10 10:00:00 ZZZ260116C00050000.US 1 CAD 1.01 -101.0 0\n")

    @rule("CA-OPT-01")
    def test_lock_via_prior_year_record(self):
        """A2-0360: the 2025 lock the 2026 project names in
        prior_year_record is read like a local filed/2025.json."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            p25 = make_project(base / "p2025", toml=TOML, book=self.TT)
            tj(p25, "close-year")
            outs = {}
            for name, extra, local in (("w", 'prior_year_record = "../p2025/filed/2025.json"\n', False),
                                       ("x", "", True)):
                toml = TOML.replace("year = 2025", "year = 2026").replace(
                    "option_grant_timing_since = 2025\n",
                    "option_grant_timing_since = 2026\n" + extra)
                p = make_project(base / f"p2026{name}", toml=toml, book=self.TT, run=False)
                if local:
                    (p / "filed").mkdir()
                    shutil.copy(p25 / "filed" / "2025.json", p / "filed" / "2025.json")
                tj(p, "run", "--no-input")
                doc = json.loads(tj(p, "option-boundary", "--json").stdout)
                outs[name] = (doc["filed_years"], doc["attention"],
                              cl.d_option_boundary(cl.Ctx(
                                  root=p, cfg=ctx(p).cfg, year=2026, today=date(2026, 9, 1),
                                  run_sub=lambda argv, timeout=900, p=p: (lambda r: (
                                      r.returncode, r.stdout, r.stderr))(tj(p, *argv, check=False)))).status)
        self.assertEqual(outs["x"][0], [2025])
        self.assertEqual(outs["w"], outs["x"])
        self.assertGreaterEqual(outs["w"][1], 1)
        self.assertEqual(outs["w"][2], "attention")
