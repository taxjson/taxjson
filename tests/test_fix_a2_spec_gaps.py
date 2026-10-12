"""Re-audit-2 conformance gaps: a mutant of each rule below survived the
whole suite in the re-audit's conformance pass. Each test here fails on
that mutant and passes on the real code. Synthetic data only.
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx


def _gains_one(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         **dict(dict(year=2025), **req)))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res):
    return [t for t in res["transactions"] if t.get("qty")]


class TestCarrybackReachesThreeYears(unittest.TestCase):
    """CA-RPT-10, mutant RPT10-carryback (carry back 2 years)."""

    @rule("CA-RPT-10")
    def test_third_prior_year_is_a_candidate(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        nets = {2020: {"net": 9000.0, "st": 0, "lt": 0, "dispositions": 1},
                2023: {"net": -1000.0, "st": 0, "lt": 0,
                       "dispositions": 1}}
        rows = {r["year"]: r for r in build_canada_ledger(nets, {})["rows"]}
        self.assertEqual(rows[2023]["carryback_candidates"],
                         [{"year": 2020, "amount": 1000.0}])


def _engine(country, rows):
    """compute_gains of one country's engine on TaxTransactions."""
    from taxjson.lib.core import get_tax_rules
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = get_tax_rules(country).compute_gains(list(rows))
    res["_stderr"] = err.getvalue()
    return res


class TestPhantomLossNeverDenied(unittest.TestCase):
    """CA-ACB-11 (mutant ACB11-taintfeeds) and US-BASIS-04 (mutant
    USB04-phantomnowash): a loss drawn on phantom (missing-history)
    shares is listed for manual reporting and never feeds the
    superficial-loss / wash-sale matching, even with a replacement
    bought in the window and still held."""

    @rule("CA-ACB-11")
    def test_canada_tainted_loss_with_held_rebuy(self):
        res = _engine("canada", [
            tx("OPENING_BALANCE", "2026-01-02", "TNT.TO", 100, 0.0,
               currency="CAD", time="00:00:00", price=0.0),
            tx("BUYSELL", "2026-01-05", "TNT.TO", 100, 5000.0,
               currency="CAD"),
            tx("BUYSELL", "2026-02-02", "TNT.TO", -200, 4000.0,
               currency="CAD"),
            tx("BUYSELL", "2026-02-10", "TNT.TO", 100, 2000.0,
               currency="CAD")])
        self.assertEqual(res["wash_sales"], [])
        loss = [t for t in _sales(res) if t["qty"] == 200][0]
        self.assertTrue(loss["tainted"])
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)
        inv = {i["symbol"]: i for i in res["inventory"]}
        self.assertAlmostEqual(inv["TNT.TO"]["total_cost"], 2000.0,
                               places=2)

    @rule("US-BASIS-04")
    def test_usa_tainted_lot_loss_with_held_rebuy(self):
        # A phantom lot (basis 0) sold for less than nothing (the
        # commission exceeded the gross) is a loss on a tainted lot.
        res = _engine("usa", [
            tx("OPENING_BALANCE", "2025-01-02", "TNT.US", 10, 0.0,
               time="00:00:00", price=0.0),
            tx("BUYSELL", "2025-03-03", "TNT.US", -10, -5.0, price=0.01),
            tx("BUYSELL", "2025-03-10", "TNT.US", 10, 100.0)])
        self.assertEqual(res["wash_sales"], [])
        loss = [t for t in _sales(res) if t["qty"] in (10, -10)
                and t["date"] == "2025-03-03"][0]
        self.assertTrue(loss["tainted"])
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)
        lots = [i for i in res["inventory"] if i["symbol"] == "TNT.US"]
        self.assertAlmostEqual(sum(i["total_cost"] for i in lots), 100.0,
                               places=2)


