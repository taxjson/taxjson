"""lib/pipeline: the single definition of "a gains run".

Covers: GainsRequest's country-aware defaults, run_gains parity with the
taxjson-gains CLI (same inputs → byte-identical JSON), prepare_books'
transfer/phantom handling, explain agreeing with the pipeline on a
self-cancelling-transfer scenario, and the web what-if surfacing a
corrupt phantoms.json as a warning instead of a silent pass.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.pipeline import (GainsRequest, TransferValidationError,
                                  prepare_books, run_gains)

REPO_ROOT = Path(__file__).resolve().parent.parent


def tx(action="BUYSELL", date="2025-01-15", symbol="AAA.TO", qty=0.0,
       net=0.0, account="margin", **extra):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "09:30:00", "symbol": symbol, "quantity": qty,
         "net_amount": net, "currency": "CAD", "account": account}
    d.update(extra)
    return d


class TestGainsRequestDefaults(unittest.TestCase):
    def test_tax_date_country_aware(self):
        self.assertEqual(GainsRequest(country="canada").effective_tax_date(),
                         "settle")
        self.assertEqual(GainsRequest(country="ca").effective_tax_date(),
                         "settle")
        self.assertEqual(GainsRequest(country="usa").effective_tax_date(),
                         "trade")
        self.assertEqual(GainsRequest(country="us").effective_tax_date(),
                         "trade")
        self.assertEqual(GainsRequest(country="usa",
                                      tax_date="settle").effective_tax_date(),
                         "settle")                      # explicit wins

    def test_detect_wash_policy(self):
        self.assertFalse(GainsRequest().effective_detect_wash())
        self.assertTrue(GainsRequest(taxable=True).effective_detect_wash())
        self.assertFalse(GainsRequest(taxable=True,
                                      no_wash=True).effective_detect_wash())
        self.assertTrue(GainsRequest(detect_wash=True).effective_detect_wash())
        self.assertFalse(GainsRequest(taxable=True,
                                      detect_wash=False).effective_detect_wash())


class TestPrepareBooks(unittest.TestCase):
    def _t(self, **kw):
        return TaxTransaction(**tx(**kw))

    def test_sheltered_unmatched_transfer_becomes_acquisition(self):
        # An in-kind contribution into a registered account arrives as a
        # lone TRANSFER-in. Stripping it hid the canonical permanently-
        # denied superficial loss from the wash walk (2026-09 audit) —
        # it is now rewritten to BUYSELL so the trigger fires.
        main = [self._t(qty=100, net=1000.0)]
        shel = [self._t(action="TRANSFER", qty=50, net=500.0,
                        account="rrsp")]
        with redirect_stderr(io.StringIO()):
            _, shel_out, _, _ = prepare_books(main, shel, taxable=False,
                                              phantom_hint=False)
        self.assertEqual([t.action for t in shel_out], ["BUYSELL"])
        self.assertEqual(shel_out[0].quantity, 50)

    def test_sheltered_custody_pair_still_netted(self):
        # A same-account broker move in the sheltered context stays
        # invisible to the wash walk — it acquires nothing.
        main = [self._t(qty=100, net=1000.0)]
        shel = [self._t(action="TRANSFER", qty=-50, net=500.0,
                        account="rrsp", date="2025-03-01"),
                self._t(action="TRANSFER", qty=50, net=0.0,
                        account="rrsp", date="2025-03-04")]
        with redirect_stderr(io.StringIO()):
            _, shel_out, _, _ = prepare_books(main, shel, taxable=False,
                                              phantom_hint=False)
        self.assertEqual(shel_out, [])

    def test_sheltered_custody_pair_near_loss_survives(self):
        # 2026-09 audit finding 4: a same-account contribution+
        # withdrawal pair INSIDE a main-book loss window is
        # byte-identical to a custody move but must NOT be netted —
        # the legs survive (as transfer_rewrite BUYSELLs) so the
        # engine's AmbiguousTransferDateError can force a declaration.
        main = [self._t(qty=100, net=10000.0, date="2025-02-01"),
                self._t(qty=-100, net=8000.0, date="2025-03-02")]
        shel = [self._t(action="TRANSFER", qty=50, net=4000.0,
                        account="rrsp", date="2025-03-06"),
                self._t(action="TRANSFER", qty=-50, net=4000.0,
                        account="rrsp", date="2025-03-10")]
        with redirect_stderr(io.StringIO()):
            _, shel_out, _, _ = prepare_books(main, shel, taxable=False,
                                              phantom_hint=False)
        self.assertEqual([(t.action, t.type) for t in shel_out],
                         [("BUYSELL", "transfer_rewrite")] * 2)

    def test_sheltered_cross_account_move_netted(self):
        # rrsp -> rrsp2: moving your own shares between registered
        # accounts acquires nothing; treating the receiving leg as a
        # buy would fabricate wash triggers.
        main = [self._t(qty=100, net=1000.0)]
        shel = [self._t(action="TRANSFER", qty=-50, net=500.0,
                        account="rrsp", date="2025-03-01"),
                self._t(action="TRANSFER", qty=50, net=500.0,
                        account="rrsp2", date="2025-03-03")]
        with redirect_stderr(io.StringIO()):
            _, shel_out, _, _ = prepare_books(main, shel, taxable=False,
                                              phantom_hint=False)
        self.assertEqual(shel_out, [])

    def test_self_cancelling_pair_dropped(self):
        main = [
            self._t(qty=100, net=1000.0, date="2025-01-02"),
            self._t(action="TRANSFER", qty=-100, date="2025-03-01"),
            self._t(action="TRANSFER", qty=100, date="2025-03-05"),
        ]
        with redirect_stderr(io.StringIO()):
            out, _, _, _ = prepare_books(main, [], taxable=False,
                                         phantom_hint=False)
        self.assertEqual([t.action for t in out], ["BUYSELL"])

    def test_remaining_transfer_rewritten_when_not_taxable(self):
        main = [self._t(action="TRANSFER", qty=100, net=1000.0)]
        with redirect_stderr(io.StringIO()):
            out, _, _, _ = prepare_books(main, [], taxable=False,
                                         phantom_hint=False)
        self.assertEqual(out[0].action, "BUYSELL")
        self.assertAlmostEqual(out[0].net_amount, 1000.0)

    def test_taxable_transfer_hard_errors(self):
        # Typed error, not SystemExit: lib code must never kill its host
        # process (the web server calls prepare_books too).
        main = [self._t(action="TRANSFER", qty=100, net=1000.0)]
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(TransferValidationError):
                prepare_books(main, [], taxable=True, phantom_hint=False)

    def test_phantoms_applied(self):
        with tempfile.TemporaryDirectory() as td:
            ph = Path(td) / "phantoms.json"
            ph.write_text(json.dumps(
                [{"symbol": "AAA.TO", "account": "margin"}]))
            # Sell with no prior buy — the phantom file covers it.
            main = [self._t(qty=-100, net=1500.0)]
            with redirect_stderr(io.StringIO()):
                out, _, _, log = prepare_books(main, [], taxable=True,
                                               incomplete_history=ph,
                                               phantom_hint=False)
        self.assertTrue(any(t.action == "OPENING_BALANCE" for t in out))
        self.assertTrue(log)


class TestRunGainsParityWithCli(unittest.TestCase):
    """The CLI is run_gains + json.dump — this pins the shim's flag→request
    wiring by running a rich scenario through both and comparing dicts."""

    SCENARIO = [
        # Prior-year buy, in-year wash-sale loss + rebuy, dividend, option
        # with a fee, and a next-year sale (year filter must trim it).
        tx(date="2024-06-01", qty=200, net=20000.0),
        tx(date="2025-03-03", qty=-100, net=7000.0),          # loss 3000
        tx(date="2025-03-20", qty=50, net=3600.0),            # wash trigger
        tx(action="DIVIDEND", date="2025-04-01", qty=0, net=55.0,
           gross_amount=55.0, type="dividend"),
        tx(date="2025-05-05", symbol="AAA250620C00080000.TO", qty=-1,
           net=200.0, fee=1.5),
        tx(date="2025-06-20", symbol="AAA250620C00080000.TO", qty=1,
           net=0.0),
        tx(date="2026-02-01", qty=-50, net=5500.0),           # out of year
    ]
    SHELTERED = [
        tx(action="TRANSFER", date="2025-01-05", symbol="BBB.TO", qty=10,
           net=100.0, account="rrsp"),
        tx(date="2025-03-10", symbol="AAA.TO", qty=25, net=1800.0,
           account="rrsp"),
    ]

    def test_identical_output(self):
        with tempfile.TemporaryDirectory() as td:
            main_f = Path(td) / "main.json"
            shel_f = Path(td) / "shel.json"
            main_f.write_text(json.dumps({"transactions": self.SCENARIO}))
            shel_f.write_text(json.dumps({"transactions": self.SHELTERED}))

            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT / "src")
            proc = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2025", "--taxable",
                 "--sheltered", str(shel_f), str(main_f)],
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            cli_out = json.loads(proc.stdout)

            from taxjson.lib.core import load_transactions
            with redirect_stderr(io.StringIO()):
                lib_out = run_gains(
                    load_transactions(main_f), load_transactions(shel_f), [],
                    GainsRequest(country="canada", year=2025, taxable=True))
        self.assertEqual(cli_out, lib_out)
        # Sanity: the scenario actually exercised the interesting paths.
        self.assertGreater(lib_out["summary"].get("total_disallowed", 0), 0)
        self.assertEqual(lib_out["summary"]["year"], "2025")


class TestExplainAgreesWithPipeline(unittest.TestCase):
    def test_self_cancelling_pair_invisible_in_both(self):
        """A net-zero TRANSFER journal pair must be a no-op in explain
        exactly as it is in the gains pipeline (previously explain
        rewrote the pair to BUYSELLs and traced a spurious round trip)."""
        txs = [
            tx(date="2025-01-02", qty=100, net=1000.0),
            tx(date="2025-02-01", qty=-100, net=1400.0),      # real gain
            tx(action="TRANSFER", date="2025-03-01", symbol="CCC.TO",
               qty=-40),
            tx(action="TRANSFER", date="2025-03-05", symbol="CCC.TO",
               qty=40),
        ]
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "txs.json"
            f.write_text(json.dumps({"transactions": txs}))
            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT / "src")
            proc = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_explain",
                 "--country", "canada", "--list", str(f)],
                capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("AAA.TO", proc.stdout)
        self.assertNotIn("CCC.TO", proc.stdout)   # journal pair = no-op


class TestWhatIfWarnings(unittest.TestCase):
    def _project(self, tmp, *, phantoms_text=None):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "reports").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_base.json").write_text(json.dumps(
            {"transactions": [tx(date="2025-01-02", qty=100,
                                 net=1000.0)]}))
        if phantoms_text is not None:
            (root / "phantoms.json").write_text(phantoms_text)
        return root

    def test_corrupt_phantoms_warns_instead_of_silent(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, phantoms_text="{not valid json")
            ctx = ProjectContext.load(root)
            with redirect_stderr(io.StringIO()):
                r = data.what_if_sell(ctx, "margin", "AAA.TO", 50, 15.0,
                                      on="2026-07-01")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["warnings"])
        self.assertIn("phantoms.json", r["warnings"][0])

    def test_clean_run_has_empty_warnings(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            ctx = ProjectContext.load(root)
            with redirect_stderr(io.StringIO()):
                r = data.what_if_sell(ctx, "margin", "AAA.TO", 50, 15.0,
                                      on="2026-07-01")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["warnings"], [])




class TestTransferErrorCliContract(unittest.TestCase):
    """The typed TransferValidationError still surfaces as exit 1 + stderr
    `error:` from the taxjson-gains CLI (the old sys.exit contract)."""

    def test_cli_exits_1_with_error_on_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            main_f = Path(td) / "main.json"
            main_f.write_text(json.dumps({"transactions": [
                tx(action="TRANSFER", date="2025-01-15", qty=100,
                   net=1000.0),
            ]}))
            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT / "src")
            proc = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--taxable", str(main_f)],
                capture_output=True, text=True, env=env)
            # 2 since the FUZZ #K hardening: invalid input data
            # exits 2 (usage/data convention), same as NaN and
            # impossible-date inputs.
            self.assertEqual(proc.returncode, 2)
            self.assertIn("error:", proc.stderr)
            self.assertIn("TRANSFER", proc.stderr)


if __name__ == "__main__":
    unittest.main()
