"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).
"""
import argparse
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_redact as R
from taxjson.bin.taxjson_export import render_holdings_toml
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.tomlcompat import tomllib

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *a, cwd=REPO_ROOT):
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run",
                                             ["-C", str(root), *a]),
                          cwd=cwd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


# ------------------------------------------------------------- TJ-0418

KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
          "margin,misc,ledgers\n")
# ZZC: bought 1.0, sold 0.9999999 (a sale of "all" leaving 1e-07 units:
# rounding residue). ZZD: 2.5 units still held.
KR = (KR_HDR
      + "TXA1,OA1,ZZC/CAD,2025-01-15 10:00:00.1234,buy,limit,40,"
        "40,0,1.0,,,\n"
      + "TXA2,OA2,ZZD/CAD,2025-02-15 10:00:00.1234,buy,limit,10,"
        "25,0,2.5,,,\n"
      + "TXA3,OA3,ZZC/CAD,2025-03-15 10:00:00.1234,sell,limit,50,"
        "49.999995,0,0.9999999,,,\n")


def _kraken_project(td):
    root = Path(td)
    (root / "inputs" / "kr1").mkdir(parents=True)
    (root / "inputs" / "kr1" / "kr_trades.csv").write_text(KR)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.kr1]\ntype = "taxable"\ncrypto = true\n')
    return root


@unittest.skipIf(tomllib is None, "reading TOML needs Python 3.11+ or tomli")
class TestKrakenHoldingsSymbol(unittest.TestCase):
    """TJ-0418: a Kraken account's holdings export wrote a coin bare
    (`ZZD`) while Kraken's own holdings snapshot writes `ZZD.KR`: a
    reader keyed on the suffix took the coin for a share. A coin's
    rounding residue was a holding too."""

    def test_kraken_coins_carry_the_venue_suffix_and_dust_is_said(self):
        with tempfile.TemporaryDirectory() as td:
            root = _kraken_project(td)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (root / "reports" / "kr1_holdings.toml").read_text(
                encoding="utf-8")
            doc = tomllib.loads(text)
            # A file the previous release wrote (bare coin): the next
            # run's moved list names no coin closed and opened again.
            prev = text.replace('symbol = "ZZD.KR"', 'symbol = "ZZD"')
            (root / "reports" / "kr1_holdings.toml").write_text(prev)
            again = _cli(root, "run", "--no-input")
        held = {h["symbol"]: h for h in doc["holding"]}
        self.assertEqual(sorted(held), ["ZZD.KR"])
        self.assertEqual(held["ZZD.KR"]["asset_type"], "crypto")
        self.assertEqual(held["ZZD.KR"]["quantity"], 2.5)
        self.assertEqual(doc["meta"]["holdings_count"], 1)
        self.assertIn("# left out as dust (under 1e-06 units and a cost "
                      "under 1): ZZC.KR 1e-07", text)
        self.assertEqual(again.returncode, 0, again.stderr)
        for said in ("changed since the last run",
                     "holdings changes vs prior run"):
            self.assertNotIn(said, again.stdout + again.stderr)

    def test_an_exchange_with_no_suffix_writes_the_bare_coin(self):
        args = argparse.Namespace(account_name="cb1", inputs=[],
                                  country="canada", base_currency=None,
                                  crypto=True, dust_threshold=1e-6)
        agg = {"ZZD": {"qty": 2.5, "total_cost": 25.0, "currency": "CAD"}}
        text = "\n".join(render_holdings_toml(agg, args))
        self.assertIn('symbol = "ZZD"\n', text + "\n")
        kr = "\n".join(render_holdings_toml(
            agg, argparse.Namespace(**vars(args), symbol_suffix="KR")))
        self.assertIn('symbol = "ZZD.KR"', kr)
        # A securities account is never suffixed.
        eq = "\n".join(render_holdings_toml(
            agg, argparse.Namespace(**{**vars(args), "crypto": False},
                                    symbol_suffix="KR")))
        self.assertIn('symbol = "ZZD"\n', eq + "\n")


# ------------------------------------------------------------- TJ-0414

def _tjs(*args, cwd=REPO_ROOT):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(args)),
        cwd=cwd, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


ACCT_HEAD = "Date,Description,Account #\n"


class TestRedactReadsCsvRecords(unittest.TestCase):
    """TJ-0414: redact read physical lines. A quoted Description with a
    line break before the Account # cell kept the account id (and
    `--check` said none was found); a quoted alias with a line break
    was blanked on its first line only, its second line kept and the
    next cell (the symbol) blanked in its place."""

    def test_an_account_after_a_multiline_cell_is_replaced(self):
        for nl in ("\n", "\r\n"):
            with self.subTest(newline=repr(nl)):
                src = (ACCT_HEAD.replace("\n", nl)
                       + f'2026-01-05,"memo{nl}keep",99900077{nl}'
                       + f'2026-01-06,"memo",ZZD{nl}')
                out, rep = R.redact_text(src)
                self.assertEqual(rep.accounts, {"99900077": "99900001"})
                self.assertNotIn("99900077", out)
                # The structure is the original's: the cell keeps its
                # line break, the line endings theirs.
                self.assertEqual(out, src.replace("99900077", "99900001"))
                self.assertEqual(rep.unreplaced, {})

    def test_a_multiline_alias_is_blanked_whole(self):
        src = ('ClientAccountID,AccountAlias,Symbol\n'
               'U1234567,"Synthetic\nAlias",ZZZ\n')
        out, rep = R.redact_text(src)
        self.assertEqual(out, 'ClientAccountID,AccountAlias,Symbol\n'
                              'U9990001,"REDACTED",ZZZ\n')
        self.assertEqual(rep.identity_rows, 1)

    def test_review_lines_are_the_copys(self):
        # A name in a free-text column after a blanked two-line alias:
        # the copy's line 3 (the alias is one line there).
        src = ('ClientAccountID,AccountAlias,Description\n'
               'U1234567,"Synthetic\nAlias",x\n'
               'U1234567,,FROM SAM SAMPLE\n')
        out, rep = R.redact_text(src)
        self.assertEqual(out.splitlines()[2],
                         "U9990001,,FROM SAM SAMPLE")
        self.assertIn(3, [n for n, _why in rep.review])

    def test_an_unclosed_quote_is_read_line_by_line_and_listed(self):
        src = (ACCT_HEAD + '2026-01-05,"memo,99900077\n'
               + "2026-01-06,x,99900077\n")
        out, rep = R.redact_text(src)
        self.assertNotIn("99900077", out)
        self.assertIn((2, R.UNBALANCED_REASON), rep.review)

    def test_check_finds_the_account_of_a_multiline_row(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "synthetic.csv"
            f.write_text(ACCT_HEAD + '2026-01-05,"memo\nkeep",99900077\n')
            r = _tjs("redact", "--no-denylist", "--check", str(f))
        self.assertIn("account ids: 1 distinct, every occurrence replaced",
                      r.stdout)
        self.assertNotIn("account ids: none found", r.stdout)


# ------------------------------------------------- TJ-0234/0235/0236

_GEN_MAP = """\
[columns]
date = "Date"
action = "Transaction type"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"

