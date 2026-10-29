"""Broker parser and taxjson-fetch fixes of 2026-10-29, round 3 (group
parsers). Every fixture is SYNTHETIC: fake broker ids and invented
tickers.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.country import plans_taxed_as_ordinary
from tax_rules import rule

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
ACCT = '55500001'   # pii-ok (synthetic)
ACCT2 = '55500002'  # pii-ok (synthetic)


def flat(text):
    """Whitespace-collapsed: where a message wraps depends on the width."""
    return " ".join(text.split())


def qbuy(acct, atype, td='2025-01-15', sym='QZEQ', cur='USD'):
    """One Questrade Buy row (dates as YYYY-MM-DD)."""
    dt = f"{td} 12:00:00 AM"
    return ','.join([dt, dt, 'Buy', sym, 'QZEQ INC WE ACTED AS AGENT', '10',
                     '30', '-300', '0', '-300', cur, acct, 'Trades',
                     atype]) + '\n'


def qt_parse(files, taxable=None, combined=False, country=None):
    """Parse {name: body} as ONE account, the way taxjson-brokerage does
    (one shared context, one parser per file; the plans the country
    taxes from lib/country, as taxjson-brokerage sets them)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, body in sorted(files.items()):
            p = Path(d) / name
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs = []
        with contextlib.redirect_stderr(err):
            ctx = QuestradeBrokerage.prepare_files(paths)
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                par.country = country
                par.plans_taxed = plans_taxed_as_ordinary(country)
                par.combined_broker_accounts = combined
                if taxable is not None:
                    par.account_taxable = taxable
                txs.extend(par.parse_file(p))
    return txs, err.getvalue()


def _attention(err):
    return [ln for ln in err.splitlines() if 'ATTENTION' in ln]


# --------------------------------------------------------------------------
# TJ-0613: a US project's Questrade TFSA beside its margin account
# --------------------------------------------------------------------------

