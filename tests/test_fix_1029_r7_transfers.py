"""Fix round 7 of 2026-10-29, group transfers: transfers between
accounts and in-kind moves to and from registered plans.

TJ-0823: a .tt sale between two covered arrivals sold the first
          arrival's units twice (the arrival had consumed them, the
          sale took them again), so the second arrival's own .tt
          purchase looked spent and its broker book value was booked
          as well: a phantom holding and a lower gain.
TJ-0828: a declared in-kind withdrawal from a plan outside the project
          never took the shares out of that plan's side of the loss
          rule, so a loss whose replacement the plan had already given
          back was denied for good.
TJ-0829: a nearby unrelated transfer-in of the same account was paired
          with a declared in-kind contribution as a journal; the line
          then booked a move to a plan outside the project, whose
          replacement shares the real plan's sale never cleared.
TJ-0830: IB's transfer rows dropped the contract size they were priced
          with, so a mini call moved into a plan replaced 100 shares.
TJ-0827: the in-kind repair lines named the project's own inputs/ when
          its exports are shared (../inputs): a line added there is
          never read.
TJ-0821: a DISTINCT line did not stop the arrival check pairing two
          listings of one root in one account as a listing journal.
TJ-0822: the .tt coverage of a transfer-in skipped the dated RENAME
          lines and the INKIND contributions the converted rows leave out.
TJ-0654: a replacement a registered account bought in a loss's window
          and moved to another registered account was lost to the
          per-account still-held test.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date as _date, timedelta
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root: Path, accounts: dict, files: dict, *, year: int = 2025,
             country: str = "canada", tail: str = "") -> Path:
    """A project: `accounts` {name: account table body}, `files`
    {path relative to the project: text}."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, source_currencies=[])
        + "".join(f"[accounts.{n}]\n{body}\n" for n, body in accounts.items())
        + tail)
    (root / "ticker.map").write_text("")
    for n in accounts:
        (root / "inputs" / n).mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _run(test: unittest.TestCase, root: Path) -> subprocess.CompletedProcess:
    r = _cli(root, "run", "--no-input")
    test.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


def _totals(root: Path) -> dict:
    r = _cli(root, "form-export", "--json")
    return json.loads(r.stdout)["totals"]


def _held(root: Path) -> list:
    held = json.loads(_cli(root, "list", "--json").stdout)
    return sorted((r["account"], r["symbol"], r["qty"])
                  for r in held["rows"])


_TAXABLE = 'type = "taxable"'


class TestSaleBetweenCoveredArrivals(unittest.TestCase):
    """TJ-0823: two arrivals of 10 SYN.TO, each backed by its original
    .tt purchase (100, then 200); the first 10 sold by a .tt line
    between them (150); the rest sold at the broker (400). Gain 250,
    nothing held — as when the middle sale is a broker row."""

    def _case(self, td: str, manual: bool) -> Path:
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-03-03 09:30:00 SYN.TO 10 CAD 20 200 0\n")
        data = _QH + (
            "2025-02-03,2025-02-03,TF6,SYN.TO,SYN CORP TRANSFER BOOK "
            "VALUE 100.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-03-10,2025-03-10,TF6,SYN.TO,SYN CORP TRANSFER BOOK "
            "VALUE 300.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-02,2025-06-03,Sell,SYN.TO,SYN CORP,-10,40,400,0,400,"
            "CAD,Trades,99900001,Margin\n")
        if manual:
            hist += "BUYSELL 2025-02-10 09:30:00 SYN.TO -10 CAD 15 150 0\n"
        else:
            data += ("2025-02-10,2025-02-11,Sell,SYN.TO,SYN CORP,-10,15,150,"
                     "0,150,CAD,Trades,99900001,Margin\n")
        return _project(Path(td) / ("manual" if manual else "broker"),
                        {"margin": _TAXABLE},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data})

    def test_manual_sale_between_arrivals(self):
        with tempfile.TemporaryDirectory() as td:
            for manual in (False, True):
                root = self._case(td, manual)
                _run(self, root)
                t = _totals(root)
                self.assertAlmostEqual(t["gain_13200"], 250.0, places=2,
                                       msg=f"manual={manual}")
                self.assertEqual(_held(root), [], f"manual={manual}")


_TFSA = 'type = "sheltered"\nplan = "tfsa"'


