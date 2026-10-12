"""Kraken dust sweeps whose leg rounds to zero units (owner report,
v0.19/v0.20): a `spend`/`dustsweeping` leg of a few ten-billionths of a
coin became a BUYSELL with quantity 0, the schema refused it and the
whole crypto account stopped (`tx[947] BUYSELL ... quantity 0`).

Synthetic data only: a 16-column Kraken ledger (with amountusd) holding a
normal multi-leg sweep with a fiat (CAD) leg, a sweep with one leg under
the books' zero, an all-dust sweep and a sweep with a leg that has no
amountusd."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule

H16 = ("txid,refid,time,type,subtype,aclass,subclass,asset,wallet,"
       "amount,fee,balance,amountusd,feeusd,balanceusd,feecurrency\n")


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _row(txid, refid, when, typ, sub, asset, amount, usd, fee="0",
         cls="crypto"):
    return (f"{txid},{refid},{when},{typ},{sub},currency,{cls},{asset},"
            f"spot / main,{amount},{fee},0,{usd},0,0,\n")


def _reward(txid, when, asset, amount, usd):
    return _row(txid, "", when, "earn", "reward", asset, amount, usd)


def _sweep(refid, when, legs, receive):
    """legs: [(asset, amount, amountusd)], receive: (asset, amount, usd)."""
    out = ""
    for i, (asset, amount, usd) in enumerate(legs):
        cls = "fiat" if asset in ("CAD", "USD") else "crypto"
        out += _row(f"{refid}S{i}", refid, when, "spend", "dustsweeping",
                    asset, amount, usd, cls=cls)
    asset, amount, usd = receive
    out += _row(f"{refid}R", refid, when, "receive", "dustsweeping", asset,
                amount, usd, cls="fiat" if asset in ("CAD", "USD")
                else "crypto")
    return out


REWARDS = (_reward("RW1", "2026-01-05 12:00:00", "ADA", "20", "10.00")
           + _reward("RW2", "2026-01-05 12:00:00", "AVAX", "1", "30.00")
           + _reward("RW3", "2026-01-05 12:00:00", "BNB", "0.1", "60.00")
           + _reward("RW4", "2026-01-05 12:00:00", "SOL", "1", "100.00"))
# A normal multi-leg sweep with a fiat (CAD) leg.
NORMAL = _sweep("DSA", "2026-09-19 12:00:00",
                [("CAD", "-0.0043", "-0.003"), ("ADA", "-4.0", "-0.9"),
                 ("BNB", "-0.001", "-0.6")], ("USD", "1.5", "1.5"))
# One leg (AVAX) under the books' zero, the rest material.
ONE_DUST = _sweep("DSB", "2026-09-30 12:00:00",
                  [("CAD", "-0.01", "-0.007"), ("ADA", "-1.0", "-0.25"),
                   ("AVAX", "-0.0000000004", "-0.0000"),
                   ("BNB", "-0.0005", "-0.3")], ("USD", "0.55", "0.55"))
# Every coin leg is dust.
ALL_DUST = _sweep("DSC", "2026-10-01 12:00:00",
                  [("AVAX", "-0.0000000003", "0"),
                   ("SOL", "-0.0000000002", "0")], ("USD", "0.0001", "0.0001"))
# A dust leg with no amountusd, a material leg with one, and a receipt
# small enough that any share of it is negligible.
NO_USD = _sweep("DSD", "2026-10-02 12:00:00",
                [("ADA", "-2.0", "-0.005"), ("AVAX", "-0.0000000001", "")],
                ("USD", "0.005", "0.005"))


def _parse(text, cash=True):
    from taxjson.lib.brokerages.kraken import KrakenBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "kr_ledgers.csv"
        p.write_text(H16 + text)
        k = KrakenBrokerage()
        k.stablecoins_as_cash = cash
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = k.parse_file(p)
    return txs, buf.getvalue()


def _sales(txs, refid):
    return {t["symbol"]: t for t in txs if t["action"] == "BUYSELL"
            and str(t.get("id", "")).startswith(refid)}


class TestDustLegParse(unittest.TestCase):
    def _check(self, cash):
        from taxjson.lib.brokerages.schema import (QTY_ZERO,
                                                   validate_transactions)
        txs, err = _parse(REWARDS + NORMAL + ONE_DUST + ALL_DUST + NO_USD,
                          cash=cash)
        # No row under the books' zero reaches the books.
        for t in txs:
            if t["action"] == "BUYSELL":
                self.assertGreaterEqual(abs(t["quantity"]), QTY_ZERO, t)
        errors, _w = validate_transactions(txs)
        self.assertEqual(errors, [])

        # The normal sweep books as before: the CAD leg is cash (its
        # share is a currency conversion), each coin a sale.
        a = _sales(txs, "DSA")
        self.assertEqual(sorted(a), ["ADA", "BNB"])
        self.assertAlmostEqual(a["ADA"]["net_amount"], 1.5 * 0.9 / 1.503, 6)
        self.assertAlmostEqual(a["BNB"]["net_amount"], 1.5 * 0.6 / 1.503, 6)

        # One dust leg: dropped; the receipt is split over the others by
        # amountusd (AVAX's 0.00 share goes nowhere).
        b = _sales(txs, "DSB")
        self.assertEqual(sorted(b), ["ADA", "BNB"])
        self.assertAlmostEqual(b["ADA"]["net_amount"], 0.55 * 0.25 / 0.557, 6)
        self.assertAlmostEqual(b["BNB"]["net_amount"], 0.55 * 0.3 / 0.557, 6)
        self.assertAlmostEqual(b["ADA"]["quantity"], -1.0)

        # All dust: nothing booked from the sweep.
        self.assertEqual(_sales(txs, "DSC"), {})

        # A dust leg with no amountusd: dropped, the material leg keeps
        # the whole receipt.
        d = _sales(txs, "DSD")
        self.assertEqual(sorted(d), ["ADA"])
        self.assertAlmostEqual(d["ADA"]["net_amount"], 0.005, 6)

        # One note per sweep naming the dust (asset, amount, USD value).
        notes = [ln for ln in err.splitlines() if "under the books' zero" in ln]
        self.assertEqual(len(notes), 3, err)
        self.assertTrue(any("AVAX 0.0000000004" in n and "0.00 USD" in n
                            for n in notes), notes)
        self.assertTrue(any("AVAX 0.0000000003" in n and "SOL 0.0000000002" in n
                            for n in notes), notes)
        self.assertTrue(any("AVAX 0.0000000001" in n and "no USD value" in n
                            for n in notes), notes)
        self.assertTrue(all(n.startswith("note:") for n in notes))
        # Only a sweep with other legs says the receipt is split over them.
        split = [n for n in notes if "split over the other legs" in n]
        self.assertEqual(len(split), 2, notes)
        self.assertFalse(any("SOL" in n for n in split))
        # Refids masked.
        self.assertNotIn("DSB", "\n".join(notes))

    @rule("CA-CRYPTO-11")
    def test_canada(self):
        self._check(cash=True)

    @rule("US-CRYPTO-07")
    def test_usa(self):
        self._check(cash=False)


class TestDustSiblings(unittest.TestCase):
    """A fee or a reward under the books' zero is not a BUYSELL either."""

    @rule("CA-CRYPTO-11")
    def test_single_pair_fee_and_reward(self):
        from taxjson.lib.brokerages.schema import validate_transactions
        text = (REWARDS
                # one spend + one receive, the coin leg dust
                + _sweep("DSE", "2026-10-03 12:00:00",
                         [("AVAX", "-0.0000000005", "0")],
                         ("USD", "0.0001", "0.0001"))
                # a withdrawal whose coin fee is dust
                + _row("WD1", "FW1", "2026-10-03 13:00:00", "withdrawal",
                       "", "SOL", "-0.5", "-50", fee="0.0000000002")
                # a reward under the zero
                + _reward("RW9", "2026-10-03 14:00:00", "ADA",
                          "0.0000000007", "0"))
        txs, err = _parse(text)
        self.assertEqual(validate_transactions(txs)[0], [])
        self.assertEqual(_sales(txs, "DSE"), {})
        self.assertFalse([t for t in txs if t.get("id") == "WD1-fee"])
        self.assertFalse([t for t in txs if str(t.get("id", ""))
                          .startswith("RW9")])
        self.assertIn("AVAX 0.0000000005", err)


