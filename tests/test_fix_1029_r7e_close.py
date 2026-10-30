"""Fix round 7 (wave E) of 2026-10-30, the close group: an option's
INKIND line whose price and total disagree, a broker sale of the
account's own .tt units, format-map's run-first refusal, the tobase-map
step's stamp order, identical event-only .tt files, the still-held test
after a later rename, a mid-year snapshot's reports and a filed year's
copy of a relative link (synthetic data only)."""
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root, accounts, year=2025, extra=""):
    """A Canadian project of `accounts` ({name: type}; a sheltered one a
    TFSA with transfers kept) and its input folders."""
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    body = "".join(
        f'[accounts.{a}]\ntype = "{t}"\n'
        + ('plan = "tfsa"\ntransfers = true\n' if t == "sheltered" else "")
        for a, t in accounts.items())
    (root / "taxjson.toml").write_text(head + body + extra)
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


def _row(day, action, sym, qty, price, net, kind="Trades", desc="SYN CORP",
         num="99900001", typ="Margin"):
    return (f"{day},{day},{action},{sym},{desc},{qty},{price},{net},0,{net},"
            f"CAD,{kind},{num},{typ}\n")


def _ts(y, m, d):
    """Noon local time of a day, as a file's mtime."""
    return datetime.datetime(y, m, d, 12).timestamp()


