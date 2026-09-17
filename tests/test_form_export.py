"""Tests for taxjson-form-export (IRS Form 8949 / CRA Schedule 3 renderer)."""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from taxjson.bin.taxjson_form_export import (
    build_8949,
    build_schedule3,
    load_dispositions,
    main,
)


def us_entry(symbol="AAPL.US", date="2025-05-02", qty=-100, proceeds=12000.0,
             cost=10000.0, gain=None, disallowed=0.0, days_held=200,
             term="SHORT_TERM", direction="LONG", **extra):
    e = {
        "date": date, "date_settle": date, "symbol": symbol, "qty": qty,
        "proceeds": proceeds, "cost": cost,
        "gain": (proceeds - cost + disallowed) if gain is None else gain,
        "raw_gain": proceeds - cost,
        "disallowed_amount": disallowed, "days_held": days_held,
        "term": term, "direction": direction, "commission": 0.0, "fee": 0.0,
        "account": "margin", "is_option": False,
    }
    e.update(extra)
    return e


def ca_entry(symbol="SHOP.TO", date="2025-06-10", qty=-50, proceeds=9000.0,
             cost=7000.0, gain=None, disallowed=0.0, days_held=400,
             direction="LONG", commission=9.99, fee=0.0, **extra):
    e = {
        "date": date, "date_settle": date, "symbol": symbol, "qty": qty,
        "proceeds": proceeds, "cost": cost,
        "gain": (proceeds - cost + disallowed) if gain is None else gain,
        "raw_gain": proceeds - cost,
        "disallowed_amount": disallowed, "days_held": days_held,
        "term": None, "direction": direction,
        "commission": commission, "fee": fee,
        "account": "margin", "is_option": False,
    }
    e.update(extra)
    return e


class TestLoadDispositions(unittest.TestCase):
    def _file(self, td, entries):
        p = Path(td) / "gains.json"
        p.write_text(json.dumps({"transactions": entries}))
        return p

    def test_income_and_tainted_filtered(self):
        entries = [
            us_entry(),
            {"action": "DIVIDEND", "symbol": "AAPL.US", "date": "2025-03-01",
             "dividend": 12.0, "gain": 0.0, "qty": 0},
            us_entry(symbol="STX.US", tainted=True),
        ]
        with tempfile.TemporaryDirectory() as td:
            got, tainted = load_dispositions(
                [self._file(td, entries)], 2025, "date")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["symbol"], "AAPL.US")
        self.assertEqual(tainted, 1)

    def test_year_filter(self):
        entries = [us_entry(date="2024-12-30")]
        with tempfile.TemporaryDirectory() as td:
            got, _ = load_dispositions([self._file(td, entries)], 2025, "date")
        self.assertEqual(got, [])


class Test8949(unittest.TestCase):
    def test_parts_and_wash_code(self):
        rep = build_8949([
            us_entry(term="SHORT_TERM", disallowed=250.0,
                     proceeds=9000.0, cost=9600.0),
            us_entry(symbol="MSFT.US", term="LONG_TERM", days_held=400,
                     proceeds=20000.0, cost=15000.0),
        ])
        self.assertEqual(len(rep["part_I"]), 1)
        self.assertEqual(len(rep["part_II"]), 1)
        wash = rep["part_I"][0]
        self.assertEqual(wash["code"], "W")
        self.assertAlmostEqual(wash["adjustment"], 250.0)
        # (h) = (d) - (e) + (g)
        self.assertAlmostEqual(
            wash["gain"], wash["proceeds"] - wash["cost"] + wash["adjustment"],
            places=2)
        clean = rep["part_II"][0]
        self.assertEqual(clean["code"], "")
        self.assertAlmostEqual(rep["part_II_totals"]["gain"], 5000.0)

    def test_acquired_date_derived_from_days_held(self):
        rep = build_8949([us_entry(date="2025-05-02", days_held=200)])
        row = rep["part_I"][0]
        self.assertEqual(row["date_sold"], "2025-05-02")
        self.assertEqual(row["date_acquired"], "2024-10-14")  # 200 days back

    def test_short_sale_uses_cover_date(self):
        rep = build_8949([us_entry(direction="SHORT", date="2025-05-02")])
        row = rep["part_I"][0]
        self.assertEqual(row["date_acquired"], "2025-05-02")
        self.assertEqual(row["date_sold"], "2025-05-02")
        self.assertIn("(short sale)", row["description"])

    def test_canada_entries_rejected(self):
        with self.assertRaises(SystemExit):
            build_8949([ca_entry()])


