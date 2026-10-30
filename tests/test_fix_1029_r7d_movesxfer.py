"""Fix round 7 (wave D) of 2026-10-29, group movesxfer: transfers,
in-kind moves and handoff.

TJ-1054: a .tt sale between two arrivals sold the first arrival's units
          even when the broker had already sold them or the account held
          .tt units of its own, so the second arrival, with no purchase
          left, read as covered and was never booked.
TJ-1036: `tjs journals` listed one ambiguous journal leg once per leg it
          could pair with: the suggestion repeated, the count inflated.
TJ-1037: handoff's own-sale replay left out a split reported in another
          account's books, so a closed-year sale out of the split units
          was not its own row and a same-size January sale read as a double.
TJ-1069: the book cost a rename carries averaged an option's premiums
          where the books pair them oldest first.
TJ-1119: `tjs years` and check-filed read the deleted lock of a year closed
          as a single folder, then migrated, as a year never closed.
TJ-1118: a shared-exports run at a terminal lost its in-memory legacy-id
          migration when it saved an answer, then stopped with a traceback.
TJ-1055: an in-kind contribution of an arrival booked at book value was
          charged to the .tt purchases, so a later arrival's own purchase
          looked spent and that arrival was booked on top of it.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _style import CapturedWidth
from tax_rules import rule
from test_fix_1029_r7_transfers import (_QH, _TAXABLE, _TFSA, _cli,
                                        _gains_with_context, _held,
                                        _project, _run, _totals)

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tf(day: str, qty: int, book: int, sym: str = "SYN.TO") -> str:
    what = f"TRANSFER BOOK VALUE {book}.00" if book else "TRANSFER OUT"
    return (f"{day},{day},TF6,{sym},SYN CORP {what},{qty},0,0,0,0,CAD,"
            f"Transfers,99900001,Margin\n")


def _sell(day: str, qty: int, net: int, sym: str = "SYN.TO") -> str:
    return (f"{day},{day},Sell,{sym},SYN CORP,-{qty},{net / qty},{net},0,"
            f"{net},CAD,Trades,99900001,Margin\n")


def _margin(td: str, name: str, tt: str, csv: str, **accounts) -> Path:
    files = {"inputs/margin/trades.tt": tt, "inputs/margin/qt.csv": _QH + csv}
    return _project(Path(td) / name, {"margin": _TAXABLE, **accounts}, files)


class TestSaleBetweenArrivals(unittest.TestCase):
    """TJ-1054, with TJ-0823's own case kept (the second purchase dated
    after the sale: tests/test_fix_1029_r7_transfers.py)."""

    def test_broker_sold_the_first_arrival(self):
        tt = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
              "BUYSELL 2025-03-03 09:30:00 SYN.TO 10 CAD 12 120 0\n"
              "BUYSELL 2025-03-05 09:30:00 SYN.TO -10 CAD 13 130 0\n")
        csv = (_tf("2025-02-03", 10, 100) + _sell("2025-02-10", 10, 150)
               + _tf("2025-04-01", 10, 300) + _sell("2025-06-02", 10, 400))
        with tempfile.TemporaryDirectory() as td:
            root = _margin(td, "p", tt, csv)
            _run(self, root)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 160.0,
                                   places=2)
            self.assertEqual(_held(root), [])

    def test_sale_of_the_accounts_own_tt_units(self):
        tt = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
              "BUYSELL 2025-01-03 09:30:00 SYN.TO 10 CAD 10 100 0\n"
              "BUYSELL 2025-02-10 09:30:00 SYN.TO -10 CAD 15 150 0\n")
        csv = (_tf("2025-02-03", 10, 100) + _tf("2025-03-10", 10, 300)
               + _sell("2025-06-02", 20, 800))
        with tempfile.TemporaryDirectory() as td:
            root = _margin(td, "p", tt, csv)
            _run(self, root)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 450.0,
                                   places=2)
            self.assertEqual(_held(root), [])

    def test_a_short_tt_round_trip_after_the_broker_sale(self):
        """The broker sold the arrived units, so a .tt sale with no .tt
        units on hand has no arrived units left to sell either: the .tt
        purchase after it closes that short and covers no arrival."""
        from taxjson.lib import transfer_in as TI

        def tx(day, q):
            return {"action": "BUYSELL", "date": day, "time": "09:30:00",
                    "symbol": "SYN.TO", "quantity": q}

        def arr(day):
            return TI.Arrival(
                account="m", symbol="SYN.TO", key="SYN.TO", date=day,
                time="09:30:00", quantity=10.0, currency="CAD",
                book_value=100.0, broker="questrade", source="",
                description="", row={"time": "09:30:00"})
        tt = [tx("2025-01-02", 10), tx("2025-03-03", -10),
              tx("2025-03-05", 10)]
        found = [arr("2025-02-03"), arr("2025-04-01")]
        TI.mark_covered(found, {"m": tt},
                        broker_rows={"m": [tx("2025-02-10", -10)]})
        self.assertEqual([a.status for a in found],
                         ["covered", "book_value"])


class TestContributedBookedArrival(unittest.TestCase):
    """TJ-1055: A0 is booked at 100 and contributed at 120 (gain 20); A1
    is covered by its purchase of 200 and sold for 400 (gain 200)."""

    def test_contribution_spends_no_later_purchase(self):
        tt = ("BUYSELL 2025-04-01 09:30:00 SYN.TO 10 CAD 20 200 0\n"
              "INKIND 2025-03-03 SYN.TO -10 CAD 12 plan=tfsa\n")
        csv = (_tf("2025-02-03", 10, 100) + _tf("2025-03-03", -10, 0)
               + _tf("2025-05-01", 10, 250) + _sell("2025-06-02", 10, 400))
        plan = _QH + ("2025-03-03,2025-03-03,TF6,SYN.TO,SYN CORP TRANSFER "
                      "IN,10,0,0,0,0,CAD,Transfers,99900002,TFSA\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p",
                            {"margin": _TAXABLE, "plan": _TFSA},
                            {"inputs/margin/trades.tt": tt,
                             "inputs/margin/qt.csv": _QH + csv,
                             "inputs/plan/qt.csv": plan})
            for _ in range(2):      # the in-kind file exists from run 2
                _run(self, root)
                self.assertIn(".tt_covers", _cli(root, "transfers").stdout)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 220.0,
                                   places=2)
            self.assertEqual(
                [r for r in _held(root) if r[0] == "margin"], [])


class TestMoveInParts(unittest.TestCase):
    """TJ-1102: a move between registered accounts that posts in parts,
    or gathers two senders' units in one leg, carries the replacement as
    a single-leg move does (TJ-0654). A 400 loss on January 20."""

    def _denied(self, *rows) -> tuple:
        from tax_rules.dual import tx
        g = _gains_with_context(
            [tx(a, d, "SYN.TO", q, n, currency="CAD", account=acct, id=i)
             for i, a, d, q, n, acct in rows])
        return (round(sum(float(t.get("disallowed_amount") or 0.0)
                          for t in g["transactions"]), 2),
                round(sum(float(t.get("permanently_disallowed") or 0.0)
                          for t in g["transactions"]), 2))

    BUY = ("rep", "BUYSELL", "2025-01-10", 100, 500, "r1")
    OUT = ("o", "TRANSFER", "2025-01-15", -100, 0, "r1")
    PARTS = (("i1", "TRANSFER", "2025-01-15", 60, 0, "r2"),
             ("i2", "TRANSFER", "2025-01-16", 40, 0, "r2"))

    @rule("CA-SL-08")
    def test_received_in_parts(self):
        self.assertEqual(self._denied(self.BUY, self.OUT, *self.PARTS),
                         (400.0, 400.0))
        # The first part listed before the out leg.
        self.assertEqual(
            self._denied(self.BUY, self.PARTS[0], self.OUT, self.PARTS[1]),
            (400.0, 400.0))

    @rule("CA-SL-08")
    def test_sent_in_parts(self):
        self.assertEqual(self._denied(
            self.BUY, ("o1", "TRANSFER", "2025-01-15", -60, 0, "r1"),
            ("o2", "TRANSFER", "2025-01-15", -40, 0, "r1"),
            ("i", "TRANSFER", "2025-01-16", 100, 0, "r2")), (400.0, 400.0))

    @rule("CA-SL-08")
    def test_two_senders_one_leg(self):
        self.assertEqual(self._denied(
            ("rep1", "BUYSELL", "2025-01-10", 60, 300, "r1"),
            ("rep3", "BUYSELL", "2025-01-10", 40, 200, "r3"),
            ("o1", "TRANSFER", "2025-01-15", -60, 0, "r1"),
            ("o3", "TRANSFER", "2025-01-15", -40, 0, "r3"),
            ("i", "TRANSFER", "2025-01-15", 100, 0, "r2")), (400.0, 400.0))

    @rule("CA-SL-02")
    def test_part_sold_before_day_30_backs_only_the_rest(self):
        self.assertEqual(self._denied(
            self.BUY, self.OUT, *self.PARTS,
            ("s", "BUYSELL", "2025-02-05", -60, 330, "r2")), (160.0, 160.0))

    @rule("CA-SL-02")
    def test_older_units_in_parts_back_nothing(self):
        self.assertEqual(self._denied(
            ("r1old", "BUYSELL", "2024-03-01", 100, 900, "r1"),
            self.OUT, *self.PARTS), (0.0, 0.0))


def _rbc_row(day, action, q, cur, desc, price=0, value=0):
    return [day, action, "SYN", "SYNTHETIC FUND", q, price, day, "99900001",
            value, cur, desc]


class TestAmbiguousJournalLegs(unittest.TestCase):
    """TJ-1036: no J~ reference, so nothing tells the same-day legs
    apart; one suggestion per journal they can be. TJ-0850's two distinct
    references stay two journals (tests/test_fix_1029_r7b_handoff.py)."""

    def _journals(self, outs: int, ins: int) -> dict:
        rows = [["Date", "Activity", "Symbol", "Symbol Description",
                 "Quantity", "Price", "Settlement Date", "Account",
                 "Value", "Currency", "Description"],
                _rbc_row("March 11, 2025", "Buy", 40, "USD",
                         "SYNTHETIC FUND UNSOLICITED DA", 10, -400)]
        rows += [_rbc_row("May 12, 2025", "Transfers", -10, "USD",
                          "TFR - SYNTHETIC FUND TRANSFER TO C$")] * outs
        rows += [_rbc_row("May 12, 2025", "Transfers", 10, "CAD",
                          "TFR - SYNTHETIC FUND TRANSFER FROM U$")] * ins
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", {"a": _TAXABLE}, {
                "inputs/a/rbc.csv":
                    '"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"'
                    "\n\n" + buf.getvalue()})
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]'))
            (root / "work").mkdir()
            d, rates = date(2025, 1, 1), []
            while d <= date(2026, 1, 31):
                rates.append(f"{d} 12:00:00 USD CAD 1.3 boc")
                d += timedelta(days=1)
            (root / "work/to_base.csv").write_text("\n".join(rates) + "\n")
            _cli(root, "run", "--no-input")
            return json.loads(_cli(root, "journals", "--json").stdout)

    def test_one_per_journal_it_can_be(self):
        for outs, ins, want in ((1, 2, 1), (2, 1, 1), (2, 2, 2)):
            rep = self._journals(outs, ins)
            self.assertEqual(
                (len(rep["journals"]), rep["counts"]["suggested"],
                 rep["pending"]), (want, want, want), f"{outs}x{ins}")

    def test_candidates_of_another_listing_or_day_stay(self):
        """A candidate the user must choose between stays listed: one
        out-leg that could be either of two listings, or one in-leg that
        could take either of two days' out-legs."""
        from taxjson.lib import cross_listings as XL

        def leg(sym, day, q):
            return XL.Leg("a", "rbc_direct", sym, day, q)

        def pairs(*legs):
            r = XL.analyze(list(legs), {}, {}, base_currency="CAD")
            return sorted((p.out.symbol, p.out.date, p.into.symbol)
                          for p in r["suggested"])
        self.assertEqual(
            pairs(leg("SYN.US", "2025-05-12", -10),
                  leg("SYN.TO", "2025-05-12", 10),
                  leg("SYN.TO", "2025-05-12", 10)),
            [("SYN.US", "2025-05-12", "SYN.TO")])
        self.assertEqual(
            pairs(leg("SYN.US", "2025-05-12", -10),
                  leg("SYN.US", "2025-05-13", -10),
                  leg("SYN.TO", "2025-05-13", 10)),
            [("SYN.US", "2025-05-12", "SYN.TO"),
             ("SYN.US", "2025-05-13", "SYN.TO")])
        self.assertEqual(
            pairs(leg("SYN.US", "2025-05-12", -10),
                  leg("SYN.TO", "2025-05-12", 10),
                  leg("SYN.V", "2025-05-12", 10)),
            [("SYN.US", "2025-05-12", "SYN.TO"),
             ("SYN.US", "2025-05-12", "SYN.V")])