class TestInKindOptionTotalRefused(unittest.TestCase):
    """TJ-1163: an option's INKIND line whose price and total disagree at
    the contract's size stops the run and is an error of check-tt; one
    that agrees at the real size still passes (TJ-0933)."""

    _SYM = "SYN251219C00010000.TO"

    def _make(self, td, value, mult="", extra_line=""):
        root = _project(Path(td), {"margin": "taxable"})
        (root / "ticker.map").write_text(mult)
        (root / "inputs/margin/trades.tt").write_text(
            f"BUYSELL 2025-01-02 09:30:00 {self._SYM} 1 CAD 1 "
            + ("10 0 x10" if mult else "100 0") + "\n"
            f"INKIND 2025-06-02 {self._SYM} -1 CAD {value} plan=tfsa\n"
            + extra_line)
        (root / "inputs/margin/qt.csv").write_text(
            _QH + _row("2025-06-02", "TF6", self._SYM, -1, 0, 0,
                       kind="Transfers", desc="SYN TRANSFER"))
        return root

    def test_a_disagreeing_line_stops_the_run_and_check_tt(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._make(td, "2 2000")
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("differs from qty x price x 100",
                          r.stdout + r.stderr)
            r = tj(root, "checklist", "--run", "--only", "tt-lines", "--json",
                   check=False)
            self.assertIn("attention", r.stdout)
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("price and total disagree", r.stderr)
            r = tj(root, "form-export", "--json", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_a_line_of_no_transfer_row_still_stops(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._make(td, "2 200", extra_line=(
                f"INKIND 2025-09-20 {self._SYM} -1 CAD 2 2000 plan=tfsa\n"))
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("price and total disagree", r.stderr)

    def test_agreeing_at_the_standard_size_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._make(td, "2 200")
            tj(root, "check-tt")
            tj(root, "run", "--no-input")
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            self.assertEqual(len(fe["rows"]), 1)

    def test_agreeing_at_the_mult_size_passes_and_a_mismatch_there_stops(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._make(td, "2 20", mult=f"MULT {self._SYM} 10\n")
            tj(root, "check-tt")
            tj(root, "run", "--no-input")
        with tempfile.TemporaryDirectory() as td:
            root = self._make(td, "2 200", mult=f"MULT {self._SYM} 10\n")
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("x 10 =", r.stdout + r.stderr)
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_price_alone_and_total_alone_still_pass(self):
        for value in ("2", "0 777"):
            with tempfile.TemporaryDirectory() as td:
                root = self._make(td, value)
                tj(root, "check-tt")
                tj(root, "run", "--no-input")

    def test_a_row_stated_size_is_the_size_for_check_tt_and_the_run(self):
        from taxjson.lib import check_tt as CK
        from taxjson.lib import in_kind as IK
        ln = {"symbol": self._SYM, "price": 2.0, "total": 100.0,
              "total_stated": True, "quantity": -1.0, "source": "x.tt:1"}
        row = {"symbol": self._SYM, "quantity": -1.0, "multiplier": 50.0}
        leg = IK.Leg("margin", "ib", row, self._SYM)
        self.assertEqual(IK._line_value(ln, leg, None), 100.0)
        CK._refuse_inkind_total(ln, {self._SYM: [50.0]})
        with self.assertRaisesRegex(ValueError, "differs from"):
            CK._refuse_inkind_total(ln, None)

    def test_usa_stops_too(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._make(td, "2 2000")
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"')
                .replace('America/Toronto', 'America/New_York'))
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("price and total disagree", r.stderr)


class TestBrokerSaleSpendsTtUnits(unittest.TestCase):
    """TJ-1179: a broker sale beyond the broker's and the arrived units
    sold the account's .tt units, so a later transfer-in with a stated
    book value is booked."""

    @staticmethod
    def _tf(day, qty, bv):
        return _row(day, "TF6", "SYN.TO", qty, 0, 0, kind="Transfers",
                    desc=f"SYN CORP TRANSFER BOOK VALUE {bv}.00")

    @staticmethod
    def _sell(day, qty, net):
        return _row(day, "Sell", "SYN.TO", -qty, net / qty, net)

    def _run(self, tt, csv):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), {"margin": "taxable"})
            (root / "inputs/margin/t.tt").write_text(tt)
            (root / "inputs/margin/q.csv").write_text(_QH + csv)
            tj(root, "run", "--no-input")
            r = tj(root, "run", "--no-input")
            self.assertNotIn("go short", r.stdout + r.stderr)
            costs = root / "work/margin_transfer_costs.json"
            rows = (json.loads(costs.read_text())["transactions"]
                    if costs.exists() else [])
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            return [t["date"][:10] for t in rows], fe["gain_unrounded"]

    def test_t1(self):
        booked, gain = self._run(
            "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            self._sell("2025-02-10", 10, 150) + self._tf("2025-04-01", 10, 300)
            + self._sell("2025-06-02", 10, 400))
        self.assertEqual(booked, ["2025-04-01"])
        self.assertAlmostEqual(gain, 150.0, places=2)

    def test_t2(self):
        booked, gain = self._run(
            "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
            "BUYSELL 2025-01-03 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            self._tf("2025-02-03", 10, 100) + self._sell("2025-02-10", 20, 300)
            + self._tf("2025-03-10", 10, 300)
            + self._sell("2025-06-02", 10, 400))
        self.assertEqual(booked, ["2025-03-10"])
        self.assertAlmostEqual(gain, 200.0, places=2)

    def test_a_sale_after_the_arrival_spends_nothing_before_it(self):
        booked, gain = self._run(
            "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            self._tf("2025-04-01", 10, 300)
            + self._sell("2025-06-02", 10, 400))
        self.assertEqual(booked, [])
        self.assertAlmostEqual(gain, 300.0, places=2)

    def test_the_brokers_own_units_are_sold_first(self):
        booked, gain = self._run(
            "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n",
            _row("2025-01-20", "Buy", "SYN.TO", 10, 20, -200)
            + self._sell("2025-02-10", 10, 300) + self._tf("2025-04-01", 10, 300)
            + self._sell("2025-06-02", 10, 400))
        self.assertEqual(booked, [])
        # 300 + 400 less the two purchases of 100 and 200
        self.assertAlmostEqual(gain, 400.0, places=2)

    def test_a_rename_and_an_earlier_year(self):
        booked, gain = self._run(
            "BUYSELL 2024-01-02 09:30:00 OLD.TO 10 CAD 10 100 0\n"
            "RENAME 2024-06-03 OLD.TO SYN.TO\n",
            self._sell("2024-11-10", 10, 150) + self._tf("2025-04-01", 10, 300)
            + self._sell("2025-06-02", 10, 400))
        self.assertEqual(booked, ["2025-04-01"])
        self.assertAlmostEqual(gain, 100.0, places=2)

    def test_usa_uses_the_same_replay(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), {"margin": "taxable"})
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"')
                .replace('America/Toronto', 'America/New_York'))
            sell = _row("2025-02-10", "Sell", "SYN.US", -10, 15, 150)
            tf = _row("2025-04-01", "TF6", "SYN.US", 10, 0, 0, kind="Transfers",
                      desc="SYN CORP TRANSFER BOOK VALUE 300.00")
            (root / "inputs/margin/t.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYN.US 10 USD 10 100 0\n")
            (root / "inputs/margin/q.csv").write_text(
                _QH + (sell + tf).replace(",CAD,", ",USD,"))
            tj(root, "run", "--no-input")
            tj(root, "run", "--no-input")
            costs = json.loads((root / "work/margin_transfer_costs.json")
                               .read_text())
            self.assertEqual([t["date"][:10] for t in costs["transactions"]],
                             ["2025-04-01"])

    def test_a_partial_sale_leaves_part_covered(self):
        from taxjson.lib import transfer_in as TI

        def tx(day, q):
            return {"action": "BUYSELL", "date": day, "time": "09:30:00",
                    "symbol": "SYN.TO", "quantity": q}
        a = TI.Arrival(
            account="m", symbol="SYN.TO", key="SYN.TO", date="2025-04-01",
            time="09:30:00", quantity=10.0, currency="CAD", book_value=100.0,
            broker="questrade", source="", description="",
            row={"time": "09:30:00"})
        TI.mark_covered([a], {"m": [tx("2025-01-02", 10)]},
                        broker_rows={"m": [tx("2025-02-10", -4)]})
        self.assertFalse(a.covered)
        self.assertAlmostEqual(a.covered_units, 6.0)

    def test_two_accounts_keep_their_own_units(self):
        from taxjson.lib import transfer_in as TI

        def tx(day, q):
            return {"action": "BUYSELL", "date": day, "time": "09:30:00",
                    "symbol": "SYN.TO", "quantity": q}
        a = TI.Arrival(
            account="m", symbol="SYN.TO", key="SYN.TO", date="2025-04-01",
            time="09:30:00", quantity=10.0, currency="CAD", book_value=100.0,
            broker="questrade", source="", description="",
            row={"time": "09:30:00"})
        TI.mark_covered([a], {"m": [tx("2025-01-02", 10)]},
                        broker_rows={"n": [tx("2025-02-10", -10)]})
        self.assertTrue(a.covered)