def _pair(refid, when, spend, receive):
    """One spend leg and one receive leg: (asset, amount, amountusd)."""
    out = ""
    for typ, (asset, amount, usd) in (("spend", spend),
                                      ("receive", receive)):
        cls = "fiat" if asset in ("CAD", "USD") else "crypto"
        out += _row(f"{refid}{typ[0].upper()}", refid, when, typ, "",
                    asset, amount, usd, cls=cls)
    return out


class TestDustOnlyWhenNegligible(unittest.TestCase):
    """Pre-release review H1 (v0.21.0): a coin leg under the books' zero
    is left out only when its VALUE is negligible too (its own amountusd
    and the share of the counter-leg it would take, each at most 0.01
    USD). A tiny quantity carrying real value is refused with a readable
    message, never dropped: a disposal, a purchase or a reward would
    vanish (or a receipt be booked with a basis from nothing)."""

    def _refused(self, text, *parts):
        with self.assertRaises(ValueError) as cm:
            _parse(text)
        msg = str(cm.exception)
        for part in ("kr_ledgers.csv",) + parts:
            self.assertIn(part, msg)
        self.assertNotIn("tx[", msg)
        return msg

    @rule("CA-CRYPTO-11")
    def test_dust_btc_spend_worth_a_lot(self):
        msg = self._refused(_pair("XAA1", "2026-03-02 12:00:00",
                                  ("BTC", "-0.0000000005", "-50000"),
                                  ("USD", "50000", "50000")),
                            "BTC", "2026-03-02", "XA***", ".tt")
        self.assertNotIn("XAA1", msg)

    @rule("US-CRYPTO-07")
    def test_dust_btc_spend_for_real_eth(self):
        self._refused(_pair("XAB1", "2026-03-03 12:00:00",
                            ("BTC", "-0.0000000005", ""),
                            ("ETH", "10", "30000")),
                      "BTC", "2026-03-03")

    @rule("CA-CRYPTO-11")
    def test_explicit_zero_spend(self):
        # v0.20 refused it (a 0-unit trade); it must not become a
        # silently dropped "dust" leg.
        msg = self._refused(_pair("XAC1", "2026-03-04 12:00:00",
                                  ("BTC", "0", "-50000"),
                                  ("USD", "50000", "50000")),
                            "BTC", "2026-03-04", "amount 0")
        self.assertNotIn("XAC1", msg)

    @rule("CA-CRYPTO-11")
    def test_explicit_zero_receive(self):
        self._refused(_pair("XAD1", "2026-03-05 12:00:00",
                            ("USD", "-100", "-100"),
                            ("ETH", "0.0000000000", "100")),
                      "ETH", "2026-03-05", "amount 0")

    @rule("CA-CRYPTO-11")
    def test_dust_spend_paying_two_receipts(self):
        text = (_row("XAE1S", "XAE1", "2026-03-06 12:00:00", "spend", "",
                     "BTC", "-0.0000000005", "0")
                + _row("XAE1R1", "XAE1", "2026-03-06 12:00:00", "receive",
                       "", "ETH", "1", "3000")
                + _row("XAE1R2", "XAE1", "2026-03-06 12:00:00", "receive",
                       "", "SOL", "10", "1500"))
        self._refused(text, "BTC", "2026-03-06")

    @rule("US-CRYPTO-07")
    def test_usd_spend_for_dust_btc(self):
        # A purchase: 50000 USD out, a few ten-billionths of a BTC in.
        self._refused(_pair("XAF1", "2026-03-07 12:00:00",
                            ("USD", "-50000", "-50000"),
                            ("BTC", "0.0000000005", "")),
                      "BTC", "2026-03-07")

    @rule("CA-CRYPTO-11")
    @rule("US-CRYPTO-07")
    def test_dust_reward_with_real_value(self):
        for cash in (True, False):
            with self.subTest(cash=cash), self.assertRaises(ValueError) as cm:
                _parse(_reward("XAG1", "2026-03-08 12:00:00", "ETH",
                               "0.0000000005", "10000"), cash=cash)
            msg = str(cm.exception)
            for part in ("kr_ledgers.csv", "ETH", "2026-03-08", "XA***",
                         "10000.00 USD", ".tt"):
                self.assertIn(part, msg)

    @rule("CA-CRYPTO-11")
    def test_dust_reward_negligible_or_unvalued_is_skipped(self):
        for usd in ("0.004", ""):
            with self.subTest(usd=usd):
                txs, err = _parse(_reward("XAH1", "2026-03-09 12:00:00",
                                          "ETH", "0.0000000005", usd))
                self.assertEqual(txs, [])
                self.assertIn("under the books' zero", err)

    @rule("CA-CRYPTO-11")
    def test_dust_fee_with_real_value(self):
        text = (REWARDS
                + _row("XAI1", "FX1", "2026-03-10 13:00:00", "withdrawal",
                       "", "SOL", "-0.5", "-50", fee="0.0000000002")
                .replace(",0,0,\n", ",25.00,0,\n", 1))
        with self.assertRaises(ValueError) as cm:
            _parse(text)
        self.assertIn("SOL", str(cm.exception))
        self.assertIn("25.00 USD", str(cm.exception))

    @rule("CA-CRYPTO-11")
    def test_one_to_one_both_negligible(self):
        # Both sides negligible: the dust leg is left out, described as
        # what it is (a sale or a purchase), not as a "dust sweep".
        txs, err = _parse(
            _pair("XAJ1", "2026-03-11 12:00:00",
                  ("AVAX", "-0.0000000004", "0"), ("USD", "0.001", "0.001"))
            + _pair("XAK1", "2026-03-12 12:00:00",
                    ("USD", "-0.002", "-0.002"),
                    ("BTC", "0.0000000006", "0.002")))
        self.assertEqual([t for t in txs if t["action"] == "BUYSELL"], [])
        notes = [ln for ln in err.splitlines() if "books' zero" in ln]
        self.assertEqual(len(notes), 2, err)
        self.assertFalse(any("dust sweep" in n for n in notes), notes)
        sale = next(n for n in notes if "AVAX" in n)
        buy = next(n for n in notes if "BTC" in n)
        self.assertIn("instant trade", sale)
        self.assertIn("disposition of a negligible amount", sale)
        self.assertIn("acquisition of a negligible amount", buy)
        self.assertNotIn("disposition", buy)
        self.assertIn("BTC 0.0000000006 received", buy)
        # The run console's short form recognizes every such note.
        from taxjson.lib.stage_msg import reword
        for n in notes:
            self.assertEqual(len(reword(n)), 2, n)
            self.assertIn("instant trade", reword(n)[0])

    @rule("CA-CRYPTO-11")
    def test_receive_leg_shows_net_amount(self):
        # 0.0000000009 received with a 0.0000000004 fee: 0.0000000005 net.
        text = (_row("XALS", "XAL1", "2026-03-13 12:00:00", "spend", "",
                     "USD", "-0.003", "-0.003", cls="fiat")
                + _row("XALR", "XAL1", "2026-03-13 12:00:00", "receive", "",
                       "BTC", "0.0000000009", "0.003",
                       fee="0.0000000004"))
        txs, err = _parse(text)
        self.assertIn("BTC 0.0000000005 received", err)

    @rule("CA-CRYPTO-11")
    def test_sweep_note_says_dust_sweep(self):
        _txs, err = _parse(REWARDS + ONE_DUST)
        notes = [ln for ln in err.splitlines() if "books' zero" in ln]
        self.assertEqual(len(notes), 1, err)
        self.assertIn("dust sweep", notes[0])
        from taxjson.lib.stage_msg import reword
        self.assertIn("dust sweep", reword(notes[0])[0])


