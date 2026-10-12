"""Trade-confirmation wording is not part of a security's name
(lib/symbol_codes: questrade_name, rbc_name; tax-logic CA-XLIST-01 /
US-XLIST-01, CA-ACB-CODES / US-BASIS-CODES).

A Canadian dealer's trade row writes its confirmation wording after the
security's name: "<NAME> UNSOLICITED WE ACTED AS PRINCIPAL AVG PRICE
SHOWN-DETAILS ON REQ DA" (RBC), "<NAME> WE ACTED AS AGENT" (Questrade).
The cross-listing auto-join compares EXACT names, and "WE ACTED AS
PRINCIPAL" left the word AS — a corporate form (A/S) — in the name, so a
real pair of listings was only suggested. The wording is cut from the
name (a share designator in the cut part is kept); another class is
still refused.

Synthetic data only: invented QZ* names and tickers, fake ids.
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib.symbol_codes import (_row_name, exact_name, name_tokens,
                                      questrade_name, rbc_name)
from tax_rules import rule
from tax_rules.dual import cli, projects_both

IB_NAME = "QZSILVER CORP"
IB_CLASS_A = "QZGOLD INC-CLASS A"
IB_CLASS_B = "QZGOLD INC-CLASS B"

# The shapes of real rows (the wording is the dealers'; the names are not).
QT_TRADES = (
    "QZSILVER CORP COMMON SHARES WE ACTED AS AGENT",
    "QZSILVER CORP COMMON SHARES WE ACTED AS PRINCIPAL",
    "QZSILVER CORP COMMON SHARES UNSOLICITED WE ACTED AS PRINCIPAL",
    "QZSILVER CORP COMMON SHARES SOLICITED WE ACTED AS AGENT",
    "QZSILVER CORP COMMON SHARES WE ACTED AS AGENT AVG PRICE - ASK US "
    "FOR DETAILS AS OF 07/01/26",
)
RBC_TRADES = (
    "QZSILVER CORP COMMON SHARES UNSOLICITED WE ACTED AS PRINCIPAL AVG "
    "PRICE SHOWN-DETAILS ON REQ DA",
    "QZSILVER CORP COMMON SHARES UNSOLICITED AVG PRICE SHOWN-DETAILS ON "
    "REQ WE ACTED AS PRINCIPAL CA JNL",
    "QZSILVER CORP COMMON SHARES UNSOLICITED DA",
    "QZSILVER CORP COMMON SHARES DA",
    "QZSILVER CORP COMMON SHARES CA AS OF 03/04/25",
    "QZSILVER CORP COMMON SHARES SHORT. UNSOLICITED AVG PRICE "
    "SHOWN-DETAILS ON REQ DA",
    "QZSILVER CORP COMMON SHARES UNSOLICITED ISSUER CONNECTED TO QZ "
    "DEALER SECURITIES INC.\" DA\"",
    "QZSILVER CORP COMMON SHARES WITH DUE-BILL SPLIT UNSOLICITED CA",
    "QZSILVER CORP COMMON SHARES ASSIGNMENT OF OPTION AS OF 03/04/25",
)
RBC_OTHER = (
    "DIV - QZSILVER CORP COMMON SHARES CASH DIV  ON     500 SHS REC "
    "05/20/25 PAY 06/02/25",
    "DIV - QZSILVER CORP COMMON SHARES CASH DIV ON 500 SHS REC 05/20/25 "
    "PAY 06/02/25 NON-RES TAX WITHHELD",
    "QZSILVER CORP COMMON SHARES DIST ON 400 SHS REC 05/20/25 PAY "
    "06/02/25",
    "TFO - QZSILVER CORP COMMON SHARES ACCOUNT TRANSFER BOOK VALUE    "
    "      900.42 TO ACCOUNT 555-55501-13",  # pii-ok
    "TFI - QZSILVER CORP COMMON SHARES ACCOUNT TRANSFER BOOK VALUE    "
    "      900.42 FROM ACCOUNT 555-55502-21",  # pii-ok
    "QZSILVER CORP COMMON SHARES TRANSFER FROM U$ J",
    "QZSILVER CORP COMMON SHARES REINV@C$12.34 REC 05/20/25 PAY 06/02/25",
)


class TestQuestradeName(unittest.TestCase):
    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_confirmation_wording_is_cut(self):
        for d in QT_TRADES:
            with self.subTest(desc=d):
                self.assertEqual(exact_name(questrade_name(d)),
                                 exact_name(IB_NAME))

    @rule("CA-XLIST-01")
    def test_designator_in_the_cut_part_is_kept(self):
        for d in ("QZGOLD INC COMMON STOCK CLASS A UNSOLICITED WE ACTED AS "
                  "PRINCIPAL",
                  "QZGOLD INC COM CL A UNSOLICITED WE ACTED AS PRINCIPAL",
                  "QZGOLD INC UNSOLICITED CLASS A WE ACTED AS PRINCIPAL"):
            with self.subTest(desc=d):
                n = exact_name(questrade_name(d))
                self.assertEqual(n, exact_name(IB_CLASS_A))
                self.assertNotEqual(n, exact_name(IB_CLASS_B))
                self.assertNotEqual(n, exact_name("QZGOLD INC"))

    def test_a_name_with_the_words_inside_it_is_left_alone(self):
        # Only the wording AFTER a name is cut, never the name's first
        # word, and AS alone (A/S) is still a corporate form.
        self.assertEqual(questrade_name("QZNORD AS"), "QZNORD AS")
        self.assertEqual(questrade_name("SOLICITED QZ INC"),
                         "SOLICITED QZ INC")


class TestRbcName(unittest.TestCase):
    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_trade_wording_is_cut(self):
        for d in RBC_TRADES:
            with self.subTest(desc=d):
                self.assertEqual(exact_name(rbc_name(d, trade=True)),
                                 exact_name(IB_NAME))

    @rule("CA-XLIST-01")
    def test_event_code_and_wording_are_cut(self):
        for d in RBC_OTHER:
            with self.subTest(desc=d):
                n = rbc_name(d)
                self.assertEqual(exact_name(n), exact_name(IB_NAME))
                # No other account's number in the name a warning shows.
                self.assertNotIn("555", n)

    @rule("CA-XLIST-01")
    def test_designator_kept_and_another_class_refused(self):
        for d in ("QZGOLD INC COM CL A UNSOLICITED AVG PRICE SHOWN-DETAILS "
                  "ON REQ WE ACTED AS PRINCIPAL DA",
                  "QZGOLD INC COM UNSOLICITED CL A DA",
                  "DIV - QZGOLD INC COM CL A CASH DIV ON 300 SHS REC "
                  "06/12/25 PAY 06/26/25"):
            with self.subTest(desc=d):
                n = exact_name(rbc_name(d, trade=True))
                self.assertEqual(n, exact_name(IB_CLASS_A))
                self.assertNotEqual(n, exact_name(IB_CLASS_B))

    @rule("CA-XLIST-01")
    def test_a_journal_row_keeps_its_designators(self):
        # A currency journal's row (RBC's shape): the UNIT and NEW
        # designators before the wording stay; the wording, the desk
        # code and the JNL marker go.
        d = ("QZ U S DLR CURRENCY ETF UNIT NEW NO PAR DEC 2014 UNSOLICITED "
             "WE ACTED AS PRINCIPAL AVG PRICE SHOWN-DETAILS ON REQ CA JNL")
        self.assertEqual(rbc_name(d, trade=True),
                         "QZ U S DLR CURRENCY ETF UNIT NEW NO PAR DEC 2014")
        self.assertEqual(rbc_name("QZ ETF UNIT NEW WE ACTED AS PRINCIPAL "
                                  "CA JNL", trade=True), "QZ ETF UNIT NEW")
        self.assertNotEqual(exact_name(rbc_name(d, trade=True)),
                            exact_name("QZ U S DLR CURRENCY ETF NO PAR DEC "
                                       "2014"))

    def test_a_desk_code_ends_only_a_trade(self):
        # CA / DA close RBC's trade rows; on another row they stay.
        self.assertEqual(rbc_name("QZ HOLDINGS CA"), "QZ HOLDINGS CA")
        self.assertEqual(rbc_name("QZ HOLDINGS CA", trade=True),
                         "QZ HOLDINGS")

    def test_row_name_reads_rbc_rows_through_rbc_name(self):
        t = {"action": "BUYSELL", "description": RBC_TRADES[0]}
        self.assertEqual(exact_name(_row_name(t, "rbc_direct")),
                         exact_name(IB_NAME))
        self.assertEqual(name_tokens(_row_name(t, "rbc_direct")),
                         name_tokens(IB_NAME))
        # Another broker's description is its name, as before.
        self.assertEqual(_row_name(t, "webull"), RBC_TRADES[0])


def _tx(sym, date, action, qty, desc="", **kw):
    t = {"symbol": sym, "date": date, "action": action, "quantity": qty,
         "description": desc, "currency": "CAD"}
    t.update(kw)
    return t


class TestGatherJoins(unittest.TestCase):
    """The parsed exports in work/: the RBC listing traded with
    confirmation wording, journaled out; the IB listing journaled in."""

    def _run(self, rbc_trade_desc, ib_name=IB_CLASS_A):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            rbc = [_tx("QZG.TO", "2025-02-03", "BUYSELL", 250,
                       rbc_trade_desc),
                   _tx("QZG.TO", "2025-03-03", "TRANSFER", -250,
                       "TFO - QZGOLD INC COM CL A ACCOUNT TRANSFER BOOK "
                       "VALUE 900.00 TO ACCOUNT 555-55501-13")]  # pii-ok
            ib = [_tx("QZG.US", "2025-03-04", "TRANSFER", 250, "QZG",
                      security_name=ib_name),
                  _tx("QZG.US", "2025-05-04", "BUYSELL", -250, "QZG",
                      security_name=ib_name)]
            for b, txs in (("rbc_direct", rbc), ("ib", ib)):
                (cache / f"margin_{b}.json").write_text(json.dumps(
                    {"metadata": {}, "transactions": txs}))
            legs, names, shown = XL.gather(cache, ["margin"])
            return XL.analyze(legs, names, shown, base_currency="CAD")

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_principal_wording_joins_exactly(self):
        r = self._run("QZGOLD INC COM CL A UNSOLICITED AVG PRICE "
                      "SHOWN-DETAILS ON REQ WE ACTED AS PRINCIPAL DA")
        self.assertEqual([(p.frm, p.to) for p in r["joined"]],
                         [("QZG.US", "QZG.TO")], r)
        for p in r["joined"]:
            self.assertNotIn("555", " ".join(p.names))

    @rule("CA-XLIST-01")
    def test_another_class_is_still_refused(self):
        r = self._run("QZGOLD INC COM CL A UNSOLICITED WE ACTED AS "
                      "PRINCIPAL DA", ib_name=IB_CLASS_B)
        self.assertEqual(r["joined"], [])
        self.assertEqual(len(r["suggested"]), 1)


_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qt(date, action, sym, desc, qty, price, net, cur, act="Trades"):
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},55500001,{act},Individual\n")  # pii-ok


def _journal(cur):
    """Bought as SAMPQ with principal wording, journaled to the SAMPR
    line, sold as SAMPR."""
    return (_QT
            + _qt("2025-01-10", "Buy", "SAMPQ", "SAMPQ ENERGY INC "
                  "UNSOLICITED WE ACTED AS PRINCIPAL", 100, 30.0, -3000.0,
                  cur)
            + _qt("2025-03-03", "TF6", "SAMPQ", "SAMPQ ENERGY INC TRANSFER",
                  -100, 0.0, 0.0, cur, act="Transfers")
            + _qt("2025-03-04", "TF6", "SAMPR", "SAMPQ ENERGY INC TRANSFER",
                  100, 0.0, 0.0, cur, act="Transfers")
            + _qt("2025-06-10", "Sell", "SAMPR", "SAMPQ ENERGY INC WE ACTED "
                  "AS PRINCIPAL", -100, 45.0, 4500.0, cur))


class TestRun(unittest.TestCase):
    def _check(self, country):
        with tempfile.TemporaryDirectory() as tmp:
            cur = "CAD" if country == "canada" else "USD"
            root = projects_both(
                Path(tmp) / country,
                files={"inputs/margin/questrade.csv": _journal(cur)},
                canada={"source_currencies": []},
                usa={"source_currencies": []})[country]
            r = cli(root, "run", "--no-input")
            out = " ".join((r.stdout + r.stderr).split())
            self.assertEqual(r.returncode, 0, out)
            self.assertEqual(out.count("joined as one security"), 1, out)
            state = XL.read_state(root / "work" / XL.STATE)
            self.assertEqual(len(state["joined"]), 1, state)
            doc = json.loads((root / "work" / "margin_gains_wash.json")
                             .read_text())
            self.assertAlmostEqual(doc["summary"]["total_gain"], 1500.0,
                                   places=2)

    @rule("CA-XLIST-01")
    def test_canada_principal_wording_pair_is_joined(self):
        self._check("canada")

    @rule("US-XLIST-01")
    def test_usa_principal_wording_pair_is_joined(self):
        self._check("usa")


if __name__ == "__main__":
    unittest.main()
