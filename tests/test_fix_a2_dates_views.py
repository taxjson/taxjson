"""Re-audit-2 fixes: edge-cases, check-dates, the settlement cycle table,
`list --date`, the list/shares horizon and the run's expired-option
warning (fix lists dates-views-01 / dates-views-02). Synthetic books
only."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, cli_both, projects_both

from taxjson.lib import check_dates as CD
from taxjson.lib.dates import settlement_date, settlement_lag_days
from taxjson.lib.edge_cases import analyze, render_text

SRC = Path(__file__).resolve().parents[1] / "src"


def _tx(acct, action, d, settle, sym, qty, net=0.0, price=None, **kw):
    r = {"account": acct, "action": action, "date": d, "date_settle": settle,
         "time": kw.pop("time", "10:00:00"), "symbol": sym,
         "quantity": qty, "net_amount": net,
         "price": abs(net / qty) if price is None and qty else (price or 0.0),
         "currency": kw.pop("currency", "CAD"),
         "id": f"{sym}-{d}-{qty}-{action}"}
    r.update(kw)
    return r


def _gain(acct, d, settle, sym, qty, raw, denied=0.0):
    return {"account": acct, "date": d, "date_settle": settle, "symbol": sym,
            "qty": qty, "gain": raw + denied, "raw_gain": raw,
            "disallowed_amount": denied, "permanently_disallowed": 0.0,
            "proceeds": 1000.0, "cost": 1000.0 - raw, "id": f"g-{sym}-{d}"}


class _Proj:
    """A project folder with work/ books written by the test."""

    def __init__(self, country="canada", year=2025, tax_date=None,
                 accounts=None, **settings):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "work").mkdir()
        s = {"year": year, "country": country}
        if tax_date:
            s["tax_date"] = tax_date
        s.update(settings)
        self.cfg = {"settings": s,
                    "accounts": accounts or {"margin": {"type": "taxable"}}}

    def base(self, acct, rows):
        (self.root / "work" / f"{acct}_base.json").write_text(
            json.dumps({"transactions": rows}))

    def gains(self, acct, rows, inventory=()):
        (self.root / "work" / f"{acct}_gains_wash.json").write_text(
            json.dumps({"transactions": rows, "inventory": list(inventory)}))

    def analyze(self, **kw):
        return analyze(self.root, self.cfg, **kw)

    def close(self):
        self.tmp.cleanup()


# ------------------------------------------------- edge-cases: window basis

class TestEdgeCasesWindowBasis(unittest.TestCase):
    """A2-0133, A2-0134, A2-0135, A2-1206: the window is counted on the
    engine's fixed dates — settle in Canada, trade in the US — whatever
    tax_date says."""

    def _book(self, country, tax_date):
        p = _Proj(country, 2025, tax_date)
        p.base("margin", [
            _tx("margin", "BUYSELL", "2025-01-02", "2025-01-03", "XYZ.TO",
                100, 1000.0),
            # Loss traded Fri Feb 28, settles Mon Mar 3.
            _tx("margin", "BUYSELL", "2025-02-28", "2025-03-03", "XYZ.TO",
                -100, 800.0),
            # Rebuy traded Mar 31 (day 31 on trade dates), settles Apr 1
            # (day 29 on settle dates).
            _tx("margin", "BUYSELL", "2025-03-31", "2025-04-01", "XYZ.TO",
                100, 800.0)])
        p.gains("margin", [_gain("margin", "2025-02-28", "2025-03-03",
                                 "XYZ.TO", 100, -200.0,
                                 denied=200.0 if country == "canada" else 0)])
        return p

    def _item(self, doc):
        we = doc["window_edges"]
        self.assertEqual(len(we), 1, we)
        acq = [i for i in we[0]["items"] if i["kind"] == "acquisition"]
        self.assertEqual(len(acq), 1)
        return we[0], acq[0]

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_window_days_ignore_tax_date(self):
        for country, tax_date, day, inside in (
                ("canada", "trade", 29, True), ("canada", "settle", 29, True),
                ("usa", "settle", 31, False), ("usa", "trade", 31, False)):
            p = self._book(country, tax_date)
            try:
                doc = p.analyze()
                loss, it = self._item(doc)
                self.assertEqual((it["day"], it["inside"]), (day, inside),
                                 (country, tax_date, it))
                text = "\n".join(render_text(doc))
                self.assertNotIn("DECIDES", text)
                self.assertIn("SETTLEMENT dates" if country == "canada"
                              else "TRADE dates", text)
                if country == "canada":
                    # Held on day 30 counts the rebuy (window on settle).
                    self.assertEqual(loss["held_at_day30"], 100.0)
                    self.assertIn("INSIDE", it["why"])
                else:
                    self.assertIn("OUTSIDE", it["why"])
            finally:
                p.close()

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_year_crossing_window_on_engine_dates(self):
        # Canada, tax_date=trade: loss settles 12-08, rebuy settles 01-07
        # (day 30 on settle dates): the window spans Dec 31 (A2-1206).
        p = _Proj("canada", 2025, "trade")
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-06-02", "2025-06-03",
                    "XYZ.TO", 100, 2000.0),
                _tx("margin", "BUYSELL", "2025-12-05", "2025-12-08",
                    "XYZ.TO", -100, 1000.0),
                _tx("margin", "BUYSELL", "2026-01-06", "2026-01-07",
                    "XYZ.TO", 100, 1000.0)])
            p.gains("margin", [_gain("margin", "2025-12-05", "2025-12-08",
                                     "XYZ.TO", 100, -1000.0, 1000.0)])
            lw = p.analyze()["year_boundary"]["loss_windows"]
            self.assertEqual([r["symbol"] for r in lw], ["XYZ.TO"])
        finally:
            p.close()
        # US, tax_date=settle: a 2026-01-02 loss washed by a 2025-12-03 buy
        # (day 30 on trade dates).
        p = _Proj("usa", 2026, "settle")
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-06-02", "2025-06-03",
                    "XYZ.US", 100, 2000.0, currency="USD"),
                _tx("margin", "BUYSELL", "2025-12-03", "2025-12-04",
                    "XYZ.US", 100, 1000.0, currency="USD"),
                _tx("margin", "BUYSELL", "2026-01-02", "2026-01-05",
                    "XYZ.US", -100, 1000.0, currency="USD")])
            p.gains("margin", [_gain("margin", "2026-01-02", "2026-01-05",
                                     "XYZ.US", 100, -1000.0, 1000.0)])
            lw = p.analyze()["year_boundary"]["loss_windows"]
            self.assertEqual([r["symbol"] for r in lw], ["XYZ.US"])
        finally:
            p.close()


# ------------------------------------------------- edge-cases: positions

class TestEdgeCasesPositions(unittest.TestCase):

    @rule("CA-RPT-07")
    def test_held_at_applies_split(self):
        # A2-0388: 100 bought, 2-for-1, loss sale of 100, rebuy of 100.
        p = _Proj("canada", 2025)
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-01-02", "2025-01-03",
                    "ABC.TO", 100, 1000.0),
                _tx("margin", "SPLIT", "2025-02-03", "2025-02-03",
                    "ABC.TO", 2),
                _tx("margin", "BUYSELL", "2025-03-03", "2025-03-04",
                    "ABC.TO", -100, 400.0),
                _tx("margin", "BUYSELL", "2025-04-02", "2025-04-03",
                    "ABC.TO", 100, 400.0)])
            p.gains("margin", [_gain("margin", "2025-03-03", "2025-03-04",
                                     "ABC.TO", 100, -100.0, 100.0)])
            we = p.analyze()["window_edges"]
            self.assertEqual(we[0]["held_at_day30"], 200.0)
        finally:
            p.close()

    @rule("CA-RPT-07")
    def test_opening_balance_is_a_position(self):
        # A2-0389: an OPENING_BALANCE opener is held like a bought one.
        p = _Proj("canada", 2025)
        try:
            p.base("margin", [
                _tx("margin", "OPENING_BALANCE", "2025-01-02", "2025-01-02",
                    "XYZ.TO", 200, 2000.0),
                _tx("margin", "BUYSELL", "2025-02-03", "2025-02-04",
                    "XYZ.TO", 100, 1000.0),
                _tx("margin", "BUYSELL", "2025-03-05", "2025-03-06",
                    "XYZ.TO", -100, 800.0),
                _tx("margin", "BUYSELL", "2025-12-31", "2026-01-02",
                    "XYZ.TO", -200, 2500.0)])
            p.gains("margin", [
                _gain("margin", "2025-03-05", "2025-03-06", "XYZ.TO", 100,
                      -200.0, 200.0),
                _gain("margin", "2025-12-31", "2026-01-02", "XYZ.TO", 200,
                      500.0)])
            doc = p.analyze()
            st = [r for r in doc["year_boundary"]["straddles"]
                  if r["symbol"] == "XYZ.TO"]
            self.assertIn("2026 sale: a disposition", st[0]["why"])
            we = doc["window_edges"][0]
            self.assertEqual(we["held_at_day30"], 200.0)
            for it in we["items"]:
                self.assertNotIn("not a replacement", it["why"])
        finally:
            p.close()

    @rule("CA-RPT-07")
    def test_phantoms_json_opening(self):
        # A2-1208: a phantom-backed year-end sale is a disposition.
        p = _Proj("canada", 2025)
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-12-31", "2026-01-02",
                    "XYZ.TO", -100, 1500.0)])
            p.gains("margin", [_gain("margin", "2025-12-31", "2026-01-02",
                                     "XYZ.TO", 100, 500.0)])
            (p.root / "phantoms.json").write_text(json.dumps(
                [{"symbol": "XYZ.TO", "account": "margin"}]))
            st = p.analyze()["year_boundary"]["straddles"]
            self.assertIn("2026 sale: a disposition", st[0]["why"])
            self.assertNotIn("short sale", st[0]["why"])
        finally:
            p.close()


# ------------------------------------------------- edge-cases: calls

class TestEdgeCasesBuyToClose(unittest.TestCase):
    """A2-0699, A2-1197: a buy-to-close of a written call is not a long
    call (the engine ignores it: core._opening_qty)."""

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_buy_back_not_listed(self):
        call_w = "ABC260116C00060000.US"
        call_l = "ABC260320C00045000.US"
        for country in ("canada", "usa"):
            p = _Proj(country, 2025)
            try:
                p.base("margin", [
                    _tx("margin", "BUYSELL", "2025-01-02", "2025-01-03",
                        "ABC.US", 100, 5000.0, currency="USD"),
                    _tx("margin", "BUYSELL", "2025-11-14", "2025-11-17",
                        call_w, -1, 300.0, currency="USD"),
                    _tx("margin", "BUYSELL", "2025-12-15", "2025-12-16",
                        "ABC.US", -100, 4000.0, currency="USD"),
                    _tx("margin", "BUYSELL", "2025-12-22", "2025-12-23",
                        call_l, 1, 200.0, currency="USD"),
                    _tx("margin", "BUYSELL", "2026-01-09", "2026-01-12",
                        call_w, 1, 100.0, currency="USD")])
                p.gains("margin", [_gain("margin", "2025-12-15",
                                         "2025-12-16", "ABC.US", 100,
                                         -1000.0)])
                doc = p.analyze(margin=10)
                listed = {it["option"] for r in doc["calls_in_windows"]
                          for it in r["items"]}
                self.assertEqual(listed, {call_l}, country)
                for r in doc["window_edges"]:
                    for it in r["items"]:
                        self.assertNotEqual(it.get("option"), call_w)
            finally:
                p.close()

    @rule("CA-RPT-07")
    def test_window_edges_loop_skips_buy_back(self):
        p = _Proj("canada", 2025)
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-01-02", "2025-01-03",
                    "ABC.TO", 100, 5000.0),
                _tx("margin", "BUYSELL", "2025-02-03", "2025-02-04",
                    "ABC250620C00050000.TO", -1, 200.0),
                _tx("margin", "BUYSELL", "2025-05-05", "2025-05-06",
                    "ABC250620C00050000.TO", 1, 50.0),
                _tx("margin", "BUYSELL", "2025-06-02", "2025-06-03",
                    "ABC.TO", -100, 4000.0)])
            p.gains("margin", [_gain("margin", "2025-06-02", "2025-06-03",
                                     "ABC.TO", 100, -1000.0)])
            doc = p.analyze()
            kinds = [it["kind"] for r in doc["window_edges"]
                     for it in r["items"]]
            self.assertNotIn("long call", kinds)
        finally:
            p.close()


# ------------------------------------------------- edge-cases: expiries

class TestEdgeCasesExpiry(unittest.TestCase):

    @rule("CA-RPT-07")
    def test_assigned_on_expiry_is_not_an_expiry(self):
        # A2-0700: written call assigned on its Dec 31 expiry; the stock
        # leg settles Jan 2 — the premium folds into a 2026 sale.
        opt = "QZW251231C00050000.US"
        p = _Proj("canada", 2025)
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-06-02", "2025-06-03",
                    "QZW.US", 100, 4000.0, currency="USD"),
                _tx("margin", "BUYSELL", "2025-11-03", "2025-11-04", opt,
                    -1, 199.0, currency="USD"),
                _tx("margin", "ASSIGN", "2025-12-31", "2025-12-31", opt, 1,
                    0.0, currency="USD"),
                _tx("margin", "ASSIGN", "2025-12-31", "2026-01-02",
                    "QZW.US", -100, 5000.0, currency="USD"),
                # A plain expiry next to it still reads as an expiry.
                _tx("margin", "BUYSELL", "2025-11-03", "2025-11-04",
                    "QZW251231P00030000.US", -1, 50.0, currency="USD"),
                _tx("margin", "BUYSELL", "2025-12-31", "2025-12-31",
                    "QZW251231P00030000.US", 1, 0.0, price=0.0,
                    currency="USD")])
            p.gains("margin", [])
            ex = {r["symbol"]: r for r in
                  p.analyze()["year_boundary"]["option_expiries"]}
            self.assertTrue(ex[opt]["assigned"])
            self.assertEqual(ex[opt]["lands_in"], 2026)
            self.assertIn("not an expiry", ex[opt]["why"])
            self.assertNotIn("already a gain", ex[opt]["why"])
            put = ex["QZW251231P00030000.US"]
            self.assertEqual(put["lands_in"], 2025)
            self.assertIn("an expiry is a disposition", put["why"])
        finally:
            p.close()


# ------------------------------------------------- edge-cases: income

class TestEdgeCasesIncome(unittest.TestCase):
    """A2-0387, A2-1207, A2-1209: income lands in the year income_dating
    gives it; payments in lieu and trust ROC are listed."""

    @rule("CA-RPT-07", "CA-INC-DATE-TRUST", "CA-INC-DATE-ROC-TRUST",
          "CA-INC-DATE-PIL")
    def test_trust_record_date_pil_and_roc(self):
        p = _Proj("canada", 2025)
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-02-03", "2025-02-04",
                    "XIU.TO", 100, 3000.0),
                _tx("margin", "DIVIDEND", "2026-01-05", "2026-01-05",
                    "XIU.TO", 0, 50.0, income_label="distribution",
                    record_date="2025-12-30",
                    description="XIU DIST ON 100 SHS REC 12/30/25 PAY 01/05/26"),
                _tx("margin", "DIVIDEND_IN_LIEU", "2025-12-30", "2025-12-30",
                    "TD.TO", 0, 40.0),
                _tx("margin", "DIVIDEND", "2025-12-30", "2025-12-30",
                    "BNS.TO", 0, 30.0),
                _tx("margin", "ADJUST", "2026-01-02", "2026-01-02",
                    "REI.UN.TO", 0, -20.0, type="roc",
                    record_date="2025-12-30")])
            p.gains("margin", [])
            inc = {(r["symbol"], r["action"]): r for r in
                   p.analyze()["year_boundary"]["income"]}
            xiu = inc[("XIU.TO", "DIVIDEND")]
            self.assertEqual(xiu["lands_in"], 2025)
            self.assertIn("s.104(13)", xiu["why"])
            self.assertNotIn("PAID", xiu["why"])
            self.assertEqual(inc[("TD.TO", "DIVIDEND_IN_LIEU")]["lands_in"],
                             2025)
            self.assertEqual(inc[("BNS.TO", "DIVIDEND")]["lands_in"], 2025)
            roc = inc[("REI.UN.TO", "ROC")]
            self.assertEqual(roc["lands_in"], 2025)
            self.assertIn("s.53(2)(h)", roc["why"])
        finally:
            p.close()


# ------------------------------------------------- edge-cases: crypto

class TestEdgeCasesCrypto(unittest.TestCase):

    def _crypto(self, country="canada", tz="America/Vancouver"):
        p = _Proj(country, 2025, accounts={
            "kr": {"type": "taxable", "crypto": True}},
            **({"local_timezone": tz} if tz else {}))
        return p

    @rule("CA-RPT-07")
    def test_midnight_uses_local_timezone(self):
        # A2-1200, A2-1202, A2-1203, A2-1204.
        p = self._crypto()
        try:
            p.base("kr", [
                _tx("kr", "BUYSELL", "2025-12-31", "2025-12-31", "BTC", -0.1,
                    9000.0, time="17:30:00"),
                _tx("kr", "BUYSELL", "2025-12-31", "2025-12-31", "ETH", -1,
                    4000.0, time="21:30:00"),
                _tx("kr", "BUYSELL", "2025-12-31", "2025-12-31", "SOL", -1,
                    200.0, time="14:00:00")])
            p.gains("kr", [])
            cm = {r["symbol"]: r for r in
                  p.analyze()["year_boundary"]["crypto_midnight"]}
            self.assertEqual(set(cm), {"BTC", "ETH"})
            self.assertEqual(cm["BTC"]["utc"], "2026-01-01 01:30:00")
            self.assertEqual(cm["ETH"]["utc"], "2026-01-01 05:30:00")
            self.assertNotIn("EST", cm["ETH"]["why"])
        finally:
            p.close()

    @rule("CA-RPT-07")
    @rule("US-RPT-05", "US-WASH-13")
    def test_us_crypto_has_no_wash_window(self):
        # A2-1201: §1091 does not reach crypto; Canada's s.54 does.
        out = {}
        for country in ("canada", "usa"):
            p = self._crypto(country, tz=None)
            try:
                p.base("kr", [
                    _tx("kr", "BUYSELL", "2025-01-02", "2025-01-02", "BTC",
                        1, 90000.0),
                    _tx("kr", "BUYSELL", "2025-06-02", "2025-06-02", "BTC",
                        -1, 70000.0),
                    _tx("kr", "BUYSELL", "2025-07-02", "2025-07-02", "BTC",
                        1, 70000.0)])
                p.gains("kr", [_gain("kr", "2025-06-02", "2025-06-02", "BTC",
                                     1, -20000.0)])
                out[country] = p.analyze()["window_edges"]
            finally:
                p.close()
        self.assertEqual(len(out["canada"]), 1)
        self.assertEqual(out["usa"], [])


# ------------------------------------------------- edge-cases: locks, errors

def _write_lock(path, year, timing):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"year": year, "country": "canada",
                                "option_timing": timing}))


class TestEdgeCasesLocks(unittest.TestCase):
    """A2-0390, A2-1205: the written-option section reads the lock's
    recorded timing and the prior_year_record lock, as option-boundary."""

    def _book(self, p):
        opt = "ABC260116C00050000.TO"
        p.base("margin", [
            _tx("margin", "BUYSELL", "2025-11-03", "2025-11-04", opt, -1,
                399.0),
            _tx("margin", "BUYSELL", "2026-01-09", "2026-01-12", opt, 1,
                101.0)])
        p.gains("margin", [])

    @rule("CA-RPT-07", "CA-OPT-01")
    def test_lock_timing_and_prior_year_record(self):
        for where in ("local", "prior"):
            p = _Proj("canada", 2026, option_grant_timing_since=2025)
            try:
                self._book(p)
                if where == "local":
                    _write_lock(p.root / "filed" / "2025.json", 2025,
                                {"option_premium_timing": "close"})
                else:
                    _write_lock(p.root / "p2025" / "filed" / "2025.json",
                                2025, {"option_premium_timing": "close"})
                    p.cfg["settings"]["prior_year_record"] = \
                        "p2025/filed/2025.json"
                wo = p.analyze()["year_boundary"]["written_options"]
                self.assertTrue(wo, where)
                self.assertTrue(any(r["attention"] for r in wo), (where, wo))
                self.assertIn("records CLOSE timing", wo[0]["action"])
            finally:
                p.close()


class TestEdgeCasesErrors(unittest.TestCase):

    def test_unreadable_work_file_is_named(self):
        # A2-1199.
        for which in ("margin_gains_wash.json", "margin_base.json"):
            p = _Proj("canada", 2025)
            try:
                p.base("margin", [_tx("margin", "BUYSELL", "2025-01-02",
                                      "2025-01-03", "XYZ.TO", 1, 10.0)])
                p.gains("margin", [])
                f = p.root / "work" / which
                f.write_text(f.read_text()[:20])
                with self.assertRaises(ValueError) as cm:
                    p.analyze()
                self.assertIn(which, str(cm.exception))
            finally:
                p.close()

    @rule("CA-RPT-07")
    def test_futures_settle_typo_refused(self):
        # A2-0697: the validator run and tax-logic use.
        p = _Proj("canada", 2025, futures_settle="nextday")
        try:
            p.base("margin", [])
            p.gains("margin", [])
            with self.assertRaises(ValueError) as cm:
                p.analyze()
            self.assertIn("futures_settle", str(cm.exception))
            with self.assertRaises(ValueError):
                CD.analyze(p.root, p.cfg)
        finally:
            p.close()

    def test_negative_margin_and_unreadable_cli(self):
        # A2-1198 (and the CLI side of A2-1199).
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2025, files={
                "work/margin_base.json": json.dumps({"transactions": []}),
                "work/margin_gains_wash.json": '{"transactions": [',
            })["canada"]
            r = cli(p, "edge-cases", "--margin", "-5")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--margin must be >= 0", r.stderr)
            r = cli(p, "edge-cases")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("margin_gains_wash.json", r.stderr)


# ------------------------------------------------- check-dates

def _cd_project(rows_by_file, settings=None):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    work = root / "work"
    work.mkdir()
    lines = []
    for (kind, name), rows in rows_by_file.items():
        if kind == "tt":
            lines.append(f"tt/{name}")
            f = work / f"m_tt_{Path(name).stem}.json"
        else:
            lines.append(f"{kind}/{name}")
            f = work / f"m_{kind}.json"
        f.write_text(rows if isinstance(rows, str) else json.dumps(rows))
    (work / "m_sources.list").write_text("\n".join(lines) + "\n")
    cfg = {"settings": dict({"year": 2025}, **(settings or {})),
           "accounts": {"m": {"type": "taxable"}}}
    return td, root, cfg


def _r(d, settle, sym="ABC.US", t="10:00:00", price=10.0, action="BUYSELL",
       cur="USD"):
    return {"action": action, "date": d, "date_settle": settle, "time": t,
            "symbol": sym, "quantity": 1, "price": price, "currency": cur}


class TestCheckDates(unittest.TestCase):
    def codes(self, doc):
        return sorted(i["code"] for i in doc["issues"])

    def test_unreadable_file_is_an_error(self):
        # A2-0385.
        td, root, cfg = _cd_project({("ib", "a.csv"): '{"transactions": ['})
        with td:
            doc = CD.analyze(root, cfg, today=date(2026, 1, 1))
        self.assertEqual(self.codes(doc), ["unreadable"])
        self.assertEqual(doc["errors"], 1)
        self.assertIn("m_ib.json", doc["issues"][0]["message"])

    def test_expiry_settling_after_expiry_warns(self):
        # A2-0386: generic EXPIRED row dated Dec 31, settling Jan 2.
        td, root, cfg = _cd_project({("generic", "g.csv"): [
            _r("2025-12-31", "2026-01-02", sym="XYZ251231C00050000.US",
               price=0.0)]})
        with td:
            doc = CD.analyze(root, cfg, today=date(2026, 3, 1))
        self.assertEqual(self.codes(doc), ["settle-after-expiry"])
        self.assertIn("2026", doc["issues"][0]["detail"])

    def test_one_error_for_a_far_future_row(self):
        # A2-1192.
        td, root, cfg = _cd_project({("ib", "a.csv"): [
            _r("2207-06-01", "2207-06-02")]}, {"year": 2026})
        with td:
            doc = CD.analyze(root, cfg, today=date(2026, 10, 1))
        self.assertEqual(doc["errors"], 1)

    def test_tt_settle_date_of_todays_trade_is_not_future(self):
        # A2-1193: a .tt line carries the settlement date.
        td, root, cfg = _cd_project({("tt", "m.tt"): [
            _r("2026-10-02", "2026-10-02", sym="XYZ.TO", cur="CAD"),
            _r("2026-10-09", "2026-10-09", sym="XYZ.TO", cur="CAD")]},
            {"year": 2026})
        with td:
            doc = CD.analyze(root, cfg, today=date(2026, 10, 1))
        fut = [i["date"] for i in doc["issues"] if i["code"] == "future-date"]
        self.assertEqual(fut, ["2026-10-09"])

    def test_slash_future_is_a_future(self):
        # A2-1194.
        self.assertEqual(CD.asset_class("/ESH5.US", False), "futures")
        self.assertEqual(CD.asset_class("F:ESH5", False), "futures")
        td, root, cfg = _cd_project({("generic", "g.csv"): [
            _r("2025-02-17", "2025-02-17", sym="/ESH5.US")]})
        with td:
            doc = CD.analyze(root, cfg, today=date(2026, 1, 1))
        self.assertNotIn("holiday-trade", self.codes(doc))

    def test_globex_holiday_evening_and_sunday_settle(self):
        # A2-1191.
        def chk(d, t):
            r = CD.check_trade_time("futures", date.fromisoformat(d),
                                    time.fromisoformat(t), "F:ESH6", "USD")
            return r[1] if r else None
        self.assertIsNone(chk("2025-12-25", "19:00:00"))
        self.assertIsNone(chk("2026-01-01", "19:00:00"))
        self.assertEqual(chk("2025-12-25", "10:00:00"), "futures-holiday")
        td, root, cfg = _cd_project({("ib", "a.csv"): [
            _r("2026-07-05", "2026-07-05", sym="F:ESU6", t="19:00:00"),
            _r("2026-07-03", "2026-07-04", sym="F:ESU6")]}, {"year": 2026})
        with td:
            doc = CD.analyze(root, cfg, today=date(2026, 10, 1))
        self.assertEqual(self.codes(doc), ["futures-settle",
                                           "futures-sunday-date",
                                           "settle-weekend"])


# ------------------------------------------------- settlement cycles

class TestSettlementCycles(unittest.TestCase):
    """A2-0704, A2-1195, A2-0705: each market's T+3 -> T+2 -> T+1 dates."""

    @rule("CA-DATE-04")
    @rule("US-DATE-04")
    def test_market_eras(self):
        lag = settlement_lag_days
        self.assertEqual(lag("2016-12-28", "USD"), 3)
        self.assertEqual(lag("2016-12-28", "CAD"), 3)
        for cur in ("GBP", "EUR", "CHF", "SEK"):
            self.assertEqual(lag("2016-12-28", cur), 2, cur)
            self.assertEqual(lag("2014-10-03", cur), 3, cur)
        self.assertEqual(lag("2016-12-28", "AUD"), 2)
        self.assertEqual(lag("2016-03-04", "AUD"), 3)
        self.assertEqual(lag("2016-12-28", "HKD"), 2)
        self.assertEqual(lag("2018-12-26", "JPY"), 3)
        self.assertEqual(lag("2019-07-16", "JPY"), 2)
        self.assertEqual(lag("2018-06-01", "SGD"), 3)
        self.assertEqual(lag("2025-03-04", "MXN"), 1)
        for cur in ("EUR", "GBP", "CHF", "SEK", "DKK", "PLN"):
            self.assertEqual(lag("2027-11-02", cur), 1, cur)
        self.assertEqual(lag("2027-11-02", "NOK"), 2)
        self.assertEqual(lag("2027-11-02", "AUD"), 2)
        # A late-December 2016 LSE / ASX sale settles in 2016.
        self.assertEqual(settlement_date("2016-12-28", "GBP"), "2016-12-30")
        self.assertEqual(settlement_date("2016-12-28", "AUD"), "2016-12-30")
        # Tokyo's T+3 era: a Dec 27 2018 sale settles in 2019.
        self.assertEqual(settlement_date("2018-12-27", "JPY"), "2019-01-01")

    def test_tax_logic_text_states_the_eras(self):
        from taxjson.lib.tax_logic import catalog
        cat = catalog()
        for rid in ("CA-DATE-04", "US-DATE-04"):
            t = cat[rid].text
            for word in ("2014-10-06", "2016-03-07", "2019-07-16",
                         "Hong Kong", "MXN"):
                self.assertIn(word, t, (rid, word))