class TestDustRefusalStopsRun(unittest.TestCase):
    """A refused dust leg is not a note the run passes over: the run
    fails and says why."""

    @rule("CA-CRYPTO-11")
    def test_run_fails_loudly(self):
        from tax_rules.dual import cli, projects_both
        acct = '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n'
        with tempfile.TemporaryDirectory() as td:
            ps = projects_both(
                td, year=2026, accounts=acct,
                files={"inputs/crypto/kr_ledgers_2026.csv":
                       H16 + REWARDS
                       + _pair("XAM1", "2026-03-02 12:00:00",
                               ("SOL", "-0.0000000005", "-50000"),
                               ("USD", "50000", "50000"))},
                canada={"source_currencies": ["USD"]})
            root = ps["canada"]
            lines = "".join(f"2026-{m:02d}-{d:02d} 12:00:00 USD CAD 1.40 "
                            f"yahoo\n" for m in range(1, 13)
                            for d in range(1, 29))
            (root / "work").mkdir(exist_ok=True)
            (root / "work" / "to_base.csv").write_text(lines)
            r = cli(root, "run", "--no-input")
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, out)
            self.assertIn("books' zero", out)


class TestZeroQuantityMessage(unittest.TestCase):
    """A zero-unit trade that still reaches the validator names its file,
    row id, coin and date and says what to do — not `tx[947]`."""

    def test_message(self):
        from taxjson.lib.brokerages.schema import validate_transactions
        errors, _ = validate_transactions([{
            "action": "BUYSELL", "date": "2026-09-30", "symbol": "AVAX",
            "quantity": 0.0, "currency": "USD", "net_amount": 0.0,
            "price": 0.0, "source": "kr_ledgers.csv",
            "id": "TSX1-AVAX-sell"}])
        self.assertEqual(len(errors), 1)
        e = errors[0]
        self.assertNotIn("tx[", e)
        for part in ("kr_ledgers.csv", "TS***", "AVAX", "2026-09-30",
                     "quantity 0", ".tt"):
            self.assertIn(part, e)
        self.assertNotIn("TSX1", e)


