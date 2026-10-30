"""Fix round 7 (wave B) of 2026-10-29, slip-audit: backlog TJ-0791 to
TJ-0806 (the third worktree audit's continuation).

Synthetic data only: made-up tickers (SYN, ALFA, BETA), issuers and
broker accounts (U1234567).
"""
import csv
import io
import json
import unittest
from pathlib import Path

from _tmpfiles import private_dir
from taxjson.lib import slip_audit as SA
from taxjson.lib.ib_dividends import Component, Payment
from tax_rules import rule

ACCOUNTS = {"margin": {"type": "taxable"}}


def _row(symbol="SYN.UN.TO", day="2025-06-02", amt=50.0, action="DIVIDEND",
         category="ca_div", cur="CAD", tax=None, record="",
         source="trades.tt", hashes=()):
    return SA.BookRow("margin", f"id-{symbol}-{day}-{action}-{amt}", action,
                      symbol, SA._root(symbol), cur, amt, amt, day,
                      tax or day, source, "", category,
                      pil=(action == "DIVIDEND_IN_LIEU"), hashes=hashes,
                      record=record)


def _pay(symbol="SYN.UN", day="2025-07-02", ex="2025-06-15", parts=(),
         cur="CAD", country="CA", pil=False):
    """An IB report payment: parts = ((slip, category, label, amount),)."""
    return Payment(symbol=symbol, country=country, currency=cur,
                   pay_date=day, ex_date=ex, pil=pil,
                   components=[Component(sl, cat, label, a, a, 0.0, 0.0)
                               for sl, cat, label, a in parts])


def _ib(payments):
    return SA.Slip("U1***.2025.dividends.csv", "T5", "IB", "margin", "CAD",
                   source="ib", payments=list(payments))


REPORT_HEAD = ["DataDiscriminator", "Currency", "Symbol", "Conid",
               "Country", "ReportDate", "ExDate", "Shares",
               "RevenueComponent", "Gross", "GrossInBase", "Withhold",
               "WithholdInBase"]


def _report(rows):
    """An IB dividends report (synthetic account U1234567, CAD base):
    rows = (discriminator, symbol, pay YYYYMMDD, ex, component, amount)."""
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(["Account", "Header", "AccountNumber", "BaseCurrency"])
    w.writerow(["Account", "Data", "U1234567", "CAD"])
    w.writerow(["DividendDetail", "Header"] + REPORT_HEAD)
    for disc, sym, pay, ex, comp, amt in rows:
        w.writerow(["DividendDetail", "Data", disc, "CAD", sym, "1", "CA",
                    pay, ex, 10 if disc == "Summary" else "", comp, amt,
                    amt, 0, 0])
    return f.getvalue()


def _project(book_rows, report=None, slips_toml="year = 2025\n"):
    """A project folder with work/margin_base.json holding `book_rows`
    (transaction dicts) and inputs/slips/: an audit() without a run."""
    root = Path(private_dir("r7b-slip-"))
    (root / "inputs" / "margin").mkdir(parents=True)
    sd = root / "inputs" / "slips"
    sd.mkdir()
    (root / "work").mkdir()
    tx = []
    for i, r in enumerate(book_rows):
        t = {"id": f"t{i}", "account": "margin", "currency": "CAD",
             "source": "trades.tt", "description": ""}
        t.update(r)
        tx.append(t)
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": tx, "metadata": {}}), encoding="utf-8")
    if report is not None:
        (sd / "U1234567.2025.dividends.csv").write_text(report,
                                                         encoding="utf-8")
        slips_toml += ('[[ib_report]]\nfile = "U1234567.2025.dividends.csv"'
                       '\naccount = "margin"\n')
    (sd / "slips.toml").write_text(slips_toml, encoding="utf-8")
    return root


CFG = {"settings": {"year": 2025, "country": "canada",
                    "base_currency": "CAD"},
       "accounts": {"margin": {"type": "taxable"}}}


def _div(symbol, day, amt, **kw):
    t = {"action": "DIVIDEND", "symbol": symbol, "date": day,
         "gross_amount": amt, "net_amount": amt}
    t.update(kw)
    return t


def _slip(table, n=1):
    t = {"account": "margin", "currency": "CAD"}
    t.update(table)
    return SA._slip_from_table(t, f"slips.toml [[slip]] #{n}", ACCOUNTS)


def _securities(slips, rows, sales=None, year=2025):
    cgd, tt, notes = [], {}, []
    recs = SA._securities("margin", "margin", slips, rows, year, 1.0, cgd,
                          tt, notes, rows, sales=sales or {})
    return recs, tt.get("margin", []), notes