class TestFormatMapRunFirst(unittest.TestCase):
    """TJ-1182: format-map's run-first refusal is for files that build
    books; a README or a positions report in another-kind account builds
    none, and no run would cure it."""

    def _make(self, d):
        root = _project(Path(d), {"margin": "taxable", "coins": "taxable"})
        cfg = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(
            cfg.replace('[accounts.coins]\ntype = "taxable"\n',
                        '[accounts.coins]\ntype = "taxable"\n'
                        'crypto = true\n'))
        (root / "inputs/margin/trades.tt").write_text(
            "BUYSELL 2025-01-02 10:00:00 SYNA.TO 10 CAD 10 100 0\n"
            "BUYSELL 2025-07-02 10:00:00 NEWA.TO -10 CAD 12 120 0\n")
        (root / "ticker.map").write_text(
            "RENAME SYNA.TO NEWA.TO 2025-06-02\n")
        return root

    def test_a_folder_no_run_reads_does_not_block(self):
        for name in ("README.md", "notes.txt", "statement.pdf",
                     "manifest.json", "sends.json", "map.toml"):
            with tempfile.TemporaryDirectory() as d:
                root = self._make(d)
                (root / "inputs/coins" / name).write_text("{}\n")
                r = tj(root, "format-map", "--write", check=False)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("RENAME 2025-06-02 SYNA.TO NEWA.TO",
                              (root / "inputs/margin/renames.tt").read_text())
                r = tj(root, "run", "--no-input")
                fe = json.loads(tj(root, "form-export", "--json").stdout)
                self.assertAlmostEqual(fe["gain_unrounded"], 20.0, places=2)

    def test_a_file_that_builds_books_still_needs_a_run(self):
        for name, text in (("COINS.TT", "# none yet\n"),
                           ("x.tt", "# none yet\n"),
                           ("kr_x.CSV", "not, an, export\n")):
            with tempfile.TemporaryDirectory() as d:
                root = self._make(d)
                (root / "inputs/coins" / name).write_text(text)
                r = tj(root, "format-map", "--write", check=False)
                self.assertNotEqual(r.returncode, 0, name)
                self.assertIn("account coins has no books yet",
                              r.stdout + r.stderr)

    def test_a_comment_only_tt_is_cured_by_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d)
            (root / "inputs/coins/x.tt").write_text("# none yet\n")
            tj(root, "run", "--no-input")
            r = tj(root, "format-map", "--write", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_positions_report_does_not_block(self):
        from unittest import mock

        from taxjson.lib import dated_events as DE
        from taxjson.lib.brokerages import detect as DT
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d)
            (root / "inputs/coins/pos.csv").write_text("a,b\n1,2\n")
            found = mock.Mock(positions=True)
            with mock.patch.object(DT, "detect", return_value=found):
                self.assertFalse(DE._has_inputs(root, "coins"))
            found = mock.Mock(positions=False)
            with mock.patch.object(DT, "detect", return_value=found):
                self.assertTrue(DE._has_inputs(root, "coins"))