[formats]
date = "%m/%d/%Y"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"""
_GEN_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
            "01/15/2025,BUY,ZZD,10,{price},100.00,CAD\n")
_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")


def _brokerage(files, broker, name):
    """taxjson-brokerage on `name`, `files` alone in a folder of their
    own: the CompletedProcess."""
    with tempfile.TemporaryDirectory() as td:
        for n, text in files.items():
            (Path(td) / n).write_text(text)
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                   TAXJSON_LOCAL_TZ="America/Toronto")
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", broker, "--account", "x",
                                str(Path(td) / name)]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)


def _error_line(r):
    return next(x for x in r.stderr.splitlines() if "error:" in x)


class TestParserRefusalsNameTheFileOnce(unittest.TestCase):
    """TJ-0234 / TJ-0236: a refusal that names its file after the
    parser's label ("generic importer: ws.csv line 2: …", "Kraken
    trades kr.csv txid=…: …") was prefixed with the file again."""

    def test_generic_refusal(self):
        r = _brokerage({"ws.csv": _GEN_CSV.format(price="broken"),
                        "ws.csv.toml": _GEN_MAP}, "generic", "ws.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = _error_line(r)
        self.assertIn("error: generic importer: ws.csv line 2: ", line)
        self.assertEqual(line.count("ws.csv"), 1, line)

    def test_kraken_unparseable_cost(self):
        rows = ("T1,O1,XBT/USD,2024-02-10 14:25:18,buy,limit,40000,"
                "4x00,8,0.1,,,\n")
        r = _brokerage({"kr.csv": _KT_H + rows}, "kraken", "kr.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = _error_line(r)
        self.assertIn("error: Kraken trades kr.csv txid=", line)
        self.assertEqual(line.count("kr.csv"), 1, line)

    def test_a_message_naming_no_file_is_still_prefixed(self):
        from taxjson.bin.taxjson_brokerage import _names_its_file
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ws.csv"
            f.write_text("x")
            self.assertFalse(_names_its_file(
                "generic importer: unparseable price value in the "
                "ws.csv.bak copy", f))
            self.assertFalse(_names_its_file(
                "generic importer: ws.csvx line 2: x", f))
            self.assertTrue(_names_its_file("ws.csv: x", f))


class TestGenericMappingNameMasked(unittest.TestCase):
    """TJ-0235: a mapping sidecar named for the account
    (99900001.csv.toml) was printed whole in its refusals."""

    def test_unknown_key_names_the_masked_mapping(self):
        bad = _GEN_MAP.replace('price = "Price"', 'prices = "Price"')
        r = _brokerage({"99900001.csv": _GEN_CSV.format(price="10.00"),
                        "99900001.csv.toml": bad}, "generic",
                       "99900001.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("99900001", r.stdout + r.stderr)
        self.assertIn("99***.csv.toml: unknown [columns].prices",
                      _error_line(r))

    def test_date_format_refusal_names_the_masked_mapping(self):
        csv_text = _GEN_CSV.format(price="10.00").replace(
            "01/15/2025", "2025-01-15")
        r = _brokerage({"99900001.csv": csv_text,
                        "99900001.csv.toml": _GEN_MAP}, "generic",
                       "99900001.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("99900001", r.stdout + r.stderr)


# ------------------------------------------------------------- TJ-0221

class TestFxCashV1RewardPricedInBase(unittest.TestCase):
    """TJ-0221: ledger v1 read a USDC reward priced in CAD as CAD
    income: the 5 US dollars never came in, and a later spend of them
    was an overdraft. It now books them as v2 does (TJ-0038)."""

    REWARD = {"action": "DIVIDEND", "description": "Staking Reward",
              "symbol": "USDC", "quantity": 5.0, "currency": "CAD",
              "price": 1.37, "gross_amount": 6.85, "net_amount": 6.85,
              "date": "2025-07-02", "date_settle": "2025-07-02",
              "account": "crypto", "source": "cb.csv"}

    @staticmethod
    def _ledger(rows, country="canada"):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        return build_ledger(rows, "CAD", {}, 2025, country=country,
                            rate_of=lambda cur, on: 1.40)

    @rule("CA-FX-07")
    def test_the_rewards_dollars_come_in_at_the_incomes_value(self):
        doc = self._ledger([self.REWARD])
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 5.0, "acb": 6.85}})
        self.assertEqual(doc["per_currency"]["USD"]["acquired"], 5.0)
        # Priced in USD: at the day's rate, as before.
        doc = self._ledger([dict(self.REWARD, currency="USD", price=1.0,
                                 gross_amount=5.0, net_amount=5.0)])
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 5.0, "acb": 7.0}})

    def test_a_us_project_keeps_the_stablecoin_as_property(self):
        doc = self._ledger([self.REWARD], country="usa")
        self.assertEqual(doc["pools_year_end"], {})


