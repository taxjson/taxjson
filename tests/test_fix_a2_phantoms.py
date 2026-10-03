"""Regression pins for the re-audit-2 "phantoms" list (lib/missing_history,
formerly lib/phantom_holdings; find-missing-history; the run's
missing_history.json handling — the file was phantoms.json until 2026-10).

Synthetic data only: fake account numbers (55500001 # pii-ok), all-CAD
Questrade books, no FX fetch.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(action, date, symbol, qty, price=0.0, net=0.0, account="margin",
        time="09:30:00", **kw):
    from taxjson.lib.core import TaxTransaction
    kw.setdefault("currency", "CAD")
    return TaxTransaction(action=action, date=date, time=time, symbol=symbol,
                          quantity=qty, price=price, net_amount=net,
                          account=account, date_settle=kw.pop("settle", date),
                          **kw)


def _run_cli(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _run_mod(mod, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    return subprocess.run([sys.executable, "-m", mod, *map(str, args)],
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          env=e, stdin=subprocess.DEVNULL)


_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(date, settle, action, sym, qty, price, net):
    gross = -net if action == "Buy" else net
    return (f"{date} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},"
            f"{sym} CORP,{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,"
            f"55500001,Trades,Individual\n")      # pii-ok


# ZZZ.TO: 100 sold with no buy in the data (bought before it), 100
# bought back — the phantom case. XEI.TO: a clean round trip.
_PHANTOM_CSV = _QT_HEADER + (
    _qt("2025-01-10", "2025-01-13", "Sell", "ZZZ.TO", -100, 30.0, 3000.0)
    + _qt("2025-03-03", "2025-03-04", "Buy", "ZZZ.TO", 100, 20.0, -2000.0)
    + _qt("2025-02-03", "2025-02-04", "Buy", "XEI.TO", 10, 10.0, -100.0)
    + _qt("2025-06-02", "2025-06-03", "Sell", "XEI.TO", -10, 12.0, 120.0))


def _project(tmp, csv=_PHANTOM_CSV, missing_history=None, config=_CONFIG):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(csv)
    if missing_history is not None:
        (root / "missing_history.json").write_text(
            json.dumps(missing_history))
    return root


# ------------------------------------------------------------------ A2-1097

class TestRegisteredLabelTokens(unittest.TestCase):
    """A2-1097: the Canadian plan names match as whole tokens, like the
    US ones (R1-243): a taxable 'sunlife' is not a LIF."""

    def test_canadian_plan_names_are_tokens(self):
        from taxjson.lib.missing_history import is_registered_account
        for label in ("sunlife", "cliff-margin", "response", "stfsa",
                      "Lifeco"):
            self.assertFalse(is_registered_account(label, None, "canada"),
                             label)
            self.assertFalse(is_registered_account(label), label)
        for label in ("rrsp2", "my-tfsa", "Spousal RRSP", "LIF", "TFSA-Self",
                      "lira_old"):
            self.assertTrue(is_registered_account(label, None, "canada"),
                            label)


# ------------------------------------------------------------------ A2-1096

class TestMergerRatioDecimalComma(unittest.TestCase):
    """A2-1096: the merger hint reads the ratio with the corp-actions
    reader: a decimal comma is left out, never read as 125."""

    def _link(self, desc):
        from taxjson.lib.missing_history import detect_corp_action_links
        txs = [
            _tx("BUYSELL", "2025-04-01", "OLDCO.TO", -100, 0.0, 0.0,
                description=f"MGR OLDCO MERGER TO NEWCO {desc}"),
            _tx("BUYSELL", "2025-04-01", "NEWCO.TO", 12.5, 0.0, 0.0,
                description="MGR NEWCO SHRS RECEIVED THRU MERGER"),
        ]
        links = detect_corp_action_links(txs)
        self.assertEqual(len(links), 1)
        return links[0].ratio

    def test_decimal_comma_ratio_is_omitted(self):
        self.assertEqual(self._link("0,125 NEW = 1 OLD"), 0.0)
        self.assertEqual(self._link("1 NEW = 0,500 OLD"), 0.0)

    def test_thousands_and_decimal_point_still_read(self):
        self.assertAlmostEqual(self._link("1 NEW = 1,000 OLD"), 0.001)
        self.assertAlmostEqual(self._link("0.125 NEW = 1 OLD"), 0.125)


# ------------------------------------------------------------------ A2-0307

class TestZeroBasisAssign(unittest.TestCase):
    """A2-0307: an ASSIGN stock leg moves the $0-basis walk like a
    trade (the module's other walks count it)."""

    def test_assign_disposition_of_zero_basis_shares_is_flagged(self):
        from taxjson.lib.missing_history import \
            detect_zero_basis_acquisitions
        txs = [
            _tx("BUYSELL", "2025-02-03", "SPN.TO", 100, 0.0, 0.0,
                description="SPIN OFF SHARES RECEIVED"),
            _tx("ASSIGN", "2025-05-16", "SPN.TO", -100, 50.0, 5000.0),
        ]
        rows = detect_zero_basis_acquisitions(txs, 2025)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].affects_year)
        self.assertEqual(rows[0].in_year_dispositions, 1)
        self.assertAlmostEqual(rows[0].in_year_proceeds, 5000.0)

    def test_assign_drains_the_pool_so_a_later_clean_sale_is_clean(self):
        from taxjson.lib.missing_history import \
            detect_zero_basis_acquisitions
        txs = [
            _tx("BUYSELL", "2025-02-03", "SPN.TO", 100, 0.0, 0.0,
                description="SPIN OFF SHARES RECEIVED"),
            _tx("ASSIGN", "2025-05-16", "SPN.TO", -100, 50.0, 5000.0),
            _tx("BUYSELL", "2025-06-02", "SPN.TO", 100, 60.0, -6000.0),
            _tx("BUYSELL", "2025-07-02", "SPN.TO", -100, 65.0, 6500.0),
        ]
        rows = detect_zero_basis_acquisitions(txs, 2025)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].in_year_dispositions, 1)
        self.assertAlmostEqual(rows[0].in_year_proceeds, 5000.0)

    def test_option_leg_of_an_assignment_is_not_a_zero_cost_buy(self):
        from taxjson.lib.missing_history import \
            detect_zero_basis_acquisitions
        opt = "SPN250516P00050000.TO"
        txs = [
            _tx("BUYSELL", "2025-04-01", opt, -1, 2.0, 200.0),
            _tx("ASSIGN", "2025-05-16", opt, 1, 0.0, 0.0),
            _tx("BUYSELL", "2025-06-01", opt, -1, 1.0, 100.0),
        ]
        self.assertEqual(
            detect_zero_basis_acquisitions(txs, 2025, include_options=True),
            [])