class TestSchedule3(unittest.TestCase):
    def test_proceeds_resplit_and_totals(self):
        rep = build_schedule3([ca_entry(proceeds=9000.0, cost=7000.0,
                                        commission=9.99)])
        row = rep["rows"][0]
        # net 9000 + outlays 9.99 back into proceeds; gain unchanged.
        self.assertAlmostEqual(row["proceeds"], 9009.99, places=2)
        self.assertAlmostEqual(row["outlays"], 9.99, places=2)
        self.assertAlmostEqual(row["gain"], 2000.0, places=2)
        self.assertAlmostEqual(rep["totals"]["proceeds_13199"], 9009.99,
                               places=2)
        self.assertAlmostEqual(rep["totals"]["gain_13200"], 2000.0, places=2)

    def test_aggregates_per_symbol_with_min_acq_year(self):
        rep = build_schedule3([
            ca_entry(date="2025-03-01", days_held=800, commission=0.0),
            ca_entry(date="2025-09-01", days_held=30, commission=0.0),
        ])
        self.assertEqual(len(rep["rows"]), 1)
        row = rep["rows"][0]
        self.assertEqual(row["units"], 100.0)
        self.assertEqual(row["acq_year"], "2022")   # 800 days before Mar 2025

    def test_superficial_loss_note(self):
        rep = build_schedule3([ca_entry(proceeds=5000.0, cost=6000.0,
                                        disallowed=400.0, commission=0.0)])
        row = rep["rows"][0]
        self.assertIn("superficial loss", row["notes"])
        self.assertAlmostEqual(row["gain"], -600.0, places=2)  # allowed part

    def test_short_rows_absolute_with_marker(self):
        # Signed engine convention: proceeds=-4000 is a $4,000 cover
        # cost; cost=-4500 is $4,500 of opening short-sale proceeds.
        # The FILED row maps disposition proceeds = 4500, ACB = 4000
        # (the earlier pin of 4000/4500 preserved the engine's field
        # swap — internally contradictory and wrong on line 13199;
        # 2026-08 deep-audit finding #2).
        rep = build_schedule3([ca_entry(direction="SHORT", proceeds=-4000.0,
                                        cost=-4500.0, commission=0.0,
                                        gain=500.0)])
        row = rep["rows"][0]
        self.assertAlmostEqual(row["proceeds"], 4500.0, places=2)
        self.assertAlmostEqual(row["acb"], 4000.0, places=2)
        self.assertAlmostEqual(row["gain"], 500.0, places=2)
        # Row is now internally consistent: proceeds − ACB == gain.
        self.assertAlmostEqual(row["proceeds"] - row["acb"], row["gain"],
                               places=2)
        self.assertIn("short", row["notes"].lower())


class TestCli(unittest.TestCase):
    def _gains(self, td, entries, name="margin_gains.json"):
        p = Path(td) / name
        p.write_text(json.dumps({"transactions": entries}))
        return p

    def test_8949_text_json_csv(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._gains(td, [us_entry(disallowed=250.0)])
            csv_path = Path(td) / "8949.csv"

            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = main([str(g), "--form", "8949", "--year", "2025",
                           "--csv", str(csv_path)])
            self.assertEqual(rc, 0)
            self.assertIn("PART I — SHORT-TERM", out.getvalue())
            self.assertIn("W", out.getvalue())
            csv_text = csv_path.read_text()
            self.assertIn("date_acquired", csv_text)
            self.assertIn("AAPL.US", csv_text)

            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(g), "--form", "8949", "--json"])
            rep = json.loads(out.getvalue())
            self.assertEqual(len(rep["part_I"]), 1)

    def test_schedule3_text(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._gains(td, [ca_entry()])
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(g), "--form", "schedule3", "--year", "2025"])
            self.assertEqual(rc, 0)
            self.assertIn("Line 13199", out.getvalue())
            self.assertIn("SHOP.TO", out.getvalue())

    def test_tainted_warning(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._gains(td, [us_entry(), us_entry(symbol="STX.US",
                                                      tainted=True)])
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = main([str(g), "--form", "8949"])
            self.assertEqual(rc, 0)
            self.assertIn("tainted", err.getvalue())

    def test_missing_file(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["/nonexistent.json", "--form", "8949"]), 2)