class TestNothingButTheIdenticalContractReplacesAnOption(unittest.TestCase):
    """CA-SL-06: a long call's loss is replaced only by the identical
    contract. The audit's mutants (SL06-shares-replace-option: share buys
    admitted as candidates for an option loss; SL06-series: the call scan
    run for an option loss) are equivalent on the current engine — the
    still-held walk counts the loss contract's own class only, and the
    call scan compares the call's underlying with that class — so these
    tests pin the behaviour itself."""

    CALL = "ZZQ260116C00050000.US"

    def _loss_then(self, *rebuy):
        return _gains_one("canada", [
            tx("BUYSELL", "2025-06-02", self.CALL, 1, 500),
            tx("BUYSELL", "2025-07-01", self.CALL, -1, 200),
            tx("BUYSELL", "2025-07-08", *rebuy)])

    def _loss(self, res):
        return [t for t in _sales(res) if t["symbol"] == self.CALL][0]

    @rule("CA-SL-06")
    def test_shares_bought_do_not_replace_the_call(self):
        loss = self._loss(self._loss_then("ZZQ.US", 100, 5000))
        self.assertAlmostEqual(loss["gain"], -300.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule("CA-SL-06")
    def test_another_series_does_not_replace_the_call(self):
        loss = self._loss(self._loss_then("ZZQ260116C00055000.US", 1, 150))
        self.assertAlmostEqual(loss["gain"], -300.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule("CA-SL-06")
    def test_the_identical_contract_does(self):
        loss = self._loss(self._loss_then(self.CALL, 1, 150))
        self.assertAlmostEqual(loss["disallowed_amount"], 300.0, places=2)


class TestOvernightFillBeforeAHoliday(unittest.TestCase):
    """CA-DATE-SESSION / US-DATE-SESSION (mutant SESSION-holiday): an
    overnight-session fill trades on the next NYSE trading day — a
    holiday is skipped, not only a weekend."""

    def _one(self):
        from test_fix_ibparse import _parse_ib, _trade
        from test_fix_l_ibparse import _trades
        # Thursday 2026-07-02 20:30 ET; Friday 2026-07-03 is the NYSE's
        # Independence Day holiday (July 4 is a Saturday).
        _, txs, _ = _parse_ib(_trades(
            _trade('QZN', '2026-07-02, 20:30:00', 10, 10.0, -100.0,
                   cur='USD')))
        self.assertEqual(len(txs), 1)
        return txs[0]

    @rule("CA-DATE-SESSION")
    def test_canada(self):
        t = self._one()
        self.assertEqual((t["date"], t["date_settle"]),
                         ("2026-07-06", "2026-07-07"))

    @rule("US-DATE-SESSION")
    def test_usa(self):
        t = self._one()
        self.assertEqual(t["date"], "2026-07-06")


class TestUsRocExcessTerm(unittest.TestCase):
    """US-ROC-02 (mutant USROC02-term): the part of a return of capital
    beyond a lot's basis is short- or long-term by that lot's holding
    period — the earlier test only had a short-term lot."""

    @rule("US-ROC-02")
    def test_long_held_lot_gives_a_long_term_gain(self):
        res = _engine("usa", [
            tx("BUYSELL", "2023-01-10", "PRFD.US", 100, 1000.0),
            tx("BUYSELL", "2025-03-03", "PRFD.US", 100, 1000.0),
            tx("ADJUST", "2025-06-30", "PRFD.US", 0, -2400.0, type="roc",
               price=0.0)])
        deemed = sorted((g["acquired_date"], round(g["gain"], 2), g["term"])
                        for g in res["transactions"] if g.get("deemed"))
        self.assertEqual(deemed, [("2023-01-10", 200.0, "LONG_TERM"),
                                  ("2025-03-03", 200.0, "SHORT_TERM")])


class TestUsTrustRocKeepsThePayDate(unittest.TestCase):
    """US-INC-DATE-ROC: a Canadian trust's return of capital with a
    printed record date lowers US basis on its PAY date (the record-date
    rule is Canada's, CA-INC-DATE-ROC-TRUST). The audit's mutant
    (USINCDATE-roc: roc_record_date's country gate removed) is
    equivalent: is_canadian_trust is Canada-only too."""

    @rule("US-INC-DATE-ROC")
    def test_record_date_is_not_used(self):
        from taxjson.lib.income_dating import IncomeRules
        row = {"action": "ADJUST", "type": "roc", "symbol": "ZZT.UN.TO",
               "date": "2026-01-15", "record_date": "2025-12-31",
               "net_amount": -50.0, "currency": "CAD"}
        r = IncomeRules(country="usa")
        self.assertEqual(r.roc_date(row), "2026-01-15")
        self.assertEqual(r.row_date(row), "2026-01-15")


class TestUsEstimateInputs(unittest.TestCase):
    """US-RPT-07 (mutants USRPT07-pil, USRPT07-unterm): `taxjson
    estimate` taxes a payment in lieu as ordinary income (never
    qualified) and folds gains with no term into short-term."""

    CFG = {"settings": {"country": "usa", "year": 2025}}

    def _est(self, **est):
        from taxjson.bin.taxjson_run import _tax_estimate_result
        base = {"realized": 0.0, "st": 0.0, "lt": 0.0, "div_ca": 0.0,
                "div_foreign": 0.0, "pil": 0.0}
        base.update(est)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = _tax_estimate_result(self.CFG, base, other_income=80000.0,
                                     other_losses=0.0, province=None)
        return r, err.getvalue()

    @rule("US-RPT-07")
    def test_payment_in_lieu_is_ordinary(self):
        # 1,000 PIL on 80,000 of other income: 22% band = 220 (as
        # qualified it would be taxed at 15% = 150).
        r, _ = self._est(pil=1000.0)
        self.assertEqual(r["estimated_tax"], 220.0)

    @rule("US-RPT-07")
    def test_gain_with_no_term_is_short_term(self):
        r, err = self._est(realized=1000.0)
        self.assertEqual(r["st_input"], 1000.0)
        self.assertEqual(r["estimated_tax"], 220.0)
        self.assertIn("SHORT-TERM", err)


class TestUsConsumedTaxableReplacement(unittest.TestCase):
    """A2-0817 / US-WASH-21: a purchase in the loss's own account sold
    before the loss no longer washes it (an IRA one still does,
    US-WASH-11; one in another taxable account too, US-WASH-22 — owner
    decision on A2-0544, which replaced the other-account case pinned
    here). The dead 'fully consumed' branch that mutant USW06-stillheld
    edited is gone."""

    def _book(self, rebuy_account):
        return [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
                   account="a"),
                tx("BUYSELL", "2025-02-10", "XYZ.US", 100, 4100.0,
                   account=rebuy_account),
                tx("BUYSELL", "2025-02-20", "XYZ.US", -100, 4200.0,
                   account=rebuy_account),
                tx("BUYSELL", "2025-03-01", "XYZ.US", -100, 4000.0,
                   account="a")]

    @rule("US-WASH-21")
    def test_taxable_replacement_sold_before_the_loss(self):
        # Same account: buy R, sell R, buy L, sell L at a loss — R's
        # shares were sold first in, first out before the loss.
        book = [tx("BUYSELL", "2025-02-10", "XYZ.US", 100, 4100.0,
                   account="a"),
                tx("BUYSELL", "2025-02-20", "XYZ.US", -100, 4200.0,
                   account="a"),
                tx("BUYSELL", "2025-02-21", "XYZ.US", 100, 5000.0,
                   account="a"),
                tx("BUYSELL", "2025-03-01", "XYZ.US", -100, 4000.0,
                   account="a")]
        r = _gains_one("usa", book)
        loss = [t for t in _sales(r) if t["date"] == "2025-03-01"][0]
        self.assertAlmostEqual(loss["gain"], -1000.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)
        self.assertAlmostEqual(loss["permanently_disallowed"], 0.0)

    @rule("US-WASH-11")
    def test_ira_replacement_sold_before_the_loss(self):
        book = self._book("ira")
        r = _gains_one("usa", [t for t in book if t.account == "a"],
                       sheltered=[t for t in book if t.account == "ira"])
        loss = _sales(r)[0]
        self.assertAlmostEqual(loss["permanently_disallowed"], 1000.0,
                               places=2)


if __name__ == "__main__":
    unittest.main()
