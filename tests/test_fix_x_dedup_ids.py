"""Deferred-round fixes, area dedup-ids: cross-file dedup (R1-296,
S031-02), account-independent corp-action election ids (R1-301), and
the generic importer's broker name (S027-05). Synthetic data only."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.corp_actions import (CorporateAction, ElectionRecord,
                                      Manifest)

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TAXJSON_OFFLINE"] = "1"
    return env


# ---------------------------------------------------------------- R1-301

_SSL_RGLD_CSV = '''\
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (RGLD.CAD, ROYAL GOLD INC, US0000000002)",100.0026,0,25840.67184,0,
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (SSL, SANDSTORM GOLD LTD, CA0000000001)",-1600.0416,0,-25920.67392,0,
'''


def _old_scheme_id(ev: CorporateAction, account: str) -> str:
    """The id a manifest written before R1-301 carries: the readable
    part plus a 4-hex suffix hashed WITH the account name. Computed
    here independently of the library so the test pins the old
    on-disk format."""
    parts = (ev.date[:10], ev.action_type, ev.source_isin, ev.target_isin,
             f"{ev.ratio_new}-for-{ev.ratio_old}", account)
    suffix = hashlib.sha256("|".join(parts).encode()).hexdigest()[:4]
    return ev.event_id.rsplit('-', 1)[0] + '-' + suffix


def _event(account):
    return CorporateAction(
        date='2025-10-22', time='20:25:00', action_type='merger',
        source_symbol='SSL.TO', source_isin='CA0000000001',
        target_symbol='RGLD.US', target_isin='US0000000002',
        ratio_new=1, ratio_old=16, qty_disposed=1600.0416,
        qty_received=100.0026, fmv=25920.67, currency='CAD',
        target_currency='USD', account=account)


class TestElectionIdsSurviveRename(unittest.TestCase):
    """R1-301: renaming [accounts.rrsp] to retireA orphaned every
    election, because the event id hashed the account name."""

    def test_event_id_does_not_depend_on_account_name(self):
        self.assertEqual(_event('rrsp').event_id,
                         _event('retireA').event_id)

    def test_old_scheme_manifest_same_account_migrates(self):
        ev = _event('rrsp')
        old = _old_scheme_id(ev, 'rrsp')
        self.assertNotEqual(old, ev.event_id)
        self.assertEqual(ev.account_salted_event_id(), old)
        man = Manifest({old: ElectionRecord(
            event_id=old, summary='', election='rollover_s_85_1_5')})
        self.assertEqual(man.migrate_legacy([ev]), 1)
        self.assertIsNone(man.get(old))
        self.assertEqual(man.get(ev.event_id).election, 'rollover_s_85_1_5')
        self.assertEqual(man.migration_notes, [])     # a silent rekey
        self.assertEqual(man.migrate_legacy([ev]), 0)

    def test_old_scheme_manifest_renamed_account_migrates(self):
        """A manifest written by the old scheme under 'rrsp', read after
        the account became 'retireA' (the old salted id cannot be
        recomputed): the one record with the event's date and symbols
        is carried over, with a note."""
        old = _old_scheme_id(_event('rrsp'), 'rrsp')
        ev = _event('retireA')
        man = Manifest({old: ElectionRecord(
            event_id=old, summary='', election='rollover_s_85_1_5')})
        self.assertEqual(man.migrate_legacy([ev]), 1)
        self.assertEqual(man.get(ev.event_id).election, 'rollover_s_85_1_5')
        self.assertEqual(len(man.migration_notes), 1)
        self.assertIn('another account name', man.migration_notes[0])

    def test_rename_fallback_refuses_an_ambiguous_prefix(self):
        """Two events share the readable prefix (same day, same symbols,
        different ratio): the renamed-account fallback cannot tell which
        record is which, so neither is adopted (the run asks again)."""
        a = _event('retireA')
        b = _event('retireA')
        b.ratio_old = 8
        b.event_id = b._compute_id()
        old_a = _old_scheme_id(_event('rrsp'), 'rrsp')
        man = Manifest({old_a: ElectionRecord(
            event_id=old_a, summary='', election='rollover_s_85_1_5')})
        self.assertEqual(man.migrate_legacy([a, b]), 0)
        self.assertIsNotNone(man.get(old_a))

    def test_twelve_hex_legacy_id_still_migrates(self):
        ev = _event('rrsp')
        parts = ('2025-10-22', 'merger', 'CA0000000001', 'US0000000002',
                 '1-for-16', 'rrsp')
        legacy = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
        man = Manifest({legacy: ElectionRecord(
            event_id=legacy, summary='', election='taxable_disposition')})
        self.assertEqual(man.migrate_legacy([ev]), 1)
        self.assertEqual(man.get(ev.event_id).election,
                         'taxable_disposition')

    def test_cli_renamed_account_needs_no_new_election(self):
        """End to end: taxjson-corp-actions --no-input over a manifest
        written by the old scheme for 'rrsp', with the account now
        named 'retireA', resolves the event (exit 0, no pending)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            csv_p = td / "ib.csv"
            csv_p.write_text(_SSL_RGLD_CSV)
            from taxjson.lib.corp_actions import parse_ib_corporate_actions
            [ev] = parse_ib_corporate_actions(csv_p, account='rrsp')
            old = _old_scheme_id(ev, 'rrsp')
            self.assertNotEqual(old, ev.event_id)
            man_p = td / "manifest.json"
            man_p.write_text(json.dumps({"elections": {old: {
                "summary": "", "election": "rollover_s_85_1_5",
                "notes": ""}}}))
            pend = td / "pending.json"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
                 "--brokerage", "ib", "--account-name", "retireA",
                 "--country", "canada", "--manifest", str(man_p),
                 "--no-input", "--pending-json", str(pend), str(csv_p)],
                capture_output=True, text=True, env=_env())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(pend.exists() and json.loads(
                pend.read_text()).get("pending"), r.stderr)
            self.assertIn("another account name", r.stderr)
            saved = json.loads(man_p.read_text())["elections"]
            self.assertEqual(list(saved), [ev.event_id])


