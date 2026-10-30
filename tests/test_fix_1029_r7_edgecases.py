"""Fix round 7 of 2026-10-29, group "edgecases": `taxjson edge-cases`'s
year-boundary, expiry and long-call reviews (lib/edge_cases), on
synthetic books the gains engine computes, as `taxjson run` writes
them to work/.

Each class names the backlog item it pins.

Made-up symbols (ZZE*) and small amounts only.
"""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.bin.taxjson_split_gains import split_for_account
from taxjson.lib.core import TaxTransaction
from taxjson.lib.edge_cases import analyze, render_text
from taxjson.lib.pipeline import GainsRequest, run_gains

STOCK = "ZZE.TO"
PUT = "ZZE251231P00001000.TO"       # strike 1.00, expires 2025-12-31


def _t(acct, action, day, sym, qty, net, settle=None, price=None,
       cur="CAD", **kw):
    return TaxTransaction(
        account=acct, action=action, date=day, date_settle=settle or day,
        time=kw.pop("time", "10:00:00"), symbol=sym, quantity=qty,
        net_amount=net, currency=cur,
        price=(abs(net / qty) if price is None and qty else price or 0.0),
        id=kw.pop("id", f"{acct}-{action}-{day}-{sym}-{qty}"), **kw)


def _edge(books, sheltered=None, *, country="canada", year=2025,
          tax_date=None):
    """analyze() and the --details text of a project whose taxable
    accounts are `books` and sheltered accounts `sheltered` ({account:
    rows} each); more than one taxable account is the blended pass split
    per account, as the run does."""
    sheltered = sheltered or {}
    tax_date = tax_date or ("settle" if country == "canada" else "trade")
    taxable = [t for rows in books.values() for t in rows]
    shel = [t for rows in sheltered.values() for t in rows]
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        doc = run_gains(copy.deepcopy(taxable), copy.deepcopy(shel),
                        req=GainsRequest(country=country, year=year,
                                         taxable=True, tax_date=tax_date))
    accounts = {n: {"type": "taxable"} for n in books}
    accounts.update({n: {"type": "sheltered"} for n in sheltered})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        for n, rows in dict(books, **sheltered).items():
            (work / f"{n}_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in rows]}))
        for n, rows in books.items():
            out = doc if len(books) == 1 else split_for_account(
                doc, n, [t.to_dict() for t in rows])
            (work / f"{n}_gains_wash.json").write_text(json.dumps(out))
        cfg = {"settings": {"year": year, "country": country,
                            "tax_date": tax_date},
               "accounts": accounts}
        with contextlib.redirect_stderr(io.StringIO()):
            edge = analyze(root, cfg)
        text = "\n".join(render_text(edge, details=True, width_=200))
    return doc, edge, text


def _gains(doc, sym=STOCK):
    return sorted((t["id"], round(float(t["gain"]), 2))
                  for t in doc["transactions"]
                  if t.get("gain") is not None and not t.get("action")
                  and t.get("symbol") == sym)


# A put written in November and assigned on its Dec 31 expiry.
WRITE = _t("margin", "BUYSELL", "2025-11-03", PUT, -1, 10.0)
ASSIGNED = _t("margin", "ASSIGN", "2025-12-31", PUT, 1, 0.0, price=0.0)


@rule("CA-RPT-07", "CA-OPT-08")
class TestAssignedExpiryLandsWithItsLeg(unittest.TestCase):
    """TJ-0674: the shares are delivered on Jan 2 (within the engine's
    7-day pairing window): the premium goes into a 2026 share leg."""

    def test_next_year_delivery(self):
        leg = _t("margin", "ASSIGN", "2026-01-02", STOCK, 100, -100.0)
        doc, edge, text = _edge({"margin": [WRITE, ASSIGNED, leg]})
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertTrue(r["assigned"])
        self.assertEqual(r["settlement"], "physical")
        self.assertEqual(r["lands_in"], 2026)
        self.assertIn("folds into the ZZE.TO leg of 2026-01-02, which "
                      "lands in 2026", r["why"])
        # The engine put the delivered shares in its 2026 books.
        self.assertEqual([h for h in doc.get("inventory_year_end") or []
                          if h.get("symbol") == STOCK], [])

    def test_a_plain_buy_at_the_strike_is_the_leg(self):
        # The plain convention: a BUYSELL leg at the strike, booked
        # the next business day.
        leg = _t("margin", "BUYSELL", "2026-01-02", STOCK, 100, -100.0)
        _doc, edge, _text = _edge({"margin": [WRITE, ASSIGNED, leg]})
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertEqual(r["lands_in"], 2026)

    def test_same_day_leg_settling_in_the_new_year(self):
        leg = _t("margin", "ASSIGN", "2025-12-31", STOCK, 100, -100.0,
                 settle="2026-01-02")
        _doc, edge, _text = _edge({"margin": [WRITE, ASSIGNED, leg]})
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertEqual(r["lands_in"], 2026)
        self.assertIn("(settles 2026-01-02)", r["why"])

    def test_no_leg_in_the_window_names_none(self):
        # The shares trade in the account (so not cash-settled), but no
        # leg comes within 7 days: no year is claimed.
        old = _t("margin", "BUYSELL", "2025-02-03", STOCK, 100, -100.0)
        _doc, edge, text = _edge({"margin": [old, WRITE, ASSIGNED]})
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertIsNone(r["lands_in"])
        self.assertIn("no ZZE.TO leg in this account up to 7 days after "
                      "it takes the premium", r["why"])
        (line,) = [x for x in text.splitlines() if PUT in x]
        self.assertTrue(line.endswith("2025-12-31         -"), line)


@rule("CA-RPT-07", "CA-OPT-08")
class TestCashSettledAssignment(unittest.TestCase):
    """TJ-0675: no share leg, no share inventory: a cash close."""

    def test_cash_close(self):
        cash = _t("margin", "ASSIGN", "2025-12-31", PUT, 1, -20.0)
        doc, edge, text = _edge({"margin": [WRITE, cash]})
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertEqual(r["settlement"], "cash")
        self.assertEqual(r["lands_in"], 2025)
        self.assertEqual(r["outcome"], "1 assigned for cash")
        self.assertIn("settled in cash (20.00 paid)", r["why"])
        self.assertNotIn("folds into", r["why"])
        self.assertIn("1 assigned for cash", text)


@rule("CA-RPT-07")
class TestUntypedManualReturnOfCapital(unittest.TestCase):
    """TJ-0685: a .tt ADJUST with no type is a return of capital (the
    shared income_dating.is_return_of_capital), listed like one tagged
    type=roc."""

    def test_untyped_and_tagged_alike(self):
        unit = "ZZE.UN.TO"
        buy = _t("margin", "BUYSELL", "2025-01-02", unit, 10, -100.0)
        rows = {}
        for typ in ("", "roc"):
            adj = _t("margin", "ADJUST", "2025-12-31", unit, 0, -10.0,
                     price=0.0, type=typ)
            _doc, edge, _text = _edge({"margin": [buy, adj]})
            rows[typ] = [(r["action"], r["amount"], r["lands_in"])
                         for r in edge["year_boundary"]["income"]]
        self.assertEqual(rows[""], [("ROC", -10.0, 2025)])
        self.assertEqual(rows[""], rows["roc"])


@rule("US-RPT-05", "US-WASH-05")
class TestUSShortCoverWindowAcrossYearEnd(unittest.TestCase):
    """TJ-0686: a short covered at a loss on Dec 29 and re-shorted on
    Jan 2: the engine disallows the loss; the review lists the window."""

    def test_reshort_in_january(self):
        sym = "ZZE.US"
        rows = [_t("margin", "BUYSELL", "2025-11-03", sym, -10, 100.0,
                   cur="USD"),
                _t("margin", "BUYSELL", "2025-12-29", sym, 10, -110.0,
                   cur="USD"),
                _t("margin", "BUYSELL", "2026-01-02", sym, -10, 110.0,
                   cur="USD")]
        doc, edge, text = _edge({"margin": rows}, country="usa")
        (loss,) = [t for t in doc["transactions"]
                   if t.get("symbol") == sym
                   and t.get("raw_gain") is not None
                   and float(t["raw_gain"]) < 0]
        self.assertAlmostEqual(float(loss["disallowed_amount"]), 10.0)
        (w,) = edge["year_boundary"]["loss_windows"]
        self.assertEqual(w["denied"], 10.0)
        self.assertEqual(w["other_year_acquisitions"],
                         "margin 2026-01-02 -10")
        self.assertIn("short-cover loss", w["why"])
        self.assertIn("new short sales in the other year", w["why"])
        self.assertIn("comes back when that short is covered", w["why"])

    def test_a_buy_never_replaces_a_short_cover_loss(self):
        sym = "ZZE.US"
        rows = [_t("margin", "BUYSELL", "2025-11-03", sym, -10, 100.0,
                   cur="USD"),
                _t("margin", "BUYSELL", "2025-12-29", sym, 10, -110.0,
                   cur="USD"),
                _t("margin", "BUYSELL", "2026-01-02", sym, 10, -110.0,
                   cur="USD")]
        _doc, edge, _text = _edge({"margin": rows}, country="usa")
        self.assertEqual(edge["year_boundary"]["loss_windows"], [])


@rule("CA-RPT-07", "CA-SL-08")
class TestLongCallHeldPerHolder(unittest.TestCase):
    """TJ-0689: the RRSP's call, bought and sold inside the window, is
    not held on day 30; the TFSA's old call is the TFSA's."""

    CALL = "ZZE251219C00001000.TO"

    def _run(self, rrsp_sells=True):
        margin = [_t("margin", "BUYSELL", "2025-01-02", STOCK, 100, -100.0),
                  _t("margin", "BUYSELL", "2025-11-03", STOCK, -100, 90.0)]
        tfsa = [_t("tfsa", "BUYSELL", "2025-01-02", self.CALL, 1, -10.0)]
        rrsp = [_t("rrsp", "BUYSELL", "2025-11-04", self.CALL, 1, -10.0)]
        if rrsp_sells:
            rrsp.append(_t("rrsp", "BUYSELL", "2025-11-05", self.CALL, -1,
                           10.0))
        return _edge({"margin": margin}, {"tfsa": tfsa, "rrsp": rrsp})

    def test_sold_inside_the_window(self):
        doc, edge, _text = self._run()
        (loss,) = [t for t in doc["transactions"]
                   if t.get("symbol") == STOCK and t.get("gain") is not None
                   and not t.get("action")]
        self.assertAlmostEqual(float(loss["disallowed_amount"] or 0), 0.0)
        (c,) = edge["calls_in_windows"]
        (it,) = c["items"]
        self.assertEqual(it["account"], "rrsp")
        self.assertEqual(it["held_at_day30"], 0.0)
        self.assertIn("no longer held on day 30, so it does not",
                      it["why"])
        self.assertNotIn("backs a denial", it["why"])

    def test_still_held_backs_the_denial(self):
        doc, edge, _text = self._run(rrsp_sells=False)
        (it,) = edge["calls_in_windows"][0]["items"]
        self.assertEqual(it["held_at_day30"], 1.0)
        self.assertIn("still held on day 30, so it backs a denial",
                      it["why"])


@rule("CA-RPT-07")
class TestPooledCrossYearSale(unittest.TestCase):
    """TJ-0690: account a holds the shares, account b sells them on
    Dec 31 settling Jan 2: one ACB pool, so b's sale is a disposition."""

    def test_sale_from_the_pool(self):
        a = [_t("a", "BUYSELL", "2025-01-02", STOCK, 100, -100.0)]
        b = [_t("b", "BUYSELL", "2025-12-31", STOCK, -100, 200.0,
                settle="2026-01-02")]
        _doc, edge, _text = _edge({"a": a, "b": b}, year=2026)
        (r,) = edge["year_boundary"]["straddles"]
        self.assertEqual(r["account"], "b")
        self.assertEqual(r["gain"], 100.0)
        self.assertIn("so it is a 2026 sale: a disposition whose gain or "
                      "loss is in 2026's Schedule 3 (+100.00)", r["why"])
        self.assertNotIn("short sale", r["why"])

    def test_the_old_year_project_too(self):
        # In the 2025 project the sale has no gain row (it is 2026's),
        # and the pooled position still makes it a sale.
        a = [_t("a", "BUYSELL", "2025-01-02", STOCK, 100, -100.0)]
        b = [_t("b", "BUYSELL", "2025-12-31", STOCK, -100, 200.0,
                settle="2026-01-02")]
        _doc, edge, _text = _edge({"a": a, "b": b}, year=2025)
        (r,) = edge["year_boundary"]["straddles"]
        self.assertIn("so it is a 2026 sale", r["why"])


@rule("CA-RPT-07")
class TestSameDaySalesEachTheirGain(unittest.TestCase):
    """TJ-0691: 5 shares sold at 2 and 10 at 3 on Dec 31, both settling
    Jan 2, from 20 bought at 1: gains 5 and 20, not 25 each."""

    def test_each_sale_its_own_gain(self):
        rows = [_t("margin", "BUYSELL", "2025-01-02", STOCK, 20, -20.0),
                _t("margin", "BUYSELL", "2025-12-31", STOCK, -5, 10.0,
                   settle="2026-01-02"),
                _t("margin", "BUYSELL", "2025-12-31", STOCK, -10, 30.0,
                   settle="2026-01-02", time="11:00:00")]
        doc, edge, _text = _edge({"margin": rows}, year=2026)
        self.assertEqual(sorted(g for _i, g in _gains(doc)), [5.0, 20.0])
        got = sorted((r["qty"], r["gain"])
                     for r in edge["year_boundary"]["straddles"])
        self.assertEqual(got, [(-10.0, 20.0), (-5.0, 5.0)])
        whys = sorted(r["why"] for r in edge["year_boundary"]["straddles"])
        self.assertIn("(+20.00)", whys[0] + whys[1])
        self.assertIn("(+5.00)", whys[0] + whys[1])
        self.assertNotIn("(+25.00)", whys[0] + whys[1])


if __name__ == "__main__":
    unittest.main()