class TestRunWrapper(unittest.TestCase):
    def test_form_defaults_from_country(self):
        try:
            import tomllib  # noqa: F401
        except ImportError:
            try:
                import tomli  # noqa: F401
            except ImportError:
                self.skipTest("no TOML support on this interpreter")
        from taxjson.bin.taxjson_run import cmd_form_export

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n'
                '[accounts.margin]\ntype = "taxable"\n'
            )
            work = root / "work"
            work.mkdir()
            (work / "margin_gains.json").write_text(json.dumps({
                "transactions": [ca_entry()]}))

            args = argparse.Namespace(dir=str(root), form=None, csv=None,
                                      json=True)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    cmd_form_export(args)
            self.assertEqual(cm.exception.code, 0)
            rep = json.loads(out.getvalue())
        self.assertEqual(rep["form"], "schedule3")   # canada default
        self.assertEqual(rep["rows"][0]["symbol"], "SHOP.TO")




class TestTxf(unittest.TestCase):
    """TXF V042 export — TurboTax import built on the 8949 model."""

    def _txf(self, entries, box="A"):
        from taxjson.bin.taxjson_form_export import build_8949, build_txf
        return build_txf(build_8949(entries), box)

    def test_record_shape_and_refnums(self):
        doc = self._txf([
            us_entry(term="SHORT_TERM", proceeds=1600.0, cost=1500.0,
                     date="2026-01-20", days_held=10),
            us_entry(symbol="MSFT.US", term="LONG_TERM",
                     proceeds=4500.0, cost=3000.0, date="2026-02-01",
                     days_held=400),
        ])
        lines = doc.splitlines()
        self.assertEqual(lines[0], "V042")
        self.assertIn("N711", lines)          # ST box A
        self.assertIn("N714", lines)          # LT box D
        self.assertIn("$1500.00", lines)      # cost
        self.assertIn("$1600.00", lines)      # proceeds
        self.assertIn("D01/20/2026", lines)   # MM/DD/YYYY
        # record framing: every TD record ends with ^
        self.assertEqual(lines.count("TD"), 2)
        self.assertEqual(lines.count("^"), 3)  # header + 2 records

    def test_wash_sale_amount_rides_along(self):
        doc = self._txf([
            us_entry(proceeds=4000.0, cost=5000.0, disallowed=1000.0,
                     term="SHORT_TERM"),
        ])
        lines = doc.splitlines()
        rec = lines[lines.index("TD"):]
        self.assertIn("$1000.00", rec,
                      "the code-W disallowed amount must be the "
                      "trailing dollar field")
        # it must come AFTER cost and proceeds
        self.assertGreater(rec.index("$1000.00"), rec.index("$4000.00"))

    def test_box_c_uses_713_716(self):
        doc = self._txf([
            us_entry(term="SHORT_TERM"),
            us_entry(symbol="MSFT.US", term="LONG_TERM", days_held=400),
        ], box="C")
        self.assertIn("N713", doc)
        self.assertIn("N716", doc)
        self.assertNotIn("N711", doc)

    def test_short_sale_rows_use_real_world_columns(self):
        doc = self._txf([
            us_entry(symbol="TSLA.US", qty=100, proceeds=-10000.0,
                     cost=-12000.0, gain=2000.0, direction="SHORT",
                     term="SHORT_TERM"),
        ])
        self.assertIn("$10000.00", doc)   # cover cost -> cost column
        self.assertIn("$12000.00", doc)   # short-sale proceeds
        self.assertNotIn("$-", doc)


if __name__ == "__main__":
    unittest.main()
