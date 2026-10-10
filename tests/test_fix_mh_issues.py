"""Missing-history issues #23, #24, #25, #28 (synthetic data only).

#23: an `OPENING ... cost=unknown` line's symbol goes through ticker.map
     and tobase.map as the rows do — the base-currency books pool it with
     its TOBASE target, the native-currency views keep the listing the
     books trade — so the declared units are never dropped and later
     trades never become short covers.
#24: a dated line's units are held even when no later row trades them
     (a holding simply kept, or one that only pays dividends).
#25: `taxjson migrate` dates each entry from the books of the year that
     sized it, and never archives a missing_history.json whose entries
     were not all converted.
#28: the newest year listing an entry decides it, an explicit "opens
     nothing" included; a year whose books cannot size it does not.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from _style import env
from _tmpfiles import private_dir
from tax_rules import rule

from taxjson.lib.core import TaxTransaction


def tjs(*args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *args],
        capture_output=True, text=True, env=env(TAXJSON_WIDTH=0),
        timeout=900, stdin=subprocess.DEVNULL)


def _ok(tc, r):
    tc.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
    return r


_CUR = {"canada": "CAD", "usa": "USD"}


def _project(country, tt, ticker_map=None):
    d = Path(private_dir()) / "p"
    (d / "inputs" / "margin").mkdir(parents=True)
    (d / "inputs" / "margin" / "demo.tt").write_text(tt)
    (d / "taxjson.toml").write_text(
        "[settings]\nyear = 2024\n" f'country = "{country}"\n'
        f'base_currency = "{_CUR[country]}"\ntax_date = "trade"\n'
        + ('option_grant_timing_since = 2024\n' if country == "canada"
           else '') + '\n'
        '[accounts.margin]\ntype = "taxable"\n')
    if ticker_map is not None:
        (d / "ticker.map").write_text(ticker_map)
    return d


def _gains(d, name="margin_gains"):
    return json.loads((d / "work" / f"{name}.json").read_text())


def _inventory(doc):
    return {i["symbol"]: i["qty"] for i in doc.get("inventory") or []}


def _holdings(d):
    try:
        import tomllib
    except ImportError:                         # Python < 3.11
        import tomli as tomllib
    doc = tomllib.loads((d / "reports" / "margin_holdings.toml")
                        .read_text())
    return {h["symbol"]: h["quantity"] for h in doc.get("holding") or []}


class TestDeclarationFollowsTheMap(unittest.TestCase):
    """#23: the declaration is the security the books pool it with."""

    def _check(self, country, us, base, declared):
        cur = _CUR[country]
        d = _project(country, (
            f"OPENING 2023-12-31 {declared} 10 cost=unknown\n"
            f"BUYSELL 2024-02-01 10:00:00 {us} -5 {cur} 20 99 1\n"
            f"BUYSELL 2024-04-01 10:00:00 {us} 5 {cur} 16 81 1\n"),
            ticker_map=f"TOBASE {us} {base}\n")
        _ok(self, tjs("-C", str(d), "run", "--no-input"))
        g = _gains(d)
        # No short cover: the first sale draws on the unknown-cost units.
        self.assertEqual(g["transactions"], [])
        self.assertEqual(len(g["manual_reporting_required"]), 1)
        self.assertEqual(_inventory(g), {base: 10.0})
        log = [e for e in g["missing_history_log"] if e["inserted"]]
        self.assertEqual([(e["symbol"], e["opening_qty"]) for e in log],
                         [(base, 10.0)])
        # The native views keep the listing the books trade: held there,
        # never short (also when the line is spelled with the base
        # listing the native books never trade).
        self.assertEqual(_inventory(_gains(d, "margin_raw_gains")),
                         {us: 10.0})
        self.assertEqual(_holdings(d), {us: 10.0})
        return d

    @rule("CA-ACB-11")
    def test_canada_mapped_declaration(self):
        self._check("canada", "SYNTH.US", "SYNTH.TO", "SYNTH.US")

    @rule("CA-ACB-11")
    def test_canada_declaration_spelled_as_the_base_listing(self):
        self._check("canada", "SYNTH.US", "SYNTH.TO", "SYNTH.TO")

    @rule("US-BASIS-04")
    def test_usa_mapped_declaration(self):
        self._check("usa", "SYNTH.TO", "SYNTH.US", "SYNTH.TO")

    @rule("CA-ACB-11")
    def test_a_mapping_added_after_the_declaration(self):
        d = _project("canada", (
            "OPENING 2023-12-31 SYNTH.US 10 cost=unknown\n"
            "BUYSELL 2024-02-01 10:00:00 SYNTH.US -5 CAD 20 99 1\n"
            "BUYSELL 2024-03-01 10:00:00 SYNTH.TO 5 CAD 16 81 1\n"))
        _ok(self, tjs("-C", str(d), "run", "--no-input"))
        # Two listings, two pools: the US one holds 5, the TSX one 5.
        self.assertEqual(_inventory(_gains(d)),
                         {"SYNTH.US": 5.0, "SYNTH.TO": 5.0})
        (d / "ticker.map").write_text("TOBASE SYNTH.US SYNTH.TO\n")
        _ok(self, tjs("-C", str(d), "run", "--no-input"))
        g = _gains(d)
        self.assertEqual(g["transactions"], [])
        self.assertEqual(_inventory(g), {"SYNTH.TO": 10.0})
        self.assertEqual(_holdings(d), {"SYNTH.US": 5.0, "SYNTH.TO": 5.0})