# ------------------------------------------------------- A2-0306 / A2-0175

class TestUnbackedCovers(unittest.TestCase):
    """A2-0306 (RBC 'COVER SHORT.'), IB twin A2-0175 (a buy coded C):
    a broker-marked cover no short in the data backs is missing
    history, reported by find-missing-history."""

    def test_rbc_cover_with_no_short_in_the_data(self):
        from taxjson.lib.missing_history import detect_unbacked_covers
        txs = [_tx("BUYSELL", "2025-04-19", "QQA.TO", 100, 10.0, -1000.0,
                   description="QQA CORP COVER SHORT. UNSOLICITED")]
        got = detect_unbacked_covers(txs)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].marker, "COVER SHORT.")
        self.assertAlmostEqual(got[0].unbacked_qty, 100.0)

    def test_rbc_cover_backed_by_an_in_data_short_is_silent(self):
        from taxjson.lib.missing_history import detect_unbacked_covers
        txs = [
            _tx("BUYSELL", "2025-04-15", "QQA.TO", -100, 10.5, 1050.0,
                time="09:30:01", description="QQA CORP SHORT. UNSOLICITED"),
            _tx("BUYSELL", "2025-04-15", "QQA.TO", 100, 10.0, -1000.0,
                time="09:30:01",
                description="QQA CORP COVER SHORT. UNSOLICITED"),
        ]
        self.assertEqual(detect_unbacked_covers(txs), [])

    def test_partly_backed_cover_reports_the_excess(self):
        from taxjson.lib.missing_history import detect_unbacked_covers
        txs = [
            _tx("BUYSELL", "2025-04-15", "QQA.TO", -40, 10.5, 420.0,
                description="QQA CORP SHORT. UNSOLICITED"),
            _tx("BUYSELL", "2025-04-19", "QQA.TO", 100, 10.0, -1000.0,
                description="QQA CORP COVER SHORT. UNSOLICITED"),
        ]
        got = detect_unbacked_covers(txs)
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0].unbacked_qty, 60.0)

    def test_ib_buy_coded_c_with_no_short(self):
        from taxjson.lib.missing_history import detect_unbacked_covers
        txs = [_tx("BUYSELL", "2025-04-19", "XYZ.US", 100, 10.0, -1000.0,
                   currency="USD", open_close="C")]
        got = detect_unbacked_covers(txs)
        self.assertEqual([(c.symbol, c.marker) for c in got],
                         [("XYZ.US", "IB code C")])

    def test_plain_buy_is_not_a_cover(self):
        from taxjson.lib.missing_history import detect_unbacked_covers
        txs = [_tx("BUYSELL", "2025-04-19", "XYZ.TO", 100, 10.0, -1000.0)]
        self.assertEqual(detect_unbacked_covers(txs), [])

    def test_find_missing_history_reports_the_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2025-04-19", "QQA.TO", 100, 10.0, -1000.0,
                    description="QQA CORP COVER SHORT. UNSOLICITED"
                    ).to_dict()]}))
            r = _run_mod("taxjson.bin.taxjson_missing_history",
                         "--year", "2025", base)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Covers of a short opened before the data",
                          r.stdout)
            self.assertIn("AFFECTS 2025", r.stdout)
            self.assertIn("QQA.TO", r.stdout)
            self.assertNotIn("No missing-cost-basis issues", r.stdout)