class TestSlipLinesAreDetail(unittest.TestCase):
    """TJ-0791: a fund's T3 with a box-42 line of the same fund counts
    its return of capital once."""

    rows = [_row(day="2025-06-02"), _row(day="2025-12-01")]

    def test_box_and_line_count_once(self):
        both = _slip({"type": "T3", "security": "SYN.UN.TO",
                      "boxes": {"49": 60, "42": 40},
                      "line": [{"symbol": "SYN.UN.TO", "box": "42",
                                "amount": 40}]})
        alone = _slip({"type": "T3", "security": "SYN.UN.TO",
                       "boxes": {"49": 60, "42": 40}})
        r1, l1, _ = _securities([both], list(self.rows))
        r2, l2, _ = _securities([alone], list(self.rows))
        self.assertEqual(r1[0]["slip"]["roc"], 40.0)
        self.assertEqual(l1, l2)
        self.assertIn("ADJUST 2025-12-01 16:00:00 SYN.UN.TO CAD -40.00 "
                      "type=roc", l1)
        self.assertIn("DIVIDEND 2025-12-01 16:00:00 SYN.UN.TO 0 CAD 0 "
                      "-40.00", l1)

    def test_lines_only_fund_slip_counts_once(self):
        s = _slip({"type": "T3", "security": "SYN.UN.TO",
                   "line": [{"symbol": "SYN.UN.TO", "box": "42",
                             "amount": 40}]})
        recs, _lines, _ = _securities([s], list(self.rows))
        self.assertEqual(recs[0]["slip"]["roc"], 40.0)

    def test_lines_above_the_box_are_said(self):
        s = _slip({"type": "T3", "security": "SYN.UN.TO",
                   "boxes": {"49": 60, "42": 40},
                   "line": [{"symbol": "SYN.UN.TO", "box": "42",
                             "amount": 50}]})
        recs, _lines, notes = _securities([s], list(self.rows))
        self.assertEqual(recs[0]["slip"]["roc"], 50.0)
        self.assertTrue(any("add up to 50.00 of return of capital" in n
                            for n in notes), notes)


class TestDatedSlipLine(unittest.TestCase):
    """TJ-0792: a dated box-42 line puts its return of capital on its
    own distribution, before the sale that follows it."""

    rows = [_row(day="2025-06-02"), _row(day="2025-12-01")]
    sales = {"SYN.UN": [("2025-07-02", "margin", 5.0)]}

    def test_dated_line_keeps_its_date(self):
        s = _slip({"type": "T3", "boxes": {"49": 60, "42": 40},
                   "line": [{"symbol": "SYN.UN.TO", "date": "2025-06-02",
                             "box": "42", "amount": 40}]})
        _recs, lines, _ = _securities([s], list(self.rows), self.sales)
        adj = [ln for ln in lines if ln.startswith("ADJUST")]
        self.assertEqual(adj, ["ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD "
                               "-40.00 type=roc"])

    def test_undated_rest_is_spread(self):
        s = _slip({"type": "T3", "security": "SYN.UN.TO",
                   "boxes": {"49": 60, "42": 40},
                   "line": [{"symbol": "SYN.UN.TO", "date": "2025-06-02",
                             "box": "42", "amount": 30}]})
        _recs, lines, _ = _securities([s], list(self.rows), self.sales)
        adj = [ln for ln in lines if ln.startswith("ADJUST")]
        self.assertEqual(adj, [
            "ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -30.00 type=roc",
            "ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -5.00 type=roc",
            "ADJUST 2025-12-01 16:00:00 SYN.UN.TO CAD -5.00 type=roc"])
        # The dividend correction is the whole return of capital.
        self.assertIn("DIVIDEND 2025-12-01 16:00:00 SYN.UN.TO 0 CAD 0 "
                      "-40.00", lines)

    def test_line_on_a_trust_distribution_keeps_its_record_date(self):
        rows = [_row(day="2025-07-02", amt=100.0, tax="2025-06-15",
                     record="2025-06-15")]
        s = _slip({"type": "T3", "boxes": {"49": 60, "42": 40},
                   "line": [{"symbol": "SYN.UN.TO", "date": "2025-07-02",
                             "box": "42", "amount": 40}]})
        _recs, lines, _ = _securities([s], rows)
        self.assertIn("ADJUST 2025-07-02 16:00:00 SYN.UN.TO CAD -40.00 "
                      "type=roc record=2025-06-15", lines)

    def test_booked_roc_on_the_line_date_is_not_suggested_again(self):
        rows = list(self.rows) + [_row(day="2025-06-02", amt=40.0,
                                       action="ADJUST", category="roc")]
        s = _slip({"type": "T3", "security": "SYN.UN.TO",
                   "boxes": {"49": 60, "42": 50},
                   "line": [{"symbol": "SYN.UN.TO", "date": "2025-06-02",
                             "box": "42", "amount": 40}]})
        _recs, lines, _ = _securities([s], rows)
        adj = [ln for ln in lines if ln.startswith("ADJUST")]
        # The 10 no line dates goes on the last distribution.
        self.assertEqual(adj, ["ADJUST 2025-12-01 16:00:00 SYN.UN.TO CAD "
                               "-10.00 type=roc"])


