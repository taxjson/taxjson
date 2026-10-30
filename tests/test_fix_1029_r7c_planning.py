"""The 2026-10-29 fix round 7 (wave C), planning: buy-check and
sell-check with a mapped option alias, harvest's cached radar cohorts,
pooled grant-timed premium split per account, a negative grant-timed
premium in the inventory, and the loss-window review's day-30
explanation of a new short (backlog TJ-1000, TJ-1009, TJ-1010,
TJ-1012, TJ-1014). Synthetic data only: made-up tickers (SYN, ALT) in
throwaway folders."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj
from test_fix_1029_planning import _ago, _toml


def _expiry(days=400):
    """An option expiry a year or so ahead, as YYMMDD (a Friday)."""
    d = date.today() + timedelta(days=days)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d.strftime("%y%m%d")


class TestOptionAliasBuyCheck(unittest.TestCase):
    """TJ-1000: ticker.map declares GLOBAL SYN<put>.TO ALT<put>.TO (one
    contract, two spellings). The put was sold at a loss 9 days ago.
    buy-check / sell-check of the broker's SYN spelling dropped the
    loss (its row is booked as ALT) and answered SAFE; the ALT spelling
    gave UNSAFE with the re-entry date."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        ex = _expiry()
        cls.syn = f"SYN{ex}P00001000.TO"
        cls.alt = f"ALT{ex}P00001000.TO"
        (root / "taxjson.toml").write_text(_toml())
        (root / "ticker.map").write_text(f"GLOBAL {cls.syn} {cls.alt}\n")
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 {cls.syn} 1 CAD .2 20 0\n"
            f"BUYSELL {_ago(9)} 10:00:00 {cls.syn} -1 CAD .1 10 0\n")
        tj(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _check(self, cmd, sym):
        r = tj(self.root, cmd, sym, "--json", check=False)
        return r.returncode, json.loads(r.stdout)["results"][0]

    def test_tj1000_buy_check_both_spellings_unsafe(self):
        rc_alt, alt = self._check("buy-check", self.alt)
        self.assertEqual((rc_alt, alt["verdict"]), (1, "UNSAFE"), alt)
        rc, syn = self._check("buy-check", self.syn)
        self.assertEqual((rc, syn["verdict"]), (1, "UNSAFE"), syn)
        self.assertEqual(syn["clears_at"], alt["clears_at"])
        self.assertIsNotNone(syn["last_loss"], syn)
        self.assertEqual(syn["last_loss"]["gain"], -10.0)

    def test_tj1000_sell_check_both_spellings_see_the_loss(self):
        _rc, alt = self._check("sell-check", self.alt)
        _rc2, syn = self._check("sell-check", self.syn)
        self.assertIsNotNone(alt["last_loss"], alt)
        self.assertEqual(syn["last_loss"], alt["last_loss"])
        self.assertEqual(syn["verdict"], alt["verdict"])
        self.assertEqual(syn["clears_at"], alt["clears_at"])


class TestHarvestRadarOfAnEarlierDay(unittest.TestCase):
    """TJ-1009: 10 taxable SYN.TO at a loss of 100; the RRSP bought 5
    shares 39 days ago and 5 more 14 days ago. The saved radar, newer
    than the books but computed 10 days ago (both buys then inside the
    window), says LOCKED with 10 units at risk; today only 5 are.
    harvest took the saved 10 and said nothing was claimable now."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml(sheltered=True))
        for acct, rows in (
                ("margin", f"BUYSELL {_ago(120)} 10:00:00 SYN.TO 10 CAD "
                           f"20 200 0\n"),
                ("rrsp", f"BUYSELL {_ago(39)} 10:00:00 SYN.TO 5 CAD 10 50 0\n"
                         f"BUYSELL {_ago(14)} 10:00:00 SYN.TO 5 CAD 10 50 0\n")):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "m.tt").write_text(rows)
        tj(root, "run", "--no-input")
        old = tj(root, "wash-radar", "--date", _ago(10), "--json")
        cls.old_doc = json.loads(old.stdout)
        sidecars = list((root / "reports").glob("wash_radar_*.json"))
        assert sidecars, "no radar sidecar"
        for f in sidecars:
            f.write_text(json.dumps(cls.old_doc))
        (root / "work" / ".price_cache.json").write_text(json.dumps(
            {"SYN.TO": {"price": 10, "currency": "CAD",
                        "quote_symbol": "SYN.TO",
                        "asof": date.today().isoformat()}}))

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj1009_saved_radar_said_ten(self):
        rows = [r for sec in self.old_doc["sections"] for r in sec["rows"]
                if r["ticker"] == "SYN.TO"]
        self.assertEqual((rows[0]["category"], rows[0]["at_risk_qty"]),
                         ("LOCKED", 10.0))

    def test_tj1009_harvest_counts_todays_units(self):
        r = tj(self.root, "harvest", "--no-ibkr", "--json", check=False)
        doc = json.loads(r.stdout)
        self.assertAlmostEqual(doc["totals"]["harvestable"]["now"], 50.0,
                               places=2, msg=doc["totals"])


def _grant_year():
    """A year whose January 2 is past: the grant-timed writes' year."""
    t = date.today()
    return t.year if t >= date(t.year, 1, 3) else t.year - 1


def _grant_project(root, accounts, rows):
    """A Canadian project under option grant timing, each account
    writing `rows` (a .tt text), run; its option priced in the cache at
    0.10 a unit (10 a contract)."""
    year = _grant_year()
    sym = _grant_symbol()
    text = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            f'source_currencies = []\noption_grant_timing_since = {year}\n')
    for a in accounts:
        text += f'[accounts.{a}]\ntype = "taxable"\n'
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "m.tt").write_text(rows)
    (root / "taxjson.toml").write_text(text)
    tj(root, "run", "--no-input")
    (root / "work" / ".price_cache.json").write_text(json.dumps(
        {sym: {"price": 0.1, "currency": "CAD", "quote_symbol": sym,
               "asof": date.today().isoformat()}}))
    return sym


