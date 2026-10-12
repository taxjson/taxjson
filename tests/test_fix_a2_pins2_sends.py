"""Re-audit-2 test pins (tests-pins-05, crypto-sends group): branches no
test failed on when they were removed.

  A2-0875  the checklist's refused-gift and stale-.tt attention; the run
           prompt refuses `gift` in a US project; the US .tt header; the
           USD pool disposes an unmatched send's fee; a hand-written BUY
           is not a duplicate of a generated sale
  A2-1542  the checklist's undecided-send attention; the run prompt is
           not offered while another crypto account's evidence is stale;
           the .tt network-fee header and 'already booked' comment

All data is synthetic.
"""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tax_rules import rule

from taxjson.lib import checklist as cl
from taxjson.lib import crypto_sends as cs


# ------------------------------------------------------------- checklist
def _report(**acct):
    a = {"sends": [], "undecided": 0, "tt_file": "/nonexistent.tt",
         "overridden": [], "cross_account_moves": []}
    a.update(acct)
    return {"accounts": {"coins": a}}


class TestChecklistCryptoSends(unittest.TestCase):
    """d_crypto_sends: `if refused:` (A2-0875), `if cs.tt_stale_ids(...)`
    (A2-0875) and `if a["undecided"]:` (A2-1542) each keep the step
    open."""

    def _status(self, country, report, stale_ids=()):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "coins_kraken_transfers.json").write_text("[]")
            cfg = {"settings": {"year": 2025, "country": country,
                                "base_currency": "USD" if country == "usa"
                                else "CAD"},
                   "accounts": {"coins": {"type": "taxable",
                                          "crypto": True}}}
            ctx = cl.Ctx(root=root, cfg=cfg, year=2025,
                         today=date(2026, 3, 1),
                         run_sub=lambda argv, timeout=900: (0, "", ""))
            with mock.patch.object(cs, "stale_evidence",
                                   return_value=[]), \
                    mock.patch.object(cs, "build_report",
                                      return_value=report), \
                    mock.patch.object(cs, "tt_stale_ids",
                                      return_value=list(stale_ids)), \
                    mock.patch.object(cs, "duplicate_lines",
                                      return_value=[]):
                r = cl.d_crypto_sends(ctx)
        return r.status, r.detail

    def _send(self, **kw):
        e = {"id": "kr-20250304T120000-SOL-50", "decision": "self",
             "stable": False}
        e.update(kw)
        return e

    def test_all_decided_is_done(self):
        st, det = self._status("canada", _report(sends=[self._send()]))
        self.assertEqual(st, "done", det)

    @rule("US-SEND-02")
    def test_refused_us_gift_is_attention(self):
        rep = _report(sends=[self._send(decision="gift", refused=True)])
        st, det = self._status("usa", rep)
        self.assertEqual(st, "attention", det)
        self.assertIn("which a US project refuses", det)

    @rule("CA-CRYPTO-07")
    def test_stale_tt_is_attention(self):
        rep = _report(sends=[self._send(decision="gift")])
        st, det = self._status("canada", rep,
                               stale_ids=["kr-20250304T120000-SOL-50"])
        self.assertEqual(st, "attention", det)
        self.assertIn("crypto_sends.tt out of date for coins", det)

    @rule("CA-CRYPTO-07")
    def test_undecided_is_attention(self):
        rep = _report(sends=[self._send(decision=None)], undecided=1)
        st, det = self._status("canada", rep)
        self.assertEqual(st, "attention", det)
        self.assertIn("undecided send(s) — coins: 1", det)