class TestIbRocRecordDate(unittest.TestCase):
    """TJ-0794: the IB-dated ROC suggestion keeps the distribution's
    record date the books date it by."""

    def test_record_date_is_kept(self):
        rows = [_row(day="2025-07-02", amt=100.0, tax="2025-06-15",
                     record="2025-06-15")]
        pay = _pay(parts=(("T3", "roc", "T3: Return of Capital", 40.0),
                          ("T3", "eligible", "T3: Eligible Dividend Income",
                           60.0)))
        _recs, lines, _ = _securities([_ib([pay])], rows)
        self.assertEqual(lines, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -40.00 type=roc "
            "record=2025-06-15",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -40.00 "
            "record=2025-06-15 label=distribution"])

    def test_pay_dated_distribution_has_no_record(self):
        rows = [_row(day="2025-07-02", amt=100.0)]
        pay = _pay(parts=(("T3", "roc", "T3: Return of Capital", 40.0),
                          ("T3", "eligible", "T3: Eligible Dividend Income",
                           60.0)))
        _recs, lines, _ = _securities([_ib([pay])], rows)
        self.assertEqual(lines, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -40.00 type=roc",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -40.00"])


class TestAmendedSlipReplaces(unittest.TestCase):
    """TJ-0793: a typed amended slip replaces its original."""

    def _t5(self, status, amount, n, **kw):
        t = {"type": "T5", "issuer": "Syn Broker", "boxes": {"24": amount}}
        if status:
            t["status"] = status
        t.update(kw)
        return _slip(t, n)

    def test_amended_replaces_original(self):
        slips = [self._t5("original", 100, 1), self._t5("amended", 80, 2)]
        problems, nc = [], []
        kept = SA.apply_amendments(slips, problems, nc)
        self.assertEqual([s.amounts["ca_div"] for s in kept], [80.0])
        self.assertEqual(nc, [])
        self.assertIn("is replaced by the amended slip slips.toml [[slip]] "
                      "#2", problems[0])

    def test_other_issuer_or_currency_is_kept(self):
        slips = [self._t5("original", 100, 1, issuer="Other Bank"),
                 self._t5("original", 50, 2, currency="USD"),
                 self._t5("amended", 80, 3)]
        kept = SA.apply_amendments(slips, [], [])
        self.assertEqual(len(kept), 3)

    def test_two_amended_slips_cannot_be_told(self):
        slips = [self._t5("original", 100, 1), self._t5("amended", 80, 2),
                 self._t5("amended", 90, 3)]
        problems, nc = [], []
        kept = SA.apply_amendments(slips, problems, nc)
        self.assertEqual(len(kept), 3)
        self.assertEqual([x["kind"] for x in nc], ["amended-slip"])
        self.assertIn("cannot be told", nc[0]["text"])


class TestJanuaryPaidTrustReport(unittest.TestCase):
    """TJ-0795: IB's report whose one payment is a December
    distribution paid in January is this year's T3."""

    def test_report_is_compared(self):
        rows = [("Summary", "SYN.UN", "20260102", "20251215", "", 100),
                ("RevenueComponent", "SYN.UN", "20260102", "20251215",
                 "T3: Return of Capital", 40),
                ("RevenueComponent", "SYN.UN", "20260102", "20251215",
                 "T3: Eligible Dividend Income", 60)]
        root = _project([_div("SYN.UN.TO", "2026-01-02", 100.0,
                              record_date="2025-12-15",
                              income_label="distribution",
                              issuer_country="CA")],
                        report=_report(rows))
        rep = SA.audit(root, CFG)
        t3 = [s for g in rep["accounts"][0]["groups"] for s in g["slips"]
              if s["type"] == "T3"]
        self.assertEqual(t3[0]["boxes"], {"ca_div": 60.0, "roc": 40.0})
        self.assertEqual(rep["accounts"][0]["groups"][0]["payments"]
                         ["matched"], 1)

    def test_other_year_report_still_refused(self):
        rows = [("Summary", "SYN", "20260302", "20260215", "", 100),
                ("RevenueComponent", "SYN", "20260302", "20260215",
                 "T5: Eligible Dividend Income", 100)]
        root = _project([_div("SYN.TO", "2025-06-02", 100.0)],
                        report=_report(rows))
        with self.assertRaises(SA.SlipsError) as cm:
            SA.audit(root, CFG)
        self.assertIn("IB's report for 2026, not 2025", str(cm.exception))


