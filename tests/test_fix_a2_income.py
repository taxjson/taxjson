"""Re-audit-2 fixes, income list (A2-...): distributions.map sizing and
dating, the per-account split of a blended pool, income dating of
Canadian split-share corporations and trusts, capital_gains_dividends.map
and ric_january_dividends matching.

All data is synthetic (fake account ids, invented tickers).
"""
import contextlib
import io
import unittest

from tax_rules import rule

ACCT = "55500001"  # pii-ok (synthetic)


def _buy(date, qty, sym, acct=ACCT, time="10:00:00", settle=None):
    return {"action": "BUYSELL", "date": date, "time": time,
            "date_settle": settle or date, "symbol": sym,
            "quantity": float(qty), "account": acct}


def _split(date, sym, ratio, new="", acct=ACCT, time="00:00:00"):
    return {"action": "SPLIT", "date": date, "time": time, "symbol": sym,
            "quantity": float(ratio), "symbol_new": new, "account": acct}


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


class TestBalanceOnPerSymbol(unittest.TestCase):
    """A2-0021, A2-0074, A2-0986, A2-0225: balance_on holds shares per
    (account, raw symbol) like the engine walk."""

    @rule("CA-DIST-01")
    def test_a2_0021_rename_split_does_not_scale_target_holding(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [_buy("2025-01-02", 1600, "AAA.TO"),
               _buy("2025-01-03", 50, "BBB.TO"),
               _split("2025-03-01", "AAA.TO", 0.0625, "BBB.TO")]
        self.assertAlmostEqual(balance_on(txs, "BBB.TO", "2025-12-31"), 150)
        txs = [_buy("2025-01-03", 30, "BBB.TO"),
               _split("2025-03-01", "AAA.TO", 2, "BBB.TO")]
        self.assertAlmostEqual(balance_on(txs, "BBB.TO", "2025-12-31"), 30)

    @rule("CA-DIST-01")
    def test_a2_0021_split_for_account_apportions_true_count(self):
        from taxjson.bin.taxjson_split_gains import split_for_account
        base = [_buy("2025-01-02", 1600, "AAA.TO"),
                _buy("2025-01-03", 50, "BBB.TO"),
                _split("2025-03-01", "AAA.TO", 0.0625, "BBB.TO")]
        combined = {"inventory": [{"symbol": "BBB.TO", "qty": 150.0,
                                   "total_cost": 21000.0}],
                    "transactions": [], "summary": {}}
        out = split_for_account(combined, ACCT, base)
        row = out["inventory"][0]
        self.assertAlmostEqual(row["qty"], 150.0)
        self.assertAlmostEqual(row["total_cost"], 21000.0)

    @rule("US-DIST-01")
    def test_a2_0074_old_ticker_after_rename_is_its_own_holding(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [_buy("2025-01-02", 100, "XYZ.US"),
               _split("2025-02-01", "XYZ.US", 2, "XYZN.US"),
               _buy("2025-03-01", 30, "XYZ.US")]
        self.assertAlmostEqual(balance_on(txs, "XYZ.US", "2025-12-31"), 30)
        self.assertAlmostEqual(balance_on(txs, "XYZN.US", "2025-12-31"), 200)

    @rule("US-DIST-01")
    def test_a2_0074_reused_ticker_map_rows_sized_per_holding(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        txs = [_buy("2022-01-03", 100, "FB.US"),
               _split("2022-06-09", "FB.US", 1, "META.US"),
               _buy("2025-02-03", 50, "FB.US"),
               _buy("2025-03-03", -10, "META.US")]
        doc = {"transactions": txs, "metadata": {}}
        (doc, n), _err = _quiet(
            apply_distributions, doc,
            [("META.US", "2025-06-30", 1.0), ("FB.US", "2025-06-30", 1.0)],
            ACCT, country="usa")
        adj = {(t["symbol"], t["net_amount"]) for t in doc["transactions"]
               if t["action"] == "ADJUST"}
        self.assertEqual(n, 2)
        self.assertEqual(adj, {("META.US", 90.0), ("FB.US", 50.0)})

    @rule("CA-DIST-01")
    def test_a2_0986_accounts_do_not_share_splits(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [_buy("2025-01-02", 100, "X.TO", "a"),
               _buy("2025-01-02", 100, "X.TO", "b"),
               _split("2025-02-01", "X.TO", 2, acct="a"),
               _split("2025-02-01", "X.TO", 2, acct="b")]
        self.assertAlmostEqual(balance_on(txs, "X.TO", "2025-12-31"), 400)

    @rule("CA-DIST-01")
    def test_a2_0225_split_before_same_stamp_trade(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        for order in (0, 1):
            txs = [_buy("2025-01-02", 100, "XYZ.TO"),
                   _buy("2025-03-03", 50, "XYZ.TO", time="09:30:00"),
                   _split("2025-03-03", "XYZ.TO", 2, time="09:30:00")]
            if order:
                txs[1], txs[2] = txs[2], txs[1]
            self.assertAlmostEqual(
                balance_on(txs, "XYZ.TO", "2025-03-03"), 250, msg=order)

    def test_conservation_message_names_direction(self):
        from taxjson.bin.taxjson_run import _blend_conservation_gaps
        blended = {"inventory": [{"symbol": "BBB.TO", "qty": 150}]}
        over = [{"inventory": [{"symbol": "BBB.TO", "qty": 160,
                                "blended_pool": True}]}]
        msg = _blend_conservation_gaps(blended, over)[0]
        self.assertIn("MORE than the pool", msg)
        self.assertIn("over-report", msg)
        self.assertNotIn("only", msg)
        under = [{"inventory": [{"symbol": "BBB.TO", "qty": 140,
                                 "blended_pool": True}]}]
        self.assertIn("under-report",
                      _blend_conservation_gaps(blended, under)[0])


def _usa_gains(rows, map_rows):
    """apply-distributions on dict rows, then the US engine."""
    import copy
    from taxjson.bin.taxjson_apply_distributions import apply_distributions
    from taxjson.lib.core import coerce_transaction_row
    from taxjson.lib.pipeline import GainsRequest, run_gains
    doc = {"transactions": copy.deepcopy(rows),
           "metadata": {"target_currency": "USD"}}
    (doc, _n), _e = _quiet(apply_distributions, doc, map_rows, ACCT,
                           country="usa")
    txs = [coerce_transaction_row(t, i, "t")
           for i, t in enumerate(doc["transactions"])]
    res, err = _quiet(run_gains, txs, [], [],
                      req=GainsRequest(country="usa", taxable=True))
    return doc, res, err


def _trade(date, qty, price, sym, settle=None, time="10:00:00"):
    return {"action": "BUYSELL", "date": date, "time": time,
            "date_settle": settle or date, "symbol": sym,
            "quantity": float(qty), "price": float(price),
            "net_amount": -float(qty) * float(price), "currency": "USD",
            "account": ACCT}


class TestMapAdjustStamp(unittest.TestCase):
    """A2-0071, A2-0988: the map ADJUST reaches the holder-of-record lots
    in a trade-date-ordered engine."""

    @rule("US-DIST-01")
    def test_a2_0071_sold_on_record_date_keeps_adjust(self):
        rows = [_trade("2025-03-03", 100, 10, "XYZ.US", "2025-03-04"),
                _trade("2025-12-29", -100, 12, "XYZ.US", "2025-12-30")]
        doc, res, err = _usa_gains(rows, [("XYZ.US", "2025-12-29", 0.5)])
        self.assertAlmostEqual(res["summary"]["total_gain"], 150.0, 2)
        self.assertNotIn("NOT applied", err)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"]
        self.assertEqual(adj[0]["date_settle"], "2025-12-29")

    @rule("US-DIST-01")
    def test_a2_0988_buy_on_record_date_gets_no_share(self):
        rows = [_trade("2025-02-03", 100, 30, "VTI.US", "2025-02-04"),
                _trade("2025-06-20", 100, 30, "VTI.US", "2025-06-23"),
                _trade("2025-07-01", -100, 45, "VTI.US", "2025-07-02")]
        _doc, res, _err = _usa_gains(rows, [("VTI.US", "2025-06-20", 0.5)])
        self.assertAlmostEqual(res["summary"]["total_gain"], 1450.0, 2)

    def test_no_straddle_keeps_record_date_stamp(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        rows = [_trade("2025-03-03", 100, 10, "XYZ.US", "2025-03-04")]
        (doc, _n), _e = _quiet(apply_distributions,
                               {"transactions": rows, "metadata": {}},
                               [("XYZ.US", "2025-12-29", 0.5)], ACCT)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"][0]
        self.assertEqual((adj["date"], adj["time"], adj["date_settle"]),
                         ("2025-12-29", "23:59:58", "2025-12-29"))


    @rule("US-DIST-01")
    def test_a2_0071_no_lots_warning_names_basis_increase(self):
        from taxjson.lib.core import coerce_transaction_row
        from taxjson.lib.pipeline import GainsRequest, run_gains
        row = {"action": "ADJUST", "date": "2025-06-30", "time": "23:59:58",
               "date_settle": "2025-06-30", "symbol": "XYZ.US",
               "quantity": 0.0, "net_amount": 50.0, "currency": "USD",
               "account": ACCT}
        _r, err = _quiet(run_gains, [coerce_transaction_row(row, 0, "t")],
                         [], [], req=GainsRequest(country="usa",
                                                  taxable=True))
        self.assertIn("basis increase", err)
        self.assertNotIn("return of capital", err)


class TestRocDoubleEntry(unittest.TestCase):
    """A2-0072 (broker ROC + map ROC), A2-0232 (map ROC whose cash is a
    DIVIDEND row)."""

    _BOOK = [
        {"action": "BUYSELL", "date": "2025-03-03", "time": "10:00:00",
         "date_settle": "2025-03-04", "symbol": "XYZ.UN.TO",
         "quantity": 1000.0, "price": 1.0, "net_amount": -1000.0,
         "currency": "CAD", "account": ACCT},
        {"action": "ADJUST", "date": "2026-01-08", "time": "00:00:00",
         "symbol": "XYZ.UN.TO", "quantity": 0.0, "net_amount": -20.0,
         "type": "roc", "record_date": "2025-12-30", "currency": "CAD",
         "account": ACCT,
         "description": "XYZ UNITS RETURN OF CAPITAL REC 12/30/25 "
                        "PAY 01/08/26"}]

    @rule("CA-DIST-01")
    def test_a2_0072_apply_distributions_warns_either_date(self):
        import copy
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        for d in ("2025-12-30", "2026-01-08"):
            doc = {"transactions": copy.deepcopy(self._BOOK),
                   "metadata": {}}
            _o, err = _quiet(apply_distributions, doc,
                             [("XYZ.UN.TO", d, -0.02)], ACCT,
                             country="canada")
            self.assertIn("reduced TWICE", err, d)

    def test_a2_0072_roc_sum_warns_across_year_end(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        for mapdate in ("2025-12-30", "2026-01-08"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "work").mkdir()
                (root / "taxjson.toml").write_text(
                    '[settings]\nyear = 2025\ncountry = "canada"\n'
                    'base_currency = "CAD"\n'
                    '[accounts.margin]\ntype = "taxable"\n')
                native = [dict(t, account="margin") for t in self._BOOK]
                (root / "work" / "margin_raw.json").write_text(
                    json.dumps({"transactions": native}))
                dist = {"action": "ADJUST", "date": mapdate,
                        "time": "23:59:58", "date_settle": mapdate,
                        "symbol": "XYZ.UN.TO", "quantity": 0.0,
                        "currency": "CAD", "net_amount": -20.0,
                        "type": "dist", "account": "margin",
                        "id": f"DIST-XYZ.UN.TO-{mapdate}-margin"}
                (root / "work" / "margin_base.json").write_text(
                    json.dumps({"transactions": native + [dist]}))
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run",
                     "-C", str(root), "roc-sum"],
                    cwd=repo, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("twice", r.stderr, mapdate)

    @rule("CA-DIST-01")
    def test_a2_0232_map_roc_with_dividend_cash_warns(self):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        book = [dict(self._BOOK[0], symbol="ZRE.TO"),
                {"action": "DIVIDEND", "date": "2025-06-30",
                 "time": "00:00:00", "symbol": "ZRE.TO", "quantity": 0.0,
                 "net_amount": 120.0, "currency": "CAD", "account": ACCT}]
        _o, err = _quiet(apply_distributions,
                         {"transactions": book, "metadata": {}},
                         [("ZRE.TO", "2025-06-27", -0.12)], ACCT,
                         country="canada")
        self.assertIn("still counted IN FULL as income", err)


def _cgdiv(sym, date, amt, rec="", cur="CAD"):
    return {"action": "DIVIDEND", "symbol": sym, "date": date,
            "gross_amount": amt, "net_amount": amt, "currency": cur,
            "record_date": rec, "id": f"{sym}-{date}"}


class TestCgDividendsMap(unittest.TestCase):
    """A2-0075, A2-0228 (bare root), A2-0227/0987/0990 (amount),
    A2-0994 (unreadable file), A2-0560 (pay date)."""

    @rule("CA-INC-06")
    def test_bare_root_matches_canadian_listings_only(self):
        from taxjson.lib.cg_dividends import allocate, parse_map
        rows = [("m", _cgdiv("FTN.TO", "2025-03-10", 100.0)),
                ("m", _cgdiv("FTN.PR.A.TO", "2025-03-10", 50.0)),
                ("m", _cgdiv("T.TO", "2025-04-01", 40.0)),
                ("m", _cgdiv("T.US", "2025-05-01", 27.75, cur="USD")),
                ("m", _cgdiv("LFE.PR.B.TO", "2025-03-10", 40.0)),
                ("m", _cgdiv("LFE.TO", "2025-03-10", 100.0))]
        for text, want in (("FTN 2025 all\n", {"FTN.TO-2025-03-10"}),
                           ("T 2025 all\n", {"T.TO-2025-04-01"}),
                           ("LFE 2025 all\n", {"LFE.TO-2025-03-10"})):
            f = allocate(parse_map(text), rows, date_of=lambda t: t["date"],
                         default_accounts={"m"})
            self.assertEqual({k[1] for k in f}, want, text)

    def test_amount_refuses_decimal_comma_and_underscore(self):
        from taxjson.lib.cg_dividends import CgDividendMapError, parse_map
        for amt in ("17,11", "0,125", "1_0", "1,23", "12,34", "1,2,3",
                    "1_000", "5,50", "1234,56"):
            with self.assertRaises(CgDividendMapError, msg=amt):
                parse_map(f"FFN.TO 2024 {amt}\n")
        for amt, want in (("1,711.05", 1711.05), ("17.11", 17.11),
                          (".5", 0.5), ("5", 5.0)):
            self.assertEqual(parse_map(f"FFN.TO 2024 {amt}\n")[0].amount,
                             want)

    def test_directory_or_dangling_symlink_is_an_error(self):
        import os
        import tempfile
        from pathlib import Path
        from taxjson.lib.cg_dividends import (MAP_NAME, CgDividendMapError,
                                              load_map)
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(load_map(Path(d)))
            (Path(d) / MAP_NAME).mkdir()
            with self.assertRaises(CgDividendMapError):
                load_map(Path(d))
        with tempfile.TemporaryDirectory() as d:
            os.symlink(Path(d) / "missing", Path(d) / MAP_NAME)
            with self.assertRaises(CgDividendMapError):
                load_map(Path(d))

    @rule("CA-INC-06")
    def test_date_entry_matches_pay_date_of_record_dated_row(self):
        from taxjson.lib.cg_dividends import allocate, parse_map
        rows = [("m", _cgdiv("XTD.TO", "2025-09-10", 100.0,
                             rec="2025-08-29"))]
        for when in ("2025-09-10", "2025-08-29"):
            f = allocate(parse_map(f"XTD.TO {when} 5.50\n"), rows,
                         date_of=lambda t: t["record_date"],
                         default_accounts={"m"})
            self.assertAlmostEqual(f[("m", "XTD.TO-2025-09-10")], 0.055)


def _dist(sym, pay, rec, amt=100.0, desc="", action="DIVIDEND", **kw):
    r = {"action": action, "symbol": sym, "date": pay, "record_date": rec,
         "gross_amount": amt, "net_amount": amt, "currency": "CAD",
         "description": desc, "account": ACCT}
    if action == "DIVIDEND":
        r["income_label"] = "distribution"
    else:
        r.update(type="roc", net_amount=-amt, gross_amount=0.0)
    r.update(kw)
    return r


class TestIncomeDating(unittest.TestCase):
    """A2-0076, A2-0231, A2-0991, A2-0229, A2-0073, A2-0561/A2-0993."""

    def _ca(self, **settings):
        from taxjson.lib.income_dating import rules_for
        return rules_for("canada", settings)

    @rule("CA-INC-DATE-TRUST")
    def test_a2_0076_split_share_corps_keep_pay_date(self):
        r = self._ca()
        for sym in ("XTD.TO", "GDV.TO", "LCS.TO", "PWI.TO", "SBN.TO",
                    "WFS.TO", "PIC.A.TO"):
            self.assertEqual(
                r.income_date(_dist(sym, "2026-01-12", "2025-12-31")),
                "2026-01-12", sym)
        # Off the list, but the description names a split corporation.
        row = _dist("ZZQ.TO", "2026-01-12", "2025-12-31",
                    desc="ZZQ SPLIT CORP CL A DIST ON 1000 SHS REC "
                         "12/31/25 PAY 01/12/26")
        self.assertEqual(r.income_date(row), "2026-01-12")
        # A trust keeps its record date.
        self.assertEqual(r.income_date(_dist("XIC.TO", "2026-01-12",
                                             "2025-12-31")), "2025-12-31")

    @rule("CA-INC-DATE-ROC")
    def test_a2_0231_split_corp_roc_on_pay_date(self):
        r = self._ca()
        row = _dist("XTD.TO", "2026-01-12", "2025-12-31", amt=100.0,
                    action="ADJUST", desc="TDB SPLIT CORP RETURN OF CAPITAL")
        self.assertEqual(r.roc_date(row), "2026-01-12")

    @rule("CA-INC-DATE-TRUST")
    def test_a2_0991_corporate_entry_covers_issuer_classes(self):
        r = self._ca(corporate_distributions=["GHI.TO", "ABC.PR.A",
                                              "DEF.UN"])
        for sym in ("GHI.PR.B.TO", "GHI.TO", "ABC.PR.A.TO", "DEF.UN.TO"):
            self.assertEqual(
                r.income_date(_dist(sym, "2026-01-06", "2025-12-30")),
                "2026-01-06", sym)

    @rule("CA-INC-DATE-TRUST")
    def test_a2_0229_implausible_record_date_uses_pay_date(self):
        r = self._ca()
        row = _dist("XYZ.UN.TO", "2025-01-03", "2023-12-30")
        roc = _dist("XYZ.UN.TO", "2025-01-03", "2023-12-30",
                    action="ADJUST")
        self.assertEqual(r.income_date(row), "2025-01-03")
        self.assertEqual(r.roc_date(roc), "2025-01-03")
        w = r.warnings([row, roc], 2025)
        self.assertEqual(sum("not a plausible declaration" in x
                             for x in w), 2)
        self.assertTrue(all(x.startswith("ATTENTION: income year: ")
                            for x in w))

    @rule("CA-INC-DATE-TRUST")
    def test_a2_0073_cross_year_trust_income_is_flagged(self):
        r = self._ca()
        jan = _dist("XIC.TO", "2025-01-03", "2024-12-30", amt=576.91)
        dec = _dist("XIC.TO", "2026-01-05", "2025-12-30", amt=10.0)
        w = r.warnings([jan, dec], 2025)
        self.assertEqual(len(w), 2)
        self.assertIn("is income of 2024", w[0])
        self.assertIn("NOT in 2025's numbers", w[0])
        self.assertIn("576.91", w[0])
        self.assertIn("counted in 2025 here", w[1])
        # Out of the project's two years: quiet.
        self.assertEqual(r.warnings([jan], 2027), [])
        # A corporation named in the description gets the remedy.
        corp = _dist("ZZC.TO", "2025-01-03", "2024-12-30",
                     desc="ZZC INCOME CORP DIST ON 100 SHS")
        self.assertIn("corporate_distributions", r.warnings([corp], 2025)[0])

    @rule("CA-INC-DATE-TRUST")
    def test_a2_0073_gains_stage_prints_attention(self):
        from taxjson.lib.core import coerce_transaction_row
        from taxjson.lib.pipeline import GainsRequest, run_gains
        row = coerce_transaction_row(
            _dist("XIC.TO", "2025-01-03", "2024-12-30"), 0, "t")
        _r, err = _quiet(run_gains, [row], [], [],
                         req=GainsRequest(country="canada", taxable=True,
                                          year=2025))
        self.assertIn("warning: ATTENTION: income year: XIC.TO", err)

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_a2_0561_ib_roc_warning_quiet_once_pair_entered(self):
        r = self._ca()
        roc = {"action": "ADJUST", "type": "roc", "symbol": "XYZ.UN.TO",
               "date": "2025-01-15", "net_amount": -20.0,
               "currency": "CAD", "account": ACCT}
        self.assertEqual(len(r.warnings([roc], 2025)), 1)
        pair = [{"action": "ADJUST", "symbol": "XYZ.UN.TO",
                 "date": "2024-12-31", "net_amount": -20.0,
                 "currency": "CAD"},
                {"action": "ADJUST", "symbol": "XYZ.UN.TO",
                 "date": "2025-01-15", "net_amount": 20.0,
                 "currency": "CAD"}]
        self.assertEqual(r.warnings([roc] + pair, 2025), [])
        self.assertEqual(len(r.warnings([roc, pair[0]], 2025)), 1)


class TestRicBareRoot(unittest.TestCase):
    """A2-0230, A2-0992: a bare ric_january_dividends entry is that
    fund's US listing only."""

    @rule("US-INC-DATE-RIC")
    def test_bare_root_is_us_listing_only(self):
        from taxjson.lib.income_dating import rules_for
        r = rules_for("usa", {"ric_january_dividends": ["T", "PSA"]})

        def moved(sym):
            return r.ric_prior_year({"action": "DIVIDEND", "symbol": sym,
                                     "date": "2025-01-15"})
        self.assertTrue(moved("T.US"))
        self.assertTrue(moved("PSA.US"))
        self.assertFalse(moved("T.TO"))
        self.assertFalse(moved("T.PR.A.US"))
        self.assertFalse(moved("PSA.PR.H.US"))


class TestListDateIncomeFlags(unittest.TestCase):
    """A2-0995, A2-0996: `list --date` dates a listed corporation's ROC
    like the run (pay date)."""

    @rule("CA-INC-DATE-ROC")
    def test_corporate_roc_stays_on_pay_date(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent

        def row(d, q, p):
            return {"action": "BUYSELL", "date": d, "date_settle": d,
                    "time": "09:30:00", "symbol": "ZZC.TO", "quantity": q,
                    "price": p, "net_amount": abs(q) * p,
                    "currency": "CAD", "account": "margin"}
        txs = [row("2025-01-06", 1000, 10.0), row("2025-03-10", -500, 12.0),
               {"action": "ADJUST", "type": "roc", "date": "2025-03-20",
                "date_settle": "2025-03-20", "time": "00:00:00",
                "record_date": "2025-03-03", "symbol": "ZZC.TO",
                "quantity": 0.0, "net_amount": -500.0, "currency": "CAD",
                "account": "margin"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                'corporate_distributions = ["ZZC.TO"]\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": txs}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "list", "--date", "2025-03-31"],
                cwd=repo, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("4,500.00", r.stdout)
        self.assertNotIn("4,750.00", r.stdout)


if __name__ == "__main__":
    unittest.main()
