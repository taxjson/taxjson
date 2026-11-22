"""Shared test helpers: IB dividends-report payments, book rows, a project and slip tables for checking slips against the books.

Shared from: test_fix_1029_r7b_slipaudit
"""
import csv
import io
import json
from pathlib import Path

from _tmpfiles import private_dir
from taxjson.lib import slip_audit as SA
from taxjson.lib.ib_dividends import Component, Payment


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
