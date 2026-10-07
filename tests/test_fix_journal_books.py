"""A broker journal between a security's two lines needs no ticker.map
JOURNAL line (fix/journal-books).

- A Questrade BRW currency journal keeps its pair id (`journal_pair`) on
  the parsed rows whether the account keeps its transfers in the books
  (`transfers = true`) or aside, so the run joins the two lines as a
  currency journal (Canada, CA-XLIST-03) in both settings.
- The missing-history walks read a detected journal (a join of the run,
  a Questrade pair id, RBC's J~ reference on the two TFR legs) the way a
  JOURNAL line made them: an RBC gambit booked as a buy of one line and
  a sale of the other on one day, its TFR legs dated the settlement day,
  is no short with a TOBASE line or with no line at all.
- The "NOT in `taxjson sum`" warning is said only for sales the gains
  files lack (lib/first_run.engine_booking).

Every fixture is SYNTHETIC: invented QZ* / ZZ* names and symbols, fake
account ids (pii-ok: 55500001).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib.core import TaxTransaction
from tax_rules import rule, rule_absent

REPO = Path(__file__).resolve().parent.parent


def _rates(path: Path, pair='USD CAD', rate='1.3500'):
    d, lines = date(2025, 1, 1), []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 {pair} {rate} boc")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def _run(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
             PYTHONPATH=str(REPO / "src"))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


def _config(country='canada', transfers=None, year=2025, source='USD'):
    base = 'CAD' if country == 'canada' else 'USD'
    return (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{source}"]\n'
            + (f'option_grant_timing_since = {year}\n'
               if country == 'canada' else '')
            + '\n[accounts.margin]\ntype = "taxable"\n'
            + ('' if transfers is None
               else f'transfers = {"true" if transfers else "false"}\n'))


def _sum(root):
    r = _run(root, 'sum', '--json')
    return json.loads(r.stdout)


# ------------------------------------------------- 3. Questrade pair id

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
QNAME = 'QZ US DLR CURRENCY ETF UNIT CL A'


def _q(td, action, sym, desc, qty, price='0.0', gross='0.0', net='0.0',
       cur='CAD', act='Other', sd=None):
    return (f"{td} 12:00:00 AM,{sd or td} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price},{gross},0.0,{net},{cur},55500001,{act},"  # pii-ok
            f"Individual margin\n")


QT_CSV = QH + (
    _q('2025-09-10', 'Buy', 'QZD.TO', f'{QNAME} WE ACTED AS AGENT', '300.0',
       price='14.20', gross='-4260.00', net='-4260.00', act='Trades',
       sd='2025-09-11')
    + _q('2025-09-25', 'BRW', 'QZD', f'{QNAME} JOURNAL POSITION FROM CAD '
         'BOOK VALUE: $3016.67 CNV@ 1.4138', '300.0', cur='USD')
    + _q('2025-09-25', 'BRW', 'QZD', f'{QNAME} JOURNAL POSITION TO USD',
         '-300.0')
    + _q('2025-09-30', 'Sell', 'QZD.U.TO', f'{QNAME} WE ACTED AS AGENT',
         '-300.0', price='10.00', gross='3000.00', net='3000.00', cur='USD',
         act='Trades', sd='2025-10-01'))


def _qt_project(td, transfers, country='canada'):
    root = Path(td) / f"{country}-{transfers}"
    (root / 'inputs' / 'margin').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(
        _config(country, transfers,
                source='USD' if country == 'canada' else 'CAD'))
    (root / 'inputs' / 'margin' / 'questrade.csv').write_text(QT_CSV)
    (root / 'work').mkdir()
    _rates(root / 'work' / 'to_base.csv',
           *(() if country == 'canada' else ('CAD USD', '0.7400')))
    return root


def _journal_legs(root):
    """The parsed TRANSFER legs, from the books and the sidecar."""
    out = []
    for name in ('margin_questrade.json', 'margin_questrade_transfers.json'):
        f = root / 'work' / name
        if f.is_file():
            out += [t for t in json.loads(f.read_text())['transactions']
                    if t.get('action') == 'TRANSFER']
    return out


@rule("CA-XLIST-03")
class TestQuestradePairIdKept(unittest.TestCase):
    """The pair id survives the parse with transfers in the books and
    aside, and the run joins the two lines as a currency journal."""

    def _check(self, transfers):
        with tempfile.TemporaryDirectory() as td:
            root = _qt_project(td, transfers)
            r = _run(root, 'run', '--no-input')
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, out[-3000:])
            legs = _journal_legs(root)
            self.assertEqual(sorted(t['symbol'] for t in legs),
                             ['QZD.TO', 'QZD.U.TO'])
            ids = {t.get('journal_pair') for t in legs}
            self.assertEqual(len(ids), 1, legs)
            self.assertTrue(next(iter(ids)))
            st = XL.read_state(root / 'work' / XL.STATE)
            self.assertEqual([(j.get('kind'), j['from'], j['to'])
                              for j in st['joined']],
                             [('JOURNAL', 'QZD.U.TO', 'QZD.TO')])
            self.assertIn('(currency journal 2025-09-25)',
                          ' '.join(out.split()))
            self.assertNotIn('no purchase in your files', out)

    def test_transfers_in_the_books(self):
        self._check(True)

    def test_transfers_aside(self):
        self._check(False)

    def test_book_row_keeps_the_id(self):
        t = TaxTransaction(action='TRANSFER', date='2025-09-25',
                           symbol='QZD.TO', quantity=-300.0,
                           journal_pair='2025-09-25#1')
        self.assertEqual(t.to_dict()['journal_pair'], '2025-09-25#1')
        self.assertNotIn('journal_pair',
                         TaxTransaction(action='TRANSFER',
                                        date='2025-09-25').to_dict())
        self.assertEqual(t.id, TaxTransaction(
            action='TRANSFER', date='2025-09-25', symbol='QZD.TO',
            quantity=-300.0).id)


@rule("US-XLIST-01")
@rule_absent("CA-XLIST-03", country="usa")
class TestQuestradePairIdUsa(unittest.TestCase):
    def test_usa_keeps_the_id_but_never_joins_as_a_currency_journal(self):
        with tempfile.TemporaryDirectory() as td:
            root = _qt_project(td, True, country='usa')
            r = _run(root, 'run', '--no-input')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            self.assertEqual(len({t.get('journal_pair')
                                  for t in _journal_legs(root)}), 1)
            st = XL.read_state(root / 'work' / XL.STATE)
            self.assertFalse(any(j.get('kind') == 'JOURNAL'
                                 for j in st['joined']))


if __name__ == '__main__':
    unittest.main()