def _grant_symbol():
    return f"SYN{(date.today().year + 1) % 100:02d}0115P00001000.TO"


def _inventory(root, account):
    for name in (f"{account}_gains_wash.json", f"{account}_gains.json"):
        f = root / "work" / name
        if f.exists():
            return json.loads(f.read_text())["inventory"]
    raise AssertionError(f"no gains file for {account}")


class TestPooledGrantPremiumSplit(unittest.TestCase):
    """TJ-1010: two taxable accounts each write one put for 20 under
    grant timing; the pool holds two contracts, premium 40 recognised.
    Each account's slice kept the pool's whole recognised_premium (40)
    while its cost was halved, so harvest showed a buy-back loss of 30
    a contract (60) where it is 10 (20)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        cls.sym = _grant_project(
            root, ("a", "b"),
            f"BUYSELL {_grant_year()}-01-02 10:00:00 {_grant_symbol()} -1 "
            f"CAD .2 20 0\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj1010_each_slice_carries_its_share(self):
        for a in ("a", "b"):
            inv = [r for r in _inventory(self.root, a)
                   if r["symbol"] == self.sym]
            self.assertEqual(len(inv), 1, inv)
            self.assertAlmostEqual(inv[0]["total_cost"], -20.0, places=4)
            self.assertAlmostEqual(inv[0]["recognised_premium"], 20.0,
                                   places=4, msg=inv[0])

    def test_tj1010_harvest_loss_once(self):
        r = tj(self.root, "harvest", "--options", "--no-ibkr", "--json")
        doc = json.loads(r.stdout)
        self.assertAlmostEqual(doc["totals"]["unrealized"], -20.0,
                               places=2, msg=doc["totals"])


class TestNegativeGrantPremium(unittest.TestCase):
    """TJ-1012: a grant-timed put written for 10 with a commission of 15
    (net debit 5): the write books a loss of 5. The inventory dropped
    the negative recognised premium, so harvest charged the 5 again (a
    buy-back at 10 forecast a loss of 15; the close books 10)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        cls.sym = _grant_project(
            root, ("margin",),
            f"BUYSELL {_grant_year()}-01-02 10:00:00 {_grant_symbol()} -1 "
            f"CAD .1 -5 15\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj1012_inventory_keeps_the_signed_premium(self):
        inv = [r for r in _inventory(self.root, "margin")
               if r["symbol"] == self.sym]
        self.assertEqual(len(inv), 1, inv)
        self.assertAlmostEqual(inv[0].get("recognised_premium", 0.0), -5.0,
                               places=4, msg=inv[0])

    def test_tj1012_harvest_forecasts_the_close(self):
        r = tj(self.root, "harvest", "--options", "--no-ibkr", "--json")
        doc = json.loads(r.stdout)
        self.assertAlmostEqual(doc["totals"]["unrealized"], -10.0,
                               places=2, msg=doc["totals"])


class TestEdgeShortOpenAfterDay30(unittest.TestCase):
    """TJ-1014: the whole SYN.TO position sold at a loss on June 2; flat
    until a new SHORT sale on July 3 (day 31). edge-cases explained that
    sale as "after day 30, so those units were still held when the
    window closed" beside HELD DAY 30 = 0. A control that bought 10 on
    July 1 and sold them July 3 keeps that explanation."""

    _HEAD = ("BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 20 -200 0\n"
             "BUYSELL 2025-06-02 10:00:00 SYN.TO -10 CAD 10 100 0\n")
    _TAIL = "BUYSELL 2025-07-03 10:00:00 SYN.TO -10 CAD 10 100 0\n"

    def _edges(self, extra):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "taxjson.toml").write_text(_toml(year=2025))
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "m.tt").write_text(
                self._HEAD + extra + self._TAIL)
            tj(root, "run", "--no-input")
            r = tj(root, "edge-cases", "--json")
            text = tj(root, "edge-cases", "--details").stdout
            return json.loads(r.stdout)["window_edges"], text

    def test_tj1014_short_open_is_not_a_day30_holding(self):
        we, text = self._edges("")
        self.assertEqual(len(we), 1, we)
        self.assertEqual(we[0]["held_at_day30"], 0.0)
        items = [i for i in we[0]["items"] if i["date"] == "2025-07-03"]
        self.assertEqual(len(items), 1, items)
        it = items[0]
        self.assertEqual(it["kind"], "short sale", it)
        self.assertNotIn("still held", it["why"])
        self.assertIn("opens a short", it["why"])
        self.assertNotIn("still held when the window closed", text)

    def test_tj1014_control_sale_of_replacements_keeps_its_why(self):
        we, _text = self._edges(
            "BUYSELL 2025-07-01 10:00:00 SYN.TO 10 CAD 10 -100 0\n")
        self.assertEqual(we[0]["held_at_day30"], 10.0)
        sale = [i for i in we[0]["items"] if i["date"] == "2025-07-03"]
        self.assertEqual([i["kind"] for i in sale], ["sale"], sale)
        self.assertEqual(sale[0]["qty"], -10.0)
        self.assertIn("after day 30, so those units were still held",
                      sale[0]["why"])


if __name__ == "__main__":
    unittest.main()
