"""Backlog group fxcorp: the FX-on-cash ledger v2 (lib/fx_cash_v2, the
Kraken cash reader) and the spin-off views and checks (lib/corp_views,
lib/missing_history, lib/positions_check, `tjs sanity`). One test
per backlog item, each named for it. Synthetic data only."""
import itertools
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as FX
from taxjson.lib.brokerages import _crypto_common as CC
from taxjson.lib.dispatch import python_module_argv

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *args])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=e, stdin=subprocess.DEVNULL,
                          timeout=600)


def _tt(book, line, n):
    """A .tt cash line of `book` (acct/label), as the reader gives it."""
    where = f"inputs/{book.split('/')[0]}/cash.tt:{n}"
    e = CE.parse_line(line, where)
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
    """kraken_cash_events over `text` as a ledger export, on Toronto
    time."""
    from taxjson.lib.brokerages.kraken import kraken_cash_events
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(CC, "local_tz_name",
                         return_value="America/Toronto"):
        p = Path(tmp) / "kraken_demo.csv"
        p.write_text(text)
        return kraken_cash_events(p, True)


class TestKrakenWalletBalances(unittest.TestCase):
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


# ------------------------------------------------------ TJ-0054, TJ-0168

class TestCashEventsRead(unittest.TestCase):
    def _read(self):
        dep = dict(CE.move("2026-02-01", "USD", 1000.0,
                           settle="2026-02-01"),
                   book="crypto/kraken", label="kraken", origin="broker",
                   desc="Kraken deposit USD")
        ln = _tt("crypto/kraken",
                 "CASHMOVE 2026-02-01 USD 1000.00 cost=1340.00 at=kraken", 2)
        opn = _tt("crypto/kraken", "CASHOPEN 2026-01-01 USD 0 0 at=kraken",
                  1)
        CE.apply_declarations([dep], [ln])
        self.assertTrue(ln.get("matched"))
        doc = _build(lines=[opn, ln], events=[dep])
        return doc, [opn, dep, ln]

    def test_tj0054_declared_move_listed_once(self):
        """TJ-0054: `fx-cash --cash-events` listed a .tt CASHMOVE that
        declared a broker's deposit as a second deposit (four lines for
        two moves). The broker's move is listed once, with its declared
        cost and the line that declared it."""
        doc, evs = self._read()
        text = FX.render(doc, None, "canada", width_=160, cash_events=evs)
        part = text.split("CASH EVENTS READ", 1)[1]
        moves = [ln for ln in part.splitlines() if "CASHMOVE" in ln]
        self.assertEqual(len(moves), 1, part)
        self.assertIn("cost=1,340.00", moves[0])
        self.assertIn("DECLARED BY", part)
        self.assertIn("inputs/crypto/cash.tt:2", moves[0])
        self.assertIn("CASHOPEN", part)

    def test_tj0168_empty_list_says_so(self):
        """TJ-0168: `--cash-events` with no cash event read printed no
        section at all (the same output as without the flag)."""
        doc, _evs = self._read()
        text = FX.render(doc, None, "canada", width_=160, cash_events=[])
        self.assertIn("CASH EVENTS READ\nNone read from the exports or "
                      ".tt lines.", text)
        self.assertNotIn("CASH EVENTS READ",
                         FX.render(doc, None, "canada", width_=160))