# ------------------------------------------------------------- TJ-0207

class TestTtLineRefusalOnce(unittest.TestCase):
    """TJ-0207: a contract size of 0 (or a stray trailing token) was
    refused inside the row parse and wrapped again: "h.tt:1:
    taxjson-convert-tt: malformed .tt line (h.tt:1: contract size …)"
    named the place twice."""

    def _refusal(self, line):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        with self.assertRaises(ValueError) as cm:
            parse_tt_line(line, "margin", "h.tt:1")
        return str(cm.exception)

    def test_contract_size(self):
        line = "BUYSELL 2024-03-01 10:00:00 ZZQA.US 10 USD 1 10 0 x0"
        msg = self._refusal(line)
        self.assertTrue(msg.startswith("h.tt:1: contract size 'x0' must "
                                       "be > 0 ("), msg)
        self.assertEqual(msg.count("h.tt:1"), 1, msg)
        self.assertNotIn("malformed .tt line", msg)
        self.assertTrue(msg.endswith(f": {line!r}"), msg)

    def test_trailing_token(self):
        msg = self._refusal(
            "BUYSELL 2024-03-01 10:00:00 ZZQA.US 10 USD 1 10 0 x100 zz")
        self.assertTrue(msg.startswith("h.tt:1: BUYSELL row has 1 "
                                       "unexpected trailing token(s)"), msg)
        self.assertEqual(msg.count("h.tt:1"), 1, msg)

    def test_a_bad_number_keeps_the_general_wording(self):
        msg = self._refusal("BUYSELL 2024-03-01 10:00:00 ZZQA.US ten USD "
                            "1 10 0")
        self.assertIn("malformed .tt line", msg)


