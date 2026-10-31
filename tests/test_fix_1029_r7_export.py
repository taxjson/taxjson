"""Fix round 2026-10-29 (round 7), group export: the holdings snapshot
(`taxjson-export --holdings-toml`) read back, filtered and re-written,
and the trade history it embeds.

TJ-0873  A Questrade snapshot's average_entry_price (and a book_cost,
         cost_basis or cost_per_share) is the holding's cost when it is
         read back; a holding with no cost at all is cost_unknown, never
         a cost of 0.
TJ-0874  The snapshot's own date ([meta] as_of, else the day of its
         generated_at) is kept as [meta] as_of; snapshots of different
         days are refused.
TJ-0875  A holding's asset_type = "crypto" is kept.
TJ-0876  A holding whose cost is unknown is never dropped as dust.
TJ-0877  A snapshot whose [meta] holdings_count is not its number of
         [[holding]] tables is refused, as `tjs sanity` refuses it.
TJ-0881  --long / --short keep every currency of a mixed-currency
         holding.
TJ-0882  A listing move keeps a mixed-currency holding mixed.
TJ-0883  --map's GLOBAL renames apply to a snapshot read back.
TJ-0884  The trade history takes a split ahead of its day's trades, as
         the Canadian engine does, whatever the split's clock time.
TJ-0886  A US project's trade history is in trade-date order.
TJ-0887  An opening balance is no purchase in the trade history.
TJ-0733  A holding's base-currency cost (base_total_cost) and its
         cost_source = "blended" are kept.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from _style import CapturedWidth

from taxjson.bin import taxjson_export as export
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.positions_reports import (PositionsReportError,
                                           read_positions)
from taxjson.lib.tomlcompat import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _export(files, *args):
    """Run taxjson-export with `files` ({name: text}) written in a folder
    of their own; `args` name them as {name}. (CompletedProcess, the
    parsed snapshot or None, the positions reader's read-back of it)."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, text in files.items():
            (root / name).write_text(text)
        argv = [str(root / a[1:-1]) if a.startswith("{") else a
                for a in args]
        env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="0")
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_export", argv),
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=120)
        snap = r.returncode == 0 and "--holdings-toml" in args
        doc = tomllib.loads(r.stdout) if snap else None
        back = None
        if snap:
            out = root / "out.toml"
            out.write_text(r.stdout)
            try:
                back = read_positions(out)
            except PositionsReportError as e:
                back = str(e)
        return r, doc, back


def _holding(doc, sym):
    return next(h for h in doc.get("holding", []) if h["symbol"] == sym)


_FETCH = """\
# Live Questrade holdings snapshot — tjs fetch/verify.
[meta]
account = "margin"
broker_account = "99900001"
source = "questrade-api"
generated_at = "2025-12-31 16:30:00"
holdings_count = 1

[[holding]]
symbol = "SYN.TO"
quantity = 10.0
currency = "CAD"
average_entry_price = 12.0
"""


