"""Re-audit-2 pins (tests-pins lists, G5): crypto-sends country gates,
the send/arrival band, the stablecoin pool's withdrawal fee, the
crypto-sends command and checklist guards, the S076-19 messages,
taxjson-diff's sub-micro tolerance and the rename fallbacks.

Each class names the finding and the code it pins; each test fails when
that code is reverted (verified by mutating the source). Synthetic data
only.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from taxjson.lib import crypto_sends as cs
from tax_rules import rule, rule_absent
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def tearDownModule():
    _WIDTH.stop()


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    _WIDTH.start()
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _s(exchange="coinbase", account="c", date="2025-06-22",
       time="10:05:10", symbol="ATOM", quantity=-1.0, **kw):
    r = {"exchange": exchange, "account": account, "date": date,
         "time": time, "symbol": symbol, "quantity": quantity,
         "price": 0.0, "currency": "", "fee": 0.0, "kind": "Send",
         "ref": ""}
    r.update(kw)
    return r


# ------------------------------------------------- build_report gates
_USDC_SIDE = [{"date": "2025-02-01", "time": "10:00:00", "symbol": "USDC",
               "quantity": -100, "description": "withdrawal"}]
_USDC_ID = "kr-20250201T100000-USDC-100"


def _sends_project(tmp, country, base):
    root = Path(tmp)
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "a").mkdir(parents=True, exist_ok=True)
    (root / "work" / "a_kraken_transfers.json").write_text(json.dumps({
        "metadata": {"kind": "transfer_sidecar", "brokerage": "kraken"},
        "transactions": _USDC_SIDE}))
    (root / "work" / "to_base.csv").write_text(
        "2025-01-01 12:00:00 USD CAD 1.40 boc\n"
        "2025-02-01 12:00:00 USD CAD 1.40 boc\n")
    (root / "inputs" / "a" / "sends.json").write_text(json.dumps(
        {"sends": {_USDC_ID: {"decision": "payment"}}}))
    settings = {"country": country}
    if base:
        settings["base_currency"] = base
    return root, {"settings": settings, "accounts": {"a": {"crypto": True}}}


_CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
              "Price Currency,Price at Transaction,Subtotal,"
              "Total (inclusive of fees and/or spread),Fees and/or Spread,"
              "Notes\n")


class TestUsStablecoinPayment(unittest.TestCase):
    """A2-0170 / A2-0506 (crypto_sends.build_report `stable = ... and
    country != "usa"`), A2-0900 / A2-1543 (`fair_value(...,
    stable_cash=country != "usa")`, the `or home_currency(country)`
    base default and the pool's `base != "USD"` gate).

    The same USDC payment: a US project writes it as a sale at par
    (stablecoins are property, US-CRYPTO-02; a payment is a sale,
    US-SEND-01); a Canada project books it as US-dollar cash
    (CA-CRYPTO-08: no sale line, a currency disposition on the pool)."""

    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "UTC"})
        p.start()
        self.addCleanup(p.stop)

    def _report(self, country, base):
        with tempfile.TemporaryDirectory() as tmp:
            root, cfg = _sends_project(tmp, country, base)
            cb = Path(tmp) / "cb.csv"
            cb.write_text(_CB_HEADER +
                          "c1,2025-01-15 12:00:00 UTC,Buy,USDC,150,CAD,"
                          "1.38,207,207,0,Bought 150 USDC\n")
            doc = cs.build_report(root, cfg, broker_files={
                "a": [("coinbase", cb)]})
        return doc, {s["id"]: s for s in doc["accounts"]["a"]["sends"]}

    @rule("US-CRYPTO-02")
    @rule("US-SEND-01")
    @rule_absent("US-CRYPTO-02", country="canada")
    @rule("CA-CRYPTO-08")
    def test_us_payment_is_a_sale_at_par_canada_keeps_cash(self):
        # A US project with NO base_currency: the base is the country's
        # own currency (USD), never CAD.
        doc, sends = self._report("usa", None)
        self.assertEqual(doc["base_currency"], "USD")
        s = sends[_USDC_ID]
        self.assertFalse(s["stable"])
        self.assertEqual(s["tt"], "BUYSELL 2025-02-01 10:00:00 USDC -100 "
                                  "USD 1 100.00 0")
        src = s["fair_value"]["source"]
        self.assertIn("USDC at its 1.00 USD par", src)
        self.assertNotIn("US-dollar cash", src)
        self.assertIsNone(s["fx"])
        # No US-dollar-cash pool in a US project (stablecoins are
        # property there): no pool summary at all.
        self.assertNotIn("pool", doc)

        doc, sends = self._report("canada", "CAD")
        s = sends[_USDC_ID]
        self.assertTrue(s["stable"])
        self.assertIsNone(s["tt"])
        self.assertIn("stablecoins are US-dollar cash",
                      s["fair_value"]["source"])
        self.assertIn("pool", doc)
        self.assertIsNotNone(s["fx"])


# ------------------------------------------------ the 100% upper bound
class TestArrivalAboveTheSend(unittest.TestCase):
    """A2-1541: crypto_sends._fits `q_in > q_out * (1 + 1e-9) + 1e-12`
    — an arrival of MORE coins than were sent is not the send's own
    move (CA-CRYPTO-06 / US-CRYPTO-05: 90% to 100% of the coins)."""

    def _paired(self, got):
        rows = [_s(quantity=-1.0, time="12:00:00"),
                _s(exchange="kraken", quantity=got, time="12:05:00")]
        _un, pairs = cs.match_transfers(rows)
        return bool(pairs)

    @rule("CA-CRYPTO-06")
    def test_canada_band_top(self):
        self.assertTrue(self._paired(1.0))
        self.assertTrue(self._paired(1.0 + 1e-10))   # float noise
        for got in (1.001, 1.005, 1.01, 1.019):
            self.assertFalse(self._paired(got), got)

    @rule("US-CRYPTO-05")
    def test_us_band_top(self):
        self.assertTrue(self._paired(1.0))
        self.assertFalse(self._paired(1.005))


# ------------------------------------------- pool: a withdrawal's fee
class TestPoolWithdrawalFee(unittest.TestCase):
    """A2-1544: crypto_sends.usd_pool, the out-leg's `if e.get("fee"):
    dispose(e["fee"], day)` — a stablecoin withdrawal's fee leaves the
    US-dollar pool too (CA-CRYPTO-08), so a later send is overdrawn."""

    @rule("CA-CRYPTO-08")
    def test_fee_leaves_the_pool(self):
        days = {}
        d = date(2025, 1, 1)
        while d <= date(2025, 3, 31):
            days[d.isoformat()] = (1.40, "boc")
            d = date.fromordinal(d.toordinal() + 1)
        rates = cs.Rates({"USD": days}, "CAD")
        flows = [{"dt": datetime(2025, 1, 15, 12), "units": 100.0,
                  "cad": -130.0, "what": "test"}]
        a = _s(exchange="kraken", date="2025-02-01", time="13:00:00",
               symbol="USDC", quantity=-50.0, fee=40.0)
        b = _s(exchange="kraken", date="2025-02-02", time="13:00:00",
               symbol="USDC", quantity=-50.0)
        ids = {id(a): "A", id(b): "B"}
        res = cs.usd_pool(flows, [a, b], [], ids, rates)
        # 100 - 50 - 40 (fee) = 10 left for B's 50: 40 overdrawn.
        rb = res["results"]["B"]
        self.assertTrue(rb["overdrawn"])
        self.assertEqual(res["overdrafts"], 1)
        # ACB 10 x 1.30 + 40 x 1.40 (overdraft at the rate) = 69.
        self.assertEqual((rb["value"], rb["acb"], rb["gain"]),
                         (70.0, 69.0, 1.0))
        self.assertEqual(res["units_now"], 0.0)


# -------------------------------------------- command / checklist guards
def _crypto_project(td, country="canada"):
    from test_fix_sends import _project
    return _project(td, country=country)


def _cli(root, home, *a):
    from test_fix_sends import _cli as c
    return c(root, home, *a)


class TestNoTransferEvidenceGuards(unittest.TestCase):
    """A2-1602: taxjson_run.cmd_crypto_sends and
    checklist.d_crypto_sends refuse when no
    work/<acct>_<exchange>_transfers.json exists (never parsed): with
    no evidence there is nothing to judge, not "nothing to decide"."""

    def _bare(self, td):
        root = Path(td) / "proj"
        (root / "inputs" / "crypto").mkdir(parents=True)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
        home = Path(td) / "home"
        home.mkdir()
        return root, home

    def test_command_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._bare(td)
            r = _cli(root, home, "crypto-sends")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("no crypto transfer evidence in work/", r.stderr)
            # Also with the exports present but never parsed.
            from test_fix_sends import KR_LEDGER
            (root / "inputs" / "crypto" / "kr_ledgers.csv").write_text(
                KR_LEDGER)
            r = _cli(root, home, "crypto-sends")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("no crypto transfer evidence in work/", r.stderr)

    def test_checklist_step_is_blocked(self):
        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            root, _home = self._bare(td)
            cfg = tomllib.loads((root / "taxjson.toml").read_text())
            ctx = cl.Ctx(root=root, cfg=cfg, year=2025,
                         today=date(2026, 9, 1),
                         run_sub=lambda argv, timeout=900: (0, "", ""))
            res = cl.d_crypto_sends(ctx)
        self.assertEqual(res.status, "blocked", res.detail)
        self.assertIn("no crypto transfer evidence", res.detail)


class TestHandPriceGuards(unittest.TestCase):
    """A2-1603: a --price must be a finite value of at least 1e-8 per
    coin — refused by the argparse type (numeric.positive_float_arg on
    crypto-sends --price) and again by crypto_sends.record_decision
    (price_problem), so neither layer alone can let -1 / 0 / inf in."""

    def test_record_decision_refuses_bad_prices(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            for bad in (-1.0, 0.0, 1e-9, float("inf"), float("nan"), True,
                        "5"):
                with self.assertRaises(ValueError, msg=repr(bad)) as cm:
                    cs.record_decision(p, "k", "payment", price=bad)
                self.assertIn("--price", str(cm.exception))
            self.assertFalse(p.exists())
            cs.record_decision(p, "k", "payment", price=1e-8)
            self.assertEqual(json.loads(p.read_text())["sends"]["k"]
                             ["price"], 1e-8)

    def test_cli_refuses_bad_prices(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _crypto_project(td)
            for bad in ("-1", "0", "inf"):
                r = _cli(root, home, "crypto-sends", "crypto", "--set",
                         "kr-20260413T173000-QZL-2.5=payment",
                         f"--price={bad}")
                self.assertEqual(r.returncode, 2, (bad, r.stderr))
                self.assertIn("argument --price: must be a finite number",
                              r.stderr)
            self.assertFalse((root / "inputs" / "crypto" /
                              "sends.json").exists())


class TestUsDecideHint(unittest.TestCase):
    """A2-1540: cmd_crypto_sends's closing hint is
    `--set ID=self|payment` in a US project (a gift is refused there,
    US-SEND-02), `self|gift|payment` in Canada."""

    @rule("US-SEND-02")
    def test_us_hint_offers_no_gift(self):
        from test_fix_sends import _cad_usd_rates_file
        with tempfile.TemporaryDirectory() as td:
            root, home = _crypto_project(td, country="usa")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            _cad_usd_rates_file(root / "work" / "to_base.csv")
            (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, home, "crypto-sends", "crypto")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Decide: taxjson crypto-sends crypto --set "
                      "ID=self|payment [--note TEXT]", r.stdout)
        self.assertNotIn("gift|", r.stdout)


class TestRunStageUnparsedPeer(unittest.TestCase):
    """A2-0935 (run half): taxjson_run._stage_crypto_sends computes
    `unparsed` (crypto_sends.stale_evidence over the OTHER crypto
    accounts) and never prompts while a peer is unparsed
    (`... and interactive and not unparsed`): a send to that peer would
    look like a gift. It notes the stale evidence in the account's
    .diag instead."""

    def test_no_prompt_while_a_peer_is_unparsed(self):
        from test_fix_sends import CB_CSV, CRYPTO_MAP_LINE, KR_LEDGER, _env, _rates_file
        from taxjson.bin import taxjson_run as tr
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            root = td / "proj"
            for a, fn, body in (("cb", "cb_2025.csv", CB_CSV),
                                ("kr", "kr_ledgers.csv", KR_LEDGER)):
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / fn).write_text(body)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                '[accounts.cb]\ntype = "taxable"\ncrypto = true\n'
                '[accounts.kr]\ntype = "taxable"\ncrypto = true\n')
            (root / "ticker.map").write_text(CRYPTO_MAP_LINE)
            (root / "work").mkdir()
            _rates_file(root / "work" / "to_base.csv")
            home = td / "home"
            home.mkdir()
            (home / ".crypto_price_cache.json").write_text(json.dumps({
                "QZL55501-2026-04-13": 1.84,
                "QZL55501-2026-01-12": 2.1}))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            # kr never parsed (a first-time run reaching cb first).
            (root / "work" / "kr_kraken_transfers.json").unlink()
            env = _env(home)
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(cs, "prompt_undecided",
                                      return_value=False) as prompt, \
                    contextlib.redirect_stderr(io.StringIO()) as err, \
                    contextlib.redirect_stdout(io.StringIO()):
                tr._stage_crypto_sends(root, "cb", interactive=True)
            prompt.assert_not_called()
            self.assertIn("not yet classified", err.getvalue())
            diag = (root / "work" / "cb_crypto_sends.diag").read_text()
        self.assertIn("the transfer evidence of kr (kraken: not parsed "
                      "yet) is not current", diag)


class TestCoinbaseNegativeDerivedTotal(unittest.TestCase):
    """A2-0935 (Coinbase half, S056-18 / R1-103): a Sell with a blank
    Total derives Subtotal - fee and KEEPS its sign — a sale whose fee
    exceeds its value nets negative proceeds, never +|x|."""

    @rule("CA-CRYPTO-01")
    def test_blank_total_fee_above_subtotal(self):
        from test_fix_crypto import _bs, _cb_row, _parse_cb
        buy = _cb_row("b1", "2026-02-01 15:00:00 UTC", "Buy", "SOL", "1",
                      "CAD", "$200.00", "$200.00", "$200.00", "$0.00")
        txs, _ = _parse_cb(buy + _cb_row(
            "b2", "2026-02-03 15:00:00 UTC", "Sell", "SOL", "-0.005",
            "CAD", "$200.00", "$1.00", "", "$3.00"))
        sell = [t for t in _bs(txs) if t["quantity"] < 0][0]
        self.assertAlmostEqual(sell["net_amount"], -2.0)


# --------------------------------------------------- S076-19 messages
class TestS07619Messages(unittest.TestCase):
    """A2-0905: the two S076-19 message fixes nothing pinned —
    find-missing-history's closing advice names the project commands
    (`taxjson find-missing-history --write-missing-history`, `taxjson run`), and
    pipeline._handle_transfers' rewrite NOTE scopes --taxable to the
    standalone `taxjson-gains` (the run never passes a flag the user
    typed)."""

    def test_missing_history_advice_names_the_project_commands(self):
        from taxjson.bin.taxjson_missing_history import main
        txs = [dict(action="BUYSELL", date="2025-06-01", time="09:30:00",
                    symbol="FOO.US", quantity=-10, net_amount=2000.0,
                    currency="USD", account="margin", description="")]
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "margin_base.json"
            f.write_text(json.dumps({"transactions": txs}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                main(["--year", "2025", str(f)])
        text = out.getvalue()
        self.assertIn("`taxjson find-missing-history "
                      "--write-missing-history` in the project", text)
        self.assertIn("into inputs/<account>/missing_history.tt", text)
        self.assertIn("then `taxjson run` (it reads them)", text)

    def test_transfer_rewrite_note_scopes_the_flag(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.pipeline import _handle_transfers
        t = TaxTransaction(action="TRANSFER", date="2025-03-03",
                           time="10:00:00", date_settle="2025-03-03",
                           symbol="XYZ.TO", quantity=10.0, price=10.0,
                           currency="CAD", net_amount=100.0,
                           account="rrsp")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out, _sh = _handle_transfers([t], [], taxable=False,
                                         country="canada")
        self.assertEqual([x.action for x in out], ["BUYSELL"])
        msg = err.getvalue()
        self.assertIn("NOTE: rewrote 1 TRANSFER row(s)", msg)
        self.assertIn("(Standalone `taxjson-gains`: pass --taxable to "
                      "reject TRANSFER rows instead.)", msg)


# ---------------------------------------------------- taxjson-diff
class TestDiffSubMicro(unittest.TestCase):
    """A2-0922 (S029-18): each half on its own — values_equal's RELATIVE
    test and _key_float's 7 significant digits below 1."""

    def test_values_equal_relative_half(self):
        from taxjson.bin.taxjson_diff import values_equal
        self.assertFalse(values_equal(9.4e-7, 5.6e-7))
        self.assertTrue(values_equal(100.0, 100.0 + 1e-9))
        self.assertTrue(values_equal(5.6e-7, 5.6e-7))

    def test_key_float_keeps_sub_micro_apart(self):
        from taxjson.bin.taxjson_diff import _key_float
        self.assertNotEqual(_key_float(9.4e-7), _key_float(5.6e-7))
        self.assertEqual(_key_float(9.4e-7), 9.4e-7)
        self.assertEqual(_key_float(100.00000001), 100.0)

    def test_quantity_outside_the_key_is_modified(self):
        rows = lambda q: {"transactions": [{  # noqa: E731
            "action": "BUYSELL", "date": "2025-01-06", "time": "10:00:00",
            "symbol": "BTC", "quantity": q, "net_amount": 0.0,
            "price": 0.0, "currency": "CAD"}]}
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
            a.write_text(json.dumps(rows(9.4e-7)))
            b.write_text(json.dumps(rows(5.6e-7)))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_diff",
                 "--summary", "--by", "action,date,symbol", str(a), str(b)],
                capture_output=True, text=True, cwd=REPO_ROOT,
                env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0 added | 0 removed | 1 modified | 0 unchanged",
                      r.stdout)


