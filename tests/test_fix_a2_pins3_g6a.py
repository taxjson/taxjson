"""Re-audit-2 tests-pins (G6a): taxjson_run.py wrapper wiring and the
taxjson.toml account order (CA-DATE-14).

Each test pins a line the full suite let a mutant change: a wrapper that
forwards a flag or file to its tool, or a recompute that merges the
taxable books. Synthetic data only: `.tt` books in CAD (no FX fetch),
made-up accounts and symbols.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *args, env_extra=None):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
    if env_extra:
        e.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(root, accounts, books, settings="", extra_files=None,
             country="canada", year=2025):
    """A 2025 project: `accounts` in taxjson.toml order, each a taxable
    account with a `.tt` book (books[name]); a name -> dict entry in
    `accounts` adds keys to that account's table."""
    root = Path(root)
    cur = "USD" if country == "usa" else "CAD"
    toml = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            + settings)
    for name in accounts:
        more = ""
        if isinstance(name, tuple):
            name, more = name
        toml += f'\n[accounts.{name}]\ntype = "taxable"\n{more}'
        d = root / "inputs" / name
        d.mkdir(parents=True, exist_ok=True)
        if name in books:
            (d / "book.tt").write_text(books[name])
    (root / "taxjson.toml").write_text(toml)
    for rel, text in (extra_files or {}).items():
        (root / rel).write_text(text)
    return root


class _Res:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _inproc(root, *args, fake=()):
    """Run `taxjson -C root ARGS` in process, recording every tool
    command the wrapper hands to lib.dispatch.run_cmd. A tool whose
    module name is in `fake` is not run (rc 0, empty output)."""
    from taxjson.bin import taxjson_run as R
    from taxjson.lib import dispatch
    real = dispatch.run_cmd
    seen = []

    def spy(cmd, **kw):
        seen.append(list(cmd))
        mod = cmd[2] if len(cmd) > 2 else ""
        if any(mod.endswith(f) for f in fake):
            return _Res()
        return real(cmd, **kw)

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(dispatch, "run_cmd", spy), \
            mock.patch.object(sys, "argv",
                              ["taxjson", "-C", str(root), *args]), \
            mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}), \
            redirect_stdout(out), redirect_stderr(err):
        try:
            R._main()
            code = 0
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue(), seen


def _calls(seen, module):
    return [c for c in seen if len(c) > 2 and c[2].endswith(module)]


def _flag_values(cmd, flag):
    return [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == flag]


# ----------------------------------------------- CA-DATE-14 account order
# zeta sells its 100 XYZ.US at the same moment alpha buys 100. With zeta
# listed first the sale comes first (gain 200, no denial, year-end cost
# 2,000); with alpha first the sale is made from the blended pool of 200
# (cost 1,500: a 300 superficial loss, added to the 100 still held ->
# year-end cost 1,800).
_ORDER_BOOKS = {
    "zeta": ("BUYSELL 2025-01-06 10:00:00 XYZ.US 100 CAD 10 1000 0\n"
             "BUYSELL 2025-03-03 10:00:00 XYZ.US -100 CAD 12 1200 0\n"),
    "alpha": "BUYSELL 2025-03-03 10:00:00 XYZ.US 100 CAD 20 2000 0\n",
}


class _OrderProjects:
    """Both toml orders, built and run once for the class."""
    tmp = None
    roots = {}

    @classmethod
    def build(cls):
        if cls.tmp is not None:
            return
        cls.tmp = tempfile.mkdtemp(prefix="tj_g6a_order_")
        for key, order in (("zeta_first", ["zeta", "alpha"]),
                           ("alpha_first", ["alpha", "zeta"])):
            root = _project(Path(cls.tmp) / key, order, _ORDER_BOOKS)
            r = _cli(root, "run", "--no-input")
            assert r.returncode == 0, r.stderr[-2000:]
            cls.roots[key] = root

    @classmethod
    def cleanup(cls):
        if cls.tmp:
            shutil.rmtree(cls.tmp, ignore_errors=True)
        cls.tmp, cls.roots = None, {}