# ------------------------------------------------- Kraken fiat-only ledger

class TestKrakenFiatOnlyLedger(unittest.TestCase):
    """A2-0703: a ledger of only fiat cash moves books nothing correctly —
    not the 'parsed to 0 transactions' warning `run --strict` refuses."""

    def test_no_zero_transaction_warning(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "kr_ledgers_2025.csv"
            f.write_text(
                "txid,refid,time,type,subtype,aclass,asset,wallet,amount,"
                "fee,balance\n"
                "LA1AAA,RA1,2025-01-05 12:00:00,deposit,,currency,ZCAD,spot,"
                "100,0,100\n"
                "LB1BBB,RB1,2025-03-04 12:00:00,withdrawal,,currency,ZCAD,"
                "spot,-50,0,50\n")
            env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                       TAXJSON_LOCAL_TZ="America/Toronto", HOME=td)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "kraken", "--country", "canada", str(f)],
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("parsed to 0 transactions", r.stderr)
            self.assertIn("0 tax objects", r.stderr)


# ------------------------------------------------- list --date, shares

def _bt(d, settle, sym, qty, net, cur):
    return {"action": "BUYSELL", "date": d, "date_settle": settle,
            "time": "10:00:00", "symbol": sym, "quantity": qty,
            "price": abs(net / qty), "net_amount": net, "commission": 0.0,
            "currency": cur, "id": f"{sym}-{d}-{qty}"}


