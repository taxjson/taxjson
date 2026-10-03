"""US engine work deferred from the re-audit (owner request): a wash-sale
replacement sold before the loss in another taxable account (A2-0544),
the §355 spin-off per lot (A2-0065), §356 boot per block (A2-0066), and
custody moves between your own taxable accounts that carry the lots
(A2-0032 securities, A2-0003 crypto). Synthetic data only.
"""
import contextlib
import io
import unittest

from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx


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


# ---------------------------------------------------------------- A2-0544
def _sold_replacement_book(rep_buy="2025-03-03", rep_sell="2025-03-05",
                           loss="2025-03-10"):
    """margin buys 100 @50 and sells @30 (a 2,000 loss); account b buys
    100 @30 inside the window and sells them @31 BEFORE the loss."""
    return [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
               account="margin"),
            tx("BUYSELL", rep_buy, "XYZ.US", 100, 3000.0, account="b"),
            tx("BUYSELL", rep_sell, "XYZ.US", -100, 3100.0, account="b"),
            tx("BUYSELL", loss, "XYZ.US", -100, 3000.0, account="margin")]


class TestReplacementSoldBeforeTheLoss(unittest.TestCase):
    """A2-0544: §1091 has no still-held test — a purchase in another
    taxable account bought and sold before the loss still replaces it;
    the disallowed loss goes into that earlier sale's basis."""

    @rule("US-WASH-22")
    @rule_absent("US-WASH-22", country="canada")
    def test_us_disallows_and_moves_the_loss_canada_keeps_it(self):
        r = gains_both(_sold_replacement_book(), year=2025)
        us = r["usa"]
        loss = _sales(us, account="margin")[0]
        self.assertAlmostEqual(loss["disallowed_amount"], 2000.0, places=2)
        self.assertAlmostEqual(loss["gain"], 0.0, places=2)
        self.assertEqual(loss["wash_replacements"][0]["sold_before_loss"],
                         "2025-03-05")
        rep = _sales(us, account="b")[0]
        self.assertAlmostEqual(rep["cost"], 5000.0, places=2)
        self.assertAlmostEqual(rep["gain"], -1900.0, places=2)
        self.assertAlmostEqual(us["summary"]["total_gain"], -1900.0,
                               places=2)
        # Canada: the substituted property is not held at day 30
        # (s.54): no superficial loss.
        ca = r["canada"]
        self.assertAlmostEqual(ca["summary"]["total_disallowed"], 0.0)

    @rule("US-WASH-22")
    def test_holding_period_of_the_loss_shares_carries_over(self):
        # Loss shares held 2024-01-02 .. 2025-03-10 (433 days): the
        # replacement sold 2025-03-05 becomes long-term.
        book = _sold_replacement_book()
        book[0] = tx("BUYSELL", "2024-01-02", "XYZ.US", 100, 5000.0,
                     account="margin")
        us = _gains("usa", book, year=2025)
        rep = _sales(us, account="b")[0]
        self.assertEqual(rep["term"], "LONG_TERM")

    @rule("US-WASH-22")
    def test_partial_match_splits_the_earlier_sale(self):
        # b bought 200 and sold them; only 100 replace the loss.
        book = [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
                   account="margin"),
                tx("BUYSELL", "2025-03-03", "XYZ.US", 200, 6000.0,
                   account="b"),
                tx("BUYSELL", "2025-03-05", "XYZ.US", -200, 6200.0,
                   account="b"),
                tx("BUYSELL", "2025-03-10", "XYZ.US", -100, 3000.0,
                   account="margin")]
        us = _gains("usa", book, year=2025)
        rows = _sales(us, account="b")
        self.assertEqual(sorted(round(r["qty"]) for r in rows), [100, 100])
        bumped = [r for r in rows if r.get("wash_basis_added")]
        self.assertEqual(len(bumped), 1)
        self.assertAlmostEqual(bumped[0]["cost"], 5000.0, places=2)
        # b: +200 on its round trip, less the 2,000 moved into it;
        # margin's loss is disallowed.
        self.assertAlmostEqual(us["summary"]["total_gain"], -1800.0,
                               places=2)

    @rule("US-WASH-22")
    def test_replacement_sold_at_a_disallowed_loss_is_flagged(self):
        # b's own sale is a loss washed by b's rebuy: not matched again.
        book = [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000.0,
                   account="margin"),
                tx("BUYSELL", "2025-03-03", "XYZ.US", 100, 3000.0,
                   account="b"),
                tx("BUYSELL", "2025-03-05", "XYZ.US", -100, 2900.0,
                   account="b"),
                tx("BUYSELL", "2025-03-06", "XYZ.US", 100, 2900.0,
                   account="b"),
                tx("BUYSELL", "2025-03-10", "XYZ.US", -100, 3000.0,
                   account="margin")]
        us = _gains("usa", book, year=2025)
        self.assertIn("check this wash sale by hand", us["_stderr"])
        b_sale = _sales(us, account="b")[0]
        self.assertFalse(b_sale.get("wash_basis_added"))

    @rule("US-WASH-22")
    def test_filed_year_is_left_as_filed(self):
        book = _sold_replacement_book(rep_buy="2025-12-15",
                                      rep_sell="2025-12-17",
                                      loss="2026-01-05")
        unlocked = _gains("usa", book)
        rep = _sales(unlocked, account="b")[0]
        self.assertAlmostEqual(rep["gain"], -1900.0, places=2)
        self.assertIn("amend that return", unlocked["_stderr"])
        locked = _gains("usa", book, locked_years=(2025,))
        rep = _sales(locked, account="b")[0]
        self.assertAlmostEqual(rep["gain"], 100.0, places=2)
        self.assertFalse(rep.get("wash_basis_added"))
        moved = [t for t in locked["transactions"] if t.get("deemed")]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["date"], "2026-01-05")
        self.assertAlmostEqual(moved[0]["gain"], -2000.0, places=2)
        self.assertIn("ATTENTION: wash sale reaches a filed year",
                      locked["_stderr"])
        self.assertIn("1040-X", locked["_stderr"])
        # Year totals: 2025 as filed, 2026 nets the loss out.
        y25 = _gains("usa", book, locked_years=(2025,), year=2025)
        y26 = _gains("usa", book, locked_years=(2025,), year=2026)
        self.assertAlmostEqual(y25["summary"]["total_gain"], 100.0,
                               places=2)
        self.assertAlmostEqual(y26["summary"]["total_gain"], -2000.0,
                               places=2)

    @rule("US-WASH-22")
    def test_locked_year_flags_are_us_only(self):
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_filed import locked_year_flags
        from taxjson.lib.country import flag_country_problems
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "filed").mkdir()
            (root / "filed" / "2025.json").write_text('{"year": 2025}')
            self.assertEqual(locked_year_flags(root, {"country": "usa"}),
                             ["--locked-year", "2025"])
            self.assertEqual(locked_year_flags(root,
                                               {"country": "canada"}), [])
        self.assertTrue(flag_country_problems(
            "canada", {"--locked-year": [2025]}, tool="taxjson-gains"))