class TestOutsidePlanWithdrawal(unittest.TestCase):
    """TJ-0828: buy 20 SYN.TO for 200, sell 10 for 80 on June 2 (a loss
    of 20), contribute 10 to a TFSA outside the project on June 3 at 90
    (a loss of 10, denied for good), withdraw them on June 23 at 100
    and sell them on June 24 for 110. No replacement is held on day 30:
    the June 2 loss is allowed, -20 + 10 = -10 — as with the TFSA in
    the project."""

    def _case(self, td: str, inside: bool) -> Path:
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 20 CAD 10 200 0\n"
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 8 80 0\n"
                "INKIND 2025-06-03 SYN.TO -10 CAD 9 plan=tfsa\n"
                "INKIND 2025-06-23 SYN.TO 10 CAD 10 plan=tfsa\n"
                "BUYSELL 2025-06-24 09:30:00 SYN.TO -10 CAD 11 110 0\n")
        data = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER OUT,-10,0,"
            "0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-23,2025-06-23,TF6,SYN.TO,SYN CORP TRANSFER IN,10,0,0,"
            "0,0,CAD,Transfers,99900001,Margin\n")
        accounts = {"margin": _TAXABLE}
        files = {"inputs/margin/trades.tt": hist,
                 "inputs/margin/qt.csv": data}
        if inside:
            accounts["plan"] = _TFSA
            files["inputs/plan/qt.csv"] = _QH + (
                "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER IN,10,"
                "0,0,0,0,CAD,Transfers,99900002,TFSA\n"
                "2025-06-23,2025-06-23,TF6,SYN.TO,SYN CORP TRANSFER OUT,"
                "-10,0,0,0,0,CAD,Transfers,99900002,TFSA\n")
        return _project(Path(td) / ("inside" if inside else "outside"),
                        accounts, files)

    @rule("CA-SL-03")
    def test_withdrawal_clears_the_outside_plan(self):
        with tempfile.TemporaryDirectory() as td:
            for inside in (True, False):
                root = self._case(td, inside)
                for _ in range(2):      # a second run: the same books
                    _run(self, root)
                    self.assertAlmostEqual(
                        _totals(root)["gain_13200"], -10.0, places=2,
                        msg=f"inside={inside}")


class TestDeclaredContributionBeforeJournal(unittest.TestCase):
    """TJ-0829: margin sells 10 SYN.TO at a loss of 20 on June 2 and
    contributes its other 10 to the project's TFSA on June 3 (an INKIND
    line; the TFSA's transfer-in is there), which sells them on June
    20. An unrelated 10-share transfer-in of margin posts on June 4
    (its own .tt purchase covers it) and margin sells 10 on June 24.
    On day 30 margin and the plan hold nothing: -20 + (-10 denied) +
    10 = -10 — as when the unrelated arrival is weeks later."""

    def _case(self, td: str, arrival: str) -> Path:
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 20 CAD 10 200 0\n"
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 8 80 0\n"
                "INKIND 2025-06-03 SYN.TO -10 CAD 9 plan=tfsa\n"
                "BUYSELL 2025-06-24 09:30:00 SYN.TO -10 CAD 11 110 0\n"
                f"BUYSELL {arrival} 09:30:00 SYN.TO 10 CAD 10 100 0\n")
        data = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER OUT,-10,0,"
            "0,0,0,CAD,Transfers,99900001,Margin\n"
            f"{arrival},{arrival},TF6,SYN.TO,SYN CORP TRANSFER BOOK VALUE "
            "100.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n")
        plan = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER IN,10,0,0,"
            "0,0,CAD,Transfers,99900002,TFSA\n"
            "2025-06-20,2025-06-23,Sell,SYN.TO,SYN CORP,-10,10,100,0,100,"
            "CAD,Trades,99900002,TFSA\n")
        return _project(Path(td) / arrival,
                        {"margin": _TAXABLE, "plan": _TFSA},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data,
                         "inputs/plan/qt.csv": plan})

    @rule("CA-SL-03")
    def test_line_wins_over_the_journal(self):
        with tempfile.TemporaryDirectory() as td:
            for arrival in ("2025-06-16", "2025-06-04"):
                root = self._case(td, arrival)
                _run(self, root)
                self.assertAlmostEqual(_totals(root)["gain_13200"], -10.0,
                                       places=2, msg=arrival)
                moves = json.loads((root / "work" / "in_kind.json")
                                   .read_text())["moves"]
                self.assertEqual([(m["registered"], m["booked"])
                                  for m in moves], [("plan", True)],
                                 arrival)


