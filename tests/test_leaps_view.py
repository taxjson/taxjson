"""`taxjson leaps` / `leaps-sum`: closed LEAPS positions and their realized
gains. LEAPS = a LONG option BUY placed more than 3 calendar months before
expiry; contracts qualify over FULL history, so in-window exits of older
entries are included. Gains come from the wash-adjusted gains files —
lot-matched, superficial-loss-adjusted, base currency."""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from taxjson.lib.core import parse_option_expiry
from taxjson.bin.taxjson_run import _add_months, _leaps_contracts


def tx(action="BUYSELL", date="2025-01-10", symbol="AAPL.US", qty=0.0,
       net=0.0, currency="USD"):
    return {"action": action, "date": date, "date_settle": date,
            "time": "09:30:00", "symbol": symbol, "quantity": qty,
            "net_amount": net, "currency": currency}


LEAP = "AAPL270115C00150000.US"        # bought >3mo out → qualifies
NEAR = "AAPL250221C00150000.US"        # bought <3mo out → excluded
SHORT = "AAPL270115P00120000.US"       # only ever sold short → excluded

HISTORY = [
    # Qualifying entry OUTSIDE the 2025 tax year (2024) — its 2025 exit
    # must still appear in the window views.
    tx(date="2024-12-15", symbol=LEAP, qty=2, net=2400.0),
    tx(date="2025-06-02", symbol=LEAP, qty=-1, net=1900.0),
    # Near-dated long buy: 2025-01-10 → 2025-02-21 expiry is under 3 months.
    tx(date="2025-01-10", symbol=NEAR, qty=1, net=300.0),
    # Short premium: never a long buy, must not qualify.
    tx(date="2025-01-10", symbol=SHORT, qty=-1, net=500.0),
    # Plain shares: never in a leaps view.
    tx(date="2025-03-01", symbol="AAPL.US", qty=100, net=20000.0),
]

# Engine-shaped disposition entries (base currency, lot-matched).
LEAP_GAIN = {"symbol": LEAP, "date": "2025-06-02",
             "date_settle": "2025-06-03", "qty": -1,
             "proceeds": 2565.0, "cost": 1620.0, "gain": 945.0,
             "raw_gain": 945.0, "disallowed_amount": 0.0,
             "days_held": 169, "direction": "LONG", "account": "margin"}
NEAR_GAIN = {"symbol": NEAR, "date": "2025-02-10",
             "date_settle": "2025-02-11", "qty": -1,
             "proceeds": 500.0, "cost": 405.0, "gain": 95.0,
             "raw_gain": 95.0, "disallowed_amount": 0.0,
             "days_held": 31, "direction": "LONG", "account": "margin"}


def _project(tmp, gains=None, wash_gains=None):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\n\n[accounts.margin]\ntype = "taxable"\n')
    work = root / "work"
    work.mkdir()
    (work / "margin_raw.json").write_text(
        json.dumps({"transactions": HISTORY}))
    if gains is not None:
        (work / "margin_gains.json").write_text(
            json.dumps({"transactions": gains}))
    if wash_gains is not None:
        (work / "margin_gains_wash.json").write_text(
            json.dumps({"transactions": wash_gains}))
    return root


class TestExpiryParser(unittest.TestCase):
    def test_parses_occ_expiry(self):
        self.assertEqual(parse_option_expiry(LEAP), "2027-01-15")
        self.assertEqual(parse_option_expiry("F:CL251220P00053000.US"),
                         "2025-12-20")

    def test_non_options_and_garbage(self):
        self.assertIsNone(parse_option_expiry("AAPL.US"))
        self.assertIsNone(parse_option_expiry(""))
        self.assertIsNone(parse_option_expiry(None))
        # Impossible calendar date inside an otherwise OCC-shaped block.
        self.assertIsNone(parse_option_expiry("AAPL259941C00150000.US"))


class TestAddMonths(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_add_months("2025-01-10", 3), "2025-04-10")

    def test_year_rollover_and_clamp(self):
        self.assertEqual(_add_months("2025-11-30", 3), "2026-02-28")
        self.assertEqual(_add_months("2025-01-31", 3), "2025-04-30")


class TestContractIdentification(unittest.TestCase):
    def test_only_qualifying_long_buys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            leaps = _leaps_contracts(root, None, "leaps")
        self.assertEqual(set(leaps), {LEAP})


class TestViews(unittest.TestCase):
    def _run(self, cmd_fn, **kw):
        args = argparse.Namespace(dir=kw.pop("dir"), period=kw.pop("period"),
                                  account=kw.pop("account", None))
        out = io.StringIO()
        with redirect_stdout(out):
            cmd_fn(args)
        return out.getvalue()

    def test_leaps_lists_closed_positions_only(self):
        from taxjson.bin.taxjson_run import cmd_leaps
        with tempfile.TemporaryDirectory() as tmp:
            # NEAR's disposition is in the gains file but NEAR is not a
            # LEAPS contract — it must not appear.
            root = _project(tmp, gains=[LEAP_GAIN, NEAR_GAIN])
            text = self._run(cmd_leaps, dir=str(root), period="all")
        self.assertIn("CLOSED LEAPS POSITIONS", text)
        self.assertIn(LEAP, text)
        self.assertIn("945.00", text)
        self.assertIn("169", text)                     # days held
        self.assertNotIn(NEAR, text)
        self.assertIn("TOTAL REALIZED GAIN: 945.00 CAD", text)

    def test_leaps_sum_summarizes_gain_per_contract(self):
        from taxjson.bin.taxjson_run import cmd_leaps_sum
        with tempfile.TemporaryDirectory() as tmp:
            # Two partial closes of the same contract aggregate.
            second = dict(LEAP_GAIN, date="2025-09-15", proceeds=2700.0,
                          cost=1620.0, gain=1080.0)
            root = _project(tmp, wash_gains=[LEAP_GAIN, second],
                            gains=[dict(LEAP_GAIN, gain=111.0)])  # decoy
            text = self._run(cmd_leaps_sum, dir=str(root), period=None)
        self.assertIn("LEAPS REALIZED GAINS", text)
        self.assertIn("2027-01-15", text)              # expiry column
        self.assertIn("2,025.00", text)                # 945 + 1080
        self.assertNotIn("111.00", text)               # wash file wins
        self.assertIn("TOTAL REALIZED GAIN: 2,025.00 CAD", text)

    def test_window_excludes_out_of_period_dispositions(self):
        from taxjson.bin.taxjson_run import cmd_leaps_sum
        with tempfile.TemporaryDirectory() as tmp:
            stale = dict(LEAP_GAIN, date="2024-12-20")
            root = _project(tmp, gains=[stale])
            text = self._run(cmd_leaps_sum, dir=str(root), period=None)
        # Tax-year 2025 window: the 2024 close contributes nothing.
        self.assertIn("No closed LEAPS positions", text)

    def test_missing_gains_files_exit_with_hint(self):
        from taxjson.bin.taxjson_run import cmd_leaps
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)                        # no gains files
            with self.assertRaises(SystemExit) as cm:
                self._run(cmd_leaps, dir=str(root), period="all")
        self.assertIn("taxjson run", str(cm.exception))

    def test_no_leaps_message(self):
        from taxjson.bin.taxjson_run import cmd_leaps
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n[accounts.margin]\ntype = "taxable"\n')
            (root / "work").mkdir()
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [tx(qty=100, net=20000.0)]}))
            text = self._run(cmd_leaps, dir=str(root), period="all")
        self.assertIn("No LEAPS contracts", text)


if __name__ == "__main__":
    unittest.main()