class TestCostReadBack(unittest.TestCase):
    """TJ-0873."""

    def test_average_entry_price_is_the_cost(self):
        r, doc, back = _export({"q.toml": _FETCH}, "--holdings-toml",
                               "{q.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.TO")
        self.assertEqual(h["total_cost"], 120.0)
        self.assertEqual(h["cost_per_share"], 12.0)
        self.assertNotIn("cost_unknown", h)
        self.assertEqual(back.rows[0].cost, 120.0)

    def test_other_cost_spellings(self):
        for key, val, want in (("book_cost", 90, 90.0),
                               ("cost_basis", 80, 80.0),
                               ("cost_per_share", 7, 70.0)):
            text = ('[meta]\n[[holding]]\nsymbol = "SYN.TO"\n'
                    f'quantity = 10\ncurrency = "CAD"\n{key} = {val}\n')
            r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                                "{h.toml}")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_holding(doc, "SYN.TO")["total_cost"], want,
                             key)

    def test_option_average_price_is_per_share(self):
        text = ('[meta]\n[[holding]]\nsymbol = "SYN260116C00010000.US"\n'
                'quantity = 2\ncurrency = "USD"\n'
                'average_entry_price = 1.5\n')
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = doc["holding"][0]
        self.assertEqual(h["total_cost"], 300.0)        # 2 x 100 x 1.5
        text = text.replace("quantity = 2", "quantity = 2\nmultiplier = 10")
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        self.assertEqual(doc["holding"][0]["total_cost"], 30.0)

    def test_no_cost_at_all_is_unknown_not_zero(self):
        text = ('[meta]\n[[holding]]\nsymbol = "SYN.TO"\nquantity = 10\n'
                'currency = "CAD"\n')
        r, doc, back = _export({"h.toml": text}, "--holdings-toml",
                               "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.TO")
        self.assertTrue(h.get("cost_unknown"))
        self.assertNotIn("total_cost", h)
        self.assertIsNone(back.rows[0].cost)

    def test_a_cash_line_is_no_holding(self):
        text = ('[meta]\nholdings_count = 2\n[[holding]]\nsymbol = "CAD"\n'
                'asset_type = "cash"\nquantity = 500\ncurrency = "CAD"\n'
                '[[holding]]\nsymbol = "SYN.TO"\nquantity = 1\n'
                'currency = "CAD"\ntotal_cost = 9\n')
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([h["symbol"] for h in doc["holding"]], ["SYN.TO"])

    def test_short_keeps_the_sign_convention(self):
        text = ('[meta]\n[[holding]]\nsymbol = "SYN.TO"\nquantity = -10\n'
                'currency = "CAD"\naverage_entry_price = 12.0\n')
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        h = _holding(doc, "SYN.TO")
        self.assertEqual(h["total_cost"], -120.0)
        self.assertEqual(h["cost_per_share"], 12.0)