# ---------------------------------------------------------- R1-296 / S031-02

from taxjson.bin.taxjson_sort import deduplicate, plan_dedup  # noqa: E402
from taxjson.lib.core import TaxTransaction  # noqa: E402


def _tx(date, qty, source, desc="", **kw):
    return TaxTransaction(action="BUYSELL", date=date, symbol="XYZ.TO",
                          quantity=qty, price=10.0, net_amount=-10.0 * qty,
                          currency="CAD", account="margin",
                          description=desc, source=source, **kw)


class TestDedupPlan(unittest.TestCase):
    """The one dedup rule shared by the books and the fee report."""

    def test_same_file_repeat_collapses(self):
        rows = [_tx("2025-03-03", 100, "a.csv"), _tx("2025-03-03", 100, "a.csv")]
        self.assertEqual(len(deduplicate(rows)), 1)

    def test_rows_without_source_keep_the_id_rule(self):
        rows = [_tx("2025-03-03", 100, ""), _tx("2025-03-03", 100, "")]
        self.assertEqual(len(deduplicate(rows)), 1)

    def test_identical_lines_in_two_tt_files_are_both_booked(self):
        rows = [_tx("2025-03-04", 100, "m1.tt"), _tx("2025-03-04", 100, "m2.tt")]
        plan = plan_dedup(rows)
        self.assertEqual(plan.keep, [0, 1])
        self.assertEqual(plan.relabel, {1: rows[0].id + "~2"})
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("m1.tt and m2.tt", plan.attention[0])
        kept = deduplicate(rows)
        self.assertEqual(len({t.id for t in kept}), 2)

    def test_statements_of_different_broker_accounts_are_both_booked(self):
        rows = [_tx("2025-03-03", 100, "ib_a.csv"), _tx("2025-03-03", 100, "ib_b.csv")]
        plan = plan_dedup(rows, {"ib_a.csv": ["h1"], "ib_b.csv": ["h2"]})
        self.assertEqual(plan.keep, [0, 1])
        self.assertEqual(plan.attention, [])
        self.assertEqual(len(plan.notes), 1)
        # A consolidated statement covering both accounts overlaps each.
        plan = plan_dedup(rows, {"ib_a.csv": ["h1", "h2"], "ib_b.csv": ["h2"]})
        self.assertEqual(plan.keep, [0])

    def test_overlapping_re_export_collapses_quietly(self):
        a = [_tx("2025-12-1%d" % d, 10 + d, "q_2025.csv") for d in range(5, 10)]
        b = [_tx("2025-12-1%d" % d, 10 + d, "q_2026.csv") for d in range(5, 10)]
        b.append(_tx("2026-01-05", 7, "q_2026.csv"))
        a.insert(0, _tx("2025-06-01", 3, "q_2025.csv"))
        plan = plan_dedup(a + b)
        self.assertEqual(len(plan.keep), 7)
        self.assertEqual(len(plan.drop), 5)
        self.assertEqual(plan.attention, [])

    def test_partial_last_day_is_still_a_copy(self):
        """The earlier export was downloaded mid-day: its last day holds
        a subset of the later export's rows."""
        a = [_tx("2025-12-30", 1, "a.csv"), _tx("2025-12-31", 2, "a.csv")]
        b = [_tx("2025-12-30", 1, "b.csv"), _tx("2025-12-31", 2, "b.csv"),
             _tx("2025-12-31", 3, "b.csv"), _tx("2026-01-02", 4, "b.csv")]
        plan = plan_dedup(a + b)
        self.assertEqual(len(plan.drop), 2)
        self.assertEqual(plan.attention, [])

    def test_thin_overlap_is_collapsed_with_attention(self):
        """One identical row is all the two exports share: read as a
        re-export (exports cannot split a day), but said loudly."""
        a = [_tx("2025-03-03", 100, "qa.csv"), _tx("2025-06-02", -200, "qa.csv")]
        b = [_tx("2025-03-03", 100, "qb.csv")]
        plan = plan_dedup(a + b)
        self.assertEqual(plan.drop, [2])
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("qa.csv and qb.csv", plan.attention[0])
        self.assertIn("Booked ONCE", plan.attention[0])

    def test_tt_line_equal_to_an_exported_row_is_collapsed_with_attention(self):
        rows = [_tx("2025-03-03", 100, "q.csv"), _tx("2025-03-03", 100, "hist.tt")]
        plan = plan_dedup(rows)
        self.assertEqual(plan.drop, [1])
        self.assertEqual(len(plan.attention), 1)

    def test_dict_rows_without_id_never_collapse(self):
        rows = [{"action": "BUYSELL", "source": "a.csv"},
                {"action": "BUYSELL", "source": "b.csv"}]
        self.assertEqual(plan_dedup(rows).keep, [0, 1])