# ------------------------------------------------------- A2-0309 / A2-0636

class TestJournalPairNotAPhantomShort(unittest.TestCase):
    """A2-0636 / A2-0309: a Norbert's-gambit pair folded by a JOURNAL
    line, the sale stamped ahead of the buy (RBC row ordinals), is not
    a phantom short; other symbols keep the clock."""

    def _txs(self, sym="DLR.TO"):
        return [
            _tx("BUYSELL", "2025-05-05", sym, -1000, 13.80, 13800.0,
                time="09:30:00", settle="2025-05-06"),
            _tx("BUYSELL", "2025-05-05", sym, 1000, 13.783, -13783.0,
                time="09:30:01", settle="2025-05-06"),
        ]

    def test_journal_symbol_reads_buys_first(self):
        from taxjson.lib.missing_history import detect_missing_history
        self.assertEqual(len(detect_missing_history(self._txs())), 1)
        self.assertEqual(detect_missing_history(self._txs(),
                                         journal_symbols={"DLR.TO"}), [])

    def test_other_symbol_same_day_sale_and_rebuy_still_reported(self):
        from taxjson.lib.missing_history import detect_missing_history
        got = detect_missing_history(self._txs("ZZQ.TO"),
                              journal_symbols={"DLR.TO"})
        self.assertEqual([c.symbol for c in got], ["ZZQ.TO"])

    def test_find_missing_history_with_ticker_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                t.to_dict() for t in self._txs()]}))
            tmap = Path(tmp) / "ticker.map"
            tmap.write_text("JOURNAL DLR.US DLR.TO\n")
            r = _run_mod("taxjson.bin.taxjson_missing_history",
                         "--year", "2025", base)
            self.assertIn("DLR.TO", r.stdout)
            r = _run_mod("taxjson.bin.taxjson_missing_history",
                         "--year", "2025", "--ticker-map", tmap, base)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("DLR.TO", r.stdout)
            self.assertIn("No missing-cost-basis issues", r.stdout)


