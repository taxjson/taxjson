"""Owner decision (round 3): `[accounts.<name>] combined_broker_accounts =
true` declares that every broker account in the label's statements is the
user's and taxable together. The 'statement spans N accounts' ATTENTION
(IB per statement and across statements, RBC, Questrade) then becomes a
one-line NOTE with masked ids; on a sheltered account it is honoured only
when the statement shows every account is the same plan, else refused.

Every fixture is synthetic (fake account ids 55500001 / U5550001)."""  # pii-ok
import unittest

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.questrade import QuestradeBrokerage

from test_fix_a2_ib import A1, A2, _stmt, _prepare
from test_fix_ibparse import TRADES_H, _trade, _brokerage_cli
from test_fix_rbc import HDR, row
from test_fix_rbcqt import q, qt_parse
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

ACCT = "55500001"   # pii-ok
ACCT2 = "55500002"  # pii-ok


def _attention(err):
    """The multi-account ATTENTION lines (other ATTENTIONs — a statement
    with no Cash Report, say — are not this setting's business)."""
    return [ln for ln in err.splitlines()
            if "ATTENTION" in ln and " accounts" in ln]


def _notes(err, word):
    return [ln for ln in err.splitlines()
            if ln.startswith("note:") and word in ln]


IB_TWO = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H,
               _trade('QZA', '2025-03-03, 10:00:00', 1, 10, -10),
               accts=(A1, A2))


class TestIbCombined(unittest.TestCase):

    def test_default_is_still_attention(self):
        rc, _out, err, _ = _brokerage_cli({'ib.csv': IB_TWO},
                                          '--account-type', 'taxable')
        self.assertEqual(rc, 0, err)
        self.assertTrue(any('spans 2 accounts' in ln
                            for ln in _attention(err)), err)

    def test_combined_taxable_is_one_note_with_masked_ids(self):
        rc, out, err, _ = _brokerage_cli({'ib.csv': IB_TWO},
                                         '--account-type', 'taxable',
                                         '--combined-broker-accounts')
        self.assertEqual(rc, 0, err)
        self.assertEqual(_attention(err), [], err)
        notes = _notes(err, 'combined_broker_accounts')
        self.assertEqual(len(notes), 1, err)
        self.assertIn('U5***', notes[0])
        self.assertNotIn(A1, err)
        self.assertNotIn(A2, err)
        self.assertEqual(len(out['transactions']), 1)

    def test_combined_across_statements(self):
        files = {'a.csv': _stmt('January 1, 2025', 'December 31, 2025'),
                 'b.csv': _stmt('January 1, 2025', 'December 31, 2025',
                                accts=(A2,))}
        self.assertIn('belong to 2 IB accounts', _prepare(files))
        rc, _out, err, _ = _brokerage_cli(files, '--account-type',
                                          'taxable',
                                          '--combined-broker-accounts')
        self.assertEqual(rc, 0, err)
        self.assertEqual(_attention(err), [], err)
        self.assertEqual(len(_notes(err, 'combined_broker_accounts')), 1,
                         err)

    def test_combined_on_a_sheltered_account_is_refused(self):
        # IB's statement does not name each account's plan.
        rc, _out, err, _ = _brokerage_cli({'ib.csv': IB_TWO},
                                          '--account-type', 'sheltered',
                                          '--combined-broker-accounts')
        self.assertNotEqual(rc, 0, err)
        self.assertIn('combined_broker_accounts', err)
        self.assertIn('sheltered', err)
        self.assertNotIn(A1, err)

    def test_one_account_statement_needs_nothing(self):
        one = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H,
                    _trade('QZA', '2025-03-03, 10:00:00', 1, 10, -10))
        rc, _out, err, _ = _brokerage_cli({'ib.csv': one},
                                          '--account-type', 'sheltered',
                                          '--combined-broker-accounts')
        self.assertEqual(rc, 0, err)
        self.assertEqual(_notes(err, 'combined_broker_accounts'), [])


def _qt(acct, atype, td, qty, price):
    g = qty * price
    return q(td=td, action="Buy", sym="QZEQ.TO",
             desc="QZEQ ETF WE ACTED AS AGENT", qty=str(qty),
             price=str(price), gross=f"{-g:.2f}", comm="0",
             net=f"{-g:.2f}", cur="CAD").replace(
                 f",{ACCT},Trades,Individual margin",
                 f",{acct},Trades,{atype}")


def _qt_combined(body, taxable):
    orig = QuestradeBrokerage.combined_broker_accounts
    QuestradeBrokerage.combined_broker_accounts = True
    try:
        return qt_parse(body, taxable=taxable)
    finally:
        QuestradeBrokerage.combined_broker_accounts = orig