# ------------------------------------------------- rename fallbacks
class TestRenameFallbacks(unittest.TestCase):
    """A2-1613: the rename-fallback twins."""

    def test_option_on_a_renamed_underlying_is_traced(self):
        # taxjson_missing_history._rename_sources: `if
        # is_option_symbol(key)` traces an option to its underlying.
        from taxjson.bin.taxjson_missing_history import _rename_sources
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "ticker.map"
            m.write_text("GLOBAL OLDCO.US NEWCO.US\n")
            got = _rename_sources(m, ["NEWCO250620C00050000.US",
                                      "NEWCO.US", "OTHER.US"])
        self.assertEqual(got, {"NEWCO250620C00050000.US": ["OLDCO.US"],
                               "NEWCO.US": ["OLDCO.US"]})

    def test_bare_slip_symbol_tries_to_and_stays_bare(self):
        # taxjson_reconcile_slips._rename_fn: a bare slip symbol is
        # tried as .US then .TO, and the answer stays bare.
        from taxjson.bin.taxjson_reconcile_slips import _rename_fn
        fn = _rename_fn({"KGC.TO": "K.TO", "OLD.US": "NEW.US"})
        self.assertEqual(fn("KGC"), "K")
        self.assertEqual(fn("OLD"), "NEW")
        self.assertEqual(fn("KGC.TO"), "K.TO")
        self.assertEqual(fn("ZZZ"), "ZZZ")

    @rule("CA-ACB-RENAME")
    @rule("US-BASIS-RENAME")
    def test_three_hop_chain_reaches_its_fixed_point(self):
        # taxjson_ticker_map.merge_renames: `while cur in raw` — a
        # three-hop chain pools OLD into the end of the chain.
        from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                    merge_renames)
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "ticker.map"
            m.write_text("GLOBAL OLD.US MID.US\nGLOBAL MID.US NEW.US\n"
                         "TOBASE NEW.US NEW.TO\n")
            with contextlib.redirect_stderr(io.StringIO()):
                ren = merge_renames(load_map_file(m), True)
        self.assertEqual(ren["OLD.US"], "NEW.TO")
        self.assertEqual(ren["MID.US"], "NEW.TO")

    def test_ambiguous_option_root_keeps_its_spelling(self):
        # taxjson_export._resolve_underlying: `len(hits) == 1`.
        from taxjson.bin.taxjson_export import _resolve_underlying
        self.assertEqual(_resolve_underlying(
            "RCI.TO", {"RCI.A.TO", "RCI.B.TO"}), "RCI.TO")
        self.assertEqual(_resolve_underlying("RCI.TO", {"RCI.B.TO"}),
                         "RCI.B.TO")


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------- watch / sell-check scope
class TestWatchStatesItsScope(unittest.TestCase):
    """A2-0909: tax-logic CA-PLAN-04 / US-PLAN-04 name `watch` among the
    planning tools whose verdicts say they cover the project's accounts
    only, but watch printed "CLEAR — safe to sell at a loss" with no
    scope line (text or JSON). Also pins sell-check's text scope line
    (taxjson_run `print(_scope)`)."""

    def _project(self, tmp, country):
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        sym, ccy = (("XYZ.US", "USD") if country == "usa"
                    else ("XYZ.TO", "CAD"))
        root = Path(tmp) / country
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {date.today().year}\ncountry = '
            f'"{country}"\nbase_currency = "{ccy}"\n'
            f'source_currencies = []\n'
            f'[accounts.margin]\ntype = "taxable"\n')
        tt = root / "inputs" / "margin" / "m.tt"
        tt.write_text(f"BUYSELL {d(60)} 10:00:00 {sym} 100 {ccy} 10.00 "
                      f"1000.00 0.00\n")
        return root, d, sym, ccy, tt

    def _tj(self, root, *a):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), *a], cwd=REPO_ROOT, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
                 "PYTHONPATH": str(REPO_ROOT / "src")})

    def _check(self, country, must, must_not):
        with tempfile.TemporaryDirectory() as tmp:
            root, d, sym, ccy, tt = self._project(tmp, country)
            r = self._tj(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            j = json.loads(self._tj(root, "watch", "--json").stdout)
            self.assertTrue(j["baseline"])
            self.assertIn(must, j["scope_note"])
            # Quiet when nothing changed (cron): no scope line either.
            self.assertEqual(self._tj(root, "watch").stdout, "")
            with tt.open("a") as f:
                f.write(f"BUYSELL {d(1)} 10:00:00 {sym} 50 {ccy} 9.00 "
                        f"450.00 0.00\n")
            r = self._tj(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            w = self._tj(root, "watch")
            self.assertEqual(w.returncode, 0, w.stderr)
            self.assertIn("change(s) since the last run", w.stdout)
            last = w.stdout.strip().splitlines()[-1]
            self.assertTrue(last.startswith("Scope:"), w.stdout)
            self.assertIn(must, last)
            self.assertNotIn(must_not, last)
            # The default view closes with the scope in a few words;
            # --details with the whole paragraph (Essentials first).
            s = self._tj(root, "sell-check", sym)
            last = s.stdout.strip().splitlines()[-1]
            self.assertIn("not checked:", last, s.stdout)
            self.assertIn(must, last)
            self.assertNotIn(must_not, last)
            s = self._tj(root, "sell-check", sym, "--details")
            last = s.stdout.strip().splitlines()[-1]
            self.assertTrue(last.startswith("Scope:"), s.stdout)
            self.assertIn(must, last)
            self.assertNotIn(must_not, last)

    @rule("CA-PLAN-04")
    def test_canada_watch_and_sell_check_scope(self):
        self._check("canada", "s.251.1", "Pub. 550")

    @rule("US-PLAN-04")
    def test_usa_watch_and_sell_check_scope(self):
        self._check("usa", "Pub. 550", "s.251.1")

    def test_render_report_closes_with_the_scope(self):
        from taxjson.bin.taxjson_watch import render_report
        ch = [{"line": "AAA.TO: LOCKED -> CLEAR — safe to sell at a loss"}]
        out = render_report(ch, "2026-09-01", scope="Scope: x")
        self.assertEqual(out.splitlines()[-1], "Scope: x")
        self.assertNotIn("Scope", render_report(ch, "2026-09-01"))