# ------------------------------- A2-0308 / 0310 / 0311 / 0637 / 0638 / 0639

class TestStaleMissingHistoryEntries(unittest.TestCase):
    """An explicit missing_history.json entry on a broker-marked real short or
    a written option is still applied (the user's record), but every
    applier says so — synthesize_openings is the one they share."""

    _MARKED = [
        ("BUYSELL", "2024-04-15", "QQA.TO", -700, 10.7, 7490.0,
         "QQA CORP SHORT. UNSOLICITED"),
        ("BUYSELL", "2024-04-16", "QQA.TO", -300, 10.7, 3210.0,
         "QQA CORP SHORT. UNSOLICITED"),
        ("BUYSELL", "2024-04-19", "QQA.TO", 1000, 10.7, -10700.0,
         "QQA CORP COVER SHORT. UNSOLICITED"),
    ]

    def _marked(self):
        return [_tx(a, d, s, q, p, n, description=desc)
                for a, d, s, q, p, n, desc in self._MARKED]

    def _synth(self, txs, pairs, **kw):
        from taxjson.lib.missing_history import synthesize_openings
        err = io.StringIO()
        with redirect_stderr(err):
            out, log = synthesize_openings(txs, pairs, **kw)
        return out, log, err.getvalue()

    @rule("CA-ACB-11")
    def test_marked_short_entry_is_flagged(self):
        _out, log, err = self._synth(self._marked(), {("QQA.TO", "margin")})
        e = next(x for x in log if x["symbol"] == "QQA.TO")
        self.assertTrue(e["inserted"])
        self.assertEqual(e["stale"], "broker-short")
        self.assertIn("warning: ATTENTION: missing_history.json lists "
                      "QQA.TO / margin", err)
        self.assertIn("REAL short", err)

    def test_ib_code_o_entry_is_flagged(self):
        txs = [_tx("BUYSELL", "2024-03-01", "XYZ.US", -100, 17.0, 1700.0,
                   currency="USD", open_close="O"),
               _tx("BUYSELL", "2024-03-20", "XYZ.US", 100, 34.0, -3400.0,
                   currency="USD", open_close="C")]
        _out, log, err = self._synth(txs, {("XYZ.US", "margin")})
        self.assertEqual(log[0]["stale"], "broker-short")
        self.assertIn("codes the sale O (opening)", err)

    def test_written_option_entry_is_flagged(self):
        opt = "XYZ260619C00050000.TO"
        txs = [_tx("BUYSELL", "2026-01-05", opt, -1, 3.0, 300.0),
               _tx("BUYSELL", "2026-02-05", opt, 1, 1.0, -100.0)]
        _out, log, err = self._synth(txs, {(opt, "margin")})
        self.assertEqual(log[0]["stale"], "derivative")
        self.assertIn("reads as a WRITE", err)

    def test_ib_closing_option_entry_is_not_flagged(self):
        opt = "XYZ260619C00050000.US"
        txs = [_tx("BUYSELL", "2026-01-05", opt, -1, 3.0, 300.0,
                   currency="USD", open_close="C")]
        _out, log, err = self._synth(txs, {(opt, "margin")})
        self.assertTrue(log[0]["inserted"])
        self.assertNotIn("stale", log[0])
        self.assertEqual(err, "")

    def test_genuine_missing_history_entry_is_quiet(self):
        txs = [_tx("BUYSELL", "2024-03-01", "OLD.TO", -100, 10.0, 1000.0)]
        _out, log, err = self._synth(txs, {("OLD.TO", "margin")})
        self.assertTrue(log[0]["inserted"])
        self.assertNotIn("stale", log[0])
        self.assertEqual(err, "")

    def test_flag_stale_false_is_quiet_but_logs(self):
        _out, log, err = self._synth(self._marked(), {("QQA.TO", "margin")},
                                     flag_stale=False)
        self.assertEqual(log[0]["stale"], "broker-short")
        self.assertEqual(err, "")

    def test_find_missing_history_lists_the_entry_for_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                t.to_dict() for t in self._marked()]}))
            ph = Path(tmp) / "missing_history.json"
            ph.write_text(json.dumps([{"symbol": "QQA.TO",
                                       "account": "margin"}]))
            r = _run_mod("taxjson.bin.taxjson_missing_history",
                         "--year", "2024", "--missing-history", ph, base)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("REMOVE from missing_history.json", r.stdout)
            self.assertIn("missing_history.json LISTS it: remove that entry",
                          r.stdout)
            self.assertNotIn("No missing-cost-basis issues", r.stdout)

    def test_checklist_turns_attention_on_a_stale_entry(self):
        from taxjson.lib import checklist

        out = ("\n## missing_history.json entries that are not missing "
               "history: 1\n"
               "REMOVE from missing_history.json - the run applies them "
               "and they move a real gain or loss off the totals:\n"
               "QQA.TO margin\n"
               "    missing_history.json lists QQA.TO / margin, but ...\n")

        class _Ctx:
            year = 2024

            def sub(self, *_a, **_k):
                return 0, out, ""
        res = checklist.d_missing_history(_Ctx())
        self.assertEqual(res.status, "attention")
        self.assertIn("QQA.TO (margin)", res.detail)

    def test_run_echoes_the_attention_line(self):
        # A written call in a .tt book with a stale missing_history.json
        # entry:
        # the gains stage's ATTENTION line reaches the console.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, missing_history=[
                {"symbol": "XYZ260619C00050000.TO", "account": "margin"}])
            (root / "inputs" / "margin" / "opts.tt").write_text(
                "BUYSELL 2025-03-02 09:30:00 XYZ260619C00050000.TO -1 CAD "
                "3.00 300.00 0.0\n"
                "BUYSELL 2025-04-02 09:30:00 XYZ260619C00050000.TO 1 CAD "
                "1.00 -100.00 0.0\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("ATTENTION: missing_history.json lists "
                          "XYZ260619C00050000.TO / margin", r.stdout)