_IB_HEAD = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    "Statement,Data,Title,Activity Statement\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Financial Instrument Information,Header,Asset Category,Symbol,"
    "Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,"
    "Expiry,Delivery Month,Type,Strike,Code\n"
    "Financial Instrument Information,Data,Equity and Index Options,"
    "SYN 19DEC25 10 C,SYN CALL,999000101,,SYN,TSE,10,2025-12-19,,C,10,\n"
    "Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,"
    "Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,"
    "Cash Amount,Code\n")


class TestIbTransferMultiplier(unittest.TestCase):
    """TJ-0830: IB states the mini call's size (10) in its Financial
    Instrument Information; the call moves from margin to the TFSA on
    June 3 (an INKIND line at 20; the plan's own transfer row kept,
    transfers = true), a day after 100 SYN.TO were sold at a loss of
    100. The call is a right to acquire 10 shares: 10 of the loss is
    denied, and with the call's own gain of 10 the total is -80 — as
    with a ticker.map MULT line."""

    def _case(self, td: str, mult_line: bool) -> Path:
        sym = "SYN251219C00010000.TO"
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 100 CAD 2 200 0\n"
                f"BUYSELL 2025-01-02 09:30:00 {sym} 1 CAD 1 10 0 x10\n"
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -100 CAD 1 100 0\n"
                f"INKIND 2025-06-03 {sym} -1 CAD 0 20 plan=tfsa\n")
        out = (_IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
               "SYN 19DEC25 10 C,2025-06-03,ACATS,Out,Other Broker,"
               "99900002,-1,0,-20,0,0,\n")
        inn = (_IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
               "SYN 19DEC25 10 C,2025-06-03,ACATS,In,Other Broker,"
               "99900002,1,0,20,0,0,\n")
        root = _project(Path(td) / ("map" if mult_line else "row"),
                        {"margin": _TAXABLE,
                         "plan": _TFSA + "\ntransfers = true"},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/ib.csv": out,
                         "inputs/plan/ib.csv": inn})
        if mult_line:
            (root / "ticker.map").write_text("MULT SYN 10\n")
        return root

    @rule("CA-SL-05")
    def test_stated_size_reaches_the_loss_rule(self):
        with tempfile.TemporaryDirectory() as td:
            for mult_line in (True, False):
                root = self._case(td, mult_line)
                _run(self, root)
                t = _totals(root)
                # The shares' -100 + 10 denied on line 13200, the
                # call's +10 on 15300.
                self.assertAlmostEqual(t["gain_13200"], -90.0, places=2,
                                       msg=f"MULT line={mult_line}")
                self.assertAlmostEqual(t["gain_all"], -80.0, places=2,
                                       msg=f"MULT line={mult_line}")

    def test_transfer_row_carries_the_multiplier(self):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.csv"
            p.write_text(_IB_HEAD + "Transfers,Data,Equity and Index "
                         "Options,CAD,SYN 19DEC25 10 C,2025-06-03,ACATS,"
                         "Out,Other Broker,99900002,-1,0,-20,0,0,\n")
            rows = [t for t in IbBrokerage().parse_file(p)
                    if t.get("action") == "TRANSFER"]
            self.assertEqual([(t["quantity"], t["price"], t["multiplier"])
                              for t in rows], [(-1.0, 2.0, 10.0)])