class TestUsTfsaIsTaxable(unittest.TestCase):
    """TJ-0613: a US return taxes a TFSA (RESP, RDSP, FHSA) like an
    ordinary account (no treaty deferral): in a US project its Questrade
    export may share the taxable folder; an RRSP's still may not."""

    FILES = {'margin.csv': qbuy(ACCT, 'Individual margin'),
             'tfsa.csv': qbuy(ACCT2, 'Individual TFSA', '2025-02-18')}

    @rule("US-BASIS-09")
    def test_tfsa_beside_margin_is_booked_in_a_us_project(self):
        txs, err = qt_parse(self.FILES, taxable=True, combined=True,
                            country='usa')
        self.assertEqual(len(txs), 2)
        self.assertEqual(_attention(err), [])
        self.assertIn("booked together", err)
        # Without combined_broker_accounts: the two-accounts ATTENTION,
        # naming a retirement plan (not a TFSA) as the one to keep apart.
        txs, err = qt_parse(self.FILES, taxable=True, country='usa')
        self.assertEqual(len(txs), 2)
        att = _attention(err)
        self.assertEqual(len(att), 1, err)
        self.assertIn("retirement plan (RRSP/RRIF)", att[0])
        self.assertNotIn("TFSA/RRSP", att[0])
        # A TFSA export alone in a taxable folder: nothing to say.
        _txs, err = qt_parse({'tfsa.csv': self.FILES['tfsa.csv']},
                             taxable=True, country='usa')
        self.assertEqual(_attention(err), [])

    @rule("US-BASIS-09")
    def test_rrsp_beside_margin_is_still_refused_in_a_us_project(self):
        files = {'margin.csv': qbuy(ACCT, 'Individual margin'),
                 'rrsp.csv': qbuy(ACCT2, 'Individual RRSP', '2025-02-18')}
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(files, taxable=True, combined=True, country='usa')
        self.assertIn("is a registered plan", str(cm.exception))
        self.assertNotIn(ACCT2, str(cm.exception))

    @rule("US-BASIS-09")
    def test_tfsa_in_a_sheltered_folder_is_said_in_a_us_project(self):
        txs, err = qt_parse({'tfsa.csv': self.FILES['tfsa.csv']},
                            taxable=False, country='usa')
        self.assertEqual(len(txs), 1)                 # booked as declared
        att = _attention(err)
        self.assertEqual(len(att), 1, err)
        self.assertIn("a taxable account (Individual TFSA)", att[0])
        self.assertIn("a US return taxes a TFSA like an ordinary account",
                      att[0])
        self.assertIn('type = "taxable"', att[0])
        # A TFSA and an RRSP in one sheltered folder: the TFSA is refused.
        files = {'rrsp.csv': qbuy(ACCT, 'Individual RRSP'),
                 'tfsa.csv': self.FILES['tfsa.csv']}
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(files, taxable=False, combined=True, country='usa')
        self.assertIn("is a taxable account (a US return taxes a TFSA",
                      str(cm.exception))

    @rule("US-BASIS-09")
    def test_us_project_run_books_the_tfsa_with_the_margin_account(self):
        import json
        from _qa_project import project, tj
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, "p", {
                "margin/margin.csv": QH + self.FILES['margin.csv'],
                "margin/tfsa.csv": QH + self.FILES['tfsa.csv']},
                country="usa")
            t = root / "taxjson.toml"
            t.write_text(t.read_text().replace("year = 2024", "year = 2025")
                         + "combined_broker_accounts = true\n")
            r = tj(root, "run", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            base = json.loads((root / "work" / "margin_base.json")
                              .read_text())["transactions"]
            self.assertEqual(sorted((x['source'], x['quantity'])
                                    for x in base
                                    if x['action'] == 'BUYSELL'),
                             [('margin.csv', 10.0), ('tfsa.csv', 10.0)])


class TestRegisteredPlansStayShelteredWithoutUs(unittest.TestCase):
    """TJ-0613: the guard of TJ-0527 / TJ-0477 holds where every plan is
    a shelter: no country given (and a Canadian project: the same empty
    set, `plans_taxed_as_ordinary`)."""

    def test_tfsa_beside_margin_is_refused_without_a_country(self):
        files = {'margin.csv': qbuy(ACCT, 'Individual margin'),
                 'tfsa.csv': qbuy(ACCT2, 'Individual TFSA', '2025-02-18')}
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(files, taxable=True, combined=True)
        self.assertIn("is a registered plan", str(cm.exception))
        _txs, err = qt_parse({'m.csv': files['margin.csv'],
                              'j.csv': qbuy(ACCT2, 'Joint margin')},
                             taxable=True)
        self.assertIn("registered plan (TFSA/RRSP)", _attention(err)[0])

    def test_the_table(self):
        self.assertEqual(plans_taxed_as_ordinary(None), frozenset())
        self.assertEqual(plans_taxed_as_ordinary('canada'), frozenset())
        self.assertEqual(plans_taxed_as_ordinary('usa'),
                         frozenset({'tfsa', 'resp', 'rdsp', 'fhsa'}))


# --------------------------------------------------------------------------
# TJ-0608: redact reads 'Name,Jane,Sample' as an identity line
# --------------------------------------------------------------------------

class TestRedactIdentityLineIsNoHeader(unittest.TestCase):
    """TJ-0608: a label word, then a name split over cells, passed the
    flat-header fallback (three digit-free cells): the name was kept,
    and the preamble ended there."""

    TABLE = 'Date,Symbol,Amount\n2025-01-03,SYN.TO,10\n'

    def _redact(self, text):
        from taxjson.bin.taxjson_redact import redact_text
        out, rep = redact_text(text)
        return out, rep

    def test_label_then_name_cells_is_redacted(self):
        out, rep = self._redact('Name,Jane,Sample\n' + self.TABLE)
        self.assertEqual(out, 'Name,REDACTED,REDACTED\n' + self.TABLE)
        self.assertEqual(rep.identity_rows, 1)
        out, _rep = self._redact('"Client","Jane","Q.","Sample"\n'
                                 + self.TABLE)
        self.assertEqual(out.splitlines()[0],
                         '"Client","REDACTED","REDACTED","REDACTED"')
        self.assertNotIn('Sample', out)

    def test_the_preamble_goes_on_after_it(self):
        out, _rep = self._redact('Name,Jane,Sample\n12 Sample Street\n'
                                 + self.TABLE)
        self.assertEqual(out.splitlines()[:2],
                         ['Name,REDACTED,REDACTED', 'REDACTED'])
        self.assertTrue(out.endswith(self.TABLE))

    def test_a_header_starting_with_name_is_still_a_header(self):
        # Webull's order export: 'Name' first, then real column names.
        text = 'Name,Symbol,Side,Status,Filled\nSYN CORP,SYN,Buy,Filled,10\n'
        out, _rep = self._redact(text)
        self.assertEqual(out, text)
        # The two-cell identity line is unchanged (blanked whole).
        out, _rep = self._redact('Name,Jane Sample\n' + self.TABLE)
        self.assertEqual(out, 'REDACTED,REDACTED\n' + self.TABLE)


# --------------------------------------------------------------------------
# TJ-0590: an unpaired warrant exercise names the .tt lines that book it
# --------------------------------------------------------------------------

class TestUnpairedWarrantExerciseNamesTheTtLines(unittest.TestCase):
    """TJ-0590: the IB and RBC Direct messages for a warrant exercise
    leg with no single share leg give the .tt form (`exercise_of=`,
    docs/settings.md .tt ASSIGN) and say to remove the row."""

    def _rbc_refusal(self, rows):
        from test_fix_a2_warrants import TestWarrantExerciseRbc
        with self.assertRaises(BrokerageParseError) as cm:
            TestWarrantExerciseRbc()._parse(rows)
        return flat(str(cm.exception))

    def test_rbc_refusal_gives_the_lines(self):
        from test_fix_a2_warrants import TestWarrantExerciseRbc as R
        msg = self._rbc_refusal([R._BUY, R._WLEG])
        self.assertIn("remove the row from the export", msg)
        self.assertIn("`ASSIGN 2025-05-05 09:30:00 QZW.WT.TO -100 CAD 0 0 "
                      "exercise_of=SHARES`", msg)
        self.assertIn("`BUYSELL 2025-05-05 09:30:00 SHARES N CAD PRICE "
                      "TOTAL` at the exercise price", msg)
        self.assertIn("docs/settings.md, .tt ASSIGN", msg)
        # Two same-day share legs: the leg names one, no BUYSELL.
        msg = self._rbc_refusal([R._BUY, R._WLEG, R._SLEG, R._SLEG])
        self.assertIn("SHARES being the one of QZ.TO the warrants deliver",
                      msg)
        self.assertNotIn("BUYSELL", msg)

    def test_ib_attention_gives_the_lines(self):
        from test_fix_a2_ib import HEAD, TRADES_H, _trade, _parse_ib
        _, _, err = _parse_ib(HEAD + TRADES_H
                              + _trade('QZYW', '2025-05-02, 16:20:00', -100,
                                       0, 0, code='C;Ex', cat='Warrants'))
        att = flat(next(ln for ln in err.splitlines()
                        if 'warrant QZYW' in ln))
        self.assertIn("remove the row from the statement", att)
        self.assertIn("`ASSIGN 2025-05-02 16:20:00 QZYW.US -100 USD 0 0 "
                      "exercise_of=SHARES`", att)
        self.assertNotIn("Correct it by hand", att)

    def test_the_line_given_is_a_valid_tt_line(self):
        from taxjson.lib.brokerages.base import warrant_exercise_tt_hint
        from taxjson.lib.check_tt import parse_line
        hint = warrant_exercise_tt_hint(
            {'date': '2025-05-02', 'time': '16:20:00', 'symbol': 'QZW.US',
             'quantity': -100.0, 'currency': 'USD'})
        line = hint.split('`')[1].replace('SHARES', 'QZX.US')
        rows = []
        _kind, warns = parse_line(line, "h.tt:1", account="margin",
                                  crypto=False, equity=True, rows=rows)
        self.assertEqual(warns, [])
        self.assertEqual(rows[0]["exercise_of"], "QZX.US")

    def test_following_the_rbc_message_books_no_loss(self):
        # The warrant's row removed from the export, the two .tt lines
        # the message gives added: the shares carry the warrants' cost.
        import json
        from _qa_project import project, tj
        from test_fix_a2_warrants import _RBC_H, _rbc
        from test_fix_a2_warrants import TestWarrantExerciseRbc as R
        sell = _rbc('2025-09-10', 'Sell', 'QZ', 'QZ CORP', '-100',
                    '900.00', 'QZ CORP SELL', '9.00')
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, "p", {
                "margin/rbc.csv": _RBC_H + R._BUY + sell,
                "margin/hand.tt": (
                    "ASSIGN 2025-05-05 09:30:00 QZW.WT.TO -100 CAD 0 0 "
                    "exercise_of=QZ.TO\n"
                    "BUYSELL 2025-05-05 09:30:00 QZ.TO 100 CAD 5.00 500.00\n")})
            t = root / "taxjson.toml"
            t.write_text(t.read_text().replace("year = 2024", "year = 2025"))
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            g = json.loads((root / "work" / "margin_gains.json")
                           .read_text())["transactions"]
            self.assertEqual([(x["symbol"], x["cost"]) for x in g
                              if "gain" in x], [("QZ.TO", 600.0)])


if __name__ == '__main__':
    unittest.main()
