"""Explicit broker journal pairs between two listings of one security
(tax-logic CA-XLIST-01 / US-XLIST-01): a same-account, same-day,
same-quantity pair whose legs carry the broker's journal wording (RBC's
TFR "TRANSFER TO C$" / "FROM U$" with a J or J~ reference, IB's
InterDepot, Questrade's BRW JOURNAL POSITION) compares the two legs' OWN
names, not every name either listing ever had; a corporate-form word
stated by one name only and a "COM NEW" spelling do not block it. Equal
gambits two days apart pair same day first, a shared broker reference is
a hard pair id, the pairing window counts business days, and a listing
two journals map onto (the base-currency line) is no ambiguity.

Synthetic data only (QZ names, a fund-rename pattern).
"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib.symbol_codes import exact_name
from tax_rules import rule
from tax_rules.dual import cli, projects_both

OLD = "QZOLDBRAND U S DLR CURRENCY ETF UNIT NEW NO PAR DEC 2014"
NEW = "QZNEWBRAND US DLR CURRENCY ETF UNIT CL A"


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


class TestWording(unittest.TestCase):
    def test_journal_wording_and_reference(self):
        w = XL.journal_wording
        self.assertEqual(
            w("rbc_direct", "TFR - " + OLD + " TRANSFER TO C$    J"),
            ("rbc_direct", ""))
        self.assertEqual(
            w("rbc_direct", "TFR - " + NEW + " TRANSFER FROM U$  J~0TFR1Q"),
            ("rbc_direct", "J~0TFR1Q"))
        self.assertEqual(w("ib", "InterDepot (QZN)"), ("ib", ""))
        self.assertEqual(w("questrade", "QZD JOURNAL POSITION TO USD"),
                         ("questrade", ""))
        for b, d in (("rbc_direct", "TFO - " + OLD + " ACCOUNT TRANSFER"),
                     ("rbc_direct", "TFR - " + OLD + " TRANSFER TO C$"),
                     ("ib", "ATON (QZN)"), ("ib", "ACATS (QZN)"),
                     ("questrade", "QZD TRANSFER IN"),
                     ("webull", "InterDepot (QZN)")):
            with self.subTest(b=b, d=d):
                self.assertEqual(w(b, d), ("", ""))

    def test_business_days(self):
        bd = XL.business_days
        # Wednesday to the next Tuesday: Thu, Fri, Mon, Tue.
        self.assertEqual(bd(date(2025, 4, 9), date(2025, 4, 15)), 4)
        self.assertEqual(bd(date(2025, 4, 15), date(2025, 4, 9)), 4)
        self.assertEqual(bd(date(2025, 4, 11), date(2025, 4, 18)), 5)
        self.assertEqual(bd(date(2025, 4, 9), date(2025, 4, 17)), 6)
        self.assertEqual(bd(date(2025, 4, 12), date(2025, 4, 13)), 0)
        self.assertEqual(bd(date(2025, 4, 9), date(2025, 4, 9)), 0)


class TestJournalNames(unittest.TestCase):
    """Item 1: the legs' own names on the journal's date."""

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_fund_rename_gambit_joins(self):
        # The CAD line is also named under the fund's later brand (its
        # designators re-spelled): today's listing-wide test refused.
        names = _names(QZD_US=[OLD], QZD_TO=[OLD, NEW])
        for base in ("CAD", "USD"):
            with self.subTest(base=base):
                r = _run(_gambit("2022-02-15", 1200, OLD), names,
                         base_currency=base)
                self.assertEqual(r["suggested"], [])
                p, = r["joined"]
                self.assertEqual(p.journal, "rbc_direct")
                self.assertEqual(p.names, (OLD, OLD))
                self.assertEqual(p.record()["journal"], "rbc_direct")
        # Without the journal wording the strict listing-wide rule stays.
        r = _run(_gambit("2022-02-15", 1200, OLD, journal=False), names)
        self.assertEqual(r["joined"], [])
        self.assertTrue(r["suggested"][0].reason.startswith(
            "another name of the listings states another share"))

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_one_sided_corporate_form_and_com_new_join(self):
        for a, b in (("QZNATURAL RESOURCES LTD", "QZNATURAL RESOURCES"),
                     ("QZGOLD CORP COM NEW", "QZGOLD CORP"),
                     ("QZGOLD CORP COMMON NEW", "QZGOLD"),
                     ("QZMINES LIMITED", "QZMINES")):
            for x, y in ((a, b), (b, a)):
                with self.subTest(a=x, b=y):
                    legs = [_leg("QZN.US", "2025-04-23", -250, x,
                                 broker="ib"),
                            _leg("QZN.TO", "2025-04-23", 250, y,
                                 broker="ib")]
                    names = _names(QZN_US=[x, y], QZN_TO=[x, y])
                    r = _run(legs, names)
                    self.assertEqual(len(r["joined"]), 1, r)
                    # Not journal-worded (an ATON pair): strict.
                    for g in legs:
                        g.journal = ""
                    r = _run(legs, names)
                    self.assertEqual(r["joined"], [])

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_journal_pairs_still_refuse_another_security(self):
        cases = (
            # another class, another designator
            ("QZCO INC CL A", "QZCO INC CL B"),
            ("QZCO INC", "QZCO INC CL B"),
            ("QZCO INC", "QZCO INC SPONSORED ADR"),
            ("QZCO INC", "QZCO INC PFD SER 2"),
            ("QZWORLD S&P 500 INDEX ETF",
             "QZWORLD S&P 500 INDEX ETF CAD HEDGED"),
            # two corporate forms both stated
            ("QZFIELD INFRASTRUCTURE PARTNERS LP",
             "QZFIELD INFRASTRUCTURE CORP"),
            ("QZCO INC", "QZCO CORP"),
            # more words
            ("QZALPHA BANK", "QZALPHA BANK OF CANADA"),
            # NEW that does not follow a share word
            ("QZCO CORP", "QZCO NEW CORP"))
        for a, b in cases:
            for x, y in ((a, b), (b, a)):
                with self.subTest(a=x, b=y):
                    legs = [_leg("QZN.US", "2025-04-23", -250, x,
                                 broker="ib"),
                            _leg("QZN.TO", "2025-04-23", 250, y,
                                 broker="ib")]
                    r = _run(legs, _names(QZN_US=[x], QZN_TO=[y]))
                    self.assertEqual(r["joined"], [])
                    self.assertTrue(r["suggested"][0].reason.startswith(
                        "the legs' names are not equal word for word"),
                        r["suggested"][0].reason)

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_journal_pairs_never_join_two_companies(self):
        legs = [_leg("QZN.US", "2025-04-23", -250, "QZNATURAL ENERGY INC",
                     broker="ib"),
                _leg("QZN.TO", "2025-04-23", 250, "QZWV MINING LTD",
                     broker="ib")]
        r = _run(legs, _names(QZN_US=["QZNATURAL ENERGY INC"],
                              QZN_TO=["QZWV MINING LTD"]))
        self.assertEqual(r, {"joined": [], "suggested": []})
        # Equal legs, but the listing's other name is another company.
        legs = [_leg("QZN.US", "2025-04-23", -250, "QZNATURAL ENERGY INC",
                     broker="ib"),
                _leg("QZN.TO", "2025-04-23", 250, "QZNATURAL ENERGY INC",
                     broker="ib")]
        r = _run(legs, _names(QZN_US=["QZNATURAL ENERGY INC"],
                              QZN_TO=["QZNATURAL ENERGY INC",
                                      "QZWV MINING LTD"]))
        self.assertEqual(r["joined"], [])
        self.assertTrue(r["suggested"][0].reason.startswith(
            "another name of the listings names another company"))

    @rule("CA-XLIST-01")
    def test_two_brokers_or_two_days_are_not_a_journal_pair(self):
        names = _names(QZD_US=[OLD], QZD_TO=[OLD, NEW])
        for legs in ([_leg("QZD.US", "2022-02-15", -1200, OLD),
                      _leg("QZD.TO", "2022-02-16", 1200, OLD)],
                     [_leg("QZD.US", "2022-02-15", -1200, OLD),
                      _leg("QZD.TO", "2022-02-15", 1200, OLD,
                           account="tfsa")]):
            with self.subTest(legs=legs):
                r = _run(legs, names)
                self.assertEqual(r["joined"], [])
                self.assertEqual(r["suggested"][0].journal, "")


class TestAmbiguity(unittest.TestCase):
    """Item 2: same day first; a broker reference is a hard pair id."""

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_equal_gambits_two_days_apart_pair_same_day(self):
        names = _names(QZD_US=[NEW], QZD_TO=[NEW])
        legs = _gambit("2023-03-07", 2500, NEW) + _gambit("2023-03-09",
                                                          2500, NEW)
        r = _run(legs, names)
        self.assertEqual(r["suggested"], [])
        self.assertEqual(sorted((p.out.date, p.into.date)
                                for p in r["joined"]),
                         [("2023-03-07", "2023-03-07"),
                          ("2023-03-09", "2023-03-09")])
        # Ordinary transfer legs of the same shape stay ambiguous.
        legs = (_gambit("2023-03-07", 2500, NEW, journal=False)
                + _gambit("2023-03-09", 2500, NEW, journal=False))
        r = _run(legs, names)
        self.assertEqual(r["joined"], [])
        self.assertEqual({p.reason for p in r["suggested"]},
                         {"the legs pair with more than one other leg"})

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_a_shared_reference_is_the_pair_id(self):
        names = _names(QZD_US=[NEW], QZD_TO=[NEW])
        # Two equal gambits on ONE day: ambiguous without a reference.
        r = _run(_gambit("2023-03-07", 2500, NEW)
                 + _gambit("2023-03-07", 2500, NEW), names)
        self.assertEqual(r["joined"], [])
        self.assertEqual({p.reason for p in r["suggested"]},
                         {"the legs pair with more than one other leg"})
        # With RBC's J~ reference on each journal's two legs: two pairs.
        r = _run(_gambit("2023-03-07", 2500, NEW, ref="J~0TFR1")
                 + _gambit("2023-03-07", 2500, NEW, ref="J~0TFR2"), names)
        self.assertEqual(r["suggested"], [])
        self.assertEqual(sorted(p.out.ref for p in r["joined"]),
                         ["J~0TFR1", "J~0TFR2"])
        # Legs whose references differ are never one pair.
        r = _run([_leg("QZD.US", "2023-03-07", -2500, NEW, ref="J~0TFR1"),
                  _leg("QZD.TO", "2023-03-07", 2500, NEW, ref="J~0TFR2")],
                 names)
        self.assertEqual(r, {"joined": [], "suggested": []})

    @rule("US-XLIST-01")
    def test_usa_questrade_journal_pair_id_pairs_the_legs(self):
        # A US project reads Questrade's BRW legs as transfer legs: the
        # parser's journal_pair id still says which two legs are one
        # journal; the names decide the join.
        nm = "QZD US DLR CURRENCY ETF UNIT CL A"
        legs = [_leg("QZD.TO", "2025-10-21", -150, nm, account="rrsp",
                     broker="questrade", ref="p1"),
                _leg("QZD.U.TO", "2025-10-21", 150, nm, account="rrsp",
                     broker="questrade", ref="p1"),
                _leg("QZD.TO", "2025-10-21", -150, nm, account="rrsp",
                     broker="questrade", ref="p2"),
                _leg("QZD.U.TO", "2025-10-21", 150, nm, account="rrsp",
                     broker="questrade", ref="p2")]
        for g in legs:
            g.pair = g.ref
        r = _run(legs, _names(QZD_TO=[nm], QZD_U_TO=[nm]),
                 base_currency="USD", currency_journals=False)
        self.assertEqual(r["suggested"], [])
        self.assertEqual(len(r["joined"]), 2)
        self.assertEqual({p.kind for p in r["joined"]}, {"TOBASE"})


class TestWindowAndStar(unittest.TestCase):

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_cross_broker_window_counts_business_days(self):
        nm = "QZPAN SILVER CORP"
        names = _names(QZP_TO=[nm], QZP_US=[nm])
        # Out at one broker on a Wednesday, in at the other the next
        # Tuesday: 6 calendar days, 4 business days.
        legs = [_leg("QZP.TO", "2025-04-09", -450, nm, journal=False),
                _leg("QZP.US", "2025-04-15", 450, nm, broker="ib",
                     journal=False)]
        r = _run(legs, names)
        self.assertEqual(len(r["joined"]), 1)
        # 6 business days apart: no pair.
        legs[1].date = "2025-04-17"
        self.assertEqual(_run(legs, names),
                         {"joined": [], "suggested": []})
        # The same-quantity/unique rule holds over the longer window:
        # another listing's leg in the window makes it a suggestion.
        legs[1].date = "2025-04-15"
        legs.append(_leg("QZQ.US", "2025-04-14", 450, nm, broker="ib",
                         journal=False))
        r = _run(legs, dict(names, **_names(QZQ_US=[nm])))
        self.assertEqual(r["joined"], [])
        self.assertEqual({p.reason for p in r["suggested"]},
                         {"the legs pair with more than one other leg"})
        # A custody move of one symbol over a weekend cancels first.
        legs = [_leg("QZP.TO", "2025-04-09", -450, nm, journal=False),
                _leg("QZP.TO", "2025-04-15", 450, nm, broker="ib",
                     journal=False),
                _leg("QZP.US", "2025-04-15", 450, nm, broker="ib",
                     journal=False)]
        self.assertEqual(_run(legs, names),
                         {"joined": [], "suggested": []})

    @rule("CA-XLIST-01")
    def test_two_usd_lines_journaled_onto_one_cad_line_join(self):
        # The fund's USD line under two symbols over the years (QZD.US,
        # later QZD.U.TO), both journaled onto QZD.TO by RBC.
        names = _names(QZD_US=[OLD], QZD_TO=[OLD, NEW], QZD_U_TO=[NEW])
        legs = (_gambit("2022-02-15", 1200, OLD)
                + _gambit("2024-06-12", 700, NEW, frm="QZD.U.TO",
                          ref="J~0TFR3"))
        r = _run(legs, names)
        self.assertEqual(r["suggested"], [])
        self.assertEqual(sorted((p.frm, p.to) for p in r["joined"]),
                         [("QZD.U.TO", "QZD.TO"), ("QZD.US", "QZD.TO")])
        self.assertEqual(XL.map_lines(r["joined"]), [
            "TOBASE QZD.U.TO QZD.TO  # transfer QZD.U.TO -> QZD.TO "
            "2024-06-12 (margin)",
            "TOBASE QZD.US QZD.TO  # transfer QZD.US -> QZD.TO 2022-02-15 "
            "(margin)"])
        # Ordinary transfers in that shape stay ambiguous.
        for g in legs:
            g.journal = g.ref = ""
        r = _run(legs, _names(QZD_US=[NEW], QZD_TO=[NEW],
                              QZD_U_TO=[NEW]))
        self.assertEqual(r["joined"], [])
        self.assertEqual({p.reason for p in r["suggested"]},
                         {"a listing pairs with two other listings"})

    @rule("CA-XLIST-03")
    def test_canada_currency_journal_and_gambit_share_the_cad_line(self):
        # Questrade's parser-paired journal (JOURNAL line) onto QZD.TO
        # and RBC's gambit from QZD.US onto QZD.TO: both joined.
        nm = "QZD US DLR CURRENCY ETF UNIT CL A"
        qt = [_leg("QZD.U.TO", "2025-10-21", -150, nm, account="tfsa",
                   broker="questrade"),
              _leg("QZD.TO", "2025-10-21", 150, nm, account="tfsa",
                   broker="questrade")]
        qt[0].currency, qt[1].currency = "USD", "CAD"
        for g in qt:
            g.pair = g.ref = "p1"
        r = _run(qt + _gambit("2022-02-15", 1200, OLD),
                 _names(QZD_US=[OLD], QZD_TO=[OLD, nm], QZD_U_TO=[nm]),
                 currency_journals=True)
        self.assertEqual(r["suggested"], [])
        self.assertEqual(sorted((p.kind, p.frm, p.to) for p in r["joined"]),
                         [("JOURNAL", "QZD.U.TO", "QZD.TO"),
                          ("TOBASE", "QZD.US", "QZD.TO")])

    @rule("CA-XLIST-01")
    @rule("US-XLIST-01")
    def test_a_move_between_listings_a_journal_joined_is_joined(self):
        # RBC sends QZN.TO ("... LTD"), IB receives it as QZN.US (IB's
        # name has no LTD); IB later journals QZN.US -> QZN.TO.
        a, b = "QZNATURAL RESOURCES LTD", "QZNATURAL RESOURCES"
        names = _names(QZN_TO=[a, b], QZN_US=[a, b])
        move = [_leg("QZN.TO", "2025-04-09", -250, a, journal=False),
                _leg("QZN.US", "2025-04-15", 250, b, broker="ib",
                     journal=False)]
        flip = [_leg("QZN.US", "2025-04-23", -250, b, broker="ib"),
                _leg("QZN.TO", "2025-04-23", 250, b, broker="ib")]
        r = _run(move, names)
        self.assertEqual(r["joined"], [])           # strict on its own
        r = _run(move + flip, names)
        self.assertEqual(r["suggested"], [])
        self.assertEqual(len(r["joined"]), 2)
        via = [p for p in r["joined"] if not p.journal]
        self.assertEqual(via[0].record()["via"], "journal")
        head, det = XL.joined_note("margin", r["joined"])
        self.assertIn("a transfer between two listings a broker journal "
                      "joined", " ".join(det))
        self.assertIn("the broker's journal moved the units between the "
                      "two listings", " ".join(det))
        # A move whose names state another class is never folded in.
        move[1].name = exact_name(b + " CL B")
        move[1].raw_name = b + " CL B"
        r = _run(move + flip, dict(names, **{"QZN.US": names["QZN.US"]
                                            | {exact_name(b + " CL B")}}))
        self.assertEqual(len(r["joined"]), 1)
        self.assertEqual(len(r["suggested"]), 1)


# ------------------------------------------------------------ end to end

def _rbc(gambits):
    """An RBC export: the fund bought in USD under its former brand,
    journaled to the CAD line (TFR legs), the CAD line later traded
    under the new brand."""
    from test_fix_rbc import HDR, row
    body = ('"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
            + HDR
            + row("March 11, 2025", "Buy", "QZD", OLD, "800", "10", "-8000",
                  "USD", OLD + " UNSOLICITED DA"))
    for day, qty, ref in gambits:
        j = "J~" + ref if ref else "J"
        body += (row(day, "Transfers", "QZD", OLD, f"-{qty}", "", "0", "USD",
                     f"TFR - {OLD} TRANSFER TO C$  {j}")
                 + row(day, "Transfers", "QZD", OLD, str(qty), "", "0", "CAD",
                       f"TFR - {OLD} TRANSFER FROM U$  {j}"))
    body += row("June 18, 2025", "Sell", "QZD", NEW, "-100", "14", "1400",
                "CAD", NEW + " UNSOLICITED CA JNL")
    return body


def _ib_flip():
    """An IB statement: QZN bought on its NYSE line, journaled to the TSX
    line (InterDepot) — IB names the instrument without its corporate
    form; an RBC export in the same account names it with LTD (the
    listing-wide test refused: another name states another corporate
    form)."""
    from test_fix_ibparse import HEAD, TRADES_H, XFER_H, FII_H
    from test_fix_rbc import HDR, row
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


class TestRun(unittest.TestCase):

    def _state(self, country, files):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(Path(td), files=files)[country]
            r = cli(root, "run", "--no-input")
            out = " ".join((r.stdout + r.stderr).split())
            st = XL.read_state(root / "work" / XL.STATE)
            eff = root / "work" / XL.EFFECTIVE_MAP
            return out, st, (eff.read_text() if eff.exists() else "")

    def _check_rbc(self, country):
        out, st, eff = self._state(country, {"inputs/margin/rbc.csv": _rbc(
            [("May 12, 2025", 300, ""), ("May 14, 2025", 300, ""),
             ("May 16, 2025", 200, "0TFR1Q")])})
        self.assertEqual(st["suggested"], [], st)
        self.assertEqual(len(st["joined"]), 3, st)
        self.assertEqual({r["journal"] for r in st["joined"]},
                         {"rbc_direct"})
        frm, to = (("QZD.US", "QZD.TO") if country == "canada"
                   else ("QZD.TO", "QZD.US"))
        self.assertIn(f"TOBASE {frm} {to}", eff)
        self.assertIn("joined as one security by their transfer journal",
                      out)
        self.assertIn("the broker's journal moved the units between the "
                      "two listings", out)

    @rule("CA-XLIST-01")
    def test_canada_rbc_gambits_with_a_fund_rename_join(self):
        self._check_rbc("canada")

    @rule("US-XLIST-01")
    def test_usa_rbc_gambits_with_a_fund_rename_join(self):
        self._check_rbc("usa")

    def _check_ib(self, country):
        out, st, eff = self._state(country, _ib_flip())
        self.assertEqual(st["suggested"], [], st)
        self.assertEqual([(r["out"]["symbol"], r["in"]["symbol"],
                           r.get("journal")) for r in st["joined"]],
                         [("QZN.US", "QZN.TO", "ib")])
        self.assertIn("both legs naming one security ('QZNATURAL "
                      "RESOURCES')", out)

    @rule("CA-XLIST-01")
    def test_canada_ib_interdepot_with_one_sided_form_joins(self):
        self._check_ib("canada")

    @rule("US-XLIST-01")
    def test_usa_ib_interdepot_with_one_sided_form_joins(self):
        self._check_ib("usa")


if __name__ == "__main__":
    unittest.main()