_TOML = """[settings]
year = 2025
country = "canada"
province = "ON"
base_currency = "CAD"
source_currencies = []
tax_date = "settle"
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""
_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type")
_QF = ("2025-03-03 12:00:00 AM,2025-03-04 12:00:00 AM,Buy,XYZ.TO,XYZ TEST "
       "CORP,100,10,-1000,-4.95,-1004.95,CAD,55500001,Trades,"  # pii-ok
       "Individual margin")
_QS = ("2025-06-02 12:00:00 AM,2025-06-03 12:00:00 AM,Sell,XYZ.TO,XYZ TEST "
       "CORP,-200,11,2200,-4.95,2195.05,CAD,55500001,Trades,"  # pii-ok
       "Individual margin")
_TTF = "BUYSELL  2025-03-04  09:30:00  XYZ.TO  100  CAD  10.00  1000.00  0\n"
_TTS = "BUYSELL  2025-06-03  09:30:00  XYZ.TO  -200  CAD  11.00  2200.00  0\n"


def _ib(acct, rows):
    return ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers\n"
            "Statement,Data,Title,Activity Statement\n"
            "Statement,Data,Period,\"January 1, 2025 - December 31, 2025\"\n"
            "Account Information,Header,Field Name,Field Value\n"
            "Account Information,Data,Name,Synth\n"
            f"Account Information,Data,Account,{acct}\n"
            "Account Information,Data,Base Currency,CAD\n"
            "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
            "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,MTM P/L,Code\n" + "".join(rows))


_IBF = ('Trades,Data,Order,Stocks,CAD,XYZ,"2025-03-03, 10:00:00",100,10.00,'
        '10.00,-1000.00,0,1000.00,0,0,O\n')
_IBS = ('Trades,Data,Order,Stocks,CAD,XYZ,"2025-06-02, 10:00:00",-200,11.00,'
        '11.00,2200.00,0,-2000.00,200,0,C\n')


def _run_project(files):
    td = tempfile.mkdtemp()
    root = Path(td)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_TOML)
    for fn, body in files.items():
        (root / "inputs" / "margin" / fn).write_text(body)
    r = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "run", "--no-input"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, env=_env())
    g = json.loads((root / "work" / "margin_gains.json").read_text())
    return root, r, g


class TestCrossFileDedupEndToEnd(unittest.TestCase):
    """Every case books 2 x 100 XYZ bought at 10 and one 200-share sale
    at 11 (the audit's repro, R1-296)."""

    def _check(self, files, gain, flat=True):
        import shutil
        root, r, g = _run_project(files)
        try:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertAlmostEqual(g["summary"]["total_gain"], gain, places=2)
            if flat:
                self.assertEqual(
                    [i for i in g["inventory"] if abs(i["qty"]) > 1e-9], [])
            return r.stdout + r.stderr, root
        finally:
            self._root = root
            self.addCleanup(shutil.rmtree, root, True)

    def test_identical_line_in_two_tt_files_books_both(self):
        out, _ = self._check({"m1.tt": _TTF + _TTS, "m2.tt": _TTF}, 200.0)
        self.assertIn("ATTENTION: dedup: m1.tt and m2.tt", out)

    def test_two_ib_statements_of_different_accounts_book_both(self):
        self._check({"ib_a.csv": _ib("U5550001", [_IBF, _IBS]),  # pii-ok
                     "ib_b.csv": _ib("U5550002", [_IBF])}, 200.0)  # pii-ok

    def test_split_questrade_export_is_loud(self):
        """A hand-split export: the two files share one row and nothing
        else — read as a re-export (gain 100, short 100), but the
        console names both files and the row."""
        root, r, g = _run_project({
            "questrade_a.csv": "\n".join([_QH, _QF, _QS]) + "\n",
            "questrade_b.csv": "\n".join([_QH, _QF]) + "\n"})
        import shutil
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ATTENTION: dedup: questrade_a.csv and questrade_b.csv",
                      r.stdout)
        # The fee report applies the same rule: it agrees with the books.
        fr = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_fees", "--cache",
             str(root / "work"), "--year", "2025", "--json"],
            capture_output=True, text=True, env=_env())
        self.assertEqual(fr.returncode, 0, fr.stderr)
        doc = json.loads(fr.stdout)
        self.assertEqual(doc["total"]["trades"], 2)
        self.assertEqual(doc["meta"]["dups_collapsed"], 1)
        self.assertIn("ATTENTION: dedup:", fr.stderr)

    def test_fee_report_counts_both_tt_lines(self):
        """S031-02: the fee report must count what the books book —
        identical commissions in two .tt files are two trades."""
        ttf = "BUYSELL  2025-03-04  09:30:00  XYZ.TO  100  CAD  10.00  1004.95  4.95\n"
        tts = "BUYSELL  2025-06-03  09:30:00  XYZ.TO  -200  CAD  11.00  2195.05  4.95\n"
        root, r, g = _run_project({"m1.tt": ttf + tts, "m2.tt": ttf})
        import shutil
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(r.returncode, 0, r.stderr)
        fr = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_fees", "--cache",
             str(root / "work"), "--year", "2025", "--json"],
            capture_output=True, text=True, env=_env())
        doc = json.loads(fr.stdout)
        self.assertEqual(doc["total"]["trades"], 3)
        self.assertAlmostEqual(doc["total"]["total"], 14.85, places=2)
        self.assertEqual(doc["meta"]["dups_collapsed"], 0)

    def test_overlapping_questrade_exports_collapse_quietly(self):
        q2 = ("2025-06-05 12:00:00 AM,2025-06-06 12:00:00 AM,Buy,ABC.TO,ABC "
              "CORP,10,5,-50,0,-50,CAD,55500001,Trades,Individual margin")  # pii-ok
        out, _ = self._check({
            "questrade_a.csv": "\n".join([_QH, _QF, _QF, _QS, q2]) + "\n",
            "questrade_b.csv": "\n".join([_QH, _QS, q2]) + "\n"},
            200.0 - 3 * 4.95, flat=False)
        self.assertNotIn("ATTENTION: dedup", out)


