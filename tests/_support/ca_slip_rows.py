"""Shared test helpers: the slips' rows.

Shared from: test_fix_1029_r7c_slips
"""
import json
from pathlib import Path

from _tmpfiles import private_dir
from taxjson.lib import slip_audit as SA


CFG = {"settings": {"year": 2025, "country": "canada",
                    "base_currency": "CAD"},
       "accounts": {"margin": {"type": "taxable"}}}


def _project(book_rows, slips_toml="year = 2025\n"):
    """A project folder with work/margin_base.json holding `book_rows`
    (transaction dicts) and inputs/slips/slips.toml."""
    root = Path(private_dir("r7c-slip-"))
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
    (sd / "slips.toml").write_text(slips_toml, encoding="utf-8")
    return root


def _div(symbol, day, amt, **kw):
    t = {"action": "DIVIDEND", "symbol": symbol, "date": day,
         "gross_amount": amt, "net_amount": amt}
    t.update(kw)
    return t


def _interest(day, amt, **kw):
    t = {"action": "INTEREST", "symbol": "", "date": day,
         "gross_amount": amt, "net_amount": amt}
    t.update(kw)
    return t


def _t5(boxes, extra=""):
    return ('year = 2025\n[[slip]]\ntype = "T5"\naccount = "margin"\n'
            'issuer = "Synthetic Broker"\n' + extra + "boxes = { "
            + ", ".join(f'"{k}" = {v}' for k, v in boxes.items()) + " }\n")


def _line(rep, cat):
    for a in rep["accounts"]:
        for g in a["groups"]:
            for b in g["buckets"]:
                for ln in b["lines"]:
                    if ln["category"] == cat:
                        return ln
    return None


_CRA_T5 = """  2025 T5 slip (original) from SYNTHETIC CARRYING BROKER

    Box                   Box name                               Box value
    number
    24                    Actual amount of eligible dividends         100.00
    27                    Foreign currency                               CAD
{acct}"""


def _cra_t5(shown, acct_no=None):
    from taxjson.lib.cra_slips import parse_text
    row = (f"    29                    Recipient account                "
           f"      {acct_no}\n" if acct_no else "")
    return parse_text(_CRA_T5.format(acct=row), shown)


def _brow(h, sym, amt, cat="ca_div", src="a.csv", desc=""):
    return SA.BookRow(account="margin", id=f"{h}{sym}{amt}{cat}",
                      action="INTEREST" if cat == "interest" else "DIVIDEND",
                      symbol=sym, root=SA._root(sym) if sym else "",
                      currency="CAD", native=amt, cad=amt,
                      pay_date="2025-03-03", tax_date="2025-03-03",
                      source=src, source_hash=h, category=cat, hashes=(h,),
                      description=desc)


QT = "QUESTRADE, INC."
