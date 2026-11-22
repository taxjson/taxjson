"""US engine work deferred from the re-audit (owner request): a wash-sale
replacement sold before the loss in another taxable account (A2-0544),
the §355 spin-off per lot (A2-0065), §356 boot per block (A2-0066), and
custody moves between your own taxable accounts that carry the lots
(A2-0032 securities, A2-0003 crypto). Synthetic data only.

Formerly: test_fix_us_engine
"""
import contextlib
import io
import unittest

from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx
from taxjson.rules import ids as R
from taxjson.project.settings import Settings


def _gains(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res, **match):
    return [t for t in res["transactions"]
            if t.get("qty") and all(t.get(k) == v for k, v in match.items())]


# ------------------------------------------------------ A2-0032 / A2-0003
def _move_legs(date, sym, qty, src, dst, n=1, time="00:00:00"):
    from taxjson.lib.row_types import LOT_MOVE_TYPE
    desc = f"own-account move #{n}: {src} -> {dst} (test)"
    return [tx("TRANSFER", date, sym, -qty, 0.0, account=src,
               type=LOT_MOVE_TYPE, description=desc, time=time),
            tx("TRANSFER", date, sym, qty, 0.0, account=dst,
               type=LOT_MOVE_TYPE, description=desc, time=time)]


def _moved_book():
    return ([tx("BUYSELL", "2024-01-02", "XYZ.US", 60, 3000.0,
                account="qa"),
             tx("BUYSELL", "2025-02-02", "XYZ.US", 40, 4000.0,
                account="qa")]
            + _move_legs("2025-03-02", "XYZ.US", 100, "qa", "qb")
            + [tx("BUYSELL", "2025-04-01", "XYZ.US", -70, 5600.0,
                  account="qb")])


class TestOwnAccountMoveCarriesLots(unittest.TestCase):
    """A2-0032 / A2-0003: a move between two of your own taxable
    accounts is not a sale; the US books carry each lot's basis and
    purchase date to the receiving account (FIFO per account). Canada
    pools the ACB across the accounts (s.47): the legs change nothing.

    Formerly: test_fix_us_engine.TestOwnAccountMoveCarriesLots"""

    @rule(R.US_BASIS_05)
    @rule_absent(R.US_BASIS_05, country="canada")
    def test_lots_carry_in_the_us_and_change_nothing_in_canada(self):
        r = gains_both(_moved_book(), year=2025)
        us = _sales(r["usa"])
        self.assertEqual([(t["account"], round(t["qty"]), round(t["cost"], 2),
                           t["acquired_date"], t["term"]) for t in us],
                         [("qb", 60, 3000.0, "2024-01-02", "LONG_TERM"),
                          ("qb", 10, 1000.0, "2025-02-02", "SHORT_TERM")])
        inv = {(i["account"], i["symbol"]): i["qty"]
               for i in r["usa"]["inventory"]}
        self.assertEqual(inv, {("qb", "XYZ.US"): 30.0})
        self.assertNotIn("short", r["usa"]["_stderr"].lower())
        # Canada: the same book without the legs gives the same result.
        book = [t for t in _moved_book() if t.action != "TRANSFER"]
        ca_plain = gains_both(book, year=2025)["canada"]
        self.assertAlmostEqual(r["canada"]["summary"]["total_gain"],
                               ca_plain["summary"]["total_gain"], places=6)

    @rule(R.US_BASIS_05)
    def test_a_sender_without_the_lots_is_attention(self):
        book = _move_legs("2025-03-02", "XYZ.US", 100, "qa", "qb") + [
            tx("BUYSELL", "2025-01-02", "XYZ.US", 40, 2000.0, account="qa"),
            tx("BUYSELL", "2025-04-01", "XYZ.US", -100, 8000.0,
               account="qb")]
        r = _gains("usa", book, year=2025)
        self.assertIn("ATTENTION: own-account move: XYZ.US: 100 moved "
                      "from qa to qb", r["_stderr"])
        self.assertIn("held 40", r["_stderr"])

    @rule(R.US_BASIS_05)
    def test_one_accounts_own_book_moves_the_lots_out(self):
        # tjs run's per-account (pre-blend) view: the sender's book
        # alone loses the lots without a sale; the receiver's alone
        # holds them with an unknown basis (manual reporting).
        book = _moved_book()
        qa = _gains("usa", [t for t in book if t.account == "qa"],
                    year=2025)
        self.assertEqual(qa["inventory"], [])
        self.assertEqual(_sales(qa), [])
        qb = _gains("usa", [t for t in book if t.account == "qb"],
                    year=2025)
        self.assertEqual(len(qb.get("manual_reporting_required") or []), 1)
        self.assertNotIn("conservation", qb["_stderr"])
        self.assertIn("the blended pass of `tjs run` carries",
                      qb["_stderr"])

    @rule(R.US_BASIS_05)
    def test_legs_that_do_not_pair_are_attention_and_strict_stops(self):
        import json as _json
        import tempfile
        from pathlib import Path
        from taxjson.pipeline import stages as pipeline_stages
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            for acct, rows in (
                    ("qa", [{"action": "TRANSFER", "symbol": "XYZ.US",
                             "quantity": -100.0, "date": "2025-03-02"}]),
                    ("qb", [{"action": "TRANSFER", "symbol": "XYZ.US",
                             "quantity": 90.0, "date": "2025-03-03"}])):
                (cache / f"{acct}_questrade_transfers.json").write_text(
                    _json.dumps({"metadata": {"account": acct,
                                              "brokerage": "questrade",
                                              "kind": "transfer_sidecar"},
                                 "transactions": rows}))
            cfg = {"accounts": {"qa": {"type": "taxable"},
                                "qb": {"type": "taxable"}}}
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                moves = pipeline_stages.stage_own_account_moves(
                    cache, cfg, Settings.from_table({"country": "usa"}), cache)
            # The 90 pair, as the arrival stage reads them (one pairing,
            # backlog TJ-0269: qb's 90 are no arrival from outside, so
            # their lots come from qa); the 10 stay with qa.
            self.assertEqual([(m["from"], m["to"], m["qty"])
                              for m in moves], [("qa", "qb", 90.0)])
            self.assertIn("quantities do not pair", err.getvalue())
            self.assertIn("The other 10 of the transfer-out stay with qa",
                          err.getvalue())
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                pipeline_stages.stage_own_account_moves(
                    cache, cfg, Settings.from_table({"country": "usa"}), cache, strict=True)

    @rule(R.US_BASIS_05)
    def test_a_delivery_in_two_parts_pairs(self):
        import json as _json
        import tempfile
        from pathlib import Path
        from taxjson.pipeline.stages import own_account_custody_moves
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            for acct, rows in (
                    ("qa", [{"action": "TRANSFER", "symbol": "XYZ.US",
                             "quantity": -100.0, "date": "2025-03-02"}]),
                    ("qb", [{"action": "TRANSFER", "symbol": "XYZ.US",
                             "quantity": 60.0, "date": "2025-03-03"},
                            {"action": "TRANSFER", "symbol": "XYZ.US",
                             "quantity": 40.0, "date": "2025-03-05"}])):
                (cache / f"{acct}_questrade_transfers.json").write_text(
                    _json.dumps({"metadata": {"account": acct,
                                              "brokerage": "questrade",
                                              "kind": "transfer_sidecar"},
                                 "transactions": rows}))
            moves = own_account_custody_moves(["qa", "qb"], cache)
        self.assertEqual(sorted(m["qty"] for m in moves), [40.0, 60.0])