def tearDownModule():
    _OrderProjects.cleanup()


@rule("CA-DATE-14")
class TestAccountOrderFollowsToml(unittest.TestCase):
    """Rows of different accounts at one moment follow the accounts'
    order in taxjson.toml — in the run's blend (A2-0937) and in every
    tool that recomputes the blended book: check-filed / the run's
    filed-year drift check (A2-0512), t1135 (A2-1592), and its twins
    carryover, audit and wash-sales --explain."""

    @classmethod
    def setUpClass(cls):
        _OrderProjects.build()
        cls.roots = _OrderProjects.roots

    def _summary(self, key):
        doc = json.loads((self.roots[key] / "work" /
                          "zeta_gains_wash.json").read_text())
        return doc["summary"]

    def test_run_blend_follows_toml_order(self):
        """A2-0937: stage_blended_wash_pass merges in toml order
        (taxjson_run.py, `for n in names` in the taxjson-merge call)."""
        s = self._summary("zeta_first")
        self.assertAlmostEqual(s["total_gain"], 200.0, 2)
        self.assertAlmostEqual(s["total_disallowed"], 0.0, 2)
        s = self._summary("alpha_first")
        self.assertAlmostEqual(s["total_gain"], 0.0, 2)
        self.assertAlmostEqual(s["total_disallowed"], 300.0, 2)

    def test_t1135_cost_matches_the_run(self):
        """A2-1592: cmd_t1135 fed the bases in sorted() order."""
        for key, cost in (("zeta_first", 2000.0), ("alpha_first", 1800.0)):
            r = _cli(self.roots[key], "t1135", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (p,) = [x for x in json.loads(r.stdout)["properties"]
                    if x["symbol"] == "XYZ.US"]
            self.assertAlmostEqual(p["year_end_cost"], cost, 2, key)

    def test_carryover_net_gain_matches_the_run(self):
        """A2-1592 twin: cmd_carryover fed the bases in sorted() order."""
        for key, gain in (("zeta_first", 200.0), ("alpha_first", 0.0)):
            r = _cli(self.roots[key], "carryover", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (row,) = [x for x in json.loads(r.stdout)["rows"]
                      if x["year"] == 2025]
            self.assertAlmostEqual(row["net_gain"], gain, 2, key)

    def test_audit_ties_out_in_either_order(self):
        """A2-1592 twin: cmd_audit merged the equity books sorted()."""
        for key in ("zeta_first", "alpha_first"):
            r = _cli(self.roots[key], "audit")
            self.assertEqual(r.returncode, 0,
                             key + "\n" + (r.stdout + r.stderr)[-2500:])
            self.assertIn("1 tied, 0 MISMATCHED", r.stdout + r.stderr)

    def test_wash_sales_explain_traces_the_run_book(self):
        """A2-1592 twin: _explain_wash_sales merged sorted(_by_name)."""
        r = _cli(self.roots["zeta_first"], "wash-sales", "--explain")
        self.assertNotIn("LOSS SALE", r.stdout + r.stderr)
        r = _cli(self.roots["alpha_first"], "wash-sales", "--explain")
        self.assertIn("LOSS SALE", r.stdout + r.stderr)

    def test_check_filed_ok_right_after_close_year(self):
        """A2-0512: the drift check recomputed in the lock's alphabetical
        key order and reported a false DRIFT right after close-year."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(self.roots["zeta_first"], root)
            r = _cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "check-filed")
            self.assertEqual(r.returncode, 0,
                             (r.stdout + r.stderr)[-2500:])
            self.assertNotIn("DRIFT", r.stdout + r.stderr)



# ------------------------------------------------ reconcile-slips wrapper
class TestReconcileSlipsWrapper(unittest.TestCase):
    """A2-0910 / A2-1584 / A2-1594: cmd_reconcile_slips passes the
    project's ticker.map (R1-19) and its country to the reconciler."""

    def test_ticker_map_reaches_the_reconciler(self):
        """A GLOBAL rename: the books carry NEWCO.TO, the slip prints the
        broker's OLDCO. Without --ticker-map: MISSING both ways, rc 1."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ["margin"], {"margin": (
                "BUYSELL 2025-03-03 10:00:00 OLDCO.TO 100 CAD 10 1000 0\n"
                "BUYSELL 2025-06-03 10:00:00 OLDCO.TO -100 CAD 15 1500 0\n"
            )}, extra_files={"ticker.map": "GLOBAL OLDCO.TO NEWCO.TO\n"})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            slip = Path(td) / "slip.csv"
            slip.write_text("Symbol,Quantity,Proceeds\nOLDCO,100,1500.00\n")
            r = _cli(root, "reconcile-slips", str(slip))
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out[-2000:])
        self.assertIn("NEWCO  OK", out)
        self.assertIn("0 missing from computed, 0 missing from slip", out)

    def test_country_reaches_the_reconciler(self):
        """A US project reconciles 1099-B slips under US rules."""
        from taxjson.bin import taxjson_reconcile_slips as RS
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ["margin"], {"margin": (
                "BUYSELL 2025-03-03 10:00:00 ZZZ.US 100 USD 10 1000 0\n"
                "BUYSELL 2025-06-03 10:00:00 ZZZ.US -100 USD 15 1500 0\n"
            )}, country="usa")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            slip = Path(td) / "slip.csv"
            slip.write_text("Symbol,Quantity,Proceeds\nZZZ,100,1500.00\n")
            got = []
            with mock.patch.object(RS, "main",
                                   lambda argv: got.append(argv) or 0):
                code, _o, err, _s = _inproc(root, "reconcile-slips",
                                            str(slip))
        self.assertEqual(code, 0, err[-2000:])
        (argv,) = got
        self.assertEqual(_flag_values(argv, "--country"), ["usa"])


# ------------------------------------------- run: parser --tax-year wiring
def _rbc_csv(as_of):
    hdr = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
           '"Price","Settlement Date","Value","Currency","Description"\n')

    def row(d, q, v):
        return ",".join(f'"{c}"' for c in (
            d, "Buy", "QZF", "QZ FUND UNITS", q, "10", d, v, "CAD",
            "QZ FUND UNITS")) + "\n"
    return (f'"Activity Export as of {as_of}"\n\n' + hdr
            + row("December 1, 2025", "100", "-1000")
            + row("March 3, 2025", "100", "-1000"))


class TestRunPassesTaxYearToParsers(unittest.TestCase):
    """A2-1588 / A2-1594: the run's parse stage passes --tax-year, so an
    RBC export taken before year end (S063-22) is an ATTENTION on the
    console and in the .sum. Without it the warning is silent."""

    def test_rbc_export_before_year_end_is_attention(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ["margin"], {})
            (root / "inputs" / "margin" / "rbc.csv").write_text(
                _rbc_csv("Dec 15, 2025"))
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            con = r.stdout + r.stderr
            sums = "".join(p.read_text()
                           for p in (root / "reports").glob("*.sum"))
        self.assertIn("ATTENTION: rbc.csv", con)
        self.assertIn("2025-12-16 to 2025-12-31", con)
        self.assertIn("2025-12-16 to 2025-12-31", sums)


# --------------------------------------------------- audit gates / --filled
def _audit_project(td, country):
    """Two equity and two crypto taxable accounts with stub books (the
    audit tool itself is faked: these tests read the commands built)."""
    root = Path(td)
    cur = "USD" if country == "usa" else "CAD"
    toml = (f'[settings]\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n')
    work = root / "work"
    work.mkdir(parents=True)
    for n, crypto in (("margin", False), ("cash", False),
                      ("kraken", True), ("coinbase", True)):
        toml += (f'\n[accounts.{n}]\ntype = "taxable"\n'
                 + ("crypto = true\n" if crypto else ""))
        (root / "inputs" / n).mkdir(parents=True)
        (work / f"{n}_base.json").write_text('{"transactions": []}')
        if crypto:
            (work / f"{n}_filled.json").write_text('{"transactions": []}')
    (root / "taxjson.toml").write_text(toml)
    return root


class TestAuditCountryGatesAndFilled(unittest.TestCase):
    """A2-1528: cmd_audit's crypto-blend gate (Canada blends two or more
    crypto accounts, CA-SL-13; a US project audits each on its own books,
    US-CRYPTO-05) and its --per-account-basis gate (US equity blend,
    US-BASIS-01). A2-1594: both --filled lines (the blended crypto pass
    and the per-account crypto pass, R1-271)."""

    def _audit_cmds(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = _audit_project(td, country)
            code, _o, err, seen = _inproc(root, "audit",
                                          fake=("taxjson_audit",))
            self.assertEqual(code, 0, err[-2000:])
            cmds = _calls(seen, "taxjson_audit")
            # Only the account each --filled names, relative to work/.
            norm = [[a.replace(str(root / "work") + os.sep, "")
                     for a in c] for c in cmds]
        return norm

    @rule("CA-SL-13")
    @rule_absent("CA-SL-13", country="usa")
    @rule("US-CRYPTO-05")
    def test_crypto_blend_is_canada_only(self):
        ca = self._audit_cmds("canada")
        self.assertEqual(len(ca), 2)          # equity blend + crypto blend
        crypto = [c for c in ca if "kraken_filled.json" in c]
        self.assertEqual(len(crypto), 1)
        self.assertEqual(sorted(_flag_values(crypto[0], "--filled")),
                         ["coinbase_filled.json", "kraken_filled.json"])
        us = self._audit_cmds("usa")
        self.assertEqual(len(us), 3)          # equity blend + 2 crypto
        for acct in ("kraken", "coinbase"):
            (c,) = [c for c in us if f"{acct}_base.json" in c]
            self.assertEqual(_flag_values(c, "--filled"),
                             [f"{acct}_filled.json"])
            self.assertIn("--no-wash", c)
            self.assertNotIn("--per-account-basis", c)

    @rule("US-BASIS-01")
    @rule_absent("US-BASIS-01", country="canada")
    def test_us_equity_blend_keeps_basis_per_account(self):
        (us_eq,) = [c for c in self._audit_cmds("usa")
                    if "kraken_filled.json" not in c
                    and "kraken_base.json" not in c
                    and "coinbase_base.json" not in c]
        self.assertIn("--per-account-basis", us_eq)
        (ca_eq,) = [c for c in self._audit_cmds("canada")
                    if "kraken_filled.json" not in c]
        self.assertNotIn("--per-account-basis", ca_eq)


# ------------------------------------------- option timing in the wrappers
# Grant timing (since 2025) with option_buyback_loss_superficial = true:
# 10 calls written, bought back at a 5,000 loss and bought again five days
# later. The loss is superficial and lands in the replacement calls' cost
# (5,500 + 5,000 = 10,500). A wrapper that drops the option settings
# recomputes on close timing with no deferral (cost 5,500).
_OPT_SETTINGS = ('option_premium_timing = "grant"\n'
                 'option_grant_timing_since = 2025\n'
                 'option_buyback_loss_superficial = true\n')
_OPT_BOOK = (
    "BUYSELL 2025-01-02 10:00:00 XYZ.US 1000 CAD 120 120000 0\n"
    "BUYSELL 2025-03-03 10:00:00 XYZ251219C00150000.US -10 CAD 3 3000 0\n"
    "BUYSELL 2025-03-20 10:00:00 XYZ251219C00150000.US 10 CAD 5 5000 0\n"
    "BUYSELL 2025-03-25 10:00:00 XYZ251219C00150000.US 10 CAD 5.5 5500 0\n"
    "BUYSELL 2025-11-03 10:00:00 XYZ260116C00160000.US -5 CAD 4 2000 0\n")
_CALL = "XYZ251219C00150000.US"


@rule("CA-SL-12")
class TestOptionTimingForwarded(unittest.TestCase):
    """A2-0939: t1135, close-year/handoff (_handoff_gains_flags), audit
    and wash-sales --explain forward [settings] option_premium_timing,
    option_grant_timing_since and option_buyback_loss_superficial.
    A2-1607: so does `list --date`."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.mkdtemp(prefix="tj_g6a_opt_")
        cls.root = _project(Path(cls._td) / "p", ["margin"],
                            {"margin": _OPT_BOOK}, settings=_OPT_SETTINGS)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._td, ignore_errors=True)

    @rule("CA-RPT-12")
    def test_t1135_cost_carries_the_denied_buyback_loss(self):
        r = _cli(self.root, "t1135", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        (p,) = [x for x in json.loads(r.stdout)["properties"]
                if x["symbol"] == _CALL]
        self.assertAlmostEqual(p["year_end_cost"], 10500.0, 2)

    def test_audit_recomputes_with_the_project_timing(self):
        r = _cli(self.root, "audit")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out[-2500:])
        self.assertIn("3 tied, 0 MISMATCHED", out)

    def test_wash_sales_explain_traces_the_denial(self):
        r = _cli(self.root, "wash-sales", "--explain")
        out = r.stdout + r.stderr
        self.assertIn(f"{_CALL}   2025-03-20", out)
        self.assertIn("disallowed +$5,000.00", out)

    def test_close_year_lock_records_the_deferral(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(self.root, root)
            r = _cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            lock = json.loads((root / "filed" / "2025.json").read_text())
        pos = lock["year_end"]["equity"][_CALL]
        self.assertAlmostEqual(pos["acb"], 10500.0, 2)
        self.assertAlmostEqual(pos["deferred"], 5000.0, 2)

    def test_list_as_of_date_uses_the_project_timing(self):
        r = _cli(self.root, "list", "--date", "2025-12-31", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        (row,) = [x for x in json.loads(r.stdout)["rows"]
                  if x["symbol"] == _CALL]
        self.assertAlmostEqual(row["cost"], 10500.0, 2)
        self.assertAlmostEqual(row["deferred_wash"], 5000.0, 2)


# --------------------------------------------- missing_history.json in the wrappers
# margin sells 100 ZZZ.TO it has no purchase for (missing_history.json declares
# the opening, cost 2,500), buys 100, sells at a 500 loss and rebuys
# (superficial); cash buys 50 in the window. Without missing_history.json the
# first sale opens a short instead.
_PH_BOOKS = {
    "margin": ("BUYSELL 2025-01-10 10:00:00 ZZZ.TO -100 CAD 30 3000 0\n"
               "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 20 2000 0\n"
               "BUYSELL 2025-03-10 10:00:00 ZZZ.TO -100 CAD 15 1500 0\n"
               "BUYSELL 2025-03-20 10:00:00 ZZZ.TO 100 CAD 16 1600 0\n"),
    "cash": "BUYSELL 2025-03-12 10:00:00 ZZZ.TO 50 CAD 15 750 0\n",
}
_PHANTOMS = ('[{"symbol": "ZZZ.TO", "account": "margin", "quantity": 100,'
             ' "total_cost": 2500}]')


@rule("CA-ACB-11")
class TestPhantomsForwarded(unittest.TestCase):
    """A2-1571: carryover, wash-sales --explain and audit apply
    missing_history.json. A2-1617: so do the run's combined cross-account radar
    and audit's recompute. A2-1572: handoff, harvest (and its
    --ticker-map) and watch pass them too."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.mkdtemp(prefix="tj_g6a_ph_")
        cls.root = _project(Path(cls._td) / "p", ["margin", "cash"],
                            _PH_BOOKS,
                            extra_files={"missing_history.json": _PHANTOMS,
                                         "ticker.map": ""})
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._td, ignore_errors=True)

    def test_carryover(self):
        r = _cli(self.root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        (row,) = json.loads(r.stdout)["rows"]
        self.assertEqual(row["dispositions"], 1)
        self.assertAlmostEqual(row["net_gain"], 0.0, 2)

    def test_wash_sales_explain(self):
        r = _cli(self.root, "wash-sales", "--explain")
        out = r.stdout + r.stderr
        self.assertIn("ZZZ.TO   2025-03-10   qty=100.0000", out)
        self.assertIn("raw -$500.00, disallowed +$500.00", out)

    def test_audit(self):
        r = _cli(self.root, "audit")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out[-2500:])
        self.assertIn("1 tied, 0 MISMATCHED, 0 not found", out)

    def test_run_combined_radar(self):
        doc = json.loads((self.root / "reports" /
                          "wash_radar_COMBINED.json").read_text())
        rows = [x for sec in doc["sections"] for x in sec["rows"]
                if x.get("ticker") == "ZZZ.TO"]
        self.assertEqual([x["taxable_qty"] for x in rows], [150.0])

    def test_watch_passes_phantoms(self):
        code, _o, err, seen = _inproc(self.root, "watch", "--json",
                                      fake=("taxjson_wash_radar",))
        (cmd,) = _calls(seen, "taxjson_wash_radar")
        self.assertEqual(_flag_values(cmd, "--incomplete-history"),
                         [str(self.root / "missing_history.json")])

    def test_harvest_passes_phantoms_and_ticker_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(self.root, root)
            # Radar reports older than the books: harvest runs a live
            # radar over the taxable books.
            for f in (root / "reports").glob("wash_radar_*.json"):
                os.utime(f, (1_000_000_000, 1_000_000_000))
            code, _o, err, seen = _inproc(
                root, "harvest", "--no-ibkr",
                fake=("taxjson_wash_radar", "taxjson_harvest"))
            (radar,) = _calls(seen, "taxjson_wash_radar")
            (harvest,) = _calls(seen, "taxjson_harvest")
            self.assertEqual(_flag_values(radar, "--incomplete-history"),
                             [str(root / "missing_history.json")])
            self.assertEqual(_flag_values(harvest, "--ticker-map"),
                             [str(root / "ticker.map")])


@rule("CA-ACB-11")
class TestHandoffAppliesPhantoms(unittest.TestCase):
    """A2-1572: cmd_handoff's opening snapshot applies missing_history.json, as
    close-year did; without it the 2026 opening is short the phantom
    lot and handoff reports a missing lot (rc 1)."""

    def test_handoff_ok(self):
        book = ("BUYSELL 2025-01-10 10:00:00 ZZZ.TO -100 CAD 30 3000 0\n"
                "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 300 CAD 20 6000 0\n"
                "BUYSELL 2025-04-03 10:00:00 YYY.TO 10 CAD 20 200 0\n"
                "BUYSELL 2025-05-03 10:00:00 YYY.TO -10 CAD 25 250 0\n")
        with tempfile.TemporaryDirectory() as td:
            p25 = _project(Path(td) / "p25", ["margin"], {"margin": book},
                           extra_files={"missing_history.json": _PHANTOMS})
            self.assertEqual(_cli(p25, "run", "--no-input").returncode, 0)
            r = _cli(p25, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            p26 = _project(
                Path(td) / "p26", ["margin"], {"margin": book + (
                    "BUYSELL 2026-02-03 10:00:00 ZZZ.TO -50 CAD 25 1250 0\n")},
                settings=f'prior_year_record = "{p25}/filed/2025.json"\n',
                extra_files={"missing_history.json": _PHANTOMS}, year=2026)
            self.assertEqual(_cli(p26, "run", "--no-input").returncode, 0)
            r = _cli(p26, "handoff")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out[-2500:])
        self.assertIn("Everything the closed year carried forward is here",
                      out)


if __name__ == "__main__":
    unittest.main()