class TestQuestradeCombined(unittest.TestCase):

    def test_two_taxable_accounts_become_a_note(self):
        body = (_qt(ACCT, "Individual margin", "2025-01-15", 10, 30)
                + _qt(ACCT2, "Joint margin", "2025-02-18", 100, 30))
        txs, err, _ = _qt_combined(body, True)
        self.assertEqual(len(txs), 2)
        self.assertEqual(_attention(err), [], err)
        notes = _notes(err, 'combined_broker_accounts')
        self.assertEqual(len(notes), 1, err)
        self.assertIn('55***', notes[0])
        self.assertNotIn(ACCT2, err)

    def test_registered_rows_in_a_taxable_account_still_refused(self):
        body = (_qt(ACCT, "Individual margin", "2025-01-15", 10, 30)
                + _qt(ACCT2, "Individual TFSA", "2025-02-18", 100, 30))
        with self.assertRaises(BrokerageParseError):
            _qt_combined(body, True)

    def test_sheltered_same_plan_is_a_note(self):
        body = (_qt(ACCT, "Individual RRSP", "2025-01-15", 10, 30)
                + _qt(ACCT2, "Individual RRSP", "2025-02-18", 100, 30))
        txs, err, _ = _qt_combined(body, False)
        self.assertEqual(len(txs), 2)
        self.assertEqual(_attention(err), [], err)
        self.assertEqual(len(_notes(err, 'combined_broker_accounts')), 1,
                         err)

    def test_sheltered_two_plans_are_refused(self):
        body = (_qt(ACCT, "Individual RRSP", "2025-01-15", 10, 30)
                + _qt(ACCT2, "Individual TFSA", "2025-02-18", 100, 30))
        with self.assertRaises(BrokerageParseError) as cm:
            _qt_combined(body, False)
        self.assertIn('combined_broker_accounts', str(cm.exception))
        self.assertNotIn(ACCT2, str(cm.exception))


class TestRbcCombined(unittest.TestCase):
    A = row("March 3, 2025", "Buy", "XYZ", "XYZ CORP", "10", "5.00",
            "-59.95", "CAD", "XYZ CORP UNSOLICITED")
    B = row("March 4, 2025", "Buy", "XYZ", "XYZ CORP", "10", "5.00",
            "-59.95", "CAD", "XYZ CORP UNSOLICITED", acct=ACCT2)

    def test_taxable_combined_is_a_note(self):
        rc, out, err, _ = _brokerage_cli({'rbc.csv': HDR + self.A + self.B},
                                         '--account-type', 'taxable',
                                         '--combined-broker-accounts',
                                         brokerage='rbc')
        self.assertEqual(rc, 0, err)
        self.assertEqual(_attention(err), [], err)
        notes = _notes(err, 'combined_broker_accounts')
        self.assertEqual(len(notes), 1, err)
        self.assertNotIn(ACCT2, err)
        self.assertEqual(len(out['transactions']), 2)

    def test_default_is_still_attention(self):
        rc, _out, err, _ = _brokerage_cli({'rbc.csv': HDR + self.A + self.B},
                                          '--account-type', 'taxable',
                                          brokerage='rbc')
        self.assertEqual(rc, 0, err)
        self.assertTrue(any('2 RBC accounts' in ln
                            for ln in _attention(err)), err)

    def test_sheltered_combined_is_refused(self):
        rc, _out, err, _ = _brokerage_cli({'rbc.csv': HDR + self.A + self.B},
                                          '--account-type', 'sheltered',
                                          '--combined-broker-accounts',
                                          brokerage='rbc')
        self.assertNotEqual(rc, 0, err)
        self.assertIn('combined_broker_accounts', err)
        self.assertNotIn(ACCT2, err)


class TestConfig(unittest.TestCase):

    def test_must_be_a_bool(self):
        from taxjson.lib.config_check import account_type_problems
        cfg = {"accounts": {"margin": {"type": "taxable",
                                       "combined_broker_accounts": "true"}}}
        probs = account_type_problems(cfg)
        self.assertTrue(any("combined_broker_accounts" in p
                            for p in probs), probs)
        cfg["accounts"]["margin"]["combined_broker_accounts"] = True
        self.assertEqual(account_type_problems(cfg), [])

    def test_known_key_and_passed_to_the_parser(self):
        from taxjson.bin import taxjson_run as R
        self.assertIn("combined_broker_accounts", R._ACCOUNT_KEYS)
        cfg = {"settings": {"year": 2025, "country": "canada",
                            "base_currency": "CAD"},
               "accounts": {"margin": {"type": "taxable",
                                       "combined_broker_accounts": True}}}
        warns = R.validate_config(cfg)
        self.assertFalse(any("combined_broker_accounts" in w
                             for w in warns), warns)
        self.assertEqual(R._brokerage_account_flags(
            cfg["accounts"]["margin"]), ["--combined-broker-accounts"])
        self.assertEqual(R._brokerage_account_flags({"type": "taxable"}),
                         [])


if __name__ == "__main__":
    unittest.main()