class TestSnapshotDate(unittest.TestCase):
    """TJ-0874."""

    def test_as_of_kept(self):
        text = ('[meta]\naccount = "margin"\nas_of = 2025-12-31\n'
                '[[holding]]\nsymbol = "SYN.TO"\nquantity = 10\n'
                'currency = "CAD"\ntotal_cost = 120\n')
        r, doc, back = _export({"h.toml": text}, "--holdings-toml",
                               "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(str(doc["meta"]["as_of"]), "2025-12-31")
        self.assertEqual(back.as_of, "2025-12-31")

    def test_generated_at_day_kept(self):
        r, doc, back = _export({"q.toml": _FETCH}, "--holdings-toml",
                               "{q.toml}")
        self.assertEqual(back.as_of, "2025-12-31")

    def test_a_gains_file_has_no_as_of(self):
        gains = json.dumps({"inventory": [
            {"symbol": "SYN.TO", "qty": 10, "total_cost": 100,
             "currency": "CAD"}]})
        r, doc, _ = _export({"g.json": gains}, "--holdings-toml",
                            "{g.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("as_of", doc["meta"])

    def test_two_days_refused(self):
        one = ('[meta]\nas_of = 2025-12-31\n[[holding]]\nsymbol = "A.TO"\n'
               'quantity = 1\ncurrency = "CAD"\ntotal_cost = 1\n')
        two = one.replace("2025-12-31", "2026-01-30").replace("A.TO",
                                                              "B.TO")
        r, _, _ = _export({"a.toml": one, "b.toml": two},
                          "--holdings-toml", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("2026-01-30", r.stderr)

    def test_the_report_has_no_day_to_refuse(self):
        one = ('[meta]\nas_of = 2025-12-31\n[[holding]]\nsymbol = "A.TO"\n'
               'quantity = 1\ncurrency = "CAD"\ntotal_cost = 1\n')
        two = one.replace("2025-12-31", "2026-01-30").replace("A.TO",
                                                              "B.TO")
        r, _, _ = _export({"a.toml": one, "b.toml": two}, "--report",
                          "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_one_day_with_a_gains_file_says_it(self):
        one = ('[meta]\nas_of = 2025-12-31\n[[holding]]\nsymbol = "A.TO"\n'
               'quantity = 1\ncurrency = "CAD"\ntotal_cost = 1\n')
        gains = json.dumps({"inventory": [
            {"symbol": "B.TO", "qty": 1, "total_cost": 1,
             "currency": "CAD"}]})
        r, doc, _ = _export({"a.toml": one, "g.json": gains},
                            "--holdings-toml", "{a.toml}", "{g.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("as_of", doc["meta"])
        self.assertIn("2025-12-31", r.stderr)


class TestCryptoKept(unittest.TestCase):
    """TJ-0875."""

    def test_asset_type_crypto_round_trips(self):
        text = ('[meta]\nas_of = 2025-12-31\n[[holding]]\n'
                'symbol = "ZZE.KR"\nasset_type = "crypto"\nquantity = 10\n'
                'currency = "CAD"\ntotal_cost = 120\n'
                '[[holding]]\nsymbol = "SYN.TO"\nquantity = 5\n'
                'currency = "CAD"\ntotal_cost = 50\n')
        r, doc, back = _export({"h.toml": text}, "--holdings-toml",
                               "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_holding(doc, "ZZE.KR")["asset_type"], "crypto")
        self.assertEqual(_holding(doc, "SYN.TO")["asset_type"], "equity")
        rows = {x.symbol: x.asset_type for x in back.rows}
        self.assertEqual(rows, {"ZZE": "crypto", "SYN.TO": "stock"})


class TestUnknownCostNotDust(unittest.TestCase):
    """TJ-0876."""

    def test_small_unknown_cost_holding_kept(self):
        text = ('[meta]\nas_of = 2025-12-31\n[[holding]]\n'
                'symbol = "ZZE.KR"\nasset_type = "crypto"\n'
                'quantity = 0.0001\ncurrency = "CAD"\ncost_unknown = true\n')
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(doc["meta"]["holdings_count"], 1)
        h = _holding(doc, "ZZE.KR")
        self.assertTrue(h["cost_unknown"])
        self.assertEqual(h["quantity"], 0.0001)

    def test_a_rounding_residue_of_unknown_cost_is_said(self):
        text = ('[meta]\n[[holding]]\nsymbol = "ZZE.KR"\n'
                'asset_type = "crypto"\nquantity = 1e-07\n'
                'currency = "CAD"\ncost_unknown = true\n')
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(doc["meta"]["holdings_count"], 0)
        self.assertIn("# left out as dust (under 1e-06 units, cost "
                      "unknown): ZZE.KR 1e-07", r.stdout)

    def test_dust_with_a_known_small_cost_still_dropped(self):
        self.assertTrue(export._is_dust(0.0001, 0.2, 0.001))
        self.assertFalse(export._is_dust(0.0001, 0.0, 0.001, unknown=True))
        self.assertTrue(export._is_dust(1e-12, 0.0, 0.001, unknown=True))


class TestHoldingsCount(unittest.TestCase):
    """TJ-0877."""

    def test_short_snapshot_refused(self):
        text = ('[meta]\nas_of = 2025-12-31\nholdings_count = 2\n'
                '[[holding]]\nsymbol = "SYN.TO"\nquantity = 10\n'
                'currency = "CAD"\ntotal_cost = 120\n')
        r, _, _ = _export({"h.toml": text}, "--holdings-toml", "{h.toml}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("holdings_count", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_matching_count_accepted(self):
        text = ('[meta]\nholdings_count = 1\n[[holding]]\n'
                'symbol = "SYN.TO"\nquantity = 10\ncurrency = "CAD"\n'
                'total_cost = 120\n')
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)


_MIXED = ('[meta]\naccount = "margin"\n[[holding]]\nsymbol = "SYN.TO"\n'
          'quantity = {q}\nmixed_currency = true\ntotal_cost_cad = {c}\n'
          'total_cost_usd = {u}\n')


class TestMixedFilters(unittest.TestCase):
    """TJ-0881."""

    def test_long_and_short(self):
        for q, c, u, flag in ((10, 100, 50, "--long"),
                              (-10, -100, -50, "--short")):
            r, doc, _ = _export({"h.toml": _MIXED.format(q=q, c=c, u=u)},
                                "--holdings-toml", flag, "{h.toml}")
            self.assertEqual(r.returncode, 0, r.stderr)
            h = _holding(doc, "SYN.TO")
            self.assertEqual(h["quantity"], float(q))
            self.assertTrue(h["mixed_currency"], flag)
            self.assertEqual(h["total_cost_cad"], float(c))
            self.assertEqual(h["total_cost_usd"], float(u))
            self.assertNotIn("total_cost", h)

    def test_the_other_direction_still_left_out(self):
        r, doc, _ = _export({"h.toml": _MIXED.format(q=10, c=100, u=50)},
                            "--holdings-toml", "--short", "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(doc.get("holding", []), [])


class TestMixedMove(unittest.TestCase):
    """TJ-0882."""

    def test_move_keeps_mixed(self):
        agg = {"SYN.US": {"qty": 10.0, "total_cost": 150.0,
                          "currency": "CAD", "mixed_currency": True,
                          "cost_by_currency": {"CAD": 100.0, "USD": 50.0},
                          "position_start_date": None}}
        export._move_units(agg, "SYN.US", "SYN.TO", 10.0)
        self.assertNotIn("SYN.US", agg)
        d = agg["SYN.TO"]
        self.assertTrue(d.get("mixed_currency"))
        self.assertEqual(d["cost_by_currency"], {"CAD": 100.0, "USD": 50.0})

    def test_cli_journal(self):
        hold = ('[meta]\naccount = "margin"\n[[holding]]\n'
                'symbol = "SYN.US"\nquantity = 10\nmixed_currency = true\n'
                'total_cost_cad = 100\ntotal_cost_usd = 50\n')
        legs = [dict(action="TRANSFER", date="2025-06-02",
                     date_settle="2025-06-02", symbol=s, quantity=q,
                     account="margin", currency=c)
                for s, q, c in (("SYN.US", -10, "USD"),
                                ("SYN.TO", 10, "CAD"))]
        side = json.dumps({"metadata": {"kind": "transfer_sidecar"},
                           "transactions": legs})
        r, doc, _ = _export({"h.toml": hold, "t.map": "TOBASE SYN.US SYN.TO\n",
                             "m.json": side},
                            "--holdings-toml", "--map", "{t.map}",
                            "--transfer-evidence", "{m.json}", "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.TO")
        self.assertEqual(h["quantity"], 10.0)
        self.assertTrue(h["mixed_currency"])
        self.assertEqual((h["total_cost_cad"], h["total_cost_usd"]),
                         (100.0, 50.0))
        self.assertNotIn("total_cost", h)


class TestGlobalMap(unittest.TestCase):
    """TJ-0883."""

    def test_global_alias_folds_a_snapshot(self):
        hold = ('[meta]\naccount = "margin"\n'
                '[[holding]]\nsymbol = "OLD.TO"\nquantity = 5\n'
                'currency = "CAD"\ntotal_cost = 50\n'
                '[[holding]]\nsymbol = "SYN.TO"\nquantity = 5\n'
                'currency = "CAD"\ntotal_cost = 50\n')
        r, doc, _ = _export({"h.toml": hold, "t.map": "GLOBAL OLD.TO SYN.TO\n"},
                            "--holdings-toml", "--map", "{t.map}",
                            "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([h["symbol"] for h in doc["holding"]], ["SYN.TO"])
        h = doc["holding"][0]
        self.assertEqual((h["quantity"], h["total_cost"]), (10.0, 100.0))

    def test_a_gains_file_is_not_renamed_again(self):
        # The run's gains file has its GLOBAL renames already; the map
        # is not applied to it a second time.
        gains = json.dumps({"inventory": [
            {"symbol": "OLD.TO", "qty": 5, "total_cost": 50,
             "currency": "CAD"}]})
        r, doc, _ = _export({"g.json": gains,
                             "t.map": "GLOBAL OLD.TO SYN.TO\n"},
                            "--holdings-toml", "--map", "{t.map}",
                            "{g.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([h["symbol"] for h in doc["holding"]], ["OLD.TO"])


def _events(rows, country=None):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "raw.json"
        p.write_text(json.dumps({"transactions": rows}))
        return export._load_trade_events([p], country=country)


def _row(d, q, price, t="10:00:00", ds=None, action="BUYSELL", **kw):
    r = dict(action=action, date=d, date_settle=ds or d, time=t,
             symbol="SYN.TO", symbol_new="", quantity=q, price=price,
             currency="CAD")
    r.update(kw)
    return r


class TestTradeHistory(unittest.TestCase):
    """TJ-0884, TJ-0886, TJ-0887."""

    def _split_book(self, clock):
        return [_row("2025-01-02", 10, 10),
                _row("2025-06-02", 2, 0, t=clock, action="SPLIT",
                     symbol_new="SYN.TO"),
                _row("2025-06-02", 10, 5),
                _row("2025-06-03", -30, 6),
                _row("2025-06-04", 1, 6)]

    def test_noon_split_before_the_days_trades(self):
        for country in ("canada", None):
            for clock in ("12:00:00", "00:00:00"):
                ev = _events(self._split_book(clock), country)
                self.assertEqual(
                    ev["SYN.TO"],
                    [{"date": "2025-06-04", "action": "BUY", "qty": 1.0,
                      "price": 6.0}], (country, clock))

    def test_us_trade_date_order(self):
        rows = [dict(_row("2024-05-24", 10, 10, t="15:00:00",
                          ds="2024-05-29"), symbol="SYN.US"),
                dict(_row("2024-05-28", -10, 20, t="09:30:00",
                          ds="2024-05-29"), symbol="SYN.US"),
                dict(_row("2024-05-28", 10, 30, t="10:00:00",
                          ds="2024-05-29"), symbol="SYN.US")]
        for country in ("usa", "canada"):
            ev = _events(rows, country)
            self.assertEqual(ev["SYN.US"],
                             [{"date": "2024-05-28", "action": "BUY",
                               "qty": 10.0, "price": 30.0}], country)

    def test_opening_is_no_purchase(self):
        rows = [_row("2024-12-31", 10, 10, t="00:00:00", type="opening",
                     lot_date="2020-06-02"),
                _row("2025-06-02", -5, 20)]
        ev = _events(rows, "canada")
        self.assertEqual(ev["SYN.TO"],
                         [{"date": "2025-06-02", "action": "SELL",
                           "qty": 5.0, "price": 20.0}])
        # Its units are held: a sale of all of them closes the round.
        rows.append(_row("2025-06-03", -5, 20))
        rows.append(_row("2025-06-04", 2, 21))
        ev = _events(rows, "canada")
        self.assertEqual(ev["SYN.TO"],
                         [{"date": "2025-06-04", "action": "BUY",
                           "qty": 2.0, "price": 21.0}])


_BLENDED = """\
[meta]
account = "margin"
generated_at = 2025-12-31T12:00:00Z
holdings_count = 2

[[holding]]
symbol = "SYN.US"
account = "margin"
asset_type = "equity"
quantity = 10.0
currency = "USD"
cost_unknown = true
cost_source = "blended"
base_currency = "CAD"
base_total_cost = 270.0
base_cost_per_share = 27.0

[[holding]]
symbol = "ZZQ.US"
account = "margin"
asset_type = "equity"
quantity = 4.0
currency = "USD"
total_cost = 40.0
cost_per_share = 10.0
base_currency = "CAD"
base_total_cost = 54.0
base_cost_per_share = 13.5
"""


class TestBaseCostKept(unittest.TestCase):
    """TJ-0733."""

    def test_blended_and_base_figures_round_trip(self):
        r, doc, _ = _export({"h.toml": _BLENDED}, "--holdings-toml",
                            "--account-name", "margin", "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.US")
        self.assertTrue(h["cost_unknown"])
        self.assertEqual(h["cost_source"], "blended")
        self.assertEqual(h["currency"], "USD")
        self.assertEqual((h["base_currency"], h["base_total_cost"],
                          h["base_cost_per_share"]), ("CAD", 270.0, 27.0))
        self.assertNotIn("total_cost", h)
        z = _holding(doc, "ZZQ.US")
        self.assertEqual((z["total_cost"], z["base_currency"],
                          z["base_total_cost"]), (40.0, "CAD", 54.0))
        self.assertNotIn("cost_source", z)
        # A second pass writes the same holdings.
        r2, doc2, _ = _export({"h.toml": r.stdout}, "--holdings-toml",
                              "--account-name", "margin", "{h.toml}")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(doc2["holding"], doc["holding"])

    def test_the_exporters_own_blended_snapshot(self):
        native = json.dumps({"inventory": [
            {"symbol": "SYN.US", "qty": 10.0, "total_cost": 0.0,
             "currency": "USD", "unknown_cost": True,
             "position_start_date": "2025-01-06"}]})
        blend = json.dumps({"inventory": [
            {"symbol": "SYN.US", "qty": 10.0, "total_cost": 270.0,
             "currency": "CAD", "account": "receiver"}]})
        common = ("--holdings-toml", "--account-name", "receiver",
                  "--country", "canada", "--base-currency", "CAD")
        r, first, _ = _export({"n.json": native, "b.json": blend}, *common,
                              "--blended-gains", "{b.json}", "{n.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        r2, again, _ = _export({"f.toml": r.stdout}, *common, "{f.toml}")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        h = again["holding"][0]
        for key in ("cost_unknown", "cost_source", "base_currency",
                    "base_total_cost", "base_cost_per_share",
                    "position_start_date", "currency"):
            self.assertEqual(h.get(key), first["holding"][0].get(key), key)
        self.assertEqual(h["base_total_cost"], 270.0)

    def test_blended_in_the_base_currency(self):
        text = ('[meta]\n[[holding]]\nsymbol = "SYN.TO"\nquantity = 10.0\n'
                'currency = "CAD"\ntotal_cost = 270.0\n'
                'cost_per_share = 27.0\ncost_source = "blended"\n'
                'base_currency = "CAD"\nbase_total_cost = 270.0\n'
                'base_cost_per_share = 27.0\n')
        r, doc, _ = _export({"h.toml": text}, "--holdings-toml",
                            "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.TO")
        self.assertEqual((h["total_cost"], h["cost_source"],
                          h["base_total_cost"]), (270.0, "blended", 270.0))
        self.assertNotIn("cost_unknown", h)

    def test_wrong_base_currency_still_left_out(self):
        r, doc, _ = _export({"h.toml": _BLENDED}, "--holdings-toml",
                            "--base-currency", "USD", "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("base_total_cost", _holding(doc, "ZZQ.US"))


class TestAdapterDirect(unittest.TestCase):

    def test_bad_cost_field_refused(self):
        doc = {"holding": [{"symbol": "SYN.TO", "quantity": 1,
                            "average_entry_price": "x"}]}
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            export._holdings_toml_to_inventory(doc, "h.toml")
        self.assertIn("average_entry_price", err.getvalue())


if __name__ == "__main__":
    unittest.main()