class TestRepairLineNamesSharedInputs(unittest.TestCase):
    """TJ-0827: a year folder reading its exports from ../inputs. An
    in-kind contribution the offline run cannot value names the folder
    the run reads; the line added there lets the run book it."""

    def test_offline_refusal_names_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            root = _project(top / "2025", {"margin": _TAXABLE,
                                           "plan": _TFSA}, {})
            cfg = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(cfg.replace(
                "[settings]\n", '[settings]\ninputs_dir = "../inputs"\n'))
            (root / "inputs").rename(top / "inputs")
            (top / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n")
            for a, q, day, no, kind in (
                    ("margin", -10, "2025-06-02", "99900001", "Margin"),
                    ("plan", 10, "2025-06-03", "99900002", "TFSA")):
                (top / "inputs" / a / "qt.csv").write_text(
                    _QH + f"{day},{day},TF6,SYN.TO,SYN CORP TRANSFER,{q},0,"
                    f"0,0,0,CAD,Transfers,{no},{kind}\n")
            r = _cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("add to a .tt file in ../inputs/margin/: INKIND "
                          "2025-06-02 SYN.TO -10 CAD", r.stderr)
            (top / "inputs/margin/fix.tt").write_text(
                "INKIND 2025-06-02 SYN.TO -10 CAD 15 plan=tfsa\n")
            _run(self, root)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 50.0,
                                   places=2)

    def test_messages_take_the_folder(self):
        from taxjson.lib import in_kind as IK
        row = {"action": "TRANSFER", "symbol": "SYN.TO", "quantity": -10.0,
               "date": "2025-06-02", "currency": "CAD"}
        leg = IK.Leg(account="margin", broker="questrade", row=row,
                     key="SYN.TO")
        m = IK.Move(IK.CONTRIBUTION, taxable=leg, registered=None,
                    plan="tfsa", quantity=10.0, date="2025-06-02",
                    problem="no_value")
        _head, details = IK.message([m], [], {}, "canada", "CAD",
                                    "../inputs")
        self.assertIn("a .tt file in ../inputs/margin/", details[0])
        plan = IK.Leg(account="plan", broker="questrade", plan="tfsa",
                      row=dict(row, quantity=6.0), key="SYN.TO")
        _head, details = IK.parts_message([(leg, [plan])], "../inputs")
        self.assertIn("a .tt file in ../inputs/margin/", details[0])


class TestDistinctListingIsNoJournal(unittest.TestCase):
    """TJ-0821: margin sends 10 SYN.TO out on March 3 and receives 10
    SYN.US (another company: DISTINCT SYN.TO SYN.US) from outside its
    books on March 4 at a stated 250, and sells them for 300 (USD). The
    arrival is no listing journal: booked at its stated cost, its sale
    a gain of 50 USD — as an arrival of DIFF.US is."""

    def _case(self, td: str, new: str) -> Path:
        rows = [
            ("2025-01-06", "2025-01-07", "Buy", "SYN.TO", "SYN ALPHA CORP",
             10, 10, -100, "CAD", "Trades"),
            ("2025-03-03", "2025-03-03", "TF6", "SYN.TO",
             "SYN ALPHA CORP TRANSFER OUT", -10, 0, 0, "CAD", "Transfers"),
            ("2025-03-04", "2025-03-04", "TF6", new,
             "SYN BETA CORP TRANSFER BOOK VALUE 250.00", 10, 0, 0, "USD",
             "Transfers"),
            ("2025-06-02", "2025-06-03", "Sell", new, "SYN BETA CORP", -10,
             30, 300, "USD", "Trades")]
        data = _QH + "".join(
            f"{d},{s},{a},{sym},{desc},{q},{px},{net},0,{net},{cur},{k},"
            f"99900001,Margin\n"
            for d, s, a, sym, desc, q, px, net, cur, k in rows)
        start = _date(2024, 1, 1)
        rates = "".join(f"{start + timedelta(days=i)} 12:00:00 USD CAD "
                        f"1.0000 boc\n" for i in range(1096))
        root = _project(Path(td) / new, {"margin": _TAXABLE},
                        {"inputs/margin/qt.csv": data,
                         "work/to_base.csv": rates})
        cfg = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(cfg.replace(
            "source_currencies = []", 'source_currencies = ["USD"]'))
        (root / "ticker.map").write_text(f"DISTINCT SYN.TO {new}\n")
        return root

    def test_distinct_listing_arrival_is_booked(self):
        # The figures in CAD at whatever rate the run converts with: the
        # SYN.US arrival is booked as the DIFF.US one is.
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for new in ("DIFF.US", "SYN.US"):
                root = self._case(td, new)
                _run(self, root)
                t = _totals(root)
                got[new] = (round(t["gain_13200"], 2),
                            round(t["proceeds_13199"], 2))
            self.assertGreater(got["DIFF.US"][1], 0.0)
            self.assertGreater(got["DIFF.US"][0], 0.0)
            self.assertEqual(got["SYN.US"], got["DIFF.US"])


