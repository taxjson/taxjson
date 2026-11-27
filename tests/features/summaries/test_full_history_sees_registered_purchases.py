"""A window outside the tax year (`tjs stats all`, `2y`, another year,
--all-history) reads the full-history gains (src/taxjson/cli/common.py
`_gains_all_file`, `_gains_all_blend`), built with the registered
accounts' book the run's pass across accounts reads (`_registered_book`:
work/sheltered_base.json, which the stage table's gains_all and
gains_all_blend rows declare): a sale in any year sees a registered
account's purchase in its 61-day window, so the rows inside the tax year
equal the filing file's, denials included (backlog TJ-5347; TJ-0677's
contract).

The demo (`tjs init --demo`: its margin INTC loss bought back in the
rrsp 14 days later); a two-year project whose earlier year's loss is
bought back in an RRSP; the registered book changed by `tjs run
--account rrsp` (no pass across accounts runs), the next view's
full-history file following it; a Canadian crypto account's loss
denied through a registered crypto account's purchase; a US blend with
an IRA through the engine (the IRA's part disallowed for good, the rest
in the taxable replacement's cost), and a US crypto account that reads
no registered book (§1091 does not reach digital assets) while the
equity accounts do; what the note says is checked (taxable accounts,
registered ones too, or no rule), and the basis label: what is checked,
the tax year's and the full history's alike (TJ-5471, TJ-5473), a lone
Canadian crypto account's included, and with no LEAPS contract (TJ-5474),
as the run recorded it (pipeline/run `_record_loss_checks`): accounts built
by `tjs run --account` alone, a registered account not yet built or added
later, taxjson.toml edited since the run (an edit no account's type
depends on, an account added, removed or retyped), a US crypto blend, `tjs
harvest`; a full-history file by what its own build read (the view builds
it, no run does: src/taxjson/pipeline/basis_label.py `build_check`),
books with no record of the run's (an older taxjson's, a run that failed),
a run that stops in its reports, and one waiting on elections (the verify's
V1 to V4); a record that cannot be written, and an in-kind contribution
booked after the registered book was merged (the second verify's N1, N2).
Synthetic data only.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from _qa_project import ENV
from _support.ib_merger_election import _SSL_RGLD_CSV
from _support.transfer_in_kind import _QT, _ib, _xfer_qt
from _support.transfer_in_kind import _project as _in_kind_project
from _tmpfiles import private_dir

from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.records import record_path, sha256_file

_ENV = dict(ENV, TAXJSON_WIDTH="0")
_SHELTERED = "work/sheltered_base.json"


def _tjs(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(python_module_argv("taxjson.bin.taxjson_run", list(args)),
                       cwd=cwd, capture_output=True, text=True, env=_ENV,
                       stdin=subprocess.DEVNULL, timeout=600, check=False)
    if r.returncode != 0:
        raise AssertionError(f"tjs {' '.join(args)}: rc {r.returncode}\n"
                             f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return r


def _json(cwd: Path, *args: str) -> dict:
    return json.loads(_tjs(cwd, *args, "--json").stdout)


def _denied(doc: dict) -> tuple:
    return doc["denied_count"], doc["denied_total"]


_INCOME = ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST", "FEE")


def _in_2024(t: dict) -> bool:
    """A row of 2024 as the gains run's year filter keeps it (the demo's
    tax date is the settlement date; income by the day it is paid)."""
    if t.get("action") in _INCOME:
        return str(t.get("income_date") or t.get("date") or "").startswith("2024")
    return str(t.get("date_settle") or t.get("date") or "").startswith("2024")


def _key(t: dict) -> str:
    return json.dumps(t, sort_keys=True)


def _rows(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))["transactions"]


def _row(path: Path, symbol: str, date: str) -> dict:
    (row,) = [t for t in _rows(path) if t.get("symbol") == symbol and t.get("date") == date]
    return row


_CANADA = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
           'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n')
_TOML = _CANADA + '[accounts.margin]\ntype = "taxable"\n[accounts.rrsp]\ntype = "sheltered"\n'
# 2024 (before the tax year): 100 bought at 10 and sold at 8, a loss of
# 200; the RRSP buys 50 seven days later and holds them, so half the loss
# is denied for good (s.54; the registered account's purchase). 2025: a
# small gain, so the tax year has a sale.
_MARGIN = ("BUYSELL 2024-03-01 09:30:00 QZL.TO 100 CAD 10.00 1000.00 0\n"
           "BUYSELL 2024-09-03 09:30:00 QZL.TO -100 CAD 8.00 800.00 0\n"
           "BUYSELL 2025-02-03 09:30:00 QZM.TO 10 CAD 5.00 50.00 0\n"
           "BUYSELL 2025-05-01 09:30:00 QZM.TO -10 CAD 6.00 60.00 0\n")
_RRSP_IN_WINDOW = "BUYSELL 2024-09-10 09:30:00 QZL.TO 50 CAD 8.00 400.00 0\n"
# The same purchase two months later: outside the loss's window.
_RRSP_AFTER = "BUYSELL 2024-11-15 09:30:00 QZL.TO 50 CAD 8.00 400.00 0\n"


def _project(name: str, toml: str, books: dict, *, run: bool = True) -> Path:
    """A project of one .tt book per account ({account: text}; a key
    `account/file` names the export), run in full unless not `run`."""
    root = Path(private_dir()) / name
    for account, text in books.items():
        path = root / "inputs" / (account if "/" in account else f"{account}/trades.tt")
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
    (root / "taxjson.toml").write_text(toml, encoding="utf-8")
    (root / "ticker.map").write_text("", encoding="utf-8")
    if run:
        _tjs(root, "run", "--no-input")
    return root


def _two_years(name: str) -> Path:
    return _project(name, _TOML, {"margin": _MARGIN, "rrsp": _RRSP_IN_WINDOW})


class TestTheDemo(unittest.TestCase):
    """The backlog's repro: the tax year's INTC sale keeps its denial in a
    window that reaches outside the tax year."""

    @classmethod
    def setUpClass(cls):
        demo = Path(private_dir()) / "demo"
        _tjs(Path(private_dir()), "init", "--demo", str(demo))
        cls.root = demo / "2024"
        _tjs(cls.root, "run", "--no-input")

    def test_stats_all_keeps_the_tax_years_denial(self):
        year = _json(self.root, "stats", "margin")
        whole = _json(self.root, "stats", "all", "margin")
        self.assertEqual(_denied(year), (1, 394.2))
        self.assertEqual(_denied(whole), (1, 394.2))
        # The same trades and economic P/L (before any denial) as before.
        self.assertEqual(*[{k: (c["trades"], c["net_pl"]) for k, c in d["classes"].items()}
                           for d in (whole, year)])
        row = _row(self.root / "work/margin_gains_all.json", "INTC.US", "2024-08-05")
        self.assertEqual(row["disallowed_amount"], 394.2)
        self.assertEqual(row["wash_trigger"]["trigger_account"], "rrsp")
        self.assertTrue(row["wash_trigger"]["trigger_sheltered"])

    def test_the_rows_inside_the_tax_year_equal_the_filing_files(self):
        # Every taxable account's: the four the blend pools and the crypto
        # account's own pass with the registered book.
        _tjs(self.root, "stats", "all")
        work = self.root / "work"
        for account in ("margin", "ib", "questrade", "webull", "crypto"):
            with self.subTest(account=account):
                filed = json.loads((work / f"{account}_gains_wash.json").read_text(
                    encoding="utf-8"))["transactions"]
                whole = [t for t in json.loads((work / f"{account}_gains_all.json").read_text(
                    encoding="utf-8"))["transactions"] if _in_2024(t)]
                self.assertEqual(sorted(map(_key, whole)), sorted(map(_key, filed)))

    def test_the_records_name_the_registered_book(self):
        # The blend's full history (`_gains_all_blend`) and the crypto
        # account's own (`_gains_all_file`).
        _tjs(self.root, "stats", "all")
        for out in (".blend_gains_all.json", "crypto_gains_all.json"):
            rec = json.loads(record_path(self.root / "work" / out).read_text(encoding="utf-8"))
            with self.subTest(out=out):
                self.assertEqual(rec["inputs"][_SHELTERED], sha256_file(self.root / _SHELTERED))


class TestAnEarlierYearsSale(unittest.TestCase):
    """A 2024 loss in a 2025 project, bought back in the RRSP inside its
    window: denied in the full-history file as the 2024 project's filing
    file would deny it."""

    @classmethod
    def setUpClass(cls):
        cls.root = _two_years("earlier")

    def test_the_earlier_years_sale_is_denied(self):
        self.assertEqual(_denied(_json(self.root, "stats", "2024")), (1, 100.0))
        self.assertEqual(_denied(_json(self.root, "stats", "--all-history")), (1, 100.0))
        row = _row(self.root / "work/margin_gains_all.json", "QZL.TO", "2024-09-03")
        self.assertEqual(row["disallowed_amount"], 100.0)
        self.assertEqual(row["gain"], -100.0)
        self.assertEqual(row["wash_trigger"]["trigger_account"], "rrsp")

    def test_the_note_says_what_is_checked(self):
        # margin's sales against both accounts' purchases; the rrsp's own
        # sales under no rule, named; the rrsp alone: no rule said.
        r = _tjs(self.root, "stats", "2024")
        self.assertIn("a purchase in any of your taxable or registered accounts is "
                      "checked, in every year (no rule for the sales in rrsp).", r.stderr)
        self.assertNotIn("checked for the tax year", r.stderr)
        r = _tjs(self.root, "stats", "2024", "rrsp")
        self.assertIn("gains are; no superficial-loss / wash-sale rule applies to these "
                      "accounts' sales. A view", r.stderr)
        self.assertNotIn("is checked", r.stderr)

    def test_the_label_follows_the_tax_years(self):
        # The basis label as the tax year's for the same accounts, over
        # every year (TJ-5471): the rrsp alone "pre-wash", margin and a
        # mix "wash-adjusted".
        for window, account, basis in (("2024", "rrsp", "pre-wash, full history"),
                                       ("2024", "margin", "wash-adjusted, full history"),
                                       ("2024", "margin,rrsp", "wash-adjusted, full history"),
                                       ("2025", "rrsp", "pre-wash"),
                                       ("2025", "margin", "wash-adjusted")):
            with self.subTest(window=window, account=account):
                self.assertEqual(_json(self.root, "losers", window, account)["basis"], basis)

    def test_no_leaps_contract_labels_the_window(self):
        # TJ-5474: with no LEAPS contract at all the empty document named
        # the tax year's basis under the full-history note.
        for args, basis in ((("leaps", "2024"), "wash-adjusted, full history"),
                            (("leaps", "2024", "rrsp"), "pre-wash, full history"),
                            (("leaps-sum", "2024"), "wash-adjusted, full history"),
                            (("leaps",), "wash-adjusted")):
            with self.subTest(args=args):
                doc = _json(self.root, *args)
                self.assertEqual((doc["rows"], doc["basis"]), ([], basis))


_LONE_COINS = ("BUYSELL 2024-03-01 09:30:00 QZC 2 CAD 100.00 200.00 0\n"
               "BUYSELL 2024-09-03 09:30:00 QZC -2 CAD 80.00 160.00 0\n"
               "BUYSELL 2024-09-10 09:30:00 QZC 1 CAD 80.00 80.00 0\n"
               "BUYSELL 2025-02-03 09:30:00 QZD 1 CAD 50.00 50.00 0\n"
               "BUYSELL 2025-03-03 09:30:00 QZD -1 CAD 40.00 40.00 0\n"
               "BUYSELL 2025-03-10 09:30:00 QZD 1 CAD 40.00 40.00 0\n")


class TestALoneCanadianCryptoAccount(unittest.TestCase):
    """One Canadian crypto account and no registered account: the run gives
    it no wash pass (its own pass is the whole check), so its losses are
    denied in its plain gains file; the label says "wash-adjusted" over
    them, the tax year's and the full history's (TJ-5473: "pre-wash",
    TJ-0452's symptom). A 2024 loss of 40 on 2 coins, 1 bought back a week
    later (20 denied); a 2025 loss of 10 bought back (denied)."""

    @classmethod
    def setUpClass(cls):
        cls.root = _project("lonecoin", _CANADA + '[accounts.coins]\ntype = "taxable"\n'
                            'crypto = true\n', {"coins": _LONE_COINS})

    def test_the_run_gives_it_no_wash_pass(self):
        work = self.root / "work"
        self.assertFalse((work / "coins_gains_wash.json").exists())
        self.assertEqual(_row(work / "coins_gains.json", "QZD", "2025-03-03")["disallowed_amount"],
                         10.0)

    def test_the_tax_years_label(self):
        self.assertEqual(_denied(_json(self.root, "stats", "coins")), (1, 10.0))
        for cmd in ("losers", "wash-sales", "gains-base"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_json(self.root, cmd, "coins")["basis"], "wash-adjusted")

    def test_the_full_historys_label(self):
        doc = _json(self.root, "losers", "all", "coins")
        self.assertEqual((doc["basis"], doc["rows"][0]["ticker"]),
                         ("wash-adjusted, full history", "QZC"))
        r = _tjs(self.root, "losers", "all", "coins")
        self.assertIn("basis: wash-adjusted, full history;", r.stdout)
        self.assertIn("a purchase in any of your taxable accounts is checked", r.stderr)

    def test_harvest_says_it(self):
        # `tjs harvest` gives taxjson-harvest the label (--basis-label): the
        # file name alone says "pre-wash" (3b97801f's).
        _prices(self.root, "QZC", "QZD")
        doc = _json(self.root, "harvest", "--crypto", "--no-ibkr")
        self.assertEqual(doc["totals"]["basis"], "wash-adjusted")


_M1 = ("BUYSELL 2024-03-01 09:30:00 QZK.TO 100 CAD 10.00 1000.00 0\n"
       "BUYSELL 2024-09-03 09:30:00 QZK.TO -100 CAD 8.00 800.00 0\n"
       "BUYSELL 2025-02-03 09:30:00 QZL.TO 100 CAD 10.00 1000.00 0\n"
       "BUYSELL 2025-06-02 09:30:00 QZL.TO -100 CAD 8.00 800.00 0\n")
# m2 buys back half of m1's losses a week after each: a full run denies 100
# of each.
_M2 = ("BUYSELL 2024-09-10 09:30:00 QZK.TO 50 CAD 8.00 400.00 0\n"
       "BUYSELL 2025-06-09 09:30:00 QZL.TO 50 CAD 8.00 400.00 0\n")
# A 2025 crypto loss of 20; the registered account's purchase a week after
# denies 10 (a full run's pass).
_COIN_LOSS = ("BUYSELL 2025-02-03 09:30:00 QZD 2 CAD 50.00 100.00 0\n"
              "BUYSELL 2025-03-03 09:30:00 QZD -2 CAD 40.00 80.00 0\n")
_RRSP_COIN = "BUYSELL 2025-03-08 09:30:00 QZD 1 CAD 40.00 40.00 0\n"
_COINS = '[accounts.coins]\ntype = "taxable"\ncrypto = true\n'
_RRSP = '[accounts.rrsp]\ntype = "sheltered"\n'
# A registered purchase of nothing a taxable account trades.
_RRSP_OTHER = "BUYSELL 2025-03-10 09:30:00 QZM.TO 10 CAD 5.00 50.00 0\n"


def _prices(root: Path, *symbols: str) -> None:
    """`tjs harvest`'s price cache (work/.price_cache.json), current, so it
    runs offline: a crypto coin's quote spelling SYM-USD."""
    from datetime import date
    doc = {s: {"price": 6.0, "asof": date.today().isoformat(), "source": "yfinance",
               "currency": "CAD", "quote_symbol": s if "." in s else f"{s}-USD"}
           for s in symbols}
    (root / "work/.price_cache.json").write_text(json.dumps(doc), encoding="utf-8")


def _basis(root: Path, *args: str) -> str:
    return _json(root, *args)["basis"]


class TestNoFullRun(unittest.TestCase):
    """Every account built by `tjs run --account` alone, never a full run:
    the pass across accounts never saw m1's losses, which m2 bought back.
    The label says "pre-wash" for a set with m1 or m2, in the tax year and
    over every year; the lone crypto account's own pass is the whole check
    (the run's record: pipeline/run `_record_loss_checks`). On this round's
    start tree a set with the crypto account said "wash-adjusted", and a
    full-history set "wash-adjusted, full history", over the undenied
    losses (the review's F1, F3)."""

    @classmethod
    def setUpClass(cls):
        cls.root = _project("nofull", _CANADA + '[accounts.m1]\ntype = "taxable"\n'
                            '[accounts.m2]\ntype = "taxable"\n' + _COINS,
                            {"m1": _M1, "m2": _M2, "coins": _LONE_COINS}, run=False)
        for account in ("m1", "m2", "coins"):
            _tjs(cls.root, "run", "--no-input", "--account", account)

    def test_the_tax_years_label(self):
        doc = _json(self.root, "losers", "m1")
        self.assertEqual((doc["basis"], doc["rows"][0]["ticker"], doc["rows"][0]["denied"]),
                         ("pre-wash", "QZL.TO", 0.0))
        for args, basis in ((("losers",), "pre-wash"),
                            (("losers", "m1,coins"), "pre-wash"),
                            (("wash-sales",), "pre-wash"),
                            (("losers", "coins"), "wash-adjusted")):
            with self.subTest(args=args):
                self.assertEqual(_basis(self.root, *args), basis)

    def test_the_full_historys_label(self):
        for args, basis in ((("losers", "all", "m1,m2"), "pre-wash, full history"),
                            (("losers", "all", "m1"), "pre-wash, full history"),
                            (("losers", "all", "coins"), "wash-adjusted, full history")):
            with self.subTest(args=args):
                self.assertEqual(_basis(self.root, *args), basis)

    def test_harvest_says_it(self):
        _prices(self.root, "QZK.TO", "QZL.TO", "QZC", "QZD")
        doc = _json(self.root, "harvest", "--crypto", "--no-ibkr")
        self.assertEqual(doc["totals"]["basis"], "pre-wash")


class TestALoneCryptoAccountAndARegisteredOne(unittest.TestCase):
    """A Canadian crypto account and a registered account: its own pass is
    the whole check only while no registered account has inputs (the run's
    own test, pipeline/run `_record_loss_checks`), else the label waits for
    the pass that reads the registered book. On this round's start tree
    (the label read "no work/sheltered_base.json") each said
    "wash-adjusted" over the loss the registered purchase makes superficial
    (the review's F2)."""

    def test_built_alone_it_waits_for_the_pass(self):
        root = _project("coinrrsp", _CANADA + _COINS + _RRSP,
                        {"coins": _COIN_LOSS, "rrsp": _RRSP_COIN}, run=False)
        _tjs(root, "run", "--no-input", "--account", "coins")
        self.assertEqual(_denied(_json(root, "stats", "coins")), (0, 0.0))
        self.assertEqual(_basis(root, "losers", "coins"), "pre-wash")
        self.assertEqual(_basis(root, "losers", "all", "coins"), "pre-wash, full history")
        _tjs(root, "run", "--no-input")
        self.assertEqual(_denied(_json(root, "stats", "coins")), (1, 10.0))
        self.assertEqual(_basis(root, "losers", "coins"), "wash-adjusted")

    def test_an_export_put_in_its_folder(self):
        root = _project("coinempty", _CANADA + _COINS + _RRSP, {"coins": _COIN_LOSS})
        self.assertEqual(_basis(root, "losers", "coins"), "wash-adjusted")
        (root / "inputs/rrsp").mkdir()
        (root / "inputs/rrsp/trades.tt").write_text(_RRSP_COIN, encoding="utf-8")
        self.assertEqual(_basis(root, "losers", "coins"), "pre-wash")
        _tjs(root, "run", "--no-input")
        self.assertEqual(_basis(root, "losers", "coins"), "wash-adjusted")
        self.assertEqual(_denied(_json(root, "stats", "coins")), (1, 10.0))

    def test_one_added_later_not_yet_run(self):
        root = _project("coinadded", _CANADA + _COINS, {"coins": _COIN_LOSS})
        self.assertEqual(_basis(root, "losers", "coins"), "wash-adjusted")
        with open(root / "taxjson.toml", "a", encoding="utf-8") as f:
            f.write(_RRSP)
        (root / "inputs/rrsp").mkdir()
        (root / "inputs/rrsp/trades.tt").write_text(_RRSP_COIN, encoding="utf-8")
        self.assertEqual(_basis(root, "losers", "coins"), "pre-wash")
        self.assertEqual(_basis(root, "losers", "all", "coins"), "pre-wash, full history")


_M1_M2 = '[accounts.m1]\ntype = "taxable"\n[accounts.m2]\ntype = "taxable"\n'


# m1 beside a registered account that buys back half of each of its losses
# four days after (as m2 does in the two-account projects).
_RRSP_STOCK = ("BUYSELL 2024-09-06 09:30:00 QZK.TO 50 CAD 8.00 400.00 0\n"
               "BUYSELL 2025-06-06 09:30:00 QZL.TO 50 CAD 8.00 400.00 0\n")
_M1_RRSP = _CANADA + '[accounts.m1]\ntype = "taxable"\n' + _RRSP
_RUN_RECORD = "work/.deps/run.json"


def _checks(root: Path) -> dict:
    """{account: (file, check)} of the run's record of the tax year's files."""
    doc = json.loads((root / _RUN_RECORD).read_text(encoding="utf-8"))
    return {a: (e["tax_year"]["file"], e["tax_year"]["check"])
            for a, e in doc["loss_checks"]["accounts"].items()}


class TestAFullHistoryFileByItsOwnBuild(unittest.TestCase):
    """The views build `<account>_gains_all.json`, from the books work/
    holds, so its label says what THAT build read (recorded in the file's
    own record: lib/report_model `record_full_history_check`), not what the
    run recorded of the tax year's file. The verify's V3: m1 and the
    registered account each built by `tjs run --account`; no pass across
    accounts ran, so the tax year's file is "pre-wash" with no denial; the
    full-history build reads m1, the only taxable account, and the
    registered book: the whole check, its rows denied, and it said
    "pre-wash, full history" over them."""

    def test_the_build_read_the_whole_check(self):
        root = _project("ownbuild", _M1_RRSP, {"m1": _M1, "rrsp": _RRSP_STOCK}, run=False)
        _tjs(root, "run", "--no-input", "--account", "m1")
        # The registered account has inputs and no book yet: part of the check.
        doc = _json(root, "losers", "all", "m1")
        self.assertEqual((doc["basis"], sum(r["denied"] for r in doc["rows"])),
                         ("pre-wash, full history", 0.0))
        _tjs(root, "run", "--no-input", "--account", "rrsp")
        doc = _json(root, "losers", "m1")
        self.assertEqual((doc["basis"], sum(r["denied"] for r in doc["rows"])), ("pre-wash", 0.0))
        doc = _json(root, "losers", "all", "m1")
        self.assertEqual((doc["basis"], sum(r["denied"] for r in doc["rows"])),
                         ("wash-adjusted, full history", 200.0))
        rec = json.loads(record_path(root / "work/m1_gains_all.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["loss_check"]["check"], "pass")
        self.assertEqual(rec["loss_check"]["of"]["sha256"],
                         sha256_file(root / "work/m1_gains_all.json"))
        # A registered account's own sales: no rule, alone or beside m1.
        self.assertEqual(_basis(root, "losers", "all", "rrsp"), "pre-wash, full history")
        self.assertEqual(_basis(root, "losers", "all"), "wash-adjusted, full history")
        self.assertEqual(_checks(root)["m1"], ("m1_gains.json", "not_yet"))

    def test_a_second_registered_account_the_book_was_not_merged_from(self):
        # The book the build read must hold every registered account with
        # inputs: one added since is not in it.
        root = _project("ownbuild2", _M1_RRSP, {"m1": _M1, "rrsp": _RRSP_STOCK})
        self.assertEqual(_basis(root, "losers", "all", "m1"), "wash-adjusted, full history")
        with open(root / "taxjson.toml", "a", encoding="utf-8") as f:
            f.write('[accounts.tfsa]\ntype = "sheltered"\n')
        self.assertEqual(_basis(root, "losers", "all", "m1"), "wash-adjusted, full history")
        (root / "inputs/tfsa").mkdir()
        (root / "inputs/tfsa/trades.tt").write_text(_RRSP_OTHER, encoding="utf-8")
        self.assertEqual(_basis(root, "losers", "all", "m1"), "pre-wash, full history")
        _tjs(root, "run", "--no-input", "--account", "tfsa")
        self.assertEqual(_basis(root, "losers", "all", "m1"), "wash-adjusted, full history")

    def test_a_blend_that_is_short_of_a_taxable_account(self):
        # m1 and m2 blended by the full run's pass; m3 added and built
        # alone: the full-history build blends the pass's members, not m3.
        root = _project("ownbuild3", _CANADA + _M1_M2, {"m1": _M1, "m2": _M2})
        self.assertEqual(_basis(root, "losers", "all"), "wash-adjusted, full history")
        with open(root / "taxjson.toml", "a", encoding="utf-8") as f:
            f.write('[accounts.m3]\ntype = "taxable"\n')
        (root / "inputs/m3").mkdir()
        (root / "inputs/m3/trades.tt").write_text(_RRSP_OTHER, encoding="utf-8")
        self.assertEqual(_basis(root, "losers", "all", "m1"), "pre-wash, full history")
        _tjs(root, "run", "--no-input", "--account", "m3")
        self.assertEqual(_basis(root, "losers", "all", "m1,m2"), "pre-wash, full history")
        _tjs(root, "run", "--no-input")
        self.assertEqual(_basis(root, "losers", "all"), "wash-adjusted, full history")


class TestARecordThatCannotBeWritten(unittest.TestCase):
    """The second verify's N1: work/.deps not writable. The full-history
    file's record kept the earlier build's check, and the rebuilt bytes
    were the same, so the label read "wash-adjusted, full history" after a
    taxable account was added and not built. The command that builds the
    file takes the label from its own build; a record it cannot write is
    removed where that is possible."""

    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("root writes a folder whatever its mode")
        self.root = _project("nowrite", _CANADA + _M1_M2, {"m1": _M1, "m2": _M2})
        self.assertEqual(_basis(self.root, "losers", "all", "m1"), "wash-adjusted, full history")
        with open(self.root / "taxjson.toml", "a", encoding="utf-8") as f:
            f.write('[accounts.m3]\ntype = "taxable"\n')
        (self.root / "inputs/m3").mkdir()
        (self.root / "inputs/m3/trades.tt").write_text(
            "BUYSELL 2025-06-10 09:30:00 QZL.TO 50 CAD 8.00 400.00 0\n", encoding="utf-8")
        self.deps = self.root / "work/.deps"
        self.record = record_path(self.root / "work/m1_gains_all.json")

    def test_the_folder(self):
        self.deps.chmod(0o500)
        self.addCleanup(self.deps.chmod, 0o700)
        self.assertEqual(_basis(self.root, "losers", "all", "m1"), "pre-wash, full history")

    def test_the_record_alone(self):
        # The folder takes a new file and a removal; the record itself
        # (and its replacement's temporary name) cannot be written over
        # in place: whatever the writer does, no earlier "pass" is left.
        self.record.chmod(0o400)
        self.assertEqual(_basis(self.root, "losers", "all", "m1"), "pre-wash, full history")
        if self.record.exists():
            rec = json.loads(self.record.read_text(encoding="utf-8"))
            self.assertEqual(rec["loss_check"]["check"], "not_yet")


class TestAnInKindContributionBookedAfterTheBook(unittest.TestCase):
    """The second verify's N2. margin sells 100 QZK.TO at a loss on 03-04
    and moves its other 100 in kind to the rrsp on 03-14: the plan's
    acquisition is a purchase in the loss's window (CA-INKIND-04), marked
    in the registered accounts' book by a run that merges it (a full run,
    `tjs run --account rrsp`). `tjs run --account margin` books the
    contribution (work/in_kind.json) and merges no book: the book the
    full-history build reads is older than the booking and lacks the mark,
    and the label said "wash-adjusted, full history" over the undenied
    loss."""
    _TRADES = [("2025-01-10", 200, 10.0), ("2025-03-04", -100, 6.0)]
    _MOVED = _ib(_TRADES, [("2025-03-14", -100, 600)])
    _RRSP = _QT + _xfer_qt("2025-03-17", 100)

    def _all(self, root: Path) -> tuple:
        doc = _json(root, "losers", "all", "margin")
        return doc["basis"], sum(r["denied"] for r in doc["rows"])

    def test_a_project_never_run_in_full(self):
        root = _in_kind_project(private_dir(), {"inputs/margin/ib.csv": _ib(self._TRADES),
                                                "inputs/rrsp/questrade.csv": self._RRSP})
        _tjs(root, "run", "--no-input", "--account", "rrsp")
        _tjs(root, "run", "--no-input", "--account", "margin")
        # No contribution yet: margin alone with the registered book is the
        # whole check (a run of either account reads the other's transfer
        # rows, so one already in margin's export would be marked here).
        self.assertEqual(self._all(root), ("wash-adjusted, full history", 0.0))
        (root / "inputs/margin/ib.csv").write_text(self._MOVED, encoding="utf-8")
        _tjs(root, "run", "--no-input", "--account", "margin")
        moves = json.loads((root / "work/in_kind.json").read_text(encoding="utf-8"))["moves"]
        self.assertEqual([(m["kind"], m["booked"]) for m in moves], [("contribution", True)])
        self.assertEqual(_basis(root, "losers", "margin"), "pre-wash")
        self.assertEqual(self._all(root), ("pre-wash, full history", 0.0))
        # The registered account's run merges the book and marks it.
        _tjs(root, "run", "--no-input", "--account", "rrsp")
        self.assertEqual(self._all(root), ("wash-adjusted, full history", 400.0))

    def test_a_project_run_in_full_before_the_contribution(self):
        # The stale class: the tax year's file is the earlier pass's, served
        # with the warning that it is stale, and keeps "wash-adjusted"; the
        # full-history build is this command's, and says what it read.
        root = _in_kind_project(private_dir(), {"inputs/margin/ib.csv": _ib(self._TRADES),
                                                "inputs/rrsp/questrade.csv": self._RRSP})
        _tjs(root, "run", "--no-input")
        self.assertEqual(self._all(root), ("wash-adjusted, full history", 0.0))
        (root / "inputs/margin/ib.csv").write_text(self._MOVED, encoding="utf-8")
        _tjs(root, "run", "--no-input", "--account", "margin")
        self.assertEqual(_basis(root, "losers", "margin"), "wash-adjusted")
        self.assertEqual(self._all(root), ("pre-wash, full history", 0.0))
        _tjs(root, "run", "--no-input")
        self.assertEqual(_basis(root, "losers", "margin"), "wash-adjusted")
        self.assertEqual(self._all(root), ("wash-adjusted, full history", 400.0))


class TestBooksWithNoRecord(unittest.TestCase):
    """work/.deps/run.json with no `loss_checks` (an older taxjson's books,
    a first run that failed, the file damaged or deleted: here deleted).
    The verify's V1: any full-history file read "wash-adjusted, full
    history" (its name), over books no pass checked. A tax-year file is
    judged by the file served, a full-history file by its own build."""

    def test_an_account_built_alone(self):
        root = _project("norec1", _M1_RRSP, {"m1": _M1, "rrsp": _RRSP_STOCK}, run=False)
        _tjs(root, "run", "--no-input", "--account", "m1")
        (root / _RUN_RECORD).unlink()
        self.assertEqual(_basis(root, "losers", "m1"), "pre-wash")
        doc = _json(root, "losers", "all", "m1")
        self.assertEqual((doc["basis"], sum(r["denied"] for r in doc["rows"])),
                         ("pre-wash, full history", 0.0))
        (root / _RUN_RECORD).write_text('{"format": 2, "inst', encoding="utf-8")
        self.assertEqual(_basis(root, "losers", "all", "m1"), "pre-wash, full history")

    def test_a_first_run_that_failed(self):
        root = _project("norec2", _CANADA + _M1_M2, {"m1": _M1, "m2": _M2 + "NOT A LINE\n"},
                        run=False)
        r = subprocess.run(python_module_argv("taxjson.bin.taxjson_run", ["run", "--no-input"]),
                           cwd=root, capture_output=True, text=True, env=_ENV,
                           stdin=subprocess.DEVNULL, timeout=600, check=False)
        self.assertEqual(r.returncode, 1, r.stderr[-2000:])
        self.assertFalse((root / _RUN_RECORD).exists())
        self.assertEqual(_basis(root, "losers"), "pre-wash")
        self.assertEqual(_basis(root, "losers", "all", "m1"), "pre-wash, full history")

    def test_a_full_runs_books(self):
        root = _project("norec3", _CANADA + _M1_M2 + _RRSP,
                        {"m1": _M1, "m2": _M2, "rrsp": _RRSP_OTHER})
        (root / _RUN_RECORD).unlink()
        self.assertEqual(_basis(root, "losers"), "wash-adjusted")
        self.assertEqual(_basis(root, "losers", "m1,rrsp"), "wash-adjusted")
        self.assertEqual(_basis(root, "losers", "rrsp"), "pre-wash")
        # A registered account alone read "wash-adjusted, full history".
        self.assertEqual(_basis(root, "losers", "all", "rrsp"), "pre-wash, full history")
        self.assertEqual(_basis(root, "losers", "all", "m1"), "wash-adjusted, full history")

    def test_then_one_partial_run(self):
        # The verify's V2: the partial run began the record with m2 alone
        # (not yet), and every label turned "pre-wash" over the files the
        # earlier pass wrote, denials in the rows.
        root = _project("norec4", _CANADA + _M1_M2 + _RRSP,
                        {"m1": _M1, "m2": _M2, "rrsp": _RRSP_OTHER})
        (root / _RUN_RECORD).unlink()
        _tjs(root, "run", "--no-input", "--account", "m2")
        self.assertEqual(_checks(root), {"m1": ("m1_gains_wash.json", "pass"),
                                         "m2": ("m2_gains_wash.json", "pass"),
                                         "rrsp": ("rrsp_gains.json", "none")})
        for args in (("losers",), ("losers", "m1"), ("wash-sales",)):
            with self.subTest(args=args):
                doc = _json(root, *args)
                self.assertEqual(doc["basis"], "wash-adjusted")
        self.assertEqual(sum(r["denied"] for r in _json(root, "losers")["rows"]), 100.0)
        self.assertEqual(_basis(root, "losers", "all"), "wash-adjusted, full history")


_STOPS_IN_ITS_REPORTS = ("import sys\n"
                         "from taxjson.pipeline import run\n"
                         "def stop(*a, **k):\n"
                         "    raise SystemExit(7)\n"
                         "run.stage_reports = stop\n"
                         "from taxjson.bin.taxjson_run import main\n"
                         "sys.argv = ['tjs', 'run', '--no-input']\n"
                         "sys.exit(main())\n")


class TestARunThatStopsAfterItsPasses(unittest.TestCase):
    """The verify's V4: the record was written after the reports, so a run
    that stopped in them (a `--strict` exit, a report that fails: here the
    report stage, replaced by an exit) left the earlier record, which names
    the plain files: "pre-wash" over the files this run's pass wrote."""

    def test_the_record_is_the_passes(self):
        import sys
        root = _project("stops", _CANADA + _M1_M2, {"m1": _M1, "m2": _M2}, run=False)
        for account in ("m1", "m2"):
            _tjs(root, "run", "--no-input", "--account", account)
        self.assertEqual(_checks(root), {"m1": ("m1_gains.json", "not_yet"),
                                         "m2": ("m2_gains.json", "not_yet")})
        r = subprocess.run([sys.executable, "-P", "-c", _STOPS_IN_ITS_REPORTS], cwd=root,
                           capture_output=True, text=True, env=_ENV,
                           stdin=subprocess.DEVNULL, timeout=600, check=False)
        self.assertEqual(r.returncode, 7, r.stderr[-2000:])
        self.assertEqual(_checks(root), {"m1": ("m1_gains_wash.json", "pass"),
                                         "m2": ("m2_gains_wash.json", "pass")})
        doc = _json(root, "losers")
        self.assertEqual((doc["basis"], sum(r["denied"] for r in doc["rows"])),
                         ("wash-adjusted", 100.0))


class TestARunWaitingOnElections(unittest.TestCase):
    """A taxable account's merger needs an election (`tjs run --no-input`
    exits 3, the account deferred): no pass across accounts runs."""
    _EV = '[accounts.ev]\ntype = "taxable"\n'

    def _waits(self, root: Path) -> None:
        r = subprocess.run(python_module_argv("taxjson.bin.taxjson_run", ["run", "--no-input"]),
                           cwd=root, capture_output=True, text=True, env=_ENV,
                           stdin=subprocess.DEVNULL, timeout=600, check=False)
        self.assertEqual(r.returncode, 3, r.stderr[-2000:])

    def test_a_first_run(self):
        root = _project("waits", _CANADA + _M1_M2 + self._EV,
                        {"m1": _M1, "m2": _M2, "ev/ib_events.csv": _SSL_RGLD_CSV}, run=False)
        self._waits(root)
        self.assertEqual(_checks(root), {"m1": ("m1_gains.json", "not_yet"),
                                         "m2": ("m2_gains.json", "not_yet")})
        self.assertEqual(_basis(root, "losers"), "pre-wash")
        self.assertEqual(_basis(root, "losers", "all", "m1"), "pre-wash, full history")

    def test_after_books_with_no_record(self):
        # The earlier pass's files are still the ones served (V1, V2: the
        # deferred run's record began empty, and they read "pre-wash"); the
        # full-history build has no book of the account that waits.
        root = _project("waits2", _CANADA + _M1_M2, {"m1": _M1, "m2": _M2})
        (root / _RUN_RECORD).unlink()
        with open(root / "taxjson.toml", "a", encoding="utf-8") as f:
            f.write(self._EV)
        (root / "inputs/ev").mkdir()
        (root / "inputs/ev/ib_events.csv").write_text(_SSL_RGLD_CSV, encoding="utf-8")
        self._waits(root)
        self.assertEqual(_checks(root), {"m1": ("m1_gains_wash.json", "pass"),
                                         "m2": ("m2_gains_wash.json", "pass")})
        self.assertEqual(_basis(root, "losers", "m1,m2"), "wash-adjusted")
        self.assertEqual(_basis(root, "losers", "all", "m1,m2"), "pre-wash, full history")


class TestTaxjsonTomlEdited(unittest.TestCase):
    """taxjson.toml edited since the full run (the books are not current:
    the staleness banner says so): what the record says of a file still
    served holds while what it depends on is unchanged (lib/report_model
    `_recorded_checks`). The pass's check of m1 (its 2025 loss denied
    through m2) holds through every edit; the lone crypto account's own
    check held for the registered accounts the run saw; a registered
    account's no rule for its type. On round 4's tree any edit said
    "pre-wash" over the denial (the TJ-0452 symptom)."""

    def _built(self, toml: str, books: dict) -> None:
        self.root = _project("edited", _CANADA + toml, books)

    def _with_coins(self) -> None:
        # rrsp has no inputs: the crypto account's own pass is its check.
        self._built(_M1_M2 + _COINS + _RRSP, {"m1": _M1, "m2": _M2, "coins": _COIN_LOSS})

    def _labels(self, *sets: str) -> list:
        return [_basis(self.root, "losers", s) for s in sets]

    def _toml(self, text: str, *, append: bool = False) -> None:
        with open(self.root / "taxjson.toml", "a" if append else "w", encoding="utf-8") as f:
            f.write(text if append else _CANADA + text)

    def test_an_edit_no_account_depends_on(self):
        self._with_coins()
        self.assertEqual(self._labels("m1", "coins"), ["wash-adjusted", "wash-adjusted"])
        self.assertEqual(_denied(_json(self.root, "stats", "m1")), (1, 100.0))
        self._toml("# a note\n", append=True)
        self.assertEqual(self._labels("m1", "coins", "m1,m2,coins"),
                         ["wash-adjusted", "wash-adjusted", "wash-adjusted"])
        self.assertEqual(_basis(self.root, "losers", "all", "m1,m2"),
                         "wash-adjusted, full history")

    def test_a_registered_account_added(self):
        self._with_coins()
        self._toml('[accounts.tfsa]\ntype = "sheltered"\n', append=True)
        self.assertEqual(self._labels("m1", "coins", "m1,m2"),
                         ["wash-adjusted", "pre-wash", "wash-adjusted"])

    def test_an_account_removed(self):
        self._with_coins()
        self._toml('[accounts.m1]\ntype = "taxable"\n' + _COINS + _RRSP)
        self.assertEqual(self._labels("m1", "coins"), ["wash-adjusted", "wash-adjusted"])
        # Every account's view still reads m2's books until a run sweeps
        # them: the pass's check is still true of the file served.
        self.assertEqual(_basis(self.root, "losers"), "wash-adjusted")
        # The registered account removed: the own check held for it.
        self._toml('[accounts.m1]\ntype = "taxable"\n' + _COINS)
        self.assertEqual(self._labels("m1", "coins"), ["wash-adjusted", "pre-wash"])

    def test_a_type_change(self):
        self._built(_M1_M2 + _RRSP, {"m1": _M1, "m2": _M2, "rrsp": _RRSP_OTHER})
        self.assertEqual(self._labels("m1", "m1,rrsp"), ["wash-adjusted", "wash-adjusted"])
        # rrsp made taxable: its file was built as a registered account's,
        # whose sales no rule reaches; nothing checked them as taxable ones.
        self._toml(_M1_M2 + '[accounts.rrsp]\ntype = "taxable"\n')
        self.assertEqual(self._labels("m1", "m1,rrsp"), ["wash-adjusted", "pre-wash"])
        # m2 made registered: the pass's file is still the one served.
        self._toml('[accounts.m1]\ntype = "taxable"\n[accounts.m2]\ntype = "sheltered"\n'
                   + _RRSP)
        self.assertEqual(self._labels("m1,m2", "m1,rrsp"), ["wash-adjusted", "wash-adjusted"])


_CB = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,"
       "Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),"
       "Fees and/or Spread,Notes\n")


class TestAUsCryptoBlend(unittest.TestCase):
    """Two US crypto accounts with a move of coins between them: the run's
    blended crypto pass carries the lots (US-CRYPTO-05) under no wash-sale
    rule (§1091 does not reach digital assets), so its wash-adjusted files
    are "pre-wash", the tax year's and the full history's (3b97801f read
    the file name: "wash-adjusted")."""

    def test_the_blend_is_under_no_rule(self):
        root = _project("uscblend", '[settings]\nlocal_timezone = "America/New_York"\n'
                        'year = 2025\ncountry = "usa"\nbase_currency = "USD"\n'
                        'source_currencies = []\n[accounts.bkr]\ntype = "taxable"\n'
                        'crypto = true\n[accounts.acoin]\ntype = "taxable"\ncrypto = true\n', {
                            "bkr/cb_bkr.csv": _CB + (
                                "u1,2025-05-01 12:00:00 UTC,Buy,QZB,1,USD,900,900,900,0,Bought\n"
                                "u2,2025-07-01 12:00:00 UTC,Send,QZB,1,USD,800,800,800,0,Sent\n"
                                "u3,2025-07-12 12:00:00 UTC,Buy,QZB,1,USD,700,700,700,0,Bought\n"),
                            "acoin/cb_acoin.csv": _CB + (
                                "v1,2025-07-01 13:00:00 UTC,Receive,QZB,1,USD,800,800,800,0,In\n"
                                "v2,2025-07-10 12:00:00 UTC,Sell,QZB,1,USD,700,700,700,0,Sold\n")})
        self.assertTrue((root / "work/acoin_gains_wash.json").is_file())
        for args, basis in ((("losers", "acoin"), "pre-wash"),
                            (("losers", "bkr,acoin"), "pre-wash"),
                            (("losers", "all", "acoin"), "pre-wash, full history"),
                            (("gains-base", "acoin"), "pre-wash"),
                            (("sum", "acoin"), "pre-wash")):
            with self.subTest(args=args):
                self.assertEqual(_basis(root, *args), basis)


class TestNoRegisteredAccount(unittest.TestCase):
    """A project with no registered account has no registered book: the
    note names the taxable accounts alone."""

    def test_the_note_names_the_taxable_accounts(self):
        root = _project("taxable", _CANADA + '[accounts.margin]\ntype = "taxable"\n',
                        {"margin": _MARGIN})
        self.assertFalse((root / _SHELTERED).exists())
        r = _tjs(root, "stats", "2024")
        self.assertIn("a purchase in any of your taxable accounts is checked, in every "
                      "year. A view, not a filing figure.", r.stderr)


class TestACanadianCryptoAccount(unittest.TestCase):
    """One Canadian crypto account (its own wash pass with the registered
    book, stage_wash_pass): a 2024 loss of 40 on 2 coins, 1 bought back
    in a registered crypto account a week later and held, so half the
    loss is denied (s.54, the registered account's purchase)."""

    def test_the_registered_purchase_denies_the_loss(self):
        root = _project("coins", _CANADA + '[accounts.coins]\ntype = "taxable"\n'
                        'crypto = true\n[accounts.rcoin]\ntype = "sheltered"\ncrypto = true\n', {
                            "coins": ("BUYSELL 2024-03-01 09:30:00 QZC 2 CAD 100.00 200.00 0\n"
                                      "BUYSELL 2024-09-03 09:30:00 QZC -2 CAD 80.00 160.00 0\n"
                                      "BUYSELL 2025-02-03 09:30:00 QZD 1 CAD 50.00 50.00 0\n"
                                      "BUYSELL 2025-05-01 09:30:00 QZD -1 CAD 60.00 60.00 0\n"),
                            "rcoin": "BUYSELL 2024-09-10 09:30:00 QZC 1 CAD 80.00 80.00 0\n"})
        self.assertEqual(_denied(_json(root, "stats", "all", "coins")), (1, 20.0))
        row = _row(root / "work/coins_gains_all.json", "QZC", "2024-09-03")
        self.assertEqual((row["disallowed_amount"], row["gain"]), (20.0, -20.0))
        self.assertEqual(row["wash_trigger"]["trigger_account"], "rcoin")


_US = ('[settings]\nlocal_timezone = "America/New_York"\nyear = 2025\n'
       'country = "usa"\nbase_currency = "USD"\nsource_currencies = []\n'
       '[accounts.margin]\ntype = "taxable"\n[accounts.brk]\ntype = "taxable"\n'
       '[accounts.coins]\ntype = "taxable"\ncrypto = true\n'
       '[accounts.ira]\ntype = "sheltered"\n')
# 2024 (before the tax year): margin sells 50 at a loss of 2 a share. The
# IRA bought 30 a week before and brk buys 40 a week after; replacements
# match in order of acquisition (Reg. 1.1091-1(c)), the IRA's 30 first:
# 60 is disallowed for good, no basis added (Rev. Rul. 2008-5), and 40
# goes into the cost of 20 of brk's 40, sold in 2025. coins: a crypto
# loss bought back a week later, which no wash-sale rule reaches.
_US_BOOKS = {
    "margin": ("BUYSELL 2024-03-01 09:30:00 QZA 50 USD 10.00 500.00 0\n"
               "BUYSELL 2024-09-03 09:30:00 QZA -50 USD 8.00 400.00 0\n"),
    "brk": ("BUYSELL 2024-09-10 09:30:00 QZA 40 USD 8.00 320.00 0\n"
            "BUYSELL 2025-02-03 09:30:00 QZA -40 USD 9.00 360.00 0\n"),
    "ira": "BUYSELL 2024-08-27 09:30:00 QZA 30 USD 8.00 240.00 0\n",
    "coins": ("BUYSELL 2024-03-01 09:30:00 QZC 2 USD 100.00 200.00 0\n"
              "BUYSELL 2024-09-03 09:30:00 QZC -2 USD 80.00 160.00 0\n"
              "BUYSELL 2024-09-10 09:30:00 QZC 2 USD 80.00 160.00 0\n"),
}


class TestAUsBlendWithAnIra(unittest.TestCase):
    """The US engine end to end: margin and brk blended (the run's
    blended pass, `_gains_all_blend`), an IRA's purchase checked in an
    earlier year; a crypto account under no rule."""

    @classmethod
    def setUpClass(cls):
        cls.root = _project("usira", _US, _US_BOOKS)
        cls.work = cls.root / "work"
        # Every account's full-history file, built for this view.
        cls.stats = _json(cls.root, "stats", "2024")

    def test_the_iras_part_is_disallowed_for_good(self):
        self.assertEqual(_denied(self.stats), (1, 100.0))
        row = _row(self.work / "margin_gains_all.json", "QZA", "2024-09-03")
        self.assertEqual((row["disallowed_amount"], row["permanently_disallowed"], row["gain"]),
                         (100.0, 60.0, 0.0))

    def test_only_the_taxable_part_reaches_brks_cost(self):
        def sold(name):
            return sorted((t["cost"], t["gain"]) for t in _rows(self.work / name)
                          if t.get("symbol") == "QZA" and t.get("date") == "2025-02-03")
        self.assertEqual(sold("brk_gains_all.json"), [(160.0, 20.0), (200.0, -20.0)])
        self.assertEqual(sold("brk_gains_all.json"), sold("brk_gains_wash.json"))

    def test_the_crypto_account_is_under_no_rule(self):
        r = _tjs(self.root, "stats", "2024", "coins")
        self.assertIn("no superficial-loss / wash-sale rule applies to these accounts' "
                      "sales.", r.stderr)
        row = _row(self.work / "coins_gains_all.json", "QZC", "2024-09-03")
        self.assertEqual(row["gain"], -40.0)
        self.assertFalse(row.get("disallowed_amount"))
        r = _tjs(self.root, "stats", "2024")
        self.assertIn("checked, in every year (no rule for the sales in coins, ira).", r.stderr)
        # No rule reaches its sales: "pre-wash", as its tax year's
        # (TJ-5471, TJ-5473); the equity accounts' "wash-adjusted".
        self.assertEqual(_json(self.root, "losers", "2024", "coins")["basis"],
                         "pre-wash, full history")
        self.assertIn("basis: pre-wash, full history;",
                      _tjs(self.root, "losers", "2024", "coins").stdout)
        self.assertEqual(_json(self.root, "losers", "2024", "margin")["basis"],
                         "wash-adjusted, full history")


class TestTheRegisteredBookChanged(unittest.TestCase):
    """`tjs run --account rrsp` rebuilds the registered book alone (no pass
    across accounts): the next view's full-history file follows it, and
    its record names the book it was built from."""

    @classmethod
    def setUpClass(cls):
        cls.root = _two_years("changed")
        cls.before = _json(cls.root, "stats", "2024")
        (cls.root / "inputs/rrsp/trades.tt").write_text(_RRSP_AFTER, encoding="utf-8")
        _tjs(cls.root, "run", "--no-input", "--account", "rrsp")
        cls.after = _json(cls.root, "stats", "2024")

    def test_the_view_follows_the_registered_book(self):
        self.assertEqual(_denied(self.before), (1, 100.0))
        self.assertEqual(_denied(self.after), (0, 0.0))
        row = _row(self.root / "work/margin_gains_all.json", "QZL.TO", "2024-09-03")
        self.assertEqual(row["gain"], -200.0)
        self.assertFalse(row.get("disallowed_amount"))

    def test_the_record_names_the_registered_book(self):
        rec = json.loads(record_path(self.root / "work/margin_gains_all.json").read_text(
            encoding="utf-8"))
        self.assertEqual(rec["inputs"][_SHELTERED], sha256_file(self.root / _SHELTERED))


class TestAUsCryptoAccountReadsNone(unittest.TestCase):
    """The US: an equity account's full-history run reads the registered
    book (an IRA purchase's wash sale, as the run's blended pass reads it);
    a crypto account's never (the run's crypto pass carries no rule)."""

    def _built(self, account: str):
        from taxjson.cli import common
        from taxjson.pipeline import run_state
        root = Path(private_dir()) / "us"
        work = root / "work"
        work.mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/New_York"\nyear = 2024\n'
            'country = "usa"\nbase_currency = "USD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.coins]\ntype = "taxable"\ncrypto = true\n'
            '[accounts.ira]\ntype = "sheltered"\n', encoding="utf-8")
        for name in (f"{account}_base.json", "sheltered_base.json"):
            (work / name).write_text('{"transactions": []}\n', encoding="utf-8")
        settings = common.load_settings(root, missing_ok=True)
        self.addCleanup(run_state.begin_command)
        run_state.begin_command()
        with mock.patch.object(common.ST, "execute", return_value=[]) as ran:
            common._gains_all_file(root, work, account, settings)
        ctx = ran.call_args.args[1]
        return ctx, work / "sheltered_base.json"

    def test_the_equity_account_reads_it_the_crypto_account_none(self):
        ctx, book = self._built("margin")
        self.assertEqual(ctx.sheltered, book)
        self.assertIn("--sheltered", ctx.own)
        self.assertEqual(ctx.own[ctx.own.index("--sheltered") + 1], str(book))
        ctx, _book = self._built("coins")
        self.assertIsNone(ctx.sheltered)
        self.assertNotIn("--sheltered", ctx.own)
        self.assertIn("--no-wash", ctx.own)


if __name__ == "__main__":
    unittest.main()
