"""Backlog group `missing` (2026-10-25): find-missing-history, its drafts
and transfers.

TJ-0008  a transfer row stating a book value of 0 was drafted as a $0.00
         purchase; filled in with its date only it booked a $0 lot. The
         line now leaves the cost as COST (the run refuses it until it is
         filled in), and a row with no id no longer says "(row ?)".
TJ-0009  a transfer-in the run reports PARTLY covered by .tt purchases
         was "not drafted: ... a line would count it twice": the other
         units are drafted now, at COST, with a CHECK.
TJ-0017  a grant-timed written option sold with no purchase in the
         files was called "NOT in the totals" while its premium was.
TJ-0018  one draft line per sale that went short: more than the deepest
         short when a purchase in the files came between two of them.
TJ-0043  `tjs transfers`' `! ` lines were over 100 columns and wrapped.
TJ-0044  the run's closing list named one $0 lot twice (part sold, part
         held): "2 positions at a $0 cost (SAMPK.TO, SAMPK.TO)".
TJ-0055  `tjs transfers` TYPE showed the broker's free text (with the
         other account's number) instead of a transfer type.
TJ-0074  the drafts' slower own-move tier paired a leg of an in-kind
         move (to a registered account) with an outside arrival.
TJ-0077  find-missing-history, sanity and transfers said nothing about
         books older than the inputs, and --write-missing-history wrote
         an OPENING line for a position a just-added purchase fixes.
TJ-0175  the --write-missing-history note was one unwrapped line.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_backlog_missing
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
import _paths

REPO = _paths.REPO
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root, *args, width=None):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", NO_COLOR="1",
               TAXJSON_LOCAL_TZ="America/Toronto")
    if width is not None:
        env["TAXJSON_WIDTH"] = str(width)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=300)


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type")


def _qt(date, action, sym, desc, qty, price, gross, comm, net,
        kind="Trades", acct="99900001"):
    return (f"{date},{date},{action},{sym},{desc},{qty},{price},{gross},"
            f"{comm},{net},CAD,{kind},{acct},Margin")


_TOML = """\
[settings]
country = "canada"
local_timezone = "America/Toronto"
tax_date = "settle"
year = {year}
base_currency = "CAD"
source_currencies = []
{extra}
[accounts.margin]
type = "taxable"
"""


def _project(tmp, name, rows, *, year=2024, extra="", files=None):
    root = Path(tmp) / name
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_TOML.format(year=year, extra=extra))
    if rows:
        (root / "inputs" / "margin" / "activity.csv").write_text(
            "\n".join([_QT_HEAD] + rows) + "\n")
    for fname, text in (files or {}).items():
        (root / "inputs" / "margin" / fname).write_text(text)
    return root


def _transfer(sym, d, q, desc, acct="margin", **kw):
    return dict(action="TRANSFER", date=d, symbol=sym, quantity=float(q),
                currency="CAD", account=acct, description=desc, **kw)


# -------------------------------------------- `tjs transfers` and drafts
class TestTransfersProject(unittest.TestCase):
    """One Questrade project: a transfer stated at 0 (sold in part), one
    partly covered by a .tt purchase, another stated at 0, and a
    transfer out naming the receiving account.

    Formerly: test_fix_backlog_missing.TestTransfersProject"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = _project(cls.tmp.name, "qt", [
            _qt("2024-02-01", "Buy", "SAMPA.TO", "SAMPLE A CORP", 10, 10,
                -100, -4.95, -104.95),
            _qt("2024-06-03", "TF6", "SAMPK.TO",
                "SAMPLE K CORP TRANSFER BOOK VALUE 0.00", 40, 0, 0, 0, 0,
                kind="Transfers"),
            _qt("2024-09-10", "Sell", "SAMPK.TO", "SAMPLE K CORP", -10, 12,
                120, -4.95, 115.05),
            _qt("2024-06-03", "TF6", "SAMPL.TO",
                "SAMPLE L CORP TRANSFER BOOK VALUE 400.00", 20, 0, 0, 0, 0,
                kind="Transfers"),
            _qt("2024-10-01", "Sell", "SAMPL.TO", "SAMPLE L CORP", -5, 25,
                125, -4.95, 120.05),
            _qt("2024-06-04", "TF6", "SAMPMLONGNAME.TO",
                "SAMPLE M CORP TRANSFER BOOK VALUE 0.00", 15, 0, 0, 0, 0,
                kind="Transfers"),
            _qt("2024-07-02", "TF6", "SAMPA.TO",
                "SAMPLE A CORP TFER TO 99900077", -5, 0, 0, 0, 0,
                kind="Transfers"),
        ], files={"old.tt": "BUYSELL 2021-03-15 09:30:00 SAMPL.TO 10 CAD "
                            "15.00 150.00 0\n"})
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tj0043_act_lines_are_one_line_within_100_columns(self):
        r = _cli(self.root, "transfers", width=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertEqual(len(acts), 2, r.stdout)
        for ln in acts:
            self.assertLessEqual(len(ln), 100, ln)
            self.assertTrue(ln.endswith("— tjs transfers --details"), ln)
        self.assertIn("! 2 transfer-in(s) with no cost (SAMPK.TO, "
                      "SAMPMLONGNAME.TO)", acts[0])
        self.assertIn("! 1 transfer-in(s) only partly covered, NOT booked "
                      "(SAMPL.TO)", acts[1])

    def test_tj0055_type_is_a_transfer_type_not_the_description(self):
        r = _cli(self.root, "transfers", width=0)
        rows = [ln for ln in r.stdout.splitlines()
                if ln.startswith("2024-")]
        self.assertEqual(len(rows), 4, r.stdout)
        for ln in rows:
            self.assertNotIn("9990", ln)
            self.assertNotIn("SAMPLE", ln)
        out = [ln for ln in rows if " SAMPA.TO " in ln][0]
        self.assertIn(" transfer-out ", out)
        self.assertIn(" transfer-in ", [ln for ln in rows
                                        if " SAMPK.TO " in ln][0])
        # --json keeps the row's own text.
        j = json.loads(_cli(self.root, "transfers", "--json").stdout)
        self.assertIn("SAMPLE A CORP TFER TO 99900077",
                      [t["type"] for t in j["transfers"]])
        self.assertTrue(all(not k.startswith("_")
                            for t in j["transfers"] for k in t))

    def test_tj0008_tj0009_the_drafts_of_the_project(self):
        r = _cli(self.root, "find-missing-history", "--write-purchases",
                 "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.root / "inputs" / "margin"
                / "purchases_draft.tt.txt").read_text()
        self.assertIn("BUYSELL  YYYY-MM-DD  09:30:00  SAMPK.TO  40  CAD  "
                      "COST  COST  0", text)
        self.assertIn("BUYSELL  YYYY-MM-DD  09:30:00  SAMPL.TO  10  CAD  "
                      "COST  COST  0", text)
        self.assertNotIn("0.000000  0.00", text)
        self.assertNotIn("(row ?)", text)
        self.assertNotIn("count it twice", text)


class TestTransferKinds(unittest.TestCase):
    """TJ-0055: the TYPE column of hand-made sidecars of three brokers.

    Formerly: test_fix_backlog_missing.TestTransferKinds"""

    def test_tj0055_ib_kind_crypto_type_questrade_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "rows.tt").write_text("# none\n")
            (root / "taxjson.toml").write_text(_TOML.format(year=2024,
                                                            extra=""))
            work = root / "work"
            work.mkdir()

            def side(broker, rows):
                (work / f"margin_{broker}_transfers.json").write_text(
                    json.dumps({"metadata": {"kind": "transfer_sidecar",
                                             "account": "margin",
                                             "brokerage": broker},
                                "transactions": rows}))
            side("ib", [_transfer("ZZQ.US", "2024-03-01", 5,
                                  "ACATS (ZZQ)")])
            side("questrade", [_transfer("ZZR.TO", "2024-03-02", -5,
                                         "ZZR INC TFER TO 99900077")])
            side("kraken", [_transfer("ZZX", "2024-03-03", -2,
                                      "withdrawal (fee 0.25 ZZX)")])
            r = _cli(root, "transfers", width=0)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = {ln.split()[2]: ln.split()[4] for ln in r.stdout.splitlines()
                   if ln.startswith("2024-")}
            self.assertEqual(got, {"ZZQ.US": "ACATS",
                                   "ZZR.TO": "transfer-out",
                                   "ZZX": "withdrawal"})


if __name__ == "__main__":
    unittest.main()