# ------------------------------------------------------- A2-0111 / A2-0305

class TestNativeBooksApplyMissingHistory(unittest.TestCase):
    """A2-0111 / A2-0305 (R1-275 / R1-322): the native raw-gains pass
    applies missing_history.json, so holdings.toml carries no false short
    and `taxjson gains` shows no realized gain for the tainted sale."""

    @rule("CA-ACB-11")
    def test_holdings_and_native_gains_agree_with_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, missing_history=[{"symbol": "ZZZ.TO",
                                            "account": "margin"}])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            raw = json.loads((root / "work" / "margin_raw_gains.json")
                             .read_text())
            syms = [g.get("symbol") for g in raw.get("transactions", [])
                    if "gain" in g]
            self.assertNotIn("ZZZ.TO", syms)
            self.assertIn("XEI.TO", syms)
            self.assertEqual(
                [m.get("symbol") for m in raw["manual_reporting_required"]],
                ["ZZZ.TO"])
            inv = {i["symbol"]: i["qty"] for i in raw.get("inventory", [])}
            self.assertAlmostEqual(inv.get("ZZZ.TO", 0.0), 100.0)
            toml = (root / "reports" / "margin_holdings.toml").read_text()
            self.assertNotIn("-100", toml)
            self.assertIn("ZZZ", toml)
            g = _run_cli(root, "gains", "--json")
            self.assertEqual(g.returncode, 0, g.stderr)
            rows = json.loads(g.stdout)["rows"]
            self.assertEqual([x["symbol"] for x in rows], ["XEI.TO"])
            self.assertIn("1 sale(s) with unknown cost", g.stderr)

    def test_without_missing_history_the_native_view_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            g = _run_cli(root, "gains", "--json")
            self.assertEqual(sorted(x["symbol"] for x in
                                    json.loads(g.stdout)["rows"]),
                             ["XEI.TO", "ZZZ.TO"])
            self.assertNotIn("unknown cost", g.stderr)