# ------------------------------------------------------ run's prompt guard
class TestRunPromptGuards(unittest.TestCase):
    """_stage_crypto_sends: `allow_gift=command_country_problem(...) is
    None` (A2-0875) and `... and interactive and not unparsed`
    (A2-1542)."""

    def _stage(self, country, unparsed=()):
        from taxjson.bin import taxjson_run as R
        calls = []

        def prompt(sends, manifest, **kw):
            calls.append(kw)
            return 0
        adoc = {"undecided": 1, "sends": [{"id": "x"}],
                "manifest": "/nonexistent/sends.json"}
        cfg = {"settings": {"year": 2025, "country": country,
                            "base_currency": "USD" if country == "usa"
                            else "CAD"},
               "accounts": {"coins": {"type": "taxable", "crypto": True}}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            with mock.patch.object(R, "_soft_config", return_value=cfg), \
                    mock.patch.object(R, "_crypto_broker_files",
                                      return_value={}), \
                    mock.patch.object(R, "_crypto_sends_problems",
                                      return_value=[]), \
                    mock.patch.object(R, "_crypto_sends_tt",
                                      return_value=("", [])), \
                    mock.patch.object(cs, "stale_evidence",
                                      return_value=list(unparsed)), \
                    mock.patch.object(cs, "yahoo_usd_price",
                                      return_value=None), \
                    mock.patch.object(cs, "build_report",
                                      return_value={"accounts":
                                                    {"coins": adoc}}), \
                    mock.patch.object(cs, "prompt_undecided",
                                      side_effect=prompt), \
                    mock.patch("sys.stderr"):
                R._stage_crypto_sends(root, "coins", True)
        return calls

    @rule("US-SEND-02")
    def test_us_prompt_does_not_offer_gift(self):
        self.assertEqual(self._stage("usa"), [{"allow_gift": False}])

    @rule("CA-CRYPTO-07")
    def test_canada_prompt_offers_gift(self):
        self.assertEqual(self._stage("canada"), [{"allow_gift": True}])

    @rule("CA-CRYPTO-06")
    def test_no_prompt_while_evidence_is_stale(self):
        self.assertEqual(self._stage("canada",
                                     unparsed=["other (kraken: not "
                                               "parsed yet)"]), [])


# ------------------------------------------------------------- render_tt
def _entry(**kw):
    e = {"id": "kr-20250304T120000-SOL-50", "date": "2025-03-04",
         "time": "12:00:00", "decision": "payment", "exchange": "kraken",
         "kind": "withdrawal", "quantity": 50.0, "symbol": "SOL",
         "fee_booked": 0.0,
         "fair_value": {"price": 200.0, "currency": "CAD",
                        "source": "test"},
         "tt": "BUYSELL 2025-03-04 12:00:00 SOL -50 CAD 200 10000 0"}
    e.update(kw)
    return e


class TestRenderTt(unittest.TestCase):
    """render_tt: the US header (A2-0875), the network-fee header and
    the 'already booked' comment (A2-1542)."""

    @rule("US-SEND-02")
    @rule("CA-CRYPTO-05")
    def test_us_header_never_calls_a_gift_a_disposition(self):
        us = cs.render_tt("coins", [_entry()], country="usa")
        ca = cs.render_tt("coins", [_entry()], country="canada")
        self.assertIn("a gift is not a sale for a US donor", us)
        self.assertNotIn("s.69(1)(b)", us)
        self.assertIn("a gift: ITA s.69(1)(b)", ca)
        self.assertNotIn("US donor", ca)

    @rule("CA-CRYPTO-06")
    def test_network_fee_header(self):
        fee = _entry(id="kr-20250304T120000-SOL-50-fee", network_fee=True,
                     sent=50.0, arrived=49.9, arrived_on="coinbase",
                     quantity=0.1,
                     tt="BUYSELL 2025-03-04 12:00:00 SOL -0.1 CAD 200 20 0")
        with_fee = cs.render_tt("coins", [fee], country="canada")
        plain = cs.render_tt("coins", [_entry()], country="canada")
        self.assertIn("# A `-fee` line is the network fee hidden in a "
                      "send that arrived short on", with_fee)
        self.assertNotIn("`-fee` line", plain)

    @rule("CA-CRYPTO-03")
    def test_fee_already_booked_comment(self):
        out = cs.render_tt("coins", [_entry(fee_booked=0.01)],
                           country="canada")
        self.assertIn("#   the 0.01 SOL network fee is already booked "
                      "from the ledger", out)
        self.assertNotIn("already booked",
                         cs.render_tt("coins", [_entry()],
                                      country="canada"))


# ------------------------------------------------------------- usd_pool
class TestUsdPoolSendFee(unittest.TestCase):
    """usd_pool: an unmatched send's own fee leaves the pool
    (`if e.get("fee"): dispose(...)` on the out-leg, A2-0875)."""

    @rule("CA-CRYPTO-08")
    def test_fee_of_an_unmatched_send_is_disposed(self):
        from test_fix_x_mut_crypto_sends import _flow, _rates, _s
        a = _s(exchange="kraken", date="2025-02-01", time="13:00:00",
               symbol="USDC", quantity=-50.0, fee=5.0)
        b = _s(exchange="kraken", date="2025-02-02", time="13:00:00",
               symbol="USDC", quantity=-50.0)
        ids = {id(t): cs.send_id(t["exchange"], t["date"], t["time"],
                                 t["symbol"], t["quantity"])
               for t in (a, b)}
        res = cs.usd_pool([_flow("2025-01-15", 100.0, -130.0)], [a, b],
                          [], ids, _rates())
        rb = res["results"][ids[id(b)]]
        # 100 units: A takes 50 and its fee 5, so B finds 45 and is
        # 5 units overdrawn.
        self.assertTrue(rb["overdrawn"], rb)
        self.assertEqual(res["overdrafts"], 1)
        self.assertAlmostEqual(res["units_now"], 0.0)


# ------------------------------------------------------- duplicate_lines
class TestDuplicateLinesIgnoreBuys(unittest.TestCase):
    """duplicate_lines: `if q < 0` — a hand-written BUY of the same coin
    and quantity does not sell the send again (A2-0875)."""

    @rule("CA-CRYPTO-07")
    def test_buy_is_not_a_duplicate(self):
        e = {"id": "kr-x", "date": "2026-05-04", "time": "22:00:00",
             "symbol": "SOL", "quantity": 10.0}
        with tempfile.TemporaryDirectory() as tmp:
            acct = Path(tmp) / "inputs" / "a"
            acct.mkdir(parents=True)
            (acct / "hand.tt").write_text(
                "BUYSELL 2026-05-04 22:00:00 SOL 10 CAD 100 -1000 0\n")
            self.assertEqual(cs.duplicate_lines(acct, [e]), [])
            (acct / "hand.tt").write_text(
                "BUYSELL 2026-05-04 22:00:00 SOL -10 CAD 100 1000 0\n")
            self.assertEqual(len(cs.duplicate_lines(acct, [e])), 1)


if __name__ == "__main__":
    unittest.main()