# ------------------------------------------------------------ TJ-0022

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
    def test_tj0022_cashopen_lines_open_a_year_after_a_foreign_record(self):
        """TJ-0022: a US year after a Canadian record was refused, and
        the advice ("open the year with CASHOPEN lines") did not clear
        the refusal; a record closed under the default ledger (no pool
        to carry) was refused too. Now a record with no fx_cash_v2 pool
        is no carry, whatever its country, and with CASHOPEN lines the
        other country's pool is a note, not a problem."""
        from taxjson.bin.taxjson_run import _fx_cash_carry
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
            r = _cli(root, "fx-cash", "--ledger", "v2", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            out = json.loads(r.stdout)
            self.assertEqual(out["problems"], [])
            # A Canadian pool: refused without CASHOPEN lines (C04) ...
            rec.write_text(json.dumps({"year": 2025, "country": "canada",
                                       "fx_cash_v2": pool}))
            out = json.loads(_cli(root, "fx-cash", "--ledger", "v2",
                                  "--json").stdout)
            self.assertEqual(out["status"], "not_computed")
            self.assertIn("Canada law", out["problems"][0]["text"])
            # ... and noted when CASHOPEN lines open the year.
            (root / "inputs" / "margin" / "cash.tt").write_text(
                "CASHOPEN 2026-01-01 CAD 100.00 72.00\n")
            r = _cli(root, "fx-cash", "--ledger", "v2", "--json")
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
    @rule("CA-FX-07")
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
    @rule("CA-FX-07")
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
    return dict(CE.move(td, "USD", amount, settle=sd), decl={"how": "own"})


def _opening(book, units, cost):
    return dict(kind="CASHOPEN", book=book, date="2026-01-01",
                currency="USD", units=units, cost=cost, origin="tt")


class TestOwnMoveOrder(unittest.TestCase):
    @rule("CA-FX-07")
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
                        dict(CE.balance(dep_sd, "USD", 0), book="margin/a"),
                        dict(CE.balance(dep_sd, "USD", 100),
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

    @rule("CA-FX-07")
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
            events += [dict(CE.balance("2026-03-05", "USD", v), book=b)
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


# ------------------------------------------------- TJ-0134, TJ-0166

def _views_proj(rows, elections):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "work").mkdir()
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "work" / "m_base.json").write_text(json.dumps(rows))
    (root / "inputs" / "m" / "manifest.json").write_text(
        json.dumps({"elections": elections}))
    cfg = {"settings": {"base_currency": "CAD", "country": "canada"},
           "accounts": {"m": {"type": "taxable"}}}
    return td, root, cfg


def _row(action, d, sym, qty, net=0.0, **kw):
    r = {"action": action, "date": d, "time": "09:30:00", "symbol": sym,
         "quantity": qty, "net_amount": net}
    r.update(kw)
    return r


def _spin(eid, d, parent, child, new, old, received, cur, fmv=0.0, **kw):
    from taxjson.lib.corp_actions import CorporateAction
    return CorporateAction(
        date=d, time="09:30:00", action_type="spinoff",
        source_symbol=parent, source_isin="", target_symbol=child,
        target_isin="", ratio_new=new, ratio_old=old, qty_disposed=0.0,
        qty_received=received, fmv=fmv, currency=cur, target_currency=cur,
        account="m", event_id=eid, target_fmv=fmv, **kw)


class TestSpinoffViews(unittest.TestCase):
    def test_tj0134_broker_value_labelled_in_its_currency(self):
        """TJ-0134: `tjs spinoffs` printed the broker's USD value
        per share as "20.00 CAD/share"."""
        from taxjson.lib import corp_views as CV
        eid = "20240603-parnt-spnco-0001"
        desc = "Spinoff PARNT.US→SPNCO.US (deemed dividend at FMV)"
        rows = [_row("DIVIDEND", "2024-06-03", "SPNCO.US", 0, 681.75,
                     corp_event_id=eid, description=desc),
                _row("BUYSELL", "2024-06-03", "SPNCO.US", 25, 681.75,
                     corp_event_id=eid, description=desc)]
        ev = _spin(eid, "2024-06-03", "PARNT.US", "SPNCO.US", 1, 4, 25,
                   "USD", fmv=500.0)
        td, root, cfg = _views_proj(rows, {eid: {
            "election": "taxable_deemed_dividend", "hints": {}}})
        with td, patch.object(CV, "_current_events",
                              return_value={eid: ev}):
            doc = CV.spinoffs(root, cfg)
        s = doc["spinoffs"][0]
        self.assertEqual((s["fmv_per_share"], s["fmv_currency"]),
                         (20.0, "USD"))
        text = "\n".join(CV.render_spinoffs(doc))
        self.assertIn("20.00 USD/share (the broker's value)", text)
        self.assertNotIn("CAD/share", text)

    @rule("CA-CORP-05")
    def test_tj0166_ratio_and_cash_in_lieu_count_the_fraction(self):
        """TJ-0166: an s.86.1 spin-off of 10.5 new shares for 105 (10
        delivered, 0.5 paid in cash) showed "0.0952381 new per share
        (10 for 105)" while 10.5 are booked. The views give the
        entitlement and the cash in lieu; `elect --pending` says it
        too."""
        from taxjson.lib import corp_views as CV
        eid = "20240614-zzp-zzs-0001"
        desc = "Spinoff ZZP.TO→ZZS.TO (s.86.1 rollover)"
        rows = [_row("BUYSELL", "2024-06-14", "ZZS.TO", 10.5, 210.0,
                     corp_event_id=eid, description=desc,
                     corp_election="rollover_s_86_1"),
                _row("BUYSELL", "2024-06-18", "ZZS.TO", -0.5, 12.0,
                     corp_event_id=eid, description=desc,
                     corp_election="rollover_s_86_1")]
        ev = _spin(eid, "2024-06-14", "ZZP.TO", "ZZS.TO", 10, 105, 10,
                   "CAD", fraction_in_lieu=0.5, cash_in_lieu=12.0,
                   cash_in_lieu_currency="CAD",
                   cash_in_lieu_date="2024-06-18")
        td, root, cfg = _views_proj(rows, {eid: {
            "election": "rollover_s_86_1",
            "hints": {"allocated_acb_cad": 210}}})
        with td, patch.object(CV, "_current_events",
                              return_value={eid: ev}):
            doc = CV.spinoffs(root, cfg)
        s = doc["spinoffs"][0]
        self.assertEqual(s["ratio"], "0.1 new per share (10.5 for 105)")
        self.assertEqual(s["cash_in_lieu"],
                         "0.5 of the 10.5 new shares, paid in cash: 12.00 "
                         "CAD on 2024-06-18 (booked as their sale)")
        self.assertIn("cash in lieu:  0.5 of the 10.5 new shares",
                      "\n".join(CV.render_spinoffs(doc)))
        # An export that pays cash without sizing the fraction (RBC).
        self.assertEqual(CV.cash_in_lieu_line(10, 0, 12, "CAD",
                                              "2024-06-18"),
                         "12.00 CAD on 2024-06-18, for a fraction the "
                         "export does not size")
        self.assertEqual(CV.cash_in_lieu_line(10, 0, 0, "CAD", ""), "")
        # `tjs elect --pending` on the same event, from a Questrade
        # export.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _qt_project(root, "2024-06-14", "2024-06-18")
            r = _cli(root, "elect", "--pending")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("cash in lieu: 0.5 of the 10.5 new shares, paid "
                          "in cash: 12.00 CAD on 2024-06-18", r.stdout)


# ------------------------------------------------- TJ-0101, TJ-0149

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


_ACCT = "99900001"         # a synthetic broker account id


def _q(d, action, sym, desc, qty, net="0", price="0", gross="0",
       act="Dividends"):
    t = f"{d} 12:00:00 AM"
    return ",".join([t, t, action, sym, desc, qty, price, gross, "0", net,
                     "CAD", _ACCT, act, "Individual margin"]) + "\n"


def _project(root: Path, files, year=2024):
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    for name, text in files.items():
        (root / "inputs" / "margin" / name).write_text(text)
    r = _cli(root, "run", "--no-input")
    assert r.returncode in (0, 3), r.stderr[-2000:]


def _elect(root):
    pend = json.loads((root / "work" / "pending_elections.json").read_text())
    eid = pend["accounts"]["margin"]["pending"][0]["event_id"]
    r = _cli(root, "elect", "margin", "--set", f"{eid}=rollover_s_86_1",
             "--hint", "allocated_acb_cad=210")
    assert r.returncode == 0, r.stderr
    return eid


def _qt_project(root, spin_day, cil_day, more=""):
    """A Questrade export: 105 ZZP.TO, a spin-off of 10 ZZS.TO on them
    and 0.5 paid in cash (12.00) on `cil_day`; not yet elected."""
    buy = _q("2024-01-05", "Buy", "ZZP.TO", "ZZP PARENT CORP", "105",
             net="-1050", price="10", gross="-1050", act="Trades")
    m, d = spin_day[5:7], spin_day[8:10]
    dis = _q(spin_day, "DIS", "ZZS.TO",
             f"ZZS SPINCO INC SPINOFF FROM SEC# X123456 ZZP PARENT CORP "
             f"REC {m}/01/24 PAY {m}/{d}/24 ON 105 SHS", "10")
    cil = _q(cil_day, "CIL", "ZZS.TO",
             "ZZS SPINCO INC CASH IN LIEU OF .5 SHS", "0", net="12.00",
             act="Other")
    _project(root, {"qt.csv": _QH + buy + dis + cil + more})


class TestSpinoffFraction(unittest.TestCase):
    @rule("CA-CORP-05")
    def test_tj0101_hand_booked_fraction_is_not_a_missing_basis(self):
        """TJ-0101: the documented .tt fix for an RBC cash in lieu (a
        BUY of the fraction at 0, then its SELL for the cash) cleared
        UNBOOKED but was called a "$0 cost" position whose sale
        overstates the gain, though the pooled s.86.1 cost covers it.
        A $0 BUY of less than one share, sold again by a .tt line, into
        a pool the election gave a cost is not flagged; one sold by the
        broker, or a whole share, still is."""
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.missing_history import \
            detect_zero_basis_acquisitions as zero

        def tx(d, q, net, src, election=""):
            return TaxTransaction(
                action="BUYSELL", date=d, time="10:00:00", symbol="ZZS.TO",
                quantity=q, price=abs(net / q) if net else 0.0,
                net_amount=net, account="margin", date_settle=d,
                currency="CAD", source=src, corp_election=election)
        spin = tx("2024-06-14", 10, 210.0, "", "rollover_s_86_1")
        sale = tx("2024-09-10", -10, 250.0, "rbc_spin.csv")
        self.assertEqual(zero([spin, tx("2024-06-18", 0.5, 0, "f.tt"),
                               tx("2024-06-18", -0.5, 12.0, "f.tt"), sale],
                              2024), [])
        for txs in ([spin, tx("2024-06-18", 0.5, 0, "f.tt"),
                     tx("2024-06-18", -0.5, 12.0, "x.csv"), sale],
                    [spin, tx("2024-06-18", 1, 0, "f.tt"),
                     tx("2024-06-18", -1, 24.0, "f.tt"), sale],
                    [tx("2024-06-14", 10, 210.0, "x.csv"),
                     tx("2024-06-18", 0.5, 0, "f.tt"),
                     tx("2024-06-18", -0.5, 12.0, "f.tt"), sale]):
            self.assertEqual(len(zero(txs, 2024)), 1, txs)
        # The documented fix on an RBC export, end to end.
        head = ('Account Activity Detail Report\n'
                f'"Account Number: {_ACCT}"\n'
                '"Date Range: January 1, 2024 - December 31, 2024"\n\n'
                'Date,Activity,Symbol,Description,Quantity,Price,'
                'Settlement Date,Currency,Value,Amount\n')
        rows = ("01/05/2024,Buy,ZZP,ZZP PARENT CORP,105,10.00,01/09/2024,"
                "CAD,-1050.00,-1050.00\n"
                "06/14/2024,Reorganization,ZZS,DIS - ZZS SPINCO INC SPINOFF "
                "ON 105 SHS FROM SEC# X123456 ZZP PARENT CORP REC 06/01/24 "
                "PAY 06/14/24,10,0,06/14/2024,CAD,0,0\n"
                "06/18/2024,Reorganization,ZZS,CIL - ZZS SPINCO INC CASH IN "
                "LIEU OF FRAC SHARES,0,0,06/18/2024,CAD,12.00,12.00\n"
                "09/10/2024,Sell,ZZS,ZZS SPINCO INC - Sell,10,25.00,"
                "09/11/2024,CAD,250.00,250.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root, {"rbc_spin.csv": head + rows})
            _elect(root)
            (root / "inputs" / "margin" / "fraction.tt").write_text(
                "BUYSELL 2024-06-18 10:00:00 ZZS.TO 0.5 CAD 0 0\n"
                "BUYSELL 2024-06-18 10:00:01 ZZS.TO -0.5 CAD 24 12\n")
            r = _cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("$0 cost", r.stderr)
            r = _cli(root, "find-missing-history")
            self.assertNotIn("$0-COST", r.stdout)

    @rule("CA-CORP-05")
    def test_tj0149_fraction_awaiting_its_cash_is_no_difference(self):
        """TJ-0149: an s.86.1 spin-off on 2024-12-27 whose cash in lieu
        is paid 2025-01-06: the books hold 10.5 at the year end, the
        broker 10, and sanity said QTY_MISMATCH (a missing trade or a
        booking problem) and exited 1. The fraction is named instead;
        any other difference is still one."""
        from taxjson.lib.positions_check import fractions_awaiting_cash
        rows = [{"action": "BUYSELL", "date": "2024-12-27",
                 "symbol": "ZZS.TO", "quantity": 10.5,
                 "corp_event_id": "e1", "corp_election": "rollover_s_86_1"},
                {"action": "BUYSELL", "date": "2025-01-06",
                 "symbol": "ZZS.TO", "quantity": -0.5,
                 "corp_event_id": "e1", "corp_election": "rollover_s_86_1"}]
        want = {"ZZS.TO": [{"quantity": 0.5, "received": "2024-12-27",
                            "paid": "2025-01-06"}]}
        self.assertEqual(fractions_awaiting_cash(rows, "2024-12-31"), want)
        for day in ("2024-12-26", "2025-01-06"):
            self.assertEqual(fractions_awaiting_cash(rows, day), {}, day)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sell = _q("2025-02-10", "Sell", "ZZP.TO", "ZZP PARENT CORP",
                      "-105", net="1200", price="11.43", gross="1200",
                      act="Trades")
            _qt_project(root, "2024-12-27", "2025-01-06", more=sell)
            _elect(root)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            hold = root / "holdings" / "margin_holdings.toml"
            hold.parent.mkdir(exist_ok=True)

            def holdings(zzs):
                hold.write_text(
                    '[meta]\nas_of = "2024-12-31"\n\n[[holding]]\n'
                    'symbol = "ZZP.TO"\nquantity = 105\ncurrency = "CAD"\n'
                    'total_cost = 840.00\n\n[[holding]]\nsymbol = "ZZS.TO"'
                    f'\nquantity = {zzs}\ncurrency = "CAD"\n'
                    'total_cost = 200.00\n')
            holdings(10)
            r = _cli(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("QTY_MISMATCH", r.stdout)
            self.assertIn("ZZS.TO: 0.5 more in the books, a spin-off's "
                          "fraction (s.86.1, received 2024-12-27) sold for "
                          "its cash in lieu on 2025-01-06", r.stdout)
            out = json.loads(_cli(root, "sanity", "--json").stdout)
            grp = [g for g in out["groups"] if g["accounts"] == ["margin"]]
            self.assertTrue(grp, out)
            self.assertEqual(grp[0]["discrepancies"], [])
            self.assertEqual(
                grp[0]["fractions_awaiting_cash_in_lieu"][0]["symbol"],
                "ZZS.TO")
            holdings(9)
            r = _cli(root, "sanity")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("QTY_MISMATCH", r.stdout)


if __name__ == "__main__":
    unittest.main()