class TestHandoffSplitInAnotherAccount(unittest.TestCase):
    """TJ-1037, with TJ-0808's own case kept (the split in the selling
    account's books: tests/test_fix_1029_r7b_handoff.py)."""

    SPLIT = "SPLIT 2024-12-02 09:30:00 SYN.TO SYN.TO 2\n"

    def _handoff(self, a_extra: str, b_extra: str):
        a = "BUYSELL 2024-01-02 09:30:00 SYN.TO 1 CAD 10 10 0\n" + a_extra
        b = (b_extra + "BUYSELL 2024-01-02 09:30:00 SYN.TO 1 CAD 10 10 0\n"
             "BUYSELL 2024-12-30 09:30:00 SYN.TO -2 CAD 15 30 0\n"
             "BUYSELL 2024-12-31 09:30:00 SYN.TO 2 CAD 10 20 0\n")
        accounts = {"a": _TAXABLE, "b": _TAXABLE}
        with tempfile.TemporaryDirectory() as td:
            old = _project(Path(td) / "2024", accounts,
                           {"inputs/a/t.tt": a, "inputs/b/t.tt": b},
                           year=2024)
            _run(self, old)
            r = _cli(old, "close-year", "--yes", "--force")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            p = _project(
                Path(td) / "2025", accounts,
                {"inputs/a/t.tt": a,
                 "inputs/b/t.tt":
                     b + "BUYSELL 2025-01-02 09:30:00 SYN.TO -2 CAD 15 30 0\n"},
                tail="")
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "[settings]\n",
                '[settings]\nprior_year_record = "../2024/filed/2024.json"\n',
                1))
            _run(self, p)
            r = _cli(p, "handoff", "--json")
            return r.returncode, json.loads(r.stdout)

    def test_split_in_another_accounts_books(self):
        rc, rep = self._handoff(self.SPLIT, "")
        self.assertEqual((rc, rep["double"]), (0, []), rep)

    def test_split_in_the_selling_account_or_both(self):
        for a, b in (("", self.SPLIT), (self.SPLIT, self.SPLIT)):
            rc, rep = self._handoff(a, b)
            self.assertEqual((rc, rep["double"]), (0, []), rep)