class TestCoverageReadsDeclarations(unittest.TestCase):
    """TJ-0822: the .tt coverage of a transfer-in reads the account's
    declarations too.

    A dated RENAME: 10 OLDS.TO bought for 100 (.tt), renamed NEWS.TO on
    February 3 (a .tt RENAME line), arrive by transfer on March 3 (a
    stated 250) and are sold for 300: the purchase covers the arrival,
    gain 200 and nothing held — as with a SPLIT line that renames.

    An INKIND contribution: 10 bought for 100 (.tt) go into the TFSA on
    June 3 at 120 (an INKIND line), 10 others arrive from outside on
    June 16 (a stated 100) and are sold for 150 on June 24: the
    contributed purchase covers nothing — 20 + 50 = 70, proceeds 270 —
    as when the purchase is a broker row."""

    def _rename(self, td: str, how: str) -> Path:
        hist = "BUYSELL 2025-01-02 09:30:00 OLDS.TO 10 CAD 10 100 0\n"
        hist += {"rename": "RENAME 2025-02-03 OLDS.TO NEWS.TO\n",
                 "split": "SPLIT 2025-02-03 00:00:00 OLDS.TO NEWS.TO 1\n"
                 }[how]
        data = _QH + (
            "2025-03-03,2025-03-03,TF6,NEWS.TO,SYN CORP TRANSFER BOOK VALUE"
            " 250.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-02,2025-06-03,Sell,NEWS.TO,SYN CORP,-10,30,300,0,300,"
            "CAD,Trades,99900001,Margin\n")
        return _project(Path(td) / how, {"margin": _TAXABLE},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data})

    def test_dated_rename_carries_the_purchase(self):
        with tempfile.TemporaryDirectory() as td:
            for how in ("split", "rename"):
                root = self._rename(td, how)
                _run(self, root)
                self.assertAlmostEqual(_totals(root)["gain_13200"], 200.0,
                                       places=2, msg=how)
                self.assertEqual(_held(root), [], how)

    def _inkind(self, td: str, manual: bool) -> Path:
        hist = "INKIND 2025-06-03 SYN.TO -10 CAD 12 plan=tfsa\n"
        data = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER OUT,-10,0,"
            "0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-16,2025-06-16,TF6,SYN.TO,SYN CORP TRANSFER BOOK VALUE "
            "100.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-24,2025-06-25,Sell,SYN.TO,SYN CORP,-10,15,150,0,150,"
            "CAD,Trades,99900001,Margin\n")
        if manual:
            hist += "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
        else:
            data += ("2025-01-02,2025-01-03,Buy,SYN.TO,SYN CORP,10,10,-100,"
                     "0,-100,CAD,Trades,99900001,Margin\n")
        plan = _QH + ("2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER "
                      "IN,10,0,0,0,0,CAD,Transfers,99900002,TFSA\n")
        return _project(Path(td) / ("manual" if manual else "broker"),
                        {"margin": _TAXABLE, "plan": _TFSA},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data,
                         "inputs/plan/qt.csv": plan})

    def test_contributed_purchase_covers_no_later_arrival(self):
        with tempfile.TemporaryDirectory() as td:
            for manual in (False, True):
                root = self._inkind(td, manual)
                _run(self, root)
                t = _totals(root)
                self.assertAlmostEqual(t["gain_13200"], 70.0, places=2,
                                       msg=f"manual={manual}")
                self.assertAlmostEqual(t["proceeds_13199"], 270.0,
                                       places=2, msg=f"manual={manual}")
                self.assertEqual(
                    [r for r in _held(root) if r[0] == "margin"], [],
                    f"manual={manual}")


def _gains_with_context(sheltered):
    """Canada, 2025: 100 SYN.TO bought in 2024 for 1000 and sold on
    January 20, 2025 for 600 (a loss of 400), with the registered
    accounts' rows `sheltered` (a list of TaxTransaction)."""
    import contextlib
    import copy
    import io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    from tax_rules.dual import tx
    book = [tx("BUYSELL", "2024-01-08", "SYN.TO", 100, 1000,
               currency="CAD", account="a", id="old"),
            tx("BUYSELL", "2025-01-20", "SYN.TO", -100, 600,
               currency="CAD", account="a", id="loss")]
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return run_gains(copy.deepcopy(book), copy.deepcopy(sheltered), [],
                         GainsRequest(country="canada", year=2025,
                                      taxable=True, phantom_hint=False))