class TestReportThatDoesNotAddUp(unittest.TestCase):
    """TJ-0796: a payment whose components disagree with its Summary
    row is never certified clean."""

    def _audit(self, summary, comps):
        rows = [("Summary", "SYN", "20250602", "20250515", "", summary)]
        rows += [("RevenueComponent", "SYN", "20250602", "20250515",
                  "T5: Eligible Dividend Income", a) for a in comps]
        root = _project([_div("SYN.TO", "2025-06-02", 80.0,
                              issuer_country="CA")], report=_report(rows))
        return SA.audit(root, CFG)

    def test_consistent_report_is_ok(self):
        rep = self._audit(80, [80])
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["not_compared"], [])

    def test_components_short_of_summary(self):
        rep = self._audit(100, [80])
        self.assertEqual(rep["status"], "incomplete")
        self.assertEqual([x["kind"] for x in rep["not_compared"]],
                         ["report-mismatch"])
        self.assertIn("SYN 2025-06-02: the components add up to 80.00 CAD, "
                      "the payment to 100.00",
                      rep["not_compared"][0]["text"])
        self.assertIn("download the report again",
                      rep["not_compared"][0]["text"])

    def test_components_above_summary(self):
        rep = self._audit(40, [40, 40])
        self.assertEqual(rep["status"], "incomplete")


class TestReportSpellingThroughTheMap(unittest.TestCase):
    """TJ-0801: IB's spelling of a payment the project's GLOBAL rule
    books under another symbol is matched through the map."""

    def _audit(self, mapped):
        rows = [("Summary", "RAW", "20250602", "20250515", "", 80),
                ("RevenueComponent", "RAW", "20250602", "20250515",
                 "T5: Eligible Dividend Income", 80)]
        sym = "CANON.TO" if mapped else "RAW.TO"
        root = _project([_div(sym, "2025-06-02", 80.0,
                              issuer_country="CA")], report=_report(rows))
        (root / "ticker.map").write_text(
            "GLOBAL RAW.TO CANON.TO\n" if mapped else "", encoding="utf-8")
        return SA.audit(root, CFG)

    def test_mapped_payment_matches(self):
        for mapped in (False, True):
            rep = self._audit(mapped)
            pay = rep["accounts"][0]["groups"][0]["payments"]
            self.assertEqual(pay["matched"], 1, mapped)
            self.assertEqual(pay["missing_from_books"], [], mapped)
            self.assertEqual(pay["missing_from_slip"], [], mapped)
            self.assertEqual(rep["issues"], [], mapped)

    def test_map_roots(self):
        root = Path(private_dir("r7b-map-"))
        (root / "ticker.map").write_text(
            "GLOBAL RAW.TO CANON.TO\nGLOBAL SAME.TO SAME.US\n",
            encoding="utf-8")
        self.assertEqual(SA.map_roots(root), {"RAW": "CANON"})

    def test_own_root_in_books_is_not_renamed(self):
        pay = _pay(symbol="RAW", parts=(("T5", "eligible", "x", 1.0),))

        class Rep:
            payments = [pay]
        SA.map_report_roots(Rep, {"RAW": "CANON"}, {"RAW", "CANON"})
        self.assertEqual(pay.book_root, "")
        SA.map_report_roots(Rep, {"RAW": "CANON"}, {"CANON"})
        self.assertEqual(pay.book_root, "CANON")


