"""buy-check / sell-check / verify — the advice they give.

These commands answer a yes/no question a user acts on with real
money, so the failure mode that matters is not a crash but CONFIDENT
WRONG ADVICE. Each test below pins a case where an earlier version
said something plausible and wrong:

  * printing a VIOLATION's sell-by deadline as a "safe to buy from"
    date (it is the LAST day to rescue the loss, roughly 30 days
    EARLIER than re-entry is actually safe),
  * telling a user to sell a sheltered-only holding "at a loss",
  * calling a violation rescueable when a registered account holds
    the same name (the matched portion is permanently denied),
  * taking the first date alphabetically across a class of
    cross-listings instead of the worst one.
"""
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import taxjson.bin.taxjson_run as R


def _run(cmd, radar, last_loss=None, symbols=("XYZ",)):
    """Drive one check command against a synthetic radar."""
    ctx = (radar, lambda t: t.strip().upper().rsplit(".", 1)[0],
           last_loss or {})
    buf = StringIO()
    code = 0
    with patch.object(R, "_wash_class_context", return_value=ctx):
        try:
            with redirect_stdout(buf):
                cmd(Namespace(dir=".", symbol=list(symbols), json=False))
        except SystemExit as e:
            code = e.code or 0
    return buf.getvalue(), code


class TestBuyCheckDates(unittest.TestCase):
    def test_violation_does_not_advertise_a_safe_to_buy_date(self):
        # clears_at on a VIOLATION is the SELL-BY deadline. Quoting it
        # as a buy date would invite the rebuy that kills the loss.
        out, code = _run(R.cmd_buy_check,
                         {"XYZ.TO": {"category": "VIOLATION",
                                     "clears_at": "2026-09-01"}})
        self.assertEqual(code, 1)
        self.assertNotIn("Safe to buy from 2026-09-01", out)
        self.assertIn("UNSAFE", out)
        self.assertIn("31 days after", out)

    def test_class_takes_the_latest_clearing_date(self):
        out, _ = _run(R.cmd_buy_check,
                      {"XYZ.TO": {"category": "BLOCKED",
                                  "clears_at": "2026-09-01"},
                       "XYZ.US": {"category": "BLOCKED",
                                  "clears_at": "2026-09-20"}})
        # Both are named; the later date must not be hidden by the
        # earlier one (sorted() puts .TO first).
        self.assertIn("2026-09-20", out)

    def test_blank_category_is_not_reported_as_clear(self):
        out, _ = _run(R.cmd_buy_check, {"XYZ.TO": {"category": ""}})
        self.assertNotIn("(CLEAR)", out)
        self.assertIn("SAFE", out)


class TestSellCheckScope(unittest.TestCase):
    def test_sheltered_only_holding_is_not_told_to_sell_at_a_loss(self):
        out, code = _run(R.cmd_sell_check,
                         {"XYZ.TO": {"category": "CLEAR",
                                     "taxable_qty": 0.0,
                                     "sheltered_qty": 300.0}})
        self.assertEqual(code, 0)
        self.assertIn("sheltered", out.lower())
        self.assertNotIn("safe to sell at a loss", out.lower())

    def test_taxable_holding_still_gets_the_clear_advice(self):
        out, _ = _run(R.cmd_sell_check,
                      {"XYZ.TO": {"category": "CLEAR",
                                  "taxable_qty": 100.0,
                                  "sheltered_qty": 0.0}})
        self.assertIn("safe to sell at a loss", out.lower())
        self.assertIn("30 days", out)

    def test_violation_with_sheltered_holding_is_unsafe_not_action(self):
        out, code = _run(R.cmd_sell_check,
                         {"XYZ.TO": {"category": "VIOLATION",
                                     "advisory": "sell all 100 by 2026-09-01.",
                                     "taxable_qty": 100.0,
                                     "sheltered_qty": 50.0}})
        self.assertEqual(code, 1, "must exit non-zero")
        self.assertIn("UNSAFE", out)
        self.assertIn("permanently denied", out)

    def test_violation_without_sheltered_holding_stays_rescueable(self):
        out, code = _run(R.cmd_sell_check,
                         {"XYZ.TO": {"category": "VIOLATION",
                                     "advisory": "sell all 100 by 2026-09-01.",
                                     "taxable_qty": 100.0,
                                     "sheltered_qty": 0.0}})
        self.assertEqual(code, 0)
        self.assertIn("ACTION", out)


class TestRadarCarriesScope(unittest.TestCase):
    def test_flatten_radar_keeps_the_quantity_split(self):
        # The checks cannot reason about scope if flatten drops it.
        from taxjson.bin.taxjson_watch import flatten_radar
        doc = {"sections": [{"rows": [{"ticker": "XYZ.TO",
                                       "category": "CLEAR",
                                       "taxable_qty": 0.0,
                                       "sheltered_qty": 300.0}]}]}
        flat = flatten_radar(doc)
        self.assertIn("XYZ.TO", flat)
        self.assertEqual(flat["XYZ.TO"]["sheltered_qty"], 300.0)
        self.assertEqual(flat["XYZ.TO"]["taxable_qty"], 0.0)


