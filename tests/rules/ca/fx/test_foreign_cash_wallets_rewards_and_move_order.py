"""Backlog group fxcorp: the FX-on-cash ledger v2 (lib/fx_cash_v2, the
Kraken cash reader) and the spin-off views and checks (lib/corp_views,
lib/missing_history, lib/positions_check, `tjs sanity`). One test
per backlog item, each named for it. Synthetic data only.

Formerly: test_fix_backlog_fxcorp"""
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as FX
from taxjson.lib import timezones as TZ

from tax_rules import rule
from taxjson.rules import ids as R
from _support.fx_cash_run import record_run
from _support.qt_spinoff import _cli


def _tt(book, line, n):
    """A .tt cash line of `book` (acct/label), as the reader gives it."""
    where = f"inputs/{book.split('/')[0]}/cash.tt:{n}"
    e = CL.parse_line(line, where)
    e.update(book=book, origin="tt", where=where,
             label=book.split("/")[1])
    return e


def _build(lines=(), events=(), year=2026, native=(), carry=None,
           rate=1.40, base="CAD", country="canada", **cash):
    return FX.build(list(native), dict(cash, events=list(events),
                                       lines=list(lines)),
                    base, year, lambda c, d: rate, country=country,
                    carry=carry)


# ------------------------------------------------------------ TJ-0019

_KR_HEAD = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"


def _kraken(text):
    """The Kraken parse's cash events of `text` as a ledger export, on Toronto
    time."""
    from _support.parser_cash import parsed_cash_events
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(TZ, "local_tz_name",
                         return_value="America/Toronto"):
        p = Path(tmp) / "kraken_demo.csv"
        p.write_text(text)
        return parsed_cash_events("kraken", p)