# ------------------------------------------------------------------ A2-0312

class TestWriteMissingHistoryKeepsAReviewedFile(unittest.TestCase):
    """A2-0312: --write-missing-history never rewrites an existing
    (reviewed) missing_history.json without --force, and --force keeps a
    .bak."""

    def test_refuses_then_force_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            ph = root / "missing_history.json"
            reviewed = json.dumps([{"symbol": "HAND.TO",
                                    "account": "margin"}])
            ph.write_text(reviewed)
            r = _run_cli(root, "find-missing-history",
                         "--write-missing-history", str(ph))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("already exists", r.stderr)
            self.assertEqual(ph.read_text(), reviewed)
            r = _run_cli(root, "find-missing-history",
                         "--write-missing-history", str(ph), "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((root / "missing_history.json.bak").read_text(),
                             reviewed)
            self.assertEqual([e["symbol"] for e in
                              json.loads(ph.read_text())], ["ZZZ.TO"])

    def test_new_file_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input").returncode,
                             0)
            out = root / "cand.json"
            r = _run_cli(root, "find-missing-history",
                         "--write-missing-history", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([e["symbol"] for e in
                              json.loads(out.read_text())], ["ZZZ.TO"])

    def test_standalone_suggest_refuses_a_non_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2025-01-10", "ZZZ.TO", -100, 30.0,
                    3000.0).to_dict()]}))
            out = Path(tmp) / "missing_history.json"
            out.write_text("[]\n")
            r = _run_mod("taxjson.bin.taxjson_gains", "--country", "canada",
                         "--suggest-missing-history", out, base)
            self.assertEqual(r.returncode, 2)
            self.assertIn("already exists", r.stderr)
            self.assertEqual(out.read_text(), "[]\n")


# ------------------------------------------------------------------ A2-0313

class TestDanglingProjectMap(unittest.TestCase):
    """A2-0313: a project map that is a dangling symlink is unreadable,
    not absent — the run stops."""

    def test_dangling_symlinks_stop_the_run(self):
        for name in ("ticker.map", "missing_history.json", "phantoms.json",
                     "distributions.map", "crypto_ticker.map"):
            with self.subTest(name=name), \
                    tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp)
                (root / name).symlink_to(root / "nowhere" / name)
                r = _run_cli(root, "run", "--no-input")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(f"{name} is a symlink", r.stderr)
                self.assertIn("nothing was run", r.stderr)

    def test_live_symlink_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            shared = root / "shared.map"
            shared.write_text("GLOBAL ZZQ.TO ZZR.TO\n")
            (root / "ticker.map").symlink_to(shared)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])


# ------------------------------------------------------------------ A2-0032

_QT_US_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
                 "Quantity,Price,Gross Amount,Commission,Net Amount,"
                 "Currency,Activity Type,Account #,Account Type\n")
_QA = _QT_US_HEADER + (
    "2026-02-02,2026-02-03,Buy,XYZ,XYZ CORP,100,50.00,-5000.00,0.00,"
    "-5000.00,USD,Trades,55500001,Margin\n"                    # pii-ok
    "2026-03-02,2026-03-02,TF6,XYZ,XYZ CORP TRANSFER OUT,-100,0.00,0.00,"
    "0.00,0.00,USD,Transfers,55500001,Margin\n")               # pii-ok
_QB = _QT_US_HEADER + (
    "2026-03-03,2026-03-03,TF6,XYZ,XYZ CORP TRANSFER BOOK VALUE 5000.00,"
    "100,0.00,0.00,0.00,0.00,USD,Transfers,55500002,Margin\n"  # pii-ok
    "2026-04-01,2026-04-02,Sell,XYZ,XYZ CORP,-100,40.00,4000.00,0.00,"
    "4000.00,USD,Trades,55500002,Margin\n")                    # pii-ok