class TestRenameCarriesTheClosedContractsPremium(unittest.TestCase):
    """TJ-1069: the carried book cost is what the engine's books carry
    into NEW, with TJ-0852's single-contract case kept
    (tests/test_fix_1029_r7b_elect.py)."""

    PUTS = ("BUYSELL 2025-01-02 10:00:00 SYN250620P00010000{s} -1 {c} 1 100 0\n"
            "BUYSELL 2025-02-03 10:00:00 SYN250620P00010000{s} -1 {c} 5 500 0\n"
            "ASSIGN 2025-06-10 16:00:00 SYN250620P00010000{s} 1 {c} 0 0 0\n"
            "ASSIGN 2025-06-10 16:01:00 SYN{s} 100 {c} 10 1000 0\n")
    CALLS = ("BUYSELL 2025-01-02 10:00:00 SYN250620C00010000{s} 1 {c} 1 100 0\n"
             "BUYSELL 2025-02-03 10:00:00 SYN250620C00010000{s} 1 {c} 5 500 0\n"
             "ASSIGN 2025-06-10 16:00:00 SYN250620C00010000{s} -1 {c} 0 0 0\n"
             "ASSIGN 2025-06-10 16:01:00 SYN{s} 100 {c} 10 1000 0\n")

    def _carried(self, td: str, country: str, book: str):
        cur, sfx = ("CAD", ".US") if country == "canada" else ("USD", "")
        root = _project(
            Path(td) / "p", {"margin": _TAXABLE},
            {"inputs/margin/book.tt": book.format(c=cur, s=sfx)
             + f"RENAME 2025-07-02 SYN{sfx} NEW{sfx}\n"}, country=country)
        _run(self, root)
        doc = json.loads(_cli(root, "renames", "--json").stdout)
        wash = root / "work" / "margin_gains_wash.json"
        inv = json.loads((wash if wash.exists()
                          else root / "work" / "margin_gains.json")
                         .read_text())["inventory"]
        held = {x["symbol"]: round(float(x["total_cost"]), 2) for x in inv}
        return (doc["renames"][0]["carried"][0]["book_cost"],
                held[f"NEW{sfx}"])

    def test_carried_cost_is_the_books(self):
        for country, book, want in (("canada", self.PUTS, 900),
                                    ("usa", self.PUTS, 900),
                                    ("usa", self.CALLS, 1100),
                                    ("canada", self.CALLS, 1300)):
            with tempfile.TemporaryDirectory() as td:
                carried, books = self._carried(td, country, book)
                self.assertEqual((carried, books), (want, want),
                                 (country, want))