class TestRegisteredReplacementMoved(unittest.TestCase):
    """TJ-0654: registered account r1 buys 100 SYN.TO on January 10
    (inside the loss's window); a custody move of your own takes them to
    registered account r2 on January 15, which holds them past day 30.
    The replacement is still owned: the 400 loss is denied for good, as
    when r1 keeps them. A round trip through r2 (sold before day 30)
    denies nothing, and r2's own older shares never back a denial."""

    def _ctx(self, *rows):
        from tax_rules.dual import tx
        return [tx(a, d, "SYN.TO", q, n, currency="CAD", account=acct,
                   id=i) for i, a, d, q, n, acct in rows]

    def _denied(self, *rows) -> float:
        g = _gains_with_context(self._ctx(*rows))
        denied = round(sum(float(t.get("disallowed_amount") or 0.0)
                           for t in g["transactions"]), 2)
        # A registered account's replacement: denied for good, all of it.
        self.assertEqual(round(sum(
            float(t.get("permanently_disallowed") or 0.0)
            for t in g["transactions"]), 2), denied)
        return denied

    @rule("CA-SL-08")
    def test_move_between_registered_accounts_keeps_the_replacement(self):
        buy = ("rep", "BUYSELL", "2025-01-10", 100, 500, "r1")
        self.assertEqual(self._denied(buy), 400.0)
        self.assertEqual(self._denied(
            buy, ("out", "TRANSFER", "2025-01-15", -100, 0, "r1"),
            ("in", "TRANSFER", "2025-01-15", 100, 0, "r2")), 400.0)
        # The move's legs listed the other way round.
        self.assertEqual(self._denied(
            buy, ("in", "TRANSFER", "2025-01-15", 100, 0, "r2"),
            ("out", "TRANSFER", "2025-01-15", -100, 0, "r1")), 400.0)

    @rule("CA-SL-02")
    def test_sold_before_day_30_or_older_units_deny_nothing(self):
        buy = ("rep", "BUYSELL", "2025-01-10", 100, 500, "r1")
        move = (("out", "TRANSFER", "2025-01-15", -100, 0, "r1"),
                ("in", "TRANSFER", "2025-01-15", 100, 0, "r2"))
        # Moved, then sold by r2 inside the window: nothing held.
        self.assertEqual(self._denied(
            buy, *move,
            ("sell", "BUYSELL", "2025-02-05", -100, 550, "r2")), 0.0)
        # r1 buys and sells inside the window; r2's own 2024 units then
        # move to r1: older units, never a backing.
        self.assertEqual(self._denied(
            ("r2old", "BUYSELL", "2024-03-01", 100, 900, "r2"),
            buy,
            ("sell", "BUYSELL", "2025-01-12", -100, 520, "r1"),
            ("out", "TRANSFER", "2025-01-15", -100, 0, "r2"),
            ("in", "TRANSFER", "2025-01-15", 100, 0, "r1")), 0.0)

    @rule("CA-SL-08")
    def test_taxable_lot_move_keeps_a_taxable_replacement(self):
        """A taxable rebuy moved between two taxable accounts (a lot
        move) is still the replacement, with a registered move of older
        units in the window too: deferred, as without that move."""
        import contextlib
        import copy
        import io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        from tax_rules.dual import tx

        def _t(i, a, d, q, n, acct, **kw):
            return tx(a, d, "SYN.TO", q, n, currency="CAD", account=acct,
                      id=i, **kw)
        book = [_t("old", "BUYSELL", "2024-01-08", 100, 1000, "a"),
                _t("loss", "BUYSELL", "2025-01-20", -100, 600, "a"),
                _t("rebuy", "BUYSELL", "2025-01-25", 100, 500, "a"),
                _t("lo", "TRANSFER", "2025-01-27", -100, 0, "a",
                   type="lot_move"),
                _t("li", "TRANSFER", "2025-01-27", 100, 0, "b",
                   type="lot_move")]
        for ctx in ([], [_t("r1old", "BUYSELL", "2024-03-01", 50, 400, "r1"),
                         _t("o", "TRANSFER", "2025-01-15", -50, 0, "r1"),
                         _t("i", "TRANSFER", "2025-01-15", 50, 0, "r2")]):
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                g = run_gains(copy.deepcopy(book), copy.deepcopy(ctx), [],
                              GainsRequest(country="canada", year=2025,
                                           taxable=True, phantom_hint=False))
            self.assertEqual(
                [(t["id"], round(t["disallowed_amount"], 2),
                  round(t["permanently_disallowed"], 2))
                 for t in g["transactions"]], [("loss", 400.0, 0.0)],
                len(ctx))


if __name__ == "__main__":
    unittest.main()