# ---------------------------------------------------------------- S027-05

_GEN_MAP = """[columns]
date = "Date"
action = "Type"
symbol = "Ticker"
quantity = "Shares"
price = "Price"
fee = "Commission"
amount = "Net"
[actions]
"BUY" = "buy"
"SELL" = "sell"
[defaults]
currency = "CAD"
"""
_GEN_HDR = "Date,Type,Ticker,Shares,Price,Commission,Net\n"
_Q2024 = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
          "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
          "Activity Type,Account #,Account Type\n"
          "2024-02-01,2024-02-05,Buy,RY.TO,ROYAL BANK,100,120.00,-12000.00,"
          "-5.00,-12005.00,CAD,Trades,55500001,Margin\n")  # pii-ok


class TestGenericBrokerName(unittest.TestCase):
    """S027-05: every generic-imported file was 'generic'; the fees
    report lumped two brokers and said Questrade had NO fees."""

    def _project(self, qt_name='[broker]\nname = "questrade"\n',
                 ws_name='[broker]\nname = "Wealthsimple"\n'):
        import shutil
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        m = td / "inputs" / "margin"
        m.mkdir(parents=True)
        (td / "taxjson.toml").write_text(_TOML)
        (m / "questrade_2024.csv").write_text(_Q2024)
        (m / "generic_questrade_2025.csv").write_text(
            _GEN_HDR + "2025-03-03,SELL,RY,100,150.00,5.00,14995.00\n"
            "2025-04-01,BUY,TD,50,80.00,5.00,-4005.00\n")
        (m / "generic_questrade_2025.csv.toml").write_text(_GEN_MAP + qt_name)
        (m / "generic_wealthsimple_2025.csv").write_text(
            _GEN_HDR + "2025-05-05,BUY,BNS,10,60.00,1.00,-601.00\n")
        (m / "generic_wealthsimple_2025.csv.toml").write_text(
            _GEN_MAP + ws_name)
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(td),
             "run", "--no-input"], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, env=_env())
        return td, r

    def test_named_generic_brokers_are_parsed_and_reported_apart(self):
        td, r = self._project()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        work = td / "work"
        q = json.loads((work / "margin_generic-questrade.json").read_text())
        w = json.loads((work / "margin_generic-wealthsimple.json").read_text())
        self.assertEqual(q["metadata"]["source_brokerage"], "generic:questrade")
        self.assertEqual(w["metadata"]["source_brokerage"],
                         "generic:wealthsimple")
        self.assertEqual([Path(f).name for f in q["metadata"]["input_files"]],
                         ["generic_questrade_2025.csv"])
        self.assertFalse((work / "margin_generic.json").exists())
        rpt = (td / "reports" / "fees.rpt").read_text()
        self.assertIn("generic:questrade", rpt)
        self.assertIn("generic:wealthsimple", rpt)
        self.assertNotIn("NO fees in this period: questrade", rpt)
        g = json.loads((work / "margin_gains.json").read_text())
        self.assertAlmostEqual(g["summary"]["total_gain"],
                               14995.00 - 12005.00, places=2)

    def test_unnamed_mappings_keep_the_generic_group(self):
        td, r = self._project(qt_name="", ws_name="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads((td / "work" / "margin_generic.json").read_text())
        self.assertEqual(d["metadata"]["source_brokerage"], "generic")

    def test_bad_broker_name_is_refused(self):
        from taxjson.lib.brokerages.generic import mapping_broker_name
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / "generic_x.csv"
            c.write_text(_GEN_HDR)
            Path(str(c) + ".toml").write_text(
                _GEN_MAP + '[broker]\nname = "a/b"\n')
            with self.assertRaises(ValueError) as cm:
                mapping_broker_name(c)
            self.assertIn("[broker].name", str(cm.exception))
            Path(str(c) + ".toml").write_text(
                _GEN_MAP + '[broker]\nnmae = "x"\n')
            with self.assertRaises(ValueError):
                mapping_broker_name(c)

    def test_one_parse_refuses_two_named_brokers(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            for nm in ("aaa", "bbb"):
                c = t / f"generic_{nm}.csv"
                c.write_text(_GEN_HDR
                             + "2025-04-01,BUY,TD,50,80.00,5.00,-4005.00\n")
                Path(str(c) + ".toml").write_text(
                    _GEN_MAP + f'[broker]\nname = "{nm}"\n')
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "generic", str(t / "generic_aaa.csv"),
                 str(t / "generic_bbb.csv")],
                capture_output=True, text=True, env=_env())
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("name different brokers", r.stderr)


if __name__ == "__main__":
    unittest.main()