class TestTwoEntitlementsOneDay(unittest.TestCase):
    """TJ-0804: two report payments of one security paid the same day
    (two ex-dates) are compared with that day's book rows together."""

    def _match(self, pays, rows):
        issues = []
        out = SA._match_payments("margin", "margin", [_ib(pays)], rows,
                                 2025, issues)
        return out, issues

    def test_two_payments_match_two_rows(self):
        eligible = (("T5", "eligible", "T5: Eligible Dividend Income",
                     40.0),)
        pays = [_pay("SYN", "2025-06-02", "2025-05-15", eligible),
                _pay("SYN", "2025-06-02", "2025-05-28", eligible)]
        rows = [_row("SYN.TO", "2025-06-02", 40.0),
                _row("SYN.TO", "2025-06-02", 40.0, source="other.tt")]
        out, issues = self._match(pays, rows)
        self.assertEqual(issues, [])
        self.assertEqual(out["matched"], 2)
        self.assertEqual(out["amount_differs"], [])

    def test_one_payment_short_still_differs(self):
        eligible = (("T5", "eligible", "T5: Eligible Dividend Income",
                     40.0),)
        pays = [_pay("SYN", "2025-06-02", "2025-05-15", eligible),
                _pay("SYN", "2025-06-02", "2025-05-28", eligible)]
        rows = [_row("SYN.TO", "2025-06-02", 40.0)]
        out, issues = self._match(pays, rows)
        self.assertEqual([i["kind"] for i in issues], ["amount-differs"])
        self.assertEqual(out["amount_differs"][0]["slip"], 80.0)


class TestTrustPaymentInLieu(unittest.TestCase):
    """TJ-0805: a payment in lieu on a Canadian trust's unit is other
    income (T5 box 14), as dil-sum counts it; a corporation's is a
    dividend (box 24)."""

    def _audit(self, symbol, label, box):
        pil = {"action": "DIVIDEND_IN_LIEU", "symbol": symbol,
               "date": "2025-06-02", "gross_amount": 40.0,
               "net_amount": 40.0, "record_date": "2025-05-15",
               "income_label": label, "dealer_country": "CA",
               "issuer_country": "CA"}
        root = _project([pil], slips_toml=(
            f'year = 2025\n[[slip]]\ntype = "T5"\naccount = "margin"\n'
            f'boxes = {{ {box} = 40 }}\n'))
        return SA.audit(root, CFG)

    @rule("CA-INC-07")
    def test_trust_pil_is_other_income(self):
        rep = self._audit("SYN.UN.TO", "distribution", 14)
        self.assertEqual(rep["issues"], [])
        self.assertEqual(rep["status"], "ok")

    @rule("CA-INC-07")
    def test_corporation_pil_is_a_dividend(self):
        rep = self._audit("SYN.TO", "dividend", 24)
        self.assertEqual(rep["issues"], [])
        self.assertEqual(rep["status"], "ok")


class TestOffsettingFundRoc(unittest.TestCase):
    """TJ-0806: ROC booked on the wrong fund is a finding even when the
    account's total agrees."""

    T3S = ('year = 2025\n'
           '[[slip]]\ntype = "T3"\naccount = "margin"\n'
           'security = "ALFA.UN.TO"\nboxes = { 49 = 80, 42 = 20 }\n'
           '[[slip]]\ntype = "T3"\naccount = "margin"\n'
           'security = "BETA.UN.TO"\nboxes = { 49 = 60, 42 = 40 }\n')

    def _audit(self, alfa, beta):
        def roc(sym, amt):
            return {"action": "ADJUST", "symbol": sym, "date": "2025-06-02",
                    "net_amount": -amt, "type": "roc",
                    "issuer_country": "CA"}
        rows = [_div("ALFA.UN.TO", "2025-06-02", 100.0, issuer_country="CA"),
                _div("BETA.UN.TO", "2025-06-02", 100.0, issuer_country="CA"),
                roc("ALFA.UN.TO", alfa), roc("BETA.UN.TO", beta)]
        root = _project(rows, slips_toml=self.T3S)
        return SA.audit(root, CFG)

    def test_correct_allocation_is_ok(self):
        rep = self._audit(20, 40)
        self.assertEqual([i["kind"] for i in rep["issues"]], [])
        self.assertEqual(rep["status"], "ok")

    def test_swapped_allocation_is_a_finding(self):
        rep = self._audit(40, 20)
        self.assertEqual(rep["status"], "attention")
        self.assertEqual([i["kind"] for i in rep["issues"]],
                         ["security-differs", "security-differs"])
        self.assertIn("ALFA.UN.TO return of capital (T3 42) slip 20.00, "
                      "books 40.00 CAD (+20.00)", rep["issues"][0]["text"])
        text = "\n".join(SA.render(rep, width_=120))
        self.assertIn("Return of capital differs: ALFA.UN.TO slip 20.00, "
                      "books 40.00 CAD", text)

    def test_total_differs_is_one_finding(self):
        rep = self._audit(40, 40)
        kinds = [i["kind"] for i in rep["issues"]]
        self.assertNotIn("security-differs", kinds)
        self.assertIn("differs", kinds)


if __name__ == "__main__":
    unittest.main()