class TestTobaseStampOrder(unittest.TestCase):
    """TJ-1183: the tobase-map step orders stamps by generation."""

    def _status(self, stamp, generated):
        from types import SimpleNamespace as NS
        from unittest import mock

        from taxjson.lib import checklist as CL
        from taxjson.lib import tobase_map as TB
        with tempfile.TemporaryDirectory() as d:
            ctx = NS(settings={"country": "canada"}, root=Path(d))
            with mock.patch.object(TB, "load_master",
                                   return_value=NS(generated=generated)), \
                    mock.patch.object(TB, "read_tobase",
                                      return_value=NS(stamp=stamp, lines=[])):
                return CL.s_tobase_map(ctx, None)

    def test_order(self):
        for stamp, generated, status in (
                ("2025-06-02.9", "2025-06-02.10", "attention"),
                ("2025-06-02.10", "2025-06-02.9", "done"),
                ("2025-06-02", "2025-06-02.2", "attention"),
                ("2025-06-02.2", "2025-06-02", "done"),
                ("2025-06-02.10", "2025-06-02.10", "done"),
                ("2025-06-01.10", "2025-06-02", "attention"),
                ("", "2025-06-02", "attention")):
            r = self._status(stamp, generated)
            self.assertEqual(r.status, status, (stamp, generated, r.detail))