class TestQuestradePositionSymbols(unittest.TestCase):
    def test_class_share_is_not_mistaken_for_an_exchange(self):
        from taxjson.bin.taxjson_fetch import qt_position_symbol as q
        self.assertEqual(q("BRK.B"), "BRK.B.US")
        self.assertEqual(q("RDS.A"), "RDS.A.US")

    def test_real_exchange_suffixes_pass_through(self):
        from taxjson.bin.taxjson_fetch import qt_position_symbol as q
        self.assertEqual(q("SHOP.TO"), "SHOP.TO")
        self.assertEqual(q("ABC.VN"), "ABC.V")
        self.assertEqual(q("AAPL"), "AAPL.US")

    def test_montreal_options_follow_the_underlying(self):
        from taxjson.bin.taxjson_fetch import qt_position_symbol as q
        self.assertEqual(q("BMO20Jan26C88.00", to_roots={"BMO"}),
                         "BMO260120C00088000.TO")
        self.assertEqual(q("BMO20Jan26C88.00"),
                         "BMO260120C00088000.US")


class TestRootMatcherClasses(unittest.TestCase):
    """2026-09 audit: `_root()` stripped the exchange suffix before any
    union ran, so `DISTINCT UNH.US UNH.TO` (a CDR vs its underlying —
    separate pools in the engine) still made buy-check UNH.TO UNSAFE
    after a UNH.US loss; and SPLIT-renames (identical property to the
    engine and radar) were never unioned, so `sell-check OLD.TO` missed
    the violation filed under NEW.TO."""

    def _project(self, tmp):
        import json
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "ticker.map").write_text("DISTINCT UNH.US UNH.TO\n")

        # Dates are written as of 2026-09-05 and shifted to today, so the
        # OLD.TO loss (settled 2026-08-25) is always 11 days old and its
        # 30-day window still open. Hard-coded, the verdict flipped from
        # VIOLATION to EXITABLE the day the window closed (2026-09-25).
        from datetime import date as _d, timedelta as _td
        _shift = _d.today() - _d(2026, 9, 5)

        def _mv(s):
            return (_d.fromisoformat(s) + _shift).isoformat()

        def tx(action, date, settle, sym, qty, price, **kw):
            d = dict(action=action, date=_mv(date), time="09:30:00",
                     date_settle=_mv(settle), symbol=sym, quantity=qty,
                     currency="CAD", price=price, net_amount=abs(qty) * price,
                     fee=0.0, account="margin")
            d.update(kw)
            return d
        rows = [
            tx("BUYSELL", "2026-06-01", "2026-06-02", "UNH.US", 100, 500.0),
            tx("BUYSELL", "2026-08-25", "2026-08-26", "UNH.US", -100, 400.0),
            tx("BUYSELL", "2026-01-05", "2026-01-06", "UNH.TO", 300, 30.0),
            tx("BUYSELL", "2026-06-01", "2026-06-02", "OLD.TO", 100, 50.0),
            tx("BUYSELL", "2026-08-24", "2026-08-25", "OLD.TO", -100, 40.0),
            {"action": "SPLIT", "date": _mv("2026-08-27"), "time": "00:00:00",
             "date_settle": _mv("2026-08-27"), "symbol": "OLD.TO",
             "symbol_new": "NEW.TO", "quantity": 1.0, "currency": "CAD",
             "account": "margin"},
            tx("BUYSELL", "2026-09-01", "2026-09-02", "NEW.TO", 100, 41.0),
        ]
        (root / "work" / "margin_base.json").write_text(
            json.dumps({"transactions": rows}))
        return root

    def test_distinct_pair_is_never_merged_and_renames_are(self):
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            with redirect_stderr(StringIO()):
                radar, canon, _ = R._wash_class_context(
                    root, root / "work", "t")
        self.assertNotEqual(canon("UNH.TO"), canon("UNH.US"))
        self.assertEqual(canon("OLD.TO"), canon("NEW.TO"))
        self.assertEqual(canon("XYZ.TO"), canon("XYZ.US"))   # untouched
        # And the verdicts that follow from it.
        _, m_to, _ = R._class_matches(radar, canon, "UNH.TO")
        self.assertEqual(set(m_to), {"UNH.TO"})
        _, m_old, _ = R._class_matches(radar, canon, "OLD.TO")
        self.assertIn("NEW.TO", m_old)
        self.assertEqual(m_old["NEW.TO"]["category"], "VIOLATION")

    def test_bare_query_over_a_distinct_pair_is_flagged_not_merged(self):
        # Members keep their full symbol as root; a bare "UNH" matches
        # neither by root, so it falls back to BOTH with a note (worst
        # verdict) rather than silently answering for one of them.
        radar = {"UNH.US": {"category": "COOLING", "clears_at": "2026-09-26"},
                 "UNH.TO": {"category": "CLEAR"}}
        prot = {"UNH.US", "UNH.TO"}
        canon = lambda t: (t.strip().upper() if t.strip().upper() in prot  # noqa: E731
                           else t.strip().upper().rsplit(".", 1)[0])
        _, m, note = R._class_matches(radar, canon, "UNH")
        self.assertEqual(set(m), prot)
        self.assertIn("DISTINCT", note)
        _, m, note = R._class_matches(radar, canon, "UNH.TO")
        self.assertEqual(set(m), {"UNH.TO"})
        self.assertIsNone(note)


class TestToleranceZero(unittest.TestCase):
    def test_zero_tolerance_is_honoured_not_defaulted(self):
        # `--tolerance 0` means EXACT; `or` coercion silently
        # restored the 1e-4 default.
        import inspect
        src = inspect.getsource(R)
        self.assertNotIn('float(getattr(args, "tolerance", None) or 1e-4)',
                         src,
                         "falsy coercion turns --tolerance 0 into the "
                         "default")


if __name__ == "__main__":
    unittest.main()
