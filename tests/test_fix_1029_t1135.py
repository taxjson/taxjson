"""T1135 fixes of 2026-10-29: a Canadian issuer's shares held with a
custodian outside Canada (backlog TJ-0369; tax-logic CA-RPT-02,
CA-RPT-18), the standalone tool's income and gain columns without a
gains file (TJ-0355) and the country of a foreign issuer listed only in
Toronto (TJ-0294, a pin).

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented account names, amounts under 1,000.
"""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.bin.taxjson_t1135 import (build_report, main, render_brief,
                                       render_report)
from taxjson.lib import out as _out
from taxjson.lib import tobase_map as TB
from test_tobase_map import _master, _project


def _r(action, date, symbol, qty, net, account="ibus", **kw):
    """A base-book row; net signed as the parsers write it (a buy
    negative, a sale positive)."""
    row = {"action": action, "date": date, "date_settle": date,
           "time": "10:00:00", "symbol": symbol, "quantity": qty,
           "net_amount": net, "currency": "CAD", "account": account,
           "id": kw.pop("id", f"{action}-{date}-{symbol}-{qty}-{account}")}
    row.update(kw)
    return row


def _report(rows, gains=None, **kw):
    """build_report over one base file per account (named
    <account>_base.json) and the gains docs {file name: doc}."""
    with tempfile.TemporaryDirectory() as td:
        by_acct = {}
        for r in rows:
            by_acct.setdefault(r.get("account") or "book", []).append(r)
        bases = []
        for acct, rs in by_acct.items():
            p = Path(td) / f"{acct}_base.json"
            p.write_text(json.dumps({"transactions": rs}))
            bases.append(p)
        gpaths = []
        for name, doc in (gains or {}).items():
            p = Path(td) / name
            p.write_text(json.dumps(doc))
            gpaths.append(p)
        kw.setdefault("today", "2026-06-01")
        kw.setdefault("full_history", False)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rep = build_report(bases, gpaths, 2025, kw.pop("overrides", {}),
                               "CAD", **kw)
        rep["_stderr"] = " ".join(err.getvalue().split())
        return rep


def _props(rep):
    return {(p["symbol"], p.get("custody_account")): p
            for p in rep["properties"]}


def _no_gains(*accounts):
    """A gains file per account, as a run writes it (no row in it)."""
    return {f"{a}_gains.json": {"transactions": []} for a in accounts}


BUY = _r("BUYSELL", "2025-01-02", "QZCAN.TO", 10, -600.0)