class TestOwnAccountMoveFuzz(unittest.TestCase):
    """Random long-only books over three accounts with own-account moves:
    every lot ends in exactly one place (no short, no phantom), the basis
    is conserved (buys = sold cost + held cost), each account holds what
    it should, and Canada's result is the same with or without the
    legs (US-BASIS-05). TAXJSON_FUZZ_BOOKS sets the depth.

    Formerly: test_fix_us_engine.TestOwnAccountMoveFuzz"""

    def _book(self, seed):
        import random
        rng = random.Random(seed)
        accts = ["a1", "a2", "a3"]
        held = {(a, s): 0.0 for a in accts for s in ("S1.US", "S2.US")}
        book, cost, n = [], 0.0, 0
        day = 0
        for _ in range(rng.randint(4, 14)):
            day += rng.randint(1, 20)
            d = f"2025-{1 + day // 28:02d}-{1 + day % 28:02d}" \
                if day < 336 else None
            if d is None:
                break
            sym = rng.choice(["S1.US", "S2.US"])
            a = rng.choice(accts)
            r = rng.random()
            if r < 0.45 or held[(a, sym)] <= 0:
                q = float(rng.choice([10, 25, 40]))
                net = round(q * rng.uniform(5, 50), 2)
                book.append(tx("BUYSELL", d, sym, q, net, account=a))
                held[(a, sym)] += q
                cost += net
            elif r < 0.75:
                b = rng.choice([x for x in accts if x != a])
                q = float(rng.randint(1, int(held[(a, sym)])))
                n += 1
                book += _move_legs(d, sym, q, a, b, n=n,
                                   time="12:00:00")
                held[(a, sym)] -= q
                held[(b, sym)] += q
            else:
                q = float(rng.randint(1, int(held[(a, sym)])))
                book.append(tx("BUYSELL", d, sym, -q,
                               round(q * rng.uniform(5, 50), 2),
                               account=a))
                held[(a, sym)] -= q
        return book, held, cost

    @rule(R.US_BASIS_05)
    @rule_absent(R.US_BASIS_05, country="canada")
    def test_fuzz(self):
        import os
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        from taxjson.engine.us.taxrules import USATaxRules
        n_books = int(os.environ.get("TAXJSON_FUZZ_BOOKS", "200"))
        for seed in range(n_books):
            with self.subTest(seed=seed):
                book, held, cost = self._book(seed)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    r = USATaxRules().compute_gains(
                        list(book), per_account_basis=True,
                        detect_wash_sales=False)
                self.assertNotIn("conservation", err.getvalue())
                self.assertNotIn("ATTENTION", err.getvalue())
                inv = {}
                for i in r["inventory"]:
                    self.assertGreater(i["qty"], 0, i)
                    inv[(i["account"], i["symbol"])] = i["qty"]
                self.assertEqual(
                    {k: round(v, 6) for k, v in inv.items()},
                    {k: round(v, 6) for k, v in held.items() if v > 1e-9})
                sold = sum(g["cost"] for g in r["transactions"]
                           if g.get("qty"))
                kept = sum(i["total_cost"] for i in r["inventory"])
                self.assertAlmostEqual(sold + kept, cost, places=4)
                with contextlib.redirect_stderr(io.StringIO()):
                    ca = CanadaTaxRules().compute_gains(list(book))
                    ca0 = CanadaTaxRules().compute_gains(
                        [t for t in book if t.action != "TRANSFER"])
                self.assertAlmostEqual(ca["summary"]["total_gain"],
                                       ca0["summary"]["total_gain"],
                                       places=6)


if __name__ == "__main__":
    unittest.main()