class TestKrakenWalletBalances(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestKrakenWalletBalances"""
    def test_tj0019_wallet_suffixed_asset_keeps_its_own_balance(self):
        """TJ-0019: a reward wallet's asset (USDC.F, USD.HOLD) shared
        its balance series with the spot asset: the day's CASHBAL took
        the reward wallet's 1.50 as the whole USDC balance, and ledger
        v2 refused the year with a gap it called a missing deposit.
        Each asset code keeps its own balance, summed into the
        currency."""
        for wallet in ("USDC.F", "USD.HOLD"):
            data = (_KR_HEAD
                    + "L1,R1,2024-02-01 15:00:00,deposit,,currency,ZUSD,"
                      "1000,0,1000\n"
                    + "L2,R2,2024-02-01 15:05:00,deposit,,currency,USDC,"
                      "100,0,100\n"
                    + f"L3,R3,2024-10-02 15:00:00,earn,reward,currency,"
                      f"{wallet},1.5,0,1.5\n")
            bals = {(e["date"], e["currency"]): e["balance"]
                    for e in _kraken(data) if e["kind"] == "CASHBAL"}
            self.assertAlmostEqual(bals[("2024-10-02", "USD")], 1101.5,
                                   msg=wallet)


# ------------------------------------------------------------ TJ-0022

def _fx_v2(root):
    """`fx-cash --ledger v2 --json` over the inputs as they are now (what a
    run would leave for the hand-built books: `record_run`)."""
    record_run(root)
    return _cli(root, "fx-cash", "--ledger", "v2", "--json")


def _v2_project(root: Path, country="canada", cash_tt=""):
    """A 2026 project opted into ledger v2 whose books are already built
    (an empty work/margin_sorted.json)."""
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "filed").mkdir(exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n'
        f'fx_cash_ledger = "v2"\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_sorted.json").write_text(
        json.dumps({"transactions": []}))
    if cash_tt:
        (root / "inputs" / "margin" / "cash.tt").write_text(cash_tt)


class TestCarryOtherCountry(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestCarryOtherCountry"""
    def test_tj0022_cashopen_lines_open_a_year_after_a_foreign_record(self):
        """TJ-0022: a US year after a Canadian record was refused, and
        the advice ("open the year with CASHOPEN lines") did not clear
        the refusal; a record closed under the default ledger (no pool
        to carry) was refused too. Now a record with no fx_cash_v2 pool
        is no carry, whatever its country, and with CASHOPEN lines the
        other country's pool is a note, not a problem."""
        from taxjson.pipeline.run import _fx_cash_carry
        pool = {"year": 2025, "currency": "CAD", "books": {
            "margin/ib": {"USD": {"units": 100, "cost": 140}}}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _v2_project(root, country="usa")
            rec = root / "filed" / "2025.json"
            # Closed under the default ledger: nothing to carry.
            rec.write_text(json.dumps({"year": 2025, "country": "canada"}))
            self.assertIsNone(_fx_cash_carry(root, {}, 2026,
                                             country="usa"))
            r = _fx_v2(root)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            out = json.loads(r.stdout)
            self.assertEqual(out["problems"], [])
            # A Canadian pool: refused without CASHOPEN lines (C04) ...
            rec.write_text(json.dumps({"year": 2025, "country": "canada",
                                       "fx_cash_v2": pool}))
            out = json.loads(_fx_v2(root).stdout)
            self.assertEqual(out["status"], "not_computed")
            self.assertIn("Canada law", out["problems"][0]["text"])
            # ... and noted when CASHOPEN lines open the year.
            (root / "inputs" / "margin" / "cash.tt").write_text(
                "CASHOPEN 2026-01-01 CAD 100.00 72.00\n")
            r = _fx_v2(root)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "computed", out["problems"])
            self.assertEqual(out["carried_in"], 0)
            self.assertEqual(out["pools_year_end"],
                             {"CAD": {"units": 100.0, "acb": 72.0}})
            self.assertTrue(any("not carried into this United States year"
                                in n for n in out["notes"]), out["notes"])


# ------------------------------------------------------------ TJ-0028

class TestZeroBookClose(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestZeroBookClose"""
    @rule(R.CA_FX_07)
    def test_tj0028_close_records_a_book_that_ends_at_zero(self):
        """TJ-0028: the close left out a book that ended the year at
        zero, so every later year asked for `CASHOPEN ... 0 0` for it
        and to re-close the prior year (which writes the same record).
        The close records it at zero, the next year opens it at zero,
        and a book missing from a v2 close is not sent to re-close."""
        y24 = [_tt("a/a", "CASHOPEN 2024-01-01 USD 0 0", 1),
               _tt("a/a", "FXCONV 2024-03-01 CAD 1350.00 USD 1000.00", 2),
               _tt("a/a", "CASHMOVE 2024-03-02 USD -1000.00 own", 3),
               _tt("a/a", "CASHBAL 2024-12-31 USD 0.00", 4),
               _tt("b/b", "CASHOPEN 2024-01-01 USD 0 0", 1),
               _tt("b/b", "CASHMOVE 2024-03-02 USD 1000.00 own", 2),
               _tt("b/b", "CASHBAL 2024-12-31 USD 1000.00", 3)]
        d24 = _build(lines=y24, year=2024)
        self.assertEqual(d24["status"], "computed", d24["problems"])
        self.assertEqual(d24["close"]["books"]["a/a"]["USD"],
                         {"units": 0.0, "cost": 0.0})
        self.assertEqual(d24["close"]["books"]["b/b"]["USD"],
                         {"units": 1000.0, "cost": 1350.0})
        y25 = [_tt("a/a", "CASHBAL 2025-12-31 USD 0.00", 1),
               _tt("b/b", "FXCONV 2025-03-03 USD 500.00 CAD 700.00", 1),
               _tt("b/b", "CASHBAL 2025-12-31 USD 500.00", 2)]
        d25 = _build(lines=y25, year=2025, carry=d24["close"])
        self.assertEqual(d25["status"], "computed", d25["problems"])
        self.assertAlmostEqual(d25["net_gain"], 25.0)
        # A close written before the fix (no a/a): the message no longer
        # asks to close 2024 with this ledger.
        old = dict(d24["close"], books={"b/b": d24["close"]["books"]["b/b"]})
        d25 = _build(lines=y25, year=2025, carry=old)
        self.assertEqual(d25["status"], "not_computed")
        text = d25["problems"][0]["text"]
        self.assertIn("no opening pool for a/a USD", text)
        self.assertIn("the close of 2024 does not list it", text)
        self.assertNotIn("close 2024 with this ledger", text)
        # No close at all: closing the prior year is still the remedy.
        d25 = _build(lines=y25, year=2025)
        self.assertIn("or close 2024 with this ledger",
                      d25["problems"][0]["text"])


# ------------------------------------------------------------ TJ-0038

class TestRewardPricedInBase(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestRewardPricedInBase"""
    @rule(R.CA_FX_07)
    def test_tj0038_usdc_reward_priced_in_cad_brings_the_dollars_in(self):
        """TJ-0038: a Coinbase USDC reward priced in CAD was booked as
        CAD income only: the ledger never took the 5 US dollars in and
        refused the year with a gap it called a missing deposit. The
        reward's US dollars come in at the income's CAD value."""
        reward = {"action": "DIVIDEND", "description": "Staking Reward",
                  "symbol": "USDC", "quantity": 5.0, "currency": "CAD",
                  "price": 1.37, "gross_amount": 6.85, "net_amount": 6.85,
                  "date": "2025-07-02", "date_settle": "2025-07-02",
                  "account": "crypto", "source": "cb.csv",
                  "_book": "crypto/coinbase"}
        lines = [_tt("crypto/coinbase",
                     "CASHOPEN 2025-01-01 USD 0 0 at=coinbase", 1),
                 _tt("crypto/coinbase",
                     "CASHBAL 2025-12-31 USD 5.00 at=coinbase", 2)]
        doc = _build(lines=lines, native=[reward], year=2025,
                     stablecoins_as_cash=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 5.0, "acb": 6.85}})
        self.assertAlmostEqual(doc["net_gain"], 0.0)
        # Priced in USD (the reader's own case): at the day's rate.
        doc = _build(lines=lines, year=2025, native=[dict(
            reward, currency="USD", price=1.0, gross_amount=5.0,
            net_amount=5.0)], stablecoins_as_cash=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 5.0, "acb": 7.0}})