class TestListAsOf(unittest.TestCase):
    """A2-0391, A2-0392, A2-0701: `list --date` keeps the in-account
    superficial-loss / wash-sale addition to the replacement's cost."""

    @rule("CA-SL-09")
    @rule("US-WASH-09")
    def test_as_of_keeps_in_account_deferral(self):
        rows = {"canada": [_bt("2025-01-02", "2025-01-03", "XYZ.TO", 100,
                               10000.0, "CAD"),
                           _bt("2025-02-03", "2025-02-04", "XYZ.TO", -100,
                               7000.0, "CAD"),
                           _bt("2025-02-10", "2025-02-11", "XYZ.TO", 100,
                               7000.0, "CAD")],
                "usa": [_bt("2025-01-02", "2025-01-03", "XYZ.US", 100,
                            10000.0, "USD"),
                        _bt("2025-02-03", "2025-02-04", "XYZ.US", -100,
                            7000.0, "USD"),
                        _bt("2025-02-10", "2025-02-11", "XYZ.US", 100,
                            7000.0, "USD")]}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2025)
            for c, root in p.items():
                (root / "work").mkdir(exist_ok=True)
                (root / "work" / "margin_base.json").write_text(
                    json.dumps({"transactions": rows[c]}))
            r = cli_both(p, "list", "--date", "2025-12-31", "--json")
            for c in p:
                self.assertEqual(r[c].returncode, 0, r[c].stderr)
                doc = json.loads(r[c].stdout)
                row = doc["rows"][0]
                self.assertAlmostEqual(row["cost"], 10000.0, places=2,
                                       msg=c)
                self.assertAlmostEqual(row["deferred_wash"], 3000.0,
                                       places=2, msg=c)


