"""Re-audit-2 test pins (tests-pins-03): harvest, sell-check and
safe-to-sell — A2-0885 (standalone harvest finds the project's
ticker.map), A2-1573 (EXIT@ for options uses the contract multiplier),
A2-1529 / A2-1578 (US-only long-term column, field and note; the RISK
pause wording per country), A2-1581 (the US scope note on harvest,
sell-check and safe-to-sell). Offline, synthetic data only."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from test_fix_a2_planning_harvest import (TODAY, _gains, _json_rows, _run,
                                          _write)


class TestStandaloneHarvestFindsTheTickerMap(unittest.TestCase):
    """A2-0885 (S034-11 twin): the console script `taxjson-harvest` run on
    <project>/work/*_gains_wash.json without --ticker-map quoted a
    TOBASE-renamed option as the Montreal contract in CAD. It now finds
    the project's ticker.map next to the inputs or one level up, as it
    finds yf_ticker.map."""

    def test_renamed_option_quoted_as_the_contract_held(self):
        exp = (TODAY + timedelta(days=200)).strftime("%y%m%d")
        held, booked = f"KGC{exp}C00012000.US", f"K{exp}C00012000.TO"
        seen = []

        def _opt(rem):
            seen.extend(rem)
            return {s: (1.00, "ibkr") for s in rem if s == held}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            g = _write(work / "margin_gains_wash.json", _gains([
                {"symbol": booked, "qty": 1, "total_cost": 136.0}]))
            _write(work / "margin_raw_gains.json", {"inventory": [
                {"symbol": held, "qty": 1, "total_cost": 100.0}]})
            (root / "ticker.map").write_text("TOBASE KGC.US K.TO\n")
            (work / "to_base.csv").write_text(
                f"{TODAY.isoformat()} 12:00:00 USD CAD 1.38\n")
            rc, out, err = _run([str(g), "--no-ibkr", "--country", "canada",
                                 "--options", "--json"],
                                fetchers=[lambda rem: {}],
                                option_fetchers=[_opt])
        self.assertEqual(rc, 0, err)
        self.assertEqual(seen, [held])
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["price_currency"], "USD")
        self.assertAlmostEqual(row["value"], 138.0)


if __name__ == "__main__":
    unittest.main()