# ---------------------------------------------------------------- A2-0065
def _spin_rows(alloc, qty_received=20.0):
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.corp_actions import CorporateAction, resolve_event
    ev = CorporateAction(
        date="2025-04-01", time="09:30:00", action_type="spinoff",
        source_symbol="PAR.US", source_isin="", target_symbol="SPN.US",
        target_isin="", ratio_new=1, ratio_old=5, qty_disposed=0.0,
        qty_received=qty_received, fmv=0.0, currency="USD",
        target_currency="USD", account="margin", event_id="ev-spin")
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, "tax_free_355", country="usa",
                             hints={"allocated_acb": alloc})
    return [TaxTransaction(**r) for r in rows]


def _parent_lots():
    return [tx("BUYSELL", "2023-01-10", "PAR.US", 50, 200.0),
            tx("BUYSELL", "2025-03-01", "PAR.US", 50, 1800.0)]


def _engine(book, **kw):
    from taxjson.lib.core import USATaxRules
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = USATaxRules().compute_gains(list(book), per_account_basis=True,
                                        **kw)
    r["_stderr"] = err.getvalue()
    return r


def _lots(res, symbol):
    return [i for i in res["inventory"] if i["symbol"] == symbol]


class TestSpinoff355PerLot(unittest.TestCase):
    """A2-0065: Reg. §1.358-2 — every parent share gives up the same
    fraction of its own basis; one spun-off block per parent block with
    the parent's holding period (§1223(1)); never a §301(c)(3) gain."""

    @rule("US-CORP-07")
    def test_basis_by_fraction_and_tacked_blocks(self):
        book = _parent_lots() + _spin_rows(400.0) + [
            tx("BUYSELL", "2025-06-02", "SPN.US", -20, 600.0),
            tx("BUYSELL", "2025-06-02", "PAR.US", -100, 3000.0)]
        r = _engine(book)
        spn = sorted((t for t in r["transactions"]
                      if t["symbol"] == "SPN.US"),
                     key=lambda t: t["acquired_date"])
        self.assertEqual([(round(t["qty"]), round(t["cost"], 2), t["term"],
                           t["acquired_date"]) for t in spn],
                         [(10, 40.0, "LONG_TERM", "2023-01-10"),
                          (10, 360.0, "SHORT_TERM", "2025-03-01")])
        par = sorted((t for t in r["transactions"]
                      if t["symbol"] == "PAR.US"),
                     key=lambda t: t["acquired_date"])
        self.assertEqual([round(t["cost"], 2) for t in par],
                         [160.0, 1440.0])
        self.assertFalse(any(t.get("deemed") for t in r["transactions"]))

    @rule("US-CORP-07")
    def test_no_deemed_gain_and_an_allocation_beyond_basis_is_capped(self):
        r = _engine(_parent_lots() + _spin_rows(600.0))
        self.assertFalse(any(t.get("deemed") for t in r["transactions"]))
        self.assertNotIn("§301(c)(3)", r["_stderr"])
        r = _engine(_parent_lots() + _spin_rows(2500.0))
        self.assertIn("capped at the basis", r["_stderr"])
        self.assertFalse(any(t.get("deemed") for t in r["transactions"]))
        par = _lots(r, "PAR.US")[0]
        spn = _lots(r, "SPN.US")[0]
        self.assertAlmostEqual(par["total_cost"], 0.0, places=2)
        self.assertAlmostEqual(spn["total_cost"], 2000.0, places=2)

    @rule("US-CORP-07")
    def test_spun_off_shares_are_not_a_wash_replacement(self):
        # A when-issued SPN loss five days before the distribution.
        book = _parent_lots() + [
            tx("BUYSELL", "2025-03-20", "SPN.US", 10, 500.0),
            tx("BUYSELL", "2025-03-27", "SPN.US", -10, 400.0)] \
            + _spin_rows(400.0)
        r = _engine(book)
        loss = [t for t in r["transactions"] if t["symbol"] == "SPN.US"][0]
        self.assertAlmostEqual(loss["gain"], -100.0, places=2)
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule("US-CORP-07")
    def test_no_parent_lots_falls_back_with_a_warning(self):
        r = _engine(_spin_rows(400.0))
        self.assertIn("finds no long PAR.US lots", r["_stderr"])


