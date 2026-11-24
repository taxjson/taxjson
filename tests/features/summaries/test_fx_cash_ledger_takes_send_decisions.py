"""The FX-on-cash ledger v2 takes a stablecoin send's crypto-sends
decision (backlog TJ-4953). In a Canadian project a USD stablecoin is
US-dollar cash, so a Kraken USDC withdrawal is a cash move the ledger
asks about ("went out ... and nothing says what it was"), while
`tjs crypto-sends` already holds the owner's decision of the same send
in inputs/<account>/sends.json. The decision was written twice and
nothing checked the two against each other.

The ledger and `tjs cash` now take the decision where no `.tt` CASHMOVE
line declares the move: a gift or a payment at the day's rate (`spot`,
the value crypto-sends shows), a `self` send `kept`. A line of the same
side (still yours: own, kept; a disposition: spot, proceeds=) overrides
it; one of the other side is refused, both named. Sends alike to the
second carrying different decisions give none.

All data synthetic.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cash_events as CE

H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance,"
     "amountusd,feeusd,feecurrency\n")
# 2000 USDC bought for 2800 CAD (1.40 each); 300 sent out on 2026-03-10
# (11:00 in Toronto), when a US dollar is 1.35.
LEDGER = H + (
    "L01,R01,2026-01-05 15:00:00,deposit,,currency,ZCAD,spot / main,5000,0,5000,3600,0,CAD\n"
    "L02,RT1,2026-01-12 15:00:00,spend,,currency,ZCAD,spot / main,-2800,0,2200,-2000,0,CAD\n"
    "L03,RT1,2026-01-12 15:00:00,receive,,currency,USDC,spot / main,2000,0,2000,2000,0,USDC\n"
    "L04,RW1,2026-03-10 15:00:00,withdrawal,,currency,USDC,spot / main,-300,0,1700,-300,0,USDC\n")
SID = "kr-20260310T110000-USDC-300"
ASKED = "-300.00 USD went out on 2026-03-10 (Kraken withdrawal USDC, kr_ledgers.csv:5)"


class TestTheLedgerTakesTheSendsDecision(unittest.TestCase):
    def _cli(self, root, *args):
        from taxjson.lib.dispatch import python_module_argv
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run", ["-C", str(root), *args]),
            cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900,
            env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0", NO_COLOR="1",
                     TAXJSON_LOCAL_TZ="America/Toronto"))

    def _ledger(self, decision=None, tt="", more="", also=None):
        """`tjs fx-cash --ledger v2 --cash-events` and `tjs cash` of a
        project whose one send is decided `decision` (None: undecided),
        with the .tt lines `tt` and the ledger rows `more` after its own:
        (the ledger's text, tjs cash's)."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "proj"
        (root / "inputs" / "crypto").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\n'
            'country = "canada"\nprovince = "ON"\nbase_currency = "CAD"\n'
            'source_currencies = ["USD"]\n'
            '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n', encoding="utf-8")
        (root / "inputs" / "crypto" / "kr_ledgers.csv").write_text(LEDGER + more,
                                                              encoding="utf-8")
        if decision:
            (root / "inputs" / "crypto" / "sends.json").write_text(json.dumps(
                {"schema_version": 2,
                 "sends": dict({SID: {"decision": decision}}, **(also or {}))}),
                encoding="utf-8")
        if tt:
            (root / "inputs" / "crypto" / "cash.tt").write_text(tt, encoding="utf-8")
        os.chmod(root, 0o700)
        r = self._cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        fx = self._cli(root, "fx-cash", "--ledger", "v2", "--cash-events")
        self.assertEqual(fx.returncode, 0, fx.stdout + fx.stderr)
        cash = self._cli(root, "cash")
        self.assertEqual(cash.returncode, 0, cash.stdout + cash.stderr)
        return fx.stdout, cash.stdout + cash.stderr

    def test_an_undecided_send_is_asked_about_as_before(self):
        out, cash = self._ledger()
        self.assertIn("NOT COMPUTED — 1 problem(s)", out)
        self.assertIn(f"crypto/kraken: {ASKED} and nothing says what it was — add `CASHMOVE "
                      f"2026-03-10 USD -300.00 kept`", out)
        self.assertRegex(cash, r"CASHMOVE\s+2026-03-10 USD\s+-300\.00 \?")

    def test_a_gift_or_a_payment_is_out_at_the_days_rate(self):
        # 300 at 1.35 = 405.00 against 300 x 1.40 = 420.00: the -15.00
        # `tjs crypto-sends` shows for the send, now in the ledger with
        # no line written for it.
        for decision in ("gift", "payment"):
            with self.subTest(decision):
                out, cash = self._ledger(decision)
                self.assertNotIn("NOT COMPUTED", out)
                self.assertNotIn("went out", out)
                self.assertRegex(out, r"NET\s+-15\.00")
                self.assertRegex(out, r"2026-03-10  crypto/kraken  USD\s+-300\.00  spot")
                self.assertRegex(
                    out, rf"CASHMOVE  -300\.00 USD spot Kraken withdrawal USDC\s+"
                         rf"inputs/crypto/sends\.json \({SID}: {decision}\)")
                self.assertRegex(
                    cash, rf"CASHMOVE\s+2026-03-10 USD\s+-300\.00 spot\s+# Kraken withdrawal "
                          rf"USDC; declared in inputs/crypto/sends\.json \({SID}: {decision}\)")

    def test_a_send_with_a_fee_is_the_same_send(self):
        # A second withdrawal, of 200 USDC with a fee of 1: the cash
        # events have the 200 as the move and the 1 as a flow, and the
        # send is of 200. Its decision is taken as any send's.
        sid = "kr-20260311T110000-USDC-200"
        out, cash = self._ledger("gift", more=(
            "L05,RW2,2026-03-11 15:00:00,withdrawal,,currency,USDC,spot / main,-200,1,1499,"
            "-200,1,USDC\n"), also={sid: {"decision": "payment"}})
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, rf"CASHMOVE  -200\.00 USD spot Kraken withdrawal USDC\s+"
                              rf"inputs/crypto/sends\.json \({sid}: payment\)")
        self.assertRegex(out, r"FLOW      -1\.00 USD Kraken withdrawal fee")
        self.assertRegex(out, r"2026-03-11  crypto/kraken  USD\s+1,499\.00\s+1,499\.00\s+0\.00")
        self.assertRegex(cash, r"CASHMOVE\s+2026-03-11 USD\s+-200\.00 spot")

    def test_a_self_send_is_kept(self):
        # Still yours, in a wallet outside the books: out at its cost, no
        # gain, the line `tjs crypto-sends` prints for it.
        out, cash = self._ledger("self")
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"NET\s+0\.00")
        self.assertRegex(out, rf"CASHMOVE  -300\.00 USD kept Kraken withdrawal USDC\s+"
                              rf"inputs/crypto/sends\.json \({SID}: self\)")
        self.assertRegex(cash, r"CASHMOVE\s+2026-03-10 USD\s+-300\.00 kept")

    def test_a_line_of_the_same_side_overrides_the_decision(self):
        # proceeds= says a disposition more exactly than `gift`'s day
        # rate: 400.00 against 420.00. `own` and `kept` both say still
        # yours, as `self` does.
        out, cash = self._ledger("gift", "CASHMOVE 2026-03-10 USD -300 proceeds=400.00\n")
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"NET\s+-20\.00")
        self.assertRegex(out, r"USD proceeds=400\.00 Kraken withdrawal USDC\s+"
                              r"inputs/crypto/cash\.tt:1")
        self.assertNotIn("sends.json", cash)
        out, _cash = self._ledger("self", "CASHMOVE 2026-03-10 USD -300 kept\n")
        self.assertNotIn("NOT COMPUTED", out)
        self.assertRegex(out, r"USD kept Kraken withdrawal USDC\s+inputs/crypto/cash\.tt:1")

    def test_a_line_of_the_other_side_is_refused_and_both_are_named(self):
        said = ("inputs/crypto/cash.tt:1: `{0}` declares the USDC sent on 2026-03-10 `{1}` "
                "({2}), and inputs/crypto/sends.json decides the same send `{3}` "
                f"({SID}: {{4}}) — one of the two is wrong: change the line, or the decision "
                f"(`tjs crypto-sends crypto --set {SID}=...`)")
        for decision, line, how, mine, theirs in (
                ("gift", "CASHMOVE 2026-03-10 USD -300 kept", "kept",
                 "still yours", "a disposition"),
                ("self", "CASHMOVE 2026-03-10 USD -300 spot", "spot",
                 "a disposition", "still yours")):
            with self.subTest(decision):
                out, cash = self._ledger(decision, line + "\n")
                text = said.format(line, how, mine, decision, theirs)
                self.assertIn("NOT COMPUTED — 1 problem(s)", out)
                self.assertIn(f"- {text}", out)
                self.assertIn(f"tjs cash: warning: {text}", cash)


    def test_two_sends_alike_that_day_with_one_decision_are_both_asked_about(self):
        # A second withdrawal of 300 USDC an hour later, undecided: the
        # cash events carry a day, not a time, so nothing says which of
        # the two rows the gift is. Neither takes it, and each question
        # says why.
        out, _cash = self._ledger("gift", more=(
            "L05,RW2,2026-03-10 16:00:00,withdrawal,,currency,USDC,spot / main,-300,0,1400,"
            "-300,0,USDC\n"))
        self.assertIn("NOT COMPUTED — 2 problem(s)", out)
        self.assertEqual(out.count(
            f" to a .tt of crypto (the account's sends.json has a decision for a send "
            f"alike to this one — {SID}: gift, kr-20260310T120000-USDC-300: undecided — "
            f"and nothing says which row each decides, so none is taken)\n"), 2)