# ------------------------------------------------------------ TJ-0067

def _own(td, amount, sd):
    return dict(CL.move(td, "USD", amount, settle=sd), decl={"how": "own"})


def _opening(book, units, cost):
    return dict(kind="CASHOPEN", book=book, date="2026-01-01",
                currency="USD", units=units, cost=cost, origin="tt")


class TestOwnMoveOrder(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestOwnMoveOrder"""
    @rule(R.CA_FX_07)
    def test_tj0067_arrival_settling_before_its_departure(self):
        """TJ-0067 (c): an own arrival that settles the day before its
        departure (the receiving broker credited it first) was refused
        ("only 0.00 USD had left one of them before it"). It is walked
        on the departure: the units stay in the pool, no figure moves.
        One a week or more early is still refused, with the reason."""
        def doc(dep_sd):
            return _build(
                lines=[_opening("margin/a", 100, 135),
                       _opening("margin/b", 0, 0)],
                events=[dict(_own("2026-03-03", 100, "2026-03-03"),
                             book="margin/b"),
                        dict(_own(dep_sd, -100, dep_sd), book="margin/a"),
                        dict(CL.balance(dep_sd, "USD", 0), book="margin/a"),
                        dict(CL.balance(dep_sd, "USD", 100),
                             book="margin/b")])
        d = doc("2026-03-04")
        self.assertEqual(d["status"], "computed", d["problems"])
        self.assertAlmostEqual(d["net_gain"], 0.0)
        self.assertEqual(d["borrowed"], [])
        self.assertEqual(d["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 135.0}})
        d = doc("2026-03-20")
        self.assertEqual(d["status"], "not_computed")
        text = [p["text"] for p in d["problems"] if p["kind"] == "own"][0]
        self.assertIn("margin/a's on 2026-03-20, settles more than 7 days "
                      "after it", text)

    @rule(R.CA_FX_07)
    def test_tj0067_chain_through_an_account_on_one_settle_day(self):
        """TJ-0067 (b): a chain a -> b -> c settling on one day made up
        a debt of b (net gain 2.5, pool cost 137.5) in 7 of the 12
        orders whose broker dates b's arrival before its departure. The
        arrival is walked on the departure of the account that holds
        the units, and b's own departure after b's arrival."""
        evs = {"a-": ("margin/a", -100), "b+": ("margin/b", 100),
               "b-": ("margin/b", -100), "c+": ("margin/c", 100)}
        n = 0
        for perm in itertools.permutations(evs):
            if perm.index("b+") > perm.index("b-"):
                continue        # b's own broker dates it out first
            events = [dict(_own(f"2026-03-0{k + 1}", evs[name][1],
                                "2026-03-05"), book=evs[name][0])
                      for k, name in enumerate(perm)]
            events += [dict(CL.balance("2026-03-05", "USD", v), book=b)
                       for b, v in (("margin/a", 0), ("margin/b", 0),
                                    ("margin/c", 100))]
            d = _build(lines=[_opening("margin/a", 100, 135),
                              _opening("margin/b", 0, 0),
                              _opening("margin/c", 0, 0)], events=events)
            self.assertEqual(d["status"], "computed", (perm, d["problems"]))
            self.assertAlmostEqual(d["net_gain"], 0.0, msg=perm)
            self.assertEqual(d["borrowed"], [], perm)
            self.assertEqual(d["pools_year_end"],
                             {"USD": {"units": 100.0, "acb": 135.0}}, perm)
            n += 1
        self.assertEqual(n, 12)


if __name__ == "__main__":
    unittest.main()