def _sidecar(cache, acct, rows):
    (cache / f"{acct}_questrade_transfers.json").write_text(json.dumps({
        "metadata": {"account": acct, "brokerage": "questrade",
                     "kind": "transfer_sidecar"},
        "transactions": rows}))


class TestOwnAccountCustodyMove(unittest.TestCase):
    """A2-0032: a US custody move between two of your own taxable
    accounts carries the lots (basis and purchase dates) to the
    receiving account — the stopgap ATTENTION + --strict stop is gone
    (owner request). Canada pools the ACB across the accounts (s.47):
    no legs are written."""

    def _cache(self, tmp):
        cache = Path(tmp)
        _sidecar(cache, "qa", [{"action": "TRANSFER", "symbol": "XYZ.US",
                                "quantity": -100.0, "date": "2026-03-02"}])
        _sidecar(cache, "qb", [{"action": "TRANSFER", "symbol": "XYZ.US",
                                "quantity": 100.0, "date": "2026-03-03"}])
        return cache

    def test_pairs_out_and_in_legs(self):
        from taxjson.bin.taxjson_run import own_account_custody_moves
        with tempfile.TemporaryDirectory() as tmp:
            moves = own_account_custody_moves(["qa", "qb"],
                                              self._cache(tmp))
        self.assertEqual([(m["symbol"], m["from"], m["to"], m["qty"])
                          for m in moves], [("XYZ.US", "qa", "qb", 100.0)])

    def test_unrelated_legs_are_not_a_move(self):
        from taxjson.bin.taxjson_run import own_account_custody_moves
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            _sidecar(cache, "qa", [{"action": "TRANSFER",
                                    "symbol": "XYZ.US", "quantity": -100.0,
                                    "date": "2026-03-02"}])
            _sidecar(cache, "qb", [
                {"action": "TRANSFER", "symbol": "XYZ.US",
                 "quantity": 50.0, "date": "2026-03-03"},
                {"action": "TRANSFER", "symbol": "XYZ.US",
                 "quantity": 100.0, "date": "2026-06-03"}])
            self.assertEqual(own_account_custody_moves(["qa", "qb"], cache),
                             [])

    @rule("US-BASIS-05")
    @rule_absent("US-BASIS-05", country="canada")
    def test_legs_are_written_in_the_us_only(self):
        from taxjson.bin import taxjson_run
        cfg = {"accounts": {"qa": {"type": "taxable"},
                            "qb": {"type": "taxable"}}}
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            for country, expect in (("usa", True), ("canada", False)):
                out = io.StringIO()
                with redirect_stderr(io.StringIO()), \
                        contextlib.redirect_stdout(out):
                    moves = taxjson_run.stage_own_account_moves(
                        Path(tmp), cfg, {"country": country}, cache,
                        strict=True)
                self.assertEqual(bool(moves), expect, country)
                self.assertEqual((cache / "qb_own_moves.json").exists(),
                                 expect, country)
                self.assertEqual("own-account move: XYZ.US 100 qa -> qb"
                                 in out.getvalue(), expect, country)

    @rule("US-BASIS-05")
    def test_us_run_carries_the_lot_and_strict_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = []\n'
                '[accounts.qa]\ntype = "taxable"\n'
                '[accounts.qb]\ntype = "taxable"\n')
            for a, csv in (("qa", _QA), ("qb", _QB)):
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / "questrade.csv").write_text(csv)
            r = _run_cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("ATTENTION", r.stderr)
            qb = json.loads((root / "work" / "qb_gains_wash.json")
                            .read_text())
            sale = [t for t in qb["transactions"] if t.get("qty")]
            self.assertEqual([(round(t["gain"], 2), t["acquired_date"])
                              for t in sale], [(-1000.0, "2026-02-02")])
            qa = json.loads((root / "work" / "qa_gains_wash.json")
                            .read_text())
            self.assertEqual(qa.get("inventory") or [], [])


if __name__ == "__main__":
    unittest.main()
