"""Shared test helpers: an RBC Norbert's gambit export: bought on the CAD line, journalled to the USD line, the legs dated the settlement day.

Shared from: test_fix_dated_events
"""


def _rbc_gambit(both_legs=True):
    """RBC: bought on the CAD line, sold on the USD line on one day, the
    journal's TFR legs dated the settlement day (J~ reference)."""
    from _support.rbc_rows import HDR, row
    nm = "QZD US DLR CURRENCY ETF UNIT"
    desc = nm + " UNSOLICITED CA JNL"
    body = ('"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n' + HDR)
    if both_legs:
        body += row("May 6, 2025", "Transfers", "QZD", nm, "1000", "", "0",
                    "USD", "TFR - " + nm + " TRANSFER FROM C$  J~1")
    body += (row("May 6, 2025", "Transfers", "QZD", nm, "-1000", "", "0",
                 "CAD", "TFR - " + nm + " TRANSFER TO U$  J~1")
             + row("May 5, 2025", "Buy", "QZD", nm, "1000", "13.80",
                   "-13800", "CAD", desc, settle="May 6, 2025")
             + row("May 5, 2025", "Sell", "QZD", nm, "-1000", "10.10",
                   "10100", "USD", desc, settle="May 6, 2025"))
    return body, f"EXTRACT {nm} | USD | QZD.U.TO\n"