# ============================================== TJ-0369 foreign custody
class TestForeignCustody(unittest.TestCase):

    @rule("CA-RPT-02")
    @rule("CA-RPT-18")
    def test_a_canadian_issuer_held_abroad_is_a_category_6_row(self):
        rep = _report([BUY], _no_gains("ibus"), custody={"ibus": "USA"})
        row = _props(rep)[("QZCAN.TO", "ibus")]
        self.assertEqual(
            (row["country"], row["category"], row["category_from"],
             row["max_cost"], row["year_end_cost"]),
            ("USA", 6, "custody", 600.0, 600.0))
        self.assertEqual(rep["max_total_cost"], 600.0)
        self.assertEqual(rep["custodians"], {"ibus": "USA"})
        self.assertEqual(rep["by_country"]["USA"]["max_cost"], 600.0)
        brief = render_brief(rep)
        self.assertRegex(brief, r"QZCAN\.TO +USA +6 ")
        self.assertIn("QZCAN.TO (ibus): a Canadian issuer held with a "
                      "custodian outside Canada, CAT 6 under its country",
                      brief)
        self.assertIn("QZCAN.TO: a Canadian issuer held with a custodian "
                      "outside Canada ([accounts.ibus] custodian_country)",
                      " ".join(render_report(rep).split()))

    @rule("CA-RPT-02")
    def test_unset_or_canada_is_not_foreign_property(self):
        for custody in (None, {}, {"ibus": None}, {"margin": "USA"}):
            with self.subTest(custody=custody):
                rep = _report([BUY], _no_gains("ibus"), custody=custody)
                self.assertEqual(rep["properties"], [])
                self.assertEqual(rep["max_total_cost"], 0.0)

    @rule("CA-RPT-02")
    @rule("CA-RPT-18")
    def test_foreign_property_in_the_account_keeps_its_own_country(self):
        rep = _report([_r("BUYSELL", "2025-01-02", "QZA.US", 10, -500.0),
                       _r("BUYSELL", "2025-01-02", "QZB.TO", 10, -400.0),
                       BUY],
                      _no_gains("ibus"), custody={"ibus": "USA"},
                      issuer_countries={"QZB.TO": "BMU"})
        got = {(p["symbol"], p["country"], p["category"],
                p.get("custody_account")) for p in rep["properties"]}
        self.assertEqual(got, {("QZA.US", "USA", 2, None),
                               ("QZB.TO", "BMU", 2, None),
                               ("QZCAN.TO", "USA", 6, "ibus")})
        self.assertEqual(rep["max_total_cost"], 1500.0)

    @rule("CA-RPT-02")
    def test_income_and_gain_follow_the_custody_row(self):
        rows = [BUY, _r("BUYSELL", "2025-06-02", "QZCAN.TO", -5, 400.0),
                _r("BUYSELL", "2025-01-02", "QZCAN.TO", 10, -900.0,
                   account="margin")]
        gains = {
            "ibus_gains.json": {"transactions": [
                {"action": "DIVIDEND", "symbol": "QZCAN.TO",
                 "date": "2025-03-03", "dividend": 20.0, "account": "ibus"},
                {"action": "BUYSELL", "symbol": "QZCAN.TO",
                 "date": "2025-06-02", "qty": 5, "gain": 100.0,
                 "account": "ibus"}]},
            # The account held in Canada: not on the custody row.
            "margin_gains.json": {"metadata": {"account": "margin"},
                                  "transactions": [
                {"action": "DIVIDEND", "symbol": "QZCAN.TO",
                 "date": "2025-03-03", "dividend": 30.0}]}}
        rep = _report(rows, gains, custody={"ibus": "USA"})
        (row,) = rep["properties"]
        # Held in a Canadian account too: one pool of 20 at 1500.00
        # (75.00 a share, s.47), the custody row its 10, then 5, shares
        # of it — not the account's own 60.00 a share.
        self.assertEqual((row["symbol"], row["income"], row["gain"],
                          row["max_cost"], row["year_end_cost"]),
                         ("QZCAN.TO", 20.0, 100.0, 750.0, 375.0))
        self.assertNotIn("custody_mixed_symbols", rep)
        self.assertNotIn("another account", rep["_stderr"])
        self.assertEqual([ln for ln in _out.act_lines(render_brief(rep))
                          if "QZCAN.TO" in ln], [])

    @rule("CA-RPT-02")
    def test_a_split_or_rename_booked_in_another_account_moves_it(self):
        # A 2:1 split each account's books carry (one event), then a
        # rename only the Canadian account's books carry (a .tt RENAME is
        # recorded once): the custody pool takes each once.
        rows = [BUY,
                _r("SPLIT", "2025-02-03", "QZCAN.TO", 2.0, 0.0),
                _r("SPLIT", "2025-02-03", "QZCAN.TO", 2.0, 0.0,
                   account="margin"),
                _r("SPLIT", "2025-03-03", "QZCAN.TO", 1.0, 0.0,
                   account="margin", symbol_new="QZNEW.TO"),
                _r("BUYSELL", "2025-04-01", "QZNEW.TO", -10, 500.0)]
        rep = _report(rows, _no_gains("ibus", "margin"),
                      custody={"ibus": "USA"})
        (row,) = rep["properties"]
        # 20 shares after the split, 10 sold: half the cost left.
        self.assertEqual((row["symbol"], row["custody_account"],
                          row["max_cost"], row["year_end_cost"]),
                         ("QZNEW.TO", "ibus", 600.0, 300.0))

    @rule("CA-RPT-02")
    @rule("CA-RPT-18")
    def test_the_custody_row_is_its_share_of_the_pooled_acb(self):
        # One issuer in a Canadian account and in the custody account,
        # bought at different prices: one s.47 pool. By hand —
        #   2024-06-03 margin buys 30 for 300.00: pool 30, 300.00
        #   2025-01-06 ibus buys 10 for 200.00:   pool 40, 500.00
        #              ibus 10 x 12.50                    = 125.00
        #   2025-03-03 margin buys 20 for 600.00: pool 60, 1100.00
        #              ibus 10 x 18.3333                  = 183.33
        #   2025-05-01 ibus sells 4: 4 x 18.3333 leaves; pool 56,
        #              1026.67; ibus 6 x 18.3333         = 110.00
        #   2025-08-01 margin moves 10 to ibus (its own account): the
        #              pool is as it was; ibus 16 x 18.3333 = 293.33
        # The account's own purchases would read 200.00, then 120.00.
        from taxjson.bin.taxjson_split_gains import _split_inventory
        move = "own-account move #1: margin -> ibus (not a sale)"
        rows = [_r("BUYSELL", "2024-06-03", "QZP.TO", 30, -300.0,
                   account="margin"),
                _r("BUYSELL", "2025-01-06", "QZP.TO", 10, -200.0),
                _r("BUYSELL", "2025-03-03", "QZP.TO", 20, -600.0,
                   account="margin"),
                _r("BUYSELL", "2025-05-01", "QZP.TO", -4, 120.0),
                _r("TRANSFER", "2025-08-01", "QZP.TO", -10, 0.0,
                   account="margin", type="lot_move", description=move),
                _r("TRANSFER", "2025-08-01", "QZP.TO", 10, 0.0,
                   type="lot_move", description=move)]
        rep = _report(rows, _no_gains("ibus", "margin"),
                      custody={"ibus": "USA"}, today="2026-03-02")
        (row,) = rep["properties"]
        self.assertEqual((row["symbol"], row["custody_account"],
                          row["country"], row["category"],
                          row["max_cost"], row["year_end_cost"],
                          row["unknown_acb"]),
                         ("QZP.TO", "ibus", "USA", 6, 293.33, 293.33,
                          False))
        self.assertEqual((rep["max_total_cost"], rep["max_total_date"]),
                         (293.33, "2025-08-01"))
        self.assertNotIn("custody_mixed_symbols", rep)
        # The same share `taxjson list --date` gives the account of the
        # engine's own year-end pool (taxjson-split-gains).
        from taxjson.lib.pipeline import GainsRequest, run_gains
        from taxjson.lib.core import coerce_transaction_row
        with contextlib.redirect_stderr(io.StringIO()):
            res = run_gains(
                [coerce_transaction_row(r, i, "t") for i, r in
                 enumerate(rows)], [], (),
                GainsRequest(country="canada", year=2025, taxable=True,
                             phantom_hint=False))
        (pool,) = res["inventory_year_end"]
        self.assertEqual((pool["qty"], round(pool["total_cost"], 2)),
                         (56.0, 1026.67))
        (mine,) = _split_inventory(res["inventory_year_end"], "ibus",
                                   [r for r in rows
                                    if r["account"] == "ibus"],
                                   {}, "2025-12-31")
        self.assertEqual((mine["qty"], round(mine["total_cost"], 2)),
                         (16.0, row["year_end_cost"]))
        # The issuer's whole pool is never a row: the rest of it (40
        # shares, 733.33) is held in Canada.
        self.assertEqual(rep["by_country"],
                         {"USA": {"max_cost": 293.33,
                                  "year_end_cost": 293.33,
                                  "income": 0.0, "gain": 0.0}})

    @rule("CA-RPT-02")
    def test_a_canadian_isin_line_names_custody(self):
        # The Canadian-ISIN warning no longer says such shares are never
        # foreign property, and a custody pool is never named by it.
        from taxjson.bin import taxjson_t1135 as T
        rows = [_r("BUYSELL", "2025-01-02", "QZW.US", 10, -500.0,
                   issuer_country="CA")]
        with mock.patch.object(_out, "warn") as w, \
                mock.patch.object(T, "_DETAILS", True):
            _report(rows, _no_gains("ibus"))
        text = " ".join(" ".join(c.kwargs.get("details") or [])
                        for c in w.call_args_list)
        self.assertIn("unless held with a custodian outside Canada", text)
        rep = _report([dict(BUY, issuer_country="CA")],
                      _no_gains("ibus"), custody={"ibus": "USA"})
        self.assertEqual(rep["canadian_issuer_symbols"], [])
        self.assertEqual(len(rep["properties"]), 1)

    @rule("CA-RPT-02")
    def test_the_standalone_flag(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "ibus_base.json"
            base.write_text(json.dumps({"transactions": [BUY]}))
            gains = Path(td) / "ibus_gains.json"
            gains.write_text(json.dumps({"transactions": []}))
            argv = [str(base), "--gains", str(gains), "--year", "2025",
                    "--year-wash-only", "--option-premium-timing", "close",
                    "--json"]
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = main(argv + ["--custodian", "ibus=usa"])
            self.assertEqual(rc, 0)
            (row,) = json.loads(out.getvalue())["properties"]
            self.assertEqual((row["country"], row["category"]), ("USA", 6))
            for bad in ("ibus", "ibus=US", "ibus=EXCLUDE", "=USA"):
                with self.subTest(bad=bad):
                    err = io.StringIO()
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(err), \
                            self.assertRaises(SystemExit) as cm:
                        main(argv + ["--custodian", bad])
                    self.assertEqual(cm.exception.code, 2)
                    self.assertIn("--custodian", err.getvalue())


class TestCustodianCountryKey(unittest.TestCase):

    def test_the_key_is_checked_like_the_other_account_keys(self):
        from taxjson.lib.config_check import (ACCOUNT_KEYS,
                                              account_type_problems)
        self.assertIn("custodian_country", ACCOUNT_KEYS)

        def probs(v):
            return account_type_problems({"accounts": {"ibus": {
                "type": "taxable", "custodian_country": v}}})
        for good in ("USA", "usa", "CAN", "CA", "Canada", "GBR"):
            self.assertEqual(probs(good), [], good)
        for bad in ("US", "EXCLUDE", 840, "", True):
            with self.subTest(bad=bad):
                (p,) = probs(bad)
                self.assertIn("[accounts.ibus] custodian_country must be",
                              p)

    @rule("CA-RPT-02")
    def test_canada_only(self):
        from taxjson.lib.country import config_country_problems
        cfg = {"settings": {"country": "usa"},
               "accounts": {"ibus": {"type": "taxable",
                                     "custodian_country": "USA"}}}
        (p,) = config_country_problems(cfg)
        self.assertIn("[accounts.ibus] custodian_country is Canada-only", p)
        cfg["settings"]["country"] = "canada"
        cfg["settings"]["base_currency"] = "CAD"
        self.assertEqual(config_country_problems(cfg), [])

    @rule("CA-RPT-02")
    def test_tjs_t1135_passes_it(self):
        from taxjson.bin.taxjson_run import cmd_t1135
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n'
                '[accounts.ibus]\ntype = "taxable"\n'
                'custodian_country = "USA"\n\n'
                '[accounts.margin]\ntype = "taxable"\n')
            work = root / "work"
            work.mkdir()
            (work / "ibus_base.json").write_text(
                json.dumps({"transactions": [BUY]}))
            (work / "margin_base.json").write_text(json.dumps(
                {"transactions": [dict(BUY, account="margin",
                                       id="m1")]}))
            for a in ("ibus", "margin"):
                (work / f"{a}_gains.json").write_text(
                    json.dumps({"transactions": []}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                cmd_t1135(argparse.Namespace(dir=str(root), json=True))
            self.assertEqual(cm.exception.code, 0)
        rep = json.loads(out.getvalue())
        self.assertEqual(rep["custodians"], {"ibus": "USA"})
        self.assertEqual([(p["symbol"], p["custody_account"],
                           p["year_end_cost"]) for p in rep["properties"]],
                         [("QZCAN.TO", "ibus", 600.0)])


# ============================================== TJ-0355 no gains file
class TestNoGainsFile(unittest.TestCase):

    ROWS = [_r("BUYSELL", "2025-01-02", "QZA.US", 10, -500.0,
               account="margin"),
            _r("DIVIDEND", "2025-06-02", "QZA.US", 0, 50.0,
               account="margin"),
            _r("BUYSELL", "2025-12-01", "QZA.US", -5, 300.0,
               account="margin")]

    def test_the_columns_are_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": self.ROWS}))
            argv = [str(base), "--year", "2025", "--year-wash-only",
                    "--option-premium-timing", "close"]
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = main(argv + ["--json"])
            self.assertEqual(rc, 0)
            rep = json.loads(out.getvalue())
            (row,) = rep["properties"]
            self.assertEqual((row["symbol"], row["income"], row["gain"],
                              row["max_cost"]),
                             ("QZA.US", None, None, 500.0))
            self.assertEqual(rep["by_country"]["USA"]["income"], None)
            self.assertEqual(rep["by_country"]["USA"]["max_cost"], 500.0)
            self.assertEqual(rep["gains_not_given"], ["margin"])
            self.assertIn("no gains file (--gains) for margin: the INCOME "
                          "and GAIN(LOSS) columns of every property read "
                          "n/a, not 0", " ".join(err.getvalue().split()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                main(argv)
        text = out.getvalue()
        self.assertRegex(text, r"QZA\.US +USA +2 +500\.00 +250\.00 +n/a +n/a")
        self.assertIn("INCOME and GAIN(LOSS) n/a: no gains file (--gains) "
                      "for margin", " ".join(text.split()))

    def test_books_without_an_account_name_the_file(self):
        rows = [{k: v for k, v in r.items() if k != "account"}
                for r in self.ROWS]
        rep = _report(rows)
        self.assertEqual(rep["gains_not_given"], ["book_base.json"])
        self.assertIsNone(rep["properties"][0]["income"])

    def test_an_account_whose_gains_file_was_not_given(self):
        rows = self.ROWS + [
            _r("BUYSELL", "2024-01-02", "QZB.US", 10, -400.0, account="ib"),
            _r("BUYSELL", "2025-03-03", "QZB.US", -10, 450.0, account="ib"),
            # Held all year, no row in it: nothing unknown.
            _r("BUYSELL", "2024-01-02", "QZC.US", 10, -300.0, account="ib")]
        gains = {"margin_gains_wash.json": {"transactions": [
            {"action": "DIVIDEND", "symbol": "QZA.US", "date": "2025-06-02",
             "dividend": 50.0, "account": "margin"},
            {"action": "BUYSELL", "symbol": "QZA.US", "date": "2025-12-01",
             "qty": 5, "gain": 50.0, "account": "margin"}]}}
        rep = _report(rows, gains)
        got = {p["symbol"]: (p["income"], p["gain"])
               for p in rep["properties"]}
        self.assertEqual(got, {"QZA.US": (50.0, 50.0),
                               "QZB.US": (None, None),
                               "QZC.US": (0.0, 0.0)})
        self.assertEqual(rep["gains_not_given"], ["ib"])
        self.assertEqual(rep["by_country"]["USA"]["gain"], None)
        self.assertIn("no gains file (--gains) for ib: the INCOME and "
                      "GAIN(LOSS) columns of its properties read n/a",
                      rep["_stderr"])

    def test_every_account_covered_or_coverage_unknown(self):
        # A gains file per account (by its name, metadata or rows), or
        # one that names no account at all (a hand-run file): no n/a.
        for gains in (_no_gains("margin"),
                      {"x.json": {"metadata": {"account": "margin"},
                                  "transactions": []}},
                      {"gains.json": {"transactions": []}}):
            with self.subTest(gains=list(gains)):
                rep = _report(self.ROWS, gains)
                self.assertEqual(rep["gains_not_given"], [])
                self.assertEqual(rep["properties"][0]["income"], 0.0)
                self.assertNotIn("no gains file", rep["_stderr"])


# ============================================== TJ-0294 (pin)
class TestTorontoOnlyForeignIssuer(unittest.TestCase):
    """A foreign issuer the master knows, its shares listed in Toronto
    and over the counter in the US only (no US exchange listing): the
    project's tobase.map pools the OTC line under the TSX listing with
    the issuer's country, so the TSX shares are foreign property under
    it (the shipped master's four such issuers all have an OTC line)."""

    FIGI = "BBG000000T94"

    @rule("CA-RPT-02")
    @rule("CA-XLIST-06")
    def test_the_tsx_listing_takes_the_masters_country(self):
        m = _master({self.FIGI: {
            "name": "QZ BERMUDA MINING", "kind": "share",
            "share_class_figi": self.FIGI, "domicile": "BM",
            "ca": ["QZBM.TO"], "us": [], "us_exchange": [],
            "us_otc": ["QZBMF.US"]}})
        text = TB.render(m)
        self.assertIn("TOBASE QZBMF.US QZBM.TO", text)
        self.assertIn("country=BMU", text)
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=text)
            self.assertEqual(TB.t1135_countries(root / "ticker.map"),
                             {"QZBM.TO": "BMU", "QZBMF.US": "BMU"})
            base = root / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                _r("BUYSELL", "2025-02-03", "QZBM.TO", 10, -500.0,
                   account="margin")]}))
            gains = root / "margin_gains.json"
            gains.write_text(json.dumps({"transactions": []}))
            out = io.StringIO()
            with mock.patch.object(TB, "load_master", return_value=m), \
                    contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = main([str(base), "--gains", str(gains), "--year",
                           "2025", "--map", str(root / "ticker.map"),
                           "--year-wash-only", "--json"])
            self.assertEqual(rc, 0)
        (row,) = json.loads(out.getvalue())["properties"]
        self.assertEqual((row["symbol"], row["country"], row["category"]),
                         ("QZBM.TO", "BMU", 2))


if __name__ == "__main__":
    unittest.main()