def _t(date, sym, qty, action="BUYSELL", new="", cur="USD"):
    return TaxTransaction(action=action, date=date, time="10:00:00",
                          symbol=sym, symbol_new=new, quantity=qty,
                          currency=cur, price=1.0, net_amount=abs(qty),
                          account="margin")


class TestLoadMaps(unittest.TestCase):
    """load_missing_history maps each line as the merge stages map rows."""

    def _proj(self, tt, tmap):
        d = _project("canada", "BUYSELL 2024-01-02 10:00:00 ZZB.TO 1 CAD "
                               "1 2 1\n", ticker_map=tmap)
        (d / "inputs" / "margin" / "missing_history.tt").write_text(tt)
        return d

    def test_base_and_native_views(self):
        from taxjson.lib.missing_history import load_missing_history
        d = self._proj("OPENING 2023-12-31 OLDN.US 4 cost=unknown\n"
                       "OPENING 2023-12-31 GONE.US 1 cost=unknown\n",
                       "GLOBAL OLDN.US SYNTH.US\nTOBASE SYNTH.US SYNTH.TO\n"
                       "DELETE GONE.US\n")
        base = load_missing_history(d)
        self.assertEqual(set(base), {("SYNTH.TO", "margin")})
        t = base.fixed[("SYNTH.TO", "margin")]
        self.assertEqual((t.symbol, t.declared, t.quantity),
                         ("SYNTH.TO", "OLDN.US", 4))
        self.assertEqual(base.base_currency, "CAD")
        self.assertEqual([p for p, _w in base.deleted],
                         [("GONE.US", "margin")])
        native = load_missing_history(d, native=True)
        self.assertEqual(set(native), {("SYNTH.US", "margin")})
        self.assertEqual(native.listings[("SYNTH.US", "margin")],
                         ("SYNTH.TO",))

    def test_two_lines_one_security(self):
        from taxjson.lib.missing_history import (TtOpeningError,
                                                 load_missing_history)
        d = self._proj("OPENING 2023-12-31 SYNTH.US 4 cost=unknown\n"
                       "OPENING 2023-12-31 SYNTH.TO 6 cost=unknown\n",
                       "TOBASE SYNTH.US SYNTH.TO\n")
        self.assertEqual(load_missing_history(d).fixed[
            ("SYNTH.TO", "margin")].quantity, 10)
        # The native views keep them apart.
        self.assertEqual(len(load_missing_history(d, native=True).fixed), 2)
        (d / "inputs" / "margin" / "missing_history.tt").write_text(
            "OPENING 2023-12-31 SYNTH.US 4 cost=unknown\n"
            "OPENING 2024-01-05 SYNTH.TO 6 cost=unknown\n")
        with self.assertRaises(TtOpeningError) as cm:
            load_missing_history(d)
        self.assertIn("opened on two dates", str(cm.exception))


class TestTheBooksSymbol(unittest.TestCase):
    """synthesize_openings: the symbol the units carry on the line's date
    in these books."""

    def _apply(self, txs, sym, day, qty, **attrs):
        from taxjson.lib.missing_history import (MissingHistoryPairs,
                                                 TtOpening,
                                                 synthesize_openings)
        pairs = MissingHistoryPairs({(sym, "margin")})
        pairs.fixed[(sym, "margin")] = TtOpening(
            "margin", day, sym, qty, "inputs/margin/missing_history.tt:1")
        for k, v in attrs.items():
            setattr(pairs, k, v)
        with contextlib.redirect_stderr(io.StringIO()):
            out, log = synthesize_openings(txs, pairs)
        return [t for t in out if t.action == "OPENING_BALANCE"], log

    @rule("US-BASIS-04")
    def test_a_rename_before_the_date_opens_the_new_symbol(self):
        txs = [_t("2024-03-01", "OLDQ.US", 1, "SPLIT", "NEWQ.US"),
               _t("2024-06-01", "NEWQ.US", -8)]
        ob, _log = self._apply(txs, "OLDQ.US", "2024-04-01", 8)
        self.assertEqual([(t.symbol, t.quantity) for t in ob],
                         [("NEWQ.US", 8.0)])

    def test_native_view_opens_the_traded_listing(self):
        txs = [_t("2024-02-01", "SYNTH.US", -5)]
        ob, _log = self._apply(txs, "SYNTH.TO", "2023-12-31", 10,
                               view="native",
                               listings={("SYNTH.TO", "margin"):
                                         ("SYNTH.US",)})
        self.assertEqual([(t.symbol, t.currency) for t in ob],
                         [("SYNTH.US", "USD")])