class TestIdenticalEventOnlyTt(unittest.TestCase):
    """TJ-1184: identical per-account SPLIT or RENAME files book nothing
    twice; a copied file with a row still does."""

    def _make(self, d, shared, a_extra="", b_extra=""):
        root = _project(Path(d), {"a": "taxable", "b": "taxable"})
        (root / "inputs/a/t.tt").write_text(
            "BUYSELL 2025-01-02 10:00:00 SYNA.TO 10 CAD 10 100 0\n" + a_extra)
        (root / "inputs/b/t.tt").write_text(
            "BUYSELL 2025-01-03 10:00:00 SYNA.TO 5 CAD 10 50 0\n" + b_extra)
        (root / "inputs/a/shared.tt").write_text(shared)
        (root / "inputs/b/shared.tt").write_text(shared)
        return root

    def test_a_split_file_in_both_accounts_passes_strict(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make(
                d, "# the split\nSPLIT 2025-03-03 10:00:00 SYNA.TO SYNA.TO 2\n",
                "BUYSELL 2025-07-02 10:00:00 SYNA.TO -20 CAD 12 240 0\n",
                "BUYSELL 2025-07-03 10:00:00 SYNA.TO -10 CAD 12 120 0\n")
            r = tj(root, "run", "--strict", "--no-input", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("the same export file", r.stdout + r.stderr)
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            self.assertAlmostEqual(fe["gain_unrounded"], 210.0, places=2)

    def test_a_rename_file_in_both_accounts_passes_strict(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make(
                d, "RENAME 2025-03-03 SYNA.TO NEWA.TO\n",
                "BUYSELL 2025-07-02 10:00:00 NEWA.TO -10 CAD 12 120 0\n",
                "BUYSELL 2025-07-03 10:00:00 NEWA.TO -5 CAD 12 60 0\n")
            r = tj(root, "run", "--strict", "--no-input", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("the same export file", r.stdout + r.stderr)
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            self.assertAlmostEqual(fe["gain_unrounded"], 30.0, places=2)

    def test_a_copied_file_with_a_row_still_stops(self):
        for line in ("BUYSELL 2025-02-03 10:00:00 SYNA.TO 1 CAD 10 10 0\n",
                     "DIVIDEND 2025-02-03 10:00:00 SYNA.TO 1 CAD 1 1 0\n"
                     "SPLIT 2025-03-03 10:00:00 SYNA.TO SYNA.TO 2\n"):
            with tempfile.TemporaryDirectory() as d:
                root = self._make(d, line)
                r = tj(root, "run", "--strict", "--no-input", check=False)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("the same export file", r.stdout + r.stderr)


class TestSnapshotReportsAreNoClosing(unittest.TestCase):
    """TJ-1247: reports all written within the year are a mid-year
    snapshot's: with the lock gone the year stays open."""

    def _make(self, d, stamp):
        root = _project(Path(d), {"margin": "taxable"}, year=2024)
        (root / "inputs/margin/t.tt").write_text(
            "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10 100 0\n"
            "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 5 25 0\n")
        tj(root, "run", "--no-input")
        rep = root / "filed/2024/reports"
        rep.mkdir(parents=True)
        for name in ("margin.sum", "margin.diag"):
            (rep / name).write_text("x\n")
            os.utime(rep / name, (stamp, stamp))
        return root

    def test_a_snapshot_leaves_the_year_open(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d, _ts(2024, 11, 15))
            r = tj(root, "check-filed", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("is missing", r.stdout + r.stderr)
            for step in ("filed-lock", "inputs-frozen"):
                out = tj(root, "checklist", "--run", "--only", step, "--json",
                         check=False).stdout
                self.assertNotIn("the year's folder shows", out)
                self.assertNotIn("filed with the exports", out)
            tj(root, "migrate", "--to-years")
            doc = json.loads(tj(root, "years", "--json").stdout)
            self.assertFalse(doc["years"][0]["filed"], doc)

    def test_dec_31_is_a_snapshot_and_jan_1_a_closing(self):
        from taxjson.lib import project_layout as PL
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d, _ts(2024, 12, 31))
            self.assertFalse(PL.lock_missing_frozen(root, 2024))
            one = root / "filed/2024/reports/margin.sum"
            os.utime(one, (_ts(2025, 1, 1), _ts(2025, 1, 1)))
            self.assertTrue(PL.lock_missing_frozen(root, 2024))

    def test_a_closing_after_the_year_still_reads_lock_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d, _ts(2025, 2, 15))
            r = tj(root, "check-filed", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("filed/2024.json is missing", r.stdout + r.stderr)

    def test_the_next_years_reader_agrees(self):
        from taxjson.bin import taxjson_filed as F
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d, _ts(2024, 11, 15))
            lock = root / "filed/2024.json"
            self.assertIsNone(F.missing_lock_problem(lock))
            for f in (root / "filed/2024/reports").iterdir():
                os.utime(f, (_ts(2025, 2, 15), _ts(2025, 2, 15)))
            self.assertIn("is missing", F.missing_lock_problem(lock))


class TestHeldFollowingLaterRename(unittest.TestCase):
    """TJ-1197: the still-held test follows a rename only once it has
    happened."""

    def _findings(self, rename_day, other_rows, plan_tail=""):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), {"margin": "taxable", "plan": "sheltered",
                                      "other": "taxable"})
            (root / "ticker.map").write_text("SIMILAR SYNA.TO SYNB.TO\n")
            (root / "inputs/margin/t.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYNA.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-06-02 09:30:00 SYNA.TO -10 CAD 8 80 0\n")
            (root / "inputs/plan/t.tt").write_text(
                "BUYSELL 2025-06-03 09:30:00 SYNB.TO 10 CAD 10 100 0\n"
                + plan_tail + f"RENAME {rename_day} SYNB.TO BIGC.TO\n")
            (root / "inputs/other/t.tt").write_text(other_rows)
            tj(root, "run", "--no-input")
            doc = json.loads((root / "work/similar.state").read_text())
            return [(f["loss_symbol"], f["other_symbol"])
                    for f in doc["findings"]]

    _BIG = "BUYSELL 2025-01-02 09:30:00 BIGC.TO 100 CAD 5 500 0\n"
    _SOLD = ("BUYSELL 2025-06-05 09:30:00 SYNB.TO -10 CAD 10 100 0\n"
             "BUYSELL 2025-08-01 09:30:00 SYNB.TO 5 CAD 10 50 0\n")

    def test_a_rename_after_day_30_does_not_count(self):
        self.assertEqual(self._findings("2025-09-01", self._BIG, self._SOLD),
                         [])

    def test_a_replacement_held_through_a_rename_inside_the_window_counts(self):
        self.assertEqual(self._findings("2025-06-20", ""),
                         [("SYNA.TO", "SYNB.TO")])

    def test_a_replacement_held_through_a_later_rename_counts(self):
        self.assertEqual(self._findings("2025-09-01", ""),
                         [("SYNA.TO", "SYNB.TO")])

    def test_held_following_directly(self):
        from datetime import date

        from taxjson.lib import xlist_loss_radar as XR
        rows = {"AAA.TO": [
            {"action": "BUYSELL", "symbol": "AAA.TO", "account": "p",
             "date": "2025-06-03", "date_settle": "2025-06-03", "qty": 5.0,
             "quantity": 5.0},
            {"action": "SPLIT", "symbol": "AAA.TO", "symbol_new": "BBB.TO",
             "account": "p", "date": "2025-07-02", "date_settle": "2025-07-02",
             "qty": 1.0, "quantity": 1.0}],
            "BBB.TO": [
            {"action": "BUYSELL", "symbol": "BBB.TO", "account": "x",
             "date": "2025-01-02", "date_settle": "2025-01-02", "qty": 100.0,
             "quantity": 100.0}]}
        held = XR.held_following
        self.assertEqual(held(rows, "AAA.TO", date(2025, 7, 1), "canada"), 5.0)
        self.assertEqual(held(rows, "AAA.TO", date(2025, 7, 2), "canada"),
                         105.0)


class TestMigrateFrozenCopyOfALink(unittest.TestCase):
    """TJ-1248: a filed year's copy of a relative link names the same
    file from where the copy sits."""

    def _make(self, d, link, target):
        root = _project(Path(d), {"margin": "taxable"}, year=2025)
        (root / "inputs/margin/t.tt").write_text(
            "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
            "BUYSELL 2025-02-03 10:00:00 SYN.TO -10 CAD 15 150 0\n")
        tgt = root / target
        tgt.parent.mkdir(parents=True, exist_ok=True)
        tgt.write_text(
            "BUYSELL 2025-03-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
            "BUYSELL 2025-03-20 10:00:00 SYN.TO -10 CAD 15 150 0\n")
        os.symlink(link, root / "inputs/margin/more.tt")
        tj(root, "run", "--no-input")
        tj(root, "close-year", "--yes")
        tj(root, "migrate", "--to-years")
        return root

    def _gain(self, root):
        tj(root / "2025", "run", "--no-input")
        fe = json.loads(tj(root / "2025", "form-export", "--json").stdout)
        return fe["gain_unrounded"]

    def test_a_link_to_a_folder_that_stays(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d, "../../maps/more.tt", "maps/more.tt")
            copy = root / "2025/inputs/margin/more.tt"
            self.assertEqual(os.readlink(copy), "../../../maps/more.tt")
            self.assertEqual(os.readlink(root / "inputs/margin/more.tt"),
                             "../../maps/more.tt")
            self.assertAlmostEqual(self._gain(root), 100.0, places=2)
            r = tj(root / "2025", "check-filed", check=False)
            self.assertIn("filed 2025: OK", r.stdout + r.stderr)

    def test_a_link_inside_the_account_stays_as_written(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make(d, "held/more.tt", "inputs/margin/held/more.tt")
            copy = root / "2025/inputs/margin/more.tt"
            self.assertEqual(os.readlink(copy), "held/more.tt")
            self.assertAlmostEqual(self._gain(root), 100.0, places=2)

    def test_a_read_only_account_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d), {"margin": "taxable"}, year=2025)
            acct = root / "inputs/margin"
            (acct / "t.tt").write_text(
                "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-02-03 10:00:00 SYN.TO -10 CAD 15 150 0\n")
            (root / "maps").mkdir()
            (root / "maps/more.tt").write_text("# none\n")
            os.symlink("../../maps/more.tt", acct / "more.tt")
            tj(root, "run", "--no-input")
            tj(root, "close-year", "--yes")
            acct.chmod(0o500)
            try:
                tj(root, "migrate", "--to-years")
                copy = root / "2025/inputs/margin"
                self.assertEqual(os.readlink(copy / "more.tt"),
                                 "../../../maps/more.tt")
                self.assertEqual(copy.stat().st_mode & 0o777, 0o500)
            finally:
                acct.chmod(0o700)
                if (root / "2025/inputs/margin").exists():
                    (root / "2025/inputs/margin").chmod(0o700)

    def test_an_absolute_link_stays_as_written(self):
        with tempfile.TemporaryDirectory() as d:
            target = str(Path(d) / "maps/more.tt")
            root = self._make(d, target, "maps/more.tt")
            copy = root / "2025/inputs/margin/more.tt"
            self.assertEqual(os.readlink(copy), target)
            self.assertAlmostEqual(self._gain(root), 100.0, places=2)


if __name__ == "__main__":
    unittest.main()