class TestDustSweepRun(unittest.TestCase):
    """`taxjson run` books the ledger in both countries; the swept coins
    are conserved (the dust stays as a residue)."""

    @rule("CA-CRYPTO-11")
    @rule("US-CRYPTO-07")
    def test_run_succeeds_both_countries(self):
        from tax_rules.dual import cli, projects_both
        acct = '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n'
        with tempfile.TemporaryDirectory() as td:
            ps = projects_both(
                td, year=2026, accounts=acct,
                files={"inputs/crypto/kr_ledgers_2026.csv":
                       H16 + REWARDS + NORMAL + ONE_DUST + ALL_DUST + NO_USD},
                canada={"source_currencies": ["USD"]})
            lines = "".join(f"2026-{m:02d}-{d:02d} 12:00:00 USD CAD 1.40 "
                            f"yahoo\n" for m in range(1, 13)
                            for d in range(1, 29))
            (ps["canada"] / "work").mkdir(exist_ok=True)
            (ps["canada"] / "work" / "to_base.csv").write_text(lines)
            for country, root in ps.items():
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0,
                                 f"{country}: {r.stdout}\n{r.stderr}")
                self.assertNotIn("quantity 0", r.stdout + r.stderr)
                rows = json.loads((root / "work" / "crypto_filled.json")
                                  .read_text())["transactions"]
                held = {}
                for t in rows:
                    if t["action"] == "BUYSELL":
                        held[t["symbol"]] = (held.get(t["symbol"], 0.0)
                                             + t["quantity"])
                # 20 - 4 - 1 - 2 ADA; 0.1 - 0.001 - 0.0005 BNB; the
                # AVAX and SOL dust stays in the holdings.
                self.assertAlmostEqual(held["ADA"], 13.0, 9)
                self.assertAlmostEqual(held["BNB"], 0.0985, 9)
                self.assertAlmostEqual(held["AVAX"], 1.0, 9)
                self.assertAlmostEqual(held["SOL"], 1.0, 9)


if __name__ == "__main__":
    unittest.main()