# ------------------------------------------------------------- TJ-0216

class TestCorpActionsNamesTheManifestShort(unittest.TestCase):
    """TJ-0216: the corp-actions stage named the elections manifest by
    its absolute path (captured in the .diag); the console turned it
    into inputs/margin/manifest.json in a year folder whose exports are
    in ../inputs/. It is named as `taxjson elect` names it."""

    def test_pending_election_names_the_shared_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "demo"
            r = _tjs("init", "--demo", str(demo))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (demo / "inputs" / "margin" / "manifest.json").write_text(
                '{"elections": {}}\n')
            r = _tjs("-C", str(demo / "2024"), "run", "--account",
                     "margin", "--no-input")
            diag = "".join(
                p.read_text(encoding="utf-8") for p in
                (demo / "2024" / "work").glob("margin_*corp.json.diag"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("are saved in ../inputs/margin/manifest.json.",
                      r.stdout + r.stderr)
        self.assertIn("the elections are saved in "
                      "../inputs/margin/manifest.json.", diag)
        self.assertNotIn(f"{td}/demo/inputs/margin/manifest.json",
                         diag + r.stdout + r.stderr)


# ------------------------------------------------------------- TJ-0253

class TestUndetectedFileInSharedInputs(unittest.TestCase):
    """TJ-0253: a file no rule routes, in exports shared by every year,
    was named inputs/margin/x.csv from the year folder, where it is
    ../inputs/margin/x.csv."""

    UNKNOWN = "zz_col_a,zz_col_b,zz_col_c\n1,2,3\n"

    def test_year_folder_names_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "demo"
            r = _tjs("init", "--demo", str(demo))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (demo / "inputs" / "margin" / "zz_unknown.csv").write_text(
                self.UNKNOWN)
            r = _tjs("-C", str(demo / "2024"), "run", "--account",
                     "margin", "--no-input")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("cannot detect broker for "
                      "../inputs/margin/zz_unknown.csv.", r.stdout + r.stderr)

    def test_single_folder_project_names_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = _kraken_project(td)
            (root / "inputs" / "kr1" / "zz_unknown.csv").write_text(
                self.UNKNOWN)
            r = _tjs("-C", str(root), "run", "--no-input")
        self.assertIn("cannot detect broker for "
                      "inputs/kr1/zz_unknown.csv.", r.stdout + r.stderr)


# ------------------------------------------------------- TJ-0218, TJ-0219

_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _locked(path, mode):
    """chmod `path` to `mode`; the cleanup puts 0o755 / 0o644 back."""
    path.chmod(mode)

    def _unlock():
        if path.exists():
            path.chmod(0o755 if path.is_dir() else 0o644)
    return _unlock


@unittest.skipIf(_ROOT, "root ignores permissions")
class TestPermissionMessagesNameTheFolderAndFix(unittest.TestCase):
    """TJ-0218: a write refused in a read-only folder gave the reason
    alone ("cannot write checklist.json: Permission denied") or, where
    no writer worded it, "permission denied: /abs/path". The file is
    named from where the command was run, with the `chmod` to run."""

    def test_checklist_done_in_a_read_only_project(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            _kraken_project(proj)
            self.addCleanup(_locked(proj, 0o555))
            r = _tjs("-C", "proj", "checklist", "--done", "1", cwd=td)
            self.assertFalse((proj / "checklist.json").exists())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        text = " ".join(r.stderr.split())
        self.assertIn("cannot write proj/checklist.json: Permission denied "
                      "— nothing was written", text)
        self.assertIn("The folder proj/ is not writable: `chmod u+w proj`.",
                      text)

    def test_an_unworded_write_names_the_folder_not_the_temp_file(self):
        from taxjson.lib.cli_diag import describe_input_error
        with tempfile.TemporaryDirectory() as td:
            ro = Path(td) / "rodir"
            ro.mkdir()
            self.addCleanup(_locked(ro, 0o555))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            e = PermissionError(13, "Permission denied",
                                str(ro / "x.json.ab12CD34.part"))
            self.assertEqual(
                describe_input_error(e),
                "cannot write rodir/x.json: Permission denied — The folder "
                "rodir/ is not writable: `chmod u+w rodir`")

    def test_an_unreadable_file_names_chmod_u_r(self):
        from taxjson.lib.cli_diag import describe_input_error
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "sub" / "zz.csv"
            f.parent.mkdir()
            f.write_text("a\n")
            self.addCleanup(_locked(f, 0o200))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            e = PermissionError(13, "Permission denied", str(f))
            self.assertEqual(
                describe_input_error(e),
                "cannot read sub/zz.csv: Permission denied — `chmod u+r "
                "sub/zz.csv` lets taxjson read it")


@unittest.skipIf(_ROOT, "root ignores permissions")
class TestReadOnlyRefusalNamesThePath(unittest.TestCase):
    """TJ-0219: a read-only ticker.map refused by format-map (and
    ticker-map --suggest --write) said `chmod u+w ticker.map`, the bare
    file name, wrong when run with -C from elsewhere."""

    def test_format_map_from_elsewhere(self):
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "demo"
            r = _tjs("init", "--demo", str(demo))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            tm = demo / "2024" / "ticker.map"
            before = tm.read_text() + "\nZZQ   ZZQ.TO\n"
            tm.write_text(before)
            self.addCleanup(_locked(tm, 0o444))
            r = _tjs("-C", "demo/2024", "format-map", "--write", cwd=td)
            self.assertEqual(tm.read_text(), before)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        text = " ".join(r.stderr.split())
        self.assertIn("cannot write demo/2024/ticker.map: the file is "
                      "read-only", text)
        self.assertIn("`chmod u+w demo/2024/ticker.map` lets taxjson "
                      "rewrite it.", text)
        self.assertNotIn("`chmod u+w ticker.map`", text)

    def test_the_refusal_reason_names_the_path_from_here(self):
        from taxjson.lib.safe_write import ReadOnlyFileError, \
            refuse_read_only
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "y" / "slips.toml"
            f.parent.mkdir()
            f.write_text("")
            self.addCleanup(_locked(f, 0o444))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            with self.assertRaises(ReadOnlyFileError) as cm:
                refuse_read_only(f)
        self.assertIn("`chmod u+w y/slips.toml` lets taxjson rewrite it",
                      cm.exception.strerror)


# ------------------------------------------------------------- TJ-0242

class _NoisyYahoo:
    """A yfinance stand-in that prints and logs as yfinance does on a
    miss, and returns nothing."""

    def _noise(self):
        import logging
        import sys
        print("[*****100%*****]  1 of 1 completed")
        print("1 Failed download: ['ZZQCAD=X']: possibly delisted",
              file=sys.stderr)
        logging.getLogger("yfinance").error("ZZQCAD=X: possibly delisted")

    def download(self, *a, **k):
        self._noise()
        return None

    def Ticker(self, sym):
        outer = self

        class _T:
            def history(self, *a, **k):
                outer._noise()
                raise RuntimeError("no data")
        return _T()


class TestToBaseCurrQuietsYahoo(unittest.TestCase):
    """TJ-0242: to_base_curr asked yfinance without silencing it, as the
    price chain does: its raw lines reached the console (and stdout,
    where the program prints its rates)."""

    def _quiet(self, fn):
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin import to_base_curr as TB
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(TB, "yf", _NoisyYahoo()), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            got = fn(TB)
        return got, out.getvalue(), err.getvalue()

    def test_fetch_yahoo_prints_nothing(self):
        got, out, err = self._quiet(
            lambda TB: TB.fetch_yahoo("ZZQCAD=X", "2025-01-02", "2025-01-03"))
        self.assertEqual(got, {})
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_spot_row_prints_nothing(self):
        got, out, err = self._quiet(lambda TB: TB._spot_row("ZZQ", "USD"))
        self.assertIsNone(got)
        self.assertEqual(out, "")
        self.assertEqual(err, "")


# ------------------------------------------------------------- TJ-0124

_EXAMPLES = REPO_ROOT / "examples"
# (parser id, demo file, the first transaction's export line)
_DEMO_FIRST_LINE = (("ib", "ib_demo.csv", 9),
                    ("rbc_direct", "rbc_direct_demo.csv", 6),
                    ("webull", "webull_demo.csv", 7),
                    ("coinbase", "coinbase_demo.csv", 2),
                    ("kraken", "kraken_demo.csv", 2))


def _parse_demo(bid, name, td):
    import contextlib
    import io
    import shutil
    from unittest import mock
    from taxjson.bin.taxjson_brokerage import load_brokerage
    path = Path(td) / name
    shutil.copy(_EXAMPLES / name, path)
    ex = load_brokerage(bid)()
    with mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "America/Toronto"}), \
            contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        txs = ex.parse_file(path)
    return ex, txs, path


class TestEveryParserNamesTheRowLine(unittest.TestCase):
    """TJ-0124 (left open): only Questrade and the generic importer
    noted each transaction's export line, so a row the schema check
    refused from IB, RBC, Webull, Coinbase or Kraken was named by a
    masked row id ('row id 25***'), which cannot be searched for."""

    def test_each_demo_transaction_has_its_line(self):
        for bid, name, first in _DEMO_FIRST_LINE:
            with self.subTest(parser=bid), \
                    tempfile.TemporaryDirectory() as td:
                ex, txs, path = _parse_demo(bid, name, td)
                n = len(path.read_text(encoding="utf-8").splitlines())
                self.assertTrue(txs)
                lines = [ex.row_line(t) for t in txs]
                self.assertEqual(lines[0], first, lines)
                for ln in lines:
                    self.assertIsNotNone(ln, lines)
                    self.assertTrue(1 < ln <= n, lines)

    def test_schema_error_names_the_ib_line(self):
        import contextlib
        import io
        import sys
        from unittest import mock
        from taxjson.bin import taxjson_brokerage as B
        cls = B.load_brokerage("ib")
        real = cls.parse_file

        def _blank_first_symbol(self, path):
            txs = real(self, path)
            txs[0]["symbol"] = ""
            return txs
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ib_demo.csv"
            path.write_bytes((_EXAMPLES / "ib_demo.csv").read_bytes())
            err = io.StringIO()
            with mock.patch.object(cls, "parse_file", _blank_first_symbol), \
                    mock.patch.object(sys, "argv", [
                        "taxjson-brokerage", "--brokerage", "ib",
                        "--account", "x", "--strict", str(path)]), \
                    mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}), \
                    contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                B.main()
        line = next(x for x in err.getvalue().splitlines()
                    if "missing required field" in x)
        self.assertIn("ib_demo.csv line 9: BUYSELL (no symbol) 2024-02-12",
                      line)
        self.assertNotIn("row id", line)


if __name__ == "__main__":
    unittest.main()