class TestHeldWithoutLaterRows(unittest.TestCase):
    """#24: the declared units are held with no later trade."""

    def _check(self, country):
        cur = _CUR[country]
        d = _project(country, (
            "OPENING 2023-12-31 IDLE.TO 10 cost=unknown\n"
            "OPENING 2023-12-31 DIVY.US 7 cost=unknown\n"
            f"BUYSELL 2024-02-01 10:00:00 OTHER.US 1 {cur} 20 21 1\n"
            "DIVIDEND 2024-03-01 10:00:00 DIVY.US 0 USD 0 5\n"))
        r = _ok(self, tjs("-C", str(d), "run", "--no-input"))
        self.assertNotIn("nothing was applied", r.stdout + r.stderr)
        want = {"IDLE.TO": 10.0, "DIVY.US": 7.0, "OTHER.US": 1.0}
        self.assertEqual(_inventory(_gains(d)), want)
        self.assertEqual(_inventory(_gains(d, "margin_raw_gains")), want)
        self.assertEqual(_holdings(d), want)
        log = {e["symbol"]: e for e in _gains(d)["missing_history_log"]}
        self.assertTrue(log["IDLE.TO"]["inserted"])
        self.assertIn("declared units are held", log["IDLE.TO"]["note"])
        return d

    @rule("CA-ACB-11")
    def test_canada(self):
        d = self._check("canada")
        # The T1135 view holds the idle US holding (cost unknown).
        r = _ok(self, tjs("-C", str(d), "t1135"))
        self.assertIn("DIVY.US: unknown ACB", r.stdout)

    @rule("US-BASIS-04")
    def test_usa(self):
        self._check("usa")

    def test_another_accounts_line_opens_nothing_here(self):
        from taxjson.lib.missing_history import (MissingHistoryPairs,
                                                 TtOpening,
                                                 synthesize_openings)
        pairs = MissingHistoryPairs({("IDLE.TO", "other")})
        pairs.fixed[("IDLE.TO", "other")] = TtOpening(
            "other", "2023-12-31", "IDLE.TO", 3, "inputs/other/a.tt:1")
        out, log = synthesize_openings([_t("2024-01-02", "ZZB.US", 1)],
                                       pairs)
        self.assertFalse([t for t in out if t.action == "OPENING_BALANCE"])
        self.assertFalse(log[0]["inserted"])

    def test_no_currency_is_an_error_naming_the_line(self):
        from taxjson.lib.missing_history import (MissingHistoryPairs,
                                                 TtOpening, TtOpeningError,
                                                 synthesize_openings)
        pairs = MissingHistoryPairs({("IDLE", "margin")})
        pairs.view = "native"
        pairs.fixed[("IDLE", "margin")] = TtOpening(
            "margin", "2023-12-31", "IDLE", 3, "inputs/margin/a.tt:4")
        txs = [_t("2024-01-02", "ZZB.US", 1),
               _t("2024-01-03", "ZZC.TO", 1, cur="CAD")]
        with self.assertRaises(TtOpeningError) as cm:
            synthesize_openings(txs, pairs)
        self.assertIn("inputs/margin/a.tt:4", str(cm.exception))


def _years(accounts, logs):
    """2024/ and 2025/ sharing ../inputs, each with a taxable account
    (accounts[year]), a missing_history.json listing SYNTH.TO in it, its
    books (one sale) and a gains file whose log sizes the entry
    (logs[year]: the log entry's fields)."""
    top = Path(private_dir()) / "p"
    (top / "inputs").mkdir(parents=True)
    for y in (2024, 2025):
        acct = accounts[y]
        d = top / str(y)
        (d / "work").mkdir(parents=True)
        (top / "inputs" / acct).mkdir(exist_ok=True)
        (d / "taxjson.toml").write_text(
            f'[settings]\nyear = {y}\ncountry = "canada"\n'
            'base_currency = "CAD"\ninputs_dir = "../inputs"\n\n'
            f'[accounts.{acct}]\ntype = "taxable"\n')
        (d / "missing_history.json").write_text(json.dumps(
            [{"symbol": "SYNTH.TO", "account": acct}]))
        (d / "work" / f"{acct}_base.json").write_text(json.dumps(
            {"transactions": [{
                "action": "BUYSELL", "date": "2024-02-01",
                "time": "10:00:00", "symbol": "SYNTH.TO", "quantity": -5,
                "price": 10, "net_amount": 49, "currency": "CAD",
                "account": acct}]}))
    time.sleep(0.05)
    for y in (2024, 2025):
        acct = accounts[y]
        g = top / str(y) / "work" / f"{acct}_gains.json"
        g.write_text(json.dumps({"missing_history_log": [dict(
            {"account": acct, "symbol": "SYNTH.TO"}, **logs[y])]}))
        later = os.stat(top / str(y) / "missing_history.json").st_mtime + 2
        os.utime(g, (later, later))
    return top


