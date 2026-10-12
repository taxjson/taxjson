"""Re-audit-2 partition fixes in the planning tools (wash radar,
sell-check, buy-check, harvest).

- A2-0443: Canada radar replays same-settle rows in trade-date order.
- A2-0442: CA-SL-11 buy-back exemption in the radar's own pool.
- A2-0435 / A2-1343: US futures (and options on them) are outside §1091
  (US-WASH-18): no COOLING/BLOCKED, buy-check SAFE*; Canada unchanged.
- A2-0436 / A2-1369: US short-cover loss: only a re-short replaces it
  (§1091(e)); Canada's CA-SL-07 keeps the long buy as a replacement.
- A2-0751 / A2-0754 / A2-1370: LOCKED wording for an IRA that sold out;
  a sheltered-only row names its recent purchase.
- A2-0434 / A2-0445 / A2-1341: the warn-only flags reach sell-check (any
  verdict, through the real radar JSON) and harvest.

All data is synthetic.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tax_rules import rule, rule_absent
from test_fix_a2_planning import _check, _radar_doc, _row, _rows


def _flat(doc):
    from taxjson.bin.taxjson_watch import flatten_radar
    return flatten_radar(doc)


def _usd(*a, **kw):
    kw.setdefault("currency", "USD")
    return _row(*a, **kw)


# ------------------------------------------------------------ A2-0443
class TestSameSettleTradeDateOrder(unittest.TestCase):
    """A Friday 15:00 sale and the next trading day's 09:45 buy settle on
    the same day (a settlement holiday in between): the sale comes first,
    at the old ACB, so it is a gain — no VIOLATION."""

    @rule("CA-PLAN-01")
    def test_friday_sale_before_monday_buy(self):
        book = [_row("2025-09-02", "QQA.TO", 100, 1000.0, rid="b1"),
                dict(_row("2025-10-10", "QQA.TO", -50, 600.0,
                          settle="2025-10-14", rid="s1"), time="15:00:00"),
                dict(_row("2025-10-13", "QQA.TO", 100, 2000.0,
                          settle="2025-10-14", rid="b2"), time="09:45:00")]
        row = _rows(book, "2025-10-20")["QQA.TO"]
        self.assertNotEqual(row["category"], "VIOLATION", row)
        self.assertIsNone(row["denied_qty"])


# ------------------------------------------------------------ A2-0442
class TestBuyBackLossOutsideGainsYear(unittest.TestCase):
    """A written call bought back at a loss outside the gains files'
    year: exempt by default (CA-SL-11), superficial-eligible with the
    project's opt-in (CA-SL-12)."""

    BOOK = [_row("2025-12-01", "XYZ260116C00050000.TO", -1, 100.0,
                 rid="w1"),
            _row("2025-12-22", "XYZ260116C00050000.TO", 1, 200.0,
                 rid="bb1")]

    @rule("CA-SL-11")
    def test_exempt_by_default(self):
        row = _rows(self.BOOK, "2026-01-01")["XYZ260116C00050000.TO"]
        self.assertNotIn(row["category"], ("COOLING", "BLOCKED"), row)

    @rule("CA-SL-12")
    def test_opt_in_keeps_the_window(self):
        row = _rows(self.BOOK, "2026-01-01",
                    extra=["--option-buyback-wash"])[
            "XYZ260116C00050000.TO"]
        self.assertEqual(row["category"], "COOLING", row)

    @rule("CA-SL-12")
    def test_run_passes_the_setting(self):
        import taxjson.bin.taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'option_buyback_loss_superficial = true\n')
            args = R._radar_engine_args([], root,
                                        "canada")
        self.assertIn("--option-buyback-wash", args)

    @rule("CA-SL-12")
    @rule_absent("CA-SL-12", country="usa")
    def test_flag_is_canada_only(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "m_base.json"
            t.write_text(json.dumps({"transactions": []}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
                 "--country", "usa", "--taxable", str(t),
                 "--option-buyback-wash"],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Canada-only", r.stderr)


# ------------------------------------------------- A2-0435 / A2-1343
def _fut_book(sfx_ccy="USD"):
    return [_row("2026-06-01", "F:CLG7.US", 1, 0.0, rid="f1",
                 currency=sfx_ccy, multiplier=1000.0),
            _row("2026-09-21", "F:CLG7.US", -1, -10000.0, rid="f2",
                 currency=sfx_ccy, multiplier=1000.0,
                 type="futures_settlement")]


class TestUsFuturesOutsideWashRule(unittest.TestCase):

    @rule("US-WASH-18", "US-PLAN-02")
    @rule_absent("US-WASH-18", country="canada")
    @rule("CA-PLAN-01")
    def test_radar_and_buy_check(self):
        doc = _radar_doc(_fut_book(), "2026-10-01", country="usa")
        row = _flat(doc)["F:CLG7.US"]
        self.assertEqual(row["category"], "CLEAR", row)
        self.assertTrue(row["outside_wash_rule"])
        self.assertIn("US-WASH-18", row["advisory"])
        out, code = _check("cmd_buy_check", _flat(doc), ["F:CLG7.US"],
                           usa=True)
        self.assertEqual(code, 0, out)
        self.assertNotIn("UNSAFE", out)
        self.assertIn("§1256", out)
        # Canada: s.54 covers any property — the same book keeps its
        # re-entry window and buy-check stays UNSAFE.
        doc = _radar_doc(_fut_book(), "2026-10-01")
        row = _flat(doc)["F:CLG7.US"]
        self.assertEqual(row["category"], "COOLING", row)
        out, code = _check("cmd_buy_check", _flat(doc), ["F:CLG7.US"])
        self.assertEqual(code, 1, out)

    @rule("US-WASH-15", "US-WASH-18")
    def test_futures_option_note_is_not_about_shares(self):
        book = _fut_book() + [
            _usd("2026-09-25", "F:CLG7261216C00070000.US", 1, 2000.0,
                 rid="o1", multiplier=1000.0)]
        row = _rows(book, "2026-10-01", country="usa")["F:CLG7.US"]
        self.assertNotIn("on these shares", row["advisory"])
        self.assertNotIn("may make that loss a wash sale",
                         row["advisory"])
        self.assertIn("futures_option_vs_loss", row["advisory"])


# ------------------------------------------------- A2-0436 / A2-1369
def _short_book(sfx, ccy, rebuy=False):
    b = [_row("2026-06-01", f"XYZ.{sfx}", -100, 4000.0, rid="s1",
              currency=ccy),
         _row("2026-09-21", f"XYZ.{sfx}", 100, 5000.0, rid="c1",
              currency=ccy)]
    if rebuy:
        b.append(_row("2026-09-28", f"XYZ.{sfx}", 100, 5000.0, rid="b2",
                      currency=ccy))
    return b


class TestShortCoverLossReplacement(unittest.TestCase):

    @rule("US-WASH-05", "US-PLAN-02")
    @rule_absent("US-WASH-05", country="canada")
    @rule("CA-SL-07")
    def test_long_rebuy_after_a_short_cover_loss(self):
        doc = _radar_doc(_short_book("US", "USD", rebuy=True),
                         "2026-10-01", country="usa")
        row = _flat(doc)["XYZ.US"]
        self.assertNotEqual(row["category"], "BLOCKED", row)
        self.assertNotIn("disallows the loss on as many units as you buy",
                         row["advisory"])
        self.assertIn("SHORT sale", row["advisory"])
        out, code = _check("cmd_buy_check", _flat(doc), ["XYZ"], usa=True)
        self.assertEqual(code, 0, out)
        self.assertIn("§1091(e)", out)
        # Canada: a purchase replaces a short-cover loss (CA-SL-07).
        doc = _radar_doc(_short_book("TO", "CAD", rebuy=True),
                         "2026-10-01")
        self.assertEqual(_flat(doc)["XYZ.TO"]["category"], "VIOLATION")

    @rule("US-WASH-05", "US-PLAN-02")
    def test_no_rebuy_cooling_names_the_re_short(self):
        doc = _radar_doc(_short_book("US", "USD"), "2026-10-01",
                         country="usa")
        row = _flat(doc)["XYZ.US"]
        self.assertEqual(row["category"], "COOLING")
        self.assertIn("short it again", row["advisory"])
        out, code = _check("cmd_buy_check", _flat(doc), ["XYZ"], usa=True)
        self.assertEqual(code, 0, out)
        self.assertIn("SAFE*", out)


# ------------------------------------- A2-0751 / A2-0754 / A2-1370
class TestShelteredWording(unittest.TestCase):

    @rule("US-WASH-11", "US-PLAN-01")
    def test_us_locked_ira_sold_out(self):
        tax = [_usd("2026-01-05", "XYZ.US", 100, 2000.0, rid="b1")]
        shl = [_usd("2026-09-23", "XYZ.US", 40, 800.0, account="rr",
                    rid="r1"),
               _usd("2026-09-28", "XYZ.US", -40, 700.0, account="rr",
                    rid="r2")]
        row = _rows(tax, "2026-10-01", sheltered=shl, country="usa")[
            "XYZ.US"]
        self.assertEqual(row["category"], "LOCKED")
        self.assertNotIn("still holds", row["advisory"])
        self.assertIn("bought 40", row["advisory"])
        self.assertIn("holds 0 now", row["advisory"])

    @rule("US-PLAN-02")
    def test_us_sheltered_call_row(self):
        tax = [_usd("2026-01-05", "XYZ.US", 100, 2000.0, rid="b1")]
        shl = [_usd("2026-09-23", "XYZ261218C00015000.US", 1, 300.0,
                    account="rr", rid="c1")]
        row = _rows(tax, "2026-10-01", sheltered=shl, country="usa")[
            "XYZ261218C00015000.US"]
        self.assertNotIn("No recent buys", row["advisory"])
        self.assertIn("2026-09-23", row["advisory"])

    @rule("CA-PLAN-02")
    def test_ca_sheltered_call_row(self):
        tax = [_row("2026-01-05", "XYZ.TO", 100, 2000.0, rid="b1")]
        shl = [_row("2026-09-23", "XYZ261218C00015000.TO", 1, 300.0,
                    account="rr", rid="c1")]
        rows = _rows(tax, "2026-10-01", sheltered=shl)
        row = rows["XYZ261218C00015000.TO"]
        self.assertNotIn("No recent buys", row["advisory"])
        self.assertIn("'rr' bought it 2026-09-23", row["advisory"])
        self.assertEqual(rows["XYZ.TO"]["category"], "LOCKED")


# ------------------------------------- A2-0434 / A2-0445 / A2-1341
def _flag_book(sfx, ccy, loss=True):
    b = [_row("2026-06-01", f"XYZ.{sfx}", 200, 10000.0, rid="b1",
              currency=ccy)]
    if loss:
        b.append(_row("2026-09-21", f"XYZ.{sfx}", -100, 4000.0, rid="s1",
                      currency=ccy))
    b.append(_row("2026-09-26", f"XYZ.WS.{sfx}", 100, 200.0, rid="w1",
                  currency=ccy))
    return b


class TestFlagsReachTheTools(unittest.TestCase):

    @rule("CA-SL-14", "CA-PLAN-02")
    def test_ca_sell_check_blocked_row_carries_the_flag(self):
        doc = _radar_doc(_flag_book("TO", "CAD"), "2026-10-01")
        radar = _flat(doc)
        self.assertEqual(radar["XYZ.TO"]["category"], "BLOCKED")
        out, code = _check("cmd_sell_check", radar, ["XYZ"])
        self.assertIn("SAFE*", out)
        self.assertIn("right_vs_share_loss", out)
        # one line per purchase, not one per view
        self.assertEqual(out.count("XYZ.WS.TO (a warrant"), 1, out)

    @rule("US-WASH-14", "US-PLAN-02")
    def test_us_sell_check_clear_row_carries_the_flag(self):
        doc = _radar_doc(_flag_book("US", "USD", loss=False), "2026-10-01",
                         country="usa")
        out, code = _check("cmd_sell_check", _flat(doc), ["XYZ"],
                           usa=True)
        self.assertEqual(code, 0, out)
        self.assertIn("SAFE*", out)
        self.assertIn("right_vs_share_loss", out)

    @rule("US-WASH-12", "US-PLAN-02")
    def test_us_forward_call_note_through_the_radar_json(self):
        tax = [_usd("2026-06-01", "XYZ.US", 200, 10000.0, rid="b1"),
               _usd("2026-09-26", "XYZ261218C00040000.US", 1, 300.0,
                    rid="c1")]
        doc = _radar_doc(tax, "2026-10-01", country="usa")
        out, code = _check("cmd_sell_check", _flat(doc), ["XYZ"],
                           usa=True)
        self.assertIn("SAFE*", out)
        self.assertIn("long call", out)

    @rule("CA-SL-14", "CA-PLAN-02")
    def test_harvest_stars_and_lists_the_flag(self):
        from taxjson.bin import taxjson_harvest as H
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "wash_radar_margin.json"
            p.write_text(json.dumps(_radar_doc(
                _flag_book("TO", "CAD", loss=False), "2026-10-01")))
            radar = H.load_radar([p])
        rec = radar["XYZ.TO"]
        self.assertTrue(rec["notes"])
        self.assertTrue(H._advisory_display(rec).endswith("*"))


if __name__ == "__main__":
    unittest.main()