class TestWhichMoveADecisionIsOf(unittest.TestCase):
    """`send_decisions` and `apply_declarations` on events built by hand."""

    @staticmethod
    def _move(amount=-300.0, date="2026-03-10", desc="Kraken withdrawal USDC"):
        return {"kind": "CASHMOVE", "date": date, "settle": date, "currency": "USD",
                "amount": amount, "decl": None, "desc": desc, "origin": "broker",
                "label": "crypto", "broker": "kraken", "book": "crypto/kraken"}

    @staticmethod
    def _send(sid, decision, quantity=300.0, date="2026-03-10", symbol="USDC"):
        return {"account": "crypto", "exchange": "kraken", "date": date, "symbol": symbol,
                "quantity": quantity, "id": sid, "decision": decision,
                "where": "inputs/crypto/sends.json"}

    def test_the_move_of_the_sends_account_exchange_day_coin_and_units(self):
        hit = self._move()
        others = [self._move(amount=300.0), self._move(date="2026-03-11"),
                  self._move(desc="Kraken withdrawal USDT"), self._move(amount=-300.02),
                  dict(self._move(), label="other"), dict(self._move(), broker="coinbase"),
                  dict(self._move(), origin="tt")]
        CE.send_decisions([hit] + others, [self._send("kr-1", "gift")])
        self.assertEqual(hit["send"], {"id": "kr-1", "decision": "gift", "symbol": "USDC",
                                       "where": "inputs/crypto/sends.json"})
        self.assertEqual([m for m in others if "send" in m or "send_unclear" in m], [])
        CE.apply_declarations([hit] + others, [])
        self.assertEqual(hit["decl"], {"how": "spot", "value": None})
        self.assertEqual(hit["decl_where"], "inputs/crypto/sends.json (kr-1: gift)")
        self.assertEqual([m["decl"] for m in others], [None] * len(others))

    def test_sends_alike_with_one_decision_each_take_it(self):
        moves = [self._move(), self._move()]
        CE.send_decisions(moves, [self._send("kr-1", "self"), self._send("kr-1-2", "self")])
        self.assertEqual([m["send"]["id"] for m in moves], ["kr-1", "kr-1-2"])

    def test_sends_alike_with_two_decisions_give_none(self):
        # Nothing says which row each id decides: neither is taken, and
        # the ledger's question names the ids.
        moves = [self._move(), self._move()]
        CE.send_decisions(moves, [self._send("kr-1", "gift"), self._send("kr-1-2", None)])
        CE.apply_declarations(moves, [])
        self.assertEqual([m["decl"] for m in moves], [None, None])
        self.assertEqual({m["send_unclear"] for m in moves},
                         {"kr-1: gift, kr-1-2: undecided"})

    def test_one_of_two_moves_alike_paired_with_its_arrival_gives_none(self):
        # Two withdrawals alike, one of them paired with an arrival (an
        # own move: no decision, not among the sends): the one decision
        # is not taken for either.
        moves = [self._move(), self._move()]
        CE.send_decisions(moves, [self._send("kr-1", "gift")])
        self.assertEqual([m.get("send") for m in moves], [None, None])
        self.assertEqual({m["send_unclear"] for m in moves}, {"kr-1: gift"})

    def test_the_evidence_of_a_removed_export_decides_nothing(self):
        # work/ still holds the transfer evidence of an export no longer
        # in inputs/: its send is not read (as `tjs crypto-sends` leaves
        # it out), whatever sends.json still says of it.
        from taxjson.lib import crypto_sends as CS
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "work").mkdir()
        (root / "inputs" / "crypto").mkdir(parents=True)
        (root / "work" / "crypto_kraken_transfers.json").write_text(json.dumps({
            "metadata": {"kind": "transfer_sidecar", "brokerage": "kraken"},
            "transactions": [{"action": "TRANSFER", "date": "2026-03-10", "time": "11:00:00",
                              "symbol": "USDC", "quantity": -300.0, "currency": "USD",
                              "price": 0.0, "description": "withdrawal", "id": "L04-xfer"}]}),
            encoding="utf-8")
        (root / "inputs" / "crypto" / "sends.json").write_text(json.dumps(
            {"schema_version": 2, "sends": {SID: {"decision": "gift"}}}), encoding="utf-8")
        cfg = {"settings": {"country": "canada", "base_currency": "CAD", "year": 2026},
               "accounts": {"crypto": {"type": "taxable", "crypto": True}}}
        here = CS.cash_send_decisions(root, cfg, root / "work", {("crypto", "kraken")})
        self.assertEqual([(s["id"], s["decision"]) for s in here], [(SID, "gift")])
        self.assertEqual(CS.cash_send_decisions(root, cfg, root / "work", set()), [])

    def test_an_undecided_send_marks_nothing(self):
        move = self._move()
        CE.send_decisions([move], [self._send("kr-1", None)])
        CE.apply_declarations([move], [])
        self.assertIsNone(move["decl"])
        self.assertNotIn("send_unclear", move)


if __name__ == "__main__":
    unittest.main()