_OPENS_5 = {"inserted": True, "opening_qty": 5, "anchor_date": "2024-01-31",
            "anchor_symbol": "SYNTH.TO"}


class TestMigrateDatesEachYearsBooks(unittest.TestCase):
    """#25: an account only an older year's books carry."""

    def test_older_years_account_converted(self):
        top = _years({2024: "margin", 2025: "newmargin"},
                     {2024: _OPENS_5, 2025: _OPENS_5})
        r = _ok(self, tjs("-C", str(top / "2025"), "migrate", "--write"))
        for acct in ("margin", "newmargin"):
            tt = (top / "inputs" / acct / "missing_history.tt").read_text()
            self.assertIn("OPENING 2024-01-31 SYNTH.TO 5 cost=unknown", tt)
        for y in (2024, 2025):
            self.assertFalse((top / str(y) / "missing_history.json")
                             .exists(), r.stdout)

    def test_an_entry_not_converted_keeps_its_file(self):
        top = _years({2024: "margin", 2025: "newmargin"},
                     {2024: _OPENS_5, 2025: _OPENS_5})
        (top / "2024" / "work" / "margin_base.json").unlink()
        r = tjs("-C", str(top / "2025"), "migrate", "--write")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("SYNTH.TO / margin", r.stdout + r.stderr)
        self.assertIn("2024/missing_history.json", r.stdout + r.stderr)
        for y in (2024, 2025):
            self.assertTrue((top / str(y) / "missing_history.json")
                            .exists())
        self.assertFalse((top / "inputs" / "newmargin" /
                          "missing_history.tt").exists())


class TestMigrateNewestListedDecides(unittest.TestCase):
    """#28: an explicit "opens nothing" in the newest year wins; a year
    that does not list the entry does not decide."""

    def test_explicit_zero_in_the_newest_year(self):
        top = _years({2024: "margin", 2025: "margin"},
                     {2024: _OPENS_5,
                      2025: {"inserted": False, "opening_qty": 0,
                             "note": "no opening needed"}})
        r = tjs("-C", str(top / "2025"), "migrate")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("2024: 5, 2025: opens nothing (no opening needed) — "
                      "written: nothing (2025's view", r.stdout)
        r = _ok(self, tjs("-C", str(top / "2025"), "migrate", "--write"))
        self.assertFalse((top / "inputs" / "margin" /
                          "missing_history.tt").exists())
        self.assertFalse((top / "2024" / "missing_history.json").exists())

    def test_absent_from_the_newest_year(self):
        top = _years({2024: "margin", 2025: "margin"},
                     {2024: _OPENS_5, 2025: _OPENS_5})
        (top / "2025" / "missing_history.json").write_text("[]")
        g = top / "2025" / "work" / "margin_gains.json"
        later = os.stat(top / "2025" / "missing_history.json").st_mtime + 2
        g.write_text(json.dumps({"missing_history_log": []}))
        os.utime(g, (later, later))
        r = tjs("-C", str(top / "2025"), "migrate")
        self.assertIn("2024: 5, 2025: not listed — written: 5 (2024's "
                      "view)", r.stdout)
        _ok(self, tjs("-C", str(top / "2025"), "migrate", "--write"))
        tt = (top / "inputs" / "margin" / "missing_history.tt").read_text()
        self.assertIn("OPENING 2024-01-31 SYNTH.TO 5 cost=unknown", tt)

    def test_no_rows_in_the_newest_year_does_not_decide(self):
        top = _years({2024: "margin", 2025: "margin"},
                     {2024: _OPENS_5,
                      2025: {"inserted": False, "opening_qty": 0,
                             "note": "no rows for this symbol/account"}})
        r = tjs("-C", str(top / "2025"), "migrate")
        self.assertIn("written: 5 (2024's view)", r.stdout)


if __name__ == "__main__":
    unittest.main()