class TestDeletedLockOfAMigratedYear(unittest.TestCase):
    """TJ-1119: the reports close-year kept show the year was closed (the
    sign TJ-0272 gave the next year's readers); its own commands say
    `lock missing`, as with a freeze note."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.top = top = Path(cls._td.name) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
            "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 5.00 25.00 0.00\n")
        (top / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
        for d, args in ((top, ("run", "--no-input")),
                        (top, ("close-year", "--yes")),
                        (top, ("migrate", "--to-years")),
                        (top / "2024", ("new-year", "2025"))):
            r = _cli(d, *args)
            assert r.returncode == 0, r.stdout + r.stderr
        (top / "2024" / "filed" / "2024.json").unlink()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_years_says_lock_missing(self):
        r = _cli(self.top, "years")
        self.assertIn("lock missing", r.stdout, r.stdout + r.stderr)
        self.assertIn("filed/2024.json is missing", " ".join(r.stdout.split()))
        doc = json.loads(_cli(self.top, "years", "--json").stdout)
        y = next(x for x in doc["years"] if x["year"] == 2024)
        self.assertTrue(y["filed"])
        self.assertIn("filed/2024.json is missing", y["problem"])

    def test_check_filed_says_so_and_fails(self):
        r = _cli(self.top / "2024", "check-filed")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("filed/2024.json is missing",
                      " ".join((r.stdout + r.stderr).split()))

    def test_no_sign_or_a_year_not_ended_is_no_closed_year(self):
        from datetime import date as _date
        from taxjson.lib import project_layout as PL
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "taxjson.toml").write_text("[settings]\n")
            self.assertFalse(PL.lock_missing_frozen(d, 2024))
            rep = d / "filed" / "2024" / "reports"
            rep.mkdir(parents=True)
            self.assertFalse(PL.lock_missing_frozen(d, 2024))   # empty
            (rep / "sum.txt").write_text("x")
            self.assertTrue(PL.lock_missing_frozen(d, 2024))
            # A lock taken during the year keeps reports too.
            y = _date.today().year
            rep = d / "filed" / str(y) / "reports"
            rep.mkdir(parents=True)
            (rep / "sum.txt").write_text("x")
            self.assertFalse(PL.lock_missing_frozen(d, y))


class TestSharedExportsPromptKeepsTheMigration(unittest.TestCase):
    """TJ-1118: a year folder reading ../inputs, one spin-off's election
    under its legacy id, the other's asked at the terminal. Both are
    booked (as `run --no-input` books the first alone), the shared
    manifest keeps the legacy id (TJ-0848) and takes the new answer."""

    def test_both_events_are_booked(self):
        import shutil
        from taxjson.lib import corp_actions as ca
        from test_fix_1029_r7b_elect import (_IB_HEADER, _ib_spinoff,
                                             _on_a_terminal, _save)
        from test_fix_1029_r7b_elect import _project as _book_project
        with tempfile.TemporaryDirectory() as d:
            top = Path(d) / "taxes"
            year = _book_project(top / "y", {"margin": ""},
                                 settings='inputs_dir = "../inputs"\n')
            year = year.rename(top / "2025")
            shutil.rmtree(year / "inputs" / "margin")
            shared = top / "inputs" / "margin"
            shared.mkdir(parents=True)
            (shared / "book.tt").write_text(
                "BUYSELL 2025-01-02 09:00:00 PARENT.US 10 CAD 10 100 0\n")
            csv_file = shared / "ib.csv"
            csv_file.write_text(_IB_HEADER + _ib_spinoff("2025-06-02",
                                                         "CHILDONE")
                                + _ib_spinoff("2025-07-02", "CHILDTWO"))
            one, two = ca.parse_ib_corporate_actions(csv_file,
                                                     account="margin")
            old = one.legacy_event_id()
            self.assertNotEqual(old, one.event_id)
            path = shared / "manifest.json"
            _save(path, {old: ("taxable_deemed_dividend", one.summary(),
                               {"fmv_per_share": 10})})
            keys = [k for k, _ in ca.options_for("canada", "spinoff")]
            n = keys.index("ignore") + 1
            rc, out, err = _on_a_terminal(year, "run",
                                          answers=f"{n}\n\n{n}\n\n")
            self.assertEqual(rc, 0, out[-1500:] + err[-1500:])
            self.assertNotIn("Traceback", err)
            self.assertNotIn(f"event {one.event_id} unresolved", err)
            recs = ca.Manifest.load(path).records
            self.assertEqual(sorted(recs), sorted([old, two.event_id]))
            self.assertEqual(recs[two.event_id].election, "ignore")


if __name__ == "__main__":
    unittest.main()