# ---------------------------------------------------------------- A2-0066
def _boot_rows(boot=400.0, fmv_per_share=32.0):
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.corp_actions import CorporateAction, resolve_event
    ev = CorporateAction(
        date="2025-06-20", time="09:30:00", action_type="merger",
        source_symbol="OLD.US", source_isin="", target_symbol="NEW.US",
        target_isin="", ratio_new=1, ratio_old=2, qty_disposed=100.0,
        qty_received=50.0, fmv=0.0, currency="USD", target_currency="USD",
        account="margin", event_id="ev-boot")
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, "reorg_368_boot", country="usa",
                             hints={"cash_boot": boot,
                                    "fmv_per_share": fmv_per_share})
    return [TaxTransaction(**r) for r in rows]


def _old_lots():
    return [tx("BUYSELL", "2023-01-10", "OLD.US", 50, 200.0),
            tx("BUYSELL", "2025-03-01", "OLD.US", 50, 1800.0)]


class TestBoot356PerBlock(unittest.TestCase):
    """A2-0066: Reg. §1.356-1(b) / Rev. Rul. 68-23 — realized and
    recognized gain per block, never a loss; new basis per block = old
    basis − boot share + recognized; holding period tacked."""

    @rule("US-CORP-05")
    def test_per_block_gain_and_no_loss_row(self):
        r = _engine(_old_lots() + _boot_rows())
        rows = sorted((t for t in r["transactions"]
                       if t["symbol"] == "OLD.US"),
                      key=lambda t: t["acquired_date"])
        # Lot A: realized 1000 − 200 = 800, recognized min(800, 200);
        # lot B: realized 1000 − 1800 < 0, recognized 0.
        self.assertEqual([(round(t["gain"], 2), t["term"]) for t in rows],
                         [(200.0, "LONG_TERM"), (0.0, "SHORT_TERM")])
        self.assertFalse(any(t["gain"] < -0.005 for t in rows))
        self.assertEqual([round(t["proceeds"], 2) for t in rows],
                         [200.0, 200.0])
        self.assertAlmostEqual(r["summary"]["total_gain"], 200.0, places=2)
        new = _lots(r, "NEW.US")[0]
        self.assertAlmostEqual(new["total_cost"], 1800.0, places=2)
        self.assertAlmostEqual(new["qty"], 50.0)

    @rule("US-CORP-05")
    def test_new_blocks_keep_the_old_blocks_dates(self):
        book = _old_lots() + _boot_rows() + [
            tx("BUYSELL", "2025-09-02", "NEW.US", -50, 2000.0)]
        r = _engine(book)
        sold = sorted((t for t in r["transactions"]
                       if t["symbol"] == "NEW.US"),
                      key=lambda t: t["acquired_date"])
        self.assertEqual([(round(t["qty"]), round(t["cost"], 2),
                           t["acquired_date"], t["term"]) for t in sold],
                         [(25, 200.0, "2023-01-10", "LONG_TERM"),
                          (25, 1600.0, "2025-03-01", "SHORT_TERM")])

    @rule("US-CORP-05")
    def test_new_shares_are_not_a_wash_replacement(self):
        # A NEW.US loss in the window of the exchange is not washed by
        # shares received in a §356 exchange (§1091(a)).
        book = _old_lots() + [
            tx("BUYSELL", "2025-06-01", "NEW.US", 10, 500.0),
            tx("BUYSELL", "2025-06-05", "NEW.US", -10, 400.0)] \
            + _boot_rows()
        r = _engine(book)
        loss = [t for t in r["transactions"] if t["symbol"] == "NEW.US"][0]
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)

    @rule("US-CORP-05")
    def test_missing_old_lots_are_named(self):
        r = _engine(_old_lots()[:1] + _boot_rows())
        self.assertIn("have no basis in the books", r["_stderr"])