class TestListAsOfOwnerCalls(unittest.TestCase):
    """OWNER-LIST-ASOF-WASH: on the owner's 2025 books `list --date
    2025-12-31` gave two long-call lines (T ... calls) a cost below the
    engine's by the in-account superficial-loss addition (14,817.50 vs
    15,305.50). The same shape, synthetic: a long call sold at a loss
    and bought back within 30 days."""

    @rule("CA-SL-09")
    def test_long_call_rebuy_keeps_the_denied_loss(self):
        sym = "ZZT270115C00022000.TO"
        rows = [_bt("2025-03-03", "2025-03-04", sym, 2, 1000.0, "CAD"),
                _bt("2025-04-01", "2025-04-02", sym, -2, 600.0, "CAD"),
                _bt("2025-04-10", "2025-04-11", sym, 2, 500.0, "CAD")]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, year=2025)["canada"]
            (root / "work").mkdir(exist_ok=True)
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": rows}))
            r = cli(root, "list", "--date", "2025-12-31", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            row = [x for x in json.loads(r.stdout)["rows"]
                   if x["symbol"] == sym][0]
        self.assertAlmostEqual(row["cost"], 900.0, places=2)
        self.assertAlmostEqual(row["deferred_wash"], 400.0, places=2)


class TestBooksHorizon(unittest.TestCase):

    def _project(self, td, base_rows, extra=None):
        p = projects_both(td, year=2025)["canada"]
        (p / "work").mkdir(exist_ok=True)
        (p / "work" / "margin_base.json").write_text(
            json.dumps({"transactions": base_rows}))
        (p / "work" / "margin_gains.json").write_text(json.dumps(
            {"transactions": [], "inventory": [], "summary": {}}))
        for k, v in (extra or {}).items():
            (p / k).write_text(v)
        return p

    def test_settle_basis_horizon(self):
        # A2-0698: a settle-basis book's horizon is its last settlement.
        with tempfile.TemporaryDirectory() as td:
            p = self._project(td, [_bt("2025-12-31", "2026-01-02", "XYZ.TO",
                                       100, 1000.0, "CAD")])
            r = cli(p, "shares", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["as_of"], "2026-01-02")

    def test_unreadable_base_is_named(self):
        # A2-0702.
        with tempfile.TemporaryDirectory() as td:
            p = self._project(td, [])
            (p / "work" / "margin_base.json").write_text('{"transactions": [')
            r = cli(p, "shares")
            self.assertIn("margin_base.json cannot be read", r.stderr)


# ------------------------------------------------- run: expired options

class TestRunExpiredOptionWarning(unittest.TestCase):
    """A2-1210: the per-run warning uses option_boundary's cutoff (an
    expiry ON the cutoff included; the cutoff extended to the last data
    date), like its twin expired_open."""

    def _warn(self, year, base_rows, inv):
        from taxjson.bin import taxjson_run as R
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "m_base.json").write_text(
                json.dumps({"transactions": base_rows}))
            g = cache / "m_gains.json"
            g.write_text(json.dumps({"inventory": inv}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                R._warn_expired_open_options("m", g, cache, year)
            return err.getvalue()

    def test_expiry_on_year_end_and_after(self):
        opt_a = "ABC251231C00010000.US"
        out = self._warn(2025, [
            _bt("2025-06-02", "2025-06-03", opt_a, 1, 100.0, "USD")],
            [{"symbol": opt_a, "qty": 1, "total_cost": 100.0}])
        self.assertIn(opt_a, out)
        opt_b = "ABC260116P00010000.US"
        out = self._warn(2025, [
            _bt("2025-06-02", "2025-06-03", opt_b, -1, 100.0, "USD"),
            _bt("2026-01-30", "2026-01-30", "ABC.US", 1, 10.0, "USD")],
            [{"symbol": opt_b, "qty": -1, "total_cost": -100.0}])
        self.assertIn(opt_b, out)


# ------------------------------------------------- run: config twins

class TestValidateConfigTwins(unittest.TestCase):
    """A2-1211: the option-timing settings are refused by every config
    reader (load_config), before run's validate_config: one check."""

    def test_load_config_refuses(self):
        for line in ('option_premium_timing = "grants"',
                     'option_grant_timing_since = "2025"',
                     'option_buyback_loss_superficial = "yes"'):
            with tempfile.TemporaryDirectory() as td:
                p = projects_both(td, year=2025, tail="")["canada"]
                t = (p / "taxjson.toml").read_text()
                (p / "taxjson.toml").write_text(
                    t.replace("[settings]\n", f"[settings]\n{line}\n"))
                r = cli(p, "run", "--no-input")
                self.assertNotEqual(r.returncode, 0, line)
                self.assertIn(line.split(" ")[0], r.stderr)


if __name__ == "__main__":
    unittest.main()
