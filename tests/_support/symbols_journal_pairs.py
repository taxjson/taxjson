"""Shared test helpers: broker journal legs between two listings.

Shared from: test_fix_journal_pairing
"""
from taxjson.lib import cross_listings as XL
from taxjson.lib.symbol_codes import exact_name


def _leg(sym, day, qty, name, *, account="margin", broker="rbc_direct",
         journal=True, ref=""):
    return XL.Leg(account, broker, sym, day, qty, name=exact_name(name),
                  raw_name=name, journal=broker if journal else "",
                  ref=ref)


def _names(**kw):
    """{symbol: {exact_name keys}} from SYM_TO=[names...]."""
    return {s.replace("_", "."): {exact_name(n) for n in ns}
            for s, ns in kw.items()}


def _run(legs, names, **kw):
    for g in legs:
        g.used = False              # analyze marks the legs it pairs
    shown = {t: " ".join(t) for v in names.values() for t in v}
    kw.setdefault("base_currency", "CAD")
    return XL.analyze(legs, names, shown, **kw)


def _gambit(day, qty, name, ref="", journal=True, frm="QZD.US",
            to="QZD.TO"):
    return [_leg(frm, day, -qty, name, ref=ref, journal=journal),
            _leg(to, day, qty, name, ref=ref, journal=journal)]


def _ib_flip():
    """An IB statement: QZN bought on its NYSE line, journaled to the TSX
    line (InterDepot) — IB names the instrument without its corporate
    form; an RBC export in the same account names it with LTD (the
    listing-wide test refused: another name states another corporate
    form)."""
    from _support.ib_statement_rows import HEAD, TRADES_H, XFER_H, FII_H
    from _support.rbc_rows import HDR, row
    fii = ('Financial Instrument Information,Data,Stocks,QZN,'
           '"QZNATURAL RESOURCES",990000301,CA9990003011,,NYSE,1,,,'
           'COMMON,,\n')
    xf = ('Transfers,Data,Stocks,USD,QZN,2025-04-23,InterDepot,Out,'
          'IB,U5550001,-250,0,-5000,0,0,\n'  # pii-ok
          'Transfers,Data,Stocks,CAD,QZN,2025-04-23,InterDepot,In,'
          'IB,U5550001,250,0,6800,0,0,\n')  # pii-ok
    ib = (HEAD + 'Statement,Data,Period,"January 1, 2025 - December '
          '31, 2025"\n' + TRADES_H
          + 'Trades,Data,Order,Stocks,USD,U5550001,QZN,'  # pii-ok
            '"2025-03-12, 10:00:00",250,20,0,-5000,0,0,0,0,O\n'
          + XFER_H + xf + FII_H + fii)
    nm = "QZNATURAL RESOURCES LTD"
    rbc = ('"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
           + HDR
           + row("February 3, 2025", "Buy", "QZN", nm, "10", "20", "-200",
                 "USD", nm + " UNSOLICITED DA")
           + row("February 4, 2025", "Sell", "QZN", nm, "-10", "21", "210",
                 "USD", nm + " UNSOLICITED DA")
           + row("February 5, 2025", "Buy", "QZN", nm, "10", "27", "-270",
                 "CAD", nm + " UNSOLICITED DA")
           + row("February 6, 2025", "Sell", "QZN", nm, "-10", "28", "280",
                 "CAD", nm + " UNSOLICITED DA"))
    return {"inputs/margin/ib.csv": ib, "inputs/margin/rbc.csv": rbc}