# ------------------------------------------------------ A2-0032 / A2-0003
def _move_legs(date, sym, qty, src, dst, n=1, time="00:00:00"):
    from taxjson.lib.core import LOT_MOVE_TYPE
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
    pools the ACB across the accounts (s.47): the legs change nothing."""

    @rule("US-BASIS-05")
    @rule_absent("US-BASIS-05", country="canada")
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

    @rule("US-BASIS-05")
    def test_a_sender_without_the_lots_is_attention(self):
        book = _move_legs("2025-03-02", "XYZ.US", 100, "qa", "qb") + [
            tx("BUYSELL", "2025-01-02", "XYZ.US", 40, 2000.0, account="qa"),
            tx("BUYSELL", "2025-04-01", "XYZ.US", -100, 8000.0,
               account="qb")]
        r = _gains("usa", book, year=2025)
        self.assertIn("ATTENTION: own-account move: XYZ.US: 100 moved "
                      "from qa to qb", r["_stderr"])
        self.assertIn("held 40", r["_stderr"])

    @rule("US-BASIS-05")
    def test_one_accounts_own_book_moves_the_lots_out(self):
        # taxjson run's per-account (pre-blend) view: the sender's book
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
        self.assertIn("the blended pass of `taxjson run` carries",
                      qb["_stderr"])

    @rule("US-BASIS-05")
    def test_legs_that_do_not_pair_are_attention_and_strict_stops(self):
        import json as _json
        import tempfile
        from pathlib import Path
        from taxjson.bin import taxjson_run
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
                moves = taxjson_run.stage_own_account_moves(
                    cache, cfg, {"country": "usa"}, cache)
            self.assertEqual(moves, [])
            self.assertIn("quantities do not pair", err.getvalue())
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                taxjson_run.stage_own_account_moves(
                    cache, cfg, {"country": "usa"}, cache, strict=True)

    @rule("US-BASIS-05")
    def test_a_delivery_in_two_parts_pairs(self):
        import json as _json
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_run import own_account_custody_moves
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
    legs (US-BASIS-05). TAXJSON_FUZZ_BOOKS sets the depth."""

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

    @rule("US-BASIS-05")
    @rule_absent("US-BASIS-05", country="canada")
    def test_fuzz(self):
        import os
        from taxjson.lib.core import CanadaTaxRules, USATaxRules
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


class TestOwnMoveFiledLock(unittest.TestCase):
    """check-filed recomputes a locked year the way the run booked it:
    a US crypto move carried by the blended crypto pass is no DRIFT."""

    @rule("US-CRYPTO-05")
    def test_close_year_then_check_filed_is_ok(self):
        import tempfile
        from test_fix_a2_crypto_sends import US_ACOIN, US_BKR, _proj
        from test_fix_sends import _cli
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"bkr": {"cb_bkr.csv": US_BKR},
                                    "acoin": {"cb_acoin.csv": US_ACOIN}},
                               country="usa")
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, home, "close-year")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-2000:])
            k = _cli(root, home, "check-filed")
            self.assertEqual(k.returncode, 0, k.stdout + k.stderr[-2000:])
            self.assertIn("filed 2025: OK", k.stdout)


if __name__ == "__main__":
    unittest.main()
